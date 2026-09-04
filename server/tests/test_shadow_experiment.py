"""Forward $1,000 virtual-bankroll shadow experiment.

Focused tests: immutable start authority, exact-price binding, the final-pregame
cutoff, "no quote / not QUALIFIED_BET => no position", exposure caps, idempotent
freezing and settlement, bankroll before/after, push/void, closing-line value,
no historical backfill, and the empty/populated scorecard states. Everything is
virtual; no provider call is made.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update

from app.jobs import seed_demo_data
from app.markets.odds import american_to_decimal
from app.models import (
    ExperimentDecisionPhase,
    Forecast,
    Game,
    GameFeatureSnapshot,
    GameRefreshState,
    GameStatus,
    GameTeam,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    OfficialAspPosition,
    PositionResult,
    PositionSettlement,
    PositionStatus,
    ShadowExperiment,
    ShadowExperimentDecision,
    Sportsbook,
    WeatherSnapshot,
)
from app.schemas.api import GameOpportunity, OpportunitySelection, OpportunitySummary
from app.services.read_service import ReadService
from app.services.shadow_experiment_service import (
    FINAL_PREGAME_CUTOFF_MINUTES,
    EXPERIMENT_NAME,
    RECOMMENDATION_POLICY_VERSION,
    STAKING_POLICY_VERSION,
    ShadowExperimentService,
)


async def _live_slate(session, settings):
    """Seed the demo slate as a fresh LIVE forward slate three hours out.

    Odds snapshots are append-only, so they are never mutated: the moneyline
    edge is provided by inserting a new labelled synthetic-test price. The board
    itself is bypassed in these tests (its recommendation state is injected),
    while the exact-price / staking / settlement logic runs against real rows.
    """
    zone = ZoneInfo(settings.display_timezone)
    # Seed a completed prior date so the demo fixture's point-in-time forecast
    # cutoff is never in the real wall clock's future. The test still exercises
    # a prospective slate relative to its explicit simulated ``now`` below.
    event_date = datetime.now(zone).date() - timedelta(days=1)
    now = datetime.combine(event_date, time(12, 0), tzinfo=zone).astimezone(UTC)
    # First pitch 26 minutes out: the FINAL_PREGAME capture (cutoff - 10m)
    # then falls one minute after ``now`` and inside the final freeze window.
    start = now + timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES + 11)
    await seed_demo_data(session, settings, event_date)
    for model in (Game, GameFeatureSnapshot, WeatherSnapshot, GameRefreshState):
        session.execute(update(model).values(source_mode="LIVE"))
    session.execute(update(Game).values(starts_at=start, status=GameStatus.SCHEDULED))
    evidence_time = now - timedelta(minutes=30)
    session.execute(update(Forecast).values(
        source_mode="LIVE",
        generated_at=evidence_time,
        available_as_of=evidence_time,
        published_at=evidence_time,
    ))
    session.commit()
    games = list(session.scalars(select(Game).order_by(Game.starts_at)))
    return games, event_date, now, start


def _add_home_value(session, game, *, observed_at, american=+300):
    """Add a real (labelled synthetic-test) high home moneyline price → clear edge."""
    home = session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(True)))
    market = session.scalar(select(Market).where(Market.game_id == game.id, Market.market_type == MarketType.MONEYLINE))
    outcome = session.scalar(select(MarketOutcome).where(
        MarketOutcome.market_id == market.id, MarketOutcome.participant_team_id == home.team_id))
    sportsbook_id = session.scalar(select(OddsSnapshot.sportsbook_id).where(
        OddsSnapshot.market_outcome_id == outcome.id,
    ).limit(1))
    sportsbook = session.get(Sportsbook, sportsbook_id)
    snapshot = OddsSnapshot(
        market_outcome_id=outcome.id,
        sportsbook_id=sportsbook.id,
        observed_at=observed_at, available_as_of=observed_at,
        american_odds=american, decimal_odds=american_to_decimal(american),
        source="SYNTHETIC_TEST_BOOK", source_mode="LIVE",
    )
    session.add(snapshot)
    session.commit()
    return outcome, snapshot, sportsbook


def _opportunities(monkeypatch, evidence_by_game, state="QUALIFIED_BET"):
    """Stub the read board with exact OpportunitySelection-shaped evidence."""

    def fake_daily_opportunities(self, games, cards, forecasts):
        rows = []
        for game in games:
            evidence = evidence_by_game.get(game.id)
            selection = None
            recommendation = state if evidence else "NO_BET"
            if evidence:
                outcome, snapshot, sportsbook = evidence
                selection = OpportunitySelection(
                    recommendation=state,
                    market_type="MONEYLINE",
                    period="Full game",
                    selection=outcome.label,
                    sportsbook=sportsbook.name,
                    sportsbook_id=str(sportsbook.id),
                    market_outcome_id=str(outcome.id),
                    odds_snapshot_id=str(snapshot.id),
                    american_odds=snapshot.american_odds,
                    decimal_odds=float(snapshot.decimal_odds),
                    model_probability=65.0,
                    no_vig_probability=25.0,
                    raw_edge=40.0,
                    uncertainty_adjusted_edge=40.0,
                    expected_value=160.0,
                    quote_observed_at=snapshot.observed_at,
                    quote_age_seconds=0,
                    model_probability_fraction=0.65,
                    no_vig_probability_fraction=0.25,
                    edge_fraction=0.40,
                    expected_value_fraction=1.60,
                )
            rows.append(GameOpportunity(
                game_id=str(game.id),
                recommendation_state=recommendation,
                recommendation_reason=(
                    "All qualification gates passed."
                    if recommendation == "QUALIFIED_BET"
                    else "No complete qualified opportunity."
                ),
                qualification_reasons=(
                    [] if recommendation == "QUALIFIED_BET"
                    else ["Qualification policy not satisfied"]
                ),
                best=selection,
            ))
        return rows, OpportunitySummary()

    monkeypatch.setattr(ReadService, "_daily_opportunities", fake_daily_opportunities)


def _start(service, event_date, now):
    experiment = service.ensure_started(
        start_date=event_date,
        now=now,
        code_commit="abc123",
    )
    service.session.commit()
    return experiment


def _append_then_position(service, event_date, now, start):
    # The FINAL_PREGAME phase is frozen only inside the final window
    # (FINAL_FREEZE_WINDOW_MINUTES before the cutoff); earlier snapshots are
    # the MORNING phase. Capture at cutoff - 10 minutes.
    captured = service.freeze(
        event_date,
        phase=ExperimentDecisionPhase.FINAL_PREGAME,
        now=start - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES + 10),
    )
    positioned = service.freeze(
        event_date,
        phase=ExperimentDecisionPhase.FINAL_PREGAME,
        now=start - timedelta(minutes=10),
    )
    return captured, positioned


def _aware(value):
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# -- immutable experiment authority ------------------------------------------


@pytest.mark.asyncio
async def test_experiment_creation_and_immutable_start(session, settings):
    zone = ZoneInfo(settings.display_timezone)
    event_date = datetime.now(zone).date()
    now = datetime.combine(event_date, time(12, 0), tzinfo=zone).astimezone(UTC)
    service = ShadowExperimentService(session, settings)

    with pytest.raises(ValueError, match="historical backfill is not permitted"):
        service.ensure_started(
            start_date=event_date - timedelta(days=1),
            now=now,
            code_commit="abc123",
        )

    experiment = _start(service, event_date, now)
    assert experiment.name == EXPERIMENT_NAME
    assert float(experiment.starting_bankroll) == 1000.0
    assert experiment.version == 1
    assert experiment.controlling_engine == "AISP_BASELINE"
    assert experiment.recommendation_policy_version == RECOMMENDATION_POLICY_VERSION
    assert experiment.staking_policy_version == STAKING_POLICY_VERSION
    assert experiment.kelly_fraction == settings.shadow_kelly_fraction
    assert experiment.max_position_percentage == settings.shadow_max_position_percentage
    assert experiment.max_same_game_percentage == settings.shadow_max_same_game_percentage
    assert experiment.max_daily_percentage == settings.shadow_max_daily_percentage
    assert experiment.code_commit == "abc123"

    # Idempotent: no field in the immutable start authority is rewritten.
    again = service.ensure_started(
        start_date=event_date + timedelta(days=1),
        now=now + timedelta(days=1),
        code_commit="different",
    )
    assert again.id == experiment.id
    assert _aware(again.started_at) == now
    assert again.start_date == event_date
    assert again.code_commit == "abc123"
    assert len(list(session.scalars(select(ShadowExperiment)))) == 1


# -- exact bindings, cutoff, qualification, duplicate and exposure controls --


@pytest.mark.asyncio
async def test_final_pregame_qualified_creates_exact_bound_canonical_position(
    session, settings, monkeypatch,
):
    games, event_date, now, start = await _live_slate(session, settings)
    evidence = _add_home_value(session, games[0], observed_at=now)
    _opportunities(monkeypatch, {games[0].id: evidence})
    service = ShadowExperimentService(session, settings)
    experiment = _start(service, event_date, now)

    captured, positioned = _append_then_position(service, event_date, now, start)
    assert captured["frozen"] == len(games)
    assert captured["primary"] == 0
    assert positioned["primary"] == 1

    decision = session.scalar(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.game_id == games[0].id,
        ShadowExperimentDecision.recommendation == "QUALIFIED_BET",
    ))
    position = session.scalar(select(OfficialAspPosition).where(
        OfficialAspPosition.shadow_experiment_id == experiment.id,
    ))
    outcome, snapshot, _ = evidence
    assert decision is not None and position is not None
    assert _aware(decision.recommended_at) == now
    assert _aware(decision.quote_observed_at) <= start - timedelta(minutes=15)
    assert _aware(decision.forecast_cutoff) <= start - timedelta(minutes=15)
    assert position.shadow_decision_id == decision.id
    assert position.forecast_id == decision.forecast_id
    assert position.market_outcome_id == outcome.id == decision.market_outcome_id
    assert position.odds_snapshot_id == snapshot.id == decision.odds_snapshot_id
    assert float(position.decimal_odds) == float(snapshot.decimal_odds)
    assert float(position.bankroll_before_position) == 1000.0
    assert position.recommendation_policy_version == RECOMMENDATION_POLICY_VERSION
    assert position.staking_policy_version == STAKING_POLICY_VERSION
    assert position.status == PositionStatus.OPEN
    assert position.result == PositionResult.PENDING
    assert 0 < float(position.stake) <= 1000 * settings.shadow_max_position_percentage

    # Re-entering the freeze window cannot create a duplicate canonical row.
    repeated = service.freeze(
        event_date,
        phase=ExperimentDecisionPhase.FINAL_PREGAME,
        now=start - timedelta(minutes=5),
    )
    assert repeated["primary"] == 0
    assert len(list(session.scalars(select(OfficialAspPosition).where(
        OfficialAspPosition.shadow_experiment_id == experiment.id,
    )))) == 1


@pytest.mark.asyncio
async def test_decision_reasons_bind_the_exact_moneyline_alternate(
    session, settings,
):
    games, event_date, now, _ = await _live_slate(session, settings)
    outcome, snapshot, sportsbook = _add_home_value(
        session, games[0], observed_at=now,
    )
    forecast = ReadService(session, settings)._latest_forecast(games[0].id)
    assert forecast is not None

    stale_extreme_total = OpportunitySelection(
        recommendation="EXTREME_DISAGREEMENT",
        market_type="TOTAL",
        period="Full game",
        selection="Over 8.5",
        is_stale=True,
        uncertainty_adjusted_edge=35.0,
    )
    exact_moneyline = OpportunitySelection(
        recommendation="QUALIFIED_BET",
        market_type="MONEYLINE",
        period="Full game",
        selection=outcome.label,
        sportsbook=sportsbook.name,
        sportsbook_id=str(sportsbook.id),
        market_outcome_id=str(outcome.id),
        odds_snapshot_id=str(snapshot.id),
        american_odds=snapshot.american_odds,
        decimal_odds=float(snapshot.decimal_odds),
        model_probability=65.0,
        no_vig_probability=25.0,
        raw_edge=40.0,
        uncertainty_adjusted_edge=40.0,
        expected_value=160.0,
        quote_observed_at=snapshot.observed_at,
        model_probability_fraction=0.65,
        no_vig_probability_fraction=0.25,
        edge_fraction=0.40,
        expected_value_fraction=1.60,
    )
    opportunity = GameOpportunity(
        game_id=str(games[0].id),
        recommendation_state="EXTREME_DISAGREEMENT",
        recommendation_reason="The top TOTAL candidate is stale and extreme.",
        qualification_reasons=[
            "Latest quote is stale",
            "Extreme model-market disagreement",
        ],
        best=stale_extreme_total,
        alternates=[exact_moneyline],
    )
    service = ShadowExperimentService(session, settings)
    experiment = _start(service, event_date, now)
    decision = service._append_decision(
        experiment,
        games[0],
        forecast,
        ExperimentDecisionPhase.FINAL_PREGAME,
        opportunity,
        now,
    )
    session.commit()

    assert decision is not None
    assert decision.market_outcome_id == outcome.id
    assert decision.odds_snapshot_id == snapshot.id
    assert decision.recommendation == "QUALIFIED_BET"
    assert decision.selection == outcome.label
    assert "Exact MONEYLINE selection" in decision.recommendation_reason
    assert "QUALIFIED_BET" in decision.recommendation_reason
    assert "Latest quote is stale" not in decision.qualification_reasons
    assert "Extreme model-market disagreement" not in decision.qualification_reasons


@pytest.mark.asyncio
async def test_non_qualified_and_missing_quote_create_no_position(
    session, settings, monkeypatch,
):
    games, event_date, now, start = await _live_slate(session, settings)
    evidence = _add_home_value(session, games[0], observed_at=now)
    _opportunities(monkeypatch, {games[0].id: evidence}, state="PROVISIONAL_EDGE")
    service = ShadowExperimentService(session, settings)
    _start(service, event_date, now)

    _append_then_position(service, event_date, now, start)
    provisional = session.scalar(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.game_id == games[0].id,
        ShadowExperimentDecision.recommendation == "PROVISIONAL_EDGE",
    ))
    assert provisional is not None
    assert list(session.scalars(select(OfficialAspPosition))) == []

    # The remaining games have no governed quote, so their snapshot is an
    # explicit NO_BET with no invented price identity.
    missing = session.scalar(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.game_id == games[1].id,
        ShadowExperimentDecision.recommendation == "NO_BET",
    ))
    assert missing is not None
    assert missing.odds_snapshot_id is None
    assert missing.market_outcome_id is None


@pytest.mark.asyncio
async def test_final_pregame_cutoff_blocks_late_or_post_start_substitution(
    session, settings, monkeypatch,
):
    games, event_date, now, start = await _live_slate(session, settings)
    evidence = _add_home_value(session, games[0], observed_at=now)
    _opportunities(monkeypatch, {games[0].id: evidence})
    service = ShadowExperimentService(session, settings)
    _start(service, event_date, now)

    # Without a pre-cutoff decision, neither the late window nor post-start can
    # append evidence or create a position.
    service.freeze(
        event_date,
        phase=ExperimentDecisionPhase.FINAL_PREGAME,
        now=start - timedelta(minutes=5),
    )
    service.freeze(
        event_date,
        phase=ExperimentDecisionPhase.FINAL_PREGAME,
        now=start + timedelta(seconds=1),
    )
    assert list(session.scalars(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME,
    ))) == []
    assert list(session.scalars(select(OfficialAspPosition))) == []


@pytest.mark.asyncio
async def test_duplicate_prevention_and_configured_exposure_caps(session, settings, monkeypatch):
    games, event_date, now, start = await _live_slate(session, settings)
    evidence = {
        game.id: _add_home_value(session, game, observed_at=now, american=+300)
        for game in games
    }
    _opportunities(monkeypatch, evidence)
    service = ShadowExperimentService(session, settings)
    experiment = _start(service, event_date, now)

    captured, positioned = _append_then_position(service, event_date, now, start)
    assert captured["frozen"] == len(games)
    assert positioned["primary"] == len(games)
    positions = list(session.scalars(select(OfficialAspPosition).where(
        OfficialAspPosition.shadow_experiment_id == experiment.id,
    )))
    assert len(positions) == len(games)
    assert len({position.shadow_decision_id for position in positions}) == len(positions)
    assert all(
        float(position.stake) <= 1000.0 * settings.shadow_max_position_percentage + 1e-6
        for position in positions
    )
    assert sum(float(position.stake) for position in positions) <= (
        1000.0 * settings.shadow_max_daily_percentage + 1e-6
    )
    assert all(
        float(position.stake) <= 1000.0 * settings.shadow_max_same_game_percentage + 1e-6
        for position in positions
    )

    service.freeze(
        event_date,
        phase=ExperimentDecisionPhase.FINAL_PREGAME,
        now=start - timedelta(minutes=2),
    )
    assert len(list(session.scalars(select(OfficialAspPosition).where(
        OfficialAspPosition.shadow_experiment_id == experiment.id,
    )))) == len(games)


# -- canonical settlement, bankroll, cancellation, and same-book CLV ---------

async def _frozen_primary(session, settings, monkeypatch, *, american=+300):
    games, event_date, now, start = await _live_slate(session, settings)
    evidence = _add_home_value(
        session,
        games[0],
        observed_at=now,
        american=american,
    )
    _opportunities(monkeypatch, {games[0].id: evidence})
    service = ShadowExperimentService(session, settings)
    _start(service, event_date, now)
    _append_then_position(service, event_date, now, start)
    position = session.scalar(select(OfficialAspPosition).where(
        OfficialAspPosition.market_outcome_id == evidence[0].id,
        OfficialAspPosition.shadow_experiment_id.is_not(None),
    ))
    decision = session.get(ShadowExperimentDecision, position.shadow_decision_id) if position else None
    return service, games[0], position, decision, event_date, start, evidence[1]


@pytest.mark.asyncio
async def test_win_settlement_updates_bankroll_idempotently(session, settings, monkeypatch):
    service, game, position, _, event_date, start, _ = await _frozen_primary(
        session, settings, monkeypatch,
    )
    assert position is not None
    game.status = GameStatus.FINAL
    game.home_score, game.away_score = 6, 3  # home (our pick) wins
    session.commit()

    result = service.settle(event_date, now=start + timedelta(hours=4))
    settlement = session.scalar(select(PositionSettlement).where(
        PositionSettlement.position_id == position.id,
    ))
    assert result["settled"] == 1
    assert settlement is not None and settlement.result == PositionResult.WIN
    assert float(settlement.profit_loss) > 0
    assert float(settlement.bankroll_before) == 1000.0
    assert float(settlement.bankroll_after) == pytest.approx(1000.0 + float(settlement.profit_loss), abs=0.01)
    assert position.status == PositionStatus.SETTLED

    # Idempotent: a rerun never double-settles.
    rerun = service.settle(event_date, now=start + timedelta(hours=5))
    assert rerun["settled"] == 0
    assert len(list(session.scalars(select(PositionSettlement).where(
        PositionSettlement.position_id == position.id,
    )))) == 1
    assert ReadService(session, settings).bankroll().current_bankroll == pytest.approx(float(settlement.bankroll_after), abs=0.01)
    performance = ReadService(session, settings).performance().betting
    assert performance is not None
    assert performance.has_settled is True
    assert performance.roi is not None


@pytest.mark.asyncio
async def test_loss_settlement_debits_bankroll(session, settings, monkeypatch):
    service, game, position, _, event_date, start, _ = await _frozen_primary(
        session, settings, monkeypatch,
    )
    game.status = GameStatus.FINAL
    game.home_score, game.away_score = 2, 7  # home (our pick) loses
    session.commit()
    service.settle(event_date, now=start + timedelta(hours=4))
    settlement = session.scalar(select(PositionSettlement).where(
        PositionSettlement.position_id == position.id,
    ))
    assert settlement.result == PositionResult.LOSS
    assert float(settlement.profit_loss) == pytest.approx(-float(position.stake), abs=0.01)
    assert float(settlement.bankroll_after) == pytest.approx(
        1000.0 - float(position.stake), abs=0.01,
    )


@pytest.mark.asyncio
async def test_cancelled_game_voids_once_without_changing_bankroll(
    session, settings, monkeypatch,
):
    service, game, position, _, event_date, start, _ = await _frozen_primary(
        session, settings, monkeypatch,
    )
    game.status = GameStatus.CANCELED
    game.home_score = game.away_score = None
    session.commit()

    service.settle(event_date, now=start + timedelta(hours=1))
    settlement = session.scalar(select(PositionSettlement).where(
        PositionSettlement.position_id == position.id,
    ))
    assert settlement.result == PositionResult.VOID
    assert float(settlement.profit_loss) == 0.0
    assert float(settlement.bankroll_before) == float(settlement.bankroll_after) == 1000.0
    assert position.status == PositionStatus.VOID
    assert "CANCELLED" in settlement.settlement_note

    service.settle(event_date, now=start + timedelta(hours=2))
    assert len(list(session.scalars(select(PositionSettlement).where(
        PositionSettlement.position_id == position.id,
    )))) == 1


@pytest.mark.asyncio
async def test_closing_line_value_calculation(session, settings, monkeypatch):
    service, game, position, _, event_date, start, recommendation = await _frozen_primary(
        session, settings, monkeypatch, american=+300,
    )
    # A shorter closing price than our locked +300 → we beat the close (positive CLV).
    same_book_close = OddsSnapshot(
        market_outcome_id=position.market_outcome_id,
        sportsbook_id=recommendation.sportsbook_id,
        observed_at=start - timedelta(minutes=1), available_as_of=start - timedelta(minutes=1),
        american_odds=+150, decimal_odds=american_to_decimal(+150), source="SYNTHETIC_TEST_BOOK", source_mode="LIVE",
    )
    other_book = Sportsbook(key="different-test-book", name="Different Test Book", region="us")
    session.add_all([same_book_close, other_book])
    session.flush()
    different_book_close = OddsSnapshot(
        market_outcome_id=position.market_outcome_id,
        sportsbook_id=other_book.id,
        observed_at=start - timedelta(seconds=30),
        available_as_of=start - timedelta(seconds=30),
        american_odds=+110,
        decimal_odds=american_to_decimal(+110),
        source="OTHER_SYNTHETIC_TEST_BOOK",
        source_mode="LIVE",
    )
    session.add(different_book_close)
    game.status = GameStatus.FINAL
    game.home_score, game.away_score = 5, 1
    session.commit()
    service.settle(event_date, now=start + timedelta(hours=4))
    settlement = session.scalar(select(PositionSettlement).where(
        PositionSettlement.position_id == position.id,
    ))
    assert settlement.closing_odds_snapshot_id == same_book_close.id
    assert settlement.closing_odds_snapshot_id != different_book_close.id
    assert settlement.closing_american_odds == 150
    assert settlement.closing_line_value is not None and settlement.closing_line_value > 0
    assert settlement.implied_probability_movement is not None
    assert settlement.implied_probability_movement > 0
    assert settlement.beat_close is True


@pytest.mark.asyncio
async def test_ledger_bankroll_uses_settlement_order_not_placement_order(
    session, settings, monkeypatch,
):
    games, event_date, now, start = await _live_slate(session, settings)
    first_evidence = _add_home_value(session, games[0], observed_at=now)
    second_evidence = _add_home_value(
        session, games[1], observed_at=now + timedelta(minutes=1),
    )
    service = ShadowExperimentService(session, settings)
    experiment = _start(service, event_date, now)

    _opportunities(monkeypatch, {games[0].id: first_evidence})
    _append_then_position(service, event_date, now, start)
    _opportunities(monkeypatch, {games[1].id: second_evidence})
    service.freeze(
        event_date,
        phase=ExperimentDecisionPhase.FINAL_PREGAME,
        now=now + timedelta(minutes=1),
    )
    service.freeze(
        event_date,
        phase=ExperimentDecisionPhase.FINAL_PREGAME,
        now=start - timedelta(minutes=9),
    )

    first_position = session.scalar(select(OfficialAspPosition).where(
        OfficialAspPosition.market_outcome_id == first_evidence[0].id,
    ))
    second_position = session.scalar(select(OfficialAspPosition).where(
        OfficialAspPosition.market_outcome_id == second_evidence[0].id,
    ))
    assert first_position is not None and second_position is not None
    assert _aware(first_position.placed_at) < _aware(second_position.placed_at)

    # The later-placed position settles first and wins.
    games[1].status = GameStatus.FINAL
    games[1].home_score, games[1].away_score = 6, 2
    session.commit()
    assert service.settle(event_date, now=start + timedelta(hours=3))["settled"] == 1

    # The earlier-placed position settles second and loses.
    games[0].status = GameStatus.FINAL
    games[0].home_score, games[0].away_score = 2, 6
    session.commit()
    assert service.settle(event_date, now=start + timedelta(hours=4))["settled"] == 1

    first_settlement = session.scalar(select(PositionSettlement).where(
        PositionSettlement.position_id == first_position.id,
    ))
    second_settlement = session.scalar(select(PositionSettlement).where(
        PositionSettlement.position_id == second_position.id,
    ))
    assert first_settlement is not None and second_settlement is not None
    assert _aware(second_settlement.settled_at) < _aware(first_settlement.settled_at)

    expected = 1000.0 + sum(
        float(item.profit_loss) for item in (first_settlement, second_settlement)
    )
    ledger = service.ledger_view(experiment)
    assert ledger["current"] == pytest.approx(expected, abs=0.01)
    assert ledger["betting"]["current_bankroll"] == pytest.approx(expected, abs=0.01)
    assert ledger["equity"][-1]["bankroll"] == pytest.approx(expected, abs=0.01)


# -- no backfill, no-bet day, and honest collecting read states --------------

@pytest.mark.asyncio
async def test_no_historical_backfill(session, settings, monkeypatch):
    games, event_date, now, start = await _live_slate(session, settings)
    evidence = _add_home_value(session, games[0], observed_at=now)
    _opportunities(monkeypatch, {games[0].id: evidence})
    service = ShadowExperimentService(session, settings)
    _start(service, event_date, now)
    # Freezing for a PAST date must not fabricate decisions for earlier games.
    past = event_date - timedelta(days=7)
    result = service.freeze(past, phase=ExperimentDecisionPhase.FINAL_PREGAME, now=now)
    assert result.get("frozen", 0) == 0
    assert list(session.scalars(select(ShadowExperimentDecision).where(ShadowExperimentDecision.slate_date == past))) == []
    assert list(session.scalars(select(OfficialAspPosition))) == []


@pytest.mark.asyncio
async def test_no_bet_day_and_collecting_states(session, settings, monkeypatch):
    games, event_date, now, start = await _live_slate(session, settings)
    _opportunities(monkeypatch, {})
    service = ShadowExperimentService(session, settings)

    read = ReadService(session, settings)
    assert read.bankroll().experiment_status == "NOT_STARTED"
    assert read.performance().betting is None
    _start(service, event_date, now)
    service.freeze(event_date, phase=ExperimentDecisionPhase.FINAL_PREGAME, now=now)

    bankroll = ReadService(session, settings).bankroll()
    assert bankroll.experiment_status == "ACTIVE"
    assert bankroll.starting_bankroll == bankroll.current_bankroll == 1000.0
    assert bankroll.positions_count == 0
    assert bankroll.no_bet_days == 1
    assert len(bankroll.daily) == 1
    assert bankroll.daily[0].date == event_date.isoformat()
    assert bankroll.daily[0].no_bet is True
    assert bankroll.daily[0].positions == 0
    assert bankroll.daily[0].no_bet_reasons
    assert bankroll.betting is not None
    assert bankroll.betting.has_settled is False
    assert bankroll.betting.roi is None

    performance = ReadService(session, settings).performance()
    assert performance.betting is not None
    assert performance.betting.has_settled is False
    assert performance.betting.roi is None
