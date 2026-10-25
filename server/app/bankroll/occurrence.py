"""Which occurrence of an MLB game a paper position was placed on, and whether it was played.

A rained-out MLB game keeps its ``games`` row: MLB reschedules the same gamePk
and the projection simply moves ``starts_at`` to the makeup. A position placed
on the original occurrence must therefore not be settled on the makeup's
final. Sportsbooks void such a bet; AISP_MLB_POSTPONED_NOT_PLAYED_ON_ORIGINAL_ET_DATE_V1
does the same for a virtual position:

    A position whose game was POSTPONED (or CANCELED) and was not played on
    the Eastern calendar date of the start AISP recorded when it placed the
    position is VOID: stake returned, profit 0. A game merely delayed, or
    suspended and later completed, was played on that date and settles W/L.

Everything here is READ-ONLY and uses evidence AISP already stores:

* the original date ``D0`` comes from the decision's ``slate_date`` -- the
  Eastern date of the game's start when AISP decided, which the position path
  requires to equal the occurrence it funds -- corroborated by the latest
  schedule observation (``game_state_observations``) available at placement
  for THAT date (falling back to the latest one at all), ignoring
  postponed/canceled copies, and, for a producer forecast, the forecast's
  ``scheduled_start_utc``. If the sources that exist disagree the date is
  AMBIGUOUS and the rule never voids: the position settles the legacy way and
  the verdict is marked UNVERIFIED (a receipt in the settlement payload and,
  when the game was ever postponed or went final on another date, an
  operating alert). The governed correction can still void it once the owner
  names the original date.
* "played on D0" is a LIVE/FINAL schedule observation whose ``scheduled_at``
  falls on D0; "not played on D0" is a POSTPONED/CANCELED observation on D0
  with no play on D0. MLB keeps the original gameDate on the postponed copy and
  gives the makeup its own date, so the two never collide.

There is no open-ended "hold": evidence about the placement never changes
after the fact, so waiting could not resolve an ambiguity; it would only keep
the stake reserved. Every verdict is SETTLE, VOID or WAIT (not yet decidable:
the game is not final, or a postponed original date has not yet ended).

The Eastern date is always taken from an aware instant converted to
America/New_York: 20:00 ET is 00:00Z on the NEXT day, so ``.date()`` of a UTC
value would name the wrong slate. A naive value is read as UTC, the convention
of every ``_aware`` helper in this code base (SQLite returns naive values).
``slate_date`` is written in ``settings.display_timezone`` (America/New_York
in every deployment); were that ever changed, slate dates would disagree with
the Eastern schedule dates and the rule would fail safe to UNVERIFIED legacy
settlement, never to a void.

This mirrors research_exports.v18_market_pilot_outcomes.admitted_state (rule
AISP_CANONICAL_POSTPONED_OR_CANCELED_AT_SEALED_START), keyed on the Eastern
date rather than the exact sealed start.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Game, GameStateObservation, GameStatus

RULE_ID = "AISP_MLB_POSTPONED_NOT_PLAYED_ON_ORIGINAL_ET_DATE_V1"
ET = ZoneInfo("America/New_York")
#: A postponed occurrence is voided early -- before the makeup is played -- once
#: its original Eastern date has ended plus this grace. It returns the reserved
#: stake; it never voids anything on the original date itself.
EARLY_VOID_GRACE = timedelta(hours=3)

#: ``game_state_observations.status`` holds GameStatus VALUES (lower case).
PLAYED_STATES = (GameStatus.LIVE.value, GameStatus.FINAL.value)
UNPLAYED_STATES = (GameStatus.POSTPONED.value, GameStatus.CANCELED.value)

SETTLE = "SETTLE"
VOID = "VOID"
WAIT = "WAIT"

BASIS_SLATE_OBSERVATION = "DECISION_SLATE_DATE_WITH_PLACEMENT_SCHEDULE_OBSERVATION"
BASIS_OBSERVATION = "PLACEMENT_SCHEDULE_OBSERVATION"
BASIS_SLATE = "DECISION_SLATE_DATE"
BASIS_FORECAST = "FORECAST_SCHEDULED_START"
BASIS_AMBIGUOUS = "AMBIGUOUS"
BASIS_NONE = "NO_EVIDENCE"
#: The governed correction only: the owner named the original date because the
#: stored evidence was ambiguous. The occurrence rule is then evaluated on it.
BASIS_OWNER_PINNED = "OWNER_PINNED_ORIGINAL_DATE"

REASON_CANCELED = "CANCELED"
REASON_NO_HISTORY = "NO_SCHEDULE_HISTORY_LEGACY_SETTLEMENT"
REASON_UNKNOWN = "ORIGINAL_DATE_UNKNOWN_LEGACY_SETTLEMENT"
REASON_AMBIGUOUS = "ORIGINAL_DATE_AMBIGUOUS_LEGACY_SETTLEMENT"
REASON_AMBIGUOUS_POSTPONED = "ORIGINAL_DATE_AMBIGUOUS_GAME_WAS_POSTPONED_LEGACY_SETTLEMENT"
REASON_PLAYED = "PLAYED_ON_ORIGINAL_DATE"
REASON_FINAL_ON_ORIGINAL = "FINAL_ON_ORIGINAL_DATE"
REASON_NOT_FINAL = "NOT_FINAL"
REASON_PLAYED_ELSEWHERE = "POSTPONED_PLAYED_ON_ANOTHER_DATE"
REASON_DATE_PASSED = "POSTPONED_ORIGINAL_DATE_PASSED"
REASON_AWAITING = "POSTPONED_AWAITING_END_OF_ORIGINAL_DATE"
REASON_FINAL_WITHOUT_EVIDENCE = "FINAL_ON_ANOTHER_DATE_NO_ORIGINAL_DATE_EVIDENCE_LEGACY_SETTLEMENT"

#: SETTLE verdicts the rule could not verify against the original occurrence.
#: They settle exactly as before the rule existed, never silently: the
#: settlement payload lists them (``legacy_settled_unverified``).
UNVERIFIED_REASONS = frozenset({
    REASON_UNKNOWN, REASON_AMBIGUOUS, REASON_AMBIGUOUS_POSTPONED, REASON_FINAL_WITHOUT_EVIDENCE,
})
#: The unverified settlements that look like the 2026-10-16 incident class --
#: the game was postponed at some point, or went final on a date other than
#: the original -- also raise one operating alert per position.
REVIEW_REASONS = frozenset({REASON_AMBIGUOUS_POSTPONED, REASON_FINAL_WITHOUT_EVIDENCE})
#: Operating alerts are AuditEvent rows (the established alert channel), one
#: per position, never repeated. Mission Control lists recent ones as an issue.
REVIEW_ALERT_ACTION = "position.occurrence_review_required"
REVIEW_ALERT_ENTITY = "official_asp_position"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def et_date(value: datetime) -> date:
    """The Eastern calendar date of an instant (naive values are UTC)."""

    return _aware(value).astimezone(ET).date()


def et_day_bounds(day: date) -> tuple[datetime, datetime]:
    """The UTC span [start, end) of one Eastern calendar date."""

    start = datetime.combine(day, datetime.min.time(), tzinfo=ET)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _aware(value).astimezone(ET).isoformat() if value is not None else None


@dataclass(frozen=True)
class OriginalOccurrence:
    """The occurrence a position was placed on, from evidence available at placement."""

    et_date: date | None
    scheduled_at: datetime | None
    basis: str
    observation_id: Any = None
    slate_date: date | None = None
    forecast_et_date: date | None = None
    shadow_forecast_id: Any = None
    observation_et_date: date | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "et_date": self.et_date.isoformat() if self.et_date else None,
            "scheduled_at": _iso(self.scheduled_at),
            "basis": self.basis,
            "observation_id": str(self.observation_id) if self.observation_id else None,
            "observation_et_date": self.observation_et_date.isoformat() if self.observation_et_date else None,
            "slate_date": self.slate_date.isoformat() if self.slate_date else None,
            "forecast_et_date": self.forecast_et_date.isoformat() if self.forecast_et_date else None,
            "shadow_forecast_id": str(self.shadow_forecast_id) if self.shadow_forecast_id else None,
        }


@dataclass(frozen=True)
class OccurrenceVerdict:
    """SETTLE (on the game row's final), VOID or WAIT (not yet decidable)."""

    kind: str
    reason: str
    original: OriginalOccurrence
    postponement_observation_id: Any = None
    postponement_available_as_of: datetime | None = None
    postponement_source: str | None = None
    played_et_date: date | None = None
    current_et_date: date | None = None

    @property
    def postponement_related(self) -> bool:
        return self.postponement_observation_id is not None

    @property
    def unverified(self) -> bool:
        """A legacy settlement the rule could not check against the original occurrence."""

        return self.kind == SETTLE and self.reason in UNVERIFIED_REASONS

    @property
    def review_required(self) -> bool:
        """An unverified settlement of the incident class: raise one operating alert."""

        return self.kind == SETTLE and self.reason in REVIEW_REASONS

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": RULE_ID,
            "kind": self.kind,
            "reason": self.reason,
            "unverified": self.unverified,
            "original": self.original.as_dict(),
            "postponement_observation_id": (
                str(self.postponement_observation_id) if self.postponement_observation_id else None
            ),
            "postponement_available_as_of": _iso(self.postponement_available_as_of),
            "postponement_source": self.postponement_source,
            "played_et_date": self.played_et_date.isoformat() if self.played_et_date else None,
            "current_et_date": self.current_et_date.isoformat() if self.current_et_date else None,
        }

    def void_note(self) -> str:
        """The honest settlement note / adjustment reason for a postponement VOID."""

        original = self.original
        if self.played_et_date is not None:
            played = f"played {self.played_et_date.isoformat()} instead"
        elif self.current_et_date is not None and self.current_et_date != original.et_date:
            played = f"not yet played (currently scheduled {self.current_et_date.isoformat()})"
        else:
            played = "not yet played"
        return (
            f"{RULE_ID}: not played on its original ET date "
            f"{original.et_date.isoformat() if original.et_date else 'unknown'} "
            f"(scheduled {_iso(original.scheduled_at) or 'n/a'} at placement; basis {original.basis}: "
            f"observation {original.observation_id or 'none'}, "
            f"slate {original.slate_date.isoformat() if original.slate_date else 'none'}, "
            f"forecast {original.shadow_forecast_id or 'none'}); "
            f"{self.postponement_source or 'schedule'} postponed observation "
            f"{self.postponement_observation_id} available {_iso(self.postponement_available_as_of)}; "
            f"{played}. Virtual stake voided: stake returned, P&L 0."
        )


def _mode(game: Game) -> str:
    return getattr(game, "source_mode", None) or "LIVE"


def original_occurrence(
    session: Session, *, game: Game, placed_at: datetime, decision: Any = None,
) -> OriginalOccurrence:
    """The Eastern date of the occurrence the position was placed on.

    The decision's slate date names the occurrence (the position path only
    funds a decision whose slate date is the Eastern date of the game's start).
    The schedule observation that corroborates it is the latest non-postponed
    one available at placement FOR THAT DATE -- so a makeup listing published
    before placement (an early-announced rain-out) cannot re-date the bet --
    falling back to the latest non-postponed one at all. The producer
    forecast's scheduled start corroborates too. Any disagreement between the
    sources that exist is AMBIGUOUS (no date): the rule never voids on a guess.
    """

    placed = _aware(placed_at)
    slate = getattr(decision, "slate_date", None)
    base = (
        GameStateObservation.game_id == game.id,
        GameStateObservation.source_mode == _mode(game),
        GameStateObservation.available_as_of <= placed,
        GameStateObservation.status.not_in(UNPLAYED_STATES),
        GameStateObservation.is_postponed.is_(False),
    )
    newest_first = (
        GameStateObservation.available_as_of.desc(),
        GameStateObservation.observed_at.desc(),
        GameStateObservation.id.desc(),
    )
    observation = None
    if slate is not None:
        low, high = et_day_bounds(slate)
        observation = session.scalar(select(GameStateObservation).where(
            *base, GameStateObservation.scheduled_at >= low, GameStateObservation.scheduled_at < high,
        ).order_by(*newest_first).limit(1))
    if observation is None:
        observation = session.scalar(select(GameStateObservation).where(*base).order_by(*newest_first).limit(1))
    shadow_forecast_id = getattr(decision, "shadow_forecast_id", None)
    forecast_date = None
    if shadow_forecast_id is not None:
        from app.models.entities import ShadowForecast

        forecast = session.get(ShadowForecast, shadow_forecast_id)
        if forecast is not None and forecast.scheduled_start_utc is not None:
            forecast_date = et_date(forecast.scheduled_start_utc)
    observed_date = et_date(observation.scheduled_at) if observation is not None else None
    dates = {value for value in (observed_date, slate, forecast_date) if value is not None}
    common = {
        "scheduled_at": observation.scheduled_at if observation is not None else None,
        "observation_id": observation.id if observation is not None else None,
        "observation_et_date": observed_date,
        "slate_date": slate,
        "forecast_et_date": forecast_date,
        "shadow_forecast_id": shadow_forecast_id,
    }
    if not dates:
        return OriginalOccurrence(et_date=None, basis=BASIS_NONE, **common)
    if len(dates) > 1:
        return OriginalOccurrence(et_date=None, basis=BASIS_AMBIGUOUS, **common)
    if slate and observed_date:
        basis = BASIS_SLATE_OBSERVATION
    else:
        basis = BASIS_OBSERVATION if observed_date else (BASIS_SLATE if slate else BASIS_FORECAST)
    return OriginalOccurrence(et_date=dates.pop(), basis=basis, **common)


def _first_unplayed(session: Session, game: Game, now: datetime, *extra: Any) -> GameStateObservation | None:
    return session.scalar(select(GameStateObservation).where(
        GameStateObservation.game_id == game.id,
        GameStateObservation.source_mode == _mode(game),
        GameStateObservation.available_as_of <= now,
        or_(GameStateObservation.status.in_(UNPLAYED_STATES), GameStateObservation.is_postponed.is_(True)),
        *extra,
    ).order_by(GameStateObservation.available_as_of, GameStateObservation.id).limit(1))


def _evidence(unplayed: GameStateObservation | None, played_et: date | None,
              current: date | None) -> dict[str, Any]:
    return {
        "postponement_observation_id": unplayed.id if unplayed is not None else None,
        "postponement_available_as_of": unplayed.available_as_of if unplayed is not None else None,
        "postponement_source": unplayed.source if unplayed is not None else None,
        "played_et_date": played_et,
        "current_et_date": current,
    }


def occurrence_verdict(
    session: Session, *, position: Any, decision: Any, game: Game, now: datetime,
    pinned_et_date: date | None = None,
) -> OccurrenceVerdict:
    """Decide whether an OPEN position settles on the game row's final, voids or waits.

    SETTLE means "the game row's final is the occurrence this position was
    placed on" (or there is no evidence to say otherwise -- legacy behaviour,
    marked UNVERIFIED when the rule could not check it); the caller still
    requires a FINAL with scores before settling.

    ``pinned_et_date`` is for the governed correction only: an owner-named
    original date, used when (and only when) the stored evidence is ambiguous.
    """

    now = _aware(now)
    final = (game.status == GameStatus.FINAL and game.home_score is not None
             and game.away_score is not None)
    current = et_date(game.starts_at) if getattr(game, "starts_at", None) else None
    played_et = current if final else None
    if game.status == GameStatus.CANCELED:
        return OccurrenceVerdict(VOID, REASON_CANCELED, OriginalOccurrence(None, None, BASIS_NONE),
                                 current_et_date=current)
    has_history = session.scalar(select(GameStateObservation.id).where(
        GameStateObservation.game_id == game.id,
        GameStateObservation.source_mode == _mode(game),
    ).limit(1)) is not None
    if not has_history:
        return OccurrenceVerdict(SETTLE, REASON_NO_HISTORY, OriginalOccurrence(None, None, BASIS_NONE),
                                 played_et_date=played_et, current_et_date=current)
    original = original_occurrence(session, game=game, placed_at=position.placed_at, decision=decision)
    if original.et_date is None and pinned_et_date is not None:
        original = replace(original, et_date=pinned_et_date, basis=BASIS_OWNER_PINNED)
    if original.et_date is None:
        # Unresolvable from what AISP recorded at placement. Settle the legacy
        # way (no void on a guess), but never silently: the receipt names it,
        # and a game that was ever postponed raises an operating alert.
        unplayed = _first_unplayed(session, game, now)
        if unplayed is not None:
            reason = REASON_AMBIGUOUS_POSTPONED
        else:
            reason = REASON_AMBIGUOUS if original.basis == BASIS_AMBIGUOUS else REASON_UNKNOWN
        return OccurrenceVerdict(SETTLE if final else WAIT, reason if final else REASON_NOT_FINAL, original,
                                 **_evidence(unplayed, played_et, current))
    start, end = et_day_bounds(original.et_date)
    on_original_date = (
        GameStateObservation.scheduled_at >= start,
        GameStateObservation.scheduled_at < end,
    )
    played = session.scalar(select(GameStateObservation.id).where(
        GameStateObservation.game_id == game.id,
        GameStateObservation.source_mode == _mode(game),
        GameStateObservation.available_as_of <= now,
        *on_original_date,
        GameStateObservation.status.in_(PLAYED_STATES),
    ).limit(1))
    if played is not None:
        # Delays, suspensions and same-day makeups all leave a live/final
        # observation on the original date: the bet has action.
        return OccurrenceVerdict(SETTLE if final else WAIT, REASON_PLAYED if final else REASON_NOT_FINAL,
                                 original, played_et_date=played_et, current_et_date=current)
    unplayed = _first_unplayed(session, game, now, *on_original_date)
    if unplayed is not None:
        evidence = _evidence(unplayed, played_et, current)
        if final:
            if played_et == original.et_date:
                return OccurrenceVerdict(SETTLE, REASON_FINAL_ON_ORIGINAL, original, **evidence)
            return OccurrenceVerdict(VOID, REASON_PLAYED_ELSEWHERE, original, **evidence)
        if now >= end + EARLY_VOID_GRACE:
            return OccurrenceVerdict(VOID, REASON_DATE_PASSED, original, **evidence)
        return OccurrenceVerdict(WAIT, REASON_AWAITING, original, **evidence)
    if not final:
        return OccurrenceVerdict(WAIT, REASON_NOT_FINAL, original, current_et_date=current)
    if played_et == original.et_date:
        return OccurrenceVerdict(SETTLE, REASON_FINAL_ON_ORIGINAL, original,
                                 played_et_date=played_et, current_et_date=current)
    # Final on another date with neither play nor postponement evidence on the
    # original date (e.g. a game suspended before any live observation, or a
    # move AISP never saw). Without POSTPONED/CANCELED evidence the rule does
    # not void: legacy settlement, UNVERIFIED, with an operating alert.
    return OccurrenceVerdict(SETTLE, REASON_FINAL_WITHOUT_EVIDENCE, original,
                             played_et_date=played_et, current_et_date=current)


def record_review_alert(session: Session, *, position: Any, game: Any, verdict: OccurrenceVerdict,
                        now: datetime) -> bool:
    """One operating alert per position for an unverified settlement of the incident class.

    The production session does not autoflush, so the lookup flushes first and
    the new row is flushed at once: a second call in one transaction sees it.
    Returns True when a new alert was written.
    """

    from app.models import AuditEvent

    session.flush()
    if session.scalar(select(AuditEvent.id).where(
            AuditEvent.action == REVIEW_ALERT_ACTION,
            AuditEvent.entity_type == REVIEW_ALERT_ENTITY,
            AuditEvent.entity_id == position.id).limit(1)) is not None:
        return False
    session.add(AuditEvent(
        actor_user_id=None, action=REVIEW_ALERT_ACTION, entity_type=REVIEW_ALERT_ENTITY,
        entity_id=position.id, occurred_at=_aware(now),
        metadata_json={
            "position_id": str(position.id), "game_id": str(game.id),
            "next_step": ("Settled the legacy way because the original occurrence could not be verified. "
                          "If the owner decides this bet had no action, void it through the governed "
                          "postponement correction (naming the original Eastern date)."),
            **verdict.as_dict(),
        },
    ))
    session.flush()
    return True


__all__ = [
    "EARLY_VOID_GRACE", "ET", "OccurrenceVerdict", "OriginalOccurrence", "REVIEW_ALERT_ACTION", "RULE_ID",
    "SETTLE", "VOID", "WAIT", "et_date", "et_day_bounds", "occurrence_verdict", "original_occurrence",
    "record_review_alert",
]
