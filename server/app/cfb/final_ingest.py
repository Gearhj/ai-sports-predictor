"""CFB finals from the producer's retained CFBD evidence -- validated, bound, projected.

WHERE FINALS COME FROM
----------------------
Only from evidence Codex already captured under its bounded, approved plan:
``RETAINED_EVIDENCE_POINTER.json`` -> a content-addressed evidence index -> a
hash-bound final-result export, each row of which names the raw CFBD
``/games`` response it came from (path, SHA-256, array locator). AISP makes no
request of its own. There is no CFB poller here and none is created.

WHAT AISP CHECKS BEFORE ANYTHING IS RECORDED
--------------------------------------------
* Every hop from pointer to row verifies by SHA-256 and byte count.
* The row is re-read from the RAW response at its locator: same game id, same
  home and away CFBD ids, same points, ``completed`` literally true. A row that
  disagrees with the bytes it cites is refused.
* The game is one AISP holds: admitted by exact CFBD game id, with home and away
  CFBD team ids equal to the admitted sides. Names are never compared.
* The scheduled kickoff equals the admitted kickoff. A moved game is reported
  and left alone -- eligibility was decided against the schedule as it stood.

WHAT IS NOT CLAIMED
-------------------
CFBD's retained rows carry no completion, update or revision time. The capture
time is recorded as the OBSERVATION time and the completion clock is recorded as
absent. ``games.final_at`` is left empty rather than filled with a capture time
pretending to be a final whistle.

Overtime is read from the raw line scores (more than four periods) and kept with
the observation. The moneyline settles on the final score including overtime.
A later capture with different points is a new observation; the projection then
follows provenance rank and recency, and any settled position is corrected by
an adjustment beside its original settlement (``app.cfb.settlement_corrections``).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.gen2.exchange.canonical_json import canonical_sha256
from app.models import (
    CfbFinalObservation, CfbResearchForecast, ExternalIdMapping, Game, GameStateObservation, GameStatus,
    GameTeam, IngestionRun, RunStatus,
)
from app.services.result_provenance import result_tier

JOB_NAME = "ingest-cfb-finals"
SOURCE = "CFBD"
SOURCE_MODE = "RESEARCH_SHADOW"
POINTER_SCHEMA = "CFB_ACTIVATION_RETAINED_EVIDENCE_POINTER_V1"
INDEX_SCHEMA = "CFB_ACTIVATION_RETAINED_EVIDENCE_INDEX_V1"
FINALS_SCHEMA = "CFB_RETAINED_FINAL_RESULT_EVIDENCE_V1"
COMPLETION_CLOCK_ABSENT = "ABSENT_FROM_RETAINED_SOURCE_SCHEMA"


class FinalEvidenceRefused(RuntimeError):
    """The pointer, index or export cannot be proved from bytes."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _bound(ref: dict[str, Any], label: str) -> bytes:
    path = pathlib.Path(ref["path"])
    if not path.exists():
        raise FinalEvidenceRefused(f"{label} is missing: {path}")
    raw = path.read_bytes()
    if _sha(raw) != str(ref["sha256"]).lower() or len(raw) != int(ref["bytes"]):
        raise FinalEvidenceRefused(f"{label} does not match its declared SHA-256 and byte count")
    return raw


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def load_evidence(pointer_path: pathlib.Path) -> dict[str, Any]:
    """Pointer -> index -> final-result export, every hop proved."""

    pointer = json.loads(pointer_path.read_bytes())
    if pointer.get("schemaVersion") != POINTER_SCHEMA:
        raise FinalEvidenceRefused(f"pointer schema {pointer.get('schemaVersion')!r}")
    index_ref = pointer["index"]
    index_raw = _bound(index_ref, "evidence index")
    if pathlib.Path(index_ref["path"]).parent.name.lower() != str(index_ref["sha256"]).lower():
        raise FinalEvidenceRefused("evidence index is not stored at its content address")
    index = json.loads(index_raw)
    if index.get("schemaVersion") != INDEX_SCHEMA:
        raise FinalEvidenceRefused(f"index schema {index.get('schemaVersion')!r}")
    export_ref = index["artifacts"]["finalResultEvidenceJson"]
    export = json.loads(_bound(export_ref, "final-result export"))
    if export.get("schemaVersion") != FINALS_SCHEMA:
        raise FinalEvidenceRefused(f"export schema {export.get('schemaVersion')!r}")
    return {"index_sha256": str(index_ref["sha256"]).lower(), "export_sha256": str(export_ref["sha256"]).lower(),
            "export": export, "generated_at": index.get("generatedAtUtc")}


def _raw_rows(export: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, str]:
    source = export.get("source") or {}
    try:
        raw = _bound(source, "raw CFBD /games response")
    except (FinalEvidenceRefused, KeyError) as exc:
        return None, f"RAW_UNAVAILABLE:{exc}"[:200]
    rows = json.loads(raw)
    return (rows if isinstance(rows, list) else None), "RAW_VERIFIED"


def _raw_check(row: dict[str, Any], raw_rows: list[dict[str, Any]] | None) -> tuple[bool, dict[str, Any]]:
    """Re-read the row from the bytes it cites. Returns (ok, raw facts)."""

    if raw_rows is None:
        return False, {}
    locator = str(row.get("sourceRecordLocator") or "")
    try:
        position = int(locator.strip("$[]"))
        raw = raw_rows[position]
    except (ValueError, IndexError):
        return False, {}
    facts = {
        "id": raw.get("id"), "homeId": raw.get("homeId"), "awayId": raw.get("awayId"),
        "homePoints": raw.get("homePoints"), "awayPoints": raw.get("awayPoints"),
        "completed": raw.get("completed"), "startDate": raw.get("startDate"),
        "periods": max(len(raw.get("homeLineScores") or []), len(raw.get("awayLineScores") or [])) or None,
    }
    ok = (str(facts["id"]) == str(row.get("sourceGameId"))
          and str(facts["homeId"]) == str(row.get("homeTeamId"))
          and str(facts["awayId"]) == str(row.get("awayTeamId"))
          and facts["homePoints"] == row.get("homeFinalPoints")
          and facts["awayPoints"] == row.get("awayFinalPoints")
          and facts["completed"] is True)
    return ok, facts


def _team_cfbd_id(session: Session, team_id: Any) -> str | None:
    return session.scalar(select(ExternalIdMapping.external_id).where(
        ExternalIdMapping.entity_type == "team", ExternalIdMapping.source == SOURCE,
        ExternalIdMapping.entity_id == team_id))


def _game_for(session: Session, source_game_id: str) -> tuple[Game | None, str | None]:
    """The admitted game, admitting it from a held forecast if needed. Never by name."""

    from app.cfb.game_binding import CfbBindingError, admit_game, canonical_game_id

    game_id = canonical_game_id(session, source_game_id)
    if game_id is None:
        forecast = session.scalar(select(CfbResearchForecast).where(
            CfbResearchForecast.source_game_id == str(source_game_id)).limit(1))
        if forecast is None:
            return None, "NO_AISP_FORECAST_OR_GAME"
        try:
            admitted = admit_game(session, forecast)
        except CfbBindingError as exc:
            return None, f"GAME_BINDING_REFUSED:{exc}"[:200]
        game_id = canonical_game_id(session, source_game_id)
        for row in session.scalars(select(CfbResearchForecast).where(
                CfbResearchForecast.source_game_id == str(source_game_id))):
            if row.canonical_game_id is None:
                row.canonical_game_id = game_id
        if admitted.get("result") != "ADMITTED" and game_id is None:
            return None, "GAME_BINDING_REFUSED"
    return session.get(Game, game_id), None


def strongest_final(session: Session, game_id: Any) -> GameStateObservation | None:
    observations = [row for row in session.scalars(select(GameStateObservation).where(
        GameStateObservation.game_id == game_id, GameStateObservation.status == GameStatus.FINAL.value))
        if row.home_score is not None and row.away_score is not None]
    if not observations:
        return None
    floor = datetime.min.replace(tzinfo=UTC)
    return min(observations, key=lambda row: (
        result_tier(row.source), -(_aware(row.available_as_of) or floor).timestamp(),
        str(row.payload_checksum or "")))


def _project(session: Session, game: Game) -> dict[str, Any]:
    strongest = strongest_final(session, game.id)
    if strongest is None:
        return {"changed": False}
    home, away = int(strongest.home_score), int(strongest.away_score)
    previous = (game.status, game.home_score, game.away_score)
    changed = previous != (GameStatus.FINAL, home, away)
    game.status = GameStatus.FINAL
    game.home_score, game.away_score = home, away
    for side in session.scalars(select(GameTeam).where(GameTeam.game_id == game.id)):
        side.score = home if side.is_home else away
    session.flush()
    return {"changed": changed, "previous": [str(previous[0]), previous[1], previous[2]],
            "observation_id": strongest.id, "scores": [home, away]}


def ingest_retained_finals(session: Session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """One pass over the current retained evidence. Idempotent by result checksum."""

    moment = now or datetime.now(UTC)
    summary: dict[str, Any] = {"job": JOB_NAME, "source": SOURCE, "provider_requests": 0,
                               "rows_in_export": 0, "held_games_matched": 0, "observations_written": 0,
                               "observations_reused": 0, "games_projected_final": 0, "corrections": [],
                               "skipped": [], "not_held": 0}
    pointer = pathlib.Path(str(getattr(settings, "cfb_final_evidence_pointer_path", "") or ""))
    if not str(pointer) or not pointer.exists():
        summary["status"] = "NO_RETAINED_EVIDENCE_POINTER"
        return summary
    try:
        evidence = load_evidence(pointer)
    except (FinalEvidenceRefused, KeyError, ValueError, OSError) as exc:
        summary["status"] = f"EVIDENCE_REFUSED:{exc}"[:300]
        return summary
    export = evidence["export"]
    summary.update({"evidence_index_sha256": evidence["index_sha256"],
                    "export_sha256": evidence["export_sha256"]})
    rows = export.get("rows") or []
    summary["rows_in_export"] = len(rows)
    raw_rows, raw_state = _raw_rows(export)
    summary["raw_source"] = raw_state
    held_ids = {sid for (sid,) in session.execute(select(CfbResearchForecast.source_game_id)).all()}
    held_ids |= {ext for (ext,) in session.execute(select(ExternalIdMapping.external_id).where(
        ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == SOURCE)).all()}

    from app.cfb.settlement_corrections import correct_settled_positions

    for row in rows:
        source_game_id = str(row.get("sourceGameId"))
        if source_game_id not in held_ids:
            summary["not_held"] += 1
            continue
        base = {"source_game_id": source_game_id,
                "matchup": f"{row.get('awayTeam')} @ {row.get('homeTeam')}"}
        if row.get("sourceCompleted") is not True or row.get("sourceStatusField") != "completed":
            summary["skipped"].append({**base, "skipped": "SOURCE_NOT_COMPLETED"})
            continue
        ok, facts = _raw_check(row, raw_rows)
        if not ok:
            summary["skipped"].append({**base, "skipped": "RAW_RECORD_DISAGREES_OR_UNAVAILABLE",
                                       "raw_source": raw_state})
            continue
        game, reason = _game_for(session, source_game_id)
        if game is None:
            summary["skipped"].append({**base, "skipped": reason})
            continue
        sides = {side.is_home: side.team_id for side in session.scalars(
            select(GameTeam).where(GameTeam.game_id == game.id))}
        if (_team_cfbd_id(session, sides.get(True)) != str(row.get("homeTeamId"))
                or _team_cfbd_id(session, sides.get(False)) != str(row.get("awayTeamId"))):
            summary["skipped"].append({**base, "skipped": "HOME_AWAY_IDENTITY_MISMATCH"})
            continue
        kickoff = _ts(row.get("scheduledKickoffUtc"))
        if kickoff is None or _aware(game.starts_at) != kickoff:
            summary["skipped"].append({**base, "skipped": "KICKOFF_DIFFERS_FROM_ADMITTED_GAME",
                                       "source_kickoff": row.get("scheduledKickoffUtc"),
                                       "admitted_kickoff": _aware(game.starts_at).isoformat()})
            continue
        summary["held_games_matched"] += 1
        home_points, away_points = int(row["homeFinalPoints"]), int(row["awayFinalPoints"])
        result_facts = {"source": SOURCE, "sourceGameId": source_game_id,
                        "homeTeamId": str(row["homeTeamId"]), "awayTeamId": str(row["awayTeamId"]),
                        "homePoints": home_points, "awayPoints": away_points, "completed": True,
                        "periods": facts.get("periods")}
        checksum = canonical_sha256(result_facts)
        existing = session.scalar(select(CfbFinalObservation).where(
            CfbFinalObservation.game_id == game.id, CfbFinalObservation.payload_checksum == checksum))
        captured = _ts(row.get("capturedAtUtc")) or moment
        if existing is None:
            state = GameStateObservation(
                game_id=game.id, home_team_id=sides.get(True), away_team_id=sides.get(False),
                scheduled_at=game.starts_at, status=GameStatus.FINAL.value,
                home_score=home_points, away_score=away_points, final_at=None,
                observed_at=captured, available_as_of=moment, source=SOURCE, source_mode=SOURCE_MODE,
                source_record_id=source_game_id, payload_checksum=checksum)
            session.add(state)
            session.flush()
            session.add(CfbFinalObservation(
                game_id=game.id, game_state_observation_id=state.id, source=SOURCE,
                source_game_id=source_game_id, home_source_team_id=str(row["homeTeamId"]),
                away_source_team_id=str(row["awayTeamId"]), home_points=home_points, away_points=away_points,
                source_completed=True, captured_at_utc=captured,
                completion_clock_status=str(row.get("sourceCompletionTimestampStatus") or COMPLETION_CLOCK_ABSENT),
                evidence_index_sha256=evidence["index_sha256"], evidence_artifact_sha256=evidence["export_sha256"],
                source_response_sha256=str(row.get("sourceResponseSha256") or ""),
                source_record_locator=str(row.get("sourceRecordLocator") or "") or None,
                raw_record_verified=True, payload_checksum=checksum,
                evidence_row={**row, "rawFacts": facts, "overtime": bool((facts.get("periods") or 0) > 4)}))
            session.flush()
            summary["observations_written"] += 1
        else:
            summary["observations_reused"] += 1
        projection = _project(session, game)
        if projection["changed"]:
            summary["games_projected_final"] += 1
        corrections = correct_settled_positions(session, game, observation_id=projection.get("observation_id"),
                                                now=moment)
        summary["corrections"].extend(corrections)
    if summary["observations_written"] or summary["games_projected_final"]:
        session.add(IngestionRun(
            source=SOURCE, job_name=JOB_NAME, status=RunStatus.SUCCEEDED, started_at=moment,
            completed_at=datetime.now(UTC), records_processed=summary["observations_written"],
            schema_version=FINALS_SCHEMA,
            source_metadata={k: summary[k] for k in ("evidence_index_sha256", "export_sha256", "rows_in_export",
                                                     "held_games_matched", "observations_written",
                                                     "observations_reused", "games_projected_final")}))
    session.flush()
    summary["status"] = "OK"
    return summary


__all__ = ["JOB_NAME", "FinalEvidenceRefused", "ingest_retained_finals", "load_evidence", "strongest_final"]
