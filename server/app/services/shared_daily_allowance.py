"""One cross-sport daily allowance for every official AISP paper position (owner, 2026-10-10).

From its effective clock, ONE flat $100 per Eastern slate date replaces each
official paper sport's own aggregate daily ceiling (CFB's $50 amendment, the
2%-of-bankroll rules of MLB, NBA and the analytical-only NFL ceiling). Every
official stake whose game kicks off on that Eastern date counts, whatever its
sport or status: settling or voiding a position frees nothing. Per-position,
same-game, Kelly, edge, model, source, quote and cutoff rules are untouched;
this module only supplies the remaining daily room.

Community / House Divided wagers live in ``competition_wagers`` and browser
picks never reach the database, so neither is counted or limited here.

Atomicity: ``room()`` takes a transaction-scoped PostgreSQL advisory lock
before it reads the day's total. Every official position writer calls it in
the same transaction that inserts the position, so concurrent sport workers
serialize: the lock is held from the read through the insert until commit.
On other databases (the offline SQLite tests) the lock is a no-op.

Before the clock (setting unset or in the future) ``room()`` returns None and
every sport behaves exactly as before.

Priority (owner, 2026-10-16 21:20Z, AISP_SHARED_100_PRIORITY_MLB_NHL_V1): the
$100 is unchanged, but MLB and NHL games still waiting for their decision hold
their policy's maximum stake back from every other sport
(``shared_priority_reservation``). ``room()`` reports the hold beside the total.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

SHARED_DAILY_LIMIT_USD = 100.00
SETTING = "shared_daily_allowance_effective_from"
BASIS = "ALL_OFFICIAL_SPORTS_ENTERED_ON_EASTERN_SLATE_DATE_ANY_STATUS"
AUTHORIZED_ON = "2026-10-10"
#: Stable 64-bit advisory lock key ("AISPDAY1").
LOCK_KEY = 0x4149535044415931
ET = ZoneInfo("America/New_York")


def effective_from(settings: Any) -> datetime | None:
    raw = getattr(settings, SETTING, None) if settings is not None else None
    if raw in (None, ""):
        return None
    if isinstance(raw, str):
        try:
            raw = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(raw, datetime):
        return None
    return raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)


def is_effective(settings: Any, moment: datetime) -> bool:
    clock = effective_from(settings)
    moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return clock is not None and moment >= clock


def eastern_date(moment: datetime) -> date:
    aware = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
    return aware.astimezone(ET).date()


def _lock(session: Session) -> str:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("select pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})
        return "pg_advisory_xact_lock"
    return "none (non-PostgreSQL)"


#: Decision rows whose ``slate_date`` is, by construction, the game's Eastern
#: kickoff date at decision time (CFB/NBA ``slate_date(game)``; MLB uses the
#: display timezone America/New_York). NFL's ``slate_date`` is the UTC date of
#: the decision moment, so NFL needs the charged date recorded at entry.
EASTERN_KICKOFF_SLATE_SPORTS = frozenset({"CFB", "NBA", "BASEBALL_MLB"})


def _recorded_charge(*blobs: Any) -> date | None:
    """The charged slate date written into a decision's evidence at entry, if any."""

    for blob in blobs:
        shared = blob.get("shared_daily_allowance") if isinstance(blob, dict) else None
        if isinstance(shared, dict) and shared.get("slate_date"):
            try:
                return date.fromisoformat(str(shared["slate_date"]))
            except ValueError:
                return None
    return None


def charged_dates(*, sport: str | None, decision_slate: date | None, evidence: Any, staking_detail: Any,
                  placed_at: datetime | None) -> tuple[frozenset[date], bool]:
    """The immutable Eastern slate date(s) an entry is charged to, and whether that is ambiguous.

    Never reads the game's CURRENT kickoff, so a later schedule change cannot move
    a stake off the day it was charged to. In order:

    1. the slate date recorded in the decision at entry under this allowance;
    2. the decision's own ``slate_date`` when that is the Eastern kickoff date by
       construction (CFB, NBA, MLB);
    3. otherwise (NFL before the allowance, or an entry with no decision) the
       assignment is ambiguous: it is charged to EVERY retained candidate --
       the decision's slate date and the Eastern date of ``placed_at`` -- so an
       unknown never frees room. Ambiguity is reported, not resolved by guessing.
    """

    recorded = _recorded_charge(staking_detail, evidence)
    if recorded is not None:
        return frozenset({recorded}), False
    if decision_slate is not None and sport in EASTERN_KICKOFF_SLATE_SPORTS:
        return frozenset({decision_slate}), False
    candidates = {d for d in (decision_slate, eastern_date(placed_at) if placed_at else None) if d is not None}
    return frozenset(candidates), True


def entered_detail(session: Session, day: date) -> dict[str, Any]:
    """Every official paper stake charged to Eastern ``day``, any sport, any status.

    Flushes the session first, so entries added earlier in the same transaction
    (production sessions do not autoflush) are counted before the next
    admission is sized.
    """

    from app.models import OfficialAspPosition, ShadowExperimentDecision

    session.flush()
    lo = datetime(day.year, day.month, day.day, tzinfo=ET).astimezone(UTC) - timedelta(days=2)
    hi = lo + timedelta(days=5)
    rows = session.execute(
        select(OfficialAspPosition.stake, OfficialAspPosition.placed_at, ShadowExperimentDecision.sport,
               ShadowExperimentDecision.slate_date, ShadowExperimentDecision.evidence,
               ShadowExperimentDecision.staking_detail)
        .outerjoin(ShadowExperimentDecision,
                   ShadowExperimentDecision.id == OfficialAspPosition.shadow_decision_id)
        .where(((ShadowExperimentDecision.slate_date >= day - timedelta(days=1))
                & (ShadowExperimentDecision.slate_date <= day + timedelta(days=1)))
               | ((ShadowExperimentDecision.id.is_(None))
                  & (OfficialAspPosition.placed_at >= lo) & (OfficialAspPosition.placed_at < hi)))).all()
    total, ambiguous, counted = 0.0, 0, 0
    for stake, placed_at, sport, decision_slate, evidence, staking_detail in rows:
        dates, unclear = charged_dates(sport=sport, decision_slate=decision_slate, evidence=evidence,
                                       staking_detail=staking_detail, placed_at=placed_at)
        if day in dates:
            total += float(stake)
            counted += 1
            ambiguous += int(unclear)
    return {"entered_usd": round(total, 2), "entries": counted, "ambiguous_entries": ambiguous}


def entered_on(session: Session, day: date) -> float:
    return entered_detail(session, day)["entered_usd"]


#: Honest reasons for a value-qualified decision sized to $0 by this allowance
#: (audit item 13): never a no-bet / no-edge.
ROOM_EXHAUSTED = "SHARED_ALLOWANCE_EXHAUSTED"
ROOM_RESERVED = "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS"
BUDGET_REASONS = frozenset({ROOM_EXHAUSTED, ROOM_RESERVED})


def room(session: Session, settings: Any, *, day: date, moment: datetime, sport: Any = None,
         game_id: Any = None) -> dict[str, Any] | None:
    """The shared allowance for Eastern ``day`` at ``moment`` for ``sport``'s ``game_id``; None before the clock.

    Takes the cross-sport lock first, then reads the total and the priority
    reservations (``shared_priority_reservation``, owner 2026-10-16: MLB and NHL
    before CFB), so the caller must insert any position in this same
    transaction (the lock ends at commit). Under PostgreSQL READ COMMITTED every
    statement after the lock sees every writer that committed before it.

    ``room_usd`` = $100 - entered - reserved_usd (never below zero) is the ONLY
    figure a caller may stake against; ``exposure_counted_usd`` is entered +
    reserved, for callers that pass an exposure and a limit to the sizing rule.
    A caller that names no sport is treated as non-priority (most restrictive).
    """

    if not is_effective(settings, moment):
        return None
    from app.services import shared_priority_reservation as priority

    lock = _lock(session)
    detail = entered_detail(session, day)
    entered = detail["entered_usd"]
    held = priority.reservations(session, settings, day=day, moment=moment, sport=sport, game_id=game_id)
    reserved = held["reserved_usd"]
    before = round(max(0.0, SHARED_DAILY_LIMIT_USD - entered), 2)
    room_usd = round(max(0.0, SHARED_DAILY_LIMIT_USD - entered - reserved), 2)
    state = "EXHAUSTED" if before <= 0 else "RESERVED_FOR_PRIORITY_SPORTS" if room_usd <= 0 else "OPEN"
    return {"limit_usd": SHARED_DAILY_LIMIT_USD, "entered_usd": entered,
            "room_usd": room_usd, "slate_date": day.isoformat(),
            "reserved_usd": reserved, "reserved_games": held["reserved_games"],
            "exposure_counted_usd": round(entered + reserved, 2), "room_before_reservations_usd": before,
            "room_state": state, "priority": held["priority"], "requesting_sport": held["requesting_sport"],
            "requesting_game_id": held["requesting_game_id"],
            "priority_policy": {k: held[k] for k in ("policy", "owner_decision", "state", "priority_sports",
                                                     "ordering", "pending_priority_games")},
            "reservations": held["reservations"],
            "entries": detail["entries"], "ambiguous_entries": detail["ambiguous_entries"],
            "charge_rule": "IMMUTABLE_AT_ENTRY (recorded slate / Eastern-kickoff decision slate / all candidates)",
            "basis": BASIS, "effective_from": effective_from(settings).isoformat(),
            "authorized_on": AUTHORIZED_ON, "replaces": "each official sport's aggregate daily ceiling",
            "lock": lock}


def zero_room_reason(shared: dict[str, Any]) -> str:
    """Why this allowance left no room: fully entered, or the rest held for undecided priority games."""

    return ROOM_EXHAUSTED if float(shared.get("room_before_reservations_usd", shared.get("room_usd", 0.0))) <= 0 \
        else ROOM_RESERVED


def zero_room_detail(shared: dict[str, Any], sport: str) -> str:
    if zero_room_reason(shared) == ROOM_EXHAUSTED:
        return (f"The shared ${shared['limit_usd']:.0f} for {shared['slate_date']} is fully entered "
                f"(${shared['entered_usd']:.2f}); the value qualified, but no room remains for this {sport} decision.")
    return (f"The shared ${shared['limit_usd']:.0f} for {shared['slate_date']} has ${shared['entered_usd']:.2f} "
            f"entered and ${shared.get('reserved_usd', 0.0):.2f} held for {shared.get('reserved_games', 0)} MLB/NHL "
            f"game(s) still to be decided ({(shared.get('priority_policy') or {}).get('policy')}); the value "
            f"qualified, but no room remains for this {sport} decision. The hold releases as those games are "
            "decided or start.")


def budget_only(terms: dict[str, Any] | None, min_edge: float) -> bool:
    """True when the sizing rule's DAILY cap alone sized a value-qualified side to $0.

    Edge at or above the policy minimum, positive expected return, the daily cap
    at $0.00 (to the cent) and every other recorded limit still positive.
    """

    if not isinstance(terms, dict):
        return False
    limits = terms.get("limits")
    if not isinstance(limits, dict) or "daily_cap" not in limits:
        return False
    try:
        return (float(terms["edge"]) >= min_edge and float(terms["expected_return_per_dollar"]) > 0
                and round(float(limits["daily_cap"]), 2) <= 0
                and all(round(float(v), 2) > 0 for k, v in limits.items() if k != "daily_cap"))
    except (KeyError, TypeError, ValueError):
        return False


__all__ = ["AUTHORIZED_ON", "BASIS", "BUDGET_REASONS", "LOCK_KEY", "ROOM_EXHAUSTED", "ROOM_RESERVED", "SETTING",
           "SHARED_DAILY_LIMIT_USD", "budget_only", "eastern_date", "effective_from", "entered_on", "is_effective",
           "room", "zero_room_detail", "zero_room_reason"]
