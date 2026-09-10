"""The market tape — the pairing rule, and the carry-forward.

Two things can quietly ruin a replay and neither looks wrong on screen. The
first is a pair built from two different sources: The Odds API's "betmgm" and
the SportsGameOdds fallback's "sgo:betmgm" are different rows for what a
reader thinks of as one brand, and a no-vig probability derived from one
provider's home price and another's away price is a number nobody could have
taken. The second is carrying a price forward without saying so: a book that
stopped quoting at noon must not look like it is still quoting at eight.

Nothing here calls a provider or writes a row.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType,
    OddsSnapshot, Season, Sport, Sportsbook, Team,
)
from app.services.market_tape import (
    FALLBACK_PROVIDER, MARKET_TYPES, MAX_FRAMES, MarketTapeService,
    PRIMARY_PROVIDER, display_name, provider_of,
)

START = datetime(2026, 9, 10, 23, 0, tzinfo=UTC)


@pytest.fixture
def fixture(session: Session):
    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id, name="2026",
        starts_at=START - timedelta(days=180), ends_at=START + timedelta(days=60),
    )
    home = Team(league_id=league.id, name="Atlanta Braves", abbreviation="ATL")
    away = Team(league_id=league.id, name="Tampa Bay Rays", abbreviation="TB")
    session.add_all([season, home, away])
    session.flush()
    game = Game(
        league_id=league.id, season_id=season.id, starts_at=START, status=GameStatus.SCHEDULED,
    )
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
    session.add(market)
    session.flush()
    outcome_home = MarketOutcome(
        market_id=market.id, outcome_key="a:home", label="Atlanta Braves",
        participant_team_id=home.id,
    )
    outcome_away = MarketOutcome(
        market_id=market.id, outcome_key="b:away", label="Tampa Bay Rays",
        participant_team_id=away.id,
    )
    session.add_all([outcome_home, outcome_away])
    session.flush()
    return {
        "session": session, "game": game, "home": home, "away": away,
        "market": market, "home_outcome": outcome_home, "away_outcome": outcome_away,
    }


def book(session: Session, name: str, key: str) -> Sportsbook:
    entry = Sportsbook(name=name, key=key)
    session.add(entry)
    session.flush()
    return entry


def quote(session: Session, *, outcome, sportsbook, american: int, at: datetime, source: str) -> None:
    session.add(OddsSnapshot(
        market_outcome_id=outcome.id, sportsbook_id=sportsbook.id,
        observed_at=at, available_as_of=at, american_odds=american,
        decimal_odds=(1 + american / 100) if american > 0 else (1 - 100 / american),
        implied_probability=(100 / (american + 100)) if american > 0 else (-american / (-american + 100)),
        source=source, source_mode="LIVE",
    ))
    session.flush()


def test_a_pair_is_one_book_at_one_provider(fixture):
    """The failure this test exists for.

    "BetMGM" via The Odds API and "Betmgm (SportsGameOdds, research)" are one
    brand and two providers. Pairing across them would mint a no-vig
    probability out of two prices that were never on one screen.
    """

    session = fixture["session"]
    primary = book(session, "BetMGM", "betmgm")
    fallback = book(session, "Betmgm (SportsGameOdds, research)", "sgo:betmgm")
    at = START - timedelta(hours=3)
    # Each provider supplies exactly ONE side. Neither book has a pair.
    quote(session, outcome=fixture["home_outcome"], sportsbook=primary, american=-120, at=at, source="THE_ODDS_API")
    quote(session, outcome=fixture["away_outcome"], sportsbook=fallback, american=105, at=at, source="SPORTSGAMEODDS")

    tape = MarketTapeService(session).tape(game_id=fixture["game"].id, now=START)
    assert tape["frames"], "the instant is still a frame"
    assert all(frame["usable_books"] == 0 for frame in tape["frames"])
    assert all(frame["consensus_no_vig_a"] is None for frame in tape["frames"])
    assert provider_of(primary) == PRIMARY_PROVIDER
    assert provider_of(fallback) == FALLBACK_PROVIDER


def test_a_complete_pair_at_one_book_prices(fixture):
    session = fixture["session"]
    entry = book(session, "Bovada (SportsGameOdds, research)", "sgo:bovada")
    at = START - timedelta(hours=3)
    quote(session, outcome=fixture["home_outcome"], sportsbook=entry, american=-120, at=at, source="SPORTSGAMEODDS")
    quote(session, outcome=fixture["away_outcome"], sportsbook=entry, american=100, at=at, source="SPORTSGAMEODDS")

    tape = MarketTapeService(session).tape(game_id=fixture["game"].id, now=START)
    latest = tape["frames"][-1]
    assert latest["usable_books"] == 1
    # -120 / +100 implies 0.5455 / 0.5000; no-vig home is 0.5455/1.0455.
    assert latest["consensus_no_vig_a"] == pytest.approx(0.521739, abs=1e-5)
    assert latest["consensus_no_vig_home"] == pytest.approx(0.521739, abs=1e-5)
    assert tape["market"]["home_side"] == "a"


def test_a_price_is_carried_forward_but_keeps_its_real_observation_time(fixture):
    """A book that stopped quoting still shows its last price, dated honestly."""

    session = fixture["session"]
    quiet = book(session, "Caesars (SportsGameOdds, research)", "sgo:caesars")
    active = book(session, "Bovada (SportsGameOdds, research)", "sgo:bovada")
    # Inside the four-hour freshness window: this test is about carrying a
    # price forward, and staleness is the next test's subject.
    early, late = START - timedelta(hours=3), START - timedelta(hours=1)
    for outcome, price in ((fixture["home_outcome"], -120), (fixture["away_outcome"], 100)):
        quote(session, outcome=outcome, sportsbook=quiet, american=price, at=early, source="SPORTSGAMEODDS")
        quote(session, outcome=outcome, sportsbook=active, american=price, at=early, source="SPORTSGAMEODDS")
    quote(session, outcome=fixture["home_outcome"], sportsbook=active, american=-140, at=late, source="SPORTSGAMEODDS")
    quote(session, outcome=fixture["away_outcome"], sportsbook=active, american=120, at=late, source="SPORTSGAMEODDS")

    tape = MarketTapeService(session).tape(game_id=fixture["game"].id, now=START)
    latest = tape["frames"][-1]
    assert latest["usable_books"] == 2, "the quiet book is still in the market"
    series = {entry["name"]: entry["series"] for entry in tape["books"]}
    carried = series["Caesars"][-1]
    assert carried["observed_at_utc"] == early.isoformat(), "carried, not re-dated"
    assert series["Caesars"][-1]["a"] == -120
    assert len(series["Bovada"]) == 2, "only the instants Bovada actually changed"
    assert len(series["Caesars"]) == 1, "a book that never moved is one entry, not two"


def test_a_stale_carried_price_is_reported_but_not_counted_as_usable(fixture):
    """Past the freshness window a price is evidence, not a usable quote."""

    session = fixture["session"]
    stale = book(session, "Caesars (SportsGameOdds, research)", "sgo:caesars")
    fresh = book(session, "Bovada (SportsGameOdds, research)", "sgo:bovada")
    long_ago, recent = START - timedelta(hours=12), START - timedelta(minutes=30)
    for outcome, price in ((fixture["home_outcome"], -120), (fixture["away_outcome"], 100)):
        quote(session, outcome=outcome, sportsbook=stale, american=price, at=long_ago, source="SPORTSGAMEODDS")
        quote(session, outcome=outcome, sportsbook=fresh, american=price, at=recent, source="SPORTSGAMEODDS")

    tape = MarketTapeService(session).tape(game_id=fixture["game"].id, now=START)
    latest = tape["frames"][-1]
    assert latest["usable_books"] == 1
    entries = {entry["name"]: entry["series"][-1] for entry in tape["books"]}
    assert entries["Caesars"]["usable"] is False
    assert entries["Bovada"]["usable"] is True


def test_two_markets_of_the_same_game_never_share_a_frame(fixture):
    """A total is a different question; its prices may not land on a win axis."""

    session = fixture["session"]
    entry = book(session, "Bovada (SportsGameOdds, research)", "sgo:bovada")
    at = START - timedelta(hours=2)
    quote(session, outcome=fixture["home_outcome"], sportsbook=entry, american=-120, at=at, source="SPORTSGAMEODDS")
    quote(session, outcome=fixture["away_outcome"], sportsbook=entry, american=100, at=at, source="SPORTSGAMEODDS")

    total = Market(game_id=fixture["game"].id, market_type=MarketType.TOTAL, period="full_game")
    session.add(total)
    session.flush()
    over = MarketOutcome(market_id=total.id, outcome_key="over:8.5", label="Over 8.5")
    under = MarketOutcome(market_id=total.id, outcome_key="under:8.5", label="Under 8.5")
    session.add_all([over, under])
    session.flush()
    quote(session, outcome=over, sportsbook=entry, american=-110, at=at, source="SPORTSGAMEODDS")
    quote(session, outcome=under, sportsbook=entry, american=-110, at=at, source="SPORTSGAMEODDS")

    service = MarketTapeService(session)
    moneyline = service.tape(game_id=fixture["game"].id, market_type="MONEYLINE", now=START)
    totals = service.tape(game_id=fixture["game"].id, market_type="TOTAL", now=START)
    assert moneyline["frames"][-1]["consensus_no_vig_a"] == pytest.approx(0.521739, abs=1e-5)
    assert totals["frames"][-1]["consensus_no_vig_a"] == pytest.approx(0.5, abs=1e-6)
    # A total has no home side, and saying it did would put a run count on a
    # win-probability axis.
    assert totals["market"]["home_side"] is None
    assert totals["frames"][-1]["consensus_no_vig_home"] is None


def test_a_market_with_no_persisted_price_states_the_absence(fixture):
    tape = MarketTapeService(session=fixture["session"]).tape(
        game_id=fixture["game"].id, market_type="SPREAD", now=START,
    )
    assert tape["frames"] == []
    assert "no spread price has been persisted" in tape["requirement"].lower()
    assert tape["books"] == []


def test_thinning_drops_frames_and_never_invents_one(fixture):
    session = fixture["session"]
    entry = book(session, "Bovada (SportsGameOdds, research)", "sgo:bovada")
    instants = [START - timedelta(hours=10) + timedelta(seconds=index * 30)
                for index in range(MAX_FRAMES + 90)]
    for index, at in enumerate(instants):
        quote(session, outcome=fixture["home_outcome"], sportsbook=entry,
              american=-120 - (index % 5), at=at, source="SPORTSGAMEODDS")
        quote(session, outcome=fixture["away_outcome"], sportsbook=entry,
              american=100 + (index % 5), at=at, source="SPORTSGAMEODDS")

    tape = MarketTapeService(session).tape(game_id=fixture["game"].id, now=START)
    assert tape["frames_sampled"] is True
    assert tape["observed_instants"] == len(instants)
    assert len(tape["frames"]) <= MAX_FRAMES + 2
    kept = {frame["t"] for frame in tape["frames"]}
    assert kept <= {at.isoformat() for at in instants}, "every kept frame is a real instant"
    assert instants[0].isoformat() in kept and instants[-1].isoformat() in kept


def test_the_book_label_drops_only_the_provenance_parenthetical():
    assert display_name(Sportsbook(name="Betmgm (SportsGameOdds, research)", key="sgo:betmgm")) == "Betmgm"
    assert display_name(Sportsbook(name="DraftKings", key="draftkings")) == "DraftKings"


def test_every_supported_market_type_is_reachable(fixture):
    service = MarketTapeService(fixture["session"])
    for market_type in MARKET_TYPES:
        assert service.tape(game_id=fixture["game"].id, market_type=market_type, now=START)
    with pytest.raises(ValueError):
        service.tape(game_id=fixture["game"].id, market_type="FIRST_FIVE", now=START)


def test_an_unregistered_game_is_refused_without_inventing_a_market(fixture):
    tape = MarketTapeService(fixture["session"]).tape(game_id=uuid4(), now=START)
    assert tape["requirement"] == "This game is not registered."
    assert tape["frames"] == [] and tape["books"] == []


def test_a_spread_pairs_within_one_line_and_never_across_two(fixture):
    """The failure this exists for.

    A spread market can hold -3.5 and -4.5 at the same time. "Home -3.5 against
    away +4.5" is not a price anybody could take, so the two sides of a pair
    must sit on the same handicap.
    """

    session = fixture["session"]
    entry = book(session, "Bovada (SportsGameOdds, research)", "sgo:bovada")
    spread = Market(game_id=fixture["game"].id, market_type=MarketType.SPREAD, period="full_game")
    session.add(spread)
    session.flush()
    sides = {}
    for key, label, point in (
        ("home:-3.5", "ATL -3.5", -3.5), ("away:3.5", "TB +3.5", 3.5),
        ("home:-4.5", "ATL -4.5", -4.5),
    ):
        outcome = MarketOutcome(market_id=spread.id, outcome_key=key, label=label)
        session.add(outcome)
        session.flush()
        sides[key] = (outcome, point)

    at = START - timedelta(hours=2)
    for key, american in (("home:-3.5", -110), ("away:3.5", -110), ("home:-4.5", 130)):
        outcome, point = sides[key]
        session.add(OddsSnapshot(
            market_outcome_id=outcome.id, sportsbook_id=entry.id, observed_at=at,
            available_as_of=at, american_odds=american, point=point,
            decimal_odds=1.91, implied_probability=0.5238,
            source="SPORTSGAMEODDS", source_mode="LIVE",
        ))
    session.flush()

    tape = MarketTapeService(session).tape(
        game_id=fixture["game"].id, market_type="SPREAD", now=START)
    # The -3.5 line is the only one with two sides, and it is what is shown.
    assert tape["market"]["line"] == "3.50"
    labels = {tape["market"]["side_a"]["label"], tape["market"]["side_b"]["label"]}
    assert labels == {"ATL -3.5", "TB +3.5"}
    assert tape["frames"][-1]["usable_books"] == 1
    assert tape["frames"][-1]["consensus_no_vig_a"] == pytest.approx(0.5, abs=1e-6)


def test_a_spread_with_no_two_sided_line_states_the_absence(fixture):
    session = fixture["session"]
    entry = book(session, "Bovada (SportsGameOdds, research)", "sgo:bovada")
    spread = Market(game_id=fixture["game"].id, market_type=MarketType.SPREAD, period="full_game")
    session.add(spread)
    session.flush()
    for key, point in (("home:-3.5", -3.5), ("home:-4.5", -4.5)):
        outcome = MarketOutcome(market_id=spread.id, outcome_key=key, label=key)
        session.add(outcome)
        session.flush()
        session.add(OddsSnapshot(
            market_outcome_id=outcome.id, sportsbook_id=entry.id,
            observed_at=START - timedelta(hours=1), available_as_of=START - timedelta(hours=1),
            american_odds=-110, point=point, decimal_odds=1.91, implied_probability=0.5238,
            source="SPORTSGAMEODDS", source_mode="LIVE",
        ))
    session.flush()

    tape = MarketTapeService(session).tape(
        game_id=fixture["game"].id, market_type="SPREAD", now=START)
    assert tape["frames"] == []
    assert "both sides of one line" in tape["requirement"]


def test_milestones_keep_one_shape_whether_or_not_a_market_exists(fixture):
    """A consumer should never have to tell "no tape" from "a different tape"."""

    empty = MarketTapeService(fixture["session"]).tape(
        game_id=fixture["game"].id, market_type="TOTAL", now=START)
    assert set(empty["milestones"]) == {"opening", "latest", "at_cutoff", "movement"}
    assert all(value is None for value in empty["milestones"].values())
