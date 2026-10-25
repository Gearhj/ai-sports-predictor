"""The configured producer-evidence directory must actually reach build_record.

It was configured and readable from the first deployment, but the export owner
omitted `evidence_dir`, so it defaulted to None. Every record therefore resolved
PREGAME_SCHEDULE_MANIFEST_NOT_SUPPLIED, and season type, week, the nflverse
identity and the narrow alias set were null in all of them -- including the
first natural ATL-GB record, which stays exactly as written.

A setting that exists and is never read is worse than one that is missing: the
configuration says the evidence is bound and the records say it is absent.
"""
from __future__ import annotations

import ast
import hashlib
import json
import pathlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
import app.models  # noqa: F401
from app.research_exports import nfl_early_line as el

MODULE = pathlib.Path(el.__file__)
EVIDENCE = pathlib.Path.home() / "asp-model-studio" / "outputs" / (
    "nfl-early-line-information-edge-v1") / "producer-evidence"
SCHEDULE_SHA = "f80236ce1e9de10088adbd5b0c244169e13ebd5554754370aa0b5234811447cb"

#: The first natural record. Immutable: a code repair is not authority to
#: revise a closed horizon.
FIRST_RECORD = (pathlib.Path.home() / "asp-model-exchange"
                / "nfl-early-line-research-v1" / "records"
                / "1f3403f3-be4e-4ef3-a2e7-befdf81b76fe" / "EARLY_T24"
                / "775b6b7166b74880c6d17a524c32e78beba635910638dbef965657ee8c8d68c7.json")
FIRST_RECORD_SHA = "775b6b7166b74880c6d17a524c32e78beba635910638dbef965657ee8c8d68c7"

needs_evidence = pytest.mark.skipif(
    not (EVIDENCE / "schedule" / "sha256" / SCHEDULE_SHA / "SCHEDULE_IDENTITY.json").is_file(),
    reason="producer evidence absent")


def _owner_source() -> str:
    lines = MODULE.read_text(encoding="utf-8").splitlines()
    fn = next(n for n in ast.walk(ast.parse(MODULE.read_text(encoding="utf-8")))
              if isinstance(n, ast.FunctionDef) and n.name == "run_early_line_capture")
    return chr(10).join(lines[fn.lineno - 1:fn.end_lineno])


# ==========================================================================
# The wiring itself
# ==========================================================================

def test_the_owner_passes_the_configured_evidence_directory():
    """The keyword is present at the call, and it comes from Settings."""

    body = _owner_source()
    assert "settings.nfl_early_line_producer_evidence_dir" in body
    assert "evidence_dir=evidence_dir" in body


def test_omitting_the_keyword_makes_this_fail():
    """The guard against the exact regression: no keyword, no binding.

    This is what the deployed bytes did. Reproduced here as an assertion about
    the source rather than a comment, so the defect cannot return silently.
    """

    body = _owner_source()
    call_start = body.index("build_record(")
    call = body[call_start:body.index("))", call_start) + 2]
    assert "evidence_dir" in call, (
        "build_record is called without evidence_dir; it would default to None "
        "and every record would resolve PREGAME_SCHEDULE_MANIFEST_NOT_SUPPLIED")


@needs_evidence
def test_the_exact_schedule_manifest_loads_from_the_configured_directory():
    manifest, reason = el.load_schedule_identity(EVIDENCE)

    assert reason is None
    assert manifest["manifestSha256"] == SCHEDULE_SHA
    assert manifest["contract"] == "NFL_PREGAME_SCHEDULE_IDENTITY_V1"
    assert len(manifest["games"]) == 240


@needs_evidence
def test_season_type_week_identity_and_aliases_become_available():
    """What was null in the first record, and will not be in future ones."""

    manifest, _ = el.load_schedule_identity(EVIDENCE)
    row = next(g for g in manifest["games"] if g["nflverseGameId"] == "2026_03_ATL_GB")

    assert (row["season"], row["seasonType"], row["week"]) == (2026, "REG", 3)
    assert row["nflverseGameId"] == "2026_03_ATL_GB"
    assert row["home"]["supportedProviderCodes"] == ["GB"]
    assert row["away"]["supportedProviderCodes"] == ["ATL"]


@needs_evidence
def test_the_row_resolves_for_a_game_by_kickoff_and_both_teams():
    manifest, _ = el.load_schedule_identity(EVIDENCE)
    game = SimpleNamespace(
        starts_at=datetime(2026, 9, 25, 0, 15, tzinfo=UTC),
        home_abbr="GB", away_abbr="ATL")

    row = el.schedule_row(manifest, game)
    assert row is not None and row["nflverseGameId"] == "2026_03_ATL_GB"


# ==========================================================================
# Fail-closed behaviour is unchanged
# ==========================================================================

@pytest.mark.parametrize("directory", [None, pathlib.Path("Z:/no/such/place")])
def test_a_missing_or_unreadable_directory_still_fails_closed(directory):
    manifest, reason = el.load_schedule_identity(directory)

    assert manifest is None
    assert reason == "PREGAME_SCHEDULE_MANIFEST_NOT_SUPPLIED"


def test_an_unresolvable_row_is_still_nothing(tmp_path):
    """A present manifest that does not contain the game resolves to nothing."""

    game = SimpleNamespace(starts_at=datetime(2026, 9, 25, 0, 15, tzinfo=UTC),
                           home_abbr="ZZZ", away_abbr="YYY")
    assert el.schedule_row({"games": []}, game) is None
    assert el.schedule_row(None, game) is None


# ==========================================================================
# Nothing else moves
# ==========================================================================

def test_no_provider_call_or_database_write_is_introduced():
    body = _owner_source()
    for forbidden in ("httpx", "requests.", "session.add", "session.commit",
                      "session.execute(insert", "fetch_nfl_pregame", "record_request"):
        assert forbidden not in body, forbidden
    # The read still happens inside the read-only transaction helper.
    assert "with read_only_session(session):" in body


def test_horizon_timing_and_selection_constants_are_untouched():
    assert (el.EARLY_EARLIEST, el.EARLY_TARGET, el.EARLY_LATEST) == (108000, 86400, 64800)
    assert (el.LATE_PRIMARY_EARLIEST, el.LATE_PRIMARY_LATEST) == (1620, 1500)
    assert (el.LATE_RETRY, el.LATE_HARD_STOP, el.LATE_CHECKPOINT) == (1380, 1260, 900)
    assert el.SETTLING == timedelta(minutes=10)
    assert el.HORIZONS == ("MARKET_OPEN_PROXY", "EARLY_T24", "LATE_T26")
    assert el.CONTRACT_FILES == {
        "FROZEN_PROTOCOL.json": el.PROTOCOL_SHA256,
        "CONSUMER_EXPORT_CONTRACT.md": el.CONTRACT_SHA256,
        "validate_observation.py": el.VALIDATOR_SHA256,
    }


def test_the_capture_flag_still_gates_the_owner():
    off = el.run_early_line_capture(
        None, SimpleNamespace(nfl_early_line_capture_enabled=False))
    assert off == {"enabled": False, "records": 0,
                   "note": "NFL_EARLY_LINE_CAPTURE_ENABLED is off; nothing read, "
                           "nothing written."}


def test_only_one_source_file_changed():
    """The repair is one file: the export owner's call site."""

    import subprocess

    root = MODULE.parent.parent.parent.parent
    changed = subprocess.run(["git", "-C", str(root), "diff", "--name-only",
                              "ffcfa8217baf4e6d65624bf62eedd57865420ead", "--",
                              "server/app"], capture_output=True, text=True).stdout.split()
    assert changed == ["server/app/research_exports/nfl_early_line.py"], changed


# ==========================================================================
# The closed horizon stays closed
# ==========================================================================

@pytest.mark.skipif(not FIRST_RECORD.is_file(), reason="first record absent")
def test_the_first_natural_record_is_byte_identical():
    """A code repair is not authority to revise a closed horizon."""

    raw = FIRST_RECORD.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIRST_RECORD_SHA
    doc = json.loads(raw)
    # It still says what it said: the evidence was absent when it was written.
    assert doc["eligibility"]["state"] == "EXCLUDED"
    assert doc["producerEvidence"]["scheduleRowDisposition"] == (
        "PREGAME_SCHEDULE_MANIFEST_NOT_SUPPLIED")
    assert doc["game"]["seasonType"] is None and doc["game"]["week"] is None


@pytest.mark.skipif(not FIRST_RECORD.is_file(), reason="first record absent")
def test_the_repair_writes_nothing_into_the_live_output_root():
    """Importing and exercising the module must not touch the live records."""

    root = FIRST_RECORD.parent.parent.parent.parent
    before = sorted((p.name, p.stat().st_size) for p in root.rglob("*") if p.is_file())
    el.load_schedule_identity(EVIDENCE if EVIDENCE.exists() else None)
    after = sorted((p.name, p.stat().st_size) for p in root.rglob("*") if p.is_file())
    assert before == after
