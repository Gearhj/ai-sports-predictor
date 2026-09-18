"""STAGED, NOT ACTIVE: independent verification of the CFB Gen1 slate publisher.

WHAT THIS IS
------------
The consumer side of ``CFB_GEN1_SLATE_DISCOVERY_V1``. It reads the producer's
discovery pointer, the content-addressed manifest it names, and every package
and publication receipt the manifest binds, and verifies each one from bytes:
addresses, member digests, the 10,000-draw population, clocks, orientation and
the eligibility lease. It returns a report. It writes nothing -- no import, no
game admission, no forecast row, no decision -- and it never touches a
provider.

WHY STAGED
----------
Nothing here grants production authority. ``production_status`` is
``NOT_RECEIVED`` until an operator pins the staged handoff (path and SHA-256),
and even a verified handoff that declares ``consumerActivated: false`` stays
``STAGED_AWAITING_ACTIVATION``: no production label, no Daily Decisions entry,
no House Divided pipeline. The research/production boundary holds until the
owner activates it.

PUBLICATION CLOCK
-----------------
A package's publication time is the discovery-bound receipt's
``committedAtUtc`` -- first durable availability -- never the prepared index's
null ``publishedAtUtc`` and never the package-local receipt the research
importer reads.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.cfb.research_import import CfbImportError, read_pmfs, verify_package

POINTER_SCHEMA = "CFB_GEN1_SLATE_DISCOVERY_POINTER_V1"
MANIFEST_SCHEMA = "CFB_GEN1_SLATE_DISCOVERY_V1"
HANDOFF_SCHEMA = "CFB_GEN1_STAGED_PRODUCER_HANDOFF_INDEX_V1"
CONFIGURATION = "CFB_GEN1_RIDGE_A100_PAIRED_RESIDUAL_V1"
DRAW_COUNT = 10_000
#: The producer closes publication at T-30m (its contract); AISP's pick lock is T-15m.
PRODUCER_CLOSE = timedelta(minutes=30)

NOT_RECEIVED = "NOT_RECEIVED"
REJECTED = "REJECTED"
STAGED = "STAGED_AWAITING_ACTIVATION"
ACTIVE = "ACTIVE"


class DiscoveryRefused(Exception):
    """The pointer or manifest itself cannot be trusted; nothing below it is read."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _bound(ref: dict[str, Any], label: str) -> bytes:
    """Read a {path, sha256, bytes} reference and prove it."""

    path = pathlib.Path(ref["path"])
    if not path.exists():
        raise CfbImportError(f"{label} is missing: {path}")
    raw = path.read_bytes()
    if _sha(raw) != str(ref["sha256"]).lower():
        raise CfbImportError(f"{label} does not match its declared SHA-256")
    if len(raw) != int(ref["bytes"]):
        raise CfbImportError(f"{label} is {len(raw)} bytes, declared {ref['bytes']}")
    return raw


# ---------------------------------------------------------------------------
# The staged handoff pin (mirrors the NFL/NBA production handoff pattern)
# ---------------------------------------------------------------------------
@dataclass
class HandoffVerdict:
    status: str
    reason: str
    release_sha256: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "release_sha256": self.release_sha256,
                "production_label_allowed": self.status == ACTIVE, **self.detail}


def verify_handoff(index_path: pathlib.Path, pin: str) -> HandoffVerdict:
    if not index_path.exists():
        return HandoffVerdict(REJECTED, "HANDOFF_INDEX_MISSING")
    raw = index_path.read_bytes()
    digest = _sha(raw)
    if digest != pin.lower() or digest != index_path.parent.name.lower():
        return HandoffVerdict(REJECTED, "HANDOFF_PIN_MISMATCH")
    index = json.loads(raw)
    if index.get("schema") != HANDOFF_SCHEMA:
        return HandoffVerdict(REJECTED, "HANDOFF_SCHEMA_UNKNOWN")
    for member in index.get("members", []):
        path = index_path.parent / member["relativePath"]
        if not path.exists():
            return HandoffVerdict(REJECTED, f"HANDOFF_MEMBER_MISSING:{member['relativePath']}")
        body = path.read_bytes()
        if _sha(body) != member["sha256"] or len(body) != int(member["bytes"]):
            return HandoffVerdict(REJECTED, f"HANDOFF_MEMBER_TAMPERED:{member['relativePath']}")
    release = index.get("release") or {}
    try:
        _bound(release, "release")
    except CfbImportError as exc:
        return HandoffVerdict(REJECTED, f"RELEASE_UNVERIFIED:{exc}")
    if index.get("consumerActivated") is not True:
        return HandoffVerdict(STAGED, "PRODUCER_DECLARES_CONSUMER_NOT_ACTIVATED", release.get("sha256"))
    return HandoffVerdict(ACTIVE, "HANDOFF_VERIFIED_AND_ACTIVATED", release.get("sha256"))


def production_status(settings: Any) -> HandoffVerdict:
    """NOT_RECEIVED unless both the path and the SHA-256 pin are configured."""

    path = str(getattr(settings, "cfb_production_handoff_path", "") or "").strip()
    pin = str(getattr(settings, "cfb_production_handoff_sha256", "") or "").strip()
    if not path or not pin:
        return HandoffVerdict(NOT_RECEIVED, "NO_PINNED_CFB_PRODUCTION_HANDOFF")
    verdict = verify_handoff(pathlib.Path(path), pin)
    if verdict.status == ACTIVE and not getattr(settings, "cfb_production_consumer_enabled", False):
        return HandoffVerdict(STAGED, "CONSUMER_SWITCH_OFF", verdict.release_sha256)
    return verdict


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _population(root: pathlib.Path, baseline: dict[str, Any]) -> list[str]:
    """Recompute what the forecast claims from its own 10,000 paired draws."""

    import polars as pl

    problems = []
    draws = pl.read_parquet(root / "baseline-joint-draws.parquet")
    home, away = draws["home_score"], draws["away_score"]
    if draws.height != DRAW_COUNT:
        problems.append(f"DRAW_ROWS_{draws.height}")
    if "total" in draws.columns and not bool(((home + away) == draws["total"]).all()):
        problems.append("TOTAL_NOT_SUM")
    if "home_margin" in draws.columns and not bool(((home - away) == draws["home_margin"]).all()):
        problems.append("MARGIN_NOT_DIFFERENCE")
    if int((home == away).sum()):
        problems.append("TIED_DRAWS_PRESENT")
    if int(((home < 0) | (away < 0)).sum()):
        problems.append("NEGATIVE_SCORES")
    joint = baseline.get("jointHomeWinProbability")
    if joint is None or round(float((home > away).mean()), 4) != round(float(joint), 4):
        problems.append("JOINT_HOME_WIN_NOT_REPRODUCED")
    return problems


def verify_entry(entry: dict[str, Any], *, now: datetime, orientation: dict[str, tuple[int, int]]) -> dict[str, Any]:
    """One discovery package entry, verified from bytes. Never raises."""

    report: dict[str, Any] = {"game_id": str(entry.get("gameId")), "forecast_id": entry.get("forecastId"),
                              "package": str(entry.get("packageAddress", ""))[:16], "problems": []}
    problems: list[str] = report["problems"]
    try:
        address = str(entry["packageAddress"]).lower()
        root = pathlib.Path(entry["packageDirectory"])
        if str(entry["index"]["sha256"]).lower() != address or root.name.lower() != address:
            problems.append("ADDRESS_DISAGREES_WITH_INDEX_OR_DIRECTORY")
        _bound(entry["index"], "package index")
        verified = verify_package(root)                        # schema, research flags, members
        index = verified["index"]
        forecast = json.loads((root / "forecast.json").read_text(encoding="utf-8"))
        baseline = forecast.get("baseline", {})
        game = forecast.get("game", {})
        if forecast.get("forecastId") != entry.get("forecastId") or index.get("forecastId") != entry.get("forecastId"):
            problems.append("FORECAST_ID_MISMATCH")
        if str(game.get("game_id")) != str(entry.get("gameId")) or str(index.get("gameId")) != str(entry.get("gameId")):
            problems.append("GAME_ID_MISMATCH")
        if index.get("configurationIdentity", CONFIGURATION) != CONFIGURATION:
            problems.append("CONFIGURATION_MISMATCH")
        if int(index.get("drawCount", baseline.get("drawCount", 0))) != DRAW_COUNT or int(baseline.get("drawCount", 0)) != DRAW_COUNT:
            problems.append("DRAW_COUNT_MISMATCH")
        read_pmfs(root, expected_draws=DRAW_COUNT)            # all four PMFs carry full mass
        problems.extend(_population(root, baseline))

        # Clocks: cutoff <= created < committed-publication < kickoff.
        cutoff, created = _ts(forecast.get("sourceCutoff")), _ts(forecast.get("createdAt"))
        kickoff = _ts(game.get("scheduled_at"))
        receipt_raw = _bound(entry["publicationReceipt"], "publication receipt")
        receipt = json.loads(receipt_raw)
        committed = _ts(receipt.get("committedAtUtc"))
        for key in ("forecastId", "gameId", "packageAddress", "revisionKey", "committedAtUtc"):
            if str(receipt.get(key)) != str(entry.get(key)):
                problems.append(f"RECEIPT_{key.upper()}_MISMATCH")
        if not (cutoff and created and cutoff <= created):
            problems.append("CREATED_BEFORE_SOURCE_CUTOFF")
        if not (kickoff and created and created < kickoff):
            problems.append("CREATED_AT_OR_AFTER_KICKOFF")
        if not (committed and created and committed >= created):
            problems.append("PUBLICATION_BEFORE_CREATION")
        if not (committed and kickoff and committed <= kickoff - PRODUCER_CLOSE):
            problems.append("PUBLISHED_AFTER_PRODUCER_CLOSE")
        report.update({"kickoff": kickoff.isoformat() if kickoff else None,
                       "published_at": committed.isoformat() if committed else None,
                       "matchup": f"{game.get('away_team')} @ {game.get('home_team')}"})

        # Orientation: exact CFBD school ids, never names.
        home_id, away_id = game.get("home_id"), game.get("away_id")
        if home_id is None or away_id is None or home_id == away_id:
            problems.append("ORIENTATION_IDS_INVALID")
        expected = orientation.get(str(entry.get("gameId")))
        if expected is None:
            report["orientation"] = "NO_INDEPENDENT_SCHEDULE_ROW"
            problems.append("ORIENTATION_UNCONFIRMED")
        elif (int(home_id), int(away_id)) != expected:
            report["orientation"] = "REVERSED_OR_DIFFERENT"
            problems.append("ORIENTATION_MISMATCH")
        else:
            report["orientation"] = "CONFIRMED"

        # Lease: current only while the producer says so and the lease is unexpired.
        valid_until = _ts(entry.get("eligibilityValidUntilUtc"))
        current = bool(entry.get("currentlyEligible")) and bool(valid_until and now < valid_until)
        report["lease"] = "CURRENT" if current else ("EXPIRED_LEASE" if entry.get("currentlyEligible") else "HISTORICAL")
        report["lease_valid_until"] = valid_until.isoformat() if valid_until else None
    except (CfbImportError, KeyError, ValueError, OSError) as exc:
        problems.append(f"UNVERIFIABLE:{type(exc).__name__}:{exc}"[:200])
    report["verified"] = not problems
    return report


def _orientation_from_input(manifest: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """Home/away CFBD ids from the manifest's own hash-bound schedule object.

    This is a CONSISTENCY check against the producer's own admitted input,
    not independent evidence; callers with an independent table (AISP's CFBD
    mappings, a separately captured raw CFBD schedule) pass it instead.
    """

    import polars as pl

    document = json.loads(_bound(manifest["inputVersion"], "input version"))
    schedule = document.get("schedule")
    if not isinstance(schedule, dict) or "path" not in schedule:
        return {}
    _bound(schedule, "input schedule")
    frame = pl.read_parquet(schedule["path"], columns=["game_id", "home_id", "away_id"])
    return {str(r[0]): (int(r[1]), int(r[2])) for r in frame.iter_rows() if r[1] is not None and r[2] is not None}


def raw_cfbd_orientation(path: pathlib.Path) -> dict[str, tuple[int, int]]:
    """Home/away ids from a raw CFBD /games capture (native homeId/awayId fields)."""

    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(r["id"]): (int(r["homeId"]), int(r["awayId"])) for r in rows
            if r.get("homeId") is not None and r.get("awayId") is not None}


def verify_discovery(pointer_path: pathlib.Path, *, now: datetime | None = None,
                     release_pin: str | None = None,
                     orientation: dict[str, tuple[int, int]] | None = None,
                     orientation_source: str | None = None) -> dict[str, Any]:
    """Pointer -> manifest -> every package. Refuses the whole slate on a bad pointer or manifest."""

    moment = now or datetime.now(UTC)
    pointer = json.loads(pointer_path.read_bytes())
    if pointer.get("schemaVersion") != POINTER_SCHEMA:
        raise DiscoveryRefused(f"pointer schema {pointer.get('schemaVersion')!r}")
    ref = pointer["manifest"]
    try:
        raw = _bound(ref, "discovery manifest")
    except CfbImportError as exc:
        raise DiscoveryRefused(str(exc)) from exc
    if pathlib.Path(ref["path"]).parent.name.lower() != str(ref["sha256"]).lower():
        raise DiscoveryRefused("manifest is not stored at its content address")
    manifest = json.loads(raw)
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA:
        raise DiscoveryRefused(f"manifest schema {manifest.get('schemaVersion')!r}")
    release_sha = str((manifest.get("release") or {}).get("sha256", "")).lower()
    if release_pin and release_sha != release_pin.lower():
        raise DiscoveryRefused("manifest names a different producer release than the pinned handoff")
    try:
        _bound(manifest["release"], "release")
    except CfbImportError as exc:
        raise DiscoveryRefused(str(exc)) from exc
    table = orientation if orientation is not None else _orientation_from_input(manifest)
    source = orientation_source or ("CALLER_SUPPLIED" if orientation is not None else "PRODUCER_INPUT_CONSISTENCY_ONLY")
    packages = [verify_entry(entry, now=moment, orientation=table) for entry in manifest.get("packages", [])]
    dispositions: dict[str, int] = {}
    for game in manifest.get("games", []):
        dispositions[game.get("disposition", "?")] = dispositions.get(game.get("disposition", "?"), 0) + 1
    return {
        "manifest_sha256": str(ref["sha256"]).lower(), "created_at": manifest.get("createdAtUtc"),
        "release_sha256": release_sha, "predecessor": manifest.get("predecessor"),
        "orientation_source": source,
        "packages": packages, "dispositions": dispositions,
        "verified": sum(1 for p in packages if p["verified"]),
        "current": sum(1 for p in packages if p["verified"] and p.get("lease") == "CURRENT"),
        "rejected": [p for p in packages if not p["verified"]],
        "checked_at": moment.isoformat(),
    }


__all__ = ["ACTIVE", "NOT_RECEIVED", "REJECTED", "STAGED", "DiscoveryRefused", "production_status",
           "raw_cfbd_orientation", "verify_discovery", "verify_entry", "verify_handoff"]
