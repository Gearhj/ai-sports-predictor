"""Chronological, point-in-time Elo replay for historical MLB games."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Iterable

from app.historical.temporal import as_utc


@dataclass(frozen=True)
class EloConfig:
    initial_rating: float = 1500.0
    k_factor: float = 20.0
    home_field_advantage: float = 35.0
    scale: float = 400.0
    method: str = "chronological_elo_v1"


@dataclass(frozen=True)
class InitialTeamRating:
    team_id: str
    rating: float


@dataclass(frozen=True)
class CompletedGameResult:
    game_id: str
    home_team_id: str
    away_team_id: str
    completed_at: datetime
    eligible_at: datetime
    home_score: int
    away_score: int


@dataclass(frozen=True)
class EloGameUpdate:
    game_id: str
    completed_at: datetime
    eligible_at: datetime
    home_team_id: str
    away_team_id: str
    home_rating_pre: float
    away_rating_pre: float
    expected_home_win: float
    actual_home_result: float
    home_rating_post: float
    away_rating_post: float


@dataclass(frozen=True)
class TeamRating:
    team_id: str
    rating: float
    games_played: int


@dataclass(frozen=True)
class EloPregameSnapshot:
    target_game_id: str
    cutoff: datetime
    home_team_id: str
    away_team_id: str
    home_rating: float
    away_rating: float
    expected_home_win: float
    processed_game_ids: tuple[str, ...]
    updates: tuple[EloGameUpdate, ...]
    final_ratings: tuple[TeamRating, ...]
    method: str


def expected_home_probability(
    home_rating: float,
    away_rating: float,
    *,
    config: EloConfig = EloConfig(),
) -> float:
    _validate_config(config)
    _finite("home_rating", home_rating)
    _finite("away_rating", away_rating)
    exponent = (away_rating - (home_rating + config.home_field_advantage)) / config.scale
    return 1.0 / (1.0 + 10.0**exponent)


def replay_pregame_elo(
    games: Iterable[CompletedGameResult],
    *,
    target_game_id: str,
    home_team_id: str,
    away_team_id: str,
    cutoff: datetime,
    initial_ratings: Iterable[InitialTeamRating] = (),
    config: EloConfig = EloConfig(),
) -> EloPregameSnapshot:
    """Replay only completed, eligible games and snapshot before the target.

    The target is excluded by identity in addition to timestamp checks.  Games
    are updated in completion order, with deterministic identity tie-breaking.
    """

    _validate_config(config)
    boundary = as_utc(cutoff)
    if not target_game_id or not home_team_id or not away_team_id:
        raise ValueError("Target game and team identifiers are required")
    if home_team_id == away_team_id:
        raise ValueError("Home and away teams must differ")

    ratings: dict[str, float] = {}
    games_played: dict[str, int] = {}
    for item in initial_ratings:
        if not item.team_id:
            raise ValueError("Initial rating team_id is required")
        if item.team_id in ratings:
            raise ValueError(f"Duplicate initial rating: {item.team_id}")
        _finite("initial rating", item.rating)
        ratings[item.team_id] = item.rating
        games_played[item.team_id] = 0

    rows = tuple(games)
    game_ids = [item.game_id for item in rows]
    if len(game_ids) != len(set(game_ids)):
        raise ValueError("Completed game identifiers must be unique")
    eligible: list[CompletedGameResult] = []
    for game in rows:
        _validate_game(game)
        if (
            game.game_id != target_game_id
            and as_utc(game.completed_at) < boundary
            and as_utc(game.eligible_at) <= boundary
        ):
            eligible.append(game)
    eligible.sort(key=lambda item: (as_utc(item.completed_at), item.game_id))

    updates: list[EloGameUpdate] = []
    for game in eligible:
        home_pre = ratings.get(game.home_team_id, config.initial_rating)
        away_pre = ratings.get(game.away_team_id, config.initial_rating)
        expected = expected_home_probability(home_pre, away_pre, config=config)
        actual = 1.0 if game.home_score > game.away_score else 0.0
        if game.home_score == game.away_score:
            actual = 0.5
        delta = config.k_factor * (actual - expected)
        home_post = home_pre + delta
        away_post = away_pre - delta
        ratings[game.home_team_id] = home_post
        ratings[game.away_team_id] = away_post
        games_played[game.home_team_id] = games_played.get(game.home_team_id, 0) + 1
        games_played[game.away_team_id] = games_played.get(game.away_team_id, 0) + 1
        updates.append(
            EloGameUpdate(
                game_id=game.game_id,
                completed_at=as_utc(game.completed_at),
                eligible_at=as_utc(game.eligible_at),
                home_team_id=game.home_team_id,
                away_team_id=game.away_team_id,
                home_rating_pre=home_pre,
                away_rating_pre=away_pre,
                expected_home_win=expected,
                actual_home_result=actual,
                home_rating_post=home_post,
                away_rating_post=away_post,
            )
        )

    home_rating = ratings.get(home_team_id, config.initial_rating)
    away_rating = ratings.get(away_team_id, config.initial_rating)
    all_teams = set(ratings) | {home_team_id, away_team_id}
    final_ratings = tuple(
        TeamRating(
            team_id=team_id,
            rating=ratings.get(team_id, config.initial_rating),
            games_played=games_played.get(team_id, 0),
        )
        for team_id in sorted(all_teams)
    )
    return EloPregameSnapshot(
        target_game_id=target_game_id,
        cutoff=boundary,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_rating=home_rating,
        away_rating=away_rating,
        expected_home_win=expected_home_probability(home_rating, away_rating, config=config),
        processed_game_ids=tuple(update.game_id for update in updates),
        updates=tuple(updates),
        final_ratings=final_ratings,
        method=config.method,
    )


def _validate_config(config: EloConfig) -> None:
    for name, value in (
        ("initial_rating", config.initial_rating),
        ("k_factor", config.k_factor),
        ("home_field_advantage", config.home_field_advantage),
        ("scale", config.scale),
    ):
        _finite(name, value)
    if config.k_factor <= 0 or config.scale <= 0:
        raise ValueError("Elo k_factor and scale must be positive")
    if not config.method:
        raise ValueError("Elo method is required")


def _validate_game(game: CompletedGameResult) -> None:
    if not game.game_id or not game.home_team_id or not game.away_team_id:
        raise ValueError("Completed game and team identifiers are required")
    if game.home_team_id == game.away_team_id:
        raise ValueError("Completed game teams must differ")
    if game.home_score < 0 or game.away_score < 0:
        raise ValueError("Completed game scores must be non-negative")
    if as_utc(game.eligible_at) < as_utc(game.completed_at):
        raise ValueError("A game cannot be eligible before it completed")


def _finite(name: str, value: float) -> None:
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")
