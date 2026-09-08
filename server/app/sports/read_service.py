"""TODAY_ALL_SPORTS_READ_MODEL_V1 assembler.

Composes each registered sport's adapter output into one all-sports view of the
operational day. Every value originates from persisted evidence through an
existing read service — this module performs no provider I/O, creates nothing,
and decides nothing.

Three invariants are enforced here rather than trusted from adapters:

* QUALIFIED TODAY contains ONLY selections whose sport can create official
  positions AND whose normalized state is FINAL_QUALIFIED. A research-shadow
  qualification can never reach it.
* A canonical selection identity (league + event + market + selection +
  policy/experiment identity) appears at most once across the qualified,
  awaiting, and provisional lists. Contradictory adapter output fails that
  sport closed instead of occupying multiple lanes.
* Final-qualified decisions and immutable official positions have distinct,
  reconciled counters. The legacy V1 aliases retain their original meanings.

There is deliberately NO cross-sport ranking: MLB probability edge and NFL
spread edge are not comparable without a validated portfolio contract, so
opportunities are grouped by sport and ranked only within a sport using that
sport's own governed ordering.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from importlib import import_module
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import local_today
from app.sports.contracts import (
    AWAITING_STATES,
    AvailabilityEntry,
    DataFreshness,
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


logger = logging.getLogger(__name__)

_ADAPTER_IMPORTS = {
    "MLB": ("app.sports.adapters.mlb", "MlbTodayAdapter"),
    "NFL": ("app.sports.adapters.nfl", "NflTodayAdapter"),
}


class SportTodayAdapter(Protocol):
    """Every sport adapter projects its own authority into a SportSection."""

    def build_section(self, event_date: date, *, now: datetime | None = None) -> SportSection: ...


def _adapter_factory(league: str) -> Any | None:
    """Resolve exactly one adapter lazily.

    Resolution is deliberately called from inside the per-sport fail-closed
    boundary. An import or attribute failure for one league therefore cannot
    prevent another league's persisted read from being reported.
    """

    target = _ADAPTER_IMPORTS.get(league)
    if target is None:
        return None
    module_name, class_name = target
    return getattr(import_module(module_name), class_name)


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
        requested = (league_filter or "ALL").strip().upper()
        sections: list[SportSection] = []
        for definition in active_leagues():
            league = str(definition.league_code)
            if requested not in ("ALL", league):
                continue
            try:
                factory = _adapter_factory(league)
                if factory is None:
                    # Fail closed: no adapter means we cannot claim to know this
                    # sport's day. Never report zeros as though it were healthy.
                    sections.append(self._unavailable_section(
                        definition,
                        f"No read adapter is registered for {league}; its day cannot be reported.",
                    ))
                    continue
                section = factory(self.session, self.settings).build_section(event_date, now=now)
                section = self._enforce_unique_selection_keys(definition, section)
                self._reconcile_official_position_count(section)
                sections.append(section)
            except Exception as exc:  # fail closed, never fabricate a healthy zero
                detail = str(exc).strip()
                reason = f"{league} read adapter failed: {type(exc).__name__}"
                if detail:
                    reason = f"{reason}: {detail}"
                logger.exception("%s", reason)
                sections.append(self._unavailable_section(
                    definition, reason,
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
        lane. Registry capability and exact position identity are required, so
        research-shadow output is structurally excluded even if an adapter
        mislabels it.
        """
        definition = get_league(str(item.league_code))
        return (
            definition is not None
            and definition.official_positions_supported
            and item.can_ever_fund_official_position
            and item.funding_state == FState.OFFICIAL_VIRTUAL_POSITION
            and bool(item.official_position_id)
        )

    def _reconcile_official_position_count(self, section: SportSection) -> None:
        """Reconcile the section counter against registry funding authority."""

        section.official_position_count = sum(
            item.decision_state in QUALIFIED_STATES and self._can_fund(item)
            for item in section.selections
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

    @staticmethod
    def _selection_key(item: NormalizedSelection) -> tuple[str, str, str, str, str, str]:
        """Exact identity used for cross-lane uniqueness.

        Multiple markets from one event remain valid because market and
        selection are part of the key. Separate governed experiments/policies
        also remain distinct and are never pooled.
        """

        return (
            str(item.league_code),
            item.event_id,
            item.market_label or "",
            item.selection_label or "",
            item.experiment_id or "",
            item.policy_version or "",
        )

    def _enforce_unique_selection_keys(
        self, definition: Any, section: SportSection,
    ) -> SportSection:
        seen: set[tuple[str, str, str, str, str, str]] = set()
        for item in section.selections:
            key = self._selection_key(item)
            if key in seen:
                reason = (
                    f"{definition.league_code} read adapter emitted duplicate canonical "
                    f"selection identity {key!r}; the sport was failed closed."
                )
                logger.error("%s", reason)
                return self._unavailable_section(definition, reason)
            seen.add(key)
        return section

    def _overall(
        self, sections: list[SportSection], qualified: list[NormalizedSelection],
    ) -> OverallSummary:
        funded = [item for item in qualified if item.funding_state == FState.OFFICIAL_VIRTUAL_POSITION]
        refreshes = [
            timestamp
            for section in sections
            for timestamp in (section.latest_forecast_at, section.latest_market_at)
            if timestamp is not None
        ]
        return OverallSummary(
            final_qualified_decision_count=sum(
                section.final_qualified_decision_count for section in sections
            ),
            official_position_count=len(qualified),
            # Compatibility alias: this field has always counted actual
            # Qualified Today positions despite its ambiguous name.
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
        state, detail, running = self._odds_refresh_state()
        return DataFreshness(
            latest_forecast_at=max(forecasts) if forecasts else None,
            latest_market_at=max(markets) if markets else None,
            scheduler_running=scheduler_is_running(),
            provider_calls_made=0,
            automatic_odds_refresh_running=running,
            odds_refresh_state=state,
            odds_refresh_detail=detail,
        )

    def _odds_refresh_state(self) -> tuple[str, str | None, bool]:
        """Whether sportsbook prices are actually being refreshed, and why not.

        Read-only: it reports the persisted guard state and configuration, and
        contacts no provider.
        """

        from app.jobs.odds_quota_recovery import (
            GUARD_KNOWN_BELOW_FLOOR,
            GUARD_MAY_HAVE_RENEWED_PROBE_COOLING_DOWN,
            GUARD_MAY_HAVE_RENEWED_PROBE_REQUIRED,
            GUARD_NO_READING,
            quota_state,
        )
        from app.jobs.scheduler import scheduler_is_running

        state = quota_state(
            self.session,
            floor=self.settings.odds_quota_floor,
            probe_after_hours=self.settings.odds_quota_probe_after_hours,
            probe_cooldown_hours=self.settings.odds_quota_probe_cooldown_hours,
            renewal_days=self.settings.odds_quota_renewal_days,
        )
        if state.guard_state in (
            GUARD_MAY_HAVE_RENEWED_PROBE_REQUIRED,
            GUARD_MAY_HAVE_RENEWED_PROBE_COOLING_DOWN,
        ):
            return (
                "QUOTA_MAY_HAVE_RENEWED_PROBE_REQUIRED",
                "Automatic odds refresh is not running. The last provider quota "
                f"reading ({state.remaining} remaining, floor {state.floor}) is "
                "older than the provider's quota period, so the balance may have "
                "renewed; one authorized probe would settle it.",
                False,
            )
        if state.guard_state == GUARD_KNOWN_BELOW_FLOOR:
            return (
                "QUOTA_BELOW_FLOOR",
                "Automatic odds refresh is not running: the provider last "
                f"reported {state.remaining} requests remaining, at or below the "
                f"configured floor of {state.floor}, so AISP stopped spending "
                "provider credit.",
                False,
            )
        if not self.settings.odds_polling_enabled:
            return (
                "POLLING_DISABLED",
                "Automatic odds refresh is not running: scheduled odds polling "
                "is disabled in this environment.",
                False,
            )
        if state.guard_state == GUARD_NO_READING:
            return (
                "NO_QUOTA_READING",
                "No provider quota reading has been recorded, so odds refresh "
                "cannot be confirmed as running.",
                False,
            )
        return ("RUNNING", None, scheduler_is_running())

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
