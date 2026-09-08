"""Verify and register the MLB V18 Play Geometry trace supplement.

WHAT THIS IS
------------
A narrow, fail-closed importer for a checksum-addressed Model Studio artifact
that adds MODELLED batted-ball geometry and explicit ANONYMOUS runner movements
beside an already-registered V18 research-shadow forecast.

WHAT IT MAY NOT DO
------------------
It executes no Model Studio code, grants no wagering authority, promotes
nothing, and cannot reach a Gen 1 forecast, decision, position, settlement or
any official history. It only appends one additive block to the shadow
forecast's own lineage.

VERIFICATION ORDER (fail closed at the first violation)
-------------------------------------------------------
1. The directory is addressed by a 64-hex SHA-256 segment.
2. ``manifest.json`` parses and declares that address as ``supplementSha256``.
3. Every artifact the manifest lists exists, and its bytes hash and length
   match exactly -- checked before any file is interpreted as data.
4. The supplement's own canonical hash equals the declared identity.
5. The handoff agrees with the manifest on supplement and parent identity.
6. Governance flags are all fail-closed.
7. The parent bundle checksum matches the registered shadow forecast, and the
   geometry path is for that forecast's own game and accepted trace.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import ShadowForecast
from app.shadow_import.mlb_v18 import ENGINE_ID

SUPPLEMENT_SCHEMA = "MLB_V18_PLAY_GEOMETRY_TRACE_V1"
HANDOFF_SCHEMA = "AISP_MLB_V18_PLAY_GEOMETRY_HANDOFF_V1"
COORDINATE_SYSTEM = "ASP_NORMALIZED_DIAMOND_V1"
DISPLAY_AUTHORITY = "RESEARCH_SHADOW_PLAY_GEOMETRY"

#: The key this importer owns inside the shadow forecast's lineage. Nothing
#: else writes it, and no existing field is repurposed to carry geometry.
LINEAGE_KEY = "playGeometry"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PlayGeometryImportError(RuntimeError):
    """The supplement is missing, altered, mismatched or unsafe to register."""


def _sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PlayGeometryImportError(f"Required file is missing: {path.name}") from error
    except json.JSONDecodeError as error:
        raise PlayGeometryImportError(f"{path.name} is not valid JSON: {error}") from error


@dataclass(frozen=True)
class VerifiedGeometry:
    """A supplement whose every artifact hash and governance flag has passed."""

    root: Path
    supplement_id: str
    supplement_sha256: str
    parent_bundle_id: str
    parent_bundle_sha256: str
    supplement: dict[str, Any]
    handoff: dict[str, Any]
    paths: list[dict[str, Any]]
    artifact_count: int

    @property
    def coordinate_system(self) -> str:
        return str(self.supplement.get("coordinateSystem", {}).get("name") or COORDINATE_SYSTEM)


def verify_supplement(root: str | Path) -> VerifiedGeometry:
    """Verify the checksum-addressed supplement, or fail closed."""

    root = Path(root)
    if not root.is_dir():
        raise PlayGeometryImportError(f"Supplement root is not a directory: {root}")

    address = root.name.lower()
    if not _SHA256.match(address):
        raise PlayGeometryImportError(
            f"Supplement root is not checksum-addressed: {root.name!r}"
        )

    manifest = _read_json(root / "manifest.json")
    declared = str(manifest.get("supplementSha256", "")).lower()
    if declared != address:
        raise PlayGeometryImportError(
            "Manifest supplementSha256 does not match the directory address"
        )

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise PlayGeometryImportError("Manifest declares no artifacts")

    # Hash every artifact before anything is interpreted as data.
    verified: set[str] = set()
    for entry in artifacts:
        relative = str(entry.get("relativePath", ""))
        expected = str(entry.get("sha256", "")).lower()
        if not relative or not _SHA256.match(expected):
            raise PlayGeometryImportError(f"Malformed artifact entry: {entry!r}")
        path = (root / relative).resolve()
        if root.resolve() not in path.parents:
            raise PlayGeometryImportError(f"Artifact escapes the supplement root: {relative}")
        if not path.is_file():
            raise PlayGeometryImportError(f"Declared artifact is missing: {relative}")
        payload = path.read_bytes()
        if _sha_bytes(payload) != expected:
            raise PlayGeometryImportError(f"Artifact checksum mismatch: {relative}")
        declared_bytes = entry.get("bytes")
        if declared_bytes is not None and len(payload) != int(declared_bytes):
            raise PlayGeometryImportError(f"Artifact byte count mismatch: {relative}")
        verified.add(relative)

    handoff = _read_json(root / "aisp-play-geometry-handoff.json")
    if handoff.get("schemaVersion") != HANDOFF_SCHEMA:
        raise PlayGeometryImportError(
            f"Unsupported handoff schema {handoff.get('schemaVersion')!r}"
        )
    for key in ("supplementFile", "pathsFile"):
        if str(handoff.get(key) or "") not in verified:
            raise PlayGeometryImportError(f"Handoff {key} names an unverified file")

    supplement = _read_json(root / str(handoff["supplementFile"]))
    if supplement.get("schemaVersion") != SUPPLEMENT_SCHEMA:
        raise PlayGeometryImportError(
            f"Unsupported supplement schema {supplement.get('schemaVersion')!r}"
        )
    # The supplement's own canonical identity must equal the address it is
    # published under, so a re-serialised or edited body is refused.
    if _canonical_sha(supplement) != address:
        raise PlayGeometryImportError(
            "The supplement body does not hash to its published identity"
        )

    _assert_governance(supplement, handoff)

    parent_sha = str(handoff.get("parentBundleSha256", "")).lower()
    if parent_sha != str(supplement.get("parentBundleSha256", "")).lower():
        raise PlayGeometryImportError("Handoff and supplement disagree on the parent bundle")
    if str(manifest.get("parentBundleSha256", "")).lower() != parent_sha:
        raise PlayGeometryImportError("Manifest and handoff disagree on the parent bundle")

    paths_document = _read_json(root / str(handoff["pathsFile"]))
    paths = paths_document.get("paths")
    if not isinstance(paths, list) or not paths:
        raise PlayGeometryImportError("The supplement carries no renderer-ready path")
    for path in paths:
        if path.get("schemaVersion") != SUPPLEMENT_SCHEMA:
            raise PlayGeometryImportError("A renderer-ready path declares a different schema")
        if not isinstance(path.get("events"), list) or not path["events"]:
            raise PlayGeometryImportError("A renderer-ready path carries no events")

    return VerifiedGeometry(
        root=root,
        supplement_id=str(handoff.get("supplementId") or ""),
        supplement_sha256=address,
        parent_bundle_id=str(handoff.get("parentBundleId") or ""),
        parent_bundle_sha256=parent_sha,
        supplement=supplement,
        handoff=handoff,
        paths=paths,
        artifact_count=len(verified),
    )


def _assert_governance(supplement: dict[str, Any], handoff: dict[str, Any]) -> None:
    """Every flag that would make the artifact unsafe to display."""

    for name, document in (("supplement", supplement), ("handoff", handoff)):
        if document.get("wageringAuthority") is not False:
            raise PlayGeometryImportError(f"{name} must declare wageringAuthority=false")
        if document.get("promotionStatus") != "NOT_PROMOTED":
            raise PlayGeometryImportError(f"{name} must declare promotionStatus=NOT_PROMOTED")
        if document.get("executionAllowedInAISP") is not False:
            raise PlayGeometryImportError(
                f"{name} must declare executionAllowedInAISP=false; AISP never "
                "executes a Model Studio artifact"
            )
        if document.get("displayAuthority") != DISPLAY_AUTHORITY:
            raise PlayGeometryImportError(
                f"{name} must declare displayAuthority={DISPLAY_AUTHORITY}"
            )
    if supplement.get("finalOotOpened") is not False:
        raise PlayGeometryImportError("The supplement must declare finalOotOpened=false")


# -- registration ---------------------------------------------------------


def register_geometry(
    session: Session,
    verified: VerifiedGeometry,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Attach the supplement to its parent shadow forecast, idempotently.

    Additive by construction: the only write is one new key inside the shadow
    forecast's own ``lineage_detail``. No Gen 1 row, decision, position or
    settlement is reachable from here, and an existing registration with the
    same checksum is re-confirmed rather than rewritten.
    """

    now = now or datetime.now(UTC)
    rows = session.scalars(
        select(ShadowForecast).where(ShadowForecast.engine_id == ENGINE_ID)
    ).all()

    targets = [
        row for row in rows
        if str((row.lineage_detail or {}).get("bundleSha256", "")).lower()
        == verified.parent_bundle_sha256
    ]
    if not targets:
        raise PlayGeometryImportError(
            "No registered V18 shadow forecast carries the supplement's parent "
            f"bundle {verified.parent_bundle_sha256[:12]}..."
        )

    registered: list[str] = []
    already: list[str] = []
    skipped: list[dict[str, str]] = []

    for row in targets:
        lineage = dict(row.lineage_detail or {})
        # The geometry path must be for this forecast's own game and its own
        # accepted trace, not merely for the same bundle.
        path = next(
            (item for item in verified.paths if str(item.get("gameId")) == str(row.event_id)),
            None,
        )
        if path is None:
            skipped.append({
                "forecastId": row.forecast_id,
                "reason": "the supplement carries no geometry path for this game",
            })
            continue
        expected_digest = lineage.get("pathDigest")
        persisted = (lineage.get("sequentialPath") or [[]])[0]
        if len(persisted) != len(path.get("events") or []):
            skipped.append({
                "forecastId": row.forecast_id,
                "reason": (
                    "the geometry path length differs from the accepted V18 trace "
                    f"({len(path.get('events') or [])} vs {len(persisted)})"
                ),
            })
            continue

        existing = lineage.get(LINEAGE_KEY)
        if existing and str(existing.get("supplementSha256")) == verified.supplement_sha256:
            already.append(row.forecast_id)
            continue

        lineage[LINEAGE_KEY] = {
            "schemaVersion": SUPPLEMENT_SCHEMA,
            "supplementId": verified.supplement_id,
            "supplementSha256": verified.supplement_sha256,
            "parentBundleId": verified.parent_bundle_id,
            "parentBundleSha256": verified.parent_bundle_sha256,
            "coordinateSystem": verified.supplement.get("coordinateSystem"),
            "registeredAt": now.isoformat(),
            "artifactsVerified": verified.artifact_count,
            "acceptedPathDigest": expected_digest,
            "geometryPathDigest": path.get("geometryDigest"),
            "geometryModelVersion": verified.supplement.get("geometryModelVersion"),
            "runnerModelVersion": verified.supplement.get("runnerModelVersion"),
            "backoffSummary": path.get("backoffSummary"),
            "limitations": verified.supplement.get("limitations"),
            "wageringAuthority": False,
            "promotionStatus": "NOT_PROMOTED",
            "executionAllowedInAISP": False,
            "displayAuthority": DISPLAY_AUTHORITY,
            # The renderer-ready events, stored beside -- never over -- the
            # accepted sequential path.
            "events": path.get("events"),
        }
        row.lineage_detail = lineage
        flag_modified(row, "lineage_detail")
        registered.append(row.forecast_id)

    session.flush()
    return {
        "supplementId": verified.supplement_id,
        "supplementSha256": verified.supplement_sha256,
        "parentBundleSha256": verified.parent_bundle_sha256,
        "artifactsVerified": verified.artifact_count,
        "registered": registered,
        "alreadyRegistered": already,
        "skipped": skipped,
        "gen1HistoryMutated": False,
        "wageringAuthority": False,
    }


def geometry_status(session: Session) -> dict[str, Any]:
    """Whether geometry is registered for each V18 shadow forecast."""

    rows = session.scalars(
        select(ShadowForecast).where(ShadowForecast.engine_id == ENGINE_ID)
        .order_by(ShadowForecast.scheduled_start_utc)
    ).all()
    return {
        "engineId": ENGINE_ID,
        "forecasts": [
            {
                "forecastId": row.forecast_id,
                "gameId": row.event_id,
                "bundleSha256": (row.lineage_detail or {}).get("bundleSha256"),
                "geometryRegistered": bool((row.lineage_detail or {}).get(LINEAGE_KEY)),
                "supplementSha256": (
                    ((row.lineage_detail or {}).get(LINEAGE_KEY) or {}).get("supplementSha256")
                ),
                "geometryEvents": len(
                    ((row.lineage_detail or {}).get(LINEAGE_KEY) or {}).get("events") or []
                ),
            }
            for row in rows
        ],
    }


def geometry_payload(session: Session, forecast_id: str) -> dict[str, Any] | None:
    """The registered geometry for one shadow forecast, for read-only display."""

    row = session.scalar(
        select(ShadowForecast).where(ShadowForecast.forecast_id == forecast_id)
    )
    if row is None or row.engine_id != ENGINE_ID:
        return None
    lineage = dict(row.lineage_detail or {})
    geometry = lineage.get(LINEAGE_KEY)
    if not geometry:
        return None

    events = geometry.get("events") or []
    # Written by the population importer; named here rather than imported so
    # the two supplements stay independent.
    population = lineage.get("populationGeometry") or {}
    return {
        "schema_version": "AISP_MLB_V18_PLAY_GEOMETRY_VIEW_V1",
        "forecast_id": row.forecast_id,
        "game_id": row.event_id,
        "engine_id": row.engine_id,
        "supplement_id": geometry.get("supplementId"),
        "supplement_sha256": geometry.get("supplementSha256"),
        "parent_bundle_id": geometry.get("parentBundleId"),
        "parent_bundle_sha256": geometry.get("parentBundleSha256"),
        "coordinate_system": geometry.get("coordinateSystem"),
        "geometry_model_version": geometry.get("geometryModelVersion"),
        "runner_model_version": geometry.get("runnerModelVersion"),
        "registered_at": geometry.get("registeredAt"),
        "backoff_summary": geometry.get("backoffSummary"),
        "limitations": geometry.get("limitations"),
        "wagering_authority": False,
        "promotion_status": "NOT_PROMOTED",
        "execution_allowed_in_aisp": False,
        "display_authority": DISPLAY_AUTHORITY,
        "plate_appearances": len(events),
        "contact_plays": sum(
            1 for event in events if (event.get("geometry") or {}).get("contactOccurred")
        ),
        "ball_traces": sum(1 for event in events if event.get("ballTrace")),
        "runner_movements": sum(len(event.get("runnerMovements") or []) for event in events),
        "events": events,
        "population": {
            # The population is a different fact from this one representative
            # path. Its aggregate geometry is a separate supplement, owned by
            # the population importer; this only reports whether it is there.
            "simulationCount": (row.uncertainty or {}).get("simulationCount"),
            "populationGeometryAvailable": bool(population),
            "populationGeometryStatus": (
                "REGISTERED" if population
                else "AWAITING_CHECKSUM_VERIFIED_MODEL_STUDIO_SUPPLEMENT"
            ),
            "populationSupplementId": population.get("supplementId"),
            "populationSupplementSha256": population.get("supplementSha256"),
        },
        "notes": (
            "One representative simulated game. Geometry is modelled from "
            "registered retrospective evidence, not measured tracking. Runner "
            "identities are anonymous."
        ),
    }
