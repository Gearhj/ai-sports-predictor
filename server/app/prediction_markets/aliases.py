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

import re

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


# ---------------------------------------------------------------------------
# NFL / NBA / NHL (added 2026-10-25). Kalshi's codes were read from the live
# KXNFLGAME / KXNBAGAME / KXNHLGAME event sub-titles ("PIT vs NO (Oct 25)") and
# AISP's from the teams on each league's games; only the listed codes differ.
AISP_NFL_ABBREVIATIONS: frozenset[str] = frozenset({
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
    "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
})
AISP_NBA_ABBREVIATIONS: frozenset[str] = frozenset({
    "ATL", "BOS", "BRK", "CHI", "CHO", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL", "MEM", "MIA",
    "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHO", "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
})
AISP_NHL_ABBREVIATIONS: frozenset[str] = frozenset({
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NJD",
    "NSH", "NYI", "NYR", "OTT", "PHI", "PIT", "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WPG", "WSH",
})


def _identity(codes: frozenset[str], extra: dict[str, str]) -> dict[str, str]:
    out = {c: c for c in codes}
    out.update(extra)
    return out


KALSHI_TICKER_ALIASES: dict[str, dict[str, str]] = {
    "MLB": KALSHI_MLB_TICKER_ALIASES,
    "NFL": _identity(AISP_NFL_ABBREVIATIONS, {"JAC": "JAX", "WSH": "WAS", "LA": "LAR", "ARZ": "ARI"}),
    "NBA": _identity(AISP_NBA_ABBREVIATIONS, {"BKN": "BRK", "CHA": "CHO", "PHX": "PHO", "GS": "GSW", "NO": "NOP",
                                              "NY": "NYK", "SA": "SAS", "WSH": "WAS", "UTAH": "UTA"}),
    "NHL": _identity(AISP_NHL_ABBREVIATIONS, {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "UTAH": "UTA",
                                              "VEG": "VGK", "WAS": "WSH"}),
}

# Kalshi yes_sub_title place names per league ("Los Angeles L", "New York I") and team nicknames.
_NFL_PLACES = {
    "arizona": "ARI", "atlanta": "ATL", "baltimore": "BAL", "buffalo": "BUF", "carolina": "CAR", "chicago": "CHI",
    "cincinnati": "CIN", "cleveland": "CLE", "dallas": "DAL", "denver": "DEN", "detroit": "DET", "green bay": "GB",
    "houston": "HOU", "indianapolis": "IND", "jacksonville": "JAX", "kansas city": "KC", "los angeles c": "LAC",
    "los angeles r": "LAR", "las vegas": "LV", "miami": "MIA", "minnesota": "MIN", "new england": "NE",
    "new orleans": "NO", "new york g": "NYG", "new york j": "NYJ", "philadelphia": "PHI", "pittsburgh": "PIT",
    "seattle": "SEA", "san francisco": "SF", "tampa bay": "TB", "tennessee": "TEN", "washington": "WAS",
}
_NFL_NICKNAMES = {
    "cardinals": "ARI", "falcons": "ATL", "ravens": "BAL", "bills": "BUF", "panthers": "CAR", "bears": "CHI",
    "bengals": "CIN", "browns": "CLE", "cowboys": "DAL", "broncos": "DEN", "lions": "DET", "packers": "GB",
    "texans": "HOU", "colts": "IND", "jaguars": "JAX", "chiefs": "KC", "chargers": "LAC", "rams": "LAR",
    "raiders": "LV", "dolphins": "MIA", "vikings": "MIN", "patriots": "NE", "saints": "NO", "giants": "NYG",
    "jets": "NYJ", "eagles": "PHI", "steelers": "PIT", "seahawks": "SEA", "49ers": "SF", "buccaneers": "TB",
    "titans": "TEN", "commanders": "WAS",
}
_NFL_FULL_PLACES = {"los angeles c": "los angeles", "los angeles r": "los angeles", "new york g": "new york", "new york j": "new york"}
_NBA_PLACES = {
    "atlanta": "ATL", "boston": "BOS", "brooklyn": "BRK", "chicago": "CHI", "charlotte": "CHO", "cleveland": "CLE",
    "dallas": "DAL", "denver": "DEN", "detroit": "DET", "golden state": "GSW", "houston": "HOU", "indiana": "IND",
    "los angeles c": "LAC", "los angeles l": "LAL", "memphis": "MEM", "miami": "MIA", "milwaukee": "MIL",
    "minnesota": "MIN", "new orleans": "NOP", "new york": "NYK", "oklahoma city": "OKC", "orlando": "ORL",
    "philadelphia": "PHI", "phoenix": "PHO", "portland": "POR", "sacramento": "SAC", "san antonio": "SAS",
    "toronto": "TOR", "utah": "UTA", "washington": "WAS",
}
_NBA_NICKNAMES = {
    "hawks": "ATL", "celtics": "BOS", "nets": "BRK", "bulls": "CHI", "hornets": "CHO", "cavaliers": "CLE",
    "mavericks": "DAL", "nuggets": "DEN", "pistons": "DET", "warriors": "GSW", "rockets": "HOU", "pacers": "IND",
    "clippers": "LAC", "lakers": "LAL", "grizzlies": "MEM", "heat": "MIA", "bucks": "MIL", "timberwolves": "MIN",
    "pelicans": "NOP", "knicks": "NYK", "thunder": "OKC", "magic": "ORL", "76ers": "PHI", "suns": "PHO",
    "trail blazers": "POR", "blazers": "POR", "kings": "SAC", "spurs": "SAS", "raptors": "TOR", "jazz": "UTA",
    "wizards": "WAS",
}
_NBA_FULL_PLACES = {"los angeles c": "los angeles", "los angeles l": "los angeles"}
_NHL_PLACES = {
    "anaheim": "ANA", "boston": "BOS", "buffalo": "BUF", "carolina": "CAR", "columbus": "CBJ", "calgary": "CGY",
    "chicago": "CHI", "colorado": "COL", "dallas": "DAL", "detroit": "DET", "edmonton": "EDM", "florida": "FLA",
    "los angeles": "LAK", "minnesota": "MIN", "montreal": "MTL", "new jersey": "NJD", "nashville": "NSH",
    "new york i": "NYI", "new york r": "NYR", "ottawa": "OTT", "philadelphia": "PHI", "pittsburgh": "PIT",
    "seattle": "SEA", "san jose": "SJS", "st. louis": "STL", "st louis": "STL", "tampa bay": "TBL",
    "toronto": "TOR", "utah": "UTA", "vancouver": "VAN", "vegas": "VGK", "winnipeg": "WPG", "washington": "WSH",
}
_NHL_NICKNAMES = {
    "ducks": "ANA", "bruins": "BOS", "sabres": "BUF", "hurricanes": "CAR", "blue jackets": "CBJ", "flames": "CGY",
    "blackhawks": "CHI", "avalanche": "COL", "stars": "DAL", "red wings": "DET", "oilers": "EDM", "panthers": "FLA",
    "kings": "LAK", "wild": "MIN", "canadiens": "MTL", "devils": "NJD", "predators": "NSH", "islanders": "NYI",
    "rangers": "NYR", "senators": "OTT", "flyers": "PHI", "penguins": "PIT", "kraken": "SEA", "sharks": "SJS",
    "blues": "STL", "lightning": "TBL", "maple leafs": "TOR", "mammoth": "UTA", "canucks": "VAN",
    "golden knights": "VGK", "jets": "WPG", "capitals": "WSH",
}
_NHL_FULL_PLACES = {"new york i": "new york", "new york r": "new york", "st louis": "st. louis"}


def _full_names(places: dict[str, str], nicknames: dict[str, str], full_place: dict[str, str]) -> dict[str, str]:
    """Nickname alone and '<place> <nickname>' for each club ('Los Angeles Rams'), never a bare shared place."""
    out = dict(nicknames)
    for place, abbr in places.items():
        for nick, abbr2 in nicknames.items():
            if abbr == abbr2:
                out[f"{full_place.get(place, place)} {nick}"] = abbr
    return out


KALSHI_NAME_ALIASES: dict[str, dict[str, str]] = {
    "MLB": KALSHI_MLB_NAME_ALIASES,
    "NFL": {**_NFL_PLACES, **_NFL_NICKNAMES},
    "NBA": {**_NBA_PLACES, **_NBA_NICKNAMES},
    "NHL": {**_NHL_PLACES, **_NHL_NICKNAMES},
}
# Polymarket labels NFL/NBA/NHL games by nickname ("Commanders vs. Giants") or by full name.
POLYMARKET_NAME_ALIASES: dict[str, dict[str, str]] = {
    "MLB": POLYMARKET_MLB_NAME_ALIASES,
    "NFL": _full_names(_NFL_PLACES, _NFL_NICKNAMES, _NFL_FULL_PLACES),
    "NBA": _full_names(_NBA_PLACES, _NBA_NICKNAMES, _NBA_FULL_PLACES),
    "NHL": _full_names(_NHL_PLACES, _NHL_NICKNAMES, _NHL_FULL_PLACES),
}

_SUB_TITLE_PAIR = re.compile(r"^\s*([A-Z]{2,4})\s+vs\.?\s+([A-Z]{2,4})\b")


def kalshi_ticker_to_aisp(abbr: str | None, league: str = "MLB") -> str | None:
    """Exact registry lookup; None when the ticker abbreviation is unregistered for that league."""
    if not abbr:
        return None
    return KALSHI_TICKER_ALIASES.get(league.upper(), {}).get(abbr.strip().upper())


def kalshi_name_to_aisp(name: str | None, league: str = "MLB") -> str | None:
    return KALSHI_NAME_ALIASES.get(league.upper(), {}).get(_norm(name))


def polymarket_name_to_aisp(name: str | None, league: str = "MLB") -> str | None:
    return POLYMARKET_NAME_ALIASES.get(league.upper(), {}).get(_norm(name))


def split_kalshi_team_pair(pair: str, league: str = "MLB", sub_title: str | None = None) -> tuple[str, str] | None:
    """Split the ``<AWAY><HOME>`` tail of a Kalshi game event ticker into two
    registered abbreviations. When the event sub-title names two registered
    codes whose concatenation is exactly the tail (``"PIT vs NO (Oct 25)"``),
    it decides. Otherwise exactly one registered split must exist, or the
    split fails closed (None). Example: ``"DETCLE"`` -> ("DET", "CLE")."""
    pair = pair.strip().upper()
    table = KALSHI_TICKER_ALIASES.get(league.upper(), {})
    if sub_title:
        m = _SUB_TITLE_PAIR.match(sub_title)
        if m and m.group(1) + m.group(2) == pair and m.group(1) in table and m.group(2) in table:
            return m.group(1), m.group(2)
    candidates: list[tuple[str, str]] = []
    for cut in (2, 3, 4):
        left, right = pair[:cut], pair[cut:]
        if 2 <= len(right) <= 4 and left in table and right in table:
            candidates.append((left, right))
    unique = {c for c in candidates}
    if len(unique) != 1:
        return None
    return candidates[0]


__all__ = [
    "AISP_MLB_ABBREVIATIONS", "AISP_NFL_ABBREVIATIONS", "AISP_NBA_ABBREVIATIONS", "AISP_NHL_ABBREVIATIONS",
    "KALSHI_MLB_TICKER_ALIASES", "KALSHI_MLB_NAME_ALIASES", "POLYMARKET_MLB_NAME_ALIASES",
    "KALSHI_TICKER_ALIASES", "KALSHI_NAME_ALIASES", "POLYMARKET_NAME_ALIASES",
    "kalshi_ticker_to_aisp", "kalshi_name_to_aisp", "polymarket_name_to_aisp", "split_kalshi_team_pair",
]
