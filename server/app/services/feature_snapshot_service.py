"""Build immutable, checksum-backed Gate 2 game feature snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.bullpen import RelieverWorkload, aggregate_bullpen_availability
from app.features.lineup import LineupEntry as LineupFeatureEntry, aggregate_lineup
from app.features.statcast import PlayerStatcastObservation, aggregate_statcast
from app.models import FeatureVersion, GameFeatureSnapshot
from app.services.game_state_service import GameKnowledgeState, TeamKnowledgeState


FEATURE_SCHEMA_VERSION = "mlb_point_in_time_v2"


class FeatureSnapshotService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def build_snapshot(
        self,
        state: GameKnowledgeState,
        feature_version: FeatureVersion,
        *,
        generated_at: datetime | None = None,
    ) -> GameFeatureSnapshot:
        generated = _aware(generated_at or state.data_as_of)
        home_values, home_meta = self._team_values(state.home)
        away_values, away_meta = self._team_values(state.away)
        park_factor = (
            state.park_factor.overall_run_factor
            if state.park_factor and state.park_factor.overall_run_factor is not None else 1.0
        )
        values: dict[str, Any] = {
            "home_rating": state.home.rating.rating if state.home.rating else None,
            "away_rating": state.away.rating.rating if state.away.rating else None,
            "home_runs_per_game": home_values.get("runs_per_game"),
            "away_runs_per_game": away_values.get("runs_per_game"),
            "home_runs_allowed_per_game": home_values.get("runs_allowed_per_game") or home_values.get("era"),
            "away_runs_allowed_per_game": away_values.get("runs_allowed_per_game") or away_values.get("era"),
            "home_recent_runs": home_values.get("recent_runs"),
            "away_recent_runs": away_values.get("recent_runs"),
            "home_starter_era": home_values.get("starter_era"),
            "away_starter_era": away_values.get("starter_era"),
            "home_starter_xera": home_meta["starter_xera"],
            "away_starter_xera": away_meta["starter_xera"],
            "home_bullpen_era": home_values.get("bullpen_era"),
            "away_bullpen_era": away_values.get("bullpen_era"),
            "home_bullpen_xera": home_meta["bullpen_xera"],
            "away_bullpen_xera": away_meta["bullpen_xera"],
            "home_ops": home_values.get("ops"),
            "away_ops": away_values.get("ops"),
            "home_lineup_xwoba": home_meta["lineup"].value,
            "away_lineup_xwoba": away_meta["lineup"].value,
            "home_lineup_talent_coverage": home_meta["lineup"].weighted_coverage,
            "away_lineup_talent_coverage": away_meta["lineup"].weighted_coverage,
            "temperature_f": state.weather.temperature_f if state.weather else None,
            "wind_speed_mph": state.weather.wind_mph if state.weather else None,
            "precipitation_probability": state.weather.precipitation_probability if state.weather else None,
            "park_run_factor": park_factor,
            "home_lineup_status": state.home.lineup.lineup_status if state.home.lineup else "MISSING",
            "away_lineup_status": state.away.lineup.lineup_status if state.away.lineup else "MISSING",
            "home_lineup_player_ids": [str(entry.player_id) for entry in state.home.lineup_entries],
            "away_lineup_player_ids": [str(entry.player_id) for entry in state.away.lineup_entries],
            "home_starter_player_id": _starter_id(state.home),
            "away_starter_player_id": _starter_id(state.away),
            "home_bullpen_availability": home_meta["bullpen_availability"],
            "away_bullpen_availability": away_meta["bullpen_availability"],
            "home_statcast_xwoba": home_meta["statcast"].get("xwoba"),
            "away_statcast_xwoba": away_meta["statcast"].get("xwoba"),
            "home_statcast_barrel_rate": home_meta["statcast"].get("barrel_rate"),
            "away_statcast_barrel_rate": away_meta["statcast"].get("barrel_rate"),
            "home_statcast_hard_hit_rate": home_meta["statcast"].get("hard_hit_rate"),
            "away_statcast_hard_hit_rate": away_meta["statcast"].get("hard_hit_rate"),
            "home_statcast_average_exit_velocity": home_meta["statcast"].get("average_exit_velocity"),
            "away_statcast_average_exit_velocity": away_meta["statcast"].get("average_exit_velocity"),
            "defense": None,
            "baserunning": None,
            "opponent_adjustment": None,
            "roof_orientation_adjustment": None,
            "home_days_since_previous_game": state.home.days_since_previous_game,
            "away_days_since_previous_game": state.away.days_since_previous_game,
            "home_travel_distance_miles": state.home.travel_distance_miles,
            "away_travel_distance_miles": state.away.travel_distance_miles,
            "doubleheader_game_number": state.game_observation.game_number,
        }
        critical = (
            "home_runs_per_game", "away_runs_per_game",
            "home_runs_allowed_per_game", "away_runs_allowed_per_game",
            "home_starter_player_id", "away_starter_player_id",
        )
        operational = (
            "home_rating", "away_rating", "home_runs_per_game", "away_runs_per_game",
            "home_runs_allowed_per_game", "away_runs_allowed_per_game", "home_recent_runs",
            "away_recent_runs", "home_starter_era", "away_starter_era", "home_bullpen_era",
            "away_bullpen_era", "home_ops", "away_ops", "temperature_f",
            "home_statcast_xwoba", "away_statcast_xwoba",
            "home_statcast_barrel_rate", "away_statcast_barrel_rate",
            "home_statcast_hard_hit_rate", "away_statcast_hard_hit_rate",
            "home_statcast_average_exit_velocity", "away_statcast_average_exit_velocity",
            "home_bullpen_availability",
            "away_bullpen_availability", "home_lineup_xwoba", "away_lineup_xwoba",
            "home_starter_xera", "away_starter_xera", "home_bullpen_xera",
            "away_bullpen_xera", "home_days_since_previous_game",
            "away_days_since_previous_game", "home_travel_distance_miles",
            "away_travel_distance_miles",
        )
        presence = {key: values.get(key) is not None for key in operational}
        coverage_gates = {
            "home_lineup_xwoba": home_meta["lineup"].weighted_coverage,
            "away_lineup_xwoba": away_meta["lineup"].weighted_coverage,
            "home_statcast_xwoba": home_meta["statcast_metadata"]["player_coverage"],
            "away_statcast_xwoba": away_meta["statcast_metadata"]["player_coverage"],
            "home_statcast_barrel_rate": home_meta["statcast_metadata"]["player_coverage"],
            "away_statcast_barrel_rate": away_meta["statcast_metadata"]["player_coverage"],
            "home_statcast_hard_hit_rate": home_meta["statcast_metadata"]["player_coverage"],
            "away_statcast_hard_hit_rate": away_meta["statcast_metadata"]["player_coverage"],
            "home_statcast_average_exit_velocity": home_meta["statcast_metadata"]["player_coverage"],
            "away_statcast_average_exit_velocity": away_meta["statcast_metadata"]["player_coverage"],
            "home_bullpen_availability": home_meta["bullpen_metadata"]["coverage"],
            "away_bullpen_availability": away_meta["bullpen_metadata"]["coverage"],
            "home_bullpen_xera": home_meta["bullpen_metadata"]["coverage"],
            "away_bullpen_xera": away_meta["bullpen_metadata"]["coverage"],
        }
        for key, coverage in coverage_gates.items():
            # Sparse evidence remains visible in Bayesian/source metadata, but
            # it does not earn measured-completeness credit until enough of the
            # expected unit (lineup or bullpen role share) is observed.
            presence[key] = presence[key] and float(coverage or 0.0) >= 0.70
        presence["park_run_factor"] = bool(
            state.park_factor is not None and state.park_factor.overall_run_factor is not None
        )
        missing = sorted(key for key, present in presence.items() if not present)
        missing.extend(("defense", "baserunning", "opponent_adjustment", "roof_orientation_adjustment"))
        missing.extend(key for key in critical if values.get(key) is None and key not in missing)
        provisional: list[str] = []
        if state.park_factor is None:
            provisional.append("park_run_factor:neutral_prior")
        if state.home.lineup is None:
            provisional.append("home_lineup:missing")
        elif state.home.lineup.lineup_status != "CONFIRMED":
            provisional.append("home_lineup:projected")
        if state.away.lineup is None:
            provisional.append("away_lineup:missing")
        elif state.away.lineup.lineup_status != "CONFIRMED":
            provisional.append("away_lineup:projected")
        provisional.extend(f"home_lineup:{item}" for item in home_meta["lineup_assumptions"])
        provisional.extend(f"away_lineup:{item}" for item in away_meta["lineup_assumptions"])
        provisional.extend(
            f"{key}:insufficient_coverage_{float(coverage or 0.0):.3f}"
            for key, coverage in coverage_gates.items()
            if values.get(key) is not None and float(coverage or 0.0) < 0.70
        )
        stale = self._stale_evidence(state)
        source_evidence = self._evidence(state)
        completeness = round(sum(presence.values()) / len(presence), 6)
        bayesian = {
            "method": "empirical_bayes_weighted_evidence_v1",
            "streak_or_morale_adjustment": False,
            "home_talent_estimates": [_talent_metadata(row) for row in state.home.talent],
            "away_talent_estimates": [_talent_metadata(row) for row in state.away.talent],
            "home_bullpen": home_meta["bullpen_metadata"],
            "away_bullpen": away_meta["bullpen_metadata"],
            "home_lineup": _lineup_metadata(home_meta["lineup"]),
            "away_lineup": _lineup_metadata(away_meta["lineup"]),
            "home_statcast": home_meta["statcast_metadata"],
            "away_statcast": away_meta["statcast_metadata"],
        }
        canonical = {
            "game_source": state.game_observation.source,
            "game_source_record_id": state.game_observation.source_record_id,
            "data_as_of": state.data_as_of.isoformat(),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "feature_version": {
                "name": feature_version.name,
                "version": feature_version.version,
                "schema_hash": feature_version.schema_hash,
            },
            "source_mode": state.source_mode,
            "source_evidence": _semantic_evidence(source_evidence),
            "feature_values": _semantic_feature_values(values),
            "missing_features": sorted(set(missing)),
            "stale_features": stale,
            "provisional_features": sorted(provisional),
            "bayesian_metadata": bayesian,
        }
        checksum = _checksum(canonical)
        existing = self.session.scalar(
            select(GameFeatureSnapshot)
            .where(GameFeatureSnapshot.checksum == checksum)
            .limit(1)
        )
        if existing is not None:
            return existing
        snapshot = GameFeatureSnapshot(
            game_id=state.game_id,
            generated_at=generated,
            data_as_of=state.data_as_of,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            feature_version_id=feature_version.id,
            game_state_observation_id=state.game_observation.id,
            home_lineup_observation_id=state.home.lineup.id if state.home.lineup else None,
            away_lineup_observation_id=state.away.lineup.id if state.away.lineup else None,
            home_starter_observation_id=state.home.starter.id if state.home.starter else None,
            away_starter_observation_id=state.away.starter.id if state.away.starter else None,
            weather_snapshot_id=state.weather.id if state.weather else None,
            park_factor_version_id=state.park_factor.id if state.park_factor else None,
            home_rating_observation_id=state.home.rating.id if state.home.rating else None,
            away_rating_observation_id=state.away.rating.id if state.away.rating else None,
            bullpen_observation_ids=[
                *[str(row.id) for row in state.home.bullpen],
                *[str(row.id) for row in state.away.bullpen],
            ],
            roster_observation_ids=[
                *[str(row.id) for row in state.home.roster],
                *[str(row.id) for row in state.away.roster],
            ],
            statcast_observation_ids=[
                *[str(row.id) for row in state.home.statcast],
                *[str(row.id) for row in state.away.statcast],
            ],
            # Market evidence is associated with the forecast, not probability
            # feature content, and therefore cannot change this checksum.
            market_snapshot_ids=[],
            source_evidence=source_evidence,
            feature_values=values,
            feature_completeness=completeness,
            missing_features=sorted(set(missing)),
            stale_features=stale,
            provisional_features=sorted(provisional),
            bayesian_metadata=bayesian,
            checksum=checksum,
            source_mode=state.source_mode,
            frozen_at=generated,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def _team_values(self, team: TeamKnowledgeState) -> tuple[dict[str, Any], dict[str, Any]]:
        values = dict(team.team_feature.values) if team.team_feature else {}
        talent_by_player_metric = {
            (row.player_id, row.metric_name): row.posterior_value
            for row in team.talent
        }
        roster_by_player = {row.player_id: row for row in team.roster}
        lineup_rows: list[LineupFeatureEntry] = []
        lineup_assumptions: list[str] = []
        lineup_confirmed = bool(team.lineup and team.lineup.lineup_status == "CONFIRMED")
        for entry in team.lineup_entries:
            if entry.batting_order is None or not 1 <= entry.batting_order <= 9:
                continue
            if entry.expected_plate_appearances is None:
                lineup_assumptions.append(f"slot_{entry.batting_order}_missing_expected_pa")
                continue
            roster = roster_by_player.get(entry.player_id)
            replacement_value = None
            if roster is not None and roster.replacement_player_id is not None:
                replacement_value = talent_by_player_metric.get(
                    (roster.replacement_player_id, "xwoba")
                )
            if roster is not None and roster.participation_probability is not None:
                participation = float(roster.participation_probability)
            elif lineup_confirmed:
                participation = 1.0
            else:
                # A visibly provisional prior for projected lineups, never an
                # observed fact. It is included in snapshot assumption metadata.
                participation = 0.5
                lineup_assumptions.append(f"slot_{entry.batting_order}_participation_prior_0.5")
            lineup_rows.append(LineupFeatureEntry(
                player_id=str(entry.player_id),
                batting_order=int(entry.batting_order),
                talent_value=talent_by_player_metric.get((entry.player_id, "xwoba")),
                projected_plate_appearances=float(entry.expected_plate_appearances),
                participation_probability=participation,
                replacement_value=replacement_value,
                role="starter" if entry.is_starter else "replacement",
            ))
        lineup = aggregate_lineup(
            lineup_rows,
            confirmed=lineup_confirmed,
            source=team.lineup.source if team.lineup else None,
        ) if team.lineup_entries else aggregate_lineup(())
        workloads: list[RelieverWorkload] = []
        for row in team.bullpen:
            workload = row.workload if hasattr(row, "workload") and isinstance(row.workload, dict) else {}
            one_day = _float_or_none(row.pitches_previous_day)
            two_day = _float_or_none(workload.get("pitches_last_2d"))
            three_day = _float_or_none(workload.get("pitches_last_3d"))
            role_weight = _float_or_none(workload.get("expected_usage_weight"))
            expected = (
                _float_or_none(row.participation_probability)
                if row.participation_probability is not None
                else 1.0 if row.expected_available is True
                else 0.0 if row.expected_available is False
                else None
            )
            if None in {one_day, two_day, three_day, role_weight}:
                continue
            workloads.append(RelieverWorkload(
                player_id=str(row.player_id),
                pitches_last_1d=float(one_day),
                pitches_last_2d=float(two_day),
                pitches_last_3d=float(three_day),
                expected_usage_weight=float(role_weight),
                expected_availability=expected,
            ))
        bullpen = aggregate_bullpen_availability(workloads)
        statcast_rows = [PlayerStatcastObservation(
            player_id=str(row.player_id),
            attempts=float(row.sample_size or 0),
            xwoba=_float_or_none(row.values.get("xwoba")),
            barrel_rate=_float_or_none(row.values.get("barrel_rate")),
            hard_hit_rate=_float_or_none(row.values.get("hard_hit_rate")),
            average_exit_velocity=_float_or_none(row.values.get("average_exit_velocity")),
        ) for row in team.statcast]
        statcast = aggregate_statcast(statcast_rows)
        expected_player_ids = {
            entry.player_id for entry in team.lineup_entries
        } or {row.player_id for row in team.roster}
        observed_player_ids = {row.player_id for row in team.statcast}
        statcast_player_coverage = (
            len(expected_player_ids & observed_player_ids) / len(expected_player_ids)
            if expected_player_ids else 0.0
        )
        starter_id = None
        if team.starter is not None:
            starter_id = team.starter.confirmed_pitcher_id or team.starter.probable_pitcher_id
        starter_xera = (
            talent_by_player_metric.get((starter_id, "xera"))
            if starter_id is not None else None
        )
        bullpen_quality_rows: list[tuple[float, float]] = []
        for row in team.bullpen:
            quality = talent_by_player_metric.get((row.player_id, "xera"))
            availability = row.participation_probability
            if quality is not None and availability is not None and availability > 0:
                bullpen_quality_rows.append((quality, float(availability)))
        bullpen_xera = (
            sum(value * weight for value, weight in bullpen_quality_rows)
            / sum(weight for _, weight in bullpen_quality_rows)
            if bullpen_quality_rows else None
        )
        return values, {
            "lineup": lineup,
            "lineup_assumptions": lineup_assumptions,
            "starter_xera": starter_xera,
            "bullpen_xera": bullpen_xera,
            "bullpen_availability": bullpen.value,
            "bullpen_metadata": {
                "method": bullpen.method,
                "coverage": bullpen.coverage,
                "missing_player_ids": list(bullpen.missing_player_ids),
            },
            "statcast": {
                "xwoba": statcast.xwoba.value,
                "barrel_rate": statcast.barrel_rate.value,
                "hard_hit_rate": statcast.hard_hit_rate.value,
                "average_exit_velocity": statcast.average_exit_velocity.value,
            },
            "statcast_metadata": {
                "method": statcast.method,
                "player_count": statcast.player_count,
                "player_coverage": statcast_player_coverage,
                "coverage": {
                    "xwoba": statcast.xwoba.coverage,
                    "barrel_rate": statcast.barrel_rate.coverage,
                    "hard_hit_rate": statcast.hard_hit_rate.coverage,
                    "average_exit_velocity": statcast.average_exit_velocity.coverage,
                },
            },
        }

    @staticmethod
    def _stale_evidence(state: GameKnowledgeState) -> list[str]:
        stale: list[str] = []
        cutoff = state.data_as_of
        for label, row, age in (
            ("schedule", state.game_observation, timedelta(hours=6)),
            ("team_stats_home", state.home.team_feature, timedelta(hours=36)),
            ("team_stats_away", state.away.team_feature, timedelta(hours=36)),
            ("weather", state.weather, timedelta(hours=6)),
            ("home_lineup", state.home.lineup, timedelta(hours=6)),
            ("away_lineup", state.away.lineup, timedelta(hours=6)),
            ("home_starter", state.home.starter, timedelta(hours=6)),
            ("away_starter", state.away.starter, timedelta(hours=6)),
            ("home_rating", state.home.rating, timedelta(hours=36)),
            ("away_rating", state.away.rating, timedelta(hours=36)),
            ("park_factor", state.park_factor, timedelta(days=400)),
        ):
            if row is not None and cutoff - _aware(row.available_as_of) > age:
                stale.append(label)
        for label, rows, age in (
            ("roster", (*state.home.roster, *state.away.roster), timedelta(hours=36)),
            ("bullpen", (*state.home.bullpen, *state.away.bullpen), timedelta(hours=6)),
            ("statcast", (*state.home.statcast, *state.away.statcast), timedelta(hours=72)),
            ("talent", (*state.home.talent, *state.away.talent), timedelta(hours=72)),
        ):
            if rows and any(cutoff - _aware(row.available_as_of) > age for row in rows):
                stale.append(label)
        return sorted(stale)

    @staticmethod
    def _evidence(state: GameKnowledgeState) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        rows: list[tuple[str, Any]] = [
            ("game_state", state.game_observation),
            ("home_lineup", state.home.lineup),
            ("away_lineup", state.away.lineup),
            ("home_starter", state.home.starter),
            ("away_starter", state.away.starter),
            ("weather", state.weather),
            ("park_factor", state.park_factor),
            ("home_rating", state.home.rating),
            ("away_rating", state.away.rating),
            ("home_team_feature", state.home.team_feature),
            ("away_team_feature", state.away.team_feature),
        ]
        rows.extend(("roster", row) for row in (*state.home.roster, *state.away.roster))
        rows.extend(("bullpen", row) for row in (*state.home.bullpen, *state.away.bullpen))
        rows.extend(("statcast", row) for row in (*state.home.statcast, *state.away.statcast))
        rows.extend(("talent", row) for row in (*state.home.talent, *state.away.talent))
        for family, row in rows:
            if row is None:
                continue
            evidence.append({
                "family": family,
                "id": str(row.id),
                "source": getattr(row, "source", None),
                "source_mode": getattr(row, "source_mode", state.source_mode),
                "source_record_id": getattr(row, "source_record_id", None),
                "observed_at": _iso(getattr(row, "observed_at", None)),
                "available_as_of": _iso(getattr(row, "available_as_of", None)),
                "payload_checksum": getattr(row, "payload_checksum", None),
            })
        return sorted(evidence, key=lambda item: (item["family"], item["id"]))


def game_features_from_snapshot(snapshot: GameFeatureSnapshot) -> Any:
    from app.forecasting.ensemble import GameFeatures

    values = snapshot.feature_values
    missing = set(snapshot.missing_features or [])

    def usable(key: str) -> Any:
        return None if key in missing else values.get(key)

    # The development ensemble still needs numeric priors. Every such choice is
    # visible in missing/provisional metadata; none is presented as observed.
    return GameFeatures(
        home_rating=float(values.get("home_rating") or 1500.0),
        away_rating=float(values.get("away_rating") or 1500.0),
        home_runs_per_game=float(values.get("home_runs_per_game") or 4.4),
        away_runs_per_game=float(values.get("away_runs_per_game") or 4.4),
        home_runs_allowed_per_game=float(values.get("home_runs_allowed_per_game") or 4.4),
        away_runs_allowed_per_game=float(values.get("away_runs_allowed_per_game") or 4.4),
        home_recent_runs=_float_or_none(values.get("home_recent_runs")),
        away_recent_runs=_float_or_none(values.get("away_recent_runs")),
        home_starter_era=_float_or_none(values.get("home_starter_xera") or values.get("home_starter_era")),
        away_starter_era=_float_or_none(values.get("away_starter_xera") or values.get("away_starter_era")),
        home_bullpen_era=_float_or_none(usable("home_bullpen_xera") or values.get("home_bullpen_era")),
        away_bullpen_era=_float_or_none(usable("away_bullpen_xera") or values.get("away_bullpen_era")),
        home_ops=_float_or_none(values.get("home_ops")),
        away_ops=_float_or_none(values.get("away_ops")),
        home_lineup_xwoba=_float_or_none(usable("home_lineup_xwoba")),
        away_lineup_xwoba=_float_or_none(usable("away_lineup_xwoba")),
        home_statcast_xwoba=_float_or_none(usable("home_statcast_xwoba")),
        away_statcast_xwoba=_float_or_none(usable("away_statcast_xwoba")),
        home_barrel_rate=_float_or_none(usable("home_statcast_barrel_rate")),
        away_barrel_rate=_float_or_none(usable("away_statcast_barrel_rate")),
        home_hard_hit_rate=_float_or_none(usable("home_statcast_hard_hit_rate")),
        away_hard_hit_rate=_float_or_none(usable("away_statcast_hard_hit_rate")),
        home_average_exit_velocity=_float_or_none(usable("home_statcast_average_exit_velocity")),
        away_average_exit_velocity=_float_or_none(usable("away_statcast_average_exit_velocity")),
        home_bullpen_availability=_float_or_none(usable("home_bullpen_availability")),
        away_bullpen_availability=_float_or_none(usable("away_bullpen_availability")),
        home_days_since_previous_game=_float_or_none(values.get("home_days_since_previous_game")),
        away_days_since_previous_game=_float_or_none(values.get("away_days_since_previous_game")),
        home_travel_distance_miles=_float_or_none(values.get("home_travel_distance_miles")),
        away_travel_distance_miles=_float_or_none(values.get("away_travel_distance_miles")),
        temperature_f=_float_or_none(values.get("temperature_f")),
        wind_out_mph=None,
        park_run_factor=float(values.get("park_run_factor") or 1.0),
        feature_completeness=snapshot.feature_completeness,
        sample_reliability=_sample_reliability(snapshot),
    )


def _starter_id(team: TeamKnowledgeState) -> str | None:
    if team.starter is None:
        return None
    value = team.starter.confirmed_pitcher_id or team.starter.probable_pitcher_id
    return str(value) if value is not None else None


def _sample_reliability(snapshot: GameFeatureSnapshot) -> float:
    penalties = len(snapshot.missing_features) + len(snapshot.stale_features)
    return max(0.0, min(1.0, snapshot.feature_completeness - penalties * 0.02))


def _lineup_metadata(value: Any) -> dict[str, Any]:
    return {
        "method": value.method,
        "confirmed": value.confirmed,
        "weighted_coverage": value.weighted_coverage,
        "slot_coverage": value.slot_coverage,
        "missing_slots": list(value.missing_slots),
        "uncertain_slots": list(value.uncertain_slots),
        "replacement_slots": list(value.replacement_slots),
        "source": value.source,
    }


def _talent_metadata(row: Any) -> dict[str, Any]:
    return {
        "source_record_id": row.source_record_id,
        "payload_checksum": row.payload_checksum,
        "role": row.role,
        "metric_name": row.metric_name,
        "metric_class": row.metric_class,
        "posterior_value": row.posterior_value,
        "evidence_cutoff": _iso(row.evidence_cutoff),
        "method": row.method,
        "version": row.version,
        "parameters": row.parameters,
    }


def _semantic_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    semantic = [{
        key: value for key, value in row.items() if key != "id"
    } for row in rows]
    return sorted(
        semantic,
        key=lambda row: (
            str(row.get("family")),
            str(row.get("source")),
            str(row.get("source_record_id")),
            str(row.get("payload_checksum")),
            str(row.get("available_as_of")),
        ),
    )


def _semantic_feature_values(values: dict[str, Any]) -> dict[str, Any]:
    # Canonical database UUIDs remain in the persisted snapshot for lineage,
    # while semantic identity is carried by source keys/checksums above.
    return {
        key: value
        for key, value in values.items()
        if not key.endswith("_player_id") and not key.endswith("_player_ids")
    }


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> str | None:
    return _aware(value).isoformat() if isinstance(value, datetime) else None


def _checksum(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
