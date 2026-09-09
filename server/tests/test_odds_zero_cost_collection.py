"""Focused checks for the zero-cost odds collection strategy.

Exactly the five things the change turns on:

1. Quota arithmetic - the billing window, the untouchable reserve, and the rule
   that a reading from a previous window proves nothing about today.
2. Checkpoint selection - the minimum cover really covers every game, stops at
   the decision cutoff, and never plans for an inactive sport.
3. Deduplication - a checkpoint already served is not served again, and that
   survives a process restart because the state lives in the ledger.
4. Fail-closed provider identity - a research provider cannot borrow a governed
   bookmaker, and the adapter refuses to act while disabled or unmapped.
5. One ingestion fixture - a recorded payload maps to AISP DTOs with namespaced
   books, and anything incomplete is dropped rather than guessed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.data_sources import provider_registry
from app.data_sources.sportsgameodds import (
    FallbackMappingUnavailable,
    FallbackProviderDisabled,
    SportsGameOddsAdapter,
)
from app.models import ProviderRequestLedger
from app.services import odds_budget
from app.services.odds_collection_plan import (
    CHECKPOINT_GRACE_MINUTES,
    CHECKPOINT_TICK_SLACK_MINUTES,
    INGEST_HEADROOM_MINUTES,
    MAX_QUOTE_STALENESS_MINUTES,
    MLB_POLICY,
    NFL_POLICY,
    build_sport_plan,
    covers,
    decision_cutoff,
    due_checkpoint,
    plan_checkpoints,
    planned_covers,
)

ET = ZoneInfo("America/New_York")


def _starts(day: str, times: list[str]) -> list[datetime]:
    return [datetime.fromisoformat(f"{day}T{t}:00").replace(tzinfo=ET) for t in times]


# ---------------------------------------------------------------------------
# 1. quota arithmetic
# ---------------------------------------------------------------------------

def _ledger(session, *, requested_at: datetime, status: str = "SUCCEEDED",
            endpoint: str = "baseball_mlb_odds", cost: int | None = 3,
            remaining: int | None = None, used: int | None = None) -> ProviderRequestLedger:
    """One request as the production path records it: a reservation, then its outcome."""

    fingerprint = hashlib.sha256(f"{endpoint}{requested_at}".encode()).hexdigest()
    reservation = ProviderRequestLedger(
        provider="THE_ODDS_API", endpoint_key=endpoint, requested_at=requested_at,
        status="RESERVED", request_fingerprint=fingerprint,
        controlled_live_request=False, source_mode="LIVE", response_metadata={},
    )
    session.add(reservation)
    session.flush()
    if status == "RESERVED":
        session.commit()
        return reservation
    terminal = ProviderRequestLedger(
        provider="THE_ODDS_API", endpoint_key=endpoint, requested_at=requested_at,
        completed_at=requested_at, status=status, request_fingerprint=fingerprint,
        quota_cost=cost, quota_remaining=remaining, records_received=15,
        controlled_live_request=False, source_mode="LIVE", reservation_id=reservation.id,
        response_metadata={"requests_used": used} if used is not None else {},
    )
    session.add(terminal)
    session.commit()
    return terminal


def test_billing_window_is_the_utc_calendar_month() -> None:
    """Measured from the real 2026-09-01T00:00Z counter reset, not assumed."""

    start, end = odds_budget.billing_window(datetime(2026, 9, 7, 23, 0, tzinfo=UTC))
    assert start == datetime(2026, 9, 1, tzinfo=UTC)
    assert end == datetime(2026, 10, 1, tzinfo=UTC)
    # 2026-08-31 20:02 EDT is already inside the September window.
    august_evening = datetime(2026, 8, 31, 20, 2, tzinfo=ET)
    assert odds_budget.billing_window(august_evening)[0] == datetime(2026, 9, 1, tzinfo=UTC)


def test_reserve_is_never_spendable_and_the_budget_fails_closed(session) -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    # 100 credits already spent this window across a few requests.
    for day in range(2, 6):
        _ledger(session, requested_at=datetime(2026, 9, day, 18, 0, tzinfo=UTC), cost=25,
                remaining=400, used=100)
    state = odds_budget.budget_state(
        session, monthly_cap=500, reserve=150, daily_cap=12, floor=10, now=now,
    )
    assert state.ledger_spent == 100
    # The provider's own counter is authoritative when it is in-window.
    assert state.provider_used == 100
    assert state.budgeted_remaining == 250          # 500 - 150 reserve - 100 spent
    assert state.reserve_intact is True
    allowed, _ = state.may_spend(1)
    assert allowed is True

    # The daily cap bites first, and says so rather than blaming the month.
    allowed, refusal = state.may_spend(13)
    assert allowed is False and "Daily budget exhausted" in refusal

    # With the day wide open, the reserve is still untouchable.
    generous = odds_budget.budget_state(
        session, monthly_cap=500, reserve=150, daily_cap=500, floor=10, now=now,
    )
    assert generous.may_spend(250)[0] is True
    allowed, refusal = generous.may_spend(251)
    assert allowed is False and "reserve of 150 is not spendable" in refusal


def test_a_reading_from_a_previous_window_never_implies_available_credits(session) -> None:
    """The August reading said 497 remaining. That proves nothing in September."""

    _ledger(session, requested_at=datetime(2026, 8, 13, 4, 15, tzinfo=UTC), cost=3,
            remaining=497, used=3)
    state = odds_budget.budget_state(
        session, monthly_cap=500, reserve=150, daily_cap=12, floor=10,
        now=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
    )
    assert state.provider_remaining is None
    assert state.balance_state == odds_budget.BALANCE_UNKNOWN
    assert "cannot show credits are available" in state.as_dict()["balance_note"]
    # The floor cannot be cleared by an out-of-window reading either.
    assert state.floor_latched is False
    assert state.spent == 0  # the August spend belongs to the August window


def test_an_in_window_reading_at_the_floor_latches_the_budget(session) -> None:
    _ledger(session, requested_at=datetime(2026, 9, 7, 20, 44, tzinfo=UTC), cost=3,
            remaining=5, used=495)
    state = odds_budget.budget_state(
        session, monthly_cap=500, reserve=150, daily_cap=12, floor=10,
        now=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    )
    assert state.floor_latched is True
    assert state.spent == 495          # the provider's counter, not our 3-credit row
    assert state.budgeted_remaining == 0
    allowed, refusal = state.may_spend(1)
    assert allowed is False and "floor" in refusal


def test_an_uncompleted_reservation_still_counts_against_the_budget(session) -> None:
    """A request that never returned may still have reached the provider."""

    _ledger(session, requested_at=datetime(2026, 9, 2, 18, 0, tzinfo=UTC),
            status="RESERVED", cost=None, remaining=None)
    state = odds_budget.budget_state(
        session, monthly_cap=500, reserve=150, daily_cap=12, floor=10,
        now=datetime(2026, 9, 2, 19, 0, tzinfo=UTC),
    )
    assert state.ledger_spent == 3  # unknown shape -> historical worst case, not free


def test_the_zero_cost_balance_probe_never_consumes_budget(session) -> None:
    _ledger(session, requested_at=datetime(2026, 9, 2, 18, 0, tzinfo=UTC),
            endpoint="quota_recovery_probe", status="RESERVED", cost=None)
    state = odds_budget.budget_state(
        session, monthly_cap=500, reserve=150, daily_cap=12, floor=10,
        now=datetime(2026, 9, 2, 19, 0, tzinfo=UTC),
    )
    assert state.ledger_spent == 0 and state.requests_this_window == 0


# ---------------------------------------------------------------------------
# 2. checkpoint selection
# ---------------------------------------------------------------------------

def test_the_worst_observed_slate_is_covered_by_a_handful_of_requests() -> None:
    """2026-09-06: 15 games, 12:10 to 22:10 ET - the widest span in the data."""

    starts = _starts("2026-09-06", ["12:10", "13:05", "16:10", "18:40", "19:10", "22:10"])
    checkpoints = plan_checkpoints(starts)
    assert len(checkpoints) == 3
    for start in starts:
        assert any(planned_covers(c, decision_cutoff(start)) for c in checkpoints), start
    assert len(checkpoints) * MLB_POLICY.credit_cost == 3


def test_the_plan_does_not_move_when_the_clock_does() -> None:
    """The cover is a function of the slate, never of `now`.

    An earlier draft derived it relative to the current instant, which made
    every checkpoint land in the future (so a tick could never reach one) and
    re-anchored the cover on every tick (so the request count drifted).
    """

    starts = _starts("2026-09-06", ["12:10", "16:10", "19:10", "22:10"])
    first = plan_checkpoints(starts)
    for hour in (0, 6, 13, 18, 23):
        assert plan_checkpoints(starts) == first, hour


def test_a_request_that_fires_late_still_covers_what_it_was_credited_with() -> None:
    """A 5-minute tick cannot land on an instant, so the plan leaves slack."""

    starts = _starts("2026-09-06", ["12:10", "16:10", "19:10", "22:10"])
    for checkpoint in plan_checkpoints(starts):
        credited = [c for c in (decision_cutoff(s) for s in starts)
                    if planned_covers(checkpoint, c)]
        # Fire anywhere inside the tick slack and every credited cutoff is still
        # genuinely covered under the true contract.
        for late in range(0, CHECKPOINT_TICK_SLACK_MINUTES + 1):
            fired = checkpoint + timedelta(minutes=late)
            for cutoff in credited:
                assert covers(fired, cutoff), (checkpoint, late, cutoff)


def test_every_covered_game_is_observed_inside_its_own_freshness_window() -> None:
    starts = _starts("2026-09-06", ["12:10", "16:10", "19:10", "22:10"])
    for checkpoint in plan_checkpoints(starts):
        for start in starts:
            cutoff = decision_cutoff(start)
            if not planned_covers(checkpoint, cutoff):
                continue
            age = (cutoff - checkpoint).total_seconds() / 60
            assert 0 < age <= MAX_QUOTE_STALENESS_MINUTES
            assert age >= INGEST_HEADROOM_MINUTES  # time to fetch, store and be read


def test_collection_stops_after_the_decision_cutoff() -> None:
    """Once a checkpoint is past its grace, no credit is spent on it."""

    starts = _starts("2026-09-06", ["13:00"])
    plan = build_sport_plan(MLB_POLICY, starts, now=_starts("2026-09-06", ["06:00"])[0])
    checkpoint = plan.checkpoints[0]
    assert due_checkpoint(plan, now=checkpoint, last_success_at=None) is not None
    late = checkpoint + timedelta(minutes=CHECKPOINT_GRACE_MINUTES + 1)
    assert due_checkpoint(plan, now=late, last_success_at=None) is None
    # After the cutoff nothing is outstanding, so the day costs nothing more.
    after = build_sport_plan(MLB_POLICY, starts, now=decision_cutoff(starts[0]) + timedelta(minutes=1))
    assert after.projected_credits == 0


def test_an_inactive_sport_plans_nothing() -> None:
    plan = build_sport_plan(NFL_POLICY, [], now=datetime(2026, 9, 6, 12, 0, tzinfo=ET))
    assert plan.checkpoints == [] and plan.projected_credits == 0
    assert due_checkpoint(plan, now=datetime(2026, 9, 6, 12, 0, tzinfo=ET),
                          last_success_at=None) is None


def test_mlb_costs_one_credit_and_nfl_three() -> None:
    """The credit cost IS the market count: V2.1 can only act on MONEYLINE."""

    assert MLB_POLICY.markets == ("h2h",) and MLB_POLICY.credit_cost == 1
    assert NFL_POLICY.credit_cost == 3 and NFL_POLICY.priority > MLB_POLICY.priority


# ---------------------------------------------------------------------------
# 3. deduplication
# ---------------------------------------------------------------------------

def test_a_served_checkpoint_is_not_served_twice() -> None:
    starts = _starts("2026-09-06", ["13:00", "20:00"])
    now = datetime(2026, 9, 6, 6, 0, tzinfo=ET)
    plan = build_sport_plan(MLB_POLICY, starts, now=now)
    first = plan.checkpoints[0]

    due = due_checkpoint(plan, now=first, last_success_at=None)
    assert due is not None and due.checkpoint == first

    # The same tick a minute later, with the request already made, spends nothing.
    served = due_checkpoint(
        plan, now=first + timedelta(minutes=1), last_success_at=first + timedelta(seconds=5),
    )
    assert served is None


def test_a_five_minute_tick_actually_reaches_every_checkpoint() -> None:
    """The regression that matters: a tick lands on a window, never an instant.

    Replays a real slate through a real 5-minute scheduler at several phase
    offsets and asserts every game ends up covered by a request that actually
    fired. The earlier instant-equality rule fired zero times at every offset
    but one.
    """

    starts = _starts("2026-09-06", ["12:10", "13:05", "16:10", "18:40", "19:10", "22:10"])
    day = datetime(2026, 9, 6, 0, 0, tzinfo=ET)
    for offset_seconds in (0, 37, 123, 271, 299):
        plan = build_sport_plan(MLB_POLICY, starts, now=day)
        served: list[datetime] = []
        last_success = None
        tick = day + timedelta(seconds=offset_seconds)
        while tick < day + timedelta(days=1):
            if due_checkpoint(plan, now=tick, last_success_at=last_success) is not None:
                served.append(tick)      # the request happens AT the tick
                last_success = tick
            tick += timedelta(minutes=5)
        assert served, f"no checkpoint ever fired at offset {offset_seconds}s"
        for start in starts:
            assert any(covers(fired, decision_cutoff(start)) for fired in served), (
                offset_seconds, start
            )
        assert len(served) <= 4


def test_a_missed_checkpoint_is_abandoned_rather_than_fired_late() -> None:
    starts = _starts("2026-09-06", ["13:00", "20:00"])
    now = datetime(2026, 9, 6, 6, 0, tzinfo=ET)
    plan = build_sport_plan(MLB_POLICY, starts, now=now)
    first = plan.checkpoints[0]
    # Well past its grace period: the credit would no longer help that game.
    assert due_checkpoint(plan, now=first + timedelta(hours=2), last_success_at=None) is None


def test_checkpoint_state_is_derived_from_the_ledger_so_a_restart_changes_nothing() -> None:
    starts = _starts("2026-09-06", ["13:00", "20:00"])
    now = datetime(2026, 9, 6, 6, 0, tzinfo=ET)
    plan = build_sport_plan(MLB_POLICY, starts, now=now)
    first = plan.checkpoints[0]
    at = first + timedelta(minutes=2)
    before_restart = due_checkpoint(plan, now=at, last_success_at=first)
    after_restart = due_checkpoint(plan, now=at, last_success_at=first)
    assert before_restart is None and after_restart is None


# ---------------------------------------------------------------------------
# 4. fail-closed provider identity
# ---------------------------------------------------------------------------

def test_only_the_odds_api_is_governed_and_unknown_providers_are_not() -> None:
    assert provider_registry.GOVERNED_SOURCES == frozenset({"THE_ODDS_API"})
    assert provider_registry.is_governed("THE_ODDS_API") is True
    assert provider_registry.is_governed("SPORTSGAMEODDS") is False
    # A provider nobody registered cannot become governed by being absent.
    assert provider_registry.is_governed("SOME_NEW_FEED") is False
    with pytest.raises(provider_registry.ProviderAuthorityError):
        provider_registry.require_governed("SPORTSGAMEODDS")


def test_a_research_provider_can_never_share_a_bookmaker_with_the_governed_one() -> None:
    """The two-sided pairing keys on sportsbook_id, so namespacing makes a
    mixed-provider 'same-book' quote structurally impossible."""

    governed = provider_registry.identity("THE_ODDS_API").bookmaker_key("DraftKings")
    research = provider_registry.identity("SPORTSGAMEODDS").bookmaker_key("DraftKings")
    assert governed == "draftkings"
    assert research == "sgo:draftkings"
    assert governed != research


@pytest.mark.asyncio
async def test_the_fallback_adapter_refuses_while_disabled_or_unmapped(tmp_path) -> None:
    disabled = SportsGameOddsAdapter(enabled=False)
    assert disabled.readiness()["ready_to_enable"] is False
    with pytest.raises(FallbackProviderDisabled):
        await disabled.get_market_snapshot()

    # Turning the flag on is NOT sufficient: without a mapping bound to a real
    # response there is nothing to interpret a payload with.
    enabled = SportsGameOddsAdapter(enabled=True, mapping_path=tmp_path / "missing.json")
    with pytest.raises(FallbackMappingUnavailable):
        await enabled.get_market_snapshot()

    # A mapping that was never checked against a payload is still refused.
    unbound = tmp_path / "unbound.json"
    unbound.write_text(json.dumps({
        "schemaVersion": "AISP_SPORTSGAMEODDS_FIELD_MAPPING_V1",
        "paths": {field: field for field in (
            "event_id", "commence_time", "home_team", "away_team", "bookmaker_key",
            "bookmaker_title", "market_key", "outcome_name", "outcome_price_american",
            "price_observed_at")},
        "marketKeys": {"moneyline": "h2h"},
    }), encoding="utf-8")
    with pytest.raises(FallbackMappingUnavailable, match="never been checked"):
        SportsGameOddsAdapter(enabled=True, mapping_path=unbound).mapping()


# ---------------------------------------------------------------------------
# 5. one ingestion fixture
# ---------------------------------------------------------------------------

def _mapping(tmp_path: Path) -> Path:
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({
        "schemaVersion": "AISP_SPORTSGAMEODDS_FIELD_MAPPING_V1",
        "paths": {
            "event_id": "eventID",
            "commence_time": "status.startsAt",
            "home_team": "teams.home.names.long",
            "away_team": "teams.away.names.long",
            "bookmaker_key": "books.key",
            "bookmaker_title": "books.name",
            "market_key": "books.markets.marketName",
            "outcome_name": "books.markets.sides.label",
            "outcome_price_american": "books.markets.sides.americanOdds",
            "price_observed_at": "books.markets.observedAt",
        },
        "marketKeys": {"moneyline": "h2h"},
        "verifiedAgainstResponseSha256": "0" * 64,
        "note": "Illustrative shape for the ingestion test only.",
    }), encoding="utf-8")
    return path


def test_a_recorded_payload_maps_to_namespaced_research_rows(tmp_path) -> None:
    payload = [{
        "eventID": "sgo-evt-1",
        "status": {"startsAt": "2026-09-20T17:00:00Z"},
        "teams": {"home": {"names": {"long": "Seattle Mariners"}},
                  "away": {"names": {"long": "Boston Red Sox"}}},
        "books": [{
            "key": "DraftKings", "name": "DraftKings",
            "markets": [{
                "marketName": "moneyline",
                "observedAt": "2026-09-20T13:05:00Z",
                "sides": [
                    {"label": "Seattle Mariners", "americanOdds": -140},
                    {"label": "Boston Red Sox", "americanOdds": 120},
                ],
            }],
        }],
    }]
    adapter = SportsGameOddsAdapter(enabled=True, mapping_path=_mapping(tmp_path))
    response = adapter.normalize(payload, fetched_at=datetime(2026, 9, 20, 13, 6, tzinfo=UTC))

    assert response.envelope.source == "SPORTSGAMEODDS"
    assert response.envelope.metadata["governed"] is False
    assert response.envelope.metadata["credit_cost"] == 0
    event = response.envelope.data[0]
    assert event["source_event_id"] == "sgo-evt-1"
    book = event["bookmakers"][0]
    # Namespaced: it can never resolve to the governed provider's Sportsbook row.
    assert book["key"] == "sgo:draftkings"
    market = book["markets"][0]
    assert market["key"] == "h2h"
    assert market["last_update"] == datetime(2026, 9, 20, 13, 5, tzinfo=UTC)
    assert {o["price"] for o in market["outcomes"]} == {-140, 120}


def test_incomplete_evidence_is_dropped_rather_than_guessed(tmp_path) -> None:
    adapter = SportsGameOddsAdapter(enabled=True, mapping_path=_mapping(tmp_path))
    payload = [
        {   # a one-sided market can never satisfy a two-sided contract
            "eventID": "sgo-evt-2",
            "status": {"startsAt": "2026-09-20T17:00:00Z"},
            "teams": {"home": {"names": {"long": "A"}}, "away": {"names": {"long": "B"}}},
            "books": [{"key": "bk", "name": "Bk", "markets": [{
                "marketName": "moneyline", "observedAt": "2026-09-20T13:05:00Z",
                "sides": [{"label": "A", "americanOdds": -140}],
            }]}],
        },
        {   # a price with no observation time cannot be aged against the cutoff
            "eventID": "sgo-evt-3",
            "status": {"startsAt": "2026-09-20T17:00:00Z"},
            "teams": {"home": {"names": {"long": "A"}}, "away": {"names": {"long": "B"}}},
            "books": [{"key": "bk", "name": "Bk", "markets": [{
                "marketName": "moneyline",
                "sides": [{"label": "A", "americanOdds": -140},
                          {"label": "B", "americanOdds": 120}],
            }]}],
        },
        {   # an unmapped market is never guessed into a known one
            "eventID": "sgo-evt-4",
            "status": {"startsAt": "2026-09-20T17:00:00Z"},
            "teams": {"home": {"names": {"long": "A"}}, "away": {"names": {"long": "B"}}},
            "books": [{"key": "bk", "name": "Bk", "markets": [{
                "marketName": "some_new_market", "observedAt": "2026-09-20T13:05:00Z",
                "sides": [{"label": "A", "americanOdds": -140},
                          {"label": "B", "americanOdds": 120}],
            }]}],
        },
    ]
    response = adapter.normalize(payload)
    assert response.envelope.data == []
    assert response.envelope.metadata["events_dropped_incomplete"] == 3


# ---------------------------------------------------------------------------
# 6. the governed read path excludes a research provider (the decisive check)
# ---------------------------------------------------------------------------

def test_a_research_provider_row_never_reaches_the_governed_pricing_path(session, settings) -> None:
    """The read path that feeds V2.1 must return governed rows only.

    Seeded side by side on the SAME market outcome: one governed row and one
    research row that is NEWER. Without the filter the research row would win
    the latest-per-book reduction and could become one side of a "same-book
    two-sided quote".
    """

    from tests.test_fable51_latest_market_snapshots import _seed
    from app.models import OddsSnapshot, Sportsbook
    from app.services.read_service import ReadService

    game, fixture = _seed(session)
    governed_rows = ReadService(session, settings)._latest_market_snapshots(game.id)
    assert governed_rows, "the governed fixture must produce rows to begin with"

    research_book = Sportsbook(key="sgo:book-a", name="Book A via SportsGameOdds",
                               region="us", active=True)
    session.add(research_book)
    session.flush()
    later = max(_aware(s.observed_at) for _, _, s, _ in governed_rows) + timedelta(hours=1)
    session.add(OddsSnapshot(
        market_outcome_id=fixture["o_home"].id, sportsbook_id=research_book.id,
        observed_at=later, available_as_of=later, american_odds=+150, decimal_odds=2.5,
        source="SPORTSGAMEODDS", source_mode="LIVE",
    ))
    session.commit()

    after = ReadService(session, settings)._latest_market_snapshots(game.id)
    sources = {snapshot.source for _, _, snapshot, _ in after}
    assert sources == {"THE_ODDS_API"}
    assert "SPORTSGAMEODDS" not in sources
    # The newer research row changed nothing about what the policy can see.
    assert len(after) == len(governed_rows)


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=UTC)
