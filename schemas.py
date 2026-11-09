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

KOMBAT_RUN_FIGHT = {
    "name": "kombat_run_fight",
    "description": (
        "Run a pure fight: send every task in the suite (or a subset) to BOTH fighters "
        "(two model IDs) via the host LLM facade and return both sets of answers plus "
        "per-call latency/tokens/cost. Use when the user wants two models compared on "
        "identical prompts. Requires llm.model_override enabled for this plugin "
        "(config: plugins.entries.model-kombat.llm.allow_model_override: true); "
        "if that is missing this returns an error explaining exactly what to set. "
        "Enforces a spend budget (budget_usd, default from settings) and retries "
        "transient errors once. Also drives the kanban scoreboard when enabled."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "fighter_a": {"type": "string", "description": "First model ID, e.g. stealth/crouton-alpha"},
            "fighter_b": {"type": "string", "description": "Second model ID, e.g. deepseek/deepseek-v4.1-flash-1017"},
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional subset of task IDs (e.g. [\"T1\",\"T6\"]). Default: all tasks.",
            },
            "temperature": {"type": "number", "description": "Sampling temperature for both fighters. Default 0 for fairness."},
            "max_tokens": {"type": "integer", "description": "Max completion tokens per answer. Default 2000."},
            "budget_usd": {"type": "number", "description": "Stop the fight once cumulative estimated cost exceeds this. Default 2.0."},
            "timeout_s": {"type": "number", "description": "Per-call timeout in seconds. Default 180."},
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
            "fighter_a": {"type": "string", "description": "First model ID, e.g. stealth/crouton-alpha"},
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
        "K.O. Errored/skipped tasks are excluded from points. Returns winner, points, KOs, "
        "and the HTML versus scorecard path, and appends the fight to history. "
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
            "usage": {
                "type": "object",
                "description": "Optional per-fighter usage totals from the run tool ({fighter: {latency_s, tokens, cost_usd}}), shown on the scorecard.",
            },
            "allow_code_execution": {
                "type": "boolean",
                "description": "Execute code-task answers in a subprocess (timeout, not a security sandbox). Default from settings (false).",
            },
            "render_report": {"type": "boolean", "description": "Write the HTML versus scorecard. Default true."},
        },
        "required": ["fighter_a", "fighter_b", "answers_a", "answers_b"],
    },
}

KOMBAT_HISTORY = {
    "name": "kombat_history",
    "description": (
        "List recent completed Model Kombat fights for this profile (winner, points, "
        "report path). Use when the user asks about past fights or wants to compare "
        "models across multiple bouts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "How many recent fights to return. Default 10."},
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
