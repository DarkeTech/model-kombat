# Demo script — 90 seconds (hackathon video)

Record your screen in Hermes Desktop/CLI. Keep it under 90s; show, don't narrate.

## 0:00–0:10 — The problem
On-screen text: *"Switching models = new session + hand-scoring. We did it by hand today."*
Show the manual mess briefly: two sessions side by side (optional cutaway).

## 0:10–0:25 — Choose your fighter
Type in chat:
> Run a fight between `stealth/crouton-alpha` and `deepseek/deepseek-v4.1-flash-1017`

Then: `/kombat tasks` → the 6 rounds flash by.

## 0:25–0:55 — The fight
`/kombat fight stealth/crouton-alpha vs deepseek/deepseek-v4.1-flash-1017`
(If live LLM calls are slow in the take, pre-run it and cut to the results —
say so on-screen: "fight pre-run, scoring is live".)

## 0:55–1:20 — Scoring (the payoff)
`/kombat score` → open the generated HTML versus scorecard:
- Round-by-round table with mechanical details ("25 words: 25 ✓", "executed: ALL CHECKS PASSED")
- A K.O. round highlighted (code passes vs fails)
- Final: winner + points

## 1:20–1:30 — Close
On-screen text: *"Model Kombat — same prompts, both fighters, scored by execution, not vibes."*
+ repo link + "Install: hermes plugins install …"

## Submission checklist (by 5PM EST Nov 11)
- [ ] PR open adding `plugin-catalog/model-kombat.yaml` to NousResearch/hermes-agent (pin full 40-char SHA, quote version)
- [ ] `hermes plugins validate . --install-deps` green at the pinned commit
- [ ] Demo video posted on X, tagging @NousResearch
- [ ] Both links submitted via the hackathon form
