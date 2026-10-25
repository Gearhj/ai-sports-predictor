"""Generate the Monday Morning QB weekly report (read-only).

    python server/scripts/mmqb_report.py --league NFL --season 2026 --week 3 \
        --out C:\\Users\\james\\aisp-analysis\\mmqb-nfl-2026-week03

    python server/scripts/mmqb_report.py --league NFL --date-from 2026-09-24 --date-to 2026-09-29 --out <dir>

Every PostgreSQL connection this process opens is forced to default_transaction_read_only=on, and the
report runs in ONE ``REPEATABLE READ READ ONLY`` transaction with a statement timeout, then rolls back.
It triggers no job, calls no provider and writes nothing to the database. Files written: report.html,
BRIEF.md, wagers.csv, wagers.json, show_segment.md and payload.json in ``--out``.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--league", default="NFL")
    parser.add_argument("--season", type=int)
    parser.add_argument("--week", type=int)
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--out", required=True)
    parser.add_argument("--env", default=str(SERVER_ROOT.parent / ".env"),
                        help="dotenv file with the database settings (values are never printed)")
    parser.add_argument("--link-base", default="http://127.0.0.1:8010",
                        help="base URL for game links in the standalone HTML")
    parser.add_argument("--statement-timeout", default="60s")
    parser.add_argument("--game-evidence", help="retained game-evidence JSON (server/scripts/mmqb_game_evidence.py)")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(args.env, override=False)
    os.environ["PGOPTIONS"] = (os.environ.get("PGOPTIONS", "") + " -c default_transaction_read_only=on").strip()

    from sqlalchemy import text

    from app.db.session import SessionLocal
    from app.services.mmqb_render import render_brief, render_csv, render_html, render_show, render_wagers_json
    from app.services.mmqb_report import mmqb_report

    started = time.time()
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        session.execute(text(f"SET LOCAL statement_timeout = '{args.statement_timeout}'"))
        payload = mmqb_report(session, league=args.league, season=args.season, week=args.week,
                              date_from=args.date_from, date_to=args.date_to, game_evidence_path=args.game_evidence)
        session.rollback()
    elapsed = time.time() - started
    if payload["snapshot"].get("read_only") != "on":
        raise SystemExit("refusing to write: the snapshot was not proved read-only")
    if not payload["data_cutoff"].get("db_clock_utc") or not payload["snapshot"].get("pg_snapshot"):
        raise SystemExit("refusing to write: the report has no data cutoff or snapshot identity")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    import json

    files = {
        "report.html": render_html(payload, link_base=args.link_base, link_target="_blank"),
        "BRIEF.md": render_brief(payload, report_dir=str(out)),
        "wagers.csv": render_csv(payload),
        "wagers.json": render_wagers_json(payload),
        "show_segment.md": render_show(payload, report_path=str(out / "report.html")),
        "payload.json": json.dumps(payload, indent=1, default=str),
    }
    for name, body in files.items():
        (out / name).write_text(body, encoding="utf-8", newline="\n")
    s = payload["financial"]["summary"]
    print(f"{payload['title']} | cutoff {payload['data_cutoff']['db_clock_utc']} | "
          f"{s['wins']}-{s['losses']}-{s['pushes']} net {s['net_profit']:+.2f} | open {s['open_count']} | "
          f"sha256 {payload['snapshot']['content_sha256'][:16]} | {elapsed:.1f}s")
    for name in files:
        print(out / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
