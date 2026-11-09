# Demo — 75 seconds, shot by shot

Hackathon submission video. Target 75 s, hard ceiling 90 s. 1920×1080, Hermes
Desktop, dark theme, one window: chat on the left, **Model Kombat** pane pinned
right, kanban board one click away. No voice-over required — the captions carry
the story, so record with the mic muted and the cursor moving deliberately.

**Cast:** `stealth/crouton-alpha` (Crouton) vs `deepseek/deepseek-v4.1-flash-1017` (DS-Flash).
**Board:** `kombat-demo`. **Card:** `t_9f21c4` — *"Checkout totals refactor"* (body: a
rounding bug in the totals function, plus a note that tax must stay 2 decimals).

---

### 0:00–0:06 — Cold open

**Screen:** the PM's board, card `t_9f21c4` highlighted under the cursor.
**Caption:** `Picking a model for this card used to mean two sessions and a spreadsheet.`

### 0:06–0:14 — Step 1: pick the work

**Screen:** hover the card so its ID is visible, then switch to chat. Type:

```
/kombat scout card:t_9f21c4
```

**Caption:** `Step 1 — scout the card. Local keyword rules, no LLM call.`

### 0:14–0:26 — The adaptive card

**Screen:** the tool result scrolls: `classified lanes: coding (…), debugging (…), sql (…)`,
then R1 Warm-up 1pt / R2 Main Event 2pt / R3 Championship 3pt / sudden death, each with
task IDs. Hold on the `"card": {"id": "card-…"}` line.
**Caption:** `Eight lanes. Three scored rounds. Same input always builds the same card.`

### 0:26–0:32 — Step 2: start the fight

**Screen:** type in chat (plain language — the bundled skill calls `kombat_run_fight`
with the card from step 1):

```
Fight stealth/crouton-alpha vs deepseek/deepseek-v4.1-flash-1017 on card-<id from the scout>
```

**Caption:** `Step 2 — two models, identical prompts, one card.`

> Use this prompt rather than `/kombat fight`: the slash command always runs the
> fixed legacy suite, and the story here is "fight the card I just scouted".

### 0:32–0:55 — Live fight

**Screen:** cut to the arena pane (or `/kombat` page) and the `model-kombat` board side by
side. HP bars drain as rounds resolve; the banner moves `ROUND 1 — WARM-UP` →
`ROUND 2 — MAIN EVENT` → `ROUND 3 — CHAMPIONSHIP`; task rows flash hits; board cards move
`ready` → `in progress` → `done`. Let it run in real time for ~15 s, then a 2–3 s jump cut.
**Caption:** `Live: every answer scored as it lands — executed code, counts, SQL rows.`
**Caption (second, small):** `No model judges another model.`

### 0:55–1:04 — Verdict

**Screen:** the pane shows the K.O. stamp (or `WINNER: <model>` on points). Cut to the
scorecard report: the round table with per-task detail (`4/4 cases`, `25 words: 25 ✓`,
`rows match`) and the usage table (latency / tokens / cost per fighter).
**Caption:** `K.O. — every Championship task won while the other side missed one.`

### 1:04–1:12 — Step 3: ladder + badge

**Screen:** `/kombat ladder` prints the per-lane Elo table; then `/kombat recommend board`
writes the picks. Cut back to the board: card `t_9f21c4` now carries the recommendation
badge, tooltip open (`Kombat pick: … · lane: coding · confidence: medium · elo: 1512`).
**Caption:** `Step 3 — per-lane Elo, cost per point, and the pick on the card itself.`

### 1:12–1:15 — Close

**Screen:** repo URL over the arena pane.
**Caption:** `Model Kombat — same prompts, both models, scored by execution.`

---

## Fallback plan (rate limits)

Providers 429 mid-take. Do **not** re-record live if it happens; cut instead.

1. Before recording, run the fight once off-camera (a warm-up run also warms the
   rate-limit window). Keep its scorecard and the populated `live.json` open in a tab.
2. If the live fight stalls, cut from the start prompt straight to the finished arena and
   put this caption on screen for the whole fight segment:
   `Fight pre-run — scoring is live.`
3. Keep `/kombat score` and `/kombat ladder` live: they make no model calls, so the payoff
   shots (scorecard, Elo, badge) are always genuine in-take.
4. If a task errors visibly, leave it in: the error row plus the caption
   `errored ≠ failed — excluded from points` is a feature demo, not a blooper.

## Shot list for the editor

| # | In | Out | Content |
|---|---|---|---|
| 1 | 0:00 | 0:06 | board, card highlighted |
| 2 | 0:06 | 0:14 | `/kombat scout card:t_9f21c4` |
| 3 | 0:14 | 0:26 | lanes + card JSON |
| 4 | 0:26 | 0:32 | fight prompt |
| 5 | 0:32 | 0:55 | arena pane + board, live |
| 6 | 0:55 | 1:04 | K.O. + scorecard |
| 7 | 1:04 | 1:12 | ladder + badge |
| 8 | 1:12 | 1:15 | close card |

Total: 75 s. If a shot overruns, cut shot 5 first (it is the only one that can be
compressed to a 5 s loop) and keep 6–7 intact.

## Pre-record checklist

- [ ] `llm.allow_model_override` on, both model IDs in `allowed_models`
- [ ] `show_on_kanban: true`, board `kombat-demo` created, card `t_9f21c4` on it
- [ ] Desktop restarted after the last plugin edit (tool list + pane reload)
- [ ] One off-camera fight of the same card already scored (fallback footage)
- [ ] Notifications off, terminal history clean, no other windows in frame
