"""How trustworthy a recorded final is, and which one wins when several exist.

THE PROBLEM
-----------
One game can accumulate several honest records of the same result: an operator
typed the box score in on Wednesday night, the free schedule feed published it
on Thursday, and the governed provider would confirm it if it were ever armed.
All three are true. None should be thrown away -- an audit trail that deletes
its weaker evidence cannot show what we believed and when.

But a product that COUNTS all three has three games where there was one, and
"NFL scored games: 3" after one Wednesday night is a lie assembled entirely
out of true statements.

THE RULE
--------
Rank the sources. Keep every record. Present exactly one.

    1  THE_ODDS_API      governed provider, the only source that may reach a
                         priced decision anywhere in this system
    2  NFLVERSE          trustworthy automated mirror, free and well-identified,
                         but not the league's own wire and not governed
    3  OPERATOR_ENTERED  a human read a box score; honest, unverified, and the
                         last resort when nothing stronger exists

Lower is stronger. An unknown source ranks below all of them rather than
above: a string nobody has classified is not evidence of quality.

WHAT RANK DOES *NOT* DO
-----------------------
It does not decide which forecast gets scored -- nothing about a result may
touch forecast selection. It does not grant wagering authority: an NFLVERSE
final is not provider-verified and never becomes so by being the best thing we
have.
"""
from __future__ import annotations

from typing import Any

THE_ODDS_API = "THE_ODDS_API"
NFLVERSE = "NFLVERSE"
MLB_STATS_API = "MLB_STATS_API"
CFBD = "CFBD"
ESPN_SPORTSDATAVERSE = "ESPN_SPORTSDATAVERSE"
NBA_SOURCE_CONFIRMED_FINAL = "NBA_GEN1_SOURCE_CONFIRMED_FINAL"
OPERATOR_ENTERED = "OPERATOR_ENTERED"

#: Lower is stronger.
RESULT_TIERS: dict[str, int] = {
    THE_ODDS_API: 1,
    NFLVERSE: 2,
    MLB_STATS_API: 2,
    CFBD: 2,
    ESPN_SPORTSDATAVERSE: 2,
    NBA_SOURCE_CONFIRMED_FINAL: 2,
    OPERATOR_ENTERED: 3,
}

#: Where a source's own note differs from the generic note for its tier. MLB's
#: statistics API is the league's own wire rather than a mirror of it, which the
#: tier-2 note explicitly disclaims -- but it is still not the governed odds
#: provider, so it must not be promoted to tier 1 and described as
#: provider-verified. It keeps tier 2 and says accurately what it is.
SOURCE_NOTES: dict[str, str] = {
    MLB_STATS_API: "Recorded automatically from MLB's own statistics API, the "
                   "league's official wire for game state and final scores. It is "
                   "not the governed odds provider, so it is not described as "
                   "provider-verified and it grants no wagering authority.",
    CFBD: "Recorded automatically from CollegeFootballData.com /games rows that the "
          "producer retained under its approved capture plan (completed=true, points, "
          "exact CFBD game and team ids). CFBD supplies no completion or revision "
          "time; the capture time is shown as the observation time only. Not the "
          "governed odds provider, so it is not described as provider-verified.",
    NBA_SOURCE_CONFIRMED_FINAL: "Recorded from a NBA_GEN1_SOURCE_CONFIRMED_FINAL_V1 record delivered on the "
                                "producer's finals-discovery lane and proved from its own bytes (exact game "
                                "and franchise identities, source STATUS_FINAL with completed=true, full-game "
                                "score including overtime). The record states that no admitted source retains "
                                "a completion time, so capture, validation and admission are shown as what "
                                "they are and none stands in for one. Evidence only: not the governed odds "
                                "provider, and it grants no settlement authority of its own.",
    ESPN_SPORTSDATAVERSE: "Recorded automatically from a SportsDataverse/hoopR ESPN-derived NBA schedule "
                          "export (CC BY 4.0) that Model Studio retained at a content address under its "
                          "reviewed acquisition receipt (STATUS_FINAL, completed=true, exact ESPN game and "
                          "team ids). The export carries no completion time; the capture time is shown as "
                          "the observation time only. Not the governed odds provider.",
}

#: An unclassified source outranks nothing.
UNCLASSIFIED_TIER = 9

#: Only tier 1 may be described as provider-verified.
PROVIDER_VERIFIED_TIER = 1

#: At or above this tier a result is automatic; below it a human supplied it.
AUTOMATED_TIER = 2

TIER_LABELS: dict[int, str] = {
    1: "Provider-verified",
    2: "Automated source",
    3: "Operator-entered",
    UNCLASSIFIED_TIER: "Unclassified source",
}

TIER_NOTES: dict[int, str] = {
    1: "Recorded from the governed odds provider's scores endpoint.",
    2: "Recorded automatically from the nflverse schedules release. Free, "
       "well-identified and independent of us, but not the governed provider, "
       "so it is not described as provider-verified.",
    3: "A person read a box score and entered it. Kept for audit history and "
       "superseded as soon as an automatic source confirms the same game.",
    UNCLASSIFIED_TIER: "This source has no recorded provenance classification.",
}


def result_tier(source: str | None) -> int:
    """The rank of a result source. Unknown sources rank last."""

    return RESULT_TIERS.get(str(source or "").strip().upper(), UNCLASSIFIED_TIER)


def is_provider_verified(source: str | None) -> bool:
    return result_tier(source) == PROVIDER_VERIFIED_TIER


def is_automated(source: str | None) -> bool:
    """True when no human keyed the score in."""

    return result_tier(source) <= AUTOMATED_TIER


def describe(source: str | None) -> dict[str, Any]:
    """A source's rank and how it should be described on a page."""

    tier = result_tier(source)
    key = str(source or "").strip().upper() or None
    return {
        "source": key,
        "tier": tier,
        "label": TIER_LABELS.get(tier, TIER_LABELS[UNCLASSIFIED_TIER]),
        "note": SOURCE_NOTES.get(
            key or "", TIER_NOTES.get(tier, TIER_NOTES[UNCLASSIFIED_TIER])
        ),
        "provider_verified": tier == PROVIDER_VERIFIED_TIER,
        "automated": tier <= AUTOMATED_TIER,
    }


def stronger(candidate: str | None, incumbent: str | None) -> bool:
    """Is ``candidate`` a strictly better-provenanced source than ``incumbent``?"""

    return result_tier(candidate) < result_tier(incumbent)


__all__ = [
    "AUTOMATED_TIER", "MLB_STATS_API", "NFLVERSE", "OPERATOR_ENTERED",
    "PROVIDER_VERIFIED_TIER", "RESULT_TIERS", "SOURCE_NOTES", "THE_ODDS_API",
    "TIER_LABELS", "TIER_NOTES", "UNCLASSIFIED_TIER",
    "describe", "is_automated", "is_provider_verified", "result_tier", "stronger",
]
