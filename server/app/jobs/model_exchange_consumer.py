"""Scheduler-ready consumer for verified Model Studio forecast bundles.

WHAT THIS IS
------------
The smallest wrapper around the EXISTING bundle importer
(:func:`app.forecast_exchange.importer.verify_and_load_any_bundle` via
``ShadowBoardService.import_bundle``). It scans one configured Model Exchange
location, verifies every candidate through that same importer, and imports only
bundles AISP has not already registered.

WHAT IT MAY NOT DO
------------------
It executes and refits nothing, calls no provider, and creates no official
decision or position. It writes only the immutable research-shadow bundle and
forecast rows the importer already writes.

WHY IT DOES NOT RESCAN EVERYTHING
---------------------------------
The exchange publishes hundreds of historical bundles alongside a small current
set. Re-importing an older schedule-wide snapshot would churn identical
evidence and could displace a newer forecast on a board that shows the latest
snapshot. So the scan is restricted to the CURRENT set the exchange itself
declares in its research-shadow index, and any bundle already registered by
checksum is skipped without re-reading it.

TIMESTAMPS
----------
Three different facts are preserved separately by the importer and are never
collapsed here: when the source evidence was cut, when the producer published
the forecast, and when AISP imported it. Import time is recorded as it happens
and is never backdated.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import ShadowForecastBundle

#: Only a 64-hex content address may name a bundle directory. Anything else --
#: a relative segment, a symlink target, a partial upload -- is not a bundle.
_ADDRESS = re.compile(r"^[0-9a-f]{64}$")

#: Index files that declare which bundles are current. A bundle outside every
#: current set is historical and is not imported by the scan.
_INDEX_FILENAMES = ("nfl-2026-research-shadow-index.json",)

SUPPORTED_INDEX_SCHEMAS = frozenset({
    "ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V1",
    "ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V2",
})


@dataclass
class ConsumeOutcome:
    """What one scan did, and what it deliberately did not do."""

    root: str
    dry_run: bool
    scanned: int = 0
    accepted: list[dict[str, Any]] = field(default_factory=list)
    reused: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    supplements_scanned: int = 0
    supplements_accepted: list[dict[str, Any]] = field(default_factory=list)
    supplements_skipped: list[str] = field(default_factory=list)
    supplements_rejected: list[dict[str, Any]] = field(default_factory=list)
    studies: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "dry_run": self.dry_run,
            "scanned": self.scanned,
            "supplements_scanned": self.supplements_scanned,
            "supplements_accepted": len(self.supplements_accepted),
            "supplements_skipped": len(self.supplements_skipped),
            "supplements_rejected": len(self.supplements_rejected),
            "supplement_records": self.supplements_accepted,
            "supplement_rejections": self.supplements_rejected,
            "studies": self.studies,
            "accepted_count": len(self.accepted),
            "reused_count": len(self.reused),
            "rejected_count": len(self.rejected),
            "skipped_count": len(self.skipped),
            "accepted": self.accepted,
            "reused": self.reused,
            "rejected": self.rejected,
            # Already-registered bundles dominate the count; list only their
            # addresses so the report stays readable.
            "skipped": [item["bundle_sha256"] for item in self.skipped],
            "models_executed": 0,
            "models_refitted": 0,
            "provider_calls": 0,
            "official_decisions_created": 0,
            "positions_created": 0,
        }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def current_bundle_addresses(root: Path) -> set[str] | None:
    """Addresses the exchange declares CURRENT, or None when it declares none.

    Returning None means "no index found": the caller then imports nothing
    rather than falling back to the whole historical archive.
    """

    found: set[str] = set()
    saw_index = False
    for name in _INDEX_FILENAMES:
        path = root / name
        if not path.is_file():
            continue
        try:
            index = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(index, dict):
            continue
        if index.get("schemaVersion") not in SUPPORTED_INDEX_SCHEMAS:
            continue
        saw_index = True
        for value in index.get("currentBundleSha256s") or []:
            text = str(value).lower()
            if _ADDRESS.fullmatch(text):
                found.add(text)
    return found if saw_index else None


def discover_bundles(root: Path) -> list[Path]:
    """Content-addressed bundle directories directly under ``<root>/sha256``.

    Only a direct child whose name is a 64-hex address and which is a real
    directory qualifies. Nothing is followed outside the root, so a crafted
    name cannot reach another location.
    """

    base = (root / "sha256").resolve()
    if not base.is_dir():
        return []
    resolved_root = root.resolve()
    bundles: list[Path] = []
    for entry in sorted(base.iterdir()):
        if not _ADDRESS.fullmatch(entry.name.lower()):
            continue
        candidate = entry.resolve()
        if resolved_root not in candidate.parents or not candidate.is_dir():
            continue
        bundles.append(candidate)
    return bundles


def _registered_addresses(session: Session) -> set[str]:
    return {
        str(value).lower()
        for value in session.scalars(select(ShadowForecastBundle.bundle_sha256)).all()
    }


def consume_forecast_bundles(
    session: Session,
    settings: Settings,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Verify and import every unseen CURRENT bundle. Read-only when dry_run."""

    from app.forecast_exchange.importer import ForecastBundleImportError
    from app.services.shadow_board_service import ShadowBoardService

    exchange_root = Path(root or settings.model_exchange_forecast_bundle_dir)
    outcome = ConsumeOutcome(root=str(exchange_root), dry_run=dry_run)
    if not exchange_root.is_dir():
        outcome.rejected.append({
            "bundle_sha256": None,
            "reason": f"Configured Model Exchange location is not a directory: {exchange_root}",
        })
        return outcome.as_dict()

    current = current_bundle_addresses(exchange_root)
    if current is None:
        outcome.rejected.append({
            "bundle_sha256": None,
            "reason": (
                "No supported research-shadow index declares a current bundle set; "
                "refusing to import the historical archive"
            ),
        })
        return outcome.as_dict()

    already = _registered_addresses(session)
    service = ShadowBoardService(session, settings)

    for bundle_dir in discover_bundles(exchange_root):
        address = bundle_dir.name.lower()
        if address not in current:
            # Historical snapshot: real evidence, but not what the exchange
            # currently publishes, so it is not imported by the scan.
            continue
        outcome.scanned += 1
        if address in already:
            outcome.skipped.append({"bundle_sha256": address, "reason": "already registered"})
            continue
        if limit is not None and len(outcome.accepted) >= limit:
            outcome.skipped.append({"bundle_sha256": address, "reason": "run limit reached"})
            continue

        try:
            verified = _verify_eligible(bundle_dir)
        except ForecastBundleImportError as error:
            outcome.rejected.append({"bundle_sha256": address, "reason": str(error)})
            continue
        except (OSError, ValueError) as error:
            outcome.rejected.append({
                "bundle_sha256": address, "reason": f"{type(error).__name__}: {error}",
            })
            continue

        record = {
            "bundle_sha256": address,
            "bundle_id": verified["bundle_id"],
            "sport": verified["sport"],
            "slate_date": verified["slate_date"],
            "forecasts": verified["forecasts"],
            # Kept apart on purpose; the importer persists each separately.
            "source_evidence_utc": verified["source_evidence_utc"],
            "published_utc": verified["published_utc"],
        }
        if dry_run:
            record["would_import"] = True
            outcome.accepted.append(record)
            continue

        try:
            result = service.import_bundle(bundle_dir)
        except ForecastBundleImportError as error:
            session.rollback()
            outcome.rejected.append({"bundle_sha256": address, "reason": str(error)})
            continue
        record["imported_at_utc"] = datetime.now(UTC).isoformat()
        if result.get("idempotent_reuse"):
            outcome.reused.append(record)
        else:
            outcome.accepted.append(record)
        already.add(address)

    _consume_supplements(session, settings, exchange_root, outcome, dry_run=dry_run)
    _consume_nfl_labs_v1(session, exchange_root, outcome, dry_run=dry_run)
    _consume_lab_study(session, settings, outcome, dry_run=dry_run)
    payload = outcome.as_dict()
    # MLB V18 publishes its own content-addressed packages beside the NFL
    # bundles. Same single owner, same cadence, same refusal to execute
    # anything -- but a different manifest shape, so it gets its own scanner
    # rather than being forced through the bundle index.
    payload["v18"] = _consume_v18(session, settings, dry_run=dry_run)
    return payload


def _consume_v18(session: Session, settings: Settings, *, dry_run: bool) -> dict[str, Any]:
    """The MLB V18 package scan, behind its own switch."""

    if not settings.mlb_v18_exchange_auto_import_enabled:
        return {
            "enabled": False,
            "reason": (
                "MLB_V18_EXCHANGE_AUTO_IMPORT_ENABLED is false; V18 packages "
                "are not consumed"
            ),
            "imported_count": 0, "reused_count": 0,
            "accounting_only_count": 0, "rejected_count": 0,
        }
    from app.jobs.v18_exchange_consumer import consume_v18_packages

    return {"enabled": True, **consume_v18_packages(session, settings, dry_run=dry_run)}


def _consume_nfl_labs_v1(session: Session, exchange_root: Path, outcome: ConsumeOutcome,
                         *, dry_run: bool) -> None:
    """The producer V1 artifact family, in the existing immutable supplement registry."""
    from app.forecast_exchange.nfl_labs_v1 import ROOT_NAME, register_labs_current, verify_labs_current
    from app.forecast_exchange.supplements import SupplementImportError

    root = exchange_root.parent if exchange_root.name == "aisp-daily-forecast-bundles" else exchange_root
    if not (root / ROOT_NAME / "current.json").is_file():
        return
    try:
        verified = verify_labs_current(root)
        outcome.supplements_scanned += len(verified)
        # Failure of this family must not roll back earlier bundle imports.
        with session.begin_nested():
            records = register_labs_current(session, verified, dry_run=dry_run)
        for record in records:
            if record["idempotent_reuse"]:
                outcome.supplements_skipped.append(record["supplement_sha256"])
            else:
                outcome.supplements_accepted.append({**record, "would_import": dry_run})
    except (SupplementImportError, OSError, ValueError, KeyError, TypeError) as error:
        outcome.supplements_rejected.append({"family": "NFL_LABS_CONTRACT_V1", "reason": str(error)})


def _consume_supplements(session: Session, settings: Settings, exchange_root: Path,
                         outcome: ConsumeOutcome, *, dry_run: bool) -> None:
    """Research supplements (AISP_RESEARCH_SUPPLEMENT_V1): separate container, same rules.

    Only supplements the exchange declares CURRENT are considered; each is
    verified file-by-file before any content is read, registered idempotently
    by content address, and refused when it does not bind to a forecast AISP
    already holds.
    """

    from app.forecast_exchange.supplements import (
        SupplementImportError,
        current_supplement_addresses,
        discover_supplements,
        register_supplement,
        verify_supplement,
    )
    from app.models import ResearchSupplement

    # The supplement tree lives beside the bundle tree in the exchange.
    supplement_root = exchange_root.parent if exchange_root.name == "aisp-daily-forecast-bundles" else exchange_root
    current = current_supplement_addresses(supplement_root) or set()
    registered = {
        str(value).lower()
        for value in session.scalars(select(ResearchSupplement.supplement_sha256)).all()
    }
    # NBA and CFB paper eligibility have no index of their own. They are
    # admitted by an explicit allowlist instead of being silently ignored: only
    # their dedicated eligibility trees, only the exact registered eligibility
    # version for that sport, and only successor-chain heads.
    allowlisted = _allowlisted_eligibility(supplement_root, registered, outcome)
    for directory in discover_supplements(supplement_root):
        address = directory.name.lower()
        if address not in current and address not in allowlisted:
            continue
        outcome.supplements_scanned += 1
        if address in registered:
            outcome.supplements_skipped.append(address)
            continue
        try:
            verified = allowlisted.get(address) or verify_supplement(directory)
            if dry_run:
                outcome.supplements_accepted.append({
                    "supplement_sha256": address, "kind": verified.kind,
                    "binding": verified.binding, "would_import": True,
                })
                continue
            record = register_supplement(session, verified)
        except SupplementImportError as error:
            session.rollback()
            outcome.supplements_rejected.append({"supplement_sha256": address, "reason": str(error)})
            continue
        except (OSError, ValueError) as error:
            session.rollback()
            outcome.supplements_rejected.append({
                "supplement_sha256": address, "reason": f"{type(error).__name__}: {error}",
            })
            continue
        record["imported_at_utc"] = datetime.now(UTC).isoformat()
        outcome.supplements_accepted.append(record)
        registered.add(address)


#: Exchange trees whose paper-eligibility supplements are admitted without an
#: index, keyed to the one sport each may carry.
ALLOWLISTED_ELIGIBILITY_TREES = {
    "nba-gen1-research-v1/paper-eligibility": "NBA",
    "cfb-gen1-research-v1/paper-eligibility": "CFB",
}


def _allowlisted_eligibility(root: Path, registered: set[str], outcome: ConsumeOutcome) -> dict[str, Any]:
    """Verified, unregistered NBA/CFB eligibility heads under the allowlisted trees.

    Everything else found there is recorded as skipped with its reason, so a
    refusal is visible in the job receipt rather than being an absence.
    """

    from app.forecast_exchange.supplements import (
        KIND_PAPER_ELIGIBILITY, PAPER_ELIGIBILITY_VERSIONS, SupplementImportError,
        chain_heads, verify_supplement,
    )

    verified_by_address: dict[str, Any] = {}
    for tree, sport in ALLOWLISTED_ELIGIBILITY_TREES.items():
        base = root / tree / "sha256"
        if not base.is_dir():
            continue
        entries = [e for e in sorted(base.iterdir()) if e.is_dir() and _is_address(e.name)]
        # Pass 1: every verifiable document in the tree, registered or not, so a
        # predecessor is recognised as superseded even when only its successor
        # is present in AISP.
        verified: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for entry in entries:
            try:
                verified[entry.name.lower()] = verify_supplement(entry)
            except (SupplementImportError, OSError, ValueError) as error:
                errors[entry.name.lower()] = str(error)
        _, superseded = chain_heads(list(verified.values()))
        superseded_set = set(superseded) | {
            str(item.supplement.get("predecessorSupplementAddress") or "").lower()
            for item in verified.values()}
        # Pass 2: admit unregistered heads of the exact version only.
        for entry in entries:
            address = entry.name.lower()
            if address in registered:
                continue
            if address in superseded_set:
                outcome.supplements_skipped.append(address)
                continue
            if address in errors:
                outcome.supplements_rejected.append({
                    "supplement_sha256": address, "tree": tree,
                    "reason": f"allowlisted tree, not admissible: {errors[address]}"})
                continue
            item = verified[address]
            doc = item.supplement
            if (item.kind != KIND_PAPER_ELIGIBILITY or str(doc.get("sport")).upper() != sport
                    or doc.get("artifactVersion") != PAPER_ELIGIBILITY_VERSIONS[sport]):
                outcome.supplements_rejected.append({
                    "supplement_sha256": address, "tree": tree,
                    "reason": (f"allowlisted tree admits only {PAPER_ELIGIBILITY_VERSIONS[sport]} "
                               f"for {sport}; found {item.kind}/{doc.get('artifactVersion')}/{doc.get('sport')}")})
                continue
            verified_by_address[address] = item
    return verified_by_address


def _is_address(name: str) -> bool:
    return len(name) == 64 and all(c in "0123456789abcdef" for c in name.lower())


def _consume_lab_study(session: Session, settings: Settings, outcome: ConsumeOutcome,
                       *, dry_run: bool) -> None:
    """Model Studio's overnight model laboratory, as one benchmark-study supplement."""

    from app.shadow_import.nfl_model_lab_study import (
        LabStudyImportError,
        register_lab_study,
        verify_lab_study,
    )

    lab_root = Path(settings.model_exchange_lab_study_dir)
    if not lab_root.is_dir():
        return
    try:
        if dry_run:
            verified = verify_lab_study(lab_root)
            outcome.studies.append({
                "handoff_sha256": verified["handoff_sha256"], "would_import": True,
            })
            return
        outcome.studies.append(register_lab_study(session, lab_root))
    except LabStudyImportError as error:
        session.rollback()
        outcome.studies.append({"rejected": True, "reason": str(error)})
    except (OSError, ValueError) as error:
        session.rollback()
        outcome.studies.append({"rejected": True, "reason": f"{type(error).__name__}: {error}"})


def _verify_eligible(bundle_dir: Path) -> dict[str, Any]:
    """Verify one bundle and assert the governance AISP requires to display it.

    Verification is the existing importer's, so checksums, the exact directory
    inventory, canonical encoding, child bindings and path safety are all
    enforced before anything here looks at the content.
    """

    from app.forecast_exchange.importer import (
        ForecastBundleImportError,
        verify_and_load_any_bundle,
    )

    imported = verify_and_load_any_bundle(bundle_dir)
    bundle = imported.bundle
    if str(getattr(bundle, "status", "")).upper() != "RESEARCH_SHADOW":
        raise ForecastBundleImportError("Bundle is not RESEARCH_SHADOW")
    if not getattr(bundle, "research_shadow_only", False):
        raise ForecastBundleImportError("Bundle is not research-shadow-only")
    if getattr(bundle, "execution_allowed", False):
        raise ForecastBundleImportError("Bundle asserts execution authority in AISP")
    if getattr(bundle, "model_promotion_allowed", False):
        raise ForecastBundleImportError("Bundle asserts model-promotion authority")

    forecasts = list(getattr(bundle, "forecasts", []) or [])
    source_cutoffs = [
        getattr(item, "source_cutoff_utc", None) or getattr(item, "schedule_as_of_utc", None)
        for item in forecasts
    ]
    published = [getattr(item, "available_as_of_utc", None) for item in forecasts]
    return {
        "bundle_id": imported.bundle_id,
        "sport": getattr(bundle, "sport", None),
        "slate_date": (
            bundle.slate_date.isoformat() if getattr(bundle, "slate_date", None) else None
        ),
        "forecasts": len(forecasts),
        "source_evidence_utc": max(
            (value.isoformat() for value in source_cutoffs if value), default=None
        ),
        "published_utc": max(
            (value.isoformat() for value in published if value), default=None
        ),
    }


def run_scheduled_import(session: Session, settings: Settings) -> dict[str, Any]:
    """Scheduler entry point. Does nothing unless explicitly enabled.

    A scheduler may call this on a cadence; until
    ``MODEL_EXCHANGE_AUTO_IMPORT_ENABLED`` is true it reports that it is
    disabled and imports nothing.
    """

    if not settings.model_exchange_auto_import_enabled:
        # The NFL bundle path stays off, but the MLB V18 path has its own
        # switch and must still run when only that one is on.
        payload = {
            "enabled": False,
            "reason": (
                "MODEL_EXCHANGE_AUTO_IMPORT_ENABLED is false; automatic bundle "
                "consumption is not active"
            ),
            "accepted_count": 0,
            "reused_count": 0,
            "rejected_count": 0,
            "skipped_count": 0,
            "v18": _consume_v18(session, settings, dry_run=False),
        }
        session.commit()
        return payload
    payload = consume_forecast_bundles(session, settings)
    session.commit()
    return {"enabled": True, **payload}


__all__ = [
    "ConsumeOutcome",
    "consume_forecast_bundles",
    "current_bundle_addresses",
    "discover_bundles",
    "run_scheduled_import",
]
