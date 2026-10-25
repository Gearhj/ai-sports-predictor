"""Exact-identity NFL odds ingestion for prospective research shadow.

The Odds API exposes provider event IDs and franchise display names, while the
frozen NFL schedule uses nflverse IDs.  A small explicit reviewed franchise map
plus exact home/away direction and kickoff establishes the mapping once.  Every
subsequent quote joins through the persisted provider event mapping; fuzzy name
matching is never used.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, DataError
from sqlalchemy.orm import Session

from app.data_sources.base import OddsEventDTO, OddsScoreEventDTO
from app.forecast_exchange.pricing import american_to_decimal, implied_probability
from app.data_sources.provider_registry import identity as provider_identity
from app.gen2.exchange.canonical_json import canonical_sha256
from app.services.event_time_reconciliation import reconcile_clock_identity, assess_clock, make_binding, utc, SLATE_TIMEZONE
from app.models import (
    ExternalIdMapping, Game, GameStateObservation, GameStatus, GameTeam, Market,
    MarketOutcome, MarketType, OddsSnapshot, ShadowForecast, ShadowForecastBundle,
    Sportsbook, League, Team,
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

    def ingest(self, events: list[OddsEventDTO], *, available_as_of: datetime,
               game_ids: set[str] | None = None) -> dict[str, Any]:
        mapped_events = quotes = duplicates = rejected = out_of_scope = 0
        bound: set[str] = set()
        receipts=[]
        claims={}
        for event in events:
            gid=self._resolve_game_id(event)
            if gid is not None:claims.setdefault(str(gid),[]).append(event)
        for event in events:
            resolved=self._resolve_game_id(event)
            if resolved is not None and len(claims[str(resolved)])!=1:
                rejected+=1;receipts.append({"provider_event_id":event.source_event_id,"reason":"MORE_THAN_ONE_EVENT_CLAIMS_GAME"});continue
            if game_ids is not None and resolved is not None and str(resolved) not in game_ids:
                out_of_scope+=1;continue
            try:
                with self.session.begin_nested():
                    forecast=self._map_event(event)
                    if forecast is None:
                        rejected+=1;receipts.append({"provider_event_id":event.source_event_id,"reason":"EXACT_EVENT_IDENTITY_UNRESOLVED"});continue
                    created,existing=self._ingest_event(forecast,event,available_as_of)
                mapped_events+=1;quotes+=created;duplicates+=existing;bound.add(str(forecast.canonical_game_id))
                receipts.append({"provider_event_id":event.source_event_id,"game_id":str(forecast.canonical_game_id),"reason":"BOUND","created":created,"existing":existing})
            except (NFLShadowOddsIdentityError, ValueError, TypeError, KeyError, IntegrityError, DataError) as error:
                rejected+=1;receipts.append({"provider_event_id":event.source_event_id,"reason":"EVENT_REFUSED","error_type":type(error).__name__,"detail":str(error)[:240]})
        self.session.flush()
        result={"events":len(events),"mapped_events":mapped_events,"quotes":quotes,"duplicates":duplicates,"rejected_events":rejected,"event_receipts":receipts}
        if game_ids is not None:result.update(out_of_scope_events=out_of_scope,bound_game_ids=sorted(bound))
        return result

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
        receipts=[]
        for event in events:
            before=(mapped,finals,reused,incomplete,rejected)
            try:
                with self.session.begin_nested():
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
                "forecast_scheduled_start_utc": _aware(forecast.scheduled_start_utc).isoformat(),
                "canonical_scheduled_start_utc": _aware(game.starts_at).isoformat(),
                "event_clock_reconciliation": reconcile_clock_identity(_aware(game.starts_at),event.commence_time).receipt(),
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
            except (NFLShadowOddsIdentityError, ValueError, TypeError, KeyError, IntegrityError, DataError) as error:
                mapped,finals,reused,incomplete,rejected=before
                rejected+=1
                receipts.append({"provider_event_id":event.source_event_id,"reason":"FINAL_EVENT_REFUSED","error_type":type(error).__name__,"detail":str(error)[:240]})
        self.session.flush()
        return {
            "events": len(events), "mapped_events": mapped,
            "final_games": finals, "reused_finals": reused,
            "incomplete_games": incomplete, "rejected_events": rejected, "event_receipts":receipts,
        }

    @staticmethod
    def _validate_score_identity(forecast: ShadowForecast, event: OddsScoreEventDTO) -> None:
        home_id = NFL_ODDS_TEAM_IDENTITIES.get(event.home_team)
        away_id = NFL_ODDS_TEAM_IDENTITIES.get(event.away_team)
        if home_id != forecast.home_team_id or away_id != forecast.away_team_id:
            raise NFLShadowOddsIdentityError(
                f"Provider final {event.source_event_id} conflicts with exact NFL sides"
            )
        if forecast.scheduled_start_utc is None or not reconcile_clock_identity(
            _aware(forecast.scheduled_start_utc),event.commence_time).accepted:
            raise NFLShadowOddsIdentityError(
                f"Provider final {event.source_event_id} conflicts with exact NFL kickoff"
            )

    def _resolve_game_id(self, event: OddsEventDTO | OddsScoreEventDTO):
        """The ONE canonical game this event binds to by exact identity, else None.

        The same rule `_map_event` applies (franchise map, home/away direction,
        kickoff to the second, a unique game), asked without writing anything.
        """

        candidates = self._identity_candidates(event)
        if candidates is None:
            return None
        game_ids = {row.canonical_game_id for row in candidates if row.canonical_game_id is not None}
        return next(iter(game_ids)) if len(game_ids) == 1 else None

    def _identity_candidates(self, event: OddsEventDTO | OddsScoreEventDTO):
        home_id = NFL_ODDS_TEAM_IDENTITIES.get(event.home_team)
        away_id = NFL_ODDS_TEAM_IDENTITIES.get(event.away_team)
        if home_id is None or away_id is None or home_id == away_id:
            return None
        try:
            kickoff = utc(event.commence_time)
        except (ValueError,TypeError):
            return None
        slate_day = kickoff.astimezone(SLATE_TIMEZONE).date()
        day_start = datetime(slate_day.year,slate_day.month,slate_day.day,tzinfo=SLATE_TIMEZONE)
        day_end = day_start+timedelta(days=1)
        same_day = {}
        # Occurrence uniqueness is canonical, independent of forecast coverage.
        # A same-day game without a forecast must not be hidden from this guard.
        games = self.session.scalars(select(Game).join(League,League.id==Game.league_id)
            .where(League.code=="NFL",Game.starts_at>=day_start.astimezone(UTC),
                   Game.starts_at<day_end.astimezone(UTC))).all()
        for game in games:
            side_rows = self.session.execute(select(GameTeam.is_home,Team.abbreviation)
                .join(Team,Team.id==GameTeam.team_id).where(GameTeam.game_id==game.id,
                                                          Team.league_id==game.league_id)).all()
            if len(side_rows)!=2 or dict(side_rows)!={True:home_id,False:away_id}:
                continue
            same_day[game.id]=game
        # Distinct same-day occurrences never resolve by nearest clock, even >12h apart.
        if len(same_day) != 1:
            return []
        game = next(iter(same_day.values()))
        canonical = _aware(game.starts_at)
        if not reconcile_clock_identity(canonical,kickoff).accepted:
            return []
        candidates = self.session.scalars(select(ShadowForecast)
            .join(ShadowForecastBundle,ShadowForecastBundle.id==ShadowForecast.bundle_id)
            .where(ShadowForecastBundle.sport=="NFL",ShadowForecast.canonical_game_id==game.id,
                   ShadowForecast.home_team_id==home_id,ShadowForecast.away_team_id==away_id,
                   ShadowForecast.engine_id=="NFL_M1_XGBOOST")).all()
        return [row for row in candidates if row.scheduled_start_utc is not None
                and reconcile_clock_identity(_aware(row.scheduled_start_utc),kickoff).accepted
                and reconcile_clock_identity(canonical,_aware(row.scheduled_start_utc)).accepted]

    def _map_event(self, event: OddsEventDTO | OddsScoreEventDTO) -> ShadowForecast | None:
        candidates = self._identity_candidates(event)
        if candidates is None:
            return None
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
        other_event = self.session.scalar(select(ExternalIdMapping.external_id).where(
            ExternalIdMapping.entity_type=="game",ExternalIdMapping.source==self.source,
            ExternalIdMapping.entity_id==canonical_game_id,ExternalIdMapping.external_id!=event.source_event_id))
        if other_event is not None:
            raise NFLShadowOddsIdentityError("NFL game already maps to another event in this provider namespace")
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
        game = self.session.get(Game,game_id)
        if game is None or game.status != GameStatus.SCHEDULED or set(sides)!={True,False}:
            raise NFLShadowOddsIdentityError("NFL quote is not a scheduled fully bound canonical game")
        clock = assess_clock(_aware(game.starts_at),event.commence_time,available_as_of)
        source_clock = reconcile_clock_identity(_aware(game.starts_at),_aware(forecast.scheduled_start_utc))
        if not clock.accepted or not source_clock.accepted or utc(available_as_of)>=min(clock.earliest_start,source_clock.earliest_start):
            raise NFLShadowOddsIdentityError("NFL quote is outside its original pregame event clocks")
        binding = make_binding(game_id=game_id,sport="NFL",home_id=sides[True],away_id=sides[False],
            provider_event_id=event.source_event_id,canonical_start=_aware(game.starts_at),
            provider_start=event.commence_time,received_at=available_as_of)
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
                        OddsSnapshot.raw_payload["event_commence_time"].as_string() == utc(event.commence_time).isoformat(),
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
                        "event_commence_time": utc(event.commence_time).isoformat(),
                        "response_received_at": utc(available_as_of).isoformat(),
                        "side": side,
                        "event_clock_binding": binding,
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
