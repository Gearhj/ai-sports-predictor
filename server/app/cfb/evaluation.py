"""Score CFB forecasts against admitted finals -- the controlling joint channel only.

WHICH FORECAST
--------------
Per game, the newest forecast whose designated publication clock is strictly
before kickoff (``exchange_published_utc``: the discovery receipt's
``committedAtUtc`` for production packages, the package-local receipt for the
earlier research packages -- each row says which). A forecast published after
kickoff is never scored. Nothing is scored against a result that is not a FINAL
``game_state_observations`` row.

WHICH PROBABILITY
-----------------
The joint-draw home/away win probability -- the channel ``AISP_CFB_PAPER_POLICY_V1``
controls on. Ridge and Elo are separate channels: their log loss is computed in
``detail.diagnostic_channels`` for the research page, labelled as diagnostics,
and never averaged with or substituted for the joint channel.

The stored Brier is the house three-class form (tie mass is zero here, so it is
exactly twice the two-way Brier; ``detail.two_way_brier`` carries that figure).
CRPS comes from the four retained PMFs, each checked to full mass at import.

Identity and supersession reuse ``ForecastEvaluationService``: a corrected final
is a new row beside the old one, and exactly one row per forecast is current.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CfbResearchForecast, Game, GameStatus, League, ResearchForecastEvaluation
from app.services import forecast_scoring as scoring
from app.services import result_provenance as provenance

SCORING_VERSION = "AISP_CFB_PROSPECTIVE_SCORING_V1"
ENGINE_ID = "CFB_GEN1_JOINT_BASELINE"
SPORT = "FOOTBALL"
LEAGUE = "CFB"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _mass(pmf: dict[str, Any]) -> scoring.MassFunction:
    return scoring.MassFunction.from_pairs(zip(pmf["values"], pmf["counts"]), draws=int(pmf["draw_count"]))


def frozen_forecast(session: Session, game: Game, source_game_id: str) -> tuple[CfbResearchForecast | None, list]:
    kickoff = _aware(game.starts_at)
    rows = list(session.scalars(select(CfbResearchForecast).where(
        CfbResearchForecast.source_game_id == str(source_game_id))))
    excluded = []
    eligible = []
    for row in rows:
        published = _aware(row.exchange_published_utc)
        if published is None:
            excluded.append({"forecast_id": row.forecast_id, "reason": "NO_PUBLICATION_CLOCK"})
        elif kickoff is None or published >= kickoff:
            excluded.append({"forecast_id": row.forecast_id, "reason": "PUBLISHED_AT_OR_AFTER_KICKOFF"})
        else:
            eligible.append(row)
    eligible.sort(key=lambda r: (_aware(r.exchange_published_utc), _aware(r.imported_at)))
    return (eligible[-1] if eligible else None), excluded


def _payload(row: CfbResearchForecast, game: Game, result: Any, *, now: datetime,
             excluded: list) -> dict[str, Any]:
    kickoff = _aware(game.starts_at)
    published = _aware(row.exchange_published_utc)
    home, away = int(result.home_score), int(result.away_score)
    outcome = scoring.actual_class(home, away)
    probabilities = scoring.OutcomeProbabilities(
        home=float(row.joint_home_win_probability), away=float(row.joint_away_win_probability),
        tie=float(row.tie_probability or 0.0))
    normalised = probabilities.normalised()
    margin_pmf = _mass(row.pmfs["home_margin"])
    total_pmf = _mass(row.pmfs["total"])
    home_pmf, away_pmf = _mass(row.pmfs["home_score"]), _mass(row.pmfs["away_score"])
    actual_margin, actual_total = home - away, home + away
    ridge = {"home": row.ridge_home_win_probability, "away": row.ridge_away_win_probability}
    elo = {"home": row.elo_home_win_probability, "away": row.elo_away_win_probability}

    def diagnostic(pair: dict[str, float]) -> dict[str, Any]:
        p = scoring.OutcomeProbabilities(home=float(pair["home"]), away=float(pair["away"]), tie=0.0).normalised()
        return {"home": pair["home"], "away": pair["away"], "log_loss": scoring.log_loss(p, outcome),
                "two_way_brier": scoring.two_way_brier(float(pair["home"]), outcome)}

    clock_verified = published is not None and _aware(row.source_cutoff_utc) is not None \
        and published != _aware(row.source_cutoff_utc)
    return {
        "sport": SPORT, "league_code": LEAGUE, "event_id": f"cfbd:{row.source_game_id}",
        "canonical_game_id": game.id, "engine_id": ENGINE_ID, "engine_version": row.baseline_configuration,
        "model_label": "CFB Gen 1 · joint paired-residual draws",
        "forecast_identity": row.forecast_id, "shadow_forecast_id": None,
        "snapshot_type": "PROSPECTIVE", "snapshot_version": None,
        "forecast_published_utc": published, "forecast_evidence_utc": _aware(row.source_cutoff_utc),
        "forecast_imported_utc": _aware(row.imported_at), "kickoff_utc": kickoff,
        "published_before_kickoff_seconds": int((kickoff - published).total_seconds()),
        "result_identity": result.identity, "result_source": result.source, "result_tier": result.tier,
        "result_observed_utc": result.observed_at, "home_score": home, "away_score": away,
        "actual_outcome": outcome, "predicted_outcome": normalised.predicted_class,
        "winner_correct": normalised.predicted_class == outcome,
        "probability_of_actual": normalised.of(outcome),
        "log_loss": scoring.log_loss(probabilities, outcome),
        "brier_score": scoring.brier_score(probabilities, outcome),
        "predicted_home_score": row.expected_home_points, "predicted_away_score": row.expected_away_points,
        "home_score_error": row.expected_home_points - home, "away_score_error": row.expected_away_points - away,
        "team_score_mae": (abs(row.expected_home_points - home) + abs(row.expected_away_points - away)) / 2,
        "predicted_margin": row.expected_home_margin, "actual_margin": actual_margin,
        "margin_error": row.expected_home_margin - actual_margin,
        "predicted_total": row.expected_total, "actual_total": actual_total,
        "total_error": row.expected_total - actual_total,
        "distribution_available": True, "distribution_unavailable_reason": None,
        "distribution_draws": int(row.draw_count),
        "crps_home": scoring.crps(home_pmf, home), "crps_away": scoring.crps(away_pmf, away),
        "crps_margin": scoring.crps(margin_pmf, actual_margin), "crps_total": scoring.crps(total_pmf, actual_total),
        "energy_score": None,
        "interval_coverage": {"margin": scoring.interval_coverage(margin_pmf, actual_margin),
                              "total": scoring.interval_coverage(total_pmf, actual_total)},
        "market_available": False,
        "market_unavailable_reason": ("No governed CFB price was stored before kickoff; the market "
                                      "comparison is not computed from a reference line."),
        "market_evidence": None,
        "detail": {
            "controlling_channel": "JOINT_PAIRED_RESIDUAL_BASELINE",
            "published_probabilities": {"home": probabilities.home, "away": probabilities.away,
                                        "tie": probabilities.tie},
            "two_way_brier": scoring.two_way_brier(float(row.joint_home_win_probability), outcome),
            "diagnostic_channels": {
                "note": "Separately estimated channels, scored for the research page only. Never averaged "
                        "with the joint channel and never used for a decision.",
                "ridge_development_calibrated": diagnostic(ridge), "elo": diagnostic(elo)},
            "expected_points_source": "Ridge alpha100 expected points (not derived from the joint draws)",
            "publication_clock": {
                "classification": "VERIFIED_INDEPENDENT_PUBLICATION" if clock_verified
                else "UNRESOLVED_PUBLICATION_CLOCK_COLLAPSED_ONTO_EVIDENCE_CUTOFF",
                "source": row.publication_clock_source, "verified": clock_verified,
                "published_utc": published.isoformat() if published else None,
                "evidence_cutoff_utc": _aware(row.source_cutoff_utc).isoformat(),
                "imported_utc": _aware(row.imported_at).isoformat()},
            "result_provenance": provenance.describe(result.source),
            "origin": row.origin, "excluded": excluded,
            "log_loss_epsilon": scoring.LOG_LOSS_EPSILON,
            "signed_error_convention": "predicted minus actual",
        },
        "scoring_version": SCORING_VERSION, "evaluated_at": now,
    }


def evaluate_cfb(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Score every FINAL CFB game AISP holds a pre-kickoff forecast for. Safe to rerun."""

    from app.cfb.final_ingest import strongest_final
    from app.models import ExternalIdMapping, Season
    from app.services.forecast_evaluation import ForecastEvaluationService

    moment = now or datetime.now(UTC)
    service = ForecastEvaluationService(session)
    games = list(session.scalars(select(Game).join(Season, Season.id == Game.season_id)
                                 .join(League, League.id == Season.league_id)
                                 .where(League.code == LEAGUE, Game.status == GameStatus.FINAL)))
    summary: dict[str, Any] = {"final_games": len(games), "scored": 0, "reused": 0, "superseded": 0,
                               "skipped": [], "scoring_version": SCORING_VERSION}
    for game in games:
        source_game_id = session.scalar(select(ExternalIdMapping.external_id).where(
            ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == "CFBD",
            ExternalIdMapping.entity_id == game.id))
        observation = strongest_final(session, game.id)
        if source_game_id is None or observation is None:
            summary["skipped"].append({"game_id": str(game.id), "skipped": "NO_AUTHORITATIVE_FINAL"})
            continue
        row, excluded = frozen_forecast(session, game, source_game_id)
        if row is None:
            summary["skipped"].append({"game_id": str(game.id), "skipped": "NO_PRE_KICKOFF_FORECAST",
                                       "excluded": excluded})
            continue

        class _Result:  # the shape ForecastEvaluationService reasons about
            home_score = observation.home_score
            away_score = observation.away_score
            identity = f"{observation.source}:{observation.payload_checksum}"
            source = str(observation.source)
            observed_at = _aware(observation.observed_at)
            tier = provenance.result_tier(observation.source)

        payload = _payload(row, game, _Result, now=moment, excluded=excluded)
        existing = session.scalar(select(ResearchForecastEvaluation).where(
            ResearchForecastEvaluation.event_id == payload["event_id"],
            ResearchForecastEvaluation.engine_id == payload["engine_id"],
            ResearchForecastEvaluation.engine_version == payload["engine_version"],
            ResearchForecastEvaluation.forecast_identity == payload["forecast_identity"],
            ResearchForecastEvaluation.result_identity == payload["result_identity"]))
        if existing is not None:
            summary["reused"] += 1
        else:
            service._demote_current(payload, now=moment)
            session.add(ResearchForecastEvaluation(**payload))
            session.flush()
            summary["scored"] += 1
        reconciliation = service._reconcile_current(payload)
        summary["superseded"] += len(reconciliation["superseded"])
    session.flush()
    return summary


__all__ = ["ENGINE_ID", "SCORING_VERSION", "evaluate_cfb", "frozen_forecast"]
