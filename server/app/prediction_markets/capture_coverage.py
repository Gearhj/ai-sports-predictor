"""Pregame snapshot coverage: one disjoint classification per
(game, provider, milestone) for the canonical slate.

Read-only, DB-only (zero provider calls). Every cell is exactly one of the
certification states below, so the per-provider counts reconcile to
games x milestones. A milestone whose capture window has not opened is
NOT_DUE — a future milestone is never reported as missed.

Milestones: FIRST_DISCOVERED, T_MINUS_6_HOURS, T_MINUS_2_HOURS,
T_MINUS_30_MINUTES, T_MINUS_5_MINUTES, FINAL_VALID_PREGAME.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import local_day_bounds
from app.models import (
    Game, GameStateObservation, GameStatus, GameTeam, IngestionRun, PredictionMarketContract, PredictionMarketMapping,
    PredictionMarketSnapshot, Team,
)
from app.prediction_markets.contracts import CanonicalMarketType, CanonicalSelection, TIMELINE_CUT_OFFSETS_MINUTES, TimelineCut
from app.schemas.prediction_market import PregameCaptureCell, PregameCaptureCoverage, PregameCaptureProvider, PregameCaptureRow

MILESTONES: tuple[str, ...] = (
    "FIRST_DISCOVERED", "T_MINUS_6_HOURS", "T_MINUS_2_HOURS", "T_MINUS_30_MINUTES", "T_MINUS_5_MINUTES", "FINAL_VALID_PREGAME",
)
CUT_MILESTONES: dict[str, TimelineCut] = {
    "T_MINUS_6_HOURS": TimelineCut.T_MINUS_6_HOURS, "T_MINUS_2_HOURS": TimelineCut.T_MINUS_2_HOURS,
    "T_MINUS_30_MINUTES": TimelineCut.T_MINUS_30_MINUTES, "T_MINUS_5_MINUTES": TimelineCut.T_MINUS_5_MINUTES,
}
STATES: tuple[str, ...] = (
    "CAPTURED", "NOT_DUE", "NO_LIQUIDITY", "MARKET_NOT_OPEN", "PROVIDER_UNAVAILABLE", "MISSED_SCHEDULER_RUN",
    "FAILED_PROVIDER_CALL", "MAPPING_UNAVAILABLE", "STRUCTURALLY_INELIGIBLE", "NOT_APPLICABLE",
)
PROVIDERS: tuple[str, ...] = ("KALSHI", "POLYMARKET")
ACCEPTED = {"EXACT", "MANUAL_APPROVED"}
CAPTURE_JOB = "capture-pregame-cuts"
CAPTURE_CADENCE_MINUTES = 5
FINAL_WINDOW_MINUTES = 30
NOT_OPEN_STATUSES = {"initialized", "unopened", "inactive", "not_open", "pending"}


def _dh_label(value: tuple[str | None, int | None] | None) -> str | None:
    if not value:
        return None
    dh, num = value
    if num and num > 0 and (dh or "").upper() not in {"", "N", "NONE"}:
        return f"DH game {num}"
    return None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class PregameCaptureCoverageService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- public ------------------------------------------------------------
    def coverage(self, event_date: date, *, now: datetime | None = None) -> PregameCaptureCoverage:
        now = _aware(now) or datetime.now(UTC)
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        games = list(self.session.scalars(select(Game).where(Game.starts_at >= start, Game.starts_at < end).order_by(Game.starts_at)))
        live = [g for g in games if g.source_mode == "LIVE"]
        games = live or (games if self.settings.demo_mode else [])
        game_ids = [g.id for g in games]
        ctx = self._context(game_ids)
        enabled = {"POLYMARKET": bool(self.settings.polymarket_enabled),
                   "KALSHI": bool(self.settings.kalshi_enabled and self.settings.kalshi_rest_polling_enabled)}
        rows: list[PregameCaptureRow] = []
        counts: dict[str, dict[str, dict[str, int]]] = {p: {m: {s: 0 for s in STATES} for m in MILESTONES} for p in PROVIDERS}
        for game in games:
            teams = ctx["teams"].get(game.id, {})
            home = teams.get(True, ("?", "?"))
            away = teams.get(False, ("?", "?"))
            providers: dict[str, PregameCaptureProvider] = {}
            for provider in PROVIDERS:
                cells = self._cells(game, provider, enabled[provider], ctx, now)
                for c in cells:
                    counts[provider][c.milestone][c.state] += 1
                mapping = ctx["mapping"].get((game.id, provider))
                providers[provider] = PregameCaptureProvider(
                    mapping_status=mapping.mapping_status if mapping else None,
                    provider_event_id=mapping.provider_event_id if mapping else None,
                    cells=cells,
                )
            rows.append(PregameCaptureRow(
                game_id=str(game.id), label=f"{away[1]} @ {home[1]}", home=home[0], away=away[0],
                starts_at=_aware(game.starts_at), game_status=str(game.status.value if hasattr(game.status, "value") else game.status),
                doubleheader_status=_dh_label(ctx["doubleheader"].get(game.id)), providers=providers,
            ))
        totals = {p: {m: sum(counts[p][m].values()) for m in MILESTONES} for p in PROVIDERS}
        return PregameCaptureCoverage(
            date=event_date, generated_at=now, milestones=list(MILESTONES), providers=list(PROVIDERS), states=list(STATES),
            canonical_games=len(games), rows=rows, counts=counts, totals=totals,
            capture_cadence_minutes=CAPTURE_CADENCE_MINUTES,
            disclosure="Read-only audit of persisted observations. Zero provider calls. Future milestones are NOT_DUE, never missed.",
        )

    # -- prefetch ------------------------------------------------------------
    def _context(self, game_ids: list[Any]) -> dict[str, Any]:
        ctx: dict[str, Any] = {"teams": {}, "mapping": {}, "pregame": {}, "latest": {}, "contract_status": {}, "runs": [],
                               "refresh_runs": [], "doubleheader": {}}
        if not game_ids:
            return ctx
        for gid, dh, num in self.session.execute(
            select(GameStateObservation.game_id, GameStateObservation.doubleheader_status, GameStateObservation.game_number)
            .where(GameStateObservation.game_id.in_(game_ids)).order_by(GameStateObservation.observed_at.desc())).all():
            ctx["doubleheader"].setdefault(gid, (dh, num))
        for gid, is_home, name, abbrev in self.session.execute(
            select(GameTeam.game_id, GameTeam.is_home, Team.name, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id.in_(game_ids))).all():
            ctx["teams"].setdefault(gid, {})[bool(is_home)] = (name, abbrev)
        for m in self.session.scalars(select(PredictionMarketMapping).where(PredictionMarketMapping.canonical_game_id.in_(game_ids))
                                      .order_by(PredictionMarketMapping.decided_at.desc())):
            ctx["mapping"].setdefault((m.canonical_game_id, m.provider), m)
        event_ids = {m.provider_event_id for m in ctx["mapping"].values()}
        if event_ids:
            for provider, event_id, status_, version in self.session.execute(
                select(PredictionMarketContract.provider, PredictionMarketContract.provider_event_id,
                       PredictionMarketContract.market_status, PredictionMarketContract.version)
                .where(PredictionMarketContract.provider_event_id.in_(event_ids),
                       PredictionMarketContract.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value)
                .order_by(PredictionMarketContract.version.desc())).all():
                ctx["contract_status"].setdefault((provider, event_id), (status_ or "").lower())
        # Every pregame-eligible moneyline observation (HOME side first) and the
        # latest observation per (game, provider), whatever its state.
        snaps = list(self.session.scalars(select(PredictionMarketSnapshot).where(
            PredictionMarketSnapshot.canonical_game_id.in_(game_ids),
            PredictionMarketSnapshot.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE.value,
        ).order_by(PredictionMarketSnapshot.provider_observed_at)))
        for s in snaps:
            key = (s.canonical_game_id, s.provider)
            latest = ctx["latest"].get(key)
            if latest is None or _aware(s.provider_observed_at) >= _aware(latest.provider_observed_at):
                ctx["latest"][key] = s
            if s.pregame_eligible and not s.in_play:
                ctx["pregame"].setdefault(key, []).append(s)
        ctx["runs"] = list(self.session.scalars(select(IngestionRun).where(
            IngestionRun.source == "PREDICTION_MARKET", IngestionRun.job_name == CAPTURE_JOB,
        ).order_by(IngestionRun.started_at)))
        ctx["refresh_runs"] = list(self.session.scalars(select(IngestionRun).where(
            IngestionRun.source == "PREDICTION_MARKET", IngestionRun.job_name == "refresh-prediction-market-snapshots",
        ).order_by(IngestionRun.started_at)))
        return ctx

    # -- classification --------------------------------------------------------
    def _cells(self, game: Game, provider: str, enabled: bool, ctx: dict[str, Any], now: datetime) -> list[PregameCaptureCell]:
        start = _aware(game.starts_at)
        mapping = ctx["mapping"].get((game.id, provider))
        pregame: list[PredictionMarketSnapshot] = ctx["pregame"].get((game.id, provider), [])
        home_side = [s for s in pregame if s.selection == CanonicalSelection.HOME.value]
        quoted = [s for s in (home_side or pregame) if s.midpoint is not None]
        latest = ctx["latest"].get((game.id, provider))
        status_text = str(game.status.value if hasattr(game.status, "value") else game.status)
        ineligible = status_text in {GameStatus.POSTPONED.value, GameStatus.CANCELED.value}
        cells: list[PregameCaptureCell] = []
        for milestone in MILESTONES:
            cells.append(self._cell(milestone, provider=provider, enabled=enabled, start=start, now=now, mapping=mapping,
                                    quoted=quoted, pregame=(home_side or pregame), latest=latest, ineligible=ineligible,
                                    ctx=ctx, status_text=status_text))
        return cells

    def _cell(self, milestone: str, *, provider: str, enabled: bool, start: datetime | None, now: datetime, mapping, quoted,
              pregame, latest, ineligible: bool, ctx: dict[str, Any], status_text: str) -> PregameCaptureCell:
        def cell(state: str, reason: str, snap: PredictionMarketSnapshot | None = None) -> PregameCaptureCell:
            return PregameCaptureCell(
                milestone=milestone, state=state, reason=reason,
                snapshot_id=str(snap.id) if snap else None,
                observed_at=_aware(snap.provider_observed_at) if snap else None,
                received_at=_aware(snap.aisp_received_at) if snap else None,
                timeline_cut=snap.timeline_cut if snap else None,
                midpoint=snap.midpoint if snap else None,
                best_bid=snap.best_bid if snap else None, best_ask=snap.best_ask if snap else None,
                seconds_before_start=int((start - _aware(snap.provider_observed_at)).total_seconds()) if (snap and start) else None,
                observed_before_start=bool(snap and start and _aware(snap.provider_observed_at) < start) if snap else None,
                late_ingestion=("LATE_INGESTION" in (snap.quality_flags or [])) if snap else None,
            )

        if not enabled:
            return cell("NOT_APPLICABLE", f"{provider} observer disabled in configuration")
        if start is None:
            return cell("STRUCTURALLY_INELIGIBLE", "canonical game has no start time")
        accepted = mapping is not None and mapping.mapping_status in ACCEPTED

        # --- milestone-specific "captured" evidence, which is fact regardless of later state
        if milestone == "FIRST_DISCOVERED":
            if quoted:
                return cell("CAPTURED", "earliest two-sided pregame observation", quoted[0])
        elif milestone in CUT_MILESTONES:
            cut = CUT_MILESTONES[milestone].value
            hit = [s for s in quoted if s.timeline_cut == cut]
            if hit:
                return cell("CAPTURED", f"observation labelled {cut} by its source timestamp", hit[-1])
        else:  # FINAL_VALID_PREGAME
            if quoted and now >= start:
                return cell("CAPTURED", "final two-sided observation with source time before first pitch", quoted[-1])

        if ineligible:
            return cell("STRUCTURALLY_INELIGIBLE", f"canonical game status is {status_text}")
        if not accepted:
            why = "no accepted provider mapping" if mapping is None else f"mapping status {mapping.mapping_status}"
            return cell("MAPPING_UNAVAILABLE", why)

        # --- window timing
        if milestone == "FIRST_DISCOVERED":
            # Due as soon as an accepted mapping exists, and never later than first pitch.
            window_open = min(_aware(mapping.decided_at) or start, start)
        elif milestone in CUT_MILESTONES:
            window_open = start - timedelta(minutes=TIMELINE_CUT_OFFSETS_MINUTES[CUT_MILESTONES[milestone]])
        else:
            window_open = start - timedelta(minutes=FINAL_WINDOW_MINUTES)
        if milestone == "FINAL_VALID_PREGAME" and now < start:
            prov = quoted[-1] if quoted else None
            return cell("NOT_DUE", "first pitch not reached; provisional final pregame shown if any", prov)
        if now < window_open:
            return cell("NOT_DUE", f"capture window opens at {window_open.isoformat()}")

        window_close = min(now, start)
        window_hit = [s for s in pregame if window_open <= _aware(s.provider_observed_at) < window_close]
        if any(s.midpoint is None for s in window_hit) and not any(s.midpoint is not None for s in window_hit):
            return cell("NO_LIQUIDITY", "observation in window carries no two-sided book", window_hit[-1])
        if milestone == "FIRST_DISCOVERED" and latest is not None and latest.midpoint is None:
            return cell("NO_LIQUIDITY", "listed contract has no two-sided book yet", latest)
        contract_status = ctx["contract_status"].get((provider, mapping.provider_event_id), "")
        if contract_status in NOT_OPEN_STATUSES:
            return cell("MARKET_NOT_OPEN", f"provider market status '{contract_status}'")

        runs = [r for r in ctx["runs"] if window_open <= _aware(r.started_at) < window_close + timedelta(minutes=CAPTURE_CADENCE_MINUTES)]
        if milestone == "FIRST_DISCOVERED":
            runs = [r for r in ctx["refresh_runs"] if window_open <= _aware(r.started_at) < window_close]
        elapsed = (window_close - window_open).total_seconds() / 60
        if not runs:
            if now < start and elapsed < 2 * CAPTURE_CADENCE_MINUTES:
                return cell("NOT_DUE", f"capture window opened at {window_open.isoformat()}; next scheduler run pending")
            return cell("MISSED_SCHEDULER_RUN", f"no {'refresh' if milestone == 'FIRST_DISCOVERED' else CAPTURE_JOB} run persisted inside the capture window")
        errored = [r for r in runs if int((r.source_metadata or {}).get("errors") or 0) > 0
                   or int(((r.source_metadata or {}).get("per_provider") or {}).get(provider, {}).get("errors") or 0) > 0]
        if errored:
            return cell("FAILED_PROVIDER_CALL", f"{len(errored)} of {len(runs)} scheduler runs in the window recorded provider errors")
        if now < start and elapsed < 2 * CAPTURE_CADENCE_MINUTES:
            return cell("NOT_DUE", "capture window open; scheduler run pending")
        return cell("PROVIDER_UNAVAILABLE", f"{len(runs)} scheduler runs in the window persisted no observation for this market")


__all__ = ["PregameCaptureCoverageService", "MILESTONES", "STATES", "PROVIDERS"]
