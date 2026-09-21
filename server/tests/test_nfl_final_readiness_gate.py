"""The readiness gate inside the decision path: both flag states, and recovery.

The flag is the ACTIVATION SWITCH. The contract requires the readiness family
"for FINALs from the activated operational-V2 producer revision", and that
revision is not activated, so shipping with the requirement ON would fail-close
every FINAL from the unchanged CURRENT producer. Shipping it OFF but SILENT
would mean deploying a gate nobody can see working. So: always runs, always
records, blocks only when armed. Both behaviours are pinned here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStatus, GameTeam, League, Season, ShadowForecast,
    ShadowForecastBundle, Sport, Team,
)
from app.services import nfl_final_readiness as gate
from app.services import nfl_paper_decision as nfl
from tests.fixtures import nfl_final_readiness as fx

KICKOFF = fx.KICKOFF
TICK = KICKOFF - timedelta(minutes=24)
ACTIVATED = KICKOFF - timedelta(days=7)


@pytest.fixture
def armed(session: Session, settings, tmp_path: Path):
    """One NFL game, one FINAL forecast row, and a complete readiness family.

    The forecast row carries the SAME forecast id and event id as the synthetic
    family on disk, which is what makes the join meaningful: the gate is asked
    about the exact snapshot the checkpoint selected.
    """

    from app.services.nfl_activation import activate

    family = fx.build(tmp_path)
    settings.nfl_final_readiness_root = str(tmp_path)
    # The armed state we will actually deploy is armed AND PINNED. Arming alone
    # is refused by the gate, so every test below that flips the flag inherits
    # the pins here rather than exercising a configuration we will never run.
    settings.nfl_final_required_producer_revision = fx.PRODUCER_REVISION
    # THE SCHEMA PIN IS OVER A FILE, NOT OVER A RECEIPT FIELD. The consumer
    # re-hashes Model Studio's reviewed contract from disk every run, so the
    # armed configuration has to name both the hash and where that file lives.
    # Pinning it here also means every document this synthetic family emits is
    # checked, field for field, against the producer's own published shape.
    settings.nfl_final_readiness_schema_sha256 = fx.SCHEMA_SHA256
    settings.nfl_final_readiness_schema_path = str(fx.SCHEMA_PATH)

    sport = Sport(code="FOOTBALL", name="Football")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="NFL", name="NFL")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=KICKOFF - timedelta(days=30),
                    ends_at=KICKOFF + timedelta(days=120))
    session.add(season)
    session.flush()
    teams = []
    for abbreviation in ("HOM", "AWY"):
        team = Team(league_id=league.id, name=f"{abbreviation} team",
                    abbreviation=abbreviation)
        session.add(team)
        teams.append(team)
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=KICKOFF,
                status=GameStatus.SCHEDULED)
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=teams[0].id, is_home=True),
        GameTeam(game_id=game.id, team_id=teams[1].id, is_home=False)])
    bundle = ShadowForecastBundle(
        bundle_sha256=family.bundle_sha256, bundle_id=f"b-{uuid.uuid4().hex[:8]}",
        logical_uri="test://bundle", source_fixture_id="fix-1",
        slate_date=date(2026, 9, 21), league="NFL", sport="FOOTBALL",
        status="IMPORTED", research_shadow_only=True, generated_at=KICKOFF,
        forecast_count=1, quote_count=0, candidate_count=1,
        # Imported well before T-15, so join 4's package clock passes.
        imported_at=KICKOFF - timedelta(minutes=23),
        contract_version="v1", evidence={})
    session.add(bundle)
    session.flush()
    activate(session, settings, now=ACTIVATED)
    session.commit()
    era = nfl._era(session)
    assert era is not None
    return {"session": session, "settings": settings, "era": era, "game": game,
            "bundle": bundle, "family": family, "root": tmp_path}


def _final(ctx, *, forecast_id: str | None = None, event_id: str | None = None,
           phase: str = nfl.PHASE_FINAL, version: int = 1):
    family = ctx["family"]
    row = ShadowForecast(
        bundle_id=ctx["bundle"].id,
        forecast_id=forecast_id or family.forecast_id,
        engine_id="NFL_M1_XGBOOST", engine_version="1.0",
        model_artifact_sha256=uuid.uuid4().hex, simulator_id="sim",
        simulator_version="1", simulator_seed=1,
        event_id=event_id or family.event_id,
        source_event_id=fx.source_event_id(event_id or family.event_id),
        event_date=date(2026, 9, 21), season=2026,
        home_team_id="HOM", away_team_id="AWY",
        is_ready=True, readiness_score=99.0, readiness_threshold=50.0,
        status="READY",
        full_home_win_probability=0.5753, full_away_win_probability=0.4047,
        full_expected_home_score=23.0, full_expected_away_score=21.0,
        distributions={}, uncertainty={}, readiness_detail={},
        forecast_schema_version="1", snapshot_type=phase, snapshot_version=version,
        identity_evidence={}, lineage_detail={},
        canonical_game_id=ctx["game"].id,
        available_as_of_utc=KICKOFF - timedelta(minutes=25))
    ctx["session"].add(row)
    ctx["session"].flush()
    ctx["session"].commit()
    return row


def _decide(ctx, *, now=None):
    return nfl._decide(
        ctx["session"], ctx["era"],
        {"game_id": str(ctx["game"].id), "away": "AWY", "home": "HOM",
         "starts_at": KICKOFF.isoformat()},
        now or (KICKOFF - timedelta(minutes=18)),
        ctx["settings"])


# ---------------------------------------------------------------------------
# the flag, both ways
# ---------------------------------------------------------------------------

def test_the_requirement_ships_off_by_default() -> None:
    from app.core.config import Settings

    assert Settings.model_fields["nfl_final_readiness_required"].default is False


def test_with_the_flag_off_an_unverified_final_is_recorded_not_blocked(armed) -> None:
    """The pre-activation behaviour: evidence without a new failure mode.

    The family on disk describes a DIFFERENT forecast, so readiness cannot
    verify. With the flag off that must not stop the FINAL from controlling --
    the decision proceeds to the next gate and simply carries the verdict.
    """

    armed["settings"].nfl_final_readiness_required = False
    _final(armed, forecast_id="asp:nfl:forecast:sha256:" + "e" * 64)

    out = _decide(armed)

    assert out["reason"] != gate.REASON
    assert out["readiness"]["verified"] is False
    assert out["readiness"]["sub_reasons"] == [gate.READINESS_INDEX_NO_ENTRY]
    assert out["readiness_required"] is False


def test_with_the_flag_on_an_unverified_final_is_blocked(armed) -> None:
    armed["settings"].nfl_final_readiness_required = True
    _final(armed, forecast_id="asp:nfl:forecast:sha256:" + "e" * 64)

    out = _decide(armed)

    assert out["outcome"] == "BLOCKED"
    assert out["reason"] == gate.REASON
    assert gate.READINESS_INDEX_NO_ENTRY in out["detail"]
    assert out["readiness"]["verified"] is False


def test_with_the_flag_on_a_verified_final_passes_the_gate(armed) -> None:
    """Armed and satisfied: readiness stops being the reason for anything."""

    armed["settings"].nfl_final_readiness_required = True
    _final(armed)

    out = _decide(armed)

    assert out["reason"] != gate.REASON
    assert out["readiness"]["verified"] is True
    assert out["readiness"]["exposure_status"] == gate.STATUS_TIMELY
    assert out["forecast_snapshot_phase"] == nfl.PHASE_FINAL


def test_the_verdict_is_recorded_whichever_way_the_flag_is_set(armed) -> None:
    armed["settings"].nfl_final_readiness_required = False
    _final(armed)

    out = _decide(armed)

    assert out["readiness"]["verified"] is True
    # The consumer's own clocks are on the record, not the producer's.
    assert out["readiness"]["consumer_observed_at_utc"]
    assert out["readiness"]["package_imported_at_utc"]
    assert out["readiness"]["checkpoint_selected_forecast_id"] == (
        armed["family"].forecast_id)


# ---------------------------------------------------------------------------
# what readiness may never do
# ---------------------------------------------------------------------------

def test_a_current_forecast_is_never_rescued_by_readiness_evidence(armed) -> None:
    """The readiness family describes the FINAL. A CURRENT row is still blocked.

    Even armed, even with complete positive evidence physically on disk for a
    forecast id this CURRENT row could claim, the decision stops at
    NO_FINAL_PREGAME_FORECAST -- readiness is only ever consulted about a FINAL
    that was already selected on its own merits.
    """

    armed["settings"].nfl_final_readiness_required = True
    _final(armed, phase=nfl.PHASE_CURRENT, version=8)

    out = _decide(armed)

    assert out["outcome"] == "BLOCKED"
    assert out["reason"] == "NO_FINAL_PREGAME_FORECAST"
    assert out["controlling"] is False
    # Readiness was never even consulted, so it cannot have supplied authority.
    assert "readiness" not in out


def test_a_readiness_block_is_recoverable_by_a_later_tick(armed) -> None:
    """Four separate filesystem operations means the proof can land late.

    A missing sidecar at 19:50 and a present one at 19:55 are the same game in
    the same window, so the first answer must not be pinned.
    """

    assert gate.REASON in nfl.PREREQUISITE_REASONS


def test_a_sidecar_arriving_mid_window_flips_the_verdict(armed) -> None:
    armed["settings"].nfl_final_readiness_required = True
    _final(armed)
    sidecar = armed["family"].availability_path()
    retained = sidecar.read_bytes()
    sidecar.unlink()

    blocked = _decide(armed)
    assert blocked["outcome"] == "BLOCKED"
    assert blocked["readiness"]["sub_reasons"] == [gate.EXPOSURE_REFERENCE_ABSENT]

    sidecar.write_bytes(retained)
    recovered = _decide(armed, now=KICKOFF - timedelta(minutes=16))
    assert recovered["reason"] != gate.REASON
    assert recovered["readiness"]["verified"] is True


def test_an_unconfigured_exchange_root_fails_closed_when_armed(armed) -> None:
    armed["settings"].nfl_final_readiness_required = True
    armed["settings"].nfl_final_readiness_root = ""
    armed["settings"].probability_exchange_root = ""
    _final(armed)

    out = _decide(armed)

    assert out["outcome"] == "BLOCKED"
    assert out["reason"] == gate.REASON
    assert out["readiness"]["sub_reasons"] == ["READINESS_ROOT_NOT_CONFIGURED"]


def test_a_late_checkpoint_cannot_relabel_its_missed_cutoff(armed) -> None:
    """Running after T-15 is a late observation, not a readiness pass."""

    armed["settings"].nfl_final_readiness_required = True
    _final(armed)

    out = _decide(armed, now=KICKOFF - timedelta(minutes=14))

    assert out["outcome"] == "BLOCKED"
    assert out["readiness"]["sub_reasons"] == [gate.CONSUMER_OBSERVATION_LATE]


# --------------------------------------------------------------------------
# pinning the reviewed operational-V2 producer revision
# --------------------------------------------------------------------------

def test_an_unpinned_revision_accepts_any_nonempty_revision(tmp_path):
    """The state we ship in: honest, and exactly why enforcement stays off."""
    from tests.fixtures.nfl_final_readiness import build
    from app.services import nfl_final_readiness as rg

    fam = build(tmp_path)
    out = rg.verify_final_readiness(
        exchange_root=fam.root, forecast_id=fam.forecast_id, event_id=fam.event_id,
        kickoff_utc=fam.kickoff, observed_at=fam.consumer_cutoff - timedelta(minutes=1))
    assert out.verified is True


def test_a_pinned_revision_refuses_a_receipt_from_another_revision(tmp_path):
    """An authentic receipt from the WRONG producer is not positive evidence."""
    from tests.fixtures.nfl_final_readiness import build
    from app.services import nfl_final_readiness as rg

    fam = build(tmp_path)
    out = rg.verify_final_readiness(
        exchange_root=fam.root, forecast_id=fam.forecast_id, event_id=fam.event_id,
        kickoff_utc=fam.kickoff, observed_at=fam.consumer_cutoff - timedelta(minutes=1),
        required_producer_revision="078e5dafd1f61fd42b7a36e4d6a4e8073be0c50b")
    assert out.verified is False
    assert rg.PRODUCER_REVISION_NOT_PINNED in out.sub_reasons


def test_a_pinned_revision_accepts_the_matching_receipt(tmp_path):
    from tests.fixtures.nfl_final_readiness import build
    from app.services import nfl_final_readiness as rg

    fam = build(tmp_path)
    receipt = json.loads(fam.receipt_path().read_text(encoding="utf-8"))
    # Read from `engine`, which is where the authentic receipt carries it: the
    # revision is part of the ENGINE identity, not a property of the document.
    out = rg.verify_final_readiness(
        exchange_root=fam.root, forecast_id=fam.forecast_id, event_id=fam.event_id,
        kickoff_utc=fam.kickoff, observed_at=fam.consumer_cutoff - timedelta(minutes=1),
        required_producer_revision=str(receipt["engine"]["producerRevision"]))
    assert out.verified is True


def _reshape_exposure(fam, mutate):
    """Reshape the exposure HONESTLY and re-point the sidecar at it.

    New bytes get a new address and the bare sidecar reference is updated to
    name it, so the content-address check passes cleanly and the SHAPE check is
    what refuses. Without this the reshape would be caught as an address
    mismatch and the test would pass for the wrong reason.
    """

    from app.gen2.exchange.canonical_json import canonical_json_bytes
    from app.services import nfl_final_readiness as rg

    document = json.loads(fam.exposure_path().read_text(encoding="utf-8"))
    mutate(document)
    raw = canonical_json_bytes(document)
    sha = hashlib.sha256(raw).hexdigest()
    target = fam.root / rg.READINESS_ROOT / rg.OBJECTS_DIR / sha
    target.mkdir(parents=True, exist_ok=True)
    (target / "exposure.json").write_bytes(raw)
    sidecar = fam.availability_path()
    reference = json.loads(sidecar.read_text(encoding="utf-8"))
    reference.update({"sha256": sha, "bytes": len(raw)})
    sidecar.write_bytes(canonical_json_bytes(reference))


def test_a_pinned_schema_hash_refuses_a_reshaped_document(tmp_path):
    """A field the reviewed schema forbids is refused under a live pin.

    THIS TEST CHANGED SHAPE, NOT INTENT.
    ------------------------------------
    It used to pass a wrong hash and expect RECEIPT_SCHEMA_MISMATCH, because
    the old reading had the receipt DECLARE its schema hash, so a mismatched
    pin meant "this receipt is the wrong shape". The pin is over the reviewed
    CONTRACT FILE, so a wrong hash now means the contract itself could not be
    bound -- which is a different, and separately tested, refusal below. The
    reshape this test names is expressed directly instead: pin the real
    contract, then add a field it forbids.
    """
    from tests.fixtures.nfl_final_readiness import SCHEMA_PATH, SCHEMA_SHA256, build
    from app.services import nfl_final_readiness as rg

    fam = build(tmp_path)
    _reshape_exposure(fam, lambda doc: doc.__setitem__("unexpectedField", "x"))
    out = rg.verify_final_readiness(
        exchange_root=fam.root, forecast_id=fam.forecast_id, event_id=fam.event_id,
        kickoff_utc=fam.kickoff, observed_at=fam.consumer_cutoff - timedelta(minutes=1),
        required_schema_sha256=SCHEMA_SHA256, schema_path=SCHEMA_PATH)
    assert out.verified is False
    assert out.sub_reasons == (rg.RECEIPT_SCHEMA_MISMATCH,), out.detail


def test_a_schema_hash_that_binds_to_nothing_is_refused(tmp_path):
    """A pin that cannot be satisfied fails closed rather than being skipped."""
    from tests.fixtures.nfl_final_readiness import SCHEMA_PATH, build
    from app.services import nfl_final_readiness as rg

    fam = build(tmp_path)
    common = dict(
        exchange_root=fam.root, forecast_id=fam.forecast_id, event_id=fam.event_id,
        kickoff_utc=fam.kickoff, observed_at=fam.consumer_cutoff - timedelta(minutes=1))

    wrong_hash = rg.verify_final_readiness(
        **common, required_schema_sha256="0" * 64, schema_path=SCHEMA_PATH)
    no_file = rg.verify_final_readiness(
        **common, required_schema_sha256="0" * 64, schema_path=None)

    for out in (wrong_hash, no_file):
        assert out.verified is False
        assert out.sub_reasons == (rg.READINESS_SCHEMA_UNBOUND,), out.detail


def test_arming_enforcement_without_a_pin_is_refused(tmp_path, settings, monkeypatch):
    """Armed-but-unpinned is a false assurance, so the gate refuses outright."""
    from app.services import nfl_paper_decision as nfl
    from app.services import nfl_final_readiness as rg

    monkeypatch.setattr(settings, "nfl_final_readiness_required", True, raising=False)
    monkeypatch.setattr(settings, "nfl_final_required_producer_revision", "", raising=False)
    monkeypatch.setattr(settings, "nfl_final_readiness_schema_sha256", "", raising=False)

    class _FC:
        forecast_id = "asp-sports-game-forecast:sha256:" + "a" * 64
        event_id = "2026_03_NYG_LA"
        snapshot_type = "FINAL_PREGAME"
        snapshot_version = 1
        engine_id = "NFL_M1_XGBOOST"
        bundle_id = None

    out = nfl._check_readiness(
        None, settings, _FC(),
        kickoff=datetime(2026, 9, 22, 0, 15, tzinfo=UTC),
        moment=datetime(2026, 9, 21, 23, 55, tzinfo=UTC))
    assert out.verified is False
    assert "READINESS_ENFORCEMENT_ARMED_WITHOUT_PIN" in out.sub_reasons


def test_a_pinned_schema_hash_accepts_the_reviewed_contract(tmp_path):
    """The pin is case-insensitive over the contract file's own bytes."""
    from tests.fixtures.nfl_final_readiness import SCHEMA_PATH, SCHEMA_SHA256, build
    from app.services import nfl_final_readiness as rg

    fam = build(tmp_path)
    out = rg.verify_final_readiness(
        exchange_root=fam.root, forecast_id=fam.forecast_id, event_id=fam.event_id,
        kickoff_utc=fam.kickoff, observed_at=fam.consumer_cutoff - timedelta(minutes=1),
        required_schema_sha256=SCHEMA_SHA256.upper(), schema_path=SCHEMA_PATH)
    assert out.verified is True, out.sub_reasons


def test_a_required_field_missing_under_a_live_pin_is_refused_not_skipped(tmp_path):
    """Absence under a live pin is a refusal, not a pass.

    THE OLD VERSION OF THIS TEST ASKED AN IMPOSSIBLE QUESTION.
    ---------------------------------------------------------
    It removed a ``schemaSha256`` field from the receipt and expected
    RECEIPT_SCHEMA_ABSENT. There is no such field on an authentic receipt and no
    such sub-reason in the module: the hash is the CONTRACT's, not the
    document's. What the test was actually protecting -- "if a required field is
    not there, fail closed rather than skip the comparison" -- is preserved by
    removing a field the reviewed schema requires and pinning the real contract.
    """
    from tests.fixtures.nfl_final_readiness import SCHEMA_PATH, SCHEMA_SHA256, build
    from app.services import nfl_final_readiness as rg

    fam = build(tmp_path)
    _reshape_exposure(fam, lambda doc: doc.pop("clockMeaning"))
    out = rg.verify_final_readiness(
        exchange_root=fam.root, forecast_id=fam.forecast_id, event_id=fam.event_id,
        kickoff_utc=fam.kickoff, observed_at=fam.consumer_cutoff - timedelta(minutes=1),
        required_schema_sha256=SCHEMA_SHA256, schema_path=SCHEMA_PATH)
    assert out.verified is False
    assert out.sub_reasons == (rg.RECEIPT_SCHEMA_MISMATCH,), out.detail
    assert "clockMeaning" in out.detail
