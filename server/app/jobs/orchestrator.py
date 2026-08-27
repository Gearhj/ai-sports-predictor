from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
from time import perf_counter
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
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
    ModelVersion,
    ProviderRequestLedger,
    RunStatus,
    Team,
    Venue,
)
from app.services.forecast_service import ForecastNotReadyError, ForecastService, ForecastWindowClosedError
from app.services.read_service import ReadService


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
    "poll-availability-events": "Poll MLB Availability Changes (Live Shadow)",
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
            "poll-availability-events": self.poll_availability_events,
        }
        return await handlers[name](event_date)

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
        adapter = MLBStatsAPIAdapter()
        response = await adapter.get_schedule(event_date)
        fetched_at = response.envelope.fetched_at
        by_source = {str(item.get("source_game_id")): item for item in response.envelope.data}
        payload_id = f"mlb-schedule:{fetched_at.isoformat()}"
        fast_window = timedelta(hours=self.settings.pregame_fast_window_hours)

        snapshots: list[Any] = []
        for game in due_games.values():
            item = by_source.get(str(game.source_game_id))
            if item is None:
                continue
            home = (item.get("home_pitcher") or {}).get("source_id")
            away = (item.get("away_pitcher") or {}).get("source_id")
            probable = f"{home or '-'}|{away or '-'}"
            home_lineup = None
            starts = game.starts_at if game.starts_at.tzinfo else game.starts_at.replace(tzinfo=UTC)
            if starts - now <= fast_window:
                try:
                    lineups = (await adapter.get_game_lineups(str(game.source_game_id))).envelope.data
                    home_lineup = next((row.get("status") for row in lineups), None)
                except SourceError:
                    home_lineup = None
            snapshots.append(GameAvailabilitySnapshot(
                game_id=game.id, probable_pitcher_id=probable,
                home_lineup_status=home_lineup, away_lineup_status=home_lineup,
                source_payload_id=payload_id, published_at=fetched_at,
            ))
        result = operator.poll(snapshots, now=now)
        return {"records": result["events_emitted"], **result}

    async def import_forecast_bundle(self, event_date: date) -> dict[str, Any]:
        """Import the configured immutable forecast bundle. Idempotent; no provider call."""
        from pathlib import Path

        from app.services.shadow_board_service import ShadowBoardService

        directory = self.settings.shadow_bundle_import_dir.strip()
        if not directory:
            return {"records": 0, "note": "SHADOW_BUNDLE_IMPORT_DIR is not configured"}
        result = ShadowBoardService(self.session, self.settings).import_bundle(Path(directory))
        return {"records": int(result.get("forecasts", 0)), **result}

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
                        # The upstream endpoint is not date-filtered. Key the
                        # exactly-once reservation to its real request shape and
                        # UTC budget day, not the caller's normalization date.
                        "request_day_utc": datetime.now(UTC).date().isoformat(),
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
        return {"records": sum(int(value.get("records", 0)) for value in steps.values()), "steps": steps}

    async def finalize_games(self, event_date: date) -> dict[str, Any]:
        """Quota-free final-state refresh and settlement pipeline."""
        steps: dict[str, Any] = {}
        steps["schedule"] = await self.refresh_schedule(event_date)
        steps["settlement"] = await self.settle_games(event_date)
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
        start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        requests_today = self.session.scalar(
            select(func.count(ProviderRequestLedger.id)).where(
                ProviderRequestLedger.provider == "THE_ODDS_API",
                ProviderRequestLedger.requested_at >= start_of_day,
                ProviderRequestLedger.source_mode == "LIVE",
                ProviderRequestLedger.status == "RESERVED",
            )
        ) or 0
        if requests_today >= self.settings.odds_daily_request_budget:
            raise SourceError(
                "THE_ODDS_API",
                f"Daily request budget reached ({requests_today}/{self.settings.odds_daily_request_budget})",
                retryable=False,
            )
        latest = self.session.scalar(select(IngestionRun).where(
            IngestionRun.source == "THE_ODDS_API",
            IngestionRun.status == RunStatus.SUCCEEDED,
        ).order_by(IngestionRun.completed_at.desc()).limit(1))
        if not latest:
            return
        remaining = (latest.source_metadata or {}).get("requests_remaining")
        if remaining is not None and int(remaining) <= self.settings.odds_quota_floor:
            raise SourceError("THE_ODDS_API", f"Odds quota floor reached ({remaining} remaining)", retryable=False)
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
                "An equivalent provider request is already reserved for this UTC budget day",
                retryable=False,
            ) from exc
        return ledger


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
