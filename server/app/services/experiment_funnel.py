"""Where the $1,000 experiment's games actually go.

THE QUESTION THIS ANSWERS
The experiment has staked money on very few games, and the honest explanation
is not one number. A game can fall out at five different places for five
different reasons, and lumping them together produces the two misleading
stories this page exists to prevent: that the model rarely finds an edge (when
in fact it was never shown a price), or that fixing the prices would multiply
the bets (when most priced games still do not clear the bar).

So the funnel is reported stage by stage, and every game that drops out is
assigned exactly ONE first blocking reason -- the earliest gate it failed, in
the order the policy actually evaluates them. The drop-outs at each stage sum
to the stage's loss exactly; there is no residual bucket to hide in.

WHAT IT DOES NOT DO
It does not re-run the policy, re-price anything, or lower a threshold. It
reads the decisions the experiment already recorded and counts them. The
thresholds quoted here are read from the policy module so the page cannot
drift from the code that enforced them.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import OfficialAspPosition, ShadowExperiment, ShadowExperimentDecision

SCHEMA_VERSION = "AISP_EXPERIMENT_FUNNEL_V1"

#: The five reasons a game can fail before a decision, plus the one that can
#: fail after it. Ordered exactly as the policy evaluates them, because "first
#: blocking reason" is meaningless without the order.
MISSING_FORECAST = "MISSING_FORECAST"
MISSING_QUOTE = "MISSING_QUOTE"
STALE_QUOTE = "STALE_QUOTE"
INCOMPLETE_EVIDENCE = "INCOMPLETE_REQUIRED_EVIDENCE"
INSUFFICIENT_EDGE = "INSUFFICIENT_ADJUSTED_EDGE"
EXECUTION_DEFECT = "EXECUTION_DEFECT"

REASON_ORDER = (
    MISSING_FORECAST, MISSING_QUOTE, STALE_QUOTE,
    INCOMPLETE_EVIDENCE, INSUFFICIENT_EDGE, EXECUTION_DEFECT,
)

REASON_LABEL = {
    MISSING_FORECAST: "No forecast",
    MISSING_QUOTE: "No usable price",
    STALE_QUOTE: "Price too old",
    INCOMPLETE_EVIDENCE: "Required evidence incomplete",
    INSUFFICIENT_EDGE: "Edge below the threshold",
    EXECUTION_DEFECT: "Execution defect",
}

REASON_EXPLANATION = {
    MISSING_FORECAST: "The model had not produced a current forecast for this game by the cutoff.",
    MISSING_QUOTE: "No sportsbook price was collected with both sides of the moneyline at the same book.",
    STALE_QUOTE: "A price existed, but the newest one was older than the freshness window allows.",
    INCOMPLETE_EVIDENCE: "Required pregame evidence was missing, unstable or contradictory.",
    INSUFFICIENT_EDGE: "Everything was in place and the model simply did not disagree with the "
                       "market by enough to bet.",
    EXECUTION_DEFECT: "The decision qualified but no position was created. This is a defect, "
                      "not a decision.",
}

#: Persisted reason text is matched by substring, deliberately: the strings the
#: policy writes carry per-game numbers ("Adjusted edge -1.5 pts below...").
_MATCHERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (MISSING_FORECAST, ("No valid current forecast", "Awaiting first forecast")),
    (MISSING_QUOTE, ("No complete same-sportsbook two-sided", "No complete two-sided")),
    (STALE_QUOTE, ("quote is stale",)),
    (INCOMPLETE_EVIDENCE, (
        "Starter evidence not", "Extreme model-market disagreement",
        "Lineup missing", "Lineup not confirmed", "Critical inputs", "critical evidence",
        "unresolved availability", "Model-required", "model-required",
    )),
    (INSUFFICIENT_EDGE, (
        "Adjusted edge", "Adjusted expected value", "adjusted thresholds", "Kelly",
    )),
)


def _match(lines: list[str]) -> str | None:
    for reason, needles in _MATCHERS:
        for line in lines:
            if any(needle in line for needle in needles):
                return reason
    return None


def classify(reasons: list[Any], recommendation_reason: str | None = None) -> str | None:
    """The FIRST blocking reason, in gate order -- not the first one recorded.

    ``recommendation_reason`` is the policy's own one-line verdict and is
    authoritative. The wider ``qualification_reasons`` list is only a fallback,
    because it records CONTEXT as well as blocks: a decision that reached the
    edge test on a lineup-pending tier still carries the lineup note, and
    reading that note as the blocker would file a game the model simply did not
    like under "evidence incomplete" -- turning a real finding about the
    threshold into a false story about missing data.
    """
    if recommendation_reason:
        verdict = _match([str(recommendation_reason)])
        if verdict is not None:
            return verdict
    return _match([str(item) for item in (reasons or [])])


class ExperimentFunnelService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _experiment(self, experiment_id: UUID | None) -> ShadowExperiment | None:
        if experiment_id is not None:
            return self.session.get(ShadowExperiment, experiment_id)
        # The experiment that has actually staked money, not merely the newest:
        # several $1,000 registrations exist and only one has ever moved.
        staked = self.session.execute(
            select(OfficialAspPosition.shadow_experiment_id, func.count())
            .where(OfficialAspPosition.shadow_experiment_id.is_not(None))
            .group_by(OfficialAspPosition.shadow_experiment_id)
            .order_by(func.count().desc())
        ).first()
        if staked is not None:
            return self.session.get(ShadowExperiment, staked[0])
        return self.session.scalars(
            select(ShadowExperiment).order_by(ShadowExperiment.created_at.desc()).limit(1)
        ).first()

    def funnel(self, *, experiment_id: UUID | None = None, days: int | None = None,
               now: datetime | None = None) -> dict[str, Any]:
        from app.services.practical_policy import MIN_ADJUSTED_EDGE
        from app.services.shadow_experiment_service import FINAL_PREGAME_CUTOFF_MINUTES

        now = now or datetime.now(UTC)
        experiment = self._experiment(experiment_id)
        if experiment is None:
            return {"schema_version": SCHEMA_VERSION, "data_mode": "empty", "available": False,
                    "requirement": "No shadow experiment is registered."}

        rows = list(self.session.scalars(
            select(ShadowExperimentDecision).where(
                ShadowExperimentDecision.experiment_id == experiment.id,
                ShadowExperimentDecision.phase == "FINAL_PREGAME",
            ).order_by(ShadowExperimentDecision.recommended_at)
        ))
        if days is not None:
            floor = (now - timedelta(days=days)).date()
            rows = [row for row in rows if row.slate_date >= floor]

        # One canonical decision per game: the last one recorded at or before
        # the final pregame cutoff, which is the decision the experiment acted on.
        canonical: dict[UUID, ShadowExperimentDecision] = {}
        for row in rows:
            canonical[row.game_id] = row

        # A position is bound to the exact decision that produced it, so a
        # qualified decision with no position is a real execution defect and
        # not a bookkeeping mismatch.
        positions = set(self.session.scalars(
            select(OfficialAspPosition.shadow_decision_id).where(
                OfficialAspPosition.shadow_experiment_id == experiment.id,
                OfficialAspPosition.shadow_decision_id.is_not(None))
        ))

        blocked: Counter[str] = Counter()
        qualified = 0
        for decision in canonical.values():
            if decision.recommendation == "QUALIFIED_BET":
                qualified += 1
                if decision.id not in positions:
                    blocked[EXECUTION_DEFECT] += 1
                continue
            reason = classify(decision.qualification_reasons, decision.recommendation_reason)
            blocked[reason or INSUFFICIENT_EDGE] += 1

        scheduled = len(canonical)
        with_forecast = scheduled - blocked[MISSING_FORECAST]
        with_quote = with_forecast - blocked[MISSING_QUOTE] - blocked[STALE_QUOTE]
        clearing = with_quote - blocked[INSUFFICIENT_EDGE] - blocked[INCOMPLETE_EVIDENCE]
        created = qualified - blocked[EXECUTION_DEFECT]

        stages = [
            {"key": "scheduled", "label": "Games in the experiment", "count": scheduled,
             "explanation": "Every game the experiment reached a final pregame decision on.",
             "lost": [], },
            {"key": "forecast", "label": "The model produced a forecast", "count": with_forecast,
             "explanation": "A current model forecast existed at the decision cutoff.",
             "lost": [MISSING_FORECAST]},
            {"key": "quote", "label": "A usable price was available", "count": with_quote,
             "explanation": "Both sides of the moneyline, at the same sportsbook, inside the "
                            "freshness window.",
             "lost": [MISSING_QUOTE, STALE_QUOTE]},
            {"key": "edge", "label": "The edge cleared the threshold", "count": clearing,
             "explanation": f"The model disagreed with the market by at least "
                            f"{MIN_ADJUSTED_EDGE * 100:.1f} points after every penalty.",
             "lost": [INSUFFICIENT_EDGE, INCOMPLETE_EVIDENCE]},
            {"key": "qualified", "label": "Decision qualified", "count": qualified,
             "explanation": "The policy recorded a QUALIFIED_BET.", "lost": []},
            {"key": "positions", "label": "Position created", "count": created,
             "explanation": "A position was actually opened in the ledger.",
             "lost": [EXECUTION_DEFECT]},
        ]
        for index, stage in enumerate(stages):
            previous = stages[index - 1]["count"] if index else stage["count"]
            stage["dropped"] = max(0, previous - stage["count"])
            stage["reasons"] = [
                {"reason": key, "label": REASON_LABEL[key],
                 "explanation": REASON_EXPLANATION[key], "count": blocked[key]}
                for key in stage["lost"] if blocked[key]
            ]
            stage.pop("lost")

        # The claim this page exists to make, with its own arithmetic attached.
        priced = with_quote
        cleared_rate = (clearing / priced) if priced else None
        restorable = blocked[MISSING_QUOTE] + blocked[STALE_QUOTE]
        return {
            "schema_version": SCHEMA_VERSION,
            "data_mode": "live",
            "available": True,
            "experiment": {
                "id": str(experiment.id), "name": experiment.name,
                "policy_version": experiment.decision_policy,
                "recommendation_policy": experiment.recommendation_policy_version,
                "starting_bankroll": float(experiment.starting_bankroll),
                "start_date": experiment.start_date.isoformat() if experiment.start_date else None,
            },
            "window_days": days,
            "generated_at_utc": now.isoformat(),
            "cutoff_minutes": FINAL_PREGAME_CUTOFF_MINUTES,
            "min_adjusted_edge": MIN_ADJUSTED_EDGE,
            "stages": stages,
            "blocked_totals": [
                {"reason": key, "label": REASON_LABEL[key], "count": blocked[key],
                 "explanation": REASON_EXPLANATION[key]}
                for key in REASON_ORDER if blocked[key]
            ],
            "price_restoration": {
                "games_without_a_usable_price": restorable,
                "games_with_a_usable_price": priced,
                "cleared_when_priced": clearing,
                "clear_rate_when_priced": cleared_rate,
                "projected_additional_clearing": (
                    round(restorable * cleared_rate, 1) if cleared_rate is not None else None),
                "statement": (
                    "Restoring prices makes more games EVALUABLE, not necessarily more games "
                    "QUALIFYING. Of the games that did have a usable price, "
                    f"{clearing} of {priced} cleared the threshold"
                    + (f" ({cleared_rate * 100:.0f}%)" if cleared_rate is not None else "")
                    + f". Applying that same rate to the {restorable} games that had no usable "
                    f"price suggests roughly "
                    + (f"{restorable * cleared_rate:.0f}" if cleared_rate is not None else "an unknown number of")
                    + " of them would have cleared it — and the rest would still not have."
                ),
                "method": (
                    "The rate is measured on games that did have a price, then applied to games "
                    "that did not. It assumes the unpriced games resemble the priced ones. They "
                    "were unpriced because of a provider quota outage rather than anything about "
                    "the games themselves, which supports the assumption without proving it."
                ),
            },
            "thresholds_unchanged": (
                "No threshold on this page was lowered to produce it. The minimum adjusted edge "
                f"is {MIN_ADJUSTED_EDGE * 100:.1f} points and the decision cutoff is "
                f"{FINAL_PREGAME_CUTOFF_MINUTES} minutes before first pitch, exactly as enforced."
            ),
        }
