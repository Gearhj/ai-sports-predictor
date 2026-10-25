from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DEMO_MODE", "true")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.audit import install_immutability_guards
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture
def artifact_root() -> Iterator[Path]:
    path = Path(".test-artifacts") / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    yield path
    shutil.rmtree(path, ignore_errors=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass


@pytest.fixture
def settings(artifact_root: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url="sqlite://",
        demo_mode=True,
        the_odds_api_key="",
        sportsdataio_api_key="",
        odds_polling_enabled=False,
        open_meteo_enabled=False,
        statcast_enabled=False,
        scheduler_enabled=False,
        simulation_draws=500,
        simulation_seed=314159,
        raw_data_root=artifact_root / "raw",
        curated_data_root=artifact_root / "curated",
        feature_data_root=artifact_root / "features",
        secret_key="test-secret-with-at-least-thirty-two-characters",
        admin_email="admin@example.com",
        admin_password="TestAdministrator123!",
        # Stated rather than inherited. Pydantic reads the deployed .env for
        # anything this fixture leaves out, so when the owner switched Show
        # Preview on for the package import, tests asserting the off-state
        # started failing on the environment rather than on the code. A test
        # that wants a switch on says so; the fixture is the off-state.
        show_preview_enabled=False,
        show_preview_provider_collection_enabled=False,
        show_preview_auto_freeze_enabled=False,
    )


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    install_immutability_guards()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db_session:
        yield db_session
        db_session.rollback()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _postgres_varchar_lengths():
    """Postgres refuses an over-length VARCHAR; SQLite stores it. Hold every decision/position write in the
    suite to the production column lengths (tests/postgres_column_lengths.py), even when the code under test
    swallows the flush error."""
    from tests import postgres_column_lengths as lengths

    lengths.install()
    lengths.drain()
    yield
    seen = lengths.drain()
    assert not seen, f"Postgres would refuse these writes (VARCHAR too long): {seen}"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Isolate the process-global rate-limiter between tests."""
    from app.core.rate_limit import get_rate_limiter

    get_rate_limiter().reset()
    yield
    get_rate_limiter().reset()


@pytest.fixture
def client(session: Session, settings: Settings):
    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def admin_headers(client, session: Session, settings: Settings) -> dict[str, str]:
    """Authenticate as the bootstrap administrator for admin-only routes."""
    from app.services.auth_service import ensure_admin

    ensure_admin(session, settings)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": settings.admin_email, "password": settings.admin_password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
