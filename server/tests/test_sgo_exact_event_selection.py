"""The MLB request may never be one the provider can answer with a different game.

Evidence reproduced (retained rows, 2026-09-18):

* 14:30:40 ET -- limit=2, startsAfter 22:39:59Z bought both 18:40 ET games and
  bound one provider event to KC @ PIT (07ddd2bf) and another
  to CHC @ CIN (3e7914d7); synthetic IDs stand in for the real ones here.
* 14:42:37 ET -- a refresh selected KC @ PIT alone. The request carried only
  leagueID=MLB, limit=1, startsAfter=2026-09-18T22:39:59Z,
  startsBefore=2026-09-18T23:42:37Z, finalized=false, oddsPresent=true -- no
  event identity. The provider returned CHC @ CIN, which those filters allowed,
  and the lane halted (collection_lane_halts aa24c634).

Everything provider-facing is replaced; the ledger, object budget, cooldown and
lane halt are the real ones in SQLite.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.data_sources.sgo_parser import ParseResult
from app.services import sgo_collection

KC_PIT = "07ddd2bf-a64d-43ca-acfc-fe62feaac824"
CHC_CIN = "3e7914d7-5840-4b6d-be27-053579c22f7c"
EVENT = {KC_PIT: "SyntheticEvtKcPit001", CHC_CIN: "SyntheticEvtChcCin01"}
AT_1840 = datetime(2026, 9, 18, 22, 40, tzinfo=UTC)
LATER = [(str(uuid.UUID(int=0xA000 + i)), AT_1840 + timedelta(minutes=m))
         for i, m in enumerate((25, 30, 30, 35))]             # 19:05, 19:10 x2, 19:15 ET
SLATE = [(KC_PIT, AT_1840), (CHC_CIN, AT_1840), *LATER]
REFRESH_AT = datetime(2026, 9, 18, 18, 42, 37, 76937, tzinfo=UTC)   # the halted request


def _events(needs: dict[str, bool]) -> list[dict[str, Any]]:
    """Coverage-report rows: every slate game, with the needs the collector reads."""
    out = []
    for gid, start in SLATE:
        need = needs.get(gid, False)
        out.append({"game_id": gid, "starts_at_utc": start.isoformat(),
                    "cutoff_utc": (start - timedelta(minutes=15)).isoformat(),
                    "covered": True, "needs_refresh": need})
    return out


class _Grant:
    def __init__(self, wanted: int, granted: int) -> None:
        self.wanted, self.granted = wanted, granted
        self.reason = "GRANTED" if granted == wanted else "GRANTED_PARTIAL"

    @property
    def allowed(self) -> bool:
        return self.granted > 0

    def as_dict(self) -> dict[str, Any]:
        return {"wanted": self.wanted, "granted": self.granted}


class Provider:
    """Answers like SportsGameOdds: earliest `limit` events in the window, ties in
    ITS order (reverse of AISP's id order here). Honours the ID filter only when
    told to, because honouring it is exactly what retained evidence has not shown."""

    def __init__(self, *, honours_ids: bool = False, force: list[str] | None = None) -> None:
        self.honours_ids = honours_ids
        self.force = force
        self.calls: list[dict[str, Any]] = []
        self.returned: list[str] = []

    def answer(self, *, limit, now, starts_within_hours, starts_after, event_ids=None) -> list[str]:
        self.calls.append({"limit": limit, "starts_after": starts_after, "event_ids": event_ids})
        if self.force is not None:
            return list(self.force)
        if event_ids and self.honours_ids:
            by_event = {v: k for k, v in EVENT.items()}
            return [by_event[e] for e in event_ids if e in by_event][:limit]
        hi = now + timedelta(hours=starts_within_hours)
        pool = [(s, gid) for gid, s in SLATE if starts_after <= s <= hi and s > now]
        pool.sort(key=lambda item: (item[0], [-ord(c) for c in item[1]]))
        return [gid for _, gid in pool[:limit]]


def _install(monkeypatch, *, events, provider: Provider, granted: int) -> None:
    class _Coverage:
        def __init__(self, *args, **kwargs) -> None: ...

        def daily_report(self, **kwargs):
            return {"events": events}

    def _fetch(settings, *, limit=10, now=None, starts_within_hours=12, starts_after=None,
               event_ids=None, **_: Any):
        ids = provider.answer(limit=limit, now=now, starts_within_hours=starts_within_hours,
                              starts_after=starts_after, event_ids=event_ids)
        provider.returned = ids
        return sgo_collection.FallbackFetch(requested_at=now, objects=len(ids), pairs=[],
                                            parse=ParseResult(), main=None, provider_league="MLB")

    def _ingest(session, settings, fetch, *, league_code, event_date=None):
        fetch.events_ingested = len(provider.returned)
        return fetch

    monkeypatch.setattr("app.services.quote_coverage.QuoteCoverageService", _Coverage)
    monkeypatch.setattr(sgo_collection, "eligible", lambda settings, now=None: (True, "OK"))
    monkeypatch.setattr(sgo_collection, "fetch_pregame_moneylines", _fetch)
    monkeypatch.setattr(sgo_collection, "ingest", _ingest)
    monkeypatch.setattr(sgo_collection, "_ingested_game_ids",
                        lambda session, fetch: sorted(provider.returned))
    monkeypatch.setattr(sgo_collection, "_games_missing_total", lambda session, ids: set())
    monkeypatch.setattr("app.services.provider_allocation.request_objects",
                        lambda session, *, lane, wanted, now=None: _Grant(wanted, min(wanted, granted)))


def _ledger_requests(session) -> list[Any]:
    from app.models import ProviderRequestLedger

    return session.query(ProviderRequestLedger).filter(
        ProviderRequestLedger.endpoint_key == sgo_collection.ENDPOINT_KEY,
        ProviderRequestLedger.status == "RESERVED").all()


def _active_halt(session):
    from app.services.collection_halt import active_halt

    return active_halt(session, lane=sgo_collection.ENDPOINT_KEY)


def _bind(session, bindings: list[tuple[str, str | None, str]]) -> None:
    """Quote rows as the canonical normalizer writes them: (game id, event id, source)."""
    from app.models import (Game, GameStatus, League, Market, MarketOutcome, MarketType,
                            OddsSnapshot, Sport, Sportsbook)

    sport = Sport(code="BASEBALL", name="Baseball", active=True)
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB", active=True)
    session.add(league)
    session.flush()
    book = Sportsbook(key="sgo:draftkings", name="DraftKings via SportsGameOdds", active=True)
    session.add(book)
    session.flush()
    markets: dict[str, Any] = {}
    at = datetime(2026, 9, 18, 18, 30, 40, tzinfo=UTC)
    for gid, event_id, source in bindings:
        if gid not in markets:
            game = Game(id=uuid.UUID(gid), league_id=league.id, starts_at=AT_1840,
                        status=GameStatus.SCHEDULED, source_mode="LIVE")
            session.add(game)
            session.flush()
            market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
            session.add(market)
            session.flush()
            outcome = MarketOutcome(market_id=market.id, outcome_key="home:None", label="HOME")
            session.add(outcome)
            session.flush()
            markets[gid] = outcome
        at += timedelta(seconds=1)
        session.add(OddsSnapshot(market_outcome_id=markets[gid].id, sportsbook_id=book.id,
                                 observed_at=at, available_as_of=at, american_odds=-120,
                                 decimal_odds=1.83, source=source, source_event_id=event_id,
                                 source_mode="LIVE"))
    session.commit()


# -- 1. the 09-18 request can no longer be sent ---------------------------------

def test_the_selection_that_halted_on_09_18_was_ambiguous():
    """The request carried no identity; its window's earliest start held two games."""

    events = _events({KC_PIT: True, CHC_CIN: True})
    selected = [e for e in events if e["game_id"] == KC_PIT]
    kept, deferred = sgo_collection.unambiguous_prefix(
        selected, events, window_start=datetime(2026, 9, 18, 22, 39, 59, tzinfo=UTC),
        window_end=datetime(2026, 9, 18, 23, 42, 37, tzinfo=UTC), moment=REFRESH_AT)
    assert kept == []
    assert deferred == [{"game_id": KC_PIT, "starts_at_utc": AT_1840.isoformat(),
                         "reason": sgo_collection.DEFERRED_TIED_START}]


def test_the_09_18_refresh_is_deferred_not_sent(session, settings, monkeypatch):
    provider = Provider()
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}),
             provider=provider, granted=1)                  # the one object left on 09-18
    result = sgo_collection.collect_for_open_cutoffs(session, settings, now=REFRESH_AT)

    assert result["reason"] == "DEFERRED_AMBIGUOUS_SELECTION"
    assert result["objects"] == 0
    assert result["games_deferred"][0]["game_id"] == KC_PIT
    assert result["games_deferred"][0]["reason"] == sgo_collection.DEFERRED_TIED_START
    assert provider.calls == []                 # no ambiguous limit=1 request
    assert _ledger_requests(session) == []      # nothing reserved, nothing billed
    assert _active_halt(session) is None


def test_with_the_real_ledger_the_old_request_would_have_halted(session, settings, monkeypatch):
    """Control: the same selection, forced through, halts exactly as it did."""

    provider = Provider()
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    monkeypatch.setattr(sgo_collection, "unambiguous_prefix",
                        lambda selected, events, **kw: (selected, []))   # the pre-repair behaviour
    result = sgo_collection.collect_for_open_cutoffs(session, settings, now=REFRESH_AT)
    assert result["request_filters"] == {
        "leagueID": "MLB", "limit": "1", "startsAfter": "2026-09-18T22:39:59Z",
        "startsBefore": "2026-09-18T23:42:37Z", "finalized": "false", "oddsPresent": "true"}
    assert result["games_ingested"] == [CHC_CIN]
    assert result["lane_halted"] is True
    assert _active_halt(session).reason == "PROVIDER_RETURNED_UNSELECTED_GAME"


# -- 2. exact selection by a verified provider event ID -------------------------

def test_verified_ids_are_the_ones_the_normalizer_bound(session):
    _bind(session, [(KC_PIT, EVENT[KC_PIT], "SPORTSGAMEODDS"),
                    (CHC_CIN, EVENT[CHC_CIN], "SPORTSGAMEODDS")])
    assert sgo_collection.verified_sgo_event_ids(session, [KC_PIT, CHC_CIN]) == EVENT


def test_exact_selection_names_the_verified_event_when_enabled(session, settings, monkeypatch):
    _bind(session, [(KC_PIT, EVENT[KC_PIT], "SPORTSGAMEODDS"),
                    (CHC_CIN, EVENT[CHC_CIN], "SPORTSGAMEODDS")])
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    on = settings.model_copy(update={"sgo_exact_event_selection_enabled": True})
    result = sgo_collection.collect_for_open_cutoffs(session, on, now=REFRESH_AT)

    assert result["reason"] == "FALLBACK_COLLECTED"
    assert result["request_filters"]["eventIDs"] == EVENT[KC_PIT]
    assert result["request_filters"]["limit"] == "1"                     # same limit
    assert result["exact_event_ids"] == [EVENT[KC_PIT]]
    assert provider.calls[0]["event_ids"] == [EVENT[KC_PIT]]
    assert result["games_ingested"] == [KC_PIT] and result["objects"] == 1   # same object
    assert result["lane_halted"] is False and _active_halt(session) is None


def test_exact_selection_is_off_by_default(session, settings, monkeypatch):
    _bind(session, [(KC_PIT, EVENT[KC_PIT], "SPORTSGAMEODDS"),
                    (CHC_CIN, EVENT[CHC_CIN], "SPORTSGAMEODDS")])
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    assert settings.sgo_exact_event_selection_enabled is False
    result = sgo_collection.collect_for_open_cutoffs(session, settings, now=REFRESH_AT)
    assert result["reason"] == "DEFERRED_AMBIGUOUS_SELECTION" and provider.calls == []


# -- 3. an unverified ID is ignored ---------------------------------------------

@pytest.mark.parametrize("bindings", [
    # the ID is bound to a second game as well
    [(KC_PIT, EVENT[KC_PIT], "SPORTSGAMEODDS"), (CHC_CIN, EVENT[KC_PIT], "SPORTSGAMEODDS")],
    # the game carries two different provider IDs
    [(KC_PIT, EVENT[KC_PIT], "SPORTSGAMEODDS"), (KC_PIT, "SomeOtherEventId0001", "SPORTSGAMEODDS")],
    # an ID from another provider is not a SportsGameOdds identity
    [(KC_PIT, EVENT[KC_PIT], "THE_ODDS_API")],
    # a quote row with no event id at all
    [(KC_PIT, None, "SPORTSGAMEODDS")],
], ids=["shared-by-two-games", "two-ids-one-game", "other-provider", "no-id"])
def test_an_unverified_id_is_ignored(session, settings, monkeypatch, bindings):
    _bind(session, bindings)
    assert KC_PIT not in sgo_collection.verified_sgo_event_ids(session, [KC_PIT])
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    on = settings.model_copy(update={"sgo_exact_event_selection_enabled": True})
    result = sgo_collection.collect_for_open_cutoffs(session, on, now=REFRESH_AT)
    assert result["reason"] == "DEFERRED_AMBIGUOUS_SELECTION"     # falls back to deferral
    assert provider.calls == [] and _ledger_requests(session) == []


# -- 4. the mismatch check and the halt are unchanged ---------------------------

def test_a_mismatch_still_halts_the_lane(session, settings, monkeypatch):
    """An unambiguous request answered with an unselected game still latches the lane."""

    later = LATER[0][0]
    provider = Provider(force=[KC_PIT, later])        # the provider misbehaves
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=2)
    result = sgo_collection.collect_for_open_cutoffs(session, settings, now=REFRESH_AT)
    assert sorted(result["games_selected"]) == sorted([KC_PIT, CHC_CIN])   # unambiguous
    assert result["lane_halted"] is True
    assert result["selection_mismatch"]["unselected_games_returned"] == [later]
    halt = _active_halt(session)
    assert halt is not None and halt.reason == "PROVIDER_RETURNED_UNSELECTED_GAME"


def test_an_ignored_id_filter_still_halts_rather_than_admitting_the_wrong_game(
        session, settings, monkeypatch):
    """If the provider ignored eventIDs, the backstop is the unchanged halt."""

    _bind(session, [(KC_PIT, EVENT[KC_PIT], "SPORTSGAMEODDS"),
                    (CHC_CIN, EVENT[CHC_CIN], "SPORTSGAMEODDS")])
    provider = Provider(honours_ids=False)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    on = settings.model_copy(update={"sgo_exact_event_selection_enabled": True})
    result = sgo_collection.collect_for_open_cutoffs(session, on, now=REFRESH_AT)
    assert result["games_ingested"] == [CHC_CIN] and result["lane_halted"] is True


def test_a_halted_lane_is_never_lifted_here(session, settings, monkeypatch):
    from app.services.collection_halt import SELECTION_MISMATCH, halt_lane

    halt_lane(session, lane=sgo_collection.ENDPOINT_KEY, reason=SELECTION_MISMATCH, evidence={},
              now=REFRESH_AT - timedelta(hours=1))
    session.commit()
    provider = Provider()
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=2)
    result = sgo_collection.collect_for_open_cutoffs(session, settings, now=REFRESH_AT)
    assert result["reason"] == "LANE_HALTED" and provider.calls == []
    assert _active_halt(session) is not None


# -- 5. object counts are unchanged ---------------------------------------------

def test_an_unambiguous_request_is_byte_identical_and_costs_the_same(session, settings, monkeypatch):
    """Both 18:40 games, as the 14:30:40 ET request bought them: limit 2, 2 objects."""

    moment = datetime(2026, 9, 18, 18, 30, 40, 944014, tzinfo=UTC)
    provider = Provider()
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=2)
    result = sgo_collection.collect_for_open_cutoffs(session, settings, now=moment)
    assert result["request_filters"] == {
        "leagueID": "MLB", "limit": "2", "startsAfter": "2026-09-18T22:39:59Z",
        "startsBefore": "2026-09-18T23:30:40Z", "finalized": "false", "oddsPresent": "true"}
    assert result["objects"] == 2 and sorted(result["games_ingested"]) == sorted([KC_PIT, CHC_CIN])
    assert "games_deferred" not in result and "exact_event_ids" not in result
    assert result["lane_halted"] is False
    reserved = _ledger_requests(session)
    assert len(reserved) == 1 and reserved[0].quota_cost == 2


def test_a_partly_ambiguous_selection_keeps_its_safe_prefix_and_buys_no_extra(
        session, settings, monkeypatch):
    """19:05 and 19:15 selected, 19:10 x2 not: only 19:05 can be named by the window."""

    at = datetime(2026, 9, 18, 22, 45, 0, tzinfo=UTC)
    g1905, g1910a, g1910b, g1915 = (gid for gid, _ in LATER)
    provider = Provider()
    _install(monkeypatch, events=_events({g1905: True, g1915: True}), provider=provider, granted=5)
    result = sgo_collection.collect_for_open_cutoffs(session, settings, now=at)
    assert result["games_selected"] == [g1905]
    assert result["request_filters"]["limit"] == "1" and result["objects"] == 1
    assert result["games_deferred"] == [{"game_id": g1915,
                                         "starts_at_utc": LATER[3][1].isoformat(),
                                         "reason": sgo_collection.DEFERRED_EARLIER_GAME}]
    assert result["lane_halted"] is False


def test_the_setting_defaults_off():
    from app.core.config import Settings

    assert Settings.model_fields["sgo_exact_event_selection_enabled"].default is False
    assert sgo_collection.EVENT_ID_FILTER_PARAM == "eventIDs"
