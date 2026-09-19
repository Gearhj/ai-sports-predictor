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
#: The pinned MODEL handoff's clock (September 18). V1 packages are dated by it.
EFFECTIVE = "2026-09-18T01:16:08.176807+00:00"
#: A LATER slate-release clock, deliberately different from the handoff's, so a
#: V2 package proves its effective time against its own bound release.
RELEASE_EFFECTIVE = "2026-09-19T20:26:29.236061+00:00"
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


def make_release(root: pathlib.Path, handoff: dict, *, effective: str = RELEASE_EFFECTIVE,
                 paper_grant: dict | None = None, refresh_plan: dict | None = None,
                 finals_contract: dict | None = None, mutate=None) -> dict:
    """One ``NBA_GEN1_SLATE_RELEASE_V1``: the clock, the model and the optional grant a V2 package binds."""

    doc = {"schema": "NBA_GEN1_SLATE_RELEASE_V1", "createdAtUtc": effective,
           "prospectiveEffectiveAtUtc": effective, "modelHandoff": handoff,
           "configurationIdentity": CONFIG, "distributionConfigurationIdentity": LATTICE,
           "drawCount": 10_000, "masterSeed": 20260914,
           "paperEligibilityAuthorization": paper_grant, "refreshPlan": refresh_plan,
           "finalsExchangeContract": finals_contract, "networkEnabledByDefault": False,
           "authority": {"producerInstalled": False, "consumerActivated": False, "wageringAuthority": False,
                         "decisionAuthority": False, "positionAuthority": False,
                         "modelPromotionAuthority": False}}
    if mutate:
        mutate(doc)
    return ref(content_addressed(root / "releases", "release.json", doc))


def make_package(root: pathlib.Path, handoff: dict, *, g: dict | None = None, schema: str = "V1",
                 created: str | None = None, seed: int = 7, elo_home: float = 0.64,
                 ridge: tuple[float, float] = (116.2, 111.4), mutate=None,
                 release: dict | None = None) -> pathlib.Path:
    g = g or game()
    if schema == "V2":
        # A V2 package carries its OWN slate release; its effective clock is that
        # release's, not the model handoff's.
        release = release or make_release(root, handoff)
        effective = json.loads(pathlib.Path(release["path"]).read_bytes())["prospectiveEffectiveAtUtc"]
        created = created or "2026-09-19T21:00:00+00:00"
    else:
        release, effective = None, EFFECTIVE
        created = created or "2026-09-18T02:00:00+00:00"
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
        "publishedAtUtc": published, "prospectiveEffectiveAtUtc": effective,
        "sourceCutoffUtc": "2026-09-14T13:04:43.697362+00:00", "game": g,
        "ridgePointExpectations": {"home": ridge[0], "away": ridge[1], "total": ridge[0] + ridge[1],
                                   "homeMargin": ridge[0] - ridge[1], "units": "points including overtime"},
        "eloWinnerProbabilities": {"home": elo_home, "away": 1 - elo_home, "tie": 0.0,
                                   "method": "UNCHANGED_CHRONOLOGICAL_ELO_1500_K20_HOME65_SCALE400_SEASON_REGRESSION_TWO_THIRDS",
                                   "units": "fractions 0 to 1"},
        "jointSimulation": joint_simulation(home, away), "population": population,
        "channelSemantics": "FIXTURE: Ridge points; Elo winner; joint draws.",
        "closeScoreChallenger": {"enabled": False, "disposition": "TESTED_AND_REJECTED"},
        "bindings": {"productionHandoff": handoff, "pointModel": {"sha256": "b" * 64},
                     **({"slateRelease": release} if release else {})},
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
             # V1 repeats the model handoff here; V2 names the slate release, exactly
             # as the producer's slate publisher writes it.
             "productionHandoff": release or handoff, "files": files, "consumerAcknowledged": False,
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
                       predecessor: dict | None = None, receipt_override: dict | None = None,
                       release: dict | None = None) -> dict:
    """packages: [(package_dir, {committedAtUtc, currentlyEligible, eligibilityValidUntilUtc})].

    The discovery release may legitimately be NEWER than a preserved package's own
    bound release; nothing here copies one onto the other.
    """

    release = release or make_release(root, release_handoff or handoff)
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
        "inputVersion": None, "release": release, "packages": entries, "preservedEarlierPackages": [],
        "games": [{"gameId": e["gameId"], "disposition": "DUE"} for e in entries], "consumerAcknowledged": False})
    pointer = root / "slate-discovery" / "current.json"
    pointer.write_bytes(canonical({"schemaVersion": "NBA_GEN1_SLATE_DISCOVERY_POINTER_V1", "manifest": ref(manifest)}))
    return ref(manifest)


#: The producer's finals-exchange contract, copied literally from its own source.
FINALS_CONTRACT = {
    "schema": "NBA_GEN1_FINAL_EXCHANGE_CONTRACT_V1",
    "recordSchema": "NBA_GEN1_SOURCE_CONFIRMED_FINAL_V1",
    "discoverySchema": "NBA_GEN1_FINAL_DISCOVERY_V1",
    "lane": "finals-discovery/current.json",
    "sourceStatuses": ["STATUS_FINAL/post/completed=true"],
    "competition": "NBA_REGULAR_SEASON_STANDARD_NONNEUTRAL",
    "scoreConvention": "FULL_GAME_INCLUDING_OVERTIME",
    "completionClock": ("Unknown: completedAtUtc is null; never replace with tipoff, capture, "
                        "admission or last-play time"),
    "captureClock": "Actual retained original source acquisition, unchanged by HTTP 304",
    "admissionClock": "Actual per-final projection verification time; not game completion",
    "revisions": ("Append-only physical source witness; corrections/retractions preserve previous "
                  "records and explicitly replace current membership"),
    "joins": ("Exact source/canonical game, home/away team IDs and original scheduled tipoff; changed "
              "identity or schedule requires consumer reconciliation, never silent alias or remap"),
    "authority": "Evidence delivery only: no consumer settlement, decision, policy or wagering authority",
}


def write_finals_lane(root: pathlib.Path, handoff: dict, entries: list[dict[str, Any]], *,
                      previous: dict | None = None, captured: str = "2026-10-21T03:20:00+00:00",
                      validated: str | None = None, admitted: str = "2026-10-21T04:00:00+00:00",
                      created: str | None = None, contract: dict | None = None, release: dict | None = None,
                      games: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """One ``finals-discovery/current.json`` lane: pointer, manifest and per-game records.

    ``entries``: ``[{"game": <game dict>, "home": int, "away": int, "mutate": fn}]``.
    ``previous``: an earlier result, whose outcomes are PRESERVED here with their
    membership withdrawn -- exactly as the producer does for a correction.
    """

    validated = validated or captured
    finals_root = root / "finals-lane"
    source_path = content_addressed(finals_root / "source", "source.json", {"fixtureCapturedAtUtc": captured})
    source_ref = ref(source_path)
    capture_ref = ref(content_addressed(finals_root / "captures", "receipt.json", {
        "schema": "NBA_GOVERNED_SCHEDULE_CAPTURE_V1", "httpStatus": 200, "capturedAtUtc": captured,
        "validatedAtUtc": validated, "source": source_ref}))
    contract_ref = contract or ref(content_addressed(finals_root / "contract", "contract.json", FINALS_CONTRACT))
    release_ref = release or make_release(root, handoff, finals_contract=contract_ref)

    outcomes = [{**o, "currentlySourceConfirmed": False} for o in ((previous or {}).get("outcomes") or [])]
    built: dict[str, dict[str, Any]] = {}
    for entry in entries:
        g = entry.get("game") or game()
        identity = {k: g[k] for k in ("game_id", "source_game_id", "season_end", "game_date", "home_team_id",
                                      "away_team_id", "home_team_code", "away_team_code",
                                      "scheduled_tipoff_utc", "neutral_site", "season_type")}
        key = sha(canonical({"source": source_ref["sha256"], "game": identity,
                             "homeScore": entry["home"], "awayScore": entry["away"]}))
        record = {"schemaVersion": "NBA_GEN1_SOURCE_CONFIRMED_FINAL_V1", "gameId": g["game_id"],
                  "game": identity, "status": "FINAL", "sourceStatus": "STATUS_FINAL",
                  "homeScore": entry["home"], "awayScore": entry["away"], "completedAtUtc": None,
                  "completionClockBasis": "NOT_RETAINED_BY_ADMITTED_SOURCE",
                  "capturedAtUtc": captured, "sourceValidatedAtUtc": validated, "admittedAtUtc": admitted,
                  "scoreConvention": "FULL_GAME_INCLUDING_OVERTIME", "source": source_ref,
                  "captureReceipt": capture_ref, "release": release_ref, "contract": contract_ref,
                  "revisionKey": key,
                  "predecessorRevisions": [o["record"] for o in outcomes if o["gameId"] == g["game_id"]],
                  "admissionScope": "PER_GAME_FINAL_ONLY_NOT_MODEL_HISTORY_MEMBERSHIP",
                  "consumerAuthority": "EVIDENCE_ONLY_NO_SETTLEMENT_AUTHORITY"}
        if entry.get("mutate"):
            entry["mutate"](record)
        record_ref = ref(content_addressed(finals_root / "finals", "final.json", record))
        outcomes.append({"gameId": record["gameId"], "revisionKey": record["revisionKey"],
                         "record": record_ref, "currentlySourceConfirmed": entry.get("confirmed", True)})
        built[str(record["gameId"])] = {"record": record_ref, "document": record}

    inventory = games if games is not None else [
        {"gameId": gid, "sourceGameId": int(str(gid).rsplit(":", 1)[-1]),
         "disposition": "SOURCE_CONFIRMED_FINAL", "sourceStatus": "STATUS_FINAL",
         "scheduledTipoffUtc": built[gid]["document"]["game"]["scheduled_tipoff_utc"],
         "reason": None, "currentFinal": built[gid]["record"]} for gid in built]
    manifest = {"schemaVersion": "NBA_GEN1_FINAL_DISCOVERY_V1", "createdAtUtc": created or admitted,
                "predecessor": (previous or {}).get("manifest"), "captureReceipt": capture_ref,
                "source": source_ref, "contract": contract_ref, "release": release_ref,
                "outcomes": outcomes, "games": inventory,
                "semantics": ("Current source membership, not settlement or completion-time authority; "
                              "preserve all prior revisions")}
    manifest_ref = ref(content_addressed(finals_root / "finals-discovery" / "versions", "index.json", manifest))
    pointer = finals_root / "finals-discovery" / "current.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_bytes(canonical({"schemaVersion": "NBA_GEN1_FINAL_DISCOVERY_POINTER_V1",
                                   "manifest": manifest_ref}))
    return {"pointer": str(pointer), "manifest": manifest_ref, "outcomes": outcomes, "records": built,
            "contract": contract_ref, "release": release_ref, "source": source_ref, "capture": capture_ref,
            "settings": {"nba_finals_exchange_pointer": str(pointer)}}


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
