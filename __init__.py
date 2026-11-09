"""Model Kombat plugin — registration.

Pit two Hermes models against each other on the same task set and score the
fight deterministically. Tools: kombat_list_tasks, kombat_run_fight,
kombat_run_agentic_fight, kombat_score_answers, kombat_history, kombat_export.
Slash command: /kombat (fights run in the background; check /kombat status).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from . import kanban, schemas, tools

logger = logging.getLogger(__name__)

_JOB: dict = {"running": False, "kind": None, "result": None, "error": None}


def _agentic_handler(args: dict, **kwargs) -> str:
    """Tool handler for kombat_run_agentic_fight (delegates to kanban.py)."""
    try:
        return json.dumps(kanban.run_agentic_fight(args))
    except Exception as exc:  # handler contract: never raise
        return json.dumps({"ok": False, "error": f"agentic fight failed: {exc}"})


def _start_job(ctx, kind: str, fn, fn_args: dict) -> str:
    if _JOB["running"]:
        return "A fight is already running. Check /kombat status."
    _JOB.update(running=True, kind=kind, result=None, error=None)

    async def _runner():
        try:
            _JOB["result"] = await asyncio.to_thread(fn, fn_args)
        except Exception as exc:  # surfaced via /kombat status, never crashes the host
            _JOB["error"] = str(exc)
        finally:
            _JOB["running"] = False

    try:
        ctx.spawn_task(_runner(), name=f"model-kombat:{kind}")
    except Exception:
        # No running loop (e.g. plain CLI): fall back to a sync run.
        try:
            _JOB["result"] = fn(fn_args)
        except Exception as exc:
            _JOB["error"] = str(exc)
        _JOB["running"] = False
        return f"Fight finished synchronously (background unavailable): {_JOB['result'] or _JOB['error']}"
    return (
        f"🥊 {kind.capitalize()} fight started in the background. "
        "Watch the model-kombat kanban board, then /kombat status / /kombat score."
    )


def _handle_kombat(ctx, raw_args: str) -> str:
    """Slash command: /kombat [tasks|fight|agentic|score|status|history|export|help]."""
    raw = (raw_args or "").strip()
    parts = raw.split(None, 1)
    sub = parts[0].lower() if parts else "help"
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "tasks":
        return tools.kombat_list_tasks({})

    if sub in ("fight", "agentic"):
        if " vs " not in rest:
            return (
                f"Usage: /kombat {sub} <model-a> vs <model-b>\n"
                "Example: /kombat fight stealth/crouton-alpha vs deepseek/deepseek-v4.1-flash-1017\n"
                "Requires plugins.entries.model-kombat.llm.allow_model_override: true in config.yaml."
            )
        fighter_a, fighter_b = (s.strip() for s in rest.split(" vs ", 1))
        fn = tools.kombat_run_fight if sub == "fight" else _agentic_handler
        return _start_job(ctx, sub, fn, {"fighter_a": fighter_a, "fighter_b": fighter_b})

    if sub == "rematch":
        return _start_job(ctx, "rematch", tools.kombat_rematch_errors, {})

    if sub == "status":
        if _JOB["running"]:
            return f"Fight ({_JOB['kind']}) is running… check the model-kombat kanban board."
        if _JOB["error"]:
            return f"Last fight failed: {_JOB['error']}"
        if _JOB["result"]:
            try:
                data = json.loads(_JOB["result"])
                return (
                    f"Last fight done: {data.get('fighters')} — "
                    f"errors: {data.get('errors') or 'none'}. Run /kombat score to score it."
                )
            except (json.JSONDecodeError, TypeError):
                return "Last fight done (see /kombat score)."
        return "No fight started yet in this session."

    if sub == "score":
        try:
            last = ctx.state.get("last_fight", default=None) if hasattr(ctx, "state") else None
        except Exception:
            last = None
        if not last:
            return (
                "No fight stored in this session yet. Run /kombat fight <A> vs <B> first, "
                "or call kombat_score_answers with answers_a/answers_b maps."
            )
        errors = last.get("errors") or []
        return tools.kombat_score_answers(
            {
                "fighter_a": last.get("fighter_a", "Fighter A"),
                "fighter_b": last.get("fighter_b", "Fighter B"),
                "answers_a": last.get("answers_a", {}),
                "answers_b": last.get("answers_b", {}),
                "errors_a": [e["task"] for e in errors if e.get("fighter") == last.get("fighter_a")],
                "errors_b": [e["task"] for e in errors if e.get("fighter") == last.get("fighter_b")],
                "usage": last.get("usage") or {},
                "allow_code_execution": bool(tools._cfg("allow_code_execution", False)),
            }
        )

    if sub == "history":
        return tools.kombat_history({"limit": 10})

    if sub == "export":
        fmt = rest.strip().lower() or "json"
        return tools.kombat_export({"format": fmt})

    return (
        "🥊 Model Kombat — choose your fighter. 3 rounds, escalating stakes:\n"
        "  /kombat tasks                     list the rounds (1pt / 2pt / 3pt + sudden death)\n"
        "  /kombat fight <A> vs <B>          pure fight (ctx.llm, budgeted) — background job\n"
        "  /kombat agentic <A> vs <B>        agentic fight (kanban workers, model overrides)\n"
        "  /kombat status                    background fight status\n"
        "  /kombat rematch                   re-fight the last fight's errored tasks\n"
        "  /kombat score                     score the last fight + versus scorecard\n"
        "  /kombat history                   recent fights\n"
        "  /kombat export [json|md|csv]      export the latest fight\n"
        "Fights need llm.model_override enabled for this plugin "
        "(config: plugins.entries.model-kombat.llm.allow_model_override: true). "
        "Live scoreboard: the model-kombat kanban board (on by default)."
    )


def register(ctx):
    """Wire schemas to handlers, slash command, and the bundled skill."""
    tools.set_ctx(ctx)

    ctx.register_tool(
        name="kombat_list_tasks",
        toolset="model_kombat",
        schema=schemas.KOMBAT_LIST_TASKS,
        handler=tools.kombat_list_tasks,
    )
    ctx.register_tool(
        name="kombat_run_fight",
        toolset="model_kombat",
        schema=schemas.KOMBAT_RUN_FIGHT,
        handler=tools.kombat_run_fight,
    )
    ctx.register_tool(
        name="kombat_run_agentic_fight",
        toolset="model_kombat",
        schema=schemas.KOMBAT_RUN_AGENTIC_FIGHT,
        handler=_agentic_handler,
    )
    ctx.register_tool(
        name="kombat_score_answers",
        toolset="model_kombat",
        schema=schemas.KOMBAT_SCORE_ANSWERS,
        handler=tools.kombat_score_answers,
    )
    ctx.register_tool(
        name="kombat_rematch_errors",
        toolset="model_kombat",
        schema=schemas.KOMBAT_REMATCH_ERRORS,
        handler=tools.kombat_rematch_errors,
    )
    ctx.register_tool(
        name="kombat_history",
        toolset="model_kombat",
        schema=schemas.KOMBAT_HISTORY,
        handler=tools.kombat_history,
    )
    ctx.register_tool(
        name="kombat_export",
        toolset="model_kombat",
        schema=schemas.KOMBAT_EXPORT,
        handler=tools.kombat_export,
    )

    ctx.register_command(
        "kombat",
        lambda raw: _handle_kombat(ctx, raw),
        description="Model Kombat: pit two models against each other and score the fight",
        args_hint="[tasks | fight <A> vs <B> | agentic <A> vs <B> | rematch | score | status | history | export]",
    )

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)

    logger.debug("Model Kombat registered: 6 tools, /kombat command, bundled skill")
