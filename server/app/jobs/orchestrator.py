from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
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
    League,
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
from app.services.game_state_service import GameStateUnavailableError
from app.services.league_scope import MLB, LeagueMismatchError, in_league
from app.services.read_service import ReadService


# Starter-evidence heartbeat: the availability poll persists the verified
# probable-pitcher state for a due game when its latest starter observation is
# older than this many minutes. Inside the pregame fast window (2-minute
# polls) this bounds the freshness authority's lag to ~10 minutes; outside it
# the 10-minute slow poll and the 30-minute schedule refresh already suffice.
STARTER_HEARTBEAT_MINUTES = 10
STARTER_HEARTBEAT_VERSION = "AISP_STARTER_HEARTBEAT_V1"

#: Bounds for V18 research scoring, applied to the population before any result
#: is read. The floor is the study's own start date and it also keeps the sealed
#: 2026-08-14..2026-08-27 Final OOT window out of the pass; the three gamePks are
#: permanently excluded and must never have an outcome read at all.
V18_LEAGUE = "MLB"
V18_EVALUATION_FLOOR = datetime(2026, 8, 28, tzinfo=UTC)
V18_PERMANENT_EXCLUSIONS = frozenset({"823343", "824235", "822693"})

AVAILABLE_JOBS = {
    "refresh-schedule": "Refresh Today's Schedule",
    "bridge-mlb-canonical-events": "Register Upcoming MLB Canonical Events",
    "record-shadow-threshold-ladder": "Record Inert Shadow Threshold Ladder",
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
    "refresh-scoreboard": "Refresh Live Scoreboard (MLB)",
    "collect-nfl-period-breakdowns": "Collect NFL Quarter Breakdowns",
    "collect-nfl-live-scores": "Collect NFL In-Game Scores (tracked games)",
    "collect-nfl-live-scores-espn": "Collect NFL In-Game Scores From ESPN's Free Public Scoreboard (display only)",
    "collect-nfl-pregame-odds": "Collect NFL Pregame Odds (one object, near the checkpoint)",
    "evaluate-nfl-paper-checkpoint": "Evaluate the NFL Paper Policy at its Final-Pregame Checkpoint",
    "evaluate-cfb-paper-checkpoint": "Evaluate the CFB Paper Policy at its Final-Pregame Checkpoint",
    "ingest-cfb-finals": "Admit CFB Finals From Retained Evidence and Score CFB Forecasts (no network)",
    "collect-cfb-pregame-odds": "Collect CFB Pregame Odds (capacity-gated, at most ten objects)",
    "collect-cfb-odds-api": "Collect CFB Moneylines From The Odds API (h2h, one request per kickoff cluster; off by default)",
    "evaluate-nba-paper-checkpoint": "Evaluate the NBA Paper Policy at its Final-Pregame Checkpoint (off by default)",
    "ingest-nba-finals": "Admit NBA Finals From Retained ESPN Objects and Score NBA Channels (no network)",
    "collect-nba-pregame-odds": "Collect NBA Pregame Odds (lane off; no allowance until the owner grants one)",
    "collect-nba-odds-api": "Collect NBA moneylines from the existing included OddsAPI plan (guarded)",
    "import-nba-historical-evidence": "Import Model Studio's 2025-26 NBA Assessment as HISTORICAL Evidence (manual)",
    "recover-governed-finals": "Recover Governed Finals For Completed Games",
    "refresh-provider-usage": "Refresh SportsGameOdds Account Usage",
    "backfill-scoreboard": "Backfill Scoreboard From Retained Captures",
    "controlled-live-validation": "Controlled Gate 2 Live Validation",
    "import-model-studio-forecast-bundle": "Import Model Studio Forecast Bundle",
    "evaluate-shadow-betting-board": "Evaluate Research-Shadow Betting Board",
    "settle-shadow-decisions": "Settle Research-Shadow Decisions",
    "settle-parlay-tickets": "Settle Recorded Virtual Parlay Tickets",
    "refresh-nfl-shadow-schedule": "Refresh NFL Research-Shadow Schedule Authority",
    "materialize-current-nfl-features": "Verify Current NFL Feature Vectors",
    "sync-nfl-foundation-sources": "Sync NFL Current-Season Foundation Sources",
    "generate-nfl-shadow-forecasts": "Generate NFL M1 Early/Current Research-Shadow Forecasts",
    "generate-nfl-final-pregame-forecasts": "Generate NFL M1 Final-Pregame Research-Shadow Forecasts",
    "import-nfl-shadow-forecast-bundle": "Import NFL M1 Research-Shadow Forecast Bundle",
    "consume-model-exchange-research-artifacts": "Consume Verified Model Exchange Research Artifacts",
    "import-nfl-final-pair": "Import NFL FINAL Forecast and Eligibility Pair",
    "import-nhl-forward-delivery": "Import a Verified Forward NHL Research Publication",
    "generate-nhl-forecasts": "Generate NHL Gen1 Forecasts In-Process From the Public Schedule (frozen model; off by default)",
    "evaluate-nhl-paper-checkpoint": "Evaluate the NHL Paper Checkpoint and Settle NHL Positions",
    "collect-nhl-odds-api": "Collect NHL Moneylines From The Odds API (h2h, one request per start-time cluster; off by default)",
    "ingest-nhl-finals-espn": "Ingest NHL Finals From ESPN's Free Public Scoreboard (results for NHL settlement; off by default)",
    "open-model-era-at-boundary": "Open an Armed Model Era at Its Exact Boundary",
    "refresh-nfl-shadow-odds": "Refresh NFL Research-Shadow Odds",
    "collect-odds-checkpoint": "Collect Odds at the Next Budgeted Checkpoint",
    "evaluate-nfl-shadow-board": "Evaluate NFL Research-Shadow Board",
    "freeze-nfl-final-pregame": "Freeze NFL Final-Pregame Research Shadow",
    "finalize-nfl-shadow-games": "Finalize NFL Research-Shadow Games",
    "settle-nfl-shadow-decisions": "Settle NFL Research-Shadow Decisions",
    "recalculate-nfl-shadow-performance": "Recalculate NFL Prospective Performance",
    "ingest-nfl-finals": "Ingest NFL Final Scores (nflverse, no cost)",
    "evaluate-nfl-research-forecasts": "Score NFL Research Forecasts Against Finals",
    "evaluate-mlb-v18-research-forecasts": "Score MLB V18 Research Forecasts Against Finals",
    "open-season-experiment-accounts": "Open Due $1,000 Sport/Season Paper Accounts",
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
    "seal-v18-market-pilot-packets": "Seal V18 Market-Pilot Pregame Packets (read-only research export)",
    "export-v18-market-pilot-outcomes": "Export V18 Market-Pilot Admitted Outcomes (read-only research export)",
    "export-v18-market-pilot-outcomes-v2": "Export V18 Market-Pilot Capture-Bound V2 Outcomes (read-only research export)",
    # Admin-only, never scheduled. Inert while their code registry is empty.
    "plan-postponed-position-voids": "Governed Postponement VOID Corrections: dry run (read-only plan of the registry)",
    "correct-postponed-position-voids": "Governed Postponement VOID Corrections (applies registry entries marked APPLY)",
}

def _checkpoint_already_running(job_name: str) -> dict[str, Any]:
    """Receipt for a checkpoint execution that found the slot already held.

    The decision owners write by reading first and then inserting when nothing
    is there, which is idempotent only while executions are serialised. The
    scheduled runs are -- they share one single-worker lane -- but the admin
    path (``POST /admin/jobs/{job_name}``) awaits the orchestrator on the web
    event loop, outside every lane, so it can overlap a scheduled run.

    Declining is the right answer rather than a loss: the execution holding the
    slot is evaluating the same window from the same rows and will record the
    same outcome per game. What must not happen is a silent no-op, so this is a
    named reason in ``job_runs.result``, distinguishable from "no game was due".
    """

    return {
        "records": 0,
        "reason": "CHECKPOINT_ALREADY_RUNNING",
        "detail": (f"Another execution of {job_name} is in flight in this "
                   "process; this one declined rather than interleave with it. "
                   "No decision and no position were written by this run."),
        "decisions": [],
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
        #: Which entry path is executing: the scheduler, or POST /admin/jobs.
        #: Recorded on the acquisition claim; it never decides who wins.
        self._requested_by_user_id: Any | None = None

    async def run(self, name: str, *, requested_by_user_id: Any | None = None, event_date: date | None = None) -> JobRun:
        self._requested_by_user_id = requested_by_user_id
        canonical = _job_key(name)
        if canonical not in AVAILABLE_JOBS:
            raise ValueError(f"Unknown job '{name}'. Available jobs: {', '.join(AVAILABLE_JOBS)}")
        event_date = event_date or local_today(self.settings.display_timezone)
        from app.jobs.lanes import current_meta

        parameters: dict[str, Any] = {"event_date": event_date.isoformat()}
        scheduling = current_meta()
        if scheduling:
            # When APScheduler fired this run, and how long it waited for its lane.
            parameters["scheduling"] = scheduling
        run = JobRun(
            job_name=canonical,
            status=RunStatus.RUNNING,
            requested_by_user_id=requested_by_user_id,
            started_at=datetime.now(UTC),
            parameters=parameters,
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
            "bridge-mlb-canonical-events": self.bridge_mlb_canonical_events,
            "record-shadow-threshold-ladder": self.record_shadow_threshold_ladder,
            "refresh-mlb-data": self.refresh_mlb_data,
            "refresh-statcast": self.refresh_statcast,
            "refresh-odds": self.refresh_odds,
            "refresh-weather": self.refresh_weather,
            "rebuild-features": self.rebuild_features,
            "generate-forecasts": self.generate_forecasts,
            "run-simulations": self.generate_forecasts,
            "publish-official-positions": self.publish_positions,
            "settle-games": self.settle_games,
            "settle-parlay-tickets": self.settle_parlay_tickets,
            "recalculate-performance": self.recalculate_performance,
            "daily-pipeline": self.daily_pipeline,
            "finalize-games": self.finalize_games,
            "refresh-scoreboard": self.refresh_scoreboard,
            "collect-nfl-period-breakdowns": self.collect_nfl_period_breakdowns,
            "collect-nfl-live-scores": self.collect_nfl_live_scores,
            "collect-nfl-live-scores-espn": self.collect_nfl_live_scores_espn,
            "collect-nfl-pregame-odds": self.collect_nfl_pregame_odds,
            "evaluate-nfl-paper-checkpoint": self.evaluate_nfl_paper_checkpoint,
            "evaluate-cfb-paper-checkpoint": self.evaluate_cfb_paper_checkpoint,
            "ingest-cfb-finals": self.ingest_cfb_finals,
            "collect-cfb-pregame-odds": self.collect_cfb_pregame_odds,
            "collect-cfb-odds-api": self.collect_cfb_odds_api,
            "evaluate-nba-paper-checkpoint": self.evaluate_nba_paper_checkpoint,
            "ingest-nba-finals": self.ingest_nba_finals,
            "collect-nba-pregame-odds": self.collect_nba_pregame_odds,
            "collect-nba-odds-api": self.collect_nba_odds_api,
            "import-nba-historical-evidence": self.import_nba_historical_evidence,
            "recover-governed-finals": self.recover_governed_finals,
            "refresh-provider-usage": self.refresh_provider_usage,
            "backfill-scoreboard": self.backfill_scoreboard,
            "controlled-live-validation": self.controlled_live_validation,
            "import-model-studio-forecast-bundle": self.import_forecast_bundle,
            "evaluate-shadow-betting-board": self.evaluate_shadow_board,
            "settle-shadow-decisions": self.settle_shadow_decisions,
            "refresh-nfl-shadow-schedule": self.refresh_nfl_shadow_schedule,
            "materialize-current-nfl-features": self.materialize_current_nfl_features,
            "sync-nfl-foundation-sources": self.sync_nfl_foundation_sources,
            "generate-nfl-shadow-forecasts": self.generate_nfl_shadow_forecasts,
            "generate-nfl-final-pregame-forecasts": self.generate_nfl_final_pregame_forecasts,
            "import-nfl-shadow-forecast-bundle": self.import_nfl_shadow_bundle,
            "consume-model-exchange-research-artifacts": self.consume_model_exchange_artifacts,
            "import-nfl-final-pair": self.import_nfl_final_pair,
            "import-nhl-forward-delivery": self.import_nhl_forward_delivery,
            "generate-nhl-forecasts": self.generate_nhl_forecasts,
            "evaluate-nhl-paper-checkpoint": self.evaluate_nhl_paper_checkpoint,
            "collect-nhl-odds-api": self.collect_nhl_odds_api,
            "ingest-nhl-finals-espn": self.ingest_nhl_finals_espn,
            "open-model-era-at-boundary": self.open_model_era_at_boundary,
            "refresh-nfl-shadow-odds": self.refresh_nfl_shadow_odds,
            "collect-odds-checkpoint": self.collect_odds_checkpoint,
            "evaluate-nfl-shadow-board": self.evaluate_nfl_shadow_board,
            "freeze-nfl-final-pregame": self.freeze_nfl_final_pregame,
            "finalize-nfl-shadow-games": self.finalize_nfl_shadow_games,
            "settle-nfl-shadow-decisions": self.settle_nfl_shadow_decisions,
            "recalculate-nfl-shadow-performance": self.recalculate_nfl_shadow_performance,
            "ingest-nfl-finals": self.ingest_nfl_finals,
            "evaluate-nfl-research-forecasts": self.evaluate_nfl_research_forecasts,
            "evaluate-mlb-v18-research-forecasts": self.evaluate_mlb_v18_research_forecasts,
            "open-season-experiment-accounts": self.open_season_experiment_accounts,
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
            "seal-v18-market-pilot-packets": self.seal_v18_market_pilot_packets,
            "export-v18-market-pilot-outcomes": self.export_v18_market_pilot_outcomes,
            "export-v18-market-pilot-outcomes-v2": self.export_v18_market_pilot_outcomes_v2,
            "plan-postponed-position-voids": self.plan_postponed_position_voids,
            "correct-postponed-position-voids": self.correct_postponed_position_voids,
        }
        return await handlers[name](event_date)

    async def correct_postponed_position_voids(self, event_date: date) -> dict[str, Any]:
        """Governed VOID corrections for MLB positions settled on a makeup game.

        Writes ONLY what a reviewed commit names in
        ``app.bankroll.postponement_corrections.GOVERNED_VOID_CORRECTIONS``
        with mode APPLY -- one adjustment row plus one audit event per
        position, each refused unless the position still meets
        AISP_MLB_POSTPONED_NOT_PLAYED_ON_ORIGINAL_ET_DATE_V1 and matches what
        the owner reviewed. The registry is empty, so this is inert. Never
        scheduled; ``event_date`` is not used.
        """
        from app.bankroll.postponement_corrections import run

        return run(self.session, actor_user_id=self._requested_by_user_id,
                   job_run_id=self._active_job_run_id)

    async def plan_postponed_position_voids(self, event_date: date) -> dict[str, Any]:
        """The DRY RUN of ``correct-postponed-position-voids``: every registry entry's plan, nothing written.

        Read-only whatever an entry's mode, so the owner can see exactly what
        the apply job would write (previous/corrected result and P&L, delta,
        evidence) before running it. Never scheduled; ``event_date`` is not used.
        """
        from app.bankroll.postponement_corrections import run

        return run(self.session, actor_user_id=self._requested_by_user_id,
                   job_run_id=self._active_job_run_id, dry_run=True)

    async def seal_v18_market_pilot_packets(self, event_date: date) -> dict[str, Any]:
        """Seal due V18 market-pilot pregame packets (protocol f75aeccd...).

        Inert unless V18_MARKET_PILOT_EXPORT_ENABLED. Reads retained rows only
        and writes only its own write-once files: no provider request, no
        decision, no position, no database row besides this job's own run.
        """
        from app.research_exports.v18_market_pilot_seal import run_seal_tick

        return run_seal_tick(self.session, self.settings)

    async def export_v18_market_pilot_outcomes(self, event_date: date) -> dict[str, Any]:
        """Export admitted outcomes for sealed V18 market-pilot episodes.

        Inert unless V18_MARKET_PILOT_OUTCOMES_EXPORT_ENABLED. Reads in its own
        session under a READ ONLY transaction that is always rolled back, so
        this job's run row is untouched; writes only its own write-once files
        outside the packets directory. No provider request, no decision, no
        position, no settlement.
        """
        from app.research_exports.v18_market_pilot_outcomes import run_outcomes_tick

        with Session(bind=self.session.get_bind(), autoflush=False, expire_on_commit=False) as reader:
            return run_outcomes_tick(reader, self.settings)

    async def export_v18_market_pilot_outcomes_v2(self, event_date: date) -> dict[str, Any]:
        """Export V2 (capture-bound) outcomes for sealed V18 market-pilot episodes.

        Inert unless V18_MARKET_PILOT_OUTCOMES_V2_ENABLED. Same isolation as V1:
        its own READ ONLY session, always rolled back; writes only its own
        write-once lane. V1 and its withheld lane are untouched.

        THE PROSPECTIVE CAPTURE RIDES HERE, AND IS OFF
        ----------------------------------------------
        The model-market prospective export needs an owner once Model Studio
        passes its contract, and it must not get a new one. This job is the
        right existing owner: it is already on the ``research-export`` lane
        (``asp-rx-*``), already opens its own reader session, already makes zero
        provider requests, and already runs strictly AFTER the decision-time
        evidence it reads is fixed. So the capture is a TRAILING step of this
        tick, in its own reader session, after the V2 outcome tick has returned.

        It is ``PREPARED_NOT_ACTIVATED``: ``prospective_capture_enabled``
        defaults to false, and the hook returns inert while it is. Nothing is
        registered: no scheduler id, no job, no owner is created here. The V2
        result is returned unchanged whatever the hook does, and a hook failure
        can never fail this job -- the capture must never be able to disturb an
        owner that already has work of its own.
        """
        from app.research_exports.prospective_capture import research_export_tick_hook
        from app.research_exports.v18_market_pilot_outcomes_v2 import run_outcomes_v2_tick

        with Session(bind=self.session.get_bind(), autoflush=False, expire_on_commit=False) as reader:
            result = run_outcomes_v2_tick(reader, self.settings)
        with Session(bind=self.session.get_bind(), autoflush=False, expire_on_commit=False) as reader:
            try:
                result = {**result, "prospectiveCapture": research_export_tick_hook(
                    reader, self.settings)}
            except Exception as error:  # pragma: no cover - defensive, never fails the owner
                result = {**result, "prospectiveCapture": {
                    "enabled": True, "failed": type(error).__name__, "detail": str(error)}}
        # The NFL early-line research export rides the same owner, in its own
        # reader session, after both of the above. Same guarantees: off by
        # default, never fails the owner, calls no provider, writes no row.
        from app.research_exports.nfl_early_line import research_export_tick_hook as early_line_hook

        with Session(bind=self.session.get_bind(), autoflush=False, expire_on_commit=False) as reader:
            try:
                result = {**result, "nflEarlyLine": early_line_hook(reader, self.settings)}
            except Exception as error:  # pragma: no cover - defensive, never fails the owner
                result = {**result, "nflEarlyLine": {
                    "enabled": True, "failed": type(error).__name__, "detail": str(error)}}
        return result

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
            select(Team)
            .join(League, League.id == Team.league_id)
            .where(League.code == MLB, func.upper(Team.abbreviation) == team_abbrev.upper())
        )
        if team is None:
            return None
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        game = self.session.scalar(
            select(Game)
            .join(GameTeam, GameTeam.game_id == Game.id)
            .where(
                GameTeam.team_id == team.id, Game.source_mode == "LIVE", in_league(MLB),
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

    async def consume_model_exchange_artifacts(self, event_date: date) -> dict[str, Any]:
        """Scheduler entry for the research consumer; imports nothing unless enabled."""

        from app.jobs.model_exchange_consumer import run_scheduled_import

        payload = run_scheduled_import(self.session, self.settings)
        self.session.commit()
        return {
            "records": int(payload.get("accepted_count", 0)) + int(payload.get("supplements_accepted", 0)),
            **payload,
        }

    async def import_nfl_final_pair(self, event_date: date) -> dict[str, Any]:
        """Import the NFL FINAL forecast/supplement pair alone, and commit.

        Deadline-bound: a FINAL forecast is usable only once IMPORTED, and the
        last checkpoint tick inside a T-30..T-15 window is a hard edge. This
        owner exists so neither the broad pass's duration nor its
        `max_instances=1` skip can consume that edge. No provider request, no
        model execution, and no other sport's artifacts.
        """

        from app.jobs.model_exchange_consumer import run_nfl_final_import

        payload = run_nfl_final_import(self.session, self.settings)
        return {"records": int(payload.get("records", 0)), **payload}

    async def evaluate_nhl_paper_checkpoint(self, event_date: date) -> dict[str, Any]:
        """AISP_NHL_PAPER_POLICY_V1: decisions for games in their window, then source-backed settlement.

        Local reads only (forecasts, eligibility, stored native quotes, NHL results); never a
        provider request. Without an owner-opened NHL era it records nothing; without
        ``nhl_paper_decisions_enabled`` no position is written. Commits its own work.
        """

        if not self.settings.nhl_paper_checkpoint_enabled:
            return {"records": 0, "status": "DISABLED", "research_only": True}
        from app.jobs.lanes import decision_checkpoint_guard
        from app.nhl.paper import run_checkpoint

        with decision_checkpoint_guard("evaluate-nhl-paper-checkpoint") as mine:
            if not mine:
                return _checkpoint_already_running("evaluate-nhl-paper-checkpoint")
            return run_checkpoint(self.session, self.settings)

    async def ingest_nhl_finals_espn(self, event_date: date) -> dict[str, Any]:
        """NHL finals from ESPN's free public scoreboard into ``nhl_game_results``; $0, OFF by default.

        Asks only for dates holding a real NHL game that started 2h30m+ ago and awaits its result
        (or holds one from this source inside the correction window). Writes contract results and
        marks games final; settles nothing -- ``app.nhl.paper.settle`` in the NHL checkpoint does.
        Commits its own work. With ``nhl_espn_results_enabled`` off a manual run makes no request.
        """

        if not self.settings.nhl_espn_results_enabled:
            return {"records": 0, "requests": 0, "status": "DISABLED", "research_only": True}
        from app.nhl.espn_results import ingest

        return ingest(self.session, self.settings)

    async def import_nhl_forward_delivery(self, event_date: date) -> dict[str, Any]:
        """Consume the producer's forward NHL delivery (interface 0bcf/adf270), and commit.

        Reads only local files: the producer's discovery index, its dated
        workspace and the SAME research exchange. Never a provider request, a
        model run, a decision, a quote or a position. Scheduled only when
        ``nhl_forward_import_enabled`` is on; a manual run while it is off
        returns DISABLED and reads and writes nothing.
        """

        if not self.settings.nhl_forward_import_enabled:
            return {"records": 0, "writes": False, "status": "DISABLED", "research_only": True,
                    "paper_authority": False}
        from app.nhl.forward_delivery import consume_forward_delivery

        try:
            payload = consume_forward_delivery(self.session)
        except Exception:
            self.session.rollback()
            raise
        if payload.get("writes"):
            self.session.commit()
        else:
            self.session.rollback()
        return {"records": int(payload.get("records", 0)), **payload}

    async def generate_nhl_forecasts(self, event_date: date) -> dict[str, Any]:
        """AISP's own NHL Gen1 forecasts for today and tomorrow (ET), from the frozen model. Commits.

        At most five free public GitHub requests (LICENSE, README, release API,
        asset + its redirect hop); fails closed with a named status when the
        rights text changed, the asset's bytes do not match the release digest
        or the evidence cannot be retained. Writes research forecasts only --
        never a decision, quote, price or position. Scheduled only when
        ``nhl_forecast_generation_enabled`` is on; a manual run while it is off
        returns DISABLED, sends nothing and writes nothing.
        """

        if not self.settings.nhl_forecast_generation_enabled:
            return {"records": 0, "writes": False, "status": "DISABLED", "provider_called": False,
                    "research_only": True, "paper_authority": False}
        from app.nhl.inprocess_generator import run_generation

        try:
            payload = await run_generation(self.session, self.settings)
        except Exception:
            self.session.rollback()
            raise
        if payload.get("writes"):
            self.session.commit()
        else:
            self.session.rollback()
        if payload.get("status") != "COMPLETED":
            logger.warning("nhl_forecast_generation_refused", status=payload.get("status"),
                           reason=payload.get("reason"))
        return {**payload, "records": int(payload.get("records", 0))}

    async def open_model_era_at_boundary(self, event_date: date) -> dict[str, Any]:
        """Open an armed model era once -- and only once -- its boundary arrives.

        Runs on a cadence rather than at a one-shot scheduled instant so that a
        process restart across the boundary cannot skip it. Before the boundary
        it reports ARMED and changes nothing; after it, the first run captures
        the capital snapshot and every later run is a no-op, because the
        snapshot column is written exactly once.
        """

        from app.services.v18_champion import V18ChampionService

        service = V18ChampionService(self.session, self.settings)
        armed = service.arm_experiment()
        captured = service.capture_boundary()
        self.session.commit()
        return {"armed": armed, "captured": captured, "status": service.status()}

    async def import_nfl_shadow_bundle(
        self, event_date: date, *, now: datetime | None = None
    ) -> dict[str, Any]:
        """Import the configured verified NFL bundle family; no provider call.

        The mutable index is navigation only. Each listed content-addressed
        object is independently verified before it can reach persistence.
        Unlisted objects (including the protected legacy MLB bundle in the
        shared family) are never opened.

        ONE NARROW EXCEPTION TO FAIL-THE-RUN. A bundle refused only because one
        event's snapshots disagree on exact identity, and whose every kickoff
        has already passed, is skipped and recorded rather than aborting the
        loop. Such a bundle can no longer control a pregame decision, and
        aborting on it left every address sorted after it unimported by this
        job on every run (2026_05_CHI_GB, bundle 77afaba3, 2026-10-11 onward).
        The skipped bundle is still never imported. Any other refusal, or an
        identity conflict in a bundle with a game still to start, fails the run
        exactly as before.
        """
        from pathlib import Path

        from app.forecast_exchange import (
            SportsIdentityInconsistencyError,
            verify_nfl_shadow_bundle_index,
        )
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
        identity_conflicts_skipped: list[dict[str, Any]] = []
        checked_at = now or datetime.now(UTC)
        service = ShadowBoardService(self.session, self.settings)
        for address in selected_addresses:
            object_dir = locator.sha256_root / address
            try:
                result = service.import_bundle(object_dir)
            except SportsIdentityInconsistencyError as exc:
                # Verification refuses before anything is added to the session,
                # so there is nothing to roll back.
                if exc.latest_scheduled_start_utc > checked_at:
                    raise
                skipped = {
                    "bundle_sha256": address,
                    "event_id": exc.event_id,
                    "reason": str(exc),
                    "latest_scheduled_start_utc": exc.latest_scheduled_start_utc.isoformat(),
                    "disposition": "NOT_IMPORTED_ALL_KICKOFFS_PASSED",
                }
                logger.warning("nfl_bundle_identity_conflict_skipped", **skipped)
                identity_conflicts_skipped.append(skipped)
                continue
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
            "identity_conflict_skipped_bundles": len(identity_conflicts_skipped),
            "identity_conflicts_skipped": identity_conflicts_skipped,
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

    async def sync_nfl_foundation_sources(self, event_date: date) -> dict[str, Any]:
        """Refresh the NFL current-season Foundation sources. AISP owns this.

        The producer's inspection found no recurring owner for the repaired sync,
        which meant current-season publication could run on inputs nobody had
        refreshed -- exactly the state that made PIT-NE stale. This job is that
        owner: it runs inside the existing orchestrator, on the existing
        schedule, against the existing Model Studio checkout and virtual
        environment, and it adds no scheduler of its own.

        Success and failure are both receipted. A sync that fails must not look
        like a sync that was skipped, and neither may look like one that
        succeeded -- so the return value always names the outcome, the exit
        status, and how fresh the sources were afterwards.
        """

        return await self._run_nfl_foundation_sync()

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

    async def _run_nfl_foundation_sync(self) -> dict[str, Any]:
        """Invoke the sync and receipt the outcome, whatever it is.

        Three outcomes, never collapsed into each other:

        ``SKIPPED``   the Studio root or interpreter is not configured, so
                      nothing was attempted. Not a failure and not a success.
        ``FAILED``    the child ran and returned non-zero, or timed out. The
                      exit status and the tail of its stderr are recorded.
        ``SUCCEEDED`` the child returned zero.

        Source freshness is read AFTER the sync and reported alongside, because
        "the command succeeded" and "the sources are current" are different
        claims -- a sync can exit zero having found nothing new, and a reader
        needs to see which happened.
        """

        from pathlib import Path

        started = datetime.now(UTC)
        root_text = (self.settings.nfl_model_studio_root or "").strip()
        python_text = (self.settings.nfl_model_studio_python or "").strip()
        if not root_text or not python_text:
            return {
                "status": "SKIPPED",
                "version": NFL_FOUNDATION_SYNC_VERSION,
                "detail": (
                    "NFL_MODEL_STUDIO_ROOT and NFL_MODEL_STUDIO_PYTHON are not both "
                    "configured, so the Foundation sync was not attempted."
                ),
                "attempted": False,
                "started_at_utc": started.isoformat(),
            }

        root = Path(root_text)
        python = Path(python_text)
        if not root.is_dir() or not python.is_file():
            return {
                "status": "SKIPPED",
                "version": NFL_FOUNDATION_SYNC_VERSION,
                "detail": f"Configured Model Studio root or interpreter does not exist: {root}",
                "attempted": False,
                "started_at_utc": started.isoformat(),
            }

        process = await asyncio.create_subprocess_exec(
            str(python), *NFL_FOUNDATION_SYNC_ARGS, cwd=str(root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=NFL_FOUNDATION_SYNC_TIMEOUT_SECONDS)
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            timed_out = True
        finished = datetime.now(UTC)

        succeeded = (not timed_out) and process.returncode == 0
        receipt: dict[str, Any] = {
            "status": "SUCCEEDED" if succeeded else "FAILED",
            "version": NFL_FOUNDATION_SYNC_VERSION,
            "attempted": True,
            "command": [str(python), *NFL_FOUNDATION_SYNC_ARGS],
            "cwd": str(root),
            "exit_code": None if timed_out else process.returncode,
            "timed_out": timed_out,
            "started_at_utc": started.isoformat(),
            "finished_at_utc": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 3),
            "stdout_tail": stdout.decode("utf-8", errors="replace")[-1500:],
            "stderr_tail": stderr.decode("utf-8", errors="replace")[-1500:],
            "owner": (
                "AISP orchestrator job 'sync-nfl-foundation-sources', and the "
                "CURRENT generation path, which runs it before publishing."
            ),
        }
        receipt["source_freshness"] = _nfl_foundation_freshness(
            stdout.decode("utf-8", errors="replace"))
        if not succeeded:
            logger.error(
                "nfl_foundation_sync_failed",
                exit_code=receipt["exit_code"], timed_out=timed_out,
            )
        else:
            logger.info(
                "nfl_foundation_sync_succeeded",
                duration_seconds=receipt["duration_seconds"],
            )
        return receipt

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
        # Ordinary CURRENT publication consumes Foundation sources, so the sync
        # runs first and its receipt travels with the result. A failed sync does
        # NOT abort publication: the producer's own input-value checks already
        # preserve unchanged forecasts, and refusing to publish because a
        # refresh failed would turn a stale-input problem into a no-forecast
        # problem. It is recorded loudly instead.
        foundation: dict[str, Any] | None = None
        if "CURRENT" in snapshot_types:
            foundation = await self._run_nfl_foundation_sync()

        producer = await _invoke_nfl_shadow_producer(self.settings, snapshot_types)
        if producer["status"] == "NO_ELIGIBLE_UPCOMING_GAMES":
            return {
                "records": 0,
                "status": producer["status"],
                "producer_called": True,
                "snapshot_types": list(snapshot_types),
                "games": 0,
                "imported": False,
                "foundation_sync": foundation,
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

    async def collect_odds_checkpoint(self, event_date: date) -> dict[str, Any]:
        """Spend at most one credit-bearing request, only when a checkpoint is due.

        This is the replacement for blanket polling. The decision is made
        entirely from persisted rows -- the canonical schedule and the provider
        request ledger -- so it is idempotent across restarts, cannot double
        spend within a window, and never polls a sport with no open game. When
        nothing is due it returns the reason and makes no request at all.
        """

        from app.services.odds_collection_service import OddsCollectionService

        service = OddsCollectionService(self.session, self.settings)
        decision = service.decide()
        if not decision.should_request or decision.checkpoint is None:
            # The primary cannot supply prices on this tick -- the floor is
            # latched, the budget is spent, or no checkpoint is due. That is
            # exactly and only when the temporary MLB fallback may run. It
            # never pre-empts a checkpoint the primary can serve, never
            # requests a game past its cutoff, and refuses itself by name on
            # cooldown, on budget, or once its dated window closes.
            from app.services import sgo_collection

            fallback = sgo_collection.collect_for_open_cutoffs(
                self.session, self.settings, job_run_id=self._active_job_run_id,
            )
            return {
                "records": int(fallback.get("quote_rows") or 0),
                "requested": False,
                "provider_called": False,
                "fallback": fallback,
                **decision.as_dict(),
            }

        checkpoint = decision.checkpoint
        markets = list(checkpoint.markets)
        if checkpoint.sport_key == "americanfootball_nfl":
            outcome = await self.refresh_nfl_shadow_odds(event_date, markets=markets)
        else:
            outcome = await self.refresh_odds(event_date, markets=markets)
        return {
            "requested": True,
            "provider_called": True,
            "league_code": checkpoint.league_code,
            "checkpoint_utc": checkpoint.checkpoint.isoformat(),
            "credit_cost": checkpoint.credit_cost,
            "markets": markets,
            "covered_cutoffs": checkpoint.covered_cutoffs,
            "reason": decision.reason,
            "detail": decision.detail,
            **outcome,
        }

    async def refresh_nfl_shadow_odds(
        self, event_date: date, *, markets: list[str] | None = None,
    ) -> dict[str, Any]:
        """One quota-governed current-NFL Odds API request and exact-ID ingest."""
        if not self.settings.nfl_shadow_enabled or not self.settings.nfl_shadow_odds_enabled:
            return {"records": 0, "status": "DISABLED", "provider_called": False}
        market_list = list(markets) if markets else list(self.settings.odds_market_list)
        if not self.settings.the_odds_api_key:
            raise SourceError("THE_ODDS_API", "THE_ODDS_API_KEY is not configured", retryable=False)
        self._enforce_odds_quota_policy(
            len(market_list) * max(1, len(self.settings.odds_regions.split(","))),
        )
        now = datetime.now(UTC)
        ledger = self._reserve_provider_request(
            "THE_ODDS_API", "americanfootball_nfl_odds",
            controlled_live_request=False,
            request_shape={
                "sport_key": "americanfootball_nfl",
                "regions": self.settings.odds_regions,
                "markets": sorted(market_list),
                "request_window": int(
                    now.timestamp() // (max(1, self.settings.odds_pregame_refresh_minutes) * 60)
                ),
            },
        )
        self.session.commit()
        try:
            response = await TheOddsAPIAdapter(
                self.settings.the_odds_api_key,
                market_list,
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
                response_metadata={
                    "request_shape": (ledger.response_metadata or {}).get("request_shape"),
                    "sport_key": "americanfootball_nfl", **result,
                },
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
                response_metadata={
                    "request_shape": (ledger.response_metadata or {}).get("request_shape"),
                    "sport_key": "americanfootball_nfl", "response_received": False,
                },
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
                    response_metadata={
                        "request_shape": (ledger.response_metadata or {}).get("request_shape"),
                        "sport_key": "americanfootball_nfl", **ingested,
                    },
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
                        "request_shape": (ledger.response_metadata or {}).get("request_shape"),
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

    async def ingest_nfl_finals(self, event_date: date) -> dict[str, Any]:
        """Bring completed NFL games into AISP automatically, at zero cost.

        Reads the nflverse schedules release -- a public CC-BY-4.0 GitHub asset
        with no key and no metered allowance -- and asks its 43-byte timestamp
        file whether anything changed before downloading the schedule at all.
        It deliberately does NOT call The Odds API scores endpoint, which would
        answer the same question out of the paid allowance reserved for
        sportsbook prices.

        Runs BEFORE ``evaluate-nfl-research-forecasts`` so a game that finished
        in the last hour is scored on the same pass it is detected. Bound to
        the single supervised process, and off entirely when
        ``NFL_FINAL_INGEST_ENABLED`` is false.
        """

        from app.services.nfl_final_ingest import NflFinalIngestService

        if not self.settings.nfl_final_ingest_enabled:
            return {"records": 0, "skipped": "NFL_FINAL_INGEST_DISABLED",
                    "detail": "The kill switch NFL_FINAL_INGEST_ENABLED is off.",
                    "provider_calls": 0, "models_executed": 0,
                    "positions_created": 0, "official_decisions_created": 0,
                    "event_date": event_date.isoformat()}
        if not self.settings.owns_nfl_final_ingest:
            return {"records": 0, "skipped": "NOT_THE_FINAL_INGEST_OWNER",
                    "detail": "This process is not the supervised owner, so it does not ingest.",
                    "provider_calls": 0, "models_executed": 0,
                    "positions_created": 0, "official_decisions_created": 0,
                    "event_date": event_date.isoformat()}

        summary = await NflFinalIngestService(self.session).ingest(archive=self.archive)
        self.session.flush()
        return {
            "records": summary.get("observations_written", 0),
            "status": summary.get("status"),
            "source": summary.get("source"),
            "games_pending": summary.get("games_pending", 0),
            "finals_detected": summary.get("finals_detected", 0),
            "observations_written": summary.get("observations_written", 0),
            "observations_reused": summary.get("observations_reused", 0),
            "projections_updated": summary.get("projections_updated", 0),
            "requests": summary.get("requests", 0),
            "feed_last_updated": summary.get("feed_last_updated"),
            "skip_reasons": sorted({entry.get("skipped") for entry in summary.get("skipped", [])
                                    if entry.get("skipped")}),
            "sportsbook_quote_credits_spent": 0,
            "provider_calls": 0, "models_executed": 0,
            "positions_created": 0, "official_decisions_created": 0,
            "event_date": event_date.isoformat(),
        }

    async def evaluate_nfl_research_forecasts(self, event_date: date) -> dict[str, Any]:
        """Score every final NFL game's frozen pre-kickoff forecast. Safe to rerun.

        Runs AFTER the result refresh, and reads only what that refresh already
        persisted: no provider is called, no model is executed, no decision or
        position is created, and MLB settlement and odds collection are not
        touched. A game with no authoritative final is skipped with that reason
        rather than scored against a guess, and a forecast published after
        kickoff is excluded by the evaluator itself -- this job cannot opt out
        of that rule because it never chooses a forecast.
        """

        from app.services.forecast_evaluation import ForecastEvaluationService

        summary = ForecastEvaluationService(self.session).evaluate_league(league_code="NFL")
        self.session.flush()
        return {
            "records": summary["scored"],
            "final_games": summary["final_games"],
            "evaluations_written": summary["scored"],
            "evaluations_reused": summary["reused"],
            "evaluations_superseded": summary.get("superseded", 0),
            "games_skipped": len(summary["skipped"]),
            "skip_reasons": sorted({entry.get("skipped") for entry in summary["skipped"]
                                    if entry.get("skipped")}),
            "scoring_version": summary["scoring_version"],
            "provider_calls": 0, "models_executed": 0,
            "positions_created": 0, "official_decisions_created": 0,
            "event_date": event_date.isoformat(),
        }

    async def open_season_experiment_accounts(self, event_date: date) -> dict[str, Any]:
        """Open each sport/season paper account once, at its configured boundary.

        This is the only thing that creates the $1,000. It is idempotent in
        three independent ways, because a funding job that runs twice is the
        one failure this program cannot absorb: the service checks for an
        existing opening row, the table has a unique constraint on
        ``(account_id, kind)``, and a second unique constraint on the
        deterministic idempotency key. Any one of them alone would be enough;
        all three are cheap.

        It runs on the existing scheduler as an ordinary interval job. No
        second scheduler, no one-shot trigger that cannot be re-run, and every
        contribution carries the JobRun id that actually performed it, so
        "the season was funded" is evidenced by a receipt rather than by a
        schedule that was supposed to fire.

        Structurally, this job cannot: execute a model, create a decision, open
        or settle a position, call a provider, or move capital between
        accounts. It contributes an opening balance and links model eras to the
        season they ran in.
        """

        from datetime import UTC as _UTC, datetime as _datetime

        from app.services.experiment_accounts import ExperimentAccountProvisioner

        provisioner = ExperimentAccountProvisioner(self.session, self.settings)
        report = provisioner.run(
            today=event_date,
            now=_datetime.now(_UTC),
            executed_by="job:open-season-experiment-accounts",
            job_run_id=self._active_job_run_id,
        )
        self.session.commit()
        payload = report.as_dict()
        return {
            # No "job" key: the orchestrator's completion log already supplies
            # one, and a duplicate makes the structured logger raise.
            "event_date": event_date.isoformat(),
            "records": len(payload["accounts_funded"]),
            "provider_calls": 0,
            "models_executed": 0,
            "decisions_created": 0,
            "positions_created": 0,
            **payload,
        }

    async def evaluate_mlb_v18_research_forecasts(self, event_date: date) -> dict[str, Any]:
        """Score every eligible final MLB game's frozen pregame V18 forecast.

        The same evaluator, the same freeze rule and the same provenance rule
        the NFL path uses -- only the league and its bounds differ. It reads
        the final that the live MLB state observations already persisted, so no
        provider is called and no new final evidence is invented.

        What it cannot do, structurally rather than by convention: execute a
        model, create a decision, open or settle a position, move a bankroll,
        change a policy or threshold, or edit a forecast. The V18 model era
        stays ARMED and unactivated throughout; a research score is a
        measurement of a forecast, not authority for one.

        Bounds are applied to the population BEFORE any result is read: the
        study floor of 2026-08-28, which also keeps the sealed 2026-08-14..27
        Final OOT window out, and the three permanently excluded gamePks.
        """

        from app.services.forecast_evaluation import ForecastEvaluationService

        summary = ForecastEvaluationService(self.session).evaluate_league(
            league_code=V18_LEAGUE,
            earliest_start=V18_EVALUATION_FLOOR,
            excluded_source_event_ids=V18_PERMANENT_EXCLUSIONS,
            require_shadow_forecast=True,
        )
        self.session.flush()
        verified = qualified = 0
        for game in summary["games"]:
            for record in game.get("records", ()):
                clock = record.get("publication_clock")
                if clock == "VERIFIED_INDEPENDENT_PUBLICATION":
                    verified += 1
                elif clock:
                    qualified += 1
        return {
            "records": summary["scored"],
            "final_games": summary["final_games"],
            "evaluations_written": summary["scored"],
            "evaluations_reused": summary["reused"],
            "evaluations_superseded": summary.get("superseded", 0),
            "games_skipped": len(summary["skipped"]),
            "skip_reasons": sorted({entry.get("skipped") for entry in summary["skipped"]
                                    if entry.get("skipped")}),
            "verified_publication_clock": verified,
            "qualified_publication_clock": qualified,
            "earliest_start": summary.get("earliest_start"),
            "excluded_source_event_ids": summary.get("excluded_source_event_ids", []),
            "scoring_version": summary["scoring_version"],
            "provider_calls": 0, "models_executed": 0,
            "positions_created": 0, "official_decisions_created": 0,
            "model_era_activated": False, "bankroll_mutated": False,
            "event_date": event_date.isoformat(),
        }

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
        from app.jobs.lanes import decision_checkpoint_guard

        with decision_checkpoint_guard("freeze-shadow-experiment") as mine:
            if not mine:
                return _checkpoint_already_running("freeze-shadow-experiment")
            return await self._freeze_shadow_experiment(event_date)

    async def _freeze_shadow_experiment(self, event_date: date) -> dict[str, Any]:
        """The freeze itself. Called only while this process holds the slot."""
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
        funding_pause_blocks: list[dict[str, Any]] = []
        for experiment in service.active_experiments():
            phases: dict[str, Any] = {}
            for phase in ExperimentDecisionPhase:
                result = service.freeze(event_date, phase=phase, experiment=experiment)
                phases[phase.value] = result
                primary += int(result.get("primary", 0))
                if result.get("funding_pause"):
                    funding_pause_blocks.append(result["funding_pause"])
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
        receipt = {"records": primary, "experiments": results, "pregame_evidence_monitor": monitor}
        if funding_pause_blocks:
            # MLB_V18_FUNDING_PAUSE_V1: one job-level receipt. ``results`` is
            # keyed by decision policy, which the V18 era shares with the
            # practical-policy experiment, so the per-experiment blocks are
            # collected here rather than read back out of it.
            from app.services.mlb_v18_funding_pause import aggregate_receipts

            receipt["mlb_v18_funding_pause"] = aggregate_receipts(funding_pause_blocks)
        return receipt

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

    async def refresh_scoreboard(self, event_date: date) -> dict[str, Any]:
        """Observed MLB scoreboard state for the slate. Display-only.

        One request against the free MLB Stats API, reading the linescore the
        schedule call has always hydrated and the DTO has always discarded. It
        writes nothing to game_state_observations, creates no decision and
        settles nothing; the governed final ingest and the settlement owner are
        untouched.
        """

        from app.scoreboard.collect import collect_mlb_scoreboard, should_run_fast
        from app.services.rff_capture import capture_due_rff_snapshots

        # Passive RFF Core capture rides this job's two-minute cadence. It runs
        # BEFORE the watch-window gate, because a pregame reading is due when no
        # game is live; it makes no provider request and never raises, so a
        # capture problem cannot stop the scoreboard. Research/display only.
        rff = capture_due_rff_snapshots(self.session, now=datetime.now(UTC))

        if not should_run_fast(self.session, now=datetime.now(UTC)):
            return {"records": 0, "league": "MLB", "requests": 0,
                    "reason": "NO_GAME_IN_WATCH_WINDOW", "rff_capture": rff}
        result = await collect_mlb_scoreboard(self.session, event_date=event_date)
        result["rff_capture"] = rff
        return result

    async def collect_nfl_period_breakdowns(self, event_date: date) -> dict[str, Any]:
        """One budget-governed pass for NFL quarter breakdowns still missing.

        Spends SportsGameOdds event objects, one per completed game, and only
        for games recent enough to be displayed. Refuses rather than dipping
        into the allowance reserve, and reports the budget either way.
        """

        from app.scoreboard.collect import collect_nfl_breakdowns

        return collect_nfl_breakdowns(self.session, self.settings)

    async def collect_nfl_live_scores(self, event_date: date) -> dict[str, Any]:
        """In-game score for tracked NFL games, within a hard object budget.

        Follows games a reader has something riding on, only between kickoff and
        a recorded final, and refuses rather than spending into either the
        provider reserve or the floor held for MLB pregame odds. Every refusal
        reports the arithmetic that produced it.
        """

        from app.scoreboard.nfl_live import collect_nfl_live

        return collect_nfl_live(self.session, self.settings)

    async def collect_nfl_live_scores_espn(self, event_date: date) -> dict[str, Any]:
        """In-game NFL score from ESPN's free public scoreboard; display only, $0."""

        from app.scoreboard.nfl_espn import collect_nfl_live_espn

        return collect_nfl_live_espn(self.session, self.settings)

    async def collect_nfl_pregame_odds(self, event_date: date) -> dict[str, Any]:
        """One SportsGameOdds event object for an NFL game near its checkpoint.

        The NFL paper policy qualifies against a quote that must still be fresh
        at its final pregame checkpoint, and the 240-minute freshness rule means
        a price captured earlier in the day is already disqualified by kickoff.
        So the object is spent late and only once, charged to the NFL pregame
        lane the owner funded on 2026-09-14 -- never to the live-score lane.
        """

        from app.services.sgo_nfl_pregame import collect_nfl_pregame

        # The two REAL entry paths reach the same collector, and therefore the
        # same atomic claim: the scheduler calls `run` with no user, and
        # `POST /admin/jobs/{job_name}` calls it with the requesting user. The
        # path is recorded on the claim; it never decides who wins.
        invocation_path = "ADMINISTRATIVE" if self._requested_by_user_id else "SCHEDULED"
        result = collect_nfl_pregame(
            self.session, self.settings, job_run_id=self._active_job_run_id,
            invocation_path=invocation_path)
        # THE FALLBACK, AND ONLY ON A REFUSAL. Off by default; with the flag off
        # the SGO receipt is returned exactly as before, with no added key.
        from app.services import nfl_odds_api_fallback as fallback

        if fallback.enabled(self.settings) and fallback.sgo_refused(result):
            result = {**result, "odds_api_fallback": await self._nfl_odds_api_fallback(
                invocation_path=invocation_path)}
        return result

    async def _nfl_odds_api_fallback(
        self, *, invocation_path: str = "SCHEDULED", now: datetime | None = None,
    ) -> dict[str, Any]:
        """ONE The Odds API NFL request (h2h, us) for a due T-26 cluster SGO refused.

        The order of refusals is the order of costs, as in the SGO collector:
        process ownership and credentials, then something due, then the
        fallback's own daily cap, then every existing Odds API guard, then the
        atomic per-cluster claim -- and only then a reservation and the request.
        A refusal or a failed request returns a receipt; it never fails the SGO
        job it rides on.
        """

        from app.services import nfl_odds_api_fallback as fallback
        from app.services.nfl_shadow_odds_service import NFLShadowOddsService
        from app.services.sgo_nfl_pregame import claim_phase

        moment = now or datetime.now(UTC)
        # Whatever the SGO collector decided is kept before anything here can
        # roll back.
        self.session.commit()
        base: dict[str, Any] = {"provider": fallback.PROVIDER, "sport_key": fallback.SPORT_KEY,
                                "markets": list(fallback.MARKETS), "regions": fallback.REGIONS}
        if not self.settings.owns_odds_collection:
            return {**base, "attempted": False, "reason": "NOT_THE_ODDS_COLLECTION_OWNER"}
        if not self.settings.the_odds_api_key:
            return {**base, "attempted": False, "reason": "THE_ODDS_API_KEY_NOT_CONFIGURED"}
        due = fallback.due_games(self.session, now=moment)
        if not due:
            return {**base, "attempted": False, "reason": "NO_GAME_IN_THE_CHECKPOINT_PHASE"}
        cap = fallback.daily_request_cap(self.settings)
        used = fallback.requests_today(self.session, now=moment,
                                       display_timezone=self.settings.display_timezone)
        if used >= cap:
            return {**base, "attempted": False, "reason": "DAILY_REQUEST_CAP_REACHED",
                    "requests_today": used, "daily_request_cap": cap, "games_due": due}
        try:
            self._enforce_odds_quota_policy(
                fallback.CREDIT_COST, cooldown_endpoint_key=fallback.ENDPOINT_KEY)
        except SourceError as exc:
            self.session.rollback()
            return {**base, "attempted": False, "reason": "ODDS_QUOTA_POLICY_REFUSED",
                    "detail": str(exc), "games_due": due}

        # THE ATOMIC CLAIM, one per (kickoff cluster, phase), committed before
        # any reservation or provider work. A cluster another run holds is
        # dropped; if every cluster is held, this run is inert.
        claimed = []
        for (kickoff, phase), game_ids in sorted(fallback.clusters(due).items()):
            claim = claim_phase(
                self.session, kickoff_cluster=kickoff, phase=phase, due_game_ids=game_ids,
                invocation_id=str(self._active_job_run_id or "no-job-run"),
                invocation_path=invocation_path, now=moment,
                lane=fallback.CLAIM_LANE, provider=fallback.PROVIDER)
            if claim is not None:
                claimed.append((kickoff, phase, game_ids, claim))
        if not claimed:
            return {**base, "attempted": False, "reason": "PHASE_ALREADY_CLAIMED",
                    "games_due": due}
        scope = sorted({gid for _k, _p, ids, _c in claimed for gid in ids})
        request_shape = {
            "sport_key": fallback.SPORT_KEY, "regions": fallback.REGIONS,
            "markets": sorted(fallback.MARKETS),
            "trigger": "SGO_REFUSED",
            "clusters": [{"kickoff_cluster_utc": k.isoformat(), "phase": p}
                         for k, p, _ids, _c in claimed],
            "games": scope,
        }
        try:
            ledger = self._reserve_provider_request(
                fallback.PROVIDER, fallback.ENDPOINT_KEY,
                controlled_live_request=False, request_shape=request_shape)
        except SourceError as exc:
            self.session.rollback()
            return {**base, "attempted": False, "reason": "ODDS_LANE_BUDGET_REFUSED",
                    "detail": str(exc), "games_due": due, "clusters": request_shape["clusters"]}
        for _k, _p, _ids, claim in claimed:
            claim.reservation_id = ledger.id
        self.session.commit()

        response_received = False
        try:
            response = await TheOddsAPIAdapter(
                self.settings.the_odds_api_key, list(fallback.MARKETS), fallback.REGIONS,
                sport_key=fallback.SPORT_KEY,
            ).get_market_snapshot()
            response_received = True
            archive = self.archive.write(response.envelope.source, response.raw_payload)
            events = [OddsEventDTO.model_validate(item) for item in response.envelope.data]
            result = NFLShadowOddsService(self.session).ingest(
                events, available_as_of=response.envelope.fetched_at, game_ids=set(scope))
            metadata = response.envelope.metadata or {}
            summary = {
                "sport_key": fallback.SPORT_KEY, "trigger": "SGO_REFUSED",
                "archive_path": str(archive.raw_file_reference),
                "archive_payload_sha256": archive.payload_checksum,
                "clock_refusals": list(metadata.get("clock_refusals") or []),
                **result,
            }
            self.session.add(ProviderRequestLedger(
                provider=ledger.provider, endpoint_key=ledger.endpoint_key,
                requested_at=ledger.requested_at, completed_at=datetime.now(UTC),
                status="SUCCEEDED", request_fingerprint=ledger.request_fingerprint,
                quota_cost=_int_or_none(metadata.get("requests_last")),
                quota_remaining=_int_or_none(metadata.get("requests_remaining")),
                records_received=len(events), controlled_live_request=False,
                source_mode="LIVE", job_run_id=self._active_job_run_id,
                reservation_id=ledger.id,
                response_metadata={
                    "request_shape": request_shape,
                    "requests_used": _int_or_none(metadata.get("requests_used")),
                    "response_received": True, **summary,
                },
            ))
            self._ingestion_run(response.envelope, archive.raw_file_reference, result["quotes"],
                                "collect-nfl-pregame-odds")
            self.session.commit()
            return {**base, "attempted": True, "reason": "COLLECTED",
                    "reservation_id": str(ledger.id), "games_due": due,
                    "games_selected": scope, "quota": metadata, **summary}
        except Exception as exc:  # one attempt; the owed retry is the only second one
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
                    "request_shape": request_shape, "sport_key": fallback.SPORT_KEY,
                    "response_received": response_received,
                    # explicit evidence only: a lost response or missing quota is UNKNOWN
                    "zero_charge_proven": (not response_received)
                    and fallback.zero_charge_proven(exc),
                },
            ))
            self.session.commit()
            return {**base, "attempted": True, "reason": "REQUEST_FAILED",
                    "error_type": type(exc).__name__, "response_received": response_received,
                    "zero_charge_proven": (not response_received)
                    and fallback.zero_charge_proven(exc),
                    "reservation_id": str(ledger.id), "games_due": due,
                    "games_selected": scope}

    async def evaluate_nfl_paper_checkpoint(self, event_date: date) -> dict[str, Any]:
        """The NFL paper policy's decision, at the final-pregame window.

        Always produces an outcome per game in the window -- qualified, declined
        or blocked, each with its exact reason -- because a checkpoint that came
        and went with no record is indistinguishable from a job that never ran.
        Reads the frozen forecast and the stored quote; calls no provider.
        """

        from app.services.runtime_source_proof import emit
        try:
            from app.jobs.lanes import decision_checkpoint_guard
            from app.services.nfl_paper_decision import evaluate

            with decision_checkpoint_guard("evaluate-nfl-paper-checkpoint") as mine:
                if not mine:
                    return _checkpoint_already_running("evaluate-nfl-paper-checkpoint")
                return evaluate(self.session, self.settings)
        finally:
            emit(stage="nfl_paper_checkpoint_completed")

    async def evaluate_cfb_paper_checkpoint(self, event_date: date) -> dict[str, Any]:
        """AISP_CFB_PAPER_POLICY_V1 at T-30..T-15: one recorded outcome per game, always with its reason.

        Refreshes the CFB discovery first (local files only) so the lease it
        judges is the newest one. Calls no provider.
        """

        from app.cfb.paper_decision import evaluate
        from app.jobs.lanes import decision_checkpoint_guard

        with decision_checkpoint_guard("evaluate-cfb-paper-checkpoint") as mine:
            if not mine:
                return _checkpoint_already_running("evaluate-cfb-paper-checkpoint")
            return evaluate(self.session, self.settings)

    async def ingest_cfb_finals(self, event_date: date) -> dict[str, Any]:
        """Admit CFB finals from the producer's retained evidence, then score. No network.

        Settlement stays with finalize-games, which settles any experiment's
        position once its game is FINAL with scores.
        """

        from app.cfb.evaluation import evaluate_cfb
        from app.cfb.final_ingest import ingest_retained_finals

        finals = ingest_retained_finals(self.session, self.settings)
        self.session.commit()
        scoring = evaluate_cfb(self.session)
        self.session.commit()
        return {"records": int(finals.get("observations_written", 0)) + int(scoring.get("scored", 0)),
                "finals": {k: v for k, v in finals.items() if k != "skipped"},
                "finals_skipped": finals.get("skipped", [])[:20], "scoring": scoring}

    async def collect_cfb_pregame_odds(self, event_date: date) -> dict[str, Any]:
        """CFB SportsGameOdds prices, only from genuinely uncommitted capacity.

        Spends nothing while the lane is off, its identities are unresolved or
        uncommitted capacity is zero; see app/cfb/sgo_price.py.
        """

        from app.cfb.sgo_price import collect

        result = collect(self.session, self.settings)
        self.session.commit()
        return {"records": int(result.get("objects_spent", 0)), **result}

    async def collect_cfb_odds_api(self, event_date: date) -> dict[str, Any]:
        """CFB moneylines from The Odds API (americanfootball_ncaaf, h2h) for the CFB paper checkpoint.

        Requests nothing while CFB_ODDS_API_ENABLED is off or no forecast CFB
        game is in its buy window. Otherwise ONE request, reserved in the
        provider ledger first and passed through every existing Odds API guard
        plus the CFB_RESEARCH credit lane; each event is bound or refused on its
        own (app/cfb/odds_api_price.py). SportsGameOdds is never touched.
        """

        from app.cfb import odds_api_price as cfb_odds

        if not self.settings.cfb_odds_api_enabled:
            return {"records": 0, "status": "DISABLED", "provider_called": False}
        planned = cfb_odds.plan(self.session)
        if not planned["due"]:
            return {"records": 0, "status": "NOT_DUE", "provider_called": False, "plan": planned}
        if not self.settings.the_odds_api_key:
            raise SourceError("THE_ODDS_API", "THE_ODDS_API_KEY is not configured", retryable=False)
        market_list = list(cfb_odds.MARKETS)
        self._enforce_odds_quota_policy(
            len(market_list) * max(1, len(self.settings.odds_regions.split(","))),
            cooldown_endpoint_key=cfb_odds.ENDPOINT_KEY,       # this lane's own cooldown only (MEDIUM-2)
        )
        now = datetime.now(UTC)
        ledger = self._reserve_provider_request(
            cfb_odds.PROVIDER, cfb_odds.ENDPOINT_KEY,
            controlled_live_request=False,
            request_shape={
                "sport_key": cfb_odds.SPORT_KEY,
                "regions": self.settings.odds_regions,
                "markets": sorted(market_list),
                "request_window": int(
                    now.timestamp() // (max(1, self.settings.odds_pregame_refresh_minutes) * 60)
                ),
                "due_games": [g["source_game_id"] for g in planned["due_games"]],
            },
        )
        self.session.commit()
        response_received = False
        try:
            response = await TheOddsAPIAdapter(
                self.settings.the_odds_api_key,
                market_list,
                self.settings.odds_regions,
                sport_key=cfb_odds.SPORT_KEY,
            ).get_market_snapshot()
            response_received = True
            archive = self.archive.write(response.envelope.source, response.raw_payload)
            received_at = response.envelope.fetched_at
            events = [OddsEventDTO.model_validate(item) for item in response.envelope.data]
            bound, event_receipts = cfb_odds.bind_events(self.session, events, received_at=received_at)
            written = cfb_odds.persist(
                self.session, bound, requested_at=ledger.requested_at, received_at=received_at,
                response={"archive_path": str(archive.raw_file_reference),
                          "archive_payload_sha256": archive.payload_checksum,
                          "ledger_reservation_id": str(ledger.id)},
            )
            metadata = response.envelope.metadata or {}
            summary = {
                "sport_key": cfb_odds.SPORT_KEY, "events": len(events), "bound": len(bound),
                "created": written["created"], "existing": written["existing"],
                "archive_path": str(archive.raw_file_reference),
                "archive_payload_sha256": archive.payload_checksum,
                # Bookmakers the adapter skipped because they carried no clock.
                "clock_refusals": list(metadata.get("clock_refusals") or []),
            }
            self.session.add(ProviderRequestLedger(
                provider=ledger.provider, endpoint_key=ledger.endpoint_key,
                requested_at=ledger.requested_at, completed_at=datetime.now(UTC),
                status="SUCCEEDED", request_fingerprint=ledger.request_fingerprint,
                quota_cost=_int_or_none(metadata.get("requests_last")),
                quota_remaining=_int_or_none(metadata.get("requests_remaining")),
                records_received=len(events), controlled_live_request=False,
                source_mode="LIVE", job_run_id=self._active_job_run_id,
                reservation_id=ledger.id,
                response_metadata={
                    "request_shape": (ledger.response_metadata or {}).get("request_shape"),
                    "requests_used": _int_or_none(metadata.get("requests_used")),
                    "response_received": True, **summary,
                    "event_receipts": event_receipts, "quote_receipts": written["quote_receipts"],
                },
            ))
            self._ingestion_run(response.envelope, archive.raw_file_reference, written["created"],
                                "collect-cfb-odds-api")
            self.session.commit()
            return {"records": written["created"], "provider_called": True, "quota": metadata,
                    "plan": planned, **summary, "event_receipts": event_receipts,
                    "quote_receipts": written["quote_receipts"]}
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
                    "request_shape": (ledger.response_metadata or {}).get("request_shape"),
                    "sport_key": cfb_odds.SPORT_KEY, "response_received": response_received,
                },
            ))
            self.session.commit()
            raise

    async def collect_nhl_odds_api(self, event_date: date) -> dict[str, Any]:
        """NHL moneylines from The Odds API (icehockey_nhl, h2h) for the NHL paper checkpoint.

        Requests nothing while NHL_ODDS_API_ENABLED is off, or while no SCHEDULED
        NHL game with an admissible, decision-capable forecast is in its buy
        window (app/nhl/odds_api_price.py ``plan``). Otherwise ONE request,
        reserved in the provider ledger first and passed through every existing
        Odds API guard plus the NHL_RESEARCH credit lane (zero unless funded);
        each event is bound or refused on its own (zero start tolerance). The
        same reserve -> adapter -> archive -> bind -> persist -> SUCCEEDED/FAILED
        flow as the CFB lane. SportsGameOdds is never touched.
        """

        from app.nhl import odds_api_price as nhl_odds

        if not self.settings.nhl_odds_api_enabled:
            return {"records": 0, "status": "DISABLED", "provider_called": False}
        planned = nhl_odds.plan(self.session)
        if not planned["due"]:
            return {"records": 0, "status": "NOT_DUE", "provider_called": False, "plan": planned}
        if not self.settings.the_odds_api_key:
            raise SourceError("THE_ODDS_API", "THE_ODDS_API_KEY is not configured", retryable=False)
        market_list = list(nhl_odds.MARKETS)
        # NHL yields to NFL -- and to CFB only while ODDS_NHL_YIELDS_TO_CFB is on (off by default
        # since the owner's 2026-10-16 priority "MLB and NHL over CFB"; odds_budget_lanes "A LANE
        # THAT YIELDS"): on a day their sport is still in play, their unspent daily allocation
        # stays out of the shared daily request budget.
        from app.services.odds_budget_lanes import LANE_NHL_RESEARCH

        hold = self._odds_lane_yield(LANE_NHL_RESEARCH)
        self._enforce_odds_quota_policy(
            len(market_list) * max(1, len(self.settings.odds_regions.split(","))),
            request_holdback=hold["daily"],
            holdback_for=", ".join(h["label"] for h in hold["lanes"] if h["today"]),
            cooldown_endpoint_key=nhl_odds.ENDPOINT_KEY,       # this lane's own cooldown only (MEDIUM-2)
        )
        now = datetime.now(UTC)
        ledger = self._reserve_provider_request(
            nhl_odds.PROVIDER, nhl_odds.ENDPOINT_KEY,
            controlled_live_request=False,
            request_shape={
                "sport_key": nhl_odds.SPORT_KEY,
                "regions": self.settings.odds_regions,
                "markets": sorted(market_list),
                "request_window": int(
                    now.timestamp() // (max(1, self.settings.odds_pregame_refresh_minutes) * 60)
                ),
                "due_games": [g["source_game_id"] for g in planned["due_games"]],
            },
        )
        self.session.commit()
        response_received = False
        try:
            response = await TheOddsAPIAdapter(
                self.settings.the_odds_api_key,
                market_list,
                self.settings.odds_regions,
                sport_key=nhl_odds.SPORT_KEY,
            ).get_market_snapshot()
            response_received = True
            archive = self.archive.write(response.envelope.source, response.raw_payload)
            received_at = response.envelope.fetched_at
            events = [OddsEventDTO.model_validate(item) for item in response.envelope.data]
            bound, event_receipts = nhl_odds.bind_events(self.session, events, received_at=received_at)
            written = nhl_odds.persist(
                self.session, bound, requested_at=ledger.requested_at, received_at=received_at,
                response={"archive_path": str(archive.raw_file_reference),
                          "archive_payload_sha256": archive.payload_checksum,
                          "ledger_reservation_id": str(ledger.id)},
            )
            metadata = response.envelope.metadata or {}
            summary = {
                "sport_key": nhl_odds.SPORT_KEY, "events": len(events), "bound": len(bound),
                "created": written["created"], "existing": written["existing"],
                "archive_path": str(archive.raw_file_reference),
                "archive_payload_sha256": archive.payload_checksum,
                # Bookmakers the adapter skipped because they carried no clock.
                "clock_refusals": list(metadata.get("clock_refusals") or []),
            }
            self.session.add(ProviderRequestLedger(
                provider=ledger.provider, endpoint_key=ledger.endpoint_key,
                requested_at=ledger.requested_at, completed_at=datetime.now(UTC),
                status="SUCCEEDED", request_fingerprint=ledger.request_fingerprint,
                quota_cost=_int_or_none(metadata.get("requests_last")),
                quota_remaining=_int_or_none(metadata.get("requests_remaining")),
                records_received=len(events), controlled_live_request=False,
                source_mode="LIVE", job_run_id=self._active_job_run_id,
                reservation_id=ledger.id,
                response_metadata={
                    "request_shape": (ledger.response_metadata or {}).get("request_shape"),
                    "requests_used": _int_or_none(metadata.get("requests_used")),
                    "response_received": True, **summary,
                    "event_receipts": event_receipts, "quote_receipts": written["quote_receipts"],
                },
            ))
            self._ingestion_run(response.envelope, archive.raw_file_reference, written["created"],
                                "collect-nhl-odds-api")
            self.session.commit()
            return {"records": written["created"], "provider_called": True, "quota": metadata,
                    "plan": planned, **summary, "event_receipts": event_receipts,
                    "quote_receipts": written["quote_receipts"]}
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
                    "request_shape": (ledger.response_metadata or {}).get("request_shape"),
                    "sport_key": nhl_odds.SPORT_KEY, "response_received": response_received,
                },
            ))
            self.session.commit()
            raise

    async def collect_nba_odds_api(self, event_date: date) -> dict[str, Any]:
        """NBA moneylines from The Odds API (basketball_nba, h2h) for the NBA paper checkpoint.

        Requests nothing while NBA_ODDS_API_ENABLED (or the NBA production
        consumer) is off, or while no decision-eligible NBA game is in its buy
        window. Otherwise ONE request, reserved in the provider ledger first and
        passed through every existing Odds API guard plus the NBA_RESEARCH credit
        lane (zero unless funded); each event is bound or refused on its own
        (app/nba/odds_api_price.py). SportsGameOdds is never touched. Stored
        quotes are usable by the checkpoint only once the owner sets the NBA
        source amendment's effective clock (NBA_ODDS_API_SOURCE_EFFECTIVE_FROM).
        """

        from app.services.runtime_source_proof import emit
        try:
            from app.nba import odds_api_price as nba_odds

            if not self.settings.nba_odds_api_enabled:
                return {"records": 0, "status": "DISABLED", "provider_called": False}
            if not self.settings.nba_production_consumer_enabled:
                return {"records": 0, "status": "DISABLED", "provider_called": False,
                        "reason": "NBA_PRODUCTION_CONSUMER_DISABLED"}
            from app.nba.retained_quote_admission import admit_retained_quotes
            try:
                recovery = admit_retained_quotes(self.session,self.settings)
                self.session.commit()
            except SQLAlchemyError:
                self.session.rollback()
                raise
            except Exception as error:
                self.session.rollback()
                recovery = {"status":"FAILED:"+type(error).__name__,"created":0,"in_flight":0}
            if recovery.get("in_flight") or not recovery.get("scan_complete",True):
                return {"records":recovery.get("created",0),"status":"RETAINED_RECOVERY_IN_FLIGHT","provider_called":False,"retained_recovery":recovery}
            planned = nba_odds.plan(self.session)
            if not planned["due"]:
                return {"records": recovery.get("created",0), "status": "NOT_DUE", "provider_called": False, "plan": planned, "retained_recovery": recovery}
            if not self.settings.the_odds_api_key:
                raise SourceError("THE_ODDS_API", "THE_ODDS_API_KEY is not configured", retryable=False)
            market_list = list(nba_odds.MARKETS)
            self._enforce_odds_quota_policy(
                len(market_list) * max(1, len(self.settings.odds_regions.split(","))),
                cooldown_endpoint_key=nba_odds.ENDPOINT_KEY,
            )
            now = datetime.now(UTC)
            ledger = self._reserve_provider_request(
                nba_odds.PROVIDER, nba_odds.ENDPOINT_KEY,
                controlled_live_request=False,
                request_shape={
                    "sport_key": nba_odds.SPORT_KEY,
                    "regions": self.settings.odds_regions,
                    "markets": sorted(market_list),
                    "request_window": int(
                        now.timestamp() // (max(1, self.settings.odds_pregame_refresh_minutes) * 60)
                    ),
                    "due_games": [g["espn_game_id"] for g in planned["due_games"]],
                },
            )
            self.session.commit()
            response_received = False
            archive = None
            received_at = None
            try:
                response = await TheOddsAPIAdapter(
                    self.settings.the_odds_api_key,
                    market_list,
                    self.settings.odds_regions,
                    sport_key=nba_odds.SPORT_KEY,
                ).get_market_snapshot()
                response_received = True
                received_at = response.envelope.fetched_at
                archive = self.archive.write(response.envelope.source, response.raw_payload)
                events = [OddsEventDTO.model_validate(item) for item in response.envelope.data]
                bound, event_receipts = nba_odds.bind_events(self.session, events, received_at=received_at)
                written = nba_odds.persist(
                    self.session, bound, requested_at=ledger.requested_at, received_at=received_at,
                    response={"archive_path": str(archive.raw_file_reference),
                              "archive_payload_sha256": archive.payload_checksum,
                              "ledger_reservation_id": str(ledger.id), "archive_file_sha256": archive.file_checksum, "archive_bytes": archive.bytes_written},
                )
                metadata = response.envelope.metadata or {}
                from uuid import UUID
                coverage_ids = {game_id for _event,game_id in bound} | {UUID(game["game_id"]) for game in planned["due_games"]}
                coverage = {str(game_id): nba_odds.quote_coverage(self.session,game_id,moment=datetime.now(UTC)) for game_id in coverage_ids}
                summary = {
                    "sport_key": nba_odds.SPORT_KEY, "events": len(events), "bound": len(bound),
                    "created": written["created"], "existing": written["existing"],
                    "per_event_coverage": coverage, "response_received_at": received_at.isoformat(),
                    "archive_file_sha256": archive.file_checksum, "archive_bytes": archive.bytes_written,
                    "archive_path": str(archive.raw_file_reference),
                    "archive_payload_sha256": archive.payload_checksum,
                    # Bookmakers the adapter skipped because they carried no clock.
                    "clock_refusals": list(metadata.get("clock_refusals") or []),
                }
                self.session.add(ProviderRequestLedger(
                    provider=ledger.provider, endpoint_key=ledger.endpoint_key,
                    requested_at=ledger.requested_at, completed_at=datetime.now(UTC),
                    status="SUCCEEDED", request_fingerprint=ledger.request_fingerprint,
                    quota_cost=_int_or_none(metadata.get("requests_last")),
                    quota_remaining=_int_or_none(metadata.get("requests_remaining")),
                    records_received=len(events), controlled_live_request=False,
                    source_mode="LIVE", job_run_id=self._active_job_run_id,
                    reservation_id=ledger.id,
                    response_metadata={
                        "request_shape": (ledger.response_metadata or {}).get("request_shape"),
                        "requests_used": _int_or_none(metadata.get("requests_used")),
                        "response_received": True, **summary,
                        "event_receipts": event_receipts, "quote_receipts": written["quote_receipts"],
                    },
                ))
                self._ingestion_run(response.envelope, archive.raw_file_reference, written["created"],
                                    "collect-nba-odds-api")
                self.session.commit()
                return {"records": written["created"], "provider_called": True, "quota": metadata,
                        "plan": planned, "retained_recovery": recovery, **summary, "event_receipts": event_receipts,
                        "quote_receipts": written["quote_receipts"]}
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
                        "request_shape": (ledger.response_metadata or {}).get("request_shape"),
                        "sport_key": nba_odds.SPORT_KEY, "response_received": response_received,
                        **({"response_received_at":received_at.isoformat()} if received_at is not None else {}),
                        **({"archive_path":str(archive.raw_file_reference),"archive_payload_sha256":archive.payload_checksum,
                            "archive_file_sha256":archive.file_checksum,"archive_bytes":archive.bytes_written} if archive is not None else {}),
                    },
                ))
                self.session.commit()
                raise
        finally:
            emit(stage="nba_odds_collection_completed")

    async def evaluate_nba_paper_checkpoint(self, event_date: date) -> dict[str, Any]:
        """AISP_NBA_PAPER_POLICY_V1 at T-30..T-15. Records a reason per game; positions stay off.

        Refreshes NBA discovery first (local files only) when the consumer is on.
        Calls no provider. See app/nba/gen1_paper.py for the gates.
        """

        from app.services.runtime_source_proof import emit
        try:
            from app.nba.gen1_paper import evaluate

            return evaluate(self.session, self.settings)
        finally:
            emit(stage="nba_paper_checkpoint_completed")

    async def ingest_nba_finals(self, event_date: date) -> dict[str, Any]:
        """Both NBA final lanes, then per-channel scoring. Local bytes only; no network.

        Lane 1 is the PRODUCTION path: the producer's source-confirmed finals
        exchange (``finals-discovery/current.json``). Lane 2 is the separate
        historical reconstruction from retained ESPN season exports. They are
        reported separately and never summed into one "finals" count.
        """

        from app.services.runtime_source_proof import emit
        try:
            from app.nba.gen1_evaluation import evaluate as evaluate_nba
            from app.nba.gen1_finals import ingest

            finals = ingest(self.session, self.settings)
            self.session.commit()
            scoring = evaluate_nba(self.session)
            self.session.commit()
            production = finals.get("production_lane") or {}
            historical = finals.get("historical_lane") or {}
            return {"records": int(finals.get("observations_written", 0)) + int(scoring.get("written", 0)),
                    "status": finals.get("status"),
                    "production_lane": {k: v for k, v in production.items()
                                        if k not in ("refused_records", "advisories")},
                    "historical_lane": {k: v for k, v in historical.items()
                                        if k not in ("skipped", "source_objects")},
                    "finals_skipped": historical.get("skipped", [])[:20],
                    "reconciliation_required": finals.get("reconciliation_required", [])[:20],
                    "scoring": {k: v for k, v in scoring.items() if k != "skipped"}}
        finally:
            emit(stage="nba_finals_ingest_completed")

    async def collect_nba_pregame_odds(self, event_date: date) -> dict[str, Any]:
        """NBA SportsGameOdds prices. Spends nothing while the lane is off, unauthorised or unresolved."""

        from app.nba.sgo_price import collect

        result = collect(self.session, self.settings)
        self.session.commit()
        return {"records": int(result.get("objects_spent", 0)), **result}

    async def import_nba_historical_evidence(self, event_date: date) -> dict[str, Any]:
        """HISTORICAL 2025-26 rows from pinned Model Studio receipts, their finals and channel scores.

        Manual only (never scheduled). Refuses unless both receipts are pinned.
        Reads local files; no request is made.
        """

        from app.nba.gen1_evaluation import evaluate as evaluate_nba
        from app.nba.gen1_finals import ingest
        from app.nba.gen1_historical import HistoricalRefused, import_historical

        try:
            imported = import_historical(self.session, self.settings)
        except HistoricalRefused as exc:
            self.session.rollback()
            return {"records": 0, "status": f"REFUSED:{exc}"}
        self.session.commit()
        finals = ingest(self.session, self.settings)
        self.session.commit()
        scoring = evaluate_nba(self.session)
        self.session.commit()
        return {"records": int(imported.get("imported", 0)),
                "historical": {k: v for k, v in imported.items() if k not in ("skipped", "source_objects")},
                "historical_skipped": imported.get("skipped", [])[:20],
                "finals": {"status": finals.get("status"),
                           "historical_lane": {k: v for k, v in (finals.get("historical_lane") or {}).items()
                                               if k not in ("skipped", "source_objects")}},
                "scoring": {k: v for k, v in scoring.items() if k != "skipped"}}

    async def recover_governed_finals(self, event_date: date) -> dict[str, Any]:
        """Governed finals for completed games that have none, automatically.

        Runs the existing governed reconstruction under the existing recorded
        owner decision, narrowed to the games actually missing a final so a
        slate is never re-ingested. Once these persist, the settlement owner
        picks them up on its own next pass.
        """

        from app.services.governed_final_recovery import recover_missing_finals

        return await recover_missing_finals(self.session, self.settings)

    async def refresh_provider_usage(self, event_date: date) -> dict[str, Any]:
        """Read the provider's own balance so the allocation plans on a fact."""

        from app.services.sgo_usage import refresh_usage

        return refresh_usage(self.session, self.settings)

    async def backfill_scoreboard(self, event_date: date) -> dict[str, Any]:
        """Scoreboard state for games that finished before the collector existed.

        Reads the raw archive AISP already wrote -- every schedule response ever
        fetched, linescore included -- so a completed game's final and innings
        cost a disk read rather than a request. Retained evidence first is both
        the cheaper order and the right one.
        """

        from app.scoreboard.backfill import backfill_mlb_scoreboard

        return backfill_mlb_scoreboard(
            self.session, raw_root=self.settings.raw_data_root)

    async def bridge_mlb_canonical_events(self, event_date: date) -> dict[str, Any]:
        """Register canonical MLB events for today and the next two days.

        Exists because AISP only ever created events for the CURRENT date, so
        a publisher working a day ahead found an empty table a day ahead --
        every game on the next slate came back
        CANONICAL_EVENT_NOT_REGISTERED_IN_LOCAL_IDENTITY_SOURCE. Nothing about
        identity resolution is reimplemented here: this runs the governed
        normalizer, which keys on the league's own gamePk and refuses an
        ambiguous doubleheader, over a forward horizon instead of one day.

        Free public schedule endpoint, one request per date, no key and no
        metered allowance. It creates no forecast, decision or position.
        """

        from app.services.mlb_event_bridge import MlbEventBridge

        outcome = await MlbEventBridge(self.session, self.settings).run(start=event_date)
        self.session.flush()
        return {
            "records": outcome["events_registered"],
            **outcome,
            "event_date": event_date.isoformat(),
        }

    async def record_shadow_threshold_ladder(self, event_date: date) -> dict[str, Any]:
        """Record what six inert minimums WOULD have selected today.

        Research only. Every rung reads decisions that already existed, priced
        by quotes already persisted; none creates a position, none touches the
        funded experiment, and the official minimum is unchanged. The 2.00%
        rung deliberately reproduces the official policy so the ladder can be
        checked against a known answer instead of trusted.
        """

        from app.services.shadow_threshold_ladder import ShadowThresholdLadder

        ladder = ShadowThresholdLadder(self.session, self.settings)
        outcome = ladder.run(event_date=event_date)
        self.session.flush()
        return {
            "records": sum(rung["recorded"] for rung in outcome["rungs"]),
            **outcome,
        }

    async def refresh_mlb_data(self, event_date: date) -> dict[str, Any]:
        live_game_exists = self.session.scalar(
            select(Game.id).where(
                Game.starts_at >= self._day_bounds(event_date)[0],
                Game.starts_at < self._day_bounds(event_date)[1],
                Game.source_mode == "LIVE",
                in_league(MLB),
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
                in_league(MLB),
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
        # One structured receipt per source family. Team stats, the active
        # roster and bullpen workload are independent evidence with independent
        # failures: a ConnectTimeout on team stats used to end the whole team's
        # try block, so the roster and bullpen were never even attempted -- the
        # 2026-09-14 Chicago Cubs warning. Each family now gets exactly one
        # attempt per team, its own failure record, no retry, and no stale
        # substitute; the governed 30-hour freshness gate is untouched.
        families: dict[str, dict[str, Any]] = {
            name: {"attempted": 0, "completed": 0, "failed": 0, "failures": []}
            for name in ("TEAM_STATS", "ACTIVE_ROSTER", "BULLPEN_WORKLOAD")
        }

        def _attempt(family: str) -> None:
            families[family]["attempted"] += 1

        def _completed(family: str) -> None:
            families[family]["completed"] += 1

        def _failed(family: str, team_name: str, exc: Exception) -> None:
            families[family]["failed"] += 1
            families[family]["failures"].append(
                {"team": team_name, "error_type": type(exc).__name__, "error": str(exc)})

        for team, source, external_id in team_rows:
            if team.id in seen:
                continue
            seen.add(team.id)
            _attempt("TEAM_STATS")
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
                _completed("TEAM_STATS")
            except SourceError as exc:
                warnings.append(f"{team.name} team stats: {exc}")
                _failed("TEAM_STATS", team.name, exc)
            if source != self.demo.source and self.settings.app_env != "test":
                _attempt("ACTIVE_ROSTER")
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
                    _completed("ACTIVE_ROSTER")
                except SourceError as exc:
                    warnings.append(f"{team.name} roster: {exc}")
                    _failed("ACTIVE_ROSTER", team.name, exc)

                _attempt("BULLPEN_WORKLOAD")
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
                            in_league(MLB),
                        )
                        .order_by(Game.starts_at)
                    ))
                    for game in target_games:
                        bullpen_processed += self.normalizer.ingest_bullpen_availability(
                            game.id,
                            bullpen_rows,
                            bullpen_response.envelope.source,
                        )
                    _completed("BULLPEN_WORKLOAD")
                except SourceError as exc:
                    warnings.append(f"{team.name} bullpen workload: {exc}")
                    _failed("BULLPEN_WORKLOAD", team.name, exc)
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
                    in_league(MLB),
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
            metadata={"team_count": len(seen), "warnings": warnings,
                      "source_families": families, "automatic_retries_added": 0,
                      "stale_evidence_substituted": False},
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
            "source_families": families,
            "automatic_retries_added": 0,
            "stale_evidence_substituted": False,
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
                in_league(MLB),
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
        markets: list[str] | None = None,
    ) -> dict[str, Any]:
        # The market set is what the request costs: The Odds API charges one
        # credit per market per region. A caller that knows only MONEYLINE can
        # be priced by the governing policy asks for h2h alone and pays 1
        # instead of 3. Omitted, this keeps the configured behaviour exactly.
        market_list = list(markets) if markets else list(self.settings.odds_market_list)
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
                self._enforce_odds_quota_policy(
                    len(market_list) * max(1, len(self.settings.odds_regions.split(","))),
                )
                ledger = self._reserve_provider_request(
                    "THE_ODDS_API",
                    "baseball_mlb_odds",
                    controlled_live_request=controlled_live_request,
                    request_shape={
                        "regions": self.settings.odds_regions,
                        "markets": sorted(market_list),
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
                    market_list,
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
                # This refresh is the MLB odds lane. The provider's own sport
                # key must say so; a response without one binds nothing.
                league_code=MLB,
                provider_league=("baseball_mlb" if demo_only else provider_metadata.get("sport_key")),
                source_mode=envelope.source_mode.upper(),
                event_date=event_date,
            )
            unbound_odds_events = list(self.normalizer.odds_receipts)
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
                        "request_shape": (ledger.response_metadata or {}).get("request_shape"),
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
                # Events not bound to an MLB game, each with its reason.
                "unbound_events": unbound_odds_events,
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
                        "request_shape": (ledger.response_metadata or {}).get("request_shape"),
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
                in_league(MLB),
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
        games = self._games_for_date(event_date, MLB)
        return {"records": len(games), "status": "validated", "games": len(games)}

    async def generate_forecasts(self, event_date: date, *, data_as_of: datetime | None = None) -> dict[str, Any]:
        service = ForecastService(self.session, self.settings)
        created = 0
        skipped: list[dict[str, Any]] = []
        for game in self._games_for_date(event_date, MLB):
            try:
                service.generate_for_game(game, data_as_of=data_as_of)
                created += 1
            except ForecastNotReadyError as exc:
                skipped.append({"game_id": str(game.id), "reason": str(exc), "score": exc.score})
            except ForecastWindowClosedError as exc:
                skipped.append({"game_id": str(game.id), "reason": str(exc), "score": None})
            except GameStateUnavailableError as exc:
                # No point-in-time game state yet. One game without evidence must
                # not abort the pipeline before positions and the shadow freeze.
                skipped.append({"game_id": str(game.id), "reason": str(exc), "score": None})
            except LeagueMismatchError as exc:
                skipped.append({**exc.receipt(), "score": None})
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
        positions = publish_official_positions(self.session, self.settings, self._games_for_date(event_date, MLB))
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
        elif demo_only and self.settings.demo_mode and all(game.source_mode == "DEMO" for game in self._games_for_date(event_date, MLB)):
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
        # Parlay tickets settle from the same governed finals this step just
        # refreshed, on the existing schedule rather than a new one.
        steps["parlay_tickets"] = await self.settle_parlay_tickets(event_date)
        # A House Divided: settle virtual competition wagers and copy newly
        # final official decisions. Reads the official ledger, never writes it.
        steps["house_divided"] = await self.run_house_divided(event_date)
        steps["performance"] = await self.recalculate_performance(event_date)
        return {"records": sum(int(value.get("records", 0)) for value in steps.values()), "steps": steps}

    async def run_house_divided(self, event_date: date) -> dict[str, Any]:
        """The virtual competition's step, isolated so it can never fail finalize.

        Runs in a savepoint after every official settlement step: a fault here
        rolls back only the competition's own writes and is reported, while the
        official positions, experiment and parlay settlement already committed.
        """

        from app.services import house_divided

        try:
            with self.session.begin_nested():
                report = house_divided.run(self.session)
            self.session.commit()
            return report
        except Exception as exc:  # noqa: BLE001 -- reported, never raised into finalize
            self.session.rollback()
            return {"records": 0, "error": f"{type(exc).__name__}: {exc}"[:400]}

    async def settle_parlay_tickets(self, event_date: date) -> dict[str, Any]:
        """Settle every open ticket whose games all have a governed final.

        Reads the same ``game_final_outcomes`` record the rest of AISP scores
        against, settles each ticket at most once, and leaves a ticket pending
        whenever any leg still lacks evidence. Touches no provider and creates
        no schedule of its own -- it rides the existing finalize pipeline.
        """

        from app.parlay.tickets import ParlayTicketService

        report = ParlayTicketService(self.session).settle_open_tickets()
        self.session.commit()
        return {"records": report["settled"], **report}

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
            in_league(MLB),
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

    def _games_for_date(self, event_date: date, league_code: str) -> list[Game]:
        """One league's pipeline slate: its LIVE games, else its DEMO fixtures.

        The league is required. Another league's game never enters, whatever
        its source mode, and a date with none of this league's games is empty.
        """
        games = list(self.session.scalars(select(Game).where(
            Game.starts_at >= self._day_bounds(event_date)[0], Game.starts_at < self._day_bounds(event_date)[1],
            in_league(league_code),
        ).order_by(Game.starts_at)))
        live = [game for game in games if game.source_mode == "LIVE"]
        return live or [game for game in games if game.source_mode == "DEMO"]

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

    def _enforce_odds_quota_policy(self, credit_cost: int = 1, *, request_holdback: int = 0,
                                   holdback_for: str = "", cooldown_endpoint_key: str | None = None) -> None:
        # The "daily" budget window is the operational day in the configured
        # display timezone — the same day that defines the slate it serves. A
        # UTC window resets at 20:00 ET and lets the prior evening's spend
        # exhaust the next slate's budget by mid-afternoon.
        # ``request_holdback``: requests of that shared budget a YIELDING lane (NHL)
        # must leave for the lanes it yields to (``_odds_lane_yield``); 0 for every
        # other caller, whose check is exactly what it always was.
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
        if request_holdback > 0 and requests_today + request_holdback >= self.settings.odds_daily_request_budget:
            raise SourceError(
                "THE_ODDS_API",
                f"Daily request budget: {requests_today}/{self.settings.odds_daily_request_budget} used on "
                f"operational day {budget_day.isoformat()} and {request_holdback} held for {holdback_for} "
                "(in play today); this lane yields to them",
                retryable=False,
            )
        # Second, independent guard: AISP's own credit accounting for the UTC
        # billing month, with a reserve that is never spent. The provider floor
        # below can only speak after a request has been made; this speaks before.
        from app.services import odds_budget

        state = odds_budget.budget_state(
            self.session,
            monthly_cap=self.settings.odds_monthly_credit_cap,
            reserve=self.settings.odds_monthly_credit_reserve,
            daily_cap=self.settings.odds_daily_credit_budget,
            floor=self.settings.odds_quota_floor,
            display_timezone=self.settings.display_timezone,
        )
        allowed, refusal = state.may_spend(credit_cost)
        if not allowed:
            raise SourceError("THE_ODDS_API", refusal or "Credit budget exhausted", retryable=False)
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
            # ...and then the floor still refuses THIS request. Reserving the
            # probe used to fall through, which let a caller that had just been
            # told the balance was too low to read spend its full three credits
            # anyway. That happened on 2026-09-08 (8 remaining, then 5) and
            # again on 2026-09-09. The probe exists to refresh the reading, not
            # to license the request whose refusal justified it: the next run
            # sees a fresh number and either proceeds or stays latched.
            raise SourceError(
                "THE_ODDS_API",
                f"Odds quota floor reached ({remaining} remaining, reading "
                f"{round(state.reading_age_hours or 0.0, 1)}h old). A bounded recovery "
                f"probe has been reserved to refresh the balance; this request is refused.",
                retryable=False,
            )
        cooldown_completed_at = latest.completed_at
        if cooldown_endpoint_key is not None:
            # PER-ENDPOINT COOLDOWN (hotfix 2026-10-24, review MEDIUM-2). The
            # cooldown is double-refresh protection for ONE lane, never a money
            # guard: the budget, credit, floor and latch checks above stay
            # global. Scoped lanes (NBA, the NFL fallback, and now CFB and NHL)
            # read only their own endpoint's newest success, so one sport's buy
            # cannot refuse another sport's first tick (NE@CHI 10-22: the TNF
            # fallback was refused twice by a cooldown another lane had set;
            # 15 collector ticks failed the same way since 10-20).
            cooldown_completed_at = self.session.scalar(
                select(ProviderRequestLedger.completed_at).where(
                    ProviderRequestLedger.provider == "THE_ODDS_API",
                    ProviderRequestLedger.endpoint_key == cooldown_endpoint_key,
                    ProviderRequestLedger.status == "SUCCEEDED",
                    ProviderRequestLedger.completed_at.is_not(None),
                ).order_by(ProviderRequestLedger.completed_at.desc()).limit(1)
            )
        if cooldown_completed_at:
            elapsed = datetime.now(UTC) - _aware(cooldown_completed_at)
            cooldown = timedelta(minutes=self.settings.odds_manual_cooldown_minutes)
            if elapsed < cooldown:
                seconds = int((cooldown - elapsed).total_seconds())
                raise SourceError("THE_ODDS_API", f"Odds refresh cooldown active for {seconds} more seconds", retryable=False)

    def _odds_lane_budget(self, *, yielding_lane: str | None = None):
        """Every Odds API credit lane, as the guard reads them (``odds_budget_lanes.lane_budget``).

        Only the CFB and NHL lanes' allocations come from settings (zero unless funded);
        every other lane keeps its compiled allocation. ``in_play_today`` is computed only
        when ``yielding_lane`` yields to another lane (NHL), the only arithmetic that reads it.
        """

        from app.services.odds_budget_lanes import (
            DEFAULT_LANES, LANES, lane_budget, lanes_in_play_today, lanes_with_cfb, lanes_with_nhl, lanes_with_nba,
        )

        spec = LANES.get(yielding_lane or "")
        in_play = (lanes_in_play_today(self.session, display_timezone=self.settings.display_timezone)
                   if spec is not None and spec.yields_to else None)
        return lane_budget(
            self.session,
            monthly_cap=self.settings.odds_monthly_credit_cap,
            reserve=self.settings.odds_monthly_credit_reserve,
            daily_cap=self.settings.odds_daily_credit_budget,
            floor=self.settings.odds_quota_floor,
            display_timezone=self.settings.display_timezone,
            # ODDS_NHL_YIELDS_TO_CFB (default False since the owner's 2026-10-16 priority "MLB
            # and NHL over CFB"): NHL leaves NFL its share; whether it also leaves CFB's is this switch.
            lanes=lanes_with_nba(self.settings.odds_nba_lane_monthly_credits, self.settings.odds_nba_lane_daily_credits, lanes=lanes_with_nhl(self.settings.odds_nhl_lane_monthly_credits,
                                 self.settings.odds_nhl_lane_daily_credits,
                                 lanes_with_cfb(self.settings.odds_cfb_lane_monthly_credits,
                                                self.settings.odds_cfb_lane_daily_credits, DEFAULT_LANES),
                                 yields_to_cfb=bool(getattr(self.settings, "odds_nhl_yields_to_cfb", False)))),
            in_play_today=in_play,
        )

    def _odds_lane_yield(self, lane: str) -> dict[str, Any]:
        """What a yielding lane must leave today: ``LaneBudget.yield_holdback`` (zero for other lanes)."""

        return self._odds_lane_budget(yielding_lane=lane).yield_holdback(lane)

    def _enforce_lane_budget(self, endpoint_key: str, request_shape: dict[str, Any]) -> None:
        """Refuse a request whose lane cannot pay for it.

        A lane refusal is not a failure to be retried -- it is the budget
        working -- so it is raised non-retryable with the lane named, and the
        protected MLB lanes keep their allocation whatever else is asking.
        """
        from app.services.odds_budget_lanes import lane_for_request, league_of_endpoint

        markets = list(request_shape.get("markets") or [])
        regions = request_shape.get("regions") or []
        # NCAAF before NFL: both sport keys contain "americanfootball".
        league = league_of_endpoint(endpoint_key)
        # NHL never bypasses its lane: even a shape that declares no markets is
        # charged to NHL_RESEARCH (zero unless funded), never waved through.
        if not league or (not markets and league not in ("NHL", "NBA")):
            return  # nothing declared to price; the monthly guard still applies
        cost = max(1, len(markets) * max(1, len(regions) if isinstance(regions, list) else 1))
        lane = lane_for_request(
            league_code=league, markets=markets,
            recovery=bool(request_shape.get("recovery")),
        )
        budget = self._odds_lane_budget(yielding_lane=lane)
        allowed, refusal = budget.may_spend(lane, cost)
        if not allowed:
            raise SourceError(
                "THE_ODDS_API",
                f"{lane} lane cannot fund this {cost}-credit request: "
                f"{refusal or 'lane allocation exhausted'}",
                retryable=False,
            )

    def _reserve_provider_request(
        self,
        provider: str,
        endpoint_key: str,
        *,
        controlled_live_request: bool,
        request_shape: dict[str, Any],
    ) -> ProviderRequestLedger:
        # THE ONE PLACE A CREDIT CAN BE COMMITTED.
        # Every call to the odds provider in this codebase is immediately
        # preceded by a reservation written here, so this is where a budget can
        # be made to mean something. Enforcing in each caller instead would
        # leave whichever caller was forgotten free to spend the reserve --
        # which is exactly what happened in September, when a ten-minute
        # three-market poller that consulted no budget at all consumed 495 of
        # the month's 500 credits in four days and left five slate-days with no
        # prices for any decision.
        from app.services import odds_budget

        if provider == "THE_ODDS_API" and endpoint_key not in odds_budget.ZERO_COST_ENDPOINTS:
            self._enforce_lane_budget(endpoint_key, request_shape)
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


#: The Model Studio paths that determine what the NFL M1 producer computes:
#: the frozen CLI entry point, and the package it imports. Everything the
#: producer needs lives under these two paths -- ``server/sports_nfl_m1``
#: imports only the standard library and third-party packages -- so a change
#: anywhere else in the shared monorepo cannot alter its output.
#: Bumped whenever the scope below changes, and recorded on every producer
#: receipt. A widening or narrowing of what "producer code identity is frozen"
#: covers is a change to the integrity claim itself, so it is versioned rather
#: than edited silently -- a receipt naming V1 and one naming V2 assert
#: different things, and a reader has to be able to tell which.
NFL_PRODUCER_CODE_SCOPE_VERSION = "AISP_NFL_PRODUCER_CODE_SCOPE_V2"

#: V2 adds the current-season publication lane. V1 covered only the prospective
#: shadow CLI and the estimator package, which was correct for what ran then;
#: the rolling publisher and the Foundation source sync have since become part
#: of what produces an imported forecast, and their code was unguarded.
#:
#: These are the paths the producer actually imports, traced from the entry
#: points rather than guessed:
#:
#:   run_nfl_2026_prospective_shadow.py -> server/sports_nfl_m1
#:   publish_nfl_rolling_shadow.py      -> server/sports_nfl_m1
#:   sync_nfl_current_season.py         -> server/sports_nfl,
#:                                         server/sports_cockpit/interprocess_lock
#:
#: Still deliberately NOT the whole repository. Model Studio is a shared
#: monorepo whose MLB work continuously rewrites tracked output artifacts, and
#: scoping to the repo is what silently stopped NFL forecasting from
#: 2026-09-11: every run failed on "producer code identity is not frozen" while
#: every NFL producer file was untouched. A data artifact belonging to another
#: sport cannot change what the NFL estimator computes.
NFL_PRODUCER_CODE_PATHS: tuple[str, ...] = (
    "scripts/run_nfl_2026_prospective_shadow.py",
    "scripts/publish_nfl_rolling_shadow.py",
    "scripts/sync_nfl_current_season.py",
    "server/sports_nfl_m1",
    "server/sports_nfl",
    "server/sports_cockpit/interprocess_lock.py",
)


async def _git_porcelain(root: Path, paths: tuple[str, ...]) -> list[str]:
    """Tracked, non-pristine paths under ``paths`` (whole repo when empty).

    Untracked files are excluded: an unversioned scratch file is not a change
    to committed producer code.
    """

    command = ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=no"]
    if paths:
        command.append("--")
        command.extend(paths)
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise ValueError("Timed out verifying the configured Model Studio worktree") from None
    if process.returncode != 0:
        raise ValueError(
            "Cannot verify configured Model Studio worktree: "
            + stderr.decode("utf-8", errors="replace")[-500:]
        )
    return [
        line[3:].strip()
        for line in stdout.decode("utf-8", errors="replace").splitlines()
        if line.strip()
    ]


#: The repaired Foundation sync, exactly as the producer documented it. Frozen
#: as an argument vector rather than a shell string: there is no shell here, so
#: nothing in a settings value can become a command.
NFL_FOUNDATION_SYNC_ARGS: tuple[str, ...] = (
    "-B", "-m", "scripts.sync_nfl_current_season", "--through", "latest-completed",
)
NFL_FOUNDATION_SYNC_VERSION = "AISP_NFL_FOUNDATION_SYNC_V1"
#: Generous, because the sync downloads season assets. Bounded, because a hung
#: child must not hold the orchestrator open indefinitely.
NFL_FOUNDATION_SYNC_TIMEOUT_SECONDS = 900


def _nfl_foundation_freshness(stdout: str) -> dict[str, Any]:
    """Source freshness, taken from the sync's OWN report.

    The sync prints an ``ASP_NFL_CURRENT_SEASON_SYNC_REPORT_V1`` JSON document
    naming the dataset version it advanced to, which source families changed,
    and whether the warehouse actually moved. That is far better evidence than
    a filesystem mtime: it is the producer's own declaration about what it did,
    and it distinguishes the two outcomes a caller most needs told apart --
    "exit 0, nothing had changed" and "exit 0, new assets landed".

    An earlier draft walked a guessed directory tree instead and reported
    nothing, because the staging root lives outside the Studio checkout
    entirely. Guessing a path to describe someone else's work was the wrong
    shape; reading what they published is the right one.
    """

    opening = stdout.find("{")
    if opening < 0:
        return {
            "basis": "NO_SYNC_REPORT_ON_STDOUT",
            "detail": "The sync produced no JSON report, so freshness is unknown.",
        }
    try:
        report = json.loads(stdout[opening:])
    except ValueError:
        return {
            "basis": "SYNC_REPORT_UNPARSEABLE",
            "detail": "The sync printed something that is not its declared report.",
        }
    if not isinstance(report, dict):
        return {"basis": "SYNC_REPORT_UNPARSEABLE", "detail": "Report is not an object."}

    authority = report.get("resultAuthority") or {}
    probe = report.get("sourceProbe") or {}
    changed = report.get("changedFamilies") or []
    return {
        "basis": "SYNC_SELF_REPORT",
        "contract": report.get("contract"),
        "report_status": report.get("status"),
        "attempted_at_utc": report.get("attemptedAtUtc"),
        "current_season": report.get("currentSeason"),
        "requested_through": report.get("requestedThrough"),
        # The two facts a reader actually needs, kept apart on purpose.
        "source_changed": report.get("sourceChanged"),
        "warehouse_advanced": report.get("warehouseAdvanced"),
        "changed_families": list(changed),
        "changed_family_count": len(changed),
        "dataset_version_id": authority.get("datasetVersionId"),
        "foundation_manifest_sha256": authority.get("foundationManifestSha256"),
        "source_snapshot_identity": authority.get("sourceSnapshotIdentity"),
        "current_season_completed_games": authority.get("currentSeasonCompletedGames"),
        "current_season_scheduled_games": authority.get("currentSeasonScheduledGames"),
        "asset_count": probe.get("assetCount"),
        "family_count": probe.get("familyCount"),
        "historical_authority_preserved": report.get("historicalAuthorityPreserved"),
        # Recorded because the sync is allowed to fit nothing, and a reader
        # should be able to confirm that it did not.
        "models_fitted": report.get("modelsFitted"),
        "models_promoted": report.get("modelsPromoted"),
    }


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
    # The worktree check is scoped to the paths that actually determine what
    # this producer computes: its CLI and the one package that CLI imports.
    #
    # It used to check the whole repository. Model Studio is a shared monorepo
    # where MLB work continuously rewrites tracked *output* artifacts
    # (slate JSON, readiness state, monitoring snapshots), so ordinary MLB
    # activity by the Studio owner silently stopped NFL forecasting: every run
    # from 2026-09-11 onward failed with "producer code identity is not
    # frozen" while every NFL producer file was untouched. A data artifact
    # belonging to another sport cannot change what the NFL estimator computes,
    # and treating it as if it could is what made the guard misfire.
    #
    # The integrity property is unchanged and still enforced: the producer runs
    # only when its own code is at the recorded HEAD with no local
    # modification, and the producer independently reports the content hashes
    # of its model binary, manifest and feature contract, which are verified
    # against the frozen contract after it returns.
    dirty_scoped = await _git_porcelain(root, NFL_PRODUCER_CODE_PATHS)
    if dirty_scoped:
        raise ValueError(
            "Configured Model Studio has uncommitted changes to NFL producer code; "
            "producer code identity is not frozen: " + ", ".join(dirty_scoped[:10])
        )
    # Recorded, not enforced: the operator can see the repository was not
    # pristine and exactly how many unrelated paths differed.
    dirty_repository = await _git_porcelain(root, ())

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
    # Recorded so a reader of the receipt can see the Studio worktree was not
    # pristine, and that the producer's own code nevertheless was.
    payload["producerCodePathsVerified"] = list(NFL_PRODUCER_CODE_PATHS)
    payload["producerCodeScopeVersion"] = NFL_PRODUCER_CODE_SCOPE_VERSION
    payload["studioUnrelatedDirtyPathCount"] = len(dirty_repository)
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
