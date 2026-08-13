from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from .base import (
    AdapterResponse,
    BullpenAvailabilityDTO,
    HistoricalBoxscoreDTO,
    HistoricalFinalDTO,
    HistoricalPlayerGameEvidenceDTO,
    HistoricalScheduleGameDTO,
    HistoricalTeamGameEvidenceDTO,
    LineupDTO,
    LineupEntryDTO,
    PitcherDTO,
    RosterAvailabilityDTO,
    ScheduleGameDTO,
    SourceEnvelope,
    SourceError,
    TeamDTO,
    TeamStatsDTO,
    VenueDTO,
)
from .request_policy import ConservativeRequestExecutor


class MLBStatsAPIAdapter:
    """Adapter for stable, public MLB Stats API schedule and season summaries."""

    source = "MLB_STATS_API"
    base_url = "https://statsapi.mlb.com/api/v1"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
        request_executor: ConservativeRequestExecutor | None = None,
    ) -> None:
        self._client = client
        self.timeout = timeout
        self.request_executor = request_executor

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            async def request_once() -> dict[str, Any]:
                response = await client.get(f"{self.base_url}{path}", params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise SourceError(
                        self.source,
                        "MLB returned an unexpected payload",
                        retryable=False,
                    )
                return payload

            if self.request_executor is not None:
                return await self.request_executor.execute(request_once)
            return await request_once()
        except SourceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(self.source, f"MLB request failed: {type(exc).__name__}") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def get_historical_schedule(self, event_date: date) -> AdapterResponse:
        """Fetch one regular-season date as explicitly reconstructed evidence."""

        raw = await self._get(
            "/schedule",
            {
                "sportId": 1,
                "date": event_date.isoformat(),
                "gameType": "R",
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
                people_raw = await self._get(
                    "/people",
                    {"personIds": ",".join(sorted(pitcher_ids))},
                )
                pitcher_hands = {
                    str(person["id"]): str((person.get("pitchHand") or {}).get("code", "")).upper()
                    for person in people_raw.get("people", [])
                    if person.get("id") is not None and (person.get("pitchHand") or {}).get("code")
                }
            except SourceError as exc:
                # This is optional metadata and does not change schedule/final
                # identity. The failed physical request remains fully counted.
                enrichment_warning = str(exc)
        acquired_at = datetime.now(UTC)
        rows: list[HistoricalScheduleGameDTO] = []
        try:
            for date_node in raw.get("dates", []):
                official_date = date.fromisoformat(str(date_node.get("date") or event_date.isoformat()))
                for item in date_node.get("games", []):
                    game = self._historical_schedule_game(
                        item,
                        official_date=official_date,
                        pitcher_hands=pitcher_hands,
                    )
                    final = self._historical_final(item, official_date, acquired_at)
                    rows.append(HistoricalScheduleGameDTO(
                        game=game,
                        season=_to_int(item.get("season")) or official_date.year,
                        official_date=official_date,
                        game_type=item.get("gameType"),
                        abstract_state=(item.get("status") or {}).get("abstractGameState"),
                        coded_state=(item.get("status") or {}).get("codedGameState"),
                        acquired_at=acquired_at,
                        final=final,
                    ))
        except (KeyError, TypeError, ValueError) as exc:
            error = SourceError(
                self.source,
                f"Historical schedule schema validation failed: {type(exc).__name__}",
                retryable=False,
            )
            error.raw_payload = {"schedule": raw, "people": people_raw}  # type: ignore[attr-defined]
            raise error from exc
        return AdapterResponse(
            envelope=SourceEnvelope(
                source=self.source,
                source_mode="LIVE",
                fetched_at=acquired_at,
                data=[row.model_dump(mode="json") for row in rows],
                metadata={
                    "event_date": event_date.isoformat(),
                    "record_count": len(rows),
                    "research_provenance": "HISTORICAL_RECONSTRUCTION",
                    "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                    "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
                    "game_type": "R",
                    "pitcher_handedness_enrichment_warning": enrichment_warning,
                },
            ),
            raw_payload={"schedule": raw, "people": people_raw},
        )

    async def get_historical_final(self, source_game_id: str) -> AdapterResponse:
        """Fetch the final event fact without claiming historical publication time."""

        raw = await self._get(f"/game/{source_game_id}/feed/live", {})
        acquired_at = datetime.now(UTC)
        try:
            game_data = raw["gameData"]
            live_data = raw["liveData"]
            status = game_data.get("status") or {}
            abstract_state = str(status.get("abstractGameState") or "").strip()
            if abstract_state.lower() != "final":
                raise ValueError("game status is not final")
            datetime_node = game_data.get("datetime") or {}
            official_date = date.fromisoformat(str(datetime_node["officialDate"]))
            linescore = live_data.get("linescore") or {}
            teams = linescore.get("teams") or {}
            home_score = _to_int((teams.get("home") or {}).get("runs"))
            away_score = _to_int((teams.get("away") or {}).get("runs"))
            if home_score is None or away_score is None:
                raise ValueError("final scores are missing")
            final = HistoricalFinalDTO(
                source_game_id=source_game_id,
                official_date=official_date,
                status=status.get("detailedState") or "Unknown",
                abstract_state=abstract_state,
                home_score=home_score,
                away_score=away_score,
                innings_played=_to_int(linescore.get("currentInning")),
                # gameData.datetime.dateTime is scheduled first pitch, not a
                # trustworthy completion timestamp. Preserve that uncertainty.
                event_completed_at=None,
                acquired_at=acquired_at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            error = SourceError(
                self.source,
                f"Historical final schema validation failed: {type(exc).__name__}",
                retryable=False,
            )
            error.raw_payload = raw  # type: ignore[attr-defined]
            raise error from exc
        return AdapterResponse(
            envelope=SourceEnvelope(
                source=self.source,
                source_mode="LIVE",
                fetched_at=acquired_at,
                data=final.model_dump(mode="json"),
                metadata={
                    "game_id": source_game_id,
                    "research_provenance": "HISTORICAL_RECONSTRUCTION",
                    "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                    "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
                },
            ),
            raw_payload=raw,
        )

    async def get_historical_boxscore(self, source_game_id: str) -> AdapterResponse:
        """Fetch actual player/team participation as retrospective evidence."""

        raw = await self._get(f"/game/{source_game_id}/boxscore", {})
        acquired_at = datetime.now(UTC)
        players: list[HistoricalPlayerGameEvidenceDTO] = []
        teams: list[HistoricalTeamGameEvidenceDTO] = []
        try:
            for side in ("away", "home"):
                is_home = side == "home"
                team_node = (raw.get("teams") or {})[side]
                source_team_id = str((team_node.get("team") or {})["id"])
                pitcher_order = [str(value) for value in team_node.get("pitchers") or []]
                batting_order = [str(value) for value in team_node.get("battingOrder") or []]
                teams.append(HistoricalTeamGameEvidenceDTO(
                    source_game_id=source_game_id,
                    source_team_id=source_team_id,
                    is_home=is_home,
                    team_stats=dict(team_node.get("teamStats") or {}),
                    acquired_at=acquired_at,
                ))
                for key, node in sorted((team_node.get("players") or {}).items()):
                    person = node.get("person") or {}
                    player_id = person.get("id")
                    if player_id is None:
                        continue
                    player_key = str(player_id)
                    stats = node.get("stats") or {}
                    raw_order = str(node.get("battingOrder") or "")[:3]
                    players.append(HistoricalPlayerGameEvidenceDTO(
                        source_game_id=source_game_id,
                        source_player_id=player_key,
                        source_team_id=source_team_id,
                        full_name=person.get("fullName"),
                        is_home=is_home,
                        batting_order=(
                            _to_int(raw_order)
                            or (batting_order.index(player_key) + 1 if player_key in batting_order else None)
                        ),
                        appeared_as_pitcher=player_key in pitcher_order,
                        # Boxscore order proves actual participation, not when a
                        # probable-starter announcement became public.
                        actual_starting_pitcher=bool(pitcher_order and pitcher_order[0] == player_key),
                        position=(node.get("position") or {}).get("abbreviation"),
                        batting=dict(stats.get("batting") or {}) or None,
                        pitching=dict(stats.get("pitching") or {}) or None,
                        acquired_at=acquired_at,
                    ))
            boxscore = HistoricalBoxscoreDTO(
                source_game_id=source_game_id,
                acquired_at=acquired_at,
                players=players,
                teams=teams,
            )
        except (KeyError, TypeError, ValueError) as exc:
            error = SourceError(
                self.source,
                f"Historical boxscore schema validation failed: {type(exc).__name__}",
                retryable=False,
            )
            error.raw_payload = raw  # type: ignore[attr-defined]
            raise error from exc
        return AdapterResponse(
            envelope=SourceEnvelope(
                source=self.source,
                source_mode="LIVE",
                fetched_at=acquired_at,
                data=boxscore.model_dump(mode="json"),
                metadata={
                    "game_id": source_game_id,
                    "player_records": len(players),
                    "team_records": len(teams),
                    "research_provenance": "HISTORICAL_RECONSTRUCTION",
                    "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                    "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
                },
            ),
            raw_payload=raw,
        )

    @classmethod
    def _historical_schedule_game(
        cls,
        item: dict[str, Any],
        *,
        official_date: date,
        pitcher_hands: dict[str, str],
    ) -> ScheduleGameDTO:
        teams = item["teams"]
        venue = item.get("venue") or {"name": "Venue unavailable"}
        return ScheduleGameDTO(
            source_game_id=str(item["gamePk"]),
            scheduled_at=item["gameDate"],
            event_date=official_date,
            status=(item.get("status") or {}).get("detailedState", "Unknown"),
            away_team=cls._team(teams.get("away") or {}),
            home_team=cls._team(teams.get("home") or {}),
            away_pitcher=cls._pitcher(teams.get("away") or {}, pitcher_hands),
            home_pitcher=cls._pitcher(teams.get("home") or {}, pitcher_hands),
            venue=VenueDTO(
                source_id=str(venue.get("id", "")) or None,
                name=venue.get("name") or "Venue unavailable",
            ),
            away_score=_to_int((teams.get("away") or {}).get("score")),
            home_score=_to_int((teams.get("home") or {}).get("score")),
            final_at=None,
            doubleheader_status=item.get("doubleHeader"),
            game_number=_to_int(item.get("gameNumber")),
            scheduled_innings=_to_int(item.get("scheduledInnings")) or 9,
        )

    @staticmethod
    def _historical_final(
        item: dict[str, Any],
        official_date: date,
        acquired_at: datetime,
    ) -> HistoricalFinalDTO | None:
        status = item.get("status") or {}
        abstract = str(status.get("abstractGameState") or "").lower()
        detailed = str(status.get("detailedState") or "")
        teams = item.get("teams") or {}
        home_score = _to_int((teams.get("home") or {}).get("score"))
        away_score = _to_int((teams.get("away") or {}).get("score"))
        if abstract != "final" or home_score is None or away_score is None:
            return None
        linescore = item.get("linescore") or {}
        return HistoricalFinalDTO(
            source_game_id=str(item["gamePk"]),
            official_date=official_date,
            status=detailed or "Final",
            abstract_state=str(status.get("abstractGameState") or "Final"),
            home_score=home_score,
            away_score=away_score,
            innings_played=_to_int(linescore.get("currentInning")),
            # The schedule response does not prove a completion timestamp.
            event_completed_at=None,
            acquired_at=acquired_at,
        )

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
                        # The schedule feed does not provide a trustworthy
                        # completion timestamp in this response. Do not reuse
                        # first pitch as final-at provenance.
                        final_at=None,
                        doubleheader_status=item.get("doubleHeader"),
                        game_number=_to_int(item.get("gameNumber")),
                        scheduled_innings=_to_int(item.get("scheduledInnings")) or 9,
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

    async def get_active_roster(self, source_team_id: str, at: date) -> AdapterResponse:
        raw = await self._get(
            f"/teams/{source_team_id}/roster",
            {"rosterType": "active", "date": at.isoformat(), "hydrate": "person"},
        )
        fetched_at = datetime.now(UTC)
        rows: list[RosterAvailabilityDTO] = []
        for item in raw.get("roster", []):
            person = item.get("person") or {}
            player_id = person.get("id")
            if player_id is None:
                continue
            rows.append(RosterAvailabilityDTO(
                source_team_id=source_team_id,
                source_player_id=str(player_id),
                full_name=person.get("fullName") or "Player name unavailable",
                available_as_of=fetched_at,
                effective_at=fetched_at,
                status="ACTIVE",
                position=(item.get("position") or {}).get("abbreviation"),
                bats=(person.get("batSide") or {}).get("code"),
                throws=(person.get("pitchHand") or {}).get("code"),
                participation_probability=1.0,
            ))
        return AdapterResponse(
            envelope=SourceEnvelope(
                source=self.source,
                fetched_at=fetched_at,
                data=[row.model_dump(mode="json") for row in rows],
                metadata={"team_id": source_team_id, "date": at.isoformat(), "record_count": len(rows)},
            ),
            raw_payload=raw,
        )

    async def get_bullpen_workload(
        self,
        source_team_id: str,
        target_date: date,
    ) -> AdapterResponse:
        """Return recent, explicitly evidenced relief workload for a team.

        The provider date range ends on the day before ``target_date``. This
        deliberately excludes target-day results, which could otherwise leak a
        completed first game into an earlier doubleheader snapshot. Same-day
        workload can be added later only with a reliable per-game completion
        timestamp and a target-game cutoff.

        Players are included only when StatsAPI explicitly reports at least one
        relief appearance (games pitched minus games started). A missing pitch
        count remains missing and suppresses the workload score.
        """

        evidence_end = target_date - timedelta(days=1)
        evidence_start = target_date - timedelta(days=3)
        raw = await self._get(
            "/stats",
            {
                "stats": "gameLog",
                "group": "pitching",
                "teamId": source_team_id,
                "startDate": evidence_start.isoformat(),
                "endDate": evidence_end.isoformat(),
                "playerPool": "all",
                "limit": 1000,
                "hydrate": "person",
            },
        )
        fetched_at = datetime.now(UTC)
        response_complete = _stats_payload_complete(raw)
        rows = self._bullpen_rows(
            raw,
            source_team_id=source_team_id,
            evidence_start=evidence_start,
            evidence_end=evidence_end,
            fetched_at=fetched_at,
            response_complete=response_complete,
        )
        return AdapterResponse(
            envelope=SourceEnvelope(
                source=self.source,
                source_mode="LIVE",
                fetched_at=fetched_at,
                data=[row.model_dump(mode="json") for row in rows],
                metadata={
                    "team_id": source_team_id,
                    "record_count": len(rows),
                    "evidence_start_date": evidence_start.isoformat(),
                    "evidence_end_date": evidence_end.isoformat(),
                    "score_method": "weighted_pitch_count_penalty_v1",
                    "target_day_excluded": True,
                    "response_complete": response_complete,
                },
            ),
            raw_payload=raw,
        )

    async def get_game_lineups(self, source_game_id: str) -> AdapterResponse:
        raw = await self._get(f"/game/{source_game_id}/boxscore", {})
        fetched_at = datetime.now(UTC)
        lineups: list[LineupDTO] = []
        for side in ("away", "home"):
            team_node = (raw.get("teams") or {}).get(side) or {}
            team = team_node.get("team") or {}
            source_team_id = team.get("id")
            players = team_node.get("players") or {}
            batting_order = team_node.get("battingOrder") or []
            if source_team_id is None or not batting_order:
                continue
            entries: list[LineupEntryDTO] = []
            for index, raw_player_id in enumerate(batting_order, start=1):
                player_id = str(raw_player_id)
                node = players.get(f"ID{player_id}") or {}
                person = node.get("person") or {}
                position = node.get("position") or {}
                order = _to_int((node.get("battingOrder") or "")[:3]) or index
                entries.append(LineupEntryDTO(
                    source_player_id=player_id,
                    full_name=person.get("fullName") or f"MLB player {player_id}",
                    batting_order=max(1, min(9, order)),
                    defensive_position=position.get("abbreviation"),
                    bats=(person.get("batSide") or {}).get("code"),
                    expected_plate_appearance_weight=_lineup_weight(max(1, min(9, order))),
                ))
            lineups.append(LineupDTO(
                source_game_id=source_game_id,
                source_team_id=str(source_team_id),
                available_as_of=fetched_at,
                status="CONFIRMED" if len(entries) >= 9 else "EXPECTED",
                entries=entries,
            ))
        return AdapterResponse(
            envelope=SourceEnvelope(
                source=self.source,
                fetched_at=fetched_at,
                data=[lineup.model_dump(mode="json") for lineup in lineups],
                metadata={"game_id": source_game_id, "lineup_count": len(lineups)},
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

    @staticmethod
    def _bullpen_rows(
        raw: dict[str, Any],
        *,
        source_team_id: str,
        evidence_start: date,
        evidence_end: date,
        fetched_at: datetime,
        response_complete: bool,
    ) -> list[BullpenAvailabilityDTO]:
        relievers: dict[str, dict[str, Any]] = {}
        for block in raw.get("stats", []):
            group = block.get("group") or {}
            group_name = str(group.get("displayName") or group.get("name") or "").lower()
            if group_name and group_name != "pitching":
                continue
            for split in block.get("splits") or []:
                stat = split.get("stat") or {}
                games_started = _to_int(stat.get("gamesStarted"))
                games_pitched = _to_int(stat.get("gamesPitched"))
                if games_pitched is None:
                    games_pitched = _to_int(stat.get("gamesPlayed"))
                if games_started is None or games_pitched is None:
                    # Do not label a pitcher a reliever without provider evidence.
                    continue
                relief_appearances = max(0, games_pitched - games_started)
                if relief_appearances == 0:
                    continue
                player = split.get("player") or split.get("person") or {}
                player_id = player.get("id")
                if player_id is None:
                    continue
                key = str(player_id)
                entry = relievers.setdefault(
                    key,
                    {
                        "full_name": player.get("fullName") or f"MLB player {key}",
                        "throws": (player.get("pitchHand") or {}).get("code"),
                        "evidence": [],
                    },
                )
                entry["evidence"].append(
                    {
                        "date": _split_date(split),
                        "relief_appearances": relief_appearances,
                        "pitches": _first_int(stat, "numberOfPitches", "pitchesThrown"),
                        "innings": _innings_to_float(stat.get("inningsPitched")),
                    }
                )

        rows: list[BullpenAvailabilityDTO] = []
        expected_dates = [evidence_end - timedelta(days=offset) for offset in range(3)]
        for player_id, player in sorted(relievers.items()):
            daily = {
                day: {"relief_appearances": 0, "pitches": 0, "innings": 0.0}
                for day in expected_dates
            }
            missing_fields: set[str] = (
                set() if response_complete else {"response_truncated"}
            )
            for evidence in player["evidence"]:
                evidence_date = evidence["date"]
                if evidence_date not in daily:
                    missing_fields.add("appearance_date")
                    continue
                bucket = daily[evidence_date]
                bucket["relief_appearances"] += evidence["relief_appearances"]
                if evidence["pitches"] is None:
                    bucket["pitches"] = None
                    missing_fields.add("numberOfPitches")
                elif bucket["pitches"] is not None:
                    bucket["pitches"] += evidence["pitches"]
                if evidence["innings"] is None:
                    bucket["innings"] = None
                    missing_fields.add("inningsPitched")
                elif bucket["innings"] is not None:
                    bucket["innings"] += evidence["innings"]

            dated_evidence_complete = not {
                "appearance_date", "response_truncated"
            }.intersection(missing_fields)
            pitch_evidence_complete = dated_evidence_complete and "numberOfPitches" not in missing_fields
            innings_evidence_complete = dated_evidence_complete and "inningsPitched" not in missing_fields
            pitches_by_recency = [daily[day]["pitches"] for day in expected_dates]
            score = (
                _workload_availability_score(*pitches_by_recency)
                if pitch_evidence_complete
                else None
            )
            appearances = (
                sum(daily[day]["relief_appearances"] for day in expected_dates)
                if dated_evidence_complete
                else None
            )
            innings = (
                round(sum(daily[day]["innings"] for day in expected_dates), 6)
                if innings_evidence_complete
                else None
            )
            used_consecutive = (
                daily[expected_dates[0]]["relief_appearances"] > 0
                and daily[expected_dates[1]]["relief_appearances"] > 0
                if dated_evidence_complete
                else None
            )
            cumulative_pitches = None
            if pitch_evidence_complete:
                one_day = pitches_by_recency[0]
                two_days = one_day + pitches_by_recency[1]
                three_days = two_days + pitches_by_recency[2]
                cumulative_pitches = {
                    "one_day": one_day,
                    "two_days": two_days,
                    "three_days": three_days,
                }
            workload = {
                "schema_version": "mlb_statsapi_bullpen_workload_v1",
                "score_status": "READY" if score is not None else "NOT_READY",
                "score_method": "weighted_pitch_count_penalty_v1",
                "score_formula": (
                    "max(0, 1 - min(1, 0.55*P1/35 + 0.30*P2/60 + 0.15*P3/90))"
                ),
                "score_interpretation": (
                    "workload-only estimate; not official club availability or a calibrated "
                    "participation probability"
                ),
                "evidence_window": {
                    "start_date": evidence_start.isoformat(),
                    "end_date": evidence_end.isoformat(),
                    "target_day_excluded": True,
                },
                "daily": [
                    {"date": day.isoformat(), **daily[day]}
                    for day in reversed(expected_dates)
                ],
                "cumulative_pitches": cumulative_pitches,
                "pitches_last_2d": (
                    cumulative_pitches["two_days"] if cumulative_pitches else None
                ),
                "pitches_last_3d": (
                    cumulative_pitches["three_days"] if cumulative_pitches else None
                ),
                # Usage hierarchy is not reported by this endpoint. Keep it
                # unknown instead of assigning an invented equal/closer weight.
                "expected_usage_weight": None,
                "availability_score": score,
                "missing_fields": sorted(missing_fields),
                "zero_usage_basis": (
                    "absence from complete provider game-log splits"
                    if response_complete else None
                ),
            }
            rows.append(BullpenAvailabilityDTO(
                source_team_id=source_team_id,
                source_player_id=player_id,
                full_name=player["full_name"],
                available_as_of=fetched_at,
                evidence_start_date=evidence_start,
                evidence_end_date=evidence_end,
                role="RELIEVER",
                throws=player["throws"],
                pitches_previous_day=(
                    pitches_by_recency[0] if dated_evidence_complete else None
                ),
                appearances_last_three_days=appearances,
                innings_last_three_days=innings,
                used_consecutive_days=used_consecutive,
                # StatsAPI game logs do not report an official availability flag.
                expected_available=None,
                participation_probability=score,
                workload=workload,
            ))
        return rows


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


def _first_int(values: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in values:
            parsed = _to_int(values[key])
            if parsed is not None:
                return parsed
    return None


def _split_date(split: dict[str, Any]) -> date | None:
    value = split.get("date") or (split.get("game") or {}).get("gameDate")
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _innings_to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value)
    try:
        whole, separator, outs = text.partition(".")
        if not separator:
            return float(whole)
        outs_value = int(outs)
        if outs_value not in {0, 1, 2}:
            return None
        return int(whole) + outs_value / 3
    except (TypeError, ValueError):
        return None


def _workload_availability_score(
    pitches_one_day: int,
    pitches_second_day: int,
    pitches_third_day: int,
) -> float:
    cumulative_one = pitches_one_day
    cumulative_two = cumulative_one + pitches_second_day
    cumulative_three = cumulative_two + pitches_third_day
    penalty = min(
        1.0,
        0.55 * cumulative_one / 35
        + 0.30 * cumulative_two / 60
        + 0.15 * cumulative_three / 90,
    )
    return round(max(0.0, 1.0 - penalty), 6)


def _stats_payload_complete(raw: dict[str, Any]) -> bool:
    """Fail closed when StatsAPI reports more splits than it returned."""

    for block in raw.get("stats", []):
        total = _to_int(block.get("totalSplits"))
        if total is not None and total > len(block.get("splits") or []):
            return False
    return True


def _rate(numerator: Any, denominator: Any) -> float | None:
    num, den = _to_float(numerator), _to_float(denominator)
    return num / den if num is not None and den else None


def _lineup_weight(position: int) -> float:
    # Approximate expected PA share by batting slot; normalized by the feature
    # aggregator. This is a transparent participation weight, not a talent value.
    return (1.00, 0.98, 0.96, 0.94, 0.92, 0.90, 0.88, 0.86, 0.84)[position - 1]
