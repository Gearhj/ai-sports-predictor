"""Persisting the NBA point/Elo forecast against a reconciled game identity.

WHERE THE GAME IDENTITY COMES FROM
----------------------------------
Not from the blocked SportsDataIO pilot, and not from the fixture rows. AISP's
``nba_*`` tables hold three invented clubs and four invented games under a
provider id space that has never carried a real game; joining a real ESPN game
to those would be worse than having nothing.

The package carries its own schedule evidence, classified
``REAL_UNSCRAMBLED_GAMES`` with rights evidence attached, and names the game by
ESPN id and each club by franchise id. Those are the identities reconciled here,
recorded as external id mappings so the join is by id and never by "Boston, on
the twentieth" -- which works until two clubs meet twice in a week.

The fixture tables are not touched. They remain what they are, separately.

WHAT PERSISTING DOES NOT DO
---------------------------
It creates no account, no wager, no staking policy, no decision and no
scheduler entry. The forecast's own authority block declares research only, the
importer refuses a package that claims otherwise, and there is no code path from
here to a position.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.nba.point_elo_import import (
    ENGINE_ID,
    VerifiedPackage,
    clocks,
    display_values,
    source_identity,
)

#: The identity space the package speaks. Kept distinct from the SportsDataIO
#: provider ids the fixture rows use, so the two can never be confused.
SOURCE_ESPN = "NBA_ESPN"
SOURCE_FRANCHISE = "NBA_NEIL_FRANCHISE"

#: How this forecast reached AISP. Not a provider feed.
SOURCE_KEY = "NBA_GEN1_POINT_ELO_EXCHANGE"


class PointEloStoreError(RuntimeError):
    """Persistence refused, with the reason named."""


@dataclass(frozen=True)
class ImportResult:
    forecast_id: str
    forecast_sha256: str
    canonical_game_id: uuid.UUID
    created: bool
    reason: str


def _aware(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def reconcile_game(session: Session, package: VerifiedPackage) -> uuid.UUID:
    """Resolve -- or register -- the AISP game this forecast is about.

    Registration is by the package's own source ids. A second import of the same
    package finds the same game rather than creating another.
    """

    from app.models import ExternalIdMapping, Game, League, Season, Sport, Team, GameTeam

    identity = source_identity(package.forecast)
    espn_id = identity["espn_game_id"]
    if not espn_id:
        raise PointEloStoreError("the forecast carries no ESPN game id to reconcile")

    existing = session.scalar(
        select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == "game",
            ExternalIdMapping.source == SOURCE_ESPN,
            ExternalIdMapping.external_id == espn_id,
        )
    )
    if existing is not None:
        return existing.entity_id

    sport = session.scalar(select(Sport).where(Sport.code == "BASKETBALL"))
    if sport is None:
        sport = Sport(name="Basketball", code="BASKETBALL")
        session.add(sport)
        session.flush()
    league = session.scalar(select(League).where(League.code == "NBA"))
    if league is None:
        league = League(sport_id=sport.id, code="NBA", name="NBA")
        session.add(league)
        session.flush()

    season_end = int(identity["season_end"] or 0)
    season_name = f"{season_end - 1}-{str(season_end)[-2:]}"
    season = session.scalar(
        select(Season).where(Season.league_id == league.id, Season.name == season_name))
    if season is None:
        season = Season(
            league_id=league.id, name=season_name,
            starts_at=datetime(season_end - 1, 10, 1, tzinfo=UTC),
            ends_at=datetime(season_end, 6, 30, tzinfo=UTC),
        )
        session.add(season)
        session.flush()

    def club(code: str, franchise: str) -> Any:
        mapping = session.scalar(
            select(ExternalIdMapping).where(
                ExternalIdMapping.entity_type == "team",
                ExternalIdMapping.source == SOURCE_FRANCHISE,
                ExternalIdMapping.external_id == franchise,
            )
        )
        if mapping is not None:
            return session.get(Team, mapping.entity_id)
        team = session.scalar(
            select(Team).where(Team.league_id == league.id, Team.abbreviation == code))
        if team is None:
            team = Team(league_id=league.id, name=code, abbreviation=code)
            session.add(team)
            session.flush()
        session.add(ExternalIdMapping(
            entity_type="team", entity_id=team.id,
            source=SOURCE_FRANCHISE, external_id=franchise))
        return team

    home = club(identity["home_code"], identity["home_franchise"])
    away = club(identity["away_code"], identity["away_franchise"])

    tipoff = _aware((package.forecast.get("game") or {}).get("scheduled_tipoff_utc"))
    if tipoff is None:
        raise PointEloStoreError("the forecast carries no scheduled tipoff to register")

    game = Game(
        league_id=league.id, season_id=season.id, starts_at=tipoff,
        status="SCHEDULED", source_mode="LIVE",
    )
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
        ExternalIdMapping(
            entity_type="game", entity_id=game.id,
            source=SOURCE_ESPN, external_id=espn_id),
    ])
    session.flush()
    return game.id


def import_forecast(
    session: Session, package: VerifiedPackage, *, now: datetime | None = None,
) -> ImportResult:
    """Store the forecast once, by its own identity. Re-import is a no-op.

    Conflicting bytes under an existing id are rejected rather than merged: two
    different forecasts sharing an identity is the one failure that cannot be
    untangled afterwards.
    """

    from app.models import NbaResearchForecast

    moment = now or datetime.now(UTC)
    forecast = package.forecast
    forecast_id = str(forecast["forecastId"])

    existing = session.scalar(
        select(NbaResearchForecast).where(
            NbaResearchForecast.forecast_id == forecast_id))
    if existing is not None:
        if existing.forecast_sha256 != package.forecast_sha256:
            raise PointEloStoreError(
                f"forecast {forecast_id} is already stored with digest "
                f"{existing.forecast_sha256}; the delivered bytes hash to "
                f"{package.forecast_sha256}. Conflicting bytes for an existing "
                "id are rejected, never merged."
            )
        return ImportResult(
            forecast_id=forecast_id, forecast_sha256=package.forecast_sha256,
            canonical_game_id=existing.canonical_game_id, created=False,
            reason="ALREADY_IMPORTED_IDENTICAL_BYTES",
        )

    game_id = reconcile_game(session, package)
    values = display_values(forecast)
    times = clocks(package)
    identity = source_identity(forecast)
    points = forecast.get("pointExpectations") or {}
    winner = forecast.get("winnerProbabilities") or {}
    distributions = forecast.get("distributions") or {}
    game = forecast.get("game") or {}

    row = NbaResearchForecast(
        forecast_id=forecast_id,
        forecast_sha256=package.forecast_sha256,
        forecast_bytes=package.forecast_bytes,
        forecast_schema=str(forecast.get("schema")),
        package_index_sha256=package.index_sha256,
        package_index_bytes=package.index_bytes,
        envelope_schema=str(package.index.get("schemaVersion")),
        engine_id=ENGINE_ID,
        configuration_identity=forecast.get("configurationIdentity"),
        point_configuration_identity=forecast.get("pointConfigurationIdentity"),
        canonical_game_id=game_id,
        source_game_id=identity["espn_game_id"],
        home_franchise_id=identity["home_franchise"],
        away_franchise_id=identity["away_franchise"],
        schedule_source_revision=identity["source_revision"],
        captured_tipoff_utc=_aware(game.get("scheduled_tipoff_utc")),
        captured_game_date=datetime.fromisoformat(
            str(game.get("game_date"))).date(),
        # Ridge, under its own name.
        ridge_expected_points_home=points.get("home"),
        ridge_expected_points_away=points.get("away"),
        ridge_expected_total=points.get("total"),
        ridge_home_margin=points.get("homeMargin"),
        # Elo, a separate model, stored as delivered fractions.
        elo_win_probability_home=winner.get("home"),
        elo_win_probability_away=winner.get("away"),
        elo_tie_probability=winner.get("tie"),
        elo_method=winner.get("method"),
        # Zero, and stays zero.
        draw_count=int(distributions.get("drawCount") or 0),
        distribution_status=str(distributions.get("status") or "UNAVAILABLE"),
        source_cutoff_utc=_aware(times["source_cutoff_utc"]),
        forecast_created_utc=_aware(times["forecast_created_utc"]),
        local_export_utc=_aware(times["local_export_utc"]),
        # Null in the artifact, null here.
        producer_published_utc=_aware(times["producer_published_utc"]),
        exchange_delivered_utc=_aware(times["exchange_delivered_utc"]),
        imported_at=moment,
        supersedes_forecast_id=package.index.get("supersedesForecastId"),
        detail={
            "display": values,
            "clocks": times,
            "source_identity": identity,
            "restSupportAudit": forecast.get("restSupportAudit"),
            "limitations": forecast.get("limitations"),
            "bindings": forecast.get("bindings"),
            "featureAudit": forecast.get("featureAudit"),
            "authority": forecast.get("authority"),
            "files_verified": package.files_verified,
            "source_key": SOURCE_KEY,
        },
    )
    session.add(row)
    session.flush()
    return ImportResult(
        forecast_id=forecast_id, forecast_sha256=package.forecast_sha256,
        canonical_game_id=game_id, created=True, reason="IMPORTED",
    )


__all__ = [
    "SOURCE_ESPN",
    "SOURCE_FRANCHISE",
    "SOURCE_KEY",
    "ImportResult",
    "PointEloStoreError",
    "import_forecast",
    "reconcile_game",
]
