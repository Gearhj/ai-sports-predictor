"""Verify and register the MLB V18 Population Geometry supplement.

WHAT THIS IS
------------
A narrow, fail-closed importer for a checksum-addressed Model Studio artifact
that carries DETERMINISTIC AGGREGATION over the accepted 5,000-simulation V18
population: 100 accumulation frames, compact contact and ball-path density
grids, anonymous runner-segment traffic, and six distinct representative
simulated games.

It is a sibling of :mod:`mlb_v18_play_geometry` and reuses that association:
one additive block inside the parent shadow forecast's own lineage. There is no
migration and no new persistence shape.

WHAT IT MAY NOT DO
------------------
It executes no Model Studio code, grants no wagering authority, promotes
nothing, and cannot reach a Gen 1 forecast, decision, position or settlement.

VERIFICATION ORDER (fail closed at the first violation)
-------------------------------------------------------
1. The directory is addressed by a 64-hex SHA-256 segment.
2. ``manifest.json`` parses, declares that address, and its own canonical
   identity recomputes to it.
3. Every artifact the manifest lists exists, and its bytes hash and length
   match exactly -- checked before any file is interpreted as data.
4. The execution-authority evidence decompresses and hashes to its declared
   digest.
5. The handoff and summary agree with the manifest on the supplement, the
   parent V18 bundle, the parent Play Geometry supplement and the population
   digest.
6. Governance flags are all fail-closed in every document that carries them.
7. The population is internally consistent: 100 frames covering 5,000
   simulations with a monotonic cumulative count, density grids of the declared
   shape whose totals equal the declared counts, and six distinct
   representative paths matching the summary's selectors.
8. At registration, the parent bundle AND the registered Play Geometry
   supplement must match the shadow forecast, and every representative path
   must be for that forecast's own game.
"""

from __future__ import annotations

import gzip
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
from app.shadow_import.mlb_v18_play_geometry import LINEAGE_KEY as PLAY_GEOMETRY_KEY

MANIFEST_SCHEMA = "MLB_V18_POPULATION_GEOMETRY_MANIFEST_V1"
HANDOFF_SCHEMA = "AISP_MLB_V18_POPULATION_GEOMETRY_HANDOFF_V1"
SUMMARY_SCHEMA = "MLB_V18_POPULATION_GEOMETRY_SUMMARY_V1"
LAYER_SCHEMA = "MLB_V18_POPULATION_GEOMETRY_SUPPLEMENT_V1"
FRAMES_SCHEMA = "MLB_V18_POPULATION_GEOMETRY_FRAMES_V1"
PATH_SCHEMA = "MLB_V18_PLAY_GEOMETRY_TRACE_V1"
COORDINATE_SYSTEM = "ASP_NORMALIZED_DIAMOND_V1"
DISPLAY_AUTHORITY = "RESEARCH_SHADOW_POPULATION_GEOMETRY"

#: The key this importer owns inside the shadow forecast's lineage.
LINEAGE_KEY = "populationGeometry"

#: The six selectors the artifact is required to carry, each a different game.
SELECTORS = (
    "TYPICAL", "HOME_WIN", "AWAY_WIN", "HIGH_SCORING", "LOW_SCORING", "EXTRA_INNINGS",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_SUMMARY_FILE = "population-summary.json"
_FRAMES_FILE = "population-accumulation-frames.json"
_CONTACT_FILE = "population-contact-density.json"
_PATHS_FILE = "population-path-density.json"
_RUNNERS_FILE = "population-runner-traffic.json"
_REPRESENTATIVES_FILE = "representative-paths.json"
_AUTHORITY_FILE = "execution-authority.json.gz"
_HANDOFF_FILE = "aisp-population-geometry-handoff.json"

_REQUIRED_FILES = (
    _SUMMARY_FILE, _FRAMES_FILE, _CONTACT_FILE, _PATHS_FILE,
    _RUNNERS_FILE, _REPRESENTATIVES_FILE, _AUTHORITY_FILE, _HANDOFF_FILE,
)


class PopulationImportError(RuntimeError):
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
        raise PopulationImportError(f"Required file is missing: {path.name}") from error
    except json.JSONDecodeError as error:
        raise PopulationImportError(f"{path.name} is not valid JSON: {error}") from error


@dataclass(frozen=True)
class VerifiedPopulation:
    """A supplement whose every hash, count and governance flag has passed."""

    root: Path
    supplement_id: str
    supplement_sha256: str
    parent_bundle_id: str
    parent_bundle_sha256: str
    play_geometry_sha256: str
    population_digest: str
    execution_authority_sha256: str
    manifest: dict[str, Any]
    handoff: dict[str, Any]
    summary: dict[str, Any]
    frames: list[dict[str, Any]]
    contact_density: dict[str, Any]
    path_density: dict[str, Any]
    runner_traffic: dict[str, Any]
    representatives: dict[str, Any]
    artifact_count: int

    @property
    def simulation_count(self) -> int:
        return int(self.summary["simulationCount"])


def verify_population(root: str | Path) -> VerifiedPopulation:
    """Verify the checksum-addressed population supplement, or fail closed."""

    root = Path(root)
    if not root.is_dir():
        raise PopulationImportError(f"Supplement root is not a directory: {root}")

    address = root.name.lower()
    if not _SHA256.match(address):
        raise PopulationImportError(
            f"Supplement root is not checksum-addressed: {root.name!r}"
        )

    manifest = _read_json(root / "manifest.json")
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA:
        raise PopulationImportError(
            f"Unsupported manifest schema {manifest.get('schemaVersion')!r}"
        )
    if str(manifest.get("supplementSha256", "")).lower() != address:
        raise PopulationImportError(
            "Manifest supplementSha256 does not match the directory address"
        )
    # The manifest's own canonical identity, computed over everything but the
    # two self-referential fields, must reproduce the published address.
    identity_body = {
        key: value for key, value in manifest.items()
        if key not in {"supplementSha256", "supplementId"}
    }
    if _canonical_sha(identity_body) != address:
        raise PopulationImportError(
            "The manifest body does not hash to its published identity"
        )

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise PopulationImportError("Manifest declares no artifacts")

    # Hash every artifact before anything is interpreted as data.
    verified: set[str] = set()
    for entry in artifacts:
        relative = str(entry.get("relativePath", ""))
        expected = str(entry.get("sha256", "")).lower()
        if not relative or not _SHA256.match(expected):
            raise PopulationImportError(f"Malformed artifact entry: {entry!r}")
        path = (root / relative).resolve()
        if root.resolve() not in path.parents:
            raise PopulationImportError(f"Artifact escapes the supplement root: {relative}")
        if not path.is_file():
            raise PopulationImportError(f"Declared artifact is missing: {relative}")
        payload = path.read_bytes()
        if _sha_bytes(payload) != expected:
            raise PopulationImportError(f"Artifact checksum mismatch: {relative}")
        declared_bytes = entry.get("bytes")
        if declared_bytes is not None and len(payload) != int(declared_bytes):
            raise PopulationImportError(f"Artifact byte count mismatch: {relative}")
        verified.add(relative)

    missing = [name for name in _REQUIRED_FILES if name not in verified]
    if missing:
        raise PopulationImportError(
            "The manifest does not cover every required object: " + ", ".join(missing)
        )

    handoff = _read_json(root / _HANDOFF_FILE)
    if handoff.get("schemaVersion") != HANDOFF_SCHEMA:
        raise PopulationImportError(
            f"Unsupported handoff schema {handoff.get('schemaVersion')!r}"
        )

    summary = _read_json(root / _SUMMARY_FILE)
    if summary.get("schemaVersion") != SUMMARY_SCHEMA:
        raise PopulationImportError(
            f"Unsupported summary schema {summary.get('schemaVersion')!r}"
        )

    _assert_governance(summary, handoff)
    parent_bundle = _assert_identity(address, manifest, handoff, summary)
    play_geometry_sha = str(handoff.get("playGeometrySupplementSha256", "")).lower()
    authority_sha = _verify_execution_authority(root, manifest, parent_bundle)

    frames = _verify_frames(_read_json(root / _FRAMES_FILE), summary)
    contact = _verify_contact_density(_read_json(root / _CONTACT_FILE), summary)
    paths = _verify_path_density(_read_json(root / _PATHS_FILE))
    runners = _verify_runner_traffic(_read_json(root / _RUNNERS_FILE), summary)
    representatives = _verify_representatives(_read_json(root / _REPRESENTATIVES_FILE), summary)

    return VerifiedPopulation(
        root=root,
        supplement_id=str(manifest.get("supplementId") or ""),
        supplement_sha256=address,
        parent_bundle_id=str(manifest.get("parentBundleId") or ""),
        parent_bundle_sha256=parent_bundle,
        play_geometry_sha256=play_geometry_sha,
        population_digest=str(summary.get("populationDigest") or ""),
        execution_authority_sha256=authority_sha,
        manifest=manifest,
        handoff=handoff,
        summary=summary,
        frames=frames,
        contact_density=contact,
        path_density=paths,
        runner_traffic=runners,
        representatives=representatives,
        artifact_count=len(verified),
    )


def _assert_governance(summary: dict[str, Any], handoff: dict[str, Any]) -> None:
    """Every flag that would make the artifact unsafe to display."""

    governance = summary.get("governance")
    if not isinstance(governance, dict):
        raise PopulationImportError("The summary carries no governance block")
    for name, document in (("summary governance", governance), ("handoff", handoff)):
        if document.get("wageringAuthority") is not False:
            raise PopulationImportError(f"{name} must declare wageringAuthority=false")
        if document.get("promotionStatus") != "NOT_PROMOTED":
            raise PopulationImportError(f"{name} must declare promotionStatus=NOT_PROMOTED")
        if document.get("executionAllowedInAISP") is not False:
            raise PopulationImportError(
                f"{name} must declare executionAllowedInAISP=false; AISP never "
                "executes a Model Studio artifact"
            )
        if document.get("finalOotOpened") is not False:
            raise PopulationImportError(f"{name} must declare finalOotOpened=false")
    if governance.get("researchShadow") is not True:
        raise PopulationImportError("The summary must declare researchShadow=true")
    if handoff.get("aispModified") is not False:
        raise PopulationImportError("The handoff must declare aispModified=false")
    for counter in ("aispWrites", "providerCalls", "recalibrations", "v18PredictiveModelFits"):
        if int(governance.get(counter, -1)) != 0:
            raise PopulationImportError(f"The summary must declare {counter}=0")


def _assert_identity(
    address: str,
    manifest: dict[str, Any],
    handoff: dict[str, Any],
    summary: dict[str, Any],
) -> str:
    """Manifest, handoff and summary must name the same parents and population."""

    if str(handoff.get("supplementSha256", address)).lower() != address:
        raise PopulationImportError("The handoff names a different supplement")

    parents = {
        str(document.get("parentBundleSha256", "")).lower()
        for document in (manifest, handoff, summary)
    }
    if len(parents) != 1 or not _SHA256.match(next(iter(parents))):
        raise PopulationImportError(
            "Manifest, handoff and summary disagree on the parent V18 bundle"
        )

    play_geometry = {
        str(document.get("playGeometrySupplementSha256", "")).lower()
        for document in (manifest, handoff, summary)
    }
    if len(play_geometry) != 1 or not _SHA256.match(next(iter(play_geometry))):
        raise PopulationImportError(
            "Manifest, handoff and summary disagree on the parent Play Geometry supplement"
        )

    digests = {
        str(document.get("populationDigest", "")).lower()
        for document in (manifest, handoff, summary)
    }
    if len(digests) != 1 or not _SHA256.match(next(iter(digests))):
        raise PopulationImportError(
            "Manifest, handoff and summary disagree on the population digest"
        )

    reconstruction = summary.get("reconstruction") or {}
    if reconstruction.get("firstPopulationDigest") != reconstruction.get(
        "replayPopulationDigest"
    ):
        raise PopulationImportError("The population did not replay deterministically")

    return next(iter(parents))


def _verify_execution_authority(
    root: Path, manifest: dict[str, Any], parent_bundle: str
) -> str:
    """The provenance evidence must decompress and hash to its declared digest."""

    declared = str(manifest.get("executionAuthoritySha256", "")).lower()
    if not _SHA256.match(declared):
        raise PopulationImportError("The manifest declares no execution-authority digest")
    try:
        raw = gzip.decompress((root / _AUTHORITY_FILE).read_bytes())
        authority = json.loads(raw)
    except (OSError, gzip.BadGzipFile, json.JSONDecodeError) as error:
        raise PopulationImportError(
            f"The execution-authority evidence is unreadable: {error}"
        ) from error
    body = {
        key: value for key, value in authority.items()
        if key != "executionAuthoritySha256"
    }
    if _canonical_sha(body) != declared:
        raise PopulationImportError(
            "The execution-authority evidence does not hash to its declared digest"
        )
    if str(authority.get("parentBundleSha256", "")).lower() != parent_bundle:
        raise PopulationImportError(
            "The execution-authority evidence names a different parent bundle"
        )
    return declared


def _verify_frames(
    document: dict[str, Any], summary: dict[str, Any]
) -> list[dict[str, Any]]:
    """100 frames, contiguous, monotonic, ending at the full population."""

    frames = document.get("frames") if isinstance(document, dict) else None
    if not isinstance(frames, list) or not frames:
        raise PopulationImportError("The supplement carries no accumulation frames")

    expected_frames = int(summary.get("frameCount", 0))
    expected_total = int(summary.get("simulationCount", 0))
    frame_size = int(summary.get("frameSize", 0))
    if len(frames) != expected_frames:
        raise PopulationImportError(
            f"Expected {expected_frames} accumulation frames, found {len(frames)}"
        )
    if expected_frames * frame_size != expected_total:
        raise PopulationImportError(
            "The frame count and frame size do not cover the declared population"
        )

    previous = 0
    for position, frame in enumerate(frames):
        if int(frame.get("frameIndex", -1)) != position:
            raise PopulationImportError(f"Frame {position} is out of order")
        cumulative = int(frame.get("cumulativeSimulationCount", -1))
        if cumulative <= previous:
            raise PopulationImportError(
                f"Frame {position} does not increase the cumulative simulation count"
            )
        if int(frame.get("simulationIndexStart", -1)) != previous:
            raise PopulationImportError(f"Frame {position} does not continue the population")
        if int(frame.get("simulationIndexEndInclusive", -1)) != cumulative - 1:
            raise PopulationImportError(f"Frame {position} declares an inconsistent range")
        previous = cumulative

    if previous != expected_total:
        raise PopulationImportError(
            f"The frames accumulate {previous} simulations, not {expected_total}"
        )

    final = summary.get("finalPopulation") or {}
    last = frames[-1]
    for frame_key, summary_key in (
        ("cumulativeHomeWinProbability", "homeWinProbability"),
        ("cumulativeExpectedHomeRuns", "meanHomeRuns"),
        ("cumulativeExpectedAwayRuns", "meanAwayRuns"),
        ("cumulativeExpectedTotal", "meanTotalRuns"),
        ("cumulativeRunDifferential", "meanRunDifferential"),
        ("cumulativeOneRunGameRate", "oneRunRate"),
        ("cumulativeExtraInningRate", "extraInningRate"),
    ):
        if abs(float(last.get(frame_key, 0)) - float(final.get(summary_key, 0))) > 1e-9:
            raise PopulationImportError(
                f"The final frame's {frame_key} disagrees with the accepted population"
            )
    return frames


def _verify_contact_density(
    document: dict[str, Any], summary: dict[str, Any]
) -> dict[str, Any]:
    """A sparse 32x32 grid whose ALL layer totals the declared contact plays."""

    if document.get("schemaVersion") != LAYER_SCHEMA:
        raise PopulationImportError("The contact-density layer declares a different schema")
    if document.get("coordinateSystem") != COORDINATE_SYSTEM:
        raise PopulationImportError("The contact-density layer uses a different coordinate system")
    grid = document.get("grid") or {}
    width, height = int(grid.get("width", 0)), int(grid.get("height", 0))
    if width <= 0 or height <= 0:
        raise PopulationImportError("The contact-density grid declares no shape")
    layers = document.get("layers")
    if not isinstance(layers, dict) or "ALL" not in layers:
        raise PopulationImportError("The contact-density layer set has no ALL layer")

    cells = layers["ALL"].get("sparseRawCounts") or []
    total = 0
    for cell in cells:
        index, count = int(cell[0]), int(cell[1])
        if not 0 <= index < width * height:
            raise PopulationImportError("A contact-density cell falls outside the grid")
        total += count
    declared = int((summary.get("counts") or {}).get("contactPlays", -1))
    if total != declared:
        raise PopulationImportError(
            f"The contact density totals {total} plays, not the declared {declared}"
        )
    return document


def _verify_path_density(document: dict[str, Any]) -> dict[str, Any]:
    """A sparse quadratic-cell ball-path grid."""

    if document.get("schemaVersion") != LAYER_SCHEMA:
        raise PopulationImportError("The path-density layer declares a different schema")
    grid = document.get("grid") or {}
    width, height = int(grid.get("width", 0)), int(grid.get("height", 0))
    if width <= 0 or height <= 0:
        raise PopulationImportError("The path-density grid declares no shape")
    layers = document.get("layers")
    if not isinstance(layers, dict) or "ALL" not in layers:
        raise PopulationImportError("The path-density layer set has no ALL layer")
    limit = width * height
    for entry in layers["ALL"]:
        origin, control = divmod(int(entry[0]), limit)
        destination = int(entry[1])
        if not (0 <= origin < limit and 0 <= control < limit and 0 <= destination < limit):
            raise PopulationImportError("A ball-path cell falls outside the grid")
    return document


def _verify_runner_traffic(
    document: dict[str, Any], summary: dict[str, Any]
) -> dict[str, Any]:
    """Anonymous segment counts totalling the declared runner segments."""

    if document.get("schemaVersion") != LAYER_SCHEMA:
        raise PopulationImportError("The runner-traffic layer declares a different schema")
    segments = document.get("allSegments")
    if not isinstance(segments, dict) or not segments:
        raise PopulationImportError("The runner-traffic layer carries no segments")
    total = sum(int(value) for value in segments.values())
    declared = int((summary.get("counts") or {}).get("runnerSegments", -1))
    if total != declared:
        raise PopulationImportError(
            f"Runner traffic totals {total} segments, not the declared {declared}"
        )
    return document


def _verify_representatives(
    document: dict[str, Any], summary: dict[str, Any]
) -> dict[str, Any]:
    """Six distinct representative games, matching the summary's selectors."""

    paths = document.get("paths")
    selectors = (summary.get("representatives") or {}).get("selectors") or {}
    if not isinstance(paths, list) or not paths:
        raise PopulationImportError("The supplement carries no representative paths")
    if set(selectors) != set(SELECTORS):
        raise PopulationImportError(
            "The summary does not carry exactly the six required selectors"
        )

    by_digest = {}
    for path in paths:
        if path.get("schemaVersion") != PATH_SCHEMA:
            raise PopulationImportError("A representative path declares a different schema")
        events = path.get("events")
        if not isinstance(events, list) or not events:
            raise PopulationImportError("A representative path carries no events")
        if len(events) != int(path.get("plateAppearances", -1)):
            raise PopulationImportError(
                "A representative path disagrees with its own plate-appearance count"
            )
        by_digest[str(path.get("pathDigest"))] = path

    if len(by_digest) != len(paths):
        raise PopulationImportError("Two representative paths share a digest")

    chosen = {}
    for selector in SELECTORS:
        digest = str(selectors[selector].get("pathDigest"))
        if digest not in by_digest:
            raise PopulationImportError(
                f"The {selector} selector names a path the supplement does not carry"
            )
        # `simulationIndex` may legitimately be 0: LOW_SCORING is simulation
        # zero, which is a selection, not a missing value.
        index = selectors[selector].get("simulationIndex")
        if not isinstance(index, int) or index < 0:
            raise PopulationImportError(f"The {selector} selector names no simulation")
        chosen[selector] = digest

    if len(set(chosen.values())) != len(SELECTORS):
        raise PopulationImportError(
            "The six selectors do not resolve to six distinct simulated games"
        )
    return document


# -- registration ---------------------------------------------------------


def register_population(
    session: Session,
    verified: VerifiedPopulation,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Attach the population supplement to its parent shadow forecast.

    Additive and idempotent, exactly like the Play Geometry importer: the only
    write is one new key inside the shadow forecast's own ``lineage_detail``.
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
        raise PopulationImportError(
            "No registered V18 shadow forecast carries the supplement's parent "
            f"bundle {verified.parent_bundle_sha256[:12]}..."
        )

    registered: list[str] = []
    already: list[str] = []
    skipped: list[dict[str, str]] = []

    for row in targets:
        lineage = dict(row.lineage_detail or {})
        play_geometry = lineage.get(PLAY_GEOMETRY_KEY) or {}
        if str(play_geometry.get("supplementSha256", "")).lower() != verified.play_geometry_sha256:
            skipped.append({
                "forecastId": row.forecast_id,
                "reason": (
                    "the forecast does not carry the parent Play Geometry supplement "
                    f"{verified.play_geometry_sha256[:12]}..."
                ),
            })
            continue

        paths = verified.representatives.get("paths") or []
        foreign = [
            str(path.get("gameId")) for path in paths
            if str(path.get("gameId")) != str(row.event_id)
        ]
        if foreign:
            skipped.append({
                "forecastId": row.forecast_id,
                "reason": "a representative path belongs to a different game",
            })
            continue

        existing = lineage.get(LINEAGE_KEY)
        if existing and str(existing.get("supplementSha256")) == verified.supplement_sha256:
            already.append(row.forecast_id)
            continue

        lineage[LINEAGE_KEY] = {
            "schemaVersion": SUMMARY_SCHEMA,
            "supplementId": verified.supplement_id,
            "supplementSha256": verified.supplement_sha256,
            "parentBundleId": verified.parent_bundle_id,
            "parentBundleSha256": verified.parent_bundle_sha256,
            "playGeometrySupplementSha256": verified.play_geometry_sha256,
            "populationDigest": verified.population_digest,
            "executionAuthoritySha256": verified.execution_authority_sha256,
            "registeredAt": now.isoformat(),
            "artifactsVerified": verified.artifact_count,
            "wageringAuthority": False,
            "promotionStatus": "NOT_PROMOTED",
            "executionAllowedInAISP": False,
            "displayAuthority": DISPLAY_AUTHORITY,
            # The verified objects themselves, stored beside -- never over --
            # the accepted forecast and its single-game geometry.
            "summary": verified.summary,
            "frames": verified.frames,
            "contactDensity": verified.contact_density,
            "pathDensity": verified.path_density,
            "runnerTraffic": verified.runner_traffic,
            "representatives": verified.representatives,
        }
        row.lineage_detail = lineage
        flag_modified(row, "lineage_detail")
        registered.append(row.forecast_id)

    session.flush()
    return {
        "supplementId": verified.supplement_id,
        "supplementSha256": verified.supplement_sha256,
        "parentBundleSha256": verified.parent_bundle_sha256,
        "playGeometrySha256": verified.play_geometry_sha256,
        "simulationCount": verified.simulation_count,
        "frameCount": len(verified.frames),
        "artifactsVerified": verified.artifact_count,
        "registered": registered,
        "alreadyRegistered": already,
        "skipped": skipped,
        "gen1HistoryMutated": False,
        "wageringAuthority": False,
    }


def population_status(session: Session) -> dict[str, Any]:
    """Whether population geometry is registered for each V18 shadow forecast."""

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
                "playGeometryRegistered": bool(
                    (row.lineage_detail or {}).get(PLAY_GEOMETRY_KEY)
                ),
                "populationRegistered": bool((row.lineage_detail or {}).get(LINEAGE_KEY)),
                "supplementSha256": (
                    ((row.lineage_detail or {}).get(LINEAGE_KEY) or {}).get("supplementSha256")
                ),
                "simulationCount": (
                    (((row.lineage_detail or {}).get(LINEAGE_KEY) or {}).get("summary") or {})
                    .get("simulationCount")
                ),
                "frameCount": len(
                    ((row.lineage_detail or {}).get(LINEAGE_KEY) or {}).get("frames") or []
                ),
            }
            for row in rows
        ],
    }


def _registered(session: Session, forecast_id: str) -> dict[str, Any] | None:
    row = session.scalar(
        select(ShadowForecast).where(ShadowForecast.forecast_id == forecast_id)
    )
    if row is None or row.engine_id != ENGINE_ID:
        return None
    population = (row.lineage_detail or {}).get(LINEAGE_KEY)
    return population or None


def _identity(population: dict[str, Any]) -> dict[str, Any]:
    """The provenance block every population response repeats."""

    return {
        "supplement_id": population.get("supplementId"),
        "supplement_sha256": population.get("supplementSha256"),
        "parent_bundle_sha256": population.get("parentBundleSha256"),
        "play_geometry_sha256": population.get("playGeometrySupplementSha256"),
        "population_digest": population.get("populationDigest"),
        "execution_authority_sha256": population.get("executionAuthoritySha256"),
        "wagering_authority": False,
        "promotion_status": "NOT_PROMOTED",
        "execution_allowed_in_aisp": False,
        "display_authority": DISPLAY_AUTHORITY,
    }


def population_payload(session: Session, forecast_id: str) -> dict[str, Any] | None:
    """Stage one: identity, accepted summary and the 100 accumulation frames."""

    population = _registered(session, forecast_id)
    if population is None:
        return None
    summary = population.get("summary") or {}
    return {
        "schema_version": "AISP_MLB_V18_POPULATION_VIEW_V1",
        "forecast_id": forecast_id,
        **_identity(population),
        "simulation_count": summary.get("simulationCount"),
        "frame_count": summary.get("frameCount"),
        "frame_size": summary.get("frameSize"),
        "master_seed": summary.get("masterSeed"),
        "counts": summary.get("counts"),
        "final_population": summary.get("finalPopulation"),
        "illegal_state_count": summary.get("illegalStateCount"),
        "nontermination_count": summary.get("nonterminationCount"),
        "deterministic_replay": (summary.get("reconstruction") or {}).get("deterministicReplay"),
        "frames": population.get("frames") or [],
        "selectors": (summary.get("representatives") or {}).get("selectors") or {},
        "notes": (
            "Deterministic aggregation over 5,000 complete V18 simulations. Each "
            "frame adds 50 completed games; it is not one continuous baseball "
            "game. Geometry is modelled from Retrosheet zone evidence, not "
            "measured tracking, and runner identities are anonymous."
        ),
    }


def population_layers(session: Session, forecast_id: str) -> dict[str, Any] | None:
    """Stage two: the compact density and traffic layers."""

    population = _registered(session, forecast_id)
    if population is None:
        return None
    return {
        "schema_version": "AISP_MLB_V18_POPULATION_LAYERS_V1",
        "forecast_id": forecast_id,
        **_identity(population),
        "contact_density": population.get("contactDensity"),
        "path_density": population.get("pathDensity"),
        "runner_traffic": population.get("runnerTraffic"),
    }


def population_representatives(session: Session, forecast_id: str) -> dict[str, Any] | None:
    """Stage three: the six distinct representative simulated games."""

    population = _registered(session, forecast_id)
    if population is None:
        return None
    representatives = population.get("representatives") or {}
    summary = population.get("summary") or {}
    return {
        "schema_version": "AISP_MLB_V18_POPULATION_REPRESENTATIVES_V1",
        "forecast_id": forecast_id,
        **_identity(population),
        "selectors": (summary.get("representatives") or {}).get("selectors") or {},
        "paths": representatives.get("paths") or [],
        "unique_path_count": representatives.get("uniquePathCount"),
        "notes": (
            "One selected simulated game out of 5,000. A representative path is "
            "not the expected exact play sequence of the real game."
        ),
    }
