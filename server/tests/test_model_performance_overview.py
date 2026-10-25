"""The shared Model Performance Overview calculation (page and brief use the same code).

Each test is one way the overview could mislead:

* the same game counted twice, or FINAL and days-old CURRENT forecasts pooled silently;
* a decision scored with a forecast it did not use, or one that became available after it;
* a native three-class Brier compared with a two-way market Brier, or "native = 2 x two-way"
  asserted where tie mass exists;
* a selection conclusion drawn from an interval that does not support one;
* CFB market movement confused with model disagreement;
* "predicted 43.47, actual 49" read as an overestimate;
* the page and the brief printing different numbers for the same snapshot.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from math import log
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import model_performance_overview as mpo

KO = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
T = KO - timedelta(minutes=30)


def evaluation(*, game=None, engine="NFL_M1_XGBOOST", version="v1", snapshot="FINAL_PREGAME", lead_h=0.5,
               home=0.6, away=0.37, tie=0.03, hs=24, as_=20, pred_home=24.0, pred_away=21.0, current=True,
               league="NFL", shadow=None):
    outcome = "HOME" if hs > as_ else "AWAY" if as_ > hs else "TIE"
    return SimpleNamespace(
        id=uuid4(), canonical_game_id=game or uuid4(), event_id="e", league_code=league, engine_id=engine,
        engine_version=version, snapshot_type=snapshot, snapshot_version=1, is_current=current,
        forecast_published_utc=KO - timedelta(hours=lead_h), kickoff_utc=KO,
        published_before_kickoff_seconds=int(lead_h * 3600), forecast_identity="f", shadow_forecast_id=shadow,
        detail={"published_probabilities": {"home": home, "away": away, "tie": tie}},
        home_score=hs, away_score=as_, actual_outcome=outcome, predicted_outcome="HOME" if home > away else "AWAY",
        winner_correct=(home > away) == (outcome == "HOME"), log_loss=None,
        brier_score=mpo.brier_three_class(home, away, tie, outcome),
        predicted_home_score=pred_home, predicted_away_score=pred_away,
        predicted_margin=pred_home - pred_away, actual_margin=hs - as_,
        margin_error=(pred_home - pred_away) - (hs - as_), predicted_total=pred_home + pred_away,
        actual_total=hs + as_, total_error=(pred_home + pred_away) - (hs + as_),
        interval_coverage=None, created_at=KO,
    )


def decision(*, available=T - timedelta(minutes=10), home=0.7, away=0.3, tie=0.0, hs=21, as_=17, q=0.55,
             rec="NO_BET", side=None, funded=False, sport="NFL", engine="NFL_M1_XGBOOST", version=7,
             snapshot="FINAL_PREGAME", same=None, q_avail=None, q_early=None, position=None, table="shadow_forecasts"):
    market = lambda p, t: None if p is None else {"p_home": p, "n_books": 5, "books": [], "reference_time_utc": t.isoformat(),
                                                  "oldest_leg_utc": None, "newest_leg_utc": None}
    return mpo.decision_record({
        "sport": sport, "experiment_version": version, "engine_id": engine, "decision_id": str(uuid4()),
        "game_id": uuid4(), "home": "H", "away": "A", "kickoff": KO, "decision_time": T, "governing_rule": "X",
        "n_decisions": 1, "recommendation": rec, "selected_side": side, "funded": funded, "position": position,
        "forecast": {"table": table, "row_id": "r", "snapshot": snapshot, "snapshot_type": snapshot,
                     "available_utc": available, "published_utc": available, "p_home": home, "p_away": away, "p_tie": tie},
        "final": {"home": hs, "away": as_}, "market": market(q, T),
        "market_at_availability": market(q_avail, available) if available else None,
        "market_earliest_retained": market(q_early, T - timedelta(hours=4)), "evaluation_forecast_same": same})


# -- one primary evaluation per game; FINAL / CURRENT split ---------------------------------------

def test_one_primary_evaluation_per_game_and_model_keeps_the_latest_pre_kickoff_publication() -> None:
    game = uuid4()
    early = evaluation(game=game, snapshot="CURRENT", lead_h=130)
    late = evaluation(game=game, snapshot="FINAL_PREGAME", lead_h=0.5)
    superseded = evaluation(game=game, lead_h=0.2, current=False)
    other_model = evaluation(game=game, version="v2", lead_h=3)
    kept = mpo.primary_evaluations([early, superseded, late, other_model])
    assert len(kept) == 2 and late in kept and other_model in kept


def test_forecast_type_and_lead_bucket_split_final_from_current() -> None:
    assert (mpo.forecast_type("FINAL_PREGAME"), mpo.forecast_type("CURRENT"), mpo.forecast_type("X")) == (
        "FINAL", "CURRENT", "PROSPECTIVE")
    assert (mpo.lead_bucket(0.5), mpo.lead_bucket(3), mpo.lead_bucket(130.5)) == ("LE_1H", "1_6H", "GT_72H")
    rows = [mpo.game_record(evaluation(snapshot="FINAL_PREGAME", lead_h=0.5)),
            mpo.game_record(evaluation(snapshot="CURRENT", lead_h=154.5))]
    assert [(g["forecast_type"], g["n"]) for g in mpo.summarize(rows)] == [("ALL", 2), ("FINAL", 1), ("CURRENT", 1)]
    assert len(mpo.filter_rows(rows, forecast_kind="CURRENT")) == 1 and len(mpo.filter_rows(rows, lead="GT_72H")) == 1
    rows.append(mpo.game_record(evaluation(league="MLB", engine="MLB_V18_WHOLE_GAME")))
    assert sorted({g["sport"] for g in mpo.summarize(rows)}) == ["MLB", "NFL"]


# -- decision-bound forecast and its availability clock ---------------------------------------------

def test_a_forecast_available_after_the_decision_is_excluded_with_no_allowance() -> None:
    late = decision(available=T + timedelta(minutes=1))
    assert late["availability_status"] == "NOT_AVAILABLE_BEFORE_DECISION"
    assert late["delta_log_loss"] is None and late["comparable"] is False and late["selected_side_model_p"] is None
    on_time = decision(available=T)
    assert on_time["availability_status"] == "AVAILABLE" and on_time["comparable"] is True
    group = mpo.decision_metrics([late, on_time])
    assert group["excluded_not_available_before_decision"] == 1
    assert group["market_decision_time"]["n"] == 1


def test_decision_level_scores_use_the_decisions_own_forecast_not_the_scored_one() -> None:
    d = decision(home=0.7, away=0.3, q=0.55, hs=21, as_=17, same=False)
    assert d["p_home_two_way"] == pytest.approx(0.7)
    assert d["delta_log_loss"] == pytest.approx(-log(0.7) + log(0.55))
    group = mpo.decision_metrics([d])
    assert group["evaluation_joined"] == 1 and group["evaluation_forecast_differs"] == 1


def test_comparable_means_available_within_six_hours_before_the_decision() -> None:
    assert decision(available=T - timedelta(hours=5))["comparable"] is True
    stale = decision(available=T - timedelta(hours=86), snapshot="CURRENT")
    assert stale["comparable"] is False and stale["delta_log_loss"] is not None
    g = mpo.decision_metrics([stale, decision()])
    assert g["market_comparable"]["n"] == 1 and g["market_decision_time"]["n"] == 2


def test_governing_decision_prefers_funded_then_latest_before_kickoff() -> None:
    early, late = {"decision_time": KO - timedelta(hours=2)}, {"decision_time": KO - timedelta(minutes=20)}
    funded = {"decision_time": KO - timedelta(hours=3), "funded": True}
    after = {"decision_time": KO + timedelta(minutes=5)}
    assert mpo.governing_decision([early, late, after], KO) == (late, "LATEST_FINAL_PREGAME_BEFORE_KICKOFF")
    assert mpo.governing_decision([early, late, funded], KO) == (funded, "FUNDED_POSITION_DECISION")


# -- Brier: native versus conditional two-way -------------------------------------------------------

def test_native_brier_is_twice_two_way_only_without_tie_mass() -> None:
    assert mpo.brier_three_class(0.6, 0.4, 0.0, "HOME") == pytest.approx(2 * mpo.two_way_brier_from_probabilities(0.6, 0.4, "HOME"))
    assert mpo.two_way(0.57, 0.38) == pytest.approx(0.6)
    assert mpo.two_way_brier_from_probabilities(0.57, 0.38, "TIE") is None
    nfl = [mpo.game_record(evaluation(home=0.57, away=0.38, tie=0.05))]
    mlb = [mpo.game_record(evaluation(home=0.6, away=0.4, tie=0.0, league="MLB", engine="MLB_V18_WHOLE_GAME"))]
    check_nfl, check_mlb = mpo.brier_checks(nfl), mpo.brier_checks(mlb)
    assert check_nfl["native_matches_stored"] == 1 and check_nfl["native_max_abs_diff"] == pytest.approx(0)
    assert check_nfl["twice_two_way_fails"] == 1 and check_nfl["fails_with_tie_mass"] == 1
    assert check_mlb["twice_two_way_holds"] == 1 and check_mlb["twice_two_way_fails"] == 0
    assert "conditional two-way" in check_nfl["market_comparison_uses"]


# -- sign conventions -------------------------------------------------------------------------------

def test_predicted_total_below_actual_is_an_underestimate() -> None:
    record = mpo.game_record(evaluation(pred_home=24.551, pred_away=18.9153, hs=14, as_=35))
    assert record["total_error"] == pytest.approx(-5.5337, abs=1e-4) and record["total_direction"] == "UNDERESTIMATE"
    assert record["margin_error"] == pytest.approx(26.6357, abs=1e-4)
    assert record["forecast_favourite"] == "HOME" and record["forecast_favourite_correct"] is False
    assert mpo.error_direction(-5.53) == "UNDERESTIMATE" and mpo.error_direction(19.69) == "OVERESTIMATE"


def test_forecast_favourite_and_wager_are_separate_fields() -> None:
    d = decision(rec="QUALIFIED_BET", side="AWAY", funded=True,
                 position={"position_id": "p", "side": "AWAY", "result": "LOSS", "status": "SETTLED", "stake": 5.0})
    ev = evaluation(hs=24, as_=20)
    record = mpo.game_record(ev, decision=d)
    assert record["forecast_favourite"] == "HOME" and record["forecast_favourite_correct"] is True
    assert record["wager"]["side"] == "AWAY" and record["wager"]["result"] == "LOSS"
    assert mpo.game_record(evaluation())["wager"] is None


# -- market reference ---------------------------------------------------------------------------------

def q(side, book, obs, dec, *, source="THE_ODDS_API", avail=None):
    return {"side": side, "book": book, "source": source, "observed_at": obs, "available_as_of": avail or obs, "decimal": dec}


def test_market_reference_is_the_median_of_visible_fresh_same_book_pairs() -> None:
    quotes = [
        q("HOME", "a", T - timedelta(minutes=5), 1.8), q("AWAY", "a", T - timedelta(minutes=5), 2.1),
        q("HOME", "b", T - timedelta(minutes=10), 1.9), q("AWAY", "b", T - timedelta(minutes=10), 2.0),
        q("HOME", "c", T - timedelta(minutes=10), 1.7), q("AWAY", "c", T - timedelta(minutes=10), 2.3),
        q("HOME", "d", T - timedelta(minutes=5), 3.0), q("AWAY", "d", T - timedelta(minutes=300), 1.3),
        q("HOME", "e", T - timedelta(minutes=5), 3.0), q("AWAY", "e", T - timedelta(minutes=50), 1.3),
        q("HOME", "f", T - timedelta(minutes=2), 3.0, avail=T + timedelta(minutes=1)),
        q("AWAY", "f", T - timedelta(minutes=2), 1.3, avail=T + timedelta(minutes=1)),
        q("HOME", "sgo:a", T - timedelta(minutes=60), 1.5, source="SPORTSGAMEODDS"),
        q("AWAY", "sgo:a", T - timedelta(minutes=60), 2.6, source="SPORTSGAMEODDS"),
    ]
    ref = mpo.market_reference(quotes, T)
    assert ref["books"] == ["a", "b", "c"]
    expected = sorted([(1 / 1.8) / (1 / 1.8 + 1 / 2.1), (1 / 1.9) / (1 / 1.9 + 1 / 2.0), (1 / 1.7) / (1 / 1.7 + 1 / 2.3)])[1]
    assert ref["p_home"] == pytest.approx(expected)
    assert mpo.earliest_reference(quotes, T - timedelta(hours=3), T)["reference_time_utc"] == (T - timedelta(minutes=60)).isoformat()


# -- selection: conclusions only where supported ------------------------------------------------------

def test_selection_statement_draws_no_conclusion_from_a_small_or_inclusive_interval() -> None:
    small = [decision(rec="QUALIFIED_BET", side="HOME", home=0.55, away=0.45, hs=10, as_=20) for _ in range(7)] + \
        [decision(rec="QUALIFIED_BET", side="HOME", home=0.55, away=0.45) for _ in range(2)]
    text = mpo.selection_statement(mpo.decision_metrics(small)["selection"]["selected"], "CFB")
    assert "too small to support a conclusion" in text
    big = [decision(rec="QUALIFIED_BET", side="HOME", home=0.6, away=0.4, hs=10, as_=20) for _ in range(20)] + \
        [decision(rec="QUALIFIED_BET", side="HOME", home=0.6, away=0.4) for _ in range(5)]
    block = mpo.decision_metrics(big)["selection"]["selected"]
    assert block["ci_excludes_model_p"] is True
    assert "won less often than the model said" in mpo.selection_statement(block, "MLB")


def _sides(wins: int, n: int, p: float) -> list[dict]:
    return [decision(rec="QUALIFIED_BET", side="HOME", home=p, away=1 - p, hs=21, as_=17) for _ in range(wins)] + \
        [decision(rec="QUALIFIED_BET", side="HOME", home=p, away=1 - p, hs=17, as_=21) for _ in range(n - wins)]


def test_a_rate_within_one_point_of_the_interval_bound_is_borderline_not_a_verdict() -> None:
    block = mpo.decision_metrics(_sides(23, 59, 0.5179))["selection"]["selected"]
    ci = block["side_win_rate_ci95"]
    assert ci[1] == pytest.approx(0.517, abs=1e-3)
    assert block["ci_excludes_model_p"] is True  # outside by ~0.1 pt ...
    assert mpo.borderline(ci, 0.5179) == ("upper", ci[1])  # ... which is inside the 1-pt band
    text = mpo.selection_statement(block, "MLB")
    assert "borderline: the interval's upper bound (51.7%) is within 1 pt of the model's rate (51.8%); not established" in text
    assert "won less often than the model said" not in text
    assert mpo.selection_is_lead_worthy(block) is False
    # The same wording replaces "narrowly excludes" for a small sample.
    small = mpo.selection_statement(mpo.decision_metrics(_sides(2, 9, 0.557))["selection"]["selected"], "CFB")
    assert "narrowly" not in small and "too small to support a conclusion" in small


def test_a_borderline_selection_statement_never_becomes_a_lead_finding() -> None:
    borderline = mpo.Loaded(games=[], decisions=_sides(23, 59, 0.5179), positions_total=0, positions_open=0, as_of=KO)
    payload = mpo.build_payload(borderline, now=KO)
    assert not any(f["family"] == "selection" for f in payload["narrative"]["findings"])
    assert any("borderline" in s for s in payload["narrative"]["selection_statements"])
    clear = mpo.Loaded(games=[], decisions=_sides(10, 40, 0.6), positions_total=0, positions_open=0, as_of=KO)
    assert any(f["family"] == "selection" for f in mpo.build_payload(clear, now=KO)["narrative"]["findings"])


# -- CFB: availability vs decision time ------------------------------------------------------------

def test_cfb_movement_separates_market_movement_from_model_disagreement() -> None:
    rows = [decision(sport="CFB", engine="CFB_GEN1_JOINT_BASELINE", version=9, table="cfb_research_forecasts",
                     available=T - timedelta(hours=150), home=0.7, away=0.3, q=0.3, q_early=0.32, hs=7, as_=30),
            decision(sport="CFB", engine="CFB_GEN1_JOINT_BASELINE", version=9, table="cfb_research_forecasts",
                     available=T - timedelta(hours=150), home=0.4, away=0.6, q=0.7, q_early=0.65, hs=30, as_=7)]
    out = mpo.cfb_publication_vs_decision(rows)
    assert out["n_at_availability"] == 0 and out["n_earliest_retained_and_decision"] == 2 and out["unsupported"]
    mv = out["disagreement_earliest_to_decision"]
    # Game 1: |0.7-0.32| = 0.38 -> |0.7-0.30| = 0.40 (grew 0.02); game 2: 0.25 -> 0.30 (grew 0.05).
    assert mv["mean_abs_gap_change"] == pytest.approx(0.035)
    assert mv["share_grew"] == 1.0
    # The market moved toward the result in both games (away won game 1: 0.32 -> 0.30 home; home won game 2).
    assert mv["share_market_moved_toward_outcome"] == 1.0
    with_avail = [decision(sport="CFB", engine="CFB_GEN1_JOINT_BASELINE", version=9, table="cfb_research_forecasts",
                           available=T - timedelta(hours=10), q=0.5, q_avail=0.6)]
    assert mpo.cfb_publication_vs_decision(with_avail)["n_both"] == 1


# -- narrative, filters and page/brief agreement -----------------------------------------------------

def loaded() -> mpo.Loaded:
    games = [mpo.game_record(evaluation(snapshot="FINAL_PREGAME", lead_h=0.5)) for _ in range(4)] + \
        [mpo.game_record(evaluation(snapshot="CURRENT", lead_h=140, hs=10, as_=30)) for _ in range(3)]
    decisions = [decision() for _ in range(3)] + [decision(available=T + timedelta(minutes=5))]
    return mpo.Loaded(games=games, decisions=decisions, positions_total=0, positions_open=0, as_of=KO)


def test_findings_follow_the_filtered_snapshot_and_name_their_experiment() -> None:
    everything = mpo.build_payload(loaded(), now=KO)
    keys = [f["key"] for f in everything["narrative"]["findings"]]
    assert "timing:NFL" in keys and "availability" in keys
    for finding in everything["narrative"]["findings"]:
        assert finding["experiment"] in mpo.EXPERIMENTS.values() and finding["game_ids"]
    current = mpo.build_payload(loaded(), sport="NFL", forecast_kind="CURRENT", now=KO)
    assert current["scope"]["label"] == "sport NFL · CURRENT forecasts · all lead times"
    assert current["scope"]["n_games"] == 3


def test_endpoint_returns_exactly_the_service_payload_for_the_same_snapshot(client, monkeypatch) -> None:
    fixture = loaded()
    monkeypatch.setattr(mpo, "load", lambda session: fixture)
    monkeypatch.setattr(mpo, "_wagering", lambda session, sport, now: {})
    body = client.get("/api/v1/research/model-performance/overview?sport=NFL").json()
    expected = json.loads(json.dumps(mpo.build_payload(fixture, sport="NFL", forecast_kind="ALL", lead="ALL", wagering={}),
                                     default=str))
    body.pop("generated_at_utc"); expected.pop("generated_at_utc")
    assert body == expected


def test_overview_endpoint_is_read_only_and_states_that_it_creates_no_position(client) -> None:
    response = client.get("/api/v1/research/model-performance/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == mpo.SCHEMA_VERSION and body["games"] == [] and body["decisions"] == []
    assert body["authority"]["creates_positions"] is False
    assert client.get("/api/v1/research/model-performance/overview?sport=XFL").status_code == 404
    assert client.get("/api/v1/research/model-performance/overview?forecast_type=EARLY").status_code == 422
