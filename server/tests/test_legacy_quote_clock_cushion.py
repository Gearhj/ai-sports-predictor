"""Legacy quote legs: identity exact, clock cushioned (hotfix 2026-10-24, review HIGH-4).

WHAT HAPPENED
-------------
The BOTH-leg event-clock gate (``pair_clock_verdict``) required
``event_commence_time`` on both legs. Every SportsGameOdds NFL row and the 120
week-7 Odds API snapshots held since 08-29 predate that key, so no legacy pair
could qualify and NFL Sunday rested on one quote source (readiness already showed
``QUOTE_EVENT_CLOCK_UNVERIFIED / MISSING_OR_INVALID_EVENT_CLOCK`` for PIT@NO).

THE CUSHION (owner: "the game time needs to exactly match or we can't move
forward. I want there to be some cushion for a lot of these stringent requirements")
-----------------------------------------------------------------------------------
A pair with a leg that has no event clock at all is a LEGACY pair. It binds when
* both legs name the same non-empty provider event (identity stays exact), agree
  on any identity mapping they retained and keep their orientation where stated;
* the kickoff each leg retained (``identity_mapping.scheduled_start_utc`` /
  ``commence_time``) is within the sport's tolerance of the canonical start
  (``<sport>_legacy_quote_clock_tolerance_minutes``: NFL/CFB 120, NHL/NBA/MLB 20).
The canonical start is then the provider clock, so earliest-start and pregame
deadline gates apply unchanged and the cushion never extends betting time.

Also pinned here: the SportsGameOdds NFL writer (the shared
``NFLShadowOddsService._ingest_event``) records the event-clock keys going
forward, and the three Odds API event binders accept the provider's
:10-past-the-hour listing (the gap that refused 8 NBA games on 10-21/22).
SQLite fixtures only; no provider call.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models import (
    ExternalIdMapping, Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType, OddsSnapshot,
    Season, Sport, Sportsbook, Team,
)
from app.services import event_time_reconciliation as etr
from app.services.event_time_reconciliation import (
    LEGACY_NO_PROVIDER_CLOCK, LEGACY_TOLERANCE_DEFAULT_MINUTES, MATCH_WINDOW, legacy_clock_tolerance,
    pair_clock_verdict,
)

KICK = datetime(2026, 10, 25, 17, 0, tzinfo=UTC)           # a 1:00 PM ET Sunday kickoff
T20 = KICK - timedelta(minutes=20)
WINDOW_CLOSES = timedelta(minutes=15)
EVENT = "oddsapi-legacy-event"


def _legacy_leg(*, kickoff=KICK, event=EVENT, side=None, mapping=True, **extra) -> dict:
    """A row as the pre-contract writer kept it: no event_commence_time / side / receipt clock."""

    raw = {"name": "Somebody", "price": -120, "point": None, "provider_event_id": event,
           "snapshot_state": "T26_PRIMARY"}
    if mapping:
        raw["identity_mapping"] = {"canonical_event_id": "2026_08_KC_LV", "home_team_id": "LV",
                                   "away_team_id": "KC", "scheduled_start_utc": kickoff.isoformat()}
    if side is not None:
        raw["side"] = side
    raw.update(extra)
    return raw


def _verdict(home, away, *, moment=T20, tolerance=timedelta(minutes=120), canonical=KICK, close=WINDOW_CLOSES):
    return pair_clock_verdict(home, away, game_id="g", sport="NFL", home_id="h", away_id="a",
                              canonical_start=canonical, moment=moment, close_before=close,
                              legacy_tolerance=tolerance)


# ------------------------------------------------------------ the pure gate --
def test_a_legacy_pair_inside_the_tolerance_binds_with_the_canonical_clock():
    verdict = _verdict(_legacy_leg(kickoff=KICK + timedelta(minutes=90)), _legacy_leg(kickoff=KICK + timedelta(minutes=90)))
    assert verdict["accepted"] is True and verdict["reason"] == LEGACY_NO_PROVIDER_CLOCK
    # The canonical start is the provider clock: nothing opens earlier or closes later than before.
    assert verdict["provider_start"] == KICK.isoformat() and verdict["earliest_start"] == KICK.isoformat()
    assert verdict["decision_deadline"] == (KICK - WINDOW_CLOSES).isoformat()
    assert verdict["legacy"]["retained_delta_seconds"] == [5400.0, 5400.0]
    assert verdict["legacy"]["tolerance_seconds"] == 7200.0 and verdict["legacy"]["provider_event_id"] == EVENT


def test_an_exactly_matching_legacy_pair_binds_too():
    assert _verdict(_legacy_leg(), _legacy_leg())["accepted"] is True


@pytest.mark.parametrize("offset", [timedelta(minutes=121), -timedelta(minutes=121), timedelta(days=7)])
def test_a_legacy_pair_outside_the_tolerance_is_refused(offset):
    verdict = _verdict(_legacy_leg(kickoff=KICK + offset), _legacy_leg(kickoff=KICK + offset))
    assert verdict == {"accepted": False, "reason": "LEGACY_START_OUTSIDE_TOLERANCE",
                       "legacy": verdict["legacy"], "canonical_start": KICK.isoformat()}


def test_the_tolerance_is_per_call_and_zero_means_exact_only():
    ten = timedelta(minutes=10)
    assert _verdict(_legacy_leg(kickoff=KICK + ten), _legacy_leg(kickoff=KICK + ten), tolerance=timedelta(minutes=20))["accepted"]
    assert not _verdict(_legacy_leg(kickoff=KICK + ten), _legacy_leg(kickoff=KICK + ten), tolerance=timedelta(0))["accepted"]
    assert _verdict(_legacy_leg(), _legacy_leg(), tolerance=timedelta(0))["accepted"]
    # No tolerance at all is the pre-hotfix refusal, byte for byte.
    assert _verdict(_legacy_leg(), _legacy_leg(), tolerance=None) == {
        "accepted": False, "reason": "MISSING_OR_INVALID_EVENT_CLOCK"}


def test_identity_mismatches_are_still_refused():
    # Different provider events on the two legs.
    assert _verdict(_legacy_leg(), _legacy_leg(event="another-event"))["reason"] == "PAIR_PROVIDER_EVENT_DISAGREEMENT"
    # A leg with no provider event at all.
    assert _verdict(_legacy_leg(event=""), _legacy_leg(event=""))["reason"] == "PAIR_PROVIDER_EVENT_DISAGREEMENT"
    # The legs retained different games.
    other = _legacy_leg()
    other["identity_mapping"] = {**other["identity_mapping"], "canonical_event_id": "2026_08_DEN_LV"}
    assert _verdict(_legacy_leg(), other)["reason"] == "LEGACY_IDENTITY_DISAGREEMENT"
    # A stated orientation that contradicts the pair.
    assert _verdict(_legacy_leg(side="AWAY"), _legacy_leg(side="AWAY"))["reason"] == "PAIR_ORIENTATION_CONFLICT"
    # And a leg that retained no kickoff at all cannot be checked, so it is refused.
    assert _verdict(_legacy_leg(mapping=False), _legacy_leg(mapping=False))["reason"] == "MISSING_OR_INVALID_EVENT_CLOCK"


def test_the_original_time_gates_still_close_a_legacy_pair():
    legs = (_legacy_leg(), _legacy_leg())
    assert _verdict(*legs, moment=KICK - timedelta(minutes=14))["reason"] == "PREGAME_DEADLINE_PASSED"
    assert _verdict(*legs, moment=KICK + timedelta(seconds=1))["reason"] == "EARLIEST_START_REACHED"
    assert _verdict(*legs, moment=KICK - WINDOW_CLOSES)["accepted"] is True       # the inclusive T-15 endpoint


def test_a_mixed_pair_is_judged_as_legacy_and_the_modern_leg_keeps_its_own_clock():
    modern = _legacy_leg(event_commence_time=(KICK + timedelta(minutes=30)).isoformat())
    assert _verdict(modern, _legacy_leg())["accepted"] is True
    far = _legacy_leg(event_commence_time=(KICK + timedelta(hours=3)).isoformat())
    assert _verdict(far, _legacy_leg())["reason"] == "LEGACY_START_OUTSIDE_TOLERANCE"


def test_a_pair_with_both_event_clocks_never_takes_the_legacy_path():
    modern = {"event_commence_time": KICK.isoformat(), "provider_event_id": EVENT, "side": "HOME"}
    verdict = _verdict(modern, {**modern, "side": "AWAY"}, tolerance=None)
    assert verdict["accepted"] is True and verdict["reason"] == "LEGACY_EXACT_START"


# ------------------------------------------------------------- the setting --
def test_the_defaults_are_two_hours_for_football_and_twenty_minutes_elsewhere(settings):
    assert LEGACY_TOLERANCE_DEFAULT_MINUTES == {"NFL": 120, "CFB": 120, "NHL": 20, "NBA": 20, "MLB": 20}
    for sport, minutes in LEGACY_TOLERANCE_DEFAULT_MINUTES.items():
        assert getattr(settings, f"{sport.lower()}_legacy_quote_clock_tolerance_minutes") == minutes
        assert legacy_clock_tolerance(sport, settings) == timedelta(minutes=minutes)
    assert legacy_clock_tolerance("NFL") == timedelta(hours=2)                     # process settings
    assert legacy_clock_tolerance("CRICKET", settings) is None                     # unknown sport: refused


def test_the_setting_overrides_the_default_and_is_capped_at_the_match_window(settings):
    narrowed = settings.model_copy(update={"nfl_legacy_quote_clock_tolerance_minutes": 0})
    assert legacy_clock_tolerance("NFL", narrowed) == timedelta(0)
    assert legacy_clock_tolerance("NHL", SimpleNamespace(nhl_legacy_quote_clock_tolerance_minutes=600)) == MATCH_WINDOW
    with pytest.raises(ValueError):
        Settings.model_validate({**settings.model_dump(), "cfb_legacy_quote_clock_tolerance_minutes": 121})


# --------------------------------------------------------------- NFL legs --
def _kick(game) -> datetime:
    return game.starts_at.replace(tzinfo=UTC) if game.starts_at.tzinfo is None else game.starts_at.astimezone(UTC)


def _nfl_legacy_pair(session, game, *, moved_by=timedelta(0), source="SPORTSGAMEODDS", events=(EVENT, EVENT),
                     book_key="sgo:draftkings", captured=None):
    """A SportsGameOdds-shaped (pre-contract) two-sided moneyline for ``game``, as the old writer kept it.

    ``moved_by``: how far the kickoff the row RETAINED differs from the game's canonical start.
    """

    kickoff = _kick(game) + moved_by
    captured = captured or (_kick(game) - timedelta(minutes=40))
    book = session.scalar(select(Sportsbook).where(Sportsbook.key == book_key))
    if book is None:
        book = Sportsbook(key=book_key, name=book_key, region="us", active=True)
        session.add(book)
        session.flush()
    market = session.scalar(select(Market).where(Market.game_id == game.id, Market.market_type == MarketType.MONEYLINE))
    if market is None:
        market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
        session.add(market)
        session.flush()
    sides = {gt.is_home: gt.team_id for gt in session.scalars(select(GameTeam).where(GameTeam.game_id == game.id))}
    ids = {}
    for side, is_home, price, event in (("HOME", True, -140, events[0]), ("AWAY", False, +120, events[1])):
        key = f"{side}:None"
        outcome = session.scalar(select(MarketOutcome).where(MarketOutcome.market_id == market.id,
                                                             MarketOutcome.outcome_key == key))
        if outcome is None:
            outcome = MarketOutcome(market_id=market.id, outcome_key=key, label=side, participant_team_id=sides[is_home])
            session.add(outcome)
            session.flush()
        snapshot = OddsSnapshot(
            market_outcome_id=outcome.id, sportsbook_id=book.id, observed_at=captured - timedelta(minutes=2),
            available_as_of=captured, american_odds=price, decimal_odds=1.71 if price < 0 else 2.2,
            implied_probability=0.58 if price < 0 else 0.45, source=source, source_event_id=event,
            source_mode="LIVE", raw_payload=_legacy_leg(kickoff=kickoff, event=event))
        session.add(snapshot)
        session.flush()
        ids[side] = snapshot.id
    session.commit()
    return ids


@pytest.fixture
def nfl(session):
    from tests.test_nfl_odds_api_fallback import World

    return World(session)


def test_a_legacy_sgo_pair_qualifies_for_the_nfl_checkpoint_with_the_canonical_clock(nfl):
    from app.services.nfl_paper_decision import clock_bound_pairs, operational_start, paired_quotes

    game = nfl.games["LV"]
    kick = _kick(game)
    kept_ids = _nfl_legacy_pair(nfl.session, game, moved_by=timedelta(minutes=90))   # retained 90 min off, inside 120
    moment = kick - timedelta(minutes=20)
    pairs = paired_quotes(nfl.session, game.id, moment=moment)
    assert len(pairs) == 1
    kept, refused = clock_bound_pairs(nfl.session, game.id, pairs, moment=moment)
    assert refused == [] and len(kept) == 1
    clock = kept[0]["event_clock"]
    assert clock["reason"] == LEGACY_NO_PROVIDER_CLOCK and clock["provider_start"] == kick.isoformat()
    assert {kept[0]["home_snapshot_id"], kept[0]["away_snapshot_id"]} == set(kept_ids.values())
    # Identity-only qualification (the operational-start reader) does not KeyError on the
    # missing receipt clock and never moves the window earlier than the canonical start.
    identity, _ = clock_bound_pairs(nfl.session, game.id, pairs, moment=moment, identity_only=True)
    assert len(identity) == 1 and identity[0]["event_clock"]["earliest_start"] == kick.isoformat()
    assert operational_start(nfl.session, game.id, moment=moment) == kick


def test_a_legacy_pair_whose_retained_kickoff_moved_past_the_tolerance_is_refused(nfl):
    from app.services.nfl_paper_decision import clock_bound_pairs, paired_quotes

    game = nfl.games["BAL"]
    _nfl_legacy_pair(nfl.session, game, moved_by=timedelta(hours=3))
    moment = _kick(game) - timedelta(minutes=20)
    kept, refused = clock_bound_pairs(nfl.session, game.id, paired_quotes(nfl.session, game.id, moment=moment), moment=moment)
    assert kept == []
    assert [(r["reason"], r["clock_reason"]) for r in refused] == [
        ("QUOTE_EVENT_CLOCK_UNVERIFIED", "LEGACY_START_OUTSIDE_TOLERANCE")]


def test_a_legacy_pair_with_mismatched_provider_events_is_refused(nfl):
    from app.services.nfl_paper_decision import clock_bound_pairs, paired_quotes

    game = nfl.games["LV"]
    _nfl_legacy_pair(nfl.session, game, events=("oddsapi-one", "oddsapi-two"))
    moment = _kick(game) - timedelta(minutes=20)
    kept, refused = clock_bound_pairs(nfl.session, game.id, paired_quotes(nfl.session, game.id, moment=moment), moment=moment)
    assert kept == [] and refused[0]["clock_reason"] == "PAIR_PROVIDER_EVENT_DISAGREEMENT"


def test_the_nfl_setting_narrows_the_cushion_at_the_call_site(nfl, monkeypatch):
    from app.services.nfl_paper_decision import clock_bound_pairs, paired_quotes

    game = nfl.games["LV"]
    _nfl_legacy_pair(nfl.session, game, moved_by=timedelta(minutes=30))
    moment = _kick(game) - timedelta(minutes=20)
    pairs = paired_quotes(nfl.session, game.id, moment=moment)
    assert clock_bound_pairs(nfl.session, game.id, pairs, moment=moment)[0]           # 30 min < 120 min: kept
    monkeypatch.setattr(etr, "get_settings", lambda: SimpleNamespace(nfl_legacy_quote_clock_tolerance_minutes=15),
                        raising=False)
    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace(nfl_legacy_quote_clock_tolerance_minutes=15))
    kept, refused = clock_bound_pairs(nfl.session, game.id, pairs, moment=moment)
    assert kept == [] and refused[0]["clock_reason"] == "LEGACY_START_OUTSIDE_TOLERANCE"


# ------------------------------------------------- the SGO NFL writer, forward --
def test_sportsgameodds_nfl_rows_now_carry_the_event_clock_keys(nfl):
    """The SGO lane and the Odds API fallback share ``_ingest_event``; both write the keys."""

    from app.data_sources.base import OddsEventDTO
    from app.data_sources.odds import TheOddsAPIAdapter
    from app.services.nfl_shadow_odds_service import NFLShadowOddsService

    from tests.test_nfl_odds_api_fallback import KICK as FKICK

    received = FKICK - timedelta(minutes=26)
    events = [OddsEventDTO.model_validate(TheOddsAPIAdapter._normalize_event(nfl.raw("LV")).model_dump(mode="json"))]
    result = NFLShadowOddsService(nfl.session, source="SPORTSGAMEODDS").ingest(events, available_as_of=received)
    assert result["quotes"] == 2 and result["rejected_events"] == 0
    rows = nfl.session.scalars(select(OddsSnapshot).where(OddsSnapshot.source == "SPORTSGAMEODDS")).all()
    assert len(rows) == 2
    for row in rows:
        raw = row.raw_payload
        assert raw["event_commence_time"] == FKICK.isoformat()
        assert raw["response_received_at"] == received.isoformat()
        assert raw["side"] in ("HOME", "AWAY") and raw["provider_event_id"] == row.source_event_id
        assert raw["event_clock_binding"]["contract"] == etr.CONTRACT
        assert raw["event_clock_binding"]["provider_event_id"] == row.source_event_id
    # ...and such a pair takes the modern path, not the legacy one.
    home = next(r for r in rows if r.raw_payload["side"] == "HOME")
    away = next(r for r in rows if r.raw_payload["side"] == "AWAY")
    sides = {gt.is_home: gt.team_id for gt in nfl.session.scalars(
        select(GameTeam).where(GameTeam.game_id == nfl.games["LV"].id))}
    verdict = pair_clock_verdict(home.raw_payload, away.raw_payload, game_id=nfl.games["LV"].id, sport="NFL",
                                 home_id=sides[True], away_id=sides[False], canonical_start=FKICK,
                                 moment=FKICK - timedelta(minutes=20), close_before=WINDOW_CLOSES, legacy_tolerance=None)
    assert verdict["accepted"] is True and verdict["reason"] == "EXACT_START"


# ------------------------------------------- the Odds API binders at :10 past --
TIP = datetime(2026, 10, 24, 23, 0, tzinfo=UTC)           # 7:00 PM ET listed; the provider says 7:10


def _nba_world(session):
    from app.nba import gen1_common as common

    sport = Sport(code="BASKETBALL", name="Basketball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code=common.LEAGUE_CODE, name="NBA")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026-27", starts_at=TIP - timedelta(days=10), ends_at=TIP + timedelta(days=200))
    session.add(season)
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=TIP, status=GameStatus.SCHEDULED)
    session.add(game)
    session.flush()
    for is_home, code, name in ((True, "ATL", "Atlanta Hawks"), (False, "BOS", "Boston Celtics")):
        team = Team(league_id=league.id, name=name, abbreviation=code)
        session.add(team)
        session.flush()
        session.add(GameTeam(game_id=game.id, team_id=team.id, is_home=is_home))
        session.add(ExternalIdMapping(entity_type="team", entity_id=team.id, source=common.SOURCE_FRANCHISE,
                                      external_id=f"nba-neil:franchise:{code}"))
    session.commit()
    return game


def _odds_api_event(sport_key, event_id, home, away, commence, *, clock):
    from app.data_sources.base import OddsEventDTO
    from app.data_sources.odds import TheOddsAPIAdapter

    stamp = lambda v: v.astimezone(UTC).isoformat().replace("+00:00", "Z")  # noqa: E731
    raw = {"id": event_id, "sport_key": sport_key, "commence_time": stamp(commence), "home_team": home,
           "away_team": away, "bookmakers": [{"key": "draftkings", "title": "DraftKings", "last_update": stamp(clock),
                                              "markets": [{"key": "h2h", "last_update": stamp(clock), "outcomes": [
                                                  {"name": home, "price": -150}, {"name": away, "price": +130}]}]}]}
    return OddsEventDTO.model_validate(TheOddsAPIAdapter._normalize_event(raw).model_dump(mode="json"))


@pytest.mark.parametrize("offset, bound", [
    (timedelta(minutes=10), True),            # the provider's :10-past listing (8 NBA games refused on 10-21/22)
    (-timedelta(minutes=10), True),
    (timedelta(hours=2), True),               # the MATCH_WINDOW edge, inclusive
    (timedelta(hours=2, minutes=1), False),
])
def test_the_nba_binder_accepts_the_ten_past_the_hour_listing_within_the_match_window(session, offset, bound):
    from app.nba import odds_api_price as nba_odds

    game = _nba_world(session)
    received = TIP - timedelta(minutes=50)
    event = _odds_api_event("basketball_nba", "FIXTURENBA00001", "Atlanta Hawks", "Boston Celtics", TIP + offset,
                            clock=received - timedelta(minutes=5))
    bound_pairs, (receipt,) = nba_odds.bind_events(session, [event], received_at=received)
    assert (receipt["reason"] == nba_odds.BOUND) is bound
    assert ([g for _e, g in bound_pairs] == [game.id]) is bound
    if not bound:
        assert receipt["reason"] == nba_odds.TIPOFF_DISAGREEMENT and receipt["gap_minutes"] == 121.0
    assert nba_odds.TIPOFF_TOLERANCE == MATCH_WINDOW
