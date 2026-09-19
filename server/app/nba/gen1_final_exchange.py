"""The NBA source-confirmed finals lane, verified from bytes. THE PRODUCTION FINAL PATH.

WHAT THIS LANE IS
-----------------
``finals-discovery/current.json`` (``NBA_GEN1_FINAL_DISCOVERY_POINTER_V1``) points
at an immutable ``NBA_GEN1_FINAL_DISCOVERY_V1`` manifest. That manifest's
``outcomes`` bind ``finals/sha256/<hash>/final.json`` records -- each a
``NBA_GEN1_SOURCE_CONFIRMED_FINAL_V1`` -- by path, SHA-256 and byte count. This
is how a 2026-27 NBA final reaches AISP. The retained-season-export reader in
``app.nba.gen1_source`` remains a SEPARATE historical-reconstruction lane and is
not this path.

MEMBERSHIP, NOT DELETION
------------------------
Only ``currentlySourceConfirmed`` is current source membership. A withdrawal, a
correction or an absent game leaves its earlier record listed with membership
false, and AISP keeps that row as retained, explicitly ineligible evidence. The
producer's ``games`` inventory separately states every current source game, its
status and its exclusion or rejected-final reason.

CLOCKS
------
``completedAtUtc`` is null and ``completionClockBasis`` says
``NOT_RETAINED_BY_ADMITTED_SOURCE``. That is an explicit unknown. ``capturedAtUtc``
(acquisition), ``sourceValidatedAtUtc`` (validation) and ``admittedAtUtc``
(per-game admission) are real and separate, and NONE of them may stand in for
completion. A record that carries a completion time, or whose basis differs, is
refused rather than reinterpreted.

WHAT THE CONSUMER CAN AND CANNOT READ
-------------------------------------
Records, the manifest, the contract and the release are delivered TO the
consumer and must verify from bytes. The raw season object and the capture
receipt may stay private to the producer ("the raw season object stays private"),
so those two bindings are checked by hash equality against the manifest and read
only when they are readable here; an unreadable one is an advisory, never a
silent pass and never a reason to reject a delivered final.

NO AUTHORITY
------------
Every record says ``EVIDENCE_ONLY_NO_SETTLEMENT_AUTHORITY``. This module proves
bytes and identities. It writes nothing; ``app.nba.gen1_finals`` decides what, if
anything, AISP records, and refuses to settle anything the contract says needs
explicit reconciliation.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.nba import gen1_common as common

#: Relative to the exchange root.
POINTER_RELATIVE = "finals-discovery/current.json"

#: Producer dispositions in the manifest's ``games`` inventory.
DISPOSITION_CONFIRMED = "SOURCE_CONFIRMED_FINAL"

#: AISP dispositions on a retained row.
CONFIRMED = "SOURCE_CONFIRMED_FINAL"
WITHDRAWN = "WITHDRAWN_FROM_SOURCE_MEMBERSHIP"
SUPERSEDED = "SUPERSEDED_BY_CORRECTION"
MISSING = "ABSENT_FROM_CURRENT_DISCOVERY"
UNRECONCILED = "AWAITING_EXPLICIT_RECONCILIATION"
REFUSED = "RECORD_REFUSED"


class FinalsLaneRefused(RuntimeError):
    """The lane itself cannot be proved; ``problems`` lists every reason."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems)[:500])
        self.problems = problems


@dataclass
class FinalRecord:
    """One verified ``NBA_GEN1_SOURCE_CONFIRMED_FINAL_V1`` revision."""

    codex_game_id: str
    source_game_id: str
    revision_key: str
    record_sha256: str
    record_bytes: int
    currently_confirmed: bool
    home_franchise_id: str
    away_franchise_id: str
    home_code: str
    away_code: str
    home_score: int
    away_score: int
    tipoff: datetime | None
    game_date: str | None
    season_end: int | None
    season_type: str | None
    neutral_site: bool
    source_status: str
    captured_at: datetime | None
    validated_at: datetime | None
    admitted_at: datetime | None
    predecessors: list[str] = field(default_factory=list)
    doc: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return not self.problems

    def describe(self) -> dict[str, Any]:
        return {"codex_game_id": self.codex_game_id, "source_game_id": self.source_game_id,
                "revision_key": self.revision_key, "record_sha256": self.record_sha256,
                "currently_source_confirmed": self.currently_confirmed,
                "scores": [self.home_score, self.away_score], "problems": self.problems or None}


def _ref_sha(ref: Any) -> str:
    return str((ref or {}).get("sha256", "")).lower() if isinstance(ref, dict) else ""


def _advisory_bind(ref: Any, label: str, advisories: list[str]) -> dict[str, Any] | None:
    """Read and prove ``ref`` when the consumer can reach it; say so when it cannot.

    Used only for the two objects the producer keeps private (the raw season
    parquet and its capture receipt). Their HASHES are still compared exactly
    wherever the records and the manifest both name them.
    """

    if not isinstance(ref, dict) or not ref.get("path"):
        return None
    path = pathlib.Path(str(ref["path"]))
    if not path.is_file():
        advisories.append(f"NOT_READABLE_BY_CONSUMER:{label}")
        return None
    try:
        raw = common.bound(ref, label)
    except common.BindingRefused as exc:
        advisories.append(f"BINDING_REFUSED:{exc}"[:200])
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return {"bytes": len(raw)}


def _clock(value: Any) -> datetime | None:
    """One clock from a record, or None. A malformed clock refuses ONE final, not the lane."""

    try:
        return common.ts(value)
    except (ValueError, TypeError):
        return None


def _contract_ok(ref: Any) -> bool:
    """Prove one finals-exchange contract document from its own bytes."""

    try:
        doc = json.loads(common.bound(ref, "finals exchange contract"))
    except (common.BindingRefused, ValueError, TypeError):
        return False
    return (doc.get("schema") == common.FINAL_CONTRACT_SCHEMA
            and doc.get("recordSchema") == common.FINAL_RECORD_SCHEMA
            and doc.get("discoverySchema") == common.FINAL_DISCOVERY_SCHEMA
            and str(doc.get("lane")) == POINTER_RELATIVE)


def _verify_record(outcome: dict[str, Any], manifest: dict[str, Any], *,
                   known: dict[str, FinalRecord] | None = None) -> FinalRecord:
    problems: list[str] = []
    ref = outcome.get("record")
    sha = _ref_sha(ref)
    confirmed = bool(outcome.get("currentlySourceConfirmed"))
    cached = (known or {}).get(sha)
    if cached is not None:
        # The bytes cannot change without the SHA-256 changing, and the SHA-256 is
        # what the manifest binds. Membership is the only thing that moves -- and
        # the one check that depends on it is re-run here rather than assumed.
        record = FinalRecord(**{**cached.__dict__, "currently_confirmed": confirmed,
                                "problems": list(cached.problems)})
        if confirmed and _ref_sha(record.doc.get("source")) != _ref_sha(manifest.get("source")):
            record.problems.append("CURRENT_RECORD_NAMES_A_DIFFERENT_SOURCE_OBJECT")
        return record
    doc: dict[str, Any] = {}
    try:
        doc = json.loads(common.bound(ref, f"final record {outcome.get('gameId')}"))
    except (common.BindingRefused, ValueError, TypeError) as exc:
        problems.append(f"RECORD_UNVERIFIED:{exc}"[:200])
    game = (doc.get("game") or {}) if isinstance(doc, dict) else {}

    if doc.get("schemaVersion") != common.FINAL_RECORD_SCHEMA:
        problems.append(f"RECORD_SCHEMA_UNKNOWN:{doc.get('schemaVersion')!r}")
    if str(doc.get("gameId")) != str(outcome.get("gameId")):
        problems.append("RECORD_GAME_DIFFERS_FROM_DISCOVERY")
    if str(doc.get("revisionKey")) != str(outcome.get("revisionKey")):
        problems.append("RECORD_REVISION_DIFFERS_FROM_DISCOVERY")
    if str(game.get("game_id")) != str(doc.get("gameId")):
        problems.append("RECORD_GAME_IDENTITY_INCONSISTENT")
    if str(game.get("game_id", "")) != f"nba-espn:game:{game.get('source_game_id')}":
        problems.append("GAME_ID_DOES_NOT_NAME_ITS_ESPN_ID")
    # A record keeps the bindings it was written with. A PRESERVED older revision
    # therefore names an older capture, release and possibly an older contract
    # document, and that is correct -- the producer never rewrites it. So:
    #   * the contract is verified on its own terms (below, by the caller's cache),
    #   * the raw source object must match the manifest's for a CURRENTLY confirmed
    #     record, because that is what current source membership means,
    #   * the capture receipt and release are recorded, never required to match.
    if confirmed and _ref_sha(doc.get("source")) != _ref_sha(manifest.get("source")):
        problems.append("CURRENT_RECORD_NAMES_A_DIFFERENT_SOURCE_OBJECT")
    if not _ref_sha(doc.get("contract")):
        problems.append("RECORD_NAMES_NO_CONTRACT")
    if doc.get("status") != "FINAL" or doc.get("sourceStatus") != "STATUS_FINAL":
        problems.append(f"NOT_A_SOURCE_CONFIRMED_FINAL:{doc.get('sourceStatus')!r}")
    if doc.get("scoreConvention") != "FULL_GAME_INCLUDING_OVERTIME":
        problems.append("SCORE_CONVENTION_UNKNOWN")
    if doc.get("consumerAuthority") != "EVIDENCE_ONLY_NO_SETTLEMENT_AUTHORITY":
        problems.append("RECORD_CLAIMS_CONSUMER_AUTHORITY")
    # The explicit unknown, enforced: no clock may be promoted to completion.
    if doc.get("completedAtUtc") is not None:
        problems.append("COMPLETION_CLOCK_CLAIMED")
    if doc.get("completionClockBasis") != common.COMPLETION_CLOCK_BASIS:
        problems.append(f"COMPLETION_CLOCK_BASIS_UNKNOWN:{doc.get('completionClockBasis')!r}")

    home = away = None
    try:
        home, away = int(doc["homeScore"]), int(doc["awayScore"])
        if home < 0 or away < 0:
            problems.append("NEGATIVE_FINAL_SCORE")
        if home == away:
            problems.append("TIED_NBA_FINAL")
    except (KeyError, TypeError, ValueError):
        problems.append("FINAL_SCORE_MISSING_OR_NONINTEGRAL")

    captured = _clock(doc.get("capturedAtUtc"))
    validated = _clock(doc.get("sourceValidatedAtUtc"))
    admitted = _clock(doc.get("admittedAtUtc"))
    if captured is None or validated is None or admitted is None:
        problems.append("CAPTURE_VALIDATION_ADMISSION_CLOCK_MISSING")
    elif not captured <= validated <= admitted:
        problems.append("CLOCK_ORDER_CAPTURE_VALIDATION_ADMISSION")
    tipoff = _clock(game.get("scheduled_tipoff_utc"))
    if tipoff is None:
        problems.append("SOURCE_TIPOFF_MISSING")
    elif captured is not None and tipoff >= captured:
        problems.append("SOURCE_FINAL_BEFORE_START_CAPTURE")
    for side in ("home", "away"):
        franchise, code = str(game.get(f"{side}_team_id") or ""), str(game.get(f"{side}_team_code") or "")
        if not franchise or not code:
            problems.append(f"{side.upper()}_TEAM_IDENTITY_MISSING")
        elif not franchise.startswith("nba-neil:franchise:") or franchise.rsplit(":", 1)[-1] != code:
            # No implicit alias or remap: the franchise id must literally name its code.
            problems.append(f"{side.upper()}_FRANCHISE_ID_DOES_NOT_NAME_ITS_CODE")
    if game.get("home_team_id") == game.get("away_team_id"):
        problems.append("INVALID_ORIENTATION")

    return FinalRecord(
        codex_game_id=str(outcome.get("gameId")), source_game_id=str(game.get("source_game_id")),
        revision_key=str(outcome.get("revisionKey")), record_sha256=sha,
        record_bytes=int((ref or {}).get("bytes") or 0) if isinstance(ref, dict) else 0,
        currently_confirmed=confirmed, home_franchise_id=str(game.get("home_team_id")),
        away_franchise_id=str(game.get("away_team_id")), home_code=str(game.get("home_team_code")),
        away_code=str(game.get("away_team_code")), home_score=home if home is not None else -1,
        away_score=away if away is not None else -1, tipoff=tipoff,
        game_date=str(game.get("game_date")) if game.get("game_date") else None,
        season_end=int(game["season_end"]) if str(game.get("season_end") or "").isdigit() else None,
        season_type=str(game.get("season_type")) if game.get("season_type") else None,
        neutral_site=bool(game.get("neutral_site")), source_status=str(doc.get("sourceStatus")),
        captured_at=captured, validated_at=validated, admitted_at=admitted,
        predecessors=[_ref_sha(p) for p in (doc.get("predecessorRevisions") or [])],
        doc=doc, problems=problems)


def verify_lane(pointer_path: Any, *, handoff_sha256: str | None = None,
                known: dict[str, FinalRecord] | None = None) -> dict[str, Any]:
    """Prove the pointer, the manifest and every bound record. Writes nothing.

    ``known`` maps an already-proved record SHA-256 to its ``FinalRecord`` so a
    steady-state pass re-reads only what is new. ``handoff_sha256``, when given
    and when the bound release is a full slate release, additionally requires
    that release to name the pinned model handoff.
    """

    problems: list[str] = []
    advisories: list[str] = []
    pointer_file = pathlib.Path(str(pointer_path))
    if not pointer_file.is_file():
        raise FinalsLaneRefused([f"POINTER_MISSING:{pointer_file}"])
    pointer = json.loads(pointer_file.read_bytes())
    if pointer.get("schemaVersion") != common.FINAL_POINTER_SCHEMA:
        raise FinalsLaneRefused([f"POINTER_SCHEMA_UNKNOWN:{pointer.get('schemaVersion')!r}"])
    ref = pointer.get("manifest")
    try:
        manifest_raw = common.bound(ref, "final discovery manifest")
    except (common.BindingRefused, TypeError) as exc:
        raise FinalsLaneRefused([f"MANIFEST_UNVERIFIED:{exc}"[:200]]) from exc
    manifest_sha = _ref_sha(ref)
    if pathlib.Path(str(ref["path"])).parent.name.lower() != manifest_sha:
        problems.append("MANIFEST_NOT_AT_ITS_CONTENT_ADDRESS")
    manifest = json.loads(manifest_raw)
    if manifest.get("schemaVersion") != common.FINAL_DISCOVERY_SCHEMA:
        raise FinalsLaneRefused([f"MANIFEST_SCHEMA_UNKNOWN:{manifest.get('schemaVersion')!r}"])

    contract: dict[str, Any] = {}
    try:
        contract = json.loads(common.bound(manifest.get("contract"), "finals exchange contract"))
    except (common.BindingRefused, ValueError, TypeError) as exc:
        problems.append(f"CONTRACT_UNVERIFIED:{exc}"[:200])
    if contract:
        if contract.get("schema") != common.FINAL_CONTRACT_SCHEMA:
            problems.append(f"CONTRACT_SCHEMA_UNKNOWN:{contract.get('schema')!r}")
        if contract.get("recordSchema") != common.FINAL_RECORD_SCHEMA:
            problems.append("CONTRACT_RECORD_SCHEMA_DIFFERS")
        if contract.get("discoverySchema") != common.FINAL_DISCOVERY_SCHEMA:
            problems.append("CONTRACT_DISCOVERY_SCHEMA_DIFFERS")
        if str(contract.get("lane")) != POINTER_RELATIVE:
            problems.append(f"CONTRACT_LANE_DIFFERS:{contract.get('lane')!r}")

    release: dict[str, Any] = {}
    try:
        release = json.loads(common.bound(manifest.get("release"), "finals release"))
    except (common.BindingRefused, ValueError, TypeError) as exc:
        problems.append(f"RELEASE_UNVERIFIED:{exc}"[:200])
    if release:
        if _ref_sha(release.get("finalsExchangeContract")) != _ref_sha(manifest.get("contract")):
            problems.append("RELEASE_DOES_NOT_BIND_THIS_CONTRACT")
        if release.get("schema") == common.SLATE_RELEASE_SCHEMA and handoff_sha256:
            if _ref_sha(release.get("modelHandoff")) != handoff_sha256.lower():
                problems.append("RELEASE_DOES_NOT_BIND_PINNED_MODEL_HANDOFF")
    capture = _advisory_bind(manifest.get("captureReceipt"), "capture receipt", advisories)
    _advisory_bind(manifest.get("source"), "raw source object", advisories)
    if capture and capture.get("httpStatus") not in (200, 304, None):
        problems.append(f"CAPTURE_HTTP_STATUS_UNSUPPORTED:{capture.get('httpStatus')!r}")
    if capture and _ref_sha(capture.get("source")) and \
            _ref_sha(capture.get("source")) != _ref_sha(manifest.get("source")):
        problems.append("CAPTURE_RECEIPT_NAMES_A_DIFFERENT_SOURCE_OBJECT")
    if problems:
        raise FinalsLaneRefused(problems)

    records: list[FinalRecord] = []
    seen: set[str] = set()
    current_by_game: dict[str, int] = {}
    contracts: dict[str, bool] = {_ref_sha(manifest.get("contract")): True}
    for outcome in manifest.get("outcomes") or []:
        record = _verify_record(outcome, manifest, known=known)
        if record.record_sha256 in seen:
            record.problems.append("DUPLICATE_RECORD_IN_MANIFEST")
        seen.add(record.record_sha256)
        if record.currently_confirmed and record.verified:
            # A current record's own contract is proved, even when it is not the
            # manifest's -- never assumed from the manifest's contract.
            ref_c = record.doc.get("contract")
            sha_c = _ref_sha(ref_c)
            if sha_c not in contracts:
                contracts[sha_c] = _contract_ok(ref_c)
            if not contracts[sha_c]:
                record.problems.append("RECORD_CONTRACT_UNVERIFIED_OR_INCOMPATIBLE")
        if record.currently_confirmed and record.verified:
            current_by_game[record.codex_game_id] = current_by_game.get(record.codex_game_id, 0) + 1
        records.append(record)
    for record in records:
        if record.currently_confirmed and current_by_game.get(record.codex_game_id, 0) > 1:
            record.problems.append("MULTIPLE_CURRENT_FINALS_FOR_GAME")

    games = list(manifest.get("games") or [])
    dispositions: dict[str, int] = {}
    for row in games:
        key = str(row.get("disposition") or "?")
        dispositions[key] = dispositions.get(key, 0) + 1
    return {
        "pointer_path": str(pointer_file), "manifest_sha256": manifest_sha,
        "manifest_bytes": len(manifest_raw), "manifest": manifest,
        "schema_version": manifest.get("schemaVersion"),
        "created_at_utc": _clock(manifest.get("createdAtUtc")),
        "predecessor_sha256": _ref_sha(manifest.get("predecessor")) or None,
        "contract_sha256": _ref_sha(manifest.get("contract")) or None,
        "release_sha256": _ref_sha(manifest.get("release")) or None,
        "capture_receipt_sha256": _ref_sha(manifest.get("captureReceipt")) or None,
        "source_object_sha256": _ref_sha(manifest.get("source")) or None,
        "source_object_bytes": int((manifest.get("source") or {}).get("bytes") or 0),
        "records": records, "games": games, "dispositions": dispositions,
        "advisories": advisories,
        "current_records": [r for r in records if r.currently_confirmed and r.verified],
        "semantics": manifest.get("semantics"),
    }


def pointer_path(settings: Any) -> pathlib.Path | None:
    """The configured lane pointer, or the exchange root's default location."""

    explicit = str(getattr(settings, "nba_finals_exchange_pointer", "") or "").strip()
    if explicit:
        return pathlib.Path(explicit)
    root = str(getattr(settings, "nba_exchange_root", "") or "").strip()
    return pathlib.Path(root) / POINTER_RELATIVE if root else None


__all__ = ["CONFIRMED", "DISPOSITION_CONFIRMED", "MISSING", "POINTER_RELATIVE", "REFUSED", "SUPERSEDED",
           "UNRECONCILED", "WITHDRAWN", "FinalRecord", "FinalsLaneRefused", "pointer_path", "verify_lane"]
