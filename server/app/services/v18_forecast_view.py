"""Present a saved V18 forecast through the surface the pricing path already reads.

WHY AN ADAPTER AND NOT A SECOND PIPELINE
----------------------------------------
The policy that decides whether a price is worth taking -- the 2% adjusted-edge
floor, the freshness window, the cutoff, the staking rules -- is the thing we
must NOT change when the forecasting engine changes. Writing a parallel V18
decision path would mean two copies of those rules, and two copies drift. So
V18 forecasts are adapted into the shape the existing path already consumes,
and every gate downstream runs unmodified on them.

WHAT THIS IS NOT
----------------
It is not an execution. AISP never fits or calls the estimator; the numbers
here were computed by the producer and saved, and this object only reads the
row. Nothing is recomputed, rescaled or filled in: a field the producer did not
send is None, and the gates treat None as missing rather than as zero.

THE THREE CLOCKS STAY APART
---------------------------
``generated_at`` is publication (``forecastCreatedAt``), ``available_as_of`` is
the evidence cutoff (``capturedAt``). The existing cutoff gate compares both
against the decision cutoff, which is the whole point of keeping them distinct:
a forecast published before the cutoff but built on evidence captured after it
must fail, and it can only fail if the two are stored separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Game, ShadowForecast
from app.services.league_scope import MLB, LeagueMismatchError, require_league

ENGINE_ID = "MLB_V18_WHOLE_GAME"

#: Explicit skip reasons. Every one of them means "no V18 decision", never
#: "fall back to Gen1".
NO_V18_FORECAST = "NO_V18_FORECAST_FOR_THIS_GAME"
NOT_READY = "V18_FORECAST_IS_NOT_MARKED_READY"
NO_PROBABILITY = "V18_FORECAST_CARRIES_NO_WHOLE_GAME_WIN_PROBABILITY"
EVIDENCE_AFTER_CUTOFF = "V18_EVIDENCE_CUTOFF_IS_AFTER_THE_DECISION_CUTOFF"
PUBLISHED_AFTER_CUTOFF = "V18_FORECAST_WAS_PUBLISHED_AFTER_THE_DECISION_CUTOFF"
NOT_AN_MLB_GAME = "V18_DECIDES_MLB_GAMES_ONLY"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class _Lifecycle:
    """Mirrors ``ForecastLifecycle`` closely enough for the label the row stores."""

    value: str


class V18ForecastView:
    """One saved ``ShadowForecast``, read through the Gen1 forecast surface.

    Only the attributes the pricing and policy path actually reads are exposed.
    An attribute that would have to be invented to exist is absent rather than
    guessed, so a caller that needs one fails loudly instead of pricing a
    fabricated number.
    """

    __slots__ = ("row", "id", "shadow_forecast_id", "game_id")

    def __init__(self, row: ShadowForecast) -> None:
        self.row = row
        # Deliberately None: there is no row in `forecasts`, and claiming one
        # would write a dangling foreign key onto the decision.
        self.id: UUID | None = None
        self.shadow_forecast_id: UUID = row.id
        self.game_id = row.event_id

    # -- identity -------------------------------------------------------
    @property
    def engine_id(self) -> str:
        return self.row.engine_id

    @property
    def engine_version(self) -> str:
        return self.row.engine_version

    @property
    def version(self) -> int:
        # The producer versions by content digest, not by an incrementing
        # integer. One is honest here: this is the only V18 forecast for this
        # (game, engine version) identity, because a second one would be a
        # different digest and therefore a different row.
        return 1

    @property
    def lifecycle(self) -> _Lifecycle:
        return _Lifecycle(value="v18")

    # -- the three clocks ------------------------------------------------
    @property
    def generated_at(self) -> datetime | None:
        """Publication: when the producer created the forecast."""

        return _aware(self.row.available_as_of_utc)

    @property
    def available_as_of(self) -> datetime | None:
        """Evidence cutoff: the latest source availability it incorporates."""

        return _aware(self.row.source_cutoff_utc)

    @property
    def published_at(self) -> datetime | None:
        return _aware(self.row.available_as_of_utc)

    # -- the numbers -----------------------------------------------------
    @property
    def home_win_probability(self) -> float | None:
        value = self.row.full_home_win_probability
        return float(value) if value is not None else None

    @property
    def away_win_probability(self) -> float | None:
        value = self.row.full_away_win_probability
        return float(value) if value is not None else None

    @property
    def expected_home_runs(self) -> float | None:
        value = self.row.full_expected_home_score
        return float(value) if value is not None else None

    @property
    def expected_away_runs(self) -> float | None:
        value = self.row.full_expected_away_score
        return float(value) if value is not None else None

    @property
    def readiness_score(self) -> float | None:
        value = self.row.readiness_score
        return float(value) if value is not None else None

    #: The one scalar the V18 import stores for the policy's uncertainty input.
    #: Producer ``monteCarloUncertainty.homeWinProbabilityStandardError``
    #: (sqrt(p(1-p)/n), probability units) is imported by
    #: ``shadow_import.mlb_v18_daily`` as ``uncertainty["monteCarloStandardError"]``.
    #: Until 2026-09-18 this view read ``fullGameWinProbabilityStandardError``, a
    #: name no producer or importer ever wrote, so every V18 decision received
    #: None -> the policy's 0.0 default. Corrected prospectively; decisions
    #: already recorded keep the evidence they were made on.
    UNCERTAINTY_FIELD = "monteCarloStandardError"
    #: What that number is: Monte Carlo SAMPLING error of the win probability,
    #: explicitly not model (epistemic) uncertainty.
    UNCERTAINTY_BASIS = "MONTE_CARLO_SAMPLING_STANDARD_ERROR"

    @property
    def uncertainty(self) -> float | None:
        value = self.row.uncertainty
        if isinstance(value, dict):
            # One named scalar, never an average of fields that mean different things.
            scalar = value.get(self.UNCERTAINTY_FIELD)
            return float(scalar) if scalar is not None else None
        return float(value) if value is not None else None

    @property
    def uncertainty_basis(self) -> str | None:
        """What the uncertainty scalar measures, or None when the forecast carries none."""

        return self.UNCERTAINTY_BASIS if self.uncertainty is not None else None

    @property
    def readiness_components(self) -> dict[str, Any]:
        detail = self.row.readiness_detail
        return detail if isinstance(detail, dict) else {}

    @property
    def simulation_summary(self) -> dict[str, Any]:
        distributions = self.row.distributions
        return distributions if isinstance(distributions, dict) else {}

    @property
    def model_drivers(self) -> list[Any]:
        return []

    @property
    def source_mode(self) -> str:
        return "LIVE"

    @property
    def confidence(self) -> float | None:
        return None

    # -- lineage, for the decision's evidence ----------------------------
    def lineage(self) -> dict[str, Any]:
        """Exactly which saved artifact produced this number."""

        return {
            "engine_id": self.row.engine_id,
            "engine_version": self.row.engine_version,
            "forecast_id": self.row.forecast_id,
            "shadow_forecast_id": str(self.row.id),
            "model_artifact_sha256": self.row.model_artifact_sha256,
            "model_manifest_sha256": self.row.model_manifest_sha256,
            "feature_vector_sha256": self.row.feature_vector_sha256,
            "simulator_id": self.row.simulator_id,
            "simulator_version": self.row.simulator_version,
            "simulator_seed": self.row.simulator_seed,
            "source_event_id": self.row.source_event_id,
            "evidence_cutoff_utc": (
                self.available_as_of.isoformat() if self.available_as_of else None
            ),
            "published_utc": self.generated_at.isoformat() if self.generated_at else None,
            "executed_in_aisp": False,
        }


def latest_v18_forecast(
    session: Session, game_id: Any, *, engine_id: str = ENGINE_ID
) -> ShadowForecast | None:
    """The most recently PUBLISHED V18 forecast for one game.

    Ordered by publication, not by import time: which forecast the producer
    issued last is a fact about the producer, and re-importing an older package
    must not promote an older forecast.
    """

    return session.scalar(
        select(ShadowForecast)
        .where(ShadowForecast.engine_id == engine_id,
               ShadowForecast.event_id == str(game_id))
        .order_by(ShadowForecast.available_as_of_utc.desc(),
                  ShadowForecast.source_cutoff_utc.desc())
        .limit(1)
    )


def resolve_for_decision(
    session: Session,
    game_id: Any,
    *,
    cutoff: datetime,
    engine_id: str = ENGINE_ID,
) -> tuple[V18ForecastView | None, str | None]:
    """The V18 forecast this game may decide on, or the exact reason there is none.

    Returning a reason rather than None-with-no-explanation is the point: the
    era forbids a Gen1 fallback, so every game that does not decide has to say
    what stopped it, and "no forecast" must be distinguishable from "a forecast
    that arrived too late".
    """

    game = session.get(Game, game_id if isinstance(game_id, UUID) else UUID(str(game_id)))
    if game is not None:
        try:
            require_league(session, game, MLB, process="V18 decision input")
        except LeagueMismatchError:
            return None, NOT_AN_MLB_GAME
    row = latest_v18_forecast(session, game_id, engine_id=engine_id)
    if row is None:
        return None, NO_V18_FORECAST
    view = V18ForecastView(row)
    if view.home_win_probability is None or view.away_win_probability is None:
        return None, NO_PROBABILITY
    if not row.is_ready:
        return None, NOT_READY
    evidence = view.available_as_of
    published = view.generated_at
    if evidence is not None and evidence > cutoff:
        return None, EVIDENCE_AFTER_CUTOFF
    if published is not None and published > cutoff:
        return None, PUBLISHED_AFTER_CUTOFF
    return view, None


def v18_coverage(session: Session, event_date: date, *, engine_id: str = ENGINE_ID) -> dict[str, Any]:
    """How many of one slate's games have a saved V18 forecast at all."""

    rows = list(session.scalars(
        select(ShadowForecast).where(ShadowForecast.engine_id == engine_id,
                                     ShadowForecast.event_date == event_date)
    ))
    return {
        "event_date": event_date.isoformat(),
        "engine_id": engine_id,
        "forecasts": len(rows),
        "games": len({row.event_id for row in rows}),
        "ready": sum(1 for row in rows if row.is_ready),
    }


__all__ = [
    "ENGINE_ID", "EVIDENCE_AFTER_CUTOFF", "NOT_READY", "NO_PROBABILITY",
    "NOT_AN_MLB_GAME", "NO_V18_FORECAST", "PUBLISHED_AFTER_CUTOFF", "V18ForecastView",
    "latest_v18_forecast", "resolve_for_decision", "v18_coverage",
]
