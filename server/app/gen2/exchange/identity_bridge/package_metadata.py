"""Deterministic auxiliary metadata for an identity-bridge package."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Protocol

from app.gen2.exchange.canonical_json import canonical_json_bytes


class _ManifestView(Protocol):
    bridge_manifest_id: str
    bridge_manifest_sha256: str
    bridge_package_id: str


def validation_report_bytes(report: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(dict(report), trailing_newline=True)


def readme_bytes(manifest: _ManifestView, report_bytes: bytes) -> bytes:
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    text = f"""# MLB FD1 Sports Identity Bridge

Contract: `SPORTS_IDENTITY_BRIDGE_V1`

Manifest ID: `{manifest.bridge_manifest_id}`

Manifest semantic SHA-256: `{manifest.bridge_manifest_sha256}`

Package ID: `{manifest.bridge_package_id}`

This immutable companion binds exactly to the canonical MLB FD1 dataset
manifest `1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49`.
It contains only governed public professional-baseball player aliases needed by
the 2021–2025 corpus. It contains no names, fuzzy matches, database export,
runtime path, raw source archive, provider credential, forecast, or user data.

## Consumer join

For Retrosheet event families, join the physical player identifier (for example
`batter_id`, `pitcher_id`, or `player_id`) exactly to
`player-identity-aliases.parquet.parent_join_key` after filtering the alias file
to `namespace == "RETROSHEET"`. The result field is
`canonical_player_uuid`.

For `player_prior`, join its physical `canonical_player_id` directly to
`active-player-dimension.parquet.canonical_player_uuid`. A target season may use
only prior rows whose `eligible_from_season` is less than or equal to the target
season. Absence of eligible history is a legitimate missing-prior case, not an
identity failure.

Every consumer must run the standalone bridge validator with both the parent
dataset package and this package before use. The validator requires no AI Sports
Predictor database, runtime files, raw archives, provider, network, ASP Model
Studio, or financial ATLAS access.

validation-report-sha256: `{report_sha256}`

Use is limited to local/private model development, internal derived analytics,
and read-only registration. Public raw bridge download, raw redistribution,
identity-data resale, and unrestricted commercial redistribution are not
authorized by this package.
"""
    return (text.rstrip("\n") + "\n").encode("utf-8")


__all__ = ["readme_bytes", "validation_report_bytes"]
