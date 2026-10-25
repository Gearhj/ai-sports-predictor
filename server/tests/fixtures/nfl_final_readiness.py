"""A synthetic NFL FINAL readiness family in the AUTHENTIC operational-V2 shapes.

WHY THIS EXISTS
---------------
The readiness joins have to be exercised against many states the producer will
never deliberately publish -- an absent sidecar, a superseded forecast, a grant
that moved, a boundary missed by ninety seconds. Model Studio's vendored
fixture (``tests/fixtures/model_studio_readiness_v2``) is ONE positive family
and cannot be re-parameterised, so this module builds the same family
synthetically: discovery index, forecast bundle, paper-eligibility supplement,
readiness index, receipt, availability sidecar and exposure object, with real
content addresses over real canonical bytes.

Nothing here is authentic production evidence and none of it is ever written
into the configured exchange root; ``build`` writes only under the destination
it was handed.

WHAT CHANGED, AND WHY THE OLD SHAPES WERE WRONG
-----------------------------------------------
An earlier revision of this module guessed the field spelling from the contract
prose, because no producer had emitted one of these documents yet. Almost every
guess was wrong, and the vendored bytes settled it:

* the ready index is a MAP keyed by forecast id (``receipts``), not a list of
  ``entries``, and it carries no exposure status at all -- status lives only on
  the exposure object;
* the availability sidecar is a BARE ``{bytes, path, sha256}`` reference with no
  schema wrapper, no ``baseReceiptSha256`` and no exposure path field: what
  binds it to a receipt is its own FILENAME plus the exposure's
  ``readyReceiptRef``;
* the producer revision is nested at ``receipt["engine"]["producerRevision"]``,
  because the revision is part of the engine identity, not of the document;
* the snapshots are content-addressed REFERENCES to separate frozen objects,
  not inline copies -- which is strictly stronger, since both parties can cite
  the same address;
* there is no schema-hash field on the receipt. The pin is over the CONTRACT
  FILE (``READINESS_CONSUMER_SCHEMA.json``), which is why ``SCHEMA_SHA256``
  below is computed from the vendored schema rather than invented here.

The shapes emitted below are therefore validated twice over: the joins accept
them, and (in the gate tests, which pin ``SCHEMA_SHA256``) Model Studio's own
field-shape schema accepts them field for field.

WHAT IS REAL AND WHAT IS SYNTHETIC
----------------------------------
REAL: the forecast wrapper and the supplement. The wrapper is a genuine
``ASP_SPORTS_FORECAST_EXCHANGE_V1`` bundle that passes the deployed
``verify_and_load_sports_bundle`` unmodified, and the supplement passes the
deployed ``verify_supplement`` in full, including its manifest-identity address.

SYNTHETIC: the identifiers, the clocks and the declared absolute paths.
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

#: Model Studio's reviewed field-shape contract, vendored beside this module.
#:
#: THE PIN IS OVER THIS FILE, not over a field inside any receipt. An earlier
#: revision of this fixture invented a ``schemaSha256`` receipt field and a hash
#: to go in it; no authentic receipt carries one, and the consumer hashes the
#: contract from disk instead. So the fixture publishes the contract's real hash
#: and lets a test pin it honestly.
SCHEMA_PATH = (Path(__file__).parent / "model_studio_readiness_v2"
               / "READINESS_CONSUMER_SCHEMA.json")
SCHEMA_SHA256 = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()
#: Retained under its old name so callers that pinned "the schema hash" keep
#: working. It no longer means "a hash the receipt declares" -- there is no such
#: field -- it means "the hash of the reviewed contract file".
RECEIPT_SCHEMA_SHA256 = SCHEMA_SHA256

SUPPLEMENT_ROOT = "nfl-labs-supplements"
SUPPLEMENT_INDEX_FILENAME = "nfl-labs-supplement-index.json"
SUPPLEMENT_INDEX_SCHEMA = "AISP_NFL_LABS_SUPPLEMENT_INDEX_V1"
FORECAST_FAMILY = "aisp-daily-forecast-bundles"
FORECAST_INDEX_FILENAME = "nfl-2026-research-shadow-index.json"
FORECAST_INDEX_SCHEMA = "ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V2"

#: The receipt's own status, taken verbatim from the authentic document. It is
#: recorded, never read as a gate: the positive value the consumer requires is
#: the EXPOSURE's status, below.
RECEIPT_STATUS = "EXACT_PAIR_VALIDATED_AWAIT_EXPOSURE_SIDECAR"
TIMING_POLICY_VERSION = "NFL_FINAL_DELIVERY_T60_T21_T15_V1"
SUPPLEMENT_DISPOSITION = "PUBLISHED_NEW_EXACT_SUCCESSOR"

#: What the exposure's ``readyAvailableAtUtc`` actually means, in the producer's
#: own words. It is an observation of the READY-INDEX REPLACE, not of the
#: sidecar file becoming readable -- which is why sidecar readability can never
#: be a timing failure for this consumer.
CLOCK_MEANING = (
    "ACTUAL_OBSERVATION_AFTER_READY_INDEX_REPLACE_NOT_SIDECAR_FILE_EXPOSURE")

#: The obligations the producer restates to the consumer, verbatim in spirit.
CONSUMER_REQUIREMENTS = [
    "Matching positive exposure sidecar is mandatory",
    "Reverify exact forecast and grant plus current membership",
    "Actual consumer observation/import and checkpoint must meet T-15",
    "No inferred grant, no CURRENT fallback, no cross-file atomicity claim",
]

#: Where the producer says it wrote these files. NOTHING resolves through it.
#:
#: Every authentic reference carries an absolute path on the machine that emitted
#: it, and the consumer deliberately never follows one -- it resolves each object
#: by content address beneath the root it was configured with. Keeping a foreign,
#: non-existent prefix here means the synthetic family proves that too.
PRODUCER_WORKSPACE = r"D:\model-studio\synthetic-readiness-v1\workspace\exchange"

#: A three-cell joint distribution. Everything the forecast contract
#: cross-checks -- win/tie probabilities, expected scores, margins, totals,
#: spreads, over/unders and the most-likely table -- is DERIVED from it below,
#: so the fixture cannot drift out of agreement with itself.
_JOINT = ((24, 21, 0.5), (21, 24, 0.3), (21, 21, 0.2))


def _hex(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _producer_path(*parts: str) -> str:
    """An absolute-looking path on the producer's machine, for a reference."""

    return "\\".join((PRODUCER_WORKSPACE, *parts))


def _ref(raw: bytes, declared_path: str) -> dict[str, Any]:
    """One ``{bytes, path, sha256}`` reference over real bytes.

    ``bytes`` and ``sha256`` are computed, never asserted: the consumer checks
    both against the object it finds at the address, so a hand-written length
    would simply be a refusal.
    """

    return {"bytes": len(raw), "path": declared_path,
            "sha256": hashlib.sha256(raw).hexdigest()}


def _write_object(root: Path, name: str, document: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Store one readiness document at its own content address.

    ``objects/sha256/<sha-of-canonical-bytes>/<name>.json``. The consumer
    re-hashes what it reads, so the bytes written here are the canonical ones or
    nothing verifies at all.
    """

    raw = canonical_json_bytes(document)
    sha = hashlib.sha256(raw).hexdigest()
    directory = Path(root) / READINESS_ROOT / OBJECTS_DIR / sha
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(raw)
    return sha, _ref(raw, _producer_path(READINESS_ROOT, "objects", "sha256", sha, name))


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
        # These eleven fields are EXACTLY the engine identity the readiness
        # receipt carries, which is why the receipt can reuse this object
        # verbatim: the reviewed schema forbids any other field in `engine`.
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


def forecast_membership(
    current: list[str], *, historical: list[str] | None = None,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    """The ``forecastCurrentMembership`` document, exactly as the schema shapes it.

    Built separately from writing it, because the SAME document is both the
    mutable discovery locator on disk and (frozen, at its own content address)
    the receipt's ``currentLocatorSnapshotRef``.
    """

    current_sorted = sorted(set(current))
    historical_sorted = sorted(set(historical or []))
    return {
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


def write_forecast_index(
    root: Path, current: list[str], *, historical: list[str] | None = None,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    """The MUTABLE discovery locator. It carries no immutable authority."""

    index = forecast_membership(current, historical=historical, updated_at=updated_at)
    path = Path(root) / FORECAST_FAMILY / FORECAST_INDEX_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(index))
    return index


def write_supplement(
    root: Path, *, forecast_id: str, event_id: str = EVENT_ID,
    published: datetime | None = None, source_cutoff: datetime | None = None,
) -> str:
    """A content-addressed paper-eligibility grant that the DEPLOYED validator accepts.

    THE ADDRESS IS NOT THE HASH OF ``supplement.json``.
    ---------------------------------------------------
    A supplement's package address is the canonical hash of its MANIFEST
    IDENTITY -- the manifest minus ``supplementSha256`` -- which is what the
    receipt's ``supplementAddress`` names. An earlier revision of this fixture
    hashed the payload file instead, which no authentic package would satisfy.

    The whole document is built, not just the parts the readiness joins read,
    because ``_verify_grant_currently_named`` calls ``verify_supplement`` in
    full: governance, artifact hashes, contract version and the paper-eligibility
    summaries are all re-checked there.
    """

    published_at = published or (KICKOFF - timedelta(minutes=23))
    cutoff = source_cutoff or (KICKOFF - timedelta(minutes=35))
    distributions = _derived_distributions()
    supplement_id = (
        f"nfl-synthetic-paper-eligibility:AISP_NFL_PAPER_POLICY_V1:NFL:2026:"
        f"{event_id}:v1")
    governance = {
        "status": "RESEARCH_SHADOW",
        "researchShadowOnly": True,
        "executionAllowedInAisp": False,
        "modelPromotionAllowed": False,
        "wageringAuthority": False,
    }
    supplement = {
        "schemaVersion": "AISP_RESEARCH_SUPPLEMENT_V1",
        "supplementId": supplement_id,
        "kind": "PAPER_RESEARCH_ELIGIBILITY",
        "sport": "NFL",
        "artifactVersion": "ASP_NFL_PAPER_RESEARCH_ELIGIBILITY_V1",
        "governance": governance,
        "binding": {
            "eventId": event_id,
            "engineId": "NFL_M1_XGBOOST",
            "forecastId": forecast_id,
            "modelArtifactSha256": NFL_MODEL_BINARY_SHA256,
        },
        "sourceCutoffUtc": _iso(cutoff),
        "publishedUtc": _iso(published_at),
        "simulation": {"kind": "NONE", "count": 0},
        "summaries": {
            "authorization": {
                "experimentId": "AISP_NFL_PAPER_POLICY_V1:NFL:2026",
                "policyVersion": "AISP_NFL_PAPER_POLICY_V1",
                "mode": "VIRTUAL_RESEARCH_ONLY",
                "consumerVirtualExperimentAuthorized": True,
                "realMoneyAuthorized": False,
                "realMoneyExecutionAllowed": False,
            },
            "frozenConfiguration": {
                "forecastId": forecast_id,
                "eventId": event_id,
                "executionAllowed": False,
                "modelPromotionAllowed": False,
                # The three masses must be a distribution, so they come from
                # the same joint table the forecast child was derived from.
                "homeWinProbability": distributions["homeWinProbability"],
                "awayWinProbability": distributions["awayWinProbability"],
                "tieProbability": distributions["tieProbability"],
            },
            "probabilitySemantics": {
                "units": "FRACTION_0_TO_1",
                "settlementMustBeExplicit": True,
                "tieSettlementForThisExperiment": "STAKE_REFUNDED_PUSH",
            },
        },
    }
    payload = canonical_json_bytes(supplement)
    identity = {
        "schemaVersion": "AISP_RESEARCH_SUPPLEMENT_MANIFEST_V1",
        "contractVersion": "AISP_RESEARCH_SUPPLEMENT_V1",
        "supplementId": supplement_id,
        "kind": "PAPER_RESEARCH_ELIGIBILITY",
        "sport": "NFL",
        "governance": governance,
        "artifacts": [
            {"file": "supplement.json",
             "sha256": hashlib.sha256(payload).hexdigest(),
             "bytes": len(payload)},
        ],
    }
    address = canonical_sha256(identity)
    directory = Path(root) / SUPPLEMENT_ROOT / "sha256" / address
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "supplement.json").write_bytes(payload)
    (directory / "manifest.json").write_bytes(
        canonical_json_bytes({**identity, "supplementSha256": address}))
    return address


def supplement_membership(
    addresses: list[str], *, updated_at: datetime | None = None,
) -> dict[str, Any]:
    """The ``supplementCurrentMembership`` document.

    The reviewed schema allows these three fields and no others, so the older
    two-field form this fixture used to emit would now be refused outright.
    """

    return {
        "schemaVersion": SUPPLEMENT_INDEX_SCHEMA,
        "currentSupplementSha256s": sorted(set(addresses)),
        "updatedAtUtc": _iso(updated_at or (KICKOFF - timedelta(minutes=23))),
    }


def write_supplement_index(
    root: Path, addresses: list[str], *, updated_at: datetime | None = None,
) -> dict[str, Any]:
    index = supplement_membership(addresses, updated_at=updated_at)
    path = Path(root) / SUPPLEMENT_ROOT / SUPPLEMENT_INDEX_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(index))
    return index


def ready_index(forecast_id: str, entry: dict[str, Any], *,
                updated_at: datetime) -> dict[str, Any]:
    """The ready index, live or frozen -- they are the same shape.

    A MAP keyed by forecast id, not a list of entries: "the index maps this
    forecast twice" cannot be expressed, because a duplicate key does not
    survive JSON parsing. The only ambiguity left to refuse is a key that
    differs from the selected forecast by case or whitespace.
    """

    return {
        "schemaVersion": INDEX_SCHEMA,
        "receipts": {forecast_id: entry} if entry is not None else {},
        "updatedAtUtc": _iso(updated_at),
    }


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
    #: The frozen snapshot objects, so a test can reason about join 2 without
    #: re-deriving their addresses.
    ready_snapshot_sha256: str = ""
    forecast_snapshot_sha256: str = ""
    supplement_snapshot_sha256: str = ""

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
    snapshot_forecast_current: bool = True,
    ready_snapshot_receipt: str | None = "self",
) -> ReadinessFamily:
    """Write one complete, correctly shaped readiness family under ``root``.

    ``root`` is the caller's temporary directory. Nothing is written anywhere
    else, and in particular nothing is written into the configured exchange
    root: the tests pass ``tmp_path`` and the production tree is never touched.

    KEYWORDS, AND WHAT EACH ONE STILL MEANS
    ---------------------------------------
    ``exposure_status``      the exposure object's ``status``. Unchanged.
    ``with_availability``    whether the bare sidecar reference is written.
    ``supplement_current``   whether the LIVE grant index still names the grant.
    ``forecast_current``     whether the LIVE forecast locator still names the
                             wrapper (it moves to ``historical`` if not).
    ``ready_index_observed`` the producer's ``readyValidatedAtUtc``. This is the
                             clock that used to be called
                             ``currentIndexObservedAtUtc``; the authentic
                             receipt has no such field and states its ready-index
                             validation as ``readyValidatedAtUtc`` instead.
    ``grant_observed``       the supplement block's publication clocks
                             (``declaredPublishedUtc`` / ``indexVisibleAtUtc`` /
                             ``packageVisibleAtUtc``), which replaced the old
                             ``grantObservedAtUtc``.
    ``sidecar_first_observed``
                             the exposure's ``readyAvailableAtUtc``. The fields
                             this used to drive -- ``sidecarFirstObservedAtUtc``,
                             ``proofObservedAfterProducerBoundary``,
                             ``consumerCutoffPassedAtProofObservation`` -- DO NOT
                             EXIST in the authentic exposure. ``readyAvailableAtUtc``
                             is its nearest equivalent and carries the same
                             point: it is a producer observation clock that the
                             consumer records and never gates on, which the
                             document says itself via ``clockMeaning``.
    ``snapshot_forecast_current``
                             whether the FROZEN forecast-index snapshot lists the
                             bundle. Join 2's negative case; the live locator is
                             untouched.
    ``ready_snapshot_receipt``
                             what the frozen ready-index snapshot names for this
                             forecast: ``"self"`` (this receipt), ``None`` (no
                             entry at all), or an explicit sha256 to name some
                             other receipt.
    """

    root = Path(root)
    child = build_forecast_child(kickoff=kickoff)
    forecast_id = child["forecastId"]
    children = [child, *(extra_children or [])]
    bundle_sha, bundle_dir = write_forecast_wrapper(root, children)
    bundle_ref = _ref(
        (bundle_dir / "bundle.json").read_bytes(),
        _producer_path(FORECAST_FAMILY, "sha256", bundle_sha, "bundle.json"))

    deadline = kickoff - timedelta(minutes=21)
    grant_seen = grant_observed or (kickoff - timedelta(minutes=23))
    index_seen = ready_index_observed or (kickoff - timedelta(minutes=22))
    locator_visible = kickoff - timedelta(minutes=24)
    locator_checked = kickoff - timedelta(minutes=23)

    # ORDER MATTERS HERE.
    #
    # The discovery locators are first written in the state they were in AT
    # PUBLICATION, and the receipt snapshots THAT. Only afterwards is the
    # present-tense degradation applied. Snapshotting an already-degraded index
    # would make every join-3 negative case fail at join 2 instead, which is
    # the opposite of what these fixtures are for: the whole point of a
    # superseded forecast is that the ORIGINAL snapshot was valid and the
    # PRESENT locator no longer names it.
    write_forecast_index(root, [bundle_sha], historical=[], updated_at=locator_visible)
    supplement_sha = write_supplement(
        root, forecast_id=forecast_id, event_id=child["eventId"], published=grant_seen)
    write_supplement_index(root, [supplement_sha], updated_at=grant_seen)

    forecast_index_snapshot = json.loads(
        (root / FORECAST_FAMILY / FORECAST_INDEX_FILENAME).read_bytes())
    supplement_index_snapshot = json.loads(
        (root / SUPPLEMENT_ROOT / SUPPLEMENT_INDEX_FILENAME).read_bytes())

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
        write_forecast_index(root, [other_sha], historical=[bundle_sha],
                             updated_at=locator_visible)
    if not supplement_current:
        write_supplement_index(root, [], updated_at=grant_seen)
    if not snapshot_forecast_current:
        # A membership the producer never froze. The LIVE locator still names
        # the bundle, so the refusal is attributable to join 2 alone.
        forecast_index_snapshot = forecast_membership([], updated_at=locator_visible)

    forecast_snapshot_sha, forecast_snapshot_ref = _write_object(
        root, "forecast-index.json", forecast_index_snapshot)
    supplement_snapshot_sha, supplement_snapshot_ref = _write_object(
        root, "supplement-index.json", supplement_index_snapshot)

    supplement_dir = root / SUPPLEMENT_ROOT / "sha256" / supplement_sha
    supplement_ref = _ref(
        (supplement_dir / "supplement.json").read_bytes(),
        _producer_path(SUPPLEMENT_ROOT, "sha256", supplement_sha, "supplement.json"))
    manifest_ref = _ref(
        (supplement_dir / "manifest.json").read_bytes(),
        _producer_path(SUPPLEMENT_ROOT, "sha256", supplement_sha, "manifest.json"))

    receipt = {
        "schemaVersion": RECEIPT_SCHEMA,
        "status": RECEIPT_STATUS,
        "eventId": child["eventId"],
        "forecastId": forecast_id,
        "snapshotType": child["snapshotType"],
        "snapshotVersion": child["snapshotVersion"],
        "scheduledStartUtc": _iso(kickoff),
        "availableAsOfUtc": child["availableAsOfUtc"],
        "forecastCutoffUtc": child["forecastCutoffUtc"],
        "sourceCutoffUtc": child["sourceCutoffUtc"],
        "producerDeadlineUtc": _iso(deadline),
        "readyValidatedAtUtc": _iso(index_seen),
        "timingPolicyVersion": TIMING_POLICY_VERSION,
        "consumerRequirements": list(CONSUMER_REQUIREMENTS),
        # The whole engine identity in one object, revision included. The
        # revision is NOT a top-level receipt field; the consumer reads it at
        # engine.producerRevision and a top-level copy rescues nothing.
        "engine": dict(child["engine"]),
        "bundleRef": bundle_ref,
        "currentLocatorDiscoveryPath": _producer_path(
            FORECAST_FAMILY, FORECAST_INDEX_FILENAME),
        "currentLocatorSnapshotRef": forecast_snapshot_ref,
        # The receipt names the frozen locator TWICE: once as the immutable
        # reference above, once as the locator it actually observed. The
        # consumer requires them to be the same object, so locatorSha256 is the
        # snapshot's address and nothing else.
        "forecastLocatorExposure": {
            "locatorPath": _producer_path(FORECAST_FAMILY, FORECAST_INDEX_FILENAME),
            "locatorSha256": forecast_snapshot_sha,
            "notAfterUtc": _iso(deadline),
            "observedAfterDeadline": locator_visible > deadline,
            "publicationCheckedAtUtc": _iso(locator_checked),
            "visibleAtUtc": _iso(locator_visible),
        },
        "supplement": {
            "bundleRef": bundle_ref,
            "declaredPublishedUtc": _iso(grant_seen),
            "eventId": child["eventId"],
            "forecastId": forecast_id,
            "indexDiscoveryPath": _producer_path(
                SUPPLEMENT_ROOT, SUPPLEMENT_INDEX_FILENAME),
            "indexRef": supplement_snapshot_ref,
            "indexVisibleAtUtc": _iso(grant_seen),
            "manifestRef": manifest_ref,
            "packageVisibleAtUtc": _iso(grant_seen),
            "publicationDisposition": SUPPLEMENT_DISPOSITION,
            # The PACKAGE address -- the canonical hash of the manifest
            # identity -- not the hash of supplement.json.
            "supplementAddress": supplement_sha,
            "supplementRef": supplement_ref,
        },
    }
    receipt_bytes = canonical_json_bytes(receipt)
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    receipt_dir = root / READINESS_ROOT / OBJECTS_DIR / receipt_sha
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / "receipt.json").write_bytes(receipt_bytes)
    receipt_ref = _ref(receipt_bytes, _producer_path(
        READINESS_ROOT, "objects", "sha256", receipt_sha, "receipt.json"))

    sidecar_path = _producer_path(
        READINESS_ROOT, AVAILABILITY_DIR, f"{receipt_sha}.json")
    entry = {"exposureReferencePath": sidecar_path, "receiptRef": receipt_ref}

    # The frozen ready-index snapshot. It must name THIS receipt, or a positive
    # exposure could be carried across from an earlier readiness pass.
    if ready_snapshot_receipt is None:
        snapshot_entry = None
    elif ready_snapshot_receipt == "self":
        snapshot_entry = entry
    else:
        snapshot_entry = {
            "exposureReferencePath": _producer_path(
                READINESS_ROOT, AVAILABILITY_DIR, f"{ready_snapshot_receipt}.json"),
            "receiptRef": {**receipt_ref, "sha256": ready_snapshot_receipt},
        }
    ready_snapshot_sha, ready_snapshot_ref = _write_object(
        root, "index.json",
        ready_index(forecast_id, snapshot_entry, updated_at=index_seen))

    # ``readyAvailableAtUtc`` is a PRODUCER observation of the ready-index
    # replace. The consumer records it and never gates on it, which is what
    # clockMeaning says in the producer's own words.
    ready_available = sidecar_first_observed or (index_seen + timedelta(seconds=30))
    exposure = {
        "schemaVersion": EXPOSURE_SCHEMA,
        "status": exposure_status,
        "eventId": child["eventId"],
        "forecastId": forecast_id,
        "producerDeadlineUtc": _iso(deadline),
        "readyAvailableAtUtc": _iso(ready_available),
        "clockMeaning": CLOCK_MEANING,
        # Both of these are assertions the consumer requires: the document must
        # NOT claim to certify sidecar exposure, and must require the consumer
        # to establish its own availability observation.
        "certifiesSidecarExposureBeforeProducerDeadline": False,
        "consumerObservedAvailabilityRequired": True,
        "readyIndexSnapshotRef": ready_snapshot_ref,
        "readyReceiptRef": receipt_ref,
    }
    exposure_sha, exposure_ref = _write_object(root, "exposure.json", exposure)

    availability_dir = root / READINESS_ROOT / AVAILABILITY_DIR
    availability_dir.mkdir(parents=True, exist_ok=True)
    if with_availability:
        # A BARE reference and nothing else. The sidecar carries no schema
        # version and no base-receipt field; what binds it to the receipt is its
        # own filename plus the exposure's readyReceiptRef.
        (availability_dir / f"{receipt_sha}.json").write_bytes(
            canonical_json_bytes(exposure_ref))

    (root / READINESS_ROOT).mkdir(parents=True, exist_ok=True)
    (root / READINESS_ROOT / READINESS_INDEX_FILENAME).write_bytes(
        canonical_json_bytes(ready_index(forecast_id, entry, updated_at=index_seen)))

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
        ready_snapshot_sha256=ready_snapshot_sha,
        forecast_snapshot_sha256=forecast_snapshot_sha,
        supplement_snapshot_sha256=supplement_snapshot_sha,
    )


__all__ = [
    "CLOCK_MEANING", "CONSUMER_CUTOFF", "EVENT_ID", "KICKOFF", "PRODUCER_DEADLINE",
    "PRODUCER_REVISION", "RECEIPT_SCHEMA_SHA256", "SCHEMA_PATH", "SCHEMA_SHA256",
    "source_event_id", "ReadinessFamily", "build", "build_forecast_child",
    "forecast_membership", "ready_index", "supplement_membership",
    "write_forecast_index", "write_forecast_wrapper", "write_supplement",
    "write_supplement_index",
]
