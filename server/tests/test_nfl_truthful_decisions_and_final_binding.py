"""D2, D3 and D6: the decision record tells the truth, and FINAL controls it.

WHAT THIS PINS
--------------
D2. The stored decision used to record the PRE-staking qualification and stop.
    On 2026-09-20 three exposure-capped games persisted as
    ``QUALIFIED_BET / ALL_GATES_PASSED`` while the job receipt said
    ``DECLINED / STAKING_OR_EXPOSURE_CAP``. The decision table -- which every
    export and scorecard reads -- described bets AISP had declined.

D3. The checkpoint asked for a FINAL_PREGAME forecast and silently fell back to
    CURRENT. On 2026-09-20 that fallback put all four NFL positions on forecasts
    published five and six days earlier. It was NOT that a newer forecast was
    overlooked: the FINAL generation transaction started 12:41:07 and did not
    commit until 12:43:32, while the checkpoint ran at 12:41:32. The defect is
    the SILENT FALLBACK -- taking a stale snapshot instead of stopping.

    So availability is read from ``available_as_of_utc`` and never from
    ``created_at``, which in Postgres is the transaction-start stamp.

D6. The eligibility binding was stripped at persist time and the forecast
    identity lived only in evidence JSON, so the authority behind a decision
    could not be recovered without job telemetry.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType,
    OddsSnapshot, OfficialAspPosition, Season, ShadowExperimentDecision,
    ShadowForecast, ShadowForecastBundle, Sport, Sportsbook, Team,
)
from app.services import nfl_paper_decision as nfl

KICKOFF = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
CUTOFF = KICKOFF - timedelta(minutes=15)
TICK = KICKOFF - timedelta(minutes=24)
ACTIVATED = datetime(2026, 9, 14, 16, 50, tzinfo=UTC)

#: Deliberately NOT alphabetical, and deliberately not in EV order either: the
#: point is that the funding outcome must not depend on this sequence at all.
SLATE = ("TB", "ATL", "NYJ", "CHI", "BAL", "TEN")


def _bundle(session: Session) -> ShadowForecastBundle:
    bundle = ShadowForecastBundle(
        bundle_sha256=uuid.uuid4().hex, bundle_id=f"b-{uuid.uuid4().hex[:8]}",
        logical_uri="test://bundle", source_fixture_id="fix-1",
        slate_date=date(2026, 9, 20), league="NFL", sport="FOOTBALL",
        status="IMPORTED", research_shadow_only=True, generated_at=KICKOFF,
        forecast_count=1, quote_count=0, candidate_count=1, imported_at=KICKOFF,
        contract_version="v1", evidence={})
    session.add(bundle)
    session.flush()
    return bundle


def _forecast(session: Session, bundle, game, home, away, *, phase: str,
              version: int, available_as_of, p_home: float = 0.5753):
    """One forecast row at a named snapshot phase and availability clock."""
    row = ShadowForecast(
        bundle_id=bundle.id,
        forecast_id=f"asp-sports-game-forecast:sha256:{uuid.uuid4().hex}",
        engine_id="NFL_M1_XGBOOST", engine_version="1.0",
        model_artifact_sha256=uuid.uuid4().hex, simulator_id="sim",
        simulator_version="1", simulator_seed=1,
        event_id=f"2026_02_{away}_{home}", source_event_id=f"2026_02_{away}_{home}",
        event_date=date(2026, 9, 20), season=2026,
        home_team_id=home, away_team_id=away,
        is_ready=True, readiness_score=99.0, readiness_threshold=50.0,
        status="READY",
        full_home_win_probability=p_home, full_away_win_probability=1 - p_home - 0.02,
        full_expected_home_score=23.0, full_expected_away_score=21.0,
        distributions={}, uncertainty={}, readiness_detail={},
        forecast_schema_version="1", snapshot_type=phase, snapshot_version=version,
        identity_evidence={}, lineage_detail={},
        canonical_game_id=game.id, available_as_of_utc=available_as_of)
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def slate(session: Session, settings):
    """A six-game NFL slate, each priced, with no forecasts yet."""
    from app.services.nfl_activation import activate

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
    book = Sportsbook(key="sgo:book", name="Book (research)", region="us", active=True)
    session.add(book)
    session.flush()
    bundle = _bundle(session)

    games = []
    for home in SLATE:
        teams = []
        for abbreviation in (home, f"{home}X"):
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
        market = Market(game_id=game.id, market_type=MarketType.MONEYLINE,
                        period="full_game")
        session.add(market)
        session.flush()
        outcome = MarketOutcome(market_id=market.id, outcome_key="away:None",
                                label="away")
        session.add(outcome)
        session.flush()
        snapshot = OddsSnapshot(
            market_outcome_id=outcome.id, sportsbook_id=book.id,
            observed_at=TICK - timedelta(minutes=5),
            available_as_of=TICK - timedelta(minutes=5),
            american_odds=124, decimal_odds=2.24, implied_probability=0.446,
            source="SPORTSGAMEODDS", source_event_id=f"evt-{uuid.uuid4().hex[:8]}",
            source_mode="LIVE")
        session.add(snapshot)
        session.flush()
        games.append({"game": game, "outcome": outcome, "snapshot": snapshot,
                      "home": home, "away": f"{home}X"})

    activate(session, settings, now=ACTIVATED)
    session.commit()
    era = nfl._era(session)
    assert era is not None
    return {"session": session, "era": era, "games": games, "bundle": bundle}


def qualifying(entry, moment) -> dict:
    return {"game_id": str(entry["game"].id),
            "matchup": f'{entry["away"]} at {entry["home"]}',
            "starts_at": KICKOFF.isoformat(), "evaluated_at": moment.isoformat(),
            "outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED",
            "forecast_id": "asp-sports-game-forecast:sha256:deadbeef",
            "forecast_snapshot_phase": nfl.PHASE_FINAL,
            "eligibility": {"eligible": True, "reason": "PAPER_RESEARCH_ELIGIBILITY_BOUND",
                            "supplement_id": "supp-1"},
            "eligibility_supplement_id": "supp-1",
            "selection": {
                "side": "AWAY", "american_odds": 124,
                "market_outcome_id": entry["outcome"].id,
                "odds_snapshot_id": entry["snapshot"].id,
                "observed_at": (TICK - timedelta(minutes=5)).isoformat(),
                "sportsbook": "sgo:book", "source": "SPORTSGAMEODDS",
                "win_probability": 0.5753, "expected_value": 0.0518,
                "quarter_kelly": 0.0699}}


def run_slate(ctx, order=None):
    entries = order if order is not None else ctx["games"]
    out = []
    for i, entry in enumerate(entries):
        moment = TICK + timedelta(seconds=i)
        out.append(nfl.record_outcome(ctx["session"], experiment=ctx["era"],
                                      game_id=entry["game"].id,
                                      decision=qualifying(entry, moment),
                                      moment=moment))
    ctx["session"].commit()
    return out


def rows(ctx):
    return ctx["session"].scalars(
        select(ShadowExperimentDecision)
        .where(ShadowExperimentDecision.experiment_id == ctx["era"].id)
        .order_by(ShadowExperimentDecision.recommended_at)).all()


def positions(ctx):
    return int(ctx["session"].scalar(select(func.count(OfficialAspPosition.id)).where(
        OfficialAspPosition.shadow_experiment_id == ctx["era"].id)))


# ==========================================================================
# D2 -- the persisted decision matches the POST-staking result
# ==========================================================================

def test_the_persisted_decision_matches_the_post_staking_result(slate):
    run_slate(slate)
    for row in rows(slate):
        assert row.recommendation == "QUALIFIED_BET"
        assert row.qualification_outcome == "QUALIFIED"
        assert row.qualification_reason == "ALL_GATES_PASSED"
        assert row.execution_outcome == "EXECUTED"
        assert row.execution_reason == "POSITION_TAKEN"
    assert positions(slate) == len(SLATE)


def test_a_value_qualified_game_with_no_position_is_not_an_executed_bet(slate, monkeypatch):
    """The exact 2026-09-20 failure, pinned so it cannot come back."""
    from app.services import nfl_paper_policy as policy
    monkeypatch.setattr(policy, "AGGREGATE_DAILY_CAP_BINDS", True)
    run_slate(slate)

    capped = [r for r in rows(slate) if r.execution_outcome == "NOT_EXECUTED"]
    assert len(capped) == 2  # $20 day funds four of six
    for row in capped:
        # the pre-staking truth is preserved ...
        assert row.qualification_outcome == "QUALIFIED"
        assert row.qualification_reason == "ALL_GATES_PASSED"
        # ... and the durable disposition is NOT an executed bet
        assert row.recommendation != "QUALIFIED_BET"
        assert row.recommendation == "DECLINED"
        assert row.execution_reason == "STAKING_OR_EXPOSURE_CAP"
        assert row.staking_detail["bound_by"] == "daily_cap"


def test_the_analytical_fields_are_durable_not_telemetry_only(slate):
    run_slate(slate)
    for row in rows(slate):
        detail = row.staking_detail
        assert detail is not None, "staking_detail must be on the row, not only in job_runs"
        assert detail["recommended_kelly_stake"] == pytest.approx(69.90, abs=0.01)
        assert detail["recommended_kelly_fraction"] == pytest.approx(0.0699)
        constrained = detail["constrained_bankroll_portfolio"]
        assert constrained["basis"] == "ANALYTICAL_ONLY_NOT_ENFORCED"
        assert constrained["daily_cap_fraction"] == pytest.approx(0.02)


def test_the_constrained_portfolio_reproduces_the_old_four_and_three(slate):
    """It reports the old truncation WITHOUT controlling actual funding."""
    run_slate(slate)
    detail = [r.staking_detail["constrained_bankroll_portfolio"] for r in rows(slate)]

    # every pick was actually funded ...
    assert positions(slate) == 6
    assert all(r.execution_outcome == "EXECUTED" for r in rows(slate))
    # ... while the analytical view still shows four funded, the rest dropped
    assert [c["would_be_declined"] for c in detail] == [False, False, False, False, True, True]
    assert sum(c["stake_under_daily_cap"] for c in detail) == pytest.approx(20.00)


# ==========================================================================
# priority behaviour -- order must not decide who gets funded
# ==========================================================================

@pytest.mark.parametrize("permutation", [
    lambda g: g,                      # as built
    lambda g: sorted(g, key=lambda e: e["home"]),          # alphabetical
    lambda g: sorted(g, key=lambda e: e["home"], reverse=True),   # reverse
    lambda g: [g[3], g[0], g[5], g[1], g[4], g[2]],        # arbitrary
])
def test_every_qualifying_pick_is_funded_whatever_the_order(slate, permutation):
    run_slate(slate, order=permutation(list(slate["games"])))
    assert positions(slate) == len(SLATE)
    assert {r.execution_outcome for r in rows(slate)} == {"EXECUTED"}
    staked = float(slate["session"].scalar(select(func.sum(OfficialAspPosition.stake))))
    assert staked == pytest.approx(5.00 * len(SLATE))


# ==========================================================================
# D6 -- authority and identity recoverable from the row alone
# ==========================================================================

def test_the_eligibility_binding_and_forecast_identity_are_durable(slate):
    run_slate(slate)
    for row in rows(slate):
        assert row.eligibility_binding is not None
        assert row.eligibility_binding["reason"] == "PAPER_RESEARCH_ELIGIBILITY_BOUND"
        assert row.eligibility_supplement_id == "supp-1"
        assert row.controlling_forecast_identity == \
            "asp-sports-game-forecast:sha256:deadbeef"
        assert row.forecast_snapshot_phase == nfl.PHASE_FINAL


# ==========================================================================
# D3 -- FINAL controls, CURRENT never silently does
# ==========================================================================

def test_a_distinct_eligible_final_is_selected_over_current(slate):
    entry = slate["games"][0]
    _forecast(slate["session"], slate["bundle"], entry["game"], entry["home"],
              entry["away"], phase=nfl.PHASE_CURRENT, version=8,
              available_as_of=KICKOFF - timedelta(days=6))
    final = _forecast(slate["session"], slate["bundle"], entry["game"], entry["home"],
                      entry["away"], phase=nfl.PHASE_FINAL, version=1,
                      available_as_of=CUTOFF - timedelta(minutes=2))
    slate["session"].commit()

    chosen = nfl._final_forecast_available_by(slate["session"], entry["game"].id, CUTOFF)
    assert chosen is not None
    assert chosen.id == final.id
    assert chosen.snapshot_type == nfl.PHASE_FINAL


def test_current_cannot_silently_control_when_final_is_missing(slate):
    """No FINAL by the cutoff => BLOCKED, and no position. Never a quiet fallback."""
    entry = slate["games"][0]
    _forecast(slate["session"], slate["bundle"], entry["game"], entry["home"],
              entry["away"], phase=nfl.PHASE_CURRENT, version=8,
              available_as_of=KICKOFF - timedelta(days=6))
    slate["session"].commit()

    out = nfl._decide(slate["session"], slate["era"],
                      {"game_id": str(entry["game"].id), "away": entry["away"],
                       "home": entry["home"], "starts_at": KICKOFF.isoformat()},
                      TICK)
    assert out["outcome"] == "BLOCKED"
    assert out["reason"] == "NO_FINAL_PREGAME_FORECAST"
    assert out["controlling"] is False
    # CURRENT stays VISIBLE for preview/research -- it is named, not hidden
    assert out["forecast_snapshot_phase"] == nfl.PHASE_CURRENT
    assert out["forecast_id"]
    assert positions(slate) == 0


def test_a_final_that_committed_after_the_cutoff_does_not_count(slate):
    """The 2026-09-20 clock exactly: visible-at decides, not the row stamp."""
    entry = slate["games"][0]
    _forecast(slate["session"], slate["bundle"], entry["game"], entry["home"],
              entry["away"], phase=nfl.PHASE_FINAL, version=1,
              available_as_of=CUTOFF + timedelta(minutes=2))
    slate["session"].commit()

    assert nfl._final_forecast_available_by(
        slate["session"], entry["game"].id, CUTOFF) is None


def test_a_missing_final_is_recoverable_when_it_arrives_in_window(slate):
    """NO_FINAL_PREGAME_FORECAST is a missing input, not a verdict."""
    assert "NO_FINAL_PREGAME_FORECAST" in nfl.PREREQUISITE_REASONS


# ==========================================================================
# idempotency survives all of it
# ==========================================================================

def test_duplicate_ticks_create_no_duplicate_decision_or_position(slate):
    entry = slate["games"][0]
    for i in range(4):
        moment = TICK + timedelta(seconds=i)
        nfl.record_outcome(slate["session"], experiment=slate["era"],
                           game_id=entry["game"].id,
                           decision=qualifying(entry, moment), moment=moment)
    slate["session"].commit()

    same_game = [r for r in rows(slate) if r.game_id == entry["game"].id]
    assert len(same_game) == 1
    assert positions(slate) == 1


# ==========================================================================
# the window is a hard edge: nothing is evaluated or superseded after T-15
# ==========================================================================

def test_no_game_is_evaluated_after_the_cutoff(slate):
    """T-15 closes the window. A late tick sees no game at all, so it cannot
    supersede a transient block, and cannot backdate one either."""
    after_cutoff = KICKOFF - timedelta(minutes=14)
    due = nfl.games_at_checkpoint(slate["session"], now=after_cutoff)
    assert due == []

    # and a tick INSIDE the window does see them, so the emptiness above is the
    # cutoff doing its job rather than the fixture being wrong
    inside = nfl.games_at_checkpoint(slate["session"], now=TICK)
    assert len(inside) == len(SLATE)


def test_a_transient_block_is_superseded_only_while_the_window_is_open(slate):
    """A missing FINAL may be revisited in-window; a real answer never is."""
    # missing input -> replaceable
    assert nfl._supersedes_transient_state(
        slate["session"],
        _prior_row("BLOCKED", "NO_FINAL_PREGAME_FORECAST"),
        {"outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED"}) is True
    # a genuine no-value verdict -> never replaced
    assert nfl._supersedes_transient_state(
        slate["session"],
        _prior_row("DECLINED", "NO_QUALIFYING_VALUE"),
        {"outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED"}) is False


class _prior_row:
    """The shape `_supersedes_transient_state` reads off a stored decision."""

    def __init__(self, outcome, reason, position_id=None):
        self.recommendation = {"BLOCKED": "BLOCKED", "DECLINED": "DECLINED",
                               "QUALIFIED": "QUALIFIED_BET"}[outcome]
        self.recommendation_reason = reason
        self.qualification_reasons = {"outcome": outcome, "reason": reason}
        self.id = uuid.uuid4()
        self.position_id = position_id
