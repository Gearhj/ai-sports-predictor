"""Extract retained, finalized NFL game evidence for the Monday Morning QB report (read-only, local files only).

    <python with pandas+pyarrow> server/scripts/mmqb_game_evidence.py \
        --pbp-vintage C:\\Users\\james\\asp-model-studio\\outputs\\nfl-forward-vintages-v1\\vintages\\play_by_play\\sha256\\<id>\\vintage.json \
        --qb-bundle <SHADOW_BUNDLE_CANDIDATE.json or *_CAPTURE_CLOSEOUT_V1 json> [--qb-bundle ...] \
        --game 2026_03_SEA_WAS --game 2026_03_LV_NO --game 2026_03_BAL_DAL \
        --out C:\\Users\\james\\aisp-analysis\\mmqb-nfl-2026-week03\\game_evidence.json

It opens files that are ALREADY retained on this machine. It never downloads, never calls a provider and
never writes to a database. The AISP server venv has no pandas, so run it with an interpreter that has
pandas and pyarrow (for example the Model Studio venv). Everything is copied exactly as recorded: the
scoring sequence, turnovers and passers come from the retained nflverse play-by-play rows, with the source
path, sha256 (recomputed here), retrieval and observation clocks, and row counts. The QB inputs come from
the retained research-shadow capture bundles, which are NOT inputs of the M1 FINAL forecast.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _clock(sec: float | None, qtr: int | None) -> str | None:
    if sec is None or qtr is None:
        return None
    rem = int(sec)
    return f"Q{qtr} {rem // 60}:{rem % 60:02d}"


def _num(v):
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def game_rows(df, game: str) -> dict:
    x = df[df["game_id"] == game].reset_index(drop=True)
    if x.empty:
        return {"game_id": game, "rows": 0, "retained": False}
    home, away = x["home_team"].iloc[0], x["away_team"].iloc[0]
    last = x.iloc[-1]
    scoring, turnovers = [], []
    prev_h = prev_a = 0.0
    for _, r in x.iterrows():
        h, a = _num(r.get("total_home_score")), _num(r.get("total_away_score"))
        if h is None or a is None:
            continue
        if (h, a) != (prev_h, prev_a):
            gained_home = h - prev_h
            team = home if gained_home > 0 else away
            pts = int(gained_home if gained_home > 0 else a - prev_a)
            kind = ("TD" if r.get("touchdown") == 1 else "FG" if r.get("field_goal_result") == "made" else
                    "SAFETY" if r.get("safety") == 1 else "XP" if r.get("extra_point_result") == "good" else
                    "2PT" if r.get("two_point_conv_result") == "success" else "OTHER")
            scoring.append({"clock": _clock(_num(r.get("quarter_seconds_remaining")), int(r["qtr"]) if _num(r.get("qtr")) else None),
                            "team": team, "points": pts, "kind": kind,
                            "away": int(a), "home": int(h), "play_id": _num(r.get("play_id")),
                            "player_as_recorded": (r.get("td_player_name") if kind == "TD" else
                                                   r.get("kicker_player_name") if kind in ("FG", "XP") else None),
                            "kick_distance_yards": _num(r.get("kick_distance")) if kind == "FG" else None})
            prev_h, prev_a = h, a
        if r.get("interception") == 1 or r.get("fumble_lost") == 1:
            turnovers.append({"clock": _clock(_num(r.get("quarter_seconds_remaining")), int(r["qtr"]) if _num(r.get("qtr")) else None),
                              "lost_by": r.get("posteam"),
                              "kind": "INTERCEPTION" if r.get("interception") == 1 else "FUMBLE_LOST",
                              "play_id": _num(r.get("play_id"))})
    passers: dict[str, dict] = {}
    pa = x[(x.get("pass_attempt") == 1) & x["passer_player_id"].notna()]
    for _, r in pa.iterrows():
        key = f"{r['posteam']}|{r['passer_player_id']}"
        item = passers.setdefault(key, {"team": r["posteam"], "gsis_id": r["passer_player_id"],
                                        "name_as_recorded": r.get("passer_player_name"), "pass_attempts": 0})
        item["pass_attempts"] += 1
    return {
        "game_id": game, "retained": True, "rows": int(len(x)), "home": home, "away": away,
        "final_as_recorded": {"away": int(_num(last["total_away_score"]) or 0), "home": int(_num(last["total_home_score"]) or 0)},
        "last_row_desc": str(last.get("desc")), "ends_with_end_game": str(last.get("desc")).strip().upper() == "END GAME",
        "max_quarter": int(_num(x["qtr"].max()) or 0),
        "scoring_sequence": scoring, "turnovers": turnovers,
        "turnover_counts": {t: sum(1 for v in turnovers if v["lost_by"] == t) for t in (away, home)},
        "passers": sorted(passers.values(), key=lambda p: (p["team"], -p["pass_attempts"])),
    }


def qb_inputs(bundles: list[Path], games: set[str]) -> dict:
    out: dict[str, dict] = {}
    for path in bundles:
        doc = json.loads(path.read_text(encoding="utf-8"))
        meta = {"path": str(path), "sha256": _sha(path), "schema": doc.get("schemaVersion") or doc.get("schema"),
                "forecast_cutoff_utc": doc.get("forecastCutoffUtc"), "admission_granted": doc.get("admissionGranted"),
                "evidence_class": doc.get("evidenceClass"), "funding_eligible": doc.get("fundingEligible"),
                "m1_production_probability_changed": doc.get("m1ProductionProbabilityChanged")}
        if "bridgeGame" in doc:
            g = doc["bridgeGame"]
            sides = []
            for side, key in (("away", "awayQb"), ("home", "homeQb")):
                q = g.get(key) or {}
                usage = q.get("priorCompletedGameUsage") or {}
                perf = q.get("priorPerformance") or {}
                sides.append({"side": side, "team": q.get("teamId"),
                              "depth_rank_one_gsis_id": (q.get("depthRankOne") or {}).get("playerGsisId"),
                              "depth_meaning": (q.get("depthRankOne") or {}).get("meaning"),
                              "prior_dominant_gsis_id": usage.get("dominantQbGsisId"),
                              "prior_dominant_share": usage.get("dominantShare"), "prior_game": usage.get("gameId"),
                              "change_signal": q.get("changeSignal"),
                              "confirmed_starter_status": q.get("confirmedStarterStatus"),
                              "reported_availability_status": q.get("reportedAvailabilityStatus"),
                              "depth_proxy_prior_pass_attempts": perf.get("priorPassAttempts"),
                              "roster_years_exp": (q.get("rosterExperience") or {}).get("yearsExp"),
                              "source_observed_at_utc": (q.get("depthRankOne") or {}).get("sourceObservedAtUtc")})
            if g.get("eventId") in games:
                out[g["eventId"]] = meta | {"sides": sides}
        for s in doc.get("sides") or []:
            if s.get("eventId") not in games:
                continue
            entry = out.setdefault(s["eventId"], meta | {"sides": []})
            entry["sides"].append({"side": s.get("side"), "team": s.get("teamId"),
                                   "depth_rank_one_gsis_id": s.get("depthRankOneGsisId"),
                                   "depth_meaning": "DEPTH_RANK_ONE_EXPECTATION_PROXY_NOT_CONFIRMED_STARTER",
                                   "prior_dominant_gsis_id": s.get("priorDominantGsisId"), "prior_game": s.get("priorCompletedUsageGameId"),
                                   "change_signal": s.get("changeSignal"),
                                   "confirmed_starter_status": "NOT_ESTABLISHED" if s.get("confirmedStarter") is None else "CONFIRMED",
                                   "reported_availability_status": "NO_QUALIFIED_SOURCE" if s.get("reportedAvailability") is None else "REPORTED",
                                   "depth_proxy_prior_pass_attempts": s.get("priorPassAttempts"),
                                   "roster_years_exp": s.get("rosterYearsExpNotPriorStarts"),
                                   "source_observed_at_utc": s.get("depthSourceLoadedAtUtc")})
    return out


def content_sha256(doc: dict) -> str:
    """sha256 of the canonical JSON of the document without its own content_sha256 field."""

    body = {k: v for k, v in doc.items() if k != "content_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pbp-vintage", required=True)
    ap.add_argument("--qb-bundle", action="append", default=[])
    ap.add_argument("--game", action="append", required=True)
    ap.add_argument("--searched", action="append", default=[], help="SOURCE|what it contained (recorded verbatim)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--league", default="NFL")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--manifest", help="also write/refresh this manifest (vendored artifacts)")
    a = ap.parse_args()
    import pandas as pd

    vintage_path = Path(a.pbp_vintage)
    vintage = json.loads(vintage_path.read_text(encoding="utf-8"))
    payload = Path(vintage["payload"]["path"])
    digest = _sha(payload)
    df = pd.read_parquet(payload)
    games = list(dict.fromkeys(a.game))
    doc = {
        "schema": "AISP_MMQB_GAME_EVIDENCE_V1",
        "league": a.league, "season": a.season, "week": a.week,
        "extracted_at_utc": datetime.now(UTC).isoformat(),
        "play_by_play": {
            "vintage_path": str(vintage_path), "payload_path": str(payload), "payload_sha256": digest,
            "sha256_matches_vintage": digest == vintage["payload"]["sha256"],
            "source_url_as_recorded": (vintage.get("sourceIdentity") or {}).get("sourceUrl"),
            "source_retrieved_at_utc": vintage.get("sourceRetrievedAtUtc"),
            "archive_observed_at_utc": vintage.get("observedAtUtc"),
            "asset_updated_at_utc": vintage.get("assetUpdatedAtUtc"),
            "evidence_class": vintage.get("evidenceClass"), "row_count_total": int(len(df)),
            "row_count_vintage": vintage.get("rowCount"),
            "games_by_week": {str(int(k)): int(v) for k, v in df.groupby("week")["game_id"].nunique().items()},
            "retrieved_by_this_script": False,
        },
        "games": {g: game_rows(df, g) for g in games},
        "qb_inputs": qb_inputs([Path(p) for p in a.qb_bundle], set(games)),
        "searched": [dict(zip(("source", "finding"), s.split("|", 1))) for s in a.searched],
    }
    doc = json.loads(json.dumps(doc, default=str))
    doc["content_sha256"] = content_sha256(doc)
    body = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body.encode("utf-8"))
    if a.manifest:
        mpath = Path(a.manifest)
        manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.is_file() else {
            "schema": "AISP_MMQB_GAME_EVIDENCE_MANIFEST_V1", "artifacts": {}}
        pbp = doc["play_by_play"]
        manifest["artifacts"][f"{a.league}|{a.season}|{a.week}"] = {
            "file": out.name, "file_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
            "content_sha256": doc["content_sha256"], "bytes": out.stat().st_size, "games": games,
            "source_vintage": {"object_sha256": pbp["payload_sha256"], "retrieved_at_utc": pbp["source_retrieved_at_utc"],
                               "archive_observed_at_utc": pbp["archive_observed_at_utc"],
                               "row_count": pbp["row_count_total"], "source_url_as_recorded": pbp["source_url_as_recorded"]},
            "extracted_at_utc": doc["extracted_at_utc"]}
        mpath.write_bytes((json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    print(a.out, digest[:16], {g: doc["games"][g]["rows"] for g in games})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
