"""Fail-closed adapter for the Codex NBA Gen1 production handoff.

STATUS TODAY
------------
STAGED, NOT ACTIVE. Nothing is pinned: ``nba_production_handoff_path`` and
``nba_production_handoff_sha256`` are empty, so :func:`production_status`
reports ``NOT_RECEIVED`` and no production label may be displayed anywhere.
Activating is a separate, deliberate act -- see ``docs/NBA_CONSUMER_STAGING.md``.

WHAT THE PIN ACTUALLY COVERS
----------------------------
The handoff is a DIRECTORY, not a file, so the pin is the sha256 of its
``index.json`` and the index carries the sha256 and byte count of every member.
Verification is therefore two-step and total: the index must hash to the pin,
and every member must hash to what the index says with the byte count it says.
A member that is one byte different is refused before anything is parsed.

THREE CHANNELS THAT ARE NEVER BLENDED
-------------------------------------
The producer estimates three separate things, and the handoff says so in its
own disclosures. Mixing them would be the most dangerous thing this adapter
could do, so each is named, kept apart, and mapped to the markets it is allowed
to answer:

``pointModel``   Ridge A100 expected scores. A POINT EXPECTATION, never a
                 probability. Display only.
``winnerModel``  Elo. Its declared target is ``HOME_MINUS_AWAY_MARGIN`` -- a
                 training-only OLS association, not a calibrated win
                 probability. It is a diagnostic and an input, never a consumer
                 probability channel.
``distribution`` 10,000 paired HOME/AWAY full-game draws including overtime,
                 configuration ``NBA_GEN1_JOINT_FINAL_PAIRED_OOF_LATTICE_V1``.
                 The ONLY channel that yields a probability, because it is the
                 only one that carries a distribution.

So every market probability -- moneyline, spread, total -- comes from the joint
draws and from nothing else. Deriving a win probability from the Elo margin, or
a cover probability from the Ridge expectation, would be exactly the silent
substitution the handoff warns about.

WHAT THIS MODULE MAY NOT DO
---------------------------
Grant wagering authority, create a decision or a position, relabel a research
forecast, or consume a package created before ``prospectiveEffectiveAtUtc``.
The BOS-DET research artifact predates that instant and stays research.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HANDOFF_INDEX_SCHEMA = "NBA_GEN1_PRODUCTION_HANDOFF_INDEX_V2"

#: The producer's own status values. Anything else is refused.
READY_AWAITING_ACTIVATION = "READY_AWAITING_ACTIVATION"
ACTIVE = "ACTIVE"
KNOWN_STATUSES = frozenset({READY_AWAITING_ACTIVATION, ACTIVE})

NOT_RECEIVED = "NOT_RECEIVED"
REJECTED = "REJECTED"

#: The configuration AISP already admits for NBA probabilities. The handoff's
#: own `distribution.configurationIdentity` must equal this: the joint lattice
#: population is the admitted channel, and a production baseline may not quietly
#: swap the population underneath it.
EXPECTED_DISTRIBUTION_CONFIGURATION = "NBA_GEN1_JOINT_FINAL_PAIRED_OOF_LATTICE_V1"
EXPECTED_DRAW_COUNT = 10_000

#: The three channels, and the only markets each may answer.
CHANNEL_POINT = "RIDGE_POINT_EXPECTATION"
CHANNEL_WINNER = "ELO_WINNER_ASSOCIATION"
CHANNEL_DISTRIBUTION = "JOINT_PAIRED_RESIDUAL_DRAWS"

#: Market -> the one channel authorised to answer it. Read this as a closed
#: allow-list: a market absent here has no authorised channel at all.
MARKET_CHANNEL: dict[str, str] = {
    "MONEYLINE": CHANNEL_DISTRIBUTION,
    "SPREAD": CHANNEL_DISTRIBUTION,
    "TOTAL": CHANNEL_DISTRIBUTION,
}

CHANNEL_ROLES: dict[str, dict[str, Any]] = {
    CHANNEL_POINT: {
        "produces": "Expected home and away score",
        "is_probability": False,
        "markets": [],
        "display_note": "An expected score, not a probability. It cannot answer a "
                        "moneyline, a spread or a total on its own.",
    },
    CHANNEL_WINNER: {
        "produces": "Elo association on home-minus-away margin",
        "is_probability": False,
        "markets": [],
        "display_note": "A training-only OLS association whose target is a margin, "
                        "not a calibrated win probability. Diagnostic only.",
    },
    CHANNEL_DISTRIBUTION: {
        "produces": "10,000 paired HOME/AWAY full-game draws, overtime included",
        "is_probability": True,
        "markets": ["MONEYLINE", "SPREAD", "TOTAL"],
        "display_note": "The only channel carrying a distribution, so the only one "
                        "that may produce a market probability.",
    },
}

#: Limitations the producer retained. They are displayed, not summarised away.
#: The one-point-finish gap matters most where a margin sits on an integer, so
#: it travels with the spread channel rather than sitting in a footnote.
RETAINED_LIMITATIONS: tuple[str, ...] = (
    "Simulated one-point finishes were 7.78% against 4.01% observed, so margins "
    "at or near a single point are the least trustworthy part of this model.",
    "A close-score correction was tested and rejected; the challenger stays disabled.",
    "Overtime games remain materially harder to forecast.",
    "No profitability claim has been established.",
    "Ridge score and Elo winner channels are separately estimated.",
)


class NbaHandoffRejected(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class NbaHandoffVerdict:
    status: str
    accepted: bool
    handoff_sha256: str | None = None
    identity: dict[str, Any] | None = None
    reasons: tuple[str, ...] = ()

    @property
    def production_label_allowed(self) -> bool:
        """Only an ACTIVE, accepted handoff may show a production label."""

        return self.accepted and self.status == ACTIVE

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "accepted": self.accepted,
            "production_label_allowed": self.production_label_allowed,
            "handoff_sha256": self.handoff_sha256,
            "identity": self.identity,
            "reasons": list(self.reasons),
            "market_channels": dict(MARKET_CHANNEL),
            "channel_roles": CHANNEL_ROLES,
            "retained_limitations": list(RETAINED_LIMITATIONS),
        }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_package(index_path: Path, pin: str) -> dict[str, Any]:
    """The index must hash to the pin and every member to the index.

    Raises :class:`NbaHandoffRejected` rather than returning a partial result:
    a package that cannot be proven is not a package.
    """

    if not index_path.is_file():
        raise NbaHandoffRejected("INDEX_MISSING", f"no handoff index at {index_path}")
    raw = index_path.read_bytes()
    digest = _sha256(raw)
    if digest != pin:
        raise NbaHandoffRejected(
            "INDEX_SHA256_MISMATCH",
            f"index hashes to {digest}, pinned {pin}")
    try:
        index = json.loads(raw)
    except json.JSONDecodeError as error:
        raise NbaHandoffRejected("INDEX_UNREADABLE", str(error)) from error
    if str(index.get("schemaVersion")) != HANDOFF_INDEX_SCHEMA:
        raise NbaHandoffRejected(
            "INDEX_SCHEMA_UNKNOWN",
            f"index declares {index.get('schemaVersion')!r}, expected {HANDOFF_INDEX_SCHEMA}")

    members = index.get("members")
    if not isinstance(members, list) or not members:
        raise NbaHandoffRejected("INDEX_EMPTY", "the index lists no members")

    verified: dict[str, str] = {}
    for member in members:
        relative = str(member.get("relativePath") or "")
        expected = str(member.get("sha256") or "")
        size = member.get("bytes")
        if not relative or not expected:
            raise NbaHandoffRejected("MEMBER_UNNAMED", "a member has no path or hash")
        path = index_path.parent / relative
        if not path.is_file():
            raise NbaHandoffRejected("MEMBER_MISSING", f"{relative} is not present")
        body = path.read_bytes()
        if isinstance(size, int) and len(body) != size:
            raise NbaHandoffRejected(
                "MEMBER_SIZE_MISMATCH",
                f"{relative} is {len(body)} bytes, the index says {size}")
        actual = _sha256(body)
        if actual != expected:
            raise NbaHandoffRejected(
                "MEMBER_SHA256_MISMATCH", f"{relative} hashes to {actual}, index says {expected}")
        verified[relative] = actual
    if "manifest.json" not in verified:
        raise NbaHandoffRejected("MANIFEST_ABSENT", "the index does not carry a manifest")
    return {"index": index, "verified": verified,
            "manifest": json.loads((index_path.parent / "manifest.json").read_bytes())}


def _identity(manifest: dict[str, Any]) -> dict[str, Any]:
    distribution = manifest.get("distribution") or {}
    authority = manifest.get("authority") or {}
    return {
        "configuration_identity": manifest.get("configurationIdentity"),
        "distribution_configuration": distribution.get("configurationIdentity"),
        "draw_count": distribution.get("drawCount"),
        "close_score_challenger_enabled": distribution.get("closeScoreChallengerEnabled"),
        "residual_population_sha256": (distribution.get("residualPopulation") or {}).get("sha256"),
        "point_model_sha256": ((manifest.get("pointModel") or {}).get("artifact") or {}).get("sha256"),
        "winner_model_sha256": ((manifest.get("winnerModel") or {}).get("artifact") or {}).get("sha256"),
        "prospective_effective_at_utc": manifest.get("prospectiveEffectiveAtUtc"),
        "producer_status": manifest.get("status"),
        "authority": {
            "aisp_activated": bool(authority.get("aispActivated")),
            "producer_activated": bool(authority.get("producerActivated")),
            "producer_ready": bool(authority.get("producerReady")),
            "decision_authority": bool(authority.get("decisionAuthority")),
            "position_authority": bool(authority.get("positionAuthority")),
            "wagering_authority": bool(authority.get("wageringAuthority")),
        },
        "disclosures": list(manifest.get("disclosures") or ()),
        "monitoring_review_at_completed_games": (
            (manifest.get("monitoring") or {}).get("reviewAtCompletedGames")),
    }


def _check_identity(identity: dict[str, Any]) -> list[str]:
    """Everything that must hold before a probability may be served."""

    reasons: list[str] = []
    if identity["distribution_configuration"] != EXPECTED_DISTRIBUTION_CONFIGURATION:
        reasons.append(
            "DISTRIBUTION_CONFIGURATION_MISMATCH: the joint population is "
            f"{identity['distribution_configuration']!r}, admitted "
            f"{EXPECTED_DISTRIBUTION_CONFIGURATION!r}")
    if identity["draw_count"] != EXPECTED_DRAW_COUNT:
        reasons.append(
            f"DRAW_COUNT_MISMATCH: {identity['draw_count']} draws, admitted {EXPECTED_DRAW_COUNT}")
    if identity["close_score_challenger_enabled"]:
        reasons.append(
            "CLOSE_SCORE_CHALLENGER_ENABLED: the handoff requires it to stay disabled")
    if str(identity["producer_status"]) not in KNOWN_STATUSES:
        reasons.append(f"UNKNOWN_PRODUCER_STATUS: {identity['producer_status']!r}")
    for field_name in ("residual_population_sha256", "point_model_sha256", "winner_model_sha256"):
        value = identity.get(field_name)
        if not (isinstance(value, str) and len(value) == 64):
            reasons.append(f"ARTIFACT_HASH_MISSING: {field_name}")
    if not identity["prospective_effective_at_utc"]:
        reasons.append("EFFECTIVE_TIME_MISSING: no prospectiveEffectiveAtUtc")
    return reasons


def governs(identity: dict[str, Any], created_at_utc: str | datetime | None) -> bool:
    """Whether a forecast package is governed by this handoff.

    Only packages created at or after ``prospectiveEffectiveAtUtc``. Anything
    earlier -- BOS-DET among them -- stays research and is never relabelled.
    """

    effective = identity.get("prospective_effective_at_utc")
    if not effective or created_at_utc is None:
        return False
    try:
        start = datetime.fromisoformat(str(effective))
        moment = (created_at_utc if isinstance(created_at_utc, datetime)
                  else datetime.fromisoformat(str(created_at_utc)))
    except ValueError:
        return False
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment >= start


def authorised_channel(market: str) -> str | None:
    """The one channel allowed to answer this market, or None."""

    return MARKET_CHANNEL.get(str(market).upper())


def production_status(settings: Any) -> NbaHandoffVerdict:
    """Fail-closed. No pin, no package, no label."""

    path = str(getattr(settings, "nba_production_handoff_path", "") or "").strip()
    pin = str(getattr(settings, "nba_production_handoff_sha256", "") or "").strip()
    if not path or not pin:
        return NbaHandoffVerdict(
            status=NOT_RECEIVED, accepted=False,
            reasons=("No NBA production handoff is pinned. NBA stays research-only.",))
    try:
        package = verify_package(Path(path), pin)
    except NbaHandoffRejected as error:
        return NbaHandoffVerdict(status=REJECTED, accepted=False, handoff_sha256=pin,
                                 reasons=(str(error),))
    identity = _identity(package["manifest"])
    reasons = _check_identity(identity)
    if reasons:
        return NbaHandoffVerdict(status=REJECTED, accepted=False, handoff_sha256=pin,
                                 identity=identity, reasons=tuple(reasons))
    return NbaHandoffVerdict(
        status=str(identity["producer_status"]), accepted=True,
        handoff_sha256=pin, identity=identity,
        reasons=() if identity["authority"]["producer_activated"] else (
            "The producer is ready but not activated, so AISP shows a staged "
            "status rather than a production label.",))


__all__ = [
    "ACTIVE", "CHANNEL_DISTRIBUTION", "CHANNEL_POINT", "CHANNEL_WINNER",
    "CHANNEL_ROLES", "EXPECTED_DISTRIBUTION_CONFIGURATION", "EXPECTED_DRAW_COUNT",
    "HANDOFF_INDEX_SCHEMA", "KNOWN_STATUSES", "MARKET_CHANNEL", "NOT_RECEIVED",
    "NbaHandoffRejected", "NbaHandoffVerdict", "READY_AWAITING_ACTIVATION",
    "REJECTED", "RETAINED_LIMITATIONS", "authorised_channel", "governs",
    "production_status", "verify_package",
]
