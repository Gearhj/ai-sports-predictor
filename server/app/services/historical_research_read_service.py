"""Read-only public summaries of governed historical research evidence.

This service intentionally reads database manifests only.  It never opens a
runtime artifact, invokes a provider, loads training rows, or computes model
metrics.  Historical temporal scope is explicit so these records cannot be
mistaken for today's forecasts or live-forward performance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from math import ceil, isfinite
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import (
    FeatureVersion,
    Game,
    GameFeatureSnapshot,
    GameFinalOutcome,
    GameTeam,
    HistoricalFeatureMaterialization,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    PlayerGameHistoryObservation,
    Team,
    TeamGameHistoryObservation,
    TrainingDatasetManifest,
)
from app.schemas.api import (
    HistoricalCanonicalSummary,
    HistoricalCoverageItem,
    HistoricalDatasetPerformanceSummary,
    HistoricalGameExplorerPage,
    HistoricalGameExplorerRow,
    HistoricalPopulationSummary,
    HistoricalReadinessSummary,
    HistoricalResearchData,
    HistoricalRunStrengthSnapshot,
    HistoricalTeamPrior,
    HistoricalWarehouseHealth,
)


STRICT_NAME = "mlb_historical_a2_tier_a_strict"
ENHANCED_NAME = "mlb_historical_a2_tier_b_enhanced"
STAGE = "GATE_3A_A2"
SEASON = 2025
TEMPORAL_SEMANTICS = "RETROSPECTIVE_RECONSTRUCTION"
DISPLAY_TIMEZONE = ZoneInfo("America/New_York")
SEASON_START = datetime(SEASON, 1, 1, tzinfo=UTC)
SEASON_END = datetime(SEASON + 1, 1, 1, tzinfo=UTC)
READY_STATUS = "READY_FOR_GATE_3B_MODEL_DEVELOPMENT"
NOT_READY_STATUS = "NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT"
UNAVAILABLE_STATUS = "UNAVAILABLE"

_POPULATION_NAMES = {
    "TIER_A_STRICT": STRICT_NAME,
    "TIER_B_ENHANCED": ENHANCED_NAME,
}
_POPULATION_TIERS = {
    "TIER_A_STRICT": ["A"],
    "TIER_B_ENHANCED": ["B"],
}
_FAMILY_LABELS = {
    "team_strength": "Team strength",
    "starting_pitcher": "Starting pitcher",
    "lineup": "Lineup",
    "bullpen": "Bullpen",
    "statcast": "Statcast",
    "recent_form": "Recent form",
    "park_weather": "Park and weather",
    "rest_travel": "Rest and travel",
    "defense_baserunning": "Defense and baserunning",
    "opponent_adjustment": "Opponent adjustment",
    "team": "Team",
    "recent": "Recent",
    "talent": "Talent",
    "rating": "Rating",
    "hitting": "Hitting",
    "pitching": "Pitching",
    "schedule": "Schedule",
    "target_context": "Target context",
    "lifecycle": "Lifecycle",
}

class HistoricalResearchReadService:
    """Project immutable A2 manifest facts into public-safe summaries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def history(self) -> HistoricalResearchData:
        strict_manifest = self._latest_manifest("TIER_A_STRICT")
        enhanced_manifest = self._latest_manifest("TIER_B_ENHANCED")
        strict = self._population(strict_manifest, "TIER_A_STRICT")
        enhanced = self._population(enhanced_manifest, "TIER_B_ENHANCED")
        populations = [item for item in (strict, enhanced) if item is not None]

        if not populations:
            availability = "UNAVAILABLE"
        elif any(item.integrity_status == "FAILED" for item in populations):
            availability = "INTEGRITY_WARNING"
        elif strict is None or enhanced is None:
            availability = "PARTIAL"
        else:
            availability = "AVAILABLE"

        built_at = max((item.built_at for item in populations), default=None)
        readiness = strict.readiness if strict is not None else None
        canonical_summary = self._canonical_summary(
            strict_manifest,
            strict,
            enhanced,
        )
        failed_labels = [
            item.label
            for item in (strict.required_family_coverage if strict is not None else ())
            if not item.passed
        ]
        limitations = []
        if failed_labels:
            limitations.append(
                "Required family coverage remains below the governed threshold: "
                + ", ".join(failed_labels)
                + "."
            )
        limitations.append(
            "Historical market and closing-line evidence is unavailable and was not imputed."
        )
        limitations.append(
            "The immutable A2 materialization/readiness audit itself published no "
            "model fit, backtest metric, promotion decision, or live forecast. "
            "Subsequent governed model evaluation is reported separately on Performance."
        )
        if enhanced is not None:
            limitations.append(
                "The Tier B enhanced population is a research-only shadow and is "
                "not evaluation evidence."
            )

        cutoff_policy_version = self._cutoff_policy_version(strict_manifest)
        return HistoricalResearchData(
            data_mode="live" if populations else "empty",
            availability=availability,
            temporal_scope="HISTORICAL",
            temporal_semantics=TEMPORAL_SEMANTICS,
            stage=STAGE,
            season=SEASON,
            cutoff_policy_version=cutoff_policy_version,
            built_at=built_at,
            verdict=(
                readiness.status
                if readiness is not None
                and strict is not None
                and strict.integrity_status == "PASS"
                else UNAVAILABLE_STATUS
            ),
            historical_market_status="UNAVAILABLE",
            model_training_invoked=(
                readiness.model_training_invoked if readiness is not None else None
            ),
            model_fitting_invoked=(
                readiness.model_fitting_invoked if readiness is not None else None
            ),
            holdout_metrics_inspected=(
                readiness.holdout_metrics_inspected if readiness is not None else None
            ),
            promotion_invoked=None,
            strict_population=strict,
            enhanced_population=enhanced,
            canonical_summary=canonical_summary,
            limitations=limitations,
        )

    def game_explorer(
        self,
        *,
        page: int,
        page_size: int,
        team: str | None,
        month: int | None,
        tier: str,
        source: str | None,
    ) -> HistoricalGameExplorerPage:
        """Return public-safe per-game summaries from canonical SQL snapshots only."""

        normalized_team = self._text(team)
        normalized_source = self._text(source)
        history = self.history()
        canonical = history.canonical_summary
        if canonical.availability != "AVAILABLE":
            return self._empty_explorer(
                page=page,
                page_size=page_size,
                team=normalized_team,
                month=month,
                tier=tier,
                source=normalized_source,
                availability=(
                    "INTEGRITY_WARNING"
                    if canonical.integrity_status == "FAILED"
                    else "UNAVAILABLE"
                ),
                reason=(
                    "Canonical historical integrity could not be proven; game-level "
                    "summaries are withheld."
                ),
            )
        strict_manifest = self._latest_manifest("TIER_A_STRICT")
        enhanced_manifest = self._latest_manifest("TIER_B_ENHANCED")
        if strict_manifest is None or enhanced_manifest is None:
            return self._empty_explorer(
                page=page,
                page_size=page_size,
                team=normalized_team,
                month=month,
                tier=tier,
                source=normalized_source,
                availability="INTEGRITY_WARNING",
                reason=(
                    "The latest strict and enhanced manifests could not be resolved; "
                    "game-level summaries are withheld."
                ),
            )
        current_feature_version_ids = {
            "A": strict_manifest.feature_version_id,
            "B": enhanced_manifest.feature_version_id,
        }
        if len(set(current_feature_version_ids.values())) != 2:
            return self._empty_explorer(
                page=page,
                page_size=page_size,
                team=normalized_team,
                month=month,
                tier=tier,
                source=normalized_source,
                availability="INTEGRITY_WARNING",
                reason=(
                    "The latest strict and enhanced manifests do not resolve to "
                    "distinct feature versions; game-level summaries are withheld."
                ),
            )
        expected_materializations = (
            strict_manifest.game_count + enhanced_manifest.game_count
        )
        explorer_integrity_issues = self._explorer_warehouse_integrity_issues(
            expected_materializations,
            feature_version_ids=current_feature_version_ids,
        )
        if explorer_integrity_issues:
            return self._empty_explorer(
                page=page,
                page_size=page_size,
                team=normalized_team,
                month=month,
                tier=tier,
                source=normalized_source,
                availability="INTEGRITY_WARNING",
                reason=(
                    "Canonical game-explorer lineage failed integrity checks: "
                    + "; ".join(explorer_integrity_issues)
                    + "."
                ),
            )

        home_side = aliased(GameTeam)
        away_side = aliased(GameTeam)
        home_team = aliased(Team)
        away_team = aliased(Team)
        criteria = [
            HistoricalFeatureMaterialization.source_mode == "LIVE",
            HistoricalFeatureMaterialization.temporal_semantics == TEMPORAL_SEMANTICS,
            HistoricalFeatureMaterialization.quality_tier == tier,
            HistoricalFeatureMaterialization.feature_version_id
            == current_feature_version_ids[tier],
            HistoricalFeatureMaterialization.scheduled_at >= SEASON_START,
            HistoricalFeatureMaterialization.scheduled_at < SEASON_END,
            Game.source_mode == "LIVE",
        ]
        if month is not None:
            month_start = datetime(SEASON, month, 1, tzinfo=UTC)
            month_end = (
                datetime(SEASON + 1, 1, 1, tzinfo=UTC)
                if month == 12
                else datetime(SEASON, month + 1, 1, tzinfo=UTC)
            )
            criteria.extend([
                HistoricalFeatureMaterialization.scheduled_at >= month_start,
                HistoricalFeatureMaterialization.scheduled_at < month_end,
            ])
        if normalized_team:
            pattern = f"%{normalized_team.lower()}%"
            criteria.append(or_(
                func.lower(home_team.name).like(pattern),
                func.lower(home_team.abbreviation).like(pattern),
                func.lower(away_team.name).like(pattern),
                func.lower(away_team.abbreviation).like(pattern),
            ))

        candidate_statement = (
            select(
                HistoricalFeatureMaterialization.id,
                HistoricalFeatureMaterialization.scheduled_at,
                HistoricalFeatureMaterialization.source_bundle,
            )
            .join(Game, Game.id == HistoricalFeatureMaterialization.game_id)
            .join(
                home_side,
                and_(
                    home_side.game_id == HistoricalFeatureMaterialization.game_id,
                    home_side.is_home.is_(True),
                ),
            )
            .join(home_team, home_team.id == home_side.team_id)
            .join(
                away_side,
                and_(
                    away_side.game_id == HistoricalFeatureMaterialization.game_id,
                    away_side.is_home.is_(False),
                ),
            )
            .join(away_team, away_team.id == away_side.team_id)
            .where(*criteria)
            .order_by(
                HistoricalFeatureMaterialization.scheduled_at.desc(),
                HistoricalFeatureMaterialization.id.desc(),
            )
        )
        candidates = []
        invalid_source_lineage = False
        for materialization_id, scheduled_at, source_bundle in self.session.execute(
            candidate_statement
        ):
            source_keys = self._source_keys(source_bundle)
            if not source_keys:
                invalid_source_lineage = True
                continue
            if normalized_source and normalized_source.upper() not in {
                item.upper() for item in source_keys
            }:
                continue
            candidates.append((materialization_id, scheduled_at))
        if invalid_source_lineage:
            return self._empty_explorer(
                page=page,
                page_size=page_size,
                team=normalized_team,
                month=month,
                tier=tier,
                source=normalized_source,
                availability="INTEGRITY_WARNING",
                reason=(
                    "At least one selected materialization lacks public-safe source "
                    "lineage; the filtered result is withheld."
                ),
            )

        total_rows = len(candidates)
        total_pages = ceil(total_rows / page_size) if total_rows else 0
        selected = candidates[(page - 1) * page_size:page * page_size]
        if not selected:
            return HistoricalGameExplorerPage(
                data_mode="empty",
                availability="AVAILABLE",
                status_reason="No governed games match the selected filters.",
                season=SEASON,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
                total_rows=total_rows,
                team=normalized_team,
                month=month,
                tier=tier,
                source=normalized_source,
                rows=[],
            )

        selected_ids = [item[0] for item in selected]
        source_version = aliased(HistoricalSourceVersion)
        checkpoint = aliased(HistoryBackfillCheckpoint)
        row_statement = (
            select(
                HistoricalFeatureMaterialization,
                GameFeatureSnapshot,
                home_team,
                away_team,
                source_version,
            )
            .join(
                GameFeatureSnapshot,
                GameFeatureSnapshot.id
                == HistoricalFeatureMaterialization.game_feature_snapshot_id,
            )
            .join(Game, Game.id == HistoricalFeatureMaterialization.game_id)
            .join(
                home_side,
                and_(
                    home_side.game_id == HistoricalFeatureMaterialization.game_id,
                    home_side.is_home.is_(True),
                ),
            )
            .join(home_team, home_team.id == home_side.team_id)
            .join(
                away_side,
                and_(
                    away_side.game_id == HistoricalFeatureMaterialization.game_id,
                    away_side.is_home.is_(False),
                ),
            )
            .join(away_team, away_team.id == away_side.team_id)
            .join(checkpoint, checkpoint.id == HistoricalFeatureMaterialization.checkpoint_id)
            .join(source_version, source_version.id == checkpoint.source_version_id)
            .where(HistoricalFeatureMaterialization.id.in_(selected_ids))
        )
        resolved = {
            materialization.id: (materialization, snapshot, home, away, source_row)
            for materialization, snapshot, home, away, source_row
            in self.session.execute(row_statement)
        }
        if len(resolved) != len(selected_ids):
            return self._empty_explorer(
                page=page,
                page_size=page_size,
                team=normalized_team,
                month=month,
                tier=tier,
                source=normalized_source,
                availability="INTEGRITY_WARNING",
                reason=(
                    "A selected game does not resolve to one snapshot, two team sides, "
                    "and one governed source checkpoint."
                ),
            )

        rows: list[HistoricalGameExplorerRow] = []
        for materialization_id, _ in selected:
            projected, issue = self._explorer_row(*resolved[materialization_id])
            if issue is not None or projected is None:
                return self._empty_explorer(
                    page=page,
                    page_size=page_size,
                    team=normalized_team,
                    month=month,
                    tier=tier,
                    source=normalized_source,
                    availability="INTEGRITY_WARNING",
                    reason=f"A selected game failed point-in-time integrity: {issue}.",
                )
            rows.append(projected)

        return HistoricalGameExplorerPage(
            data_mode="live",
            availability="AVAILABLE",
            status_reason=(
                "Governed pregame feature summaries are shown without target outcomes "
                "or raw/provider payloads."
            ),
            season=SEASON,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            total_rows=total_rows,
            team=normalized_team,
            month=month,
            tier=tier,
            source=normalized_source,
            rows=rows,
        )

    def _canonical_summary(
        self,
        strict_manifest: TrainingDatasetManifest | None,
        strict: HistoricalPopulationSummary | None,
        enhanced: HistoricalPopulationSummary | None,
    ) -> HistoricalCanonicalSummary:
        if strict is None or enhanced is None:
            return self._unavailable_canonical_summary(
                "Both governed A2 populations are required to prove canonical counts."
            )
        if strict.integrity_status != "PASS" or enhanced.integrity_status != "PASS":
            return self._unavailable_canonical_summary(
                "Manifest integrity did not pass for both governed A2 populations.",
                failed=True,
            )

        official_games = self._group_count(
            select(GameFinalOutcome.game_id).where(
                GameFinalOutcome.source_mode == "LIVE",
                GameFinalOutcome.temporal_semantics == TEMPORAL_SEMANTICS,
                GameFinalOutcome.official.is_(True),
                GameFinalOutcome.official_date.is_not(None),
                GameFinalOutcome.official_date >= datetime(SEASON, 1, 1).date(),
                GameFinalOutcome.official_date < datetime(SEASON + 1, 1, 1).date(),
            ).group_by(GameFinalOutcome.game_id)
        )
        strict_outcomes = self._group_count(
            select(GameFinalOutcome.game_id).where(
                GameFinalOutcome.source_mode == "LIVE",
                GameFinalOutcome.temporal_semantics == TEMPORAL_SEMANTICS,
                GameFinalOutcome.official.is_(True),
                GameFinalOutcome.quality_tier == "A",
                GameFinalOutcome.official_date >= datetime(SEASON, 1, 1).date(),
                GameFinalOutcome.official_date < datetime(SEASON + 1, 1, 1).date(),
            ).group_by(GameFinalOutcome.game_id)
        )
        team_game_rows = self._group_count(
            select(
                TeamGameHistoryObservation.game_id,
                TeamGameHistoryObservation.team_id,
            ).where(
                TeamGameHistoryObservation.source_mode == "LIVE",
                TeamGameHistoryObservation.temporal_semantics == TEMPORAL_SEMANTICS,
                TeamGameHistoryObservation.event_at >= SEASON_START,
                TeamGameHistoryObservation.event_at < SEASON_END,
            ).group_by(
                TeamGameHistoryObservation.game_id,
                TeamGameHistoryObservation.team_id,
            )
        )
        player_game_rows = self._group_count(
            select(
                PlayerGameHistoryObservation.game_id,
                PlayerGameHistoryObservation.player_id,
                PlayerGameHistoryObservation.team_id,
                PlayerGameHistoryObservation.role,
            ).where(
                PlayerGameHistoryObservation.source_mode == "LIVE",
                PlayerGameHistoryObservation.temporal_semantics == TEMPORAL_SEMANTICS,
                PlayerGameHistoryObservation.event_at >= SEASON_START,
                PlayerGameHistoryObservation.event_at < SEASON_END,
            ).group_by(
                PlayerGameHistoryObservation.game_id,
                PlayerGameHistoryObservation.player_id,
                PlayerGameHistoryObservation.team_id,
                PlayerGameHistoryObservation.role,
            )
        )
        player_games = self._group_count(
            select(PlayerGameHistoryObservation.game_id).where(
                PlayerGameHistoryObservation.source_mode == "LIVE",
                PlayerGameHistoryObservation.temporal_semantics == TEMPORAL_SEMANTICS,
                PlayerGameHistoryObservation.event_at >= SEASON_START,
                PlayerGameHistoryObservation.event_at < SEASON_END,
            ).group_by(PlayerGameHistoryObservation.game_id)
        )
        strict_materializations = self._materialization_game_count("A")
        enhanced_materializations = self._materialization_game_count("B")
        primary_source_keys = set(self.session.scalars(
            select(HistoricalSourceVersion.source_key)
            .join(
                HistoryBackfillCheckpoint,
                HistoryBackfillCheckpoint.source_version_id
                == HistoricalSourceVersion.id,
            )
            .join(
                HistoricalFeatureMaterialization,
                HistoricalFeatureMaterialization.checkpoint_id
                == HistoryBackfillCheckpoint.id,
            )
            .where(
                HistoricalFeatureMaterialization.source_mode == "LIVE",
                HistoricalFeatureMaterialization.temporal_semantics
                == TEMPORAL_SEMANTICS,
                HistoricalFeatureMaterialization.scheduled_at >= SEASON_START,
                HistoricalFeatureMaterialization.scheduled_at < SEASON_END,
            )
        ))
        source_keys: set[str] = set()
        missing_source_bundles = 0
        for source_bundle in self.session.scalars(
            select(HistoricalFeatureMaterialization.source_bundle).where(
                HistoricalFeatureMaterialization.source_mode == "LIVE",
                HistoricalFeatureMaterialization.temporal_semantics
                == TEMPORAL_SEMANTICS,
                HistoricalFeatureMaterialization.quality_tier.in_(["A", "B"]),
                HistoricalFeatureMaterialization.scheduled_at >= SEASON_START,
                HistoricalFeatureMaterialization.scheduled_at < SEASON_END,
            )
        ):
            bundle_keys = self._source_keys(source_bundle)
            if not bundle_keys:
                missing_source_bundles += 1
            source_keys.update(bundle_keys)

        issues: list[str] = []
        if official_games != enhanced.game_count:
            issues.append("official-game count disagrees with the enhanced A2 manifest")
        if strict_outcomes != strict.game_count:
            issues.append("Tier A official-game count disagrees with the strict A2 manifest")
        if strict_materializations != strict.game_count:
            issues.append("strict materialization count disagrees with the strict A2 manifest")
        if enhanced_materializations != enhanced.game_count:
            issues.append("enhanced materialization count disagrees with the enhanced A2 manifest")
        if team_game_rows != official_games * 2:
            issues.append("canonical team-game rows do not resolve to two sides per game")
        if player_game_rows == 0 or player_games != official_games:
            issues.append("canonical player-game rows do not cover every official game")
        if not source_keys or missing_source_bundles:
            issues.append("canonical source identity is unavailable")
        if not primary_source_keys.issubset(source_keys):
            issues.append("checkpoint source identity is absent from source bundles")

        strict_quality = (
            strict_manifest.quality_summary
            if strict_manifest is not None
            and isinstance(strict_manifest.quality_summary, dict)
            else {}
        )
        leakage = strict_quality.get("leakage_checks")
        leakage_pass = (
            isinstance(leakage, dict)
            and bool(leakage)
            and all(value is True for value in leakage.values())
        )
        if not leakage_pass:
            issues.append("strict manifest leakage checks are missing or did not all pass")
        mean_coverage = (
            strict.mean_quality_score / 100.0
            if strict.mean_quality_score is not None
            else None
        )
        if mean_coverage is None:
            issues.append("mean strict feature coverage is unavailable")

        statcast_item = next(
            (item for item in strict.required_family_coverage if item.key == "statcast"),
            None,
        )
        statcast_status = (
            "UNAVAILABLE"
            if statcast_item is None or not statcast_item.coverage
            else "AVAILABLE" if statcast_item.coverage >= 1 else "PARTIAL"
        )
        if issues:
            return HistoricalCanonicalSummary(
                availability="INTEGRITY_WARNING",
                integrity_status="FAILED",
                leakage_status="FAILED" if leakage is not None else "UNAVAILABLE",
                statcast_status="UNAVAILABLE",
                historical_market_status="UNAVAILABLE",
                integrity_issues=sorted(set(issues)),
            )
        return HistoricalCanonicalSummary(
            availability="AVAILABLE",
            integrity_status="PASS",
            official_games=official_games,
            team_game_rows=team_game_rows,
            player_game_rows=player_game_rows,
            strict_feature_coverage=mean_coverage,
            leakage_status="PASS",
            statcast_status=statcast_status,
            historical_market_status="UNAVAILABLE",
            source_keys=sorted(source_keys),
            integrity_issues=[],
        )

    def _explorer_warehouse_integrity_issues(
        self,
        expected_materializations: int,
        *,
        feature_version_ids: Mapping[str, Any],
    ) -> list[str]:
        """Prove the latest governed A/B populations before paginating a subset."""

        home_side = aliased(GameTeam)
        away_side = aliased(GameTeam)
        rows = list(self.session.execute(
            select(
                HistoricalFeatureMaterialization.id.label("materialization_id"),
                HistoricalFeatureMaterialization.game_id.label("materialization_game_id"),
                HistoricalFeatureMaterialization.game_feature_snapshot_id.label("snapshot_id"),
                HistoricalFeatureMaterialization.feature_version_id.label("materialization_feature_id"),
                HistoricalFeatureMaterialization.game_final_outcome_id.label("outcome_id"),
                HistoricalFeatureMaterialization.cutoff_at.label("cutoff_at"),
                HistoricalFeatureMaterialization.scheduled_at.label("scheduled_at"),
                HistoricalFeatureMaterialization.source_bundle.label("source_bundle"),
                GameFeatureSnapshot.id.label("resolved_snapshot_id"),
                GameFeatureSnapshot.game_id.label("snapshot_game_id"),
                GameFeatureSnapshot.feature_version_id.label("snapshot_feature_id"),
                GameFeatureSnapshot.data_as_of.label("data_as_of"),
                GameFeatureSnapshot.source_mode.label("snapshot_source_mode"),
                GameFeatureSnapshot.source_evidence.label("source_evidence"),
                GameFeatureSnapshot.market_snapshot_ids.label("market_snapshot_ids"),
                HistoricalSourceVersion.source_key.label("source_key"),
                HistoricalSourceVersion.source_mode.label("source_mode"),
                HistoricalSourceVersion.temporal_semantics.label("source_temporal"),
            )
            .join(
                GameFeatureSnapshot,
                GameFeatureSnapshot.id
                == HistoricalFeatureMaterialization.game_feature_snapshot_id,
            )
            .join(Game, Game.id == HistoricalFeatureMaterialization.game_id)
            .join(
                home_side,
                and_(
                    home_side.game_id == HistoricalFeatureMaterialization.game_id,
                    home_side.is_home.is_(True),
                ),
            )
            .join(
                away_side,
                and_(
                    away_side.game_id == HistoricalFeatureMaterialization.game_id,
                    away_side.is_home.is_(False),
                ),
            )
            .join(
                HistoryBackfillCheckpoint,
                HistoryBackfillCheckpoint.id
                == HistoricalFeatureMaterialization.checkpoint_id,
            )
            .join(
                HistoricalSourceVersion,
                HistoricalSourceVersion.id
                == HistoryBackfillCheckpoint.source_version_id,
            )
            .where(
                HistoricalFeatureMaterialization.source_mode == "LIVE",
                HistoricalFeatureMaterialization.temporal_semantics
                == TEMPORAL_SEMANTICS,
                or_(
                    and_(
                        HistoricalFeatureMaterialization.quality_tier == "A",
                        HistoricalFeatureMaterialization.feature_version_id
                        == feature_version_ids["A"],
                    ),
                    and_(
                        HistoricalFeatureMaterialization.quality_tier == "B",
                        HistoricalFeatureMaterialization.feature_version_id
                        == feature_version_ids["B"],
                    ),
                ),
                HistoricalFeatureMaterialization.scheduled_at >= SEASON_START,
                HistoricalFeatureMaterialization.scheduled_at < SEASON_END,
                Game.source_mode == "LIVE",
            )
        ).mappings())
        issues: list[str] = []
        if len(rows) != expected_materializations:
            issues.append(
                "not every materialization resolves to a snapshot, game, two sides, "
                "and checkpoint source"
            )
        for row in rows:
            source_keys = self._source_keys(row["source_bundle"])
            evidence = row["source_evidence"]
            if (
                row["snapshot_id"] != row["resolved_snapshot_id"]
                or row["materialization_game_id"] != row["snapshot_game_id"]
                or row["materialization_feature_id"] != row["snapshot_feature_id"]
                or row["outcome_id"] is None
            ):
                issues.append("materialization snapshot/feature/outcome lineage is inconsistent")
                break
            if (
                row["snapshot_source_mode"] != "LIVE"
                or row["source_mode"] != "LIVE"
                or row["source_temporal"] != TEMPORAL_SEMANTICS
            ):
                issues.append("materialization source semantics are inconsistent")
                break
            if row["source_key"] not in source_keys:
                issues.append("checkpoint source is absent from a materialization bundle")
                break
            if self._as_utc(row["data_as_of"]) != self._as_utc(row["cutoff_at"]):
                issues.append("snapshot data-as-of disagrees with a governed cutoff")
                break
            if self._as_utc(row["cutoff_at"]) >= self._as_utc(row["scheduled_at"]):
                issues.append("a governed cutoff is not strictly before first pitch")
                break
            if row["market_snapshot_ids"]:
                issues.append("market evidence unexpectedly appears in an A2 snapshot")
                break
            if not (
                isinstance(evidence, list)
                and bool(evidence)
                and all(
                    isinstance(item, dict)
                    and item.get("target_excluded") is True
                    and item.get("temporal_valid") is True
                    for item in evidence
                )
            ):
                issues.append("target-exclusion or temporal attestation is missing")
                break
        return sorted(set(issues))

    def _explorer_row(
        self,
        materialization: HistoricalFeatureMaterialization,
        snapshot: GameFeatureSnapshot,
        home: Team,
        away: Team,
        source: HistoricalSourceVersion,
    ) -> tuple[HistoricalGameExplorerRow | None, str | None]:
        source_keys = self._source_keys(materialization.source_bundle)
        evidence = snapshot.source_evidence
        evidence_is_safe = (
            isinstance(evidence, list)
            and bool(evidence)
            and all(
                isinstance(item, dict)
                and item.get("target_excluded") is True
                and item.get("temporal_valid") is True
                for item in evidence
            )
        )
        if materialization.game_id != snapshot.game_id:
            return None, "materialization and snapshot game identities disagree"
        if materialization.feature_version_id != snapshot.feature_version_id:
            return None, "materialization and snapshot feature identities disagree"
        if materialization.game_final_outcome_id is None:
            return None, "governed final-outcome lineage is missing"
        if (
            materialization.source_mode != "LIVE"
            or snapshot.source_mode != "LIVE"
            or source.source_mode != "LIVE"
            or materialization.temporal_semantics != TEMPORAL_SEMANTICS
            or source.temporal_semantics != TEMPORAL_SEMANTICS
        ):
            return None, "LIVE retrospective source semantics are inconsistent"
        if source.source_key not in source_keys:
            return None, "checkpoint source is absent from the materialization source bundle"
        if not evidence_is_safe:
            return None, "target-exclusion or temporal attestation is missing"
        if self._as_utc(snapshot.data_as_of) != self._as_utc(materialization.cutoff_at):
            return None, "snapshot data-as-of does not equal the governed cutoff"
        if self._as_utc(materialization.cutoff_at) >= self._as_utc(
            materialization.scheduled_at
        ):
            return None, "cutoff is not strictly before first pitch"
        if snapshot.market_snapshot_ids:
            return None, "market evidence unexpectedly appears in the A2 snapshot"

        values = snapshot.feature_values if isinstance(snapshot.feature_values, dict) else {}
        missing = {
            str(item) for item in snapshot.missing_features
        } if isinstance(snapshot.missing_features, list) else set()

        def value(name: str) -> float | None:
            return self._feature_number(values, missing, name)

        home_prior = HistoricalTeamPrior(
            team_id=str(home.id),
            abbreviation=home.abbreviation,
            name=home.name,
            elo_rating=value("home_elo_rating"),
            runs_per_game=value("home_runs_per_game_season"),
            runs_allowed_per_game=value("home_runs_allowed_per_game_season"),
            offense_true_talent=value("home_offense_true_talent"),
            run_prevention_true_talent=value("home_run_prevention_true_talent"),
            reliability=value("home_team_reliability"),
        )
        away_prior = HistoricalTeamPrior(
            team_id=str(away.id),
            abbreviation=away.abbreviation,
            name=away.name,
            elo_rating=value("away_elo_rating"),
            runs_per_game=value("away_runs_per_game_season"),
            runs_allowed_per_game=value("away_runs_allowed_per_game_season"),
            offense_true_talent=value("away_offense_true_talent"),
            run_prevention_true_talent=value("away_run_prevention_true_talent"),
            reliability=value("away_team_reliability"),
        )
        starter_state = self._evidence_state(
            value("starters_confirmed"),
            value("home_starter_prior_era"),
            value("away_starter_prior_era"),
        )
        lineup_state = self._evidence_state(
            value("lineups_confirmed"),
            value("home_lineup_member_count"),
            value("away_lineup_member_count"),
            completeness_threshold=9,
        )
        total_count = len(values)
        present_count = sum(
            key not in missing and item is not None
            for key, item in values.items()
        )
        scheduled_at = self._as_utc(materialization.scheduled_at)
        return HistoricalGameExplorerRow(
            game_id=str(materialization.game_id),
            event_date=scheduled_at.astimezone(DISPLAY_TIMEZONE).date(),
            scheduled_at=scheduled_at,
            matchup=f"{away.abbreviation} at {home.abbreviation}",
            cutoff_at=self._as_utc(materialization.cutoff_at),
            cutoff_policy_version=materialization.cutoff_policy_version,
            quality_tier=materialization.quality_tier,
            source_mode="LIVE",
            source_keys=source_keys,
            feature_completeness=snapshot.feature_completeness,
            admission_quality_score=materialization.quality_score,
            present_feature_count=present_count,
            total_feature_count=total_count,
            starter_state=starter_state,
            lineup_state=lineup_state,
            home_team_prior=home_prior,
            away_team_prior=away_prior,
            run_strength=HistoricalRunStrengthSnapshot(
                home_offense=home_prior.offense_true_talent,
                home_run_prevention=home_prior.run_prevention_true_talent,
                away_offense=away_prior.offense_true_talent,
                away_run_prevention=away_prior.run_prevention_true_talent,
                expected_home_win_probability=value(
                    "elo_expected_home_probability"
                ),
            ),
            market_available=None,
            market_status="UNAVAILABLE",
            target_excluded=True,
        ), None

    @staticmethod
    def _empty_explorer(
        *,
        page: int,
        page_size: int,
        team: str | None,
        month: int | None,
        tier: str,
        source: str | None,
        availability: str,
        reason: str,
    ) -> HistoricalGameExplorerPage:
        return HistoricalGameExplorerPage(
            data_mode="empty",
            availability=availability,
            status_reason=reason,
            season=SEASON,
            page=page,
            page_size=page_size,
            total_pages=0,
            total_rows=0,
            team=team,
            month=month,
            tier=tier,
            source=source,
            rows=[],
        )

    @staticmethod
    def _unavailable_canonical_summary(
        issue: str,
        *,
        failed: bool = False,
    ) -> HistoricalCanonicalSummary:
        return HistoricalCanonicalSummary(
            availability="INTEGRITY_WARNING" if failed else "UNAVAILABLE",
            integrity_status="FAILED" if failed else "UNAVAILABLE",
            leakage_status="FAILED" if failed else "UNAVAILABLE",
            statcast_status="UNAVAILABLE",
            historical_market_status="UNAVAILABLE",
            integrity_issues=[issue],
        )

    def _materialization_game_count(self, tier: str) -> int:
        return self._group_count(
            select(HistoricalFeatureMaterialization.game_id).where(
                HistoricalFeatureMaterialization.source_mode == "LIVE",
                HistoricalFeatureMaterialization.temporal_semantics
                == TEMPORAL_SEMANTICS,
                HistoricalFeatureMaterialization.quality_tier == tier,
                HistoricalFeatureMaterialization.scheduled_at >= SEASON_START,
                HistoricalFeatureMaterialization.scheduled_at < SEASON_END,
            ).group_by(HistoricalFeatureMaterialization.game_id)
        )

    def _group_count(self, statement: Any) -> int:
        return int(
            self.session.scalar(
                select(func.count()).select_from(statement.subquery())
            )
            or 0
        )

    @staticmethod
    def _source_keys(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        keys = {
            item.get("source_key").strip()
            for item in value
            if isinstance(item, dict)
            and isinstance(item.get("source_key"), str)
            and item.get("source_key").strip()
        }
        return sorted(keys)

    @staticmethod
    def _feature_number(
        values: Mapping[str, Any],
        missing: set[str],
        name: str,
    ) -> float | None:
        if name in missing or values.get(f"{name}__missing") == 1:
            return None
        raw = values.get(name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        result = float(raw)
        return result if isfinite(result) else None

    @staticmethod
    def _evidence_state(
        confirmed: float | None,
        home_value: float | None,
        away_value: float | None,
        *,
        completeness_threshold: float | None = None,
    ) -> str:
        if confirmed is not None and confirmed >= 1:
            return "CONFIRMED"
        available = [home_value, away_value]
        if completeness_threshold is not None:
            available = [
                item
                for item in available
                if item is not None and item >= completeness_threshold
            ]
            count = len(available)
        else:
            count = sum(item is not None for item in available)
        return "CONFIRMED" if count == 2 else "PARTIAL" if count == 1 else "MISSING"

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def performance_summary(self) -> HistoricalDatasetPerformanceSummary:
        history = self.history()
        strict = history.strict_population
        if strict is None:
            return HistoricalDatasetPerformanceSummary(
                availability="UNAVAILABLE",
                stage=STAGE,
                season=SEASON,
                population="TIER_A_STRICT",
                readiness_status=UNAVAILABLE_STATUS,
                status_reason="No governed strict historical dataset manifest is available.",
            )
        if strict.integrity_status == "FAILED":
            reason = (
                "The latest strict historical manifest failed public integrity checks; "
                "dataset-readiness and model-performance claims are blocked."
            )
            availability = "INTEGRITY_WARNING"
        elif strict.readiness.status == READY_STATUS:
            reason = (
                "The audit-only dataset readiness checks passed. This is not a model "
                "evaluation and publishes no performance metric."
            )
            availability = "AVAILABLE"
        else:
            failed = ", ".join(strict.readiness.failed_checks) or "unpublished checks"
            reason = (
                f"The strict historical dataset is not ready; failed checks: {failed}. "
                "No model evaluation was run as part of the immutable A2 "
                "materialization/readiness audit."
            )
            availability = "AVAILABLE"
        return HistoricalDatasetPerformanceSummary(
            availability=availability,
            stage=STAGE,
            season=SEASON,
            population="TIER_A_STRICT",
            row_count=strict.row_count,
            distinct_event_dates=strict.readiness.distinct_event_dates,
            holdout_rows=strict.readiness.holdout_rows,
            rolling_origin_folds=strict.readiness.rolling_origin_folds,
            readiness_status=strict.readiness.status,
            failed_checks=strict.readiness.failed_checks,
            status_reason=reason,
            model_training_invoked=strict.readiness.model_training_invoked,
            model_fitting_invoked=strict.readiness.model_fitting_invoked,
            holdout_metrics_inspected=strict.readiness.holdout_metrics_inspected,
        )

    def health_summary(self) -> HistoricalWarehouseHealth:
        history = self.history()
        strict = history.strict_population
        enhanced = history.enhanced_population
        canonical = history.canonical_summary
        if strict is None:
            detail = "No governed strict A2 historical manifest is available."
            readiness_status = UNAVAILABLE_STATUS
            failed_checks: list[str] = []
        elif strict.integrity_status == "FAILED":
            detail = "Historical evidence exists but its public manifest integrity check failed."
            readiness_status = UNAVAILABLE_STATUS
            failed_checks = strict.integrity_issues
        else:
            detail = (
                "Persisted retrospective evidence is reported separately from current "
                "provider freshness and does not affect the live health score."
            )
            readiness_status = strict.readiness.status
            failed_checks = strict.readiness.failed_checks
        return HistoricalWarehouseHealth(
            availability=history.availability,
            season=SEASON,
            temporal_semantics=TEMPORAL_SEMANTICS,
            strict_rows=strict.row_count if strict is not None else 0,
            enhanced_rows=enhanced.row_count if enhanced is not None else 0,
            readiness_status=readiness_status,
            mean_strict_feature_coverage=canonical.strict_feature_coverage,
            integrity_status=canonical.integrity_status,
            leakage_status=canonical.leakage_status,
            statcast_status=canonical.statcast_status,
            historical_market_status=canonical.historical_market_status,
            failed_checks=failed_checks,
            built_at=history.built_at,
            detail=detail,
        )

    def _latest_manifest(self, population: str) -> TrainingDatasetManifest | None:
        return self.session.scalar(
            select(TrainingDatasetManifest)
            .where(
                TrainingDatasetManifest.name == _POPULATION_NAMES[population],
                TrainingDatasetManifest.source_mode == "LIVE",
            )
            .order_by(
                TrainingDatasetManifest.built_at.desc(),
                TrainingDatasetManifest.created_at.desc(),
                TrainingDatasetManifest.id.desc(),
            )
            .limit(1)
        )

    def _population(
        self,
        manifest: TrainingDatasetManifest | None,
        population: str,
    ) -> HistoricalPopulationSummary | None:
        if manifest is None:
            return None
        quality = manifest.quality_summary if isinstance(manifest.quality_summary, dict) else {}
        readiness_payload = quality.get("readiness")
        readiness, readiness_issues = self._readiness(readiness_payload, manifest)
        issues = self._integrity_issues(
            manifest,
            quality,
            readiness_payload,
            population,
        ) + readiness_issues
        feature = self.session.get(FeatureVersion, manifest.feature_version_id)
        schema_hash = quality.get("schema_hash")
        if not isinstance(schema_hash, str):
            schema_hash = feature.schema_hash if feature is not None else None
        required_threshold = self._required_threshold(readiness_payload)
        required_coverage = self._coverage_items(
            quality.get("feature_family_coverage"),
            required=required_threshold,
        )
        raw_coverage = self._coverage_items(
            quality.get("raw_a2_feature_family_coverage"),
            required=required_threshold,
        )
        quality_score = quality.get("quality_score_summary")
        mean_quality = (
            self._number(quality_score.get("mean"), minimum=0, maximum=100)
            if isinstance(quality_score, dict)
            else None
        )
        raw_value_count = self._integer(quality.get("raw_value_count"))
        ordered_output_count = self._integer(quality.get("ordered_output_count"))
        return HistoricalPopulationSummary(
            population=population,
            role=(
                "PRIMARY_STRICT"
                if population == "TIER_A_STRICT"
                else "RESEARCH_ONLY_SHADOW"
            ),
            source_mode="LIVE",
            quality_tiers=[item for item in _POPULATION_TIERS[population]],
            row_count=manifest.row_count,
            game_count=manifest.game_count,
            excluded_row_count=manifest.excluded_row_count,
            event_start_date=manifest.event_start_at.date(),
            event_end_date=manifest.event_end_at.date(),
            schema_version=manifest.schema_version,
            schema_hash=schema_hash,
            feature_version=(
                f"{feature.name}/{feature.version}" if feature is not None else None
            ),
            raw_value_count=raw_value_count,
            ordered_output_count=(
                ordered_output_count
                if ordered_output_count is not None
                else len(manifest.feature_names)
            ),
            mean_quality_score=mean_quality,
            manifest_version=manifest.version,
            manifest_identity_checksum=manifest.identity_checksum,
            rows_checksum=manifest.artifact_checksum,
            built_at=manifest.built_at,
            integrity_status="FAILED" if issues else "PASS",
            integrity_issues=sorted(set(issues)),
            required_family_coverage=required_coverage,
            raw_family_coverage=raw_coverage,
            readiness=readiness,
        )

    def _integrity_issues(
        self,
        manifest: TrainingDatasetManifest,
        quality: Mapping[str, Any],
        readiness: object,
        population: str,
    ) -> list[str]:
        issues: list[str] = []
        if quality.get("stage") != STAGE:
            issues.append("stage identity does not match GATE_3A_A2")
        if quality.get("population") != population:
            issues.append("population identity does not match the manifest name")
        if quality.get("quality_tiers") != _POPULATION_TIERS[population]:
            issues.append("quality tiers do not match the governed population")
        leakage_checks = quality.get("leakage_checks")
        if (
            not isinstance(leakage_checks, dict)
            or not leakage_checks
            or any(result is not True for result in leakage_checks.values())
        ):
            issues.append("leakage checks are missing or did not all pass")
        if manifest.row_count != manifest.game_count:
            issues.append("row and distinct-game counts do not match")
        if len(manifest.feature_names) == 0:
            issues.append("frozen feature output list is empty")
        ordered_output_count = self._integer(quality.get("ordered_output_count"))
        if ordered_output_count != len(manifest.feature_names):
            issues.append("ordered output count does not match the frozen feature list")
        if not self._sha256(manifest.identity_checksum):
            issues.append("manifest identity checksum is invalid")
        if not self._sha256(manifest.artifact_checksum):
            issues.append("rows checksum is invalid")
        schema_hash = quality.get("schema_hash")
        if not self._sha256(schema_hash):
            issues.append("feature schema hash is invalid")
        feature = self.session.get(FeatureVersion, manifest.feature_version_id)
        if feature is None:
            issues.append("feature version does not resolve")
        elif schema_hash != feature.schema_hash:
            issues.append("feature-version and manifest schema hashes disagree")
        if not isinstance(readiness, dict):
            issues.append("audit-only readiness result is missing")
        return issues

    def _readiness(
        self,
        value: object,
        manifest: TrainingDatasetManifest,
    ) -> tuple[HistoricalReadinessSummary, list[str]]:
        if not isinstance(value, dict):
            return HistoricalReadinessSummary(status=UNAVAILABLE_STATUS), []
        issues: list[str] = []
        status = value.get("status")
        if status not in {READY_STATUS, NOT_READY_STATUS}:
            status = UNAVAILABLE_STATUS
            issues.append("readiness status is invalid or unavailable")
        checks = value.get("checks")
        if isinstance(checks, dict):
            failed_checks = sorted(
                str(name)
                for name, result in checks.items()
                if not isinstance(result, dict) or result.get("passed") is not True
            )
        else:
            failed_checks = []
            issues.append("readiness checks are missing")
        if status == READY_STATUS and failed_checks:
            issues.append("readiness claims READY while one or more checks failed")
        row_count = self._integer(value.get("row_count")) or 0
        game_count = self._integer(value.get("distinct_game_count")) or 0
        if row_count != manifest.row_count or game_count != manifest.game_count:
            issues.append("readiness row/game counts disagree with the manifest")
        return HistoricalReadinessSummary(
            status=status,
            policy_version=self._text(value.get("policy_version")),
            row_count=row_count,
            distinct_game_count=game_count,
            distinct_event_dates=self._integer(value.get("distinct_event_dates")) or 0,
            development_date_groups=(
                self._integer(value.get("development_date_groups")) or 0
            ),
            holdout_date_groups=self._integer(value.get("holdout_date_groups")) or 0,
            holdout_rows=self._integer(value.get("holdout_rows")) or 0,
            rolling_origin_folds=self._integer(value.get("rolling_origin_folds")) or 0,
            failed_checks=failed_checks,
            model_training_invoked=self._boolean(value.get("model_training_invoked")),
            model_fitting_invoked=self._boolean(value.get("model_fitting_invoked")),
            holdout_metrics_inspected=self._boolean(value.get("holdout_metrics_inspected")),
            holdout_outcomes_inspected=self._boolean(
                value.get("holdout_outcomes_inspected")
            ),
        ), issues

    def _coverage_items(
        self,
        value: object,
        *,
        required: float,
    ) -> list[HistoricalCoverageItem]:
        if not isinstance(value, dict):
            return []
        result = []
        for key in sorted(value):
            coverage = self._number(value[key], minimum=0, maximum=1)
            result.append(HistoricalCoverageItem(
                key=str(key),
                label=_FAMILY_LABELS.get(str(key), str(key).replace("_", " ").title()),
                coverage=coverage,
                required=required,
                passed=coverage is not None and coverage >= required,
            ))
        return result

    @staticmethod
    def _required_threshold(readiness: object) -> float:
        if not isinstance(readiness, dict):
            return 0.70
        checks = readiness.get("checks")
        coverage = (
            checks.get("required_feature_family_coverage")
            if isinstance(checks, dict)
            else None
        )
        required = coverage.get("required") if isinstance(coverage, dict) else None
        value = required.get("minimum_each") if isinstance(required, dict) else None
        result = HistoricalResearchReadService._number(value, minimum=0, maximum=1)
        return result if result is not None else 0.70

    @staticmethod
    def _cutoff_policy_version(manifest: TrainingDatasetManifest | None) -> str | None:
        if manifest is None or not isinstance(manifest.cutoff_policy, dict):
            return None
        return HistoricalResearchReadService._text(
            manifest.cutoff_policy.get("policy_version")
        )

    @staticmethod
    def _sha256(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdefABCDEF" for character in value)
        )

    @staticmethod
    def _text(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _boolean(value: object) -> bool | None:
        return value if isinstance(value, bool) else None

    @staticmethod
    def _integer(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not isfinite(float(value)) or value < 0 or int(value) != value:
            return None
        return int(value)

    @staticmethod
    def _number(
        value: object,
        *,
        minimum: float,
        maximum: float,
    ) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        result = float(value)
        if not isfinite(result) or not minimum <= result <= maximum:
            return None
        return result


__all__ = ["HistoricalResearchReadService"]
