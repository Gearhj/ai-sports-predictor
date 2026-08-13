from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx

from .base import (
    AdapterResponse,
    PitcherDTO,
    ScheduleGameDTO,
    SourceEnvelope,
    SourceError,
    TeamDTO,
    TeamStatsDTO,
    VenueDTO,
)


class MLBStatsAPIAdapter:
    """Adapter for stable, public MLB Stats API schedule and season summaries."""

    source = "MLB_STATS_API"
    base_url = "https://statsapi.mlb.com/api/v1"

    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 15.0) -> None:
        self._client = client
        self.timeout = timeout

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise SourceError(self.source, "MLB returned an unexpected payload", retryable=False)
            return payload
        except SourceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(self.source, f"MLB request failed: {type(exc).__name__}") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def get_schedule(self, event_date: date) -> AdapterResponse:
        raw = await self._get(
            "/schedule",
            {
                "sportId": 1,
                "date": event_date.isoformat(),
                "hydrate": "probablePitcher,team,venue,linescore",
            },
        )
        pitcher_ids = {
            str(pitcher["id"])
            for date_node in raw.get("dates", [])
            for item in date_node.get("games", [])
            for side in (item.get("teams") or {}).values()
            if (pitcher := side.get("probablePitcher")) and pitcher.get("id") is not None
        }
        people_raw: dict[str, Any] = {}
        pitcher_hands: dict[str, str] = {}
        enrichment_warning: str | None = None
        if pitcher_ids:
            try:
                people_raw = await self._get("/people", {"personIds": ",".join(sorted(pitcher_ids))})
                pitcher_hands = {
                    str(person["id"]): str((person.get("pitchHand") or {}).get("code", "")).upper()
                    for person in people_raw.get("people", [])
                    if person.get("id") is not None and (person.get("pitchHand") or {}).get("code")
                }
            except SourceError as exc:
                # Handedness is optional enrichment. A transient people lookup
                # must not make the authoritative schedule unavailable.
                enrichment_warning = str(exc)
        games: list[ScheduleGameDTO] = []
        for date_node in raw.get("dates", []):
            for item in date_node.get("games", []):
                teams = item.get("teams", {})
                venue = item.get("venue") or {"name": "Venue unavailable"}
                games.append(
                    ScheduleGameDTO(
                        source_game_id=str(item["gamePk"]),
                        scheduled_at=item["gameDate"],
                        event_date=event_date,
                        status=(item.get("status") or {}).get("detailedState", "Scheduled"),
                        away_team=self._team(teams.get("away") or {}),
                        home_team=self._team(teams.get("home") or {}),
                        away_pitcher=self._pitcher(teams.get("away") or {}, pitcher_hands),
                        home_pitcher=self._pitcher(teams.get("home") or {}, pitcher_hands),
                        venue=VenueDTO(source_id=str(venue.get("id", "")) or None, name=venue["name"]),
                        away_score=_to_int((teams.get("away") or {}).get("score")),
                        home_score=_to_int((teams.get("home") or {}).get("score")),
                        final_at=(item.get("gameDate") if "final" in (item.get("status") or {}).get("detailedState", "").lower() else None),
                    )
                )
        fetched_at = datetime.now(UTC)
        return AdapterResponse(
            envelope=SourceEnvelope(
                source=self.source,
                fetched_at=fetched_at,
                data=[game.model_dump(mode="json") for game in games],
                metadata={
                    "game_count": len(games),
                    "event_date": event_date.isoformat(),
                    "pitcher_handedness_enrichment_warning": enrichment_warning,
                },
            ),
            raw_payload={"schedule": raw, "people": people_raw},
        )

    async def get_team_stats(self, source_team_id: str, season: int) -> AdapterResponse:
        raw = await self._get(
            f"/teams/{source_team_id}/stats",
            {"stats": "season", "group": "hitting,pitching", "season": season},
        )
        hitting: dict[str, Any] = {}
        pitching: dict[str, Any] = {}
        for block in raw.get("stats", []):
            group = (block.get("group") or {}).get("displayName", "").lower()
            splits = block.get("splits") or []
            stat = (splits[0].get("stat") if splits else {}) or {}
            if group == "hitting":
                hitting = stat
            elif group == "pitching":
                pitching = stat
        runs = _to_float(hitting.get("runs"))
        games = _to_int(hitting.get("gamesPlayed"))
        normalized = TeamStatsDTO(
            source_team_id=source_team_id,
            available_as_of=datetime.now(UTC),
            games_played=games,
            runs_per_game=(runs / games if runs is not None and games else None),
            ops=_to_float(hitting.get("ops")),
            obp=_to_float(hitting.get("obp")),
            slg=_to_float(hitting.get("slg")),
            strikeout_rate=_rate(hitting.get("strikeOuts"), hitting.get("plateAppearances")),
            walk_rate=_rate(hitting.get("baseOnBalls"), hitting.get("plateAppearances")),
            era=_to_float(pitching.get("era")),
            whip=_to_float(pitching.get("whip")),
            raw_metrics={"hitting": hitting, "pitching": pitching},
        )
        return AdapterResponse(
            envelope=SourceEnvelope(
                source=self.source,
                fetched_at=normalized.available_as_of,
                data=normalized.model_dump(mode="json"),
                metadata={"team_id": source_team_id, "season": season},
            ),
            raw_payload=raw,
        )

    @staticmethod
    def _team(side: dict[str, Any]) -> TeamDTO:
        team = side.get("team") or {}
        record = side.get("leagueRecord") or {}
        return TeamDTO(
            source_id=str(team.get("id", "")),
            name=team.get("name", "Unknown team"),
            record_wins=_to_int(record.get("wins")),
            record_losses=_to_int(record.get("losses")),
        )

    @staticmethod
    def _pitcher(side: dict[str, Any], pitcher_hands: dict[str, str] | None = None) -> PitcherDTO | None:
        pitcher = side.get("probablePitcher")
        if not pitcher:
            return None
        return PitcherDTO(
            source_id=str(pitcher.get("id", "")) or None,
            full_name=pitcher.get("fullName"),
            throws=(pitcher_hands or {}).get(str(pitcher.get("id"))),
            # The MLB hydrate only identifies a probable pitcher. Confirmation
            # requires a distinct lineup/club signal and must not be inferred.
            confirmed=False,
        )


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rate(numerator: Any, denominator: Any) -> float | None:
    num, den = _to_float(numerator), _to_float(denominator)
    return num / den if num is not None and den else None
