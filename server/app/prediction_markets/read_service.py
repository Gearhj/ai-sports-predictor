"""Public, database-only read model for the Prediction Market Observer.

Every method reads persisted rows only — it NEVER calls Polymarket or Kalshi.
It reuses the existing AISP forecast + sportsbook no-vig read model for the
comparison columns and never recomputes sportsbook pricing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import local_day_bounds
from app.markets.availability import observed_probability
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
    RunStatus,
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
    ObserverJobStatus,
    OrderBookView,
    PredictionMarketAlertsData,
    PredictionMarketAlertView,
    PredictionMarketHealthSummary,
    ProviderCoverage,
    ProviderHealth,
    ProviderProbability,
    TimelineMarker,
    TimelinePoint,
)

_SPORT_LEAGUES = {"MLB": "MLB", "NFL": "NFL", "NBA": "NBA", "NHL": "NHL"}


# Kalshi listing quotes persisted before the persister was restricted to
# accepted-mapped contracts (2026-09-05 02:08 UTC). Rows before this instant with
# no canonical game are LEGACY_PRE_AUTHORITY_OBSERVATION: kept, never rewritten,
# never counted as current pregame / book evidence.
LEGACY_LISTING_AUTHORITY_CUTOFF = datetime(2026, 9, 5, 2, 8, tzinfo=UTC)


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
        """One row per canonical scheduled game.

        The board is seeded from the canonical slate (the same LIVE games the
        Daily Command Center shows), never from whichever games happen to have
        a persisted snapshot. A game without a quote is still a row whose
        provider coverage says exactly why the column is blank.
        """
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        slate = self._canonical_slate(start, end)
        seen = {g.id for g in slate}
        # Snapshot-bearing games outside the canonical slate (e.g. demo-mode
        # rows) are still shown so a persisted observation is never hidden.
        for gid in self.session.scalars(
            select(PredictionMarketSnapshot.canonical_game_id)
            .where(PredictionMarketSnapshot.canonical_game_id.is_not(None)).distinct()
        ):
            if gid in seen:
                continue
            game = self.session.get(Game, gid)
            if game is not None and start <= _aware(game.starts_at) < end:
                slate.append(game)
                seen.add(gid)
        ctx = self._coverage_context([g.id for g in slate])
        rows: list[MarketPulseRow] = []
        sports_available: set[str] = set()
        for game in slate:
            row = self._row(game, ctx)
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
        board_state, reason = self._board_state(rows, providers, now)
        # The real registered next fire time — never a stale projection.
        next_discovery = self._next_run_time("asp-pm-discover") if observer_enabled else None
        coverage = self._coverage_counts(rows)
        summary = {
            "games": len(rows),
            "canonical_games": len(rows),
            "mapped": sum(1 for r in rows if r.mapping_status in {"EXACT", "MANUAL_APPROVED"}),
            "quoted": coverage["quoted_any"],
            "decision_eligible": coverage["decision_eligible"],
            "unreviewed_observations": coverage["QUOTED_UNREVIEWED"],
            "review_required": sum(1 for r in rows if r.mapping_status == "REVIEW_REQUIRED"),
            "semantics_review_pending": coverage["semantics_review_pending"],
            "consensus_available": sum(1 for r in rows if r.consensus_eligible),
            # Only reviewed, decision-eligible quotes may claim a gap.
            "biggest_disagreement": max((abs(r.diff_aisp_prediction) for r in rows if r.diff_aisp_prediction is not None), default=None),
            "biggest_unreviewed_gap": max((abs(r.diff_aisp_prediction_unreviewed) for r in rows
                                           if r.diff_aisp_prediction_unreviewed is not None), default=None),
            "generated_at": now.isoformat(),
        }
        return MarketPulseBoard(
            data_mode="live" if rows else "empty", date=event_date, sport=sport,
            sports_available=sorted(sports_available),
            public_display_enabled=self.settings.prediction_market_public_display_enabled,
            observer_enabled=observer_enabled, board_state=board_state, state_reason=reason,
            providers=providers, next_discovery_at=next_discovery,
            semantics_review_count=self._semantics_review_count(), rows=rows, summary=summary,
            canonical_games=len(rows), coverage=coverage, coverage_by_provider=self._coverage_by_provider(rows),
            semantic_template_decisions_pending=self._template_decisions_pending(),
            excluded_summary=self._excluded_summary(providers),
            jobs=self._observer_jobs(),
        )

    def _canonical_slate(self, start: datetime, end: datetime) -> list[Game]:
        """Same slate rule as the operational read model: LIVE games for the
        local day; demo games only when demo mode is on."""
        games = list(self.session.scalars(
            select(Game).where(Game.starts_at >= start, Game.starts_at < end).order_by(Game.starts_at)))
        live = [g for g in games if g.source_mode == "LIVE"]
        if live:
            return live
        return games if self.settings.demo_mode else []

    # -- canonical coverage ----------------------------------------------
    def _enabled_providers(self) -> dict[str, bool]:
        s = self.settings
        return {"POLYMARKET": bool(s.polymarket_enabled),
                "KALSHI": bool(s.kalshi_enabled and s.kalshi_rest_polling_enabled)}

    def _coverage_context(self, game_ids: list[Any]) -> dict[str, Any]:
        """Bulk-prefetch everything `_row` needs per (game, provider) in three
        queries instead of three per row: latest mapping, listed-contract
        counts for the mapped events, and the latest moneyline snapshot."""
        ctx: dict[str, Any] = {"mapping": {}, "latest_mapping": {}, "contracts": {}, "snapshot": {},
                               "teams": {}, "refresh": self._latest_refresh_meta()}
        if not game_ids:
            return ctx
        for gid, is_home, name, abbrev in self.session.execute(
            select(GameTeam.game_id, GameTeam.is_home, Team.name, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id.in_(game_ids))
        ).all():
            ctx["teams"].setdefault(gid, {})[bool(is_home)] = (name, abbrev)
        mappings = list(self.session.scalars(select(PredictionMarketMapping).where(
            PredictionMarketMapping.canonical_game_id.in_(game_ids),
        ).order_by(PredictionMarketMapping.decided_at.desc())))
        event_keys: set[tuple[str, str]] = set()
        for m in mappings:
            ctx["latest_mapping"].setdefault(m.canonical_game_id, m)
            ctx["mapping"].setdefault((m.canonical_game_id, m.provider), m)
            event_keys.add((m.provider, m.provider_event_id))
        if event_keys:
            counts = self.session.execute(
                select(PredictionMarketContract.provider, PredictionMarketContract.provider_event_id,
                       func.count(func.distinct(PredictionMarketContract.provider_market_id)))
                .where(PredictionMarketContract.provider_event_id.in_({e for _, e in event_keys}))
                .group_by(PredictionMarketContract.provider, PredictionMarketContract.provider_event_id)
            ).all()
            ctx["contracts"] = {(p, e): int(n) for p, e, n in counts}
        ranked = select(
            PredictionMarketSnapshot.id,
            func.row_number().over(
                partition_by=(PredictionMarketSnapshot.canonical_game_id, PredictionMarketSnapshot.provider),
                order_by=PredictionMarketSnapshot.provider_observed_at.desc(),
            ).label("rn"),
        ).where(
            PredictionMarketSnapshot.canonical_game_id.in_(game_ids),
            PredictionMarketSnapshot.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value,
        ).subquery()
        latest_ids = select(ranked.c.id).where(ranked.c.rn == 1)
        for snap in self.session.scalars(select(PredictionMarketSnapshot).where(PredictionMarketSnapshot.id.in_(latest_ids))):
            ctx["snapshot"][(snap.canonical_game_id, snap.provider)] = snap
        # The FINAL VALID PREGAME observation per (game, provider): the latest
        # snapshot whose governed source time is strictly before first pitch and
        # that carries a two-sided midpoint. It survives after the market goes
        # in-play or final; the latest observation is kept separately.
        pre_ranked = select(
            PredictionMarketSnapshot.id,
            func.row_number().over(
                partition_by=(PredictionMarketSnapshot.canonical_game_id, PredictionMarketSnapshot.provider),
                order_by=PredictionMarketSnapshot.provider_observed_at.desc(),
            ).label("rn"),
        ).where(
            PredictionMarketSnapshot.canonical_game_id.in_(game_ids),
            PredictionMarketSnapshot.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value,
            PredictionMarketSnapshot.pregame_eligible.is_(True),
            PredictionMarketSnapshot.in_play.is_(False),
            PredictionMarketSnapshot.midpoint.is_not(None),
        ).subquery()
        ctx["pregame"] = {}
        for snap in self.session.scalars(select(PredictionMarketSnapshot).where(
                PredictionMarketSnapshot.id.in_(select(pre_ranked.c.id).where(pre_ranked.c.rn == 1)))):
            ctx["pregame"][(snap.canonical_game_id, snap.provider)] = snap
        ctx["provider_failed"] = self._failed_providers()
        # Latest HOME moneyline consensus per game, alert counts, league codes.
        cons_ranked = select(
            PredictionMarketConsensusSnapshot.id,
            func.row_number().over(partition_by=PredictionMarketConsensusSnapshot.canonical_game_id,
                                   order_by=PredictionMarketConsensusSnapshot.computed_at.desc()).label("rn"),
        ).where(
            PredictionMarketConsensusSnapshot.canonical_game_id.in_(game_ids),
            PredictionMarketConsensusSnapshot.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value,
            PredictionMarketConsensusSnapshot.selection == CanonicalSelection.HOME.value,
        ).subquery()
        ctx["consensus"] = {c.canonical_game_id: c for c in self.session.scalars(
            select(PredictionMarketConsensusSnapshot).where(
                PredictionMarketConsensusSnapshot.id.in_(select(cons_ranked.c.id).where(cons_ranked.c.rn == 1))))}
        ctx["alerts"] = {gid: int(n) for gid, n in self.session.execute(
            select(PredictionMarketAlert.canonical_game_id, func.count())
            .where(PredictionMarketAlert.canonical_game_id.in_(game_ids))
            .group_by(PredictionMarketAlert.canonical_game_id)).all()}
        from app.models import League

        ctx["league"] = {lid: code for lid, code in self.session.execute(select(League.id, League.code)).all()}
        return ctx

    def _template_decisions_pending(self) -> int | None:
        try:
            from app.prediction_markets.semantics import STATUS_HELD, SemanticTemplateAuthority

            authority = SemanticTemplateAuthority(self.session)
            return sum(1 for t in authority.all_latest() if t.status == STATUS_HELD)
        except Exception:
            self.session.rollback()
            return None

    def _failed_providers(self) -> set[str]:
        """Providers whose most recent discovery run FAILED (provider error state)."""
        failed: set[str] = set()
        for provider in ("POLYMARKET", "KALSHI"):
            run = self.session.scalar(select(IngestionRun).where(
                IngestionRun.source == provider, IngestionRun.job_name == "discover-prediction-markets",
            ).order_by(IngestionRun.completed_at.desc().nulls_last()))
            if run is not None and run.status == RunStatus.FAILED:
                failed.add(provider)
        return failed

    def _latest_refresh_meta(self) -> dict[str, Any]:
        run = self.session.scalar(select(IngestionRun).where(
            IngestionRun.source == "PREDICTION_MARKET",
            IngestionRun.job_name == "refresh-prediction-market-snapshots",
        ).order_by(IngestionRun.completed_at.desc().nulls_last()))
        return dict(run.source_metadata or {}) if run else {}

    def _provider_coverage(self, game: Game, provider: str, ctx: dict[str, Any]) -> tuple[ProviderCoverage, ProviderProbability | None]:
        enabled = self._enabled_providers().get(provider, False)
        mapping = ctx["mapping"].get((game.id, provider))
        latest = ctx["snapshot"].get((game.id, provider))
        pregame = (ctx.get("pregame") or {}).get((game.id, provider))
        listed = ctx["contracts"].get((provider, mapping.provider_event_id), 0) if mapping else 0
        common = {"provider": provider, "contracts_listed": listed,
                  "mapping_status": mapping.mapping_status if mapping else None,
                  "semantics_status": mapping.semantics_status if mapping else None,
                  "last_snapshot_at": _aware(latest.provider_observed_at) if latest else None,
                  "pregame_observed_at": _aware(pregame.provider_observed_at) if pregame else None,
                  "pregame_timeline_cut": pregame.timeline_cut if pregame else None,
                  "latest_in_play": bool(latest.in_play) if latest else False,
                  "semantics_authority": self._semantics_authority(mapping)}
        # The final valid pregame observation is the board's price: it is never
        # overwritten by a later in-play/final value (that is kept separately).
        if pregame is not None:
            prob = self._probability_from_snapshot(pregame, latest=latest, mapping=mapping, kind="FINAL_PREGAME")
            common["quality_tier"] = pregame.quality_tier
            when = _aware(pregame.provider_observed_at)
            mins = int((_aware(game.starts_at) - when).total_seconds() // 60) if game.starts_at and when else None
            tail = (f" Latest observation is {'in-play' if latest and latest.in_play else 'later'} and is retained separately."
                    if latest is not None and latest.id != pregame.id else "")
            if pregame.semantics_status == "SEMANTICS_MATCHED":
                return ProviderCoverage(state="QUOTED_REVIEWED",
                                        reason=f"Reviewed semantics; final pregame observation {mins} min before first pitch, tier {pregame.quality_tier}.{tail}", **common), prob
            return ProviderCoverage(
                state="QUOTED_UNREVIEWED",
                reason=f"Final pregame observation {mins} min before first pitch; market semantics await template approval — "
                       f"shown as an unreviewed research observation, not decision-eligible.{tail}", **common), prob
        snap = latest
        # A persisted observation is always shown, even if the provider flag
        # has since been turned off — the flag only governs new collection.
        if snap is not None:
            prob = self._probability_from_snapshot(snap, latest=latest, mapping=mapping, kind="IN_PLAY" if snap.in_play else "UNKNOWN")
            common["quality_tier"] = snap.quality_tier
            excluded_reason = self._structural_exclusion(game, snap, prob)
            if excluded_reason is None and not pregame:
                excluded_reason = "no valid pregame observation was captured before first pitch"
            if excluded_reason is not None:
                # A persisted quote that cannot be a pregame observation is
                # reported exactly once, in its own mutually exclusive state,
                # and contributes no price to the board.
                return ProviderCoverage(state="QUOTED_STRUCTURALLY_EXCLUDED",
                                        reason=f"Quote persisted but structurally excluded: {excluded_reason}.",
                                        **common), None
            if snap.semantics_status == "SEMANTICS_MATCHED":
                return ProviderCoverage(state="QUOTED_REVIEWED",
                                        reason=f"Reviewed market semantics; latest quote tier {snap.quality_tier}.", **common), prob
            return ProviderCoverage(
                state="QUOTED_UNREVIEWED",
                reason="Quote persisted but market semantics are not yet reviewed — shown as an unreviewed "
                       "research observation, not decision-eligible.", **common), prob
        if not enabled:
            return ProviderCoverage(state="PROVIDER_DISABLED", reason=f"{provider} is disabled by configuration.", **common), None
        if provider in (ctx.get("provider_failed") or set()) and mapping is None:
            return ProviderCoverage(state="PROVIDER_ERROR", reason=f"{provider}'s latest discovery run failed; no mapping could be attempted.", **common), None
        if mapping is None:
            return ProviderCoverage(state="NO_MAPPED_MARKET",
                                    reason=f"No {provider} market is mapped to this game (not listed, or not discovered).", **common), None
        if mapping.mapping_status in {"EXACT", "MANUAL_APPROVED"}:
            return ProviderCoverage(state="MAPPED_NOT_QUOTED", reason=self._not_quoted_reason(provider, listed, ctx["refresh"]), **common), None
        if mapping.mapping_status == "REJECTED":
            return ProviderCoverage(state="MAPPING_REJECTED", reason=mapping.reason or "Mapping rejected by review.", **common), None
        return ProviderCoverage(state="MAPPING_REVIEW_REQUIRED",
                                reason=mapping.reason or f"Game identity is {mapping.mapping_status}; human review required.", **common), None

    @staticmethod
    def _structural_exclusion(game: Game, snap: PredictionMarketSnapshot, prob: ProviderProbability) -> str | None:
        """Why a persisted quote is not a pregame observation (None when it is one)."""
        status = game.status.value.upper() if hasattr(game.status, "value") else str(game.status).upper()
        if status != "SCHEDULED":
            return f"game is {status}, not pregame"
        if snap.in_play:
            return "quote was captured in-play"
        if prob.midpoint is None:
            return "quote carries no usable midpoint"
        return None

    @staticmethod
    def _semantics_authority(mapping: PredictionMarketMapping | None) -> str | None:
        if mapping is None:
            return None
        reason = mapping.reason or ""
        if "[TEMPLATE:" in reason:
            return reason.split("[TEMPLATE:", 1)[1].split("]", 1)[0].join(("TEMPLATE:", ""))
        if mapping.decision_source == "MANUAL":
            return "MANUAL"
        return "NONE"

    def _not_quoted_reason(self, provider: str, listed: int, refresh: dict[str, Any]) -> str:
        skipped = (refresh.get("skipped") or {}).get(provider)
        if skipped:
            return f"{listed} contract(s) listed; the refresh job skipped {provider}: {skipped}."
        if not refresh:
            return f"{listed} contract(s) listed; no snapshot refresh job has completed yet."
        return (f"{listed} contract(s) listed; not yet quoted by the last refresh "
                f"({refresh.get('requests', 0)} requests, {refresh.get('errors', 0)} errors, "
                f"cap {self.settings.prediction_market_max_active_markets}).")

    @staticmethod
    def _coverage_counts(rows: list[MarketPulseRow]) -> dict[str, int]:
        # Mutually exclusive row states: every canonical game is counted in
        # exactly one of these, so they sum to canonical_games.
        states = ["QUOTED_REVIEWED", "QUOTED_UNREVIEWED", "QUOTED_STRUCTURALLY_EXCLUDED", "MAPPED_NOT_QUOTED",
                  "MAPPING_REVIEW_REQUIRED", "MAPPING_REJECTED", "NO_MAPPED_MARKET", "PROVIDER_DISABLED",
                  "PROVIDER_ERROR", "NO_PROVIDER_ENABLED"]
        out = {s: sum(1 for r in rows if r.coverage_state == s) for s in states}
        out["canonical_games"] = len(rows)
        out["exclusive_sum"] = sum(out[s] for s in states)
        # Disjoint GAME-level evidence states (headline authority).
        for s in ("REVIEWED_PREGAME", "PREGAME_UNREVIEWED", "EXCLUDED_IN_PLAY", "LISTED_NOT_QUOTED", "NO_MAPPED_MARKET_GAME", "PROVIDER_UNAVAILABLE"):
            out[s] = 0
        for r in rows:
            key = "NO_MAPPED_MARKET_GAME" if r.evidence_state == "NO_MAPPED_MARKET" else r.evidence_state
            out[key] += 1
        out["evidence_state_sum"] = sum(out[s] for s in ("REVIEWED_PREGAME", "PREGAME_UNREVIEWED", "EXCLUDED_IN_PLAY", "LISTED_NOT_QUOTED", "NO_MAPPED_MARKET_GAME", "PROVIDER_UNAVAILABLE"))
        # Overlapping measures (a row may appear in several): labelled as such by the client.
        out["quoted_any"] = out["QUOTED_REVIEWED"] + out["QUOTED_UNREVIEWED"] + out["QUOTED_STRUCTURALLY_EXCLUDED"]
        out["unreviewed_observations"] = sum(1 for r in rows if r.unreviewed_observation)
        out["decision_eligible"] = sum(1 for r in rows if r.decision_eligible_quote)
        out["consensus_available"] = sum(1 for r in rows if r.consensus_eligible)
        out["semantics_review_pending"] = sum(
            1 for r in rows if any(c.state == "QUOTED_UNREVIEWED" or c.semantics_status == "SEMANTICS_REVIEW_REQUIRED"
                                   for c in r.provider_coverage))
        return out

    @staticmethod
    def _coverage_by_provider(rows: list[MarketPulseRow]) -> dict[str, dict[str, int]]:
        """Per-provider coverage (overlapping across providers; audit detail only)."""
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            for c in r.provider_coverage:
                out.setdefault(c.provider, {}).setdefault(c.state, 0)
                out[c.provider][c.state] += 1
        return out

    @staticmethod
    def _scheduler_running() -> bool:
        from app.jobs.scheduler import scheduler_is_running

        return scheduler_is_running()

    @staticmethod
    def _next_run_time(job_id: str) -> datetime | None:
        from app.jobs.scheduler import scheduler_next_run_time

        return scheduler_next_run_time(job_id)

    @staticmethod
    def _scheduler_running_since() -> datetime | None:
        from app.jobs.scheduler import scheduler_started_at

        return scheduler_started_at()

    def _board_state(self, rows, providers: list[ProviderHealth], now: datetime) -> tuple[str, str]:
        """Exact, non-contradictory observer state machine.

        API_UNAVAILABLE is a frontend-only state (backend unreachable). The
        healthy-zero label is only earned by a successful discovery that
        actually inspected provider records.
        """
        s = self.settings
        if not s.prediction_market_observer_enabled:
            return ("OBSERVER_DISABLED",
                    "The backend is healthy but the Prediction Market Observer is disabled "
                    "(PREDICTION_MARKET_OBSERVER_ENABLED=false); no discovery or snapshots run.")
        polymarket_active = s.polymarket_enabled
        kalshi_active = s.kalshi_enabled and s.kalshi_rest_polling_enabled
        if not (polymarket_active or kalshi_active):
            return ("PROVIDERS_DISABLED",
                    "The observer is enabled but every provider is disabled "
                    "(POLYMARKET_ENABLED / KALSHI_ENABLED+KALSHI_REST_POLLING_ENABLED are off); nothing can be discovered.")
        eligible = [r for r in rows if r.decision_eligible_quote]
        if eligible:
            return ("ELIGIBLE_MARKETS_AVAILABLE",
                    f"{len(eligible)} of {len(rows)} canonical game(s) have a reviewed, decision-eligible quote.")
        # Same authority as the coverage strip: a row is an unreviewed research
        # observation iff its exclusive coverage state says so.
        observed = [r for r in rows if r.coverage_state == "QUOTED_UNREVIEWED"]
        excluded = [r for r in rows if r.coverage_state == "QUOTED_STRUCTURALLY_EXCLUDED"]
        if observed:
            suffix = (f" {len(excluded)} further quoted game(s) are structurally excluded (final, in-play or no usable price)"
                      " and are not observations." if excluded else "")
            return ("RESEARCH_OBSERVATIONS_UNREVIEWED",
                    f"{len(observed)} of {len(rows)} canonical game(s) have persisted quotes whose market semantics "
                    "are not yet reviewed. They are shown as unreviewed research observations; none is "
                    f"decision-eligible and no AISP–market gap is claimed from them.{suffix}")
        enabled = [p for p in providers if p.enabled]
        stale_cutoff = now - timedelta(minutes=3 * s.prediction_market_discovery_minutes)
        # Staleness only indicts the scheduler once it has been running long
        # enough to have fired a discovery — a fresh restart after downtime is
        # catching up, not degraded.
        started = self._scheduler_running_since()
        scheduler_owes_a_run = (
            self._scheduler_running()
            and started is not None
            and now - _aware(started) > timedelta(minutes=s.prediction_market_discovery_minutes)
        )
        degraded: list[tuple[ProviderHealth, str]] = []
        for p in enabled:
            if (p.last_job_status or "").lower() == "failed":
                degraded.append((p, f"last audited job failed: {p.last_error or 'see admin health'}"))
            elif p.last_success_at is not None and _aware(p.last_success_at) < stale_cutoff and scheduler_owes_a_run:
                degraded.append((p, f"last successful discovery is stale (before {stale_cutoff.isoformat(timespec='minutes')})"))
        if degraded:
            names = ", ".join(p.provider for p, _ in degraded)
            return ("PROVIDER_DEGRADED", f"Provider(s) {names} degraded — {degraded[0][1]}.")
        successes = [p for p in enabled if p.last_success_at is not None]
        if not successes:
            return ("DISCOVERY_PENDING",
                    "Providers are enabled but no discovery job has completed yet. "
                    "Counts appear after the first audited discovery run.")
        raw_total = sum(p.raw_markets_inspected or 0 for p in enabled)
        if raw_total == 0:
            return ("DISCOVERY_PENDING",
                    "The latest discovery succeeded but inspected zero provider records; the exact provider "
                    "response and query are recorded on the audited ingestion run. Healthy-zero is not claimed "
                    "without inspected records.")
        mapped = sum(p.mapped_markets for p in enabled)
        unresolved = sum(p.unresolved_markets for p in enabled)
        events = sum(p.events_inspected for p in enabled)
        return ("PROVIDERS_HEALTHY_ZERO_ELIGIBLE",
                f"Discovery succeeded and inspected {raw_total} raw provider market(s) "
                f"({events} events persisted; {mapped} mapped, {unresolved} unresolved), but none of the "
                f"{len(rows)} canonical game(s) for this date has a persisted, semantically comparable quote. "
                "This is a healthy zero-eligible state — no demo data is substituted.")

    def _excluded_summary(self, providers: list[ProviderHealth]) -> dict[str, int]:
        """Excluded Market Activity: discovery-time exclusions by reason plus
        current mapping/quality exclusions derived from persisted evidence."""
        summary: dict[str, int] = {}
        for p in providers:
            for reason, n in (p.excluded or {}).items():
                summary[reason] = summary.get(reason, 0) + int(n)
        summary["unresolved_mappings"] = sum(p.unresolved_markets for p in providers)
        mismatch = int(self.session.scalar(select(func.count()).select_from(PredictionMarketMapping).where(
            PredictionMarketMapping.semantics_status == "SEMANTICS_MISMATCH")) or 0)
        if mismatch:
            summary["rules_mismatch"] = mismatch
        # Latest snapshot per contract in the last 24h, reduced in SQL (the
        # previous ORM scan materialised every row of the window).
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        ranked = select(
            PredictionMarketSnapshot.quality_tier, PredictionMarketSnapshot.book_complete,
            func.row_number().over(
                partition_by=(PredictionMarketSnapshot.provider, PredictionMarketSnapshot.provider_market_id,
                              PredictionMarketSnapshot.provider_outcome_id),
                order_by=PredictionMarketSnapshot.provider_observed_at.desc(),
            ).label("rn"),
        ).where(
            PredictionMarketSnapshot.aisp_received_at >= cutoff,
            # LEGACY_PRE_AUTHORITY_OBSERVATION rows are not current book evidence.
            ~and_(PredictionMarketSnapshot.provider == "KALSHI", PredictionMarketSnapshot.canonical_game_id.is_(None),
                  PredictionMarketSnapshot.aisp_received_at < LEGACY_LISTING_AUTHORITY_CUTOFF),
        ).subquery()
        thin = int(self.session.scalar(select(func.count()).select_from(ranked).where(ranked.c.rn == 1, ranked.c.quality_tier == "THIN")) or 0)
        incomplete = int(self.session.scalar(select(func.count()).select_from(ranked).where(ranked.c.rn == 1, ranked.c.book_complete.is_(False))) or 0)
        # Legacy pre-authority observations: Kalshi listing quotes persisted for
        # unmapped (historical / settled) markets before the persister was
        # restricted to accepted mappings. Preserved for audit, classified
        # explicitly, and never counted as current pregame or book evidence.
        legacy = int(self.session.scalar(select(func.count()).select_from(PredictionMarketSnapshot).where(
            PredictionMarketSnapshot.provider == "KALSHI",
            PredictionMarketSnapshot.canonical_game_id.is_(None),
            PredictionMarketSnapshot.aisp_received_at < LEGACY_LISTING_AUTHORITY_CUTOFF,
        )) or 0)
        if legacy:
            summary["legacy_pre_authority_observations"] = legacy
        if thin:
            summary["thin_markets"] = thin
        if incomplete:
            summary["incomplete_order_books"] = incomplete
        return {k: v for k, v in summary.items() if v}

    def _observer_jobs(self) -> list[ObserverJobStatus]:
        """Registered observer jobs with honest cadence/run evidence (§ scheduler)."""
        from app.models import JobRun

        s = self.settings
        snapshot_cadence = s.prediction_market_snapshot_minutes
        defs = [
            ("discover-prediction-markets", s.prediction_market_discovery_minutes, "asp-pm-discover"),
            ("refresh-prediction-market-snapshots", snapshot_cadence, "asp-pm-refresh"),
            ("compute-prediction-market-consensus", max(snapshot_cadence, 5), "asp-pm-consensus"),
            ("evaluate-prediction-market-alerts", max(snapshot_cadence, 5), "asp-pm-alerts"),
            ("finalize-prediction-market-pregame", 5, "asp-pm-finalize-pregame"),
        ]
        running = self._scheduler_running() and s.prediction_market_observer_enabled
        out: list[ObserverJobStatus] = []
        for name, cadence, job_id in defs:
            last = self.session.scalar(select(JobRun).where(JobRun.job_name == name)
                                       .order_by(JobRun.started_at.desc()))
            last_success = self.session.scalar(select(JobRun).where(
                JobRun.job_name == name, JobRun.status == RunStatus.SUCCEEDED,
            ).order_by(JobRun.started_at.desc()))
            result = (last_success.result or {}) if last_success else {}
            # Only the live scheduler knows the true next fire time. A derived
            # last-run-plus-cadence value goes stale (into the past) the moment
            # a process restarts after downtime, so it is never projected.
            next_run = self._next_run_time(job_id) if running else None
            out.append(ObserverJobStatus(
                name=name,
                enabled=s.prediction_market_observer_enabled,
                cadence_minutes=cadence,
                scheduler_running=running,
                last_run_at=_aware(last.started_at) if last else None,
                last_status=last.status.value if last and last.status is not None else None,
                last_success_at=_aware(last_success.started_at) if last_success else None,
                last_error=last.error_message if last else None,
                next_run_at=next_run,
                records_inspected=_int_or_zero(result.get("markets_seen") or result.get("contracts") or result.get("requests")),
                records_persisted=_int_or_zero(result.get("records")),
            ))
        return out

    _COVERAGE_RANK = ["QUOTED_REVIEWED", "QUOTED_UNREVIEWED", "QUOTED_STRUCTURALLY_EXCLUDED", "MAPPED_NOT_QUOTED",
                      "MAPPING_REVIEW_REQUIRED", "MAPPING_REJECTED", "NO_MAPPED_MARKET", "PROVIDER_ERROR", "PROVIDER_DISABLED"]
    _EVIDENCE_STATE = {"QUOTED_REVIEWED": "REVIEWED_PREGAME", "QUOTED_UNREVIEWED": "PREGAME_UNREVIEWED",
                       "QUOTED_STRUCTURALLY_EXCLUDED": "EXCLUDED_IN_PLAY", "MAPPED_NOT_QUOTED": "LISTED_NOT_QUOTED",
                       "MAPPING_REVIEW_REQUIRED": "NO_MAPPED_MARKET", "MAPPING_REJECTED": "NO_MAPPED_MARKET",
                       "NO_MAPPED_MARKET": "NO_MAPPED_MARKET", "PROVIDER_ERROR": "PROVIDER_UNAVAILABLE",
                       "PROVIDER_DISABLED": "PROVIDER_UNAVAILABLE", "NO_PROVIDER_ENABLED": "PROVIDER_UNAVAILABLE"}

    def _row(self, game: Game, ctx: dict[str, Any] | None = None) -> MarketPulseRow | None:
        if ctx is None:
            ctx = self._coverage_context([game.id])
        teams = ctx["teams"].get(game.id)
        if teams:
            (home_team, home_abbr), (away_team, away_abbr) = teams.get(True, (None, None)), teams.get(False, (None, None))
        else:
            home_team, home_abbr, away_team, away_abbr = self._teams(game)
        mapping = ctx["latest_mapping"].get(game.id)
        coverage: list[ProviderCoverage] = []
        providers: dict[str, ProviderProbability] = {}
        for provider in ("POLYMARKET", "KALSHI"):
            cov, prob = self._provider_coverage(game, provider, ctx)
            coverage.append(cov)
            if prob is not None:
                providers[provider] = prob
        poly = providers.get("POLYMARKET")
        kalshi = providers.get("KALSHI")
        poly_home = poly.midpoint if poly else None
        kalshi_home = kalshi.midpoint if kalshi else None
        aisp_home, aisp_fresh = self._aisp_home_probability(game)
        book_home = self._sportsbook_home_probability(game)
        consensus = ctx["consensus"].get(game.id) if "consensus" in ctx else self._latest_consensus(game.id)
        alert_count = ctx["alerts"].get(game.id, 0) if "alerts" in ctx else self._alert_count(game.id)
        league_code = ctx["league"].get(game.league_id) if "league" in ctx else None
        # The AISP–market gap is claimed only from reviewed quotes. Unreviewed
        # observations are reported on a separate, clearly labelled field.
        reviewed = [p.midpoint for p in providers.values()
                    if p.semantics_status == "SEMANTICS_MATCHED" and p.midpoint is not None]
        unreviewed = [p.midpoint for p in providers.values()
                      if p.semantics_status != "SEMANTICS_MATCHED" and p.midpoint is not None]
        pred_avg = _avg(reviewed)
        pred_avg_unreviewed = _avg(unreviewed) if not reviewed else None
        if all(c.state == "PROVIDER_DISABLED" for c in coverage):
            coverage_state = "NO_PROVIDER_ENABLED"
        else:
            coverage_state = next(s for s in self._COVERAGE_RANK if any(c.state == s for c in coverage))
        eligible = any(p.decision_eligible for p in providers.values())
        note = None
        if coverage_state == "QUOTED_UNREVIEWED":
            note = "UNREVIEWED RESEARCH OBSERVATION — NOT DECISION ELIGIBLE"
        elif coverage_state == "QUOTED_STRUCTURALLY_EXCLUDED":
            note = "QUOTED — STRUCTURALLY EXCLUDED (NOT PREGAME / IN-PLAY / NO USABLE PRICE)"
        elif coverage_state == "MAPPED_NOT_QUOTED":
            note = "LISTED — NOT YET QUOTED"
        elif coverage_state == "NO_MAPPED_MARKET":
            note = "NO PROVIDER MARKET MAPPED"
        league = league_code or self._league(game)
        reviewed_probs = [p for p in reviewed if p is not None]
        if book_home is not None:
            reviewed_probs.append(book_home)
        row_min = min(reviewed_probs) if reviewed_probs else None
        row_max = max(reviewed_probs) if reviewed_probs else None
        row = MarketPulseRow(
            game_id=str(game.id), sport={"MLB": "MLB", "NFL": "NFL", "NBA": "NBA", "NHL": "NHL"}.get(league, league), league=league,
            home_team=home_team, away_team=away_team, home_abbrev=home_abbr, away_abbrev=away_abbr,
            starts_at=_aware(game.starts_at), status=game.status.value.upper(),
            aisp_home_probability=aisp_home, aisp_forecast_fresh=aisp_fresh,
            aisp_comparison_engine_id=getattr(self, "_comparison_engine", None),
            forecast_selection=self._forecast_selection(game),
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
            alert_count=alert_count,
            coverage_state=coverage_state, provider_coverage=coverage,
            decision_eligible_quote=eligible,
            unreviewed_observation=coverage_state == "QUOTED_UNREVIEWED" and bool(unreviewed) and not reviewed,
            diff_aisp_prediction_unreviewed=_diff(aisp_home, pred_avg_unreviewed),
            display_note=note,
            evidence_state=self._EVIDENCE_STATE.get(coverage_state, "NO_MAPPED_MARKET"),
            reviewed_probability_min=row_min, reviewed_probability_max=row_max,
            market_disagreement=(round(row_max - row_min, 4) if row_min is not None and row_max is not None else None),
        )
        self._movement(game.id, row)
        return row

    _DECISION_ELIGIBLE_TIERS = {"ROBUST", "USABLE"}

    def _probability_from_snapshot(self, snap: PredictionMarketSnapshot, *, latest: PredictionMarketSnapshot | None = None,
                                   mapping: PredictionMarketMapping | None = None, kind: str = "UNKNOWN") -> ProviderProbability:
        """HOME win probability view of one persisted snapshot (P(home))."""
        obs = _aware(snap.provider_observed_at)
        latest_obs = _aware(latest.provider_observed_at) if latest is not None else obs
        return ProviderProbability(
            observation_kind=kind if kind != "UNKNOWN" else ("IN_PLAY" if snap.in_play else ("PREGAME" if snap.pregame_eligible else "UNKNOWN")),
            seconds_before_start=snap.seconds_to_game, timeline_cut=snap.timeline_cut,
            latest_observed_at=latest_obs, latest_in_play=bool(latest.in_play) if latest is not None else bool(snap.in_play),
            mapping_authority=(mapping.decision_source if mapping else None),
            semantics_authority=self._semantics_authority(mapping),
            freshness_seconds=(datetime.now(UTC) - obs).total_seconds() if obs else None,
            provider=snap.provider, midpoint=self._to_home(snap.midpoint, snap.selection),
            microprice=self._to_home(snap.microprice, snap.selection),
            # Raw, unoriented, and now accompanied by the identity of the
            # contract they belong to. The midpoint above is turned to the home
            # side; these are not, and a consumer that guessed the side from
            # the numbers would get it wrong every time the venue happens to
            # list the away contract -- which both venues do.
            best_bid=snap.best_bid, best_ask=snap.best_ask, last_trade=snap.last_trade,
            **self._quoted_contract(snap),
            bid_ask_spread=snap.bid_ask_spread, volume=snap.volume, open_interest=snap.open_interest,
            top_depth=(snap.top_bid_size or 0) + (snap.top_ask_size or 0) if snap.top_bid_size is not None else None,
            quality_tier=snap.quality_tier,
            quote_age_seconds=(snap.quality_components or {}).get("quote_age"),
            mapping_status=snap.mapping_status, semantics_status=snap.semantics_status,
            in_play=snap.in_play, observed_at=_aware(snap.provider_observed_at),
            decision_eligible=(snap.semantics_status == "SEMANTICS_MATCHED"
                               and snap.quality_tier in self._DECISION_ELIGIBLE_TIERS),
        )

    def _provider_probabilities(self, game_id: Any) -> dict[str, ProviderProbability]:
        """Latest moneyline HOME win probability per provider (P(home))."""
        return {provider: self._probability_from_snapshot(snap)
                for provider, snap in self._provider_snapshots(game_id).items()}

    def _quoted_contract(self, snap: PredictionMarketSnapshot) -> dict[str, Any]:
        """Identity of the contract a raw quote belongs to, from stored metadata.

        The side is read, never inferred. ``PredictionMarketContract`` already
        records the venue's own outcome label, market and outcome identifiers
        and question, and the snapshot records the canonical selection those
        were normalized to. Nothing here looks at a price.

        A contract whose stored metadata cannot establish a canonical side is
        reported as UNVERIFIED with whatever native label exists, so the caller
        can show the raw quote and withhold only the normalized comparison.
        """

        contract = self.session.get(PredictionMarketContract, snap.contract_id)
        selection = (snap.selection or "").upper()
        resolved = selection in {
            CanonicalSelection.HOME.value, CanonicalSelection.AWAY.value,
        }
        label = contract.outcome_label if contract else None
        question = contract.market_question if contract else None
        # Kalshi encodes the settlement side in the outcome id itself
        # (``…-DET:YES``); Polymarket's token id is opaque, so the meaning comes
        # from the outcome label the venue published for that token.
        outcome_id = snap.provider_outcome_id or (contract.provider_outcome_id if contract else None)
        meaning = None
        if label:
            side = str(outcome_id).rsplit(":", 1)[-1].upper() if outcome_id and ":" in str(outcome_id) else "YES"
            meaning = f"{side} settles if {label} wins" if side in {"YES", "NO"} else None
        return {
            "quoted_selection": selection if resolved else None,
            "quoted_outcome_label": label,
            "quoted_market_id": snap.provider_market_id or (contract.provider_market_id if contract else None),
            "quoted_outcome_id": outcome_id,
            "quoted_market_question": question,
            "quoted_settlement_meaning": meaning,
            "quoted_side_source": "CONTRACT_METADATA" if (resolved and label) else "UNVERIFIED",
        }

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
            timeline=self._timeline(game), timeline_markers=self._timeline_markers(game),
            order_books=self._order_books(game.id),
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
            if snap.midpoint is None:
                continue  # no two-sided price: no point, never interpolated
            key = _aware(snap.provider_observed_at).isoformat(timespec="minutes")
            pt = points.setdefault(key, TimelinePoint(time=key))
            home = self._to_home(snap.midpoint, snap.selection)
            if snap.provider == "POLYMARKET":
                pt.polymarket = _pct(home)
                pt.polymarket_pregame = bool(snap.pregame_eligible and not snap.in_play)
            elif snap.provider == "KALSHI":
                pt.kalshi = _pct(home)
                pt.kalshi_pregame = bool(snap.pregame_eligible and not snap.in_play)
        # ASP forecast history (each published version is its own point).
        from app.models import Forecast

        for fc in self.session.scalars(select(Forecast).where(Forecast.game_id == game.id).order_by(Forecast.generated_at)):
            if fc.home_win_probability is None:
                continue
            key = _aware(fc.generated_at).isoformat(timespec="minutes")
            points.setdefault(key, TimelinePoint(time=key)).aisp = _pct(float(fc.home_win_probability))
        for key, prob in self._sportsbook_history(game).items():
            points.setdefault(key, TimelinePoint(time=key)).sportsbook = _pct(prob)
        for cons in self.session.scalars(select(PredictionMarketConsensusSnapshot).where(
            PredictionMarketConsensusSnapshot.canonical_game_id == game.id,
            PredictionMarketConsensusSnapshot.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value,
            PredictionMarketConsensusSnapshot.selection == CanonicalSelection.HOME.value,
        ).order_by(PredictionMarketConsensusSnapshot.computed_at)):
            if cons.median_probability is None or not cons.eligible:
                continue
            key = _aware(cons.computed_at).isoformat(timespec="minutes")
            points.setdefault(key, TimelinePoint(time=key)).consensus = _pct(cons.median_probability)
        # A point exists only where at least one source actually observed something.
        return [points[k] for k in sorted(points)
                if any(v is not None for v in (points[k].aisp, points[k].sportsbook, points[k].polymarket, points[k].kalshi, points[k].consensus))]

    def _sportsbook_history(self, game: Game) -> dict[str, float]:
        """Per-observation sportsbook no-vig HOME probability (median across
        books at each observation minute). Only minutes where a book posted
        both sides are emitted — nothing is interpolated."""
        from collections import defaultdict
        from statistics import median

        from app.markets.odds import no_vig_probabilities
        from app.models import Market, MarketOutcome, MarketType, OddsSnapshot

        home_gt = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(True)))
        away_gt = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(False)))
        if not home_gt or not away_gt:
            return {}
        rows = self.session.execute(
            select(OddsSnapshot.observed_at, OddsSnapshot.sportsbook_id, MarketOutcome.participant_team_id, OddsSnapshot.decimal_odds)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .where(Market.game_id == game.id, Market.market_type == MarketType.MONEYLINE)
            .order_by(OddsSnapshot.observed_at)
        ).all()
        by_minute: dict[str, dict[Any, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
        for observed_at, book_id, team_id, dec in rows:
            key = _aware(observed_at).isoformat(timespec="minutes")
            side = "home" if team_id == home_gt.team_id else ("away" if team_id == away_gt.team_id else None)
            if side and dec:
                by_minute[key][book_id][side] = float(dec)
        out: dict[str, float] = {}
        for key, books in by_minute.items():
            probs = []
            for sides in books.values():
                if "home" in sides and "away" in sides:
                    try:
                        res = no_vig_probabilities({"home": sides["home"], "away": sides["away"]}, american=False)
                        p = res.probabilities.get("home") if hasattr(res, "probabilities") else (res.get("home") if isinstance(res, dict) else None)
                        if p is not None:
                            probs.append(float(p))
                    except Exception:
                        continue
            if probs:
                out[key] = round(median(probs), 6)
        return out

    def _timeline_markers(self, game: Game) -> list[TimelineMarker]:
        from app.services.shadow_experiment_service import FINAL_PREGAME_CUTOFF_MINUTES

        start = _aware(game.starts_at)
        if start is None:
            return []
        cutoff = start - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
        return [TimelineMarker(key="decision_cutoff", label=f"Decision cutoff (T-{FINAL_PREGAME_CUTOFF_MINUTES}m)", time=cutoff.isoformat(timespec="minutes")),
                TimelineMarker(key="first_pitch", label="First pitch", time=start.isoformat(timespec="minutes"))]

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
        last_run = self.session.scalar(select(IngestionRun).where(
            IngestionRun.source == provider,
            IngestionRun.job_name == "discover-prediction-markets",
        ).order_by(IngestionRun.completed_at.desc().nulls_last()))
        last_success = self.session.scalar(select(IngestionRun).where(
            IngestionRun.source == provider,
            IngestionRun.job_name == "discover-prediction-markets",
            IngestionRun.status == RunStatus.SUCCEEDED,
        ).order_by(IngestionRun.completed_at.desc().nulls_last()))
        raw_markets, excluded = self._discovery_cycle_stats(provider, last_success)
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
            last_success_at=_aware(last_success.completed_at) if last_success else None,
            last_snapshot_at=_aware(last_snap), subscribed_markets=int(subscribed),
            last_error=last_run.error_message if last_run else None,
            status="ACTIVE" if last_snap else ("ARMED" if enabled else "IDLE"),
            events_inspected=int(events), markets_inspected=int(subscribed),
            raw_markets_inspected=raw_markets, excluded=excluded,
            eligible_game_markets=int(mapped), mapped_markets=int(mapped), unresolved_markets=int(unresolved),
            last_job_status=last_status,
        )

    def _discovery_cycle_stats(
        self, provider: str, last_success: IngestionRun | None
    ) -> tuple[int | None, dict[str, int]]:
        """Raw inspected/excluded counts for the latest discovery cycle.

        One discovery cycle records one audited run per governed sport; runs
        completed within ten minutes of the newest success are the same cycle.
        """
        if last_success is None or last_success.completed_at is None:
            return None, {}
        window_start = _aware(last_success.completed_at) - timedelta(minutes=10)
        runs = list(self.session.scalars(select(IngestionRun).where(
            IngestionRun.source == provider,
            IngestionRun.job_name == "discover-prediction-markets",
            IngestionRun.status == RunStatus.SUCCEEDED,
            IngestionRun.completed_at >= window_start,
        )))
        raw_total = 0
        excluded: dict[str, int] = {}
        saw_meta = False
        for run in runs:
            meta = run.source_metadata or {}
            if "markets_seen" in meta:
                saw_meta = True
                raw_total += int(meta.get("markets_seen") or 0)
            for reason, n in (meta.get("excluded") or {}).items():
                excluded[reason] = excluded.get(reason, 0) + int(n)
        return (raw_total if saw_meta else None), excluded

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
        """The model probability this board compares against.

        Uses the same selector every other surface uses, and applies the same
        rule the slate applies: the selected forecast is shown whenever it has
        a number, with its condition stated beside it. An earlier version here
        required AVAILABLE, which meant an invalidated game showed V18's 60.2%
        on the slate and Gen 1's 58.9% on this board for the same game --
        two engines under one heading, which is the disagreement the shared
        selector exists to remove. A limitation is a caption, not a reason to
        substitute a different model's number.

        When the selected forecast has no probability at all -- the controlling
        engine published nothing for this game -- there is no comparison. This
        used to fall through to Gen 1 with a label. A labelled substitution is
        still a substitution: it put one model's number under a heading a reader
        reads as the other's, and it made this board carry a number for a game
        the slate showed as unavailable. The row now reports no model
        probability, and Gen 1 stays reachable as an explicit comparison in the
        selection payload.
        """

        from app.services.forecast_selection import ForecastSelector

        selection = ForecastSelector(self.session).select(game)
        primary = selection.primary
        if primary is not None and primary.home_win_probability is not None:
            published = primary.published_utc
            fresh = published is not None and (datetime.now(UTC) - published) <= timedelta(hours=24)
            self._comparison_engine = primary.engine_id
            return round(float(primary.home_win_probability), 6), fresh

        self._comparison_engine = None
        return None, False

    def _forecast_selection(self, game: Game) -> dict[str, Any]:
        """The shared selection, plus the engine that supplied the number.

        `displayed_engine_id` is the same field the slate emits, so one client
        rule names the engine on every surface. It is taken from the attribution
        `_aisp_home_probability` just recorded, which is why that call comes
        first when a row is built.
        """

        from app.services.forecast_selection import ForecastSelector

        payload = ForecastSelector(self.session).select(game).as_dict()
        payload["displayed_engine_id"] = getattr(self, "_comparison_engine", None)
        return payload

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
        if not isinstance(ctx, dict):
            return None
        # The shared rule: a probability exists only with a complete two-sided
        # observation behind it and a value strictly inside (0, 1). Without one
        # this returns None, which keeps the sportsbook out of the displayed
        # value, the reviewed range, the cross-market dispersion and every diff.
        probability = observed_probability(
            ctx.get("home_probability"),
            has_two_sided_quote=bool(ctx.get("has_moneyline")),
        )
        return round(probability, 6) if probability is not None else None

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


def _int_or_zero(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _diff(a: float | None, b: float | None) -> float | None:
    return round(a - b, 4) if a is not None and b is not None else None


def _pct(prob: float | None) -> float | None:
    return round(prob * 100, 2) if prob is not None else None


__all__ = ["PredictionMarketReadService"]
