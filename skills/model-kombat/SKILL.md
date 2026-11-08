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
1. `kombat_list_tasks` — show the rounds on offer (T1 reasoning, T2 debugging,
   T3 recall, T4 exact format, T5 summary fidelity, T6 executed code).
2. `kombat_run_fight` with `fighter_a` / `fighter_b` model IDs. Both fighters
   get identical prompts (temperature 0 by default for fairness).
   - If the host refuses the model override, do NOT work around it: tell the
     user to set `plugins.entries.model-kombat.llm.allow_model_override: true`
     in config.yaml and retry. The plugin never touches credentials itself.
3. `kombat_score_answers` with both answer maps. Code rounds only execute with
   `allow_code_execution=true` (a user-visible setting) — otherwise they score
   as skipped. The tool returns round winners, points, KOs, and a path to the
   HTML versus scorecard — share that path.
4. Report the verdict plainly: winner, points, which rounds decided it, and any
   K.O. (one fighter passed a hard check the other failed). Quote the score
   details; don't invent strengths the scores don't show.

## Slash commands (for the user)
- `/kombat tasks` · `/kombat fight <A> vs <B>` · `/kombat score`

## Extending the suite
Add a task to `tasks.json` with a scorer type: `money`, `recall`, `format25`,
`summary50`, `debug` (heuristic), or `code_exec`. Prefer scorers that execute
or count over scorers that read.
