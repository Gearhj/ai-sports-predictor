"""NHL in the read models: the served ledger, the sports readiness card, edge labels and the account note.

TEST FIXTURES ONLY. Every forecast, quote, account and result is invented in a
throwaway SQLite database (the NHL paper lifecycle's own builders). What each
test guards against, now that NHL paper is live (era opened 2026-10-16 with its
own $1,000 season funding):

* the ledger summing four accounts ($4,000) and silently omitting NHL's era,
  positions and settlements -- or counting NHL's $1,000 twice;
* /sports/readiness carrying no NHL card, or an NHL card that disagrees with
  what the checkpoint itself would hold (forecast route, eligibility, price);
* a readiness read writing anything (it must stay a cheap, cached read);
* NHL decisions on the board without their recorded edge figures;
* a future NHL account note still saying no NHL policy exists.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.nhl import paper
from app.services import command_center, sport_readiness
from app.services.experiment_accounts import ExperimentAccountService
from tests.test_nhl_paper_lifecycle import (
    DECIDE, START, _activate, _evaluate, _in_process_forecast, _quotes, _result, _settings,
)


def _sqlite_ledger(session, monkeypatch, now):
    """The real experiment_ledger on SQLite, stubbing only its Postgres-only helpers."""

    def iso(moment):
        if isinstance(moment, str):
            moment = datetime.fromisoformat(moment)
        if moment is None:
            return None
        return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC).isoformat()

    monkeypatch.setattr(command_center, "_matchups", lambda s, ids: {})
    monkeypatch.setattr(command_center, "_iso", iso)
    return command_center.experiment_ledger(session, now)


def _fund_other_sports(session, settings):
    """MLB, NFL, NBA and CFB at $1,000 each, the way production holds them."""

    service = ExperimentAccountService(session, settings)
    for sport, season, effective in (("MLB", "2026", date(2026, 9, 1)), ("NFL", "2026", date(2026, 9, 14)),
                                     ("NBA", "2026-27", date(2026, 10, 1)), ("CFB", "2026", date(2026, 9, 15))):
        account = service.ensure_account(sport, season)
        service.open_season(account, now=datetime(2026, 9, 15, 12, tzinfo=UTC), executed_by="test",
                            tracking_started_on=effective)
    session.flush()


def _live_settings(settings, **extra):
    return _settings(settings, nhl_paper_checkpoint_enabled=True, nhl_odds_api_enabled=True,
                     nhl_forecast_generation_enabled=True, **extra)


@pytest.fixture
def live(session, settings, monkeypatch):
    """Five funded accounts; NHL opened under its standing authority with an in-process forecast and a pair."""

    import app.core.config as config

    s = _live_settings(settings)
    monkeypatch.setattr(config, "get_settings", lambda: s)
    _fund_other_sports(session, s)
    forecast, game = _in_process_forecast(session)
    stored, receipts = _quotes(session)
    assert stored["created"] == 2 and receipts[0]["reason"] == "BOUND"
    _activate(session, s)
    session.commit()
    return s, forecast, game


# ------------------------------------------------------------------ ledger --
def test_the_ledger_counts_nhl_once_with_its_positions_and_settlement(session, live, monkeypatch):
    s, forecast, game = live
    before = _sqlite_ledger(session, monkeypatch, DECIDE)
    by_sport = {row["sport"]: row for row in before["sports"]}
    assert list(by_sport) == ["MLB", "NFL", "NBA", "CFB", "NHL"]
    assert by_sport["NHL"]["label"] == "NHL" and by_sport["NHL"]["account"]["funded"] is True
    assert by_sport["NHL"]["starting_capital"] == 1000.0
    assert before["totals"]["starting_capital"] == 5000.0
    assert before["combined_series"][0]["bankroll"] == 5000.0

    # Opening the era again changes nothing: one SEASON_OPENING contribution, counted once.
    again = _activate(session, s)
    assert again["funded_now"] is False
    session.commit()
    assert _sqlite_ledger(session, monkeypatch, DECIDE)["totals"]["starting_capital"] == 5000.0

    decision = _evaluate(session, s, now=DECIDE)["decisions"][0]
    assert decision["outcome"] == "QUALIFIED" and decision["position_id"]
    opened = _sqlite_ledger(session, monkeypatch, DECIDE + timedelta(minutes=1))
    nhl = next(row for row in opened["sports"] if row["sport"] == "NHL")
    assert nhl["open_count"] == 1 and nhl["settled_count"] == 0
    position = nhl["positions"][0]
    assert position["experiment"]["name"] == paper.experiment(session).name
    assert position["status"] == "OPEN" and position["entry"]["provider"] == "The Odds API"
    assert nhl["open_exposure"] == pytest.approx(position["stake"]) and opened["totals"]["open_count"] == 1
    assert opened["totals"]["available_capital"] == pytest.approx(5000.0 - position["stake"])

    side = decision["selection"]["side"]
    _result(session, game, reg=(3, 1) if side == "HOME" else (1, 3), decided="REGULATION")
    assert paper.settle(session, s, now=START + timedelta(hours=5))["settled"] == 1
    session.commit()
    settled = _sqlite_ledger(session, monkeypatch, START + timedelta(hours=6))
    nhl = next(row for row in settled["sports"] if row["sport"] == "NHL")
    assert nhl["settled_count"] == 1 and nhl["record"]["WIN"] == 1 and nhl["profit_loss"] > 0
    assert nhl["reconciled"] is True
    assert nhl["series"][-1]["kind"] == "SETTLEMENT" and nhl["series"][-1]["bankroll"] == nhl["realized_bankroll"]
    assert settled["totals"]["starting_capital"] == 5000.0
    assert settled["totals"]["profit_loss"] == pytest.approx(nhl["profit_loss"])
    assert settled["combined_series"][-1]["sport"] == "NHL"
    assert settled["combined_series"][-1]["bankroll"] == pytest.approx(5000.0 + nhl["profit_loss"])


# --------------------------------------------------------------- readiness --
def _rows(session):
    from app.models import OddsSnapshot, OfficialAspPosition, ShadowExperimentDecision

    return tuple(session.scalar(select(func.count()).select_from(m))
                 for m in (ShadowExperimentDecision, OfficialAspPosition, OddsSnapshot))


def test_readiness_reads_the_in_process_route_before_collection_and_writes_nothing(session, live):
    _s, forecast, _game = live
    counts = _rows(session)
    now = START - timedelta(hours=2)                         # before the T-75..T-35 buy window
    card = sport_readiness._nhl(session, now)
    assert not session.new and not session.dirty and _rows(session) == counts
    assert card["sport"] == "NHL" and card["policy_version"] == "AISP_NHL_PAPER_POLICY_V1"
    assert card["account"]["funded"] is True and card["account"]["contribution"] == 1000.0
    assert card["era"]["name"] == paper.experiment(session).name
    assert card["next_game"]["game_id"] == str(forecast.game_id)
    assert card["next_game"]["research_href"] == f"/nhl/games/{forecast.source_game_id}"
    assert card["forecast"]["state"] == "IN_PROCESS_FORECAST_AVAILABLE"
    assert card["forecast"]["identity"] == forecast.forecast_id and card["forecast"]["route"] == "AISP_IN_PROCESS"
    assert card["eligibility"] == {**card["eligibility"], "eligible": True, "reason": "AISP_IN_PROCESS_ELIGIBLE",
                                   "contract": "AISP_NHL_IN_PROCESS_ELIGIBILITY_V1", "route": "AISP_IN_PROCESS"}
    # The stored pair was quoted after `now`: the checkpoint could not hold it yet, and neither does readiness.
    assert card["price"]["state"] == "COLLECTION_SCHEDULED" and "blocker" not in card["price"]
    assert card["decision"]["state"] == "WINDOW_NOT_OPEN" and card["decision"]["recorded"] is None
    assert card["funding"]["shared_allowance_effective"] is True and card["funding"]["room_usd"] == 100.0
    assert card["pipeline"] == {**card["pipeline"], "ready": True, "blocker": None}


def test_readiness_in_the_window_sees_the_pair_then_the_recorded_decision(session, live):
    s, forecast, _game = live
    card = sport_readiness._nhl(session, DECIDE)
    assert card["price"]["state"] == "FRESH_PRICE" and card["price"]["pairs"] == 1
    assert card["decision"]["state"] == "WINDOW_OPEN"
    decision = _evaluate(session, s, now=DECIDE)["decisions"][0]
    after = sport_readiness._nhl(session, DECIDE + timedelta(minutes=1))
    assert after["decision"]["recorded"]["decision_id"] == decision["decision_id"]
    assert after["decision"]["recorded"]["recommendation"] == "QUALIFIED_BET"
    assert after["decision"]["recorded"]["reason"] == "ALL_GATES_PASSED"
    # Sport-wide count (the per-game count binds the game id as text, which only PostgreSQL casts to uuid).
    assert after["position"]["open"] == 1 and after["position"]["state"] == "OPEN_POSITIONS"
    assert after["funding"]["room_usd"] == pytest.approx(100.0 - decision["selection"]["terms"]["stake"])


def test_readiness_names_the_missing_forecast_and_the_switches(session, settings, monkeypatch):
    import app.core.config as config
    from app.nhl.identity import ensure_game
    from tests import nhl_fixtures as fx

    s = _settings(settings, nhl_paper_checkpoint_enabled=False, nhl_forecast_generation_enabled=True)
    monkeypatch.setattr(config, "get_settings", lambda: s)
    ensure_game(session, fx.game_block("2026020300", "BOS", "NYR", START))
    session.commit()
    now = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)          # 10:00 ET, after the 09:20 run
    card = sport_readiness._nhl(session, now)
    assert card["forecast"]["state"] == "FORECAST_AWAITING_GENERATOR"
    assert card["forecast"]["next_generation_at"] == "2026-10-20T19:20:00+00:00"   # 15:20 ET
    assert card["eligibility"]["eligible"] is False and card["eligibility"]["reason"] == "NO_FORECAST"
    assert card["account"]["funded"] is False and card["era"] is None
    assert card["decision"]["state"] == "OWNER_INACTIVE"
    assert card["pipeline"]["ready"] is False
    blocker = card["pipeline"]["blocker"]
    assert blocker.startswith("No NHL paper era is open") and "checkpoint is not active" in blocker
    assert "no admissible nhl forecast" in blocker.lower()


def test_the_served_readiness_lists_nhl_beside_the_other_sports(session, live):
    payload = sport_readiness.sport_readiness(session, now=START - timedelta(hours=2))
    codes = [entry["sport"] for entry in payload["sports"]]
    assert codes == ["MLB", "NFL", "NBA", "CFB", "NHL"]
    nhl = payload["sports"][-1]
    assert nhl.get("read_state") != "READ_FAILED", nhl
    assert nhl["forecast"]["state"] == "IN_PROCESS_FORECAST_AVAILABLE"


def test_a_failed_nhl_read_never_blanks_the_others(session, live, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("nhl read")

    monkeypatch.setattr(sport_readiness, "BUILDERS", (("NHL", boom),))
    payload = sport_readiness.sport_readiness(session, now=START - timedelta(hours=2))
    assert payload["sports"][0]["read_state"] == "READ_FAILED" and payload["sports"][0]["sport"] == "NHL"


# ---------------------------------------------------------- display labels --
def test_nhl_decisions_carry_their_recorded_edge_figures(session, live):
    from app.services.edge_metrics import edge_metrics
    from app.services.research_paper_policy import NHL

    s, _forecast, _game = live
    decision = _evaluate(session, s, now=DECIDE)["decisions"][0]
    selection = decision["selection"]
    metrics = edge_metrics("NHL", selection["american_odds"], selection["win_probability"], None, None, None,
                           {"selection": selection})
    assert metrics["policy"] == NHL.policy_version
    assert metrics["inputs"]["channel"] == "FINAL_TWO_WAY_INCLUDING_OT_SO"
    assert metrics["inputs"]["model_probability"] == pytest.approx(selection["win_probability"])
    edge, ret = metrics["fields"]
    assert edge["value"] == pytest.approx(selection["terms"]["edge"])
    assert ret["value"] == pytest.approx(selection["terms"]["expected_return_per_dollar"])
    assert [t["passed"] for t in metrics["tests"]] == [True, True]
    assert metrics["tests"][0]["threshold"] == NHL.min_adjusted_edge


def test_a_planned_nhl_account_names_the_live_in_process_route(session, settings):
    account = ExperimentAccountService(session, settings).ensure_account("NHL", "2027-28")
    note = account.status_note
    assert "AISP_NHL_PAPER_POLICY_V1" in note and "in-process" in note
    assert "No NHL ingestion" not in note


def test_the_registry_note_no_longer_says_nhl_is_not_live():
    from app.sports.registry import get_league

    nhl = get_league("NHL")
    assert "AISP_NHL_PAPER_POLICY_V1" in nhl.official_positions_note
    assert "not live" not in nhl.official_positions_note and "built and OFF" not in nhl.official_positions_note
    assert nhl.official_positions_supported is False and nhl.navigable is False     # flags unchanged
