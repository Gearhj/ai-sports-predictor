"""SYNTHETIC NBA Gen1 exchange fixtures, built in a temp directory. Never real data.

Every object mirrors the byte-level structure Model Studio publishes (content
addresses, canonical JSON, member digests, paired parquet populations,
publication receipts, slate discovery pointers) so the consumer is exercised
end to end. Identities are real-shaped but the numbers are generated here; the
teams are fixture codes (``FXH``/``FXA``) and the ESPN ids are outside ESPN's
range (``9900000xx``) so nothing can be mistaken for a real game.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import polars as pl

CONFIG = "NBA_GEN1_ELO_WIN_RIDGE_A100_ORDINARY_REST_V1"
LATTICE = "NBA_GEN1_JOINT_FINAL_PAIRED_OOF_LATTICE_V1"
EFFECTIVE = "2026-09-18T01:16:08.176807+00:00"
TIPOFF = datetime(2026, 10, 20, 23, 0, tzinfo=UTC)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def ref(path: pathlib.Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"path": str(path), "sha256": sha(raw), "bytes": len(raw)}


def content_addressed(parent: pathlib.Path, name: str, value: Any) -> pathlib.Path:
    raw = canonical(value)
    target = parent / "sha256" / sha(raw)
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_bytes(raw)
    return target / name


def draws(seed: int, home_mean: float = 116.0, away_mean: float = 111.0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    home = np.maximum(0, np.round(rng.normal(home_mean, 12, 10_000))).astype(np.int64)
    away = np.maximum(0, np.round(rng.normal(away_mean, 12, 10_000))).astype(np.int64)
    tied = home == away
    home[tied] += 1  # NBA finals cannot tie; the producer resolves ties the same direction-free way
    return home, away


def _pmf(values: np.ndarray) -> list[dict[str, Any]]:
    unique, counts = np.unique(values, return_counts=True)
    return [{"value": int(v), "count": int(c), "probability": float(c / len(values))} for v, c in zip(unique, counts)]


def _interval(values: np.ndarray, level: int) -> dict[str, Any]:
    alpha = (1 - level / 100) / 2
    lo, hi = np.quantile(values, [alpha, 1 - alpha], method="inverted_cdf")
    return {"level": level / 100, "lower": int(lo), "upper": int(hi),
            "method": "central equal-tail inverted_cdf", "units": "points including overtime"}


def _totals(home: np.ndarray, away: np.ndarray, line: float) -> dict[str, Any]:
    total = home + away
    n = len(total)
    over, push, under = int((total > line).sum()), int((total == line).sum()), int((total < line).sum())
    return {"line": float(line), "lineClassification": "ILLUSTRATIVE_NOT_RECORDED_MARKET", "drawCount": n,
            "probabilityUnits": "FRACTIONS_0_TO_1", "over": {"win": over / n, "push": push / n, "loss": under / n},
            "under": {"win": under / n, "push": push / n, "loss": over / n}}


def joint_simulation(home: np.ndarray, away: np.ndarray) -> dict[str, Any]:
    """An independent re-implementation of the producer's summary, for fixtures."""

    total, margin = home + away, home - away
    p = float((margin > 0).mean())
    mean_total = float(total.mean())
    return {"drawCount": 10_000, "scoreUnits": "points including overtime",
            "probabilityUnits": "fractions from 0 to 1",
            "expectedScores": {"home": float(home.mean()), "away": float(away.mean()), "total": mean_total,
                               "homeMargin": float(margin.mean())},
            "winnerProbabilities": {"home": p, "away": 1 - p, "tie": 0.0,
                                    "homeMcse": float(math.sqrt(p * (1 - p) / 10_000)),
                                    "awayMcse": float(math.sqrt(p * (1 - p) / 10_000))},
            "pmfs": {"home": _pmf(home), "away": _pmf(away), "total": _pmf(total), "margin": _pmf(margin)},
            "intervals": {n: [_interval(v, lvl) for lvl in (50, 80, 90)]
                          for n, v in (("home", home), ("away", away), ("total", total), ("margin", margin))},
            "illustrativeTotals": [_totals(home, away, float(round(mean_total))),
                                   _totals(home, away, float(math.floor(mean_total) + 0.5))]}


def make_handoff(root: pathlib.Path, *, wagering: bool = False, status: str = "READY_AWAITING_ACTIVATION") -> dict:
    staging = root / "handoff-staging"
    staging.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schemaVersion": "NBA_GEN1_PRODUCTION_BASELINE_HANDOFF_V2", "configurationIdentity": CONFIG,
        "createdAtUtc": EFFECTIVE, "prospectiveEffectiveAtUtc": EFFECTIVE, "status": status,
        "distribution": {"configurationIdentity": LATTICE, "drawCount": 10_000, "masterSeed": 20260914,
                         "closeScoreChallengerEnabled": False, "residualPopulation": {"sha256": "a" * 64}},
        "pointModel": {"artifact": {"sha256": "b" * 64}}, "winnerModel": {"artifact": {"sha256": "c" * 64}},
        "training": {"trainingGames": 8177, "lastDate": "2026-04-12"},
        "monitoring": {"reviewAtCompletedGames": [50, 100]},
        "authority": {"aispActivated": False, "producerActivated": False, "producerReady": True,
                      "decisionAuthority": False, "positionAuthority": False, "wageringAuthority": wagering},
        "disclosures": ["FIXTURE handoff"]}
    (staging / "manifest.json").write_bytes(canonical(manifest))
    (staging / "ACTIVATION.md").write_bytes(b"FIXTURE activation\n")
    members = [{"relativePath": name, "sha256": sha((staging / name).read_bytes()),
                "bytes": len((staging / name).read_bytes())} for name in ("ACTIVATION.md", "manifest.json")]
    index = {"createdAtUtc": EFFECTIVE, "members": members, "schemaVersion": "NBA_GEN1_PRODUCTION_HANDOFF_INDEX_V2"}
    raw = canonical(index)
    target = root / "handoffs" / "sha256" / sha(raw)
    target.mkdir(parents=True, exist_ok=True)
    for name in ("ACTIVATION.md", "manifest.json"):
        shutil.copy(staging / name, target / name)
    (target / "index.json").write_bytes(raw)
    return {"path": str(target / "index.json"), "sha256": sha(raw), "bytes": len(raw)}


def game(espn: int = 990000001, home: str = "FXH", away: str = "FXA", tipoff: datetime = TIPOFF) -> dict:
    return {"away_team_code": away, "away_team_id": f"nba-neil:franchise:{away}",
            "game_date": tipoff.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).date().isoformat(),
            "game_id": f"nba-espn:game:{espn}", "home_team_code": home, "home_team_id": f"nba-neil:franchise:{home}",
            "neutral_site": False, "scheduled_tipoff_utc": tipoff.isoformat(), "season_end": 2027,
            "season_type": "REGULAR", "source_game_id": espn,
            "source_revision": "github-asset:0:sha256:" + "d" * 64, "status": "SCHEDULED"}


def make_package(root: pathlib.Path, handoff: dict, *, g: dict | None = None, schema: str = "V1",
                 created: str = "2026-09-18T02:00:00+00:00", seed: int = 7, elo_home: float = 0.64,
                 ridge: tuple[float, float] = (116.2, 111.4), mutate=None) -> pathlib.Path:
    g = g or game()
    home, away = draws(seed)
    build = root / f"build-{seed}-{schema}-{g['source_game_id']}"
    (build / "population").mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"drawIndex": np.arange(10_000, dtype=np.int32), "homeScore": home.astype(np.int16),
                  "awayScore": away.astype(np.int16), "homeTeamCode": [g["home_team_code"]] * 10_000,
                  "awayTeamCode": [g["away_team_code"]] * 10_000}).write_parquet(
        build / "population" / "joint-final-score-draws.parquet")
    pop_ref = ref(build / "population" / "joint-final-score-draws.parquet")
    population = {"artifact": {"relativePath": "population/joint-final-score-draws.parquet",
                               "sha256": pop_ref["sha256"], "bytes": pop_ref["bytes"]},
                  "drawCount": 10_000, "fullTailRetained": True, "orientation": "HOME_AWAY", "paired": True,
                  "logicalHomeAwayInt64LeSha256": sha(np.column_stack((home, away)).astype("<i8").tobytes())}
    published = created if schema == "V1" else None
    forecast = {
        "schema": "NBA_GEN1_PRODUCTION_FORECAST_V1", "forecastHorizon": "PREGAME_PRODUCTION_BASELINE",
        "configurationIdentity": CONFIG, "distributionConfigurationIdentity": LATTICE, "createdAtUtc": created,
        "publishedAtUtc": published, "prospectiveEffectiveAtUtc": EFFECTIVE,
        "sourceCutoffUtc": "2026-09-14T13:04:43.697362+00:00", "game": g,
        "ridgePointExpectations": {"home": ridge[0], "away": ridge[1], "total": ridge[0] + ridge[1],
                                   "homeMargin": ridge[0] - ridge[1], "units": "points including overtime"},
        "eloWinnerProbabilities": {"home": elo_home, "away": 1 - elo_home, "tie": 0.0,
                                   "method": "UNCHANGED_CHRONOLOGICAL_ELO_1500_K20_HOME65_SCALE400_SEASON_REGRESSION_TWO_THIRDS",
                                   "units": "fractions 0 to 1"},
        "jointSimulation": joint_simulation(home, away), "population": population,
        "channelSemantics": "FIXTURE: Ridge points; Elo winner; joint draws.",
        "closeScoreChallenger": {"enabled": False, "disposition": "TESTED_AND_REJECTED"},
        "bindings": {"productionHandoff": handoff, "pointModel": {"sha256": "b" * 64}},
        "monitoring": {"firstReviewCompletedGames": 50}, "limitations": ["FIXTURE"],
        "authority": {"productionBaseline": True, "prospectiveOnly": True, "requiresConsumerPin": True,
                      "wageringAuthority": False, "decisionAuthority": False, "positionAuthority": False,
                      "modelPromotionAuthority": False},
        "forecastId": f"nba-production:fixture-{seed}-{g['source_game_id']}-{schema}"}
    if mutate:
        mutate(forecast)
    (build / "forecast.json").write_bytes(canonical(forecast))
    files = []
    for path in sorted(build.rglob("*")):
        if path.is_file():
            files.append({"relativePath": path.relative_to(build).as_posix(), "sha256": sha(path.read_bytes()),
                          "bytes": len(path.read_bytes())})
    index = {"schemaVersion": f"NBA_GEN1_PRODUCTION_FORECAST_PACKAGE_INDEX_{schema}", "createdAtUtc": created,
             "publishedAtUtc": published, "forecastId": forecast["forecastId"], "game": g,
             "configurationIdentity": CONFIG, "distributionConfigurationIdentity": LATTICE, "drawCount": 10_000,
             "population": population, "forecastSha256": sha((build / "forecast.json").read_bytes()),
             "productionHandoff": handoff, "files": files, "consumerAcknowledged": False,
             **({"revisionKey": f"rev-{seed}"} if schema == "V2" else {})}
    raw = canonical(index)
    target = root / "forecasts" / "sha256" / sha(raw)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(build, target)
    (target / "index.json").write_bytes(raw)
    shutil.rmtree(build)
    return target


def write_v1_discovery(root: pathlib.Path, packages: list[pathlib.Path]) -> pathlib.Path:
    entries = []
    for package in packages:
        index = json.loads((package / "index.json").read_bytes())
        entries.append({"forecastId": index["forecastId"], "gameId": index["game"]["game_id"],
                        "index": ref(package / "index.json"), "packageAddress": package.name})
    path = root / "discovery" / "current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical({"consumerAcknowledged": False, "packages": entries,
                                "schemaVersion": "NBA_GEN1_PRODUCTION_DISCOVERY_V1",
                                "updatedAtUtc": "2026-09-18T02:00:01+00:00"}))
    return path


def write_v2_discovery(root: pathlib.Path, handoff: dict, packages: list[tuple[pathlib.Path, dict]], *,
                       created: str = "2026-10-20T22:15:00+00:00", release_handoff: dict | None = None,
                       predecessor: dict | None = None, receipt_override: dict | None = None) -> dict:
    """packages: [(package_dir, {committedAtUtc, currentlyEligible, eligibilityValidUntilUtc})]."""

    release = content_addressed(root / "releases", "release.json",
                                {"schema": "FIXTURE_RELEASE", "modelHandoff": release_handoff or handoff})
    entries = []
    for package, lease in packages:
        index = json.loads((package / "index.json").read_bytes())
        saved = {"packageDirectory": str(package), "packageAddress": package.name,
                 "revisionKey": index.get("revisionKey"), "forecastId": index["forecastId"],
                 "gameId": index["game"]["game_id"], "index": ref(package / "index.json"),
                 "committedAtUtc": lease["committedAtUtc"], "originallyPublishedAtUtc": None,
                 "publicationClockSemantics": "First durable availability confirmation"}
        receipt_doc = {**saved, **(receipt_override or {})}
        receipt = content_addressed(root / "publication-receipts", "receipt.json", receipt_doc)
        entries.append({**saved, "publicationReceipt": ref(receipt), "currentlyEligible": lease["currentlyEligible"],
                        "eligibilityValidUntilUtc": lease.get("eligibilityValidUntilUtc"),
                        "status": "CURRENT_ELIGIBLE" if lease["currentlyEligible"] else "PRESERVED_HISTORICAL_VERSION"})
    manifest = content_addressed(root / "slate-discovery" / "versions", "index.json", {
        "schemaVersion": "NBA_GEN1_SLATE_DISCOVERY_V1", "createdAtUtc": created, "predecessor": predecessor,
        "inputVersion": None, "release": ref(release), "packages": entries, "preservedEarlierPackages": [],
        "games": [{"gameId": e["gameId"], "disposition": "DUE"} for e in entries], "consumerAcknowledged": False})
    pointer = root / "slate-discovery" / "current.json"
    pointer.write_bytes(canonical({"schemaVersion": "NBA_GEN1_SLATE_DISCOVERY_POINTER_V1", "manifest": ref(manifest)}))
    return ref(manifest)


def write_schedule_object(root: pathlib.Path, rows: list[dict[str, Any]], *, receipt: bool = True,
                          captured: str = "2026-10-21T12:00:00+00:00") -> str:
    """A synthetic ESPN-shaped schedule parquet at its content address (+ an optional receipt)."""

    staging = root / "schedule-staging.parquet"
    columns = {"game_id": [], "season": [], "season_type": [], "type_abbreviation": [], "start_date": [],
               "neutral_site": [], "status_type_name": [], "status_type_completed": [], "status_period": [],
               "home_id": [], "away_id": [], "home_abbreviation": [], "away_abbreviation": [],
               "home_display_name": [], "away_display_name": [], "home_score": [], "away_score": []}
    for row in rows:
        for key in columns:
            columns[key].append(row.get(key))
    pl.DataFrame(columns, schema={"game_id": pl.Int64, "season": pl.Int64, "season_type": pl.Int64,
                                  "type_abbreviation": pl.Utf8, "start_date": pl.Utf8, "neutral_site": pl.Boolean,
                                  "status_type_name": pl.Utf8, "status_type_completed": pl.Boolean,
                                  "status_period": pl.Float64, "home_id": pl.Int64, "away_id": pl.Int64,
                                  "home_abbreviation": pl.Utf8, "away_abbreviation": pl.Utf8,
                                  "home_display_name": pl.Utf8, "away_display_name": pl.Utf8,
                                  "home_score": pl.Int64, "away_score": pl.Int64}).write_parquet(staging)
    digest = sha(staging.read_bytes())
    target = root / "source" / "sha256" / digest
    target.mkdir(parents=True, exist_ok=True)
    final = target / "schedule.parquet"
    shutil.move(str(staging), str(final))
    entry = f"{final}|{digest}"
    if receipt:
        receipt_path = root / f"receipt-{digest[:8]}.json"
        receipt_path.write_bytes(canonical({"acquiredAtUtc": captured, "original": {"sha256": digest}}))
        entry += f"|{receipt_path}|{sha(receipt_path.read_bytes())}"
    return entry


def make_historical(root: pathlib.Path, games: list[dict[str, Any]], *, corrupt_digest_for: int | None = None,
                    joint_ridge_offset_for: int | None = None) -> dict[str, str]:
    """Synthetic assessment + joint-residual receipts shaped like Model Studio's.

    ``games``: [{espn, home, away, date, tipoff, elo, ridge_home, ridge_away, seed}].
    """

    pred_rows, pop_rows, cell_rows = [], [], []
    for index, g in enumerate(games):
        gid = f"nba-espn:game:{g['espn']}"
        common = {"game_id": gid, "season_end": 2026, "game_date": g["date"],
                  "home_team_id": f"nba-neil:franchise:{g['home']}", "away_team_id": f"nba-neil:franchise:{g['away']}",
                  "home_team_code": g["home"], "away_team_code": g["away"]}
        for model_id, sha_value in (("ridge_a100_rest", "7" * 64), ("elo", "e" * 64)):
            pred_rows.append({**common, "model_id": model_id, "family": model_id.split("_")[0],
                              "model_sha256": sha_value, "evidence_class": "A_HISTORICAL_FINALIZED_RECORD_RECONSTRUCTION",
                              "expected_home_score": g["ridge_home"], "expected_away_score": g["ridge_away"],
                              "p_home_win": g["elo"], "p_away_win": 1 - g["elo"], "p_tie": 0.0,
                              "winner_probability_source": "UNCHANGED_CHRONOLOGICAL_ELO"})
        home, away = draws(g["seed"], g["ridge_home"], g["ridge_away"])
        pairs, counts = np.unique(np.column_stack((home, away)), axis=0, return_counts=True)
        cells = np.column_stack((pairs, counts)).astype(np.int64)
        digest = sha(cells.astype("<i8").tobytes())
        if corrupt_digest_for == index:
            digest = "0" * 64
        ridge_home = g["ridge_home"] + (0.5 if joint_ridge_offset_for == index else 0.0)
        pop_rows.append({"game_id": gid, "game_index": 5000 + index, "configuration": "paired",
                         "ridge_expected_home": ridge_home, "ridge_expected_away": g["ridge_away"],
                         "elo_home_win_probability": g["elo"], "joint_counts_int64_le_sha256": digest,
                         "donors": 6000, "bank_id": "f" * 64, "both_ordinary_rest_supported": True,
                         "either_season_opener": False, "early_season": False})
        for h, a, c in cells:
            cell_rows.append({"game_index": 5000 + index, "home_score": int(h), "away_score": int(a), "count": int(c)})
    folder = root / "assessment"
    folder.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(pred_rows).write_parquet(folder / "predictions.parquet")
    pl.DataFrame(pop_rows).write_parquet(folder / "populations.parquet")
    pl.DataFrame(cell_rows, schema={"game_index": pl.Int32, "home_score": pl.Int32, "away_score": pl.Int32,
                                    "count": pl.Int32}).write_parquet(folder / "paired-joint-counts.parquet")
    (folder / "method-freeze.json").write_bytes(canonical({"engine": LATTICE}))
    assessment = {"schema": "NBA_GEN1_REST_FIXED_2026_ASSESSMENT_V1_ASSESSMENT_RECEIPT",
                  "evidenceClass": "A_HISTORICAL_FINALIZED_RECORD_RECONSTRUCTION",
                  "authority": {"researchOnly": True, "wageringAuthority": False},
                  "artifacts": {"predictions": ref(folder / "predictions.parquet")}}
    (folder / "assessment-receipt.json").write_bytes(canonical(assessment))
    joint = {"schema": "NBA_GEN1_JOINT_RESIDUAL_SEASON_ASSESSMENT_V1", "drawsPerConfigurationPerGame": 10_000,
             "artifacts": [ref(folder / "paired-joint-counts.parquet"), ref(folder / "populations.parquet")],
             "method": ref(folder / "method-freeze.json")}
    (folder / "joint-receipt.json").write_bytes(canonical(joint))
    return {"assessment_path": str(folder / "assessment-receipt.json"),
            "assessment_sha": sha((folder / "assessment-receipt.json").read_bytes()),
            "joint_path": str(folder / "joint-receipt.json"),
            "joint_sha": sha((folder / "joint-receipt.json").read_bytes())}


def schedule_row(espn: int, *, home_id: int = 101, away_id: int = 202, home: int | None = 118,
                 away: int | None = 109, final: bool = True, tipoff: datetime = TIPOFF, periods: float = 4.0,
                 home_code: str = "FXH", away_code: str = "FXA") -> dict[str, Any]:
    return {"game_id": espn, "season": 2027, "season_type": 2, "type_abbreviation": "STD",
            "start_date": tipoff.strftime("%Y-%m-%dT%H:%MZ"), "neutral_site": False,
            "status_type_name": "STATUS_FINAL" if final else "STATUS_SCHEDULED", "status_type_completed": final,
            "status_period": periods, "home_id": home_id, "away_id": away_id, "home_abbreviation": home_code,
            "away_abbreviation": away_code, "home_display_name": f"Fixture {home_code}",
            "away_display_name": f"Fixture {away_code}", "home_score": home if final else None,
            "away_score": away if final else None}
