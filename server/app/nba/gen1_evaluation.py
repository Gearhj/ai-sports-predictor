"""Score NBA Gen1 forecasts against authoritative finals, ONE CHANNEL AT A TIME.

WHICH FORECAST
--------------
* PROSPECTIVE: per game, the newest production package whose designated
  publication clock is strictly before tipoff. A package published at or after
  tipoff is never scored.
* HISTORICAL: the 2025-26 assessment row for the game (evidence class A). Kept in
  its own evidence class; never averaged with, or counted as, prospective.

WHICH METRICS, PER CHANNEL
--------------------------
``RIDGE_POINTS``        points only: home/away absolute error, signed margin and
                        total error. No log loss, no Brier, no winner call --
                        a Ridge expectation is not a probability.
``ELO_WINNER``          winner only: two-way log loss, two-way Brier, the
                        probability given to the actual winner, winner correct.
                        No points.
``JOINT_DISTRIBUTION``  winner metrics from the joint winner mass, the joint's own
                        expected scores (not Ridge's), CRPS on home/away/margin/
                        total from the retained PMFs, and 50/80/90% interval hits
                        from the producer-exact intervals.

Only CURRENT FINAL ``game_state_observations`` are scored (strongest by provenance
tier, then recency). Rerunning writes nothing new when forecast, channel and
result identity all match.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.nba_gen1 import (
    CHANNEL_ELO, CHANNEL_JOINT, CHANNEL_RIDGE, EVIDENCE_HISTORICAL, EVIDENCE_PROSPECTIVE,
)
from app.nba import gen1_common as common
from app.services import forecast_scoring as scoring

SCORING_VERSION = "AISP_NBA_GEN1_CHANNEL_SCORING_V1"


def _winner(p_home: float, home: int, away: int) -> dict[str, Any]:
    outcome = scoring.actual_class(home, away)
    if outcome == scoring.TIE:
        raise ValueError("an NBA final cannot tie")
    p_actual = p_home if outcome == scoring.HOME else 1 - p_home
    return {"home_win_probability": p_home, "probability_of_actual": p_actual,
            "log_loss": -math.log(max(p_actual, scoring.LOG_LOSS_EPSILON)),
            "two_way_brier": scoring.two_way_brier(p_home, outcome),
            # A 0.5 winner mass picks nobody; it is not scored as right or wrong.
            "winner_correct": None if p_home == 0.5 else ((p_home > 0.5) == (outcome == scoring.HOME))}


def _points(pred_home: float, pred_away: float, home: int, away: int) -> dict[str, Any]:
    return {"predicted_home": pred_home, "predicted_away": pred_away,
            "home_abs_error": abs(pred_home - home), "away_abs_error": abs(pred_away - away),
            "margin_error": (pred_home - pred_away) - (home - away), "total_error": (pred_home + pred_away) - (home + away)}


def _mass(pmf: dict[str, Any]) -> scoring.MassFunction:
    return scoring.MassFunction.from_pairs(zip(pmf["values"], pmf["counts"]), draws=int(pmf["draw_count"]))


def _coverage(intervals: dict[str, Any], name: str, observed: int) -> dict[str, Any]:
    out = {}
    for item in intervals.get(name) or []:
        level = int(round(float(item["level"]) * 100))
        out[str(level)] = {"lower": item["lower"], "upper": item["upper"],
                           "contains": bool(item["lower"] <= observed <= item["upper"]),
                           "width": item["upper"] - item["lower"]}
    return out


def channel_rows(forecast: Any, *, home: int, away: int) -> dict[str, dict[str, Any]]:
    """The three channel score payloads for one forecast row (production or historical)."""

    rows: dict[str, dict[str, Any]] = {}
    if forecast.ridge_home_points is not None and forecast.ridge_away_points is not None:
        rows[CHANNEL_RIDGE] = {**_points(forecast.ridge_home_points, forecast.ridge_away_points, home, away),
                               "detail": {"units": "points including overtime",
                                          "note": "Points channel: no probability metric exists for it."}}
    if forecast.elo_home_win_probability is not None:
        rows[CHANNEL_ELO] = {**_winner(float(forecast.elo_home_win_probability), home, away),
                             "detail": {"method": forecast.elo_method,
                                        "note": "Winner channel: no point metric exists for it."}}
    summary = forecast.joint_summary or {}
    if forecast.joint_home_win_probability is not None and summary.get("pmfs"):
        pmfs = summary["pmfs"]
        margin, total = home - away, home + away
        rows[CHANNEL_JOINT] = {
            **_winner(float(forecast.joint_home_win_probability), home, away),
            **_points(float(forecast.joint_expected_home), float(forecast.joint_expected_away), home, away),
            "crps_home": scoring.crps(_mass(pmfs["home"]), home), "crps_away": scoring.crps(_mass(pmfs["away"]), away),
            "crps_margin": scoring.crps(_mass(pmfs["margin"]), margin),
            "crps_total": scoring.crps(_mass(pmfs["total"]), total),
            "interval_coverage": {"margin": _coverage(summary.get("intervals") or {}, "margin", margin),
                                  "total": _coverage(summary.get("intervals") or {}, "total", total),
                                  "home": _coverage(summary.get("intervals") or {}, "home", home),
                                  "away": _coverage(summary.get("intervals") or {}, "away", away)},
            "detail": {"draw_count": forecast.joint_draw_count,
                       "one_point_final": abs(margin) == 1,
                       "one_point_probability": common.pmf_probability(pmfs["margin"], lambda m: abs(m) == 1),
                       "expected_points_source": "the joint population's own means (not Ridge)"}}
    return rows


def _strongest_final(session: Session, game_id: Any):
    from app.cfb.final_ingest import strongest_final

    return strongest_final(session, game_id)


def _frozen_production(session: Session, game) -> tuple[Any, list[dict[str, Any]]]:
    from app.models import NbaGen1Forecast

    tipoff = common.aware(game.starts_at)
    excluded, eligible = [], []
    for row in session.scalars(select(NbaGen1Forecast).where(NbaGen1Forecast.canonical_game_id == game.id)):
        published = common.aware(row.exchange_published_utc)
        if published is None:
            excluded.append({"forecast_id": row.forecast_id, "reason": "NO_PUBLICATION_CLOCK"})
        elif tipoff is None or published >= tipoff:
            excluded.append({"forecast_id": row.forecast_id, "reason": "PUBLISHED_AT_OR_AFTER_TIPOFF"})
        else:
            eligible.append(row)
    eligible.sort(key=lambda r: (common.aware(r.exchange_published_utc), common.aware(r.imported_at)))
    return (eligible[-1] if eligible else None), excluded


def evaluate(session: Session, *, now: datetime | None = None, evidence: tuple[str, ...] = (
        EVIDENCE_PROSPECTIVE, EVIDENCE_HISTORICAL)) -> dict[str, Any]:
    """Score every FINAL NBA game AISP holds a forecast for, channel by channel. Safe to rerun."""

    from app.models import Game, GameStatus, League, NbaChannelEvaluation, NbaFinalObservation, NbaHistoricalForecast

    moment = now or datetime.now(UTC)
    games = list(session.scalars(select(Game).join(League, League.id == Game.league_id).where(
        League.code == common.LEAGUE_CODE, Game.status == GameStatus.FINAL)))
    summary: dict[str, Any] = {"final_games": len(games), "written": 0, "reused": 0, "skipped": [],
                               "by_class": {EVIDENCE_PROSPECTIVE: 0, EVIDENCE_HISTORICAL: 0},
                               "scoring_version": SCORING_VERSION}
    for game in games:
        observation = _strongest_final(session, game.id)
        if observation is None:
            summary["skipped"].append({"game_id": str(game.id), "skipped": "NO_AUTHORITATIVE_FINAL"})
            continue
        home, away = int(observation.home_score), int(observation.away_score)
        final = session.scalar(select(NbaFinalObservation).where(
            NbaFinalObservation.game_state_observation_id == observation.id))
        overtime = final.overtime if final is not None else None
        result_identity = f"{observation.source}:{observation.payload_checksum}"
        targets: list[tuple[str, Any, list]] = []
        if EVIDENCE_PROSPECTIVE in evidence:
            row, excluded = _frozen_production(session, game)
            if row is not None:
                targets.append((EVIDENCE_PROSPECTIVE, row, excluded))
        if EVIDENCE_HISTORICAL in evidence:
            for row in session.scalars(select(NbaHistoricalForecast).where(
                    NbaHistoricalForecast.canonical_game_id == game.id)):
                targets.append((EVIDENCE_HISTORICAL, row, []))
        if not targets:
            summary["skipped"].append({"game_id": str(game.id), "skipped": "NO_SCORABLE_FORECAST"})
            continue
        for evidence_class, row, excluded in targets:
            prospective = evidence_class == EVIDENCE_PROSPECTIVE
            identity_key = row.forecast_id if prospective else f"historical:{row.assessment_receipt_sha256[:16]}:{row.codex_game_id}"
            for channel, payload in channel_rows(row, home=home, away=away).items():
                known = session.scalar(select(NbaChannelEvaluation.id).where(
                    NbaChannelEvaluation.forecast_identity == identity_key, NbaChannelEvaluation.channel == channel,
                    NbaChannelEvaluation.result_identity == result_identity))
                if known is not None:
                    summary["reused"] += 1
                    continue
                detail = dict(payload.pop("detail", {}))
                detail.update({"excluded": excluded, "evidence_label": (
                    "PROSPECTIVE production package" if prospective else "HISTORICAL · 2025-26 reconstruction"),
                    "result_source": observation.source})
                session.add(NbaChannelEvaluation(
                    evidence_class=evidence_class, channel=channel, forecast_identity=identity_key,
                    nba_gen1_forecast_id=row.id if prospective else None,
                    nba_historical_forecast_id=None if prospective else row.id,
                    canonical_game_id=game.id, source_game_id=row.source_game_id, start_utc=game.starts_at,
                    forecast_published_utc=row.exchange_published_utc if prospective else None,
                    result_identity=result_identity, result_source=str(observation.source),
                    home_score=home, away_score=away, overtime=overtime, detail=detail,
                    scoring_version=SCORING_VERSION, evaluated_at=moment, **payload))
                summary["written"] += 1
                summary["by_class"][evidence_class] += 1
    session.flush()
    return summary


__all__ = ["SCORING_VERSION", "channel_rows", "evaluate"]
