"""Retaining totals and run lines from the SportsGameOdds event object.

THE BUG THESE PIN
-----------------
The request never filtered markets. ``leagueID``, ``limit``, ``startsAfter``,
``startsBefore``, ``finalized``, ``oddsPresent`` -- no bet-type parameter at all,
so the provider always returned the whole event including its totals and
spreads. The parser matched two hardcoded moneyline oddIDs and dropped the other
~1,270 odds on the floor. That is a parser omission, not a request filter, an
unavailable book market, or an account restriction, and fixing it costs zero
additional requests and zero additional objects because billing is per event.

WHAT MUST NOT BE CONFUSED WITH THE GAME TOTAL
---------------------------------------------
``betTypeID: "ou"`` alone is not enough, and the real payload proves it:

  points-all-game-ou-over                 combined game total   <- this one
  points-home-game-ou-over                HOME TEAM total runs
  points-ALEC_BOHM_1_MLB-game-ou-over     a player prop
  points-all-1ix5-ou-over                 first five innings
  batting_hits-all-game-ou-over           hits, not runs

Only ``statEntityID`` separates the first three and only ``periodID`` separates
the fourth, so both are checked explicitly rather than inferred.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.data_sources.sgo_parser import (
    REJECT_BOOK_ONE_SIDED,
    REJECT_LINE_DISAGREES,
    REJECT_LINE_MISSING,
    REJECT_PRICE_INVALID,
    REJECT_UNAVAILABLE,
    SgoPayloadError,
    classify_odd,
    parse_main_markets,
)

NOW = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)
START = datetime(2026, 9, 13, 23, 20, tzinfo=UTC)
BOOKS = ("fanduel", "draftkings")


def book_entry(odds: str, *, line: str | None = None, field: str = "overUnder",
               available: bool = True, updated: str = "2026-09-13T19:50:00.000Z"):
    entry = {"odds": odds, "lastUpdatedAt": updated, "available": available}
    if line is not None:
        entry[field] = line
    return entry


def odd(odd_id: str, opposing: str, *, stat: str, entity: str, period: str,
        bet_type: str, side: str, books: dict | None = None):
    return {
        "oddID": odd_id, "opposingOddID": opposing, "statID": stat,
        "statEntityID": entity, "periodID": period, "betTypeID": bet_type,
        "sideID": side, "cancelled": False,
        "byBookmaker": books if books is not None else {},
    }


def event(odds: dict, *, started: bool = False, league: str = "MLB"):
    return {
        "eventID": "evt-1", "leagueID": league,
        "status": {"startsAt": START.isoformat().replace("+00:00", "Z"),
                   "started": started, "live": False, "completed": False,
                   "ended": False, "cancelled": False},
        "teams": {"home": {"teamID": "SF_MLB", "names": {"long": "San Francisco Giants"}},
                  "away": {"teamID": "SD_MLB", "names": {"long": "San Diego Padres"}}},
        "odds": odds,
    }


def totals_pair(line: str = "7.5", over: str = "+105", under: str = "-125",
                books: tuple[str, ...] = BOOKS, **kw):
    return {
        "points-all-game-ou-over": odd(
            "points-all-game-ou-over", "points-all-game-ou-under",
            stat="points", entity="all", period="game", bet_type="ou", side="over",
            books={b: book_entry(over, line=line, **kw) for b in books}),
        "points-all-game-ou-under": odd(
            "points-all-game-ou-under", "points-all-game-ou-over",
            stat="points", entity="all", period="game", bet_type="ou", side="under",
            books={b: book_entry(under, line=line, **kw) for b in books}),
    }


def moneyline_pair(home: str = "-130", away: str = "+110"):
    return {
        "points-home-game-ml-home": odd(
            "points-home-game-ml-home", "points-away-game-ml-away",
            stat="points", entity="home", period="game", bet_type="ml", side="home",
            books={b: book_entry(home) for b in BOOKS}),
        "points-away-game-ml-away": odd(
            "points-away-game-ml-away", "points-home-game-ml-home",
            stat="points", entity="away", period="game", bet_type="ml", side="away",
            books={b: book_entry(away) for b in BOOKS}),
    }


def spread_pair(home_line: str = "-1.5", away_line: str = "+1.5"):
    return {
        "points-home-game-sp-home": odd(
            "points-home-game-sp-home", "points-away-game-sp-away",
            stat="points", entity="home", period="game", bet_type="sp", side="home",
            books={b: book_entry("+140", line=home_line, field="spread") for b in BOOKS}),
        "points-away-game-sp-away": odd(
            "points-away-game-sp-away", "points-home-game-sp-home",
            stat="points", entity="away", period="game", bet_type="sp", side="away",
            books={b: book_entry("-165", line=away_line, field="spread") for b in BOOKS}),
    }


def parse(odds: dict, **kw):
    payload = {"data": [event(odds, **kw)]}
    return parse_main_markets(payload, received_at=NOW, now=NOW, league_id="MLB")


class TestAllThreeMainMarketsAreRetained:
    def test_retains_moneyline_totals_and_run_line_from_one_event(self):
        result = parse({**moneyline_pair(), **totals_pair(), **spread_pair()})
        assert result.by_market() == {"h2h": 2, "totals": 2, "spreads": 2}

    def test_a_totals_quote_carries_both_sides_with_the_line(self):
        result = parse(totals_pair(line="7.5", over="+105", under="-125"))
        quote = next(q for q in result.quotes if q.bookmaker_key == "fanduel")
        over, under = quote.sides
        assert (over.label, over.point, over.american) == ("Over", 7.5, 105)
        assert (under.label, under.point, under.american) == ("Under", 7.5, -125)
        assert quote.stat_entity_id == "all"
        assert quote.period_id == "game"

    def test_a_run_line_keeps_its_signed_handicap_per_side(self):
        result = parse(spread_pair(home_line="-1.5", away_line="+1.5"))
        quote = result.quotes[0]
        assert {side.point for side in quote.sides} == {-1.5, 1.5}

    def test_the_quote_is_only_as_fresh_as_its_stalest_half(self):
        odds = totals_pair()
        odds["points-all-game-ou-under"]["byBookmaker"]["fanduel"]["lastUpdatedAt"] = \
            "2026-09-13T19:10:00.000Z"
        result = parse(odds)
        quote = next(q for q in result.quotes if q.bookmaker_key == "fanduel")
        assert quote.freshness_at == datetime(2026, 9, 13, 19, 10, tzinfo=UTC)


class TestTheLineComesFromTheBookmakerNotTheConsensus:
    def test_each_book_keeps_its_own_line(self):
        """The real payload has FanDuel at 8.5 while six books sit at 8.0.

        Storing the provider's odd-level consensus against FanDuel's price would
        record a quote no book ever offered.
        """

        odds = totals_pair(line="8.0")
        for side in ("over", "under"):
            odds[f"points-all-game-ou-{side}"]["byBookmaker"]["fanduel"]["overUnder"] = "8.5"
        # And a consensus on the odd itself, which must be ignored.
        odds["points-all-game-ou-over"]["bookOverUnder"] = "8.0"
        odds["points-all-game-ou-over"]["fairOverUnder"] = "8.0"

        result = parse(odds)
        lines = {q.bookmaker_key: q.sides[0].point for q in result.quotes}
        assert lines == {"fanduel": 8.5, "draftkings": 8.0}

    def test_a_book_whose_sides_disagree_is_refused(self):
        odds = totals_pair(line="8.0")
        odds["points-all-game-ou-under"]["byBookmaker"]["fanduel"]["overUnder"] = "9.0"
        result = parse(odds)
        assert {q.bookmaker_key for q in result.quotes} == {"draftkings"}
        assert any(r["reason"] == REJECT_LINE_DISAGREES for r in result.rejected)

    def test_a_run_line_whose_sides_are_not_opposites_is_refused(self):
        result = parse(spread_pair(home_line="-1.5", away_line="+2.5"))
        assert result.quotes == []
        assert any(r["reason"] == REJECT_LINE_DISAGREES for r in result.rejected)

    def test_a_totals_side_with_no_line_is_refused(self):
        odds = totals_pair(line="7.5")
        del odds["points-all-game-ou-over"]["byBookmaker"]["fanduel"]["overUnder"]
        result = parse(odds)
        assert {q.bookmaker_key for q in result.quotes} == {"draftkings"}
        assert any(r["reason"] == REJECT_LINE_MISSING for r in result.rejected)


class TestEntityAndPeriodAreVerified:
    def test_a_team_total_is_never_read_as_the_game_total(self):
        """betTypeID "ou" with statEntityID "home" is one team's runs."""

        odds = {
            "points-all-game-ou-over": odd(
                "points-all-game-ou-over", "points-all-game-ou-under",
                stat="points", entity="home", period="game", bet_type="ou",
                side="over", books={b: book_entry("+105", line="4.5") for b in BOOKS}),
            "points-all-game-ou-under": odd(
                "points-all-game-ou-under", "points-all-game-ou-over",
                stat="points", entity="home", period="game", bet_type="ou",
                side="under", books={b: book_entry("-125", line="4.5") for b in BOOKS}),
        }
        result = parse(odds)
        assert result.quotes == []

    def test_a_first_five_innings_total_is_not_a_full_game_total(self):
        odds = {
            "points-all-game-ou-over": odd(
                "points-all-game-ou-over", "points-all-game-ou-under",
                stat="points", entity="all", period="1ix5", bet_type="ou",
                side="over", books={b: book_entry("+105", line="4.5") for b in BOOKS}),
            "points-all-game-ou-under": odd(
                "points-all-game-ou-under", "points-all-game-ou-over",
                stat="points", entity="all", period="1ix5", bet_type="ou",
                side="under", books={b: book_entry("-125", line="4.5") for b in BOOKS}),
        }
        assert parse(odds).quotes == []

    def test_a_non_runs_stat_is_not_a_runs_total(self):
        odds = {
            "points-all-game-ou-over": odd(
                "points-all-game-ou-over", "points-all-game-ou-under",
                stat="batting_hits", entity="all", period="game", bet_type="ou",
                side="over", books={b: book_entry("+105", line="8.5") for b in BOOKS}),
            "points-all-game-ou-under": odd(
                "points-all-game-ou-under", "points-all-game-ou-over",
                stat="batting_hits", entity="all", period="game", bet_type="ou",
                side="under", books={b: book_entry("-125", line="8.5") for b in BOOKS}),
        }
        assert parse(odds).quotes == []


class TestOrdinaryQuoteHygieneStillApplies:
    def test_a_book_that_priced_one_side_is_refused(self):
        odds = totals_pair()
        del odds["points-all-game-ou-under"]["byBookmaker"]["fanduel"]
        result = parse(odds)
        assert {q.bookmaker_key for q in result.quotes} == {"draftkings"}
        assert any(r["reason"] == REJECT_BOOK_ONE_SIDED for r in result.rejected)

    def test_an_unavailable_price_is_refused(self):
        odds = totals_pair()
        odds["points-all-game-ou-over"]["byBookmaker"]["fanduel"]["available"] = False
        result = parse(odds)
        assert {q.bookmaker_key for q in result.quotes} == {"draftkings"}
        assert any(r["reason"] == REJECT_UNAVAILABLE for r in result.rejected)

    def test_an_invalid_american_price_is_refused(self):
        odds = totals_pair(over="+50")
        result = parse(odds)
        assert result.quotes == []
        assert any(r["reason"] == REJECT_PRICE_INVALID for r in result.rejected)

    def test_a_started_event_yields_nothing(self):
        result = parse(totals_pair(), started=True)
        assert result.quotes == []
        assert result.pregame_events == 0

    def test_a_foreign_league_is_refused(self):
        result = parse(totals_pair(), league="NBA")
        assert result.quotes == []

    def test_a_payload_that_is_not_an_events_response_raises(self):
        with pytest.raises(SgoPayloadError):
            parse_main_markets({"nope": 1}, received_at=NOW)


class TestCoverageAccounting:
    def test_every_returned_odd_is_classified_exactly_once(self):
        odds = {**moneyline_pair(), **totals_pair(), **spread_pair()}
        odds["points-home-game-ou-over"] = odd(
            "points-home-game-ou-over", "points-home-game-ou-under",
            stat="points", entity="home", period="game", bet_type="ou", side="over")
        odds["points-all-1ix5-ou-over"] = odd(
            "points-all-1ix5-ou-over", "points-all-1ix5-ou-under",
            stat="points", entity="all", period="1ix5", bet_type="ou", side="over")
        odds["batting_hits-PLAYER_1-game-ou-over"] = odd(
            "batting_hits-PLAYER_1-game-ou-over", "x",
            stat="batting_hits", entity="ALEC_BOHM_1_MLB", period="game",
            bet_type="ou", side="over")

        result = parse(odds)
        observed = result.observed_not_ingested
        assert sum(observed.values()) == len(odds)
        assert observed["MAIN_FULL_GAME"] == 6
        assert observed["TEAM_TOTAL"] == 1
        assert observed["PARTIAL_GAME_PERIOD"] == 1
        assert observed["PLAYER_PROP"] == 1

    def test_classify_separates_the_lookalike_over_unders(self):
        combined = {"betTypeID": "ou", "periodID": "game", "statID": "points",
                    "statEntityID": "all"}
        team = {**combined, "statEntityID": "home"}
        player = {**combined, "statEntityID": "ALEC_BOHM_1_MLB"}
        first_five = {**combined, "periodID": "1ix5"}
        hits = {**combined, "statID": "batting_hits"}
        assert classify_odd(combined) == "MAIN_FULL_GAME"
        assert classify_odd(team) == "TEAM_TOTAL"
        assert classify_odd(player) == "PLAYER_PROP"
        assert classify_odd(first_five) == "PARTIAL_GAME_PERIOD"
        assert classify_odd(hits) == "NON_RUNS_STAT"
