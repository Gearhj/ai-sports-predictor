"""The startup RUNTIME_SETTINGS_PROOF line: pin echoed, secrets never."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic_settings import SettingsConfigDict

from datetime import datetime

from app.core import config as config_module
from app.core.config import Settings, _construct_with_load_proof, settings_load_proof
from app.main import app, emit_runtime_settings_proof

PIN = "d1dc9e3a0b1c2d3e4f5061728394a5b6c7d8e9f0"
SECRET_KEY = "SYNTH-SECRET-KEY-zz9-plural-z-alpha-0123456789"
DATABASE_URL = "postgresql+psycopg://synthuser:SYNTH-DB-PASSWORD-qq7@127.0.0.1:5432/synthdb"  # secret-guard: test-fixture
ODDS_KEY = "SYNTH-ODDS-API-KEY-77aa"
SGO_KEY = "SYNTH-SGO-API-KEY-88bb"
SDIO_KEY = "SYNTH-SDIO-API-KEY-99cc"
SECRET_SUBSTRINGS = (
    SECRET_KEY,
    "SYNTH-SECRET",
    DATABASE_URL,
    "SYNTH-DB-PASSWORD",
    "synthuser",
    ODDS_KEY,
    SGO_KEY,
    SDIO_KEY,
    "API-KEY",
    "postgresql",
)
_ENV_KEYS = (
    "NFL_FINAL_REQUIRED_PRODUCER_REVISION",
    "NFL_FINAL_READINESS_REQUIRED",
    "SECRET_KEY",
    "DATABASE_URL",
    "APP_ENV",
    "THE_ODDS_API_KEY",
    "SPORTSGAMEODDS_API_KEY",
    "SPORTSDATAIO_API_KEY",
    "NFL_LATE_CHECKPOINT_RETIME_ENABLED",
    "SPORTSGAMEODDS_ENABLED",
    "SCHEDULER_ENABLED",
    "NFL_SHADOW_ENABLED",
)


def _synthetic(tmp_path: Path, monkeypatch, *, revision: str = PIN, write: bool = True,
               retime: bool = True, via_load_proof: bool = False):
    env_path = tmp_path / ".env"
    if write:
        env_path.write_bytes(
            (
                f"APP_ENV=development\n"
                f"SECRET_KEY={SECRET_KEY}\n"
                f"DATABASE_URL={DATABASE_URL}\n"
                f"THE_ODDS_API_KEY={ODDS_KEY}\n"
                f"SPORTSGAMEODDS_API_KEY={SGO_KEY}\n"
                f"SPORTSDATAIO_API_KEY={SDIO_KEY}\n"
                f"NFL_FINAL_REQUIRED_PRODUCER_REVISION={revision}\n"
                f"NFL_FINAL_READINESS_REQUIRED=true\n"
                f"NFL_LATE_CHECKPOINT_RETIME_ENABLED={'true' if retime else 'false'}\n"
                f"SPORTSGAMEODDS_ENABLED=true\n"
                f"SCHEDULER_ENABLED=true\n"
            ).encode("utf-8")
        )
    # The dotenv is the only source: process variables would otherwise win.
    for key in list(os.environ):
        if key.upper() in _ENV_KEYS:
            monkeypatch.delenv(key, raising=False)

    class SyntheticSettings(Settings):
        model_config = SettingsConfigDict(
            env_file=str(env_path),
            env_file_encoding="utf-8",
            extra="ignore",
            case_sensitive=False,
        )

    if not write:
        return SyntheticSettings(
            app_env="test",
            database_url="sqlite://",
            nfl_final_required_producer_revision=revision,
        ), env_path
    if via_load_proof:
        return _construct_with_load_proof(SyntheticSettings), env_path
    return SyntheticSettings(), env_path


def _proof_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "RUNTIME_SETTINGS_PROOF" in r.getMessage()]


def _fields(line: str) -> dict[str, str]:
    head, *rest = line.split(" ")
    assert head == "RUNTIME_SETTINGS_PROOF"
    return dict(part.split("=", 1) for part in rest)


def test_line_carries_the_pin_and_no_secret(tmp_path, monkeypatch, caplog):
    settings, env_path = _synthetic(tmp_path, monkeypatch)
    # The synthetic dotenv really was the source of every value below.
    assert settings.secret_key == SECRET_KEY
    assert settings.database_url == DATABASE_URL
    monkeypatch.setenv("ASP_SCHEDULER_INSTANCE", "0123456789abcdef0123456789abcdef")

    with caplog.at_level(logging.INFO, logger="app.runtime_proof"):
        returned = emit_runtime_settings_proof(settings, cached_before=True)

    lines = _proof_lines(caplog)
    assert lines == [returned]
    line = lines[0]
    fields = _fields(line)
    assert fields["pid"] == str(os.getpid())
    assert fields["ppid"] == str(os.getppid())
    assert fields["loaded_at_utc"].endswith("+00:00")
    assert fields["nfl_final_required_producer_revision"] == PIN
    assert fields["nfl_final_readiness_required"] == "True"
    assert fields["env_file_sha256"] == hashlib.sha256(env_path.read_bytes()).hexdigest()
    assert fields["scheduler_instance"] == "0123456789abcdef0123456789abcdef"
    assert fields["settings_cached_before"] == "True"
    assert fields["revision_env_override"] == "False"
    assert fields["env_file"] == str(env_path)
    for secret in SECRET_SUBSTRINGS:
        assert secret not in line
    for name in ("SECRET_KEY", "DATABASE_URL", "API_KEY", "api_key", "secret_key", "database_url"):
        assert name not in line


def test_empty_pin_and_unreadable_env_and_unset_instance(tmp_path, monkeypatch, caplog):
    settings, _ = _synthetic(tmp_path, monkeypatch, revision="", write=False)
    monkeypatch.delenv("ASP_SCHEDULER_INSTANCE", raising=False)
    with caplog.at_level(logging.INFO, logger="app.runtime_proof"):
        emit_runtime_settings_proof(settings, cached_before=False)
    fields = _fields(_proof_lines(caplog)[0])
    assert fields["nfl_final_required_producer_revision"] == "EMPTY"
    assert fields["env_file_sha256"] == "UNREADABLE"
    assert fields["scheduler_instance"] == "UNSET"
    assert fields["settings_cached_before"] == "False"


def test_a_non_hex_value_is_digested_not_echoed(tmp_path, monkeypatch, caplog):
    settings, _ = _synthetic(tmp_path, monkeypatch, revision=SECRET_KEY)
    monkeypatch.setenv("ASP_SCHEDULER_INSTANCE", SGO_KEY)
    with caplog.at_level(logging.INFO, logger="app.runtime_proof"):
        emit_runtime_settings_proof(settings, cached_before=None)
    line = _proof_lines(caplog)[0]
    fields = _fields(line)
    assert fields["nfl_final_required_producer_revision"].startswith("sha256_12:")
    assert fields["scheduler_instance"].startswith("sha256_12:")
    for secret in SECRET_SUBSTRINGS:
        assert secret not in line


def test_a_broken_settings_object_never_raises(caplog):
    with caplog.at_level(logging.INFO, logger="app.runtime_proof"):
        assert emit_runtime_settings_proof(object(), cached_before=True) is None
    assert _proof_lines(caplog) == []


def test_app_startup_emits_exactly_one_line_from_this_process(caplog):
    with caplog.at_level(logging.INFO, logger="app.runtime_proof"):
        with TestClient(app):
            pass
    lines = _proof_lines(caplog)
    assert len(lines) == 1
    fields = _fields(lines[0])
    assert fields["pid"] == str(os.getpid())
    # create_app() at import already populated the lru_cache.
    assert fields["settings_cached_before"] == "True"
    for key in ("settings_load_env_sha256_before", "settings_load_env_sha256_after",
                "settings_load_before_utc", "settings_load_after_utc",
                "settings_load_interval_stable", "nfl_late_checkpoint_retime_enabled",
                "nfl_final_readiness_required", "sportsgameodds_enabled",
                "scheduler_enabled", "nfl_shadow_enabled", "app_env"):
        assert key in fields


_HEX64 = set("0123456789abcdef")


def _emit(settings, caplog) -> tuple[str, dict[str, str]]:
    with caplog.at_level(logging.INFO, logger="app.runtime_proof"):
        emit_runtime_settings_proof(settings, cached_before=True)
    line = _proof_lines(caplog)[-1]
    return line, _fields(line)


def test_stable_file_gives_before_equal_after_and_a_bracketing_clock(tmp_path, monkeypatch, caplog):
    settings, env_path = _synthetic(tmp_path, monkeypatch, via_load_proof=True)
    assert settings.secret_key == SECRET_KEY  # really loaded from the synthetic file
    expected = hashlib.sha256(env_path.read_bytes()).hexdigest()
    line, f = _emit(settings, caplog)
    assert f["settings_load_env_sha256_before"] == expected
    assert f["settings_load_env_sha256_after"] == expected
    assert f["settings_load_interval_stable"] == "true"
    assert f["settings_load_before_utc"].endswith("+00:00")
    assert f["settings_load_after_utc"].endswith("+00:00")
    before = datetime.fromisoformat(f["settings_load_before_utc"])
    after = datetime.fromisoformat(f["settings_load_after_utc"])
    loaded = datetime.fromisoformat(f["loaded_at_utc"])
    assert before <= after <= loaded
    for secret in SECRET_SUBSTRINGS:
        assert secret not in line


def test_a_write_between_before_and_after_is_not_stable(tmp_path, monkeypatch, caplog):
    reads = iter([b"IMAGE-ONE", b"IMAGE-TWO"])
    monkeypatch.setattr(config_module, "_read_env_file_bytes", lambda path: next(reads))
    settings, _ = _synthetic(tmp_path, monkeypatch, via_load_proof=True)
    _, f = _emit(settings, caplog)
    assert f["settings_load_env_sha256_before"] == hashlib.sha256(b"IMAGE-ONE").hexdigest()
    assert f["settings_load_env_sha256_after"] == hashlib.sha256(b"IMAGE-TWO").hexdigest()
    assert f["settings_load_interval_stable"] == "false"


def test_a_real_write_during_construction_is_not_stable(tmp_path, monkeypatch, caplog):
    settings, env_path = _synthetic(tmp_path, monkeypatch)
    cls = type(settings)

    class WritesDuringLoad(cls):
        def __init__(self, **kw):
            super().__init__(**kw)
            env_path.write_bytes(env_path.read_bytes() + b"# touched\n")

    original = hashlib.sha256(env_path.read_bytes()).hexdigest()
    loaded = _construct_with_load_proof(WritesDuringLoad)
    _, f = _emit(loaded, caplog)
    assert f["settings_load_env_sha256_before"] == original
    assert f["settings_load_env_sha256_after"] != original
    assert f["settings_load_interval_stable"] == "false"


def test_an_unreadable_load_probe_never_breaks_loading(tmp_path, monkeypatch, caplog):
    def boom(path):
        raise OSError("nope")

    monkeypatch.setattr(config_module, "_read_env_file_bytes", boom)
    settings, _ = _synthetic(tmp_path, monkeypatch, via_load_proof=True)
    assert settings.nfl_final_required_producer_revision == PIN
    _, f = _emit(settings, caplog)
    assert f["settings_load_env_sha256_before"] == "UNREADABLE"
    assert f["settings_load_env_sha256_after"] == "UNREADABLE"
    assert f["settings_load_interval_stable"] == "false"


def test_an_instance_without_a_recorded_load_is_unrecorded(tmp_path, monkeypatch, caplog):
    settings, _ = _synthetic(tmp_path, monkeypatch)  # constructed directly
    assert settings_load_proof(settings) is None
    _, f = _emit(settings, caplog)
    for key in ("settings_load_env_sha256_before", "settings_load_env_sha256_after",
                "settings_load_before_utc", "settings_load_after_utc"):
        assert f[key] == "UNRECORDED"
    assert f["settings_load_interval_stable"] == "false"


def test_the_runtime_profile_fields_are_present_and_bare(tmp_path, monkeypatch, caplog):
    settings, _ = _synthetic(tmp_path, monkeypatch, via_load_proof=True)
    line, f = _emit(settings, caplog)
    assert f["nfl_late_checkpoint_retime_enabled"] == "true"
    assert f["nfl_final_readiness_required"] == "True"
    assert f["sportsgameodds_enabled"] == "true"
    assert f["scheduler_enabled"] == "true"
    assert f["nfl_shadow_enabled"] == "false"
    assert f["app_env"] == "development"
    for secret in SECRET_SUBSTRINGS:
        assert secret not in line
    for name in ("SECRET_KEY", "DATABASE_URL", "API_KEY", "api_key", "secret_key", "database_url"):
        assert name not in line

    off, _ = _synthetic(tmp_path, monkeypatch, retime=False, via_load_proof=True)
    _, f_off = _emit(off, caplog)
    assert f_off["nfl_late_checkpoint_retime_enabled"] == "false"


def test_every_value_is_bare_hex_bool_enum_iso_or_digest(tmp_path, monkeypatch, caplog):
    settings, env_path = _synthetic(tmp_path, monkeypatch, via_load_proof=True)
    _, f = _emit(settings, caplog)
    for key, value in f.items():
        if key == "env_file":
            assert value == str(env_path)
            continue
        ok = (
            value in {"True", "False", "true", "false", "None", "EMPTY", "UNSET",
                      "UNREADABLE", "UNRECORDED", "development", "test", "production"}
            or value.isdigit()
            or (len(value) >= 7 and set(value.lower()) <= _HEX64)
            or value.startswith("sha256_12:")
            or value.endswith("+00:00")
        )
        assert ok, (key, value)


def test_the_cached_get_settings_carries_its_own_load_proof():
    # The lru_cache object the app uses is constructed through the proof.
    from app.core.config import get_settings

    # __wrapped__ is exactly what the cache calls; calling it leaves the cache alone.
    fresh = get_settings.__wrapped__()
    proof = settings_load_proof(fresh)
    assert proof is not None
    assert set(proof) == {"env_sha256_before", "env_sha256_after", "before_utc",
                          "after_utc", "interval_stable"}
