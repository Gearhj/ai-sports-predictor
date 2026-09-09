"""Offline MLB acceptance-policy research study (outcome-linked) -- stage 1.

    python -m scripts.mlb_acceptance_policy_study export
    python -m scripts.mlb_acceptance_policy_study analyze

STAGE 1 ``export`` takes a single read-only extraction watermark and writes an
immutable cohort snapshot to the output directory. Every later stage reads that
file, so the live database is queried once and no long transaction is held open
while the supervised scheduler keeps running.

STAGE 2 ``analyze`` (see :mod:`scripts.mlb_acceptance_policy_analysis`) runs
entirely on the exported copy: the rejection waterfall, the frozen policy
matrix, the exact-control replay reconciliation and the outcome-linked
comparison.

BOUNDARIES. Read-only. No provider call, no odds refresh, no forecast, decision,
position or settlement write, no migration, no policy change. Hypothetical
replay results never touch the live ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STUDY_ID = "AISP_MLB_ACCEPTANCE_POLICY_STUDY_V2"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "outputs" / "mlb-acceptance-policy-study-v2"

#: The study control. V1 is its separate historical strict comparison.
CONTROL_POLICY = "AISP_PRACTICAL_PREGAME_POLICY_V2_1"
CONTROL_EXPERIMENT_VERSION = 3
COMPARISON_EXPERIMENT_VERSION = 1

FINAL_PREGAME_CUTOFF_MINUTES = 15


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"unserialisable: {type(value)!r}")


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=json_default),
        encoding="utf-8",
    )
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


#: Evidence keys kept for every row. FINAL_PREGAME rows keep the whole object;
#: the ~11k indicative rows keep only what the waterfall and matrix consume, so
#: the frozen cohort stays small enough to store and re-read verbatim.
EVIDENCE_KEYS = (
    "policy_version", "tier", "lifecycle", "critical_evidence_complete",
    "gates_passed", "raw_model_probability", "market_probability", "raw_edge",
    "uncertainty_adjustment", "starter_penalty", "lineup_penalty",
    "adjusted_probability", "adjusted_edge", "adjusted_expected_value",
    "evidence_multiplier", "position_cap_fraction",
)


def _trim_evidence(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    if not evidence:
        return evidence
    trimmed: dict[str, Any] = {k: evidence.get(k) for k in EVIDENCE_KEYS if k in evidence}
    decomposition = evidence.get("feature_decomposition") or {}
    trimmed["feature_decomposition_summary"] = {
        "model_required_missing": decomposition.get("model_required_missing"),
        "duplicate_components": len(decomposition.get("duplicate_components") or []),
        "fallback_components": len(decomposition.get("fallback_components") or []),
        "optional_components": len(decomposition.get("optional_components") or []),
        "components": len(decomposition.get("components") or []),
    }
    for side in ("starter_home", "starter_away", "lineup"):
        value = evidence.get(side)
        if isinstance(value, dict):
            trimmed[side] = {
                k: value.get(k) for k in
                ("state", "eligible", "penalized", "eligible_for_moneyline", "reasons")
                if k in value
            }
    return trimmed


def export() -> dict[str, Any]:
    import sqlalchemy as sa

    from app.core.config import get_settings

    settings = get_settings()
    engine = sa.create_engine(settings.database_url)

    def rows(sql: str, **params: Any) -> list[dict[str, Any]]:
        # One short connection per statement: nothing is held open across the
        # whole extraction while the supervised scheduler keeps writing.
        with engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(sa.text(sql), params)]

    watermark_at = datetime.now(UTC)
    watermark = rows("""
        select
          (select max(created_at) from shadow_experiment_decisions) as decisions_max_created_at,
          (select count(*) from shadow_experiment_decisions) as decisions_count,
          (select max(created_at) from official_asp_positions) as positions_max_created_at,
          (select count(*) from official_asp_positions) as positions_count,
          (select count(*) from position_results) as position_results_count,
          (select count(*) from odds_snapshots) as odds_snapshots_count,
          (select max(observed_at) from odds_snapshots) as odds_snapshots_max_observed_at,
          (select count(*) from forecasts) as forecasts_count
    """)[0]

    experiments = rows("""
        select id, version, name, sport, recommendation_policy_version, decision_policy,
               staking_policy_version, is_active, start_date, started_at, starting_bankroll,
               kelly_fraction, unit_percentage, max_position_percentage,
               max_same_game_percentage, max_daily_percentage, code_identity, policy_config
        from shadow_experiments order by version
    """)

    decisions = rows("""
        select d.*, e.version as experiment_version,
               e.recommendation_policy_version as experiment_policy
        from shadow_experiment_decisions d
        join shadow_experiments e on e.id = d.experiment_id
        where e.version = any(cast(:versions as int[]))
        order by d.recommended_at, d.game_id
    """, versions=[CONTROL_EXPERIMENT_VERSION, COMPARISON_EXPERIMENT_VERSION])
    for row in decisions:
        if row["phase"] != "FINAL_PREGAME":
            row["evidence"] = _trim_evidence(row.get("evidence"))

    cohort_game_ids = sorted({
        str(r["game_id"]) for r in decisions
        if r["experiment_version"] == CONTROL_EXPERIMENT_VERSION
    })

    games = rows("""
        select g.id, g.starts_at, g.status, g.home_score, g.away_score, g.final_at,
               g.source_mode, g.league_id,
               ht.team_id as home_team_id, ht_t.abbreviation as home_abbr, ht_t.name as home_name,
               at.team_id as away_team_id, at_t.abbreviation as away_abbr, at_t.name as away_name,
               fo.id as certified_outcome_id, fo.home_score as certified_home_score,
               fo.away_score as certified_away_score, fo.outcome_status as certified_status
        from games g
        left join game_teams ht on ht.game_id = g.id and ht.is_home
        left join teams ht_t on ht_t.id = ht.team_id
        left join game_teams at on at.game_id = g.id and not at.is_home
        left join teams at_t on at_t.id = at.team_id
        left join game_final_outcomes fo on fo.game_id = g.id
        where g.id = any(cast(:ids as uuid[]))
        order by g.starts_at
    """, ids=cohort_game_ids)

    outcome_ids = sorted({
        str(r["market_outcome_id"]) for r in decisions if r["market_outcome_id"]
    })
    market_outcomes = rows("""
        select mo.id, mo.label, mo.outcome_key, mo.participant_team_id,
               m.game_id, m.market_type, t.abbreviation as team_abbr
        from market_outcomes mo
        join markets m on m.id = mo.market_id
        left join teams t on t.id = mo.participant_team_id
        where mo.id = any(cast(:ids as uuid[]))
    """, ids=outcome_ids)

    positions = rows("""
        select p.*, e.version as experiment_version, m.game_id,
               mo.participant_team_id, mo.label as outcome_label,
               r.result as settled_result, r.profit_loss, r.settled_at as result_settled_at,
               r.game_home_score, r.game_away_score, r.bankroll_before, r.bankroll_after,
               r.closing_decimal_odds, r.closing_line_value, r.beat_close
        from official_asp_positions p
        left join shadow_experiments e on e.id = p.shadow_experiment_id
        join market_outcomes mo on mo.id = p.market_outcome_id
        join markets m on m.id = mo.market_id
        left join position_results r on r.position_id = p.id
        order by p.placed_at
    """)

    # Every moneyline snapshot for the cohort's games: the availability facts
    # behind "missing price", and the only legitimate source of a closing quote.
    quote_coverage = rows("""
        select m.game_id, mo.id as market_outcome_id, mo.participant_team_id,
               s.sportsbook_id, count(*) as snapshots,
               min(s.observed_at) as first_observed_at,
               max(s.observed_at) as last_observed_at,
               bool_or(s.is_closing) as has_closing
        from odds_snapshots s
        join market_outcomes mo on mo.id = s.market_outcome_id
        join markets m on m.id = mo.market_id
        where m.game_id = any(cast(:ids as uuid[])) and m.market_type = 'MONEYLINE'
        group by 1,2,3,4
    """, ids=cohort_game_ids)

    # Every snapshot for the exact outcomes the policy ever selected. This is
    # what makes a closing price checkable for a hypothetical selection under
    # the same definition the live settlement engine uses, without inventing one.
    moneyline_snapshots = rows("""
        select s.id, s.market_outcome_id, s.sportsbook_id, s.observed_at, s.available_as_of,
               s.american_odds, s.decimal_odds, s.implied_probability, s.status,
               s.is_closing, s.source_mode
        from odds_snapshots s
        where s.market_outcome_id = any(cast(:ids as uuid[]))
        order by s.market_outcome_id, s.observed_at
    """, ids=outcome_ids)

    forecasts = rows("""
        select id, game_id, version, generated_at, available_as_of, status, lifecycle,
               forecast_track, readiness_score, home_win_probability, away_win_probability,
               uncertainty, confidence, source_mode
        from forecasts
        where game_id = any(cast(:ids as uuid[]))
        order by generated_at
    """, ids=cohort_game_ids)

    engine.dispose()

    cohort = {
        "study_id": STUDY_ID,
        "exported_at_utc": watermark_at.isoformat(),
        "extraction_watermark": watermark,
        "control_policy": CONTROL_POLICY,
        "read_only": True,
        "final_pregame_cutoff_minutes": FINAL_PREGAME_CUTOFF_MINUTES,
        "evidence_trimming": {
            "full_evidence_phases": ["FINAL_PREGAME"],
            "trimmed_keys": list(EVIDENCE_KEYS),
            "note": ("Indicative MORNING/LINEUP_CONFIRMED rows keep only the fields the "
                     "waterfall and matrix consume; FINAL_PREGAME rows keep everything."),
        },
        "experiments": experiments,
        "decisions": decisions,
        "games": games,
        "market_outcomes": market_outcomes,
        "positions": positions,
        "moneyline_quote_coverage": quote_coverage,
        "moneyline_snapshots": moneyline_snapshots,
        "forecasts": forecasts,
    }
    path = write_json(OUTPUT_DIR / "cohort.json", cohort)
    manifest = {
        "study_id": STUDY_ID,
        "stage": "export",
        "exported_at_utc": watermark_at.isoformat(),
        "file": path.name,
        "sha256": digest(path),
        "bytes": path.stat().st_size,
        "counts": {
            "experiments": len(experiments), "decisions": len(decisions),
            "games": len(games), "market_outcomes": len(market_outcomes),
            "positions": len(positions), "quote_coverage_rows": len(quote_coverage),
            "moneyline_snapshots": len(moneyline_snapshots),
            "forecasts": len(forecasts),
        },
        "extraction_watermark": watermark,
    }
    write_json(OUTPUT_DIR / "cohort_manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mlb_acceptance_policy_study")
    parser.add_argument("stage", choices=("export", "analyze"))
    args = parser.parse_args(argv)
    if args.stage == "export":
        print(json.dumps(export(), indent=2, default=json_default))
        return 0
    from scripts.mlb_acceptance_policy_analysis import analyze

    print(json.dumps(analyze(OUTPUT_DIR), indent=2, default=json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
