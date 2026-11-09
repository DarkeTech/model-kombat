# Demo — 75–85 seconds, shot by shot

Catalog demo video. Target **80 s**, hard ceiling 85 s. 1920×1080,
Hermes Desktop, dark theme, one window: chat on the left, **Model Kombat** pane
pinned right, kanban board one click away. No voice-over required — the captions
carry the story, so record with the mic muted and the cursor moving deliberately.

**Cast:** `stealth/crouton-alpha` (Crouton) vs `deepseek/deepseek-v4.1-flash-1017` (DS-Flash).
**Board:** `kombat-demo`. **Card:** `t_9f21c4` — *"Checkout totals refactor"* (body: a
rounding bug in the totals function, plus a note that tax must stay 2 decimals).

The video is honest about draws: the card is fought at `--gauntlet` (every task
difficulty 3) because that is the setting that actually separates strong models,
and a separate draw result is shown for three seconds as a real outcome, not a
failure. The final pair after the live fight is `{WINNER}` / `{LOSER}` — fill
those in from the take you actually record.

---

### 0:00–0:05 — Cold open

**Screen:** the PM's board, card `t_9f21c4` highlighted under the cursor.
**Caption:** `Picking a model for this card used to mean two sessions and a spreadsheet.`

### 0:05–0:13 — Step 1: scout the card at gauntlet intensity

**Screen:** hover the card so its ID is visible, then switch to chat. Type:

```
/kombat scout card:t_9f21c4 --gauntlet
```

**Caption:** `Step 1 — scout the card. Local keyword rules, no LLM call. --gauntlet = every task difficulty 3.`

### 0:13–0:23 — The adaptive card

**Screen:** the tool result scrolls: `classified lanes: coding (…), debugging (…)`,
`intensity: gauntlet (expected discrimination: low|medium|high)`, then R1 Warm-up
1pt / R2 Main Event 2pt / R3 Championship 3pt / `sudden death: … (fought up
front, used only on a tie)`, each with task IDs. Hold on the
`Next: /kombat fight … --card card-…` line.
**Caption:** `Eight lanes. Three scored rounds. The same input always builds the same card.`

### 0:23–0:29 — Step 2: start the fight

**Screen:** copy the exact next command the scout printed (card id differs per
machine — `{CARD_ID}` below) and run it:

```
/kombat fight stealth/crouton-alpha vs deepseek/deepseek-v4.1-flash-1017 --card {CARD_ID}
```

**Caption:** `Step 2 — two models, identical prompts, one card.`

> Pure fights run as a background job, so the chat stays usable. Use this instead
> of `/kombat fight` with no `--card`: that would run the fixed legacy suite, and
> the story here is "fight the card I just scouted".

### 0:29–0:52 — Live fight

**Screen:** cut to the arena pane (or `/kombat` page) and the `model-kombat` board
side by side. HP bars drain as rounds resolve; the banner moves
`ROUND 1 — WARM-UP` → `ROUND 2 — MAIN EVENT` → `ROUND 3 — CHAMPIONSHIP`; task
rows flash hits; board cards move `ready` → `in progress` → `done`. The
sudden-death answers are already fetched here but stay invisible unless the
points tie. Let it run in real time for ~15 s, then a 2–3 s jump cut.
**Caption:** `Live: every answer scored as it lands — executed code, counts, SQL rows.`
**Caption (second, small):** `No model judges another model.`

### 0:52–1:02 — Decisive mixed-tier result

**Screen:** the pane shows the K.O. stamp, or `WINNER: {WINNER}` on points. Cut to
the scorecard report: the round table with per-task detail (`4/4 cases`,
`25 words: 25 ✓`, `rows match`) and the usage table with `cost_source`
(`host` / `estimated` / `n/a` per fighter).
**Caption:** `{WINNER} takes the d3 rounds; {LOSER} takes the warm-ups. Decisive on the hard tasks.`

> Mixed-tier is the point: a model that wins only the easy tasks does not win the
> card. If the recorded take is a tie instead, use the draw beat below and skip
> this win caption — do not fabricate a winner.

### 1:02–1:10 — Step 3: ladder + badge (≈ vs clear edge)

**Screen:** `/kombat ladder` prints the per-lane Elo table; then:

```
/kombat recommend board
```

Cut back to the board. Card `t_9f21c4` now carries a badge whose tooltip reads
`Kombat pick: … · lane: coding · verdict: clear edge · confidence: medium · elo: 15xx`.
Point the cursor at a second card whose top-two margin is under 15 Elo: its badge
short name starts with `≈` and the tooltip says `verdict: no clear edge` —
`why: … and … are within 8.4 Elo — this is an efficiency tiebreak (latency/cost), not a performance win.`
**Caption:** `Step 3 — per-lane Elo and the pick on the card. ≈ means "no clear edge", not "winner".`

### 1:10–1:13 — Honest-result beat: the draw screen

**Screen:** cut to the Desktop arena showing a real draw
(`docs/previews/arena_draw_edge.png` is the reference frame): `DRAW` banner,
equal points, and the line
`efficiency edge: Crouton — faster total latency (18.400s vs 21.500s) — efficiency edge (not a win)`.
Hold exactly 3 s. No zoom, no stamp.
**Caption:** `Strong models tie. This is a draw — the cheaper model gets an efficiency note, not a win.`

### 1:13–1:20 — Close

**Screen:** repo URL over the arena pane.
**Caption:** `Model Kombat — same prompts, both models, scored by execution.`

---

## Fallback plan (rate limits)

Providers 429 mid-take. Do **not** re-record live if it happens; cut instead.

1. Before recording, run the fight once off-camera at the same `--gauntlet`
   intensity (a warm-up also warms the rate-limit window). Keep its scorecard and
   the populated `live.json` open in a tab.
2. If the live fight stalls, cut from the start prompt straight to the finished
   arena and put this caption on screen for the whole fight segment:
   `Fight pre-run — scoring is live.`
3. Keep `/kombat score`, `/kombat ladder` and `/kombat recommend board` live: they
   make no model calls, so the payoff shots (scorecard, Elo, badges) are always
   genuine in-take.
4. If a task errors visibly, leave it in: the error row plus the caption
   `errored ≠ failed — excluded from points` is a feature demo, not a blooper.
5. If the fight hits `BUDGET CAP HIT — remaining tasks skipped`, either raise
   `max_fight_tokens` before recording or keep the shot and caption it
   `budget cap fired — the rest was skipped, not scored as a loss`.

## Shot list for the editor

| # | In | Out | Content |
|---|---|---|---|
| 1 | 0:00 | 0:05 | board, card highlighted |
| 2 | 0:05 | 0:13 | `/kombat scout card:t_9f21c4 --gauntlet` |
| 3 | 0:13 | 0:23 | lanes, intensity, card JSON |
| 4 | 0:23 | 0:29 | `/kombat fight … --card {CARD_ID}` |
| 5 | 0:29 | 0:52 | arena pane + board, live |
| 6 | 0:52 | 1:02 | `WINNER: {WINNER}` + scorecard |
| 7 | 1:02 | 1:10 | `/kombat ladder` + `recommend board`, ≈ vs clear edge |
| 8 | 1:10 | 1:13 | Desktop draw screen, 3 s honest beat |
| 9 | 1:13 | 1:20 | close card |

Total: 80 s. If a shot overruns, cut shot 5 first (the only one that can be
compressed to a 5 s loop) and keep 6–8 intact. If the take lands at 75 s, still
inside spec.

## Pre-record checklist

- [ ] `llm.allow_model_override` on, both model IDs in `allowed_models`
- [ ] `show_on_kanban: true`, board `kombat-demo` created, card `t_9f21c4` on it
- [ ] A second card on the board whose ladder margin is under 15 Elo (for the `≈` badge shot)
- [ ] `max_fight_cost_usd` / `max_fight_tokens` high enough for a full gauntlet card
- [ ] Desktop restarted after the last plugin edit (tool list + pane reload)
- [ ] One off-camera gauntlet fight of the same card already scored (fallback footage)
- [ ] `{WINNER}` / `{LOSER}` filled from the actual take before export
- [ ] Notifications off, terminal history clean, no other windows in frame
