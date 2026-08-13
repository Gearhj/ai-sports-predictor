"""Explicit venue/season context selection with a visible neutral fallback."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable


@dataclass(frozen=True)
class ParkFactorReference:
    venue_id: str
    season: int
    run_factor: float
    source: str
    sample_size: float | None = None


@dataclass(frozen=True)
class ParkFactorSelection:
    run_factor: float
    venue_id: str | None
    requested_season: int
    reference_season: int | None
    source: str | None
    provisional: bool
    neutral_fallback: bool
    reason: str


def select_park_factor(
    references: Iterable[ParkFactorReference],
    *,
    venue_id: str | None,
    season: int,
    neutral_factor: float = 1.0,
) -> ParkFactorSelection:
    """Select exact venue-season evidence or return an explicit neutral prior."""

    if not isfinite(neutral_factor) or neutral_factor <= 0:
        raise ValueError("neutral_factor must be finite and positive")
    candidates: list[ParkFactorReference] = []
    for reference in references:
        if not isfinite(reference.run_factor) or reference.run_factor <= 0:
            raise ValueError("Park factors must be finite and positive")
        if reference.venue_id == venue_id and reference.season == season:
            candidates.append(reference)
    if candidates:
        selected = sorted(candidates, key=lambda item: (item.source, item.run_factor))[0]
        return ParkFactorSelection(
            run_factor=selected.run_factor,
            venue_id=venue_id,
            requested_season=season,
            reference_season=selected.season,
            source=selected.source,
            provisional=False,
            neutral_fallback=False,
            reason="Exact venue-season park-factor reference",
        )
    reason = "Venue unavailable" if venue_id is None else "Exact venue-season reference unavailable"
    return ParkFactorSelection(
        run_factor=neutral_factor,
        venue_id=venue_id,
        requested_season=season,
        reference_season=None,
        source=None,
        provisional=True,
        neutral_fallback=True,
        reason=f"{reason}; neutral provisional factor used",
    )
