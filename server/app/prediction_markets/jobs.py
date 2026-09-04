"""Prediction Market Observer jobs — the ONLY place provider I/O happens.

Each job is gated by settings (observer + provider flags, all off by default),
bounded by max-active-markets and a per-run request budget, records an
IngestionRun, and persists append-only evidence through the observer. No public
read path ever reaches this module.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.data_sources.base import SourceError
from app.models import (
    Game,
    IngestionRun,
    PredictionMarketContract,
    PredictionMarketMapping,
    RunStatus,
)
from app.prediction_markets.contracts import CanonicalMarketType, ProviderContractDTO, ProviderQuoteDTO
from app.prediction_markets.kalshi import KalshiPredictionMarketSource
from app.prediction_markets.polymarket import PolymarketPredictionMarketSource
from app.prediction_markets.service import PredictionMarketObserver

# Sport -> (polymarket league label, kalshi sports category hint)
_SPORT_LEAGUE = {"MLB": "MLB", "NFL": "NFL", "NBA": "NBA", "NHL": "NHL"}


class PredictionMarketJobs:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.observer = PredictionMarketObserver(session, settings)

    def _record_run(self, source: str, job: str, *, status, count: int, error: str | None = None, meta: dict | None = None) -> IngestionRun:
        run = IngestionRun(
            source=source, job_name=job, status=status, started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC), records_processed=count, error_message=error,
            source_metadata=meta or {},
        )
        self.session.add(run)
        self.session.commit()
        return run

    # -- discovery -------------------------------------------------------
    async def discover(self, event_date: date) -> dict[str, Any]:
        if not self.settings.prediction_market_observer_enabled:
            return {"records": 0, "status": "skipped", "reason": "observer disabled"}
        totals = {"contracts": 0, "events": 0, "mappings": 0}
        per_provider: dict[str, Any] = {}
        for sport in self.settings.prediction_market_sport_list:
            league = _SPORT_LEAGUE.get(sport, sport)
            if self.settings.polymarket_enabled:
                per_provider[f"POLYMARKET/{sport}"] = await self._discover_polymarket(league)
                _accumulate(totals, per_provider[f"POLYMARKET/{sport}"])
            if self.settings.kalshi_enabled and self.settings.kalshi_rest_polling_enabled:
                per_provider[f"KALSHI/{sport}"] = await self._discover_kalshi(league, sport)
                _accumulate(totals, per_provider[f"KALSHI/{sport}"])
        return {"records": totals["contracts"], **totals, "providers": per_provider}

    async def _discover_polymarket(self, league: str) -> dict[str, Any]:
        source = PolymarketPredictionMarketSource(
            gamma_base_url=self.settings.polymarket_gamma_base_url,
            clob_base_url=self.settings.polymarket_clob_base_url,
        )
        try:
            resp = await source.discover_contracts(league=league, limit=min(self.settings.prediction_market_max_active_markets, 200))
        except SourceError as exc:
            self._record_run("POLYMARKET", "discover-prediction-markets", status=RunStatus.FAILED, count=0, error=str(exc))
            return {"error": str(exc), "contracts": 0}
        contracts = [ProviderContractDTO.model_validate(item) for item in resp.envelope.data]
        meta = dict(resp.envelope.metadata or {})
        run = self._record_run("POLYMARKET", "discover-prediction-markets", status=RunStatus.SUCCEEDED,
                                count=len(contracts), meta=meta)
        result = self.observer.ingest_contracts(contracts, ingestion_run_id=run.id)
        return {**result, "provider": "POLYMARKET",
                "markets_seen": int(meta.get("markets_seen") or 0),
                "excluded": meta.get("excluded") or {}}

    async def _discover_kalshi(self, league: str, sport: str) -> dict[str, Any]:
        source = KalshiPredictionMarketSource(
            rest_base_url=self.settings.kalshi_rest_base_url,
            api_key_id=self.settings.kalshi_api_key_id, private_key_path=self.settings.kalshi_private_key_path,
            ws_enabled=self.settings.kalshi_market_data_ws_enabled,
        )
        # Kalshi requires a series ticker; find sports series by category, bounded.
        try:
            series_resp = await source.list_series(category="Sports")
        except SourceError as exc:
            self._record_run("KALSHI", "discover-prediction-markets", status=RunStatus.FAILED, count=0, error=str(exc))
            return {"error": str(exc), "contracts": 0}
        series = series_resp.envelope.data or []
        tickers = [s.get("ticker") for s in series if isinstance(s, dict) and self._series_matches(s, sport)][:5]
        contracts: list[ProviderContractDTO] = []
        markets_seen = 0
        excluded: dict[str, int] = {}
        queries: list[dict[str, Any]] = []
        errors: list[str] = []
        for ticker in tickers:
            try:
                resp = await source.discover_contracts(series_ticker=ticker, league=league, sport=sport, limit=50)
                contracts.extend(ProviderContractDTO.model_validate(item) for item in resp.envelope.data)
                meta = resp.envelope.metadata or {}
                markets_seen += int(meta.get("markets_seen") or 0)
                for reason, n in (meta.get("excluded") or {}).items():
                    excluded[reason] = excluded.get(reason, 0) + int(n)
                queries.append(meta.get("query") or {"series_ticker": ticker})
            except SourceError as exc:
                errors.append(f"{ticker}: {exc}")
                continue
        run = self._record_run(
            "KALSHI", "discover-prediction-markets", status=RunStatus.SUCCEEDED,
            count=len(contracts),
            meta={
                "series_scanned": len(tickers), "series_seen": len(series), "mode": source.connection_mode,
                "markets_seen": markets_seen, "contracts": len(contracts), "league": league,
                "excluded": excluded, "query": {"series": tickers, "requests": queries},
                "series_errors": errors,
            },
        )
        result = self.observer.ingest_contracts(contracts, ingestion_run_id=run.id)
        return {**result, "provider": "KALSHI", "mode": source.connection_mode,
                "markets_seen": markets_seen, "excluded": excluded}

    @staticmethod
    def _series_matches(series: dict[str, Any], sport: str) -> bool:
        blob = f"{series.get('ticker','')} {series.get('title','')} {series.get('category','')}".lower()
        keys = {"MLB": ("mlb", "baseball"), "NFL": ("nfl", "football"), "NBA": ("nba", "basketball"), "NHL": ("nhl", "hockey")}
        return any(k in blob for k in keys.get(sport, ()))

    # -- snapshot refresh ------------------------------------------------
    async def refresh(self, event_date: date) -> dict[str, Any]:
        if not self.settings.prediction_market_observer_enabled:
            return {"records": 0, "status": "skipped", "reason": "observer disabled"}
        contracts = self._active_contracts(event_date)
        budget = self.settings.prediction_market_max_requests_per_hour
        snapshots = errors = 0
        used = 0
        persist_errors: list[str] = []
        # Providers that cannot possibly answer are skipped with the reason
        # recorded, instead of burning the hourly budget on guaranteed errors.
        skipped: dict[str, str] = {}
        if self.settings.kalshi_enabled and not self.settings.kalshi_api_key_id:
            skipped["KALSHI"] = "order books require API credentials; none configured"
        for contract in contracts:
            if used >= budget:
                break
            if contract.provider in skipped:
                continue
            try:
                quote = await self._fetch_quote(contract)
                used += 1
                if quote is not None and self.observer.record_snapshot(contract=contract, quote=quote) is not None:
                    snapshots += 1
            except SourceError:
                errors += 1
            except Exception as exc:  # one bad row must not abort the audited run
                self.session.rollback()
                errors += 1
                if len(persist_errors) < 5:
                    persist_errors.append(f"{contract.provider}:{contract.provider_market_id}: {type(exc).__name__}: {exc}"[:300])
        self._record_run("PREDICTION_MARKET", "refresh-prediction-market-snapshots", status=RunStatus.SUCCEEDED,
                         count=snapshots, meta={"contracts": len(contracts), "requests": used, "errors": errors,
                                                "persist_errors": persist_errors, "skipped": skipped})
        return {"records": snapshots, "contracts": len(contracts), "requests": used, "errors": errors,
                "skipped": skipped}

    async def _fetch_quote(self, contract: PredictionMarketContract) -> ProviderQuoteDTO | None:
        if contract.provider == "POLYMARKET":
            source = PolymarketPredictionMarketSource(
                gamma_base_url=self.settings.polymarket_gamma_base_url, clob_base_url=self.settings.polymarket_clob_base_url)
            resp = await source.get_order_book(contract.provider_outcome_id or contract.provider_market_id)
        elif contract.provider == "KALSHI":
            source = KalshiPredictionMarketSource(
                rest_base_url=self.settings.kalshi_rest_base_url, api_key_id=self.settings.kalshi_api_key_id,
                private_key_path=self.settings.kalshi_private_key_path, ws_enabled=self.settings.kalshi_market_data_ws_enabled)
            resp = await source.get_order_book(contract.provider_market_id)
        else:
            return None
        data = resp.envelope.data
        return ProviderQuoteDTO.model_validate(data[0]) if data else None

    def _active_contracts(self, event_date: date | None = None) -> list[PredictionMarketContract]:
        """Contracts to quote this cycle, within the active-market cap.

        Ordering is by evidence value, not alphabet: contracts whose provider
        event is EXACT/MANUAL_APPROVED-mapped to a canonical game on the
        operational date come first, then other mapped contracts, then the
        unmapped remainder; providers are interleaved so one provider's
        listing volume cannot starve the other. (The previous alphabetical
        `provider_market_id` order let unrelated markets fill the cap and left
        mapped slate games permanently unquoted.)
        """
        rows = list(self.session.scalars(select(PredictionMarketContract).order_by(
            PredictionMarketContract.provider_market_id, PredictionMarketContract.version.desc())))
        seen: set[tuple[str, str, Any]] = set()
        latest: list[PredictionMarketContract] = []
        for r in rows:
            key = (r.provider, r.provider_market_id, r.provider_outcome_id)
            if key in seen:
                continue
            seen.add(key)
            if (r.market_status or "").lower() not in {"closed", "settled", "inactive"}:
                latest.append(r)
        slate_events, mapped_events = self._mapped_event_priority(event_date)

        def priority(c: PredictionMarketContract) -> int:
            key = (c.provider, c.provider_event_id)
            # Within each mapping tier the full-game moneyline (the board's
            # comparison market) precedes spread/total contracts.
            moneyline = 0 if c.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value else 1
            if key in slate_events:
                return 0 + moneyline
            if key in mapped_events:
                return 2 + moneyline
            return 4 + moneyline

        buckets: dict[tuple[int, str], list[PredictionMarketContract]] = {}
        for c in latest:
            buckets.setdefault((priority(c), c.provider), []).append(c)
        ordered: list[PredictionMarketContract] = []
        for level in range(6):
            queues = [buckets[k] for k in sorted(buckets) if k[0] == level]
            while any(queues):
                for q in queues:
                    if q:
                        ordered.append(q.pop(0))
        return ordered[: self.settings.prediction_market_max_active_markets]

    def _mapped_event_priority(self, event_date: date | None) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
        """(events mapped to games on the operational date, all mapped events)."""
        from app.core.time import local_day_bounds

        rows = self.session.execute(select(
            PredictionMarketMapping.provider, PredictionMarketMapping.provider_event_id,
            PredictionMarketMapping.canonical_game_id, PredictionMarketMapping.mapping_status,
            PredictionMarketMapping.decided_at,
        ).order_by(PredictionMarketMapping.decided_at.desc())).all()
        latest: dict[tuple[str, str], tuple[Any, str]] = {}
        for provider, event_id, gid, status, _ in rows:
            latest.setdefault((provider, event_id), (gid, status))
        mapped = {k for k, (gid, st) in latest.items() if gid is not None and st in {"EXACT", "MANUAL_APPROVED"}}
        if not mapped or event_date is None:
            return set(), mapped
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        slate_ids = set(self.session.scalars(select(Game.id).where(Game.starts_at >= start, Game.starts_at < end)))
        slate = {k for k in mapped if latest[k][0] in slate_ids}
        return slate, mapped

    # -- consensus / alerts / finalize / backfill ------------------------
    async def compute_consensus(self, event_date: date) -> dict[str, Any]:
        written = 0
        for gid in self._mapped_game_ids():
            written += self.observer.compute_consensus(gid)
        return {"records": written}

    async def evaluate_alerts(self, event_date: date) -> dict[str, Any]:
        written = 0
        for gid in self._mapped_game_ids():
            written += self.observer.evaluate_alerts(gid)
        return {"records": written}

    async def finalize_pregame(self, event_date: date) -> dict[str, Any]:
        """Capture a final pregame snapshot for games about to start, then consensus."""
        result = await self.refresh(event_date)
        consensus = await self.compute_consensus(event_date)
        return {"records": result["records"], "final_snapshots": result["records"], "consensus": consensus["records"]}

    async def backfill(self, event_date: date, *, days: int = 1) -> dict[str, Any]:
        """Bounded historical backfill via official history endpoints only."""
        if not (self.settings.prediction_market_observer_enabled and self.settings.polymarket_enabled):
            return {"records": 0, "status": "skipped"}
        points = 0
        source = PolymarketPredictionMarketSource(
            gamma_base_url=self.settings.polymarket_gamma_base_url, clob_base_url=self.settings.polymarket_clob_base_url)
        for contract in self._active_contracts()[:20]:
            if contract.provider != "POLYMARKET" or not contract.provider_outcome_id:
                continue
            try:
                resp = await source.get_prices_history(contract.provider_outcome_id, interval="1h")
                points += len(resp.envelope.data)
            except SourceError:
                continue
        self._record_run("POLYMARKET", "backfill-prediction-market-history", status=RunStatus.SUCCEEDED,
                         count=points, meta={"note": "bounded history probe; not season-complete"})
        return {"records": points, "note": "bounded; not season complete"}

    def _mapped_game_ids(self) -> list[Any]:
        rows = self.session.execute(select(
            PredictionMarketMapping.provider_event_id, PredictionMarketMapping.canonical_game_id,
            PredictionMarketMapping.mapping_status, PredictionMarketMapping.decided_at,
        ).order_by(PredictionMarketMapping.decided_at.desc())).all()
        seen: set[str] = set()
        games: list[Any] = []
        for _event, gid, status, _dt in rows:
            if _event in seen or gid is None:
                continue
            seen.add(_event)
            if status in {"EXACT", "MANUAL_APPROVED"} and gid not in games:
                games.append(gid)
        return games


def _accumulate(totals: dict[str, int], result: dict[str, Any]) -> None:
    for key in ("contracts", "events", "mappings"):
        totals[key] = totals.get(key, 0) + int(result.get(key, 0) or 0)


__all__ = ["PredictionMarketJobs"]
