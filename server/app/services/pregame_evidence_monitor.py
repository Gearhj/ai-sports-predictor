"""Narrow, idempotent monitoring for the MLB pregame starter/lineup evidence
pipeline. Runs inside the 5-minute experiment freeze job; reads persisted
rows only (never a provider); records each condition at most once per
(kind, slate date, game) as an AuditEvent with action
``pregame_evidence.alert``.

Conditions (AISP_PREGAME_EVIDENCE_MONITOR_V1):

  NO_STARTER_OBSERVATIONS         a LIVE slate game before first pitch has no starter observation at all
  STARTER_EVIDENCE_OLDER_THAN_EXPECTED
                                  a game inside the pregame fast window whose latest starter observation
                                  is older than EXPECTED_MAX_STARTER_AGE_MINUTES (the poll heartbeat +
                                  schedule-refresh cadence should keep it under this)
  POLL_JOB_SILENT                 no MLB poll-availability-events ingestion run completed within
                                  POLL_SILENCE_MINUTES while a slate game is inside the fast window
  FINAL_DECISION_MISSING          a game's cutoff has passed and no FINAL_PREGAME decision exists for an
                                  active experiment (the freeze job did not run inside the final window)
  STALE_BLOCK_SHARE_ABNORMAL      more than STALE_SHARE_THRESHOLD of games that reached their final
                                  window were blocked ONLY by starter freshness at the reference
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import local_day_bounds
from app.models import (
    AuditEvent,
    ExperimentDecisionPhase,
    Game,
    GameStatus,
    IngestionRun,
    RunStatus,
    ShadowExperiment,
    ShadowExperimentDecision,
    StarterObservation,
)
from app.services.shadow_experiment_service import FINAL_PREGAME_CUTOFF_MINUTES

MONITOR_VERSION = "AISP_PREGAME_EVIDENCE_MONITOR_V1"
ALERT_ACTION = "pregame_evidence.alert"
EXPECTED_MAX_STARTER_AGE_MINUTES = 45   # > 30-minute schedule refresh + one missed poll heartbeat
POLL_SILENCE_MINUTES = 10               # 5 fast-poll cadences
STALE_SHARE_THRESHOLD = 0.5


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class PregameEvidenceMonitor:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- public -------------------------------------------------------------
    def run(self, event_date: date, *, now: datetime | None = None) -> dict[str, Any]:
        now = _aware(now) or datetime.now(UTC)
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        games = list(self.session.scalars(select(Game).where(
            Game.starts_at >= start, Game.starts_at < end, Game.source_mode == "LIVE",
        ).order_by(Game.starts_at)))
        found: list[dict[str, Any]] = []
        fast_window = timedelta(hours=self.settings.pregame_fast_window_hours)
        any_in_fast_window = False
        final_window_games = 0
        stale_only_blocked = 0
        experiments = list(self.session.scalars(select(ShadowExperiment).where(ShadowExperiment.is_active.is_(True))))

        for game in games:
            starts_at = _aware(game.starts_at)
            cutoff = starts_at - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
            pregame = now < starts_at and game.status in (GameStatus.SCHEDULED, GameStatus.LIVE)
            latest = self.session.scalar(select(func.max(StarterObservation.available_as_of))
                                         .where(StarterObservation.game_id == game.id))
            latest = _aware(latest)
            if pregame and latest is None:
                found.append(self._alert("NO_STARTER_OBSERVATIONS", event_date, game, now,
                                         {"starts_at": starts_at.isoformat()}))
            in_fast = pregame and (starts_at - now) <= fast_window
            any_in_fast_window = any_in_fast_window or in_fast
            if in_fast and latest is not None and (now - latest) > timedelta(minutes=EXPECTED_MAX_STARTER_AGE_MINUTES):
                found.append(self._alert("STARTER_EVIDENCE_OLDER_THAN_EXPECTED", event_date, game, now, {
                    "latest_observation_at": latest.isoformat(),
                    "age_minutes": round((now - latest).total_seconds() / 60, 1),
                    "expected_max_minutes": EXPECTED_MAX_STARTER_AGE_MINUTES,
                }))
            if now > cutoff:
                final_window_games += 1
                for experiment in experiments:
                    if experiment.decision_policy == "AISP_PRACTICAL_PREGAME_POLICY_V2":
                        continue  # superseded before activation; never evaluated
                    if _aware(experiment.started_at) > starts_at:
                        continue
                    final = self.session.scalar(select(ShadowExperimentDecision).where(
                        ShadowExperimentDecision.experiment_id == experiment.id,
                        ShadowExperimentDecision.game_id == game.id,
                        ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME,
                        ShadowExperimentDecision.recommended_at <= cutoff,
                    ).order_by(ShadowExperimentDecision.recommended_at.desc()).limit(1))
                    if final is None:
                        found.append(self._alert("FINAL_DECISION_MISSING", event_date, game, now, {
                            "experiment": experiment.decision_policy, "cutoff_at": cutoff.isoformat(),
                        }))
                    elif experiment.decision_policy.startswith("AISP_PRACTICAL_PREGAME_POLICY") and self._stale_only(final):
                        stale_only_blocked += 1

        if any_in_fast_window:
            last_poll = self.session.scalar(select(func.max(IngestionRun.completed_at)).where(
                IngestionRun.job_name == "poll-availability-events",
                IngestionRun.source == "MLB_STATS_API",
                IngestionRun.status == RunStatus.SUCCEEDED,
            ))
            last_poll = _aware(last_poll)
            if last_poll is None or (now - last_poll) > timedelta(minutes=POLL_SILENCE_MINUTES):
                found.append(self._alert("POLL_JOB_SILENT", event_date, None, now, {
                    "last_poll_run_at": last_poll.isoformat() if last_poll else None,
                    "silence_threshold_minutes": POLL_SILENCE_MINUTES,
                }))
        if final_window_games and stale_only_blocked / final_window_games > STALE_SHARE_THRESHOLD:
            found.append(self._alert("STALE_BLOCK_SHARE_ABNORMAL", event_date, None, now, {
                "games_past_cutoff": final_window_games, "blocked_only_by_stale_starter": stale_only_blocked,
            }))
        self.session.flush()
        return {"version": MONITOR_VERSION, "games": len(games), "alerts_recorded": sum(1 for f in found if f["recorded"]),
                "alerts_seen": len(found), "kinds": sorted({f["kind"] for f in found})}

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _stale_only(decision: ShadowExperimentDecision) -> bool:
        ev = (decision.evidence or {}).get("v2_evidence") or {}
        sides = [ev.get("starter_home") or {}, ev.get("starter_away") or {}]
        reasons = [r for s in sides for r in (s.get("reasons") or [])]
        return bool(reasons) and all("not current" in r for r in reasons)

    def _alert(self, kind: str, event_date: date, game: Game | None, now: datetime, detail: dict[str, Any]) -> dict[str, Any]:
        key = f"{kind}:{event_date.isoformat()}:{game.id if game else 'slate'}"
        existing = self.session.scalar(select(AuditEvent.id).where(
            AuditEvent.action == ALERT_ACTION,
            AuditEvent.entity_type == "pregame_evidence",
            AuditEvent.metadata_json["key"].as_string() == key,
        ).limit(1))
        if existing is not None:
            return {"kind": kind, "key": key, "recorded": False}
        self.session.add(AuditEvent(
            actor_user_id=None, action=ALERT_ACTION, entity_type="pregame_evidence",
            entity_id=game.id if game else None, occurred_at=now,
            metadata_json={"key": key, "kind": kind, "version": MONITOR_VERSION,
                           "slate_date": event_date.isoformat(), "game_id": str(game.id) if game else None, **detail},
        ))
        return {"kind": kind, "key": key, "recorded": True}


__all__ = ["PregameEvidenceMonitor", "MONITOR_VERSION", "ALERT_ACTION"]
