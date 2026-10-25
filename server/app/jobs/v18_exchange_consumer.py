"""Automatic discovery of published MLB V18 exchange packages.

WHAT IT ADDS
------------
The V18 producer publishes a content-addressed package per slate into
``mlb-v18-current``. Until now a person had to read the package, verify it and
run the importer by hand, which meant a slate's forecasts existed in the
exchange for a day before AISP knew about them. This scans that location on the
existing single-owner consumer cadence and hands the legitimate ones to the
SAME verified importer a person would have run.

WHAT IT REFUSES
---------------
Two package kinds live side by side in that directory and only one is a
forecast publication:

  * ``importable: true`` with forecasts -- a real slate publication;
  * ``RESEARCH_ACCOUNTING_ONLY_NO_FORECASTS`` with ``forecastCount: 0`` -- the
    producer's honest record of a slate it could NOT forecast. The 2026-09-11
    package is one of these: all fifteen games skipped because AISP had not
    registered tomorrow's canonical events yet.

An accounting package must never reach the forecast importer. It contains no
forecast to import, and passing it would either crash or, worse, look like a
successful import of nothing. It is recognised, counted, and left alone.

WHAT IT MAY NOT DO
------------------
It executes no model, calls no provider, creates no decision or position, and
writes nothing Gen1 can see. Everything it imports lands in the separate
RESEARCH_SHADOW track, and the import is keyed by object checksum so an
unchanged package is reused rather than duplicated.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings

PACKAGE_SCHEMA = "MLB_V18_PRECOMPUTED_EXCHANGE_PACKAGE_V1"
PACKAGE_FILE = "exchange-package.json"

#: Packages that verified from bytes AND were already fully registered earlier
#: in THIS process, keyed by address -> (manifest size, manifest mtime_ns,
#: monotonic time of that verification). Packages are content-addressed and
#: immutable, yet every pass re-hashed ~256 of them (the V18 stage was 168-215 s
#: of each 5-minute pass on 2026-09-18, holding the core lane in front of the
#: decision checkpoints). A changed manifest, a restart, or MEMO_TTL_SECONDS
#: elapsing forces full verification again.
_VERIFIED_MEMO: dict[str, tuple[int, int, float]] = {}
MEMO_TTL_SECONDS = 6 * 3600
#: At most this many TTL-expired packages are re-verified per pass; the rest
#: keep their memo until their turn. Without the cap every package expired in
#: the same pass (2026-09-19 03:28 ET: a 275 s pass, checkpoints waiting up to
#: 470 s), which inside a decision window re-creates the delay the memo removed.
#: New or changed packages are never capped.
MEMO_REVERIFY_PER_PASS = 20


def _manifest_stamp(package_dir: Path) -> tuple[int, int] | None:
    try:
        stat = (package_dir / PACKAGE_FILE).stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _memo_state(address: str, package_dir: Path) -> str:
    """HIT (use the memo), EXPIRED (unchanged but due for re-verification) or MISS."""

    from time import monotonic

    entry = _VERIFIED_MEMO.get(address)
    stamp = _manifest_stamp(package_dir)
    if entry is None or stamp is None or (entry[0], entry[1]) != stamp:
        return "MISS"
    return "HIT" if monotonic() - entry[2] < MEMO_TTL_SECONDS else "EXPIRED"


def _memo_hit(address: str, package_dir: Path) -> bool:
    return _memo_state(address, package_dir) == "HIT"


def _memo_store(address: str, package_dir: Path) -> None:
    from time import monotonic

    stamp = _manifest_stamp(package_dir)
    if stamp is not None:
        _VERIFIED_MEMO[address] = (stamp[0], stamp[1], monotonic())
ACCOUNTING_ONLY = "RESEARCH_ACCOUNTING_ONLY_NO_FORECASTS"

_ADDRESS = re.compile(r"^[0-9a-f]{64}$")

#: Rejection reasons, each naming the specific thing that was wrong.
NOT_A_PACKAGE = "NO_EXCHANGE_PACKAGE_MANIFEST"
UNSUPPORTED_SCHEMA = "UNSUPPORTED_PACKAGE_SCHEMA"
UNREADABLE = "PACKAGE_NOT_READABLE"
ACCOUNTING_PACKAGE = "ACCOUNTING_ONLY_NO_FORECASTS_TO_IMPORT"
NOT_IMPORTABLE = "PACKAGE_DECLARES_ITSELF_NOT_IMPORTABLE"
EXECUTION_CLAIMED = "PACKAGE_CLAIMS_EXECUTION_OR_AUTHORITY"
CHILD_MISMATCH = "CHILD_CHECKSUM_OR_BYTE_COUNT_MISMATCH"
MISSING_BINDING = "PACKAGE_OMITS_HANDOFF_OR_RECEIPT"


@dataclass
class V18ConsumeOutcome:
    root: str
    dry_run: bool
    scanned: int = 0
    imported: list[dict[str, Any]] = field(default_factory=list)
    reused: list[dict[str, Any]] = field(default_factory=list)
    accounting_only: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "dry_run": self.dry_run,
            "scanned": self.scanned,
            "imported_count": len(self.imported),
            "reused_count": len(self.reused),
            "accounting_only_count": len(self.accounting_only),
            "rejected_count": len(self.rejected),
            "imported": self.imported,
            "reused": self.reused,
            "accounting_only": self.accounting_only,
            "rejected": self.rejected,
            "models_executed": 0,
            "provider_calls": 0,
            "official_decisions_created": 0,
            "positions_created": 0,
            "gen1_history_mutated": False,
        }


def discover_packages(root: Path) -> list[Path]:
    """Content-addressed package directories directly under ``<root>/sha256``."""

    base = (root / "sha256")
    if not base.is_dir():
        return []
    resolved_root = root.resolve()
    found: list[Path] = []
    for entry in sorted(base.iterdir()):
        if not _ADDRESS.fullmatch(entry.name.lower()):
            continue
        candidate = entry.resolve()
        # Nothing outside the configured root is ever opened, so a crafted
        # name or a symlink cannot reach another location.
        if resolved_root not in candidate.parents or not candidate.is_dir():
            continue
        found.append(candidate)
    return found


def classify(package_dir: Path) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """(kind, manifest, reason). kind is IMPORTABLE, ACCOUNTING or None."""

    # The producer holds some published packages read-only or locked, and one
    # unreadable directory must never abort the scan of every other package.
    manifest_path = package_dir / PACKAGE_FILE
    try:
        if not manifest_path.is_file():
            return None, None, NOT_A_PACKAGE
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except PermissionError as error:
        return None, None, f"{UNREADABLE}: {error.strerror or error}"
    except (OSError, json.JSONDecodeError):
        return None, None, NOT_A_PACKAGE
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != PACKAGE_SCHEMA:
        return None, manifest if isinstance(manifest, dict) else None, UNSUPPORTED_SCHEMA

    # Authority claims are checked before anything else is read: a package that
    # asserts it may execute or wager is rejected outright rather than parsed.
    if (manifest.get("executionAllowedInAISP") is True
            or manifest.get("wageringAuthority") is True
            or str(manifest.get("promotionStatus") or "NOT_PROMOTED") != "NOT_PROMOTED"
            or manifest.get("researchShadowOnly") is False):
        return None, manifest, EXECUTION_CLAIMED

    if manifest.get("status") == ACCOUNTING_ONLY or int(manifest.get("forecastCount") or 0) == 0:
        return "ACCOUNTING", manifest, None
    if manifest.get("importable") is not True:
        return None, manifest, NOT_IMPORTABLE
    return "IMPORTABLE", manifest, None


def verify_children(package_dir: Path, manifest: dict[str, Any]) -> str | None:
    """None when every declared child matches by checksum AND byte count."""

    import hashlib

    for entry in manifest.get("files") or []:
        relative = entry.get("path") or entry.get("relativePath")
        if not relative:
            return CHILD_MISMATCH
        child = (package_dir / str(relative).replace("/", "\\").replace("\\", "/")).resolve()
        if package_dir.resolve() not in child.parents and child != package_dir.resolve():
            return CHILD_MISMATCH
        try:
            if not child.is_file():
                return f"{CHILD_MISMATCH}: missing {relative}"
            payload = child.read_bytes()
        except OSError as error:
            return f"{UNREADABLE}: {relative} ({error.strerror or error})"
        want_bytes = entry.get("bytes")
        if want_bytes is not None and len(payload) != int(want_bytes):
            return f"{CHILD_MISMATCH}: {relative} is {len(payload)} bytes, manifest says {want_bytes}"
        want_sha = str(entry.get("sha256") or "").lower()
        if want_sha and hashlib.sha256(payload).hexdigest() != want_sha:
            return f"{CHILD_MISMATCH}: {relative} checksum differs"
    return None


def consume_v18_packages(
    session: Session, settings: Settings, *, dry_run: bool = False, root: Path | None = None,
) -> dict[str, Any]:
    """Import every legitimate unseen V18 package. Read-only when dry_run."""

    from app.shadow_import.mlb_v18_daily import (
        DailyV18ImportError, register_daily_slate, verify_daily_slate,
    )

    exchange_root = Path(root or _default_root(settings))
    outcome = V18ConsumeOutcome(root=str(exchange_root), dry_run=dry_run)
    if not exchange_root.is_dir():
        outcome.rejected.append({
            "package": None,
            "reason": f"Configured V18 exchange location is not a directory: {exchange_root}",
        })
        return outcome.as_dict()

    reverify_budget = MEMO_REVERIFY_PER_PASS
    # Oldest memo first, so the periodic re-verification rolls through every package.
    packages = sorted(discover_packages(exchange_root),
                      key=lambda d: _VERIFIED_MEMO.get(d.name.lower(), (0, 0, 0.0))[2])
    for package_dir in packages:
        outcome.scanned += 1
        address = package_dir.name.lower()
        try:
            kind, manifest, reason = classify(package_dir)
        except OSError as error:
            outcome.rejected.append({
                "package": address, "reason": f"{UNREADABLE}: {error.strerror or error}",
            })
            continue

        if kind is None:
            outcome.rejected.append({"package": address, "reason": reason})
            continue
        if kind != "ACCOUNTING" and not dry_run:
            state = _memo_state(address, package_dir)
            if state == "EXPIRED":
                if reverify_budget > 0:
                    reverify_budget -= 1  # falls through to full verification this pass
                else:
                    state = "HIT"  # unchanged manifest; re-verified on a later pass
            if state == "HIT":
                outcome.reused.append({
                    "package": address, "registered": 0,
                    "fast_path": "VERIFIED_AND_FULLY_REGISTERED_EARLIER_IN_THIS_PROCESS",
                })
                continue
        if kind == "ACCOUNTING":
            # Counted, never imported. This is the producer telling us it could
            # not forecast a slate, which is evidence in itself.
            outcome.accounting_only.append({
                "package": address,
                "status": manifest.get("status"),
                "games": manifest.get("gameCount"),
                "forecasts": manifest.get("forecastCount", 0),
                "note": "Producer published accounting only; there is no forecast to import.",
            })
            continue

        mismatch = verify_children(package_dir, manifest or {})
        if mismatch:
            outcome.rejected.append({"package": address, "reason": mismatch})
            continue

        handoff = ((manifest or {}).get("handoff") or {}).get("path")
        receipt = ((manifest or {}).get("receipt") or {}).get("path")
        if not handoff or not receipt:
            outcome.rejected.append({"package": address, "reason": MISSING_BINDING})
            continue

        try:
            verified = verify_daily_slate(handoff, receipt)
        except DailyV18ImportError as error:
            outcome.rejected.append({"package": address, "reason": str(error)})
            continue

        record = {
            "package": address,
            "slate_date": str(verified.handoff.get("slateDate")),
            "handoff_sha256": verified.handoff_sha256,
            "forecasts": len(verified.forecasts),
        }
        if dry_run:
            record["would_import"] = True
            outcome.imported.append(record)
            continue

        if already_fully_registered(session, verified):
            # Every forecast, and both view supplements on each, are already
            # registered under this exact handoff. Re-running registration
            # would reload every V18 forecast row twice per item for nothing
            # (214 of a 217-second consumer pass on 2026-09-18), holding the
            # core lane in front of the decision checkpoints.
            record.update({"registered": 0, "already_present": len(verified.forecasts),
                           "fast_path": "ALREADY_FULLY_REGISTERED"})
            outcome.reused.append(record)
            _memo_store(address, package_dir)
            continue

        try:
            result = register_daily_slate(session, verified)
        except DailyV18ImportError as error:
            session.rollback()
            outcome.rejected.append({"package": address, "reason": str(error)})
            continue

        registered = len(getattr(result, "imported", []) or [])
        already = len(getattr(result, "already_present", []) or [])
        record.update({
            "registered": registered,
            "already_present": already,
            "imported_at_utc": datetime.now(UTC).isoformat(),
        })
        (outcome.imported if registered else outcome.reused).append(record)

    return outcome.as_dict()


def already_fully_registered(session: Session, verified: Any) -> bool:
    """True only when registering ``verified`` again would change nothing.

    Read-only. Requires the handoff's own bundle row, every forecast present
    under its object-addressed identity for the same game and object, and --
    where the forecast carries them -- both view supplements already attached
    with the same supplement hash (the exact checks the registration functions
    make before they skip). Anything else, including a new cumulative handoff
    whose forecasts are already present, takes the full registration path.
    """

    from sqlalchemy import select

    from app.models import ShadowForecast, ShadowForecastBundle
    from app.shadow_import.mlb_v18_daily import forecast_identity
    from app.shadow_import.mlb_v18_play_geometry import LINEAGE_KEY as PLAY_KEY
    from app.shadow_import.mlb_v18_population_geometry import LINEAGE_KEY as POPULATION_KEY

    forecasts = list(getattr(verified, "forecasts", ()) or ())
    if not forecasts:
        return False
    bundle = session.scalar(select(ShadowForecastBundle.id).where(
        ShadowForecastBundle.bundle_sha256 == verified.handoff_sha256))
    if bundle is None:
        return False
    identities = {forecast_identity(item.object_sha256): item for item in forecasts}
    rows = session.execute(
        select(ShadowForecast.forecast_id, ShadowForecast.canonical_game_id, ShadowForecast.lineage_detail)
        .where(ShadowForecast.forecast_id.in_(list(identities)))
    ).all()
    if len(rows) != len(identities):
        return False
    for forecast_id, game_id, lineage in rows:
        item = identities.get(forecast_id)
        lineage = lineage or {}
        if item is None or str(game_id) != str(item.forecast.get("gameId")):
            return False
        if lineage.get("objectSha256") != item.object_sha256:
            return False
        if item.play_geometry is not None and str((lineage.get(PLAY_KEY) or {}).get("supplementSha256"))                 != item.play_geometry.supplement_sha256:
            return False
        if item.population_geometry is not None and str((lineage.get(POPULATION_KEY) or {}).get("supplementSha256"))                 != item.population_geometry.supplement_sha256:
            return False
    return True


def _default_root(settings: Settings) -> Path:
    configured = getattr(settings, "model_exchange_v18_dir", "") or ""
    if configured:
        return Path(configured)
    # Sibling of the configured forecast-bundle location, which is where the
    # producer publishes.
    bundles = Path(settings.model_exchange_forecast_bundle_dir or ".")
    return bundles.parent / "mlb-v18-current"


__all__ = [
    "ACCOUNTING_ONLY", "ACCOUNTING_PACKAGE", "CHILD_MISMATCH", "EXECUTION_CLAIMED",
    "MISSING_BINDING", "NOT_A_PACKAGE", "NOT_IMPORTABLE", "PACKAGE_SCHEMA", "UNREADABLE",
    "UNSUPPORTED_SCHEMA", "V18ConsumeOutcome", "classify", "consume_v18_packages",
    "discover_packages", "verify_children",
]
