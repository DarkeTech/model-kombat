# Architecture

One page: how the modules fit together, what each data file holds, and how a
fight flows from a kanban card to a badge on that card.

## Modules

```
                         ┌──────────────────────────────────────────────┐
                         │  __init__.py  (register)                     │
                         │  9 tools · /kombat command · bundled skill   │
                         │  sets tools.ctx, background jobs via spawn   │
                         └───────────────┬──────────────────────────────┘
                                         │ calls
              ┌──────────────────────────┴───────────────────────────┐
              │                        tools.py                      │
              │  scout wrapper · pure fight runner · scoring loop    │
              │  intensity resolve · SD fetched up front · pricing   │
              │  history/export · data-dir resolution · kanban hook  │
              └──┬──────────┬──────────┬──────────┬──────────┬───────┘
                 │          │          │          │          │
      ┌──────────┴──┐ ┌─────┴─────┐ ┌──┴──────┐ ┌─┴───────┐ ┌┴───────────┐
      │  lanes.py   │ │ladder.py  │ │live.py  │ │render.py│ │ kanban.py  │
      │ classifier  │ │ Elo per   │ │ live    │ │ HTML    │ │ board      │
      │ task pool   │ │ lane,     │ │ state   │ │ score-  │ │ scoreboard │
      │ intensity   │ │ recommend │ │ machine │ │ card +  │ │ agentic    │
      │ task_stats  │ │ cost/pt   │ │ +events │ │ arena   │ │ workers    │
      │ card build  │ │ verdict ≈ │ │         │ │ edge    │ │            │
      │ (seeded)    │ │           │ │         │ │ line    │ │            │
      └──────┬──────┘ └─────┬─────┘ └──┬──────┘ └────┬────┘ └─────┬──────┘
             │              │          │             │            │
             │        ┌─────┴──────────┴─────────────┴───┐        │
             │        │        plugin data dir           │        │
             │        │  $HERMES_HOME/plugin-data/        │        │
             │        │            model-kombat/          │        │
             │        └─────┬──────────┬──────────┬──────┘        │
             │              │          │          │               │
             │              │          │          │        host `terminal` CLI
             │              │          │          │        (`hermes kanban …`)
             │              │          │          │        dedicated board,
             │              │          │          │        unassigned display
             │              │          │          │        cards
             │              │          │          │
      ┌──────┴──────┐ ┌─────┴─────┐ ┌──┴───────┐ ┌┴──────────────┐
      │ tasks/*.json│ │ladder.json│ │live.json │ │fights.jsonl   │
      │ tasks.json  │ │recommenda-│ │          │ │exports/       │
      │ (legacy)    │ │tions.json │ │          │ │reports *.html │
      │ + prices.json│ │cards.json │ └────┬─────┘ └───────────────┘
      │ (shipped)   │ │task_stats.│      │
      └─────────────┘ │json       │      │
                      └───────────┘      │
                                         │ read (poll)      ┌──────────────────┐
                      ┌──────────────────┴───────────────► │ dashboard/       │
                      │                                    │ plugin_api.py    │
                      │  broadcast_plugin_event            │ GET /live        │
                      │  "plugin.model-kombat.            │     /ladder      │
                      │   fight.updated"                   │     /trends      │
                      │                                    │     /history     │
                      │                                    │     /recommend…  │
                      │                                    │     /health      │
                      │                                    └────────┬─────────┘
                      │                                             │ ctx.rest
                      ▼                                             ▼
             ┌────────────────────────────────────────────────────────────┐
             │ desktop/plugin.js   (renderer, plugin SDK only)            │
             │ panes: arena (right) · route /kombat · sidebar · statuschip│
             │ palette "Open Model Kombat" · kanban card badges           │
             └────────────────────────────────────────────────────────────┘
```

`engine_checks.py` is the scoring kernel called by `tools.py` for every task:
`score_check(check, answer, allow_exec) -> {score, status, detail}` for the eight
check types, plus `_run_sandboxed` — the single hardened runner behind both the
`code_exec` checks and the legacy T2/T6/T7/T8 harnesses.
`schemas.py` holds the tool JSON schemas the model sees; it has no runtime state.

## Data files (plugin data dir)

| File | Written by | Contents |
|---|---|---|
| `cards.json` | `tools.kombat_scout` | Scouted cards keyed by `card-…` id, so a fight can be started later by id. Each card stores its `intensity` (`standard`/`hard`/`gauntlet`), `seed`, `stats_used` and rounds. `persist: false` skips the write. |
| `live.json` | `live.py` (single writer) | Current fight state: `status` (`idle`/`fighting`/`scoring`/`done`/`error`), fighters, HP, per-round task rows, points, winner, K.O. flag, `report_path`, `updated`. Sudden death is hidden until a tie uses it. Rewritten atomically on every state change. |
| `ladder.json` | `ladder.py` | `{updated, lanes: {lane: {models: {id: {elo, wins, losses, draws, fights, points, cost_usd, latency_s}}}}, history: [{ts, fight_id, lane, model, opponent, elo, delta, bout_score, result}]}`. K=24, start 1500. `history` (capped at 4,000 entries, oldest dropped) records one Elo entry per model per lane bout for trend reads. A corrupt file is preserved as `ladder.json.bak` and treated as empty. |
| `recommendations.json` | `ladder.write_recommendations` | `{kanban_task_id: {lane, pick, short, verdict, confidence, elo, updated}}` — the source of the board badges. `short` carries the `≈` prefix when the verdict is `no clear edge`. Merged, not replaced. |
| `task_stats.json` | `tools._update_task_stats` (after each scored fight) | Task-discrimination memory: `{updated, applied: [fight_id…], tasks: {task_id: {fights, both_perfect, both_zero, split, mean_gap, per_model: {model: {n, mean}}}}}`. Atomic, idempotent per `fight_id`; `applied` capped at 500. Read by `lanes._Picker` to prefer never-fought then discriminating tasks and demote saturated ones. |
| `prices.json` (repo root, shipped — not written at runtime) | `scripts`/maintainers | USD-per-1M-token table `{model_id: {"in": …, "out": …}}`, merged under the `model_prices` setting. Used to **estimate** cost when the host reports none; a missing model yields `cost_source: "unknown"` and `n/a`, never `$0`. |
| `fights.jsonl` | `tools` (append) | One JSON object per scored fight, in order; `kombat_history` and `kombat_export` read it by index. |
| `exports/`, report `*.html` | `tools`, `render.py` | Generated scorecards and JSON/MD/CSV exports. |

There is no database and no network store. Deleting the directory resets
history, ladder and cards; the plugin recreates it on the next write.

## Event flow: card → badge

```
PM selects kanban card t_9f21c4
  │
  ├─ 1  /kombat scout card:t_9f21c4 [--hard|--gauntlet]
  │      tools.kombat_scout → hermes kanban show --json (host terminal)
  │      → tools._intensity(arg, default_intensity)   standard|hard|gauntlet
  │      → lanes.classify(text)            keyword weights, no LLM call
  │      → lanes.load_task_stats()         task_stats.json (optional memory)
  │      → lanes.build_card(lanes, seed, intensity, stats)
  │                                       deterministic rounds 1/2/3 + SD
  │      → cards.json                      card stored by id
  │
  ├─ 2  kombat_run_fight(card="card-…", fighter_a, fighter_b)
  │      live.py: status=fighting  → live.json  → broadcast fight.updated
  │      for each task: ctx.llm.complete(model=A) then (model=B)
  │        429 → wait (bounded per-wait + per-fight budget) → retry
  │        exhausted → recorded as errored, never failed
  │        after every call: price it (prices.json/model_prices) and check
  │        max_fight_cost_usd + max_fight_tokens → cap hit stops the fight
  │      after the rounds: sudden-death task fetched up front, one call per
  │        fighter; stored but hidden until a points tie needs it
  │      kanban.py mirrors rounds as cards on the `model-kombat` board
  │      (host terminal tool; best-effort, never fails the fight)
  │      live.py: hp/points/round state updated per task
  │
  ├─ 3  kombat_score_answers(fight)
  │      engine_checks.score_check per task  → fractions
  │      exclusion pass (errored / skipped)  → round asterisks
  │      round stakes, K.O. check
  │      points tied → score the up-front sudden-death answers (no points)
  │      still tied → winner=draw, efficiency edge (latency/tokens/cost) is
  │        displayed and labelled "not a win"; ladder untouched
  │      render.render_report(...)            → HTML scorecard
  │      append fights.jsonl
  │      ladder.update(...)                   → ladder.json (per-lane Elo)
  │      tools._update_task_stats(...)        → task_stats.json
  │      live.py: status=done, winner, ko     → live.json → event
  │
  └─ 4  kombat_recommend({board: true})
         lanes.classify each open card → lane
         ladder.recommend(lane)  → pick, margin, confidence, cost/point
         margin < 15 Elo → verdict "no clear edge", short name prefixed "≈"
         ladder.write_recommendations(...)  → recommendations.json
         desktop/plugin.js polls /recommendations (and repaints on change)
         → badge on card t_9f21c4
```

## Read paths

- **Desktop pane / `/kombat` page:** `ctx.onEvent('plugin.model-kombat.fight.updated')`
  for push updates, `ctx.setInterval` + `ctx.rest('/live')` and `/recommendations` as
  the polling fallback; badges are re-registered whenever the recommendation map changes.
- **Dashboard API:** every route reads the data dir under `HERMES_HOME` (falling back to
  `~/.hermes/plugin-data/model-kombat`), returns JSON, and returns an empty/default object
  instead of raising when a file is missing or unreadable.
- **Kanban board:** written only by `kanban.py` through the host `terminal` tool; the
  plugin never reads another plugin's cards and never dispatches the display cards it
  creates.

## Invariants

- Handlers always return JSON, never raise; a failed provider call is data.
- One writer per file; `live.json` and `ladder.json` are written atomically.
- Scoring is deterministic given (task, answer, allow_exec) — no model in the loop.
- Cost is a local estimate from `prices.json`/`model_prices`; an unpriced model is
  `cost_source: "unknown"` and displays `n/a`, never `$0`.
- Sudden death is fetched up front but scored only on an exact points tie; a draw is
  reported as a draw (an efficiency edge is never a win).
- Code execution is off unless explicitly enabled, and its runner is defense-in-depth,
  not a sandbox (see README → Disclosure).
- No credentials are read; the host LLM facade is the only network path.
