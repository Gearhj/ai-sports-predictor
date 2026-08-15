"""Provider-free Gate 3C-FD1 acquisition-to-registry synchronization.

The acquisition layer and the SQL registry deliberately use different
authorization identities.  Acquisition binds each physical request to the
frozen FD1 source policy; the registry binds a row to an immutable owner and
provider decision.  This module is the only bridge between those identities.
It first proves the original manifest still matches the frozen catalog and
policy, then proves that the source release governance binds that acquisition
identity to the exact provider-decision identity.  Only then does it create a
registry projection of the artifact.

No transport or provider adapter is constructed here.  Dry-run operation reads
only ignored local manifests and their content-addressed blobs.  Live operation
adds append-only rows through :class:`OpenDataRegistry` and leaves transaction
commit control to the caller (the CLI commits explicitly).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.gen2.fd1_pipeline import (
    CHADWICK_REVISION,
    DEFAULT_RAW_ROOT,
    DOWNLOAD_CATALOG,
    FD1_SOURCE_POLICIES,
    FD1DownloadSpec,
)
from app.gen2.open_data_acquisition import (
    DOWNLOAD_MANIFEST_SCHEMA_VERSION,
    ArchiveFormat,
    AttemptAccounting,
    DownloadArtifact,
    DownloadDisposition,
)
from app.gen2.open_data_registry import (
    OpenDataDownloadSpec,
    OpenDataOwnerDecisionSpec,
    OpenDataProviderDecisionSpec,
    OpenDataRegistry,
    OpenDataSourceReleaseSpec,
    build_download_semantic_fingerprint,
    build_owner_decision_identity,
    build_provider_decision_identity,
    canonical_sha256,
)
from app.models import (
    HistoricalSourceVersion,
    OpenDataDownloadManifest,
    ProviderAccessAuthorization,
    SourceOwnerApproval,
)


FD1_REGISTRY_SYNC_SCHEMA_VERSION = "gate3c_fd1_registry_sync_v1"
FD1_AUTHORIZATION_BRIDGE_SCHEMA_VERSION = "gate3c_fd1_authorization_bridge_v1"
FD1_GOVERNANCE_DECIDED_AT = datetime(2026, 8, 14, tzinfo=UTC)
FD1_ACQUIRED_SOURCE_KEYS = frozenset(
    {
        "RETROSHEET_CSV_DOWNLOADS",
        "RETROSHEET_EVENT_ARCHIVES",
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "CHADWICK_REGISTER_ARCHIVES",
        "SABR_LAHMAN_BOX_DOWNLOADS",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_MANIFEST_BYTES = 1024 * 1024
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "bearer",
    "cookie",
    "credential",
    "password",
    "secret",
    "signature",
    "signed_url",
    "token",
)
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "source_key",
        "object_key",
        "sanitized_url",
        "policy_identity_checksum",
        "authorization_identity_checksum",
        "disposition",
        "revision",
        "content_logical_uri",
        "manifest_logical_uri",
        "sha256",
        "content_length",
        "mime_type",
        "archive_format",
        "http_status",
        "started_at",
        "completed_at",
        "physical_attempt_count",
        "transport_bytes_received",
        "attempts",
    }
)
_ATTEMPT_FIELDS = frozenset(
    {
        "attempt_number",
        "started_at",
        "completed_at",
        "requested_range_start",
        "status_code",
        "response_content_length",
        "received_bytes",
        "outcome",
    }
)

_RETROSHEET_ATTRIBUTION = (
    "The information used here was obtained free of\n"
    "charge from and is copyrighted by Retrosheet.  Interested\n"
    'parties may contact Retrosheet at "www.retrosheet.org".'
)
_CHADWICK_ATTRIBUTION = (
    "Contains information from the Chadwick Baseball Bureau Persons Register, "
    "which is made available under the ODC Attribution License."
)
_LAHMAN_ATTRIBUTION = (
    "Includes data from the SABR Lahman Baseball Database 1871\u20132025, created "
    "by Sean Lahman and published by SABR, under CC BY-SA 3.0. ASP "
    "transformations are identified separately."
)

_RETROSHEET_NOTICE_SHA256 = (
    "2218abe680e21d3308de06352c394baefbffc7618d36a821d38ed6e97c24105d"
)
_CHADWICK_README_SHA256 = (
    "74bbe60a72b17ea709ef97fc7f191e717f4d28174cdeccbb9f429e133fb8c434"
)
_LAHMAN_README_SHA256 = (
    "8f3d8f12b638887e4d182561a668e8103e11fbe1483019cae6d12a4c67cc3aab"
)

_LAHMAN_FILE_NAMES = {
    "lahman-2025-batting-csv": "Batting.csv",
    "lahman-2025-battingpost-csv": "BattingPost.csv",
    "lahman-2025-collegeplaying-csv": "CollegePlaying.csv",
    "lahman-2025-fielding-csv": "Fielding.csv",
    "lahman-2025-fieldingpost-csv": "FieldingPost.csv",
    "lahman-2025-halloffame-csv": "HallOfFame.csv",
    "lahman-2025-homegames-csv": "HomeGames.csv",
    "lahman-2025-managers-csv": "Managers.csv",
    "lahman-2025-managershalf-csv": "ManagersHalf.csv",
    "lahman-2025-parks-csv": "Parks.csv",
    "lahman-2025-people-csv": "People.csv",
    "lahman-2025-pitching-csv": "Pitching.csv",
    "lahman-2025-pitchingpost-csv": "PitchingPost.csv",
    "lahman-2025-readme": "readme2025.txt",
    "lahman-2025-salaries-csv": "Salaries.csv",
    "lahman-2025-schools-csv": "Schools.csv",
    "lahman-2025-seriespost-csv": "SeriesPost.csv",
    "lahman-2025-teams-csv": "Teams.csv",
    "lahman-2025-teamsfranchises-csv": "TeamsFranchises.csv",
    "lahman-2025-teamshalf-csv": "TeamsHalf.csv",
}


class FD1RegistrySyncError(RuntimeError):
    """A local manifest or authorization bridge failed closed."""


@dataclass(frozen=True, slots=True)
class FD1SourceDefinition:
    source_key: str
    dataset_version: str
    provider_name: str
    documentation_uri: str
    rights_classification: str
    license_identity: str
    license_uri: str
    attribution_text: str
    terms_reference: str
    terms_checksum: str
    decision_basis: str
    training_eligible: bool
    availability_policy: Mapping[str, Any]
    governance: Mapping[str, Any]

    def owner_spec(self) -> OpenDataOwnerDecisionSpec:
        return OpenDataOwnerDecisionSpec(
            source_key=self.source_key,
            decided_by="project-owner-attached-master-prompt",
            decided_at=FD1_GOVERNANCE_DECIDED_AT,
            authorization_reference=self.terms_reference,
            conditions={
                "gate": "3C-FD1",
                "official_source_only": True,
                "local_private_research": True,
                "required_attribution": True,
                "automated_mlb_savant_acquisition": "PROHIBITED",
            },
        )

    def provider_spec(self) -> OpenDataProviderDecisionSpec:
        return OpenDataProviderDecisionSpec(
            source_key=self.source_key,
            provider_name=self.provider_name,
            access_method="OFFICIAL_HTTPS_DOWNLOAD",
            decided_by="project-owner-attached-master-prompt",
            decided_at=FD1_GOVERNANCE_DECIDED_AT,
            terms_reference=self.terms_reference,
            terms_checksum=self.terms_checksum,
            decision_basis=self.decision_basis,
        )

    @property
    def provider_decision_identity(self) -> str:
        owner = self.owner_spec()
        return build_provider_decision_identity(
            build_owner_decision_identity(owner), self.provider_spec()
        )

    def authorization_bridge(self) -> dict[str, Any]:
        policy = FD1_SOURCE_POLICIES[self.source_key]
        accepted_policy_identities = _accepted_policy_identities(self.source_key)
        entries = _catalog_entries_for_source(self.source_key)
        releases = {item.source_release for item in entries}
        licenses = {item.license_identity for item in entries}
        attributions = {item.attribution_identity for item in entries}
        if releases != {self.dataset_version} or licenses != {self.license_identity}:
            raise FD1RegistrySyncError(
                "Frozen catalog release or license differs from source governance"
            )
        if len(attributions) != 1:
            raise FD1RegistrySyncError(
                "Frozen catalog attribution identity is not source-specific"
            )
        payload = {
            "schema_version": FD1_AUTHORIZATION_BRIDGE_SCHEMA_VERSION,
            "source_key": self.source_key,
            "catalog_source_release": self.dataset_version,
            "catalog_license_identity": self.license_identity,
            "catalog_attribution_identity": next(iter(attributions)),
            "acquisition_policy_identity_checksum": policy.identity_checksum,
            "accepted_acquisition_policy_identity_checksums": (
                list(accepted_policy_identities)
            ),
            "acquisition_authorization_identity_checksum": (
                policy.authorization_identity_checksum
            ),
            "owner_decision_identity_checksum": build_owner_decision_identity(
                self.owner_spec()
            ),
            "provider_decision_identity_checksum": self.provider_decision_identity,
            "projection_rule": (
                "REPLACE_ACQUISITION_AUTH_WITH_EXACT_PROVIDER_DECISION_IDENTITY_"
                "ONLY_AFTER_FROZEN_POLICY_AND_GOVERNANCE_MATCH"
            ),
            "policy_lineage_note": (
                "The SABR source records the exact pre-rm-query policy and its "
                "strictly additive rm-query successor; all other sources have one "
                "accepted policy identity."
            ),
        }
        return {
            **payload,
            "identity_checksum": canonical_sha256(
                {
                    "schema_version": FD1_AUTHORIZATION_BRIDGE_SCHEMA_VERSION,
                    "record_type": "FD1_AUTHORIZATION_BRIDGE",
                    "fields": payload,
                }
            ),
        }

    def release_spec(self) -> OpenDataSourceReleaseSpec:
        return OpenDataSourceReleaseSpec(
            source_key=self.source_key,
            dataset_version=self.dataset_version,
            provider_name=self.provider_name,
            effective_from=FD1_GOVERNANCE_DECIDED_AT,
            policy_version="gate3c-fd1-open-data-v1",
            documentation_uri=self.documentation_uri,
            rights_classification=self.rights_classification,
            license_identity=self.license_identity,
            license_uri=self.license_uri,
            attribution_text=self.attribution_text,
            availability_policy=dict(self.availability_policy),
            source_classification="PUBLIC_SECONDARY",
            quality_tier="A",
            training_eligible=self.training_eligible,
            evaluation_eligible=True,
            governance={
                **dict(self.governance),
                "fd1_authorization_bridge": self.authorization_bridge(),
                "raw_publication": "PROHIBITED_BY_ASP_POLICY",
                "automated_mlb_savant_acquisition": "PROHIBITED",
            },
        )


def _retrosheet_definition(source_key: str) -> FD1SourceDefinition:
    return FD1SourceDefinition(
        source_key=source_key,
        dataset_version="retrosheet_through_2025",
        provider_name="Retrosheet",
        documentation_uri="https://www.retrosheet.org/downloads/csvdownloads.html",
        rights_classification=(
            "FREE_DOWNLOAD_COMMERCIAL_DERIVED_USE_ALLOWED_REQUIRED_ATTRIBUTION"
        ),
        license_identity=(
            "RETROSHEET_COMMERCIAL_DERIVED_USE_ALLOWED_REQUIRED_ATTRIBUTION"
        ),
        license_uri="https://www.retrosheet.org/notice.txt",
        attribution_text=_RETROSHEET_ATTRIBUTION,
        terms_reference="https://www.retrosheet.org/notice.txt",
        terms_checksum=_RETROSHEET_NOTICE_SHA256,
        decision_basis=(
            "The Gate 3C-FD1 owner decision authorizes only Retrosheet's official "
            "HTTPS downloads under its exact required attribution notice."
        ),
        training_eligible=True,
        availability_policy={
            "population": "RETROSPECTIVE_RECONSTRUCTION",
            "current_game_excluded_from_priors": True,
            "actual_lineup_timing_risk": True,
            "source_horizon": "through_2025",
        },
        governance={
            "required_attribution_verbatim": True,
            "source_corrections_expected": True,
            "no_endorsement": True,
        },
    )


FD1_SOURCE_DEFINITIONS: Mapping[str, FD1SourceDefinition] = {
    "RETROSHEET_CSV_DOWNLOADS": _retrosheet_definition(
        "RETROSHEET_CSV_DOWNLOADS"
    ),
    "RETROSHEET_EVENT_ARCHIVES": _retrosheet_definition(
        "RETROSHEET_EVENT_ARCHIVES"
    ),
    "RETROSHEET_AUXILIARY_DOWNLOADS": _retrosheet_definition(
        "RETROSHEET_AUXILIARY_DOWNLOADS"
    ),
    "CHADWICK_REGISTER_ARCHIVES": FD1SourceDefinition(
        source_key="CHADWICK_REGISTER_ARCHIVES",
        dataset_version=CHADWICK_REVISION,
        provider_name="Chadwick Baseball Bureau",
        documentation_uri="https://github.com/chadwickbureau/register",
        rights_classification="ODC_BY_1_0_ATTRIBUTION_REQUIRED",
        license_identity="ODC_BY_1_0",
        license_uri="https://opendatacommons.org/licenses/by/1-0/",
        attribution_text=_CHADWICK_ATTRIBUTION,
        terms_reference="https://github.com/chadwickbureau/register",
        terms_checksum=_CHADWICK_README_SHA256,
        decision_basis=(
            "The official Chadwick Persons Register archive is pinned to one "
            "immutable commit whose README declares ODC-By 1.0."
        ),
        training_eligible=False,
        availability_policy={
            "population": "IDENTITY_SPINE",
            "revision": CHADWICK_REVISION,
            "names_are_primary_identity": False,
        },
        governance={
            "canonical_person_key": "key_uuid",
            "ambiguous_links": "UNRESOLVED",
            "attribution_required": True,
        },
    ),
    "SABR_LAHMAN_BOX_DOWNLOADS": FD1SourceDefinition(
        source_key="SABR_LAHMAN_BOX_DOWNLOADS",
        dataset_version="lahman_1871-2025_csv",
        provider_name="Society for American Baseball Research",
        documentation_uri="https://sabr.org/lahman-database/",
        rights_classification="CC_BY_SA_3_0_ATTRIBUTION_SHAREALIKE_REQUIRED",
        license_identity="CC_BY_SA_3_0",
        license_uri="https://creativecommons.org/licenses/by-sa/3.0/",
        attribution_text=_LAHMAN_ATTRIBUTION,
        terms_reference=(
            "https://sabr.box.com/shared/static/"
            "qtgh1olzcaauz5x234wqx8huixizff8l.txt"
        ),
        terms_checksum=_LAHMAN_README_SHA256,
        decision_basis=(
            "The official SABR 2025 README confirms CC BY-SA 3.0 for the "
            "package; the separately sourced Negro Leagues subset remains isolated."
        ),
        training_eligible=True,
        availability_policy={
            "population": "PRIOR_SEASON_AGGREGATES",
            "same_season_totals_forbidden_before_season_end": True,
            "modern_mlb_scope": True,
        },
        governance={
            "share_alike_review_required_for_public_derivative_database": True,
            "negro_leagues_subset": "ISOLATE_REVIEW_REQUIRED",
            "attribution_required": True,
        },
    ),
}


@dataclass(frozen=True, slots=True)
class FD1RegistrySyncPlan:
    artifacts: tuple[DownloadArtifact, ...]
    content_verified: bool

    @property
    def source_keys(self) -> tuple[str, ...]:
        return tuple(sorted({item.source_key for item in self.artifacts}))

    def summary(self, *, mode: str) -> dict[str, Any]:
        unique = {
            (item.source_key, item.object_key, item.revision): item
            for item in self.artifacts
        }
        source_rows: list[dict[str, Any]] = []
        for source_key in self.source_keys:
            definition = FD1_SOURCE_DEFINITIONS[source_key]
            observations = [
                item for item in self.artifacts if item.source_key == source_key
            ]
            revisions = [
                item for key, item in unique.items() if key[0] == source_key
            ]
            bridge = definition.authorization_bridge()
            source_rows.append(
                {
                    "source_key": source_key,
                    "dataset_version": definition.dataset_version,
                    "manifest_observations": len(observations),
                    "objects": len({item.object_key for item in observations}),
                    "immutable_revisions": len(revisions),
                    "unique_content_bytes": sum(item.content_length for item in revisions),
                    "observed_transport_bytes": sum(
                        item.transport_bytes_received for item in observations
                    ),
                    "acquisition_policy_identity_checksum": bridge[
                        "acquisition_policy_identity_checksum"
                    ],
                    "acquisition_authorization_identity_checksum": bridge[
                        "acquisition_authorization_identity_checksum"
                    ],
                    "provider_decision_identity_checksum": bridge[
                        "provider_decision_identity_checksum"
                    ],
                    "authorization_bridge_identity_checksum": bridge[
                        "identity_checksum"
                    ],
                }
            )
        acquired_objects = {
            (item.source_key, item.object_key) for item in self.artifacts
        }
        catalog_objects = {
            (item.request.source_key, item.request.object_key)
            for item in DOWNLOAD_CATALOG
        }
        return {
            "schema_version": FD1_REGISTRY_SYNC_SCHEMA_VERSION,
            "mode": mode,
            "provider_transport_constructed": False,
            "network_requests": 0,
            "absolute_paths_emitted": False,
            "content_bytes_verified": self.content_verified,
            "manifest_observations": len(self.artifacts),
            "immutable_download_rows": len(unique),
            "source_release_rows": len(self.source_keys),
            "owner_decision_rows": len(self.source_keys),
            "provider_decision_rows": len(self.source_keys),
            "catalog_objects_acquired": len(acquired_objects),
            "catalog_objects_missing": len(catalog_objects - acquired_objects),
            "sources": source_rows,
        }


def _accepted_policy_identities(source_key: str) -> tuple[str, ...]:
    """Return an exact, reconstructed policy lineage for acquired evidence."""

    policy = FD1_SOURCE_POLICIES[source_key]
    identities = {policy.identity_checksum}
    if source_key == "SABR_LAHMAN_BOX_DOWNLOADS":
        # The README was first acquired under the policy that allowed Box's
        # ``file_id`` and ``shared_name`` keys.  Box then required the benign
        # ``rm=box_download_shared_file`` selector for CSV bodies; adding only
        # that exact key changed the content-derived policy identity.  Preserve
        # both policies instead of relabeling the already-written observation.
        predecessor = replace(
            policy, allowed_query_keys=("file_id", "shared_name")
        )
        identities.add(predecessor.identity_checksum)
    return tuple(sorted(identities))


def _catalog_entries_for_source(source_key: str) -> tuple[FD1DownloadSpec, ...]:
    return tuple(
        item for item in DOWNLOAD_CATALOG if item.request.source_key == source_key
    )


def _catalog_index() -> dict[tuple[str, str], FD1DownloadSpec]:
    result: dict[tuple[str, str], FD1DownloadSpec] = {}
    for item in DOWNLOAD_CATALOG:
        key = (item.request.source_key, item.request.object_key)
        if key in result:
            raise FD1RegistrySyncError("Frozen acquisition catalog has a duplicate object")
        result[key] = item
    return result


def _file_name(item: FD1DownloadSpec) -> str:
    source_key = item.request.source_key
    object_key = item.request.object_key
    if source_key == "SABR_LAHMAN_BOX_DOWNLOADS":
        try:
            return _LAHMAN_FILE_NAMES[object_key]
        except KeyError as exc:  # pragma: no cover - catalog import invariant
            raise FD1RegistrySyncError("Unknown frozen SABR Lahman object") from exc
    if source_key == "CHADWICK_REGISTER_ARCHIVES":
        return f"chadwick-register-{CHADWICK_REVISION}.tar.gz"
    name = PurePosixPath(urlsplit(item.request.url).path).name
    if not name:
        raise FD1RegistrySyncError("Frozen catalog object lacks a file name")
    return name


def _download_spec(item: FD1DownloadSpec) -> OpenDataDownloadSpec:
    return OpenDataDownloadSpec(
        source_key=item.request.source_key,
        source_release=item.source_release,
        file_name=_file_name(item),
    )


def _assert_definitions_complete() -> None:
    if set(FD1_SOURCE_DEFINITIONS) != set(FD1_ACQUIRED_SOURCE_KEYS):
        raise FD1RegistrySyncError("FD1 source governance set is not exact")
    catalog_sources = {item.request.source_key for item in DOWNLOAD_CATALOG}
    if catalog_sources != set(FD1_ACQUIRED_SOURCE_KEYS):
        raise FD1RegistrySyncError("FD1 catalog source set differs from governance")
    if set(FD1_SOURCE_POLICIES) != set(FD1_ACQUIRED_SOURCE_KEYS):
        raise FD1RegistrySyncError("FD1 policy source set differs from governance")
    _catalog_index()
    for definition in FD1_SOURCE_DEFINITIONS.values():
        definition.release_spec()


def load_download_artifact(
    manifest_path: Path,
    *,
    raw_root: Path = DEFAULT_RAW_ROOT,
    verify_content: bool = True,
) -> DownloadArtifact:
    """Load one ignored manifest and reconstruct its artifact exactly.

    The physical path is accepted only as an input locator.  All persisted and
    returned evidence remains a relative logical URI, and error messages never
    echo the physical path.
    """

    _assert_definitions_complete()
    root = _safe_root(raw_root)
    path = _safe_file_within_root(manifest_path, root, "Acquisition manifest")
    try:
        size = path.stat().st_size
    except OSError as exc:  # pragma: no cover - raced filesystem failure
        raise FD1RegistrySyncError("Acquisition manifest cannot be inspected") from exc
    if size <= 0 or size > _MAX_MANIFEST_BYTES:
        raise FD1RegistrySyncError("Acquisition manifest size is invalid")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        payload = json.loads(text, object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FD1RegistrySyncError("Acquisition manifest JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise FD1RegistrySyncError("Acquisition manifest must be a JSON object")
    _assert_secret_free(payload)
    artifact = _artifact_from_payload(payload)

    relative_manifest = path.relative_to(root).as_posix()
    if artifact.manifest_logical_uri != relative_manifest:
        raise FD1RegistrySyncError(
            "Manifest logical URI does not identify its local evidence file"
        )
    _validate_artifact_against_catalog(artifact)
    if verify_content:
        _verify_content_file(root, artifact)
    return artifact


def discover_download_artifacts(
    *, raw_root: Path = DEFAULT_RAW_ROOT, verify_content: bool = True
) -> tuple[DownloadArtifact, ...]:
    """Discover and validate every local acquisition observation."""

    _assert_definitions_complete()
    root = _safe_root(raw_root)
    open_data = root / "open-data"
    if not open_data.is_dir() or _is_linklike(open_data):
        raise FD1RegistrySyncError("Ignored open-data root is missing or unsafe")
    paths = sorted(open_data.glob("*/*/manifests/acquisition-*.json"))
    if not paths:
        raise FD1RegistrySyncError("No local acquisition manifests were found")
    artifacts = tuple(
        load_download_artifact(
            path, raw_root=root, verify_content=verify_content
        )
        for path in paths
    )
    ordered = tuple(
        sorted(
            artifacts,
            key=lambda item: (
                item.source_key,
                item.object_key,
                item.revision,
                _aware_datetime(item.completed_at, "completed_at"),
                item.manifest_logical_uri,
            ),
        )
    )
    _validate_observation_history(ordered)
    return ordered


def build_registry_sync_plan(
    *, raw_root: Path = DEFAULT_RAW_ROOT, verify_content: bool = True
) -> FD1RegistrySyncPlan:
    artifacts = discover_download_artifacts(
        raw_root=raw_root, verify_content=verify_content
    )
    for artifact in artifacts:
        definition = FD1_SOURCE_DEFINITIONS[artifact.source_key]
        bridge_artifact_for_registry(
            artifact,
            provider_identity_checksum=definition.provider_decision_identity,
            source_governance=definition.release_spec().governance,
        )
    return FD1RegistrySyncPlan(
        artifacts=artifacts, content_verified=verify_content
    )


def dry_run_registry_sync(
    *, raw_root: Path = DEFAULT_RAW_ROOT, verify_content: bool = True
) -> dict[str, Any]:
    """Validate a complete local plan without opening a database session."""

    return build_registry_sync_plan(
        raw_root=raw_root, verify_content=verify_content
    ).summary(mode="dry-run")


def bridge_artifact_for_registry(
    artifact: DownloadArtifact,
    *,
    provider_identity_checksum: str,
    source_governance: Mapping[str, Any] | None,
) -> DownloadArtifact:
    """Project an acquisition artifact onto its exact registry decision.

    The returned object is intentionally distinct from ``artifact``.  The raw
    artifact keeps the acquisition authorization identity reconstructed from
    disk; the projection carries the provider-decision identity required by
    :meth:`OpenDataRegistry.record_download`.
    """

    _validate_artifact_against_catalog(artifact)
    definition = FD1_SOURCE_DEFINITIONS[artifact.source_key]
    expected_bridge = definition.authorization_bridge()
    if not isinstance(source_governance, Mapping):
        raise FD1RegistrySyncError("Source governance lacks the FD1 bridge")
    observed_bridge = source_governance.get("fd1_authorization_bridge")
    if observed_bridge != expected_bridge:
        raise FD1RegistrySyncError(
            "Source governance authorization bridge differs from the frozen bridge"
        )
    policy = FD1_SOURCE_POLICIES[artifact.source_key]
    if artifact.policy_identity_checksum not in _accepted_policy_identities(
        artifact.source_key
    ):
        raise FD1RegistrySyncError(
            "Artifact policy identity is not in the frozen policy lineage"
        )
    if (
        artifact.authorization_identity_checksum
        != policy.authorization_identity_checksum
        or artifact.authorization_identity_checksum
        != expected_bridge["acquisition_authorization_identity_checksum"]
    ):
        raise FD1RegistrySyncError(
            "Artifact authorization identity is not the frozen acquisition decision"
        )
    if (
        not _SHA256.fullmatch(provider_identity_checksum)
        or provider_identity_checksum
        != expected_bridge["provider_decision_identity_checksum"]
    ):
        raise FD1RegistrySyncError(
            "Provider decision identity is not bound by source governance"
        )
    projected = replace(
        artifact, authorization_identity_checksum=provider_identity_checksum
    )
    item = _catalog_index()[(artifact.source_key, artifact.object_key)]
    # Invoke the registry's full artifact validator before any SQL mutation.
    build_download_semantic_fingerprint(
        source_version_identity_checksum="0" * 64,
        spec=_download_spec(item),
        artifact=projected,
    )
    return projected


def synchronize_registry(
    session: Session,
    *,
    raw_root: Path = DEFAULT_RAW_ROOT,
    verify_content: bool = True,
    plan: FD1RegistrySyncPlan | None = None,
) -> dict[str, Any]:
    """Flush idempotent append-only rows; the caller owns commit/rollback."""

    selected = plan or build_registry_sync_plan(
        raw_root=raw_root, verify_content=verify_content
    )
    if not selected.content_verified:
        raise FD1RegistrySyncError(
            "Live synchronization requires content-byte verification"
        )
    counts_before = _registry_counts(session)
    registry = OpenDataRegistry(session)
    index = _catalog_index()
    for source_key in selected.source_keys:
        definition = FD1_SOURCE_DEFINITIONS[source_key]
        source = registry.ensure_source_release(definition.release_spec())
        owner, provider = registry.ensure_source_decisions(
            definition.owner_spec(), definition.provider_spec()
        )
        if provider.identity_checksum != definition.provider_decision_identity:
            raise FD1RegistrySyncError(
                "Persisted provider identity differs from the frozen decision"
            )
        # One SQL row represents one immutable upstream revision.  Re-fetch
        # observations remain preserved in their append-only raw manifests,
        # but must not be replayed as new revision rows.  In particular, a
        # later unchanged-byte observation may have been acquired under a
        # strictly additive transport-policy revision; the creation
        # observation is the canonical evidence for the immutable SQL row.
        for artifact in (
            item
            for item in selected.artifacts
            if item.source_key == source_key
            and item.disposition is DownloadDisposition.CREATED_REVISION
        ):
            catalog_item = index[(artifact.source_key, artifact.object_key)]
            projected = bridge_artifact_for_registry(
                artifact,
                provider_identity_checksum=provider.identity_checksum,
                source_governance=source.governance,
            )
            registry.record_download(
                source, provider, projected, _download_spec(catalog_item)
            )
    session.flush()
    counts_after = _registry_counts(session)
    summary = selected.summary(mode="live")
    summary["database"] = {
        "transaction_committed_by_function": False,
        "created_rows": {
            key: counts_after[key] - counts_before[key] for key in counts_before
        },
        "table_rows_after_flush": counts_after,
    }
    return summary


def _registry_counts(session: Session) -> dict[str, int]:
    return {
        "historical_source_versions": int(
            session.scalar(select(func.count()).select_from(HistoricalSourceVersion)) or 0
        ),
        "source_owner_approvals": int(
            session.scalar(select(func.count()).select_from(SourceOwnerApproval)) or 0
        ),
        "provider_access_authorizations": int(
            session.scalar(
                select(func.count()).select_from(ProviderAccessAuthorization)
            )
            or 0
        ),
        "open_data_download_manifests": int(
            session.scalar(select(func.count()).select_from(OpenDataDownloadManifest))
            or 0
        ),
    }


def _artifact_from_payload(payload: Mapping[str, Any]) -> DownloadArtifact:
    _exact_fields(payload, _MANIFEST_FIELDS, "Acquisition manifest")
    attempts_raw = payload.get("attempts")
    if not isinstance(attempts_raw, list) or not attempts_raw:
        raise FD1RegistrySyncError("Manifest attempts must be a nonempty array")
    attempts: list[AttemptAccounting] = []
    for value in attempts_raw:
        if not isinstance(value, Mapping):
            raise FD1RegistrySyncError("Manifest attempt must be an object")
        _exact_fields(value, _ATTEMPT_FIELDS, "Manifest attempt")
        status = value["status_code"]
        response_length = value["response_content_length"]
        attempts.append(
            AttemptAccounting(
                attempt_number=_strict_int(value, "attempt_number"),
                started_at=_strict_text(value, "started_at"),
                completed_at=_strict_text(value, "completed_at"),
                requested_range_start=_strict_int(value, "requested_range_start"),
                status_code=(
                    None if status is None else _strict_int(value, "status_code")
                ),
                response_content_length=(
                    None
                    if response_length is None
                    else _strict_int(value, "response_content_length")
                ),
                received_bytes=_strict_int(value, "received_bytes"),
                outcome=_strict_text(value, "outcome"),
            )
        )
    try:
        artifact = DownloadArtifact(
            schema_version=_strict_text(payload, "schema_version"),
            source_key=_strict_text(payload, "source_key"),
            object_key=_strict_text(payload, "object_key"),
            sanitized_url=_strict_text(payload, "sanitized_url"),
            policy_identity_checksum=_strict_text(
                payload, "policy_identity_checksum"
            ),
            authorization_identity_checksum=_strict_text(
                payload, "authorization_identity_checksum"
            ),
            disposition=DownloadDisposition(_strict_text(payload, "disposition")),
            revision=_strict_int(payload, "revision"),
            content_logical_uri=_strict_text(payload, "content_logical_uri"),
            manifest_logical_uri=_strict_text(payload, "manifest_logical_uri"),
            sha256=_strict_text(payload, "sha256"),
            content_length=_strict_int(payload, "content_length"),
            mime_type=_strict_text(payload, "mime_type"),
            archive_format=ArchiveFormat(_strict_text(payload, "archive_format")),
            http_status=_strict_int(payload, "http_status"),
            started_at=_strict_text(payload, "started_at"),
            completed_at=_strict_text(payload, "completed_at"),
            physical_attempt_count=_strict_int(payload, "physical_attempt_count"),
            transport_bytes_received=_strict_int(
                payload, "transport_bytes_received"
            ),
            attempts=tuple(attempts),
        )
    except (TypeError, ValueError) as exc:
        raise FD1RegistrySyncError("Acquisition manifest artifact is invalid") from exc
    if artifact.manifest() != dict(payload):
        raise FD1RegistrySyncError(
            "Acquisition manifest does not round-trip to an exact artifact"
        )
    return artifact


def _validate_artifact_against_catalog(artifact: DownloadArtifact) -> None:
    if artifact.source_key not in FD1_ACQUIRED_SOURCE_KEYS:
        raise FD1RegistrySyncError("Manifest source is not an acquired FD1 source")
    item = _catalog_index().get((artifact.source_key, artifact.object_key))
    if item is None:
        raise FD1RegistrySyncError("Manifest object is absent from the frozen catalog")
    request = item.request
    policy = FD1_SOURCE_POLICIES[artifact.source_key]
    expected_url = urlsplit(request.url)._replace(query="", fragment="").geturl()
    if artifact.sanitized_url != expected_url or urlsplit(artifact.sanitized_url).query:
        raise FD1RegistrySyncError("Manifest URL differs from the frozen official URL")
    if (
        artifact.policy_identity_checksum
        not in _accepted_policy_identities(artifact.source_key)
        or artifact.authorization_identity_checksum
        != policy.authorization_identity_checksum
    ):
        raise FD1RegistrySyncError(
            "Manifest authorization does not match the frozen FD1 source policy"
        )
    if artifact.archive_format is not request.archive_format:
        raise FD1RegistrySyncError("Manifest archive format differs from the catalog")
    if artifact.mime_type not in policy.allowed_mime_types:
        raise FD1RegistrySyncError("Manifest MIME type is outside the frozen policy")
    if request.expected_mime_type and artifact.mime_type != request.expected_mime_type:
        raise FD1RegistrySyncError("Manifest MIME type differs from catalog evidence")
    if request.expected_sha256 and artifact.sha256 != request.expected_sha256:
        raise FD1RegistrySyncError("Manifest checksum differs from catalog evidence")
    if (
        request.expected_content_length is not None
        and artifact.content_length != request.expected_content_length
    ):
        raise FD1RegistrySyncError("Manifest length differs from catalog evidence")
    expected_prefix = (
        f"open-data/{artifact.source_key.lower().replace('_', '-')}/"
        f"{artifact.object_key}"
    )
    expected_content = (
        f"{expected_prefix}/revisions/"
        f"r{artifact.revision:06d}-{artifact.sha256}.blob"
    )
    if artifact.content_logical_uri != expected_content:
        raise FD1RegistrySyncError("Manifest content URI is not content-addressed")
    manifest_prefix = f"{expected_prefix}/manifests/acquisition-"
    if (
        not artifact.manifest_logical_uri.startswith(manifest_prefix)
        or not artifact.manifest_logical_uri.endswith(".json")
    ):
        raise FD1RegistrySyncError("Manifest evidence URI is not canonical")


def _validate_observation_history(artifacts: Sequence[DownloadArtifact]) -> None:
    grouped: dict[tuple[str, str, int], list[DownloadArtifact]] = defaultdict(list)
    for artifact in artifacts:
        grouped[(artifact.source_key, artifact.object_key, artifact.revision)].append(
            artifact
        )
    for observations in grouped.values():
        created = [
            item
            for item in observations
            if item.disposition is DownloadDisposition.CREATED_REVISION
        ]
        if len(created) != 1:
            raise FD1RegistrySyncError(
                "Each immutable revision needs exactly one creation observation"
            )
        if observations[0].disposition is not DownloadDisposition.CREATED_REVISION:
            raise FD1RegistrySyncError(
                "Revision reuse observation predates its creation observation"
            )
        identity = (
            created[0].sha256,
            created[0].content_length,
            created[0].mime_type,
            created[0].archive_format,
        )
        for item in observations:
            if (item.sha256, item.content_length, item.mime_type, item.archive_format) != identity:
                raise FD1RegistrySyncError(
                    "One immutable revision has conflicting content evidence"
                )


def _verify_content_file(root: Path, artifact: DownloadArtifact) -> None:
    logical = _safe_logical_uri(artifact.content_logical_uri, "content logical URI")
    content = _safe_file_within_root(
        root.joinpath(*logical.parts), root, "Content-addressed blob"
    )
    try:
        size = content.stat().st_size
    except OSError as exc:  # pragma: no cover - raced filesystem failure
        raise FD1RegistrySyncError("Content-addressed blob cannot be inspected") from exc
    if size != artifact.content_length:
        raise FD1RegistrySyncError("Content-addressed blob length differs from manifest")
    digest = hashlib.sha256()
    try:
        with content.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise FD1RegistrySyncError("Content-addressed blob cannot be read") from exc
    if digest.hexdigest() != artifact.sha256:
        raise FD1RegistrySyncError("Content-addressed blob checksum differs from manifest")


def _safe_root(raw_root: Path) -> Path:
    lexical = Path(os.path.abspath(raw_root))
    if _is_linklike(lexical):
        raise FD1RegistrySyncError("Raw evidence root is missing or unsafe")
    try:
        root = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FD1RegistrySyncError("Raw evidence root is missing or unsafe") from exc
    if not root.is_dir() or _is_linklike(root):
        raise FD1RegistrySyncError("Raw evidence root is missing or unsafe")
    return root


def _safe_file_within_root(path: Path, root: Path, label: str) -> Path:
    try:
        lexical = Path(os.path.abspath(path))
        lexical.relative_to(root)
        current = lexical
        while current != root:
            if _is_linklike(current):
                raise FD1RegistrySyncError(
                    f"{label} traverses a link or reparse point"
                )
            current = current.parent
        candidate = lexical.resolve(strict=True)
        candidate.relative_to(root)
    except FD1RegistrySyncError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise FD1RegistrySyncError(f"{label} is missing or outside the raw root") from exc
    if not candidate.is_file():
        raise FD1RegistrySyncError(f"{label} is not a regular file")
    return candidate


def _is_linklike(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(
        getattr(details, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _safe_logical_uri(value: str, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise FD1RegistrySyncError(f"{label} is missing")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or "\\" in value
        or "://" in value
        or "?" in value
        or "#" in value
        or any(part in {"", ".", ".."} for part in pure.parts)
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise FD1RegistrySyncError(f"{label} must be a relative logical URI")
    return pure


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FD1RegistrySyncError("Acquisition manifest contains a duplicate key")
        result[key] = value
    return result


def _assert_secret_free(value: object, path: str = "manifest") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise FD1RegistrySyncError("Manifest contains a non-text field name")
            lowered = key.lower()
            if any(marker in lowered for marker in _SECRET_MARKERS) and not lowered.endswith(
                ("_identity_checksum", "_checksum")
            ):
                raise FD1RegistrySyncError(f"Secret-like manifest field at {path}")
            _assert_secret_free(item, path)
    elif isinstance(value, list):
        for item in value:
            _assert_secret_free(item, path)
    elif isinstance(value, str):
        lowered = value.lower()
        if "?" in value or any(f"{marker}=" in lowered for marker in _SECRET_MARKERS):
            raise FD1RegistrySyncError(f"Secret-like manifest value at {path}")


def _exact_fields(
    value: Mapping[str, Any], expected: frozenset[str], label: str
) -> None:
    if set(value) != set(expected):
        raise FD1RegistrySyncError(f"{label} fields do not match the frozen schema")


def _strict_text(value: Mapping[str, Any], key: str) -> str:
    selected = value.get(key)
    if not isinstance(selected, str) or not selected:
        raise FD1RegistrySyncError(f"Manifest field {key} must be nonempty text")
    return selected


def _strict_int(value: Mapping[str, Any], key: str) -> int:
    selected = value.get(key)
    if not isinstance(selected, int) or isinstance(selected, bool):
        raise FD1RegistrySyncError(f"Manifest field {key} must be an integer")
    return selected


def _aware_datetime(value: str, label: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except (TypeError, ValueError) as exc:
        raise FD1RegistrySyncError(f"Manifest {label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise FD1RegistrySyncError(f"Manifest {label} must be timezone-aware")
    return parsed.astimezone(UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("dry-run", "sync"):
        child = subparsers.add_parser(command)
        child.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    plan = build_registry_sync_plan(raw_root=args.raw_root, verify_content=True)
    if args.command == "dry-run":
        payload = plan.summary(mode="dry-run")
    else:
        # Importing the configured session is intentionally deferred until the
        # explicit live command, so dry-run cannot open a database connection.
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            try:
                payload = synchronize_registry(session, plan=plan)
                session.commit()
                payload["database"]["transaction_committed_by_cli"] = True
            except Exception:
                session.rollback()
                raise
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


_assert_definitions_complete()


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())


__all__ = [
    "FD1_ACQUIRED_SOURCE_KEYS",
    "FD1_AUTHORIZATION_BRIDGE_SCHEMA_VERSION",
    "FD1_REGISTRY_SYNC_SCHEMA_VERSION",
    "FD1_SOURCE_DEFINITIONS",
    "FD1RegistrySyncError",
    "FD1RegistrySyncPlan",
    "bridge_artifact_for_registry",
    "build_registry_sync_plan",
    "discover_download_artifacts",
    "dry_run_registry_sync",
    "load_download_artifact",
    "main",
    "synchronize_registry",
]
