"""Prediction Market Observer jobs — the ONLY place provider I/O happens.

Each job is gated by settings (observer + provider flags, all off by default),
bounded by max-active-markets and a per-run request budget, records an
IngestionRun, and persists append-only evidence through the observer. No public
read path ever reaches this module. Nothing here places an order: the adapters
expose market data only.

Kalshi's baseline path is PUBLIC REST — series verification, event enumeration
with cursor pagination and top-of-book quotes, and public order books. An
absent API key never disables it (credentials only matter for the optional,
separately flagged read-only WebSocket).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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
    PredictionMarketSnapshot,
    RunStatus,
)
from app.prediction_markets.contracts import (
    TIMELINE_CUT_OFFSETS_MINUTES,
    CanonicalMarketType,
    CanonicalSelection,
    ProviderContractDTO,
    ProviderQuoteDTO,
    TimelineCut,
)
from app.prediction_markets.kalshi import KalshiPredictionMarketSource, classify_series
from app.prediction_markets.polymarket import PolymarketPredictionMarketSource
from app.prediction_markets.service import PredictionMarketObserver

# Sport -> (polymarket league label, kalshi sports category hint)
_SPORT_LEAGUE = {"MLB": "MLB", "NFL": "NFL", "NBA": "NBA", "NHL": "NHL"}

# Pregame capture schedule (source-time governed): opening/first-discovered
# observation, ~T-6h, T-2h, T-30m, T-10m, T-5m; LAST_PREGAME is derived at read
# time as the latest observation strictly before first pitch.
PREGAME_CAPTURE_CUTS: tuple[TimelineCut, ...] = (
    TimelineCut.T_MINUS_6_HOURS, TimelineCut.T_MINUS_2_HOURS, TimelineCut.T_MINUS_30_MINUTES,
    TimelineCut.T_MINUS_10_MINUTES, TimelineCut.T_MINUS_5_MINUTES,
)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


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

    def _kalshi_source(self) -> KalshiPredictionMarketSource:
        return KalshiPredictionMarketSource(
            rest_base_url=self.settings.kalshi_rest_base_url,
            api_key_id=self.settings.kalshi_api_key_id, private_key_path=self.settings.kalshi_private_key_path,
            ws_enabled=self.settings.kalshi_market_data_ws_enabled,
        )

    def _polymarket_source(self) -> PolymarketPredictionMarketSource:
        return PolymarketPredictionMarketSource(
            gamma_base_url=self.settings.polymarket_gamma_base_url, clob_base_url=self.settings.polymarket_clob_base_url)

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
                per_provider[f"KALSHI/{sport}"] = await self._discover_kalshi(league, sport, event_date=event_date)
                _accumulate(totals, per_provider[f"KALSHI/{sport}"])
        return {"records": totals["contracts"], **totals, "providers": per_provider}

    async def _discover_polymarket(self, league: str) -> dict[str, Any]:
        source = self._polymarket_source()
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

    async def _discover_kalshi(self, league: str, sport: str, *, event_date: date | None = None) -> dict[str, Any]:
        """Enumerate the league's full-game series through the public events
        listing (all pages), verify the series metadata dynamically, ingest the
        contracts and persist the top-of-book quotes the listing already carries."""
        source = self._kalshi_source()
        today = datetime.now(UTC).date()
        statuses: tuple[str, ...] = ("open",)
        not_before = None
        if event_date is not None and event_date < today:
            statuses = ("open", "settled")  # reconstruct a completed slate, bounded to its date window
            not_before = datetime(event_date.year, event_date.month, event_date.day, tzinfo=UTC) - timedelta(days=1)
        series_tickers = [f"KX{league.upper()}GAME"]
        if "SPREAD" in self.settings.prediction_market_type_list:
            series_tickers.append(f"KX{league.upper()}SPREAD")
        if "TOTAL" in self.settings.prediction_market_type_list:
            series_tickers.append(f"KX{league.upper()}TOTAL")
        contracts: list[ProviderContractDTO] = []
        quotes: list[dict[str, Any]] = []
        markets_seen = events_seen = pages = 0
        excluded: dict[str, int] = {}
        queries: list[dict[str, Any]] = []
        errors: list[str] = []
        series_meta: dict[str, Any] = {}
        for ticker in series_tickers:
            try:
                verified = await source.get_series(ticker)
                series_meta[ticker] = {
                    **{k: v for k, v in (verified.envelope.data[0] or {}).items()
                       if k in ("ticker", "title", "category", "tags", "contract_url", "settlement_sources", "frequency")},
                    "classification": classify_series(ticker, league),
                }
            except SourceError as exc:
                errors.append(f"{ticker}: series verification failed: {exc}")
                continue
            if series_meta[ticker]["classification"] not in {"MONEYLINE", "SPREAD", "TOTAL"}:
                errors.append(f"{ticker}: not a full-game series")
                continue
            try:
                resp = await source.discover_game_events(series_ticker=ticker, league=league, sport=sport, statuses=statuses,
                                                         max_pages=3 if not_before else 10, not_before=not_before)
            except SourceError as exc:
                errors.append(f"{ticker}: {exc}")
                continue
            contracts.extend(ProviderContractDTO.model_validate(item) for item in resp.envelope.data)
            meta = resp.envelope.metadata or {}
            markets_seen += int(meta.get("markets_seen") or 0)
            events_seen += int(meta.get("events_seen") or 0)
            pages += int(meta.get("pages") or 0)
            quotes.extend(meta.get("quotes") or [])
            for reason, n in (meta.get("excluded") or {}).items():
                excluded[reason] = excluded.get(reason, 0) + int(n)
            queries.extend((meta.get("query") or {}).get("requests") or [])
        status = RunStatus.SUCCEEDED if contracts or not errors else RunStatus.FAILED
        run = self._record_run(
            "KALSHI", "discover-prediction-markets", status=status,
            count=len(contracts), error="; ".join(errors) if status == RunStatus.FAILED else None,
            meta={
                "series_scanned": len(series_tickers), "series": series_meta, "mode": source.connection_mode,
                "credentials_used": False, "requests": source.request_count, "pages": pages,
                "events_seen": events_seen, "markets_seen": markets_seen, "contracts": len(contracts), "league": league,
                "statuses": list(statuses), "excluded": excluded, "query": {"series": series_tickers, "requests": queries},
                "series_errors": errors,
            },
        )
        result = self.observer.ingest_contracts(contracts, ingestion_run_id=run.id)
        persisted = self._persist_listing_quotes(quotes)
        return {**result, "provider": "KALSHI", "mode": source.connection_mode, "requests": source.request_count,
                "credentials_used": False, "markets_seen": markets_seen, "events_seen": events_seen,
                "excluded": excluded, "listing_quotes": persisted, "series": series_meta, "errors": errors}

    def _persist_listing_quotes(self, quotes: list[dict[str, Any]]) -> int:
        """Record top-of-book quotes carried by a listing, only for contracts
        whose event is EXACT/MANUAL_APPROVED-mapped to a canonical game (a
        listing spans the provider's whole catalogue; unmapped and historical
        markets are not evidence for any AISP game)."""
        persisted = 0
        _, mapped_events = self._mapped_event_priority(None)
        for raw in quotes:
            try:
                quote = ProviderQuoteDTO.model_validate(raw)
            except Exception:
                continue
            contract = self.session.scalar(select(PredictionMarketContract).where(
                PredictionMarketContract.provider == quote.provider.value,
                PredictionMarketContract.provider_market_id == quote.provider_market_id,
            ).order_by(PredictionMarketContract.version.desc()))
            if contract is None or (contract.provider, contract.provider_event_id) not in mapped_events:
                continue
            try:
                if self.observer.record_snapshot(contract=contract, quote=quote) is not None:
                    persisted += 1
            except Exception:
                self.session.rollback()
        return persisted

    # -- snapshot refresh ------------------------------------------------
    async def refresh(self, event_date: date) -> dict[str, Any]:
        if not self.settings.prediction_market_observer_enabled:
            return {"records": 0, "status": "skipped", "reason": "observer disabled"}
        contracts = self._active_contracts(event_date)
        budget = self.settings.prediction_market_max_requests_per_hour
        snapshots = errors = 0
        used = 0
        persist_errors: list[str] = []
        per_provider: dict[str, dict[str, int]] = {"POLYMARKET": {"requests": 0, "snapshots": 0, "errors": 0},
                                                   "KALSHI": {"requests": 0, "snapshots": 0, "errors": 0}}
        # Kalshi: one public events listing per league answers every mapped
        # market's top of book (no credentials, no per-market request).
        kalshi_contracts = [c for c in contracts if c.provider == "KALSHI"]
        if kalshi_contracts and self.settings.kalshi_enabled and self.settings.kalshi_rest_polling_enabled:
            source = self._kalshi_source()
            wanted = {c.provider_market_id for c in kalshi_contracts}
            for league in {c.league for c in kalshi_contracts}:
                try:
                    resp = await source.discover_game_events(series_ticker=f"KX{league.upper()}GAME", league=league, sport=league)
                    for raw in (resp.envelope.metadata or {}).get("quotes") or []:
                        if raw.get("provider_market_id") in wanted:
                            n = self._persist_listing_quotes([raw])
                            snapshots += n
                            per_provider["KALSHI"]["snapshots"] += n
                except SourceError:
                    errors += 1
                    per_provider["KALSHI"]["errors"] += 1
            used += source.request_count
            per_provider["KALSHI"]["requests"] = source.request_count
        for contract in contracts:
            if contract.provider == "KALSHI":
                continue
            if used >= budget:
                break
            try:
                quote = await self._fetch_quote(contract)
                used += 1
                per_provider.setdefault(contract.provider, {"requests": 0, "snapshots": 0, "errors": 0})["requests"] += 1
                if quote is not None and self.observer.record_snapshot(contract=contract, quote=quote) is not None:
                    snapshots += 1
                    per_provider[contract.provider]["snapshots"] += 1
            except SourceError:
                errors += 1
                per_provider.setdefault(contract.provider, {"requests": 0, "snapshots": 0, "errors": 0})["errors"] += 1
            except Exception as exc:  # one bad row must not abort the audited run
                self.session.rollback()
                errors += 1
                if len(persist_errors) < 5:
                    persist_errors.append(f"{contract.provider}:{contract.provider_market_id}: {type(exc).__name__}: {exc}"[:300])
        self._record_run("PREDICTION_MARKET", "refresh-prediction-market-snapshots", status=RunStatus.SUCCEEDED,
                         count=snapshots, meta={"contracts": len(contracts), "requests": used, "errors": errors,
                                                "persist_errors": persist_errors, "skipped": {},
                                                "per_provider": per_provider, "credentials_used": False})
        return {"records": snapshots, "contracts": len(contracts), "requests": used, "errors": errors,
                "skipped": {}, "per_provider": per_provider}

    async def _fetch_quote(self, contract: PredictionMarketContract) -> ProviderQuoteDTO | None:
        if contract.provider == "POLYMARKET":
            resp = await self._polymarket_source().get_order_book(contract.provider_outcome_id or contract.provider_market_id)
        elif contract.provider == "KALSHI":
            resp = await self._kalshi_source().get_order_book(contract.provider_market_id)
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
        listing volume cannot starve the other. Within a tier the full-game
        moneyline precedes spread/total and the HOME side precedes the AWAY side.
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
            if (r.market_status or "").lower() not in {"closed", "settled", "inactive", "finalized"}:
                latest.append(r)
        slate_events, mapped_events = self._mapped_event_priority(event_date)

        def priority(c: PredictionMarketContract) -> int:
            key = (c.provider, c.provider_event_id)
            moneyline = 0 if c.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value else 1
            if key in slate_events:
                return 0 + moneyline
            if key in mapped_events:
                return 2 + moneyline
            return 4 + moneyline

        buckets: dict[tuple[int, str], list[PredictionMarketContract]] = {}
        for c in sorted(latest, key=lambda c: 0 if c.selection == CanonicalSelection.HOME.value else 1):
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

    # -- pregame capture schedule --------------------------------------------
    def _slate_moneyline_contracts(self, event_date: date) -> list[tuple[PredictionMarketContract, Game]]:
        from app.core.time import local_day_bounds

        slate_events, _ = self._mapped_event_priority(event_date)
        if not slate_events:
            return []
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        games = {g.id: g for g in self.session.scalars(select(Game).where(Game.starts_at >= start, Game.starts_at < end))}
        rows = self.session.execute(select(
            PredictionMarketMapping.provider, PredictionMarketMapping.provider_event_id,
            PredictionMarketMapping.canonical_game_id, PredictionMarketMapping.decided_at,
        ).order_by(PredictionMarketMapping.decided_at.desc())).all()
        game_of: dict[tuple[str, str], Any] = {}
        for provider, event_id, gid, _ in rows:
            game_of.setdefault((provider, event_id), gid)
        out: list[tuple[PredictionMarketContract, Game]] = []
        for c in self._active_contracts(event_date):
            key = (c.provider, c.provider_event_id)
            if key not in slate_events or c.market_type != CanonicalMarketType.FULL_GAME_MONEYLINE.value:
                continue
            if c.selection != CanonicalSelection.HOME.value:
                continue  # the HOME side is the board's comparison contract
            game = games.get(game_of.get(key))
            if game is not None:
                out.append((c, game))
        return out

    def due_cuts(self, contract: PredictionMarketContract, game: Game, now: datetime) -> list[TimelineCut]:
        """Cuts whose window has opened (start - offset <= now < start) and that
        this contract has not captured yet. Idempotent by persisted timeline_cut."""
        start = _aware(game.starts_at)
        if start is None or now >= start:
            return []
        captured = set(self.session.scalars(select(PredictionMarketSnapshot.timeline_cut).where(
            PredictionMarketSnapshot.contract_id == contract.id,
            PredictionMarketSnapshot.timeline_cut.is_not(None),
        )))
        due: list[TimelineCut] = []
        for cut in PREGAME_CAPTURE_CUTS:
            offset = TIMELINE_CUT_OFFSETS_MINUTES[cut]
            if start - timedelta(minutes=offset) <= now and cut.value not in captured:
                due.append(cut)
        return due

    async def capture_pregame_cuts(self, event_date: date, *, now: datetime | None = None) -> dict[str, Any]:
        """Bounded, idempotent capture of the scheduled pregame observations for
        every mapped slate moneyline contract. Source timestamps govern the cut
        label (record_snapshot), so a delayed run never backdates evidence."""
        if not self.settings.prediction_market_observer_enabled:
            return {"records": 0, "status": "skipped", "reason": "observer disabled"}
        now = now or datetime.now(UTC)
        budget = min(self.settings.prediction_market_max_requests_per_hour, 120)
        used = errors = captured = 0
        for contract, game in self._slate_moneyline_contracts(event_date):
            if used >= budget:
                break
            if not self.due_cuts(contract, game, now):
                continue
            try:
                quote = await self._fetch_quote(contract)
                used += 1
                if quote is not None and self.observer.record_snapshot(contract=contract, quote=quote, now=now) is not None:
                    captured += 1
            except SourceError:
                errors += 1
            except Exception:
                self.session.rollback()
                errors += 1
        self._record_run("PREDICTION_MARKET", "capture-pregame-cuts", status=RunStatus.SUCCEEDED, count=captured,
                         meta={"requests": used, "errors": errors, "credentials_used": False, "cuts": [c.value for c in PREGAME_CAPTURE_CUTS]})
        return {"records": captured, "requests": used, "errors": errors}

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
        """Capture due pregame cuts, refresh, then consensus."""
        cuts = await self.capture_pregame_cuts(event_date)
        result = await self.refresh(event_date)
        consensus = await self.compute_consensus(event_date)
        return {"records": result["records"] + cuts["records"], "final_snapshots": result["records"],
                "pregame_cuts": cuts["records"], "consensus": consensus["records"]}

    async def backfill(self, event_date: date, *, days: int = 1) -> dict[str, Any]:
        """Bounded historical backfill via official history endpoints only."""
        if not (self.settings.prediction_market_observer_enabled and self.settings.polymarket_enabled):
            return {"records": 0, "status": "skipped"}
        points = 0
        source = self._polymarket_source()
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


__all__ = ["PredictionMarketJobs", "PREGAME_CAPTURE_CUTS"]
