"""Forward NHL deliveries: discover the producer's dated publication, prove it, then import it.

Interface (accepted by the consumer against exactly these bytes): Model Studio
``nhl-forward-publication-workflow-20261014-v2`` -- ``SOURCE_MANIFEST.json``
sha256 0bcf3b16... (8591 B) and ``INTERFACE_CONTRACT.json`` sha256 adf270f1...
(896 B), source-accepted by MAIN review 8786cdce.

After a successful publication into the SAME ``nhl-gen1-research-v1`` exchange
the producer writes, in its dated workspace ``outputs/nhl-current-slate-producer-
YYYYMMDD-v1``, the handoff (``NHL_CURRENT_PRODUCER_HANDOFF_V1``), then a
post-exposure witness, then replaces one local ``DELIVERY_INDEX.json`` naming
both by path + SHA-256 + size, then records its own completion.

The index is DISCOVERY ONLY: nothing it says is believed. Before the accepted
importer is called this module proves from bytes:

* the pinned interface: the producer source manifest (0bcf), the interface
  contract (adf270) it lists, the model it names and the saved schedule
  baseline it pins;
* the dated workspace: index, witness, handoff and publication receipt all in
  it; no recorded capture/publication/workflow failure; a completion record
  binding exactly this index and witness;
* the canonical exchange: the current pointer names exactly the handoff's
  immutable manifest and keeps the result-manifest reference untouched; the
  manifest is the receipt's current membership; the prior manifest is the
  receipt's prior membership AND the one the replaced pointer named (the
  producer's byte copy of it), and that replaced pointer is either the pinned
  genesis (the closed Oct 10 publication) or the one an earlier forward run
  successfully published (its receipt, bound by its own exposure witness) --
  never an older or never-pointed manifest;
* exact membership: current = preserved + new, no overlap, every preserved row
  identical to its prior row (a null clock may only be filled with the prior
  manifest's own recorded fallback, never a newer time);
* every new forecast object: identity, model, authority, producer evidence at
  its content address in the SAME exchange, and one original start agreed by
  the handoff, the receipt, the object, the current input and the pinned
  baseline;
* MAIN's source review: the exact bytes the producer pinned, an exact PASS for
  source 0bcf and its publisher, already pinned by the producer's own
  WORKFLOW_START before any source request (a recordedAtUtc, if present, may
  not be later than that start);
* the clock chain: workflow start <= capture request <= capture response <=
  preparation (input = qualification = object) <= publication start <= object
  exposure <= manifest exposure <= pointer exposure <= handoff <= witness <=
  index <= completion <= now; the index strictly before every new game's
  original start; the publication on its slate's ET date;
* all four authority flags exactly FALSE wherever they appear.

Only then does the handoff go, by its SHA pin, to ``import_from_handoff``
(unchanged), which re-proves the pointer, manifest, membership, producer
evidence and preserved originals itself and imports exactly that manifest.
If any NEW member is not admitted by the importer, nothing is committed (the
caller rolls back), so a partial import can never latch the delivery.

Scheduling consequence: the importer stamps a first-seen forecast with its
manifest's committed clock, and a handoff verifies only while its pointer is
current. A delivery must be consumed before the next publication replaces the
pointer; a forecast first seen in a later manifest carries that later clock --
never an earlier one -- so a missed day can only cost scoring, not overstate it.

Research only: no decision, quote, price, position, account or result. Reads
producer files; writes only to the caller's session, which it does not commit.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.nhl import contract as c
from app.nhl.handoff_import import AUTHORITY_FLAGS, HANDOFF_SCHEMA, HandoffRefused, import_from_handoff

INDEX_SCHEMA = "NHL_FORWARD_DELIVERY_INDEX_V1"
INTERFACE_SCHEMA = "NHL_FORWARD_SAME_CONSUMER_INTERFACE_CONTRACT_V2"
WITNESS_SCHEMA = "NHL_HANDOFF_EXPOSURE_RECEIPT_V1"
WITNESS_STATUS = "PROSPECTIVE_HANDOFF_EXPOSURE_VERIFIED"
HANDOFF_STATUS = "CURRENT_PUBLISHED_PRODUCER_VERIFIED_CONSUMER_IMPORT_PENDING"
PUBLICATION_STATUS = "CURRENT_PRIVATE_RESEARCH_PUBLISHED_IMPORT_PENDING"
COMPLETE_STATUS = "PUBLISHED_HANDOFF_IMPORT_PENDING"
FORECAST_CUTOFF = "STRICTLY_BEFORE_EACH_ORIGINAL_SCHEDULED_START"
REVIEW_STATUS = "PASS_NHL_FORWARD_WORKFLOW_EXACT_SOURCE"
REVIEW_SCOPE = "PRIVATE_RESEARCH_EXCHANGE_ONLY_NO_PAPER_AUTHORITY"
INPUT_SCHEMA = "NHL_CURRENT_MODEL_INPUTS_V1"
QUALIFICATION_SCHEMA = "NHL_CURRENT_SOURCE_QUALIFICATION_V1"
WORKFLOW_START_SCHEMA = "NHL_FORWARD_WORKFLOW_START_V1"
FAILURE_RECORDS = ("CAPTURE_FAILURE.json", "PUBLICATION_FAILURE.json", "WORKFLOW_FAILURE.json")
SOURCE_REQUEST_LIMIT = 5
SOURCE_BYTE_LIMIT = 8 * 1024 * 1024
ET = ZoneInfo("America/New_York")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class DeliveryRefused(ValueError):
    """The delivery is not admissible as it stands."""


class DeliveryNotReady(ValueError):
    """Nothing to consume yet: no index, or the producer has not recorded completion."""


@dataclass(frozen=True)
class Pin:
    path: Path
    sha256: str
    bytes: int


@dataclass(frozen=True)
class ForwardInterface:
    outputs_root: Path          # parent of the producer's dated workspaces
    workflow_root: Path         # the reviewed workflow package: index, MAIN review, pinned source
    exchange_root: Path         # the SAME canonical NHL research exchange
    source_manifest: Pin
    interface_contract: Pin
    #: The exchange pointer the FIRST forward publication must replace (the closed
    #: historical Oct 10 publication). Every later one must replace the pointer an
    #: earlier forward run successfully published -- so the prior manifest is always
    #: the one the exchange actually pointed to, never an older or unpointed one.
    genesis_pointer_sha256: str

    @property
    def index_path(self) -> Path:
        return self.workflow_root / "DELIVERY_INDEX.json"

    @property
    def review_path(self) -> Path:
        return self.workflow_root / "MAIN_SOURCE_ACCEPTANCE.json"

    def run_root(self, slate: str) -> Path:
        return self.outputs_root / f"nhl-current-slate-producer-{slate.replace('-', '')}-v1"


_OUTPUTS = Path.home() / "asp-model-studio" / "outputs"
_WORKFLOW = _OUTPUTS / "nhl-forward-publication-workflow-20261014-v2"
PRODUCTION_INTERFACE = ForwardInterface(
    outputs_root=_OUTPUTS,
    workflow_root=_WORKFLOW,
    exchange_root=Path.home() / "asp-model-exchange" / "nhl-gen1-research-v1",
    source_manifest=Pin(_WORKFLOW / "SOURCE_MANIFEST.json",
                        "0bcf3b16157c6130233ad8c6ed037fb256c1824fefbb44675cf90de33d9ce10f", 8591),
    interface_contract=Pin(_WORKFLOW / "INTERFACE_CONTRACT.json",
                           "adf270f1faf636bc58c128ea88ebb008fa0632a4f9cb195db83f3beb5e253d1c", 896),
    # current.json naming manifest bedd8150 (the Oct 10 publication), 278 B.
    genesis_pointer_sha256="70034950dd9a86b23e43e480742067064174e452fb268ac126cdf3b16a6d9831",
)


# ------------------------------------------------------------------ proving --
def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _same(a: Any, b: Any) -> bool:
    return isinstance(a, (str, Path)) and str(a) != "" and PureWindowsPath(str(a)) == PureWindowsPath(str(b))


def _is_ref(ref: Any) -> bool:
    return (isinstance(ref, dict) and isinstance(ref.get("path"), str) and ref["path"] != ""
            and isinstance(ref.get("sha256"), str) and _HEX64.fullmatch(ref["sha256"]) is not None
            and type(ref.get("bytes")) is int and ref["bytes"] > 0)


def _matches(ref: Any, pin: Pin) -> bool:
    return _is_ref(ref) and _same(ref["path"], pin.path) and ref["sha256"] == pin.sha256 and ref["bytes"] == pin.bytes


def _pinned(ref: Any, expected: Path, label: str) -> bytes:
    """Bytes of ``expected``, which ``ref`` must name exactly and bind by SHA-256 + size."""

    if not _is_ref(ref):
        raise DeliveryRefused(f"{label}:NOT_A_PATH_SHA256_BYTES_BINDING")
    if not _same(ref["path"], expected):
        raise DeliveryRefused(f"{label}:NOT_AT_THE_CONTRACT_LOCATION")
    if not expected.is_file():
        raise DeliveryRefused(f"{label}:MISSING")
    raw = expected.read_bytes()
    if len(raw) != ref["bytes"] or _sha(raw) != ref["sha256"]:
        raise DeliveryRefused(f"{label}:SHA256_OR_BYTES_MISMATCH")
    return raw


def _addressed(ref: Any, root: Path, family: str, name: str, label: str) -> bytes:
    """An immutable exchange member at its own content address, e.g. ``manifests/sha256/<sha>/manifest.json``."""

    if not _is_ref(ref) or ref["path"] != f"{family}/sha256/{ref['sha256']}/{name}":
        raise DeliveryRefused(f"{label}:NOT_CONTENT_ADDRESSED")
    path = root.joinpath(*PurePosixPath(ref["path"]).parts)
    if not path.is_file():
        raise DeliveryRefused(f"{label}:MISSING")
    raw = path.read_bytes()
    if len(raw) != ref["bytes"] or _sha(raw) != ref["sha256"]:
        raise DeliveryRefused(f"{label}:SHA256_OR_BYTES_MISMATCH")
    return raw


def _json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DeliveryRefused(f"{label}:NOT_JSON") from exc
    if not isinstance(value, dict):
        raise DeliveryRefused(f"{label}:NOT_AN_OBJECT")
    return value


def _clock(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise DeliveryRefused(f"CLOCK_MISSING:{label}")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeliveryRefused(f"CLOCK_MALFORMED:{label}") from exc
    if moment.tzinfo is None:
        raise DeliveryRefused(f"CLOCK_NAIVE:{label}")
    return moment.astimezone(UTC)


def _all_false(mapping: Any, label: str) -> None:
    if (not isinstance(mapping, dict) or set(mapping) != set(AUTHORITY_FLAGS)
            or any(mapping[flag] is not False for flag in AUTHORITY_FLAGS)):
        raise DeliveryRefused(f"AUTHORITY_NOT_EXACTLY_ALL_FALSE:{label}")


def _zero(value: Any) -> bool:
    return type(value) in (int, float) and value == 0


def _count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise DeliveryRefused("COUNT_NOT_A_NONNEGATIVE_INTEGER")
    return value


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise DeliveryRefused(reason)


def _same_ref(a: Any, b: Any) -> bool:
    return _is_ref(a) and _is_ref(b) and _same(a["path"], b["path"]) and (a["sha256"], a["bytes"]) == (
        b["sha256"], b["bytes"])


def _read_exact(path: Path, label: str) -> bytes:
    """A producer record at its fixed contract location (not referenced by hash anywhere else)."""

    if not path.is_file():
        raise DeliveryRefused(f"{label}:MISSING")
    return path.read_bytes()


def _workspace_date(root: Path) -> date | None:
    match = re.fullmatch(r"nhl-current-slate-producer-(\d{4})(\d{2})(\d{2})-v1", root.name)
    try:
        return date(int(match[1]), int(match[2]), int(match[3])) if match else None
    except ValueError:
        return None


def _left_by_earlier_delivery(x: ForwardInterface, slate: str, pointer_ref: dict[str, Any],
                              manifest_ref: dict[str, Any]) -> bool:
    """Whether an earlier forward run SUCCESSFULLY PUBLISHED exactly this pointer -- the producer's own chain rule.

    run_once chains from the delivery index it replaced last, and it replaces the index only after the
    post-exposure witness exists. So a link is: a successful publication receipt (written only after the
    publisher re-read the exchange pointer as these bytes) that the run's own witness binds, with no capture
    or publication failure. Completion is NOT required: if WORKFLOW_COMPLETE failed after the index was
    replaced, the producer still chains from that day. That day's own delivery stays refused or not ready;
    its members are first seen in the successor under the successor's later clock, never as pregame.
    """

    for root in x.outputs_root.glob("nhl-current-slate-producer-*-v1"):
        day = _workspace_date(root)
        if day is None or day.isoformat() >= slate or any(
                (root / n).exists() for n in ("CAPTURE_FAILURE.json", "PUBLICATION_FAILURE.json")):
            continue
        try:
            receipt_raw = (root / "PUBLICATION_RECEIPT.json").read_bytes()
            receipt = json.loads(receipt_raw)
            witness = json.loads((root / "HANDOFF_EXPOSURE_RECEIPT.json").read_bytes())
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        bound = witness.get("publicationReceipt") if isinstance(witness, dict) else None
        if (isinstance(receipt, dict) and receipt.get("status") == PUBLICATION_STATUS
                and _same(receipt.get("publishedPackageRoot"), x.exchange_root)
                and receipt.get("pointer") == pointer_ref and receipt.get("manifest") == manifest_ref
                and witness.get("status") == WITNESS_STATUS and _is_ref(bound)
                and _same(bound["path"], root / "PUBLICATION_RECEIPT.json")
                and (bound["sha256"], bound["bytes"]) == (_sha(receipt_raw), len(receipt_raw))):
            return True
    return False


def _key(row: Any) -> tuple[Any, Any, Any]:
    row = row if isinstance(row, dict) else {}
    return row.get("canonicalGameId"), row.get("forecastId"), row.get("sha256")


def _rows(manifest: dict[str, Any], label: str) -> list[dict[str, Any]]:
    rows = manifest.get("objects")
    _require(isinstance(rows, list) and all(isinstance(r, dict) for r in rows), f"{label}:OBJECTS_NOT_A_LIST")
    games = [r.get("canonicalGameId") for r in rows]
    keys = [_key(r) for r in rows]
    _require(len(set(games)) == len(games) and len(set(keys)) == len(keys), f"{label}:DUPLICATE_MEMBER")
    return rows


# ---------------------------------------------------------------- discovery --
def discover(interface: ForwardInterface | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    """Prove the currently indexed forward delivery. Raises DeliveryNotReady / DeliveryRefused."""

    x = interface or PRODUCTION_INTERFACE
    now = (now or datetime.now(UTC)).astimezone(UTC)

    # 1. The pinned interface itself: producer source 0bcf and the contract adf270 it lists.
    source = _json(_pinned({"path": str(x.source_manifest.path), "sha256": x.source_manifest.sha256,
                            "bytes": x.source_manifest.bytes}, x.source_manifest.path, "SOURCE_MANIFEST"),
                   "SOURCE_MANIFEST")
    agreed = _json(_pinned({"path": str(x.interface_contract.path), "sha256": x.interface_contract.sha256,
                            "bytes": x.interface_contract.bytes}, x.interface_contract.path, "INTERFACE_CONTRACT"),
                   "INTERFACE_CONTRACT")
    _require(agreed.get("schema") == INTERFACE_SCHEMA and agreed.get("handoffSchema") == HANDOFF_SCHEMA
             and _same(agreed.get("discoveryIndexPath"), x.index_path), "INTERFACE_CONTRACT:NOT_THE_AGREED_CONTRACT")
    listed = {PureWindowsPath(f["path"]).name: f for f in source.get("sourceFiles") or []
              if isinstance(f, dict) and isinstance(f.get("path"), str)}
    _require(_matches(listed.get("INTERFACE_CONTRACT.json"), x.interface_contract),
             "SOURCE_MANIFEST:DOES_NOT_PIN_THE_AGREED_INTERFACE_CONTRACT")
    _all_false(source.get("authority"), "SOURCE_MANIFEST")
    _require(_same(source.get("publicationRootConstant"), x.exchange_root), "SOURCE_MANIFEST:NOT_THE_SAME_EXCHANGE")
    model = {"modelId": source.get("modelId"), "modelVersion": source.get("modelVersion"),
             "configurationSha256": source.get("modelSha256")}
    _require(all(isinstance(v, str) and v for v in model.values()), "SOURCE_MANIFEST:MODEL_NOT_NAMED")
    baseline = _json(_pinned(listed.get("TARGET_SCHEDULE_BASELINE.json"),
                             x.workflow_root / "TARGET_SCHEDULE_BASELINE.json", "SCHEDULE_BASELINE"),
                     "SCHEDULE_BASELINE")

    # 2. The discovery index: read once, believed in nothing.
    if not x.index_path.is_file():
        raise DeliveryNotReady("NO_DELIVERY_INDEX")
    index_raw = x.index_path.read_bytes()
    index = _json(index_raw, "INDEX")
    _require(index.get("schema") == INDEX_SCHEMA, "INDEX:SCHEMA")
    _all_false(index.get("authority"), "INDEX")
    slate = index.get("slateDate")
    try:
        _require(isinstance(slate, str) and date.fromisoformat(slate).isoformat() == slate, "INDEX:SLATE_DATE")
    except ValueError as exc:
        raise DeliveryRefused("INDEX:SLATE_DATE") from exc
    run_root = x.run_root(slate)
    for key, name in (("handoff", "HANDOFF.json"), ("exposureReceipt", "HANDOFF_EXPOSURE_RECEIPT.json"),
                      ("publicationReceipt", "PUBLICATION_RECEIPT.json")):
        _require(_is_ref(index.get(key)) and _same(index[key]["path"], run_root / name),
                 f"INDEX:{key}:NOT_THE_DATED_WORKSPACE_{name}")
    index_created = _clock(index.get("createdAtUtc"), "index.createdAtUtc")

    # 3. The producer's own outcome for that workspace: any failure refuses; completion must bind this index.
    for name in FAILURE_RECORDS:
        if (run_root / name).exists():
            raise DeliveryRefused(f"PRODUCER_RECORDED_FAILURE:{name}")
    complete_path = run_root / "WORKFLOW_COMPLETE.json"
    if not complete_path.is_file():
        raise DeliveryNotReady("PRODUCER_COMPLETION_NOT_YET_RECORDED")
    try:
        complete = json.loads(complete_path.read_bytes())
    except (ValueError, UnicodeDecodeError) as exc:
        # The producer creates WORKFLOW_COMPLETE with open('xb') and fills it afterwards,
        # so for a moment it can exist empty or half-written: not yet, rather than wrong.
        raise DeliveryNotReady("PRODUCER_COMPLETION_BEING_WRITTEN") from exc
    _require(isinstance(complete, dict), "WORKFLOW_COMPLETE:NOT_AN_OBJECT")
    bound_index = complete.get("deliveryIndex")
    _require(complete.get("status") == COMPLETE_STATUS and _is_ref(bound_index)
             and _same(bound_index["path"], x.index_path) and bound_index["sha256"] == _sha(index_raw)
             and bound_index["bytes"] == len(index_raw)
             and complete.get("handoffExposureReceipt") == index["exposureReceipt"]
             and complete.get("consumerImportProof") is False,
             "WORKFLOW_COMPLETE:DOES_NOT_BIND_THIS_INDEX_AND_WITNESS")
    _all_false(complete.get("authority"), "WORKFLOW_COMPLETE")
    completed = _clock(complete.get("completedAtUtc"), "workflowComplete.completedAtUtc")

    # 4. The post-exposure witness and the handoff it witnesses.
    witness = _json(_pinned(index["exposureReceipt"], run_root / "HANDOFF_EXPOSURE_RECEIPT.json", "WITNESS"),
                    "WITNESS")
    _require(witness.get("schema") == WITNESS_SCHEMA and witness.get("status") == WITNESS_STATUS
             and witness.get("handoff") == index["handoff"]
             and witness.get("publicationReceipt") == index["publicationReceipt"],
             "WITNESS:DOES_NOT_BIND_THE_INDEXED_HANDOFF")
    _all_false(witness.get("authority"), "WITNESS")
    exposed = _clock(witness.get("exposureVerifiedAtUtc"), "witness.exposureVerifiedAtUtc")

    handoff_raw = _pinned(index["handoff"], run_root / "HANDOFF.json", "HANDOFF")
    h = _json(handoff_raw, "HANDOFF")
    _require(h.get("schema") == HANDOFF_SCHEMA and h.get("status") == HANDOFF_STATUS, "HANDOFF:SCHEMA_OR_STATUS")
    _all_false(h.get("authority"), "HANDOFF")
    _require(_same(h.get("exchangeRoot"), x.exchange_root), "HANDOFF:NOT_THE_SAME_EXCHANGE")
    _require(h.get("publicationReceipt") == index["publicationReceipt"], "HANDOFF:PUBLICATION_RECEIPT_NOT_INDEXED")
    _require(_matches(h.get("sourceManifestRef"), x.source_manifest), "HANDOFF:NOT_FROM_THE_ACCEPTED_SOURCE")
    _require(h.get("allPublicationClocksGenuinelyProspective") is True and h.get("forecastCutoff") == FORECAST_CUTOFF
             and _zero(h.get("addedCostUsd")) and h.get("oldObjectsBytePreserved") is True,
             "HANDOFF:PROSPECTIVE_COST_OR_PRESERVATION_DECLARATION")
    handoff_created = _clock(h.get("createdAtUtc"), "handoff.createdAtUtc")
    games = h.get("games")
    _require(isinstance(games, list) and games and all(isinstance(g, dict) for g in games), "HANDOFF:NO_GAMES")
    counts = h.get("counts")
    _require(isinstance(counts, dict), "HANDOFF:COUNTS")

    # 5. The publication receipt, which must agree with the handoff on every shared reference.
    receipt = _json(_pinned(h["publicationReceipt"], run_root / "PUBLICATION_RECEIPT.json", "PUBLICATION_RECEIPT"),
                    "PUBLICATION_RECEIPT")
    _require(receipt.get("status") == PUBLICATION_STATUS and _same(receipt.get("publishedPackageRoot"), x.exchange_root),
             "PUBLICATION_RECEIPT:STATUS_OR_EXCHANGE")
    for key, theirs in (("manifest", "manifest"), ("pointer", "pointer"), ("independentReviewRef", "independentReviewRef"),
                        ("priorManifestRef", "priorManifestRef"), ("preservedResultManifest", "resultManifest"),
                        ("omittedAtPublication", "omittedAtPublication")):
        _require(theirs in h and receipt.get(key) == h[theirs], f"PUBLICATION_RECEIPT:{key}_DIFFERS_FROM_HANDOFF")
    _all_false({flag: receipt.get(flag) for flag in AUTHORITY_FLAGS}, "PUBLICATION_RECEIPT")
    _require(receipt.get("priorObjectsByteVerified") is True and _zero(receipt.get("addedCostUsd"))
             and _zero(receipt.get("providerRequests")), "PUBLICATION_RECEIPT:PRESERVATION_COST_OR_PROVIDER")
    object_started = _clock(receipt.get("objectExposureStartedAtUtc"), "objectExposureStartedAtUtc")
    object_done = _clock(receipt.get("objectExposureCompletedAtUtc"), "objectExposureCompletedAtUtc")
    manifest_done = _clock(receipt.get("manifestExposureCompletedAtUtc"), "manifestExposureCompletedAtUtc")
    pointer_done = _clock(receipt.get("currentPointerExposureCompletedAtUtc"), "currentPointerExposureCompletedAtUtc")

    # 6. The SAME canonical exchange: pointer -> exactly this immutable manifest; prior manifest retained.
    pointer = _json(_pinned(h["pointer"], x.exchange_root / "current.json", "POINTER"), "POINTER")
    _require(pointer.get("schema") == c.POINTER_SCHEMA, "POINTER:SCHEMA")
    _require(pointer.get("forecastManifest") == h["manifest"], "POINTER:DOES_NOT_NAME_EXACTLY_THE_HANDOFF_MANIFEST")
    _require(pointer.get("resultManifest") == h["resultManifest"], "POINTER:RESULT_MANIFEST_REFERENCE_CHANGED")
    manifest = _json(_addressed(h["manifest"], x.exchange_root, "manifests", "manifest.json", "MANIFEST"), "MANIFEST")
    prior = _json(_addressed(h["priorManifestRef"], x.exchange_root, "manifests", "manifest.json", "PRIOR_MANIFEST"),
                  "PRIOR_MANIFEST")
    _require(h["priorManifestRef"]["sha256"] != h["manifest"]["sha256"], "PRIOR_MANIFEST:IS_THE_CURRENT_MANIFEST")
    for doc, label in ((manifest, "MANIFEST"), (prior, "PRIOR_MANIFEST")):
        _require(doc.get("schema") == c.FORECAST_MANIFEST_SCHEMA and doc.get("researchStatus") == "RESEARCH_ONLY",
                 f"{label}:SCHEMA_OR_RESEARCH_STATUS")
    _require(manifest.get("modelId") == model["modelId"] and manifest.get("modelVersion") == model["modelVersion"],
             "MANIFEST:NOT_THE_ACCEPTED_MODEL")
    committed_raw = (manifest.get("publicationReceipt") or {}).get("committedAtUtc")
    committed = _clock(committed_raw, "manifest.committedAtUtc")
    # The importer stamps first-seen forecasts with this clock through its own stricter parser.
    _require(committed == object_done and c.parse_utc(committed_raw) == committed,
             "MANIFEST:COMMITTED_CLOCK_IS_NOT_THE_OBJECT_EXPOSURE")

    # The prior manifest is the one the exchange ACTUALLY pointed to before this publication:
    # the producer's byte copy of the replaced pointer names it, and that pointer is either the
    # pinned genesis or the pointer a completed earlier forward delivery left.
    original_ref = receipt.get("originalPointerRef")
    _require(_is_ref(original_ref) and _same(original_ref["path"], x.exchange_root / "current.json"),
             "PUBLICATION_RECEIPT:ORIGINAL_POINTER_REF")
    original = _json(_pinned({**original_ref, "path": str(run_root / "ORIGINAL_CURRENT_POINTER.json")},
                             run_root / "ORIGINAL_CURRENT_POINTER.json", "ORIGINAL_POINTER"), "ORIGINAL_POINTER")
    _require(original.get("forecastManifest") == h["priorManifestRef"]
             and original.get("resultManifest") == h["resultManifest"],
             "ORIGINAL_POINTER:DOES_NOT_NAME_THE_PRIOR_MANIFEST")
    _require(original_ref["sha256"] == x.genesis_pointer_sha256
             or _left_by_earlier_delivery(x, slate, original_ref, h["priorManifestRef"]),
             "PRIOR_MANIFEST:NOT_THE_POINTER_AN_EARLIER_FORWARD_PUBLICATION_LEFT")
    publication_start = _json(_read_exact(run_root / "PUBLICATION_START.json", "PUBLICATION_START"),
                              "PUBLICATION_START")
    _require(publication_start.get("independentReviewRef") == h.get("independentReviewRef")
             and publication_start.get("originalPointerRef") == original_ref
             and _zero(publication_start.get("providerRequests")), "PUBLICATION_START:DOES_NOT_BIND_THIS_PUBLICATION")
    publication_started = _clock(publication_start.get("startedAtUtc"), "publicationStart.startedAtUtc")
    current_rows, prior_rows = _rows(manifest, "MANIFEST"), _rows(prior, "PRIOR_MANIFEST")
    _require(current_rows == receipt.get("currentManifestMembers"), "MANIFEST:NOT_THE_RECEIPT_CURRENT_MEMBERSHIP")
    _require(prior_rows == receipt.get("priorManifestMembers"), "PRIOR_MANIFEST:NOT_THE_RECEIPT_PRIOR_MEMBERSHIP")

    # 7. Exact membership. Preserved rows are the prior rows unchanged; only a null clock may be filled,
    #    and only with the prior manifest's own recorded fallback (never restamped with a newer time).
    by_game = {r["canonicalGameId"]: r for r in current_rows}
    fallback = (prior.get("publicationReceipt") or {}).get("committedAtUtc")
    for old in prior_rows:
        now_row = by_game.get(old.get("canonicalGameId"))
        _require(now_row is not None, "PRESERVED_MEMBER_DROPPED")
        _require({k: v for k, v in now_row.items() if k != "publishedAtUtc"}
                 == {k: v for k, v in old.items() if k != "publishedAtUtc"}, "PRESERVED_MEMBER_CHANGED")
        kept = old.get("publishedAtUtc") if old.get("publishedAtUtc") is not None else fallback
        _require(kept is not None and now_row.get("publishedAtUtc") == kept, "PRESERVED_MEMBER_CLOCK_RESTAMPED")
    prior_games = {r["canonicalGameId"] for r in prior_rows}
    new_rows = [r for r in current_rows if r["canonicalGameId"] not in prior_games]
    new_keys = {_key(r) for r in new_rows}
    game_keys = [_key({**(g.get("object") or {}), "canonicalGameId": g.get("canonicalGameId"),
                       "forecastId": g.get("forecastId")}) for g in games]
    receipt_new = receipt.get("newForecasts")
    _require(isinstance(receipt_new, list) and all(isinstance(i, dict) for i in receipt_new),
             "PUBLICATION_RECEIPT:NEW_FORECASTS")
    receipt_keys = [(i.get("canonicalGameId"), i.get("forecastId"), (i.get("object") or {}).get("sha256"))
                    for i in receipt_new]
    _require(bool(new_rows) and len(game_keys) == len(new_rows) == len(receipt_keys)
             and set(game_keys) == new_keys == set(receipt_keys)
             and not new_keys & {_key(r) for r in prior_rows}, "NEW_MEMBERSHIP_NOT_EXACT")
    declared = {k: _count(counts.get(k)) for k in ("newCurrentPublished", "originalForecastsPreserved",
                                                    "currentManifestTotal")}
    _require(declared == {"newCurrentPublished": len(new_rows), "originalForecastsPreserved": len(prior_rows),
                          "currentManifestTotal": len(current_rows)}
             and receipt.get("newForecastCount") == len(new_rows)
             and receipt.get("preservedOriginalForecastCount") == len(prior_rows)
             and receipt.get("currentManifestTotalCount") == len(current_rows), "MEMBERSHIP_COUNTS_MISMATCH")

    # 8. Source capture, current input, qualification and MAIN's source review, with their clocks.
    capture_ref = h.get("sourceCaptureReceipt")
    _require(_is_ref(capture_ref), "CAPTURE_RECEIPT:BINDING")
    capture = _json(_pinned(capture_ref, run_root / "receipts" / "sha256" / capture_ref["sha256"] / "receipt.json",
                            "CAPTURE_RECEIPT"), "CAPTURE_RECEIPT")
    _require(capture.get("status") == 200 and capture.get("publisherDigestVerified") is True
             and capture.get("authentication") == "NONE" and _zero(capture.get("sourceRetry"))
             and _zero(capture.get("addedCostUsd")) and type(capture.get("requestCount")) is int
             and 1 <= capture["requestCount"] <= SOURCE_REQUEST_LIMIT
             and type(capture.get("totalResponseBytes")) is int
             and 0 <= capture["totalResponseBytes"] <= SOURCE_BYTE_LIMIT, "CAPTURE_RECEIPT:SOURCE_BOUNDS")
    requested = _clock(capture.get("requestStartedAtUtc"), "capture.requestStartedAtUtc")
    observed = _clock(capture.get("responseCompletedAtUtc"), "capture.responseCompletedAtUtc")
    asset_updated = _clock(capture.get("assetUpdatedAtUtc"), "capture.assetUpdatedAtUtc")
    _require(asset_updated <= observed, "CLOCK_ORDER:SOURCE_ASSET_UPDATED_AFTER_CAPTURE")

    inputs = _json(_pinned(h.get("currentInputRef"), run_root / "CURRENT_SCHEDULE_AND_MODEL_INPUTS.json",
                           "CURRENT_INPUT"), "CURRENT_INPUT")
    _require(inputs.get("schema") == INPUT_SCHEMA and inputs.get("sourceCaptureReceipt") == capture_ref,
             "CURRENT_INPUT:SCHEMA_OR_CAPTURE")
    _require(_clock(inputs.get("sourceRetrievedAtUtc"), "input.sourceRetrievedAtUtc") == observed
             and _clock(inputs.get("sourceAssetUpdatedAtUtc"), "input.sourceAssetUpdatedAtUtc") == asset_updated,
             "CURRENT_INPUT:CAPTURE_CLOCKS_DIFFER")
    prepared = _clock(inputs.get("createdAtUtc"), "input.createdAtUtc")
    input_games = inputs.get("games")
    _require(isinstance(input_games, list), "CURRENT_INPUT:GAMES")

    qualification = _json(_pinned(h.get("sourceQualificationRef"), run_root / "SOURCE_QUALIFICATION.json",
                                  "SOURCE_QUALIFICATION"), "SOURCE_QUALIFICATION")
    _require(qualification.get("schema") == QUALIFICATION_SCHEMA
             and qualification.get("sourceCaptureReceipt") == capture_ref
             and _zero(qualification.get("addedCostUsd")) and qualification.get("paidEntitlementUsed") is False,
             "SOURCE_QUALIFICATION:SCHEMA_CAPTURE_OR_COST")
    _require(_clock(qualification.get("actualCapturedAtUtc"), "qualification.actualCapturedAtUtc") == observed
             and _clock(qualification.get("createdAtUtc"), "qualification.createdAtUtc") == prepared,
             "SOURCE_QUALIFICATION:CLOCKS_DIFFER")

    # MAIN's source review: the exact bytes the producer pinned, an exact PASS for the accepted source and
    # its publisher. Its clock is proved by the producer's own start record, which pinned those bytes
    # before any source request; a self-declared recordedAtUtc, when present, must agree.
    review = _json(_pinned(h.get("independentReviewRef"), x.review_path, "MAIN_SOURCE_REVIEW"), "MAIN_SOURCE_REVIEW")
    _require(review.get("status") == REVIEW_STATUS and review.get("sourceManifestSha256") == x.source_manifest.sha256
             and review.get("scope") == REVIEW_SCOPE and _same_ref(review.get("publisherRef"), listed.get("publish_current.py")),
             "MAIN_SOURCE_REVIEW:NOT_AN_EXACT_PASS_FOR_THE_ACCEPTED_SOURCE")
    workflow_start = _json(_read_exact(run_root / "WORKFLOW_START.json", "WORKFLOW_START"), "WORKFLOW_START")
    _require(workflow_start.get("schema") == WORKFLOW_START_SCHEMA and workflow_start.get("slateDate") == slate
             and workflow_start.get("sourceReviewRef") == h.get("independentReviewRef")
             and _matches(workflow_start.get("sourceManifestRef"), x.source_manifest),
             "WORKFLOW_START:DOES_NOT_BIND_THIS_REVIEW_SOURCE_AND_DATE")
    _all_false(workflow_start.get("authority"), "WORKFLOW_START")
    workflow_started = _clock(workflow_start.get("startedAtUtc"), "workflowStart.startedAtUtc")
    if "recordedAtUtc" in review:
        _require(_clock(review["recordedAtUtc"], "review.recordedAtUtc") <= workflow_started,
                 "CLOCK_ORDER:MAIN_SOURCE_REVIEW_RECORDED_AFTER_THE_RUN_STARTED")

    # 9. Every new forecast object, one original start agreed everywhere, against the pinned baseline.
    baseline_rows = (baseline.get("dates") or {}).get(slate)
    _require(isinstance(baseline_rows, list), "SCHEDULE_BASELINE:SLATE_DATE_NOT_IN_THE_PINNED_BASELINE")
    game_by_key = dict(zip(game_keys, games))
    item_by_key = dict(zip(receipt_keys, receipt_new))
    starts: dict[str, datetime] = {}
    members: list[dict[str, Any]] = []
    for row in new_rows:
        k = _key(row)
        gid, fid, sha = k
        g, item = game_by_key[k], item_by_key[k]
        label = f"NEW:{gid}"
        rel = f"objects/sha256/{sha}/forecast.json"
        _require(row.get("relativePath") == rel, f"{label}:ROW_NOT_CONTENT_ADDRESSED")
        _require(g.get("object") == {"relativePath": rel, "sha256": sha, "bytes": row.get("bytes"),
                                     "canonicalGameId": gid, "forecastId": fid}, f"{label}:HANDOFF_OBJECT_IS_NOT_THE_ROW")
        _require(item.get("object") == {"path": rel, "sha256": sha, "bytes": row.get("bytes")},
                 f"{label}:RECEIPT_OBJECT_IS_NOT_THE_ROW")
        doc = _json(_addressed({"path": rel, "sha256": sha, "bytes": row.get("bytes")}, x.exchange_root,
                               "objects", "forecast.json", label), label)
        _require(doc.get("forecastId") == fid and c.forecast_identity(doc) == fid, f"{label}:IDENTITY")
        game = doc.get("game") if isinstance(doc.get("game"), dict) else {}
        home = game.get("home") if isinstance(game.get("home"), dict) else {}
        away = game.get("away") if isinstance(game.get("away"), dict) else {}
        source_id = game.get("sourceGameId")
        _require(isinstance(source_id, str) and game.get("canonicalGameId") == gid == f"nhl:game:{source_id}"
                 and g.get("sourceGameId") == source_id, f"{label}:GAME_IDENTITY")
        _require(g.get("home") == home.get("triCode") and g.get("away") == away.get("triCode")
                 and g.get("homeTeamId") == home.get("sourceTeamId") and g.get("awayTeamId") == away.get("sourceTeamId"),
                 f"{label}:TEAMS_OR_ORIENTATION")
        start = _clock(game.get("scheduledStartUtc"), f"{label}.scheduledStartUtc")
        _require(_clock(item.get("scheduledStartUtc"), f"{label}.receipt.start") == start
                 and _clock(g.get("originalScheduledStartUtc"), f"{label}.handoff.start") == start,
                 f"{label}:ORIGINAL_START_DISAGREES")
        named = [r for r in baseline_rows if isinstance(r, dict) and r.get("game_id") == source_id]
        _require(len(named) == 1 and _clock(named[0].get("game_time"), f"{label}.baseline") == start
                 and named[0].get("home_team_abbr") == home.get("triCode")
                 and named[0].get("away_team_abbr") == away.get("triCode"), f"{label}:NOT_THE_BASELINE_ORIGINAL")
        captured = [r for r in input_games if isinstance(r, dict) and r.get("game_id") == source_id]
        _require(len(captured) == 1 and _clock(captured[0].get("game_time"), f"{label}.input") == start,
                 f"{label}:NOT_IN_THE_CURRENT_INPUT")
        _require(doc.get("researchStatus") == "RESEARCH_ONLY", f"{label}:RESEARCH_STATUS")
        _all_false(doc.get("authority"), label)
        _require({k2: (doc.get("model") or {}).get(k2) for k2 in model} == model, f"{label}:NOT_THE_ACCEPTED_MODEL")
        _require((doc.get("provenance") or {}).get("producerEvidenceRef") == g.get("producerEvidence"),
                 f"{label}:PRODUCER_EVIDENCE_NOT_THE_HANDOFF_REFERENCE")
        _addressed(g.get("producerEvidence"), x.exchange_root, "evidence", "producer-receipt.json", f"{label}:EVIDENCE")
        _require(doc.get("publishedAtUtc") is None, f"{label}:OBJECT_CARRIES_ITS_OWN_PUBLICATION_CLOCK")
        _require(_clock(row.get("publishedAtUtc"), f"{label}.row.publishedAtUtc") == object_done,
                 f"{label}:ROW_CLOCK_IS_NOT_THE_OBJECT_EXPOSURE")
        _require(_clock(doc.get("sourceCutoffUtc"), f"{label}.sourceCutoffUtc") == observed
                 and _clock(doc.get("sourceRetrievedAtUtc"), f"{label}.sourceRetrievedAtUtc") == observed
                 and _clock(doc.get("createdAtUtc"), f"{label}.createdAtUtc") == prepared
                 and _clock(g.get("forecastCreatedAtUtc"), f"{label}.handoff.created") == prepared,
                 f"{label}:SOURCE_OR_CREATION_CLOCK_DIFFERS")
        starts[gid] = start
        members.append({"canonicalGameId": gid, "forecastId": fid, "objectSha256": sha,
                        "originalScheduledStartUtc": start.isoformat()})

    # 10. The clock chain, then strictly before every original start.
    chain = (("workflowStart.startedAtUtc", workflow_started), ("capture.requestStartedAtUtc", requested),
             ("capture.responseCompletedAtUtc", observed), ("preparation.createdAtUtc", prepared),
             ("publicationStart.startedAtUtc", publication_started),
             ("objectExposureStartedAtUtc", object_started), ("objectExposureCompletedAtUtc", object_done),
             ("manifestExposureCompletedAtUtc", manifest_done), ("currentPointerExposureCompletedAtUtc", pointer_done),
             ("handoff.createdAtUtc", handoff_created), ("witness.exposureVerifiedAtUtc", exposed),
             ("index.createdAtUtc", index_created), ("workflowComplete.completedAtUtc", completed), ("now", now))
    for (a, ta), (b, tb) in zip(chain, chain[1:]):
        _require(ta <= tb, f"CLOCK_ORDER:{a}>{b}")
    _require(index_created < min(starts.values()), "DELIVERY_NOT_STRICTLY_BEFORE_EVERY_ORIGINAL_START")
    _require(object_started.astimezone(ET).date().isoformat() == slate, "SLATE_DATE_IS_NOT_THE_PUBLICATION_DATE")

    return {
        "slateDate": slate,
        "runRoot": str(run_root),
        "interface": {"sourceManifestSha256": x.source_manifest.sha256,
                      "interfaceContractSha256": x.interface_contract.sha256},
        "index": {"sha256": _sha(index_raw), "bytes": len(index_raw)},
        "witness": {"sha256": index["exposureReceipt"]["sha256"], "bytes": index["exposureReceipt"]["bytes"]},
        "handoff": {"path": str(run_root / "HANDOFF.json"), "sha256": index["handoff"]["sha256"],
                    "bytes": len(handoff_raw)},
        "manifest": {"sha256": h["manifest"]["sha256"], "bytes": h["manifest"]["bytes"]},
        "priorManifest": {"sha256": h["priorManifestRef"]["sha256"], "bytes": h["priorManifestRef"]["bytes"]},
        "pointer": {"sha256": h["pointer"]["sha256"], "bytes": h["pointer"]["bytes"]},
        "counts": {"new": len(new_rows), "preserved": len(prior_rows), "total": len(current_rows)},
        "newMembers": members,
        "omittedAtPublication": h.get("omittedAtPublication"),
        "originalPointer": {"sha256": original_ref["sha256"], "bytes": original_ref["bytes"],
                            "genesis": original_ref["sha256"] == x.genesis_pointer_sha256},
        "clocks": {**{name: moment.isoformat() for name, moment in chain if name != "now"},
                   "earliestOriginalStartUtc": min(starts.values()).isoformat()},
        "authority": {flag: False for flag in AUTHORITY_FLAGS},
    }


# ------------------------------------------------------------------- import --
def _later_producer_runs(x: ForwardInterface, now: datetime) -> list[dict[str, Any]] | None:
    """Dated workspaces after the indexed slate, up to today (ET). Reporting only: never believed or imported.

    A refusal names the indexed workspace; once a later run has replaced the exchange pointer (or failed
    before re-indexing), the later run's own state is what an operator needs to see.
    """

    try:
        slate = date.fromisoformat(json.loads(x.index_path.read_bytes())["slateDate"])
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None
    runs = []
    for root in sorted(x.outputs_root.glob("nhl-current-slate-producer-*-v1")):
        day = _workspace_date(root)
        if day is not None and slate < day <= now.astimezone(ET).date():
            runs.append({"runRoot": str(root), "failureRecords": [n for n in FAILURE_RECORDS if (root / n).exists()],
                         "completed": (root / "WORKFLOW_COMPLETE.json").is_file()})
    return runs


def _indexed_delivery_held(session: Session, x: ForwardInterface) -> bool:
    """Reporting only: whether every new forecast the index-pinned handoff names is already held. Never writes."""

    from app.models import NhlResearchForecast

    try:
        index = json.loads(x.index_path.read_bytes())
        pin = index["handoff"]
        raw = (x.run_root(index["slateDate"]) / "HANDOFF.json").read_bytes()
        if _sha(raw) != pin["sha256"] or len(raw) != pin["bytes"]:
            return False
        ids = {g["forecastId"] for g in json.loads(raw)["games"]}
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError):
        return False
    if not ids or not all(isinstance(i, str) for i in ids):
        return False
    held = set(session.scalars(select(NhlResearchForecast.forecast_id)
                               .where(NhlResearchForecast.forecast_id.in_(list(ids)))))
    return held == ids


def consume_forward_delivery(session: Session, interface: ForwardInterface | None = None, *,
                             now: datetime | None = None) -> dict[str, Any]:
    """Discover, prove and import the indexed forward delivery. Does not commit.

    ``writes`` says whether the session holds import rows the caller should
    commit. Every other outcome -- including an import in which any NEW member
    was not admitted -- must be rolled back, so nothing latches and the next
    poll retries while the delivery's pointer is still current.
    """

    from app.models import NhlResearchForecast

    x = interface or PRODUCTION_INTERFACE
    now = (now or datetime.now(UTC)).astimezone(UTC)
    base = {"records": 0, "writes": False, "research_only": True, "paper_authority": False}
    try:
        found = discover(x, now=now)
    except DeliveryNotReady as exc:
        return {**base, "status": "NOT_READY", "reason": str(exc)}
    except DeliveryRefused as exc:
        # Once a later run has replaced the pointer, an indexed delivery that was already imported can no
        # longer verify; say so rather than report a refusal of something that is fully held.
        superseded = str(exc).startswith("POINTER:") and _indexed_delivery_held(session, x)
        return {**base, "status": "SUPERSEDED_AFTER_CONSUMPTION" if superseded else "REFUSED", "reason": str(exc),
                "laterProducerRuns": _later_producer_runs(x, now)}
    want = {m["forecastId"]: m["canonicalGameId"] for m in found["newMembers"]}
    held = set(session.scalars(select(NhlResearchForecast.forecast_id)
                               .where(NhlResearchForecast.forecast_id.in_(list(want)))))
    if held == set(want):
        return {**base, "status": "ALREADY_CONSUMED", "manifest_sha256": found["manifest"]["sha256"],
                "slateDate": found["slateDate"]}
    run_id = f"nhl-forward-{found['slateDate']}-{found['handoff']['sha256'][:12]}"
    try:
        receipt = import_from_handoff(session, handoff_path=Path(found["handoff"]["path"]),
                                      pin_sha256=found["handoff"]["sha256"], exchange_root=x.exchange_root,
                                      run_id=run_id, now=now)
    except HandoffRefused as exc:
        return {**base, "status": "REFUSED_BY_IMPORTER", "reason": str(exc), "delivery": found}
    verified = receipt["verified"]
    classes = [m["class"] for m in verified["membership"]]
    if (verified["manifest"]["sha256"] != found["manifest"]["sha256"]
            or classes.count("NEW_CURRENT") != found["counts"]["new"]
            or classes.count("PRESERVED_ORIGINAL") != found["counts"]["preserved"]):
        # Cannot happen while both read the same pinned bytes; if it does, the caller must roll back.
        raise DeliveryRefused("IMPORTER_VERIFIED_A_DIFFERENT_DELIVERY")
    imported = receipt["import"]
    got = {r.get("identity"): r for r in imported["receipts"] if r.get("kind") == "FORECAST"}
    incomplete = sorted(f"{want[fid]}:{(got.get(fid) or {}).get('disposition', 'NO_RECEIPT')}:"
                        f"{(got.get(fid) or {}).get('problems')}" for fid in want
                        if (got.get(fid) or {}).get("disposition") not in ("IMPORTED", "REVISED", "DUPLICATE"))
    outcome = {"run_id": run_id, "forecasts": imported["forecasts"], "problems": imported["problems"],
               "import_started_utc": receipt["import_started_utc"], "delivery": found}
    if incomplete or imported["problems"]:
        # Nothing may be committed: committed rows would latch the delivery while a new member is missing.
        return {**base, **outcome, "status": "IMPORT_INCOMPLETE", "notAdmittedNewMembers": incomplete}
    return {**base, **outcome, "status": "IMPORTED", "writes": True,
            "records": int(imported["forecasts"].get("IMPORTED", 0)) + int(imported["forecasts"].get("REVISED", 0))}


__all__ = ["DeliveryNotReady", "DeliveryRefused", "ForwardInterface", "PRODUCTION_INTERFACE", "Pin",
           "consume_forward_delivery", "discover"]
