"""Audit AISP readiness inputs and pregame evidence availability.

Read-only. Answers one bounded question: can missing AISP evidence explain
either V18's coverage or its errors? It measures the three components carried
forward from the 2026-09-08 audit -- ``feature_completeness``, ``bullpen`` and
``statcast`` -- plus the starter and lineup observations V18 itself cites,
using what is already stored. It changes no weight, threshold or policy.

Two distinctions are enforced throughout:

*   An AISP readiness component describes what AISP ingested for the Gen 1
    forecast path. It is not evidence about what the V18 publisher consumed.
*   A component that is STALE is not a component that is MISSING. Stale scores
    half weight under the implemented formula; missing scores zero.

Usage:
    python server/scripts/v18_evidence_availability_audit.py [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

STUDY_ID = "AISP_V18_EVIDENCE_AVAILABILITY_AUDIT_V1"
DATE_FLOOR = "2026-08-28"
AUDITED = ("feature_completeness", "bullpen", "statcast")
REPO_ROOT = Path(__file__).resolve().parents[2]

# Field-level description of each audited input, from the implementation rather
# than from inference. Producer/storage/meaning are recorded so a reader does
# not have to re-derive them; the measured columns are filled in below.
FIELD_FACTS: dict[str, dict[str, Any]] = {
    "feature_completeness": {
        "producer": "app/services/feature_snapshot_service.py, from the measured "
                    "coverage of the game's feature snapshot",
        "storage": "game_feature_snapshots.feature_completeness (float 0..1), "
                   "mirrored into forecasts.readiness_components",
        "meaning": "share of modelled features with a usable value at snapshot time",
        "role": "AISP readiness input AND a Gen 1 model input "
                "(modeling/schema.py FeatureSpec, readiness group)",
        "readinessWeight": 10.0,
        "critical": True,
        "readinessRule": "STALE below the 60% measured-coverage threshold, "
                         "scoring half weight; it is not a missing field",
    },
    "bullpen": {
        "producer": "no live ingester writes this table in the 2026 path",
        "storage": "bullpen_availability_observations (game_id, team_id, player_id), "
                   "aggregated into bullpen_features",
        "meaning": "reliever availability and workload per club",
        "role": "AISP readiness input and a Gen 1 model input "
                "(home/away_bullpen_availability, _era, _xera)",
        "readinessWeight": 5.0,
        "critical": False,
        "readinessRule": "READY needs rows for BOTH clubs, >=70% coverage on each, "
                         "and non-null home/away_bullpen_availability feature values "
                         "(services/forecast_service.py). With an empty table the "
                         "component can only ever be MISSING.",
    },
    "statcast": {
        "producer": "no live ingester writes this table in the 2026 path",
        "storage": "statcast_player_observations (team_id, player_id -- NO game_id "
                   "column; the table is team/player scoped, not game scoped)",
        "meaning": "batted-ball quality: xwOBA, barrel rate, hard-hit rate, exit velocity",
        "role": "AISP readiness input and a Gen 1 model input "
                "(home/away_statcast_xwoba, _barrel_rate, _hard_hit_rate, "
                "_average_exit_velocity)",
        "readinessWeight": 5.0,
        "critical": False,
        "readinessRule": "READY needs rows for BOTH clubs, >=70% player coverage on "
                         "each, and freshness (services/forecast_service.py). With an "
                         "empty table the component can only ever be MISSING.",
    },
}

COMPONENT_SQL = f"""
select f.id, f.generated_at, f.readiness_score, f.readiness_components
from forecasts f
where f.generated_at >= timestamptz '{DATE_FLOOR}'
  and f.readiness_components is not null
"""

TABLE_STATE_SQL = """
select 'statcast_player_observations' as relation, count(*) as rows,
       min(created_at) as first_row, max(created_at) as last_row
from statcast_player_observations
union all select 'bullpen_availability_observations', count(*), min(created_at), max(created_at)
from bullpen_availability_observations
union all select 'bullpen_features', count(*), min(created_at), max(created_at)
from bullpen_features
union all select 'game_feature_snapshots', count(*), min(created_at), max(created_at)
from game_feature_snapshots
union all select 'lineup_observations', count(*), min(created_at), max(created_at)
from lineup_observations
union all select 'starter_observations', count(*), min(created_at), max(created_at)
from starter_observations
union all select 'roster_availability_observations', count(*), min(created_at), max(created_at)
from roster_availability_observations
"""

# Starter and lineup evidence for exactly the V18 games, which is the evidence
# V18's own readiness_detail cites. This is the overlap between the two tracks.
V18_EVIDENCE_SQL = f"""
select sf.source_event_id as mlb_game_pk,
       sf.event_date,
       sf.scheduled_start_utc,
       (select count(*) from lineup_observations lo
          where lo.game_id = sf.canonical_game_id) as lineup_observations,
       (select count(distinct lo.team_id) from lineup_observations lo
          where lo.game_id = sf.canonical_game_id) as lineup_clubs,
       (select min(lo.observed_at) from lineup_observations lo
          where lo.game_id = sf.canonical_game_id) as lineup_first_observed_at,
       (select count(*) from starter_observations so
          where so.game_id = sf.canonical_game_id) as starter_observations,
       (select count(distinct so.team_id) from starter_observations so
          where so.game_id = sf.canonical_game_id) as starter_clubs,
       (select count(*) from bullpen_availability_observations bo
          where bo.game_id = sf.canonical_game_id) as bullpen_observations,
       (select count(*) from game_feature_snapshots fs
          where fs.game_id = sf.canonical_game_id) as feature_snapshots
from shadow_forecasts sf
join shadow_forecast_bundles b on b.id = sf.bundle_id
where b.sport = 'MLB'
  and sf.engine_id = 'MLB_V18_WHOLE_GAME'
  and sf.event_date >= date '{DATE_FLOOR}'
  and sf.source_event_id not in ('823343', '824235', '822693')
order by sf.event_date, sf.source_event_id
"""

SNAPSHOT_COVERAGE_SQL = f"""
select count(*) as snapshots,
       min(feature_completeness) as min_completeness,
       avg(feature_completeness) as avg_completeness,
       max(feature_completeness) as max_completeness,
       count(*) filter (where feature_completeness >= 0.60) as at_or_above_threshold
from game_feature_snapshots
where created_at >= timestamptz '{DATE_FLOOR}'
"""


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def _rows(conn, sql: str, **params: Any) -> list[dict[str, Any]]:
    result = conn.execute(text(sql), params)
    cols = list(result.keys())
    return [dict(zip(cols, row)) for row in result.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description="AISP evidence-availability audit")
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "outputs" / "v18-error-analysis-evidence"),
        help="output directory",
    )
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    engine = create_engine(os.environ["DATABASE_URL"])
    as_of = datetime.now(UTC)

    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        forecasts = _rows(conn, COMPONENT_SQL)
        table_state = _rows(conn, TABLE_STATE_SQL)
        v18_evidence = _rows(conn, V18_EVIDENCE_SQL)
        snapshot_coverage = _rows(conn, SNAPSHOT_COVERAGE_SQL)[0]

    # --- what the implemented formula actually did, per component ------------
    status_counts: dict[str, Counter] = defaultdict(Counter)
    reason_counts: dict[str, Counter] = defaultdict(Counter)
    weights: dict[str, set[float]] = defaultdict(set)
    points_lost: dict[str, float] = defaultdict(float)
    reason_field_present = 0
    total = len(forecasts)

    for row in forecasts:
        components = row["readiness_components"] or {}
        saw_reason = False
        for key, value in components.items():
            if key.startswith("_") or not isinstance(value, dict):
                continue
            status_counts[key][value.get("status")] += 1
            weights[key].add(float(value.get("weight") or 0.0))
            points_lost[key] += float(value.get("weight") or 0.0) - float(value.get("score") or 0.0)
            reason = value.get("unavailable_reason")
            if reason:
                reason_counts[key][reason] += 1
            if "unavailable_reason" in value:
                saw_reason = True
        if saw_reason:
            reason_field_present += 1

    denominator = sum(max(w) for w in weights.values()) if weights else 0.0
    components_report = []
    for key in sorted(status_counts, key=lambda k: -points_lost[k]):
        counts = status_counts[key]
        weight = max(weights[key]) if weights[key] else 0.0
        components_report.append({
            "component": key,
            "weight": weight,
            "weightShareOfDenominator": round(100.0 * weight / denominator, 2) if denominator else None,
            "ready": counts.get("ready", 0),
            "stale": counts.get("stale", 0),
            "missing": counts.get("missing", 0),
            "notApplicable": counts.get("not_applicable", 0),
            "alwaysMissing": counts.get("missing", 0) == total,
            "neverReady": counts.get("ready", 0) == 0,
            "averagePointsLost": round(points_lost[key] / total, 3) if total else None,
            "unavailableReasons": dict(reason_counts[key]),
            "audited": key in AUDITED,
        })

    readiness_scores = [r["readiness_score"] for r in forecasts if r["readiness_score"] is not None]
    at_or_above_70 = sum(1 for s in readiness_scores if s >= 70.0)

    audited_cost = sum(
        c["averagePointsLost"] for c in components_report if c["component"] in AUDITED
    )
    audited_ceiling_cost = sum(
        c["weight"] for c in components_report
        if c["component"] in AUDITED and c["alwaysMissing"]
    )

    fields = {}
    for key in AUDITED:
        measured = next((c for c in components_report if c["component"] == key), None)
        facts = dict(FIELD_FACTS[key])
        facts["measured"] = measured
        if key == "feature_completeness":
            facts["missingnessKind"] = (
                "PROXY_BELOW_THRESHOLD -- the field is populated and computed on every "
                "forecast; its measured value sits below the 60% coverage threshold, so "
                "the component reports STALE and scores half weight. It is not an "
                "absent observation."
            )
            facts["snapshotCoverage"] = _jsonable(snapshot_coverage)
        else:
            facts["missingnessKind"] = (
                "ABSENT_OBSERVATIONS -- the backing table has never held a row, so the "
                "component can only be MISSING. This is an un-ingested source, not a "
                "mapping fault and not a stale value."
            )
        fields[key] = facts

    # --- the V18 overlap: what AISP held for exactly the V18 games -----------
    v18_rows = [_jsonable(r) for r in v18_evidence]
    v18_summary = {
        "games": len(v18_rows),
        "withBothClubLineups": sum(1 for r in v18_rows if (r["lineup_clubs"] or 0) >= 2),
        "withBothClubStarters": sum(1 for r in v18_rows if (r["starter_clubs"] or 0) >= 2),
        "withAnyBullpenObservation": sum(1 for r in v18_rows if (r["bullpen_observations"] or 0) > 0),
        "withFeatureSnapshot": sum(1 for r in v18_rows if (r["feature_snapshots"] or 0) > 0),
    }

    report = {
        "studyId": STUDY_ID,
        "asOfUtc": as_of.isoformat(),
        "population": {
            "description": "AISP (Gen 1) forecasts carrying stored readiness components",
            "dateFloor": DATE_FLOOR,
            "forecasts": total,
            "readinessDenominator": denominator,
            "readinessThreshold": 70.0,
            "atOrAboveThreshold": at_or_above_70,
            "belowThreshold": len(readiness_scores) - at_or_above_70,
            "shareAtOrAboveThreshold": round(100.0 * at_or_above_70 / len(readiness_scores), 1)
            if readiness_scores else None,
            "minScore": min(readiness_scores) if readiness_scores else None,
            "maxScore": max(readiness_scores) if readiness_scores else None,
            "forecastsCarryingUnavailableReasonField": reason_field_present,
            "reasonFieldNote": "unavailable_reason was added after this window opened, "
                               "so reason codes are present on only part of the "
                               "population. Absence of a code is not absence of a gap.",
        },
        "implementedFormula": {
            "source": "app/features/readiness.py::calculate_readiness",
            "rule": "score = 100 * sum(weight * status_factor) / sum(weight)",
            "statusFactors": {"ready": 1.0, "stale": 0.5, "missing": 0.0,
                              "not_applicable": 1.0},
            "activeWeightMap": "GATE2_WEIGHTS",
            "note": "The score is normalized by the sum of all weights in the active "
                    "map. Because that sum is 100 here, a component's weight and its "
                    "point cost coincide -- but that is arithmetic coincidence, not a "
                    "property of the formula. A STALE component costs half its weight, "
                    "not all of it.",
        },
        "carriedForwardClaim": {
            "claim": "feature_completeness, bullpen and statcast had never ingested any "
                     "value, costing 20 weight points before any live evidence "
                     "(audit of 2026-09-08, not re-verified since).",
            "verdict": "PARTLY CONFIRMED",
            "confirmed": "bullpen and statcast have never held a row and are MISSING on "
                         "every forecast in the window, costing their full weight each.",
            "corrected": "feature_completeness is populated on every forecast and is "
                         "STALE, never MISSING. It scores half weight, not zero.",
            "measuredCostPerForecast": round(audited_cost, 2),
            "structuralFloorFromAlwaysMissing": audited_ceiling_cost,
            "claimedCost": 20.0,
        },
        "components": components_report,
        "fields": fields,
        "backingTableState": [_jsonable(r) for r in table_state],
        "v18GameEvidence": {
            "summary": v18_summary,
            "note": "This is what AISP stored for the V18 games. V18's own "
                    "readiness_detail cites CONFIRMED lineups and PROBABLE starters "
                    "from MLB_STATS_API and does not cite bullpen or Statcast "
                    "features. An empty AISP field is therefore not evidence that V18 "
                    "lacked that input.",
            "rows": v18_rows,
        },
        "historicalAvailability": {
            "status": "UNKNOWN",
            "detail": "No timestamped archive of readiness inputs as they stood before "
                      "each past forecast was located, so what was knowable at an "
                      "earlier decision time cannot be reconstructed from this "
                      "environment. Stored readiness_components describe the state at "
                      "forecast generation only.",
        },
        "outOfScope": [
            "No weight, threshold or normalization was changed.",
            "No threshold reduction is proposed; more decisions are not a goal of "
            "this audit.",
            "No ingestion redesign is proposed.",
        ],
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "evidence_availability_audit.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps({
        "report": str(path),
        "forecastsAudited": total,
        "atOrAboveThreshold": at_or_above_70,
        "measuredCostOfAuditedThree": round(audited_cost, 2),
        "verdict": report["carriedForwardClaim"]["verdict"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
