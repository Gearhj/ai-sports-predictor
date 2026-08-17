"""Deterministic source-rights subset for ``SPORTS_IDENTITY_BRIDGE_V1``.

The bridge makes no independent legal interpretation.  It projects the three
applicable controls byte-for-byte from the already-validated parent dataset
rights record and preserves the frozen attribution language.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Mapping

from app.gen2.exchange.canonical_json import (
    canonical_json_bytes,
    canonical_sha256,
    load_canonical_json,
)


SOURCE_RIGHTS_SCHEMA_VERSION = "SPORTS_IDENTITY_BRIDGE_SOURCE_RIGHTS_V1"
PARENT_SOURCE_RIGHTS_SCHEMA_VERSION = "SPORTS_DATASET_SOURCE_RIGHTS_V1"
REQUIRED_RIGHTS_IDS = (
    "CHADWICK_PERSONS_REGISTER",
    "RETROSHEET",
    "SABR_LAHMAN_MODERN_MLB",
)
ALLOWED_PURPOSES = (
    "INTERNAL_DERIVED_ANALYTICS",
    "LOCAL_PRIVATE_MODEL_DEVELOPMENT",
    "READ_ONLY_REGISTRATION",
)
PROHIBITED_PURPOSES = (
    "IDENTITY_DATA_RESALE",
    "PUBLIC_BULK_DOWNLOAD",
    "PUBLIC_RAW_BRIDGE_DOWNLOAD",
    "PUBLIC_RAW_REDISTRIBUTION",
    "UNRESTRICTED_COMMERCIAL_REDISTRIBUTION",
)

_RETROSHEET_NOTICE = (
    "The information used here was obtained free of\n"
    "charge from and is copyrighted by Retrosheet.  Interested\n"
    'parties may contact Retrosheet at "www.retrosheet.org".'
)
_CHADWICK_NOTICE = (
    "Contains information from the Chadwick Baseball Bureau Persons Register, "
    "which is made available under the ODC Attribution License."
)
_LAHMAN_NOTICE = (
    "Includes data from the SABR Lahman Baseball Database 1871–2025, created by "
    "Sean Lahman and published by SABR, under CC BY-SA 3.0. ASP transformations "
    "are identified separately."
)


class IdentityBridgeRightsError(ValueError):
    """The parent rights evidence or bridge projection is not exact."""


def source_rights_payload(parent_package_root: Path) -> dict[str, Any]:
    """Project only applicable player-identity controls from the parent package."""

    root = Path(parent_package_root)
    parent_path = root / "source-rights.json"
    parent = load_canonical_json(parent_path)
    if not isinstance(parent, dict):
        raise IdentityBridgeRightsError("Parent source-rights record must be an object")
    if parent.get("schema_version") != PARENT_SOURCE_RIGHTS_SCHEMA_VERSION:
        raise IdentityBridgeRightsError("Parent source-rights schema is unsupported")
    if tuple(parent.get("package_allowed_purposes", ())) != ALLOWED_PURPOSES:
        raise IdentityBridgeRightsError("Parent allowed-purpose policy differs")
    records = parent.get("records")
    if not isinstance(records, list):
        raise IdentityBridgeRightsError("Parent source-right records are missing")
    by_id: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise IdentityBridgeRightsError("Parent source-right record is invalid")
        rights_id = record.get("rights_control_id")
        if not isinstance(rights_id, str) or rights_id in by_id:
            raise IdentityBridgeRightsError("Parent source-right IDs are invalid")
        by_id[rights_id] = record
    if not set(REQUIRED_RIGHTS_IDS).issubset(by_id):
        raise IdentityBridgeRightsError("Applicable parent source-rights are incomplete")

    selected = [copy.deepcopy(dict(by_id[rights_id])) for rights_id in REQUIRED_RIGHTS_IDS]
    notices = {
        "RETROSHEET": _RETROSHEET_NOTICE,
        "CHADWICK_PERSONS_REGISTER": _CHADWICK_NOTICE,
        "SABR_LAHMAN_MODERN_MLB": _LAHMAN_NOTICE,
    }
    for record in selected:
        rights_id = str(record["rights_control_id"])
        if record.get("required_attribution_text") != notices[rights_id]:
            raise IdentityBridgeRightsError(
                f"Parent attribution text differs for {rights_id}"
            )
        if tuple(record.get("allowed_purposes", ())) != ALLOWED_PURPOSES:
            raise IdentityBridgeRightsError(
                f"Parent allowed purposes differ for {rights_id}"
            )

    controls = parent.get("package_controls")
    if not isinstance(controls, dict) or controls != {
        "legal_conclusion": "NO_NEW_LEGAL_CONCLUSIONS",
        "public_redistribution_authorized": False,
        "raw_redistribution_authorized": False,
        "unreviewed_commercial_redistribution_authorized": False,
    }:
        raise IdentityBridgeRightsError("Parent package controls differ")

    payload: dict[str, Any] = {
        "schema_version": SOURCE_RIGHTS_SCHEMA_VERSION,
        "parent_schema_version": PARENT_SOURCE_RIGHTS_SCHEMA_VERSION,
        "parent_source_rights_file_sha256": hashlib.sha256(
            parent_path.read_bytes()
        ).hexdigest(),
        "package_allowed_purposes": list(ALLOWED_PURPOSES),
        "package_prohibited_purposes": list(PROHIBITED_PURPOSES),
        "package_controls": copy.deepcopy(controls),
        "record_count": len(selected),
        "records": selected,
    }
    payload["identity_checksum"] = canonical_sha256(payload)
    return payload


def source_rights_bytes(parent_package_root: Path) -> bytes:
    return canonical_json_bytes(
        source_rights_payload(parent_package_root), trailing_newline=True
    )


def attribution_bytes(parent_package_root: Path) -> bytes:
    """Return bridge attribution after proving all notices occur in the parent."""

    try:
        parent_text = (Path(parent_package_root) / "attribution.md").read_text(
            encoding="utf-8"
        )
    except (OSError, UnicodeError) as exc:
        raise IdentityBridgeRightsError("Parent attribution is unreadable") from exc
    expected_fragments = (
        *_RETROSHEET_NOTICE.splitlines(),
        _CHADWICK_NOTICE,
        _LAHMAN_NOTICE,
    )
    if any(fragment not in parent_text for fragment in expected_fragments):
        raise IdentityBridgeRightsError("Parent attribution notice differs")

    text = f'''# Source Rights and Attribution

This companion package preserves the applicable rights decisions already frozen
by the parent MLB FD1 package. It is limited to local/private model development,
internal derived analytics, and read-only registration. It authorizes no public
bulk download, raw redistribution, identity-data resale, or unrestricted
commercial redistribution, and it makes no new legal conclusion.

## Retrosheet

> The information used here was obtained free of
> charge from and is copyrighted by Retrosheet.  Interested
> parties may contact Retrosheet at "www.retrosheet.org".

The parent package's separate public-release review boundary remains unchanged.

## Chadwick Baseball Bureau Persons Register

> {_CHADWICK_NOTICE}

The governed identity evidence remains pinned to commit
`2e8e73355f9c77b963115377bd98c784cfeec10f` under ODC-By 1.0.

## SABR Lahman Baseball Database

> {_LAHMAN_NOTICE}

Only the modern MLB population used by the parent FD1 package is represented.
The parent package's separate excluded-population review boundary remains
unchanged.

No raw source archive or source payload is included in this bridge.
'''
    return (text.rstrip("\n") + "\n").encode("utf-8")


def validate_rights_artifacts(
    parent_package_root: Path,
    *,
    actual_source_rights: bytes,
    actual_attribution: bytes,
) -> None:
    if actual_source_rights != source_rights_bytes(parent_package_root):
        raise IdentityBridgeRightsError("Bridge source-rights bytes differ")
    if actual_attribution != attribution_bytes(parent_package_root):
        raise IdentityBridgeRightsError("Bridge attribution bytes differ")


__all__ = [
    "ALLOWED_PURPOSES",
    "IdentityBridgeRightsError",
    "PROHIBITED_PURPOSES",
    "REQUIRED_RIGHTS_IDS",
    "SOURCE_RIGHTS_SCHEMA_VERSION",
    "attribution_bytes",
    "source_rights_bytes",
    "source_rights_payload",
    "validate_rights_artifacts",
]
