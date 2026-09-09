"""The invariants that would be expensive to discover in production.

Grouped by what breaks if they stop holding:

* the play importer must refuse evidence that does not reconcile, because a
  theater built on unverified plays is a theater that invents them;
* the funnel must attribute every lost game to exactly one gate, and the
  stages must close arithmetically, because a residual bucket is where an
  inconvenient failure goes to hide;
* the credit lanes must refuse a spend that would eat a protected allocation,
  because that refusal IS the reserve.
"""
from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.forecast_exchange.nfl_play_level_v1 import (
    ARTIFACT,
    KIND_PLAY,
    PlayEvidenceUnavailable,
    _verify_game,
    _verify_geometry,
    read_representative_game,
)
from app.forecast_exchange.supplements import SupplementImportError
from app.services.experiment_funnel import (
    EXECUTION_DEFECT,
    INCOMPLETE_EVIDENCE,
    INSUFFICIENT_EDGE,
    MISSING_FORECAST,
    MISSING_QUOTE,
    STALE_QUOTE,
    classify,
)

EXCHANGE = Path(r"C:\Users\james\asp-model-exchange")
INDEX = EXCHANGE / "nfl-labs-play-level-supplements" / "current.json"


def _first_artifact() -> dict:
    index = json.loads(INDEX.read_bytes())
    return json.loads(Path(index["artifacts"][0]["artifact"]["path"]).read_bytes())


requires_evidence = pytest.mark.skipif(
    not INDEX.is_file(), reason="play-level exchange evidence is not present on this host")


# ---------------------------------------------------------------------------
# the importer refuses what it cannot reconcile
# ---------------------------------------------------------------------------

@requires_evidence
def test_a_real_representative_game_verifies() -> None:
    artifact = _first_artifact()
    counted = _verify_game(
        artifact["simulatedGames"][0], population=artifact["populationFinals"], label="probe")
    assert counted["plays"] > 0
    assert counted["drives"] > 0


@requires_evidence
def test_a_broken_state_join_is_refused() -> None:
    """The one invariant an animated replay depends on completely.

    If a play's stateBefore does not continue the previous stateAfter, then
    something happened between two plays that no play records -- and the
    animation would be showing a continuity the evidence does not have.
    """
    artifact = _first_artifact()
    game = copy.deepcopy(artifact["simulatedGames"][0])
    game["plays"][3]["stateBefore"]["yardline100"] = 7.0
    with pytest.raises(SupplementImportError, match="does not continue the previous"):
        _verify_game(game, population=artifact["populationFinals"], label="probe")


@requires_evidence
def test_a_clock_that_does_not_match_its_outcome_is_refused() -> None:
    artifact = _first_artifact()
    game = copy.deepcopy(artifact["simulatedGames"][0])
    scrimmage = next(p for p in game["plays"] if p["playType"] != "PERIOD_END")
    scrimmage["outcome"]["seconds"] = scrimmage["outcome"]["seconds"] + 3
    with pytest.raises(SupplementImportError, match="clock consumed"):
        _verify_game(game, population=artifact["populationFinals"], label="probe")


@requires_evidence
def test_a_score_that_does_not_match_its_increment_is_refused() -> None:
    artifact = _first_artifact()
    game = copy.deepcopy(artifact["simulatedGames"][0])
    scoring = next(p for p in game["plays"]
                   if p["outcome"]["homePointsAdded"] or p["outcome"]["awayPointsAdded"])
    side = "homePointsAdded" if scoring["outcome"]["homePointsAdded"] else "awayPointsAdded"
    scoring["outcome"][side] = 1
    with pytest.raises(SupplementImportError, match="score moved by"):
        _verify_game(game, population=artifact["populationFinals"], label="probe")


@requires_evidence
def test_a_game_that_does_not_reconcile_to_its_population_is_refused() -> None:
    artifact = _first_artifact()
    game = copy.deepcopy(artifact["simulatedGames"][0])
    game["homeScore"] = game["homeScore"] + 1
    with pytest.raises(SupplementImportError, match="final score differs"):
        _verify_game(game, population=artifact["populationFinals"], label="probe")


@requires_evidence
def test_geometry_outside_the_field_is_refused() -> None:
    artifact = _first_artifact()
    play = copy.deepcopy(next(p for p in artifact["simulatedGames"][0]["plays"]
                              if p["geometry"]["segments"]))
    play["geometry"]["segments"][0]["end"]["x"] = 140.0
    with pytest.raises(SupplementImportError, match="escapes the field"):
        _verify_geometry(play["geometry"], play_type=play["playType"], label="probe")


@requires_evidence
def test_geometry_claiming_measured_tracking_is_refused() -> None:
    """The anonymity flags are the contract's whole honesty boundary."""
    artifact = _first_artifact()
    play = copy.deepcopy(artifact["simulatedGames"][0]["plays"][1])
    play["geometry"]["measuredTracking"] = True
    with pytest.raises(SupplementImportError, match="no measured tracking"):
        _verify_geometry(play["geometry"], play_type=play["playType"], label="probe")


@requires_evidence
def test_a_period_boundary_may_not_carry_a_ball_path() -> None:
    artifact = _first_artifact()
    boundary = copy.deepcopy(next(p for p in artifact["simulatedGames"][0]["plays"]
                                  if p["playType"] == "PERIOD_END"))
    moving = next(p for p in artifact["simulatedGames"][0]["plays"] if p["geometry"]["segments"])
    boundary["geometry"]["segments"] = copy.deepcopy(moving["geometry"]["segments"])
    with pytest.raises(SupplementImportError, match="no ball path"):
        _verify_geometry(boundary["geometry"], play_type="PERIOD_END", label="probe")


def test_missing_play_evidence_names_its_cause(tmp_path: Path) -> None:
    """An empty animation is indistinguishable from a game with no plays."""

    class Row:
        supplement_sha256 = "0" * 64
        evidence = {"files": {"artifact": {"path": str(tmp_path / "gone.json"), "byteCount": 1}}}

    with pytest.raises(PlayEvidenceUnavailable, match="PLAY_ARTIFACT_FILE_MISSING"):
        read_representative_game(Row(), position=0)  # type: ignore[arg-type]


def test_a_swapped_play_artifact_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_bytes(json.dumps({"simulatedGames": []}).encode())

    class Row:
        supplement_sha256 = "a" * 64  # not the hash of those bytes
        evidence = {"files": {"artifact": {"path": str(path), "byteCount": path.stat().st_size}}}

    with pytest.raises(PlayEvidenceUnavailable, match="CHECKSUM_MISMATCH"):
        read_representative_game(Row(), position=0)  # type: ignore[arg-type]


def test_the_play_kind_is_distinct_from_the_drive_kind() -> None:
    """A reader filtering for drives must not silently pick up plays."""
    from app.forecast_exchange.supplements import KIND_DRIVE

    assert KIND_PLAY != KIND_DRIVE
    assert ARTIFACT != "NFL_LABS_DRIVE_SIMULATION_V1"


# ---------------------------------------------------------------------------
# the funnel attributes every loss to exactly one gate
# ---------------------------------------------------------------------------

def test_the_policy_verdict_outranks_the_context_it_carries() -> None:
    """The bug this prevents is quiet and changes the story.

    A decision evaluated on a lineup-pending tier still records its lineup
    note. Reading that note as the blocker files a game the model simply did
    not like under "evidence incomplete", turning a real finding about the
    threshold into a false story about missing data.
    """
    verdict = ("V2 TIER_C_LINEUP_PENDING: MONEYLINE selection ATH ML does not clear the "
               "adjusted thresholds — Adjusted edge -6.1 pts")
    context = ["Starter evidence not CONFIRMED/PROBABLE_STABLE: home starter PROBABLE_UNSTABLE"]
    assert classify(context, verdict) == INSUFFICIENT_EDGE


def test_a_genuine_evidence_block_is_still_an_evidence_block() -> None:
    verdict = ("V2 TIER_D_INELIGIBLE: Starter evidence not CONFIRMED/PROBABLE_STABLE: "
               "away starter PROBABLE_UNSTABLE")
    assert classify([], verdict) == INCOMPLETE_EVIDENCE


def test_each_reason_category_is_recognised() -> None:
    assert classify([], "No valid current forecast") == MISSING_FORECAST
    assert classify([], "No complete same-sportsbook two-sided MONEYLINE quote") == MISSING_QUOTE
    assert classify([], "Latest quote is stale") == STALE_QUOTE
    assert classify([], "V2 TIER_D_INELIGIBLE: Extreme model-market disagreement") == INCOMPLETE_EVIDENCE
    assert classify([], "Adjusted expected value is not positive") == INSUFFICIENT_EDGE


def test_the_earliest_gate_wins_when_several_are_recorded() -> None:
    """Order decides, so the same failure lands in the same bucket every day."""
    reasons = ["Adjusted edge -1.5 pts below the 2.0 pt minimum",
               "No complete same-sportsbook two-sided MONEYLINE quote"]
    assert classify(reasons) == MISSING_QUOTE


def test_funnel_stages_close_without_a_residual_bucket(session, settings) -> None:
    from app.services.experiment_funnel import ExperimentFunnelService

    report = ExperimentFunnelService(session, settings).funnel()
    if not report.get("available"):
        pytest.skip("no experiment registered in this database")
    stages = report["stages"]
    for index in range(1, len(stages)):
        assert stages[index]["count"] <= stages[index - 1]["count"], "a stage grew"
        named = sum(reason["count"] for reason in stages[index]["reasons"])
        assert named == stages[index]["dropped"], (
            f"stage {stages[index]['key']} lost {stages[index]['dropped']} games but named {named}")
    total_blocked = sum(item["count"] for item in report["blocked_totals"])
    assert total_blocked == stages[0]["count"] - stages[-1]["count"]


def test_the_execution_defect_category_exists_and_is_last() -> None:
    from app.services.experiment_funnel import REASON_ORDER

    assert REASON_ORDER[-1] == EXECUTION_DEFECT


# ---------------------------------------------------------------------------
# the lanes are the reserve
# ---------------------------------------------------------------------------

def test_an_optional_lane_may_not_eat_a_protected_allocation() -> None:
    from app.services.odds_budget_lanes import (
        DEFAULT_LANES, LANE_MLB_RECOVERY, LANE_OPTIONAL_MARKETS, LaneBudget, LaneState,
    )
    from app.services import odds_budget

    # 440 of the month's 500 credits are gone, so 60 remain -- but 250 are
    # promised to the two protected MLB lanes, which have spent nothing.
    base = odds_budget.BudgetState(
        monthly_cap=500, reserve=0, daily_cap=100, floor=0,
        ledger_spent=440, daily_spent=0, requests_this_window=0,
        provider_used=None, provider_remaining=None, provider_reading_at=None,
        window_start=datetime(2026, 9, 1, tzinfo=UTC),
        window_end=datetime(2026, 10, 1, tzinfo=UTC),
        balance_state=odds_budget.BALANCE_UNKNOWN, floor_latched=False,
    )
    lanes = {spec.key: LaneState(spec=spec) for spec in DEFAULT_LANES}
    budget = LaneBudget(base=base, lanes=lanes, unattributed_spend=440,
                        reconciliation={"state": "RECONCILED", "optional_locked": False,
                                        "detail": "", "provider_used": None,
                                        "lane_total": 440, "delta": None})
    assert base.budgeted_remaining == 60
    assert budget.protected_unspent == 250
    allowed, refusal = budget.may_spend(LANE_OPTIONAL_MARKETS, 3)
    assert allowed is False
    assert refusal and "protected" in refusal.lower()
    # The protected lane itself may still spend.
    assert budget.may_spend(LANE_MLB_RECOVERY, 3)[0] is True


def test_a_three_market_mlb_poll_is_an_optional_lane_request() -> None:
    """The exact shape that consumed 495 of September's 500 credits."""
    from app.services.odds_budget_lanes import LANE_OPTIONAL_MARKETS, lane_for_request

    lane = lane_for_request(league_code="MLB", markets=("h2h", "spreads", "totals"))
    assert lane == LANE_OPTIONAL_MARKETS


def test_a_moneyline_only_mlb_request_is_the_required_lane() -> None:
    from app.services.odds_budget_lanes import LANE_MLB_REQUIRED, lane_for_request

    assert lane_for_request(league_code="MLB", markets=("h2h",)) == LANE_MLB_REQUIRED


def test_a_terminal_row_without_its_shape_is_attributed_from_its_reservation() -> None:
    """Without this, every completed request looks shapeless and lands in the
    optional lane -- so the protected lanes report zero spent no matter what
    they actually spent."""
    from app.models import ProviderRequestLedger
    from app.services.odds_budget_lanes import LANE_MLB_REQUIRED, lane_of_ledger_row

    reserved = ProviderRequestLedger(
        provider="THE_ODDS_API", endpoint_key="baseball_mlb_odds", status="RESERVED",
        requested_at=datetime.now(UTC), request_fingerprint="x",
        controlled_live_request=True, source_mode="LIVE",
        response_metadata={"request_shape": {"markets": ["h2h"], "regions": ["us"]}},
    )
    terminal = ProviderRequestLedger(
        provider="THE_ODDS_API", endpoint_key="baseball_mlb_odds", status="SUCCEEDED",
        requested_at=datetime.now(UTC), request_fingerprint="x",
        controlled_live_request=True, source_mode="LIVE",
        response_metadata={"event_count": 15},
    )
    assert lane_of_ledger_row(terminal) != LANE_MLB_REQUIRED
    assert lane_of_ledger_row(terminal, group=[reserved, terminal]) == LANE_MLB_REQUIRED


def test_the_quota_floor_probe_does_not_authorise_the_refused_request() -> None:
    """It used to fall through, which spent three credits at the floor twice.

    The probe exists to refresh a stale balance reading. Reserving it is not a
    licence for the request whose refusal justified it.
    """
    source = Path("app/jobs/orchestrator.py").read_text(encoding="utf-8")
    marker = source.index("QUOTA_RECOVERY_PROBE")
    after = source[marker:marker + 1400]
    assert "raise SourceError" in after, (
        "the floor guard must refuse after reserving the recovery probe")


# ---------------------------------------------------------------------------
# activation: exactly one process may spend, and the alert fires in time
# ---------------------------------------------------------------------------

def _scheduled_odds_jobs(settings) -> list[str]:
    from app.jobs.scheduler import build_scheduler

    known = {"asp-odds-poll", "asp-odds-checkpoint", "asp-live-odds-refresh", "asp-nfl-shadow-odds"}
    scheduler = build_scheduler(settings)
    try:
        return sorted(job.id for job in scheduler.get_jobs() if job.id in known)
    finally:
        try:
            scheduler.shutdown(wait=False)
        except Exception:  # pragma: no cover - never started
            pass


def test_only_the_declared_owner_schedules_a_credit_spending_job(settings, monkeypatch) -> None:
    """Every backend on the host reads the same .env.

    Without an owner token, arming collection in the file arms it in every
    process that happens to be running -- and this host has had four listening
    at once. The token is exported by the supervisor and by nothing else.
    """
    armed = settings.model_copy(update={
        "odds_checkpoint_collection_enabled": True,
        "odds_collection_owner": "asp-backend-8010",
        "scheduler_enabled": True,
        "scheduler_core_jobs_enabled": True,
    })

    monkeypatch.setenv("ASP_ODDS_COLLECTION_OWNER", "asp-backend-8010")
    assert _scheduled_odds_jobs(armed) == ["asp-odds-checkpoint"]

    monkeypatch.setenv("ASP_ODDS_COLLECTION_OWNER", "some-other-process")
    assert _scheduled_odds_jobs(armed) == []

    monkeypatch.delenv("ASP_ODDS_COLLECTION_OWNER", raising=False)
    assert _scheduled_odds_jobs(armed) == []


def test_an_unset_owner_on_either_side_means_no_owner(settings, monkeypatch) -> None:
    """Fail closed twice: a blank token must never match a blank declaration."""
    monkeypatch.setenv("ASP_ODDS_COLLECTION_OWNER", "")
    assert settings.model_copy(update={"odds_collection_owner": ""}).owns_odds_collection is False
    monkeypatch.delenv("ASP_ODDS_COLLECTION_OWNER", raising=False)
    assert settings.model_copy(update={"odds_collection_owner": "x"}).owns_odds_collection is False


def test_the_legacy_pregame_poller_needs_its_own_explicit_flag(settings, monkeypatch) -> None:
    """It consumed 495 of 500 credits in four days. Turning live shadow
    operation on must never be enough to arm it again."""
    monkeypatch.setenv("ASP_ODDS_COLLECTION_OWNER", "asp-backend-8010")
    base = settings.model_copy(update={
        "scheduler_enabled": True, "scheduler_core_jobs_enabled": True,
        "live_shadow_enabled": True, "the_odds_api_key": "test-key",
        "odds_collection_owner": "asp-backend-8010",
    })
    assert "asp-live-odds-refresh" not in _scheduled_odds_jobs(base)
    revived = base.model_copy(update={"odds_legacy_pregame_refresh_enabled": True})
    assert "asp-live-odds-refresh" in _scheduled_odds_jobs(revived)


def test_coverage_alert_fires_inside_ninety_minutes_and_not_before() -> None:
    """The alert exists so nobody has to notice a row in a table of fifteen.

    Inside 90 minutes the scheduled cover has already had its chance, so only
    the recovery ladder is left and after the cutoff nothing helps at all.
    """
    from app.services.quote_coverage import (
        ALERT_AT_RISK, ALERT_CLEAR, ALERT_MISSED, COVERAGE_CHECK_MINUTES,
        STATE_COMPLETE, STATE_MISSED, STATE_QUOTE_EXPECTED, EventCoverage, _alert,
    )

    cutoff = datetime(2026, 9, 9, 23, 0, tzinfo=UTC)

    def event(state: str) -> EventCoverage:
        return EventCoverage(
            game_id="g", matchup="AAA @ BBB", starts_at=cutoff + timedelta(minutes=15),
            cutoff=cutoff, state=state,
            cause=None if state == STATE_COMPLETE else "QUOTA_GUARD_BLOCKED",
            best_quote=None, qualifying=[], out_of_window=[], partial_observations=0,
            next_action_at=None, next_action=None,
        )

    outside = cutoff - timedelta(minutes=COVERAGE_CHECK_MINUTES + 5)
    inside = cutoff - timedelta(minutes=COVERAGE_CHECK_MINUTES - 5)

    assert _alert([event(STATE_QUOTE_EXPECTED)], outside)["severity"] == ALERT_CLEAR
    alert = _alert([event(STATE_QUOTE_EXPECTED)], inside)
    assert alert["severity"] == ALERT_AT_RISK and alert["active"] is True
    assert alert["games"][0]["cause"] == "QUOTA_GUARD_BLOCKED"
    assert alert["threshold_minutes_before_cutoff"] == COVERAGE_CHECK_MINUTES
    # A covered game never raises an alert, however close its cutoff is.
    assert _alert([event(STATE_COMPLETE)], inside)["severity"] == ALERT_CLEAR
    # A missed cutoff outranks an at-risk game.
    assert _alert([event(STATE_MISSED)], inside)["severity"] == ALERT_MISSED


# ---------------------------------------------------------------------------
# the research fallback narrows in code, not in configuration
# ---------------------------------------------------------------------------

def _fallback_settings(settings, **over):
    base = {"sportsgameodds_enabled": True,
            "sportsgameodds_mapping_path": "C:/mapping.json",
            "sportsgameodds_fallback_until": "2026-10-01"}
    base.update(over)
    return settings.model_copy(update=base)


SEPT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def test_the_fallback_serves_mlb_moneyline_and_nothing_else(session, settings) -> None:
    from app.services.sportsgameodds_fallback import (
        REASON_ELIGIBLE, REASON_WRONG_LEAGUE, REASON_WRONG_MARKET, fallback_decision)

    armed = _fallback_settings(settings)
    ok = fallback_decision(session, armed, league_code="MLB", markets=["h2h"], now=SEPT)
    assert ok.eligible and ok.reason == REASON_ELIGIBLE

    # NFL is optional research; it waits for the primary provider.
    nfl = fallback_decision(session, armed, league_code="NFL", markets=["h2h"], now=SEPT)
    assert not nfl.eligible and nfl.reason == REASON_WRONG_LEAGUE

    # Spreads and totals are not what a governed decision prices.
    wide = fallback_decision(session, armed, league_code="MLB",
                             markets=["h2h", "spreads", "totals"], now=SEPT)
    assert not wide.eligible and wide.reason == REASON_WRONG_MARKET


def test_the_fallback_expires_on_its_own(session, settings) -> None:
    """From 1 October the primary is primary again, with nobody remembering."""
    from app.services.sportsgameodds_fallback import REASON_EXPIRED, fallback_decision

    armed = _fallback_settings(settings)
    october = datetime(2026, 10, 1, 0, 1, tzinfo=UTC)
    decision = fallback_decision(session, armed, league_code="MLB", markets=["h2h"], now=october)
    assert not decision.eligible and decision.reason == REASON_EXPIRED


def test_an_unconfigured_window_is_closed_not_open(session, settings) -> None:
    from app.services.sportsgameodds_fallback import REASON_EXPIRED, fallback_decision

    for value in ("", "not-a-date"):
        armed = _fallback_settings(settings, sportsgameodds_fallback_until=value)
        decision = fallback_decision(session, armed, league_code="MLB", markets=["h2h"], now=SEPT)
        assert not decision.eligible and decision.reason == REASON_EXPIRED


def test_a_guessed_mapping_can_never_serve_a_quote(session, settings) -> None:
    from app.services.sportsgameodds_fallback import REASON_UNMAPPED, fallback_decision

    armed = _fallback_settings(settings, sportsgameodds_mapping_path="")
    decision = fallback_decision(session, armed, league_code="MLB", markets=["h2h"], now=SEPT)
    assert not decision.eligible and decision.reason == REASON_UNMAPPED


def test_the_flag_alone_does_not_arm_it(session, settings) -> None:
    from app.services.sportsgameodds_fallback import REASON_DISABLED, fallback_decision

    off = _fallback_settings(settings, sportsgameodds_enabled=False)
    decision = fallback_decision(session, off, league_code="MLB", markets=["h2h"], now=SEPT)
    assert not decision.eligible and decision.reason == REASON_DISABLED


def test_an_uncounted_request_is_charged_the_worst_case(session, settings) -> None:
    """Undercounting an allowance is how an allowance gets exceeded."""
    from app.data_sources.provider_registry import SPORTSGAMEODDS
    from app.models import ProviderRequestLedger
    from app.services.sportsgameodds_fallback import (
        FREE_TIER_OBJECT_ALLOWANCE, REASON_OBJECTS_EXHAUSTED, fallback_decision, objects_used)

    reserved = ProviderRequestLedger(
        provider=SPORTSGAMEODDS, endpoint_key="sgo_mlb_events", status="RESERVED",
        requested_at=SEPT, request_fingerprint="sgo-1",
        controlled_live_request=True, source_mode="RESEARCH_SHADOW", response_metadata={})
    session.add(reserved)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=SPORTSGAMEODDS, endpoint_key="sgo_mlb_events", status="SUCCEEDED",
        requested_at=SEPT, completed_at=SEPT, reservation_id=reserved.id,
        request_fingerprint="sgo-1", records_received=None,  # provider said nothing
        controlled_live_request=True, source_mode="RESEARCH_SHADOW", response_metadata={}))
    session.flush()

    assert objects_used(session, since=date(2026, 9, 1)) == 100  # not zero

    # Enough uncounted requests and the allowance closes, fail-closed.
    for index in range(2, 27):
        held = ProviderRequestLedger(
            provider=SPORTSGAMEODDS, endpoint_key="sgo_mlb_events", status="RESERVED",
            requested_at=SEPT, request_fingerprint=f"sgo-{index}",
            controlled_live_request=True, source_mode="RESEARCH_SHADOW", response_metadata={})
        session.add(held)
        session.flush()
        session.add(ProviderRequestLedger(
            provider=SPORTSGAMEODDS, endpoint_key="sgo_mlb_events", status="SUCCEEDED",
            requested_at=SEPT, completed_at=SEPT, reservation_id=held.id,
            request_fingerprint=f"sgo-{index}", records_received=None,
            controlled_live_request=True, source_mode="RESEARCH_SHADOW", response_metadata={}))
        session.flush()

    assert objects_used(session, since=date(2026, 9, 1)) >= FREE_TIER_OBJECT_ALLOWANCE
    decision = fallback_decision(session, _fallback_settings(settings),
                                 league_code="MLB", markets=["h2h"], now=SEPT)
    assert not decision.eligible and decision.reason == REASON_OBJECTS_EXHAUSTED
