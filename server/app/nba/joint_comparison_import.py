"""Import the NBA Gen-1 joint comparison, byte-verified and idempotently.

WHAT IT ADDS
------------
The first NBA forecast that actually has a joint distribution. The existing
point/Elo forecast declares `drawCount = 0` and every distribution unavailable;
these two arms carry ten thousand retained draws each, with PMFs, intervals and
illustrative totals.

WHAT IT MUST NOT TOUCH
----------------------
That point/Elo forecast. The arms SUPPLEMENT it -- they carry its id and its
digest and say so -- and the handoff is explicit that importing them changes
nothing about it. So this writes only to its own table and never updates
`nba_research_forecasts`.

AND WHAT IT MUST NOT CREATE
---------------------------
Any wagering authority. Every arm declares `researchOnly` with funded, staking,
promotion, scheduler and wagering authority all false, and a package that said
otherwise would be refused rather than narrowed. The illustrative total at line
226 is labelled `ILLUSTRATIVE_NOT_RECORDED_MARKET` by the producer and that
classification travels with the row: it is not a price AISP was offered.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NbaJointComparisonArm

SCHEMA = "NBA_GEN1_JOINT_RESEARCH_FORECAST_V1"

#: Authority flags that must be false in every arm. A research package that
#: granted any of these would be refused, not read selectively.
MUST_BE_FALSE = (
    "funded", "productionAuthority", "promotion", "schedulerAuthority",
    "stakingAuthority", "wageringAuthority",
)


class JointComparisonImportError(RuntimeError):
    """The package is missing, corrupted, unbound, or claims authority."""


def verify_package(root: pathlib.Path) -> dict[str, Any]:
    """Every declared file, hashed before any of it is read as data."""

    index_path = root / "index.json"
    if not index_path.exists():
        raise JointComparisonImportError(f"No index.json at {root}")
    index = json.loads(index_path.read_text(encoding="utf-8"))

    verified: dict[str, dict[str, Any]] = {}
    for entry in index.get("files", []):
        rel = entry["relativePath"]
        path = root / rel
        if not path.exists():
            raise JointComparisonImportError(f"Declared file is missing: {rel}")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != str(entry["sha256"]).lower():
            raise JointComparisonImportError(f"{rel} does not match its digest")
        if len(raw) != int(entry["bytes"]):
            raise JointComparisonImportError(f"{rel} does not match its byte count")
        verified[rel] = {"sha256": digest, "bytes": len(raw)}

    authority = index.get("authority") or {}
    if authority.get("researchOnly") is not True:
        raise JointComparisonImportError("Package does not declare researchOnly")
    for flag in MUST_BE_FALSE:
        if authority.get(flag) is not False:
            raise JointComparisonImportError(
                f"Package does not hold {flag} false; refused rather than narrowed")

    return {"index": index, "files": verified, "address": root.name}


def import_package(
    session: Session, root: pathlib.Path, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Register both arms. A second run returns the same rows and writes nothing."""

    moment = now or datetime.now(UTC)
    verified = verify_package(root)
    index = verified["index"]
    game_id = index["aispGameId"]

    imported: list[str] = []
    already: list[str] = []
    for arm_entry in index["arms"]:
        rel = arm_entry["forecast"]["relativePath"]
        document = json.loads((root / rel).read_text(encoding="utf-8"))
        if document.get("schema") != SCHEMA:
            raise JointComparisonImportError(
                f"{rel} declares schema {document.get('schema')}, not {SCHEMA}")

        forecast_id = document["forecastId"]
        existing = session.scalar(select(NbaJointComparisonArm).where(
            NbaJointComparisonArm.forecast_id == forecast_id))
        if existing is not None:
            already.append(forecast_id)
            continue

        joint = document["jointSimulation"]
        winners = joint.get("winnerProbabilities") or {}
        expected = joint.get("expectedScores") or {}
        session.add(NbaJointComparisonArm(
            package_sha256=verified["address"],
            forecast_id=forecast_id,
            forecast_sha256=arm_entry["forecast"]["sha256"],
            arm=document["arm"],
            label=document["label"],
            configuration_identity=document["configurationIdentity"],
            schema_version=document["schema"],
            canonical_game_id=game_id,
            supplements_forecast_id=document["supplementsForecastId"],
            supplements_forecast_sha256=document["supplementsForecastSha256"],
            draw_count=int(joint.get("drawCount") or 0),
            # The keys are team-suffixed in the package (awayBOS/homeDET), so
            # they are resolved by prefix rather than by a hardcoded matchup.
            away_win_probability=_by_prefix(winners, "away"),
            home_win_probability=_by_prefix(winners, "home"),
            tie_probability=winners.get("tie"),
            expected_away=_by_prefix(expected, "away"),
            expected_home=_by_prefix(expected, "home"),
            expected_total=expected.get("total"),
            joint_simulation=joint,
            ridge_point_expectations=document.get("ridgePointExpectations"),
            separate_elo_probabilities=document.get("separateEloProbabilities"),
            limitations=document.get("limitations"),
            authority=document["authority"],
            population=arm_entry.get("population"),
            source_cutoff_utc=_iso(document.get("sourceCutoffUtc")),
            created_at_utc=_iso(document.get("createdAtUtc")),
            published_at_utc=_iso(document.get("publishedAtUtc")),
            imported_at=moment,
            research_only=True,
        ))
        imported.append(forecast_id)
    session.flush()

    return {
        "package_sha256": verified["address"],
        "files_verified": len(verified["files"]),
        "arms_declared": len(index["arms"]),
        "imported": imported,
        "already_present": already,
        "imported_count": len(imported),
        "already_present_count": len(already),
        "rejected_count": 0,
        "point_elo_forecast_untouched": index.get("pointEloForecast", {}).get("forecastId"),
        "authority": index.get("authority"),
    }


def _by_prefix(mapping: dict[str, Any], prefix: str) -> float | None:
    """The value whose key starts with `prefix` and is not an MCSE."""

    for key, value in mapping.items():
        low = key.lower()
        if low.startswith(prefix) and "mcse" not in low and "margin" not in low:
            return value
    return None


def _iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = ["JointComparisonImportError", "MUST_BE_FALSE", "SCHEMA",
           "import_package", "verify_package"]
