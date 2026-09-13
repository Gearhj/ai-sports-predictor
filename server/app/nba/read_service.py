"""What the NBA data path currently holds, for the admin surface.

Read-only and provider-free. Everything is broken out by ``data_mode`` so a
reader cannot mistake locally authored fixtures for ingested provider data, and
the entitlement assessment travels with the counts so "what we have" is never
shown without "what we are permitted to get".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.data_sources.nba_sportsdataio import ENDPOINTS, SEASON_TYPES
from app.models import IngestionRun
from app.models.nba import (
    NbaGame, NbaPlayer, NbaPlayerGameStat, NbaRosterMembership,
    NbaSourcePayload, NbaTeam, NbaTeamGameStat,
)
from app.nba.entitlement import (
    PILOT_MAX_GAMES, PILOT_MAX_ORIGINAL_BYTES, assess,
)

SCHEMA_VERSION = "AISP_NBA_DATA_STATUS_V1"

#: Stated on the surface itself, because both sentences are easy to overstate.
HONESTY_NOTES = (
    "Historical ingestion is not a live NBA feed. Nothing here polls, and no "
    "recurring acquisition is enabled.",
    "Box-score coverage is not possession-model readiness. Possession "
    "segmentation and five-player stint reconstruction are not implemented.",
)


class NbaDataStatusService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _count(self, model: Any, mode: str | None = None, **where: Any) -> int:
        stmt = select(func.count()).select_from(model)
        if mode is not None:
            stmt = stmt.where(model.data_mode == mode)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return int(self.session.scalar(stmt) or 0)

    def _modes(self) -> list[str]:
        rows = self.session.scalars(
            select(NbaSourcePayload.data_mode).distinct()).all()
        games = self.session.scalars(select(NbaGame.data_mode).distinct()).all()
        return sorted({*rows, *games})

    def status(self) -> dict[str, Any]:
        runs = self._count(IngestionRun) and self.session.scalar(
            select(func.count()).select_from(IngestionRun).where(
                IngestionRun.source == "SPORTSDATAIO")) or 0
        entitlement = assess(self.settings, sportsdataio_runs=int(runs))

        coverage: dict[str, Any] = {}
        for mode in self._modes():
            seasons = self.session.execute(
                select(NbaGame.season, NbaGame.season_type_label,
                       func.count(), func.min(NbaGame.game_day),
                       func.max(NbaGame.game_day))
                .where(NbaGame.data_mode == mode, NbaGame.is_current.is_(True))
                .group_by(NbaGame.season, NbaGame.season_type_label)
            ).all()
            coverage[mode] = {
                "payloads": self._count(NbaSourcePayload, mode),
                "originalBytesAdmitted": int(self.session.scalar(
                    select(func.coalesce(func.sum(NbaSourcePayload.byte_count), 0))
                    .where(NbaSourcePayload.data_mode == mode)) or 0),
                "teams": self._count(NbaTeam, mode),
                "players": self._count(NbaPlayer, mode),
                "rosterObservations": self._count(NbaRosterMembership, mode),
                "gameRowsAllRevisions": self._count(NbaGame, mode),
                "currentGames": self._count(NbaGame, mode, is_current=True),
                "finalGames": self.session.scalar(
                    select(func.count()).select_from(NbaGame).where(
                        NbaGame.data_mode == mode, NbaGame.is_current.is_(True),
                        NbaGame.is_final.is_(True))) or 0,
                "revisionsBeyondFirst": self.session.scalar(
                    select(func.count()).select_from(NbaGame).where(
                        NbaGame.data_mode == mode, NbaGame.revision > 1)) or 0,
                "teamGameRows": self._count(NbaTeamGameStat, mode),
                "playerGameRows": self._count(NbaPlayerGameStat, mode),
                "seasons": [
                    {"season": s, "seasonType": label, "currentGames": n,
                     "firstDay": str(lo) if lo else None,
                     "lastDay": str(hi) if hi else None}
                    for s, label, n, lo, hi in seasons
                ],
            }

        last_run = self.session.scalars(
            select(IngestionRun)
            .where(IngestionRun.source.like("SPORTSDATAIO_NBA%"))
            .order_by(IngestionRun.started_at.desc()).limit(1)
        ).first()

        real = coverage.get("REAL", {})
        return {
            "schemaVersion": SCHEMA_VERSION,
            "source": {
                "identity": "SPORTSDATAIO_NBA",
                "baseUrl": "https://api.sportsdata.io/v3/nba",
                "authentication": "Ocp-Apim-Subscription-Key header (never the "
                                  "query string); no credential is exposed here",
                "endpointsImplemented": sorted(ENDPOINTS),
                "seasonTypes": SEASON_TYPES,
            },
            "dataMode": {
                "effective": str(entitlement.data_mode),
                "storedModes": self._modes(),
                "note": "FIXTURE rows are locally authored test data. They are "
                        "isolated in nba_* tables, carry data_mode on every row, "
                        "and no MLB/NFL or customer-facing surface reads them.",
            },
            "entitlement": entitlement.as_dict(),
            "pilotBudget": {
                "maxCompletedGames": PILOT_MAX_GAMES,
                "maxOriginalResponseBytes": PILOT_MAX_ORIGINAL_BYTES,
                "realGamesConsumed": int(real.get("finalGames", 0)),
                "realBytesConsumed": int(real.get("originalBytesAdmitted", 0)),
                "note": "Counts REAL data only. Fixture bytes do not consume the "
                        "provider ceiling.",
            },
            "lastIngest": {
                "runId": str(last_run.id) if last_run else None,
                "jobName": last_run.job_name if last_run else None,
                "status": last_run.status if last_run else None,
                "startedAtUtc": last_run.started_at.isoformat() if last_run else None,
                "completedAtUtc": (last_run.completed_at.isoformat()
                                   if last_run and last_run.completed_at else None),
                "recordsProcessed": last_run.records_processed if last_run else None,
                "providerRequests": (last_run.source_metadata or {}).get(
                    "providerRequests") if last_run else None,
            },
            "coverage": coverage,
            "recurringAcquisition": {
                "enabled": False,
                "note": "No recurring NBA job is registered. The pilot is manual "
                        "only and MLB/NFL collection is untouched.",
            },
            "notImplemented": [
                "possession segmentation",
                "five-player stint reconstruction",
                "play-by-play ingestion (optional for this milestone)",
                "live or delayed polling of any kind",
            ],
            "honesty": list(HONESTY_NOTES),
        }

    def games(self, *, limit: int = 25) -> dict[str, Any]:
        home = NbaTeam.__table__.alias("home")
        away = NbaTeam.__table__.alias("away")
        rows = self.session.execute(
            select(NbaGame, home.c.team_key, away.c.team_key)
            .join(home, home.c.id == NbaGame.home_nba_team_id)
            .join(away, away.c.id == NbaGame.away_nba_team_id)
            .where(NbaGame.is_current.is_(True))
            .order_by(NbaGame.game_day.desc().nullslast(), NbaGame.provider_game_id)
            .limit(max(1, min(int(limit), 200)))
        ).all()
        out = []
        for game, home_key, away_key in rows:
            out.append({
                "providerGameId": game.provider_game_id,
                "dataMode": game.data_mode,
                "season": game.season,
                "seasonType": game.season_type_label,
                "gameDay": str(game.game_day) if game.game_day else None,
                "scheduledLocalNoOffset": (game.scheduled_local.isoformat()
                                           if game.scheduled_local else None),
                "home": home_key, "away": away_key,
                "status": game.status,
                "isFinal": game.is_final,
                "homeScore": game.home_score, "awayScore": game.away_score,
                "periods": len((game.period_scores or {}).get("quarters") or []),
                "revision": game.revision,
                "supersedesGameId": (str(game.supersedes_game_id)
                                     if game.supersedes_game_id else None),
                "teamGameRows": self.session.scalar(
                    select(func.count()).select_from(NbaTeamGameStat).where(
                        NbaTeamGameStat.nba_game_id == game.id)) or 0,
                "playerGameRows": self.session.scalar(
                    select(func.count()).select_from(NbaPlayerGameStat).where(
                        NbaPlayerGameStat.nba_game_id == game.id)) or 0,
                "retrievedAtUtc": game.retrieved_at.isoformat(),
                "providerUpdatedAt": (game.provider_updated_at.isoformat()
                                      if game.provider_updated_at else None),
            })
        return {
            "schemaVersion": SCHEMA_VERSION,
            "games": out,
            "note": "Retrieval time is when AISP received the bytes. It is not the "
                    "event time and not the provider's update time; all three are "
                    "stored separately.",
            "honesty": list(HONESTY_NOTES),
        }


__all__ = ["NbaDataStatusService", "SCHEMA_VERSION"]
