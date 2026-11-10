"""Per-lane Elo ladder and pick recommendations (SPEC §5).

Pure stdlib; no Hermes host needed. All writes are atomic (``tmp`` + ``os.replace``)
inside ``data_dir``; a missing or corrupt ``ladder.json`` is treated as an empty
ladder and the corrupt file is preserved as ``ladder.json.bak``.

Public API (frozen — engine-smith integrates against it):

- ``update_ladder(data_dir, record) -> dict``
- ``recommend(data_dir, lane, candidates=None) -> dict``
- ``write_recommendations(data_dir, mapping) -> None``
- ``short_name(model_id) -> str``
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["update_ladder", "recommend", "write_recommendations", "short_name", "value_edge"]

START_ELO = 1500.0
K_FACTOR = 24.0
DRAW_MARGIN = 0.25
APPLIED_CAP = 500
NO_DATA_WHY = "no fights recorded for this lane yet — run /kombat fight with a card that includes it"


# --------------------------------------------------------------------------- io


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty_ladder() -> dict:
    return {"updated": None, "lanes": {}, "applied": []}


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON to ``path`` atomically, never leaving a partial file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _backup_corrupt(path: Path) -> None:
    try:
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    except OSError:
        pass


def _load_ladder(data_dir: Path, repair: bool = False) -> dict:
    """Return a valid ladder object; never raise on missing/corrupt input."""
    path = Path(data_dir) / "ladder.json"
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("ladder root is not an object")
        lanes = data.get("lanes")
        if lanes is None:
            lanes = {}
        if not isinstance(lanes, dict):
            raise ValueError("ladder.lanes is not an object")
        applied_raw = data.get("applied")
        if applied_raw is None:
            applied_raw = []
        if not isinstance(applied_raw, list):
            raise ValueError("ladder.applied is not a list")
        return {
            "updated": data.get("updated"),
            "lanes": lanes,
            "applied": [str(x) for x in applied_raw],
        }
    except FileNotFoundError:
        return _empty_ladder()
    except (OSError, ValueError, TypeError):
        if repair:
            _backup_corrupt(path)
        return _empty_ladder()


def _num(value, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


# ------------------------------------------------------------------- ladder math


def _scored_score(side) -> float | None:
    """Score of a task side only when it was actually mechanically scored."""
    if not isinstance(side, dict):
        return None
    if str(side.get("status") or "").strip().lower() != "scored":
        return None
    score = side.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    return float(score)


def _model_slot(ladder: dict, lane: str, model_id: str) -> dict:
    lane_obj = ladder["lanes"].setdefault(lane, {"models": {}})
    if not isinstance(lane_obj, dict):
        lane_obj = {"models": {}}
        ladder["lanes"][lane] = lane_obj
    models = lane_obj.setdefault("models", {})
    if not isinstance(models, dict):
        models = {}
        lane_obj["models"] = models
    model = models.get(model_id)
    if not isinstance(model, dict):
        model = {}
        models[model_id] = model
    model.setdefault("elo", START_ELO)
    for key in ("wins", "losses", "draws", "fights", "points", "cost_usd", "latency_s", "scored_tasks"):
        model.setdefault(key, 0)
    return model


def update_ladder(data_dir: Path, record: dict) -> dict:
    """Apply one scored fight ``record`` to the lane ladder; idempotent per fight_id.

    Tasks are grouped by lane. A lane produces one bout when both fighters have at
    least one mechanically scored task there; bout score is each side's summed task
    score, a win needs a lead of >= 0.25 * (#scored tasks in that lane), otherwise
    draw. Elo: K=24, start 1500, standard expected score.
    """
    data_dir = Path(data_dir)
    try:
        ladder = _load_ladder(data_dir, repair=True)
    except Exception:
        ladder = _empty_ladder()

    try:
        if not isinstance(record, dict):
            return ladder
        fight_id = str(record.get("fight_id") or "").strip()
        applied = ladder["applied"]
        if fight_id and fight_id in applied:
            return ladder

        fighter_a = record.get("fighter_a")
        fighter_b = record.get("fighter_b")
        if not fighter_a or not fighter_b or fighter_a == fighter_b:
            return ladder

        tasks = record.get("tasks")
        if not isinstance(tasks, list):
            tasks = []
        by_lane: dict[str, list[dict]] = {}
        for task in tasks:
            if not isinstance(task, dict):
                continue
            lane = task.get("lane")
            if not lane:
                continue
            by_lane.setdefault(str(lane), []).append(task)

        usage = record.get("usage")
        if not isinstance(usage, dict):
            usage = {}

        for lane, lane_tasks in by_lane.items():
            sums = {"a": 0.0, "b": 0.0}
            counts = {"a": 0, "b": 0}
            scored_tasks = 0
            for task in lane_tasks:
                score_a = _scored_score(task.get("a"))
                score_b = _scored_score(task.get("b"))
                if score_a is not None:
                    sums["a"] += score_a
                    counts["a"] += 1
                if score_b is not None:
                    sums["b"] += score_b
                    counts["b"] += 1
                if score_a is not None or score_b is not None:
                    scored_tasks += 1
            if counts["a"] < 1 or counts["b"] < 1:
                continue

            total = sums["a"] + sums["b"]
            a_fraction = (sums["a"] / total) if total > 0 else 0.5
            lead = abs(sums["a"] - sums["b"])
            if lead >= DRAW_MARGIN * scored_tasks:
                score_a = 1.0 if sums["a"] > sums["b"] else 0.0
            else:
                score_a = 0.5
            score_b = 1.0 - score_a

            slot_a = _model_slot(ladder, lane, str(fighter_a))
            slot_b = _model_slot(ladder, lane, str(fighter_b))
            elo_a = _num(slot_a.get("elo"), START_ELO)
            elo_b = _num(slot_b.get("elo"), START_ELO)
            expected_a = 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))
            expected_b = 1.0 - expected_a
            slot_a["elo"] = round(elo_a + K_FACTOR * (score_a - expected_a), 6)
            slot_b["elo"] = round(elo_b + K_FACTOR * (score_b - expected_b), 6)

            for side, model_id, score, points_sum in (
                ("a", str(fighter_a), score_a, sums["a"]),
                ("b", str(fighter_b), score_b, sums["b"]),
            ):
                model = slot_a if side == "a" else slot_b
                if score >= 1.0:
                    model["wins"] = int(_num(model.get("wins"))) + 1
                elif score <= 0.0:
                    model["losses"] = int(_num(model.get("losses"))) + 1
                else:
                    model["draws"] = int(_num(model.get("draws"))) + 1
                model["fights"] = int(_num(model.get("fights"))) + 1
                model["points"] = round(_num(model.get("points")) + points_sum, 6)
                model["scored_tasks"] = int(_num(model.get("scored_tasks"))) + counts[side]
                side_usage = usage.get(model_id)
                if not isinstance(side_usage, dict):
                    side_usage = {}
                side_cost = side_usage.get("cost_usd")
                if side_cost is None or model.get("cost_usd") is None:
                    # Unknown price: keep the total unknown (never a silent 0),
                    # so cost-per-point renders as 'n/a'.
                    model["cost_usd"] = None
                else:
                    model["cost_usd"] = round(_num(model.get("cost_usd")) + _num(side_cost), 9)
                model["latency_s"] = round(
                    _num(model.get("latency_s")) + _num(side_usage.get("latency_s")), 6
                )

        if fight_id:
            applied.append(fight_id)
        ladder["applied"] = applied[-APPLIED_CAP:]
        ladder["updated"] = _now_iso()
        _atomic_write_json(data_dir / "ladder.json", ladder)
        return ladder
    except Exception:
        # Contract: callers get the ladder, never an exception.
        return ladder


# ----------------------------------------------------------------- recommendations


def short_name(model_id: str) -> str:
    """Compact arcade nameplate, e.g. ``deepseek/deepseek-v4.1-flash-1017`` -> ``DS-Flash``."""
    if not isinstance(model_id, str) or not model_id.strip():
        return "Model"
    model_id = model_id.strip()
    segments = [seg for seg in re.split(r"[/\\]+", model_id) if seg]
    last = segments[-1] if segments else model_id
    vendor = segments[0] if len(segments) > 1 else ""

    vendor_alias = _VENDOR_ALIASES.get(vendor.lower(), "")
    family_alias = _FAMILY_ALIASES.get(last.lower())

    tokens = [tok for tok in re.split(r"[-_.\s]+", last) if tok]
    kept: list[str] = []
    for token in tokens:
        low = token.lower()
        if low in _QUALIFIERS:
            continue
        if low == vendor.lower() or (vendor_alias and low == vendor_alias.lower()):
            continue
        if _is_version_token(low):
            continue
        kept.append(_FAMILY_ALIASES.get(low) or _title_token(token))

    if family_alias and not kept:
        kept = [family_alias]

    if not kept:
        # Nothing distinctive survived token pruning: title-case the raw segment.
        name = "-".join(_title_token(tok) for tok in tokens) or _title_token(last)
    else:
        parts: list[str] = []
        if vendor_alias and kept[0].lower() != vendor_alias.lower():
            parts.append(vendor_alias)
        if vendor_alias:
            parts.extend(kept[:2])
        else:
            parts.extend(kept)
        name = "-".join(part for part in parts if part).strip("-_")
    name = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")
    if len(name) > 14:
        name = name[:14].rstrip("-_")
    return name or "Model"


_VENDOR_ALIASES = {
    "deepseek": "DS",
    "nvidia": "Nemo",
    "openai": "GPT",
    "anthropic": "Claude",
    "google": "Gemini",
    "meta": "Llama",
    "meta-llama": "Llama",
    "mistralai": "Mistral",
    "mistral": "Mistral",
    "xai": "Grok",
    "qwen": "Qwen",
    "alibaba": "Qwen",
    "microsoft": "Phi",
    "cohere": "Cohere",
    "ai21": "Jamba",
    "stealth": "",
}

_FAMILY_ALIASES = {
    "nemotron": "Nemo",
}

_QUALIFIERS = {
    "alpha",
    "beta",
    "preview",
    "latest",
    "instruct",
    "chat",
    "it",
    "gguf",
    "fp8",
    "fp16",
    "awq",
    "int4",
    "int8",
    "thinking",
}


def _is_version_token(low: str) -> bool:
    return bool(
        re.fullmatch(r"v?\d+(?:\.\d+)*[a-z]?", low)
        or re.fullmatch(r"\d+(?:\.\d+)?[bmk]", low)
        or re.fullmatch(r"[a-z]\d+[a-z]*", low)
        or low.isdigit()
    )


def _title_token(token: str) -> str:
    if not token:
        return ""
    if token[0].isdigit():
        return token
    return token.capitalize()


def _num_or_none(value):
    """Numeric passthrough; anything else (bool/str/None) becomes None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


VALUE_MIN_RATIO = 1.05  # under 5% is noise, not a value edge


def value_edge(pick: dict, runner: dict) -> dict | None:
    """Measured efficiency edge between two ranked models (never a performance win).

    Compares ``cost_per_point`` and ``latency_per_task`` of the two rows and
    returns which model is cheaper/faster and by how much. Ratios are oriented
    so ``>= 1`` is an advantage for ``fighter``. Returns ``None`` when neither
    metric is measured on both sides, so unknown prices stay ``n/a`` rather than
    inventing a comparison.
    """
    if not isinstance(pick, dict) or not isinstance(runner, dict):
        return None
    pick_cost = _num_or_none(pick.get("cost_per_point"))
    run_cost = _num_or_none(runner.get("cost_per_point"))
    pick_lat = _num_or_none(pick.get("latency_per_task"))
    run_lat = _num_or_none(runner.get("latency_per_task"))
    cost_ratio = (run_cost / pick_cost) if (pick_cost and run_cost) else None
    lat_ratio = (run_lat / pick_lat) if (pick_lat and run_lat) else None
    if cost_ratio is None and lat_ratio is None:
        return None

    # Cheaper wins the value call; latency only decides when cost is unknown.
    if cost_ratio is not None:
        pick_wins = cost_ratio > 1.0
    elif lat_ratio is not None:
        pick_wins = lat_ratio > 1.0
    else:
        return None
    winner, loser = (pick, runner) if pick_wins else (runner, pick)
    win_cost = cost_ratio if pick_wins else (1.0 / cost_ratio if cost_ratio else None)
    win_lat = lat_ratio if pick_wins else (1.0 / lat_ratio if lat_ratio else None)
    win_cost = round(win_cost, 2) if win_cost is not None else None
    win_lat = round(win_lat, 2) if win_lat is not None else None

    parts = []
    if win_cost is not None and win_cost >= VALUE_MIN_RATIO:
        parts.append(f"{win_cost:.1f}× cheaper")
    if win_lat is not None and win_lat >= VALUE_MIN_RATIO:
        parts.append(f"{win_lat:.1f}× faster")
    if not parts:
        return None

    win_name = short_name(str(winner.get("model") or ""))

    out = {
        "fighter": winner.get("model"),
        "other": loser.get("model"),
        "short": win_name,
        "cost_ratio": win_cost,
        "latency_ratio": win_lat,
        "cheaper": bool(win_cost is not None and win_cost >= VALUE_MIN_RATIO),
        "faster": bool(win_lat is not None and win_lat >= VALUE_MIN_RATIO),
        "label": f"equal quality — {win_name} is " + " and ".join(parts),
        "note": "measured cost/latency edge at equal Elo — not a performance win",
    }
    return out


def _model_view(model_id: str, model: dict | None) -> dict:
    model = model if isinstance(model, dict) else {}
    points = _num(model.get("points"))
    cost_raw = model.get("cost_usd")
    cost = _num(cost_raw) if isinstance(cost_raw, (int, float)) and not isinstance(cost_raw, bool) else None
    elo = _num(model.get("elo"), START_ELO)
    latency = _num(model.get("latency_s"))
    scored_tasks = int(_num(model.get("scored_tasks")))
    fights = int(_num(model.get("fights")))
    if scored_tasks > 0:
        latency_per_task = round(latency / scored_tasks, 6)
    elif fights > 0:
        latency_per_task = round(latency / fights, 6)
    else:
        latency_per_task = None
    cpp = round(cost / points, 6) if (cost is not None and points > 0) else None
    return {
        "model": model_id,
        "elo": round(elo, 2),
        "fights": fights,
        "scored_tasks": scored_tasks,
        "latency_s": round(latency, 6),
        "latency_per_task": latency_per_task,
        "cost_usd": cost,
        "cost_per_point": cpp,
        "cost_per_point_display": f"{cpp:.4f}" if cpp is not None else "n/a",
    }


def recommend(data_dir: Path, lane: str, candidates: list[str] | None = None) -> dict:
    """Rank a lane's fighters and return the knockout pick (SPEC §5)."""
    data_dir = Path(data_dir)
    lane = str(lane or "")
    try:
        ladder = _load_ladder(data_dir, repair=False)
        lanes = ladder.get("lanes") if isinstance(ladder, dict) else {}
        lane_obj = lanes.get(lane) if isinstance(lanes, dict) else None
        models = lane_obj.get("models") if isinstance(lane_obj, dict) else None
        if not isinstance(models, dict):
            models = {}

        if candidates is None:
            pool = [str(mid) for mid in models.keys()]
        else:
            pool = [str(mid) for mid in candidates if str(mid)]

        if not pool:
            return {
                "ok": True,
                "pick": None,
                "lane": lane,
                "confidence": "none",
                "why": NO_DATA_WHY,
            }

        ranking = [_model_view(mid, models.get(mid)) for mid in pool]
        ranking.sort(
            key=lambda row: (
                -row["elo"],
                row["latency_per_task"] is None,
                row["latency_per_task"] if row["latency_per_task"] is not None else 0.0,
                row["cost_per_point"] is None,
                row["cost_per_point"] if row["cost_per_point"] is not None else 0.0,
                -row["fights"],
                row["model"],
            )
        )

        pick = ranking[0]
        runner_up = ranking[1] if len(ranking) > 1 else None
        margin = (pick["elo"] - runner_up["elo"]) if runner_up else 0.0

        fights = pick["fights"]
        confidence = "low" if fights < 3 else ("medium" if fights < 8 else "high")
        no_edge = runner_up is not None and margin < 15
        if no_edge:
            confidence = "low"

        pick_name = short_name(pick["model"])
        cpp = pick["cost_per_point"]
        cpp_txt = f"{cpp:.4f}" if cpp is not None else "n/a"
        if runner_up is not None and no_edge:
            why = (
                f"{pick_name} and {short_name(runner_up['model'])} are within "
                f"{margin:.0f} Elo in {lane} — this is an efficiency tiebreak "
                f"(latency/cost), not a performance win ({fights} fights at {cpp_txt} per point)."
            )
        elif runner_up is not None:
            why = (
                f"{pick_name} leads {lane} at {pick['elo']:.0f} Elo "
                f"(+{margin:.0f} over {short_name(runner_up['model'])}) across "
                f"{fights} fights at {cpp_txt} per point."
            )
        else:
            why = (
                f"{pick_name} leads {lane} at {pick['elo']:.0f} Elo with no "
                f"runner-up across {fights} fights at {cpp_txt} per point."
            )

        ve = value_edge(pick, runner_up) if (runner_up is not None and no_edge) else None
        if ve:
            why = f"{why} Measured: {ve['label']}."
        why = why if why.endswith(".") else why + "."

        return {
            "ok": True,
            "pick": pick["model"],
            "lane": lane,
            "elo": pick["elo"],
            "margin": round(margin, 2),
            "verdict": "no clear edge" if no_edge else "clear edge",
            "confidence": confidence,
            "fights": fights,
            "cost_per_point": cpp,
            "cost_per_point_display": f"{cpp:.4f}" if cpp is not None else "n/a",
            "runner_up": runner_up["model"] if runner_up else None,
            "why": why,
            "value_edge": ve,
            "ranking": ranking,
        }

    except Exception:
        return {
            "ok": True,
            "pick": None,
            "lane": lane,
            "confidence": "none",
            "why": NO_DATA_WHY,
        }


def write_recommendations(data_dir: Path, mapping: dict) -> None:
    """Merge ``mapping`` (kanban task id -> recommendation) into recommendations.json."""
    data_dir = Path(data_dir)
    path = data_dir / "recommendations.json"
    existing: dict = {}
    try:
        raw = path.read_text(encoding="utf-8")
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            existing = loaded
        else:
            _backup_corrupt(path)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        _backup_corrupt(path)

    now = _now_iso()
    if isinstance(mapping, dict):
        for key, value in mapping.items():
            if value is None:
                continue
            if isinstance(value, dict):
                item = dict(value)
                item["updated"] = now
            else:
                item = value
            existing[str(key)] = item
    _atomic_write_json(path, existing)
