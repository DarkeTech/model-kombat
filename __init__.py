"""Model Kombat plugin — registration.

Pit two Hermes models against each other on the same task set and score the
fight deterministically. Tools: kombat_list_tasks, kombat_run_fight,
kombat_score_answers. Slash command: /kombat.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import schemas, tools

logger = logging.getLogger(__name__)


def _handle_kombat(ctx, raw_args: str) -> str:
    """Slash command: /kombat [tasks | fight <A> vs <B> | score | help]."""
    raw = (raw_args or "").strip()
    parts = raw.split(None, 1)
    sub = parts[0].lower() if parts else "help"
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "tasks":
        return tools.kombat_list_tasks({})

    if sub == "fight":
        if " vs " not in rest:
            return (
                "Usage: /kombat fight <model-a> vs <model-b>\n"
                "Example: /kombat fight stealth/crouton-alpha vs deepseek/deepseek-v4.1-flash-1017\n"
                "Requires plugins.entries.model-kombat.llm.allow_model_override: true in config.yaml."
            )
        fighter_a, fighter_b = (s.strip() for s in rest.split(" vs ", 1))
        return tools.kombat_run_fight({"fighter_a": fighter_a, "fighter_b": fighter_b})

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
        return tools.kombat_score_answers(
            {
                "fighter_a": last.get("fighter_a", "Fighter A"),
                "fighter_b": last.get("fighter_b", "Fighter B"),
                "answers_a": last.get("answers_a", {}),
                "answers_b": last.get("answers_b", {}),
                "allow_code_execution": bool(ctx.get_config("allow_code_execution", default=False))
                if hasattr(ctx, "get_config")
                else False,
            }
        )

    return (
        "🥊 Model Kombat — choose your fighter.\n"
        "  /kombat tasks                     list the fight rounds\n"
        "  /kombat fight <A> vs <B>          run both models on every round\n"
        "  /kombat score                     score the last fight + render the versus scorecard\n"
        "Fights need llm.model_override enabled for this plugin "
        "(config: plugins.entries.model-kombat.llm.allow_model_override: true)."
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
        name="kombat_score_answers",
        toolset="model_kombat",
        schema=schemas.KOMBAT_SCORE_ANSWERS,
        handler=tools.kombat_score_answers,
    )

    ctx.register_command(
        "kombat",
        lambda raw: _handle_kombat(ctx, raw),
        description="Model Kombat: pit two models against each other and score the fight",
        args_hint="[tasks | fight <A> vs <B> | score]",
    )

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)

    logger.debug("Model Kombat registered: 3 tools, /kombat command, bundled skill")
