# 🥊 Model Kombat

**Choose your fighter.** Pit two Hermes models against each other in three
escalating rounds — Warm-up, Main Event, Championship, plus sudden death —
and score the fight *mechanically*: code gets executed, word counts get
counted, facts get matched. No model judges the other.

Built for the [Hermes Agent Plugin Hackathon](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins).

## Why

Switching models in Hermes today means opening a new session per model and
hand-scoring the results (we did exactly this — word counts by eye, code by
copy-paste, and a fight *lost* on an exact-phrase scorer false-negative).
Model Kombat automates the whole loop inside one session:

```
kombat_run_fight   →  both fighters answer identical prompts (via ctx.llm, host credentials)
kombat_score_answers → weighted mechanical scoring + HTML versus scorecard
```

…while a **live kanban scoreboard** (`model-kombat` board, on by default)
shows the fight moving card by card.

## The fight format

| Round | Name | Tasks | Stakes |
|---|---|---|---|
| R1 | Warm-up | Money math, exact format | 1 pt/task |
| R2 | Main Event | Recall, summary fidelity, debug (executed) | 2 pts/task |
| R3 | Championship | Leap-year code, Sudoku validator (both executed) | 3 pts/task |
| SD | Sudden Death | Run-length encode (executed) | tie-break, winner takes the fight |

- **Championship K.O.:** pass every R3 task while your opponent fails one → win by K.O.
- Errored and skipped (code-exec-off) rounds are **excluded from points**, never scored as failures.
- Every call records **latency, tokens, and estimated cost**, with a spend budget cap (`max_fight_cost_usd`, default $2.00).

## Install

```bash
hermes plugins install model-kombat          # from the catalog, once listed
# or from a checkout:
git clone <this repo> ~/src/model-kombat
ln -s ~/src/model-kombat ~/.hermes/plugins/model-kombat
hermes plugins enable model-kombat
```

### One-time setup for fights

Fights call `ctx.llm.complete(model=...)`, which is gated by design (the plugin
never sees your credentials). Enable model overrides for this plugin in
`~/.hermes/config.yaml`:

```yaml
plugins:
  entries:
    model-kombat:
      llm:
        allow_model_override: true
        # recommended: bound spend to named models
        allowed_models: ["stealth/crouton-alpha", "deepseek/deepseek-v4.1-flash-1017"]
      granted_capabilities: ["llm.model_override"]
```

Scoring pasted answer sets (`kombat_score_answers`) needs no LLM access at all.

## Usage

In any Hermes session:

```
/kombat tasks                          # list the rounds + sudden death
/kombat fight A vs B                   # pure fight — background job, watch the board
/kombat agentic A vs B                 # agentic fight — kanban workers with model overrides
/kombat status                         # background fight status
/kombat score                          # score the last fight + versus scorecard
/kombat history                        # recent fights
/kombat export [json|md|csv]           # export the latest fight
```

Or just ask: *"Run a fight between stealth/crouton-alpha and
deepseek/deepseek-v4.1-flash-1017"* — the bundled `model-kombat` skill drives
the tools.

### Pure vs agentic

- **Pure (default):** both fighters answer via `ctx.llm` — fast, budgeted, benchmark-pure.
- **Agentic (explicit opt-in):** each task becomes a real kanban worker task with a
  per-task model override (`--model`), so full agent workers solve with tools and cards
  move live. Requires a running gateway dispatcher. Workers are billed per session and
  are **not** covered by the pure fight's budget cap.

## Settings (Settings → Plugins → Model Kombat)

`fighter_a/b` defaults · `temperature` · `max_tokens` · `max_fight_cost_usd` ·
`llm_timeout_s` · `agentic_timeout_s` · `allow_code_execution` ·
`show_on_kanban` (default **on**).

## How scoring works

Each task is scored mechanically: numeric tolerance for money, synonym-aware
phrase matching for recall (so "no one was injured" counts), whitespace word
counts for format/summary, and **subprocess execution** for code tasks
(leap-year, Sudoku validator, run-length encode, second-largest debug).
The debug task falls back to a labelled heuristic only when no executable code
is present. Round points go to the higher score fraction; draws split.

## Disclosure (please read before installing)

- **LLM spend:** pure fights call your configured provider through `ctx.llm`
  with the model you name; usage/cost is recorded per call and capped by
  `max_fight_cost_usd`. Agentic fights spawn worker sessions billed separately.
- **Code execution:** when `allow_code_execution` is on, model-written Python
  runs in a subprocess as your user with a 10s timeout and a stripped-down env.
  **This is not a security sandbox** — it is not safe against hostile code.
  Only enable it for models you trust. Off by default; those rounds are excluded.
- **Kanban writes:** with `show_on_kanban` on (default), the plugin creates/updates
  cards on a dedicated `model-kombat` board via the host's `terminal` tool
  (approval pipeline). Cards are unassigned display cards, never dispatched.
  Board updates are best-effort and never fail a fight.
- **File writes:** reports, history (`fights.jsonl`), and exports live under
  `~/.hermes/plugin-data/model-kombat/`. No telemetry, no other files read.

## Troubleshooting

- *"Host refused the model override"* → set the `llm.allow_model_override` config
  block above (and `granted_capabilities`), then retry. Nothing was sent.
- *`kombat_run_fight` says no host context* → you're calling it outside a live
  Hermes session with the plugin enabled; tools load next session after enable.
- *Edited the plugin source but behavior is stale* → the gateway's plugin host
  caches loaded code. Run `hermes plugins disable model-kombat` then
  `hermes plugins enable model-kombat` to reload it, then start a fresh session
  (no gateway restart needed).
- *Agentic tasks stuck `ready`* → the gateway dispatcher isn't running
  (`hermes gateway start`). Pure fights don't need it.

## Develop / validate

```bash
python3 tests/test_scoring.py              # scorer + mock-host tests (no Hermes needed)
hermes plugins doctor . --ci               # registration check
hermes plugins validate . --install-deps   # catalog check (must pass before PR)
```

## Layout

```
plugin.yaml          manifest (capabilities: llm.model_override)
__init__.py          register(): 6 tools + /kombat command (background jobs) + bundled skill
schemas.py           tool schemas (what the LLM sees)
tools.py             pure fight runner (ctx.llm), deterministic scorers, HTML report, history/export
kanban.py            kanban scoreboard + agentic worker fights
tasks.json           seed suite (3 rounds + sudden death)
skills/model-kombat/ bundled skill (plugin:model-kombat)
tests/               scorer + mock-host tests
catalog/model-kombat.yaml  draft catalog entry (for the submission PR)
```

## Roadmap

- Desktop versus-screen pane (`desktop/plugin.js`)
- Judge-assisted rounds for open-ended tasks (labelled non-deterministic)
- Custom task-suite files; model discovery suggestions from fight history
- GPU feasibility round ("Can I run it?" — VRAM vs model size)

## License

MIT (see `LICENSE`). Name is a parody homage; no assets, logos, or audio from any game series.
