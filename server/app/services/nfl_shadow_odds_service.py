"""Exact-identity NFL odds ingestion for prospective research shadow.

The Odds API exposes provider event IDs and franchise display names, while the
frozen NFL schedule uses nflverse IDs.  A small explicit reviewed franchise map
plus exact home/away direction and kickoff establishes the mapping once.  Every
subsequent quote joins through the persisted provider event mapping; fuzzy name
matching is never used.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_sources.base import OddsEventDTO, OddsScoreEventDTO
from app.forecast_exchange.pricing import american_to_decimal, implied_probability
from app.data_sources.provider_registry import identity as provider_identity
from app.gen2.exchange.canonical_json import canonical_sha256
from app.models import (
    ExternalIdMapping, Game, GameStateObservation, GameStatus, GameTeam, Market,
    MarketOutcome, MarketType, OddsSnapshot, ShadowForecast, ShadowForecastBundle,
    Sportsbook,
)


NFL_ODDS_TEAM_IDENTITIES: dict[str, str] = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}


class NFLShadowOddsIdentityError(RuntimeError):
    pass


class NFLShadowOddsService:
    """NFL odds ingestion, namespaced by the provider that supplied them.

    The source used to be hardcoded to THE_ODDS_API in three places: the
    duplicate check, the snapshot's own provenance, and the external event-id
    mapping. That was correct while one provider fed this path and silently
    wrong the moment a second one did -- SportsGameOdds rows would have been
    stamped THE_ODDS_API, its event ids registered in another provider's id
    namespace, and its "draftkings" merged into the same Sportsbook row as The
    Odds API's. Because the governed two-sided pairing keys on sportsbook_id,
    that last one could put two providers on opposite sides of a single
    "same-book" quote.

    So the provider is a constructor argument, and the bookmaker key goes
    through the same `provider_identity` namespacing the canonical normalizer
    uses. The default keeps every existing caller on exactly its old behaviour.
    """

    def __init__(self, session: Session, *, source: str = "THE_ODDS_API") -> None:
        self.session = session
        self.source = source

    def ingest(self, events: list[OddsEventDTO], *, available_as_of: datetime) -> dict[str, int]:
        mapped_events = quotes = duplicates = rejected = 0
        for event in events:
            forecast = self._map_event(event)
            if forecast is None:
                rejected += 1
                continue
            mapped_events += 1
            created, existing = self._ingest_event(forecast, event, available_as_of)
            quotes += created
            duplicates += existing
        self.session.flush()
        return {
            "events": len(events), "mapped_events": mapped_events,
            "quotes": quotes, "duplicates": duplicates, "rejected_events": rejected,
        }

    def checkpoint_due(self, now: datetime) -> bool:
        """Return true only when a bounded prospective market checkpoint is missing."""

        now = _aware(now)
        forecasts = list(self.session.scalars(
            select(ShadowForecast)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(
                ShadowForecastBundle.sport == "NFL",
                ShadowForecast.engine_id == "NFL_M1_XGBOOST",
                ShadowForecast.scheduled_start_utc > now,
                ShadowForecast.scheduled_start_utc <= now.replace(microsecond=0) + timedelta(hours=48),
            )
        ))
        seen_games: set[object] = set()
        for forecast in forecasts:
            if forecast.canonical_game_id in seen_games:
                continue
            seen_games.add(forecast.canonical_game_id)
            mapping = self.session.scalar(select(ExternalIdMapping).where(
                ExternalIdMapping.entity_type == "game",
                ExternalIdMapping.source == "THE_ODDS_API",
                ExternalIdMapping.entity_id == forecast.canonical_game_id,
            ))
            states: set[str] = set()
            if mapping is not None:
                raw_payloads = self.session.scalars(select(OddsSnapshot.raw_payload).where(
                    OddsSnapshot.source == "THE_ODDS_API",
                    OddsSnapshot.source_event_id == mapping.external_id,
                ))
                states = {
                    str(raw.get("snapshot_state"))
                    for raw in raw_payloads if isinstance(raw, dict) and raw.get("snapshot_state")
                }
            desired = _checkpoint_state(
                first_observed=not states,
                available_as_of=now,
                kickoff=_aware(forecast.scheduled_start_utc),
            )
            if desired != "CURRENT" and desired not in states:
                return True
        return False

    def ingest_scores(
        self,
        events: list[OddsScoreEventDTO],
        *,
        available_as_of: datetime,
        event_date: date,
    ) -> dict[str, int]:
        """Persist exact mapped NFL finals without touching LIVE MLB games."""

        mapped = finals = reused = incomplete = rejected = 0
        for event in events:
            # A quote is not required for scientific score evaluation. Establish
            # the provider mapping from the same exact team sides + kickoff
            # authority when the scores endpoint is the first observation.
            forecast = self._map_event(event)
            if forecast is None or forecast.event_date != event_date:
                rejected += 1
                continue
            self._validate_score_identity(forecast, event)
            mapped += 1
            if not event.completed:
                incomplete += 1
                continue
            if len(event.scores) != 2 or len({row.name for row in event.scores}) != 2:
                raise NFLShadowOddsIdentityError(
                    f"Final scores for {event.source_event_id} are not exactly two unique team rows"
                )
            score_by_team = {
                NFL_ODDS_TEAM_IDENTITIES.get(row.name): int(row.score)
                for row in event.scores
            }
            if set(score_by_team) != {forecast.home_team_id, forecast.away_team_id}:
                raise NFLShadowOddsIdentityError(
                    f"Final scores for {event.source_event_id} do not cover exact home/away identities"
                )
            home_score = score_by_team[forecast.home_team_id]
            away_score = score_by_team[forecast.away_team_id]
            game = self.session.get(Game, forecast.canonical_game_id)
            if game is None or game.source_mode != "RESEARCH_SHADOW":
                raise NFLShadowOddsIdentityError("Mapped NFL final is not a research-shadow game")
            if game.status == GameStatus.FINAL:
                if game.home_score != home_score or game.away_score != away_score:
                    raise NFLShadowOddsIdentityError(
                        f"Provider final conflicts with persisted score for {event.source_event_id}"
                    )
                reused += 1
                continue
            observed_at = _aware(event.last_update or available_as_of)
            evidence = {
                "provider_event_id": event.source_event_id,
                "canonical_event_id": forecast.event_id,
                "scheduled_start_utc": _aware(event.commence_time).isoformat(),
                "home_team_id": forecast.home_team_id,
                "away_team_id": forecast.away_team_id,
                "home_score": home_score,
                "away_score": away_score,
                "completed": True,
                "provider_last_update": observed_at.isoformat(),
            }
            checksum = canonical_sha256(evidence)
            observation = self.session.scalar(select(GameStateObservation).where(
                GameStateObservation.game_id == game.id,
                GameStateObservation.source == "THE_ODDS_API",
                GameStateObservation.source_mode == "RESEARCH_SHADOW",
                GameStateObservation.payload_checksum == checksum,
            ))
            if observation is None:
                sides = {row.is_home: row for row in self.session.scalars(
                    select(GameTeam).where(GameTeam.game_id == game.id)
                )}
                if set(sides) != {False, True}:
                    raise NFLShadowOddsIdentityError("Mapped NFL game lacks exact home/away rows")
                sides[True].score = home_score
                sides[False].score = away_score
                self.session.add(GameStateObservation(
                    game_id=game.id,
                    home_team_id=sides[True].team_id,
                    away_team_id=sides[False].team_id,
                    scheduled_at=game.starts_at,
                    status=GameStatus.FINAL.value,
                    home_score=home_score,
                    away_score=away_score,
                    final_at=observed_at,
                    observed_at=observed_at,
                    available_as_of=_aware(available_as_of),
                    source="THE_ODDS_API",
                    source_mode="RESEARCH_SHADOW",
                    source_record_id=event.source_event_id,
                    payload_checksum=checksum,
                ))
            game.status = GameStatus.FINAL
            game.home_score = home_score
            game.away_score = away_score
            game.final_at = observed_at
            finals += 1
        self.session.flush()
        return {
            "events": len(events), "mapped_events": mapped,
            "final_games": finals, "reused_finals": reused,
            "incomplete_games": incomplete, "rejected_events": rejected,
        }

    @staticmethod
    def _validate_score_identity(forecast: ShadowForecast, event: OddsScoreEventDTO) -> None:
        home_id = NFL_ODDS_TEAM_IDENTITIES.get(event.home_team)
        away_id = NFL_ODDS_TEAM_IDENTITIES.get(event.away_team)
        if home_id != forecast.home_team_id or away_id != forecast.away_team_id:
            raise NFLShadowOddsIdentityError(
                f"Provider final {event.source_event_id} conflicts with exact NFL sides"
            )
        if (
            forecast.scheduled_start_utc is None
            or _aware(forecast.scheduled_start_utc).replace(microsecond=0)
            != _aware(event.commence_time).replace(microsecond=0)
        ):
            raise NFLShadowOddsIdentityError(
                f"Provider final {event.source_event_id} conflicts with exact NFL kickoff"
            )

    def _map_event(self, event: OddsEventDTO | OddsScoreEventDTO) -> ShadowForecast | None:
        home_id = NFL_ODDS_TEAM_IDENTITIES.get(event.home_team)
        away_id = NFL_ODDS_TEAM_IDENTITIES.get(event.away_team)
        if home_id is None or away_id is None:
            return None
        kickoff = _aware(event.commence_time)
        candidates = list(self.session.scalars(
            select(ShadowForecast)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(
                ShadowForecastBundle.sport == "NFL",
                ShadowForecast.home_team_id == home_id,
                ShadowForecast.away_team_id == away_id,
                ShadowForecast.engine_id == "NFL_M1_XGBOOST",
            )
        ))
        candidates = [
            row for row in candidates
            if row.scheduled_start_utc is not None
            and _aware(row.scheduled_start_utc).replace(microsecond=0)
            == kickoff.replace(microsecond=0)
        ]
        game_ids = {row.canonical_game_id for row in candidates if row.canonical_game_id is not None}
        if len(game_ids) != 1:
            return None
        canonical_game_id = next(iter(game_ids))
        mapping = self.session.scalar(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == "game",
            ExternalIdMapping.source == self.source,
            ExternalIdMapping.external_id == event.source_event_id,
        ))
        if mapping is not None and mapping.entity_id != canonical_game_id:
            raise NFLShadowOddsIdentityError(
                f"Odds event {event.source_event_id} conflicts with its persisted NFL game mapping"
            )
        if mapping is None:
            self.session.add(ExternalIdMapping(
                entity_type="game", entity_id=canonical_game_id,
                source=self.source, external_id=event.source_event_id,
            ))
            self.session.flush()
        return max(candidates, key=lambda row: (row.snapshot_version, row.available_as_of_utc or row.created_at))

    def _ingest_event(
        self, forecast: ShadowForecast, event: OddsEventDTO, available_as_of: datetime
    ) -> tuple[int, int]:
        game_id = forecast.canonical_game_id
        if game_id is None:
            raise NFLShadowOddsIdentityError("Mapped NFL forecast has no canonical game identity")
        sides = {row.is_home: row.team_id for row in self.session.scalars(
            select(GameTeam).where(GameTeam.game_id == game_id)
        )}
        created = existing = 0
        first_observed = self.session.scalar(select(OddsSnapshot.id).where(
            OddsSnapshot.source == self.source,
            OddsSnapshot.source_event_id == event.source_event_id,
        ).limit(1)) is None
        snapshot_state = _checkpoint_state(
            first_observed=first_observed,
            available_as_of=_aware(available_as_of),
            kickoff=_aware(forecast.scheduled_start_utc),
        )
        provider = provider_identity(self.source)
        for book_dto in event.bookmakers:
            # Namespaced by provider, exactly as the canonical normalizer does.
            book_key = provider.bookmaker_key(book_dto.key)
            book_name = (
                book_dto.title if provider.governed
                else f"{book_dto.title} ({provider.display_name}, research)"
            )
            book = self.session.scalar(select(Sportsbook).where(Sportsbook.key == book_key))
            if book is None:
                book = Sportsbook(key=book_key, name=book_name, region="us", active=True)
                self.session.add(book)
                self.session.flush()
            for market_dto in book_dto.markets:
                market_type = {
                    "h2h": MarketType.MONEYLINE,
                    "spreads": MarketType.SPREAD,
                    "totals": MarketType.TOTAL,
                }.get(market_dto.key)
                if market_type is None:
                    continue
                market = self.session.scalar(select(Market).where(
                    Market.game_id == game_id,
                    Market.market_type == market_type,
                    Market.period == "full_game",
                ))
                if market is None:
                    market = Market(game_id=game_id, market_type=market_type, period="full_game")
                    self.session.add(market)
                    self.session.flush()
                for item in market_dto.outcomes:
                    side, participant = self._outcome_identity(
                        item.name, event=event, market_type=market_type, sides=sides
                    )
                    if side is None:
                        continue
                    outcome_key = f"{side}:{item.point}"
                    outcome = self.session.scalar(select(MarketOutcome).where(
                        MarketOutcome.market_id == market.id,
                        MarketOutcome.outcome_key == outcome_key,
                    ))
                    if outcome is None:
                        outcome = MarketOutcome(
                            market_id=market.id, outcome_key=outcome_key,
                            label=side, participant_team_id=participant,
                        )
                        self.session.add(outcome)
                        self.session.flush()
                    duplicate = self.session.scalar(select(OddsSnapshot.id).where(
                        OddsSnapshot.market_outcome_id == outcome.id,
                        OddsSnapshot.sportsbook_id == book.id,
                        OddsSnapshot.observed_at == market_dto.last_update,
                        OddsSnapshot.american_odds == item.price,
                        OddsSnapshot.point == item.point,
                        OddsSnapshot.source == self.source,
                    ).limit(1))
                    if duplicate is not None:
                        existing += 1
                        continue
                    raw = {
                        **item.model_dump(mode="json"),
                        "provider_event_id": event.source_event_id,
                        "identity_mapping": {
                            "canonical_event_id": forecast.event_id,
                            "home_team_id": forecast.home_team_id,
                            "away_team_id": forecast.away_team_id,
                            "scheduled_start_utc": _aware(forecast.scheduled_start_utc).isoformat(),
                        },
                        "snapshot_state": snapshot_state,
                    }
                    self.session.add(OddsSnapshot(
                        market_outcome_id=outcome.id,
                        sportsbook_id=book.id,
                        observed_at=_aware(market_dto.last_update),
                        available_as_of=_aware(available_as_of),
                        american_odds=item.price,
                        decimal_odds=american_to_decimal(item.price),
                        point=item.point,
                        implied_probability=implied_probability(item.price),
                        source=self.source,
                        source_event_id=event.source_event_id,
                        source_mode="LIVE",
                        raw_payload=raw,
                        payload_checksum=canonical_sha256(raw),
                    ))
                    created += 1
        return created, existing

    @staticmethod
    def _outcome_identity(name: str, *, event, market_type, sides):
        if market_type in {MarketType.MONEYLINE, MarketType.SPREAD}:
            canonical = NFL_ODDS_TEAM_IDENTITIES.get(name)
            if canonical == NFL_ODDS_TEAM_IDENTITIES.get(event.home_team):
                return "HOME", sides.get(True)
            if canonical == NFL_ODDS_TEAM_IDENTITIES.get(event.away_team):
                return "AWAY", sides.get(False)
            return None, None
        normalized = name.strip().upper()
        if normalized in {"OVER", "UNDER"}:
            return normalized, None
        return None, None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _checkpoint_state(
    *, first_observed: bool, available_as_of: datetime, kickoff: datetime
) -> str:
    if first_observed:
        return "FIRST_OBSERVED"
    remaining = _aware(kickoff) - _aware(available_as_of)
    if timedelta(minutes=15) <= remaining <= timedelta(minutes=30):
        return "FINAL_PREGAME"
    if timedelta(minutes=30) < remaining <= timedelta(minutes=75):
        return "1H"
    if timedelta(minutes=75) < remaining <= timedelta(hours=4, minutes=15):
        return "4H"
    if timedelta(hours=4, minutes=15) < remaining <= timedelta(hours=25):
        return "24H"
    return "CURRENT"


__all__ = [
    "NFL_ODDS_TEAM_IDENTITIES", "NFLShadowOddsIdentityError", "NFLShadowOddsService",
    "_checkpoint_state",
]
