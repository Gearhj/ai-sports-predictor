"""The CFB production discovery consumer: verify, retain, import, bind.

WHAT RUNS IT
------------
The existing exchange consumer (``consume-model-exchange-research-artifacts``)
calls ``consume_discovery`` before it reads research supplements, so a CFB
paper-eligibility record can bind a forecast imported on the same pass. The CFB
paper checkpoint calls it too, immediately before deciding, so a decision reads
the newest manifest rather than one up to fifteen minutes old. One function,
no second schedule.

THREE THINGS KEPT APART
-----------------------
* Forecast history: every verified package becomes one immutable
  ``cfb_research_forecasts`` row, keyed on the producer's forecast id. It is
  never deleted when its lease lapses or its game starts.
* Current display: the newest verified revision per game, by the designated
  publication clock (the discovery receipt's ``committedAtUtc``).
* Present decision eligibility: ``present_eligibility`` -- the NEWEST verified
  manifest must list the exact forecast ``currentlyEligible`` with an unexpired
  lease, and the game must not have started. AISP never extends a lease.

WHAT IT REFUSES
---------------
A pointer or manifest that does not verify from bytes; a manifest from a
release lineage the pinned handoff does not name; a package whose orientation
disagrees with the independent raw CFBD capture; different bytes under a known
forecast id; a game whose admitted kickoff disagrees with the package. Each is
reported by name and imports nothing.
"""

from __future__ import annotations

import json
import uuid
import pathlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cfb import production_discovery as pd
from app.cfb.game_binding import CfbBindingError, admit_game
from app.cfb.research_import import CfbForecastConflict, CfbImportError, import_package
from app.models import CfbDiscoveryManifest, CfbDiscoveryObservation, CfbResearchForecast

CURRENT = "CURRENT"
EXPIRED_LEASE = "EXPIRED_LEASE"
HISTORICAL = "HISTORICAL"
NOT_IN_CURRENT_DISCOVERY = "NOT_IN_CURRENT_DISCOVERY"
STARTED = "GAME_STARTED"
NO_DISCOVERY = "NO_VERIFIED_DISCOVERY"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def independent_orientation(settings: Any) -> tuple[dict[str, tuple[int, int]], dict[str, Any]]:
    """Home/away CFBD ids from raw CFBD /games captures, never the producer's input.

    Several captures may be configured (``;``-separated). A game two captures
    orient differently is dropped from the table -- it then fails as
    ORIENTATION_UNCONFIRMED rather than being resolved by picking one.
    """

    table: dict[str, tuple[int, int]] = {}
    conflicts: set[str] = set()
    sources: list[dict[str, Any]] = []
    for raw_path in str(getattr(settings, "cfb_independent_schedule_path", "") or "").split(";"):
        path = pathlib.Path(raw_path.strip())
        if not raw_path.strip() or not path.exists():
            sources.append({"path": str(path), "status": "MISSING"})
            continue
        rows = pd.raw_cfbd_orientation(path)
        sources.append({"path": str(path), "status": "READ", "games": len(rows)})
        for game_id, sides in rows.items():
            if game_id in table and table[game_id] != sides:
                conflicts.add(game_id)
            table.setdefault(game_id, sides)
    for game_id in conflicts:
        table.pop(game_id, None)
    return table, {"sources": sources, "conflicts": sorted(conflicts), "games": len(table)}


def _load_manifest(pointer_path: pathlib.Path) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    pointer = json.loads(pointer_path.read_bytes())
    if pointer.get("schemaVersion") != pd.POINTER_SCHEMA:
        raise pd.DiscoveryRefused(f"pointer schema {pointer.get('schemaVersion')!r}")
    ref = pointer["manifest"]
    try:
        raw = pd._bound(ref, "discovery manifest")
    except CfbImportError as exc:
        raise pd.DiscoveryRefused(str(exc)) from exc
    if pathlib.Path(ref["path"]).parent.name.lower() != str(ref["sha256"]).lower():
        raise pd.DiscoveryRefused("manifest is not stored at its content address")
    manifest = json.loads(raw)
    if manifest.get("schemaVersion") != pd.MANIFEST_SCHEMA:
        raise pd.DiscoveryRefused(f"manifest schema {manifest.get('schemaVersion')!r}")
    return ref, manifest, raw


def _light_verify(entry: dict[str, Any], known: CfbResearchForecast, *, now: datetime) -> dict[str, Any]:
    """A package AISP already verified in full: rebind the index and the receipt only.

    The population, PMFs and clocks were proved when the package was first
    seen, and the bytes cannot have changed without the index digest -- its
    directory name -- changing too, which is checked here.
    """

    report: dict[str, Any] = {"game_id": str(entry.get("gameId")), "forecast_id": entry.get("forecastId"),
                              "package": str(entry.get("packageAddress", ""))[:16], "problems": [],
                              "light": True}
    problems: list[str] = report["problems"]
    try:
        address = str(entry["packageAddress"]).lower()
        if address != known.package_index_sha256 or str(entry["index"]["sha256"]).lower() != address:
            problems.append("PACKAGE_ADDRESS_CHANGED_UNDER_KNOWN_FORECAST")
        pd._bound(entry["index"], "package index")
        receipt = json.loads(pd._bound(entry["publicationReceipt"], "publication receipt"))
        for key in ("forecastId", "gameId", "packageAddress", "revisionKey", "committedAtUtc"):
            if str(receipt.get(key)) != str(entry.get(key)):
                problems.append(f"RECEIPT_{key.upper()}_MISMATCH")
        if str(entry["publicationReceipt"]["sha256"]).lower() != (known.discovery_receipt_sha256 or ""):
            problems.append("RECEIPT_DIFFERS_FROM_FIRST_IMPORT")
        report["published_at"] = receipt.get("committedAtUtc")
        report["orientation"] = known.orientation_check or "CONFIRMED"
    except (CfbImportError, KeyError, ValueError, OSError) as exc:
        problems.append(f"UNVERIFIABLE:{type(exc).__name__}:{exc}"[:200])
    valid_until = pd._ts(entry.get("eligibilityValidUntilUtc"))
    current = bool(entry.get("currentlyEligible")) and bool(valid_until and now < valid_until)
    report["lease"] = CURRENT if current else (EXPIRED_LEASE if entry.get("currentlyEligible") else HISTORICAL)
    report["lease_valid_until"] = valid_until.isoformat() if valid_until else None
    report["verified"] = not problems
    return report


def _predecessor_ref(manifest: dict[str, Any]) -> dict[str, Any] | None:
    pred = manifest.get("predecessor")
    return pred if isinstance(pred, dict) and pred.get("sha256") else None


def _lineage(session: Session, ref: dict[str, Any], manifest: dict[str, Any], raw: bytes, *,
             anchor_sha: str, max_depth: int = 400) -> tuple[list[tuple[dict, dict, bytes]], str | None]:
    """Walk hash-bound predecessors back to the pinned discovery, or to one AISP accepted.

    A new producer release (a deployment successor) does not by itself make a
    manifest foreign: the discovery journal is continuous when each manifest
    names its predecessor by SHA-256 and that chain reaches the manifest the
    pinned handoff binds. A chain that ends anywhere else is refused.

    Returns the unrecorded manifests oldest-first, or an empty list with the
    refusal reason.
    """

    recorded = {sha for (sha,) in session.execute(select(CfbDiscoveryManifest.manifest_sha256)).all()}
    chain: list[tuple[dict, dict, bytes]] = [(ref, manifest, raw)]
    node = manifest
    anchored = False
    for _ in range(max_depth):
        sha = str(chain[-1][0]["sha256"]).lower()
        if sha in recorded:
            return list(reversed(chain[:-1])), None
        anchored = anchored or sha == anchor_sha
        pred = _predecessor_ref(node)
        if pred is None:
            # The anchor's own ancestors are hash-bound history: retained too,
            # so an original lease that lapsed is on record as having lapsed.
            if anchored:
                return list(reversed(chain)), None
            return [], "CHAIN_DOES_NOT_REACH_PINNED_DISCOVERY"
        try:
            pred_raw = pd._bound(pred, "predecessor manifest")
        except CfbImportError as exc:
            return [], f"PREDECESSOR_UNVERIFIED:{exc}"[:200]
        if pathlib.Path(pred["path"]).parent.name.lower() != str(pred["sha256"]).lower():
            return [], "PREDECESSOR_NOT_CONTENT_ADDRESSED"
        node = json.loads(pred_raw)
        if node.get("schemaVersion") != pd.MANIFEST_SCHEMA:
            return [], "PREDECESSOR_SCHEMA_UNKNOWN"
        chain.append((pred, node, pred_raw))
    if anchored:
        return list(reversed(chain)), None
    return [], "CHAIN_TOO_LONG"


def consume_discovery(session: Session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """One pass. Idempotent: an already-recorded manifest is not re-verified or re-imported."""

    moment = now or datetime.now(UTC)
    verdict = pd.production_status(settings)
    summary: dict[str, Any] = {"handoff": verdict.as_dict(), "checked_at": moment.isoformat(),
                               "imported": 0, "duplicate": 0, "historical_imported": 0, "rejected": 0,
                               "currently_eligible": 0, "games_admitted": 0, "rejections": [],
                               "manifests_recorded": []}
    if verdict.status != pd.ACTIVE:
        summary["status"] = f"CONSUMER_NOT_ACTIVE:{verdict.status}:{verdict.reason}"
        return summary
    anchor = str(verdict.detail.get("bound_discovery_sha256") or "").lower()
    pointer_path = pathlib.Path(settings.cfb_discovery_pointer_path)
    try:
        ref, manifest, raw = _load_manifest(pointer_path)
    except (pd.DiscoveryRefused, OSError, ValueError, KeyError) as exc:
        summary["status"] = f"DISCOVERY_REFUSED:{exc}"[:300]
        return summary
    manifest_sha = str(ref["sha256"]).lower()
    summary["manifest_sha256"] = manifest_sha
    existing = session.scalar(select(CfbDiscoveryManifest).where(
        CfbDiscoveryManifest.manifest_sha256 == manifest_sha))
    if existing is not None:
        summary["status"] = "MANIFEST_ALREADY_RECORDED"
        summary["currently_eligible"] = sum(1 for o in session.scalars(select(CfbDiscoveryObservation).where(
            CfbDiscoveryObservation.manifest_id == existing.id)) if _lease_state(o, moment) == CURRENT)
        return summary

    chain, refusal = _lineage(session, ref, manifest, raw, anchor_sha=anchor)
    if refusal:
        summary["status"] = f"MANIFEST_NOT_IN_PINNED_LINEAGE:{refusal}"
        return summary

    orientation, orientation_meta = independent_orientation(settings)
    summary["orientation"] = orientation_meta
    for node_ref, node, node_raw in chain:
        summary["manifests_recorded"].append(
            _record_manifest(session, node_ref, node, node_raw, verdict=verdict, orientation=orientation,
                             moment=moment, summary=summary))
    summary["status"] = "MANIFEST_RECORDED"
    return summary


def _record_manifest(session: Session, ref: dict[str, Any], manifest: dict[str, Any], raw: bytes, *,
                     verdict: Any, orientation: dict[str, tuple[int, int]], moment: datetime,
                     summary: dict[str, Any]) -> dict[str, Any]:
    """Verify one manifest's packages, import what is new, and retain what it said."""

    manifest_sha = str(ref["sha256"]).lower()
    release_sha = str((manifest.get("release") or {}).get("sha256", "")).lower()
    try:
        pd._bound(manifest["release"], "release")
        release_state = "VERIFIED"
    except (CfbImportError, KeyError) as exc:
        release_state = f"UNVERIFIED:{exc}"[:120]
    dispositions: dict[str, Any] = {}
    for game in manifest.get("games", []):
        key = game.get("disposition", "?")
        dispositions[key] = dispositions.get(key, 0) + 1
    pred = _predecessor_ref(manifest)
    record = CfbDiscoveryManifest(
        manifest_sha256=manifest_sha, manifest_bytes=len(raw),
        producer_created_utc=pd._ts(manifest.get("createdAtUtc")), release_sha256=release_sha or None,
        predecessor_sha256=str(pred["sha256"]).lower() if pred else None,
        handoff_sha256=verdict.detail.get("handoff_sha256"),
        orientation_source="CFBD_RAW_GAMES_CAPTURE" if orientation else "NONE_AVAILABLE",
        packages_listed=len(manifest.get("packages", [])),
        dispositions={**dispositions, "_release": release_state}, first_verified_at=moment)
    session.add(record)
    session.flush()

    verified_count = current_count = 0
    release_ok = release_state == "VERIFIED"
    for entry in manifest.get("packages", []):
        forecast_id = str(entry.get("forecastId"))
        known = session.scalar(select(CfbResearchForecast).where(CfbResearchForecast.forecast_id == forecast_id))
        if known is not None and known.origin == "PRODUCTION_DISCOVERY":
            report = _light_verify(entry, known, now=moment)
        else:
            report = pd.verify_entry(entry, now=moment, orientation=orientation)
        if not release_ok:
            report["problems"].append("MANIFEST_RELEASE_UNVERIFIED")
            report["verified"] = False
        row_id = known.id if known is not None else None
        if report["verified"] and known is None:
            try:
                result = import_package(session, pathlib.Path(entry["packageDirectory"]), now=moment, discovery={
                    "committed_at_utc": entry["committedAtUtc"],
                    "receipt_sha256": str(entry["publicationReceipt"]["sha256"]).lower(),
                    "revision_key": entry.get("revisionKey"), "manifest_sha256": manifest_sha,
                    "orientation": report.get("orientation")})
                known = session.scalar(select(CfbResearchForecast).where(
                    CfbResearchForecast.forecast_id == forecast_id))
                row_id = known.id
                if result["result"] == "IMPORTED":
                    summary["imported"] += 1
                    if report.get("lease") != CURRENT:
                        summary["historical_imported"] += 1
                else:
                    summary["duplicate"] += 1
            except (CfbForecastConflict, CfbImportError, KeyError, ValueError) as exc:
                report["verified"] = False
                report["problems"].append(f"IMPORT_REFUSED:{exc}"[:200])
        elif report["verified"]:
            summary["duplicate"] += 1
        if report["verified"] and known is not None and known.canonical_game_id is None:
            try:
                admitted = admit_game(session, known)
                known.canonical_game_id = uuid.UUID(str(admitted["game_id"]))
                if admitted["result"] == "ADMITTED":
                    summary["games_admitted"] += 1
            except CfbBindingError as exc:
                report["problems"].append(f"GAME_BINDING_REFUSED:{exc}"[:200])
                report["verified"] = False
        if not report["verified"]:
            summary["rejected"] += 1
            summary["rejections"].append({"manifest": manifest_sha[:12], "forecast_id": forecast_id,
                                          "game_id": report.get("game_id"), "problems": report["problems"][:4]})
        else:
            verified_count += 1
            if report.get("lease") == CURRENT:
                current_count += 1
        session.add(CfbDiscoveryObservation(
            manifest_id=record.id, manifest_sha256=manifest_sha, forecast_id=forecast_id,
            cfb_research_forecast_id=row_id if report["verified"] else None,
            source_game_id=str(entry.get("gameId")), package_address=str(entry.get("packageAddress", "")).lower(),
            producer_status=str(entry.get("status") or "")[:60] or None,
            currently_eligible=bool(entry.get("currentlyEligible")),
            eligibility_valid_until_utc=pd._ts(entry.get("eligibilityValidUntilUtc")),
            lease_at_check=report.get("lease") or HISTORICAL, verified=bool(report["verified"]),
            problems=report["problems"] or None, checked_at=moment))
    record.packages_verified = verified_count
    record.packages_current_at_check = current_count
    session.flush()
    summary["currently_eligible"] = current_count
    summary["verified"] = verified_count
    return {"manifest_sha256": manifest_sha, "release_sha256": release_sha, "release": release_state,
            "listed": record.packages_listed, "verified": verified_count, "current_at_check": current_count}


def _lease_state(observation: CfbDiscoveryObservation, now: datetime) -> str:
    if not observation.verified:
        return "REJECTED"
    if not observation.currently_eligible:
        return HISTORICAL
    until = _aware(observation.eligibility_valid_until_utc)
    return CURRENT if until is not None and now < until else EXPIRED_LEASE


def newest_manifest(session: Session) -> CfbDiscoveryManifest | None:
    return session.scalar(select(CfbDiscoveryManifest).order_by(
        CfbDiscoveryManifest.producer_created_utc.desc().nullslast(),
        CfbDiscoveryManifest.first_verified_at.desc()).limit(1))


def present_eligibility(session: Session, forecast: CfbResearchForecast, *, now: datetime | None = None) -> dict[str, Any]:
    """Is this exact forecast decision-eligible at ``now``? Never extends a lease."""

    moment = now or datetime.now(UTC)
    base = {"forecast_id": forecast.forecast_id, "evaluated_at": moment.isoformat()}
    kickoff = _aware(forecast.captured_kickoff_utc)
    if kickoff is not None and moment >= kickoff:
        return {**base, "state": STARTED, "eligible": False,
                "detail": "The game has started; an authentic pregame forecast gains no new authority."}
    manifest = newest_manifest(session)
    if manifest is None:
        return {**base, "state": NO_DISCOVERY, "eligible": False,
                "detail": "AISP has verified no discovery manifest."}
    observation = session.scalar(select(CfbDiscoveryObservation).where(
        CfbDiscoveryObservation.manifest_id == manifest.id,
        CfbDiscoveryObservation.forecast_id == forecast.forecast_id))
    base = {**base, "manifest_sha256": manifest.manifest_sha256,
            "manifest_created_utc": _aware(manifest.producer_created_utc).isoformat()
            if manifest.producer_created_utc else None}
    if observation is None:
        return {**base, "state": NOT_IN_CURRENT_DISCOVERY, "eligible": False,
                "detail": "The newest verified discovery manifest does not list this forecast."}
    state = _lease_state(observation, moment)
    until = _aware(observation.eligibility_valid_until_utc)
    detail = {
        CURRENT: "The newest verified manifest lists this forecast as currently eligible.",
        EXPIRED_LEASE: "The producer's eligibility lease has expired; a new owner tick must renew it.",
        HISTORICAL: "The producer lists this forecast as historical, not currently eligible.",
        "REJECTED": "AISP could not verify this package in the newest manifest.",
    }[state]
    return {**base, "state": state, "eligible": state == CURRENT, "detail": detail,
            "lease_valid_until": until.isoformat() if until else None,
            "producer_status": observation.producer_status}


def current_forecast_for_game(session: Session, source_game_id: str) -> CfbResearchForecast | None:
    """The newest verified revision for a game, by its designated publication clock."""

    return session.scalar(select(CfbResearchForecast).where(
        CfbResearchForecast.source_game_id == str(source_game_id))
        .order_by(CfbResearchForecast.exchange_published_utc.desc().nullslast(),
                  CfbResearchForecast.imported_at.desc()).limit(1))


def verify_only(settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Dry verification of the current pointer, writing nothing (for acknowledgements)."""

    orientation, meta = independent_orientation(settings)
    report = pd.verify_discovery(pathlib.Path(settings.cfb_discovery_pointer_path), now=now,
                                 orientation=orientation, orientation_source="CFBD_RAW_GAMES_CAPTURE")
    report["orientation_meta"] = meta
    return report


__all__ = ["CURRENT", "EXPIRED_LEASE", "HISTORICAL", "NOT_IN_CURRENT_DISCOVERY", "STARTED",
           "consume_discovery", "current_forecast_for_game", "independent_orientation",
           "newest_manifest", "present_eligibility", "verify_only"]
