"""Bounded, read-only prediction-market capture monitor.

Samples the live backend (/healthz, /readyz), the 8010 listener count and RSS,
PostgreSQL connection count, prediction-market scheduler runs and per-game /
per-provider snapshot milestones for a target slate, at a fixed cadence, until
an explicit expiry, then exits. It performs NO provider calls and NO writes
except its own JSON report artifact. Reusable operational tool:

    python scripts/pm_capture_monitor.py --date 2026-09-05 --until 2026-09-05T08:00:00-04:00 \
        --interval-minutes 5 --out C:/path/report.json

Reads the primary checkout's .env for DATABASE_URL (never printed).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

CUTS = ["FIRST_OBSERVED", "T_MINUS_6_HOURS", "T_MINUS_2_HOURS", "T_MINUS_30_MINUTES", "T_MINUS_10_MINUTES", "T_MINUS_5_MINUTES"]


def http(url: str) -> tuple[int | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")[:200]
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__


def listener_and_rss(port: int) -> dict:
    ps = (
        f"$l=@(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue);"
        "$out=@{listeners=$l.Count; rss_mb=@()};"
        "foreach($x in $l){$p=Get-CimInstance Win32_Process -Filter \"ProcessId=$($x.OwningProcess)\";"
        "$out.rss_mb+=[math]::Round($p.WorkingSetSize/1MB)}; $out | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60).stdout.strip()
        return json.loads(out) if out else {"listeners": None, "rss_mb": []}
    except Exception as exc:  # noqa: BLE001
        return {"listeners": None, "rss_mb": [], "error": type(exc).__name__}


def sample(conn, slate_date: str) -> dict:
    q = lambda sql, **p: conn.execute(text(sql), p).fetchall()  # noqa: E731
    games = q(
        """select g.id, g.starts_at, g.status::text,
                  string_agg(t.abbreviation || case when gt.is_home then '(H)' else '(A)' end, ' ' order by gt.is_home)
           from games g join leagues l on l.id=g.league_id join game_teams gt on gt.game_id=g.id join teams t on t.id=gt.team_id
           where l.code='MLB' and (g.starts_at at time zone 'America/New_York')::date = :d
           group by g.id, g.starts_at, g.status order by g.starts_at""", d=slate_date)
    per_game = []
    for gid, starts_at, status, teams in games:
        row = {"game_id": str(gid), "starts_at": starts_at.isoformat(), "status": status, "teams": teams, "providers": {}}
        for provider in ("KALSHI", "POLYMARKET"):
            m = q("""select mapping_status, semantics_status from prediction_market_mappings
                     where provider=:p and canonical_game_id=:g order by decided_at desc limit 1""", p=provider, g=gid)
            cuts = q("""select timeline_cut, count(*), min(provider_observed_at), max(provider_observed_at)
                        from prediction_market_snapshots where provider=:p and canonical_game_id=:g
                          and market_type='FULL_GAME_MONEYLINE' and pregame_eligible and midpoint is not null
                        group by timeline_cut""", p=provider, g=gid)
            latest = q("""select provider_observed_at, in_play, pregame_eligible, midpoint from prediction_market_snapshots
                          where provider=:p and canonical_game_id=:g and market_type='FULL_GAME_MONEYLINE'
                          order by provider_observed_at desc limit 1""", p=provider, g=gid)
            row["providers"][provider] = {
                "mapping": m[0][0] if m else None, "semantics": m[0][1] if m else None,
                "pregame_by_cut": {c or "UNCUT": {"n": n, "first": a.isoformat(), "last": b.isoformat()} for c, n, a, b in cuts},
                "latest": {"observed_at": latest[0][0].isoformat(), "in_play": latest[0][1], "pregame": latest[0][2], "midpoint": latest[0][3]} if latest else None,
            }
        per_game.append(row)
    runs = q("""select source, job_name, status::text, started_at, extract(epoch from (completed_at-started_at)),
                       records_processed, source_metadata->>'requests', source_metadata->>'errors'
                from ingestion_runs where source in ('KALSHI','POLYMARKET','PREDICTION_MARKET','MLB_STATS_API')
                  and started_at > now() - interval '15 minutes' order by started_at desc""")
    counts = q("""select (select count(*) from prediction_market_snapshots), (select count(*) from prediction_market_contracts),
                         (select count(*) from prediction_market_mappings), (select count(*) from games),
                         (select count(*) from shadow_experiment_decisions), (select count(*) from official_asp_positions),
                         (select count(*) from position_results), (select count(*) from forecasts),
                         (select count(*) from pg_stat_activity where datname='asp')""")[0]
    return {
        "games": len(games), "per_game": per_game,
        "recent_runs": [{"source": s, "job": j, "status": st, "started_at": at.isoformat(), "seconds": float(sec) if sec is not None else None,
                         "records": rec, "requests": rq, "errors": er} for s, j, st, at, sec, rec, rq, er in runs],
        "counts": {"pm_snapshots": counts[0], "pm_contracts": counts[1], "pm_mappings": counts[2], "games": counts[3],
                   "decisions": counts[4], "positions": counts[5], "settlements": counts[6], "forecasts": counts[7], "pg_connections": counts[8]},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--until", required=True, help="ISO datetime with offset; the monitor exits at this time")
    ap.add_argument("--interval-minutes", type=float, default=5.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--env", default=r"C:\Users\james\ai-sports-predictor\.env")
    ap.add_argument("--backend", default="http://127.0.0.1:8010")
    args = ap.parse_args()
    load_dotenv(args.env)
    engine = create_engine(os.environ["DATABASE_URL"])
    until = datetime.fromisoformat(args.until)
    out = Path(args.out)
    report = {"tool": "pm_capture_monitor", "date": args.date, "until": until.isoformat(), "interval_minutes": args.interval_minutes,
              "started_at": datetime.now(UTC).isoformat(), "samples": []}
    while True:
        now = datetime.now(UTC)
        if now >= until:
            break
        s = {"at": now.isoformat(), "healthz": http(f"{args.backend}/healthz")[0], "readyz": http(f"{args.backend}/readyz"), "port_8010": listener_and_rss(8010)}
        try:
            with engine.connect() as conn:
                s.update(sample(conn, args.date))
        except Exception as exc:  # noqa: BLE001
            s["db_error"] = type(exc).__name__
        report["samples"].append(s)
        report["last_sample_at"] = s["at"]
        out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        sys.stdout.write(f"{s['at']} games={s.get('games')} snapshots={s.get('counts', {}).get('pm_snapshots')} ready={s['readyz'][0]}\n")
        sys.stdout.flush()
        remaining = (until - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            break
        time.sleep(min(args.interval_minutes * 60, remaining))
    report["finished_at"] = datetime.now(UTC).isoformat()
    out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
