"""Import a preview forecast that satisfies AISP_SHOW_PREVIEW_FORECAST_V1.

Idempotent by ``forecast_id``: the same content returns the stored row; a
different document under the same id is refused. Requires the Show Preview
switch. AISP never produces the document -- a producer publishes it.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Game, GameTeam, Team
from app.models.show_preview import ShowPreviewForecast
from app.show_preview.contract import KICKOFF_TOLERANCE, SHOW_PREVIEW, ContractViolation, validate_forecast
from app.show_preview.freeze import ShowPreviewDisabled


def import_preview_forecast(session: Session, document: dict[str, Any], settings: Settings, *,
                            now: datetime | None = None) -> tuple[ShowPreviewForecast, bool]:
    if not settings.show_preview_enabled:
        raise ShowPreviewDisabled("SHOW_PREVIEW_ENABLED is false; no preview forecast was imported")
    moment = now or datetime.now(UTC)
    forecast = validate_forecast(document, now=moment)
    existing = session.scalar(select(ShowPreviewForecast).where(ShowPreviewForecast.forecast_id == document["forecast_id"]))
    if existing is not None:
        if existing.content_sha256 != forecast.content_sha256:
            raise ContractViolation("forecast_id already stored with different content")
        return existing, False

    game_id = None
    if forecast.canonical_game_id:
        game = session.get(Game, uuid.UUID(str(forecast.canonical_game_id)))
        if game is None:
            raise ContractViolation("canonical_game_id is not an AISP game")
        starts = game.starts_at if game.starts_at.tzinfo else game.starts_at.replace(tzinfo=UTC)
        if abs(starts - forecast.kickoff_utc) > KICKOFF_TOLERANCE:
            raise ContractViolation("kickoff disagrees with AISP's schedule for canonical_game_id")
        sides = {is_home: abbreviation.upper() for is_home, abbreviation in session.execute(
            select(GameTeam.is_home, Team.abbreviation).join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id == game.id)).all()}
        if sides.get(True) != forecast.home or sides.get(False) != forecast.away:
            raise ContractViolation("teams disagree with AISP's schedule for canonical_game_id")
        game_id = game.id

    doc = forecast.document
    row = ShowPreviewForecast(
        forecast_id=doc["forecast_id"], content_sha256=forecast.content_sha256, content_bytes=forecast.content_bytes,
        schema_version=doc["schema_version"], horizon=SHOW_PREVIEW, sport=forecast.sport, league=forecast.league,
        season=forecast.season, slate_date=forecast.slate_date, canonical_game_id=game_id,
        source_game_ref=forecast.source_game_ref, away_abbreviation=forecast.away, home_abbreviation=forecast.home,
        kickoff_utc=forecast.kickoff_utc, created_utc=forecast.created_utc, source_cutoff_utc=forecast.source_cutoff_utc,
        model_id=doc["model"]["model_id"], model_version=doc["model"]["model_version"],
        artifact_sha256=doc["model"]["artifact_sha256"],
        expected_away_score=float(doc["expected_score"]["away"]), expected_home_score=float(doc["expected_score"]["home"]),
        away_win_probability=float(doc["probabilities"]["away_win"]), home_win_probability=float(doc["probabilities"]["home_win"]),
        tie_probability=float(doc["probabilities"].get("tie", 0.0)),
        lineup_status=doc["availability"]["lineups"], starter_status=doc["availability"]["starters"],
        grants_wagering_authority=False, payload=doc, imported_utc=moment,
    )
    session.add(row)
    session.flush()
    return row, True
