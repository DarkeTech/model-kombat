# Demo — 75–85 seconds, shot by shot

Catalog demo video. Target **80 s**, hard ceiling 85 s. 1920×1080,
Hermes Desktop, dark theme, one window: chat on the left, **Model Kombat** pane
pinned right, kanban board one click away. No voice-over required — the captions
carry the story, so record with the mic muted and the cursor moving deliberately.

**Cast (verified, 2026-11-10):** `nvidia/nemotron-3.5-super-vl` (Nemo-VL) vs
`deepseek/deepseek-v4.1-flash-1017` (DS-Flash).
**Board:** `kombat-demo`. **Card:** `t_9f21c4` — *"Checkout totals refactor"* (body: a
rounding bug in the totals function, plus a note that tax must stay 2 decimals).

This pair is proven decisive at **`--hard`** (card `card-c2a527047b`, fight
`20261110-101512`, 0 errors, no time cap): 3.0–3.0 after three rounds, then sudden
death on `coding-011` — **DS-Flash passes 19/19 cases, Nemo-VL emits no code
block at all.** DS-Flash wins, 63.7s vs 261.4s latency, ~$0.017 total.

Do **not** scout this demo card at `--gauntlet`: at d3 a slow model can burn the
180s per-call deadline and get excluded (`nemotron-3.5-lightning` did exactly
that — 14 errored calls, R2/R3 excluded, no comparison). `--hard` is the setting
that produced a real result.

The video is honest about draws: one real draw screen is shown for three seconds
as an outcome, not a failure, and the value beat explains what a draw still tells
you. Fill `{WINNER}` / `{LOSER}` from the take you actually record.

---

### 0:00–0:05 — Cold open

**Screen:** the PM's board, card `t_9f21c4` highlighted under the cursor.
**Caption:** `Picking a model for this card used to mean two sessions and a spreadsheet.`

### 0:05–0:13 — Step 1: scout the card at hard intensity

**Screen:** hover the card so its ID is visible, then switch to chat. Type:

```
/kombat scout card:t_9f21c4 --hard
```

**Caption:** `Step 1 — scout the card. Local keyword rules, no LLM call. --hard = difficulty 2–3 only.`

### 0:13–0:23 — The adaptive card

**Screen:** the tool result scrolls: `classified lanes: coding (…), debugging (…)`,
`intensity: hard (expected discrimination: low|medium|high)`, then R1 Warm-up
1pt / R2 Main Event 2pt / R3 Championship 3pt / `sudden death: … (fought up
front, used only on a tie)`, each with task IDs. Hold on the
`Next: /kombat fight … --card card-…` line.
**Caption:** `Eight lanes. Three scored rounds. The same input always builds the same card.`

### 0:23–0:29 — Step 2: start the fight

**Screen:** copy the exact next command the scout printed (card id differs per
machine — `{CARD_ID}` below) and run it:

```
/kombat fight nvidia/nemotron-3.5-super-vl vs deepseek/deepseek-v4.1-flash-1017 --card {CARD_ID}
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

### 0:52–1:02 — Decisive result: sudden death

**Screen:** the arena goes `ROUND 3 — CHAMPIONSHIP` → points stay level → the
sudden-death answer is revealed on `coding-011` → `WINNER: DS-Flash`. Cut to the
scorecard report: the round table with per-task detail (`17/17 cases`,
`12/12 cases`, `A: no python code block/function found · B: 19/19 cases`) and the
usage table with `cost_source` (`host` / `estimated` / `n/a` per fighter).
**Caption:** `Round 3 tied at 3.0 — so it goes to sudden death. DS-Flash passes 19/19 cases; Nemo-VL writes no code at all.`

> The point is that a card can tie twice and still resolve honestly: warm-ups and
> main event split evenly, and the deciding evidence is executed code, not an
> opinion. If the recorded take ties all the way, use the draw + value beats
> below and skip this win caption — do not fabricate a winner.

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

### 1:10–1:15 — Honest-result beat: the draw screen and the value call

**Screen:** cut to the Desktop arena showing a real draw
(`docs/previews/arena_draw_edge.png` is the reference frame): `DRAW` banner,
equal points, and the line
`efficiency edge: Crouton — faster total latency (18.400s vs 21.500s) — efficiency edge (not a win)`.
Then cut to a `/kombat recommend` result whose `why` ends with
`Measured: equal quality — Nemo-Nano is 18.0× cheaper and 2.8× faster.`
Hold each about 2.5 s. No zoom, no stamp.
**Caption:** `Strong models tie. Then Kombat tells you what the tie costs: 18× cheaper, 2.8× faster, same answers.`
**Caption (small):** `Measured on its own runs — still not a quality win.`

### 1:13–1:20 — Close

**Screen:** repo URL over the arena pane.
**Caption:** `Model Kombat — same prompts, both models, scored by execution.`

---

## Fallback plan (rate limits)

Providers 429 mid-take. Do **not** re-record live if it happens; cut instead.

1. Before recording, run the fight once off-camera at the same `--hard`
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
| 2 | 0:05 | 0:13 | `/kombat scout card:t_9f21c4 --hard` |
| 3 | 0:13 | 0:23 | lanes, intensity, card JSON |
| 4 | 0:23 | 0:29 | `/kombat fight … --card {CARD_ID}` |
| 5 | 0:29 | 0:52 | arena pane + board, live |
| 6 | 0:52 | 1:02 | `WINNER: DS-Flash` after sudden death + scorecard |
| 7 | 1:02 | 1:10 | `/kombat ladder` + `recommend board`, ≈ vs clear edge |
| 8 | 1:10 | 1:15 | Desktop draw screen + measured value clause |
| 9 | 1:13 | 1:20 | close card |

Total: 80 s. If a shot overruns, cut shot 5 first (the only one that can be
compressed to a 5 s loop) and keep 6–8 intact. If the take lands at 75 s, still
inside spec.

## Pre-record checklist

- [ ] `llm.allow_model_override` on, both model IDs in `allowed_models`
- [ ] Gateway/Desktop **restarted** since the last `tools.py` edit (the plugin host keeps old modules in memory until restart)
- [ ] `show_on_kanban: true`, board `kombat-demo` created, card `t_9f21c4` on it
- [ ] A second card on the board whose ladder margin is under 15 Elo (for the `≈` badge shot)
- [ ] `max_fight_cost_usd` / `max_fight_tokens` high enough for a full hard card (~$0.02 for this pair)
- [ ] Desktop restarted after the last plugin edit (tool list + pane reload)
- [ ] One off-camera hard fight of the same card already scored (fallback footage — card-c2a527047b/fight 20261110-101512 qualifies)
- [ ] `{WINNER}` / `{LOSER}` filled from the actual take before export (verified pair expectation: DS-Flash wins on sudden death)
- [ ] Notifications off, terminal history clean, no other windows in frame
