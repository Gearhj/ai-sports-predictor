"""Reading the NBA joint comparison for display. Research only.

The two arms are shown side by side because that is what a comparison is, and
because their difference is the whole point of publishing both. Their winner
probabilities happen to be identical to four decimals; saying so is more useful
than presenting two numbers and letting a reader assume they differ.

Every illustrative total keeps its producer classification. A line labelled
ILLUSTRATIVE_NOT_RECORDED_MARKET is not a price AISP was offered and is never
rendered as one.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

SCHEMA_VERSION = "AISP_NBA_JOINT_COMPARISON_V1"


def joint_comparison(session: Session, *, game_id: str | None = None) -> dict[str, Any]:
    """Both arms of the imported joint comparison, with their distributions."""

    from app.models import NbaJointComparisonArm

    query = select(NbaJointComparisonArm).order_by(NbaJointComparisonArm.arm)
    if game_id:
        query = query.where(NbaJointComparisonArm.canonical_game_id == game_id)
    rows = session.scalars(query).all()

    arms: list[dict[str, Any]] = []
    for row in rows:
        joint = dict(row.joint_simulation or {})
        totals = [
            {
                "line": entry.get("line"),
                "classification": entry.get("lineClassification"),
                "over": entry.get("over"),
                "under": entry.get("under"),
                "draw_count": entry.get("drawCount"),
            }
            for entry in (joint.get("illustrativeTotals") or [])
        ]
        arms.append({
            "arm": row.arm,
            "label": row.label,
            "forecast_id": row.forecast_id,
            "forecast_sha256": row.forecast_sha256,
            "configuration_identity": row.configuration_identity,
            "draw_count": row.draw_count,
            "winner_probabilities": {
                "away": row.away_win_probability,
                "home": row.home_win_probability,
                "tie": row.tie_probability,
            },
            "expected_scores": {
                "away": row.expected_away, "home": row.expected_home,
                "total": row.expected_total,
            },
            "intervals": joint.get("intervals"),
            "illustrative_totals": totals,
            "limitations": row.limitations,
            "supplements_forecast_id": row.supplements_forecast_id,
            "source_cutoff_utc": _iso(row.source_cutoff_utc),
            "created_at_utc": _iso(row.created_at_utc),
            "imported_at": _iso(row.imported_at),
        })

    identical = False
    if len(arms) == 2:
        first, second = (a["winner_probabilities"] for a in arms)
        identical = all(
            _close(first.get(k), second.get(k)) for k in ("away", "home", "tie"))

    return {
        "schema_version": SCHEMA_VERSION,
        "arms": arms,
        "winner_probabilities_identical": identical,
        "comparison_note": (
            "Both arms return the same winner split to four decimals; they "
            "differ in the shape of the margin distribution, not in who is "
            "favoured." if identical else
            "The arms differ in their winner probabilities as well as their "
            "margin shape."
        ) if len(arms) == 2 else None,
        "authority": {
            "research_only": True,
            "wagering_authority": False,
            "note": (
                "Research only. No NBA account, market, policy, position or "
                "wagering authority exists, and importing these arms created "
                "none. Illustrative totals are labelled by the producer as not "
                "a recorded market and are not prices AISP was offered."
            ),
        },
    }


def _close(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) < 5e-5


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


__all__ = ["SCHEMA_VERSION", "joint_comparison"]
