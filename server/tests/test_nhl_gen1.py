"""NHL Gen1 preview: contract validation, importer idempotency/revisions/conflicts, results, read model."""

from __future__ import annotations

import copy
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Game, GameStatus, NhlGameResult, NhlImportReceipt, NhlResearchForecast
from app.nhl import contract as c
from tests import nhl_fixtures as fx
from app.nhl.gen1_import import ImportSummary, import_exchange, import_forecast_bytes, import_result_bytes
from app.nhl.identity import ensure_game
from app.nhl.read_service import NhlReadService

NOW = datetime(2026, 10, 1, 2, 0, tzinfo=UTC)


@pytest.fixture
def scenario():
    return fx.scenario(NOW)


def _seed_games(session, scenario) -> None:
    for game in scenario["games"].values():
        ensure_game(session, game, source_mode="FIXTURE")
    session.flush()


def _import_all(session, artifact_root: pathlib.Path):
    built = fx.build_trees(artifact_root / "exchange", NOW)
    _seed_games(session, built["scenario"])
    summaries = [import_exchange(session, tree, now=NOW, record_class="PREVIEW_FIXTURE") for tree in built["trees"]]
    session.flush()
    return built, summaries


# ------------------------------------------------------------------ contract --
def test_fixture_forecasts_and_results_validate(scenario):
    for key, doc in scenario["forecasts"].items():
        assert c.validate_forecast(doc) == [], key
    for key, doc in scenario["results"].items():
        assert c.validate_result(doc) == [], key


def test_identity_is_a_content_hash_that_ignores_publication(scenario):
    doc = scenario["forecasts"]["REG_FINAL"]
    republished = copy.deepcopy(doc)
    republished["publishedAtUtc"] = "2026-09-28T22:30:00Z"
    assert c.forecast_identity(republished) == doc["forecastId"]
    changed = copy.deepcopy(doc)
    changed["goalies"]["home"]["goalieName"] = "Someone else"
    assert c.forecast_identity(changed) != doc["forecastId"]
    assert "FORECAST_ID_NOT_CONTENT_HASH" in c.validate_forecast(changed)


def _rehash(doc):
    return c.with_identity(doc)


def test_regulation_mass_must_sum_to_one(scenario):
    doc = copy.deepcopy(scenario["forecasts"]["REG_FINAL"])
    doc["probabilities"]["regulation"]["home"] += 0.01
    assert "REGULATION_MASS_NOT_ONE" in c.validate_forecast(_rehash(doc))


def test_final_mass_must_sum_to_one_and_not_fall_below_regulation(scenario):
    doc = copy.deepcopy(scenario["forecasts"]["REG_FINAL"])
    doc["probabilities"]["final"]["home"] += 0.02
    assert "FINAL_MASS_NOT_ONE" in c.validate_forecast(_rehash(doc))
    doc = copy.deepcopy(scenario["forecasts"]["REG_FINAL"])
    reg = doc["probabilities"]["regulation"]
    doc["probabilities"]["final"] = {"home": reg["home"] - 0.05, "away": 1 - (reg["home"] - 0.05)}
    doc["probabilities"]["overtime"] = None
    assert "FINAL_BELOW_REGULATION" in c.validate_forecast(_rehash(doc))


def test_overtime_block_must_match_regulation_tie(scenario):
    doc = copy.deepcopy(scenario["forecasts"]["OT_FINAL"])
    doc["probabilities"]["overtime"]["pOvertime"] += 0.05
    problems = c.validate_forecast(_rehash(doc))
    assert any(p.startswith("OVERTIME_INCONSISTENT") for p in problems)


def test_optional_fields_may_be_null_but_not_absent(scenario):
    doc = copy.deepcopy(scenario["forecasts"]["REG_FINAL"])
    doc["probabilities"]["overtime"] = None
    doc["goals"]["final"] = None
    assert c.validate_forecast(_rehash(doc)) == []
    del doc["probabilities"]["overtime"]
    assert any("probabilities.overtime_MISSING" in p for p in c.validate_forecast(_rehash(doc)))


def test_unknown_field_and_authority_are_refused(scenario):
    doc = copy.deepcopy(scenario["forecasts"]["REG_FINAL"])
    doc["edge"] = 0.04
    doc["authority"]["wageringAuthority"] = True
    problems = c.validate_forecast(_rehash(doc))
    assert "UNKNOWN_FIELD:forecast.edge" in problems
    assert "AUTHORITY_NOT_FALSE" in problems


def test_identity_formats_and_game_type_must_agree(scenario):
    doc = copy.deepcopy(scenario["forecasts"]["REG_FINAL"])
    doc["game"]["gameType"] = "REGULAR"  # id says 01 = preseason
    assert "CANONICAL_ID_MISMATCH:game.gameType" in c.validate_forecast(_rehash(doc))
    doc = copy.deepcopy(scenario["forecasts"]["REG_FINAL"])
    doc["game"]["away"] = copy.deepcopy(doc["game"]["home"])
    assert "INVALID_ORIENTATION" in c.validate_forecast(_rehash(doc))


def test_forecast_must_be_pregame(scenario):
    doc = copy.deepcopy(scenario["forecasts"]["REG_FINAL"])
    start = c.parse_utc(doc["game"]["scheduledStartUtc"])
    doc["createdAtUtc"] = fx.z(start + timedelta(minutes=1))
    doc["publishedAtUtc"] = fx.z(start + timedelta(minutes=2))
    assert "FORECAST_NOT_PREGAME" in c.validate_forecast(_rehash(doc))


def test_goalie_status_must_be_consistent(scenario):
    doc = copy.deepcopy(scenario["forecasts"]["FUTURE_V1"])
    doc["missingInputs"] = ["MARKET_PRICES_NOT_USED"]  # PROJECTED + UNKNOWN goalies, but not flagged
    problems = c.validate_forecast(_rehash(doc))
    assert any(p.startswith("GOALIE_STATUS_INCONSISTENT:goalies.home") for p in problems)
    assert any(p.startswith("GOALIE_STATUS_INCONSISTENT:goalies.away") for p in problems)


def test_results_rules_regulation_ot_shootout(scenario):
    so = scenario["results"]["SO_FINAL"]
    assert so["final"]["homeScore"] == so["regulation"]["homeGoals"] + 1  # winner credited one goal
    tied_final = copy.deepcopy(so)
    tied_final["final"]["homeScore"] = tied_final["final"]["awayScore"]
    tied_final = c.with_result_identity(tied_final)
    assert "RESULT_INVALID:final_tied" in c.validate_result(tied_final)
    reg_tie_no_ot = copy.deepcopy(scenario["results"]["OT_FINAL"])
    reg_tie_no_ot["decidedIn"] = "REGULATION"
    assert any("regulation_tie_iff_extra_time" in p for p in c.validate_result(c.with_result_identity(reg_tie_no_ot)))
    playoff_so = copy.deepcopy(so)
    playoff_so["canonicalGameId"] = "nhl:game:2026030111"
    playoff_so["gameType"] = "PLAYOFF"
    assert "RESULT_INVALID:playoff_shootout" in c.validate_result(c.with_result_identity(playoff_so))


# ------------------------------------------------------------------ importer --
def test_delivery_counts_duplicates_revisions_and_conflict(session, artifact_root):
    _, (first, second, third) = _import_all(session, artifact_root)
    assert first.counts == {"IMPORTED": 4, "REVISED": 0, "DUPLICATE": 0, "REJECTED": 0}
    assert first.result_counts == {"IMPORTED": 3, "DUPLICATE": 0, "REJECTED": 0}
    assert second.counts == {"IMPORTED": 0, "REVISED": 1, "DUPLICATE": 4, "REJECTED": 0}
    assert third.counts["REJECTED"] == 1
    assert "CONFLICTING_PAYLOAD_FOR_IDENTITY" in third.receipts[0]["problems"]
    assert session.query(NhlResearchForecast).count() == 5
    assert session.query(NhlImportReceipt).count() == 4 + 3 + 5 + 1


def test_identical_reimport_is_a_noop_with_receipt(session, artifact_root):
    built, _ = _import_all(session, artifact_root)
    again = import_exchange(session, built["trees"][0], now=NOW, record_class="PREVIEW_FIXTURE")
    assert again.counts["DUPLICATE"] == 4 and again.counts["IMPORTED"] == 0
    assert again.result_counts["DUPLICATE"] == 3
    assert session.query(NhlResearchForecast).count() == 5
    assert session.query(NhlGameResult).count() == 3


def test_revision_history_is_kept_and_current_is_latest(session, artifact_root, scenario):
    _import_all(session, artifact_root)
    detail = NhlReadService(session).game("2026010080", now=NOW)
    assert detail["revision_count"] == 2
    assert len(detail["history"]) == 2
    assert detail["forecast"]["forecast_id"] == scenario["forecasts"]["FUTURE_V2"]["forecastId"]
    assert detail["forecast"]["supersedes_forecast_id"] == scenario["forecasts"]["FUTURE_V1"]["forecastId"]
    assert detail["forecast"]["goalies"]["home"]["status"] == "CONFIRMED"
    assert [h["is_current"] for h in detail["history"]] == [True, False]


def test_conflicting_payload_is_rejected_without_writing(session, artifact_root, scenario):
    _seed_games(session, scenario)
    s = ImportSummary(run_id="t")
    good = scenario["forecasts"]["REG_FINAL"]
    assert import_forecast_bytes(session, c.canonical_bytes(good), summary=s, now=NOW) == "IMPORTED"
    bad = fx.conflicting(good)
    assert import_forecast_bytes(session, c.canonical_bytes(bad), summary=s, now=NOW) == "REJECTED"
    held = session.scalar(select(NhlResearchForecast).where(NhlResearchForecast.forecast_id == good["forecastId"]))
    assert held.regulation_home_probability == good["probabilities"]["regulation"]["home"]


def test_invalid_forecast_is_rejected_with_reasons(session, scenario):
    s = ImportSummary(run_id="t")
    doc = copy.deepcopy(scenario["forecasts"]["REG_FINAL"])
    doc["probabilities"]["regulation"]["tie"] = 0.9
    doc = c.with_identity(doc)
    assert import_forecast_bytes(session, c.canonical_bytes(doc), summary=s, now=NOW) == "REJECTED"
    assert "REGULATION_MASS_NOT_ONE" in s.receipts[0]["problems"]
    assert session.query(NhlResearchForecast).count() == 0


def test_unbound_game_is_kept_unbound_unless_admitted(session, scenario):
    s = ImportSummary(run_id="t")
    raw = c.canonical_bytes(scenario["forecasts"]["REG_FINAL"])
    assert import_forecast_bytes(session, raw, summary=s, now=NOW) == "IMPORTED"
    row = session.scalar(select(NhlResearchForecast))
    assert row.game_id is None and "UNBOUND_GAME" in s.receipts[0]["problems"]
    s2 = ImportSummary(run_id="t2")
    assert import_forecast_bytes(session, c.canonical_bytes(scenario["forecasts"]["OT_FINAL"]), summary=s2, now=NOW,
                                 admit_unbound_games=True) == "IMPORTED"
    assert "GAME_ADMITTED_FROM_FORECAST_IDENTITY" in s2.receipts[0]["problems"]


def test_orientation_mismatch_with_aisp_game_is_rejected(session, scenario):
    flipped = copy.deepcopy(scenario["games"]["REG_FINAL"])
    flipped["home"], flipped["away"] = flipped["away"], flipped["home"]
    ensure_game(session, flipped, source_mode="FIXTURE")
    s = ImportSummary(run_id="t")
    assert import_forecast_bytes(session, c.canonical_bytes(scenario["forecasts"]["REG_FINAL"]), summary=s,
                                 now=NOW) == "REJECTED"
    assert any(p.startswith("ORIENTATION_DIFFERS_FROM_AISP_GAME") for p in s.receipts[0]["problems"])


def test_checksum_mismatch_object_is_rejected(session, artifact_root, scenario):
    root = artifact_root / "tamper"
    entry = fx.write_object(root, scenario["forecasts"]["REG_FINAL"], "forecast.json")
    (root / entry["relativePath"]).write_bytes(b"{}")
    fx.write_pointer(root, fx.write_manifest(root, c.FORECAST_MANIFEST_SCHEMA, [entry], NOW), None)
    summary = import_exchange(session, root, now=NOW)
    assert summary.counts["REJECTED"] == 1
    assert any("OBJECT_CHECKSUM_MISMATCH" in p for p in summary.receipts[0]["problems"])


# -------------------------------------------------------------------- results --
def test_results_reconcile_regulation_ot_so_separately(session, artifact_root):
    _import_all(session, artifact_root)
    rows = {r.canonical_game_ref: r for r in session.scalars(select(NhlGameResult))}
    reg, ot, so = rows["nhl:game:2026010071"], rows["nhl:game:2026010072"], rows["nhl:game:2026010073"]
    assert (reg.regulation_result, reg.decided_in, reg.final_winner) == ("HOME", "REGULATION", "HOME")
    assert (ot.regulation_result, ot.decided_in, ot.final_winner) == ("TIE", "OVERTIME", "AWAY")
    assert (so.regulation_result, so.decided_in, so.final_winner) == ("TIE", "SHOOTOUT", "HOME")
    assert (so.final_home_score, so.final_away_score) == (4, 3)
    game = session.get(Game, so.game_id)
    assert game.status == GameStatus.FINAL and (game.home_score, game.away_score) == (4, 3)
    assert game.final_at is None  # no completion clock is invented


def test_no_nfl_tie_or_settlement_logic_is_reached(session, artifact_root):
    import ast

    import app.nhl as nhl_pkg

    # No NHL research module imports NFL scoring, settlement or position code. The two paper-lifecycle
    # modules (AISP_NHL_PAPER_POLICY_V1, off by default) own NHL positions and their settlement; from NFL
    # code they may use only the shared, sport-neutral eligibility-record vocabulary (the same one the
    # NBA/CFB policy module reads) -- never NFL pairing, tie or settlement logic (MAIN 4fd: NHL pairs are
    # NHL-local now).
    lifecycle = {"paper.py", "odds_api_price.py"}
    shared_vocabulary = {"KIND", "MUST_REMAIN_FALSE", "MUST_REMAIN_TRUE", "REQUIRED_BINDINGS", "_at",
                         "experiment_key", "stored_document"}
    for path in pathlib.Path(nhl_pkg.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                name = node.module.lower()
                if path.name in lifecycle:
                    if "nfl" in name:
                        assert name == "app.services.nfl_paper_eligibility" and {a.name for a in node.names} <= shared_vocabulary, (path.name, name)
                    continue
                assert "nfl" not in name and "settle" not in name and "position" not in name, (path.name, name)
    _import_all(session, artifact_root)
    from app.models import PositionSettlement

    assert session.query(PositionSettlement).count() == 0
    # An NHL tie can only exist at 60:00; the final never ties and is never a PUSH.
    for r in session.scalars(select(NhlGameResult)):
        assert r.final_winner in ("HOME", "AWAY") and r.final_home_score != r.final_away_score


def test_result_correction_supersedes_without_editing(session, artifact_root, scenario):
    _import_all(session, artifact_root)
    original = scenario["results"]["REG_FINAL"]
    fixed = copy.deepcopy(original)
    fixed["regulation"]["awayGoals"] = 3
    fixed["final"]["awayScore"] = 3
    fixed["supersedesResultSha256"] = original["resultSha256"]
    fixed = c.with_result_identity(fixed)
    s = ImportSummary(run_id="fix")
    assert import_result_bytes(session, c.canonical_bytes(fixed), summary=s, now=NOW) == "IMPORTED"
    assert session.query(NhlGameResult).filter_by(canonical_game_ref="nhl:game:2026010071").count() == 2
    detail = NhlReadService(session).game("2026010071", now=NOW)
    assert detail["result"]["final"]["away_score"] == 3


# ---------------------------------------------------------------- read model --
def test_board_rows_carry_no_market_edge_or_position(session, artifact_root):
    _import_all(session, artifact_root)
    board = NhlReadService(session).board(now=NOW, include_test_data=True)
    assert board["counts"] == {"games": 5, "with_forecast": 4, "finals": 3}
    by_id = {r["source_game_id"]: r for r in board["games"]}
    assert by_id["2026010081"]["forecast_state"] == "FORECAST_PENDING" and by_id["2026010081"]["forecast"] is None
    assert by_id["2026010072"]["status_label"] == "Final OT"
    assert by_id["2026010073"]["status_label"] == "Final SO"
    assert by_id["2026010071"]["status_label"] == "Final"
    for row in board["games"]:
        assert row["markets"]["status"] == "NO_NHL_MARKET_PRICES_COLLECTED"
        assert row["wagering"]["enabled"] is False
        def keys(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield str(k).lower()
                    yield from keys(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from keys(v)

        for key in keys(row):
            assert not any(word in key for word in ("edge", "recommend", "pick", "stake", "price", "odds")), key
        if row["forecast"]:
            reg = row["forecast"]["probabilities"]["regulation"]
            fin = row["forecast"]["probabilities"]["final"]
            assert reg is None or abs(sum(reg.values()) - 1) < 1e-9
            assert abs(sum(fin.values()) - 1) < 1e-9


def test_scores_regulation_three_way_and_final_two_way_separately(session, artifact_root):
    _import_all(session, artifact_root)
    models = NhlReadService(session).performance_models(include_test_data=True)
    assert [m["outcome_definition"] for m in models] == ["REGULATION_3WAY", "FINAL_2WAY"]
    reg, fin = (m["cohorts"]["ALL"] for m in models)
    # The shootout game's forecast is final-only: regulation NOT MODELLED, so it is
    # scored on the final (n=3) but never on regulation (n=2) -- not as a zero.
    assert reg["n"] == 2 and fin["n"] == 3 and fin["regulation_not_modelled"] == 1
    assert reg["brier_two_way"] is None and fin["brier_three_class"] is None
    assert reg["regulation_ties"] == 1
    assert all(m["record_class"] == "PREVIEW_FIXTURE" and m["model_label"].startswith("[PREVIEW FIXTURE") for m in models)
    assert fin["decided_in"] == {"REGULATION": 1, "OVERTIME": 1, "SHOOTOUT": 1}
    games = {g["nhl_game_id"]: g for g in models[0]["games"]}
    ot_game = games["nhl:game:2026010072"]
    p = ot_game["probabilities"]
    expected = (p["home"] ** 2) + ((p["tie"] - 1) ** 2) + (p["away"] ** 2)
    assert ot_game["actual_outcome"] == "TIE" and ot_game["brier_three_class"] == pytest.approx(expected)
    fin_games = {g["nhl_game_id"]: g for g in models[1]["games"]}
    assert fin_games["nhl:game:2026010072"]["actual_outcome"] == "AWAY"


def test_nhl_has_no_verified_publication_cohort_never_a_copy_of_all(session, artifact_root):
    # MAIN 6496d6b cohort P2: no independent NHL publication clock exists, so the
    # verified cohort is empty -- the ALL metrics are never served under its label.
    _import_all(session, artifact_root)
    for model in NhlReadService(session).performance_models(include_test_data=True):
        everything, verified = model["cohorts"]["ALL"], model["cohorts"]["VERIFIED_PUBLICATION"]
        assert everything["n"] > 0 and verified["n"] == 0
        assert verified.keys() == everything.keys()
        assert verified["accuracy"] is None and verified["log_loss"] is None and verified["brier_two_way"] is None
        assert verified["period"] == {"first_start_utc": None, "last_start_utc": None}
        assert all(g["publication_clock"] != "VERIFIED_INDEPENDENT_PUBLICATION" for g in model["games"])
        assert "no independent publication clock" in model["cohort_note"]


def test_test_data_flag_off_hides_fixtures_whatever_the_request_asks(client, session, artifact_root, settings):
    _import_all(session, artifact_root)
    session.commit()
    assert settings.nhl_test_data_enabled is False
    board = client.get("/api/v1/nhl/research", params={"test_data": "true"}).json()
    assert board["counts"]["games"] == 0 and board["test_data_rows"] == 0 and board["test_data_available"] is False
    assert client.get("/api/v1/nhl/games/2026010073").status_code == 404
    perf = client.get("/api/v1/research/model-performance", params={"league": "NHL", "test_data": "true"}).json()
    assert perf["models"] == []


def test_api_endpoints(client, session, artifact_root, settings):
    settings.nhl_test_data_enabled = True  # dev-only flag; production default is False
    _import_all(session, artifact_root)
    session.commit()
    board = client.get("/api/v1/nhl/research", params={"test_data": "true"}).json()
    hidden = client.get("/api/v1/nhl/research").json()  # test data hidden by default
    assert hidden["counts"]["games"] == 0 and hidden["test_data_rows"] == 5
    assert board["counts"]["games"] == 5
    assert client.get("/api/v1/nhl/games/2026010073").json()["result"]["decided_in"] == "SHOOTOUT"
    assert client.get("/api/v1/nhl/games/123").status_code == 404
    perf = client.get("/api/v1/research/model-performance", params={"league": "NHL", "test_data": "true"}).json()
    assert {m["outcome_definition"] for m in perf["models"]} == {"REGULATION_3WAY", "FINAL_2WAY"}
    overview = client.get("/api/v1/research/model-performance/overview", params={"sport": "NHL", "test_data": "true"}).json()
    assert overview["nhl"]["scored"] == 0  # fixtures are never counted as real evidence
    fixture = overview["nhl"]["preview_fixture"]
    assert fixture["regulation_3way"]["n"] == 2 and fixture["regulation_3way"]["not_modelled"] == 1
    assert fixture["final_2way"]["n"] == 3
    assert overview["nhl"]["wagering"]["enabled"] is False
    mlb_only = client.get("/api/v1/research/model-performance/overview", params={"sport": "MLB"}).json()
    assert mlb_only["nhl"] is None
    wagers = client.get("/api/v1/research/wager-results", params={"sport": "NHL"}).json()
    # Since the NHL paper era opened (2026-10-16) NHL is a funded account like the others: no research-only
    # notice; with no era and no position here, its table is simply empty.
    assert wagers["rows"] == [] and wagers["research_only_sports"] == {}
    assert client.get("/api/v1/research/mmqb/index", params={"league": "NHL"}).status_code in (404, 422)
    registry = {e["league_code"]: e for e in client.get("/api/v1/sports/registry").json()["sports"]}
    assert registry["NHL"]["official_positions_supported"] is False
    assert registry["NHL"]["research_href"] == "/nhl/research"


# ------------------------------------------------------------ v1 revision 2 --
def test_final_only_model_is_valid_and_not_fabricated(scenario):
    doc = scenario["forecasts"]["SO_FINAL"]
    assert doc["probabilities"]["regulation"] is None and doc["goals"] == {"regulation": None, "final": None}
    assert c.validate_forecast(doc) == []
    fabricated = copy.deepcopy(doc)
    fabricated["goals"]["regulation"] = {"horizon": c.REGULATION_HORIZON, "expectedHome": 3.0, "expectedAway": 2.8,
                                         "distribution": None}
    assert any(p.startswith("FINAL_ONLY_UNSUPPLIED_CHANNEL") for p in c.validate_forecast(_rehash(fabricated)))
    with_ot = copy.deepcopy(doc)
    with_ot["probabilities"]["overtime"] = {"pOvertime": 0.2, "pShootout": None,
                                            "conditionalOnTieAfterRegulation": None, "conditionalOnShootout": None}
    assert any(p.startswith("FINAL_ONLY_UNSUPPLIED_CHANNEL") for p in c.validate_forecast(_rehash(with_ot)))


def test_regulation_goals_optional_and_distributions_refused(scenario):
    doc = copy.deepcopy(scenario["forecasts"]["REG_FINAL"])
    doc["goals"]["regulation"] = None
    assert c.validate_forecast(_rehash(doc)) == []
    doc = copy.deepcopy(scenario["forecasts"]["REG_FINAL"])
    doc["goals"]["regulation"]["distribution"] = {"maxGoals": 1, "jointHomeAway": [[0.25, 0.25], [0.25, 0.25]]}
    assert any(p.startswith("GOAL_DISTRIBUTION_UNSUPPORTED_V1") for p in c.validate_forecast(_rehash(doc)))


def test_provenance_is_required_and_closed(scenario):
    doc = copy.deepcopy(scenario["forecasts"]["REG_FINAL"])
    doc["provenance"]["evidenceMode"] = "BACKDATED"
    assert "PROVENANCE_INVALID:evidenceMode" in c.validate_forecast(_rehash(doc))
    del doc["provenance"]
    assert "SCHEMA_INVALID:forecast.provenance_MISSING" in c.validate_forecast(_rehash(doc))


def test_producer_evidence_is_verified_when_resolvable(session, artifact_root):
    _import_all(session, artifact_root)
    statuses = {r.producer_evidence_status for r in session.scalars(select(NhlResearchForecast))}
    assert statuses == {"VERIFIED"}


def test_synthetic_documents_only_enter_through_the_synthetic_lane(session, scenario):
    doc = copy.deepcopy(scenario["forecasts"]["FUTURE_V1"])
    doc["limitations"].append(c.SYNTHETIC_MARKER)
    doc["publishedAtUtc"] = None
    doc = _rehash(doc)
    s = ImportSummary(run_id="syn")
    assert import_forecast_bytes(session, c.canonical_bytes(doc), summary=s, now=NOW) == "REJECTED"
    assert "SYNTHETIC_NOT_ADMISSIBLE_AS_FORECAST" in s.receipts[0]["problems"]
    assert import_forecast_bytes(session, c.canonical_bytes(doc), summary=s, now=NOW,
                                 record_class="SYNTHETIC_FIXTURE") == "IMPORTED"
    row = session.scalar(select(NhlResearchForecast))
    assert row.record_class == "SYNTHETIC_FIXTURE" and row.published_utc is None
    assert row.publication_clock_source == "UNPUBLISHED_SYNTHETIC_FIXTURE"


def test_republication_keeps_original_clocks(session, scenario):
    s = ImportSummary(run_id="rp")
    doc = scenario["forecasts"]["REG_FINAL"]
    import_forecast_bytes(session, c.canonical_bytes(doc), summary=s, now=NOW)
    again = copy.deepcopy(doc)
    again["publishedAtUtc"] = fx.z(c.parse_utc(doc["publishedAtUtc"]) + timedelta(minutes=30))
    later = NOW + timedelta(hours=1)
    assert import_forecast_bytes(session, c.canonical_bytes(again), summary=s, now=later) == "DUPLICATE"
    assert "REPUBLISHED_SAME_IDENTITY_ORIGINAL_CLOCKS_PRESERVED" in s.receipts[-1]["problems"]
    row = session.scalar(select(NhlResearchForecast))
    assert row.published_utc.replace(tzinfo=UTC) == c.parse_utc(doc["publishedAtUtc"])
    assert row.imported_at.replace(tzinfo=UTC) == NOW


CODEX = pathlib.Path(r"C:/Users/james/asp-model-studio/outputs/nhl-gen1-overnight-20261001-v1/contract/fixtures")


@pytest.mark.skipif(not CODEX.is_dir(), reason="Model Studio synthetic fixtures not present on this machine")
def test_model_studio_synthetic_fixtures(session):
    s = ImportSummary(run_id="codex")
    expected = {"elo_final_only.json": "IMPORTED", "regulation_candidate.json": "IMPORTED",
                "refuse_final_only_fabricated_regulation_goal.json": "REJECTED",
                "refuse_overflow_matrix.json": "REJECTED", "refuse_funded_authority.json": "REJECTED",
                "refuse_inconsistent_final_mapping.json": "REJECTED"}
    for name, want in expected.items():
        raw = (CODEX / name).read_bytes()
        got = import_forecast_bytes(session, raw, summary=s, now=NOW, record_class="SYNTHETIC_FIXTURE",
                                    admit_unbound_games=True, source_label=name)
        assert got == want, (name, s.receipts[-1]["problems"])
    # Same game, two different models: two separate forecasts, not a revision.
    assert [r["disposition"] for r in s.receipts[:2]] == ["IMPORTED", "IMPORTED"]


REAL = pathlib.Path(r"C:/Users/james/asp-model-exchange/nhl-gen1-research-v1")


@pytest.mark.skipif(not (REAL / "current.json").is_file(), reason="authentic NHL package not present on this machine")
def test_authentic_package_imports_then_reimports_as_duplicate(session):
    first = import_exchange(session, REAL, now=NOW, admit_unbound_games=True)
    assert first.problems == [] and first.counts["REJECTED"] == 0 and first.counts["IMPORTED"] >= 1
    rows = {r.forecast_id: (r.object_sha256, r.imported_at) for r in session.scalars(select(NhlResearchForecast))}
    assert all(r.producer_evidence_status == "VERIFIED" for r in session.scalars(select(NhlResearchForecast)))
    again = import_exchange(session, REAL, now=NOW + timedelta(hours=1), admit_unbound_games=True)
    assert again.counts["DUPLICATE"] == first.counts["IMPORTED"] and again.counts["IMPORTED"] == 0
    assert {r.forecast_id: (r.object_sha256, r.imported_at)
            for r in session.scalars(select(NhlResearchForecast))} == rows



def test_test_data_is_excluded_from_counts_and_scores_by_default(session, artifact_root):
    _import_all(session, artifact_root)
    svc = NhlReadService(session)
    assert svc.board(now=NOW)["counts"] == {"games": 0, "with_forecast": 0, "finals": 0}
    assert svc.performance_models() == []
    block = svc.overview_block()
    assert block["preview_fixture"]["scored"] == 0 and block["test_data_rows"] == 5
    assert svc.game("2026010073", now=NOW)["is_fixture"] is True  # still reachable directly, labelled



# ------------------------------------------------------------------ schedule --
from app.nhl import schedule as sched  # noqa: E402
from app.nhl.gen1_import import import_schedule_bytes  # noqa: E402


def _schedule(games, *, created="2026-10-01T03:00:00Z"):
    doc = {"schema": sched.SCHEDULE_SCHEMA, "canonicalization": c.CANONICALIZATION, "league": "NHL", "sport": "HOCKEY",
           "scheduleId": None, "season": "2026-27", "seasonStartYear": 2026,
           "coverage": {"fromDate": "2026-10-01", "toDate": "2026-10-03", "complete": False},
           "source": {"source": "TEST_SCHEDULE", "observedAtUtc": None, "retrievedAtUtc": created, "evidenceRef": None},
           "createdAtUtc": created, "publishedAtUtc": None, "researchStatus": "RESEARCH_ONLY", "games": games}
    return sched.with_schedule_identity(doc)


def _sg(source_id, home, away, start, status="SCHEDULED"):
    return {**fx.game_block(source_id, home, away, start), "status": status}


def test_schedule_creates_games_without_any_forecast(session):
    doc = _schedule([_sg("2026020101", "TOR", "MTL", datetime(2026, 10, 2, 23, tzinfo=UTC)),
                     _sg("2026020102", "BOS", "NYR", datetime(2026, 10, 3, 0, tzinfo=UTC))])
    assert sched.validate_schedule(doc) == []
    s = ImportSummary(run_id="sch")
    counts = import_schedule_bytes(session, c.canonical_bytes(doc), summary=s, now=NOW)
    assert counts["IMPORTED"] == 2
    board = NhlReadService(session).board(now=NOW)
    assert board["counts"] == {"games": 2, "with_forecast": 0, "finals": 0}
    assert {r["forecast_label"] for r in board["games"]} == {"Forecast pending"}
    assert {r["source_mode"] for r in board["games"]} == {"SCHEDULE_PACKAGE"}


def test_schedule_reimport_is_unchanged_and_a_move_is_an_update(session):
    start = datetime(2026, 10, 2, 23, tzinfo=UTC)
    doc = _schedule([_sg("2026020101", "TOR", "MTL", start)])
    s = ImportSummary(run_id="sch")
    import_schedule_bytes(session, c.canonical_bytes(doc), summary=s, now=NOW)
    assert import_schedule_bytes(session, c.canonical_bytes(doc), summary=s, now=NOW)["UNCHANGED"] == 1
    moved = _schedule([_sg("2026020101", "TOR", "MTL", start + timedelta(minutes=30))], created="2026-10-01T04:00:00Z")
    assert import_schedule_bytes(session, c.canonical_bytes(moved), summary=s, now=NOW)["UPDATED"] == 1
    postponed = _schedule([_sg("2026020101", "TOR", "MTL", start + timedelta(minutes=30), "POSTPONED")],
                          created="2026-10-01T05:00:00Z")
    import_schedule_bytes(session, c.canonical_bytes(postponed), summary=s, now=NOW)
    game = session.scalar(select(Game))
    assert game.status == GameStatus.POSTPONED and session.query(Game).count() == 1


def test_schedule_orientation_conflict_and_invalid_documents_are_rejected(session):
    start = datetime(2026, 10, 2, 23, tzinfo=UTC)
    s = ImportSummary(run_id="sch")
    import_schedule_bytes(session, c.canonical_bytes(_schedule([_sg("2026020101", "TOR", "MTL", start)])), summary=s, now=NOW)
    flipped = _schedule([_sg("2026020101", "MTL", "TOR", start)], created="2026-10-01T04:00:00Z")
    assert import_schedule_bytes(session, c.canonical_bytes(flipped), summary=s, now=NOW)["REJECTED"] == 1
    bad = _schedule([_sg("2026020101", "TOR", "MTL", start)])
    bad["games"][0]["gameType"] = "PLAYOFF"
    assert any(p.startswith("SCHEDULE_ID_NOT_CONTENT_HASH") or "gameType" in p for p in sched.validate_schedule(bad))


def test_forecast_binds_to_a_schedule_game_without_admission(session, scenario):
    doc = scenario["forecasts"]["FUTURE_V1"]
    g = doc["game"]
    sdoc = _schedule([{**g, "status": "SCHEDULED"}])
    s = ImportSummary(run_id="sch")
    import_schedule_bytes(session, c.canonical_bytes(sdoc), summary=s, now=NOW)
    assert import_forecast_bytes(session, c.canonical_bytes(doc), summary=s, now=NOW) == "IMPORTED"
    assert s.receipts[-1]["game_bound"] is True and "UNBOUND_GAME" not in s.receipts[-1]["problems"]


def test_pointer_imports_schedule_before_forecasts(session, artifact_root, scenario):
    root = artifact_root / "pointer"
    doc = scenario["forecasts"]["FUTURE_V1"]
    sobj = fx.write_object(root, _schedule([{**doc["game"], "status": "SCHEDULED"}]), "schedule.json")
    smanifest = fx.write_manifest(root, c.FORECAST_MANIFEST_SCHEMA, [sobj], NOW)
    # rewrite as a schedule manifest
    import json as _json
    mpath = root / smanifest["path"]
    m = _json.loads(mpath.read_bytes())
    m["schema"] = sched.SCHEDULE_MANIFEST_SCHEMA
    m["counts"] = {"schedules": 1}
    raw = c.canonical_bytes(m)
    sha = c.sha256_hex(raw)
    (root / "manifests" / "sha256" / sha).mkdir(parents=True, exist_ok=True)
    (root / "manifests" / "sha256" / sha / "manifest.json").write_bytes(raw)
    fobj = fx.write_object(root, doc, "forecast.json")
    fref = fx.write_manifest(root, c.FORECAST_MANIFEST_SCHEMA, [fobj], NOW)
    fx.write_pointer(root, fref, None)
    pointer = _json.loads((root / "current.json").read_bytes())
    pointer["scheduleManifest"] = {"path": f"manifests/sha256/{sha}/manifest.json", "sha256": sha, "bytes": len(raw)}
    (root / "current.json").write_bytes(c.canonical_bytes(pointer))
    summary = import_exchange(session, root, now=NOW)
    assert summary.problems == []
    assert summary.schedule_counts["IMPORTED"] == 1 and summary.counts["IMPORTED"] == 1
    assert summary.receipts[-1]["game_bound"] is True


# ------------------------------------------------------- wagering exclusion --
def test_nhl_never_enters_settlement_positions_or_market_listing(session, artifact_root):
    from app.models import OfficialAspPosition, PositionSettlement, ShadowExperimentDecision
    from app.services import command_center
    from app.services.wager_results_read import wager_results

    _import_all(session, artifact_root)
    finals = session.query(Game).filter(Game.status == GameStatus.FINAL).count()
    assert finals == 3
    from app.bankroll.settlement import settle_completed_positions

    assert settle_completed_positions(session) == []
    assert session.query(PositionSettlement).count() == 0
    assert session.query(OfficialAspPosition).count() == 0
    assert session.query(ShadowExperimentDecision).count() == 0
    # Listed READ-ONLY on the slate since the paper era opened (2026-10-16): listing writes nothing,
    # and with no era here there is still no decision, position or settlement.
    assert "NHL" in command_center.LISTED_LEAGUES
    assert session.query(ShadowExperimentDecision).count() == 0
    assert wager_results(session, sport="NHL")["rows"] == []


def test_nhl_games_fail_every_governed_league_scope(session, scenario):
    from app.services.league_scope import MLB, LeagueMismatchError, in_league, require_league

    gid = ensure_game(session, scenario["games"]["REG_FINAL"], source_mode="FIXTURE")
    game = session.get(Game, gid)
    assert session.scalars(select(Game.id).where(in_league(MLB))).all() == []
    assert session.scalars(select(Game.id).where(in_league("NFL"))).all() == []
    with pytest.raises(LeagueMismatchError):
        require_league(session, game, MLB, process="test")


def test_registry_keeps_nhl_out_of_positions_and_navigation():
    from app.sports.registry import active_leagues, get_league, navigable_leagues

    nhl = get_league("NHL")
    assert nhl.official_positions_supported is False and nhl.navigable is False
    assert "NHL" not in {str(e.league_code) for e in active_leagues()}
    assert "NHL" not in {str(e.league_code) for e in navigable_leagues()}


def test_wagering_modules_never_reference_nhl():
    """Static guard: decision, staking, position and settlement modules contain no NHL path."""
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    guarded = ["services/shadow_experiment_service.py", "bankroll/settlement.py", "bankroll/positions.py",
               "bankroll/service.py", "services/house_divided.py", "cfb/paper_decision.py", "nba/gen1_paper.py",
               "parlay/tickets.py", "services/command_center.py", "services/wager_results_read.py"]
    exempt: dict[str, tuple[str, ...]] = {}
    for rel in guarded:
        path = root / rel
        assert path.is_file(), rel
        text = path.read_text(encoding="utf-8")
        if rel == "services/shadow_experiment_service.py":
            # Its only NHL line is the one that hands NHL positions to their own owner (app.nhl.paper),
            # so the generic score rule never settles them; any other NHL path here is refused.
            code = [line for line in text.splitlines() if "NHL" in line and not line.strip().startswith("#")]
            assert code == ['        if experiment.sport == "NHL":'], code
            continue
        if rel in ("services/command_center.py", "services/wager_results_read.py"):
            # Since the NHL era was funded (2026-10-16) these READ models list its account, slate and
            # positions. They may name NHL, but never reach an NHL decision, record or settlement path.
            code = [line for line in text.splitlines() if "NHL" in line and not line.strip().startswith("#")]
            assert code, rel
            forbidden = ("settle(", "decide(", "record(", "evaluate(", "run_checkpoint", ".add(", "commit(")
            assert not [line for line in code if any(f in line for f in forbidden)], rel
            assert "from app.nhl.paper import" not in text and "import app.nhl.paper" not in text, rel
            for name in ("settle", "decide", "record", "evaluate", "run_checkpoint", "correct"):
                assert f"nhl_paper.{name}(" not in text and f"paper.{name}(" not in text, (rel, name)
            continue
        if rel in exempt:
            # Only the "no official NHL positions" notice may name NHL there.
            for line in text.splitlines():
                if "NHL" in line and not line.strip().startswith("#"):
                    assert any(k in line for k in ("RESEARCH_ONLY_SPORTS", "SPORT_LABELS", "SPORTS =", "NHL\"",
                                                   "_research_only_notice", "wager_results_notice", "if code ==")), line
            continue
        assert "NHL" not in text, rel



def test_nhl_external_ids_are_namespaced(session, scenario):
    from app.models import ExternalIdMapping

    ensure_game(session, scenario["games"]["REG_FINAL"], source_mode="FIXTURE")
    ids = {m.external_id for m in session.scalars(select(ExternalIdMapping))}
    assert ids == {"nhl:game:2026010071", "nhl:team:10", "nhl:team:8"}


def test_shared_loops_exclude_nhl(session, scenario, settings):
    from app.services.rff_capture import due_targets

    game = dict(scenario["games"]["FUTURE"])
    gid = ensure_game(session, game, source_mode="FIXTURE")
    start = session.get(Game, gid).starts_at
    assert due_targets(session, now=start.replace(tzinfo=UTC) - timedelta(minutes=30)) == []
    assert "NHL" not in settings.model_copy(update={"prediction_market_sports": "MLB,NFL,NHL"}).prediction_market_sport_list
