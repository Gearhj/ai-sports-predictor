"""Import the College Football Gen 1 research package, byte-verified.

WHAT IS VERIFIED BEFORE ANYTHING IS READ AS DATA
------------------------------------------------
The physical index digest against the directory that addresses it, then every
declared member's digest AND byte count -- read as bytes, never as text, so no
newline translation can make a tampered file look intact on Windows.

IDEMPOTENCE, AND WHAT A SECOND RUN MUST NOT DO
----------------------------------------------
Keyed on the producer's forecast identity. A re-import of the same bytes returns
the existing row and writes nothing. A re-import of DIFFERENT bytes under the
same forecast id is refused -- not merged, not versioned, not overwritten --
because two different artifacts claiming one identity is a producer problem and
silently picking one would hide it.

WHAT IS NOT DONE HERE
---------------------
No model artifact is executed and no simulation is regenerated. The joint draws
and the PMFs were computed by the producer; this reads the retained values. The
model bindings are recorded as digests and paths so a reader can find what
produced the numbers, and are never loaded.

AND WHAT IS NOT CREATED
-----------------------
Any wagering surface. The package declares researchOnly with wagering and AISP
authority false, and a package that said otherwise would be refused rather than
narrowed. No account, bankroll, policy, price, decision, position or scheduler
follows from this import.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CfbResearchForecast

FORECAST_SCHEMA = "CFB_GEN1_PROSPECTIVE_RESEARCH_FORECAST_V1"
INDEX_SCHEMA = "CFB_GEN1_PRIVATE_RESEARCH_PACKAGE_INDEX_V1"

#: Every one of these must be false in the package. A package that granted any
#: of them is refused, not read selectively.
MUST_BE_FALSE = ("aispAuthority", "wageringAuthority")

#: The four metrics whose complete PMFs the package retains.
PMF_METRICS = ("away_score", "home_score", "total", "home_margin")


class CfbImportError(RuntimeError):
    """The package is missing, tampered, unbound, or claims authority."""


class CfbForecastConflict(CfbImportError):
    """Different bytes arrived under an existing forecast identity."""


def verify_package(root: pathlib.Path) -> dict[str, Any]:
    """Physical digests first, contract second. Bytes, never text."""

    index_path = root / "index.json"
    if not index_path.exists():
        raise CfbImportError(f"No index.json at {root}")

    index_raw = index_path.read_bytes()
    index_digest = hashlib.sha256(index_raw).hexdigest()
    if index_digest != root.name.lower():
        raise CfbImportError(
            f"index.json digest {index_digest} does not address its directory {root.name}")

    index = json.loads(index_raw.decode("utf-8"))
    if index.get("schemaVersion") != INDEX_SCHEMA:
        raise CfbImportError(
            f"Index declares {index.get('schemaVersion')}, not {INDEX_SCHEMA}")
    if index.get("researchOnly") is not True:
        raise CfbImportError("Package does not declare researchOnly")
    for flag in MUST_BE_FALSE:
        if index.get(flag) is not False:
            raise CfbImportError(
                f"Package does not hold {flag} false; refused rather than narrowed")

    members: dict[str, dict[str, Any]] = {}
    for entry in index.get("files", []):
        rel = entry["path"]
        path = root / rel
        if not path.exists():
            raise CfbImportError(f"Declared member is missing: {rel}")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != str(entry["physicalSha256"]).lower():
            raise CfbImportError(f"{rel} does not match its declared digest")
        if len(raw) != int(entry["bytes"]):
            raise CfbImportError(
                f"{rel} is {len(raw)} bytes, declared {entry['bytes']}")
        members[rel] = {"sha256": digest, "bytes": len(raw)}

    return {"index": index, "index_sha256": index_digest,
            "index_bytes": len(index_raw), "members": members, "root": root}


def _receipt(root: pathlib.Path) -> dict[str, Any] | None:
    path = root / "publication-receipt.json"
    if not path.exists():
        return None
    raw = path.read_bytes()
    return {"sha256": hashlib.sha256(raw).hexdigest(),
            "document": json.loads(raw.decode("utf-8"))}


def read_pmfs(root: pathlib.Path, *, expected_draws: int) -> dict[str, Any]:
    """The four retained PMFs, checked for complete mass before they are stored.

    A PMF that does not sum to the declared draw count is not a chart with a
    small error in it -- it is a different distribution, and every interval and
    scenario read off it would be wrong.
    """

    import polars as pl

    frame = pl.read_parquet(root / "baseline-pmfs.parquet")
    out: dict[str, Any] = {}
    for metric in PMF_METRICS:
        rows = frame.filter(pl.col("metric") == metric).sort("value")
        if rows.is_empty():
            raise CfbImportError(f"PMF for {metric} is missing")
        mass = int(rows["count"].sum())
        if mass != expected_draws:
            raise CfbImportError(
                f"PMF for {metric} sums to {mass}, not the declared {expected_draws}")
        declared = set(rows["draw_count"].unique().to_list())
        if declared != {expected_draws}:
            raise CfbImportError(
                f"PMF for {metric} declares draw counts {declared}")
        out[metric] = {
            "values": rows["value"].to_list(),
            "counts": rows["count"].to_list(),
            "draw_count": expected_draws,
            "mass": mass,
        }
    return out


def import_package(
    session: Session, root: pathlib.Path, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Register the forecast. A second run returns the same row and writes nothing."""

    moment = now or datetime.now(UTC)
    verified = verify_package(root)
    index = verified["index"]

    forecast_rel = "forecast.json"
    forecast_raw = (root / forecast_rel).read_bytes()
    document = json.loads(forecast_raw.decode("utf-8"))
    forecast_sha256 = verified["members"][forecast_rel]["sha256"]

    if document.get("schemaVersion") != FORECAST_SCHEMA:
        raise CfbImportError(
            f"forecast.json declares {document.get('schemaVersion')}, not {FORECAST_SCHEMA}")
    forecast_id = document["forecastId"]
    if forecast_id != index["forecastId"]:
        raise CfbImportError("Index and forecast disagree on the forecast identity")
    if document.get("researchOnly") is not True:
        raise CfbImportError("Forecast does not declare researchOnly")
    for flag in MUST_BE_FALSE:
        if document.get(flag) is not False:
            raise CfbImportError(f"Forecast does not hold {flag} false")

    existing = session.scalar(select(CfbResearchForecast).where(
        CfbResearchForecast.forecast_id == forecast_id))
    if existing is not None:
        if existing.forecast_sha256 != forecast_sha256:
            raise CfbForecastConflict(
                f"{forecast_id} already exists with digest {existing.forecast_sha256}; "
                f"the supplied bytes hash to {forecast_sha256}. Conflicting bytes under "
                "one identity are refused, never merged.")
        return {"imported_count": 0, "already_present_count": 1, "rejected_count": 0,
                "forecast_id": forecast_id, "row_id": str(existing.id),
                "imported_at": existing.imported_at.isoformat(),
                "package_index_sha256": verified["index_sha256"],
                "members_verified": len(verified["members"]),
                "result": "ALREADY_PRESENT"}

    game = document["game"]
    if str(game["game_id"]) != str(index["gameId"]):
        raise CfbImportError("Index and forecast disagree on the source game id")

    baseline = document["baseline"]
    draws = int(baseline["drawCount"])
    pmfs = read_pmfs(root, expected_draws=draws)

    receipt = _receipt(root)
    row = CfbResearchForecast(
        forecast_id=forecast_id,
        forecast_sha256=forecast_sha256,
        forecast_bytes=verified["members"][forecast_rel]["bytes"],
        forecast_schema=document["schemaVersion"],
        package_index_sha256=verified["index_sha256"],
        package_index_bytes=verified["index_bytes"],
        publication_receipt_sha256=(receipt or {}).get("sha256"),
        joint_draws_sha256=verified["members"].get(
            "baseline-joint-draws.parquet", {}).get("sha256"),
        pmf_sha256=verified["members"].get("baseline-pmfs.parquet", {}).get("sha256"),
        baseline_configuration=baseline["configuration"],
        source_provider="CFBD",
        # Reconciled on the producer's own identifiers. Team names are carried
        # for display and are never the thing that is matched on.
        source_game_id=str(game["game_id"]),
        home_source_team_id=str(game["home_id"]),
        away_source_team_id=str(game["away_id"]),
        home_team_name=game["home_team"],
        away_team_name=game["away_team"],
        neutral_site=bool(game.get("neutral_site")),
        conference_competition=bool(game.get("conference_competition")),
        captured_kickoff_utc=_iso(game["scheduled_at"]),
        schedule_revision_captured_utc=_iso(document.get("scheduleRevisionCapturedAt")),
        expected_away_points=baseline["expectedAwayPoints"],
        expected_home_points=baseline["expectedHomePoints"],
        expected_total=baseline["expectedTotal"],
        expected_home_margin=baseline["expectedHomeMargin"],
        ridge_home_win_probability=baseline["ridgeHomeWinProbability"],
        ridge_away_win_probability=baseline["ridgeAwayWinProbability"],
        elo_home_win_probability=baseline["eloHomeWinProbability"],
        elo_away_win_probability=baseline["eloAwayWinProbability"],
        joint_home_win_probability=baseline["jointHomeWinProbability"],
        joint_away_win_probability=baseline["jointAwayWinProbability"],
        tie_probability=baseline["tieProbability"],
        draw_count=draws,
        pmfs=pmfs,
        intervals=baseline["intervals"],
        illustrative_totals=baseline["illustrativeTotals"],
        simulation_mcse=baseline.get("simulationMcse"),
        transformation_accounting=baseline.get("transformationAccounting"),
        feature_values=document.get("featureValues"),
        limitations=document.get("limitations"),
        challenger=document.get("challenger"),
        model_bindings=document.get("modelBindings"),
        source_bindings=document.get("sourceBindings"),
        source_usage=None,
        source_cutoff_utc=_iso(document["sourceCutoff"]),
        forecast_created_utc=_iso(document["createdAt"]),
        exchange_published_utc=_iso(
            ((receipt or {}).get("document") or {}).get("publishedAtUtc")),
        imported_at=moment,
        research_only=True,
    )
    session.add(row)
    session.flush()

    return {"imported_count": 1, "already_present_count": 0, "rejected_count": 0,
            "forecast_id": forecast_id, "row_id": str(row.id),
            "imported_at": moment.isoformat(),
            "package_index_sha256": verified["index_sha256"],
            "members_verified": len(verified["members"]),
            "source_game_id": row.source_game_id,
            "result": "IMPORTED"}


def _iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = ["CfbForecastConflict", "CfbImportError", "FORECAST_SCHEMA", "INDEX_SCHEMA",
           "PMF_METRICS", "import_package", "read_pmfs", "verify_package"]
