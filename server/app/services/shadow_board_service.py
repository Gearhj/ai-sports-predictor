"""Persist, evaluate, and settle the Model Studio research-shadow board.

Nothing here is executable. Imported forecasts are RESEARCH_SHADOW / NOT_PROMOTED
/ NOT_EXECUTABLE. Quotes are never fabricated: a decision is priced only when a
real, fresh, complete two-sided governed quote is supplied; otherwise it is a
NO_BET with a zero virtual stake. Virtual staking uses the existing bankroll
math under quarter-Kelly with position, same-game, and daily exposure caps.
Real-money execution is impossible by construction.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Iterable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bankroll.service import SettlementResult, StakePolicy, StakingStrategy, calculate_stake, settle_profit
from app.core.config import Settings
from app.forecast_exchange.contracts import GameForecastExportV1
from app.forecast_exchange.importer import (
    ImportedForecastBundle,
    ImportedSportsForecastBundle,
    verify_and_load_any_bundle,
)
from app.forecast_exchange.sports_contracts import SportsGameForecastV1
from app.forecast_exchange.pricing import (
    Recommendation,
    ShadowMarket,
    ShadowPricingConfig,
    american_to_decimal,
    implied_probability,
    model_probability,
    price_selection,
)
from app.models import (
    ExternalIdMapping,
    Game,
    GameStatus,
    GameTeam,
    League,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    Season,
    ShadowBoardDecision,
    ShadowDecisionSettlement,
    ShadowForecast,
    ShadowForecastBundle,
    Sport,
    Sportsbook,
    Team,
)


# The initially supported market/selection surface. "line_dependent" markets
# require a governed line (from the quote); without one no probability is stated.
@dataclass(frozen=True)
class SelectionSpec:
    market: ShadowMarket
    selection: str
    line_dependent: bool


SUPPORTED_SELECTIONS: tuple[SelectionSpec, ...] = (
    SelectionSpec(ShadowMarket.FULL_GAME_MONEYLINE, "HOME", False),
    SelectionSpec(ShadowMarket.FULL_GAME_MONEYLINE, "AWAY", False),
    SelectionSpec(ShadowMarket.FIRST_FIVE_MONEYLINE, "HOME", False),
    SelectionSpec(ShadowMarket.FIRST_FIVE_MONEYLINE, "AWAY", False),
    SelectionSpec(ShadowMarket.RUN_LINE_1_5, "HOME_-1.5", False),
    SelectionSpec(ShadowMarket.RUN_LINE_1_5, "AWAY_+1.5", False),
    SelectionSpec(ShadowMarket.FULL_GAME_TOTAL, "OVER", True),
    SelectionSpec(ShadowMarket.FULL_GAME_TOTAL, "UNDER", True),
    SelectionSpec(ShadowMarket.FIRST_FIVE_TOTAL, "OVER", True),
    SelectionSpec(ShadowMarket.FIRST_FIVE_TOTAL, "UNDER", True),
    SelectionSpec(ShadowMarket.TEAM_TOTAL, "HOME_OVER", True),
    SelectionSpec(ShadowMarket.TEAM_TOTAL, "HOME_UNDER", True),
    SelectionSpec(ShadowMarket.TEAM_TOTAL, "AWAY_OVER", True),
    SelectionSpec(ShadowMarket.TEAM_TOTAL, "AWAY_UNDER", True),
)

NFL_SUPPORTED_SELECTIONS: tuple[SelectionSpec, ...] = (
    SelectionSpec(ShadowMarket.FULL_GAME_MONEYLINE, "HOME", False),
    SelectionSpec(ShadowMarket.FULL_GAME_MONEYLINE, "AWAY", False),
    SelectionSpec(ShadowMarket.FULL_GAME_SPREAD, "HOME", True),
    SelectionSpec(ShadowMarket.FULL_GAME_SPREAD, "AWAY", True),
    SelectionSpec(ShadowMarket.FULL_GAME_TOTAL, "OVER", True),
    SelectionSpec(ShadowMarket.FULL_GAME_TOTAL, "UNDER", True),
)


@dataclass(frozen=True)
class GovernedQuote:
    """A real, complete two-sided quote. ``book_prices`` feeds best-line search."""

    book: str
    side_american: int
    opposite_american: int
    observed_at: datetime
    line: float | None = None
    book_prices: tuple[tuple[str, int], ...] = ()
    side_snapshot_id: object | None = None
    opposite_snapshot_id: object | None = None
    source_event_id: str | None = None
    available_as_of: datetime | None = None

    def best_american(self) -> int:
        candidates = list(self.book_prices) or [(self.book, self.side_american)]
        return max(candidates, key=lambda item: american_to_decimal(item[1]))[1]

    def best_book(self) -> str:
        candidates = list(self.book_prices) or [(self.book, self.side_american)]
        return max(candidates, key=lambda item: american_to_decimal(item[1]))[0]


class QuoteSource(Protocol):
    def quote_for(
        self, forecast: ShadowForecast, market: str, selection: str, *, as_of: datetime
    ) -> GovernedQuote | None: ...


class LiveOddsQuoteSource:
    """Resolve governed two-sided quotes from persisted odds (existing refresh-odds).

    Governed quotes are matched from ``odds_snapshots`` on ``source_event_id``.
    Historical research-shadow events have no canonical live event mapping, so
    they resolve to ``None``.  Prospective NFL rows bind a canonical ``Game`` at
    import using exact nflverse IDs; odds are joined only through that key.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def quote_for(
        self, forecast: ShadowForecast, market: str, selection: str, *, as_of: datetime
    ) -> GovernedQuote | None:
        if forecast.canonical_game_id is None or forecast.scheduled_start_utc is None:
            return None
        as_of = _aware(as_of)
        kickoff = _aware(forecast.scheduled_start_utc)
        market_type = {
            ShadowMarket.FULL_GAME_MONEYLINE.value: MarketType.MONEYLINE,
            ShadowMarket.FULL_GAME_SPREAD.value: MarketType.SPREAD,
            ShadowMarket.FULL_GAME_TOTAL.value: MarketType.TOTAL,
        }.get(market)
        if market_type is None:
            return None
        rows = list(self.session.execute(
            select(OddsSnapshot, MarketOutcome, Sportsbook)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
            .where(
                Market.game_id == forecast.canonical_game_id,
                Market.market_type == market_type,
                Market.period == "full_game",
                OddsSnapshot.source_mode == "LIVE",
                OddsSnapshot.available_as_of <= as_of,
                OddsSnapshot.available_as_of < kickoff,
            )
            .order_by(OddsSnapshot.available_as_of.desc())
        ))
        if not rows:
            return None
        sides = list(self.session.execute(
            select(GameTeam).where(GameTeam.game_id == forecast.canonical_game_id)
        ))
        team_by_home = {row[0].is_home: row[0].team_id for row in sides}

        def side_key(outcome: MarketOutcome, snapshot: OddsSnapshot) -> str | None:
            if market_type in {MarketType.MONEYLINE, MarketType.SPREAD}:
                if outcome.participant_team_id == team_by_home.get(True):
                    return "HOME"
                if outcome.participant_team_id == team_by_home.get(False):
                    return "AWAY"
                return None
            label = outcome.label.strip().upper()
            if label.startswith("OVER"):
                return "OVER"
            if label.startswith("UNDER"):
                return "UNDER"
            return None

        by_observation: dict[tuple[object, datetime, float | None], dict[str, tuple]] = {}
        for snapshot, outcome, book in rows:
            key_side = side_key(outcome, snapshot)
            if key_side is None or snapshot.american_odds is None:
                continue
            line_key = abs(snapshot.point) if market_type == MarketType.SPREAD and snapshot.point is not None else snapshot.point
            key = (snapshot.sportsbook_id, snapshot.observed_at, line_key)
            by_observation.setdefault(key, {})[key_side] = (snapshot, outcome, book)
        opposite = {"HOME": "AWAY", "AWAY": "HOME", "OVER": "UNDER", "UNDER": "OVER"}.get(selection)
        if opposite is None:
            return None
        candidates: list[tuple[OddsSnapshot, OddsSnapshot, Sportsbook]] = []
        for paired in by_observation.values():
            if selection in paired and opposite in paired:
                side_snapshot, _, book = paired[selection]
                opposite_snapshot, _, _ = paired[opposite]
                candidates.append((side_snapshot, opposite_snapshot, book))
        if not candidates:
            return None
        # Only compare currently available prices from the latest complete
        # source observation at each book.  Best-line selection never combines
        # opposite sides from different books or timestamps.
        latest_by_book: dict[object, tuple[OddsSnapshot, OddsSnapshot, Sportsbook]] = {}
        for candidate in candidates:
            current = latest_by_book.get(candidate[0].sportsbook_id)
            if current is None or candidate[0].available_as_of > current[0].available_as_of:
                latest_by_book[candidate[0].sportsbook_id] = candidate
        best = max(
            latest_by_book.values(),
            key=lambda item: american_to_decimal(int(item[0].american_odds)),
        )
        side_snapshot, opposite_snapshot, book = best
        return GovernedQuote(
            book=book.name,
            side_american=int(side_snapshot.american_odds),
            opposite_american=int(opposite_snapshot.american_odds),
            observed_at=side_snapshot.observed_at,
            line=side_snapshot.point,
            book_prices=tuple(
                (candidate[2].name, int(candidate[0].american_odds))
                for candidate in latest_by_book.values()
            ),
            side_snapshot_id=side_snapshot.id,
            opposite_snapshot_id=opposite_snapshot.id,
            source_event_id=side_snapshot.source_event_id,
            available_as_of=side_snapshot.available_as_of,
        )


class MappingQuoteSource:
    """Explicit dict-backed quote source. Test odds are synthetic and labeled."""

    def __init__(self, quotes: dict[tuple[str, str, str], GovernedQuote]) -> None:
        self._quotes = quotes

    def quote_for(
        self, forecast: ShadowForecast, market: str, selection: str, *, as_of: datetime
    ) -> GovernedQuote | None:
        return self._quotes.get((forecast.event_id, market, selection))


@dataclass
class _Exposure:
    per_game: dict[str, float] = field(default_factory=dict)
    daily: float = 0.0


class ShadowBoardService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- import ----------------------------------------------------------
    def import_bundle(self, bundle_dir) -> dict:
        imported = verify_and_load_any_bundle(bundle_dir)
        existing = self.session.scalar(
            select(ShadowForecastBundle).where(
                ShadowForecastBundle.bundle_sha256 == imported.bundle_sha256
            )
        )
        if existing is not None:
            return {
                "bundle_sha256": existing.bundle_sha256,
                "bundle_id": existing.bundle_id,
                "slate_date": existing.slate_date.isoformat(),
                "forecasts": existing.forecast_count,
                "idempotent_reuse": True,
            }
        return self._persist_bundle(imported)

    def _persist_bundle(
        self, imported: ImportedForecastBundle | ImportedSportsForecastBundle
    ) -> dict:
        bundle = imported.bundle
        row = ShadowForecastBundle(
            bundle_sha256=imported.bundle_sha256,
            contract_version=bundle.schema_version,
            bundle_id=imported.bundle_id,
            logical_uri=imported.logical_uri,
            source_fixture_id=imported.fixture.fixture_id,
            slate_date=bundle.slate_date,
            league=bundle.league,
            sport=bundle.sport,
            status=bundle.status,
            research_shadow_only=bundle.research_shadow_only,
            generated_at=bundle.generated_at_utc,
            forecast_count=len(bundle.forecasts),
            quote_count=len(getattr(bundle, "market_quotes", [])),
            candidate_count=len(getattr(bundle, "bet_candidates", [])),
        )
        self.session.add(row)
        self.session.flush()
        created_children = reused_children = 0
        for forecast in bundle.forecasts:
            existing_forecast = self.session.scalar(select(ShadowForecast).where(
                ShadowForecast.forecast_id == forecast.forecast_id
            ))
            if existing_forecast is not None:
                self._validate_reused_forecast_child(existing_forecast, forecast)
                reused_children += 1
                continue
            self._validate_persisted_identity(forecast)
            self.session.add(self._forecast_row(row.id, forecast))
            created_children += 1
        self.session.commit()
        return {
            "bundle_sha256": imported.bundle_sha256,
            "bundle_id": imported.bundle_id,
            "slate_date": bundle.slate_date.isoformat(),
            "forecasts": len(bundle.forecasts),
            "engines": sorted({f.engine.engine_id for f in bundle.forecasts}),
            "quotes": len(getattr(bundle, "market_quotes", [])),
            "candidates": len(getattr(bundle, "bet_candidates", [])),
            "forecast_rows_created": created_children,
            "forecast_children_reused": reused_children,
            "idempotent_reuse": False,
        }

    @staticmethod
    def _validate_reused_forecast_child(
        existing: ShadowForecast,
        forecast: GameForecastExportV1 | SportsGameForecastV1,
    ) -> None:
        """Allow one content-addressed child to appear in multiple containers."""

        expected = (
            forecast.event_id,
            forecast.engine.engine_id,
            forecast.home_team.team_id,
            forecast.away_team.team_id,
            forecast.source_event_id,
        )
        actual = (
            existing.event_id, existing.engine_id, existing.home_team_id,
            existing.away_team_id, existing.source_event_id,
        )
        if actual != expected:
            raise ValueError(f"Forecast child {forecast.forecast_id} conflicts with persisted identity")
        if isinstance(forecast, SportsGameForecastV1):
            if (
                existing.model_manifest_sha256 != forecast.engine.model_manifest_sha256
                or existing.model_artifact_sha256 != forecast.engine.model_binary_sha256
                or existing.feature_vector_sha256 != forecast.features.feature_vector_sha256
                or existing.snapshot_type != forecast.snapshot_type
                or existing.snapshot_version != forecast.snapshot_version
            ):
                raise ValueError(f"Forecast child {forecast.forecast_id} conflicts with persisted lineage")

    def _forecast_row(
        self, bundle_pk, forecast: GameForecastExportV1 | SportsGameForecastV1
    ) -> ShadowForecast:
        if isinstance(forecast, SportsGameForecastV1):
            return self._sports_forecast_row(bundle_pk, forecast)
        return ShadowForecast(
            bundle_id=bundle_pk,
            forecast_id=forecast.forecast_id,
            forecast_schema_version=forecast.schema_version,
            snapshot_type="HISTORICAL",
            snapshot_version=1,
            engine_id=forecast.engine.engine_id,
            engine_version=forecast.engine.engine_version,
            model_artifact_sha256=forecast.engine.model_artifact_sha256,
            simulator_id=forecast.simulator.simulator_id,
            simulator_version=forecast.simulator.simulator_version,
            simulator_seed=forecast.simulator.seed,
            event_id=forecast.event_id,
            source_event_id=forecast.source_event_id,
            event_date=forecast.event_date,
            season=forecast.season,
            home_team_id=forecast.home_team.team_id,
            away_team_id=forecast.away_team.team_id,
            is_ready=forecast.readiness.is_ready,
            readiness_score=forecast.readiness.score,
            readiness_threshold=forecast.readiness.threshold,
            status=forecast.status,
            scheduled_start_utc=forecast.scheduled_start_utc,
            forecast_cutoff_utc=forecast.forecast_cutoff_utc,
            full_home_win_probability=forecast.full_game.home_win_probability,
            full_away_win_probability=forecast.full_game.away_win_probability,
            full_expected_home_score=forecast.full_game.expected_home_score,
            full_expected_away_score=forecast.full_game.expected_away_score,
            f5_home_win_probability=forecast.first_five.home_win_probability,
            f5_away_win_probability=forecast.first_five.away_win_probability,
            f5_expected_home_score=forecast.first_five.expected_home_score,
            f5_expected_away_score=forecast.first_five.expected_away_score,
            distributions={
                "full_game": forecast.full_game.model_dump(mode="json"),
                "first_five": forecast.first_five.model_dump(mode="json"),
            },
            uncertainty=forecast.uncertainty.model_dump(mode="json"),
            readiness_detail=forecast.readiness.model_dump(mode="json"),
        )

    def _sports_forecast_row(self, bundle_pk, forecast: SportsGameForecastV1) -> ShadowForecast:
        canonical_game_id = self._ensure_nfl_game_identity(forecast)
        return ShadowForecast(
            bundle_id=bundle_pk,
            canonical_game_id=canonical_game_id,
            forecast_id=forecast.forecast_id,
            forecast_schema_version=forecast.schema_version,
            snapshot_type=forecast.snapshot_type,
            snapshot_version=forecast.snapshot_version,
            engine_id=forecast.engine.engine_id,
            engine_version=forecast.engine.engine_version,
            model_artifact_sha256=forecast.engine.model_binary_sha256,
            model_manifest_sha256=forecast.engine.model_manifest_sha256,
            feature_vector_sha256=forecast.features.feature_vector_sha256,
            simulator_id=forecast.simulator.simulator_id,
            simulator_version=forecast.simulator.simulator_version,
            simulator_seed=forecast.simulator.seed,
            event_id=forecast.event_id,
            source_event_id=forecast.source_event_id,
            event_date=forecast.event_date,
            season=forecast.season,
            season_type=forecast.season_type,
            week=forecast.week,
            home_team_id=forecast.home_team.team_id,
            away_team_id=forecast.away_team.team_id,
            is_ready=forecast.readiness.is_ready,
            readiness_score=forecast.readiness.score,
            readiness_threshold=forecast.readiness.threshold,
            status=forecast.status,
            scheduled_start_utc=forecast.scheduled_start_utc,
            schedule_as_of_utc=forecast.schedule_as_of_utc,
            source_cutoff_utc=forecast.source_cutoff_utc,
            forecast_cutoff_utc=forecast.forecast_cutoff_utc,
            available_as_of_utc=forecast.available_as_of_utc,
            full_home_win_probability=forecast.home_win_probability,
            full_away_win_probability=forecast.away_win_probability,
            full_expected_home_score=forecast.expected_home_score,
            full_expected_away_score=forecast.expected_away_score,
            f5_home_win_probability=None,
            f5_away_win_probability=None,
            f5_expected_home_score=None,
            f5_expected_away_score=None,
            distributions={
                "joint_score_distribution": [row.model_dump(mode="json") for row in forecast.joint_score_distribution],
                "score_margin_distribution": [row.model_dump(mode="json") for row in forecast.score_margin_distribution],
                "total_score_distribution": [row.model_dump(mode="json") for row in forecast.total_score_distribution],
                "spread_cover_probabilities": [row.model_dump(mode="json") for row in forecast.spread_cover_probabilities],
                "over_under_probabilities": [row.model_dump(mode="json") for row in forecast.over_under_probabilities],
                "most_likely_scores": [row.model_dump(mode="json") for row in forecast.most_likely_scores],
                "prediction_interval": forecast.prediction_interval.model_dump(mode="json"),
                "tie_probability": forecast.tie_probability,
            },
            uncertainty=forecast.uncertainty.model_dump(mode="json"),
            readiness_detail={
                **forecast.readiness.model_dump(mode="json"),
                "features": forecast.features.model_dump(mode="json"),
            },
            identity_evidence={
                "event_id": forecast.event_id,
                "source_event_id": forecast.source_event_id,
                "home_team": forecast.home_team.model_dump(mode="json"),
                "away_team": forecast.away_team.model_dump(mode="json"),
            },
            lineage_detail={
                "candidate_id": forecast.engine.candidate_id,
                "feature_tier": forecast.engine.feature_tier,
                "feature_contract_sha256": forecast.engine.feature_contract_sha256,
                "training_cutoff": forecast.engine.training_cutoff.isoformat(),
                "training_dataset_version": forecast.engine.training_dataset_version,
                "training_dataset_manifest_sha256": forecast.engine.training_dataset_manifest_sha256,
                "producer_revision": forecast.engine.producer_revision,
                "promotion_status": forecast.promotion_status,
                "execution_status": forecast.execution_status,
                "simulation_count": forecast.simulator.simulation_count,
                "completed_simulations": forecast.simulator.completed_simulations,
            },
        )

    def _ensure_nfl_game_identity(self, forecast: SportsGameForecastV1):
        """Materialize exact exchange identities into the canonical sport tables.

        No name matching occurs.  The canonical/source IDs carried by the
        independently verified bundle are the sole authority; display names are
        presentation metadata only.
        """

        sport = self.session.scalar(select(Sport).where(Sport.code == "NFL"))
        if sport is None:
            sport = Sport(code="NFL", name="American Football")
            self.session.add(sport)
            self.session.flush()
        league = self.session.scalar(select(League).where(League.code == "NFL"))
        if league is None:
            league = League(
                sport_id=sport.id, code="NFL", name="National Football League", country_code="US"
            )
            self.session.add(league)
            self.session.flush()
        elif league.sport_id != sport.id:
            raise ValueError("Persisted NFL league belongs to a different sport identity")
        season = self.session.scalar(
            select(Season).where(Season.league_id == league.id, Season.name == "2026")
        )
        if season is None:
            season = Season(
                league_id=league.id,
                name="2026",
                starts_at=datetime(2026, 1, 1, tzinfo=UTC),
                ends_at=datetime(2027, 3, 1, tzinfo=UTC),
            )
            self.session.add(season)
            self.session.flush()

        home = self._ensure_nfl_team(league.id, forecast.home_team)
        away = self._ensure_nfl_team(league.id, forecast.away_team)
        mapping = self.session.scalar(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == "game",
            ExternalIdMapping.source == "NFLVERSE",
            ExternalIdMapping.external_id == forecast.source_event_id,
        ))
        game = self.session.get(Game, mapping.entity_id) if mapping else None
        if game is None:
            game = Game(
                league_id=league.id,
                season_id=season.id,
                starts_at=forecast.scheduled_start_utc,
                status=GameStatus.SCHEDULED,
                source_mode="RESEARCH_SHADOW",
            )
            self.session.add(game)
            self.session.flush()
            self.session.add(ExternalIdMapping(
                entity_type="game", entity_id=game.id, source="NFLVERSE",
                external_id=forecast.source_event_id,
            ))
            self.session.add_all([
                GameTeam(game_id=game.id, team_id=home.id, is_home=True),
                GameTeam(game_id=game.id, team_id=away.id, is_home=False),
            ])
            self.session.flush()
        else:
            sides = {row.is_home: row.team_id for row in self.session.scalars(
                select(GameTeam).where(GameTeam.game_id == game.id)
            )}
            exact_kickoff = (
                _aware(game.starts_at).replace(microsecond=0)
                == _aware(forecast.scheduled_start_utc).replace(microsecond=0)
            )
            if (
                game.league_id != league.id
                or game.season_id != season.id
                or sides != {True: home.id, False: away.id}
                or not exact_kickoff
            ):
                raise ValueError(f"NFL game {forecast.event_id} conflicts with exact persisted identity")
        return game.id

    def _ensure_nfl_team(self, league_id, ref):
        canonical = self.session.scalar(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == "team",
            ExternalIdMapping.source == "NFLVERSE_CANONICAL",
            ExternalIdMapping.external_id == ref.team_id,
        ))
        team = self.session.get(Team, canonical.entity_id) if canonical else None
        if team is None:
            team = self.session.scalar(select(Team).where(
                Team.league_id == league_id, Team.abbreviation == ref.team_id,
            ))
        if team is None:
            team = Team(
                league_id=league_id,
                name=ref.display_name or ref.team_id,
                abbreviation=ref.team_id,
                city=None,
            )
            self.session.add(team)
            self.session.flush()
        if team.league_id != league_id or team.abbreviation != ref.team_id:
            raise ValueError(f"NFL team {ref.team_id} conflicts with canonical identity")
        if canonical is None:
            self.session.add(ExternalIdMapping(
                entity_type="team", entity_id=team.id, source="NFLVERSE_CANONICAL",
                external_id=ref.team_id,
            ))
        source_mapping = self.session.scalar(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == "team",
            ExternalIdMapping.source == ref.source.upper(),
            ExternalIdMapping.external_id == ref.source_team_id,
        ))
        if source_mapping is not None and source_mapping.entity_id != team.id:
            raise ValueError(f"NFL source team {ref.source_team_id} maps to another canonical team")
        if source_mapping is None:
            self.session.add(ExternalIdMapping(
                entity_type="team", entity_id=team.id, source=ref.source.upper(),
                external_id=ref.source_team_id,
            ))
        self.session.flush()
        return team

    def _validate_persisted_identity(
        self, forecast: GameForecastExportV1 | SportsGameForecastV1
    ) -> None:
        """Reject cross-bundle remapping or duplicate snapshot version."""

        if not isinstance(forecast, SportsGameForecastV1):
            return
        previous = list(self.session.scalars(
            select(ShadowForecast).where(
                ShadowForecast.event_id == forecast.event_id,
                ShadowForecast.engine_id == forecast.engine.engine_id,
            )
        ))
        for row in previous:
            if (
                row.source_event_id != forecast.source_event_id
                or row.home_team_id != forecast.home_team.team_id
                or row.away_team_id != forecast.away_team.team_id
            ):
                raise ValueError(f"NFL event {forecast.event_id} conflicts with persisted exact identity")
            if (
                row.snapshot_type == forecast.snapshot_type
                and row.snapshot_version == forecast.snapshot_version
            ):
                raise ValueError(
                    f"NFL event {forecast.event_id} {forecast.snapshot_type} "
                    f"snapshot version {forecast.snapshot_version} already exists"
                )

    # -- evaluate --------------------------------------------------------
    def evaluate_board(
        self,
        slate_date: date,
        *,
        sport: str | None = None,
        snapshot_type: str | None = None,
        quote_source: QuoteSource | None = None,
        as_of: datetime | None = None,
        max_staleness_minutes: int = 240,
    ) -> dict:
        quote_source = quote_source or LiveOddsQuoteSource(self.session)
        as_of = as_of or datetime.now(UTC)
        bankroll = float(self.settings.shadow_virtual_bankroll)
        same_game_cap = bankroll * self.settings.shadow_max_same_game_percentage
        daily_cap = bankroll * self.settings.shadow_max_daily_percentage
        policy = StakePolicy(
            strategy=StakingStrategy.FRACTIONAL_KELLY,
            kelly_fraction=self.settings.shadow_kelly_fraction,
            maximum_stake_percentage=self.settings.shadow_max_position_percentage,
        )
        pricing_config = ShadowPricingConfig(qualified_edge=self.settings.shadow_qualified_edge)
        exposure = _Exposure()

        forecast_query = (
            select(ShadowForecast)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(ShadowForecastBundle.slate_date == slate_date)
        )
        if sport:
            forecast_query = forecast_query.where(ShadowForecastBundle.sport == sport.upper())
        if snapshot_type:
            forecast_query = forecast_query.where(ShadowForecast.snapshot_type == snapshot_type)
        forecast_query = forecast_query.where(
            (ShadowForecast.available_as_of_utc.is_(None))
            | (ShadowForecast.available_as_of_utc <= as_of)
        )
        candidates = list(self.session.scalars(
            forecast_query.order_by(ShadowForecast.event_id, ShadowForecast.engine_id)
        ))
        # One immutable snapshot controls each event/engine evaluation. Snapshot
        # history remains persisted, but EARLY and CURRENT never double-price or
        # double-consume the virtual exposure budget.
        priority = {"HISTORICAL": 0, "EARLY": 1, "CURRENT": 2, "FINAL_PREGAME": 3}
        latest: dict[tuple[str, str], ShadowForecast] = {}
        for row in candidates:
            key = (row.event_id, row.engine_id)
            current = latest.get(key)
            row_key = (
                _aware(row.available_as_of_utc or row.forecast_cutoff_utc or row.created_at),
                _aware(row.forecast_cutoff_utc or row.created_at),
                priority.get(row.snapshot_type, -1),
                row.snapshot_version,
            )
            if current is None:
                latest[key] = row
                continue
            current_key = (
                _aware(current.available_as_of_utc or current.forecast_cutoff_utc or current.created_at),
                _aware(current.forecast_cutoff_utc or current.created_at),
                priority.get(current.snapshot_type, -1),
                current.snapshot_version,
            )
            if row_key > current_key:
                latest[key] = row
        forecasts = sorted(latest.values(), key=lambda row: (row.event_id, row.engine_id))
        counts = {"decisions": 0, "qualified": 0, "watch": 0, "no_bet": 0, "priced": 0, "skipped": 0}
        total_stake = 0.0
        for forecast in forecasts:
            full, first_five, uncertainty = self._split_models(forecast)
            selections = (
                NFL_SUPPORTED_SELECTIONS
                if forecast.engine_id == "NFL_M1_XGBOOST"
                else SUPPORTED_SELECTIONS
            )
            for spec in selections:
                quote = quote_source.quote_for(
                    forecast, spec.market.value, spec.selection, as_of=as_of
                )
                if quote is not None and self._is_stale(quote, as_of, max_staleness_minutes):
                    quote = None
                signature = self._quote_signature(quote)
                if self._decision_exists(forecast.id, spec.market.value, spec.selection, signature):
                    counts["skipped"] += 1
                    continue
                decision, stake = self._build_decision(
                    forecast, spec, quote, full, first_five, uncertainty,
                    bankroll=bankroll, policy=policy, pricing_config=pricing_config,
                    same_game_cap=same_game_cap, daily_cap=daily_cap, exposure=exposure,
                    signature=signature,
                )
                self.session.add(decision)
                counts["decisions"] += 1
                total_stake += stake
                if decision.recommendation in {
                    Recommendation.QUALIFIED_BET.value, "QUALIFIED_SHADOW"
                }:
                    counts["qualified"] += 1
                elif decision.recommendation == Recommendation.WATCH.value:
                    counts["watch"] += 1
                else:
                    counts["no_bet"] += 1
                if decision.has_quote:
                    counts["priced"] += 1
        self.session.commit()
        return {
            "slate_date": slate_date.isoformat(),
            "forecasts_evaluated": len(forecasts),
            "total_virtual_stake": round(total_stake, 2),
            **counts,
        }

    def _build_decision(
        self, forecast, spec, quote, full, first_five, uncertainty, *,
        bankroll, policy, pricing_config, same_game_cap, daily_cap, exposure, signature,
    ) -> tuple[ShadowBoardDecision, float]:
        base = dict(
            shadow_forecast_id=forecast.id,
            bundle_id=forecast.bundle_id,
            slate_date=forecast.event_date,
            engine_id=forecast.engine_id,
            event_id=forecast.event_id,
            market=spec.market.value,
            selection=spec.selection,
            quote_signature=signature,
        )
        if quote is None:
            model_p = None
            push_probability = None
            if not spec.line_dependent:
                model_result = model_probability(
                    spec.market, spec.selection, line=None,
                    full_game=full, first_five=first_five, uncertainty=uncertainty,
                )
                model_p = model_result.probability
                push_probability = model_result.push_probability
            return ShadowBoardDecision(
                **base, model_probability=model_p, push_probability=push_probability, has_quote=False,
                recommendation=Recommendation.NO_BET.value, virtual_stake=0,
            ), 0.0

        line = quote.line
        model = model_probability(
            spec.market, spec.selection, line=line,
            full_game=full, first_five=first_five, uncertainty=uncertainty,
        )
        priced = price_selection(
            model=model,
            side_american=quote.side_american,
            opposite_american=quote.opposite_american,
            best_american=quote.best_american(),
            is_ready=forecast.is_ready,
            config=pricing_config,
        )
        stake = 0.0
        recommendation = priced.recommendation.value
        if forecast.engine_id == "NFL_M1_XGBOOST":
            if abs(priced.no_vig_edge) >= self.settings.opportunity_extreme_disagreement_threshold:
                recommendation = "REVIEW_REQUIRED"
            elif priced.recommendation == Recommendation.QUALIFIED_BET:
                recommendation = "QUALIFIED_SHADOW"
            elif priced.no_vig_edge > 0 and not forecast.is_ready:
                recommendation = "PROVISIONAL_EDGE"
        if recommendation in {Recommendation.QUALIFIED_BET.value, "QUALIFIED_SHADOW"}:
            raw = calculate_stake(
                bankroll, policy,
                model_probability=model.probability, decimal_odds=priced.best_decimal_odds,
            )
            remaining_game = max(0.0, same_game_cap - exposure.per_game.get(forecast.event_id, 0.0))
            remaining_day = max(0.0, daily_cap - exposure.daily)
            stake = round(min(raw, remaining_game, remaining_day), 2)
            exposure.per_game[forecast.event_id] = exposure.per_game.get(forecast.event_id, 0.0) + stake
            exposure.daily += stake
        decision = ShadowBoardDecision(
            **base,
            model_probability=priced.model_probability,
            push_probability=priced.push_probability,
            has_quote=True,
            odds_snapshot_id=quote.side_snapshot_id,
            opposite_odds_snapshot_id=quote.opposite_snapshot_id,
            source_quote_event_id=quote.source_event_id,
            quote_available_as_of=quote.available_as_of,
            book=quote.best_book(),
            line=line,
            side_american=quote.side_american,
            opposite_american=quote.opposite_american,
            best_american=priced.best_american_odds,
            best_decimal=priced.best_decimal_odds,
            quote_observed_at=quote.observed_at,
            break_even_probability=priced.break_even_probability,
            no_vig_probability=priced.no_vig_probability,
            raw_edge=priced.raw_edge,
            no_vig_edge=priced.no_vig_edge,
            expected_value=priced.expected_value,
            uncertainty_adjusted_edge=priced.uncertainty_adjusted_edge,
            recommendation=recommendation,
            virtual_stake=stake,
        )
        return decision, stake

    # -- settle ----------------------------------------------------------
    def settle_decisions(self, slate_date: date, outcomes: "Iterable[DecisionOutcome]") -> dict:
        outcome_rows = list(outcomes)
        by_id = {o.decision_id: o for o in outcome_rows if o.decision_id is not None}
        by_key = {
            (o.event_id, o.market, o.selection, o.engine_id): o
            for o in outcome_rows if o.decision_id is None
        }
        decisions = list(
            self.session.scalars(
                select(ShadowBoardDecision).where(ShadowBoardDecision.slate_date == slate_date)
            )
        )
        already = {
            row.decision_id
            for row in self.session.scalars(select(ShadowDecisionSettlement))
        }
        settled = 0
        total_pl = 0.0
        for decision in decisions:
            if decision.id in already:
                continue
            outcome = by_id.get(decision.id)
            if outcome is None:
                outcome = by_key.get(
                    (decision.event_id, decision.market, decision.selection, decision.engine_id)
                )
            if outcome is None:
                continue
            stake = float(decision.virtual_stake or 0.0)
            decimal_odds = float(decision.best_decimal or 0.0)
            if stake > 0 and decimal_odds > 1.0:
                pl = settle_profit(stake, decimal_odds, SettlementResult(outcome.result.lower()))
            else:
                pl = 0.0
            clv = None
            if outcome.closing_american is not None and decision.side_american is not None:
                clv = implied_probability(outcome.closing_american) - implied_probability(decision.side_american)
            self.session.add(ShadowDecisionSettlement(
                decision_id=decision.id,
                outcome=outcome.result.upper(),
                closing_american=outcome.closing_american,
                closing_decimal=american_to_decimal(outcome.closing_american) if outcome.closing_american else None,
                closing_line_value=clv,
                virtual_profit_loss=pl,
                settlement_note=outcome.note,
                performance_detail=self._performance_detail(decision, outcome),
            ))
            settled += 1
            total_pl += pl
        self.session.commit()
        return {"slate_date": slate_date.isoformat(), "settled": settled, "virtual_profit_loss": round(total_pl, 2)}

    def _performance_detail(self, decision: ShadowBoardDecision, outcome: "DecisionOutcome") -> dict:
        if outcome.actual_home_score is None or outcome.actual_away_score is None:
            return {}
        forecast = self.session.get(ShadowForecast, decision.shadow_forecast_id)
        if forecast is None:
            return {}
        home_score = int(outcome.actual_home_score)
        away_score = int(outcome.actual_away_score)
        home_result = 1.0 if home_score > away_score else 0.0 if home_score < away_score else None
        detail = {
            "actual_home_score": home_score,
            "actual_away_score": away_score,
            "home_points_absolute_error": abs(forecast.full_expected_home_score - home_score),
            "away_points_absolute_error": abs(forecast.full_expected_away_score - away_score),
            "margin_absolute_error": abs(
                (forecast.full_expected_home_score - forecast.full_expected_away_score)
                - (home_score - away_score)
            ),
            "total_absolute_error": abs(
                (forecast.full_expected_home_score + forecast.full_expected_away_score)
                - (home_score + away_score)
            ),
            "market_timing": "FINAL_PREGAME" if outcome.closing_american is not None else None,
        }
        if home_result is not None:
            full, first_five, uncertainty = self._split_models(forecast)
            probability = model_probability(
                ShadowMarket.FULL_GAME_MONEYLINE, "HOME", line=None,
                full_game=full, first_five=first_five, uncertainty=uncertainty,
            ).probability
            probability = min(max(probability, 1e-12), 1 - 1e-12)
            detail["home_win_log_loss"] = -(
                home_result * math.log(probability) + (1 - home_result) * math.log(1 - probability)
            )
            detail["home_win_brier"] = (probability - home_result) ** 2
            if (
                decision.market == ShadowMarket.FULL_GAME_MONEYLINE.value
                and decision.no_vig_probability is not None
                and decision.selection in {"HOME", "AWAY"}
            ):
                market_home = (
                    float(decision.no_vig_probability)
                    if decision.selection == "HOME"
                    else 1.0 - float(decision.no_vig_probability)
                )
                market_home = min(max(market_home, 1e-12), 1 - 1e-12)
                detail["market_home_win_probability"] = market_home
                detail["market_home_win_log_loss"] = -(
                    home_result * math.log(market_home)
                    + (1 - home_result) * math.log(1 - market_home)
                )
                detail["market_home_win_brier"] = (market_home - home_result) ** 2
                detail["model_minus_market_home_probability"] = probability - market_home
        if outcome.market_evidence:
            detail.update(outcome.market_evidence)
        return detail

    # -- read board ------------------------------------------------------
    def board(self, slate_date: date, *, sport: str | None = None) -> dict:
        sport_filter = sport.upper() if sport else None
        forecast_query = (
            select(ShadowForecast)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(ShadowForecastBundle.slate_date == slate_date)
        )
        if sport_filter:
            forecast_query = forecast_query.where(ShadowForecastBundle.sport == sport_filter)
        forecasts = list(
            self.session.scalars(
                forecast_query.order_by(ShadowForecast.event_id, ShadowForecast.engine_id)
            )
        )
        # Preserve every immutable snapshot in storage while showing only the
        # latest valid version of an event/engine on the CURRENT board.
        latest: dict[tuple[str, str], ShadowForecast] = {}
        type_priority = {"HISTORICAL": 0, "EARLY": 1, "CURRENT": 2, "FINAL_PREGAME": 3}
        for forecast in forecasts:
            key = (forecast.event_id, forecast.engine_id)
            prior = latest.get(key)
            forecast_time = forecast.available_as_of_utc or forecast.forecast_cutoff_utc
            prior_time = prior.available_as_of_utc or prior.forecast_cutoff_utc if prior else None
            forecast_rank = (
                _aware(forecast_time).timestamp() if forecast_time else 0.0,
                type_priority.get(forecast.snapshot_type, -1),
                forecast.snapshot_version,
            )
            prior_rank = (
                _aware(prior_time).timestamp() if prior_time else 0.0,
                type_priority.get(prior.snapshot_type, -1) if prior else -1,
                prior.snapshot_version if prior else -1,
            )
            if prior is None or forecast_rank > prior_rank:
                latest[key] = forecast
        forecasts = list(latest.values())
        decisions = list(
            self.session.scalars(
                select(ShadowBoardDecision)
                .where(ShadowBoardDecision.slate_date == slate_date)
                .order_by(ShadowBoardDecision.evaluated_at)
            )
        )
        decisions_by_forecast: dict = {}
        for decision in decisions:
            decisions_by_forecast.setdefault(decision.shadow_forecast_id, []).append(decision)

        bundle_sports = {
            row.id: row.sport
            for row in self.session.scalars(select(ShadowForecastBundle))
        }
        games: dict[str, dict] = {}
        for forecast in forecasts:
            forecast_sport = bundle_sports.get(forecast.bundle_id, "BASEBALL")
            game = games.setdefault(forecast.event_id, {
                "event_id": forecast.event_id,
                "event_date": forecast.event_date.isoformat(),
                "sport": forecast_sport,
                "scheduled_start_utc": forecast.scheduled_start_utc.isoformat() if forecast.scheduled_start_utc else None,
                "home_team_id": forecast.home_team_id,
                "away_team_id": forecast.away_team_id,
                "engines": [],
            })
            game["engines"].append(self._engine_view(forecast, decisions_by_forecast.get(forecast.id, [])))
        for game in games.values():
            game["model_disagreement"] = self._disagreement(game["engines"])
        available_sports = sorted({bundle_sports.get(row.bundle_id, "BASEBALL") for row in forecasts})
        has_nfl = "NFL" in available_sports
        return {
            "slate_date": slate_date.isoformat(),
            "status": "RESEARCH_SHADOW",
            "executable": False,
            "sports_available": available_sports,
            "active_sport": sport_filter,
            "lineage_disclosure": ({
                "model": "NFL M1 Long-History XGBoost",
                "status": "Research shadow — not promoted",
                "training_evidence": "1999 through completed 2025 season",
                "training_cutoff": "2026-02-08",
                "feature_tier": "LONG_HISTORY_CORE",
                "historical_oot_win_log_loss": 0.628770,
                "historical_sportsbook_benchmark": 0.606989,
                "historical_conclusion": "No demonstrated improvement over the market",
            } if has_nfl else None),
            "games": list(games.values()),
        }

    def _engine_view(self, forecast: ShadowForecast, decisions: list[ShadowBoardDecision]) -> dict:
        return {
            "engine_id": forecast.engine_id,
            "forecast_id": forecast.forecast_id,
            "snapshot_type": forecast.snapshot_type,
            "snapshot_version": forecast.snapshot_version,
            "forecast_cutoff_utc": forecast.forecast_cutoff_utc.isoformat() if forecast.forecast_cutoff_utc else None,
            "status": forecast.status,
            "is_ready": forecast.is_ready,
            "readiness_score": forecast.readiness_score,
            "readiness_threshold": forecast.readiness_threshold,
            "home_win_probability": forecast.full_home_win_probability,
            "away_win_probability": forecast.full_away_win_probability,
            "expected_home_score": forecast.full_expected_home_score,
            "expected_away_score": forecast.full_expected_away_score,
            "first_five_home_win_probability": forecast.f5_home_win_probability,
            "first_five_away_win_probability": forecast.f5_away_win_probability,
            # Stated separately, never redistributed into the two win
            # probabilities: a tie is its own outcome.
            "tie_probability": (forecast.distributions or {}).get("tie_probability"),
            # Arithmetic on the two persisted expected scores. No new estimate.
            "expected_margin_home": (
                round(forecast.full_expected_home_score - forecast.full_expected_away_score, 4)
                if forecast.full_expected_home_score is not None
                and forecast.full_expected_away_score is not None else None
            ),
            "expected_total_score": (
                round(forecast.full_expected_home_score + forecast.full_expected_away_score, 4)
                if forecast.full_expected_home_score is not None
                and forecast.full_expected_away_score is not None else None
            ),
            # Three distinct timestamps that are routinely conflated: when the
            # underlying source evidence was cut, when the producer published
            # the forecast, and when AISP actually imported it. The import time
            # is recorded as it happened and is never backdated.
            "source_evidence_utc": (
                forecast.source_cutoff_utc.isoformat() if forecast.source_cutoff_utc else None
            ),
            "forecast_published_utc": (
                forecast.available_as_of_utc.isoformat() if forecast.available_as_of_utc else None
            ),
            "imported_at_utc": (
                forecast.created_at.isoformat() if forecast.created_at else None
            ),
            "evidence_basis": self._evidence_basis(forecast),
            "uncertainty": forecast.uncertainty,
            "score_margin_distribution": forecast.distributions.get("score_margin_distribution", []),
            "total_score_distribution": forecast.distributions.get("total_score_distribution", []),
            "prediction_interval": forecast.distributions.get("prediction_interval"),
            "model_manifest_sha256": forecast.model_manifest_sha256,
            "model_binary_sha256": forecast.model_artifact_sha256,
            "feature_vector_sha256": forecast.feature_vector_sha256,
            "lineage": forecast.lineage_detail,
            "decisions": [self._decision_view(d) for d in decisions],
        }

    @staticmethod
    def _evidence_basis(forecast: ShadowForecast) -> dict | None:
        """What this forecast was actually built from.

        Reports the persisted missing-input count so an early-season baseline
        is never read as a refreshed, fully-evidenced forecast. It asserts
        nothing about quarterback, injury, weather or market evidence, because
        none of that is in the record.
        """

        detail = forecast.readiness_detail or {}
        missing = list(detail.get("missing_inputs") or [])
        if not missing:
            return None
        return {
            "missing_input_count": len(missing),
            "treatment": "FROZEN_PREGAME_MISSING_VALUE_TREATMENT",
            "note": (
                f"Early-season baseline: {len(missing)} season-to-date inputs are "
                "not yet observable, so the model uses its frozen preseason "
                "missing-value treatment. A newer snapshot version does not mean "
                "refreshed source evidence."
            ),
            "excluded_evidence": [
                "quarterback status", "injury report", "weather", "market price",
            ],
        }

    @staticmethod
    def _decision_view(d: ShadowBoardDecision) -> dict:
        return {
            "market": d.market,
            "selection": d.selection,
            "model_probability": d.model_probability,
            "push_probability": d.push_probability,
            "has_quote": d.has_quote,
            "book": d.book,
            "odds_snapshot_id": str(d.odds_snapshot_id) if d.odds_snapshot_id else None,
            "opposite_odds_snapshot_id": str(d.opposite_odds_snapshot_id) if d.opposite_odds_snapshot_id else None,
            "source_quote_event_id": d.source_quote_event_id,
            "quote_available_as_of": d.quote_available_as_of.isoformat() if d.quote_available_as_of else None,
            "line": d.line,
            "american_odds": d.side_american,
            "best_american_odds": d.best_american,
            "no_vig_probability": d.no_vig_probability,
            "raw_edge": d.raw_edge,
            "no_vig_edge": d.no_vig_edge,
            "expected_value": d.expected_value,
            "uncertainty_adjusted_edge": d.uncertainty_adjusted_edge,
            "recommendation": d.recommendation,
            "virtual_stake": float(d.virtual_stake or 0.0),
        }

    @staticmethod
    def _disagreement(engines: list[dict]) -> float | None:
        probs = [e["home_win_probability"] for e in engines if e["home_win_probability"] is not None]
        if len(probs) < 2:
            return None
        return round(max(probs) - min(probs), 4)

    # -- helpers ---------------------------------------------------------
    def _split_models(self, forecast: ShadowForecast):
        from app.forecast_exchange.contracts import GameSplit, Uncertainty

        if forecast.engine_id == "NFL_M1_XGBOOST":
            distributions = forecast.distributions
            completed = int(forecast.lineage_detail.get("completed_simulations") or 1)
            requested = int(forecast.lineage_detail.get("simulation_count") or completed)
            full = GameSplit.model_validate({
                "available": True,
                "away_win_probability": forecast.full_away_win_probability,
                "completed_simulations": completed,
                "completion_rate": completed / requested,
                "expected_away_score": forecast.full_expected_away_score,
                "expected_home_score": forecast.full_expected_home_score,
                "expected_total_score": forecast.full_expected_home_score + forecast.full_expected_away_score,
                "home_win_probability": forecast.full_home_win_probability,
                "joint_score_distribution": distributions["joint_score_distribution"],
                "missing_reason": None,
                "score_margin_distribution": distributions["score_margin_distribution"],
                "tie_probability": distributions.get("tie_probability", 0.0),
                "total_score_distribution": distributions["total_score_distribution"],
            })
            raw_uncertainty = forecast.uncertainty
            tie_probability = float(distributions.get("tie_probability", 0.0))
            home_lower = float(raw_uncertainty["home_win_probability_lower"])
            home_upper = float(raw_uncertainty["home_win_probability_upper"])
            uncertainty = Uncertainty.model_validate({
                "away_win_probability_lower": max(0.0, 1.0 - home_upper - tie_probability),
                "away_win_probability_upper": max(0.0, 1.0 - home_lower - tie_probability),
                "confidence_level": raw_uncertainty["confidence_level"],
                "expected_total_score_std_error": raw_uncertainty["expected_total_score_std_error"],
                "first_five_expected_total_score_std_error": 0.0,
                "first_five_home_win_probability_lower": 0.0,
                "first_five_home_win_probability_upper": 0.0,
                "home_win_probability_lower": home_lower,
                "home_win_probability_upper": home_upper,
                "max_batch_probability_delta": raw_uncertainty["max_batch_probability_delta"],
                "method": raw_uncertainty["method"],
            })
            return full, full, uncertainty
        full = GameSplit.model_validate(forecast.distributions["full_game"])
        first_five = GameSplit.model_validate(forecast.distributions["first_five"])
        uncertainty = Uncertainty.model_validate(forecast.uncertainty)
        return full, first_five, uncertainty

    @staticmethod
    def _is_stale(quote: GovernedQuote, as_of: datetime, max_staleness_minutes: int) -> bool:
        observed = quote.observed_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        return observed < as_of - timedelta(minutes=max_staleness_minutes)

    @staticmethod
    def _quote_signature(quote: GovernedQuote | None) -> str:
        if quote is None:
            return "NO_QUOTE"
        raw = f"{quote.book}|{quote.side_american}|{quote.opposite_american}|{quote.line}|{quote.observed_at.isoformat()}"
        if quote.side_snapshot_id is not None or quote.opposite_snapshot_id is not None:
            raw += f"|{quote.side_snapshot_id}|{quote.opposite_snapshot_id}|{quote.source_event_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _decision_exists(self, forecast_id, market: str, selection: str, signature: str) -> bool:
        return self.session.scalar(
            select(ShadowBoardDecision.id).where(
                ShadowBoardDecision.shadow_forecast_id == forecast_id,
                ShadowBoardDecision.market == market,
                ShadowBoardDecision.selection == selection,
                ShadowBoardDecision.quote_signature == signature,
            ).limit(1)
        ) is not None


@dataclass(frozen=True)
class DecisionOutcome:
    event_id: str
    engine_id: str
    market: str
    selection: str
    result: str  # WIN / LOSS / PUSH / VOID
    decision_id: object | None = None
    closing_american: int | None = None
    market_evidence: dict | None = None
    actual_home_score: int | None = None
    actual_away_score: int | None = None
    note: str | None = None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


__all__ = [
    "DecisionOutcome",
    "GovernedQuote",
    "LiveOddsQuoteSource",
    "MappingQuoteSource",
    "NFL_SUPPORTED_SELECTIONS",
    "ShadowBoardService",
    "SUPPORTED_SELECTIONS",
]
