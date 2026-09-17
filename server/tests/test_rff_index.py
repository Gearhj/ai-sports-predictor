"""The RFF index — the arithmetic, and the things it is not allowed to claim.

The measure is provisional because the component it is really about, where the
public's money is, has no source. The tests that matter are therefore not
"does it produce a number" but:

  * an unavailable component is never scored zero, because zero is a finding
    and absence is not;
  * evidence coverage is the weight actually behind the number, not a count;
  * the point estimate never escapes without the range it could occupy;
  * nothing it returns is an input to a forecast, a threshold or a position.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import (
    FeatureVersion, Forecast, ForecastStatus, Game, GameStatus, GameTeam, League,
    LineupObservation, Market, MarketOutcome, MarketType, ModelVersion, OddsSnapshot,
    Season, Sport, Sportsbook, StarterObservation, Team,
)
from app.services.rff_index import (
    BANDS, CROSS_BOOK_DISAGREEMENT, INFORMATION_UNCERTAINTY, LATE_MOVEMENT,
    MODEL_MARKET_DISAGREEMENT, PUBLIC_CONCENTRATION, REVERSE_LINE_MOVEMENT,
    RffService, SOCIAL_NARRATIVE, SPORTSBOOK_RELIEF, STATUS_PROVISIONAL, WEIGHTS,
    band_for,
)

START = datetime(2026, 9, 10, 23, 0, tzinfo=UTC)


@pytest.fixture
def game(session: Session):
    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=START - timedelta(days=180), ends_at=START + timedelta(days=60))
    home = Team(league_id=league.id, name="Atlanta Braves", abbreviation="ATL")
    away = Team(league_id=league.id, name="Tampa Bay Rays", abbreviation="TB")
    session.add_all([season, home, away])
    session.flush()
    entry = Game(league_id=league.id, season_id=season.id, starts_at=START,
                 status=GameStatus.SCHEDULED)
    session.add(entry)
    session.flush()
    session.add_all([
        GameTeam(game_id=entry.id, team_id=home.id, is_home=True),
        GameTeam(game_id=entry.id, team_id=away.id, is_home=False),
    ])
    market = Market(game_id=entry.id, market_type=MarketType.MONEYLINE, period="full_game")
    session.add(market)
    session.flush()
    outcomes = [
        MarketOutcome(market_id=market.id, outcome_key="a:home", label="Atlanta Braves",
                      participant_team_id=home.id),
        MarketOutcome(market_id=market.id, outcome_key="b:away", label="Tampa Bay Rays",
                      participant_team_id=away.id),
    ]
    session.add_all(outcomes)
    session.flush()
    return {"session": session, "game": entry, "home": home, "away": away,
            "home_outcome": outcomes[0], "away_outcome": outcomes[1], "league": league}


def price(session: Session, *, outcome, sportsbook, american: int, at: datetime) -> None:
    session.add(OddsSnapshot(
        market_outcome_id=outcome.id, sportsbook_id=sportsbook.id,
        observed_at=at, available_as_of=at, american_odds=american,
        decimal_odds=(1 + american / 100) if american > 0 else (1 - 100 / american),
        implied_probability=(100 / (american + 100)) if american > 0 else (-american / (-american + 100)),
        source="SPORTSGAMEODDS", source_mode="LIVE",
    ))
    session.flush()


def add_book(session: Session, key: str) -> Sportsbook:
    entry = Sportsbook(name=f"{key.title()} (SportsGameOdds, research)", key=f"sgo:{key}")
    session.add(entry)
    session.flush()
    return entry


def component(payload: dict, key: str) -> dict:
    return next(entry for entry in payload["components"] if entry["key"] == key)


def test_the_public_money_components_are_unavailable_and_never_zero(game):
    """The failure this exists for: a zero says 'we looked'. We did not look."""

    payload = RffService(game["session"]).for_game(game_id=game["game"].id, now=START)
    for key in (PUBLIC_CONCENTRATION, SPORTSBOOK_RELIEF, SOCIAL_NARRATIVE):
        entry = component(payload, key)
        assert entry["available"] is False
        assert entry["score"] is None
        assert entry["display"] == "Unavailable"
        assert entry["requires"], "an unavailable component states what it would need"
    assert payload["status"] == STATUS_PROVISIONAL


def test_a_relief_proxy_is_never_derived_from_price_concentration(game):
    relief = component(
        RffService(game["session"]).for_game(game_id=game["game"].id, now=START),
        SPORTSBOOK_RELIEF,
    )
    assert "not liability" in relief["requires"].lower()


def test_coverage_is_the_weight_behind_the_number_not_a_count(game):
    session = game["session"]
    book = add_book(session, "bovada")
    for at, home_price, away_price in (
        (START - timedelta(hours=6), -120, 100),
        (START - timedelta(minutes=40), -140, 120),
    ):
        price(session, outcome=game["home_outcome"], sportsbook=book, american=home_price, at=at)
        price(session, outcome=game["away_outcome"], sportsbook=book, american=away_price, at=at)

    payload = RffService(session).for_game(game_id=game["game"].id, now=START)
    available = [entry for entry in payload["components"] if entry["available"]]
    expected = sum(WEIGHTS[entry["key"]] for entry in available) / sum(WEIGHTS.values())
    assert payload["evidence_coverage"] == pytest.approx(expected, abs=1e-6)
    assert payload["available_components"] == len(available)
    assert payload["unavailable_components"] == 8 - len(available)


def test_the_point_estimate_never_travels_without_its_range(game):
    session = game["session"]
    book = add_book(session, "bovada")
    for at, home_price, away_price in (
        (START - timedelta(hours=6), -120, 100),
        (START - timedelta(minutes=40), -160, 140),
    ):
        price(session, outcome=game["home_outcome"], sportsbook=book, american=home_price, at=at)
        price(session, outcome=game["away_outcome"], sportsbook=book, american=away_price, at=at)

    payload = RffService(session).for_game(game_id=game["game"].id, now=START)
    low, high = payload["score_range"]
    assert low <= payload["score"] <= high
    # The bounds are what the score would be with the missing weight at either
    # extreme, so the gap between them is exactly the uncovered weight.
    assert (high - low) == pytest.approx((1 - payload["evidence_coverage"]) * 100, abs=0.2)


def test_a_game_with_no_market_at_all_is_unscored_not_calm(game):
    payload = RffService(game["session"]).for_game(game_id=game["game"].id, now=START)
    market_driven = [
        component(payload, key)["available"]
        for key in (REVERSE_LINE_MOVEMENT, LATE_MOVEMENT, CROSS_BOOK_DISAGREEMENT)
    ]
    assert not any(market_driven)
    assert "absence of evidence, not a calm market" in payload["explanation"] or payload["score"] is not None


def test_cross_book_disagreement_measures_the_spread_between_books(game):
    session = game["session"]
    tight, wide = add_book(session, "bovada"), add_book(session, "caesars")
    at = START - timedelta(minutes=30)
    price(session, outcome=game["home_outcome"], sportsbook=tight, american=-120, at=at)
    price(session, outcome=game["away_outcome"], sportsbook=tight, american=100, at=at)
    price(session, outcome=game["home_outcome"], sportsbook=wide, american=-160, at=at)
    price(session, outcome=game["away_outcome"], sportsbook=wide, american=140, at=at)

    entry = component(
        RffService(session).for_game(game_id=game["game"].id, now=START),
        CROSS_BOOK_DISAGREEMENT,
    )
    assert entry["available"] is True
    assert entry["detail"]["books"] == 2
    assert entry["detail"]["spread_points"] > 3.0
    assert entry["score"] > 60


def test_model_market_disagreement_is_labelled_as_not_an_edge(game):
    session = game["session"]
    book = add_book(session, "bovada")
    at = START - timedelta(minutes=30)
    price(session, outcome=game["home_outcome"], sportsbook=book, american=-120, at=at)
    price(session, outcome=game["away_outcome"], sportsbook=book, american=100, at=at)
    feature = FeatureVersion(name="fv", version="1", schema_hash="0" * 64)
    version = ModelVersion(name="asp_mlb_ensemble", version="2.0.0")
    session.add_all([feature, version])
    session.flush()
    session.add(Forecast(
        game_id=game["game"].id, model_version_id=version.id,
        feature_version_id=feature.id, version=1,
        generated_at=at, available_as_of=at, status=ForecastStatus.PUBLISHED,
        home_win_probability=0.60, away_win_probability=0.40,
    ))
    session.flush()

    entry = component(
        RffService(session).for_game(game_id=game["game"].id, now=START),
        MODEL_MARKET_DISAGREEMENT,
    )
    assert entry["available"] is True
    assert entry["detail"]["gap_points"] == pytest.approx(7.83, abs=0.1)
    assert "not an edge" in (entry["limitation"] or "").lower()


def test_reverse_line_movement_states_that_it_only_sees_the_price_half(game):
    session = game["session"]
    book = add_book(session, "bovada")
    # The home side opens favourite and gets cheaper.
    price(session, outcome=game["home_outcome"], sportsbook=book, american=-200, at=START - timedelta(hours=5))
    price(session, outcome=game["away_outcome"], sportsbook=book, american=170, at=START - timedelta(hours=5))
    price(session, outcome=game["home_outcome"], sportsbook=book, american=-130, at=START - timedelta(minutes=30))
    price(session, outcome=game["away_outcome"], sportsbook=book, american=110, at=START - timedelta(minutes=30))

    entry = component(
        RffService(session).for_game(game_id=game["game"].id, now=START),
        REVERSE_LINE_MOVEMENT,
    )
    assert entry["available"] is True
    assert entry["detail"]["favourite_side"] == "a"
    assert entry["detail"]["adverse_move_points"] > 4.0
    assert entry["score"] == 100.0, "saturates rather than running away"
    assert "public tickets" in (entry["limitation"] or "").lower()


def test_information_uncertainty_reads_persisted_evidence_only(game):
    session = game["session"]
    session.add(LineupObservation(
        game_id=game["game"].id, team_id=game["home"].id, lineup_status="PROJECTED",
        is_complete=False, observed_at=START - timedelta(hours=2),
        available_as_of=START - timedelta(hours=2), source="MLB_STATS_API", source_mode="LIVE",
    ))
    session.add(StarterObservation(
        game_id=game["game"].id, team_id=game["home"].id, confirmation_state="PROBABLE",
        observed_at=START - timedelta(hours=2), available_as_of=START - timedelta(hours=2),
        source="MLB_STATS_API", source_mode="LIVE",
    ))
    session.flush()

    entry = component(
        RffService(session).for_game(game_id=game["game"].id, now=START),
        INFORMATION_UNCERTAINTY,
    )
    assert entry["available"] is True
    assert entry["detail"]["lineup_sides_observed"] == 1
    assert entry["detail"]["lineup_sides_unresolved"] == 2, "one unresolved plus one never observed"
    assert entry["detail"]["starter_sides_unconfirmed"] == 2
    assert entry["score"] > 50


def test_the_index_declares_that_it_governs_nothing(game):
    authority = RffService(game["session"]).for_game(game_id=game["game"].id, now=START)["authority"]
    assert authority == {
        "affects_forecast": False,
        "affects_adjusted_edge": False,
        "creates_or_suppresses_position": False,
        "changes_threshold": False,
        "grants_wagering_authority": False,
        "statement": authority["statement"],
    }
    assert "not a betting signal" in authority["statement"].lower()


def test_bands_are_ascending_and_descriptive():
    floors = [band[0] for band in BANDS]
    assert floors == sorted(floors)
    assert band_for(0)["label"] == "Calm"
    assert band_for(29.9)["label"] == "Calm"
    assert band_for(30)["label"] == "Curious"
    assert band_for(74.9)["label"] == "Spicy"
    assert band_for(75)["label"] == "Maximum unrest"
    assert band_for(75)["key"] == "MAXIMUM_FUCKERY"  # stable contract key, never displayed
    assert band_for(None)["label"] == "Unscored"


def test_the_share_card_never_asserts_manipulation(game):
    session = game["session"]
    book = add_book(session, "bovada")
    at = START - timedelta(minutes=30)
    price(session, outcome=game["home_outcome"], sportsbook=book, american=-120, at=at)
    price(session, outcome=game["away_outcome"], sportsbook=book, american=100, at=at)

    card = RffService(session).for_game(game_id=game["game"].id, now=START)["share_card"]
    assert card["matchup"] == "TB @ ATL"
    assert "not evidence that a game is manipulated" in card["disclaimer"].lower()
    text = " ".join(str(value).lower() for value in card.values())
    for forbidden in ("rigged", "fixed", "manipulating", "cheating", "corrupt"):
        assert forbidden not in text


def test_the_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)


def test_public_wording_is_clean():
    from app.services.rff_index import BANDS, PUBLIC_LABEL

    assert PUBLIC_LABEL == "RFF Lens — Market reality check"
    shown = [PUBLIC_LABEL] + [label for _, _, label, _ in BANDS] + [text for *_, text in BANDS]
    assert not any("fuck" in text.lower() for text in shown)
