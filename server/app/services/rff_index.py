"""RFF Lens — Market reality check.

WHAT THIS IS
------------
A research measure of market imbalance, unusual movement and unresolved
information. It is NOT evidence that a game is manipulated, and nothing here
accuses a league, team, official, player or sportsbook of anything. A high
score means the observable market looks unsettled; the ordinary explanations
for that -- a late scratch, a sharp opinion, a thin market, two books
disagreeing -- are the common ones.

WHAT IT MAY NOT TOUCH
---------------------
Forecasts, adjusted edge, the 2% minimum, positions, settlements, wagering
authority. It reads persisted evidence and returns a number. Nothing in this
module is imported by the policy, and nothing it returns is an input to one.
The layers stay in order and stay separate:

    forecast  ->  market evidence  ->  RFF research index  ->  decision policy
                                        (this file)            (untouched)

HOW MISSING EVIDENCE IS ACCOUNTED FOR (the rule that matters)
-------------------------------------------------------------
Components carry FIXED nominal weights that always sum to 1. The published
figures are computed against that fixed denominator, never against the weight
that happens to be available:

    knownContribution = sum(w_i * s_i) over available components   (0..100)
    coverage          = sum(w_i)       over available components   (0..1)
    unknownWeight     = 1 - coverage
    lowerBound        = knownContribution
    upperBound        = knownContribution + unknownWeight * 100

Renormalising by the available weight -- what this module used to do -- let a
single 8%-weight component become 100% of the composite, so sparse data read as
market unrest. The renormalised figure is retained only as an internal
diagnostic, ``observedComponentAverage``. It is not RFF, it never sets a
category, and it never ranks games with different availability.

Missing is never zero, never imputed, and never silently averaged away: it
widens the interval instead, and the publication policy below decides whether a
number may be shown at all.

WHY IT IS PROVISIONAL, AND WILL STAY PROVISIONAL
------------------------------------------------
The version of this measure that would actually be worth arguing about needs
to know where the PUBLIC's money is, so that a line moving the other way means
something. AISP has no ticket-count or handle-share source, and no amount of
price data substitutes for it: a price is where a book will trade, not who is
on it. Two of the eight components therefore have no data at all, and the
result is labelled PROVISIONAL until they do.

The missing components are not scored as zero. A zero would say "we looked and
found no public concentration", which is a claim; ``Unavailable`` says we did
not look, which is the truth. Coverage is published so a reader can see how
much of the measure is actually behind the number, and the score is published
with the range it could occupy if the missing components were at either
extreme.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Forecast, Game, LineupObservation, StarterObservation
from app.services.market_tape import MarketTapeService, display_name

SCHEMA_VERSION = "AISP_RFF_INDEX_V1"
PUBLIC_LABEL = "RFF Lens — Market reality check"
PUBLIC_DESCRIPTION = (
    "A research measure of market imbalance, unusual movement and unresolved "
    "information. It is not evidence that a game is manipulated."
)
#: Publication status. A number is published only when the evidence supports one.
STATUS_PUBLISHED = "RFF_PUBLISHED"
STATUS_PROVISIONAL = "PROVISIONAL_WIDE_RANGE"
STATUS_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

#: The publication policy, frozen for this release. Thresholds are set from the
#: structure of the measure, never from outcomes or postgame evidence.
MIN_COVERAGE_PROVISIONAL = 0.50
MIN_COVERAGE_PUBLISHED = 0.70
MIN_COMPONENTS_PUBLISHED = 4
MIN_FAMILIES_PUBLISHED = 2
#: A category is assigned only when the plausible interval is this narrow AND
#: does not straddle two bands.
MAX_CATEGORY_INTERVAL_POINTS = 25.0
CATEGORY_UNCERTAIN = "CATEGORY_UNCERTAIN"

#: The window that counts as "late" when the cutoff has not arrived yet.
LATE_WINDOW_MINUTES = 120
#: Evidence older than this is stale: it is reported, never counted as coverage.
MARKET_MAX_AGE_MINUTES = 240
INFORMATION_MAX_AGE_MINUTES = 360
#: Lineup and starter resolution is only observable near first pitch/kickoff.
#: Before that, "not confirmed yet" is the normal state of the world, not unrest.
INFORMATION_WINDOW_HOURS = 6

#: Why a component has no score. Absence is classified, never scored.
NO_SOURCE = "NO_SOURCE"
DEPENDS_ON_UNAVAILABLE = "DEPENDS_ON_UNAVAILABLE_COMPONENT"
INSUFFICIENT_OBSERVATIONS = "INSUFFICIENT_OBSERVATIONS"
NOT_YET_OBSERVABLE = "NOT_YET_OBSERVABLE"
STALE_EVIDENCE = "STALE_EVIDENCE"
INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"

# -- component identity -----------------------------------------------------
PUBLIC_CONCENTRATION = "PUBLIC_CONCENTRATION"
SPORTSBOOK_RELIEF = "SPORTSBOOK_RELIEF"
REVERSE_LINE_MOVEMENT = "REVERSE_LINE_MOVEMENT"
LATE_MOVEMENT = "LATE_MOVEMENT"
CROSS_BOOK_DISAGREEMENT = "CROSS_BOOK_DISAGREEMENT"
MODEL_MARKET_DISAGREEMENT = "MODEL_MARKET_DISAGREEMENT"
INFORMATION_UNCERTAINTY = "INFORMATION_UNCERTAINTY"
SOCIAL_NARRATIVE = "SOCIAL_NARRATIVE"

#: Weights sum to exactly 1. Public money carries the most because it is the
#: component the measure is really about; everything else is a symptom that
#: could have a dozen innocent causes.
WEIGHTS: dict[str, float] = {
    PUBLIC_CONCENTRATION: 0.20,
    SPORTSBOOK_RELIEF: 0.12,
    REVERSE_LINE_MOVEMENT: 0.18,
    LATE_MOVEMENT: 0.14,
    CROSS_BOOK_DISAGREEMENT: 0.14,
    MODEL_MARKET_DISAGREEMENT: 0.10,
    INFORMATION_UNCERTAINTY: 0.08,
    SOCIAL_NARRATIVE: 0.04,
}

#: Conceptual families. Coverage alone is not diversity: two components in one
#: family measure nearly the same thing, so publication requires more than one
#: family to be represented.
PUBLIC_POSITIONING = "PUBLIC_POSITIONING"
PRICE_MOVEMENT = "PRICE_MOVEMENT"
CROSS_BOOK_STRUCTURE = "CROSS_BOOK_STRUCTURE"
MODEL_VS_MARKET = "MODEL_VS_MARKET"
GAME_INFORMATION = "GAME_INFORMATION"
NARRATIVE = "NARRATIVE"

FAMILIES: dict[str, str] = {
    PUBLIC_CONCENTRATION: PUBLIC_POSITIONING,
    SPORTSBOOK_RELIEF: PUBLIC_POSITIONING,
    REVERSE_LINE_MOVEMENT: PRICE_MOVEMENT,
    LATE_MOVEMENT: PRICE_MOVEMENT,
    CROSS_BOOK_DISAGREEMENT: CROSS_BOOK_STRUCTURE,
    MODEL_MARKET_DISAGREEMENT: MODEL_VS_MARKET,
    INFORMATION_UNCERTAINTY: GAME_INFORMATION,
    SOCIAL_NARRATIVE: NARRATIVE,
}

FAMILY_LABELS: dict[str, str] = {
    PUBLIC_POSITIONING: "Public positioning",
    PRICE_MOVEMENT: "Price movement",
    CROSS_BOOK_STRUCTURE: "Cross-book structure",
    MODEL_VS_MARKET: "Model versus market",
    GAME_INFORMATION: "Game-information uncertainty",
    NARRATIVE: "Narrative / social",
}

#: Components that overlap or depend on each other, so a reader is never shown
#: two views of one phenomenon as if they were independent confirmation.
CORRELATED: tuple[dict[str, Any], ...] = (
    {"components": [REVERSE_LINE_MOVEMENT, LATE_MOVEMENT],
     "relation": "Both read the same consensus price series over different windows; a single "
                 "large move can raise both."},
    {"components": [SPORTSBOOK_RELIEF, PUBLIC_CONCENTRATION],
     "relation": "Relief is a function of public concentration. Without handle share it cannot "
                 "be computed, and it is never derived from price alone."},
)

LABELS: dict[str, str] = {
    PUBLIC_CONCENTRATION: "Public concentration",
    SPORTSBOOK_RELIEF: "Sportsbook-relief proxy",
    REVERSE_LINE_MOVEMENT: "Reverse line movement",
    LATE_MOVEMENT: "Late movement",
    CROSS_BOOK_DISAGREEMENT: "Cross-book disagreement",
    MODEL_MARKET_DISAGREEMENT: "Model–market disagreement",
    INFORMATION_UNCERTAINTY: "Information uncertainty",
    SOCIAL_NARRATIVE: "Social / narrative signal",
}

#: Bands are descriptive, in ascending order, and never predictive.
#: (floor, stable key, display label, description). Keys are an API contract
#: and do not change when the display wording does.
BANDS: tuple[tuple[int, str, str, str], ...] = (
    (0, "CALM", "Calm", "The market agrees with itself and has not moved much."),
    (30, "CURIOUS", "Curious", "Something is a little off — worth a look, not an alarm."),
    (55, "SPICY", "Spicy", "Real disagreement or real movement, with evidence behind it."),
    (75, "MAXIMUM_FUCKERY", "Maximum unrest", "As unsettled as this measure can see. Still not proof of anything."),
)


def band_for(score: float | None) -> dict[str, Any]:
    if score is None:
        return {"key": "UNSCORED", "label": "Unscored", "description": "No component has data."}
    chosen = BANDS[0]
    for band in BANDS:
        if score >= band[0]:
            chosen = band
    floor, key, label, description = chosen
    return {"key": key, "label": label, "description": description, "floor": floor}


def _publication_status(available: list["Component"], coverage: float,
                        families: list[str]) -> tuple[str, list[dict[str, str]]]:
    """Whether a composite may be published, and every reason it may not be.

    Frozen policy:
      * below 50% coverage            -> INSUFFICIENT_EVIDENCE (no number, no
        category, not ranked);
      * 50%-69%                       -> PROVISIONAL_WIDE_RANGE (known
        contribution and interval only, kept apart from published games);
      * 70%+                          -> a number, but only with at least four
        components drawn from at least two conceptual families, and only from
        fresh, timing-eligible evidence (a stale component never reached this
        list, so it cannot buy coverage).
    """

    blockers: list[dict[str, str]] = []
    if coverage < MIN_COVERAGE_PUBLISHED:
        blockers.append({
            "code": "COVERAGE_BELOW_PUBLICATION_THRESHOLD",
            "detail": f"{coverage * 100:.0f}% of the measure has data; {MIN_COVERAGE_PUBLISHED * 100:.0f}% is required.",
        })
    if len(available) < MIN_COMPONENTS_PUBLISHED:
        blockers.append({
            "code": "TOO_FEW_COMPONENTS",
            "detail": f"{len(available)} component(s) available; {MIN_COMPONENTS_PUBLISHED} are required.",
        })
    if len(families) < MIN_FAMILIES_PUBLISHED:
        blockers.append({
            "code": "TOO_FEW_EVIDENCE_FAMILIES",
            "detail": ("Evidence comes from "
                       f"{len(families)} conceptual family(ies); {MIN_FAMILIES_PUBLISHED} are required, "
                       "because two components of one family measure nearly the same thing."),
        })
    if not blockers:
        return STATUS_PUBLISHED, []
    if coverage >= MIN_COVERAGE_PROVISIONAL:
        return STATUS_PROVISIONAL, blockers
    return STATUS_INSUFFICIENT, blockers


def _category(status: str, lower: float, upper: float,
              available: list["Component"]) -> tuple[dict[str, Any], str]:
    """A band only when the evidence interval actually identifies one."""

    uncertain = {
        "key": CATEGORY_UNCERTAIN, "label": "Category uncertain",
        "description": "The evidence does not narrow the measure to one band.",
    }
    if status != STATUS_PUBLISHED:
        return uncertain, ("No category is assigned below the publication threshold: a label from "
                           "partial evidence would read as a finding about the market.")
    width = upper - lower
    if width > MAX_CATEGORY_INTERVAL_POINTS:
        return uncertain, (f"The plausible interval spans {width:.0f} points, wider than the "
                           f"{MAX_CATEGORY_INTERVAL_POINTS:.0f}-point limit for naming a band.")
    low_band, high_band = band_for(lower), band_for(upper)
    if low_band["key"] != high_band["key"]:
        return uncertain, (f"The interval spans {low_band['label']} to {high_band['label']}, so no "
                           "single band describes it.")
    if len(available) < MIN_COMPONENTS_PUBLISHED:
        return uncertain, "Too few components to name a band."
    if high_band["key"] == BANDS[-1][1] and len(available) < MIN_COMPONENTS_PUBLISHED:
        return uncertain, "The highest band is never assigned from a thin component set."
    return low_band, "The whole plausible interval sits inside one band."


def _stale(observed: str | None, now: datetime, max_age_minutes: float) -> bool:
    """Evidence older than the limit is reported but never counted as coverage."""

    if not observed:
        return True
    moment = datetime.fromisoformat(observed)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (now - moment).total_seconds() / 60.0 > max_age_minutes


def _ramp(value: float, saturate_at: float) -> float:
    """0..100, linear to a stated saturation point. No hidden curve."""

    if saturate_at <= 0:
        return 0.0
    return round(min(100.0, max(0.0, (value / saturate_at) * 100.0)), 1)


@dataclass
class Component:
    key: str
    available: bool
    score: float | None = None
    basis: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    requires: str | None = None
    limitation: str | None = None
    #: Why it is unavailable: missing source, stale, not yet observable, and so on.
    reason: str | None = None
    #: When the evidence behind the score was observed, and whether it is fresh.
    observed_utc: str | None = None
    stale: bool = False

    @property
    def family(self) -> str:
        return FAMILIES[self.key]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": LABELS[self.key],
            "weight": WEIGHTS[self.key],
            "family": self.family,
            "family_label": FAMILY_LABELS[self.family],
            "available": self.available,
            # Never 0 for an absent component: the client renders the word.
            "score": self.score if self.available else None,
            "display": (f"{self.score:.0f}" if self.available and self.score is not None else "Unavailable"),
            "basis": self.basis,
            "detail": self.detail,
            "requires": self.requires,
            "limitation": self.limitation,
            "unavailable_reason": None if self.available else (self.reason or NO_SOURCE),
            "observed_utc": self.observed_utc,
            "stale": self.stale,
        }


class RffService:
    """Computes the provisional RFF index from persisted evidence only."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.tapes = MarketTapeService(session)

    # -- public -----------------------------------------------------------
    def for_game(
        self, *, game_id: UUID, market_type: str = "MONEYLINE", now: datetime | None = None,
        include_tape: bool = False,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        tape = self.tapes.tape(game_id=game_id, market_type=market_type, now=now)
        components = [
            self._public_concentration(),
            self._sportsbook_relief(),
            self._reverse_line_movement(tape, now),
            self._late_movement(tape, now),
            self._cross_book_disagreement(tape, now),
            self._model_market_disagreement(tape, now),
            self._information_uncertainty(game_id, tape, now),
            self._social_narrative(),
        ]
        summary = self._score(components)
        earlier = self._earlier_score(tape, components, now)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "data_mode": "live",
            "generated_at_utc": now.isoformat(),
            "game_id": str(game_id),
            "label": PUBLIC_LABEL,
            "description": PUBLIC_DESCRIPTION,
            "market_type": market_type.upper(),
            "event": tape["event"],
            "components": [component.as_dict() for component in components],
            "missing_requirements": [
                {"key": component.key, "label": LABELS[component.key], "weight": WEIGHTS[component.key],
                 "family": component.family, "reason": component.reason or NO_SOURCE,
                 "requires": component.requires}
                for component in components if not component.available
            ],
            "policy": {
                "min_coverage_provisional": MIN_COVERAGE_PROVISIONAL,
                "min_coverage_published": MIN_COVERAGE_PUBLISHED,
                "min_components_published": MIN_COMPONENTS_PUBLISHED,
                "min_families_published": MIN_FAMILIES_PUBLISHED,
                "max_category_interval_points": MAX_CATEGORY_INTERVAL_POINTS,
                "statement": "Missing evidence widens the interval and can block publication. It "
                             "is never scored as zero and never imputed.",
            },
            **summary,
            "earlier": earlier,
            "explanation": self._explanation(components, summary, earlier),
            "share_card": self._share_card(tape, components, summary),
            "authority": {
                "affects_forecast": False,
                "affects_adjusted_edge": False,
                "creates_or_suppresses_position": False,
                "changes_threshold": False,
                "grants_wagering_authority": False,
                "statement": "RFF is a research index. It is not an input to any forecast, "
                             "threshold, decision or position, and it is not a betting signal.",
            },
            "sources": self._sources(tape),
            "requirement": tape.get("requirement"),
        }
        if include_tape:
            payload["tape"] = tape
        return payload

    def board(
        self, *, game_ids: list[UUID], market_type: str = "MONEYLINE",
        now: datetime | None = None, limit: int | None = None,
    ) -> dict[str, Any]:
        """Three sections, never one ranking.

        Only games whose evidence supports a composite are ranked. Provisional
        reads are kept apart with their interval, and games below the coverage
        floor are listed with what is missing -- never with a number or a band
        that would invite comparison with a fully evidenced game.
        """

        now = now or datetime.now(UTC)
        entries = [self.for_game(game_id=game_id, market_type=market_type, now=now)
                   for game_id in game_ids]
        published = [entry for entry in entries if entry["status"] == STATUS_PUBLISHED]
        provisional = [entry for entry in entries if entry["status"] == STATUS_PROVISIONAL]
        insufficient = [entry for entry in entries if entry["status"] == STATUS_INSUFFICIENT]
        published.sort(key=lambda entry: entry["score"], reverse=True)
        # Provisional reads are ordered by evidence, not by their known
        # contribution: ordering them by number would be the ranking this
        # section exists to avoid.
        provisional.sort(key=lambda entry: (-entry["evidence_coverage"], entry["known_contribution"]))
        insufficient.sort(key=lambda entry: -entry["evidence_coverage"])
        if limit is not None:
            published = published[:limit]
        return {
            "schema_version": SCHEMA_VERSION, "data_mode": "live",
            "generated_at_utc": now.isoformat(),
            "label": PUBLIC_LABEL, "description": PUBLIC_DESCRIPTION,
            "market_type": market_type.upper(),
            # `games` stays the ranked, publishable set: a caller that reads it
            # gets only comparable games.
            "games": published,
            "published": published,
            "provisional": provisional,
            "insufficient": insufficient,
            "section_notes": {
                "published": "Enough evidence for a composite: at least "
                             f"{MIN_COVERAGE_PUBLISHED * 100:.0f}% coverage, "
                             f"{MIN_COMPONENTS_PUBLISHED} components and "
                             f"{MIN_FAMILIES_PUBLISHED} evidence families. Ranked.",
                "provisional": "Half the measure or more, but not enough to publish a number. "
                               "Known contribution and plausible range only; not ranked against "
                               "published games.",
                "insufficient": "Below half the measure. No score and no category: what is shown "
                                "is what was observed and what is still needed.",
            },
            "counts": {"published": len(published), "provisional": len(provisional),
                       "insufficient": len(insufficient)},
            "unscored": [
                {"game_id": entry["game_id"], "event": entry["event"],
                 "evidence_coverage": entry["evidence_coverage"],
                 "requirement": entry["requirement"] or "Not enough evidence to publish a score."}
                for entry in insufficient
            ],
            "weights": WEIGHTS,
            "families": {key: FAMILY_LABELS[value] for key, value in FAMILIES.items()},
            "correlated_components": list(CORRELATED),
            "policy": {
                "min_coverage_provisional": MIN_COVERAGE_PROVISIONAL,
                "min_coverage_published": MIN_COVERAGE_PUBLISHED,
                "min_components_published": MIN_COMPONENTS_PUBLISHED,
                "min_families_published": MIN_FAMILIES_PUBLISHED,
                "max_category_interval_points": MAX_CATEGORY_INTERVAL_POINTS,
            },
            "bands": [{"floor": floor, "key": key, "label": label, "description": description}
                      for floor, key, label, description in BANDS],
        }

    # -- components -------------------------------------------------------
    def _public_concentration(self) -> Component:
        return Component(
            key=PUBLIC_CONCENTRATION, available=False,
            basis="Share of public tickets and handle on each side, at the same book, "
                  "at the same instant as the price.",
            requires="A public betting-percentage source (ticket count and handle share by "
                     "side, per book). AISP collects no such feed, and no price series "
                     "substitutes for it: a price says where a book will trade, not who is on it.",
            reason=NO_SOURCE,
        )

    def _sportsbook_relief(self) -> Component:
        return Component(
            key=SPORTSBOOK_RELIEF, available=False,
            basis="Whether a price move reduces the book's exposure on the side the public "
                  "is holding.",
            requires="Depends entirely on public concentration; without handle share there "
                     "is no exposure to relieve. Price concentration is not liability and is "
                     "never presented as if it were.",
            reason=DEPENDS_ON_UNAVAILABLE,
        )

    def _reverse_line_movement(self, tape: dict[str, Any], now: datetime) -> Component:
        milestones = tape.get("milestones") or {}
        opening, latest = milestones.get("opening"), milestones.get("latest")
        limitation = (
            "True reverse line movement compares the price move against the direction of "
            "public tickets. That direction is unavailable, so this measures only the "
            "price half: the opening favourite getting cheaper."
        )
        if not opening or not latest or opening["consensus_no_vig_a"] is None:
            return Component(
                key=REVERSE_LINE_MOVEMENT, available=False,
                basis="Consensus no-vig movement against the opening favourite.",
                requires="At least two priced frames with a complete two-sided quote.",
                limitation=limitation, reason=INSUFFICIENT_OBSERVATIONS,
            )
        stale = _stale(latest.get("t"), now, MARKET_MAX_AGE_MINUTES)
        if stale:
            return Component(
                key=REVERSE_LINE_MOVEMENT, available=False,
                basis="Consensus no-vig movement against the opening favourite.",
                requires=f"A consensus frame observed within {MARKET_MAX_AGE_MINUTES} minutes; "
                         f"the newest is {latest.get('t')}.",
                limitation=limitation, reason=STALE_EVIDENCE,
                observed_utc=latest.get("t"), stale=True,
            )
        open_a, last_a = opening["consensus_no_vig_a"], latest["consensus_no_vig_a"]
        favourite_is_a = open_a >= 0.5
        # Adverse move: the side that opened favourite got cheaper.
        adverse = (open_a - last_a) if favourite_is_a else (last_a - open_a)
        points = max(0.0, adverse * 100)
        return Component(
            key=REVERSE_LINE_MOVEMENT, available=True,
            score=_ramp(points, saturate_at=4.0),
            basis="Points of consensus no-vig probability the opening favourite LOST between "
                  "the opening frame and the latest, saturating at 4.0 points.",
            detail={
                "opening_no_vig_a": open_a, "latest_no_vig_a": last_a,
                "favourite_side": "a" if favourite_is_a else "b",
                "adverse_move_points": round(points, 4),
                "saturates_at_points": 4.0,
            },
            limitation=limitation, observed_utc=latest.get("t"),
        )

    def _late_movement(self, tape: dict[str, Any], now: datetime) -> Component:
        frames = [frame for frame in tape.get("frames") or []
                  if frame["consensus_no_vig_a"] is not None]
        cutoff = tape["event"].get("decision_cutoff_utc")
        if len(frames) < 2:
            return Component(
                key=LATE_MOVEMENT, available=False,
                basis="Consensus movement inside the late window before the decision cutoff.",
                requires="At least two priced frames.", reason=INSUFFICIENT_OBSERVATIONS,
            )
        anchor = datetime.fromisoformat(cutoff) if cutoff else now
        boundary = anchor - timedelta(minutes=LATE_WINDOW_MINUTES)
        late = [frame for frame in frames if datetime.fromisoformat(frame["t"]) >= boundary]
        if len(late) < 2:
            return Component(
                key=LATE_MOVEMENT, available=False,
                basis=f"Consensus movement inside the {LATE_WINDOW_MINUTES}-minute window "
                      "before the decision cutoff.",
                requires=f"At least two priced frames inside the final {LATE_WINDOW_MINUTES} "
                         "minutes; this market has "
                         f"{len(late)}.",
                detail={"window_start_utc": boundary.isoformat(), "frames_in_window": len(late)},
                # Before the window opens this is not missing data, it is a
                # measurement whose time has not come.
                reason=NOT_YET_OBSERVABLE if boundary > now else INSUFFICIENT_OBSERVATIONS,
            )
        move = abs(late[-1]["consensus_no_vig_a"] - late[0]["consensus_no_vig_a"]) * 100
        return Component(
            key=LATE_MOVEMENT, available=True,
            score=_ramp(move, saturate_at=3.0),
            basis=f"Absolute consensus no-vig movement in the final {LATE_WINDOW_MINUTES} "
                  "minutes before the cutoff, saturating at 3.0 points.",
            detail={
                "window_start_utc": boundary.isoformat(),
                "frames_in_window": len(late),
                "from_no_vig_a": late[0]["consensus_no_vig_a"],
                "to_no_vig_a": late[-1]["consensus_no_vig_a"],
                "move_points": round(move, 4),
                "saturates_at_points": 3.0,
            },
            observed_utc=late[-1]["t"],
        )

    def _cross_book_disagreement(self, tape: dict[str, Any], now: datetime) -> Component:
        frames = [frame for frame in tape.get("frames") or [] if frame["usable_books"] >= 2]
        if not frames:
            return Component(
                key=CROSS_BOOK_DISAGREEMENT, available=False,
                basis="Spread between the highest and lowest no-vig probability across books "
                      "at the latest frame.",
                requires="At least two books with a complete, fresh two-sided quote at the "
                         "same instant.", reason=INSUFFICIENT_OBSERVATIONS,
            )
        latest = frames[-1]
        if _stale(latest.get("t"), now, MARKET_MAX_AGE_MINUTES):
            return Component(
                key=CROSS_BOOK_DISAGREEMENT, available=False,
                basis="Spread between the highest and lowest no-vig probability across books.",
                requires=f"A multi-book frame observed within {MARKET_MAX_AGE_MINUTES} minutes; "
                         f"the newest is {latest.get('t')}.",
                reason=STALE_EVIDENCE, observed_utc=latest.get("t"), stale=True,
            )
        low, high = latest["range_a"]
        spread = (high - low) * 100
        return Component(
            key=CROSS_BOOK_DISAGREEMENT, available=True,
            score=_ramp(spread, saturate_at=5.0),
            basis="Points between the highest and lowest per-book no-vig probability at the "
                  "latest frame, saturating at 5.0 points.",
            detail={
                "t": latest["t"], "books": latest["usable_books"],
                "low": low, "high": high, "spread_points": round(spread, 4),
                "standard_deviation": latest["dispersion"],
                "saturates_at_points": 5.0,
            },
            observed_utc=latest["t"],
        )

    def _model_market_disagreement(self, tape: dict[str, Any], now: datetime) -> Component:
        series = tape.get("model_probability_series") or []
        frames = [frame for frame in tape.get("frames") or []
                  if frame["consensus_no_vig_home"] is not None]
        note = ("A gap between the model and the market is a disagreement to look at. It is "
                "not an edge: an edge is a policy decision made after penalties, and this "
                "index is not part of that decision.")
        if not series or not frames:
            return Component(
                key=MODEL_MARKET_DISAGREEMENT, available=False,
                basis="Absolute gap between the model's home-win probability and the "
                      "consensus no-vig home probability.",
                requires="A published forecast and a consensus with an identified home side.",
                limitation=note, reason=INSUFFICIENT_OBSERVATIONS,
            )
        if _stale(frames[-1].get("t"), now, MARKET_MAX_AGE_MINUTES):
            return Component(
                key=MODEL_MARKET_DISAGREEMENT, available=False,
                basis="Absolute gap between the model and the consensus no-vig home probability.",
                requires=f"A consensus frame observed within {MARKET_MAX_AGE_MINUTES} minutes; "
                         f"the newest is {frames[-1].get('t')}.",
                limitation=note, reason=STALE_EVIDENCE, observed_utc=frames[-1].get("t"), stale=True,
            )
        model = series[-1]["home_win_probability"]
        market = frames[-1]["consensus_no_vig_home"]
        gap = abs(model - market) * 100
        return Component(
            key=MODEL_MARKET_DISAGREEMENT, available=True,
            score=_ramp(gap, saturate_at=8.0),
            basis="Points between the latest model home-win probability and the latest "
                  "consensus no-vig home probability, saturating at 8.0 points.",
            detail={
                "model_home_probability": model, "market_home_probability": market,
                "gap_points": round(gap, 4), "model_version": series[-1]["version"],
                "model_generated_at_utc": series[-1]["generated_at_utc"],
                "saturates_at_points": 8.0,
            },
            limitation=note, observed_utc=frames[-1].get("t"),
        )

    def _information_uncertainty(self, game_id: UUID, tape: dict[str, Any], now: datetime) -> Component:
        """Unresolved GAME information, never AISP's own data gaps.

        Four things were previously added together and called uncertainty:

          1. evidence AISP holds that says the card is genuinely unsettled
             (a lineup posted but not confirmed, a starter still probable);
          2. evidence AISP simply does not hold (no lineup rows at all);
          3. AISP's own pipeline completeness (the forecast readiness score);
          4. contradictions between sources.

        Only (1) is a fact about the game. (2) and (3) are facts about AISP, and
        scoring them made sparse collection look like market unrest -- a game
        with no lineup feed scored 100. They are now reported beside the
        component as ``aisp_evidence_completeness`` and never added to it.

        The component is also only observable near first pitch/kickoff: hours
        earlier, "not confirmed yet" is the normal state of the world, so the
        component is NOT_YET_OBSERVABLE rather than maximal.
        """

        lineups = list(self.session.execute(
            select(LineupObservation.team_id, LineupObservation.lineup_status,
                   LineupObservation.is_complete, LineupObservation.observed_at)
            .where(LineupObservation.game_id == game_id)
            .order_by(LineupObservation.observed_at.desc())
        ).all())
        starters = list(self.session.execute(
            select(StarterObservation.team_id, StarterObservation.confirmation_state,
                   StarterObservation.observed_at)
            .where(StarterObservation.game_id == game_id)
            .order_by(StarterObservation.observed_at.desc())
        ).all())
        readiness = self.session.scalar(
            select(Forecast.readiness_score).where(Forecast.game_id == game_id)
            .order_by(Forecast.generated_at.desc()).limit(1)
        )
        starts_at = (tape.get("event") or {}).get("starts_at_utc")
        completeness = {
            "lineup_sides_observed": len({row[0] for row in lineups}),
            "starter_sides_observed": len({row[0] for row in starters}),
            "forecast_readiness_score": None if readiness is None else float(readiness),
            "note": "AISP's own collection completeness. It is reported so a reader can see how "
                    "much evidence exists, and it is never added to the RFF score: missing data "
                    "is an absence of evidence, not market unrest.",
        }

        opens_at = None
        if starts_at:
            opens_at = datetime.fromisoformat(starts_at) - timedelta(hours=INFORMATION_WINDOW_HOURS)
            if opens_at.tzinfo is None:
                opens_at = opens_at.replace(tzinfo=UTC)
        if opens_at is not None and now < opens_at:
            return Component(
                key=INFORMATION_UNCERTAINTY, available=False,
                basis="Unresolved lineup and starter evidence inside the information window.",
                requires=f"The information window opens {INFORMATION_WINDOW_HOURS} hours before "
                         f"the start ({opens_at.isoformat()}). Before it, an unconfirmed card is "
                         "the normal state of the world, not evidence of unrest.",
                reason=NOT_YET_OBSERVABLE,
                detail={"window_opens_utc": opens_at.isoformat(), **completeness},
            )

        # One resolution state per side, from evidence that EXISTS. A side with
        # no observation at all cannot be judged.
        latest_lineup: dict[Any, tuple[str, bool, Any]] = {}
        for team_id, status, complete, observed in lineups:
            latest_lineup.setdefault(team_id, (str(status or ""), bool(complete), observed))
        latest_starter: dict[Any, tuple[str, Any]] = {}
        for team_id, state, observed in starters:
            latest_starter.setdefault(team_id, (str(state or ""), observed))

        sides = sorted({*latest_lineup, *latest_starter}, key=str)
        observed_times = [row[2] for row in latest_lineup.values()] + [row[1] for row in latest_starter.values()]
        newest = max((t for t in observed_times if t is not None), default=None)
        newest_iso = newest.isoformat() if newest is not None else None
        if len(sides) < 2:
            return Component(
                key=INFORMATION_UNCERTAINTY, available=False,
                basis="Unresolved lineup and starter evidence for both sides.",
                requires="A lineup or starter observation for BOTH sides. AISP holds evidence for "
                         f"{len(sides)} side(s), which is a gap in collection rather than a fact "
                         "about the game, so it is not scored.",
                reason=INCOMPLETE_EVIDENCE,
                detail=completeness, observed_utc=newest_iso,
            )
        if _stale(newest_iso, now, INFORMATION_MAX_AGE_MINUTES):
            return Component(
                key=INFORMATION_UNCERTAINTY, available=False,
                basis="Unresolved lineup and starter evidence for both sides.",
                requires=f"An observation within {INFORMATION_MAX_AGE_MINUTES} minutes; the newest "
                         f"is {newest_iso}.",
                reason=STALE_EVIDENCE, detail=completeness, observed_utc=newest_iso, stale=True,
            )

        per_side: list[float] = []
        detail_sides: list[dict[str, Any]] = []
        for side in sides:
            kinds: list[tuple[str, bool]] = []
            lineup = latest_lineup.get(side)
            if lineup is not None:
                status, complete, _ = lineup
                kinds.append(("lineup", complete and status.upper() == "CONFIRMED"))
            starter = latest_starter.get(side)
            if starter is not None:
                state, _ = starter
                kinds.append(("starter", state.upper() == "CONFIRMED"))
            unresolved = sum(1 for _, resolved in kinds if not resolved)
            per_side.append((unresolved / len(kinds)) * 100.0 if kinds else 0.0)
            detail_sides.append({
                "team_id": str(side),
                "evidence_kinds": [kind for kind, _ in kinds],
                "unresolved_kinds": [kind for kind, resolved in kinds if not resolved],
            })
        score = round(sum(per_side) / len(per_side), 1)
        return Component(
            key=INFORMATION_UNCERTAINTY, available=True, score=score,
            basis="Share of the lineup and starter evidence AISP HOLDS that is still unresolved, "
                  "averaged over the two sides, inside the information window. Evidence AISP does "
                  "not hold is never counted as uncertainty.",
            detail={"sides": detail_sides, "window_opens_utc": opens_at.isoformat() if opens_at else None,
                    **completeness},
            observed_utc=newest_iso,
            limitation="Measures only what is unresolved in the evidence AISP holds. It says "
                       "nothing about information AISP has no source for.",
        )

    def _social_narrative(self) -> Component:
        return Component(
            key=SOCIAL_NARRATIVE, available=False,
            basis="Abnormal social volume or a narrative spike around one side of the game.",
            requires="A social-volume feed and a baseline to call a spike abnormal. This "
                     "assignment builds the ingestion boundary for information events but "
                     "calls no social platform, so there is nothing to measure yet.",
            reason=NO_SOURCE,
        )

    # -- scoring ----------------------------------------------------------
    @staticmethod
    def _score(components: list[Component]) -> dict[str, Any]:
        """Fixed-weight evidence accounting, then the publication policy.

        The denominator is always the full weight of the measure. What is not
        observed is not averaged away and not set to zero: it becomes the width
        of the interval, and the policy decides what may be shown.
        """

        available = [component for component in components if component.available]
        total_weight = sum(WEIGHTS.values())
        coverage = sum(WEIGHTS[component.key] for component in available)
        known = sum(WEIGHTS[component.key] * (component.score or 0.0) for component in available)
        unknown_weight = max(0.0, total_weight - coverage)
        lower = known
        upper = known + unknown_weight * 100.0
        families = sorted({component.family for component in available})
        observed_average = (known / coverage) if coverage > 0 else None

        status, blockers = _publication_status(available, coverage, families)
        score = round(lower, 1) if status == STATUS_PUBLISHED else None
        band, category_reason = _category(status, lower, upper, available)
        contributions = sorted(
            [
                {
                    "key": component.key, "label": LABELS[component.key],
                    "family": component.family, "score": component.score,
                    "weight": WEIGHTS[component.key],
                    # Share of the FULL measure, not of the observed part: a
                    # component can never be "100% of the score" again.
                    "share_of_measure": round(
                        (WEIGHTS[component.key] * (component.score or 0.0)) / 100.0, 4),
                    "share_of_known_contribution": round(
                        (WEIGHTS[component.key] * (component.score or 0.0)) / known, 4) if known > 0 else 0.0,
                }
                for component in available
            ],
            key=lambda entry: entry["share_of_known_contribution"], reverse=True,
        )
        return {
            "status": status,
            "score": score,
            "known_contribution": round(known, 1),
            "unknown_weight": round(unknown_weight, 4),
            "score_range": [round(lower, 1), round(upper, 1)],
            "lower_bound": round(lower, 1),
            "upper_bound": round(upper, 1),
            "interval_width": round(upper - lower, 1),
            "score_basis": (
                "Fixed weights over the whole measure: known contribution is the sum of "
                "weight x component score for the components that have data, and the interval "
                "adds the unobserved weight at 0 and at 100. Unavailable is never 0 and never "
                "imputed."
            ),
            "band": band,
            "category_reason": category_reason,
            "evidence_coverage": round(coverage / total_weight, 4),
            "available_components": len(available),
            "unavailable_components": len(components) - len(available),
            "families_present": families,
            "family_labels": [FAMILY_LABELS[family] for family in families],
            "publication_blockers": blockers,
            "rankable": status == STATUS_PUBLISHED,
            "diagnostics": {
                "observedComponentAverage": None if observed_average is None else round(observed_average, 1),
                "note": "Internal diagnostic only: the mean over the components that happen to "
                        "have data. It is not RFF, it never sets a category, and games with "
                        "different coverage are never ranked by it.",
                "correlated_components": [
                    entry for entry in CORRELATED
                    if all(key in {component.key for component in available} for key in entry["components"])
                ],
            },
            "contributions": contributions,
        }

    def _earlier_score(
        self, tape: dict[str, Any], components: list[Component], now: datetime,
    ) -> dict[str, Any] | None:
        """The same measure at the opening frame, for a current-versus-earlier read.

        Only the components that are functions of the tape can be recomputed
        historically; the rest are read at read time and are excluded from the
        comparison rather than back-dated, which would be a fabrication.
        """

        frames = [frame for frame in tape.get("frames") or []
                  if frame["consensus_no_vig_a"] is not None]
        if len(frames) < 2:
            return None
        opening = frames[0]
        low, high = (opening["range_a"] or [None, None])
        historic: list[Component] = []
        if opening["usable_books"] >= 2 and low is not None:
            historic.append(Component(
                key=CROSS_BOOK_DISAGREEMENT, available=True,
                score=_ramp((high - low) * 100, saturate_at=5.0), basis="at the opening frame",
            ))
        series = tape.get("model_probability_series") or []
        if series and opening["consensus_no_vig_home"] is not None:
            first = min(series, key=lambda entry: entry["generated_at_utc"] or "")
            historic.append(Component(
                key=MODEL_MARKET_DISAGREEMENT, available=True,
                score=_ramp(abs(first["home_win_probability"] - opening["consensus_no_vig_home"]) * 100,
                            saturate_at=8.0),
                basis="at the opening frame, against the first published forecast",
            ))
        if not historic:
            return None
        comparable = [component.key for component in historic]
        current = {component.key: component.score for component in components
                   if component.available and component.key in comparable}
        if len(current) != len(historic):
            return None
        weight = sum(WEIGHTS[key] for key in comparable)
        earlier_score = sum(WEIGHTS[c.key] * (c.score or 0.0) for c in historic) / weight
        current_score = sum(WEIGHTS[key] * (current[key] or 0.0) for key in comparable) / weight
        return {
            "t": opening["t"],
            "comparable_components": comparable,
            "earlier_score": round(earlier_score, 1),
            "current_score": round(current_score, 1),
            "change": round(current_score - earlier_score, 1),
            "note": "Only the components that are functions of the price tape can be "
                    "recomputed at an earlier instant. The rest are read at read time and "
                    "are left out of this comparison rather than back-dated.",
        }

    # -- narration --------------------------------------------------------
    @staticmethod
    def _explanation(
        components: list[Component], summary: dict[str, Any], earlier: dict[str, Any] | None,
    ) -> str:
        """Plain language that leads with the evidence, not with a number."""

        coverage = summary["evidence_coverage"] * 100
        available = [component for component in components if component.available]
        missing = [LABELS[component.key] for component in components if not component.available]
        drivers = [entry["label"].lower() for entry in summary["contributions"][:3]
                   if (entry["score"] or 0) >= 20]
        observed = (f"Observed: {drivers[0]}" if len(drivers) == 1
                    else f"Observed: {', '.join(drivers[:-1])} and {drivers[-1]}" if drivers
                    else "Nothing observed is scoring high")

        if summary["status"] == STATUS_INSUFFICIENT:
            parts = [
                f"Insufficient evidence for an RFF score: {coverage:.0f}% coverage from "
                f"{len(available)} of {len(components)} components.",
                f"{observed}.",
                f"The measure could sit anywhere between {summary['lower_bound']:.0f} and "
                f"{summary['upper_bound']:.0f} once the missing components are known, so no number "
                "and no category is published and this game is not ranked.",
            ]
        elif summary["status"] == STATUS_PROVISIONAL:
            parts = [
                f"Provisional read · {coverage:.0f}% coverage.",
                f"Known contribution {summary['known_contribution']:.0f} of 100, with the rest of "
                f"the measure unobserved: the plausible range is {summary['lower_bound']:.0f}–"
                f"{summary['upper_bound']:.0f}.",
                f"{observed}.",
                "It is not comparable with a fully evidenced game and carries no category.",
            ]
        else:
            parts = [
                f"RFF {summary['score']:.0f} ({summary['band']['label']}) · {coverage:.0f}% coverage "
                f"from {len(available)} components across {len(summary['families_present'])} evidence "
                "families.",
                f"Plausible range {summary['lower_bound']:.0f}–{summary['upper_bound']:.0f}.",
                f"{observed}.",
            ]
        if missing:
            parts.append(
                ("The " + missing[0].lower() + " component is unavailable."
                 if len(missing) == 1 else
                 "Unavailable: " + ", ".join(m.lower() for m in missing) + "."))
        if earlier:
            direction = "up" if earlier["change"] > 0 else "down" if earlier["change"] < 0 else "flat"
            parts.append(
                f"On the components that can be recomputed at the opening, they are {direction} "
                f"{abs(earlier['change']):.0f} points since then.")
        return " ".join(parts)

    @staticmethod
    def _share_card(
        tape: dict[str, Any], components: list[Component], summary: dict[str, Any],
    ) -> dict[str, Any]:
        home, away = tape["event"].get("home"), tape["event"].get("away")
        triggers = [
            {"label": LABELS[component.key], "score": component.score, "why": component.basis}
            for component in components
            if component.available and (component.score or 0) >= 20
        ][:3]
        headline = {
            STATUS_PUBLISHED: None if summary["score"] is None else f"RFF {summary['score']:.0f}",
            STATUS_PROVISIONAL: (f"Provisional · known {summary['known_contribution']:.0f}, range "
                                 f"{summary['lower_bound']:.0f}–{summary['upper_bound']:.0f}"),
            STATUS_INSUFFICIENT: "Insufficient evidence",
        }[summary["status"]]
        return {
            "matchup": (f"{away['abbreviation']} @ {home['abbreviation']}"
                        if home and away else None),
            "headline": headline,
            "status": summary["status"],
            "band": summary["band"]["label"],
            "coverage": summary["evidence_coverage"],
            "range": [summary["lower_bound"], summary["upper_bound"]],
            "triggers": triggers,
            "disclaimer": "Research index. Not evidence that a game is manipulated, and not "
                          "a betting signal.",
        }

    @staticmethod
    def _sources(tape: dict[str, Any]) -> dict[str, Any]:
        return {
            "market": {
                "providers": tape.get("providers"),
                "books": [
                    {"name": book["name"], "provider": book["provider"],
                     "observations": book["observations"]}
                    for book in tape.get("books") or []
                ],
                "first_observed_utc": tape.get("first_observed_utc"),
                "last_observed_utc": tape.get("last_observed_utc"),
                "observed_instants": tape.get("observed_instants"),
            },
            "forecast": {
                "versions": len(tape.get("model_probability_series") or []),
                "latest_generated_at_utc": (
                    (tape.get("model_probability_series") or [{}])[-1].get("generated_at_utc")
                ),
            },
            "public_money": None,
            "social": None,
        }


def book_label(name: str) -> str:
    """Re-exported so a caller labels a book the same way the tape does."""

    return display_name(type("B", (), {"name": name, "key": ""})())


__all__ = [
    "BANDS", "CATEGORY_UNCERTAIN", "CORRELATED", "CROSS_BOOK_DISAGREEMENT", "Component",
    "DEPENDS_ON_UNAVAILABLE", "FAMILIES", "FAMILY_LABELS", "INCOMPLETE_EVIDENCE",
    "INFORMATION_UNCERTAINTY", "INFORMATION_WINDOW_HOURS", "INSUFFICIENT_OBSERVATIONS",
    "LABELS", "LATE_MOVEMENT", "MARKET_MAX_AGE_MINUTES", "MAX_CATEGORY_INTERVAL_POINTS",
    "MIN_COMPONENTS_PUBLISHED", "MIN_COVERAGE_PROVISIONAL", "MIN_COVERAGE_PUBLISHED",
    "MIN_FAMILIES_PUBLISHED", "MODEL_MARKET_DISAGREEMENT", "NOT_YET_OBSERVABLE", "NO_SOURCE",
    "PUBLIC_CONCENTRATION", "PUBLIC_DESCRIPTION", "PUBLIC_LABEL", "REVERSE_LINE_MOVEMENT",
    "RffService", "SCHEMA_VERSION", "SOCIAL_NARRATIVE", "SPORTSBOOK_RELIEF", "STALE_EVIDENCE",
    "STATUS_INSUFFICIENT", "STATUS_PROVISIONAL", "STATUS_PUBLISHED", "WEIGHTS", "band_for",
]
