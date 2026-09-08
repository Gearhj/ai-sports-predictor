from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.jobs.odds_quota_recovery import PROBE_ENDPOINT_KEY, quota_state
from app.core.logging import logger
from app.core.time import local_day_bounds, local_today
from app.analytics import ParquetSnapshotWriter
from app.bankroll.settlement import settle_completed_positions
from app.bankroll.positions import publish_official_positions
from app.data_sources import BaseballSavantStatcastAdapter, DemoSportsSource, MLBStatsAPIAdapter, OpenMeteoAdapter, SourceError, TheOddsAPIAdapter
from app.data_sources.base import (
    AdapterResponse,
    BullpenAvailabilityDTO,
    LineupDTO,
    OddsEventDTO,
    OddsScoreEventDTO,
    RosterAvailabilityDTO,
    ScheduleGameDTO,
    SourceEnvelope,
    TeamStatsDTO,
    WeatherDTO,
)
from app.data_sources.fixtures import demo_games
from app.ingestion import CanonicalNormalizer, RawArchive
from app.models import (
    AuditEvent,
    ExternalIdMapping,
    Forecast,
    ForecastTrack,
    Game,
    GameStatus,
    GameTeam,
    IngestionRun,
    JobRun,
    MarketOutcome,
    ModelVersion,
    OddsSnapshot,
    ProviderRequestLedger,
    RunStatus,
    Team,
    Venue,
    ShadowBoardDecision,
    ShadowForecast,
    ShadowForecastBundle,
)
from app.services.forecast_service import ForecastNotReadyError, ForecastService, ForecastWindowClosedError
from app.services.read_service import ReadService


# Starter-evidence heartbeat: the availability poll persists the verified
# probable-pitcher state for a due game when its latest starter observation is
# older than this many minutes. Inside the pregame fast window (2-minute
# polls) this bounds the freshness authority's lag to ~10 minutes; outside it
# the 10-minute slow poll and the 30-minute schedule refresh already suffice.
STARTER_HEARTBEAT_MINUTES = 10
STARTER_HEARTBEAT_VERSION = "AISP_STARTER_HEARTBEAT_V1"

AVAILABLE_JOBS = {
    "refresh-schedule": "Refresh Today's Schedule",
    "refresh-mlb-data": "Refresh MLB Data",
    "refresh-statcast": "Refresh Statcast",
    "refresh-odds": "Refresh Odds",
    "refresh-weather": "Refresh Weather",
    "rebuild-features": "Rebuild Features",
    "generate-forecasts": "Generate Forecasts",
    "run-simulations": "Run Simulations",
    "publish-official-positions": "Publish Official Virtual Positions",
    "settle-games": "Settle Completed Games",
    "recalculate-performance": "Recalculate Performance",
    "daily-pipeline": "Daily Pipeline",
    "finalize-games": "Refresh Finals, Settle Positions, Recalculate Performance",
    "controlled-live-validation": "Controlled Gate 2 Live Validation",
    "import-model-studio-forecast-bundle": "Import Model Studio Forecast Bundle",
    "evaluate-shadow-betting-board": "Evaluate Research-Shadow Betting Board",
    "settle-shadow-decisions": "Settle Research-Shadow Decisions",
    "refresh-nfl-shadow-schedule": "Refresh NFL Research-Shadow Schedule Authority",
    "materialize-current-nfl-features": "Verify Current NFL Feature Vectors",
    "generate-nfl-shadow-forecasts": "Generate NFL M1 Early/Current Research-Shadow Forecasts",
    "generate-nfl-final-pregame-forecasts": "Generate NFL M1 Final-Pregame Research-Shadow Forecasts",
    "import-nfl-shadow-forecast-bundle": "Import NFL M1 Research-Shadow Forecast Bundle",
    "refresh-nfl-shadow-odds": "Refresh NFL Research-Shadow Odds",
    "evaluate-nfl-shadow-board": "Evaluate NFL Research-Shadow Board",
    "freeze-nfl-final-pregame": "Freeze NFL Final-Pregame Research Shadow",
    "finalize-nfl-shadow-games": "Finalize NFL Research-Shadow Games",
    "settle-nfl-shadow-decisions": "Settle NFL Research-Shadow Decisions",
    "recalculate-nfl-shadow-performance": "Recalculate NFL Prospective Performance",
    "poll-availability-events": "Poll MLB Availability Changes (Live Shadow)",
    "freeze-shadow-experiment": "Freeze $1,000 Shadow Experiment Decisions",
    "settle-shadow-experiment": "Settle $1,000 Shadow Experiment Positions",
    "register-practical-experiment-v2": "Register Practical Pregame Challenger (V2, prospective)",
    "discover-prediction-markets": "Discover Polymarket + Kalshi Sports Markets",
    "refresh-prediction-market-snapshots": "Refresh Prediction-Market Snapshots",
    "compute-prediction-market-consensus": "Compute Prediction-Market Consensus",
    "evaluate-prediction-market-alerts": "Evaluate Prediction-Market Alerts",
    "finalize-prediction-market-pregame": "Finalize Prediction-Market Pregame",
    "capture-prediction-market-pregame-cuts": "Capture Scheduled Pregame Cuts (T-6h/T-2h/T-30m/T-5m)",
    "backfill-prediction-market-history": "Backfill Prediction-Market History (bounded)",
}

ALIASES = {
    "schedule": "refresh-schedule", "refresh-todays-schedule": "refresh-schedule",
    "mlb-data": "refresh-mlb-data", "statcast": "refresh-statcast", "odds": "refresh-odds",
    "weather": "refresh-weather", "features": "rebuild-features", "forecasts": "generate-forecasts",
    "simulations": "run-simulations", "settlement": "settle-games", "performance": "recalculate-performance",
    "positions": "publish-official-positions",
}


class JobOrchestrator:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.archive = RawArchive(settings.raw_data_root)
        self.normalizer = CanonicalNormalizer(session)
        self.demo = DemoSportsSource()
        self._active_job_run_id: Any | None = None

    async def run(self, name: str, *, requested_by_user_id: Any | None = None, event_date: date | None = None) -> JobRun:
        canonical = _job_key(name)
        if canonical not in AVAILABLE_JOBS:
            raise ValueError(f"Unknown job '{name}'. Available jobs: {', '.join(AVAILABLE_JOBS)}")
        event_date = event_date or local_today(self.settings.display_timezone)
        run = JobRun(
            job_name=canonical,
            status=RunStatus.RUNNING,
            requested_by_user_id=requested_by_user_id,
            started_at=datetime.now(UTC),
            parameters={"event_date": event_date.isoformat()},
        )
        self.session.add(run)
        self.session.commit()
        started = perf_counter()
        try:
            self._active_job_run_id = run.id
            result = await self._execute(canonical, event_date)
            self._active_job_run_id = None
            run = self.session.get(JobRun, run.id)
            assert run is not None
            run.status = RunStatus.SUCCEEDED
            run.completed_at = datetime.now(UTC)
            run.result = {**result, "duration_seconds": round(perf_counter() - started, 3)}
            self.session.add(AuditEvent(
                actor_user_id=requested_by_user_id,
                action=f"job.{canonical}.succeeded",
                entity_type="job_run",
                entity_id=run.id,
                metadata_json=run.result,
            ))
            self.session.commit()
            logger.info("job_completed", job_id=str(run.id), job=canonical, **result)
            return run
        except Exception as exc:
            self._active_job_run_id = None
            self.session.rollback()
            run = self.session.get(JobRun, run.id)
            assert run is not None
            run.status = RunStatus.FAILED
            run.completed_at = datetime.now(UTC)
            run.error_message = str(exc)[:2000]
            run.result = {"duration_seconds": round(perf_counter() - started, 3)}
            self.session.add(AuditEvent(
                actor_user_id=requested_by_user_id,
                action=f"job.{canonical}.failed",
                entity_type="job_run",
                entity_id=run.id,
                metadata_json={"error": type(exc).__name__},
            ))
            self.session.commit()
            logger.error("job_failed", job_id=str(run.id), job=canonical, error=str(exc))
            raise

    async def _execute(self, name: str, event_date: date) -> dict[str, Any]:
        handlers: dict[str, Callable[[date], Awaitable[dict[str, Any]]]] = {
            "refresh-schedule": self.refresh_schedule,
            "refresh-mlb-data": self.refresh_mlb_data,
            "refresh-statcast": self.refresh_statcast,
            "refresh-odds": self.refresh_odds,
            "refresh-weather": self.refresh_weather,
            "rebuild-features": self.rebuild_features,
            "generate-forecasts": self.generate_forecasts,
            "run-simulations": self.generate_forecasts,
            "publish-official-positions": self.publish_positions,
            "settle-games": self.settle_games,
            "recalculate-performance": self.recalculate_performance,
            "daily-pipeline": self.daily_pipeline,
            "finalize-games": self.finalize_games,
            "controlled-live-validation": self.controlled_live_validation,
            "import-model-studio-forecast-bundle": self.import_forecast_bundle,
            "evaluate-shadow-betting-board": self.evaluate_shadow_board,
            "settle-shadow-decisions": self.settle_shadow_decisions,
            "refresh-nfl-shadow-schedule": self.refresh_nfl_shadow_schedule,
            "materialize-current-nfl-features": self.materialize_current_nfl_features,
            "generate-nfl-shadow-forecasts": self.generate_nfl_shadow_forecasts,
            "generate-nfl-final-pregame-forecasts": self.generate_nfl_final_pregame_forecasts,
            "import-nfl-shadow-forecast-bundle": self.import_nfl_shadow_bundle,
            "refresh-nfl-shadow-odds": self.refresh_nfl_shadow_odds,
            "evaluate-nfl-shadow-board": self.evaluate_nfl_shadow_board,
            "freeze-nfl-final-pregame": self.freeze_nfl_final_pregame,
            "finalize-nfl-shadow-games": self.finalize_nfl_shadow_games,
            "settle-nfl-shadow-decisions": self.settle_nfl_shadow_decisions,
            "recalculate-nfl-shadow-performance": self.recalculate_nfl_shadow_performance,
            "poll-availability-events": self.poll_availability_events,
            "discover-prediction-markets": self._pm_discover,
            "refresh-prediction-market-snapshots": self._pm_refresh,
            "compute-prediction-market-consensus": self._pm_consensus,
            "evaluate-prediction-market-alerts": self._pm_alerts,
            "finalize-prediction-market-pregame": self._pm_finalize,
            "capture-prediction-market-pregame-cuts": self._pm_capture_cuts,
            "backfill-prediction-market-history": self._pm_backfill,
            "freeze-shadow-experiment": self.freeze_shadow_experiment,
            "settle-shadow-experiment": self.settle_shadow_experiment,
            "register-practical-experiment-v2": self.register_practical_experiment_v2,
        }
        return await handlers[name](event_date)

    # -- Prediction Market Observer jobs (provider I/O confined here) -----
    def _pm_jobs(self):
        from app.prediction_markets.jobs import PredictionMarketJobs

        return PredictionMarketJobs(self.session, self.settings)

    async def _pm_discover(self, event_date: date) -> dict[str, Any]:
        return await self._pm_jobs().discover(event_date)

    async def _pm_refresh(self, event_date: date) -> dict[str, Any]:
        return await self._pm_jobs().refresh(event_date)

    async def _pm_consensus(self, event_date: date) -> dict[str, Any]:
        return await self._pm_jobs().compute_consensus(event_date)

    async def _pm_alerts(self, event_date: date) -> dict[str, Any]:
        return await self._pm_jobs().evaluate_alerts(event_date)

    async def _pm_finalize(self, event_date: date) -> dict[str, Any]:
        return await self._pm_jobs().finalize_pregame(event_date)

    async def _pm_capture_cuts(self, event_date: date) -> dict[str, Any]:
        return await self._pm_jobs().capture_pregame_cuts(event_date)

    async def _pm_backfill(self, event_date: date) -> dict[str, Any]:
        return await self._pm_jobs().backfill(event_date)

    def _persist_starter_heartbeats(
        self, due_games: dict[Any, Any], by_source: dict[str, Any], game_source_ids: dict[Any, str],
        fetched_at: datetime, now: datetime,
    ) -> int:
        """Persist the poll-verified starter state for due games whose latest
        starter observation is older than STARTER_HEARTBEAT_MINUTES.

        Uses the same governed normalizer path as the schedule refresh, with
        the poll's fetch time as ``available_as_of``. Never fabricates: only
        games present in the fetched schedule payload are written."""
        from app.models import StarterObservation

        candidates: list[ScheduleGameDTO] = []
        threshold = now - timedelta(minutes=STARTER_HEARTBEAT_MINUTES)
        for game in due_games.values():
            source_id = game_source_ids.get(game.id)
            item = by_source.get(str(source_id)) if source_id is not None else None
            if item is None:
                continue
            latest = self.session.scalar(
                select(func.max(StarterObservation.available_as_of)).where(StarterObservation.game_id == game.id)
            )
            if latest is not None:
                latest = latest if latest.tzinfo else latest.replace(tzinfo=UTC)
                if latest > threshold:
                    continue
            try:
                candidates.append(ScheduleGameDTO.model_validate(item))
            except Exception:  # a malformed row must never abort the poll
                continue
        if not candidates:
            return 0
        persisted = self.normalizer.ingest_schedule(candidates, "MLB_STATS_API", available_as_of=fetched_at)
        return len(persisted)

    async def poll_availability_events(self, event_date: date) -> dict[str, Any]:
        """Automatic MLB availability poll: detect real probable-pitcher/lineup changes.

        Bounded by per-game cadence (fast inside the pregame window, slow outside;
        never polls a started game). Uses the free MLB Stats API for schedule and
        lineup state; it makes no Odds API request and fabricates no event.
        """
        from app.services.live_shadow_service import GameAvailabilitySnapshot, LiveShadowOperator

        operator = LiveShadowOperator(self.session, self.settings)
        now = datetime.now(UTC)
        due = operator.due_game_ids(now)
        if not due:
            return {"records": 0, "polled_games": 0, "events_emitted": 0, "note": "no games due for polling"}

        due_games = {game.id: game for game in operator.monitored_games(now) if game.id in due}
        # Resolve each game's MLB source id from the identity mapping — Game has no
        # source-id column, so without this the poll cannot match schedule/lineups.
        game_source_ids = {
            mapping.entity_id: mapping.external_id
            for mapping in self.session.scalars(
                select(ExternalIdMapping).where(
                    ExternalIdMapping.entity_type == "game",
                    ExternalIdMapping.source == "MLB_STATS_API",
                    ExternalIdMapping.entity_id.in_(list(due_games.keys())),
                )
            )
        }
        adapter = MLBStatsAPIAdapter()
        response = await adapter.get_schedule(event_date)
        fetched_at = response.envelope.fetched_at
        by_source = {str(item.get("source_game_id")): item for item in response.envelope.data}
        payload_id = f"mlb-schedule:{fetched_at.isoformat()}"
        fast_window = timedelta(hours=self.settings.pregame_fast_window_hours)

        snapshots: list[Any] = []
        lineups_persisted = 0
        for game in due_games.values():
            source_id = game_source_ids.get(game.id)
            if source_id is None:
                continue
            item = by_source.get(str(source_id))
            if item is None:
                continue
            home = (item.get("home_pitcher") or {}).get("source_id")
            away = (item.get("away_pitcher") or {}).get("source_id")
            probable = f"{home or '-'}|{away or '-'}"
            home_lineup = None
            starts = game.starts_at if game.starts_at.tzinfo else game.starts_at.replace(tzinfo=UTC)
            if starts - now <= fast_window:
                try:
                    rows = (await adapter.get_game_lineups(str(source_id))).envelope.data
                    # Persist the full lineup payload (not just a status) so the
                    # board reflects the confirmed lineup once MLB publishes it.
                    lineups_persisted += self.normalizer.ingest_lineups(
                        [LineupDTO.model_validate(row) for row in rows], "MLB_STATS_API",
                    )
                    home_lineup = next((row.get("status") for row in rows), None)
                except SourceError:
                    home_lineup = None
            snapshots.append(GameAvailabilitySnapshot(
                game_id=game.id, probable_pitcher_id=probable,
                home_lineup_status=home_lineup, away_lineup_status=home_lineup,
                source_payload_id=payload_id, published_at=fetched_at,
            ))
        # Heartbeat starter observations: the poll has just VERIFIED the
        # source's current probable pitcher for every due game, but a diff-only
        # poll discarded that verification when nothing changed, so the
        # governed freshness authority (latest available_as_of) advanced only
        # at the 30-minute schedule-refresh cadence. Persist the verified state
        # for due games whose latest observation is older than the heartbeat
        # interval — bounded, idempotent (unique on available_as_of+checksum),
        # and using the poll's own fetch time as the source timestamp.
        heartbeats = self._persist_starter_heartbeats(due_games, by_source, game_source_ids, fetched_at, now)
        if lineups_persisted or heartbeats:
            self.session.commit()
        result = operator.poll(snapshots, now=now)
        result["lineups_persisted"] = lineups_persisted
        result["starter_heartbeats"] = heartbeats
        # Honest run record for the MLB part of the poll (previously only the
        # SportsDataIO part recorded a run, so the poll was invisible to the
        # ingestion-run audit and to freshness monitoring).
        self._ingestion_run(
            SourceEnvelope(
                source="MLB_STATS_API", source_mode="LIVE", fetched_at=fetched_at, data=[],
                metadata={"polled_games": result.get("polled_games", 0), "due_games": len(due_games),
                          "events_emitted": result.get("events_emitted", 0), "rescores": result.get("rescores", 0),
                          "lineups_persisted": lineups_persisted, "starter_heartbeats": heartbeats,
                          "cadence_version": STARTER_HEARTBEAT_VERSION},
            ),
            None, heartbeats + lineups_persisted, "poll-availability-events",
        )
        self.session.commit()
        injuries = await self._poll_sportsdataio_injuries(event_date, now)
        return {"records": result["events_emitted"] + injuries.get("events_created", 0), **result, "sportsdataio": injuries}

    async def _poll_sportsdataio_injuries(self, event_date: date, now: datetime) -> dict[str, Any]:
        """Fetch the SportsDataIO injuries feed, detect trial/scrambled data, run
        stateful change detection against the prior snapshot, and route material
        changes into the existing rescore pipeline. One API call per poll; the key
        is never logged; a provider failure fabricates nothing.
        """
        from app.prospective.contracts import SPORTSDATAIO_SOURCE
        from app.prospective.sportsdataio import (
            INJURED_PLAYERS_PATH,
            SportsDataIOAvailabilityAdapter,
            plan_injury_poll,
        )
        from app.services.prospective_shadow_service import ProspectiveShadowService, RescoreError

        adapter = SportsDataIOAvailabilityAdapter(self.settings)
        if not adapter.is_configured():
            return {"configured": False, "records": 0, "events_created": 0}

        started = datetime.now(UTC)
        http_status, rows = await adapter.fetch_injuries()
        run = IngestionRun(
            source="SPORTSDATAIO", job_name="poll-availability-events",
            status=RunStatus.RUNNING, started_at=started,
        )
        self.session.add(run)
        self.session.flush()
        if http_status != 200:
            run.status = RunStatus.FAILED
            run.completed_at = datetime.now(UTC)
            run.error_message = f"SportsDataIO InjuredPlayers HTTP {http_status}"
            run.source_metadata = {"endpoint": INJURED_PLAYERS_PATH, "http_status": http_status, "error": True}
            self.session.commit()
            return {"configured": True, "http_status": http_status, "error": True, "records": 0, "events_created": 0}

        prev_run = self.session.scalar(
            select(IngestionRun).where(
                IngestionRun.source == "SPORTSDATAIO", IngestionRun.status == RunStatus.SUCCEEDED,
            ).order_by(IngestionRun.completed_at.desc()).limit(1)
        )
        prev_snapshot = (prev_run.source_metadata or {}).get("snapshot") if prev_run else None
        plan = plan_injury_poll(rows, prev_snapshot, published_at=now)

        events_created = matched = games_rescored = 0
        for event in plan.candidate_events:
            game_id = self._resolve_injury_game(event, event_date)
            if game_id is None:
                continue  # no safe identity match (e.g. trial data) → never forced
            matched += 1
            event.game_id = str(game_id)
            try:
                outcome = ProspectiveShadowService(self.session, self.settings).process_event(
                    event, source=SPORTSDATAIO_SOURCE, now=now,
                )
            except RescoreError:
                continue
            events_created += 1
            games_rescored += outcome.get("games_rescored", 0)

        run.status = RunStatus.SUCCEEDED
        run.completed_at = datetime.now(UTC)
        run.records_processed = plan.records
        run.source_metadata = {
            "endpoint": INJURED_PLAYERS_PATH, "http_status": 200, "trial": plan.trial,
            "changes": len(plan.candidate_events), "events_created": events_created,
            "matched": matched, "games_rescored": games_rescored, "snapshot": plan.snapshot,
        }
        self.session.commit()
        return {
            "configured": True, "trial": plan.trial, "records": plan.records,
            "changes": len(plan.candidate_events), "events_created": events_created,
            "matched": matched, "games_rescored": games_rescored,
        }

    def _resolve_injury_game(self, event, event_date: date):
        """Resolve an injury event to a live game today via the affected team.

        Returns None when identity cannot be safely established — e.g. trial data
        blanks the team, so no game is ever forced.
        """
        team_abbrev = (event.team_source_id or "").strip()
        if not team_abbrev:
            return None
        team = self.session.scalar(
            select(Team).where(func.upper(Team.abbreviation) == team_abbrev.upper())
        )
        if team is None:
            return None
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        game = self.session.scalar(
            select(Game)
            .join(GameTeam, GameTeam.game_id == Game.id)
            .where(
                GameTeam.team_id == team.id, Game.source_mode == "LIVE",
                Game.starts_at >= start, Game.starts_at < end,
            )
            .limit(1)
        )
        return game.id if game else None

    async def import_forecast_bundle(self, event_date: date) -> dict[str, Any]:
        """Import the configured immutable forecast bundle. Idempotent; no provider call."""
        from pathlib import Path

        from app.services.shadow_board_service import ShadowBoardService

        directory = self.settings.shadow_bundle_import_dir.strip()
        if not directory:
            return {"records": 0, "note": "SHADOW_BUNDLE_IMPORT_DIR is not configured"}
        result = ShadowBoardService(self.session, self.settings).import_bundle(Path(directory))
        return {"records": int(result.get("forecasts", 0)), **result}

    async def import_nfl_shadow_bundle(self, event_date: date) -> dict[str, Any]:
        """Import the configured verified NFL bundle family; no provider call.

        The mutable index is navigation only. Each listed content-addressed
        object is independently verified before it can reach persistence.
        Unlisted objects (including the protected legacy MLB bundle in the
        shared family) are never opened.
        """
        from pathlib import Path

        from app.forecast_exchange import verify_nfl_shadow_bundle_index
        from app.services.shadow_board_service import ShadowBoardService

        directory = self.settings.nfl_shadow_bundle_import_dir.strip()
        if not directory:
            return {"records": 0, "note": "NFL_SHADOW_BUNDLE_IMPORT_DIR is not configured"}
        configured = Path(directory)
        if (configured / "consumer-import-fixture.json").is_file():
            result = ShadowBoardService(self.session, self.settings).import_bundle(configured)
            if "NFL_M1_XGBOOST" not in result.get("engines", ["NFL_M1_XGBOOST"]):
                raise ValueError("Configured NFL bundle did not import the NFL M1 engine")
            return {
                "records": int(result.get("forecasts", 0)),
                "selected_bundles": 1,
                "unlisted_objects_skipped": 0,
                **result,
            }

        locator = verify_nfl_shadow_bundle_index(configured)
        historical_addresses = tuple(locator.index.historical_addresses)
        current_addresses = tuple(locator.index.current_addresses)
        selected_addresses = (*historical_addresses, *current_addresses)
        selected = set(selected_addresses)
        # Count directory names only; unlisted objects are deliberately not
        # opened, parsed, or used as authority.
        address_names = {
            child.name for child in locator.sha256_root.iterdir()
            if len(child.name) == 64 and all(ch in "0123456789abcdef" for ch in child.name)
        }
        results: list[dict[str, Any]] = []
        service = ShadowBoardService(self.session, self.settings)
        for address in selected_addresses:
            object_dir = locator.sha256_root / address
            result = service.import_bundle(object_dir)
            if result.get("bundle_sha256") != address:
                raise ValueError(f"NFL bundle import returned a different address for {address}")
            if "NFL_M1_XGBOOST" not in result.get("engines", ["NFL_M1_XGBOOST"]):
                raise ValueError(f"NFL bundle {address} did not import the NFL M1 engine")
            results.append(result)
        return {
            "records": sum(int(row.get("forecasts", 0)) for row in results),
            "selected_bundles": len(results),
            "current_bundles": len(current_addresses),
            "historical_bundles": len(historical_addresses),
            "imported_bundles": sum(not bool(row.get("idempotent_reuse")) for row in results),
            "reused_bundles": sum(bool(row.get("idempotent_reuse")) for row in results),
            "forecast_rows_created": sum(int(row.get("forecast_rows_created", 0)) for row in results),
            "forecast_children_reused": sum(
                int(row.get("forecast_children_reused", row.get("forecasts", 0)))
                if row.get("idempotent_reuse") else int(row.get("forecast_children_reused", 0))
                for row in results
            ),
            "unlisted_objects_skipped": len(address_names - selected),
            "locator_schema": locator.index.schema_version,
            "locator_current_list_sha256": getattr(
                locator.index, "current_bundle_list_sha256",
                getattr(locator.index, "bundle_list_sha256", None),
            ),
            "locator_historical_list_sha256": getattr(
                locator.index, "historical_bundle_list_sha256", None
            ),
            "unlisted_objects_opened": False,
        }

    async def refresh_nfl_shadow_schedule(self, event_date: date) -> dict[str, Any]:
        """Verify schedule identities already carried by immutable NFL imports."""
        rows = list(self.session.scalars(
            select(ShadowForecast)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(
                ShadowForecastBundle.sport == "NFL",
                ShadowForecast.event_date == event_date,
                ShadowForecast.canonical_game_id.is_not(None),
            )
        ))
        games = {row.canonical_game_id for row in rows}
        return {"records": len(games), "games": len(games), "source": "VERIFIED_MODEL_STUDIO_EXCHANGE"}

    async def materialize_current_nfl_features(self, event_date: date) -> dict[str, Any]:
        """Verify persisted feature hashes; feature computation stays in Model Studio."""
        rows = list(self.session.scalars(
            select(ShadowForecast)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(
                ShadowForecastBundle.sport == "NFL",
                ShadowForecast.event_date == event_date,
            )
        ))
        valid = [row for row in rows if row.feature_vector_sha256 and len(row.feature_vector_sha256) == 64]
        return {
            "records": len(valid), "forecasts": len(rows),
            "status": "VERIFIED_IMPORTED_FEATURE_EVIDENCE",
            "missing_feature_hashes": len(rows) - len(valid),
        }

    async def generate_nfl_shadow_forecasts(self, event_date: date) -> dict[str, Any]:
        """Run the frozen Model Studio producer, then verify/import its exchange.

        This is deliberately a local process boundary rather than a second
        integration channel. The child receives a fixed argument vector (never
        a shell command), and its declared lineage must match both the configured
        Model Studio HEAD and the independently verified exchange locator.
        """

        return await self._generate_and_import_nfl_shadow(
            event_date, snapshot_types=("EARLY", "CURRENT")
        )

    async def generate_nfl_final_pregame_forecasts(self, event_date: date) -> dict[str, Any]:
        """Generate only the producer-governed 15–30 minute FINAL snapshot."""

        return await self._generate_and_import_nfl_shadow(
            event_date, snapshot_types=("FINAL_PREGAME",)
        )

    async def _generate_and_import_nfl_shadow(
        self, event_date: date, *, snapshot_types: tuple[str, ...]
    ) -> dict[str, Any]:
        if not self.settings.nfl_shadow_enabled or not self.settings.nfl_shadow_producer_enabled:
            return {
                "records": 0,
                "status": "DISABLED",
                "producer_called": False,
                "snapshot_types": list(snapshot_types),
            }
        producer = await _invoke_nfl_shadow_producer(self.settings, snapshot_types)
        if producer["status"] == "NO_ELIGIBLE_UPCOMING_GAMES":
            return {
                "records": 0,
                "status": producer["status"],
                "producer_called": True,
                "snapshot_types": list(snapshot_types),
                "games": 0,
                "imported": False,
            }

        # Navigation metadata is not scientific authority. Re-read and validate
        # the locator here, then import every listed object through the strict
        # content-addressed verifier.
        from app.forecast_exchange import verify_nfl_shadow_bundle_index

        configured = Path(self.settings.nfl_shadow_bundle_import_dir).resolve()
        locator = verify_nfl_shadow_bundle_index(configured)
        current_list_sha = locator.index.current_bundle_list_sha256
        historical_list_sha = getattr(locator.index, "historical_bundle_list_sha256", None)
        if current_list_sha != producer["exchangeLocatorCurrentBundleListSha256"]:
            raise ValueError("Model Studio stdout and verified current locator authority differ")
        if historical_list_sha != producer["exchangeLocatorHistoricalBundleListSha256"]:
            raise ValueError("Model Studio stdout and verified historical locator authority differ")
        if len(locator.index.current_addresses) != producer["exchangeLocatorCurrentBundleCount"]:
            raise ValueError("Model Studio stdout and verified current locator count differ")

        imported = await self.import_nfl_shadow_bundle(event_date)
        return {
            "records": int(imported.get("records", 0)),
            "status": "ACTIVE",
            "producer_called": True,
            "snapshot_types": list(snapshot_types),
            "producer_revision": producer["producerRevision"],
            "games": int(producer["games"]),
            "forecasts_created": int(producer["forecastsCreated"]),
            "feature_vectors": int(producer["featureVectorCount"]),
            "current_forecasts": int(producer["exchangeLocatorCurrentForecastCount"]),
            "model_manifest_sha256": producer["modelManifestSha256"],
            "model_binary_sha256": producer["modelBinarySha256"],
            "feature_contract_sha256": producer["featureContractSha256"],
            "models_fit": 0,
            "models_promoted": 0,
            "market_fields_read": False,
            "import": imported,
        }

    async def refresh_nfl_shadow_odds(self, event_date: date) -> dict[str, Any]:
        """One quota-governed current-NFL Odds API request and exact-ID ingest."""
        if not self.settings.nfl_shadow_enabled or not self.settings.nfl_shadow_odds_enabled:
            return {"records": 0, "status": "DISABLED", "provider_called": False}
        if not self.settings.the_odds_api_key:
            raise SourceError("THE_ODDS_API", "THE_ODDS_API_KEY is not configured", retryable=False)
        self._enforce_odds_quota_policy()
        now = datetime.now(UTC)
        ledger = self._reserve_provider_request(
            "THE_ODDS_API", "americanfootball_nfl_odds",
            controlled_live_request=False,
            request_shape={
                "sport_key": "americanfootball_nfl",
                "regions": self.settings.odds_regions,
                "markets": sorted(self.settings.odds_market_list),
                "request_window": int(
                    now.timestamp() // (max(1, self.settings.odds_pregame_refresh_minutes) * 60)
                ),
            },
        )
        self.session.commit()
        try:
            response = await TheOddsAPIAdapter(
                self.settings.the_odds_api_key,
                self.settings.odds_market_list,
                self.settings.odds_regions,
                sport_key="americanfootball_nfl",
            ).get_market_snapshot()
            self.archive.write(response.envelope.source, response.raw_payload)
            events = [OddsEventDTO.model_validate(item) for item in response.envelope.data]
            from app.services.nfl_shadow_odds_service import NFLShadowOddsService

            result = NFLShadowOddsService(self.session).ingest(
                events, available_as_of=response.envelope.fetched_at
            )
            metadata = response.envelope.metadata or {}
            self.session.add(ProviderRequestLedger(
                provider=ledger.provider, endpoint_key=ledger.endpoint_key,
                requested_at=ledger.requested_at, completed_at=datetime.now(UTC),
                status="SUCCEEDED", request_fingerprint=ledger.request_fingerprint,
                quota_cost=_int_or_none(metadata.get("requests_last")),
                quota_remaining=_int_or_none(metadata.get("requests_remaining")),
                records_received=len(events), controlled_live_request=False,
                source_mode="LIVE", job_run_id=self._active_job_run_id,
                reservation_id=ledger.id,
                response_metadata={"sport_key": "americanfootball_nfl", **result},
            ))
            self.session.commit()
            return {"records": result["quotes"], "provider_called": True, "quota": metadata, **result}
        except Exception as exc:
            self.session.rollback()
            self.session.add(ProviderRequestLedger(
                provider=ledger.provider, endpoint_key=ledger.endpoint_key,
                requested_at=ledger.requested_at, completed_at=datetime.now(UTC),
                status="FAILED", request_fingerprint=ledger.request_fingerprint,
                quota_cost=None, quota_remaining=None, records_received=None,
                controlled_live_request=False, source_mode="LIVE",
                job_run_id=self._active_job_run_id, reservation_id=ledger.id,
                error_type=type(exc).__name__,
                response_metadata={"sport_key": "americanfootball_nfl", "response_received": False},
            ))
            self.session.commit()
            raise

    async def evaluate_nfl_shadow_board(self, event_date: date) -> dict[str, Any]:
        from app.services.shadow_board_service import ShadowBoardService

        result = ShadowBoardService(self.session, self.settings).evaluate_board(event_date, sport="NFL")
        return {"records": int(result.get("decisions", 0)), **result}

    async def freeze_nfl_final_pregame(self, event_date: date) -> dict[str, Any]:
        """Evaluate only already-frozen FINAL_PREGAME snapshots; provider-free."""
        from app.services.shadow_board_service import ShadowBoardService

        final_ids = set(self.session.scalars(
            select(ShadowForecast.id)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(
                ShadowForecastBundle.sport == "NFL",
                ShadowForecast.event_date == event_date,
                ShadowForecast.snapshot_type == "FINAL_PREGAME",
            )
        ))
        if not final_ids:
            return {"records": 0, "forecasts": 0, "status": "NO_FINAL_PREGAME_FORECAST"}
        result = ShadowBoardService(self.session, self.settings).evaluate_board(
            event_date, sport="NFL", snapshot_type="FINAL_PREGAME"
        )
        return {"records": int(result.get("decisions", 0)), "final_forecasts": len(final_ids), **result}

    async def finalize_nfl_shadow_games(self, event_date: date) -> dict[str, Any]:
        """Acquire and persist exact NFL finals through the existing Odds API.

        Only provider IDs already bound by exact home/away/kickoff odds mapping
        can update a research-shadow Game. This path cannot address LIVE MLB.
        """
        provider_result: dict[str, Any] = {"provider_called": False}
        today = local_today(self.settings.display_timezone)
        request_eligible = today - timedelta(days=3) <= event_date <= today
        if (
            self.settings.nfl_shadow_enabled
            and self.settings.nfl_shadow_odds_enabled
            and self.settings.the_odds_api_key
            and request_eligible
        ):
            self._enforce_odds_quota_policy()
            now = datetime.now(UTC)
            ledger = self._reserve_provider_request(
                "THE_ODDS_API", "americanfootball_nfl_scores",
                controlled_live_request=False,
                request_shape={
                    "sport_key": "americanfootball_nfl",
                    "days_from": 3,
                    "event_date": event_date.isoformat(),
                    "request_window": int(now.timestamp() // 3600),
                },
            )
            self.session.commit()
            try:
                response = await TheOddsAPIAdapter(
                    self.settings.the_odds_api_key,
                    sport_key="americanfootball_nfl",
                ).get_scores(days_from=3)
                archive = self.archive.write(response.envelope.source, response.raw_payload)
                events = [OddsScoreEventDTO.model_validate(item) for item in response.envelope.data]
                from app.services.nfl_shadow_odds_service import NFLShadowOddsService

                ingested = NFLShadowOddsService(self.session).ingest_scores(
                    events,
                    available_as_of=response.envelope.fetched_at,
                    event_date=event_date,
                )
                metadata = response.envelope.metadata or {}
                self._ingestion_run(
                    response.envelope,
                    archive.raw_file_reference,
                    int(ingested["final_games"]),
                    "finalize-nfl-shadow-games",
                )
                self.session.add(ProviderRequestLedger(
                    provider=ledger.provider, endpoint_key=ledger.endpoint_key,
                    requested_at=ledger.requested_at, completed_at=datetime.now(UTC),
                    status="SUCCEEDED", request_fingerprint=ledger.request_fingerprint,
                    quota_cost=_int_or_none(metadata.get("requests_last")),
                    quota_remaining=_int_or_none(metadata.get("requests_remaining")),
                    records_received=len(events), controlled_live_request=False,
                    source_mode="LIVE", job_run_id=self._active_job_run_id,
                    reservation_id=ledger.id,
                    response_metadata={"sport_key": "americanfootball_nfl", **ingested},
                ))
                self.session.commit()
                provider_result = {
                    "provider_called": True,
                    "provider_events": len(events),
                    "provider_final_updates": int(ingested["final_games"]),
                    "quota": metadata,
                }
            except Exception as exc:
                self.session.rollback()
                self.session.add(ProviderRequestLedger(
                    provider=ledger.provider, endpoint_key=ledger.endpoint_key,
                    requested_at=ledger.requested_at, completed_at=datetime.now(UTC),
                    status="FAILED", request_fingerprint=ledger.request_fingerprint,
                    quota_cost=None, quota_remaining=None, records_received=None,
                    controlled_live_request=False, source_mode="LIVE",
                    job_run_id=self._active_job_run_id, reservation_id=ledger.id,
                    error_type=type(exc).__name__,
                    response_metadata={
                        "sport_key": "americanfootball_nfl", "response_received": False,
                    },
                ))
                self.session.commit()
                raise
        rows = list(self.session.scalars(
            select(Game)
            .join(ShadowForecast, ShadowForecast.canonical_game_id == Game.id)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(ShadowForecastBundle.sport == "NFL", ShadowForecast.event_date == event_date)
        ))
        unique = {row.id: row for row in rows}
        finals = [row for row in unique.values() if row.status == GameStatus.FINAL]
        incomplete = [row for row in unique.values() if row.status != GameStatus.FINAL]
        return {
            "records": len(finals), "final_games": len(finals),
            "incomplete_games": len(incomplete), **provider_result,
        }

    async def settle_nfl_shadow_decisions(self, event_date: date) -> dict[str, Any]:
        from app.services.shadow_board_service import DecisionOutcome, ShadowBoardService

        decisions = list(self.session.scalars(
            select(ShadowBoardDecision)
            .join(ShadowForecast, ShadowForecast.id == ShadowBoardDecision.shadow_forecast_id)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(ShadowForecastBundle.sport == "NFL", ShadowBoardDecision.slate_date == event_date)
        ))
        outcomes: list[DecisionOutcome] = []
        for decision in decisions:
            forecast = self.session.get(ShadowForecast, decision.shadow_forecast_id)
            game = self.session.get(Game, forecast.canonical_game_id) if forecast else None
            if game is None or game.status != GameStatus.FINAL or game.home_score is None or game.away_score is None:
                continue
            result = _nfl_shadow_result(decision, game.home_score, game.away_score)
            market_evidence = _nfl_closing_evidence(
                self.session, decision, forecast, cutoff_minutes=15
            )
            outcomes.append(DecisionOutcome(
                decision_id=decision.id,
                event_id=decision.event_id, engine_id=decision.engine_id,
                market=decision.market, selection=decision.selection, result=result,
                closing_american=market_evidence.get("closing_american"),
                market_evidence=market_evidence,
                actual_home_score=game.home_score, actual_away_score=game.away_score,
            ))
        result = ShadowBoardService(self.session, self.settings).settle_decisions(event_date, outcomes)
        return {"records": int(result.get("settled", 0)), **result}

    async def recalculate_nfl_shadow_performance(self, event_date: date) -> dict[str, Any]:
        from app.models import ShadowDecisionSettlement

        rows = list(self.session.execute(
            select(ShadowDecisionSettlement, ShadowBoardDecision, ShadowForecast)
            .join(ShadowBoardDecision, ShadowBoardDecision.id == ShadowDecisionSettlement.decision_id)
            .join(ShadowForecast, ShadowForecast.id == ShadowBoardDecision.shadow_forecast_id)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(ShadowForecastBundle.sport == "NFL")
        ))
        latest: dict[tuple[str, str], tuple[tuple[Any, ...], dict[str, Any]]] = {}
        priority = {"HISTORICAL": 0, "EARLY": 1, "CURRENT": 2, "FINAL_PREGAME": 3}
        for settlement, decision, forecast in rows:
            if not settlement.performance_detail:
                continue
            key = (decision.event_id, decision.engine_id)
            ordering = (
                _aware(forecast.forecast_cutoff_utc or forecast.created_at),
                priority.get(forecast.snapshot_type, -1),
                forecast.snapshot_version,
                int(
                    decision.market == "FULL_GAME_MONEYLINE"
                    and decision.selection == "HOME"
                    and decision.no_vig_probability is not None
                ),
            )
            if key not in latest or ordering > latest[key][0]:
                latest[key] = (ordering, settlement.performance_detail)
        details = [row[1] for row in latest.values()]
        def mean(key: str):
            values = [float(row[key]) for row in details if key in row]
            return sum(values) / len(values) if values else None
        return {
            "records": len(details), "games_evaluated": len(details), "settlements": len(rows),
            "home_win_log_loss": mean("home_win_log_loss"),
            "home_win_brier": mean("home_win_brier"),
            "market_home_win_log_loss": mean("market_home_win_log_loss"),
            "market_home_win_brier": mean("market_home_win_brier"),
            "model_minus_market_home_probability": mean("model_minus_market_home_probability"),
            "mean_team_points_mae": (
                (mean("home_points_absolute_error") + mean("away_points_absolute_error")) / 2
                if mean("home_points_absolute_error") is not None and mean("away_points_absolute_error") is not None
                else None
            ),
            "margin_mae": mean("margin_absolute_error"),
            "total_mae": mean("total_absolute_error"),
            "status": "COLLECTING_DATA" if not details else "PROSPECTIVE_RESEARCH_SHADOW",
        }

    async def evaluate_shadow_board(self, event_date: date) -> dict[str, Any]:
        """Evaluate the research-shadow board for the slate. Reads persisted odds only."""
        from app.services.shadow_board_service import ShadowBoardService

        result = ShadowBoardService(self.session, self.settings).evaluate_board(event_date)
        return {"records": int(result.get("decisions", 0)), **result}

    async def settle_shadow_decisions(self, event_date: date) -> dict[str, Any]:
        """Settle research-shadow decisions from governed final outcomes (virtual only)."""
        from app.services.shadow_board_service import ShadowBoardService

        result = ShadowBoardService(self.session, self.settings).settle_decisions(event_date, [])
        return {"records": int(result.get("settled", 0)), **result}

    async def freeze_shadow_experiment(self, event_date: date) -> dict[str, Any]:
        """Freeze the $1,000 shadow experiment's game-day decision snapshots.

        Idempotently ensures the immutable experiment exists, then captures the
        MORNING / LINEUP_CONFIRMED / FINAL_PREGAME snapshots as they become
        available. Only a FINAL_PREGAME QUALIFIED_BET may create a virtual
        position. Reads persisted forecasts + odds only; never a provider call.
        """
        from app.models import ExperimentDecisionPhase
        from app.services.shadow_experiment_service import ShadowExperimentService

        service = ShadowExperimentService(self.session, self.settings)
        service.ensure_started(start_date=event_date, code_commit=self.settings.build_commit or None)
        self.session.commit()
        # Every active experiment (V1 strict control + any registered
        # challenger) is evaluated exactly once from the same point-in-time
        # snapshot; each freeze() call is independently idempotent and a
        # challenger's pre-effective-date slates are skipped inside freeze().
        results: dict[str, Any] = {}
        primary = 0
        for experiment in service.active_experiments():
            phases: dict[str, Any] = {}
            for phase in ExperimentDecisionPhase:
                result = service.freeze(event_date, phase=phase, experiment=experiment)
                phases[phase.value] = result
                primary += int(result.get("primary", 0))
            results[experiment.decision_policy] = phases
        # Narrow pregame-evidence monitoring (idempotent AuditEvents): runs on
        # the freeze cadence, reads persisted rows only, never a provider.
        from app.services.pregame_evidence_monitor import PregameEvidenceMonitor

        try:
            monitor = PregameEvidenceMonitor(self.session, self.settings).run(event_date)
            self.session.commit()
        except Exception as exc:  # monitoring must never break the freeze job
            self.session.rollback()
            monitor = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        return {"records": primary, "experiments": results, "pregame_evidence_monitor": monitor}

    async def register_practical_experiment_v2(self, event_date: date) -> dict[str, Any]:
        """Register the V2 practical pregame challenger prospectively (idempotent).

        Effective no earlier than the NEXT Eastern operational date; never
        retroactive. The frozen policy calibration is persisted on the row."""
        from app.services.shadow_experiment_service import ShadowExperimentService

        service = ShadowExperimentService(self.session, self.settings)
        effective = max(event_date, local_today(self.settings.display_timezone)) + timedelta(days=1)
        experiment = service.ensure_v2_started(
            effective_date=effective, code_commit=self.settings.build_commit or None,
        )
        self.session.commit()
        config = experiment.policy_config or {}
        return {
            "records": 1,
            "experiment_id": str(experiment.id),
            "name": experiment.name,
            "version": experiment.version,
            "effective_date": experiment.start_date.isoformat(),
            "policy": experiment.decision_policy,
            "staking_policy": experiment.staking_policy_version,
            "policy_checksum": config.get("checksum"),
        }

    async def settle_shadow_experiment(self, event_date: date) -> dict[str, Any]:
        """Settle the $1,000 shadow experiment's primary positions from real finals."""
        from app.services.shadow_experiment_service import ShadowExperimentService

        result = ShadowExperimentService(self.session, self.settings).settle(event_date)
        return {"records": int(result.get("settled", 0)), **result}

    async def refresh_schedule(self, event_date: date, *, demo_only: bool = False) -> dict[str, Any]:
        raw: dict[str, Any] | list[Any]
        if demo_only:
            # Demo fixtures are available only through an explicitly requested
            # seed path. Provider failures must never silently change provenance.
            envelope = await self.demo.get_schedule(event_date)
            raw = envelope.data
            mode = "DEMO"
        else:
            response = await MLBStatsAPIAdapter().get_schedule(event_date)
            envelope, raw = response.envelope, response.raw_payload
            mode = "LIVE"
        archived = self.archive.write(envelope.source, raw)
        games = [ScheduleGameDTO.model_validate(item) for item in envelope.data]
        persisted = self.normalizer.ingest_schedule(
            games,
            envelope.source,
            available_as_of=envelope.fetched_at,
        )
        self._ingestion_run(envelope, archived.raw_file_reference, len(persisted), "refresh-schedule")
        self.session.commit()
        return {"records": len(persisted), "games": len(persisted), "source": envelope.source, "source_mode": mode}

    async def refresh_mlb_data(self, event_date: date) -> dict[str, Any]:
        live_game_exists = self.session.scalar(
            select(Game.id).where(
                Game.starts_at >= self._day_bounds(event_date)[0],
                Game.starts_at < self._day_bounds(event_date)[1],
                Game.source_mode == "LIVE",
            ).limit(1)
        ) is not None
        # Never mix provenance within a refresh. Live games take precedence;
        # explicit demo seeding works only when the target date has no live row.
        target_mode = "LIVE" if live_game_exists else "DEMO"
        team_rows = list(self.session.execute(
            select(Team, ExternalIdMapping.source, ExternalIdMapping.external_id)
            .join(GameTeam, GameTeam.team_id == Team.id)
            .join(Game, Game.id == GameTeam.game_id)
            .join(ExternalIdMapping, (ExternalIdMapping.entity_id == Team.id) & (ExternalIdMapping.entity_type == "team"))
            .where(
                Game.starts_at >= self._day_bounds(event_date)[0],
                Game.starts_at < self._day_bounds(event_date)[1],
                Game.source_mode == target_mode,
                ExternalIdMapping.source == (
                    "MLB_STATS_API" if target_mode == "LIVE" else self.demo.source
                ),
            )
            .order_by((ExternalIdMapping.source == self.demo.source).asc())
        ))
        processed = 0
        roster_processed = 0
        bullpen_processed = 0
        lineup_processed = 0
        warnings: list[str] = []
        seen: set[Any] = set()
        live_adapter = MLBStatsAPIAdapter()
        for team, source, external_id in team_rows:
            if team.id in seen:
                continue
            seen.add(team.id)
            try:
                if source == self.demo.source or self.settings.app_env == "test":
                    envelope = await self.demo.get_team_stats(external_id, event_date.year)
                else:
                    response = await live_adapter.get_team_stats(external_id, event_date.year)
                    envelope = response.envelope
                    self.archive.write(envelope.source, response.raw_payload)
                dto = TeamStatsDTO.model_validate(envelope.data)
                if envelope.source_mode == "DEMO":
                    first_pitch = self.session.scalar(
                        select(func.min(Game.starts_at)).join(GameTeam, GameTeam.game_id == Game.id).where(GameTeam.team_id == team.id)
                    )
                    if first_pitch:
                        dto.available_as_of = min(dto.available_as_of, _aware(first_pitch) - timedelta(hours=2))
                if self.normalizer.ingest_team_stats(dto, source):
                    processed += 1
                if source != self.demo.source and self.settings.app_env != "test":
                    try:
                        roster_response = await live_adapter.get_active_roster(external_id, event_date)
                        self.archive.write(roster_response.envelope.source, roster_response.raw_payload)
                        roster_rows = [
                            RosterAvailabilityDTO.model_validate(item)
                            for item in roster_response.envelope.data
                        ]
                        roster_processed += self.normalizer.ingest_roster_availability(
                            roster_rows,
                            roster_response.envelope.source,
                        )
                    except SourceError as exc:
                        warnings.append(f"{team.name} roster: {exc}")

                    try:
                        bullpen_response = await live_adapter.get_bullpen_workload(
                            external_id,
                            event_date,
                        )
                        self.archive.write(
                            bullpen_response.envelope.source,
                            bullpen_response.raw_payload,
                        )
                        bullpen_rows = [
                            BullpenAvailabilityDTO.model_validate(item)
                            for item in bullpen_response.envelope.data
                        ]
                        target_games = list(self.session.scalars(
                            select(Game)
                            .join(GameTeam, GameTeam.game_id == Game.id)
                            .where(
                                GameTeam.team_id == team.id,
                                Game.starts_at >= self._day_bounds(event_date)[0],
                                Game.starts_at < self._day_bounds(event_date)[1],
                                Game.source_mode == "LIVE",
                            )
                            .order_by(Game.starts_at)
                        ))
                        for game in target_games:
                            bullpen_processed += self.normalizer.ingest_bullpen_availability(
                                game.id,
                                bullpen_rows,
                                bullpen_response.envelope.source,
                            )
                    except SourceError as exc:
                        warnings.append(f"{team.name} bullpen workload: {exc}")
            except SourceError as exc:
                warnings.append(f"{team.name}: {exc}")
        if self.settings.app_env != "test":
            game_mappings = list(self.session.scalars(
                select(ExternalIdMapping)
                .join(Game, Game.id == ExternalIdMapping.entity_id)
                .where(
                    ExternalIdMapping.entity_type == "game",
                    ExternalIdMapping.source == "MLB_STATS_API",
                    Game.starts_at >= self._day_bounds(event_date)[0],
                    Game.starts_at < self._day_bounds(event_date)[1],
                    Game.source_mode == "LIVE",
                )
            ))
            for mapping in game_mappings:
                try:
                    lineup_response = await live_adapter.get_game_lineups(mapping.external_id)
                    self.archive.write(lineup_response.envelope.source, lineup_response.raw_payload)
                    lineups = [LineupDTO.model_validate(item) for item in lineup_response.envelope.data]
                    lineup_processed += self.normalizer.ingest_lineups(
                        lineups,
                        lineup_response.envelope.source,
                    )
                except SourceError as exc:
                    warnings.append(f"game {mapping.external_id} lineup: {exc}")
        mode_source = self.demo.source if team_rows and all(row[1] == self.demo.source for row in team_rows) else "MLB_STATS_API"
        envelope = SourceEnvelope(
            source=mode_source,
            source_mode="DEMO" if mode_source == self.demo.source else "LIVE",
            fetched_at=datetime.now(UTC),
            data=[],
            metadata={"team_count": len(seen), "warnings": warnings},
        )
        failed = bool(seen and processed == 0 and warnings)
        self._ingestion_run(
            envelope,
            None,
            processed + roster_processed + bullpen_processed + lineup_processed,
            "refresh-mlb-data",
            status=RunStatus.FAILED if failed else RunStatus.SUCCEEDED,
        )
        self.session.commit()
        if failed:
            raise SourceError("MLB_STATS_API", f"No team statistics were ingested: {warnings[0]}")
        return {
            "records": processed + roster_processed + bullpen_processed + lineup_processed,
            "team_stats": processed,
            "roster_observations": roster_processed,
            "bullpen_observations": bullpen_processed,
            "lineup_observations": lineup_processed,
            "teams": len(seen),
            "warnings": warnings,
        }

    def odds_pregame_window_open(self, now: datetime) -> bool:
        """True when a monitored LIVE game is within the pregame odds window.

        Lets the scheduled cadence concentrate its daily budget on the hours
        before first pitch instead of spending it overnight. Started games and
        games beyond the window do not keep the window open."""

        window = timedelta(hours=self.settings.odds_pregame_window_hours)
        upcoming = self.session.scalars(
            select(Game).where(
                Game.source_mode == "LIVE",
                Game.status.in_((GameStatus.SCHEDULED, GameStatus.LIVE)),
                Game.starts_at > now,
            )
        )
        for game in upcoming:
            starts = game.starts_at if game.starts_at.tzinfo else game.starts_at.replace(tzinfo=UTC)
            if starts - now <= window:
                return True
        return False

    async def refresh_odds(
        self,
        event_date: date,
        *,
        demo_only: bool = False,
        controlled_live_request: bool = False,
    ) -> dict[str, Any]:
        ledger: ProviderRequestLedger | None = None
        provider_metadata: dict[str, Any] = {}
        provider_records_received: int | None = None
        response_received = False
        try:
            if demo_only:
                # Fixture odds are available only through an explicitly requested
                # demo path. Missing credentials must never change provenance.
                envelope = await self.demo.get_market_snapshot(event_date)
                raw_payload = envelope.data
            elif self.settings.the_odds_api_key:
                self._enforce_odds_quota_policy()
                ledger = self._reserve_provider_request(
                    "THE_ODDS_API",
                    "baseball_mlb_odds",
                    controlled_live_request=controlled_live_request,
                    request_shape={
                        "regions": self.settings.odds_regions,
                        "markets": sorted(self.settings.odds_market_list),
                        # The upstream endpoint is not date-filtered. The exactly-
                        # once reservation deduplicates concurrent/retried calls
                        # within one refresh window; the number of windows per day
                        # is governed by odds_daily_request_budget (default 1, so
                        # this stays one-per-day unless a deployer raises it).
                        "request_window": int(
                            datetime.now(UTC).timestamp()
                            // (max(1, self.settings.odds_pregame_refresh_minutes) * 60)
                        ),
                    },
                )
                self.session.commit()
                response = await TheOddsAPIAdapter(
                    self.settings.the_odds_api_key,
                    self.settings.odds_market_list,
                    self.settings.odds_regions,
                ).get_market_snapshot()
                envelope = response.envelope
                raw_payload = response.raw_payload
                provider_metadata = dict(envelope.metadata or {})
                provider_records_received = len(envelope.data) if isinstance(envelope.data, list) else 1
                response_received = True
            else:
                raise SourceError("THE_ODDS_API", "THE_ODDS_API_KEY is not configured", retryable=False)
            archive = self.archive.write(envelope.source, raw_payload)
            events = [OddsEventDTO.model_validate(item) for item in envelope.data]
            count = self.normalizer.ingest_odds(
                events,
                envelope.source,
                envelope.fetched_at,
                source_mode=envelope.source_mode.upper(),
                event_date=event_date,
            )
            if ledger is not None:
                self.session.add(ProviderRequestLedger(
                    provider=ledger.provider,
                    endpoint_key=ledger.endpoint_key,
                    requested_at=ledger.requested_at,
                    completed_at=datetime.now(UTC),
                    status="SUCCEEDED",
                    request_fingerprint=ledger.request_fingerprint,
                    quota_cost=_int_or_none(provider_metadata.get("requests_last")),
                    quota_remaining=_int_or_none(provider_metadata.get("requests_remaining")),
                    records_received=len(events),
                    controlled_live_request=ledger.controlled_live_request,
                    source_mode=ledger.source_mode,
                    job_run_id=self._active_job_run_id,
                    reservation_id=ledger.id,
                    response_metadata={
                        "reservation_id": str(ledger.id),
                        "event_count": len(events),
                        "requests_used": _int_or_none(provider_metadata.get("requests_used")),
                        "response_received": response_received,
                    },
                ))
            self._ingestion_run(envelope, archive.raw_file_reference, count, "refresh-odds")
            self.session.commit()
            return {
                "records": count,
                "events": len(events),
                "source": envelope.source,
                "source_mode": envelope.source_mode,
                "quota": envelope.metadata,
            }
        except Exception as exc:
            self.session.rollback()
            if ledger is not None:
                self.session.add(ProviderRequestLedger(
                    provider=ledger.provider,
                    endpoint_key=ledger.endpoint_key,
                    requested_at=ledger.requested_at,
                    completed_at=datetime.now(UTC),
                    status="FAILED",
                    request_fingerprint=ledger.request_fingerprint,
                    quota_cost=_int_or_none(provider_metadata.get("requests_last")),
                    quota_remaining=_int_or_none(provider_metadata.get("requests_remaining")),
                    records_received=provider_records_received,
                    controlled_live_request=ledger.controlled_live_request,
                    source_mode=ledger.source_mode,
                    job_run_id=self._active_job_run_id,
                    reservation_id=ledger.id,
                    error_type=type(exc).__name__,
                    response_metadata={
                        "reservation_id": str(ledger.id),
                        "requests_used": _int_or_none(provider_metadata.get("requests_used")),
                        "response_received": response_received,
                    },
                ))
                self.session.commit()
            raise

    async def refresh_weather(self, event_date: date, *, demo_only: bool = False) -> dict[str, Any]:
        rows = list(self.session.execute(
            select(Game, Venue).join(Venue, Venue.id == Game.venue_id).where(
                Game.starts_at >= self._day_bounds(event_date)[0],
                Game.starts_at < self._day_bounds(event_date)[1],
                Game.source_mode == ("DEMO" if demo_only else "LIVE"),
            )
        ))
        count = 0
        warnings: list[str] = []
        source_modes: set[str] = set()
        for game, venue in rows:
            if venue.latitude is None or venue.longitude is None:
                warnings.append(f"{venue.name}: coordinates unavailable")
                continue
            try:
                if game.source_mode == "LIVE" and self.settings.open_meteo_enabled and self.settings.app_env != "test" and not demo_only:
                    response = await OpenMeteoAdapter().get_weather(venue.latitude, venue.longitude, game.starts_at)
                    envelope = response.envelope
                    self.archive.write(envelope.source, response.raw_payload)
                elif game.source_mode == "DEMO" and self.settings.demo_mode:
                    envelope = await self.demo.get_weather(venue.latitude, venue.longitude, game.starts_at)
                else:
                    raise SourceError(
                        "OPEN_METEO",
                        "Weather source disabled for a live game; demo observations were not mixed in",
                        retryable=False,
                    )
                dto = WeatherDTO.model_validate(envelope.data)
                source_modes.add(envelope.source)
                self.normalizer.ingest_weather(game.id, dto, envelope.source)
                count += 1
            except SourceError as exc:
                warnings.append(f"{venue.name}: {exc}")
        has_live_games = any(game.source_mode == "LIVE" for game, _ in rows)
        source_name = "OPEN_METEO" if "OPEN_METEO" in source_modes or has_live_games else self.demo.source
        failed = bool(rows and count == 0 and warnings)
        self._ingestion_run(
            SourceEnvelope(source=source_name, source_mode="DEMO" if source_name == self.demo.source else "LIVE", fetched_at=datetime.now(UTC), data=[], metadata={"warnings": warnings}),
            None,
            count,
            "refresh-weather",
            status=RunStatus.FAILED if failed else RunStatus.SUCCEEDED,
        )
        self.session.commit()
        if failed:
            raise SourceError("OPEN_METEO", f"No weather observations were ingested: {warnings[0]}")
        return {"records": count, "warnings": warnings}

    async def refresh_statcast(self, event_date: date) -> dict[str, Any]:
        if not self.settings.statcast_enabled:
            return {"records": 0, "status": "unavailable", "warning": "STATCAST_ENABLED=false; no synthetic Statcast values were created"}
        adapter = BaseballSavantStatcastAdapter()
        batting = await adapter.get_batting_leaderboard(event_date.year)
        pitching = await adapter.get_pitching_leaderboard(event_date.year)
        batting_archive = self.archive.write(batting.envelope.source, batting.raw_payload)
        pitching_archive = self.archive.write(pitching.envelope.source, pitching.raw_payload)
        combined_rows = [*batting.envelope.data, *pitching.envelope.data]
        snapshot = ParquetSnapshotWriter(self.settings.curated_data_root).write(
            "statcast_player",
            combined_rows,
            event_date=datetime(event_date.year, event_date.month, event_date.day, tzinfo=UTC),
        )
        promoted = self.normalizer.ingest_statcast(
            combined_rows,
            batting.envelope.source,
            season=event_date.year,
            available_as_of=max(batting.envelope.fetched_at, pitching.envelope.fetched_at),
            raw_reference=f"{batting_archive.raw_file_reference},{pitching_archive.raw_file_reference}",
            source_mode="LIVE",
        )
        combined_envelope = SourceEnvelope(
            source=batting.envelope.source,
            source_mode="LIVE",
            fetched_at=max(batting.envelope.fetched_at, pitching.envelope.fetched_at),
            data=[],
            metadata={
                "batting_rows": len(batting.envelope.data),
                "pitching_rows": len(pitching.envelope.data),
            },
        )
        self._ingestion_run(
            combined_envelope,
            f"{batting_archive.raw_file_reference},{pitching_archive.raw_file_reference}",
            promoted,
            "refresh-statcast",
        )
        self.session.commit()
        return {
            "records": promoted,
            "downloaded_rows": snapshot.row_count,
            "canonical_observations": promoted,
            "status": "success",
            "parquet_reference": snapshot.path,
            "checksum": snapshot.checksum,
        }

    async def rebuild_features(self, event_date: date) -> dict[str, Any]:
        # Team features are normalized as part of MLB data ingestion. This job is
        # idempotent and validates readiness rather than copying observations.
        games = self._games_for_date(event_date)
        return {"records": len(games), "status": "validated", "games": len(games)}

    async def generate_forecasts(self, event_date: date, *, data_as_of: datetime | None = None) -> dict[str, Any]:
        service = ForecastService(self.session, self.settings)
        created = 0
        skipped: list[dict[str, Any]] = []
        for game in self._games_for_date(event_date):
            try:
                service.generate_for_game(game, data_as_of=data_as_of)
                created += 1
            except ForecastNotReadyError as exc:
                skipped.append({"game_id": str(game.id), "reason": str(exc), "score": exc.score})
            except ForecastWindowClosedError as exc:
                skipped.append({"game_id": str(game.id), "reason": str(exc), "score": None})
        self.session.commit()
        return {"records": created, "forecasts": created, "skipped": skipped}

    async def settle_games(self, event_date: date) -> dict[str, Any]:
        # Settlement service only acts when canonical final scores and open
        # positions exist; it never guesses final scores.
        final_games = list(self.session.scalars(select(Game).where(Game.status == GameStatus.FINAL)))
        settlements = settle_completed_positions(self.session)
        self.session.commit()
        return {"records": len(settlements), "final_games_seen": len(final_games), "status": "settlement complete"}

    async def publish_positions(self, event_date: date) -> dict[str, Any]:
        positions = publish_official_positions(self.session, self.settings, self._games_for_date(event_date))
        self.session.commit()
        return {
            "records": len(positions),
            "positions": [str(position.id) for position in positions],
            "policy": "PROVISIONAL PHASE 1 BANKROLL POLICY",
            "feature_enabled": self.settings.official_positions_enabled,
        }

    async def recalculate_performance(self, event_date: date) -> dict[str, Any]:
        reader = ReadService(self.session, self.settings)
        evaluation = reader.performance()
        # Only the model bound to a current-development forecast may receive
        # operational forward metrics. A newer research registry row is not a
        # public model by recency alone.
        model = reader._forward_model([])
        metrics = {
            "evaluated_games": evaluation.evaluated_games,
            "brier_score": evaluation.brier_score,
            "log_loss": evaluation.log_loss,
            "accuracy": evaluation.accuracy,
            "run_mae": evaluation.run_mae,
            "total_mae": evaluation.total_mae,
            "versus_market_brier": evaluation.versus_market_brier,
            "evaluation_policy": "latest published pregame forecast per completed game",
            "calculated_at": datetime.now(UTC).isoformat(),
        }
        if model:
            model.metrics = metrics
        self.session.commit()
        return {
            "records": evaluation.evaluated_games,
            "evaluated_games": evaluation.evaluated_games,
            "status": "calculated" if evaluation.evaluated_games else "awaiting settled production sample",
            "metrics": metrics,
        }

    async def daily_pipeline(self, event_date: date, *, demo_only: bool = False) -> dict[str, Any]:
        steps: dict[str, Any] = {}
        steps["schedule"] = await self.refresh_schedule(event_date, demo_only=demo_only)
        steps["mlb_data"] = await self.refresh_mlb_data(event_date)
        steps["statcast"] = await self.refresh_statcast(event_date)
        steps["weather"] = await self.refresh_weather(event_date)
        if self.settings.odds_polling_enabled:
            steps["odds"] = await self.refresh_odds(event_date)
        elif demo_only and self.settings.demo_mode and all(game.source_mode == "DEMO" for game in self._games_for_date(event_date)):
            steps["odds"] = await self.refresh_odds(event_date, demo_only=True)
        else:
            steps["odds"] = {
                "records": 0,
                "status": "skipped",
                "reason": "ODDS_POLLING_ENABLED=false; demo odds are not mixed into live games",
            }
        steps["features"] = await self.rebuild_features(event_date)
        steps["forecasts"] = await self.generate_forecasts(event_date)
        if self.settings.official_positions_enabled:
            steps["positions"] = await self.publish_positions(event_date)
        # Ensure the immutable $1,000 experiment exists and capture the day's
        # earliest decision snapshots (virtual only; no provider call).
        steps["shadow_experiment"] = await self.freeze_shadow_experiment(event_date)
        return {"records": sum(int(value.get("records", 0)) for value in steps.values()), "steps": steps}

    async def finalize_games(self, event_date: date) -> dict[str, Any]:
        """Quota-free final-state refresh and settlement pipeline."""
        steps: dict[str, Any] = {}
        steps["schedule"] = await self.refresh_schedule(event_date)
        # Capture lineup/final-pregame snapshots as they become due, then settle
        # both the official positions and the experiment from real finals.
        steps["shadow_experiment_freeze"] = await self.freeze_shadow_experiment(event_date)
        steps["settlement"] = await self.settle_games(event_date)
        steps["shadow_experiment_settle"] = await self.settle_shadow_experiment(event_date)
        steps["performance"] = await self.recalculate_performance(event_date)
        return {"records": sum(int(value.get("records", 0)) for value in steps.values()), "steps": steps}

    async def controlled_live_validation(self, event_date: date) -> dict[str, Any]:
        """Run the one-shot Gate 2 validation under strict quota safeguards.

        This operator-only path never enables schedulers, polling, Statcast, or
        official positions. The single Odds request is attempted only after
        live schedule/context/weather acquisition and a clean provenance/budget
        preflight; provider failures are terminal and are never retried here.
        """

        unsafe_flags = {
            "scheduler_enabled": self.settings.scheduler_enabled,
            "event_relative_scheduler_enabled": self.settings.event_relative_scheduler_enabled,
            "odds_polling_enabled": self.settings.odds_polling_enabled,
            "statcast_enabled": self.settings.statcast_enabled,
            "official_positions_enabled": self.settings.official_positions_enabled,
        }
        enabled = [name for name, value in unsafe_flags.items() if value]
        if enabled:
            raise SourceError(
                "GATE2_CONTROLLED_VALIDATION",
                f"Controlled validation requires disabled background/optional flags: {', '.join(enabled)}",
                retryable=False,
            )
        if self.settings.app_env == "test":
            raise SourceError(
                "GATE2_CONTROLLED_VALIDATION",
                "Controlled live validation is disabled in the test environment",
                retryable=False,
            )
        if self.settings.odds_daily_request_budget != 1:
            raise SourceError(
                "GATE2_CONTROLLED_VALIDATION",
                "ODDS_DAILY_REQUEST_BUDGET must equal 1",
                retryable=False,
            )
        if not self.settings.the_odds_api_key:
            raise SourceError(
                "GATE2_CONTROLLED_VALIDATION",
                "THE_ODDS_API_KEY is not configured",
                retryable=False,
            )

        bounds = self._day_bounds(event_date)
        demo_games_before = self.session.scalar(select(func.count(Game.id)).where(
            Game.starts_at >= bounds[0],
            Game.starts_at < bounds[1],
            Game.source_mode == "DEMO",
        )) or 0
        if demo_games_before:
            raise SourceError(
                "GATE2_CONTROLLED_VALIDATION",
                f"Target date contains {demo_games_before} DEMO game rows; use a clean live date/database",
                retryable=False,
            )
        self._enforce_odds_quota_policy()

        steps: dict[str, Any] = {}
        steps["schedule"] = await self.refresh_schedule(event_date)
        live_games = self.session.scalar(select(func.count(Game.id)).where(
            Game.starts_at >= bounds[0],
            Game.starts_at < bounds[1],
            Game.source_mode == "LIVE",
        )) or 0
        if live_games == 0:
            raise SourceError(
                "GATE2_CONTROLLED_VALIDATION",
                "MLB schedule returned no live games for the target date; Odds request was not spent",
                retryable=False,
            )
        steps["mlb_data"] = await self.refresh_mlb_data(event_date)
        steps["weather"] = await self.refresh_weather(event_date)
        demo_games_after = self.session.scalar(select(func.count(Game.id)).where(
            Game.starts_at >= bounds[0],
            Game.starts_at < bounds[1],
            Game.source_mode == "DEMO",
        )) or 0
        if demo_games_after:
            raise SourceError(
                "GATE2_CONTROLLED_VALIDATION",
                "Demo provenance appeared during live preflight; Odds request was not spent",
                retryable=False,
            )
        steps["odds"] = await self.refresh_odds(
            event_date,
            controlled_live_request=True,
        )
        steps["features"] = await self.rebuild_features(event_date)
        steps["forecasts"] = await self.generate_forecasts(event_date)
        return {
            "records": sum(int(value.get("records", 0)) for value in steps.values()),
            "event_date": event_date.isoformat(),
            "live_games": live_games,
            "demo_games": demo_games_after,
            "one_odds_request_attempted": True,
            "steps": steps,
        }

    def _games_for_date(self, event_date: date) -> list[Game]:
        games = list(self.session.scalars(select(Game).where(
            Game.starts_at >= self._day_bounds(event_date)[0], Game.starts_at < self._day_bounds(event_date)[1]
        ).order_by(Game.starts_at)))
        live = [game for game in games if game.source_mode == "LIVE"]
        return live or games

    def _ingestion_run(
        self,
        envelope: SourceEnvelope,
        raw_reference: str | None,
        count: int,
        job_name: str,
        *,
        status: RunStatus = RunStatus.SUCCEEDED,
    ) -> None:
        now = datetime.now(UTC)
        self.session.add(IngestionRun(
            source=envelope.source,
            job_name=job_name,
            status=status,
            started_at=envelope.fetched_at,
            completed_at=now,
            records_processed=count,
            warnings=list((envelope.metadata or {}).get("warnings") or []),
            raw_reference=raw_reference,
            schema_version="1",
            source_metadata=envelope.metadata,
        ))

    def _day_bounds(self, value: date) -> tuple[datetime, datetime]:
        return local_day_bounds(value, self.settings.display_timezone)

    def _enforce_odds_quota_policy(self) -> None:
        # The "daily" budget window is the operational day in the configured
        # display timezone — the same day that defines the slate it serves. A
        # UTC window resets at 20:00 ET and lets the prior evening's spend
        # exhaust the next slate's budget by mid-afternoon.
        budget_day = local_today(self.settings.display_timezone)
        start_of_day, end_of_day = local_day_bounds(budget_day, self.settings.display_timezone)
        requests_today = self.session.scalar(
            select(func.count(ProviderRequestLedger.id)).where(
                ProviderRequestLedger.provider == "THE_ODDS_API",
                ProviderRequestLedger.requested_at >= start_of_day,
                ProviderRequestLedger.requested_at < end_of_day,
                ProviderRequestLedger.source_mode == "LIVE",
                ProviderRequestLedger.status == "RESERVED",
            )
        ) or 0
        if requests_today >= self.settings.odds_daily_request_budget:
            raise SourceError(
                "THE_ODDS_API",
                f"Daily request budget reached ({requests_today}/{self.settings.odds_daily_request_budget}) "
                f"for operational day {budget_day.isoformat()}",
                retryable=False,
            )
        # Anchored to the newest successful run that actually carries a quota
        # reading. A later run without one is silent about the balance and must
        # not be able to clear a latch a real reading set.
        from app.jobs.odds_quota_recovery import latest_successful_run

        latest = latest_successful_run(self.session)
        if not latest:
            return
        remaining = (latest.source_metadata or {}).get("requests_remaining")
        if remaining is not None and int(remaining) <= self.settings.odds_quota_floor:
            # The floor still holds. The only exception is a single bounded
            # recovery probe for a reading too old to describe the balance now
            # -- otherwise this guard can never be released, because only a
            # request can refresh the number it is reading.
            state = quota_state(
                self.session,
                floor=self.settings.odds_quota_floor,
                probe_after_hours=self.settings.odds_quota_probe_after_hours,
                probe_cooldown_hours=self.settings.odds_quota_probe_cooldown_hours,
                renewal_days=self.settings.odds_quota_renewal_days,
            )
            if not state.probe_allowed:
                raise SourceError(
                    "THE_ODDS_API",
                    f"Odds quota floor reached ({remaining} remaining). "
                    f"{state.probe_blocked_reason or ''}".strip(),
                    retryable=False,
                )
            # The probe is ledgered like any other request, so it is counted,
            # auditable, and mutually exclusive with a concurrent probe.
            self._reserve_provider_request(
                "THE_ODDS_API",
                PROBE_ENDPOINT_KEY,
                controlled_live_request=True,
                request_shape={
                    "reason": "QUOTA_RECOVERY_PROBE",
                    "stale_reading": int(remaining),
                    "reading_age_hours": round(state.reading_age_hours or 0.0, 2),
                },
            )
        if latest.completed_at:
            elapsed = datetime.now(UTC) - _aware(latest.completed_at)
            cooldown = timedelta(minutes=self.settings.odds_manual_cooldown_minutes)
            if elapsed < cooldown:
                seconds = int((cooldown - elapsed).total_seconds())
                raise SourceError("THE_ODDS_API", f"Odds refresh cooldown active for {seconds} more seconds", retryable=False)

    def _reserve_provider_request(
        self,
        provider: str,
        endpoint_key: str,
        *,
        controlled_live_request: bool,
        request_shape: dict[str, Any],
    ) -> ProviderRequestLedger:
        now = datetime.now(UTC)
        fingerprint = sha256(
            json.dumps(request_shape, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        ledger = ProviderRequestLedger(
            provider=provider,
            endpoint_key=endpoint_key,
            requested_at=now,
            status="RESERVED",
            request_fingerprint=fingerprint,
            controlled_live_request=controlled_live_request,
            source_mode="LIVE",
            job_run_id=self._active_job_run_id,
            response_metadata={"request_shape": request_shape},
        )
        self.session.add(ledger)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise SourceError(
                provider,
                "An equivalent provider request is already reserved for this refresh window",
                retryable=False,
            ) from exc
        return ledger


async def _invoke_nfl_shadow_producer(
    settings: Settings, snapshot_types: tuple[str, ...]
) -> dict[str, Any]:
    """Invoke and verify the sole bounded Model Studio producer entry point."""

    from app.forecast_exchange.sports_contracts import (
        NFL_FEATURE_CONTRACT_SHA256,
        NFL_MODEL_BINARY_SHA256,
        NFL_MODEL_MANIFEST_SHA256,
    )

    allowed = {"EARLY", "CURRENT", "FINAL_PREGAME"}
    if not snapshot_types or len(set(snapshot_types)) != len(snapshot_types):
        raise ValueError("NFL producer snapshot types must be nonempty and unique")
    if set(snapshot_types) - allowed:
        raise ValueError("NFL producer snapshot types differ from the frozen contract")
    if "FINAL_PREGAME" in snapshot_types and len(snapshot_types) != 1:
        raise ValueError("FINAL_PREGAME cannot be mixed with EARLY or CURRENT generation")
    root_text = settings.nfl_model_studio_root.strip()
    python_text = settings.nfl_model_studio_python.strip()
    exchange_text = settings.nfl_shadow_bundle_import_dir.strip()
    if not root_text or not python_text or not exchange_text:
        raise ValueError(
            "NFL producer requires NFL_MODEL_STUDIO_ROOT, NFL_MODEL_STUDIO_PYTHON, "
            "and NFL_SHADOW_BUNDLE_IMPORT_DIR"
        )
    root = Path(root_text).resolve()
    python = Path(python_text).resolve()
    if not root.is_dir():
        raise ValueError("Configured NFL Model Studio root is not a directory")
    if not python.is_file():
        raise ValueError("Configured NFL Model Studio Python executable is not a file")
    if not (root / "scripts" / "run_nfl_2026_prospective_shadow.py").is_file():
        raise ValueError("Configured Model Studio root lacks the frozen NFL producer CLI")

    git_process = await asyncio.create_subprocess_exec(
        "git", "-C", str(root), "rev-parse", "HEAD",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        git_stdout, git_stderr = await asyncio.wait_for(git_process.communicate(), timeout=30)
    except TimeoutError:
        git_process.kill()
        await git_process.communicate()
        raise ValueError("Timed out resolving the configured Model Studio HEAD") from None
    if git_process.returncode != 0:
        raise ValueError(
            "Cannot resolve configured Model Studio HEAD: "
            + git_stderr.decode("utf-8", errors="replace")[-500:]
        )
    expected_head = git_stdout.decode("ascii", errors="strict").strip().lower()
    if len(expected_head) != 40 or any(ch not in "0123456789abcdef" for ch in expected_head):
        raise ValueError("Configured Model Studio HEAD is not a full Git commit identity")
    clean_process = await asyncio.create_subprocess_exec(
        "git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=no",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        clean_stdout, clean_stderr = await asyncio.wait_for(clean_process.communicate(), timeout=30)
    except TimeoutError:
        clean_process.kill()
        await clean_process.communicate()
        raise ValueError("Timed out verifying the configured Model Studio worktree") from None
    if clean_process.returncode != 0:
        raise ValueError(
            "Cannot verify configured Model Studio worktree: "
            + clean_stderr.decode("utf-8", errors="replace")[-500:]
        )
    if clean_stdout.strip():
        raise ValueError(
            "Configured Model Studio has tracked or staged changes; producer code identity is not frozen"
        )

    arguments: list[str] = ["-m", "scripts.run_nfl_2026_prospective_shadow"]
    for snapshot_type in snapshot_types:
        arguments.extend(("--snapshot-type", snapshot_type))
    process = await asyncio.create_subprocess_exec(
        str(python), *arguments, cwd=str(root),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=settings.nfl_shadow_producer_timeout_seconds
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise ValueError("Model Studio NFL producer exceeded its configured timeout") from None
    if process.returncode != 0:
        raise ValueError(
            "Model Studio NFL producer failed: "
            + stderr.decode("utf-8", errors="replace")[-1000:]
        )
    try:
        payload = json.loads(stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Model Studio NFL producer stdout is not one JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("Model Studio NFL producer stdout must be a JSON object")
    status = payload.get("status")
    if status == "NO_ELIGIBLE_UPCOMING_GAMES":
        if payload.get("games") != 0:
            raise ValueError("NO_ELIGIBLE_UPCOMING_GAMES must declare zero games")
        return payload
    if status != "ACTIVE":
        raise ValueError(f"Model Studio NFL producer returned unsupported status {status!r}")

    exact = {
        "modelManifestSha256": NFL_MODEL_MANIFEST_SHA256,
        "modelBinarySha256": NFL_MODEL_BINARY_SHA256,
        "featureContractSha256": NFL_FEATURE_CONTRACT_SHA256,
        "modelsFit": 0,
        "modelsPromoted": 0,
        "marketFieldsRead": False,
        "reloadFixtureReproduced": True,
        "producerRevision": expected_head,
    }
    for field, authority in exact.items():
        if payload.get(field) != authority:
            raise ValueError(f"Model Studio NFL producer {field} differs from frozen authority")
    for field in (
        "games", "featureVectorCount", "exchangeLocatorCurrentBundleCount",
        "exchangeLocatorCurrentForecastCount",
    ):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"Model Studio NFL producer {field} must be a positive integer")
    for field in (
        "forecastsCreated", "bundleCount", "exchangeLocatorHistoricalBundleCount",
        "lateFinalPregameOmissions",
    ):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Model Studio NFL producer {field} must be a nonnegative integer")
    try:
        forecast_cutoff = datetime.fromisoformat(
            str(payload["forecastCutoffUtc"]).replace("Z", "+00:00")
        )
        completed_at = datetime.fromisoformat(
            str(payload["completedAtUtc"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Model Studio NFL producer completion timestamps are invalid") from exc
    if forecast_cutoff.tzinfo is None or completed_at.tzinfo is None:
        raise ValueError("Model Studio NFL producer completion timestamps must be timezone-aware")
    if completed_at < forecast_cutoff:
        raise ValueError("Model Studio NFL producer completion predates its evidence cutoff")
    for field in (
        "exchangeLocatorCurrentBundleListSha256",
        "exchangeLocatorHistoricalBundleListSha256",
    ):
        value = payload.get(field)
        if (
            not isinstance(value, str) or len(value) != 64
            or any(ch not in "0123456789abcdef" for ch in value)
        ):
            raise ValueError(f"Model Studio NFL producer {field} is not a SHA-256")

    configured = Path(exchange_text).resolve()
    expected_locator = (
        configured / "nfl-2026-research-shadow-index.json"
        if configured.is_dir() else configured
    )
    declared_locator = Path(str(payload.get("exchangeLocatorPath", ""))).resolve()
    if declared_locator != expected_locator:
        raise ValueError("Model Studio NFL producer wrote a different configured exchange locator")
    return payload


def _nfl_shadow_result(decision: ShadowBoardDecision, home_score: int, away_score: int) -> str:
    """Settle one NFL research-shadow market from immutable recorded terms."""

    margin = home_score - away_score
    if decision.market == "FULL_GAME_MONEYLINE":
        if margin == 0:
            return "PUSH"
        selected_home = decision.selection == "HOME"
        return "WIN" if (margin > 0) == selected_home else "LOSS"
    if decision.market == "FULL_GAME_SPREAD":
        if decision.line is None:
            return "VOID"
        adjusted = margin + decision.line if decision.selection == "HOME" else -margin + decision.line
    elif decision.market == "FULL_GAME_TOTAL":
        if decision.line is None:
            return "VOID"
        adjusted = home_score + away_score - decision.line
        if decision.selection == "UNDER":
            adjusted *= -1
    else:
        return "VOID"
    if adjusted > 0:
        return "WIN"
    if adjusted < 0:
        return "LOSS"
    return "PUSH"


def _nfl_closing_evidence(
    session: Session,
    decision: ShadowBoardDecision,
    forecast: ShadowForecast,
    *,
    cutoff_minutes: int,
) -> dict[str, Any]:
    """Return exact same-selection checkpoint evidence for CLV and line movement."""

    if (
        decision.odds_snapshot_id is None
        or decision.opposite_odds_snapshot_id is None
        or forecast.scheduled_start_utc is None
    ):
        return {}
    original = session.get(OddsSnapshot, decision.odds_snapshot_id)
    original_opposite = session.get(OddsSnapshot, decision.opposite_odds_snapshot_id)
    if original is None or original_opposite is None:
        return {}
    original_outcome = session.get(MarketOutcome, original.market_outcome_id)
    opposite_outcome = session.get(MarketOutcome, original_opposite.market_outcome_id)
    if original_outcome is None or opposite_outcome is None:
        return {}
    cutoff = _aware(forecast.scheduled_start_utc) - timedelta(minutes=cutoff_minutes)
    rows = list(session.execute(
        select(OddsSnapshot, MarketOutcome)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .where(
            MarketOutcome.market_id == original_outcome.market_id,
            OddsSnapshot.sportsbook_id == original.sportsbook_id,
            OddsSnapshot.source_event_id == original.source_event_id,
            OddsSnapshot.available_as_of <= cutoff,
        ).order_by(OddsSnapshot.available_as_of.desc())
    ))

    def same_selection(candidate: MarketOutcome, reference: MarketOutcome) -> bool:
        if reference.participant_team_id is not None:
            return candidate.participant_team_id == reference.participant_team_id
        return candidate.label.strip().upper() == reference.label.strip().upper()

    selected = [
        (snapshot, outcome) for snapshot, outcome in rows
        if same_selection(outcome, original_outcome)
    ]
    first_observed = next((
        snapshot for snapshot, _ in reversed(selected)
        if isinstance(snapshot.raw_payload, dict)
        and snapshot.raw_payload.get("snapshot_state") == "FIRST_OBSERVED"
    ), None)
    for candidate, _ in selected:
        if not isinstance(candidate.raw_payload, dict):
            continue
        if candidate.raw_payload.get("snapshot_state") != "FINAL_PREGAME":
            continue
        opposite_rows = [
            (snapshot, outcome) for snapshot, outcome in rows
            if snapshot.observed_at == candidate.observed_at
            and snapshot.available_as_of == candidate.available_as_of
            and same_selection(outcome, opposite_outcome)
        ]
        if len(opposite_rows) != 1:
            continue
        opposite, _ = opposite_rows[0]
        if not isinstance(opposite.raw_payload, dict):
            continue
        if opposite.raw_payload.get("snapshot_state") != "FINAL_PREGAME":
            continue
        if decision.market in {"FULL_GAME_SPREAD", "FULL_GAME_TOTAL"}:
            if candidate.point is None or opposite.point is None:
                continue
            if decision.market == "FULL_GAME_SPREAD":
                if abs(float(candidate.point)) != abs(float(opposite.point)):
                    continue
            elif float(candidate.point) != float(opposite.point):
                continue
        recommendation_line = float(decision.line) if decision.line is not None else None
        final_line = float(candidate.point) if candidate.point is not None else None
        same_line = (
            decision.market == "FULL_GAME_MONEYLINE"
            or (recommendation_line is not None and final_line == recommendation_line)
        )
        final_american = int(candidate.american_odds) if candidate.american_odds is not None else None
        first_american = (
            int(first_observed.american_odds)
            if first_observed is not None and first_observed.american_odds is not None else None
        )
        return {
            "recommendation_line": recommendation_line,
            "final_pregame_line": final_line,
            "line_movement": (
                final_line - recommendation_line
                if final_line is not None and recommendation_line is not None else None
            ),
            "recommendation_american": decision.side_american,
            "first_observed_american": first_american,
            "final_pregame_american": final_american,
            "american_price_movement": (
                final_american - first_american
                if final_american is not None and first_american is not None else None
            ),
            # Price-implied CLV is only comparable at the exact decision line.
            "closing_american": final_american if same_line else None,
            "closing_price_same_line": same_line,
            "final_pregame_available_as_of": _aware(candidate.available_as_of).isoformat(),
        }
    return {}


async def seed_demo_data(session: Session, settings: Settings, event_date: date | None = None) -> dict[str, Any]:
    """Create a deterministic, visibly demo-labeled vertical slice without network calls."""
    event_date = event_date or local_today(settings.display_timezone)
    orchestrator = JobOrchestrator(session, settings)
    # Explicit demo seeding replays a coherent pregame snapshot. This keeps the
    # fixture usable for any requested date without bypassing the live first-
    # pitch guard or allowing postgame observations into the forecast.
    first_pitch = min(game.scheduled_at for game in demo_games(event_date))
    demo_data_as_of = first_pitch - timedelta(minutes=90)
    orchestrator.demo = DemoSportsSource(available_as_of=demo_data_as_of)
    steps: dict[str, Any] = {}
    steps["schedule"] = await orchestrator.refresh_schedule(event_date, demo_only=True)
    steps["mlb_data"] = await orchestrator.refresh_mlb_data(event_date)
    steps["weather"] = await orchestrator.refresh_weather(event_date, demo_only=True)
    steps["odds"] = await orchestrator.refresh_odds(event_date, demo_only=True)
    existing = session.scalar(select(Forecast.id).join(Game, Game.id == Forecast.game_id).where(
        Game.starts_at >= local_day_bounds(event_date, settings.display_timezone)[0],
        Game.starts_at < local_day_bounds(event_date, settings.display_timezone)[1],
        Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
    ).limit(1))
    steps["forecasts"] = (
        {"records": 0, "status": "already seeded"}
        if existing
        else await orchestrator.generate_forecasts(event_date, data_as_of=demo_data_as_of)
    )
    return {"mode": "DEMO", "notice": "DEMO DATA — not live sportsbook observations", "steps": steps}


def _job_key(value: str) -> str:
    normalized = "-".join(value.strip().lower().replace("'", "").replace("_", " ").split())
    return ALIASES.get(normalized, normalized)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
