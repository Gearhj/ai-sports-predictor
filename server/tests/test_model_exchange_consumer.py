"""Focused checks for the Model Exchange bundle consumer.

Only behaviour this assignment added: what the scan will and will not pick up,
and that an ineligible bundle is refused. Verification itself belongs to the
existing importer and is not re-tested here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.jobs.model_exchange_consumer import (
    consume_forecast_bundles,
    current_bundle_addresses,
    discover_bundles,
)

CURRENT = "a" * 64
HISTORICAL = "b" * 64


def _bundle(root: Path, address: str, *, status: str = "RESEARCH_SHADOW") -> Path:
    directory = root / "sha256" / address
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "bundle.json").write_text(
        json.dumps({"status": status}), encoding="utf-8"
    )
    return directory


def _index(root: Path, addresses: list[str], *, schema: str = "ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V2") -> None:
    (root / "nfl-2026-research-shadow-index.json").write_text(
        json.dumps({
            "schemaVersion": schema,
            "league": "NFL",
            "currentBundleCount": len(addresses),
            "currentBundleSha256s": addresses,
        }),
        encoding="utf-8",
    )


def test_only_content_addressed_directories_are_discoverable(tmp_path: Path) -> None:
    _bundle(tmp_path, CURRENT)
    # Neither a traversal-shaped name nor a partial upload is a bundle.
    (tmp_path / "sha256" / "..").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sha256" / "not-an-address").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sha256" / f"{CURRENT[:63]}").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sha256" / "loose-file.json").write_text("{}", encoding="utf-8")

    found = [path.name for path in discover_bundles(tmp_path)]
    assert found == [CURRENT]


def test_the_scan_ignores_historical_bundles_and_needs_an_index(tmp_path: Path) -> None:
    _bundle(tmp_path, CURRENT)
    _bundle(tmp_path, HISTORICAL)

    # No index: nothing is declared current, so nothing is imported.
    assert current_bundle_addresses(tmp_path) is None

    _index(tmp_path, [CURRENT])
    assert current_bundle_addresses(tmp_path) == {CURRENT}

    # An unsupported index schema is not accepted as an authority.
    _index(tmp_path, [CURRENT], schema="ASP_NFL_INDEX_V99")
    assert current_bundle_addresses(tmp_path) is None


def test_a_dry_run_without_an_index_imports_nothing(session, settings, tmp_path: Path) -> None:
    _bundle(tmp_path, CURRENT)
    outcome = consume_forecast_bundles(session, settings, dry_run=True, root=tmp_path)
    assert outcome["accepted_count"] == 0
    assert outcome["rejected_count"] == 1
    assert "historical archive" in outcome["rejected"][0]["reason"]
    assert outcome["official_decisions_created"] == 0
    assert outcome["positions_created"] == 0
    assert outcome["models_executed"] == 0


def test_an_ineligible_bundle_is_rejected_and_not_imported(
    session, settings, tmp_path: Path,
) -> None:
    """A malformed/ineligible current bundle is refused by existing validation."""

    directory = _bundle(tmp_path, CURRENT)
    # No fixture, no VALIDATION.md, and a body that is not the exchange
    # contract: the importer must refuse it rather than register anything.
    _index(tmp_path, [CURRENT])
    digest = hashlib.sha256((directory / "bundle.json").read_bytes()).hexdigest()
    assert digest != CURRENT  # the address does not even match its content

    outcome = consume_forecast_bundles(session, settings, root=tmp_path)
    assert outcome["accepted_count"] == 0
    assert outcome["reused_count"] == 0
    assert outcome["rejected_count"] == 1
    assert outcome["rejected"][0]["bundle_sha256"] == CURRENT
    assert outcome["positions_created"] == 0

    from sqlalchemy import func, select

    from app.models import OfficialAspPosition, ShadowForecastBundle

    assert session.scalar(select(func.count()).select_from(ShadowForecastBundle)) == 0
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 0
