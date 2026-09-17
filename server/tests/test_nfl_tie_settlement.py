"""An NFL moneyline tie refunds the stake.

The NFL paper policy declares ``TIE_TREATMENT = STAKE_REFUNDED_PUSH`` and the
probability contract prices a HOME/AWAY moneyline tie as a push. Settlement now
agrees with both: a tie is a PUSH for BOTH sides, profit and loss is zero and
the payout is the stake.

The rule is stated once, in ``determine_result``, and is reached identically by
legacy settlement and by experiment settlement. MLB cannot tie -- the canonical
final-outcome table forbids equal scores -- so no MLB settlement can change,
and spread and total grading is untouched.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.bankroll.service import SettlementResult, settle_profit
from app.bankroll.settlement import determine_result
from app.models import (
    Game, GameFinalOutcome, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType,
    PositionResult, Season, Sport, Team,
)
from app.services import nfl_paper_policy as policy
from app.probability.adapter import ADMITTED

KICKOFF = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)


@pytest.fixture
def nfl(session):
    sport = Sport(code="FOOTBALL", name="Football")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="NFL", name="NFL")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026", starts_at=KICKOFF - timedelta(days=30),
                    ends_at=KICKOFF + timedelta(days=150))
    home = Team(league_id=league.id, name="Buffalo Bills", abbreviation="BUF")
    away = Team(league_id=league.id, name="Detroit Lions", abbreviation="DET")
    session.add_all([season, home, away])
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=KICKOFF,
                status=GameStatus.FINAL, source_mode="RESEARCH_SHADOW", home_score=20, away_score=20)
    session.add(game)
    session.flush()
    session.add_all([GameTeam(game_id=game.id, team_id=home.id, is_home=True),
                     GameTeam(game_id=game.id, team_id=away.id, is_home=False)])
    moneyline = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="FULL_GAME")
    spread = Market(game_id=game.id, market_type=MarketType.SPREAD, period="FULL_GAME")
    total = Market(game_id=game.id, market_type=MarketType.TOTAL, period="FULL_GAME")
    session.add_all([moneyline, spread, total])
    session.flush()
    outcomes = {
        "home": MarketOutcome(market_id=moneyline.id, outcome_key="home", label="BUF", participant_team_id=home.id),
        "away": MarketOutcome(market_id=moneyline.id, outcome_key="away", label="DET", participant_team_id=away.id),
        "spread_home": MarketOutcome(market_id=spread.id, outcome_key="home:-3.0", label="BUF", participant_team_id=home.id),
        "spread_home_pk": MarketOutcome(market_id=spread.id, outcome_key="home:0.0", label="BUF", participant_team_id=home.id),
        "over_40": MarketOutcome(market_id=total.id, outcome_key="over:40.0", label="Over"),
        "over_39": MarketOutcome(market_id=total.id, outcome_key="over:39.5", label="Over"),
        "under_40": MarketOutcome(market_id=total.id, outcome_key="under:40.0", label="Under"),
    }
    session.add_all(outcomes.values())
    session.flush()
    return {"session": session, "game": game, "moneyline": moneyline, "spread": spread,
            "total": total, "outcomes": outcomes, "league": league, "season": season}


def test_both_moneyline_sides_push_on_a_tie(nfl):
    session, game, market = nfl["session"], nfl["game"], nfl["moneyline"]
    assert (game.home_score, game.away_score) == (20, 20)
    assert determine_result(session, game, market, nfl["outcomes"]["home"]) == PositionResult.PUSH
    assert determine_result(session, game, market, nfl["outcomes"]["away"]) == PositionResult.PUSH


def test_a_push_refunds_the_stake_and_books_no_profit():
    stake, odds = 12.50, 2.15
    profit = settle_profit(stake, odds, SettlementResult(PositionResult.PUSH.value))
    assert profit == 0.0
    assert round(stake + profit, 2) == stake, "payout is the stake"
    # The two rules the tie previously fell into, unchanged.
    assert settle_profit(stake, odds, SettlementResult.WIN) == pytest.approx(14.37)
    assert settle_profit(stake, odds, SettlementResult.LOSS) == -stake


def test_the_settled_rule_is_the_one_the_policy_and_the_contract_declare():
    assert policy.TIE_TREATMENT == "STAKE_REFUNDED_PUSH"
    assert ADMITTED["NFL"]["tie_policy"]
    from app.probability.adapter import MONEYLINE_TIE_SETTLEMENT
    assert MONEYLINE_TIE_SETTLEMENT == "PUSH"


def test_a_decided_game_still_wins_and_loses(nfl):
    session, game, market = nfl["session"], nfl["game"], nfl["moneyline"]
    game.home_score, game.away_score = 24, 20
    session.flush()
    assert determine_result(session, game, market, nfl["outcomes"]["home"]) == PositionResult.WIN
    assert determine_result(session, game, market, nfl["outcomes"]["away"]) == PositionResult.LOSS


def test_spread_and_total_push_behaviour_is_unchanged(nfl):
    session, game = nfl["session"], nfl["game"]
    game.home_score, game.away_score = 23, 20
    session.flush()
    # An exact spread cover is still a push; a pick'em on a decided game is not.
    assert determine_result(session, game, nfl["spread"], nfl["outcomes"]["spread_home"]) == PositionResult.PUSH
    assert determine_result(session, game, nfl["spread"], nfl["outcomes"]["spread_home_pk"]) == PositionResult.WIN
    # Total 43: over 40 wins, over 39.5 wins; on an exact total the push stands.
    assert determine_result(session, game, nfl["total"], nfl["outcomes"]["over_40"]) == PositionResult.WIN
    game.home_score, game.away_score = 20, 20
    session.flush()
    assert determine_result(session, game, nfl["total"], nfl["outcomes"]["over_40"]) == PositionResult.PUSH
    assert determine_result(session, game, nfl["total"], nfl["outcomes"]["under_40"]) == PositionResult.PUSH
    assert determine_result(session, game, nfl["total"], nfl["outcomes"]["over_39"]) == PositionResult.WIN


def test_mlb_cannot_record_a_tie_so_no_mlb_settlement_can_move(session):
    """The canonical MLB final-outcome table refuses equal scores."""

    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026", starts_at=KICKOFF - timedelta(days=30),
                    ends_at=KICKOFF + timedelta(days=150))
    home = Team(league_id=league.id, name="Tampa Bay Rays", abbreviation="TB")
    away = Team(league_id=league.id, name="Athletics", abbreviation="ATH")
    session.add_all([season, home, away])
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=KICKOFF, status=GameStatus.FINAL,
                source_mode="LIVE", home_score=4, away_score=4)
    session.add(game)
    session.flush()
    session.add(GameFinalOutcome(
        game_id=game.id, season_id=season.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_at=KICKOFF, final_at=KICKOFF + timedelta(hours=3), home_score=4, away_score=4,
        outcome_status="FINAL", revision=1, official=True, retrieved_at=KICKOFF + timedelta(hours=3),
        payload_checksum="c" * 64, source_mode="LIVE", identity_checksum="d" * 64,
    ))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_the_rule_lives_in_one_place(nfl):
    """Legacy settlement and experiment settlement both call determine_result."""

    import inspect
    from app.bankroll import settlement as legacy
    from app.services import shadow_experiment_service as experiment

    assert "determine_result(" in inspect.getsource(legacy.settle_completed_positions)
    assert "determine_result(" in inspect.getsource(experiment.ShadowExperimentService._settle_experiment)
    # And the rule itself is stated once.
    source = inspect.getsource(legacy.determine_result)
    assert source.count("PositionResult.PUSH") == 2, "moneyline tie and the handicap zero case"


def test_no_existing_nfl_settlement_is_affected(session):
    """Nothing in this database has settled an NFL tie: none has occurred."""

    from app.models import PositionSettlement

    ties = [row for row in session.scalars(select(PositionSettlement))
            if row.game_home_score is not None and row.game_home_score == row.game_away_score]
    assert ties == []
