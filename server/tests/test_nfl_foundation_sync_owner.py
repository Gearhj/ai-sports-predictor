"""Who owns the Foundation sync, what its receipt says, and what the guard covers.

These are deliberately fast: no Model Studio subprocess, no bundle fixtures. They
pin the three things the change actually asserts --

*   the sync is a registered job AISP owns, and it runs before CURRENT
    publication rather than being left to nobody;
*   its receipt distinguishes SKIPPED, FAILED and SUCCEEDED, and reads source
    freshness from the sync's own report rather than from a guessed path;
*   the producer code guard was widened to the paths the producer imports, and
    versioned so a receipt says which claim it is making.
"""
from __future__ import annotations

import json

import pytest

from app.jobs import AVAILABLE_JOBS
from app.jobs.orchestrator import (
    NFL_FOUNDATION_SYNC_ARGS,
    NFL_FOUNDATION_SYNC_VERSION,
    NFL_PRODUCER_CODE_PATHS,
    NFL_PRODUCER_CODE_SCOPE_VERSION,
    JobOrchestrator,
    _nfl_foundation_freshness,
)

#: A trimmed but structurally real ASP_NFL_CURRENT_SEASON_SYNC_REPORT_V1.
REPORT = {
    "contract": "ASP_NFL_CURRENT_SEASON_SYNC_REPORT_V1",
    "status": "COMPLETE",
    "attemptedAtUtc": "2026-09-13T20:21:09.798425Z",
    "currentSeason": 2026,
    "requestedThrough": "latest-completed",
    "sourceChanged": True,
    "warehouseAdvanced": True,
    "changedFamilies": ["schedules"],
    "historicalAuthorityPreserved": True,
    "modelsFitted": 0,
    "modelsPromoted": 0,
    "resultAuthority": {
        "datasetVersionId": "asp.nfl.gold-data-foundation.v1:sha256:a5d0e813",
        "foundationManifestSha256": "a5d0e813",
        "sourceSnapshotIdentity": "a462dbd7",
        "currentSeasonCompletedGames": 7,
        "currentSeasonScheduledGames": 272,
    },
    "sourceProbe": {"assetCount": 16, "familyCount": 25},
}


class TestOwnership:
    def test_the_sync_is_a_registered_job(self):
        """The producer found no recurring owner. This is the owner."""

        assert "sync-nfl-foundation-sources" in AVAILABLE_JOBS

    def test_the_command_is_the_documented_one_as_an_argument_vector(self):
        """A frozen vector, not a shell string: nothing can become a command."""

        assert NFL_FOUNDATION_SYNC_ARGS == (
            "-B", "-m", "scripts.sync_nfl_current_season",
            "--through", "latest-completed",
        )

    def test_the_orchestrator_exposes_the_handler(self):
        assert hasattr(JobOrchestrator, "sync_nfl_foundation_sources")
        assert hasattr(JobOrchestrator, "_run_nfl_foundation_sync")


class TestFreshnessComesFromTheSyncsOwnReport:
    def test_reads_the_declared_report(self):
        freshness = _nfl_foundation_freshness("noise\n" + json.dumps(REPORT))
        assert freshness["basis"] == "SYNC_SELF_REPORT"
        assert freshness["contract"] == "ASP_NFL_CURRENT_SEASON_SYNC_REPORT_V1"
        assert freshness["report_status"] == "COMPLETE"
        assert freshness["dataset_version_id"].endswith("a5d0e813")
        assert freshness["current_season_completed_games"] == 7

    def test_keeps_source_changed_and_warehouse_advanced_apart(self):
        """"The command succeeded" and "the sources are current" differ.

        A sync can exit zero having found nothing new. Collapsing the two would
        make a no-op indistinguishable from a real advance.
        """

        changed = _nfl_foundation_freshness(json.dumps(REPORT))
        assert changed["source_changed"] is True
        assert changed["warehouse_advanced"] is True
        assert changed["changed_families"] == ["schedules"]

        quiet = _nfl_foundation_freshness(json.dumps({
            **REPORT, "sourceChanged": False, "warehouseAdvanced": False,
            "changedFamilies": [],
        }))
        assert quiet["source_changed"] is False
        assert quiet["warehouse_advanced"] is False
        assert quiet["changed_family_count"] == 0

    def test_records_that_nothing_was_fitted_or_promoted(self):
        freshness = _nfl_foundation_freshness(json.dumps(REPORT))
        assert freshness["models_fitted"] == 0
        assert freshness["models_promoted"] == 0
        assert freshness["historical_authority_preserved"] is True

    def test_says_so_when_there_is_no_report_rather_than_inventing_one(self):
        assert _nfl_foundation_freshness("")["basis"] == "NO_SYNC_REPORT_ON_STDOUT"

    def test_says_so_when_the_report_will_not_parse(self):
        result = _nfl_foundation_freshness("{not json at all")
        assert result["basis"] == "SYNC_REPORT_UNPARSEABLE"


class TestReceiptOutcomes:
    @pytest.mark.asyncio
    async def test_an_unconfigured_studio_is_skipped_not_failed(self, session, settings):
        """Nothing attempted is not a failure, and not a success either."""

        configured = settings.model_copy(update={
            "nfl_model_studio_root": "", "nfl_model_studio_python": "",
        })
        receipt = await JobOrchestrator(session, configured)._run_nfl_foundation_sync()
        assert receipt["status"] == "SKIPPED"
        assert receipt["attempted"] is False
        assert receipt["version"] == NFL_FOUNDATION_SYNC_VERSION

    @pytest.mark.asyncio
    async def test_a_missing_studio_root_is_skipped(self, session, settings, tmp_path):
        configured = settings.model_copy(update={
            "nfl_model_studio_root": str(tmp_path / "absent"),
            "nfl_model_studio_python": str(tmp_path / "absent" / "python.exe"),
        })
        receipt = await JobOrchestrator(session, configured)._run_nfl_foundation_sync()
        assert receipt["status"] == "SKIPPED"
        assert receipt["attempted"] is False

    @pytest.mark.asyncio
    async def test_a_nonzero_exit_is_recorded_as_failed_with_its_status(
        self, session, settings, tmp_path, monkeypatch,
    ):
        """A failed sync must not read as a skip or as a success."""

        root = tmp_path / "studio"
        root.mkdir()
        python = root / "python.exe"
        python.write_text("", encoding="utf-8")

        class _Proc:
            returncode = 1

            async def communicate(self):
                return (json.dumps({**REPORT, "status": "FAILED",
                                    "warehouseAdvanced": False}).encode(), b"boom")

        async def fake_exec(*args, **kwargs):
            return _Proc()

        monkeypatch.setattr(
            "app.jobs.orchestrator.asyncio.create_subprocess_exec", fake_exec)
        configured = settings.model_copy(update={
            "nfl_model_studio_root": str(root),
            "nfl_model_studio_python": str(python),
        })
        receipt = await JobOrchestrator(session, configured)._run_nfl_foundation_sync()
        assert receipt["status"] == "FAILED"
        assert receipt["attempted"] is True
        assert receipt["exit_code"] == 1
        assert receipt["timed_out"] is False
        assert "boom" in receipt["stderr_tail"]
        # Freshness is still reported, so a reader can see what the failed run
        # had found before it gave up.
        assert receipt["source_freshness"]["warehouse_advanced"] is False

    @pytest.mark.asyncio
    async def test_a_zero_exit_is_recorded_as_succeeded_and_names_its_owner(
        self, session, settings, tmp_path, monkeypatch,
    ):
        root = tmp_path / "studio"
        root.mkdir()
        python = root / "python.exe"
        python.write_text("", encoding="utf-8")

        class _Proc:
            returncode = 0

            async def communicate(self):
                return (json.dumps(REPORT).encode(), b"")

        async def fake_exec(*args, **kwargs):
            return _Proc()

        monkeypatch.setattr(
            "app.jobs.orchestrator.asyncio.create_subprocess_exec", fake_exec)
        configured = settings.model_copy(update={
            "nfl_model_studio_root": str(root),
            "nfl_model_studio_python": str(python),
        })
        receipt = await JobOrchestrator(session, configured)._run_nfl_foundation_sync()
        assert receipt["status"] == "SUCCEEDED"
        assert receipt["exit_code"] == 0
        assert "sync-nfl-foundation-sources" in receipt["owner"]
        assert receipt["source_freshness"]["report_status"] == "COMPLETE"


class TestProducerCodeGuardScope:
    def test_the_scope_is_versioned(self):
        assert NFL_PRODUCER_CODE_SCOPE_VERSION == "AISP_NFL_PRODUCER_CODE_SCOPE_V2"

    def test_it_covers_every_entry_point_that_produces_an_imported_forecast(self):
        for path in (
            "scripts/run_nfl_2026_prospective_shadow.py",
            "scripts/publish_nfl_rolling_shadow.py",
            "scripts/sync_nfl_current_season.py",
        ):
            assert path in NFL_PRODUCER_CODE_PATHS

    def test_it_covers_the_packages_those_entry_points_import(self):
        for path in (
            "server/sports_nfl_m1",
            "server/sports_nfl",
            "server/sports_cockpit/interprocess_lock.py",
        ):
            assert path in NFL_PRODUCER_CODE_PATHS

    def test_it_is_still_not_the_whole_repository(self):
        """Scoping to the repo is what silently stopped NFL forecasting.

        Ordinary MLB work rewrites tracked output artifacts in the shared
        monorepo; every run from 2026-09-11 failed on "producer code identity is
        not frozen" while every NFL producer file was untouched.
        """

        assert "" not in NFL_PRODUCER_CODE_PATHS
        assert "." not in NFL_PRODUCER_CODE_PATHS
        assert all(path.strip() for path in NFL_PRODUCER_CODE_PATHS)
