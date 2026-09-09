"""Persisted NFL market board; no provider requests or model execution."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.prediction_markets.read_service import PredictionMarketReadService
from app.services import nfl_market as arithmetic
from app.services.nfl_read_service import NflReadService


def nfl_market_board(session, settings, season: int, week: int) -> dict:
    reader = NflReadService(session, settings)
    board = reader.week_overview(season, week)
    now = datetime.fromisoformat(board["now_utc"])
    forecasts = {row.forecast_id: row for row in reader._forecast_rows(season=season, week=week)}
    game_ids = {UUID(event["canonical_game_id"]) for event in board["events"] if event["canonical_game_id"]}
    games = reader._games(game_ids)
    crowd = PredictionMarketReadService(session, settings)
    context = crowd._coverage_context(list(game_ids))
    for event in board["events"]:
        game_id = UUID(event["canonical_game_id"]) if event["canonical_game_id"] else None
        game = games.get(game_id)
        pulse = crowd._row(game, context) if game is not None else None
        # Carry observations/semantics, not the MLB/Gen1 forecast fields on that
        # legacy row. Each NFL model stays bound to its own frozen forecast.
        event["prediction_market"] = {
            "providers": [p.model_dump(mode="json") for p in pulse.providers] if pulse else [],
            "provider_coverage": [p.model_dump(mode="json") for p in pulse.provider_coverage] if pulse else [],
            "coverage_state": pulse.coverage_state if pulse else "NO_MAPPED_MARKET",
            "display_note": pulse.display_note if pulse else "No canonical prediction market mapping",
            "comparison_note": "Home-win observations only. Unreviewed settlement semantics are research references, not model edges.",
        }
        for engine in event["engines"]:
            forecast = forecasts[engine["forecast_id"]]
            joint = (forecast.distributions or {}).get("joint_score_distribution")
            cells = arithmetic.cells_from_distribution(joint) if joint else None
            outcomes = arithmetic.outcome_probabilities(cells) if cells else None
            engine["market_comparison"] = reader._market_comparison(game_id, cells, outcomes, now)
    board["schema_version"] = "AISP_NFL_MARKETS_READ_V1"
    board["read_note"] = "Stored evidence only. Loading this board does not refresh source evidence or request prices."
    return board
