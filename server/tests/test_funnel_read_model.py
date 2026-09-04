"""Experiment funnels — set-based read model contracts.

Query complexity (bounded, constant in slate size), legacy/set-based
equivalence, temporal safety of the prefetched evidence, ledger safety and
zero side effects. Fixtures are rollback-isolated SQLite rows; no provider is
called; nothing here creates a decision, position or settlement.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, func, select

from app.models import (
    AvailabilityEvent,
    Game,
    GameStatus,
    GameTeam,
    IngestionRun,
    League,
    LineupObservation,
    ModelVersion,
    OfficialAspPosition,
    PositionSettlement,
    ShadowExperiment,
    ShadowExperimentDecision,
    Sport,
    StarterObservation,
    Team,
)
from app.services.evidence_states import (
    FRESHNESS_FRESH,
    LINEUP_SOURCE_NOT_YET_PUBLISHED,
    LineupContext,
    lineup_evidence,
    starter_evidence,
)
from app.services.funnel_read_context import (
    READ_MODEL_VERSION,
    attach_context,
    attached_context,
    build_funnel_read_context,
    detach_context,
)
from app.services.qualification_service import QualificationService
from app.services.read_service import ReadService

QUERY_BUDGET = 40
STARTS = datetime(2026, 9, 4, 23, 10, tzinfo=UTC)
P1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
P2 = uuid.UUID("22222222-2222-2222-2222-222222222222")


# -- fixtures ----------------------------------------------------------------

class QueryCounter:
    def __init__(self, session):
        self.engine = session.get_bind()
        self.count = 0
        self.statements: list[str] = []

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hook)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hook)

    def _hook(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1
        self.statements.append(statement)


def _league(session):
    sport = session.scalar(select(Sport).where(Sport.code == "BASEBALL")) or Sport(code="BASEBALL", name="Baseball", active=True)
    session.add(sport); session.flush()
    league = session.scalar(select(League).where(League.code == "MLB")) or League(sport_id=sport.id, code="MLB", name="MLB", active=True)
    session.add(league); session.flush()
    return league


def _team(session, league, abbr):
    row = session.scalar(select(Team).where(Team.league_id == league.id, Team.abbreviation == abbr))
    if row is None:
        row = Team(league_id=league.id, name=f"Team {abbr}", abbreviation=abbr, city=abbr, active=True)
        session.add(row); session.flush()
    return row


def _slate(session, n: int, *, starts=STARTS):
    """n LIVE games on one slate with two probable-starter observations per side
    and a lineup for the home side; no forecast (the funnels handle the
    'awaiting first forecast' path without any per-game SQL)."""
    league = _league(session)
    games = []
    for i in range(n):
        home = _team(session, league, f"H{i:02d}")
        away = _team(session, league, f"A{i:02d}")
        game = Game(league_id=league.id, starts_at=starts + timedelta(minutes=i), status=GameStatus.SCHEDULED, source_mode="LIVE")
        session.add(game); session.flush()
        session.add_all([GameTeam(game_id=game.id, team_id=home.id, is_home=True), GameTeam(game_id=game.id, team_id=away.id, is_home=False)])
        for team, pitcher in ((home, P1), (away, P2)):
            for m in (90, 40):
                at = starts - timedelta(minutes=m)
                session.add(StarterObservation(
                    game_id=game.id, team_id=team.id, probable_pitcher_id=pitcher, confirmation_state="PROBABLE",
                    observed_at=at, available_as_of=at, source="MLB_STATS_API", source_mode="LIVE",
                    source_record_id=f"{game.id}:{team.id}:{m}", payload_checksum=f"cs-{game.id}-{team.id}-{m}",
                ))
        at = starts - timedelta(minutes=35)
        session.add(LineupObservation(
            game_id=game.id, team_id=home.id, lineup_status="CONFIRMED", is_complete=True, observed_at=at,
            available_as_of=at, source="MLB_STATS_API", source_mode="LIVE", source_record_id=f"{game.id}:{home.id}",
            payload_checksum=f"lc-{game.id}-{home.id}",
        ))
        games.append(game)
    session.commit()
    return games


def _strip(payload: dict) -> dict:
    out = json.loads(json.dumps(payload, default=str))
    out.pop("read_model", None)
    return out


def _funnel_counts(session, settings, event_date, *, prefetch: bool, now=None):
    qs = QualificationService(session, settings)
    with QueryCounter(session) as c1:
        v1 = qs.funnel(event_date, prefetch=prefetch)
    with QueryCounter(session) as c2:
        v2 = qs.funnel_v2(event_date, now=now, prefetch=prefetch)
    return v1, c1, v2, c2


EVENT_DATE = STARTS.astimezone(ZoneInfo("America/New_York")).date()
NOW = STARTS - timedelta(hours=2)


# -- QUERY COMPLEXITY (1–5) --------------------------------------------------

@pytest.mark.parametrize("n", [1, 5, 16, 30])
def test_funnels_stay_within_the_query_budget(session, settings, n):
    s = settings.model_copy(update={"demo_mode": False})
    _slate(session, n)
    v1, c1, v2, c2 = _funnel_counts(session, s, EVENT_DATE, prefetch=True, now=NOW)
    assert len(v1["games"]) == n == len(v2["games"])
    assert c1.count <= QUERY_BUDGET, f"V1 {c1.count} statements for {n} games"           # (1)
    assert c2.count <= QUERY_BUDGET, f"V2.1 {c2.count} statements for {n} games"         # (2)
    assert v1["read_model"]["version"] == READ_MODEL_VERSION == v2["read_model"]["version"]


def test_query_count_is_constant_in_slate_size(session, settings):
    s = settings.model_copy(update={"demo_mode": False})
    counts = {}
    for n in (1, 30):
        # fresh slate per size: distinct dates so games don't accumulate
        starts = STARTS + timedelta(days=n)
        _slate(session, n, starts=starts)
        d = starts.astimezone(ZoneInfo("America/New_York")).date()
        _, c1, _, c2 = _funnel_counts(session, s, d, prefetch=True, now=starts - timedelta(hours=2))
        counts[n] = (c1.count, c2.count)
    assert abs(counts[30][0] - counts[1][0]) <= 5 and abs(counts[30][1] - counts[1][1]) <= 5, counts   # (3)


def test_no_sql_inside_the_per_game_loop(session, settings, monkeypatch):
    """(4)(5) With the context attached, walking every game through the card,
    opportunity, evaluator and evidence helpers issues zero statements."""
    s = settings.model_copy(update={"demo_mode": False})
    games = _slate(session, 6)
    read = ReadService(session, s)
    ctx = build_funnel_read_context(session, s, games, now=NOW)
    attach_context(session, ctx)
    try:
        with QueryCounter(session) as c:
            cards = [read.game_card(g) for g in games]
            forecasts = [read._latest_forecast(g.id) for g in games]
            for g in games:
                home = ctx.game_team(g.id, True); away = ctx.game_team(g.id, False)
                starter_evidence(session, g, home.team_id, cutoff=STARTS, forecast_generated_at=NOW, as_of=NOW)
                lineup_evidence(session, g, home.team_id, away.team_id, cutoff=STARTS, forecast_generated_at=NOW,
                                context=LineupContext(market_is_moneyline=True, dependency_allows_pending=True, pregame=True), as_of=NOW)
            json.dumps([c.model_dump(mode="json") for c in cards])   # serialization triggers no lazy loads
        assert c.count == 0, c.statements[:3]
        assert len(cards) == 6 and all(f is None for f in forecasts)
    finally:
        detach_context(session)
    assert attached_context(session) is None


# -- EQUIVALENCE (6–12) ------------------------------------------------------

def test_legacy_and_set_based_funnels_are_equivalent(session, settings):
    s = settings.model_copy(update={"demo_mode": False})
    _slate(session, 7)
    v1_old, _, v2_old, _ = _funnel_counts(session, s, EVENT_DATE, prefetch=False, now=NOW)
    v1_new, _, v2_new, _ = _funnel_counts(session, s, EVENT_DATE, prefetch=True, now=NOW)
    assert _strip(v1_old) == _strip(v1_new)                                     # (6, 8, 9, 10)
    assert _strip(v2_old) == _strip(v2_new)                                     # (7, 8, 9, 10)
    assert [g["game_id"] for g in v1_new["games"]] == [g["game_id"] for g in v1_old["games"]]
    assert v2_new["policy"] == v2_old["policy"] == "AISP_PRACTICAL_PREGAME_POLICY_V2_1"   # (11)
    assert v2_new["starter_evidence_reconciliation"] == v2_old["starter_evidence_reconciliation"]
    assert v1_old["read_model"]["prefetched"] is False and v1_new["read_model"]["prefetched"] is True


def test_comparison_metrics_unchanged_by_read_model(session, settings):
    s = settings.model_copy(update={"demo_mode": False})
    _slate(session, 3)
    before = ReadService(session, s).experiment_comparison()
    QualificationService(session, s).funnel_v2(EVENT_DATE, now=NOW)
    after = ReadService(session, s).experiment_comparison()
    assert before == after                                                       # (12)


# -- TEMPORAL SAFETY (13–19) -------------------------------------------------

def test_prefetched_evidence_respects_reference_and_source_time(session, settings):
    s = settings.model_copy(update={"demo_mode": False})
    (game,) = _slate(session, 1)
    home = session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(True)))
    away = session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(False)))
    early = STARTS - timedelta(hours=3)   # before every fixture observation
    # A future-dated starter row (persisted now, source time after the reference)
    late = StarterObservation(
        game_id=game.id, team_id=home.team_id, probable_pitcher_id=P2, confirmation_state="PROBABLE",
        observed_at=STARTS - timedelta(minutes=5), available_as_of=STARTS - timedelta(minutes=5), source="MLB_STATS_API",
        source_mode="LIVE", source_record_id="late", payload_checksum="late",
    )
    session.add(late); session.commit()
    ctx = build_funnel_read_context(session, s, [game], now=early)
    attach_context(session, ctx)
    try:
        with QueryCounter(session) as c:
            ev_early = starter_evidence(session, game, home.team_id, cutoff=STARTS - timedelta(minutes=15), forecast_generated_at=early, as_of=early)
            ref = STARTS - timedelta(minutes=30)   # after both fixture observations, before the future-dated row
            ev_ref = starter_evidence(session, game, home.team_id, cutoff=STARTS - timedelta(minutes=15), forecast_generated_at=ref, as_of=ref)
            lu = lineup_evidence(session, game, home.team_id, away.team_id, cutoff=STARTS - timedelta(minutes=15), forecast_generated_at=early,
                                 context=LineupContext(market_is_moneyline=True, dependency_allows_pending=True, pregame=True), as_of=early)
        assert c.count == 0
        assert ev_early.state == "MISSING"                                       # (13) nothing available yet
        assert ev_ref.pitcher_id == str(P1) and ev_ref.observation_count == 2     # (15/16) the late P2 row is excluded at the reference
        assert ev_ref.freshness == FRESHNESS_FRESH
        assert lu.home_source_state == LINEUP_SOURCE_NOT_YET_PUBLISHED             # (14) lineup published later is invisible
    finally:
        detach_context(session)
    # (17) persisted-at time is irrelevant: rows are ordered by available_as_of, id.
    rows = ctx.starter_rows(game.id, home.team_id)
    ts = [(r.available_as_of if r.available_as_of.tzinfo else r.available_as_of.replace(tzinfo=UTC)) for r in rows]
    assert ts == sorted(ts) and len(ts) == 3


def test_doubleheader_games_are_distinct_in_the_context(session, settings):
    s = settings.model_copy(update={"demo_mode": False})
    league = _league(session)
    h, a = _team(session, league, "DH1"), _team(session, league, "DH2")
    games = []
    for offset in (0, 5):
        g = Game(league_id=league.id, starts_at=STARTS + timedelta(hours=offset), status=GameStatus.SCHEDULED, source_mode="LIVE")
        session.add(g); session.flush()
        session.add_all([GameTeam(game_id=g.id, team_id=h.id, is_home=True), GameTeam(game_id=g.id, team_id=a.id, is_home=False)])
        games.append(g)
    at = STARTS - timedelta(minutes=30)
    session.add(StarterObservation(game_id=games[0].id, team_id=h.id, probable_pitcher_id=P1, confirmation_state="PROBABLE",
                                   observed_at=at, available_as_of=at, source="MLB_STATS_API", source_mode="LIVE",
                                   source_record_id="g1", payload_checksum="g1"))
    session.commit()
    ctx = build_funnel_read_context(session, s, games, now=NOW)
    assert len(ctx.starter_rows(games[0].id, h.id)) == 1 and ctx.starter_rows(games[1].id, h.id) == []   # (18)
    assert ctx.game_team(games[1].id, True).team_id == h.id


def test_snapshot_pinning_is_requested_only_on_postgresql(session, settings):
    """(19) On PostgreSQL the request pins REPEATABLE READ before its first
    statement; the SQLite fixture must simply pass through unchanged."""
    from app.services.funnel_read_context import begin_read_snapshot

    begin_read_snapshot(session)
    assert session.get_bind().dialect.name == "sqlite"
    s = settings.model_copy(update={"demo_mode": False})
    _slate(session, 2)
    assert len(QualificationService(session, s).funnel_v2(EVENT_DATE, now=NOW)["games"]) == 2


# -- LEDGER SAFETY + SIDE EFFECTS (20–30) -----------------------------------

def test_page_reads_create_no_writes_and_no_provider_calls(session, settings, monkeypatch):
    import httpx

    def boom(*a, **k):
        raise AssertionError("funnel read attempted an outbound HTTP call")

    monkeypatch.setattr(httpx.AsyncClient, "get", boom, raising=False)
    monkeypatch.setattr(httpx.Client, "get", boom, raising=False)
    s = settings.model_copy(update={"demo_mode": False})
    _slate(session, 4)

    def counts():
        return tuple(session.scalar(select(func.count()).select_from(m)) for m in
                     (ShadowExperiment, ShadowExperimentDecision, OfficialAspPosition, PositionSettlement, IngestionRun, ModelVersion))

    before = counts()
    qs = QualificationService(session, s)
    for _ in range(2):
        qs.funnel(EVENT_DATE); qs.funnel_v2(EVENT_DATE, now=NOW)
        ReadService(session, s).dashboard(EVENT_DATE)
    assert counts() == before                                                    # (20–23, 27–30)
    assert attached_context(session) is None                                     # context never leaks past the request


def test_candidate_evaluations_remain_distinct_from_positions_in_metadata(session, settings):
    s = settings.model_copy(update={"demo_mode": False})
    _slate(session, 2)
    out = QualificationService(session, s).funnel_v2(EVENT_DATE, now=NOW)
    assert out["positions_created"] == 0 and out["qualified_selections"] == 0     # (25)
    assert out["read_model"]["games"] == 2 and out["read_model"]["prefetch_statements"] >= 10
