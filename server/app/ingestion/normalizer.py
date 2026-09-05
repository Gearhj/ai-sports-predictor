from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
import logging
from math import log10
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_sources.base import (
    BullpenAvailabilityDTO,
    LineupDTO,
    OddsEventDTO,
    RosterAvailabilityDTO,
    ScheduleGameDTO,
    TeamStatsDTO,
    WeatherDTO,
)
from app.markets.odds import american_implied_probability, american_to_decimal, game_match, team_name_match
from app.features.talent import estimate_true_talent
from app.models import (
    Batter,
    BullpenAvailabilityObservation,
    ExternalIdMapping,
    FeatureVersion,
    Game,
    GameStateObservation,
    GameStatus,
    GameTeam,
    League,
    LineupEntry,
    LineupObservation,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    Season,
    Sport,
    Sportsbook,
    Team,
    TeamFeature,
    TeamRatingObservation,
    Venue,
    WeatherSnapshot,
    Player,
    PlayerTalentEstimate,
    PlayerTeamHistory,
    Pitcher,
    Roster,
    RosterAvailabilityObservation,
    StarterObservation,
    StatcastPlayerObservation,
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


logger = logging.getLogger(__name__)


class CanonicalNormalizer:
    def __init__(self, session: Session) -> None:
        self.session = session
        # Observable quarantine of conflicting per-batch team-rating payloads
        # (INT-008). Callers and tests can inspect this after ingest_schedule.
        self.rating_conflicts: list[dict[str, Any]] = []

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
            select(FeatureVersion).where(FeatureVersion.name == "mlb_pregame", FeatureVersion.version == "2.0.0")
        )
        if not feature_version:
            feature_version = FeatureVersion(
                name="mlb_pregame",
                version="2.0.0",
                schema_hash=sha256(b"asp-mlb-point-in-time-feature-schema-v2").hexdigest(),
                definition={
                    "available_as_of_required": True,
                    "knowledge_time_contract": "GameStateService.get_game_state",
                    "windows_days": [7, 14, 30],
                    "missing_values_are_explicit": True,
                    "lineup_weighting": "plate appearances and batting order",
                    "talent_method": "empirical_bayes_weighted_evidence_v1",
                },
            )
            self.session.add(feature_version)
        self.session.flush()
        return sport, league, season, feature_version

    def ingest_schedule(
        self,
        games: list[ScheduleGameDTO],
        source: str,
        *,
        available_as_of: datetime | None = None,
        ingestion_run_id: Any | None = None,
    ) -> list[Game]:
        if not games:
            return []
        knowledge_time = _aware(available_as_of or datetime.now(UTC))
        _, league, season, _ = self.ensure_core(games[0].event_date.year)
        persisted: list[Game] = []
        # INT-008: collect team-record rating candidates across the whole batch and
        # resolve one canonical record per natural key after the loop, so a
        # doubleheader team appearing in two games with a differing record no longer
        # double-inserts and aborts the entire date's finalization.
        rating_candidates: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for dto in games:
            mode = _source_mode(dto.source_mode, "schedule")
            venue = self._venue(dto.venue.name, dto.venue.latitude, dto.venue.longitude, dto.venue.source_id, source)
            away = self._team(
                league.id,
                dto.away_team.source_id,
                dto.away_team.name,
                dto.away_team.abbreviation,
                source,
                mode,
            )
            home = self._team(
                league.id,
                dto.home_team.source_id,
                dto.home_team.name,
                dto.home_team.abbreviation,
                source,
                mode,
            )
            existing_mapping = self.session.scalar(
                select(ExternalIdMapping).where(
                    ExternalIdMapping.entity_type == "game",
                    ExternalIdMapping.source == source,
                    ExternalIdMapping.external_id == dto.source_game_id,
                )
            )
            if existing_mapping:
                game = self.session.get(Game, existing_mapping.entity_id)
            else:
                game = None
            if game is None:
                game = self._find_existing_game(league.id, dto.scheduled_at, home.id, away.id)
                if game is not None:
                    self.session.add(ExternalIdMapping(
                        entity_type="game",
                        entity_id=game.id,
                        source=source,
                        external_id=dto.source_game_id,
                    ))
            if game is None:
                game = Game(
                    league_id=league.id,
                    season_id=season.id,
                    venue_id=venue.id,
                    starts_at=dto.scheduled_at,
                    status=_game_status(dto.status),
                    scheduled_innings=dto.scheduled_innings,
                    home_score=dto.home_score,
                    away_score=dto.away_score,
                    final_at=dto.final_at,
                    source_mode=mode,
                )
                self.session.add(game)
                self.session.flush()
                self.session.add(ExternalIdMapping(
                    entity_type="game",
                    entity_id=game.id,
                    source=source,
                    external_id=dto.source_game_id,
                ))
            elif game.status != GameStatus.FINAL:
                # Compatibility projection only. Historical consumers read the
                # append-only observations persisted below.
                game.starts_at = dto.scheduled_at
                game.status = _game_status(dto.status)
                game.venue_id = venue.id
                game.home_score = dto.home_score
                game.away_score = dto.away_score
                game.final_at = dto.final_at
                game.scheduled_innings = dto.scheduled_innings
                if mode == "LIVE":
                    game.source_mode = "LIVE"

            away_side = self._upsert_game_side(
                game.id,
                away,
                dto.away_team,
                dto.away_pitcher,
                False,
                source,
                observed_at=knowledge_time,
            )
            home_side = self._upsert_game_side(
                game.id,
                home,
                dto.home_team,
                dto.home_pitcher,
                True,
                source,
                observed_at=knowledge_time,
            )
            self._append_schedule_observations(
                game,
                dto,
                venue.id,
                home_side,
                away_side,
                source,
                knowledge_time,
                ingestion_run_id,
            )
            self._collect_record_rating(
                rating_candidates,
                home.id,
                dto.home_team.record_wins,
                dto.home_team.record_losses,
                source,
                dto.source_mode.upper(),
                knowledge_time,
            )
            self._collect_record_rating(
                rating_candidates,
                away.id,
                dto.away_team.record_wins,
                dto.away_team.record_losses,
                source,
                dto.source_mode.upper(),
                knowledge_time,
            )
            persisted.append(game)
        self._resolve_and_persist_record_ratings(rating_candidates, ingestion_run_id)
        self.session.flush()
        return persisted

    _RATING_METHOD = "record_log_odds_shrunk"
    _RATING_VERSION = "gate2_v1"

    def _collect_record_rating(
        self,
        candidates: dict[tuple[Any, ...], list[dict[str, Any]]],
        team_id: Any,
        wins: int | None,
        losses: int | None,
        source: str,
        source_mode: str,
        available_as_of: datetime,
    ) -> None:
        """Buffer one team-record rating candidate keyed by its natural key.

        Nothing is written here; ``_resolve_and_persist_record_ratings`` picks a
        single canonical record per key after the whole batch is collected.
        """
        if wins is None or losses is None or wins < 0 or losses < 0:
            return
        games = wins + losses
        if games <= 0:
            return
        # Ten neutral pseudo-games keep early-season records appropriately
        # shrunk. The log-odds mapping is descriptive, not a trained Elo claim.
        probability = (wins + 5.0) / (games + 10.0)
        rating = max(1000.0, min(2000.0, 1500.0 + 400.0 * log10(probability / (1.0 - probability))))
        payload = {
            "team_id": str(team_id),
            "wins": wins,
            "losses": losses,
            "neutral_pseudo_games": 10,
            "rating": round(rating, 6),
        }
        checksum = _checksum(payload)
        # The key mirrors uq_team_rating_observation exactly (no payload_checksum),
        # so buffering, idempotency, and the database natural key now agree.
        key = (team_id, self._RATING_METHOD, self._RATING_VERSION, source, source_mode, available_as_of)
        candidates.setdefault(key, []).append({
            "team_id": team_id,
            "wins": wins,
            "losses": losses,
            "games": games,
            "rating": rating,
            "payload": payload,
            "checksum": checksum,
            "source": source,
            "source_mode": source_mode,
            "available_as_of": available_as_of,
        })

    def _resolve_and_persist_record_ratings(
        self,
        candidates: dict[tuple[Any, ...], list[dict[str, Any]]],
        ingestion_run_id: Any | None,
    ) -> None:
        """Persist exactly one lawful rating per natural key.

        Identical repeats collapse to one row (idempotent across retries). When a
        team appears more than once in a batch (e.g. a doubleheader) with a
        *differing* record, one canonical record is chosen deterministically and
        the conflict is quarantined for observability rather than aborting the
        batch or overwriting already-persisted append-only evidence.
        """
        for key, group in candidates.items():
            team_id, method, version, source, source_mode, available_as_of = key
            distinct_checksums = {candidate["checksum"] for candidate in group}
            canonical = self._canonical_rating(group)

            existing = self.session.execute(
                select(
                    TeamRatingObservation.id,
                    TeamRatingObservation.payload_checksum,
                ).where(
                    TeamRatingObservation.team_id == team_id,
                    TeamRatingObservation.method == method,
                    TeamRatingObservation.version == version,
                    TeamRatingObservation.available_as_of == available_as_of,
                    TeamRatingObservation.source == source,
                    TeamRatingObservation.source_mode == source_mode,
                ).limit(1)
            ).first()

            if existing is not None:
                if existing.payload_checksum != canonical["checksum"]:
                    # A conflicting observation already exists for this natural key.
                    # It is append-only authoritative evidence: never overwrite it.
                    self._record_rating_conflict(
                        canonical,
                        distinct_checksums | {existing.payload_checksum},
                        reason="conflicts_with_persisted_observation",
                        persisted_checksum=existing.payload_checksum,
                    )
                elif len(distinct_checksums) > 1:
                    self._record_rating_conflict(
                        canonical, distinct_checksums, reason="intra_batch_conflict_idempotent"
                    )
                # Same natural key already persisted: idempotent no-op.
                continue

            if len(distinct_checksums) > 1:
                self._record_rating_conflict(
                    canonical, distinct_checksums, reason="intra_batch_conflict_resolved"
                )

            self.session.add(TeamRatingObservation(
                team_id=team_id,
                rating=canonical["rating"],
                method=method,
                version=version,
                evidence_cutoff=available_as_of,
                sample_size=float(canonical["games"]),
                components=canonical["payload"],
                observed_at=available_as_of,
                available_as_of=available_as_of,
                source=source,
                source_mode=source_mode,
                source_record_id=f"{team_id}:{canonical['wins']}-{canonical['losses']}",
                payload_checksum=canonical["checksum"],
                ingestion_run_id=ingestion_run_id,
            ))

    @staticmethod
    def _canonical_rating(group: list[dict[str, Any]]) -> dict[str, Any]:
        """Deterministic, order-independent choice of one record for a natural key.

        Prefer the most complete record (most games), then the higher rating, and
        finally the lexicographically smallest checksum so the result never depends
        on the order games were processed in.
        """
        best = max(group, key=lambda candidate: (candidate["games"], candidate["rating"]))
        tied = [
            candidate
            for candidate in group
            if (candidate["games"], candidate["rating"]) == (best["games"], best["rating"])
        ]
        return min(tied, key=lambda candidate: candidate["checksum"])

    def _record_rating_conflict(
        self,
        canonical: dict[str, Any],
        checksums: set[str],
        *,
        reason: str,
        persisted_checksum: str | None = None,
    ) -> None:
        detail = {
            "team_id": str(canonical["team_id"]),
            "source": canonical["source"],
            "source_mode": canonical["source_mode"],
            "available_as_of": canonical["available_as_of"].isoformat(),
            "method": self._RATING_METHOD,
            "version": self._RATING_VERSION,
            "chosen_checksum": canonical["checksum"],
            "chosen_record": f"{canonical['wins']}-{canonical['losses']}",
            "conflicting_checksums": sorted(checksums),
            "reason": reason,
        }
        if persisted_checksum is not None:
            detail["persisted_checksum"] = persisted_checksum
        self.rating_conflicts.append(detail)
        logger.warning("team-rating conflict quarantined (INT-008): %s", detail)

    def _append_schedule_observations(
        self,
        game: Game,
        dto: ScheduleGameDTO,
        venue_id: Any,
        home_side: GameTeam,
        away_side: GameTeam,
        source: str,
        available_as_of: datetime,
        ingestion_run_id: Any | None,
    ) -> None:
        mode = dto.source_mode.upper()
        payload = dto.model_dump(mode="json")
        checksum = _checksum(payload)
        duplicate = self.session.scalar(
            select(GameStateObservation.id).where(
                GameStateObservation.game_id == game.id,
                GameStateObservation.source == source,
                GameStateObservation.source_mode == mode,
                GameStateObservation.available_as_of == available_as_of,
                GameStateObservation.payload_checksum == checksum,
            ).limit(1)
        )
        if duplicate is None:
            status = _game_status(dto.status)
            self.session.add(GameStateObservation(
                game_id=game.id,
                home_team_id=home_side.team_id,
                away_team_id=away_side.team_id,
                scheduled_at=dto.scheduled_at,
                status=status.value,
                is_postponed=status == GameStatus.POSTPONED,
                venue_id=venue_id,
                doubleheader_status=dto.doubleheader_status,
                game_number=dto.game_number,
                scheduled_innings=dto.scheduled_innings,
                home_probable_pitcher_id=home_side.probable_pitcher_id,
                away_probable_pitcher_id=away_side.probable_pitcher_id,
                home_starter_confirmation=(
                    "CONFIRMED" if dto.home_pitcher and dto.home_pitcher.confirmed
                    else "PROBABLE" if dto.home_pitcher else "UNKNOWN"
                ),
                away_starter_confirmation=(
                    "CONFIRMED" if dto.away_pitcher and dto.away_pitcher.confirmed
                    else "PROBABLE" if dto.away_pitcher else "UNKNOWN"
                ),
                home_score=dto.home_score,
                away_score=dto.away_score,
                final_at=dto.final_at,
                observed_at=available_as_of,
                available_as_of=available_as_of,
                source=source,
                source_mode=mode,
                source_record_id=dto.source_game_id,
                payload_checksum=checksum,
                ingestion_run_id=ingestion_run_id,
            ))
        for side, pitcher, label in (
            (away_side, dto.away_pitcher, "away"),
            (home_side, dto.home_pitcher, "home"),
        ):
            confirmation = (
                "CONFIRMED" if pitcher and pitcher.confirmed
                else "PROBABLE" if pitcher else "UNKNOWN"
            )
            starter_payload = {
                "game": dto.source_game_id,
                "side": label,
                "pitcher": pitcher.model_dump(mode="json") if pitcher else None,
            }
            starter_checksum = _checksum(starter_payload)
            existing = self.session.scalar(
                select(StarterObservation.id).where(
                    StarterObservation.game_id == game.id,
                    StarterObservation.team_id == side.team_id,
                    StarterObservation.source == source,
                    StarterObservation.source_mode == mode,
                    StarterObservation.available_as_of == available_as_of,
                    StarterObservation.payload_checksum == starter_checksum,
                ).limit(1)
            )
            if existing is None:
                self.session.add(StarterObservation(
                    game_id=game.id,
                    team_id=side.team_id,
                    probable_pitcher_id=side.probable_pitcher_id,
                    confirmed_pitcher_id=side.probable_pitcher_id if pitcher and pitcher.confirmed else None,
                    confirmation_state=confirmation,
                    throws=pitcher.throws if pitcher else None,
                    observed_at=available_as_of,
                    available_as_of=available_as_of,
                    source=source,
                    source_mode=mode,
                    source_record_id=f"{dto.source_game_id}:{label}",
                    payload_checksum=starter_checksum,
                    ingestion_run_id=ingestion_run_id,
                ))

    def ingest_lineups(
        self,
        lineups: list[LineupDTO],
        source: str,
        *,
        ingestion_run_id: Any | None = None,
    ) -> int:
        count = 0
        for dto in lineups:
            mode = _source_mode(dto.source_mode, "lineup")
            game = self._mapped_entity("game", source, dto.source_game_id, Game)
            team = self._mapped_entity("team", source, dto.source_team_id, Team)
            if game is None or team is None:
                continue
            if game.source_mode != mode:
                raise ValueError(
                    f"Lineup source mode {mode!r} does not match game mode {game.source_mode!r}"
                )
            payload = dto.model_dump(mode="json")
            checksum = _checksum(payload)
            available = _aware(dto.available_as_of)
            existing = self.session.scalar(
                select(LineupObservation.id).where(
                    LineupObservation.game_id == game.id,
                    LineupObservation.team_id == team.id,
                    LineupObservation.source == source,
                    LineupObservation.source_mode == mode,
                    LineupObservation.available_as_of == available,
                    LineupObservation.payload_checksum == checksum,
                ).limit(1)
            )
            if existing is not None:
                continue
            orders: set[int] = set()
            valid_entries = []
            for entry in dto.entries:
                if entry.batting_order in orders or not 1 <= entry.batting_order <= 9:
                    continue
                orders.add(entry.batting_order)
                valid_entries.append(entry)
            status = dto.status.upper()
            if status not in {"PROJECTED", "EXPECTED", "CONFIRMED"}:
                status = "EXPECTED"
            observation = LineupObservation(
                game_id=game.id,
                team_id=team.id,
                lineup_status=status,
                is_complete=len(valid_entries) == 9,
                observed_at=available,
                available_as_of=available,
                source=source,
                source_mode=mode,
                source_record_id=f"{dto.source_game_id}:{dto.source_team_id}",
                payload_checksum=checksum,
                ingestion_run_id=ingestion_run_id,
            )
            self.session.add(observation)
            self.session.flush()
            for entry in valid_entries:
                player = self._position_player(
                    entry.source_player_id,
                    entry.full_name,
                    team.id,
                    source,
                    position=entry.defensive_position,
                    bats=entry.bats,
                )
                self.session.add(LineupEntry(
                    lineup_observation_id=observation.id,
                    player_id=player.id,
                    batting_order=entry.batting_order,
                    defensive_position=entry.defensive_position,
                    bats=entry.bats,
                    is_starter=entry.starter,
                    expected_plate_appearance_weight=entry.expected_plate_appearance_weight,
                    expected_plate_appearances=(
                        4.5 * entry.expected_plate_appearance_weight
                        if entry.expected_plate_appearance_weight is not None else None
                    ),
                ))
            count += 1
        self.session.flush()
        return count

    def ingest_roster_availability(
        self,
        rows: list[RosterAvailabilityDTO],
        source: str,
        *,
        ingestion_run_id: Any | None = None,
    ) -> int:
        count = 0
        for dto in rows:
            mode = _source_mode(dto.source_mode, "roster availability")
            team = self._mapped_entity("team", source, dto.source_team_id, Team)
            if team is None:
                continue
            _, _, season, _ = self.ensure_core(dto.available_as_of.year)
            player = self._position_player(
                dto.source_player_id,
                dto.full_name,
                team.id,
                source,
                position=dto.position,
                bats=dto.bats,
                throws=dto.throws,
            )
            payload = dto.model_dump(mode="json")
            checksum = _checksum(payload)
            available = _aware(dto.available_as_of)
            effective = _aware(dto.effective_at)
            existing = self.session.scalar(
                select(RosterAvailabilityObservation.id).where(
                    RosterAvailabilityObservation.player_id == player.id,
                    RosterAvailabilityObservation.team_id == team.id,
                    RosterAvailabilityObservation.source == source,
                    RosterAvailabilityObservation.source_mode == mode,
                    RosterAvailabilityObservation.available_as_of == available,
                    RosterAvailabilityObservation.payload_checksum == checksum,
                ).limit(1)
            )
            if existing is not None:
                continue
            replacement = (
                self._player_by_external_id(dto.replacement_source_player_id)
                if dto.replacement_source_player_id else None
            )
            active = dto.status.upper() in {"ACTIVE", "AVAILABLE", "EXPECTED"}
            self.session.add(RosterAvailabilityObservation(
                season_id=season.id,
                team_id=team.id,
                player_id=player.id,
                availability_status=dto.status.upper(),
                transaction_status=dto.status.upper(),
                effective_at=effective,
                participation_probability=dto.participation_probability,
                replacement_player_id=replacement.id if replacement else None,
                active_roster=active,
                observed_at=available,
                available_as_of=available,
                source=source,
                source_mode=mode,
                source_record_id=f"{dto.source_team_id}:{dto.source_player_id}",
                payload_checksum=checksum,
                ingestion_run_id=ingestion_run_id,
            ))
            self.session.add(Roster(
                season_id=season.id,
                team_id=team.id,
                player_id=player.id,
                available_as_of=available,
                status=dto.status.lower(),
            ))
            latest_history = self.session.scalar(
                select(PlayerTeamHistory)
                .where(
                    PlayerTeamHistory.player_id == player.id,
                    PlayerTeamHistory.available_as_of <= available,
                )
                .order_by(PlayerTeamHistory.available_as_of.desc(), PlayerTeamHistory.created_at.desc())
                .limit(1)
            )
            if latest_history is None or latest_history.team_id != team.id:
                self.session.add(PlayerTeamHistory(
                    player_id=player.id,
                    team_id=team.id,
                    starts_at=effective,
                    available_as_of=available,
                ))
            count += 1
        self.session.flush()
        return count

    def ingest_bullpen_availability(
        self,
        game_id: Any,
        rows: list[BullpenAvailabilityDTO],
        source: str,
        *,
        ingestion_run_id: Any | None = None,
    ) -> int:
        """Append workload observations without turning missing evidence into zero.

        The caller supplies the canonical target game because the provider feed
        is team/date scoped. Source mode and first-pitch checks make it
        impossible to attach demo evidence to a live game or fetch-time evidence
        to a game whose pregame cutoff has already closed.
        """

        game = self.session.get(Game, game_id)
        if game is None:
            return 0
        game_starts_at = _aware(game.starts_at)
        game_team_ids = set(self.session.scalars(
            select(GameTeam.team_id).where(GameTeam.game_id == game.id)
        ))
        count = 0
        for dto in rows:
            mode = _source_mode(dto.source_mode, "bullpen availability")
            if game.source_mode != mode:
                raise ValueError(
                    f"Bullpen source mode {mode!r} does not match game mode {game.source_mode!r}"
                )
            available = _aware(dto.available_as_of)
            if available >= game_starts_at:
                # Pregame availability evidence is closed at first pitch.
                continue
            team = self._mapped_entity("team", source, dto.source_team_id, Team)
            if team is None or team.id not in game_team_ids:
                continue
            player = self._position_player(
                dto.source_player_id,
                dto.full_name,
                team.id,
                source,
                position="P",
                throws=dto.throws,
            )
            payload = dto.model_dump(mode="json")
            checksum = _checksum(payload)
            existing = self.session.scalar(
                select(BullpenAvailabilityObservation.id).where(
                    BullpenAvailabilityObservation.game_id == game.id,
                    BullpenAvailabilityObservation.team_id == team.id,
                    BullpenAvailabilityObservation.player_id == player.id,
                    BullpenAvailabilityObservation.source == source,
                    BullpenAvailabilityObservation.source_mode == mode,
                    BullpenAvailabilityObservation.available_as_of == available,
                    BullpenAvailabilityObservation.payload_checksum == checksum,
                ).limit(1)
            )
            if existing is not None:
                continue
            score_ready = (dto.workload or {}).get("score_status") == "READY"
            probability = dto.participation_probability if score_ready else None
            self.session.add(BullpenAvailabilityObservation(
                game_id=game.id,
                team_id=team.id,
                player_id=player.id,
                true_talent_estimate_id=None,
                role=dto.role,
                throws=dto.throws,
                pitches_previous_day=dto.pitches_previous_day,
                appearances_last_three_days=dto.appearances_last_three_days,
                innings_last_three_days=dto.innings_last_three_days,
                used_consecutive_days=dto.used_consecutive_days,
                # Workload telemetry is not an official club availability flag.
                expected_available=dto.expected_available,
                participation_probability=probability,
                workload=dto.workload,
                observed_at=available,
                available_as_of=available,
                source=source,
                source_mode=mode,
                source_record_id=(
                    f"{game.id}:{dto.source_team_id}:{dto.source_player_id}:"
                    f"{dto.evidence_start_date.isoformat()}:{dto.evidence_end_date.isoformat()}"
                ),
                payload_checksum=checksum,
                ingestion_run_id=ingestion_run_id,
            ))
            count += 1
        self.session.flush()
        return count

    def ingest_statcast(
        self,
        rows: list[dict[str, Any]],
        source: str,
        *,
        season: int,
        available_as_of: datetime,
        raw_reference: str | None = None,
        ingestion_run_id: Any | None = None,
        source_mode: str = "LIVE",
    ) -> int:
        count = 0
        available = _aware(available_as_of)
        mode = source_mode.upper()
        if mode not in {"LIVE", "DEMO"}:
            raise ValueError(f"Unsupported Statcast source mode: {source_mode!r}")
        for row in rows:
            external_id = str(row.get("source_player_id") or "").strip()
            if not external_id:
                continue
            player = self._player_by_external_id(external_id)
            if player is None:
                # Promotion requires a canonical identity established by the
                # authoritative MLB roster/schedule feed.
                continue
            if self._mapped_entity("player", source, external_id, Player) is None:
                self.session.add(ExternalIdMapping(
                    entity_type="player",
                    entity_id=player.id,
                    source=source,
                    external_id=external_id,
                ))
                self.session.flush()
            values = {
                key: value for key, value in row.items()
                if key not in {"source_player_id", "player_name", "source_team", "player_type", "attempts"}
            }
            checksum = _checksum(row)
            existing = self.session.scalar(
                select(StatcastPlayerObservation.id).where(
                    StatcastPlayerObservation.player_id == player.id,
                    StatcastPlayerObservation.season == season,
                    StatcastPlayerObservation.source == source,
                    StatcastPlayerObservation.source_mode == mode,
                    StatcastPlayerObservation.available_as_of == available,
                    StatcastPlayerObservation.payload_checksum == checksum,
                ).limit(1)
            )
            if existing is not None:
                continue
            self.session.add(StatcastPlayerObservation(
                player_id=player.id,
                team_id=player.team_id,
                season=season,
                role=str(row.get("player_type") or "HITTER").upper(),
                evidence_cutoff=available,
                sample_size=_float_or_none(row.get("attempts")),
                feature_schema_version="statcast_player_v1",
                values=values,
                raw_reference=raw_reference,
                observed_at=available,
                available_as_of=available,
                source=source,
                source_mode=mode,
                source_record_id=external_id,
                payload_checksum=checksum,
                ingestion_run_id=ingestion_run_id,
            ))
            role = str(row.get("player_type") or "HITTER").upper()
            metric_specs = (
                (
                    ("xera", 4.20, 0.0, 15.0),
                    ("velocity", 93.0, 50.0, 110.0),
                    ("whiff_rate", 0.25, 0.0, 1.0),
                    ("strikeout_rate", 0.22, 0.0, 1.0),
                    ("walk_rate", 0.08, 0.0, 1.0),
                )
                if role == "PITCHER"
                else (
                    ("xwoba", 0.320, 0.0, 1.0),
                    ("barrel_rate", 0.080, 0.0, 1.0),
                    ("hard_hit_rate", 0.380, 0.0, 1.0),
                    ("average_exit_velocity", 88.0, 0.0, 130.0),
                )
            )
            for metric_name, prior, lower, upper in metric_specs:
                season_value = _float_or_none(values.get(metric_name))
                sample_size = _float_or_none(row.get("attempts")) or 0.0
                if season_value is None or sample_size <= 0:
                    continue
                estimate = estimate_true_talent(
                    prior=prior,
                    prior_sample_size=200.0,
                    season_value=season_value,
                    season_sample_size=sample_size,
                    recent_value=None,
                    recent_sample_size=0.0,
                    lower_bound=lower,
                    upper_bound=upper,
                )
                duplicate_talent = self.session.scalar(
                    select(PlayerTalentEstimate.id).where(
                        PlayerTalentEstimate.player_id == player.id,
                        PlayerTalentEstimate.role == role,
                        PlayerTalentEstimate.metric_name == metric_name,
                        PlayerTalentEstimate.version == "gate2_empirical_bayes_v1",
                        PlayerTalentEstimate.source == source,
                        PlayerTalentEstimate.source_mode == mode,
                        PlayerTalentEstimate.available_as_of == available,
                    ).limit(1)
                )
                if duplicate_talent is not None:
                    continue
                self.session.add(PlayerTalentEstimate(
                    player_id=player.id,
                    role=role,
                    metric_name=metric_name,
                    metric_class="UNDERLYING_SKILL",
                    prior_value=prior,
                    prior_sample_size=200.0,
                    season_value=season_value,
                    season_sample_size=sample_size,
                    posterior_value=estimate.value,
                    posterior_delta=estimate.value - prior,
                    reliability=min(1.0, estimate.total_effective_sample_size / 600.0),
                    evidence_cutoff=available,
                    method=estimate.method,
                    version="gate2_empirical_bayes_v1",
                    parameters={
                        "lower_bound": lower,
                        "upper_bound": upper,
                        "weights": estimate.weights,
                    },
                    evidence_references=[{
                        "source": source,
                        "source_player_id": external_id,
                        "metric": metric_name,
                        "statcast_payload_checksum": checksum,
                    }],
                    observed_at=available,
                    available_as_of=available,
                    source=source,
                    source_mode=mode,
                    source_record_id=f"{external_id}:{metric_name}",
                    payload_checksum=_checksum({
                        "statcast": checksum,
                        "metric": metric_name,
                        "posterior": estimate.value,
                    }),
                    ingestion_run_id=ingestion_run_id,
                ))
            count += 1
        self.session.flush()
        return count

    def ingest_team_stats(self, dto: TeamStatsDTO, source: str) -> TeamFeature | None:
        team = self._mapped_entity("team", source, dto.source_team_id, Team)
        if not team:
            return None
        _, _, _, feature_version = self.ensure_core(dto.available_as_of.year)
        # Game-neutral team snapshots use the next game known after the
        # observation cutoff. They must never be attached by looking backward
        # from the current mutable schedule.
        game_id = self.session.scalar(
            select(GameTeam.game_id)
            .join(Game, Game.id == GameTeam.game_id)
            .where(GameTeam.team_id == team.id, Game.starts_at >= dto.available_as_of)
            .order_by(Game.starts_at.asc())
            .limit(1)
        )
        if game_id is None:
            game_id = self.session.scalar(
                select(GameTeam.game_id)
                .join(Game, Game.id == GameTeam.game_id)
                .where(GameTeam.team_id == team.id)
                .order_by(Game.starts_at.desc())
                .limit(1)
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
        mode = _source_mode(dto.source_mode, "weather")
        payload = dto.model_dump(mode="json")
        snapshot = WeatherSnapshot(
            game_id=game_id,
            source=source,
            # ``observed_at`` is when the provider observation became known;
            # the future forecast target remains in the archived raw payload.
            observed_at=dto.available_as_of,
            available_as_of=dto.available_as_of,
            temperature_f=dto.temperature_f,
            wind_mph=dto.wind_speed_mph,
            wind_direction=str(dto.wind_direction_degrees) if dto.wind_direction_degrees is not None else None,
            precipitation_probability=dto.precipitation_probability,
            raw_payload=payload,
            payload_checksum=sha256(str(sorted(payload.items())).encode()).hexdigest(),
            source_mode=mode,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def ingest_odds(
        self,
        events: list[OddsEventDTO],
        source: str,
        available_as_of: datetime,
        *,
        source_mode: str | None = None,
        event_date: date | None = None,
    ) -> int:
        count = 0
        inferred_event_modes = {_source_mode(event.source_mode, "odds event") for event in events}
        if source_mode is not None:
            expected_mode = _source_mode(source_mode, "odds ingestion")
        elif source == "ASP_DEMO_FIXTURE":
            expected_mode = "DEMO"
        elif inferred_event_modes == {"DEMO"}:
            expected_mode = "DEMO"
        else:
            expected_mode = "LIVE"
        # Direct compatibility/test callers may omit source_mode on a synthetic
        # event while targeting an already-known DEMO game. Production
        # orchestration always supplies an explicit mode and never takes this.
        if source_mode is None and inferred_event_modes == {"LIVE"}:
            live_exists = self.session.scalar(select(Game.id).where(Game.source_mode == "LIVE").limit(1))
            demo_exists = self.session.scalar(select(Game.id).where(Game.source_mode == "DEMO").limit(1))
            if live_exists is None and demo_exists is not None:
                expected_mode = "DEMO"
        game_query = select(Game).where(Game.source_mode == expected_mode)
        if event_date is not None:
            start = datetime.combine(event_date, datetime.min.time(), tzinfo=UTC) - timedelta(hours=12)
            end = start + timedelta(days=2)
            game_query = game_query.where(Game.starts_at >= start, Game.starts_at < end)
        games = list(self.session.scalars(game_query.order_by(Game.starts_at)))
        for event in events:
            if source_mode is not None and event.source_mode.upper() != expected_mode:
                continue
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
                                OddsSnapshot.source == source,
                                OddsSnapshot.source_mode == event.source_mode.upper(),
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
                            payload_checksum=_checksum({
                                "event": event.source_event_id,
                                "sportsbook": book.key,
                                "market": market_dto.key,
                                "market_observed_at": market_dto.last_update,
                                "outcome": outcome_dto.model_dump(mode="json"),
                            }),
                        )
                        self.session.add(snapshot)
                        count += 1
        self.session.flush()
        return count

    def _team(
        self,
        league_id: Any,
        external_id: str,
        name: str,
        abbreviation: str | None,
        source: str,
        source_mode: str,
    ) -> Team:
        mapped = self._mapped_entity("team", source, external_id, Team)
        if mapped:
            authoritative_abbreviation = (abbreviation or "").strip()
            historical_placeholder = f"MLB{external_id}"[:12]
            if (
                source == "MLB_STATS_API"
                and source_mode == "LIVE"
                and authoritative_abbreviation
                and mapped.abbreviation == historical_placeholder
            ):
                mapped.abbreviation = authoritative_abbreviation
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

    def _position_player(
        self,
        external_id: str,
        full_name: str,
        team_id: Any,
        source: str,
        *,
        position: str | None = None,
        bats: str | None = None,
        throws: str | None = None,
    ) -> Player:
        mapped = self._mapped_entity("player", source, external_id, Player)
        if mapped is None:
            # MLB and Savant use the same numeric person identifiers but are
            # distinct provenance sources. Reuse an already-canonical person.
            mapped = self._player_by_external_id(external_id)
        if mapped is None:
            mapped = Player(
                team_id=team_id,
                full_name=full_name or f"MLB player {external_id}",
                position=position,
            )
            self.session.add(mapped)
            self.session.flush()
        else:
            mapped.team_id = team_id
            if position:
                mapped.position = position
        if self._mapped_entity("player", source, external_id, Player) is None:
            self.session.add(ExternalIdMapping(
                entity_type="player",
                entity_id=mapped.id,
                source=source,
                external_id=external_id,
            ))
            self.session.flush()
        if bats:
            batter = self.session.scalar(select(Batter).where(Batter.player_id == mapped.id))
            if batter is None:
                self.session.add(Batter(player_id=mapped.id, bats=bats))
            else:
                batter.bats = bats
        if throws or (position and position.upper() == "P"):
            self._ensure_pitcher(mapped.id, throws)
        return mapped

    def _player_by_external_id(self, external_id: str | None) -> Player | None:
        if not external_id:
            return None
        mappings = list(self.session.scalars(
            select(ExternalIdMapping)
            .where(
                ExternalIdMapping.entity_type == "player",
                ExternalIdMapping.external_id == str(external_id),
            )
            .order_by(ExternalIdMapping.created_at.asc(), ExternalIdMapping.id.asc())
        ))
        if not mappings:
            return None
        preferred = next((row for row in mappings if row.source == "MLB_STATS_API"), mappings[0])
        return self.session.get(Player, preferred.entity_id)

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
        *,
        observed_at: datetime | None = None,
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
        side.pitcher_status_observed_at = observed_at if pitcher_dto else None
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


def _checksum(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _source_mode(value: str, context: str) -> str:
    mode = str(value).upper()
    if mode not in {"LIVE", "DEMO"}:
        raise ValueError(f"Unsupported {context} source mode: {value!r}")
    return mode


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
