"""A CFB forecast's discovery lease, read the way the paper checkpoint would read it now. Read-only.

WHY THIS EXISTS
---------------
The producer renews every eligibility lease about every 15 minutes, and each
lease runs about 14.7 minutes from its manifest's creation. AISP's broad
consumer imports the newest manifest on its own 15-minute cadence, so for a
few minutes of most cycles the NEWEST RECORDED manifest says EXPIRED_LEASE even
though the producer has already published the renewal at its pointer. The
checkpoint never sees that gap: ``paper_decision.evaluate`` calls
``consume_discovery`` immediately before deciding. Readiness and the slate
surfaces read without importing, so they showed a false "lease expired"
between imports.

WHAT A PROJECTION IS
--------------------
``project`` takes the recorded reading (``production_import.present_eligibility``
shape) and, only when it is EXPIRED_LEASE or NOT_IN_CURRENT_DISCOVERY and the
consumer switch is on, looks at the producer's pointer exactly as far as the
consumer would on its next pass, without recording anything:

1. the pointer's manifest verifies from bytes (``_load_manifest``: declared
   SHA-256 and size, content address, schema) and the pinned handoff verdict is
   ACTIVE (the consumer refuses otherwise);
2. it is NOT already recorded, and it is newer than the recorded manifest;
3. its hash-bound predecessor IS a recorded manifest -- the lineage the
   consumer already accepted (a longer unrecorded chain is left to the
   consumer, and the projection then keeps the recorded reading);
4. its release verifies from bytes;
5. the forecast's own entry passes the consumer's light verification
   (``_light_verify``: index digest and publication receipt against the bytes
   AISP first imported).

The lease is then the pointer's (``currentlyEligible`` and an unexpired
``eligibilityValidUntilUtc``), reported with ``basis``
``PRODUCER_POINTER_VERIFIED`` and the recorded reading kept beside it. Anything
that stops short of that returns the recorded reading unchanged, annotated with
why (``pointer``). A recorded lease that lapsed less than one producer cadence
ago and has no verified renewal yet is still EXPIRED_LEASE (the checkpoint
would block on it right now), labelled as a renewal that is due.

WHAT IT NEVER DOES
------------------
Write or flush a row, import a package, extend, restamp or backdate a lease, or
touch what the checkpoint decides: ``cfb/paper_decision.py`` still imports and
reads the recorded manifest itself. A projection is display only.

COST
----
Readiness is served through the SWR read cache, and this runs at most once per
refresh per surface. A pointer manifest is parsed once per manifest SHA-256
(content addressed: its bytes cannot change under that SHA); a forecast's light
verification is cached per (manifest SHA-256, forecast id); the handoff verdict
for five minutes. A steady-state call reads the small pointer file and runs one
indexed lookup.
"""

from __future__ import annotations

import json
import pathlib
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cfb import production_discovery as pd
from app.cfb.production_import import (
    CURRENT, EXPIRED_LEASE, HISTORICAL, NOT_IN_CURRENT_DISCOVERY, _light_verify, _load_manifest,
    _predecessor_ref, present_eligibility,
)
from app.models import CfbDiscoveryManifest, CfbResearchForecast

#: The producer's renewal cadence (manifests ~15 min apart, leases ~14.7 min long).
PRODUCER_CADENCE = timedelta(minutes=15)
RECORDED = "RECORDED_MANIFEST"
POINTER = "PRODUCER_POINTER_VERIFIED"
#: Recorded states a newer verified manifest at the pointer can change.
PROJECTABLE = (EXPIRED_LEASE, NOT_IN_CURRENT_DISCOVERY)
_VERDICT_TTL_SECONDS = 300.0

_lock = threading.Lock()
_manifests: dict[str, dict[str, Any]] = {}          # manifest sha -> parsed pointer manifest
_entries: dict[tuple[str, str], dict[str, Any]] = {}  # (manifest sha, forecast id) -> light verification
_verdict: dict[str, Any] = {}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat() if value else None


def _minutes(delta: timedelta) -> float:
    return round(delta.total_seconds() / 60, 1)


def _handoff_active(settings: Any) -> bool:
    key = (str(getattr(settings, "cfb_production_handoff_path", "") or ""),
           str(getattr(settings, "cfb_production_handoff_sha256", "") or ""),
           bool(getattr(settings, "cfb_production_consumer_enabled", False)))
    now = time.monotonic()
    with _lock:
        if _verdict.get("key") == key and now - _verdict.get("at", 0.0) < _VERDICT_TTL_SECONDS:
            return bool(_verdict["active"])
    try:
        active = pd.production_status(settings).status == pd.ACTIVE
    except Exception:  # noqa: BLE001 -- an unreadable pin projects nothing
        active = False
    with _lock:
        _verdict.update({"key": key, "at": now, "active": active})
    return active


def _pointer_manifest(pointer_path: pathlib.Path) -> dict[str, Any]:
    """The pointer's manifest, verified from bytes and parsed once per SHA-256 (no row is read or written)."""

    pointer = json.loads(pointer_path.read_bytes())
    if pointer.get("schemaVersion") != pd.POINTER_SCHEMA:
        raise pd.DiscoveryRefused(f"pointer schema {pointer.get('schemaVersion')!r}")
    sha = str((pointer.get("manifest") or {}).get("sha256", "")).lower()
    with _lock:
        cached = _manifests.get(sha)
    if cached is not None:
        return cached
    ref, manifest, _raw = _load_manifest(pointer_path)
    sha = str(ref["sha256"]).lower()
    try:
        pd._bound(manifest["release"], "release")
        release_ok = True
    except Exception:  # noqa: BLE001 -- recorded as unverified, exactly as the consumer would
        release_ok = False
    pred = _predecessor_ref(manifest)
    parsed = {"sha256": sha, "created": pd._ts(manifest.get("createdAtUtc")),
              "predecessor": str(pred["sha256"]).lower() if pred else None, "release_ok": release_ok,
              "entries": {str(e.get("forecastId")): e for e in manifest.get("packages", [])}}
    with _lock:
        # Content addressed and superseded every ~15 minutes: keep the newest two.
        for old in [k for k in _manifests if k != sha][: max(0, len(_manifests) - 1)]:
            _manifests.pop(old, None)
            for key in [k for k in _entries if k[0] == old]:
                _entries.pop(key, None)
        _manifests[sha] = parsed
    return parsed


def _entry_verified(manifest: dict[str, Any], entry: dict[str, Any], known: CfbResearchForecast,
                    now: datetime) -> dict[str, Any]:
    key = (manifest["sha256"], known.forecast_id)
    with _lock:
        cached = _entries.get(key)
    if cached is None:
        report = _light_verify(entry, known, now=now)
        cached = {"verified": bool(report.get("verified")) and manifest["release_ok"],
                  "problems": list(report.get("problems") or [])
                  + ([] if manifest["release_ok"] else ["MANIFEST_RELEASE_UNVERIFIED"])}
        with _lock:
            _entries[key] = cached
    return cached


def _renewal(recorded: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """A recorded lease that lapsed under one producer cadence ago: a renewal is due, not a failure."""

    until = recorded.get("lease_valid_until")
    if recorded.get("state") != EXPIRED_LEASE or not until:
        return None
    lapsed = now - datetime.fromisoformat(str(until).replace("Z", "+00:00"))
    if timedelta(0) <= lapsed < PRODUCER_CADENCE:
        return {"state": "RENEWAL_DUE", "lapsed_minutes": _minutes(lapsed),
                "cadence_minutes": _minutes(PRODUCER_CADENCE)}
    return None


def _label(reading: dict[str, Any]) -> str:
    state = str(reading.get("state") or "")
    renewal = reading.get("renewal") or {}
    if reading.get("basis") == POINTER and state == CURRENT:
        return "current (renews every 15 min; verified renewal at the producer pointer, import pending)"
    if renewal.get("state") == "RENEWAL_DUE":
        return (f"renewing (lapsed {renewal['lapsed_minutes']:g} min ago; the producer renews every "
                f"{renewal['cadence_minutes']:g} min)")
    return state.replace("_", " ").lower()


def _keep(recorded: dict[str, Any], now: datetime, why: str) -> dict[str, Any]:
    out = {**recorded, "basis": RECORDED, "pointer": why}
    renewal = _renewal(recorded, now)
    if renewal:
        out["renewal"] = renewal
    out["label"] = _label(out)
    return out


def project(session: Session, settings: Any, forecast: CfbResearchForecast, *, recorded: dict[str, Any],
            now: datetime) -> dict[str, Any]:
    """The lease the checkpoint would read for ``forecast`` if it ran at ``now``. Never writes."""

    moment = _aware(now)
    if recorded.get("state") not in PROJECTABLE:
        return {**recorded, "basis": RECORDED, "label": _label(recorded)}
    kickoff = _aware(getattr(forecast, "captured_kickoff_utc", None))
    if kickoff is not None and moment >= kickoff:
        return _keep(recorded, moment, "GAME_STARTED")
    if getattr(forecast, "origin", None) != "PRODUCTION_DISCOVERY":
        return _keep(recorded, moment, "NOT_A_PRODUCTION_DISCOVERY_FORECAST")
    if not getattr(settings, "cfb_production_consumer_enabled", False):
        return _keep(recorded, moment, "CONSUMER_OFF")
    pointer_path = pathlib.Path(str(getattr(settings, "cfb_discovery_pointer_path", "") or ""))
    if not str(pointer_path) or not pointer_path.is_file():
        return _keep(recorded, moment, "POINTER_MISSING")
    try:
        manifest = _pointer_manifest(pointer_path)
    except Exception as error:  # noqa: BLE001 -- display only: an unreadable pointer changes nothing
        return _keep(recorded, moment, f"POINTER_UNVERIFIED:{type(error).__name__}")
    if manifest["sha256"] == str(recorded.get("manifest_sha256") or "").lower():
        return _keep(recorded, moment, "NO_NEWER_MANIFEST")
    if session.scalar(select(CfbDiscoveryManifest.id).where(
            CfbDiscoveryManifest.manifest_sha256 == manifest["sha256"]).limit(1)) is not None:
        return _keep(recorded, moment, "NO_NEWER_MANIFEST")
    recorded_created = recorded.get("manifest_created_utc")
    if manifest["created"] is None or (recorded_created and manifest["created"] <= datetime.fromisoformat(
            str(recorded_created).replace("Z", "+00:00"))):
        return _keep(recorded, moment, "POINTER_NOT_NEWER")
    if not manifest["predecessor"] or session.scalar(select(CfbDiscoveryManifest.id).where(
            CfbDiscoveryManifest.manifest_sha256 == manifest["predecessor"]).limit(1)) is None:
        return _keep(recorded, moment, "POINTER_LINEAGE_NOT_YET_RECORDED")
    if not _handoff_active(settings):
        return _keep(recorded, moment, "HANDOFF_NOT_ACTIVE")
    entry = manifest["entries"].get(forecast.forecast_id)
    if entry is None:
        return _keep(recorded, moment, "NOT_LISTED_AT_POINTER")
    check = _entry_verified(manifest, entry, forecast, moment)
    until = pd._ts(entry.get("eligibilityValidUntilUtc"))
    if not check["verified"]:
        state = "REJECTED"
    elif not entry.get("currentlyEligible"):
        state = HISTORICAL
    else:
        state = CURRENT if until is not None and moment < until else EXPIRED_LEASE
    detail = {
        CURRENT: ("The producer's newer manifest, verified from bytes at its pointer and not yet imported, lists "
                  "this forecast as currently eligible; the checkpoint imports it before deciding."),
        EXPIRED_LEASE: "The producer's newer manifest also carries an expired lease for this forecast.",
        HISTORICAL: "The producer's newer manifest lists this forecast as historical, not currently eligible.",
        "REJECTED": "This forecast's entry in the producer's newer manifest does not verify.",
    }[state]
    reading = {"forecast_id": forecast.forecast_id, "evaluated_at": moment.isoformat(), "state": state,
               "eligible": state == CURRENT, "detail": detail, "basis": POINTER,
               "manifest_sha256": manifest["sha256"], "manifest_created_utc": _iso(manifest["created"]),
               "lease_valid_until": _iso(until), "producer_status": entry.get("status"),
               "problems": check["problems"] or None,
               "recorded": {k: recorded.get(k) for k in ("state", "manifest_sha256", "manifest_created_utc",
                                                         "lease_valid_until")}}
    renewal = _renewal(recorded, moment)
    if renewal:
        reading["renewal"] = {**renewal, "state": "RENEWED_AT_POINTER" if state == CURRENT else renewal["state"]}
    reading["label"] = _label(reading)
    return reading


def projected_eligibility(session: Session, settings: Any, forecast: CfbResearchForecast, *,
                          now: datetime | None = None) -> dict[str, Any]:
    """``present_eligibility`` at ``now``, then ``project``: the reading the checkpoint would make now."""

    moment = _aware(now) or datetime.now(UTC)
    return project(session, settings, forecast, recorded=present_eligibility(session, forecast, now=moment),
                   now=moment)


def clear_cache() -> None:
    with _lock:
        _manifests.clear()
        _entries.clear()
        _verdict.clear()


__all__ = ["POINTER", "PRODUCER_CADENCE", "RECORDED", "clear_cache", "project", "projected_eligibility"]
