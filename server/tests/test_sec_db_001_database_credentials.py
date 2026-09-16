"""SEC-DB-001: no shipped credentials, and configuration that fails closed.

Proves that outside tests the application requires an explicitly supplied
database URL and signing key, that no administrator password is shipped, that
bootstrapping an administrator refuses a missing or short password, and that no
credential-bearing PostgreSQL URL is committed to the scanned tree.

Ported from claude/aisp-critical-integrity-repair-v1 (f4e90a6, 7f6698e) onto the
development line, and extended to the signing key and administrator password.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import Settings
from app.models import User
from app.services.auth_service import BootstrapConflictError, ensure_admin

REPO_ROOT = Path(__file__).resolve().parents[2]

# An embedded credential: user:password@ where the user is a literal name. Shell
# ${...} interpolation and <placeholder> users do not match.
_CREDENTIAL_URL = re.compile(r"postgres(?:ql)?(?:\+[a-z0-9]+)?://[A-Za-z0-9_.-]+:([^@\s'\"`]+)@")
# A password that is exactly one documentation template token, e.g. <redacted>.
_TEMPLATE_TOKEN = re.compile(r"^<[a-z_ -]+>$")

_SCAN_DIRS = ("server/app", "server/alembic", "server/scripts", "docs")
_SCAN_FILES = (".env.example", "docker-compose.yml", "server/alembic.ini", "README.md")
_SKIP_SUFFIXES = (".pyc", ".png", ".jpg", ".jpeg", ".gz", ".parquet")

_STRONG = "x" * 40


def _fake_url(password: str) -> str:
    # Built at runtime so this file never holds a credential-shaped literal.
    return "postgresql+psycopg://" + "someuser" + ":" + password + "@localhost:5432/somedb"


def _iter_scanned_files():
    for rel in _SCAN_FILES:
        path = REPO_ROOT / rel
        if path.is_file():
            yield path
    for rel in _SCAN_DIRS:
        base = REPO_ROOT / rel
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix not in _SKIP_SUFFIXES and "__pycache__" not in path.parts:
                yield path


# --- database URL -----------------------------------------------------------

def test_missing_database_url_fails_closed_outside_tests(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError) as excinfo:
        Settings(app_env="development", database_url="", secret_key=_STRONG, _env_file=None)
    assert "DATABASE_URL must be set" in str(excinfo.value)


def test_missing_database_url_fails_closed_in_production(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(app_env="production", database_url="", secret_key=_STRONG, _env_file=None)


def test_explicit_test_url_is_accepted() -> None:
    settings = Settings(app_env="test", database_url="sqlite://", _env_file=None)
    assert settings.database_url == "sqlite://"


def test_injected_development_url_is_accepted() -> None:
    url = _fake_url("p" * 12)
    settings = Settings(app_env="development", database_url=url, secret_key=_STRONG, _env_file=None)
    assert settings.database_url == url


def test_no_database_url_default_is_shipped() -> None:
    assert Settings.model_fields["database_url"].default == ""


# --- signing key ------------------------------------------------------------

def test_no_signing_key_default_is_shipped() -> None:
    assert Settings.model_fields["secret_key"].default == ""


def test_missing_signing_key_fails_closed_outside_tests(monkeypatch) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ValidationError) as excinfo:
        Settings(app_env="development", database_url=_fake_url("p" * 12), secret_key="", _env_file=None)
    assert "SECRET_KEY must be set" in str(excinfo.value)


def test_short_signing_key_is_still_rejected_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production", database_url=_fake_url("p" * 12), secret_key="short",
                 admin_password="A" * 16, demo_mode=False, cors_origins="https://example.invalid",
                 _env_file=None)


# --- administrator password -------------------------------------------------

def test_no_admin_password_default_is_shipped() -> None:
    assert Settings.model_fields["admin_password"].default == ""


def test_production_rejects_a_short_admin_password() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(app_env="production", database_url=_fake_url("p" * 12), secret_key=_STRONG,
                 admin_password="short", demo_mode=False, cors_origins="https://example.invalid",
                 _env_file=None)
    assert "ADMIN_PASSWORD" in str(excinfo.value)


@pytest.mark.parametrize("password", ["", "elevenchars"])
def test_bootstrap_refuses_a_missing_or_short_admin_password(session, settings, password) -> None:
    weak = settings.model_copy(update={"admin_password": password})
    with pytest.raises(BootstrapConflictError, match="ADMIN_PASSWORD must be set"):
        ensure_admin(session, weak)
    assert session.scalar(select(func.count()).select_from(User)) == 0


def test_bootstrap_creates_the_admin_with_an_explicit_password(session, settings) -> None:
    admin = ensure_admin(session, settings)
    assert admin.email == settings.admin_email.strip().lower()


def test_an_existing_admin_is_returned_without_needing_the_password(session, settings) -> None:
    """The live credential is the stored hash; ADMIN_PASSWORD only bootstraps."""
    created = ensure_admin(session, settings)
    again = ensure_admin(session, settings.model_copy(update={"admin_password": ""}))
    assert again.id == created.id


# --- committed tree ---------------------------------------------------------

def test_no_committed_credential_bearing_database_url() -> None:
    offenders: list[str] = []
    for path in _iter_scanned_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _CREDENTIAL_URL.finditer(text):
            if not _TEMPLATE_TOKEN.match(match.group(1)):
                offenders.append(str(path.relative_to(REPO_ROOT)))
                break
    assert not offenders, f"credential-bearing database URL committed in: {offenders}"


def test_the_scanner_would_catch_a_real_looking_password(tmp_path) -> None:
    """Guards the guard: a template token is skipped, a real-looking value is not."""
    assert _TEMPLATE_TOKEN.match("<redacted>")
    for sample in (_fake_url("s3cr3t-Value"), _fake_url("<redacted>")):
        m = _CREDENTIAL_URL.search(sample)
        assert m is not None
    assert not _TEMPLATE_TOKEN.match(_CREDENTIAL_URL.search(_fake_url("s3cr3t-Value")).group(1))
