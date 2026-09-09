"""Read model for the NFL product: week overview, game detail, labs, research.

Everything here is read-only and comes from persisted, verified rows:
imported research-shadow forecasts, their own joint score distributions,
registered research supplements, governed odds snapshots and the canonical
schedule. Nothing is executed, refreshed or invented.

RULES THE PAGES DEPEND ON
-------------------------
* One event, one card. The latest VALID snapshot per engine is shown; older
  immutable snapshots stay in storage and are listed as history.
* The runtime clock decides upcoming/started/final. A started game keeps its
  last published pregame snapshot; no snapshot is ever manufactured after
  kickoff.
* Home / away / tie are separate outcomes. The tie is the model's own
  final-score tie, never an overtime probability.
* A market comparison exists only for a fresh, complete, two-sided quote.
  A stale or one-sided quote is shown as what it is, with no edge computed.
* A supplement is attached only when it binds to this event, engine AND
  forecast id. An engine with no joint evidence gets no explorer.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.forecast_exchange.supplements import KIND_DRIVE, KIND_JOINT, KIND_SCENARIO, KIND_STUDY
from app.models import (
    Game,
    GameTeam,
    JobRun,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    ResearchSupplement,
    ShadowForecast,
    ShadowForecastBundle,
    Sportsbook,
    Team,
)
from app.services import nfl_market as arithmetic

SCHEMA_VERSION = "AISP_NFL_PRODUCT_READ_V1"
SPORT = "NFL"
#: The same freshness window the governing MLB policy applies to a quote.
QUOTE_FRESHNESS_MINUTES = 240
CONSUMER_JOB_NAME = "consume-model-exchange-research-artifacts"
LEGACY_BUNDLE_JOB_NAME = "import-nfl-shadow-forecast-bundle"

_TYPE_PRIORITY = {"HISTORICAL": 0, "EARLY": 1, "CURRENT": 2, "FINAL_PREGAME": 3}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat() if value else None


def _rank(forecast: ShadowForecast) -> tuple[float, int, int]:
    stamp = _aware(forecast.available_as_of_utc or forecast.forecast_cutoff_utc)
    return (
        stamp.timestamp() if stamp else 0.0,
        _TYPE_PRIORITY.get(forecast.snapshot_type, -1),
        int(forecast.snapshot_version or 0),
    )


class NflReadService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- forecasts -----------------------------------------------------------
    def _forecast_rows(self, *, season: int | None = None, week: int | None = None,
                       game_id: UUID | None = None) -> list[ShadowForecast]:
        query = (
            select(ShadowForecast)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(ShadowForecastBundle.sport == SPORT)
        )
        if season is not None:
            query = query.where(ShadowForecast.season == season)
        if week is not None:
            query = query.where(ShadowForecast.week == week)
        if game_id is not None:
            query = query.where(ShadowForecast.canonical_game_id == game_id)
        return list(self.session.scalars(query.order_by(ShadowForecast.event_id)))

    @staticmethod
    def _latest_per_engine(rows: list[ShadowForecast]) -> dict[str, ShadowForecast]:
        latest: dict[str, ShadowForecast] = {}
        for row in rows:
            prior = latest.get(row.engine_id)
            if prior is None or _rank(row) > _rank(prior):
                latest[row.engine_id] = row
        return latest

    # -- schedule ------------------------------------------------------------
    def _games(self, game_ids: set[UUID]) -> dict[UUID, Game]:
        if not game_ids:
            return {}
        return {g.id: g for g in self.session.scalars(select(Game).where(Game.id.in_(game_ids)))}

    def _sides(self, game_ids: set[UUID]) -> dict[UUID, dict[str, dict[str, Any]]]:
        if not game_ids:
            return {}
        rows = self.session.execute(
            select(GameTeam.game_id, GameTeam.is_home, Team.id, Team.abbreviation, Team.name)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id.in_(game_ids))
        ).all()
        sides: dict[UUID, dict[str, dict[str, Any]]] = defaultdict(dict)
        for game_id, is_home, team_id, abbreviation, name in rows:
            sides[game_id]["home" if is_home else "away"] = {
                "team_id": str(team_id), "abbreviation": abbreviation, "name": name,
            }
        return sides

    @staticmethod
    def _event_state(game: Game | None, now: datetime) -> str:
        if game is None:
            return "UNSCHEDULED"
        status = str(getattr(game.status, "value", game.status)).upper()
        if status == "FINAL":
            return "FINAL"
        if status in ("POSTPONED", "CANCELED"):
            return status
        starts = _aware(game.starts_at)
        if starts and now >= starts:
            return "STARTED"
        return "UPCOMING"

    # -- supplements ---------------------------------------------------------
    def _supplements(self, event_ids: set[str]) -> dict[str, list[ResearchSupplement]]:
        if not event_ids:
            return {}
        rows = self.session.scalars(
            select(ResearchSupplement)
            .where(ResearchSupplement.sport == SPORT, ResearchSupplement.event_id.in_(event_ids))
            .order_by(ResearchSupplement.imported_at.desc())
        )
        grouped: dict[str, list[ResearchSupplement]] = defaultdict(list)
        for row in rows:
            grouped[row.event_id].append(row)
        return grouped

    @staticmethod
    def _supplement_summary(row: ResearchSupplement) -> dict[str, Any]:
        return {
            "supplement_sha256": row.supplement_sha256,
            "supplement_id": row.supplement_id,
            "kind": row.kind,
            "engine_id": row.engine_id,
            "forecast_id": row.forecast_id,
            "artifact_version": row.artifact_version,
            "simulation_kind": row.simulation_kind,
            "simulation_count": row.simulation_count,
            "simulation_seed": row.simulation_seed,
            "source_cutoff_utc": _iso(row.source_cutoff_utc),
            "published_utc": _iso(row.published_utc),
            "imported_at_utc": _iso(row.imported_at),
        }

    # -- odds ----------------------------------------------------------------
    def _latest_quotes(self, game_id: UUID) -> list[dict[str, Any]]:
        """Latest snapshot per (market type, book, outcome) for one game."""

        rows = self.session.execute(
            select(OddsSnapshot, MarketOutcome, Market, Sportsbook)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
            .where(Market.game_id == game_id)
            .order_by(OddsSnapshot.observed_at.desc())
        ).all()
        latest: dict[tuple[str, UUID, str], dict[str, Any]] = {}
        for snapshot, outcome, market, book in rows:
            market_type = str(getattr(market.market_type, "name", market.market_type))
            if market_type not in ("MONEYLINE", "SPREAD", "TOTAL"):
                key_name = {"h2h": "MONEYLINE", "spreads": "SPREAD", "totals": "TOTAL"}.get(
                    str(getattr(market.market_type, "value", market.market_type)), market_type)
                market_type = key_name
            side = str(outcome.outcome_key).split(":")[0]
            key = (market_type, snapshot.sportsbook_id, str(outcome.outcome_key))
            if key in latest:
                continue
            latest[key] = {
                "market_type": market_type,
                "book": book.name,
                "book_key": book.key,
                "side": side,
                "outcome_key": outcome.outcome_key,
                "point": float(snapshot.point) if snapshot.point is not None else None,
                "american": int(snapshot.american_odds) if snapshot.american_odds is not None else None,
                "decimal": float(snapshot.decimal_odds) if snapshot.decimal_odds is not None else None,
                "observed_at": _iso(snapshot.observed_at),
                "_observed": _aware(snapshot.observed_at),
                "snapshot_id": str(snapshot.id),
            }
        return list(latest.values())

    def _market_comparison(
        self, game_id: UUID | None, cells: list[arithmetic.Cell] | None,
        outcomes: dict[str, float] | None, now: datetime,
    ) -> dict[str, Any]:
        """Model versus the freshest complete two-sided quote per market type."""

        if game_id is None:
            return {"available": False, "reason": "No canonical game is bound to this forecast",
                    "markets": []}
        quotes = self._latest_quotes(game_id)
        if not quotes:
            return {"available": False, "reason": "No governed sportsbook quote has been observed "
                    "for this game", "markets": [], "quotes_observed": 0}

        freshness_floor = now - timedelta(minutes=QUOTE_FRESHNESS_MINUTES)
        markets: list[dict[str, Any]] = []
        for market_type in ("MONEYLINE", "SPREAD", "TOTAL"):
            entries = [q for q in quotes if q["market_type"] == market_type]
            if not entries:
                continue
            markets.append(self._market_block(market_type, entries, cells, outcomes, freshness_floor))
        any_fresh = any(m.get("comparison_available") for m in markets)
        newest = max((q["_observed"] for q in quotes if q["_observed"]), default=None)
        return {
            "available": any_fresh,
            "reason": None if any_fresh else (
                f"Last observed quote is {self._age_text(newest, now)} old, beyond the "
                f"{QUOTE_FRESHNESS_MINUTES}-minute freshness window; comparison unavailable"
            ),
            "freshness_window_minutes": QUOTE_FRESHNESS_MINUTES,
            "last_observed_utc": _iso(newest),
            "quotes_observed": len(quotes),
            "markets": markets,
        }

    @staticmethod
    def _age_text(observed: datetime | None, now: datetime) -> str:
        if observed is None:
            return "an unknown time"
        delta = now - observed
        hours = delta.total_seconds() / 3600.0
        if hours < 48:
            return f"{hours:.0f} hours"
        return f"{delta.days} days"

    def _market_block(self, market_type: str, entries: list[dict[str, Any]],
                      cells: list[arithmetic.Cell] | None, outcomes: dict[str, float] | None,
                      freshness_floor: datetime) -> dict[str, Any]:
        by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            by_book[entry["book"]].append(entry)

        pairs: list[dict[str, Any]] = []
        for book, sides in by_book.items():
            if market_type == "MONEYLINE":
                home = next((s for s in sides if s["side"] == "HOME"), None)
                away = next((s for s in sides if s["side"] == "AWAY"), None)
                if home and away:
                    pairs.append({"book": book, "a": home, "b": away, "line": None})
            else:
                # Pair each line with its opposite on the same book.
                for a in sides:
                    if a["point"] is None:
                        continue
                    opposite_side = {"HOME": "AWAY", "AWAY": "HOME", "OVER": "UNDER", "UNDER": "OVER"}.get(a["side"])
                    if opposite_side is None or a["side"] not in ("HOME", "OVER"):
                        continue
                    expected_point = -a["point"] if market_type == "SPREAD" else a["point"]
                    b = next((s for s in sides if s["side"] == opposite_side
                              and s["point"] is not None and abs(s["point"] - expected_point) < 1e-9), None)
                    if b:
                        pairs.append({"book": book, "a": a, "b": b, "line": a["point"]})
        if not pairs:
            return {"market_type": market_type, "comparison_available": False,
                    "reason": "No complete two-sided quote on one book", "quotes": entries and [
                        self._public_quote(q) for q in entries], "selected": None}

        # Freshest complete pair; ties broken by the better home/over price.
        def pair_time(pair: dict[str, Any]) -> datetime:
            return min(pair["a"]["_observed"], pair["b"]["_observed"])
        selected = max(pairs, key=pair_time)
        observed = pair_time(selected)
        fresh = observed >= freshness_floor
        block: dict[str, Any] = {
            "market_type": market_type,
            "book": selected["book"],
            "line": selected["line"],
            "observed_at": _iso(observed),
            "fresh": fresh,
            "quote": {"a": self._public_quote(selected["a"]), "b": self._public_quote(selected["b"])},
            "books_with_two_sided_quote": len(pairs),
            "comparison_available": False,
            "reason": None,
        }
        if not fresh:
            block["reason"] = "Quote is older than the freshness window; shown for reference only"
            return block
        if cells is None or outcomes is None:
            block["reason"] = "No joint score distribution to compare against"
            return block

        a, b = selected["a"], selected["b"]
        implied_a = arithmetic.implied_probability(a["american"])
        implied_b = arithmetic.implied_probability(b["american"])
        no_vig_a, no_vig_b = arithmetic.no_vig_pair(implied_a, implied_b)
        if market_type == "MONEYLINE":
            tie = outcomes["tie"]
            model_a = arithmetic.conditional_non_tie(outcomes["home"], tie)
            model_b = arithmetic.conditional_non_tie(outcomes["away"], tie)
            block["settlement_rule"] = arithmetic.MONEYLINE_TIE_RULE_VOID
            block["settlement_note"] = (
                "Two-way moneyline: a tie voids the wager, so the comparable model probability "
                "is the conditional non-tie probability. The three-way forecast is unchanged."
            )
            block["three_way"] = {"home": outcomes["home"], "away": outcomes["away"], "tie": tie}
            push = 0.0
        elif market_type == "SPREAD":
            query = arithmetic.spread_query(cells, home_line=float(a["point"]))
            model_a, model_b, push = query["home_cover"], query["away_cover"], query["push"]
            block["settlement_note"] = ("Push mass is stated separately and refunded; "
                                        "final scores include overtime as the model simulates them.")
        else:
            query = arithmetic.total_query(cells, line=float(a["point"]))
            model_a, model_b, push = query["over"], query["under"], query["push"]
            block["settlement_note"] = "Push mass is stated separately and refunded."
        block.update({
            "comparison_available": True,
            "model": {"a": model_a, "b": model_b, "push": push},
            "market_implied": {"a": implied_a, "b": implied_b},
            "market_no_vig": {"a": no_vig_a, "b": no_vig_b},
            "edge": {"a": arithmetic.edge(model_a, no_vig_a), "b": arithmetic.edge(model_b, no_vig_b)},
            "expected_value": {
                "a": arithmetic.expected_value(model_a or 0.0, a["decimal"], push=push) if a["decimal"] else None,
                "b": arithmetic.expected_value(model_b or 0.0, b["decimal"], push=push) if b["decimal"] else None,
            },
            "authority": "RESEARCH_SHADOW — an edge shown here grants no wagering authority",
        })
        return block

    @staticmethod
    def _public_quote(q: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in q.items() if not k.startswith("_")}

    # -- engine view ---------------------------------------------------------
    def _engine_summary(self, forecast: ShadowForecast) -> dict[str, Any]:
        dist = forecast.distributions or {}
        detail = forecast.readiness_detail or {}
        missing = list(detail.get("missing_inputs") or [])
        lineage = forecast.lineage_detail or {}
        tie = dist.get("tie_probability")
        return {
            "engine_id": forecast.engine_id,
            "engine_version": forecast.engine_version,
            "model_label": self._model_label(forecast.engine_id),
            "forecast_id": forecast.forecast_id,
            "snapshot_type": forecast.snapshot_type,
            "snapshot_version": forecast.snapshot_version,
            "status": forecast.status,
            "home_win_probability": forecast.full_home_win_probability,
            "away_win_probability": forecast.full_away_win_probability,
            "tie_probability": tie,
            "tie_note": ("The model's own final-score tie. It does not simulate overtime, so this "
                         "is not an overtime probability."),
            "expected_home_score": forecast.full_expected_home_score,
            "expected_away_score": forecast.full_expected_away_score,
            "expected_margin_home": round(forecast.full_expected_home_score - forecast.full_expected_away_score, 4),
            "expected_total_score": round(forecast.full_expected_home_score + forecast.full_expected_away_score, 4),
            "evidence_readiness": {
                "score": forecast.readiness_score,
                "threshold": forecast.readiness_threshold,
                "label": f"Evidence readiness {forecast.readiness_score:.0f} of 100 (publishes at {forecast.readiness_threshold:.0f})",
                "missing_input_count": len(missing),
                "explanation": (
                    f"{len(missing)} season-to-date inputs are not yet observable this early in the season; "
                    "the model applies its frozen preseason missing-value treatment. The published "
                    "readiness score counts available inputs; it is not a confidence in the forecast."
                ) if missing else "All registered inputs were observable.",
            },
            "evidence_basis": {
                "model_inputs": "197 registered pregame inputs from the frozen feature contract",
                "not_consumed": ["quarterback status", "injury report", "weather", "roster or depth-chart changes", "market prices"],
                "note": ("A newer snapshot version reflects a producer re-publication, not refreshed "
                         "evidence for inputs this model does not consume."),
                "training_cutoff": lineage.get("training_cutoff"),
                "feature_tier": lineage.get("feature_tier"),
                "promotion_status": lineage.get("promotion_status"),
                "execution_status": lineage.get("execution_status"),
            },
            "timestamps": {
                "source_evidence_utc": _iso(forecast.source_cutoff_utc),
                "forecast_cutoff_utc": _iso(forecast.forecast_cutoff_utc),
                "published_utc": _iso(forecast.available_as_of_utc),
                "imported_at_utc": _iso(forecast.created_at),
            },
            "simulation": {
                "simulator_id": forecast.simulator_id,
                "simulator_version": forecast.simulator_version,
                "seed": forecast.simulator_seed,
                "kind": "MONTE_CARLO" if lineage.get("simulation_count") else "UNKNOWN",
                "count": lineage.get("completed_simulations") or lineage.get("simulation_count"),
                "joint_available": bool(dist.get("joint_score_distribution")),
            },
            "uncertainty": forecast.uncertainty or {},
            "model_artifact_sha256": forecast.model_artifact_sha256,
            "model_manifest_sha256": forecast.model_manifest_sha256,
        }

    @staticmethod
    def _model_label(engine_id: str) -> str:
        return {"NFL_M1_XGBOOST": "M1 long-history XGBoost (research shadow)"}.get(engine_id, engine_id)

    # -- public: weeks -------------------------------------------------------
    def weeks(self) -> dict[str, Any]:
        rows = self.session.execute(
            select(ShadowForecast.season, ShadowForecast.week,
                   func.count(func.distinct(ShadowForecast.event_id)),
                   func.min(ShadowForecast.scheduled_start_utc), func.max(ShadowForecast.scheduled_start_utc))
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(ShadowForecastBundle.sport == SPORT, ShadowForecast.week.is_not(None))
            .group_by(ShadowForecast.season, ShadowForecast.week)
            .order_by(ShadowForecast.season, ShadowForecast.week)
        ).all()
        now = datetime.now(UTC)
        weeks = [{
            "season": int(season), "week": int(week), "events": int(count),
            "first_kickoff_utc": _iso(first), "last_kickoff_utc": _iso(last),
        } for season, week, count, first, last in rows]
        current = self._current_week(weeks, now)
        return {"schema_version": SCHEMA_VERSION, "sport": SPORT, "now_utc": now.isoformat(),
                "data_mode": "live", "weeks": weeks, "current": current}

    @staticmethod
    def _current_week(weeks: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
        # The first week whose last kickoff has not passed, else the last week.
        for week in weeks:
            last = datetime.fromisoformat(week["last_kickoff_utc"]) if week["last_kickoff_utc"] else None
            if last is None or last + timedelta(hours=4) >= now:
                return {"season": week["season"], "week": week["week"]}
        return {"season": weeks[-1]["season"], "week": weeks[-1]["week"]} if weeks else None

    # -- public: week overview -----------------------------------------------
    def week_overview(self, season: int, week: int) -> dict[str, Any]:
        now = datetime.now(UTC)
        rows = self._forecast_rows(season=season, week=week)
        by_event: dict[str, list[ShadowForecast]] = defaultdict(list)
        for row in rows:
            by_event[row.event_id].append(row)
        game_ids = {r.canonical_game_id for r in rows if r.canonical_game_id}
        games = self._games(game_ids)
        sides = self._sides(game_ids)
        supplements = self._supplements(set(by_event))

        events: list[dict[str, Any]] = []
        for event_id, event_rows in by_event.items():
            latest = self._latest_per_engine(event_rows)
            anchor = next(iter(latest.values()))
            game = games.get(anchor.canonical_game_id) if anchor.canonical_game_id else None
            side = sides.get(anchor.canonical_game_id, {}) if anchor.canonical_game_id else {}
            identity = anchor.identity_evidence or {}
            home = side.get("home") or {"team_id": anchor.home_team_id, "abbreviation": anchor.home_team_id,
                                        "name": (identity.get("home_team") or {}).get("display_name", anchor.home_team_id)}
            away = side.get("away") or {"team_id": anchor.away_team_id, "abbreviation": anchor.away_team_id,
                                        "name": (identity.get("away_team") or {}).get("display_name", anchor.away_team_id)}
            engines = [self._engine_summary(f) for f in sorted(latest.values(), key=lambda f: f.engine_id)]
            event_supplements = supplements.get(event_id, [])
            events.append({
                "event_id": event_id,
                "canonical_game_id": str(anchor.canonical_game_id) if anchor.canonical_game_id else None,
                "season": anchor.season, "week": anchor.week,
                "kickoff_utc": _iso(anchor.scheduled_start_utc),
                "state": self._event_state(game, now),
                "final_score": ({"home": game.home_score, "away": game.away_score}
                                if game is not None and self._event_state(game, now) == "FINAL" else None),
                "home": home, "away": away,
                "engines": engines,
                "snapshot_history_count": len(event_rows),
                "supplements": {
                    "joint_engines": sorted({s.engine_id for s in event_supplements if s.kind == KIND_JOINT and s.engine_id}),
                    "drive_engines": sorted({s.engine_id for s in event_supplements if s.kind == KIND_DRIVE and s.engine_id}),
                    "scenario_engines": sorted({s.engine_id for s in event_supplements if s.kind == KIND_SCENARIO and s.engine_id}),
                },
                "lab_available": any(e["simulation"]["joint_available"] for e in engines)
                                 or any(s.kind == KIND_JOINT for s in event_supplements),
                "analysis_href": f"/nfl/games/{anchor.canonical_game_id}" if anchor.canonical_game_id else None,
                "lab_href": f"/nfl/games/{anchor.canonical_game_id}/lab" if anchor.canonical_game_id else None,
            })
        events.sort(key=lambda e: (e["kickoff_utc"] or "", e["event_id"]))
        return {
            "schema_version": SCHEMA_VERSION, "sport": SPORT, "season": season, "week": week,
            "data_mode": "live", "now_utc": now.isoformat(), "generated_at_utc": now.isoformat(),
            "authority": "RESEARCH_SHADOW — not executable, no wagering authority",
            "events": events,
            "counts": {
                "events": len(events),
                "upcoming": sum(e["state"] == "UPCOMING" for e in events),
                "started": sum(e["state"] == "STARTED" for e in events),
                "final": sum(e["state"] == "FINAL" for e in events),
                "with_joint_evidence": sum(e["lab_available"] for e in events),
            },
            "consumer": self.consumer_status(),
        }

    # -- public: game detail -------------------------------------------------
    def game_detail(self, game_id: UUID) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        rows = self._forecast_rows(game_id=game_id)
        if not rows:
            return None
        game = self._games({game_id}).get(game_id)
        side = self._sides({game_id}).get(game_id, {})
        latest = self._latest_per_engine(rows)
        anchor = next(iter(latest.values()))
        event_id = anchor.event_id
        event_supplements = self._supplements({event_id}).get(event_id, [])

        engines: list[dict[str, Any]] = []
        for engine_id, forecast in sorted(latest.items()):
            summary = self._engine_summary(forecast)
            dist = forecast.distributions or {}
            joint_rows = dist.get("joint_score_distribution") or []
            cells = arithmetic.cells_from_distribution(joint_rows) if joint_rows else None
            outcomes = arithmetic.outcome_probabilities(cells) if cells else None
            summary["explorer"] = self._explorer(forecast, joint_rows, cells, outcomes) if cells else None
            summary["market_comparison"] = self._market_comparison(game_id, cells, outcomes, now)
            summary["history"] = [{
                "forecast_id": r.forecast_id, "snapshot_type": r.snapshot_type,
                "snapshot_version": r.snapshot_version, "published_utc": _iso(r.available_as_of_utc),
                "imported_at_utc": _iso(r.created_at),
                "home_win_probability": r.full_home_win_probability,
                "away_win_probability": r.full_away_win_probability,
            } for r in sorted((x for x in rows if x.engine_id == engine_id), key=_rank)]
            engines.append(summary)

        # Supplement-backed research candidates, kept apart from M1.
        candidates: list[dict[str, Any]] = []
        for row in event_supplements:
            if row.kind != KIND_JOINT:
                continue
            summaries = (row.payload or {}).get("summaries") or {}
            joint_rows = summaries.get("jointScoreDistribution") or []
            cells = arithmetic.cells_from_distribution(joint_rows)
            outcomes = arithmetic.outcome_probabilities(cells)
            candidates.append({
                **self._supplement_summary(row),
                "model_label": summaries.get("modelLabel") or row.engine_id,
                "separately_calibrated_win_probabilities": summaries.get("calibratedWinProbabilities"),
                "explorer": self._explorer_from_cells(joint_rows, cells, outcomes, {
                    "kind": row.simulation_kind, "count": row.simulation_count, "seed": row.simulation_seed,
                }),
                "market_comparison": self._market_comparison(game_id, cells, outcomes, now),
            })

        drives = [{**self._supplement_summary(r), "summaries": (r.payload or {}).get("summaries"),
                   "paths": (r.payload or {}).get("paths")}
                  for r in event_supplements if r.kind == KIND_DRIVE]
        scenarios = [{**self._supplement_summary(r), "summaries": (r.payload or {}).get("summaries"),
                      "scenarios": (r.payload or {}).get("scenarios")}
                     for r in event_supplements if r.kind == KIND_SCENARIO]

        return {
            "schema_version": SCHEMA_VERSION, "sport": SPORT, "now_utc": now.isoformat(),
            "data_mode": "live",
            "authority": "RESEARCH_SHADOW — not executable, no wagering authority",
            "event": {
                "event_id": event_id, "canonical_game_id": str(game_id),
                "season": anchor.season, "week": anchor.week,
                "kickoff_utc": _iso(anchor.scheduled_start_utc),
                "state": self._event_state(game, now),
                "final_score": ({"home": game.home_score, "away": game.away_score}
                                if game is not None and self._event_state(game, now) == "FINAL" else None),
                "home": side.get("home"), "away": side.get("away"),
            },
            "engines": engines,
            "research_candidates": candidates,
            "drive_theater": {
                "available": bool(drives),
                "supplements": drives,
                "requirement": None if drives else (
                    "Awaiting a DRIVE_SIMULATION supplement from the producer: persisted possessions "
                    "with period, clock, offense, start/end yard line, result and running score, "
                    "reconciling to its own final score."
                ),
            },
            "scenario_comparison": {
                "available": bool(scenarios),
                "supplements": scenarios,
                "requirement": None if scenarios else (
                    "Awaiting a SCENARIO_COMPARISON supplement: precomputed alternate assumptions for "
                    "inputs the engine actually consumes. M1 consumes no quarterback, injury or "
                    "weather input, so those controls stay disabled."
                ),
            },
            "research_results": self.research_results(),
        }

    def _explorer(self, forecast: ShadowForecast, joint_rows: list[dict[str, Any]],
                  cells: list[arithmetic.Cell], outcomes: dict[str, float]) -> dict[str, Any]:
        lineage = forecast.lineage_detail or {}
        dist = forecast.distributions or {}
        payload = self._explorer_from_cells(joint_rows, cells, outcomes, {
            "kind": "MONTE_CARLO", "count": lineage.get("completed_simulations") or lineage.get("simulation_count"),
            "seed": forecast.simulator_seed,
        })
        payload["producer_prediction_interval"] = dist.get("prediction_interval")
        payload["producer_most_likely_scores"] = dist.get("most_likely_scores")
        return payload

    @staticmethod
    def _explorer_from_cells(joint_rows: list[dict[str, Any]], cells: list[arithmetic.Cell],
                             outcomes: dict[str, float], simulation: dict[str, Any]) -> dict[str, Any]:
        margin: dict[int, float] = defaultdict(float)
        total: dict[int, float] = defaultdict(float)
        exp_home = exp_away = 0.0
        for cell in cells:
            margin[cell.home - cell.away] += cell.probability
            total[cell.home + cell.away] += cell.probability
            exp_home += cell.probability * cell.home
            exp_away += cell.probability * cell.away
        likely = sorted(cells, key=lambda c: -c.probability)[:8]
        return {
            "simulation": simulation,
            "joint_score_distribution": [
                {"home": c.home, "away": c.away, "probability": c.probability} for c in cells
            ],
            "outcomes": outcomes,
            "expected": {"home": exp_home, "away": exp_away, "margin_home": exp_home - exp_away,
                         "total": exp_home + exp_away},
            "margin_distribution": [{"margin": k, "probability": v} for k, v in sorted(margin.items())],
            "total_distribution": [{"total": k, "probability": v} for k, v in sorted(total.items())],
            "quantiles": {
                "margin": _quantiles(margin), "total": _quantiles(total),
                "home": _quantiles(_marginal(cells, "home")), "away": _quantiles(_marginal(cells, "away")),
            },
            "most_likely_scores": [
                {"home": c.home, "away": c.away, "probability": c.probability} for c in likely
            ],
            "cells": len(cells),
        }

    # -- public: research results -------------------------------------------
    def research_results(self) -> dict[str, Any]:
        rows = list(self.session.scalars(
            select(ResearchSupplement)
            .where(ResearchSupplement.sport == SPORT, ResearchSupplement.kind == KIND_STUDY)
            .order_by(ResearchSupplement.imported_at.desc())
        ))
        return {
            "available": bool(rows),
            "studies": [{**self._supplement_summary(r), "contract_version": r.contract_version,
                         "summaries": (r.payload or {}).get("summaries"),
                         "producer": (r.payload or {}).get("producer")} for r in rows],
            "requirement": None if rows else "No machine-readable model benchmark study has been imported.",
        }

    # -- public: consumer status --------------------------------------------
    def consumer_status(self) -> dict[str, Any]:
        def latest(job_name: str, status: str | None = None) -> JobRun | None:
            query = select(JobRun).where(JobRun.job_name == job_name)
            if status:
                query = query.where(JobRun.status == status)
            return self.session.scalar(query.order_by(JobRun.started_at.desc()).limit(1))

        success = latest(CONSUMER_JOB_NAME, "SUCCEEDED")
        failed = latest(CONSUMER_JOB_NAME, "FAILED")
        any_run = latest(CONSUMER_JOB_NAME)
        legacy = latest(LEGACY_BUNDLE_JOB_NAME, "SUCCEEDED")
        bundle_count = self.session.scalar(
            select(func.count()).select_from(ShadowForecastBundle).where(ShadowForecastBundle.sport == SPORT)
        )
        supplement_count = self.session.scalar(
            select(func.count()).select_from(ResearchSupplement).where(ResearchSupplement.sport == SPORT)
        )
        last_bundle = self.session.scalar(
            select(func.max(ShadowForecastBundle.imported_at)).where(ShadowForecastBundle.sport == SPORT)
        )
        last_supplement = self.session.scalar(
            select(func.max(ResearchSupplement.imported_at)).where(ResearchSupplement.sport == SPORT)
        )
        return {
            "auto_import_enabled": bool(self.settings.model_exchange_auto_import_enabled),
            "job_name": CONSUMER_JOB_NAME,
            "last_success_utc": _iso(success.completed_at or success.started_at) if success else None,
            "last_success_result": success.result if success else None,
            "last_run_utc": _iso(any_run.started_at) if any_run else None,
            "last_run_status": any_run.status if any_run else None,
            "last_failure_utc": _iso(failed.started_at) if failed else None,
            "last_failure_message": (failed.error_message or "")[:300] if failed else None,
            "legacy_bundle_job": {
                "job_name": LEGACY_BUNDLE_JOB_NAME,
                "last_success_utc": _iso(legacy.completed_at or legacy.started_at) if legacy else None,
            },
            "registered_bundles": int(bundle_count or 0),
            "registered_supplements": int(supplement_count or 0),
            "last_bundle_import_utc": _iso(last_bundle),
            "last_supplement_import_utc": _iso(last_supplement),
        }


def _marginal(cells: list[arithmetic.Cell], side: str) -> dict[int, float]:
    out: dict[int, float] = defaultdict(float)
    for cell in cells:
        out[cell.home if side == "home" else cell.away] += cell.probability
    return out


def _quantiles(mass: dict[int, float]) -> dict[str, int | None]:
    if not mass:
        return {"p05": None, "p25": None, "p50": None, "p75": None, "p95": None}
    ordered = sorted(mass.items())
    total = sum(v for _, v in ordered) or 1.0
    targets = {"p05": 0.05, "p25": 0.25, "p50": 0.5, "p75": 0.75, "p95": 0.95}
    result: dict[str, int | None] = {}
    for name, target in targets.items():
        acc = 0.0
        value: int | None = ordered[-1][0]
        for k, v in ordered:
            acc += v / total
            if acc >= target:
                value = k
                break
        result[name] = value
    return result


__all__ = ["NflReadService", "QUOTE_FRESHNESS_MINUTES", "SCHEMA_VERSION", "CONSUMER_JOB_NAME"]
