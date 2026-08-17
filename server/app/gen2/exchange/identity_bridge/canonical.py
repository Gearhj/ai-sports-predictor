"""Canonical JSON and semantic identity for ``SPORTS_IDENTITY_BRIDGE_V1``."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from app.gen2.exchange.canonical_json import (
    CANONICALIZATION_VERSION,
    CanonicalJsonError,
    canonical_json_bytes,
    canonical_sha256,
    load_canonical_json,
)


SEMANTICALLY_EXCLUDED_FIELDS = frozenset(
    {
        "bridge_manifest_id",
        "bridge_manifest_sha256",
        "bridge_package_id",
        "publication_timestamp",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def semantic_bridge_manifest_payload(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the documented bridge semantic projection without mutating input."""

    if not isinstance(manifest, Mapping):
        raise CanonicalJsonError("Bridge manifest must be a JSON object")
    payload = copy.deepcopy(dict(manifest))
    for field in SEMANTICALLY_EXCLUDED_FIELDS:
        payload.pop(field, None)
    return payload


def bridge_manifest_semantic_sha256(manifest: Mapping[str, Any]) -> str:
    """Hash the canonical semantic projection without a trailing newline."""

    return canonical_sha256(semantic_bridge_manifest_payload(manifest))


def apply_bridge_manifest_identity(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Populate the three digest-derived bridge identifiers on a deep copy."""

    resolved = copy.deepcopy(dict(manifest))
    digest = bridge_manifest_semantic_sha256(resolved)
    resolved.update(
        {
            "bridge_manifest_id": (
                f"sports-identity-bridge-manifest:sha256:{digest}"
            ),
            "bridge_manifest_sha256": digest,
            "bridge_package_id": f"sha256-{digest}",
        }
    )
    return resolved


def verify_bridge_manifest_identity(manifest: Mapping[str, Any]) -> str:
    """Recompute the semantic digest and verify every derived bridge identifier."""

    digest = bridge_manifest_semantic_sha256(manifest)
    declared = manifest.get("bridge_manifest_sha256")
    if not isinstance(declared, str) or _SHA256.fullmatch(declared) is None:
        raise CanonicalJsonError(
            "bridge_manifest_sha256 is not a lowercase SHA-256"
        )
    expected = {
        "bridge_manifest_sha256": digest,
        "bridge_manifest_id": f"sports-identity-bridge-manifest:sha256:{digest}",
        "bridge_package_id": f"sha256-{digest}",
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise CanonicalJsonError(
                f"{field} does not match the semantic bridge manifest digest"
            )
    return digest


__all__ = [
    "CANONICALIZATION_VERSION",
    "CanonicalJsonError",
    "SEMANTICALLY_EXCLUDED_FIELDS",
    "apply_bridge_manifest_identity",
    "bridge_manifest_semantic_sha256",
    "canonical_json_bytes",
    "canonical_sha256",
    "load_canonical_json",
    "semantic_bridge_manifest_payload",
    "verify_bridge_manifest_identity",
]
