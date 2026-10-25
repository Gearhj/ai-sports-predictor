"""NFL outcome-scoring readiness and the consumer scorecard (read-only).

Two read models over data AISP already stores. Neither writes a row, calls a
provider, runs a model or touches a decision, position or settlement.

``week_readiness(season, week)``
    One row per NFL game in a schedule week (identified by the nflverse game id
    ``nflverse:<season>_<week>_<away>_<home>``): identities, every scoring view's
    controlling forecast revision, the expected final source, the current
    outcome state, whether the game will be scored automatically after a
    governed final, and every blocker or ambiguity.

``scorecard()``
    The NFL model-performance record a normal reader can follow: settled,
    open and unscorable counts, a per-cohort scorecard (never pooled across
    model versions or authority cohorts), game rows with an audit drawer, line
    (spread/total) evaluations with push, calibration only at a sufficient
    sample, and review-checkpoint progress.

SCORING VIEWS -- kept apart, never collapsed
    RESEARCH_PROSPECTIVE  the stored evaluation: latest NFL M1 research-shadow
                          snapshot published before kickoff (per model version).
    PAPER_RESEARCH        the exact forecast a PAPER_RESEARCH_ELIGIBILITY
                          supplement names, scored against the same final. It
                          may be an earlier revision than the research view.
    PRODUCTION            only a forecast governed by a verified Codex
                          production handoff. None exists today.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.forecast_exchange import nfl_production_handoff as handoff
from app.models import (
    ExternalIdMapping, Game, GameStateObservation, GameStatus, GameTeam, JobRun, League, Market,
    MarketOutcome, NflFinalObservation, OfficialAspPosition, PositionSettlement, RunStatus,
    ResearchForecastEvaluation, ResearchSupplement, Season, ShadowForecast,
    ShadowForecastBundle, Team,
)
from app.probability.adapter import ADMITTED, ProbabilityUnavailable, verified_nfl_population
from app.services import forecast_scoring as scoring
from app.services import nfl_outcome_scoring as outcomes
from app.services import result_provenance as provenance
from app.services.forecast_evaluation import (
    LEAGUE, SCORING_VERSION, ForecastEvaluationService,
)

READINESS_SCHEMA = "AISP_NFL_SCORING_READINESS_V1"
SCORECARD_SCHEMA = "AISP_NFL_SCORECARD_V1"
ET = ZoneInfo("America/New_York")
NFLVERSE = "NFLVERSE"
PAPER_KIND = "PAPER_RESEARCH_ELIGIBILITY"
M1 = ADMITTED["NFL"]

VIEW_RESEARCH = "RESEARCH_PROSPECTIVE"
VIEW_PAPER = "PAPER_RESEARCH"
VIEW_PRODUCTION = "PRODUCTION"

JOBS = {
    "final_ingest": {"job": "ingest-nfl-finals", "scheduler_id": "asp-nfl-final-ingest",
                     "source": "nflverse games.csv (public GitHub release; no key, no credits)"},
    "scoring": {"job": "evaluate-nfl-research-forecasts", "scheduler_id": "asp-nfl-research-evaluate",
                "source": "stored forecasts and stored finals only"},
    "settlement": {"job": "finalize-games", "scheduler_id": "asp-finalize-games",
                   "source": "governed experiment settlement (separate from scoring)"},
}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    moment = _aware(value)
    return moment.isoformat() if moment else None


def _status_value(status: Any) -> str:
    """An enum's value (any enum), or the lower-cased string."""

    value = getattr(status, "value", status)
    return str(value or "").lower()


def _week_of(external_id: str) -> tuple[int, int] | None:
    match = re.match(r"^nflverse:(\d{4})_(\d{2})_", external_id or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


class NflScoringReadinessService:
    def __init__(self, session: Session, settings: Any | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.evaluator = ForecastEvaluationService(session)

    # ------------------------------------------------------------ shared --
    def _last_run(self, job: str) -> dict[str, Any]:
        row = self.session.execute(
            select(JobRun.status, JobRun.completed_at).where(JobRun.job_name == job)
            .order_by(JobRun.created_at.desc()).limit(1)).first()
        success = self.session.scalar(
            select(func.max(JobRun.completed_at)).where(JobRun.job_name == job,
                                                        JobRun.status == RunStatus.SUCCEEDED))
        return {"last_status": _status_value(row[0]).upper() if row else None,
                "last_completed_utc": _iso(row[1]) if row else None,
                "last_success_utc": _iso(success)}

    def ownership(self, now: datetime | None = None) -> dict[str, Any]:
        """Who owns each step, from configuration AND from recorded runs.

        The configured flags describe THIS process; the scheduler process is the
        one that matters, so a job that has succeeded within three intervals is
        also treated as owned and running.
        """

        now = now or datetime.now(UTC)
        settings = self.settings
        shadow = bool(getattr(settings, "nfl_shadow_enabled", False))
        ingest = bool(getattr(settings, "owns_nfl_final_ingest", False))

        def step(key: str, configured: bool, interval: int | None) -> dict[str, Any]:
            evidence = self._last_run(JOBS[key]["job"])
            success = evidence["last_success_utc"]
            recent = bool(success and interval and
                          (now - datetime.fromisoformat(success)).total_seconds() <= 3 * interval * 60)
            return {**JOBS[key], **evidence, "interval_minutes": interval,
                    "configured_in_this_process": configured, "recently_succeeded": recent,
                    "armed": configured or recent}

        return {
            "final_ingest": step("final_ingest", shadow and ingest,
                                 getattr(settings, "nfl_final_ingest_interval_minutes", 60)),
            "scoring": step("scoring", shadow, 60),
            "settlement": step("settlement", True, 30),
            "secondary_result_source": {
                "job": "finalize-nfl-shadow-games", "source": "The Odds API /scores",
                "armed": bool(getattr(settings, "nfl_shadow_odds_enabled", False)),
            },
            "new_scheduler_created": False,
            "note": "Scoring runs as its own job after final ingestion, so a scoring failure "
                    "cannot stop or alter settlement.",
        }

    def production(self) -> dict[str, Any]:
        return handoff.production_status(self.settings).as_dict()

    def _teams(self, game_ids: set[Any]) -> dict[Any, dict[str, Any]]:
        out: dict[Any, dict[str, Any]] = defaultdict(dict)
        if not game_ids:
            return out
        for game_id, is_home, abbreviation, name in self.session.execute(
            select(GameTeam.game_id, GameTeam.is_home, Team.abbreviation, Team.name)
            .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id.in_(game_ids))
        ).all():
            out[game_id]["home" if is_home else "away"] = {"abbreviation": abbreviation, "name": name}
        return out

    def _mappings(self, game_ids: set[Any]) -> dict[Any, dict[str, list[str]]]:
        out: dict[Any, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        if not game_ids:
            return out
        for entity_id, source, external_id in self.session.execute(
            select(ExternalIdMapping.entity_id, ExternalIdMapping.source, ExternalIdMapping.external_id)
            .where(ExternalIdMapping.entity_type == "game", ExternalIdMapping.entity_id.in_(game_ids))
        ).all():
            out[entity_id][source].append(external_id)
        return out

    def _bundle_sha(self, row: ShadowForecast) -> str | None:
        return self.session.scalar(
            select(ShadowForecastBundle.bundle_sha256).where(ShadowForecastBundle.id == row.bundle_id))

    def _revision(self, row: ShadowForecast, kickoff: datetime | None, *, reason: str,
                  verify: bool = True) -> dict[str, Any]:
        published = _aware(row.available_as_of_utc)
        record: dict[str, Any] = {
            "forecast_id": row.forecast_id,
            "shadow_forecast_id": str(row.id),
            "snapshot_type": row.snapshot_type,
            "snapshot_version": row.snapshot_version,
            "model_id": row.engine_id,
            "model_revision": row.engine_version,
            "model_artifact_sha256": row.model_artifact_sha256,
            "package_sha256": self._bundle_sha(row),
            "created_utc": _iso(row.created_at),
            "published_utc": _iso(published),
            "source_cutoff_utc": _iso(row.source_cutoff_utc),
            "kickoff_utc": _iso(kickoff),
            "horizon": {
                "snapshot_type": row.snapshot_type,
                "hours_before_kickoff": (round((kickoff - published).total_seconds() / 3600, 2)
                                         if kickoff and published else None),
            },
            "published_before_kickoff": bool(kickoff and published and published < kickoff),
            "forecast_kind": "RESEARCH_SHADOW_PACKAGE",
            "controlling_reason": reason,
            "population_sha256": None, "draw_count": None, "population_verification": None,
        }
        if verify:
            try:
                population = verified_nfl_population(self.session, row)
            except ProbabilityUnavailable as exc:
                record["population_verification"] = {"verified": False, "code": exc.code, "detail": exc.message}
            else:
                record["population_sha256"] = population.population_sha256
                record["draw_count"] = population.draw_count
                record["population_verification"] = {"verified": True}
        return record

    def _paper_supplements(self, forecast_ids: set[str]) -> dict[str, list[ResearchSupplement]]:
        out: dict[str, list[ResearchSupplement]] = defaultdict(list)
        if not forecast_ids:
            return out
        for row in self.session.scalars(
            select(ResearchSupplement).where(ResearchSupplement.kind == PAPER_KIND,
                                             ResearchSupplement.forecast_id.in_(forecast_ids))
        ):
            out[row.forecast_id].append(row)
        return out

    def _final_detail(self, game: Game) -> dict[str, Any] | None:
        result = self.evaluator.result_for(game)
        if result is None:
            return None
        checksum = result.identity.split(":", 1)[1] if ":" in result.identity else None
        nfl = self.session.scalar(
            select(NflFinalObservation).where(NflFinalObservation.game_id == game.id,
                                              NflFinalObservation.payload_checksum == checksum)
        ) if checksum else None
        lineage = list(self.session.execute(
            select(GameStateObservation.source, GameStateObservation.payload_checksum,
                   GameStateObservation.home_score, GameStateObservation.away_score,
                   GameStateObservation.available_as_of)
            .where(GameStateObservation.game_id == game.id,
                   GameStateObservation.status == GameStatus.FINAL.value)
            .order_by(GameStateObservation.available_as_of)
        ).all())
        return {
            "result_identity": result.identity,
            "source": result.source,
            "tier": result.tier,
            "home_score": result.home_score,
            "away_score": result.away_score,
            "outcome": scoring.actual_class(result.home_score, result.away_score),
            # Retained only where the source states it; never inferred.
            "overtime": nfl.overtime if nfl is not None else None,
            "observed_utc": _iso(result.observed_at),
            **provenance.describe(result.source),
            "revisions": [
                {"source": source, "result_identity": f"{source}:{checksum_}",
                 "score": f"{home}-{away}", "available_utc": _iso(available),
                 "current": f"{source}:{checksum_}" == result.identity}
                for source, checksum_, home, away, available in lineage
            ],
        }

    def _positions(self, game_ids: set[Any]) -> dict[Any, list[dict[str, Any]]]:
        out: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        if not game_ids:
            return out
        rows = self.session.execute(
            select(Market.game_id, OfficialAspPosition, PositionSettlement)
            .join(MarketOutcome, MarketOutcome.id == OfficialAspPosition.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .outerjoin(PositionSettlement, PositionSettlement.position_id == OfficialAspPosition.id)
            .where(Market.game_id.in_(game_ids))
        ).all()
        for game_id, position, settlement in rows:
            out[game_id].append({
                "position_id": str(position.id),
                "status": _status_value(position.status).upper(),
                "stake": float(position.stake),
                "decimal_odds": position.decimal_odds,
                "settlement_id": str(settlement.id) if settlement else None,
                "result": _status_value(settlement.result).upper() if settlement else None,
                "settled_utc": _iso(settlement.settled_at) if settlement else None,
                "note": "An experiment position is settled by its own policy and is not part "
                        "of model scoring.",
            })
        return out

    # --------------------------------------------------------- readiness --
    def week_readiness(self, *, season: int, week: int, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        prefix = f"nflverse:{season}_{week:02d}_"
        mapped = list(self.session.execute(
            select(ExternalIdMapping.entity_id, ExternalIdMapping.external_id)
            .join(Game, Game.id == ExternalIdMapping.entity_id)
            .join(Season, Season.id == Game.season_id)
            .join(League, League.id == Season.league_id)
            .where(ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == NFLVERSE,
                   ExternalIdMapping.external_id.like(f"{prefix}%"), League.code == LEAGUE)
        ).all())
        by_game: dict[Any, list[str]] = defaultdict(list)
        for game_id, external_id in mapped:
            by_game[game_id].append(external_id)
        games = sorted((self.session.get(Game, gid) for gid in by_game), key=lambda g: (g.starts_at, str(g.id)))
        ids = {game.id for game in games}
        teams = self._teams(ids)
        mappings = self._mappings(ids)
        positions = self._positions(ids)
        production = handoff.production_status(self.settings)
        ownership = self.ownership()
        rows = [self._readiness_row(game, teams.get(game.id, {}), mappings.get(game.id, {}),
                                    positions.get(game.id, []), production, ownership, now)
                for game in games]
        states = Counter(row["automatic_scoring"]["state"] for row in rows)
        return {
            "schema_version": READINESS_SCHEMA,
            "generated_at_utc": _iso(now),
            "season": season, "week": week,
            "week_identity": f"{prefix}*",
            "games": len(rows),
            "summary": dict(states),
            "scheduler_ownership": ownership,
            "production_status": production.as_dict(),
            "rows": rows,
            "provider_calls": 0, "models_executed": 0, "positions_created": 0,
        }

    def _readiness_row(self, game: Game, teams: dict[str, Any], mapping: dict[str, list[str]],
                       positions: list[dict[str, Any]], production: handoff.HandoffVerdict,
                       ownership: dict[str, Any], now: datetime) -> dict[str, Any]:
        kickoff = _aware(game.starts_at)
        blockers: list[dict[str, str]] = []
        notes: list[str] = []
        nflverse_ids = mapping.get(NFLVERSE, [])
        if len(nflverse_ids) != 1:
            blockers.append({"code": "NFLVERSE_IDENTITY_AMBIGUOUS",
                             "detail": f"{len(nflverse_ids)} nflverse ids map to this game."})
        home = (teams.get("home") or {}).get("abbreviation")
        away = (teams.get("away") or {}).get("abbreviation")
        if not home or not away:
            blockers.append({"code": "TEAMS_UNRESOLVED", "detail": "Home or away team is not recorded."})
        if nflverse_ids and home and not nflverse_ids[0].endswith(f"_{home}"):
            notes.append(f"nflverse spells the home club {nflverse_ids[0].rsplit('_', 1)[-1]}; AISP holds "
                         f"{home}. Final ingestion resolves this alias explicitly (same_club).")

        snapshots = list(self.session.scalars(
            select(ShadowForecast).where(ShadowForecast.canonical_game_id == game.id)
            .order_by(ShadowForecast.available_as_of_utc)))
        by_type = Counter(row.snapshot_type for row in snapshots)
        late = [row for row in snapshots if kickoff and _aware(row.available_as_of_utc) and
                _aware(row.available_as_of_utc) >= kickoff]

        # Research view: what the scorer would freeze if kickoff were now.
        frozen = self.evaluator.freeze_eligible(canonical_game_id=game.id)
        m1_key = f"{M1['engine_id']}::{M1['configuration_id']}"
        research = None
        if m1_key in frozen:
            selection = frozen[m1_key]
            provisional = bool(kickoff and now < kickoff)
            research = self._revision(
                selection.row, kickoff,
                reason=("LATEST_PRE_KICKOFF_PUBLICATION (provisional: a later pre-kickoff snapshot, e.g. "
                        "FINAL_PREGAME, would replace it)" if provisional
                        else "LATEST_PRE_KICKOFF_PUBLICATION"))
            research["provisional"] = provisional
            # The package's own status labels this view; the probability channel's
            # label is kept beside it, never substituted for it.
            research["authority"] = "RESEARCH_SHADOW"
            research["probability_channel_authority"] = M1["authority"]
            if not research["population_verification"]["verified"]:
                blockers.append({"code": "POPULATION_NOT_VERIFIED",
                                 "detail": research["population_verification"]["detail"]})
        else:
            blockers.append({"code": "NO_PRE_KICKOFF_M1_FORECAST",
                             "detail": "No NFL M1 research forecast is published before kickoff."})
        other_models = sorted(k for k in frozen if k != m1_key)

        # Paper view: the exact forecast each paper supplement names.
        papers = self._paper_supplements({row.forecast_id for row in snapshots})
        paper_rows = []
        for forecast_id, supplements in papers.items():
            row = next(r for r in snapshots if r.forecast_id == forecast_id)
            for supplement in supplements:
                paper_rows.append({
                    "supplement_sha256": supplement.supplement_sha256,
                    "supplement_id": supplement.supplement_id,
                    "artifact_version": supplement.artifact_version,
                    "status": supplement.status,
                    "published_utc": _iso(supplement.published_utc),
                    "authority": "PAPER_RESEARCH_EVIDENCE",
                    "forecast": self._revision(row, kickoff, reason="NAMED_BY_PAPER_ELIGIBILITY_SUPPLEMENT",
                                               verify=False),
                    "same_revision_as_research_view": bool(research and research["forecast_id"] == forecast_id),
                })
        if not paper_rows:
            notes.append("No paper-research eligibility supplement names a forecast for this game.")
        elif research and not any(p["same_revision_as_research_view"] for p in paper_rows):
            notes.append("The paper supplement names an earlier revision than the research view; the two "
                         "views are scored separately and never merged.")

        final = self._final_detail(game)
        status = _status_value(game.status)
        if status in {"postponed", "canceled", "live"}:
            outcome_state = status.upper()
        elif final is not None:
            outcome_state = "FINAL"
        elif kickoff and now < kickoff:
            outcome_state = "NOT_STARTED"
        else:
            outcome_state = "AWAITING_FINAL"

        evaluation = self.session.scalar(
            select(ResearchForecastEvaluation).where(
                ResearchForecastEvaluation.canonical_game_id == game.id,
                ResearchForecastEvaluation.is_current.is_(True),
                ResearchForecastEvaluation.engine_id == M1["engine_id"]))
        if not ownership["final_ingest"]["armed"]:
            blockers.append({"code": "FINAL_INGEST_NOT_ARMED",
                             "detail": "The nflverse final-ingest job is not armed in this process."})
        if not ownership["scoring"]["armed"]:
            blockers.append({"code": "SCORING_NOT_ARMED", "detail": "The NFL scoring job is not armed."})
        if outcome_state in {"POSTPONED", "CANCELED"}:
            blockers.append({"code": f"GAME_{outcome_state}", "detail": "Not scored unless it becomes final."})
        if evaluation is not None:
            state = "SCORED"
        elif blockers:
            state = "BLOCKED"
        else:
            state = "READY_AFTER_GOVERNED_FINAL"

        production_view = {
            "status": production.status,
            "governed": False,
            "detail": ("No verified Codex production handoff; this game has no production forecast."
                       if not production.accepted else "Production governance is checked per forecast."),
        }
        if production.accepted and research:
            production_view = {"status": production.status, **handoff.verify_forecast(
                production, forecast_id=research["forecast_id"], engine_id=research["model_id"],
                engine_version=research["model_revision"],
                model_artifact_sha256=research["model_artifact_sha256"],
                package_sha256=research["package_sha256"], population_sha256=research["population_sha256"],
                draw_count=research["draw_count"],
                published_utc=_aware(datetime.fromisoformat(research["published_utc"])),
                kickoff_utc=kickoff)}

        return {
            "game_id": str(game.id),
            "matchup": f"{away or '?'} @ {home or '?'}",
            "home": teams.get("home"), "away": teams.get("away"),
            "identity": {
                "nflverse": nflverse_ids,
                "the_odds_api": mapping.get("THE_ODDS_API", []),
                "other_sources": {k: v for k, v in mapping.items() if k not in {NFLVERSE, "THE_ODDS_API"}},
            },
            "kickoff_utc": _iso(kickoff),
            "kickoff_et": kickoff.astimezone(ET).strftime("%a %b %d %I:%M %p ET") if kickoff else None,
            "timezone": "America/New_York",
            "schedule_status": status.upper(),
            "forecasts": {
                "snapshots": len(snapshots),
                "by_type": dict(by_type),
                "published_at_or_after_kickoff": len(late),
                "other_numerical_models": other_models,
            },
            "views": {
                VIEW_RESEARCH: research,
                VIEW_PAPER: paper_rows,
                VIEW_PRODUCTION: production_view,
            },
            "expected_final_source": {
                "primary": "NFLVERSE (games.csv via ingest-nfl-finals, hourly)",
                "secondary": "The Odds API /scores (disarmed)" if not ownership["secondary_result_source"]["armed"]
                             else "The Odds API /scores",
                "status_limitation": "nflverse publishes no status column: a game is final once both "
                                     "scores appear; postponement or cancellation is not inferred.",
            },
            "outcome_state": outcome_state,
            "final": final,
            "evaluation_id": str(evaluation.id) if evaluation else None,
            "automatic_scoring": {"state": state, "blockers": blockers},
            "notes": notes,
            "positions": positions,
        }

    # --------------------------------------------------------- scorecard --
    def scorecard(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        rows = list(self.session.scalars(
            select(ResearchForecastEvaluation)
            .where(ResearchForecastEvaluation.league_code == LEAGUE,
                   ResearchForecastEvaluation.is_current.is_(True))
            .order_by(ResearchForecastEvaluation.kickoff_utc)))
        ids = {row.canonical_game_id for row in rows if row.canonical_game_id}
        teams = self._teams(ids)
        positions = self._positions(ids)
        papers = self._paper_supplements({row.forecast_identity for row in rows})
        production = handoff.production_status(self.settings)

        cohorts: dict[tuple[str, str], list[ResearchForecastEvaluation]] = defaultdict(list)
        for row in rows:
            cohorts[(row.engine_id, row.engine_version)].append(row)
        cohort_cards = [self._cohort(engine, version, entries, teams, positions, papers)
                        for (engine, version), entries in sorted(cohorts.items())]
        cohort_cards.sort(key=lambda c: c["settled_games"], reverse=True)

        return {
            "schema_version": SCORECARD_SCHEMA,
            "generated_at_utc": _iso(now),
            "league": LEAGUE,
            "scoring_version": SCORING_VERSION,
            "coverage": self._coverage(now),
            "cohorts": cohort_cards,
            "production_status": production.as_dict(),
            "benchmark": {
                "registered_prospective_benchmark": None,
                "statement": "No benchmark is registered as authoritative for prospective NFL comparison. "
                             "Where a two-sided pre-kickoff sportsbook quote was stored, the market's no-vig "
                             "probability is shown as a reference, not a benchmark and not a closing line.",
            },
            "small_sample_warning": outcomes.SMALL_SAMPLE_WARNING,
            "separation": {
                "model_scoring": "research_forecast_evaluations (this page)",
                "decision_policy": "shadow experiment decisions (Daily Decisions)",
                "virtual_bankroll": "experiment positions and settlements ($1,000 Experiment)",
                "my_card": "browser-local picks; never read here",
            },
            "scheduler_ownership": self.ownership(),
            "provider_calls": 0, "models_executed": 0, "positions_created": 0,
        }

    def _coverage(self, now: datetime) -> dict[str, Any]:
        """Every NFL game with an M1 research forecast whose kickoff has passed."""

        game_ids = list(self.session.scalars(
            select(Game.id).join(Season, Season.id == Game.season_id)
            .join(League, League.id == Season.league_id)
            .where(League.code == LEAGUE, Game.starts_at <= now,
                   select(ShadowForecast.id).where(ShadowForecast.canonical_game_id == Game.id,
                                                   ShadowForecast.engine_id == M1["engine_id"]).exists())))
        scored = set(self.session.scalars(
            select(ResearchForecastEvaluation.canonical_game_id).where(
                ResearchForecastEvaluation.league_code == LEAGUE,
                ResearchForecastEvaluation.is_current.is_(True),
                ResearchForecastEvaluation.engine_id == M1["engine_id"])))
        reasons: Counter[str] = Counter()
        open_games = 0
        for game_id in game_ids:
            if game_id in scored:
                continue
            game = self.session.get(Game, game_id)
            status = _status_value(game.status)
            if status in {"postponed", "canceled"}:
                reasons[f"GAME_{status.upper()}"] += 1
            elif self.evaluator.result_for(game) is None:
                open_games += 1
            else:
                frozen = self.evaluator.freeze_eligible(canonical_game_id=game_id)
                reasons["NO_PRE_KICKOFF_FORECAST" if not frozen else "SCORING_PENDING"] += 1
        upcoming = self.session.scalar(
            select(func.count()).select_from(Game).join(Season, Season.id == Game.season_id)
            .join(League, League.id == Season.league_id)
            .where(League.code == LEAGUE, Game.starts_at > now,
                   select(ShadowForecast.id).where(ShadowForecast.canonical_game_id == Game.id).exists())) or 0
        return {
            "eligible_games_started": len(game_ids),
            "settled_and_scored": len(scored & set(game_ids)),
            "open_awaiting_final": open_games,
            "unscored": sum(reasons.values()),
            "unscored_reasons": dict(reasons),
            "upcoming_with_forecast": int(upcoming),
        }

    def _cohort(self, engine: str, version: str, entries: list[ResearchForecastEvaluation],
                teams: dict[Any, dict[str, Any]], positions: dict[Any, list[dict[str, Any]]],
                papers: dict[str, list[ResearchSupplement]]) -> dict[str, Any]:
        games = [self._scored_game(row, teams.get(row.canonical_game_id, {}),
                                   positions.get(row.canonical_game_id, []), papers)
                 for row in entries]
        n = len(entries)
        winners = [row.winner_correct for row in entries]
        home_errors = [abs(row.home_score_error) for row in entries if row.home_score_error is not None]
        away_errors = [abs(row.away_score_error) for row in entries if row.away_score_error is not None]
        lines: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for game in games:
            for line in game["lines"]:
                if line.get("scored"):
                    lines[line["market"]].append(line)
        paper_games = [g for g in games if g["paper_view"]]
        kickoffs = [_aware(row.kickoff_utc) for row in entries if row.kickoff_utc]
        weeks = sorted({g["week"] for g in games if g["week"] is not None})
        return {
            "model_id": engine,
            "model_revision": version,
            "model_label": entries[0].model_label or engine,
            "authority": "RESEARCH_SHADOW",
            "probability_channel_authority": M1["authority"] if engine == M1["engine_id"] else None,
            "cohort_identity": {
                "model_id": engine, "model_revision": version,
                "scoring_version": entries[0].scoring_version,
                "pooled_with_other_versions": False,
            },
            "settled_games": n,
            "dates": sorted({k.astimezone(ET).date().isoformat() for k in kickoffs}),
            "weeks": weeks,
            "review": outcomes.review_progress(n),
            "summary": {
                "games": n,
                "correct_winners": sum(1 for w in winners if w),
                "winner_accuracy": (sum(1 for w in winners if w) / n) if n else None,
                "ties": sum(1 for row in entries if row.actual_outcome == scoring.TIE),
                "log_loss": outcomes.mean(row.log_loss for row in entries),
                "brier_score": outcomes.mean(row.brier_score for row in entries),
                "home_score_mae": outcomes.mean(home_errors),
                "away_score_mae": outcomes.mean(away_errors),
                "pooled_score_mae": outcomes.mean(home_errors + away_errors),
                "margin_mae": outcomes.mean(abs(row.margin_error) for row in entries if row.margin_error is not None),
                "total_mae": outcomes.mean(abs(row.total_error) for row in entries if row.total_error is not None),
                "mean_margin_error": outcomes.mean(row.margin_error for row in entries),
                "mean_total_error": outcomes.mean(row.total_error for row in entries),
                "probability_of_actual": outcomes.mean(row.probability_of_actual for row in entries),
            },
            "explanations": {
                "log_loss": "How surprised the forecast was by what happened, across home win, away win "
                            "and tie. Lower is better; a forecast that gave the result 50% scores 0.69.",
                "brier_score": "The squared miss across the three outcomes, from 0 (certain and right) to "
                               "2 (certain and wrong). Lower is better.",
                "winner_accuracy": "How often the most likely outcome happened. A tie counts as a miss for a "
                                   "home or away call.",
                "score_errors": "Average distance between predicted and final points; margin is home minus "
                                "away and total is both teams combined.",
            },
            "lines": {
                market: {
                    "scored": len(values),
                    "pushes": sum(1 for v in values if v["actual_state"] == "PUSH"),
                    "integer_lines": sum(1 for v in values if v["line_kind"] == "INTEGER"),
                    "mean_log_loss": outcomes.mean(v["log_loss"] for v in values),
                    "mean_brier_score": outcomes.mean(v["brier_score"] for v in values),
                    "basis": "The model's win/loss/push masses at the last stored pre-kickoff line "
                             "(not a closing line).",
                }
                for market, values in sorted(lines.items())
            },
            "calibration": outcomes.calibration([
                (float(g["probabilities"]["home"]), 1 if g["final"]["outcome"] == scoring.HOME else 0)
                for g in games if g["probabilities"] and g["probabilities"].get("home") is not None
            ]),
            "paper_view": {
                "games": len(paper_games),
                "same_revision_as_research": sum(1 for g in paper_games if g["paper_view"]["same_revision"]),
                "log_loss": outcomes.mean(g["paper_view"]["log_loss"] for g in paper_games),
                "brier_score": outcomes.mean(g["paper_view"]["brier_score"] for g in paper_games),
                "note": "Scores of the exact revision each paper-eligibility supplement names. Kept "
                        "separate from the research record and never added to it.",
            },
            "games": games,
        }

    def _scored_game(self, row: ResearchForecastEvaluation, teams: dict[str, Any],
                     positions: list[dict[str, Any]], papers: dict[str, list[ResearchSupplement]]) -> dict[str, Any]:
        detail = row.detail or {}
        published = detail.get("published_probabilities") or {}
        forecast = self.session.get(ShadowForecast, row.shadow_forecast_id) if row.shadow_forecast_id else None
        game = self.session.get(Game, row.canonical_game_id) if row.canonical_game_id else None
        nflverse = self.session.scalar(select(ExternalIdMapping.external_id).where(
            ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == NFLVERSE,
            ExternalIdMapping.entity_id == row.canonical_game_id)) if row.canonical_game_id else None
        revision = detail.get("controlling_revision")
        population = None
        if forecast is not None:
            if revision is None:
                revision = self._revision(forecast, _aware(row.kickoff_utc),
                                          reason="LATEST_PRE_KICKOFF_PUBLICATION_PER_MODEL_IDENTITY")
            try:
                population = verified_nfl_population(self.session, forecast)
            except ProbabilityUnavailable:
                population = None
        lines = self._lines(row, population)
        superseded = list(self.session.scalars(select(ResearchForecastEvaluation).where(
            ResearchForecastEvaluation.superseded_by_id == row.id)))
        final = self._final_detail(game) if game is not None else None
        return {
            "evaluation_id": str(row.id),
            "game_id": str(row.canonical_game_id) if row.canonical_game_id else None,
            "week": (_week_of(nflverse) or (None, None))[1] if nflverse else None,
            "nflverse_id": nflverse,
            "matchup": f"{(teams.get('away') or {}).get('abbreviation', '?')} @ "
                       f"{(teams.get('home') or {}).get('abbreviation', '?')}",
            "home": teams.get("home"), "away": teams.get("away"),
            "kickoff_utc": _iso(row.kickoff_utc),
            "probabilities": {"home": published.get("home"), "away": published.get("away"),
                              "tie": published.get("tie")},
            "predicted": {"home": row.predicted_home_score, "away": row.predicted_away_score,
                          "margin": row.predicted_margin, "total": row.predicted_total,
                          "outcome": row.predicted_outcome},
            "final": {"home": row.home_score, "away": row.away_score, "outcome": row.actual_outcome,
                      "margin": row.actual_margin, "total": row.actual_total,
                      "overtime": final["overtime"] if final else None},
            "errors": {"home": row.home_score_error, "away": row.away_score_error,
                       "margin": row.margin_error, "total": row.total_error},
            "scores": {"log_loss": row.log_loss, "brier_score": row.brier_score,
                       "winner_correct": row.winner_correct,
                       "probability_of_actual": row.probability_of_actual},
            "mass_reconciliation": detail.get("mass_reconciliation") or outcomes.mass_reconciliation(
                published.get("home"), published.get("away"), published.get("tie")),
            "lines": lines,
            "market_reference": self._market_reference(row),
            "paper_view": self._paper_view(row, papers, forecast),
            "audit": {
                "forecast_revision": revision,
                "population_sha256": population.population_sha256 if population else
                (revision or {}).get("population_sha256"),
                "kickoff_utc": _iso(row.kickoff_utc),
                "final_result": final,
                "scoring_receipt": {
                    "evaluation_id": str(row.id),
                    "scoring_version": row.scoring_version,
                    "evaluated_utc": _iso(row.evaluated_at),
                    "result_identity": row.result_identity,
                    "forecast_identity": row.forecast_identity,
                    "replaces": [{"evaluation_id": str(s.id), "reason": s.supersede_reason,
                                  "score": f"{s.home_score}-{s.away_score}",
                                  "result_identity": s.result_identity} for s in superseded],
                },
                "settlement_records": positions,
                "publication_clock": (detail.get("publication_clock") or {}).get("classification"),
            },
        }

    def _lines(self, row: ResearchForecastEvaluation, population) -> list[dict[str, Any]]:
        markets = ((row.market_evidence or {}).get("markets") or {})
        out = []
        for market in ("SPREAD", "TOTAL"):
            evidence = markets.get(market)
            if not evidence:
                continue
            line = evidence.get("home_handicap") if market == "SPREAD" else evidence.get("line")
            base = {"market": market, "line": line, "book": evidence.get("book"),
                    "observed_utc": evidence.get("observed_at_utc")}
            if line is None or population is None:
                out.append({**base, "scored": False,
                            "reason": "No line recorded." if line is None
                            else "The forecast population could not be verified."})
                continue
            try:
                scored = outcomes.evaluate_line(population, market, float(line), row.home_score, row.away_score)
            except outcomes.LineScoringError as exc:
                out.append({**base, "scored": False, "reason": str(exc)})
                continue
            out.append({**base, **scored, "scored": True})
        return out

    @staticmethod
    def _market_reference(row: ResearchForecastEvaluation) -> dict[str, Any] | None:
        moneyline = (((row.market_evidence or {}).get("markets") or {}).get("MONEYLINE") or {})
        if not moneyline.get("available"):
            return None
        return {"book": moneyline.get("book"), "observed_utc": moneyline.get("observed_at_utc"),
                "no_vig_home": moneyline.get("no_vig_home_probability"),
                "log_loss_two_way": moneyline.get("log_loss"), "brier_two_way": moneyline.get("brier_score"),
                "scored": moneyline.get("scored"), "reason": moneyline.get("reason")}

    def _paper_view(self, row: ResearchForecastEvaluation, papers: dict[str, list[ResearchSupplement]],
                    research_forecast: ShadowForecast | None) -> dict[str, Any] | None:
        """Score the exact revision a paper supplement names (it may not be the scored one)."""

        if research_forecast is None or row.canonical_game_id is None:
            return None
        forecasts = {f.forecast_id: f for f in self.session.scalars(select(ShadowForecast).where(
            ShadowForecast.canonical_game_id == row.canonical_game_id,
            ShadowForecast.engine_id == row.engine_id))}
        named = [(fid, sups) for fid, sups in self._paper_supplements(set(forecasts)).items()]
        if not named:
            return None
        forecast_id, supplements = sorted(named, key=lambda item: item[0])[0]
        forecast = forecasts[forecast_id]
        kickoff = _aware(row.kickoff_utc)
        published = _aware(forecast.available_as_of_utc)
        if not kickoff or not published or published >= kickoff:
            return {"forecast_id": forecast_id, "scored": False, "same_revision": False,
                    "reason": "The named revision was not published before kickoff.",
                    "log_loss": None, "brier_score": None}
        tie = self.evaluator._tie_probability(forecast)
        probabilities = scoring.OutcomeProbabilities(
            home=float(forecast.full_home_win_probability), away=float(forecast.full_away_win_probability), tie=tie)
        return {
            "forecast_id": forecast_id,
            "supplement_sha256": supplements[0].supplement_sha256,
            "authority": "PAPER_RESEARCH_EVIDENCE",
            "same_revision": forecast_id == row.forecast_identity,
            "scored": True,
            "published_utc": _iso(published),
            "log_loss": scoring.log_loss(probabilities, row.actual_outcome),
            "brier_score": scoring.brier_score(probabilities, row.actual_outcome),
            "result_identity": row.result_identity,
        }


__all__ = ["NflScoringReadinessService", "READINESS_SCHEMA", "SCORECARD_SCHEMA",
           "VIEW_PAPER", "VIEW_PRODUCTION", "VIEW_RESEARCH"]
