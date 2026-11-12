"""Model Kombat plugin — registration.

Pit two Hermes models against each other on the same task set and score the
fight deterministically. Tools: kombat_list_tasks, kombat_scout, kombat_run_fight,
kombat_run_agentic_fight, kombat_score_answers, kombat_rematch_errors,
kombat_recommend, kombat_history, kombat_export.
Slash command: /kombat (fights run in the background; check /kombat status).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from . import kanban, schemas, tools

logger = logging.getLogger(__name__)

_JOB: dict = tools.JOB  # shared with tools.start_job / kombat_history({"running": true})


def _agentic_handler(args: dict, **kwargs) -> str:
    """Tool handler for kombat_run_agentic_fight (delegates to kanban.py)."""
    try:
        return json.dumps(kanban.run_agentic_fight(args))
    except Exception as exc:  # handler contract: never raise
        return json.dumps({"ok": False, "error": f"agentic fight failed: {exc}"})


def _start_job(ctx, kind: str, fn, fn_args: dict) -> str:
    job = tools.start_job(ctx, kind, fn, fn_args)
    if not job.get("started"):
        if job.get("status") == "busy":
            return (
                "A fight is already running. Check /kombat status or "
                "kombat_history {\"running\": true}."
            )
        return f"Could not start the fight: {job.get('error') or job.get('status')}"
    return (
        f"🥊 {kind.capitalize()} fight started in the background (job {job.get('job_id')}). "
        "Watch the model-kombat kanban board, then /kombat status / /kombat score."
    )


def _split_fight_flags(text: str) -> tuple[str, dict]:
    """Pull ``--card ID`` / ``--lanes a,b`` / ``--seed N`` / ``--hard`` / ``--gauntlet``."""
    flags: dict = {}

    def _take(match):
        flags[match.group(1)] = match.group(2)
        return " "

    def _flag(match):
        flags[match.group(1)] = True
        return " "

    remainder = re.sub(r"--(card|lanes|seed)\s+(\S+)", _take, text or "")
    remainder = re.sub(r"--(hard|gauntlet)\b", _flag, remainder)
    return re.sub(r"\s+", " ", remainder).strip(), flags


def _looks_like_task_id(text: str) -> bool:
    """Heuristic for ``/kombat scout <kanban task id>`` vs free text."""
    token = (text or "").strip()
    if not token or any(ch.isspace() for ch in token):
        return False
    low = token.lower()
    if low.startswith(("t-", "t_", "task-")):
        return True
    if re.fullmatch(r"t\d+", low):
        return True
    if re.fullmatch(r"[0-9a-f]{8,}(?:-[0-9a-f]{4,})*", low):
        return True
    return False


def _scout_summary(raw: str) -> str:
    """Turn kombat_scout JSON into a human card summary + the exact next command."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    if not data.get("ok"):
        return raw
    card = data.get("card") or {}
    card_id = card.get("id") or "?"
    lines = [f"🥊 Fight card {card_id}", f"  {card.get('title') or 'Kombat card'}"]
    if card.get("intensity"):
        expected = data.get("expected_discrimination") or "unknown"
        lines.append(f"  intensity: {card['intensity']} (expected discrimination: {expected})")
    lanes = card.get("lane_weights") or [{"lane": lane, "weight": 1} for lane in card.get("lanes") or []]
    if lanes:
        lines.append("  lanes: " + ", ".join(f"{l['lane']} ({l.get('weight', 1)})" for l in lanes))
    for rnd in card.get("rounds") or []:
        tasks = ", ".join(rnd.get("tasks") or []) or "—"
        lines.append(f"  {rnd.get('id')} {rnd.get('name')} ({rnd.get('points', 1)}pt/task): {tasks}")
    if card.get("sudden_death_task"):
        lines.append(f"  sudden death: {card['sudden_death_task']} (fought up front, used only on a tie)")
    fighter_a = tools._cfg("fighter_a", "stealth/crouton-alpha") or "stealth/crouton-alpha"
    fighter_b = tools._cfg("fighter_b", "deepseek/deepseek-v4.1-flash-1017") or "deepseek/deepseek-v4.1-flash-1017"
    lines.append("")
    lines.append(f"Next: /kombat fight {fighter_a} vs {fighter_b} --card {card_id}")
    return "\n".join(lines)


def _handle_kombat(ctx, raw_args: str) -> str:
    """Slash command: /kombat [tasks|scout|fight|agentic|score|status|history|export|help]."""
    raw = (raw_args or "").strip()
    parts = raw.split(None, 1)
    sub = parts[0].lower() if parts else "help"
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "tasks":
        return tools.kombat_list_tasks({})

    if sub == "scout":
        if not rest.strip():
            return "Usage: /kombat scout <task text, or a kanban card id> [--hard|--gauntlet]"
        intensity = None
        for token in ("--gauntlet", "--hard"):
            if re.search(rf"(?:^|\s){re.escape(token)}\b", rest):
                intensity = token[2:]
                rest = re.sub(rf"\s*{re.escape(token)}\b", "", rest)
        rest = rest.strip()
        args: dict = {"input": rest}
        if intensity:
            args["intensity"] = intensity
        if rest.startswith("card:"):
            args = {"card": rest[5:].strip()}
            if intensity:
                args["intensity"] = intensity
        elif rest.lower() in ("board", "all"):
            args = {"board": True}
            if intensity:
                args["intensity"] = intensity
        elif _looks_like_task_id(rest):
            args = {"card": rest}
            if intensity:
                args["intensity"] = intensity
        return _scout_summary(tools.kombat_scout(args))

    if sub == "recommend":
        target = rest.strip().lower()
        if target in ("board", "all", ""):
            return tools.kombat_recommend({"board": True})
        if target == "ladder":
            return tools.kombat_recommend({"ladder": True})
        return tools.kombat_recommend({"lane": rest.strip()})

    if sub == "ladder":
        return tools.kombat_recommend({"ladder": True})

    if sub in ("fight", "agentic"):
        phrase, flags = _split_fight_flags(rest)
        if " vs " not in phrase:
            return (
                f"Usage: /kombat {sub} <model-a> vs <model-b> [--card <card-id>] "
                f"[--lanes coding,sql] [--seed N] [--hard|--gauntlet]\n"
                "Example: /kombat fight stealth/crouton-alpha vs deepseek/deepseek-v4.1-flash-1017 "
                "--card card-15b9f4edc0\n"
                "Requires plugins.entries.model-kombat.llm.allow_model_override: true in config.yaml."
            )
        fighter_a, fighter_b = (s.strip() for s in phrase.split(" vs ", 1))
        payload: dict = {"fighter_a": fighter_a, "fighter_b": fighter_b}
        intensity = "gauntlet" if flags.get("gauntlet") else ("hard" if flags.get("hard") else None)
        card_id = (flags.get("card") or "").strip()
        lanes = [lane.strip() for lane in (flags.get("lanes") or "").split(",") if lane.strip()]
        seed = flags.get("seed")
        if intensity:
            payload["intensity"] = intensity
        if card_id:
            payload["card"] = card_id
        elif lanes:
            card_spec: dict = {"lanes": lanes}
            if intensity:
                card_spec["intensity"] = intensity
            if seed is not None:
                try:
                    card_spec["seed"] = int(seed)
                except (TypeError, ValueError):
                    return f"--seed must be an integer, got {seed!r}."
            payload["card"] = card_spec
        fn = tools.kombat_run_fight if sub == "fight" else _agentic_handler
        return _start_job(ctx, sub, fn, payload)

    if sub == "rematch":
        return _start_job(ctx, "rematch", tools.kombat_rematch_errors, {})

    if sub == "status":
        if _JOB["running"]:
            return (
                f"Fight ({_JOB['kind']}) is running… job {_JOB.get('job_id') or '—'}; "
                "check the model-kombat kanban board or kombat_history {\"running\": true}."
            )
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
        return tools.kombat_score_answers({"last": True})

    if sub == "history":
        return tools.kombat_history({"limit": 10})

    if sub == "export":
        fmt = rest.strip().lower() or "json"
        return tools.kombat_export({"format": fmt})

    return (
        "🥊 Model Kombat — choose your fighter. Adaptive cards, escalating stakes:\n"
        "  /kombat tasks                     list the legacy rounds (1pt / 2pt / 3pt + sudden death)\n"
        "  /kombat scout <text|card:id|board> [--hard|--gauntlet]  classify lanes, print card id + next fight command\n"
        "  /kombat recommend [lane|board]    ladder knockout pick (board = every open card)\n"
        "  /kombat ladder                    show the per-lane Elo ladder\n"
        "  /kombat fight <A> vs <B> [--card id] [--lanes a,b] [--seed N] [--hard|--gauntlet]  pure fight (ctx.llm, budgeted) — background job\n"
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
        name="kombat_scout",
        toolset="model_kombat",
        schema=schemas.KOMBAT_SCOUT,
        handler=tools.kombat_scout,
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
        name="kombat_recommend",
        toolset="model_kombat",
        schema=schemas.KOMBAT_RECOMMEND,
        handler=tools.kombat_recommend,
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
        args_hint="[tasks | scout <text|task-id> [--hard|--gauntlet] | recommend [lane|board] | ladder | fight <A> vs <B> [--card id] [--lanes a,b] [--seed N] [--hard|--gauntlet] | agentic <A> vs <B> | rematch | score | status | history | export]",
    )

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)

    logger.debug("Model Kombat registered: 9 tools, /kombat command, bundled skill")
