"""Tool schemas for Model Kombat — what the LLM reads to decide when to call us."""

KOMBAT_LIST_TASKS = {
    "name": "kombat_list_tasks",
    "description": (
        "List the Model Kombat seed fight suite: the rounds (tasks) a fight is fought over, "
        "each with its deterministic scorer type. Call this first to see what a fight covers."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

KOMBAT_RUN_FIGHT = {
    "name": "kombat_run_fight",
    "description": (
        "Run a fight: send every task in the suite (or a subset) to BOTH fighters "
        "(two model IDs) via the host LLM facade and return both sets of answers. "
        "Use when the user wants two models compared on identical prompts. "
        "Requires the user to have enabled llm.model_override for this plugin "
        "(config: plugins.entries.model-kombat.llm.allow_model_override: true); "
        "if that is missing this returns an error explaining exactly what to set."
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
        },
        "required": ["fighter_a", "fighter_b"],
    },
}

KOMBAT_SCORE_ANSWERS = {
    "name": "kombat_score_answers",
    "description": (
        "Score a fight deterministically and render the versus scorecard. Takes both fighters' "
        "answers (keyed by task ID) and scores each round mechanically: exact money/recall "
        "matches, word+sentence counts, banned-word checks, preserved-token checks, and — only "
        "with allow_code_execution=true — actually executing model-written code against reference "
        "checks. Returns round winners, points, KOs, and the path to a standalone HTML versus "
        "scorecard. Use after kombat_run_fight, or whenever the user pastes two answer sets to compare."
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
            "allow_code_execution": {
                "type": "boolean",
                "description": "Execute code-task answers in a subprocess sandbox. Default false (code rounds score as skipped).",
            },
            "render_report": {"type": "boolean", "description": "Write the HTML versus scorecard. Default true."},
        },
        "required": ["fighter_a", "fighter_b", "answers_a", "answers_b"],
    },
}
