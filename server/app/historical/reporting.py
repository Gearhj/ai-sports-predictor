"""Provider-free coverage and operations reporting for Gate 3A MLB history.

The reporter reads only locally persisted canonical rows and configured artifact
directories.  It never constructs a source adapter, starts a job, or infers
that missing evidence is complete.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite, sqrt
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Iterable

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import (
    BullpenAvailabilityObservation,
    ExternalIdMapping,
    Game,
    GameFeatureSnapshot,
    GameFinalOutcome,
    GameStateObservation,
    HistoricalFeatureMaterialization,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    League,
    ParkFactorVersion,
    PlayerGameHistoryObservation,
    RosterAvailabilityObservation,
    Season,
    StatcastPlayerObservation,
    TeamGameHistoryObservation,
    WeatherSnapshot,
)


REPORT_SCHEMA_VERSION = "gate3a-history-operations-report-v1"
TEMPORAL_SEMANTICS = "RETROSPECTIVE_RECONSTRUCTION"
FAMILY_ORDER = (
    "finals",
    "starters",
    "actual_lineups",
    "roster",
    "team_history",
    "player_history",
    "bullpen",
    "statcast",
    "park",
    "weather",
    "rest_travel",
    "platoon",
    "defense",
    "baserunning",
    "opponent_adjustment",
    "tier_a_b_eligible",
)

_FEATURE_KEYS = {
    "rest_travel": (
        "home_days_since_previous_game",
        "away_days_since_previous_game",
        "home_travel_distance_miles",
        "away_travel_distance_miles",
    ),
}
_FEATURE_TOKENS = {
    "platoon": ("platoon",),
    "defense": ("defense", "defensive", "fielding"),
    "baserunning": ("baserunning", "base_running"),
    "opponent_adjustment": ("opponent_adjustment", "opponent_adjusted"),
}


@dataclass(frozen=True)
class HistoricalReportPaths:
    raw_root: Path
    curated_root: Path
    feature_root: Path
    runtime_root: Path


@dataclass(frozen=True)
class _SeasonDescriptor:
    season: int
    season_ids: tuple[Any, ...]
    names: tuple[str, ...]
    starts_at: datetime | None
    ends_at: datetime | None


def build_mlb_history_report(
    session: Session,
    *,
    paths: HistoricalReportPaths,
    season: int | None = None,
    validation_sample_size: int = 10,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a secret-free, JSON-serializable report from local state only."""

    if validation_sample_size < 1:
        raise ValueError("validation_sample_size must be positive")
    generated = _as_utc(generated_at or datetime.now(UTC))
    descriptors = _season_descriptors(session, season)
    season_reports: list[dict[str, Any]] = []
    drift_inputs: dict[int, dict[str, list[float]]] = {}
    for descriptor in descriptors:
        season_report, values = _build_season_report(
            session,
            descriptor,
            validation_sample_size=validation_sample_size,
        )
        season_reports.append(season_report)
        drift_inputs[descriptor.season] = values

    drift = _feature_drift(drift_inputs)
    drift_by_season = {
        item["season"]: item
        for item in drift.get("by_season", [])
    }
    for row in season_reports:
        row["feature_drift"] = drift_by_season.get(
            row["season"],
            {
                "season": row["season"],
                "status": "UNAVAILABLE",
                "reference_season": None,
                "reason": "No earlier locally persisted season has comparable numeric features.",
                "features": [],
            },
        )

    operations = _source_operations(session, paths.raw_root, season)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": _timestamp(generated),
        "report_execution_provider_requests": 0,
        "source_mode": "LIVE",
        "temporal_semantics": TEMPORAL_SEMANTICS,
        "season_filter": season,
        "season_count": len(season_reports),
        "seasons": season_reports,
        "source_operations": operations,
        "feature_drift": drift,
        "storage": _storage_report(session, paths),
        "notes": [
            "Coverage is measured only from locally persisted evidence.",
            "Validation samples contain only official persisted final outcomes.",
            "Historical physical request successes/failures are unavailable when terminal checkpoints do not encode them separately.",
        ],
    }


def _season_descriptors(session: Session, requested: int | None) -> list[_SeasonDescriptor]:
    rows = session.execute(
        select(Season.id, Season.name, Season.starts_at, Season.ends_at)
        .join(League, League.id == Season.league_id)
        .where(League.code.like("MLB%"))
        .order_by(Season.starts_at, Season.name)
    ).all()
    grouped: dict[int, dict[str, Any]] = {}
    for identifier, name, starts_at, ends_at in rows:
        year = _season_year(str(name), starts_at)
        if requested is not None and year != requested:
            continue
        item = grouped.setdefault(
            year,
            {"ids": [], "names": [], "starts": [], "ends": []},
        )
        item["ids"].append(identifier)
        item["names"].append(str(name))
        item["starts"].append(_as_utc(starts_at))
        item["ends"].append(_as_utc(ends_at))

    checkpoint_years = session.scalars(
        select(HistoryBackfillCheckpoint.season)
        .where(HistoryBackfillCheckpoint.season.is_not(None))
        .distinct()
    ).all()
    for raw_year in checkpoint_years:
        year = int(raw_year)
        if requested is None or requested == year:
            grouped.setdefault(year, {"ids": [], "names": [], "starts": [], "ends": []})
    if requested is not None:
        grouped.setdefault(requested, {"ids": [], "names": [], "starts": [], "ends": []})

    return [
        _SeasonDescriptor(
            season=year,
            season_ids=tuple(item["ids"]),
            names=tuple(sorted(set(item["names"]))),
            starts_at=min(item["starts"]) if item["starts"] else None,
            ends_at=max(item["ends"]) if item["ends"] else None,
        )
        for year, item in sorted(grouped.items())
    ]


def _latest_team_history_rows(
    session: Session,
    season_ids: tuple[Any, ...],
) -> tuple[list[TeamGameHistoryObservation], int]:
    if not season_ids:
        return [], 0
    rows = session.scalars(
        select(TeamGameHistoryObservation).where(
            TeamGameHistoryObservation.season_id.in_(season_ids),
            TeamGameHistoryObservation.source_mode == "LIVE",
        )
    ).all()
    latest: dict[tuple[Any, Any, Any, str], TeamGameHistoryObservation] = {}
    for row in rows:
        key = (row.game_id, row.team_id, row.source_version_id, row.source_record_id)
        current = latest.get(key)
        if current is None or (int(row.revision), str(row.id)) > (
            int(current.revision),
            str(current.id),
        ):
            latest[key] = row
    selected = sorted(latest.values(), key=lambda row: (str(row.game_id), str(row.team_id), str(row.id)))
    return selected, len(rows) - len(selected)


def _latest_player_history_rows(
    session: Session,
    season_ids: tuple[Any, ...],
) -> tuple[list[PlayerGameHistoryObservation], int]:
    if not season_ids:
        return [], 0
    rows = session.scalars(
        select(PlayerGameHistoryObservation).where(
            PlayerGameHistoryObservation.season_id.in_(season_ids),
            PlayerGameHistoryObservation.source_mode == "LIVE",
        )
    ).all()
    latest: dict[
        tuple[Any, Any, Any, str, Any, str],
        PlayerGameHistoryObservation,
    ] = {}
    for row in rows:
        key = (
            row.game_id,
            row.player_id,
            row.team_id,
            str(row.role),
            row.source_version_id,
            row.source_record_id,
        )
        current = latest.get(key)
        if current is None or (int(row.revision), str(row.id)) > (
            int(current.revision),
            str(current.id),
        ):
            latest[key] = row
    selected = sorted(
        latest.values(),
        key=lambda row: (str(row.game_id), str(row.team_id), str(row.player_id), str(row.id)),
    )
    return selected, len(rows) - len(selected)


def _build_season_report(
    session: Session,
    descriptor: _SeasonDescriptor,
    *,
    validation_sample_size: int,
) -> tuple[dict[str, Any], dict[str, list[float]]]:
    season_ids = descriptor.season_ids
    game_rows = (
        session.execute(
            select(Game.id, Game.starts_at, Game.venue_id)
            .where(Game.season_id.in_(season_ids), Game.source_mode == "LIVE")
            .order_by(Game.starts_at, Game.id)
        ).all()
        if season_ids
        else []
    )
    game_ids = {row.id for row in game_rows}
    game_starts = {row.id: _as_utc(row.starts_at) for row in game_rows}

    outcomes = (
        session.scalars(
            select(GameFinalOutcome)
            .where(
                GameFinalOutcome.season_id.in_(season_ids),
                GameFinalOutcome.source_mode == "LIVE",
                GameFinalOutcome.official.is_(True),
                GameFinalOutcome.outcome_status == "FINAL",
            )
            .order_by(
                GameFinalOutcome.game_id,
                GameFinalOutcome.revision,
                GameFinalOutcome.label_available_at,
            )
        ).all()
        if season_ids
        else []
    )
    latest_outcomes: dict[Any, GameFinalOutcome] = {}
    for outcome in outcomes:
        previous = latest_outcomes.get(outcome.game_id)
        if previous is None or (outcome.revision, outcome.label_available_at) > (
            previous.revision,
            previous.label_available_at,
        ):
            latest_outcomes[outcome.game_id] = outcome
    final_game_ids = set(latest_outcomes)

    player_history_rows, superseded_player_records = _latest_player_history_rows(
        session,
        season_ids,
    )
    player_group_players: dict[tuple[Any, Any, str, bool | None], set[Any]] = defaultdict(set)
    for row in player_history_rows:
        player_group_players[
            (row.game_id, row.team_id, str(row.role), row.started)
        ].add(row.player_id)
    player_record_count = len(player_history_rows)
    starter_teams: dict[Any, set[Any]] = defaultdict(set)
    lineup_teams: dict[Any, set[Any]] = defaultdict(set)
    player_teams: dict[Any, set[Any]] = defaultdict(set)
    starter_records = lineup_records = 0
    for (game_id, team_id, raw_role, started), players in player_group_players.items():
        player_teams[game_id].add(team_id)
        role = raw_role.upper()
        if role == "PITCHER" and started is True:
            starter_records += len(players)
            starter_teams[game_id].add(team_id)
        if role == "BATTER" and started is True:
            lineup_records += len(players)
            if len(players) >= 9:
                lineup_teams[game_id].add(team_id)
    starter_games = {game for game, teams in starter_teams.items() if len(teams) >= 2}
    lineup_games = {game for game, teams in lineup_teams.items() if len(teams) >= 2}
    player_history_games = {game for game, teams in player_teams.items() if len(teams) >= 2}

    team_history_rows, superseded_team_records = _latest_team_history_rows(
        session,
        season_ids,
    )
    team_groups: dict[Any, set[Any]] = defaultdict(set)
    for row in team_history_rows:
        team_groups[row.game_id].add(row.team_id)
    team_history_games = {game_id for game_id, teams in team_groups.items() if len(teams) >= 2}
    team_record_count = len(team_history_rows)

    bullpen_rows = (
        session.execute(
            select(
                BullpenAvailabilityObservation.game_id,
                func.count(func.distinct(BullpenAvailabilityObservation.team_id)).label("teams"),
            )
            .where(
                BullpenAvailabilityObservation.game_id.in_(game_ids),
                BullpenAvailabilityObservation.source_mode == "LIVE",
            )
            .group_by(BullpenAvailabilityObservation.game_id)
        ).all()
        if game_ids
        else []
    )
    bullpen_games = {row.game_id for row in bullpen_rows if int(row.teams) >= 2}
    bullpen_records = _count(
        session,
        BullpenAvailabilityObservation,
        BullpenAvailabilityObservation.game_id.in_(game_ids),
        BullpenAvailabilityObservation.source_mode == "LIVE",
    ) if game_ids else 0

    materialized_rows = (
        session.execute(
            select(HistoricalFeatureMaterialization, GameFeatureSnapshot)
            .join(
                GameFeatureSnapshot,
                GameFeatureSnapshot.id
                == HistoricalFeatureMaterialization.game_feature_snapshot_id,
            )
            .where(
                HistoricalFeatureMaterialization.game_id.in_(game_ids),
                HistoricalFeatureMaterialization.source_mode == "LIVE",
                GameFeatureSnapshot.source_mode == "LIVE",
            )
            .order_by(
                HistoricalFeatureMaterialization.game_id,
                HistoricalFeatureMaterialization.generated_at,
                HistoricalFeatureMaterialization.id,
            )
        ).all()
        if game_ids
        else []
    )
    latest_materialized: dict[Any, tuple[HistoricalFeatureMaterialization, GameFeatureSnapshot]] = {}
    for materialization, snapshot in materialized_rows:
        latest_materialized[materialization.game_id] = (materialization, snapshot)

    roster_games: set[Any] = set()
    statcast_games: set[Any] = set()
    park_games: set[Any] = set()
    weather_games: set[Any] = set()
    feature_games: dict[str, set[Any]] = defaultdict(set)
    drift_values: dict[str, list[float]] = defaultdict(list)
    for game_id, (materialization, snapshot) in latest_materialized.items():
        if _nonempty(snapshot.roster_observation_ids):
            roster_games.add(game_id)
        if _nonempty(snapshot.statcast_observation_ids):
            statcast_games.add(game_id)
        if snapshot.park_factor_version_id is not None:
            park_games.add(game_id)
        if snapshot.weather_snapshot_id is not None:
            weather_games.add(game_id)
        missing = {str(value) for value in (snapshot.missing_features or [])}
        values = dict(snapshot.feature_values or {})
        for family, required in _FEATURE_KEYS.items():
            if all(_measured(values, missing, key) for key in required):
                feature_games[family].add(game_id)
        for family, tokens in _FEATURE_TOKENS.items():
            if any(
                _measured(values, missing, key)
                and any(token in key.lower() for token in tokens)
                for key in values
            ):
                feature_games[family].add(game_id)
        for key, value in values.items():
            number = _finite_number(value)
            if key not in missing and number is not None:
                drift_values[str(key)].append(number)

    roster_records = _count(
        session,
        RosterAvailabilityObservation,
        RosterAvailabilityObservation.season_id.in_(season_ids),
        RosterAvailabilityObservation.source_mode == "LIVE",
    ) if season_ids else 0
    statcast_records = _count(
        session,
        StatcastPlayerObservation,
        StatcastPlayerObservation.season == descriptor.season,
        StatcastPlayerObservation.source_mode == "LIVE",
    )
    park_records = _count(
        session,
        ParkFactorVersion,
        ParkFactorVersion.season == descriptor.season,
        ParkFactorVersion.source_mode == "LIVE",
    )
    weather_records = _count(
        session,
        WeatherSnapshot,
        WeatherSnapshot.game_id.in_(game_ids),
        WeatherSnapshot.source_mode == "LIVE",
    ) if game_ids else 0
    eligible_games = {
        materialization.game_id
        for materialization, _snapshot in latest_materialized.values()
        if materialization.training_eligible
        and materialization.eligibility_status == "ELIGIBLE"
        and materialization.quality_tier in {"A", "B"}
    }

    coverage_sets = {
        "finals": final_game_ids,
        "starters": starter_games,
        "actual_lineups": lineup_games,
        "roster": roster_games,
        "team_history": team_history_games,
        "player_history": player_history_games,
        "bullpen": bullpen_games,
        "statcast": statcast_games,
        "park": park_games,
        "weather": weather_games,
        "rest_travel": feature_games["rest_travel"],
        "platoon": feature_games["platoon"],
        "defense": feature_games["defense"],
        "baserunning": feature_games["baserunning"],
        "opponent_adjustment": feature_games["opponent_adjustment"],
        "tier_a_b_eligible": eligible_games,
    }
    record_counts = {
        "finals": len(outcomes),
        "starters": starter_records,
        "actual_lineups": lineup_records,
        "roster": roster_records,
        "team_history": team_record_count,
        "player_history": player_record_count,
        "bullpen": bullpen_records,
        "statcast": statcast_records,
        "park": park_records,
        "weather": weather_records,
        "rest_travel": len(feature_games["rest_travel"]),
        "platoon": len(feature_games["platoon"]),
        "defense": len(feature_games["defense"]),
        "baserunning": len(feature_games["baserunning"]),
        "opponent_adjustment": len(feature_games["opponent_adjustment"]),
        "tier_a_b_eligible": len(eligible_games),
    }
    bases = {
        "finals": "Distinct official LIVE GameFinalOutcome games / normalized LIVE games.",
        "starters": "Games with an actual started PITCHER history row for both teams / official final games.",
        "actual_lineups": "Games with at least nine actual started BATTER history rows for both teams / official final games.",
        "roster": "Historical snapshots referencing roster observation IDs / official final games.",
        "team_history": "Games with governed team history for both teams / official final games.",
        "player_history": "Games with governed player history for both teams / official final games.",
        "bullpen": "Games with LIVE bullpen observations for both teams / official final games.",
        "statcast": "Historical snapshots referencing Statcast observations / official final games.",
        "park": "Historical snapshots referencing a park-factor version / official final games.",
        "weather": "Historical snapshots referencing a weather snapshot / official final games.",
        "rest_travel": "Historical snapshots with all four measured rest/travel fields / official final games.",
        "platoon": "Historical snapshots with a measured feature whose name identifies platoon evidence / official final games.",
        "defense": "Historical snapshots with a measured defense/fielding feature / official final games.",
        "baserunning": "Historical snapshots with a measured baserunning feature / official final games.",
        "opponent_adjustment": "Historical snapshots with a measured opponent-adjustment feature / official final games.",
        "tier_a_b_eligible": "LIVE ELIGIBLE training materializations in quality tier A or B / official final games.",
    }
    coverage = []
    for family in FAMILY_ORDER:
        denominator = game_ids if family == "finals" else final_game_ids
        denominator_name = "normalized_live_games" if family == "finals" else "official_live_final_games"
        coverage.append(
            _coverage_metric(
                family,
                coverage_sets[family],
                denominator,
                evidence_records=record_counts[family],
                denominator_name=denominator_name,
                basis=bases[family],
            )
        )

    checkpoint_summary = _checkpoint_summary(session, descriptor.season)
    exclusions = _exclusions_and_mappings(
        session,
        season_ids,
        game_ids,
        latest_outcomes,
        latest_materialized,
        team_history_rows,
        player_history_rows,
    )
    validation_sample = _validation_sample(
        session,
        descriptor,
        latest_outcomes,
        game_starts,
        player_history_rows,
        requested=validation_sample_size,
    )
    return (
        {
            "season": descriptor.season,
            "season_ids": [str(value) for value in descriptor.season_ids],
            "season_names": list(descriptor.names),
            "window": {
                "starts_at": _timestamp(descriptor.starts_at) if descriptor.starts_at else None,
                "ends_at": _timestamp(descriptor.ends_at) if descriptor.ends_at else None,
            },
            "games": {
                "normalized_live": len(game_ids),
                "official_live_finals": len(final_game_ids),
                "official_final_records_including_revisions": len(outcomes),
            },
            "coverage": coverage,
            "history_revision_accounting": {
                "basis": (
                    "Latest LIVE revision per canonical semantic key; superseded revisions "
                    "are retained physically but excluded from coverage and evidence counts."
                ),
                "team_history": {
                    "latest_records": team_record_count,
                    "superseded_records": superseded_team_records,
                    "physical_records": team_record_count + superseded_team_records,
                },
                "player_history": {
                    "latest_records": player_record_count,
                    "superseded_records": superseded_player_records,
                    "physical_records": player_record_count + superseded_player_records,
                },
            },
            "checkpoint_summary": checkpoint_summary,
            "exclusions": exclusions,
            "validation_sample": validation_sample,
        },
        {key: list(values) for key, values in sorted(drift_values.items())},
    )


def _coverage_metric(
    family: str,
    covered: set[Any],
    denominator: set[Any],
    *,
    evidence_records: int,
    denominator_name: str,
    basis: str,
) -> dict[str, Any]:
    covered_count = len(covered & denominator)
    if not denominator:
        return {
            "family": family,
            "status": "UNAVAILABLE",
            "covered_games": 0,
            "denominator_games": 0,
            "denominator": denominator_name,
            "coverage": None,
            "evidence_records": int(evidence_records),
            "basis": basis,
            "reason": f"No {denominator_name.replace('_', ' ')} are locally persisted.",
        }
    return {
        "family": family,
        "status": "AVAILABLE" if covered_count else "MISSING",
        "covered_games": covered_count,
        "denominator_games": len(denominator),
        "denominator": denominator_name,
        "coverage": round(covered_count / len(denominator), 8),
        "evidence_records": int(evidence_records),
        "basis": basis,
        "reason": None if covered_count else "No qualifying locally persisted evidence was found.",
    }


def _validation_sample(
    session: Session,
    descriptor: _SeasonDescriptor,
    outcomes: dict[Any, GameFinalOutcome],
    game_starts: dict[Any, datetime],
    player_history_rows: list[PlayerGameHistoryObservation],
    *,
    requested: int,
) -> dict[str, Any]:
    ordered = sorted(
        outcomes.values(),
        key=lambda item: (
            game_starts.get(item.game_id, _as_utc(item.scheduled_at)),
            str(item.game_id),
        ),
    )
    selected = _evenly_spaced(ordered, requested)
    if not selected:
        return {
            "status": "UNAVAILABLE",
            "requested": requested,
            "selected": 0,
            "selection_method": "chronologically_even_official_finals_v1",
            "reason": "No official persisted final outcomes are available; completion was not inferred.",
            "games": [],
        }

    selected_ids = {item.game_id for item in selected}
    state_rows = session.scalars(
        select(GameStateObservation)
        .where(
            GameStateObservation.game_id.in_(selected_ids),
            GameStateObservation.source_mode == "LIVE",
        )
        .order_by(GameStateObservation.game_id, GameStateObservation.available_as_of)
    ).all()
    latest_states: dict[Any, GameStateObservation] = {}
    for state in state_rows:
        latest_states[state.game_id] = state

    selected_player_rows = [row for row in player_history_rows if row.game_id in selected_ids]
    selected_player_ids = {row.player_id for row in selected_player_rows}
    player_history = sorted(
        (row for row in player_history_rows if row.player_id in selected_player_ids),
        key=lambda row: (str(row.player_id), _as_utc(row.event_at), str(row.game_id)),
    )
    trade_flags: dict[Any, bool] = defaultdict(bool)
    seen_teams: dict[Any, set[Any]] = defaultdict(set)
    for row in player_history:
        if row.game_id in selected_ids and seen_teams[row.player_id] - {row.team_id}:
            trade_flags[row.game_id] = True
        seen_teams[row.player_id].add(row.team_id)

    rookie_evidence: dict[Any, list[bool]] = defaultdict(list)
    for row in selected_player_rows:
        explicit = _rookie_value(row.values, descriptor.season)
        if explicit is not None:
            rookie_evidence[row.game_id].append(explicit)

    mappings = session.execute(
        select(ExternalIdMapping.entity_id, ExternalIdMapping.external_id)
        .where(
            ExternalIdMapping.entity_type == "game",
            ExternalIdMapping.entity_id.in_(selected_ids),
        )
        .order_by(ExternalIdMapping.entity_id, ExternalIdMapping.source)
    ).all()
    external_ids: dict[Any, str] = {}
    for entity_id, external_id in mappings:
        external_ids.setdefault(entity_id, str(external_id))

    games = []
    for outcome in selected:
        state = latest_states.get(outcome.game_id)
        doubleheader = _doubleheader_flag(state)
        local_player_evidence = any(row.game_id == outcome.game_id for row in selected_player_rows)
        rookie_values = rookie_evidence.get(outcome.game_id, [])
        games.append({
            "game_id": str(outcome.game_id),
            "source_game_id": external_ids.get(outcome.game_id),
            "scheduled_at": _timestamp(_as_utc(outcome.scheduled_at)),
            "final_at": _timestamp(_as_utc(outcome.final_at)),
            "completion": {
                "status": "AVAILABLE",
                "value": True,
                "basis": "official LIVE GameFinalOutcome",
            },
            "flags": {
                "home_away": {
                    "status": "AVAILABLE",
                    "home_team_id": str(outcome.home_team_id),
                    "away_team_id": str(outcome.away_team_id),
                },
                "doubleheader": doubleheader,
                "trade": {
                    "status": "AVAILABLE" if local_player_evidence else "UNAVAILABLE",
                    "value": bool(trade_flags[outcome.game_id]) if local_player_evidence else None,
                    "basis": (
                        "Prior differing team observed in locally persisted same-season player history."
                        if trade_flags[outcome.game_id]
                        else "No prior differing team observed in available same-season player history."
                    ) if local_player_evidence else None,
                },
                "rookie": {
                    "status": "AVAILABLE" if rookie_values else "UNAVAILABLE",
                    "value": any(rookie_values) if rookie_values else None,
                    "basis": "Explicit rookie/debut field in player history values." if rookie_values else None,
                },
            },
        })
    return {
        "status": "COMPLETE" if len(games) == requested else "PARTIAL",
        "requested": requested,
        "selected": len(games),
        "selection_method": "chronologically_even_official_finals_v1",
        "reason": None if len(games) == requested else "Fewer than the requested official finals are persisted.",
        "games": games,
    }


def _exclusions_and_mappings(
    session: Session,
    season_ids: tuple[Any, ...],
    game_ids: set[Any],
    outcomes: dict[Any, GameFinalOutcome],
    materialized: dict[Any, tuple[HistoricalFeatureMaterialization, GameFeatureSnapshot]],
    team_history_rows: list[TeamGameHistoryObservation],
    player_history_rows: list[PlayerGameHistoryObservation],
) -> dict[str, Any]:
    reason_counts: Counter[str] = Counter()
    tier_counts: Counter[str] = Counter()
    ineligible = 0
    for materialization, _snapshot in materialized.values():
        tier_counts[str(materialization.quality_tier)] += 1
        if not materialization.training_eligible:
            ineligible += 1
            reasons = materialization.eligibility_reasons or []
            if not reasons:
                reason_counts["unspecified"] += 1
            for reason in reasons:
                reason_counts[_reason_key(reason)] += 1

    excluded_team = sum(row.quality_tier == "EXCLUDED" for row in team_history_rows)
    excluded_player = sum(row.quality_tier == "EXCLUDED" for row in player_history_rows)

    relevant: dict[str, set[Any]] = {
        "game": set(game_ids),
        "team": set(),
        "venue": set(),
        "player": set(),
    }
    for outcome in outcomes.values():
        relevant["team"].update((outcome.home_team_id, outcome.away_team_id))
        if outcome.venue_id is not None:
            relevant["venue"].add(outcome.venue_id)
    relevant["player"].update(row.player_id for row in player_history_rows)
    relevant["team"].update(row.team_id for row in team_history_rows)
    mapped_rows = session.execute(
        select(ExternalIdMapping.entity_type, ExternalIdMapping.entity_id)
        .where(ExternalIdMapping.entity_type.in_(tuple(relevant)))
    ).all()
    mapped: dict[str, set[Any]] = defaultdict(set)
    for entity_type, entity_id in mapped_rows:
        mapped[str(entity_type)].add(entity_id)
    mapping_counts = {
        entity_type: {
            "referenced": len(identifiers),
            "mapped": len(identifiers & mapped[entity_type]),
            "unresolved": len(identifiers - mapped[entity_type]),
        }
        for entity_type, identifiers in relevant.items()
    }
    referenced = sum(item["referenced"] for item in mapping_counts.values())
    unresolved = sum(item["unresolved"] for item in mapping_counts.values())
    return {
        "materializations": {
            "total": len(materialized),
            "training_ineligible": ineligible,
            "quality_tiers": dict(sorted(tier_counts.items())),
            "eligibility_reasons": dict(sorted(reason_counts.items())),
        },
        "quality_excluded_history_records": {
            "team": excluded_team,
            "player": excluded_player,
        },
        "unresolved_mappings": {
            "status": "AVAILABLE" if referenced else "UNAVAILABLE",
            "referenced_entities": referenced,
            "unresolved_entities": unresolved,
            "by_entity_type": mapping_counts,
            "reason": None if referenced else "No historical entities are locally persisted.",
        },
    }


def _checkpoint_summary(session: Session, season: int) -> dict[str, Any]:
    checkpoints = session.scalars(
        select(HistoryBackfillCheckpoint).where(HistoryBackfillCheckpoint.season == season)
    ).all()
    statuses = Counter(str(item.status) for item in checkpoints)
    return {
        "total": len(checkpoints),
        "statuses": {key: statuses.get(key, 0) for key in ("SUCCEEDED", "FAILED", "SKIPPED", "BLOCKED")},
        "attempts": sum(int(item.attempt_count) for item in checkpoints),
        "retries": sum(int(item.retry_count) for item in checkpoints),
        "records_received": sum(int(item.records_received) for item in checkpoints),
        "records_accepted": sum(int(item.records_accepted) for item in checkpoints),
        "records_rejected": sum(int(item.records_rejected) for item in checkpoints),
        "duplicates": sum(int(item.duplicate_count) for item in checkpoints),
    }


def _source_operations(session: Session, raw_root: Path, season: int | None) -> list[dict[str, Any]]:
    sources = {
        item.id: item
        for item in session.scalars(select(HistoricalSourceVersion)).all()
    }
    query = select(HistoryBackfillCheckpoint)
    if season is not None:
        query = query.where(HistoryBackfillCheckpoint.season == season)
    checkpoints = session.scalars(
        query.order_by(
            HistoryBackfillCheckpoint.season,
            HistoryBackfillCheckpoint.source_version_id,
            HistoryBackfillCheckpoint.sequence,
        )
    ).all()
    grouped: dict[tuple[Any, int | None], list[HistoryBackfillCheckpoint]] = defaultdict(list)
    for checkpoint in checkpoints:
        grouped[(checkpoint.source_version_id, checkpoint.season)].append(checkpoint)

    rows = []
    for (source_id, source_season), items in sorted(
        grouped.items(),
        key=lambda pair: (
            pair[0][1] if pair[0][1] is not None else -1,
            str(pair[0][0]),
        ),
    ):
        source = sources.get(source_id)
        statuses = Counter(str(item.status) for item in items)
        raw = _referenced_raw_size(raw_root, items)
        rows.append({
            "season": source_season,
            "source_version_id": str(source_id),
            "source_key": source.source_key if source else None,
            "dataset_version": source.dataset_version if source else None,
            "provider_name": source.provider_name if source else None,
            "source_mode": source.source_mode if source else None,
            "checkpoints": {
                "total": len(items),
                "succeeded": statuses["SUCCEEDED"],
                "failed": statuses["FAILED"],
                "skipped": statuses["SKIPPED"],
                "blocked": statuses["BLOCKED"],
                "status_semantics": (
                    "SUCCEEDED means the checkpoint stream's terminal operation succeeded. "
                    "It does not by itself claim canonical persistence; archive-only writers "
                    "record archive_only/canonical_status in quality_summary."
                ),
            },
            "physical_requests": {
                "attempts": {
                    "status": "AVAILABLE",
                    "value": sum(int(item.attempt_count) for item in items),
                    "basis": (
                        "Sum of persisted checkpoint attempt_count. Exactness depends on the writer; "
                        "legacy/canonical writers may have stored a minimum or default rather than "
                        "executor-observed physical attempts. Never inferred from chunk status."
                    ),
                },
                "retries": {
                    "status": "AVAILABLE",
                    "value": sum(int(item.retry_count) for item in items),
                    "basis": (
                        "Sum of persisted checkpoint retry_count. Exactness depends on the writer; "
                        "never inferred from chunk status."
                    ),
                },
                "successes": {
                    "status": "UNAVAILABLE",
                    "value": None,
                    "reason": "Terminal checkpoints do not persist physical successful request count separately.",
                },
                "failures": {
                    "status": "UNAVAILABLE",
                    "value": None,
                    "reason": "Terminal checkpoint failure is exact, but physical failed attempts are not persisted separately.",
                },
            },
            "records": {
                "received": sum(int(item.records_received) for item in items),
                "accepted": sum(int(item.records_accepted) for item in items),
                "rejected": sum(int(item.records_rejected) for item in items),
                "duplicates": sum(int(item.duplicate_count) for item in items),
            },
            "raw_archive": raw,
            "raw_checksum_semantics": {
                "raw_checksums": (
                    "Writer-defined for legacy rows; Gate 3A archive runner stores "
                    "SHA-256 of exact compressed archive-file bytes."
                ),
                "payload_checksum_location": "checkpoint.quality_summary.checksum_semantics.payload_checksums",
            },
            "errors": dict(sorted(Counter(
                str(item.error_type)
                for item in items
                if item.error_type
            ).items())),
        })
    return rows


def _referenced_raw_size(
    raw_root: Path,
    checkpoints: Iterable[HistoryBackfillCheckpoint],
) -> dict[str, Any]:
    root = Path(raw_root).resolve()
    references: set[str] = set()
    for checkpoint in checkpoints:
        references.update(
            str(value)
            for value in (checkpoint.raw_references or [])
            if isinstance(value, (str, Path))
        )
    if not references:
        return {
            "status": "UNAVAILABLE",
            "bytes": None,
            "referenced_files": 0,
            "resolved_files": 0,
            "missing_or_outside_root": 0,
            "reason": "No raw file references are persisted on these checkpoints.",
        }
    total = resolved = unresolved = 0
    seen: set[Path] = set()
    for reference in sorted(references):
        candidate = Path(reference)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate = candidate.resolve()
            if not candidate.is_relative_to(root) or candidate in seen or not candidate.is_file():
                unresolved += 1
                continue
            seen.add(candidate)
            total += candidate.stat().st_size
            resolved += 1
        except OSError:
            unresolved += 1
    status = "AVAILABLE" if resolved and not unresolved else "PARTIAL" if resolved else "UNAVAILABLE"
    return {
        "status": status,
        "bytes": total if resolved else None,
        "referenced_files": len(references),
        "resolved_files": resolved,
        "missing_or_outside_root": unresolved,
        "reason": None if status == "AVAILABLE" else "Some or all referenced raw files are unavailable inside the configured raw root.",
    }


def _feature_drift(values: dict[int, dict[str, list[float]]]) -> dict[str, Any]:
    seasons = sorted(values)
    by_season = []
    all_features = 0
    for index, current_season in enumerate(seasons):
        if index == 0:
            by_season.append({
                "season": current_season,
                "status": "UNAVAILABLE",
                "reference_season": None,
                "reason": "No earlier locally persisted season has comparable numeric features.",
                "features": [],
            })
            continue
        previous_season = seasons[index - 1]
        common = sorted(set(values[previous_season]) & set(values[current_season]))
        diagnostics = []
        for feature in common:
            previous = values[previous_season][feature]
            current = values[current_season][feature]
            if not previous or not current:
                continue
            previous_mean = fmean(previous)
            current_mean = fmean(current)
            pooled = None
            if len(previous) >= 2 and len(current) >= 2:
                pooled = sqrt((pstdev(previous) ** 2 + pstdev(current) ** 2) / 2)
            standardized = (
                (current_mean - previous_mean) / pooled
                if pooled is not None and pooled > 0
                else None
            )
            diagnostics.append({
                "feature": feature,
                "reference_n": len(previous),
                "current_n": len(current),
                "reference_mean": round(previous_mean, 8),
                "current_mean": round(current_mean, 8),
                "mean_shift": round(current_mean - previous_mean, 8),
                "standardized_mean_shift": round(standardized, 8) if standardized is not None else None,
                "standardized_status": "AVAILABLE" if standardized is not None else "UNAVAILABLE",
            })
        all_features += len(diagnostics)
        by_season.append({
            "season": current_season,
            "status": "AVAILABLE" if diagnostics else "UNAVAILABLE",
            "reference_season": previous_season,
            "reason": None if diagnostics else "No comparable measured numeric historical features exist.",
            "features": diagnostics,
        })
    return {
        "status": "AVAILABLE" if all_features else "UNAVAILABLE",
        "method": "consecutive_season_numeric_mean_shift_v1",
        "reason": None if all_features else "At least two seasons with comparable measured numeric historical features are required.",
        "by_season": by_season,
    }


def _storage_report(session: Session, paths: HistoricalReportPaths) -> dict[str, Any]:
    curated_parquet = _tree_size(paths.curated_root, suffixes={".parquet"})
    feature_parquet = _tree_size(paths.feature_root, suffixes={".parquet"})
    return {
        "database": _database_size(session),
        "raw": _tree_size(paths.raw_root),
        "parquet": {
            "status": _combined_status(curated_parquet["status"], feature_parquet["status"]),
            "bytes": int(curated_parquet["bytes"]) + int(feature_parquet["bytes"]),
            "files": int(curated_parquet["files"]) + int(feature_parquet["files"]),
            "curated": curated_parquet,
            "features": feature_parquet,
        },
        "runtime": _tree_size(paths.runtime_root),
    }


def _database_size(session: Session) -> dict[str, Any]:
    bind = session.get_bind()
    if bind is None:
        return {"status": "UNAVAILABLE", "bytes": None, "reason": "Session has no database bind."}
    dialect = bind.dialect.name
    if dialect == "sqlite":
        database = bind.url.database
        if not database or database == ":memory:":
            return {
                "status": "UNAVAILABLE",
                "bytes": None,
                "dialect": dialect,
                "reason": "The SQLite database is in memory and has no file size.",
            }
        path = Path(database)
        try:
            return {
                "status": "AVAILABLE" if path.is_file() else "UNAVAILABLE",
                "bytes": path.stat().st_size if path.is_file() else None,
                "dialect": dialect,
                "reason": None if path.is_file() else "The configured SQLite database file does not exist.",
            }
        except OSError:
            return {"status": "UNAVAILABLE", "bytes": None, "dialect": dialect, "reason": "Database file size could not be read."}
    if dialect == "postgresql":
        try:
            size = session.scalar(text("SELECT pg_database_size(current_database())"))
            return {"status": "AVAILABLE", "bytes": int(size), "dialect": dialect, "reason": None}
        except Exception:  # pragma: no cover - depends on local database privileges
            return {"status": "UNAVAILABLE", "bytes": None, "dialect": dialect, "reason": "Database size query was unavailable."}
    return {"status": "UNAVAILABLE", "bytes": None, "dialect": dialect, "reason": "Database size is unsupported for this dialect."}


def _tree_size(root: Path, suffixes: set[str] | None = None) -> dict[str, Any]:
    root = Path(root)
    if not root.exists():
        return {"status": "AVAILABLE", "exists": False, "bytes": 0, "files": 0, "unreadable": 0}
    total = files = unreadable = 0
    try:
        root_resolved = root.resolve()
        candidates = root.rglob("*")
        for candidate in candidates:
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                resolved = candidate.resolve()
                if not resolved.is_relative_to(root_resolved):
                    unreadable += 1
                    continue
                if suffixes is not None and candidate.suffix.lower() not in suffixes:
                    continue
                total += candidate.stat().st_size
                files += 1
            except OSError:
                unreadable += 1
    except OSError:
        unreadable += 1
    return {
        "status": "PARTIAL" if unreadable else "AVAILABLE",
        "exists": True,
        "bytes": total,
        "files": files,
        "unreadable": unreadable,
    }


def _evenly_spaced(rows: list[Any], limit: int) -> list[Any]:
    if len(rows) <= limit:
        return list(rows)
    if limit == 1:
        return [rows[0]]
    indices = [round(index * (len(rows) - 1) / (limit - 1)) for index in range(limit)]
    return [rows[index] for index in indices]


def _doubleheader_flag(state: GameStateObservation | None) -> dict[str, Any]:
    if state is None or (state.doubleheader_status is None and state.game_number is None):
        return {"status": "UNAVAILABLE", "value": None, "basis": None}
    status = str(state.doubleheader_status or "").strip().upper()
    positive = bool(
        (state.game_number is not None and state.game_number > 1)
        or status not in {"", "N", "NONE", "SINGLE", "SINGLE_GAME"}
    )
    return {
        "status": "AVAILABLE",
        "value": positive,
        "basis": "Latest locally persisted GameStateObservation doubleheader fields.",
    }


def _rookie_value(values: dict[str, Any] | None, season: int) -> bool | None:
    if not isinstance(values, dict):
        return None
    lowered = {str(key).lower(): value for key, value in values.items()}
    for key in ("is_rookie", "rookie"):
        if isinstance(lowered.get(key), bool):
            return bool(lowered[key])
    debut = lowered.get("mlb_debut_date") or lowered.get("debut_date")
    if isinstance(debut, str):
        try:
            return datetime.fromisoformat(debut.replace("Z", "+00:00")).year == season
        except ValueError:
            return None
    return None


def _count(session: Session, model: type, *criteria: Any) -> int:
    return int(session.scalar(select(func.count()).select_from(model).where(*criteria)) or 0)


def _measured(values: dict[str, Any], missing: set[str], key: str) -> bool:
    return key not in missing and _finite_number(values.get(key)) is not None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _nonempty(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _reason_key(value: Any) -> str:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        for key in ("code", "reason", "name"):
            if value.get(key):
                return str(value[key])
    return json.dumps(value, sort_keys=True, default=str)


def _season_year(name: str, starts_at: datetime) -> int:
    stripped = name.strip()
    if len(stripped) >= 4 and stripped[:4].isdigit():
        return int(stripped[:4])
    return _as_utc(starts_at).year


def _combined_status(*statuses: str) -> str:
    return "PARTIAL" if "PARTIAL" in statuses else "AVAILABLE"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")
