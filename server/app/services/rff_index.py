"""RFF Index — Ripe For Fuckery.

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
PUBLIC_LABEL = "RFF Index — Ripe For Fuckery"
PUBLIC_DESCRIPTION = (
    "A research measure of market imbalance, unusual movement and unresolved "
    "information. It is not evidence that a game is manipulated."
)
STATUS_PROVISIONAL = "PROVISIONAL_RFF"

#: The window that counts as "late" when the cutoff has not arrived yet.
LATE_WINDOW_MINUTES = 120

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
BANDS: tuple[tuple[int, str, str], ...] = (
    (0, "Calm", "The market agrees with itself and has not moved much."),
    (30, "Curious", "Something is a little off — worth a look, not an alarm."),
    (55, "Spicy", "Real disagreement or real movement, with evidence behind it."),
    (75, "Maximum Fuckery", "As unsettled as this measure can see. Still not proof of anything."),
)


def band_for(score: float | None) -> dict[str, Any]:
    if score is None:
        return {"key": "UNSCORED", "label": "Unscored", "description": "No component has data."}
    chosen = BANDS[0]
    for floor, label, description in BANDS:
        if score >= floor:
            chosen = (floor, label, description)
    return {"key": chosen[1].upper().replace(" ", "_"), "label": chosen[1],
            "description": chosen[2], "floor": chosen[0]}


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": LABELS[self.key],
            "weight": WEIGHTS[self.key],
            "available": self.available,
            # Never 0 for an absent component: the client renders the word.
            "score": self.score if self.available else None,
            "display": (f"{self.score:.0f}" if self.available and self.score is not None else "Unavailable"),
            "basis": self.basis,
            "detail": self.detail,
            "requires": self.requires,
            "limitation": self.limitation,
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
            self._reverse_line_movement(tape),
            self._late_movement(tape, now),
            self._cross_book_disagreement(tape),
            self._model_market_disagreement(tape),
            self._information_uncertainty(game_id, now),
            self._social_narrative(),
        ]
        summary = self._score(components)
        earlier = self._earlier_score(tape, components, now)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "data_mode": "live",
            "generated_at_utc": now.isoformat(),
            "game_id": str(game_id),
            "status": STATUS_PROVISIONAL,
            "label": PUBLIC_LABEL,
            "description": PUBLIC_DESCRIPTION,
            "market_type": market_type.upper(),
            "event": tape["event"],
            "components": [component.as_dict() for component in components],
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
        now = now or datetime.now(UTC)
        entries = [self.for_game(game_id=game_id, market_type=market_type, now=now)
                   for game_id in game_ids]
        scored = [entry for entry in entries if entry["score"] is not None]
        scored.sort(key=lambda entry: entry["score"], reverse=True)
        if limit is not None:
            scored = scored[:limit]
        return {
            "schema_version": SCHEMA_VERSION, "data_mode": "live",
            "generated_at_utc": now.isoformat(),
            "status": STATUS_PROVISIONAL, "label": PUBLIC_LABEL, "description": PUBLIC_DESCRIPTION,
            "market_type": market_type.upper(),
            "games": scored,
            "unscored": [
                {"game_id": entry["game_id"], "event": entry["event"],
                 "requirement": entry["requirement"] or "No component has data for this game."}
                for entry in entries if entry["score"] is None
            ],
            "weights": WEIGHTS,
            "bands": [{"floor": floor, "label": label, "description": description}
                      for floor, label, description in BANDS],
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
        )

    def _sportsbook_relief(self) -> Component:
        return Component(
            key=SPORTSBOOK_RELIEF, available=False,
            basis="Whether a price move reduces the book's exposure on the side the public "
                  "is holding.",
            requires="Depends entirely on public concentration; without handle share there "
                     "is no exposure to relieve. Price concentration is not liability and is "
                     "never presented as if it were.",
        )

    def _reverse_line_movement(self, tape: dict[str, Any]) -> Component:
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
                limitation=limitation,
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
            limitation=limitation,
        )

    def _late_movement(self, tape: dict[str, Any], now: datetime) -> Component:
        frames = [frame for frame in tape.get("frames") or []
                  if frame["consensus_no_vig_a"] is not None]
        cutoff = tape["event"].get("decision_cutoff_utc")
        if len(frames) < 2:
            return Component(
                key=LATE_MOVEMENT, available=False,
                basis="Consensus movement inside the late window before the decision cutoff.",
                requires="At least two priced frames.",
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
        )

    def _cross_book_disagreement(self, tape: dict[str, Any]) -> Component:
        frames = [frame for frame in tape.get("frames") or [] if frame["usable_books"] >= 2]
        if not frames:
            return Component(
                key=CROSS_BOOK_DISAGREEMENT, available=False,
                basis="Spread between the highest and lowest no-vig probability across books "
                      "at the latest frame.",
                requires="At least two books with a complete, fresh two-sided quote at the "
                         "same instant.",
            )
        latest = frames[-1]
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
        )

    def _model_market_disagreement(self, tape: dict[str, Any]) -> Component:
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
                limitation=note,
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
            limitation=note,
        )

    def _information_uncertainty(self, game_id: UUID, now: datetime) -> Component:
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
        if not lineups and not starters and readiness is None:
            return Component(
                key=INFORMATION_UNCERTAINTY, available=False,
                basis="Unresolved lineup, starter and readiness evidence at read time.",
                requires="At least one persisted lineup observation, starter observation or "
                         "forecast readiness score for this game.",
            )

        latest_lineup: dict[Any, tuple[str, bool]] = {}
        for team_id, status, complete, _ in lineups:
            latest_lineup.setdefault(team_id, (str(status or ""), bool(complete)))
        latest_starter: dict[Any, str] = {}
        for team_id, state, _ in starters:
            latest_starter.setdefault(team_id, str(state or ""))

        # A lineup is only resolved when it is CONFIRMED and complete. The
        # schema admits PROJECTED and EXPECTED, and both mean the card can
        # still change; a missing side has no row at all and is counted below.
        unresolved_lineups = sum(1 for status, complete in latest_lineup.values()
                                 if not complete or status.upper() != "CONFIRMED")
        missing_lineups = max(0, 2 - len(latest_lineup))
        unconfirmed_starters = sum(1 for state in latest_starter.values()
                                   if state.upper() != "CONFIRMED")
        missing_starters = max(0, 2 - len(latest_starter))
        readiness_gap = 0.0 if readiness is None else max(0.0, 100.0 - float(readiness))

        # Four sides of evidence, each worth a quarter of the component, plus a
        # readiness term that saturates at a 40-point shortfall.
        parts = [
            ((unresolved_lineups + missing_lineups) / 2.0) * 30.0,
            ((unconfirmed_starters + missing_starters) / 2.0) * 40.0,
            _ramp(readiness_gap, saturate_at=40.0) * 0.30,
        ]
        return Component(
            key=INFORMATION_UNCERTAINTY, available=True,
            score=round(min(100.0, sum(parts)), 1),
            basis="Unresolved or missing lineup sides (30), unconfirmed or missing starters "
                  "(40) and the forecast readiness shortfall (30, saturating at a 40-point gap).",
            detail={
                "lineup_sides_observed": len(latest_lineup),
                "lineup_sides_unresolved": unresolved_lineups + missing_lineups,
                "starter_sides_observed": len(latest_starter),
                "starter_sides_unconfirmed": unconfirmed_starters + missing_starters,
                "readiness_score": None if readiness is None else float(readiness),
                "readiness_shortfall": round(readiness_gap, 2),
            },
        )

    def _social_narrative(self) -> Component:
        return Component(
            key=SOCIAL_NARRATIVE, available=False,
            basis="Abnormal social volume or a narrative spike around one side of the game.",
            requires="A social-volume feed and a baseline to call a spike abnormal. This "
                     "assignment builds the ingestion boundary for information events but "
                     "calls no social platform, so there is nothing to measure yet.",
        )

    # -- scoring ----------------------------------------------------------
    @staticmethod
    def _score(components: list[Component]) -> dict[str, Any]:
        available = [component for component in components if component.available]
        total_weight = sum(WEIGHTS.values())
        covered = sum(WEIGHTS[component.key] for component in available)
        if not available or covered <= 0:
            return {
                "score": None, "score_range": None, "band": band_for(None),
                "evidence_coverage": 0.0, "available_components": 0,
                "unavailable_components": len(components), "contributions": [],
            }
        weighted = sum(WEIGHTS[component.key] * (component.score or 0.0) for component in available)
        point = weighted / covered
        missing = total_weight - covered
        # The bounds are what the score would be if every unavailable component
        # sat at either extreme. The point estimate assumes the missing parts
        # look like the present ones, which is an assumption, so it is never
        # published without the range beside it.
        low = weighted / total_weight
        high = (weighted + missing * 100.0) / total_weight
        return {
            "score": round(point, 1),
            "score_range": [round(low, 1), round(high, 1)],
            "score_basis": "Weighted mean of the components that have data. The range is the "
                           "score if every unavailable component were 0 and if every one were 100.",
            "band": band_for(point),
            "evidence_coverage": round(covered / total_weight, 4),
            "available_components": len(available),
            "unavailable_components": len(components) - len(available),
            "contributions": sorted(
                [
                    {
                        "key": component.key, "label": LABELS[component.key],
                        "score": component.score,
                        "weight": WEIGHTS[component.key],
                        "share_of_score": round(
                            (WEIGHTS[component.key] * (component.score or 0.0)) / weighted, 4,
                        ) if weighted > 0 else 0.0,
                    }
                    for component in available
                ],
                key=lambda entry: entry["share_of_score"], reverse=True,
            ),
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
        if summary["score"] is None:
            return ("No component of this index has data for this game, so there is no score. "
                    "That is an absence of evidence, not a calm market.")
        missing = [LABELS[component.key] for component in components if not component.available]
        drivers = [entry["label"].lower() for entry in summary["contributions"][:3]
                   if (entry["score"] or 0) >= 20]
        parts = [
            f"Provisional RFF {summary['score']:.0f} · "
            f"{summary['evidence_coverage'] * 100:.0f}% evidence coverage."
        ]
        if missing:
            parts.append(
                f"{'The ' + missing[0].lower() + ' component is' if len(missing) == 1 else 'The ' + ', '.join(m.lower() for m in missing[:-1]) + ' and ' + missing[-1].lower() + ' components are'} unavailable."
            )
        if drivers:
            joined = drivers[0] if len(drivers) == 1 else ", ".join(drivers[:-1]) + " and " + drivers[-1]
            parts.append(f"The current score is driven by {joined}.")
        else:
            parts.append("No available component is scoring high; the market looks settled on "
                         "what can be measured.")
        if earlier:
            direction = "up" if earlier["change"] > 0 else "down" if earlier["change"] < 0 else "flat"
            parts.append(
                f"On the components that can be recomputed at the opening, it is {direction} "
                f"{abs(earlier['change']):.0f} points since then."
            )
        return " ".join(parts)

    @staticmethod
    def _share_card(
        tape: dict[str, Any], components: list[Component], summary: dict[str, Any],
    ) -> dict[str, Any]:
        home, away = tape["event"].get("home"), tape["event"].get("away")
        triggers = [
            {"label": LABELS[component.key], "score": component.score,
             "why": component.basis}
            for component in components
            if component.available and (component.score or 0) >= 20
        ][:3]
        return {
            "matchup": (f"{away['abbreviation']} @ {home['abbreviation']}"
                        if home and away else None),
            "headline": (None if summary["score"] is None
                         else f"Provisional RFF {summary['score']:.0f}"),
            "band": summary["band"]["label"],
            "coverage": summary["evidence_coverage"],
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
    "BANDS", "CROSS_BOOK_DISAGREEMENT", "Component", "INFORMATION_UNCERTAINTY",
    "LABELS", "LATE_MOVEMENT", "MODEL_MARKET_DISAGREEMENT", "PUBLIC_CONCENTRATION",
    "PUBLIC_DESCRIPTION", "PUBLIC_LABEL", "REVERSE_LINE_MOVEMENT", "RffService",
    "SCHEMA_VERSION", "SOCIAL_NARRATIVE", "SPORTSBOOK_RELIEF", "STATUS_PROVISIONAL",
    "WEIGHTS", "band_for",
]
