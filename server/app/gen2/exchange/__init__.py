"""Immutable, portable Gate 3C-X1 sports dataset exchange."""

from app.gen2.exchange.canonical_json import (
    CANONICALIZATION_VERSION,
    canonical_json_bytes,
    manifest_semantic_sha256,
    verify_manifest_identity,
)

__all__ = [
    "CANONICALIZATION_VERSION",
    "canonical_json_bytes",
    "manifest_semantic_sha256",
    "verify_manifest_identity",
]
