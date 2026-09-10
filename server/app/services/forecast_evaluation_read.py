"""The read model behind the NFL prospective-results section.

TWO RULES THIS SERVICE ENFORCES BEFORE ANY NUMBER LEAVES IT
-----------------------------------------------------------
Models are never pooled. Every aggregate is computed within one
(engine, engine version) identity, because averaging M1 with a different model
produces a number that describes neither and would quietly reward whichever
model happened to be scored more often.

n travels with every aggregate. A mean over one game is a fact about one game;
the page cannot describe it honestly without knowing how many games are behind
it, so the count is part of the payload rather than something the client is
trusted to remember.

AND ONE RULE ABOUT WHICH ROWS EXIST AT ALL
------------------------------------------
``research_forecast_evaluations`` is append-only, so one game can hold several
honest evaluations of the same forecast: the one scored against a human's
box-score entry, and the one scored against the automatic feed that later
confirmed it. Both are true and both are kept. Only the CURRENT one is read.

Reading all of them would report two scored games where one was played -- a
count inflated purely by provenance improving, which is the most misleading
possible direction for a number to move.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from statistics import fmean
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameTeam, League, ResearchForecastEvaluation, ResearchSupplement,
    Season, ShadowForecast, Team,
)
from app.services import result_provenance as provenance
from app.services.forecast_evaluation import LEAGUE, NON_NUMERICAL_KINDS, SCORING_VERSION

SCHEMA_VERSION = "AISP_NFL_PROSPECTIVE_RESULTS_V1"

#: The first formal review checkpoint, unchanged. Results are shown from the
#: first game -- waiting for fifty would hide the evidence that exists -- but
#: nothing is reviewed against policy until the sample reaches it.
REVIEW_CHECKPOINT_GAMES = 50

SAMPLE_NOTE = (
    "These are descriptive results for the games scored so far. With this sample they "
    "establish nothing about whether one model is better than another, and nothing here "
    "promotes a model, changes a threshold or grants wagering authority."
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


class ForecastEvaluationReadService:
    """Read-only. Writes nothing and calls no provider."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def nfl_prospective(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        rows = list(self.session.scalars(
            select(ResearchForecastEvaluation)
            .where(ResearchForecastEvaluation.league_code == LEAGUE,
                   ResearchForecastEvaluation.is_current.is_(True))
            .order_by(ResearchForecastEvaluation.kickoff_utc.desc())
        ))
        superseded = self.session.scalar(
            select(func.count()).select_from(ResearchForecastEvaluation)
            .where(ResearchForecastEvaluation.league_code == LEAGUE,
                   ResearchForecastEvaluation.is_current.is_(False))
        ) or 0
        by_model: dict[tuple[str, str], list[ResearchForecastEvaluation]] = defaultdict(list)
        for row in rows:
            by_model[(row.engine_id, row.engine_version)].append(row)

        teams = self._teams({row.canonical_game_id for row in rows if row.canonical_game_id})
        models = [
            self._model_summary(engine_id, engine_version, entries, teams)
            for (engine_id, engine_version), entries in sorted(by_model.items())
        ]
        models.sort(key=lambda entry: entry["scored_games"], reverse=True)

        return {
            "schema_version": SCHEMA_VERSION,
            "data_mode": "live",
            "generated_at_utc": _iso(now),
            "league": LEAGUE,
            "scoring_version": SCORING_VERSION,
            "models": models,
            "total_scored_games": len({row.event_id for row in rows}),
            # Kept for audit, deliberately excluded from every metric above.
            "superseded_evaluations": superseded,
            "provenance_note": (
                "One game can hold several evaluations as its result gains better provenance. "
                "Every one is retained, and exactly one is current; counts and averages read "
                f"only the current row, so n cannot rise because a source improved. "
                f"{superseded} superseded evaluation(s) are held for audit."
            ),
            "review_checkpoint_games": REVIEW_CHECKPOINT_GAMES,
            "sample_note": SAMPLE_NOTE,
            "inventory": self._inventory(),
            "authority": {
                "grants_wagering_authority": False,
                "promotes_a_model": False,
                "changes_a_threshold": False,
                "creates_a_position": False,
                "statement": "Outcome evaluation of research forecasts. NFL creates no position "
                             "and holds no wagering authority.",
            },
            "requirement": None if rows else (
                "No NFL forecast has been scored yet. A forecast is scored once its game has an "
                "authoritative final recorded; nothing is scored against an assumed result."
            ),
        }

    # -- internals --------------------------------------------------------
    def _model_summary(
        self, engine_id: str, engine_version: str,
        entries: list[ResearchForecastEvaluation], teams: dict[Any, dict[str, Any]],
    ) -> dict[str, Any]:
        def mean_of(attribute: str) -> float | None:
            values = [getattr(row, attribute) for row in entries
                      if getattr(row, attribute) is not None]
            return fmean(values) if values else None

        def mean_abs(attribute: str) -> float | None:
            values = [abs(getattr(row, attribute)) for row in entries
                      if getattr(row, attribute) is not None]
            return fmean(values) if values else None

        with_distribution = [row for row in entries if row.distribution_available]
        with_market = [row for row in entries if row.market_available]
        return {
            "engine_id": engine_id,
            "engine_version": engine_version,
            "model_label": entries[0].model_label or engine_id,
            "scored_games": len(entries),
            "scoreboard": {
                "scored_games": len(entries),
                "correct_winners": sum(1 for row in entries if row.winner_correct),
                "winner_accuracy": (
                    sum(1 for row in entries if row.winner_correct) / len(entries)
                    if entries else None
                ),
                "average_log_loss": mean_of("log_loss"),
                "average_brier_score": mean_of("brier_score"),
                "margin_mae": mean_abs("margin_error"),
                "total_mae": mean_abs("total_error"),
                "team_score_mae": mean_of("team_score_mae"),
                "average_probability_of_actual": mean_of("probability_of_actual"),
                # Sample sizes are separate because they are different
                # denominators: not every scored game has a distribution, and
                # far fewer have a comparable market.
                "distribution_sample": len(with_distribution),
                "market_comparison_sample": len(with_market),
                "average_crps_margin": (
                    fmean([row.crps_margin for row in with_distribution if row.crps_margin is not None])
                    if with_distribution else None
                ),
                "average_crps_total": (
                    fmean([row.crps_total for row in with_distribution if row.crps_total is not None])
                    if with_distribution else None
                ),
                "average_energy_score": (
                    fmean([row.energy_score for row in with_distribution if row.energy_score is not None])
                    if with_distribution else None
                ),
                "interval_coverage": self._coverage(with_distribution),
            },
            "games": [self._game(row, teams) for row in entries],
        }

    def _superseded_count(self, row: ResearchForecastEvaluation) -> int:
        """How many earlier evaluations this one replaced. Usually zero."""

        return self.session.scalar(
            select(func.count()).select_from(ResearchForecastEvaluation)
            .where(ResearchForecastEvaluation.superseded_by_id == row.id)
        ) or 0

    @staticmethod
    def _coverage(entries: list[ResearchForecastEvaluation]) -> dict[str, Any] | None:
        """Containment RATE across games, with its own n stated."""

        if not entries:
            return None
        out: dict[str, Any] = {"sample": len(entries)}
        for quantity in ("margin", "total"):
            levels: dict[str, Any] = {}
            for level in ("50", "80", "90"):
                hits = [
                    bool(((row.interval_coverage or {}).get(quantity) or {}).get(level, {}).get("contains"))
                    for row in entries
                    if ((row.interval_coverage or {}).get(quantity) or {}).get(level) is not None
                ]
                levels[level] = {
                    "contained": sum(hits), "of": len(hits),
                    "rate": (sum(hits) / len(hits)) if hits else None,
                }
            out[quantity] = levels
        out["note"] = ("Containment rate across scored games. At this sample it is a count, not "
                       "a calibration result.")
        return out

    def _game(
        self, row: ResearchForecastEvaluation, teams: dict[Any, dict[str, Any]],
    ) -> dict[str, Any]:
        side = teams.get(row.canonical_game_id, {})
        return {
            "evaluation_id": str(row.id),
            "event_id": row.event_id,
            "canonical_game_id": str(row.canonical_game_id) if row.canonical_game_id else None,
            "home": side.get("home"), "away": side.get("away"),
            "kickoff_utc": _iso(row.kickoff_utc),
            "final": {"home": row.home_score, "away": row.away_score},
            "forecast": {
                "identity": row.forecast_identity,
                "snapshot_type": row.snapshot_type,
                "snapshot_version": row.snapshot_version,
                "frozen_published_utc": _iso(row.forecast_published_utc),
                "evidence_utc": _iso(row.forecast_evidence_utc),
                "imported_utc": _iso(row.forecast_imported_utc),
                "hours_before_kickoff": (
                    round(row.published_before_kickoff_seconds / 3600, 2)
                    if row.published_before_kickoff_seconds is not None else None
                ),
                "engine_id": row.engine_id, "engine_version": row.engine_version,
            },
            "result": {
                "identity": row.result_identity, "source": row.result_source,
                "observed_utc": _iso(row.result_observed_utc),
                **provenance.describe(row.result_source),
                "superseded_count": self._superseded_count(row),
            },
            "outcome": {
                "actual": row.actual_outcome, "predicted": row.predicted_outcome,
                "winner_correct": row.winner_correct,
                "probability_of_actual": row.probability_of_actual,
                "log_loss": row.log_loss, "brier_score": row.brier_score,
                "published_probabilities": (row.detail or {}).get("published_probabilities"),
            },
            "scores": {
                "predicted_home": row.predicted_home_score,
                "predicted_away": row.predicted_away_score,
                "home_error": row.home_score_error, "away_error": row.away_score_error,
                "team_score_mae": row.team_score_mae,
            },
            "margin": {
                "predicted": row.predicted_margin, "actual": row.actual_margin,
                "error": row.margin_error,
            },
            "total": {
                "predicted": row.predicted_total, "actual": row.actual_total,
                "error": row.total_error,
            },
            "distribution": {
                "available": row.distribution_available,
                "reason": row.distribution_unavailable_reason,
                "draws": row.distribution_draws,
                "crps_home": row.crps_home, "crps_away": row.crps_away,
                "crps_margin": row.crps_margin, "crps_total": row.crps_total,
                "energy_score": row.energy_score,
                "interval_coverage": row.interval_coverage,
            },
            "market": {
                "available": row.market_available,
                "reason": row.market_unavailable_reason,
                "evidence": row.market_evidence,
            },
            "excluded_snapshots": (row.detail or {}).get("excluded") or [],
            "snapshots_considered": (row.detail or {}).get("snapshots_considered"),
        }

    def _teams(self, game_ids: set[Any]) -> dict[Any, dict[str, Any]]:
        if not game_ids:
            return {}
        rows = list(self.session.execute(
            select(GameTeam.game_id, GameTeam.is_home, Team.abbreviation, Team.name)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id.in_(game_ids))
        ).all())
        out: dict[Any, dict[str, Any]] = defaultdict(dict)
        for game_id, is_home, abbreviation, name in rows:
            out[game_id]["home" if is_home else "away"] = {
                "abbreviation": abbreviation, "name": name,
            }
        return dict(out)

    def _inventory(self) -> dict[str, Any]:
        """What NFL forecast evidence exists, and what of it is scoreable.

        A score-distribution supplement, a drive replay and a play-level
        population are evidence ABOUT a forecast. Listing them beside the
        numerical forecasts -- and saying plainly that they are not scored as
        forecasts -- is the difference between "we only scored one model" and
        "we only have one model to score".
        """

        numerical = list(self.session.execute(
            select(
                ShadowForecast.engine_id, ShadowForecast.engine_version,
                func.count(ShadowForecast.id), func.count(func.distinct(ShadowForecast.event_id)),
                func.min(ShadowForecast.available_as_of_utc),
                func.max(ShadowForecast.available_as_of_utc),
            )
            .join(Game, Game.id == ShadowForecast.canonical_game_id)
            .join(Season, Season.id == Game.season_id)
            .join(League, League.id == Season.league_id)
            .where(League.code == LEAGUE)
            .group_by(ShadowForecast.engine_id, ShadowForecast.engine_version)
            .order_by(ShadowForecast.engine_id)
        ).all())
        supplements = list(self.session.execute(
            select(
                ResearchSupplement.kind, ResearchSupplement.engine_id,
                func.count(ResearchSupplement.id),
                func.count(func.distinct(ResearchSupplement.event_id)),
            )
            .where(ResearchSupplement.sport == "NFL")
            .group_by(ResearchSupplement.kind, ResearchSupplement.engine_id)
            .order_by(ResearchSupplement.kind)
        ).all())
        final_games = self.session.scalar(
            select(func.count()).select_from(Game)
            .join(Season, Season.id == Game.season_id)
            .join(League, League.id == Season.league_id)
            .where(League.code == LEAGUE, Game.home_score.is_not(None))
        )
        return {
            "numerical_forecast_models": [
                {
                    "engine_id": engine_id, "engine_version": engine_version,
                    "snapshots": snapshots, "events": events,
                    "first_published_utc": _iso(first), "last_published_utc": _iso(last),
                    "scoreable": True,
                }
                for engine_id, engine_version, snapshots, events, first, last in numerical
            ],
            "supporting_evidence": [
                {
                    "kind": kind, "engine_id": engine_id, "records": records, "events": events,
                    "scoreable_as_a_forecast": False,
                    "why": ("A score distribution, a drive replay and a play-level population "
                            "are evidence about a forecast, not forecasts. They publish no "
                            "independent outcome probability to score."),
                }
                for kind, engine_id, records, events in supplements
                if kind in NON_NUMERICAL_KINDS
            ],
            "nfl_games_with_a_final": int(final_games or 0),
        }


__all__ = ["ForecastEvaluationReadService", "REVIEW_CHECKPOINT_GAMES", "SAMPLE_NOTE", "SCHEMA_VERSION"]
