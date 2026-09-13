"""Which forecast a surface should show for one game, and why.

One selector, used by every surface, so `/games`, `/markets`, the analysis page
and the simulator cannot disagree about which forecast a game "has". They used
to answer this independently: the slate and the market board read Gen 1 while
the simulator and the funded experiment used V18, so the same game showed
58.9% on one page and 60.2% on another with nothing saying they were different
models.

THE RULES, AND WHY EACH ONE EXISTS
----------------------------------
**Current MLB games follow the controlling era.** V18 became the controlling
engine at a specific instant. A game that starts after that instant is a V18
game, and showing Gen 1 for it by default misrepresents what governs it.

**History is not restated.** A game that started *before* the boundary was
forecast under Gen 1, and Gen 1 remains its forecast of record. Applying
today's era backwards would rewrite what the product said at the time, and a
forecast published after a game began is a replay, never a pregame forecast.

**A missing or invalidated V18 is reported, not replaced.** When V18 is absent,
superseded, or invalidated by inputs that changed after it was published, the
surface says so. It never silently falls back to Gen 1 and presents that as the
current forecast: the reader would see a number with no indication that the
governing engine had nothing to say.

**Gen 1 stays available as an explicit comparison.** It is real evidence and is
returned alongside, labelled as the comparison rather than the default.

Nothing here creates, edits or evaluates a decision. Selecting which forecast to
*display* is a read; it cannot move a bankroll, write a NO_BET, or change
authority.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Forecast,
    ForecastStatus,
    Game,
    ModelVersion,
    ShadowExperimentDecision,
    ShadowForecast,
)
from app.models.entities import ModelChampionAuthority

#: The producer engine that publishes V18 whole-game forecasts.
V18_ENGINE = "MLB_V18_WHOLE_GAME"
#: The label the product uses for the Gen 1 baseline.
GEN1_ENGINE = "AISP_BASELINE"


class Availability(str, enum.Enum):
    """Why a forecast is or is not usable as the current one."""

    AVAILABLE = "AVAILABLE"
    #: No forecast of this engine exists for this game at all.
    MISSING = "MISSING"
    #: A later forecast of the same engine replaced this one.
    SUPERSEDED = "SUPERSEDED"
    #: Published, but an input it depends on changed afterwards.
    INVALIDATED = "INVALIDATED"
    #: Published after first pitch. Evidence, but not a pregame forecast.
    NOT_PREGAME = "NOT_PREGAME"
    #: The game predates this engine's controlling era.
    OUTSIDE_ERA = "OUTSIDE_ERA"


@dataclass(frozen=True)
class ForecastIdentity:
    """Everything a surface needs to name a forecast without guessing."""

    engine_id: str
    engine_version: str | None
    model_label: str
    forecast_id: str
    snapshot_type: str | None
    snapshot_version: int | None
    #: When the newest evidence this forecast consumed was observed.
    source_cutoff_utc: datetime | None
    #: When the producer published it.
    published_utc: datetime | None
    #: When AISP imported or generated it.
    recorded_utc: datetime | None
    home_win_probability: float | None
    away_win_probability: float | None
    expected_home_score: float | None
    expected_away_score: float | None
    availability: Availability
    availability_detail: str
    #: False whenever availability is anything but AVAILABLE. A forecast may
    #: still be shown while this is False; it must not be shown as current.
    decision_eligible: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "engine_version": self.engine_version,
            "model_label": self.model_label,
            "forecast_id": self.forecast_id,
            "snapshot_type": self.snapshot_type,
            "snapshot_version": self.snapshot_version,
            "source_cutoff_utc": self.source_cutoff_utc,
            "published_utc": self.published_utc,
            "recorded_utc": self.recorded_utc,
            "home_win_probability": self.home_win_probability,
            "away_win_probability": self.away_win_probability,
            "expected_home_score": self.expected_home_score,
            "expected_away_score": self.expected_away_score,
            "availability": self.availability.value,
            "availability_detail": self.availability_detail,
            "decision_eligible": self.decision_eligible,
        }


@dataclass
class Selection:
    """The forecast a surface should lead with, and the alternatives."""

    primary: ForecastIdentity | None
    comparison: list[ForecastIdentity] = field(default_factory=list)
    #: Which engine the controlling era names for this game's era window.
    controlling_engine: str | None = None
    #: Plain sentence for the reader when the primary is not the era's engine.
    selection_note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary.as_dict() if self.primary else None,
            "comparison": [row.as_dict() for row in self.comparison],
            "controlling_engine": self.controlling_engine,
            "selection_note": self.selection_note,
        }


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class ForecastSelector:
    """Resolves the display forecast for one game."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- era ---------------------------------------------------------------

    def era_effective_at(self) -> datetime | None:
        """When the V18 engine began controlling, or None if it never did."""

        authority = self.session.scalar(
            select(ModelChampionAuthority)
            .where(ModelChampionAuthority.engine_id == V18_ENGINE)
            .order_by(ModelChampionAuthority.effective_at.desc())
            .limit(1)
        )
        return _aware(authority.effective_at) if authority else None

    # -- selection ---------------------------------------------------------

    def select(self, game: Game, *, now: datetime | None = None) -> Selection:
        """The forecast this game should lead with.

        ``now`` exists so a historical read can be reproduced; it is not used to
        decide whether a forecast was pregame — the game's own start time does
        that.
        """

        now = now or datetime.now(UTC)
        starts_at = _aware(game.starts_at)
        effective_at = self.era_effective_at()

        gen1 = self._gen1(game)
        v18 = self._v18(game, starts_at=starts_at, effective_at=effective_at)

        # A game that began before the boundary was forecast under Gen 1 and
        # keeps it. Today's era is not applied backwards.
        if v18 is not None and v18.availability is Availability.OUTSIDE_ERA:
            primary = gen1
            comparison = [v18]
            note = (
                "This game started before the current engine took control, so it keeps "
                "the forecast that governed it at the time."
            )
        elif v18 is not None and v18.availability is Availability.AVAILABLE:
            primary = v18
            comparison = [row for row in (gen1,) if row is not None]
            note = None
        elif v18 is not None:
            # Present, but not usable as the current forecast. It is still
            # shown -- with its condition -- and Gen 1 is offered as an
            # explicit comparison rather than a silent substitute.
            primary = v18
            comparison = [row for row in (gen1,) if row is not None]
            note = v18.availability_detail
        else:
            absent = self._absent_v18(game, effective_at=effective_at, starts_at=starts_at)
            if absent is None:
                # The game predates the era (or no era is registered), and the
                # controlling engine published nothing for it. Gen 1 is not a
                # substitute here -- it is the forecast that actually governed
                # this game, and showing it is how history stays unrestated.
                primary = gen1
                comparison = []
                note = (
                    "This game started before the current engine took control, so it "
                    "keeps the forecast that governed it at the time."
                ) if effective_at and gen1 is not None else None
            else:
                primary = absent
                comparison = [row for row in (gen1,) if row is not None]
                note = absent.availability_detail

        return Selection(
            primary=primary,
            comparison=[row for row in comparison if row is not None],
            controlling_engine=V18_ENGINE if effective_at else None,
            selection_note=note,
        )

    # -- engines -----------------------------------------------------------

    def _v18(
        self, game: Game, *, starts_at: datetime | None, effective_at: datetime | None,
    ) -> ForecastIdentity | None:
        rows = list(self.session.scalars(
            select(ShadowForecast)
            .where(
                ShadowForecast.canonical_game_id == game.id,
                ShadowForecast.engine_id == V18_ENGINE,
            )
            .order_by(ShadowForecast.snapshot_version, ShadowForecast.created_at)
        ))
        if not rows:
            return None
        current = rows[-1]
        superseded = rows[:-1]

        availability, detail = self._v18_condition(
            game, current, starts_at=starts_at, effective_at=effective_at,
            superseded_count=len(superseded),
        )
        return ForecastIdentity(
            engine_id=current.engine_id,
            engine_version=current.engine_version,
            model_label="V18 whole-game (research shadow)",
            forecast_id=current.forecast_id,
            snapshot_type=current.snapshot_type,
            snapshot_version=current.snapshot_version,
            source_cutoff_utc=_aware(current.source_cutoff_utc),
            published_utc=_aware(current.available_as_of_utc),
            recorded_utc=_aware(current.created_at),
            home_win_probability=current.full_home_win_probability,
            away_win_probability=current.full_away_win_probability,
            expected_home_score=current.full_expected_home_score,
            expected_away_score=current.full_expected_away_score,
            availability=availability,
            availability_detail=detail,
            decision_eligible=availability is Availability.AVAILABLE,
        )

    def _v18_condition(
        self,
        game: Game,
        current: ShadowForecast,
        *,
        starts_at: datetime | None,
        effective_at: datetime | None,
        superseded_count: int,
    ) -> tuple[Availability, str]:
        published = _aware(current.available_as_of_utc)

        if effective_at is not None and starts_at is not None and starts_at < effective_at:
            return (
                Availability.OUTSIDE_ERA,
                "This game started before the V18 engine took control, so V18 is shown "
                "as a comparison and not as the forecast that governed it.",
            )
        if published is not None and starts_at is not None and published > starts_at:
            return (
                Availability.NOT_PREGAME,
                "This forecast was published after first pitch. It is a replay, not a "
                "pregame forecast, and is not decision-eligible.",
            )

        # Inputs that changed after publication. The decision record already
        # carries this judgement; it is read rather than recomputed, so the
        # display cannot disagree with what the policy actually concluded.
        blocking = self._blocking_decision(game.id)
        if blocking:
            return (
                Availability.INVALIDATED,
                f"An input changed after this forecast was published: {blocking} "
                "It remains visible for the record and is not currently decision-eligible.",
            )
        return (Availability.AVAILABLE, "")

    def _blocking_decision(self, game_id: UUID) -> str | None:
        """The most recent recorded reason this game's V18 forecast is blocked.

        Read from the persisted decision, never recomputed: the policy already
        made this judgement, and a second opinion computed at display time
        could contradict the record.
        """

        decision = self.session.scalar(
            select(ShadowExperimentDecision)
            .where(ShadowExperimentDecision.game_id == game_id)
            .order_by(ShadowExperimentDecision.recommended_at.desc())
            .limit(1)
        )
        if decision is None:
            return None
        reason = decision.recommendation_reason or ""
        if "INELIGIBLE" not in reason.upper() and "MISSING_BLOCKING" not in reason.upper():
            return None
        # The recorded sentence, trimmed to the part a reader can act on. The
        # policy writes the human-readable half inside parentheses, after a tier
        # code that means nothing to a reader, so the parenthetical is preferred
        # when there is one. Trailing punctuation is stripped as a set rather
        # than in a fixed order: the stored reason ends ")." and stripping the
        # bracket first would leave the period, or the period first the bracket.
        match = re.search(r"\(([^()]*)\)\s*\.?\s*$", reason)
        trimmed = (match.group(1) if match else reason).strip(" .—-")
        return f"{trimmed}." if trimmed else None

    def _absent_v18(
        self, game: Game, *, effective_at: datetime | None, starts_at: datetime | None,
    ) -> ForecastIdentity | None:
        """A placeholder describing why the era's engine has nothing here."""

        if effective_at is None:
            return None
        if starts_at is not None and starts_at < effective_at:
            return None
        return ForecastIdentity(
            engine_id=V18_ENGINE, engine_version=None,
            model_label="V18 whole-game (research shadow)",
            forecast_id="", snapshot_type=None, snapshot_version=None,
            source_cutoff_utc=None, published_utc=None, recorded_utc=None,
            home_win_probability=None, away_win_probability=None,
            expected_home_score=None, expected_away_score=None,
            availability=Availability.MISSING,
            availability_detail=(
                "The controlling engine published no forecast for this game. "
                "No other model is shown in its place; Gen 1 remains available "
                "as an explicit comparison."
            ),
            decision_eligible=False,
        )

    def _gen1(self, game: Game) -> ForecastIdentity | None:
        forecast = self.session.scalar(
            select(Forecast)
            .where(Forecast.game_id == game.id, Forecast.status == ForecastStatus.PUBLISHED)
            .order_by(Forecast.generated_at.desc())
            .limit(1)
        ) or self.session.scalar(
            select(Forecast)
            .where(Forecast.game_id == game.id)
            .order_by(Forecast.generated_at.desc())
            .limit(1)
        )
        if forecast is None:
            return None
        version = (
            self.session.get(ModelVersion, forecast.model_version_id)
            if forecast.model_version_id else None
        )
        return ForecastIdentity(
            engine_id=GEN1_ENGINE,
            engine_version=(f"{version.name} {version.version}" if version else None),
            model_label="Gen 1 baseline",
            forecast_id=str(forecast.id),
            snapshot_type=None, snapshot_version=None,
            source_cutoff_utc=_aware(forecast.available_as_of),
            published_utc=_aware(forecast.generated_at),
            recorded_utc=_aware(forecast.created_at),
            home_win_probability=forecast.home_win_probability,
            away_win_probability=forecast.away_win_probability,
            expected_home_score=getattr(forecast, "expected_home_score", None),
            expected_away_score=getattr(forecast, "expected_away_score", None),
            availability=Availability.AVAILABLE,
            availability_detail="",
            decision_eligible=False,
        )
