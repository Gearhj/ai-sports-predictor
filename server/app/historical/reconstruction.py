"""Provider-free A1 reconstruction of governed historical pregame populations.

This module reads canonical, append-only historical facts already present in
the database.  It never constructs an adapter and never invokes a provider.
Target-game outcomes are used only after snapshot construction as labels by the
existing materialization/dataset boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from math import isfinite
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.features.talent import estimate_true_talent
from app.historical.elo import CompletedGameResult, replay_pregame_elo
from app.historical.materialization import (
    ADMISSION_QUALITY_POLICY_VERSION,
    HistoricalMaterializationError,
    HistoricalMaterializationResult,
    persist_historical_snapshot,
)
from app.historical.policy import (
    EvidenceClassification,
    EvidenceTier,
    FamilyEvidence,
    FamilyWeight,
    QualityPolicy,
)
from app.historical.snapshot import (
    HISTORICAL_SNAPSHOT_TYPE,
    EvidenceRole,
    HistoricalEvidenceReference,
    HistoricalFeature,
    HistoricalSnapshot,
    build_historical_snapshot,
)
from app.historical.temporal import CUTOFF_POLICY_VERSION, as_utc, deterministic_cutoff
from app.historical.workload import (
    AppearanceRole,
    HistoricalGameStatus,
    PlayerAppearanceEvent,
    TeamGameEvent,
    derive_bullpen_workloads,
    derive_rest_travel,
    derive_starter_workload,
)
from app.modeling.schema import GATE3_FEATURE_SCHEMA
from app.models import (
    FeatureVersion,
    Game,
    GameFinalOutcome,
    GameStatus,
    GameTeam,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    PlayerGameHistoryObservation,
    TeamGameHistoryObservation,
    TrainingDatasetManifest,
    Venue,
)
from app.training.dataset import (
    DEFAULT_MAX_DATASET_ARTIFACT_BYTES,
    DatasetBuildResult,
    GovernedTrainingDatasetBuilder,
    TrainingDatasetArtifactStore,
    TrainingDatasetManifestData,
    HISTORICAL_ROW_LINEAGE_VERSION,
)


TEMPORAL_SEMANTICS = "RETROSPECTIVE_RECONSTRUCTION"
SOURCE_MODE = "LIVE"
A1_BUILDER_VERSION = "gate3a_a1_reconstruction_v4"
A1_MIN_FREE_BYTES_AFTER_WRITE = 100 * 1024**3
_DERIVED_DATASET_FEATURES = {
    "feature_completeness",
    "sample_reliability",
    "lineups_confirmed",
    "starters_confirmed",
}


class ReconstructionPopulation(StrEnum):
    TIER_A_STRICT = "TIER_A_STRICT"
    TIER_B_ENHANCED = "TIER_B_ENHANCED"

    @property
    def tier(self) -> EvidenceTier:
        return EvidenceTier.A if self is self.TIER_A_STRICT else EvidenceTier.B

    @property
    def feature_version_name(self) -> str:
        return "mlb_historical_reconstructed_pregame"

    @property
    def feature_version(self) -> str:
        suffix = "tier-a-strict" if self is self.TIER_A_STRICT else "tier-b-enhanced"
        return f"a1-{suffix}-v4"

    @property
    def materializer_version(self) -> str:
        suffix = "tier_a_strict" if self is self.TIER_A_STRICT else "tier_b_enhanced"
        return f"gate3a_a1_{suffix}_v4"


@dataclass(frozen=True)
class ReconstructionResult:
    status: str
    population: ReconstructionPopulation
    game_id: str
    snapshot: HistoricalSnapshot | None
    reason: str | None
    prior_team_rows: int
    prior_player_rows: int
    target_context_rows: int


@dataclass(frozen=True)
class MaterializedReconstructionResult:
    reconstruction: ReconstructionResult
    persistence: HistoricalMaterializationResult | None


@dataclass(frozen=True)
class HistoricalDatasetPersistenceResult:
    status: str
    population: ReconstructionPopulation
    build: DatasetBuildResult
    artifact: TrainingDatasetManifestData | None
    manifest: TrainingDatasetManifest | None
    reason: str | None = None


@dataclass(frozen=True)
class HistoricalReconstructionDatasetRunResult:
    status: str
    population: ReconstructionPopulation
    materializations: tuple[MaterializedReconstructionResult, ...]
    dataset: HistoricalDatasetPersistenceResult


class HistoricalReconstructionService:
    """Construct A1 snapshots exclusively from canonical local history."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_feature_version(
        self,
        population: ReconstructionPopulation,
    ) -> FeatureVersion:
        expected_definition = {
            "snapshot_type": HISTORICAL_SNAPSHOT_TYPE,
            "temporal_semantics": TEMPORAL_SEMANTICS,
            "population": population.value,
            "quality_tier": population.tier.value,
            "frozen_feature_schema_version": GATE3_FEATURE_SCHEMA.version,
            "frozen_feature_schema_hash": GATE3_FEATURE_SCHEMA.schema_hash,
            "ordered_input_names": [item.name for item in GATE3_FEATURE_SCHEMA.features],
            "ordered_output_names": list(GATE3_FEATURE_SCHEMA.output_names),
            "builder_version": A1_BUILDER_VERSION,
            "materializer_version": population.materializer_version,
            "admission_quality_policy_version": ADMISSION_QUALITY_POLICY_VERSION,
            "historical_row_lineage_version": HISTORICAL_ROW_LINEAGE_VERSION,
            "quality_semantics": {
                "temporal_integrity": "INDEPENDENT_EVIDENCE_TIMING_AND_PROVENANCE_SCORE",
                "feature_coverage": "FROZEN_SCHEMA_NON_DERIVED_FEATURE_COMPLETENESS",
                "admission": "MIN_TEMPORAL_INTEGRITY_AND_FEATURE_COVERAGE",
            },
            "sample_reliability_method": "FEATURE_COMPLETENESS_V3",
            "target_context_policy": (
                "EXCLUDED_UNLESS_CUTOFF_PUBLISHED"
                if population is ReconstructionPopulation.TIER_A_STRICT
                else "PROVISIONAL_HINDSIGHT_IDENTITY_ONLY_NO_TARGET_PERFORMANCE"
            ),
        }
        row = self.session.scalar(select(FeatureVersion).where(
            FeatureVersion.name == population.feature_version_name,
            FeatureVersion.version == population.feature_version,
        ))
        if row is None:
            row = FeatureVersion(
                name=population.feature_version_name,
                version=population.feature_version,
                schema_hash=GATE3_FEATURE_SCHEMA.schema_hash,
                definition=expected_definition,
            )
            self.session.add(row)
            self.session.flush()
            return row
        if (
            row.schema_hash != GATE3_FEATURE_SCHEMA.schema_hash
            or row.definition != expected_definition
        ):
            raise ValueError("A1 feature-version registry identity collision")
        return row

    def build_snapshot(
        self,
        game: Game,
        *,
        population: ReconstructionPopulation,
        feature_version: FeatureVersion | None = None,
        computed_at: datetime | None = None,
    ) -> ReconstructionResult:
        generated_at = as_utc(computed_at or datetime.now(UTC))
        if game.id is None or self.session.get(Game, game.id) is None:
            raise ValueError("A persisted target game is required")
        if game.source_mode != SOURCE_MODE or game.season_id is None:
            return self._insufficient(game, population, "TARGET_GAME_NOT_CANONICAL_LIVE")
        cutoff = deterministic_cutoff(_orm_utc(game.starts_at))
        if generated_at < cutoff:
            return self._insufficient(game, population, "RECONSTRUCTION_PRECEDES_CUTOFF")
        sides = list(self.session.scalars(select(GameTeam).where(
            GameTeam.game_id == game.id,
        )))
        by_home = {bool(row.is_home): row for row in sides}
        if len(sides) != 2 or len(by_home) != 2:
            return self._insufficient(game, population, "TARGET_GAME_SIDES_INCOMPLETE")
        home, away = by_home[True], by_home[False]

        team_rows = self._prior_team_rows(
            game,
            cutoff=cutoff,
            computed_at=generated_at,
            population=population,
        )
        side_team_rows = [
            row for row in team_rows if row.team_id in {home.team_id, away.team_id}
        ]
        if not {row.team_id for row in side_team_rows}.issuperset({home.team_id, away.team_id}):
            return self._insufficient(
                game,
                population,
                "PRIOR_TEAM_HISTORY_REQUIRED_FOR_BOTH_SIDES",
                prior_team_rows=len(team_rows),
            )

        context_rows = (
            self._target_context_rows(game, computed_at=generated_at)
            if population is ReconstructionPopulation.TIER_B_ENHANCED
            else []
        )
        if population is ReconstructionPopulation.TIER_B_ENHANCED and not context_rows:
            return self._insufficient(
                game,
                population,
                "TIER_B_TARGET_CONTEXT_UNAVAILABLE",
                prior_team_rows=len(team_rows),
            )
        context_player_ids = {row.player_id for row in context_rows}
        player_rows = self._prior_player_rows(
            game,
            cutoff=cutoff,
            computed_at=generated_at,
            population=population,
            selected_player_ids=context_player_ids,
            selected_team_ids={home.team_id, away.team_id},
        )

        feature_version = feature_version or self.ensure_feature_version(population)
        _validate_feature_version(feature_version, population)
        features, missing, feature_metadata = self._features(
            game=game,
            home=home,
            away=away,
            cutoff=cutoff,
            team_rows=team_rows,
            player_rows=player_rows,
            context_rows=context_rows,
        )
        evidence_source_versions = _load_rows_by_ids(
            self.session,
            HistoricalSourceVersion,
            {
                row.source_version_id
                for row in (*team_rows, *player_rows, *context_rows)
            },
        )
        evidence = [
            *(
                _reference(
                    row,
                    family="historical_team",
                    source_versions=evidence_source_versions,
                )
                for row in team_rows
            ),
            *(
                _reference(
                    row,
                    family="historical_player",
                    source_versions=evidence_source_versions,
                )
                for row in player_rows
            ),
            *(
                _target_context_reference(
                    row,
                    source_versions=evidence_source_versions,
                )
                for row in context_rows
            ),
        ]
        family_claims, quality_policy = _quality_contract(
            population,
            team_rows=team_rows,
            player_rows=player_rows,
            context_rows=context_rows,
        )
        snapshot = build_historical_snapshot(
            game_id=str(game.id),
            scheduled_first_pitch=_orm_utc(game.starts_at),
            builder_version=A1_BUILDER_VERSION,
            feature_schema_version=GATE3_FEATURE_SCHEMA.version,
            feature_version_id=str(feature_version.id),
            features=features,
            evidence=evidence,
            quality_policy=quality_policy,
            family_evidence=family_claims,
            missing_fields=missing,
            provisional_fields=(
                _tier_b_provisional_feature_names(features)
                if population is ReconstructionPopulation.TIER_B_ENHANCED
                else ()
            ),
            timing_risk_families=(
                ("target_context",)
                if population is ReconstructionPopulation.TIER_B_ENHANCED
                else ()
            ),
            reconstruction_metadata={
                "snapshot_type": HISTORICAL_SNAPSHOT_TYPE,
                "population": population.value,
                "quality_tier": population.tier.value,
                "operational_source_mode": SOURCE_MODE,
                "operational_source_mode_is_not_snapshot_type": True,
                "target_outcome_used_in_features": False,
                "target_performance_values_used": False,
                "current_game_excluded_from_prior_history": True,
                "cutoff_policy_version": CUTOFF_POLICY_VERSION,
                "feature_derivation": feature_metadata,
                "quality_semantics": {
                    "snapshot_quality": "TEMPORAL_INTEGRITY_ONLY",
                    "persisted_admission_quality": (
                        "MIN_TEMPORAL_INTEGRITY_AND_FEATURE_COVERAGE"
                    ),
                },
                "sample_reliability_method": "FEATURE_COMPLETENESS_V3",
                "target_context": [_target_context_metadata(row) for row in context_rows],
            },
        )
        if snapshot.tier is not population.tier:
            return self._insufficient(
                game,
                population,
                f"SNAPSHOT_TIER_MISMATCH:{snapshot.tier.value}",
                prior_team_rows=len(team_rows),
                prior_player_rows=len(player_rows),
                target_context_rows=len(context_rows),
            )
        return ReconstructionResult(
            status="BUILT",
            population=population,
            game_id=str(game.id),
            snapshot=snapshot,
            reason=None,
            prior_team_rows=len(team_rows),
            prior_player_rows=len(player_rows),
            target_context_rows=len(context_rows),
        )

    def materialize_game(
        self,
        game: Game,
        *,
        population: ReconstructionPopulation,
        computed_at: datetime | None = None,
        code_revision: str,
    ) -> MaterializedReconstructionResult:
        generated_at = as_utc(computed_at or datetime.now(UTC))
        feature_version = self.ensure_feature_version(population)
        built = self.build_snapshot(
            game,
            population=population,
            feature_version=feature_version,
            computed_at=generated_at,
        )
        if built.snapshot is None:
            return MaterializedReconstructionResult(built, None)
        source_ids = {
            UUID(item.evidence_id)
            for item in built.snapshot.evidence
        }
        team_rows_by_id = _load_rows_by_ids(
            self.session,
            TeamGameHistoryObservation,
            source_ids,
        )
        player_rows_by_id = _load_rows_by_ids(
            self.session,
            PlayerGameHistoryObservation,
            source_ids,
        )
        canonical_rows = [
            row
            for identifier in source_ids
            for row in (
                team_rows_by_id.get(identifier),
                player_rows_by_id.get(identifier),
            )
            if row is not None
        ]
        source_by_id = _load_rows_by_ids(
            self.session,
            HistoricalSourceVersion,
            {row.source_version_id for row in canonical_rows},
        )
        if not source_by_id or any(row is None for row in source_by_id.values()):
            raise HistoricalMaterializationError("Canonical source bundle is incomplete")
        source_versions = tuple(sorted(
            source_by_id.values(),
            key=lambda row: (row.source_key, row.dataset_version),
        ))
        checkpoint = self.session.get(
            HistoryBackfillCheckpoint,
            canonical_rows[0].checkpoint_id,
        )
        if checkpoint is None:
            raise HistoricalMaterializationError("Canonical checkpoint is missing")
        final_outcome = self._latest_target_outcome(game, computed_at=generated_at)
        persisted = persist_historical_snapshot(
            self.session,
            game=game,
            feature_version=feature_version,
            checkpoint=checkpoint,
            historical_snapshot=built.snapshot,
            source_versions=source_versions,
            code_revision=code_revision,
            final_outcome=final_outcome,
            computed_at=generated_at,
            materializer_version=population.materializer_version,
        )
        return MaterializedReconstructionResult(built, persisted)

    def materialize_eligible_games(
        self,
        *,
        population: ReconstructionPopulation,
        computed_at: datetime | None = None,
        code_revision: str,
        limit: int | None = None,
    ) -> tuple[MaterializedReconstructionResult, ...]:
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive when supplied")
        generated_at = as_utc(computed_at or datetime.now(UTC))
        games = list(self.session.scalars(select(Game).where(
            Game.source_mode == SOURCE_MODE,
            Game.status == GameStatus.FINAL,
            Game.starts_at < generated_at,
        ).order_by(Game.starts_at, Game.id)))
        results: list[MaterializedReconstructionResult] = []
        for game in games:
            if limit is not None and len(results) >= limit:
                break
            results.append(self.materialize_game(
                game,
                population=population,
                computed_at=generated_at,
                code_revision=code_revision,
            ))
        return tuple(results)

    def _prior_team_rows(
        self,
        game: Game,
        *,
        cutoff: datetime,
        computed_at: datetime,
        population: ReconstructionPopulation,
    ) -> list[TeamGameHistoryObservation]:
        allowed = (
            (EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME.value,)
            if population is ReconstructionPopulation.TIER_A_STRICT
            else (
                EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME.value,
                EvidenceClassification.PARTIALLY_RECONSTRUCTABLE.value,
            )
        )
        rows = list(self.session.scalars(select(TeamGameHistoryObservation).where(
            TeamGameHistoryObservation.season_id == game.season_id,
            TeamGameHistoryObservation.game_id != game.id,
            TeamGameHistoryObservation.source_mode == SOURCE_MODE,
            TeamGameHistoryObservation.temporal_semantics == TEMPORAL_SEMANTICS,
            TeamGameHistoryObservation.evidence_classification.in_(allowed),
            TeamGameHistoryObservation.event_at < cutoff,
            TeamGameHistoryObservation.evidence_end_at < cutoff,
            TeamGameHistoryObservation.eligible_at <= cutoff,
            TeamGameHistoryObservation.retrieved_at <= computed_at,
        ).order_by(
            TeamGameHistoryObservation.event_at,
            TeamGameHistoryObservation.game_id,
            TeamGameHistoryObservation.team_id,
            TeamGameHistoryObservation.id,
        )))
        return self._governed_latest(rows, cutoff=cutoff, computed_at=computed_at)

    def _prior_player_rows(
        self,
        game: Game,
        *,
        cutoff: datetime,
        computed_at: datetime,
        population: ReconstructionPopulation,
        selected_player_ids: set[Any],
        selected_team_ids: set[Any],
    ) -> list[PlayerGameHistoryObservation]:
        allowed = (
            (EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME.value,)
            if population is ReconstructionPopulation.TIER_A_STRICT
            else (
                EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME.value,
                EvidenceClassification.PARTIALLY_RECONSTRUCTABLE.value,
            )
        )
        query = select(PlayerGameHistoryObservation).where(
            PlayerGameHistoryObservation.season_id == game.season_id,
            PlayerGameHistoryObservation.game_id != game.id,
            PlayerGameHistoryObservation.source_mode == SOURCE_MODE,
            PlayerGameHistoryObservation.temporal_semantics == TEMPORAL_SEMANTICS,
            PlayerGameHistoryObservation.evidence_classification.in_(allowed),
            PlayerGameHistoryObservation.event_at < cutoff,
            PlayerGameHistoryObservation.evidence_end_at < cutoff,
            PlayerGameHistoryObservation.eligible_at <= cutoff,
            PlayerGameHistoryObservation.retrieved_at <= computed_at,
        )
        selected_predicates = []
        if selected_player_ids:
            selected_predicates.append(
                PlayerGameHistoryObservation.player_id.in_(selected_player_ids)
            )
        if selected_team_ids:
            selected_predicates.append(and_(
                PlayerGameHistoryObservation.team_id.in_(selected_team_ids),
                PlayerGameHistoryObservation.role == "PITCHER",
            ))
        if not selected_predicates:
            return []
        query = query.where(or_(*selected_predicates))
        rows = list(self.session.scalars(query.order_by(
            PlayerGameHistoryObservation.event_at,
            PlayerGameHistoryObservation.game_id,
            PlayerGameHistoryObservation.player_id,
            PlayerGameHistoryObservation.role,
            PlayerGameHistoryObservation.id,
        )))
        return self._governed_latest(rows, cutoff=cutoff, computed_at=computed_at)

    def _target_context_rows(
        self,
        game: Game,
        *,
        computed_at: datetime,
    ) -> list[PlayerGameHistoryObservation]:
        rows = list(self.session.scalars(select(PlayerGameHistoryObservation).where(
            PlayerGameHistoryObservation.game_id == game.id,
            PlayerGameHistoryObservation.source_mode == SOURCE_MODE,
            PlayerGameHistoryObservation.temporal_semantics == TEMPORAL_SEMANTICS,
            PlayerGameHistoryObservation.role.in_(("PITCHER", "BATTER")),
            PlayerGameHistoryObservation.started.is_(True),
            PlayerGameHistoryObservation.retrieved_at <= computed_at,
            PlayerGameHistoryObservation.event_at >= deterministic_cutoff(_orm_utc(game.starts_at)),
            PlayerGameHistoryObservation.eligible_at >= deterministic_cutoff(_orm_utc(game.starts_at)),
        ).order_by(
            PlayerGameHistoryObservation.team_id,
            PlayerGameHistoryObservation.role,
            PlayerGameHistoryObservation.player_id,
            PlayerGameHistoryObservation.id,
        )))
        latest: dict[tuple[Any, ...], PlayerGameHistoryObservation] = {}
        for row in rows:
            key = (
                row.game_id,
                row.player_id,
                row.team_id,
                row.role,
                row.source_version_id,
                row.source_record_id,
            )
            existing = latest.get(key)
            if existing is None or (
                int(row.revision), _orm_utc(row.retrieved_at), str(row.id)
            ) > (
                int(existing.revision), _orm_utc(existing.retrieved_at), str(existing.id)
            ):
                latest[key] = row
        result: list[PlayerGameHistoryObservation] = []
        seen: set[tuple[Any, str]] = set()
        for row in sorted(
            latest.values(),
            key=lambda item: (str(item.team_id), item.role, str(item.player_id), str(item.id)),
        ):
            values = row.values if isinstance(row.values, dict) else {}
            starter = (
                row.role == "PITCHER"
                and values.get("actual_starting_pitcher") is True
                and values.get("starter_provenance")
                == "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION"
            )
            lineup = (
                row.role == "BATTER"
                and values.get("lineup_provenance") == "HISTORICAL_ACTUAL_LINEUP"
                and _lineup_order(values.get("batting_order")) is not None
            )
            key = (row.player_id, row.role)
            if (starter or lineup) and key not in seen:
                result.append(row)
                seen.add(key)
        current_outcomes = _latest_outcomes_by_history_identity(
            self.session,
            result,
            computed_at=computed_at,
        )
        return [
            row
            for row in result
            if (
                current := current_outcomes.get((row.game_id, row.source_version_id))
            ) is not None
            and current.id == row.final_outcome_id
        ]

    def _governed_latest(
        self,
        rows: Iterable[Any],
        *,
        cutoff: datetime,
        computed_at: datetime,
    ) -> list[Any]:
        rows = tuple(rows)
        current_outcomes = _latest_outcomes_by_history_identity(
            self.session,
            rows,
            computed_at=computed_at,
        )
        sources = _load_rows_by_ids(
            self.session,
            HistoricalSourceVersion,
            {row.source_version_id for row in rows},
        )
        checkpoints = _load_rows_by_ids(
            self.session,
            HistoryBackfillCheckpoint,
            {row.checkpoint_id for row in rows},
        )
        selected: dict[tuple[Any, ...], Any] = {}
        for row in rows:
            source = sources.get(row.source_version_id)
            checkpoint = checkpoints.get(row.checkpoint_id)
            current_outcome = current_outcomes.get((row.game_id, row.source_version_id))
            outcome = (
                current_outcome
                if current_outcome is not None and current_outcome.id == row.final_outcome_id
                else None
            )
            if (
                source is None
                or checkpoint is None
                or outcome is None
                or current_outcome is None
                or current_outcome.id != outcome.id
                or source.source_mode != SOURCE_MODE
                or source.temporal_semantics != TEMPORAL_SEMANTICS
                or not source.training_eligible
                or not source.evaluation_eligible
                or source.source_classification == "SYNTHETIC"
                or checkpoint.status != "SUCCEEDED"
                or checkpoint.source_version_id != row.source_version_id
                or _orm_utc(checkpoint.completed_at) > computed_at
                or outcome.game_id != row.game_id
                or outcome.source_version_id != row.source_version_id
                or outcome.source_mode != SOURCE_MODE
                or outcome.temporal_semantics != TEMPORAL_SEMANTICS
                or outcome.outcome_status != "FINAL"
                or not outcome.official
            ):
                continue
            if isinstance(row, TeamGameHistoryObservation):
                key = (
                    "TEAM",
                    row.game_id,
                    row.team_id,
                    row.source_version_id,
                    row.source_record_id,
                )
            else:
                key = (
                    "PLAYER",
                    row.game_id,
                    row.player_id,
                    row.team_id,
                    row.role,
                    row.source_version_id,
                    row.source_record_id,
                )
            existing = selected.get(key)
            if existing is None or (
                int(row.revision), _orm_utc(row.retrieved_at), str(row.id)
            ) > (
                int(existing.revision), _orm_utc(existing.retrieved_at), str(existing.id)
            ):
                selected[key] = row
        return sorted(
            selected.values(),
            key=lambda row: (_orm_utc(row.event_at), str(row.game_id), str(row.id)),
        )

    def _latest_target_outcome(
        self,
        game: Game,
        *,
        computed_at: datetime,
    ) -> GameFinalOutcome | None:
        return self.session.scalar(select(GameFinalOutcome).where(
            GameFinalOutcome.game_id == game.id,
            GameFinalOutcome.source_mode == SOURCE_MODE,
            GameFinalOutcome.temporal_semantics == TEMPORAL_SEMANTICS,
            GameFinalOutcome.outcome_status == "FINAL",
            GameFinalOutcome.official.is_(True),
            GameFinalOutcome.retrieved_at <= computed_at,
        ).order_by(GameFinalOutcome.revision.desc(), GameFinalOutcome.id.desc()))

    def _features(
        self,
        *,
        game: Game,
        home: GameTeam,
        away: GameTeam,
        cutoff: datetime,
        team_rows: list[TeamGameHistoryObservation],
        player_rows: list[PlayerGameHistoryObservation],
        context_rows: list[PlayerGameHistoryObservation],
    ) -> tuple[list[HistoricalFeature], tuple[str, ...], dict[str, Any]]:
        outcomes = _unique_prior_outcomes(self.session, team_rows, cutoff=cutoff)
        elo = replay_pregame_elo(
            [
                CompletedGameResult(
                    game_id=str(row.id),
                    home_team_id=str(row.home_team_id),
                    away_team_id=str(row.away_team_id),
                    completed_at=_orm_utc(row.final_at),
                    eligible_at=_orm_utc(row.final_at),
                    home_score=int(row.home_score),
                    away_score=int(row.away_score),
                )
                for row in outcomes
            ],
            target_game_id=str(game.id),
            home_team_id=str(home.team_id),
            away_team_id=str(away.team_id),
            cutoff=cutoff,
        )
        values: dict[str, float | None] = {
            "home_rating": elo.home_rating,
            "away_rating": elo.away_rating,
        }
        metadata: dict[str, Any] = {
            "elo": {
                "method": elo.method,
                "processed_game_ids": list(elo.processed_game_ids),
                "target_game_excluded": str(game.id) not in elo.processed_game_ids,
            },
        }
        for prefix, team_id in (("home", home.team_id), ("away", away.team_id)):
            team_outcomes = [
                row for row in outcomes if team_id in {row.home_team_id, row.away_team_id}
            ]
            scored = [
                float(row.home_score if row.home_team_id == team_id else row.away_score)
                for row in team_outcomes
            ]
            allowed = [
                float(row.away_score if row.home_team_id == team_id else row.home_score)
                for row in team_outcomes
            ]
            recent_scored = [
                score
                for row, score in zip(team_outcomes, scored, strict=True)
                if _orm_utc(row.final_at) >= cutoff - timedelta(days=14)
            ]
            posterior = estimate_true_talent(
                prior=4.4,
                prior_sample_size=20.0,
                season_value=fmean(scored) if scored else None,
                season_sample_size=float(len(scored)),
                recent_value=fmean(recent_scored) if recent_scored else None,
                recent_sample_size=float(len(recent_scored)),
                recent_evidence_multiplier=0.25,
                lower_bound=0.0,
                upper_bound=20.0,
            )
            values[f"{prefix}_runs_per_game"] = posterior.value
            values[f"{prefix}_runs_allowed_per_game"] = fmean(allowed) if allowed else None
            values[f"{prefix}_recent_runs"] = fmean(recent_scored) if recent_scored else None
            side_rows = [row for row in team_rows if row.team_id == team_id]
            values[f"{prefix}_ops"] = _last_numeric(
                side_rows,
                ("ops",),
                ("batting", "ops"),
                ("batting", "onBasePlusSlugging"),
            )
            metadata[f"{prefix}_bayesian_runs"] = {
                "method": posterior.method,
                "prior": posterior.prior,
                "prior_sample_size": 20.0,
                "season_games": len(scored),
                "recent_games": len(recent_scored),
                "target_game_excluded": True,
            }

        target_venue = self.session.get(Venue, game.venue_id) if game.venue_id else None
        team_events = _team_game_events(self.session, team_rows)
        for prefix, team_id in (("home", home.team_id), ("away", away.team_id)):
            rest = derive_rest_travel(
                (row for row in team_events if row.team_id == str(team_id)),
                team_id=str(team_id),
                target_game_id=str(game.id),
                target_first_pitch=_orm_utc(game.starts_at),
                cutoff=cutoff,
                target_venue_latitude=target_venue.latitude if target_venue else None,
                target_venue_longitude=target_venue.longitude if target_venue else None,
            )
            values[f"{prefix}_days_since_previous_game"] = (
                rest.hours_since_previous_completion / 24.0
                if rest.hours_since_previous_completion is not None
                else None
            )
            values[f"{prefix}_travel_distance_miles"] = rest.travel_miles
            metadata[f"{prefix}_rest_travel"] = {
                "method": rest.method,
                "previous_game_id": rest.previous_game_id,
                "previous_completed_at": (
                    rest.previous_completed_at.isoformat()
                    if rest.previous_completed_at is not None
                    else None
                ),
            }

        context_by_team = {
            team_id: [row for row in context_rows if row.team_id == team_id]
            for team_id in (home.team_id, away.team_id)
        }
        appearances = _appearance_events(player_rows)
        for prefix, team_id in (("home", home.team_id), ("away", away.team_id)):
            target_starter = next(
                (row for row in context_by_team[team_id] if row.role == "PITCHER"),
                None,
            )
            starter_history = [
                row
                for row in player_rows
                if target_starter is not None
                and row.player_id == target_starter.player_id
                and row.role == "PITCHER"
            ]
            values[f"{prefix}_starter_era"] = _pitching_era(starter_history)
            values[f"{prefix}_starter_xera"] = _last_numeric(
                starter_history,
                ("pitching", "xera"),
                ("pitching", "xERA"),
            )
            bullpen_history = [
                row
                for row in player_rows
                if row.team_id == team_id and row.role == "PITCHER" and row.started is not True
            ]
            values[f"{prefix}_bullpen_era"] = _pitching_era(bullpen_history)
            values[f"{prefix}_bullpen_xera"] = _last_numeric(
                bullpen_history,
                ("pitching", "xera"),
                ("pitching", "xERA"),
            )
            values[f"{prefix}_bullpen_availability"] = None
            lineup = [row for row in context_by_team[team_id] if row.role == "BATTER"]
            prior_batters = {
                row.player_id for row in player_rows if row.role == "BATTER"
            }
            values[f"{prefix}_lineup_talent_coverage"] = (
                sum(row.player_id in prior_batters for row in lineup) / len(lineup)
                if lineup
                else None
            )
            values[f"{prefix}_lineup_xwoba"] = _lineup_metric(
                lineup,
                player_rows,
                metric_paths=(("batting", "xwoba"),),
            )
            starter_workload = (
                derive_starter_workload(
                    appearances,
                    starter_id=str(target_starter.player_id),
                    cutoff=cutoff,
                    current_game_id=str(game.id),
                    team_id=None,
                )
                if target_starter is not None
                else None
            )
            bullpen_workload = derive_bullpen_workloads(
                appearances,
                team_id=str(team_id),
                cutoff=cutoff,
                current_game_id=str(game.id),
            )
            metadata[f"{prefix}_starter_bullpen_lineup"] = {
                "starter_identity_source": (
                    "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION"
                    if target_starter is not None
                    else None
                ),
                "starter_prior_games": len(starter_history),
                "bullpen_prior_appearances": len(bullpen_history),
                "starter_workload": (
                    {
                        "method": starter_workload.method,
                        "windows": [
                            {
                                "days": window.days,
                                "pitches": window.pitches,
                                "outs_recorded": window.outs_recorded,
                                "appearances": window.appearances,
                            }
                            for window in starter_workload.windows
                        ],
                    }
                    if starter_workload is not None
                    else {"status": "UNAVAILABLE_WITHOUT_CUTOFF_STARTER_IDENTITY"}
                ),
                "bullpen_workload": {
                    "method": bullpen_workload.method,
                    "relievers": len(bullpen_workload.relievers),
                    "windows": [
                        {
                            "days": days,
                            "pitches": sum(
                                reliever.window(days).pitches
                                for reliever in bullpen_workload.relievers
                            ),
                            "outs_recorded": sum(
                                reliever.window(days).outs_recorded
                                for reliever in bullpen_workload.relievers
                            ),
                            "appearances": sum(
                                reliever.window(days).appearances
                                for reliever in bullpen_workload.relievers
                            ),
                        }
                        for days in (1, 2, 3)
                    ],
                    "availability_feature": None,
                    "availability_unavailable_reason": (
                        "HISTORICAL_EXPECTED_ROLE_WEIGHTS_NOT_ESTABLISHED"
                    ),
                },
                "lineup_identity_source": (
                    "HISTORICAL_ACTUAL_LINEUP" if lineup else None
                ),
                "lineup_members": len(lineup),
                "target_performance_values_used": False,
            }

        feature_rows: list[HistoricalFeature] = []
        team_names = {
            "home_rating", "away_rating", "home_runs_per_game", "away_runs_per_game",
            "home_runs_allowed_per_game", "away_runs_allowed_per_game", "home_ops",
            "away_ops", "home_recent_runs", "away_recent_runs",
            "home_days_since_previous_game", "away_days_since_previous_game",
            "home_travel_distance_miles", "away_travel_distance_miles",
        }
        for spec in GATE3_FEATURE_SCHEMA.features:
            value = values.get(spec.name)
            if value is None:
                continue
            if spec.name.endswith("lineup_talent_coverage"):
                family = "target_context"
            else:
                family = "historical_team" if spec.name in team_names else "historical_player"
            feature_rows.append(HistoricalFeature(spec.name, family, float(value)))
        observed = {row.name for row in feature_rows}
        missing = tuple(
            spec.name
            for spec in GATE3_FEATURE_SCHEMA.features
            if spec.name not in observed and spec.name not in _DERIVED_DATASET_FEATURES
        )
        return feature_rows, missing, metadata

    @staticmethod
    def _insufficient(
        game: Game,
        population: ReconstructionPopulation,
        reason: str,
        *,
        prior_team_rows: int = 0,
        prior_player_rows: int = 0,
        target_context_rows: int = 0,
    ) -> ReconstructionResult:
        return ReconstructionResult(
            status="INSUFFICIENT_DATA",
            population=population,
            game_id=str(game.id),
            snapshot=None,
            reason=reason,
            prior_team_rows=prior_team_rows,
            prior_player_rows=prior_player_rows,
            target_context_rows=target_context_rows,
        )


def build_and_persist_historical_dataset(
    session: Session,
    *,
    population: ReconstructionPopulation,
    artifact_root: Path,
    training_cutoff: datetime,
    code_revision: str,
    minimum_quality_score: float = 0.0,
    maximum_artifact_bytes: int = DEFAULT_MAX_DATASET_ARTIFACT_BYTES,
    minimum_free_bytes_after_write: int = A1_MIN_FREE_BYTES_AFTER_WRITE,
) -> HistoricalDatasetPersistenceResult:
    """Build JSONL + manifest + ORM lineage without fitting any model."""

    feature_version = HistoricalReconstructionService(session).ensure_feature_version(
        population
    )
    built = GovernedTrainingDatasetBuilder(session).build_historical(
        cutoff_policy_version=CUTOFF_POLICY_VERSION,
        training_cutoff=as_utc(training_cutoff),
        feature_schema_version=GATE3_FEATURE_SCHEMA.version,
        quality_tiers=(population.tier.value,),
        minimum_quality_score=minimum_quality_score,
        allow_mixed_tiers=False,
        historical_feature_version_id=feature_version.id,
        historical_materializer_version=population.materializer_version,
    )
    if built.manifest.row_count == 0:
        return HistoricalDatasetPersistenceResult(
            status="INSUFFICIENT_DATA",
            population=population,
            build=built,
            artifact=None,
            manifest=None,
            reason="NO_ELIGIBLE_HISTORICAL_ROWS",
        )
    if not all(built.manifest.leakage_checks.values()):
        return HistoricalDatasetPersistenceResult(
            status="BLOCKED",
            population=population,
            build=built,
            artifact=None,
            manifest=None,
            reason="HISTORICAL_LEAKAGE_CHECK_FAILED",
        )
    artifact = TrainingDatasetArtifactStore(
        artifact_root,
        maximum_artifact_bytes=maximum_artifact_bytes,
        minimum_free_bytes_after_write=minimum_free_bytes_after_write,
    ).save(built)
    manifest = _persist_dataset_manifest(
        session,
        artifact,
        built,
        population=population,
        code_revision=code_revision,
    )
    return HistoricalDatasetPersistenceResult(
        status="SUCCEEDED",
        population=population,
        build=built,
        artifact=artifact,
        manifest=manifest,
    )


def run_historical_reconstruction_dataset(
    session: Session,
    *,
    population: ReconstructionPopulation,
    artifact_root: Path,
    computed_at: datetime,
    training_cutoff: datetime,
    code_revision: str,
    minimum_quality_score: float = 0.0,
    limit: int | None = None,
    maximum_artifact_bytes: int = DEFAULT_MAX_DATASET_ARTIFACT_BYTES,
    minimum_free_bytes_after_write: int = A1_MIN_FREE_BYTES_AFTER_WRITE,
) -> HistoricalReconstructionDatasetRunResult:
    """Canonical history -> snapshots -> immutable rows; never model fitting."""

    materializations = HistoricalReconstructionService(session).materialize_eligible_games(
        population=population,
        computed_at=as_utc(computed_at),
        code_revision=code_revision,
        limit=limit,
    )
    dataset = build_and_persist_historical_dataset(
        session,
        population=population,
        artifact_root=artifact_root,
        training_cutoff=as_utc(training_cutoff),
        code_revision=code_revision,
        minimum_quality_score=minimum_quality_score,
        maximum_artifact_bytes=maximum_artifact_bytes,
        minimum_free_bytes_after_write=minimum_free_bytes_after_write,
    )
    return HistoricalReconstructionDatasetRunResult(
        status=dataset.status,
        population=population,
        materializations=materializations,
        dataset=dataset,
    )


def _persist_dataset_manifest(
    session: Session,
    artifact: TrainingDatasetManifestData,
    built: DatasetBuildResult,
    *,
    population: ReconstructionPopulation,
    code_revision: str,
) -> TrainingDatasetManifest:
    existing = session.scalar(select(TrainingDatasetManifest).where(
        TrainingDatasetManifest.identity_checksum == artifact.checksum,
    ))
    if existing is not None:
        return existing
    if not artifact.artifact_uri or not artifact.feature_version_id:
        raise ValueError("Saved historical dataset artifact lineage is incomplete")
    feature_id = UUID(artifact.feature_version_id)
    feature = session.get(FeatureVersion, feature_id)
    if feature is None:
        raise ValueError("Historical dataset feature version is missing")
    start = _date_boundary(artifact.date_start, end=False) or as_utc(
        datetime.fromisoformat(artifact.training_cutoff.replace("Z", "+00:00"))
    )
    end = _date_boundary(artifact.date_end, end=True) or start
    row = TrainingDatasetManifest(
        name=f"mlb_historical_reconstructed_pregame_{population.tier.value.lower()}",
        version=artifact.dataset_id,
        feature_version_id=feature.id,
        source_mode=SOURCE_MODE,
        schema_version=artifact.feature_schema_version or GATE3_FEATURE_SCHEMA.version,
        target="multi_target_mlb_game",
        target_definition=artifact.target_definitions,
        cutoff_policy={
            "snapshot_type": HISTORICAL_SNAPSHOT_TYPE,
            "temporal_semantics": TEMPORAL_SEMANTICS,
            "policy_version": CUTOFF_POLICY_VERSION,
            "hours_before_first_pitch": 6,
            "population": population.value,
        },
        split_policy={
            "status": "NOT_SPLIT_STAGE_A1_DATASET_ONLY",
            "model_training_invoked": False,
        },
        feature_names=artifact.feature_names,
        seasons=artifact.seasons,
        event_start_at=start,
        event_end_at=end,
        row_count=artifact.row_count,
        game_count=artifact.row_count,
        excluded_row_count=artifact.excluded_row_count,
        artifact_uri=artifact.artifact_uri,
        artifact_checksum=artifact.rows_checksum,
        source_evidence=[item for row in built.rows for item in row.source_evidence],
        quality_summary={
            "snapshot_type": HISTORICAL_SNAPSHOT_TYPE,
            "population": population.value,
            "quality_tiers": artifact.quality_tiers,
            "quality_score_summary": artifact.quality_score_summary,
            "admission_quality_score_summary": artifact.quality_score_summary,
            "exclusion_reasons": artifact.exclusion_reasons,
            "leakage_checks": artifact.leakage_checks,
            "feature_family_coverage": artifact.feature_family_coverage,
            "feature_family_coverage_basis": artifact.feature_family_coverage_basis,
            "observed_feature_names": artifact.observed_feature_names,
            "frozen_ordered_input_names": artifact.feature_names,
            "frozen_ordered_output_names": artifact.frozen_output_names,
            "historical_row_lineage_version": HISTORICAL_ROW_LINEAGE_VERSION,
            "sample_reliability_method": "FEATURE_COMPLETENESS_V3",
            "artifact_storage_policy": artifact.artifact_storage_policy,
            "model_training_invoked": False,
        },
        code_revision=code_revision,
        identity_checksum=artifact.checksum,
        built_at=as_utc(datetime.fromisoformat(artifact.created_at.replace("Z", "+00:00"))),
    )
    session.add(row)
    session.flush()
    return row


def _validate_feature_version(
    row: FeatureVersion,
    population: ReconstructionPopulation,
) -> None:
    if (
        row.name != population.feature_version_name
        or row.version != population.feature_version
        or row.schema_hash != GATE3_FEATURE_SCHEMA.schema_hash
        or not isinstance(row.definition, dict)
        or row.definition.get("snapshot_type") != HISTORICAL_SNAPSHOT_TYPE
        or row.definition.get("population") != population.value
    ):
        raise ValueError("FeatureVersion does not match the requested A1 population")


def _quality_contract(
    population: ReconstructionPopulation,
    *,
    team_rows: list[TeamGameHistoryObservation],
    player_rows: list[PlayerGameHistoryObservation],
    context_rows: list[PlayerGameHistoryObservation],
) -> tuple[tuple[FamilyEvidence, ...], QualityPolicy]:
    team_class = _weakest_classification(team_rows)
    player_class = _weakest_classification(player_rows)
    claims = [
        FamilyEvidence(
            "historical_team",
            team_class,
            coverage=1.0,
            present=bool(team_rows),
            timing_valid=bool(team_rows),
        ),
        FamilyEvidence(
            "historical_player",
            player_class,
            coverage=1.0 if player_rows else 0.0,
            present=bool(player_rows),
            timing_valid=bool(player_rows),
        ),
    ]
    weights = [
        FamilyWeight("historical_team", 0.7, critical=True),
        FamilyWeight("historical_player", 0.3, critical=False),
    ]
    if population is ReconstructionPopulation.TIER_B_ENHANCED:
        claims.append(FamilyEvidence(
            "target_context",
            EvidenceClassification.PARTIALLY_RECONSTRUCTABLE,
            coverage=1.0,
            present=bool(context_rows),
            provisional=True,
            timing_valid=bool(context_rows),
        ))
        weights = [
            FamilyWeight("historical_team", 0.6, critical=True),
            FamilyWeight("historical_player", 0.3, critical=False),
            FamilyWeight("target_context", 0.1, critical=True),
        ]
    return tuple(claims), QualityPolicy(
        tuple(weights),
        version=f"gate3a_a1_{population.value.lower()}_quality_v3",
    )


def _weakest_classification(rows: Iterable[Any]) -> EvidenceClassification:
    values = {
        EvidenceClassification(str(row.evidence_classification))
        for row in rows
    }
    if EvidenceClassification.PARTIALLY_RECONSTRUCTABLE in values:
        return EvidenceClassification.PARTIALLY_RECONSTRUCTABLE
    return EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME


def _reference(
    row: Any,
    *,
    family: str,
    source_versions: dict[Any, HistoricalSourceVersion],
) -> HistoricalEvidenceReference:
    source = source_versions.get(row.source_version_id)
    if source is None:
        raise ValueError("Canonical evidence source version is missing")
    return HistoricalEvidenceReference(
        evidence_id=str(row.id),
        family=family,
        source=source.source_key,
        source_record_id=row.source_record_id,
        raw_sha256=row.payload_checksum,
        game_id=str(row.game_id),
        event_at=_orm_utc(row.event_at),
        eligible_at=_orm_utc(row.eligible_at),
        classification=EvidenceClassification(str(row.evidence_classification)),
    )


def _target_context_reference(
    row: PlayerGameHistoryObservation,
    *,
    source_versions: dict[Any, HistoricalSourceVersion],
) -> HistoricalEvidenceReference:
    context = _target_context_metadata(row)
    source = source_versions.get(row.source_version_id)
    if source is None:
        raise ValueError("Canonical target-context source version is missing")
    return HistoricalEvidenceReference(
        evidence_id=str(row.id),
        family="target_context",
        source=source.source_key,
        source_record_id=row.source_record_id,
        raw_sha256=row.payload_checksum,
        game_id=str(row.game_id),
        event_at=_orm_utc(row.event_at),
        eligible_at=_orm_utc(row.eligible_at),
        classification=EvidenceClassification.PARTIALLY_RECONSTRUCTABLE,
        role=EvidenceRole.TARGET_CONTEXT,
        context=context,
    )


def _target_context_metadata(row: PlayerGameHistoryObservation) -> dict[str, object]:
    values = row.values if isinstance(row.values, dict) else {}
    if row.role == "PITCHER":
        kind = "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION"
        result: dict[str, object] = {
            "context_type": kind,
            "player_id": str(row.player_id),
            "performance_values_used": False,
            "publication_time_established": False,
        }
    else:
        result = {
            "context_type": "HISTORICAL_ACTUAL_LINEUP",
            "player_id": str(row.player_id),
            "batting_order": _lineup_order(values.get("batting_order")),
            "performance_values_used": False,
            "publication_time_established": False,
        }
    return result


def _latest_outcomes_by_history_identity(
    session: Session,
    rows: Iterable[Any],
    *,
    computed_at: datetime,
) -> dict[tuple[Any, Any], GameFinalOutcome]:
    """Resolve the latest final known for every canonical history identity."""

    rows = tuple(rows)
    keys = {(row.game_id, row.source_version_id) for row in rows}
    if not keys:
        return {}
    candidates = session.scalars(
        select(GameFinalOutcome)
        .where(
            GameFinalOutcome.game_id.in_({key[0] for key in keys}),
            GameFinalOutcome.source_version_id.in_({key[1] for key in keys}),
            GameFinalOutcome.source_mode == SOURCE_MODE,
            GameFinalOutcome.temporal_semantics == TEMPORAL_SEMANTICS,
            GameFinalOutcome.outcome_status == "FINAL",
            GameFinalOutcome.official.is_(True),
            GameFinalOutcome.retrieved_at <= as_utc(computed_at),
        )
        .order_by(
            GameFinalOutcome.revision.desc(),
            GameFinalOutcome.retrieved_at.desc(),
            GameFinalOutcome.id.desc(),
        )
    )
    latest: dict[tuple[Any, Any], GameFinalOutcome] = {}
    for candidate in candidates:
        key = (candidate.game_id, candidate.source_version_id)
        if key in keys and key not in latest:
            latest[key] = candidate
    return latest


def _unique_prior_outcomes(
    session: Session,
    rows: Iterable[TeamGameHistoryObservation],
    *,
    cutoff: datetime,
) -> list[GameFinalOutcome]:
    rows = tuple(rows)
    outcomes = _load_rows_by_ids(
        session,
        GameFinalOutcome,
        {row.final_outcome_id for row in rows},
    )
    result: dict[Any, GameFinalOutcome] = {}
    for row in rows:
        outcome = outcomes.get(row.final_outcome_id)
        if (
            outcome is not None
            and outcome.official
            and outcome.outcome_status == "FINAL"
            and _orm_utc(outcome.final_at) < cutoff
        ):
            existing = result.get(outcome.game_id)
            if existing is None or outcome.revision > existing.revision:
                result[outcome.game_id] = outcome
    return sorted(result.values(), key=lambda row: (_orm_utc(row.final_at), str(row.game_id)))


def _team_game_events(
    session: Session,
    rows: Iterable[TeamGameHistoryObservation],
) -> tuple[TeamGameEvent, ...]:
    rows = tuple(rows)
    outcomes = _load_rows_by_ids(
        session,
        GameFinalOutcome,
        {row.final_outcome_id for row in rows},
    )
    games = _load_rows_by_ids(session, Game, {row.game_id for row in rows})
    venue_ids = {
        outcome.venue_id
        for outcome in outcomes.values()
        if outcome.venue_id is not None
    }
    venues = _load_rows_by_ids(session, Venue, venue_ids)
    result: dict[tuple[Any, Any], TeamGameEvent] = {}
    for row in rows:
        outcome = outcomes.get(row.final_outcome_id)
        game = games.get(row.game_id)
        venue = venues.get(outcome.venue_id) if outcome and outcome.venue_id else None
        if outcome is None or game is None:
            continue
        result[(row.game_id, row.team_id)] = TeamGameEvent(
            game_id=str(row.game_id),
            team_id=str(row.team_id),
            first_pitch=_orm_utc(outcome.scheduled_at),
            completed_at=_orm_utc(outcome.final_at),
            eligible_at=_orm_utc(row.eligible_at),
            status=HistoricalGameStatus.FINAL,
            venue_id=str(outcome.venue_id) if outcome.venue_id else None,
            venue_latitude=venue.latitude if venue else None,
            venue_longitude=venue.longitude if venue else None,
            innings=outcome.innings_played,
        )
    return tuple(sorted(result.values(), key=lambda row: (row.completed_at, row.game_id, row.team_id)))


def _load_rows_by_ids(
    session: Session,
    model: Any,
    identifiers: Iterable[Any],
) -> dict[Any, Any]:
    """Load an ORM identity set without issuing one query per evidence row."""

    ids = tuple(dict.fromkeys(identifiers))
    if not ids:
        return {}
    return {
        row.id: row
        for row in session.scalars(select(model).where(model.id.in_(ids)))
    }


def _last_numeric(rows: Iterable[Any], *paths: tuple[str, ...]) -> float | None:
    for row in reversed(tuple(rows)):
        for path in paths:
            value: Any = row.values
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            number = _finite_number(value)
            if number is not None:
                return number
    return None


def _pitching_era(rows: Iterable[PlayerGameHistoryObservation]) -> float | None:
    earned_runs = 0.0
    innings = 0.0
    for row in rows:
        values = row.values if isinstance(row.values, dict) else {}
        pitching = values.get("pitching") if isinstance(values.get("pitching"), dict) else {}
        er = _finite_number(pitching.get("earnedRuns"))
        ip = _innings(pitching.get("inningsPitched") or pitching.get("innings"))
        if er is not None and ip is not None and ip > 0:
            earned_runs += er
            innings += ip
    return earned_runs * 9.0 / innings if innings > 0 else None


def _lineup_metric(
    lineup: Iterable[PlayerGameHistoryObservation],
    history: Iterable[PlayerGameHistoryObservation],
    *,
    metric_paths: tuple[tuple[str, ...], ...],
) -> float | None:
    selected = {row.player_id for row in lineup}
    by_player: dict[Any, float] = {}
    for row in history:
        if row.player_id not in selected or row.role != "BATTER":
            continue
        value = _last_numeric((row,), *metric_paths)
        if value is not None:
            by_player[row.player_id] = value
    return fmean(by_player.values()) if by_player else None


def _appearance_events(
    rows: Iterable[PlayerGameHistoryObservation],
) -> tuple[PlayerAppearanceEvent, ...]:
    result: list[PlayerAppearanceEvent] = []
    for row in rows:
        if row.role != "PITCHER":
            continue
        values = row.values if isinstance(row.values, dict) else {}
        pitching = values.get("pitching") if isinstance(values.get("pitching"), dict) else {}
        pitches = _finite_number(
            pitching.get("numberOfPitches")
            if pitching.get("numberOfPitches") is not None
            else pitching.get("pitchesThrown")
        )
        innings = _innings(
            pitching.get("inningsPitched")
            if pitching.get("inningsPitched") is not None
            else pitching.get("innings")
        )
        if pitches is None:
            continue
        result.append(PlayerAppearanceEvent(
            event_id=str(row.id),
            game_id=str(row.game_id),
            player_id=str(row.player_id),
            team_id=str(row.team_id),
            role=(AppearanceRole.STARTER if row.started is True else AppearanceRole.RELIEVER),
            completed_at=_orm_utc(row.event_at),
            eligible_at=_orm_utc(row.eligible_at),
            pitches=pitches,
            outs_recorded=round((innings or 0.0) * 3),
            classification=EvidenceClassification(str(row.evidence_classification)),
        ))
    return tuple(result)


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _innings(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value)
    if "." not in text:
        return _finite_number(text)
    whole_text, outs_text = text.split(".", 1)
    try:
        whole, outs = int(whole_text), int(outs_text)
    except ValueError:
        return None
    if whole < 0 or outs not in {0, 1, 2}:
        return None
    return whole + outs / 3.0


def _lineup_order(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        order = int(value)
    except (TypeError, ValueError):
        return None
    return order if 1 <= order <= 9 else None


def _tier_b_provisional_feature_names(
    rows: Iterable[HistoricalFeature],
) -> tuple[str, ...]:
    return tuple(sorted(
        row.name
        for row in rows
        if row.name.startswith(("home_starter", "away_starter", "home_lineup", "away_lineup"))
    ))


def _date_boundary(value: str | None, *, end: bool) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value).replace(tzinfo=UTC)
    return parsed + timedelta(days=1) - timedelta(microseconds=1) if end else parsed


def _orm_utc(value: datetime) -> datetime:
    """Normalize ORM timestamps; SQLite drops timezone metadata in tests."""

    return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).astimezone(UTC)
