"""Live fight state for the Model Kombat arena (SPEC §6).

``LiveState`` is the engine's single writer of ``live.json``: every mutation
recomputes earned points + HP, writes the file atomically (``tmp`` +
``os.replace``) inside ``data_dir``, and best-effort broadcasts
``plugin.model-kombat.fight.updated``. Nothing in this module raises: a missing
event bus, an unwritable data dir or a malformed card all degrade quietly.

Pure stdlib, no Hermes host required.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

try:  # package import (plugins / plugin loader)
    from .ladder import short_name
except Exception:  # pragma: no cover - direct script / top-level import
    try:
        from ladder import short_name
    except Exception:
        def short_name(model_id: str) -> str:  # type: ignore
            text = str(model_id or "").rsplit("/", 1)[-1] or "Model"
            return text.title()[:14]

__all__ = ["LiveState", "read_live"]

STATES = {"waiting", "fighting", "scoring", "done", "error", "skipped"}
TERMINAL = {"done", "error", "skipped"}
STATE_ALIASES = {"scored": "done", "ok": "done", "errored": "error", "failed": "error", "skip": "skipped"}
EVENT_PLUGIN = "model-kombat"
EVENT_NAME = "fight.updated"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_json(path: Path, payload: dict) -> None:
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


def _num(value, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _idle_live() -> dict:
    return {
        "status": "idle",
        "fight_id": None,
        "card": {"title": None, "lanes": []},
        "fighters": {"a": {"id": None, "short": None}, "b": {"id": None, "short": None}},
        "hp": {"a": 100, "b": 100},
        "rounds": [],
        "sudden_death": None,
        "winner": None,
        "ko": False,
        "edge": None,
        "points": {"a": 0, "b": 0},
        "usage": {
            "a": {"cost_usd": 0, "tokens": 0, "latency_s": 0},
            "b": {"cost_usd": 0, "tokens": 0, "latency_s": 0},
        },
        "report_path": None,
        "updated": None,
    }


def read_live(data_dir: Path) -> dict:
    """Read ``live.json``; missing/corrupt files yield the default idle object."""
    path = Path(data_dir) / "live.json"
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("live root is not an object")
        return data
    except (OSError, ValueError, TypeError):
        return _idle_live()


def _guard(method):
    """Public methods must never raise (SPEC §9)."""

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:
            return None

    return wrapper


class LiveState:
    """Mutable arena state; writes ``live.json`` after every mutation.

    ``card`` is the SPEC §4 fight card object::

        {"title": ..., "lanes": [...],
         "rounds": [{"id","name","points","tasks":[task_ids]}],
         "sudden_death_task": task_id | None}

    ``task_meta`` optionally maps ``task_id -> {"lane","title"}`` so tasks show
    human titles/lanes even though the card only carries ids.
    """

    def __init__(
        self,
        data_dir: Path,
        card: dict | None = None,
        fighter_a=None,
        fighter_b=None,
        fight_id: str | None = None,
        task_meta: dict | None = None,
        broadcast: bool = True,
    ):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "live.json"
        self.broadcast_enabled = bool(broadcast)
        self._task_meta = task_meta if isinstance(task_meta, dict) else {}

        self.fight_id = (
            str(fight_id).strip()
            if fight_id
            else datetime.now().strftime("%Y%m%d-%H%M%S")
        )
        self.status = "idle"
        self.card = {"title": None, "lanes": []}
        self.fighters = {
            "a": self._fighter(fighter_a),
            "b": self._fighter(fighter_b),
        }
        self.hp = {"a": 100, "b": 100}
        self.rounds: list[dict] = []
        self.sudden_death: dict | None = None
        self.winner = None
        self.ko = False
        self.edge = None
        self.points = {"a": 0.0, "b": 0.0}
        self.usage = {
            "a": {"cost_usd": 0.0, "tokens": 0, "latency_s": 0.0},
            "b": {"cost_usd": 0.0, "tokens": 0, "latency_s": 0.0},
        }
        self.report_path = None
        self.updated = _now_iso()

        try:
            self._build(card)
        except Exception:
            pass
        self._touch()

    # ------------------------------------------------------------- construction

    @staticmethod
    def _fighter(value) -> dict:
        if isinstance(value, dict):
            model_id = value.get("id") or value.get("model") or value.get("name")
            if not model_id:
                return {"id": None, "short": None}
            model_id = str(model_id)
            short = value.get("short") or short_name(model_id)
            return {"id": model_id, "short": str(short)}
        if value:
            model_id = str(value)
            return {"id": model_id, "short": short_name(model_id)}
        return {"id": None, "short": None}

    @staticmethod
    def _slot() -> dict:
        return {
            "state": "waiting",
            "score": None,
            "latency_s": None,
            "cost_usd": 0.0,
            "detail": "",
        }

    def _entry(self, task) -> dict:
        if isinstance(task, dict):
            task_id = str(task.get("id") or task.get("task_id") or "")
            meta = task
        else:
            task_id = str(task or "")
            meta = self._task_meta.get(task_id) or {}
        if not isinstance(meta, dict):
            meta = {}
        return {
            "id": task_id,
            "lane": str(meta.get("lane") or ""),
            "title": str(meta.get("title") or task_id),
            "a": self._slot(),
            "b": self._slot(),
        }

    def _build(self, card) -> None:
        if not isinstance(card, dict):
            return
        title = card.get("title")
        lanes = card.get("lanes")
        if not isinstance(lanes, list):
            lanes = []
        lanes = [str(lane) for lane in lanes if lane]
        if not lanes:
            seen: list[str] = []
            for rnd in card.get("rounds") or []:
                if not isinstance(rnd, dict):
                    continue
                for task in rnd.get("tasks") or []:
                    meta = (
                        task
                        if isinstance(task, dict)
                        else self._task_meta.get(str(task)) or {}
                    )
                    lane = meta.get("lane") if isinstance(meta, dict) else None
                    if lane and str(lane) not in seen:
                        seen.append(str(lane))
            lanes = seen
        self.card = {"title": title if isinstance(title, str) else title, "lanes": lanes}

        rounds: list[dict] = []
        raw_rounds = card.get("rounds")
        if isinstance(raw_rounds, list):
            for index, raw in enumerate(raw_rounds):
                if not isinstance(raw, dict):
                    continue
                try:
                    points = _num(raw.get("points"), 1.0)
                except Exception:
                    points = 1.0
                ids = raw.get("tasks")
                if not isinstance(ids, list):
                    ids = []
                rounds.append(
                    {
                        "id": str(raw.get("id") or f"R{index + 1}"),
                        "name": str(raw.get("name") or ""),
                        "points": points if points > 0 else 1.0,
                        "state": "waiting",
                        "tasks": [
                            entry
                            for entry in (self._entry(task) for task in ids if task)
                            if entry.get("id")
                        ],
                    }
                )
        self.rounds = rounds

        sd = card.get("sudden_death_task")
        if isinstance(sd, (list, tuple)):
            sd = sd[0] if sd else None
        if sd:
            self.sudden_death = {
                "id": "SD",
                "name": "Sudden Death",
                "points": 1.0,
                "state": "waiting",
                "tasks": [self._entry(sd)],
            }

    # --------------------------------------------------------------- internals

    def _iter_groups(self):
        for rnd in self.rounds:
            yield rnd
        if self.sudden_death:
            yield self.sudden_death

    def _find(self, round_id, task_id) -> tuple[dict | None, dict | None]:
        task_id = str(task_id or "")
        wanted_round = str(round_id) if round_id else None
        for group in self._iter_groups():
            if wanted_round and str(group.get("id")) != wanted_round:
                continue
            for task in group.get("tasks") or []:
                if str(task.get("id")) == task_id:
                    return group, task
        return None, None

    @staticmethod
    def _refresh_round(group: dict) -> None:
        tasks = group.get("tasks") or []
        if not tasks:
            group["state"] = "waiting"
            return
        states = [str(task.get(side, {}).get("state") or "waiting") for task in tasks for side in ("a", "b")]
        if all(state in TERMINAL for state in states):
            group["state"] = "done"
        elif any(state != "waiting" for state in states):
            group["state"] = "fighting"
        else:
            group["state"] = "waiting"

    def _recompute(self) -> None:
        earned = {"a": 0.0, "b": 0.0}
        total = 0.0
        for group in self._iter_groups():
            weight = _num(group.get("points"), 1.0)
            for task in group.get("tasks") or []:
                total += weight
                for side in ("a", "b"):
                    slot = task.get(side) or {}
                    score = slot.get("score")
                    if slot.get("state") == "done" and isinstance(score, (int, float)) and not isinstance(score, bool):
                        earned[side] += float(score) * weight
        self.points = {side: round(earned[side], 6) for side in ("a", "b")}

        if total > 0:
            lead = self.points["b"] - self.points["a"]
            damage_a = max(0.0, lead) / total * 100.0
            damage_b = max(0.0, -lead) / total * 100.0
            hp_a = max(0.0, 100.0 - damage_a)
            hp_b = max(0.0, 100.0 - damage_b)
        else:
            hp_a = hp_b = 100.0
        if self.ko:
            winner = self._winner_side()
            if winner == "a":
                hp_b = 0.0
            elif winner == "b":
                hp_a = 0.0
        self.hp = {"a": round(hp_a, 2), "b": round(hp_b, 2)}

    def _winner_side(self) -> str | None:
        if self.winner in ("a", "b"):
            return self.winner
        for side in ("a", "b"):
            if self.winner and self.winner == (self.fighters.get(side) or {}).get("id"):
                return side
        return None

    def snapshot(self) -> dict:
        return {
            "status": self.status,
            "fight_id": self.fight_id,
            "card": {
                "title": self.card.get("title"),
                "lanes": list(self.card.get("lanes") or []),
            },
            "fighters": {
                "a": dict(self.fighters["a"]),
                "b": dict(self.fighters["b"]),
            },
            "hp": dict(self.hp),
            "rounds": _copy_rounds(self.rounds),
            "sudden_death": _copy_rounds([self.sudden_death])[0] if self.sudden_death else None,
            "winner": self.winner,
            "ko": bool(self.ko),
            "edge": self.edge,
            "points": dict(self.points),
            "usage": {
                "a": dict(self.usage["a"]),
                "b": dict(self.usage["b"]),
            },
            "report_path": self.report_path,
            "updated": self.updated,
        }

    # kept for engine convenience / tests
    def to_dict(self) -> dict:
        return self.snapshot()

    def _touch(self) -> None:
        try:
            self._recompute()
            for group in self._iter_groups():
                self._refresh_round(group)
            self.updated = _now_iso()
            payload = self.snapshot()
            try:
                _atomic_write_json(self.path, payload)
            except Exception:
                pass
            if self.broadcast_enabled:
                _broadcast(payload)
        except Exception:
            pass

    # ------------------------------------------------------------ mutations

    @_guard
    def start(self) -> dict:
        self.status = "fighting"
        self._touch()
        return self.snapshot()

    @_guard
    def set_status(self, status: str) -> dict:
        status = str(status or "")
        if status in ("idle", "fighting", "scoring", "done", "error"):
            self.status = status
            self._touch()
        return self.snapshot()

    @_guard
    def task_started(self, round_id, task_id, side=None) -> dict:
        group, task = self._find(round_id, task_id)
        if task is None:
            return self.snapshot()
        sides = [side] if side in ("a", "b") else ["a", "b"]
        for which in sides:
            task[which]["state"] = "fighting"
        if group is not None:
            group["state"] = "fighting"
        if self.status == "idle":
            self.status = "fighting"
        self._touch()
        return self.snapshot()

    @_guard
    def update_task(
        self,
        round_id,
        task_id,
        side,
        state=None,
        score=None,
        latency_s=None,
        cost_usd=None,
        detail=None,
    ) -> dict:
        _, task = self._find(round_id, task_id)
        if task is None or side not in ("a", "b"):
            return self.snapshot()
        slot = task[side]
        if state is not None:
            state = str(state)
            state = STATE_ALIASES.get(state, state)
            if state in STATES:
                slot["state"] = state
        if score is not None:
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                slot["score"] = float(score)
            else:
                slot["score"] = None
        if latency_s is not None:
            slot["latency_s"] = _num(latency_s)
        if cost_usd is not None:
            slot["cost_usd"] = _num(cost_usd)
        if detail is not None:
            slot["detail"] = str(detail)
        self._touch()
        return self.snapshot()

    @_guard
    def task_done(
        self,
        round_id,
        task_id,
        side,
        score=None,
        latency_s=None,
        cost_usd=None,
        detail=None,
    ) -> dict:
        return self.update_task(
            round_id,
            task_id,
            side,
            state="done",
            score=score,
            latency_s=latency_s,
            cost_usd=cost_usd,
            detail=detail,
        )

    @_guard
    def task_error(self, round_id, task_id, side, detail=None) -> dict:
        return self.update_task(round_id, task_id, side, state="error", detail=detail)

    @_guard
    def task_skipped(self, round_id, task_id, side, detail=None) -> dict:
        return self.update_task(round_id, task_id, side, state="skipped", detail=detail)

    @_guard
    def apply_task_result(self, round_id, task_id, side, result: dict) -> dict:
        result = result if isinstance(result, dict) else {}
        state = result.get("state") or result.get("status")
        if state == "scored":
            state = "done"
        return self.update_task(
            round_id,
            task_id,
            side,
            state=state,
            score=result.get("score"),
            latency_s=result.get("latency_s"),
            cost_usd=result.get("cost_usd"),
            detail=result.get("detail"),
        )

    # Common engine alias
    record_task = apply_task_result

    @_guard
    def add_usage(self, side, cost_usd=0.0, tokens=0, latency_s=0.0) -> dict:
        if side not in ("a", "b"):
            return self.snapshot()
        slot = self.usage[side]
        if cost_usd is None:
            slot["cost_usd"] = None
        elif slot.get("cost_usd") is not None:
            slot["cost_usd"] = round(_num(slot.get("cost_usd")) + _num(cost_usd), 9)
        slot["tokens"] = int(_num(slot.get("tokens")) + _num(tokens))
        slot["latency_s"] = round(_num(slot.get("latency_s")) + _num(latency_s), 6)
        self._touch()
        return self.snapshot()

    @_guard
    def finish(self, winner=None, ko=False, report_path=None, edge=None) -> dict:
        self.status = "done"
        if winner is not None:
            self.winner = winner
        self.ko = bool(ko)
        if edge is not None:
            self.edge = edge
        if report_path is not None:
            self.report_path = str(report_path)
        self._touch()
        return self.snapshot()

    @_guard
    def fail(self, detail=None) -> dict:
        self.status = "error"
        self._touch()
        return self.snapshot()


# ------------------------------------------------------------------ helpers


def _copy_rounds(groups) -> list[dict]:
    out: list[dict] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        tasks = []
        for task in group.get("tasks") or []:
            tasks.append(
                {
                    "id": task.get("id"),
                    "lane": task.get("lane", ""),
                    "title": task.get("title", ""),
                    "a": dict(task.get("a") or {}),
                    "b": dict(task.get("b") or {}),
                }
            )
        out.append(
            {
                "id": group.get("id"),
                "name": group.get("name", ""),
                "points": group.get("points", 1.0),
                "state": group.get("state", "waiting"),
                "tasks": tasks,
            }
        )
    return out


def _broadcast(payload: dict) -> None:
    """Best-effort event bus fan-out; unavailable or broken buses are ignored."""
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event  # type: ignore
    except Exception:
        return
    try:
        broadcast_plugin_event(EVENT_PLUGIN, EVENT_NAME, payload)
    except Exception:
        pass
