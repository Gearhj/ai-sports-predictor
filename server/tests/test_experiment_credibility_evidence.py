"""Public proof-page evidence contracts.

1. The settlement-ordered equity curve and the per-position ledger extension
   reconcile with the settlement engine's own persisted bankroll chain, and
   simultaneous settlements follow that chain rather than a display sort.
2. Crowd & Market Pulse counts every canonical game exactly once: a persisted
   quote that cannot be a pregame observation (game final, in-play capture,
   no usable midpoint) is its own mutually exclusive state, and the observer
   banner and the coverage strip share that single authority.
3. The bankroll read stays read-only and bounded.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from app.models import GameStatus, OfficialAspPosition, PositionSettlement, PredictionMarketSnapshot
from app.prediction_markets.read_service import PredictionMarketReadService
from app.services.read_service import ReadService
from app.services.shadow_experiment_service import ShadowExperimentService
from test_market_pulse_canonical_coverage import _event_date, _observer_settings, _seed_game, _seed_quoted_game
from test_shadow_experiment import _add_home_value, _append_then_position, _live_slate, _opportunities, _start


# -- 1. settlement order + ledger extension -----------------------------------

def _settlement(*, settled_at, before, after, pl, recommended_at, position_id="p"):
    return (
        SimpleNamespace(settled_at=settled_at, bankroll_before=before, bankroll_after=after, profit_loss=pl,
                        position_id=position_id),
        SimpleNamespace(recommended_at=recommended_at),
    )


def test_simultaneous_settlements_follow_the_persisted_bankroll_chain():
    """Same settled_at: the engine's bankroll_before chain wins over recommended_at."""
    t = datetime(2026, 9, 4, 5, 35, 1, tzinfo=UTC)
    early = datetime(2026, 9, 3, 20, 0, tzinfo=UTC)
    loss = _settlement(settled_at=t, before=997.58, after=995.08, pl=-2.5, recommended_at=early + timedelta(hours=1), position_id="loss")
    win = _settlement(settled_at=t, before=995.08, after=999.78, pl=4.7, recommended_at=early, position_id="win")
    ordered = ShadowExperimentService._settlement_order([win, loss], starting=997.58)
    assert [item[0].position_id for item in ordered] == ["loss", "win"]


def test_settlement_order_keeps_settled_at_as_the_primary_key():
    t0 = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)
    first = _settlement(settled_at=t0, before=1000.0, after=997.5, pl=-2.5, recommended_at=t0, position_id="a")
    later = _settlement(settled_at=t0 + timedelta(hours=1), before=997.5, after=1001.08, pl=3.58, recommended_at=t0 - timedelta(days=1), position_id="b")
    ordered = ShadowExperimentService._settlement_order([later, first], starting=1000.0)
    assert [item[0].position_id for item in ordered] == ["a", "b"]


def test_settlement_order_falls_back_to_recommended_at_without_a_chain():
    t = datetime(2026, 9, 4, 5, 35, 1, tzinfo=UTC)
    a = _settlement(settled_at=t, before=None, after=None, pl=1.0, recommended_at=t - timedelta(hours=2), position_id="a")
    b = _settlement(settled_at=t, before=None, after=None, pl=1.0, recommended_at=t - timedelta(hours=1), position_id="b")
    ordered = ShadowExperimentService._settlement_order([b, a], starting=1000.0)
    assert [item[0].position_id for item in ordered] == ["a", "b"]


@pytest.mark.asyncio
async def test_ledger_view_exposes_reconciled_public_evidence(session, settings, monkeypatch):
    games, event_date, now, start = await _live_slate(session, settings)
    evidence = _add_home_value(session, games[0], observed_at=now)
    _opportunities(monkeypatch, {games[0].id: evidence})
    service = ShadowExperimentService(session, settings)
    experiment = _start(service, event_date, now)
    _append_then_position(service, event_date, now, start)
    position = session.scalar(select(OfficialAspPosition).where(OfficialAspPosition.shadow_experiment_id == experiment.id))
    assert position is not None

    before = service.ledger_view(experiment)
    open_row = next(r for r in before["positions"] if r["id"] == str(position.id))
    assert open_row["result"] == "OPEN"
    assert open_row["bankroll_after"] is None and open_row["settlement_sequence"] is None and open_row["settled_at"] is None
    assert open_row["matchup"] and " @ " in open_row["matchup"]
    assert open_row["decision_id"] and open_row["decision_phase"] == "FINAL_PREGAME"
    assert open_row["model_probability"] is not None and open_row["market_probability"] is not None
    assert len(before["equity"]) == 1 and before["equity"][0]["sequence"] == 0
    assert before["equity"][0]["high_water"] == before["equity"][0]["bankroll"] == float(experiment.starting_bankroll)

    games[0].status = GameStatus.FINAL
    games[0].home_score, games[0].away_score = 6, 2
    session.commit()
    assert service.settle(event_date, now=start + timedelta(hours=3))["settled"] == 1
    settlement = session.scalar(select(PositionSettlement).where(PositionSettlement.position_id == position.id))
    assert settlement is not None

    ledger = service.ledger_view(experiment)
    row = next(r for r in ledger["positions"] if r["id"] == str(position.id))
    assert row["result"] == "WIN" and row["settlement_sequence"] == 1
    assert row["bankroll_after"] == pytest.approx(float(settlement.bankroll_after), abs=0.005)
    assert row["settled_at"] is not None and row["profit_loss"] == pytest.approx(float(settlement.profit_loss), abs=0.005)
    # Equity: opening point + exactly one point per settlement, carrying the settlement.
    assert [p["sequence"] for p in ledger["equity"]] == [0, 1]
    point = ledger["equity"][1]
    assert point["position_id"] == str(position.id) and point["result"] == "WIN"
    assert point["bankroll"] == pytest.approx(row["bankroll_after"], abs=0.005)
    assert point["high_water"] == pytest.approx(max(float(experiment.starting_bankroll), point["bankroll"]), abs=0.005)
    assert point["drawdown"] == pytest.approx(point["high_water"] - point["bankroll"], abs=0.005)
    assert ledger["betting"]["bankroll_chain_reconciled"] is True
    assert ledger["betting"]["current_bankroll"] == pytest.approx(row["bankroll_after"], abs=0.005)
    # Missing closing evidence is None, never zero.
    assert row["clv"] is None or isinstance(row["clv"], float)
    assert "beat_close" in row


@pytest.mark.asyncio
async def test_bankroll_read_is_readonly_and_bounded(session, settings, monkeypatch):
    games, event_date, now, start = await _live_slate(session, settings)
    evidence = _add_home_value(session, games[0], observed_at=now)
    _opportunities(monkeypatch, {games[0].id: evidence})
    service = ShadowExperimentService(session, settings)
    _start(service, event_date, now)
    session.commit()
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        data = ReadService(session, settings).bankroll("v1")
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    assert data.experiment_status in {"ACTIVE", "SCHEDULED"}
    assert data.staking_policy and data.experiment_start_date == event_date.isoformat()
    writes = [s for s in statements if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert writes == []
    assert len(statements) <= 40


# -- 2. Market Pulse: every canonical game counted exactly once ----------------

def _exclusive_sum(board) -> int:
    states = ["QUOTED_REVIEWED", "QUOTED_UNREVIEWED", "QUOTED_STRUCTURALLY_EXCLUDED", "MAPPED_NOT_QUOTED",
              "MAPPING_REVIEW_REQUIRED", "MAPPING_REJECTED", "NO_MAPPED_MARKET", "PROVIDER_DISABLED", "NO_PROVIDER_ENABLED"]
    return sum(board.coverage[k] for k in states)


def test_final_game_quote_is_structurally_excluded_not_an_observation(session, settings, monkeypatch):
    """A game whose ONLY observation was captured after first pitch (and is now
    FINAL) has no pregame evidence: structurally excluded, never an observation."""
    s = _observer_settings(settings)
    start = datetime.now(UTC) - timedelta(minutes=30)
    game, _, _ = _seed_quoted_game(session, s, start=start, rules="Resolves per the official market description.",
                                   question="Yankees vs Red Sox")
    game.status = GameStatus.FINAL
    session.commit()
    monkeypatch.setattr(PredictionMarketReadService, "_aisp_home_probability", lambda self, g: (0.50, True))
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    row = board.rows[0]
    assert row.coverage_state == "QUOTED_STRUCTURALLY_EXCLUDED"
    assert row.unreviewed_observation is False
    assert row.polymarket_home_probability is None and row.diff_aisp_prediction_unreviewed is None
    assert row.display_note == "QUOTED — STRUCTURALLY EXCLUDED (NOT PREGAME / IN-PLAY / NO USABLE PRICE)"
    poly = next(c for c in row.provider_coverage if c.provider == "POLYMARKET")
    assert poly.state == "QUOTED_STRUCTURALLY_EXCLUDED" and "game is FINAL" in poly.reason
    assert board.coverage["QUOTED_STRUCTURALLY_EXCLUDED"] == 1 and board.coverage["QUOTED_UNREVIEWED"] == 0
    assert board.coverage["unreviewed_observations"] == 0
    assert board.coverage["exclusive_sum"] == _exclusive_sum(board) == board.canonical_games == 1
    assert board.board_state != "RESEARCH_OBSERVATIONS_UNREVIEWED"


def test_in_play_quote_on_a_scheduled_game_is_structurally_excluded(session, settings, monkeypatch):
    s = _observer_settings(settings)
    # A quote observed after the scheduled start is recorded in-play; the game
    # row itself may still read SCHEDULED until the status feed catches up.
    start = datetime.now(UTC) - timedelta(minutes=30)
    _seed_quoted_game(session, s, start=start, rules="Resolves per the official market description.", question="Yankees vs Red Sox")
    snap = session.scalar(select(PredictionMarketSnapshot))
    assert snap.in_play is True
    monkeypatch.setattr(PredictionMarketReadService, "_aisp_home_probability", lambda self, g: (0.50, True))
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    row = board.rows[0]
    assert row.coverage_state == "QUOTED_STRUCTURALLY_EXCLUDED" and row.unreviewed_observation is False
    poly = next(c for c in row.provider_coverage if c.provider == "POLYMARKET")
    assert "captured in-play" in poly.reason
    assert board.coverage["exclusive_sum"] == board.canonical_games == 1


def test_banner_and_coverage_strip_share_one_authority(session, settings, monkeypatch):
    """One pregame unreviewed quote + one final-game quote: 1 observation of 2, 1 excluded — never 2 of 2 or 1 of 2 disagreeing."""
    s = _observer_settings(settings)
    start = datetime.now(UTC) + timedelta(minutes=30)  # pregame, same operating date as the in-play game
    _seed_quoted_game(session, s, start=start, event_id="evt-a", rules="Resolves per the official market description.", question="Yankees vs Red Sox")
    # The second game's only quote is captured after its first pitch (in-play),
    # so it carries no pregame evidence; both games sit on today's operating date.
    final_game, _, _ = _seed_quoted_game(session, s, start=datetime.now(UTC) - timedelta(minutes=30), event_id="evt-b", home="Los Angeles Dodgers",
                                         away="Washington Nationals", rules="Resolves per the official market description.",
                                         question="Dodgers vs Nationals")
    final_game.status = GameStatus.FINAL
    session.commit()
    monkeypatch.setattr(PredictionMarketReadService, "_aisp_home_probability", lambda self, g: (0.50, True))
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    assert board.canonical_games == 2
    assert board.coverage["QUOTED_UNREVIEWED"] == 1 and board.coverage["QUOTED_STRUCTURALLY_EXCLUDED"] == 1
    assert board.coverage["unreviewed_observations"] == sum(1 for r in board.rows if r.unreviewed_observation) == 1
    assert board.coverage["exclusive_sum"] == _exclusive_sum(board) == 2
    assert board.coverage["quoted_any"] == 2 and board.summary["quoted"] == 2 and board.summary["unreviewed_observations"] == 1
    assert board.board_state == "RESEARCH_OBSERVATIONS_UNREVIEWED"
    assert board.state_reason.startswith("1 of 2 canonical game(s)")
    assert "1 further quoted game(s) are structurally excluded" in board.state_reason


def test_pregame_unreviewed_quote_still_counts_as_an_observation(session, settings, monkeypatch):
    s = _observer_settings(settings)
    start = datetime.now(UTC) + timedelta(hours=4)
    _seed_quoted_game(session, s, start=start, rules="Resolves per the official market description.", question="Yankees vs Red Sox")
    monkeypatch.setattr(PredictionMarketReadService, "_aisp_home_probability", lambda self, g: (0.50, True))
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    row = board.rows[0]
    assert row.coverage_state == "QUOTED_UNREVIEWED" and row.unreviewed_observation is True
    assert board.coverage["QUOTED_STRUCTURALLY_EXCLUDED"] == 0 and board.coverage["exclusive_sum"] == 1
    assert board.state_reason.startswith("1 of 1 canonical game(s)") and "structurally excluded" not in board.state_reason


def test_unquoted_games_are_unaffected_by_the_new_state(session, settings):
    s = _observer_settings(settings)
    start = datetime.now(UTC) + timedelta(hours=4)
    _seed_game(session, start=start)
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    assert board.canonical_games == 1
    assert board.coverage["NO_MAPPED_MARKET"] == 1 and board.coverage["QUOTED_STRUCTURALLY_EXCLUDED"] == 0
    assert board.coverage["exclusive_sum"] == 1


def test_market_pulse_board_read_makes_no_write(session, settings, monkeypatch):
    s = _observer_settings(settings)
    start = datetime.now(UTC) + timedelta(hours=4)
    game, _, _ = _seed_quoted_game(session, s, start=start, rules="Resolves per the official market description.", question="Yankees vs Red Sox")
    game.status = GameStatus.FINAL
    session.commit()
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        PredictionMarketReadService(session, s).board(_event_date(s, start))
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    assert [x for x in statements if x.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))] == []
