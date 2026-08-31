"""TODAY_ALL_SPORTS_READ_MODEL_V1 assembler.

Composes each registered sport's adapter output into one all-sports view of the
operational day. Every value originates from persisted evidence through an
existing read service — this module performs no provider I/O, creates nothing,
and decides nothing.

Two invariants are enforced here rather than trusted from adapters:

* QUALIFIED TODAY contains ONLY selections whose sport can create official
  positions AND whose normalized state is FINAL_QUALIFIED. A research-shadow
  qualification can never reach it.
* Each event appears exactly once across the qualified/awaiting/provisional
  lists, because those lists are filtered views of the same per-event objects
  the sport sections hold — inconsistent duplicate states are impossible by
  construction.

There is deliberately NO cross-sport ranking: MLB probability edge and NFL
spread edge are not comparable without a validated portfolio contract, so
opportunities are grouped by sport and ranked only within a sport using that
sport's own governed ordering.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import local_today
from app.sports.contracts import (
    AWAITING_STATES,
    AvailabilityEntry,
    DataFreshness,
    NormalizedDailyDecisionState as DState,
    NormalizedFundingState as FState,
    NormalizedSelection,
    OverallSummary,
    PROVISIONAL_STATES,
    QUALIFIED_STATES,
    SportAvailabilityState,
    SportSection,
    TodayAllSportsReadModel,
)
from app.sports.registry import SPORT_REGISTRY, active_leagues, get_league


class SportTodayAdapter(Protocol):
    """Every sport adapter projects its own authority into a SportSection."""

    def build_section(self, event_date: date, *, now: datetime | None = None) -> SportSection: ...


def _adapter_factories() -> dict[str, Any]:
    """League code -> adapter class. A registered league with no entry here
    fails closed as UNAVAILABLE; it is never reported as a healthy zero."""

    from app.sports.adapters.mlb import MlbTodayAdapter
    from app.sports.adapters.nfl import NflTodayAdapter

    return {"MLB": MlbTodayAdapter, "NFL": NflTodayAdapter}


class TodayAllSportsReadService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def build(
        self,
        event_date: date | None = None,
        *,
        league_filter: str | None = None,
        now: datetime | None = None,
    ) -> TodayAllSportsReadModel:
        now = now or datetime.now(UTC)
        event_date = event_date or local_today(self.settings.display_timezone)
        factories = _adapter_factories()

        requested = (league_filter or "ALL").strip().upper()
        sections: list[SportSection] = []
        for definition in active_leagues():
            league = str(definition.league_code)
            if requested not in ("ALL", league):
                continue
            factory = factories.get(league)
            if factory is None:
                # Fail closed: no adapter means we cannot claim to know this
                # sport's day. Never report zeros as though it were healthy.
                sections.append(self._unavailable_section(
                    definition,
                    f"No read adapter is registered for {league}; its day cannot be reported.",
                ))
                continue
            try:
                sections.append(factory(self.session, self.settings).build_section(event_date, now=now))
            except Exception as exc:  # fail closed, never fabricate a healthy zero
                sections.append(self._unavailable_section(
                    definition, f"{league} read adapter failed: {type(exc).__name__}",
                ))

        selections = [item for section in sections for item in section.selections]
        qualified = [
            item for item in selections
            if item.decision_state in QUALIFIED_STATES and self._can_fund(item)
        ]
        awaiting = [item for item in selections if item.decision_state in AWAITING_STATES]
        provisional = [item for item in selections if item.decision_state in PROVISIONAL_STATES]
        # Rank ONLY within a sport, using each sport's own governed ordering
        # (already applied by the adapter). No universal score exists.
        self._apply_within_sport_ranks(qualified)
        self._apply_within_sport_ranks(awaiting)
        self._apply_within_sport_ranks(provisional)

        return TodayAllSportsReadModel(
            operational_date=event_date,
            display_timezone=self.settings.display_timezone,
            generated_at=now,
            overall_summary=self._overall(sections, qualified),
            qualified_positions=qualified,
            awaiting_final_evidence=awaiting,
            provisional_opportunities=provisional,
            sport_sections=sections,
            data_freshness=self._freshness(sections),
            availability=self._availability(),
            notes={
                "provider_calls": "none — every value is read from persisted evidence",
                "ranking": "within-sport only; cross-sport comparison is not validated in this phase",
            },
        )

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _can_fund(item: NormalizedSelection) -> bool:
        """Only an already-frozen governed position belongs in Qualified Today.

        ``ELIGIBLE_NOT_FUNDED`` remains useful sport-level evidence, but it is
        not a position and therefore cannot enter the cross-sport actionable
        lane. Research-shadow output is structurally excluded as well.
        """
        return (
            item.can_ever_fund_official_position
            and item.funding_state == FState.OFFICIAL_VIRTUAL_POSITION
        )

    @staticmethod
    def _apply_within_sport_ranks(items: list[NormalizedSelection]) -> None:
        by_sport: dict[str, int] = {}
        for item in items:
            key = str(item.league_code)
            by_sport[key] = by_sport.get(key, 0) + 1
            item.sport_rank = by_sport[key]

    @staticmethod
    def _unavailable_section(definition: Any, reason: str) -> SportSection:
        return SportSection(
            sport_code=definition.sport_code,
            league_code=definition.league_code,
            display_name=definition.display_name,
            availability_state=SportAvailabilityState.UNAVAILABLE,
            engine_label=definition.engine_label,
            can_create_official_positions=False,
            official_positions_blocked_reason=reason,
            degraded=True,
            degraded_reason=reason,
        )

    def _overall(self, sections: list[SportSection], qualified: list[NormalizedSelection]) -> OverallSummary:
        funded = [item for item in qualified if item.funding_state == FState.OFFICIAL_VIRTUAL_POSITION]
        refreshes = [
            timestamp
            for section in sections
            for timestamp in (section.latest_forecast_at, section.latest_market_at)
            if timestamp is not None
        ]
        return OverallSummary(
            final_qualified_position_count=len(qualified),
            total_virtual_stake=round(sum(item.virtual_stake or 0.0 for item in funded), 2),
            open_position_count=sum(s.open_position_count for s in sections),
            settled_today_count=sum(s.settled_today_count for s in sections),
            sports_with_official_positions=sorted({
                str(item.league_code) for item in funded
            }),
            sports_with_events=sorted({
                str(s.league_code) for s in sections if s.scheduled_event_count > 0
            }),
            latest_complete_refresh=max(refreshes) if refreshes else None,
            stale_or_degraded_sport_count=sum(1 for s in sections if s.degraded),
        )

    def _freshness(self, sections: list[SportSection]) -> DataFreshness:
        from app.jobs.scheduler import scheduler_is_running

        forecasts = [s.latest_forecast_at for s in sections if s.latest_forecast_at]
        markets = [s.latest_market_at for s in sections if s.latest_market_at]
        return DataFreshness(
            latest_forecast_at=max(forecasts) if forecasts else None,
            latest_market_at=max(markets) if markets else None,
            scheduler_running=scheduler_is_running(),
            provider_calls_made=0,
        )

    @staticmethod
    def _availability() -> list[AvailabilityEntry]:
        """Every registered sport, including PLANNED ones. PLANNED entries are
        declared for honesty but are never navigable."""
        return [
            AvailabilityEntry(
                sport_code=definition.sport_code,
                league_code=definition.league_code,
                display_name=definition.display_name,
                availability=definition.availability,
                navigable=definition.navigable and definition.availability in (
                    SportAvailabilityState.LIVE, SportAvailabilityState.RESEARCH_SHADOW,
                ),
                note=definition.official_positions_note or None,
            )
            for definition in SPORT_REGISTRY
        ]


def supported_league_filters() -> list[str]:
    """Filter values the API accepts in this phase (never a PLANNED sport)."""
    return ["ALL", *[str(entry.league_code) for entry in active_leagues()]]


def is_supported_filter(value: str | None) -> bool:
    if value is None:
        return True
    candidate = value.strip().upper()
    if candidate == "ALL":
        return True
    entry = get_league(candidate)
    return entry is not None and entry.availability in (
        SportAvailabilityState.LIVE, SportAvailabilityState.RESEARCH_SHADOW,
    )


__all__ = [
    "SportTodayAdapter",
    "TodayAllSportsReadService",
    "is_supported_filter",
    "supported_league_filters",
]
