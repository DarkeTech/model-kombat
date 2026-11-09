# Model Kombat v0.4 — build contract (orchestrator-owned; do not edit)

Goal: adaptive, project-aware head-to-head model evaluation with an arcade-fighter UI.
A PM points Kombat at a task or kanban card → it picks the lanes that task needs → builds a
fight card (rounds) from those lanes → fights two models → scores MECHANICALLY → recommends
the knockout pick per lane. No model ever judges a model.

Deadline: Wed 2026-11-11 17:00 EST. Repo: ~/src/model-kombat (symlinked in ~/.hermes/plugins/).

## 1. Lanes (final list; supersedes any other list)
`coding` · `debugging` · `extraction` · `summarization` · `reasoning` · `instruction` · `recall` · `sql`

## 2. Task schema (tasks.json legacy T1–T8 stay valid; new pool in tasks/<lane>.json)
Each file: `{"lane": "<lane>", "tasks": [ ... ]}`. Each task:
```json
{
  "id": "coding-007",            // unique: <lane>-NNN (legacy: T1..T8)
  "lane": "coding",
  "difficulty": 1,               // 1 easy, 2 medium, 3 hard
  "title": "short human title",
  "prompt": "full prompt text sent verbatim to both fighters",
  "check": { ... },              // MECHANICAL check spec, see §3
  "reference": "an answer that must score 1.0 against its own check"
}
```
Legacy tasks keep their `scorer` field; the engine maps them (add `lane` + `difficulty`, keep behavior).

## 3. Check specs (`check.type`) — score is a float 0..1, with a human `detail` string
- `exact`        `{"type":"exact","expected":"42","normalize":true}` — whole-answer match after trim/case/whitespace norm (also accepts the expected value as the last non-empty line).
- `numeric`      `{"type":"numeric","expected":[44.3,5.7,12.2],"tol":0.01}` — each expected number must appear in the answer; score = fraction found.
- `phrases`      `{"type":"phrases","expected":[["sol 43"],["tomas beck"],["no injuries","no one was injured"]]}` — each inner list = acceptable alternatives (case/space-insensitive); score = fraction of groups hit.
- `regex`        `{"type":"regex","pattern":"^\\d{4}-\\d{2}-\\d{2}$","flags":"im","must_match":true}` — 1.0 / 0.0.
- `constraints`  `{"type":"constraints","words":{"min":25,"max":25},"sentences":3,"starts_with":"Stay","ends_with":".","banned":["amazing"],"contains":["x"]}` — every present key is one equal-weight sub-check; score = fraction passed.
- `json_fields`  `{"type":"json_fields","expected":{"vendor":"Acme","total":123.45,"dates":["2026-01-02"]},"tol":0.01}` — the answer must contain a JSON object (first fenced or bare object); score = fraction of expected leaf fields matching exactly (numbers within tol).
- `code_exec`    `{"type":"code_exec","entrypoint":"is_leap_year","cases":[{"args":[2000],"expect":true},{"args":["x"],"raises":"TypeError"},{"args":[[3,1,2]],"expect":2}],"timeout_s":10}` — code is extracted from the first fenced ```python block (or whole answer), executed in the hardened runner, every case is a sub-check; score = fraction passed. Only runs when `allow_code_execution` is true, otherwise the task is `skipped` (never failed).
- `sql_exec`     `{"type":"sql_exec","setup":["CREATE TABLE ...","INSERT ..."],"expect_rows":[[1,"a"],[2,"b"]],"ordered":false}` — answer's SQL (first fenced ```sql block) runs against an in-memory sqlite3 DB (stdlib, no code exec risk beyond sqlite; read-only statements only: SELECT/WITH), result rows compared to expected.
Rules: exactly one defensible answer; no date/web dependence; no memorizable trivia; the `reference` MUST score 1.0 (tests enforce).

## 4. Fight card (adaptive rounds)
`kombat_scout(input)` — `input` = free text, or `{"card": "<kanban task id>"}`, or `{"board": true}` (all open cards on the active kanban board).
1. Local rules classifier (keyword/regex weights per lane; NO LLM call) → `lanes: [{"lane","weight"}]` sorted, top 3 with weight>0; fallback `reasoning`+`instruction`.
2. Builds a fight card, deterministic given (lanes, seed): 
   - R1 Warm-up 1pt: 2 difficulty-1 tasks from top lane(s)
   - R2 Main Event 2pt: 2–3 difficulty-2 tasks spread over top 2 lanes
   - R3 Championship 3pt: 1–2 difficulty-3 tasks from the top lane (weighted toward code/sql if present)
   - Sudden death: 1 difficulty-2/3 task from the top lane
   Optional project tasks: `<cwd>/.kombat/tasks.json` or path arg, same schema, mixed in at the lane's R2.
3. Returns `{"card": {"id","title","lanes","seed","rounds":[{"id","name","points","tasks":[ids]}],"sudden_death_task"}, "reasons": [...]}`.
`kombat_run_fight` accepts `card` (the object above, or a stored card id) in addition to current args; no `card` = legacy fixed T1–T8 suite.

## 5. Ladder + recommendation
`ladder.json` in plugin data dir, updated when a fight is scored (per-lane, per-model Elo, K=24, start 1500; a lane task result adds a bout per lane in the card):
```json
{"updated":"ISO","lanes":{"coding":{"models":{"deepseek/x":{"elo":1512.3,"wins":3,"losses":1,"draws":0,"fights":4,"points":14.5,"cost_usd":0.012,"latency_s":41.2}}}}}
```
`kombat_recommend({"lane"|"input"|"card":..., "candidates":[ids]?})` → `{"pick","lane","margin","confidence":"low|medium|high","fights","cost_per_point","runner_up","why"}`; confidence from fight count (<3 low, 3–7 medium, ≥8 high) and Elo margin. Also writes `recommendations.json` keyed by kanban task id for the board badges:
```json
{"t_abc123":{"lane":"coding","pick":"deepseek/deepseek-v4.1-flash-1017","short":"DS-Flash","confidence":"medium","elo":1512,"updated":"ISO"}}
```

## 6. Live state + events (engine writes, UI reads)
Engine writes `live.json` (plugin data dir) atomically at every state change AND best-effort calls
`hermes_cli.plugin_events.broadcast_plugin_event("model-kombat","fight.updated", live)` (try/except; no-op if unavailable).
```json
{
  "status":"idle|fighting|scoring|done|error",
  "fight_id":"20261110-101500",
  "card":{"title":"...","lanes":["coding","debugging"]},
  "fighters":{"a":{"id":"stealth/crouton-alpha","short":"Crouton"},"b":{"id":"deepseek/deepseek-v4.1-flash-1017","short":"DS-Flash"}},
  "hp":{"a":100,"b":100},                       // 100 - damage; damage = opponent's lead in points scaled to total
  "rounds":[{"id":"R1","name":"Warm-up","points":1,"state":"waiting|fighting|done",
    "tasks":[{"id":"coding-003","lane":"coding","title":"...",
      "a":{"state":"waiting|fighting|done|error|skipped","score":0.75,"latency_s":4.2,"cost_usd":0.0003,"detail":"3/4 cases"},
      "b":{...}}]}],
  "sudden_death":null,
  "winner":null,"ko":false,"points":{"a":0,"b":0},"usage":{"a":{"cost_usd":0,"tokens":0,"latency_s":0},"b":{...}},
  "report_path":null,"updated":"ISO"
}
```
Event name on the wire: `plugin.model-kombat.fight.updated`, payload = the live object.

## 7. Backend + Desktop (artist-owned)
`dashboard/manifest.json` `{"name":"model-kombat","api":"plugin_api.py"}`; `dashboard/plugin_api.py` exports `router` (FastAPI APIRouter), READ-ONLY routes that read the plugin data dir (honor HERMES_HOME; fall back to ~/.hermes/plugin-data/model-kombat):
`GET /live` · `GET /ladder` · `GET /history?limit=` · `GET /recommendations` · `GET /health`. Every route returns JSON, never raises (empty/default objects when files are missing).
`desktop/plugin.js` default-exports a HermesPlugin id `model-kombat`: arcade versus pane (placement right), full page route `/kombat` + sidebar nav, status-bar chip, kanban card badge from recommendations.json, palette command "Open Model Kombat", `ctx.rest` + `host.onEvent('plugin.model-kombat.fight.updated')` with REST polling fallback.
`render.py`: `render_report(fighter_a, fighter_b, rounds_out, usage, winner, ko) -> str` (self-contained HTML, no external assets) — same inputs as the current `tools._render_report`; plus `render_arena_preview(live: dict) -> str` for a standalone preview page. Engine will delegate to it (fallback to old renderer on import error).

## 8. Look & feel (original — no third-party marks/sprites/audio/slogans)
16-bit arcade fighter: top HP bars with fighter names, round timer/“ROUND 2 — MAIN EVENT” banner, stakes shown as 1/2/3 stars, per-task “hit” flashes, K.O. flash + “K.O.” stamp, winner pose card, scanline + pixel-font via CSS only (system monospace fallback; no remote fonts). Respect `prefers-reduced-motion`. Use Hermes theme CSS vars in the Desktop pane; the standalone HTML scorecard has its own dark arcade palette.

## 9. Non-negotiables (referee enforces)
Handlers return JSON & never raise · errored ≠ failed · budget cap enforced · no credential reads · code-exec off by default, stripped env, tempdir cwd, timeouts · provides_tools == registered tools · no `__pycache__`/binaries committed · Desktop: no JSX, no eval, only allowed imports.

## 10. File ownership
engine-smith: tools.py kanban.py schemas.py __init__.py plugin.yaml tests/ (not test_referee_*)
arcade-artist: desktop/ dashboard/ render.py
task-forger: tasks.json tasks/
referee: tests/test_referee_*.py (+ reports)
release-marshal: README.md DEMO.md catalog/ docs/ (except SPEC.md) LICENSE .gitignore
Nobody commits or pushes. The orchestrator commits at merge points.
