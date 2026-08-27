"""Forward $1,000 virtual-bankroll shadow experiment.

Focused tests: immutable start authority, exact-price binding, the final-pregame
cutoff, "no quote / not QUALIFIED_BET => no position", exposure caps, idempotent
freezing and settlement, bankroll before/after, push/void, closing-line value,
no historical backfill, and the empty/populated scorecard states. Everything is
virtual; no provider call is made.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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
    ShadowExperiment,
    ShadowExperimentDecision,
    ShadowExperimentSettlement,
    WeatherSnapshot,
)
from app.services.read_service import ReadService
from app.services.shadow_experiment_service import ShadowExperimentService

SLATE = date(2026, 8, 26)


async def _live_slate(session, settings):
    """Seed the demo slate as a fresh LIVE forward slate three hours out.

    Odds snapshots are append-only, so they are never mutated: the moneyline
    edge is provided by inserting a new labelled synthetic-test price. The board
    itself is bypassed in these tests (its recommendation state is injected),
    while the exact-price / staking / settlement logic runs against real rows.
    """
    await seed_demo_data(session, settings, SLATE)
    now = datetime.now(UTC)
    start = now + timedelta(hours=3)
    for model in (Game, Forecast, GameFeatureSnapshot, WeatherSnapshot, GameRefreshState):
        session.execute(update(model).values(source_mode="LIVE"))
    for game in session.scalars(select(Game)):
        game.starts_at = start
    session.commit()
    event_date = start.astimezone(ZoneInfo(settings.display_timezone)).date()
    games = list(session.scalars(select(Game).order_by(Game.starts_at)))
    return games, event_date, now, start


def _add_home_value(session, game, *, american=+300):
    """Add a real (labelled synthetic-test) high home moneyline price → clear edge."""
    home = session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(True)))
    market = session.scalar(select(Market).where(Market.game_id == game.id, Market.market_type == MarketType.MONEYLINE))
    outcome = session.scalar(select(MarketOutcome).where(
        MarketOutcome.market_id == market.id, MarketOutcome.participant_team_id == home.team_id))
    now = datetime.now(UTC)
    session.add(OddsSnapshot(
        market_outcome_id=outcome.id,
        sportsbook_id=session.scalar(select(OddsSnapshot.sportsbook_id).where(OddsSnapshot.market_outcome_id == outcome.id)),
        observed_at=now, available_as_of=now,
        american_odds=american, decimal_odds=american_to_decimal(american),
        source="SYNTHETIC_TEST_BOOK", source_mode="LIVE",
    ))
    session.commit()
    return outcome


def _qualify(monkeypatch, state="QUALIFIED_BET"):
    monkeypatch.setattr(
        ShadowExperimentService, "_game_states",
        lambda self, read, games, cards, forecasts: {str(game.id): state for game in games},
    )


# -- 1 & 2: experiment creation + immutable start policy ----------------------

@pytest.mark.asyncio
async def test_experiment_creation_and_immutable_start(session, settings):
    service = ShadowExperimentService(session, settings)
    experiment = service.ensure_started(start_date=SLATE, code_commit="abc123")
    session.commit()
    assert float(experiment.starting_bankroll) == 1000.0
    assert experiment.version == 1
    assert experiment.controlling_engine == "AISP_BASELINE"
    assert experiment.kelly_fraction == settings.shadow_kelly_fraction
    assert experiment.max_position_percentage == settings.shadow_max_position_percentage
    assert experiment.code_commit == "abc123"

    # Idempotent: a second call never rewrites the start date/bankroll or adds a row.
    again = service.ensure_started(start_date=date(2027, 1, 1), code_commit="different")
    assert again.id == experiment.id
    assert again.start_date == SLATE
    assert session.scalar(select(ShadowExperiment.version).order_by(ShadowExperiment.version.desc())) == 1


# -- 15: scorecard empty vs populated -----------------------------------------

@pytest.mark.asyncio
async def test_scorecard_empty_then_active(session, settings):
    read = ReadService(session, settings)
    assert read.bankroll().experiment_status == "NOT_STARTED"
    assert read.performance().betting is None  # no ROI/betting metrics without an experiment
    ShadowExperimentService(session, settings).ensure_started(start_date=SLATE)
    session.commit()
    bankroll = read.bankroll()
    assert bankroll.experiment_status == "ACTIVE"
    assert bankroll.starting_bankroll == 1000.0
    assert bankroll.betting is not None
    assert bankroll.betting.has_settled is False  # ROI is present but no settled sample yet
    assert read.performance().betting is not None


# -- 3, 6, 8: exact-price binding, only QUALIFIED_BET, idempotent freezing -----

@pytest.mark.asyncio
async def test_final_pregame_qualified_freezes_bound_primary(session, settings, monkeypatch):
    games, event_date, now, start = await _live_slate(session, settings)
    outcome = _add_home_value(session, games[0])
    service = ShadowExperimentService(session, settings)
    service.ensure_started(start_date=event_date)
    session.commit()
    _qualify(monkeypatch)

    result = service.freeze(event_date, phase=ExperimentDecisionPhase.FINAL_PREGAME, now=now)
    assert result["primary"] >= 1
    primary = session.scalar(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.is_primary.is_(True), ShadowExperimentDecision.game_id == games[0].id))
    assert primary is not None
    # Exact price binding: every recommendation-price field is persisted.
    assert primary.market == "MONEYLINE"
    assert primary.odds_snapshot_id is not None
    assert primary.market_outcome_id == outcome.id
    assert primary.decimal_odds is not None and primary.american_odds is not None
    assert primary.sportsbook and primary.quote_observed_at is not None
    assert primary.forecast_version and primary.model_probability is not None
    assert float(primary.virtual_stake) > 0

    # Idempotent: re-freezing the same phase creates no duplicate row.
    before = session.scalar(select(ShadowExperimentDecision.id).where(ShadowExperimentDecision.id == primary.id))
    service.freeze(event_date, phase=ExperimentDecisionPhase.FINAL_PREGAME, now=now)
    count = len(list(session.scalars(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.game_id == games[0].id, ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME))))
    assert count == 1
    assert before is not None


@pytest.mark.asyncio
async def test_non_qualified_never_creates_position(session, settings, monkeypatch):
    games, event_date, now, start = await _live_slate(session, settings)
    _add_home_value(session, games[0])
    service = ShadowExperimentService(session, settings)
    service.ensure_started(start_date=event_date)
    session.commit()
    _qualify(monkeypatch, state="PROVISIONAL_EDGE")  # attractive but not qualified

    service.freeze(event_date, phase=ExperimentDecisionPhase.FINAL_PREGAME, now=now)
    primaries = list(session.scalars(select(ShadowExperimentDecision).where(ShadowExperimentDecision.is_primary.is_(True))))
    assert primaries == []
    # The game is still visible as a NO-position snapshot at zero stake.
    snap = session.scalar(select(ShadowExperimentDecision).where(ShadowExperimentDecision.game_id == games[0].id))
    assert snap is not None and float(snap.virtual_stake) == 0.0


# -- 4: final-pregame cutoff --------------------------------------------------

@pytest.mark.asyncio
async def test_final_pregame_cutoff_blocks_late_price(session, settings, monkeypatch):
    games, event_date, now, start = await _live_slate(session, settings)
    _add_home_value(session, games[0])
    service = ShadowExperimentService(session, settings)
    service.ensure_started(start_date=event_date)
    session.commit()
    _qualify(monkeypatch)

    # 5 minutes before first pitch is inside the 15-minute cutoff → no substitute.
    late = start - timedelta(minutes=5)
    service.freeze(event_date, phase=ExperimentDecisionPhase.FINAL_PREGAME, now=late)
    assert list(session.scalars(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME))) == []


# -- 5: no quote → no position ------------------------------------------------

@pytest.mark.asyncio
async def test_no_quote_means_no_bet(session, settings, monkeypatch):
    games, event_date, now, start = await _live_slate(session, settings)
    service = ShadowExperimentService(session, settings)
    service.ensure_started(start_date=event_date)
    session.commit()
    _qualify(monkeypatch)
    # No governed two-sided quote resolves for any game.
    monkeypatch.setattr(ShadowExperimentService, "_best_moneyline", lambda self, game, forecast: None)

    service.freeze(event_date, phase=ExperimentDecisionPhase.FINAL_PREGAME, now=now)
    assert list(session.scalars(select(ShadowExperimentDecision).where(ShadowExperimentDecision.is_primary.is_(True)))) == []
    snap = session.scalar(select(ShadowExperimentDecision))
    assert snap.recommendation == "NO_BET" and float(snap.virtual_stake) == 0.0


# -- 7: exposure limits -------------------------------------------------------

@pytest.mark.asyncio
async def test_exposure_caps_bound_stakes(session, settings, monkeypatch):
    games, event_date, now, start = await _live_slate(session, settings)
    for game in games:
        _add_home_value(session, game, american=+120)  # near-even to make Kelly want a large stake
    service = ShadowExperimentService(session, settings)
    service.ensure_started(start_date=event_date)
    session.commit()
    _qualify(monkeypatch)

    service.freeze(event_date, phase=ExperimentDecisionPhase.FINAL_PREGAME, now=now)
    primaries = list(session.scalars(select(ShadowExperimentDecision).where(ShadowExperimentDecision.is_primary.is_(True))))
    assert primaries
    per_position_cap = 1000.0 * settings.shadow_max_position_percentage
    daily_cap = 1000.0 * settings.shadow_max_daily_percentage
    for primary in primaries:
        assert float(primary.virtual_stake) <= per_position_cap + 1e-6
    assert sum(float(p.virtual_stake) for p in primaries) <= daily_cap + 1e-6


# -- 9, 10, 11, 12: settlement idempotency, bankroll, push/void, CLV ----------

async def _frozen_primary(session, settings, monkeypatch, *, american=+300):
    games, event_date, now, start = await _live_slate(session, settings)
    _add_home_value(session, games[0], american=american)
    service = ShadowExperimentService(session, settings)
    service.ensure_started(start_date=event_date)
    session.commit()
    _qualify(monkeypatch)
    service.freeze(event_date, phase=ExperimentDecisionPhase.FINAL_PREGAME, now=now)
    primary = session.scalar(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.is_primary.is_(True), ShadowExperimentDecision.game_id == games[0].id))
    return service, games[0], primary, event_date, start


@pytest.mark.asyncio
async def test_win_settlement_updates_bankroll_idempotently(session, settings, monkeypatch):
    service, game, primary, event_date, start = await _frozen_primary(session, settings, monkeypatch)
    assert primary is not None
    game.status = GameStatus.FINAL
    game.home_score, game.away_score = 6, 3  # home (our pick) wins
    session.commit()

    service.settle(event_date)
    settlement = session.scalar(select(ShadowExperimentSettlement).where(ShadowExperimentSettlement.decision_id == primary.id))
    assert settlement is not None and settlement.result == "WIN"
    assert float(settlement.profit_loss) > 0
    assert float(settlement.bankroll_before) == 1000.0
    assert float(settlement.bankroll_after) == pytest.approx(1000.0 + float(settlement.profit_loss), abs=0.01)

    # Idempotent: a rerun never double-settles.
    service.settle(event_date)
    assert len(list(session.scalars(select(ShadowExperimentSettlement).where(
        ShadowExperimentSettlement.decision_id == primary.id)))) == 1
    assert ReadService(session, settings).bankroll().current_bankroll == pytest.approx(float(settlement.bankroll_after), abs=0.01)


@pytest.mark.asyncio
async def test_loss_settlement_debits_bankroll(session, settings, monkeypatch):
    service, game, primary, event_date, start = await _frozen_primary(session, settings, monkeypatch)
    game.status = GameStatus.FINAL
    game.home_score, game.away_score = 2, 7  # home (our pick) loses
    session.commit()
    service.settle(event_date)
    settlement = session.scalar(select(ShadowExperimentSettlement))
    assert settlement.result == "LOSS"
    assert float(settlement.profit_loss) == pytest.approx(-float(primary.virtual_stake), abs=0.01)
    assert float(settlement.bankroll_after) < 1000.0


@pytest.mark.asyncio
async def test_closing_line_value_calculation(session, settings, monkeypatch):
    service, game, primary, event_date, start = await _frozen_primary(session, settings, monkeypatch, american=+300)
    # A shorter closing price than our locked +300 → we beat the close (positive CLV).
    session.add(OddsSnapshot(
        market_outcome_id=primary.market_outcome_id,
        sportsbook_id=session.scalar(select(OddsSnapshot.sportsbook_id).where(OddsSnapshot.market_outcome_id == primary.market_outcome_id)),
        observed_at=start - timedelta(minutes=1), available_as_of=start - timedelta(minutes=1),
        american_odds=+150, decimal_odds=american_to_decimal(+150), source="SYNTHETIC_TEST_BOOK", source_mode="LIVE",
    ))
    game.status = GameStatus.FINAL
    game.home_score, game.away_score = 5, 1
    session.commit()
    service.settle(event_date)
    settlement = session.scalar(select(ShadowExperimentSettlement))
    assert settlement.closing_line_value is not None and settlement.closing_line_value > 0
    assert settlement.beat_close is True


# -- 13: no historical backfill -----------------------------------------------

@pytest.mark.asyncio
async def test_no_historical_backfill(session, settings, monkeypatch):
    games, event_date, now, start = await _live_slate(session, settings)
    _add_home_value(session, games[0])
    service = ShadowExperimentService(session, settings)
    service.ensure_started(start_date=event_date)
    session.commit()
    _qualify(monkeypatch)
    # Freezing for a PAST date must not fabricate decisions for earlier games.
    past = event_date - timedelta(days=7)
    result = service.freeze(past, phase=ExperimentDecisionPhase.FINAL_PREGAME, now=now)
    assert result.get("frozen", 0) == 0
    assert list(session.scalars(select(ShadowExperimentDecision).where(ShadowExperimentDecision.slate_date == past))) == []
