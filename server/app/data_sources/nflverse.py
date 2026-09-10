"""NFL final scores from the nflverse schedules release.

WHY THIS SOURCE
---------------
AISP's NFL event identity is *already* nflverse's. Every stored forecast
carries ``source_event_id = "nflverse:2026_01_NE_SEA"`` and every event id in
``shadow_forecasts`` is a verbatim nflverse ``game_id``. So the join between a
result row and the game it belongs to needs no fuzzy matching on team names or
kickoff times -- the two systems already agree on what a game is called, which
removes the single largest source of silent mis-attribution in result feeds.

It is also free. The schedules release is a public GitHub release asset under
CC-BY-4.0: no key, no account, no quota, no per-request cost. The alternative
already wired into this codebase (``finalize-nfl-shadow-games``) reads The Odds
API scores endpoint, which draws on the same paid allowance we reserve for
sportsbook prices -- spending price credits to learn a score that a free,
better-identified feed publishes anyway is a poor trade.

THE CHEAP PROBE
---------------
The release publishes ``timestamp.json`` beside ``games.csv``. It is 43 bytes
against the CSV's ~2 MB, so the polling loop asks the small file whether
anything changed and downloads the large one only when it has. A quiet hour
costs one request and 43 bytes.

WHAT THIS DELIBERATELY THROWS AWAY
----------------------------------
``games.csv`` carries ``away_moneyline``, ``spread_line``, ``total_line`` and
friends. They are dropped at the parse boundary and never reach a DTO. They are
one aggregated line per game with no sportsbook, no observation timestamp and
no two-sided pairing, so treating them as market evidence would manufacture a
"quote" that no book ever posted at a time nobody recorded. NE @ SEA has no
eligible persisted quote, and it must keep saying so.

PROVENANCE HONESTY
------------------
nflverse is a trustworthy automated mirror, not the league's own wire and not
the governed provider. Results from it are labelled ``NFLVERSE`` and rank below
``THE_ODDS_API`` in the result hierarchy. Nothing here may be presented as
provider-verified.
"""
from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.data_sources.base import SourceError

#: The source string that travels with every row this module produces.
NFLVERSE = "NFLVERSE"

RELEASE = "https://github.com/nflverse/nflverse-data/releases/download/schedules"
TIMESTAMP_URL = f"{RELEASE}/timestamp.json"
GAMES_URL = f"{RELEASE}/games.csv"

#: nflverse publishes kickoff as a local Eastern date and clock time.
EASTERN = ZoneInfo("America/New_York")

#: Attribution required by CC-BY-4.0, carried into every archived payload.
ATTRIBUTION = "nflverse-data schedules release (https://github.com/nflverse/nflverse-data), CC-BY-4.0"

#: Price columns present in the CSV and intentionally never parsed. Named here
#: so the omission reads as a decision rather than an oversight.
IGNORED_PRICE_COLUMNS = (
    "away_moneyline", "home_moneyline", "spread_line", "away_spread_odds",
    "home_spread_odds", "total_line", "under_odds", "over_odds",
)


def _int(value: str | None) -> int | None:
    text = (value or "").strip()
    if not text or text.upper() in {"NA", "NULL", "NONE"}:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


@dataclass(frozen=True)
class NflverseGame:
    """One scheduled or completed game, as the feed describes it."""

    game_id: str
    season: int | None
    game_type: str | None
    week: int | None
    gameday: str | None
    gametime: str | None
    weekday: str | None
    away_team: str | None
    home_team: str | None
    away_score: int | None
    home_score: int | None
    overtime: bool | None
    location: str | None
    espn_id: str | None
    gsis_id: str | None
    pfr_id: str | None
    old_game_id: str | None

    @property
    def is_final(self) -> bool:
        """Both scores published. The feed carries no explicit status column.

        A game in progress and a game that never kicked off look identical
        here -- both have empty scores -- so this says only "a result exists",
        never "the game is over but we have no score", and the caller treats a
        started game with no scores as still awaiting a final.
        """

        return self.home_score is not None and self.away_score is not None

    @property
    def scheduled_utc(self) -> datetime | None:
        """Kickoff as the feed currently states it, in UTC.

        Used only to detect that a game has been MOVED. It never overwrites the
        kickoff a forecast was frozen against: eligibility was decided at the
        time by the schedule as it then stood, and re-deciding it against a
        later correction would be hindsight.
        """

        if not self.gameday:
            return None
        clock = self.gametime or "00:00"
        try:
            naive = datetime.fromisoformat(f"{self.gameday}T{clock}")
        except ValueError:
            return None
        return naive.replace(tzinfo=EASTERN).astimezone(UTC)

    def result_evidence(self) -> dict[str, Any]:
        """The result facts, and ONLY the result facts.

        This dict is what gets checksummed into the result identity, so it
        deliberately excludes the retrieval time and the feed's own
        last-updated stamp. Including either would mint a fresh identity on
        every refresh and turn one game into an unbounded stream of
        "corrections" that all say the same thing.
        """

        return {
            "source": NFLVERSE,
            "source_event_id": self.game_id,
            "season": self.season,
            "game_type": self.game_type,
            "week": self.week,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "overtime": self.overtime,
            "status": "FINAL" if self.is_final else "SCHEDULED",
        }


@dataclass(frozen=True)
class NflverseSchedule:
    """One retrieval of the schedules release."""

    fetched_at: datetime
    last_updated: str | None
    games: dict[str, NflverseGame]
    payload_checksum: str
    row_count: int

    def get(self, game_id: str) -> NflverseGame | None:
        return self.games.get(game_id)

    def envelope_metadata(self) -> dict[str, Any]:
        return {
            "source": NFLVERSE,
            "release": RELEASE,
            "attribution": ATTRIBUTION,
            "feed_last_updated": self.last_updated,
            "fetched_at": self.fetched_at.isoformat(),
            "rows": self.row_count,
            "payload_checksum": self.payload_checksum,
            "cost": {"currency": "USD", "amount": 0.0, "quota_consumed": 0,
                     "note": "Public GitHub release asset; no key and no metered allowance."},
            "ignored_price_columns": list(IGNORED_PRICE_COLUMNS),
        }


def parse_games_csv(payload: bytes) -> tuple[dict[str, NflverseGame], str]:
    """Parse the release CSV into games by id, with the payload checksum."""

    checksum = hashlib.sha256(payload).hexdigest()
    text = payload.decode("utf-8-sig", errors="strict")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "game_id" not in reader.fieldnames:
        raise SourceError(NFLVERSE, "The schedules CSV has no game_id column.", retryable=False)

    games: dict[str, NflverseGame] = {}
    for row in reader:
        game_id = _text(row.get("game_id"))
        if not game_id:
            continue
        overtime = _int(row.get("overtime"))
        games[game_id] = NflverseGame(
            game_id=game_id,
            season=_int(row.get("season")),
            game_type=_text(row.get("game_type")),
            week=_int(row.get("week")),
            gameday=_text(row.get("gameday")),
            gametime=_text(row.get("gametime")),
            weekday=_text(row.get("weekday")),
            away_team=_text(row.get("away_team")),
            home_team=_text(row.get("home_team")),
            away_score=_int(row.get("away_score")),
            home_score=_int(row.get("home_score")),
            overtime=None if overtime is None else bool(overtime),
            location=_text(row.get("location")),
            espn_id=_text(row.get("espn")),
            gsis_id=_text(row.get("gsis")),
            pfr_id=_text(row.get("pfr")),
            old_game_id=_text(row.get("old_game_id")),
        )
    return games, checksum


class NflverseScheduleSource:
    """Reads the schedules release. Sends no credentials and spends nothing."""

    source = NFLVERSE

    def __init__(self, *, timeout: float = 25.0, transport: Any = None) -> None:
        self.timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True, transport=self._transport,
            headers={"User-Agent": "aisp-nfl-research/1.0 (+research evaluation, no credentials)"},
        )

    async def probe(self) -> str | None:
        """The feed's own last-updated stamp. 43 bytes, so poll this first."""

        async with self._client() as client:
            try:
                response = await client.get(TIMESTAMP_URL)
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise SourceError(NFLVERSE, f"timestamp.json returned {error.response.status_code}",
                                  status_code=error.response.status_code) from error
            except httpx.HTTPError as error:
                raise SourceError(NFLVERSE, f"timestamp.json unreachable: {error}") from error
            try:
                return str(response.json().get("last_updated") or "").strip() or None
            except ValueError:
                return None

    async def fetch(self, *, now: datetime | None = None) -> NflverseSchedule:
        """Download and parse the full schedule."""

        now = now or datetime.now(UTC)
        last_updated = await self.probe()
        async with self._client() as client:
            try:
                response = await client.get(GAMES_URL)
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise SourceError(NFLVERSE, f"games.csv returned {error.response.status_code}",
                                  status_code=error.response.status_code) from error
            except httpx.HTTPError as error:
                raise SourceError(NFLVERSE, f"games.csv unreachable: {error}") from error
            payload = response.content

        games, checksum = parse_games_csv(payload)
        if not games:
            raise SourceError(NFLVERSE, "The schedules CSV parsed to zero games.", retryable=False)
        return NflverseSchedule(
            fetched_at=now, last_updated=last_updated, games=games,
            payload_checksum=checksum, row_count=len(games),
        )


__all__ = [
    "ATTRIBUTION", "GAMES_URL", "IGNORED_PRICE_COLUMNS", "NFLVERSE", "NflverseGame",
    "NflverseSchedule", "NflverseScheduleSource", "TIMESTAMP_URL", "parse_games_csv",
]
