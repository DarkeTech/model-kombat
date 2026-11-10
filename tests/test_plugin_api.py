"""Tests for dashboard/plugin_api.py (read-only routes, never-raise contract).

Run: python -m pytest tests/test_plugin_api.py -q
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:  # fastapi/httpx are test-only deps; the plugin itself declares none.
    from fastapi import FastAPI  # noqa: F401
    from fastapi.testclient import TestClient  # noqa: F401

    import dashboard.plugin_api as api  # noqa: E402
except ImportError:
    if __name__ == "__main__":
        print("SKIP test_plugin_api: fastapi/httpx not installed "
              "(pytest-only suite) — run: pip install fastapi httpx pytest")
        raise SystemExit(0)
    raise


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_KOMBAT_DATA_DIR", str(tmp_path))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_missing_files_defaults(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["live_exists"] is False

    live = client.get("/live")
    assert live.status_code == 200
    assert live.json()["status"] == "idle"
    assert live.json()["hp"] == {"a": 100, "b": 100}

    assert client.get("/ladder").json() == {"updated": None, "lanes": {}}
    assert client.get("/recommendations").json() == {}
    assert client.get("/history").json() == []
    assert client.get("/cards").json() == []


def test_files_roundtrip(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    live_payload = {"status": "fighting", "hp": {"a": 80, "b": 60}}
    (tmp_path / "live.json").write_text(json.dumps(live_payload), encoding="utf-8")
    (tmp_path / "ladder.json").write_text(
        json.dumps({"updated": "2026-11-10T10:00:00Z", "lanes": {"coding": {"models": {}}}}),
        encoding="utf-8",
    )
    (tmp_path / "recommendations.json").write_text(
        json.dumps({"t_abc": {"lane": "coding", "pick": "stealth/crouton-alpha",
                              "short": "Crouton", "confidence": "medium", "elo": 1512}}),
        encoding="utf-8",
    )
    (tmp_path / "cards.json").write_text(
        json.dumps({
            "card-abc123": {"id": "card-abc123", "title": "Kombat card — coding · sql",
                            "lanes": ["coding", "sql"]},
            "card-def456": {"title": "Kombat card — debugging",
                            "lane_weights": [{"lane": "debugging", "weight": 1.0}]},
            "junk": "not a card",
        }),
        encoding="utf-8",
    )
    assert client.get("/live").json()["hp"] == {"a": 80, "b": 60}
    assert client.get("/ladder").json()["lanes"].keys() == {"coding"}
    assert client.get("/recommendations").json()["t_abc"]["short"] == "Crouton"
    assert client.get("/health").json()["live_exists"] is True
    assert client.get("/cards").json() == [
        {"id": "card-abc123", "title": "Kombat card — coding · sql", "lanes": ["coding", "sql"]},
        {"id": "card-def456", "title": "Kombat card — debugging", "lanes": ["debugging"]},
    ]


def test_corrupt_files_never_raise(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    (tmp_path / "live.json").write_text("{ not json", encoding="utf-8")
    (tmp_path / "ladder.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "recommendations.json").write_text("oops", encoding="utf-8")
    (tmp_path / "cards.json").write_text("{ not json", encoding="utf-8")
    assert client.get("/live").status_code == 200
    assert client.get("/live").json()["status"] == "idle"
    assert client.get("/ladder").json() == {"updated": None, "lanes": {}}
    assert client.get("/recommendations").json() == {}
    assert client.get("/cards").json() == []


def test_history_limit_and_bad_lines(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    lines = [json.dumps({"fight_id": f"f{i}", "n": i}) for i in range(5)]
    lines.append("not json")
    lines.append(json.dumps([1, 2, 3]))  # non-dict skipped
    (tmp_path / "fights.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = client.get("/history?limit=3")
    assert r.status_code == 200
    assert [rec["n"] for rec in r.json()] == [2, 3, 4]
    # Default limit returns everything kept.
    assert len(client.get("/history").json()) == 5
    # Out-of-range limit is clamped, not an error.
    assert client.get("/history?limit=99999").json()


def test_direct_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_KOMBAT_DATA_DIR", str(tmp_path))
    assert api.health()["ok"] is True
    assert api.live()["status"] == "idle"
    assert api.history(limit=5) == []
    assert api.recommendations() == {}
    assert api.cards() == []
