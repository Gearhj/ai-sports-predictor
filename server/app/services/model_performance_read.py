"""The consolidated Model Performance read model, one league at a time.

Reads only CURRENT rows of ``research_forecast_evaluations`` (see
``forecast_evaluation_read`` for why superseded rows are excluded) and derives
every aggregate within one (engine, engine version) identity. It writes
nothing, calls no provider and executes no model.

Metric definitions travel with the payload because they differ in ways a page
must not paper over:

* ``brier_three_class`` is the stored score: squared error summed over home,
  away and tie (range 0-2). For a sport with no tie it is exactly twice the
  two-way Brier.
* ``brier_two_way`` / ``log_loss_two_way`` / ``auc`` use the model's two-way
  conditional home probability, home / (home + away), on games that did not
  tie. Only these are compared with the sportsbook, whose no-vig two-way
  probability is scored the same way on the same games.
* Leagues are never pooled and never ranked against each other: the endpoint
  takes one league.

Publication-clock cohorts are kept apart: a forecast whose publication time is
the evidence cutoff under another name is scored, but counted separately from
one with an independently evidenced publication time.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from math import log
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ResearchForecastEvaluation
from app.services.forecast_evaluation import CLOCK_VERIFIED
from app.services.forecast_evaluation_read import ForecastEvaluationReadService

SCHEMA_VERSION = "AISP_MODEL_PERFORMANCE_V1"
LEAGUES = ("MLB", "NFL", "NBA", "CFB")
EPSILON = 1e-15
CALIBRATION_BINS = 10
EASTERN = ZoneInfo("America/New_York")

CONVENTIONS = {
    "brier_three_class": "Squared error summed over home, away and tie (range 0 to 2; lower is better). "
                         "With no tie it equals twice the two-way Brier.",
    "brier_two_way": "Squared error of the two-way home-win probability, home / (home + away) (range 0 to 1), "
                     "on games without a tie.",
    "log_loss": "Negative log of the probability given to the actual outcome, as scored (three-way).",
    "log_loss_two_way": "Negative log of the two-way probability of the actual winner, on games without a tie.",
    "auc": "Area under the ROC curve of the two-way home-win probability; undefined unless both home and away wins occur.",
    "signed_error": "Predicted minus actual. Margin is home minus away.",
    "market": "Sportsbook no-vig two-way probability from the last eligible recorded pre-start quote -- not a closing line.",
}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def _two_way_home(row: ResearchForecastEvaluation) -> float | None:
    probs = (row.detail or {}).get("published_probabilities") or {}
    home, away = probs.get("home"), probs.get("away")
    if home is None or away is None or (home + away) <= 0:
        return None
    return float(home) / float(home + away)


def _auc(points: list[tuple[float, bool]]) -> float | None:
    positives = [p for p, won in points if won]
    negatives = [p for p, won in points if not won]
    if not positives or not negatives:
        return None
    wins = 0.0
    for p in positives:
        for n in negatives:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(positives) * len(negatives))


def _market_moneyline(row: ResearchForecastEvaluation) -> dict[str, Any] | None:
    if not row.market_available:
        return None
    moneyline = ((row.market_evidence or {}).get("markets") or {}).get("MONEYLINE") or {}
    if not moneyline.get("available") or moneyline.get("no_vig_home_probability") is None:
        return None
    return moneyline


class ModelPerformanceReadService:
    """Read-only."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def league(self, league: str, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        code = league.upper()
        rows = list(self.session.scalars(
            select(ResearchForecastEvaluation)
            .where(ResearchForecastEvaluation.league_code == code,
                   ResearchForecastEvaluation.is_current.is_(True))
            .order_by(ResearchForecastEvaluation.kickoff_utc.asc())
        ))
        by_model: dict[tuple[str, str], list[ResearchForecastEvaluation]] = defaultdict(list)
        for row in rows:
            by_model[(row.engine_id, row.engine_version)].append(row)
        teams = ForecastEvaluationReadService(self.session)._teams(
            {row.canonical_game_id for row in rows if row.canonical_game_id})
        models = [self._model(engine_id, version, entries, teams)
                  for (engine_id, version), entries in by_model.items()]
        models.sort(key=lambda m: m["cohorts"]["ALL"]["n"], reverse=True)
        as_of = max((row.created_at for row in rows if row.created_at is not None), default=None)
        if code == "NBA":
            # NBA scores live per channel in nba_channel_evaluations (a Ridge row has
            # no log loss to put in research_forecast_evaluations). One entry per
            # (evidence class, channel): HISTORICAL rows are never pooled with
            # prospective ones, and the three channels are never averaged.
            from app.nba.gen1_read import performance_models

            models.extend(performance_models(self.session))
        scored = bool(models)
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": _iso(now),
            "league": code,
            "evidence_as_of_utc": _iso(as_of),
            "conventions": CONVENTIONS,
            "models": models,
            "requirement": None if scored else (
                f"No {code} forecast has been scored. A forecast is scored only once its game has an "
                "authoritative final; nothing is scored against an assumed result."
            ),
            "authority": {
                "grants_wagering_authority": False, "promotes_a_model": False, "changes_a_threshold": False,
                "statement": "Outcome evaluation of research forecasts. It changes no model, policy or position.",
            },
        }

    # -- internals --------------------------------------------------------
    def _model(self, engine_id: str, version: str, entries: list[ResearchForecastEvaluation],
               teams: dict[Any, dict[str, Any]]) -> dict[str, Any]:
        verified = [row for row in entries if self._clock(row) == CLOCK_VERIFIED]
        return {
            "engine_id": engine_id,
            "engine_version": version,
            "model_label": entries[0].model_label or engine_id,
            "scoring_version": entries[0].scoring_version,
            "snapshot_types": sorted({row.snapshot_type for row in entries if row.snapshot_type}),
            "evidence_classification": "PROSPECTIVE_RESEARCH_SCORES",
            "cohorts": {
                "ALL": self._metrics(entries),
                "VERIFIED_PUBLICATION": self._metrics(verified),
            },
            "cohort_note": (
                f"{len(verified)} of {len(entries)} scored forecasts have an independently evidenced publication "
                "time. The rest are scored under the same published-before-start rule but their publication "
                "time equals their evidence cutoff, so they are held as a qualified, diagnostic cohort."
            ),
            "games": [self._game(row, teams) for row in reversed(entries)],
        }

    @staticmethod
    def _clock(row: ResearchForecastEvaluation) -> str | None:
        return ((row.detail or {}).get("publication_clock") or {}).get("classification")

    def _metrics(self, entries: list[ResearchForecastEvaluation]) -> dict[str, Any]:
        def mean(values: list[float]) -> float | None:
            return fmean(values) if values else None

        two_way: list[tuple[ResearchForecastEvaluation, float, bool]] = []
        for row in entries:
            p = _two_way_home(row)
            if p is None or row.actual_outcome not in ("HOME", "AWAY"):
                continue
            two_way.append((row, p, row.actual_outcome == "HOME"))

        brier2 = [(p - (1.0 if won else 0.0)) ** 2 for _, p, won in two_way]
        logloss2 = [-log(max(p if won else 1 - p, EPSILON)) for _, p, won in two_way]

        matched = []
        for row, p, won in two_way:
            market = _market_moneyline(row)
            if market is None:
                continue
            q = float(market["no_vig_home_probability"])
            y = 1.0 if won else 0.0
            matched.append({
                "model_brier": (p - y) ** 2, "market_brier": (q - y) ** 2,
                "model_log_loss": -log(max(p if won else 1 - p, EPSILON)),
                "market_log_loss": -log(max(q if won else 1 - q, EPSILON)),
            })
        market_block = None
        if matched:
            mb, kb = fmean(m["model_brier"] for m in matched), fmean(m["market_brier"] for m in matched)
            ml, kl = fmean(m["model_log_loss"] for m in matched), fmean(m["market_log_loss"] for m in matched)
            market_block = {
                "n": len(matched), "model_brier_two_way": mb, "market_brier_two_way": kb, "delta_brier": mb - kb,
                "model_log_loss_two_way": ml, "market_log_loss_two_way": kl, "delta_log_loss": ml - kl,
            }

        bins: list[dict[str, Any]] = []
        for index in range(CALIBRATION_BINS):
            lo, hi = index / CALIBRATION_BINS, (index + 1) / CALIBRATION_BINS
            members = [(p, won) for _, p, won in two_way if lo <= p < hi or (index == CALIBRATION_BINS - 1 and p == 1.0)]
            if members:
                bins.append({"lower": lo, "upper": hi, "n": len(members),
                             "mean_predicted": fmean(p for p, _ in members),
                             "observed": sum(1 for _, won in members if won) / len(members)})

        daily: dict[str, list[tuple[ResearchForecastEvaluation, float, bool]]] = defaultdict(list)
        for item in two_way:
            kickoff = item[0].kickoff_utc
            key = (kickoff if kickoff.tzinfo else kickoff.replace(tzinfo=UTC)).astimezone(EASTERN).date().isoformat()
            daily[key].append(item)
        series, seen_b, seen_l, seen_m, seen_k, count, mcount = [], 0.0, 0.0, 0.0, 0.0, 0, 0
        for day in sorted(daily):
            for row, p, won in daily[day]:
                y = 1.0 if won else 0.0
                seen_b += (p - y) ** 2
                seen_l += -log(max(p if won else 1 - p, EPSILON))
                count += 1
                market = _market_moneyline(row)
                if market is not None:
                    q = float(market["no_vig_home_probability"])
                    seen_m += (p - y) ** 2
                    seen_k += (q - y) ** 2
                    mcount += 1
            series.append({
                "date": day, "games": len(daily[day]), "cumulative_n": count,
                "cumulative_brier_two_way": seen_b / count, "cumulative_log_loss_two_way": seen_l / count,
                "cumulative_market_n": mcount,
                "cumulative_model_brier_matched": seen_m / mcount if mcount else None,
                "cumulative_market_brier_matched": seen_k / mcount if mcount else None,
            })

        kickoffs = [row.kickoff_utc for row in entries if row.kickoff_utc is not None]
        return {
            "n": len(entries),
            "period": {"first_start_utc": _iso(min(kickoffs)) if kickoffs else None,
                       "last_start_utc": _iso(max(kickoffs)) if kickoffs else None},
            "accuracy": (sum(1 for row in entries if row.winner_correct) / len(entries)) if entries else None,
            "correct_winners": sum(1 for row in entries if row.winner_correct),
            "log_loss": mean([row.log_loss for row in entries if row.log_loss is not None]),
            "brier_three_class": mean([row.brier_score for row in entries if row.brier_score is not None]),
            "two_way_n": len(two_way),
            "brier_two_way": mean(brier2),
            "log_loss_two_way": mean(logloss2),
            "auc": _auc([(p, won) for _, p, won in two_way]),
            "margin_mae": mean([abs(row.margin_error) for row in entries if row.margin_error is not None]),
            "total_mae": mean([abs(row.total_error) for row in entries if row.total_error is not None]),
            "margin_errors": [row.margin_error for row in entries if row.margin_error is not None],
            "total_errors": [row.total_error for row in entries if row.total_error is not None],
            "market": market_block,
            "calibration": bins,
            "series": series,
        }

    def _game(self, row: ResearchForecastEvaluation, teams: dict[Any, dict[str, Any]]) -> dict[str, Any]:
        side = teams.get(row.canonical_game_id, {})
        market = _market_moneyline(row)
        p = _two_way_home(row)
        return {
            "evaluation_id": str(row.id),
            "canonical_game_id": str(row.canonical_game_id) if row.canonical_game_id else None,
            "start_utc": _iso(row.kickoff_utc),
            "home": (side.get("home") or {}).get("abbreviation"),
            "away": (side.get("away") or {}).get("abbreviation"),
            "final": {"home": row.home_score, "away": row.away_score},
            "home_probability_two_way": p,
            "predicted_outcome": row.predicted_outcome,
            "actual_outcome": row.actual_outcome,
            "winner_correct": row.winner_correct,
            "log_loss": row.log_loss,
            "brier_three_class": row.brier_score,
            "predicted_score": {"home": row.predicted_home_score, "away": row.predicted_away_score},
            "margin_error": row.margin_error,
            "total_error": row.total_error,
            "market_no_vig_home": float(market["no_vig_home_probability"]) if market else None,
            "market_book": market.get("book") if market else None,
            "publication_clock": self._clock(row),
            "published_before_start_hours": (
                round(row.published_before_kickoff_seconds / 3600, 2)
                if row.published_before_kickoff_seconds is not None else None),
        }


__all__ = ["ModelPerformanceReadService", "SCHEMA_VERSION", "CONVENTIONS", "LEAGUES"]
