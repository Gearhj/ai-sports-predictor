"""Verify one NBA Gen1 production forecast package from bytes, and nothing else.

Supports the two index schemas the producer has published or staged:

* ``NBA_GEN1_PRODUCTION_FORECAST_PACKAGE_INDEX_V1`` -- the one real package today
  (PHI at NYK, ESPN 401909089). Its ``publishedAtUtc`` is set in the bytes.
* ``NBA_GEN1_PRODUCTION_FORECAST_PACKAGE_INDEX_V2`` -- the slate publisher's
  format. ``publishedAtUtc`` is null BY DESIGN; the actual publication clock is
  the bound publication receipt's ``committedAtUtc``, supplied by discovery.

WHAT IS PROVED BEFORE A ROW IS WRITTEN
--------------------------------------
1. ``index.json`` hashes to the directory's name; every listed member matches its
   SHA-256 and byte count; member paths are relative and unique.
2. ``forecast.json`` matches ``forecastSha256``; ``forecastId`` agrees with the index.
3. Identity: the forecast schema, the point/Elo configuration, the joint lattice,
   10,000 draws, the close-score challenger disabled, and NO wagering, decision
   or position authority.
4. Binding: the forecast names the pinned handoff by SHA-256, and was created at
   or after the handoff's ``prospectiveEffectiveAtUtc`` (so BOS-DET and anything
   else earlier stays research and is never relabelled).
5. Chronology: source cutoff <= creation < scheduled tipoff.
6. The population: the parquet re-read, draw indexes 0..9999, one home and one
   away team code equal to the forecast's, the logical HOME/AWAY int64 digest
   equal to the declared one, and EVERY joint summary number -- winner mass,
   expected scores, the four PMFs, the twelve intervals and the illustrative
   totals -- recomputed from the draws and compared exactly.
7. Channels: Elo mass sums to one; Ridge fields are present and finite.

A failure at any step returns the problems and imports nothing.
"""

from __future__ import annotations

import json
import math
import pathlib
from typing import Any

import numpy as np

from app.nba import gen1_common as common


class PackageRefused(RuntimeError):
    """The package cannot be proved; ``problems`` lists every reason found."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems)[:500])
        self.problems = problems


def _read_population(root: pathlib.Path, relative: str) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    import polars as pl

    frame = pl.read_parquet(root / relative)
    columns = set(frame.columns)
    needed = {"drawIndex", "homeScore", "awayScore", "homeTeamCode", "awayTeamCode"}
    if not needed <= columns:
        raise ValueError(f"population lacks columns {sorted(needed - columns)}")
    home = frame["homeScore"].to_numpy().astype(np.int64)
    away = frame["awayScore"].to_numpy().astype(np.int64)
    meta = {"draw_index_ok": frame["drawIndex"].to_list() == list(range(len(frame))),
            "home_codes": sorted(set(frame["homeTeamCode"].to_list())),
            "away_codes": sorted(set(frame["awayTeamCode"].to_list())), "rows": len(frame)}
    return home, away, meta


def _compare_summary(declared: dict[str, Any], home: np.ndarray, away: np.ndarray) -> list[str]:
    """Recompute the producer's jointSimulation from the draws; any difference is a problem."""

    problems: list[str] = []
    summary = common.with_intervals(common.summarize_draws(home, away))
    winner = declared.get("winnerProbabilities") or {}
    if not math.isclose(float(winner.get("home", -1)), summary["winner"]["home"], abs_tol=1e-12):
        problems.append("JOINT_WINNER_DOES_NOT_REPRODUCE")
    if not math.isclose(float(winner.get("homeMcse", -1)), summary["winner"]["home_mcse"], abs_tol=1e-12):
        problems.append("JOINT_MCSE_DOES_NOT_REPRODUCE")
    expected = declared.get("expectedScores") or {}
    for key, ours in (("home", "home"), ("away", "away"), ("total", "total"), ("homeMargin", "margin")):
        if not math.isclose(float(expected.get(key, -1e9)), summary["expected"][ours], abs_tol=1e-9):
            problems.append(f"JOINT_EXPECTED_{key.upper()}_DOES_NOT_REPRODUCE")
    pmfs = declared.get("pmfs") or {}
    for name in ("home", "away", "total", "margin"):
        rows = pmfs.get(name) or []
        mass = summary["pmfs"][name]
        if [int(r.get("value")) for r in rows] != mass["values"] or \
                [int(r.get("count")) for r in rows] != mass["counts"]:
            problems.append(f"JOINT_PMF_{name.upper()}_DOES_NOT_REPRODUCE")
    intervals = declared.get("intervals") or {}
    for name in ("home", "away", "total", "margin"):
        ours = [(round(i["level"], 2), i["lower"], i["upper"]) for i in summary["intervals"][name]]
        theirs = [(round(float(i.get("level")), 2), int(i.get("lower")), int(i.get("upper")))
                  for i in intervals.get(name) or []]
        if ours != theirs:
            problems.append(f"JOINT_INTERVAL_{name.upper()}_DOES_NOT_REPRODUCE")
    total = summary["pmfs"]["total"]
    for query in declared.get("illustrativeTotals") or []:
        if query.get("lineClassification") != "ILLUSTRATIVE_NOT_RECORDED_MARKET":
            problems.append("ILLUSTRATIVE_TOTAL_NOT_LABELLED_ILLUSTRATIVE")
            continue
        ours = common.totals_query(total["values"], total["counts"], float(query.get("line")))
        for side in ("over", "under"):
            for key in ("win", "push", "loss"):
                if not math.isclose(float((query.get(side) or {}).get(key, -1)), ours[side][key], abs_tol=1e-12):
                    problems.append(f"ILLUSTRATIVE_TOTAL_{query.get('line')}_DOES_NOT_REPRODUCE")
                    break
    return problems


def verify_package(root: pathlib.Path, *, handoff_sha256: str, effective_at: Any) -> dict[str, Any]:
    """Return the verified package, or raise :class:`PackageRefused` with every problem found."""

    problems: list[str] = []
    root = pathlib.Path(root)
    index_path = root / "index.json"
    if not index_path.is_file():
        raise PackageRefused([f"NO_INDEX:{root}"])
    index_raw = index_path.read_bytes()
    index_sha = common.sha256_bytes(index_raw)
    if index_sha != root.name.lower():
        problems.append("INDEX_NOT_AT_ITS_CONTENT_ADDRESS")
    index = json.loads(index_raw)
    schema = index.get("schemaVersion")
    if schema not in (common.INDEX_SCHEMA_V1, common.INDEX_SCHEMA_V2):
        raise PackageRefused([f"INDEX_SCHEMA_UNKNOWN:{schema!r}"])

    seen: set[str] = set()
    members: dict[str, dict[str, Any]] = {}
    for entry in index.get("files") or []:
        relative = str(entry.get("relativePath") or "")
        rel = pathlib.PurePosixPath(relative)
        if not relative or rel.is_absolute() or ".." in rel.parts or relative in seen:
            problems.append(f"MEMBER_PATH_UNSAFE_OR_DUPLICATE:{relative}")
            continue
        seen.add(relative)
        try:
            common.bound({"path": root / relative, "sha256": entry.get("sha256"), "bytes": entry.get("bytes")},
                         relative)
            members[relative] = {"sha256": str(entry.get("sha256")).lower(), "bytes": int(entry.get("bytes"))}
        except common.BindingRefused as exc:
            problems.append(f"MEMBER_MISMATCH:{exc}")
    if "forecast.json" not in members:
        problems.append("FORECAST_NOT_A_BOUND_MEMBER")
    if problems:
        raise PackageRefused(problems)

    forecast_raw = (root / "forecast.json").read_bytes()
    forecast_sha = common.sha256_bytes(forecast_raw)
    if forecast_sha != str(index.get("forecastSha256", "")).lower():
        problems.append("FORECAST_SHA256_DIFFERS_FROM_INDEX")
    forecast = json.loads(forecast_raw)
    if forecast.get("forecastId") != index.get("forecastId"):
        problems.append("FORECAST_ID_DIFFERS_FROM_INDEX")
    if forecast.get("schema") != common.FORECAST_SCHEMA:
        problems.append(f"FORECAST_SCHEMA_UNKNOWN:{forecast.get('schema')!r}")
    if forecast.get("configurationIdentity") != common.CONFIGURATION:
        problems.append(f"CONFIGURATION_MISMATCH:{forecast.get('configurationIdentity')!r}")
    if forecast.get("distributionConfigurationIdentity") != common.DISTRIBUTION_CONFIGURATION:
        problems.append("DISTRIBUTION_CONFIGURATION_MISMATCH")
    if (forecast.get("closeScoreChallenger") or {}).get("enabled") is not False:
        problems.append("CLOSE_SCORE_CHALLENGER_NOT_DISABLED")
    authority = forecast.get("authority") or {}
    for claim in ("wageringAuthority", "decisionAuthority", "positionAuthority", "modelPromotionAuthority"):
        if authority.get(claim) is not False:
            problems.append(f"AUTHORITY_{claim.upper()}_NOT_FALSE")

    bindings = forecast.get("bindings") or {}
    # The pinned model handoff is checked the same way for both schemas, from the
    # FORECAST's own binding. For V1 the index repeats it, so the index is an
    # accepted fallback; for V2 the index names the slate release instead and is
    # never read here, or a release hash could be mistaken for a handoff hash.
    handoff_ref = bindings.get("productionHandoff") or (
        {} if schema == common.INDEX_SCHEMA_V2 else (index.get("productionHandoff") or {}))
    model_handoff = str((handoff_ref or {}).get("sha256", "")).lower()
    if model_handoff != handoff_sha256.lower():
        problems.append("PACKAGE_NOT_BOUND_TO_PINNED_HANDOFF")
    effective = common.ts(forecast.get("prospectiveEffectiveAtUtc"))
    release: dict[str, Any] | None = None
    release_ref: dict[str, Any] | None = None
    if schema == common.INDEX_SCHEMA_V2:
        # A V2 package's effective clock belongs to ITS OWN slate release, which is
        # physically bound in its forecast. The pinned model handoff is checked
        # independently (above and through the release), and the newest release's
        # clock is never copied onto a preserved older package.
        release_ref = bindings.get("slateRelease")
        if not isinstance(release_ref, dict) or not release_ref.get("sha256"):
            problems.append("SLATE_RELEASE_NOT_BOUND")
        else:
            try:
                release = json.loads(common.bound(release_ref, "slate release"))
            except (common.BindingRefused, ValueError) as exc:
                problems.append(f"SLATE_RELEASE_UNVERIFIED:{exc}"[:200])
        if release is not None:
            if release.get("schema") != common.SLATE_RELEASE_SCHEMA:
                problems.append(f"SLATE_RELEASE_SCHEMA_UNKNOWN:{release.get('schema')!r}")
            if str((release.get("modelHandoff") or {}).get("sha256", "")).lower() != handoff_sha256.lower():
                problems.append("RELEASE_DOES_NOT_BIND_PINNED_MODEL_HANDOFF")
            if (release.get("configurationIdentity") != common.CONFIGURATION
                    or release.get("distributionConfigurationIdentity") != common.DISTRIBUTION_CONFIGURATION
                    or int(release.get("drawCount") or 0) != common.DRAW_COUNT):
                problems.append("RELEASE_MODEL_IDENTITY_MISMATCH")
            index_release = index.get("productionHandoff")
            if index_release is not None and str((index_release or {}).get("sha256", "")).lower() != \
                    str(release_ref.get("sha256", "")).lower():
                problems.append("INDEX_RELEASE_DIFFERS_FROM_FORECAST")
            release_effective = common.ts(release.get("prospectiveEffectiveAtUtc"))
            if effective is None or release_effective is None or effective != release_effective:
                problems.append("EFFECTIVE_TIME_DIFFERS_FROM_BOUND_SLATE_RELEASE")
    else:
        if effective is None or effective != common.ts(effective_at):
            problems.append("EFFECTIVE_TIME_DIFFERS_FROM_HANDOFF")
    created = common.ts(forecast.get("createdAtUtc"))
    if created is None or effective is None or created < effective:
        problems.append("CREATED_BEFORE_PROSPECTIVE_EFFECTIVE_TIME")

    game = forecast.get("game") or {}
    tipoff = common.ts(game.get("scheduled_tipoff_utc"))
    cutoff = common.ts(forecast.get("sourceCutoffUtc"))
    if tipoff is None or cutoff is None or created is None or not (cutoff <= created < tipoff):
        problems.append("CHRONOLOGY_CUTOFF_CREATED_TIPOFF_OUT_OF_ORDER")
    if not game.get("home_team_id") or game.get("home_team_id") == game.get("away_team_id"):
        problems.append("INVALID_ORIENTATION")
    if str(game.get("game_id", "")) != f"nba-espn:game:{game.get('source_game_id')}":
        problems.append("GAME_ID_DOES_NOT_NAME_ITS_ESPN_ID")
    if index.get("game") and (index["game"].get("game_id") != game.get("game_id")):
        problems.append("INDEX_GAME_DIFFERS_FROM_FORECAST")

    elo = forecast.get("eloWinnerProbabilities") or {}
    try:
        mass = float(elo["home"]) + float(elo["away"]) + float(elo.get("tie", 0.0))
        if abs(mass - 1.0) > 1e-12 or not 0 <= float(elo["home"]) <= 1:
            problems.append("ELO_MASS_NOT_ONE")
    except (KeyError, TypeError, ValueError):
        problems.append("ELO_CHANNEL_MISSING")
    ridge = forecast.get("ridgePointExpectations") or {}
    if not all(isinstance(ridge.get(k), (int, float)) and math.isfinite(ridge[k])
               for k in ("home", "away", "total", "homeMargin")):
        problems.append("RIDGE_CHANNEL_MISSING")

    population = forecast.get("population") or {}
    artifact = population.get("artifact") or {}
    relative = str(artifact.get("relativePath") or "")
    if population.get("drawCount") != common.DRAW_COUNT or index.get("drawCount") != common.DRAW_COUNT:
        problems.append("DRAW_COUNT_NOT_10000")
    if relative not in members or members[relative]["sha256"] != str(artifact.get("sha256", "")).lower():
        problems.append("POPULATION_NOT_A_BOUND_MEMBER")
    if population.get("orientation") != "HOME_AWAY" or population.get("paired") is not True:
        problems.append("POPULATION_NOT_PAIRED_HOME_AWAY")
    summary: dict[str, Any] | None = None
    if not problems:
        try:
            home, away, meta = _read_population(root, relative)
        except Exception as exc:  # noqa: BLE001 -- an unreadable population is a refusal
            raise PackageRefused([f"POPULATION_UNREADABLE:{type(exc).__name__}:{exc}"[:200]]) from exc
        if not meta["draw_index_ok"] or meta["rows"] != common.DRAW_COUNT:
            problems.append("DRAW_INDEX_NOT_0_TO_9999")
        if meta["home_codes"] != [game.get("home_team_code")] or meta["away_codes"] != [game.get("away_team_code")]:
            problems.append("POPULATION_ORIENTATION_DIFFERS_FROM_FORECAST")
        logical = common.sha256_bytes(np.column_stack((home, away)).astype("<i8").tobytes())
        if logical != str(population.get("logicalHomeAwayInt64LeSha256", "")).lower():
            problems.append("POPULATION_LOGICAL_DIGEST_MISMATCH")
        try:
            problems.extend(_compare_summary(forecast.get("jointSimulation") or {}, home, away))
            summary = common.with_intervals(common.summarize_draws(home, away))
        except ValueError as exc:
            problems.append(f"POPULATION_INVALID:{exc}")
        summary_extra = {"logical_sha256": logical}
    if problems:
        raise PackageRefused(problems)

    assert summary is not None
    summary["illustrative_totals"] = (forecast.get("jointSimulation") or {}).get("illustrativeTotals") or []
    return {
        "root": root, "schema": schema, "index": index, "index_sha256": index_sha, "index_bytes": len(index_raw),
        "forecast": forecast, "forecast_sha256": forecast_sha, "forecast_bytes": len(forecast_raw),
        "members": members, "joint_summary": summary, "population_artifact_sha256": str(artifact["sha256"]).lower(),
        "population_logical_sha256": summary_extra["logical_sha256"],
        "handoff_sha256": handoff_sha256.lower(), "revision_key": index.get("revisionKey"),
        # V2 only: the package's OWN release and the effective clock it binds.
        "slate_release_sha256": str((release_ref or {}).get("sha256", "")).lower() or None,
        "slate_release": release_ref, "release_effective_at_utc": common.iso(effective),
        "paper_authorization_ref": (release or {}).get("paperEligibilityAuthorization"),
        "finals_contract_ref": (release or {}).get("finalsExchangeContract"),
    }


__all__ = ["PackageRefused", "verify_package"]
