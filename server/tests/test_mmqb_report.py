"""Monday Morning QB weekly report: each test is one way the report could mislead a reader.

* the model's predicted winner shown as the funded selection;
* a push counted as a loss, or dropped from the record;
* expected profit that ignores the tie refund, or disagrees with the recorded EV silently;
* a pending game (Sunday night, Monday night) shown as a result, or missing from the report;
* a later quote called a closing line;
* a quote that became available after the decision passed as clean;
* the page and the offline report printing different numbers for the same snapshot.
"""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.services import model_performance_overview as mpo
from app.services import mmqb_render
from app.services import mmqb_report as mm
from app.services import wager_results_read as wr

KO = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)
DECIDED = KO - timedelta(minutes=21, seconds=31)
NOW = datetime(2026, 9, 28, 2, 0, tzinfo=UTC)
EXP = str(uuid4())


def wrow(*, side="AWAY", away="SEA", home="WAS", pw=0.2575, pt=0.0249, american=330, stake=5.0, result=None,
         net=None, kickoff=KO, game_id=None):
    decimal = mm.american_to_decimal(american)
    ev = mm.nfl_ev(pw, pt, decimal)
    comparison, basis = wr._comparison("NFL", pw, pt)
    settled = result is not None
    return {
        "position_id": str(uuid4()), "decision_id": str(uuid4()), "game_id": game_id or str(uuid4()),
        "matchup": f"{away} @ {home}", "starts_at": kickoff.isoformat(), "slate_date": "2026-09-27", "sport": "NFL",
        "selected_side": side, "selection": f"{away if side == 'AWAY' else home} ML", "raw_probability": pw,
        "tie_probability": pt, "adjusted_probability": None, "comparison_probability": comparison,
        "comparison_basis": basis, "probability_valid": True, "recorded_return": ev, "estimated_profit": stake * ev,
        "entry_decimal": decimal, "entry_american": american, "sportsbook": "Fanduel", "stake": stake,
        "status": "SETTLED" if settled else "OPEN", "result": result, "settled": settled, "net_profit": net,
        "settled_at": (kickoff + timedelta(hours=4)).isoformat() if settled else None, "decision_at": DECIDED.isoformat(),
        "probability_band": wr.band_of(pw, wr.PROBABILITY_BANDS), "return_band": wr.band_of(ev, wr.RETURN_BANDS),
        "binding_status": "OK", "flags": [], "experiment_id": EXP, "experiment_name": "NFL paper",
    }


def forecast(*, p_home=0.72, p_away=0.2551, p_tie=0.0249, exp_home=24.0, exp_away=19.0, available=None,
             event="2026_03_SEA_WAS"):
    return {
        "row_id": str(uuid4()), "event_id": event, "season": 2026, "week": 3, "snapshot_type": "FINAL_PREGAME",
        "snapshot_version": 1, "engine": "NFL_M1_XGBOOST v1",
        "available_utc": (available or KO - timedelta(minutes=37)).isoformat(), "cutoff_utc": None,
        "p_home": p_home, "p_away": p_away, "p_tie": p_tie, "expected_home": exp_home, "expected_away": exp_away,
        "margin_distribution": [{"home_minus_away": m, "probability": 1 / 21} for m in range(-10, 11)],
        "total_distribution": [{"total_score": t, "probability": 1 / 21} for t in range(33, 54)],
        "joint_distribution": [], "prediction_interval": {"margin_lower": -8, "margin_upper": 8, "total_lower": 35,
                                                          "total_upper": 52}, "most_likely_scores": [],
    }


def detail(row, fc, *, quote_available=None, opposite=-450, american=None):
    american = american if american is not None else row["entry_american"]
    return {
        "recommended_at_utc": DECIDED.isoformat(), "created_at_utc": DECIDED.isoformat(),
        "quote_observed_at_utc": (DECIDED - timedelta(minutes=10)).isoformat(), "odds_snapshot_id": "snap-1",
        "market_outcome_id": "mo-1", "american_odds": american, "decimal_odds": row["entry_decimal"],
        "sportsbook": "sgo:fanduel", "selection": row["selection"], "shadow_forecast_id": fc["row_id"],
        "evidence": {"outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED", "shadow_forecast_row_id": fc["row_id"],
                     "selection": {"side": row["selected_side"], "american_odds": american, "opposite_american": opposite,
                                   "win_probability": row["raw_probability"], "tie_probability": row["tie_probability"],
                                   "expected_value": row["recorded_return"], "pair_age_minutes": 3.0},
                     "eligibility": {"reason": "PAPER_RESEARCH_ELIGIBILITY_BOUND"}},
        "staking_detail": {"bound_by": "position_cap"},
        "entry_snapshot": {"observed_at_utc": (DECIDED - timedelta(minutes=10)).isoformat(),
                           "available_as_of_utc": (quote_available or DECIDED - timedelta(minutes=5)).isoformat(),
                           "created_at_utc": None, "decimal": row["entry_decimal"], "american": american,
                           "book_key": "sgo:fanduel", "source": "SPORTSGAMEODDS", "sportsbook_id": "book-1"},
    }


def decision(row, fc, *, final=None, market_home=0.75):
    return mpo.decision_record({
        "sport": "NFL", "experiment_version": 1, "engine_id": "NFL_M1_XGBOOST", "decision_id": row["decision_id"],
        "game_id": row["game_id"], "home": row["matchup"].split(" @ ")[1], "away": row["matchup"].split(" @ ")[0],
        "kickoff": KO, "decision_time": DECIDED, "governing_rule": "FUNDED_POSITION_DECISION", "n_decisions": 2,
        "recommendation": "QUALIFIED_BET", "selected_side": row["selected_side"], "funded": True, "position": None,
        "forecast": {"table": "shadow_forecasts", "row_id": fc["row_id"], "snapshot": "FINAL_PREGAME v1",
                     "snapshot_type": "FINAL_PREGAME", "available_utc": datetime.fromisoformat(fc["available_utc"]),
                     "published_utc": None, "p_home": fc["p_home"], "p_away": fc["p_away"], "p_tie": fc["p_tie"]},
        "final": final, "market": {"p_home": market_home, "n_books": 9, "books": []} if market_home else None,
        "market_at_availability": None, "evaluation_forecast_same": True})


def game(row, *, status="FINAL", home_score=None, away_score=None):
    return {"game_id": row["game_id"], "kickoff_utc": row["starts_at"], "status": status, "home_score": home_score,
            "away_score": away_score, "final_at_utc": None, "home": row["matchup"].split(" @ ")[1],
            "away": row["matchup"].split(" @ ")[0],
            "final_observation": None if status != "FINAL" else {"source": "NFLVERSE", "home_score": home_score,
                                                                 "away_score": away_score, "overtime": False,
                                                                 "retrieved_at_utc": (KO + timedelta(hours=3)).isoformat()},
            "retained_evidence": {"game_state_observations": 1, "game_scoreboard_observations": 0, "availability_events": 0}}


def build(*, late_quote=False, later_quote=None):
    """Four games: an underdog WIN (model favours the other side), a PUSH, an OPEN night game, and a scheduled
    Monday game with no decision yet."""

    win = wrow(result="WIN", net=16.5)
    push = wrow(side="HOME", away="CIN", home="PIT", pw=0.40, pt=0.05, american=152, result="PUSH", net=0.0)
    night = wrow(away="LAR", home="DEN", pw=0.5454, pt=0.0463, american=-111, kickoff=KO + timedelta(hours=7, minutes=20))
    fc_win, fc_push, fc_night = forecast(), forecast(p_home=0.40, p_away=0.55, p_tie=0.05, exp_home=21, exp_away=21), \
        forecast(p_home=0.4083, p_away=0.5454, p_tie=0.0463)
    later = {}
    if later_quote is not None:
        later[win["position_id"]] = later_quote
    monday = {"game_id": str(uuid4()), "kickoff_utc": (KO + timedelta(days=1, hours=7, minutes=15)).isoformat(),
              "status": "SCHEDULED", "home_score": None, "away_score": None, "final_at_utc": None, "home": "CHI",
              "away": "PHI", "final_observation": None, "retained_evidence": {}}
    games = [game(win, home_score=33, away_score=31), game(push, home_score=20, away_score=20),
             game(night, status="SCHEDULED"), monday]
    decisions = [decision(win, fc_win, final={"home": 33, "away": 31}),
                 decision(push, fc_push, final={"home": 20, "away": 20}, market_home=0.39),
                 decision(night, fc_night, market_home=None)]
    details = {win["decision_id"]: detail(win, fc_win), push["decision_id"]: detail(push, fc_push, opposite=-180),
               night["decision_id"]: detail(night, fc_night, opposite=-111,
                                            quote_available=DECIDED + timedelta(milliseconds=2) if late_quote else None)}
    history = {win["game_id"]: [{"decision_id": "early", "recommendation": "DECLINED",
                                 "qualification_reason": "QUOTE_TOO_STALE",
                                 "recommended_at_utc": (DECIDED - timedelta(minutes=5)).isoformat()},
                                {"decision_id": win["decision_id"], "recommendation": "QUALIFIED_BET",
                                 "qualification_reason": "ALL_GATES_PASSED", "recommended_at_utc": DECIDED.isoformat()}]}
    settlements = {win["position_id"]: {"game_home_score": 33, "game_away_score": 31, "position_decimal": win["entry_decimal"],
                                        "position_snapshot_id": "snap-1"},
                   push["position_id"]: {"game_home_score": 20, "game_away_score": 20, "position_decimal": push["entry_decimal"],
                                         "position_snapshot_id": "snap-1"}}
    if late_quote:
        snap = details[night["decision_id"]]["entry_snapshot"]
        snap["request_started_at_utc"] = snap["available_as_of_utc"]
        snap["request_completed_at_utc"] = (DECIDED + timedelta(seconds=1, microseconds=687551)).isoformat()
        snap["created_at_utc"] = (DECIDED + timedelta(seconds=1, microseconds=219344)).isoformat()
    inputs = mm.Inputs(
        league="NFL",
        window={"mode": "WEEK", "season": 2026, "week": 3, "start_utc": (KO - timedelta(days=3)).isoformat(),
                "end_utc": (KO + timedelta(days=2)).isoformat(), "label": "NFL 2026 Week 3", "short_label": "NFL Week 3"},
        snapshot={"db_clock_utc": NOW.isoformat(), "isolation": "repeatable read", "read_only": "on",
                  "pg_snapshot": "1:1:"},
        games=games, wager_rows=[win, push, night],
        loaded=mpo.Loaded(games=[], decisions=decisions, positions_total=3, positions_open=1, as_of=NOW),
        decision_detail=details, decision_history=history,
        forecasts={fc_win["row_id"]: fc_win, fc_push["row_id"]: fc_push, fc_night["row_id"]: fc_night},
        later_quotes=later, settlements=settlements, now=NOW, generator_commit="test")
    return inputs, win, push, night


def _slate(payload, row):
    return next(s for s in payload["slate"] if s["position_id"] == row["position_id"])


def test_selected_team_is_the_funded_selection_not_the_models_predicted_winner() -> None:
    inputs, win, _push, _night = build()
    s = _slate(mm.assemble(inputs), win)
    assert s["selected_team"] == "SEA" and s["selected_side"] == "AWAY"
    assert s["model_favourite_team"] == "WAS" and s["selected_is_model_favourite"] is False
    # The probability shown is the SELECTED side's recorded pWin, not the favourite's.
    assert s["p_win"] == 0.2575 and s["p_tie"] == 0.0249
    assert math.isclose(s["p_loss"], 1 - 0.2575 - 0.0249)


def test_a_push_is_a_refund_counted_as_a_push_never_a_loss() -> None:
    inputs, _win, push, _night = build()
    p = mm.assemble(inputs)
    summary = p["financial"]["summary"]
    assert (summary["wins"], summary["losses"], summary["pushes"]) == (1, 0, 1)
    assert summary["net_profit"] == 16.5 and summary["settled_stake"] == 10.0
    exp = p["financial"]["expectation_settled"]
    assert exp["actual_pushes"] == 1 and exp["actual_losses"] == 0
    story = next(g for g in p["games"] if g["position_id"] == push["position_id"])
    check = next(c for c in story["checks"] if c["key"] == "settlement_matches_score")
    assert check["status"] == "PASS" and "PUSH" in check["detail"]


def test_expected_profit_treats_a_tie_as_a_push_and_matches_the_recorded_ev() -> None:
    inputs, win, push, _night = build()
    exp = mm.assemble(inputs)["financial"]["expectation_settled"]
    by_hand = 0.0
    var = 0.0
    for r in (win, push):
        pw, pt, d, s = r["raw_probability"], r["tie_probability"], r["entry_decimal"], r["stake"]
        mean = pw * s * (d - 1) - (1 - pw - pt) * s   # the tie mass earns nothing and loses nothing
        by_hand += mean
        var += pw * (s * (d - 1)) ** 2 + (1 - pw - pt) * s ** 2 - mean ** 2
    assert math.isclose(exp["expected_profit"], by_hand, rel_tol=1e-12)
    assert math.isclose(exp["expected_profit_recomputed"], by_hand, rel_tol=1e-12)
    assert math.isclose(exp["expected_wins"], 0.2575 + 0.40)
    assert math.isclose(exp["expected_pushes"], 0.0249 + 0.05)
    assert math.isclose(exp["sd_profit"], math.sqrt(var))
    assert exp["max_abs_recorded_vs_recomputed_ev"] < 1e-12


def test_a_recorded_ev_that_does_not_recompute_is_flagged_not_repaired() -> None:
    inputs, win, _push, _night = build()
    win["recorded_return"] = win["recorded_return"] + 0.02
    story = next(g for g in mm.assemble(inputs)["games"] if g["position_id"] == win["position_id"])
    check = next(c for c in story["checks"] if c["key"] == "ev_arithmetic")
    assert check["status"] == "FLAG"


def test_pending_night_game_and_scheduled_monday_game_are_explicit_never_results() -> None:
    inputs, _win, _push, night = build()
    p = mm.assemble(inputs)
    assert p["open"] == [night["position_id"]]
    assert night["position_id"] not in p["settled"]
    summary = p["financial"]["summary"]
    assert summary["open_count"] == 1 and summary["open_exposure"] == 5.0 and summary["settled"] == 2
    s = _slate(p, night)
    assert s["result"] is None and s["net_profit"] is None and s["final"] is None
    story = next(g for g in p["games"] if g["position_id"] == night["position_id"])
    assert story["postgame"]["final"] is None
    assert next(c for c in story["checks"] if c["key"] == "settlement_matches_score")["status"] == "PENDING"
    monday = [g for g in p["games_without_position"] if g["matchup"] == "PHI @ CHI"]
    assert monday and monday[0]["final"] is None and "no FINAL_PREGAME decision" in monday[0]["note"]
    html = mmqb_render.render_html(p)
    assert "OPEN" in html and "PHI @ CHI" in html


def test_later_quotes_are_last_observed_quotes_never_closing_lines() -> None:
    entry = {"observed_at_utc": (DECIDED - timedelta(minutes=10)).isoformat()}
    quotes = [
        {"id": "entry", "market_outcome_id": "sel", "sportsbook_id": "b", "observed_at": DECIDED - timedelta(minutes=10),
         "available_as_of": DECIDED - timedelta(minutes=5), "decimal_odds": 4.3, "american_odds": 330, "source": "SGO"},
        {"id": "later", "market_outcome_id": "sel", "sportsbook_id": "b", "observed_at": KO - timedelta(minutes=8),
         "available_as_of": KO - timedelta(minutes=2), "decimal_odds": 4.1, "american_odds": 310, "source": "SGO"},
        {"id": "opp", "market_outcome_id": "opp", "sportsbook_id": "b", "observed_at": KO - timedelta(minutes=8),
         "available_as_of": KO - timedelta(minutes=2), "decimal_odds": 1.25, "american_odds": -400, "source": "SGO"},
        {"id": "after-ko", "market_outcome_id": "sel", "sportsbook_id": "b", "observed_at": KO + timedelta(minutes=1),
         "available_as_of": KO + timedelta(minutes=3), "decimal_odds": 5.0, "american_odds": 400, "source": "SGO"},
    ]
    q = mm.last_observed_quote(quotes, selected_outcome="sel", market_outcomes=["sel", "opp"], book_id="b", entry=entry,
                               entry_snapshot_id="entry", kickoff=KO)
    # Even with both legs in one receipt, this report never calls it a close.
    assert q["label"] == "LAST_OBSERVED_QUOTE" and q["is_closing_line"] is False
    assert q["quote"]["american"] == 310 and q["opposite_leg_same_receipt"] is True
    none = mm.last_observed_quote(quotes[:1], selected_outcome="sel", market_outcomes=["sel"], book_id="b", entry=entry,
                                  entry_snapshot_id="entry", kickoff=KO)
    assert none["quote"] is None and "last observed quote" in none["statement"]
    inputs, win, _push, _night = build(later_quote=q)
    p = mm.assemble(inputs)
    s = _slate(p, win)
    assert s["closing_line"] == "NOT_CAPTURED" and s["last_observed_quote"]["is_closing_line"] is False
    assert "genuine close requires capture evidence" in p["closing_line_rule"]
    for text in (mmqb_render.render_html(p), mmqb_render.render_brief(p), mmqb_render.render_show(p)):
        assert "closing line value was" not in text.lower() and "beat the close" not in text.lower()


def test_a_quote_available_after_the_decision_states_the_clocks_and_draws_no_conclusion() -> None:
    inputs, _win, _push, night = build(late_quote=True)
    p = mm.assemble(inputs)
    story = next(g for g in p["games"] if g["position_id"] == night["position_id"])
    check = next(c for c in story["checks"] if c["key"] == "quote_available_before_decision")
    assert check["status"] == "FLAG"
    assert "Quote completion follows the recorded tick-start decision time by 1.687551 s" in check["detail"]
    # Without a recorded review, nothing beyond the clocks is concluded.
    assert "Pending review of the actual receipt and read evidence" in check["detail"]
    assert "look-ahead" not in check["detail"]
    ordering = next(f for f in p["findings"] if f["key"] == "ORDERING")
    assert "1.687551 s" in ordering["finding"]


def test_a_recorded_external_review_is_quoted_verbatim_with_its_source() -> None:
    inputs, _win, _push, night = build(late_quote=True)
    real = "7770a220-1100-4c32-a065-c42b4f20c59f"
    old = night["decision_id"]
    inputs.decision_detail[real] = inputs.decision_detail.pop(old)
    night["decision_id"] = real
    for d in inputs.loaded.decisions:
        if d["decision_id"] == old:
            d["decision_id"] = real
    story = next(g for g in mm.assemble(inputs)["games"] if g["position_id"] == night["position_id"])
    check = next(c for c in story["checks"] if c["key"] == "quote_available_before_decision")
    ruling = mm.EXTERNAL_RULINGS[real]
    assert ruling["text"] in check["detail"] and ruling["source"] in check["detail"]
    assert "RECEIPT_ORDER_UNPROVEN" in check["detail"] and "1.687551 s" in check["detail"]


def test_first_in_window_decline_is_reported_as_a_one_tick_delay() -> None:
    inputs, win, _push, _night = build()
    story = next(g for g in mm.assemble(inputs)["games"] if g["position_id"] == win["position_id"])
    check = next(c for c in story["checks"] if c["key"] == "first_in_window_decision")
    assert check["status"] == "INFO" and "QUOTE_TOO_STALE" in check["detail"] and "5 min later" in check["detail"]


def test_model_vs_market_is_two_way_on_the_same_decided_games() -> None:
    inputs, win, push, _night = build()
    mc = mm.assemble(inputs)["model_vs_market"]["week"]
    # The push (a tie) is not scored two-way, the open game has no outcome: one game remains.
    assert [g["position_id"] for g in mc["games"]] == [win["position_id"]]
    g = mc["games"][0]
    p_model = mpo.two_way(0.72, 0.2551)
    assert math.isclose(g["model_log_loss"], -math.log(p_model))
    nv_sel = mm.no_vig_pair(win["entry_decimal"], mm.american_to_decimal(-450))
    assert math.isclose(g["pair_p_home"], 1 - nv_sel)


def test_mid_percentile_of_a_stored_distribution() -> None:
    dist = [{"total_score": t, "probability": 0.25} for t in (40, 44, 48, 52)]
    assert mm.mid_percentile(dist, "total_score", 44) == 0.375
    assert mm.mid_percentile(dist, "total_score", 60) == 1.0
    assert mm.mid_percentile([], "total_score", 44) is None


def test_every_output_states_the_data_cutoff_and_snapshot() -> None:
    inputs, *_ = build()
    p = mm.assemble(inputs)
    sha = p["snapshot"]["content_sha256"]
    assert sha == mm.content_hash(p)
    for text in (mmqb_render.render_html(p), mmqb_render.render_brief(p), mmqb_render.render_csv(p),
                 mmqb_render.render_show(p), mmqb_render.render_wagers_json(p)):
        assert "2026-09-28 02:00Z" in text or NOW.isoformat() in text
        assert sha[:16] in text
    assert p["caveat"].startswith("One Sunday can flag cases")


def test_endpoint_serves_the_generator_payload_and_its_html(client, monkeypatch) -> None:
    inputs, *_ = build()
    monkeypatch.setattr(mm, "load_inputs", lambda session, **kw: inputs)
    response = client.get("/api/v1/research/mmqb?league=NFL&season=2026&week=3")
    assert response.status_code == 200
    expected = json.loads(json.dumps(mm.assemble(inputs), default=str))
    body = response.json()
    body["snapshot"].pop("generated_at_utc")
    expected["snapshot"].pop("generated_at_utc")
    assert body == expected
    page = client.get("/api/v1/research/mmqb?league=NFL&season=2026&week=3&format=html")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "Monday Morning QB: NFL Week 3" in page.text
    assert client.get("/api/v1/research/mmqb?league=MLB").status_code == 404


def _evidence(win):
    seq = [{"clock": "Q1 9:14", "team": "WAS", "points": 6, "kind": "TD", "away": 0, "home": 6},
           {"clock": "Q4 2:55", "team": "SEA", "points": 6, "kind": "TD", "away": 31, "home": 33}]
    return {"schema": "AISP_MMQB_GAME_EVIDENCE_V1", "extracted_at_utc": NOW.isoformat(),
            "play_by_play": {"payload_path": "pbp.parquet", "payload_sha256": "ab" * 32, "sha256_matches_vintage": True,
                             "source_retrieved_at_utc": NOW.isoformat(), "archive_observed_at_utc": NOW.isoformat(),
                             "row_count_total": 100},
            "games": {"2026_03_SEA_WAS": {"retained": True, "rows": 50, "ends_with_end_game": True,
                                          "final_as_recorded": {"away": 31, "home": 33}, "scoring_sequence": seq,
                                          "turnovers": [{"clock": "Q1 11:06", "lost_by": "SEA", "kind": "FUMBLE_LOST"}],
                                          "turnover_counts": {"SEA": 1, "WAS": 0},
                                          "passers": [{"team": "WAS", "gsis_id": "00-B", "name_as_recorded": "B.Other",
                                                       "pass_attempts": 33}]}},
            "qb_inputs": {"2026_03_SEA_WAS": {"sha256": "cd" * 32, "forecast_cutoff_utc": "2026-09-27T16:00:00Z",
                                              "admission_granted": False,
                                              "sides": [{"side": "home", "team": "WAS", "depth_rank_one_gsis_id": "00-A",
                                                         "change_signal": "SAME_AS_PRIOR_DOMINANT_QB",
                                                         "confirmed_starter_status": "NOT_ESTABLISHED",
                                                         "reported_availability_status": "NO_QUALIFIED_SOURCE"}]}},
            "searched": [{"source": "db", "finding": "finals only"}]}


def test_deep_dives_are_chosen_by_rule_and_keep_observed_apart_from_hypothesis() -> None:
    inputs, win, _push, _night = build()
    inputs.game_evidence = _evidence(win)
    p = mm.assemble(inputs)
    dives = p["deep_dives"]
    assert dives and dives[0]["key"] == "LONGSHOT_WIN" and dives[0]["position_id"] == win["position_id"]
    d = dives[0]
    assert d["observed"]["label"] == "OBSERVED" and d["observed"]["play_by_play_status"] == "FINAL_PLAY_BY_PLAY_RETAINED"
    assert d["observed"]["turnover_counts"] == {"SEA": 1, "WAS": 0}
    assert d["before_kickoff"]["forecast_inputs"] == mm.FORECAST_INPUTS_STATEMENT
    qb = next(h for h in d["hypotheses"] if h["topic"] == "QB input")
    assert "did not attempt a pass" in qb["text"] and "B.Other" in qb["text"]
    assert "NOT evidence that production selected the wrong starter" in qb["text"]
    # Observed facts never carry causal words; hypotheses are labelled as such.
    assert "because" not in json.dumps(d["observed"]).lower()
    html = mmqb_render.render_html(p)
    assert "OBSERVED" in html and "HYPOTHESIS" in html and "BEFORE KICKOFF" in html


def test_a_final_mismatch_suppresses_the_play_by_play() -> None:
    inputs, win, _push, _night = build()
    ev = _evidence(win)
    ev["games"]["2026_03_SEA_WAS"]["final_as_recorded"] = {"away": 30, "home": 33}
    inputs.game_evidence = ev
    d = mm.assemble(inputs)["deep_dives"][0]
    assert d["observed"]["play_by_play_status"] == "PLAY_BY_PLAY_NOT_FINAL_OR_MISMATCHED"
    assert "scoring_sequence" not in d["observed"]


def test_every_output_carries_the_same_identity() -> None:
    inputs, win, *_ = build()
    inputs.game_evidence = _evidence(win)
    p = mm.assemble(inputs)
    ident = mmqb_render.identity(p)
    assert all(ident.values())
    outputs = {"html": mmqb_render.render_html(p), "brief": mmqb_render.render_brief(p),
               "show": mmqb_render.render_show(p), "csv": mmqb_render.render_csv(p)}
    for name, text in outputs.items():
        for value in ident.values():
            assert str(value) in text, (name, value)
    wj = json.loads(mmqb_render.render_wagers_json(p))
    assert wj["identity"] == ident
    assert all(r["content_sha256"] == ident["content_sha256"] for r in wj["wagers"])


def test_interpretation_wording_is_uncertainty_honest() -> None:
    inputs, *_ = build()
    p = mm.assemble(inputs)
    assert mm.verdict({"delta_log_loss": 0.05, "delta_log_loss_ci95": [-0.05, 0.19]}) == (
        "The model scored worse in this sample, but the uncertainty does not establish a difference.")
    for text in (mmqb_render.render_html(p), mmqb_render.render_brief(p), mmqb_render.render_show(p)):
        assert "neither is a real difference" not in text and "no difference is established" not in text.lower()
    assert "does not validate calibration" in mmqb_render.render_html(p)


# ------------------------------------------------------------ owner corrections ---

def week3_like():
    """The three Week 3 deep-dive positions, with the finals the vendored play-by-play records."""

    was = wrow(side="HOME", away="SEA", home="WAS", pw=0.2575, pt=0.0249, american=330, result="WIN", net=16.5)
    dal = wrow(side="HOME", away="BAL", home="DAL", pw=0.5471, pt=0.0285, american=155, result="LOSS", net=-5.0)
    no = wrow(side="HOME", away="LV", home="NO", pw=0.7744, pt=0.0227, american=-179, result="LOSS", net=-5.0)
    rows = [(was, "2026_03_SEA_WAS", (33, 31), -450, (0.2575, 0.7176, 0.0249)),
            (dal, "2026_03_BAL_DAL", (31, 34), -185, (0.5471, 0.4244, 0.0285)),
            (no, "2026_03_LV_NO", (27, 35), 151, (0.7744, 0.2029, 0.0227))]
    games, decisions, details, forecasts, settlements = [], [], {}, {}, {}
    for r, event, (hs, as_), opp, (ph, pa, pt) in rows:
        fc = forecast(p_home=ph, p_away=pa, p_tie=pt, event=event)
        forecasts[fc["row_id"]] = fc
        games.append(game(r, home_score=hs, away_score=as_))
        decisions.append(decision(r, fc, final={"home": hs, "away": as_}, market_home=0.5))
        details[r["decision_id"]] = detail(r, fc, opposite=opp)
        settlements[r["position_id"]] = {"game_home_score": hs, "game_away_score": as_,
                                         "position_decimal": r["entry_decimal"], "position_snapshot_id": "snap-1"}
    inputs = mm.Inputs(
        league="NFL",
        window={"mode": "WEEK", "season": 2026, "week": 3, "start_utc": (KO - timedelta(days=3)).isoformat(),
                "end_utc": (KO + timedelta(days=2)).isoformat(), "label": "NFL 2026 Week 3", "short_label": "NFL Week 3"},
        snapshot={"db_clock_utc": NOW.isoformat(), "isolation": "repeatable read", "read_only": "on", "pg_snapshot": "1:1:"},
        games=games, wager_rows=[was, dal, no],
        loaded=mpo.Loaded(games=[], decisions=decisions, positions_total=3, positions_open=0, as_of=NOW),
        decision_detail=details, decision_history={}, forecasts=forecasts, later_quotes={}, settlements=settlements,
        now=NOW, generator_commit="test")
    return inputs, was, dal, no


def test_vendored_week3_evidence_loads_and_verifies() -> None:
    doc, status = mm.resolve_game_evidence(league="NFL", season=2026, week=3)
    assert status["status"] == "VERIFIED" and status["source"] == "VENDORED", status
    assert status["source_vintage_sha256"] == "c30db07862378e0b3b1e540ac18763b6993371e9879f7ee008c0b70c5cd88332"
    assert set(doc["games"]) == {"2026_03_SEA_WAS", "2026_03_LV_NO", "2026_03_BAL_DAL"}
    assert all(g["ends_with_end_game"] for g in doc["games"].values())
    manifest = json.loads((mm.EVIDENCE_DIR / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["artifacts"]["NFL|2026|3"]
    assert entry["bytes"] == (mm.EVIDENCE_DIR / entry["file"]).stat().st_size < 64_000


def test_tampered_evidence_is_invalid(tmp_path) -> None:
    import shutil

    for name in ("manifest.json", "nfl_2026_w03_game_evidence.json"):
        shutil.copy(mm.EVIDENCE_DIR / name, tmp_path / name)
    path = tmp_path / "nfl_2026_w03_game_evidence.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["games"]["2026_03_LV_NO"]["turnover_counts"]["NO"] = 0
    path.write_text(json.dumps(doc), encoding="utf-8")
    _, status = mm.resolve_game_evidence(league="NFL", season=2026, week=3, evidence_dir=tmp_path)
    assert status["status"] == "INVALID" and "manifest" in status["reason"]
    # Even with the manifest's file hash updated, the content hash catches the edit.
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    import hashlib

    manifest["artifacts"]["NFL|2026|3"]["file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _, status = mm.resolve_game_evidence(league="NFL", season=2026, week=3, evidence_dir=tmp_path)
    assert status["status"] == "INVALID" and "content sha256" in status["reason"]
    _, status = mm.resolve_game_evidence(league="NFL", season=2026, week=3, override=str(path))
    assert status["status"] == "INVALID"


def test_missing_evidence_shows_a_visible_banner_never_a_silent_gap() -> None:
    _, status = mm.resolve_game_evidence(league="NFL", season=2026, week=4)
    assert status["status"] == "MISSING"
    inputs, *_ = week3_like()
    inputs.game_evidence, inputs.game_evidence_status = None, status
    p = mm.assemble(inputs)
    assert p["game_evidence_status"]["status"] == "MISSING"
    assert len(p["deep_dives"]) == 3
    assert all(d["observed"]["play_by_play_status"] == "GAME_EVIDENCE_UNAVAILABLE" for d in p["deep_dives"])
    html = mmqb_render.render_html(p)
    assert "Game evidence unavailable: no vendored game evidence for NFL 2026 week 4" in html
    assert 'data-testid="evidence-banner"' in html


def test_endpoint_week3_payload_has_three_deep_dives_with_verified_play_by_play(client, monkeypatch) -> None:
    inputs, *_ = week3_like()
    monkeypatch.setattr(mm, "load_inputs", lambda session, **kw: inputs)
    monkeypatch.delenv("MMQB_GAME_EVIDENCE", raising=False)
    body = client.get("/api/v1/research/mmqb?league=NFL&season=2026&week=3").json()
    assert body["game_evidence_status"]["status"] == "VERIFIED"
    dives = body["deep_dives"]
    assert [d["event_id"] for d in dives] == ["2026_03_SEA_WAS", "2026_03_BAL_DAL", "2026_03_LV_NO"]
    assert all(d["observed"]["play_by_play_status"] == "FINAL_PLAY_BY_PLAY_RETAINED" for d in dives)
    bal = dives[1]["observed"]["scoring_sequence"][-1]
    assert (bal["team"], bal["kind"], bal["player_as_recorded"], bal["kick_distance_yards"], bal["clock"]) == (
        "BAL", "FG", "T.Loop", 56.0, "Q4 0:01")


def test_deep_dive_superlatives_are_verified_against_the_slate() -> None:
    inputs, was, dal, no = week3_like()
    p = mm.assemble(inputs)
    slate = p["slate"]
    for d in p["deep_dives"]:
        assert "surest" not in d["reason"]
        if d["reason"].startswith("the "):
            assert d["superlative_verified"] is True
    confident = next(d for d in p["deep_dives"] if d["key"] == "CONFIDENT_LOSS")
    losses = [s for s in slate if s["result"] == "LOSS"]
    top = max(losses, key=lambda s: s["p_win"])
    assert confident["position_id"] == top["position_id"]
    assert confident["reason"] == "the highest-confidence losing selection"
    # A tie for the top losing pWin must fall back to the plain label.
    dal["raw_probability"] = no["raw_probability"]
    tied = next(d for d in mm.assemble(inputs)["deep_dives"] if d["key"] == "CONFIDENT_LOSS")
    assert tied["reason"] == "a high-confidence losing selection" and tied["superlative_verified"] is False
    for text in (mmqb_render.render_html(p), mmqb_render.render_brief(p), mmqb_render.render_show(p)):
        assert "surest" not in text.lower() and "most sure" not in text.lower()


def test_hypotheses_name_only_testable_assumptions_or_missing_inputs() -> None:
    inputs, *_ = week3_like()
    inputs.game_evidence, inputs.game_evidence_status = mm.resolve_game_evidence(league="NFL", season=2026, week=3)
    p = mm.assemble(inputs)
    banned = ("luck", "variance ate", "forecast error", "unlucky", "fluke")
    for d in p["deep_dives"]:
        for h in d["hypotheses"]:
            assert "turnover" not in h["topic"].lower()
            assert not any(w in h["text"].lower() for w in banned)
    show = mmqb_render.render_show(p).lower()
    assert not any(w in show for w in banned)


# ------------------------------------------------------------ Monday night ---

def _postdeploy():
    import importlib.util
    from pathlib import Path

    path = Path(mm.__file__).resolve().parents[2] / "scripts" / "mmqb_postdeploy_check.py"
    spec = importlib.util.spec_from_file_location("mmqb_postdeploy_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def with_monday_night(settled: bool):
    inputs, was, dal, no = week3_like()
    mnf_ko = KO + timedelta(days=1, hours=7, minutes=15)          # 2026-09-29 00:15Z = Monday 20:15 ET
    mnf = wrow(side="HOME", away="PHI", home="CHI", pw=0.45, pt=0.03, american=150, kickoff=mnf_ko,
               result="WIN" if settled else None, net=7.5 if settled else None)
    fc = forecast(p_home=0.45, p_away=0.52, p_tie=0.03, event="2026_03_PHI_CHI")
    inputs.forecasts[fc["row_id"]] = fc
    inputs.games.append(game(mnf, status="FINAL" if settled else "SCHEDULED",
                             home_score=24 if settled else None, away_score=20 if settled else None))
    inputs.loaded.decisions.append(decision(mnf, fc, final={"home": 24, "away": 20} if settled else None, market_home=0.4))
    inputs.decision_detail[mnf["decision_id"]] = detail(mnf, fc, opposite=-180)
    if settled:
        inputs.settlements[mnf["position_id"]] = {"game_home_score": 24, "game_away_score": 20,
                                                   "position_decimal": mnf["entry_decimal"], "position_snapshot_id": "snap-1"}
    inputs.wager_rows.append(mnf)
    inputs.game_evidence, inputs.game_evidence_status = mm.resolve_game_evidence(league="NFL", season=2026, week=3)
    return inputs, [was, dal, no], mnf


def test_an_open_monday_night_position_joins_the_week_and_stays_open() -> None:
    inputs, sunday, mnf = with_monday_night(settled=False)
    p = mm.assemble(inputs)
    s = p["financial"]["summary"]
    assert (s["n"], s["settled"], s["open_count"], s["open_exposure"]) == (4, 3, 1, 5.0)
    assert s["net_profit"] == 6.5                                   # Sunday only: +16.50 - 5 - 5
    groups = {g["day"]: g for g in p["slate_groups"]}
    assert groups["Sunday"]["n"] == 3 and groups["Monday night"]["position_ids"] == [mnf["position_id"]]
    assert groups["Monday night"]["open"] == 1
    assert mnf["position_id"] in p["open"] and len(p["deep_dives"]) == 3
    html = mmqb_render.render_html(p)
    assert "Monday night" in html and "week total by slate: Sunday 3, Monday night 1" in html
    pd = _postdeploy()
    results = pd.evaluate(json.loads(json.dumps(p, default=str)), artifact={"file_sha256": p["game_evidence_status"]["file_sha256"]},
                          required={"scope": "test Sunday", "decision_ids": [r["decision_id"] for r in sunday]})
    assert all(ok for ok, _ in results), [m for ok, m in results if not ok]
    assert any("additional week position (allowed): PHI @ CHI" in m for _, m in results)


def test_a_settled_monday_night_position_counts_in_the_week_scorecard() -> None:
    inputs, sunday, mnf = with_monday_night(settled=True)
    p = mm.assemble(inputs)
    s = p["financial"]["summary"]
    assert (s["n"], s["settled"], s["open_count"], s["wins"], s["losses"]) == (4, 4, 0, 2, 2)
    assert s["net_profit"] == 14.0 and s["settled_stake"] == 20.0
    assert {g["day"]: g["settled"] for g in p["slate_groups"]} == {"Sunday": 3, "Monday night": 1}
    pd = _postdeploy()
    body = json.loads(json.dumps(p, default=str))
    art = {"file_sha256": p["game_evidence_status"]["file_sha256"]}
    req = {"decision_ids": [r["decision_id"] for r in sunday]}
    assert all(ok for ok, _ in pd.evaluate(body, artifact=art, required=req))
    exact = pd.evaluate(body, artifact=art, required=req, expect_positions=3)
    assert not all(ok for ok, _ in exact)


def test_a_missing_required_sunday_position_fails_the_postdeploy_check() -> None:
    inputs, sunday, _mnf = with_monday_night(settled=True)
    p = json.loads(json.dumps(mm.assemble(inputs), default=str))
    pd = _postdeploy()
    results = pd.evaluate(p, artifact={"file_sha256": p["game_evidence_status"]["file_sha256"]},
                          required={"decision_ids": [r["decision_id"] for r in sunday] + ["deadbeef-0000"]})
    failed = [m for ok, m in results if not ok]
    assert failed and "MISSING deadbeef" in failed[0]


def test_the_vendored_manifest_requires_the_fourteen_reviewed_sunday_positions() -> None:
    entry = _postdeploy().load_manifest_entry("NFL", 2026, 3)
    ids = entry["required"]["decision_ids"]
    assert len(ids) == len(set(ids)) == 14 and "7770a220-1100-4c32-a065-c42b4f20c59f" in ids


def test_embedded_report_follows_the_page_theme_and_links_same_origin(client, monkeypatch) -> None:
    inputs, *_ = week3_like()
    monkeypatch.setattr(mm, "load_inputs", lambda session, **kw: inputs)
    page = client.get("/api/v1/research/mmqb?league=NFL&season=2026&week=3&format=html&theme=dark").text
    assert '<html lang="en" data-theme="dark">' in page
    assert 'href="/performance?panel=wagers&amp;sport=NFL" target="_top"' in page
    assert 'href="/games/' in page and 'target="_top"' in page and "127.0.0.1" not in page
    plain = client.get("/api/v1/research/mmqb?league=NFL&season=2026&week=3&format=html").text
    assert "data-theme=" not in plain.split("<head>")[0]
    assert client.get("/api/v1/research/mmqb?league=NFL&season=2026&week=3&format=html&theme=pink").status_code == 422


def test_the_report_page_may_be_framed_by_the_site_only_and_json_stays_locked(client, monkeypatch) -> None:
    inputs, *_ = week3_like()
    monkeypatch.setattr(mm, "load_inputs", lambda session, **kw: inputs)
    page = client.get("/api/v1/research/mmqb?league=NFL&season=2026&week=3&format=html")
    assert page.headers["x-frame-options"] == "SAMEORIGIN"
    csp = page.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in csp and "style-src 'unsafe-inline'" in csp and "script" not in csp
    assert "x-aisp-frame-policy" not in page.headers
    data = client.get("/api/v1/research/mmqb?league=NFL&season=2026&week=3")
    assert data.headers["x-frame-options"] == "DENY"
    assert data.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
