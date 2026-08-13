"""Point-in-time MLB game state assembled from append-only observations.

The mutable ``games`` and ``game_teams`` tables are identity/current-state
projections.  They are intentionally not used for scheduled time, score,
starter, lineup, roster, weather, market, rating, or player-feature knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import asin, cos, radians, sin, sqrt
from typing import Any, Iterable, TypeVar

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import (
    BullpenAvailabilityObservation,
    Game,
    GameStateObservation,
    GameTeam,
    LineupEntry,
    LineupObservation,
    Market,
    MarketOutcome,
    OddsSnapshot,
    ParkFactorVersion,
    PlayerTalentEstimate,
    RosterAvailabilityObservation,
    StarterObservation,
    StatcastPlayerObservation,
    TeamFeature,
    TeamRatingObservation,
    Venue,
    WeatherSnapshot,
)


class GameStateUnavailableError(RuntimeError):
    """Raised when no canonical game-state evidence existed by the cutoff."""


@dataclass(frozen=True)
class TeamKnowledgeState:
    team_id: Any
    is_home: bool
    lineup: LineupObservation | None
    lineup_entries: tuple[LineupEntry, ...]
    starter: StarterObservation | None
    roster: tuple[RosterAvailabilityObservation, ...]
    bullpen: tuple[BullpenAvailabilityObservation, ...]
    statcast: tuple[StatcastPlayerObservation, ...]
    talent: tuple[PlayerTalentEstimate, ...]
    team_feature: TeamFeature | None
    rating: TeamRatingObservation | None
    days_since_previous_game: float | None
    previous_venue_id: Any | None
    travel_distance_miles: float | None


@dataclass(frozen=True)
class GameKnowledgeState:
    game_id: Any
    source_mode: str
    data_as_of: datetime
    game: Game
    game_observation: GameStateObservation
    home: TeamKnowledgeState
    away: TeamKnowledgeState
    weather: WeatherSnapshot | None
    odds: tuple[OddsSnapshot, ...]
    park_factor: ParkFactorVersion | None

    @property
    def scheduled_at(self) -> datetime:
        return _aware(self.game_observation.scheduled_at)

    @property
    def final_at(self) -> datetime | None:
        return _aware(self.game_observation.final_at) if self.game_observation.final_at else None


class GameStateService:
    """Resolve exactly what ASP could know at one instant."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_game_state(self, game_id: Any, as_of_timestamp: datetime) -> GameKnowledgeState:
        cutoff = _aware(as_of_timestamp)
        game = self.session.get(Game, game_id)
        if game is None:
            raise LookupError(f"Unknown game {game_id}")
        source_mode = _mode(game.source_mode)
        observation = self.session.scalar(
            select(GameStateObservation)
            .where(
                GameStateObservation.game_id == game.id,
                GameStateObservation.available_as_of <= cutoff,
                GameStateObservation.observed_at <= cutoff,
                GameStateObservation.source_mode == source_mode,
            )
            .order_by(
                GameStateObservation.available_as_of.desc(),
                GameStateObservation.observed_at.desc(),
                GameStateObservation.created_at.desc(),
                GameStateObservation.id.desc(),
            )
            .limit(1)
        )
        if observation is None:
            raise GameStateUnavailableError(
                f"No {source_mode} game-state observation for {game.id} was available by {cutoff.isoformat()}"
            )

        home_team_id = observation.home_team_id
        away_team_id = observation.away_team_id
        if home_team_id is None or away_team_id is None:
            # Nullable observed-side IDs preserve compatibility with evidence
            # created before Gate 2. New ingestion always records both IDs.
            sides = list(self.session.scalars(select(GameTeam).where(GameTeam.game_id == game.id)))
            home_side = next((side for side in sides if side.is_home), None)
            away_side = next((side for side in sides if not side.is_home), None)
            home_team_id = home_team_id or (home_side.team_id if home_side else None)
            away_team_id = away_team_id or (away_side.team_id if away_side else None)
        if home_team_id is None or away_team_id is None:
            raise ValueError("Canonical game must have one home and one away team")

        home = self._team_state(game, observation, home_team_id, True, cutoff, source_mode)
        away = self._team_state(game, observation, away_team_id, False, cutoff, source_mode)
        weather = self._latest(
            WeatherSnapshot,
            (
                WeatherSnapshot.game_id == game.id,
                WeatherSnapshot.available_as_of <= cutoff,
                WeatherSnapshot.source_mode == source_mode,
            ),
        )
        odds = self._latest_market_observations(game.id, cutoff, source_mode)
        park = None
        if observation.venue_id is not None:
            park = self.session.scalar(
                select(ParkFactorVersion)
                .where(
                    ParkFactorVersion.venue_id == observation.venue_id,
                    ParkFactorVersion.available_as_of <= cutoff,
                    ParkFactorVersion.observed_at <= cutoff,
                    ParkFactorVersion.source_mode == source_mode,
                    ParkFactorVersion.season == observation.scheduled_at.year,
                )
                .order_by(
                    ParkFactorVersion.available_as_of.desc(),
                    ParkFactorVersion.observed_at.desc(),
                    ParkFactorVersion.created_at.desc(),
                    ParkFactorVersion.id.desc(),
                )
                .limit(1)
            )
        return GameKnowledgeState(
            game_id=game.id,
            source_mode=source_mode,
            data_as_of=cutoff,
            game=game,
            game_observation=observation,
            home=home,
            away=away,
            weather=weather,
            odds=odds,
            park_factor=park,
        )

    def _team_state(
        self,
        game: Game,
        game_observation: GameStateObservation,
        team_id: Any,
        is_home: bool,
        cutoff: datetime,
        source_mode: str,
    ) -> TeamKnowledgeState:
        lineup_candidates = list(self.session.scalars(
            select(LineupObservation).where(
                LineupObservation.game_id == game.id,
                LineupObservation.team_id == team_id,
                LineupObservation.available_as_of <= cutoff,
                LineupObservation.observed_at <= cutoff,
                LineupObservation.source_mode == source_mode,
            )
        ))
        status_rank = {"PROJECTED": 1, "EXPECTED": 2, "CONFIRMED": 3}
        lineup = max(
            lineup_candidates,
            key=lambda row: (
                status_rank.get(row.lineup_status.upper(), 0),
                _aware(row.available_as_of),
                _aware(row.observed_at),
                str(row.id),
            ),
            default=None,
        )
        entries: tuple[LineupEntry, ...] = ()
        if lineup is not None:
            entries = tuple(self.session.scalars(
                select(LineupEntry)
                .where(LineupEntry.lineup_observation_id == lineup.id)
                .order_by(LineupEntry.batting_order, LineupEntry.id)
            ))
        starter = self._latest(
            StarterObservation,
            (
                StarterObservation.game_id == game.id,
                StarterObservation.team_id == team_id,
                StarterObservation.available_as_of <= cutoff,
                StarterObservation.observed_at <= cutoff,
                StarterObservation.source_mode == source_mode,
            ),
        )
        roster = self._current_roster(team_id, cutoff, source_mode)
        bullpen = self._latest_per_player(
            BullpenAvailabilityObservation,
            (
                BullpenAvailabilityObservation.game_id == game.id,
                BullpenAvailabilityObservation.team_id == team_id,
                BullpenAvailabilityObservation.available_as_of <= cutoff,
                BullpenAvailabilityObservation.observed_at <= cutoff,
                BullpenAvailabilityObservation.source_mode == source_mode,
            ),
        )
        player_ids = {row.player_id for row in roster}
        player_ids.update(row.player_id for row in entries)
        if starter is not None:
            player_ids.update(
                player_id
                for player_id in (starter.confirmed_pitcher_id, starter.probable_pitcher_id)
                if player_id is not None
            )
        statcast_identity_filter = (
            StatcastPlayerObservation.player_id.in_(player_ids)
            if player_ids else StatcastPlayerObservation.team_id == team_id
        )
        statcast = self._latest_per_player(
            StatcastPlayerObservation,
            (
                statcast_identity_filter,
                StatcastPlayerObservation.available_as_of <= cutoff,
                StatcastPlayerObservation.observed_at <= cutoff,
                StatcastPlayerObservation.evidence_cutoff <= cutoff,
                StatcastPlayerObservation.source_mode == source_mode,
            ),
            secondary_key=lambda row: (row.player_id, row.role),
        )
        talent: tuple[PlayerTalentEstimate, ...] = ()
        if player_ids:
            talent = self._latest_per_player(
                PlayerTalentEstimate,
                (
                    PlayerTalentEstimate.player_id.in_(player_ids),
                    PlayerTalentEstimate.available_as_of <= cutoff,
                    PlayerTalentEstimate.observed_at <= cutoff,
                    PlayerTalentEstimate.evidence_cutoff <= cutoff,
                    PlayerTalentEstimate.source_mode == source_mode,
                ),
                secondary_key=lambda row: (row.player_id, row.role, row.metric_name),
            )
        team_feature = self._latest(
            TeamFeature,
            (
                TeamFeature.team_id == team_id,
                TeamFeature.available_as_of <= cutoff,
                TeamFeature.observed_at <= cutoff,
                TeamFeature.source_mode == source_mode,
            ),
        )
        rating = self.session.scalar(
            select(TeamRatingObservation)
            .where(
                TeamRatingObservation.team_id == team_id,
                TeamRatingObservation.available_as_of <= cutoff,
                TeamRatingObservation.observed_at <= cutoff,
                TeamRatingObservation.evidence_cutoff <= cutoff,
                TeamRatingObservation.source_mode == source_mode,
            )
            .order_by(
                TeamRatingObservation.available_as_of.desc(),
                TeamRatingObservation.evidence_cutoff.desc(),
                TeamRatingObservation.created_at.desc(),
                TeamRatingObservation.id.desc(),
            )
            .limit(1)
        )
        previous = self.session.scalar(
            select(GameStateObservation)
            .where(
                or_(
                    GameStateObservation.home_team_id == team_id,
                    GameStateObservation.away_team_id == team_id,
                ),
                GameStateObservation.game_id != game.id,
                GameStateObservation.scheduled_at < game_observation.scheduled_at,
                GameStateObservation.available_as_of <= cutoff,
                GameStateObservation.observed_at <= cutoff,
                GameStateObservation.source_mode == source_mode,
            )
            .order_by(
                GameStateObservation.scheduled_at.desc(),
                GameStateObservation.available_as_of.desc(),
                GameStateObservation.created_at.desc(),
                GameStateObservation.id.desc(),
            )
            .limit(1)
        )
        if previous is None:
            # Legacy observations may predate the observed-side columns. Only
            # those null-side rows fall back to the mutable projection.
            previous = self.session.scalar(
                select(GameStateObservation)
                .join(GameTeam, GameTeam.game_id == GameStateObservation.game_id)
                .where(
                    or_(
                        and_(
                            GameStateObservation.home_team_id.is_(None),
                            GameTeam.is_home.is_(True),
                            GameTeam.team_id == team_id,
                        ),
                        and_(
                            GameStateObservation.away_team_id.is_(None),
                            GameTeam.is_home.is_(False),
                            GameTeam.team_id == team_id,
                        ),
                    ),
                    GameStateObservation.game_id != game.id,
                    GameStateObservation.scheduled_at < game_observation.scheduled_at,
                    GameStateObservation.available_as_of <= cutoff,
                    GameStateObservation.observed_at <= cutoff,
                    GameStateObservation.source_mode == source_mode,
                )
                .order_by(
                    GameStateObservation.scheduled_at.desc(),
                    GameStateObservation.available_as_of.desc(),
                    GameStateObservation.created_at.desc(),
                    GameStateObservation.id.desc(),
                )
                .limit(1)
            )
        days_since_previous = None
        travel_distance = None
        previous_venue_id = previous.venue_id if previous else None
        if previous is not None:
            days_since_previous = max(
                0.0,
                (_aware(game_observation.scheduled_at) - _aware(previous.scheduled_at)).total_seconds()
                / 86_400.0,
            )
            travel_distance = self._travel_distance(previous.venue_id, game_observation.venue_id)
        return TeamKnowledgeState(
            team_id=team_id,
            is_home=is_home,
            lineup=lineup,
            lineup_entries=entries,
            starter=starter,
            roster=roster,
            bullpen=bullpen,
            statcast=statcast,
            talent=talent,
            team_feature=team_feature,
            rating=rating,
            days_since_previous_game=days_since_previous,
            previous_venue_id=previous_venue_id,
            travel_distance_miles=travel_distance,
        )

    def _travel_distance(self, from_venue_id: Any | None, to_venue_id: Any | None) -> float | None:
        if from_venue_id is None or to_venue_id is None:
            return None
        if from_venue_id == to_venue_id:
            return 0.0
        origin = self.session.get(Venue, from_venue_id)
        destination = self.session.get(Venue, to_venue_id)
        if (
            origin is None or destination is None
            or origin.latitude is None or origin.longitude is None
            or destination.latitude is None or destination.longitude is None
        ):
            return None
        return round(_haversine_miles(
            origin.latitude,
            origin.longitude,
            destination.latitude,
            destination.longitude,
        ), 3)

    def _current_roster(
        self,
        team_id: Any,
        cutoff: datetime,
        source_mode: str,
    ) -> tuple[RosterAvailabilityObservation, ...]:
        # Resolve the latest eligible observation per player across all teams.
        # Filtering to the requested team only after that resolution prevents a
        # player's old-club row from surviving a later point-in-time trade.
        rows = list(self.session.scalars(
            select(RosterAvailabilityObservation)
            .where(
                RosterAvailabilityObservation.available_as_of <= cutoff,
                RosterAvailabilityObservation.observed_at <= cutoff,
                RosterAvailabilityObservation.effective_at <= cutoff,
                RosterAvailabilityObservation.source_mode == source_mode,
            )
            .order_by(
                RosterAvailabilityObservation.available_as_of.desc(),
                RosterAvailabilityObservation.effective_at.desc(),
                RosterAvailabilityObservation.created_at.desc(),
                RosterAvailabilityObservation.id.desc(),
            )
        ))
        current = _dedupe(rows, lambda row: row.player_id)
        return tuple(
            row for row in current
            if row.team_id == team_id and row.active_roster is not False
        )

    def _latest_market_observations(
        self,
        game_id: Any,
        cutoff: datetime,
        source_mode: str,
    ) -> tuple[OddsSnapshot, ...]:
        rows = list(self.session.scalars(
            select(OddsSnapshot)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .where(
                Market.game_id == game_id,
                OddsSnapshot.available_as_of <= cutoff,
                OddsSnapshot.observed_at <= cutoff,
                OddsSnapshot.source_mode == source_mode,
            )
            .order_by(
                OddsSnapshot.available_as_of.desc(),
                OddsSnapshot.observed_at.desc(),
                OddsSnapshot.created_at.desc(),
                OddsSnapshot.id.desc(),
            )
        ))
        latest_market_book: dict[tuple[Any, Any], OddsSnapshot] = {}
        result: list[OddsSnapshot] = []
        for row in rows:
            outcome = self.session.get(MarketOutcome, row.market_outcome_id)
            if outcome is None:
                continue
            key = (outcome.market_id, row.sportsbook_id)
            anchor = latest_market_book.setdefault(key, row)
            if row.observed_at == anchor.observed_at and row.available_as_of == anchor.available_as_of:
                result.append(row)
        return tuple(result)

    def _latest(self, model: type[Any], criteria: Iterable[Any]) -> Any | None:
        return self.session.scalar(
            select(model)
            .where(*criteria)
            .order_by(
                model.available_as_of.desc(),
                model.observed_at.desc(),
                model.created_at.desc(),
                model.id.desc(),
            )
            .limit(1)
        )

    def _latest_per_player(
        self,
        model: type[Any],
        criteria: Iterable[Any],
        *,
        secondary_key: Any = lambda row: row.player_id,
    ) -> tuple[Any, ...]:
        rows = list(self.session.scalars(
            select(model)
            .where(*criteria)
            .order_by(
                model.available_as_of.desc(),
                model.observed_at.desc(),
                model.created_at.desc(),
                model.id.desc(),
            )
        ))
        return _dedupe(rows, secondary_key)


T = TypeVar("T")


def _dedupe(rows: Iterable[T], key: Any) -> tuple[T, ...]:
    seen: set[Any] = set()
    result: list[T] = []
    for row in rows:
        identity = key(row)
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return tuple(result)


def _mode(value: str) -> str:
    mode = str(value).upper()
    if mode not in {"LIVE", "DEMO"}:
        raise ValueError(f"Unsupported game source mode: {value!r}")
    return mode


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_miles = 3958.7613
    phi1, phi2 = radians(lat1), radians(lat2)
    delta_phi = radians(lat2 - lat1)
    delta_lambda = radians(lon2 - lon1)
    a = sin(delta_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
    return 2 * radius_miles * asin(sqrt(a))
