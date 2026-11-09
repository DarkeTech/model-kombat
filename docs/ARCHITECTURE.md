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
              │  history/export · data-dir resolution · kanban hook  │
              └──┬──────────┬──────────┬──────────┬──────────┬───────┘
                 │          │          │          │          │
      ┌──────────┴──┐ ┌─────┴─────┐ ┌──┴──────┐ ┌─┴───────┐ ┌┴───────────┐
      │  lanes.py   │ │ladder.py  │ │live.py  │ │render.py│ │ kanban.py  │
      │ classifier  │ │ Elo per   │ │ live    │ │ HTML    │ │ board      │
      │ task pool   │ │ lane,     │ │ state   │ │ score-  │ │ scoreboard │
      │ card build  │ │ recommend │ │ machine │ │ card +  │ │ agentic    │
      │ (seeded)    │ │ cost/pt   │ │ +events │ │ arena   │ │ workers    │
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
      └─────────────┘ │cards.json │ └────┬─────┘ └───────────────┘
                      └───────────┘      │
                                         │ read (poll)      ┌──────────────────┐
                      ┌──────────────────┴───────────────► │ dashboard/       │
                      │                                    │ plugin_api.py    │
                      │  broadcast_plugin_event            │ GET /live        │
                      │  "plugin.model-kombat.            │     /ladder      │
                      │   fight.updated"                   │     /history     │
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
check types, plus the subprocess runner used only by `code_exec`.
`schemas.py` holds the tool JSON schemas the model sees; it has no runtime state.

## Data files (plugin data dir)

| File | Written by | Contents |
|---|---|---|
| `cards.json` | `tools.kombat_scout` | Scouted cards keyed by `card-…` id, so a fight can be started later by id. `persist: false` skips the write. |
| `live.json` | `live.py` (single writer) | Current fight state: `status` (`idle`/`fighting`/`scoring`/`done`/`error`), fighters, HP, per-round task rows, points, winner, K.O. flag, `report_path`, `updated`. Rewritten atomically on every state change. |
| `ladder.json` | `ladder.py` | `{updated, lanes: {lane: {models: {id: {elo, wins, losses, draws, fights, points, cost_usd, latency_s}}}}}`. K=24, start 1500. A corrupt file is preserved as `ladder.json.bak` and treated as empty. |
| `recommendations.json` | `ladder.write_recommendations` | `{kanban_task_id: {lane, pick, short, confidence, elo, updated}}` — the source of the board badges. Merged, not replaced. |
| `fights.jsonl` | `tools` (append) | One JSON object per scored fight, in order; `kombat_history` and `kombat_export` read it by index. |
| `exports/`, report `*.html` | `tools`, `render.py` | Generated scorecards and JSON/MD/CSV exports. |

There is no database and no network store. Deleting the directory resets
history, ladder and cards; the plugin recreates it on the next write.

## Event flow: card → badge

```
PM selects kanban card t_9f21c4
  │
  ├─ 1  /kombat scout card:t_9f21c4
  │      tools.kombat_scout → hermes kanban show --json (host terminal)
  │      → lanes.classify(text)            keyword weights, no LLM call
  │      → lanes.build_card(lanes, seed)   deterministic rounds 1/2/3 + SD
  │      → cards.json                      card stored by id
  │
  ├─ 2  kombat_run_fight(card="card-…", fighter_a, fighter_b)
  │      live.py: status=fighting  → live.json  → broadcast fight.updated
  │      for each task: ctx.llm.complete(model=A) then (model=B)
  │        429 → wait (bounded per-wait + per-fight budget) → retry
  │        exhausted → recorded as errored, never failed
  │      kanban.py mirrors rounds as cards on the `model-kombat` board
  │      (host terminal tool; best-effort, never fails the fight)
  │      live.py: hp/points/round state updated per task
  │
  ├─ 3  kombat_score_answers(fight)
  │      engine_checks.score_check per task  → fractions
  │      exclusion pass (errored / skipped)  → round asterisks
  │      round stakes, K.O. check, sudden death on a tie
  │      render.render_report(...)            → HTML scorecard
  │      append fights.jsonl
  │      ladder.update(...)                   → ladder.json (per-lane Elo)
  │      live.py: status=done, winner, ko     → live.json → event
  │
  └─ 4  kombat_recommend({board: true})
         lanes.classify each open card → lane
         ladder.recommend(lane)  → pick, margin, confidence, cost/point
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
- Code execution is off unless explicitly enabled, and its runner is defense-in-depth,
  not a sandbox (see README → Disclosure).
- No credentials are read; the host LLM facade is the only network path.
