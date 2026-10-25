"""Model Studio's 2025-26 NBA Gen1 assessment, imported as HISTORICAL evidence only.

WHAT THIS IS
------------
Two Model Studio receipts, each pinned by SHA-256 and each binding its own
artifacts by SHA-256 and byte count:

* ``NBA_GEN1_REST_FIXED_2026_ASSESSMENT_V1_ASSESSMENT_RECEIPT`` -- the fixed
  2025-26 assessment: Ridge alpha100 ordinary-rest expected points and the
  unchanged chronological Elo winner probability for 1,225 oriented regular-
  season games, the models frozen on 6,952 earlier games BEFORE any 2025-26
  score was read.
* ``NBA_GEN1_JOINT_RESIDUAL_SEASON_ASSESSMENT_V1`` (season 2026) -- the paired
  OOF residual lattice populations for the same games: 10,000 draws per game,
  retained losslessly as joint-cell counts, each game's counts digest-bound.
  Its frozen method (``method-freeze.json`` ``c4960a87...``) is the SAME method
  the production handoff binds for its joint channel.

WHAT IT IS NOT
--------------
Prospective. Evidence class ``A_HISTORICAL_FINALIZED_RECORD_RECONSTRUCTION``:
reconstructed from finalized records, never published before the games, with
no publication clock. Every row and every read says HISTORICAL. These rows are
scored in their own evidence class and never counted with production forecasts,
never used by a paper decision, and never relabelled.

Models: the assessment-frozen Ridge (``ridge_a100_rest``) and Elo artifacts,
not the production refit (8,177 games). Both digests are recorded per row.

THE CROSS-CHECKS
----------------
* The joint receipt's per-game Ridge means and Elo probability must equal the
  assessment's EXACTLY (they are the same predictions), or the game is skipped.
* Each game's joint-cell digest (``joint_counts_int64_le_sha256``) is recomputed
  from the lexicographically ordered cells and must match; counts must total
  10,000 and no cell may be tied.
* The game is admitted only from the retained ESPN schedule object: exact ESPN
  game id, its tipoff, and ESPN team ids whose franchise pairing is unique both
  ways across the whole season. The source's Eastern calendar date must equal
  the assessment's ``game_date``.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.nba_gen1 import EVIDENCE_HISTORICAL
from app.nba import gen1_common as common
from app.nba import gen1_identity as identity
from app.nba import gen1_source as source

ASSESSMENT_SCHEMA = "NBA_GEN1_REST_FIXED_2026_ASSESSMENT_V1_ASSESSMENT_RECEIPT"
JOINT_SCHEMA = "NBA_GEN1_JOINT_RESIDUAL_SEASON_ASSESSMENT_V1"
POINT_MODEL_ID = "ridge_a100_rest"
ELO_MODEL_ID = "elo"
EASTERN = ZoneInfo("America/New_York")
LABEL = "HISTORICAL · 2025-26 reconstruction (not a prospective forecast)"


class HistoricalRefused(RuntimeError):
    pass


def _pinned(path: str, sha: str, label: str) -> tuple[dict[str, Any], str]:
    if not path or not sha:
        raise HistoricalRefused(f"{label} is not pinned")
    raw = pathlib.Path(path).read_bytes()
    digest = common.sha256_bytes(raw)
    if digest != sha.strip().lower():
        raise HistoricalRefused(f"{label} hashes to {digest}, pinned {sha}")
    return json.loads(raw), digest


def load(settings: Any) -> dict[str, Any]:
    """Verify both receipts and read their artifacts. Writes nothing."""

    import polars as pl

    assessment, assessment_sha = _pinned(settings.nba_historical_assessment_receipt_path,
                                         settings.nba_historical_assessment_receipt_sha256, "assessment receipt")
    if assessment.get("schema") != ASSESSMENT_SCHEMA:
        raise HistoricalRefused(f"assessment schema {assessment.get('schema')!r}")
    if assessment.get("evidenceClass") != EVIDENCE_HISTORICAL:
        raise HistoricalRefused("assessment is not evidence class A (historical reconstruction)")
    authority = assessment.get("authority") or {}
    if authority.get("researchOnly") is not True or authority.get("wageringAuthority"):
        raise HistoricalRefused("assessment does not declare research-only authority")
    artifacts = assessment.get("artifacts") or {}
    predictions_raw = common.bound(artifacts["predictions"], "assessment predictions")
    predictions_sha = common.sha256_bytes(predictions_raw)
    predictions = pl.read_parquet(pathlib.Path(artifacts["predictions"]["path"]))

    joint, joint_sha = _pinned(settings.nba_historical_joint_receipt_path,
                               settings.nba_historical_joint_receipt_sha256, "joint receipt")
    if joint.get("schema") != JOINT_SCHEMA:
        raise HistoricalRefused(f"joint schema {joint.get('schema')!r}")
    if int(joint.get("drawsPerConfigurationPerGame") or 0) != common.DRAW_COUNT:
        raise HistoricalRefused("joint receipt is not 10,000 draws per game")
    by_name = {pathlib.Path(a["path"]).name: a for a in joint.get("artifacts") or []}
    for name in ("paired-joint-counts.parquet", "populations.parquet"):
        if name not in by_name:
            raise HistoricalRefused(f"joint receipt does not bind {name}")
        common.bound(by_name[name], name)
    method = joint.get("method") or {}
    common.bound(method, "joint method freeze")
    populations = pl.read_parquet(by_name["populations.parquet"]["path"]).filter(pl.col("configuration") == "paired")
    counts = pl.read_parquet(by_name["paired-joint-counts.parquet"]["path"])
    return {"assessment": assessment, "assessment_sha256": assessment_sha, "predictions": predictions,
            "predictions_sha256": predictions_sha, "joint": joint, "joint_sha256": joint_sha,
            "joint_counts_sha256": str(by_name["paired-joint-counts.parquet"]["sha256"]).lower(),
            "joint_method_sha256": str(method.get("sha256", "")).lower(),
            "populations": populations, "counts": counts}


def _cells(partitions: dict, game_index: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    part = partitions.get((game_index,)) if (game_index,) in partitions else partitions.get(game_index)
    if part is None:
        raise ValueError(f"no joint cells for game index {game_index}")
    pairs = np.column_stack([part["home_score"].to_numpy().astype(np.int64),
                             part["away_score"].to_numpy().astype(np.int64),
                             part["count"].to_numpy().astype(np.int64)])
    order = np.lexsort((pairs[:, 1], pairs[:, 0]))
    pairs = pairs[order]
    return pairs[:, 0], pairs[:, 1], pairs[:, 2], common.sha256_bytes(pairs.astype("<i8").tobytes())


def import_historical(session: Session, settings: Any, *, now: datetime | None = None,
                      limit: int | None = None, only_games: set[str] | None = None) -> dict[str, Any]:
    """Verify, admit and write every assessment game once. Idempotent."""

    from app.models import NbaHistoricalForecast

    moment = now or datetime.now(UTC)
    loaded = load(settings)
    objects, source_problems = source.load_objects(settings)
    rows_by_espn: dict[str, dict[str, Any]] = {}
    for obj in objects:
        rows_by_espn.update(obj.rows)
    predictions = loaded["predictions"]
    by_game: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in predictions.iter_rows(named=True):
        if row["model_id"] in (POINT_MODEL_ID, ELO_MODEL_ID):
            by_game[row["game_id"]][row["model_id"]] = row
    joint_rows = {row["game_id"]: row for row in loaded["populations"].iter_rows(named=True)}

    codex_rows = []
    for game_id, models in by_game.items():
        point = models.get(POINT_MODEL_ID)
        if point is None:
            continue
        codex_rows.append({"espn_game_id": identity.espn_id(game_id), "home_franchise_id": point["home_team_id"],
                           "away_franchise_id": point["away_team_id"]})
    pairing = identity.espn_team_pairs(codex_rows, rows_by_espn)

    summary: dict[str, Any] = {
        "evidence_class": EVIDENCE_HISTORICAL, "label": LABEL, "assessment_receipt_sha256": loaded["assessment_sha256"],
        "joint_receipt_sha256": loaded["joint_sha256"], "predictions_sha256": loaded["predictions_sha256"],
        "joint_counts_sha256": loaded["joint_counts_sha256"], "joint_method_sha256": loaded["joint_method_sha256"],
        "source_objects": [o.describe() for o in objects], "source_problems": source_problems,
        "franchise_espn_pairs": len(pairing["pairs"]), "pairing_conflicts": pairing["conflicts"],
        "games_in_assessment": len(by_game), "imported": 0, "reused": 0, "games_admitted": 0, "skipped": [],
        "provider_requests": 0,
    }
    existing = {row.codex_game_id for row in session.scalars(select(NbaHistoricalForecast).where(
        NbaHistoricalForecast.assessment_receipt_sha256 == loaded["assessment_sha256"]))}
    written = 0
    partitions = None
    for game_id in sorted(by_game):
        if only_games is not None and game_id not in only_games:
            continue
        if limit is not None and written >= limit:
            break
        if game_id in existing:
            summary["reused"] += 1
            continue
        models = by_game[game_id]
        point, elo = models.get(POINT_MODEL_ID), models.get(ELO_MODEL_ID)
        skip = {"codex_game_id": game_id}
        if point is None or elo is None:
            summary["skipped"].append({**skip, "reason": "ASSESSMENT_LACKS_A_CHANNEL"})
            continue
        espn = identity.espn_id(game_id)
        raw = rows_by_espn.get(espn)
        if raw is None:
            summary["skipped"].append({**skip, "reason": "NO_RETAINED_SOURCE_ROW"})
            continue
        tipoff = common.ts(raw.get("start_date"))
        if tipoff is None or tipoff.astimezone(EASTERN).date().isoformat() != str(point["game_date"]):
            summary["skipped"].append({**skip, "reason": "SOURCE_DATE_DIFFERS_FROM_ASSESSMENT"})
            continue
        home_pair = pairing["pairs"].get(point["home_team_id"])
        away_pair = pairing["pairs"].get(point["away_team_id"])
        if home_pair != str(raw["home_id"]) or away_pair != str(raw["away_id"]):
            summary["skipped"].append({**skip, "reason": "ORIENTATION_NOT_CONFIRMED_BY_UNIQUE_TEAM_PAIRING"})
            continue
        jrow = joint_rows.get(game_id)
        joint_summary, joint_digest = None, None
        if jrow is None:
            summary["skipped"].append({**skip, "reason": "NO_JOINT_POPULATION"})
            continue
        if (jrow["ridge_expected_home"] != point["expected_home_score"]
                or jrow["ridge_expected_away"] != point["expected_away_score"]
                or jrow["elo_home_win_probability"] != elo["p_home_win"]):
            summary["skipped"].append({**skip, "reason": "JOINT_RECEIPT_DISAGREES_WITH_ASSESSMENT"})
            continue
        if partitions is None:
            partitions = loaded["counts"].partition_by("game_index", as_dict=True)
        try:
            home, away, cnt, joint_digest = _cells(partitions, int(jrow["game_index"]))
        except ValueError as exc:
            summary["skipped"].append({**skip, "reason": f"JOINT_CELLS_MISSING:{exc}"})
            continue
        if joint_digest != str(jrow["joint_counts_int64_le_sha256"]).lower():
            summary["skipped"].append({**skip, "reason": "JOINT_COUNTS_DIGEST_MISMATCH"})
            continue
        try:
            joint_summary = common.with_intervals(common.summarize_counts(home, away, cnt))
        except ValueError as exc:
            summary["skipped"].append({**skip, "reason": f"JOINT_POPULATION_INVALID:{exc}"})
            continue
        try:
            admitted = identity.admit_game(
                session, codex_game_id=game_id, home_franchise_id=point["home_team_id"],
                away_franchise_id=point["away_team_id"], home_code=point["home_team_code"],
                away_code=point["away_team_code"], tipoff_utc=tipoff, season_end=int(point["season_end"]),
                home_espn_team_id=home_pair, away_espn_team_id=away_pair,
                home_name=raw.get("home_display_name"), away_name=raw.get("away_display_name"))
        except identity.IdentityRefused as exc:
            summary["skipped"].append({**skip, "reason": f"ADMISSION_REFUSED:{exc}"[:200]})
            continue
        summary["games_admitted"] += int(admitted["result"] == "ADMITTED")
        p_home = float(elo["p_home_win"])
        rh, ra = float(point["expected_home_score"]), float(point["expected_away_score"])
        session.add(NbaHistoricalForecast(
            evidence_class=EVIDENCE_HISTORICAL, assessment_receipt_sha256=loaded["assessment_sha256"],
            joint_receipt_sha256=loaded["joint_sha256"], predictions_sha256=loaded["predictions_sha256"],
            joint_counts_sha256=loaded["joint_counts_sha256"], joint_counts_int64_le_sha256=joint_digest,
            point_model_id=POINT_MODEL_ID, point_model_sha256=point["model_sha256"],
            elo_model_sha256=elo["model_sha256"], joint_configuration=common.DISTRIBUTION_CONFIGURATION,
            codex_game_id=game_id, source_game_id=espn, home_franchise_id=point["home_team_id"],
            away_franchise_id=point["away_team_id"], home_team_code=point["home_team_code"],
            away_team_code=point["away_team_code"], season_end=int(point["season_end"]),
            game_date=datetime.fromisoformat(str(point["game_date"])).date(),
            canonical_game_id=uuid.UUID(admitted["game_id"]), imported_at=moment,
            ridge_home_points=rh, ridge_away_points=ra, ridge_total_points=rh + ra, ridge_home_margin=rh - ra,
            elo_home_win_probability=p_home, elo_away_win_probability=float(elo["p_away_win"]),
            elo_tie_probability=float(elo["p_tie"]), elo_method=str(elo["winner_probability_source"]),
            joint_draw_count=joint_summary["draw_count"], joint_home_win_probability=joint_summary["winner"]["home"],
            joint_away_win_probability=joint_summary["winner"]["away"],
            joint_home_win_mcse=joint_summary["winner"]["home_mcse"],
            joint_expected_home=joint_summary["expected"]["home"], joint_expected_away=joint_summary["expected"]["away"],
            joint_expected_total=joint_summary["expected"]["total"],
            joint_expected_margin=joint_summary["expected"]["margin"],
            joint_summary={k: joint_summary[k] for k in ("pmfs", "intervals", "winner", "expected")},
            detail={"label": LABEL, "evidence_class": EVIDENCE_HISTORICAL,
                    "qualification": ("Category A reconstruction from finalized records; the models were frozen "
                                      "before 2025-26 scores were read. Never published before the game."),
                    "joint_game_index": int(jrow["game_index"]), "joint_donors": int(jrow["donors"]),
                    "joint_bank_id": jrow["bank_id"], "joint_method_sha256": loaded["joint_method_sha256"],
                    "support_flags": {"both_ordinary_rest_supported": jrow["both_ordinary_rest_supported"],
                                      "either_season_opener": jrow["either_season_opener"],
                                      "early_season": jrow["early_season"]},
                    "source_tipoff_utc": tipoff.isoformat(), "source_espn_team_ids":
                        {"home": str(raw["home_id"]), "away": str(raw["away_id"])}}))
        written += 1
        summary["imported"] += 1
    session.flush()
    return summary


__all__ = ["ASSESSMENT_SCHEMA", "JOINT_SCHEMA", "LABEL", "HistoricalRefused", "import_historical", "load"]
