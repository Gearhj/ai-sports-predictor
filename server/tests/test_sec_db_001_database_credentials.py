"""SEC-DB-001: no shared database credential and fail-closed configuration.

Proves that the application requires an explicitly supplied database URL outside
tests, that tests may still inject an explicit disposable URL, and that no
credential-bearing PostgreSQL URL literal is committed to the tracked tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]

# A literal embedded credential: user:password@ where neither part is a shell
# ${...} interpolation or empty. Fail-closed env interpolation and empty defaults
# do not match.
_CREDENTIAL_URL = re.compile(r"postgresql(?:\+[a-z0-9]+)?://[A-Za-z0-9_.-]+:[^@\s${}/]+@")

_SCAN_DIRS = ("server/app", "server/alembic", "docs")
_SCAN_FILES = (".env.example", "docker-compose.yml", "server/alembic.ini")
_SKIP_SUFFIXES = (".pyc",)
_SKIP_NAMES = {"test_sec_db_001_database_credentials.py"}


def _iter_tracked_text_files():
    for rel in _SCAN_FILES:
        path = REPO_ROOT / rel
        if path.is_file():
            yield path
    for rel in _SCAN_DIRS:
        base = REPO_ROOT / rel
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix in _SKIP_SUFFIXES or path.name in _SKIP_NAMES:
                continue
            if "__pycache__" in path.parts:
                continue
            yield path


def test_missing_database_url_fails_closed_outside_tests(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError) as excinfo:
        Settings(app_env="development", database_url="", _env_file=None)
    assert "DATABASE_URL must be set" in str(excinfo.value)


def test_missing_database_url_fails_closed_in_production(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(app_env="production", database_url="", _env_file=None)


def test_explicit_test_url_is_accepted() -> None:
    settings = Settings(app_env="test", database_url="sqlite://", _env_file=None)
    assert settings.database_url == "sqlite://"


def test_injected_development_url_is_accepted() -> None:
    url = "postgresql+psycopg://someuser:somepass@localhost:5432/somedb"
    settings = Settings(app_env="development", database_url=url, _env_file=None)
    assert settings.database_url == url


def test_default_database_url_is_empty() -> None:
    # The shipped default carries no credential; it is the empty string.
    assert Settings.model_fields["database_url"].default == ""


def test_no_committed_credential_bearing_database_url() -> None:
    offenders: list[str] = []
    for path in _iter_tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _CREDENTIAL_URL.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"credential-bearing database URL committed in: {offenders}"
