"""Request-scoped, set-based read context for the experiment qualification
funnels (V1 strict control and V2.1 practical challenger).

Why: the funnels evaluated one game at a time and every helper on the path
(`game_card`, `_daily_opportunities`, the V2.1 evaluator, the evidence state
machines, `_matchup`, `_sport`) issued its own SQL per game / per team /
per side — 587 statements for V1 and 732 for V2.1 on a 16-game slate,
linear in slate size.

What: one `FunnelReadContext` built with ~25 bounded statements (IN-lists of
the slate's canonical game ids, window/DISTINCT-ON reductions, grouped
loads). It is attached to the SQLAlchemy `Session.info` for the duration of
one request; the existing helpers consult it first and fall back to their
original queries when it is absent, so every other caller (jobs, pages that
do not attach a context) is behaviourally untouched.

Temporal authority is preserved exactly: the context stores complete
per-(game, team) observation histories ordered by the governed source time
(`available_as_of`, then id as a stable tie-breaker); the evidence reducers
apply the same `<= reference` / `<= cutoff` filters they applied in SQL.
Nothing here writes, calls a provider, or changes a threshold.

Read consistency: on PostgreSQL the request runs inside one REPEATABLE READ
transaction (a single MVCC snapshot) so concurrent scheduler writes cannot
produce a response stitched from different instants. The transaction holds
no locks and is released with the session.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.data_sources.provider_registry import GOVERNED_SOURCES, governed_sources
from app.models import (
    AvailabilityEvent,
    ExperimentDecisionPhase,
    Forecast,
    ForecastComponent,
    ForecastStatus,
    ForecastTrack,
    Game,
    GameFeatureSnapshot,
    GameRefreshState,
    GameTeam,
    League,
    LineupObservation,
    Market,
    MarketOutcome,
    OddsSnapshot,
    Pitcher,
    Player,
    ShadowExperiment,
    ShadowExperimentDecision,
    ShadowRescore,
    Sportsbook,
    StarterObservation,
    Team,
    TeamFeature,
    Venue,
)

SESSION_INFO_KEY = "funnel_prefetch"
READ_MODEL_VERSION = "AISP_FUNNEL_SET_BASED_READ_MODEL_V1"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass
class FunnelReadContext:
    """Everything one funnel request needs, keyed by immutable identities."""

    generated_at: datetime
    game_ids: list[Any]
    games_by_id: dict[Any, Game] = field(default_factory=dict)
    # (GameTeam, Team) pairs per game, home first.
    sides_by_game: dict[Any, list[tuple[GameTeam, Team]]] = field(default_factory=dict)
    league_code_by_id: dict[Any, str] = field(default_factory=dict)
    venues_by_id: dict[Any, Venue] = field(default_factory=dict)
    # Newest valid forecast per game (same rule as ReadService._latest_forecast).
    latest_forecast_by_game: dict[Any, Forecast | None] = field(default_factory=dict)
    components_by_forecast: dict[Any, list[ForecastComponent]] = field(default_factory=dict)
    rescores_by_game: dict[Any, list[ShadowRescore]] = field(default_factory=dict)  # newest first
    refresh_state_by_game: dict[Any, GameRefreshState | None] = field(default_factory=dict)
    latest_feature_snapshot_by_game: dict[Any, GameFeatureSnapshot | None] = field(default_factory=dict)
    feature_snapshots_by_id: dict[Any, GameFeatureSnapshot] = field(default_factory=dict)
    # Complete histories, ordered by (available_as_of, id) ascending.
    lineup_rows_by_game_team: dict[tuple[Any, Any], list[LineupObservation]] = field(default_factory=dict)
    starter_rows_by_game_team: dict[tuple[Any, Any], list[StarterObservation]] = field(default_factory=dict)
    players_by_id: dict[Any, Player] = field(default_factory=dict)
    # Every player id the context looked up, including ids with no player row,
    # so a known-missing pitcher never triggers a per-game fallback query.
    player_ids_loaded: set[Any] = field(default_factory=set)
    pitcher_by_player_id: dict[Any, Pitcher] = field(default_factory=dict)
    team_features_by_team: dict[Any, list[TeamFeature]] = field(default_factory=dict)  # newest first
    # Availability events per game, newest received first (same order as the read model).
    events_by_game: dict[Any, list[AvailabilityEvent]] = field(default_factory=dict)
    # Latest quote batch per (market, sportsbook) per game — same reduction as
    # ReadService._latest_market_snapshots(cutoff=None), newest observed first.
    market_rows_by_game: dict[Any, list[tuple[Market, MarketOutcome, OddsSnapshot, Sportsbook]]] = field(default_factory=dict)
    active_experiments: list[ShadowExperiment] = field(default_factory=list)
    # Latest FINAL_PREGAME decision at or before each game's cutoff, per experiment.
    final_decision_by_experiment_game: dict[tuple[Any, Any], ShadowExperimentDecision] = field(default_factory=dict)
    # Forecast selection (app.services.forecast_selection), bulk-loaded so the
    # selector issues no per-game SQL. ``selection_loaded`` distinguishes
    # "loaded and empty" from "not loaded".
    selection_loaded: bool = False
    v18_effective_at: datetime | None = None
    v18_rows_by_game: dict[Any, list[Any]] = field(default_factory=dict)
    latest_decision_by_game: dict[Any, ShadowExperimentDecision] = field(default_factory=dict)
    gen1_forecast_by_game: dict[Any, Forecast | None] = field(default_factory=dict)
    model_versions_by_id: dict[Any, Any] = field(default_factory=dict)
    # Request-local memo for ledger-derived values (e.g. current bankroll).
    memo: dict[str, Any] = field(default_factory=dict)
    statement_count: int = 0
    evidence_watermark: datetime | None = None

    # -- accessors used by the read helpers --------------------------------
    def experiment(self, policy: str) -> ShadowExperiment | None:
        for row in self.active_experiments:
            if row.decision_policy == policy:
                return row
        return None

    def lineup_rows(self, game_id: Any, team_id: Any) -> list[LineupObservation]:
        return self.lineup_rows_by_game_team.get((game_id, team_id), [])

    def starter_rows(self, game_id: Any, team_id: Any) -> list[StarterObservation]:
        return self.starter_rows_by_game_team.get((game_id, team_id), [])

    def events(self, game_id: Any) -> list[AvailabilityEvent]:
        return self.events_by_game.get(game_id, [])

    def game_team(self, game_id: Any, is_home: bool) -> GameTeam | None:
        for side, _team in self.sides_by_game.get(game_id, []):
            if bool(side.is_home) == is_home:
                return side
        return None


def attached_context(session: Session) -> FunnelReadContext | None:
    """The request's prefetched context, or None when a caller did not attach one."""
    return session.info.get(SESSION_INFO_KEY)


def attach_context(session: Session, ctx: FunnelReadContext) -> None:
    session.info[SESSION_INFO_KEY] = ctx


def detach_context(session: Session) -> None:
    session.info.pop(SESSION_INFO_KEY, None)


def begin_read_snapshot(session: Session) -> None:
    """Pin one MVCC snapshot for the request (PostgreSQL REPEATABLE READ).

    Must run before the transaction's first statement. Ignored on dialects
    without transactional snapshots (SQLite test fixtures)."""
    try:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql" and not session.in_transaction():
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    except Exception:  # never let snapshot pinning break a read
        pass


def build_funnel_read_context(
    session: Session, settings: Settings, games: list[Game], *, now: datetime | None = None,
) -> FunnelReadContext:
    """Bulk-load every family the funnels touch with a bounded statement count."""
    now = _aware(now) or datetime.now(UTC)
    ctx = FunnelReadContext(generated_at=now, game_ids=[g.id for g in games])
    ctx.games_by_id = {g.id: g for g in games}
    ids = ctx.game_ids
    if not ids:
        return ctx
    watermark: list[datetime] = []
    source_modes = {g.source_mode for g in games}

    # 1. sides (GameTeam + Team), home first
    for side, team in session.execute(
        select(GameTeam, Team).join(Team, Team.id == GameTeam.team_id)
        .where(GameTeam.game_id.in_(ids)).order_by(GameTeam.game_id, GameTeam.is_home.desc())
    ):
        ctx.sides_by_game.setdefault(side.game_id, []).append((side, team))
    ctx.statement_count += 1

    # 2. leagues, 3. venues
    league_ids = {g.league_id for g in games if g.league_id}
    if league_ids:
        ctx.league_code_by_id = dict(session.execute(select(League.id, League.code).where(League.id.in_(league_ids))).all())
        ctx.statement_count += 1
    venue_ids = {g.venue_id for g in games if g.venue_id}
    if venue_ids:
        ctx.venues_by_id = {v.id: v for v in session.scalars(select(Venue).where(Venue.id.in_(venue_ids)))}
        ctx.statement_count += 1

    # 4. rescores (newest completed first) → after-forecast ids + latest per game
    for row in session.scalars(select(ShadowRescore).where(ShadowRescore.game_id.in_(ids))
                               .order_by(ShadowRescore.rescore_completed_at.desc(), ShadowRescore.id.desc())):
        ctx.rescores_by_game.setdefault(row.game_id, []).append(row)
    ctx.statement_count += 1
    after_ids_by_game = {gid: {r.after_forecast_id for r in rows} for gid, rows in ctx.rescores_by_game.items()}

    # 5. newest valid forecast per game: same predicate as _latest_forecast
    #    (game's source_mode, CURRENT_DEVELOPMENT track, PUBLISHED or a rescore
    #    output), highest version wins.
    candidates: dict[Any, Forecast] = {}
    for f in session.scalars(select(Forecast).where(
        Forecast.game_id.in_(ids),
        Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
        Forecast.source_mode.in_(source_modes),
    ).order_by(Forecast.game_id, Forecast.version.desc())):
        game = ctx.games_by_id.get(f.game_id)
        if game is None or f.source_mode != game.source_mode:
            continue
        if f.status != ForecastStatus.PUBLISHED and f.id not in after_ids_by_game.get(f.game_id, set()):
            continue
        current = candidates.get(f.game_id)
        if current is None or f.version > current.version:
            candidates[f.game_id] = f
    ctx.statement_count += 1
    ctx.latest_forecast_by_game = {gid: candidates.get(gid) for gid in ids}
    forecast_ids = [f.id for f in candidates.values()]
    for f in candidates.values():
        watermark.append(_aware(f.generated_at))

    # 6. forecast components for the latest forecasts
    if forecast_ids:
        for comp in session.scalars(select(ForecastComponent).where(ForecastComponent.forecast_id.in_(forecast_ids))):
            ctx.components_by_forecast.setdefault(comp.forecast_id, []).append(comp)
        ctx.statement_count += 1

    # 7. latest refresh state per (game, source_mode) — same ordering as _latest_refresh_state
    rn = func.row_number().over(
        partition_by=(GameRefreshState.game_id, GameRefreshState.source_mode),
        order_by=(GameRefreshState.available_as_of.desc(), GameRefreshState.evaluated_at.desc()),
    ).label("rn")
    ranked = select(GameRefreshState.id, rn).where(GameRefreshState.game_id.in_(ids)).subquery()
    for state in session.scalars(select(GameRefreshState).where(GameRefreshState.id.in_(select(ranked.c.id).where(ranked.c.rn == 1)))):
        game = ctx.games_by_id.get(state.game_id)
        if game is not None and state.source_mode == game.source_mode:
            ctx.refresh_state_by_game[state.game_id] = state
    ctx.statement_count += 1

    # 8. feature snapshots: those referenced by forecasts / refresh states, plus
    #    the latest per (game, source_mode) with _latest_feature_snapshot's ordering
    referenced = {f.feature_snapshot_id for f in candidates.values() if f.feature_snapshot_id}
    referenced |= {s.last_feature_snapshot_id for s in ctx.refresh_state_by_game.values() if s and s.last_feature_snapshot_id}
    rn2 = func.row_number().over(
        partition_by=(GameFeatureSnapshot.game_id, GameFeatureSnapshot.source_mode),
        order_by=(GameFeatureSnapshot.data_as_of.desc(), GameFeatureSnapshot.generated_at.desc(),
                  GameFeatureSnapshot.created_at.desc(), GameFeatureSnapshot.id.desc()),
    ).label("rn")
    ranked2 = select(GameFeatureSnapshot.id, rn2).where(GameFeatureSnapshot.game_id.in_(ids)).subquery()
    latest_ids = select(ranked2.c.id).where(ranked2.c.rn == 1)
    snap_filter = GameFeatureSnapshot.id.in_(latest_ids)
    if referenced:
        snap_filter = snap_filter | GameFeatureSnapshot.id.in_(list(referenced))
    latest_snapshot_ids = set(session.scalars(latest_ids))
    ctx.statement_count += 1
    for snap in session.scalars(select(GameFeatureSnapshot).where(snap_filter)):
        ctx.feature_snapshots_by_id[snap.id] = snap
        game = ctx.games_by_id.get(snap.game_id)
        if snap.id in latest_snapshot_ids and game is not None and snap.source_mode == game.source_mode:
            ctx.latest_feature_snapshot_by_game[snap.game_id] = snap
    ctx.statement_count += 1

    # 9. lineup observations (complete histories), 10. starter observations
    for row in session.scalars(select(LineupObservation).where(LineupObservation.game_id.in_(ids))
                               .order_by(LineupObservation.available_as_of, LineupObservation.id)):
        ctx.lineup_rows_by_game_team.setdefault((row.game_id, row.team_id), []).append(row)
        watermark.append(_aware(row.available_as_of))
    ctx.statement_count += 1
    pitcher_ids: set[Any] = set()
    for row in session.scalars(select(StarterObservation).where(StarterObservation.game_id.in_(ids))
                               .order_by(StarterObservation.available_as_of, StarterObservation.id)):
        ctx.starter_rows_by_game_team.setdefault((row.game_id, row.team_id), []).append(row)
        watermark.append(_aware(row.available_as_of))
        for pid in (row.probable_pitcher_id, row.confirmed_pitcher_id):
            if pid:
                pitcher_ids.add(pid)
    ctx.statement_count += 1
    for sides in ctx.sides_by_game.values():
        for side, _ in sides:
            if side.probable_pitcher_id:
                pitcher_ids.add(side.probable_pitcher_id)

    # 11. players, 12. pitchers
    ctx.player_ids_loaded = set(pitcher_ids)
    if pitcher_ids:
        ctx.players_by_id = {p.id: p for p in session.scalars(select(Player).where(Player.id.in_(list(pitcher_ids))))}
        ctx.statement_count += 1
        ctx.pitcher_by_player_id = {p.player_id: p for p in session.scalars(select(Pitcher).where(Pitcher.player_id.in_(list(pitcher_ids))))}
        ctx.statement_count += 1

    # 13. team features — only needed when a game has no feature snapshot
    #     (the card then reads the latest team feature at its cutoff).
    unsnapshotted = [gid for gid in ids if ctx.latest_feature_snapshot_by_game.get(gid) is None
                     and not (ctx.latest_forecast_by_game.get(gid) and ctx.latest_forecast_by_game[gid].feature_snapshot_id)]
    if unsnapshotted:
        team_ids = [t.id for gid in unsnapshotted for _, t in ctx.sides_by_game.get(gid, [])]
        if team_ids:
            # Record every loaded team (even with zero rows) so the read helper
            # never falls back to a per-team query for a known-empty team.
            for tid in team_ids:
                ctx.team_features_by_team.setdefault(tid, [])
            for tf in session.scalars(select(TeamFeature).where(TeamFeature.team_id.in_(team_ids))
                                      .order_by(TeamFeature.available_as_of.desc(), TeamFeature.id.desc())):
                ctx.team_features_by_team.setdefault(tf.team_id, []).append(tf)
            ctx.statement_count += 1

    # 14. availability events, newest received first (read-model order)
    for ev in session.scalars(select(AvailabilityEvent).where(AvailabilityEvent.game_id.in_(ids))
                              .order_by(AvailabilityEvent.received_at.desc(), AvailabilityEvent.id.desc())):
        ctx.events_by_game.setdefault(ev.game_id, []).append(ev)
        watermark.append(_aware(ev.received_at))
    ctx.statement_count += 1

    # 15. latest quote batch per (market, sportsbook) for every slate game —
    #     the bulk form of _latest_market_snapshots(cutoff=None).
    modes = {gid: ctx.games_by_id[gid].source_mode for gid in ids}
    latest_per_market_book = (
        select(Market.id.label("market_id"), OddsSnapshot.sportsbook_id.label("sportsbook_id"),
               func.max(OddsSnapshot.observed_at).label("latest_observed_at"))
        .join(MarketOutcome, MarketOutcome.market_id == Market.id)
        .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
        .join(Game, Game.id == Market.game_id)
        .where(Market.game_id.in_(ids), OddsSnapshot.source_mode == Game.source_mode,
               # Governed pricing reads governed providers only; see provider_registry.
           # This path is the LIVE slate read model, so the governed set applies
           # directly rather than through the per-mode authority helper.
               OddsSnapshot.source.in_(governed_sources()))
        .group_by(Market.id, OddsSnapshot.sportsbook_id).subquery("latest_market_book")
    )
    query = (
        select(Market, MarketOutcome, OddsSnapshot, Sportsbook)
        .join(MarketOutcome, MarketOutcome.market_id == Market.id)
        .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .join(Game, Game.id == Market.game_id)
        .join(latest_per_market_book, and_(
            latest_per_market_book.c.market_id == Market.id,
            latest_per_market_book.c.sportsbook_id == OddsSnapshot.sportsbook_id,
            latest_per_market_book.c.latest_observed_at == OddsSnapshot.observed_at,
        ))
        .where(Market.game_id.in_(ids), OddsSnapshot.source_mode == Game.source_mode,
               # Governed pricing reads governed providers only; see provider_registry.
           # This path is the LIVE slate read model, so the governed set applies
           # directly rather than through the per-mode authority helper.
               OddsSnapshot.source.in_(governed_sources()))
        # Same deterministic order as ReadService._latest_market_snapshots.
        .order_by(OddsSnapshot.observed_at.desc(), Market.id, MarketOutcome.id, Sportsbook.id)
    )
    seen: set[tuple[Any, Any, Any]] = set()
    for row in session.execute(query):
        market, outcome, snapshot, book = row
        if modes.get(market.game_id) != snapshot.source_mode:
            continue
        key = (market.game_id, outcome.id, book.id)
        if key in seen:
            continue
        seen.add(key)
        ctx.market_rows_by_game.setdefault(market.game_id, []).append((market, outcome, snapshot, book))
        watermark.append(_aware(snapshot.observed_at))
    ctx.statement_count += 1

    # 16. active experiments (V1 control, registered challengers)
    ctx.active_experiments = list(session.scalars(
        select(ShadowExperiment).where(ShadowExperiment.is_active.is_(True)).order_by(ShadowExperiment.version)
    ))
    ctx.statement_count += 1

    # 17. latest FINAL_PREGAME decision at/before each game's cutoff, per experiment
    #     (only consumed post-cutoff by the V2 view; loaded once for the slate).
    from app.services.shadow_experiment_service import FINAL_PREGAME_CUTOFF_MINUTES

    if ctx.active_experiments:
        exp_ids = [e.id for e in ctx.active_experiments]
        cutoff_expr = Game.starts_at - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
        rn3 = func.row_number().over(
            partition_by=(ShadowExperimentDecision.experiment_id, ShadowExperimentDecision.game_id),
            order_by=(ShadowExperimentDecision.recommended_at.desc(), ShadowExperimentDecision.id.desc()),
        ).label("rn")
        ranked3 = (
            select(ShadowExperimentDecision.id, rn3)
            .join(Game, Game.id == ShadowExperimentDecision.game_id)
            .where(
                ShadowExperimentDecision.experiment_id.in_(exp_ids),
                ShadowExperimentDecision.game_id.in_(ids),
                ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME,
                ShadowExperimentDecision.recommended_at <= cutoff_expr,
            ).subquery()
        )
        for d in session.scalars(select(ShadowExperimentDecision).where(
            ShadowExperimentDecision.id.in_(select(ranked3.c.id).where(ranked3.c.rn == 1))
        )):
            ctx.final_decision_by_experiment_game[(d.experiment_id, d.game_id)] = d
        ctx.statement_count += 1

    # 18. ledger-derived current bankroll per active experiment (one settlement
    #     scan each), so no per-game stake computation touches the database.
    if ctx.active_experiments:
        from app.services.shadow_experiment_service import ShadowExperimentService

        service = ShadowExperimentService(session, settings)
        for experiment in ctx.active_experiments:
            ctx.memo[f"bankroll:{experiment.id}"] = service.current_bankroll(experiment)
            ctx.statement_count += 1

    # 19-23. forecast selection families, same rules as ForecastSelector.
    _load_selection(session, ctx)

    ctx.evidence_watermark = max((w for w in watermark if w is not None), default=None)
    return ctx


def _load_selection(session: Session, ctx: FunnelReadContext) -> None:
    """Bulk form of ForecastSelector's per-game reads (at most 5 statements)."""

    from app.models import ModelVersion, ShadowForecast
    from app.models.entities import ModelChampionAuthority
    from app.services.forecast_selection import V18_ENGINE

    ids = ctx.game_ids
    authority = session.scalar(
        select(ModelChampionAuthority).where(ModelChampionAuthority.engine_id == V18_ENGINE)
        .order_by(ModelChampionAuthority.effective_at.desc()).limit(1))
    ctx.v18_effective_at = _aware(authority.effective_at) if authority else None
    ctx.statement_count += 1

    for row in session.scalars(
        select(ShadowForecast)
        .where(ShadowForecast.canonical_game_id.in_(ids), ShadowForecast.engine_id == V18_ENGINE)
        .order_by(ShadowForecast.canonical_game_id, ShadowForecast.snapshot_version, ShadowForecast.created_at)
    ):
        ctx.v18_rows_by_game.setdefault(row.canonical_game_id, []).append(row)
    ctx.statement_count += 1

    rn = func.row_number().over(
        partition_by=ShadowExperimentDecision.game_id,
        order_by=(ShadowExperimentDecision.recommended_at.desc(), ShadowExperimentDecision.id.desc()),
    ).label("rn")
    ranked = (select(ShadowExperimentDecision.id, rn)
              .where(ShadowExperimentDecision.game_id.in_(ids)).subquery())
    for decision in session.scalars(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.id.in_(select(ranked.c.id).where(ranked.c.rn == 1))
    )):
        ctx.latest_decision_by_game[decision.game_id] = decision
    ctx.statement_count += 1

    published: dict[Any, Forecast] = {}
    anything: dict[Any, Forecast] = {}
    for forecast in session.scalars(
        select(Forecast).where(Forecast.game_id.in_(ids))
        .order_by(Forecast.game_id, Forecast.generated_at.desc(), Forecast.id)
    ):
        anything.setdefault(forecast.game_id, forecast)
        if forecast.status == ForecastStatus.PUBLISHED:
            published.setdefault(forecast.game_id, forecast)
    ctx.statement_count += 1
    ctx.gen1_forecast_by_game = {gid: published.get(gid) or anything.get(gid) for gid in ids}

    version_ids = {f.model_version_id for f in ctx.gen1_forecast_by_game.values() if f and f.model_version_id}
    if version_ids:
        ctx.model_versions_by_id = {
            v.id: v for v in session.scalars(select(ModelVersion).where(ModelVersion.id.in_(version_ids)))}
        ctx.statement_count += 1
    ctx.selection_loaded = True


__all__ = [
    "FunnelReadContext", "READ_MODEL_VERSION", "SESSION_INFO_KEY",
    "attach_context", "attached_context", "begin_read_snapshot", "build_funnel_read_context", "detach_context",
]
