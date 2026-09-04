"""Governed pregame evidence state machines for the V2 practical policy.

Replaces the single binary starter interpretation with an auditable state
machine, and classifies lineup evidence for the moneyline-only pending
allowance. Every evaluation is point-in-time: only observations with
``available_as_of <= cutoff`` are consulted, and the exact observations and
rule results that produced each state are returned for persistence.

Nothing here reads a game outcome, calls a provider, or mutates any row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AvailabilityEvent, Game, LineupObservation, StarterObservation

# -- starter states ----------------------------------------------------------
STARTER_CONFIRMED = "CONFIRMED"
STARTER_PROBABLE_STABLE = "PROBABLE_STABLE"
STARTER_PROBABLE_UNSTABLE = "PROBABLE_UNSTABLE"
STARTER_CONFLICT = "CONFLICT"
STARTER_MISSING = "MISSING"

# -- lineup states -----------------------------------------------------------
LINEUP_CONFIRMED_COMPLETE = "CONFIRMED_COMPLETE"
LINEUP_ADEQUATE_PARTIAL = "ADEQUATE_PARTIAL"
LINEUP_PENDING_ALLOWED = "PENDING_ALLOWED_FOR_MONEYLINE"
LINEUP_MISSING_BLOCKING = "MISSING_BLOCKING"
LINEUP_CONFLICT = "CONFLICT"
LINEUP_LATE_SCRATCH_UNRESOLVED = "LATE_SCRATCH_UNRESOLVED"

# -- freshness verdicts (age measured at the evaluation reference) -----------
FRESHNESS_FRESH = "FRESH"
FRESHNESS_STALE = "STALE"
FRESHNESS_MISSING = "MISSING"
FRESHNESS_INVALID = "INVALID_FUTURE_DATED"

# -- lineup source availability (per team, distinct from the policy state) ---
LINEUP_SOURCE_NOT_YET_PUBLISHED = "EXPECTED_NOT_YET_PUBLISHED"
LINEUP_SOURCE_PARTIAL = "PARTIAL"
LINEUP_SOURCE_AVAILABLE = "AVAILABLE"
LINEUP_SOURCE_CONFIRMED = "CONFIRMED"

# PROBABLE_STABLE contract constants (versioned via the policy checksum).
MIN_STABLE_OBSERVATIONS = 2
MIN_OBSERVATION_SPACING_MINUTES = 10
STARTER_OBSERVATION_CURRENT_MINUTES = 240
SIMULTANEOUS_CONFLICT_TOLERANCE_SECONDS = 120


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# -- evidence row loaders --------------------------------------------------
# Each loader consults the request's prefetched FunnelReadContext when one is
# attached to the session (set-based read model) and otherwise issues the
# original per-(game, team) query. Both paths apply the SAME point-in-time
# filter (available_as_of / received_at <= reference) and the same ordering
# (source time ascending, id as the stable tie-breaker), so the reduction is
# identical whichever path served the rows.

def _prefetch(session: Session):
    from app.services.funnel_read_context import attached_context

    return attached_context(session)


def _starter_rows_upto(session: Session, game: Game, team_id: Any, reference: datetime) -> list[StarterObservation]:
    ctx = _prefetch(session)
    if ctx is not None and game.id in ctx.games_by_id:
        return [o for o in ctx.starter_rows(game.id, team_id) if _aware(o.available_as_of) <= reference]
    return list(session.scalars(
        select(StarterObservation).where(
            StarterObservation.game_id == game.id,
            StarterObservation.team_id == team_id,
            StarterObservation.available_as_of <= reference,
        ).order_by(StarterObservation.available_as_of, StarterObservation.id)
    ))


def _lineup_latest_upto(session: Session, game: Game, team_id: Any, reference: datetime) -> LineupObservation | None:
    ctx = _prefetch(session)
    if ctx is not None and game.id in ctx.games_by_id:
        eligible = [o for o in ctx.lineup_rows(game.id, team_id) if _aware(o.available_as_of) <= reference]
        return eligible[-1] if eligible else None
    return session.scalar(select(LineupObservation).where(
        LineupObservation.game_id == game.id,
        LineupObservation.team_id == team_id,
        LineupObservation.available_as_of <= reference,
    ).order_by(LineupObservation.available_as_of.desc(), LineupObservation.id.desc()))


def _events_upto(session: Session, game: Game, reference: datetime) -> list[AvailabilityEvent]:
    ctx = _prefetch(session)
    if ctx is not None and game.id in ctx.games_by_id:
        return [e for e in ctx.events(game.id) if _aware(e.received_at) <= reference]
    return list(session.scalars(select(AvailabilityEvent).where(
        AvailabilityEvent.game_id == game.id,
        AvailabilityEvent.received_at <= reference,
    )))


@dataclass(frozen=True)
class StarterEvidence:
    """The governed starter state for one game side, with its audit trail.

    ``stable_since`` is the instant stability was FIRST established (the first
    observation at least MIN_OBSERVATION_SPACING_MINUTES after the identity
    streak began). Later identical observations strengthen and refresh the
    evidence — they never reset ``stable_since``.
    """

    state: str
    pitcher_id: str | None = None
    observation_count: int = 0
    stable_streak_count: int = 0
    streak_started_at: str | None = None
    stable_since: str | None = None
    latest_observed_at: str | None = None
    forecast_evidence_match: bool = False
    reasons: tuple[str, ...] = ()
    # Evidence detail (user-facing and audit): the source status the provider
    # actually reported, the source family, the evaluation reference instant
    # the freshness age was measured against, that age in minutes, and the
    # freshness verdict. ``reference_at`` is min(evaluation instant, cutoff) —
    # never a future cutoff.
    source_status: str | None = None
    source: str | None = None
    reference_at: str | None = None
    age_minutes_at_reference: float | None = None
    freshness: str = FRESHNESS_MISSING

    @property
    def eligible(self) -> bool:
        return self.state in (STARTER_CONFIRMED, STARTER_PROBABLE_STABLE)

    def as_json(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "pitcher_id": self.pitcher_id,
            "observation_count": self.observation_count,
            "stable_streak_count": self.stable_streak_count,
            "streak_started_at": self.streak_started_at,
            "stable_since": self.stable_since,
            "latest_observed_at": self.latest_observed_at,
            "forecast_evidence_match": self.forecast_evidence_match,
            "reasons": list(self.reasons),
            "source_status": self.source_status,
            "source": self.source,
            "reference_at": self.reference_at,
            "age_minutes_at_reference": self.age_minutes_at_reference,
            "freshness": self.freshness,
        }


def evaluation_reference(cutoff: datetime, as_of: datetime | None) -> datetime:
    """The instant evidence is reduced and aged against: min(as_of, cutoff).

    Evidence used for a decision must have been available at or before the
    decision reference. Before the cutoff the reference is the evaluation
    instant itself; at/after the cutoff it is the cutoff. Aging evidence
    against a cutoff that is still hours away would brand every observation
    stale merely because the game is far off.
    """
    cutoff = _aware(cutoff)
    if as_of is None:
        return cutoff
    return min(_aware(as_of), cutoff)


def starter_evidence(
    session: Session,
    game: Game,
    team_id: Any,
    *,
    cutoff: datetime,
    forecast_generated_at: datetime | None,
    as_of: datetime | None = None,
) -> StarterEvidence:
    """Evaluate the governed starter evidence state for one team.

    ``cutoff`` is the final decision cutoff; ``as_of`` is the evaluation
    instant (defaults to the cutoff). All observations consulted satisfy
    ``available_as_of <= min(as_of, cutoff)`` and the freshness age is
    measured from that same reference — the governed source timestamp, never
    a persistence or render time.

    PROBABLE_STABLE requires (all point-in-time at the reference):
    exact canonical game + pitcher identities; the same pitcher in at least
    two observations spanning >= 10 minutes; the latest observation current at
    the reference; no competing identity after the streak began; no
    simultaneous provider conflict; no unresolved availability event; a
    forecast generated after the stable identity was established; and the
    game not yet started.
    """

    cutoff = _aware(cutoff)
    reference = evaluation_reference(cutoff, as_of)
    reasons: list[str] = []
    obs = _starter_rows_upto(session, game, team_id, reference)
    if not obs:
        return StarterEvidence(state=STARTER_MISSING, reasons=("No governed starter observation exists",),
                               reference_at=reference.isoformat(), freshness=FRESHNESS_MISSING)

    latest = obs[-1]
    latest_at = _aware(latest.available_as_of)
    age = reference - latest_at
    age_minutes = round(age.total_seconds() / 60.0, 1)
    if age < timedelta(0):
        # Fail closed: a future-dated observation can never be "current".
        return StarterEvidence(
            state=STARTER_MISSING, observation_count=len(obs), latest_observed_at=latest_at.isoformat(),
            source=latest.source, source_status=latest.confirmation_state,
            reference_at=reference.isoformat(), age_minutes_at_reference=age_minutes,
            freshness=FRESHNESS_INVALID,
            reasons=("Latest observation is dated after the evaluation reference (future-dated evidence)",),
        )
    freshness = FRESHNESS_FRESH if age <= timedelta(minutes=STARTER_OBSERVATION_CURRENT_MINUTES) else FRESHNESS_STALE
    detail = {
        "source": latest.source, "source_status": latest.confirmation_state,
        "reference_at": reference.isoformat(), "age_minutes_at_reference": age_minutes,
        "freshness": freshness,
    }

    # Simultaneous provider conflict: distinct identities observed within the
    # tolerance window around the latest observation.
    window_start = latest_at - timedelta(seconds=SIMULTANEOUS_CONFLICT_TOLERANCE_SECONDS)
    simultaneous = {
        o.probable_pitcher_id for o in obs
        if o.probable_pitcher_id is not None and _aware(o.available_as_of) >= window_start
    }
    if len(simultaneous) > 1:
        return StarterEvidence(
            state=STARTER_CONFLICT,
            observation_count=len(obs),
            latest_observed_at=latest_at.isoformat(),
            reasons=("Competing pitcher identities observed simultaneously",),
            **detail,
        )

    identity = latest.confirmed_pitcher_id or latest.probable_pitcher_id
    if identity is None:
        return StarterEvidence(
            state=STARTER_MISSING,
            observation_count=len(obs),
            latest_observed_at=latest_at.isoformat(),
            reasons=("Latest observation names no canonical pitcher identity",),
            **detail,
        )

    if (latest.confirmation_state or "").upper() == "CONFIRMED":
        if freshness != FRESHNESS_FRESH:
            # A confirmed identity is still evidence with an age; a stale
            # confirmation is reported as stale, never silently current.
            return StarterEvidence(
                state=STARTER_PROBABLE_UNSTABLE, pitcher_id=str(identity), observation_count=len(obs),
                latest_observed_at=latest_at.isoformat(),
                reasons=(f"Latest observation is not current at the evaluation reference "
                         f"(older than {STARTER_OBSERVATION_CURRENT_MINUTES} minutes)",),
                **detail,
            )
        return StarterEvidence(
            state=STARTER_CONFIRMED,
            pitcher_id=str(identity),
            observation_count=len(obs),
            latest_observed_at=latest_at.isoformat(),
            **detail,
        )

    # Terminal streak: the maximal suffix of observations naming this pitcher.
    # Later identical observations EXTEND the streak forward; they never reset
    # its start, so stable_since is monotone once established.
    streak: list[StarterObservation] = []
    for o in reversed(obs):
        if o.probable_pitcher_id == latest.probable_pitcher_id and o.probable_pitcher_id is not None:
            streak.append(o)
        else:
            break
    streak.reverse()
    streak_start = _aware(streak[0].available_as_of) if streak else None
    # stable_since: the first observation >= the minimum spacing after the
    # streak began — the moment two adequately separated identical
    # observations first existed.
    stable_since: datetime | None = None
    if streak_start is not None:
        for o in streak[1:]:
            if _aware(o.available_as_of) - streak_start >= timedelta(minutes=MIN_OBSERVATION_SPACING_MINUTES):
                stable_since = _aware(o.available_as_of)
                break

    if latest.probable_pitcher_id is None:
        reasons.append("Latest observation is not a named probable pitcher")
    if len(streak) < MIN_STABLE_OBSERVATIONS:
        reasons.append(
            f"Only {len(streak)} consecutive observation(s) of the current pitcher "
            f"(need >= {MIN_STABLE_OBSERVATIONS})"
        )
    elif stable_since is None:
        reasons.append(
            f"Qualifying observations span under {MIN_OBSERVATION_SPACING_MINUTES} minutes"
        )
    if freshness != FRESHNESS_FRESH:
        reasons.append(
            f"Latest observation is not current at the evaluation reference "
            f"(older than {STARTER_OBSERVATION_CURRENT_MINUTES} minutes)"
        )
    # Forecast currency: the forecast must postdate the moment the identity was
    # first known (streak start) — equivalently, its input evidence carries the
    # same canonical starter. Repeated identical polls after generation never
    # invalidate it; only an identity change/conflict/scratch does.
    forecast_evidence_match = False
    if forecast_generated_at is None:
        reasons.append("No current forecast exists")
    elif streak_start is not None and _aware(forecast_generated_at) >= streak_start:
        forecast_evidence_match = True
    else:
        reasons.append("Current forecast predates the stable starter identity")
    unresolved = _unresolved_availability_events(
        session, game, forecast_generated_at, reference, kinds=STARTER_MATERIAL_EVENTS,
    )
    if unresolved:
        reasons.append(f"Unresolved availability event(s): {', '.join(sorted(unresolved))}")
    starts_at = _aware(game.starts_at)
    if starts_at is not None and reference >= starts_at:
        reasons.append("Game has started")

    state = STARTER_PROBABLE_STABLE if not reasons else STARTER_PROBABLE_UNSTABLE
    return StarterEvidence(
        state=state,
        pitcher_id=str(identity),
        observation_count=len(obs),
        stable_streak_count=len(streak),
        streak_started_at=streak_start.isoformat() if streak_start else None,
        stable_since=stable_since.isoformat() if stable_since else None,
        latest_observed_at=latest_at.isoformat(),
        forecast_evidence_match=forecast_evidence_match,
        reasons=tuple(reasons),
        **detail,
    )


# Only these event kinds are MATERIAL to each state machine. An identical
# repeated poll never emits an event (the pollers diff before emitting), and a
# lineup event must never destabilize the starter state (or vice versa) — that
# would be duplicate derived gating.
STARTER_MATERIAL_EVENTS = ("PROBABLE_PITCHER_CHANGED", "SCRATCH")
LINEUP_MATERIAL_EVENTS = ("LINEUP", "SCRATCH")


def _unresolved_availability_events(
    session: Session,
    game: Game,
    forecast_generated_at: datetime | None,
    cutoff: datetime,
    *,
    kinds: tuple[str, ...],
) -> set[str]:
    """Material events newer than the current forecast are unresolved: the
    rescore path regenerates the forecast on a material event, so a forecast
    older than the latest material event has not absorbed it."""

    rows = _events_upto(session, game, _aware(cutoff))
    material = [
        r for r in rows
        if any(kind in (r.event_type or "").upper() for kind in kinds)
    ]
    if not material:
        return set()
    if forecast_generated_at is None:
        return {r.event_type for r in material}
    fga = _aware(forecast_generated_at)
    return {r.event_type for r in material if _aware(r.received_at) > fga}


@dataclass(frozen=True)
class LineupEvidence:
    """The governed lineup state for a game at a cutoff, with its audit trail."""

    state: str
    home_status: str | None = None
    away_status: str | None = None
    home_complete: bool = False
    away_complete: bool = False
    reasons: tuple[str, ...] = ()
    # Per-team SOURCE availability, distinct from the policy state: a lineup
    # that MLB has not published yet is EXPECTED_NOT_YET_PUBLISHED, never
    # collapsed into a generic "missing".
    home_observed_at: str | None = None
    away_observed_at: str | None = None
    home_source_state: str = LINEUP_SOURCE_NOT_YET_PUBLISHED
    away_source_state: str = LINEUP_SOURCE_NOT_YET_PUBLISHED
    source: str | None = None
    reference_at: str | None = None

    @property
    def eligible_for_moneyline(self) -> bool:
        return self.state in (LINEUP_CONFIRMED_COMPLETE, LINEUP_ADEQUATE_PARTIAL, LINEUP_PENDING_ALLOWED)

    @property
    def penalized(self) -> bool:
        return self.state == LINEUP_PENDING_ALLOWED

    def as_json(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "home_status": self.home_status,
            "away_status": self.away_status,
            "home_complete": self.home_complete,
            "away_complete": self.away_complete,
            "reasons": list(self.reasons),
            "home_observed_at": self.home_observed_at,
            "away_observed_at": self.away_observed_at,
            "home_source_state": self.home_source_state,
            "away_source_state": self.away_source_state,
            "source": self.source,
            "reference_at": self.reference_at,
        }


@dataclass(frozen=True)
class LineupContext:
    """The exact conditions the pending-lineup allowance may consider.

    Deliberately decoupled from the starter, quote, and cutoff gates: those are
    evaluated independently and must never block the lineup state a second
    time (duplicate derived gating)."""

    market_is_moneyline: bool
    dependency_allows_pending: bool
    pregame: bool = True


def lineup_evidence(
    session: Session,
    game: Game,
    home_team_id: Any,
    away_team_id: Any,
    *,
    cutoff: datetime,
    forecast_generated_at: datetime | None,
    context: LineupContext,
    as_of: datetime | None = None,
) -> LineupEvidence:
    cutoff = _aware(cutoff)
    reference = evaluation_reference(cutoff, as_of)

    def latest_for(team_id: Any) -> LineupObservation | None:
        # Newest eligible observation at the reference — never a later one.
        return _lineup_latest_upto(session, game, team_id, reference)

    home = latest_for(home_team_id)
    away = latest_for(away_team_id)
    home_status = home.lineup_status if home else None
    away_status = away.lineup_status if away else None
    home_complete = bool(home and home.is_complete)
    away_complete = bool(away and away.is_complete)

    def source_state(row: LineupObservation | None) -> str:
        if row is None:
            return LINEUP_SOURCE_NOT_YET_PUBLISHED
        if (row.lineup_status or "").upper() == "CONFIRMED" and row.is_complete:
            return LINEUP_SOURCE_CONFIRMED
        return LINEUP_SOURCE_AVAILABLE if row.is_complete else LINEUP_SOURCE_PARTIAL

    unresolved = _unresolved_availability_events(
        session, game, forecast_generated_at, reference, kinds=LINEUP_MATERIAL_EVENTS,
    )
    scratch = {e for e in unresolved if "SCRATCH" in e.upper()}
    material_change = {e for e in unresolved if "SCRATCH" not in e.upper()}
    base = {
        "home_status": home_status, "away_status": away_status,
        "home_complete": home_complete, "away_complete": away_complete,
        "home_observed_at": _aware(home.available_as_of).isoformat() if home else None,
        "away_observed_at": _aware(away.available_as_of).isoformat() if away else None,
        "home_source_state": source_state(home), "away_source_state": source_state(away),
        "source": (home or away).source if (home or away) else None,
        "reference_at": reference.isoformat(),
    }
    if scratch:
        return LineupEvidence(state=LINEUP_LATE_SCRATCH_UNRESOLVED,
                              reasons=(f"Unresolved late scratch: {', '.join(sorted(scratch))}",), **base)
    if material_change:
        # A lineup event landed after the current forecast: the affected game
        # must be refreshed once before the (now-changed) lineup evidence can
        # qualify. The earlier forecast and state remain immutable.
        return LineupEvidence(
            state=LINEUP_MISSING_BLOCKING,
            reasons=(f"Material lineup change after the current forecast: {', '.join(sorted(material_change))}",),
            **base,
        )

    both_confirmed_complete = (
        home is not None and away is not None
        and (home_status or "").upper() == "CONFIRMED" and home_complete
        and (away_status or "").upper() == "CONFIRMED" and away_complete
    )
    if both_confirmed_complete:
        return LineupEvidence(state=LINEUP_CONFIRMED_COMPLETE, **base)

    both_present = home is not None and away is not None and \
        (home_status or "").upper() not in ("", "MISSING") and (away_status or "").upper() not in ("", "MISSING")
    if both_present:
        return LineupEvidence(state=LINEUP_ADEQUATE_PARTIAL,
                              reasons=("Lineups present but not both confirmed-complete",), **base)

    # Ordinary not-yet-published lineup: the moneyline-only soft allowance.
    # This is the NORMAL morning state, never branded blocking by itself.
    blockers: list[str] = []
    if not context.market_is_moneyline:
        blockers.append("Pending lineup allowance applies to full-game moneyline only")
    if not context.dependency_allows_pending:
        blockers.append("Controlling-model lineup dependency does not permit a pending lineup")
    if not context.pregame:
        blockers.append("Game is no longer pregame")
    if blockers:
        return LineupEvidence(state=LINEUP_MISSING_BLOCKING, reasons=tuple(blockers), **base)
    return LineupEvidence(
        state=LINEUP_PENDING_ALLOWED,
        reasons=("Lineup pending; allowed for full-game moneyline with the audited evidence penalty",),
        **base,
    )


__all__ = [
    "LineupContext",
    "LineupEvidence",
    "StarterEvidence",
    "evaluation_reference",
    "lineup_evidence",
    "starter_evidence",
    "FRESHNESS_FRESH",
    "FRESHNESS_INVALID",
    "FRESHNESS_MISSING",
    "FRESHNESS_STALE",
    "LINEUP_SOURCE_AVAILABLE",
    "LINEUP_SOURCE_CONFIRMED",
    "LINEUP_SOURCE_NOT_YET_PUBLISHED",
    "LINEUP_SOURCE_PARTIAL",
    "MIN_OBSERVATION_SPACING_MINUTES",
    "MIN_STABLE_OBSERVATIONS",
    "STARTER_OBSERVATION_CURRENT_MINUTES",
    "STARTER_CONFIRMED",
    "STARTER_CONFLICT",
    "STARTER_MISSING",
    "STARTER_PROBABLE_STABLE",
    "STARTER_PROBABLE_UNSTABLE",
    "LINEUP_ADEQUATE_PARTIAL",
    "LINEUP_CONFIRMED_COMPLETE",
    "LINEUP_CONFLICT",
    "LINEUP_LATE_SCRATCH_UNRESOLVED",
    "LINEUP_MISSING_BLOCKING",
    "LINEUP_PENDING_ALLOWED",
]
