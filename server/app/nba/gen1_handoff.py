"""The pinned NBA Gen1 model handoff: verified from bytes, fail-closed.

STATUS TODAY
------------
Nothing is pinned in the deployed configuration, so :func:`production_status`
reports ``NOT_RECEIVED`` and no NBA package is imported. Pinning is an owner act
(``NBA_PRODUCTION_HANDOFF_PATH`` / ``NBA_PRODUCTION_HANDOFF_SHA256``), and even a
verified pin stays ``STAGED`` until ``NBA_PRODUCTION_CONSUMER_ENABLED`` is set.

WHAT THE PIN COVERS
-------------------
The handoff is a directory. The pin is the SHA-256 of its ``index.json``; the
index carries the SHA-256 and byte count of every member, and the index must
sit in a directory named by its own digest. A member one byte different is
refused before anything is parsed.

WHAT IS CHECKED IN THE MANIFEST
-------------------------------
The configuration identities (point/Elo and the joint lattice), 10,000 draws,
the close-score challenger disabled, every model artifact bound by a 64-hex
digest, a parseable ``prospectiveEffectiveAtUtc`` and NO wagering, decision or
position authority. ``producerActivated`` is reported, not required: the
handoff is immutable and was written before any installation, so producer
activation is evidenced by a live slate discovery lease, not by this file.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.nba import gen1_common as common

NOT_RECEIVED = "NOT_RECEIVED"
REJECTED = "REJECTED"
STAGED = "STAGED_AWAITING_ACTIVATION"
ACTIVE = "ACTIVE"

#: Codex's selected handoff at the time of writing (2026-09-18T01:16:08Z). Named
#: here for the evidence document and tests only; the deployed pin is config.
SELECTED_HANDOFF_SHA256 = "7113b71ff5819c89103ad19d5f9afb22c51dbc92980be12b56ce8826cb34ccaf"


@dataclass(frozen=True)
class HandoffVerdict:
    status: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def production_label_allowed(self) -> bool:
        return self.status == ACTIVE

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason,
                "production_label_allowed": self.production_label_allowed, "detail": self.detail,
                "channels": common.CHANNEL_ROLES,
                "market_channels": {k: list(v) for k, v in common.MARKET_CHANNELS.items()},
                "retained_limitations": list(common.RETAINED_LIMITATIONS)}


class HandoffRefused(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


def verify_handoff(index_path: pathlib.Path, pin: str) -> dict[str, Any]:
    """Index -> pin, every member -> index, manifest -> identity. Raises HandoffRefused."""

    pin = pin.strip().lower()
    if not index_path.is_file():
        raise HandoffRefused("INDEX_MISSING", f"no handoff index at {index_path}")
    raw = index_path.read_bytes()
    digest = common.sha256_bytes(raw)
    if digest != pin:
        raise HandoffRefused("INDEX_SHA256_MISMATCH", f"index hashes to {digest}, pinned {pin}")
    if index_path.parent.name.lower() != digest:
        raise HandoffRefused("NOT_CONTENT_ADDRESSED", "the index is not stored under its own digest")
    index = json.loads(raw)
    if index.get("schemaVersion") != common.HANDOFF_INDEX_SCHEMA:
        raise HandoffRefused("INDEX_SCHEMA_UNKNOWN", f"{index.get('schemaVersion')!r}")
    members = index.get("members")
    if not isinstance(members, list) or not members:
        raise HandoffRefused("INDEX_EMPTY", "the index lists no members")
    verified: dict[str, str] = {}
    for member in members:
        relative = str(member.get("relativePath") or "")
        rel = pathlib.PurePosixPath(relative)
        if not relative or rel.is_absolute() or ".." in rel.parts:
            raise HandoffRefused("MEMBER_PATH_UNSAFE", relative or "(empty)")
        try:
            common.bound({"path": index_path.parent / relative, "sha256": member.get("sha256"),
                          "bytes": member.get("bytes")}, relative)
        except common.BindingRefused as exc:
            raise HandoffRefused("MEMBER_MISMATCH", str(exc)) from exc
        verified[relative] = str(member.get("sha256"))
    if "manifest.json" not in verified:
        raise HandoffRefused("MANIFEST_ABSENT", "the index carries no manifest.json")
    manifest = json.loads((index_path.parent / "manifest.json").read_bytes())
    return {"index": index, "manifest": manifest, "members": verified, "handoff_sha256": digest}


def identity(manifest: dict[str, Any]) -> dict[str, Any]:
    distribution = manifest.get("distribution") or {}
    authority = manifest.get("authority") or {}
    return {
        "schema": manifest.get("schemaVersion"),
        "configuration_identity": manifest.get("configurationIdentity"),
        "distribution_configuration": distribution.get("configurationIdentity"),
        "draw_count": distribution.get("drawCount"),
        "master_seed": distribution.get("masterSeed"),
        "close_score_challenger_enabled": distribution.get("closeScoreChallengerEnabled"),
        "residual_population_sha256": (distribution.get("residualPopulation") or {}).get("sha256"),
        "point_model_sha256": ((manifest.get("pointModel") or {}).get("artifact") or {}).get("sha256"),
        "elo_model_sha256": ((manifest.get("winnerModel") or {}).get("artifact") or {}).get("sha256"),
        "prospective_effective_at_utc": manifest.get("prospectiveEffectiveAtUtc"),
        "producer_status": manifest.get("status"),
        "training_games": (manifest.get("training") or {}).get("trainingGames"),
        "training_last_date": (manifest.get("training") or {}).get("lastDate"),
        "monitoring_review_at_completed_games": (manifest.get("monitoring") or {}).get("reviewAtCompletedGames"),
        "authority": {key: bool(authority.get(key)) for key in (
            "aispActivated", "producerActivated", "producerReady", "decisionAuthority",
            "positionAuthority", "wageringAuthority")},
        "disclosures": list(manifest.get("disclosures") or ()),
    }


def check_identity(found: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if found["schema"] != common.HANDOFF_MANIFEST_SCHEMA:
        problems.append(f"MANIFEST_SCHEMA_UNKNOWN:{found['schema']!r}")
    if found["configuration_identity"] != common.CONFIGURATION:
        problems.append(f"CONFIGURATION_MISMATCH:{found['configuration_identity']!r}")
    if found["distribution_configuration"] != common.DISTRIBUTION_CONFIGURATION:
        problems.append(f"DISTRIBUTION_CONFIGURATION_MISMATCH:{found['distribution_configuration']!r}")
    if found["draw_count"] != common.DRAW_COUNT:
        problems.append(f"DRAW_COUNT_MISMATCH:{found['draw_count']!r}")
    if found["close_score_challenger_enabled"] is not False:
        problems.append("CLOSE_SCORE_CHALLENGER_NOT_DISABLED")
    for key in ("residual_population_sha256", "point_model_sha256", "elo_model_sha256"):
        value = found.get(key)
        if not (isinstance(value, str) and len(value) == 64):
            problems.append(f"ARTIFACT_HASH_MISSING:{key}")
    if common.ts(found["prospective_effective_at_utc"]) is None:
        problems.append("EFFECTIVE_TIME_MISSING")
    for claim in ("decisionAuthority", "positionAuthority", "wageringAuthority"):
        if found["authority"][claim]:
            problems.append(f"HANDOFF_CLAIMS_{claim.upper()}")
    return problems


def governs(effective_at: datetime | str | None, created_at: datetime | str | None) -> bool:
    """Only packages created at or after the effective instant are production packages."""

    start, moment = common.ts(effective_at), common.ts(created_at)
    return start is not None and moment is not None and moment >= start


def production_status(settings: Any) -> HandoffVerdict:
    """Fail-closed: no pin -> NOT_RECEIVED; bad bytes -> REJECTED; switch off -> STAGED."""

    path = str(getattr(settings, "nba_production_handoff_path", "") or "").strip()
    pin = str(getattr(settings, "nba_production_handoff_sha256", "") or "").strip()
    if not path or not pin:
        return HandoffVerdict(NOT_RECEIVED, "NO_PIN",
                              {"note": "No NBA production handoff is pinned; NBA stays research-only."})
    try:
        verified = verify_handoff(pathlib.Path(path), pin)
    except (HandoffRefused, common.BindingRefused, OSError, ValueError) as exc:
        return HandoffVerdict(REJECTED, str(exc)[:300], {"handoff_sha256": pin.lower()})
    found = identity(verified["manifest"])
    problems = check_identity(found)
    detail = {"handoff_sha256": verified["handoff_sha256"], "identity": found,
              "members": verified["members"]}
    if problems:
        return HandoffVerdict(REJECTED, ";".join(problems)[:300], detail)
    if not getattr(settings, "nba_production_consumer_enabled", False):
        return HandoffVerdict(STAGED, "CONSUMER_SWITCH_OFF", detail)
    return HandoffVerdict(ACTIVE, "PINNED_VERIFIED_CONSUMER_ON", detail)


__all__ = ["ACTIVE", "HandoffRefused", "HandoffVerdict", "NOT_RECEIVED", "REJECTED", "SELECTED_HANDOFF_SHA256",
           "STAGED", "check_identity", "governs", "identity", "production_status", "verify_handoff"]
