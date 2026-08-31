"""Frozen multi-sport vocabulary and the all-sports Today read contract.

Phase 1 read-layer only. Nothing here stores a value, renames stored history,
or changes a qualification rule: the canonical codes below are a *projection*
that sport adapters map existing authoritative state into. Where the database
already stores a compatible governed value (e.g. ``sports.code = 'NFL'``), the
adapter maps it — the stored row is never rewritten.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

SPORT_REGISTRY_VERSION = "SPORT_REGISTRY_V1"
TODAY_READ_MODEL_VERSION = "TODAY_ALL_SPORTS_READ_MODEL_V1"


class SportCode(StrEnum):
    BASEBALL = "BASEBALL"
    FOOTBALL = "FOOTBALL"
    BASKETBALL = "BASKETBALL"
    MMA = "MMA"


class LeagueCode(StrEnum):
    MLB = "MLB"
    NFL = "NFL"
    NBA = "NBA"
    WNBA = "WNBA"
    UFC = "UFC"


class ParticipantType(StrEnum):
    TEAM = "TEAM"
    INDIVIDUAL = "INDIVIDUAL"


class SportAvailabilityState(StrEnum):
    LIVE = "LIVE"
    RESEARCH_SHADOW = "RESEARCH_SHADOW"
    DEVELOPMENT = "DEVELOPMENT"
    PLANNED = "PLANNED"
    UNAVAILABLE = "UNAVAILABLE"


class NormalizedDailyDecisionState(StrEnum):
    """Cross-sport projection of each sport's authoritative decision state.

    A sport adapter maps its own governed states into these; it must never
    invent a state, and an unmappable value fails closed to NOT_EVALUATED.
    """

    AWAITING_FINAL_EVIDENCE = "AWAITING_FINAL_EVIDENCE"
    PROVISIONAL_EDGE = "PROVISIONAL_EDGE"
    WATCH = "WATCH"
    PASS = "PASS"
    FINAL_QUALIFIED = "FINAL_QUALIFIED"
    FINAL_NO_BET = "FINAL_NO_BET"
    NOT_EVALUATED = "NOT_EVALUATED"


class NormalizedFundingState(StrEnum):
    """Whether a selection is (or could be) funded by a governed experiment."""

    OFFICIAL_VIRTUAL_POSITION = "OFFICIAL_VIRTUAL_POSITION"
    ELIGIBLE_NOT_FUNDED = "ELIGIBLE_NOT_FUNDED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    MARKET_NOT_SUPPORTED = "MARKET_NOT_SUPPORTED"
    NOT_EVALUATED = "NOT_EVALUATED"


# States that may appear in QUALIFIED TODAY. Deliberately narrow: a raw model
# edge, a provisional edge, a watch item, a research-shadow selection, and an
# awaiting-final candidate are all excluded by construction.
QUALIFIED_STATES = frozenset({NormalizedDailyDecisionState.FINAL_QUALIFIED})
AWAITING_STATES = frozenset({NormalizedDailyDecisionState.AWAITING_FINAL_EVIDENCE})
PROVISIONAL_STATES = frozenset({
    NormalizedDailyDecisionState.PROVISIONAL_EDGE,
    NormalizedDailyDecisionState.WATCH,
})


class SportDefinition(BaseModel):
    """One immutable SPORT_REGISTRY_V1 entry."""

    sport_code: SportCode
    league_code: LeagueCode
    participant_type: ParticipantType
    availability: SportAvailabilityState
    display_name: str
    short_label: str
    # Whether this league may create an OFFICIAL virtual position in a governed
    # funding experiment during THIS phase. RESEARCH_SHADOW and PLANNED are
    # always false.
    official_positions_supported: bool = False
    official_positions_note: str = ""
    engine_label: str | None = None
    # Stored values this league may appear as in existing immutable history.
    # The adapter maps these; nothing is renamed in place.
    stored_sport_aliases: tuple[str, ...] = ()
    stored_league_aliases: tuple[str, ...] = ()
    # Only LIVE/RESEARCH_SHADOW leagues get a navigable filter in Phase 1.
    navigable: bool = False

    @property
    def key(self) -> str:
        return str(self.league_code)


class NormalizedSelection(BaseModel):
    """A sport-neutral projection of one governed daily selection."""

    sport_code: SportCode
    league_code: LeagueCode
    event_id: str
    event_label: str
    starts_at: datetime | None = None
    decision_state: NormalizedDailyDecisionState
    funding_state: NormalizedFundingState
    market_label: str | None = None
    selection_label: str | None = None
    line: float | None = None
    price_american: int | None = None
    sportsbook: str | None = None
    model_probability: float | None = None
    no_vig_probability: float | None = None
    adjusted_edge: float | None = None
    expected_value: float | None = None
    evidence_tier: str | None = None
    virtual_stake: float | None = None
    experiment_id: str | None = None
    experiment_name: str | None = None
    policy_version: str | None = None
    decision_timestamp: datetime | None = None
    final_decision_cutoff: datetime | None = None
    source_label: str | None = None
    model_status: str | None = None
    # Exact outstanding gates / reason, verbatim from the sport's own authority.
    outstanding_gates: list[str] = Field(default_factory=list)
    status_note: str | None = None
    can_ever_fund_official_position: bool = False
    # Within-sport rank from that sport's own governed ranking. There is NO
    # cross-sport score in Phase 1 — see the read service.
    sport_rank: int | None = None
    detail_href: str | None = None


class SportSection(BaseModel):
    """Per-sport rollup for the all-sports Today hub."""

    sport_code: SportCode
    league_code: LeagueCode
    display_name: str
    availability_state: SportAvailabilityState
    scheduled_event_count: int = 0
    valid_forecast_count: int = 0
    final_qualified_count: int = 0
    awaiting_final_evidence_count: int = 0
    provisional_count: int = 0
    watch_count: int = 0
    pass_count: int = 0
    open_position_count: int = 0
    settled_today_count: int = 0
    virtual_stake: float = 0.0
    data_health: float | None = None
    latest_forecast_at: datetime | None = None
    latest_market_at: datetime | None = None
    engine_label: str | None = None
    can_create_official_positions: bool = False
    # Exact reason when official positions are impossible for this sport.
    official_positions_blocked_reason: str | None = None
    degraded: bool = False
    degraded_reason: str | None = None
    selections: list[NormalizedSelection] = Field(default_factory=list)


class OverallSummary(BaseModel):
    final_qualified_position_count: int = 0
    total_virtual_stake: float = 0.0
    open_position_count: int = 0
    settled_today_count: int = 0
    sports_with_official_positions: list[str] = Field(default_factory=list)
    sports_with_events: list[str] = Field(default_factory=list)
    latest_complete_refresh: datetime | None = None
    stale_or_degraded_sport_count: int = 0


class DataFreshness(BaseModel):
    latest_forecast_at: datetime | None = None
    latest_market_at: datetime | None = None
    scheduler_running: bool = False
    provider_calls_made: int = 0  # always 0: public reads never call a provider


class AvailabilityEntry(BaseModel):
    sport_code: SportCode
    league_code: LeagueCode
    display_name: str
    availability: SportAvailabilityState
    navigable: bool
    note: str | None = None


class TodayAllSportsReadModel(BaseModel):
    """TODAY_ALL_SPORTS_READ_MODEL_V1.

    Assembled exclusively from persisted evidence via existing read services.
    """

    # The shared client provenance hook requires an explicit mode. This is a
    # persisted-read response; adapters never substitute demo or historical
    # rows, so a successful response is always LIVE even when every slate is
    # honestly empty.
    data_mode: Literal["live"] = "live"
    schema_version: str = TODAY_READ_MODEL_VERSION
    registry_version: str = SPORT_REGISTRY_VERSION
    operational_date: date
    display_timezone: str
    generated_at: datetime
    overall_summary: OverallSummary
    qualified_positions: list[NormalizedSelection] = Field(default_factory=list)
    awaiting_final_evidence: list[NormalizedSelection] = Field(default_factory=list)
    provisional_opportunities: list[NormalizedSelection] = Field(default_factory=list)
    sport_sections: list[SportSection] = Field(default_factory=list)
    data_freshness: DataFreshness = Field(default_factory=DataFreshness)
    availability: list[AvailabilityEntry] = Field(default_factory=list)
    # Explicit, machine-checkable statement that no universal cross-sport score
    # exists in this phase.
    cross_sport_ranking: str = "NOT_APPLICABLE_PHASE_1"
    notes: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AWAITING_STATES",
    "AvailabilityEntry",
    "DataFreshness",
    "LeagueCode",
    "NormalizedDailyDecisionState",
    "NormalizedFundingState",
    "NormalizedSelection",
    "OverallSummary",
    "PROVISIONAL_STATES",
    "ParticipantType",
    "QUALIFIED_STATES",
    "SPORT_REGISTRY_VERSION",
    "SportAvailabilityState",
    "SportCode",
    "SportDefinition",
    "SportSection",
    "TODAY_READ_MODEL_VERSION",
    "TodayAllSportsReadModel",
]
