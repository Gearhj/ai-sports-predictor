"""A synthetic, physically complete NFL FINAL readiness family.

WHY THIS EXISTS
---------------
The readiness family described by ``nfl-final-delivery-operational-v2.md`` does
not exist on disk yet: the producer's operational-V2 revision has not been
activated. The consumer joins still have to be written, reviewed and tested
BEFORE that activation, because the contract requires "a separately compatible
consumer deployment before activation".

So this module builds the whole family -- discovery index, forecast bundle,
supplement, readiness index, receipt, availability sidecar and exposure object
-- into a temporary directory, with real content addresses over real canonical
bytes. Nothing here is authentic production evidence and none of it is ever
written into the configured exchange root; ``build`` refuses any destination it
was not handed explicitly.

WHAT IS REAL AND WHAT IS SYNTHETIC
----------------------------------
REAL: the forecast wrapper. It is a genuine ``ASP_SPORTS_FORECAST_EXCHANGE_V1``
bundle that passes the deployed ``verify_and_load_sports_bundle`` unmodified,
including the frozen NFL model authority hashes, the pinned VALIDATION.md and
every distribution reconciliation. The readiness joins therefore run against
the same wrapper verification production uses.

SYNTHETIC: the readiness family's own shapes. The contract names the documents,
their schema ids and their required fields, but no producer has emitted one, so
the exact field spelling below is this consumer's reading of the contract and
may need to be reconciled against the first authentic object.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.forecast_exchange.importer import _SPORTS_VALIDATION_V1
from app.forecast_exchange.sports_contracts import (
    NFL_FEATURE_CONTRACT_SHA256,
    NFL_MODEL_BINARY_SHA256,
    NFL_MODEL_MANIFEST_SHA256,
    NFL_TRAINING_CUTOFF,
)
from app.gen2.exchange.canonical_json import canonical_json_bytes, canonical_sha256
from app.services.nfl_final_readiness import (
    AVAILABILITY_DIR,
    EXPOSURE_SCHEMA,
    INDEX_SCHEMA,
    OBJECTS_DIR,
    READINESS_INDEX_FILENAME,
    READINESS_ROOT,
    RECEIPT_SCHEMA,
    STATUS_TIMELY,
)

#: One kickoff, and every clock in the family derives from it. Eastern date is
#: 2026-09-21, which is what the forecast contract reconciles ``eventDate`` to.
KICKOFF = datetime(2026, 9, 22, 0, 20, tzinfo=UTC)
#: The producer's hard publication boundary and the consumer's decision cutoff.
#: They are DIFFERENT clocks and the fixture keeps them apart on purpose.
PRODUCER_DEADLINE = KICKOFF - timedelta(minutes=21)
CONSUMER_CUTOFF = KICKOFF - timedelta(minutes=15)

EVENT_ID = "NFL-2026-W03-SYN-HOME-AWAY"
#: The wrapper contract requires the exact provider-qualified form.
def source_event_id(event_id: str) -> str:
    return f"nflverse:{event_id}"
EVENT_DATE = date(2026, 9, 21)
PRODUCER_REVISION = "nfl-final-delivery-operational-v2"
#: The receipt's declared schema hash, and the FIELD carrying it.
#:
#: Both are our reading of the contract, not an authentic producer byte: the
#: contract names the receipt schema id but not the field that carries its hash.
#: Codex supplies the real value; this fixture exists so the pinned-pass,
#: pinned-mismatch and pinned-but-absent paths are all exercised before then.
RECEIPT_SCHEMA_SHA256_FIELD = "schemaSha256"
RECEIPT_SCHEMA_SHA256 = hashlib.sha256(
    b"NFL_FINAL_READY_RECEIPT_V1").hexdigest()
SUPPLEMENT_ROOT = "nfl-labs-supplements"
SUPPLEMENT_INDEX_FILENAME = "nfl-labs-supplement-index.json"
SUPPLEMENT_INDEX_SCHEMA = "AISP_NFL_LABS_SUPPLEMENT_INDEX_V1"
FORECAST_FAMILY = "aisp-daily-forecast-bundles"
FORECAST_INDEX_FILENAME = "nfl-2026-research-shadow-index.json"
FORECAST_INDEX_SCHEMA = "ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V2"

#: A three-cell joint distribution. Everything the forecast contract
#: cross-checks -- win/tie probabilities, expected scores, margins, totals,
#: spreads, over/unders and the most-likely table -- is DERIVED from it below,
#: so the fixture cannot drift out of agreement with itself.
_JOINT = ((24, 21, 0.5), (21, 24, 0.3), (21, 21, 0.2))


def _hex(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _bind(document: dict[str, Any], id_field: str, prefix: str) -> dict[str, Any]:
    """Give a document the sha256-addressed id its own content binds to.

    This is the deployed ``_verify_child_binding`` rule, run forwards: the id is
    the canonical hash of the document MINUS the id field.
    """

    projection = {key: value for key, value in document.items() if key != id_field}
    return {**document, id_field: f"{prefix}sha256:{canonical_sha256(projection)}"}


def _team(team_id: str, source_team_id: str, abbreviation: str, name: str) -> dict[str, Any]:
    projection = {
        "abbreviation": abbreviation,
        "source": "espn",
        "sourceTeamId": source_team_id,
        "teamId": team_id,
    }
    return {
        "teamId": team_id,
        "source": "espn",
        "sourceTeamId": source_team_id,
        "abbreviation": abbreviation,
        "identitySha256": canonical_sha256(projection),
        "displayName": name,
    }


def _derived_distributions() -> dict[str, Any]:
    """Every declared probability, computed from ``_JOINT`` and nothing else."""

    joint = [{"homeScore": h, "awayScore": a, "probability": p} for h, a, p in _JOINT]
    home_win = sum(p for h, a, p in _JOINT if h > a)
    away_win = sum(p for h, a, p in _JOINT if a > h)
    tie = sum(p for h, a, p in _JOINT if h == a)
    margins: dict[int, float] = {}
    totals: dict[int, float] = {}
    for h, a, p in _JOINT:
        margins[h - a] = margins.get(h - a, 0.0) + p
        totals[h + a] = totals.get(h + a, 0.0) + p

    def spread(line: float) -> dict[str, Any]:
        return {
            "line": line,
            "homeCoverProbability": sum(p for m, p in margins.items() if m + line > 0),
            "awayCoverProbability": sum(p for m, p in margins.items() if m + line < 0),
            "pushProbability": sum(p for m, p in margins.items() if m + line == 0),
        }

    def total(line: float) -> dict[str, Any]:
        return {
            "line": line,
            "overProbability": sum(p for t, p in totals.items() if t - line > 0),
            "underProbability": sum(p for t, p in totals.items() if t - line < 0),
            "pushProbability": sum(p for t, p in totals.items() if t - line == 0),
        }

    ranked = sorted(_JOINT, key=lambda row: (-row[2], row[0], row[1]))
    return {
        "expectedHomeScore": sum(h * p for h, a, p in _JOINT),
        "expectedAwayScore": sum(a * p for h, a, p in _JOINT),
        "homeWinProbability": home_win,
        "awayWinProbability": away_win,
        "tieProbability": tie,
        "jointScoreDistribution": joint,
        "scoreMarginDistribution": [
            {"homeMinusAway": m, "probability": p} for m, p in sorted(margins.items())
        ],
        "totalScoreDistribution": [
            {"totalScore": t, "probability": p} for t, p in sorted(totals.items())
        ],
        "spreadCoverProbabilities": [spread(0.0), spread(-2.5)],
        "overUnderProbabilities": [total(44.5), total(45.0)],
        "mostLikelyScores": [
            {"homeScore": h, "awayScore": a, "probability": p}
            for h, a, p in ranked[: min(5, len(_JOINT))]
        ],
    }


def _readiness() -> dict[str, Any]:
    return {
        "isReady": True,
        "score": 92.0,
        "threshold": 70.0,
        "missingInputs": [],
        "reasonCodes": ["READY"],
    }


def build_forecast_child(
    *,
    event_id: str = EVENT_ID,
    kickoff: datetime = KICKOFF,
    snapshot_type: str = "FINAL_PREGAME",
    snapshot_version: int = 1,
    available_as_of: datetime | None = None,
    home: tuple[str, str, str, str] = ("nfl-home-001", "21", "HOM", "Home Club"),
    away: tuple[str, str, str, str] = ("nfl-away-002", "22", "AWY", "Away Club"),
) -> dict[str, Any]:
    """One contract-valid ``ASP_SPORTS_GAME_FORECAST_V1`` child."""

    available = available_as_of or (kickoff - timedelta(minutes=25))
    forecast_cutoff = available - timedelta(minutes=5)
    source_cutoff = forecast_cutoff - timedelta(minutes=5)
    schedule_as_of = source_cutoff - timedelta(minutes=5)
    body = {
        "schemaVersion": "ASP_SPORTS_GAME_FORECAST_V1",
        "snapshotType": snapshot_type,
        "sport": "NFL",
        "league": "NFL",
        "season": 2026,
        "seasonType": "REG",
        "week": 3,
        "eventDate": kickoff.astimezone(UTC).date().isoformat()
        if kickoff.astimezone(UTC).date() == EVENT_DATE
        else EVENT_DATE.isoformat(),
        "eventId": event_id,
        "sourceEventId": source_event_id(event_id),
        "scheduledStartUtc": _iso(kickoff),
        "scheduleAsOfUtc": _iso(schedule_as_of),
        "forecastCutoffUtc": _iso(forecast_cutoff),
        "sourceCutoffUtc": _iso(source_cutoff),
        "availableAsOfUtc": _iso(available),
        "snapshotVersion": snapshot_version,
        "homeTeam": _team(*home),
        "awayTeam": _team(*away),
        "engine": {
            "engineId": "NFL_M1_XGBOOST",
            "engineVersion": "1.0.0",
            "candidateId": "NFL_M1_XGBOOST_CAND_0001",
            "modelManifestSha256": NFL_MODEL_MANIFEST_SHA256,
            "modelBinarySha256": NFL_MODEL_BINARY_SHA256,
            "trainingDatasetVersion": "nfl-2005-2025-v1",
            "trainingDatasetManifestSha256": _hex("training-dataset-manifest"),
            "featureContractSha256": NFL_FEATURE_CONTRACT_SHA256,
            "producerRevision": PRODUCER_REVISION,
            "featureTier": "LONG_HISTORY_CORE",
            "trainingCutoff": NFL_TRAINING_CUTOFF.isoformat(),
        },
        "features": {
            "featureCount": 48,
            "featureVectorSha256": _hex(f"feature-vector-{event_id}-{snapshot_version}"),
            "missingFeatureCount": 0,
            "missingFeatures": [],
            "missingReasons": {},
            "readiness": _readiness(),
        },
        "readiness": _readiness(),
        "status": "RESEARCH_SHADOW",
        "promotionStatus": "NOT_PROMOTED",
        "executionStatus": "NOT_EXECUTABLE",
        "researchShadowOnly": True,
        "modelPromotionAllowed": False,
        "executionAllowed": False,
        **_derived_distributions(),
        "predictionInterval": {
            "confidenceLevel": 0.8,
            "homeScoreLower": 17.0,
            "homeScoreUpper": 28.0,
            "awayScoreLower": 16.0,
            "awayScoreUpper": 27.0,
            "totalLower": 35.0,
            "totalUpper": 55.0,
            "marginLower": -10.0,
            "marginUpper": 12.0,
        },
        "uncertainty": {
            "method": "BOOTSTRAP",
            "confidenceLevel": 0.8,
            "homeWinProbabilityLower": 0.42,
            "homeWinProbabilityUpper": 0.58,
            "expectedTotalScoreStdError": 2.5,
            "maxBatchProbabilityDelta": 0.004,
        },
        "simulator": {
            "simulatorId": "NFL_JOINT_EXACT",
            "simulatorVersion": "1.0.0",
            "seed": 731001,
            "simulationCount": 20000,
            "completedSimulations": 20000,
        },
    }
    return _bind(body, "forecastId", "asp:nfl:forecast:")


def write_forecast_wrapper(
    root: Path, children: list[dict[str, Any]], *, generated_at: datetime | None = None,
) -> tuple[str, Path]:
    """Write one immutable three-file wrapper; return its address and directory.

    The address is the sha256 of ``bundle.json``'s canonical bytes, which is
    exactly what the deployed verifier recomputes.
    """

    earliest_kickoff = min(
        datetime.fromisoformat(child["scheduledStartUtc"].replace("Z", "+00:00"))
        for child in children
    )
    latest_available = max(
        datetime.fromisoformat(child["availableAsOfUtc"].replace("Z", "+00:00"))
        for child in children
    )
    generated = generated_at or max(latest_available, earliest_kickoff - timedelta(minutes=24))
    bundle_body = {
        "schemaVersion": "ASP_SPORTS_FORECAST_EXCHANGE_V1",
        "sport": "NFL",
        "league": "NFL",
        "season": 2026,
        "slateDate": children[0]["eventDate"],
        "generatedAtUtc": _iso(generated),
        "status": "RESEARCH_SHADOW",
        "researchShadowOnly": True,
        "modelPromotionAllowed": False,
        "executionAllowed": False,
        "parlaysAllowed": False,
        "forecasts": children,
    }
    bundle = _bind(bundle_body, "bundleId", "asp:nfl:bundle:")
    bundle_bytes = canonical_json_bytes(bundle)
    address = hashlib.sha256(bundle_bytes).hexdigest()

    directory = Path(root) / FORECAST_FAMILY / "sha256" / address
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "bundle.json").write_bytes(bundle_bytes)
    (directory / "VALIDATION.md").write_bytes(_SPORTS_VALIDATION_V1)
    fixture_body = {
        "schemaVersion": "ASP_SPORTS_FORECAST_IMPORT_FIXTURE_V1",
        "bundleLogicalUri": (
            f"asp-model-exchange://{FORECAST_FAMILY}/sha256/{address}/bundle.json"
        ),
        "bundleSha256": address,
        "researchShadowOnly": True,
        "executionAllowed": False,
        "expectedContractVersions": {
            "sportsForecastExchange": "ASP_SPORTS_FORECAST_EXCHANGE_V1",
            "sportsGameForecast": "ASP_SPORTS_GAME_FORECAST_V1",
        },
        "expectedRecordCounts": {"forecasts": len(children)},
        "validationInstructionsSha256": hashlib.sha256(_SPORTS_VALIDATION_V1).hexdigest(),
    }
    fixture = _bind(fixture_body, "fixtureId", "asp:nfl:fixture:")
    (directory / "consumer-import-fixture.json").write_bytes(canonical_json_bytes(fixture))
    return address, directory


def write_forecast_index(
    root: Path, current: list[str], *, historical: list[str] | None = None,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    """The MUTABLE discovery locator. It carries no immutable authority."""

    current_sorted = sorted(set(current))
    historical_sorted = sorted(set(historical or []))
    index = {
        "schemaVersion": FORECAST_INDEX_SCHEMA,
        "sport": "NFL",
        "league": "NFL",
        "season": 2026,
        "status": "RESEARCH_SHADOW",
        "researchShadowOnly": True,
        "bundleContractVersion": "ASP_SPORTS_FORECAST_EXCHANGE_V1",
        "forecastContractVersion": "ASP_SPORTS_GAME_FORECAST_V1",
        "modelManifestSha256": NFL_MODEL_MANIFEST_SHA256,
        "modelBinarySha256": NFL_MODEL_BINARY_SHA256,
        "featureContractSha256": NFL_FEATURE_CONTRACT_SHA256,
        "updatedAtUtc": _iso(updated_at or (KICKOFF - timedelta(minutes=24))),
        "currentBundleCount": len(current_sorted),
        "currentBundleSha256s": current_sorted,
        "currentBundleListSha256": canonical_sha256(current_sorted),
        "historicalBundleCount": len(historical_sorted),
        "historicalBundleSha256s": historical_sorted,
        "historicalBundleListSha256": canonical_sha256(historical_sorted),
    }
    path = Path(root) / FORECAST_FAMILY / FORECAST_INDEX_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(index))
    return index


def write_supplement(root: Path, *, forecast_id: str, event_id: str = EVENT_ID) -> str:
    """A content-addressed paper-eligibility grant.

    Only the shape the readiness joins actually traverse is built here: the
    address, the two-file inventory and the forecast/event binding. The full
    ``verify_supplement`` contract is the eligibility owner's job and is not
    re-litigated by the readiness gate.
    """

    supplement = {
        "schemaVersion": "AISP_RESEARCH_SUPPLEMENT_V1",
        "kind": "PAPER_RESEARCH_ELIGIBILITY",
        "sport": "NFL",
        "eventId": event_id,
        "forecastId": forecast_id,
        "engineId": "NFL_M1_XGBOOST",
        "artifactVersion": "ASP_NFL_PAPER_RESEARCH_ELIGIBILITY_V1",
        "researchShadowOnly": True,
        "executionAllowed": False,
    }
    body = canonical_json_bytes(supplement)
    address = hashlib.sha256(body).hexdigest()
    directory = Path(root) / SUPPLEMENT_ROOT / "sha256" / address
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "supplement.json").write_bytes(body)
    manifest = {
        "schemaVersion": "AISP_RESEARCH_SUPPLEMENT_MANIFEST_V1",
        "supplementSha256": address,
        "kind": "PAPER_RESEARCH_ELIGIBILITY",
        "sport": "NFL",
    }
    (directory / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    return address


def write_supplement_index(root: Path, addresses: list[str]) -> None:
    index = {
        "schemaVersion": SUPPLEMENT_INDEX_SCHEMA,
        "currentSupplementSha256s": sorted(set(addresses)),
    }
    path = Path(root) / SUPPLEMENT_ROOT / SUPPLEMENT_INDEX_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(index))


@dataclass(frozen=True)
class ReadinessFamily:
    """Everything a test needs to drive one verification."""

    root: Path
    forecast_id: str
    event_id: str
    kickoff: datetime
    bundle_sha256: str
    supplement_sha256: str
    receipt_sha256: str
    exposure_sha256: str
    forecast_child: dict[str, Any] = field(default_factory=dict)

    @property
    def producer_deadline(self) -> datetime:
        return self.kickoff - timedelta(minutes=21)

    @property
    def consumer_cutoff(self) -> datetime:
        return self.kickoff - timedelta(minutes=15)

    def receipt_path(self) -> Path:
        return self.root / READINESS_ROOT / OBJECTS_DIR / self.receipt_sha256 / "receipt.json"

    def exposure_path(self) -> Path:
        return self.root / READINESS_ROOT / OBJECTS_DIR / self.exposure_sha256 / "exposure.json"

    def availability_path(self) -> Path:
        return self.root / READINESS_ROOT / AVAILABILITY_DIR / f"{self.receipt_sha256}.json"

    def index_path(self) -> Path:
        return self.root / READINESS_ROOT / READINESS_INDEX_FILENAME


def build(
    root: Path,
    *,
    kickoff: datetime = KICKOFF,
    exposure_status: str = STATUS_TIMELY,
    with_availability: bool = True,
    extra_children: list[dict[str, Any]] | None = None,
    supplement_current: bool = True,
    forecast_current: bool = True,
    sidecar_first_observed: datetime | None = None,
    grant_observed: datetime | None = None,
    ready_index_observed: datetime | None = None,
) -> ReadinessFamily:
    """Write one complete, correctly shaped readiness family under ``root``.

    ``root`` is the caller's temporary directory. Nothing is written anywhere
    else, and in particular nothing is written into the configured exchange
    root: the tests pass ``tmp_path`` and the production tree is never touched.
    """

    root = Path(root)
    child = build_forecast_child(kickoff=kickoff)
    forecast_id = child["forecastId"]
    children = [child, *(extra_children or [])]
    bundle_sha, _ = write_forecast_wrapper(root, children)

    # ORDER MATTERS HERE.
    #
    # The discovery locators are first written in the state they were in AT
    # PUBLICATION, and the receipt snapshots THAT. Only afterwards is the
    # present-tense degradation applied. Snapshotting an already-degraded index
    # would make every join-3 negative case fail at join 2 instead, which is
    # the opposite of what these fixtures are for: the whole point of a
    # superseded forecast is that the ORIGINAL snapshot was valid and the
    # PRESENT locator no longer names it.
    write_forecast_index(root, [bundle_sha], historical=[])
    supplement_sha = write_supplement(root, forecast_id=forecast_id)
    write_supplement_index(root, [supplement_sha])

    grant_seen = grant_observed or (kickoff - timedelta(minutes=23))
    index_seen = ready_index_observed or (kickoff - timedelta(minutes=22))
    forecast_index_snapshot = json.loads(
        (root / FORECAST_FAMILY / FORECAST_INDEX_FILENAME).read_bytes()
    )
    supplement_index_snapshot = json.loads(
        (root / SUPPLEMENT_ROOT / SUPPLEMENT_INDEX_FILENAME).read_bytes()
    )

    if not forecast_current:
        # An index with no current bundle at all is not a legal V2 index, so a
        # superseded forecast is modelled the honest way: the wrapper moves to
        # the historical list and some OTHER bundle becomes current.
        other = build_forecast_child(
            kickoff=kickoff, event_id="NFL-2026-W03-OTHER",
            home=("nfl-home-101", "31", "OTH", "Other Home"),
            away=("nfl-away-102", "32", "OTA", "Other Away"),
        )
        other_sha, _ = write_forecast_wrapper(root, [other])
        write_forecast_index(root, [other_sha], historical=[bundle_sha])
    if not supplement_current:
        write_supplement_index(root, [])
    ready_index_snapshot = {
        "schemaVersion": INDEX_SCHEMA,
        "entries": [{"forecastId": forecast_id, "exposureStatus": exposure_status}],
    }

    receipt = {
        "schemaVersion": RECEIPT_SCHEMA,
        RECEIPT_SCHEMA_SHA256_FIELD: RECEIPT_SCHEMA_SHA256,
        "eventId": child["eventId"],
        "forecastId": forecast_id,
        "snapshotType": child["snapshotType"],
        "snapshotVersion": child["snapshotVersion"],
        "engineId": "NFL_M1_XGBOOST",
        "modelManifestSha256": NFL_MODEL_MANIFEST_SHA256,
        "modelBinarySha256": NFL_MODEL_BINARY_SHA256,
        "producerRevision": PRODUCER_REVISION,
        "scheduledStartUtc": _iso(kickoff),
        "availableAsOfUtc": child["availableAsOfUtc"],
        "forecastCutoffUtc": child["forecastCutoffUtc"],
        "producerDeadlineUtc": _iso(kickoff - timedelta(minutes=21)),
        "bundle": {
            "bundleSha256": bundle_sha,
            "logicalUri": (
                f"asp-model-exchange://{FORECAST_FAMILY}/sha256/{bundle_sha}/bundle.json"
            ),
            "forecastChildIds": [item["forecastId"] for item in children],
        },
        "grant": {"supplementSha256": supplement_sha, "forecastId": forecast_id},
        "grantObservedAtUtc": _iso(grant_seen),
        "currentIndexObservedAtUtc": _iso(index_seen),
        "forecastIndexSnapshot": forecast_index_snapshot,
        "supplementIndexSnapshot": supplement_index_snapshot,
        "readyIndexSnapshot": ready_index_snapshot,
    }
    receipt_bytes = canonical_json_bytes(receipt)
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    receipt_dir = root / READINESS_ROOT / OBJECTS_DIR / receipt_sha
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / "receipt.json").write_bytes(receipt_bytes)

    # The sidecar is FIRST OBSERVED AFTER the T-21 producer boundary on the
    # positive path, deliberately. That is the contract's central distinction:
    # the pair and the ready index were observed by T-21, the proof of it
    # showed up later, and that delay is telemetry, not a missed boundary.
    sidecar_seen = sidecar_first_observed or (kickoff - timedelta(minutes=19))
    exposure = {
        "schemaVersion": EXPOSURE_SCHEMA,
        "forecastId": forecast_id,
        "baseReceiptSha256": receipt_sha,
        "exposureStatus": exposure_status,
        "readyAvailableAtUtc": _iso(index_seen + timedelta(seconds=30)),
        "readyIndexSnapshot": ready_index_snapshot,
        "sidecarFirstObservedAtUtc": _iso(sidecar_seen),
        "proofObservedAfterProducerBoundary": sidecar_seen > kickoff - timedelta(minutes=21),
        "consumerCutoffPassedAtProofObservation": (
            sidecar_seen > kickoff - timedelta(minutes=15)
        ),
    }
    exposure_bytes = canonical_json_bytes(exposure)
    exposure_sha = hashlib.sha256(exposure_bytes).hexdigest()
    exposure_dir = root / READINESS_ROOT / OBJECTS_DIR / exposure_sha
    exposure_dir.mkdir(parents=True, exist_ok=True)
    (exposure_dir / "exposure.json").write_bytes(exposure_bytes)

    availability_dir = root / READINESS_ROOT / AVAILABILITY_DIR
    availability_dir.mkdir(parents=True, exist_ok=True)
    if with_availability:
        availability = {
            "schemaVersion": "NFL_FINAL_READY_AVAILABILITY_V1",
            "baseReceiptSha256": receipt_sha,
            "forecastId": forecast_id,
            "exposureObjectPath": f"{OBJECTS_DIR}/{exposure_sha}/exposure.json",
            "exposureSha256": exposure_sha,
        }
        (availability_dir / f"{receipt_sha}.json").write_bytes(
            canonical_json_bytes(availability))

    ready_index = {
        "schemaVersion": INDEX_SCHEMA,
        "sport": "NFL",
        "league": "NFL",
        "season": 2026,
        "updatedAtUtc": _iso(index_seen),
        "entries": [
            {
                "forecastId": forecast_id,
                "receiptRef": f"{OBJECTS_DIR}/{receipt_sha}/receipt.json",
                "receiptSha256": receipt_sha,
                "exposureReferencePath": f"{AVAILABILITY_DIR}/{receipt_sha}.json",
            }
        ],
    }
    (root / READINESS_ROOT).mkdir(parents=True, exist_ok=True)
    (root / READINESS_ROOT / READINESS_INDEX_FILENAME).write_bytes(
        canonical_json_bytes(ready_index))

    return ReadinessFamily(
        root=root,
        forecast_id=forecast_id,
        event_id=child["eventId"],
        kickoff=kickoff,
        bundle_sha256=bundle_sha,
        supplement_sha256=supplement_sha,
        receipt_sha256=receipt_sha,
        exposure_sha256=exposure_sha,
        forecast_child=child,
    )


__all__ = [
    "CONSUMER_CUTOFF", "EVENT_ID", "KICKOFF", "PRODUCER_DEADLINE", "source_event_id",
    "ReadinessFamily", "build", "build_forecast_child", "write_forecast_index",
    "write_forecast_wrapper", "write_supplement", "write_supplement_index",
]
