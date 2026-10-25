"""Forward NHL deliveries (interface 0bcf/adf270): discovery proves everything before the accepted importer runs.

``_build`` writes one complete synthetic forward delivery the way the producer's
v2 workflow writes it (run_once.py, code/publish_current.py and
workflow_guards.py under source manifest 0bcf): the prior exchange state (the
genesis publication, or the state an earlier build left), the dated workspace
records, the new objects, the successor manifest and pointer, the handoff, the
post-exposure witness, the discovery index and the completion record. A case
mutates exactly one record before it is written, so every downstream binding is
recomputed and the refusal is the targeted one.

When the real producer package is on this machine, two more tests prove the
production pins and that the builder's shapes ARE the producer's own (its pure
guard functions, and the record keys its source actually writes).
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import importlib.util
import json
import pathlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

import pytest

from app.nhl import contract as c
from app.nhl import forward_delivery as fd
from app.nhl.forward_delivery import (
    PRODUCTION_INTERFACE, DeliveryNotReady, DeliveryRefused, ForwardInterface, Pin, consume_forward_delivery, discover)
from tests import nhl_fixtures as fx

MODEL = {"modelId": "NHL_GEN1_REGULATION_POISSON", "modelVersion": "1.0.0",
         "configurationSha256": "3f7981aac2bfee6f3d05b7e1d099a3342cc9057d09e13b09aaf4c81b7f720417",
         "trainingCutoffUtc": None, "handoffSha256": None}
AUTH = {"wageringAuthority": False, "decisionAuthority": False, "positionAuthority": False,
        "modelPromotionAuthority": False}
MISSING = ["HOME_GOALIE_UNKNOWN", "AWAY_GOALIE_UNKNOWN", "LINEUPS_UNAVAILABLE", "INJURIES_UNAVAILABLE",
           "MARKET_PRICES_NOT_USED"]
SLATE = "2026-10-14"
T = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)          # 09:00 ET
CLOCK = {
    "review": T - timedelta(hours=2), "workflow_start": T + timedelta(seconds=5),
    "requested": T + timedelta(seconds=20), "asset": T - timedelta(hours=5), "observed": T + timedelta(seconds=30),
    "prepared": T + timedelta(seconds=60), "publication_start": T + timedelta(seconds=90),
    "object_started": T + timedelta(seconds=120), "object_done": T + timedelta(seconds=121),
    "manifest_done": T + timedelta(seconds=122), "pointer_done": T + timedelta(seconds=123),
    "handoff": T + timedelta(seconds=124), "witness": T + timedelta(seconds=125),
    "index": T + timedelta(seconds=126), "complete": T + timedelta(seconds=127),
}
NOW = T + timedelta(hours=1)
NEXT_DAY = {k: v + timedelta(days=1) for k, v in CLOCK.items()}
PRIOR_COMMITTED = "2026-10-10T19:30:48.114715Z"
PRIOR_GAMES = (("2026020072", "BOS", "NYR", datetime(2026, 10, 10, 20, 0, tzinfo=UTC)),
               ("2026020073", "EDM", "CGY", datetime(2026, 10, 10, 22, 0, tzinfo=UTC)))
NEW_GAMES = (("2026020106", "TOR", "MTL", datetime(2026, 10, 14, 23, 30, tzinfo=UTC)),
             ("2026020107", "COL", "DAL", datetime(2026, 10, 15, 0, 30, tzinfo=UTC)))
DAY2_GAMES = (("2026020110", "BOS", "TOR", datetime(2026, 10, 15, 23, 0, tzinfo=UTC)),
              ("2026020111", "EDM", "VGK", datetime(2026, 10, 16, 2, 0, tzinfo=UTC)))
ASSET = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/nhl_schedules/nhl_schedule_2027.csv"


def z(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _ref(path: pathlib.Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"path": str(path.resolve()), "sha256": _sha(raw), "bytes": len(raw)}


def _write(path: pathlib.Path, value: Any) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(c.canonical_bytes(value))
    return _ref(path)


def _put(root: pathlib.Path, family: str, name: str, value: Any) -> dict[str, Any]:
    raw = c.canonical_bytes(value)
    rel = f"{family}/sha256/{_sha(raw)}/{name}"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(raw)
    return {"path": rel, "sha256": _sha(raw), "bytes": len(raw)}


@dataclass
class Built:
    interface: ForwardInterface
    run_root: pathlib.Path
    base: pathlib.Path
    docs: dict[str, dict[str, Any]] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)
    prior_manifest: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    handoff: dict[str, Any] = field(default_factory=dict)
    added: list[dict[str, Any]] = field(default_factory=list)


def _doc(source_id: str, home: str, away: str, start: datetime, *, observed: datetime, created: datetime,
         evidence: dict[str, Any]) -> dict[str, Any]:
    doc = fx.forecast(fx.game_block(source_id, home, away, start), created=created, home_mu=3.1, away_mu=2.8,
                      ot_home=0.52, goalies={"home": fx.goalie("UNKNOWN"), "away": fx.goalie("UNKNOWN")},
                      missing=list(MISSING))
    doc.update(model=dict(MODEL), sourceCutoffUtc=z(observed), sourceRetrievedAtUtc=z(observed),
               createdAtUtc=z(created), publishedAtUtc=None)
    doc["provenance"]["producerEvidenceRef"] = evidence
    return doc


def _genesis(exchange: pathlib.Path, hook: Callable[[str, Any], Any]) -> None:
    """The closed historical publication the first forward run replaces (all row clocks null, like bedd8150)."""

    evidence = _put(exchange, "evidence", "producer-receipt.json",
                    {"schema": "NHL_GEN1_PRIVATE_PRODUCER_EVIDENCE_V1", "slate": "2026-10-10"})
    rows = []
    for sid, home, away, start in PRIOR_GAMES:
        doc = c.with_identity(_doc(sid, home, away, start, observed=start - timedelta(hours=3),
                                   created=start - timedelta(hours=2), evidence=evidence))
        ref = _put(exchange, "objects", "forecast.json", doc)
        rows.append({"relativePath": ref["path"], "sha256": ref["sha256"], "bytes": ref["bytes"],
                     "forecastId": doc["forecastId"], "canonicalGameId": doc["game"]["canonicalGameId"],
                     "publishedAtUtc": None})
    manifest = hook("prior_manifest", {
        "schema": c.FORECAST_MANIFEST_SCHEMA, "manifestCreatedAtUtc": PRIOR_COMMITTED, "producer": "Model Studio",
        "modelId": MODEL["modelId"], "modelVersion": MODEL["modelVersion"], "researchStatus": "RESEARCH_ONLY",
        "publicationReceipt": {"committedAtUtc": PRIOR_COMMITTED}, "objects": rows, "counts": {"forecasts": len(rows)}})
    _write(exchange / "current.json", {"schema": c.POINTER_SCHEMA, "forecastManifest":
                                       _put(exchange, "manifests", "manifest.json", manifest), "resultManifest": None})


def _build(base: pathlib.Path, hooks: dict[str, Callable[..., Any]] | None = None, new_games: tuple = NEW_GAMES,
           clock: dict[str, datetime] | None = None, slate: str = SLATE, chain_from: Built | None = None) -> Built:
    hooks, k = hooks or {}, {**CLOCK, **(clock or {})}

    def hook(name: str, value: Any) -> Any:
        if name not in hooks:
            return value
        value = copy.deepcopy(value)            # never mutate a reference another record shares
        out = hooks[name](value)
        return value if out is None else out

    base = base.resolve()
    outputs, exchange = base / "outputs", base / "exchange" / "nhl-gen1-research-v1"
    workflow, run_root = outputs / "nhl-forward-publication-workflow-test", outputs / (
        "nhl-current-slate-producer-" + slate.replace("-", "") + "-v1")
    if chain_from is None:
        _genesis(exchange, hook)
        genesis = _sha((exchange / "current.json").read_bytes())
    else:
        genesis = chain_from.interface.genesis_pointer_sha256
    original_pointer_ref = _ref(exchange / "current.json")
    original_pointer = json.loads((exchange / "current.json").read_bytes())
    prior_ref = original_pointer["forecastManifest"]
    prior_manifest = json.loads((exchange / prior_ref["path"]).read_bytes())

    # The reviewed workflow package.
    schedule = [{"game_id": sid, "season_full": "20262027", "game_type": "R", "game_date": slate,
                 "game_time": z(start), "home_team_abbr": home, "away_team_abbr": away, "game_state": "FUT",
                 "venue": "Arena"} for sid, home, away, start in new_games]
    interface_ref = _write(workflow / "INTERFACE_CONTRACT.json", hook("interface", {
        "schema": "NHL_FORWARD_SAME_CONSUMER_INTERFACE_CONTRACT_V2", "consumerOwner": "d7150e32-ed94-41c7-bb5e-c7f85221ed41",
        "handoffSchema": "NHL_CURRENT_PRODUCER_HANDOFF_V1", "discoveryIndexPath": str(workflow / "DELIVERY_INDEX.json"),
        "required": [], "actualConsumerAcceptanceSupplied": False}))
    baseline_ref = _write(workflow / "TARGET_SCHEDULE_BASELINE.json", hook("baseline", {
        "schema": "NHL_FORWARD_TARGET_SCHEDULE_BASELINE_V1", "dates": {slate: [dict(r) for r in schedule]}}))
    publisher = workflow / "code" / "publish_current.py"
    publisher.parent.mkdir(parents=True, exist_ok=True)
    publisher.write_bytes(b"# stand-in for the reviewed publisher\n")
    publisher_ref = _ref(publisher)
    source_ref = _write(workflow / "SOURCE_MANIFEST.json", hook("source_manifest", {
        "schema": "NHL_FORWARD_REVIEW_SOURCE_MANIFEST_V2", "sourceFiles": [interface_ref, baseline_ref, publisher_ref],
        "modelId": MODEL["modelId"], "modelVersion": MODEL["modelVersion"], "modelSha256": MODEL["configurationSha256"],
        "authority": dict(AUTH), "publicationRootConstant": exchange.as_posix()}))
    review_ref = _write(workflow / "MAIN_SOURCE_ACCEPTANCE.json", hook("review", {
        "status": "PASS_NHL_FORWARD_WORKFLOW_EXACT_SOURCE", "sourceManifestSha256": source_ref["sha256"],
        "scope": "PRIVATE_RESEARCH_EXCHANGE_ONLY_NO_PAPER_AUTHORITY", "publisherRef": publisher_ref,
        "recordedAtUtc": z(k["review"])}))

    # Dated workspace: start record -> capture -> preparation.
    _write(run_root / "WORKFLOW_START.json", hook("workflow_start", {
        "schema": "NHL_FORWARD_WORKFLOW_START_V1", "startedAtUtc": z(k["workflow_start"]),
        "scopeRef": {"path": str(workflow / "NATIVE_EXECUTION_SCOPE.json"), "sha256": "1" * 64, "bytes": 1},
        "sourceReviewRef": review_ref, "sourceManifestRef": source_ref,
        "ownerSid": "S-1-5-21-2872558424-197641707-2663611391-1002", "slateDate": slate, "authority": dict(AUTH)}))
    capture = hook("capture", {
        "url": ASSET, "requestStartedAtUtc": z(k["requested"]), "responseCompletedAtUtc": z(k["observed"]),
        "status": 200, "headers": {}, "authentication": "NONE", "sourceRetry": 0, "addedCostUsd": 0,
        "expectedSha256": "a" * 64, "publisherDigestVerified": True, "assetId": 1, "assetUpdatedAtUtc": z(k["asset"]),
        "publicMetadataQuota": {}, "requestCount": 5, "totalResponseBytes": 420000})
    capture_raw = json.dumps(capture, sort_keys=True, separators=(",", ":")).encode()
    capture_path = run_root / "receipts" / "sha256" / _sha(capture_raw) / "receipt.json"
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    capture_path.write_bytes(capture_raw)
    capture_ref = _ref(capture_path)
    input_ref = _write(run_root / "CURRENT_SCHEDULE_AND_MODEL_INPUTS.json", hook("inputs", {
        "schema": "NHL_CURRENT_MODEL_INPUTS_V1", "createdAtUtc": z(k["prepared"]), "sourceCaptureReceipt": capture_ref,
        "sourceAssetUpdatedAtUtc": z(k["asset"]), "sourceRetrievedAtUtc": z(k["observed"]),
        "games": [dict(r) for r in schedule], "outcomeColumnsLoaded": []}))
    qualification_ref = _write(run_root / "SOURCE_QUALIFICATION.json", hook("qualification", {
        "schema": "NHL_CURRENT_SOURCE_QUALIFICATION_V1", "createdAtUtc": z(k["prepared"]),
        "actualCapturedAtUtc": z(k["observed"]), "sourceCaptureReceipt": capture_ref, "addedCostUsd": 0,
        "paidEntitlementUsed": False}))

    # New immutable objects (prepared), then publication: start record, successor manifest, replaced pointer.
    evidence = _put(exchange, "evidence", "producer-receipt.json",
                    {"schema": "NHL_GEN1_PRIVATE_PRODUCER_EVIDENCE_V1", "slate": slate})
    built = Built(interface=None, run_root=run_root, base=base)  # type: ignore[arg-type]
    items = []
    for sid, home, away, start in new_games:
        doc = c.with_identity(hook("new_doc", _doc(sid, home, away, start, observed=k["observed"],
                                                   created=k["prepared"], evidence=evidence)))
        ref = _put(exchange, "objects", "forecast.json", doc)
        built.docs[doc["game"]["canonicalGameId"]] = doc
        items.append({"object": ref, "forecastId": doc["forecastId"], "canonicalGameId": doc["game"]["canonicalGameId"],
                      "scheduledStartUtc": z(start)})
    items = hook("items", items)
    _write(run_root / "PUBLICATION_START.json", hook("publication_start", {
        "startedAtUtc": z(k["publication_start"]), "preparedPackageRef": input_ref, "independentReviewRef": review_ref,
        "publisherRef": publisher_ref, "originalPointerRef": original_pointer_ref, "eligibleCount": len(items),
        "omitted": [], "providerRequests": 0, "newScheduler": False}))
    preserved = [{**r, "publishedAtUtc": r["publishedAtUtc"] if r.get("publishedAtUtc") is not None
                  else prior_manifest["publicationReceipt"]["committedAtUtc"]} for r in prior_manifest["objects"]]
    added = [{"relativePath": i["object"]["path"], "sha256": i["object"]["sha256"], "bytes": i["object"]["bytes"],
              "forecastId": i["forecastId"], "canonicalGameId": i["canonicalGameId"],
              "publishedAtUtc": z(k["object_done"])} for i in items]
    manifest = hook("manifest", {
        "schema": c.FORECAST_MANIFEST_SCHEMA, "manifestCreatedAtUtc": z(k["object_done"]), "producer": "Model Studio",
        "modelId": MODEL["modelId"], "modelVersion": MODEL["modelVersion"], "researchStatus": "RESEARCH_ONLY",
        "publicationReceipt": {"committedAtUtc": z(k["object_done"])}, "objects": preserved + added,
        "counts": {"forecasts": len(preserved) + len(added)}})
    manifest_ref = _put(exchange, "manifests", "manifest.json", manifest)
    _write(run_root / "ORIGINAL_CURRENT_POINTER.json", hook("original_pointer", original_pointer))
    pointer_ref = _write(exchange / "current.json", hook("pointer", {**original_pointer, "forecastManifest": manifest_ref}))

    receipt = hook("receipt", {
        "status": "CURRENT_PRIVATE_RESEARCH_PUBLISHED_IMPORT_PENDING", "publishedPackageRoot": str(exchange),
        "independentReviewRef": review_ref, "publisherRef": publisher_ref, "originalPointerRef": original_pointer_ref,
        "objectExposureStartedAtUtc": z(k["object_started"]), "objectExposureCompletedAtUtc": z(k["object_done"]),
        "manifestExposureCompletedAtUtc": z(k["manifest_done"]), "currentPointerExposureCompletedAtUtc": z(k["pointer_done"]),
        "manifest": manifest_ref, "pointer": pointer_ref, "newForecastCount": len(items),
        "preservedOriginalForecastCount": len(prior_manifest["objects"]), "currentManifestTotalCount": len(manifest["objects"]),
        "newForecasts": items, "omittedAtPublication": [], "consumerImport": "PENDING_EXACT_OWNER_ACK",
        "paperQualification": "NOT_CLAIMED", "providerRequests": 0, "addedCostUsd": 0, "newScheduler": False,
        "productionWrites": 0, **AUTH, "priorObjectsByteVerified": True, "priorManifestMembers": prior_manifest["objects"],
        "currentManifestMembers": manifest["objects"], "preservedResultManifest": original_pointer.get("resultManifest"),
        "priorManifestRef": prior_ref})
    receipt_ref = _write(run_root / "PUBLICATION_RECEIPT.json", receipt)

    games = []
    for item in items:
        d = built.docs.get(item["canonicalGameId"]) or next(iter(built.docs.values()))
        games.append({"canonicalGameId": item["canonicalGameId"], "sourceGameId": d["game"]["sourceGameId"],
                      "originalScheduledStartUtc": item["scheduledStartUtc"], "forecastId": item["forecastId"],
                      "forecastCreatedAtUtc": d["createdAtUtc"], "home": d["game"]["home"]["triCode"],
                      "away": d["game"]["away"]["triCode"], "homeTeamId": d["game"]["home"]["sourceTeamId"],
                      "awayTeamId": d["game"]["away"]["sourceTeamId"],
                      "object": {"relativePath": item["object"]["path"], "sha256": item["object"]["sha256"],
                                 "bytes": item["object"]["bytes"], "canonicalGameId": item["canonicalGameId"],
                                 "forecastId": item["forecastId"]},
                      "producerEvidence": d["provenance"]["producerEvidenceRef"]})
    handoff = hook("handoff", {
        "schema": "NHL_CURRENT_PRODUCER_HANDOFF_V1", "status": "CURRENT_PUBLISHED_PRODUCER_VERIFIED_CONSUMER_IMPORT_PENDING",
        "createdAtUtc": z(k["handoff"]), "exchangeRoot": str(exchange), "authority": dict(AUTH),
        "manifest": receipt["manifest"], "pointer": receipt["pointer"], "publicationReceipt": receipt_ref,
        "independentReviewRef": review_ref, "sourceQualificationRef": qualification_ref, "currentInputRef": input_ref,
        "sourceCaptureReceipt": capture_ref,
        "counts": {"newCurrentPublished": receipt["newForecastCount"],
                   "originalForecastsPreserved": receipt["preservedOriginalForecastCount"],
                   "currentManifestTotal": receipt["currentManifestTotalCount"], "newConsumerImported": None},
        "allPublicationClocksGenuinelyProspective": True, "forecastCutoff": "STRICTLY_BEFORE_EACH_ORIGINAL_SCHEDULED_START",
        "marketSemantics": {}, "games": games, "omittedAtPublication": receipt["omittedAtPublication"],
        "addedCostUsd": 0, "resultManifest": receipt["preservedResultManifest"], "consumerImport": "PENDING_EXACT_OWNER_ACK",
        "sourceManifestRef": source_ref, "publicationClockClaimScope": "NEW_FORECAST_MEMBERS_ONLY",
        "priorManifestRef": receipt["priorManifestRef"], "oldObjectsBytePreserved": receipt["priorObjectsByteVerified"]})
    handoff_ref = _write(run_root / "HANDOFF.json", handoff)
    witness_ref = _write(run_root / "HANDOFF_EXPOSURE_RECEIPT.json", hook("witness", {
        "schema": "NHL_HANDOFF_EXPOSURE_RECEIPT_V1", "status": "PROSPECTIVE_HANDOFF_EXPOSURE_VERIFIED",
        "handoff": handoff_ref, "exposureVerifiedAtUtc": z(k["witness"]), "publicationReceipt": receipt_ref,
        "authority": dict(AUTH)}))
    index_ref = _write(workflow / "DELIVERY_INDEX.json", hook("index", {
        "schema": "NHL_FORWARD_DELIVERY_INDEX_V1", "slateDate": slate, "createdAtUtc": z(k["index"]),
        "handoff": handoff_ref, "exposureReceipt": witness_ref, "publicationReceipt": receipt_ref,
        "authority": dict(AUTH)}))
    if "no_complete" not in hooks:
        _write(run_root / "WORKFLOW_COMPLETE.json", hook("complete", {
            "status": "PUBLISHED_HANDOFF_IMPORT_PENDING", "completedAtUtc": z(k["complete"]), "deliveryIndex": index_ref,
            "handoffExposureReceipt": witness_ref, "consumerImportProof": False, "authority": dict(AUTH)}))
    for name in hook("failures", []):
        _write(run_root / name, {"status": "STOP_NO_RETRY_NO_DELIVERY_ACCEPTANCE"})

    built.interface = ForwardInterface(
        outputs_root=outputs, workflow_root=workflow, exchange_root=exchange,
        source_manifest=Pin(workflow / "SOURCE_MANIFEST.json", source_ref["sha256"], source_ref["bytes"]),
        interface_contract=Pin(workflow / "INTERFACE_CONTRACT.json", interface_ref["sha256"], interface_ref["bytes"]),
        genesis_pointer_sha256=genesis)
    built.items, built.prior_manifest, built.manifest, built.handoff, built.added = (
        items, prior_manifest, manifest, handoff, added)
    return built


def _stored_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# ------------------------------------------------------------------ admitted --
def test_a_complete_delivery_is_proved_then_imported_exactly_once(session, tmp_path):
    from app.models import NhlImportReceipt, NhlResearchForecast

    built = _build(tmp_path)
    found = discover(built.interface, now=NOW)
    assert found["counts"] == {"new": 2, "preserved": 2, "total": 4} and found["originalPointer"]["genesis"] is True
    assert [m["canonicalGameId"] for m in found["newMembers"]] == ["nhl:game:2026020106", "nhl:game:2026020107"]
    assert found["clocks"]["earliestOriginalStartUtc"] == "2026-10-14T23:30:00+00:00"

    result = consume_forward_delivery(session, built.interface, now=NOW)
    assert result["status"] == "IMPORTED" and result["writes"] is True and not result["problems"]
    assert result["forecasts"]["IMPORTED"] == 4 and result["records"] == 4
    assert {r.manifest_sha256 for r in session.query(NhlImportReceipt)} == {found["manifest"]["sha256"]}
    # The stored publication clock of a new member is the object exposure the receipt records,
    # and the delivery itself was proved to be exposed before every original start.
    for forecast in session.query(NhlResearchForecast):
        if forecast.canonical_game_ref in {"nhl:game:2026020106", "nhl:game:2026020107"}:
            stored = _stored_utc(forecast.published_utc)
            assert stored == CLOCK["object_done"] and stored < _stored_utc(forecast.scheduled_start_utc)

    again = consume_forward_delivery(session, built.interface, now=NOW)
    assert again["status"] == "ALREADY_CONSUMED" and again["writes"] is False


def test_the_next_day_chains_from_the_pointer_the_first_day_left(session, tmp_path):
    from app.models import NhlResearchForecast

    day1 = _build(tmp_path)
    assert consume_forward_delivery(session, day1.interface, now=NOW)["status"] == "IMPORTED"
    first_clocks = {f.forecast_id: _stored_utc(f.published_utc) for f in session.query(NhlResearchForecast)}
    day2 = _build(tmp_path, new_games=DAY2_GAMES, clock=NEXT_DAY, slate="2026-10-15", chain_from=day1)
    # Day 1's rows are preserved with their own recorded clocks; genesis rows keep the copied fallback.
    assert {r["canonicalGameId"]: r["publishedAtUtc"] for r in day2.prior_manifest["objects"]} == {
        "nhl:game:2026020072": PRIOR_COMMITTED, "nhl:game:2026020073": PRIOR_COMMITTED,
        "nhl:game:2026020106": z(CLOCK["object_done"]), "nhl:game:2026020107": z(CLOCK["object_done"])}
    found = discover(day2.interface, now=NOW + timedelta(days=1))
    assert found["counts"] == {"new": 2, "preserved": 4, "total": 6} and found["originalPointer"]["genesis"] is False
    result = consume_forward_delivery(session, day2.interface, now=NOW + timedelta(days=1))
    assert result["status"] == "IMPORTED"
    assert result["forecasts"]["IMPORTED"] == 2 and result["forecasts"]["DUPLICATE"] == 4
    # Already-held forecasts keep their original publication clocks; nothing is restamped.
    assert {f.forecast_id: _stored_utc(f.published_utc) for f in session.query(NhlResearchForecast)
            if f.forecast_id in first_clocks} == first_clocks


def test_a_missed_day_is_imported_later_but_never_scored_as_pregame(session, tmp_path):
    from app.models import NhlResearchForecast
    from app.nhl.read_service import scored_forecast

    day1 = _build(tmp_path)                       # published, never consumed before day 2 replaced the pointer
    day2 = _build(tmp_path, new_games=DAY2_GAMES, clock=NEXT_DAY, slate="2026-10-15", chain_from=day1)
    result = consume_forward_delivery(session, day2.interface, now=NOW + timedelta(days=1))
    assert result["status"] == "IMPORTED" and result["forecasts"]["IMPORTED"] == 6
    missed = [f for f in session.query(NhlResearchForecast)
              if f.canonical_game_ref in {"nhl:game:2026020106", "nhl:game:2026020107"}]
    assert len(missed) == 2
    for forecast in missed:
        # First seen in the day-2 manifest: stamped with ITS later clock, which is after the game started.
        assert _stored_utc(forecast.published_utc) == NEXT_DAY["object_done"]
        assert _stored_utc(forecast.published_utc) > _stored_utc(forecast.scheduled_start_utc)
        assert scored_forecast([forecast]) is None


def test_a_review_without_its_own_clock_is_dated_by_the_producers_start_record(tmp_path):
    built = _build(tmp_path, hooks={"review": lambda d: d.pop("recordedAtUtc") and None})
    assert discover(built.interface, now=NOW)["counts"]["new"] == 2


def test_no_index_no_completion_or_a_completion_still_being_written_is_not_ready(tmp_path):
    built = _build(tmp_path / "a", hooks={"no_complete": lambda v: None})
    with pytest.raises(DeliveryNotReady, match="PRODUCER_COMPLETION_NOT_YET_RECORDED"):
        discover(built.interface, now=NOW)
    (built.run_root / "WORKFLOW_COMPLETE.json").write_bytes(b"")          # created 'xb', not yet written
    with pytest.raises(DeliveryNotReady, match="PRODUCER_COMPLETION_BEING_WRITTEN"):
        discover(built.interface, now=NOW)
    built = _build(tmp_path / "b")
    built.interface.index_path.unlink()
    with pytest.raises(DeliveryNotReady, match="NO_DELIVERY_INDEX"):
        discover(built.interface, now=NOW)


def test_a_refused_delivery_writes_nothing_and_reports_later_runs(session, tmp_path):
    from app.models import NhlImportReceipt

    built = _build(tmp_path, hooks={"failures": lambda v: ["WORKFLOW_FAILURE.json"]})
    later = built.interface.run_root("2026-10-15")
    later.mkdir(parents=True)
    (later / "WORKFLOW_FAILURE.json").write_text("{}")
    result = consume_forward_delivery(session, built.interface, now=NOW + timedelta(days=1))
    assert result["status"] == "REFUSED" and result["writes"] is False
    assert "PRODUCER_RECORDED_FAILURE:WORKFLOW_FAILURE.json" in result["reason"]
    assert result["laterProducerRuns"] == [{"runRoot": str(later), "failureRecords": ["WORKFLOW_FAILURE.json"],
                                            "completed": False}]
    assert session.query(NhlImportReceipt).count() == 0


def test_a_new_member_the_importer_rejects_is_never_committed_or_latched(session, tmp_path, monkeypatch):
    from app.nhl import identity

    built = _build(tmp_path)
    real = identity.ensure_game

    def refuse_one(session_, game, **kwargs):
        if game["canonicalGameId"] == "nhl:game:2026020107":
            raise ValueError("TEST_GAME_ADMISSION_REFUSED")
        return real(session_, game, **kwargs)

    monkeypatch.setattr(identity, "ensure_game", refuse_one)
    result = consume_forward_delivery(session, built.interface, now=NOW)
    assert result["status"] == "IMPORT_INCOMPLETE" and result["writes"] is False and result["records"] == 0
    assert result["notAdmittedNewMembers"][0].startswith("nhl:game:2026020107:REJECTED")
    session.rollback()                              # what the job handler does when writes is False
    monkeypatch.setattr(identity, "ensure_game", real)
    retry = consume_forward_delivery(session, built.interface, now=NOW)
    assert retry["status"] == "IMPORTED" and retry["records"] == 4


# ------------------------------------------------------------------ refused --
def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def mutate(doc: dict[str, Any]) -> None:
        keys = path.split(".")
        for key in keys[:-1]:
            doc = doc[key]
        doc[keys[-1]] = value
    return mutate


def _first_game(mutate: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
    def apply(handoff: dict[str, Any]) -> None:
        mutate(handoff["games"][0])
    return apply


def _earlier(name: str, than: str) -> dict[str, datetime]:
    return {name: CLOCK[than] - timedelta(seconds=1)}


REFUSALS: list[tuple[str, dict[str, Any], dict[str, Any], str]] = [
    # authority, everywhere and exactly False
    ("handoff authority true", {"handoff": _set("authority.wageringAuthority", True)}, {}, "AUTHORITY_NOT_EXACTLY_ALL_FALSE:HANDOFF"),
    ("index authority 0 is not False", {"index": _set("authority.positionAuthority", 0)}, {}, "AUTHORITY_NOT_EXACTLY_ALL_FALSE:INDEX"),
    ("witness extra authority key", {"witness": _set("authority.paperAuthority", False)}, {}, "AUTHORITY_NOT_EXACTLY_ALL_FALSE:WITNESS"),
    ("receipt decision authority", {"receipt": _set("decisionAuthority", True)}, {}, "AUTHORITY_NOT_EXACTLY_ALL_FALSE:PUBLICATION_RECEIPT"),
    ("forecast object authority", {"new_doc": _set("authority.modelPromotionAuthority", True)}, {}, "AUTHORITY_NOT_EXACTLY_ALL_FALSE:NEW:"),
    ("start record authority", {"workflow_start": _set("authority.decisionAuthority", True)}, {}, "AUTHORITY_NOT_EXACTLY_ALL_FALSE:WORKFLOW_START"),
    # discovery is only discovery
    ("index names a handoff outside the dated workspace",
     {"index": lambda d: d["handoff"].update(path=d["handoff"]["path"].replace("-v1", "-v9"))}, {},
     "INDEX:handoff:NOT_THE_DATED_WORKSPACE"),
    ("witness witnesses another handoff", {"witness": lambda d: d["handoff"].update(sha256="b" * 64)}, {},
     "WITNESS:DOES_NOT_BIND_THE_INDEXED_HANDOFF"),
    ("completion binds another index", {"complete": lambda d: d["deliveryIndex"].update(sha256="c" * 64)}, {},
     "WORKFLOW_COMPLETE:DOES_NOT_BIND_THIS_INDEX_AND_WITNESS"),
    ("recorded publication failure", {"failures": lambda v: ["PUBLICATION_FAILURE.json"]}, {}, "PRODUCER_RECORDED_FAILURE"),
    ("recorded capture failure", {"failures": lambda v: ["CAPTURE_FAILURE.json"]}, {}, "PRODUCER_RECORDED_FAILURE"),
    # the accepted source and the SAME exchange
    ("alternate exchange root", {"handoff": _set("exchangeRoot", r"C:\elsewhere\nhl-gen1-research-v1")}, {},
     "HANDOFF:NOT_THE_SAME_EXCHANGE"),
    ("handoff from another source manifest", {"handoff": lambda d: d["sourceManifestRef"].update(sha256="d" * 64)}, {},
     "HANDOFF:NOT_FROM_THE_ACCEPTED_SOURCE"),
    ("source manifest drops the interface contract", {"source_manifest": _set("sourceFiles", [])}, {},
     "SOURCE_MANIFEST:DOES_NOT_PIN_THE_AGREED_INTERFACE_CONTRACT"),
    ("pointer names another manifest",
     {"pointer": lambda d: d.update(forecastManifest={**d["forecastManifest"], "bytes": d["forecastManifest"]["bytes"] + 1})},
     {}, "POINTER:DOES_NOT_NAME_EXACTLY_THE_HANDOFF_MANIFEST"),
    ("result manifest reference changed", {"pointer": _set("resultManifest", {"path": "x", "sha256": "e" * 64, "bytes": 1})},
     {}, "POINTER:RESULT_MANIFEST_REFERENCE_CHANGED"),
    ("receipt disagrees with handoff on the review", {"receipt": _set("independentReviewRef", None)}, {},
     "PUBLICATION_RECEIPT:independentReviewRef_DIFFERS_FROM_HANDOFF"),
    ("prior objects not byte verified", {"handoff": _set("oldObjectsBytePreserved", False)}, {},
     "HANDOFF:PROSPECTIVE_COST_OR_PRESERVATION_DECLARATION"),
    # the prior manifest is the pointer the chain actually left
    ("copy of the replaced pointer altered", {"original_pointer": _set("resultManifest", {"x": 1})}, {},
     "ORIGINAL_POINTER:SHA256_OR_BYTES_MISMATCH"),
    ("receipt names another replaced pointer", {"receipt": lambda d: d["originalPointerRef"].update(sha256="9" * 64)}, {},
     "ORIGINAL_POINTER:SHA256_OR_BYTES_MISMATCH"),
    ("publication start binds another pointer", {"publication_start": lambda d: d["originalPointerRef"].update(bytes=1)}, {},
     "PUBLICATION_START:DOES_NOT_BIND_THIS_PUBLICATION"),
    # membership
    ("preserved member restamped", {"manifest": lambda d: d["objects"][0].update(publishedAtUtc=z(CLOCK["object_done"]))},
     {}, "PRESERVED_MEMBER_CLOCK_RESTAMPED"),
    ("preserved member dropped", {"manifest": lambda d: d.update(objects=d["objects"][1:])}, {}, "PRESERVED_MEMBER_DROPPED"),
    ("preserved member changed", {"manifest": lambda d: d["objects"][1].update(bytes=d["objects"][1]["bytes"] + 1)}, {},
     "PRESERVED_MEMBER_CHANGED"),
    ("handoff omits a new game", {"handoff": lambda d: d.update(games=d["games"][:1])}, {}, "NEW_MEMBERSHIP_NOT_EXACT"),
    ("declared counts wrong", {"handoff": _set("counts.originalForecastsPreserved", 3)}, {}, "MEMBERSHIP_COUNTS_MISMATCH"),
    ("handoff object inner identity", {"handoff": _first_game(lambda g: g["object"].update(canonicalGameId="nhl:game:2026020199"))},
     {}, "HANDOFF_OBJECT_IS_NOT_THE_ROW"),
    # original starts and the new objects
    ("receipt start differs from the object",
     {"items": lambda items: items[0].update(scheduledStartUtc="2026-10-14T23:00:00Z")}, {}, "ORIGINAL_START_DISAGREES"),
    ("game moved away from the pinned baseline", {"baseline": lambda d: d["dates"][SLATE][0].update(game_time="2026-10-14T23:00:00Z")},
     {}, "NOT_THE_BASELINE_ORIGINAL"),
    ("orientation differs from the baseline", {"baseline": lambda d: d["dates"][SLATE][0].update(home_team_abbr="MTL", away_team_abbr="TOR")},
     {}, "NOT_THE_BASELINE_ORIGINAL"),
    ("slate not in the pinned baseline", {"baseline": _set("dates", {})}, {}, "SLATE_DATE_NOT_IN_THE_PINNED_BASELINE"),
    ("object carries its own publication clock", {"new_doc": _set("publishedAtUtc", "2026-10-14T13:02:00Z")}, {},
     "OBJECT_CARRIES_ITS_OWN_PUBLICATION_CLOCK"),
    ("another model", {"new_doc": _set("model.configurationSha256", "f" * 64)}, {}, "NOT_THE_ACCEPTED_MODEL"),
    ("evidence not the handoff reference", {"handoff": _first_game(lambda g: g.update(producerEvidence={"path": "x", "sha256": "f" * 64, "bytes": 2}))},
     {}, "PRODUCER_EVIDENCE_NOT_THE_HANDOFF_REFERENCE"),
    ("evidence outside its content address",
     {"new_doc": lambda d: d["provenance"]["producerEvidenceRef"].update(path=r"C:\private\producer-receipt.json")},
     {}, "EVIDENCE:NOT_CONTENT_ADDRESSED"),
    ("manifest committed clock moved", {"manifest": _set("publicationReceipt.committedAtUtc", z(CLOCK["object_done"] + timedelta(seconds=1)))},
     {}, "MANIFEST:COMMITTED_CLOCK_IS_NOT_THE_OBJECT_EXPOSURE"),
    ("manifest committed clock the importer cannot parse",
     {"manifest": _set("publicationReceipt.committedAtUtc", CLOCK["object_done"].isoformat())}, {},
     "MANIFEST:COMMITTED_CLOCK_IS_NOT_THE_OBJECT_EXPOSURE"),
    ("new row clock moved", {"manifest": lambda d: d["objects"][-1].update(publishedAtUtc=z(CLOCK["object_done"] + timedelta(seconds=1)))},
     {}, "ROW_CLOCK_IS_NOT_THE_OBJECT_EXPOSURE"),
    # source, preparation and review
    ("six source requests", {"capture": _set("requestCount", 6)}, {}, "CAPTURE_RECEIPT:SOURCE_BOUNDS"),
    ("a source retry", {"capture": _set("sourceRetry", 1)}, {}, "CAPTURE_RECEIPT:SOURCE_BOUNDS"),
    ("asset updated after capture", {"capture": _set("assetUpdatedAtUtc", z(CLOCK["observed"] + timedelta(seconds=1)))},
     {}, "CLOCK_ORDER:SOURCE_ASSET_UPDATED_AFTER_CAPTURE"),
    ("input not the captured source", {"inputs": _set("sourceRetrievedAtUtc", z(CLOCK["observed"] + timedelta(seconds=1)))},
     {}, "CURRENT_INPUT:CAPTURE_CLOCKS_DIFFER"),
    ("qualification prepared at another time", {"qualification": _set("createdAtUtc", z(CLOCK["prepared"] + timedelta(seconds=1)))},
     {}, "SOURCE_QUALIFICATION:CLOCKS_DIFFER"),
    ("object created at another time", {"new_doc": _set("createdAtUtc", z(CLOCK["prepared"] + timedelta(seconds=1)))}, {},
     "SOURCE_OR_CREATION_CLOCK_DIFFERS"),
    ("review not a pass", {"review": _set("status", "CHANGES_REQUIRED")}, {}, "MAIN_SOURCE_REVIEW:NOT_AN_EXACT_PASS"),
    ("review of another source", {"review": _set("sourceManifestSha256", "0" * 64)}, {}, "MAIN_SOURCE_REVIEW:NOT_AN_EXACT_PASS"),
    ("review of another publisher", {"review": lambda d: d["publisherRef"].update(sha256="2" * 64)}, {},
     "MAIN_SOURCE_REVIEW:NOT_AN_EXACT_PASS"),
    ("review recorded after the run started", {}, {"review": CLOCK["workflow_start"] + timedelta(seconds=1)},
     "CLOCK_ORDER:MAIN_SOURCE_REVIEW_RECORDED_AFTER_THE_RUN_STARTED"),
    ("start record pins another review", {"workflow_start": lambda d: d["sourceReviewRef"].update(sha256="3" * 64)}, {},
     "WORKFLOW_START:DOES_NOT_BIND_THIS_REVIEW_SOURCE_AND_DATE"),
    ("start record for another date", {"workflow_start": _set("slateDate", "2026-10-15")}, {},
     "WORKFLOW_START:DOES_NOT_BIND_THIS_REVIEW_SOURCE_AND_DATE"),
    # every link of the clock chain
    ("capture requested before the run started", {}, _earlier("requested", "workflow_start"),
     "CLOCK_ORDER:workflowStart.startedAtUtc>capture.requestStartedAtUtc"),
    ("response before request", {}, _earlier("observed", "requested"),
     "CLOCK_ORDER:capture.requestStartedAtUtc>capture.responseCompletedAtUtc"),
    ("prepared before the capture", {}, _earlier("prepared", "observed"),
     "CLOCK_ORDER:capture.responseCompletedAtUtc>preparation.createdAtUtc"),
    ("publication started before preparation", {}, _earlier("publication_start", "prepared"),
     "CLOCK_ORDER:preparation.createdAtUtc>publicationStart.startedAtUtc"),
    ("objects exposed before publication started", {}, _earlier("object_started", "publication_start"),
     "CLOCK_ORDER:publicationStart.startedAtUtc>objectExposureStartedAtUtc"),
    ("object exposure ends before it starts", {}, _earlier("object_done", "object_started"),
     "CLOCK_ORDER:objectExposureStartedAtUtc>objectExposureCompletedAtUtc"),
    ("manifest before the objects", {}, _earlier("manifest_done", "object_done"),
     "CLOCK_ORDER:objectExposureCompletedAtUtc>manifestExposureCompletedAtUtc"),
    ("pointer before the manifest", {}, _earlier("pointer_done", "manifest_done"),
     "CLOCK_ORDER:manifestExposureCompletedAtUtc>currentPointerExposureCompletedAtUtc"),
    ("handoff before the pointer exposure", {}, _earlier("handoff", "pointer_done"),
     "CLOCK_ORDER:currentPointerExposureCompletedAtUtc>handoff.createdAtUtc"),
    ("witness before the handoff", {}, _earlier("witness", "handoff"),
     "CLOCK_ORDER:handoff.createdAtUtc>witness.exposureVerifiedAtUtc"),
    ("index before the witness", {}, _earlier("index", "witness"),
     "CLOCK_ORDER:witness.exposureVerifiedAtUtc>index.createdAtUtc"),
    ("completion before the index", {}, _earlier("complete", "index"),
     "CLOCK_ORDER:index.createdAtUtc>workflowComplete.completedAtUtc"),
    ("naive clock", {"witness": _set("exposureVerifiedAtUtc", "2026-10-14T13:02:05")}, {}, "CLOCK_NAIVE"),
]


@pytest.mark.parametrize("hooks,clock,reason", [r[1:] for r in REFUSALS], ids=[r[0] for r in REFUSALS])
def test_every_broken_binding_is_refused_before_the_importer(tmp_path, hooks, clock, reason):
    built = _build(tmp_path, hooks=hooks, clock=clock)
    with pytest.raises(DeliveryRefused, match=reason.replace("(", r"\(").replace(")", r"\)")):
        discover(built.interface, now=NOW)


def test_a_delivery_exposed_at_or_after_an_original_start_is_refused(tmp_path):
    late = (("2026020106", "TOR", "MTL", CLOCK["index"]),) + NEW_GAMES[1:]
    built = _build(tmp_path, new_games=late)
    with pytest.raises(DeliveryRefused, match="DELIVERY_NOT_STRICTLY_BEFORE_EVERY_ORIGINAL_START"):
        discover(built.interface, now=NOW)


def test_a_publication_outside_its_slates_eastern_date_is_refused(tmp_path):
    # 00:30 ET on Oct 15 is a different Eastern date from the Oct 14 slate (UTC is 04:30).
    shifted = {k: v + timedelta(hours=15, minutes=30) for k, v in CLOCK.items()}
    games = (("2026020106", "TOR", "MTL", datetime(2026, 10, 15, 23, 30, tzinfo=UTC)),)
    built = _build(tmp_path, new_games=games, clock=shifted)
    with pytest.raises(DeliveryRefused, match="SLATE_DATE_IS_NOT_THE_PUBLICATION_DATE"):
        discover(built.interface, now=NOW + timedelta(days=1))
    # 23:30 ET on Oct 14 (03:30Z Oct 15) is still the Oct 14 slate.
    evening = {k: v + timedelta(hours=14, minutes=30) for k, v in CLOCK.items()}
    games = (("2026020106", "TOR", "MTL", datetime(2026, 10, 15, 23, 30, tzinfo=UTC)),)
    built = _build(tmp_path / "evening", new_games=games, clock=evening)
    assert discover(built.interface, now=NOW + timedelta(days=1))["slateDate"] == SLATE


def test_the_prior_must_be_the_genesis_or_what_an_earlier_forward_run_published(tmp_path):
    day1 = _build(tmp_path)
    with pytest.raises(DeliveryRefused, match="PRIOR_MANIFEST:NOT_THE_POINTER_AN_EARLIER_FORWARD_PUBLICATION_LEFT"):
        discover(dataclasses.replace(day1.interface, genesis_pointer_sha256="0" * 64), now=NOW)
    day2 = _build(tmp_path, new_games=DAY2_GAMES, clock=NEXT_DAY, slate="2026-10-15", chain_from=day1)
    later = NOW + timedelta(days=1)

    # The producer chains from the index it replaced even when its completion record then failed,
    # so the consumer does too: day 2 is admitted although day 1 never completed.
    (day1.run_root / "WORKFLOW_COMPLETE.json").unlink()
    (day1.run_root / "WORKFLOW_FAILURE.json").write_text("{}")
    assert discover(day2.interface, now=later)["originalPointer"]["genesis"] is False

    # ...but not from a run that never reached its own exposure witness...
    witness = day1.run_root / "HANDOFF_EXPOSURE_RECEIPT.json"
    kept = witness.read_bytes()
    witness.unlink()
    with pytest.raises(DeliveryRefused, match="PRIOR_MANIFEST:NOT_THE_POINTER_AN_EARLIER_FORWARD_PUBLICATION_LEFT"):
        discover(day2.interface, now=later)
    witness.write_bytes(kept)
    # ...nor from one that recorded a publication failure.
    (day1.run_root / "PUBLICATION_FAILURE.json").write_text("{}")
    with pytest.raises(DeliveryRefused, match="PRIOR_MANIFEST:NOT_THE_POINTER_AN_EARLIER_FORWARD_PUBLICATION_LEFT"):
        discover(day2.interface, now=later)


def _older_prior(receipt: dict[str, Any]) -> None:
    """Day one claims an older retained manifest (one member) as its prior, not the one the pointer named."""

    exchange = pathlib.Path(receipt["publishedPackageRoot"])
    older = json.loads((exchange / receipt["priorManifestRef"]["path"]).read_bytes())
    rows = older["objects"][:1]
    ref = _put(exchange, "manifests", "manifest.json", {**older, "objects": rows, "counts": {"forecasts": 1}})
    receipt.update(priorManifestRef=ref, priorManifestMembers=rows, preservedOriginalForecastCount=1)


def _drop_second_preserved(manifest: dict[str, Any]) -> None:
    manifest["objects"] = manifest["objects"][:1] + manifest["objects"][2:]
    manifest["counts"] = {"forecasts": len(manifest["objects"])}


def test_a_prior_other_than_the_one_the_replaced_pointer_named_is_refused(tmp_path):
    built = _build(tmp_path, hooks={"receipt": _older_prior, "manifest": _drop_second_preserved})
    with pytest.raises(DeliveryRefused, match="ORIGINAL_POINTER:DOES_NOT_NAME_THE_PRIOR_MANIFEST"):
        discover(built.interface, now=NOW)


def test_an_already_consumed_delivery_reads_superseded_once_a_later_run_moves_the_pointer(session, tmp_path):
    built = _build(tmp_path)
    assert consume_forward_delivery(session, built.interface, now=NOW)["status"] == "IMPORTED"
    pointer = built.interface.exchange_root / "current.json"
    pointer.write_bytes(pointer.read_bytes() + b" ")             # a later run replaced it, then stopped
    result = consume_forward_delivery(session, built.interface, now=NOW)
    assert result["status"] == "SUPERSEDED_AFTER_CONSUMPTION" and result["writes"] is False


def test_a_record_from_the_future_is_refused(tmp_path):
    built = _build(tmp_path)
    with pytest.raises(DeliveryRefused, match="CLOCK_ORDER:workflowComplete.completedAtUtc>now"):
        discover(built.interface, now=CLOCK["complete"] - timedelta(seconds=1))


def test_the_agreed_interface_bytes_are_pinned(tmp_path):
    built = _build(tmp_path)
    path = built.interface.interface_contract.path
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(DeliveryRefused, match="INTERFACE_CONTRACT:SHA256_OR_BYTES_MISMATCH"):
        discover(built.interface, now=NOW)


def test_a_pointer_advanced_after_publication_cannot_be_consumed(session, tmp_path):
    built = _build(tmp_path)
    pointer = built.interface.exchange_root / "current.json"
    pointer.write_bytes(pointer.read_bytes().replace(b'"resultManifest":null', b'"resultManifest":null '))
    result = consume_forward_delivery(session, built.interface, now=NOW)
    assert result["status"] == "REFUSED" and "POINTER:SHA256_OR_BYTES_MISMATCH" in result["reason"]


# ------------------------------------------------------------- the job --
def test_the_forward_import_job_is_off_by_default_and_on_the_import_lane(settings):
    from app.core.config import Settings
    from app.jobs.lanes import IMPORT_LANE, lane_for
    from app.jobs.orchestrator import AVAILABLE_JOBS
    from app.jobs.scheduler import build_scheduler

    assert Settings.model_fields["nhl_forward_import_enabled"].default is False
    assert "import-nhl-forward-delivery" in AVAILABLE_JOBS
    off = build_scheduler(settings.model_copy(update={"nhl_forward_import_enabled": False}))
    assert off.get_job("asp-im-nhl-forward-delivery") is None
    on = build_scheduler(settings.model_copy(update={"nhl_forward_import_enabled": True,
                                                     "nhl_forward_import_interval_minutes": 10}))
    job = on.get_job("asp-im-nhl-forward-delivery")
    assert job is not None and job.kwargs["job_name"] == "import-nhl-forward-delivery"
    assert lane_for(job.id) == IMPORT_LANE and job.trigger.interval == timedelta(minutes=10)
    assert job.max_instances == 1 and job.coalesce is True


@pytest.mark.asyncio
async def test_a_manual_run_with_the_switch_off_reads_and_writes_nothing(session, settings, tmp_path, monkeypatch):
    from app.jobs.orchestrator import JobOrchestrator
    from app.models import NhlImportReceipt

    built = _build(tmp_path)
    monkeypatch.setattr(fd, "PRODUCTION_INTERFACE", built.interface)
    run = await JobOrchestrator(session, settings.model_copy(update={"nhl_forward_import_enabled": False})).run(
        "import-nhl-forward-delivery")
    assert run.result["status"] == "DISABLED" and run.result["records"] == 0
    assert session.query(NhlImportReceipt).count() == 0


@pytest.mark.asyncio
async def test_the_job_commits_an_admitted_delivery_and_records_a_serialisable_result(session, settings, tmp_path,
                                                                                    monkeypatch):
    from app.jobs.orchestrator import JobOrchestrator
    from app.models import JobRun, NhlResearchForecast

    built = _build(tmp_path)
    monkeypatch.setattr(fd, "PRODUCTION_INTERFACE", built.interface)
    monkeypatch.setattr(fd, "datetime", type("FrozenDatetime", (datetime,), {"now": staticmethod(lambda tz=None: NOW)}))
    on = settings.model_copy(update={"nhl_forward_import_enabled": True})
    run = await JobOrchestrator(session, on).run("import-nhl-forward-delivery")
    assert run.result["status"] == "IMPORTED" and run.result["records"] == 4
    session.expire_all()
    assert session.query(NhlResearchForecast).count() == 4
    stored = session.get(JobRun, run.id)
    assert json.loads(json.dumps(stored.result))["delivery"]["slateDate"] == SLATE
    again = await JobOrchestrator(session, on).run("import-nhl-forward-delivery")
    assert again.result["status"] == "ALREADY_CONSUMED"


@pytest.mark.asyncio
async def test_the_job_rolls_back_an_incomplete_import_and_the_next_poll_imports_it(session, settings, tmp_path,
                                                                                   monkeypatch):
    """Regression guard for the latch: a partial import must not be committed, nor block the retry."""

    from app.jobs.orchestrator import JobOrchestrator
    from app.models import Game, NhlImportReceipt, NhlResearchForecast
    from app.nhl import identity

    built = _build(tmp_path)
    monkeypatch.setattr(fd, "PRODUCTION_INTERFACE", built.interface)
    monkeypatch.setattr(fd, "datetime", type("FrozenDatetime", (datetime,), {"now": staticmethod(lambda tz=None: NOW)}))
    real = identity.ensure_game

    def refuse_one(session_, game, **kwargs):
        if game["canonicalGameId"] == "nhl:game:2026020107":
            raise ValueError("TEST_GAME_ADMISSION_REFUSED")
        return real(session_, game, **kwargs)

    monkeypatch.setattr(identity, "ensure_game", refuse_one)
    on = settings.model_copy(update={"nhl_forward_import_enabled": True})
    games_before = session.query(Game).count()
    run = await JobOrchestrator(session, on).run("import-nhl-forward-delivery")
    assert run.result["status"] == "IMPORT_INCOMPLETE" and run.result["records"] == 0
    session.expire_all()
    assert session.query(NhlResearchForecast).count() == 0 and session.query(NhlImportReceipt).count() == 0
    assert session.query(Game).count() == games_before

    monkeypatch.setattr(identity, "ensure_game", real)
    again = await JobOrchestrator(session, on).run("import-nhl-forward-delivery")
    assert again.result["status"] == "IMPORTED" and again.result["records"] == 4


# ------------------------------------------- the real producer package, if present --
_REAL = PRODUCTION_INTERFACE
_real = pytest.mark.skipif(not _REAL.source_manifest.path.is_file(), reason="producer v2 package not on this machine")


@_real
def test_the_production_pins_are_the_reviewed_producer_files():
    for pin in (_REAL.source_manifest, _REAL.interface_contract):
        raw = pin.path.read_bytes()
        assert (_sha(raw), len(raw)) == (pin.sha256, pin.bytes)
    agreed = json.loads(_REAL.interface_contract.path.read_bytes())
    assert agreed["consumerOwner"] == "d7150e32-ed94-41c7-bb5e-c7f85221ed41"
    assert pathlib.PureWindowsPath(agreed["discoveryIndexPath"]) == pathlib.PureWindowsPath(_REAL.index_path)
    source = json.loads(_REAL.source_manifest.path.read_bytes())
    listed = {pathlib.PureWindowsPath(f["path"]).name: f for f in source["sourceFiles"]}
    assert listed["INTERFACE_CONTRACT.json"]["sha256"] == _REAL.interface_contract.sha256
    assert "publish_current.py" in listed
    assert pathlib.PureWindowsPath(source["publicationRootConstant"]) == pathlib.PureWindowsPath(_REAL.exchange_root)
    assert (source["modelId"], source["modelVersion"], source["modelSha256"]) == (
        MODEL["modelId"], MODEL["modelVersion"], MODEL["configurationSha256"])
    baseline = listed["TARGET_SCHEDULE_BASELINE.json"]
    raw = pathlib.Path(baseline["path"]).read_bytes()
    assert (_sha(raw), len(raw)) == (baseline["sha256"], baseline["bytes"])


def _producer_source(name: str) -> bytes:
    source = json.loads(_REAL.source_manifest.path.read_bytes())
    entry = next(f for f in source["sourceFiles"] if pathlib.PureWindowsPath(f["path"]).name == name)
    raw = pathlib.Path(entry["path"]).read_bytes()
    assert (_sha(raw), len(raw)) == (entry["sha256"], entry["bytes"]), f"{name} is not the 0bcf-pinned source"
    return raw


def _dict_keys(tree: ast.AST, target: str) -> set[str]:
    """Constant keys of ``target = {...}`` plus later ``target["k"] = ...`` in the producer source."""

    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == target and isinstance(node.value, ast.Dict):
                    keys |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
                if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == target
                        and isinstance(t.slice, ast.Constant)):
                    keys.add(t.slice.value)
    return keys


def _written_keys(tree: ast.AST, file_name: str) -> set[str]:
    """Constant keys of the dict literal the producer passes to ``exclusive(<root> / file_name, {...})``."""

    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and len(node.args) == 2 and isinstance(node.args[1], ast.Dict)
                and isinstance(node.args[0], ast.BinOp) and isinstance(node.args[0].right, ast.Constant)
                and node.args[0].right.value == file_name):
            return {k.value for k in node.args[1].keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"no exclusive write of {file_name}")


def _keys_of(path: pathlib.Path) -> set[str]:
    return set(json.loads(path.read_bytes()))


@_real
def test_the_builder_writes_the_producers_own_shapes(tmp_path):
    raw = _producer_source("workflow_guards.py")
    spec = importlib.util.spec_from_loader("nhl_forward_producer_guards", loader=None)
    guards = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    exec(compile(raw, "workflow_guards.py", "exec"), guards.__dict__)   # pure: no source, model or exchange I/O

    built = _build(tmp_path)
    for game, item in zip(built.handoff["games"], built.items):
        assert guards.handoff_game(built.docs[item["canonicalGameId"]], item) == game
        assert guards.admit_member(built.docs[item["canonicalGameId"]], item, at=z(CLOCK["object_started"])) is True
        guards.relative_member(item["object"]["path"])
        guards.relative_member(game["producerEvidence"]["path"])
    preserved = guards.stable_prior_members(built.prior_manifest)
    assert guards.merge_members(preserved, built.added) == built.manifest["objects"]
    guards.require_handoff_membership(built.handoff, built.prior_manifest["objects"], built.manifest["objects"])

    run_once = ast.parse(_producer_source("run_once.py"))
    publisher = ast.parse(_producer_source("publish_current.py"))
    assert _dict_keys(run_once, "handoff") == set(built.handoff)
    assert _dict_keys(run_once, "index") == _keys_of(built.interface.index_path)
    for name in ("HANDOFF_EXPOSURE_RECEIPT.json", "WORKFLOW_COMPLETE.json", "WORKFLOW_START.json"):
        assert _written_keys(run_once, name) == _keys_of(built.run_root / name), name
    assert _written_keys(publisher, "PUBLICATION_START.json") == _keys_of(built.run_root / "PUBLICATION_START.json")
    produced = _dict_keys(publisher, "receipt")
    consumed = {"status", "publishedPackageRoot", "independentReviewRef", "originalPointerRef",
                "objectExposureStartedAtUtc", "objectExposureCompletedAtUtc", "manifestExposureCompletedAtUtc",
                "currentPointerExposureCompletedAtUtc", "manifest", "pointer", "newForecastCount",
                "preservedOriginalForecastCount", "currentManifestTotalCount", "newForecasts", "omittedAtPublication",
                "providerRequests", "addedCostUsd", "wageringAuthority", "decisionAuthority", "positionAuthority",
                "modelPromotionAuthority", "priorObjectsByteVerified", "priorManifestMembers", "currentManifestMembers",
                "preservedResultManifest", "priorManifestRef"}
    assert consumed <= produced and consumed <= _keys_of(built.run_root / "PUBLICATION_RECEIPT.json")
