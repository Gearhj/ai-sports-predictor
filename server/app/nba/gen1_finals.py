"""NBA finals, in two separate lanes. No network, no provider request, no invented clock.

LANE 1 -- THE PRODUCTION PATH (``EXCHANGE_SOURCE_CONFIRMED``)
------------------------------------------------------------
``finals-discovery/current.json`` -> ``NBA_GEN1_FINAL_DISCOVERY_V1`` ->
``NBA_GEN1_SOURCE_CONFIRMED_FINAL_V1`` records, each bound by path, SHA-256 and
byte count (``app.nba.gen1_final_exchange`` proves them). This is how a 2026-27
NBA final reaches AISP. Only ``currentlySourceConfirmed`` records are current
source membership; every other revision is kept as retained, explicitly
INELIGIBLE evidence with its disposition stated.

LANE 2 -- HISTORICAL RECONSTRUCTION (``RETAINED_SOURCE_HISTORICAL``)
-------------------------------------------------------------------
Rows re-read from the retained ESPN season exports (``app.nba.gen1_source``).
This is how the finished 2025-26 season is reconstructed for historical scoring.
It is NOT the production path and never becomes one.

WHAT IS CHECKED BEFORE ANYTHING IS RECORDED (BOTH LANES)
--------------------------------------------------------
* The evidence proves from bytes against its own content address.
* The game is one AISP holds, by exact ESPN game id. Nothing is joined by name
  or by date, and an unheld game is listed, never guessed into existence.
* Team identity is compared exactly: the exchange lane against the AISP game's
  bound ``NBA_NEIL_FRANCHISE`` ids, the retained lane against its bound
  ``NBA_ESPN_TEAM`` ids. A team with no binding cannot be checked, so its final
  is refused rather than assumed.
* The source tipoff equals the admitted tipoff, to the second.

WHAT IS NEVER DONE
------------------
No completion clock is invented: ``completedAtUtc`` is null in every record and
``games.final_at`` stays empty; capture, validation and admission are recorded as
what they are. A withdrawn, corrected-away, absent or identity-changed final is
NOT settled automatically -- it is retained, flagged ``reconciliation_required``
and reported. Settlement corrections for an already-settled position are written
beside the settlement, never by editing it.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.gen2.exchange.canonical_json import canonical_sha256
from app.nba import gen1_common as common
from app.nba import gen1_identity as identity
from app.services import event_time_reconciliation as event_time
from app.nba import gen1_final_exchange as lane
from app.nba import gen1_source as source

JOB_NAME = "ingest-nba-finals"
EXCHANGE_JOB_NAME = "ingest-nba-source-confirmed-finals"
#: The provenance source string for a lane-1 final. Distinct from the retained
#: export reader, so one game's two lanes are never counted as two results.
EXCHANGE_SOURCE = "NBA_GEN1_SOURCE_CONFIRMED_FINAL"
EXCHANGE_SCHEMA_VERSION = "AISP_NBA_SOURCE_CONFIRMED_FINALS_V1"


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


def _franchise_ids(session: Session) -> dict[Any, str]:
    """AISP team id -> its bound Codex franchise id (exact mapping only)."""

    from app.models import ExternalIdMapping

    rows = session.execute(select(ExternalIdMapping.entity_id, ExternalIdMapping.external_id).where(
        ExternalIdMapping.entity_type == "team", ExternalIdMapping.source == common.SOURCE_FRANCHISE)).all()
    return {team_id: str(external) for team_id, external in rows}


# ---------------------------------------------------------------------------
# Lane 1: the source-confirmed finals exchange (the production path)
# ---------------------------------------------------------------------------

def ingest_exchange(session: Session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """One pass over ``finals-discovery/current.json``. Idempotent by record SHA-256."""

    from app.cfb.final_ingest import _project  # sport-neutral projection onto games/game_teams
    from app.cfb.settlement_corrections import correct_settled_positions
    from app.models import (
        Game, GameStateObservation, GameStatus, GameTeam, IngestionRun,
        NbaFinalDiscoveryManifest, NbaFinalObservation, RunStatus,
    )
    from app.models.nba_gen1 import LANE_EXCHANGE

    moment = now or datetime.now(UTC)
    summary: dict[str, Any] = {
        "job": EXCHANGE_JOB_NAME, "lane": LANE_EXCHANGE, "source": EXCHANGE_SOURCE,
        "provider_requests": 0, "records_listed": 0, "records_verified": 0, "records_refused": 0,
        "currently_source_confirmed": 0, "observations_written": 0, "observations_reused": 0,
        "retained_ineligible": 0, "games_projected_final": 0, "unbound_games": [],
        "reconciliation_required": [], "refused_records": [], "corrections": [], "advisories": [],
        "event_failures": [],
    }
    if not bool(getattr(settings, "nba_finals_exchange_enabled", True)):
        summary["status"] = "LANE_DISABLED"
        return summary
    pointer = lane.pointer_path(settings)
    if pointer is None or not pointer.is_file():
        summary["status"] = "NO_FINALS_DISCOVERY_POINTER"
        summary["pointer_path"] = str(pointer) if pointer else None
        return summary
    try:
        verified = lane.verify_lane(
            pointer, handoff_sha256=str(getattr(settings, "nba_production_handoff_sha256", "") or "") or None)
    except lane.FinalsLaneRefused as exc:
        summary["status"] = "LANE_REFUSED"
        summary["problems"] = exc.problems
        return summary
    except (ValueError, OSError) as exc:
        summary["status"] = f"LANE_UNREADABLE:{type(exc).__name__}:{exc}"[:300]
        return summary

    summary.update({"pointer_path": verified["pointer_path"], "manifest_sha256": verified["manifest_sha256"],
                    "predecessor_sha256": verified["predecessor_sha256"],
                    "contract_sha256": verified["contract_sha256"],
                    "release_sha256": verified["release_sha256"],
                    "capture_receipt_sha256": verified["capture_receipt_sha256"],
                    "source_object_sha256": verified["source_object_sha256"],
                    "dispositions": verified["dispositions"], "advisories": verified["advisories"],
                    "games_listed": len(verified["games"])})
    records = verified["records"]
    summary["records_listed"] = len(records)
    summary["records_verified"] = sum(1 for r in records if r.verified)
    summary["records_refused"] = sum(1 for r in records if not r.verified)
    summary["currently_source_confirmed"] = sum(1 for r in records if r.currently_confirmed and r.verified)
    summary["refused_records"] = [r.describe() for r in records if not r.verified][:20]

    manifest_row = session.scalar(select(NbaFinalDiscoveryManifest).where(
        NbaFinalDiscoveryManifest.manifest_sha256 == verified["manifest_sha256"]))
    new_manifest = manifest_row is None
    if new_manifest:
        manifest_row = NbaFinalDiscoveryManifest(
            manifest_sha256=verified["manifest_sha256"], manifest_bytes=verified["manifest_bytes"],
            schema_version=verified["schema_version"], producer_created_utc=verified["created_at_utc"],
            predecessor_sha256=verified["predecessor_sha256"], contract_sha256=verified["contract_sha256"],
            release_sha256=verified["release_sha256"],
            capture_receipt_sha256=verified["capture_receipt_sha256"],
            source_object_sha256=verified["source_object_sha256"],
            outcomes_listed=len(records), outcomes_current=summary["currently_source_confirmed"],
            records_verified=summary["records_verified"], records_refused=summary["records_refused"],
            games_listed=len(verified["games"]), dispositions=verified["dispositions"],
            detail={"semantics": verified["semantics"], "advisories": verified["advisories"],
                    "refused_records": summary["refused_records"],
                    "games": [g for g in verified["games"]
                              if g.get("disposition") != lane.DISPOSITION_CONFIRMED][:400]},
            first_verified_at=moment)
        session.add(manifest_row)
        session.flush()
    summary["manifest_first_seen"] = new_manifest

    held = held_games(session)
    franchises = _franchise_ids(session)
    existing_rows = {
        str(row.final_record_sha256): row for row in session.scalars(
            select(NbaFinalObservation).where(NbaFinalObservation.lane == LANE_EXCHANGE))
        if row.final_record_sha256}
    manifest_shas = {r.record_sha256 for r in records}
    current_games = {r.codex_game_id for r in records if r.currently_confirmed and r.verified}
    touched: set[Any] = set()

    for record in records:
        before_summary = deepcopy(summary)
        before_existing = dict(existing_rows)
        before_touched = set(touched)
        try:
            with session.begin_nested():
                if not record.verified:
                    continue
                espn = record.source_game_id
                game_id = held.get(espn)
                if game_id is None:
                    # Listed, never guessed: no AISP game carries this ESPN id yet.
                    if record.currently_confirmed:
                        summary["unbound_games"].append({"codex_game_id": record.codex_game_id, "espn_game_id": espn,
                                                         "record_sha256": record.record_sha256,
                                                         "state": "NO_AISP_GAME_FOR_THIS_ESPN_ID"})
                    continue
                game = session.get(Game, game_id)
                sides = {side.is_home: side.team_id for side in session.scalars(
                    select(GameTeam).where(GameTeam.game_id == game.id))}
                home_franchise = franchises.get(sides.get(True))
                away_franchise = franchises.get(sides.get(False))
                reason = None
                if home_franchise is None or away_franchise is None:
                    reason = "TEAM_FRANCHISE_BINDING_ABSENT"
                elif (home_franchise, away_franchise) != (record.home_franchise_id, record.away_franchise_id):
                    reason = "PARTICIPANTS_DIFFER_FROM_ADMITTED_GAME"
                else:
                    try:
                        clock = identity.game_clock_assessment(session, game.id, provider_start=record.tipoff,
                            home_franchise_id=record.home_franchise_id, away_franchise_id=record.away_franchise_id)
                    except identity.IdentityRefused as exc:
                        reason = str(exc)
                existing = existing_rows.get(record.record_sha256)

                if reason is not None:
                    # Changed participants or schedule: explicit reconciliation, never
                    # automatic settlement. The record is retained as evidence, unsettled.
                    summary["reconciliation_required"].append({
                        "codex_game_id": record.codex_game_id, "game_id": str(game.id), "reason": reason,
                        "record_sha256": record.record_sha256,
                        "source_tipoff_utc": common.iso(record.tipoff),
                        "admitted_tipoff_utc": common.iso(game.starts_at)})
                    if existing is None:
                        session.add(_retained_row(record, game_id=game.id, manifest=manifest_row,
                                                  disposition=lane.UNRECONCILED, reconciliation=reason,
                                                  confirmed=record.currently_confirmed))
                        summary["retained_ineligible"] += 1
                        session.flush()
                    else:
                        existing.currently_source_confirmed = record.currently_confirmed
                        existing.reconciliation_required = True
                        existing.reconciliation_reason = reason
                        existing.disposition = lane.UNRECONCILED
                    continue

                if not record.currently_confirmed:
                    # A preserved earlier revision: superseded if this game has a current
                    # record, withdrawn if the source no longer confirms any final for it.
                    disposition = lane.SUPERSEDED if record.codex_game_id in current_games else lane.WITHDRAWN
                    needs_reconciliation = disposition == lane.WITHDRAWN
                    if existing is None:
                        session.add(_retained_row(record, game_id=game.id, manifest=manifest_row,
                                                  disposition=disposition,
                                                  reconciliation="SOURCE_WITHDREW_THIS_FINAL" if needs_reconciliation else None,
                                                  confirmed=False))
                        summary["retained_ineligible"] += 1
                        session.flush()
                    else:
                        existing.currently_source_confirmed = False
                        existing.disposition = disposition
                        if needs_reconciliation and existing.game_state_observation_id is not None:
                            existing.reconciliation_required = True
                            existing.reconciliation_reason = "SOURCE_WITHDREW_A_FINAL AISP HAD ALREADY RECORDED"
                    if needs_reconciliation:
                        summary["reconciliation_required"].append({
                            "codex_game_id": record.codex_game_id, "game_id": str(game.id),
                            "reason": "FINAL_WITHDRAWN_BY_SOURCE", "record_sha256": record.record_sha256,
                            "already_recorded": existing is not None and existing.game_state_observation_id is not None})
                    continue

                if existing is not None and existing.game_state_observation_id is not None:
                    existing.currently_source_confirmed = True
                    existing.disposition = lane.CONFIRMED
                    existing.final_discovery_manifest_id = manifest_row.id
                    existing.discovery_manifest_sha256 = manifest_row.manifest_sha256
                    # The identity now agrees, so an earlier reconciliation note is spent.
                    existing.reconciliation_required = False
                    existing.reconciliation_reason = None
                    summary["observations_reused"] += 1
                    touched.add(game.id)
                    continue

                # Either the first time this record is seen, or a revision that was
                # retained WITHOUT a final (withdrawn, or unreconciled) and that the
                # source now confirms again. Both need a state observation written.
                state = GameStateObservation(
                    game_id=game.id, home_team_id=sides.get(True), away_team_id=sides.get(False),
                    scheduled_at=game.starts_at, status=GameStatus.FINAL.value,
                    home_score=record.home_score, away_score=record.away_score,
                    # No completion clock exists in this lane; admission is an OBSERVATION time.
                    final_at=None, observed_at=record.admitted_at or moment, available_as_of=moment,
                    source=EXCHANGE_SOURCE, source_mode=common.SOURCE_MODE, source_record_id=espn,
                    payload_checksum=record.record_sha256)
                session.add(state)
                session.flush()
                if existing is not None:
                    # One row per revision: the retained row is re-confirmed in place, never
                    # duplicated, and now carries the final the source confirms again.
                    row = existing
                    row.currently_source_confirmed = True
                    row.disposition = lane.CONFIRMED
                    row.final_discovery_manifest_id = manifest_row.id
                    row.discovery_manifest_sha256 = manifest_row.manifest_sha256
                    row.reconciliation_required = False
                    row.reconciliation_reason = None
                    row.game_state_observation_id = state.id
                else:
                    row = _retained_row(record, game_id=game.id, manifest=manifest_row, disposition=lane.CONFIRMED,
                                        reconciliation=None, confirmed=True)
                    row.game_state_observation_id = state.id
                    session.add(row)
                session.flush()
                row.evidence_row = {**(row.evidence_row or {}), "event_clock_reconciliation": clock.receipt()}
                existing_rows[record.record_sha256] = row
                summary["observations_written"] += 1
                touched.add(game.id)
        except (ValueError, TypeError, KeyError, IntegrityError, DataError) as exc:
            # The SAVEPOINT rolls back this event, not valid siblings. Connection/
            # operational failures still propagate; they are never called a partial success.
            summary.clear()
            summary.update(before_summary)
            existing_rows = before_existing
            touched = before_touched
            summary["event_failures"].append({"stage": "record", "source_game_id": record.source_game_id,
                "record_sha256": record.record_sha256, "exception_type": type(exc).__name__,
                "reason": "EVENT_VALIDATION_OR_CONSTRAINT_FAILURE"})

    # Anything AISP holds from this lane that the current discovery no longer lists
    # at all stays as evidence, with its membership withdrawn and stated.
    for sha, row in existing_rows.items():
        if sha in manifest_shas or row.disposition == lane.MISSING:
            continue
        row.currently_source_confirmed = False
        row.disposition = lane.MISSING
        if row.game_state_observation_id is not None:
            row.reconciliation_required = True
            row.reconciliation_reason = "A RECORDED FINAL IS ABSENT FROM THE CURRENT DISCOVERY MANIFEST"
            summary["reconciliation_required"].append({
                "game_id": str(row.game_id), "codex_game_id": row.codex_game_id,
                "reason": "ABSENT_FROM_CURRENT_DISCOVERY", "record_sha256": sha})

    for game_id in sorted(touched, key=str):
        before_projected = summary["games_projected_final"]
        before_corrections = len(summary["corrections"])
        try:
            with session.begin_nested():
                game = session.get(Game, game_id)
                projection = _project(session, game)
                summary["games_projected_final"] += int(bool(projection.get("changed")))
                summary["corrections"].extend(correct_settled_positions(
                    session, game, observation_id=projection.get("observation_id"), now=moment))
        except (ValueError, TypeError, KeyError, IntegrityError, DataError) as exc:
            summary["games_projected_final"] = before_projected
            del summary["corrections"][before_corrections:]
            summary["event_failures"].append({"stage": "projection", "game_id": str(game_id),
                "exception_type": type(exc).__name__, "reason": "EVENT_VALIDATION_OR_CONSTRAINT_FAILURE"})

    manifest_row.outcomes_current = summary["currently_source_confirmed"]
    if summary["observations_written"] or summary["games_projected_final"] or new_manifest:
        session.add(IngestionRun(
            source=EXCHANGE_SOURCE, job_name=EXCHANGE_JOB_NAME, status=RunStatus.SUCCEEDED, started_at=moment,
            completed_at=datetime.now(UTC), records_processed=summary["observations_written"],
            schema_version=EXCHANGE_SCHEMA_VERSION,
            source_metadata={k: summary[k] for k in (
                "manifest_sha256", "records_listed", "records_verified", "records_refused",
                "currently_source_confirmed", "observations_written", "observations_reused",
                "retained_ineligible", "games_projected_final")}))
    session.flush()
    summary["status"] = "PARTIAL_EVENT_FAILURES" if summary["event_failures"] else "OK"
    return summary


def _retained_row(record: lane.FinalRecord, *, game_id: Any, manifest: Any, disposition: str,
                  reconciliation: str | None, confirmed: bool):
    """One ``nba_final_observations`` row for one source-confirmed-final revision."""

    from app.models import NbaFinalObservation
    from app.models.nba_gen1 import LANE_EXCHANGE

    return NbaFinalObservation(
        game_id=game_id, game_state_observation_id=None, lane=LANE_EXCHANGE, source=EXCHANGE_SOURCE,
        source_game_id=record.source_game_id,
        # This lane identifies teams by canonical franchise id, not by ESPN team id.
        home_espn_team_id=None, away_espn_team_id=None,
        home_points=record.home_score, away_points=record.away_score, periods=None, overtime=None,
        source_status=record.source_status, source_object_sha256=record.record_sha256,
        source_object_bytes=record.record_bytes, captured_at_utc=record.captured_at,
        completion_clock_status=common.COMPLETION_CLOCK_BASIS, payload_checksum=record.record_sha256,
        final_discovery_manifest_id=manifest.id, discovery_manifest_sha256=manifest.manifest_sha256,
        final_record_sha256=record.record_sha256, final_record_bytes=record.record_bytes,
        revision_key=record.revision_key, codex_game_id=record.codex_game_id,
        currently_source_confirmed=confirmed, disposition=disposition,
        source_validated_at_utc=record.validated_at, admitted_at_utc=record.admitted_at,
        completed_at_utc=None, reconciliation_required=bool(reconciliation),
        reconciliation_reason=reconciliation,
        evidence_row={"codex_game_id": record.codex_game_id, "revision_key": record.revision_key,
                      "home_franchise_id": record.home_franchise_id,
                      "away_franchise_id": record.away_franchise_id,
                      "home_team_code": record.home_code, "away_team_code": record.away_code,
                      "scores": {"home": record.home_score, "away": record.away_score},
                      "source_tipoff_utc": common.iso(record.tipoff), "game_date": record.game_date,
                      "season_end": record.season_end, "season_type": record.season_type,
                      "neutral_site": record.neutral_site,
                      "clocks": {"captured_at_utc": common.iso(record.captured_at),
                                 "source_validated_at_utc": common.iso(record.validated_at),
                                 "admitted_at_utc": common.iso(record.admitted_at),
                                 "completed_at_utc": None,
                                 "completion_clock_basis": common.COMPLETION_CLOCK_BASIS},
                      "predecessor_revisions": record.predecessors,
                      "record_schema": common.FINAL_RECORD_SCHEMA,
                      "consumer_authority": "EVIDENCE_ONLY_NO_SETTLEMENT_AUTHORITY"})


# ---------------------------------------------------------------------------
# Lane 2: historical reconstruction from retained season exports
# ---------------------------------------------------------------------------

def ingest_retained(session: Session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """One pass over the pinned retained exports. Historical lane. Idempotent by checksum."""

    from app.cfb.final_ingest import _project  # sport-neutral projection onto games/game_teams
    from app.cfb.settlement_corrections import correct_settled_positions
    from app.models import ExternalIdMapping, Game, GameStateObservation, GameStatus, GameTeam, IngestionRun, NbaFinalObservation, RunStatus
    from app.models.nba_gen1 import LANE_RETAINED

    moment = now or datetime.now(UTC)
    summary: dict[str, Any] = {"job": JOB_NAME, "lane": LANE_RETAINED, "source": source.SOURCE,
                               "provider_requests": 0, "held_games": 0, "finals_in_source": 0,
                               "observations_written": 0, "observations_reused": 0,
                               "games_projected_final": 0, "not_final_yet": 0,
                               "skipped": [], "corrections": [], "event_failures": []}
    objects, problems = source.load_objects(settings)
    summary["source_objects"] = [o.describe() for o in objects]
    summary["source_problems"] = problems
    if not objects:
        summary["status"] = "NO_PINNED_SOURCE_OBJECT"
        return summary
    held = held_games(session)
    summary["held_games"] = len(held)
    for espn, game_id in sorted(held.items()):
        before_summary = deepcopy(summary)
        try:
            with session.begin_nested():
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
                franchises = {row.entity_id: row.external_id for row in session.scalars(select(ExternalIdMapping).where(
                    ExternalIdMapping.entity_type == "team", ExternalIdMapping.source == common.SOURCE_FRANCHISE))}
                try:
                    clock = identity.game_clock_assessment(session, game.id, provider_start=tipoff,
                        home_franchise_id=franchises.get(sides.get(True)), away_franchise_id=franchises.get(sides.get(False)))
                except identity.IdentityRefused as exc:
                    summary["skipped"].append({**base, "skipped": str(exc), "source_tipoff": common.iso(tipoff),
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
                        game_id=game.id, game_state_observation_id=state.id, lane=LANE_RETAINED,
                        source=source.SOURCE, source_game_id=espn,
                        home_espn_team_id=facts["homeEspnTeamId"], away_espn_team_id=facts["awayEspnTeamId"],
                        home_points=facts["homePoints"], away_points=facts["awayPoints"], periods=facts["periods"],
                        overtime=facts["overtime"], source_status=str(row.get("status_type_name")),
                        source_object_sha256=obj.sha256, source_object_bytes=obj.bytes, captured_at_utc=obj.captured_at,
                        completion_clock_status=source.COMPLETION_CLOCK_ABSENT, payload_checksum=checksum,
                        currently_source_confirmed=False, disposition="RETAINED_EXPORT_ROW",
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
        except (ValueError, TypeError, KeyError, IntegrityError, DataError) as exc:
            summary.clear()
            summary.update(before_summary)
            summary["event_failures"].append({"stage": "retained_event", "source_game_id": espn,
                "game_id": str(game_id), "exception_type": type(exc).__name__,
                "reason": "EVENT_VALIDATION_OR_CONSTRAINT_FAILURE"})

    if summary["observations_written"] or summary["games_projected_final"]:
        session.add(IngestionRun(
            source=source.SOURCE, job_name=JOB_NAME, status=RunStatus.SUCCEEDED, started_at=moment,
            completed_at=datetime.now(UTC), records_processed=summary["observations_written"],
            schema_version="AISP_NBA_RETAINED_ESPN_FINALS_V1",
            source_metadata={k: summary[k] for k in ("held_games", "finals_in_source", "observations_written",
                                                     "observations_reused", "games_projected_final")}))
    session.flush()
    summary["status"] = "PARTIAL_EVENT_FAILURES" if summary["event_failures"] else "OK"
    return summary


def ingest(session: Session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Both lanes, reported separately and never merged into one count."""

    moment = now or datetime.now(UTC)
    exchange = ingest_exchange(session, settings, now=moment)
    retained = ingest_retained(session, settings, now=moment)
    return {"job": JOB_NAME, "provider_requests": 0,
            "production_lane": exchange, "historical_lane": retained,
            "observations_written": exchange["observations_written"] + retained["observations_written"],
            "games_projected_final": exchange["games_projected_final"] + retained["games_projected_final"],
            "reconciliation_required": exchange["reconciliation_required"],
            "event_failures": exchange.get("event_failures", []) + retained.get("event_failures", []),
            "status": f"{exchange['status']}/{retained['status']}"}


__all__ = ["EXCHANGE_JOB_NAME", "EXCHANGE_SOURCE", "JOB_NAME", "held_games", "ingest",
           "ingest_exchange", "ingest_retained"]
