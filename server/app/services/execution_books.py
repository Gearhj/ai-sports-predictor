"""Owner rule (James, 2026-10-25): paper bets execute only at US-regulated sportsbooks.

AISP_OWNER_US_REGULATED_EXECUTION_ONLY_V1
-----------------------------------------
Every AISP paper decision path chooses ONE execution price -- the book whose
quote a position would be taken at. From the owner's decision on, that book must
be a US state-regulated sportsbook. Offshore books (BetOnline, LowVig, Bovada,
MyBookie, BetUS, ...) keep feeding every consensus / no-vig / market-probability
calculation exactly as before; they simply can never be the execution book.

FAIL CLOSED
-----------
A book key this module does not recognise may still feed consensus, but it is
never an execution book. Adding a regulated book is a code change reviewed here,
never something a provider can introduce by sending a new key.

When the best eligible pair would have been offshore, the decision paths choose
the best US-regulated pair instead; when no US-regulated pair qualifies they
decline with ``NO_US_REGULATED_PRICE`` -- a value pass, not a missing price.

ROLLBACK
--------
``EXECUTION_US_REGULATED_ONLY`` (default True: the owner approved the rule) is
the only switch. False restores the old any-book selection.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

RULE = "AISP_OWNER_US_REGULATED_EXECUTION_ONLY_V1"
SETTING = "execution_us_regulated_only"
NO_US_REGULATED_PRICE = "NO_US_REGULATED_PRICE"

#: US state-regulated sportsbooks, by provider key (The Odds API keys plus the
#: SportsGameOdds bookmakerIDs for the same brands; namespaces are stripped).
US_REGULATED_EXECUTION: frozenset[str] = frozenset({
    "draftkings", "fanduel", "betmgm",
    "williamhill_us", "williamhill", "caesars",          # Caesars Sportsbook
    "betrivers", "espnbet", "fanatics", "hardrockbet", "ballybet", "betparx", "bet365",
})

#: Offshore books. They stay in consensus; they never execute.
OFFSHORE: frozenset[str] = frozenset({
    "betonlineag", "betonline", "lowvig", "lowvigag", "bovada", "mybookieag", "mybookie",
    "betus", "betanysports", "everygame", "pinnacle",
})

#: Display names (MLB's opportunity board carries ``Sportsbook.name``, not the key).
_NAME_TO_KEY: dict[str, str] = {
    "draftkings": "draftkings", "fanduel": "fanduel", "betmgm": "betmgm",
    "caesars": "williamhill_us", "caesars sportsbook": "williamhill_us", "william hill": "williamhill_us",
    "betrivers": "betrivers", "espn bet": "espnbet", "espnbet": "espnbet", "thescore bet": "espnbet",
    "fanatics": "fanatics", "fanatics sportsbook": "fanatics", "hard rock bet": "hardrockbet",
    "bally bet": "ballybet", "betparx": "betparx", "bet365": "bet365",
    "betonline.ag": "betonlineag", "betonline": "betonlineag", "lowvig.ag": "lowvig", "lowvig": "lowvig",
    "bovada": "bovada", "mybookie.ag": "mybookieag", "mybookie": "mybookieag", "betus": "betus",
    "betanysports": "betanysports", "pinnacle": "pinnacle", "everygame": "everygame",
}

US_REGULATED = "US_REGULATED"
OFFSHORE_CLASS = "OFFSHORE"
UNKNOWN = "UNKNOWN"


def normalise(key: Any) -> str:
    """Provider-agnostic book key: lower-case, any ``namespace:`` prefix (``sgo:``) removed,
    a provider display name (``"BetOnline.ag"``, ``"DraftKings (SportsGameOdds, research)"``) mapped
    to its key."""

    text = str(key or "").strip().lower()
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    text = text.split(" (", 1)[0].strip()   # "<title> (<provider>, research)"
    return _NAME_TO_KEY.get(text, text)


def classify(key: Any) -> str:
    book = normalise(key)
    if book in US_REGULATED_EXECUTION:
        return US_REGULATED
    if book in OFFSHORE:
        return OFFSHORE_CLASS
    return UNKNOWN


def enabled(settings: Any) -> bool:
    """The rule is ON unless the rollback flag is explicitly False (also with no settings)."""

    return True if settings is None else bool(getattr(settings, SETTING, True))


def is_execution_book(key: Any, settings: Any = None) -> bool:
    """May a position be taken at this book? Unknown keys: no (fail closed)."""

    if not enabled(settings):
        return True
    return classify(key) == US_REGULATED


def _book(item: Any) -> Any:
    return item["sportsbook"]


def partition(items: Iterable[Any], settings: Any = None, *,
              book: Callable[[Any], Any] = _book) -> tuple[list[Any], list[Any]]:
    """Split candidates/pairs into (executable, excluded), order preserved."""

    executable: list[Any] = []
    excluded: list[Any] = []
    for item in items:
        (executable if is_execution_book(book(item), settings) else excluded).append(item)
    return executable, excluded


def receipt(excluded: Iterable[Any], settings: Any = None, *,
            book: Callable[[Any], Any] = _book) -> dict[str, Any]:
    """Decision evidence: the rule, and which books were kept out of execution (and why)."""

    books = sorted({str(book(item)) for item in excluded})
    on = enabled(settings)
    return {"rule": RULE, "enabled": on,
            "execution_books": "US_REGULATED_ONLY" if on else "ANY_BOOK",
            "offshore_books_feed_consensus_only": True,
            "excluded_from_execution": [{"sportsbook": b, "class": classify(b)} for b in books]}


def no_us_regulated_detail(sport: str, excluded_books: Iterable[Any]) -> str:
    books = ", ".join(sorted({str(b) for b in excluded_books})) or "none"
    return (f"{sport}: no US-regulated sportsbook offers a qualifying price; the only qualifying (or only "
            f"stored) price is at a book that is not US-regulated ({books}). Owner rule {RULE}: paper bets "
            "execute only at US-regulated books; those prices stay in consensus. A value pass, not a "
            "missing price.")


def decline_without_us_price(base: dict[str, Any], *, sport: str, executable: list[dict[str, Any]],
                             excluded: list[dict[str, Any]], bankroll: Any = None,
                             value_key: str = "expected_return_per_dollar") -> dict[str, Any] | None:
    """The shared NO_US_REGULATED_PRICE decline for the research paper paths (CFB/NBA/NHL).

    Returns the decline when no US-regulated candidate qualifies but an excluded
    one did, or when there is no US-regulated candidate at all; else None (the
    path's own value/budget/minimum-stake reasons then apply to US candidates).
    """

    if not excluded or any(c["terms"]["qualifies"] for c in executable):
        return None
    excluded_qualifying = [c for c in excluded if c["terms"]["qualifies"]]
    if executable and not excluded_qualifying:
        return None
    pool = excluded_qualifying or excluded
    would_have = max(pool, key=lambda c: c["terms"][value_key])
    decision = {**base, "outcome": "DECLINED", "reason": NO_US_REGULATED_PRICE,
                "detail": no_us_regulated_detail(sport, [c["sportsbook"] for c in pool]),
                "excluded_execution_candidate": would_have}
    if executable:
        decision["best"] = max(executable, key=lambda c: c["terms"][value_key])
    if bankroll is not None:
        decision["bankroll"] = bankroll
    return decision


__all__ = ["NO_US_REGULATED_PRICE", "OFFSHORE", "RULE", "SETTING", "US_REGULATED_EXECUTION",
           "classify", "decline_without_us_price", "enabled", "is_execution_book",
           "no_us_regulated_detail", "normalise", "partition", "receipt"]
