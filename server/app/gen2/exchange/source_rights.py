"""Deterministic Gate 3C-X1 source-rights and attribution contract.

The exchange package records the already-reviewed Gate 3C-FD1 evidence.  It
does not contact a provider, discover local files, attach a clock time, or make
a new legal conclusion.  In particular, upstream permissions are not treated
as authorization for ASP to publish source data or an unreviewed derivative.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


SOURCE_RIGHTS_SCHEMA_VERSION = "SPORTS_DATASET_SOURCE_RIGHTS_V1"
SOURCE_RIGHTS_POLICY_VERSION = "gate3c-fd1-open-data-v1"

LOCAL_PRIVATE_MODEL_DEVELOPMENT = "LOCAL_PRIVATE_MODEL_DEVELOPMENT"
INTERNAL_DERIVED_ANALYTICS = "INTERNAL_DERIVED_ANALYTICS"
READ_ONLY_REGISTRATION = "READ_ONLY_REGISTRATION"
PACKAGE_ALLOWED_PURPOSES = (
    INTERNAL_DERIVED_ANALYTICS,
    LOCAL_PRIVATE_MODEL_DEVELOPMENT,
    READ_ONLY_REGISTRATION,
)

PUBLIC_BULK_DOWNLOAD = "PUBLIC_BULK_DOWNLOAD"
PUBLIC_RAW_REDISTRIBUTION = "PUBLIC_RAW_REDISTRIBUTION"
RAW_DATA_RESALE = "RAW_DATA_RESALE"
UNREVIEWED_COMMERCIAL_REDISTRIBUTION = (
    "UNREVIEWED_COMMERCIAL_REDISTRIBUTION"
)
PACKAGE_PROHIBITED_PURPOSES = (
    PUBLIC_BULK_DOWNLOAD,
    PUBLIC_RAW_REDISTRIBUTION,
    RAW_DATA_RESALE,
    UNREVIEWED_COMMERCIAL_REDISTRIBUTION,
)

RETROSHEET_ATTRIBUTION = (
    "The information used here was obtained free of\n"
    "charge from and is copyrighted by Retrosheet.  Interested\n"
    'parties may contact Retrosheet at "www.retrosheet.org".'
)
CHADWICK_ATTRIBUTION = (
    "Contains information from the Chadwick Baseball Bureau Persons Register, "
    "which is made available under the ODC Attribution License."
)
LAHMAN_ATTRIBUTION = (
    "Includes data from the SABR Lahman Baseball Database 1871–2025, created "
    "by Sean Lahman and published by SABR, under CC BY-SA 3.0. ASP "
    "transformations are identified separately."
)
NOAA_LCDV2_CITATION_TEMPLATE = (
    "Kantor, D.; Casey, N. W.; Menne, M. J.; Buddenberg, A. (2023). "
    "Local Climatological Data (LCD), Version 2, [station/year subset]. "
    "NOAA NCEI. https://doi.org/10.25921/jp3d-3v19. Accessed [date]."
)
OPEN_METEO_ATTRIBUTION = (
    "Weather data by Open-Meteo.com under CC BY 4.0; model/reanalysis source "
    "and ASP transformations are identified with the result."
)

SOURCE_RIGHTS_RECORD_ORDER = (
    "CHADWICK_PERSONS_REGISTER",
    "LAHMAN_SEAMHEADS_NEGRO_LEAGUES_EXCLUDED",
    "NOAA_NCEI_LCDV2_ZERO_USE",
    "OPEN_METEO_FREE_HOSTED_ZERO_USE",
    "RETROSHEET",
    "SABR_LAHMAN_MODERN_MLB",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _release(
    *,
    source_key: str,
    source_version_id: str,
    dataset_version: str,
    source_release_identity_checksum: str,
    registry_attribution_identity_checksum: str,
    owner_decision_identity_checksum: str,
    provider_decision_identity_checksum: str,
    authorization_bridge_identity_checksum: str,
) -> dict[str, str]:
    return {
        "source_key": source_key,
        "source_version_id": source_version_id,
        "dataset_version": dataset_version,
        "source_release_identity_checksum": source_release_identity_checksum,
        "registry_attribution_identity_checksum": (
            registry_attribution_identity_checksum
        ),
        "owner_decision_identity_checksum": owner_decision_identity_checksum,
        "provider_decision_identity_checksum": provider_decision_identity_checksum,
        "authorization_bridge_identity_checksum": (
            authorization_bridge_identity_checksum
        ),
    }


_RETROSHEET_RELEASES = (
    _release(
        source_key="RETROSHEET_CSV_DOWNLOADS",
        source_version_id="64db602a-090a-4e37-b8f2-2a48d36dd7f2",
        dataset_version="retrosheet_through_2025",
        source_release_identity_checksum=(
            "d952cbf26b3e37be338b12a24da4099a24fbafc19f839edc03631fb1ded42a52"
        ),
        registry_attribution_identity_checksum=(
            "6917adab6cc4dddf22aef2ee6d7998a63ace46acab80d3870ebe9705c6f2337a"
        ),
        owner_decision_identity_checksum=(
            "58e95b48d297e6279a81b4c36141b7abb31a328bdd02ad2f58be2e4344490551"
        ),
        provider_decision_identity_checksum=(
            "158211185c10bdd9c96dba996cdeba673ce5b730a6de1b29f9383cc80e23343b"
        ),
        authorization_bridge_identity_checksum=(
            "b316d677104abd1e9f73d166e74391322a0274df2628288d895a88c247581ba9"
        ),
    ),
    _release(
        source_key="RETROSHEET_EVENT_ARCHIVES",
        source_version_id="9a08b5fd-e5ed-4043-a984-04021584f15e",
        dataset_version="retrosheet_through_2025",
        source_release_identity_checksum=(
            "2ee67ae07abfc2abba53011cd22239eb7ed4e8185e10e6338b829413379c85e8"
        ),
        registry_attribution_identity_checksum=(
            "231b9e62d83ea85c8d287f42e5a5db68d50a45d86cc999486e6c2962378f6e90"
        ),
        owner_decision_identity_checksum=(
            "64df4a62bc30067536703873bbfd3ceab9b757c69f61a822acfeb143ee66acfb"
        ),
        provider_decision_identity_checksum=(
            "ba7535df6f5473f23ee5abb52b3cf642bf32a642655d74f9d37de876faa87cb0"
        ),
        authorization_bridge_identity_checksum=(
            "025d86e4c2053fdf8e93cc3327701907ed23f19b8b13b73e40f865870d54088f"
        ),
    ),
    _release(
        source_key="RETROSHEET_AUXILIARY_DOWNLOADS",
        source_version_id="fef044cc-35e1-499a-8971-a8b29cd9dd7c",
        dataset_version="retrosheet_through_2025",
        source_release_identity_checksum=(
            "47eaa3c9322215f5445c7254f72ea7dac4fa29013ae56308b187a87d75a6881e"
        ),
        registry_attribution_identity_checksum=(
            "465134f2d940ebd0f10768a4829199d354facac9ffc3575daae71f0135d3ccfc"
        ),
        owner_decision_identity_checksum=(
            "62a1236378d30b273a8d8ccd2b0bcefb4f0d4cf5c8bf7ce0927e456b344ba11a"
        ),
        provider_decision_identity_checksum=(
            "eda75db40d6e69c3a81cba9b42f1302cda7a25455b397f60c2d5b8e17b8b77b6"
        ),
        authorization_bridge_identity_checksum=(
            "3b45ddfe6763e9e899ca565ce8be18b722d7d6219632e25540a3bd2520c6d141"
        ),
    ),
)

_CHADWICK_RELEASE = _release(
    source_key="CHADWICK_REGISTER_ARCHIVES",
    source_version_id="4a4956de-0769-4bb0-9dc4-cc30515ba16a",
    dataset_version="2e8e73355f9c77b963115377bd98c784cfeec10f",
    source_release_identity_checksum=(
        "8aa66c044fb49ceb681af16fb4c20bef582f94e56cdcc790f2634d26f1bf36b0"
    ),
    registry_attribution_identity_checksum=(
        "b7218f3f0f2b026aed1ed2e9b279dc7c3973c32c4cefec6daf438272dba62b93"
    ),
    owner_decision_identity_checksum=(
        "2b41506e3b6a9dd7e5b1668a075e57d22e7699d3f0e53a343064729de9e4b305"
    ),
    provider_decision_identity_checksum=(
        "dd2a16d63c0e05946502b61c7eb03cc3d43c5f5fd65bb766df56db7461bc3e6e"
    ),
    authorization_bridge_identity_checksum=(
        "7cb002a16346c442c4d0a0ef57e47089fee02feac413b797eefc9f755f74117f"
    ),
)

_LAHMAN_RELEASE = _release(
    source_key="SABR_LAHMAN_BOX_DOWNLOADS",
    source_version_id="3af682f0-ecdb-47d8-8e14-660ec5f7455c",
    dataset_version="lahman_1871-2025_csv",
    source_release_identity_checksum=(
        "61d462de4f4030263ce46a01ee213d211adbd26ff869365ec18b3477063a3cf5"
    ),
    registry_attribution_identity_checksum=(
        "8ecb39c4f06602a93a15fc40abf546fce586e954b723cf3eee2d7ee19f0a7f40"
    ),
    owner_decision_identity_checksum=(
        "5c3cba831775a287151c07e17c78151f59624299d7bf2146054899ae67423110"
    ),
    provider_decision_identity_checksum=(
        "ad457a63b92899e406ce3e62c49bd1bb04c82527bc0d2bd9391dd674d1f33379"
    ),
    authorization_bridge_identity_checksum=(
        "1b0edc2d0f87073c0fd08aa60c9e327045fd775d50082ac366f0da8731adec50"
    ),
)


def _controls(*, attribution_if_used: bool, separate_review: bool = False) -> dict[str, Any]:
    return {
        "attribution_required_if_used": attribution_if_used,
        "no_endorsement": True,
        "public_redistribution_authorized_by_package": False,
        "raw_redistribution_authorized_by_package": False,
        "unreviewed_commercial_redistribution_authorized_by_package": False,
        "separate_rights_review_required": separate_review,
        "transformation_disclosure_required_if_used": attribution_if_used,
    }


def _records() -> list[dict[str, Any]]:
    records = [
        {
            "record_key": "RETROSHEET",
            "rights_control_id": "RETROSHEET",
            "source_name": "Retrosheet",
            "display_name": "Retrosheet official downloads",
            "source_releases": [dict(item) for item in _RETROSHEET_RELEASES],
            "rights_classification": (
                "FREE_DOWNLOAD_COMMERCIAL_DERIVED_USE_ALLOWED_REQUIRED_ATTRIBUTION"
            ),
            "inventory_classification": (
                "FREE_DOWNLOAD / "
                "COMMERCIAL_DERIVED_USE_ALLOWED_WITH_REQUIRED_ATTRIBUTION"
            ),
            "license_identity": (
                "RETROSHEET_COMMERCIAL_DERIVED_USE_ALLOWED_REQUIRED_ATTRIBUTION"
            ),
            "license_uri": "https://www.retrosheet.org/notice.txt",
            "terms_uri": "https://www.retrosheet.org/notice.txt",
            "terms_checksum": (
                "2218abe680e21d3308de06352c394baefbffc7618d36a821d38ed6e97c24105d"
            ),
            "acquisition_attribution_identity_checksum": (
                "48d8423f91d6dc91dd8724af7d1b07c2f6a435d190c869e532efbbd4de8e335c"
            ),
            "attribution_text": RETROSHEET_ATTRIBUTION,
            "required_attribution_text": RETROSHEET_ATTRIBUTION,
            "fd1_use": {
                "status": "USED",
                "manifest_artifact_count": 13,
                "scope": "PRIMARY_EVENTS_AND_GOVERNANCE_SUPPORT",
            },
            "allowed_purposes": list(PACKAGE_ALLOWED_PURPOSES),
            "controls": _controls(attribution_if_used=True, separate_review=True),
            "review_note": (
                "Canonical website-address notice is frozen. The applicable "
                "download-page postal variant must be captured and reviewed before "
                "any public Retrosheet-derived output."
            ),
        },
        {
            "record_key": "CHADWICK_PERSONS_REGISTER",
            "rights_control_id": "CHADWICK_PERSONS_REGISTER",
            "source_name": "Chadwick Baseball Bureau Persons Register",
            "display_name": "Chadwick Baseball Bureau Persons Register",
            "source_releases": [dict(_CHADWICK_RELEASE)],
            "rights_classification": "ODC_BY_1_0_ATTRIBUTION_REQUIRED",
            "inventory_classification": "ODC_BY_1_0 / ATTRIBUTION_REQUIRED",
            "license_identity": "ODC_BY_1_0",
            "license_uri": "https://opendatacommons.org/licenses/by/1-0/",
            "terms_uri": "https://github.com/chadwickbureau/register",
            "terms_checksum": (
                "74bbe60a72b17ea709ef97fc7f191e717f4d28174cdeccbb9f429e133fb8c434"
            ),
            "acquisition_attribution_identity_checksum": (
                "8ca3359c957295806c4777b42ce4c3aa2a40e3810a865c99d341fc219d266a23"
            ),
            "attribution_text": CHADWICK_ATTRIBUTION,
            "required_attribution_text": CHADWICK_ATTRIBUTION,
            "fd1_use": {
                "status": "USED",
                "manifest_artifact_count": 1,
                "scope": "IDENTITY_SPINE",
            },
            "allowed_purposes": list(PACKAGE_ALLOWED_PURPOSES),
            "controls": _controls(attribution_if_used=True),
            "review_note": (
                "Identity evidence is pinned to the recorded commit; names are not "
                "primary keys and transformations must be disclosed."
            ),
        },
        {
            "record_key": "SABR_LAHMAN_MODERN_MLB",
            "rights_control_id": "SABR_LAHMAN_MODERN_MLB",
            "source_name": "SABR Lahman Baseball Database",
            "display_name": "SABR Lahman Baseball Database modern MLB population",
            "source_releases": [dict(_LAHMAN_RELEASE)],
            "rights_classification": (
                "CC_BY_SA_3_0_ATTRIBUTION_SHAREALIKE_REQUIRED"
            ),
            "inventory_classification": (
                "CC_BY_SA_3_0 / ATTRIBUTION_AND_SHAREALIKE_REQUIRED"
            ),
            "license_identity": "CC_BY_SA_3_0",
            "license_uri": "https://creativecommons.org/licenses/by-sa/3.0/",
            "terms_uri": (
                "https://sabr.box.com/shared/static/"
                "qtgh1olzcaauz5x234wqx8huixizff8l.txt"
            ),
            "terms_checksum": (
                "8f3d8f12b638887e4d182561a668e8103e11fbe1483019cae6d12a4c67cc3aab"
            ),
            "acquisition_attribution_identity_checksum": (
                "9216bb236811ed64773b04281d3e7cc63d63c82e61adbdb04076c0a28f6b66f5"
            ),
            "attribution_text": LAHMAN_ATTRIBUTION,
            "required_attribution_text": LAHMAN_ATTRIBUTION,
            "fd1_use": {
                "status": "USED_MODERN_MLB_ONLY",
                "manifest_artifact_count": 8,
                "scope": "CHRONOLOGICAL_PLAYER_PRIORS_AND_IDENTITY_SUPPORT",
            },
            "allowed_purposes": list(PACKAGE_ALLOWED_PURPOSES),
            "controls": _controls(attribution_if_used=True),
            "review_note": (
                "Public conveyance of a modified or derivative database requires "
                "separate distribution-design review of share-alike obligations."
            ),
        },
        {
            "record_key": "LAHMAN_SEAMHEADS_NEGRO_LEAGUES_EXCLUDED",
            "rights_control_id": "LAHMAN_SEAMHEADS_NEGRO_LEAGUES_EXCLUDED",
            "source_name": "Lahman/Seamheads excluded population",
            "display_name": "Lahman Negro Leagues population sourced from Seamheads",
            "source_releases": [dict(_LAHMAN_RELEASE)],
            "rights_classification": "SEPARATE_UPSTREAM_SEAMHEADS_RIGHTS",
            "inventory_classification": "SEPARATE_UPSTREAM_SEAMHEADS_RIGHTS",
            "license_identity": None,
            "license_uri": None,
            "terms_uri": "https://sabr.org/lahman-database/",
            "terms_checksum": None,
            "acquisition_attribution_identity_checksum": None,
            "attribution_text": None,
            "required_attribution_text": None,
            "fd1_use": {
                "status": "EXCLUDED_NOT_USED",
                "manifest_artifact_count": 0,
                "scope": "ISOLATED_POPULATION_RECORD_ONLY",
            },
            "allowed_purposes": [READ_ONLY_REGISTRATION],
            "controls": _controls(attribution_if_used=False, separate_review=True),
            "review_note": (
                "Package-level Lahman licensing does not establish separate rights "
                "for the embedded Seamheads population. It is excluded from the "
                "2021-2025 MLB target and blocked pending independent review."
            ),
        },
        {
            "record_key": "NOAA_NCEI_LCDV2_ZERO_USE",
            "rights_control_id": "NOAA_NCEI_LCDV2_ZERO_USE",
            "source_name": "NOAA/NCEI LCDv2",
            "display_name": "NOAA/NCEI Local Climatological Data, Version 2",
            "source_releases": [],
            "source_identifiers": [
                "NCEI DSI 2075_02",
                "gov.noaa.ncdc:C01689",
                "local-climatological-data-v2",
            ],
            "rights_classification": (
                "FREE_GOVERNMENT / DATASET_SPECIFIC_CITATION"
            ),
            "inventory_classification": (
                "FREE_GOVERNMENT / DATASET_SPECIFIC_CITATION"
            ),
            "license_identity": "NOAA_LCDV2_DATASET_SPECIFIC_CITATION",
            "license_uri": (
                "https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso"
                "?id=gov.noaa.ncdc%3AC01689"
            ),
            "terms_uri": (
                "https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso"
                "?id=gov.noaa.ncdc%3AC01689"
            ),
            "terms_checksum": None,
            "acquisition_attribution_identity_checksum": None,
            "attribution_text": NOAA_LCDV2_CITATION_TEMPLATE,
            "required_attribution_text": NOAA_LCDV2_CITATION_TEMPLATE,
            "fd1_use": {
                "status": "ZERO_USE_NO_DOWNLOAD",
                "manifest_artifact_count": 0,
                "scope": "RETROSHEET_WEATHER_GAP_FALLBACK_NOT_ACTIVATED",
            },
            "allowed_purposes": [READ_ONLY_REGISTRATION],
            "controls": _controls(attribution_if_used=True, separate_review=True),
            "review_note": (
                "No NOAA request, file, or row entered FD1. Citation and row-level "
                "station/file lineage become active only after a separately governed "
                "future use."
            ),
        },
        {
            "record_key": "OPEN_METEO_FREE_HOSTED_ZERO_USE",
            "rights_control_id": "OPEN_METEO_FREE_HOSTED_ZERO_USE",
            "source_name": "Open-Meteo",
            "display_name": "Open-Meteo free hosted Historical Weather API",
            "source_releases": [],
            "source_identifiers": ["Open-Meteo Historical Weather API"],
            "rights_classification": (
                "FREE_HOSTED_API_NONCOMMERCIAL_ONLY / CC_BY_4_0_DATA"
            ),
            "inventory_classification": (
                "FREE_HOSTED_API_NONCOMMERCIAL_ONLY / CC_BY_4_0_DATA"
            ),
            "license_identity": "CC_BY_4_0_DATA_WITH_FREE_HOSTED_NONCOMMERCIAL_BOUNDARY",
            "license_uri": "https://creativecommons.org/licenses/by/4.0/",
            "terms_uri": "https://open-meteo.com/en/terms",
            "terms_checksum": None,
            "acquisition_attribution_identity_checksum": None,
            "attribution_text": OPEN_METEO_ATTRIBUTION,
            "required_attribution_text": OPEN_METEO_ATTRIBUTION,
            "fd1_use": {
                "status": "ZERO_USE_NO_REQUEST",
                "manifest_artifact_count": 0,
                "scope": "LOCAL_NONCOMMERCIAL_FALLBACK_NOT_ACTIVATED",
            },
            "allowed_purposes": [READ_ONLY_REGISTRATION],
            "controls": _controls(attribution_if_used=True, separate_review=True),
            "review_note": (
                "No Open-Meteo evidence entered FD1. The CC BY 4.0 data license "
                "does not override the free hosted tier's noncommercial restriction; "
                "commercial use requires a new immutable source-use decision."
            ),
        },
    ]
    return sorted(records, key=lambda record: record["rights_control_id"])


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _checksum(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value


def _validate_release(value: object, *, record_key: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{record_key}: source release must be an object")
    expected = {
        "source_key",
        "source_version_id",
        "dataset_version",
        "source_release_identity_checksum",
        "registry_attribution_identity_checksum",
        "owner_decision_identity_checksum",
        "provider_decision_identity_checksum",
        "authorization_bridge_identity_checksum",
    }
    if set(value) != expected:
        raise ValueError(f"{record_key}: source release fields changed")
    _require_text(value["source_key"], f"{record_key}.source_key")
    _require_text(value["dataset_version"], f"{record_key}.dataset_version")
    if not _UUID.fullmatch(_require_text(value["source_version_id"], "source UUID")):
        raise ValueError(f"{record_key}: source version UUID is invalid")
    for key in expected:
        if key.endswith("checksum") and not _SHA256.fullmatch(
            _require_text(value[key], f"{record_key}.{key}")
        ):
            raise ValueError(f"{record_key}: {key} is not SHA-256")


def _validate_source_rights(payload: Mapping[str, Any], *, expect_identity: bool) -> None:
    expected_top = {
        "schema_version",
        "policy_version",
        "record_count",
        "package_allowed_purposes",
        "package_prohibited_purposes",
        "package_controls",
        "records",
    }
    if expect_identity:
        expected_top.add("identity_checksum")
    if set(payload) != expected_top:
        raise ValueError("Source-rights package fields changed")
    if payload["schema_version"] != SOURCE_RIGHTS_SCHEMA_VERSION:
        raise ValueError("Source-rights schema version changed")
    if payload["policy_version"] != SOURCE_RIGHTS_POLICY_VERSION:
        raise ValueError("Source-rights policy version changed")
    if tuple(payload["package_allowed_purposes"]) != PACKAGE_ALLOWED_PURPOSES:
        raise ValueError("Package allowed purposes changed")
    if tuple(payload["package_prohibited_purposes"]) != PACKAGE_PROHIBITED_PURPOSES:
        raise ValueError("Package prohibited purposes changed")

    controls = payload["package_controls"]
    if not isinstance(controls, Mapping) or controls != {
        "legal_conclusion": "NO_NEW_LEGAL_CONCLUSIONS",
        "public_redistribution_authorized": False,
        "raw_redistribution_authorized": False,
        "unreviewed_commercial_redistribution_authorized": False,
    }:
        raise ValueError("Package publication controls changed")

    records = payload["records"]
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ValueError("Source-rights records must be a sequence")
    if payload["record_count"] != len(records) or len(records) != 6:
        raise ValueError("Source-rights record count changed")
    if tuple(record.get("rights_control_id") for record in records) != (
        SOURCE_RIGHTS_RECORD_ORDER
    ):
        raise ValueError("Source-rights record order changed")

    seen_source_keys: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("Source-rights record must be an object")
        record_key = _require_text(record.get("record_key"), "record_key")
        if record.get("rights_control_id") != record_key:
            raise ValueError(f"{record_key}: rights-control identity changed")
        _require_text(record.get("source_name"), f"{record_key}.source_name")
        _require_text(record.get("display_name"), f"{record_key}.display_name")
        _require_text(
            record.get("rights_classification"),
            f"{record_key}.rights_classification",
        )
        releases = record.get("source_releases")
        if not isinstance(releases, list):
            raise ValueError(f"{record_key}: source_releases must be a list")
        for release in releases:
            _validate_release(release, record_key=record_key)
            source_key = release["source_key"]
            if record_key != "LAHMAN_SEAMHEADS_NEGRO_LEAGUES_EXCLUDED":
                if source_key in seen_source_keys:
                    raise ValueError(f"Duplicate governed source key {source_key}")
                seen_source_keys.add(source_key)

        allowed = record.get("allowed_purposes")
        if not isinstance(allowed, list) or not allowed:
            raise ValueError(f"{record_key}: allowed_purposes is invalid")
        if any(item not in PACKAGE_ALLOWED_PURPOSES for item in allowed):
            raise ValueError(f"{record_key}: purpose exceeds package scope")
        record_controls = record.get("controls")
        if not isinstance(record_controls, Mapping):
            raise ValueError(f"{record_key}: controls are missing")
        for key in (
            "public_redistribution_authorized_by_package",
            "raw_redistribution_authorized_by_package",
            "unreviewed_commercial_redistribution_authorized_by_package",
        ):
            if record_controls.get(key) is not False:
                raise ValueError(f"{record_key}: {key} must remain false")

        terms_checksum = record.get("terms_checksum")
        if terms_checksum is not None and not _SHA256.fullmatch(terms_checksum):
            raise ValueError(f"{record_key}: terms checksum is invalid")

    if seen_source_keys != {
        "RETROSHEET_CSV_DOWNLOADS",
        "RETROSHEET_EVENT_ARCHIVES",
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "CHADWICK_REGISTER_ARCHIVES",
        "SABR_LAHMAN_BOX_DOWNLOADS",
    }:
        raise ValueError("Acquired FD1 source-key coverage changed")

    by_key = {record["record_key"]: record for record in records}
    retrosheet = by_key["RETROSHEET"]
    if retrosheet["attribution_text"] != RETROSHEET_ATTRIBUTION:
        raise ValueError("Retrosheet attribution text changed")
    if retrosheet["required_attribution_text"] != RETROSHEET_ATTRIBUTION:
        raise ValueError("Retrosheet required attribution text changed")
    if tuple(
        item["source_key"] for item in retrosheet["source_releases"]
    ) != (
        "RETROSHEET_CSV_DOWNLOADS",
        "RETROSHEET_EVENT_ARCHIVES",
        "RETROSHEET_AUXILIARY_DOWNLOADS",
    ):
        raise ValueError("Retrosheet source-release coverage changed")

    for key in (
        "LAHMAN_SEAMHEADS_NEGRO_LEAGUES_EXCLUDED",
        "NOAA_NCEI_LCDV2_ZERO_USE",
        "OPEN_METEO_FREE_HOSTED_ZERO_USE",
    ):
        zero_use = by_key[key]
        use = zero_use.get("fd1_use")
        if not isinstance(use, Mapping) or use.get("manifest_artifact_count") != 0:
            raise ValueError(f"{zero_use['record_key']}: zero-use boundary changed")
        if zero_use.get("allowed_purposes") != [READ_ONLY_REGISTRATION]:
            raise ValueError(f"{zero_use['record_key']}: must remain registration-only")

    if expect_identity:
        identity = payload.get("identity_checksum")
        if not isinstance(identity, str) or not _SHA256.fullmatch(identity):
            raise ValueError("Source-rights package identity is invalid")
        without_identity = dict(payload)
        without_identity.pop("identity_checksum")
        if identity != _checksum(without_identity):
            raise ValueError("Source-rights package identity does not match its content")


def build_source_rights() -> dict[str, Any]:
    """Return a fresh, deterministic six-record source-rights package."""

    payload: dict[str, Any] = {
        "schema_version": SOURCE_RIGHTS_SCHEMA_VERSION,
        "policy_version": SOURCE_RIGHTS_POLICY_VERSION,
        "record_count": len(SOURCE_RIGHTS_RECORD_ORDER),
        "package_allowed_purposes": list(PACKAGE_ALLOWED_PURPOSES),
        "package_prohibited_purposes": list(PACKAGE_PROHIBITED_PURPOSES),
        "package_controls": {
            "legal_conclusion": "NO_NEW_LEGAL_CONCLUSIONS",
            "public_redistribution_authorized": False,
            "raw_redistribution_authorized": False,
            "unreviewed_commercial_redistribution_authorized": False,
        },
        "records": _records(),
    }
    _validate_source_rights(payload, expect_identity=False)
    payload["identity_checksum"] = _checksum(payload)
    _validate_source_rights(payload, expect_identity=True)
    # A canonical JSON round-trip guarantees callers receive no shared mutable
    # objects while retaining the package's deterministic field values.
    return json.loads(_canonical_json(payload))


def _blockquote(value: str) -> str:
    return "\n".join(f"> {line}" for line in value.splitlines())


def build_attribution_markdown() -> str:
    """Render deterministic attribution and non-publication guidance."""

    rights = build_source_rights()
    by_key = {
        record["rights_control_id"]: record for record in rights["records"]
    }
    retrosheet = by_key["RETROSHEET"]
    chadwick = by_key["CHADWICK_PERSONS_REGISTER"]
    lahman = by_key["SABR_LAHMAN_MODERN_MLB"]
    noaa = by_key["NOAA_NCEI_LCDV2_ZERO_USE"]
    open_meteo = by_key["OPEN_METEO_FREE_HOSTED_ZERO_USE"]

    lines = [
        "# Source Rights and Attribution",
        "",
        "This package records previously governed Gate 3C-FD1 evidence for "
        "local/private model development, internal derived analytics, and "
        "read-only registration. It authorizes no public dataset redistribution, "
        "raw-source redistribution, or unreviewed commercial redistribution, and "
        "it makes no new legal conclusion.",
        "",
        "## Retrosheet",
        "",
        _blockquote(retrosheet["attribution_text"]),
        "",
        "The notice applies to the three recorded Retrosheet source releases. "
        "The separate download-page postal-notice discrepancy remains a public-"
        "release blocker pending independent capture and review.",
        "",
        "## Chadwick Baseball Bureau Persons Register",
        "",
        _blockquote(chadwick["attribution_text"]),
        "",
        "The identity evidence is pinned to commit "
        f"`{chadwick['source_releases'][0]['dataset_version']}` under ODC-By 1.0; "
        "ASP transformations and unresolved mappings remain separately identified.",
        "",
        "## SABR Lahman Baseball Database",
        "",
        _blockquote(lahman["attribution_text"]),
        "",
        "Only the modern MLB population entered FD1. The Lahman Negro Leagues "
        "population sourced from Seamheads remains excluded and registration-only "
        "pending an independent embedded-rights review.",
        "",
        "## Zero-use weather records",
        "",
        f"- NOAA/NCEI LCDv2: `{noaa['fd1_use']['status']}`. The recorded citation "
        "template and station/file lineage duties activate only after separately "
        "governed future use.",
        f"- Open-Meteo free hosted API: `{open_meteo['fd1_use']['status']}`. No "
        "Open-Meteo evidence entered FD1; the free hosted tier remains "
        "noncommercial and any commercial use requires a new immutable decision.",
        "",
        "Raw archives and source payloads are not part of this attribution text and "
        "must not be exposed by the exchange package.",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "CHADWICK_ATTRIBUTION",
    "INTERNAL_DERIVED_ANALYTICS",
    "LAHMAN_ATTRIBUTION",
    "LOCAL_PRIVATE_MODEL_DEVELOPMENT",
    "NOAA_LCDV2_CITATION_TEMPLATE",
    "OPEN_METEO_ATTRIBUTION",
    "PACKAGE_ALLOWED_PURPOSES",
    "PACKAGE_PROHIBITED_PURPOSES",
    "PUBLIC_BULK_DOWNLOAD",
    "PUBLIC_RAW_REDISTRIBUTION",
    "RAW_DATA_RESALE",
    "READ_ONLY_REGISTRATION",
    "RETROSHEET_ATTRIBUTION",
    "SOURCE_RIGHTS_POLICY_VERSION",
    "SOURCE_RIGHTS_RECORD_ORDER",
    "SOURCE_RIGHTS_SCHEMA_VERSION",
    "UNREVIEWED_COMMERCIAL_REDISTRIBUTION",
    "build_attribution_markdown",
    "build_source_rights",
]
