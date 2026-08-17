"""Portable, content-addressed MLB FD1 player identity companion contract."""

from app.gen2.exchange.identity_bridge.canonical import (
    CANONICALIZATION_VERSION,
    SEMANTICALLY_EXCLUDED_FIELDS,
    apply_bridge_manifest_identity,
    bridge_manifest_semantic_sha256,
    canonical_json_bytes,
    canonical_sha256,
    load_canonical_json,
    semantic_bridge_manifest_payload,
    verify_bridge_manifest_identity,
)
from app.gen2.exchange.identity_bridge.contracts import (
    BridgeContractError,
    SportsIdentityBridgeManifestV1,
    manifest_json_schema,
)
from app.gen2.exchange.identity_bridge.trust import (
    CONTRACT_NAME,
    CONTRACT_VERSION,
    EXPECTED_ALIAS_AUTHORITY_SHA256,
    EXPECTED_BRIDGE_MANIFEST_SHA256,
    EXPECTED_DIMENSION_AUTHORITY_SHA256,
    EXPECTED_PARENT_MANIFEST_SHA256,
    EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256,
)


__all__ = [
    "BridgeContractError",
    "CANONICALIZATION_VERSION",
    "CONTRACT_NAME",
    "CONTRACT_VERSION",
    "EXPECTED_ALIAS_AUTHORITY_SHA256",
    "EXPECTED_BRIDGE_MANIFEST_SHA256",
    "EXPECTED_DIMENSION_AUTHORITY_SHA256",
    "EXPECTED_PARENT_MANIFEST_SHA256",
    "EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256",
    "SEMANTICALLY_EXCLUDED_FIELDS",
    "SportsIdentityBridgeManifestV1",
    "apply_bridge_manifest_identity",
    "bridge_manifest_semantic_sha256",
    "canonical_json_bytes",
    "canonical_sha256",
    "load_canonical_json",
    "manifest_json_schema",
    "semantic_bridge_manifest_payload",
    "verify_bridge_manifest_identity",
]
