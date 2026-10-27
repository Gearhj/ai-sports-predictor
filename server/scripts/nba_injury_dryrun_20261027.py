"""Read-only real-data dry run of the NBA star-out gate (installed vs Codex-patched) and Codex's producer workload
features, for tonight's NBA games, on AISP's retained ESPN inputs. Writes one JSON receipt; touches no DB, job or
wagering setting. Usage: python scripts/nba_injury_dryrun_20261027.py <out.json>"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

PROD = Path(r"C:\Users\james\ai-sports-predictor")
BRANCH = Path(__file__).resolve().parents[2]
ROOT = PROD / ".runtime" / "nba-availability"
PACKET = Path(r"C:\Users\james\asp-model-exchange\injury-availability-evaluation-20261027-v1")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def held(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()


def main(out_path: str) -> None:
    sys.path.insert(0, str(PROD / "server"))
    installed_path = PROD / "server" / "app" / "nba" / "availability.py"
    patched_path = BRANCH / "server" / "app" / "nba" / "availability.py"
    producer_path = PACKET / "source" / "server" / "sports_nba_gen1" / "injury.py"
    installed = load("av_installed", installed_path)
    patched = load("av_patched", patched_path)
    producer = load("codex_injury", producer_path)
    settings = SimpleNamespace(nba_availability_root=str(ROOT), nba_availability_collector_enabled=True)
    now = datetime.now(UTC)
    board = json.load(urllib.request.urlopen(f"http://127.0.0.1:8010/api/v1/availability?date={now.astimezone().date().isoformat()}", timeout=60))
    games = [g for g in board["games"] if g["league"] == "NBA"]

    boxes: dict[str, dict] = {}
    for p in (ROOT / "box").glob("*.json"):
        doc = json.loads(p.read_bytes())
        doc["held_at_utc"] = held(p)   # file write time = when AISP first stored the completed box
        boxes[str(doc.get("espn_event_id"))] = doc

    rows = []
    for g in games:
        eid = g["espn_event_id"]
        snaps = []
        for p in sorted((ROOT / "snapshots" / eid).glob("*.json")):
            d = json.loads(p.read_bytes())
            d["source_sha256"] = sha(p)
            snaps.append(d)
        latest = snaps[-1] if snaps else {}
        teams = {side: {"espn_team_id": (latest.get("teams") or {}).get(side, {}).get("espn_team_id")} for side in ("home", "away")}
        schedules = {}
        for side in ("home", "away"):
            tid = teams[side]["espn_team_id"]
            files = sorted((ROOT / "schedules" / str(tid)).glob("*.json"))
            if files:
                doc = json.loads(files[-1].read_bytes())
                doc["held_at_utc"] = held(files[-1])
                schedules[str(tid)] = doc
        game = {"game_id": eid, "espn_game_id": eid, "espn_event_id": eid, "starts_at": g["starts_at"].replace("Z", "+00:00"), "teams": teams}
        game_gate = {**game, "starts_at": datetime.fromisoformat(game["starts_at"])}
        a = installed.assess(game_gate, now, settings)
        b = patched.assess(game_gate, now, settings)
        try:
            w = producer.assess_supplied_game(game, schedules, boxes, snaps, as_of=now.isoformat())
        except Exception as exc:  # report, never hide
            w = {"error": f"{type(exc).__name__}: {exc}"}
        rows.append({
            "game": g["name"], "espn_event_id": eid, "starts_at_utc": g["starts_at"],
            "source_clocks": {"snapshots_held": len(snaps), "latest_snapshot_observed_at_utc": latest.get("observed_at_utc"),
                              "latest_snapshot_sha256": latest.get("source_sha256"),
                              "role_freeze_utc": (datetime.fromisoformat(game["starts_at"]) - patched.ROLE_FREEZE).isoformat(),
                              "schedules_held_at_utc": {k: v.get("held_at_utc") for k, v in schedules.items()},
                              "box_held_at_basis": "file write time of each retained completed box (AISP first store)"},
            "completeness": {"injury_section_present": latest.get("injury_section_present"),
                             "team_lists_present": {side: str(teams[side]["espn_team_id"]) in (latest.get("injuries") or {}) for side in ("home", "away")}},
            "gate_installed": {"state": a.get("state"), "reason": a.get("reason"),
                               "stars": {s: [(x["name"], x["status"]) for x in (a.get("teams") or {}).get(s, {}).get("stars", [])] for s in ("away", "home")}},
            "gate_patched": {"state": b.get("state"), "reason": b.get("reason"),
                             "stars": {s: [(x["name"], x["status"]) for x in (b.get("teams") or {}).get(s, {}).get("stars", [])] for s in ("away", "home")}},
            "codex_producer_features": w,
        })
    receipt = {
        "schema": "CLAUDE_NBA_INJURY_DRYRUN_V1", "ran_at_utc": now.isoformat(), "mode": "READ_ONLY_DRY_RUN (no DB, job, wagering or funding change)",
        "code_identity": {"installed_availability_sha256": sha(installed_path), "patched_availability_sha256": sha(patched_path),
                          "branch": "claude/nba-injury-codex-20261027", "codex_producer_injury_py_sha256": sha(producer_path)},
        "games": rows,
    }
    Path(out_path).write_text(json.dumps(receipt, indent=1, default=str), encoding="utf-8")
    for r in rows:
        print(r["game"], "| installed:", r["gate_installed"]["state"], r["gate_installed"]["reason"], "| patched:", r["gate_patched"]["state"], r["gate_patched"]["reason"],
              "| lists", r["completeness"]["team_lists_present"], "| producer:", (json.dumps(r["codex_producer_features"])[:160]))


if __name__ == "__main__":
    main(sys.argv[1])
