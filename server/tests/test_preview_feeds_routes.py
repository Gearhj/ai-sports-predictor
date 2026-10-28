"""The redesigned site's /preview-api/* feeds on the production backend (2026-10-25): reads only, same JSON."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.preview_feeds_routes import router


def _client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_validation_and_unknown_endpoints():
    c = _client()
    assert c.get("/preview-api/arena/live?league=mlb&date=20261025&home=A&away=B").status_code == 400
    assert c.get("/preview-api/football/live?league=nfl").status_code == 400
    assert c.get("/preview-api/insights/nope").status_code == 404


def test_feeds_pass_through_and_a_provider_failure_is_a_502(monkeypatch):
    from app.preview_feeds import court_live, feeds

    monkeypatch.setattr(court_live, "court_live", lambda league, date, home, away: {"found": True, "league": league, "home": home})
    r = _client().get("/preview-api/arena/live?league=nhl&date=20261025&home=CHI&away=FLA")
    assert r.status_code == 200 and r.json() == {"found": True, "league": "nhl", "home": "CHI"}

    def boom(*a, **k):
        raise TimeoutError("espn down")
    monkeypatch.setattr(feeds, "football_live", boom)
    r = _client().get("/preview-api/football/live?league=nfl&date=20261025")
    assert r.status_code == 502 and r.json()["found"] is False


def test_lite_football_thins_the_win_probability(monkeypatch):
    from app.preview_feeds import feeds

    monkeypatch.setattr(feeds, "football_live", lambda *a: {"found": True, "teams": [], "win_probability": list(range(400)), "plays": [1, 2]})
    body = _client().get("/preview-api/football/live?league=nfl&date=20261025&lite=1").json()
    assert "plays" not in body and len(body["win_probability"]) <= 82 and body["win_probability"][-1] == 399


def test_underdogs_insight_is_served_and_days_are_clamped(monkeypatch):
    from app.preview_feeds import insights_db

    seen = []
    monkeypatch.setattr(insights_db, "underdogs", lambda days=120: seen.append(days) or {"rows": [], "days": days})
    c = _client()
    assert c.get("/preview-api/insights/underdogs").json() == {"rows": [], "days": 120}
    assert c.get("/preview-api/insights/underdogs?days=9999").status_code == 200
    assert seen == [120, 120]


def test_crowd_vibe_validates_and_passes_through(monkeypatch):
    from app.preview_feeds import crowd_vibe

    monkeypatch.setattr(crowd_vibe, "vibe", lambda league, home, away, hn, an: {"league": league.upper(), "home_name": hn})
    c = _client()
    assert c.get("/preview-api/vibe").status_code == 400
    assert c.get("/preview-api/vibe?league=nhl&home=EDM&away=<script>").status_code == 400
    r = c.get("/preview-api/vibe?league=nhl&home=EDM&away=CGY&home_name=Edmonton%20Oilers")
    assert r.status_code == 200 and r.json() == {"league": "NHL", "home_name": "Edmonton Oilers"}


def test_heals_expose_only_display_fields(monkeypatch, tmp_path):
    import json

    from app.api import preview_feeds_routes as routes

    log = tmp_path / "heal.jsonl"
    log.write_text("\n".join([
        json.dumps({"at": "2026-10-28T02:20:01+00:00", "heal": "TRIGGER_RESULT", "job": "collect-nba-odds-api", "why": "NBA POR @ LAL",
                    "status": "succeeded", "job_run_id": "secret-id", "result": "{'quota': 1}"}),
        json.dumps({"at": "x", "heal": "TRIGGER_ERROR", "error": r"C:\Users\james\x.py failed"}),
        "not json",
    ]), encoding="utf-8")
    monkeypatch.setattr(routes, "HEAL_LOG", log)
    heals = _client().get("/preview-api/heals").json()["heals"]
    assert heals[0] == {"at": "2026-10-28T02:20:01+00:00", "heal": "TRIGGER_RESULT", "job": "collect-nba-odds-api", "why": "NBA POR @ LAL", "status": "succeeded"}
    assert heals[1]["error"] == "error" and "Users" not in json.dumps(heals)
    monkeypatch.setattr(routes, "HEAL_LOG", tmp_path / "missing.jsonl")
    assert _client().get("/preview-api/heals").json() == {"heals": []}
