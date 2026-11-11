"""Model Kombat dashboard backend (artist-owned; SPEC §7).

Read-only routes over the plugin data dir for the Desktop pane/page. Every
route returns JSON and never raises: missing or corrupt files yield empty
default objects. Data dir honours ``MODEL_KOMBAT_DATA_DIR`` (tests), then
``HERMES_HOME`` (``$HERMES_HOME/plugin-data/model-kombat``), falling back to
``~/.hermes/plugin-data/model-kombat`` (same as ``tools._data_dir``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Query

router = APIRouter()


def _data_dir() -> Path:
    override = os.environ.get("MODEL_KOMBAT_DATA_DIR")
    if override:
        return Path(override)
    home = os.environ.get("HERMES_HOME")
    if home:
        return Path(home) / "plugin-data" / "model-kombat"
    return Path.home() / ".hermes" / "plugin-data" / "model-kombat"


def _read_json(name: str):
    """Return parsed JSON from ``name``, or None (missing/corrupt/non-dict)."""
    try:
        path = _data_dir() / name
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return None


def _ladder_module():
    """The plugin's ladder module, importable from any host layout.

    The plugin host normally puts the plugin root on ``sys.path`` (plain
    ``import ladder``); tests import this file as ``dashboard.plugin_api``
    with the repo root on ``sys.path``. The explicit ``sys.path`` fallback
    covers any loader that does neither.
    """
    try:
        import ladder as _ladder  # plugin root already importable
        return _ladder
    except ImportError:
        pass
    import sys

    plugin_root = str(Path(__file__).resolve().parent.parent)
    if plugin_root not in sys.path:
        sys.path.insert(0, plugin_root)
    import ladder as _ladder
    return _ladder


def _idle_live() -> dict:
    return {
        "status": "idle", "fight_id": None, "card": {"title": None, "lanes": []},
        "fighters": {"a": {"id": None, "short": None}, "b": {"id": None, "short": None}},
        "hp": {"a": 100, "b": 100}, "rounds": [], "sudden_death": None,
        "winner": None, "ko": False, "points": {"a": 0, "b": 0},
        "usage": {"a": {"cost_usd": 0, "tokens": 0, "latency_s": 0},
                  "b": {"cost_usd": 0, "tokens": 0, "latency_s": 0}},
        "report_path": None, "updated": None,
    }


@router.get("/live")
def live():
    try:
        data = _read_json("live.json")
        if isinstance(data, dict) and data:
            return data
        return _idle_live()
    except Exception:
        return _idle_live()


@router.get("/ladder")
def ladder():
    try:
        data = _read_json("ladder.json")
        if isinstance(data, dict) and data:
            return data
        return {"updated": None, "lanes": {}}
    except Exception:
        return {"updated": None, "lanes": {}}


@router.get("/trends")
def trends(lane: str | None = None, model: str | None = None, series_limit: int = 40):
    """Per-model Elo trends (roadmap: ladder trends over time).

    Wraps ``ladder.ladder_trends``: one row per (lane, model) with a summary
    (bouts, elo, delta, direction, ...) plus the most recent ``series_limit``
    Elo points for the pane sparkline. Missing/corrupt ladder yields an empty
    trend list — never raises.
    """
    try:
        limit = max(0, min(400, int(series_limit)))
    except (TypeError, ValueError):
        limit = 40
    try:
        data = _ladder_module().ladder_trends(
            _data_dir(), lane=lane, model=model, series_limit=limit
        )
        if isinstance(data, dict) and data.get("ok"):
            return data
        return {"ok": True, "count": 0, "trends": []}
    except Exception:
        return {"ok": True, "count": 0, "trends": []}


@router.get("/history")
def history(limit: int = 20):
    try:
        limit = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        limit = 20
    try:
        path = _data_dir() / "fights.jsonl"
        if not path.is_file():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                records.append(rec)
        return records[-limit:]
    except Exception:
        return []


@router.get("/recommendations")
def recommendations():
    try:
        data = _read_json("recommendations.json")
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


@router.get("/cards")
def cards():
    """List stored fight cards: [{id, title, lanes}] from cards.json.

    cards.json is normally ``{card_id: card}`` (tools._store_card); a bare
    list of cards is also accepted. Missing/corrupt/non-card data yields [].
    Never raises.
    """
    try:
        data = _read_json("cards.json")
        entries = []
        if isinstance(data, dict):
            entries = [(key, value) for key, value in data.items()]
        elif isinstance(data, list):
            entries = [(None, value) for value in data]
        out = []
        for key, card in entries:
            if not isinstance(card, dict):
                continue
            card_id = card.get("id") or key
            if not card_id:
                continue
            lanes = card.get("lanes")
            if not isinstance(lanes, list):
                weights = card.get("lane_weights")
                lanes = (
                    [w.get("lane") for w in weights if isinstance(w, dict) and w.get("lane")]
                    if isinstance(weights, list)
                    else []
                )
            out.append(
                {
                    "id": str(card_id),
                    "title": str(card.get("title") or ""),
                    "lanes": [str(lane) for lane in lanes if lane],
                }
            )
        return out
    except Exception:
        return []


@router.get("/health")
def health():
    try:
        return {
            "ok": True,
            "plugin": "model-kombat",
            "data_dir": str(_data_dir()),
            "live_exists": (_data_dir() / "live.json").is_file(),
        }
    except Exception:
        return {"ok": False, "plugin": "model-kombat", "data_dir": None, "live_exists": False}
