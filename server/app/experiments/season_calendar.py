"""Season identity and boundaries for the per-sport $1,000 paper experiment.

WHAT THIS IS
------------
One authoritative, configured place that answers two questions for each sport:

  1. Which season does a given date belong to, and what is that season called?
  2. When does that season's account open, and when does it stop taking new
     positions?

WHAT THIS IS NOT
----------------
This is **not** a league schedule, and nothing here is presented as one. AISP
does not ingest official season calendars for NFL, NBA, NHL or MMA, so these
are *accounting* boundaries chosen for the experiment program and labelled as
such. Every rule carries a ``basis`` sentence saying exactly what it rests on,
and the read model surfaces it, so nobody can mistake a configured accounting
window for a published league date.

The distinction matters at exactly one moment: a season's opening contribution
is made at ``opens_on``. If that date were fabricated from a guess at a real
schedule, the account would claim a start it cannot support. Calling it a
configured boundary keeps the claim true.

SEASON IDENTIFIERS
------------------
Two shapes, because leagues genuinely differ:

- ``CALENDAR_YEAR`` — the season lives inside one calendar year: ``"2026"``.
  MLB and the MMA campaign use this.
- ``SPLIT_YEAR`` — the season crosses New Year: ``"2026-27"``. NBA and NHL
  use this.

NFL is the case that looks like a split year and is not. Its postseason falls
in the following calendar year, but it *completes* the season that began the
previous autumn, so the whole thing — regular season and postseason — is
identified by the year it started (``"2026"``) and its window runs into the
next February. A February playoff position belongs to the 2026 season account,
not to a 2027 one.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date

__all__ = [
    "SeasonIdStyle",
    "SeasonRule",
    "SeasonWindow",
    "SPORT_SEASON_RULES",
    "PROGRAM_CONTRIBUTION",
    "rule_for",
    "season_for_date",
    "window_for",
    "supported_sports",
]


#: Every season account in this program opens with the same virtual capital.
#: One thousand dollars, once per sport per season.
PROGRAM_CONTRIBUTION = 1000.00


class SeasonIdStyle(str, enum.Enum):
    CALENDAR_YEAR = "CALENDAR_YEAR"
    SPLIT_YEAR = "SPLIT_YEAR"


@dataclass(frozen=True)
class SeasonRule:
    """How one sport's seasons are named and bounded, and on what authority."""

    sport: str
    display_name: str
    id_style: SeasonIdStyle
    #: (month, day) on which the season's account opens and begins tracking.
    opens_on: tuple[int, int]
    #: (month, day) after which the season takes no new positions. When this is
    #: earlier in the year than ``opens_on``, the season crosses New Year.
    closes_on: tuple[int, int]
    #: What these dates actually rest on. Shown to the reader.
    basis: str
    #: Set when the boundary is a product convention rather than a league fact.
    convention: str | None = None

    @property
    def spans_calendar_years(self) -> bool:
        return self.closes_on <= self.opens_on


SPORT_SEASON_RULES: dict[str, SeasonRule] = {
    "MLB": SeasonRule(
        sport="MLB",
        display_name="Baseball (MLB)",
        id_style=SeasonIdStyle.CALENDAR_YEAR,
        opens_on=(3, 1),
        closes_on=(11, 30),
        basis=(
            "Configured accounting window for the MLB calendar-year season. "
            "MLB is the one sport whose games AISP ingests, so the account's "
            "actual tracking start comes from its real first funded decision "
            "rather than from this date."
        ),
    ),
    "NFL": SeasonRule(
        sport="NFL",
        display_name="Football (NFL)",
        id_style=SeasonIdStyle.CALENDAR_YEAR,
        opens_on=(9, 1),
        closes_on=(2, 28),
        basis=(
            "Configured accounting window. The season is identified by the "
            "year it begins, and the window runs into the following February "
            "so that the postseason settles inside the season it completes."
        ),
    ),
    "NBA": SeasonRule(
        sport="NBA",
        display_name="Basketball (NBA)",
        id_style=SeasonIdStyle.SPLIT_YEAR,
        opens_on=(10, 1),
        closes_on=(6, 30),
        basis=(
            "Configured accounting window for a season that crosses New Year "
            "and is identified by both years."
        ),
    ),
    "NHL": SeasonRule(
        sport="NHL",
        display_name="Hockey (NHL)",
        id_style=SeasonIdStyle.SPLIT_YEAR,
        opens_on=(10, 1),
        closes_on=(6, 30),
        basis=(
            "Configured accounting window for a season that crosses New Year "
            "and is identified by both years."
        ),
    ),
    "MMA": SeasonRule(
        sport="MMA",
        display_name="Mixed martial arts",
        id_style=SeasonIdStyle.CALENDAR_YEAR,
        opens_on=(1, 1),
        closes_on=(12, 31),
        basis=(
            "MMA has no season. This is a calendar-year campaign adopted so "
            "the sport can hold an account on the same terms as the others."
        ),
        convention=(
            "Calendar-year campaign — a product convention for this program, "
            "not a league season."
        ),
    ),
}


def supported_sports() -> list[str]:
    """Program sports, in the order the overview presents them."""

    return ["MLB", "NFL", "NBA", "NHL", "MMA"]


def rule_for(sport: str) -> SeasonRule:
    try:
        return SPORT_SEASON_RULES[sport.upper()]
    except KeyError as error:  # pragma: no cover - guarded by callers
        raise KeyError(f"{sport} is not a season-account sport") from error


@dataclass(frozen=True)
class SeasonWindow:
    """One concrete season: its identifier, label and accounting boundaries."""

    sport: str
    season_id: str
    label: str
    opens_on: date
    closes_on: date
    basis: str
    convention: str | None

    def contains(self, day: date) -> bool:
        return self.opens_on <= day <= self.closes_on


def _starting_year(season_id: str, rule: SeasonRule) -> int:
    if rule.id_style is SeasonIdStyle.SPLIT_YEAR:
        return int(season_id.split("-", 1)[0])
    return int(season_id)


def _season_id(starting_year: int, rule: SeasonRule) -> str:
    if rule.id_style is SeasonIdStyle.SPLIT_YEAR:
        return f"{starting_year}-{(starting_year + 1) % 100:02d}"
    return str(starting_year)


def window_for(sport: str, season_id: str) -> SeasonWindow:
    """The accounting window for one named season."""

    rule = rule_for(sport)
    start_year = _starting_year(season_id, rule)
    end_year = start_year + 1 if rule.spans_calendar_years else start_year
    label = (
        f"{rule.display_name} {season_id}"
        if rule.convention is None
        else f"{rule.display_name} {season_id} campaign"
    )
    return SeasonWindow(
        sport=rule.sport,
        season_id=_season_id(start_year, rule),
        label=label,
        opens_on=date(start_year, *rule.opens_on),
        closes_on=date(end_year, *rule.closes_on),
        basis=rule.basis,
        convention=rule.convention,
    )


def season_for_date(sport: str, day: date) -> SeasonWindow:
    """Which season a date belongs to.

    For a season that crosses New Year, a date in January–June belongs to the
    season that *began* the previous autumn. This is what keeps an NFL playoff
    game in February, or an NBA game in April, attached to the season it
    completes rather than opening a new account mid-playoffs.
    """

    rule = rule_for(sport)
    candidate = window_for(sport, _season_id(day.year, rule))
    if candidate.contains(day):
        return candidate
    if rule.spans_calendar_years:
        # Before this year's opening date: the season that began last year is
        # still running.
        previous = window_for(sport, _season_id(day.year - 1, rule))
        if previous.contains(day):
            return previous
    # A date in the off-season belongs to no open window. It is attributed to
    # the season that has most recently begun, which is where a late
    # settlement from that season must land.
    if day < candidate.opens_on:
        return window_for(sport, _season_id(day.year - 1, rule))
    return candidate


def program_season_for(sport: str, day: date) -> SeasonWindow:
    """The season this sport's account should currently represent.

    This is deliberately different from :func:`season_for_date`. That function
    answers "which season does this date's activity belong to", which is what a
    late settlement needs: a result arriving in the off-season still belongs to
    the season that produced it. This one answers "which season should the
    overview show", and in the off-season the honest answer is the season about
    to open, not the one that finished.

    Getting these the same way round would either show a finished season as the
    current one all summer, or file a September settlement into a season that
    has not started.
    """

    rule = rule_for(sport)
    current = window_for(sport, _season_id(day.year, rule))
    if current.contains(day):
        return current
    if rule.spans_calendar_years:
        previous = window_for(sport, _season_id(day.year - 1, rule))
        if previous.contains(day):
            return previous
    # Off-season: the next window to open.
    if day < current.opens_on:
        return current
    return window_for(sport, _season_id(day.year + 1, rule))
