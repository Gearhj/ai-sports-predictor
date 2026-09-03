"""Equivalence + performance-shape regression for ``ReadService._latest_market_snapshots``.

The Today/market-board read path calls this per game up to three times. The
original implementation loaded every snapshot ever recorded for the game and
reduced them in Python (profiled at ~8 s of a ~9 s Today request on a 15-game
slate with ~2,160 snapshot rows per game). The SQL rewrite must return EXACTLY
the same rows under every rule the old reducer applied:

* per (market, sportsbook) keep only the batch at that pair's latest
  ``observed_at``;
* one row per (outcome, sportsbook);
* ``cutoff`` bounds ``available_as_of``;
* rows whose ``source_mode`` differs from the game's are excluded;
* newest first.

``_reference_latest`` below is a verbatim copy of the retired reducer and is
the oracle the new implementation is compared against.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.models import (
    Game,
    GameStatus,
    GameTeam,
    League,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    Sport,
    Sportsbook,
    Team,
)
from app.services.read_service import ReadService, _aware

T0 = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _reference_latest(session, game_id, *, cutoff=None):
    """Verbatim copy of the pre-repair reducer (the oracle)."""
    query = (
        select(Market, MarketOutcome, OddsSnapshot, Sportsbook)
        .join(MarketOutcome, MarketOutcome.market_id == Market.id)
        .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id == game_id)
        .order_by(OddsSnapshot.observed_at.desc())
    )
    if cutoff is not None:
        query = query.where(OddsSnapshot.available_as_of <= _aware(cutoff))
    rows = list(session.execute(query))
    game = session.get(Game, game_id)
    if game:
        rows = [row for row in rows if row[2].source_mode == game.source_mode]
    latest_market_book: dict[tuple[Any, Any], datetime] = {}
    for market, _, snapshot, book in rows:
        key = (market.id, book.id)
        observed = _aware(snapshot.observed_at)
        if key not in latest_market_book or observed > latest_market_book[key]:
            latest_market_book[key] = observed
    seen: set[tuple[Any, Any]] = set()
    latest = []
    for row in rows:
        market, outcome, snapshot, book = row
        key = (market.id, book.id)
        outcome_key = (outcome.id, book.id)
        if _aware(snapshot.observed_at) == latest_market_book[key] and outcome_key not in seen:
            latest.append(row)
            seen.add(outcome_key)
    return latest


def _seed(session) -> tuple[Game, dict[str, Any]]:
    sport = Sport(code="BASEBALL", name="Baseball", active=True)
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB", active=True)
    session.add(league)
    session.flush()
    home = Team(league_id=league.id, name="New York Yankees", abbreviation="NYY", city="New York", active=True)
    away = Team(league_id=league.id, name="Boston Red Sox", abbreviation="BOS", city="Boston", active=True)
    session.add_all([home, away])
    session.flush()
    game = Game(league_id=league.id, starts_at=T0 + timedelta(hours=7), status=GameStatus.SCHEDULED, source_mode="LIVE")
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    ml = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
    total = Market(game_id=game.id, market_type=MarketType.TOTAL, period="full_game")
    session.add_all([ml, total])
    session.flush()
    o_home = MarketOutcome(market_id=ml.id, outcome_key="home:None", label="NYY", participant_team_id=home.id)
    o_away = MarketOutcome(market_id=ml.id, outcome_key="away:None", label="BOS", participant_team_id=away.id)
    o_over = MarketOutcome(market_id=total.id, outcome_key="over:8.5", label="Over")
    o_under = MarketOutcome(market_id=total.id, outcome_key="under:8.5", label="Under")
    session.add_all([o_home, o_away, o_over, o_under])
    book_a = Sportsbook(key="book_a", name="Book A", active=True)
    book_b = Sportsbook(key="book_b", name="Book B", active=True)
    session.add_all([book_a, book_b])
    session.flush()

    def snap(outcome, book, t, *, mode="LIVE", odds=1.9, point=None):
        row = OddsSnapshot(
            market_outcome_id=outcome.id, sportsbook_id=book.id, observed_at=t, available_as_of=t,
            american_odds=-110, decimal_odds=odds, point=point, source="test", source_mode=mode,
        )
        session.add(row)
        return row

    t1, t2, t3 = T0, T0 + timedelta(minutes=30), T0 + timedelta(hours=1)
    # Book A moneyline: full batches at t1 and t2 -> t2 wins for both outcomes.
    snap(o_home, book_a, t1); snap(o_away, book_a, t1)
    snap(o_home, book_a, t2, odds=1.95); snap(o_away, book_a, t2, odds=1.85)
    # Book B moneyline: full batch at t1, PARTIAL update (home only) at t3 ->
    # the t3 batch wins and yields ONE row (home only) for book B.
    snap(o_home, book_b, t1); snap(o_away, book_b, t1)
    snap(o_home, book_b, t3, odds=2.05)
    # Book A totals: t1 batch and t2 batch.
    snap(o_over, book_a, t1, point=8.5); snap(o_under, book_a, t1, point=8.5)
    snap(o_over, book_a, t2, point=9.0); snap(o_under, book_a, t2, point=9.0)
    # A DEMO-mode snapshot newer than everything: must be excluded for a LIVE game.
    snap(o_home, book_a, t3 + timedelta(hours=1), mode="DEMO")
    session.commit()
    return game, {"t1": t1, "t2": t2, "t3": t3, "book_a": book_a, "book_b": book_b,
                  "o_home": o_home, "o_away": o_away, "o_over": o_over, "o_under": o_under}


def _ids(rows):
    return sorted((str(m.id), str(o.id), str(s.id), str(b.id)) for m, o, s, b in rows)


def test_rewrite_matches_reference_reducer_exactly(session, settings):
    game, f = _seed(session)
    service = ReadService(session, settings)
    new = service._latest_market_snapshots(game.id)
    old = _reference_latest(session, game.id)
    assert _ids(new) == _ids(old)
    assert len(new) == 5  # A: home, away (t2) + A: over, under (t2) + B: home (t3)
    by_outcome_book = {(str(o.id), str(b.id)): s for _, o, s, b in new}
    assert _aware(by_outcome_book[(str(f["o_home"].id), str(f["book_a"].id))].observed_at) == f["t2"]
    assert _aware(by_outcome_book[(str(f["o_home"].id), str(f["book_b"].id))].observed_at) == f["t3"]
    assert (str(f["o_away"].id), str(f["book_b"].id)) not in by_outcome_book  # partial t3 batch wins
    assert all(s.source_mode == "LIVE" for _, _, s, _ in new)  # DEMO excluded
    observed = [_aware(s.observed_at) for _, _, s, _ in new]
    assert observed == sorted(observed, reverse=True)  # newest first


def test_cutoff_is_honoured_identically(session, settings):
    game, f = _seed(session)
    service = ReadService(session, settings)
    cutoff = f["t1"] + timedelta(minutes=5)
    new = service._latest_market_snapshots(game.id, cutoff=cutoff)
    old = _reference_latest(session, game.id, cutoff=cutoff)
    assert _ids(new) == _ids(old)
    assert len(new) == 6  # every t1 row: A ml x2, B ml x2, A totals x2
    assert all(_aware(s.observed_at) == f["t1"] for _, _, s, _ in new)


def test_result_is_memoised_per_service_instance(session, settings):
    game, _ = _seed(session)
    service = ReadService(session, settings)
    first = service._latest_market_snapshots(game.id)
    second = service._latest_market_snapshots(game.id)
    assert _ids(first) == _ids(second)
    assert (str(game.id), None) in service._market_snapshot_cache
    # A different cutoff is a different cache entry, never a stale reuse.
    service._latest_market_snapshots(game.id, cutoff=T0)
    assert len(service._market_snapshot_cache) == 2


def test_only_winning_rows_are_materialised(session, settings, monkeypatch):
    """The whole point: the database must not hand back the full history."""
    game, _ = _seed(session)
    service = ReadService(session, settings)
    seen_rows = {"n": 0}
    original_execute = session.execute

    def counting_execute(statement, *args, **kwargs):
        result = original_execute(statement, *args, **kwargs)
        rows = list(result)
        seen_rows["n"] += len(rows)
        return iter(rows)

    monkeypatch.setattr(session, "execute", counting_execute)
    rows = service._latest_market_snapshots(game.id)
    assert len(rows) == 5
    # 12 LIVE + 1 DEMO snapshots exist; the reference loaded all 13 (then
    # filtered in Python). The rewrite must fetch only the 5 winners.
    assert seen_rows["n"] == 5
