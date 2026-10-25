"""The 2026-10-16 CLE/TB rain-out, rebuilt row for row (no network, SQLite).

MLB gamePk 849817 was scheduled for Fri 2026-10-16 20:00 ET (after a same-day
move from 18:00), postponed at 21:32 ET, and played on the SAME games row on
Sun 2026-10-18 17:08 ET (TB 3, CLE 1). AISP's paper position (CLE ML +101,
$2.42, LowVig) was placed at 19:43 ET on Friday. The schedule source kept
listing a postponed copy at the original Friday time and a scheduled copy at
the Sunday time for a day afterwards ("flapping").

``build_incident`` creates exactly that history. Times are UTC-aware (the
SQLite convention); 20:00 ET on 10-16 is 00:00Z on 10-17, which is the whole
reason the Eastern date must never be taken from the UTC value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.models import (
    ExperimentDecisionPhase, Game, GameStateObservation, GameStatus, GameTeam, League, Market, MarketOutcome,
    MarketType, OddsSnapshot, OfficialAspPosition, PositionResult, PositionSettlement, PositionStatus,
    ShadowExperiment, ShadowExperimentDecision, Sport, Sportsbook, Team,
)

Z = UTC

#: Friday's occurrence: 20:00 ET on 2026-10-16 == 00:00Z on 2026-10-17.
FRIDAY_START = datetime(2026, 10, 17, 0, 0, tzinfo=Z)
FRIDAY_EARLY_START = datetime(2026, 10, 16, 22, 0, tzinfo=Z)       # 18:00 ET, moved the same day
PLACED_AT = datetime(2026, 10, 16, 23, 43, 48, tzinfo=Z)           # 19:43:48 ET
BASIS_AVAILABLE = datetime(2026, 10, 16, 23, 37, 44, tzinfo=Z)     # 19:37:44 ET (ed59a36d)
POSTPONED_AVAILABLE = datetime(2026, 10, 17, 1, 32, 48, tzinfo=Z)  # 21:32:48 ET (4e0ab04f)
#: Sunday's makeup on the same games row: 17:08 ET.
SUNDAY_START = datetime(2026, 10, 18, 21, 8, tzinfo=Z)
SETTLED_AT = datetime(2026, 10, 19, 0, 21, 29, tzinfo=Z)           # 20:21:29 ET

STAKE = 2.42
DECIMAL = 2.01      # +101


@dataclass
class Incident:
    experiment: ShadowExperiment
    game: Game
    market: Market
    home_outcome: MarketOutcome      # CLE (the pick)
    away_outcome: MarketOutcome      # TB
    sportsbook: Sportsbook
    friday_snapshot: OddsSnapshot
    decision: ShadowExperimentDecision
    position: OfficialAspPosition
    basis_observation: GameStateObservation
    postponed_observation: GameStateObservation
    settlement: PositionSettlement | None
    home_team: Team
    away_team: Team


def gso(session, game: Game, *, available: datetime, scheduled: datetime, status: str,
        home: int | None = None, away: int | None = None, source: str = "MLB_STATS_API",
        mode: str = "LIVE") -> GameStateObservation:
    row = GameStateObservation(
        game_id=game.id, scheduled_at=scheduled, status=status, is_postponed=(status == "postponed"),
        home_score=home, away_score=away, observed_at=available, available_as_of=available,
        source=source, source_mode=mode, source_record_id="849817", payload_checksum=uuid4().hex,
    )
    session.add(row)
    session.flush()
    return row


def mlb_experiment(session, *, version: int = 1, sport: str = "MLB",
                   policy: str = "AISP_PRACTICAL_PREGAME_POLICY_V2_1",
                   era: str | None = "ASP_MLB_V18_CONDITIONAL_VIRTUAL_20260912_V1_R1",
                   starting_bankroll: float = 1000.0) -> ShadowExperiment:
    experiment = ShadowExperiment(
        name=f"TEST {sport} ERA V{version}", version=version,
        started_at=datetime(2026, 9, 12, 12, tzinfo=Z), start_date=date(2026, 9, 12),
        starting_bankroll=starting_bankroll, sport=sport,
        recommendation_policy_version=policy, staking_policy_version="AISP_QUARTER_KELLY_EVIDENCE_V2",
        decision_policy=policy, controlling_engine="MLB_V18_WHOLE_GAME", supported_markets=["MONEYLINE"],
        kelly_fraction=0.25, unit_percentage=0.01, max_position_percentage=0.01,
        max_same_game_percentage=0.02, max_daily_percentage=0.05,
        code_commit="test", code_identity="test", is_active=True, model_era_id=era,
    )
    session.add(experiment)
    session.flush()
    return experiment


def build_incident(session, *, settled: bool = False, flapping: bool = True, played: bool = True,
                   experiment: ShadowExperiment | None = None, history: str = "rainout",
                   slate_date: date = date(2026, 10, 16)) -> Incident:
    """Friday's position and the game's full schedule history.

    ``settled`` adds the legacy LOSS settlement AISP actually wrote (position
    SETTLED/LOSS, -2.42, Sunday's +113 as the "close"); otherwise the position
    is OPEN. ``played=False`` stops the history at the postponement (the
    makeup has not happened yet; the row still reads POSTPONED).

    ``history`` swaps the rain-out for another way a game can move:
    ``delayed`` (a "Delayed Start" finished after midnight ET, same gameDate),
    ``same_day`` (postponed, then made up later the SAME Eastern date),
    ``suspended`` (started Friday, resumed and finished Saturday) and
    ``silent_move`` (re-listed on Sunday with no postponed status ever seen).
    Each ends FINAL with TB 3, CLE 1.
    """

    sport = Sport(code=f"BASEBALL-{uuid4().hex[:6]}", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="Major League Baseball")
    session.add(league)
    session.flush()
    cle = Team(league_id=league.id, name="Cleveland Guardians", abbreviation="CLE")
    tb = Team(league_id=league.id, name="Tampa Bay Rays", abbreviation="TB")
    session.add_all([cle, tb])
    session.flush()
    game = Game(league_id=league.id, starts_at=FRIDAY_EARLY_START, status=GameStatus.SCHEDULED,
                source_mode="LIVE")
    session.add(game)
    session.flush()
    session.add_all([GameTeam(game_id=game.id, team_id=cle.id, is_home=True),
                     GameTeam(game_id=game.id, team_id=tb.id, is_home=False)])
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
    session.add(market)
    session.flush()
    home = MarketOutcome(market_id=market.id, outcome_key="home", label="Cleveland Guardians",
                         participant_team_id=cle.id)
    away = MarketOutcome(market_id=market.id, outcome_key="away", label="Tampa Bay Rays",
                         participant_team_id=tb.id)
    book = Sportsbook(key=f"lowvig-{uuid4().hex[:6]}", name="LowVig", region="us")
    session.add_all([home, away, book])
    session.flush()
    friday_quote = OddsSnapshot(market_outcome_id=home.id, sportsbook_id=book.id,
                                observed_at=datetime(2026, 10, 16, 23, 30, tzinfo=Z),
                                available_as_of=datetime(2026, 10, 16, 23, 30, tzinfo=Z),
                                american_odds=101, decimal_odds=DECIMAL, source="TEST_BOOK", source_mode="LIVE")
    session.add(friday_quote)
    session.flush()

    # The schedule history, in the order AISP saw it.
    gso(session, game, available=datetime(2026, 10, 14, 16, tzinfo=Z), scheduled=FRIDAY_EARLY_START,
        status="scheduled")
    gso(session, game, available=datetime(2026, 10, 16, 4, 1, tzinfo=Z), scheduled=FRIDAY_START,
        status="scheduled")                                        # same-day 18:00 -> 20:00 ET
    basis = gso(session, game, available=BASIS_AVAILABLE, scheduled=FRIDAY_START, status="scheduled")
    postponed = None
    if history != "rainout":
        game.starts_at = FRIDAY_START
        final_start = FRIDAY_START
        if history == "delayed":
            gso(session, game, available=POSTPONED_AVAILABLE, scheduled=FRIDAY_START, status="scheduled")
            gso(session, game, available=datetime(2026, 10, 17, 3, 30, tzinfo=Z), scheduled=FRIDAY_START,
                status="live", home=0, away=0)
        elif history == "same_day":
            # Postponed at 20:10 ET, then re-listed and played at 23:00 ET Friday.
            final_start = datetime(2026, 10, 17, 3, 0, tzinfo=Z)
            postponed = gso(session, game, available=datetime(2026, 10, 17, 0, 10, tzinfo=Z),
                            scheduled=FRIDAY_START, status="postponed")
            gso(session, game, available=datetime(2026, 10, 17, 0, 40, tzinfo=Z), scheduled=final_start,
                status="scheduled")
            gso(session, game, available=datetime(2026, 10, 17, 3, 10, tzinfo=Z), scheduled=final_start,
                status="live", home=0, away=0)
        elif history == "suspended":
            # Started Friday 20:00 ET; "Suspended" reads as scheduled for the resumption Saturday.
            final_start = datetime(2026, 10, 17, 20, 0, tzinfo=Z)
            gso(session, game, available=datetime(2026, 10, 17, 0, 30, tzinfo=Z), scheduled=FRIDAY_START,
                status="live", home=0, away=1)
            gso(session, game, available=datetime(2026, 10, 17, 2, 0, tzinfo=Z), scheduled=final_start,
                status="scheduled")
            gso(session, game, available=datetime(2026, 10, 17, 20, 30, tzinfo=Z), scheduled=final_start,
                status="live", home=1, away=2)
        elif history == "silent_move":
            final_start = SUNDAY_START
            gso(session, game, available=datetime(2026, 10, 17, 4, 0, tzinfo=Z), scheduled=SUNDAY_START,
                status="scheduled")
            gso(session, game, available=datetime(2026, 10, 18, 21, 30, tzinfo=Z), scheduled=SUNDAY_START,
                status="live", home=0, away=1)
        else:
            raise ValueError(history)
        gso(session, game, available=final_start + timedelta(hours=3, minutes=30),
            scheduled=final_start, status="final", home=1, away=3)
        game.starts_at = final_start
        game.status = GameStatus.FINAL
        game.home_score, game.away_score = 1, 3
        played = False
        flapping = False
    else:
        postponed = gso(session, game, available=POSTPONED_AVAILABLE, scheduled=FRIDAY_START, status="postponed")
    if flapping:
        for hour in (4, 5, 9, 10):
            gso(session, game, available=datetime(2026, 10, 17, hour, 54, tzinfo=Z),
                scheduled=datetime(2026, 10, 18, 21, 0, tzinfo=Z), status="scheduled")
            gso(session, game, available=datetime(2026, 10, 17, hour, 24, tzinfo=Z),
                scheduled=FRIDAY_START, status="postponed")
    if history == "rainout":
        game.starts_at = FRIDAY_START
        game.status = GameStatus.POSTPONED
    if played:
        gso(session, game, available=datetime(2026, 10, 18, 20, 0, tzinfo=Z), scheduled=SUNDAY_START,
            status="scheduled")
        gso(session, game, available=datetime(2026, 10, 18, 21, 30, tzinfo=Z), scheduled=SUNDAY_START,
            status="live", home=0, away=1)
        gso(session, game, available=datetime(2026, 10, 19, 0, 15, tzinfo=Z), scheduled=SUNDAY_START,
            status="final", home=1, away=3)
        game.starts_at = SUNDAY_START
        game.status = GameStatus.FINAL
        game.home_score, game.away_score = 1, 3

    experiment = experiment or mlb_experiment(session)
    decision = ShadowExperimentDecision(
        experiment_id=experiment.id, game_id=game.id, slate_date=slate_date,
        phase=ExperimentDecisionPhase.FINAL_PREGAME, sport="BASEBALL_MLB", market="MONEYLINE",
        period="Full game", selection="Cleveland Guardians", sportsbook="LowVig",
        american_odds=101, decimal_odds=DECIMAL, market_outcome_id=home.id, odds_snapshot_id=friday_quote.id,
        quote_observed_at=friday_quote.observed_at, forecast_cutoff=datetime(2026, 10, 16, 23, 0, tzinfo=Z),
        recommended_at=PLACED_AT, model_probability=0.55, recommendation="QUALIFIED_BET",
        recommendation_reason="All qualification gates passed.", qualification_reasons=[],
        evidence_fingerprint=uuid4().hex, source_mode="LIVE",
    )
    session.add(decision)
    session.flush()
    position = OfficialAspPosition(
        market_outcome_id=home.id, odds_snapshot_id=friday_quote.id, placed_at=PLACED_AT,
        stake=STAKE, decimal_odds=DECIMAL,
        result=PositionResult.LOSS if settled else PositionResult.PENDING,
        status=PositionStatus.SETTLED if settled else PositionStatus.OPEN,
        settled_at=SETTLED_AT if settled else None,
        payout=0.0 if settled else None,
        strategy=experiment.staking_policy_version, shadow_experiment_id=experiment.id,
        shadow_decision_id=decision.id, bankroll_before_position=962.74,
        recommendation_policy_version=experiment.recommendation_policy_version,
        staking_policy_version=experiment.staking_policy_version,
    )
    session.add(position)
    session.flush()
    settlement = None
    if settled:
        sunday_close = OddsSnapshot(market_outcome_id=home.id, sportsbook_id=book.id,
                                    observed_at=datetime(2026, 10, 18, 20, 50, tzinfo=Z),
                                    available_as_of=datetime(2026, 10, 18, 20, 50, tzinfo=Z),
                                    american_odds=113, decimal_odds=2.13, source="TEST_BOOK", source_mode="LIVE")
        session.add(sunday_close)
        session.flush()
        settlement = PositionSettlement(
            position_id=position.id, result=PositionResult.LOSS, settled_at=SETTLED_AT, profit_loss=-STAKE,
            game_home_score=1, game_away_score=3,
            settlement_note="Settled deterministically from canonical final score",
            bankroll_before=1000.0, bankroll_after=round(1000.0 - STAKE, 2),
            closing_odds_snapshot_id=sunday_close.id, closing_american_odds=113, closing_decimal_odds=2.13,
            closing_line_value=round((DECIMAL / 2.13 - 1.0) * 100, 3), beat_close=False,
        )
        session.add(settlement)
        session.flush()
    session.commit()
    return Incident(experiment=experiment, game=game, market=market, home_outcome=home, away_outcome=away,
                    sportsbook=book, friday_snapshot=friday_quote, decision=decision, position=position,
                    basis_observation=basis, postponed_observation=postponed, settlement=settlement,
                    home_team=cle, away_team=tb)


def fund(session, settings, experiment: ShadowExperiment) -> Any:
    """Attach the era to a funded $1,000 MLB 2026 account (the production shape)."""

    from app.models import MembershipRole
    from app.services.experiment_accounts import ExperimentAccountService

    accounts = ExperimentAccountService(session, settings)
    account = accounts.ensure_account("MLB", "2026")
    accounts.open_season(account, now=datetime(2026, 9, 12, 12, tzinfo=Z), executed_by="test",
                         tracking_started_on=date(2026, 9, 12))
    accounts.attach(account, experiment, now=datetime(2026, 9, 12, 12, tzinfo=Z), role=MembershipRole.FUNDED_ERA)
    session.commit()
    return account
