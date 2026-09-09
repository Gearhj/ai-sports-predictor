"""Per-event sportsbook quote coverage: state, cause, and what to do next.

WHY THIS EXISTS
---------------
The governing policy can only price a game that has a COMPLETE, SAME-BOOK,
TWO-SIDED MONEYLINE observed inside its own freshness window. Collection that
merely "runs" is not the objective; a game either has that quote in time or it
does not, and when it does not there is exactly one operational reason why.

This module answers three questions per game, from persisted rows only:

    Where is this event in its collection lifecycle?
    If it is not covered, WHY, precisely?
    What is the next request that would fix it, and when?

THE CONTRACT (measured from the code it serves)
-----------------------------------------------
    cutoff        = first pitch - FINAL_PREGAME_CUTOFF_MINUTES (15)
    usable quote  = one bookmaker, both MONEYLINE outcomes, both priced,
                    both observed at the same instant
    fresh enough  = cutoff - MAX_QUOTE_STALENESS_MINUTES (240) <= observed_at <= cutoff
    target        = observed_at within FRESH_TARGET_MINUTES (60) of the cutoff,
                    whenever quota permits

THE LIFECYCLE
-------------
    SCHEDULED                    too early to need a quote
    QUOTE_EXPECTED               inside the window, none captured yet
    COMPLETE_QUOTE_CAPTURED      a usable quote exists
    FINAL_FRESH_QUOTE_CAPTURED   a usable quote inside the 60-minute target
    RECOVERY_REQUIRED            close to the cutoff with no usable quote
    CUTOFF_MISSED                the cutoff passed without one

A state is derived, never stored: it is a function of the schedule, the
snapshots and the clock, so it cannot drift from what actually happened.

WHAT THIS MODULE DOES NOT DO
----------------------------
No I/O beyond reading. It never makes a provider request, never writes a row,
and never decides whether a request may be paid for -- that is
``odds_budget``. It says what is needed; the budget says what is affordable.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Game,
    GameStatus,
    League,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    ProviderRequestLedger,
    Sportsbook,
)
from app.services.odds_collection_plan import (
    FINAL_PREGAME_CUTOFF_MINUTES,
    MAX_QUOTE_STALENESS_MINUTES,
    decision_cutoff,
)

#: A quote this fresh at the cutoff is the target whenever quota permits. It is
#: an aspiration, never a gate: the policy's own bar remains 240 minutes and is
#: not tightened here.
FRESH_TARGET_MINUTES = 60

#: Coverage is re-checked this far before the cutoff. If nothing usable exists
#: by then there is still time for two more attempts.
COVERAGE_CHECK_MINUTES = 90
#: A priority recovery request is reserved here.
RECOVERY_PRIMARY_MINUTES = 45
#: One last controlled attempt, safely ahead of the 15-minute cutoff.
RECOVERY_FINAL_MINUTES = 25

# -- lifecycle -------------------------------------------------------------
STATE_SCHEDULED = "SCHEDULED"
STATE_QUOTE_EXPECTED = "QUOTE_EXPECTED"
STATE_COMPLETE = "COMPLETE_QUOTE_CAPTURED"
STATE_FINAL_FRESH = "FINAL_FRESH_QUOTE_CAPTURED"
STATE_RECOVERY = "RECOVERY_REQUIRED"
STATE_MISSED = "CUTOFF_MISSED"

# -- exact operational causes ----------------------------------------------
CAUSE_NONE = None
CAUSE_PROVIDER_NOT_CALLED = "PROVIDER_NOT_CALLED"
CAUSE_QUOTA_GUARD_BLOCKED = "QUOTA_GUARD_BLOCKED"
CAUSE_CHECKPOINT_MISSED = "SCHEDULER_CHECKPOINT_MISSED"
CAUSE_NO_EVENT = "PROVIDER_RETURNED_NO_EVENT"
CAUSE_NO_BOOKMAKER = "PROVIDER_RETURNED_NO_BOOKMAKER"
CAUSE_INCOMPLETE_MARKET = "INCOMPLETE_TWO_SIDED_MARKET"
CAUSE_IDENTITY_FAILED = "EVENT_IDENTITY_MAPPING_FAILED"
CAUSE_VALIDATION_FAILED = "RESPONSE_VALIDATION_FAILED"
CAUSE_PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
CAUSE_STALE_AT_CUTOFF = "QUOTE_CAPTURED_BUT_STALE_AT_CUTOFF"

CAUSE_EXPLANATIONS: dict[str, str] = {
    CAUSE_PROVIDER_NOT_CALLED: (
        "No provider request was made at any time that could have served this game's "
        "freshness window."
    ),
    CAUSE_QUOTA_GUARD_BLOCKED: (
        "A request was due inside the window but a quota guard refused it: the credit "
        "budget or the provider floor stopped the spend."
    ),
    CAUSE_CHECKPOINT_MISSED: (
        "Requests were made that day, but none landed inside this game's own freshness "
        "window. The checkpoint that should have covered it did not fire."
    ),
    CAUSE_NO_EVENT: (
        "A request landed inside the window, but the provider's payload contained no "
        "market for this game at all."
    ),
    CAUSE_NO_BOOKMAKER: (
        "The market exists for this game, but no bookmaker priced it inside the window."
    ),
    CAUSE_INCOMPLETE_MARKET: (
        "Prices were observed inside the window, but never both sides of the moneyline "
        "from one bookmaker at one instant. A one-sided price cannot be priced by policy."
    ),
    CAUSE_IDENTITY_FAILED: (
        "The provider returned an event that could not be matched to this canonical game, "
        "so its prices were not attributable."
    ),
    CAUSE_VALIDATION_FAILED: (
        "The provider responded but the payload failed validation, so nothing was trusted "
        "from it."
    ),
    CAUSE_PERSISTENCE_FAILED: (
        "A valid quote was received but could not be written; the request was spent and "
        "the evidence lost."
    ),
    CAUSE_STALE_AT_CUTOFF: (
        "A complete two-sided quote exists for this game, but every one of them was "
        "observed more than the freshness window before the cutoff."
    ),
}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class QuoteObservation:
    """One complete two-sided same-book moneyline, as actually observed."""

    observed_at: datetime
    sportsbook_id: Any
    book: str
    home_american: int
    away_american: int

    def age_minutes_at(self, instant: datetime) -> float:
        return (_aware(instant) - _aware(self.observed_at)).total_seconds() / 60.0


@dataclass
class EventCoverage:
    """Everything known about one game's quote coverage, and what to do next."""

    game_id: Any
    matchup: str
    starts_at: datetime
    cutoff: datetime
    state: str
    cause: str | None
    best_quote: QuoteObservation | None
    #: Complete two-sided quotes inside the freshness window, newest first.
    qualifying: list[QuoteObservation]
    #: Complete two-sided quotes that exist but fall outside the window.
    out_of_window: list[QuoteObservation]
    #: Any moneyline observation at all inside the window, complete or not.
    partial_observations: int
    next_action_at: datetime | None
    next_action: str | None

    @property
    def covered(self) -> bool:
        return self.state in (STATE_COMPLETE, STATE_FINAL_FRESH)

    @property
    def at_risk(self) -> bool:
        return self.state in (STATE_QUOTE_EXPECTED, STATE_RECOVERY)

    def age_at_cutoff(self) -> float | None:
        return self.best_quote.age_minutes_at(self.cutoff) if self.best_quote else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "game_id": str(self.game_id),
            "matchup": self.matchup,
            "starts_at_utc": _aware(self.starts_at).isoformat(),
            "cutoff_utc": _aware(self.cutoff).isoformat(),
            "state": self.state,
            "covered": self.covered,
            "at_risk": self.at_risk,
            "cause": self.cause,
            "cause_detail": CAUSE_EXPLANATIONS.get(self.cause) if self.cause else None,
            "best_quote": (
                {
                    "observed_at_utc": _aware(self.best_quote.observed_at).isoformat(),
                    "book": self.best_quote.book,
                    "home_american": self.best_quote.home_american,
                    "away_american": self.best_quote.away_american,
                    "age_minutes_at_cutoff": round(self.age_at_cutoff() or 0.0, 1),
                    "within_target": (self.age_at_cutoff() or 1e9) <= FRESH_TARGET_MINUTES,
                }
                if self.best_quote else None
            ),
            "qualifying_quotes": len(self.qualifying),
            "out_of_window_quotes": len(self.out_of_window),
            "partial_observations_in_window": self.partial_observations,
            "next_action": self.next_action,
            "next_action_at_utc": _aware(self.next_action_at).isoformat() if self.next_action_at else None,
        }


class QuoteCoverageService:
    """Derives per-event coverage, cause and next action from persisted rows."""

    def __init__(self, session: Session, settings: Any) -> None:
        self.session = session
        self.settings = settings

    # -- observations ------------------------------------------------------
    def _complete_quotes(self, game_ids: set[Any]) -> dict[Any, list[QuoteObservation]]:
        """Complete same-book two-sided moneylines, per game.

        "Complete" is the policy's own definition: one bookmaker, both outcomes
        of one MONEYLINE market, both priced, both observed at the SAME instant.
        A pair assembled from two different observation times was never quoted
        together and is not a quote.
        """

        if not game_ids:
            return {}
        rows = self.session.execute(
            select(
                Market.game_id, MarketOutcome.id, MarketOutcome.participant_team_id,
                OddsSnapshot.observed_at, OddsSnapshot.sportsbook_id,
                OddsSnapshot.american_odds, Sportsbook.name,
            )
            .join(MarketOutcome, MarketOutcome.market_id == Market.id)
            .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
            .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
            .where(
                Market.game_id.in_(game_ids),
                Market.market_type == MarketType.MONEYLINE,
                OddsSnapshot.american_odds.is_not(None),
                OddsSnapshot.source_mode == "LIVE",
            )
        ).all()

        # (game, book, instant) -> {outcome_id: (team, price)}
        grouped: dict[tuple[Any, Any, datetime], dict[Any, tuple[Any, int, str]]] = defaultdict(dict)
        for game_id, outcome_id, team_id, observed_at, book_id, price, book_name in rows:
            key = (game_id, book_id, _aware(observed_at))
            grouped[key][outcome_id] = (team_id, int(price), book_name)

        home_by_game = self._home_team_ids(game_ids)
        quotes: dict[Any, list[QuoteObservation]] = defaultdict(list)
        for (game_id, book_id, observed_at), sides in grouped.items():
            if len(sides) != 2:
                continue  # a one-sided price is not a two-sided quote
            home_team = home_by_game.get(game_id)
            home = next((v for v in sides.values() if v[0] == home_team), None)
            away = next((v for v in sides.values() if v[0] != home_team), None)
            if home is None or away is None:
                continue
            quotes[game_id].append(QuoteObservation(
                observed_at=observed_at, sportsbook_id=book_id, book=home[2],
                home_american=home[1], away_american=away[1],
            ))
        for values in quotes.values():
            values.sort(key=lambda q: q.observed_at, reverse=True)
        return quotes

    def _home_team_ids(self, game_ids: set[Any]) -> dict[Any, Any]:
        from app.models import GameTeam

        rows = self.session.execute(
            select(GameTeam.game_id, GameTeam.team_id)
            .where(GameTeam.game_id.in_(game_ids), GameTeam.is_home.is_(True))
        ).all()
        return {game_id: team_id for game_id, team_id in rows}

    def _any_moneyline_observations(self, game_ids: set[Any]) -> dict[Any, list[datetime]]:
        if not game_ids:
            return {}
        rows = self.session.execute(
            select(Market.game_id, OddsSnapshot.observed_at)
            .join(MarketOutcome, MarketOutcome.market_id == Market.id)
            .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
            .where(
                Market.game_id.in_(game_ids),
                Market.market_type == MarketType.MONEYLINE,
                OddsSnapshot.source_mode == "LIVE",
            )
        ).all()
        out: dict[Any, list[datetime]] = defaultdict(list)
        for game_id, observed_at in rows:
            out[game_id].append(_aware(observed_at))
        return out

    def _markets_present(self, game_ids: set[Any]) -> set[Any]:
        if not game_ids:
            return set()
        return set(self.session.scalars(
            select(Market.game_id).where(
                Market.game_id.in_(game_ids), Market.market_type == MarketType.MONEYLINE,
            )
        ))

    # -- provider activity -------------------------------------------------
    def _provider_activity(self, window_start: datetime, window_end: datetime) -> dict[str, Any]:
        """What the provider was asked for, and what happened, in a time window."""

        rows = list(self.session.scalars(
            select(ProviderRequestLedger).where(
                ProviderRequestLedger.provider == "THE_ODDS_API",
                ProviderRequestLedger.requested_at >= window_start,
                ProviderRequestLedger.requested_at <= window_end,
            ).order_by(ProviderRequestLedger.requested_at)
        ))
        succeeded = [r for r in rows if r.status == "SUCCEEDED"]
        failed = [r for r in rows if r.status == "FAILED"]
        return {
            "requests": len(rows),
            "succeeded": succeeded,
            "failed": failed,
            "any": bool(rows),
        }

    def _blocked_in_window(self, window_start: datetime, window_end: datetime) -> bool:
        """Did a quota guard refuse a request that was due in this window?"""

        from app.models import JobRun

        rows = self.session.scalars(
            select(JobRun).where(
                JobRun.job_name.in_((
                    "refresh-odds", "collect-odds-checkpoint", "refresh-nfl-shadow-odds",
                )),
                JobRun.started_at >= window_start,
                JobRun.started_at <= window_end,
            )
        )
        for run in rows:
            text = f"{run.error_message or ''} {run.result or ''}"
            if "quota" in text.lower() or "budget" in text.lower() or "floor" in text.lower():
                return True
        return False

    # -- the state machine -------------------------------------------------
    def _classify(
        self,
        *,
        game: Game,
        matchup: str,
        now: datetime,
        quotes: list[QuoteObservation],
        observations: list[datetime],
        has_market: bool,
    ) -> EventCoverage:
        starts_at = _aware(game.starts_at)
        cutoff = decision_cutoff(starts_at)
        window_start = cutoff - timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES)

        qualifying = [q for q in quotes if window_start <= q.observed_at <= cutoff]
        out_of_window = [q for q in quotes if q not in qualifying]
        in_window_observations = [o for o in observations if window_start <= o <= cutoff]
        best = qualifying[0] if qualifying else None

        def build(state: str, cause: str | None, action: str | None, at: datetime | None) -> EventCoverage:
            return EventCoverage(
                game_id=game.id, matchup=matchup, starts_at=starts_at, cutoff=cutoff,
                state=state, cause=cause, best_quote=best, qualifying=qualifying,
                out_of_window=out_of_window, partial_observations=len(in_window_observations),
                next_action_at=at, next_action=action,
            )

        # Covered. Distinguish "usable" from "usable and fresh to target".
        if best is not None:
            fresh = best.age_minutes_at(cutoff) <= FRESH_TARGET_MINUTES
            if fresh:
                return build(STATE_FINAL_FRESH, CAUSE_NONE, None, None)
            # Still usable; a later checkpoint may improve it while one remains.
            improve_at = cutoff - timedelta(minutes=RECOVERY_PRIMARY_MINUTES)
            return build(
                STATE_COMPLETE, CAUSE_NONE,
                "Optional freshening toward the 60-minute target" if now < improve_at else None,
                improve_at if now < improve_at else None,
            )

        # Not covered. Before the window opens there is nothing to be wrong yet.
        if now < window_start:
            return build(
                STATE_SCHEDULED, CAUSE_NONE, "First qualifying checkpoint", window_start,
            )

        cause = self._diagnose(
            window_start=window_start, cutoff=cutoff, has_market=has_market,
            in_window_observations=len(in_window_observations),
            out_of_window=len(out_of_window),
        )

        if now > cutoff:
            return build(STATE_MISSED, cause, None, None)

        # Inside the window and still fixable. The ladder decides what next.
        check_at = cutoff - timedelta(minutes=COVERAGE_CHECK_MINUTES)
        primary_at = cutoff - timedelta(minutes=RECOVERY_PRIMARY_MINUTES)
        final_at = cutoff - timedelta(minutes=RECOVERY_FINAL_MINUTES)
        if now < check_at:
            return build(STATE_QUOTE_EXPECTED, cause, "Scheduled coverage check", check_at)
        if now < primary_at:
            return build(STATE_RECOVERY, cause, "Priority recovery request", primary_at)
        if now < final_at:
            return build(STATE_RECOVERY, cause, "Final controlled recovery attempt", final_at)
        return build(STATE_RECOVERY, cause, "Final controlled recovery attempt", final_at)

    def _diagnose(
        self, *, window_start: datetime, cutoff: datetime, has_market: bool,
        in_window_observations: int, out_of_window: int,
    ) -> str:
        """The single exact reason this game has no usable quote.

        Ordered from the most specific evidence outward, so the cause names the
        furthest point the pipeline actually reached.
        """

        if in_window_observations:
            # Prices arrived; they just never formed a same-book pair.
            return CAUSE_INCOMPLETE_MARKET
        if out_of_window:
            # A real complete quote exists, only too early to count.
            return CAUSE_STALE_AT_CUTOFF

        activity = self._provider_activity(window_start, cutoff)
        if not activity["any"]:
            if self._blocked_in_window(window_start, cutoff):
                return CAUSE_QUOTA_GUARD_BLOCKED
            # Distinguish "we never asked at all" from "we asked, elsewhere".
            day_start = window_start - timedelta(hours=12)
            if self._provider_activity(day_start, cutoff)["any"]:
                return CAUSE_CHECKPOINT_MISSED
            return CAUSE_PROVIDER_NOT_CALLED
        if activity["succeeded"]:
            # A successful call landed in the window and this game still has
            # nothing: the payload either omitted the event or priced no book.
            return CAUSE_NO_BOOKMAKER if has_market else CAUSE_NO_EVENT
        if activity["failed"]:
            return CAUSE_VALIDATION_FAILED
        return CAUSE_QUOTA_GUARD_BLOCKED

    # -- public ------------------------------------------------------------
    def coverage(
        self,
        *,
        league_code: str = "MLB",
        slate_date: date | None = None,
        now: datetime | None = None,
    ) -> list[EventCoverage]:
        """Per-event coverage for one operational day, or the live horizon."""

        now = _aware(now) or datetime.now(UTC)
        query = (
            select(Game)
            .join(League, League.id == Game.league_id)
            .where(
                League.code == league_code.upper(),
                Game.source_mode == "LIVE",
                Game.status.in_((GameStatus.SCHEDULED, GameStatus.LIVE, GameStatus.FINAL)),
            )
        )
        if slate_date is not None:
            from app.core.time import local_day_bounds

            start, end = local_day_bounds(slate_date, self.settings.display_timezone)
            query = query.where(Game.starts_at >= start, Game.starts_at < end)
        else:
            query = query.where(
                Game.starts_at >= now - timedelta(hours=6),
                Game.starts_at <= now + timedelta(hours=36),
            )
        games = list(self.session.scalars(query.order_by(Game.starts_at)))
        if not games:
            return []

        game_ids = {g.id for g in games}
        quotes = self._complete_quotes(game_ids)
        observations = self._any_moneyline_observations(game_ids)
        markets = self._markets_present(game_ids)
        names = self._matchups(game_ids)

        return [
            self._classify(
                game=game, matchup=names.get(game.id, str(game.id)), now=now,
                quotes=quotes.get(game.id, []), observations=observations.get(game.id, []),
                has_market=game.id in markets,
            )
            for game in games
        ]

    def _matchups(self, game_ids: set[Any]) -> dict[Any, str]:
        from app.models import GameTeam, Team

        rows = self.session.execute(
            select(GameTeam.game_id, GameTeam.is_home, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id.in_(game_ids))
        ).all()
        sides: dict[Any, dict[str, str]] = defaultdict(dict)
        for game_id, is_home, abbreviation in rows:
            sides[game_id]["home" if is_home else "away"] = abbreviation
        return {
            game_id: f"{value.get('away', '?')} @ {value.get('home', '?')}"
            for game_id, value in sides.items()
        }

    def daily_report(
        self, *, league_code: str = "MLB", slate_date: date | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """The coverage report that proves whether each game met the requirement."""

        now = _aware(now) or datetime.now(UTC)
        events = self.coverage(league_code=league_code, slate_date=slate_date, now=now)
        by_state: dict[str, int] = defaultdict(int)
        by_cause: dict[str, int] = defaultdict(int)
        ages: list[float] = []
        for event in events:
            by_state[event.state] += 1
            if event.cause:
                by_cause[event.cause] += 1
            age = event.age_at_cutoff()
            if age is not None:
                ages.append(age)
        covered = [e for e in events if e.covered]
        within_target = [e for e in covered if (e.age_at_cutoff() or 1e9) <= FRESH_TARGET_MINUTES]
        return {
            "schema_version": "AISP_QUOTE_COVERAGE_REPORT_V1",
            "league": league_code.upper(),
            "slate_date": slate_date.isoformat() if slate_date else None,
            "generated_at_utc": now.isoformat(),
            "contract": {
                "cutoff_minutes_before_start": FINAL_PREGAME_CUTOFF_MINUTES,
                "freshness_window_minutes": MAX_QUOTE_STALENESS_MINUTES,
                "freshness_target_minutes": FRESH_TARGET_MINUTES,
                "quote_definition": (
                    "one bookmaker, both MONEYLINE outcomes, both priced, both observed at "
                    "the same instant"
                ),
            },
            "totals": {
                "events": len(events),
                "covered": len(covered),
                "within_freshness_target": len(within_target),
                "at_risk": len([e for e in events if e.at_risk]),
                "missed": by_state.get(STATE_MISSED, 0),
                "coverage_rate": round(len(covered) / len(events), 4) if events else None,
            },
            "quote_age_at_cutoff_minutes": {
                "median": round(sorted(ages)[len(ages) // 2], 1) if ages else None,
                "max": round(max(ages), 1) if ages else None,
                "min": round(min(ages), 1) if ages else None,
            },
            "by_state": dict(by_state),
            "by_cause": dict(by_cause),
            "cause_explanations": {k: v for k, v in CAUSE_EXPLANATIONS.items() if k in by_cause},
            "events": [event.as_dict() for event in events],
        }


__all__ = [
    "CAUSE_CHECKPOINT_MISSED",
    "CAUSE_EXPLANATIONS",
    "CAUSE_IDENTITY_FAILED",
    "CAUSE_INCOMPLETE_MARKET",
    "CAUSE_NO_BOOKMAKER",
    "CAUSE_NO_EVENT",
    "CAUSE_PERSISTENCE_FAILED",
    "CAUSE_PROVIDER_NOT_CALLED",
    "CAUSE_QUOTA_GUARD_BLOCKED",
    "CAUSE_STALE_AT_CUTOFF",
    "CAUSE_VALIDATION_FAILED",
    "COVERAGE_CHECK_MINUTES",
    "FRESH_TARGET_MINUTES",
    "RECOVERY_FINAL_MINUTES",
    "RECOVERY_PRIMARY_MINUTES",
    "STATE_COMPLETE",
    "STATE_FINAL_FRESH",
    "STATE_MISSED",
    "STATE_QUOTE_EXPECTED",
    "STATE_RECOVERY",
    "STATE_SCHEDULED",
    "EventCoverage",
    "QuoteCoverageService",
    "QuoteObservation",
]
