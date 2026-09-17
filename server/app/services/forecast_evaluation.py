"""Scoring prospective research forecasts against final results.

Written for NFL, now also serving MLB's V18 whole-game research shadow. One
scorer, not two: the freeze rule, the provenance rule and the scoring rules are
shared, and only the labels, the scoring version and the published distribution
shape differ per league (see ``LeagueProfile``). A second implementation would
eventually disagree with this one about what "prospective" means, which is the
one thing that must not vary by sport.

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
    ExternalIdMapping, Game, GameStateObservation, GameStatus, GameTeam, League,
    Market, MarketOutcome, OddsSnapshot, ResearchForecastEvaluation, Season,
    ShadowForecast, Sportsbook, Team,
)
from app.services import forecast_scoring as scoring
from app.services import nfl_outcome_scoring
from app.services import result_provenance as provenance
from app.services.forecast_scoring import (
    HOME, AWAY, TIE, JointMass, MassFunction, OutcomeProbabilities, PointErrors,
    ScoringError,
)

SCORING_VERSION = "AISP_NFL_PROSPECTIVE_SCORING_V1"
SPORT = "FOOTBALL"
LEAGUE = "NFL"

MLB_SCORING_VERSION = "AISP_MLB_V18_PROSPECTIVE_SCORING_V1"
MLB_SPORT = "BASEBALL"
MLB_LEAGUE = "MLB"


@dataclass(frozen=True)
class LeagueProfile:
    """What differs between leagues. Everything else is shared on purpose.

    The scoring rules, the freeze rule and the provenance rule are identical
    across sports -- only the labels written onto the row, the scoring version
    that identifies the implementation, and the shape of the published
    distribution change. Keeping them in one object is what stops the MLB path
    from drifting into a second, subtly different scorer.
    """

    league_code: str
    sport: str
    scoring_version: str
    #: Marginal distribution keys, when the producer publishes marginals rather
    #: than a joint score distribution. Ordered home, away, total, margin.
    marginal_keys: tuple[str, str, str, str] | None = None


LEAGUE_PROFILES: dict[str, LeagueProfile] = {
    LEAGUE: LeagueProfile(LEAGUE, SPORT, SCORING_VERSION),
    MLB_LEAGUE: LeagueProfile(
        MLB_LEAGUE, MLB_SPORT, MLB_SCORING_VERSION,
        marginal_keys=(
            "homeRunDistribution", "awayRunDistribution",
            "totalRunDistribution", "runDifferentialDistribution",
        ),
    ),
}

#: How well a forecast's publication time is evidenced, recorded on every
#: score. The scorer's eligibility rule is unchanged -- published before start,
#: full stop -- but a publication time that is merely the evidence cutoff under
#: another name is not independently evidenced, and a row that does not say so
#: invites its reader to treat the two as equally strong.
CLOCK_VERIFIED = "VERIFIED_INDEPENDENT_PUBLICATION"
CLOCK_UNRESOLVED = "UNRESOLVED_PUBLICATION_CLOCK_COLLAPSED_ONTO_EVIDENCE_CUTOFF"
CLOCK_NO_EVIDENCE_CUTOFF = "NO_EVIDENCE_CUTOFF_RECORDED"

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
HISTORICAL_REPLAY = "HISTORICAL_REPLAY_NOT_PROSPECTIVE_EVIDENCE"

#: Game-level refusals. A final observation is not enough on its own: a game the
#: schedule now records as postponed, cancelled or in progress is not scored, and
#: a game outside the registered leagues is never scored under another league's
#: profile.
GAME_NOT_FINAL = "GAME_STATUS_NOT_FINAL"
LEAGUE_NOT_SCORABLE = "LEAGUE_NOT_SCORABLE"
SCORING_FAILED = "SCORING_FAILED_ISOLATED"
_NONFINAL_STATUSES = frozenset({GameStatus.LIVE, GameStatus.POSTPONED, GameStatus.CANCELED})

#: Why the scored snapshot is the controlling one.
CONTROLLING_REASON = "LATEST_PRE_KICKOFF_PUBLICATION_PER_MODEL_IDENTITY"

#: A forecast whose status begins with this is a labelled rerun over games that
#: have already happened. It is legitimate research and it is kept, but it can
#: never be prospective evidence, so the scorer refuses it outright rather than
#: relying on its publication timestamp to fall the right side of first pitch.
REPLAY_STATUS_PREFIX = "HISTORICAL_REPLAY"

#: Why an evaluation stopped being the current one. The distinction is the
#: point: the first says our verdict was right and is now better evidenced,
#: the second says the result we scored against was wrong.
SUPERSEDED_BY_PROVENANCE = "PROVENANCE_UPGRADE"
SUPERSEDED_BY_CORRECTION = "RESULT_CORRECTED"


#: The provider vocabulary and ours name the same three markets. Matching on
#: the enum's VALUE alone silently discards every quote, because MarketType
#: stores The Odds API's keys ("h2h") rather than our own labels.
_MARKET_FAMILIES = {
    "MONEYLINE": "MONEYLINE", "H2H": "MONEYLINE",
    "SPREAD": "SPREAD", "SPREADS": "SPREAD",
    "TOTAL": "TOTAL", "TOTALS": "TOTAL", "OVER_UNDER": "TOTAL",
}


def _market_family(market_type: Any) -> str | None:
    for token in (getattr(market_type, "name", None), getattr(market_type, "value", None), market_type):
        family = _MARKET_FAMILIES.get(str(token or "").upper())
        if family is not None:
            return family
    return None


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
    tier: int = provenance.UNCLASSIFIED_TIER

    @property
    def scores(self) -> tuple[int, int]:
        return (self.home_score, self.away_score)


class ForecastEvaluationService:
    """Freezes eligible forecasts, scores them, and writes at most one row each."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._orphan_exclusions: list[dict[str, Any]] = []

    # -- league profile ---------------------------------------------------
    def profile_for(self, game: Game) -> LeagueProfile:
        """The league's labels, resolved from the game rather than assumed.

        Defaults to the NFL profile for a game whose league is not registered,
        which keeps every existing caller's behaviour exactly as it was.
        """

        code = self.session.scalar(
            select(League.code)
            .join(Season, Season.league_id == League.id)
            .where(Season.id == game.season_id)
        )
        return LEAGUE_PROFILES.get(str(code or "").upper(), LEAGUE_PROFILES[LEAGUE])

    @staticmethod
    def _publication_clock(row: ShadowForecast) -> dict[str, Any]:
        """Whether this forecast's publication time stands on its own."""

        published = _aware(row.available_as_of_utc)
        cutoff = _aware(row.source_cutoff_utc)
        if cutoff is None:
            classification, detail = CLOCK_NO_EVIDENCE_CUTOFF, (
                "The snapshot records no source evidence cutoff, so publication "
                "time cannot be compared against one."
            )
        elif published == cutoff:
            classification, detail = CLOCK_UNRESOLVED, (
                "Publication time and source evidence cutoff are the same stored "
                "value, so publication is not independently evidenced. Equal "
                "timestamps neither prove nor disprove that the forecast was "
                "published when it says; this score is qualified, not verified."
            )
        else:
            classification, detail = CLOCK_VERIFIED, (
                "Publication time is recorded separately from the source evidence "
                "cutoff."
            )
        return {
            "classification": classification,
            "detail": detail,
            "verified": classification == CLOCK_VERIFIED,
            "published_utc": _iso(published),
            "evidence_cutoff_utc": _iso(cutoff),
            "imported_utc": _iso(row.created_at),
        }

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
        # Exclusions belonging to identities that produced NO eligible snapshot.
        # Without this they were dropped on the floor: a replay that is the only
        # snapshot for its engine was correctly refused, but the refusal had no
        # reason anywhere in the output, which is the same "silently vanished"
        # failure the rest of this module exists to prevent.
        self._orphan_exclusions: list[dict[str, Any]] = []
        for identity, snapshots in by_identity.items():
            excluded: list[dict[str, Any]] = []
            eligible: list[ShadowForecast] = []
            for snapshot in snapshots:
                kickoff = _aware(snapshot.scheduled_start_utc)
                published = _aware(snapshot.available_as_of_utc)
                if str(snapshot.status or "").upper().startswith(REPLAY_STATUS_PREFIX):
                    excluded.append(self._exclusion(
                        snapshot, HISTORICAL_REPLAY,
                        "This forecast is a labelled historical replay. It is research "
                        "evidence about the model, not a forecast that existed before "
                        "the game, so it cannot enter prospective performance.",
                    ))
                    continue
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
                self._orphan_exclusions.extend(excluded)
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
        """The best-provenanced final recorded for a game.

        Identity is the OBSERVATION, not the game row. The game row carries a
        convenience copy of the current score and its identity does not change
        when a correction lands; the observation's checksum does, which is what
        makes a corrected final a new evaluation rather than a silent overwrite
        of the old one.

        Which observation wins is decided by provenance rank, then recency.
        That ordering is the whole reason an operator entry can be replaced by
        an automatic one without the operator entry ever being deleted -- and
        the reason a late operator entry can never displace a provider-verified
        score.

        A score sitting on the game row with no observation behind it has no
        provenance and no stable identity, so it is not scoreable evidence.
        """

        observations = [
            row for row in self.session.scalars(
                select(GameStateObservation).where(
                    GameStateObservation.game_id == game.id,
                    GameStateObservation.status == GameStatus.FINAL.value,
                )
            )
            if row.home_score is not None and row.away_score is not None
        ]
        if not observations:
            return None
        chosen = min(observations, key=lambda row: (
            provenance.result_tier(row.source),
            -((_aware(row.available_as_of) or datetime.min.replace(tzinfo=UTC)).timestamp()),
            # Two records of one game at the same rank AND the same knowledge
            # time are contradictory evidence, not a correction -- there is no
            # principled winner. Break the tie on the checksum so the choice is
            # at least STABLE: a current evaluation that flapped between runs
            # would be worse than an arbitrary one that holds still.
            str(row.payload_checksum or ""),
        ))
        return GameResult(
            game=game, home_score=int(chosen.home_score), away_score=int(chosen.away_score),
            identity=f"{chosen.source}:{chosen.payload_checksum}",
            source=str(chosen.source),
            observed_at=_aware(chosen.observed_at),
            tier=provenance.result_tier(chosen.source),
        )

    # -- scoring ----------------------------------------------------------
    def evaluate_game(
        self, *, canonical_game_id: UUID, dry_run: bool = False, now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        game = self.session.get(Game, canonical_game_id)
        if game is None:
            return {"game_id": str(canonical_game_id), "scored": 0, "skipped": "GAME_NOT_REGISTERED"}
        league_code = self.session.scalar(
            select(League.code).join(Season, Season.league_id == League.id).where(Season.id == game.season_id)
        )
        if str(league_code or "").upper() not in LEAGUE_PROFILES:
            return {"game_id": str(canonical_game_id), "scored": 0, "skipped": LEAGUE_NOT_SCORABLE,
                    "detail": f"League {league_code!r} has no registered scoring profile."}
        status = game.status if isinstance(game.status, GameStatus) else GameStatus(str(game.status).lower())
        if status in _NONFINAL_STATUSES:
            return {"game_id": str(canonical_game_id), "scored": 0, "skipped": GAME_NOT_FINAL,
                    "detail": f"The schedule records this game as {status.value}; it is not scored "
                              "until it is final again."}
        result = self.result_for(game)
        if result is None:
            return {
                "game_id": str(canonical_game_id), "scored": 0,
                "skipped": "NO_AUTHORITATIVE_FINAL",
                "detail": "The game is not FINAL with a score backed by a state observation.",
            }

        profile = self.profile_for(game)
        frozen = self.freeze_eligible(canonical_game_id=canonical_game_id)
        written, reused, superseded, records = 0, 0, 0, []
        for identity, selection in frozen.items():
            payload = self._score(selection, result, now=now, profile=profile)
            existing = self.session.scalar(select(ResearchForecastEvaluation).where(
                ResearchForecastEvaluation.event_id == payload["event_id"],
                ResearchForecastEvaluation.engine_id == payload["engine_id"],
                ResearchForecastEvaluation.engine_version == payload["engine_version"],
                ResearchForecastEvaluation.forecast_identity == payload["forecast_identity"],
                ResearchForecastEvaluation.result_identity == payload["result_identity"],
            ))
            if dry_run:
                records.append({"identity": identity, "written": False, "dry_run": True,
                                "reused": existing is not None,
                                "log_loss": payload["log_loss"]})
                continue
            if existing is not None:
                reused += 1
                record = {"identity": identity, "written": False, "evaluation_id": str(existing.id)}
            else:
                # Stand the incumbent down before inserting. Only one row per
                # forecast identity may be current, and the database enforces
                # that with a partial unique index -- so the new row cannot be
                # inserted as current while the old one still is. Which of the
                # two ends up current is decided immediately below, by
                # provenance rather than by insertion order.
                self._demote_current(payload, now=now)
                row = ResearchForecastEvaluation(**payload)
                self.session.add(row)
                self.session.flush()
                written += 1
                record = {"identity": identity, "written": True, "evaluation_id": str(row.id)}
            # Runs on every pass, not just after a write: an earlier version of
            # this table had no notion of "current", so the first reconciliation
            # is what promotes the rows that already exist.
            reconciliation = self._reconcile_current(payload)
            superseded += len(reconciliation["superseded"])
            records.append({
                **record, **reconciliation,
                "publication_clock": payload["detail"]["publication_clock"]["classification"],
                "forecast_identity": payload["forecast_identity"],
                "result_identity": payload["result_identity"],
            })
        return {
            "game_id": str(canonical_game_id),
            "event_id": next(iter(frozen.values())).row.event_id if frozen else None,
            "models_considered": len(frozen),
            "scored": written, "reused": reused, "superseded": superseded, "records": records,
            "result_source": result.source, "result_tier": result.tier,
            "excluded": (
                [entry for selection in frozen.values() for entry in selection.excluded]
                + list(getattr(self, "_orphan_exclusions", []))
            ),
        }

    def _siblings(self, payload: dict[str, Any]) -> list[ResearchForecastEvaluation]:
        """Every evaluation ever written for this forecast identity."""

        return list(self.session.scalars(select(ResearchForecastEvaluation).where(
            ResearchForecastEvaluation.event_id == payload["event_id"],
            ResearchForecastEvaluation.engine_id == payload["engine_id"],
            ResearchForecastEvaluation.engine_version == payload["engine_version"],
            ResearchForecastEvaluation.forecast_identity == payload["forecast_identity"],
        )))

    def _demote_current(self, payload: dict[str, Any], *, now: datetime) -> None:
        """Temporarily clear the current flag so a new row can be inserted."""

        for row in self._siblings(payload):
            if row.is_current:
                row.is_current = False
                row.superseded_at = now
        self.session.flush()

    def _reconcile_current(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Leave exactly one current evaluation for this forecast identity.

        Every historical row survives -- the verdict we published on Wednesday
        is a fact about Wednesday and deleting it would destroy the audit
        trail. But only one of them is the answer, and it is the one scored
        against the best-provenanced result, latest breaking ties.

        This is the mechanism that keeps ``n`` honest. A provider confirming a
        score we already had adds a row and changes nothing else: the new row
        becomes current, the old one is marked ``PROVENANCE_UPGRADE``, and the
        count of scored games does not move.
        """

        siblings = self._siblings(payload)
        if not siblings:
            return {"current_evaluation_id": None, "superseded": []}

        def rank(row: ResearchForecastEvaluation) -> tuple[Any, ...]:
            observed = _aware(row.result_observed_utc) or datetime.min.replace(tzinfo=UTC)
            evaluated = _aware(row.evaluated_at) or datetime.min.replace(tzinfo=UTC)
            return (row.result_tier, -observed.timestamp(), -evaluated.timestamp())

        current = min(siblings, key=rank)
        # Clear the old flag first so the partial unique index never sees two
        # current rows mid-statement.
        superseded: list[dict[str, Any]] = []
        for row in siblings:
            if row.id == current.id:
                continue
            reason = (SUPERSEDED_BY_CORRECTION
                      if (row.home_score, row.away_score) != (current.home_score, current.away_score)
                      else SUPERSEDED_BY_PROVENANCE)
            if row.is_current or row.superseded_by_id != current.id:
                row.is_current = False
                row.superseded_at = row.superseded_at or datetime.now(UTC)
                row.superseded_by_id = current.id
                row.supersede_reason = reason
                superseded.append({
                    "evaluation_id": str(row.id), "reason": reason,
                    "result_source": row.result_source,
                    "score": f"{row.home_score}-{row.away_score}",
                })
        self.session.flush()
        if not current.is_current:
            current.is_current = True
            current.superseded_at = None
            current.superseded_by_id = None
            current.supersede_reason = None
        self.session.flush()
        return {"current_evaluation_id": str(current.id),
                "current_result_source": current.result_source,
                "superseded": superseded}

    def _score(
        self, selection: FrozenForecast, result: GameResult, *, now: datetime,
        profile: LeagueProfile | None = None,
    ) -> dict[str, Any]:
        profile = profile or LEAGUE_PROFILES[LEAGUE]
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

        distribution = self._distribution_metrics(row, result, profile=profile)
        market = self._market_comparison(result.game, selection.kickoff, outcome, result)

        return {
            "sport": profile.sport, "league_code": profile.league_code,
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
            "result_tier": result.tier,
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
                "publication_clock": self._publication_clock(row),
                "result_provenance": provenance.describe(result.source),
                "controlling_revision": self._controlling_revision(selection, profile),
                "mass_reconciliation": nfl_outcome_scoring.mass_reconciliation(
                    probabilities.home, probabilities.away, probabilities.tie),
                "winner_convention": "Predicted class is the largest published probability over "
                                     "HOME/AWAY/TIE; a TIE result is a miss for a HOME or AWAY call.",
            },
            "scoring_version": profile.scoring_version,
            "evaluated_at": now,
        }

    def _controlling_revision(self, selection: FrozenForecast, profile: LeagueProfile) -> dict[str, Any]:
        """The exact revision scored, and why it is the one that controls."""

        from app.models import ShadowForecastBundle

        row = selection.row
        record: dict[str, Any] = {
            "reason": CONTROLLING_REASON,
            "forecast_id": row.forecast_id,
            "snapshot_type": row.snapshot_type,
            "snapshot_version": row.snapshot_version,
            "engine_id": row.engine_id,
            "engine_version": row.engine_version,
            "model_artifact_sha256": row.model_artifact_sha256,
            "model_manifest_sha256": row.model_manifest_sha256,
            "package_sha256": self.session.scalar(
                select(ShadowForecastBundle.bundle_sha256).where(ShadowForecastBundle.id == row.bundle_id)),
            "created_utc": _iso(row.created_at),
            "published_utc": _iso(row.available_as_of_utc),
            "source_cutoff_utc": _iso(row.source_cutoff_utc),
            "kickoff_utc": _iso(selection.kickoff),
            "horizon": {"snapshot_type": row.snapshot_type,
                        "hours_before_kickoff": round(selection.lead_seconds / 3600, 2)},
            "snapshots_considered": selection.considered,
            "population_sha256": None,
            "population_draws": None,
            "population_verification": None,
        }
        if profile.league_code == LEAGUE:
            from app.probability.adapter import ProbabilityUnavailable, verified_nfl_population
            try:
                population = verified_nfl_population(self.session, row)
            except ProbabilityUnavailable as exc:
                record["population_verification"] = {"verified": False, "code": exc.code, "detail": exc.message}
            else:
                record["population_sha256"] = population.population_sha256
                record["population_draws"] = population.draw_count
                record["population_verification"] = {"verified": True}
        return record

    @staticmethod
    def _tie_probability(row: ShadowForecast) -> float:
        """The published tie, or the residual mass, and never a negative one."""

        distributions = row.distributions or {}
        published = distributions.get("tie_probability")
        if published is not None:
            return float(published)
        residual = 1.0 - float(row.full_home_win_probability) - float(row.full_away_win_probability)
        return max(0.0, residual)

    @staticmethod
    def _marginal(distributions: dict[str, Any], key: str, draws: int | None) -> MassFunction | None:
        """A published marginal, as a mass function over integer values."""

        published = distributions.get(key)
        if not isinstance(published, dict) or not published:
            return None
        try:
            return MassFunction.from_pairs(
                ((int(value), float(mass)) for value, mass in published.items()),
                draws=draws,
            )
        except (ScoringError, TypeError, ValueError):
            return None

    def _marginal_metrics(
        self, row: ShadowForecast, result: GameResult, profile: LeagueProfile,
    ) -> dict[str, Any] | None:
        """Distribution metrics from published MARGINALS rather than a joint.

        A whole-game simulator that publishes per-side run distributions has
        given us everything CRPS needs on each margin, and refusing to score it
        because the cells are not a joint would discard real measured
        uncertainty. The energy score genuinely does need the joint, so it stays
        unavailable with that reason rather than being approximated from the
        product of the marginals -- independent sides is exactly the assumption
        a whole-game simulator exists to avoid making.
        """

        if profile.marginal_keys is None:
            return None
        distributions = row.distributions or {}
        draws = self._draw_count(row)
        home_key, away_key, total_key, margin_key = profile.marginal_keys
        home = self._marginal(distributions, home_key, draws)
        away = self._marginal(distributions, away_key, draws)
        if home is None or away is None:
            return None
        total = self._marginal(distributions, total_key, draws)
        margin = self._marginal(distributions, margin_key, draws)
        actual_margin = result.home_score - result.away_score
        actual_total = result.home_score + result.away_score
        return {
            "distribution_available": True,
            "distribution_unavailable_reason": None,
            "distribution_draws": draws,
            "crps_home": scoring.crps(home, result.home_score),
            "crps_away": scoring.crps(away, result.away_score),
            "crps_margin": scoring.crps(margin, actual_margin) if margin else None,
            "crps_total": scoring.crps(total, actual_total) if total else None,
            "energy_score": None,
            "interval_coverage": {
                "margin": scoring.interval_coverage(margin, actual_margin) if margin else None,
                "total": scoring.interval_coverage(total, actual_total) if total else None,
                "note": "For one game these are hits and misses. The rate across games is the "
                        "coverage; a single containment is not.",
                "basis": "PUBLISHED_MARGINALS",
                "energy_score_unavailable_reason":
                    "The energy score is a joint statistic and this forecast publishes "
                    "marginals only. Multiplying the marginals would assume the two sides "
                    "are independent, which is the assumption a whole-game simulator "
                    "exists to avoid.",
            },
        }

    def _distribution_metrics(
        self, row: ShadowForecast, result: GameResult,
        profile: LeagueProfile | None = None,
    ) -> dict[str, Any]:
        profile = profile or LEAGUE_PROFILES[LEAGUE]
        distributions = row.distributions or {}
        cells = distributions.get("joint_score_distribution") or []
        if not cells:
            marginal = self._marginal_metrics(row, result, profile)
            if marginal is not None:
                return marginal
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
        """The simulation count the producer published.

        From its own supplement where one exists; otherwise from the
        uncertainty block the forecast itself carries, which is where a
        whole-game simulator records how many games it drew. Never guessed.
        """

        from app.models import ResearchSupplement

        published = self.session.scalar(
            select(ResearchSupplement.simulation_count).where(
                ResearchSupplement.forecast_id == row.forecast_id,
                ResearchSupplement.kind == "JOINT_SCORE_SIMULATION",
            ).limit(1)
        )
        if published is not None:
            return published
        declared = (row.uncertainty or {}).get("simulationCount")
        try:
            return int(declared) if declared is not None else None
        except (TypeError, ValueError):
            return None

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
            family = _market_family(market.market_type)
            if family is None:
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
    def _evaluate_isolated(self, game_id: UUID, *, dry_run: bool, now: datetime) -> dict[str, Any]:
        """Score one game inside a savepoint so a failure stays with that game.

        A malformed forecast or result must not stop the rest of the pass, and it
        can never reach settlement: scoring runs as its own job and writes only
        evaluation rows.
        """

        savepoint = self.session.begin_nested()
        try:
            outcome = self.evaluate_game(canonical_game_id=game_id, dry_run=dry_run, now=now)
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed silently
            savepoint.rollback()
            return {"game_id": str(game_id), "scored": 0, "skipped": SCORING_FAILED,
                    "detail": f"{type(exc).__name__}: {exc}"[:500]}
        savepoint.commit()
        return outcome

    def evaluate_league(
        self, *, league_code: str = LEAGUE, dry_run: bool = False, now: datetime | None = None,
        earliest_start: datetime | None = None,
        excluded_source_event_ids: frozenset[str] | None = None,
        excluded_id_source: str = "MLB_STATS_API",
        require_shadow_forecast: bool = False,
    ) -> dict[str, Any]:
        """Score every final game in one league. Safe to rerun.

        ``earliest_start`` and ``excluded_source_event_ids`` bound the pass
        BEFORE any result is read. They are predicates on the population query,
        not a filter applied to scored rows afterwards: a protected game must
        never have its outcome read and then be discarded.
        """

        now = now or datetime.now(UTC)
        profile = LEAGUE_PROFILES.get(str(league_code).upper(), LEAGUE_PROFILES[LEAGUE])
        # Driven by the EVIDENCE, not by the projection. A game is scoreable
        # once an observation records it final; the ``games`` row is a
        # convenience copy of that, and keying off it would let one failed
        # projection write silently stop the whole workflow.
        has_final = (
            select(GameStateObservation.id)
            .where(
                GameStateObservation.game_id == Game.id,
                GameStateObservation.status == GameStatus.FINAL.value,
                GameStateObservation.home_score.is_not(None),
                GameStateObservation.away_score.is_not(None),
            )
            .exists()
        )
        query = (
            select(Game.id)
            .join(Season, Season.id == Game.season_id)
            .join(League, League.id == Season.league_id)
            .where(League.code == league_code, has_final)
        )
        if earliest_start is not None:
            query = query.where(Game.starts_at >= earliest_start)
        if require_shadow_forecast:
            # A game nothing forecast can never be scored. Narrowing the scan to
            # games that carry a research forecast keeps ``final_games`` a count
            # of candidates rather than a count of the league's fixture list.
            # Off by default so no existing caller's reported counts move.
            query = query.where(
                select(ShadowForecast.id)
                .where(ShadowForecast.canonical_game_id == Game.id)
                .exists()
            )
        if excluded_source_event_ids:
            # Excluded by identity at the source, in the population predicate.
            protected = (
                select(ExternalIdMapping.entity_id)
                .where(
                    ExternalIdMapping.entity_type == "game",
                    # Scoped to the id source: several providers map games, and
                    # a bare id match could exclude an unrelated game that
                    # happens to share a string with a protected gamePk.
                    ExternalIdMapping.source == excluded_id_source,
                    ExternalIdMapping.external_id.in_(tuple(excluded_source_event_ids)),
                )
            )
            query = query.where(Game.id.not_in(protected))
        game_ids = list(self.session.scalars(query.order_by(Game.starts_at)))
        results = [self._evaluate_isolated(game_id, dry_run=dry_run, now=now) for game_id in game_ids]
        return {
            "league": league_code,
            "final_games": len(game_ids),
            "earliest_start": _iso(earliest_start),
            "excluded_source_event_ids": sorted(excluded_source_event_ids or ()),
            "scored": sum(entry.get("scored", 0) for entry in results),
            "reused": sum(entry.get("reused", 0) for entry in results),
            "superseded": sum(entry.get("superseded", 0) for entry in results),
            "skipped": [entry for entry in results if entry.get("skipped")],
            "games": results,
            "provider_calls": 0, "models_executed": 0,
            "positions_created": 0, "official_decisions_created": 0,
            "scoring_version": profile.scoring_version,
        }


__all__ = [
    "CLOCK_NO_EVIDENCE_CUTOFF", "CLOCK_UNRESOLVED", "CLOCK_VERIFIED",
    "ForecastEvaluationService", "FrozenForecast", "GameResult", "LEAGUE",
    "LEAGUE_PROFILES", "LeagueProfile", "MLB_LEAGUE", "MLB_SCORING_VERSION",
    "MLB_SPORT",
    "NON_NUMERICAL_KINDS", "NO_KICKOFF", "NO_PROBABILITIES", "PUBLISHED_AFTER_KICKOFF",
    "SCORING_VERSION", "SPORT", "SUPERSEDED", "SUPERSEDED_BY_CORRECTION",
    "SUPERSEDED_BY_PROVENANCE",
]
