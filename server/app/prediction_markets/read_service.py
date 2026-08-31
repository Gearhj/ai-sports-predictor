"""Public, database-only read model for the Prediction Market Observer.

Every method reads persisted rows only — it NEVER calls Polymarket or Kalshi.
It reuses the existing AISP forecast + sportsbook no-vig read model for the
comparison columns and never recomputes sportsbook pricing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import local_day_bounds
from app.models import (
    Game,
    GameTeam,
    IngestionRun,
    PredictionMarketAlert,
    PredictionMarketConsensusSnapshot,
    PredictionMarketContract,
    PredictionMarketEvent,
    PredictionMarketMapping,
    PredictionMarketSnapshot,
    Team,
)
from app.prediction_markets.contracts import CanonicalMarketType, CanonicalSelection
from app.schemas.prediction_market import (
    ContractDetail,
    MappingDetail,
    MarketPulseBoard,
    MarketPulseGameDetail,
    MarketPulseRow,
    MovementEvent,
    OrderBookView,
    PredictionMarketAlertsData,
    PredictionMarketAlertView,
    PredictionMarketHealthSummary,
    ProviderHealth,
    ProviderProbability,
    TimelinePoint,
)

_SPORT_LEAGUES = {"MLB": "MLB", "NFL": "NFL", "NBA": "NBA", "NHL": "NHL"}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class PredictionMarketReadService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- board -----------------------------------------------------------
    def board(self, event_date: date, *, sport: str | None = None) -> MarketPulseBoard:
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        game_ids = list(self.session.scalars(
            select(PredictionMarketSnapshot.canonical_game_id)
            .where(PredictionMarketSnapshot.canonical_game_id.is_not(None))
            .distinct()
        ))
        rows: list[MarketPulseRow] = []
        sports_available: set[str] = set()
        for gid in game_ids:
            game = self.session.get(Game, gid)
            if game is None or not (start <= _aware(game.starts_at) < end):
                continue
            row = self._row(game)
            if row is None:
                continue
            sports_available.add(row.sport)
            if sport and row.sport != sport.upper() and _SPORT_LEAGUES.get(sport.upper()) != row.league:
                continue
            rows.append(row)
        rows.sort(key=lambda r: (r.starts_at or datetime.max.replace(tzinfo=UTC)))
        now = datetime.now(UTC)
        providers = self._provider_health_list()
        observer_enabled = self.settings.prediction_market_observer_enabled
        degraded = [p for p in providers if (p.last_job_status or "").lower() == "failed"]
        if not observer_enabled:
            board_state = "OBSERVER_DISABLED"
            reason = ("The backend is healthy but the Prediction Market Observer is disabled "
                      "(PREDICTION_MARKET_OBSERVER_ENABLED=false); no discovery or snapshots run.")
        elif rows:
            board_state, reason = "ELIGIBLE_MARKETS", f"{len(rows)} mapped game(s) with persisted market snapshots."
        elif degraded:
            board_state = "PROVIDER_DEGRADED"
            reason = f"Provider(s) {', '.join(p.provider for p in degraded)} last audited job failed: {degraded[0].last_error or 'see admin health'}."
        else:
            board_state = "PROVIDERS_HEALTHY_ZERO_MARKETS"
            events = sum(p.events_inspected for p in providers)
            markets = sum(p.markets_inspected for p in providers)
            mapped = sum(p.mapped_markets for p in providers)
            unresolved = sum(p.unresolved_markets for p in providers)
            reason = (
                f"Providers reachable but no semantically comparable per-game market is currently available "
                f"({events} events / {markets} markets inspected; {mapped} mapped, {unresolved} unresolved). "
                "This is a healthy zero-market state — no demo data is substituted."
                if (events or markets) else
                "Observer is armed but no discovery has persisted governed sports markets yet for this date."
            )
        next_discovery = None
        if observer_enabled and providers:
            last = max((p.last_discovery_at for p in providers if p.last_discovery_at), default=None)
            if last is not None:
                next_discovery = _aware(last) + timedelta(minutes=self.settings.prediction_market_discovery_minutes)
        summary = {
            "games": len(rows),
            "mapped": sum(1 for r in rows if r.mapping_status in {"EXACT", "MANUAL_APPROVED"}),
            "review_required": sum(1 for r in rows if r.mapping_status == "REVIEW_REQUIRED"),
            "consensus_available": sum(1 for r in rows if r.consensus_eligible),
            "biggest_disagreement": max((abs(r.diff_aisp_prediction) for r in rows if r.diff_aisp_prediction is not None), default=None),
            "generated_at": now.isoformat(),
        }
        return MarketPulseBoard(
            data_mode="live" if rows else "empty", date=event_date, sport=sport,
            sports_available=sorted(sports_available),
            public_display_enabled=self.settings.prediction_market_public_display_enabled,
            observer_enabled=observer_enabled, board_state=board_state, state_reason=reason,
            providers=providers, next_discovery_at=next_discovery,
            semantics_review_count=self._semantics_review_count(), rows=rows, summary=summary,
        )

    def _row(self, game: Game) -> MarketPulseRow | None:
        home_team, home_abbr, away_team, away_abbr = self._teams(game)
        mapping = self._latest_mapping_for_game(game.id)
        providers = self._provider_probabilities(game.id)
        poly = providers.get("POLYMARKET")
        kalshi = providers.get("KALSHI")
        poly_home = poly.midpoint if poly else None
        kalshi_home = kalshi.midpoint if kalshi else None
        aisp_home, aisp_fresh = self._aisp_home_probability(game)
        book_home = self._sportsbook_home_probability(game)
        consensus = self._latest_consensus(game.id)
        pred_avg = _avg([p for p in (poly_home, kalshi_home) if p is not None])
        row = MarketPulseRow(
            game_id=str(game.id), sport=self._sport(game), league=self._league(game),
            home_team=home_team, away_team=away_team, home_abbrev=home_abbr, away_abbrev=away_abbr,
            starts_at=_aware(game.starts_at), status=game.status.value.upper(),
            aisp_home_probability=aisp_home, aisp_forecast_fresh=aisp_fresh,
            sportsbook_home_probability=book_home, polymarket_home_probability=poly_home,
            kalshi_home_probability=kalshi_home,
            research_consensus_home_probability=(consensus.median_probability if consensus and consensus.eligible else None),
            consensus_eligible=bool(consensus and consensus.eligible),
            consensus_note=(consensus.reason if consensus else "No consensus computed"),
            diff_aisp_sportsbook=_diff(aisp_home, book_home),
            diff_aisp_prediction=_diff(aisp_home, pred_avg),
            diff_polymarket_kalshi=_diff(poly_home, kalshi_home),
            cross_market_dispersion=(round(max(v for v in (poly_home, kalshi_home, book_home) if v is not None) -
                                           min(v for v in (poly_home, kalshi_home, book_home) if v is not None), 4)
                                     if len([v for v in (poly_home, kalshi_home, book_home) if v is not None]) > 1 else None),
            providers=list(providers.values()),
            mapping_status=mapping.mapping_status if mapping else "UNRESOLVED",
            semantics_status=mapping.semantics_status if mapping else "SEMANTICS_UNKNOWN",
            best_quality_tier=self._best_tier(providers.values()),
            alert_count=self._alert_count(game.id),
        )
        self._movement(game.id, row)
        return row

    def _provider_probabilities(self, game_id: Any) -> dict[str, ProviderProbability]:
        """Latest moneyline HOME win probability per provider (P(home))."""
        rows = list(self.session.scalars(
            select(PredictionMarketSnapshot).where(
                PredictionMarketSnapshot.canonical_game_id == game_id,
                PredictionMarketSnapshot.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value,
            ).order_by(PredictionMarketSnapshot.provider_observed_at.desc())
        ))
        out: dict[str, ProviderProbability] = {}
        for snap in rows:
            if snap.provider in out:
                continue
            home_mid = self._to_home(snap.midpoint, snap.selection)
            out[snap.provider] = ProviderProbability(
                provider=snap.provider, midpoint=home_mid,
                microprice=self._to_home(snap.microprice, snap.selection),
                best_bid=snap.best_bid, best_ask=snap.best_ask, last_trade=snap.last_trade,
                bid_ask_spread=snap.bid_ask_spread, volume=snap.volume, open_interest=snap.open_interest,
                top_depth=(snap.top_bid_size or 0) + (snap.top_ask_size or 0) if snap.top_bid_size is not None else None,
                quality_tier=snap.quality_tier,
                quote_age_seconds=(snap.quality_components or {}).get("quote_age"),
                mapping_status=snap.mapping_status, semantics_status=snap.semantics_status,
                in_play=snap.in_play, observed_at=_aware(snap.provider_observed_at),
            )
        return out

    @staticmethod
    def _to_home(prob: float | None, selection: str) -> float | None:
        if prob is None:
            return None
        if selection == CanonicalSelection.HOME.value:
            return round(prob, 6)
        if selection == CanonicalSelection.AWAY.value:
            return round(1.0 - prob, 6)
        return None

    def _movement(self, game_id: Any, row: MarketPulseRow) -> None:
        hist = list(self.session.scalars(
            select(PredictionMarketSnapshot).where(
                PredictionMarketSnapshot.canonical_game_id == game_id,
                PredictionMarketSnapshot.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value,
            ).order_by(PredictionMarketSnapshot.provider_observed_at.desc())
        ))
        if not hist:
            return
        now = _aware(hist[0].provider_observed_at)
        current = self._to_home(hist[0].midpoint, hist[0].selection)
        if current is None:
            return
        def move(minutes: int) -> float | None:
            cutoff = now - timedelta(minutes=minutes)
            past = next((s for s in hist if _aware(s.provider_observed_at) <= cutoff and s.midpoint is not None), None)
            if past is None:
                return None
            base = self._to_home(past.midpoint, past.selection)
            return round(current - base, 4) if base is not None else None
        row.movement_5m = move(5)
        row.movement_30m = move(30)
        row.movement_2h = move(120)
        row.movement_24h = move(24 * 60)

    # -- game detail -----------------------------------------------------
    def game_detail(self, game_id: Any) -> MarketPulseGameDetail | None:
        game = self.session.get(Game, game_id)
        if game is None:
            return None
        home_team, _, away_team, _ = self._teams(game)
        row = self._row(game)
        return MarketPulseGameDetail(
            data_mode="live" if row and row.providers else "empty",
            game_id=str(game.id), home_team=home_team, away_team=away_team,
            starts_at=_aware(game.starts_at), status=game.status.value.upper(),
            timeline=self._timeline(game), order_books=self._order_books(game.id),
            movement_events=self._movement_events(game.id), contracts=self._contract_details(game.id),
            mappings=self._mapping_details(game.id), row=row,
        )

    def _timeline(self, game: Game) -> list[TimelinePoint]:
        snaps = list(self.session.scalars(
            select(PredictionMarketSnapshot).where(
                PredictionMarketSnapshot.canonical_game_id == game.id,
                PredictionMarketSnapshot.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value,
            ).order_by(PredictionMarketSnapshot.provider_observed_at)
        ))
        points: dict[str, TimelinePoint] = {}
        for snap in snaps:
            key = _aware(snap.provider_observed_at).isoformat(timespec="minutes")
            pt = points.setdefault(key, TimelinePoint(time=key))
            home = self._to_home(snap.midpoint, snap.selection)
            if snap.provider == "POLYMARKET":
                pt.polymarket = _pct(home)
            elif snap.provider == "KALSHI":
                pt.kalshi = _pct(home)
        for cons in self.session.scalars(select(PredictionMarketConsensusSnapshot).where(
            PredictionMarketConsensusSnapshot.canonical_game_id == game.id,
            PredictionMarketConsensusSnapshot.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value,
            PredictionMarketConsensusSnapshot.selection == CanonicalSelection.HOME.value,
        ).order_by(PredictionMarketConsensusSnapshot.computed_at)):
            key = _aware(cons.computed_at).isoformat(timespec="minutes")
            points.setdefault(key, TimelinePoint(time=key)).consensus = _pct(cons.median_probability)
        return [points[k] for k in sorted(points)]

    def _order_books(self, game_id: Any) -> list[OrderBookView]:
        out: list[OrderBookView] = []
        for provider, snap in self._provider_snapshots(game_id).items():
            out.append(OrderBookView(
                provider=provider, best_bid=snap.best_bid, best_ask=snap.best_ask, midpoint=snap.midpoint,
                microprice=snap.microprice, spread=snap.bid_ask_spread, observed_at=_aware(snap.provider_observed_at),
                quality_tier=snap.quality_tier,
                bids=self._latest_book(snap, "bids"), asks=self._latest_book(snap, "asks"),
            ))
        return out

    def _latest_book(self, snap: PredictionMarketSnapshot, side: str) -> list[list[float]]:
        from app.models import PredictionMarketOrderbookSnapshot

        frame = self.session.scalar(select(PredictionMarketOrderbookSnapshot).where(
            PredictionMarketOrderbookSnapshot.contract_id == snap.contract_id,
        ).order_by(PredictionMarketOrderbookSnapshot.observed_at.desc()))
        if frame is None:
            return []
        return (frame.bids if side == "bids" else frame.asks) or []

    def _movement_events(self, game_id: Any) -> list[MovementEvent]:
        alerts = list(self.session.scalars(select(PredictionMarketAlert).where(
            PredictionMarketAlert.canonical_game_id == game_id,
        ).order_by(PredictionMarketAlert.observed_at.desc()).limit(50)))
        return [MovementEvent(
            time=_aware(a.observed_at).isoformat(), provider=a.provider or "—",
            market_type=a.market_type or "—", selection=a.selection or "—",
            magnitude=a.magnitude, alert_type=a.alert_type, details=a.details or {},
        ) for a in alerts]

    def _contract_details(self, game_id: Any) -> list[ContractDetail]:
        contracts = self.session.scalars(select(PredictionMarketContract).join(
            PredictionMarketSnapshot, PredictionMarketSnapshot.contract_id == PredictionMarketContract.id,
        ).where(PredictionMarketSnapshot.canonical_game_id == game_id).distinct())
        seen: set[Any] = set()
        out: list[ContractDetail] = []
        for c in contracts:
            if c.id in seen:
                continue
            seen.add(c.id)
            out.append(ContractDetail(
                provider=c.provider, market_type=c.market_type, selection=c.selection, line_value=c.line_value,
                market_question=c.market_question, resolution_rules=c.resolution_rules,
                settlement_source=c.settlement_source, market_close_at=_aware(c.market_close_at),
                semantics_status="SEMANTICS_UNKNOWN", line_source=c.line_source,
            ))
        return out

    def _mapping_details(self, game_id: Any) -> list[MappingDetail]:
        rows = self.session.scalars(select(PredictionMarketMapping).where(
            PredictionMarketMapping.canonical_game_id == game_id,
        ).order_by(PredictionMarketMapping.decided_at.desc()))
        seen: set[tuple[str, str]] = set()
        out: list[MappingDetail] = []
        for m in rows:
            key = (m.provider, m.provider_event_id)
            if key in seen:
                continue
            seen.add(key)
            agreement = ("aligned" if m.start_time_delta_seconds is not None and abs(m.start_time_delta_seconds) <= 5400
                         else "mismatch" if m.start_time_delta_seconds is not None else None)
            out.append(MappingDetail(
                provider=m.provider, provider_event_id=m.provider_event_id, mapping_status=m.mapping_status,
                semantics_status=m.semantics_status, start_time_delta_seconds=m.start_time_delta_seconds,
                game_time_agreement=agreement,
            ))
        return out

    # -- alerts + health -------------------------------------------------
    def alerts(self, event_date: date, *, sport: str | None = None) -> PredictionMarketAlertsData:
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        rows = self.session.scalars(select(PredictionMarketAlert).where(
            PredictionMarketAlert.observed_at >= start, PredictionMarketAlert.observed_at < end,
        ).order_by(PredictionMarketAlert.observed_at.desc()).limit(200))
        alerts = [PredictionMarketAlertView(
            id=str(a.id), alert_type=a.alert_type, game_id=str(a.canonical_game_id) if a.canonical_game_id else None,
            provider=a.provider, market_type=a.market_type, selection=a.selection, magnitude=a.magnitude,
            details=a.details or {}, observed_at=_aware(a.observed_at),
        ) for a in rows]
        return PredictionMarketAlertsData(
            data_mode="live" if alerts else "empty", date=event_date, sport=sport, alerts=alerts,
        )

    def _provider_health(self, provider: str, enabled: bool, mode: str) -> ProviderHealth:
        last_run = self.session.scalar(select(IngestionRun).where(IngestionRun.source == provider)
                                       .order_by(IngestionRun.completed_at.desc().nulls_last()))
        last_snap = self.session.scalar(select(func.max(PredictionMarketSnapshot.aisp_received_at)).where(
            PredictionMarketSnapshot.provider == provider))
        subscribed = self.session.scalar(select(func.count(func.distinct(PredictionMarketContract.provider_market_id)))
                                         .where(PredictionMarketContract.provider == provider)) or 0
        events = self.session.scalar(select(func.count(func.distinct(PredictionMarketEvent.provider_event_id)))
                                     .where(PredictionMarketEvent.provider == provider)) or 0
        mapped = self._provider_mapping_count(provider, {"EXACT", "MANUAL_APPROVED"})
        unresolved = self._provider_mapping_count(provider, {"UNRESOLVED", "AMBIGUOUS", "REVIEW_REQUIRED", "REJECTED"})
        last_status = last_run.status.value if last_run and last_run.status is not None else None
        return ProviderHealth(
            provider=provider, enabled=enabled, connection_mode=mode,
            last_discovery_at=_aware(last_run.completed_at) if last_run else None,
            last_snapshot_at=_aware(last_snap), subscribed_markets=int(subscribed),
            last_error=last_run.error_message if last_run else None,
            status="ACTIVE" if last_snap else ("ARMED" if enabled else "IDLE"),
            events_inspected=int(events), markets_inspected=int(subscribed),
            eligible_game_markets=int(mapped), mapped_markets=int(mapped), unresolved_markets=int(unresolved),
            last_job_status=last_status,
        )

    def _provider_health_list(self) -> list[ProviderHealth]:
        return [
            self._provider_health("POLYMARKET", self.settings.polymarket_enabled,
                                  "STREAMING" if self.settings.polymarket_market_stream_enabled else "REST_ONLY"),
            self._provider_health("KALSHI", self.settings.kalshi_enabled,
                                  "STREAMING" if self.settings.kalshi_market_data_ws_enabled else "REST_ONLY"),
        ]

    def _provider_mapping_count(self, provider: str, statuses: set[str]) -> int:
        rows = self.session.execute(select(
            PredictionMarketMapping.provider_event_id, PredictionMarketMapping.mapping_status,
        ).where(PredictionMarketMapping.provider == provider).order_by(PredictionMarketMapping.decided_at.desc())).all()
        seen: dict[str, str] = {}
        for event_id, status in rows:
            seen.setdefault(event_id, status)
        return sum(1 for v in seen.values() if v in statuses)

    def _semantics_review_count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(PredictionMarketMapping).where(
            PredictionMarketMapping.semantics_status == "SEMANTICS_REVIEW_REQUIRED")) or 0)

    def health_summary(self) -> PredictionMarketHealthSummary:
        stale = self.session.scalar(select(func.count()).select_from(PredictionMarketSnapshot).where(
            PredictionMarketSnapshot.quote_fresh.is_(False))) or 0
        return PredictionMarketHealthSummary(
            data_mode="live",
            observer_enabled=self.settings.prediction_market_observer_enabled,
            public_display_enabled=self.settings.prediction_market_public_display_enabled,
            providers=self._provider_health_list(),
            unresolved_mappings=self._mapping_count("UNRESOLVED"),
            review_required_mappings=self._mapping_count("REVIEW_REQUIRED"),
            semantics_review_count=self._semantics_review_count(),
            stale_markets=int(stale),
            total_contracts=int(self.session.scalar(select(func.count()).select_from(PredictionMarketContract)) or 0),
            total_snapshots=int(self.session.scalar(select(func.count()).select_from(PredictionMarketSnapshot)) or 0),
        )

    # -- helpers ---------------------------------------------------------
    def _provider_snapshots(self, game_id: Any) -> dict[str, PredictionMarketSnapshot]:
        rows = list(self.session.scalars(select(PredictionMarketSnapshot).where(
            PredictionMarketSnapshot.canonical_game_id == game_id,
            PredictionMarketSnapshot.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value,
        ).order_by(PredictionMarketSnapshot.provider_observed_at.desc())))
        out: dict[str, PredictionMarketSnapshot] = {}
        for r in rows:
            out.setdefault(r.provider, r)
        return out

    def _teams(self, game: Game):
        def side(is_home: bool):
            gt = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(is_home)))
            t = self.session.get(Team, gt.team_id) if gt else None
            return (t.name if t else None), (t.abbreviation if t else None)
        (hn, ha), (an, aa) = side(True), side(False)
        return hn, ha, an, aa

    def _aisp_home_probability(self, game: Game) -> tuple[float | None, bool]:
        from app.services.read_service import ReadService

        forecast = ReadService(self.session, self.settings)._latest_forecast(game.id)
        if forecast is None:
            return None, False
        fresh = _aware(forecast.generated_at) is not None and (datetime.now(UTC) - _aware(forecast.generated_at)) <= timedelta(hours=24)
        return round(float(forecast.home_win_probability), 6), fresh

    def _sportsbook_home_probability(self, game: Game) -> float | None:
        from app.services.read_service import ReadService

        gt_home = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(True)))
        gt_away = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(False)))
        if not gt_home or not gt_away:
            return None
        try:
            ctx = ReadService(self.session, self.settings)._market_context(game.id, gt_home.team_id, gt_away.team_id)
        except Exception:
            return None
        prob = ctx.get("home_probability") if isinstance(ctx, dict) else None
        return round(float(prob), 6) if prob is not None else None

    def _latest_consensus(self, game_id: Any) -> PredictionMarketConsensusSnapshot | None:
        return self.session.scalar(select(PredictionMarketConsensusSnapshot).where(
            PredictionMarketConsensusSnapshot.canonical_game_id == game_id,
            PredictionMarketConsensusSnapshot.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value,
            PredictionMarketConsensusSnapshot.selection == CanonicalSelection.HOME.value,
        ).order_by(PredictionMarketConsensusSnapshot.computed_at.desc()))

    def _latest_mapping_for_game(self, game_id: Any) -> PredictionMarketMapping | None:
        return self.session.scalar(select(PredictionMarketMapping).where(
            PredictionMarketMapping.canonical_game_id == game_id,
        ).order_by(PredictionMarketMapping.decided_at.desc()))

    def _alert_count(self, game_id: Any) -> int:
        return int(self.session.scalar(select(func.count()).select_from(PredictionMarketAlert).where(
            PredictionMarketAlert.canonical_game_id == game_id)) or 0)

    def _mapping_count(self, status: str) -> int:
        # count distinct events currently at this status (latest decision)
        rows = self.session.execute(select(
            PredictionMarketMapping.provider, PredictionMarketMapping.provider_event_id,
            PredictionMarketMapping.mapping_status, PredictionMarketMapping.decided_at,
        ).order_by(PredictionMarketMapping.decided_at.desc())).all()
        seen: dict[tuple[str, str], str] = {}
        for provider, event_id, st, _ in rows:
            seen.setdefault((provider, event_id), st)
        return sum(1 for v in seen.values() if v == status)

    def _best_tier(self, providers) -> str | None:
        order = ["ROBUST", "USABLE", "THIN", "STALE", "INCOMPLETE", "SEMANTICS_UNREVIEWED", "UNMAPPED"]
        tiers = [p.quality_tier for p in providers]
        for tier in order:
            if tier in tiers:
                return tier
        return None

    def _sport(self, game: Game) -> str:
        league = self._league(game)
        return {"MLB": "MLB", "NFL": "NFL", "NBA": "NBA", "NHL": "NHL"}.get(league, league)

    def _league(self, game: Game) -> str:
        from app.models import League

        code = self.session.scalar(select(League.code).where(League.id == game.league_id))
        return code or "UNKNOWN"


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _diff(a: float | None, b: float | None) -> float | None:
    return round(a - b, 4) if a is not None and b is not None else None


def _pct(prob: float | None) -> float | None:
    return round(prob * 100, 2) if prob is not None else None


__all__ = ["PredictionMarketReadService"]
