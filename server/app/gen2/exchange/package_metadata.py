"""Deterministic auxiliary metadata for a sports dataset exchange package.

This module deliberately has no dependency on the publisher.  Package-only
consumers such as the validator can therefore reproduce the exact README bytes
without importing source-runtime or publication orchestration code.
"""

from __future__ import annotations

import hashlib
from typing import Protocol


class _ManifestReadmeView(Protocol):
    manifest_id: str
    manifest_sha256: str


def normalized_markdown_bytes(value: str) -> bytes:
    """Return UTF-8 Markdown with LF endings and exactly one final newline."""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return (normalized.rstrip("\n") + "\n").encode("utf-8")


def readme_bytes(manifest: _ManifestReadmeView, report_bytes: bytes) -> bytes:
    """Build the exact package README bound to its canonical validation report."""

    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    text = f"""# Immutable MLB FD1 Dataset Exchange

Contract: `SPORTS_DATASET_MANIFEST_V1`

Manifest ID: `{manifest.manifest_id}`

Manifest semantic SHA-256: `{manifest.manifest_sha256}`

This content-addressed package contains exactly 329 byte-for-byte copied Parquet
partitions, 3,738,096 rows, and 337,421,353 Parquet bytes. Consumer data URIs are
relative to this package root. The package needs no AI Sports Predictor database,
runtime directory, raw archive, provider, or network access.

The semantic manifest digest includes the schema registry, source-rights record,
attribution, and all partition identities. `README.md` and
`validation-report.json` are auxiliary files outside the semantic digest to avoid
a self-referential checksum cycle.

validation-report-sha256: `{report_sha256}`

The exchange is limited to local/private model development, internal derived
analytics, and read-only registration. It does not authorize public bulk download,
raw resale, public raw redistribution, or unreviewed commercial redistribution.
"""
    return normalized_markdown_bytes(text)


__all__ = ["normalized_markdown_bytes", "readme_bytes"]
