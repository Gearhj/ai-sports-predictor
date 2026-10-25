"""Handoff-bound NHL Gen1 import: verify the producer's handoff, then import exactly what it names.

The producer's ``NHL_CURRENT_PRODUCER_HANDOFF_V1`` binds the exchange pointer, the
forecast manifest, each game's forecast object and producer evidence, the
publication receipt, the current-input and source-qualification records and the
independent review. Before anything is imported this module re-proves every one
of those references from bytes (path + size + SHA-256), checks that the
handoff's game list is EXACTLY the manifest's membership, and that every
authority flag is false. Only then is exactly that verified forecast manifest
imported (its bytes and each forecast's producer evidence are re-verified); the
mutable pointer is never consulted again.

Research only: nothing here creates a decision, quote, position or account,
and a verified handoff grants no paper authority. Read-only on the producer's
files; writes only to the caller's session, which it does not commit.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

HANDOFF_SCHEMA = "NHL_CURRENT_PRODUCER_HANDOFF_V1"
AUTHORITY_FLAGS = ("decisionAuthority", "modelPromotionAuthority", "positionAuthority", "wageringAuthority")


class HandoffRefused(ValueError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_bound(path: pathlib.Path, ref: dict[str, Any], label: str) -> bytes:
    if not path.is_file():
        raise HandoffRefused(f"{label}:MISSING:{path}")
    raw = path.read_bytes()
    if _sha(raw) != str(ref.get("sha256", "")).lower():
        raise HandoffRefused(f"{label}:SHA256_MISMATCH")
    if ref.get("bytes") is not None and len(raw) != int(ref["bytes"]):
        raise HandoffRefused(f"{label}:BYTES_MISMATCH")
    return raw


def _abs(ref: dict[str, Any], base: pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(str(ref.get("path") or ""))
    return p if p.is_absolute() else base / p


def verify_handoff(handoff_path: pathlib.Path, pin_sha256: str, exchange_root: pathlib.Path) -> dict[str, Any]:
    """Every reference in the handoff, proved from bytes. Raises HandoffRefused; returns the verified context."""

    handoff_path, exchange_root = pathlib.Path(handoff_path), pathlib.Path(exchange_root)
    raw = handoff_path.read_bytes()
    if _sha(raw) != pin_sha256.lower():
        raise HandoffRefused("HANDOFF:PIN_MISMATCH")
    h = json.loads(raw)
    if h.get("schema") != HANDOFF_SCHEMA:
        raise HandoffRefused(f"HANDOFF:SCHEMA:{h.get('schema')!r}")
    authority = h.get("authority") or {}
    if any(authority.get(flag) is not False for flag in AUTHORITY_FLAGS):
        raise HandoffRefused("HANDOFF:AUTHORITY_NOT_ALL_FALSE")
    root = handoff_path.parent

    pointer_ref = h["pointer"]
    pointer_raw = _read_bound(_abs(pointer_ref, exchange_root), pointer_ref, "POINTER")
    pointer = json.loads(pointer_raw)
    manifest_ref = h["manifest"]
    if str(pointer["forecastManifest"]["sha256"]).lower() != str(manifest_ref["sha256"]).lower():
        raise HandoffRefused("POINTER:DOES_NOT_NAME_THE_HANDOFF_MANIFEST")
    manifest_raw = _read_bound(exchange_root / pointer["forecastManifest"]["path"], manifest_ref, "MANIFEST")
    manifest = json.loads(manifest_raw)
    if manifest.get("researchStatus") != "RESEARCH_ONLY":
        raise HandoffRefused("MANIFEST:NOT_RESEARCH_ONLY")

    retained = {}
    for key in ("publicationReceipt", "currentInputRef", "sourceQualificationRef", "independentReviewRef",
                "sourceCaptureReceipt"):
        ref = h.get(key)
        if not isinstance(ref, dict):
            raise HandoffRefused(f"{key}:ABSENT")
        _read_bound(_abs(ref, root), ref, key)
        retained[key] = {"sha256": ref["sha256"], "bytes": ref.get("bytes")}

    members = {(o["canonicalGameId"], o["forecastId"], o["sha256"]) for o in manifest["objects"]}
    games, evidence = set(), {}
    for game in h.get("games") or []:
        obj = game["object"]
        games.add((obj["canonicalGameId"], obj["forecastId"], obj["sha256"]))
        ev = game["producerEvidence"]
        _read_bound(exchange_root / ev["path"], ev, f"EVIDENCE:{game['canonicalGameId']}")
        evidence[ev["sha256"]] = ev.get("bytes")
    # Membership: the handoff's new games plus the originals it declares preserved
    # must be EXACTLY the manifest. Each preserved original must appear, byte-for-
    # byte (same game, forecast id and object sha), in an earlier retained manifest
    # of the same exchange -- proved, not assumed from the count.
    counts = h.get("counts") or {}
    if games - members:
        raise HandoffRefused(f"MEMBERSHIP_MISMATCH:handoff_only={sorted(games - members)}")
    preserved = members - games
    if (len(games) != counts.get("newCurrentPublished") or len(preserved) != counts.get("originalForecastsPreserved")
            or len(members) != counts.get("currentManifestTotal") or h.get("oldObjectsBytePreserved") is not True):
        raise HandoffRefused(f"MEMBERSHIP_COUNTS_MISMATCH:new={len(games)} preserved={len(preserved)} "
                             f"total={len(members)} declared={counts}")
    earlier: dict[tuple[str, str, str], str] = {}
    for path in (exchange_root / "manifests" / "sha256").glob("*/manifest.json"):
        if path.parent.name == str(manifest_ref["sha256"]).lower():
            continue
        prior_raw = path.read_bytes()
        if _sha(prior_raw) != path.parent.name:
            continue                                   # not at its content address: not evidence
        for o in json.loads(prior_raw).get("objects") or []:
            earlier[(o["canonicalGameId"], o["forecastId"], o["sha256"])] = path.parent.name
    unproved = sorted(m for m in preserved if m not in earlier)
    if unproved:
        raise HandoffRefused(f"PRESERVED_ORIGINALS_NOT_IN_A_RETAINED_EARLIER_MANIFEST:{unproved}")
    for _gid, _fid, sha in preserved:
        _read_bound(exchange_root / "objects" / "sha256" / sha / "forecast.json", {"sha256": sha},
                    f"PRESERVED_OBJECT:{sha[:12]}")
    return {
        "handoff": {"sha256": pin_sha256.lower(), "bytes": len(raw), "schema": h["schema"], "status": h.get("status"),
                    "createdAtUtc": h.get("createdAtUtc")},
        "pointer": {"sha256": _sha(pointer_raw), "bytes": len(pointer_raw), "schema": pointer.get("schema"),
                    "resultManifest": pointer.get("resultManifest")},
        "manifest": {"sha256": _sha(manifest_raw), "bytes": len(manifest_raw), "schema": manifest.get("schema"),
                     "path": str(pointer["forecastManifest"]["path"]),
                     "manifestCreatedAtUtc": manifest.get("manifestCreatedAtUtc"),
                     "publicationCommittedAtUtc": (manifest.get("publicationReceipt") or {}).get("committedAtUtc"),
                     "objects": len(manifest["objects"]), "modelId": manifest.get("modelId"),
                     "modelVersion": manifest.get("modelVersion")},
        "membership": [{"canonicalGameId": g, "forecastId": f, "objectSha256": s,
                        "class": "NEW_CURRENT" if (g, f, s) in games else "PRESERVED_ORIGINAL",
                        **({"earlier_manifest_sha256": earlier[(g, f, s)]} if (g, f, s) in preserved else {})}
                       for g, f, s in sorted(members)],
        "producer_evidence": evidence,
        "retained_refs": retained,
        "omitted_at_publication": h.get("omittedAtPublication"),
        "price_and_decision_cutoff": h.get("priceAndDecisionCutoff"),
        "authority": {flag: authority.get(flag) for flag in AUTHORITY_FLAGS},
    }


def import_from_handoff(session: Session, *, handoff_path: pathlib.Path, pin_sha256: str,
                        exchange_root: pathlib.Path, run_id: str | None = None,
                        now: datetime | None = None) -> dict[str, Any]:
    """Verify the handoff, then import exactly the forecast manifest it verified. Does not commit.

    MAIN 1da8a50 H1: the import must not re-read the mutable ``current.json``.
    A legitimate pointer advance between verification and consumption would
    otherwise import a different manifest under a receipt that names the
    verified one. So the verified, content-addressed manifest reference --
    path, SHA-256 and size -- is handed straight to ``import_manifest``, which
    re-proves those bytes and every object/evidence check. No schedule or
    result manifest is read here.
    """

    from app.nhl.gen1_import import ExchangeRefused, ImportSummary, import_manifest

    context = verify_handoff(handoff_path, pin_sha256, exchange_root)
    started = now or datetime.now(UTC)
    verified = context["manifest"]
    ref = {"path": verified["path"], "sha256": verified["sha256"], "bytes": verified["bytes"]}
    summary = ImportSummary(run_id=run_id or uuid.uuid4().hex)
    try:
        import_manifest(session, pathlib.Path(exchange_root), ref, summary=summary, now=started,
                        admit_unbound_games=True, record_class="FORECAST")
    except (ExchangeRefused, ValueError) as exc:
        summary.problems.append(f"forecastManifest:{exc}")
    return {"schema": "AISP_NHL_HANDOFF_IMPORT_RECEIPT_V1", "verified": context,
            "import": summary.as_dict(), "import_started_utc": started.isoformat(),
            "research_only": True, "paper_authority": False}


__all__ = ["AUTHORITY_FLAGS", "HANDOFF_SCHEMA", "HandoffRefused", "import_from_handoff", "verify_handoff"]
