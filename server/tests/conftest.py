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


@pytest.fixture
def client(session: Session, settings: Settings):
    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
