"""Reconcile the V18 evidence export against the stored records it came from.

Read-only. Re-derives the population independently of the exporter, re-hashes
every artifact, and asserts the invariants the export claims: no sealed-window
date, no permanently excluded game, one primary forecast per game, and no
outcome payload present for a game that did not pass the eligible-final gate.

Exit code is 0 when every check passes and 1 when any check fails.

Usage:
    python server/scripts/v18_export_reconcile.py [--dir DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]
SEALED_START = date(2026, 8, 14)
SEALED_END = date(2026, 8, 27)
DATE_FLOOR = date(2026, 8, 28)
PERMANENT_EXCLUSIONS = {"823343", "824235", "822693"}

POPULATION_SQL = """
select sf.source_event_id, sf.canonical_game_id, sf.event_date, count(*) as versions
from shadow_forecasts sf
join shadow_forecast_bundles b on b.id = sf.bundle_id
where b.sport = 'MLB'
  and sf.engine_id = 'MLB_V18_WHOLE_GAME'
  and sf.event_date >= date '2026-08-28'
  and not (sf.event_date between date '2026-08-14' and date '2026-08-27')
  and sf.source_event_id not in ('823343', '824235', '822693')
group by 1, 2, 3
"""

# The same gate the scorer and the exporter use: a FINAL state observation
# carrying both scores. NOT game_final_outcomes, which is the 2025 training
# label store and holds no row for this population.
FINALS_SQL = """
select count(distinct o.game_id) as eligible_finals
from game_state_observations o
where o.game_id = any(:game_ids)
  and o.status = 'final'
  and o.home_score is not null
  and o.away_score is not null
"""

SCORES_SQL = """
select count(distinct e.canonical_game_id) as scored_games
from research_forecast_evaluations e
where e.canonical_game_id = any(:game_ids)
  and e.is_current is true
"""

DUPLICATE_SQL = """
select count(*) as duplicated
from (
    select canonical_game_id, forecast_identity, count(*) as current_rows
    from research_forecast_evaluations
    where canonical_game_id = any(:game_ids) and is_current is true
    group by canonical_game_id, forecast_identity
    having count(*) > 1
) offenders
"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="reconcile the V18 evidence export")
    parser.add_argument(
        "--dir",
        default=str(REPO_ROOT / "outputs" / "v18-error-analysis-evidence"),
        help="export directory to reconcile",
    )
    args = parser.parse_args()
    out_dir = Path(args.dir)

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (out_dir / manifest["file"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    load_dotenv(REPO_ROOT / ".env")
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        result = conn.execute(text(POPULATION_SQL))
        cols = list(result.keys())
        db_rows = [dict(zip(cols, row)) for row in result.fetchall()]
        game_ids = [str(r["canonical_game_id"]) for r in db_rows if r["canonical_game_id"]]
        eligible_finals = conn.execute(
            text(FINALS_SQL), {"game_ids": game_ids}
        ).scalar_one() if game_ids else 0
        scored_games = conn.execute(
            text(SCORES_SQL), {"game_ids": game_ids}
        ).scalar_one() if game_ids else 0
        duplicated = conn.execute(
            text(DUPLICATE_SQL), {"game_ids": game_ids}
        ).scalar_one() if game_ids else 0

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    # 1. artifact integrity
    for file_key, sha_key in (
        ("file", "sha256"),
        ("omissionsFile", "omissionsSha256"),
        ("lineageBlobsFile", "lineageBlobsSha256"),
    ):
        name = manifest.get(file_key)
        if not name:
            continue
        actual = _sha256_file(out_dir / name)
        check(f"sha256:{name}", actual == manifest[sha_key],
              f"manifest={manifest[sha_key][:16]}... actual={actual[:16]}...")

    # 2. population size matches an independently derived query
    db_games = {str(r["canonical_game_id"]) for r in db_rows if r["canonical_game_id"]}
    export_games = {r["identity"]["canonicalGameId"] for r in records}
    check("distinct games match the database", db_games == export_games,
          f"database={len(db_games)} export={len(export_games)} "
          f"symmetric_difference={len(db_games ^ export_games)}")
    check("manifest count matches record count",
          manifest["counts"]["distinctGames"] == len(records),
          f"manifest={manifest['counts']['distinctGames']} records={len(records)}")

    # 3. one primary per game, alternates account for every other version
    db_versions = sum(int(r["versions"]) for r in db_rows)
    export_versions = len(records) + sum(len(r["alternates"]) for r in records)
    check("primary + alternates account for every stored version",
          db_versions == export_versions,
          f"database={db_versions} export={export_versions}")
    check("one record per canonical game", len(export_games) == len(records),
          f"games={len(export_games)} records={len(records)}")

    # 4. exclusions and the sealed window never appear
    pks = {r["identity"]["mlbGamePk"] for r in records}
    check("no permanently excluded game present",
          not (pks & PERMANENT_EXCLUSIONS),
          f"intersection={sorted(pks & PERMANENT_EXCLUSIONS)}")
    dates = [date.fromisoformat(r["identity"]["eventDate"]) for r in records]
    check("no sealed-window event date present",
          not any(SEALED_START <= d <= SEALED_END for d in dates),
          f"earliest={min(dates).isoformat()} latest={max(dates).isoformat()}")
    check("every event date is at or after the declared floor",
          all(d >= DATE_FLOOR for d in dates),
          f"floor={DATE_FLOOR.isoformat()} earliest={min(dates).isoformat()}")

    # 5. the outcome gate held: no score present without an eligible final
    leaked = [
        r["identity"]["mlbGamePk"] for r in records
        if not r["outcome"]["eligible"] and any(
            r["outcome"].get(k) is not None
            for k in ("homeScore", "awayScore", "homeWin", "runDifferential", "totalRuns")
        )
    ]
    check("no score payload on a game without an eligible final", not leaked,
          f"leaked={leaked}")
    check("eligible-final count matches the database",
          manifest["counts"]["gamesWithEligibleFinal"] == eligible_finals,
          f"manifest={manifest['counts']['gamesWithEligibleFinal']} "
          f"database={eligible_finals}")

    # 5b. research scores reconcile to the stored evaluations, one current row
    # per forecast identity -- the duplicate-free guarantee, checked in the
    # database rather than inferred from the job's own summary.
    check("research-score count matches the database",
          manifest["counts"]["gamesWithResearchScore"] == scored_games,
          f"manifest={manifest['counts']['gamesWithResearchScore']} "
          f"database={scored_games}")
    check("no forecast identity has two current evaluations", duplicated == 0,
          f"duplicated={duplicated}")
    cohort_total = sum(manifest["counts"]["cohorts"].values())
    check("cohorts partition the population", cohort_total == len(records),
          f"cohorts={manifest['counts']['cohorts']} total={cohort_total} "
          f"records={len(records)}")
    scored_cohorts = (manifest["counts"]["cohorts"]["VERIFIED_PROSPECTIVE"]
                      + manifest["counts"]["cohorts"]["QUALIFIED_DIAGNOSTIC"])
    check("verified plus qualified equals the scored games",
          scored_cohorts == manifest["counts"]["gamesWithResearchScore"],
          f"verified+qualified={scored_cohorts} "
          f"scored={manifest['counts']['gamesWithResearchScore']}")
    unscored_with_score = [
        r["identity"]["mlbGamePk"] for r in records
        if r["cohort"] == "NOT_YET_SCORED" and r["researchScore"]["scored"]
    ]
    check("no NOT_YET_SCORED record carries a score", not unscored_with_score,
          f"offenders={unscored_with_score}")

    # 6. every record carries the fields the schema promises
    required = ("identity", "forecast", "clocks", "eligibility", "predictions",
                "publisherReadiness", "aispPregameEvidence", "outcome",
                "researchScore", "cohort",
                "decisions", "positions", "alternates", "lineage")
    missing_fields = sorted({
        key for r in records for key in required if key not in r
    })
    check("every record carries the promised top-level fields", not missing_fields,
          f"missing={missing_fields}")

    # 7. both eligibility flags are present and independent
    both_present = all(
        "scientificProspective" in r["eligibility"]
        and "operationalDecision" in r["eligibility"]
        for r in records
    )
    check("both eligibility flags retained on every record", both_present, "")

    failures = [c for c in checks if not c["pass"]]
    report = {
        "reconciledAtUtc": manifest["exportedAtUtc"],
        "directory": str(out_dir),
        "checks": checks,
        "passed": len(checks) - len(failures),
        "failed": len(failures),
    }
    (out_dir / "reconciliation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    for entry in checks:
        print(f"[{'PASS' if entry['pass'] else 'FAIL'}] {entry['check']}"
              + (f"  -- {entry['detail']}" if entry["detail"] else ""))
    print(f"\n{len(checks) - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
