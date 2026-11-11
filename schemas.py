"""Tool schemas for Model Kombat — what the LLM reads to decide when to call us."""

KOMBAT_LIST_TASKS = {
    "name": "kombat_list_tasks",
    "description": (
        "List the Model Kombat fight suite: 3 escalating rounds (Warm-up 1pt, Main Event 2pt, "
        "Championship 3pt) plus a sudden-death task, each with its deterministic scorer type. "
        "Call this first to see what a fight covers."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

KOMBAT_SCOUT = {
    "name": "kombat_scout",
    "description": (
        "Adaptive scout: classify a task's text into Model Kombat lanes and build a "
        "deterministic fight card (Warm-up 1pt, Main Event 2pt, Championship 3pt, sudden "
        "death) from those lanes. Pass free text, or {\"card\": \"<kanban task id>\"} to "
        "scout a kanban card, or {\"board\": true} for all open cards on the active board. "
        "Classification is local keyword/regex rules — no LLM call. Use before "
        "kombat_run_fight to tailor the fight to real work; identical input + seed + "
        "intensity + task-stats snapshot always yields the identical card. `intensity` "
        "picks the difficulty mix: standard (d1/d2/d3), hard (d2/d3, no d1) or gauntlet "
        "(every task d3). Returns the card plus `expected_discrimination` "
        "(low/medium/high/unknown) from prior per-task split history."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": "Free text describing the task (prompt, card title + body, etc.).",
            },
            "card": {
                "type": "string",
                "description": "Kanban task id to scout (reads it with `hermes kanban show --json`).",
            },
            "board": {
                "type": "boolean",
                "description": "Scout every open card on the active kanban board.",
            },
            "seed": {
                "type": "integer",
                "description": "Optional card seed. Default derives from the input text, so the same input gives the same card.",
            },
            "intensity": {
                "type": "string",
                "enum": ["standard", "hard", "gauntlet"],
                "description": "Difficulty mix: standard = d1/d2/d3, hard = d2/d3 (no d1), gauntlet = every task d3. Default from the default_intensity setting (standard).",
            },
            "persist": {
                "type": "boolean",
                "description": "Store the built card for later use by kombat_run_fight. Default true; pass false for a read-only scout with no cards.json write.",
            },
        },
        "required": [],
    },
}

KOMBAT_RUN_FIGHT = {
    "name": "kombat_run_fight",
    "description": (
        "Run a pure fight: send every task to BOTH fighters (two model IDs) via the host "
        "LLM facade and return both sets of answers plus per-call latency/tokens/cost. "
        "Pass `card` (the object returned by kombat_scout, {\"lanes\":[...],\"seed\":n}, or a "
        "stored card id) to fight that adaptive card; omit it to fight the legacy fixed "
        "suite. Rate limits (429) are waited out within a per-fight budget and errored "
        "(task, fighter) pairs are retried a bounded number of times; exhausted pairs are "
        "reported as errored (never failed). Every LLM call has a HARD deadline "
        "(timeout_s, default llm_timeout_s=180) enforced in a worker thread, so a hung "
        "streamed response is abandoned and counted as an errored attempt instead of "
        "blocking the fight. A whole-fight wall-clock cap (max_fight_seconds, default 360) "
        "marks the remaining pairs skipped ('fight time cap') and returns a partial result "
        "with time_cap_hit=true; scoring then excludes those pairs (round marked *). "
        "Requires llm.model_override enabled for this plugin. Sudden death is fetched up "
        "front and only scored on a points tie. `intensity` (standard/hard/gauntlet) "
        "applies when `card` is a {\"lanes\": ...} spec: hard drops difficulty 1, gauntlet "
        "fights only difficulty 3. Enforces a USD and token spend budget, prices tokens "
        "when the host gives no cost (unknown models are 'n/a', never $0), and drives the "
        "kanban scoreboard and live state. For LONG jobs (cards with more than ~6 tasks, "
        "or when a single answer can take minutes) use `background: true`: it starts the "
        "fight as a background job and returns {job_id, status:'started'} immediately; "
        "poll kombat_history {\"running\": true} and score with kombat_score_answers "
        "{\"last\": true}. The /kombat fight slash command always runs in the background. "
        "Prefer a `tasks` or `card` subset to keep a single tool call under the host's "
        "~420s tool timeout."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "fighter_a": {"type": "string", "description": "First model ID, e.g. z-ai/glm-5.3-flash"},
            "fighter_b": {"type": "string", "description": "Second model ID, e.g. deepseek/deepseek-v4.1-flash-1017"},
            "background": {
                "type": "boolean",
                "description": "Run the fight as a background job (ctx.spawn_task, like /kombat fight) and return {job_id, status:'started'} at once. Use for cards with more than 6 tasks. Poll kombat_history {\"running\": true}.",
            },
            "card": {
                "type": "object",
                "description": "Fight card from kombat_scout (object), {\"lanes\":[...],\"seed\":n,\"intensity\":\"hard\"}, or {\"id\":\"card-...\"}. A stored card id string is also accepted. Omit for the legacy fixed suite.",
            },
            "intensity": {
                "type": "string",
                "enum": ["standard", "hard", "gauntlet"],
                "description": "Difficulty mix used when `card` is a lanes spec: standard (d1/d2/d3), hard (d2/d3, no d1), gauntlet (every task d3). Default from the default_intensity setting (standard). Ignored for stored/full cards.",
            },
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional subset of task IDs. Default: every task in the card (or the whole legacy suite).",
            },
            "temperature": {"type": "number", "description": "Sampling temperature for both fighters. Default 0 for fairness."},
            "max_tokens": {"type": "integer", "description": "Max completion tokens per answer. Default 2000."},
            "budget_usd": {"type": "number", "description": "Stop the fight once cumulative estimated cost exceeds this. Default 2.0."},
            "budget_tokens": {"type": "integer", "description": "Stop the fight once cumulative input+output tokens exceed this (works even when prices are unknown). Default 400000."},
            "timeout_s": {"type": "number", "description": "Hard per-call deadline in seconds (worker thread abandoned at the deadline). Default from settings (llm_timeout_s, 180)."},
            "max_fight_seconds": {"type": "number", "description": "Whole-fight wall-clock cap in seconds (default from settings, 360). Remaining pairs are marked skipped with 'fight time cap' and the fight returns time_cap_hit: true."},
            "max_rate_wait_s": {"type": "number", "description": "Longest single 429 wait in seconds (default from settings, 120)."},
            "rate_wait_budget_s": {"type": "number", "description": "Total 429 wait budget for the whole fight in seconds. Default 240."},
            "auto_rematch": {"type": "boolean", "description": "Retry errored (task, fighter) pairs inside this fight. Default from settings (true)."},
            "pace_ms": {"type": "number", "description": "Polite pause between calls in milliseconds. Default from settings (0)."},
        },
        "required": ["fighter_a", "fighter_b"],
    },
}

KOMBAT_RUN_AGENTIC_FIGHT = {
    "name": "kombat_run_agentic_fight",
    "description": (
        "Run an AGENTIC fight: each task is dispatched as a real kanban worker task with a "
        "per-task model override, so full Hermes agent workers solve it with tools and the "
        "cards move on the model-kombat board as they work. Slower and costlier than "
        "kombat_run_fight (worker sessions billed separately, NOT budget-capped; per-task "
        "max-runtime applies). Use only when the user explicitly asks for an agentic / "
        "kanban-worker fight. Returns answers in the same shape as kombat_run_fight so "
        "kombat_score_answers applies unchanged. Requires a running gateway dispatcher."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "fighter_a": {"type": "string", "description": "First model ID, e.g. z-ai/glm-5.3-flash"},
            "fighter_b": {"type": "string", "description": "Second model ID, e.g. deepseek/deepseek-v4.1-flash-1017"},
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional subset of task IDs. Default: all tasks.",
            },
            "timeout_s": {"type": "integer", "description": "Per-task worker max-runtime in seconds. Default 600."},
        },
        "required": ["fighter_a", "fighter_b"],
    },
}

KOMBAT_SCORE_ANSWERS = {
    "name": "kombat_score_answers",
    "description": (
        "Score a fight deterministically and render the versus scorecard. Scores each task "
        "mechanically (numeric matches, synonym-aware phrase matches, word/sentence counts, "
        "preserved-token checks, and — only with allow_code_execution=true — executing "
        "model-written code against reference checks). Points are weighted by round "
        "(Warm-up 1, Main Event 2, Championship 3); ties after the Championship go to "
        "sudden death; passing every Championship task while the opponent fails one is a "
        "K.O. Errored/skipped tasks are excluded from points. A still-tied fight returns "
        "winner 'draw' plus an `edge` object ({fighter, metric, reason, label}) that is an "
        "efficiency edge only — never a win. Returns winner, points, KOs, "
        "and the HTML versus scorecard path, and appends the fight to history. "
        "Pass {\"last\": true} to score the stored last_fight without re-sending the "
        "answer maps (the usual path after a background fight). Pairs skipped by the "
        "fight time cap / budget stay excluded and their round is marked with an "
        "asterisk. "
        "Use after a run tool, or whenever the user pastes two answer sets to compare."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "fighter_a": {"type": "string", "description": "Name/ID of fighter A (label for the scorecard)."},
            "fighter_b": {"type": "string", "description": "Name/ID of fighter B (label for the scorecard)."},
            "answers_a": {
                "type": "object",
                "description": "Map of task ID -> answer text for fighter A, e.g. {\"T1\": \"...\", \"T6\": \"...\"}.",
            },
            "answers_b": {
                "type": "object",
                "description": "Map of task ID -> answer text for fighter B, e.g. {\"T1\": \"...\", \"T6\": \"...\"}.",
            },
            "errors_a": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Task IDs that errored for fighter A (excluded from points, not scored as failed).",
            },
            "errors_b": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Task IDs that errored for fighter B (excluded from points, not scored as failed).",
            },
            "card": {
                "type": "object",
                "description": "Fight card to score (object, {\"lanes\":...} spec, or {\"id\":\"card-...\"}). Defaults to the card stored with the last fight, else the legacy suite.",
            },
            "usage": {
                "type": "object",
                "description": "Optional per-fighter usage totals from the run tool ({fighter: {latency_s, tokens, cost_usd}}), shown on the scorecard.",
            },
            "allow_code_execution": {
                "type": "boolean",
                "description": "Execute code-task answers in a subprocess (timeout, not a security sandbox). Default from settings (false).",
            },
            "render_report": {"type": "boolean", "description": "Write the HTML versus scorecard. Default true."},
            "last": {
                "type": "boolean",
                "description": "Score the stored last_fight from this session (answers, card, usage and errored pairs included) — no answer maps needed. Use after a background fight finishes.",
            },
        },
        "required": [],
    },
}

KOMBAT_REMATCH_ERRORS = {
    "name": "kombat_rematch_errors",
    "description": (
        "Rematch the last fight's errored tasks: re-run only the (task, fighter) pairs that "
        "errored in the previous kombat_run_fight (typically 429 rate limits), merge the new "
        "answers into the stored fight, and shrink the error list to whatever still fails. "
        "Use when a fight's scoring excluded rounds due to errors — rematch first, then "
        "kombat_score_answers scores the merged fight. Same trust/budget rules as the run tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "temperature": {"type": "number", "description": "Sampling temperature for rematch calls. Default from settings (0)."},
            "max_tokens": {"type": "integer", "description": "Max completion tokens per answer. Default from settings."},
            "budget_usd": {"type": "number", "description": "Stop rematching once cumulative cost exceeds this. Default from settings."},
            "timeout_s": {"type": "number", "description": "Per-call timeout in seconds. Default from settings."},
        },
        "required": [],
    },
}

KOMBAT_RECOMMEND = {
    "name": "kombat_recommend",
    "description": (
        "Ladder-backed recommendation. Pass `card` as a stored scout card id "
        "('card-...'), a scouted card object, or a kanban task id: a scout card "
        "returns a pick PER lane plus one overall pick (highest summed weighted Elo "
        "margin = sum over lanes of lane_weight * elo), while a kanban task id is "
        "classified into its top lane first. Pass a lane, or input text. "
        "{\"board\": true} scouts every open card on the active board and writes "
        "recommendations.json keyed by kanban task id. {\"ladder\": true} returns "
        "the raw per-lane Elo ladder. cost_per_point is 'n/a' when a model has no "
        "known price."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "lane": {
                "type": "string",
                "enum": ["coding", "debugging", "extraction", "summarization", "reasoning",
                         "instruction", "recall", "sql"],
                "description": "Lane to recommend for.",
            },
            "input": {
                "type": "string",
                "description": "Free text to classify into a lane when no lane is given.",
            },
            "card": {
                "anyOf": [{"type": "string"}, {"type": "object"}],
                "description": "Stored scout card id ('card-...'), a scouted card object, or a kanban task id (resolved in that order).",
            },
            "board": {
                "type": "boolean",
                "description": "Recommend for every open card on the active board and write recommendations.json.",
            },
            "ladder": {
                "type": "boolean",
                "description": "Return the raw ladder.json instead of a recommendation.",
            },
            "candidates": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional candidate model IDs to rank instead of every model in the lane.",
            },
        },
        "required": [],
    },
}

KOMBAT_HISTORY = {
    "name": "kombat_history",
    "description": (
        "List recent completed Model Kombat fights for this profile (winner, points, "
        "report path). Use when the user asks about past fights or wants to compare "
        "models across multiple bouts. Pass {\"running\": true} to poll a background "
        "fight: returns the live job state (running, job_id, started/finished, error, "
        "result summary) plus the current live.json. Pass {\"trends\": true} for the "
        "per-lane Elo history (trend over time per model, with a chartable series)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "How many recent fights to return. Default 10."},
            "running": {
                "type": "boolean",
                "description": "Return the live background job state + last live.json instead of fight history.",
            },
            "trends": {
                "type": "boolean",
                "description": "Return per-lane Elo history (trend per model) instead of fight history.",
            },
            "lane": {
                "type": "string",
                "description": "With trends=true: restrict to one lane (e.g. 'coding').",
            },
            "model": {
                "type": "string",
                "description": "With trends=true: restrict to one model ID.",
            },
            "series_limit": {
                "type": "integer",
                "description": "With trends=true: Elo points per trend row (oldest first). Default 50; 0 omits series.",
            },
        },
        "required": [],
    },
}

KOMBAT_EXPORT = {
    "name": "kombat_export",
    "description": (
        "Export one past fight (by history index) as JSON, Markdown, or CSV to the "
        "plugin data dir. Use when the user wants to share, archive, or spreadsheet a result."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "format": {"type": "string", "description": "Export format: json, md, or csv. Default json."},
            "index": {"type": "integer", "description": "History index (0-based). Required when more than one fight exists."},
        },
        "required": [],
    },
}
