"""The readiness gate inside the decision path: both flag states, and recovery.

The flag is the ACTIVATION SWITCH. The contract requires the readiness family
"for FINALs from the activated operational-V2 producer revision", and that
revision is not activated, so shipping with the requirement ON would fail-close
every FINAL from the unchanged CURRENT producer. Shipping it OFF but SILENT
would mean deploying a gate nobody can see working. So: always runs, always
records, blocks only when armed. Both behaviours are pinned here.
"""

from __future__ import annotations

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
