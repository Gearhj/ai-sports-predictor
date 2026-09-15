"""The served frontend never shadows the API, the probes or the docs."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.frontend import mount_frontend


@pytest.fixture()
def client(tmp_path: Path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    app = FastAPI()

    @app.get("/api/v1/ping")
    def ping():
        return {"pong": True}

    @app.get("/readyz")
    def readyz():
        return {"status": "ready"}

    mount_frontend(app, tmp_path)
    return TestClient(app)


def test_a_client_route_returns_the_app_uncached(client):
    response = client.get("/college-football/games/401858225")
    assert response.status_code == 200 and "id=root" in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_hashed_assets_are_served(client):
    assert client.get("/assets/app-abc123.js").status_code == 200


def test_a_real_top_level_file_is_served_as_itself(client):
    assert client.get("/favicon.svg").text == "<svg/>"


def test_api_and_probes_are_never_answered_by_the_app(client):
    assert client.get("/api/v1/ping").json() == {"pong": True}
    assert client.get("/readyz").json() == {"status": "ready"}
    missing = client.get("/api/v1/does-not-exist")
    assert missing.status_code == 404 and "id=root" not in missing.text


def test_path_escape_is_refused(client):
    assert "id=root" in client.get("/../../etc/passwd").text or client.get("/../../etc/passwd").status_code == 404


def test_frontendz_reports_the_build(client):
    body = client.get("/frontendz").json()
    assert body["present"] is True and len(body["index_sha256"]) == 64
