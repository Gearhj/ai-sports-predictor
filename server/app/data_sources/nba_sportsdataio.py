"""SportsDataIO NBA adapter: endpoints, parsers and a closed transport gate.

The parsers are the durable part and they run on bytes from anywhere -- a
provider response or a local fixture -- so the whole canonical path can be
built, tested and reviewed before anybody is entitled to make a request.

The transport is the opposite: it refuses to exist unless
``app.nba.entitlement`` says collection is permitted. That check happens before
a client is constructed, not before a request is sent, so there is no window in
which a misconfigured caller reaches the network.

Endpoint paths are the documented commercial NBA ones. Writing them down is
documentation; it is not a claim that this account may call them.

Authentication, when it is ever permitted, uses the ``Ocp-Apim-Subscription-Key``
HEADER rather than the query string, so a credential cannot end up in a URL,
a log line or an exception message.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from app.nba.entitlement import DataMode

BASE_URL = "https://api.sportsdata.io/v3/nba"
AUTH_HEADER = "Ocp-Apim-Subscription-Key"
SCHEMA_VERSION = "AISP_NBA_SPORTSDATAIO_ADAPTER_V1"

#: Documented commercial NBA paths, relative to BASE_URL.
ENDPOINTS: dict[str, str] = {
    "schedule": "/scores/{format}/Games/{season}",
    "games_by_date_final": "/scores/{format}/GamesByDateFinal/{date}",
    "box_score_final": "/stats/{format}/BoxScoreFinal/{gameid}",
    "box_scores_by_date_final": "/stats/{format}/BoxScoresFinal/{date}",
    "player_games_by_date_final": "/stats/{format}/PlayerGameStatsByDateFinal/{date}",
    "play_by_play_final": "/pbp/{format}/PlayByPlayFinal/{gameid}",
    "teams": "/scores/{format}/AllTeams",
    "players_by_team": "/scores/{format}/Players/{team}",
}

#: NBA season types as the provider numbers them.
SEASON_TYPES = {1: "REGULAR", 2: "PRESEASON", 3: "POSTSEASON", 4: "ALLSTAR"}

#: Provider date format used in path parameters, e.g. 2026-JAN-14.
_PROVIDER_DATE = "%Y-%b-%d"


class NbaAdapterError(RuntimeError):
    """The adapter refused. The message is the reason, verbatim."""


def provider_date(value: date) -> str:
    return value.strftime(_PROVIDER_DATE).upper()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _aware(value: Any) -> datetime | None:
    """Parse a provider timestamp.

    Provider datetimes are US Eastern wall clock WITHOUT an offset. They are
    parsed as naive and marked as such; this adapter does not guess a zone and
    silently produce an instant that is wrong for half the year. The caller
    normalises with the venue's own zone where one is known.
    """

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


@dataclass(frozen=True)
class Envelope:
    """One original response, with everything needed to audit it later."""

    endpoint_key: str
    url_template: str
    parameters: dict[str, Any]
    payload: bytes
    retrieved_at: datetime
    data_mode: DataMode
    provider_updated_at: datetime | None = None
    source_identity: str = "SPORTSDATAIO_NBA"
    notes: tuple[str, ...] = ()

    @property
    def payload_checksum(self) -> str:
        return _sha256(self.payload)

    @property
    def byte_count(self) -> int:
        return len(self.payload)

    def metadata(self) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "sourceIdentity": self.source_identity,
            "endpointKey": self.endpoint_key,
            "urlTemplate": self.url_template,
            "parameters": self.parameters,
            "payloadChecksum": self.payload_checksum,
            "byteCount": self.byte_count,
            # Retrieval time is when WE received the bytes. It is never the
            # event time and never the provider's own update time.
            "retrievedAtUtc": self.retrieved_at.astimezone(UTC).isoformat(),
            "providerUpdatedAt": (
                self.provider_updated_at.isoformat()
                if self.provider_updated_at else None
            ),
            "dataMode": str(self.data_mode),
            "notes": list(self.notes),
        }

    def decode(self) -> Any:
        try:
            return json.loads(self.payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise NbaAdapterError(f"payload is not decodable JSON: {exc}") from exc


# ------------------------------------------------------------------ parsers --

@dataclass(frozen=True)
class NbaTeam:
    provider_team_id: int
    key: str
    city: str | None
    name: str | None
    active: bool | None

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.city, self.name) if part) or self.key


@dataclass(frozen=True)
class NbaPlayer:
    provider_player_id: int
    provider_team_id: int | None
    first_name: str | None
    last_name: str | None
    position: str | None
    jersey: int | None
    status: str | None

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p) or str(
            self.provider_player_id)


@dataclass(frozen=True)
class NbaGame:
    provider_game_id: int
    season: int
    season_type: int
    status: str | None
    home_team_id: int
    away_team_id: int
    home_team_key: str | None
    away_team_key: str | None
    game_day: date | None
    #: Provider local (US Eastern) wall clock, no offset supplied.
    datetime_local: datetime | None
    datetime_utc: datetime | None
    home_score: int | None
    away_score: int | None
    quarters: tuple[dict[str, Any], ...] = ()
    updated_at: datetime | None = None
    is_closed: bool | None = None

    @property
    def season_type_label(self) -> str:
        return SEASON_TYPES.get(self.season_type, f"UNKNOWN_{self.season_type}")

    @property
    def is_final(self) -> bool:
        return str(self.status or "").strip().lower() in {"final", "f/ot", "f"}

    def period_totals(self) -> tuple[int | None, int | None]:
        """Home and away totals summed from the period lines, or None."""

        home = away = 0
        seen = False
        for quarter in self.quarters:
            h, a = quarter.get("HomeScore"), quarter.get("AwayScore")
            if h is None or a is None:
                return None, None
            home += int(h)
            away += int(a)
            seen = True
        return (home, away) if seen else (None, None)


@dataclass(frozen=True)
class NbaTeamGame:
    provider_game_id: int
    provider_team_id: int
    opponent_team_id: int | None
    is_home: bool | None
    points: int | None
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NbaPlayerGame:
    provider_stat_id: int | None
    provider_player_id: int
    provider_game_id: int
    provider_team_id: int | None
    opponent_team_id: int | None
    name: str | None
    position: str | None
    started: bool | None
    minutes: int | None
    points: int | None
    stats: dict[str, Any] = field(default_factory=dict)


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    return None


def parse_teams(document: Any) -> tuple[NbaTeam, ...]:
    rows = document if isinstance(document, list) else []
    return tuple(
        NbaTeam(
            provider_team_id=int(row["TeamID"]),
            key=str(row.get("Key") or "").strip(),
            city=(row.get("City") or None),
            name=(row.get("Name") or None),
            active=_bool(row.get("Active")),
        )
        for row in rows if row.get("TeamID") is not None
    )


def parse_players(document: Any) -> tuple[NbaPlayer, ...]:
    rows = document if isinstance(document, list) else []
    return tuple(
        NbaPlayer(
            provider_player_id=int(row["PlayerID"]),
            provider_team_id=_int(row.get("TeamID")),
            first_name=(row.get("FirstName") or None),
            last_name=(row.get("LastName") or None),
            position=(row.get("Position") or None),
            jersey=_int(row.get("Jersey")),
            status=(row.get("Status") or None),
        )
        for row in rows if row.get("PlayerID") is not None
    )


def _game_from(row: dict[str, Any], quarters: tuple[dict[str, Any], ...] = ()) -> NbaGame:
    day_text = str(row.get("Day") or row.get("DateTime") or "")[:10]
    try:
        game_day = date.fromisoformat(day_text) if day_text else None
    except ValueError:
        game_day = None
    return NbaGame(
        provider_game_id=int(row["GameID"]),
        season=int(row.get("Season") or 0),
        season_type=int(row.get("SeasonType") or 0),
        status=(row.get("Status") or None),
        home_team_id=int(row.get("HomeTeamID") or 0),
        away_team_id=int(row.get("AwayTeamID") or 0),
        home_team_key=(row.get("HomeTeam") or None),
        away_team_key=(row.get("AwayTeam") or None),
        game_day=game_day,
        datetime_local=_aware(row.get("DateTime")),
        datetime_utc=_aware(row.get("DateTimeUTC")),
        home_score=_int(row.get("HomeTeamScore")),
        away_score=_int(row.get("AwayTeamScore")),
        quarters=quarters,
        updated_at=_aware(row.get("Updated")),
        is_closed=_bool(row.get("IsClosed")),
    )


def parse_games(document: Any) -> tuple[NbaGame, ...]:
    rows = document if isinstance(document, list) else []
    return tuple(_game_from(row) for row in rows if row.get("GameID") is not None)


@dataclass(frozen=True)
class NbaBoxScore:
    game: NbaGame
    team_games: tuple[NbaTeamGame, ...]
    player_games: tuple[NbaPlayerGame, ...]


def parse_box_score(document: Any) -> NbaBoxScore:
    """One BoxScore object into game, team and player rows."""

    if not isinstance(document, dict):
        raise NbaAdapterError("box score payload is not an object")
    game_row = document.get("Game")
    if not isinstance(game_row, dict) or game_row.get("GameID") is None:
        raise NbaAdapterError("box score carries no Game with a GameID")
    quarters = tuple(
        q for q in (document.get("Quarters") or []) if isinstance(q, dict)
    )
    game = _game_from(game_row, quarters)

    team_games = tuple(
        NbaTeamGame(
            provider_game_id=int(row.get("GameID") or game.provider_game_id),
            provider_team_id=int(row["TeamID"]),
            opponent_team_id=_int(row.get("OpponentID")),
            is_home=_bool(row.get("HomeOrAway") == "HOME") if row.get("HomeOrAway")
            else (int(row["TeamID"]) == game.home_team_id),
            points=_int(row.get("Points")),
            stats={k: v for k, v in row.items()
                   if k not in {"GameID", "TeamID", "OpponentID", "Points"}},
        )
        for row in (document.get("TeamGames") or [])
        if isinstance(row, dict) and row.get("TeamID") is not None
    )

    player_games = tuple(
        NbaPlayerGame(
            provider_stat_id=_int(row.get("StatID")),
            provider_player_id=int(row["PlayerID"]),
            provider_game_id=int(row.get("GameID") or game.provider_game_id),
            provider_team_id=_int(row.get("TeamID")),
            opponent_team_id=_int(row.get("OpponentID")),
            name=(row.get("Name") or None),
            position=(row.get("Position") or None),
            started=_bool(row.get("Started")),
            minutes=_int(row.get("Minutes")),
            points=_int(row.get("Points")),
            stats={k: v for k, v in row.items()
                   if k not in {"StatID", "PlayerID", "GameID", "TeamID",
                                "OpponentID", "Name", "Position", "Started",
                                "Minutes", "Points"}},
        )
        for row in (document.get("PlayerGames") or [])
        if isinstance(row, dict) and row.get("PlayerID") is not None
    )
    return NbaBoxScore(game=game, team_games=team_games, player_games=player_games)


# ---------------------------------------------------------------- transport --

class NbaSportsDataIoSource:
    """The network client. Refuses to exist while collection is not permitted."""

    def __init__(self, settings: Any, entitlement: Any, *, transport: Any = None) -> None:
        if not getattr(entitlement, "network_collection_allowed", False):
            raise NbaAdapterError(
                "NBA collection is not permitted: "
                f"{getattr(entitlement, 'verdict', 'UNKNOWN')}. "
                f"{getattr(entitlement, 'missing_prerequisite', '') or ''} "
                "No client was constructed and no request was made."
            )
        self._settings = settings
        self._entitlement = entitlement
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        key = str(getattr(self._settings, "sportsdataio_api_key", "") or "")
        if not key:
            raise NbaAdapterError("no credential is configured")
        # Header, never the query string.
        return {AUTH_HEADER: key, "Accept": "application/json"}

    def url(self, endpoint_key: str, **parameters: Any) -> str:
        template = ENDPOINTS.get(endpoint_key)
        if template is None:
            raise NbaAdapterError(f"unknown endpoint {endpoint_key!r}")
        return BASE_URL + template.format(format="json", **parameters)


__all__ = [
    "AUTH_HEADER", "BASE_URL", "ENDPOINTS", "SCHEMA_VERSION", "SEASON_TYPES",
    "Envelope", "NbaAdapterError", "NbaBoxScore", "NbaGame", "NbaPlayer",
    "NbaPlayerGame", "NbaSportsDataIoSource", "NbaTeam", "NbaTeamGame",
    "parse_box_score", "parse_games", "parse_players", "parse_teams",
    "provider_date",
]
