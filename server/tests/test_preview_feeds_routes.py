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
