"""Focused NFL M1 prospective research-shadow activation checks.

The real exchange test is read-only and skips when the sibling exchange is not
mounted. Synthetic prices/results below are visibly test-only and never touch a
provider or the official bankroll ledger.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.data_sources.base import OddsEventDTO, OddsScoreEventDTO
from app.forecast_exchange import (
    verify_and_load_sports_bundle,
    verify_nfl_shadow_bundle_index,
)
from app.forecast_exchange.contracts import GameSplit, Uncertainty
from app.forecast_exchange.sports_contracts import NFL_FEATURE_CONTRACT_SHA256
from app.forecast_exchange.sports_contracts import SportsForecastBundleV1
from app.forecast_exchange.pricing import (
    ShadowMarket,
    model_probability,
    price_selection,
)
from app.models import (
    Game,
    GameStateObservation,
    GameStatus,
    OddsSnapshot,
    OfficialAspPosition,
    ShadowExperiment,
    ShadowBoardDecision,
    ShadowDecisionSettlement,
    ShadowForecast,
)
from app.jobs.orchestrator import JobOrchestrator
from app.jobs.orchestrator import _invoke_nfl_shadow_producer, _nfl_closing_evidence
from app.jobs.scheduler import build_scheduler
from app.services.nfl_shadow_odds_service import (
    NFL_ODDS_TEAM_IDENTITIES,
    NFLShadowOddsService,
    _checkpoint_state,
)
from app.services.shadow_board_service import LiveOddsQuoteSource, MappingQuoteSource, ShadowBoardService


EXCHANGE_FAMILY = Path(
    "C:/Users/james/asp-model-exchange/aisp-daily-forecast-bundles"
)


def _real_object():
    if not (EXCHANGE_FAMILY / "nfl-2026-research-shadow-index.json").is_file():
        pytest.skip("external NFL prospective exchange is not mounted")
    locator = verify_nfl_shadow_bundle_index(EXCHANGE_FAMILY)
    for address in locator.index.current_addresses:
        object_dir = locator.sha256_root / address
        verified = verify_and_load_sports_bundle(object_dir)
        if len({row.event_id for row in verified.bundle.forecasts}) >= 2:
            return locator, object_dir
    pytest.skip("external NFL exchange has no two-game slate bundle")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _team_name(canonical_id: str) -> str:
    return next(name for name, value in NFL_ODDS_TEAM_IDENTITIES.items() if value == canonical_id)


def _odds_event(forecast: ShadowForecast, *, source_id: str, commence=None, prices=(-110, -110)):
    kickoff = commence or _aware(forecast.scheduled_start_utc)
    observed = _aware(forecast.available_as_of_utc) + timedelta(minutes=5)
    home = _team_name(forecast.home_team_id)
    away = _team_name(forecast.away_team_id)
    return OddsEventDTO.model_validate({
        "source_event_id": source_id,
        "commence_time": kickoff,
        "home_team": home,
        "away_team": away,
        "bookmakers": [{
            "key": "synthetic_test_book",
            "title": "SYNTHETIC TEST BOOK",
            "last_update": observed,
            "markets": [
                {"key": "h2h", "last_update": observed, "outcomes": [
                    {"name": home, "price": prices[0]},
                    {"name": away, "price": prices[1]},
                ]},
                {"key": "spreads", "last_update": observed, "outcomes": [
                    {"name": home, "price": -105, "point": -3.0},
                    {"name": away, "price": -115, "point": 3.0},
                ]},
                {"key": "totals", "last_update": observed, "outcomes": [
                    {"name": "Over", "price": -108, "point": 44.0},
                    {"name": "Under", "price": -112, "point": 44.0},
                ]},
            ],
        }],
    })


def test_real_producer_locator_and_all_bundles_strictly_verify():
    locator, _ = _real_object()
    verified = [
        verify_and_load_sports_bundle(locator.sha256_root / address)
        for address in (*locator.index.historical_addresses, *locator.index.current_addresses)
    ]
    assert len(verified) == len(locator.index.historical_addresses) + len(locator.index.current_addresses)
    assert sum(len(row.bundle.forecasts) for row in verified) >= 272
    assert min(row.bundle.slate_date for row in verified).year == 2026
    assert max(row.bundle.slate_date for row in verified).year == 2027
    assert all(row.bundle.sport == "NFL" for row in verified)

    # FINAL scientific availability is the completed/publication time, not the
    # producer invocation time. A bundle crossing the 15-minute cutoff fails.
    payload = verified[0].bundle.model_dump(mode="json", by_alias=True)
    payload["forecasts"] = [payload["forecasts"][0]]
    child = payload["forecasts"][0]
    kickoff = datetime.fromisoformat(child["scheduledStartUtc"].replace("Z", "+00:00"))
    child["snapshotType"] = "FINAL_PREGAME"
    child["forecastCutoffUtc"] = (kickoff - timedelta(minutes=20)).isoformat()
    child["availableAsOfUtc"] = (kickoff - timedelta(minutes=10)).isoformat()
    payload["generatedAtUtc"] = (kickoff - timedelta(minutes=9)).isoformat()
    with pytest.raises(ValueError, match="availability/publication"):
        SportsForecastBundleV1.model_validate(payload)


def test_import_is_idempotent_collapses_snapshots_and_never_mutates_official_bankroll(
    session, settings
):
    _, object_dir = _real_object()
    service = ShadowBoardService(session, settings)
    first = service.import_bundle(object_dir)
    second = service.import_bundle(object_dir)
    assert first["idempotent_reuse"] is False
    assert second["idempotent_reuse"] is True
    forecasts = list(session.scalars(select(ShadowForecast)))
    event_count = len({row.event_id for row in forecasts})
    assert len(forecasts) == first["forecasts"]
    as_of = max(_aware(row.available_as_of_utc) for row in forecasts) + timedelta(seconds=1)
    result = service.evaluate_board(
        forecasts[0].event_date,
        sport="NFL",
        quote_source=MappingQuoteSource({}),
        as_of=as_of,
    )
    assert result["forecasts_evaluated"] == event_count
    assert result["decisions"] == event_count * 6
    assert session.scalar(select(func.count(OfficialAspPosition.id))) == 0
    assert session.scalar(select(func.count(ShadowExperiment.id))) == 0


@pytest.mark.asyncio
async def test_locator_family_import_and_scheduler_jobs_are_idempotent(session, settings):
    locator, _ = _real_object()
    configured = settings.model_copy(update={
        "nfl_shadow_enabled": True,
        "nfl_shadow_bundle_import_dir": str(EXCHANGE_FAMILY),
        "nfl_shadow_producer_enabled": True,
    })
    orchestrator = JobOrchestrator(session, configured)
    first = await orchestrator.import_nfl_shadow_bundle(locator.index.updated_at_utc.date())
    second = await orchestrator.import_nfl_shadow_bundle(locator.index.updated_at_utc.date())
    expected_bundles = len(locator.index.historical_addresses) + len(locator.index.current_addresses)
    assert first["selected_bundles"] == expected_bundles
    assert first["records"] >= 272
    assert first["imported_bundles"] == expected_bundles
    assert first["unlisted_objects_opened"] is False
    assert second["reused_bundles"] == expected_bundles
    assert session.scalar(select(func.count(ShadowForecast.id))) == first["forecast_rows_created"]
    assert session.scalar(select(func.count(func.distinct(ShadowForecast.event_id)))) == 272
    assert session.scalar(select(func.count(ShadowForecast.id)).where(
        ShadowForecast.event_date >= datetime(2027, 1, 1, tzinfo=UTC).date()
    )) > 0
    assert session.scalar(select(func.count(OfficialAspPosition.id))) == 0
    assert session.scalar(select(func.count(ShadowExperiment.id))) == 0

    scheduler = build_scheduler(configured)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert {
        "asp-nfl-shadow-import", "asp-nfl-shadow-evaluate",
        "asp-nfl-shadow-final-pregame", "asp-nfl-shadow-odds",
        "asp-nfl-shadow-finalize", "asp-nfl-shadow-settle",
        "asp-nfl-shadow-performance", "asp-nfl-shadow-producer-current",
        "asp-nfl-shadow-producer-final",
    } - {"asp-nfl-shadow-odds"} <= job_ids


@pytest.mark.asyncio
async def test_exact_odds_mapping_point_in_time_filter_and_score_finalization(session, settings):
    _, object_dir = _real_object()
    ShadowBoardService(session, settings).import_bundle(object_dir)
    by_game: dict[object, ShadowForecast] = {}
    for forecast in session.scalars(select(ShadowForecast).order_by(ShadowForecast.event_id)):
        by_game.setdefault(forecast.canonical_game_id, forecast)
    first, second = list(by_game.values())[:2]
    odds = NFLShadowOddsService(session)
    observed = _aware(first.available_as_of_utc) + timedelta(minutes=5)
    event = _odds_event(first, source_id="synthetic-nfl-odds-1")
    result = odds.ingest([event], available_as_of=observed)
    session.commit()
    assert result["quotes"] == 6
    snapshots = list(session.scalars(select(OddsSnapshot)))
    assert {row.raw_payload["snapshot_state"] for row in snapshots} == {"FIRST_OBSERVED"}
    quote = LiveOddsQuoteSource(session).quote_for(
        first, ShadowMarket.FULL_GAME_MONEYLINE.value, "HOME", as_of=observed
    )
    assert quote is not None
    assert quote.source_event_id == "synthetic-nfl-odds-1"

    ShadowBoardService(session, settings).evaluate_board(
        first.event_date, sport="NFL", as_of=observed
    )
    recommendation = session.scalar(select(ShadowBoardDecision).where(
        ShadowBoardDecision.event_id == first.event_id,
        ShadowBoardDecision.market == "FULL_GAME_SPREAD",
        ShadowBoardDecision.selection == "HOME",
        ShadowBoardDecision.has_quote.is_(True),
    ))
    assert recommendation is not None
    recommendation_forecast = session.get(ShadowForecast, recommendation.shadow_forecast_id)
    final_quote = _odds_event(first, source_id="synthetic-nfl-odds-1", prices=(-106, -114))
    final_at = _aware(first.scheduled_start_utc) - timedelta(minutes=20)
    for market in final_quote.bookmakers[0].markets:
        market.last_update = final_at
        if market.key == "spreads":
            market.outcomes[0].point = -2.5
            market.outcomes[1].point = 2.5
    odds.ingest([final_quote], available_as_of=final_at)
    session.commit()
    evidence = _nfl_closing_evidence(
        session, recommendation, recommendation_forecast, cutoff_minutes=15
    )
    assert evidence["recommendation_line"] == -3.0
    assert evidence["final_pregame_line"] == -2.5
    assert evidence["line_movement"] == 0.5
    assert evidence["closing_american"] is None
    assert evidence["closing_price_same_line"] is False

    # A stored post-kickoff observation is preserved but cannot replace the
    # legitimate pregame quote in a point-in-time board decision.
    late = _odds_event(first, source_id="synthetic-nfl-odds-1", prices=(125, -145))
    late.bookmakers[0].markets[0].last_update = _aware(first.scheduled_start_utc) + timedelta(minutes=5)
    odds.ingest([late], available_as_of=_aware(first.scheduled_start_utc) + timedelta(minutes=6))
    session.commit()
    selected = LiveOddsQuoteSource(session).quote_for(
        first,
        ShadowMarket.FULL_GAME_MONEYLINE.value,
        "HOME",
        as_of=_aware(first.scheduled_start_utc) + timedelta(hours=1),
    )
    assert selected is not None
    assert selected.side_american == -106

    mismatch = _odds_event(
        first,
        source_id="synthetic-nfl-bad-kickoff",
        commence=_aware(first.scheduled_start_utc) + timedelta(seconds=1),
    )
    assert odds.ingest([mismatch], available_as_of=observed)["rejected_events"] == 1

    # Settle the six exact first-game market decisions once. Performance must
    # count one game and deterministically retain its HOME moneyline benchmark,
    # regardless of the other market rows sharing the same forecast snapshot.
    first_score = OddsScoreEventDTO.model_validate({
        "source_event_id": "synthetic-nfl-odds-1",
        "commence_time": _aware(first.scheduled_start_utc),
        "completed": True,
        "home_team": _team_name(first.home_team_id),
        "away_team": _team_name(first.away_team_id),
        "scores": [
            {"name": _team_name(first.home_team_id), "score": 27},
            {"name": _team_name(first.away_team_id), "score": 24},
        ],
        "last_update": _aware(first.scheduled_start_utc) + timedelta(hours=4),
    })
    assert odds.ingest_scores(
        [first_score],
        available_as_of=_aware(first.scheduled_start_utc) + timedelta(hours=4, minutes=1),
        event_date=first.event_date,
    )["final_games"] == 1
    session.commit()
    orchestrator = JobOrchestrator(session, settings)
    settled = await orchestrator.settle_nfl_shadow_decisions(first.event_date)
    settled_again = await orchestrator.settle_nfl_shadow_decisions(first.event_date)
    assert settled["settled"] == 6
    assert settled_again["settled"] == 0
    moneyline_home = session.scalar(select(ShadowBoardDecision).where(
        ShadowBoardDecision.event_id == first.event_id,
        ShadowBoardDecision.market == "FULL_GAME_MONEYLINE",
        ShadowBoardDecision.selection == "HOME",
    ))
    moneyline_settlement = session.scalar(select(ShadowDecisionSettlement).where(
        ShadowDecisionSettlement.decision_id == moneyline_home.id
    ))
    assert moneyline_settlement.closing_american == -106
    assert moneyline_settlement.performance_detail["market_home_win_log_loss"] >= 0
    performance = await orchestrator.recalculate_nfl_shadow_performance(first.event_date)
    performance_again = await orchestrator.recalculate_nfl_shadow_performance(first.event_date)
    assert performance["games_evaluated"] == 1
    assert performance["settlements"] == 6
    assert performance["market_home_win_log_loss"] is not None
    assert performance_again == performance
    assert session.scalar(select(func.count(OfficialAspPosition.id))) == 0
    assert session.scalar(select(func.count(ShadowExperiment.id))) == 0

    # A quote-less second game can establish the same exact provider mapping
    # from scores and finalize for scientific evaluation.
    score_event = OddsScoreEventDTO.model_validate({
        "source_event_id": "synthetic-nfl-score-2",
        "commence_time": _aware(second.scheduled_start_utc),
        "completed": True,
        "home_team": _team_name(second.home_team_id),
        "away_team": _team_name(second.away_team_id),
        "scores": [
            {"name": _team_name(second.home_team_id), "score": 24},
            {"name": _team_name(second.away_team_id), "score": 21},
        ],
        "last_update": _aware(second.scheduled_start_utc) + timedelta(hours=4),
    })
    final = odds.ingest_scores(
        [score_event],
        available_as_of=_aware(second.scheduled_start_utc) + timedelta(hours=4, minutes=1),
        event_date=second.event_date,
    )
    session.commit()
    game = session.get(Game, second.canonical_game_id)
    assert final["final_games"] == 1
    assert game.status == GameStatus.FINAL
    assert (game.home_score, game.away_score) == (24, 21)
    assert session.scalar(select(func.count(GameStateObservation.id))) == 2
    assert session.scalar(select(func.count(OfficialAspPosition.id))) == 0
    assert session.scalar(select(func.count(ShadowExperiment.id))) == 0


def test_nfl_ties_and_integer_pushes_are_not_losses():
    split = GameSplit.model_validate({
        "available": True,
        "homeWinProbability": 0.55,
        "awayWinProbability": 0.35,
        "tieProbability": 0.10,
        "expectedHomeScore": 21,
        "expectedAwayScore": 20,
        "expectedTotalScore": 41,
        "jointScoreDistribution": [
            {"homeScore": 21, "awayScore": 20, "probability": 0.55},
            {"homeScore": 20, "awayScore": 21, "probability": 0.35},
            {"homeScore": 20, "awayScore": 20, "probability": 0.10},
        ],
        "scoreMarginDistribution": [
            {"homeMinusAway": 1, "probability": 0.55},
            {"homeMinusAway": -1, "probability": 0.35},
            {"homeMinusAway": 0, "probability": 0.10},
        ],
        "totalScoreDistribution": [
            {"totalScore": 41, "probability": 0.90},
            {"totalScore": 40, "probability": 0.10},
        ],
        "completedSimulations": 100,
        "completionRate": 1,
        "missingReason": None,
    })
    uncertainty = Uncertainty.model_validate({
        "method": "TEST", "confidenceLevel": 0.95,
        "homeWinProbabilityLower": 0.55, "homeWinProbabilityUpper": 0.55,
        "awayWinProbabilityLower": 0.35, "awayWinProbabilityUpper": 0.35,
        "firstFiveHomeWinProbabilityLower": 0.5,
        "firstFiveHomeWinProbabilityUpper": 0.5,
        "firstFiveExpectedTotalScoreStdError": 0.1,
        "expectedTotalScoreStdError": 0.1, "maxBatchProbabilityDelta": 0,
    })
    moneyline = model_probability(
        ShadowMarket.FULL_GAME_MONEYLINE, "HOME", line=None,
        full_game=split, first_five=split, uncertainty=uncertainty,
    )
    assert moneyline.probability == pytest.approx(0.55 / 0.90)
    assert moneyline.raw_loss() == pytest.approx(0.35)
    assert moneyline.push_probability == pytest.approx(0.10)
    priced = price_selection(
        model=moneyline, side_american=100, opposite_american=-120,
        best_american=100, is_ready=True,
    )
    assert priced.expected_value == pytest.approx(0.20)
    total = model_probability(
        ShadowMarket.FULL_GAME_TOTAL, "UNDER", line=40,
        full_game=split, first_five=split, uncertainty=uncertainty,
    )
    assert total.push_probability == pytest.approx(0.10)
    assert total.raw_loss() == pytest.approx(0.90)


def test_market_checkpoint_windows_preserve_final_pregame_cutoff():
    kickoff = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)
    assert _checkpoint_state(
        first_observed=True, available_as_of=kickoff - timedelta(hours=30), kickoff=kickoff
    ) == "FIRST_OBSERVED"
    assert _checkpoint_state(
        first_observed=False, available_as_of=kickoff - timedelta(minutes=20), kickoff=kickoff
    ) == "FINAL_PREGAME"
    assert _checkpoint_state(
        first_observed=False, available_as_of=kickoff - timedelta(minutes=10), kickoff=kickoff
    ) == "CURRENT"


@pytest.mark.asyncio
async def test_model_studio_producer_uses_exact_no_shell_contract(tmp_path, settings, monkeypatch):
    root = tmp_path / "model-studio"
    script = root / "scripts" / "run_nfl_2026_prospective_shadow.py"
    script.parent.mkdir(parents=True)
    script.write_text("# governed test stub\n", encoding="utf-8")
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"test-only")
    family = tmp_path / "exchange" / "aisp-daily-forecast-bundles"
    family.mkdir(parents=True)
    locator = family / "nfl-2026-research-shadow-index.json"
    head = "a" * 40
    payload = {
        "status": "ACTIVE", "games": 272, "forecastsCreated": 0,
        "bundleCount": 0, "featureVectorCount": 272,
        "modelManifestSha256": "ab9c066052b288f3f6162d8f8558e673988fb52172c72fe00aa855aa800a45e9",
        "modelBinarySha256": "7bec06646fd5035380107501d07e139425ac483a47e02e0bbbd56301124fb8fd",
        "featureContractSha256": NFL_FEATURE_CONTRACT_SHA256,
        "modelsFit": 0, "modelsPromoted": 0, "marketFieldsRead": False,
        "reloadFixtureReproduced": True, "producerRevision": head,
        "forecastCutoffUtc": "2026-08-29T12:00:00Z",
        "completedAtUtc": "2026-08-29T12:00:01Z",
        "lateFinalPregameOmissions": 0,
        "exchangeLocatorPath": str(locator),
        "exchangeLocatorCurrentBundleCount": 57,
        "exchangeLocatorCurrentForecastCount": 544,
        "exchangeLocatorCurrentBundleListSha256": "b" * 64,
        "exchangeLocatorHistoricalBundleCount": 57,
        "exchangeLocatorHistoricalBundleListSha256": "c" * 64,
    }
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class FakeProcess:
        def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
            self._stdout = stdout
            self._stderr = stderr
            self.returncode = returncode

        async def communicate(self):
            return self._stdout, self._stderr

        def kill(self):
            self.returncode = -9

    async def fake_create(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "git" and "rev-parse" in args:
            return FakeProcess((head + "\n").encode("ascii"))
        if args[0] == "git":
            return FakeProcess(b"")
        return FakeProcess(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("app.jobs.orchestrator.asyncio.create_subprocess_exec", fake_create)
    configured = settings.model_copy(update={
        "nfl_model_studio_root": str(root),
        "nfl_model_studio_python": str(python),
        "nfl_shadow_bundle_import_dir": str(family),
    })
    result = await _invoke_nfl_shadow_producer(
        configured, ("EARLY", "CURRENT")
    )
    assert result["producerRevision"] == head
    assert calls[0][0] == ("git", "-C", str(root.resolve()), "rev-parse", "HEAD")
    assert calls[1][0] == (
        "git", "-C", str(root.resolve()), "status", "--porcelain=v1", "--untracked-files=no"
    )
    assert calls[2][0] == (
        str(python.resolve()), "-m", "scripts.run_nfl_2026_prospective_shadow",
        "--snapshot-type", "EARLY", "--snapshot-type", "CURRENT",
    )
    assert calls[2][1]["cwd"] == str(root.resolve())
    assert "shell" not in calls[2][1]


def test_public_shadow_read_never_calls_provider(client, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("public read called a provider")

    from app.data_sources.odds import TheOddsAPIAdapter

    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", forbidden)
    monkeypatch.setattr(TheOddsAPIAdapter, "get_scores", forbidden)
    response = client.get("/api/v1/shadow-board?date=2026-09-09&sport=NFL")
    assert response.status_code == 200
