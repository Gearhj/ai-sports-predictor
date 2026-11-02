"""/healthz says ok or degraded and why (scale plan Phase 1, item 5)."""


def test_healthz_reports_its_checks(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "reachable"
    assert body["checks"]["scheduler"] == "disabled"  # test settings
    assert body["checks"]["cache_warmers"] == "disabled"
    assert body["uptime_seconds"] >= 0 and body["as_of"]
    assert "build" in body


def test_healthz_is_degraded_when_the_database_is_gone(client, monkeypatch):
    from app.db import session as db_session

    class Broken:
        def __enter__(self):
            raise RuntimeError("down")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(db_session, "SessionLocal", lambda: Broken())
    body = client.get("/healthz").json()
    assert body["status"] == "degraded" and body["checks"]["database"] == "unreachable"
