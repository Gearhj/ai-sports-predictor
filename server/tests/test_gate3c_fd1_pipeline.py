from __future__ import annotations

from urllib.parse import urlsplit

from app.gen2.fd1_pipeline import (
    CHADWICK_REVISION,
    DOWNLOAD_CATALOG,
    FD1_SOURCE_POLICIES,
    catalog_summary,
)
from app.gen2.open_data_acquisition import SourcePolicyState


def test_fd1_catalog_is_exact_official_bounded_and_source_specific() -> None:
    assert CHADWICK_REVISION == "2e8e73355f9c77b963115377bd98c784cfeec10f"
    object_keys = [item.request.object_key for item in DOWNLOAD_CATALOG]
    assert len(object_keys) == len(set(object_keys)) == 39
    assert {item.stage for item in DOWNLOAD_CATALOG} == {"stage1", "stage2", "stage3"}
    assert all(item.request.url.startswith("https://") for item in DOWNLOAD_CATALOG)
    assert not any(
        marker in item.request.url.lower()
        for item in DOWNLOAD_CATALOG
        for marker in ("mlb.com", "baseballsavant", "pybaseball", "baseballr")
    )
    for item in DOWNLOAD_CATALOG:
        host = urlsplit(item.request.url).hostname
        assert host in FD1_SOURCE_POLICIES[item.request.source_key].official_hosts

    summary = catalog_summary()
    assert summary["objects"] == 39
    assert summary["known_content_bytes"] < 15 * 1024**3
    assert summary["maximum_incremental_bytes"] == 25 * 1024**3
    assert summary["minimum_free_reserve_bytes"] == 100 * 1024**3
    assert summary["mlb_savant_automated_acquisition"] == "BLOCKED"


def test_authorized_policies_are_independent_and_attribution_bound() -> None:
    assert set(FD1_SOURCE_POLICIES) == {
        "RETROSHEET_CSV_DOWNLOADS",
        "RETROSHEET_EVENT_ARCHIVES",
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "CHADWICK_REGISTER_ARCHIVES",
        "SABR_LAHMAN_BOX_DOWNLOADS",
    }
    identities = {
        policy.authorization_identity_checksum
        for policy in FD1_SOURCE_POLICIES.values()
    }
    assert len(identities) == len(FD1_SOURCE_POLICIES)
    assert all(
        policy.state is SourcePolicyState.AUTHORIZED_OPEN_DATA
        for policy in FD1_SOURCE_POLICIES.values()
    )
    assert all(item.attribution_identity for item in DOWNLOAD_CATALOG)
    assert all(item.license_identity for item in DOWNLOAD_CATALOG)


def test_current_official_lahman_folder_is_frozen_without_invented_tables() -> None:
    lahman_keys = {
        item.request.object_key
        for item in DOWNLOAD_CATALOG
        if item.request.source_key == "SABR_LAHMAN_BOX_DOWNLOADS"
    }
    assert "lahman-2025-people-csv" in lahman_keys
    assert "lahman-2025-batting-csv" in lahman_keys
    assert "lahman-2025-pitching-csv" in lahman_keys
    assert "lahman-2025-fielding-csv" in lahman_keys
    # SABR's current official 20-item CSV folder does not expose these legacy
    # table names.  FD1 must report that source limitation instead of inventing
    # a mirror or scraping a replacement.
    assert "lahman-2025-appearances-csv" not in lahman_keys
    assert "lahman-2025-allstarfull-csv" not in lahman_keys
    assert "lahman-2025-awardsplayers-csv" not in lahman_keys
