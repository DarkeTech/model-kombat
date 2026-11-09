---
name: model-kombat
description: "Run and score a head-to-head fight between two Hermes models on the same task set. Use when the user wants to compare / benchmark / test two models against each other."
---

# Model Kombat workflow

Golden rule: **same prompts, both fighters, mechanical scoring.** Never let either
model judge the other — `kombat_score_answers` scores rounds by execution and
exact checks, not by vibes.

## When to use
- "Compare model X vs model Y", "test this model against my usual one",
  "benchmark these two", "which model is better at …"

## Workflow
1. `kombat_list_tasks` — show the structure: 3 escalating rounds
   (R1 Warm-up 1pt: T1 money, T4 format · R2 Main Event 2pt: T3 recall,
   T5 summary, T2 debug · R3 Championship 3pt: T6 leap-year, T7 Sudoku
   validator), plus sudden-death T8 (run-length encode) on a tie.
2. `kombat_run_fight` with `fighter_a` / `fighter_b` model IDs. Both fighters
   get identical prompts (temperature 0, budgeted, one retry, usage tracked).
   The fight also drives the **model-kombat kanban board** (default on) — mention
   the board to the user as the live scoreboard.
   - If the host refuses the model override, do NOT work around it: tell the
     user to set `plugins.entries.model-kombat.llm.allow_model_override: true`
     in config.yaml and retry. The plugin never touches credentials itself.
3. If scoring excluded rounds due to errors (usually 429 rate limits),
   `kombat_rematch_errors` re-fights **only** the errored (task, fighter) pairs
   and merges the answers into the stored fight — then score again. Never
   re-run the whole fight just to fix a rate-limited round.
4. `kombat_score_answers` with both answer maps (pass `usage` + `errors_a/b`
   from the run). Code rounds only execute with `allow_code_execution=true`
   (a user-visible setting) — otherwise they're excluded from points.
   Weighted points, Championship K.O. rule, sudden death on a tie. The tool
   returns winner, points, KOs, usage totals, the HTML versus scorecard path,
   and appends the fight to history — share the report path.
4. Report the verdict plainly: winner, points, which rounds decided it, any
   K.O., and total cost/latency. Quote the score details; don't invent
   strengths the scores don't show. Note any excluded (errored/skipped) rounds.

## Agentic mode (explicit opt-in only)
`kombat_run_agentic_fight` dispatches each task to a real kanban worker with a
per-task model override — workers solve with tools, cards move live. Slower,
costlier (worker billing, not budget-capped), needs the gateway dispatcher.
Only use it when the user explicitly asks for an agentic/kanban-worker fight;
default to the pure fight.

## Slash commands (for the user)
- `/kombat tasks` · `/kombat fight <A> vs <B>` · `/kombat agentic <A> vs <B>`
- `/kombat status` · `/kombat rematch` · `/kombat score` · `/kombat history` · `/kombat export [json|md|csv]`
Fights launched from slash commands run as background jobs — point the user at
`/kombat status` and the kanban board.

## History & export
`kombat_history` lists recent fights; `kombat_export` writes one fight to
JSON/MD/CSV. Every scored fight lands in history automatically.

## Extending the suite
Add a task to `tasks.json` (and its round) with a scorer type: `money`,
`recall` (expected entries may be alternative-phrasing lists), `format25`,
`summary50`, `debug_exec`, or `code_exec` (needs a harness in `tools.py`).
Prefer scorers that execute or count over scorers that read.
