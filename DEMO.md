# Demo script — 90 seconds (hackathon video)

Record your screen in Hermes Desktop/CLI. Keep it under 90s; show, don't narrate.

## 0:00–0:10 — The problem
On-screen text: *"Switching models = new session + hand-scoring. We did it by hand today."*
Show the manual mess briefly: two sessions side by side (optional cutaway).

## 0:10–0:25 — Choose your fighter
Type in chat:
> Run a fight between `stealth/crouton-alpha` and `qwen/qwen3.8-flash`

Then: `/kombat tasks` → 3 escalating rounds flash by (1pt → 2pt → 3pt + sudden death).

## 0:25–0:55 — The fight, live on the board
Split screen: chat left, **kanban `model-kombat` board** right.
`/kombat fight stealth/crouton-alpha vs qwen/qwen3.8-flash` → returns instantly
(background job); round cards move to fighting as answers land.
(If live LLM calls are slow in the take, pre-run it and cut to the results —
say so on-screen: "fight pre-run, scoring is live".)

## 0:55–1:20 — Scoring (the payoff)
`/kombat score` → open the generated HTML versus scorecard:
- Rounds grouped with rising stakes, mechanical details ("25 words: 25 ✓", "executed: ALL CHECKS PASSED")
- Usage table: latency / tokens / cost per fighter
- A K.O. or sudden-death moment highlighted
- Final: winner + points; kanban parent card completes with the report attached

## 1:20–1:30 — Close
On-screen text: *"Model Kombat — same prompts, both fighters, scored by execution, not vibes."*
+ repo link + "Install: hermes plugins install model-kombat"

## Submission checklist (by 5PM EST Nov 11)
- [ ] Public GitHub repo pushed; catalog draft (`catalog/model-kombat.yaml`) SHA filled with the pinned 40-char commit
- [ ] PR open adding `plugin-catalog/model-kombat.yaml` to NousResearch/hermes-agent (pin full 40-char SHA, quote version "0.2.0")
- [ ] `hermes plugins validate . --install-deps` green at the pinned commit
- [ ] Screenshots (scorecard, kanban board, slash command) uploaded to the repo, pinned URLs in the entry
- [ ] Demo video posted on X, tagging @NousResearch
- [ ] Both links submitted via the hackathon form
