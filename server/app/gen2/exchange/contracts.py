"""Strict Pydantic contract for ``SPORTS_DATASET_MANIFEST_V1``."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.gen2.exchange.canonical_json import (
    CANONICALIZATION_VERSION,
    canonical_json_bytes,
    canonical_sha256,
)


CONTRACT_NAME = "SPORTS_DATASET_MANIFEST"
CONTRACT_VERSION = "SPORTS_DATASET_MANIFEST_V1"
DATASET_ID = "asp.mlb.open-data.fd1"
EXPECTED_FILE_COUNT = 329
EXPECTED_ROW_COUNT = 3_738_096
EXPECTED_PARQUET_BYTES = 337_421_353
EXPECTED_POPULATIONS = {
    "strict_deployable": 1_460_755,
    "enhanced_research": 785_808,
    "prior_only": 1_491_533,
}
EXPECTED_PARTITION_AUTHORITY_SHA256 = (
    "8dec143b06622af068665b9fd1ad419a0353e76f0e0083b4c3459b8fce3cb437"
)
# The V1 authority digest covers every material field of every partition in
# canonical URI order.  This compact trust anchor prevents a consumer from
# accepting a coherently re-labeled/re-digested corpus without embedding a
# brittle 329-row allowlist in source code.
PARTITION_AUTHORITY_FIELDS = (
    "partition_id",
    "family",
    "season",
    "month",
    "package_relative_uri",
    "original_source_logical_identity",
    "row_count",
    "byte_count",
    "minimum_date",
    "maximum_date",
    "file_sha256",
    "schema_id",
    "physical_schema_fingerprint",
    "physical_schema",
    "source_key",
    "source_version_id",
    "checkpoint_lineage_id",
    "governance_checksum",
    "temporal_classification",
    "target_eligibility",
    "source_rights_ids",
)

# These module-level values are the offline trust anchors for the one immutable
# Gate 3C-X1 V1 corpus.  Keeping them centralized also lets focused tests replace
# the complete authority contract explicitly; production validation never infers
# authority from attacker-recomputable manifest identifiers.
EXPECTED_FINALIZED_RUN_ID = "92d6f851-815a-5a53-9b5d-6340549121c1"
EXPECTED_SOURCE_SEMANTIC_CHECKSUM = (
    "f9e788df4f746fe749e3cd731576bab3d191bea7559eb0b6c75230149589535c"
)
EXPECTED_DATABASE_MIGRATION = "20260815_0015"
EXPECTED_GENERATION_REVISION = (
    "8c8da2e335f6af77fd204cadc20180e6077ffeccab881ca60eed1ce4d73452b7"
)
EXPECTED_FINALIZER_REVISION = (
    "e8ec15683f0a34696e6185fcb355a48b6bb7ef90f47268631bfe003aac0184db"
)
EXPECTED_PUBLISHER = "AI Sports Predictor"
EXPECTED_EVIDENCE_CUTOFF = "2026-08-15"
EXPECTED_SUPPORTED_CONSUMER_RANGE = ">=1.0.0,<2.0.0"
EXPECTED_DEVELOPMENT_PURPOSE = (
    "Portable read-only local/private model development, internal derived "
    "analytics, and future read-only registration."
)
EXPECTED_READINESS_INVENTORY_BINDING_NOTE = (
    "The terminal readiness artifact binds the finalized pre-closeout source "
    "inventory at 28135 bytes / sha256 "
    "695562057c9786cf9eadb0ee232c7f966ab8da0fd601c94669ab7d9be890bdf1; "
    "the committed closeout inventory is separately governed at 28216 bytes / "
    "sha256 6af564e573b5ef3a1cc7c1b5d6250783a0c2b432a138e057089a3c42941c076f."
)
EXPECTED_SOURCE_ARTIFACTS = (
    (
        "finalized_evidence",
        "finalized_evidence",
        "source-evidence/finalized_evidence/"
        "sha256-08d2772e4009779d1d0c485d3a5a0dfefeaab3363dc39c5a8dd17903bacc1720.json",
        "08d2772e4009779d1d0c485d3a5a0dfefeaab3363dc39c5a8dd17903bacc1720",
        33_302,
    ),
    (
        "readiness_evidence",
        "readiness_evidence",
        "source-evidence/readiness_evidence/"
        "sha256-9b2b3b1d4c06f9abad8c2bcaff7aba87cff3a08624fe14e638da8d8d27ca2e76.json",
        "9b2b3b1d4c06f9abad8c2bcaff7aba87cff3a08624fe14e638da8d8d27ca2e76",
        90_754,
    ),
    (
        "active_identity_evidence",
        "active_identity_evidence",
        "source-evidence/active_identity_evidence/"
        "sha256-0323c940e38303f9757a9fd2f6d69db5de5b8279381513d39ffcc3331fdddc07.json",
        "0323c940e38303f9757a9fd2f6d69db5de5b8279381513d39ffcc3331fdddc07",
        54_046,
    ),
    (
        "quarantine_impact_evidence",
        "quarantine_impact_evidence",
        "source-evidence/quarantine_impact_evidence/"
        "sha256-cf238f0b16ba362af551cb4ac63fca19686f34d661647a6e0a4872090e2b1c4b.json",
        "cf238f0b16ba362af551cb4ac63fca19686f34d661647a6e0a4872090e2b1c4b",
        2_140_874,
    ),
)
EXPECTED_READINESS_OPERATOR_REVISION = (
    "b1888fbb6aafafa3639cd0c0288c58ee7f213b43f5bc1551f6caeed1f5c56766"
)

EXPECTED_SEASON_ROLES = (
    (2021, "PRIOR_ONLY_HISTORY"),
    (2022, "PRIOR_ONLY_HISTORY"),
    (2023, "PRIMARY_GEN2_MODELING"),
    (2024, "PRIMARY_GEN2_MODELING"),
    (2025, "PRIMARY_GEN2_MODELING"),
)
# season, role, games, PA, transitions, starters, bullpen appearances,
# weather covered/eligible, complete-lineup covered/eligible, min/max date
EXPECTED_SEASON_COVERAGE = (
    (
        2021, "PRIOR_ONLY_HISTORY", 2429, 181818, 190700, 4858, 16683,
        2429, 2429, 2412, 2429, "2021-04-01", "2021-12-31",
    ),
    (
        2022, "PRIOR_ONLY_HISTORY", 2430, 182052, 187969, 4860, 16018,
        2430, 2430, 2421, 2430, "2022-04-07", "2022-12-31",
    ),
    (
        2023, "PRIMARY_GEN2_MODELING", 2430, 184104, 191073, 4860, 15770,
        2430, 2430, 2423, 2430, "2023-03-30", "2023-12-31",
    ),
    (
        2024, "PRIMARY_GEN2_MODELING", 2429, 182449, 189536, 4858, 15835,
        2429, 2429, 2418, 2429, "2024-03-20", "2024-12-31",
    ),
    (
        2025, "PRIMARY_GEN2_MODELING", 2430, 182926, 189939, 4860, 16008,
        2430, 2430, 2420, 2430, "2025-03-18", "2025-12-31",
    ),
)

# family, logical schema, source key, immutable source-version ID
EXPECTED_FAMILY_AUTHORITIES = (
    (
        "bullpen_usage",
        "retrosheet_fd1_bullpen_usage_v6",
        "RETROSHEET_CSV_DOWNLOADS",
        "64db602a-090a-4e37-b8f2-2a48d36dd7f2",
    ),
    (
        "pitch_sequence",
        "retrosheet_fd1_pitch_sequence_v5",
        "RETROSHEET_CSV_DOWNLOADS",
        "64db602a-090a-4e37-b8f2-2a48d36dd7f2",
    ),
    (
        "plate_appearance",
        "retrosheet_fd1_plate_appearance_v5",
        "RETROSHEET_CSV_DOWNLOADS",
        "64db602a-090a-4e37-b8f2-2a48d36dd7f2",
    ),
    (
        "play_transition",
        "retrosheet_fd1_play_transition_v5",
        "RETROSHEET_CSV_DOWNLOADS",
        "64db602a-090a-4e37-b8f2-2a48d36dd7f2",
    ),
    (
        "player_game_batting",
        "retrosheet_fd1_player_game_batting_v5",
        "RETROSHEET_CSV_DOWNLOADS",
        "64db602a-090a-4e37-b8f2-2a48d36dd7f2",
    ),
    (
        "player_game_fielding",
        "retrosheet_fd1_player_game_fielding_v5",
        "RETROSHEET_CSV_DOWNLOADS",
        "64db602a-090a-4e37-b8f2-2a48d36dd7f2",
    ),
    (
        "player_game_pitching",
        "retrosheet_fd1_player_game_pitching_v5",
        "RETROSHEET_CSV_DOWNLOADS",
        "64db602a-090a-4e37-b8f2-2a48d36dd7f2",
    ),
    (
        "player_prior",
        "gate3c_fd1_identity_prior_v1",
        "SABR_LAHMAN_BOX_DOWNLOADS",
        "3af682f0-ecdb-47d8-8e14-660ec5f7455c",
    ),
    (
        "starter_usage",
        "retrosheet_fd1_starter_usage_v6",
        "RETROSHEET_CSV_DOWNLOADS",
        "64db602a-090a-4e37-b8f2-2a48d36dd7f2",
    ),
    (
        "weather",
        "retrosheet_fd1_weather_v8",
        "RETROSHEET_CSV_DOWNLOADS",
        "64db602a-090a-4e37-b8f2-2a48d36dd7f2",
    ),
)

EXPECTED_FAMILY_SEMANTICS = (
    (
        "bullpen_usage",
        "Retrospective reliever appearance and bullpen-usage targets.",
        ("TARGET", "RETROSPECTIVE_CONTEXT"),
        ("RETROSPECTIVE", "AVAILABLE_ONLY_AFTER_GAME_FINAL", "ENHANCED_TIMING_RISK"),
        ("enhanced_research", "prior_only"),
        ("Actual reliever use is known after the game and is enhanced-only.",),
    ),
    (
        "pitch_sequence",
        "Retrosheet pitch-sequence strings for retrospective event research.",
        ("TARGET_INPUT", "RETROSPECTIVE_CONTEXT"),
        ("RETROSPECTIVE", "AVAILABLE_ONLY_AFTER_GAME_FINAL"),
        ("prior_only", "strict_deployable"),
        ("Retrosheet pitch strings are not Statcast pitch tracking.",),
    ),
    (
        "plate_appearance",
        "Canonical eight-class terminal plate-appearance outcomes.",
        ("TARGET",),
        ("RETROSPECTIVE", "AVAILABLE_ONLY_AFTER_GAME_FINAL"),
        ("prior_only", "strict_deployable"),
        ("Outcomes are retrospective targets, not same-game pregame features.",),
    ),
    (
        "play_transition",
        "Conserved base/out, runner, run, and score state transitions.",
        ("TARGET", "SIMULATOR_VALIDATION"),
        ("RETROSPECTIVE", "AVAILABLE_ONLY_AFTER_GAME_FINAL", "ENHANCED_TIMING_RISK"),
        ("enhanced_research", "prior_only"),
        ("Play state is retrospective and enhanced-only for tier projection.",),
    ),
    (
        "player_game_batting",
        "Retrospective player-game batting evidence and lineup roles.",
        ("TARGET", "LAGGABLE_FEATURE"),
        ("RETROSPECTIVE", "AVAILABLE_ONLY_AFTER_GAME_FINAL"),
        ("enhanced_research", "prior_only", "strict_deployable"),
        ("Actual starting-lineup rows carry historical timing risk.",),
    ),
    (
        "player_game_fielding",
        "Retrospective player-game fielding evidence.",
        ("TARGET", "LAGGABLE_FEATURE"),
        ("RETROSPECTIVE", "AVAILABLE_ONLY_AFTER_GAME_FINAL"),
        ("prior_only", "strict_deployable"),
        ("No tracking-based defensive measurements are present.",),
    ),
    (
        "player_game_pitching",
        "Retrospective player-game pitching evidence and actual starts.",
        ("TARGET", "LAGGABLE_FEATURE"),
        ("RETROSPECTIVE", "AVAILABLE_ONLY_AFTER_GAME_FINAL"),
        ("enhanced_research", "prior_only", "strict_deployable"),
        ("Actual starter rows carry historical timing risk.",),
    ),
    (
        "player_prior",
        "Chronological prior-period player batting, pitching, and fielding evidence.",
        ("PRIOR_FEATURE",),
        ("Y_MINUS_1_ONLY", "SAME_SEASON_PREDICTOR_INELIGIBLE"),
        ("prior_only", "strict_deployable"),
        ("A target season may use only Y-1 or earlier prior evidence.",),
    ),
    (
        "starter_usage",
        "Retrospective actual-starter duration targets.",
        ("TARGET",),
        ("RETROSPECTIVE", "AVAILABLE_ONLY_AFTER_GAME_FINAL", "ENHANCED_TIMING_RISK"),
        ("enhanced_research", "prior_only"),
        ("Actual starter identity and duration are postgame targets.",),
    ),
    (
        "weather",
        "Retrospective game and weather context from Retrosheet game information.",
        ("CONTEXT",),
        ("RETROSPECTIVE", "AVAILABLE_ONLY_AFTER_GAME_FINAL", "ENHANCED_TIMING_RISK"),
        ("enhanced_research", "prior_only"),
        ("Historical actual weather timing is not proven pregame.",),
    ),
)

# source key, source-version ID, dataset version, release identity checksum
EXPECTED_SOURCE_RELEASES = (
    (
        "CHADWICK_REGISTER_ARCHIVES",
        "4a4956de-0769-4bb0-9dc4-cc30515ba16a",
        "2e8e73355f9c77b963115377bd98c784cfeec10f",
        "8aa66c044fb49ceb681af16fb4c20bef582f94e56cdcc790f2634d26f1bf36b0",
    ),
    (
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "fef044cc-35e1-499a-8971-a8b29cd9dd7c",
        "retrosheet_through_2025",
        "47eaa3c9322215f5445c7254f72ea7dac4fa29013ae56308b187a87d75a6881e",
    ),
    (
        "RETROSHEET_CSV_DOWNLOADS",
        "64db602a-090a-4e37-b8f2-2a48d36dd7f2",
        "retrosheet_through_2025",
        "d952cbf26b3e37be338b12a24da4099a24fbafc19f839edc03631fb1ded42a52",
    ),
    (
        "RETROSHEET_EVENT_ARCHIVES",
        "9a08b5fd-e5ed-4043-a984-04021584f15e",
        "retrosheet_through_2025",
        "2ee67ae07abfc2abba53011cd22239eb7ed4e8185e10e6338b829413379c85e8",
    ),
    (
        "SABR_LAHMAN_BOX_DOWNLOADS",
        "3af682f0-ecdb-47d8-8e14-660ec5f7455c",
        "lahman_1871-2025_csv",
        "61d462de4f4030263ce46a01ee213d211adbd26ff869365ec18b3477063a3cf5",
    ),
)

EXPECTED_SOURCE_RIGHTS_IDS = (
    "CHADWICK_PERSONS_REGISTER",
    "LAHMAN_SEAMHEADS_NEGRO_LEAGUES_EXCLUDED",
    "NOAA_NCEI_LCDV2_ZERO_USE",
    "OPEN_METEO_FREE_HOSTED_ZERO_USE",
    "RETROSHEET",
    "SABR_LAHMAN_MODERN_MLB",
)
EXPECTED_RIGHTS_BY_PARTITION_SOURCE = (
    (
        "RETROSHEET_CSV_DOWNLOADS",
        ("CHADWICK_PERSONS_REGISTER", "RETROSHEET"),
    ),
    (
        "SABR_LAHMAN_BOX_DOWNLOADS",
        ("CHADWICK_PERSONS_REGISTER", "SABR_LAHMAN_MODERN_MLB"),
    ),
)

EXPECTED_TARGET_DEFINITIONS = (
    ("bullpen_usage", ("bullpen_usage",), "TARGET"),
    ("chronological_player_priors", ("player_prior",), "FEATURE"),
    (
        "game_context",
        (
            "player_game_batting",
            "player_game_fielding",
            "player_game_pitching",
            "weather",
        ),
        "CONTEXT",
    ),
    (
        "plate_appearance_outcome",
        ("pitch_sequence", "plate_appearance"),
        "TARGET",
    ),
    ("play_base_out_transition", ("play_transition",), "TARGET_AND_FEATURE"),
    (
        "starter_duration",
        ("player_game_pitching", "starter_usage"),
        "TARGET",
    ),
)
EXPECTED_TARGET_NARRATIVES = (
    (
        "bullpen_usage",
        "Actual relief appearances and postgame bullpen usage.",
        ("Actual appearance evidence is enhanced-only.",),
    ),
    (
        "chronological_player_priors",
        "Annual player evidence available only to later target seasons.",
        ("Y-1 or earlier; same-season future totals are prohibited.",),
    ),
    (
        "game_context",
        "Game, park, participant, fielding, and actual-weather context.",
        ("Historical actual-weather timing is not proven pregame.",),
    ),
    (
        "plate_appearance_outcome",
        "Canonical terminal eight-class plate-appearance outcome.",
        (
            "FD1 rows carry importer taxonomy retrosheet_to_mlb_gen2_pa_v1; "
            "probability vectors use the semantic model order recorded here.",
        ),
    ),
    (
        "play_base_out_transition",
        "Conserved outs, bases, runners, runs, and score transition.",
        ("Retrospective play state is enhanced-only.",),
    ),
    (
        "starter_duration",
        "Actual starter identity and innings/batters-faced duration.",
        ("Actual starter evidence is not proven prospectively timed.",),
    ),
)
EXPECTED_PA_TAXONOMY_VERSION = "mlb_gen2_pa_outcome_taxonomy_v1"
EXPECTED_PA_TAXONOMY_IDENTITY = (
    "20e50d93f47423579173b95d619676236a2a1874b4e0dcb6811b9c40730a987a"
)

EXPECTED_FOLDS = (
    (
        "train-2023-validate-2024",
        (2023,),
        2024,
        "2023-10-01",
        "2024-03-20",
        "2024-09-30",
        184_104,
        182_449,
    ),
    (
        "train-through-2024-validate-2025",
        (2023, 2024),
        2025,
        "2024-09-30",
        "2025-03-18",
        "2025-09-28",
        366_553,
        182_926,
    ),
)
EXPECTED_TEMPORAL_SEASON_ROLES = (
    ("2021-2022", "PRIOR_ONLY_HISTORY"),
    ("2023-2025", "PRIMARY_GEN2_MODELING"),
)
EXPECTED_TEMPORAL_NARRATIVES = (
    (
        "player_prior_rule",
        "Annual Dec-31 player_prior rows have eligible_from_season=season+1; "
        "target season Y may use only Y-1 or earlier evidence.",
    ),
    (
        "strict_timing_risk_separation",
        "Actual lineup, starter, reliever, play-state, and untimestamped-weather "
        "rows are isolated in enhanced_research.",
    ),
    (
        "same_date_game_ordering",
        "Use final_available_date, source game_number, doubleheader_id, and canonical "
        "game identity; never borrow later same-date game evidence.",
    ),
    (
        "suspended_game_handling",
        "Retain original game identity/date and delay availability until the source "
        "completion/final date.",
    ),
    (
        "doubleheader_handling",
        "Preserve source game_number and doubleheader_id as distinct ordered games.",
    ),
    (
        "fold_compatibility",
        "Rolling-origin only; training_end precedes validation_start.",
    ),
)
EXPECTED_DATA_QUALITY = (
    ("eight_class_taxonomy_complete", True),
    ("unsafe_terminal_events", 0),
    ("transition_conservation_passed", True),
    ("score_run_runner_conservation_passed", True),
    ("identity_coverage_passed", True),
    ("tier_leaks", 0),
    ("temporal_leaks", 0),
    ("unique_partitions", True),
    ("schema_validation_passed", True),
    ("quarantine_downstream_impact", 0),
)

EXPECTED_ALLOWED_USES = (
    "INTERNAL_DERIVED_ANALYTICS",
    "LOCAL_PRIVATE_MODEL_DEVELOPMENT",
    "READ_ONLY_REGISTRATION",
)
EXPECTED_PROHIBITED_USES = (
    "PUBLIC_BULK_DOWNLOAD",
    "PUBLIC_RAW_REDISTRIBUTION",
    "RAW_DATA_RESALE",
    "UNREVIEWED_COMMERCIAL_REDISTRIBUTION",
)
EXPECTED_ENHANCED_FAMILIES = (
    "bullpen_usage",
    "play_transition",
    "starter_usage",
    "weather",
)
EXPECTED_POPULATION_NARRATIVES = (
    (
        "prior_only",
        "All manifested rows from the 2021-2022 history seasons.",
        "History retained for prior construction; never primary target rows.",
    ),
    (
        "enhanced_research",
        "Primary-season actual lineup/starter/reliever, play-state, and "
        "untimestamped-weather rows isolated by the finalized readiness operator.",
        "Retrospective timing-risk evidence; separate from strict deployment.",
    ),
    (
        "strict_deployable",
        "Remaining primary-season target or safely laggable rows after prior-only "
        "and enhanced timing-risk precedence.",
        "Targets remain postgame-only; feature use must still obey "
        "final_available_date and Y-1 prior rules.",
    ),
)

EXPECTED_IDENTITY_COVERAGE = (
    (
        "all_2021_2025",
        "GEN2_ACTIVE_2021_2025",
        (2021, 2022, 2023, 2024, 2025),
        2_693,
        21_356_559,
        1.0,
        1.0,
        0,
        0,
    ),
    (
        "primary_2023_2025",
        "GEN2_PRIMARY_2023_2025",
        (2023, 2024, 2025),
        2_071,
        12_838_083,
        1.0,
        1.0,
        0,
        0,
    ),
)
EXPECTED_IDENTITY_TOTALS = (
    ("quarantine_rows", 1_203),
    ("unresolved_quarantine_rows", 1_187),
    ("ambiguous_quarantine_rows", 16),
    ("downstream_impacted_rows", 0),
    ("canonical_identity_mapping_count", 736_056),
    (
        "identity_source_versions",
        (
            "3af682f0-ecdb-47d8-8e14-660ec5f7455c",
            "4a4956de-0769-4bb0-9dc4-cc30515ba16a",
            "64db602a-090a-4e37-b8f2-2a48d36dd7f2",
            "9a08b5fd-e5ed-4043-a984-04021584f15e",
            "fef044cc-35e1-499a-8971-a8b29cd9dd7c",
        ),
    ),
    (
        "derivation_checksum",
        "84b9e134b40d01143fc72d5fa30a87114c5db8d53845361826ce47dfed616a98",
    ),
)

# This is the actual immutable probability-vector order in event_taxonomy.py.
CANONICAL_PA_CLASS_ORDER = (
    "STRIKEOUT",
    "WALK_HBP_CI",
    "HOME_RUN",
    "SINGLE",
    "DOUBLE",
    "TRIPLE",
    "BIP_OUT",
    "OTHER_REACH",
)

REQUIRED_LIMITATION_IDS = (
    "INCOMPLETE_ACTUAL_LINEUP_COVERAGE",
    "HISTORICAL_ACTUAL_LINEUP_TIMING_RISK",
    "HISTORICAL_ACTUAL_STARTER_TIMING_RISK",
    "HISTORICAL_ACTUAL_WEATHER_TIMING_RISK",
    "NO_MULTI_SEASON_STATCAST",
    "NO_VELOCITY_SPIN_MOVEMENT_LOCATION",
    "NO_LAUNCH_SPEED_ANGLE",
    "NO_BARREL_HARD_HIT_XWOBA",
    "NO_TRACKING_DEFENSE_OR_RUNNER_SPEED",
    "NO_HISTORICAL_MARKET_CLOSING_LINE_OR_CLV",
    "RETROSPECTIVE_EVIDENCE_NOT_PROSPECTIVE_PROOF",
    "NOT_PRODUCTION_MODEL_APPROVAL",
)
EXPECTED_LIMITATIONS = (
    (
        "INCOMPLETE_ACTUAL_LINEUP_COVERAGE",
        "Actual-lineup coverage is 2423/2430 in 2023, 2418/2429 in 2024, "
        "and 2420/2430 in 2025.",
        "FD1_EXCHANGE",
    ),
    (
        "HISTORICAL_ACTUAL_LINEUP_TIMING_RISK",
        "Historical actual lineups are not proven pregame-known.",
        "FD1_EXCHANGE",
    ),
    (
        "HISTORICAL_ACTUAL_STARTER_TIMING_RISK",
        "Historical actual starters are not proven pregame-known.",
        "FD1_EXCHANGE",
    ),
    (
        "HISTORICAL_ACTUAL_WEATHER_TIMING_RISK",
        "Historical actual weather is not proven pregame-known.",
        "FD1_EXCHANGE",
    ),
    (
        "NO_MULTI_SEASON_STATCAST",
        "No multi-season Statcast corpus is present.",
        "FD1_EXCHANGE",
    ),
    (
        "NO_VELOCITY_SPIN_MOVEMENT_LOCATION",
        "No velocity, spin, movement, or pitch-location tracking is present.",
        "FD1_EXCHANGE",
    ),
    (
        "NO_LAUNCH_SPEED_ANGLE",
        "No launch speed or launch angle is present.",
        "FD1_EXCHANGE",
    ),
    (
        "NO_BARREL_HARD_HIT_XWOBA",
        "No barrel, hard-hit, or xwOBA evidence is present.",
        "FD1_EXCHANGE",
    ),
    (
        "NO_TRACKING_DEFENSE_OR_RUNNER_SPEED",
        "No tracking-based defense or runner-speed evidence is present.",
        "FD1_EXCHANGE",
    ),
    (
        "NO_HISTORICAL_MARKET_CLOSING_LINE_OR_CLV",
        "No historical market, closing-line, or CLV evidence is present.",
        "FD1_EXCHANGE",
    ),
    (
        "RETROSPECTIVE_EVIDENCE_NOT_PROSPECTIVE_PROOF",
        "Retrospective reconstruction does not prove prospective availability.",
        "FD1_EXCHANGE",
    ),
    (
        "NOT_PRODUCTION_MODEL_APPROVAL",
        "This package is dataset evidence, not production-model approval.",
        "FD1_EXCHANGE",
    ),
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class ContractError(ValueError):
    """The exchange contract is invalid or semantically inconsistent."""


def validate_portable_uri(value: str) -> str:
    """Accept only normalized, package-relative POSIX paths."""

    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ContractError("Package URI must be a non-empty POSIX relative path")
    if re.match(r"^[A-Za-z]:", value) or value.startswith(("//", "file:")):
        raise ContractError("Absolute, drive-qualified, UNC, and file URIs are forbidden")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractError("Package URI is not root-confined")
    normalized = path.as_posix()
    if normalized != value:
        raise ContractError("Package URI is not normalized")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DateRange(StrictModel):
    minimum: str
    maximum: str

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.maximum < self.minimum:
            raise ContractError("Date range is reversed")
        return self


class Coverage(StrictModel):
    covered: int = Field(ge=0)
    eligible: int = Field(ge=0)
    ratio: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        expected = 1.0 if self.eligible == 0 else self.covered / self.eligible
        if self.covered > self.eligible or abs(self.ratio - expected) > 1e-12:
            raise ContractError("Coverage fields do not reconcile")
        return self


class ArtifactIdentity(StrictModel):
    artifact_id: str
    logical_uri: str
    sha256: str
    byte_count: int = Field(gt=0)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        validate_portable_uri(self.logical_uri)
        if not _SHA256.fullmatch(self.sha256):
            raise ContractError("Artifact checksum is invalid")
        return self


class SourceRelease(StrictModel):
    source_key: str
    source_version_id: str
    dataset_version: str
    identity_checksum: str

    @model_validator(mode="after")
    def _checksum(self) -> Self:
        if not _SHA256.fullmatch(self.identity_checksum):
            raise ContractError("Source release identity checksum is invalid")
        return self


class SourceLineage(StrictModel):
    finalized_fd1_recovery_run_id: str
    source_semantic_manifest_checksum: str
    finalized_evidence: ArtifactIdentity
    readiness_evidence: ArtifactIdentity
    active_identity_evidence: ArtifactIdentity
    quarantine_impact_evidence: ArtifactIdentity
    generation_revision: str
    finalizer_revision: str
    publishing_code_revision: str
    database_migration: str
    source_releases: tuple[SourceRelease, ...]
    readiness_inventory_binding_note: str

    @model_validator(mode="after")
    def _valid(self) -> Self:
        for value in (
            self.source_semantic_manifest_checksum,
            self.generation_revision,
            self.finalizer_revision,
            self.publishing_code_revision,
        ):
            normalized = value.removeprefix("sha256:")
            if not _SHA256.fullmatch(normalized):
                raise ContractError("Lineage revision/checksum is invalid")
        keys = [item.source_key for item in self.source_releases]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ContractError("Source releases must be sorted and unique")
        return self


class SeasonCoverage(StrictModel):
    season: int = Field(ge=2021, le=2025)
    role: Literal["PRIOR_ONLY_HISTORY", "PRIMARY_GEN2_MODELING"]
    games: int = Field(gt=0)
    plate_appearances: int = Field(gt=0)
    transitions: int = Field(gt=0)
    starter_targets: int = Field(gt=0)
    bullpen_appearances: int = Field(gt=0)
    weather_coverage: Coverage
    complete_lineup_coverage: Coverage
    date_range: DateRange


class Predicate(StrictModel):
    op: Literal["and", "or", "not", "eq", "in"]
    field: str | None = None
    value: Any | None = None
    values: tuple[Any, ...] | None = None
    args: tuple[Predicate, ...] | None = None
    arg: Predicate | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        supplied = {
            "field": self.field is not None,
            "value": self.value is not None,
            "values": self.values is not None,
            "args": self.args is not None,
            "arg": self.arg is not None,
        }
        if self.op in {"and", "or"}:
            if not self.args or len(self.args) < 2 or any(
                supplied[name] for name in ("field", "value", "values", "arg")
            ):
                raise ContractError("Boolean predicate has an invalid shape")
        elif self.op == "not":
            if self.arg is None or any(
                supplied[name] for name in ("field", "value", "values", "args")
            ):
                raise ContractError("not predicate has an invalid shape")
        elif self.op == "eq":
            if self.field is None or self.value is None or any(
                supplied[name] for name in ("values", "args", "arg")
            ):
                raise ContractError("eq predicate has an invalid shape")
        elif self.op == "in":
            if self.field is None or not self.values or any(
                supplied[name] for name in ("value", "args", "arg")
            ):
                raise ContractError("in predicate has an invalid shape")
        if self.field is not None and not self.field.startswith(("partition.", "row.")):
            raise ContractError("Predicate fields must use partition. or row. namespaces")
        return self


class PopulationDefinition(StrictModel):
    population_id: Literal["strict_deployable", "enhanced_research", "prior_only"]
    description: str
    expected_row_count: int = Field(gt=0)
    allowed_uses: tuple[str, ...]
    prohibited_uses: tuple[str, ...]
    season_scope: tuple[int, ...]
    temporal_meaning: str
    predicate_contract_version: str
    readiness_operator_revision: str
    evaluation_precedence: int = Field(ge=1)
    row_selection: Predicate


def expected_population_contracts() -> tuple[dict[str, Any], ...]:
    """Return fresh exact non-narrative population contracts for frozen FD1."""

    prior = {"op": "in", "field": "partition.season", "values": [2021, 2022]}
    primary = {
        "op": "in",
        "field": "partition.season",
        "values": [2023, 2024, 2025],
    }
    enhanced = {
        "op": "or",
        "args": [
            {
                "op": "in",
                "field": "partition.family",
                "values": list(EXPECTED_ENHANCED_FAMILIES),
            },
            {
                "op": "and",
                "args": [
                    {
                        "op": "eq",
                        "field": "partition.family",
                        "value": "player_game_batting",
                    },
                    {
                        "op": "eq",
                        "field": "row.actual_starting_lineup",
                        "value": True,
                    },
                ],
            },
            {
                "op": "and",
                "args": [
                    {
                        "op": "eq",
                        "field": "partition.family",
                        "value": "player_game_pitching",
                    },
                    {
                        "op": "eq",
                        "field": "row.actual_start_flag",
                        "value": 1,
                    },
                ],
            },
        ],
    }
    return (
        {
            "population_id": "prior_only",
            "expected_row_count": EXPECTED_POPULATIONS["prior_only"],
            "allowed_uses": list(EXPECTED_ALLOWED_USES),
            "prohibited_uses": [
                *EXPECTED_PROHIBITED_USES,
                "PRIMARY_2023_2025_TARGET_TRAINING",
            ],
            "season_scope": [2021, 2022],
            "predicate_contract_version": "SPORTS_FD1_POPULATION_PREDICATE_V1",
            "readiness_operator_revision": EXPECTED_READINESS_OPERATOR_REVISION,
            "evaluation_precedence": 1,
            "row_selection": prior,
        },
        {
            "population_id": "enhanced_research",
            "expected_row_count": EXPECTED_POPULATIONS["enhanced_research"],
            "allowed_uses": list(EXPECTED_ALLOWED_USES),
            "prohibited_uses": [
                *EXPECTED_PROHIBITED_USES,
                "STRICT_DEPLOYABLE_PREGAME_FEATURE_USE",
            ],
            "season_scope": [2023, 2024, 2025],
            "predicate_contract_version": "SPORTS_FD1_POPULATION_PREDICATE_V1",
            "readiness_operator_revision": EXPECTED_READINESS_OPERATOR_REVISION,
            "evaluation_precedence": 2,
            "row_selection": {"op": "and", "args": [primary, enhanced]},
        },
        {
            "population_id": "strict_deployable",
            "expected_row_count": EXPECTED_POPULATIONS["strict_deployable"],
            "allowed_uses": list(EXPECTED_ALLOWED_USES),
            "prohibited_uses": list(EXPECTED_PROHIBITED_USES),
            "season_scope": [2023, 2024, 2025],
            "predicate_contract_version": "SPORTS_FD1_POPULATION_PREDICATE_V1",
            "readiness_operator_revision": EXPECTED_READINESS_OPERATOR_REVISION,
            "evaluation_precedence": 3,
            "row_selection": {
                "op": "and",
                "args": [primary, {"op": "not", "arg": enhanced}],
            },
        },
    )


class DataFamily(StrictModel):
    family_id: str
    schema_id: str
    purpose: str
    target_feature_role: tuple[str, ...]
    temporal_classification: tuple[str, ...]
    population_eligibility: tuple[str, ...]
    file_count: int = Field(gt=0)
    row_count: int = Field(gt=0)
    byte_count: int = Field(gt=0)
    date_range: DateRange
    source_key: str
    source_version_id: str
    rights_control_ids: tuple[str, ...]
    known_limitations: tuple[str, ...]


class PhysicalField(StrictModel):
    name: str
    dtype: str


class Partition(StrictModel):
    partition_id: str
    family: str
    season: int = Field(ge=2021, le=2025)
    month: int = Field(ge=1, le=12)
    package_relative_uri: str
    original_source_logical_identity: str
    row_count: int = Field(gt=0)
    byte_count: int = Field(gt=0)
    minimum_date: str
    maximum_date: str
    file_sha256: str
    schema_id: str
    physical_schema_fingerprint: str
    physical_schema: tuple[PhysicalField, ...]
    source_key: str
    source_version_id: str
    checkpoint_lineage_id: str
    governance_checksum: str
    temporal_classification: tuple[str, ...]
    target_eligibility: tuple[str, ...]
    source_rights_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _valid(self) -> Self:
        validate_portable_uri(self.package_relative_uri)
        validate_portable_uri(self.original_source_logical_identity)
        for value in (
            self.file_sha256,
            self.physical_schema_fingerprint,
            self.governance_checksum,
        ):
            if not _SHA256.fullmatch(value):
                raise ContractError("Partition checksum/identity is invalid")
        if not _SHA256.fullmatch(self.partition_id) or self.partition_id != (
            partition_identity_sha256(self)
        ):
            raise ContractError("Partition identity checksum does not match its lineage")
        if self.maximum_date < self.minimum_date:
            raise ContractError("Partition date range is reversed")
        try:
            minimum = date.fromisoformat(self.minimum_date)
            maximum = date.fromisoformat(self.maximum_date)
        except ValueError as exc:
            raise ContractError("Partition date range is not ISO calendar dates") from exc
        if any(
            value.year != self.season or value.month != self.month
            for value in (minimum, maximum)
        ):
            raise ContractError("Partition season/month differs from its date range")
        expected_package_uri = (
            f"parquet/{self.family}/season={self.season:04d}/month={self.month:02d}/"
            f"part-{self.file_sha256}.parquet"
        )
        expected_source_identity = (
            f"historical-events/source={self.source_key.lower()}/family={self.family}/"
            f"season={self.season:04d}/month={self.month:02d}/schema={self.schema_id}/"
            f"source-version={self.source_version_id}/"
            f"checkpoint={self.checkpoint_lineage_id}/governance={self.governance_checksum}/"
            f"part-{self.file_sha256}.parquet"
        )
        if self.package_relative_uri != expected_package_uri:
            raise ContractError("Partition package URI differs from its identity fields")
        if self.original_source_logical_identity != expected_source_identity:
            raise ContractError("Partition source identity URI differs from its fields")
        return self


def partition_identity_sha256(partition: Partition) -> str:
    """Recompute the frozen recovery-manifest partition identity projection."""

    return canonical_sha256(
        {
            "checkpoint_id": partition.checkpoint_lineage_id,
            "declared_schema": partition.schema_id,
            "family": partition.family,
            "governance_checksum": partition.governance_checksum,
            "month": partition.month,
            "season": partition.season,
            "source_key": partition.source_key.lower(),
            "source_version_id": partition.source_version_id,
        }
    )


def partition_authority_sha256(partitions: tuple[Partition, ...]) -> str:
    """Hash the exact canonical V1 authority projection for all partitions."""

    projection: list[dict[str, Any]] = []
    expected_fields = set(PARTITION_AUTHORITY_FIELDS)
    for partition in partitions:
        payload = partition.model_dump(mode="json")
        if set(payload) != expected_fields:
            raise ContractError("Partition authority projection does not cover every field")
        projection.append(
            {field: payload[field] for field in PARTITION_AUTHORITY_FIELDS}
        )
    projection.sort(key=lambda row: str(row["package_relative_uri"]))
    return canonical_sha256(projection)


class IdentitySlice(StrictModel):
    label: str
    seasons: tuple[int, ...]
    unique_players: int = Field(gt=0)
    reference_count: int = Field(gt=0)
    exact_reference_rate: float = Field(ge=0, le=1)
    exact_unique_rate: float = Field(ge=0, le=1)
    unresolved_references: int = Field(ge=0)
    ambiguous_references: int = Field(ge=0)


class IdentityCoverage(StrictModel):
    all_2021_2025: IdentitySlice
    primary_2023_2025: IdentitySlice
    quarantine_rows: int = Field(ge=0)
    unresolved_quarantine_rows: int = Field(ge=0)
    ambiguous_quarantine_rows: int = Field(ge=0)
    downstream_impacted_rows: int = Field(ge=0)
    canonical_identity_mapping_count: int = Field(gt=0)
    identity_source_versions: tuple[str, ...]
    derivation_checksum: str


class TargetDefinition(StrictModel):
    target_id: str
    family_ids: tuple[str, ...]
    role: Literal["TARGET", "FEATURE", "TARGET_AND_FEATURE", "CONTEXT"]
    description: str
    ordered_classes: tuple[str, ...] | None = None
    taxonomy_version: str | None = None
    taxonomy_identity: str | None = None
    limitations: tuple[str, ...] = ()


class Fold(StrictModel):
    fold_id: str
    training_seasons: tuple[int, ...]
    validation_season: int
    training_end: str
    validation_start: str
    validation_end: str
    training_rows: int = Field(gt=0)
    validation_rows: int = Field(gt=0)

    @model_validator(mode="after")
    def _chronological(self) -> Self:
        if not self.training_end < self.validation_start <= self.validation_end:
            raise ContractError("Fold chronology is invalid")
        return self


class TemporalContract(StrictModel):
    season_roles: dict[str, str]
    player_prior_rule: str
    no_current_game_evidence: bool
    no_current_season_future_evidence: bool
    strict_timing_risk_separation: str
    same_date_game_ordering: str
    suspended_game_handling: str
    doubleheader_handling: str
    fold_compatibility: str
    folds: tuple[Fold, ...]
    untouched_final_holdout_claimed: bool


class DataQualityEvidence(StrictModel):
    eight_class_taxonomy_complete: bool
    unsafe_terminal_events: int = Field(ge=0)
    transition_conservation_passed: bool
    score_run_runner_conservation_passed: bool
    identity_coverage_passed: bool
    tier_leaks: int = Field(ge=0)
    temporal_leaks: int = Field(ge=0)
    unique_partitions: bool
    schema_validation_passed: bool
    quarantine_downstream_impact: int = Field(ge=0)


class Limitation(StrictModel):
    limitation_id: str
    description: str
    scope: str


class PackageArtifact(StrictModel):
    logical_uri: str
    sha256: str
    byte_count: int = Field(gt=0)
    semantic_digest_member: bool

    @model_validator(mode="after")
    def _valid(self) -> Self:
        validate_portable_uri(self.logical_uri)
        if not _SHA256.fullmatch(self.sha256):
            raise ContractError("Package artifact checksum is invalid")
        return self


class SportsDatasetManifestV1(StrictModel):
    contract_name: Literal["SPORTS_DATASET_MANIFEST"]
    contract_version: Literal["SPORTS_DATASET_MANIFEST_V1"]
    canonicalization_version: Literal["SPORTS_CANONICAL_JSON_V1"]
    supported_consumer_range: str
    manifest_id: str
    manifest_sha256: str
    dataset_id: Literal["asp.mlb.open-data.fd1"]
    dataset_version_id: str
    package_id: str
    publisher: str
    published_at: datetime
    sport: Literal["Baseball"]
    league: Literal["MLB"]
    development_purpose: str
    evidence_cutoff: str
    source_lineage: SourceLineage
    seasons: tuple[SeasonCoverage, ...]
    populations: tuple[PopulationDefinition, ...]
    data_families: tuple[DataFamily, ...]
    partitions: tuple[Partition, ...]
    identity_coverage: IdentityCoverage
    target_definitions: tuple[TargetDefinition, ...]
    temporal_contract: TemporalContract
    data_quality_evidence: DataQualityEvidence
    known_limitations: tuple[Limitation, ...]
    source_rights_ids: tuple[str, ...]
    package_artifacts: tuple[PackageArtifact, ...]
    totals: dict[str, int]

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
    ) -> Self:
        """Validate mappings with strict JSON semantics while accepting JSON arrays.

        Pydantic's strict Python mode correctly rejects scalar coercion but also
        rejects JSON arrays for tuple-typed contract fields.  Canonical mappings
        are therefore encoded as JSON and validated in strict JSON mode, matching
        the published JSON Schema without weakening scalar types.
        """

        del strict
        if isinstance(obj, Mapping):
            return cls.model_validate_json(
                canonical_json_bytes(obj),
                strict=True,
                context=context,
            )
        return super().model_validate(
            obj,
            strict=True,
            from_attributes=from_attributes,
            context=context,
        )

    @model_validator(mode="after")
    def _semantic_contract(self) -> Self:
        if self.published_at.tzinfo is None:
            raise ContractError("published_at must include a timezone")
        if (
            self.supported_consumer_range != EXPECTED_SUPPORTED_CONSUMER_RANGE
            or self.publisher != EXPECTED_PUBLISHER
            or self.evidence_cutoff != EXPECTED_EVIDENCE_CUTOFF
            or self.development_purpose != EXPECTED_DEVELOPMENT_PURPOSE
        ):
            raise ContractError("Top-level V1 publishing semantics differ")
        if not _SHA256.fullmatch(self.manifest_sha256):
            raise ContractError("Manifest semantic checksum is invalid")
        digest = self.manifest_sha256
        if (
            self.manifest_id != f"sports-dataset-manifest:sha256:{digest}"
            or self.dataset_version_id != f"{self.dataset_id}:sha256:{digest}"
            or self.package_id != f"sha256-{digest}"
        ):
            raise ContractError("Manifest derived identifiers do not reconcile")

        lineage = self.source_lineage
        if (
            lineage.finalized_fd1_recovery_run_id != EXPECTED_FINALIZED_RUN_ID
            or lineage.source_semantic_manifest_checksum
            != EXPECTED_SOURCE_SEMANTIC_CHECKSUM
            or lineage.database_migration != EXPECTED_DATABASE_MIGRATION
        ):
            raise ContractError("Manifest does not bind the frozen FD1 source authority")
        if (
            lineage.generation_revision != EXPECTED_GENERATION_REVISION
            or lineage.finalizer_revision != EXPECTED_FINALIZER_REVISION
            or lineage.readiness_inventory_binding_note
            != EXPECTED_READINESS_INVENTORY_BINDING_NOTE
        ):
            raise ContractError("Source execution/binding evidence differs")
        source_artifacts = tuple(
            (
                field_name,
                artifact.artifact_id,
                artifact.logical_uri,
                artifact.sha256,
                artifact.byte_count,
            )
            for field_name, artifact in (
                ("finalized_evidence", lineage.finalized_evidence),
                ("readiness_evidence", lineage.readiness_evidence),
                ("active_identity_evidence", lineage.active_identity_evidence),
                (
                    "quarantine_impact_evidence",
                    lineage.quarantine_impact_evidence,
                ),
            )
        )
        if source_artifacts != EXPECTED_SOURCE_ARTIFACTS:
            raise ContractError("Source evidence-artifact inventory differs")
        releases = tuple(
            (
                row.source_key,
                row.source_version_id,
                row.dataset_version,
                row.identity_checksum,
            )
            for row in lineage.source_releases
        )
        if releases != EXPECTED_SOURCE_RELEASES:
            raise ContractError("Source-release authority inventory differs")

        season_coverage = tuple(
            (
                row.season,
                row.role,
                row.games,
                row.plate_appearances,
                row.transitions,
                row.starter_targets,
                row.bullpen_appearances,
                row.weather_coverage.covered,
                row.weather_coverage.eligible,
                row.complete_lineup_coverage.covered,
                row.complete_lineup_coverage.eligible,
                row.date_range.minimum,
                row.date_range.maximum,
            )
            for row in self.seasons
        )
        if season_coverage != EXPECTED_SEASON_COVERAGE:
            raise ContractError("Season coverage evidence differs from frozen FD1")
        _validate_population_inventory(self.populations)

        families = [row.family_id for row in self.data_families]
        expected_families = [row[0] for row in EXPECTED_FAMILY_AUTHORITIES]
        if families != expected_families or families != sorted(families):
            raise ContractError("Manifest does not contain the exact family inventory")
        uris = [row.package_relative_uri for row in self.partitions]
        folded = [uri.casefold() for uri in uris]
        if (
            len(self.partitions) != EXPECTED_FILE_COUNT
            or uris != sorted(uris)
            or len(folded) != len(set(folded))
        ):
            raise ContractError("Manifest partitions are incomplete, unordered, or duplicated")
        partition_ids = [row.partition_id for row in self.partitions]
        checkpoint_ids = [row.checkpoint_lineage_id for row in self.partitions]
        if len(set(partition_ids)) != len(partition_ids):
            raise ContractError("Partition identities must be unique")
        if len(set(checkpoint_ids)) != len(checkpoint_ids):
            raise ContractError("Partition checkpoint lineage identities must be unique")
        if (
            partition_authority_sha256(self.partitions)
            != EXPECTED_PARTITION_AUTHORITY_SHA256
        ):
            raise ContractError("Partition authority projection differs from frozen FD1")
        if set(row.family for row in self.partitions) != set(families):
            raise ContractError("Partition family is not declared")
        _validate_family_partition_contract(self.data_families, self.partitions)
        _validate_target_inventory(self.target_definitions, self.partitions)
        _validate_identity_coverage(self.identity_coverage)

        if self.totals != {
            "parquet_bytes": EXPECTED_PARQUET_BYTES,
            "parquet_files": EXPECTED_FILE_COUNT,
            "rows": EXPECTED_ROW_COUNT,
        }:
            raise ContractError("Manifest totals do not match finalized FD1 evidence")
        if sum(row.row_count for row in self.partitions) != EXPECTED_ROW_COUNT:
            raise ContractError("Partition rows do not reconcile")
        if sum(row.byte_count for row in self.partitions) != EXPECTED_PARQUET_BYTES:
            raise ContractError("Partition bytes do not reconcile")

        temporal = self.temporal_contract
        if (
            tuple(temporal.season_roles.items()) != EXPECTED_TEMPORAL_SEASON_ROLES
            or temporal.no_current_game_evidence is not True
            or temporal.no_current_season_future_evidence is not True
            or temporal.untouched_final_holdout_claimed is not False
        ):
            raise ContractError("Temporal safety claims differ from frozen FD1")
        folds = tuple(
            (
                row.fold_id,
                row.training_seasons,
                row.validation_season,
                row.training_end,
                row.validation_start,
                row.validation_end,
                row.training_rows,
                row.validation_rows,
            )
            for row in temporal.folds
        )
        if folds != EXPECTED_FOLDS:
            raise ContractError("Temporal fold inventory differs from frozen FD1")
        if tuple(
            (field, getattr(temporal, field))
            for field, _expected in EXPECTED_TEMPORAL_NARRATIVES
        ) != EXPECTED_TEMPORAL_NARRATIVES:
            raise ContractError("Temporal narrative contract differs from frozen FD1")
        if self.data_quality_evidence.model_dump() != dict(EXPECTED_DATA_QUALITY):
            raise ContractError("Data-quality evidence is incomplete or negative")

        limitations = tuple(
            (row.limitation_id, row.description, row.scope)
            for row in self.known_limitations
        )
        if limitations != EXPECTED_LIMITATIONS:
            raise ContractError("Known limitation inventory is incomplete or reordered")
        if self.source_rights_ids != EXPECTED_SOURCE_RIGHTS_IDS:
            raise ContractError("Source-rights inventory differs from frozen FD1")
        artifacts = tuple(
            (row.logical_uri, row.semantic_digest_member) for row in self.package_artifacts
        )
        if artifacts != (
            ("attribution.md", True),
            ("schema-registry.json", True),
            ("source-rights.json", True),
        ):
            raise ContractError("Semantic package-artifact inventory differs")
        return self


def _validate_population_inventory(
    populations: tuple[PopulationDefinition, ...],
) -> None:
    actual = []
    for row in populations:
        payload = row.model_dump(mode="json")
        payload["row_selection"] = row.row_selection.model_dump(
            mode="json", exclude_none=True
        )
        actual.append(
            {
                key: payload[key]
                for key in (
                    "population_id",
                    "expected_row_count",
                    "allowed_uses",
                    "prohibited_uses",
                    "season_scope",
                    "predicate_contract_version",
                    "readiness_operator_revision",
                    "evaluation_precedence",
                    "row_selection",
                )
            }
        )
    if tuple(actual) != expected_population_contracts():
        raise ContractError("Population policy/predicate inventory differs from frozen FD1")
    narratives = tuple(
        (row.population_id, row.description, row.temporal_meaning) for row in populations
    )
    if narratives != EXPECTED_POPULATION_NARRATIVES:
        raise ContractError("Population narrative contract differs from frozen FD1")


def _validate_family_partition_contract(
    families: tuple[DataFamily, ...], partitions: tuple[Partition, ...]
) -> None:
    authority = {
        family: (schema, source_key, source_version_id)
        for family, schema, source_key, source_version_id in EXPECTED_FAMILY_AUTHORITIES
    }
    rights_by_source = dict(EXPECTED_RIGHTS_BY_PARTITION_SOURCE)
    temporal_by_family = {row[0]: row[3] for row in EXPECTED_FAMILY_SEMANTICS}
    known_populations = frozenset(EXPECTED_POPULATIONS)
    by_family: dict[str, list[Partition]] = {family: [] for family in authority}

    for row in partitions:
        expected = authority.get(row.family)
        if expected is None or (
            row.schema_id,
            row.source_key,
            row.source_version_id,
        ) != expected:
            raise ContractError("Partition family/source/schema authority differs")
        expected_rights = rights_by_source.get(row.source_key)
        if expected_rights is None or row.source_rights_ids != expected_rights:
            raise ContractError("Partition source-right references differ")
        if not row.temporal_classification or len(set(row.temporal_classification)) != len(
            row.temporal_classification
        ):
            raise ContractError("Partition temporal classification is empty or duplicated")
        if row.temporal_classification != temporal_by_family[row.family]:
            raise ContractError("Partition temporal classification differs from its family")
        by_family[row.family].append(row)

    family_by_id = {row.family_id: row for row in families}
    semantics = {row[0]: row[1:] for row in EXPECTED_FAMILY_SEMANTICS}
    for family, (schema, source_key, source_version_id) in authority.items():
        row = family_by_id[family]
        members = by_family[family]
        expected_rights = rights_by_source[source_key]
        if (
            (row.schema_id, row.source_key, row.source_version_id)
            != (schema, source_key, source_version_id)
            or row.rights_control_ids != expected_rights
        ):
            raise ContractError("Family source/schema/rights authority differs")
        if (
            row.purpose,
            row.target_feature_role,
            row.temporal_classification,
            row.population_eligibility,
            row.known_limitations,
        ) != semantics[family]:
            raise ContractError("Family semantic contract differs from frozen FD1")
        if (
            not row.population_eligibility
            or len(set(row.population_eligibility)) != len(row.population_eligibility)
            or not set(row.population_eligibility).issubset(known_populations)
        ):
            raise ContractError("Family population references are invalid")
        if (
            row.file_count != len(members)
            or row.row_count != sum(item.row_count for item in members)
            or row.byte_count != sum(item.byte_count for item in members)
            or row.date_range.minimum != min(item.minimum_date for item in members)
            or row.date_range.maximum != max(item.maximum_date for item in members)
        ):
            raise ContractError("Family aggregate does not reconcile to its partitions")


def _validate_target_inventory(
    targets: tuple[TargetDefinition, ...], partitions: tuple[Partition, ...]
) -> None:
    actual = tuple((row.target_id, row.family_ids, row.role) for row in targets)
    if actual != EXPECTED_TARGET_DEFINITIONS:
        raise ContractError("Target definition inventory is incomplete or reordered")
    narratives = tuple(
        (row.target_id, row.description, row.limitations) for row in targets
    )
    if narratives != EXPECTED_TARGET_NARRATIVES:
        raise ContractError("Target narrative contract differs from frozen FD1")
    pa = next(row for row in targets if row.target_id == "plate_appearance_outcome")
    if (
        pa.ordered_classes != CANONICAL_PA_CLASS_ORDER
        or pa.taxonomy_version != EXPECTED_PA_TAXONOMY_VERSION
        or pa.taxonomy_identity != EXPECTED_PA_TAXONOMY_IDENTITY
    ):
        raise ContractError("Plate-appearance taxonomy identity/class order differs")
    for row in targets:
        if row.target_id != "plate_appearance_outcome" and any(
            value is not None
            for value in (row.ordered_classes, row.taxonomy_version, row.taxonomy_identity)
        ):
            raise ContractError("Only the PA target may declare a taxonomy/class order")
    eligibility_by_family: dict[str, tuple[str, ...]] = {}
    for family, _schema, _source, _version in EXPECTED_FAMILY_AUTHORITIES:
        eligibility_by_family[family] = tuple(
            target_id
            for target_id, family_ids, _role in EXPECTED_TARGET_DEFINITIONS
            if family in family_ids
        )
    for row in partitions:
        if row.target_eligibility != eligibility_by_family[row.family]:
            raise ContractError("Partition target eligibility differs from target definitions")


def _validate_identity_coverage(identity: IdentityCoverage) -> None:
    slices = tuple(
        (
            field,
            row.label,
            row.seasons,
            row.unique_players,
            row.reference_count,
            row.exact_reference_rate,
            row.exact_unique_rate,
            row.unresolved_references,
            row.ambiguous_references,
        )
        for field, row in (
            ("all_2021_2025", identity.all_2021_2025),
            ("primary_2023_2025", identity.primary_2023_2025),
        )
    )
    if slices != EXPECTED_IDENTITY_COVERAGE:
        raise ContractError("Identity coverage slices differ from frozen FD1")
    totals = tuple(
        (field, getattr(identity, field)) for field, _expected in EXPECTED_IDENTITY_TOTALS
    )
    if totals != EXPECTED_IDENTITY_TOTALS:
        raise ContractError("Identity quarantine/mapping evidence differs from frozen FD1")


def manifest_json_schema() -> dict[str, Any]:
    schema = SportsDatasetManifestV1.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://ai-sports-predictor.local/contracts/SPORTS_DATASET_MANIFEST_V1.schema.json"
    schema["title"] = CONTRACT_VERSION
    return schema


__all__ = [
    "CANONICAL_PA_CLASS_ORDER",
    "CONTRACT_NAME",
    "CONTRACT_VERSION",
    "ContractError",
    "DATASET_ID",
    "EXPECTED_ALLOWED_USES",
    "EXPECTED_DATABASE_MIGRATION",
    "EXPECTED_DATA_QUALITY",
    "EXPECTED_DEVELOPMENT_PURPOSE",
    "EXPECTED_ENHANCED_FAMILIES",
    "EXPECTED_EVIDENCE_CUTOFF",
    "EXPECTED_FAMILY_AUTHORITIES",
    "EXPECTED_FAMILY_SEMANTICS",
    "EXPECTED_FILE_COUNT",
    "EXPECTED_FINALIZER_REVISION",
    "EXPECTED_FINALIZED_RUN_ID",
    "EXPECTED_FOLDS",
    "EXPECTED_GENERATION_REVISION",
    "EXPECTED_IDENTITY_COVERAGE",
    "EXPECTED_IDENTITY_TOTALS",
    "EXPECTED_LIMITATIONS",
    "EXPECTED_PA_TAXONOMY_IDENTITY",
    "EXPECTED_PA_TAXONOMY_VERSION",
    "EXPECTED_PARQUET_BYTES",
    "EXPECTED_PARTITION_AUTHORITY_SHA256",
    "EXPECTED_POPULATIONS",
    "EXPECTED_POPULATION_NARRATIVES",
    "EXPECTED_PROHIBITED_USES",
    "EXPECTED_PUBLISHER",
    "EXPECTED_READINESS_OPERATOR_REVISION",
    "EXPECTED_READINESS_INVENTORY_BINDING_NOTE",
    "EXPECTED_RIGHTS_BY_PARTITION_SOURCE",
    "EXPECTED_ROW_COUNT",
    "EXPECTED_SEASON_COVERAGE",
    "EXPECTED_SEASON_ROLES",
    "EXPECTED_SOURCE_ARTIFACTS",
    "EXPECTED_SOURCE_RELEASES",
    "EXPECTED_SOURCE_RIGHTS_IDS",
    "EXPECTED_SOURCE_SEMANTIC_CHECKSUM",
    "EXPECTED_TARGET_DEFINITIONS",
    "EXPECTED_TARGET_NARRATIVES",
    "EXPECTED_SUPPORTED_CONSUMER_RANGE",
    "EXPECTED_TEMPORAL_NARRATIVES",
    "EXPECTED_TEMPORAL_SEASON_ROLES",
    "Predicate",
    "PARTITION_AUTHORITY_FIELDS",
    "REQUIRED_LIMITATION_IDS",
    "SportsDatasetManifestV1",
    "expected_population_contracts",
    "manifest_json_schema",
    "partition_authority_sha256",
    "validate_portable_uri",
]
