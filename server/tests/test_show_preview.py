"""AISP DAY-BEFORE SHOW PREVIEW V1: contracts, board, freeze, flags and separation.

Every test runs against the isolated in-memory SQLite session; nothing here
touches the production database, calls a provider or runs a model.
"""
from __future__ import annotations

import ast
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.audit.immutability import ImmutableRecordError
from app.core.config import Settings
from app.models import (
    ExperimentDecisionPhase, Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType, OddsSnapshot,
    OfficialAspPosition, PositionResult, PositionStatus, ProviderRequestLedger, Season, ShadowExperiment,
    ShadowExperimentDecision, Sport, Sportsbook, Team,
)
from app.models.show_preview import ShowPreviewForecast, ShowPreviewGameBinding, ShowPreviewSnapshot
from app.show_preview import collection
from app.show_preview.board import build_board
from app.show_preview.clock import next_relevant_slate_date, planned_freeze_utc
from app.show_preview.contract import (
    ContractViolation, content_hash, preview_difference, settlement_rule, validate_forecast,
)
from app.show_preview.freeze import (
    ShowPreviewDisabled, SnapshotAlreadyFrozen, current_snapshot, freeze_show_board, preview_vs_final,
    record_final_comparisons, verify_snapshot,
)
from app.show_preview.imports import import_preview_forecast
from app.show_preview.jobs import AUTO_FREEZE_JOB_ID, register_show_preview_jobs, run_show_preview_auto_freeze

SLATE = date(2026, 9, 17)
START = datetime(2026, 9, 17, 23, 5, tzinfo=UTC)          # 7:05 PM ET
FREEZE = datetime(2026, 9, 16, 20, 30, tzinfo=UTC)         # 4:30 PM ET the day before
BEFORE = FREEZE - timedelta(hours=2)


def enabled(settings: Settings, **extra) -> Settings:
    return settings.model_copy(update={"show_preview_enabled": True, **extra})


@pytest.fixture
def world(session):
    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026", starts_at=START - timedelta(days=180), ends_at=START + timedelta(days=40))
    home = Team(league_id=league.id, name="New York Yankees", abbreviation="NYY")
    away = Team(league_id=league.id, name="Boston Red Sox", abbreviation="BOS")
    session.add_all([season, home, away])
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=START, status=GameStatus.SCHEDULED)
    session.add(game)
    session.flush()
    session.add_all([GameTeam(game_id=game.id, team_id=home.id, is_home=True),
                     GameTeam(game_id=game.id, team_id=away.id, is_home=False)])
    markets = {}
    for kind in (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL):
        market = Market(game_id=game.id, market_type=kind, period="full_game")
        session.add(market)
        session.flush()
        keys = ("over", "under") if kind == MarketType.TOTAL else ("home", "away")
        outcomes = {}
        for key in keys:
            participant = home.id if key == "home" else away.id if key == "away" else None
            outcome = MarketOutcome(market_id=market.id, outcome_key=key, label=key, participant_team_id=participant)
            session.add(outcome)
            session.flush()
            outcomes[key.upper()] = outcome
        markets[kind.name] = outcomes
    books = {key: Sportsbook(name=name, key=key) for key, name in
             (("sgo:fanduel", "Fanduel (SportsGameOdds, research)"), ("sgo:betmgm", "Betmgm (SportsGameOdds, research)"),
              ("draftkings", "DraftKings"))}
    session.add_all(books.values())
    session.flush()
    return {"session": session, "league": league, "game": game, "home": home, "away": away, "markets": markets, "books": books}


def quote(world, market: str, side: str, book: str, american: int, at: datetime, *, source="SPORTSGAMEODDS",
          point=None, retrieved: datetime | None = None) -> None:
    decimal = (1 + american / 100) if american > 0 else (1 - 100 / american)
    world["session"].add(OddsSnapshot(
        market_outcome_id=world["markets"][market][side].id, sportsbook_id=world["books"][book].id,
        observed_at=at, available_as_of=retrieved or at, american_odds=american, decimal_odds=decimal, point=point,
        implied_probability=1 / decimal, source=source, source_mode="LIVE"))
    world["session"].flush()


def forecast_doc(world, *, home_win=0.58, away_win=0.42, tie=0.0, created=BEFORE, **overrides) -> dict:
    doc = {
        "schema_version": "AISP_SHOW_PREVIEW_FORECAST_V1", "forecast_id": f"preview-{uuid4().hex[:8]}", "horizon": "SHOW_PREVIEW",
        "sport": "MLB", "league": "MLB", "season": "2026",
        "game": {"canonical_game_id": str(world["game"].id), "source_game_ref": "mlb-777", "slate_date": SLATE.isoformat(),
                 "kickoff_utc": START.isoformat(), "away": {"abbreviation": "BOS", "name": "Boston"},
                 "home": {"abbreviation": "NYY", "name": "New York"}},
        "created_utc": created.isoformat(), "source_cutoff_utc": (created - timedelta(minutes=30)).isoformat(),
        "model": {"model_id": "MLB_PREVIEW_TEST", "model_version": "v0-test", "artifact_sha256": "a" * 64},
        "expected_score": {"away": 4.1, "home": 4.9},
        "probabilities": {"away_win": away_win, "home_win": home_win, "tie": tie},
        "availability": {"starters": "PROBABLE", "lineups": "UNKNOWN"},
        "missing_evidence": [],
        "authority": {"research_only": True, "grants_wagering_authority": False, "final_pregame_evidence": False},
    }
    doc.update(overrides)
    doc["integrity"] = {"content_sha256": content_hash(doc), "physical_files": []}
    return doc


def complete_moneyline(world, at=BEFORE):
    quote(world, "MONEYLINE", "HOME", "sgo:fanduel", -120, at)
    quote(world, "MONEYLINE", "AWAY", "sgo:fanduel", 110, at)
    quote(world, "MONEYLINE", "HOME", "sgo:betmgm", -125, at)
    quote(world, "MONEYLINE", "AWAY", "sgo:betmgm", 105, at)


# ------------------------------------------------------------ flags & scope --

def test_every_switch_defaults_false_and_a_missing_setting_is_not_enabled() -> None:
    # app_env="test": outside tests, Settings now refuses to start without an
    # explicit DATABASE_URL and SECRET_KEY (SEC-DB-001), which is not what this
    # test is about. The switch defaults are the same in every environment.
    settings = Settings(_env_file=None, app_env="test")
    assert settings.show_preview_enabled is False
    assert settings.show_preview_provider_collection_enabled is False
    assert settings.show_preview_auto_freeze_enabled is False


def test_collection_never_requests_even_when_switched_on(session, settings) -> None:
    before = session.scalar(select(func.count()).select_from(ProviderRequestLedger))
    assert collection.collect_preview_markets(settings)["reason"] == collection.DISABLED
    on = enabled(settings, show_preview_provider_collection_enabled=True)
    result = collection.collect_preview_markets(on)
    assert result["attempted"] is False and result["objects"] == 0 and result["reservations"] == 0
    # The collector is complete, so switching it on no longer answers "not
    # implemented". It answers that it was given no session to read or write
    # with -- and it still requests nothing and reserves nothing.
    assert result["reason"] == collection.NO_SESSION
    assert session.scalar(select(func.count()).select_from(ProviderRequestLedger)) == before


FORBIDDEN_IMPORTS = ("sgo_collection", "provider_allocation", "odds_api", "the_odds_api",
                     "httpx", "requests", "sportsgameodds")


def _imported_names(tree: ast.AST) -> list[str]:
    names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    return names + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]


def test_show_preview_code_imports_no_provider_or_http_client() -> None:
    """Every module except the collector, which is the only one that may spend."""

    for path in Path("app/show_preview").glob("*.py"):
        if path.name == "collection.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not [n for n in _imported_names(tree)
                    if any(bad in n for bad in FORBIDDEN_IMPORTS)], path


def test_the_collector_reaches_a_provider_only_from_inside_a_function() -> None:
    """The one module that may spend imports nothing provider-shaped at import time.

    Importing ``app.show_preview.collection`` -- which the API, the read layer
    and this test suite all do -- must not pull in an HTTP client or the
    allocation machinery. Every provider import lives inside a function, below
    the switch check, so reaching one at all means both switches were true.
    """

    tree = ast.parse(Path("app/show_preview/collection.py").read_text(encoding="utf-8"))
    functions = {node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    nested = {child for function in functions for child in ast.walk(function)}
    module_level = [node for node in ast.walk(tree)
                    if isinstance(node, (ast.Import, ast.ImportFrom)) and node not in nested]
    offenders = [name for node in module_level for name in _imported_names(node)
                 if any(bad in name for bad in FORBIDDEN_IMPORTS)]
    assert offenders == [], offenders


def test_auto_freeze_is_registered_only_when_both_switches_are_on(settings) -> None:
    class Recorder:
        def __init__(self):
            self.ids = []

        def add_job(self, *args, **kwargs):
            self.ids.append(kwargs["id"])

    for case in (settings, enabled(settings), settings.model_copy(update={"show_preview_auto_freeze_enabled": True})):
        recorder = Recorder()
        assert register_show_preview_jobs(recorder, case, {}) == [] and recorder.ids == []
    recorder = Recorder()
    register_show_preview_jobs(recorder, enabled(settings, show_preview_auto_freeze_enabled=True), {})
    assert recorder.ids == [AUTO_FREEZE_JOB_ID]


def test_disabled_workflow_writes_nothing(world, settings) -> None:
    session = world["session"]
    with pytest.raises(ShowPreviewDisabled):
        freeze_show_board(session, SLATE, settings, now=FREEZE, trigger="TEST")
    with pytest.raises(ShowPreviewDisabled):
        import_preview_forecast(session, forecast_doc(world), settings, now=FREEZE)
    assert run_show_preview_auto_freeze(session, settings, now=FREEZE)["reason"] == "SHOW_PREVIEW_AUTO_FREEZE_DISABLED"
    assert session.scalar(select(func.count()).select_from(ShowPreviewSnapshot)) == 0
    assert session.scalar(select(func.count()).select_from(ShowPreviewForecast)) == 0


# ------------------------------------------------------------------ contract --

def test_contract_refuses_relabelled_finals_wagering_authority_and_tampering(world) -> None:
    good = forecast_doc(world)
    assert validate_forecast(good, now=FREEZE).content_sha256 == good["integrity"]["content_sha256"]
    with pytest.raises(ContractViolation, match="horizon"):
        validate_forecast(forecast_doc(world, horizon="FINAL_PREGAME"), now=FREEZE)
    with pytest.raises(ContractViolation, match="authority"):
        validate_forecast(forecast_doc(world, authority={"research_only": True, "grants_wagering_authority": True,
                                                         "final_pregame_evidence": False}), now=FREEZE)
    tampered = forecast_doc(world)
    tampered["expected_score"]["home"] = 9.9
    with pytest.raises(ContractViolation, match="content_sha256"):
        validate_forecast(tampered, now=FREEZE)
    with pytest.raises(ContractViolation, match="sum to 1"):
        validate_forecast(forecast_doc(world, home_win=0.7, away_win=0.4), now=FREEZE)


def test_import_is_idempotent_and_checks_the_schedule(world, settings) -> None:
    session = world["session"]
    doc = forecast_doc(world)
    row, created = import_preview_forecast(session, doc, enabled(settings), now=FREEZE)
    again, created_again = import_preview_forecast(session, doc, enabled(settings), now=FREEZE)
    assert created and not created_again and again.id == row.id
    assert row.horizon == "SHOW_PREVIEW" and row.grants_wagering_authority is False
    wrong = forecast_doc(world, game={**doc["game"], "home": {"abbreviation": "TOR", "name": "Toronto"}})
    with pytest.raises(ContractViolation, match="teams disagree"):
        import_preview_forecast(session, wrong, enabled(settings), now=FREEZE)


# ------------------------------------------------------------------- markets --

def test_a_lone_side_or_mixed_providers_never_pairs_and_never_compares(world, settings) -> None:
    session = world["session"]
    import_preview_forecast(session, forecast_doc(world), enabled(settings), now=FREEZE)
    quote(world, "MONEYLINE", "HOME", "sgo:fanduel", -120, BEFORE)                         # lone side
    quote(world, "MONEYLINE", "HOME", "sgo:betmgm", -125, BEFORE, source="SPORTSGAMEODDS")
    quote(world, "MONEYLINE", "AWAY", "sgo:betmgm", 105, BEFORE, source="THE_ODDS_API")  # different provider
    game = build_board(session, SLATE, settings, evidence_as_of=FREEZE)["games"][0]
    assert all(not pair["complete"] for pair in game["markets"]["MONEYLINE"])
    assert game["comparison"] is None
    assert {"PARTIAL_MARKET"} <= {m["code"] for m in game["missing"]}
    assert game["readiness"] == "FORECAST_ONLY"


def test_a_spread_with_a_moved_line_on_one_side_is_incomplete(world, settings) -> None:
    quote(world, "SPREAD", "HOME", "sgo:fanduel", 140, BEFORE, point=-1.5)
    quote(world, "SPREAD", "AWAY", "sgo:fanduel", -160, BEFORE, point=2.5)
    quote(world, "TOTAL", "OVER", "sgo:fanduel", -110, BEFORE, point=8.5)
    quote(world, "TOTAL", "UNDER", "sgo:fanduel", -110, BEFORE, point=8.5)
    game = build_board(world["session"], SLATE, settings, evidence_as_of=FREEZE)["games"][0]
    assert game["markets"]["SPREAD"][0]["complete"] is False
    assert game["markets"]["TOTAL"][0]["complete"] is True
    assert game["markets"]["TOTAL"][0]["settlement"] == "NO_PUSH_POSSIBLE"


def test_evidence_after_the_cut_is_invisible(world, settings) -> None:
    quote(world, "MONEYLINE", "HOME", "sgo:fanduel", -120, FREEZE + timedelta(minutes=1))
    quote(world, "MONEYLINE", "AWAY", "sgo:fanduel", 110, FREEZE + timedelta(minutes=1))
    # Observed before the cut but only retrieved after it: still invisible.
    quote(world, "MONEYLINE", "HOME", "sgo:betmgm", -125, BEFORE, retrieved=FREEZE + timedelta(minutes=5))
    game = build_board(world["session"], SLATE, settings, evidence_as_of=FREEZE)["games"][0]
    assert game["markets"]["MONEYLINE"] == []
    assert "MARKET_NOT_POSTED" in {m["code"] for m in game["missing"]}
    assert game["forecast_status"] == "AWAITING_PRODUCER"
    assert game["missing"][0]["message"] == "Preview forecast awaiting producer"


def test_an_mlb_preview_forecast_reaches_the_board_with_its_limitations(world, settings) -> None:
    """The wiring, end to end: the disclosures are on the row and on the board.

    They are not optional presentation. The MLB preview model estimates its win
    probability and its two scores separately, so a projected total shown beside
    a sportsbook total is the exact number a viewer would otherwise read as an
    Over/Under opinion nobody formed.
    """

    session = world["session"]
    import_preview_forecast(session, forecast_doc(world), enabled(settings), now=FREEZE)
    board = build_board(session, SLATE, settings, evidence_as_of=FREEZE)
    game = board["games"][0]
    codes = [item["code"] for item in game["disclosures"]]
    assert codes == ["TEAM_HISTORY_MODEL_ONLY", "NO_JOINT_SCORE_DISTRIBUTION",
                     "NO_TOTAL_OR_RUN_LINE_PROBABILITY", "RESEARCH_ONLY", "NOT_FINAL_V18"]
    # This package states PROBABLE starters, so the "unknown" line is dropped
    # and the existing PROBABLE_STARTERS note carries what it does say.
    assert "STARTERS_AND_LINEUPS_UNKNOWN" not in codes
    assert "PROBABLE_STARTERS" in {item["code"] for item in game["missing"]}
    # And the board-level list, which the print view reads, carries them too.
    assert {(d["game_key"], d["code"]) for d in board["disclosures"]} >= {
        (game["game_key"], code) for code in codes}
    # The pre-existing board disclosures are still there: the per-game list must
    # not have displaced them.
    assert "PREVIEW_COLLECTION_DISABLED" in {d["code"] for d in board["disclosures"]}


def test_a_game_without_a_preview_forecast_is_given_no_limitations_to_read(world, settings) -> None:
    """An absent forecast is reported as absent, never described."""

    board = build_board(world["session"], SLATE, settings, evidence_as_of=FREEZE)
    game = board["games"][0]
    assert game["forecast_status"] == "AWAITING_PRODUCER"
    assert game["disclosures"] == []


def test_a_stale_preview_price_is_shown_but_not_compared(world, settings) -> None:
    session = world["session"]
    import_preview_forecast(session, forecast_doc(world), enabled(settings), now=FREEZE)
    complete_moneyline(world, at=FREEZE - timedelta(hours=30))
    game = build_board(session, SLATE, settings, evidence_as_of=FREEZE)["games"][0]
    assert all(pair["stale"] for pair in game["markets"]["MONEYLINE"])
    assert game["comparison"] is None
    assert "STALE_PREVIEW_PRICE" in {m["code"] for m in game["missing"]}


# ---------------------------------------------------------------- arithmetic --

def test_preview_difference_is_research_arithmetic_not_an_adjusted_edge(world, settings) -> None:
    session = world["session"]
    import_preview_forecast(session, forecast_doc(world, home_win=0.58, away_win=0.42), enabled(settings), now=FREEZE)
    complete_moneyline(world)
    board = build_board(session, SLATE, settings, evidence_as_of=FREEZE)
    comparison = board["games"][0]["comparison"]
    assert comparison["label"] == "Preview model-market difference"
    # Best home price is Fanduel -120 (1.8333) against +110 (2.10): no-vig home 0.5339.
    assert comparison["selection"] == "HOME"
    assert comparison["no_vig_probability"] == pytest.approx(0.533898, abs=1e-5)
    assert comparison["difference"] == pytest.approx(0.58 - 0.533898, abs=1e-5)
    assert comparison["expected_return"] == pytest.approx(0.58 * (1 / 1.2) - 0.42, abs=1e-5)
    assert board["ranking"][0]["rank"] == 1
    assert board["authority"]["grants_wagering_authority"] is False
    assert "adjusted" not in str(comparison).lower()


def test_a_tie_refund_market_scores_the_tie_as_a_push() -> None:
    assert settlement_rule("NFL", "MONEYLINE", None) == "TIE_REFUNDS_STAKE"
    assert settlement_rule("MLB", "MONEYLINE", None) == "TWO_WAY_NO_TIE"
    assert settlement_rule("NFL", "SPREAD", -3.0) == "EXACT_LINE_PUSH_REFUNDS"
    numbers = preview_difference(side="HOME", probabilities={"home_win": 0.5, "away_win": 0.45, "tie": 0.05},
                                 decimal_side=2.0, decimal_other=2.0, settlement="TIE_REFUNDS_STAKE")
    assert numbers["expected_return"] == pytest.approx(0.5 * 1.0 - 0.45)
    assert numbers["model_probability"] == pytest.approx(0.5 / 0.95)


# -------------------------------------------------------------------- freeze --

def test_freeze_is_immutable_idempotent_and_succeeds_only_with_a_reason(world, settings) -> None:
    session = world["session"]
    on = enabled(settings)
    import_preview_forecast(session, forecast_doc(world), on, now=FREEZE)
    complete_moneyline(world)
    first = freeze_show_board(session, SLATE, on, now=FREEZE, trigger="TEST")
    assert first.created and verify_snapshot(first.snapshot)
    assert session.scalar(select(func.count()).select_from(ShowPreviewGameBinding)) == 1

    # Unchanged evidence a minute later: the same snapshot, nothing new written.
    again = freeze_show_board(session, SLATE, on, now=FREEZE + timedelta(minutes=1), trigger="TEST")
    assert not again.created and again.snapshot.id == first.snapshot.id

    # New evidence: refused without a reason, then a successor that keeps its predecessor.
    quote(world, "MONEYLINE", "HOME", "draftkings", -118, FREEZE + timedelta(minutes=2), source="THE_ODDS_API")
    quote(world, "MONEYLINE", "AWAY", "draftkings", 108, FREEZE + timedelta(minutes=2), source="THE_ODDS_API")
    with pytest.raises(SnapshotAlreadyFrozen):
        freeze_show_board(session, SLATE, on, now=FREEZE + timedelta(minutes=3), trigger="TEST")
    successor = freeze_show_board(session, SLATE, on, now=FREEZE + timedelta(minutes=3), trigger="TEST",
                                  supersede_reason="late market posted")
    assert successor.created and successor.snapshot.predecessor_id == first.snapshot.id
    assert successor.superseded.id == first.snapshot.id
    assert current_snapshot(session, SLATE).id == successor.snapshot.id
    assert verify_snapshot(first.snapshot) and first.snapshot.snapshot_sha256 != successor.snapshot.snapshot_sha256

    first.snapshot.supersede_reason = "edited"
    with pytest.raises(ImmutableRecordError):
        session.flush()
    session.rollback()


def test_tampering_with_a_stored_payload_is_detected(world, settings) -> None:
    session = world["session"]
    result = freeze_show_board(session, SLATE, enabled(settings), now=FREEZE, trigger="TEST")
    payload = dict(result.snapshot.payload)
    payload["readiness"] = {**payload["readiness"], "games": 99}
    forged = ShowPreviewSnapshot(snapshot_sha256=result.snapshot.snapshot_sha256, payload=payload)
    assert verify_snapshot(forged) is False


def test_freezing_creates_no_position_decision_or_ledger_row(world, settings) -> None:
    session = world["session"]
    on = enabled(settings)
    import_preview_forecast(session, forecast_doc(world), on, now=FREEZE)
    complete_moneyline(world)
    counts = lambda: [session.scalar(select(func.count()).select_from(t))  # noqa: E731
                      for t in (OfficialAspPosition, ShadowExperimentDecision, ProviderRequestLedger)]
    before = counts()
    freeze_show_board(session, SLATE, on, now=FREEZE, trigger="TEST")
    assert counts() == before


# ------------------------------------------------------------ dates & zones --

def test_clock_is_local_time_on_the_day_before_across_daylight_saving(settings) -> None:
    assert planned_freeze_utc(SLATE, settings) == FREEZE
    # 2026-11-01 ends DST: a 2026-11-02 slate freezes at 4:30 PM EST = 21:30 UTC.
    assert planned_freeze_utc(date(2026, 11, 2), settings) == datetime(2026, 11, 1, 21, 30, tzinfo=UTC)


def test_default_slate_is_the_next_date_with_games_not_blindly_tomorrow(world, settings) -> None:
    session = world["session"]
    # 11:30 PM ET on 9/14 is already 9/15 in UTC; the local day decides. 9/15 and 9/16 have no games.
    late = datetime(2026, 9, 15, 3, 30, tzinfo=UTC)
    assert next_relevant_slate_date(session, late, settings) == SLATE
    empty = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
    assert next_relevant_slate_date(session, empty, settings) == date(2026, 12, 2)


# ------------------------------------------------------ preview versus final --

def test_preview_versus_final_records_a_changed_selection_neutrally(world, settings) -> None:
    session = world["session"]
    on = enabled(settings)
    import_preview_forecast(session, forecast_doc(world), on, now=FREEZE)
    complete_moneyline(world)
    snapshot = freeze_show_board(session, SLATE, on, now=FREEZE, trigger="TEST").snapshot
    experiment = ShadowExperiment(
        name="TEST", version=1, started_at=FREEZE - timedelta(days=10), start_date=SLATE - timedelta(days=10),
        starting_bankroll=1000, sport="MLB", recommendation_policy_version="P", staking_policy_version="S",
        decision_policy="P", controlling_engine="MLB_V18_WHOLE_GAME", supported_markets=["MONEYLINE"], kelly_fraction=0.25,
        unit_percentage=0.01, max_position_percentage=0.01, max_same_game_percentage=0.02, max_daily_percentage=0.05,
        code_commit="t", code_identity="t", is_active=True)
    session.add(experiment)
    session.flush()
    decided = START - timedelta(minutes=20)
    decision = ShadowExperimentDecision(
        id=uuid4(), experiment_id=experiment.id, game_id=world["game"].id, slate_date=SLATE,
        phase=ExperimentDecisionPhase.FINAL_PREGAME, sport="MLB", market="MONEYLINE", selection="BOS ML",
        american_odds=115, model_probability=0.47, no_vig_probability=0.45, recommended_at=decided,
        recommendation="QUALIFIED_BET", recommendation_reason="test", qualification_reasons=[], source_mode="LIVE")
    session.add(decision)
    session.flush()
    session.add(OfficialAspPosition(market_outcome_id=world["markets"]["MONEYLINE"]["AWAY"].id, placed_at=decided, stake=10,
                                    decimal_odds=2.15, result=PositionResult.PENDING, status=PositionStatus.OPEN,
                                    strategy="test", shadow_experiment_id=experiment.id, shadow_decision_id=decision.id))
    session.flush()

    assert preview_vs_final(session, snapshot.payload, now=decided - timedelta(minutes=1))[0]["final"] is None
    row = preview_vs_final(session, snapshot.payload, now=START)[0]
    assert row["preview"]["selection"] == "NYY" and row["final"]["selection"] == "BOS"
    assert row["changes"]["selection_changed"] is True
    assert row["changes"]["model_probability_change"] is None  # different sides are not differenced
    assert row["final"]["disposition"] == "QUALIFIED_BET" and row["position"]["stake"] == 10.0
    first = record_final_comparisons(session, snapshot, on, now=START)
    second = record_final_comparisons(session, snapshot, on, now=START)
    assert len(first) == 1 and second[0].id == first[0].id


# ----------------------------------------------------------------------- API --

def test_show_board_api_is_read_only_and_fails_closed(client, admin_headers, session) -> None:
    response = client.get("/api/v1/show-board", params={"date": "2026-09-17"})
    assert response.status_code == 200
    body = response.json()
    assert body["provider_calls_made"] == 0
    assert body["flags"] == {"show_preview_enabled": False, "provider_collection_enabled": False, "auto_freeze_enabled": False}
    assert body["board"]["authority"]["grants_wagering_authority"] is False
    assert body["mode"] in {"DRAFT", "RECONSTRUCTED"}
    refused = client.post("/api/v1/show-board/freeze", json={"date": "2026-09-17"}, headers=admin_headers)
    assert refused.status_code == 409
    assert session.scalar(select(func.count()).select_from(ShowPreviewSnapshot)) == 0
    contract = client.get("/api/v1/show-board/contract").json()
    assert contract["forecast"]["required"]["horizon"] == "exactly SHOW_PREVIEW"


def test_tests_are_isolated_from_the_production_database(session) -> None:
    assert session.get_bind().url.get_backend_name() == "sqlite"
