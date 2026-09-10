"""Scoring prospective NFL research forecasts against final results.

WHAT THIS IS, AND WHAT IT IS EMPHATICALLY NOT
---------------------------------------------
Outcome evaluation. It measures how good a forecast was after the game
finished. It is not wagering activation, not model promotion, not threshold
work and not a modelling study: nothing here fits a model, creates a decision,
opens a position or touches a settlement, and NFL still has no wagering
authority when this is done.

THE ONE RULE THAT MAKES THE NUMBER MEAN ANYTHING
------------------------------------------------
A prospective score is only prospective if the forecast existed before the
game did. Every eligibility decision here reduces to that: the frozen forecast
is the latest snapshot whose PUBLICATION time precedes kickoff, per model
identity, and a snapshot published one second after kickoff is excluded with
that reason recorded rather than quietly dropped. The result may never
participate in choosing which forecast to score -- if it could, the evaluation
would be measuring hindsight.

Ten stored snapshots of one game's forecast are ONE forecast, not ten games.
Selection is per (event, engine, engine version), and the row that wins is the
one an observer would have been looking at when the ball was kicked.

WHAT IS SCORED, AND WHAT IS REFUSED
-----------------------------------
Only NUMERICAL forecasts: something that publishes outcome probabilities and
expected scores. A score-distribution supplement, a scenario sensitivity run
and a play/drive visualisation are evidence ABOUT a forecast, not forecasts,
and none of them is scored as one. The joint distribution attached to a
numerical forecast is used for the distribution metrics; when it is absent the
metric is unavailable with a reason, never estimated from the summary.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStateObservation, GameStatus, GameTeam, League, Market, MarketOutcome,
    OddsSnapshot, ResearchForecastEvaluation, Season, ShadowForecast, Sportsbook, Team,
)
from app.services import forecast_scoring as scoring
from app.services.forecast_scoring import (
    HOME, AWAY, TIE, JointMass, MassFunction, OutcomeProbabilities, PointErrors,
)

SCORING_VERSION = "AISP_NFL_PROSPECTIVE_SCORING_V1"
SPORT = "FOOTBALL"
LEAGUE = "NFL"

#: Supplement kinds that are evidence about a forecast rather than forecasts.
NON_NUMERICAL_KINDS = (
    "JOINT_SCORE_SIMULATION", "DRIVE_SIMULATION", "SCENARIO_COMPARISON",
    "PLAY_LEVEL_SIMULATION", "MODEL_BENCHMARK_STUDY",
)

#: Exclusion reasons, so a skipped forecast always says why.
PUBLISHED_AFTER_KICKOFF = "PUBLISHED_AFTER_KICKOFF"
NO_KICKOFF = "NO_SCHEDULED_KICKOFF"
NO_PROBABILITIES = "NO_OUTCOME_PROBABILITIES"
SUPERSEDED = "SUPERSEDED_BY_A_LATER_PRE_KICKOFF_SNAPSHOT"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    moment = _aware(value)
    return moment.isoformat() if moment else None


@dataclass(frozen=True)
class FrozenForecast:
    """The one snapshot that will be scored, with its original provenance."""

    row: ShadowForecast
    kickoff: datetime
    considered: int
    excluded: list[dict[str, Any]]

    @property
    def lead_seconds(self) -> int:
        published = _aware(self.row.available_as_of_utc)
        return int((self.kickoff - published).total_seconds()) if published else 0


@dataclass(frozen=True)
class GameResult:
    """The final, and where it came from. Identity is the observation, not the game."""

    game: Game
    home_score: int
    away_score: int
    identity: str
    source: str
    observed_at: datetime | None


class ForecastEvaluationService:
    """Freezes eligible forecasts, scores them, and writes at most one row each."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- eligibility ------------------------------------------------------
    def freeze_eligible(self, *, canonical_game_id: UUID) -> dict[str, FrozenForecast]:
        """The latest pre-kickoff forecast per model identity, plus exclusions.

        The result is deliberately not a parameter. Selection that could see
        the final score is not forecast selection, it is retrodiction.
        """

        rows = list(self.session.scalars(
            select(ShadowForecast)
            .where(ShadowForecast.canonical_game_id == canonical_game_id)
            .order_by(ShadowForecast.available_as_of_utc, ShadowForecast.snapshot_version)
        ))
        by_identity: dict[str, list[ShadowForecast]] = defaultdict(list)
        for row in rows:
            by_identity[f"{row.engine_id}::{row.engine_version}"].append(row)

        frozen: dict[str, FrozenForecast] = {}
        for identity, snapshots in by_identity.items():
            excluded: list[dict[str, Any]] = []
            eligible: list[ShadowForecast] = []
            for snapshot in snapshots:
                kickoff = _aware(snapshot.scheduled_start_utc)
                published = _aware(snapshot.available_as_of_utc)
                if kickoff is None:
                    excluded.append(self._exclusion(snapshot, NO_KICKOFF,
                                                    "The snapshot records no scheduled kickoff."))
                    continue
                if published is None or published >= kickoff:
                    excluded.append(self._exclusion(
                        snapshot, PUBLISHED_AFTER_KICKOFF,
                        f"Published {_iso(published)} at or after kickoff {_iso(kickoff)}; "
                        "a forecast that could see the game is not a prospective forecast.",
                    ))
                    continue
                if snapshot.full_home_win_probability is None or snapshot.full_away_win_probability is None:
                    excluded.append(self._exclusion(
                        snapshot, NO_PROBABILITIES,
                        "The snapshot publishes no home/away win probabilities, so no "
                        "outcome scoring rule applies to it.",
                    ))
                    continue
                eligible.append(snapshot)
            if not eligible:
                continue
            chosen = max(eligible, key=lambda row: (
                _aware(row.available_as_of_utc), row.snapshot_version or 0,
            ))
            for snapshot in eligible:
                if snapshot.id != chosen.id:
                    excluded.append(self._exclusion(
                        snapshot, SUPERSEDED,
                        f"A later pre-kickoff snapshot ({chosen.snapshot_type} v"
                        f"{chosen.snapshot_version}) is what an observer would have seen at kickoff.",
                    ))
            frozen[identity] = FrozenForecast(
                row=chosen, kickoff=_aware(chosen.scheduled_start_utc),
                considered=len(snapshots), excluded=excluded,
            )
        return frozen

    @staticmethod
    def _exclusion(row: ShadowForecast, reason: str, detail: str) -> dict[str, Any]:
        return {
            "forecast_identity": row.forecast_id,
            "snapshot_type": row.snapshot_type,
            "snapshot_version": row.snapshot_version,
            "published_utc": _iso(row.available_as_of_utc),
            "reason": reason,
            "detail": detail,
        }

    # -- results ----------------------------------------------------------
    def result_for(self, game: Game) -> GameResult | None:
        """The final, identified by the observation that recorded it.

        The game row carries the current score, but its identity does not
        change when a correction lands; the observation's checksum does, which
        is what makes a corrected final a NEW evaluation rather than a silent
        overwrite of the old one.
        """

        if game.status != GameStatus.FINAL or game.home_score is None or game.away_score is None:
            return None
        observation = self.session.scalars(
            select(GameStateObservation)
            .where(
                GameStateObservation.game_id == game.id,
                GameStateObservation.status == GameStatus.FINAL.value,
                GameStateObservation.home_score == game.home_score,
                GameStateObservation.away_score == game.away_score,
            )
            .order_by(GameStateObservation.available_as_of.desc())
            .limit(1)
        ).first()
        if observation is None:
            # A score with no observation behind it has no provenance and no
            # stable identity, so it is not scoreable evidence.
            return None
        return GameResult(
            game=game, home_score=int(game.home_score), away_score=int(game.away_score),
            identity=f"{observation.source}:{observation.payload_checksum}",
            source=str(observation.source),
            observed_at=_aware(observation.observed_at),
        )

    # -- scoring ----------------------------------------------------------
    def evaluate_game(
        self, *, canonical_game_id: UUID, dry_run: bool = False, now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        game = self.session.get(Game, canonical_game_id)
        if game is None:
            return {"game_id": str(canonical_game_id), "scored": 0, "skipped": "GAME_NOT_REGISTERED"}
        result = self.result_for(game)
        if result is None:
            return {
                "game_id": str(canonical_game_id), "scored": 0,
                "skipped": "NO_AUTHORITATIVE_FINAL",
                "detail": "The game is not FINAL with a score backed by a state observation.",
            }

        frozen = self.freeze_eligible(canonical_game_id=canonical_game_id)
        written, reused, records = 0, 0, []
        for identity, selection in frozen.items():
            payload = self._score(selection, result, now=now)
            existing = self.session.scalar(select(ResearchForecastEvaluation).where(
                ResearchForecastEvaluation.event_id == payload["event_id"],
                ResearchForecastEvaluation.engine_id == payload["engine_id"],
                ResearchForecastEvaluation.engine_version == payload["engine_version"],
                ResearchForecastEvaluation.forecast_identity == payload["forecast_identity"],
                ResearchForecastEvaluation.result_identity == payload["result_identity"],
            ))
            if existing is not None:
                reused += 1
                records.append({"identity": identity, "written": False, "evaluation_id": str(existing.id)})
                continue
            if dry_run:
                records.append({"identity": identity, "written": False, "dry_run": True,
                                "log_loss": payload["log_loss"]})
                continue
            row = ResearchForecastEvaluation(**payload)
            self.session.add(row)
            self.session.flush()
            written += 1
            records.append({"identity": identity, "written": True, "evaluation_id": str(row.id)})
        return {
            "game_id": str(canonical_game_id),
            "event_id": next(iter(frozen.values())).row.event_id if frozen else None,
            "models_considered": len(frozen),
            "scored": written, "reused": reused, "records": records,
            "excluded": [entry for selection in frozen.values() for entry in selection.excluded],
        }

    def _score(
        self, selection: FrozenForecast, result: GameResult, *, now: datetime,
    ) -> dict[str, Any]:
        row = selection.row
        probabilities = OutcomeProbabilities(
            home=float(row.full_home_win_probability),
            away=float(row.full_away_win_probability),
            tie=self._tie_probability(row),
        )
        outcome = scoring.actual_class(result.home_score, result.away_score)
        normalised = probabilities.normalised()

        home_points = away_points = margin = total = None
        if row.full_expected_home_score is not None and row.full_expected_away_score is not None:
            home_points = PointErrors(float(row.full_expected_home_score), result.home_score)
            away_points = PointErrors(float(row.full_expected_away_score), result.away_score)
            margin = PointErrors(
                float(row.full_expected_home_score) - float(row.full_expected_away_score),
                result.home_score - result.away_score,
            )
            total = PointErrors(
                float(row.full_expected_home_score) + float(row.full_expected_away_score),
                result.home_score + result.away_score,
            )

        distribution = self._distribution_metrics(row, result)
        market = self._market_comparison(result.game, selection.kickoff, outcome, result)

        return {
            "sport": SPORT, "league_code": LEAGUE,
            "event_id": row.event_id, "canonical_game_id": result.game.id,
            "engine_id": row.engine_id, "engine_version": row.engine_version,
            "model_label": getattr(row, "model_label", None) or row.engine_id,
            "forecast_identity": row.forecast_id, "shadow_forecast_id": row.id,
            "snapshot_type": row.snapshot_type, "snapshot_version": row.snapshot_version,
            "forecast_published_utc": _aware(row.available_as_of_utc),
            "forecast_evidence_utc": _aware(row.source_cutoff_utc),
            "forecast_imported_utc": _aware(row.created_at),
            "kickoff_utc": selection.kickoff,
            "published_before_kickoff_seconds": selection.lead_seconds,
            "result_identity": result.identity, "result_source": result.source,
            "result_observed_utc": result.observed_at,
            "home_score": result.home_score, "away_score": result.away_score,
            "actual_outcome": outcome,
            "predicted_outcome": normalised.predicted_class,
            "winner_correct": normalised.predicted_class == outcome,
            "probability_of_actual": normalised.of(outcome),
            "log_loss": scoring.log_loss(probabilities, outcome),
            "brier_score": scoring.brier_score(probabilities, outcome),
            "predicted_home_score": home_points.predicted if home_points else None,
            "predicted_away_score": away_points.predicted if away_points else None,
            "home_score_error": home_points.signed if home_points else None,
            "away_score_error": away_points.signed if away_points else None,
            "team_score_mae": (
                scoring.mean_absolute_team_score_error(home_points, away_points)
                if home_points and away_points else None
            ),
            "predicted_margin": margin.predicted if margin else None,
            "actual_margin": result.home_score - result.away_score,
            "margin_error": margin.signed if margin else None,
            "predicted_total": total.predicted if total else None,
            "actual_total": result.home_score + result.away_score,
            "total_error": total.signed if total else None,
            **distribution,
            **market,
            "detail": {
                "published_probabilities": {
                    "home": probabilities.home, "away": probabilities.away, "tie": probabilities.tie,
                    "mass_before_normalising": probabilities.mass,
                },
                "snapshots_considered": selection.considered,
                "excluded": selection.excluded,
                "log_loss_epsilon": scoring.LOG_LOSS_EPSILON,
                "signed_error_convention": "predicted minus actual",
            },
            "scoring_version": SCORING_VERSION,
            "evaluated_at": now,
        }

    @staticmethod
    def _tie_probability(row: ShadowForecast) -> float:
        """The published tie, or the residual mass, and never a negative one."""

        distributions = row.distributions or {}
        published = distributions.get("tie_probability")
        if published is not None:
            return float(published)
        residual = 1.0 - float(row.full_home_win_probability) - float(row.full_away_win_probability)
        return max(0.0, residual)

    def _distribution_metrics(self, row: ShadowForecast, result: GameResult) -> dict[str, Any]:
        distributions = row.distributions or {}
        cells = distributions.get("joint_score_distribution") or []
        if not cells:
            return {
                "distribution_available": False,
                "distribution_unavailable_reason":
                    "This forecast publishes no joint score distribution. CRPS, the energy "
                    "score and interval coverage all need real draws, and fitting a curve to "
                    "the published mean would produce numbers that look identical to measured "
                    "ones and measure nothing.",
                "distribution_draws": None,
                "crps_home": None, "crps_away": None, "crps_margin": None, "crps_total": None,
                "energy_score": None, "interval_coverage": None,
            }
        draws = self._draw_count(row)
        joint = JointMass.from_cells(
            ((int(cell["home_score"]), int(cell["away_score"]), float(cell["probability"]))
             for cell in cells),
            draws=draws,
        )
        home_marginal = joint.marginal_home()
        away_marginal = joint.marginal_away()
        margin_marginal = joint.margin()
        total_marginal = joint.total()
        return {
            "distribution_available": True,
            "distribution_unavailable_reason": None,
            "distribution_draws": draws,
            "crps_home": scoring.crps(home_marginal, result.home_score),
            "crps_away": scoring.crps(away_marginal, result.away_score),
            "crps_margin": scoring.crps(margin_marginal, result.home_score - result.away_score),
            "crps_total": scoring.crps(total_marginal, result.home_score + result.away_score),
            "energy_score": scoring.energy_score(joint, (result.home_score, result.away_score)),
            "interval_coverage": {
                "margin": scoring.interval_coverage(
                    margin_marginal, result.home_score - result.away_score),
                "total": scoring.interval_coverage(
                    total_marginal, result.home_score + result.away_score),
                "note": "For one game these are hits and misses. The rate across games is the "
                        "coverage; a single containment is not.",
            },
        }

    def _draw_count(self, row: ShadowForecast) -> int | None:
        """The simulation count the producer published, from its own supplement."""

        from app.models import ResearchSupplement

        return self.session.scalar(
            select(ResearchSupplement.simulation_count).where(
                ResearchSupplement.forecast_id == row.forecast_id,
                ResearchSupplement.kind == "JOINT_SCORE_SIMULATION",
            ).limit(1)
        )

    # -- market -----------------------------------------------------------
    def _market_comparison(
        self, game: Game, kickoff: datetime, outcome: str, result: GameResult,
    ) -> dict[str, Any]:
        """The last eligible same-book two-sided quote observed before kickoff.

        Deliberately NOT called a closing line. A closing line is the last
        price a book actually offered, and nothing in this data establishes
        that a quote observed eleven days early was the last one; calling it
        closing would turn an artefact of our collection schedule into a claim
        about the market.
        """

        families = {"MONEYLINE": {}, "SPREAD": {}, "TOTAL": {}}
        rows = list(self.session.execute(
            select(OddsSnapshot, MarketOutcome, Market, Sportsbook)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
            .where(Market.game_id == game.id, OddsSnapshot.observed_at < kickoff)
            .order_by(OddsSnapshot.observed_at)
        ).all())
        if not rows:
            return {
                "market_available": False,
                "market_unavailable_reason":
                    "No sportsbook quote was ever persisted for this game before kickoff, so "
                    "there is no market to compare against. None is reconstructed.",
                "market_evidence": None,
            }

        home_team_id = self._home_team_id(game)
        best: dict[str, dict[str, Any]] = {}
        # Latest quote per (book, market, line, outcome), forward-filled to
        # kickoff, then paired within one book and one line.
        latest: dict[tuple[Any, str, str, Any], tuple[OddsSnapshot, MarketOutcome]] = {}
        for snapshot, outcome_row, market, book in rows:
            family = str(getattr(market.market_type, "value", market.market_type)).upper()
            if family not in families:
                continue
            line = "" if family == "MONEYLINE" else f"{abs(float(snapshot.point)):.2f}" if snapshot.point is not None else ""
            latest[(book.id, family, line, outcome_row.id)] = (snapshot, outcome_row)

        grouped: dict[tuple[Any, str, str], list[tuple[OddsSnapshot, MarketOutcome]]] = defaultdict(list)
        for (book_id, family, line, _), value in latest.items():
            grouped[(book_id, family, line)].append(value)

        for (book_id, family, line), entries in grouped.items():
            if len(entries) != 2:
                continue
            observed = max(_aware(entry[0].observed_at) for entry in entries)
            current = best.get(family)
            if current is not None and current["observed_at"] >= observed:
                continue
            book = self.session.get(Sportsbook, book_id)
            best[family] = {
                "book_id": book_id, "book": book.name if book else str(book_id),
                "book_key": book.key if book else None,
                "line": line, "entries": entries, "observed_at": observed,
            }

        evidence: dict[str, Any] = {
            "basis": "LAST_ELIGIBLE_RECORDED_PRE_KICKOFF_QUOTE",
            "basis_note": "Not a closing line: nothing in the persisted evidence establishes "
                          "that this was the last price the book offered.",
            "quotes_considered": len(rows),
            "markets": {},
        }
        moneyline = best.get("MONEYLINE")
        if moneyline is not None:
            evidence["markets"]["MONEYLINE"] = self._moneyline_evidence(
                moneyline, home_team_id, outcome, kickoff)
        spread = best.get("SPREAD")
        if spread is not None:
            evidence["markets"]["SPREAD"] = self._handicap_evidence(
                spread, home_team_id, result, kickoff, family="SPREAD")
        total = best.get("TOTAL")
        if total is not None:
            evidence["markets"]["TOTAL"] = self._handicap_evidence(
                total, home_team_id, result, kickoff, family="TOTAL")

        if not evidence["markets"]:
            return {
                "market_available": False,
                "market_unavailable_reason":
                    f"{len(rows)} pre-kickoff quote(s) are persisted, but none forms a complete "
                    "two-sided price at one book on one line, so no comparable market "
                    "probability exists.",
                "market_evidence": evidence,
            }
        return {"market_available": True, "market_unavailable_reason": None, "market_evidence": evidence}

    def _home_team_id(self, game: Game) -> Any:
        return self.session.scalar(
            select(GameTeam.team_id).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(True))
        )

    def _moneyline_evidence(
        self, chosen: dict[str, Any], home_team_id: Any, outcome: str, kickoff: datetime,
    ) -> dict[str, Any]:
        home_entry = away_entry = None
        for snapshot, outcome_row in chosen["entries"]:
            if self._is_home_outcome(outcome_row, home_team_id):
                home_entry = snapshot
            else:
                away_entry = snapshot
        if home_entry is None or away_entry is None or home_entry.american_odds is None or away_entry.american_odds is None:
            return {"available": False,
                    "reason": "The two sides of this quote cannot be resolved to home and away."}
        home_probability, away_probability = scoring.no_vig_two_way(
            int(home_entry.american_odds), int(away_entry.american_odds))
        payload: dict[str, Any] = {
            "available": True,
            "book": chosen["book"], "book_key": chosen["book_key"],
            "observed_at_utc": _iso(chosen["observed_at"]),
            "seconds_before_kickoff": int((kickoff - chosen["observed_at"]).total_seconds()),
            "home_american": int(home_entry.american_odds),
            "away_american": int(away_entry.american_odds),
            "no_vig_home_probability": home_probability,
            "no_vig_away_probability": away_probability,
        }
        if outcome == TIE:
            payload["scored"] = False
            payload["reason"] = ("The game was a tie. A two-way moneyline offers no tie price, "
                                 "so it has no answer to score against this result.")
            return payload
        payload["scored"] = True
        payload["log_loss"] = scoring.two_way_log_loss(home_probability, outcome)
        payload["brier_score"] = scoring.two_way_brier(home_probability, outcome)
        payload["scoring_note"] = ("Two-way market scored against a two-way result. The model's "
                                   "three-way log loss is not comparable to this number directly; "
                                   "the difference reported beside it uses the model's own "
                                   "two-way conditional probability.")
        return payload

    def _handicap_evidence(
        self, chosen: dict[str, Any], home_team_id: Any, result: GameResult,
        kickoff: datetime, *, family: str,
    ) -> dict[str, Any]:
        line = float(chosen["line"]) if chosen["line"] else None
        payload: dict[str, Any] = {
            "available": line is not None,
            "book": chosen["book"], "book_key": chosen["book_key"],
            "observed_at_utc": _iso(chosen["observed_at"]),
            "seconds_before_kickoff": int((kickoff - chosen["observed_at"]).total_seconds()),
            "line": line,
        }
        if line is None:
            payload["reason"] = "The persisted quote carries no line."
            return payload
        if family == "TOTAL":
            payload["actual_total"] = result.home_score + result.away_score
            payload["grade"] = scoring.total_grade(result.home_score + result.away_score, line)
            return payload
        home_handicap = None
        for snapshot, outcome_row in chosen["entries"]:
            if self._is_home_outcome(outcome_row, home_team_id) and snapshot.point is not None:
                home_handicap = float(snapshot.point)
        if home_handicap is None:
            payload["available"] = False
            payload["reason"] = "The home side of this handicap cannot be resolved."
            return payload
        payload["home_handicap"] = home_handicap
        payload["actual_margin"] = result.home_score - result.away_score
        payload["grade"] = scoring.spread_grade(result.home_score - result.away_score, home_handicap)
        return payload

    @staticmethod
    def _is_home_outcome(outcome_row: MarketOutcome, home_team_id: Any) -> bool:
        if outcome_row.participant_team_id is not None and home_team_id is not None:
            return outcome_row.participant_team_id == home_team_id
        key = str(outcome_row.outcome_key or "").upper()
        return key.startswith("HOME") or key.startswith("OVER")

    # -- batch ------------------------------------------------------------
    def evaluate_league(
        self, *, league_code: str = LEAGUE, dry_run: bool = False, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Score every final game in one league. Safe to rerun."""

        now = now or datetime.now(UTC)
        game_ids = list(self.session.scalars(
            select(Game.id)
            .join(Season, Season.id == Game.season_id)
            .join(League, League.id == Season.league_id)
            .where(
                League.code == league_code,
                Game.status == GameStatus.FINAL,
                Game.home_score.is_not(None),
                Game.away_score.is_not(None),
            )
            .order_by(Game.starts_at)
        ))
        results = [self.evaluate_game(canonical_game_id=game_id, dry_run=dry_run, now=now)
                   for game_id in game_ids]
        return {
            "league": league_code,
            "final_games": len(game_ids),
            "scored": sum(entry.get("scored", 0) for entry in results),
            "reused": sum(entry.get("reused", 0) for entry in results),
            "skipped": [entry for entry in results if entry.get("skipped")],
            "games": results,
            "provider_calls": 0, "models_executed": 0,
            "positions_created": 0, "official_decisions_created": 0,
            "scoring_version": SCORING_VERSION,
        }


__all__ = [
    "ForecastEvaluationService", "FrozenForecast", "GameResult", "LEAGUE",
    "NON_NUMERICAL_KINDS", "NO_KICKOFF", "NO_PROBABILITIES", "PUBLISHED_AFTER_KICKOFF",
    "SCORING_VERSION", "SPORT", "SUPERSEDED",
]
