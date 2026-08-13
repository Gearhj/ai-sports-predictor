"""Strict historical rest, travel, starter, and bullpen workload derivation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import Iterable

from app.historical.policy import EvidenceClassification
from app.historical.temporal import as_utc


class AppearanceRole(StrEnum):
    STARTER = "STARTER"
    RELIEVER = "RELIEVER"


class HistoricalGameStatus(StrEnum):
    FINAL = "FINAL"
    SUSPENDED = "SUSPENDED"
    POSTPONED = "POSTPONED"
    CANCELED = "CANCELED"


@dataclass(frozen=True)
class PlayerAppearanceEvent:
    event_id: str
    game_id: str
    player_id: str
    team_id: str
    role: AppearanceRole
    completed_at: datetime
    eligible_at: datetime
    pitches: float
    outs_recorded: int = 0
    classification: EvidenceClassification = (
        EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME
    )


@dataclass(frozen=True)
class WorkloadWindow:
    days: int
    pitches: float
    outs_recorded: int
    appearances: int
    event_ids: tuple[str, ...]


@dataclass(frozen=True)
class PlayerWorkloadSnapshot:
    player_id: str
    cutoff: datetime
    windows: tuple[WorkloadWindow, ...]
    method: str = "prior_eligible_appearance_workload_v1"

    def window(self, days: int) -> WorkloadWindow:
        for item in self.windows:
            if item.days == days:
                return item
        raise KeyError(days)


@dataclass(frozen=True)
class BullpenWorkloadSnapshot:
    team_id: str
    cutoff: datetime
    relievers: tuple[PlayerWorkloadSnapshot, ...]
    contributing_event_ids: tuple[str, ...]
    method: str = "prior_eligible_reliever_workload_v1"


@dataclass(frozen=True)
class TeamGameEvent:
    game_id: str
    team_id: str
    first_pitch: datetime
    completed_at: datetime
    eligible_at: datetime
    status: HistoricalGameStatus
    venue_id: str | None = None
    venue_latitude: float | None = None
    venue_longitude: float | None = None
    innings: int | None = None


@dataclass(frozen=True)
class RestTravelSnapshot:
    team_id: str
    target_game_id: str
    cutoff: datetime
    previous_game_id: str | None
    previous_completed_at: datetime | None
    hours_since_previous_completion: float | None
    full_24h_rest_periods: int | None
    travel_miles: float | None
    previous_game_innings: int | None
    method: str = "prior_completed_game_rest_travel_v1"


def derive_player_workload(
    appearances: Iterable[PlayerAppearanceEvent],
    *,
    player_id: str,
    cutoff: datetime,
    current_game_id: str,
    windows_days: tuple[int, ...] = (1, 3, 5),
    team_id: str | None = None,
) -> PlayerWorkloadSnapshot:
    """Aggregate pitch/outs workload from eligible appearances only."""

    boundary = as_utc(cutoff)
    if not player_id or not current_game_id:
        raise ValueError("player_id and current_game_id are required")
    windows = _validated_windows(windows_days)
    rows = tuple(appearances)
    _validate_appearance_ids(rows)
    selected: list[PlayerAppearanceEvent] = []
    for item in rows:
        _validate_appearance(item)
        if (
            item.player_id == player_id
            and (team_id is None or item.team_id == team_id)
            and item.game_id != current_game_id
            and item.classification is not EvidenceClassification.FORWARD_ONLY
            and as_utc(item.completed_at) < boundary
            and as_utc(item.eligible_at) <= boundary
        ):
            selected.append(item)
    selected.sort(key=lambda item: (as_utc(item.completed_at), item.event_id))

    summaries = tuple(
        _summarize_window(selected, boundary=boundary, days=days)
        for days in windows
    )
    return PlayerWorkloadSnapshot(player_id=player_id, cutoff=boundary, windows=summaries)


def derive_starter_workload(
    appearances: Iterable[PlayerAppearanceEvent],
    *,
    starter_id: str,
    cutoff: datetime,
    current_game_id: str,
    windows_days: tuple[int, ...] = (1, 3, 5),
    team_id: str | None = None,
) -> PlayerWorkloadSnapshot:
    """Derive a probable starter's load, including any prior relief appearance."""

    return derive_player_workload(
        appearances,
        player_id=starter_id,
        cutoff=cutoff,
        current_game_id=current_game_id,
        windows_days=windows_days,
        team_id=team_id,
    )


def derive_bullpen_workloads(
    appearances: Iterable[PlayerAppearanceEvent],
    *,
    team_id: str,
    cutoff: datetime,
    current_game_id: str,
    windows_days: tuple[int, ...] = (1, 2, 3),
) -> BullpenWorkloadSnapshot:
    """Derive per-reliever workloads without inventing a historical roster."""

    if not team_id:
        raise ValueError("team_id is required")
    rows = tuple(appearances)
    boundary = as_utc(cutoff)
    eligible = []
    for item in rows:
        _validate_appearance(item)
        if (
            item.team_id == team_id
            and item.role is AppearanceRole.RELIEVER
            and item.game_id != current_game_id
            and item.classification is not EvidenceClassification.FORWARD_ONLY
            and as_utc(item.completed_at) < boundary
            and as_utc(item.eligible_at) <= boundary
        ):
            eligible.append(item)
    _validate_appearance_ids(rows)
    player_ids = sorted({item.player_id for item in eligible})
    relievers = tuple(
        derive_player_workload(
            rows,
            player_id=player_id,
            cutoff=boundary,
            current_game_id=current_game_id,
            windows_days=windows_days,
            team_id=team_id,
        )
        for player_id in player_ids
    )
    contributing = tuple(
        item.event_id
        for item in sorted(eligible, key=lambda row: (as_utc(row.completed_at), row.event_id))
    )
    return BullpenWorkloadSnapshot(
        team_id=team_id,
        cutoff=boundary,
        relievers=relievers,
        contributing_event_ids=contributing,
    )


def derive_rest_travel(
    games: Iterable[TeamGameEvent],
    *,
    team_id: str,
    target_game_id: str,
    target_first_pitch: datetime,
    cutoff: datetime,
    target_venue_latitude: float | None,
    target_venue_longitude: float | None,
) -> RestTravelSnapshot:
    """Use the latest eligible completed game, never a mutable schedule row."""

    boundary = as_utc(cutoff)
    target_start = as_utc(target_first_pitch)
    if boundary >= target_start:
        raise ValueError("Historical cutoff must precede target first pitch")
    if not team_id or not target_game_id:
        raise ValueError("team_id and target_game_id are required")
    _validate_coordinates(target_venue_latitude, target_venue_longitude)

    rows = tuple(games)
    identifiers = [item.game_id for item in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Team game identifiers must be unique")
    eligible: list[TeamGameEvent] = []
    for game in rows:
        _validate_team_game(game)
        if (
            game.team_id == team_id
            and game.game_id != target_game_id
            and game.status is HistoricalGameStatus.FINAL
            and as_utc(game.completed_at) < boundary
            and as_utc(game.eligible_at) <= boundary
        ):
            eligible.append(game)
    if not eligible:
        return RestTravelSnapshot(
            team_id=team_id,
            target_game_id=target_game_id,
            cutoff=boundary,
            previous_game_id=None,
            previous_completed_at=None,
            hours_since_previous_completion=None,
            full_24h_rest_periods=None,
            travel_miles=None,
            previous_game_innings=None,
        )

    previous = max(eligible, key=lambda item: (as_utc(item.completed_at), item.game_id))
    elapsed_hours = (target_start - as_utc(previous.completed_at)).total_seconds() / 3600.0
    if elapsed_hours < 0:
        raise ValueError("Previous completed game cannot end after target first pitch")
    travel = _travel_miles(
        previous.venue_latitude,
        previous.venue_longitude,
        target_venue_latitude,
        target_venue_longitude,
    )
    return RestTravelSnapshot(
        team_id=team_id,
        target_game_id=target_game_id,
        cutoff=boundary,
        previous_game_id=previous.game_id,
        previous_completed_at=as_utc(previous.completed_at),
        hours_since_previous_completion=elapsed_hours,
        full_24h_rest_periods=int(elapsed_hours // 24),
        travel_miles=travel,
        previous_game_innings=previous.innings,
    )


def _summarize_window(
    rows: list[PlayerAppearanceEvent],
    *,
    boundary: datetime,
    days: int,
) -> WorkloadWindow:
    lower = boundary - timedelta(days=days)
    selected = [item for item in rows if as_utc(item.completed_at) >= lower]
    return WorkloadWindow(
        days=days,
        pitches=sum(item.pitches for item in selected),
        outs_recorded=sum(item.outs_recorded for item in selected),
        appearances=len(selected),
        event_ids=tuple(item.event_id for item in selected),
    )


def _validated_windows(windows: tuple[int, ...]) -> tuple[int, ...]:
    if not windows or any(not isinstance(day, int) or day <= 0 for day in windows):
        raise ValueError("Workload window days must be positive integers")
    if len(windows) != len(set(windows)):
        raise ValueError("Workload window days must be unique")
    return tuple(sorted(windows))


def _validate_appearance_ids(rows: tuple[PlayerAppearanceEvent, ...]) -> None:
    identifiers = [item.event_id for item in rows]
    if any(not identifier for identifier in identifiers):
        raise ValueError("Appearance event_id is required")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Appearance event identifiers must be unique")


def _validate_appearance(item: PlayerAppearanceEvent) -> None:
    if not item.event_id or not item.game_id or not item.player_id or not item.team_id:
        raise ValueError("Appearance identifiers are required")
    if not isfinite(item.pitches) or item.pitches < 0 or item.outs_recorded < 0:
        raise ValueError("Appearance workload must be finite and non-negative")
    if as_utc(item.eligible_at) < as_utc(item.completed_at):
        raise ValueError("Appearance cannot be eligible before completion")


def _validate_team_game(game: TeamGameEvent) -> None:
    if not game.game_id or not game.team_id:
        raise ValueError("Team game identifiers are required")
    if as_utc(game.completed_at) < as_utc(game.first_pitch):
        raise ValueError("Game completion cannot precede first pitch")
    if as_utc(game.eligible_at) < as_utc(game.completed_at):
        raise ValueError("Game cannot be eligible before completion")
    if game.innings is not None and game.innings <= 0:
        raise ValueError("Game innings must be positive when supplied")
    _validate_coordinates(game.venue_latitude, game.venue_longitude)


def _validate_coordinates(latitude: float | None, longitude: float | None) -> None:
    if (latitude is None) != (longitude is None):
        raise ValueError("Latitude and longitude must both be supplied or both be missing")
    if latitude is not None and (
        not isfinite(latitude)
        or not isfinite(float(longitude))
        or not -90 <= latitude <= 90
        or not -180 <= float(longitude) <= 180
    ):
        raise ValueError("Venue coordinates are invalid")


def _travel_miles(
    from_latitude: float | None,
    from_longitude: float | None,
    to_latitude: float | None,
    to_longitude: float | None,
) -> float | None:
    if None in (from_latitude, from_longitude, to_latitude, to_longitude):
        return None
    lat1, lon1, lat2, lon2 = map(
        radians,
        (
            float(from_latitude),
            float(from_longitude),
            float(to_latitude),
            float(to_longitude),
        ),
    )
    delta_latitude = lat2 - lat1
    delta_longitude = lon2 - lon1
    haversine = (
        sin(delta_latitude / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_longitude / 2) ** 2
    )
    return 3958.7613 * 2 * asin(sqrt(haversine))
