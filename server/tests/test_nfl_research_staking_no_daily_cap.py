"""Every qualifying research pick gets the standard position; the day no longer truncates.

WHAT THIS PINS
--------------
On 2026-09-20 an eight-game slate produced seven qualifying picks and only four
positions. The 2% daily ceiling ($20) ran out against the 0.5% position cap
($5), and the three that lost their room -- NO@BAL, PHI@TEN, PIT@NE -- were the
three with the LARGEST Kelly fractions. Which four survived was decided by the
evaluator's loop order, alphabetical by matchup.

For a research ledger of virtual money that is a sampling defect, not risk
management: the picks that reach settlement are selected on spelling, so the
record they build is biased. These tests pin that

  * every qualifying pick now receives the standard $5 research position, with
    no aggregate daily truncation;
  * the per-position cap and the same-game ceiling STILL bind, so nothing about
    an individual pick's size changed;
  * the recommended Kelly size and the constrained-bankroll portfolio are both
    still recorded, as analytical fields, so the capped view stays
    reconstructable and comparable with the pre-change record;
  * flipping the single flag restores the old behaviour exactly, which is what
    makes the flag the whole of the change.

No test here creates a position for a past game: every fixture game is in the
future relative to its own checkpoint.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType,
    OddsSnapshot, OfficialAspPosition, Season, Sport, Sportsbook, Team,
)
from app.services import nfl_paper_decision as nfl
from app.services import nfl_paper_policy as policy

KICKOFF = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
TICK = KICKOFF - timedelta(minutes=24)
ACTIVATED = datetime(2026, 9, 14, 16, 50, tzinfo=UTC)

#: Six games is one more than the old ceiling could fund: 6 x $5 = $30 against
#: a $20 day. Four fit, the fifth and sixth did not.
SLATE = ("ATL", "HOU", "TB", "CHI", "BAL", "TEN")
OLD_CAP_FUNDS = 4


@pytest.fixture
def slate(session: Session, settings):
    """A six-game NFL slate, each game with one priced moneyline outcome."""
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
                      "home": home})

    activate(session, settings, now=ACTIVATED)
    session.commit()
    era = nfl._era(session)
    assert era is not None
    return {"session": session, "era": era, "games": games}


def qualifying(entry, moment) -> dict:
    """A pick that passes every gate, so only staking can stop it."""
    return {"game_id": str(entry["game"].id),
            "matchup": f'{entry["home"]}X at {entry["home"]}',
            "starts_at": KICKOFF.isoformat(), "evaluated_at": moment.isoformat(),
            "outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED",
            "selection": {
                "side": "AWAY", "american_odds": 124,
                "market_outcome_id": entry["outcome"].id,
                "odds_snapshot_id": entry["snapshot"].id,
                "observed_at": (TICK - timedelta(minutes=5)).isoformat(),
                "sportsbook": "sgo:book", "source": "SPORTSGAMEODDS",
                "win_probability": 0.5753, "expected_value": 0.0518,
                "quarter_kelly": 0.0699}}


def run_slate(ctx):
    """Evaluate the whole slate in order, as one checkpoint loop does."""
    out = []
    for i, entry in enumerate(ctx["games"]):
        moment = TICK + timedelta(seconds=i)
        out.append(nfl.record_outcome(ctx["session"], experiment=ctx["era"],
                                      game_id=entry["game"].id,
                                      decision=qualifying(entry, moment),
                                      moment=moment))
    ctx["session"].commit()
    return out


def positions(ctx):
    return ctx["session"].scalar(select(func.count(OfficialAspPosition.id)).where(
        OfficialAspPosition.shadow_experiment_id == ctx["era"].id))


def staked(ctx):
    return float(ctx["session"].scalar(select(func.coalesce(
        func.sum(OfficialAspPosition.stake), 0)).where(
        OfficialAspPosition.shadow_experiment_id == ctx["era"].id)))


# --------------------------------------------------------------------------
# the change itself
# --------------------------------------------------------------------------

def test_the_aggregate_daily_cap_does_not_bind():
    assert policy.AGGREGATE_DAILY_CAP_BINDS is False
    # retained, because the constrained portfolio is computed from it
    assert policy.MAX_DAILY_PERCENTAGE == 0.02


def test_every_qualifying_pick_receives_the_standard_position(slate):
    results = run_slate(slate)

    assert all(r["outcome"] == "QUALIFIED" for r in results)
    assert all(r["position_id"] is not None for r in results)
    assert positions(slate) == len(SLATE) == 6
    # six positions at the flat research size, past the old $20 day
    assert staked(slate) == pytest.approx(30.00)
    assert all(r["staking"]["stake"] == 5.00 for r in results)
    assert all(r["staking"]["bound_by"] == "position_cap" for r in results)
    assert all("daily_cap" not in r["staking"] for r in results)


def test_the_old_ceiling_would_have_funded_only_the_first_four(slate):
    """The defect this change removes, reproduced from the recorded analytics."""
    results = run_slate(slate)
    constrained = [r["staking"]["constrained_bankroll_portfolio"] for r in results]

    funded = [c for c in constrained if not c["would_be_declined"]]
    dropped = [c for c in constrained if c["would_be_declined"]]
    assert len(funded) == OLD_CAP_FUNDS
    assert len(dropped) == len(SLATE) - OLD_CAP_FUNDS
    assert sum(c["stake_under_daily_cap"] for c in constrained) == pytest.approx(20.00)
    # and the survivors are decided purely by position in the loop
    assert [c["would_be_declined"] for c in constrained] == [
        False, False, False, False, True, True]


# --------------------------------------------------------------------------
# what did NOT change
# --------------------------------------------------------------------------

def test_the_per_position_cap_still_binds(slate):
    results = run_slate(slate)
    # quarter-Kelly wanted $69.90; the penalised 0.5% cap still holds it to $5
    for r in results:
        assert r["staking"]["quarter_kelly"] == pytest.approx(69.90, abs=0.01)
        assert r["staking"]["position_cap"] == pytest.approx(5.00)
        assert r["staking"]["stake"] == 5.00


def test_the_same_game_ceiling_is_still_computed_and_still_binding(slate):
    """The same-game ceiling is untouched and is still one of the binding limits."""
    results = run_slate(slate)
    for r in results:
        # 1% of $1,000 = $10 of room on a game with no prior exposure
        assert r["staking"]["same_game_cap"] == pytest.approx(10.00)
    # it is in the binding set, unlike daily_cap
    assert set(results[0]["staking"]) >= {"quarter_kelly", "position_cap", "same_game_cap"}
    assert "daily_cap" not in results[0]["staking"]


def test_a_game_that_already_has_a_position_is_never_given_a_second(slate):
    """Lifting the DAY's ceiling does not let one game take two positions.

    The protection here is idempotency, not the same-game cap: a decision that
    already carries a position is never re-evaluated, so a repeated tick cannot
    stack exposure on one game however much daily room exists.
    """
    entry = slate["games"][0]
    for i in range(3):
        moment = TICK + timedelta(seconds=i)
        nfl.record_outcome(slate["session"], experiment=slate["era"],
                           game_id=entry["game"].id,
                           decision=qualifying(entry, moment), moment=moment)
    slate["session"].commit()
    assert positions(slate) == 1
    assert staked(slate) == pytest.approx(5.00)


def test_the_recommended_kelly_sizing_is_preserved_as_an_analytical_field(slate):
    results = run_slate(slate)
    for r in results:
        s = r["staking"]
        assert s["recommended_kelly_fraction"] == pytest.approx(0.0699)
        assert s["recommended_kelly_stake"] == pytest.approx(69.90, abs=0.01)
        # analytical only: it is recorded, and it did not size the position
        assert s["stake"] == 5.00


def test_the_constrained_portfolio_is_marked_as_not_enforced(slate):
    results = run_slate(slate)
    for r in results:
        c = r["staking"]["constrained_bankroll_portfolio"]
        assert c["basis"] == "ANALYTICAL_ONLY_NOT_ENFORCED"
        assert c["daily_cap_fraction"] == pytest.approx(0.02)
    assert all(r["staking"]["aggregate_daily_cap_binds"] is False for r in results)


# --------------------------------------------------------------------------
# the flag is the whole of the change
# --------------------------------------------------------------------------

def test_restoring_the_flag_restores_the_old_truncation(slate, monkeypatch):
    monkeypatch.setattr(policy, "AGGREGATE_DAILY_CAP_BINDS", True)
    results = run_slate(slate)

    assert positions(slate) == OLD_CAP_FUNDS
    assert staked(slate) == pytest.approx(20.00)
    declined = [r for r in results if r["outcome"] == "DECLINED"]
    assert len(declined) == len(SLATE) - OLD_CAP_FUNDS
    assert all(r["reason"] == "STAKING_OR_EXPOSURE_CAP" for r in declined)
    assert all(r["staking"]["bound_by"] == "daily_cap" for r in declined)
