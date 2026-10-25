"""NHL read model: board, game detail, scoring. Read-only; no provider call, no write.

MARKETS AND WAGERING, AS AISP HOLDS THEM NOW
--------------------------------------------
Every payload carries ``markets`` and ``wagering`` statements read from the
database at build time (``paper_statements``), never hard-coded. Since
2026-10-16 the NHL paper era (AISP_NHL_PAPER_POLICY_V1, its own $1,000 2026-27
account) is open: AISP runs the frozen Gen1 model in-process, The Odds API NHL
lane collects each game's full-game two-way moneyline (overtime and shootout
included) 75 to 35 minutes before puck drop, and the checkpoint decides 30 to 15
minutes before puck drop. Each game row carries its recorded paper decision and
position (``paper``), exactly as stored. With no era open the statements say so
(``NO_NHL_MARKET_PRICES_COLLECTED`` / wagering disabled), so a component that
looks for a position finds an explicit "none", not a gap. This module never
computes an edge or a recommendation itself: it reports what the checkpoint
recorded.

SCORING
-------
Two outcomes, never pooled:

* REGULATION (3-way, result at 60:00): Brier summed over HOME/TIE/AWAY (0-2) and
  three-way log loss, against ``regulation_result``.
* FINAL (2-way, including OT and SO): Brier and log loss of the home-win
  probability against the official final winner.

A forecast is scored only if it was published before the scheduled start and
the game has an official final; the scored forecast is the latest one
published before the start. Nothing is scored against an assumed result.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import ExternalIdMapping, Game, GameTeam, League, Team
from app.models.nhl_research import (
    NHL_GAME_SOURCE,
    NHL_LEAGUE_CODE,
    NHL_TEAM_SOURCE,
    NhlGameResult,
    NhlImportReceipt,
    NhlResearchForecast,
)

EASTERN = ZoneInfo("America/New_York")
EPSILON = 1e-15
BOARD_SCHEMA = "AISP_NHL_RESEARCH_BOARD_V1"
GAME_SCHEMA = "AISP_NHL_RESEARCH_GAME_V1"
ENGINE_REGULATION = "NHL_GEN1_REGULATION_3WAY"
ENGINE_FINAL = "NHL_GEN1_FINAL_2WAY"

#: The statements when NO NHL paper era is open and no NHL quote is stored (fixtures, a fresh database).
MARKETS = {
    "status": "NO_NHL_MARKET_PRICES_COLLECTED",
    "statement": "No NHL market prices collected. AISP holds no NHL sportsbook or exchange quote, so no "
                 "market comparison, edge or price is shown.",
}
WAGERING = {
    "enabled": False,
    "official_positions": 0,
    "statement": "Research only. NHL wagering is disabled: no official NHL positions, no paper positions, "
                 "no edges and no recommendations exist.",
}
PAPER_FORECAST_LABEL = "Paper forecast — decided 30–15 min before puck drop"
RESEARCH_FORECAST_LABEL = "Research forecast — wagering disabled"
OUTCOME_DEFINITIONS = {
    "regulation": "Result at the end of regulation (60:00): HOME, TIE or AWAY. A tie goes to overtime/shootout.",
    "final": "Official final including overtime and shootout: HOME or AWAY. An NHL final never ties.",
    "goals_regulation": "Regulation goals exclude overtime and shootout.",
    "goals_final": "Final goals follow NHL scoring: an overtime goal counts, and a shootout adds one goal to the "
                   "shootout winner.",
}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat().replace("+00:00", "Z") if value else None


def current_result(results: list[NhlGameResult]) -> NhlGameResult | None:
    """The result no other result supersedes; the latest import if several qualify."""

    superseded = {r.supersedes_result_sha256 for r in results if r.supersedes_result_sha256}
    live = [r for r in results if r.result_sha256 not in superseded]
    return max(live, key=lambda r: _aware(r.imported_at)) if live else None


def publication(row: NhlResearchForecast) -> datetime:
    return _aware(row.published_utc) or _aware(row.forecast_created_utc)  # type: ignore[return-value]


def current_forecast(rows: list[NhlResearchForecast]) -> NhlResearchForecast | None:
    return max(rows, key=lambda r: (publication(r), _aware(r.imported_at))) if rows else None


def scored_forecast(rows: list[NhlResearchForecast]) -> NhlResearchForecast | None:
    eligible = [r for r in rows if publication(r) < _aware(r.scheduled_start_utc)]  # type: ignore[operator]
    return current_forecast(eligible)


def has_regulation(row: NhlResearchForecast) -> bool:
    return row.regulation_home_probability is not None


def regulation_scores(row: NhlResearchForecast, outcome: str) -> dict[str, Any] | None:
    """Three-way scores, or None when the model did not supply regulation ("not modelled")."""

    if not has_regulation(row):
        return None
    probs = {"HOME": row.regulation_home_probability, "TIE": row.regulation_tie_probability,
             "AWAY": row.regulation_away_probability}
    brier = sum((p - (1.0 if k == outcome else 0.0)) ** 2 for k, p in probs.items())
    predicted = max(probs, key=lambda k: probs[k])
    return {"brier_three_way": brier, "log_loss_three_way": -math.log(max(probs[outcome], EPSILON)),
            "predicted": predicted, "correct": predicted == outcome, "p_outcome": probs[outcome]}


def final_scores(row: NhlResearchForecast, winner: str) -> dict[str, Any]:
    p = row.final_home_probability
    y = 1.0 if winner == "HOME" else 0.0
    predicted = "HOME" if p > 0.5 else "AWAY" if p < 0.5 else "EVEN"
    return {"brier_two_way": (p - y) ** 2, "log_loss_two_way": -math.log(max(p if y else 1 - p, EPSILON)),
            "predicted": predicted, "correct": predicted == winner, "p_home": p}


def _et_label(value: datetime | None) -> str | None:
    value = _aware(value)
    if value is None:
        return None
    local = value.astimezone(EASTERN)
    hour = local.hour % 12 or 12
    return f"{local:%a %b} {local.day}, {hour}:{local:%M} {'AM' if local.hour < 12 else 'PM'} ET"


def paper_statements(session: Session) -> tuple[dict[str, Any], dict[str, Any]]:
    """``markets`` and ``wagering`` as AISP holds them now (read-only, a handful of aggregate reads).

    No era and no stored quote: the research-only constants above, unchanged. Otherwise
    the stored NHL quote count and newest quote time, The Odds API lane's switch, and the
    era's recorded positions (open / settled) -- each stated, never inferred.
    """

    from sqlalchemy import func

    from app.models.entities import (
        Market, MarketOutcome, OddsSnapshot, OfficialAspPosition, PositionSettlement, PositionStatus,
    )

    try:
        from app.nhl import paper

        era = paper.experiment(session)
    except Exception:  # noqa: BLE001 -- an unreadable era reads as "no era", never as a fabricated one
        era = None
    try:
        from app.core.config import get_settings

        lane_on = bool(getattr(get_settings(), "nhl_odds_api_enabled", False))
    except Exception:  # noqa: BLE001
        lane_on = False
    quotes, newest = session.execute(
        select(func.count(OddsSnapshot.id), func.max(OddsSnapshot.observed_at))
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Game, Game.id == Market.game_id).join(League, League.id == Game.league_id)
        .where(League.code == NHL_LEAGUE_CODE, OddsSnapshot.source_mode == "LIVE")).one()
    quotes = int(quotes or 0)
    window = ("The Odds API NHL lane collects each game's full-game two-way moneyline (overtime and shootout "
              "included) once, 75 to 35 minutes before puck drop")
    if quotes:
        markets = {"status": "NHL_MARKET_PRICES_COLLECTED", "quotes_stored": quotes,
                   "newest_observed_at": _iso(newest), "collector_enabled": lane_on,
                   "statement": (f"{window}{'' if lane_on else ' (the lane is switched off now)'}. "
                                 f"{quotes} NHL quote{'' if quotes == 1 else 's'} stored; newest "
                                 f"{_et_label(newest)}. A quote is compared only with the forecast's final "
                                 "two-way channel, at the decision checkpoint.")}
    elif era is not None and lane_on:
        markets = {"status": "NHL_PRICE_COLLECTION_SCHEDULED", "quotes_stored": 0, "newest_observed_at": None,
                   "collector_enabled": True,
                   "statement": f"{window}. No NHL quote is stored yet."}
    else:
        markets = {**MARKETS, "quotes_stored": 0, "collector_enabled": lane_on}
    if era is None:
        return markets, dict(WAGERING)
    total, open_count = session.execute(
        select(func.count(OfficialAspPosition.id),
               func.count(OfficialAspPosition.id).filter(OfficialAspPosition.status == PositionStatus.OPEN))
        .where(OfficialAspPosition.shadow_experiment_id == era.id)).one()
    settled = session.scalar(
        select(func.count(PositionSettlement.id)).join(
            OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
        .where(OfficialAspPosition.shadow_experiment_id == era.id,
               PositionSettlement.profit_loss.is_not(None))) or 0
    total, open_count = int(total or 0), int(open_count or 0)
    wagering = {
        "enabled": True, "mode": "VIRTUAL_PAPER", "policy": era.decision_policy, "era": {
            "experiment_id": str(era.id), "name": (era.name or "").replace("\ufffd", "-")},
        "official_positions": total, "paper_positions": total, "open_positions": open_count,
        "settled_positions": int(settled),
        "statement": (f"Virtual paper only, no real money. AISP decides each NHL game 30 to 15 minutes before puck "
                      f"drop under {era.decision_policy} (the $1,000 2026-27 NHL account), on its in-process Gen1 "
                      f"final two-way forecast against The Odds API moneyline. {total} paper position"
                      f"{'' if total == 1 else 's'} recorded ({open_count} open, {int(settled)} settled)."),
    }
    return markets, wagering


def paper_decisions(session: Session) -> dict[Any, dict[str, Any]]:
    """The latest recorded NHL checkpoint decision per game, with its position, as stored (read-only)."""

    from app.models.entities import OfficialAspPosition, PositionSettlement, ShadowExperimentDecision

    try:
        from app.nhl import paper

        era = paper.experiment(session)
    except Exception:  # noqa: BLE001
        return {}
    if era is None:
        return {}
    out: dict[Any, dict[str, Any]] = {}
    for decision, position, settlement in session.execute(
            select(ShadowExperimentDecision, OfficialAspPosition, PositionSettlement)
            .outerjoin(OfficialAspPosition, OfficialAspPosition.shadow_decision_id == ShadowExperimentDecision.id)
            .outerjoin(PositionSettlement, PositionSettlement.position_id == OfficialAspPosition.id)
            .where(ShadowExperimentDecision.experiment_id == era.id)
            .order_by(ShadowExperimentDecision.recommended_at)).all():
        reasons = decision.qualification_reasons if isinstance(decision.qualification_reasons, dict) else {}
        out[decision.game_id] = {
            "decision": {"decision_id": str(decision.id), "recommendation": decision.recommendation,
                         "reason": reasons.get("reason"), "detail": decision.recommendation_reason,
                         "recorded_at": _iso(decision.recommended_at), "selection": decision.selection,
                         "sportsbook": decision.sportsbook, "american_odds": decision.american_odds,
                         "model_probability": decision.model_probability,
                         "no_vig_probability": decision.no_vig_probability, "edge": decision.edge,
                         "expected_return": decision.expected_value},
            "position": None if position is None else {
                "position_id": str(position.id), "stake": float(position.stake) if position.stake is not None else None,
                "status": "SETTLED" if settlement is not None and settlement.profit_loss is not None
                else str(getattr(position.status, "value", position.status)).upper(),
                "result": (str(getattr(settlement.result, "value", settlement.result)).upper()
                           if settlement is not None else None),
                "profit_loss": float(settlement.profit_loss) if settlement is not None
                and settlement.profit_loss is not None else None},
        }
    return out


def resolve_source_game_id(session: Session, game_id: str) -> str | None:
    """A ten-digit NHL game id, also when AISP's canonical game id (a UUID) is given.

    Cross-sport surfaces (decisions, the ledger) link a game by its AISP id; the NHL
    pages are addressed by the NHL id. The game's ``nhl:game:<id>`` mapping translates
    one to the other. Returns None for anything that is neither.
    """

    import uuid

    text_id = str(game_id).strip()
    if text_id.isdigit() and len(text_id) == 10:
        return text_id
    try:
        canonical = uuid.UUID(text_id)
    except ValueError:
        return None
    mapped = session.scalar(select(ExternalIdMapping.external_id).where(
        ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == NHL_GAME_SOURCE,
        ExternalIdMapping.entity_id == canonical).limit(1))
    if mapped is None:
        forecast_ref = session.scalar(select(NhlResearchForecast.canonical_game_ref).where(
            NhlResearchForecast.game_id == canonical).limit(1))
        mapped = forecast_ref
    if mapped is None:
        return None
    source = str(mapped).rsplit(":", 1)[-1]
    return source if source.isdigit() and len(source) == 10 else None


class NhlReadService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._statements: tuple[dict[str, Any], dict[str, Any]] | None = None
        self._paper: dict[Any, dict[str, Any]] | None = None

    def statements(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """(markets, wagering), read once per service instance."""

        if self._statements is None:
            self._statements = paper_statements(self.session)
        return self._statements

    def paper(self) -> dict[Any, dict[str, Any]]:
        if self._paper is None:
            self._paper = paper_decisions(self.session)
        return self._paper

    # -- loading ---------------------------------------------------------------
    def _games(self) -> list[Game]:
        league = self.session.scalar(select(League).where(League.code == NHL_LEAGUE_CODE))
        if league is None:
            return []
        return list(self.session.scalars(select(Game).where(Game.league_id == league.id).order_by(Game.starts_at)))

    def _teams(self, game_ids: list[Any]) -> dict[Any, dict[str, dict[str, Any]]]:
        if not game_ids:
            return {}
        rows = self.session.execute(
            select(GameTeam.game_id, GameTeam.is_home, GameTeam.score, Team.id, Team.name, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id.in_(game_ids))).all()
        team_refs = {m.entity_id: m.external_id for m in self.session.scalars(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == "team", ExternalIdMapping.source == NHL_TEAM_SOURCE))}
        out: dict[Any, dict[str, dict[str, Any]]] = defaultdict(dict)
        for game_id, is_home, score, team_id, name, abbr in rows:
            ref = team_refs.get(team_id)
            out[game_id]["home" if is_home else "away"] = {
                "name": name, "abbreviation": abbr, "score": score,
                "canonical_team_id": ref}
        return out

    def _game_refs(self) -> dict[Any, str]:
        return {m.entity_id: m.external_id for m in self.session.scalars(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == NHL_GAME_SOURCE))}

    def _by_ref(self) -> tuple[dict[str, list[NhlResearchForecast]], dict[str, list[NhlGameResult]]]:
        forecasts: dict[str, list[NhlResearchForecast]] = defaultdict(list)
        for row in self.session.scalars(select(NhlResearchForecast)):
            forecasts[row.canonical_game_ref].append(row)
        results: dict[str, list[NhlGameResult]] = defaultdict(list)
        for row in self.session.scalars(select(NhlGameResult)):
            results[row.canonical_game_ref].append(row)
        return forecasts, results

    # -- shaping ------------------------------------------------------------------
    @staticmethod
    def forecast_summary(row: NhlResearchForecast, now: datetime) -> dict[str, Any]:
        doc = row.document or {}
        published = publication(row)
        return {
            "forecast_id": row.forecast_id,
            "model_id": row.model_id,
            "model_version": row.model_version,
            "research_status": "RESEARCH_ONLY",
            "record_class": row.record_class,
            "provenance": {"evidence_mode": row.evidence_mode, "goalie_feature_mode": row.goalie_feature_mode,
                           "producer_evidence_status": row.producer_evidence_status,
                           # The binding only ({path, sha256, bytes}): an in-process forecast's column also
                           # holds its whole evidence document, which the read surface does not need to ship.
                           "producer_evidence_ref": {k: (row.producer_evidence_ref or {}).get(k)
                                                     for k in ("path", "sha256", "bytes")}},
            "regulation_modelled": has_regulation(row),
            "probabilities": {
                "regulation": ({"home": row.regulation_home_probability, "tie": row.regulation_tie_probability,
                                "away": row.regulation_away_probability} if has_regulation(row) else None),
                "final": {"home": row.final_home_probability, "away": row.final_away_probability},
                "overtime": row.overtime_probability,
                "shootout": row.shootout_probability,
                "overtime_detail": (doc.get("probabilities") or {}).get("overtime"),
            },
            "goals": {
                "regulation": ({"home": row.regulation_expected_home_goals,
                                "away": row.regulation_expected_away_goals, "horizon": "REGULATION_60_MINUTES"}
                               if row.regulation_expected_home_goals is not None else None),
                "final": ({"home": row.final_expected_home_goals, "away": row.final_expected_away_goals,
                           "horizon": "FINAL_INCLUDING_OT_AND_SO_WINNER_GOAL"}
                          if row.final_expected_home_goals is not None else None),
                "units": "goals",
            },
            "goalies": row.goalies,
            "missing_inputs": row.missing_inputs,
            "limitations": doc.get("limitations") or [],
            "clocks": {
                "source_cutoff_utc": _iso(row.source_cutoff_utc),
                "source_retrieved_utc": _iso(row.source_retrieved_utc),
                "created_utc": _iso(row.forecast_created_utc),
                "published_utc": _iso(row.published_utc),
                "publication_clock_source": row.publication_clock_source,
                "available_as_of": _iso(row.available_as_of),
                "imported_at": _iso(row.imported_at),
            },
            "age_seconds": max(0, int((now - published).total_seconds())),
            "published_before_start": published < _aware(row.scheduled_start_utc),  # type: ignore[operator]
            "supersedes_forecast_id": row.supersedes_forecast_id,
            "object_sha256": row.object_sha256,
        }

    @staticmethod
    def result_summary(row: NhlGameResult | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "result_sha256": row.result_sha256,
            "regulation": {"home_goals": row.regulation_home_goals, "away_goals": row.regulation_away_goals,
                           "result": row.regulation_result, "source": row.regulation_source,
                           "observed_utc": _iso(row.regulation_observed_utc),
                           "retrieved_utc": _iso(row.regulation_retrieved_utc)},
            "decided_in": row.decided_in,
            "overtime": ({"home_goals": row.overtime_home_goals, "away_goals": row.overtime_away_goals}
                         if row.overtime_home_goals is not None else None),
            "shootout": ({"home_scored": row.shootout_home_scored, "away_scored": row.shootout_away_scored}
                         if row.shootout_home_scored is not None else None),
            "final": {"home_score": row.final_home_score, "away_score": row.final_away_score,
                      "winner": row.final_winner, "official": row.final_official, "source": row.final_source,
                      "observed_utc": _iso(row.final_observed_utc), "retrieved_utc": _iso(row.final_retrieved_utc),
                      "completed_utc": _iso(row.final_completed_utc)},
            "status_label": {"REGULATION": "Final", "OVERTIME": "Final OT", "SHOOTOUT": "Final SO"}[row.decided_in],
            "supersedes_result_sha256": row.supersedes_result_sha256,
        }

    def _row(self, *, ref: str, game: Game | None, teams: dict[str, Any], forecasts: list[NhlResearchForecast],
             results: list[NhlGameResult], now: datetime) -> dict[str, Any]:
        markets, wagering = self.statements()
        forecast = current_forecast(forecasts)
        result = current_result(results)
        start = _aware(game.starts_at) if game else (_aware(forecast.scheduled_start_utc) if forecast else None)
        status = (game.status.value if game is not None and hasattr(game.status, "value") else
                  str(game.status) if game is not None else "scheduled")
        if result is not None and result.final_official:
            status = "final"
        elif status == "scheduled" and start is not None and start <= now:
            # Past its start with no source-backed result: it was (presumably) played, but AISP has
            # no result to show or score. Never "Scheduled", never a guessed final.
            status = "played_awaiting_result"
        doc_game = (forecast.document or {}).get("game") if forecast else None
        if not teams and doc_game:
            teams = {side: {"name": doc_game[side]["name"], "abbreviation": doc_game[side]["triCode"], "score": None,
                            "canonical_team_id": doc_game[side]["canonicalTeamId"]} for side in ("home", "away")}
        scored = None
        sf = scored_forecast(forecasts)
        if result is not None and sf is not None and sf.record_class != "SYNTHETIC_FIXTURE":
            scored = {"forecast_id": sf.forecast_id, "record_class": sf.record_class,
                      "regulation": regulation_scores(sf, result.regulation_result),
                      "final": final_scores(sf, result.final_winner)}
        return {
            "canonical_game_id": ref,
            "source_game_id": ref.removeprefix("nhl:game:"),
            "aisp_game_id": str(game.id) if game else None,
            "game_bound": game is not None,
            "source_mode": game.source_mode if game else None,
            "is_fixture": bool((game and game.source_mode in ("FIXTURE", "SYNTHETIC"))
                               or (forecast and forecast.record_class != "FORECAST")),
            "record_class": (forecast.record_class if forecast else
                             ("PREVIEW_FIXTURE" if game and game.source_mode == "FIXTURE" else None)),
            "models": sorted({f.model_id for f in forecasts}),
            "season": forecast.season if forecast else None,
            "game_type": forecast.game_type if forecast else (
                {"01": "PRESEASON", "02": "REGULAR", "03": "PLAYOFF"}.get(ref[-6:-4])),
            "start_utc": _iso(start),
            "et_date": start.astimezone(EASTERN).date().isoformat() if start else None,
            "status": status,
            "status_label": (self.result_summary(result) or {}).get("status_label") if result else (
                "Scheduled" if status == "scheduled" else "Final" if status == "final" else
                "Played — result not yet received" if status == "played_awaiting_result" else status.title()),
            "home": teams.get("home"),
            "away": teams.get("away"),
            "score": ({"home": result.final_home_score, "away": result.final_away_score, "units": "goals"}
                      if result else ({"home": game.home_score, "away": game.away_score, "units": "goals"}
                                      if game and game.home_score is not None else None)),
            "forecast": self.forecast_summary(forecast, now) if forecast else None,
            "forecast_state": "FORECAST_AVAILABLE" if forecast else "FORECAST_PENDING",
            "forecast_label": ((PAPER_FORECAST_LABEL if wagering.get("enabled") else RESEARCH_FORECAST_LABEL)
                               if forecast else "Forecast pending"),
            "revision_count": len(forecasts),
            "result": self.result_summary(result),
            "scored": scored,
            "markets": markets,
            "wagering": wagering,
            # The recorded checkpoint decision and position for this game (None until one is recorded).
            "paper": self.paper().get(game.id) if game is not None else None,
        }

    # -- public ------------------------------------------------------------------
    def board(self, *, date: str | None = None, now: datetime | None = None,
              include_test_data: bool = False) -> dict[str, Any]:
        """Real games only by default. Preview and synthetic fixtures appear only on request."""

        now = now or datetime.now(UTC)
        games = self._games()
        refs = self._game_refs()
        teams = self._teams([g.id for g in games])
        forecasts, results = self._by_ref()
        rows, seen = [], set()
        for game in games:
            ref = refs.get(game.id)
            if ref is None:
                continue
            seen.add(ref)
            rows.append(self._row(ref=ref, game=game, teams=teams.get(game.id, {}), forecasts=forecasts.get(ref, []),
                                  results=results.get(ref, []), now=now))
        for ref, items in forecasts.items():  # unbound forecasts are listed, never matched by name/date
            if ref not in seen:
                rows.append(self._row(ref=ref, game=None, teams={}, forecasts=items, results=results.get(ref, []),
                                      now=now))
        test_rows = sum(1 for r in rows if r["is_fixture"])
        if not include_test_data:
            rows = [r for r in rows if not r["is_fixture"]]
        rows.sort(key=lambda r: r["start_utc"] or "")
        dates = sorted({r["et_date"] for r in rows if r["et_date"]})
        if date:
            rows = [r for r in rows if r["et_date"] == date]
        markets, wagering = self.statements()
        return {
            "schema_version": BOARD_SCHEMA, "league": NHL_LEAGUE_CODE, "generated_at_utc": _iso(now),
            "date": date, "dates": dates, "games": rows,
            "counts": {"games": len(rows), "with_forecast": sum(1 for r in rows if r["forecast"]),
                       "finals": sum(1 for r in rows if r["result"])},
            "paper_counts": {"decisions": sum(1 for r in rows if r.get("paper")),
                             "positions": sum(1 for r in rows if (r.get("paper") or {}).get("position"))},
            "markets": markets, "wagering": wagering, "outcome_definitions": OUTCOME_DEFINITIONS,
            "include_test_data": include_test_data, "test_data_rows": test_rows,
            "fixture_notice": ("Rows marked PREVIEW FIXTURE or SYNTHETIC are invented test data, not real games, "
                               "forecasts or results." if any(r["is_fixture"] for r in rows) else None),
        }

    def game(self, source_game_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        now = now or datetime.now(UTC)
        ref = f"nhl:game:{source_game_id}"
        board = self.board(now=now, include_test_data=True)
        row = next((r for r in board["games"] if r["canonical_game_id"] == ref), None)
        if row is None:
            return None
        forecasts = sorted(self.session.scalars(select(NhlResearchForecast).where(
            NhlResearchForecast.canonical_game_ref == ref)), key=lambda r: (publication(r), _aware(r.imported_at)))
        results = list(self.session.scalars(select(NhlGameResult).where(NhlGameResult.canonical_game_ref == ref)))
        receipts = list(self.session.scalars(select(NhlImportReceipt).where(
            NhlImportReceipt.canonical_game_ref == ref).order_by(NhlImportReceipt.received_at)))
        current = current_forecast(forecasts)
        row.update({
            "schema_version": GAME_SCHEMA,
            "history": [{**self.forecast_summary(f, now), "is_current": f is current} for f in reversed(forecasts)],
            "results_history": [self.result_summary(r) for r in results],
            "import_receipts": [{"disposition": r.disposition, "kind": r.kind, "identity": r.identity,
                                 "problems": r.problems, "received_at": _iso(r.received_at),
                                 "source": r.source_label} for r in receipts],
            "outcome_definitions": OUTCOME_DEFINITIONS,
        })
        return row

    def receipts(self, limit: int = 200) -> dict[str, Any]:
        rows = list(self.session.scalars(select(NhlImportReceipt).order_by(NhlImportReceipt.received_at.desc())
                                         .limit(limit)))
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in self.session.scalars(select(NhlImportReceipt)):
            counts[r.kind][r.disposition] += 1
        return {"counts": {k: dict(v) for k, v in counts.items()},
                "receipts": [{"run_id": r.run_id, "kind": r.kind, "disposition": r.disposition, "identity": r.identity,
                              "canonical_game_ref": r.canonical_game_ref, "problems": r.problems,
                              "received_at": _iso(r.received_at), "source": r.source_label} for r in rows]}

    # -- scoring ---------------------------------------------------------------------
    def scored_games(self, include_test_data: bool = False) -> list[dict[str, Any]]:
        return [r for r in self.board(include_test_data=include_test_data)["games"] if r["scored"]]

    def performance_models(self, include_test_data: bool = False) -> list[dict[str, Any]]:
        """Model Performance (Detailed) entries: regulation 3-way and final 2-way, never pooled.

        Regulation is scored only where the model supplied regulation probabilities;
        final is scored for every forecast. Preview-fixture rows are a separate,
        labelled population and never mix with real forecasts.
        """

        games = self.scored_games(include_test_data)
        by_model: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for g in games:
            by_model[(g["scored"]["record_class"], g["forecast"]["model_id"], g["forecast"]["model_version"])].append(g)
        models = []
        for (record_class, model_id, version), entries in sorted(by_model.items()):
            regulated = [e for e in entries if e["scored"]["regulation"] is not None]
            if regulated:
                models.append(self._regulation_entry(model_id, version, regulated, record_class))
            models.append(self._final_entry(model_id, version, entries, record_class,
                                            regulation_not_modelled=len(entries) - len(regulated)))
        return models

    @staticmethod
    def _period(entries: list[dict[str, Any]]) -> dict[str, Any]:
        starts = sorted(e["start_utc"] for e in entries if e["start_utc"])
        return {"first_start_utc": starts[0] if starts else None, "last_start_utc": starts[-1] if starts else None}

    def _regulation_entry(self, model_id: str, version: str, entries: list[dict[str, Any]],
                          record_class: str) -> dict[str, Any]:
        regs = [e["scored"]["regulation"] for e in entries]
        metrics = {
            "n": len(entries), "period": self._period(entries),
            "accuracy": sum(1 for r in regs if r["correct"]) / len(regs) if regs else None,
            "correct_winners": sum(1 for r in regs if r["correct"]),
            "log_loss": fmean(r["log_loss_three_way"] for r in regs) if regs else None,
            "brier_three_class": fmean(r["brier_three_way"] for r in regs) if regs else None,
            "two_way_n": 0, "brier_two_way": None, "log_loss_two_way": None, "auc": None,
            "margin_mae": None, "total_mae": None, "margin_errors": [], "total_errors": [],
            "market": None, "calibration": [], "series": [],
            "regulation_ties": sum(1 for e in entries if e["result"]["regulation"]["result"] == "TIE"),
        }
        return self._entry(ENGINE_REGULATION, model_id, version, "Regulation result (3-way: home / tie / away at 60:00)",
                           "REGULATION_3WAY", metrics, entries, "regulation", record_class)

    def _final_entry(self, model_id: str, version: str, entries: list[dict[str, Any]], record_class: str,
                     regulation_not_modelled: int = 0) -> dict[str, Any]:
        fins = [e["scored"]["final"] for e in entries]
        points = [(f["p_home"], e["result"]["final"]["winner"] == "HOME") for f, e in zip(fins, entries)]
        metrics = {
            "n": len(entries), "period": self._period(entries),
            "accuracy": sum(1 for f in fins if f["correct"]) / len(fins) if fins else None,
            "correct_winners": sum(1 for f in fins if f["correct"]),
            "log_loss": None, "brier_three_class": None,
            "two_way_n": len(fins),
            "brier_two_way": fmean(f["brier_two_way"] for f in fins) if fins else None,
            "log_loss_two_way": fmean(f["log_loss_two_way"] for f in fins) if fins else None,
            "auc": _auc(points), "margin_mae": None, "total_mae": None, "margin_errors": [], "total_errors": [],
            "market": None, "calibration": [], "series": [],
            "decided_in": {k: sum(1 for e in entries if e["result"]["decided_in"] == k)
                           for k in ("REGULATION", "OVERTIME", "SHOOTOUT")},
            "regulation_not_modelled": regulation_not_modelled,
        }
        return self._entry(ENGINE_FINAL, model_id, version, "Final winner (2-way, including overtime and shootout)",
                           "FINAL_2WAY", metrics, entries, "final", record_class)

    @staticmethod
    def _entry(engine: str, model_id: str, version: str, label: str, outcome: str, metrics: dict[str, Any],
               entries: list[dict[str, Any]], key: str, record_class: str = "FORECAST") -> dict[str, Any]:
        games = []
        for e in reversed(entries):
            s = e["scored"][key]
            games.append({
                "evaluation_id": f"{e['scored']['forecast_id']}:{outcome}",
                "canonical_game_id": e["aisp_game_id"], "nhl_game_id": e["canonical_game_id"],
                "start_utc": e["start_utc"],
                "home": (e["home"] or {}).get("abbreviation"), "away": (e["away"] or {}).get("abbreviation"),
                "final": {"home": e["result"]["final"]["home_score"], "away": e["result"]["final"]["away_score"]},
                "regulation": {"home": e["result"]["regulation"]["home_goals"],
                               "away": e["result"]["regulation"]["away_goals"]},
                "decided_in": e["result"]["decided_in"],
                "home_probability_two_way": s.get("p_home") if key == "final" else None,
                "probabilities": e["forecast"]["probabilities"][key],
                "regulation_modelled": e["forecast"]["regulation_modelled"],
                "predicted_outcome": s["predicted"],
                "actual_outcome": (e["result"]["regulation"]["result"] if key == "regulation"
                                   else e["result"]["final"]["winner"]),
                "winner_correct": s["correct"],
                "log_loss": s.get("log_loss_three_way") if key == "regulation" else s.get("log_loss_two_way"),
                "brier_three_class": s.get("brier_three_way") if key == "regulation" else None,
                "brier_two_way": s.get("brier_two_way") if key == "final" else None,
                "predicted_score": {"home": None, "away": None}, "margin_error": None, "total_error": None,
                "market_no_vig_home": None, "market_book": None, "publication_clock": "PRODUCER_PUBLISHED_AT",
                "published_before_start_hours": None, "is_fixture": e["is_fixture"],
            })
        fixture = record_class != "FORECAST"
        return {
            "engine_id": engine, "engine_version": f"{model_id}@{version}",
            "model_label": ("[PREVIEW FIXTURE - not real] " if fixture else "")
                           + f"NHL Gen1 {model_id} {version} · {label}",
            "outcome_definition": outcome, "is_probability": True, "record_class": record_class,
            "scoring_version": "AISP_NHL_SCORING_V1", "snapshot_types": [],
            "evidence_classification": "PREVIEW_FIXTURE_NOT_EVIDENCE" if fixture else "PROSPECTIVE_RESEARCH_SCORES",
            # No independently verified publication clock exists for NHL (only the
            # producer's own published-at), so the verified cohort is EMPTY -- never a copy of ALL.
            "cohorts": {"ALL": metrics, "VERIFIED_PUBLICATION": _empty_cohort(metrics)},
            "cohort_note": ("Scored only when the forecast was published before the scheduled start and the game has "
                            "an official final. Regulation (3-way) and final (2-way) are separate outcomes and are "
                            "never pooled. No NHL market price is held, so there is no market comparison. NHL has no "
                            "independent publication clock, so every score shown is the all-scored cohort."),
            "games": games,
        }

    def overview_block(self, include_test_data: bool = False) -> dict[str, Any]:
        """The NHL section of the Model Performance Overview. Real forecasts and fixtures kept apart;
        fixtures are counted only when ``include_test_data`` is set."""

        board = self.board(include_test_data=include_test_data)
        games = self.scored_games(include_test_data)
        populations = {}
        for cls in ("FORECAST", "PREVIEW_FIXTURE"):
            members = [g for g in games if g["scored"]["record_class"] == cls]
            regs = [g["scored"]["regulation"] for g in members if g["scored"]["regulation"] is not None]
            fins = [g["scored"]["final"] for g in members]
            populations[cls] = {
                "scored": len(members),
                "regulation_3way": {
                    "n": len(regs), "not_modelled": len(members) - len(regs),
                    "outcome": OUTCOME_DEFINITIONS["regulation"],
                    "brier_three_way": fmean(r["brier_three_way"] for r in regs) if regs else None,
                    "log_loss_three_way": fmean(r["log_loss_three_way"] for r in regs) if regs else None,
                    "accuracy": sum(1 for r in regs if r["correct"]) / len(regs) if regs else None,
                    "ties_observed": sum(1 for g in members if g["scored"]["regulation"] is not None
                                         and g["result"]["regulation"]["result"] == "TIE"),
                    "brier_range": "0 to 2 (summed over home, tie, away)",
                },
                "final_2way": {
                    "n": len(fins), "outcome": OUTCOME_DEFINITIONS["final"],
                    "brier_two_way": fmean(f["brier_two_way"] for f in fins) if fins else None,
                    "log_loss_two_way": fmean(f["log_loss_two_way"] for f in fins) if fins else None,
                    "accuracy": sum(1 for f in fins if f["correct"]) / len(fins) if fins else None,
                    "decided_in": {k: sum(1 for g in members if g["result"]["decided_in"] == k)
                                   for k in ("REGULATION", "OVERTIME", "SHOOTOUT")},
                    "brier_range": "0 to 1",
                },
            }
        real = populations["FORECAST"]
        return {
            "league": NHL_LEAGUE_CODE,
            "forecast_games": board["counts"]["with_forecast"],
            "finals": board["counts"]["finals"],
            "scored": real["scored"],
            "regulation_3way": real["regulation_3way"],
            "final_2way": real["final_2way"],
            "preview_fixture": populations["PREVIEW_FIXTURE"],
            "synthetic_fixture_rows": sum(1 for g in board["games"] if g["record_class"] == "SYNTHETIC_FIXTURE"),
            "include_test_data": include_test_data,
            "test_data_rows": board["test_data_rows"],
            "markets": board["markets"],
            "wagering": board["wagering"],
            "statement": ("NHL Gen1 forecasts are scored here as forecasts, apart from any paper wager. Regulation "
                          "(3-way) is scored only where a model supplies regulation probabilities; final (2-way, "
                          "including OT and SO) is scored for every forecast. The two are never pooled. Preview and "
                          "synthetic fixtures are never counted as evidence."),
        }


_COUNT_FIELDS = frozenset({"n", "correct_winners", "two_way_n", "regulation_ties", "regulation_not_modelled"})


def _empty_cohort(metrics: dict[str, Any]) -> dict[str, Any]:
    """The same metric shape with nothing in it: counts 0, values None, lists empty."""
    empty: dict[str, Any] = {}
    for key, value in metrics.items():
        if key == "period":
            empty[key] = {"first_start_utc": None, "last_start_utc": None}
        elif isinstance(value, list):
            empty[key] = []
        elif isinstance(value, dict):
            empty[key] = {k: 0 for k in value}
        else:
            empty[key] = 0 if key in _COUNT_FIELDS else None
    return empty


def _auc(points: list[tuple[float, bool]]) -> float | None:
    pos = [p for p, won in points if won]
    neg = [p for p, won in points if not won]
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > q else 0.5 if p == q else 0.0 for p in pos for q in neg)
    return wins / (len(pos) * len(neg))


def wager_results_notice(session: Session | None = None) -> dict[str, Any]:
    """The NHL wagering statement for a reader that wants one line: positions as recorded, or none.

    Wager Results reads NHL's funded positions like every other account's since the
    era opened; this notice is only the one-line summary (no era: no positions).
    """

    if session is not None:
        _markets, wagering = paper_statements(session)
        if wagering.get("enabled"):
            return {"sport": NHL_LEAGUE_CODE, "official_positions": wagering["official_positions"],
                    "paper_positions": wagering["paper_positions"], "wagering_enabled": True,
                    "statement": wagering["statement"]}
    return {
        "sport": NHL_LEAGUE_CODE, "official_positions": 0, "paper_positions": 0, "wagering_enabled": False,
        "statement": "No NHL paper position exists: no NHL paper era is open.",
    }


__all__ = ["MARKETS", "NhlReadService", "OUTCOME_DEFINITIONS", "WAGERING", "current_result", "final_scores",
           "paper_decisions", "paper_statements", "regulation_scores", "resolve_source_game_id",
           "wager_results_notice"]
