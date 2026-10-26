"""The last-chance decision tick: one checkpoint evaluation per start cluster at T-16.

OWNER DECISION B (James, 2026-10-25)
------------------------------------
On 2026-10-25 the 4:25 PM NFL games got no decision because the in-slot ticks
never ran. The interval checkpoints (every 2-5 minutes inside T-30..T-15) are
still the primary path; this GUARANTEES one more evaluation per start cluster
at T-16 -- inside the window, one minute before it closes -- for every sport
whose checkpoint is switched on, even when the interval ticks were skipped (a
busy lane, a restart, an APScheduler skip).

WHAT IT RUNS
------------
The sport's OWN existing checkpoint job, unchanged, through the orchestrator
(so it gets its own ``job_runs`` row): ``evaluate-nfl-paper-checkpoint``,
``evaluate-cfb-paper-checkpoint``, ``evaluate-nba-paper-checkpoint``,
``evaluate-nhl-paper-checkpoint`` and MLB's FINAL_PREGAME checkpoint
``freeze-shadow-experiment``. Each still reads the clock itself and refuses
outside its window; each is still idempotent per game (a decision is recorded
once; a BLOCKED-for-a-prerequisite one may be superseded by a later tick).

ONCE, AND NEVER ON TOP OF A RUNNING INSTANCE
--------------------------------------------
* A cluster is skipped when every game in it already holds a FINAL_PREGAME
  decision that is not BLOCKED (``ShadowExperimentDecision``, which every sport
  writes).
* Otherwise ONE claim per (sport, cluster) is taken in ``nfl_acquisition_phase_claims``
  under lane ``last_chance_checkpoint:<SPORT>`` (UNIQUE, committed first), so a
  restart or a second process cannot run it twice.
* The planner job is registered as ``asp-dc-last-chance-checkpoint``, on the
  ``decision-checkpoints`` lane, so it is serial with the scheduled NFL, CFB, NHL
  and MLB checkpoints -- never interleaved with them, and never racing them on
  cross-sport shared staking room. Each checkpoint body also holds its
  ``decision_checkpoint_guard`` slot (NBA's included, added with this change), so
  an admin-path run or NBA's core-lane tick in flight makes this one DECLINE
  (``CHECKPOINT_ALREADY_RUNNING``); the claim is then released, so the next
  minute's tick may still run it while the window is open.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    ExperimentDecisionPhase, Game, GameStatus, League, NflAcquisitionPhaseClaim, ShadowExperimentDecision,
)

JOB_NAME = "run-last-chance-checkpoints"
PHASE = "PRIMARY"
CLAIM_PROVIDER = "AISP"
#: The decision window's hard close, shared by every sport's paper policy.
WINDOW_CLOSES = timedelta(minutes=15)

DISABLED = "DISABLED"
DUE = "DUE"
ALREADY_DECIDED = "ALREADY_DECIDED"
ALREADY_CLAIMED = "ALREADY_CLAIMED"
CHECKPOINT_DISABLED = "CHECKPOINT_DISABLED"
RAN = "RAN"
DEFERRED_CHECKPOINT_ALREADY_RUNNING = "DEFERRED_CHECKPOINT_ALREADY_RUNNING"
CHECKPOINT_FAILED = "CHECKPOINT_FAILED"


@dataclass(frozen=True)
class SportCheckpoint:
    sport: str
    league_code: str
    job_name: str
    enabled: Callable[[Any], bool]
    live_only: bool = False


SPORT_CHECKPOINTS: tuple[SportCheckpoint, ...] = (
    SportCheckpoint("NFL", "NFL", "evaluate-nfl-paper-checkpoint", lambda s: True),
    SportCheckpoint("CFB", "CFB", "evaluate-cfb-paper-checkpoint",
                    lambda s: bool(getattr(s, "cfb_production_consumer_enabled", False))),
    SportCheckpoint("NBA", "NBA", "evaluate-nba-paper-checkpoint",
                    lambda s: bool(getattr(s, "nba_production_consumer_enabled", False))),
    SportCheckpoint("NHL", "NHL", "evaluate-nhl-paper-checkpoint",
                    lambda s: bool(getattr(s, "nhl_paper_checkpoint_enabled", False))),
    SportCheckpoint("MLB", "MLB", "freeze-shadow-experiment",
                    lambda s: bool(getattr(s, "live_shadow_enabled", False)), live_only=True),
)


def claim_lane(sport: str) -> str:
    return f"last_chance_checkpoint:{sport}"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _decided(session: Session, game_id: Any) -> bool:
    """A FINAL_PREGAME decision that is not BLOCKED already exists for this game."""

    return session.scalar(select(ShadowExperimentDecision.id).where(
        ShadowExperimentDecision.game_id == game_id,
        ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME,
        ShadowExperimentDecision.recommendation != "BLOCKED").limit(1)) is not None


def _claimed(session: Session, sport: str, cluster: datetime) -> bool:
    return session.scalar(select(NflAcquisitionPhaseClaim.id).where(
        NflAcquisitionPhaseClaim.lane == claim_lane(sport),
        NflAcquisitionPhaseClaim.kickoff_cluster_utc == _aware(cluster),
        NflAcquisitionPhaseClaim.phase == PHASE).limit(1)) is not None


def plan(session: Session, settings: Any, *, now: datetime) -> dict[str, Any]:
    """Clusters at their last-chance minute: ``T-offset <= now < T-15`` (default [T-16, T-15)).

    Every cluster in that minute gets an entry with its state, so a skip is
    always named: DUE, ALREADY_DECIDED, ALREADY_CLAIMED or CHECKPOINT_DISABLED.
    """

    moment = _aware(now)
    offset = timedelta(minutes=int(getattr(settings, "last_chance_checkpoint_minutes_before_start", 16)))
    entries: list[dict[str, Any]] = []
    for spec in SPORT_CHECKPOINTS:
        query = (select(Game.id, Game.starts_at).join(League, League.id == Game.league_id)
                 .where(League.code == spec.league_code, Game.status == GameStatus.SCHEDULED,
                        Game.starts_at > moment + WINDOW_CLOSES, Game.starts_at <= moment + offset))
        if spec.live_only:
            query = query.where(Game.source_mode == "LIVE")
        clusters: dict[datetime, list[Any]] = {}
        for game_id, starts_at in session.execute(query.order_by(Game.starts_at)):
            clusters.setdefault(_aware(starts_at), []).append(game_id)
        for cluster, game_ids in sorted(clusters.items()):
            undecided = [str(g) for g in game_ids if not _decided(session, g)]
            if not spec.enabled(settings):
                state = CHECKPOINT_DISABLED
            elif not undecided:
                state = ALREADY_DECIDED
            elif _claimed(session, spec.sport, cluster):
                state = ALREADY_CLAIMED
            else:
                state = DUE
            entries.append({"sport": spec.sport, "job_name": spec.job_name, "cluster_start": cluster.isoformat(),
                            "minutes_to_start": round((cluster - moment).total_seconds() / 60.0, 2),
                            "games": sorted(str(g) for g in game_ids), "undecided_games": sorted(undecided),
                            "state": state})
    return {"evaluated_at": moment.isoformat(), "entries": entries,
            "due": [e for e in entries if e["state"] == DUE]}


def claim(session: Session, entry: dict[str, Any], *, invocation_id: str, now: datetime,
          invocation_path: str = "SCHEDULED") -> NflAcquisitionPhaseClaim | None:
    """The ONE claim for (sport, cluster), committed; None when another run holds it."""

    games = list(entry["undecided_games"])
    row = NflAcquisitionPhaseClaim(
        lane=claim_lane(entry["sport"]), provider=CLAIM_PROVIDER, sport=entry["sport"],
        kickoff_cluster_utc=_aware(datetime.fromisoformat(entry["cluster_start"])), phase=PHASE,
        due_game_ids_sha256=hashlib.sha256("\n".join(sorted(games)).encode("utf-8")).hexdigest(),
        due_game_count=len(games), claimed_at=_aware(now),
        invocation_id=str(invocation_id)[:120], invocation_path=str(invocation_path)[:32])
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return None
    return row


def release(session: Session, claim_row: NflAcquisitionPhaseClaim) -> None:
    """Hand the opportunity back: the checkpoint declined because another instance holds its slot."""

    session.execute(delete(NflAcquisitionPhaseClaim).where(NflAcquisitionPhaseClaim.id == claim_row.id))
    session.commit()


__all__ = [
    "ALREADY_CLAIMED", "ALREADY_DECIDED", "CHECKPOINT_DISABLED", "CHECKPOINT_FAILED",
    "DEFERRED_CHECKPOINT_ALREADY_RUNNING", "DUE", "JOB_NAME", "RAN", "SPORT_CHECKPOINTS",
    "claim", "claim_lane", "plan", "release",
]
