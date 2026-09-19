"""NBA finals from retained ESPN schedule objects -- validated, bound, projected. No network.

WHERE FINALS COME FROM
----------------------
Only from the content-addressed schedule objects Model Studio already captured
(``app.nba.gen1_source``). AISP makes no request of its own, and there is no NBA
poller. A 2026-27 final appears here only after Model Studio's reviewed refresh
has captured an object that contains it and the owner has pinned that object.

WHAT IS CHECKED BEFORE ANYTHING IS RECORDED
-------------------------------------------
* The object hashes to its pin (and its receipt, when given, names it).
* The row literally says ``STATUS_FINAL`` with ``completed`` true and both scores.
* The game is one AISP holds, by exact ESPN game id.
* Home and away ESPN team ids equal the AISP game's teams' bound ``NBA_ESPN_TEAM``
  ids. Names are never compared. A team with no ESPN binding cannot be checked,
  so its final is refused (``TEAM_ESPN_BINDING_ABSENT``) rather than assumed.
* The source tipoff equals the admitted tipoff. A moved game is reported and
  left alone.

WHAT IS NOT CLAIMED
-------------------
The export carries no completion or revision time. The acquisition clock (from
the bound receipt) is the OBSERVATION time; ``games.final_at`` stays empty.
Overtime is read from the period count (more than four). The moneyline settles
on the final score including overtime. A later object with different points is
a second observation; the projection follows provenance rank and recency, and
any settled position is corrected by an adjustment beside its settlement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.gen2.exchange.canonical_json import canonical_sha256
from app.nba import gen1_common as common
from app.nba import gen1_source as source

JOB_NAME = "ingest-nba-finals"


def _team_espn(session: Session, team_id: Any) -> str | None:
    from app.models import ExternalIdMapping

    return session.scalar(select(ExternalIdMapping.external_id).where(
        ExternalIdMapping.entity_type == "team", ExternalIdMapping.source == common.SOURCE_ESPN_TEAM,
        ExternalIdMapping.entity_id == team_id))


def held_games(session: Session) -> dict[str, Any]:
    """ESPN game id -> AISP game id, for every NBA game AISP holds by exact mapping."""

    from app.models import ExternalIdMapping, Game, League

    rows = session.execute(
        select(ExternalIdMapping.external_id, Game.id)
        .join(Game, Game.id == ExternalIdMapping.entity_id)
        .join(League, League.id == Game.league_id)
        .where(ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == common.SOURCE_ESPN_GAME,
               League.code == common.LEAGUE_CODE)).all()
    return {str(espn): gid for espn, gid in rows}


def ingest(session: Session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """One pass over the pinned objects. Idempotent by result checksum."""

    from app.cfb.final_ingest import _project  # sport-neutral projection onto games/game_teams
    from app.cfb.settlement_corrections import correct_settled_positions
    from app.models import Game, GameStateObservation, GameStatus, GameTeam, IngestionRun, NbaFinalObservation, RunStatus

    moment = now or datetime.now(UTC)
    summary: dict[str, Any] = {"job": JOB_NAME, "source": source.SOURCE, "provider_requests": 0,
                               "held_games": 0, "finals_in_source": 0, "observations_written": 0,
                               "observations_reused": 0, "games_projected_final": 0, "not_final_yet": 0,
                               "skipped": [], "corrections": []}
    objects, problems = source.load_objects(settings)
    summary["source_objects"] = [o.describe() for o in objects]
    summary["source_problems"] = problems
    if not objects:
        summary["status"] = "NO_PINNED_SOURCE_OBJECT"
        return summary
    held = held_games(session)
    summary["held_games"] = len(held)
    for espn, game_id in sorted(held.items()):
        obj, row = source.row_for(objects, espn)
        if row is None:
            continue
        facts = source.final_facts(row)
        if facts is None:
            summary["not_final_yet"] += 1
            continue
        summary["finals_in_source"] += 1
        game = session.get(Game, game_id)
        sides = {side.is_home: side.team_id for side in session.scalars(
            select(GameTeam).where(GameTeam.game_id == game.id))}
        home_espn, away_espn = _team_espn(session, sides.get(True)), _team_espn(session, sides.get(False))
        base = {"espn_game_id": espn, "game_id": str(game.id)}
        if home_espn is None or away_espn is None:
            summary["skipped"].append({**base, "skipped": "TEAM_ESPN_BINDING_ABSENT"})
            continue
        if (home_espn, away_espn) != (facts["homeEspnTeamId"], facts["awayEspnTeamId"]):
            summary["skipped"].append({**base, "skipped": "HOME_AWAY_IDENTITY_MISMATCH"})
            continue
        tipoff = common.ts(row.get("start_date"))
        if tipoff is None or common.aware(game.starts_at) != tipoff:
            summary["skipped"].append({**base, "skipped": "TIPOFF_DIFFERS_FROM_ADMITTED_GAME",
                                       "source_tipoff": common.iso(tipoff),
                                       "admitted_tipoff": common.iso(game.starts_at)})
            continue
        checksum = canonical_sha256(facts)
        existing = session.scalar(select(NbaFinalObservation).where(
            NbaFinalObservation.game_id == game.id, NbaFinalObservation.payload_checksum == checksum))
        if existing is None:
            state = GameStateObservation(
                game_id=game.id, home_team_id=sides.get(True), away_team_id=sides.get(False),
                scheduled_at=game.starts_at, status=GameStatus.FINAL.value,
                home_score=facts["homePoints"], away_score=facts["awayPoints"], final_at=None,
                observed_at=obj.captured_at or moment, available_as_of=moment, source=source.SOURCE,
                source_mode=common.SOURCE_MODE, source_record_id=espn, payload_checksum=checksum)
            session.add(state)
            session.flush()
            session.add(NbaFinalObservation(
                game_id=game.id, game_state_observation_id=state.id, source=source.SOURCE, source_game_id=espn,
                home_espn_team_id=facts["homeEspnTeamId"], away_espn_team_id=facts["awayEspnTeamId"],
                home_points=facts["homePoints"], away_points=facts["awayPoints"], periods=facts["periods"],
                overtime=facts["overtime"], source_status=str(row.get("status_type_name")),
                source_object_sha256=obj.sha256, source_object_bytes=obj.bytes, captured_at_utc=obj.captured_at,
                completion_clock_status=source.COMPLETION_CLOCK_ABSENT, payload_checksum=checksum,
                evidence_row={"facts": facts, "season": row.get("season"), "season_type": row.get("season_type"),
                              "type_abbreviation": row.get("type_abbreviation"),
                              "source_tipoff_utc": common.iso(tipoff),
                              "capture_clock_status": obj.capture_clock_status,
                              "receipt_sha256": obj.receipt_sha256}))
            session.flush()
            summary["observations_written"] += 1
        else:
            summary["observations_reused"] += 1
        projection = _project(session, game)
        summary["games_projected_final"] += int(bool(projection.get("changed")))
        summary["corrections"].extend(correct_settled_positions(
            session, game, observation_id=projection.get("observation_id"), now=moment))
    if summary["observations_written"] or summary["games_projected_final"]:
        session.add(IngestionRun(
            source=source.SOURCE, job_name=JOB_NAME, status=RunStatus.SUCCEEDED, started_at=moment,
            completed_at=datetime.now(UTC), records_processed=summary["observations_written"],
            schema_version="AISP_NBA_RETAINED_ESPN_FINALS_V1",
            source_metadata={k: summary[k] for k in ("held_games", "finals_in_source", "observations_written",
                                                     "observations_reused", "games_projected_final")}))
    session.flush()
    summary["status"] = "OK"
    return summary


__all__ = ["JOB_NAME", "held_games", "ingest"]
