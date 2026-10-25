"""NHL Gen1 forecast and result importer. Files only: no network, no provider, no scheduler.

WHAT EACH OBJECT CAN BECOME (one receipt per object, always)
------------------------------------------------------------
``IMPORTED``   a valid forecast, the first AISP holds for that game.
``REVISED``    a valid forecast with a NEW identity for a game AISP already has a
               forecast for. Kept beside the earlier ones; the current forecast
               is the latest by publication clock. Nothing is overwritten.
``DUPLICATE``  the identity is already held and the content re-hashes to it:
               an idempotent re-delivery. Nothing written but the receipt.
``REJECTED``   any contract problem, or a payload under an identity already
               held whose content does NOT re-hash to it
               (``CONFLICTING_PAYLOAD_FOR_IDENTITY``). Nothing written but the receipt.

Results follow the same rules keyed by ``resultSha256``; a correction that names
``supersedesResultSha256`` is stored as a new row and the old row is kept.

GAME BINDING
------------
The forecast's ``canonicalGameId`` (``nhl:game:<NHL gameId>``) is bound to an
AISP ``games`` row only through ``external_id_mappings`` (source
``NHL_STATS_GAME``). Team orientation is then proved against the game's
``NHL_STATS_TEAM`` bindings. A game AISP does not hold is imported with
``game_id`` null and the receipt says ``UNBOUND_GAME``; it is never matched on
date or team name.

RESULTS RECONCILIATION
----------------------
A valid result for a bound game sets ``games.status = final`` and the official
final score. Regulation result, the OT/SO indicator and the final stay in
``nhl_game_results`` as separate fields. No settlement runs: NHL has no
positions, and the NFL tie/push logic is never consulted.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import ExternalIdMapping, Game, GameStatus, GameTeam
from app.models.nhl_research import (
    NHL_GAME_SOURCE,
    NHL_TEAM_SOURCE,
    NhlGameResult,
    NhlImportReceipt,
    NhlResearchForecast,
)
from app.nhl import contract as c


@dataclass
class ImportSummary:
    run_id: str
    counts: dict[str, int] = field(default_factory=lambda: {
        "IMPORTED": 0, "REVISED": 0, "DUPLICATE": 0, "REJECTED": 0})
    result_counts: dict[str, int] = field(default_factory=lambda: {
        "IMPORTED": 0, "DUPLICATE": 0, "REJECTED": 0})
    schedule_counts: dict[str, int] = field(default_factory=lambda: {
        "IMPORTED": 0, "UPDATED": 0, "UNCHANGED": 0, "REJECTED": 0})
    receipts: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "schedule": self.schedule_counts, "forecasts": self.counts,
                "results": self.result_counts,
                "receipts": self.receipts, "problems": self.problems}


def _now() -> datetime:
    return datetime.now(UTC)


def _receipt(session: Session, summary: ImportSummary, *, kind: str, disposition: str, identity: str | None,
             object_sha256: str | None, manifest_sha256: str | None, game_ref: str | None, bound: bool,
             problems: list[str], source_label: str | None, now: datetime) -> None:
    session.add(NhlImportReceipt(
        run_id=summary.run_id, kind=kind, disposition=disposition, identity=identity,
        object_sha256=object_sha256, manifest_sha256=manifest_sha256, canonical_game_ref=game_ref,
        game_bound=bound, problems=problems, source_label=(source_label or "")[:300] or None, received_at=now))
    bucket = {"FORECAST": summary.counts, "RESULT": summary.result_counts,
              "SCHEDULE": summary.schedule_counts}[kind]
    bucket[disposition] = bucket.get(disposition, 0) + 1
    summary.receipts.append({"kind": kind, "disposition": disposition, "identity": identity,
                             "canonical_game_ref": game_ref, "game_bound": bound, "problems": problems,
                             "source": source_label})


# ------------------------------------------------------------------ binding --
def _mapped(session: Session, entity_type: str, source: str, external_id: str) -> uuid.UUID | None:
    return session.scalar(select(ExternalIdMapping.entity_id).where(
        ExternalIdMapping.entity_type == entity_type, ExternalIdMapping.source == source,
        ExternalIdMapping.external_id == external_id))


def bind_game(session: Session, canonical_game_ref: str, home_ref: str, away_ref: str) -> tuple[uuid.UUID | None, list[str]]:
    """The AISP game for ``canonical_game_ref`` and any orientation problems."""

    # external ids are stored namespaced (nhl:game:<id>, nhl:team:<id>) so they can
    # never equal another provider's bare numeric id.
    game_id = _mapped(session, "game", NHL_GAME_SOURCE, canonical_game_ref)
    if game_id is None:
        return None, []
    problems: list[str] = []
    sides = {row.is_home: row.team_id for row in session.scalars(select(GameTeam).where(GameTeam.game_id == game_id))}
    for is_home, ref in ((True, home_ref), (False, away_ref)):
        team_uuid = _mapped(session, "team", NHL_TEAM_SOURCE, ref)
        if team_uuid is None or sides.get(is_home) != team_uuid:
            problems.append(f"ORIENTATION_DIFFERS_FROM_AISP_GAME:{'home' if is_home else 'away'}")
    return game_id, problems


# ----------------------------------------------------------------- forecast --
RECORD_CLASSES = ("FORECAST", "PREVIEW_FIXTURE", "SYNTHETIC_FIXTURE")


def _evidence_status(ref: dict[str, Any], package_root: pathlib.Path | None) -> str:
    """VERIFIED when the producer evidence resolves inside the package and matches; else NOT_AVAILABLE.

    The receipt is private to the producer; a consumer that cannot see it records
    that fact rather than pretending to have checked it. A resolvable file that
    does NOT match is a refusal (``PRODUCER_EVIDENCE_MISMATCH``).
    """

    if package_root is None:
        return "NOT_AVAILABLE_TO_CONSUMER"
    rel = pathlib.PurePosixPath(str(ref.get("path") or ""))
    if not str(rel) or rel.is_absolute() or ".." in rel.parts:
        return "NOT_AVAILABLE_TO_CONSUMER"
    path = pathlib.Path(package_root) / rel
    if not path.is_file():
        return "NOT_AVAILABLE_TO_CONSUMER"
    raw = path.read_bytes()
    if len(raw) == ref.get("bytes") and c.sha256_hex(raw) == str(ref.get("sha256")).lower():
        return "VERIFIED"
    return "MISMATCH"


def revision_disposition(session: Session, canonical_game_ref: str, model_id: str) -> str:
    """REVISED when AISP already holds a forecast of this model for this game, else IMPORTED."""

    prior = session.scalar(select(NhlResearchForecast.id).where(
        NhlResearchForecast.canonical_game_ref == canonical_game_ref,
        NhlResearchForecast.model_id == model_id).limit(1))
    return "REVISED" if prior is not None else "IMPORTED"


def forecast_row(document: dict[str, Any], *, object_sha256: str, object_bytes: int, manifest_sha256: str | None,
                 game_id: uuid.UUID | None, record_class: str, producer_evidence_status: str,
                 producer_evidence_ref: dict[str, Any], published_utc: datetime | None,
                 publication_clock_source: str, available_as_of: datetime,
                 imported_at: datetime) -> NhlResearchForecast:
    """The ``nhl_research_forecasts`` row for one VALIDATED forecast document. Adds nothing to a session.

    Every stored field is read from the document except AISP's own facts: the
    object identity, the bound game, the record class, the evidence status and
    reference, and the publication/availability/import clocks. Shared by the
    exchange importer and AISP's in-process generator so the two can never
    diverge on how a document becomes a row.
    """

    game, model, provenance = document["game"], document["model"], document["provenance"]
    probs, goals = document["probabilities"], document["goals"]
    reg = probs.get("regulation") or {}
    ot = probs.get("overtime") or {}
    reg_goals = goals.get("regulation") or {}
    final_goals = goals.get("final") or {}
    return NhlResearchForecast(
        forecast_id=document["forecastId"], object_sha256=object_sha256, object_bytes=object_bytes,
        manifest_sha256=manifest_sha256,
        forecast_schema=document["schema"], supersedes_forecast_id=document.get("supersedesForecastId"),
        model_id=model["modelId"], model_version=model["modelVersion"],
        configuration_sha256=model["configurationSha256"],
        canonical_game_ref=game["canonicalGameId"], source_game_id=game["sourceGameId"], game_id=game_id,
        season=game["season"], game_type=game["gameType"], scheduled_start_utc=c.parse_utc(game["scheduledStartUtc"]),
        home_canonical_team_ref=game["home"]["canonicalTeamId"], away_canonical_team_ref=game["away"]["canonicalTeamId"],
        regulation_home_probability=reg.get("home"), regulation_tie_probability=reg.get("tie"),
        regulation_away_probability=reg.get("away"), final_home_probability=probs["final"]["home"],
        final_away_probability=probs["final"]["away"], overtime_probability=ot.get("pOvertime"),
        shootout_probability=ot.get("pShootout"),
        regulation_expected_home_goals=reg_goals.get("expectedHome"),
        regulation_expected_away_goals=reg_goals.get("expectedAway"),
        final_expected_home_goals=final_goals.get("expectedHome"), final_expected_away_goals=final_goals.get("expectedAway"),
        home_goalie_status=document["goalies"]["home"]["status"], away_goalie_status=document["goalies"]["away"]["status"],
        goalies=document["goalies"], missing_inputs=document["missingInputs"],
        evidence_mode=provenance["evidenceMode"], goalie_feature_mode=provenance["goalieFeatureMode"],
        producer_evidence_ref=producer_evidence_ref, producer_evidence_status=producer_evidence_status,
        record_class=record_class, document=document,
        source_cutoff_utc=c.parse_utc(document["sourceCutoffUtc"]),
        source_retrieved_utc=c.parse_utc(document["sourceRetrievedAtUtc"]),
        forecast_created_utc=c.parse_utc(document["createdAtUtc"]),
        published_utc=published_utc, publication_clock_source=publication_clock_source,
        available_as_of=available_as_of, imported_at=imported_at, research_only=True,
    )


def import_forecast_bytes(session: Session, raw: bytes, *, summary: ImportSummary, object_sha256: str | None = None,
                          manifest_sha256: str | None = None, receipt_committed_at: datetime | None = None,
                          source_label: str | None = None, now: datetime | None = None,
                          admit_unbound_games: bool = False, record_class: str = "FORECAST",
                          package_root: pathlib.Path | None = None) -> str:
    """Import one forecast object. Returns its disposition; always writes a receipt."""

    if record_class not in RECORD_CLASSES:
        raise ValueError(f"unknown record class {record_class!r}")
    now = now or _now()
    object_sha256 = object_sha256 or c.sha256_hex(raw)

    def refuse(identity: str | None, game_ref: str | None, problems: list[str], bound: bool = False) -> str:
        _receipt(session, summary, kind="FORECAST", disposition="REJECTED", identity=identity,
                 object_sha256=object_sha256, manifest_sha256=manifest_sha256, game_ref=game_ref, bound=bound,
                 problems=problems, source_label=source_label, now=now)
        return "REJECTED"

    try:
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        return refuse(None, None, [f"SCHEMA_INVALID:not_json:{type(exc).__name__}"])
    if not isinstance(document, dict):
        return refuse(None, None, ["SCHEMA_INVALID:forecast_NOT_OBJECT"])
    declared = document.get("forecastId")
    game = document.get("game")
    game_ref = game.get("canonicalGameId") if isinstance(game, dict) else None

    held = session.scalar(select(NhlResearchForecast).where(NhlResearchForecast.forecast_id == declared)) \
        if isinstance(declared, str) else None
    if held is not None:
        recomputed = c.forecast_identity(document)
        if recomputed == declared:
            notes = []
            if document.get("publishedAtUtc") != (held.document or {}).get("publishedAtUtc"):
                # Same semantic identity, new publication metadata: the original
                # exposure/import clocks are kept; nothing is rewritten.
                notes.append("REPUBLISHED_SAME_IDENTITY_ORIGINAL_CLOCKS_PRESERVED")
            _receipt(session, summary, kind="FORECAST", disposition="DUPLICATE", identity=declared,
                     object_sha256=object_sha256, manifest_sha256=manifest_sha256, game_ref=game_ref,
                     bound=held.game_id is not None, problems=notes, source_label=source_label, now=now)
            return "DUPLICATE"
        return refuse(declared, game_ref, ["CONFLICTING_PAYLOAD_FOR_IDENTITY",
                                           f"held_object_sha256={held.object_sha256}", f"recomputed={recomputed}"],
                      bound=held.game_id is not None)

    synthetic = c.SYNTHETIC_MARKER in (document.get("limitations") or [])
    if synthetic and record_class != "SYNTHETIC_FIXTURE":
        return refuse(declared, game_ref, ["SYNTHETIC_NOT_ADMISSIBLE_AS_FORECAST"])
    if record_class == "SYNTHETIC_FIXTURE" and not synthetic:
        return refuse(declared, game_ref, ["SYNTHETIC_LANE_REQUIRES_SYNTHETIC_MARKER"])
    problems = c.validate_forecast(document, receipt_committed_at=receipt_committed_at,
                                   allow_unpublished=record_class == "SYNTHETIC_FIXTURE")
    if problems:
        return refuse(declared, game_ref, problems)
    evidence_status = _evidence_status(document["provenance"]["producerEvidenceRef"], package_root)
    if evidence_status == "MISMATCH":
        return refuse(declared, game_ref, ["PRODUCER_EVIDENCE_MISMATCH"])

    game_id, orientation = bind_game(session, game["canonicalGameId"], game["home"]["canonicalTeamId"],
                                     game["away"]["canonicalTeamId"])
    if orientation:
        return refuse(declared, game_ref, orientation)
    notes: list[str] = []
    if game_id is None and admit_unbound_games:
        from app.nhl.identity import ensure_game

        try:
            game_id = ensure_game(session, game,
                                  source_mode="SYNTHETIC" if record_class == "SYNTHETIC_FIXTURE" else "RESEARCH_SHADOW")
        except ValueError as exc:
            return refuse(declared, game_ref, [str(exc)])
        notes.append("GAME_ADMITTED_FROM_FORECAST_IDENTITY")
    if game_id is None:
        notes.append("UNBOUND_GAME")
    else:
        aisp_game = session.get(Game, game_id)
        start = c.parse_utc(game["scheduledStartUtc"])
        held_start = aisp_game.starts_at if aisp_game else None
        if held_start is not None and held_start.tzinfo is None:
            held_start = held_start.replace(tzinfo=UTC)
        if held_start is not None and start is not None and held_start != start:
            notes.append(f"SCHEDULED_START_DIFFERS_FROM_AISP_GAME:{held_start.isoformat()}")
    if evidence_status != "VERIFIED":
        notes.append(f"PRODUCER_EVIDENCE_{evidence_status}")
    if record_class != "FORECAST":
        notes.append(f"RECORD_CLASS_{record_class}")

    model = document["model"]
    # A revision is a new identity for the same game AND the same model. A second
    # model for the same game is a separate forecast, not a revision of the first.
    disposition = revision_disposition(session, game["canonicalGameId"], model["modelId"])
    published_raw = document.get("publishedAtUtc")
    published = c.parse_utc(published_raw) if published_raw is not None else receipt_committed_at
    clock_source = ("FORECAST_PUBLISHED_AT" if published_raw is not None else
                    "MANIFEST_RECEIPT_COMMITTED_AT" if receipt_committed_at is not None else
                    "UNPUBLISHED_SYNTHETIC_FIXTURE")
    session.add(forecast_row(
        document, object_sha256=object_sha256, object_bytes=len(raw), manifest_sha256=manifest_sha256,
        game_id=game_id, record_class=record_class, producer_evidence_status=evidence_status,
        producer_evidence_ref=document["provenance"]["producerEvidenceRef"], published_utc=published,
        publication_clock_source=clock_source, available_as_of=now, imported_at=now))
    session.flush()
    _receipt(session, summary, kind="FORECAST", disposition=disposition, identity=declared,
             object_sha256=object_sha256, manifest_sha256=manifest_sha256, game_ref=game["canonicalGameId"],
             bound=game_id is not None, problems=notes, source_label=source_label, now=now)
    return disposition


# ------------------------------------------------------------------- result --
def apply_official_final(session: Session, game_id: uuid.UUID, final: dict[str, Any]) -> None:
    """Carry an OFFICIAL final onto the canonical game row: status FINAL and the final score.

    The canonical game row carries the OFFICIAL final only. The regulation result and
    the OT/SO indicator stay in nhl_game_results. One function, so every NHL result
    route (the producer's result manifest, the public-scoreboard lane in
    ``app.nhl.espn_results``) marks a game final exactly the same way.
    """

    aisp_game = session.get(Game, game_id)
    if aisp_game is None:
        return
    aisp_game.status = GameStatus.FINAL
    aisp_game.home_score = final["homeScore"]
    aisp_game.away_score = final["awayScore"]
    if final.get("completedAtUtc"):
        aisp_game.final_at = c.parse_utc(final["completedAtUtc"])
    for side in session.scalars(select(GameTeam).where(GameTeam.game_id == game_id)):
        side.score = final["homeScore"] if side.is_home else final["awayScore"]


def import_result_bytes(session: Session, raw: bytes, *, summary: ImportSummary, object_sha256: str | None = None,
                        manifest_sha256: str | None = None, source_label: str | None = None,
                        now: datetime | None = None) -> str:
    now = now or _now()
    object_sha256 = object_sha256 or c.sha256_hex(raw)
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        _receipt(session, summary, kind="RESULT", disposition="REJECTED", identity=None, object_sha256=object_sha256,
                 manifest_sha256=manifest_sha256, game_ref=None, bound=False, problems=["SCHEMA_INVALID:not_json"],
                 source_label=source_label, now=now)
        return "REJECTED"
    declared = document.get("resultSha256") if isinstance(document, dict) else None
    game_ref = document.get("canonicalGameId") if isinstance(document, dict) else None
    held = session.scalar(select(NhlGameResult).where(NhlGameResult.result_sha256 == declared)) \
        if isinstance(declared, str) else None
    if held is not None:
        same = c.result_identity(document) == declared
        _receipt(session, summary, kind="RESULT", disposition="DUPLICATE" if same else "REJECTED", identity=declared,
                 object_sha256=object_sha256, manifest_sha256=manifest_sha256, game_ref=game_ref,
                 bound=held.game_id is not None, problems=[] if same else ["CONFLICTING_PAYLOAD_FOR_IDENTITY"],
                 source_label=source_label, now=now)
        return "DUPLICATE" if same else "REJECTED"
    problems = c.validate_result(document)
    game_id: uuid.UUID | None = None
    notes: list[str] = []
    if not problems:
        game_id, orientation = bind_game(session, document["canonicalGameId"], document["homeCanonicalTeamId"],
                                         document["awayCanonicalTeamId"])
        problems.extend(orientation)
        if game_id is None:
            notes.append("UNBOUND_GAME")
    if problems:
        _receipt(session, summary, kind="RESULT", disposition="REJECTED", identity=declared,
                 object_sha256=object_sha256, manifest_sha256=manifest_sha256, game_ref=game_ref, bound=False,
                 problems=problems, source_label=source_label, now=now)
        return "REJECTED"
    reg, final, ot, so = document["regulation"], document["final"], document.get("overtime") or {}, document.get("shootout") or {}
    session.add(NhlGameResult(
        result_sha256=declared, object_sha256=object_sha256, supersedes_result_sha256=document.get("supersedesResultSha256"),
        canonical_game_ref=document["canonicalGameId"], game_id=game_id, game_type=document["gameType"],
        regulation_home_goals=reg["homeGoals"], regulation_away_goals=reg["awayGoals"], regulation_result=reg["result"],
        regulation_source=reg["source"], regulation_observed_utc=c.parse_utc(reg["observedAtUtc"]),
        regulation_retrieved_utc=c.parse_utc(reg["retrievedAtUtc"]), decided_in=document["decidedIn"],
        overtime_home_goals=ot.get("homeGoals") if ot.get("played") else None,
        overtime_away_goals=ot.get("awayGoals") if ot.get("played") else None,
        shootout_home_scored=so.get("homeScored"), shootout_away_scored=so.get("awayScored"),
        final_home_score=final["homeScore"], final_away_score=final["awayScore"], final_winner=final["winner"],
        final_official=final["official"], final_source=final["source"],
        final_observed_utc=c.parse_utc(final["observedAtUtc"]), final_retrieved_utc=c.parse_utc(final["retrievedAtUtc"]),
        final_completed_utc=c.parse_utc(final["completedAtUtc"]) if final.get("completedAtUtc") else None,
        document=document, available_as_of=now, imported_at=now,
    ))
    if game_id is not None and final["official"]:
        apply_official_final(session, game_id, final)
    session.flush()
    _receipt(session, summary, kind="RESULT", disposition="IMPORTED", identity=declared, object_sha256=object_sha256,
             manifest_sha256=manifest_sha256, game_ref=document["canonicalGameId"], bound=game_id is not None,
             problems=notes, source_label=source_label, now=now)
    return "IMPORTED"


# ----------------------------------------------------------------- schedule --
def import_schedule_bytes(session: Session, raw: bytes, *, summary: ImportSummary, object_sha256: str | None = None,
                          manifest_sha256: str | None = None, source_label: str | None = None,
                          now: datetime | None = None) -> dict[str, int]:
    """Import one NHL_GEN1_SCHEDULE_V1 object: one receipt per game (or one REJECTED for the object)."""

    from app.nhl import schedule as sched

    now = now or _now()
    object_sha256 = object_sha256 or c.sha256_hex(raw)
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        document = None
    identity = document.get("scheduleId") if isinstance(document, dict) else None

    def receipt(disposition: str, game_ref: str | None, notes: list[str]) -> None:
        _receipt(session, summary, kind="SCHEDULE", disposition=disposition, identity=identity,
                 object_sha256=object_sha256, manifest_sha256=manifest_sha256, game_ref=game_ref,
                 bound=disposition in ("IMPORTED", "UPDATED", "UNCHANGED"), problems=notes,
                 source_label=source_label, now=now)

    if not isinstance(document, dict):
        receipt("REJECTED", None, ["SCHEMA_INVALID:not_json_object"])
        return {"REJECTED": 1}
    return sched.import_schedule_document(session, document, receipt=receipt, now=now)


# ----------------------------------------------------------------- exchange --
class ExchangeRefused(RuntimeError):
    pass


def _bound(root: pathlib.Path, ref: dict[str, Any], label: str) -> bytes:
    try:
        rel = pathlib.PurePosixPath(str(ref["path"]))
        expected, size = str(ref["sha256"]).lower(), int(ref["bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExchangeRefused(f"{label}: binding is not {{path, sha256, bytes}}") from exc
    if rel.is_absolute() or ".." in rel.parts:
        raise ExchangeRefused(f"{label}: unsafe path {rel}")
    path = root / rel
    if not path.is_file():
        raise ExchangeRefused(f"{label}: missing {rel}")
    raw = path.read_bytes()
    if len(raw) != size or c.sha256_hex(raw) != expected:
        raise ExchangeRefused(f"OBJECT_CHECKSUM_MISMATCH:{label}")
    return raw


def import_manifest(session: Session, root: pathlib.Path, manifest_ref: dict[str, Any], *, summary: ImportSummary,
                    now: datetime | None = None, admit_unbound_games: bool = False,
                    record_class: str = "FORECAST") -> None:
    root = pathlib.Path(root)
    raw = _bound(root, manifest_ref, "manifest")
    manifest_sha = c.sha256_hex(raw)
    if pathlib.PurePosixPath(str(manifest_ref["path"])).parent.name != manifest_sha:
        raise ExchangeRefused("MANIFEST_NOT_AT_CONTENT_ADDRESS")
    manifest = json.loads(raw)
    schema = manifest.get("schema")
    from app.nhl import schedule as sched

    if schema not in (c.FORECAST_MANIFEST_SCHEMA, c.RESULT_MANIFEST_SCHEMA, sched.SCHEDULE_MANIFEST_SCHEMA):
        raise ExchangeRefused(f"MANIFEST_SCHEMA_UNKNOWN:{schema!r}")
    if manifest.get("researchStatus") != "RESEARCH_ONLY":
        raise ExchangeRefused("RESEARCH_STATUS_INVALID")
    committed = c.parse_utc((manifest.get("publicationReceipt") or {}).get("committedAtUtc"))
    seen: set[str] = set()
    for entry in manifest.get("objects") or []:
        rel = str(entry.get("relativePath") or "")
        label = f"{root.name}/{rel}"
        parts = pathlib.PurePosixPath(rel).parts
        kind = {c.RESULT_MANIFEST_SCHEMA: "RESULT", sched.SCHEDULE_MANIFEST_SCHEMA: "SCHEDULE"}.get(schema, "FORECAST")
        if rel in seen or len(parts) != 4 or parts[:2] != ("objects", "sha256") or parts[2] != str(entry.get("sha256")):
            _receipt(session, summary, kind=kind, disposition="REJECTED", identity=entry.get("forecastId"),
                     object_sha256=entry.get("sha256"), manifest_sha256=manifest_sha,
                     game_ref=entry.get("canonicalGameId"), bound=False,
                     problems=["OBJECT_PATH_UNSAFE_DUPLICATE_OR_NOT_CONTENT_ADDRESSED"], source_label=label, now=now)
            continue
        seen.add(rel)
        try:
            obj = _bound(root, {"path": rel, "sha256": entry.get("sha256"), "bytes": entry.get("bytes")}, rel)
        except ExchangeRefused as exc:
            _receipt(session, summary, kind=kind, disposition="REJECTED", identity=entry.get("forecastId"),
                     object_sha256=entry.get("sha256"), manifest_sha256=manifest_sha,
                     game_ref=entry.get("canonicalGameId"), bound=False, problems=[str(exc)], source_label=label, now=now)
            continue
        if kind == "SCHEDULE":
            import_schedule_bytes(session, obj, summary=summary, object_sha256=entry["sha256"],
                                  manifest_sha256=manifest_sha, source_label=label, now=now)
        elif kind == "FORECAST":
            import_forecast_bytes(session, obj, summary=summary, object_sha256=entry["sha256"],
                                  manifest_sha256=manifest_sha, receipt_committed_at=committed, source_label=label, now=now,
                                  admit_unbound_games=admit_unbound_games, record_class=record_class,
                                  package_root=root)
        else:
            import_result_bytes(session, obj, summary=summary, object_sha256=entry["sha256"],
                                manifest_sha256=manifest_sha, source_label=label, now=now)


def import_exchange(session: Session, root: pathlib.Path, *, now: datetime | None = None,
                    run_id: str | None = None, admit_unbound_games: bool = False,
                    record_class: str = "FORECAST") -> ImportSummary:
    """Import what ``<root>/current.json`` points at. Commits nothing; the caller decides."""

    summary = ImportSummary(run_id=run_id or uuid.uuid4().hex)
    root = pathlib.Path(root)
    pointer_path = root / "current.json"
    if not pointer_path.is_file():
        summary.problems.append(f"NO_POINTER:{pointer_path}")
        return summary
    pointer = json.loads(pointer_path.read_bytes())
    if pointer.get("schema") != c.POINTER_SCHEMA:
        summary.problems.append(f"POINTER_SCHEMA_UNKNOWN:{pointer.get('schema')!r}")
        return summary
    # Schedule first: games then exist before a forecast or result binds to them.
    for key in ("scheduleManifest", "forecastManifest", "resultManifest"):
        ref = pointer.get(key)
        if not ref:
            continue
        try:
            import_manifest(session, root, ref, summary=summary, now=now, admit_unbound_games=admit_unbound_games,
                            record_class=record_class)
        except (ExchangeRefused, ValueError) as exc:
            summary.problems.append(f"{key}:{exc}")
    return summary


def import_directory(session: Session, root: pathlib.Path, *, now: datetime | None = None,
                     run_id: str | None = None, admit_unbound_games: bool = False,
                     record_class: str = "FORECAST") -> ImportSummary:
    """Import every manifest under ``<root>/manifests/sha256/*/manifest.json`` (oldest first)."""

    summary = ImportSummary(run_id=run_id or uuid.uuid4().hex)
    root = pathlib.Path(root)
    manifests = []
    for path in (root / "manifests" / "sha256").glob("*/manifest.json"):
        raw = path.read_bytes()
        try:
            created = json.loads(raw).get("manifestCreatedAtUtc") or ""
        except ValueError:
            created = ""
        manifests.append((created, path, raw))
    for _, path, raw in sorted(manifests, key=lambda m: (m[0], str(m[1]))):
        ref = {"path": path.relative_to(root).as_posix(), "sha256": c.sha256_hex(raw), "bytes": len(raw)}
        try:
            import_manifest(session, root, ref, summary=summary, now=now, admit_unbound_games=admit_unbound_games,
                            record_class=record_class)
        except (ExchangeRefused, ValueError) as exc:
            summary.problems.append(f"{ref['path']}:{exc}")
    return summary


__all__ = ["ExchangeRefused", "ImportSummary", "apply_official_final", "bind_game", "forecast_row",
           "import_directory", "import_exchange", "import_forecast_bytes", "import_manifest", "import_result_bytes",
           "import_schedule_bytes", "revision_disposition"]
