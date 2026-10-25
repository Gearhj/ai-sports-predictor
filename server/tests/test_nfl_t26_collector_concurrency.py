"""Two real PostgreSQL sessions racing through the PRODUCTION collector.

Model Studio's previous rejection was exact: the earlier test called
`claim_phase` directly and inserted a scratch stand-in reservation, so it could
not establish that the live collector has one winner and one inert loser.

This one runs `collect_nfl_pregame` itself. The only thing stubbed is the
external HTTP transport -- `fetch_nfl_pregame`. The real atomic claim, the real
`record_request` provider-ledger reservation, the real `complete_request`
terminal row and the real object accounting all execute.

Both entry paths are the genuine ones: `JobOrchestrator.run` with no user is the
scheduler's path, and with a user is `POST /admin/jobs/{job_name}`. Neither is
simulated with a string.

The defect cannot be reproduced on SQLite, which serialises writers, so these
skip loudly without a real PostgreSQL server rather than passing on a substitute.
"""
from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from dotenv import dotenv_values
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.db.base import Base
import app.models  # noqa: F401
from app.models import (
    Game, GameStatus, GameTeam, League, NflAcquisitionPhaseClaim,
    ProviderRequestLedger, Season, Sport, Team,
)
from app.services import sgo_nfl_pregame as pregame

SCRATCH = "t26_collector_scratch"
KICK = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)
CLUSTER = 3      # a tied cluster; Week 3's largest is 9, proved separately


def _pg_url() -> str | None:
    env = dotenv_values(r"C:\Users\james\ai-sports-predictor\.env")
    url = env.get("DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url or not url.startswith(("postgresql", "postgres://")):
        return None
    return url


@pytest.fixture(autouse=True)
def no_network():
    """Any real HTTP transport in this module fails loudly and immediately.

    An earlier draft patched the provider stub inside each thread; the loser
    returned first and its `finally` restored the real client while the winner
    was still running, which made a genuine outbound call. It only surfaced as
    a `ConnectError` because the host happened to be unreachable -- that is an
    environmental accident, not a safeguard. This makes the attempt itself the
    failure.
    """

    import httpx

    original = httpx.Client.__init__

    def refuse(self, *args, **kwargs):
        raise AssertionError(
            "UNEXPECTED_NETWORK_CALL: a test tried to use the real HTTP "
            "transport. The provider must be stubbed once, around the whole "
            "concurrent execution, never per thread.")

    httpx.Client.__init__ = refuse
    try:
        yield
    finally:
        httpx.Client.__init__ = original


@pytest.fixture(scope="module")
def pg():
    """The whole schema, in a scratch PostgreSQL schema we create and drop."""

    url = _pg_url()
    if not url:
        pytest.skip("no PostgreSQL DATABASE_URL; this defect needs real concurrency")
    admin = create_engine(url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with admin.connect() as c:
            c.execute(text(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE"))
            c.execute(text(f"CREATE SCHEMA {SCRATCH}"))
    except Exception as exc:  # noqa: BLE001
        admin.dispose()
        pytest.skip(f"cannot create the scratch schema: {exc}")
    scoped = f"{url}?options=-csearch_path%3D{SCRATCH}"
    build = create_engine(scoped, pool_pre_ping=True)
    Base.metadata.create_all(build)
    build.dispose()
    yield scoped
    with admin.connect() as c:
        c.execute(text(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE"))
    admin.dispose()


@pytest.fixture()
def world(pg):
    """A tied nine-game cluster, due at the retimed primary."""

    engine = create_engine(pg)
    with Session(engine) as db:
        # EVERY table in the scratch schema, not a hand-picked subset: a
        # collection halt written by one test otherwise survives into the next
        # and makes the collector decline for a reason the test is not about.
        names = [r[0] for r in db.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = :s"), {"s": SCRATCH}).all()]
        if names:
            db.execute(text("TRUNCATE {} CASCADE".format(
                ", ".join(f"{SCRATCH}.{n}" for n in names))))
        db.commit()
        sport = Sport(code="FOOTBALL", name="NFL"); db.add(sport); db.flush()
        league = League(sport_id=sport.id, code="NFL", name="NFL"); db.add(league); db.flush()
        season = Season(league_id=league.id, name="2026",
                        starts_at=KICK - timedelta(days=30), ends_at=KICK + timedelta(days=90))
        db.add(season); db.flush()
        ids = []
        for n in range(CLUSTER):
            game = Game(league_id=league.id, season_id=season.id, starts_at=KICK,
                        status=GameStatus.SCHEDULED)
            db.add(game); db.flush()
            ids.append(str(game.id))
            for is_home, abbr in ((True, f"H{n:02d}"), (False, f"A{n:02d}")):
                team = Team(league_id=league.id, name=abbr, abbreviation=abbr)
                db.add(team); db.flush()
                db.add(GameTeam(game_id=game.id, team_id=team.id, is_home=is_home))
        db.commit()
    engine.dispose()
    return {"url": pg, "game_ids": ids}


class _Settings:
    """Only what the collector reads. The retime is ON for these tests."""

    nfl_late_checkpoint_retime_enabled = True
    sportsgameodds_enabled = True
    show_preview_freeze_time = "16:30"
    show_preview_timezone = "America/New_York"
    sportsgameodds_base_url = "https://example.invalid"
    sportsgameodds_api_key = "not-a-real-key"  # secret-guard: test-fixture
    sgo_exact_event_selection_mode = "OFF"
    raw_data_root = None


def _counts(url: str) -> dict:
    engine = create_engine(url)
    try:
        with Session(engine) as s:
            reservations = s.scalars(
                select(ProviderRequestLedger)
                .where(ProviderRequestLedger.status == "RESERVED")).all()
            terminal = s.scalars(
                select(ProviderRequestLedger)
                .where(ProviderRequestLedger.status != "RESERVED")).all()
            return {
                "claims": s.scalar(select(func.count()).select_from(NflAcquisitionPhaseClaim)),
                "reservations": len(reservations),
                "terminal": len(terminal),
                "settledReservationIds": sorted({str(r.reservation_id) for r in terminal}),
                "objects": sum((r.records_received or 0) for r in terminal),
                "fingerprints": sorted(r.request_fingerprint for r in reservations),
            }
    finally:
        engine.dispose()


def _stub_fetch(requested_at, count):
    """A real FallbackFetch, carrying no quotes. Only the transport is stubbed.

    Built from the production dataclass rather than a look-alike, so the
    collector's own attribute expectations are exercised instead of guessed --
    the first draft used a stand-in and the collector immediately found a field
    it did not have.
    """

    from app.services.sgo_collection import FallbackFetch, ParseResult

    return FallbackFetch(
        requested_at=requested_at, objects=count, pairs=[],
        parse=ParseResult(pairs=[], rejected=[], events_seen=0, pregame_events=0),
        provider_league="NFL")


class _FullGrant:
    """A fully funded allocation grant, in the shape the collector reads."""

    def __init__(self, wanted: int) -> None:
        self.allowed = True
        self.granted = wanted
        self.reason = "GRANTED_FOR_CONCURRENCY_TEST"

    def as_dict(self) -> dict:
        return {"allowed": True, "granted": self.granted, "reason": self.reason}


def _contend(url: str, *, administrative: bool, barrier, calls, lock) -> dict:
    """One genuine entry path, all the way through the production collector."""

    import app.services.sgo_nfl_pregame as target

    engine = create_engine(url)
    try:
        with Session(engine) as session:
            moment = KICK - timedelta(minutes=26)

            def stub(settings, **kwargs):
                with lock:
                    calls.append("fetch")
                return _stub_fetch(moment, kwargs.get("limit", 0))

            # THE BARRIER SITS AT THE CLAIM, not at the start of the run.
            #
            # With it at the start, the first contender's allocation grant spent
            # the lane's daily allowance and the second was refused by the
            # ALLOCATOR before it ever reached the claim -- so the test would
            # have proved the allocator serialises, not that the claim does.
            # This wrapper delegates to the real claim_phase; the claim itself
            # is untouched.
            # ALLOCATION IS GRANTED TO BOTH CONTENDERS ON PURPOSE.
            #
            # The lane's daily allowance equals the cluster, so the first
            # contender's grant spends it and the ALLOCATOR refuses the second
            # before it can reach the claim. That is real production behaviour
            # and a second line of defence -- but it would make this test prove
            # the allocator serialises rather than that the claim does. Granting
            # both leaves the atomic claim as the only thing that can stop the
            # second contender, which is exactly what is under test. Nothing
            # else is bypassed: the claim, the real reservation, the terminal
            # result and the per-game object count all execute.
            import app.services.provider_allocation as allocation

            def grant_all(session, *, lane, wanted, now):
                return _FullGrant(wanted)

            real_claim = target.claim_phase

            def claim_at_barrier(*args, **kwargs):
                barrier.wait(timeout=20)
                return real_claim(*args, **kwargs)

            try:
                return target.collect_nfl_pregame(
                    session, _Settings(), now=moment,
                    job_run_id=None,
                    invocation_path="ADMINISTRATIVE" if administrative else "SCHEDULED")
            except threading.BrokenBarrierError:
                return {"attempted": False, "reason": "NEVER_REACHED_THE_CLAIM"}
            finally:
                pass
    finally:
        engine.dispose()


def _race(url: str, *, paths=(False, True)) -> list[dict]:
    """Patch ONCE around both threads, never per thread.

    Patching inside each contender raced: the loser returned first and its
    `finally` restored the real transport while the winner was still inside the
    collector, which then made a genuine HTTP call. The stubs belong outside the
    threads, exactly as the barrier does.
    """

    import app.services.provider_allocation as allocation
    import app.services.sgo_nfl_pregame as target

    barrier = threading.Barrier(len(paths))
    calls: list[str] = []
    lock = threading.Lock()
    moment = KICK - timedelta(minutes=26)

    def stub(settings, **kwargs):
        with lock:
            calls.append("fetch")
        return _stub_fetch(moment, kwargs.get("limit", 0))

    real_claim = target.claim_phase

    def claim_at_barrier(*args, **kwargs):
        barrier.wait(timeout=20)
        return real_claim(*args, **kwargs)

    saved = (target.fetch_nfl_pregame, target.claim_phase, allocation.request_objects)
    target.fetch_nfl_pregame = stub
    target.claim_phase = claim_at_barrier
    allocation.request_objects = lambda session, *, lane, wanted, now: _FullGrant(wanted)
    try:
        with ThreadPoolExecutor(max_workers=len(paths)) as pool:
            futures = [pool.submit(_contend, url, administrative=p, barrier=barrier,
                                   calls=calls, lock=lock) for p in paths]
            results = [f.result() for f in futures]
    finally:
        (target.fetch_nfl_pregame, target.claim_phase,
         allocation.request_objects) = saved
    return results, calls


def test_two_concurrent_collector_runs_yield_one_winner_and_one_inert_loser(world):
    before = _counts(world["url"])
    assert before["claims"] == 0 and before["reservations"] == 0

    results, calls = _race(world["url"])

    inert = [r for r in results if r.get("reason") == "PHASE_ALREADY_CLAIMED"]
    worked = [r for r in results if r.get("reason") != "PHASE_ALREADY_CLAIMED"]
    assert len(inert) == 1, results
    assert len(worked) == 1, results
    assert inert[0]["attempted"] is False and inert[0]["objects"] == 0

    # THE LOSER STOPPED AT THE CLAIM, not at an earlier gate. Both contenders
    # reached the same selection, so neither was refused by eligibility,
    # allocation, an existing price or a missing setting -- an earlier draft of
    # this test was in fact stopped by the ALLOCATOR and proved nothing.
    assert len(inert[0]["games_selected"]) == CLUSTER
    assert inert[0]["phase"] == pregame.PHASE_PRIMARY
    assert worked[0]["reason"] == "COLLECTED"
    for stopper in ("ALLOCATION", "LANE_HALTED", "PROVIDER_NOT_ELIGIBLE",
                    "NO_NFL_GAME_IN_THE_COLLECTION_WINDOW", "DEFERRED"):
        assert stopper not in str(inert[0]["reason"])

    after = _counts(world["url"])
    assert after["claims"] == 1                      # exactly one atomic claim
    assert after["reservations"] == 1                # exactly one REAL reservation
    assert after["terminal"] == 1                    # exactly one terminal result
    assert len(after["settledReservationIds"]) == 1  # bound to that reservation
    assert len(calls) == 1                           # exactly one provider call
    assert after["objects"] == CLUSTER               # one object per game
    assert len(after["fingerprints"]) == len(set(after["fingerprints"])) == 1


def test_both_real_entry_paths_contend_on_the_same_claim(world):
    """The scheduler's path and POST /admin/jobs both reach the same claim."""

    results, calls = _race(world["url"], paths=(True, False))
    assert sum(1 for r in results if r.get("reason") == "PHASE_ALREADY_CLAIMED") == 1
    assert len(calls) == 1

    engine = create_engine(world["url"])
    with Session(engine) as s:
        claim = s.scalars(select(NflAcquisitionPhaseClaim)).one()
        assert claim.phase == pregame.PHASE_PRIMARY
        assert claim.lane == pregame.LANE
        assert claim.invocation_path in {"SCHEDULED", "ADMINISTRATIVE"}
        assert claim.due_game_count == CLUSTER
    engine.dispose()


# ==========================================================================
# Persistence: the claim outlives a provider outcome that leaves games due
# ==========================================================================

def _stubbed(calls, lock, moment, *, barrier=None, fail=False):
    """Install the stubs ONCE, around whatever runs inside.

    Never per thread. Patching inside each contender raced: the loser returned
    first and its `finally` restored the real transport while the winner was
    still inside the collector, which then made a genuine outbound HTTP call.
    """

    import contextlib

    import app.services.provider_allocation as allocation
    import app.services.sgo_nfl_pregame as target

    @contextlib.contextmanager
    def _ctx():
        def stub(settings, **kwargs):
            with lock:
                calls.append("fetch")
            if fail:
                # A TERMINAL ZERO-OBJECT FAILURE. Chosen deliberately: a
                # response that returns successfully but carries no pairs is
                # billed and stores none of the selection, which correctly
                # HALTS the lane -- so it could never show that the second
                # invocation was stopped by the claim. A failed call pays
                # nothing, writes no moneyline, and leaves the games due.
                raise RuntimeError("stubbed terminal provider failure")
            return _stub_fetch(moment, kwargs.get("limit", 0))

        real_claim = target.claim_phase

        def claim_at_barrier(*args, **kwargs):
            if barrier is not None:
                barrier.wait(timeout=20)
            return real_claim(*args, **kwargs)

        saved = (target.fetch_nfl_pregame, target.claim_phase,
                 allocation.request_objects)
        target.fetch_nfl_pregame = stub
        target.claim_phase = claim_at_barrier
        # Allocation is granted to every contender on purpose: the lane's daily
        # allowance equals the cluster, so the ALLOCATOR would otherwise refuse
        # the second before it reached the claim, and the test would prove the
        # allocator serialises rather than that the claim does.
        allocation.request_objects = lambda session, *, lane, wanted, now: _FullGrant(wanted)
        try:
            yield
        finally:
            (target.fetch_nfl_pregame, target.claim_phase,
             allocation.request_objects) = saved

    return _ctx()


def _invoke(url: str, *, moment, administrative: bool = False,
            still_due: bool = False) -> dict:
    """One run of the REAL collector, on its own session.

    `still_due` forces the due-population guards open for this invocation. It
    does not touch the claim, the reservation or the provider path: it
    reproduces a contender that had already passed the due check before a
    competitor's ledger row existed, so that the CLAIM is demonstrably the
    thing that refuses it rather than the earlier ledger guard.
    """

    import app.services.sgo_nfl_pregame as target

    engine = create_engine(url)
    saved = None
    if still_due:
        saved = (target._primary_is_unspent, target._retry_is_owed)
        target._primary_is_unspent = lambda session, *, kickoff: True
        target._retry_is_owed = lambda session, *, kickoff: True
    try:
        with Session(engine) as session:
            return target.collect_nfl_pregame(
                session, _Settings(), now=moment, job_run_id=None,
                invocation_path="ADMINISTRATIVE" if administrative else "SCHEDULED")
    finally:
        if saved is not None:
            target._primary_is_unspent, target._retry_is_owed = saved
        engine.dispose()


def test_the_primary_claim_persists_across_a_second_invocation(world):
    """A provider outcome that writes no price still spends the PRIMARY."""

    moment = KICK - timedelta(minutes=26)
    calls, lock = [], threading.Lock()
    with _stubbed(calls, lock, moment, fail=True):
        first = _invoke(world["url"], moment=moment)
        assert first["reason"] == "REQUEST_FAILED", first
        assert first["objects"] == 0
        mid = _counts(world["url"])
        assert mid["claims"] == 1 and mid["reservations"] == 1
        assert len(calls) == 1

        # TWO LAYERS, PROVED SEPARATELY.
        #
        # The ledger guard fires first: the failed attempt spends the primary,
        # so the game leaves the due population altogether and the collector
        # never reaches the claim. That is the accepted rule and it is checked
        # here as the ordinary case.
        ordinary = _invoke(world["url"], moment=moment)
        assert ordinary["reason"] == "NO_NFL_GAME_IN_THE_COLLECTION_WINDOW", ordinary

        # Now the case the CLAIM exists for: a contender that had already
        # passed the due check before the winner's ledger row existed -- which
        # is precisely what happens in a race. With the due population forced
        # open, the claim is the only thing left that can stop it.
        second = _invoke(world["url"], moment=moment, still_due=True)

    assert second["reason"] == "PHASE_ALREADY_CLAIMED", second
    assert second["attempted"] is False and second["objects"] == 0
    after = _counts(world["url"])
    assert after["claims"] == 1
    assert after["reservations"] == 1          # no second reservation
    assert after["terminal"] == 1              # the failure was recorded once
    assert after["objects"] == 0               # a failed call bills nothing
    assert len(calls) == 1                     # no second provider call


def _seed_terminal_zero_charge_primary(url: str) -> None:
    """One authentic PRIMARY failure, through the accepted ledger semantics."""

    engine = create_engine(url)
    try:
        with Session(engine) as s:
            requested = KICK - timedelta(minutes=26)
            reservation = ProviderRequestLedger(
                provider=pregame.SOURCE, endpoint_key=pregame.LANE,
                requested_at=requested, status="RESERVED", quota_cost=CLUSTER,
                request_fingerprint=uuid.uuid4().hex)
            s.add(reservation)
            s.flush()
            s.add(ProviderRequestLedger(
                provider=pregame.SOURCE, endpoint_key=pregame.LANE,
                requested_at=requested, completed_at=requested + timedelta(seconds=1),
                status="FAILED", quota_cost=CLUSTER, records_received=0,
                reservation_id=reservation.id, request_fingerprint=uuid.uuid4().hex))
            s.commit()
    finally:
        engine.dispose()


def test_the_retry_claim_persists_across_a_second_invocation(world):
    """One retry, consumed by the attempt, whatever the terminal result."""

    _seed_terminal_zero_charge_primary(world["url"])
    moment = KICK - timedelta(minutes=23)
    calls, lock = [], threading.Lock()
    with _stubbed(calls, lock, moment, fail=True):
        first = _invoke(world["url"], moment=moment)
        assert first["reason"] == "REQUEST_FAILED", first
        # The ledger guard alone already refuses a second retry...
        ordinary = _invoke(world["url"], moment=moment)
        assert ordinary["reason"] == "NO_NFL_GAME_IN_THE_COLLECTION_WINDOW", ordinary
        # ...and with the due population forced open, the RETRY claim is what
        # refuses it, which is what a concurrent contender would meet.
        second = _invoke(world["url"], moment=moment, still_due=True)

    assert second["reason"] == "PHASE_ALREADY_CLAIMED", second
    engine = create_engine(world["url"])
    with Session(engine) as s:
        claims = s.scalars(select(NflAcquisitionPhaseClaim)).all()
        assert [c.phase for c in claims] == [pregame.PHASE_RETRY]
    engine.dispose()
    assert len(calls) == 1                     # exactly one retry provider call


def test_an_unknown_primary_charge_refuses_the_retry_through_the_collector(world):
    """A reservation with no terminal row: the charge is unknown, so no retry.

    Refused BEFORE any retry claim, reservation or provider call.
    """

    engine = create_engine(world["url"])
    with Session(engine) as s:
        s.add(ProviderRequestLedger(
            provider=pregame.SOURCE, endpoint_key=pregame.LANE,
            requested_at=KICK - timedelta(minutes=26), status="RESERVED",
            quota_cost=CLUSTER, request_fingerprint=uuid.uuid4().hex))
        s.commit()
    engine.dispose()

    moment = KICK - timedelta(minutes=23)
    calls, lock = [], threading.Lock()
    with _stubbed(calls, lock, moment):
        out = _invoke(world["url"], moment=moment)

    assert out["reason"] == "NO_NFL_GAME_IN_THE_COLLECTION_WINDOW", out
    after = _counts(world["url"])
    assert after["claims"] == 0                # no retry claim was taken
    assert after["reservations"] == 1          # only the seeded unknown one
    assert after["terminal"] == 0
    assert calls == []                         # the provider was never called
