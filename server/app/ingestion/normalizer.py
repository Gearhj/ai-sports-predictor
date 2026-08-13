from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_sources.base import OddsEventDTO, ScheduleGameDTO, TeamStatsDTO, WeatherDTO
from app.markets.odds import american_implied_probability, american_to_decimal, game_match, team_name_match
from app.models import (
    ExternalIdMapping,
    FeatureVersion,
    Game,
    GameStatus,
    GameTeam,
    League,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    Season,
    Sport,
    Sportsbook,
    Team,
    TeamFeature,
    Venue,
    WeatherSnapshot,
    Player,
    Pitcher,
)


TEAM_ABBREVIATIONS = {
    "Arizona Diamondbacks": "ARI", "Athletics": "ATH", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS", "Chicago Cubs": "CHC",
    "Chicago White Sox": "CWS", "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET", "Houston Astros": "HOU",
    "Kansas City Royals": "KC", "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL", "Minnesota Twins": "MIN",
    "New York Mets": "NYM", "New York Yankees": "NYY", "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL", "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
}

VENUE_COORDINATES = {
    "Yankee Stadium": (40.8296, -73.9262), "Fenway Park": (42.3467, -71.0972),
    "Dodger Stadium": (34.0739, -118.2400), "Oracle Park": (37.7786, -122.3893),
    "Rogers Centre": (43.6414, -79.3894), "Wrigley Field": (41.9484, -87.6553),
    "Citi Field": (40.7571, -73.8458), "Citizens Bank Park": (39.9061, -75.1665),
    "Petco Park": (32.7076, -117.1570), "T-Mobile Park": (47.5914, -122.3325),
    "Truist Park": (33.8908, -84.4677), "Camden Yards": (39.2840, -76.6216),
    "Target Field": (44.9817, -93.2776), "Progressive Field": (41.4962, -81.6852),
    "Comerica Park": (42.3390, -83.0485), "PNC Park": (40.4469, -80.0057),
    "Busch Stadium": (38.6226, -90.1928), "Coors Field": (39.7559, -104.9942),
    "Minute Maid Park": (29.7573, -95.3555), "Globe Life Field": (32.7473, -97.0847),
    "Angel Stadium": (33.8003, -117.8827), "Chase Field": (33.4453, -112.0667),
    "Nationals Park": (38.8730, -77.0074), "Rate Field": (41.8300, -87.6338),
    "Guaranteed Rate Field": (41.8300, -87.6338), "Sutter Health Park": (38.5802, -121.5133),
    "loanDepot park": (25.7781, -80.2197), "UNIQLO Field at Dodger Stadium": (34.0739, -118.2400),
    "Daikin Park": (29.7573, -95.3555), "American Family Field": (43.0280, -87.9712),
    "Great American Ball Park": (39.0979, -84.5082), "Kauffman Stadium": (39.0517, -94.4803),
    "George M. Steinbrenner Field": (27.9802, -82.5067), "loanDepot Park": (25.7781, -80.2197),
    "Cleveland Guardians Ballpark": (41.4962, -81.6852),
}

VENUE_TIMEZONES = {
    "Yankee Stadium": "America/New_York", "Fenway Park": "America/New_York",
    "Dodger Stadium": "America/Los_Angeles", "Oracle Park": "America/Los_Angeles",
    "Rogers Centre": "America/Toronto", "Tropicana Field": "America/New_York",
    "Wrigley Field": "America/Chicago", "Citi Field": "America/New_York",
    "Citizens Bank Park": "America/New_York", "Petco Park": "America/Los_Angeles",
    "T-Mobile Park": "America/Los_Angeles", "Truist Park": "America/New_York",
    "Camden Yards": "America/New_York", "Target Field": "America/Chicago",
    "Progressive Field": "America/New_York", "Comerica Park": "America/Detroit",
    "PNC Park": "America/New_York", "Busch Stadium": "America/Chicago",
    "Coors Field": "America/Denver", "Minute Maid Park": "America/Chicago",
    "Globe Life Field": "America/Chicago",
    "Angel Stadium": "America/Los_Angeles", "Chase Field": "America/Phoenix",
    "Nationals Park": "America/New_York", "Rate Field": "America/Chicago",
    "Guaranteed Rate Field": "America/Chicago", "Sutter Health Park": "America/Los_Angeles",
    "loanDepot park": "America/New_York", "loanDepot Park": "America/New_York",
    "UNIQLO Field at Dodger Stadium": "America/Los_Angeles", "Daikin Park": "America/Chicago",
    "American Family Field": "America/Chicago", "Great American Ball Park": "America/New_York",
    "Kauffman Stadium": "America/Chicago", "George M. Steinbrenner Field": "America/New_York",
}


class CanonicalNormalizer:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_core(self, year: int) -> tuple[Sport, League, Season, FeatureVersion]:
        sport = self.session.scalar(select(Sport).where(Sport.code == "BASEBALL"))
        if not sport:
            sport = Sport(code="BASEBALL", name="Baseball", active=True)
            self.session.add(sport)
            self.session.flush()
        league = self.session.scalar(select(League).where(League.code == "MLB"))
        if not league:
            league = League(sport_id=sport.id, code="MLB", name="Major League Baseball", country_code="US")
            self.session.add(league)
            self.session.flush()
        season = self.session.scalar(select(Season).where(Season.league_id == league.id, Season.name == str(year)))
        if not season:
            season = Season(
                league_id=league.id,
                name=str(year),
                starts_at=datetime(year, 3, 1, tzinfo=UTC),
                ends_at=datetime(year, 11, 15, tzinfo=UTC),
            )
            self.session.add(season)
        feature_version = self.session.scalar(
            select(FeatureVersion).where(FeatureVersion.name == "mlb_pregame", FeatureVersion.version == "1.0.0")
        )
        if not feature_version:
            feature_version = FeatureVersion(
                name="mlb_pregame",
                version="1.0.0",
                schema_hash=sha256(b"asp-mlb-feature-schema-v1").hexdigest(),
                definition={"available_as_of_required": True, "windows_days": [7, 14, 30]},
            )
            self.session.add(feature_version)
        self.session.flush()
        return sport, league, season, feature_version

    def ingest_schedule(self, games: list[ScheduleGameDTO], source: str) -> list[Game]:
        if not games:
            return []
        _, league, season, _ = self.ensure_core(games[0].event_date.year)
        persisted: list[Game] = []
        for dto in games:
            venue = self._venue(dto.venue.name, dto.venue.latitude, dto.venue.longitude, dto.venue.source_id, source)
            away = self._team(league.id, dto.away_team.source_id, dto.away_team.name, dto.away_team.abbreviation, source)
            home = self._team(league.id, dto.home_team.source_id, dto.home_team.name, dto.home_team.abbreviation, source)
            existing_mapping = self.session.scalar(
                select(ExternalIdMapping).where(
                    ExternalIdMapping.entity_type == "game",
                    ExternalIdMapping.source == source,
                    ExternalIdMapping.external_id == dto.source_game_id,
                )
            )
            if existing_mapping:
                game = self.session.get(Game, existing_mapping.entity_id)
                if game:
                    # Schedule metadata is mutable before a game is final.
                    if game.status != GameStatus.FINAL:
                        game.starts_at = dto.scheduled_at
                        game.status = _game_status(dto.status)
                        game.venue_id = venue.id
                        game.home_score = dto.home_score
                        game.away_score = dto.away_score
                        game.final_at = dto.final_at
                        if dto.source_mode.upper() == "LIVE":
                            game.source_mode = "LIVE"
                        self._upsert_game_side(game.id, away, dto.away_team, dto.away_pitcher, False, source)
                        self._upsert_game_side(game.id, home, dto.home_team, dto.home_pitcher, True, source)
                    persisted.append(game)
                    continue
            canonical_game = self._find_existing_game(league.id, dto.scheduled_at, home.id, away.id)
            if canonical_game:
                self.session.add(ExternalIdMapping(entity_type="game", entity_id=canonical_game.id, source=source, external_id=dto.source_game_id))
                if canonical_game.status != GameStatus.FINAL:
                    canonical_game.starts_at = dto.scheduled_at
                    canonical_game.status = _game_status(dto.status)
                    canonical_game.venue_id = venue.id
                    canonical_game.home_score = dto.home_score
                    canonical_game.away_score = dto.away_score
                    canonical_game.final_at = dto.final_at
                    canonical_game.source_mode = "LIVE" if dto.source_mode.upper() == "LIVE" else canonical_game.source_mode
                    self._upsert_game_side(canonical_game.id, away, dto.away_team, dto.away_pitcher, False, source)
                    self._upsert_game_side(canonical_game.id, home, dto.home_team, dto.home_pitcher, True, source)
                persisted.append(canonical_game)
                continue
            game = Game(
                league_id=league.id,
                season_id=season.id,
                venue_id=venue.id,
                starts_at=dto.scheduled_at,
                status=_game_status(dto.status),
                scheduled_innings=9,
                home_score=dto.home_score,
                away_score=dto.away_score,
                final_at=dto.final_at,
                source_mode=dto.source_mode.upper(),
            )
            self.session.add(game)
            self.session.flush()
            self.session.add(ExternalIdMapping(entity_type="game", entity_id=game.id, source=source, external_id=dto.source_game_id))
            self.session.add_all([
                GameTeam(
                    game_id=game.id,
                    team_id=away.id,
                    is_home=False,
                    probable_pitcher_id=self._player(dto.away_pitcher, away.id, source).id if dto.away_pitcher else None,
                    pitcher_confirmed=dto.away_pitcher.confirmed if dto.away_pitcher else False,
                    pitcher_status_observed_at=datetime.now(UTC) if dto.away_pitcher else None,
                    record_wins=dto.away_team.record_wins,
                    record_losses=dto.away_team.record_losses,
                ),
                GameTeam(
                    game_id=game.id,
                    team_id=home.id,
                    is_home=True,
                    probable_pitcher_id=self._player(dto.home_pitcher, home.id, source).id if dto.home_pitcher else None,
                    pitcher_confirmed=dto.home_pitcher.confirmed if dto.home_pitcher else False,
                    pitcher_status_observed_at=datetime.now(UTC) if dto.home_pitcher else None,
                    record_wins=dto.home_team.record_wins,
                    record_losses=dto.home_team.record_losses,
                ),
            ])
            persisted.append(game)
        self.session.flush()
        return persisted

    def ingest_team_stats(self, dto: TeamStatsDTO, source: str) -> TeamFeature | None:
        team = self._mapped_entity("team", source, dto.source_team_id, Team)
        if not team:
            return None
        _, _, _, feature_version = self.ensure_core(dto.available_as_of.year)
        # Game-neutral team snapshots use the next known game for canonical game_id.
        game_id = self.session.scalar(
            select(GameTeam.game_id).join(Game, Game.id == GameTeam.game_id).where(GameTeam.team_id == team.id).order_by(Game.starts_at.desc()).limit(1)
        )
        if game_id is None:
            return None
        values = dto.model_dump(mode="json")
        feature = TeamFeature(
            game_id=game_id,
            team_id=team.id,
            feature_version_id=feature_version.id,
            observed_at=dto.available_as_of,
            available_as_of=dto.available_as_of,
            values=values,
            source=source,
            source_mode="DEMO" if source == "ASP_DEMO_FIXTURE" else "LIVE",
        )
        self.session.add(feature)
        self.session.flush()
        return feature

    def ingest_weather(self, game_id: Any, dto: WeatherDTO, source: str) -> WeatherSnapshot:
        payload = dto.model_dump(mode="json")
        snapshot = WeatherSnapshot(
            game_id=game_id,
            source=source,
            observed_at=dto.forecast_for,
            available_as_of=dto.available_as_of,
            temperature_f=dto.temperature_f,
            wind_mph=dto.wind_speed_mph,
            wind_direction=str(dto.wind_direction_degrees) if dto.wind_direction_degrees is not None else None,
            precipitation_probability=dto.precipitation_probability,
            raw_payload=payload,
            payload_checksum=sha256(str(sorted(payload.items())).encode()).hexdigest(),
            source_mode=dto.source_mode.upper(),
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def ingest_odds(self, events: list[OddsEventDTO], source: str, available_as_of: datetime) -> int:
        count = 0
        games = list(self.session.scalars(select(Game).order_by(Game.starts_at)))
        for event in events:
            game = self._match_game(games, event.home_team, event.away_team, event.commence_time)
            if game is None:
                continue
            game_teams = list(self.session.execute(
                select(GameTeam, Team).join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id == game.id)
            ))
            team_by_name = {team.name: team for _, team in game_teams}
            for book in event.bookmakers:
                sportsbook = self.session.scalar(select(Sportsbook).where(Sportsbook.key == book.key))
                if not sportsbook:
                    sportsbook = Sportsbook(key=book.key, name=book.title, region="us", active=True)
                    self.session.add(sportsbook)
                    self.session.flush()
                for market_dto in book.markets:
                    market_type = _market_type(market_dto.key)
                    if market_type is None:
                        continue
                    market = self.session.scalar(
                        select(Market).where(Market.game_id == game.id, Market.market_type == market_type, Market.period == "full_game")
                    )
                    if not market:
                        market = Market(game_id=game.id, market_type=market_type, period="full_game")
                        self.session.add(market)
                        self.session.flush()
                    for outcome_dto in market_dto.outcomes:
                        outcome_key = f"{outcome_dto.name.lower()}:{outcome_dto.point}"
                        outcome = self.session.scalar(
                            select(MarketOutcome).where(MarketOutcome.market_id == market.id, MarketOutcome.outcome_key == outcome_key)
                        )
                        if not outcome:
                            participant = next((team for name, team in team_by_name.items() if team_name_match(name, outcome_dto.name)), None)
                            outcome = MarketOutcome(
                                market_id=market.id,
                                outcome_key=outcome_key,
                                label=outcome_dto.name,
                                participant_team_id=participant.id if participant else None,
                            )
                            self.session.add(outcome)
                            self.session.flush()
                        duplicate = self.session.scalar(
                            select(OddsSnapshot.id).where(
                                OddsSnapshot.market_outcome_id == outcome.id,
                                OddsSnapshot.sportsbook_id == sportsbook.id,
                                OddsSnapshot.observed_at == market_dto.last_update,
                                OddsSnapshot.american_odds == outcome_dto.price,
                                OddsSnapshot.point == outcome_dto.point,
                            ).limit(1)
                        )
                        if duplicate:
                            continue
                        snapshot = OddsSnapshot(
                            market_outcome_id=outcome.id,
                            sportsbook_id=sportsbook.id,
                            observed_at=market_dto.last_update,
                            # ASP could not know this price before it fetched the
                            # payload, even if the provider's market timestamp is older.
                            available_as_of=available_as_of,
                            american_odds=outcome_dto.price,
                            decimal_odds=american_to_decimal(outcome_dto.price),
                            point=outcome_dto.point,
                            implied_probability=american_implied_probability(outcome_dto.price),
                            source=source,
                            source_event_id=event.source_event_id,
                            source_mode=event.source_mode.upper(),
                            raw_payload=outcome_dto.model_dump(mode="json"),
                        )
                        self.session.add(snapshot)
                        count += 1
        self.session.flush()
        return count

    def _team(self, league_id: Any, external_id: str, name: str, abbreviation: str | None, source: str) -> Team:
        mapped = self._mapped_entity("team", source, external_id, Team)
        if mapped:
            return mapped
        team = self.session.scalar(select(Team).where(Team.league_id == league_id, Team.name == name))
        if not team:
            team = Team(
                league_id=league_id,
                name=name,
                abbreviation=abbreviation or TEAM_ABBREVIATIONS.get(name, _abbreviation(name)),
                city=" ".join(name.split()[:-1]) or name,
            )
            self.session.add(team)
            self.session.flush()
        self.session.add(ExternalIdMapping(entity_type="team", entity_id=team.id, source=source, external_id=external_id))
        self.session.flush()
        return team

    def _venue(self, name: str, latitude: float | None, longitude: float | None, external_id: str | None, source: str) -> Venue:
        venue = self.session.scalar(select(Venue).where(Venue.name == name))
        ref_lat, ref_lon = VENUE_COORDINATES.get(name, (None, None))
        if not venue:
            venue = Venue(name=name, latitude=latitude or ref_lat, longitude=longitude or ref_lon, timezone=VENUE_TIMEZONES.get(name, "UTC"))
            self.session.add(venue)
            self.session.flush()
        if external_id and not self._mapped_entity("venue", source, external_id, Venue):
            self.session.add(ExternalIdMapping(entity_type="venue", entity_id=venue.id, source=source, external_id=external_id))
        self.session.flush()
        return venue

    def _player(self, dto: Any, team_id: Any, source: str) -> Player:
        external_id = dto.source_id or f"name:{dto.full_name}"
        mapped = self._mapped_entity("player", source, external_id, Player)
        if mapped:
            self._ensure_pitcher(mapped.id, getattr(dto, "throws", None))
            return mapped
        player = Player(team_id=team_id, full_name=dto.full_name or "Probable pitcher unavailable", position="P")
        self.session.add(player)
        self.session.flush()
        self.session.add(ExternalIdMapping(entity_type="player", entity_id=player.id, source=source, external_id=external_id))
        self._ensure_pitcher(player.id, getattr(dto, "throws", None))
        self.session.flush()
        return player

    def _ensure_pitcher(self, player_id: Any, throws: str | None) -> Pitcher:
        pitcher = self.session.scalar(select(Pitcher).where(Pitcher.player_id == player_id))
        if not pitcher:
            pitcher = Pitcher(player_id=player_id, throws=throws)
            self.session.add(pitcher)
        elif throws:
            pitcher.throws = throws
        return pitcher

    def _upsert_game_side(
        self,
        game_id: Any,
        team: Team,
        team_dto: Any,
        pitcher_dto: Any | None,
        is_home: bool,
        source: str,
    ) -> GameTeam:
        side = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game_id, GameTeam.is_home == is_home))
        if not side:
            side = GameTeam(game_id=game_id, team_id=team.id, is_home=is_home)
            self.session.add(side)
        side.team_id = team.id
        side.record_wins = team_dto.record_wins
        side.record_losses = team_dto.record_losses
        side.probable_pitcher_id = self._player(pitcher_dto, team.id, source).id if pitcher_dto else None
        side.pitcher_confirmed = pitcher_dto.confirmed if pitcher_dto else False
        side.pitcher_status_observed_at = datetime.now(UTC) if pitcher_dto else None
        self.session.flush()
        return side

    def _mapped_entity(self, entity_type: str, source: str, external_id: str, model: type) -> Any | None:
        mapping = self.session.scalar(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == entity_type,
            ExternalIdMapping.source == source,
            ExternalIdMapping.external_id == external_id,
        ))
        return self.session.get(model, mapping.entity_id) if mapping else None

    def _find_existing_game(self, league_id: Any, starts_at: datetime, home_team_id: Any, away_team_id: Any) -> Game | None:
        target = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=UTC)
        candidates = list(self.session.scalars(select(Game).where(
            Game.league_id == league_id,
            Game.starts_at >= target - timedelta(hours=2),
            Game.starts_at <= target + timedelta(hours=2),
        )))
        matches: list[Game] = []
        for game in candidates:
            sides = list(self.session.scalars(select(GameTeam).where(GameTeam.game_id == game.id)))
            if any(side.is_home and side.team_id == home_team_id for side in sides) and any(
                not side.is_home and side.team_id == away_team_id for side in sides
            ):
                matches.append(game)
        return matches[0] if len(matches) == 1 else None

    def _match_game(self, games: list[Game], home: str, away: str, commence_time: datetime) -> Game | None:
        target = commence_time if commence_time.tzinfo else commence_time.replace(tzinfo=UTC)
        candidates: list[tuple[float, Game]] = []
        for game in games:
            rows = list(self.session.execute(select(GameTeam, Team).join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id == game.id)))
            home_name = next((team.name for game_team, team in rows if game_team.is_home), "")
            away_name = next((team.name for game_team, team in rows if not game_team.is_home), "")
            if game_match(home_name, away_name, home, away):
                starts_at = game.starts_at if game.starts_at.tzinfo else game.starts_at.replace(tzinfo=UTC)
                difference = abs((starts_at - target).total_seconds())
                if difference <= 6 * 60 * 60:
                    candidates.append((difference, game))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        # Exact ambiguity can occur for malformed doubleheader feeds. Refuse to
        # attach odds rather than silently assigning them to an arbitrary game.
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            return None
        return candidates[0][1]


def _abbreviation(name: str) -> str:
    words = name.split()
    return "".join(word[0] for word in words[-3:]).upper()[:4]


def _game_status(value: str) -> GameStatus:
    lowered = value.lower()
    if "final" in lowered or "completed" in lowered:
        return GameStatus.FINAL
    if "progress" in lowered or "live" in lowered:
        return GameStatus.LIVE
    if "postpon" in lowered:
        return GameStatus.POSTPONED
    if "cancel" in lowered:
        return GameStatus.CANCELED
    return GameStatus.SCHEDULED


def _market_type(value: str) -> MarketType | None:
    return {"h2h": MarketType.MONEYLINE, "spreads": MarketType.SPREAD, "totals": MarketType.TOTAL}.get(value)
