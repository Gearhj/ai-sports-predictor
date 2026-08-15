from __future__ import annotations

from calendar import monthrange
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from types import MappingProxyType

import pytest

from app.gen2.fd1_identity import (
    GEN2_PRIMARY_POPULATION,
    GEN2_PRIOR_ONLY_POPULATION,
    ResolutionStatus,
)
from app.gen2.fd1_identity_coverage import (
    FD1_IDENTITY_COVERAGE_SCHEMA_VERSION,
    ActiveIdentityCoverageReport,
    ExactLinkRate,
    IdentityCoverageSlice,
    IdentityNamespace,
    IdentityRelation,
    NamespaceCoverage,
    RetrosheetIdentitySourceEvidence,
)
from app.gen2.fd1_readiness import (
    OutcomeCount,
    ReadinessDecision,
    TemporalAuditSummary,
    TemporalFoldEvidence,
)
from app.gen2.fd1_readiness_closeout import (
    EXPECTED_COMPLETED_SUSPENDED_GAMES,
    EXPECTED_RELOCATED_SUSPENDED_GAMES,
    EXPECTED_REGULAR_SEASON_GAMES,
    FD1_POSTCOMMIT_SCHEMA_VERSION,
    FD1_POSTCOMMIT_STATUS,
    FD1ManagerCoverageEvidence,
    FD1ReadinessCloseoutError,
    FD1SeasonWarehouseEvidence,
    FD1UmpireCoverageEvidence,
    FD1WarehouseCloseoutEvidence,
    MATERIALIZED_FAMILIES,
    REQUIRED_SOURCE_KEYS,
    _identity_derivation_checksum,
    build_fd1_readiness_closeout,
    canonical_fd1_closeout_checksum,
)
from app.gen2.fd1_reconciliation import (
    FIELD_FAMILIES,
    AspGameEvidence,
    ComparisonStatus,
    FieldComparison,
    GameReconciliation,
    ReconciliationResult,
    RetrosheetGameEvidence,
    normalize_asp_schedule_game_numbers,
)


def _outcomes(each: int = 100) -> tuple[OutcomeCount, ...]:
    return tuple(
        OutcomeCount(name, each)
        for name in (
            "STRIKEOUT",
            "WALK_HBP_CI",
            "HOME_RUN",
            "SINGLE",
            "DOUBLE",
            "TRIPLE",
            "BIP_OUT",
            "OTHER_REACH",
        )
    )


def _namespace_rows(
    unique: int, exact_unique: int, references: int, exact_references: int
) -> tuple[NamespaceCoverage, ...]:
    rows = []
    for namespace in IdentityNamespace:
        resolved_unique = exact_unique if namespace is not IdentityNamespace.RETROSHEET else unique
        resolved_references = (
            exact_references if namespace is not IdentityNamespace.RETROSHEET else references
        )
        rows.append(
            NamespaceCoverage(
                namespace=namespace,
                unique_player_count=unique,
                exact_unique_player_count=resolved_unique,
                conflict_unique_player_count=0,
                unresolved_unique_player_count=unique - resolved_unique,
                reference_count=references,
                exact_reference_count=resolved_references,
                conflict_reference_count=0,
                unresolved_reference_count=references - resolved_references,
            )
        )
    return tuple(rows)


def _link_rows(
    unique: int, exact_unique: int, references: int, exact_references: int
) -> tuple[ExactLinkRate, ...]:
    return tuple(
        ExactLinkRate(
            relation=relation,
            source_unique_id_count=unique,
            exact_unique_link_count=exact_unique,
            conflict_unique_link_count=0,
            unresolved_unique_link_count=unique - exact_unique,
            source_reference_count=references,
            exact_reference_count=exact_references,
            conflict_reference_count=0,
            unresolved_reference_count=references - exact_references,
        )
        for relation in IdentityRelation
    )


def _identity_slice(
    label: str,
    population: str,
    seasons: tuple[int, ...],
    *,
    unique: int = 100,
    exact_unique: int = 100,
    references: int = 1_000,
    exact_references: int = 1_000,
) -> IdentityCoverageSlice:
    return IdentityCoverageSlice(
        label=label,
        population=population,
        seasons=seasons,
        unique_retrosheet_players=unique,
        reference_count=references,
        namespace_coverage=_namespace_rows(
            unique, exact_unique, references, exact_references
        ),
        exact_link_rates=_link_rows(
            unique, exact_unique, references, exact_references
        ),
    )


def _identity(*, weak_unique_2024: bool = False) -> ActiveIdentityCoverageReport:
    seasons = tuple(
        _identity_slice(
            f"SEASON_{season}",
            (
                GEN2_PRIOR_ONLY_POPULATION
                if season < 2023
                else GEN2_PRIMARY_POPULATION
            ),
            (season,),
            unique=1_000 if weak_unique_2024 and season == 2024 else 100,
            exact_unique=994 if weak_unique_2024 and season == 2024 else 100,
        )
        for season in range(2021, 2026)
    )
    primary = _identity_slice(
        GEN2_PRIMARY_POPULATION,
        GEN2_PRIMARY_POPULATION,
        (2023, 2024, 2025),
        unique=1_000 if weak_unique_2024 else 100,
        exact_unique=994 if weak_unique_2024 else 100,
        references=3_000,
        exact_references=3_000,
    )
    source_tables = (
        "allplayers.csv",
        "batting.csv",
        "pitching.csv",
        "fielding.csv",
        "plays.csv",
    )
    report = ActiveIdentityCoverageReport(
        schema_version=FD1_IDENTITY_COVERAGE_SCHEMA_VERSION,
        source_release="retrosheet_through_2025",
        source_files=tuple(
            RetrosheetIdentitySourceEvidence(
                logical_table=table,
                source_key="RETROSHEET_CSV_DOWNLOADS",
                source_release="retrosheet_through_2025",
                content_sha256=str(index) * 64,
                content_length=100,
                rows_scanned=10,
                active_references=10,
            )
            for index, table in enumerate(source_tables, start=1)
        ),
        crosswalk_source_files=(),
        season_coverage=seasons,
        population_coverage=(
            _identity_slice(
                GEN2_PRIOR_ONLY_POPULATION,
                GEN2_PRIOR_ONLY_POPULATION,
                (2021, 2022),
                references=2_000,
                exact_references=2_000,
            ),
            primary,
        ),
        overall_coverage=_identity_slice(
            "GEN2_ACTIVE_2021_2025",
            "GEN2_ACTIVE_2021_2025",
            (2021, 2022, 2023, 2024, 2025),
            references=5_000,
            exact_references=5_000,
            unique=1_000 if weak_unique_2024 else 100,
            exact_unique=994 if weak_unique_2024 else 100,
        ),
        affected_players=(),
        primary_threshold=0.995,
        primary_unique_meets_threshold=not weak_unique_2024,
        primary_reference_meets_threshold=True,
        primary_meets_threshold=not weak_unique_2024,
        name_based_match_count=0,
        derivation_checksum="a" * 64,
    )
    return replace(
        report,
        derivation_checksum=_identity_derivation_checksum(report),
    )


def _warehouse(
    *,
    operator: dict[str, object] | None = None,
    identity: ActiveIdentityCoverageReport | None = None,
    reconciliation: ReconciliationResult | None = None,
) -> FD1WarehouseCloseoutEvidence:
    bound_operator = operator or _operator()
    bound_identity = identity or _identity()
    bound_reconciliation = reconciliation or RECONCILIATION
    reconciliation_checksum = (
        _RECONCILIATION_CHECKSUM
        if reconciliation is None
        else canonical_fd1_closeout_checksum(
            bound_reconciliation.as_dict(include_games=True)
        )
    )
    seasons = []
    manager_coverage = []
    umpire_coverage = []
    for season, games in EXPECTED_REGULAR_SEASON_GAMES.items():
        primary = season >= 2023
        seasons.append(
            FD1SeasonWarehouseEvidence(
                season=season,
                imported_regular_season_games=games,
                valid_final_games=games,
                home_win_games=games // 2,
                away_win_games=games - games // 2,
                plate_appearance_games=games,
                plate_appearances=800,
                transition_games=games,
                transitions=10_000,
                complete_lineup_games=games,
                lineup_timing_risk_games=games,
                historical_actual_lineup_rows=games * 18,
                isolated_enhanced_lineup_rows=games * 18,
                starter_games=games,
                starter_historical_reconstruction_games=games,
                starter_duration_rows=games * 2,
                historical_actual_starter_rows=games * 2,
                isolated_enhanced_starter_rows=games * 2,
                bullpen_usage_games=games,
                bullpen_usage_rows=games * 6,
                fielding_games=games,
                fielding_rows=games * 18,
                fully_covered_weather_games=games,
                pitch_sequence_games=games,
                taxonomy_terminal_events=800,
                taxonomy_mapped_terminal_events=800,
                taxonomy_outcome_counts=_outcomes(),
                tier_candidate_rows=120 if primary else 100,
                tier_eligible_rows=120 if primary else 100,
                strict_deployable_rows=100 if primary else 0,
                enhanced_research_rows=20 if primary else 0,
                prior_only_rows=0 if primary else 100,
            )
        )
        manager_usage_rows = games * 8
        manager_coverage.append(
            FD1ManagerCoverageEvidence(
                season=season,
                regular_season_games=games,
                games_with_complete_manager_coverage=games,
                manager_usage_rows=manager_usage_rows,
                manager_populated_usage_rows=manager_usage_rows,
                teamstats_lineage_verified_usage_rows=manager_usage_rows,
                unique_manager_ids=(
                    "retrosheet:player:mgraa001",
                    "retrosheet:player:mgraa002",
                ),
                missing_manager_game_ids=(),
            )
        )
        umpire_coverage.append(
            FD1UmpireCoverageEvidence(
                season=season,
                regular_season_games=games,
                weather_rows=games,
                games_with_plate_umpire=games,
                umpire_role_assignments=games * 4,
                gameinfo_lineage_verified_weather_rows=games,
                unique_umpire_ids=(
                    "retrosheet:player:umpaa001",
                    "retrosheet:player:umpaa002",
                ),
                missing_plate_umpire_game_ids=(),
            )
        )
    return FD1WarehouseCloseoutEvidence(
        seasons=tuple(seasons),
        manager_coverage=tuple(manager_coverage),
        umpire_coverage=tuple(umpire_coverage),
        temporal=TemporalAuditSummary(
            chronology_checked_rows=50_000,
            folds=(
                TemporalFoldEvidence(
                    "train-2023-validate-2024",
                    date(2023, 12, 31),
                    date(2024, 3, 20),
                    date(2024, 9, 30),
                    800,
                    800,
                ),
                TemporalFoldEvidence(
                    "train-through-2024-validate-2025",
                    date(2024, 12, 31),
                    date(2025, 3, 20),
                    date(2025, 9, 30),
                    1_600,
                    800,
                ),
            ),
        ),
        transition_state_outcome_cells=150,
        operator_ledger_checksum=canonical_fd1_closeout_checksum(bound_operator),
        identity_evidence_checksum=bound_identity.derivation_checksum,
        reconciliation_result_checksum=reconciliation_checksum,
        authorization_recorded_source_keys=REQUIRED_SOURCE_KEYS,
        use_classification_recorded_source_keys=REQUIRED_SOURCE_KEYS,
        license_recorded_source_keys=REQUIRED_SOURCE_KEYS,
        attribution_required_source_keys=REQUIRED_SOURCE_KEYS,
        attribution_recorded_source_keys=REQUIRED_SOURCE_KEYS,
    )


def _operator() -> dict[str, object]:
    reconciliation_checks = {
        "actual_participant_timing_isolated": True,
        "automatic_runner_rule_placements": True,
        "base_out_conservation": True,
        "completed_suspension_semantics": True,
        "current_and_future_excluded": True,
        "exact_seven_file_schema": True,
        "identity_references": True,
        "plate_appearance_taxonomy": True,
        "runner_conservation": True,
        "score_conservation": True,
        "starter_sequence": True,
        "statcast_claims_absent": True,
        "zero_stat_lineup_position_reconciliation": True,
    }
    versions = {
        key: {
            "id": f"id-{index}",
            "dataset_version": (
                "retrosheet_through_2025"
                if key == "RETROSHEET_CSV_DOWNLOADS"
                else f"version-{index}"
            ),
            "identity_checksum": f"{index}" * 64,
            "source_mode": "LIVE",
        }
        for index, key in enumerate(REQUIRED_SOURCE_KEYS, start=1)
    }
    downloads = [
        {
            "source_key": key,
            "object_key": f"object-{index}",
            "source_release": f"release-{index}",
            "upstream_revision": 1,
            "content_length": 100,
            "payload_sha256": f"{index}" * 64,
            "archive_sha256": f"{index}" * 64,
            "raw_logical_uri": f"raw/{index}",
            "download_identity_checksum": f"{index + 4}" * 64,
        }
        for index, key in enumerate(REQUIRED_SOURCE_KEYS, start=1)
    ]
    source_evidence = deepcopy(_OPERATOR_RECONCILIATION_EVIDENCE)
    source_evidence["logical_path"] = (
        ".runtime/gate3c-fd1/artifacts/"
        "publish-actual-execute-2025-retrosheet-source-evidence-"
        f"sha256-{source_evidence['sha256']}.jsonl"
    )
    normalization = normalize_asp_schedule_game_numbers(
        tuple(game.asp for game in RECONCILIATION.games)
    ).as_dict()
    operator: dict[str, object] = {
        "schema_version": "gate3c_fd1_actual_publication_operator_v5",
        "success": True,
        "success_scope": "COMMITTED_PUBLICATION_AND_POSTCOMMIT_VERIFICATION",
        "mode": "execute",
        "sanitized": True,
        "transaction": {
            "single_publish_call": True,
            "status_at_closeout": FD1_POSTCOMMIT_STATUS,
            "committed": True,
            "rolled_back": False,
            "committed_by_publication_function": False,
            "final_result_reported_by_operator_stdout": True,
            "commit_observed_by_postcommit_verifier": True,
        },
        "prohibited_activity": {
            "network_requests": 0,
            "provider_transport_constructed": False,
            "mlb_or_savant_automation": False,
            "predictive_model_fit_or_promotion": False,
            "prior_period_park_factor_feature_estimation": True,
            "odds_acquisition": False,
            "position_population": False,
        },
        "store": {"default_governed_limits": True},
        "source_versions": versions,
        "registered_downloads_used": downloads,
        "extractions_used": [
            {
                "source_key": REQUIRED_SOURCE_KEYS[0],
                "object_key": "object-1",
                "archive_sha256": "1" * 64,
                "identity_checksum": "c" * 64,
                "member_count": 1,
                "expanded_bytes": 100,
                "manifest_logical_uri": "raw/extraction-manifest.json",
                "member_bytes_verified": True,
            }
        ],
        "publication": {
            "mode": "execute",
            "code_revision": "d" * 64,
            "batches_seen": 100,
            "parquet_rows": 1_000,
            "identity_mappings_seen": 100,
            "partition_rows_created": 100,
            "partition_rows_reused": 0,
            "checkpoint_rows_created": 100,
            "checkpoint_rows_reused": 0,
            "ingestion_run_rows_created": 102,
            "ingestion_run_rows_reused": 0,
            "identity_mapping_rows_created": 100,
            "identity_mapping_rows_reused": 0,
            "derived_use_rows_created": 100,
            "derived_use_rows_reused": 0,
            "provider_transport_constructed": False,
            "network_requests": 0,
            "transaction_committed_by_function": False,
        },
        "readiness_evidence": {
            "raw_source_population_preflight": {
                "basis": "RAW_RETROSHEET_REGULAR_SEASON_AND_COMPLETION_EVIDENCE",
                "passed": True,
                "commit_permitted": True,
                "expected": {
                    "regular_season_games": {
                        str(key): value
                        for key, value in EXPECTED_REGULAR_SEASON_GAMES.items()
                    },
                    "completed_suspended_games": {
                        str(key): value
                        for key, value in EXPECTED_COMPLETED_SUSPENDED_GAMES.items()
                    },
                    "relocated_suspended_games": {
                        str(key): value
                        for key, value in EXPECTED_RELOCATED_SUSPENDED_GAMES.items()
                    },
                },
                "actual": {
                    "regular_season_games": {
                        str(key): value
                        for key, value in EXPECTED_REGULAR_SEASON_GAMES.items()
                    },
                    "completed_suspended_games": {
                        str(key): value
                        for key, value in EXPECTED_COMPLETED_SUSPENDED_GAMES.items()
                    },
                    "relocated_suspended_games": {
                        str(key): value
                        for key, value in EXPECTED_RELOCATED_SUSPENDED_GAMES.items()
                    },
                },
                "checks": {
                    "exact_season_set": True,
                    "regular_season_game_counts": True,
                    "completed_suspended_game_counts": True,
                    "exactly_one_2021_relocation": True,
                },
            },
            "prior_period_park_factor_formability": {
                "formable": True,
                "factor_count": 30,
                "seasons": [
                    {
                        "target_season": 2023,
                        "prior_seasons": [2021, 2022],
                        "factor_count": 10,
                        "formable": True,
                    },
                    {
                        "target_season": 2024,
                        "prior_seasons": [2021, 2022, 2023],
                        "factor_count": 10,
                        "formable": True,
                    },
                    {
                        "target_season": 2025,
                        "prior_seasons": [2021, 2022, 2023, 2024],
                        "factor_count": 10,
                        "formable": True,
                    },
                ],
            },
        },
        "retrosheet_materialization": {
            "start_date": "2021-01-01",
            "end_date": "2025-12-31",
            "knowledge_cutoff_exclusive": "2026-01-01",
            "single_pass_source_scan": True,
            "monthly_window_count": 60,
            "monthly": [
                {
                    "season": season,
                    "month": month,
                    "window_start": date(season, month, 1).isoformat(),
                    "window_end": date(
                        season, month, monthrange(season, month)[1]
                    ).isoformat(),
                    "selected_games": (
                        EXPECTED_REGULAR_SEASON_GAMES[season] if month == 1 else 0
                    ),
                    "completed_suspended_games": (
                        EXPECTED_COMPLETED_SUSPENDED_GAMES[season]
                        if month == 1
                        else 0
                    ),
                    "relocated_suspended_games": (
                        EXPECTED_RELOCATED_SUSPENDED_GAMES[season]
                        if month == 1
                        else 0
                    ),
                    "reconciliation_checks": reconciliation_checks,
                }
                for season in EXPECTED_REGULAR_SEASON_GAMES
                for month in range(1, 13)
            ],
            "seasons": {
                str(key): {
                    "selected_games": value,
                    "completed_suspended_games": (
                        EXPECTED_COMPLETED_SUSPENDED_GAMES[key]
                    ),
                    "relocated_suspended_games": (
                        EXPECTED_RELOCATED_SUSPENDED_GAMES[key]
                    ),
                    "family_rows": {
                        family: EXPECTED_REGULAR_SEASON_GAMES[key]
                        for family in MATERIALIZED_FAMILIES
                    },
                }
                for key, value in EXPECTED_REGULAR_SEASON_GAMES.items()
            },
            "families": list(MATERIALIZED_FAMILIES),
        },
        "player_priors": {
            "rows": 2_500,
            "published_rows": 2_500,
            "rows_by_season": {str(season): 500 for season in range(2021, 2026)},
        },
        "identity": {
            "name_based_person_matches": 0,
            "name_based_organization_matches": 0,
            "batches": 2,
            "mappings": 100,
        },
        "asp_game_number_preflight": normalization,
        "retrosheet_source_evidence": source_evidence,
        "schedule_fingerprint_bridge": {
            "logical_path": (
                ".runtime/gate3c-fd1/artifacts/"
                "publish-actual-execute-2025-schedule-fingerprint-bridge-"
                f"sha256-{'e' * 64}.json"
            ),
            "sha256": "e" * 64,
            "bytes": 200,
            "summary": {
                "matched_game_count": 2_430,
                "asp_game_number_normalization": normalization,
            },
        },
        "asp_retrosheet_full_reconciliation": {
            "logical_path": (
                ".runtime/gate3c-fd1/artifacts/"
                "publish-actual-execute-2025-asp-retrosheet-full-reconciliation-"
                f"sha256-{'f' * 64}.json"
            ),
            "sha256": "f" * 64,
            "bytes": 300,
            "summary": {"matched_game_count": 2_430},
        },
    }
    pretransaction = deepcopy(operator)
    pretransaction["success_scope"] = (
        "PRETRANSACTION_VALIDATION_AND_ARTIFACT_FINALIZATION"
    )
    pretransaction["transaction"].update(
        {
            "status_at_closeout": "PENDING_CALLER_COMMIT",
            "committed": False,
            "rolled_back": False,
            "committed_by_publication_function": False,
        }
    )
    pretransaction["transaction"].pop(
        "commit_observed_by_postcommit_verifier", None
    )
    proof: dict[str, object] = {
        "schema_version": FD1_POSTCOMMIT_SCHEMA_VERSION,
        "all_checks_passed": True,
        "pretransaction_ledger": {
            "logical_path": (
                ".runtime/gate3c-fd1/artifacts/"
                "publish-actual-execute-pretransaction-closeout-"
                f"sha256-{'1' * 64}.json"
            ),
            "sha256": "1" * 64,
            "canonical_sha256": canonical_fd1_closeout_checksum(pretransaction),
            "bytes": 1_000,
        },
        "execute_result": {
            "logical_path": (
                ".runtime/gate3c-fd1/artifacts/"
                f"publish-actual-execute-result-sha256-{'2' * 64}.json"
            ),
            "sha256": "2" * 64,
            "canonical_sha256": "3" * 64,
            "bytes": 500,
        },
        "operator_code": {
            "logical_path": ".runtime/gate3c-fd1/publish_actual.py",
            "sha256": "4" * 64,
            "bytes": 750,
            "stable_code_revision": "5" * 64,
            "combined_code_revision": "d" * 64,
        },
        "verifier_code": {
            "module_logical_path": "server/app/gen2/fd1_postcommit.py",
            "module_sha256": "6" * 64,
            "module_bytes": 500,
            "script_logical_path": ".runtime/gate3c-fd1/postcommit_verify.py",
            "script_sha256": "7" * 64,
            "script_bytes": 250,
        },
        "referenced_artifacts": [
            {
                "name": name,
                "logical_path": operator[name]["logical_path"],
                "sha256": operator[name]["sha256"],
                "canonical_sha256": checksum,
                "bytes": operator[name]["bytes"],
            }
            for name, checksum in (
                ("retrosheet_source_evidence", "8" * 64),
                ("schedule_fingerprint_bridge", "9" * 64),
                ("asp_retrosheet_full_reconciliation", "a" * 64),
            )
        ],
        "source_registry": {
            "source_version_count": 5,
            "registered_download_count": len(downloads),
            "operator_contributor_source_keys": list(REQUIRED_SOURCE_KEYS),
            "run_governance_source_keys": [
                "CHADWICK_REGISTER_ARCHIVES",
                "RETROSHEET_AUXILIARY_DOWNLOADS",
                "RETROSHEET_CSV_DOWNLOADS",
                "RETROSHEET_EVENT_ARCHIVES",
                "SABR_LAHMAN_BOX_DOWNLOADS",
            ],
            "population_checksum": "b" * 64,
        },
        "sql": {
            "ingestion_run_count": 102,
            "partition_run_count": 100,
            "identity_run_count": 2,
            "checkpoint_count": 100,
            "partition_count": 100,
            "partition_row_count": 1_000,
            "identity_mapping_count": 100,
            "derived_use_decision_count": 100,
            "population_checksum": "c" * 64,
        },
        "parquet": {
            "partition_count": 100,
            "row_count": 1_000,
            "population_checksum": "d" * 64,
            "all_sql_rows_validated": True,
            "all_store_files_accounted_for": True,
        },
        "prohibited_activity": {
            "network_requests": 0,
            "provider_transport_constructed": False,
            "mlb_or_savant_automation": False,
            "predictive_model_fit_or_promotion": False,
            "odds_acquisition": False,
            "position_population": False,
            "database_writes": 0,
            "governed_parquet_writes": 0,
            "raw_source_scans": 0,
            "subprocesses": 0,
        },
    }
    proof["derivation_checksum"] = canonical_fd1_closeout_checksum(proof)
    operator["postcommit_verification"] = proof
    return operator


def _rebind_postcommit_proof(operator: dict[str, object]) -> None:
    proof = operator["postcommit_verification"]
    pretransaction = deepcopy(operator)
    pretransaction["success_scope"] = (
        "PRETRANSACTION_VALIDATION_AND_ARTIFACT_FINALIZATION"
    )
    pretransaction["transaction"].update(
        {
            "status_at_closeout": "PENDING_CALLER_COMMIT",
            "committed": False,
            "rolled_back": False,
            "committed_by_publication_function": False,
        }
    )
    pretransaction["transaction"].pop(
        "commit_observed_by_postcommit_verifier", None
    )
    pretransaction.pop("postcommit_verification", None)
    proof["pretransaction_ledger"]["canonical_sha256"] = (
        canonical_fd1_closeout_checksum(pretransaction)
    )
    proof.pop("derivation_checksum", None)
    proof["derivation_checksum"] = canonical_fd1_closeout_checksum(proof)


def _reconciliation() -> ReconciliationResult:
    games = []
    when = datetime(2025, 1, 1, 18, tzinfo=UTC)
    for index in range(2_430):
        asp = AspGameEvidence(
            game_id=f"asp-{index:04d}",
            official_date=date(2025, 1, 1),
            scheduled_at=when,
            home_team_id=f"home-{index:04d}",
            away_team_id=f"away-{index:04d}",
            home_score=3,
            away_score=2,
            source_record_id=f"source-{index:04d}",
            source_version_id="live-v1",
        )
        retro = RetrosheetGameEvidence(
            raw_game_id=f"RET{index:09d}",
            source_game_id=f"retrosheet:game:{index:04d}",
            game_date=date(2025, 1, 1),
            home_team_raw_id="HOM",
            away_team_raw_id="AWY",
            game_number=1,
            source_game_number=0,
            home_score=3,
            away_score=2,
        )
        comparisons = MappingProxyType(
            {
                family: FieldComparison(
                    ComparisonStatus.MATCH,
                    {"value": family},
                    {"value": family},
                    MappingProxyType({}),
                )
                for family in FIELD_FAMILIES
            }
        )
        games.append(
            GameReconciliation(
                asp=asp,
                retrosheet=retro,
                mapping_method="EXACT_DATE_HOME_AWAY",
                mapped_retrosheet=MappingProxyType({}),
                comparisons=comparisons,
            )
        )
    status_counts = MappingProxyType(
        {
            status.value: 2_430 if status is ComparisonStatus.MATCH else 0
            for status in ComparisonStatus
        }
    )
    return ReconciliationResult(
        target_season=2025,
        expected_target_games=2_430,
        asp_target_count=2_430,
        retrosheet_target_count=2_430,
        matched_game_count=2_430,
        mapping_method_counts=MappingProxyType({"EXACT_DATE_HOME_AWAY": 2_430}),
        family_counts=MappingProxyType(
            {family: status_counts for family in FIELD_FAMILIES}
        ),
        games=tuple(games),
        unmatched_asp=(),
        unmatched_retrosheet=(),
        crosswalk_summary=MappingProxyType({"matching_method": "EXPLICIT_ID_ONLY"}),
    )


RECONCILIATION = _reconciliation()


def _operator_reconciliation_evidence(
    result: ReconciliationResult,
) -> dict[str, object]:
    rows = sorted(
        [*(game.retrosheet for game in result.games), *result.unmatched_retrosheet],
        key=lambda row: row.raw_game_id,
    )
    payload = b"".join(
        json.dumps(
            row.as_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
        for row in rows
    )
    return {
        "logical_path": "runtime/2025-retrosheet-evidence.jsonl",
        "record_count": len(rows),
        "expected_record_count": 2_430,
        "sha256": sha256(payload).hexdigest(),
        "bytes": len(payload),
        "derived_from_materialized_2025_rows": {
            family: 2_430
            for family in (
                "play_transition",
                "starter_usage",
                "player_game_batting",
                "player_game_pitching",
                "player_game_fielding",
            )
        },
        "source_game_number_counts": {"0": 2_430},
        "canonical_game_number_counts": {"1": 2_430},
        "source_zero_normalization": (
            "RETROSHEET_SOURCE_0_SINGLE_TO_CANONICAL_1_IN_RECONCILER"
        ),
        "operator_game_number_workaround_applied": False,
    }


_OPERATOR_RECONCILIATION_EVIDENCE = _operator_reconciliation_evidence(
    RECONCILIATION
)
_RECONCILIATION_CHECKSUM = canonical_fd1_closeout_checksum(
    RECONCILIATION.as_dict(include_games=True)
)


def test_closeout_projects_exact_sources_seasons_training_and_terminal_decision() -> None:
    closeout = build_fd1_readiness_closeout(
        _operator(), _warehouse(), _identity(), RECONCILIATION
    )

    assert closeout.result.decision is ReadinessDecision.READY_FOR_GEN2_FREE_DATA_MODELING
    assert closeout.evidence.training.chronological_player_prior_rows == 1_500
    assert closeout.evidence.training.prior_period_park_factor_rows == 30
    assert closeout.result.season(2024).player_identity_unique.ratio == 1.0
    assert closeout.evidence.population_separation.tier_candidate_row_count == 560
    assert closeout.evidence.population_separation.tier_eligible_row_count == 560
    exported = closeout.as_dict()
    assert exported["manager_coverage"][0][
        "games_with_complete_manager_coverage"
    ] == EXPECTED_REGULAR_SEASON_GAMES[2021]
    assert exported["manager_coverage"][0]["manager_usage_rows"] == (
        EXPECTED_REGULAR_SEASON_GAMES[2021] * 8
    )
    assert exported["umpire_coverage"][0]["games_with_plate_umpire"] == (
        EXPECTED_REGULAR_SEASON_GAMES[2021]
    )
    assert exported["umpire_coverage"][0]["umpire_role_assignments"] == (
        EXPECTED_REGULAR_SEASON_GAMES[2021] * 4
    )
    reported_checksum = exported.pop("derivation_checksum")
    assert reported_checksum == closeout.derivation_checksum
    assert reported_checksum == sha256(
        json.dumps(
            exported,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def test_closeout_binds_source_missing_game_number_preflight() -> None:
    operator = _operator()
    preflight = operator["asp_game_number_preflight"]
    bridge_preflight = operator["schedule_fingerprint_bridge"]["summary"][
        "asp_game_number_normalization"
    ]

    assert preflight == bridge_preflight
    assert preflight["asp_source_rows_mutated"] is False
    assert preflight["source_field_persistence"] == {
        "status": "UNAVAILABLE_FOR_ALL_GAMES",
        "persistence_gap": True,
        "source_evidence_preserved": True,
    }
    assert preflight["source_missing_derived_count"] == 2_430
    build_fd1_readiness_closeout(
        operator,
        _warehouse(),
        _identity(),
        RECONCILIATION,
    )

    operator["asp_game_number_preflight"] = deepcopy(preflight)
    operator["asp_game_number_preflight"]["evidence_sha256"] = "0" * 64
    _rebind_postcommit_proof(operator)
    with pytest.raises(FD1ReadinessCloseoutError, match="game-number normalization"):
        build_fd1_readiness_closeout(
            operator,
            _warehouse(operator=operator),
            _identity(),
            RECONCILIATION,
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "status",
        "observer",
        "all_checks",
        "pretransaction_binding",
        "artifact_binding",
        "sql_count",
        "source_registry_count",
        "derived_use_count",
        "verifier_path",
        "database_write",
        "subprocess",
    ),
)
def test_readiness_requires_exact_postcommit_verifier_proof(mutation: str) -> None:
    operator = _operator()
    proof = operator["postcommit_verification"]
    if mutation == "status":
        operator["transaction"]["status_at_closeout"] = "COMMITTED"
    elif mutation == "observer":
        operator["transaction"]["commit_observed_by_postcommit_verifier"] = False
    elif mutation == "all_checks":
        proof["all_checks_passed"] = False
    elif mutation == "pretransaction_binding":
        proof["pretransaction_ledger"]["canonical_sha256"] = "0" * 64
    elif mutation == "artifact_binding":
        proof["referenced_artifacts"][0]["bytes"] += 1
    elif mutation == "sql_count":
        proof["sql"]["partition_count"] -= 1
    elif mutation == "source_registry_count":
        proof["source_registry"]["registered_download_count"] -= 1
    elif mutation == "derived_use_count":
        proof["sql"]["derived_use_decision_count"] += 1
    elif mutation == "verifier_path":
        proof["verifier_code"]["module_logical_path"] = "server/other.py"
    elif mutation == "database_write":
        proof["prohibited_activity"]["database_writes"] = 1
    else:
        proof["prohibited_activity"]["subprocesses"] = 1
    if mutation not in {"status", "observer"}:
        proof.pop("derivation_checksum")
        proof["derivation_checksum"] = canonical_fd1_closeout_checksum(proof)

    with pytest.raises(FD1ReadinessCloseoutError, match="postcommit|transaction"):
        build_fd1_readiness_closeout(
            operator,
            _warehouse(),
            _identity(),
            RECONCILIATION,
        )


def test_manager_coverage_and_lineage_are_fail_closed() -> None:
    manager = _warehouse().manager_coverage[0]

    with pytest.raises(FD1ReadinessCloseoutError, match="does not reconcile"):
        replace(
            manager,
            games_with_complete_manager_coverage=manager.regular_season_games - 1,
        )

    with pytest.raises(FD1ReadinessCloseoutError, match="missing its manager"):
        replace(
            manager,
            manager_populated_usage_rows=manager.manager_usage_rows - 1,
        )

    with pytest.raises(FD1ReadinessCloseoutError, match="teamstats lineage"):
        replace(
            manager,
            teamstats_lineage_verified_usage_rows=manager.manager_usage_rows - 1,
        )


def test_partial_umpire_coverage_is_reported_without_blocking_modeling() -> None:
    warehouse = _warehouse()
    umpire = warehouse.umpire_coverage[0]
    partial = replace(
        umpire,
        games_with_plate_umpire=umpire.regular_season_games - 1,
        umpire_role_assignments=(umpire.regular_season_games - 1) * 4,
        missing_plate_umpire_game_ids=(
            "retrosheet:game:AAA202100001",
        ),
    )
    warehouse = replace(
        warehouse,
        umpire_coverage=(partial, *warehouse.umpire_coverage[1:]),
        derivation_checksum="",
    )

    closeout = build_fd1_readiness_closeout(
        _operator(), warehouse, _identity(), RECONCILIATION
    )

    assert closeout.result.decision is ReadinessDecision.READY_FOR_GEN2_FREE_DATA_MODELING
    assert closeout.as_dict()["umpire_coverage"][0][
        "missing_plate_umpire_game_ids"
    ] == ["retrosheet:game:AAA202100001"]


def test_umpire_counts_and_gameinfo_lineage_are_fail_closed() -> None:
    umpire = _warehouse().umpire_coverage[0]

    with pytest.raises(FD1ReadinessCloseoutError, match="does not reconcile"):
        replace(
            umpire,
            games_with_plate_umpire=umpire.regular_season_games - 1,
        )

    with pytest.raises(FD1ReadinessCloseoutError, match="role assignments"):
        replace(
            umpire,
            umpire_role_assignments=umpire.games_with_plate_umpire - 1,
        )

    with pytest.raises(FD1ReadinessCloseoutError, match="gameinfo lineage"):
        replace(
            umpire,
            gameinfo_lineage_verified_weather_rows=umpire.weather_rows - 1,
        )


def test_missing_source_governance_is_integrity_failure_not_projection_error() -> None:
    warehouse = replace(
        _warehouse(),
        attribution_recorded_source_keys=REQUIRED_SOURCE_KEYS[1:],
        derivation_checksum="",
    )

    closeout = build_fd1_readiness_closeout(
        _operator(), warehouse, _identity(), RECONCILIATION
    )

    assert closeout.result.decision is ReadinessDecision.NOT_READY_DATA_INTEGRITY_FAILURE
    assert any("missing source governance" in row for row in closeout.result.integrity_failures)

    warehouse = replace(
        _warehouse(),
        license_recorded_source_keys=REQUIRED_SOURCE_KEYS[1:],
        derivation_checksum="",
    )
    closeout = build_fd1_readiness_closeout(
        _operator(), warehouse, _identity(), RECONCILIATION
    )
    assert closeout.result.decision is ReadinessDecision.NOT_READY_DATA_INTEGRITY_FAILURE


def test_unique_identity_rate_blocks_even_when_reference_rate_passes() -> None:
    identity = _identity(weak_unique_2024=True)
    closeout = build_fd1_readiness_closeout(
        _operator(), _warehouse(identity=identity), identity, RECONCILIATION
    )

    assert closeout.result.decision is ReadinessDecision.NOT_READY_DATA_INTEGRITY_FAILURE
    assert not closeout.result.answer("player_ids_resolved").passed
    assert "unique_players=" in closeout.result.answer("player_ids_resolved").evidence


def test_dry_run_operator_and_inconsistent_reconciliation_fail_closed() -> None:
    dry_run = deepcopy(_operator())
    dry_run["mode"] = "dry-run"
    with pytest.raises(FD1ReadinessCloseoutError, match="successful execute"):
        build_fd1_readiness_closeout(
            dry_run, _warehouse(), _identity(), RECONCILIATION
        )

    broken = replace(RECONCILIATION, matched_game_count=2_429)
    with pytest.raises(FD1ReadinessCloseoutError, match="game detail count"):
        build_fd1_readiness_closeout(
            _operator(), _warehouse(reconciliation=broken), _identity(), broken
        )

    broken = replace(RECONCILIATION, mapping_method_counts=MappingProxyType({}))
    with pytest.raises(FD1ReadinessCloseoutError, match="mapping-method counts"):
        build_fd1_readiness_closeout(
            _operator(), _warehouse(reconciliation=broken), _identity(), broken
        )


def test_operator_monthly_and_governed_store_proofs_fail_closed() -> None:
    failed_month = deepcopy(_operator())
    failed_month["retrosheet_materialization"]["monthly"][0][
        "reconciliation_checks"
    ]["score_conservation"] = False
    _rebind_postcommit_proof(failed_month)
    with pytest.raises(FD1ReadinessCloseoutError, match="monthly materialization"):
        build_fd1_readiness_closeout(
            failed_month, _warehouse(), _identity(), RECONCILIATION
        )

    wrong_window = deepcopy(_operator())
    wrong_window["retrosheet_materialization"]["monthly"][0][
        "window_end"
    ] = "2021-01-30"
    _rebind_postcommit_proof(wrong_window)
    with pytest.raises(FD1ReadinessCloseoutError, match="calendar boundaries"):
        build_fd1_readiness_closeout(
            wrong_window, _warehouse(), _identity(), RECONCILIATION
        )

    ungoverned = deepcopy(_operator())
    ungoverned["store"]["default_governed_limits"] = False
    _rebind_postcommit_proof(ungoverned)
    with pytest.raises(FD1ReadinessCloseoutError, match="governed store"):
        build_fd1_readiness_closeout(
            ungoverned, _warehouse(), _identity(), RECONCILIATION
        )


def test_cross_run_and_operator_reconciliation_evidence_cannot_be_mixed() -> None:
    foreign_operator = _operator()
    foreign_operator["run_nonce"] = "different-valid-ledger"
    _rebind_postcommit_proof(foreign_operator)
    with pytest.raises(FD1ReadinessCloseoutError, match="different operator ledger"):
        build_fd1_readiness_closeout(
            foreign_operator, _warehouse(), _identity(), RECONCILIATION
        )

    tampered = deepcopy(_operator())
    tampered["retrosheet_source_evidence"]["sha256"] = "f" * 64
    tampered["postcommit_verification"]["referenced_artifacts"][0][
        "sha256"
    ] = "f" * 64
    _rebind_postcommit_proof(tampered)
    with pytest.raises(FD1ReadinessCloseoutError, match="checksum does not match"):
        build_fd1_readiness_closeout(
            tampered,
            _warehouse(operator=tampered),
            _identity(),
            RECONCILIATION,
        )


def test_tier_projection_and_evidence_checksum_are_fail_closed() -> None:
    with pytest.raises(FD1ReadinessCloseoutError, match="disjoint complete"):
        replace(
            _warehouse().seasons[2],
            tier_candidate_rows=121,
            tier_eligible_rows=121,
        )

    with pytest.raises(FD1ReadinessCloseoutError, match="checksum does not match"):
        replace(_warehouse(), derivation_checksum="f" * 64)

    identity = replace(_identity(), derivation_checksum="f" * 64)
    with pytest.raises(FD1ReadinessCloseoutError, match="identity derivation checksum"):
        build_fd1_readiness_closeout(
            _operator(),
            _warehouse(identity=identity),
            identity,
            RECONCILIATION,
        )
