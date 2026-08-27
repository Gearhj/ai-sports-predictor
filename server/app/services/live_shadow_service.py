"""Armed live prospective shadow operator.

Coordinates bounded MLB availability polling, cadence, change detection, the
event-triggered rescore (delegated to ProspectiveShadowService), operational
status, and the speed-hypothesis metrics. It never fabricates data, never makes
a provider call from a public read path, and never enables real-money action.

Provider I/O (the actual MLB schedule/lineup fetch and Odds API refresh) is
performed by the job layer and injected here as snapshots / callables, so this
service is fully testable without a network call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    AvailabilityEvent,
    Game,
    GameStatus,
    JobRun,
    LiveShadowPollState,
    ProviderRequestLedger,
    RunStatus,
    ShadowRescore,
)
from app.prospective.contracts import MLB_STATS_API_SOURCE, AvailabilityEventInput
from app.prospective.event_sources import MLBDerivedAvailabilityEventSource
from app.services.prospective_shadow_service import ProspectiveShadowService

ODDS_CONFIG_VARIABLE = "THE_ODDS_API_KEY"


@dataclass(frozen=True)
class GameAvailabilitySnapshot:
    """One game's freshly observed availability signals from the MLB provider."""

    game_id: object
    probable_pitcher_id: str | None
    home_lineup_status: str | None
    away_lineup_status: str | None
    source_payload_id: str
    published_at: datetime | None = None


class LiveShadowOperator:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.prospective = ProspectiveShadowService(session, settings)

    # -- cadence ---------------------------------------------------------
    def _cadence_seconds(self, game: Game, now: datetime) -> int:
        window = timedelta(hours=self.settings.pregame_fast_window_hours)
        if _aware(game.starts_at) - now <= window:
            return self.settings.availability_poll_fast_minutes * 60
        return self.settings.availability_poll_slow_minutes * 60

    def monitored_games(self, now: datetime) -> list[Game]:
        """Current/future LIVE games still before first pitch (polling stops at start)."""
        return list(self.session.scalars(
            select(Game).where(
                Game.source_mode == "LIVE",
                Game.status.in_((GameStatus.SCHEDULED, GameStatus.LIVE)),
                Game.starts_at > now,
            ).order_by(Game.starts_at)
        ))

    def due_game_ids(self, now: datetime) -> set:
        due: set = set()
        states = {s.game_id: s for s in self.session.scalars(select(LiveShadowPollState))}
        for game in self.monitored_games(now):
            state = states.get(game.id)
            if state is None or state.last_polled_at is None:
                due.add(game.id)
                continue
            if (now - _aware(state.last_polled_at)).total_seconds() >= self._cadence_seconds(game, now):
                due.add(game.id)
        return due

    # -- polling ---------------------------------------------------------
    def poll(
        self,
        snapshots: list[GameAvailabilitySnapshot],
        *,
        now: datetime | None = None,
        odds_refresher: Callable | None = None,
        rescorer=None,
    ) -> dict:
        """Diff fresh availability against last-seen state and process real changes.

        A game's first observation only establishes the baseline (no event); only
        an actual change against a known prior value becomes an event. Idempotent:
        re-polling identical snapshots produces no new events.
        """
        now = now or datetime.now(UTC)
        due = self.due_game_ids(now)
        events_emitted = 0
        rescores = 0
        for snapshot in snapshots:
            if snapshot.game_id not in due:
                continue
            state = self._poll_state(snapshot.game_id)
            inputs = self._diff(state, snapshot) if state.poll_count > 0 else []
            self._update_state(state, snapshot, now)
            for event_input in inputs:
                result = self.prospective.process_event(
                    event_input, source=MLB_STATS_API_SOURCE,
                    rescorer=rescorer, odds_refresher=odds_refresher, now=now,
                )
                events_emitted += 1
                rescores += result.get("games_rescored", 0)
        self.session.commit()
        return {
            "polled_games": len([s for s in snapshots if s.game_id in due]),
            "due_games": len(due),
            "events_emitted": events_emitted,
            "rescores": rescores,
            "now": now.isoformat(),
        }

    def _diff(self, state: LiveShadowPollState, snapshot: GameAvailabilitySnapshot) -> list[AvailabilityEventInput]:
        events: list[AvailabilityEventInput] = []
        events.extend(MLBDerivedAvailabilityEventSource.diff_probable_pitchers(
            {str(snapshot.game_id): snapshot.probable_pitcher_id},
            {str(snapshot.game_id): state.last_probable_pitcher},
            published_at=snapshot.published_at,
        ))
        if snapshot.home_lineup_status is not None:
            events.extend(MLBDerivedAvailabilityEventSource.diff_lineups(
                str(snapshot.game_id), snapshot.home_lineup_status, state.last_home_lineup_status,
                published_at=snapshot.published_at,
            ))
        for event_input in events:
            event_input.payload["source_payload_id"] = snapshot.source_payload_id
            event_input.payload["provider"] = MLB_STATS_API_SOURCE
        return events

    def _poll_state(self, game_id) -> LiveShadowPollState:
        state = self.session.scalar(select(LiveShadowPollState).where(LiveShadowPollState.game_id == game_id))
        if state is None:
            state = LiveShadowPollState(game_id=game_id, poll_count=0)
            self.session.add(state)
            self.session.flush()
        return state

    def _update_state(self, state: LiveShadowPollState, snapshot: GameAvailabilitySnapshot, now: datetime) -> None:
        state.last_polled_at = now
        state.poll_count = int(state.poll_count) + 1
        state.last_probable_pitcher = snapshot.probable_pitcher_id
        state.last_home_lineup_status = snapshot.home_lineup_status
        state.last_away_lineup_status = snapshot.away_lineup_status
        state.last_source_payload_id = snapshot.source_payload_id
        self.session.add(state)

    # -- speed hypothesis ------------------------------------------------
    def speed_metrics(self, rescore: ShadowRescore, event: AvailabilityEvent | None) -> dict:
        from app.services.prospective_shadow_service import compute_speed_metrics

        return compute_speed_metrics(rescore, event)

    # -- operational status ----------------------------------------------
    def status(self, *, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        odds_creds = bool(self.settings.the_odds_api_key)
        ledger = self.session.scalar(
            select(ProviderRequestLedger)
            .where(ProviderRequestLedger.provider == "THE_ODDS_API")
            .order_by(ProviderRequestLedger.requested_at.desc()).limit(1)
        )
        day_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
        requests_today = int(self.session.scalar(
            select(func.count()).select_from(ProviderRequestLedger).where(
                ProviderRequestLedger.provider == "THE_ODDS_API",
                ProviderRequestLedger.requested_at >= day_start,
                ProviderRequestLedger.requested_at < day_start + timedelta(days=1),
            )
        ) or 0)
        last_event = self.session.scalar(select(AvailabilityEvent).order_by(AvailabilityEvent.received_at.desc()).limit(1))
        last_rescore = self.session.scalar(select(ShadowRescore).order_by(ShadowRescore.rescore_completed_at.desc()).limit(1))
        rescore_duration = (
            _delta_seconds(last_rescore.rescore_started_at, last_rescore.rescore_completed_at)
            if last_rescore else None
        )
        circuit = self.session.scalar(
            select(JobRun).where(
                JobRun.job_name.in_(("refresh-odds", "poll-availability-events", "refresh-schedule")),
                JobRun.status == RunStatus.FAILED,
            ).order_by(JobRun.completed_at.desc()).limit(1)
        )
        if not odds_creds:
            operational_state = "LIVE_SHADOW_ARMED_NO_ODDS_CREDENTIALS"
        elif self.settings.live_shadow_enabled:
            operational_state = "LIVE_SHADOW_ARMED"
        else:
            operational_state = "LIVE_SHADOW_READY"
        return {
            "operational_state": operational_state,
            "availability_polling_enabled": self.settings.live_shadow_enabled,
            "odds_provider_enabled": odds_creds,
            "odds_credentials_present": odds_creds,
            "required_odds_config_variable": None if odds_creds else ODDS_CONFIG_VARIABLE,
            "executable": False,
            "quota": {
                "quota_remaining": ledger.quota_remaining if ledger else None,
                "quota_floor": self.settings.odds_quota_floor,
                "daily_request_budget": self.settings.odds_daily_request_budget,
                "requests_today": requests_today,
            },
            "cadence": {
                "availability_slow_minutes": self.settings.availability_poll_slow_minutes,
                "availability_fast_minutes": self.settings.availability_poll_fast_minutes,
                "pregame_fast_window_hours": self.settings.pregame_fast_window_hours,
                "odds_pregame_refresh_minutes": self.settings.odds_pregame_refresh_minutes,
                "quote_burst_offsets_seconds": self.settings.quote_burst_offset_list,
            },
            "last_successful_schedule_refresh": self._last_success("refresh-schedule"),
            "last_successful_event_poll": self._last_success("poll-availability-events"),
            "last_successful_odds_refresh": self._last_success("refresh-odds"),
            "last_event_received": _iso(last_event.received_at) if last_event else None,
            "last_event_source": last_event.source if last_event else None,
            "last_rescore_duration_seconds": rescore_duration,
            "active_games_monitored": len(self.monitored_games(now)),
            "circuit_state": {
                "last_provider_failure": _iso(circuit.completed_at) if circuit else None,
                "last_failed_job": circuit.job_name if circuit else None,
            },
        }

    def _last_success(self, job_name: str) -> str | None:
        run = self.session.scalar(
            select(JobRun).where(JobRun.job_name == job_name, JobRun.status == RunStatus.SUCCEEDED)
            .order_by(JobRun.completed_at.desc()).limit(1)
        )
        return _iso(run.completed_at) if run and run.completed_at else None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value else None


def _delta_seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((_aware(end) - _aware(start)).total_seconds(), 3)


__all__ = ["GameAvailabilitySnapshot", "LiveShadowOperator", "ODDS_CONFIG_VARIABLE"]
