# Scout Report: Model Kombat Plugin

## 1. CATALOG OVERLAP — 8 Closest Entries

| # | Name | One-line | How Model Kombat Differs |
|---|------|----------|--------------------------|
| 1 | **model-drift-watch** | Scheduled check that the model you use has not quietly got worse (34 fixed questions, per-question exact grading, cron-scheduled) | Model Kombat does adaptive head-to-head fights with mechanical scoring (code exec, exact/phrases/regex checks), not drift monitoring. Kombat builds a fight card from kanban lanes; drift-watch runs fixed question sets via cron. |
| 2 | **model-leaderboard** | Full /model-leaderboard page ranking OpenRouter models (context window, prices, SWE-bench, LF benchmarks, score views, REST surface) | Model Kombat pits two specific models in 3+1 rounds with a live kanban scoreboard. Leaderboard ranks *all* OpenRouter models globally; Kombat is a pairwise evaluation tool. |
| 3 | **evalroute** | Route a task to the right (model, reasoning effort) arm before starting; classifies task into a lane, prints a route card, installs per-model reasoning-effort defaults | Model Kombat assumes the task lane is already known and builds a fight card from it. Evalroute does local classification + optional provider calls; Kombat mechanically scores both fighters on the same tasks. |
| 4 | **arena-skill** | Tournament of N sub-agents competing to produce the best answer; runs bundled bracket.py through terminal | Model Kombat is exactly 2 models, not N agents. Kombat uses mechanical scoring (code exec, counts, fact matching), not agent-vs-agent competition. Kombat's scoring is per-task and deterministic; arena-skill costs scale with agent count. |
| 5 | **model-router** | Profile-based model routing; explicit per-profile readiness and native missing-profile preparation; routing defaults ON for every installed profile | Model Kombat does not route jobs to different models; it pits two fixed models against each other on the same task set. Model-router manages profile-level model selection and delegation; Kombat is a fixed fighting framework. |
| 6 | **jev-model-router** | One skill + one stdio MCP server (model_route) started with uv run; registers no Hermes tools or hooks | Model Kombat registers 7 tools (kombat_list_tasks, kombat_run_fight, etc.) and integrates with Hermes kanban. Jev-model-router is a minimal MCP-based router with no Hermes tool registration. |
| 7 | **jev** | Explicit Jev decision sidekick via TypeSafe or Cloudflare; advisory judgments, no automatic tool gating | Model Kombat mechanically scores answers (code exec, exact/phrases/regex checks). Jev provides advisory judgments only — it never scores or gates tool execution. |
| 8 | **jev-typesafe** | TypeSafe Jev decision tools — atomic yes/no checks, routing, rubric scoring, mixed typed questions with calibrated probabilities | Model Kombat uses deterministic mechanical scorers (exact, numeric, phrases, regex, constraints, json_fields, code_exec, sql_exec). Jev-typesafe relies on external API calls (TYPESAFE_API_KEY) and probabilistic rubric scoring. |

**Sources:** plugin-catalog/*.yaml lines 1–5 for each entry (read_file above). All entries pin exact 40-char SHAs and declare `provides_tools:` or similar capabilities.

---

## 2. DESKTOP ARCADE-STYLE/VERSUS UI OR KANBAN-CARD BADGES

**No existing catalog entry ships a desktop arcade-versus UI or kanban-card badges.** 

- The only entry with a desktop pane is **jev-model-router**, but its description mentions no UI beyond the stdio MCP server.
- **model-drift-watch**, **model-leaderboard**, **evalroute**, **arena-skill**, **model-router**, **jev**, **jev-typesafe** all describe tools/hooks/middleware with no mention of desktop panes, status-bar chips, or kanban badges.
- Model Kombat itself (per SPEC.md §7–8, README.md) plans `desktop/plugin.js` (roadmap item 1) and `dashboard/plugin_api.py` for kanban card badges from `recommendations.json`, but neither exists yet in the repo.

**Sources:** plugin-catalog/*.yaml descriptions (read_file above); SPEC.md §7–8, §9 (read_file); README.md roadmap (read_file).

---

## 3. EXACT SDK FACTS

From `~/.hermes/cache/web/hermes-agent.nousresearch.com-e6a675d567.md` (the desktop plugin SDK reference):

**Exported area constants:**
- `PANES_AREA` = `'panes'` — layout pane registration (placement: main/left/right/top/bottom, optional dock).
- `ROUTES_AREA` — full page route registration (`data: { path: '/kombat' }`).
- `SIDEBAR_NAV_AREA` — sidebar nav row registration (`data: { path, label, codicon }`).
- `STATUSBAR_AREAS` — left/right status-bar item clusters (`STATUSBAR_AREAS.right` for a chip).
- `PALETTE_AREA` — ⌘K palette command registration (`data: { id, label, keywords, run }`).
- `KANBAN_CARD_AREAS` — kanban card badges (`data: KanbanCardBadgeContribution` for per-card short text labels).

**Pattern for a route page + sidebar nav + status chip:**
```js
ctx.registerMany([
  {
    id: 'page',
    area: ROUTES_AREA,
    data: { path: '/kombat' },
    render: () => jsx(MyPage, {}),
  },
  {
    id: 'nav',
    area: SIDEBAR_NAV_AREA,
    data: { path: '/kombat', label: 'Model Kombat', codicon: 'combat' },
  },
])
```
Status chip example:
```js
ctx.register({
  id: 'chip',
  area: STATUSBAR_AREAS.right,
  order: 130,
  render: () => jsx('button', {
    type: 'button',
    className: 'px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
    onClick: () => host.notify({ kind: 'info', message: 'Hello' }),
    children: 'kombat',
  }),
})
```

**`host.onEvent` + `ctx.rest` pattern:**
- `host.onEvent('plugin.model-kombat.fight.updated', fn)` subscribes to engine‑written `live.json` events.
- `ctx.rest('/live')` → `GET /api/plugins/model-kombat/live` (REST polling fallback).
- Engine broadcasts: `hermes_cli.plugin_events.broadcast_plugin_event("model-kombat", "fight.updated", live)`.

**`plugin_api.py` handling of HERMES_HOME / plugin data dir:**
- Put backend in `dashboard/` subfolder with `manifest.json: {"name": "model-kombat", "api": "plugin_api.py"}`.
- Routes mount under `/api/plugins/<id>/` (`GET /api/plugins/model-kombat/live`, `GET /ladder`, `GET /history`, `GET /recommendations`, `GET /health`).
- Every route returns JSON, never raises (empty/default objects when files are missing).
- **Data dir resolution:** honor `HERMES_HOME`; fall back to `~/.hermes/plugin-data/model-kombat`.
- Python backend runs inside the gateway process, can import from the hermes-agent codebase directly.
- Project plugins (`./.hermes/`) never auto-import Python — security boundary (GHSA-mcfc-hp25-cjv7).
- Routes: `/live`, `/ladder`, `/history?limit=`, `/recommendations`, `/health`.

**Sources:** SDK markdown file (read_file above, lines covering area constants, contribution patterns, `host.onEvent`, `ctx.rest`, `plugin_api.py` backend mount).

---

## 4. CATALOG RULES (from `~/.hermes/hermes-agent/plugin-catalog/README.md`)

Most relevant rules for a plugin that ships a `desktop/` folder, a `dashboard/plugin_api.py`, executes model-written code in a subprocess, and writes kanban cards via terminal:

| Rule | Quote (exact) | Relevance |
|------|---------------|-----------|
| 3 | **No self-updating code.** *A listed plugin must not fetch and replace its own files* (in-app "check for updates", signed release downloaders, remote `plugin.js` loaders). The exact SHA pin *is* the trust model. | The model-kombat `desktop/plugin.js` and `dashboard/plugin_api.py` are pinned to the exact commit SHA in `catalog/model-kombat.yaml`; no runtime code fetch. |
| 6 | **Declared capabilities must match reality.** *The `capabilities:` block (tools, hooks, middleware, env vars) must match what the plugin actually registers at the pinned commit. Undeclared capability creep is treated as a security issue.* | Model Kombat declares `provides_tools: [kombat_list_tasks, kombat_run_fight, …]`. The `dashboard/plugin_api.py` must only register routes that actually exist; the `desktop/plugin.js` must only use SDK imports (`@hermes/plugin-sdk`, `react`). |
| 8 | **Desktop plugins stay inside the SDK surface.** *A `desktop/plugin.js` runs in the Desktop renderer with the app's full authority… so a listed one may only use the plugin SDK: no prototype patching of renderer or browser objects, no `eval`/`new Function`, no `import()` of anything but `@hermes/plugin-sdk` / `react` (app bundle chunks, blob or http URLs included), no script-tag injection, no reaching into the app's internal stores or its own markup.* | Model Kombat's `desktop/plugin.js` (planned) must obey the SDK import whitelist. No `eval`, no dynamic `import()` of external scripts. |
| 12 | **Approvals and unattended runs are respected.** *A plugin never routes around Hermes's approval system: no auto-approving, no disabling guards, and no spawning Hermes or shell children that inherit YOLO or non-interactive mode to run commands nobody approved.* | Model Kombat's `allow_code_execution` runs model-written Python in a subprocess with a 10s timeout — off by default, excluded from points when off. The terminal/kanban card writes go through the host's `terminal` tool with an approval pipeline (SPEC.md §12, disclosure line 122–124). |
| 13 | **Risky behaviour is disclosed.** *What a user would want to know before installing goes in the PR description and the plugin's README: network calls, reads outside the plugin's own data, shell commands, long-running background processes, stored credentials.* | Model Kombat's disclosure (README.md §113–127) explicitly mentions: code execution runs as user with timeout (NOT a security sandbox), kanban writes via host terminal tool (approval pipeline), reports/history live under `~/.hermes/plugin-data/model-kombat/`. |
| 17 | **No unscannable executable payloads.** *Everything a reviewer and the install scanner read at the pinned commit must be source they can read. A plugin must not ship a compiled binary… or an archive whose members are not themselves reviewable source.* | Model Kombat's `tools.py` contains `code_exec` scorer that executes model-written Python in a subprocess (stripped env, tempdir cwd, 10s timeout). This is source code, not a committed binary, so it passes scrutiny. |

**Sources:** `plugin-catalog/README.md` full text (read_file above, all 259 lines). Quoted verbatim; anything unverified is marked "could not verify."

---

## Summary

- **8 closest catalog entries** identified covering model drifting, leaderboarding, routing, arena tournaments, model routing, and Jev decision tools. Model Kombat differs from each in purpose, scoring mechanism, and UI paradigm.
- **No existing entry** ships a desktop arcade-versus UI or kanban-card badges; Model Kombat plans both (desktop/plugin.js roadmap, dashboard/plugin_api.py for kanban badges from `recommendations.json`).
- **SDK facts** exact: `PANES_AREA`, `ROUTES_AREA`, `SIDEBAR_NAV_AREA`, `STATUSBAR_AREAS`, `PALETTE_AREA`, `KANBAN_CARD_AREAS`; route+sidebar+chip pattern; `host.onEvent` + `ctx.rest` event broadcasting; `plugin_api.py` data dir resolution (HERMES_HOME → `~/.hermes/plugin-data/<id>`).
- **Catalog rules** most relevant: no self-updating code (rule 3), capabilities must match reality (rule 6), desktop SDK surface only (rule 8), approvals respected (rule 12), risky behavior disclosed (rule 13), no unscannable payloads (rule 17).

**File written:** `/home/themightyvh/src/model-kombat/docs/scout-report.md` (this file only).