"""Semantic trust anchors for ``SPORTS_IDENTITY_BRIDGE_V1``.

These parent, source, evidence, rights, and limitation controls are part of the
attested publishing implementation.  Release-only self anchors live separately
in :mod:`app.gen2.exchange.identity_bridge.release_trust`.
"""

from __future__ import annotations

from app.gen2.exchange.identity_bridge.release_trust import (
    EXPECTED_ALIAS_AUTHORITY_SHA256,
    EXPECTED_BRIDGE_MANIFEST_SHA256,
    EXPECTED_DIMENSION_AUTHORITY_SHA256,
)


CONTRACT_NAME = "SPORTS_IDENTITY_BRIDGE"
CONTRACT_VERSION = "SPORTS_IDENTITY_BRIDGE_V1"
PUBLISHER = "AI Sports Predictor"
EVIDENCE_CUTOFF = "2026-08-15"

EXPECTED_PARENT_DATASET_ID = "asp.mlb.open-data.fd1"
EXPECTED_PARENT_MANIFEST_SHA256 = (
    "1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49"
)
EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256 = (
    "8dec143b06622af068665b9fd1ad419a0353e76f0e0083b4c3459b8fce3cb437"
)
EXPECTED_PARENT_DATASET_VERSION_ID = (
    f"{EXPECTED_PARENT_DATASET_ID}:sha256:{EXPECTED_PARENT_MANIFEST_SHA256}"
)
EXPECTED_PARENT_MANIFEST_ID = (
    "sports-dataset-manifest:sha256:"
    f"{EXPECTED_PARENT_MANIFEST_SHA256}"
)
EXPECTED_PARENT_PACKAGE_ID = f"sha256-{EXPECTED_PARENT_MANIFEST_SHA256}"

EXPECTED_PA_CLASS_ORDER = (
    "STRIKEOUT",
    "WALK_HBP_CI",
    "HOME_RUN",
    "SINGLE",
    "DOUBLE",
    "TRIPLE",
    "BIP_OUT",
    "OTHER_REACH",
)

ALLOWED_IDENTITY_NAMESPACES = (
    "RETROSHEET",
    "LAHMAN",
    "CHADWICK",
    "MLBAM",
    "BASEBALL_REFERENCE",
)

# Semantic members are ordered by portable logical URI.  The manifest and the
# two deterministic auxiliary files are deliberately outside this inventory.
REQUIRED_SEMANTIC_BRIDGE_FILES = (
    "active-player-dimension.parquet",
    "attribution.md",
    "player-identity-aliases.parquet",
    "source-rights.json",
)
REQUIRED_AUXILIARY_FILES = ("README.md", "validation-report.json")
FINAL_PACKAGE_FILE_COUNT = 7

PACKAGE_ALLOWED_PURPOSES = (
    "INTERNAL_DERIVED_ANALYTICS",
    "LOCAL_PRIVATE_MODEL_DEVELOPMENT",
    "READ_ONLY_REGISTRATION",
)
PACKAGE_PROHIBITED_PURPOSES = (
    "IDENTITY_DATA_RESALE",
    "PUBLIC_BULK_DOWNLOAD",
    "PUBLIC_RAW_BRIDGE_DOWNLOAD",
    "PUBLIC_RAW_REDISTRIBUTION",
    "UNRESTRICTED_COMMERCIAL_REDISTRIBUTION",
)

# Finalized governed-evidence authorities emitted by the builder, in contract
# order.  These are semantic policy anchors rather than release-self anchors:
# package-only validation must reconstruct them before a manifest is frozen.
EXPECTED_EVIDENCE_ARTIFACTS = (
    (
        "active_identity",
        "governed-evidence/active-identity/"
        "sha256-0323c940e38303f9757a9fd2f6d69db5de5b8279381513d39ffcc3331fdddc07.json",
        "0323c940e38303f9757a9fd2f6d69db5de5b8279381513d39ffcc3331fdddc07",
        54_046,
    ),
    (
        "final_result",
        "governed-evidence/finalized-recovery/"
        "sha256-08d2772e4009779d1d0c485d3a5a0dfefeaab3363dc39c5a8dd17903bacc1720.json",
        "08d2772e4009779d1d0c485d3a5a0dfefeaab3363dc39c5a8dd17903bacc1720",
        33_302,
    ),
    (
        "quarantine_impact",
        "governed-evidence/quarantine-impact/"
        "sha256-cf238f0b16ba362af551cb4ac63fca19686f34d661647a6e0a4872090e2b1c4b.json",
        "cf238f0b16ba362af551cb4ac63fca19686f34d661647a6e0a4872090e2b1c4b",
        2_140_874,
    ),
)

# Exact limitations emitted in canonical limitation_id order.  Keeping these
# beside the package-only controls lets the builder and independent validator
# share policy without a validator -> builder/runtime dependency.
EXPECTED_LIMITATIONS = (
    (
        "NO_NAMES_OR_FUZZY_MATCHING",
        "Names, normalized names, fuzzy matching, probabilistic matching, and "
        "invented aliases are outside this contract.",
        "IDENTITY_BRIDGE",
    ),
    (
        "PARENT_PHYSICAL_SCOPE_OMITS_ONE_GOVERNED_ROSTER_IDENTITY",
        "The finalized upstream active audit includes one roster-only identity "
        "that is not enumerated in any immutable parent Parquet field or allowed "
        "finalized evidence artifact; the portable bridge therefore contains "
        "2,692 physical-parent players while preserving the governed 2,693 count.",
        "IDENTITY_BRIDGE",
    ),
    (
        "PUBLIC_RAW_DISTRIBUTION_NOT_AUTHORIZED",
        "This package does not authorize public raw bridge distribution or resale.",
        "IDENTITY_BRIDGE",
    ),
    (
        "ROOKIE_MISSING_PRIOR_IS_VALID",
        "An exact identity with no eligible earlier-domain prior is a legitimate "
        "missing-prior case, not an identity failure.",
        "IDENTITY_BRIDGE",
    ),
    (
        "Y_MINUS_1_ONLY",
        "Player-prior evidence is eligible only for target season Y when its "
        "source season is Y-1 or earlier.",
        "IDENTITY_BRIDGE",
    ),
)

# Immutable FD1 historical-release authority, in canonical ``source_key`` order.
# These values are duplicated here intentionally so package-only validation never
# imports the database extractor.
EXPECTED_SOURCE_RELEASES = (
    (
        "CHADWICK_REGISTER_ARCHIVES",
        "4a4956de-0769-4bb0-9dc4-cc30515ba16a",
        "8aa66c044fb49ceb681af16fb4c20bef582f94e56cdcc790f2634d26f1bf36b0",
    ),
    (
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "fef044cc-35e1-499a-8971-a8b29cd9dd7c",
        "47eaa3c9322215f5445c7254f72ea7dac4fa29013ae56308b187a87d75a6881e",
    ),
    (
        "RETROSHEET_CSV_DOWNLOADS",
        "64db602a-090a-4e37-b8f2-2a48d36dd7f2",
        "d952cbf26b3e37be338b12a24da4099a24fbafc19f839edc03631fb1ded42a52",
    ),
    (
        "RETROSHEET_EVENT_ARCHIVES",
        "9a08b5fd-e5ed-4043-a984-04021584f15e",
        "2ee67ae07abfc2abba53011cd22239eb7ed4e8185e10e6338b829413379c85e8",
    ),
    (
        "SABR_LAHMAN_BOX_DOWNLOADS",
        "3af682f0-ecdb-47d8-8e14-660ec5f7455c",
        "61d462de4f4030263ce46a01ee213d211adbd26ff869365ec18b3477063a3cf5",
    ),
)

# Person-alias rows in the frozen bridge are contributed only by the Chadwick
# register (four namespaces) and SABR/Lahman (the Lahman namespace).
EXPECTED_ALIAS_SOURCE_RELEASES = (
    EXPECTED_SOURCE_RELEASES[0],
    EXPECTED_SOURCE_RELEASES[4],
)

__all__ = [
    "ALLOWED_IDENTITY_NAMESPACES",
    "CONTRACT_NAME",
    "CONTRACT_VERSION",
    "EVIDENCE_CUTOFF",
    "EXPECTED_ALIAS_AUTHORITY_SHA256",
    "EXPECTED_ALIAS_SOURCE_RELEASES",
    "EXPECTED_BRIDGE_MANIFEST_SHA256",
    "EXPECTED_DIMENSION_AUTHORITY_SHA256",
    "EXPECTED_EVIDENCE_ARTIFACTS",
    "EXPECTED_LIMITATIONS",
    "EXPECTED_PA_CLASS_ORDER",
    "EXPECTED_SOURCE_RELEASES",
    "EXPECTED_PARENT_DATASET_ID",
    "EXPECTED_PARENT_DATASET_VERSION_ID",
    "EXPECTED_PARENT_MANIFEST_ID",
    "EXPECTED_PARENT_MANIFEST_SHA256",
    "EXPECTED_PARENT_PACKAGE_ID",
    "EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256",
    "FINAL_PACKAGE_FILE_COUNT",
    "PACKAGE_ALLOWED_PURPOSES",
    "PACKAGE_PROHIBITED_PURPOSES",
    "PUBLISHER",
    "REQUIRED_AUXILIARY_FILES",
    "REQUIRED_SEMANTIC_BRIDGE_FILES",
]
