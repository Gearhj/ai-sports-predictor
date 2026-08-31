"""Prediction Market Observer write path.

Persists discovery (events + versioned contracts + append-only mappings),
point-in-time snapshots (idempotent, bounded order-book frames), the
display-only research consensus, and research-only alerts. All provider I/O
happens upstream (in a job); this module only takes normalized DTOs and writes
append-only evidence. Nothing here can create an odds row, a stake, a forecast,
or an order.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    ExternalIdMapping,
    Game,
    PredictionMarketAlert,
    PredictionMarketConsensusSnapshot,
    PredictionMarketContract,
    PredictionMarketEvent,
    PredictionMarketMapping,
    PredictionMarketOrderbookSnapshot,
    PredictionMarketSnapshot,
)
from app.prediction_markets.contracts import (
    ACCEPTED_MAPPING_STATUSES,
    ADAPTER_VERSION,
    CONSENSUS_VERSION,
    AlertType,
    CanonicalSelection,
    MappingStatus,
    NormalizedOrderBook,
    ProviderContractDTO,
    ProviderQuoteDTO,
    QualityTier,
    SemanticsStatus,
    assess_quality,
    price_view,
    sha256_payload,
    timeline_cut_for,
)
from app.prediction_markets.mapping import map_event_to_game, review_semantics

ALERT_THRESHOLD_VERSION = "PREDICTION_MARKET_ALERTS_V1"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class PredictionMarketObserver:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- discovery persistence + mapping ---------------------------------
    def ingest_contracts(
        self, contracts: list[ProviderContractDTO], *, ingestion_run_id: Any | None = None, now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        events_new = contracts_new = mappings_new = 0
        by_event: dict[tuple[str, str], list[ProviderContractDTO]] = {}
        for dto in contracts:
            by_event.setdefault((dto.provider.value, dto.provider_event_id), []).append(dto)

        for (provider, event_id), dtos in by_event.items():
            head = dtos[0]
            # 1) event observation (append-only, idempotent by content)
            event_checksum = sha256_payload({
                "h": head.provider_home_team, "a": head.provider_away_team,
                "s": head.scheduled_start_at.isoformat() if head.scheduled_start_at else None,
                "st": head.market_status,
            })
            if not self._exists(PredictionMarketEvent, provider=provider, provider_event_id=event_id, content_checksum=event_checksum):
                self.session.add(PredictionMarketEvent(
                    provider=provider, provider_event_id=event_id, sport=head.sport, league=head.league,
                    provider_home_team=head.provider_home_team, provider_away_team=head.provider_away_team,
                    scheduled_start_at=head.scheduled_start_at, provider_game_start_at=head.provider_game_start_at,
                    status=head.market_status, observed_at=now, content_checksum=event_checksum,
                    ingestion_run_id=ingestion_run_id,
                ))
                events_new += 1

            # 2) mapping decision (append-only; only when it changes)
            decision = map_event_to_game(
                self.session, provider=provider, provider_event_id=event_id, league_code=head.league,
                provider_home_team=head.provider_home_team, provider_away_team=head.provider_away_team,
                scheduled_start_at=head.scheduled_start_at,
            )
            semantics, sem_reason = review_semantics(
                market_question=head.market_question, resolution_rules=head.resolution_rules,
                market_status=head.market_status,
            )
            latest = self._latest_mapping(provider, event_id)
            changed = (
                latest is None
                or latest.mapping_status != decision.status.value
                or str(latest.canonical_game_id) != str(decision.canonical_game_id)
                or latest.semantics_status != semantics.value
            )
            if changed:
                self.session.add(PredictionMarketMapping(
                    provider=provider, provider_event_id=event_id,
                    canonical_game_id=decision.canonical_game_id, mapping_status=decision.status.value,
                    semantics_status=semantics.value, decision_source="AUTO",
                    reason=f"{decision.reason}; {sem_reason}",
                    start_time_delta_seconds=decision.start_time_delta_seconds,
                    candidate_game_ids=decision.candidate_game_ids, decided_at=now,
                    supersedes_id=latest.id if latest else None,
                ))
                mappings_new += 1
                # An EXACT auto-decision earns a stable identity mapping (idempotent).
                if decision.status == MappingStatus.EXACT and decision.canonical_game_id is not None:
                    if not self._exists(ExternalIdMapping, entity_type="game", source=provider, external_id=event_id):
                        self.session.add(ExternalIdMapping(
                            entity_type="game", entity_id=decision.canonical_game_id,
                            source=provider, external_id=event_id,
                        ))

            # 3) contracts (append-only, versioned; idempotent by content)
            for dto in dtos:
                selection = self._canonical_selection(dto, decision.home_is_yes)
                content_checksum = sha256_payload({
                    "mt": dto.market_type.value, "sel": selection.value, "line": dto.line_value,
                    "q": dto.market_question, "rules": dto.rules_sha256(), "label": dto.outcome_label,
                    "close": dto.market_close_at.isoformat() if dto.market_close_at else None,
                })
                if self._exists(
                    PredictionMarketContract, provider=provider, provider_market_id=dto.provider_market_id,
                    provider_outcome_id=dto.provider_outcome_id, content_checksum=content_checksum,
                ):
                    continue
                prior_version = self.session.scalar(select(PredictionMarketContract.version).where(
                    PredictionMarketContract.provider == provider,
                    PredictionMarketContract.provider_market_id == dto.provider_market_id,
                    PredictionMarketContract.provider_outcome_id == dto.provider_outcome_id,
                ).order_by(PredictionMarketContract.version.desc())) or 0
                self.session.add(PredictionMarketContract(
                    provider=provider, provider_event_id=event_id, provider_market_id=dto.provider_market_id,
                    provider_outcome_id=dto.provider_outcome_id, sport=dto.sport, league=dto.league,
                    market_type=dto.market_type.value, selection=selection.value, line_value=dto.line_value,
                    outcome_label=dto.outcome_label, provider_home_team=dto.provider_home_team,
                    provider_away_team=dto.provider_away_team, scheduled_start_at=dto.scheduled_start_at,
                    provider_game_start_at=dto.provider_game_start_at, market_question=dto.market_question,
                    market_subtitle=dto.market_subtitle, resolution_rules=dto.resolution_rules,
                    rules_sha256=dto.rules_sha256(), market_close_at=dto.market_close_at,
                    settlement_source=dto.settlement_source, market_status=dto.market_status,
                    line_source=dto.line_source, version=int(prior_version) + 1,
                    content_checksum=content_checksum, adapter_version=ADAPTER_VERSION,
                    ingestion_run_id=ingestion_run_id,
                ))
                contracts_new += 1

        self.session.commit()
        return {"events": events_new, "contracts": contracts_new, "mappings": mappings_new}

    @staticmethod
    def _canonical_selection(dto: ProviderContractDTO, home_is_yes: bool | None) -> CanonicalSelection:
        # Kalshi single-sided YES markets default to HOME at discovery; flip to
        # AWAY when the mapping proved the YES team is the AISP away team.
        if home_is_yes is False and dto.selection == CanonicalSelection.HOME:
            return CanonicalSelection.AWAY
        return dto.selection

    # -- snapshot persistence --------------------------------------------
    def record_snapshot(
        self, *, contract: PredictionMarketContract, quote: ProviderQuoteDTO, now: datetime | None = None,
    ) -> PredictionMarketSnapshot | None:
        now = now or datetime.now(UTC)
        mapping = self._latest_mapping(contract.provider, contract.provider_event_id)
        game = self.session.get(Game, mapping.canonical_game_id) if mapping and mapping.canonical_game_id else None
        game_start = _aware(game.starts_at) if game else _aware(contract.provider_game_start_at)
        pv = price_view(quote.book, last_trade=quote.last_trade)
        obs = _aware(quote.provider_observed_at) or now
        quote_age = (now - obs).total_seconds()
        seconds_to_game = int((game_start - obs).total_seconds()) if game_start else None
        in_play = bool(game_start and obs >= game_start)
        pregame_eligible = bool(game_start and obs < game_start)
        mapped = mapping is not None and MappingStatus(mapping.mapping_status) in ACCEPTED_MAPPING_STATUSES
        semantics_reviewed = mapping is not None and mapping.semantics_status == SemanticsStatus.SEMANTICS_MATCHED.value
        quality = assess_quality(
            price=pv, volume=quote.volume, open_interest=quote.open_interest, quote_age_seconds=quote_age,
            mapped=mapped, semantics_reviewed=semantics_reviewed,
            stale_seconds=self.settings.prediction_market_stale_seconds,
            max_spread=self.settings.prediction_market_max_spread,
            min_depth=self.settings.prediction_market_min_depth, in_play=in_play,
        )
        payload_hash = quote.payload_sha256()
        idem = f"{contract.provider}:{contract.provider_market_id}:{contract.provider_outcome_id}:{int(obs.timestamp())}:{payload_hash[:16]}"
        if self._exists(PredictionMarketSnapshot, idempotency_key=idem):
            return None
        depth1, depth2, depth5, imbalance = self._depth_metrics(quote.book, pv.midpoint)
        snapshot = PredictionMarketSnapshot(
            provider=contract.provider, contract_id=contract.id, provider_market_id=contract.provider_market_id,
            provider_outcome_id=contract.provider_outcome_id,
            canonical_game_id=mapping.canonical_game_id if mapping else None,
            market_type=contract.market_type, selection=contract.selection, line_value=contract.line_value,
            best_bid=pv.best_bid, best_ask=pv.best_ask, midpoint=pv.midpoint, microprice=pv.microprice,
            last_trade=pv.last_trade, bid_ask_spread=pv.bid_ask_spread, top_bid_size=pv.top_bid_size,
            top_ask_size=pv.top_ask_size, depth_within_1_percent=depth1, depth_within_2_percent=depth2,
            depth_within_5_percent=depth5, order_book_imbalance=imbalance, volume=quote.volume,
            open_interest=quote.open_interest, liquidity=quote.liquidity, provider_observed_at=obs,
            aisp_received_at=now, game_starts_at=game_start, seconds_to_game=seconds_to_game,
            pregame_eligible=pregame_eligible, in_play=in_play, provider_sequence=quote.provider_sequence,
            timeline_cut=timeline_cut_for(obs, game_start), quality_tier=quality.tier.value,
            quality_components=quality.components, quality_flags=quality.flags,
            mapping_status=mapping.mapping_status if mapping else MappingStatus.UNRESOLVED.value,
            semantics_status=mapping.semantics_status if mapping else SemanticsStatus.SEMANTICS_UNKNOWN.value,
            book_complete=pv.book_complete, quote_fresh=quality.quote_fresh, market_liquid=quality.market_liquid,
            source_status="OK", raw_payload_sha256=payload_hash, adapter_version=ADAPTER_VERSION,
            idempotency_key=idem,
        )
        self.session.add(snapshot)
        self.session.flush()
        self._maybe_record_orderbook(contract, snapshot, quote.book, obs, payload_hash, depth1, depth2, depth5)
        self.session.commit()
        return snapshot

    def _maybe_record_orderbook(
        self, contract, snapshot, book: NormalizedOrderBook, observed_at, payload_hash, depth1, depth2, depth5,
    ) -> None:
        """Bounded: only persist a frame when top-of-book changed (hash differs)."""
        last = self.session.scalar(select(PredictionMarketOrderbookSnapshot).where(
            PredictionMarketOrderbookSnapshot.contract_id == contract.id,
        ).order_by(PredictionMarketOrderbookSnapshot.observed_at.desc()))
        if last is not None and last.raw_payload_sha256 == payload_hash:
            return  # nothing changed — no duplicate frame
        idem = f"{contract.provider}:{contract.provider_market_id}:{contract.provider_outcome_id}:ob:{int(observed_at.timestamp())}:{payload_hash[:16]}"
        if self._exists(PredictionMarketOrderbookSnapshot, idempotency_key=idem):
            return
        self.session.add(PredictionMarketOrderbookSnapshot(
            snapshot_id=snapshot.id, contract_id=contract.id, provider=contract.provider,
            bids=[[l.price, l.size] for l in book.bids[:10]], asks=[[l.price, l.size] for l in book.asks[:10]],
            raw_yes_bids=[[l.price, l.size] for l in (book.raw_yes_bids or [])[:10]] if book.raw_yes_bids else None,
            raw_no_bids=[[l.price, l.size] for l in (book.raw_no_bids or [])[:10]] if book.raw_no_bids else None,
            depth_within_1_percent=depth1, depth_within_2_percent=depth2, depth_within_5_percent=depth5,
            provider_sequence=book.provider_sequence, observed_at=observed_at, raw_payload_sha256=payload_hash,
            idempotency_key=idem,
        ))

    @staticmethod
    def _depth_metrics(book: NormalizedOrderBook, midpoint: float | None):
        if midpoint is None:
            return None, None, None, None
        def within(pct: float) -> float:
            lo, hi = midpoint * (1 - pct), midpoint * (1 + pct)
            size = sum(l.size for l in book.bids if lo <= l.price <= hi)
            size += sum(l.size for l in book.asks if lo <= l.price <= hi)
            return round(size, 4)
        bid_size = sum(l.size for l in book.bids)
        ask_size = sum(l.size for l in book.asks)
        imbalance = round((bid_size - ask_size) / (bid_size + ask_size), 4) if (bid_size + ask_size) > 0 else None
        return within(0.01), within(0.02), within(0.05), imbalance

    # -- consensus (display-only, research-only) -------------------------
    def compute_consensus(self, game_id: Any, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        latest = self._latest_snapshots_by_provider(game_id)
        written = 0
        groups: dict[tuple[str, str, Any], dict[str, PredictionMarketSnapshot]] = {}
        for snap in latest:
            groups.setdefault((snap.market_type, snap.selection, snap.line_value), {})[snap.provider] = snap
        for (market_type, selection, line), by_provider in groups.items():
            eligible_prices: dict[str, float] = {}
            reasons: list[str] = []
            for provider, snap in by_provider.items():
                if snap.midpoint is None:
                    reasons.append(f"{provider}: no midpoint")
                    continue
                if snap.in_play:
                    reasons.append(f"{provider}: in-play excluded")
                    continue
                if snap.mapping_status not in {s.value for s in ACCEPTED_MAPPING_STATUSES}:
                    reasons.append(f"{provider}: not mapped")
                    continue
                if snap.semantics_status != SemanticsStatus.SEMANTICS_MATCHED.value:
                    reasons.append(f"{provider}: semantics unmatched")
                    continue
                if snap.quality_tier in {QualityTier.THIN.value, QualityTier.STALE.value, QualityTier.INCOMPLETE.value}:
                    reasons.append(f"{provider}: quality {snap.quality_tier}")
                    continue
                eligible_prices[provider] = snap.midpoint
            prices = list(eligible_prices.values())
            median = round(statistics.median(prices), 6) if prices else None
            dispersion = round(max(prices) - min(prices), 6) if len(prices) > 1 else (0.0 if prices else None)
            eligible = len(prices) >= 2
            reason = "Cross-provider consensus formed" if eligible else (
                "Only one provider qualifies — cross-provider consensus unavailable" if len(prices) == 1
                else "No provider qualifies")
            self.session.add(PredictionMarketConsensusSnapshot(
                canonical_game_id=game_id, market_type=market_type, selection=selection, line_value=line,
                eligible=eligible, reason="; ".join([reason, *reasons]) if reasons else reason,
                contributing_providers=sorted(eligible_prices.keys()),
                provider_prices={p: {"midpoint": v, "tier": by_provider[p].quality_tier} for p, v in eligible_prices.items()},
                median_probability=median if eligible else None, quality_weighted_probability=median if eligible else None,
                dispersion=dispersion, consensus_version=CONSENSUS_VERSION, computed_at=now,
            ))
            written += 1
        self.session.commit()
        return written

    # -- alerts (research-only) ------------------------------------------
    def evaluate_alerts(self, game_id: Any, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        latest = self._latest_snapshots_by_provider(game_id)
        written = 0
        for snap in latest:
            flags = set(snap.quality_flags or [])
            if "THIN_DEPTH" in flags:
                written += self._alert(AlertType.THIN_MARKET_WARNING, game_id, snap, now, {"top_depth": snap.top_bid_size})
            if "STALE_QUOTE" in flags:
                written += self._alert(AlertType.STALE_MARKET_WARNING, game_id, snap, now, {"quote_age_seconds": (snap.quality_components or {}).get("quote_age")})
            if snap.mapping_status == MappingStatus.REVIEW_REQUIRED.value:
                written += self._alert(AlertType.MAPPING_REVIEW_REQUIRED, game_id, snap, now, {})
            if snap.semantics_status == SemanticsStatus.SEMANTICS_MISMATCH.value:
                written += self._alert(AlertType.CONTRACT_RULE_MISMATCH, game_id, snap, now, {})
        # cross-venue + move alerts are computed over paired providers/history
        written += self._cross_and_move_alerts(game_id, latest, now)
        self.session.commit()
        return written

    def _cross_and_move_alerts(self, game_id, latest, now) -> int:
        written = 0
        by_key: dict[tuple[str, str, Any], list[PredictionMarketSnapshot]] = {}
        for snap in latest:
            by_key.setdefault((snap.market_type, snap.selection, snap.line_value), []).append(snap)
        for key, snaps in by_key.items():
            mids = {s.provider: s.midpoint for s in snaps if s.midpoint is not None and not s.in_play}
            if len(mids) >= 2:
                spread = max(mids.values()) - min(mids.values())
                if spread >= 0.05:
                    written += self._alert(AlertType.CROSS_VENUE_DIVERGENCE, game_id, snaps[0], now, {"providers": mids, "spread": round(spread, 4)})
                elif spread <= 0.01:
                    written += self._alert(AlertType.CROSS_VENUE_CONFIRMATION, game_id, snaps[0], now, {"providers": mids, "spread": round(spread, 4)})
        return written

    def _alert(self, alert_type: AlertType, game_id, snap, now, details) -> int:
        idem = f"{alert_type.value}:{game_id}:{snap.provider}:{snap.market_type}:{snap.selection}:{int(now.timestamp() // 300)}"
        if self._exists(PredictionMarketAlert, idempotency_key=idem):
            return 0
        self.session.add(PredictionMarketAlert(
            alert_type=alert_type.value, canonical_game_id=game_id, provider=snap.provider,
            market_type=snap.market_type, selection=snap.selection, magnitude=details.get("spread"),
            details=details, threshold_version=ALERT_THRESHOLD_VERSION, observed_at=now, idempotency_key=idem,
        ))
        return 1

    # -- helpers ---------------------------------------------------------
    def _latest_mapping(self, provider: str, event_id: str) -> PredictionMarketMapping | None:
        return self.session.scalar(select(PredictionMarketMapping).where(
            PredictionMarketMapping.provider == provider,
            PredictionMarketMapping.provider_event_id == event_id,
        ).order_by(PredictionMarketMapping.decided_at.desc()))

    def _latest_snapshots_by_provider(self, game_id: Any) -> list[PredictionMarketSnapshot]:
        rows = list(self.session.scalars(select(PredictionMarketSnapshot).where(
            PredictionMarketSnapshot.canonical_game_id == game_id,
        ).order_by(PredictionMarketSnapshot.provider_observed_at.desc())))
        seen: dict[tuple[str, str, str, Any], PredictionMarketSnapshot] = {}
        for row in rows:
            key = (row.provider, row.market_type, row.selection, row.line_value)
            seen.setdefault(key, row)
        return list(seen.values())

    def _exists(self, model, **filters) -> bool:
        conds = [getattr(model, k) == v for k, v in filters.items()]
        return self.session.scalar(select(model.id).where(*conds).limit(1)) is not None


__all__ = ["PredictionMarketObserver", "ALERT_THRESHOLD_VERSION"]
