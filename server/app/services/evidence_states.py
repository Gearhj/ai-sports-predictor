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

# PROBABLE_STABLE contract constants (versioned via the policy checksum).
MIN_STABLE_OBSERVATIONS = 2
MIN_OBSERVATION_SPACING_MINUTES = 10
STARTER_OBSERVATION_CURRENT_MINUTES = 240
SIMULTANEOUS_CONFLICT_TOLERANCE_SECONDS = 120


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class StarterEvidence:
    """The governed starter state for one game side, with its audit trail."""

    state: str
    pitcher_id: str | None = None
    observation_count: int = 0
    stable_streak_count: int = 0
    streak_started_at: str | None = None
    latest_observed_at: str | None = None
    reasons: tuple[str, ...] = ()

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
            "latest_observed_at": self.latest_observed_at,
            "reasons": list(self.reasons),
        }


def starter_evidence(
    session: Session,
    game: Game,
    team_id: Any,
    *,
    cutoff: datetime,
    forecast_generated_at: datetime | None,
) -> StarterEvidence:
    """Evaluate the governed starter evidence state for one team at a cutoff.

    PROBABLE_STABLE requires (all point-in-time at ``cutoff``):
    exact canonical game + pitcher identities; the same pitcher in at least
    two observations spanning >= 10 minutes; the latest observation current at
    the cutoff; no competing identity after the streak began; no simultaneous
    provider conflict; no unresolved availability event; a forecast generated
    after the stable identity was established; and the game not yet started.
    """

    cutoff = _aware(cutoff)
    reasons: list[str] = []
    obs = list(session.scalars(
        select(StarterObservation).where(
            StarterObservation.game_id == game.id,
            StarterObservation.team_id == team_id,
            StarterObservation.available_as_of <= cutoff,
        ).order_by(StarterObservation.available_as_of)
    ))
    if not obs:
        return StarterEvidence(state=STARTER_MISSING, reasons=("No governed starter observation exists",))

    latest = obs[-1]
    latest_at = _aware(latest.available_as_of)

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
        )

    identity = latest.confirmed_pitcher_id or latest.probable_pitcher_id
    if identity is None:
        return StarterEvidence(
            state=STARTER_MISSING,
            observation_count=len(obs),
            latest_observed_at=latest_at.isoformat(),
            reasons=("Latest observation names no canonical pitcher identity",),
        )

    if (latest.confirmation_state or "").upper() == "CONFIRMED":
        return StarterEvidence(
            state=STARTER_CONFIRMED,
            pitcher_id=str(identity),
            observation_count=len(obs),
            latest_observed_at=latest_at.isoformat(),
        )

    # Terminal streak: the maximal suffix of observations naming this pitcher.
    streak: list[StarterObservation] = []
    for o in reversed(obs):
        if o.probable_pitcher_id == latest.probable_pitcher_id and o.probable_pitcher_id is not None:
            streak.append(o)
        else:
            break
    streak.reverse()
    streak_start = _aware(streak[0].available_as_of) if streak else None

    if latest.probable_pitcher_id is None:
        reasons.append("Latest observation is not a named probable pitcher")
    if len(streak) < MIN_STABLE_OBSERVATIONS:
        reasons.append(
            f"Only {len(streak)} consecutive observation(s) of the current pitcher "
            f"(need >= {MIN_STABLE_OBSERVATIONS})"
        )
    elif (_aware(streak[-1].available_as_of) - streak_start) < timedelta(minutes=MIN_OBSERVATION_SPACING_MINUTES):
        reasons.append(
            f"Qualifying observations span under {MIN_OBSERVATION_SPACING_MINUTES} minutes"
        )
    if cutoff - latest_at > timedelta(minutes=STARTER_OBSERVATION_CURRENT_MINUTES):
        reasons.append(
            f"Latest observation is not current at the decision cutoff "
            f"(older than {STARTER_OBSERVATION_CURRENT_MINUTES} minutes)"
        )
    if forecast_generated_at is None:
        reasons.append("No current forecast exists")
    elif streak_start is not None and _aware(forecast_generated_at) < streak_start:
        reasons.append("Current forecast predates the stable starter identity")
    unresolved = _unresolved_availability_events(session, game, forecast_generated_at, cutoff)
    if unresolved:
        reasons.append(f"Unresolved availability event(s): {', '.join(sorted(unresolved))}")
    starts_at = _aware(game.starts_at)
    if starts_at is not None and cutoff >= starts_at:
        reasons.append("Game has started")

    state = STARTER_PROBABLE_STABLE if not reasons else STARTER_PROBABLE_UNSTABLE
    return StarterEvidence(
        state=state,
        pitcher_id=str(identity),
        observation_count=len(obs),
        stable_streak_count=len(streak),
        streak_started_at=streak_start.isoformat() if streak_start else None,
        latest_observed_at=latest_at.isoformat(),
        reasons=tuple(reasons),
    )


def _unresolved_availability_events(
    session: Session, game: Game, forecast_generated_at: datetime | None, cutoff: datetime,
) -> set[str]:
    """Events newer than the current forecast are unresolved: the rescore path
    regenerates the forecast on a material event, so a forecast older than the
    latest event has not absorbed it."""

    rows = list(session.scalars(select(AvailabilityEvent).where(
        AvailabilityEvent.game_id == game.id,
        AvailabilityEvent.received_at <= cutoff,
    )))
    if not rows:
        return set()
    if forecast_generated_at is None:
        return {r.event_type for r in rows}
    fga = _aware(forecast_generated_at)
    return {r.event_type for r in rows if _aware(r.received_at) > fga}


@dataclass(frozen=True)
class LineupEvidence:
    """The governed lineup state for a game at a cutoff, with its audit trail."""

    state: str
    home_status: str | None = None
    away_status: str | None = None
    home_complete: bool = False
    away_complete: bool = False
    reasons: tuple[str, ...] = ()

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
        }


@dataclass(frozen=True)
class LineupContext:
    """Everything §9 requires before a pending lineup may be soft-allowed."""

    market_is_moneyline: bool
    starters_eligible: bool
    quote_fresh_and_complete: bool
    forecast_current: bool
    before_final_cutoff: bool
    dependency_allows_pending: bool


def lineup_evidence(
    session: Session,
    game: Game,
    home_team_id: Any,
    away_team_id: Any,
    *,
    cutoff: datetime,
    forecast_generated_at: datetime | None,
    context: LineupContext,
) -> LineupEvidence:
    cutoff = _aware(cutoff)

    def latest_for(team_id: Any) -> LineupObservation | None:
        return session.scalar(select(LineupObservation).where(
            LineupObservation.game_id == game.id,
            LineupObservation.team_id == team_id,
            LineupObservation.available_as_of <= cutoff,
        ).order_by(LineupObservation.available_as_of.desc()))

    home = latest_for(home_team_id)
    away = latest_for(away_team_id)
    home_status = home.lineup_status if home else None
    away_status = away.lineup_status if away else None
    home_complete = bool(home and home.is_complete)
    away_complete = bool(away and away.is_complete)

    unresolved = _unresolved_availability_events(session, game, forecast_generated_at, cutoff)
    scratch = {e for e in unresolved if "SCRATCH" in e.upper()}
    base = {
        "home_status": home_status, "away_status": away_status,
        "home_complete": home_complete, "away_complete": away_complete,
    }
    if scratch:
        return LineupEvidence(state=LINEUP_LATE_SCRATCH_UNRESOLVED,
                              reasons=(f"Unresolved late scratch: {', '.join(sorted(scratch))}",), **base)

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

    # Pending/missing: the moneyline-only soft allowance (§9), context-gated.
    blockers: list[str] = []
    if not context.market_is_moneyline:
        blockers.append("Pending lineup allowance applies to full-game moneyline only")
    if not context.dependency_allows_pending:
        blockers.append("Controlling-model lineup dependency does not permit a pending lineup")
    if not context.starters_eligible:
        blockers.append("Starters are not CONFIRMED or PROBABLE_STABLE")
    if unresolved:
        blockers.append(f"Unresolved availability event(s): {', '.join(sorted(unresolved))}")
    if not context.forecast_current:
        blockers.append("Forecast is not current after the latest material availability event")
    if not context.quote_fresh_and_complete:
        blockers.append("Quote is not fresh and complete")
    if not context.before_final_cutoff:
        blockers.append("Past the final-pregame cutoff")
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
    "lineup_evidence",
    "starter_evidence",
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
