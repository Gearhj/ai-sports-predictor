"""Canonical JSON and semantic identity for ``SPORTS_DATASET_MANIFEST_V1``.

The exchange deliberately distinguishes the semantic manifest digest from the
SHA-256 of the serialized manifest file.  The semantic digest excludes fields
that are either derived from that digest or describe the act of publication:

* ``manifest_id``
* ``manifest_sha256``
* ``dataset_version_id``
* ``package_id``
* ``published_at``

All other fields participate.  This avoids a self-reference while keeping the
dataset identity independent of a machine path and of an idempotent rerun time.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any


CANONICALIZATION_VERSION = "SPORTS_CANONICAL_JSON_V1"
SEMANTICALLY_EXCLUDED_FIELDS = frozenset(
    {
        "manifest_id",
        "manifest_sha256",
        "dataset_version_id",
        "package_id",
        "published_at",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CanonicalJsonError(ValueError):
    """JSON is ambiguous, non-canonical, or has an invalid semantic identity."""


def canonical_json_bytes(value: object, *, trailing_newline: bool = False) -> bytes:
    """Encode UTF-8 JSON with sorted keys, compact separators, and no NaN values."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CanonicalJsonError("Value cannot be represented as canonical JSON") from exc
    return encoded + (b"\n" if trailing_newline else b"")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def semantic_manifest_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the documented semantic projection used for manifest identity."""

    if not isinstance(manifest, Mapping):
        raise CanonicalJsonError("Manifest must be a JSON object")
    payload = copy.deepcopy(dict(manifest))
    for field in SEMANTICALLY_EXCLUDED_FIELDS:
        payload.pop(field, None)
    return payload


def manifest_semantic_sha256(manifest: Mapping[str, Any]) -> str:
    return canonical_sha256(semantic_manifest_payload(manifest))


def apply_manifest_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Populate all digest-derived identifiers without mutating the caller value."""

    resolved = copy.deepcopy(dict(manifest))
    digest = manifest_semantic_sha256(resolved)
    dataset_id = resolved.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise CanonicalJsonError("dataset_id is required before identity derivation")
    resolved.update(
        {
            "manifest_id": f"sports-dataset-manifest:sha256:{digest}",
            "manifest_sha256": digest,
            "dataset_version_id": f"{dataset_id}:sha256:{digest}",
            "package_id": f"sha256-{digest}",
        }
    )
    return resolved


def verify_manifest_identity(manifest: Mapping[str, Any]) -> str:
    """Recompute and verify the semantic digest plus all derived identifiers."""

    digest = manifest_semantic_sha256(manifest)
    if not _SHA256.fullmatch(str(manifest.get("manifest_sha256", ""))):
        raise CanonicalJsonError("manifest_sha256 is not a lowercase SHA-256")
    expected = {
        "manifest_sha256": digest,
        "manifest_id": f"sports-dataset-manifest:sha256:{digest}",
        "dataset_version_id": f"{manifest.get('dataset_id')}:sha256:{digest}",
        "package_id": f"sha256-{digest}",
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise CanonicalJsonError(f"{field} does not match the semantic manifest digest")
    return digest


def load_canonical_json(path: Path) -> object:
    """Load unique-key canonical JSON encoded with exactly one final LF."""

    try:
        encoded = Path(path).read_bytes()
        payload = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CanonicalJsonError("Canonical JSON is unreadable") from exc
    if encoded != canonical_json_bytes(payload, trailing_newline=True):
        raise CanonicalJsonError("JSON bytes do not use the canonical exchange encoding")
    return payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CanonicalJsonError(f"Duplicate JSON key: {key}")
        value[key] = item
    return value


__all__ = [
    "CANONICALIZATION_VERSION",
    "CanonicalJsonError",
    "SEMANTICALLY_EXCLUDED_FIELDS",
    "apply_manifest_identity",
    "canonical_json_bytes",
    "canonical_sha256",
    "load_canonical_json",
    "manifest_semantic_sha256",
    "semantic_manifest_payload",
    "verify_manifest_identity",
]
