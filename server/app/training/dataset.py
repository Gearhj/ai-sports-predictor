"""Point-in-time-safe MLB training rows and immutable dataset manifests."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.historical.materialization import canonical_evidence_manifest_error
from app.markets.odds import no_vig_probabilities
from app.modeling.schema import GATE3_FEATURE_SCHEMA
from app.models import (
    FeatureVersion,
    Forecast,
    ForecastStatus,
    ForecastTrack,
    Game,
    GameFeatureSnapshot,
    GameFinalOutcome,
    GameStateObservation,
    HistoricalFeatureMaterialization,
    HistoricalSourceVersion,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
)


TARGET_DEFINITIONS = {
    "home_win": "1 when append-only final home_score > away_score; otherwise 0",
    "home_runs": "append-only final home score",
    "away_runs": "append-only final away score",
    "run_differential": "home_runs - away_runs",
    "total_runs": "home_runs + away_runs",
}
FORBIDDEN_FEATURE_TOKENS = (
    "final_score",
    "home_score",
    "away_score",
    "home_win",
    "game_result",
    "winner",
)
FORBIDDEN_HISTORICAL_MARKET_TOKENS = (
    "market",
    "odds",
    "sportsbook",
    "closing",
    "clv",
)
HISTORICAL_TEMPORAL_SEMANTICS = "RETROSPECTIVE_RECONSTRUCTION"
HISTORICAL_POPULATION = "HISTORICAL_RECONSTRUCTION"
HISTORICAL_SNAPSHOT_TYPE = "HISTORICAL_RECONSTRUCTED_PREGAME"
HISTORICAL_ROW_LINEAGE_VERSION = "gate3a_historical_row_lineage_v3"
LEGACY_HISTORICAL_ROW_LINEAGE_VERSIONS = {"gate3a_historical_row_lineage_v2"}
HISTORICAL_DERIVED_FEATURE_NAMES = {
    "feature_completeness",
    "sample_reliability",
    "lineups_confirmed",
    "starters_confirmed",
}
DEFAULT_MAX_DATASET_ARTIFACT_BYTES = 128 * 1024 * 1024
DEFAULT_MIN_FREE_BYTES_AFTER_WRITE = 64 * 1024 * 1024


class DatasetArtifactStorageError(RuntimeError):
    """Raised before writing when a dataset exceeds its storage envelope."""


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime) -> str:
    return _aware(value).astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class TrainingRow:
    game_id: str
    scheduled_first_pitch: str
    data_as_of: str
    lifecycle: str
    feature_snapshot_id: str
    feature_snapshot_checksum: str
    feature_version_id: str | None
    feature_version: str
    feature_schema_version: str
    source_mode: str
    source_evidence: list[dict[str, Any]]
    home_team_id: str
    away_team_id: str
    features: dict[str, Any]
    missing_features: list[str]
    stale_features: list[str]
    provisional_features: list[str]
    feature_completeness: float
    home_win: int
    home_runs: int
    away_runs: int
    run_differential: int
    total_runs: int
    market_home_probability: float | None
    market_timestamp: str | None
    market_books: int
    closing_home_probability: float | None
    closing_timestamp: str | None
    temporal_semantics: str | None = None
    historical_materialization_id: str | None = None
    historical_materialization_identity_checksum: str | None = None
    historical_outcome_id: str | None = None
    historical_outcome_identity_checksum: str | None = None
    historical_quality_tier: str | None = None
    historical_quality_score: float | None = None
    cutoff_policy_version: str | None = None
    materializer_version: str | None = None
    label_available_at: str | None = None


@dataclass(frozen=True)
class TrainingDatasetManifestData:
    dataset_id: str
    created_at: str
    date_start: str | None
    date_end: str | None
    seasons: list[int]
    feature_schema_version: str | None
    feature_version: str | None
    feature_version_id: str | None
    feature_names: list[str]
    row_count: int
    excluded_row_count: int
    exclusion_reasons: dict[str, int]
    source_modes: list[str]
    rows_checksum: str
    checksum: str
    target_definitions: dict[str, str]
    training_cutoff: str
    leakage_checks: dict[str, bool]
    feature_family_coverage: dict[str, float]
    artifact_uri: str | None = None
    population: str = "PROSPECTIVE_LIVE"
    temporal_semantics: str | None = None
    cutoff_policy_version: str | None = None
    quality_tiers: list[str] = field(default_factory=list)
    quality_score_summary: dict[str, float] | None = None
    observed_feature_names: list[str] | None = None
    frozen_output_names: list[str] | None = None
    feature_family_coverage_basis: str | None = None
    artifact_storage_policy: dict[str, int] | None = None


@dataclass(frozen=True)
class DatasetBuildResult:
    manifest: TrainingDatasetManifestData
    rows: tuple[TrainingRow, ...]
    exclusions: tuple[dict[str, str], ...]

    @property
    def trainable(self) -> bool:
        return self.manifest.row_count > 0 and all(self.manifest.leakage_checks.values())


class GovernedTrainingDatasetBuilder:
    """Build one labeled LIVE row per game from frozen pregame evidence.

    Targets are read only from an append-only FINAL observation that became
    available by the training cutoff. Features are read only from the exact
    immutable snapshot referenced by a published pregame forecast.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def build(
        self,
        *,
        training_cutoff: datetime | None = None,
        feature_schema_version: str | None = None,
    ) -> DatasetBuildResult:
        cutoff = _aware(training_cutoff or datetime.now(UTC))
        games = list(self.session.scalars(select(Game).order_by(Game.starts_at, Game.id)))
        rows: list[TrainingRow] = []
        exclusions: list[dict[str, str]] = []
        schemas: Counter[str] = Counter()

        for game in games:
            if game.source_mode != "LIVE":
                exclusions.append(self._excluded(game, "non_live_source_mode"))
                continue
            final = self._final_observation(game.id, cutoff)
            if final is None:
                exclusions.append(self._excluded(game, "final_outcome_unavailable"))
                continue
            candidate = self._pregame_candidate(game.id, final.scheduled_at, cutoff)
            if candidate is None:
                exclusions.append(self._excluded(game, "pregame_snapshot_unavailable"))
                continue
            forecast, snapshot = candidate
            schemas[snapshot.feature_schema_version] += 1
            requested_schema = feature_schema_version
            if requested_schema and snapshot.feature_schema_version != requested_schema:
                exclusions.append(self._excluded(game, "feature_schema_mismatch"))
                continue
            snapshot_state = self.session.get(GameStateObservation, snapshot.game_state_observation_id)
            if snapshot_state is None:
                exclusions.append(self._excluded(game, "snapshot_game_state_missing"))
                continue
            if _aware(snapshot_state.scheduled_at) != _aware(final.scheduled_at):
                exclusions.append(self._excluded(game, "schedule_identity_changed"))
                continue
            if snapshot.source_mode != "LIVE" or any(
                item.get("source_mode") not in {None, "LIVE"}
                for item in snapshot.source_evidence or []
            ):
                exclusions.append(self._excluded(game, "mixed_source_mode"))
                continue
            if not _evidence_within_cutoff(snapshot.source_evidence, snapshot.data_as_of):
                exclusions.append(self._excluded(game, "source_evidence_after_snapshot_cutoff"))
                continue
            if self._contains_forbidden_feature(snapshot.feature_values):
                exclusions.append(self._excluded(game, "target_leakage_feature_name"))
                continue
            if final.home_team_id is None or final.away_team_id is None:
                exclusions.append(self._excluded(game, "observed_team_identity_missing"))
                continue
            market = self._market_probability(
                snapshot.market_snapshot_ids,
                final.home_team_id,
                final.away_team_id,
                as_of=snapshot.data_as_of,
            )
            closing = self._closing_probability(game.id, final.home_team_id, final.away_team_id, final.scheduled_at)
            feature_version = self.session.get(FeatureVersion, snapshot.feature_version_id) if snapshot.feature_version_id else None
            home_runs, away_runs = int(final.home_score), int(final.away_score)
            row = TrainingRow(
                game_id=str(game.id),
                scheduled_first_pitch=_iso(final.scheduled_at),
                data_as_of=_iso(snapshot.data_as_of),
                lifecycle=forecast.lifecycle.value.upper(),
                feature_snapshot_id=str(snapshot.id),
                feature_snapshot_checksum=snapshot.checksum,
                feature_version_id=str(snapshot.feature_version_id) if snapshot.feature_version_id else None,
                feature_version=f"{feature_version.name}/{feature_version.version}" if feature_version else "unknown",
                feature_schema_version=snapshot.feature_schema_version,
                source_mode="LIVE",
                source_evidence=list(snapshot.source_evidence or []),
                home_team_id=str(final.home_team_id),
                away_team_id=str(final.away_team_id),
                features=_model_ready_features(snapshot, snapshot_state),
                missing_features=sorted(str(value) for value in snapshot.missing_features or []),
                stale_features=sorted(str(value) for value in snapshot.stale_features or []),
                provisional_features=sorted(str(value) for value in snapshot.provisional_features or []),
                feature_completeness=float(snapshot.feature_completeness),
                home_win=int(home_runs > away_runs),
                home_runs=home_runs,
                away_runs=away_runs,
                run_differential=home_runs - away_runs,
                total_runs=home_runs + away_runs,
                market_home_probability=market[0],
                market_timestamp=_iso(market[1]) if market[1] else None,
                market_books=market[2],
                closing_home_probability=closing[0],
                closing_timestamp=_iso(closing[1]) if closing[1] else None,
            )
            rows.append(row)

        selected_schema = feature_schema_version or (schemas.most_common(1)[0][0] if schemas else None)
        if selected_schema:
            retained: list[TrainingRow] = []
            for row in rows:
                if row.feature_schema_version == selected_schema:
                    retained.append(row)
                else:
                    exclusions.append({"game_id": row.game_id, "reason": "feature_schema_mismatch"})
            rows = retained
        rows.sort(key=lambda row: (row.scheduled_first_pitch, row.game_id))
        reasons = Counter(item["reason"] for item in exclusions)
        manifest = self._manifest(rows, exclusions, reasons, cutoff, selected_schema)
        return DatasetBuildResult(manifest=manifest, rows=tuple(rows), exclusions=tuple(exclusions))

    def build_historical(
        self,
        *,
        cutoff_policy_version: str,
        training_cutoff: datetime | None = None,
        feature_schema_version: str | None = None,
        quality_tiers: tuple[str, ...] = ("A",),
        minimum_quality_score: float = 0.0,
        allow_mixed_tiers: bool = False,
        historical_feature_version_id: UUID | str | None = None,
        historical_materializer_version: str | None = None,
    ) -> DatasetBuildResult:
        """Build governed retrospective rows without reading or creating forecasts.

        The path is intentionally separate from :meth:`build`.  It consumes an
        eligible materialization, the exact immutable feature snapshot named by
        it, and the exact governed final outcome named by it.  Operational mode
        remains ``LIVE`` while ``temporal_semantics`` identifies retrospective
        reconstruction.
        """

        if not cutoff_policy_version:
            raise ValueError("cutoff_policy_version is required")
        requested_tiers = tuple(dict.fromkeys(str(value).upper() for value in quality_tiers))
        if not requested_tiers:
            raise ValueError("At least one historical quality tier is required")
        if any(value not in {"A", "B"} for value in requested_tiers):
            raise ValueError("Historical training quality_tiers may contain only A or B")
        if len(requested_tiers) > 1 and not allow_mixed_tiers:
            raise ValueError("Mixing historical quality tiers requires allow_mixed_tiers=True")
        if (
            not math.isfinite(minimum_quality_score)
            or not 0 <= minimum_quality_score <= 100
        ):
            raise ValueError("minimum_quality_score must be finite and within [0, 100]")
        if (historical_feature_version_id is None) != (
            historical_materializer_version is None
        ):
            raise ValueError(
                "Historical feature and materializer identities must be selected together"
            )

        cutoff = _aware(training_cutoff or datetime.now(UTC))
        materialization_query = select(HistoricalFeatureMaterialization)
        if historical_feature_version_id is not None:
            try:
                selected_feature_version_id = (
                    historical_feature_version_id
                    if isinstance(historical_feature_version_id, UUID)
                    else UUID(str(historical_feature_version_id))
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid historical feature-version identifier") from exc
            materialization_query = materialization_query.where(
                HistoricalFeatureMaterialization.feature_version_id
                == selected_feature_version_id,
                HistoricalFeatureMaterialization.materializer_version
                == historical_materializer_version,
            )
        materializations = list(self.session.scalars(
            materialization_query.order_by(
                HistoricalFeatureMaterialization.scheduled_at,
                HistoricalFeatureMaterialization.game_id,
                HistoricalFeatureMaterialization.id,
            )
        ))
        rows: list[TrainingRow] = []
        exclusions: list[dict[str, str]] = []

        for materialization in materializations:
            excluded = lambda reason: exclusions.append({
                "game_id": str(materialization.game_id),
                "reason": reason,
            })
            if materialization.cutoff_policy_version != cutoff_policy_version:
                excluded("historical_cutoff_policy_mismatch")
                continue
            if materialization.source_mode != "LIVE":
                excluded("historical_non_live_source_mode")
                continue
            if materialization.temporal_semantics != HISTORICAL_TEMPORAL_SEMANTICS:
                excluded("historical_temporal_semantics_mismatch")
                continue
            if not materialization.training_eligible:
                excluded("historical_materialization_not_training_eligible")
                continue
            if materialization.eligibility_status != "ELIGIBLE":
                excluded("historical_materialization_ineligible")
                continue
            tier = str(materialization.quality_tier).upper()
            if tier not in requested_tiers:
                excluded("historical_quality_tier_not_allowed")
                continue
            quality_score = float(materialization.quality_score)
            if (
                not math.isfinite(quality_score)
                or not 0 <= quality_score <= 100
                or quality_score == 0
                or quality_score < minimum_quality_score
            ):
                excluded("historical_quality_score_below_threshold")
                continue

            game = self.session.get(Game, materialization.game_id)
            snapshot = self.session.get(
                GameFeatureSnapshot,
                materialization.game_feature_snapshot_id,
            )
            outcome = (
                self.session.get(GameFinalOutcome, materialization.game_final_outcome_id)
                if materialization.game_final_outcome_id is not None
                else None
            )
            if game is None:
                excluded("historical_game_missing")
                continue
            if snapshot is None:
                excluded("historical_snapshot_missing")
                continue
            if outcome is None:
                excluded("historical_final_outcome_missing")
                continue

            integrity_reason = self._historical_integrity_reason(
                materialization,
                snapshot,
                outcome,
                game,
                training_cutoff=cutoff,
                minimum_quality_score=minimum_quality_score,
            )
            if integrity_reason is not None:
                excluded(integrity_reason)
                continue
            if feature_schema_version and snapshot.feature_schema_version != feature_schema_version:
                excluded("feature_schema_mismatch")
                continue
            if self._contains_forbidden_feature(snapshot.feature_values):
                excluded("target_leakage_feature_name")
                continue
            if self._contains_historical_market_feature(snapshot.feature_values):
                excluded("historical_market_or_clv_feature_forbidden")
                continue
            if snapshot.market_snapshot_ids:
                excluded("historical_market_evidence_forbidden")
                continue

            feature_version = self.session.get(FeatureVersion, snapshot.feature_version_id)
            if feature_version is None:
                excluded("historical_feature_version_missing")
                continue
            home_runs, away_runs = int(outcome.home_score), int(outcome.away_score)
            rows.append(TrainingRow(
                game_id=str(game.id),
                scheduled_first_pitch=_iso(materialization.scheduled_at),
                data_as_of=_iso(materialization.cutoff_at),
                lifecycle="HISTORICAL_T_MINUS_6H",
                feature_snapshot_id=str(snapshot.id),
                feature_snapshot_checksum=snapshot.checksum,
                feature_version_id=str(snapshot.feature_version_id),
                feature_version=f"{feature_version.name}/{feature_version.version}",
                feature_schema_version=snapshot.feature_schema_version,
                source_mode="LIVE",
                source_evidence=_compact_historical_lineage(
                    materialization,
                    snapshot,
                    outcome,
                ),
                home_team_id=str(outcome.home_team_id),
                away_team_id=str(outcome.away_team_id),
                features=_historical_model_ready_features(snapshot),
                missing_features=sorted(str(value) for value in snapshot.missing_features or []),
                stale_features=sorted(str(value) for value in snapshot.stale_features or []),
                provisional_features=sorted(
                    str(value) for value in snapshot.provisional_features or []
                ),
                feature_completeness=float(snapshot.feature_completeness),
                home_win=int(home_runs > away_runs),
                home_runs=home_runs,
                away_runs=away_runs,
                run_differential=home_runs - away_runs,
                total_runs=home_runs + away_runs,
                market_home_probability=None,
                market_timestamp=None,
                market_books=0,
                closing_home_probability=None,
                closing_timestamp=None,
                temporal_semantics=HISTORICAL_TEMPORAL_SEMANTICS,
                historical_materialization_id=str(materialization.id),
                historical_materialization_identity_checksum=materialization.identity_checksum,
                historical_outcome_id=str(outcome.id),
                historical_outcome_identity_checksum=outcome.identity_checksum,
                historical_quality_tier=tier,
                historical_quality_score=quality_score,
                cutoff_policy_version=materialization.cutoff_policy_version,
                materializer_version=materialization.materializer_version,
                label_available_at=_iso(outcome.label_available_at),
            ))

        duplicate_game_ids = {
            game_id for game_id, count in Counter(row.game_id for row in rows).items() if count > 1
        }
        if duplicate_game_ids:
            rows = [row for row in rows if row.game_id not in duplicate_game_ids]
            exclusions.extend(
                {"game_id": game_id, "reason": "duplicate_historical_materialization"}
                for game_id in sorted(duplicate_game_ids)
            )

        selected_schema = feature_schema_version
        if selected_schema is None and rows:
            selected_schema = Counter(row.feature_schema_version for row in rows).most_common(1)[0][0]
        if selected_schema:
            retained: list[TrainingRow] = []
            for row in rows:
                if row.feature_schema_version == selected_schema:
                    retained.append(row)
                else:
                    exclusions.append({"game_id": row.game_id, "reason": "feature_schema_mismatch"})
            rows = retained
        if not allow_mixed_tiers and len({row.historical_quality_tier for row in rows}) > 1:
            raise ValueError("Historical result contains mixed quality tiers")

        rows.sort(key=lambda row: (row.scheduled_first_pitch, row.game_id))
        reasons = Counter(item["reason"] for item in exclusions)
        manifest = self._manifest(
            rows,
            exclusions,
            reasons,
            cutoff,
            selected_schema,
            population=HISTORICAL_POPULATION,
            temporal_semantics=HISTORICAL_TEMPORAL_SEMANTICS,
            cutoff_policy_version=cutoff_policy_version,
            allow_mixed_tiers=allow_mixed_tiers,
        )
        return DatasetBuildResult(manifest=manifest, rows=tuple(rows), exclusions=tuple(exclusions))

    def _final_observation(self, game_id: Any, cutoff: datetime) -> GameStateObservation | None:
        candidates = list(self.session.scalars(
            select(GameStateObservation).where(
                GameStateObservation.game_id == game_id,
                GameStateObservation.source_mode == "LIVE",
                GameStateObservation.available_as_of <= cutoff,
                GameStateObservation.observed_at <= cutoff,
                GameStateObservation.home_score.is_not(None),
                GameStateObservation.away_score.is_not(None),
            ).order_by(
                GameStateObservation.available_as_of.desc(),
                GameStateObservation.observed_at.desc(),
                GameStateObservation.id.desc(),
            )
        ))
        return next(
            (row for row in candidates if str(row.status).lower() in {"final", "game_over", "completed"}),
            None,
        )

    def _pregame_candidate(
        self,
        game_id: Any,
        scheduled_at: datetime,
        cutoff: datetime,
    ) -> tuple[Forecast, GameFeatureSnapshot] | None:
        rows = list(self.session.execute(
            select(Forecast, GameFeatureSnapshot)
            .join(GameFeatureSnapshot, GameFeatureSnapshot.id == Forecast.feature_snapshot_id)
            .where(
                Forecast.game_id == game_id,
                Forecast.status == ForecastStatus.PUBLISHED,
                Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
                Forecast.source_mode == "LIVE",
                Forecast.published_at.is_not(None),
                Forecast.published_at < _aware(scheduled_at),
                Forecast.generated_at < _aware(scheduled_at),
                Forecast.available_as_of < _aware(scheduled_at),
                Forecast.available_as_of <= cutoff,
                GameFeatureSnapshot.source_mode == "LIVE",
                GameFeatureSnapshot.data_as_of < _aware(scheduled_at),
                GameFeatureSnapshot.generated_at < _aware(scheduled_at),
            )
            .order_by(
                GameFeatureSnapshot.data_as_of.desc(),
                Forecast.version.desc(),
                Forecast.id.desc(),
            )
        ))
        return rows[0] if rows else None

    def _market_probability(
        self,
        raw_ids: Iterable[Any],
        home_team_id: Any,
        away_team_id: Any,
        *,
        closing_only: bool = False,
        as_of: datetime | None = None,
    ) -> tuple[float | None, datetime | None, int]:
        ids: list[UUID] = []
        for value in raw_ids or []:
            try:
                ids.append(value if isinstance(value, UUID) else UUID(str(value)))
            except (TypeError, ValueError):
                continue
        if not ids:
            return None, None, 0
        query = (
            select(Market, MarketOutcome, OddsSnapshot)
            .join(MarketOutcome, MarketOutcome.market_id == Market.id)
            .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
            .where(
                OddsSnapshot.id.in_(ids),
                OddsSnapshot.source_mode == "LIVE",
                Market.market_type == MarketType.MONEYLINE,
            )
        )
        if closing_only:
            query = query.where(OddsSnapshot.is_closing.is_(True))
        if as_of is not None:
            query = query.where(
                OddsSnapshot.available_as_of <= _aware(as_of),
                OddsSnapshot.observed_at <= _aware(as_of),
            )
        by_book: dict[Any, dict[Any, OddsSnapshot]] = {}
        for _, outcome, snapshot in self.session.execute(query):
            if outcome.participant_team_id not in {home_team_id, away_team_id}:
                continue
            by_book.setdefault(snapshot.sportsbook_id, {})[outcome.participant_team_id] = snapshot
        probabilities: list[float] = []
        timestamps: list[datetime] = []
        for outcomes in by_book.values():
            if home_team_id not in outcomes or away_team_id not in outcomes:
                continue
            home, away = outcomes[home_team_id], outcomes[away_team_id]
            no_vig = no_vig_probabilities(
                {"home": home.decimal_odds, "away": away.decimal_odds},
                american=False,
            )
            probabilities.append(float(no_vig.probabilities["home"]))
            timestamps.append(max(_aware(home.observed_at), _aware(away.observed_at)))
        if not probabilities:
            return None, None, 0
        return sum(probabilities) / len(probabilities), max(timestamps), len(probabilities)

    def _closing_probability(
        self,
        game_id: Any,
        home_team_id: Any,
        away_team_id: Any,
        scheduled_at: datetime,
    ) -> tuple[float | None, datetime | None]:
        ids = list(self.session.scalars(
            select(OddsSnapshot.id)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .where(
                Market.game_id == game_id,
                Market.market_type == MarketType.MONEYLINE,
                OddsSnapshot.source_mode == "LIVE",
                OddsSnapshot.is_closing.is_(True),
                OddsSnapshot.available_as_of < _aware(scheduled_at),
                OddsSnapshot.observed_at < _aware(scheduled_at),
            )
        ))
        probability, observed_at, _ = self._market_probability(
            ids,
            home_team_id,
            away_team_id,
            closing_only=True,
            as_of=scheduled_at,
        )
        return probability, observed_at

    def _historical_integrity_reason(
        self,
        materialization: HistoricalFeatureMaterialization,
        snapshot: GameFeatureSnapshot,
        outcome: GameFinalOutcome,
        game: Game,
        *,
        training_cutoff: datetime,
        minimum_quality_score: float,
    ) -> str | None:
        """Return a fail-closed reason when the three immutable identities disagree."""

        scheduled_at = _aware(materialization.scheduled_at)
        cutoff_at = _aware(materialization.cutoff_at)
        if cutoff_at != scheduled_at - timedelta(hours=6):
            return "historical_cutoff_not_t_minus_six_hours"
        if scheduled_at >= training_cutoff:
            return "historical_game_not_before_training_cutoff"
        if (
            materialization.game_id != game.id
            or snapshot.game_id != game.id
            or outcome.game_id != game.id
        ):
            return "historical_game_identity_mismatch"
        if game.source_mode != "LIVE" or snapshot.source_mode != "LIVE" or outcome.source_mode != "LIVE":
            return "historical_mixed_source_mode"
        if outcome.temporal_semantics != HISTORICAL_TEMPORAL_SEMANTICS:
            return "historical_outcome_temporal_semantics_mismatch"
        if materialization.game_feature_snapshot_id != snapshot.id:
            return "historical_snapshot_reference_mismatch"
        if materialization.game_final_outcome_id != outcome.id:
            return "historical_outcome_reference_mismatch"
        if (
            snapshot.feature_version_id is None
            or materialization.feature_version_id != snapshot.feature_version_id
        ):
            return "historical_feature_version_identity_mismatch"
        if _aware(snapshot.data_as_of) != cutoff_at or _aware(snapshot.data_as_of) >= scheduled_at:
            return "historical_snapshot_cutoff_identity_mismatch"
        if _aware(snapshot.generated_at) != _aware(materialization.generated_at):
            return "historical_snapshot_generation_identity_mismatch"
        if snapshot.frozen_at is None or _aware(snapshot.frozen_at) < _aware(snapshot.data_as_of):
            return "historical_snapshot_not_frozen"
        reconstruction = (
            (snapshot.bayesian_metadata or {}).get("historical_reconstruction")
            if isinstance(snapshot.bayesian_metadata, dict)
            else None
        )
        requires_a1_type = snapshot.feature_schema_version == "mlb_pregame_gate3_v1"
        if requires_a1_type and (
            not isinstance(reconstruction, dict)
            or reconstruction.get("snapshot_type") != HISTORICAL_SNAPSHOT_TYPE
            or reconstruction.get("operational_source_mode_is_not_snapshot_type") is not True
        ):
            return "historical_snapshot_type_missing_or_mislabelled"
        if isinstance(reconstruction, dict) and reconstruction.get("snapshot_type") not in {
            None,
            HISTORICAL_SNAPSHOT_TYPE,
        }:
            return "historical_snapshot_type_missing_or_mislabelled"
        if _aware(outcome.scheduled_at) != scheduled_at:
            return "historical_schedule_identity_mismatch"
        if outcome.outcome_status != "FINAL" or not outcome.official:
            return "historical_outcome_not_official_final"
        if outcome.home_team_id == outcome.away_team_id:
            return "historical_outcome_team_identity_invalid"
        if _aware(outcome.label_available_at) > training_cutoff:
            return "historical_label_unavailable_at_training_cutoff"
        if _aware(outcome.final_at) < scheduled_at:
            return "historical_outcome_time_invalid"
        if (
            not math.isfinite(float(snapshot.feature_completeness))
            or not 0 <= float(snapshot.feature_completeness) <= 1
        ):
            return "historical_feature_completeness_invalid"
        if float(materialization.quality_score) < minimum_quality_score:
            return "historical_quality_score_below_threshold"

        checksum_values = (
            snapshot.checksum,
            materialization.source_bundle_checksum,
            materialization.evidence_manifest_checksum,
            materialization.identity_checksum,
            outcome.payload_checksum,
            outcome.identity_checksum,
        )
        if any(not _is_sha256(value) for value in checksum_values):
            return "historical_identity_checksum_invalid"
        if _checksum(materialization.source_bundle) != materialization.source_bundle_checksum.lower():
            return "historical_source_bundle_checksum_mismatch"
        if _checksum(materialization.evidence_manifest) != materialization.evidence_manifest_checksum.lower():
            return "historical_evidence_manifest_checksum_mismatch"
        if _canonical(snapshot.source_evidence or []) != _canonical(materialization.evidence_manifest):
            return "historical_snapshot_evidence_identity_mismatch"

        components = materialization.quality_components
        if not isinstance(components, dict):
            return "historical_quality_components_invalid"
        try:
            component_score = float(components["score"])
        except (KeyError, TypeError, ValueError):
            return "historical_quality_components_invalid"
        if (
            not math.isfinite(component_score)
            or not math.isclose(
                component_score,
                float(materialization.quality_score),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            or str(components.get("tier", "")).upper()
            != str(materialization.quality_tier).upper()
        ):
            return "historical_quality_identity_mismatch"
        temporal_integrity = components.get("temporal_integrity")
        feature_coverage = components.get("feature_coverage")
        materializer_version = str(materialization.materializer_version)
        quality_contract_version = (
            "v3"
            if materializer_version.endswith("_v3")
            else "v2" if materializer_version.endswith("_v2") else None
        )
        if quality_contract_version is not None and (
            components.get("policy_version")
            != f"gate3a_historical_admission_quality_{quality_contract_version}"
            or temporal_integrity is None
            or feature_coverage is None
        ):
            return "historical_quality_components_invalid"
        if temporal_integrity is not None or feature_coverage is not None:
            if not isinstance(temporal_integrity, dict) or not isinstance(
                feature_coverage,
                dict,
            ):
                return "historical_quality_components_invalid"
            feature_names = set(snapshot.feature_values or {})
            missing_names = set(snapshot.missing_features or [])
            expected_names = feature_names | missing_names
            requires_frozen_v3 = (
                quality_contract_version == "v3"
                and isinstance(reconstruction, dict)
                and reconstruction.get("builder_version")
                == "gate3a_a1_reconstruction_v3"
            )
            if requires_frozen_v3:
                frozen_names = {
                    spec.name
                    for spec in GATE3_FEATURE_SCHEMA.features
                    if spec.name not in HISTORICAL_DERIVED_FEATURE_NAMES
                }
                if (
                    snapshot.feature_schema_version != GATE3_FEATURE_SCHEMA.version
                    or expected_names != frozen_names
                    or feature_names & missing_names
                    or feature_coverage.get("basis")
                    != "FROZEN_SCHEMA_NON_DERIVED_FEATURES"
                ):
                    return "historical_frozen_feature_coverage_contract_mismatch"
                expected_names = frozen_names
            observed_count = sum(
                (snapshot.feature_values or {}).get(name) is not None
                for name in expected_names
            )
            expected_count = len(expected_names)
            expected_fraction = (
                observed_count / expected_count if expected_count else 0.0
            )
            try:
                temporal_score = float(temporal_integrity["score"])
                coverage_score = float(feature_coverage["score"])
                coverage_fraction = float(feature_coverage["fraction"])
                component_observed = int(feature_coverage["observed_feature_count"])
                component_expected = int(feature_coverage["expected_feature_count"])
            except (KeyError, TypeError, ValueError):
                return "historical_quality_components_invalid"
            if (
                components.get("admission_rule")
                != "MIN_TEMPORAL_INTEGRITY_AND_FEATURE_COVERAGE"
                or not math.isclose(
                    coverage_fraction,
                    float(snapshot.feature_completeness),
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
                or not math.isclose(
                    coverage_fraction,
                    expected_fraction,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
                or not math.isclose(
                    coverage_score,
                    expected_fraction * 100.0,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
                or component_observed != observed_count
                or component_expected != expected_count
                or not math.isclose(
                    component_score,
                    min(temporal_score, coverage_score),
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
            ):
                return "historical_quality_identity_mismatch"
            metadata_temporal = (
                reconstruction.get("temporal_integrity")
                if isinstance(reconstruction, dict)
                else None
            )
            if metadata_temporal is not None and _canonical(
                metadata_temporal
            ) != _canonical(temporal_integrity):
                return "historical_quality_identity_mismatch"

        bundle_reason, source_version_ids = self._historical_source_bundle_reason(
            materialization.source_bundle,
            materialization_tier=str(materialization.quality_tier).upper(),
        )
        if bundle_reason is not None:
            return bundle_reason
        if not _historical_evidence_within_cutoff(
            materialization.evidence_manifest,
            cutoff_at,
            target_game_id=str(game.id),
            source_version_ids=source_version_ids,
        ):
            return "historical_evidence_after_cutoff_or_mixed"
        canonical_evidence_error = canonical_evidence_manifest_error(
            self.session,
            materialization.evidence_manifest,
            cutoff=cutoff_at,
            target_game_id=str(game.id),
            source_version_ids=source_version_ids,
            computed_at=training_cutoff,
        )
        if canonical_evidence_error is not None:
            return "historical_canonical_evidence_invalid"

        latest_outcome = self.session.scalar(
            select(GameFinalOutcome).where(
                GameFinalOutcome.game_id == game.id,
                GameFinalOutcome.source_version_id == outcome.source_version_id,
                GameFinalOutcome.source_mode == "LIVE",
                GameFinalOutcome.label_available_at <= training_cutoff,
            ).order_by(GameFinalOutcome.revision.desc(), GameFinalOutcome.id.desc())
        )
        if latest_outcome is None or latest_outcome.id != outcome.id:
            return "historical_outcome_revision_not_current_at_cutoff"
        return None

    def _historical_source_bundle_reason(
        self,
        raw_bundle: Iterable[Any],
        *,
        materialization_tier: str,
    ) -> tuple[str | None, set[str]]:
        bundle = tuple(raw_bundle or [])
        if not bundle:
            return "historical_source_bundle_missing", set()
        source_version_ids: set[str] = set()
        tier_rank = {"C": 0, "A": 1, "B": 2, "EXCLUDED": 3}
        for raw in bundle:
            direct = raw if isinstance(raw, dict) else {}
            identifier = direct.get("source_version_id") or direct.get("id") or raw
            try:
                source_uuid = identifier if isinstance(identifier, UUID) else UUID(str(identifier))
            except (TypeError, ValueError):
                return "historical_source_bundle_reference_invalid", set()
            source = self.session.get(HistoricalSourceVersion, source_uuid)
            if source is None:
                return "historical_source_version_missing", set()
            source_version_ids.add(str(source.id))
            if source.source_mode != "LIVE":
                return "historical_source_bundle_mixed_mode", set()
            if source.temporal_semantics != HISTORICAL_TEMPORAL_SEMANTICS:
                return "historical_source_bundle_mixed_population", set()
            if not source.training_eligible or not source.evaluation_eligible:
                return "historical_source_bundle_ineligible", set()
            source_tier = str(source.quality_tier).upper()
            if source_tier not in {"A", "B"}:
                return "historical_source_bundle_low_tier", set()
            if tier_rank[source_tier] > tier_rank[materialization_tier]:
                return "historical_materialization_tier_overstates_source", set()
            for name, expected in (
                ("source_mode", source.source_mode),
                ("temporal_semantics", source.temporal_semantics),
                ("quality_tier", source.quality_tier),
            ):
                if name in direct and str(direct[name]) != str(expected):
                    return "historical_source_bundle_metadata_mismatch", set()
        return None, source_version_ids

    @staticmethod
    def _contains_forbidden_feature(features: dict[str, Any]) -> bool:
        return any(token in str(key).lower() for key in features for token in FORBIDDEN_FEATURE_TOKENS)

    @staticmethod
    def _contains_historical_market_feature(features: dict[str, Any]) -> bool:
        return any(
            token in str(key).lower()
            for key in features
            for token in FORBIDDEN_HISTORICAL_MARKET_TOKENS
        )

    @staticmethod
    def _excluded(game: Game, reason: str) -> dict[str, str]:
        return {"game_id": str(game.id), "reason": reason}

    def _manifest(
        self,
        rows: list[TrainingRow],
        exclusions: list[dict[str, str]],
        reasons: Counter[str],
        cutoff: datetime,
        selected_schema: str | None,
        *,
        population: str = "PROSPECTIVE_LIVE",
        temporal_semantics: str | None = None,
        cutoff_policy_version: str | None = None,
        allow_mixed_tiers: bool = False,
    ) -> TrainingDatasetManifestData:
        row_dicts = [asdict(row) for row in rows]
        rows_checksum = hashlib.sha256(b"\n".join(_canonical(row) for row in row_dicts)).hexdigest()
        observed_feature_names = sorted({key for row in rows for key in row.features})
        historical_contract = population == HISTORICAL_POPULATION
        feature_names = (
            [item.name for item in GATE3_FEATURE_SCHEMA.features]
            if historical_contract
            else observed_feature_names
        )
        frozen_output_names = (
            list(GATE3_FEATURE_SCHEMA.output_names) if historical_contract else None
        )
        feature_version_ids = sorted({row.feature_version_id for row in rows if row.feature_version_id})
        feature_versions = sorted({row.feature_version for row in rows})
        dates = [datetime.fromisoformat(row.scheduled_first_pitch.replace("Z", "+00:00")).date() for row in rows]
        source_modes = sorted({row.source_mode for row in rows})
        coverage = _feature_family_coverage(rows, frozen_schema=historical_contract)
        coverage_basis = (
            "FROZEN_SCHEMA_NON_DERIVED_FEATURES"
            if historical_contract
            else "CURATED_PROSPECTIVE_FEATURE_FAMILIES_V1"
        )
        common_checks = {
            "one_row_per_game": len({row.game_id for row in rows}) == len(rows),
            "pregame_cutoff": all(row.data_as_of < row.scheduled_first_pitch for row in rows),
            "live_source_only": all(row.source_mode == "LIVE" for row in rows),
            "no_target_named_features": all(
                not self._contains_forbidden_feature(row.features) for row in rows
            ),
            "final_scores_targets_only": all(
                row.run_differential == row.home_runs - row.away_runs
                and row.total_runs == row.home_runs + row.away_runs
                and row.home_win == int(row.home_runs > row.away_runs)
                for row in rows
            ),
            "feature_schema_consistent": len({row.feature_schema_version for row in rows}) <= 1,
            "snapshot_checksum_present": all(
                _is_sha256(row.feature_snapshot_checksum) for row in rows
            ),
        }
        if population == HISTORICAL_POPULATION:
            leakage_checks = {
                **common_checks,
                "source_evidence_cutoff": all(
                    _historical_compact_lineage_valid(row)
                    for row in rows
                ),
                "historical_lineage_db_revalidated": all(
                    _historical_compact_lineage_valid(row) for row in rows
                ),
                "no_market_or_clv": all(
                    row.market_home_probability is None
                    and row.market_timestamp is None
                    and row.market_books == 0
                    and row.closing_home_probability is None
                    and row.closing_timestamp is None
                    and not self._contains_historical_market_feature(row.features)
                    for row in rows
                ),
                "historical_population_only": all(
                    row.temporal_semantics == HISTORICAL_TEMPORAL_SEMANTICS
                    and row.cutoff_policy_version == cutoff_policy_version
                    for row in rows
                ),
                "historical_identity_present": all(
                    row.historical_materialization_id is not None
                    and _is_sha256(row.historical_materialization_identity_checksum)
                    and row.historical_outcome_id is not None
                    and _is_sha256(row.historical_outcome_identity_checksum)
                    for row in rows
                ),
                "historical_label_cutoff": all(
                    row.label_available_at is not None
                    and _parse_iso(row.label_available_at) <= cutoff
                    for row in rows
                ),
                "historical_tier_consistent": len(
                    {row.historical_quality_tier for row in rows}
                ) <= 1 or allow_mixed_tiers,
            }
        else:
            leakage_checks = {
                **common_checks,
                "source_evidence_cutoff": all(
                    _evidence_within_cutoff(row.source_evidence, _parse_iso(row.data_as_of))
                    for row in rows
                ),
                "market_cutoff": all(
                    row.market_timestamp is None or row.market_timestamp <= row.data_as_of
                    for row in rows
                ),
            }
        quality_scores = [
            float(row.historical_quality_score)
            for row in rows
            if row.historical_quality_score is not None
        ]
        quality_tiers = sorted({
            str(row.historical_quality_tier)
            for row in rows
            if row.historical_quality_tier is not None
        })
        quality_summary = (
            {
                "minimum": min(quality_scores),
                "maximum": max(quality_scores),
                "mean": sum(quality_scores) / len(quality_scores),
            }
            if quality_scores
            else None
        )
        identity = {
            "rows_checksum": rows_checksum,
            "row_count": len(rows),
            "excluded_row_count": len(exclusions),
            "exclusion_reasons": dict(sorted(reasons.items())),
            "feature_schema_version": selected_schema,
            "feature_version_ids": feature_version_ids,
            "feature_names": feature_names,
            "observed_feature_names": (
                observed_feature_names if historical_contract else None
            ),
            "frozen_output_names": frozen_output_names,
            "feature_family_coverage_basis": coverage_basis,
            "target_definitions": TARGET_DEFINITIONS,
            "training_cutoff": _iso(cutoff),
            "leakage_checks": leakage_checks,
            "population": population,
            "temporal_semantics": temporal_semantics,
            "cutoff_policy_version": cutoff_policy_version,
            "quality_tiers": quality_tiers,
            "quality_score_summary": quality_summary,
            "allow_mixed_tiers": allow_mixed_tiers,
        }
        manifest_checksum = _checksum(identity)
        return TrainingDatasetManifestData(
            dataset_id=f"mlb-gate3-{manifest_checksum[:16]}",
            created_at=_iso(datetime.now(UTC)),
            date_start=min(dates).isoformat() if dates else None,
            date_end=max(dates).isoformat() if dates else None,
            seasons=sorted({value.year for value in dates}),
            feature_schema_version=selected_schema,
            feature_version=feature_versions[0] if len(feature_versions) == 1 else None,
            feature_version_id=feature_version_ids[0] if len(feature_version_ids) == 1 else None,
            feature_names=feature_names,
            row_count=len(rows),
            excluded_row_count=len(exclusions),
            exclusion_reasons=dict(sorted(reasons.items())),
            source_modes=source_modes,
            rows_checksum=rows_checksum,
            checksum=manifest_checksum,
            target_definitions=dict(TARGET_DEFINITIONS),
            training_cutoff=_iso(cutoff),
            leakage_checks=leakage_checks,
            feature_family_coverage=coverage,
            population=population,
            temporal_semantics=temporal_semantics,
            cutoff_policy_version=cutoff_policy_version,
            quality_tiers=quality_tiers,
            quality_score_summary=quality_summary,
            observed_feature_names=(
                observed_feature_names if historical_contract else None
            ),
            frozen_output_names=frozen_output_names,
            feature_family_coverage_basis=coverage_basis,
        )


class TrainingDatasetArtifactStore:
    """Atomic, immutable, root-confined storage for governed dataset artifacts."""

    def __init__(
        self,
        root: Path,
        *,
        maximum_artifact_bytes: int = DEFAULT_MAX_DATASET_ARTIFACT_BYTES,
        minimum_free_bytes_after_write: int = DEFAULT_MIN_FREE_BYTES_AFTER_WRITE,
    ) -> None:
        if maximum_artifact_bytes <= 0:
            raise ValueError("maximum_artifact_bytes must be positive")
        if minimum_free_bytes_after_write < 0:
            raise ValueError("minimum_free_bytes_after_write cannot be negative")
        self.root = root.resolve()
        self.maximum_artifact_bytes = int(maximum_artifact_bytes)
        self.minimum_free_bytes_after_write = int(minimum_free_bytes_after_write)

    def save(self, result: DatasetBuildResult) -> TrainingDatasetManifestData:
        final = self._contained(self.root / result.manifest.dataset_id)
        if final.exists():
            existing = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
            if existing.get("checksum") != result.manifest.checksum:
                raise FileExistsError(f"Dataset identity collision at {final}")
            return TrainingDatasetManifestData(**existing)
        rows_bytes = b"\n".join(_canonical(asdict(row)) for row in result.rows)
        serialized_rows = rows_bytes + (b"\n" if rows_bytes else b"")
        if hashlib.sha256(rows_bytes).hexdigest() != result.manifest.rows_checksum:
            raise ValueError("Serialized training rows do not match the governed checksum")
        exclusions_bytes = _canonical(list(result.exclusions))
        relative = final.relative_to(self.root.parent.parent).as_posix()
        projected_bytes = 0
        while True:
            manifest = TrainingDatasetManifestData(**{
                **asdict(result.manifest),
                "artifact_uri": relative,
                "artifact_storage_policy": {
                    "maximum_artifact_bytes": self.maximum_artifact_bytes,
                    "minimum_free_bytes_after_write": self.minimum_free_bytes_after_write,
                    "projected_artifact_bytes": projected_bytes,
                },
            })
            manifest_bytes = json.dumps(
                asdict(manifest),
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            measured = len(serialized_rows) + len(exclusions_bytes) + len(manifest_bytes)
            if measured == projected_bytes:
                break
            projected_bytes = measured
        self._guard_storage(projected_bytes)

        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f".dataset-{uuid4().hex}"
        temporary.mkdir(mode=0o777, exist_ok=False)
        try:
            rows_path = temporary / "rows.jsonl"
            exclusions_path = temporary / "exclusions.json"
            manifest_path = temporary / "manifest.json"
            with rows_path.open("xb") as handle:
                handle.write(serialized_rows)
                handle.flush()
                os.fsync(handle.fileno())
            exclusions_path.write_bytes(exclusions_bytes)
            manifest_path.write_bytes(manifest_bytes)
            os.replace(temporary, final)
            return manifest
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def load(self, dataset_id: str) -> tuple[TrainingDatasetManifestData, tuple[TrainingRow, ...]]:
        if not dataset_id.startswith("mlb-gate3-") or any(value in dataset_id for value in ("/", "\\", "..")):
            raise ValueError("Invalid dataset identifier")
        directory = self._contained(self.root / dataset_id)
        manifest = TrainingDatasetManifestData(**json.loads((directory / "manifest.json").read_text(encoding="utf-8")))
        rows_path = directory / "rows.jsonl"
        actual = hashlib.sha256(rows_path.read_bytes().rstrip(b"\n")).hexdigest()
        if actual != manifest.rows_checksum:
            raise ValueError("Training dataset checksum mismatch")
        rows = tuple(
            TrainingRow(**json.loads(line))
            for line in rows_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        return manifest, rows

    def _contained(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError("Artifact path escapes the governed root")
        return resolved

    def _guard_storage(self, projected_bytes: int) -> None:
        if projected_bytes > self.maximum_artifact_bytes:
            raise DatasetArtifactStorageError(
                "Dataset artifact requires "
                f"{projected_bytes} bytes, exceeding the configured "
                f"{self.maximum_artifact_bytes}-byte maximum"
            )
        probe = self.root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            free_bytes = shutil.disk_usage(probe).free
        except OSError as exc:
            raise DatasetArtifactStorageError(
                "Unable to verify free storage before dataset artifact write"
            ) from exc
        required = projected_bytes + self.minimum_free_bytes_after_write
        if free_bytes < required:
            raise DatasetArtifactStorageError(
                "Dataset artifact requires "
                f"{projected_bytes} bytes plus a "
                f"{self.minimum_free_bytes_after_write}-byte free-space reserve; "
                f"only {free_bytes} bytes are available"
            )


def _feature_family_coverage(
    rows: list[TrainingRow],
    *,
    frozen_schema: bool = False,
) -> dict[str, float]:
    families: dict[str, tuple[str, ...]] = {
        "team_strength": ("home_rating", "away_rating", "home_runs_per_game", "away_runs_per_game"),
        "starting_pitcher": ("home_starter_era", "away_starter_era", "home_starter_xera", "away_starter_xera"),
        "lineup": ("home_lineup_xwoba", "away_lineup_xwoba"),
        "bullpen": ("home_bullpen_availability", "away_bullpen_availability"),
        "statcast": ("home_statcast_xwoba", "away_statcast_xwoba"),
        "recent_form": ("home_recent_runs", "away_recent_runs"),
        "park_weather": ("park_run_factor", "temperature_f", "wind_speed_mph"),
        "rest_travel": ("home_days_since_previous_game", "away_days_since_previous_game"),
        "defense_baserunning": ("defense", "baserunning"),
        "opponent_adjustment": ("opponent_adjustment",),
    }
    if frozen_schema:
        grouped: dict[str, list[str]] = {}
        for spec in GATE3_FEATURE_SCHEMA.features:
            if spec.name in HISTORICAL_DERIVED_FEATURE_NAMES:
                continue
            grouped.setdefault(spec.family, []).append(spec.name)
        families = {name: tuple(values) for name, values in grouped.items()}
    if not rows:
        return {family: 0.0 for family in families}
    result: dict[str, float] = {}
    for family, keys in families.items():
        observed = 0
        possible = len(rows) * len(keys)
        for row in rows:
            missing = set(row.missing_features)
            observed += sum(
                key not in missing
                and row.features.get(key) is not None
                and not (isinstance(row.features.get(key), float) and not math.isfinite(row.features[key]))
                for key in keys
            )
        result[family] = observed / possible if possible else 0.0
    return result


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _aware(parsed)


def _evidence_within_cutoff(raw_evidence: Iterable[dict[str, Any]], cutoff: datetime) -> bool:
    boundary = _aware(cutoff)
    for item in raw_evidence or []:
        available = item.get("available_as_of")
        observed = item.get("observed_at")
        if available is None or observed is None:
            return False
        try:
            if _parse_iso(str(available)) > boundary or _parse_iso(str(observed)) > boundary:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _historical_evidence_within_cutoff(
    raw_evidence: Iterable[dict[str, Any]],
    cutoff: datetime,
    *,
    target_game_id: str,
    source_version_ids: set[str] | None = None,
) -> bool:
    """Validate self-contained reconstructed evidence without backdated fetch times."""

    boundary = _aware(cutoff)
    rows = tuple(raw_evidence or [])
    if not rows:
        return False
    identifiers: set[str] = set()
    allowed_classifications = {
        "RECONSTRUCTABLE_POINT_IN_TIME",
        "PARTIALLY_RECONSTRUCTABLE",
    }
    for item in rows:
        if not isinstance(item, dict):
            return False
        evidence_id = str(item.get("evidence_id") or item.get("id") or "")
        if not evidence_id or evidence_id in identifiers:
            return False
        identifiers.add(evidence_id)
        if item.get("source_mode") != "LIVE":
            return False
        if item.get("temporal_semantics") != HISTORICAL_TEMPORAL_SEMANTICS:
            return False
        classification = str(
            item.get("evidence_classification") or item.get("classification") or ""
        ).upper()
        if classification not in allowed_classifications:
            return False
        role = str(item.get("role") or "").upper()
        if role not in {"FEATURE", "STRUCTURAL", "TARGET_CONTEXT"}:
            return False
        evidence_game_id = item.get("game_id")
        if role == "FEATURE" and str(evidence_game_id) == target_game_id:
            return False
        source_version_id = str(item.get("source_version_id") or "")
        if not source_version_id:
            return False
        if source_version_ids is not None and source_version_id not in source_version_ids:
            return False
        evidence_checksum = item.get("raw_sha256") or item.get("payload_checksum")
        if not _is_sha256(evidence_checksum):
            return False
        event_at = item.get("event_at")
        eligible_at = item.get("eligible_at")
        evidence_end_at = item.get("evidence_end_at")
        if event_at is None or eligible_at is None:
            return False
        try:
            event_time = _parse_iso(str(event_at))
            eligible_time = _parse_iso(str(eligible_at))
            evidence_end_time = (
                _parse_iso(str(evidence_end_at)) if evidence_end_at is not None else event_time
            )
        except (TypeError, ValueError):
            return False
        if role == "TARGET_CONTEXT":
            context = item.get("context")
            if (
                str(evidence_game_id) != target_game_id
                or classification != "PARTIALLY_RECONSTRUCTABLE"
                or event_time < boundary
                or evidence_end_time < boundary
                or eligible_time < boundary
                or eligible_time < evidence_end_time
                or not isinstance(context, dict)
                or context.get("context_type") not in {
                    "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION",
                    "HISTORICAL_ACTUAL_LINEUP",
                }
                or context.get("performance_values_used") is not False
                or context.get("publication_time_established") is not False
            ):
                return False
        elif (
            event_time >= boundary
            or evidence_end_time >= boundary
            or eligible_time > boundary
            or eligible_time < evidence_end_time
        ):
            return False
    return True


def _compact_historical_lineage(
    materialization: HistoricalFeatureMaterialization,
    snapshot: GameFeatureSnapshot,
    outcome: GameFinalOutcome,
) -> list[dict[str, Any]]:
    """Replace repeated canonical references with a DB-revalidated attestation."""

    return [{
        "lineage_version": HISTORICAL_ROW_LINEAGE_VERSION,
        "validation_attestation": "FULL_CANONICAL_DB_REVALIDATED_BEFORE_ROW_BUILD",
        "target_game_id": str(materialization.game_id),
        "cutoff_at": _iso(materialization.cutoff_at),
        "source_mode": materialization.source_mode,
        "temporal_semantics": materialization.temporal_semantics,
        "historical_materialization_id": str(materialization.id),
        "historical_materialization_identity_checksum": materialization.identity_checksum,
        "feature_snapshot_id": str(snapshot.id),
        "feature_snapshot_checksum": snapshot.checksum,
        "historical_outcome_id": str(outcome.id),
        "historical_outcome_identity_checksum": outcome.identity_checksum,
        "evidence_manifest_checksum": materialization.evidence_manifest_checksum,
        "canonical_reference_count": len(materialization.evidence_manifest or []),
        "source_bundle_checksum": materialization.source_bundle_checksum,
        "source_version_count": len(materialization.source_bundle or []),
        "checkpoint_id": str(materialization.checkpoint_id),
    }]


def _historical_compact_lineage_valid(row: TrainingRow) -> bool:
    lineage = tuple(row.source_evidence or [])
    if len(lineage) != 1 or not isinstance(lineage[0], dict):
        return False
    item = lineage[0]
    try:
        canonical_count = int(item["canonical_reference_count"])
        source_count = int(item["source_version_count"])
        UUID(str(item["historical_materialization_id"]))
        UUID(str(item["feature_snapshot_id"]))
        UUID(str(item["historical_outcome_id"]))
        UUID(str(item["checkpoint_id"]))
    except (KeyError, TypeError, ValueError):
        return False
    return (
        item.get("lineage_version")
        in {HISTORICAL_ROW_LINEAGE_VERSION, *LEGACY_HISTORICAL_ROW_LINEAGE_VERSIONS}
        and item.get("validation_attestation")
        == "FULL_CANONICAL_DB_REVALIDATED_BEFORE_ROW_BUILD"
        and str(item.get("target_game_id")) == row.game_id
        and str(item.get("cutoff_at")) == row.data_as_of
        and item.get("source_mode") == "LIVE"
        and item.get("temporal_semantics") == HISTORICAL_TEMPORAL_SEMANTICS
        and str(item.get("historical_materialization_id"))
        == row.historical_materialization_id
        and str(item.get("historical_materialization_identity_checksum"))
        == row.historical_materialization_identity_checksum
        and str(item.get("feature_snapshot_id")) == row.feature_snapshot_id
        and str(item.get("feature_snapshot_checksum")) == row.feature_snapshot_checksum
        and str(item.get("historical_outcome_id")) == row.historical_outcome_id
        and str(item.get("historical_outcome_identity_checksum"))
        == row.historical_outcome_identity_checksum
        and all(
            _is_sha256(item.get(name))
            for name in (
                "historical_materialization_identity_checksum",
                "feature_snapshot_checksum",
                "historical_outcome_identity_checksum",
                "evidence_manifest_checksum",
                "source_bundle_checksum",
            )
        )
        and canonical_count > 0
        and source_count > 0
    )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _model_ready_features(
    snapshot: GameFeatureSnapshot,
    state: GameStateObservation,
) -> dict[str, Any]:
    values = dict(snapshot.feature_values)
    values["feature_completeness"] = float(snapshot.feature_completeness)
    penalties = len(snapshot.missing_features or []) + len(snapshot.stale_features or [])
    values["sample_reliability"] = max(
        0.0,
        min(1.0, float(snapshot.feature_completeness) - penalties * 0.02),
    )
    values["lineups_confirmed"] = float(
        str(values.get("home_lineup_status", "")).upper() == "CONFIRMED"
        and str(values.get("away_lineup_status", "")).upper() == "CONFIRMED"
    )
    values["starters_confirmed"] = float(
        str(state.home_starter_confirmation or "").upper() == "CONFIRMED"
        and str(state.away_starter_confirmation or "").upper() == "CONFIRMED"
    )
    return values


def _historical_model_ready_features(snapshot: GameFeatureSnapshot) -> dict[str, Any]:
    """Expose the frozen historical vector without consulting mutable game state."""

    values = dict(snapshot.feature_values)
    values["feature_completeness"] = float(snapshot.feature_completeness)
    # Historical v2/v3 has no independent prospective freshness signal.  Reusing
    # the prospective missing-field penalty double-counted incompleteness and
    # collapsed every otherwise eligible A1 row to zero reliability.
    values["sample_reliability"] = max(
        0.0,
        min(1.0, float(snapshot.feature_completeness)),
    )
    values.setdefault("lineups_confirmed", 0.0)
    values.setdefault("starters_confirmed", 0.0)
    return values
