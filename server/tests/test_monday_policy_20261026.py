"""Owner decisions of 2026-10-25 (James), offline:

1. CFB paper funding record-only, switchable by clock, default OFF -- the NBA mechanism.
2. Paper bets execute only at US-regulated sportsbooks (AISP_OWNER_US_REGULATED_EXECUTION_ONLY_V1):
   offshore books stay in consensus but are never the execution book; unknown keys fail closed;
   ``EXECUTION_US_REGULATED_ONLY`` (default True) false restores the old any-book selection.
3. MLB gets the $1.00 minimum stake that 72a0198 gave CFB/NHL/NBA/NFL.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.models import AuditEvent, OfficialAspPosition, Sportsbook
from app.services import execution_books as xb
from app.services import owner_funding_restrictions as ofr
from app.services.research_paper_policy import CFB as CFB_POLICY, moneyline_terms

CLOCK = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
AFTER = CLOCK + timedelta(hours=6)
BEFORE = CLOCK - timedelta(minutes=1)


def _decimal(american: int) -> float:
    return 1 + (american / 100 if american > 0 else 100 / -american)


# ------------------------------------------------------------ 1. CFB record-only

def _qualified():
    return {"outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED",
            "selection": {"win_probability": 0.55, "american_odds": -110, "opposite_american": -110,
                          "sportsbook": "draftkings", "source": "THE_ODDS_API", "odds_snapshot_id": "snap-1",
                          "terms": {"stake": 4.5, "no_vig_probability": 0.5}}}


def test_cfb_record_only_ships_off_and_off_funds_as_today(settings):
    assert Settings.model_fields["cfb_paper_funding_record_only_effective_from"].default is None
    assert settings.cfb_paper_funding_record_only_effective_from is None
    decision = _qualified()
    assert ofr.apply_to_decision(decision, "CFB", AFTER, settings) is decision
    assert ofr.cfb_record_only_active(moment=AFTER, settings=settings) is False


def test_cfb_record_only_on_records_without_a_position_and_keeps_the_evidence(settings):
    on = settings.model_copy(update={"cfb_paper_funding_record_only_effective_from": CLOCK})
    result = ofr.apply_to_decision(_qualified(), "CFB", AFTER, on)
    assert (result["outcome"], result["reason"]) == ("RECORD_ONLY", "OWNER_CFB_PAPER_FUNDING_RECORD_ONLY")
    receipt = result["funding_restriction"]
    assert receipt["rule"] == ofr.CFB_RULE and receipt["sport"] == "CFB" and receipt["record_only"] is True
    assert receipt["original_qualification"] == {"outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED"}
    assert result["selection"] == _qualified()["selection"]
    # Before the clock, other sports, and non-qualified CFB decisions: untouched.
    assert ofr.apply_to_decision(_qualified(), "CFB", BEFORE, on)["outcome"] == "QUALIFIED"
    assert ofr.apply_to_decision(_qualified(), "NBA", AFTER, on)["outcome"] == "QUALIFIED"
    declined = {**_qualified(), "outcome": "DECLINED", "reason": "NO_QUALIFYING_EDGE"}
    assert ofr.apply_to_decision(declined, "CFB", AFTER, on) is declined
    # NBA still has its own clock and reason.
    nba = settings.model_copy(update={"nba_paper_funding_record_only_effective_from": CLOCK})
    assert ofr.apply_to_decision(_qualified(), "NBA", AFTER, nba)["reason"] == ofr.NBA_REASON
    assert ofr.apply_to_decision(_qualified(), "CFB", AFTER, nba)["outcome"] == "QUALIFIED"


def test_cfb_record_only_is_reported_beside_nba_and_parses_from_env(settings):
    off = ofr.runtime_state(settings, now=AFTER)
    assert off["cfb_paper_funding_record_only_effective_from_utc"] is None
    assert off["cfb_paper_funding_record_only_active_at_observation"] is False
    on = ofr.runtime_state(settings.model_copy(update={"cfb_paper_funding_record_only_effective_from": CLOCK}),
                           now=AFTER)
    assert on["cfb_paper_funding_record_only_effective_from_utc"] == CLOCK.isoformat()
    assert on["cfb_paper_funding_record_only_active_at_observation"] is True
    parsed = Settings.model_validate({**settings.model_dump(),
                                      "cfb_paper_funding_record_only_effective_from": "2026-10-26T12:00:00Z"})
    assert parsed.cfb_paper_funding_record_only_effective_from == CLOCK
    with pytest.raises(ValueError, match="timezone"):
        ofr.cfb_record_only_active(moment=AFTER, settings=SimpleNamespace(
            cfb_paper_funding_record_only_effective_from="2026-10-26T12:00:00"))


# ------------------------------------------------- 2. US-regulated execution only

def test_execution_book_classification_fails_closed():
    assert Settings.model_fields["execution_us_regulated_only"].default is True
    for key in ("draftkings", "fanduel", "betmgm", "williamhill_us", "betrivers", "espnbet", "fanatics",
                "hardrockbet", "ballybet", "betparx", "bet365", "sgo:draftkings", "sgo:caesars", "DraftKings",
                "Caesars", "DraftKings (SportsGameOdds, research)"):
        assert xb.is_execution_book(key), key
    for key in ("betonlineag", "lowvig", "bovada", "mybookieag", "betus", "sgo:bovada", "BetOnline.ag",
                "LowVig.ag", "MyBookie.ag"):
        assert xb.classify(key) == "OFFSHORE" and not xb.is_execution_book(key), key
    for key in ("fliff", "demo_alpha", "sgo:book", "", None):
        assert xb.classify(key) == "UNKNOWN" and not xb.is_execution_book(key), key   # unknown never executes
    off = SimpleNamespace(execution_us_regulated_only=False)
    assert xb.is_execution_book("bovada", off) and xb.is_execution_book("anything", off)


def _candidates(books: dict[str, tuple[int, int]], p_home=0.5):
    out = []
    for book, (home, away) in books.items():
        for side, win, american, other in (("HOME", p_home, home, away), ("AWAY", 1 - p_home, away, home)):
            out.append({"side": side, "sportsbook": book, "american_odds": american,
                        "terms": moneyline_terms(CFB_POLICY, win_probability=win, decimal_odds=_decimal(american),
                                                 opposite_decimal_odds=_decimal(other), bankroll=1000.0)})
    return out


def test_research_paths_choose_the_best_us_pair_when_offshore_is_better():
    cands = _candidates({"bovada": (+150, -170), "draftkings": (+130, -150)})
    executable, excluded = xb.partition(cands)
    assert {c["sportsbook"] for c in executable} == {"draftkings"}
    assert xb.decline_without_us_price({}, sport="CFB", executable=executable, excluded=excluded) is None
    best = max([c for c in executable if c["terms"]["qualifies"]], key=lambda c: c["terms"]["expected_return_per_dollar"])
    assert (best["sportsbook"], best["side"]) == ("draftkings", "HOME")
    receipt = xb.receipt(excluded)
    assert receipt["excluded_from_execution"] == [{"sportsbook": "bovada", "class": "OFFSHORE"}]
    assert receipt["offshore_books_feed_consensus_only"] is True


def test_only_offshore_value_or_only_offshore_prices_decline_no_us_regulated_price():
    # US price has no edge; offshore would have qualified.
    cands = _candidates({"bovada": (+150, -170), "draftkings": (-105, -115)})
    executable, excluded = xb.partition(cands)
    declined = xb.decline_without_us_price({"game_id": "g"}, sport="NHL", executable=executable,
                                           excluded=excluded, bankroll=1000.0)
    assert (declined["outcome"], declined["reason"]) == ("DECLINED", "NO_US_REGULATED_PRICE")
    assert declined["best"]["sportsbook"] == "draftkings"                    # the row records a US book
    assert declined["excluded_execution_candidate"]["sportsbook"] == "bovada"
    # Only offshore/unknown prices at all.
    executable, excluded = xb.partition(_candidates({"lowvig": (+150, -170), "fliff": (+160, -180)}))
    declined = xb.decline_without_us_price({}, sport="NBA", executable=executable, excluded=excluded)
    assert declined["reason"] == "NO_US_REGULATED_PRICE" and "best" not in declined
    # Nothing qualifies anywhere, US present: the path's own NO_QUALIFYING_EDGE applies.
    executable, excluded = xb.partition(_candidates({"bovada": (-105, -115), "draftkings": (-105, -115)}))
    assert xb.decline_without_us_price({}, sport="CFB", executable=executable, excluded=excluded) is None


def test_flag_false_restores_any_book_selection():
    off = SimpleNamespace(execution_us_regulated_only=False)
    executable, excluded = xb.partition(_candidates({"bovada": (+150, -170), "draftkings": (+130, -150)}), off)
    assert excluded == [] and {c["sportsbook"] for c in executable} == {"bovada", "draftkings"}
    assert xb.receipt(excluded, off)["enabled"] is False


# NBA end to end, through the real checkpoint (synthetic fixtures, SGO adapter).
from tests.test_nba_gen1_consumer import world  # noqa: E402,F401 -- fixture
from tests.test_nba_gen1_paper_and_prices import (  # noqa: E402
    AWAY, BUY_AT, DECIDE_AT, HOME, LEAGUE, _eligible_game, _grant, _permit, era, event,  # noqa: F401
)


def _price_books(session, game_id, books: dict[str, tuple[int, int]], at=BUY_AT):
    from app.data_sources.sgo_parser import parse_main_markets
    from app.nba import sgo_price

    payload = event(updated=at - timedelta(minutes=2))
    odds = payload["data"][0]["odds"]
    for odd_id, index in (("points-home-game-ml-home", 0), ("points-away-game-ml-away", 1)):
        template = odds[odd_id]["byBookmaker"]["draftkings"]
        odds[odd_id]["byBookmaker"] = {book: {**template, "odds": f"{prices[index]:+d}"}
                                       for book, prices in books.items()}
    binding = sgo_price.Binding(league_id=LEAGUE, team_map={HOME: "nba-neil:franchise:FXH",
                                                            AWAY: "nba-neil:franchise:FXA"})
    parsed = parse_main_markets(payload, received_at=at, now=at, league_id=LEAGUE)
    bound = [q for q in parsed.quotes if sgo_price.bind_quote_event(session, game_id, q, binding)[0]]
    return sgo_price.persist(session, game_id, bound, available_as_of=at)


def _nba(session, world, monkeypatch, tmp_path, books, **flags):
    from app.nba import gen1_paper

    grant = _grant(tmp_path)
    row = _eligible_game(session, world, grant=grant)
    _permit(monkeypatch)
    _price_books(session, row.canonical_game_id, books(row.joint_home_win_probability))
    configured = world["settings"].model_copy(update={**grant["settings"], "nba_paper_decisions_enabled": True,
                                                      **flags})
    return gen1_paper.evaluate(session, configured, now=DECIDE_AT)["decisions"][0]


def _long(p):        # a home price far longer than the joint probability implies
    return +250 if p > 0.4 else +500


def test_nba_offshore_best_price_executes_at_the_best_us_book(session, world, era, monkeypatch, tmp_path):
    decision = _nba(session, world, monkeypatch, tmp_path,
                    lambda p: {"bovada": (_long(p) + 50, -300), "draftkings": (_long(p), -300)})
    assert decision["reason"] == "ALL_GATES_PASSED", decision
    assert decision["selection"]["sportsbook"] == "sgo:draftkings"
    assert decision["execution_books"]["excluded_from_execution"] == [{"sportsbook": "sgo:bovada",
                                                                       "class": "OFFSHORE"}]
    position = session.get(OfficialAspPosition, uuid.UUID(decision["position_id"]))
    assert position is not None


def test_nba_only_offshore_declines_no_us_regulated_price(session, world, era, monkeypatch, tmp_path):
    decision = _nba(session, world, monkeypatch, tmp_path, lambda p: {"bovada": (_long(p), -300)})
    assert (decision["outcome"], decision["reason"]) == ("DECLINED", "NO_US_REGULATED_PRICE"), decision
    assert decision["position_id"] is None
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 0


def test_nba_unknown_book_is_never_executed(session, world, era, monkeypatch, tmp_path):
    decision = _nba(session, world, monkeypatch, tmp_path, lambda p: {"somenewbook": (_long(p), -300)})
    assert decision["reason"] == "NO_US_REGULATED_PRICE" and decision["position_id"] is None
    assert decision["execution_books"]["excluded_from_execution"][0]["class"] == "UNKNOWN"


def test_nba_flag_false_restores_the_offshore_selection(session, world, era, monkeypatch, tmp_path):
    decision = _nba(session, world, monkeypatch, tmp_path,
                    lambda p: {"bovada": (_long(p) + 50, -300), "draftkings": (_long(p), -300)},
                    execution_us_regulated_only=False)
    assert decision["reason"] == "ALL_GATES_PASSED"
    assert decision["selection"]["sportsbook"] == "sgo:bovada"


# NFL: the real ``_decide`` with its upstream gates stubbed (they are tested elsewhere).

def _nfl(monkeypatch, books: dict[str, tuple[int, int]], settings=None):
    from app.services import nfl_paper_decision as nfl

    kickoff = datetime(2026, 10, 25, 17, 0, tzinfo=UTC)
    moment = kickoff - timedelta(minutes=20)
    seen = (moment - timedelta(minutes=5)).isoformat()
    forecast = SimpleNamespace(event_id="e", forecast_id="f", id=1, snapshot_type="FINAL_PREGAME",
                               snapshot_version=1, available_as_of_utc=None, forecast_cutoff_utc=None,
                               full_home_win_probability=0.55, full_away_win_probability=0.43)
    monkeypatch.setattr(nfl, "operational_start", lambda *a, **k: kickoff)
    monkeypatch.setattr(nfl, "_final_forecast_available_by", lambda *a, **k: forecast)
    monkeypatch.setattr(nfl, "_check_readiness", lambda *a, **k: SimpleNamespace(
        verified=True, as_record=lambda: {}, reason=None, sub_reasons=[], detail=""))
    monkeypatch.setattr(nfl.eligibility, "check", lambda *a, **k: {"eligible": True})
    monkeypatch.setattr(nfl.eligibility, "successor_gap", lambda *a, **k: k["binding"])
    pairs = [{"market_id": "m", "sportsbook": book, "source": "SPORTSGAMEODDS", "home_american": home,
              "away_american": away, "home_outcome_id": f"{book}-h", "away_outcome_id": f"{book}-a",
              "home_snapshot_id": f"{book}-hs", "away_snapshot_id": f"{book}-as", "home_observed_at": seen,
              "away_observed_at": seen, "pair_observed_at": seen, "older_leg": "home"}
             for book, (home, away) in books.items()]
    monkeypatch.setattr(nfl, "paired_quotes", lambda *a, **k: pairs)
    monkeypatch.setattr(nfl, "clock_bound_pairs", lambda s, g, permitted, **k: (permitted, []))
    era = SimpleNamespace(id="era", starting_bankroll=1000)
    return nfl._decide(None, era, {"game_id": "g", "away": "A", "home": "H", "starts_at": kickoff.isoformat()},
                       moment, settings)


def test_nfl_offshore_best_price_executes_at_the_best_us_book(monkeypatch, settings):
    out = _nfl(monkeypatch, {"sgo:bovada": (+150, -170), "sgo:draftkings": (+130, -150)}, settings)
    assert out["reason"] == "ALL_GATES_PASSED" and out["selection"]["sportsbook"] == "sgo:draftkings"
    assert out["execution_books"]["excluded_from_execution"] == [{"sportsbook": "sgo:bovada", "class": "OFFSHORE"}]


def test_nfl_only_offshore_value_declines_no_us_regulated_price(monkeypatch, settings):
    out = _nfl(monkeypatch, {"sgo:bovada": (+150, -170), "sgo:draftkings": (-140, +100)}, settings)
    assert (out["outcome"], out["reason"]) == ("DECLINED", "NO_US_REGULATED_PRICE")
    assert out["best"]["sportsbook"] == "sgo:draftkings"
    assert out["excluded_execution_candidate"]["sportsbook"] == "sgo:bovada"
    out = _nfl(monkeypatch, {"sgo:lowvig": (+150, -170)}, settings)
    assert out["reason"] == "NO_US_REGULATED_PRICE" and "best" not in out
    # No value anywhere: the old reason.
    assert _nfl(monkeypatch, {"sgo:bovada": (-140, +100), "sgo:draftkings": (-140, +100)},
                settings)["reason"] == "NO_QUALIFYING_VALUE"


def test_nfl_flag_false_restores_the_offshore_selection(monkeypatch, settings):
    off = settings.model_copy(update={"execution_us_regulated_only": False})
    out = _nfl(monkeypatch, {"sgo:bovada": (+150, -170), "sgo:draftkings": (+130, -150)}, off)
    assert out["reason"] == "ALL_GATES_PASSED" and out["selection"]["sportsbook"] == "sgo:bovada"


# MLB: the opportunity board's execution price, and the position guard.

def test_mlb_execution_price_is_the_best_us_book_and_consensus_keeps_every_book():
    from app.forecast_exchange.pricing import ShadowPricingConfig
    from app.services.opportunity_service import BookQuote, SideInput, price_side

    now = datetime(2026, 10, 26, 22, 0, tzinfo=UTC)
    quotes = (BookQuote(book="Bovada", sportsbook_key="bovada", side_american=+160, opposite_american=-180,
                        observed_at=now),
              BookQuote(book="DraftKings", sportsbook_key="draftkings", side_american=+140,
                        opposite_american=-165, observed_at=now))
    side = SideInput(selection="Home", line=None, model_probability=0.45, conservative_probability=0.43,
                     quotes=quotes)
    kwargs = dict(market_label="Moneyline", period="Full game", readiness_ok=True, critical_inputs_ok=True,
                  config=ShadowPricingConfig(qualified_edge=0.02, watch_edge=0.01), now=now)
    any_book = price_side(side, **kwargs)
    us_only = price_side(side, **kwargs, execution_filter=lambda q: xb.is_execution_book(q.sportsbook_key))
    assert any_book.book == "Bovada" and us_only.book == "DraftKings" and us_only.american_odds == 140
    assert us_only.consensus_probability == any_book.consensus_probability          # consensus unchanged
    offshore_only = SideInput(selection="Home", line=None, model_probability=0.45, conservative_probability=0.43,
                              quotes=quotes[:1])
    assert price_side(offshore_only, **kwargs, execution_filter=lambda q: xb.is_execution_book(q.sportsbook_key)) \
        is None


# MLB position creation, through the real freeze (demo slate from tests/test_shadow_experiment.py).
from tests.test_shadow_experiment import (  # noqa: E402
    _add_home_value, _append_then_position, _live_slate, _opportunities, _start,
)


async def _mlb(session, settings, monkeypatch, *, book_key):
    from app.services.shadow_experiment_service import ShadowExperimentService

    games, event_date, now, start = await _live_slate(session, settings)
    evidence = _add_home_value(session, games[0], observed_at=now)
    evidence[2].key = book_key
    session.commit()
    _opportunities(monkeypatch, {games[0].id: evidence})
    service = ShadowExperimentService(session, settings)
    _start(service, event_date, now)
    _captured, positioned = _append_then_position(service, event_date, now, start)
    receipts = [m for (m,) in session.execute(select(AuditEvent.metadata_json).where(
        AuditEvent.action == "MLB_FINAL_PREGAME_FUNDING_V1"))]
    return positioned, receipts


@pytest.mark.asyncio
async def test_mlb_us_book_funds_and_offshore_book_is_refused(session, settings, monkeypatch):
    positioned, _ = await _mlb(session, settings, monkeypatch, book_key="draftkings")
    assert positioned["primary"] == 1


@pytest.mark.asyncio
async def test_mlb_offshore_decision_is_not_funded_no_us_regulated_price(session, settings, monkeypatch):
    positioned, receipts = await _mlb(session, settings, monkeypatch, book_key="bovada")
    assert positioned["primary"] == 0
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 0
    assert [r["reason"] for r in receipts] == ["NO_US_REGULATED_PRICE"]


@pytest.mark.asyncio
async def test_mlb_flag_false_funds_the_offshore_decision(session, settings, monkeypatch):
    off = settings.model_copy(update={"execution_us_regulated_only": False})
    positioned, _ = await _mlb(session, off, monkeypatch, book_key="bovada")
    assert positioned["primary"] == 1


# ------------------------------------------------------------ 3. MLB $1 minimum

@pytest.mark.asyncio
async def test_mlb_sub_dollar_stake_declines_stake_below_minimum(session, settings, monkeypatch):
    tiny = settings.model_copy(update={"shadow_max_position_percentage": 0.0005})       # $0.50 of $1,000
    positioned, receipts = await _mlb(session, tiny, monkeypatch, book_key="draftkings")
    assert positioned["primary"] == 0
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 0
    assert [r["reason"] for r in receipts] == ["STAKE_BELOW_MINIMUM"]
    assert receipts[0]["stake"] == 0.0 and receipts[0]["limits"]["sized_stake"] == 0.5


@pytest.mark.asyncio
async def test_mlb_a_dollar_stake_is_placed(session, settings, monkeypatch):
    dollar = settings.model_copy(update={"shadow_max_position_percentage": 0.001})     # $1.00 of $1,000
    positioned, _ = await _mlb(session, dollar, monkeypatch, book_key="draftkings")
    assert positioned["primary"] == 1
    assert float(session.scalar(select(OfficialAspPosition.stake))) == 1.0
