"""Which canonical league a provider's odds request belongs to, and why an event was not bound.

INVARIANT
---------
Binding a provider odds event to a canonical game requires, in order:

1. an explicit canonical league from the caller (no default);
2. a provider league / sport key that maps to exactly that league;
3. candidate games from that league only (``source_mode`` is provenance, never
   sport, so a LIVE game of another league is never a candidate);
4. compatible participants in the same home/away orientation;
5. a scheduled start within the governed tolerance, with a unique nearest
   candidate.

Anything else is not guessed: the event is skipped and a structured receipt
names the reason. Receipts carry identifiers only -- never credentials or a
provider payload.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import logger

#: Provider league / sport-key vocabulary -> canonical league code. Only
#: requests these providers are actually configured to make are listed; a new
#: sport is added here deliberately, never inferred.
PROVIDER_LEAGUES: dict[str, dict[str, str]] = {
    "THE_ODDS_API": {"baseball_mlb": "MLB", "americanfootball_nfl": "NFL"},
    "SPORTSGAMEODDS": {"MLB": "MLB", "NFL": "NFL"},
    # Checked-in demo fixtures are baseball only.
    "ASP_DEMO_FIXTURE": {"baseball_mlb": "MLB", "MLB": "MLB"},
}

LEAGUE_MISMATCH_EXCLUDED = "league_mismatch_excluded"
PROVIDER_LEAGUE_UNMAPPED = "provider_league_unmapped"
EVENT_IDENTITY_AMBIGUOUS = "event_identity_ambiguous"
PARTICIPANT_IDENTITY_CONFLICT = "participant_identity_conflict"
SCHEDULED_TIME_MISMATCH = "scheduled_time_mismatch"
NO_CANONICAL_CANDIDATE = "no_canonical_candidate"

#: Governed start-time tolerance between a provider event and its game.
START_TOLERANCE_SECONDS = 6 * 60 * 60


def canonical_league_for(source: str, provider_league: str | None) -> str | None:
    """The canonical league a provider league/sport key maps to, or None."""
    if not provider_league:
        return None
    return PROVIDER_LEAGUES.get(str(source).upper(), {}).get(str(provider_league))


def receipt(reason: str, *, source: str, league_code: str, event_id: Any = None,
            provider_league: str | None = None, **detail: Any) -> dict[str, Any]:
    """One structured, payload-free exclusion receipt, also logged."""
    row = {"reason": reason, "source": source, "league_code": league_code,
           "provider_league": provider_league,
           "source_event_id": str(event_id) if event_id is not None else None}
    row.update({key: (str(value) if value is not None else None) for key, value in detail.items()})
    logger.warning("odds_event_not_bound", **row)
    return row


__all__ = [
    "EVENT_IDENTITY_AMBIGUOUS", "LEAGUE_MISMATCH_EXCLUDED", "NO_CANONICAL_CANDIDATE",
    "PARTICIPANT_IDENTITY_CONFLICT", "PROVIDER_LEAGUES", "PROVIDER_LEAGUE_UNMAPPED",
    "SCHEDULED_TIME_MISMATCH", "START_TOLERANCE_SECONDS", "canonical_league_for", "receipt",
]
