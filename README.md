# 🥊 Model Kombat

**Choose your fighter.** Pit two Hermes models against each other on the same
task set and score the fight *mechanically* — code gets executed, word counts
get counted, facts get exact-matched. No model judges the other.

Built for the [Hermes Agent Plugin Hackathon](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins).

## Why

Switching models in Hermes today means opening a new session per model and
hand-scoring the results (we did exactly this — word counts by eye, code by
copy-paste). Model Kombat automates the whole loop inside one session:

```
kombat_run_fight  →  both fighters answer identical prompts (via ctx.llm, host credentials)
kombat_score_answers → deterministic scoring + HTML versus scorecard
```

## Install

```bash
hermes plugins install <owner>/model-kombat   # from the catalog, once listed
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
        # optional allowlist, e.g.:
        # allowed_models: ["stealth/crouton-alpha", "deepseek/deepseek-v4.1-flash-1017"]
```

Scoring pasted answer sets (`kombat_score_answers`) needs no LLM access at all.

## Usage

In any Hermes session:

```
/kombat tasks                          # list the rounds
/kombat fight A vs B                   # e.g. /kombat fight stealth/crouton-alpha vs deepseek/deepseek-v4.1-flash-1017
/kombat score                          # score the last fight + render the versus scorecard
```

Or just ask: *"Run a fight between stealth/crouton-alpha and
deepseek/deepseek-v4.1-flash-1017"* — the bundled `model-kombat` skill drives
the tools.

## The rounds (seed suite, `tasks.json`)

| Round | Task | Scorer |
|---|---|---|
| T1 | Multi-step money math | exact $ matches |
| T2 | Debug: second-largest distinct | heuristic checks |
| T3 | Long-context recall | exact phrase matches |
| T4 | Exact format (25 words / 3 sentences) | counts + banned words |
| T5 | Summary ≤50 words, preserve facts | word count + preserved tokens |
| T6 | Leap-year coding | **executed** against reference checks (opt-in) |

Code execution is **off by default** (`allow_code_execution` setting) —
model-written code only runs when the user explicitly turns it on, in a
subprocess with a timeout.

## Output

`kombat_score_answers` returns round winners, points, K.O. flags (one fighter
passed a hard check the other failed), and writes a standalone HTML versus
scorecard under the plugin data dir (`~/.hermes/plugin-data/model-kombat/reports/`).

## Develop / validate

```bash
python3 tests/test_scoring.py          # scorer unit tests (no Hermes needed)
hermes plugins doctor . --ci           # registration check
hermes plugins validate . --install-deps  # catalog check (must pass before PR)
```

## Layout

```
plugin.yaml          manifest (capabilities: llm.model_override)
__init__.py          register(): 3 tools + /kombat command + bundled skill
schemas.py           tool schemas (what the LLM sees)
tools.py             fight runner (ctx.llm), deterministic scorers, HTML report
tasks.json           seed suite
skills/model-kombat/ bundled skill (plugin:model-kombat)
tests/               scorer tests
```

## Roadmap

- `--tasks` custom suites loaded from a user file
- Desktop versus-screen pane (`desktop/plugin.js`)
- Latency/cost tracking per fighter (usage from `ctx.llm` results)
- GPU feasibility round ("Can I run it?" — VRAM vs model size), from our NCM/GTX-1060 session

## License

MIT. Name is a parody homage; no assets, logos, or audio from any game series.
