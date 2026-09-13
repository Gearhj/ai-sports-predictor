"""Extract the AISP evidence Model Studio needs to diagnose where V18 misses.

Read-only. This script never writes to the database, never calls a provider,
never runs a model and never creates a decision, position or settlement. It
produces local research artifacts under ``outputs/v18-error-analysis-evidence/``.

Selection rules are declared here, ahead of any outcome read, so that the
population cannot be shaped by what the outcomes turn out to be:

POPULATION
    ``shadow_forecasts`` whose bundle is MLB and whose engine is the V18
    whole-game engine. This is the research-shadow track; Gen 1 (``forecasts``)
    and the NFL research track are never mixed in.

DATE FLOOR
    ``event_date >= 2026-08-28``. Applied in SQL, before outcomes are touched.
    The sealed 2026-08-14..2026-08-27 Final OOT window is excluded by a second
    explicit predicate rather than relying on the floor alone, so the intent
    survives any future change to the floor.

PERMANENT EXCLUSIONS
    Source event ids 823343, 824235 and 822693 are removed in the same SQL
    predicate. They are never read and then filtered out afterwards.

PRIMARY FORECAST PER GAME
    One selected forecast per canonical game: highest ``snapshot_version``,
    then latest ``available_as_of_utc``, then latest ``created_at``. Every other
    version for that game is emitted in ``alternates`` on the same record.
    Repeated bundle imports of an unchanged forecast are not extra games.

POLICY CUTOFF
    ``scheduled_start_utc - 15 minutes`` (AISP_PRACTICAL_PREGAME_POLICY_V2_1).

ELIGIBLE FINAL RULE
    A FINAL state observation carrying both scores, ranked by provenance tier
    then recency then checksum -- byte for byte the rule
    ``app/services/forecast_evaluation.py::result_for`` applies, reusing
    ``app.services.result_provenance`` so the two cannot drift. Score payloads
    are read only for games that pass this gate. A game with no such
    observation yields nulls and an explicit reason, and a score sitting on the
    ``games`` row with nothing behind it is never substituted for one.

    ``game_final_outcomes`` is deliberately NOT this gate: it is the 2025
    retrospective training-label store and holds no 2026 row. Its coverage of
    this population is reported separately so the distinction stays visible.

COHORT
    ``VERIFIED_PROSPECTIVE`` when the scored forecast's publication time is
    recorded separately from its evidence cutoff; ``QUALIFIED_DIAGNOSTIC`` when
    the two collapsed onto one stored value, which neither proves nor disproves
    the publication claim; ``NOT_YET_SCORED`` when no eligible final exists yet.

Usage:
    python server/scripts/v18_error_analysis_export.py [--out DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import result_provenance as provenance  # noqa: E402

STUDY_ID = "AISP_MLB_V18_ERROR_ANALYSIS_EVIDENCE_V1"
SCHEMA_VERSION = "AISP_V18_ERROR_ANALYSIS_EVIDENCE_V2"
#: V2 adds ``researchScore`` and ``cohort`` and moves the eligible-final gate
#: from game_final_outcomes to the provenance-ranked FINAL state observation
#: the scorer itself uses. Every V1 field keeps its name and meaning.
SCHEMA_CHANGELOG = {
    "AISP_V18_ERROR_ANALYSIS_EVIDENCE_V2": [
        "eligible-final gate is now the provenance-ranked FINAL state "
        "observation, matching app/services/forecast_evaluation.py::result_for",
        "added researchScore, carrying the persisted research evaluation",
        "added cohort: VERIFIED_PROSPECTIVE | QUALIFIED_DIAGNOSTIC | NOT_YET_SCORED",
        "added trainingLabelStore, reporting game_final_outcomes coverage separately",
    ],
}
ENGINE_ID = "MLB_V18_WHOLE_GAME"
DATE_FLOOR = "2026-08-28"
SEALED_OOT_START = "2026-08-14"
SEALED_OOT_END = "2026-08-27"
PERMANENT_EXCLUSIONS = ("823343", "824235", "822693")
POLICY_CUTOFF_MINUTES = 15
POLICY_VERSION = "AISP_PRACTICAL_PREGAME_POLICY_V2_1"
REPO_ROOT = Path(__file__).resolve().parents[2]

# Fixed module constants, not caller input: inlined so the exclusion is visible
# in the SQL predicate itself rather than applied after the rows are read.
_EXCLUSION_SQL = ", ".join(f"'{pk}'" for pk in PERMANENT_EXCLUSIONS)

# ---------------------------------------------------------------- population --

FORECAST_SQL = f"""
select
    sf.id                      as shadow_forecast_id,
    sf.forecast_id,
    b.bundle_id                as bundle_key,
    b.bundle_sha256,
    b.contract_version,
    b.slate_date,
    b.imported_at              as bundle_imported_at,
    b.research_shadow_only,
    sf.engine_id, sf.engine_version, sf.model_artifact_sha256,
    sf.model_manifest_sha256, sf.feature_vector_sha256,
    sf.simulator_id, sf.simulator_version, sf.simulator_seed,
    sf.snapshot_type, sf.snapshot_version, sf.forecast_schema_version,
    sf.status, sf.season,
    sf.event_id, sf.source_event_id, sf.event_date,
    sf.canonical_game_id, sf.home_team_id as bundle_home_team,
    sf.away_team_id as bundle_away_team,
    sf.scheduled_start_utc, sf.schedule_as_of_utc,
    sf.source_cutoff_utc, sf.available_as_of_utc, sf.forecast_cutoff_utc,
    sf.created_at              as first_verifiable_import_at,
    sf.is_ready, sf.readiness_score, sf.readiness_threshold,
    sf.full_home_win_probability, sf.full_away_win_probability,
    sf.full_expected_home_score, sf.full_expected_away_score,
    sf.f5_home_win_probability, sf.f5_away_win_probability,
    sf.f5_expected_home_score, sf.f5_expected_away_score,
    sf.distributions, sf.uncertainty, sf.readiness_detail,
    sf.identity_evidence, sf.lineage_detail,
    g.status                   as game_status,
    g.starts_at                as game_starts_at,
    ht.abbreviation            as home_abbr, ht.name as home_name,
    at.abbreviation            as away_abbr, at.name as away_name
from shadow_forecasts sf
join shadow_forecast_bundles b on b.id = sf.bundle_id
left join games g on g.id = sf.canonical_game_id
left join game_teams gth on gth.game_id = sf.canonical_game_id and gth.is_home is true
left join teams ht on ht.id = gth.team_id
left join game_teams gta on gta.game_id = sf.canonical_game_id and gta.is_home is false
left join teams at on at.id = gta.team_id
where b.sport = 'MLB'
  and sf.engine_id = :engine_id
  and sf.event_date >= date '{DATE_FLOOR}'
  and not (sf.event_date between date '{SEALED_OOT_START}' and date '{SEALED_OOT_END}')
  and sf.source_event_id not in ({_EXCLUSION_SQL})
order by sf.event_date, sf.source_event_id, sf.snapshot_version,
         sf.available_as_of_utc, sf.created_at
"""

# The eligible-final gate, identical to the one the scorer uses
# (app/services/forecast_evaluation.py::result_for): a FINAL state observation
# carrying both scores. Ranking across several honest records of one game is
# done in Python through app.services.result_provenance, so this export and the
# scorer cannot drift apart on which final wins.
#
# game_final_outcomes is NOT this gate. That table is the 2025 retrospective
# training-label store; it holds no 2026 row and is reported separately.
FINAL_SQL = """
select o.game_id, o.id as observation_id, o.status, o.home_score, o.away_score,
       o.final_at, o.observed_at, o.available_as_of, o.source, o.source_mode,
       o.source_record_id, o.payload_checksum, o.scheduled_at
from game_state_observations o
where o.game_id = any(:game_ids)
  and o.status = 'final'
  and o.home_score is not null
  and o.away_score is not null
"""

TRAINING_LABEL_SQL = """
select count(*) as rows, min(official_date) as first_date, max(official_date) as last_date
from game_final_outcomes
where game_id = any(:game_ids)
"""

SCORE_SQL = """
select e.canonical_game_id, e.id as evaluation_id, e.shadow_forecast_id,
       e.forecast_identity, e.result_identity, e.result_source, e.result_tier,
       e.result_observed_utc, e.home_score, e.away_score, e.actual_outcome,
       e.predicted_outcome, e.winner_correct, e.probability_of_actual,
       e.log_loss, e.brier_score, e.predicted_home_score, e.predicted_away_score,
       e.home_score_error, e.away_score_error, e.team_score_mae,
       e.predicted_margin, e.actual_margin, e.margin_error,
       e.predicted_total, e.actual_total, e.total_error,
       e.distribution_available, e.distribution_unavailable_reason,
       e.distribution_draws, e.crps_home, e.crps_away, e.crps_margin,
       e.crps_total, e.energy_score, e.interval_coverage,
       e.market_available, e.market_unavailable_reason,
       e.scoring_version, e.evaluated_at, e.is_current,
       e.superseded_at, e.superseded_by_id, e.supersede_reason, e.detail
from research_forecast_evaluations e
where e.canonical_game_id = any(:game_ids)
order by e.is_current desc, e.evaluated_at desc
"""

DECISION_SQL = """
select d.shadow_forecast_id, d.market, d.selection, d.recommendation,
       d.has_quote, d.uncertainty_adjusted_edge, d.no_vig_edge, d.raw_edge,
       d.best_american, d.best_decimal, d.book, d.quote_observed_at,
       d.quote_available_as_of, d.virtual_stake, d.evaluated_at
from shadow_board_decisions d
where d.shadow_forecast_id = any(:forecast_ids)
"""

POSITION_SQL = """
select p.shadow_forecast_id, p.id as position_id, p.status, p.result,
       p.stake, p.decimal_odds, p.placed_at, p.settled_at, p.payout
from official_asp_positions p
where p.shadow_forecast_id = any(:forecast_ids)
"""

AUTHORITY_SQL = """
select engine_id, model_era_id, state, effective_at, imported_at,
       execution_allowed_in_aisp, real_money_execution_allowed,
       gen1_fallback_allowed, minimum_adjusted_edge,
       historical_validation_under_review, historical_integrity_note,
       model_artifact_sha256, champion_manifest_sha256
from model_champion_authorities
where engine_id = :engine_id
order by imported_at desc
"""

# Pregame participant evidence actually stored in AISP for the canonical game.
# Counts and latest observation times only -- this describes availability, it
# does not reconstruct what V18 consumed.
EVIDENCE_SQL = """
select g.id as game_id,
  (select count(*) from lineup_observations lo where lo.game_id = g.id) as lineup_observations,
  (select max(lo.observed_at) from lineup_observations lo where lo.game_id = g.id) as lineup_last_observed_at,
  (select count(*) from starter_observations so where so.game_id = g.id) as starter_observations,
  (select max(so.observed_at) from starter_observations so where so.game_id = g.id) as starter_last_observed_at,
  -- roster and Statcast observations are scoped to a TEAM, not a game: neither
  -- table has a game_id column. They are counted through the game's two clubs,
  -- which is the only linkage that exists.
  (select count(*) from roster_availability_observations ro
     where ro.team_id in (select gt.team_id from game_teams gt where gt.game_id = g.id)
  ) as roster_observations,
  (select count(*) from game_feature_snapshots fs where fs.game_id = g.id) as feature_snapshots,
  (select count(*) from bullpen_availability_observations bo where bo.game_id = g.id) as bullpen_observations,
  (select count(*) from statcast_player_observations sp
     where sp.team_id in (select gt.team_id from game_teams gt where gt.game_id = g.id)
  ) as statcast_observations,
  (select count(*) from weather_snapshots w where w.game_id = g.id) as weather_snapshots,
  (select count(*) from markets m join market_outcomes mo on mo.market_id = m.id
     join odds_snapshots os on os.market_outcome_id = mo.id
   where m.game_id = g.id) as odds_snapshots
from games g
where g.id = any(:game_ids)
"""

WATERMARK_SQL = """
select
  (select count(*) from shadow_forecasts) as shadow_forecasts_count,
  (select max(created_at) from shadow_forecasts) as shadow_forecasts_max_created_at,
  (select count(*) from shadow_forecast_bundles) as bundles_count,
  (select count(*) from game_final_outcomes) as game_final_outcomes_count,
  (select max(official_date) from game_final_outcomes) as game_final_outcomes_max_date,
  (select count(*) from shadow_board_decisions) as decisions_count,
  (select count(*) from official_asp_positions) as positions_count
"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


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


# Lineage keys whose payloads are large research blobs (per-game geometry views
# and the deterministic replay path). They are split into a sidecar file so the
# primary export stays small enough to stream, and the primary record keeps a
# checksum and byte count for each so the lineage is still navigable.
BULKY_LINEAGE_KEYS = ("populationGeometry", "playGeometry", "sequentialPath")


def _split_lineage(lineage: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (inline lineage with blob references, extracted blobs)."""
    if not isinstance(lineage, dict):
        return ({} if lineage is None else {"value": lineage}), {}
    inline = {k: v for k, v in lineage.items() if k not in BULKY_LINEAGE_KEYS}
    blobs = {k: lineage[k] for k in BULKY_LINEAGE_KEYS if k in lineage}
    if blobs:
        inline["blobReferences"] = {
            key: {
                "sha256": hashlib.sha256(
                    json.dumps(value, sort_keys=True, separators=(",", ":"),
                               default=str).encode()
                ).hexdigest(),
                "bytes": len(json.dumps(value, sort_keys=True,
                                        separators=(",", ":"), default=str)),
                "file": "v18_lineage_blobs.jsonl",
            }
            for key, value in blobs.items()
        }
    return inline, blobs


def _final_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    """The scorer's ordering: provenance tier, then knowledge time, then checksum."""

    known = row.get("available_as_of") or datetime.min.replace(tzinfo=UTC)
    if known.tzinfo is None:
        known = known.replace(tzinfo=UTC)
    return (
        provenance.result_tier(row.get("source")),
        -known.timestamp(),
        str(row.get("payload_checksum") or ""),
    )


def _rows(conn, sql: str, **params: Any) -> list[dict[str, Any]]:
    result = conn.execute(text(sql), params)
    cols = list(result.keys())
    return [dict(zip(cols, row)) for row in result.fetchall()]


def _select_primary(versions: list[dict[str, Any]]) -> dict[str, Any]:
    floor = datetime.min.replace(tzinfo=UTC)

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row.get("snapshot_version") or 0,
            row.get("available_as_of_utc") or floor,
            row.get("first_verifiable_import_at") or floor,
        )

    return max(versions, key=key)


def main() -> int:
    parser = argparse.ArgumentParser(description="V18 error-analysis evidence export")
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "outputs" / "v18-error-analysis-evidence"),
        help="output directory for the export artifacts",
    )
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    engine = create_engine(os.environ["DATABASE_URL"])
    as_of = datetime.now(UTC)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    omissions: list[dict[str, Any]] = []

    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))

        forecasts = _rows(
            conn,
            FORECAST_SQL,
            engine_id=ENGINE_ID,
        )
        authority = _rows(conn, AUTHORITY_SQL, engine_id=ENGINE_ID)
        era = authority[0] if authority else None

        by_game: dict[str, list[dict[str, Any]]] = {}
        unmapped: list[dict[str, Any]] = []
        for row in forecasts:
            game_id = row.get("canonical_game_id")
            if game_id is None:
                unmapped.append(row)
                omissions.append({
                    "forecastId": row["forecast_id"],
                    "sourceEventId": row["source_event_id"],
                    "stage": "identity",
                    "reason": "NO_CANONICAL_GAME_BOUND",
                    "detail": "forecast has no canonical_game_id, so it cannot be "
                              "joined to a game, a final or an evidence row",
                })
                continue
            by_game.setdefault(str(game_id), []).append(row)

        game_ids = list(by_game.keys())
        forecast_ids = [str(row["shadow_forecast_id"]) for row in forecasts]

        # --- the eligible-final gate runs BEFORE any score payload is read ----
        # Candidate observations only; which one wins is decided immediately
        # below by the scorer's own provenance module, not by this query.
        finals: dict[str, dict[str, Any]] = {}
        if game_ids:
            candidates: dict[str, list[dict[str, Any]]] = {}
            for row in _rows(conn, FINAL_SQL, game_ids=game_ids):
                candidates.setdefault(str(row["game_id"]), []).append(row)
            for key, rows in candidates.items():
                finals[key] = min(rows, key=_final_rank)
        training_labels = (
            _rows(conn, TRAINING_LABEL_SQL, game_ids=game_ids)[0] if game_ids
            else {"rows": 0, "first_date": None, "last_date": None}
        )
        scores: dict[str, list[dict[str, Any]]] = {}
        if game_ids:
            for row in _rows(conn, SCORE_SQL, game_ids=game_ids):
                scores.setdefault(str(row["canonical_game_id"]), []).append(row)
        evidence = (
            {str(r["game_id"]): r for r in _rows(conn, EVIDENCE_SQL, game_ids=game_ids)}
            if game_ids else {}
        )

        decisions: dict[str, list[dict[str, Any]]] = {}
        positions: dict[str, list[dict[str, Any]]] = {}
        if forecast_ids:
            for row in _rows(conn, DECISION_SQL, forecast_ids=forecast_ids):
                decisions.setdefault(str(row["shadow_forecast_id"]), []).append(row)
            for row in _rows(conn, POSITION_SQL, forecast_ids=forecast_ids):
                positions.setdefault(str(row["shadow_forecast_id"]), []).append(row)

        watermark = _rows(conn, WATERMARK_SQL)[0]

    records: list[dict[str, Any]] = []
    blob_rows: list[dict[str, Any]] = []
    for game_id, versions in by_game.items():
        primary = _select_primary(versions)
        others = [v for v in versions if v is not primary]
        shadow_forecast_id = str(primary["shadow_forecast_id"])

        start = primary.get("scheduled_start_utc")
        cutoff = start - timedelta(minutes=POLICY_CUTOFF_MINUTES) if start else None
        published = primary.get("available_as_of_utc")
        source_cut = primary.get("source_cutoff_utc")
        imported = primary.get("first_verifiable_import_at")

        readiness = primary.get("readiness_detail") or {}
        captured_before_start = readiness.get("capturedBeforeScheduledStart")

        # Two eligibility flags, deliberately independent of one another.
        scientific = bool(published and cutoff and published <= cutoff)
        operational = bool(
            imported and cutoff and imported <= cutoff
            and era is not None
            and era.get("effective_at") is not None
            and era["effective_at"] <= cutoff
            and bool(era.get("execution_allowed_in_aisp"))
        )

        # Publication time is only independently meaningful when it is distinct
        # from the evidence cutoff. Collapsed clocks are reported, not repaired.
        clocks_collapsed = bool(published and source_cut and published == source_cut)

        blocking: list[str] = []
        if era is None:
            blocking.append("NO_CHAMPION_AUTHORITY_IMPORTED")
        else:
            if str(era.get("state")) != "ACTIVE":
                blocking.append(f"MODEL_ERA_{era.get('state')}_NOT_ACTIVE")
            if not era.get("execution_allowed_in_aisp"):
                blocking.append("EXECUTION_NOT_ALLOWED_IN_AISP")
        if imported and cutoff and imported > cutoff:
            blocking.append("IMPORTED_AFTER_POLICY_CUTOFF")
        if not decisions.get(shadow_forecast_id):
            blocking.append("NO_DECISION_ROW_EXISTS")

        final = finals.get(game_id)
        if final is None:
            outcome = {
                "eligible": False,
                "reason": "NO_ELIGIBLE_FINAL_OBSERVATION",
                "detail": "No FINAL state observation carrying both scores exists for "
                          "this canonical game. No score payload was read for it, and "
                          "a score on the games row with no observation behind it is "
                          "not substituted.",
                "homeScore": None, "awayScore": None, "homeWin": None,
                "runDifferential": None, "totalRuns": None,
            }
            omissions.append({
                "forecastId": primary["forecast_id"],
                "sourceEventId": primary["source_event_id"],
                "stage": "outcome",
                "reason": "NO_ELIGIBLE_FINAL_OBSERVATION",
                "detail": f"games.status={primary.get('game_status')} at export time",
            })
        else:
            described = provenance.describe(final["source"])
            home, away = int(final["home_score"]), int(final["away_score"])
            outcome = {
                "eligible": True,
                "reason": None,
                "observationId": str(final["observation_id"]),
                "resultIdentity": f"{final['source']}:{final['payload_checksum']}",
                "homeScore": home,
                "awayScore": away,
                "homeWin": home > away,
                "runDifferential": home - away,
                "totalRuns": home + away,
                "finalAt": _iso(final["final_at"]),
                "observedAt": _iso(final["observed_at"]),
                "availableAsOf": _iso(final["available_as_of"]),
                "source": final["source"],
                "sourceMode": final["source_mode"],
                "sourceRecordId": final["source_record_id"],
                "payloadChecksum": final["payload_checksum"],
                "provenanceTier": described["tier"],
                "provenanceLabel": described["label"],
                "providerVerified": described["provider_verified"],
                "provenanceNote": described["note"],
            }

        game_scores = scores.get(game_id, [])
        current = next((r for r in game_scores if r["is_current"]), None)
        superseded_rows = [r for r in game_scores if not r["is_current"]]
        if current is None:
            research_score = {
                "scored": False,
                "reason": ("NO_ELIGIBLE_FINAL_OBSERVATION" if final is None
                           else "NOT_YET_EVALUATED"),
                "detail": ("The game has no eligible final observation to score "
                           "against yet." if final is None else
                           "An eligible final exists but no research evaluation has "
                           "been written for this game yet."),
            }
            cohort = "NOT_YET_SCORED"
        else:
            clock = (current.get("detail") or {}).get("publication_clock") or {}
            cohort = ("VERIFIED_PROSPECTIVE" if clock.get("verified")
                      else "QUALIFIED_DIAGNOSTIC")
            research_score = {
                "scored": True,
                "reason": None,
                "evaluationId": str(current["evaluation_id"]),
                "scoringVersion": current["scoring_version"],
                "evaluatedAt": _iso(current["evaluated_at"]),
                "forecastIdentity": current["forecast_identity"],
                "shadowForecastId": str(current["shadow_forecast_id"]),
                "resultIdentity": current["result_identity"],
                "resultSource": current["result_source"],
                "resultTier": current["result_tier"],
                "resultObservedUtc": _iso(current["result_observed_utc"]),
                "publicationClock": clock,
                "outcome": {
                    "actual": current["actual_outcome"],
                    "predicted": current["predicted_outcome"],
                    "winnerCorrect": current["winner_correct"],
                    "probabilityOfActual": current["probability_of_actual"],
                    "logLoss": current["log_loss"],
                    "brierScore": current["brier_score"],
                },
                "points": {
                    "predictedHome": current["predicted_home_score"],
                    "predictedAway": current["predicted_away_score"],
                    "homeScoreError": current["home_score_error"],
                    "awayScoreError": current["away_score_error"],
                    "teamScoreMae": current["team_score_mae"],
                    "predictedMargin": current["predicted_margin"],
                    "actualMargin": current["actual_margin"],
                    "marginError": current["margin_error"],
                    "predictedTotal": current["predicted_total"],
                    "actualTotal": current["actual_total"],
                    "totalError": current["total_error"],
                    "signedErrorConvention": "predicted minus actual",
                },
                "distribution": {
                    "available": current["distribution_available"],
                    "unavailableReason": current["distribution_unavailable_reason"],
                    "draws": current["distribution_draws"],
                    "crpsHome": current["crps_home"],
                    "crpsAway": current["crps_away"],
                    "crpsMargin": current["crps_margin"],
                    "crpsTotal": current["crps_total"],
                    "energyScore": current["energy_score"],
                    "intervalCoverage": _jsonable(current["interval_coverage"]),
                },
                "market": {
                    "available": current["market_available"],
                    "unavailableReason": current["market_unavailable_reason"],
                },
                "supersededVersions": [
                    {
                        "evaluationId": str(row["evaluation_id"]),
                        "resultIdentity": row["result_identity"],
                        "resultSource": row["result_source"],
                        "homeScore": row["home_score"],
                        "awayScore": row["away_score"],
                        "supersedeReason": row["supersede_reason"],
                        "supersededAt": _iso(row["superseded_at"]),
                    }
                    for row in superseded_rows
                ],
            }

        ev = evidence.get(game_id, {})
        inline_lineage, lineage_blobs = _split_lineage(
            _jsonable(primary["lineage_detail"])
        )
        records.append({
            "schemaVersion": SCHEMA_VERSION,
            "identity": {
                "canonicalGameId": game_id,
                "mlbGamePk": primary["source_event_id"],
                "bundleEventId": primary["event_id"],
                "eventDate": _iso(primary["event_date"]),
                "season": primary["season"],
                "homeTeamAbbr": primary.get("home_abbr") or primary.get("bundle_home_team"),
                "awayTeamAbbr": primary.get("away_abbr") or primary.get("bundle_away_team"),
                "homeTeamName": primary.get("home_name"),
                "awayTeamName": primary.get("away_name"),
                "orientation": "every probability and expected score is HOME versus AWAY",
                "scheduledStartUtc": _iso(primary["scheduled_start_utc"]),
                "gameStatusAtExport": primary.get("game_status"),
            },
            "forecast": {
                "shadowForecastId": shadow_forecast_id,
                "forecastId": primary["forecast_id"],
                "engineId": primary["engine_id"],
                "engineVersion": primary["engine_version"],
                "snapshotType": primary["snapshot_type"],
                "snapshotVersion": primary["snapshot_version"],
                "forecastSchemaVersion": primary["forecast_schema_version"],
                "status": primary["status"],
                "modelEraId": (era or {}).get("model_era_id"),
                "modelArtifactSha256": primary["model_artifact_sha256"],
                "modelManifestSha256": primary["model_manifest_sha256"],
                "featureVectorSha256": primary["feature_vector_sha256"],
                "simulatorId": primary["simulator_id"],
                "simulatorVersion": primary["simulator_version"],
                "simulatorSeed": primary["simulator_seed"],
                "bundleKey": primary["bundle_key"],
                "bundleSha256": primary["bundle_sha256"],
                "contractVersion": primary["contract_version"],
                "researchShadowOnly": primary["research_shadow_only"],
            },
            "clocks": {
                "sourceEvidenceCutoffUtc": _iso(source_cut),
                "publicationTimeUtc": _iso(published),
                "forecastCutoffUtc": _iso(primary["forecast_cutoff_utc"]),
                "firstVerifiableImportUtc": _iso(imported),
                "bundleImportedAtUtc": _iso(primary["bundle_imported_at"]),
                "scheduleAsOfUtc": _iso(primary["schedule_as_of_utc"]),
                "policyCutoffUtc": _iso(cutoff),
                "policyCutoffRule": f"scheduled_start_utc minus {POLICY_CUTOFF_MINUTES} minutes",
                "policyVersion": POLICY_VERSION,
                "timingUncertainty": {
                    "evidenceAndPublicationCollapsed": clocks_collapsed,
                    "note": (
                        "source_cutoff_utc equals available_as_of_utc, so publication "
                        "time is not independently evidenced for this forecast"
                        if clocks_collapsed else
                        "evidence cutoff and publication time are distinct values"
                    ),
                    "capturedBeforeScheduledStart": captured_before_start,
                    "publisherLimitations": readiness.get("limitations"),
                },
            },
            "eligibility": {
                "scientificProspective": {
                    "eligible": scientific,
                    "basis": "publication time at or before the policy cutoff, "
                             "independent of when AISP imported the forecast",
                },
                "operationalDecision": {
                    "eligible": operational,
                    "basis": "import and an active, execution-allowed model era both "
                             "present at or before the policy cutoff",
                    "blockingReasons": blocking,
                },
                "note": "A late import cannot create an eligible past decision, and it "
                        "does not erase an independently verified earlier publication. "
                        "Both flags are retained.",
            },
            "predictions": {
                "fullGame": {
                    "homeWinProbability": primary["full_home_win_probability"],
                    "awayWinProbability": primary["full_away_win_probability"],
                    "expectedHomeScore": primary["full_expected_home_score"],
                    "expectedAwayScore": primary["full_expected_away_score"],
                },
                "firstFive": {
                    "homeWinProbability": primary["f5_home_win_probability"],
                    "awayWinProbability": primary["f5_away_win_probability"],
                    "expectedHomeScore": primary["f5_expected_home_score"],
                    "expectedAwayScore": primary["f5_expected_away_score"],
                },
                "distributions": _jsonable(primary["distributions"]),
                "uncertainty": _jsonable(primary["uncertainty"]),
            },
            "publisherReadiness": {
                "isReady": primary["is_ready"],
                "score": primary["readiness_score"],
                "threshold": primary["readiness_threshold"],
                "detail": _jsonable(readiness),
                "note": "This is the V18 publisher's own gate, not the AISP readiness "
                        "score that qualifies Gen 1 forecasts. The two are unrelated "
                        "scales and must not be compared.",
            },
            "aispPregameEvidence": {
                "lineupObservations": ev.get("lineup_observations"),
                "lineupLastObservedAt": _iso(ev.get("lineup_last_observed_at")),
                "starterObservations": ev.get("starter_observations"),
                "starterLastObservedAt": _iso(ev.get("starter_last_observed_at")),
                "rosterObservations": ev.get("roster_observations"),
                "featureSnapshots": ev.get("feature_snapshots"),
                "bullpenObservations": ev.get("bullpen_observations"),
                "statcastObservations": ev.get("statcast_observations"),
                "weatherSnapshots": ev.get("weather_snapshots"),
                "oddsSnapshots": ev.get("odds_snapshots"),
                "note": "Counts describe what AISP stored for this game. They do not "
                        "describe what the V18 publisher consumed; an empty AISP field "
                        "is not evidence that V18 lacked the feature.",
            },
            "outcome": outcome,
            "researchScore": research_score,
            "cohort": cohort,
            "decisions": [_jsonable(d) for d in decisions.get(shadow_forecast_id, [])],
            "positions": [_jsonable(p) for p in positions.get(shadow_forecast_id, [])],
            "alternates": [
                {
                    "shadowForecastId": str(v["shadow_forecast_id"]),
                    "forecastId": v["forecast_id"],
                    "snapshotVersion": v["snapshot_version"],
                    "bundleKey": v["bundle_key"],
                    "bundleSha256": v["bundle_sha256"],
                    "publicationTimeUtc": _iso(v["available_as_of_utc"]),
                    "firstVerifiableImportUtc": _iso(v["first_verifiable_import_at"]),
                    "homeWinProbability": v["full_home_win_probability"],
                }
                for v in others
            ],
            "lineage": inline_lineage,
            "identityEvidence": _jsonable(primary["identity_evidence"]),
        })
        if lineage_blobs:
            blob_rows.append({
                "shadowForecastId": shadow_forecast_id,
                "forecastId": primary["forecast_id"],
                "canonicalGameId": game_id,
                "mlbGamePk": primary["source_event_id"],
                "blobs": lineage_blobs,
            })

    records.sort(key=lambda r: (r["identity"]["eventDate"], r["identity"]["mlbGamePk"]))

    jsonl_path = out_dir / "v18_forecast_evidence.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")

    blob_index = {r["shadowForecastId"]: r for r in blob_rows}
    blobs_path = out_dir / "v18_lineage_blobs.jsonl"
    with blobs_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            row = blob_index.get(record["forecast"]["shadowForecastId"])
            if row is not None:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    omissions_path = out_dir / "omissions.json"
    omissions_path.write_text(
        json.dumps({
            "studyId": STUDY_ID,
            "asOfUtc": as_of.isoformat(),
            "omissions": omissions,
            "byReason": {
                reason: sum(1 for o in omissions if o["reason"] == reason)
                for reason in sorted({o["reason"] for o in omissions})
            },
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    scorable = sum(1 for r in records if r["outcome"]["eligible"])
    scored = sum(1 for r in records if r["researchScore"]["scored"])
    cohorts = {
        name: sum(1 for r in records if r["cohort"] == name)
        for name in ("VERIFIED_PROSPECTIVE", "QUALIFIED_DIAGNOSTIC", "NOT_YET_SCORED")
    }
    manifest = {
        "studyId": STUDY_ID,
        "schemaVersion": SCHEMA_VERSION,
        "schemaChangelog": SCHEMA_CHANGELOG,
        "stage": "export",
        "exportedAtUtc": as_of.isoformat(),
        "file": jsonl_path.name,
        "sha256": _sha256_file(jsonl_path),
        "bytes": jsonl_path.stat().st_size,
        "omissionsFile": omissions_path.name,
        "omissionsSha256": _sha256_file(omissions_path),
        "lineageBlobsFile": blobs_path.name,
        "lineageBlobsSha256": _sha256_file(blobs_path),
        "lineageBlobsBytes": blobs_path.stat().st_size,
        "lineageBlobsNote": "Large per-game lineage payloads "
                            f"({', '.join(BULKY_LINEAGE_KEYS)}) live here, keyed by "
                            "shadowForecastId. Each primary record carries a sha256 "
                            "and byte count for its blobs under lineage.blobReferences.",
        "selectionRules": {
            "engineId": ENGINE_ID,
            "dateFloor": DATE_FLOOR,
            "sealedOutOfTimeWindowExcluded": [SEALED_OOT_START, SEALED_OOT_END],
            "permanentExclusions": list(PERMANENT_EXCLUSIONS),
            "primaryForecastRule": "max(snapshot_version, available_as_of_utc, "
                                   "created_at) per canonical game",
            "policyCutoffRule": f"scheduled_start_utc minus {POLICY_CUTOFF_MINUTES} minutes",
            "finalRevisionRule": "highest revision in game_final_outcomes for the "
                                 "canonical game",
            "outcomeGate": "score payloads read only for games passing the "
                           "final-revision gate",
        },
        "counts": {
            "shadowForecastRowsInPopulation": len(forecasts),
            "distinctGames": len(records),
            "forecastsWithoutCanonicalGame": len(unmapped),
            "alternateVersions": sum(len(r["alternates"]) for r in records),
            "gamesWithEligibleFinal": scorable,
            "gamesWithoutEligibleFinal": len(records) - scorable,
            "gamesWithResearchScore": scored,
            "cohorts": cohorts,
            "gamesWithDecision": sum(1 for r in records if r["decisions"]),
            "gamesWithPosition": sum(1 for r in records if r["positions"]),
            "scientificallyProspective": sum(
                1 for r in records
                if r["eligibility"]["scientificProspective"]["eligible"]
            ),
            "operationallyDecisionEligible": sum(
                1 for r in records
                if r["eligibility"]["operationalDecision"]["eligible"]
            ),
            "clocksCollapsed": sum(
                1 for r in records
                if r["clocks"]["timingUncertainty"]["evidenceAndPublicationCollapsed"]
            ),
        },
        "modelEra": _jsonable(era) if era else None,
        "trainingLabelStore": {
            "table": "game_final_outcomes",
            "rowsForThisPopulation": training_labels["rows"],
            "firstOfficialDate": _iso(training_labels["first_date"]),
            "lastOfficialDate": _iso(training_labels["last_date"]),
            "note": "The 2025 retrospective training-label store. It is not the "
                    "eligible-final gate for research scoring and is reported here "
                    "only so the two provenance kinds stay visibly separate.",
        },
        "eligibleFinalGate": {
            "source": "game_state_observations",
            "rule": "status FINAL with both scores, ranked by provenance tier then "
                    "knowledge time then payload checksum",
            "sharedWith": "app/services/forecast_evaluation.py::result_for",
        },
        "extractionWatermark": _jsonable(dict(watermark)),
        "guarantees": {
            "databaseWrites": 0,
            "providerCalls": 0,
            "modelExecutions": 0,
            "sealedOutcomesQueried": False,
        },
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(json.dumps({
        "manifest": str(manifest_path),
        "export": str(jsonl_path),
        "omissions": str(omissions_path),
        "distinctGames": len(records),
        "gamesWithEligibleFinal": scorable,
        "gamesWithResearchScore": scored,
        "cohorts": cohorts,
        "exportSha256": manifest["sha256"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
