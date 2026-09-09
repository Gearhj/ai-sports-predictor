"""SPORT_REGISTRY_V1 — the canonical multi-sport registry.

The single authority for which sports exist, how they map onto stored history,
and what each is permitted to do in this phase. PLANNED entries are declared so
the platform is honest about roadmap, but they are never navigable and never
produce a live forecast surface.

Adding a sport here does NOT activate it: a league only becomes navigable when
its availability is LIVE or RESEARCH_SHADOW *and* a read adapter is registered
for it. A registered league with no adapter fails closed as UNAVAILABLE.
"""

from __future__ import annotations

from app.sports.contracts import (
    LeagueCode,
    ParticipantType,
    SportAvailabilityState,
    SportCode,
    SportDefinition,
)

MLB = SportDefinition(
    sport_code=SportCode.BASEBALL,
    league_code=LeagueCode.MLB,
    participant_type=ParticipantType.TEAM,
    availability=SportAvailabilityState.LIVE,
    display_name="MLB",
    short_label="MLB",
    official_positions_supported=True,
    official_positions_note=(
        "Governed by the existing immutable experiment records (V1 strict control "
        "and the registered practical pregame challenger). Only a final governed "
        "qualification may create a virtual position."
    ),
    engine_label="AISP_BASELINE",
    # `sports.code` stores BASEBALL for MLB; leagues.code stores MLB.
    stored_sport_aliases=("BASEBALL",),
    stored_league_aliases=("MLB",),
    navigable=True,
    navigation_href="/mlb",
)

NFL = SportDefinition(
    sport_code=SportCode.FOOTBALL,
    league_code=LeagueCode.NFL,
    participant_type=ParticipantType.TEAM,
    availability=SportAvailabilityState.RESEARCH_SHADOW,
    display_name="NFL",
    short_label="NFL",
    official_positions_supported=False,
    official_positions_note=(
        "NFL runs as a frozen research shadow: imported Model Studio forecasts "
        "joined to governed odds. No multi-sport funding experiment covers NFL in "
        "this phase, so no NFL selection can create an official virtual position."
    ),
    engine_label="MODEL_STUDIO_NFL_M1",
    # Existing history stores the SPORT as 'NFL' (a league-shaped value). The
    # adapter maps it to FOOTBALL; the stored rows are never rewritten.
    stored_sport_aliases=("NFL", "FOOTBALL"),
    stored_league_aliases=("NFL",),
    navigable=True,
    navigation_href="/nfl",
)

NBA = SportDefinition(
    sport_code=SportCode.BASKETBALL,
    league_code=LeagueCode.NBA,
    participant_type=ParticipantType.TEAM,
    availability=SportAvailabilityState.PLANNED,
    display_name="NBA",
    short_label="NBA",
    official_positions_supported=False,
    official_positions_note="Planned. No model, no data acquisition, and no surface in this phase.",
    navigable=False,
)

WNBA = SportDefinition(
    sport_code=SportCode.BASKETBALL,
    league_code=LeagueCode.WNBA,
    participant_type=ParticipantType.TEAM,
    availability=SportAvailabilityState.PLANNED,
    display_name="WNBA",
    short_label="WNBA",
    official_positions_supported=False,
    official_positions_note="Planned. No model, no data acquisition, and no surface in this phase.",
    navigable=False,
)

UFC = SportDefinition(
    sport_code=SportCode.MMA,
    league_code=LeagueCode.UFC,
    participant_type=ParticipantType.INDIVIDUAL,
    availability=SportAvailabilityState.PLANNED,
    display_name="UFC",
    short_label="UFC",
    official_positions_supported=False,
    official_positions_note=(
        "Planned. Individual-participant modelling is not implemented; no surface in this phase."
    ),
    navigable=False,
)

SPORT_REGISTRY: tuple[SportDefinition, ...] = (MLB, NFL, NBA, WNBA, UFC)

_BY_LEAGUE = {str(entry.league_code): entry for entry in SPORT_REGISTRY}

# Availability states that may appear as an active surface/filter.
ACTIVE_STATES = (SportAvailabilityState.LIVE, SportAvailabilityState.RESEARCH_SHADOW)


def all_sports() -> tuple[SportDefinition, ...]:
    return SPORT_REGISTRY


def get_league(league_code: str) -> SportDefinition | None:
    return _BY_LEAGUE.get(str(league_code).upper())


def active_leagues() -> tuple[SportDefinition, ...]:
    """Leagues permitted a live surface/filter in this phase."""
    return tuple(entry for entry in SPORT_REGISTRY if entry.availability in ACTIVE_STATES)


def navigable_leagues() -> tuple[SportDefinition, ...]:
    """Leagues that may appear in navigation and sport filters."""
    return tuple(entry for entry in SPORT_REGISTRY if entry.navigable and entry.availability in ACTIVE_STATES)


def planned_leagues() -> tuple[SportDefinition, ...]:
    return tuple(entry for entry in SPORT_REGISTRY if entry.availability == SportAvailabilityState.PLANNED)


def resolve_stored_identity(stored_sport: str | None, stored_league: str | None) -> SportDefinition | None:
    """Map stored (sport, league) values onto a registry entry.

    Existing immutable rows use values such as ``sports.code = 'NFL'`` that do
    not match the frozen SportCode vocabulary. This resolves them without
    touching the stored data. Returns None when nothing matches — callers must
    fail closed rather than assume a default sport.
    """

    league = (stored_league or "").strip().upper()
    if league:
        entry = _BY_LEAGUE.get(league)
        if entry is not None:
            return entry
        for candidate in SPORT_REGISTRY:
            if league in {alias.upper() for alias in candidate.stored_league_aliases}:
                return candidate
    sport = (stored_sport or "").strip().upper()
    if sport:
        matches = [
            candidate for candidate in SPORT_REGISTRY
            if sport == str(candidate.sport_code)
            or sport in {alias.upper() for alias in candidate.stored_sport_aliases}
        ]
        # Ambiguous only when a sport spans several leagues (e.g. BASKETBALL);
        # a single unambiguous match is safe to return.
        if len(matches) == 1:
            return matches[0]
    return None


def registry_manifest() -> list[dict]:
    """Serializable SPORT_REGISTRY_V1 manifest for API/docs surfaces."""
    return [entry.model_dump(mode="json") for entry in SPORT_REGISTRY]


__all__ = [
    "ACTIVE_STATES",
    "MLB",
    "NBA",
    "NFL",
    "SPORT_REGISTRY",
    "UFC",
    "WNBA",
    "active_leagues",
    "all_sports",
    "get_league",
    "navigable_leagues",
    "planned_leagues",
    "registry_manifest",
    "resolve_stored_identity",
]
