"""Multi-sport read layer (Phase 1).

A registry plus per-sport adapters that project each sport's OWN authoritative
state into a shared read contract. No adapter owns modelling, qualification, or
staking logic — those stay in the sport's governed services.
"""

from app.sports.contracts import (
    LeagueCode,
    NormalizedDailyDecisionState,
    NormalizedFundingState,
    ParticipantType,
    SPORT_REGISTRY_VERSION,
    SportAvailabilityState,
    SportCode,
    TODAY_READ_MODEL_VERSION,
    TodayAllSportsReadModel,
)
from app.sports.registry import (
    SPORT_REGISTRY,
    active_leagues,
    get_league,
    navigable_leagues,
    planned_leagues,
    registry_manifest,
    resolve_stored_identity,
)

__all__ = [
    "LeagueCode",
    "NormalizedDailyDecisionState",
    "NormalizedFundingState",
    "ParticipantType",
    "SPORT_REGISTRY",
    "SPORT_REGISTRY_VERSION",
    "SportAvailabilityState",
    "SportCode",
    "TODAY_READ_MODEL_VERSION",
    "TodayAllSportsReadModel",
    "active_leagues",
    "get_league",
    "navigable_leagues",
    "planned_leagues",
    "registry_manifest",
    "resolve_stored_identity",
]
