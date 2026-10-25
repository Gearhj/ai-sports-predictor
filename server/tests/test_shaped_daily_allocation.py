"""The daily allocation follows demand; the month and the reserve do not move.

THE FAILURE THIS PINS
---------------------
An even `spendable // days_remaining` gave every day seventeen objects. On
2026-09-18 fourteen NFL objects came off the top of that day and left MLB three
for fifteen games. On the restored schedule the rest of September fitted -- 211
objects needed against 219 spendable -- and still every NFL weekend would have
starved MLB, because quiet days held objects the weekends needed.

The owner's decision (2026-09-18): shape the day by demand, fund MLB, NFL
checkpoints and Show Prep previews, keep the monthly total and the 300 reserve.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import Game, GameStatus, GameTeam, League, Season, Sport, Team
from app.services import nfl_paper_policy as policy
from app.services.provider_allocation import (
    MLB_OBJECTS_PER_GAME, _demand_aware_split, _shaped_daily_total,
)

SATURDAY = date(2026, 9, 19)


@pytest.fixture
def leagues(session: Session):
    out = {}
    for code, sport in (("MLB", "BASEBALL"), ("NFL", "FOOTBALL")):
        s = Sport(code=sport, name=sport.title())
        session.add(s)
        session.flush()
        league = League(sport_id=s.id, code=code, name=code)
        session.add(league)
        session.flush()
        season = Season(league_id=league.id, name="2026",
                        starts_at=datetime(2026, 3, 1, tzinfo=UTC),
                        ends_at=datetime(2027, 2, 1, tzinfo=UTC))
        session.add(season)
        session.flush()
        out[code] = (league, season)
    return {"session": session, **out}


def add_games(ctx, code: str, kickoff: datetime, n: int, *, source_mode: str) -> None:
    session = ctx["session"]
    league, season = ctx[code]
    for index in range(n):
        tag = f"{kickoff:%d%H}{index}"
        home = Team(league_id=league.id, name=f"{code}H{tag}", abbreviation=f"H{tag}")
        away = Team(league_id=league.id, name=f"{code}A{tag}", abbreviation=f"A{tag}")
        session.add_all([home, away])
        session.flush()
        game = Game(league_id=league.id, season_id=season.id, starts_at=kickoff,
                    status=GameStatus.SCHEDULED, source_mode=source_mode)
        session.add(game)
        session.flush()
        session.add_all([GameTeam(game_id=game.id, team_id=home.id, is_home=True),
                         GameTeam(game_id=game.id, team_id=away.id, is_home=False)])
    session.flush()


def shaped(ctx, *, day: date, spendable: int, days_remaining: int, nfl_reserved: int = 0):
    demand = {"reservations": {"sgo_nfl_events": nfl_reserved}}
    return _shaped_daily_total(ctx["session"], day=day, spendable=spendable,
                               days_remaining=days_remaining, demand=demand)


def test_with_no_schedule_it_is_exactly_the_old_even_split(leagues):
    result = shaped(leagues, day=SATURDAY, spendable=219, days_remaining=12)
    assert result["daily_total"] == 219 // 12
    assert result["month_projected_need"] == 0


def test_a_heavy_day_gets_its_need_and_a_quiet_day_gives_way(leagues, monkeypatch):
    monkeypatch.setattr(policy, "ACTIVATED", True)
    # Saturday: fifteen MLB games plus fourteen NFL preview objects.
    add_games(leagues, "MLB", datetime(2026, 9, 19, 23, 0, tzinfo=UTC), 15, source_mode="LIVE")
    # Monday: three MLB games and nothing else.
    add_games(leagues, "MLB", datetime(2026, 9, 21, 23, 0, tzinfo=UTC), 3, source_mode="LIVE")
    saturday = shaped(leagues, day=SATURDAY, spendable=219, days_remaining=12, nfl_reserved=14)
    mlb_need = saturday["today"]["mlb"]
    assert mlb_need == -(-15 * MLB_OBJECTS_PER_GAME // 1)
    assert saturday["daily_total"] >= mlb_need + 14, "both lanes funded in full"
    assert saturday["daily_total"] > saturday["even_split_would_have_been"]
    assert saturday["month_fits"] is True


def test_research_shadow_nfl_games_are_counted_as_future_checkpoints(leagues, monkeypatch):
    """source_mode is provenance, never sport. Filtering NFL on LIVE counted
    every future NFL checkpoint as zero."""

    monkeypatch.setattr(policy, "ACTIVATED", True)
    add_games(leagues, "NFL", datetime(2026, 9, 20, 17, 0, tzinfo=UTC), 14,
              source_mode="RESEARCH_SHADOW")
    friday = shaped(leagues, day=date(2026, 9, 18), spendable=219, days_remaining=13)
    sunday = next(entry for entry in friday["future_days"] if entry["date"] == "2026-09-20")
    assert sunday["nfl"] >= 14


def test_a_month_that_cannot_fund_everything_is_cut_pro_rata(leagues, monkeypatch):
    monkeypatch.setattr(policy, "ACTIVATED", True)
    add_games(leagues, "MLB", datetime(2026, 9, 19, 23, 0, tzinfo=UTC), 15, source_mode="LIVE")
    add_games(leagues, "MLB", datetime(2026, 9, 20, 23, 0, tzinfo=UTC), 15, source_mode="LIVE")
    result = shaped(leagues, day=SATURDAY, spendable=20, days_remaining=3, nfl_reserved=14)
    assert result["basis"] == "PRO_RATA_SHORTFALL"
    assert result["daily_total"] <= 20, "never more than the month can fund"
    assert result["daily_total"] < result["today"]["total"]


def test_the_reserve_is_never_reachable(leagues, monkeypatch):
    monkeypatch.setattr(policy, "ACTIVATED", True)
    add_games(leagues, "MLB", datetime(2026, 9, 19, 23, 0, tzinfo=UTC), 15, source_mode="LIVE")
    assert shaped(leagues, day=SATURDAY, spendable=0, days_remaining=12,
                  nfl_reserved=14)["daily_total"] == 0


# -- the split within a day --------------------------------------------------
def test_when_the_day_funds_both_nfl_is_served_and_mlb_gets_its_need():
    lanes = _demand_aware_split(31, {"reservations": {"sgo_nfl_events": 14}}, mlb_need=17)
    assert lanes["sgo_nfl_events"] == 14
    assert lanes["sgo_mlb_events"] == 17


def test_when_the_day_is_short_mlb_is_not_left_to_absorb_it_alone():
    """The 2026-09-18 shape: 17 objects, 14 NFL wanted, MLB needed 17. NFL-first
    gave MLB 3. Pro-rata gives both lanes the same fraction of their need."""

    lanes = _demand_aware_split(17, {"reservations": {"sgo_nfl_events": 14}}, mlb_need=17)
    assert lanes["sgo_nfl_events"] + lanes["sgo_mlb_events"] == 17
    assert lanes["sgo_mlb_events"] > 3, "no longer the silent remainder"
    nfl_fraction = lanes["sgo_nfl_events"] / 14
    mlb_fraction = lanes["sgo_mlb_events"] / 17
    assert abs(nfl_fraction - mlb_fraction) < 0.15


def test_the_split_never_exceeds_the_day():
    for total in (0, 1, 5, 17, 31):
        lanes = _demand_aware_split(total, {"reservations": {"sgo_nfl_events": 14}}, mlb_need=17)
        assert sum(lanes.values()) == total
