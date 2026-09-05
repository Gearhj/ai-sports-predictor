"""Explicit provider-team alias registry (no unrestricted fuzzy matching).

Production mapping resolves a provider team label to an AISP team abbreviation
ONLY through this registry. An unregistered label fails closed (the event stays
UNRESOLVED with an explicit reason) instead of being guessed from similarity.

Kalshi encodes both teams in the event ticker (``KXMLBGAME-26SEP041915DETCLEG2``
→ away ``DET``, home ``CLE``, game 2) and names the YES team in the market's
``yes_sub_title`` (``"Los Angeles D"``). Polymarket uses full team names in the
event title (``"New York Yankees vs. San Diego Padres"``) and as outcome labels.
"""

from __future__ import annotations

# AISP MLB abbreviations (teams table) — the canonical identity space.
AISP_MLB_ABBREVIATIONS: frozenset[str] = frozenset({
    "ATH", "ATL", "AZ", "BAL", "BOS", "CHC", "CIN", "CLE", "COL", "CWS", "DET", "HOU", "KC", "LAA", "LAD",
    "MIA", "MIL", "MIN", "NYM", "NYY", "PHI", "PIT", "SD", "SEA", "SF", "STL", "TB", "TEX", "TOR", "WSH",
})

# Kalshi ticker abbreviation -> AISP abbreviation (verified against live
# KXMLBGAME event tickers on 2026-09-04; Kalshi and AISP currently agree).
KALSHI_MLB_TICKER_ALIASES: dict[str, str] = {
    "ATH": "ATH", "ATL": "ATL", "AZ": "AZ", "ARI": "AZ", "BAL": "BAL", "BOS": "BOS", "CHC": "CHC", "CIN": "CIN",
    "CLE": "CLE", "COL": "COL", "CWS": "CWS", "CHW": "CWS", "DET": "DET", "HOU": "HOU", "KC": "KC", "KCR": "KC",
    "LAA": "LAA", "LAD": "LAD", "MIA": "MIA", "MIL": "MIL", "MIN": "MIN", "NYM": "NYM", "NYY": "NYY", "PHI": "PHI",
    "PIT": "PIT", "SD": "SD", "SDP": "SD", "SEA": "SEA", "SF": "SF", "SFG": "SF", "STL": "STL", "TB": "TB",
    "TBR": "TB", "TEX": "TEX", "TOR": "TOR", "WSH": "WSH", "WAS": "WSH", "OAK": "ATH",
}

# Kalshi display names (yes_sub_title / title "X wins") -> AISP abbreviation.
KALSHI_MLB_NAME_ALIASES: dict[str, str] = {
    "a's": "ATH", "athletics": "ATH", "atlanta": "ATL", "arizona": "AZ", "baltimore": "BAL", "boston": "BOS",
    "chicago c": "CHC", "chicago cubs": "CHC", "cincinnati": "CIN", "cleveland": "CLE", "colorado": "COL",
    "chicago ws": "CWS", "chicago white sox": "CWS", "detroit": "DET", "houston": "HOU", "kansas city": "KC",
    "los angeles a": "LAA", "los angeles angels": "LAA", "los angeles d": "LAD", "los angeles dodgers": "LAD",
    "miami": "MIA", "milwaukee": "MIL", "minnesota": "MIN", "new york m": "NYM", "new york mets": "NYM",
    "new york y": "NYY", "new york yankees": "NYY", "philadelphia": "PHI", "pittsburgh": "PIT", "san diego": "SD",
    "seattle": "SEA", "san francisco": "SF", "st. louis": "STL", "st louis": "STL", "tampa bay": "TB",
    "texas": "TEX", "toronto": "TOR", "washington": "WSH",
}

# Polymarket full team names -> AISP abbreviation.
POLYMARKET_MLB_NAME_ALIASES: dict[str, str] = {
    "athletics": "ATH", "oakland athletics": "ATH", "atlanta braves": "ATL", "arizona diamondbacks": "AZ",
    "baltimore orioles": "BAL", "boston red sox": "BOS", "chicago cubs": "CHC", "cincinnati reds": "CIN",
    "cleveland guardians": "CLE", "colorado rockies": "COL", "chicago white sox": "CWS", "detroit tigers": "DET",
    "houston astros": "HOU", "kansas city royals": "KC", "los angeles angels": "LAA", "los angeles dodgers": "LAD",
    "miami marlins": "MIA", "milwaukee brewers": "MIL", "minnesota twins": "MIN", "new york mets": "NYM",
    "new york yankees": "NYY", "philadelphia phillies": "PHI", "pittsburgh pirates": "PIT", "san diego padres": "SD",
    "seattle mariners": "SEA", "san francisco giants": "SF", "st. louis cardinals": "STL", "st louis cardinals": "STL",
    "tampa bay rays": "TB", "texas rangers": "TEX", "toronto blue jays": "TOR", "washington nationals": "WSH",
}


def _norm(label: str | None) -> str:
    return " ".join(str(label or "").strip().lower().replace("’", "'").split())


def kalshi_ticker_to_aisp(abbr: str | None) -> str | None:
    """Exact registry lookup; None when the ticker abbreviation is unregistered."""
    if not abbr:
        return None
    return KALSHI_MLB_TICKER_ALIASES.get(abbr.strip().upper())


def kalshi_name_to_aisp(name: str | None) -> str | None:
    return KALSHI_MLB_NAME_ALIASES.get(_norm(name))


def polymarket_name_to_aisp(name: str | None) -> str | None:
    return POLYMARKET_MLB_NAME_ALIASES.get(_norm(name))


def split_kalshi_team_pair(pair: str) -> tuple[str, str] | None:
    """Split the ``<AWAY><HOME>`` tail of a Kalshi game event ticker into two
    registered abbreviations. Exactly one registered split must exist;
    otherwise fail closed (None). Example: ``"DETCLE"`` -> ("DET", "CLE"),
    ``"AZHOU"`` -> ("AZ", "HOU"), ``"ATHSEA"`` -> ("ATH", "SEA")."""
    pair = pair.strip().upper()
    candidates: list[tuple[str, str]] = []
    for cut in (2, 3):
        left, right = pair[:cut], pair[cut:]
        if 2 <= len(right) <= 3 and left in KALSHI_MLB_TICKER_ALIASES and right in KALSHI_MLB_TICKER_ALIASES:
            candidates.append((left, right))
    unique = {c for c in candidates}
    if len(unique) != 1:
        return None
    return candidates[0]


__all__ = [
    "AISP_MLB_ABBREVIATIONS", "KALSHI_MLB_TICKER_ALIASES", "KALSHI_MLB_NAME_ALIASES", "POLYMARKET_MLB_NAME_ALIASES",
    "kalshi_ticker_to_aisp", "kalshi_name_to_aisp", "polymarket_name_to_aisp", "split_kalshi_team_pair",
]
