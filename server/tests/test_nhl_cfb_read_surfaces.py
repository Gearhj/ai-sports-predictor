"""NHL and College Football read surfaces that told the reader something false (MAIN audit, 2026-10-16).

TEST FIXTURES ONLY: every forecast, quote, account, decision and result is invented in a throwaway
SQLite database (the NHL paper lifecycle's and the CFB binding suite's own builders).

What each test guards against:

* the NHL board saying "no market prices collected" and "no paper positions" while the era is open,
  quotes are stored and a position exists -- and losing those statements when no era exists;
* an NHL decision's link (AISP game id) answering 404;
* Wager Results marking NHL research-only, so its funded positions never reach the table;
* the slate coverage carrying no NHL model, or reading the forecast another way than the checkpoint;
* College Football's no-price copy naming the dead SportsGameOdds lane (authorization expired)
  while The Odds API CFB lane is on;
* the CFB board formatting the whole season before filtering by date, and re-parsing every
  eligibility record once per game (4-13 minutes in production) -- without changing one card.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.nhl import paper
from app.nhl.read_service import NhlReadService, paper_statements, resolve_source_game_id
from tests.test_nhl_ledger_readiness import _live_settings
from tests.test_nhl_paper_lifecycle import (
    DECIDE, START, _activate, _evaluate, _in_process_forecast, _quotes, _result,
)


@pytest.fixture
def hockey(session, settings, monkeypatch):
    """An open NHL era, one in-process forecast and one stored The Odds API pair."""

    import app.core.config as config

    s = _live_settings(settings)
    monkeypatch.setattr(config, "get_settings", lambda: s)
    forecast, game = _in_process_forecast(session)
    stored, _receipts = _quotes(session)
    assert stored["created"] == 2
    _activate(session, s)
    session.commit()
    return s, forecast, game


def _writes(session):
    from app.models import OddsSnapshot, OfficialAspPosition, ShadowExperimentDecision

    return tuple(session.scalar(select(func.count()).select_from(m))
                 for m in (ShadowExperimentDecision, OfficialAspPosition, OddsSnapshot))


# ---------------------------------------------------------------- NHL board --
def test_without_an_era_the_board_keeps_saying_research_only(session, settings, monkeypatch):
    import app.core.config as config

    monkeypatch.setattr(config, "get_settings", lambda: settings)
    _in_process_forecast(session)
    session.commit()
    markets, wagering = paper_statements(session)
    assert markets["status"] == "NO_NHL_MARKET_PRICES_COLLECTED"
    assert wagering["enabled"] is False and "wagering is disabled" in wagering["statement"]
    row = NhlReadService(session).board(now=START - timedelta(hours=3))["games"][0]
    assert row["forecast_label"] == "Research forecast — wagering disabled" and row["paper"] is None


def test_the_live_board_states_stored_quotes_the_era_and_the_recorded_position(session, hockey):
    s, forecast, game = hockey
    before = NhlReadService(session).board(now=DECIDE)
    assert before["markets"]["status"] == "NHL_MARKET_PRICES_COLLECTED" and before["markets"]["quotes_stored"] == 2
    assert "75 to 35 minutes before puck drop" in before["markets"]["statement"]
    assert before["wagering"]["enabled"] is True and before["wagering"]["mode"] == "VIRTUAL_PAPER"
    assert "0 paper positions recorded" in before["wagering"]["statement"]
    row = before["games"][0]
    assert row["forecast_label"].startswith("Paper forecast") and row["paper"] is None
    assert before["paper_counts"] == {"decisions": 0, "positions": 0}

    decision = _evaluate(session, s, now=DECIDE)["decisions"][0]
    assert decision["outcome"] == "QUALIFIED"
    counts = _writes(session)
    after = NhlReadService(session).board(now=DECIDE + timedelta(minutes=1))
    assert _writes(session) == counts and not session.new and not session.dirty      # a read writes nothing
    row = after["games"][0]
    assert row["paper"]["decision"]["recommendation"] == "QUALIFIED_BET"
    assert row["paper"]["decision"]["reason"] == "ALL_GATES_PASSED"
    assert row["paper"]["decision"]["edge"] == pytest.approx(decision["selection"]["terms"]["edge"])
    assert row["paper"]["position"]["status"] == "OPEN"
    assert row["paper"]["position"]["stake"] == pytest.approx(decision["selection"]["terms"]["stake"])
    assert after["wagering"]["open_positions"] == 1 and "1 paper position recorded (1 open, 0 settled)" in \
        after["wagering"]["statement"]

    side = decision["selection"]["side"]
    _result(session, game, reg=(3, 1) if side == "HOME" else (1, 3), decided="REGULATION")
    assert paper.settle(session, s, now=START + timedelta(hours=5))["settled"] == 1
    session.commit()
    settled = NhlReadService(session).board(now=START + timedelta(hours=6))
    position = settled["games"][0]["paper"]["position"]
    assert position["status"] == "SETTLED" and position["result"] == "WIN" and position["profit_loss"] > 0
    assert settled["wagering"]["settled_positions"] == 1


def test_an_aisp_game_id_opens_the_nhl_game_page(session, hockey, client):
    _s, forecast, _block = hockey
    game = forecast.game_id                                          # AISP's canonical game id
    assert resolve_source_game_id(session, str(game)) == forecast.source_game_id
    assert resolve_source_game_id(session, forecast.source_game_id) == forecast.source_game_id
    assert resolve_source_game_id(session, "123") is None
    assert resolve_source_game_id(session, "7a242ab1-ee67-481b-9156-c369cd806687") is None

    by_uuid = client.get(f"/api/v1/nhl/games/{game}")             # the reported 404
    assert by_uuid.status_code == 200, by_uuid.text
    assert by_uuid.json()["source_game_id"] == forecast.source_game_id
    assert by_uuid.json()["aisp_game_id"] == str(game)
    assert client.get(f"/api/v1/nhl/games/{forecast.source_game_id}").status_code == 200
    assert client.get("/api/v1/nhl/games/7a242ab1-ee67-481b-9156-c369cd806687").status_code == 404
    assert client.get("/api/v1/nhl/games/123").status_code == 404


# ----------------------------------------------------------- wager results --
def test_wager_results_reads_nhl_positions_like_any_funded_account(session, hockey):
    from app.services.wager_results_read import wager_results

    s, _forecast, _game = hockey
    decision = _evaluate(session, s, now=DECIDE)["decisions"][0]
    session.commit()
    out = wager_results(session, sport="NHL", now=DECIDE + timedelta(minutes=1))
    assert out["research_only_sports"] == {}
    assert [o["code"] for o in out["options"]["sports"]][-1] == "NHL"
    (row,) = out["rows"]
    assert row["sport"] == "NHL" and row["engine"] == "NHL_GEN1_FINAL_TWO_WAY" and row["status"] == "OPEN"
    assert row["binding_status"] == "OK" if "binding_status" in row else True
    assert row["raw_probability"] == pytest.approx(decision["selection"]["win_probability"])
    assert row["recorded_return"] == pytest.approx(decision["selection"]["terms"]["expected_return_per_dollar"])
    assert row["comparison_probability"] == pytest.approx(row["raw_probability"])   # an NHL final never ties
    assert "RETURN_NOT_RECORDED" not in row["flags"]


# ----------------------------------------------------------------- coverage --
def test_slate_coverage_reads_the_forecast_the_checkpoint_would_hold(session, hockey):
    from app.services import command_center

    _s, forecast, _block = hockey
    game = forecast.game_id
    counts = _writes(session)
    out = command_center._nhl_coverage(session, [(str(game), START)], now=START - timedelta(hours=2))
    assert _writes(session) == counts
    model = out["models"][str(game)]
    assert model["engine_id"] == "NHL_GEN1_FINAL_TWO_WAY" and model["forecast_id"] == forecast.forecast_id
    assert model["controlling_channel"] == "FINAL_TWO_WAY_INCLUDING_OT_SO" and model["route"] == "AISP_IN_PROCESS"
    assert model["home_two_way"] == pytest.approx(forecast.final_home_probability, abs=1e-4)
    assert model["separate_channels"]["regulation_tie"] == forecast.regulation_tie_probability
    assert out["enabled"] is True and out["buy_opens"] == timedelta(minutes=75)
    # A replay before the forecast existed holds nothing, exactly like the checkpoint.
    early = command_center._nhl_coverage(session, [(str(game), START)], now=forecast.imported_at - timedelta(hours=1)
                                         if forecast.imported_at.tzinfo else
                                         forecast.imported_at.replace(tzinfo=UTC) - timedelta(hours=1))
    assert early["models"][str(game)] is None
    assert "NHL" in command_center.LISTED_LEAGUES


# --------------------------------------------------------- CFB price copy --
def test_the_cfb_price_note_names_the_odds_api_lane_when_it_is_on(settings, monkeypatch, session):
    import app.core.config as config
    from app.services import command_center, pregame_status

    on = settings.model_copy(update={"cfb_odds_api_enabled": True})
    monkeypatch.setattr(config, "get_settings", lambda: on)
    command_center._CFB_PRICE_NOTE.clear()
    note = command_center._cfb_price_note(session)
    assert note["reason"] == pregame_status.ODDS_API_LANE_ACTIVE and note["lane_enabled"] is True
    assert "75 to 35 minutes before kickoff" in note["detail"]
    assert "authorization" not in note["detail"].lower() and "SportsGameOdds" not in note["detail"]
    collection = pregame_status.cfb_collection(note)
    assert collection["active"] is True and collection["provider"] == "The Odds API"
    kickoff = datetime(2026, 10, 17, 19, 30, tzinfo=UTC)
    game = command_center._game_collection(collection, kickoff)
    assert game["opens_at"] == "2026-10-17T18:15:00+00:00" and game["closes_at"] == "2026-10-17T18:55:00+00:00"
    status = pregame_status.cfb_market_status([], 0, game, {"The Odds API"}, now=kickoff - timedelta(hours=3),
                                              opens=kickoff - timedelta(minutes=30),
                                              closes=kickoff - timedelta(minutes=15))
    assert status["state"] == pregame_status.NO_QUOTES_YET      # a collector IS coming, not "inactive"

    # Switching the lane off is seen at once (the ten-minute memo is keyed on the switch).
    off = settings.model_copy(update={"cfb_odds_api_enabled": False})
    monkeypatch.setattr(config, "get_settings", lambda: off)
    assert command_center._cfb_price_note(session)["reason"] != pregame_status.ODDS_API_LANE_ACTIVE
    command_center._CFB_PRICE_NOTE.clear()


# ---------------------------------------------------------- CFB board cost --
def test_the_eligibility_index_answers_exactly_like_the_per_call_scan(session, hockey):
    from app.services.research_paper_policy import NHL, EligibilityIndex, eligibility_status
    from tests.test_nhl_paper_lifecycle import _eligibility

    _s, forecast, _game = hockey
    _eligibility(session, forecast)
    session.commit()
    index = EligibilityIndex(session, NHL)
    for fid in (forecast.forecast_id, "nhl-gen1:unknown"):
        plain = eligibility_status(session, NHL, forecast_id=fid, event_id=None)
        indexed = eligibility_status(session, NHL, forecast_id=fid, event_id=None, index=index)
        assert indexed == plain
    assert index.candidates_seen == 1


def test_the_cfb_board_filters_by_date_before_formatting_and_parses_eligibility_once(session, monkeypatch):
    from app.cfb import read_service
    from app.services import research_paper_policy
    from tests.test_cfb_game_binding import _forecast

    sat = datetime(2026, 10, 17, 19, 30, tzinfo=UTC)
    _forecast(session, game_id="401000001", kickoff=sat)
    _forecast(session, game_id="401000002", home="Home B", away="Away B", home_id="11", away_id="12",
              kickoff=sat + timedelta(hours=4))
    # A later revision of the first game: only the current one is a card.
    revised = _forecast(session, game_id="401000001", joint=0.61, kickoff=sat)
    revised.exchange_published_utc = datetime(2026, 10, 16, 12, tzinfo=UTC)
    for day, gid in ((datetime(2026, 10, 10, 19, tzinfo=UTC), "401000003"),
                     (datetime(2026, 10, 24, 19, tzinfo=UTC), "401000004")):
        _forecast(session, game_id=gid, home=f"H{gid}", away=f"A{gid}", home_id=gid, away_id=gid + "9", kickoff=day)
    session.commit()

    full = read_service.research_board(session)                     # the whole season, as before
    expected = [f for f in full["forecasts"] if f["game"]["kickoff_utc"][:10] == "2026-10-17"
                or read_service._local_date(f["game"]["kickoff_utc"]) == "2026-10-17"]

    formatted, indexes = [], []
    real_fmt, real_index = read_service._fmt, research_paper_policy.EligibilityIndex

    def counting_fmt(row, session=None, index=None):
        formatted.append(row.source_game_id)
        return real_fmt(row, session, index)

    class CountingIndex(real_index):
        def __init__(self, *a, **k):
            indexes.append(1)
            super().__init__(*a, **k)

    monkeypatch.setattr(read_service, "_fmt", counting_fmt)
    monkeypatch.setattr(research_paper_policy, "EligibilityIndex", CountingIndex)
    board = read_service.research_board(session, date_iso="2026-10-17")
    assert sorted(formatted) == ["401000001", "401000002"]          # only the date's games are formatted
    assert indexes == [1]                                            # eligibility records parsed once per build
    assert [f["forecast_id"] for f in board["forecasts"]] == [f["forecast_id"] for f in expected]
    assert board["forecasts"][0]["forecast_id"] == revised.forecast_id
    strip = lambda f: {k: v for k, v in f.items() if k != "pipeline"}  # noqa: E731 -- clocks read at call time
    assert [strip(f) for f in board["forecasts"]] == [strip(f) for f in expected]
    assert board["count"] == 2
    assert "The Odds API CFB lane" in board["authority"]["market"]
    assert "only from the SportsGameOdds" not in board["authority"]["market"]
