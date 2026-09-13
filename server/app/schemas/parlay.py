"""Request and response shapes for recording a virtual parlay ticket.

The request deliberately carries **identity, not numbers**. A caller names the
game, the market, the selection and the sportsbook; it cannot post a probability,
a price or a payout. Everything numeric is re-derived server-side from the stored
quote and the stored forecast, so a recorded ticket is a record of what AISP
held at that moment rather than of what a browser had on screen.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ParlayLegRequest(BaseModel):
    """One selection, named rather than priced."""

    game_id: str = Field(..., description="Canonical AISP game id.")
    market: str = Field(..., description="MONEYLINE or TOTAL.")
    selection: str = Field(
        ..., max_length=160,
        description='Exactly as enumerated, e.g. "NYY ML" or "OVER 8.5".')


class RecordParlayTicketRequest(BaseModel):
    """Freeze one ticket. Two or three legs, distinct games, one sportsbook."""

    sportsbook: str = Field(..., max_length=120)
    legs: list[ParlayLegRequest] = Field(..., min_length=2, max_length=3)
    #: The suggested stake. A dollar is the default because these are research
    #: tickets: the point is the record, not the size.
    stake: float = Field(1.0, gt=0, le=100.0)
    #: A funded ticket draws on the existing MLB season account. There is one
    #: $1,000 contribution and no second parlay bankroll, so funding a ticket
    #: reserves against that same account or is refused.
    funded: bool = Field(
        False,
        description=(
            "Draw the stake against the existing MLB season account. False "
            "records an unfunded research ticket."
        ),
    )
    #: Present only when a real full-ticket price was observed. Absent means the
    #: ticket is recorded at the estimated combined price and stays labelled so
    #: through settlement and performance reporting.
    recorded_combined_decimal_odds: float | None = Field(
        None, gt=1.0,
        description=(
            "A genuine sportsbook combined quote, when one was observed. Omit to "
            "record an estimated-price research ticket."
        ),
    )


__all__ = ["ParlayLegRequest", "RecordParlayTicketRequest"]
