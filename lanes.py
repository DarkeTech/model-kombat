"""Adaptive lane classifier + deterministic fight-card builder (SPEC §4).

No LLM calls here: lanes come from keyword/regex weights, cards from a seeded
PRNG over the task pool. Identical (lanes, seed) always produce an identical
card; different seeds may reshuffle task selection.

Task pool sources, in load order (later sources override the same task id):
  1. ``<plugin>/tasks/*.json``        new pool: {"lane": ..., "tasks": [...]}
  2. ``<plugin>/tasks.json``          legacy T1–T8 suite
  3. ``<cwd>/.kombat/tasks.json``     optional project tasks (same schema)

Legacy T1–T8 tasks are mapped to lanes unless they carry their own ``lane``
field: T1 reasoning, T4 instruction, T3 recall, T5 summarization,
T2 debugging, T6/T7/T8 coding. Difficulty comes from the legacy round
(R1=1, R2=2, R3=3) or an explicit ``difficulty`` field.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from collections import defaultdict
from pathlib import Path

#: Card intensities. ``standard`` keeps the historic R1 d1 / R2 d2 / R3 d3 mix;
#: ``hard`` drops difficulty 1 entirely; ``gauntlet`` is all difficulty 3.
INTENSITIES = ("standard", "hard", "gauntlet")

LANES = (
    "coding",
    "debugging",
    "extraction",
    "summarization",
    "reasoning",
    "instruction",
    "recall",
    "sql",
)

# Legacy suite task -> lane (SPEC task-forger note).
LEGACY_LANES = {
    "T1": "reasoning",
    "T2": "debugging",
    "T3": "recall",
    "T4": "instruction",
    "T5": "summarization",
    "T6": "coding",
    "T7": "coding",
    "T8": "coding",
}

# Keyword weights per lane. Longer/more specific phrases carry more weight.
_KEYWORDS: dict[str, dict[str, float]] = {
    "coding": {
        "python": 1.5, "javascript": 1.5, "typescript": 1.5, "function": 1.2,
        "implement": 2.2, "program": 1.5, "code": 1.2, "class": 0.8,
        "algorithm": 2.2, "unit test": 2.2, "script": 1.2, "api": 0.8,
        "data structure": 2.0, "refactor": 2.0, "library": 1.0,
    },
    "debugging": {
        "bug": 4.2, "debug": 4.0, "traceback": 4.0, "stack trace": 4.0,
        "fix": 2.8, "broken": 2.4, "error": 1.5, "exception": 1.5,
        "regression": 2.4, "why does": 1.5, "fails": 1.6, "failing": 1.6,
        "incorrect": 1.8, "crash": 2.2, "corrected": 2.2,
    },
    "extraction": {
        "extract": 3.0, "parse": 2.5, "json": 1.4, "pull out": 2.2,
        "fields": 1.5, "entities": 2.2, "invoice": 1.5, "scrape": 2.2,
        "key-value": 2.0, "structured data": 2.0, "schema": 1.2,
        "named entity": 2.2,
    },
    "summarization": {
        "summarize": 5.0, "summary": 4.5, "tl;dr": 4.0, "condense": 3.0,
        "key points": 2.4, "brief": 1.2, "shorten": 2.4, "recap": 2.2,
        "in a nutshell": 2.4, "main idea": 2.4,
    },
    "reasoning": {
        "calculate": 2.6, "how much": 1.6, "how many": 1.6, "reason": 2.0,
        "plan": 1.6, "decide": 1.5, "estimate": 2.0, "logic": 2.0,
        "prove": 2.0, "infer": 2.0, "solve": 1.6, "math": 2.0,
        "trade-off": 2.0, "work out": 1.6, "therefore": 1.2, "puzzle": 2.0,
        "sum": 2.6, "difference": 2.0, "average": 2.2, "percent": 2.0,
        "percentage": 2.2, "total": 1.8, "multiply": 2.2, "divide": 2.0,
        "subtract": 2.5, "profit": 2.2, "revenue": 2.0, "tax": 1.8,
        "interest": 2.0, "compound": 2.2, "invest": 2.0,
    },
    "instruction": {
        "exactly": 2.0, "constraint": 3.0, "constraints": 3.0, "must": 1.0,
        "do not": 2.0, "respond with": 2.6, "return only": 2.6, "format": 1.6,
        "start with": 1.6, "end with": 1.6, "first word": 2.2,
        "keep it": 1.2, "no more than": 2.0, "at most": 1.6,
    },
    "recall": {
        "passage": 3.0, "according to the text": 3.0, "according to the passage": 3.0,
        "from the text": 2.8, "read the": 1.4, "recall": 2.5, "remember": 1.5,
        "verbatim": 2.0, "quote": 1.6, "who": 0.8, "when did": 1.2,
        "based on the passage": 3.0, "in the passage": 2.2,
    },
    "sql": {
        "sql": 3.2, "select": 2.4, "join": 2.2, "group by": 2.6,
        "order by": 2.0, "sqlite": 3.0, "postgres": 2.6, "postgresql": 2.6,
        "mysql": 2.6, "database": 1.6, "query": 1.4, "table": 0.8,
        "cte": 2.2, "window function": 2.4, "aggregate": 1.6,
    },
}

# Regex-level signals (weighted like keywords; patterns are case-insensitive).
_PATTERNS: dict[str, list[tuple[str, float]]] = {
    "coding": [
        (r"```(?:python|py|js|javascript|ts)", 3.0),
        (r"\bdef\s+\w+\s*\(", 2.5),
        (r"\b(?:write|implement|create)\s+(?:a\s+)?(?:python\s+)?function\b", 3.0),
        (r"\bfunction\s+\w+\s*\(", 2.0),
    ],
    "debugging": [
        (r"\b(?:fix|debug)\b[^.\n]{0,40}\b(?:code|bug|error|traceback|function)\b", 3.0),
        (r"\btraceback \(most recent call last\)", 3.0),
        (r"\bfind (?:the )?bugs?\b", 4.5),
        (r"\bwhat(?:'s| is) wrong with this\b", 4.0),
        (r"\bwhy (?:does|is) (?:this|it) (?:fail|break|crash)\b", 3.0),
    ],
    "extraction": [
        (r"\bextract\b[^.\n]{0,40}\b(?:json|fields?|data|entities)\b", 3.0),
        (r"\breturn (?:only )?(?:a )?json\b", 2.4),
    ],
    "summarization": [
        (r"\bsummari[sz]e\b", 3.2),
        (r"\bin (?:at most )?\d+\s+words\b", 1.8),
        (r"\bshort(?:er)? summary\b", 2.4),
    ],
    "reasoning": [
        (r"\bstep[- ]by[- ]step\b", 2.4),
        (r"\bexplain (?:your )?reasoning\b", 2.2),
        (r"\bwhat is the (?:total|cost|change|result|sum|product|average)\b", 2.4),
        (r"\bwhat(?:'s| is) the sum of\b", 3.0),
        (r"\d+(?:\.\d+)?\s*%", 1.5),
    ],
    "instruction": [
        (r"\bexactly\s+\d+\b", 1.8),
        (r"\b\d+\s+(?:words|sentences)\b", 1.4),
        (r"\b(?:do not|don't) use\b", 2.2),
        (r"\brespond (?:only )?with\b", 2.2),
    ],
    "recall": [
        (r"\baccording to (?:the )?(?:text|passage|article|document)\b", 3.2),
        (r"\banswer (?:only )?from the (?:text|passage)\b", 3.0),
        (r"\bread the (?:sentence|passage|text|paragraph)\b", 3.0),
        (r"\bbased on the (?:sentence|passage|text)\b", 3.0),
    ],
    "sql": [
        (r"\bselect\b[\s\S]{0,200}?\bfrom\b", 3.0),
        (r"\bwith\s+\w+\s+as\s*\(", 2.2),
        (r"\b(?:inner|left|right|full)?\s*join\b", 2.0),
    ],
}

_FALLBACK = [{"lane": "reasoning", "weight": 1.0}, {"lane": "instruction", "weight": 0.5}]

_MAX_HITS_PER_KEYWORD = 3


def _kw_regex(keyword: str) -> re.Pattern:
    parts = [re.escape(p) for p in keyword.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", re.I)


def classify(text: str) -> list[dict]:
    """Keyword/regex lane classifier (no LLM). Returns top 3 weighted lanes."""
    raw = str(text or "")
    if not raw.strip():
        return [dict(item) for item in _FALLBACK]

    scores: dict[str, float] = {lane: 0.0 for lane in LANES}
    for lane, keywords in _KEYWORDS.items():
        for keyword, weight in keywords.items():
            hits = len(_kw_regex(keyword).findall(raw))
            if hits:
                scores[lane] += weight * min(hits, _MAX_HITS_PER_KEYWORD)
    for lane, patterns in _PATTERNS.items():
        for pattern, weight in patterns:
            try:
                hits = len(re.findall(pattern, raw, re.I))
            except re.error:
                continue
            if hits:
                scores[lane] += weight * min(hits, _MAX_HITS_PER_KEYWORD)

    ranked = [
        {"lane": lane, "weight": round(weight, 2)}
        for lane, weight in scores.items()
        if weight > 0
    ]
    ranked.sort(key=lambda item: (-item["weight"], item["lane"]))
    if not ranked:
        return [dict(item) for item in _FALLBACK]
    return ranked[:3]


# ---------------------------------------------------------------- task pool


def _coerce_difficulty(value, default: int = 2) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return min(3, max(1, num))


def _clean_task(raw, file_lane, source: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    tid = raw.get("id")
    if not isinstance(tid, str) or not tid.strip():
        return None
    lane = raw.get("lane") or file_lane
    if lane is not None and lane not in LANES:
        lane = None
    task = dict(raw)
    task["id"] = tid.strip()
    task["lane"] = lane
    task["difficulty"] = _coerce_difficulty(raw.get("difficulty"), 2)
    task.setdefault("title", task["id"])
    task["_source"] = source
    return task


def _read_pool_file(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    tasks = []
    if isinstance(data, dict) and isinstance(data.get("tasks"), list):
        file_lane = data.get("lane")
        for raw in data["tasks"]:
            task = _clean_task(raw, file_lane, str(path))
            if task:
                tasks.append(task)
    elif isinstance(data, list):
        for raw in data:
            task = _clean_task(raw, None, str(path))
            if task:
                tasks.append(task)
    return tasks


def _legacy_rounds(data: dict) -> dict[str, int]:
    difficulty = {}
    for rnd in data.get("rounds") or []:
        if not isinstance(rnd, dict):
            continue
        points = rnd.get("points", 2)
        try:
            level = min(3, max(1, int(points)))
        except (TypeError, ValueError):
            level = 2
        for tid in rnd.get("tasks") or []:
            difficulty[str(tid).upper()] = level
    sd = data.get("sudden_death_task")
    if isinstance(sd, str):
        difficulty[sd.upper()] = 3
    return difficulty


def _read_legacy_file(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        return []
    by_round = _legacy_rounds(data)
    tasks = []
    for raw in data["tasks"]:
        task = _clean_task(raw, None, str(path))
        if not task:
            continue
        tid = task["id"]
        if not task.get("lane"):
            task["lane"] = LEGACY_LANES.get(tid.upper())
        if raw.get("difficulty") is None:
            task["difficulty"] = by_round.get(tid.upper(), 2)
        tasks.append(task)
    return tasks


def load_task_pool(
    tasks_dir: str | Path | None = None,
    legacy_path: str | Path | None = None,
    project_path: str | Path | None = None,
) -> dict[str, dict]:
    """Load tasks/*.json + legacy tasks.json + <cwd>/.kombat/tasks.json.

    Returns ``{task_id: task}`` with lane/difficulty normalised. Bad files are
    skipped, never raised; missing files are simply absent.
    """
    here = Path(__file__).resolve().parent
    tasks_dir = Path(tasks_dir) if tasks_dir is not None else here / "tasks"
    legacy_path = Path(legacy_path) if legacy_path is not None else here / "tasks.json"
    project_path = Path(project_path) if project_path is not None else Path.cwd() / ".kombat" / "tasks.json"

    pool: dict[str, dict] = {}
    if tasks_dir.is_dir():
        for path in sorted(tasks_dir.glob("*.json")):
            for task in _read_pool_file(path):
                pool[task["id"]] = task
    if legacy_path.is_file():
        for task in _read_legacy_file(legacy_path):
            pool.setdefault(task["id"], task)  # new pool wins on id collisions
    if project_path.is_file():
        for task in _read_pool_file(project_path):
            pool[task["id"]] = task
    return pool


# ---------------------------------------------------------------- fight card


def _normalise_lanes(lanes) -> list[dict]:
    merged: dict[str, float] = {}
    if isinstance(lanes, dict):
        lanes = [{"lane": k, "weight": v} for k, v in lanes.items()]
    for item in lanes or []:
        if isinstance(item, str):
            lane, weight = item, 1.0
        elif isinstance(item, dict):
            lane, weight = item.get("lane"), item.get("weight", 1.0)
        else:
            continue
        if lane not in LANES:
            continue
        try:
            weight = float(weight)
        except (TypeError, ValueError):
            weight = 1.0
        merged[lane] = max(merged.get(lane, 0.0), weight)
    out = [{"lane": lane, "weight": round(weight, 2)} for lane, weight in merged.items() if weight > 0]
    out.sort(key=lambda item: (-item["weight"], item["lane"]))
    return out or [dict(item) for item in _FALLBACK]


def _card_id(lane_names: list[str], seed, intensity: str = "standard") -> str:
    blob = json.dumps(
        {"lanes": lane_names, "seed": str(seed), "intensity": intensity}, sort_keys=True
    ).encode("utf-8")
    return "card-" + hashlib.sha256(blob).hexdigest()[:10]


# ------------------------------------------------------- task discrimination


def stats_path() -> Path | None:
    """Where ``task_stats.json`` lives (plugin data dir), or ``None`` offline."""
    try:
        try:
            from . import tools as _tools
        except ImportError:
            import tools as _tools  # type: ignore

        return Path(_tools._data_dir()) / "task_stats.json"
    except Exception:  # noqa: BLE001 — stats are optional, never fatal
        home = os.environ.get("HERMES_HOME")
        if home:
            return Path(home) / "plugin-data" / "model-kombat" / "task_stats.json"
        return Path.home() / ".hermes" / "plugin-data" / "model-kombat" / "task_stats.json"


def load_task_stats(path: Path | None = None) -> dict[str, dict]:
    """Load per-task discrimination stats; ``{}`` when absent/corrupt.

    Shape: ``{"tasks": {task_id: {"fights", "both_perfect", "both_zero",
    "split", "mean_gap", "per_model"}}}``. A bare ``{task_id: {...}}`` file is
    also accepted (tolerant read).
    """
    path = Path(path) if path is not None else stats_path()
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    tasks = data.get("tasks")
    if isinstance(tasks, dict):
        return {str(k): v for k, v in tasks.items() if isinstance(v, dict)}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _int_stat(stats: dict, tid: str, key: str) -> int:
    entry = stats.get(tid)
    if not isinstance(entry, dict):
        return 0
    try:
        return int(entry.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _float_stat(stats: dict, tid: str, key: str) -> float:
    entry = stats.get(tid)
    if not isinstance(entry, dict):
        return 0.0
    try:
        return float(entry.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def task_preference(tid: str, stats: dict[str, dict]) -> tuple:
    """Sort key for the in-bucket preference (lower = picked first).

    (a) never fought, (b) discriminating (most split then widest mean gap),
    (c) lowest both-perfect ratio; saturated tasks (fights>=3, never split)
    are demoted behind everything else.
    """
    entry = stats.get(tid)
    fights = _int_stat(stats, tid, "fights")
    if not isinstance(entry, dict) or fights <= 0:
        return (0, 0, 0.0, 0.0)
    both_perfect = _int_stat(stats, tid, "both_perfect")
    if fights >= 3 and both_perfect >= fights:
        return (3, 0, 0.0, 0.0)
    split = _int_stat(stats, tid, "split")
    mean_gap = _float_stat(stats, tid, "mean_gap")
    if split > 0 or mean_gap > 0:
        return (1, -split, -round(mean_gap, 9), 0.0)
    return (2, round(both_perfect / fights, 9), 0.0, 0.0)


def discrimination_level(card: dict, stats: dict[str, dict] | None = None) -> str:
    """Share of the card's tasks whose prior fights split — low/medium/high/unknown."""
    if stats is None:
        stats = load_task_stats()
    ids = [
        str(tid)
        for rnd in (card.get("rounds") or [])
        for tid in (rnd.get("tasks") or [])
    ]
    if not ids:
        return "unknown"
    known = [tid for tid in ids if isinstance(stats.get(tid), dict)]
    if not known:
        return "unknown"
    share = sum(1 for tid in ids if _int_stat(stats, tid, "split") > 0) / len(ids)
    if share >= 0.5:
        return "high"
    if share >= 0.25:
        return "medium"
    return "low"


class _Picker:
    """Seeded, no-repeat task picker with lane/difficulty buckets.

    When discrimination ``stats`` are present, each bucket is filtered to its
    best preference tier (never fought > discriminating > least-saturated) and
    the seed only breaks ties *within* that tier — so the card stays
    deterministic for a given stats snapshot.
    """

    def __init__(self, tasks: dict[str, dict], rng: random.Random, stats: dict[str, dict] | None = None):
        self.buckets: dict[tuple, list[str]] = defaultdict(list)
        for tid in sorted(tasks):
            task = tasks[tid]
            lane = task.get("lane")
            if lane not in LANES:
                continue
            self.buckets[(lane, task.get("difficulty", 2))].append(tid)
        self.rng = rng
        self.used: set[str] = set()
        self.stats = stats or {}

    def _available(self, lane: str, difficulty: int) -> list[str]:
        ids = [tid for tid in self.buckets.get((lane, difficulty), []) if tid not in self.used]
        if not ids or not self.stats:
            return ids
        tiers: dict[tuple, list[str]] = defaultdict(list)
        for tid in ids:
            tiers[task_preference(tid, self.stats)].append(tid)
        return tiers[min(tiers)]

    def take(self, count: int, difficulties: list[int], lanes: list[str]) -> list[str]:
        picks: list[str] = []
        for difficulty in difficulties:
            progress = True
            while len(picks) < count and progress:
                progress = False
                for lane in lanes:
                    if len(picks) >= count:
                        break
                    available = self._available(lane, difficulty)
                    if not available:
                        continue
                    chosen = self.rng.choice(available)
                    picks.append(chosen)
                    self.used.add(chosen)
                    progress = True
        return picks

    def take_extra(self, count: int, lanes: list[str], extras: dict[str, dict]) -> list[str]:
        picks: list[str] = []
        for lane in lanes:
            if len(picks) >= count:
                break
            candidates = sorted(
                tid for tid, task in extras.items()
                if task.get("lane") == lane and tid not in self.used
            )
            # Main Event wants difficulty-2 project tasks; fall back to any.
            preferred = [tid for tid in candidates if extras[tid].get("difficulty") == 2]
            candidates = preferred or candidates
            if candidates:
                chosen = self.rng.choice(candidates)
                picks.append(chosen)
                self.used.add(chosen)
        return picks

    def available_tasks(self, lanes: list[str], difficulties: list[int]) -> list[str]:
        """Unused tasks in lane order, first non-empty difficulty wins."""
        for difficulty in difficulties:
            out: list[str] = []
            for lane in lanes:
                for tid in self.buckets.get((lane, difficulty), []):
                    if tid not in self.used and tid not in out:
                        out.append(tid)
            if out:
                return out
        return []

    def any_unused(self, lanes: list[str]) -> str | None:
        for lane in lanes:
            for difficulty in (3, 2, 1):
                available = self._available(lane, difficulty)
                if available:
                    chosen = self.rng.choice(available)
                    self.used.add(chosen)
                    return chosen
        return None

    def any_task(self, lanes: list[str]) -> str | None:
        """Last-resort pick (may repeat) so sudden death always gets a task."""
        candidates = [
            tid for lane in lanes
            for difficulty in (3, 2, 1)
            for tid in self.buckets.get((lane, difficulty), [])
        ]
        return self.rng.choice(candidates) if candidates else None


def _pick_sudden_death(
    picker: _Picker,
    lanes: list[str],
    stats: dict[str, dict],
    difficulties: tuple[int, ...] = (3,),
) -> str | None:
    """Highest-discrimination task (stats, else seeded random) not on the card."""
    candidates = picker.available_tasks(lanes, list(difficulties))
    if not candidates:
        return None
    scored = [tid for tid in candidates if isinstance(stats.get(tid), dict)]
    if scored:
        def rank(tid: str) -> tuple:
            return (
                _int_stat(stats, tid, "split"),
                round(_float_stat(stats, tid, "mean_gap"), 9),
            )

        best = max(rank(tid) for tid in scored)
        if best > (0, 0.0):
            candidates = [tid for tid in scored if rank(tid) == best]
    chosen = picker.rng.choice(candidates)
    picker.used.add(chosen)
    return chosen


def _coerce_intensity(value) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in INTENSITIES else "standard"


def build_card(
    lanes,
    seed,
    extra_tasks=None,
    pool: dict[str, dict] | None = None,
    intensity: str = "standard",
    stats: dict[str, dict] | None = None,
) -> dict:
    """Build a deterministic fight card from classified lanes (SPEC §4.2).

    ``lanes`` is the classifier output (``[{"lane","weight"}]``) or a list of
    lane names. ``seed`` fixes every random choice. ``extra_tasks`` are project
    tasks mixed into their lane's Main Event round. ``pool`` overrides the
    loaded task pool (tests / callers with their own source). ``intensity`` is
    ``standard`` (historic d1/d2/d3 mix), ``hard`` (d2/d3 only) or ``gauntlet``
    (all difficulty 3). ``stats`` is the task-discrimination snapshot; when
    omitted it is read from ``task_stats.json`` in the plugin data dir.

    Determinism: identical (lanes, seed, intensity, stats snapshot) always
    yields an identical card (``stats_used`` reports whether stats were read).
    """
    intensity = _coerce_intensity(intensity)
    lane_items = _normalise_lanes(lanes)
    lane_names = [item["lane"] for item in lane_items]

    tasks = dict(pool) if pool is not None else load_task_pool()
    if stats is None:
        stats = load_task_stats()
    stats_used = bool(stats)
    extras: dict[str, dict] = {}
    for task in extra_tasks or []:
        if not isinstance(task, dict):
            continue
        tid = task.get("id")
        if not isinstance(tid, str) or not tid.strip():
            continue
        lane = task.get("lane")
        if lane not in LANES:
            continue
        extras[tid] = {
            **task,
            "difficulty": _coerce_difficulty(task.get("difficulty"), 2),
        }

    # If the shipped pool is not on disk yet (fresh checkout / concurrent work),
    # project tasks are still enough to build a card.
    if not tasks and extras:
        tasks = dict(extras)
    # Make sure every extra task id the card can reference resolves in the pool.
    for tid, task in extras.items():
        tasks.setdefault(tid, task)

    rng = random.Random(f"kombat:{seed}")
    picker = _Picker(tasks, rng, stats)
    top = lane_names[:3]
    top2 = lane_names[:2]

    if intensity == "gauntlet":
        # Every task difficulty 3: R1 2, R2 3, R3 3.
        r1 = picker.take(2, [3], top)
        if len(r1) < 2:
            r1 += picker.take(2 - len(r1), [3, 2, 1], lane_names)
        r2 = picker.take(3, [3], top2)
        if len(r2) < 3:
            r2 += picker.take(3 - len(r2), [3, 2, 1], lane_names)
        r3 = picker.take(3, [3], top)
        if len(r3) < 3:
            r3 += picker.take(3 - len(r3), [3, 2, 1], lane_names)
    elif intensity == "hard":
        # No difficulty 1: R1 d2, R2 d3, R3 d3.
        r1 = picker.take(2, [2], top)
        if len(r1) < 2:
            r1 += picker.take(2 - len(r1), [2, 3], top2)
        if len(r1) < 2:
            r1 += picker.take(2 - len(r1), [2, 3], lane_names)

        enough_d3 = sum(1 for lane in top2 if picker._available(lane, 3))
        target = 3 if len(top2) >= 2 and enough_d3 >= 2 else 2
        r2 = picker.take(target, [3], top2)
        if len(r2) < target:
            r2 += picker.take(target - len(r2), [3, 2], lane_names)

        r3 = picker.take(2, [3], top)
        if len(r3) < 2:
            r3 += picker.take(2 - len(r3), [3, 2], lane_names)
        if not r3:
            r3 = picker.take(1, [3, 2], lane_names)
    else:
        # R1 Warm-up (1pt): 2 difficulty-1 tasks from the top lanes.
        r1 = picker.take(2, [1], top)
        if len(r1) < 2:
            r1 += picker.take(2 - len(r1), [2], top)
        if len(r1) < 2:
            r1 += picker.take(2 - len(r1), [1, 2, 3], lane_names)

        # R2 Main Event (2pt): 2–3 difficulty-2 tasks spread over the top 2 lanes,
        # with project tasks mixed in at their lane.
        enough_d2 = sum(1 for lane in top2 if picker._available(lane, 2))
        target = 3 if len(top2) >= 2 and enough_d2 >= 2 else 2
        r2 = picker.take_extra(target, top2, extras)
        if len(r2) < target:
            r2 += picker.take(target - len(r2), [2], top2)
        if len(r2) < 2:
            r2 += picker.take(2 - len(r2), [2, 1, 3], lane_names)

        # R3 Championship (3pt): 1–2 difficulty-3 tasks from the top lane, weighted
        # toward coding/sql when they are on the card.
        preferred = [lane for lane in top if lane in ("coding", "sql")] + [
            lane for lane in lane_names if lane not in ("coding", "sql")
        ]
        available_d3 = sum(len(picker._available(lane, 3)) for lane in preferred)
        r3_count = 2 if available_d3 >= 2 else 1
        r3 = picker.take(r3_count, [3], preferred)
        if not r3:
            r3 = picker.take(r3_count, [2, 3], preferred)

    # Sudden death: highest-discrimination difficulty-3 task not already on the
    # card; ties (or no stats) are broken by the seed.
    all_lanes = sorted({lane for lane, _ in picker.buckets})
    sd_lanes = list(dict.fromkeys(top[:1] + top + lane_names))
    sd_difficulties = (3,) if intensity in ("hard", "gauntlet") else (3, 2)
    sudden = _pick_sudden_death(picker, sd_lanes, stats, sd_difficulties)
    if not sudden:
        sudden = _pick_sudden_death(picker, all_lanes, stats, sd_difficulties)
    if not sudden:
        fallback = (picker.any_unused(lane_names) or picker.any_task(lane_names)
                    or picker.any_unused(all_lanes) or picker.any_task(all_lanes))
        sudden = fallback

    # Partial pool (e.g. a lane's file has not landed yet): fill any short round
    # from other lanes rather than shipping an empty round. Preferences above
    # still win whenever the requested lane has tasks.
    if intensity == "standard":
        for tasks_out, difficulty, wanted in ((r1, 1, 2), (r2, 2, 2), (r3, 3, 1)):
            if len(tasks_out) < wanted:
                tasks_out += picker.take(wanted - len(tasks_out), [difficulty, 2, 1, 3], all_lanes)

    rounds = [
        {"id": "R1", "name": "Warm-up", "points": 1, "tasks": r1},
        {"id": "R2", "name": "Main Event", "points": 2, "tasks": r2},
        {"id": "R3", "name": "Championship", "points": 3, "tasks": r3},
    ]

    return {
        "id": _card_id(lane_names, seed, intensity),
        "title": "Kombat card — " + " · ".join(lane_names),
        "lanes": lane_names,
        "lane_weights": lane_items,
        "seed": seed,
        "intensity": intensity,
        "stats_used": stats_used,
        "rounds": rounds,
        "sudden_death_task": sudden if sudden else None,
    }

