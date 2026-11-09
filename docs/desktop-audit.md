# Model Kombat — Desktop Plugin Audit

Audited `desktop/plugin.js` line-by-line against the Desktop Plugin SDK
pitfalls/security sections in
`~/.hermes/cache/web/hermes-agent.nousresearch.com-e6a675d567.md`
(Security model §3015, Pitfalls §3045, Kanban card badges §1104,
One package both SDKs §2683). Line refs are to the post-fix file.

| # | Rule (SDK source) | Verdict | Evidence |
|---|---|---|---|
| 1 | Imports only `@hermes/plugin-sdk`, `react`, `react/jsx-runtime` (Pitfalls) | PASS | plugin.js:1-12 — exactly those three specifiers; no other `import`. |
| 2 | No JSX in a disk plugin; use `jsx()`/`jsxs()` (Pitfalls) | PASS | plugin.js:12,81+ — all elements via `jsx`/`jsxs`; `node --check` on an `.mjs` copy passes. |
| 3 | No prototype patching, no `eval`, no `MutationObserver` on app markup (SDK/catalog rules) | PASS | Grep finds none of `eval`, `MutationObserver`, `Object.prototype`, `document.`, `window.` in code (only in a comment at :581). Kanban badges use the sanctioned `KANBAN_CARD_AREAS.badges` contribution (:548-556), not DOM decoration. |
| 4 | No direct DOM access / no injected `<style>` (Pitfalls: bare globals survive disable) | PASS | No `document`/`window` reads or writes; styling is inline `style` objects with theme vars. |
| 5 | Never hardcode colors; use `var(--ui-*)` (Pitfalls; Theming) | PASS | All colors in style objects are `var(--ui-text)`, `var(--ui-text-tertiary)`, `var(--ui-border)`, `var(--ui-accent)`, `var(--ui-danger)`, `var(--ui-surface-hover)`. No `#hex`/`rgb()` literals. |
| 6 | Reference only imported identifiers (Pitfalls) | PASS | Every `jsx()` component is a DOM tag or a locally defined function (`KombatPane`, `KombatPage`, `KombatChip`); SDK imports are `PANES_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, STATUSBAR_AREAS, PALETTE_AREA, KANBAN_CARD_AREAS, KANBAN_CARD_BADGE_HUES, host` (:1-10), all used. Removed now-unused `useRef` import. |
| 7 | No bare `window.setInterval`/`addEventListener`; use `ctx.setInterval`/`ctx.setTimeout`/`ctx.addEventListener` (Pitfalls) | PASS | Polling at :582-583 uses `ctx.setInterval(loadLive, 5000)` and `ctx.setInterval(loadRecs, 30000)` — host-tracked and disposed with the plugin. |
| 8 | Don't poll faster than a few seconds with `host.request`; prefer `host.onEvent`/`ctx.socket` (Pitfalls) | PASS | Live state is push-driven via `host.onEvent('plugin.model-kombat.fight.updated')` (:588) with 5s/30s REST fallback. No `host.request` polling. |
| 9 | `ctx.socket` is a no-op on OAuth remotes — always have a polling fallback (Pitfalls) | PASS (N/A + fallback) | Plugin does not use `ctx.socket`; the `ctx.rest` polling fallback (:582-583) is the primary path and degrades to a plain status message when the backend is off (KombatPane/backendNote). |
| 10 | Module evaluation has a 10s deadline; no top-level `await` waiting on a gateway (Pitfalls) | PASS | No top-level `await`; all async work (`loadLive`, `loadRecs`, ladder/history fetch) happens inside `register()`/effects. |
| 11 | Hot-reload safe: module-scope state is yours to reset; ESM cannot unload, so wire disposers to `ctx.onDispose` (Pitfalls) | PASS after fix | **Fixed:** `host.onEvent` disposer was previously discarded — now stored and wired to `ctx.onDispose` (:588-594). Badge disposer is disposed before re-registering and on dispose (:547, :635-637). Module-scope stores (`liveListeners`, `recListeners`, `recMap`) reset on re-evaluation. **Fixed:** `KombatPage` used a `loadedRef` guard that blocked refetch on remount with stale ladder/history — removed; effect now refetches on mount/`ctx` change with a `cancelled` flag. |
| 12 | Kanban badge limits: label ≤24 chars, tooltip ≤160, ≤2 badges/card, tone from `KANBAN_CARD_BADGE_HUES`, one badge or array or null (Kanban §1181-1195) | PASS after fix | **Fixed:** `badgeFor` (:526-533) now `.slice(0, 24)` on label and `.slice(0, 160)` on tooltip (previously unbounded). Returns exactly one badge object (≤2) or `null`; tone is `KANBAN_CARD_BADGE_HUES[2]` (:8, :534). `decorate` is a pure lookup keyed by `card.id` into `recMap` — no card prose read. |
| 13 | Kanban updates/teardown: re-register with same `id` to repaint; keep latest disposer, first one would remove the replacement (Kanban §1209-1212) | PASS after fix | **Fixed:** `reRegisterBadges` now disposes the previous badge registration before re-registering (:547) and keeps only the latest disposer for `ctx.onDispose` (:635-637); the old code kept only the newest disposer but never disposed the previous registration. |
| 14 | Error isolation: contributions are error-bounded by the host; a throwing `register()` is rolled back (Security model) | PASS | Plugin relies on host error isolation (Security model: "a plugin can't crash the app"). All REST calls are wrapped in try/catch (`loadLive`, `loadRecs`, ladder/history) and render paths null-guard `live`/`recs`/`ladder`/`history`. No custom ErrorBoundary export exists in the SDK export list, so none is used. |
| 15 | Security: plugin runs with full app authority; import allowlist is the only tripwire — no remote imports, no `https:` dynamic imports (Security model) | PASS | No dynamic `import()`, no remote URLs, no `ctx.preview.executeJavaScript`, no raw `window.hermesDesktop` file bridge use. File/data access is only via `ctx.rest` to the plugin's own backend namespace. |
| 16 | One package, both SDKs layout: `plugin.yaml` + `dashboard/manifest.json` (`{"name","api":"plugin_api.py"}`) + `dashboard/plugin_api.py` (`router = APIRouter()`) + `desktop/plugin.js` (One package §2683) | PASS | `plugin.yaml` (`name: model-kombat`) at package root; `dashboard/manifest.json` = `{"name": "model-kombat", "api": "plugin_api.py"}`; `dashboard/plugin_api.py` exports `router = APIRouter()` with `/live`, `/ladder`, `/history`, `/recommendations`, `/health`; `desktop/plugin.js` exports `id: 'model-kombat'` and reaches the backend via `ctx.rest('/live')` etc. Folder name, manifest name and plugin `id` all agree (`model-kombat`) — no duplicate-id collision. |

## Fixes made in this round

1. Badge label/tooltip truncation to board limits (24/160 chars) — plugin.js:526-533.
2. `host.onEvent` disposer wired to `ctx.onDispose` (was discarded) — plugin.js:588-594.
3. Previous kanban badge registration disposed before re-register (was only the newest kept) — plugin.js:547.
4. Removed `loadedRef` stale-fetch guard in `KombatPage` and the now-unused `useRef` import — plugin.js:11, 369-374.
5. Scorecard Result column added to the desktop table with DRAW/SKIPPED/ERROR logic mirroring `render.py` — plugin.js:173-189 (`resultCell`), header at :213, cell at :232-234; `taskCell` now shows SKIPPED/ERROR instead of `—` (:132).
6. Trademark-adjacent wording removed: subtitle `insert coin · press start` → `Head-to-Head Benchmark Arena`; status `FIGHT!` → `FIGHT ON`, `FIGHT OVER` kept, errors → `BOUT ERROR — CHECK BELOW`, idle → `STANDBY — NO BOUT ON DECK` — plugin.js:249-256, 421-424.

## Verification

- `cp desktop/plugin.js /tmp/plugin_check.mjs && node --check /tmp/plugin_check.mjs` → `NODE_CHECK_OK`.
- No `document.`/`window.`/`eval`/`MutationObserver`/prototype patching in code (grep).
