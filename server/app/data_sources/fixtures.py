from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from .base import (
    BookmakerDTO,
    BookMarketDTO,
    OddsEventDTO,
    OddsOutcomeDTO,
    PitcherDTO,
    ScheduleGameDTO,
    SourceEnvelope,
    TeamDTO,
    TeamStatsDTO,
    VenueDTO,
    WeatherDTO,
)


DEMO_NOTICE = "DEMO DATA — deterministic fixture; not a live market observation"


class DemoSportsSource:
    """Deterministic offline fixture source. Every payload is explicitly marked DEMO."""

    source = "ASP_DEMO_FIXTURE"

    def __init__(self, *, available_as_of: datetime | None = None) -> None:
        self._fixed_available_as_of = (
            available_as_of
            if available_as_of is None or available_as_of.tzinfo is not None
            else available_as_of.replace(tzinfo=UTC)
        )

    def _available_as_of(self) -> datetime:
        return self._fixed_available_as_of or datetime.now(UTC)

    async def get_schedule(self, event_date: date) -> SourceEnvelope:
        games = demo_games(event_date)
        return SourceEnvelope(
            source=self.source,
            source_mode="DEMO",
            fetched_at=self._available_as_of(),
            data=[game.model_dump(mode="json") for game in games],
            metadata={"notice": DEMO_NOTICE, "game_count": len(games)},
        )

    async def get_team_stats(self, source_team_id: str, season: int) -> SourceEnvelope:
        # Stable variation provides useful demo matchups while remaining reproducible.
        offset = sum(ord(char) for char in source_team_id) % 12
        stats = TeamStatsDTO(
            source_team_id=source_team_id,
            available_as_of=self._available_as_of(),
            games_played=100,
            runs_per_game=4.15 + offset * 0.07,
            ops=0.700 + offset * 0.006,
            obp=0.305 + offset * 0.002,
            slg=0.395 + offset * 0.004,
            strikeout_rate=0.235 - offset * 0.002,
            walk_rate=0.075 + offset * 0.001,
            era=4.45 - offset * 0.045,
            whip=1.34 - offset * 0.007,
            recent_runs=4.0 + offset * 0.10,
            starter_era=4.15 - offset * 0.08,
            starter_xera=4.08 - offset * 0.065,
            bullpen_era=4.30 - offset * 0.055,
            xwoba=0.305 + offset * 0.002,
            barrel_rate=0.072 + offset * 0.002,
            hard_hit_rate=0.355 + offset * 0.004,
            average_exit_velocity=87.1 + offset * 0.22,
            raw_metrics={"fixture": True, "season": season},
        )
        return SourceEnvelope(
            source=self.source,
            source_mode="DEMO",
            fetched_at=stats.available_as_of,
            data=stats.model_dump(mode="json"),
            metadata={"notice": DEMO_NOTICE},
        )

    async def get_market_snapshot(self, event_date: date | None = None) -> SourceEnvelope:
        event_date = event_date or self._available_as_of().date()
        events: list[OddsEventDTO] = []
        for index, game in enumerate(demo_games(event_date)):
            home = -125 - index * 5
            away = 110 + index * 5
            books = []
            for book_index, (key, title) in enumerate(
                [("demo_alpha", "Demo Book Alpha"), ("demo_beta", "Demo Book Beta"), ("demo_gamma", "Demo Book Gamma")]
            ):
                updated = game.scheduled_at - timedelta(hours=2, minutes=book_index * 7)
                books.append(
                    BookmakerDTO(
                        key=key,
                        title=title,
                        last_update=updated,
                        markets=[
                            BookMarketDTO(
                                key="h2h",
                                last_update=updated,
                                outcomes=[
                                    OddsOutcomeDTO(name=game.home_team.name, price=home - book_index * 2),
                                    OddsOutcomeDTO(name=game.away_team.name, price=away + book_index * 2),
                                ],
                            ),
                            BookMarketDTO(
                                key="spreads",
                                last_update=updated,
                                outcomes=[
                                    OddsOutcomeDTO(name=game.home_team.name, point=-1.5, price=135 - book_index * 3),
                                    OddsOutcomeDTO(name=game.away_team.name, point=1.5, price=-155 + book_index * 3),
                                ],
                            ),
                            BookMarketDTO(
                                key="totals",
                                last_update=updated,
                                outcomes=[
                                    OddsOutcomeDTO(name="Over", point=8.5, price=-105 - book_index * 2),
                                    OddsOutcomeDTO(name="Under", point=8.5, price=-115 + book_index * 2),
                                ],
                            ),
                        ],
                    )
                )
            events.append(
                OddsEventDTO(
                    source_event_id=f"demo-odds-{game.source_game_id}",
                    commence_time=game.scheduled_at,
                    home_team=game.home_team.name,
                    away_team=game.away_team.name,
                    bookmakers=books,
                    source_mode="DEMO",
                )
            )
        return SourceEnvelope(
            source=self.source,
            source_mode="DEMO",
            fetched_at=self._available_as_of(),
            data=[event.model_dump(mode="json") for event in events],
            metadata={"notice": DEMO_NOTICE, "event_count": len(events)},
        )

    async def get_weather(self, latitude: float, longitude: float, at: datetime) -> SourceEnvelope:
        weather = WeatherDTO(
            available_as_of=self._available_as_of(),
            forecast_for=at,
            temperature_f=78.0,
            wind_speed_mph=8.0,
            wind_direction_degrees=225.0,
            precipitation_inches=0.0,
            precipitation_probability=12.0,
            humidity_percent=54.0,
            pressure_hpa=1014.0,
            weather_code=1,
            description="Mainly clear",
            source_mode="DEMO",
        )
        return SourceEnvelope(
            source=self.source,
            source_mode="DEMO",
            fetched_at=weather.available_as_of,
            data=weather.model_dump(mode="json"),
            metadata={"notice": DEMO_NOTICE, "latitude": latitude, "longitude": longitude},
        )


def demo_games(event_date: date) -> list[ScheduleGameDTO]:
    matchups = [
        ("147", "New York Yankees", "NYY", "111", "Boston Red Sox", "BOS", "Fenway Park", 42.3467, -71.0972, "Gerrit Cole", "Brayan Bello"),
        ("119", "Los Angeles Dodgers", "LAD", "137", "San Francisco Giants", "SF", "Oracle Park", 37.7786, -122.3893, "Yoshinobu Yamamoto", "Logan Webb"),
        ("141", "Toronto Blue Jays", "TOR", "139", "Tampa Bay Rays", "TB", "Tropicana Field", 27.7682, -82.6534, "Kevin Gausman", "Shane Baz"),
    ]
    result: list[ScheduleGameDTO] = []
    for index, (away_id, away, away_abbr, home_id, home, home_abbr, venue, lat, lon, away_sp, home_sp) in enumerate(matchups):
        scheduled = datetime.combine(event_date, time(23, 5), tzinfo=UTC) + timedelta(minutes=index * 35)
        result.append(
            ScheduleGameDTO(
                source_game_id=f"demo-{event_date:%Y%m%d}-{index + 1}",
                scheduled_at=scheduled,
                event_date=event_date,
                status="Scheduled — demo fixture",
                away_team=TeamDTO(source_id=away_id, name=away, abbreviation=away_abbr, record_wins=62-index, record_losses=48+index),
                home_team=TeamDTO(source_id=home_id, name=home, abbreviation=home_abbr, record_wins=59+index, record_losses=51-index),
                away_pitcher=PitcherDTO(source_id=f"demo-ap-{index}", full_name=away_sp, confirmed=True),
                home_pitcher=PitcherDTO(source_id=f"demo-hp-{index}", full_name=home_sp, confirmed=True),
                venue=VenueDTO(source_id=f"demo-v-{index}", name=venue, latitude=lat, longitude=lon),
                source_mode="DEMO",
            )
        )
    return result
