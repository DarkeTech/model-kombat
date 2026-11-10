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
   - **Long cards (>6 tasks, or answers that can take minutes): always call with
     `background: true`.** It returns `{job_id, status: "started"}` immediately
     (like `/kombat fight`), so the host's ~420s tool timeout can't kill it. Then
     poll `kombat_history {"running": true}` until `running` is false and score
     with `kombat_score_answers {"last": true}`.
   - Each LLM call has a hard deadline (`llm_timeout_s`, default 180s) enforced in
     a worker thread: a hung response is abandoned and counted as an errored
     attempt (retried once), never as a failure of the model.
   - A whole-fight wall-clock cap (`max_fight_seconds`, default 360s) marks the
     remaining pairs `skipped` ("fight time cap") and returns a partial result
     with `time_cap_hit: true`; scoring excludes them and flags the round `*`.
   - Results also carry `interrupted`, `stale_closed`, `bout_reused`. If a fight
     is interrupted (KeyboardInterrupt/SystemExit/exception/budget stop), its
     kanban cards are closed once with "Interrupted — not scored" and live.json
     goes to status `error` with the reason — never leave cards stuck `ready`.
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

## Reading a tie (do not manufacture a winner)
A strong pair on mechanical tasks usually draws. When `kombat_score_answers`
returns `winner: "draw"`, report the `edge` object as-is (an efficiency edge,
never a win). Then call `kombat_recommend` for the card: when the top-two Elo
margin is under 15 it returns `verdict: "no clear edge"` plus a `value_edge`
block (`cost_ratio`, `latency_ratio`, `label: "equal quality — X is 18.0× cheaper
and 2.8× faster"`) and the same clause appended to `why`. Lead with that clause
— it is the decision the user actually needs — and say plainly that it is
measured cost/latency at equal quality, not a quality win. `value_edge` is
`null` when price is unknown; say `n/a` rather than guessing.
The overall pick is a weighted average lane Elo restricted to models with Elo in
every card lane, with ties broken by lane picks won, then coverage, then
cost/latency (never by model name), so `overall.pick` always agrees with a
unanimous per-lane pick — if it doesn't, that is a bug worth reporting.

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
