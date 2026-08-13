from __future__ import annotations

import csv
from datetime import UTC, datetime
from io import StringIO
from typing import Any

import httpx

from .base import AdapterResponse, SourceEnvelope, SourceError


class BaseballSavantStatcastAdapter:
    """Isolated Baseball Savant CSV acquisition boundary.

    Baseball Savant does not promise this leaderboard schema as a stable public
    API. Keeping the parsing here makes drift visible and replaceable. Callers
    must archive the raw normalized rows and surface failures as missing data.
    """

    source = "BASEBALL_SAVANT_STATCAST"
    endpoint = "https://baseballsavant.mlb.com/leaderboard/statcast"

    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 30.0) -> None:
        self._client = client
        self.timeout = timeout

    async def get_batting_leaderboard(self, season: int, minimum_plate_appearances: int = 50) -> AdapterResponse:
        if season < 2015:
            raise SourceError(self.source, "Statcast coverage begins in 2015", retryable=False)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        try:
            response = await client.get(
                self.endpoint,
                params={"type": "batter", "year": season, "position": "", "team": "", "min": minimum_plate_appearances, "csv": "true"},
            )
            response.raise_for_status()
            rows = [dict(row) for row in csv.DictReader(StringIO(response.text))]
            if not rows:
                raise SourceError(self.source, "Statcast returned no parsed rows", retryable=False)
            normalized = [self._normalize(row, player_type="HITTER") for row in rows]
            now = datetime.now(UTC)
            return AdapterResponse(
                envelope=SourceEnvelope(
                    source=self.source,
                    fetched_at=now,
                    data=normalized,
                    metadata={
                        "row_count": len(normalized),
                        "season": season,
                        "minimum_plate_appearances": minimum_plate_appearances,
                        "stability_notice": "Community-facing CSV schema; adapter validation required",
                    },
                ),
                raw_payload=rows,
            )
        except SourceError:
            raise
        except (httpx.HTTPError, UnicodeError, csv.Error) as exc:
            raise SourceError(self.source, f"Statcast request failed: {type(exc).__name__}") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def get_pitching_leaderboard(
        self,
        season: int,
        minimum_batters_faced: int = 50,
    ) -> AdapterResponse:
        if season < 2015:
            raise SourceError(self.source, "Statcast coverage begins in 2015", retryable=False)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        try:
            response = await client.get(
                self.endpoint,
                params={
                    "type": "pitcher",
                    "year": season,
                    "position": "",
                    "team": "",
                    "min": minimum_batters_faced,
                    "csv": "true",
                },
            )
            response.raise_for_status()
            rows = [dict(row) for row in csv.DictReader(StringIO(response.text))]
            if not rows:
                raise SourceError(self.source, "Pitcher Statcast returned no parsed rows", retryable=False)
            normalized = [self._normalize(row, player_type="PITCHER") for row in rows]
            now = datetime.now(UTC)
            return AdapterResponse(
                envelope=SourceEnvelope(
                    source=self.source,
                    fetched_at=now,
                    data=normalized,
                    metadata={
                        "row_count": len(normalized),
                        "season": season,
                        "minimum_batters_faced": minimum_batters_faced,
                        "player_type": "PITCHER",
                        "stability_notice": "Community-facing CSV schema; adapter validation required",
                    },
                ),
                raw_payload=rows,
            )
        except SourceError:
            raise
        except (httpx.HTTPError, UnicodeError, csv.Error) as exc:
            raise SourceError(self.source, f"Pitcher Statcast request failed: {type(exc).__name__}") from exc
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def _normalize(row: dict[str, str], *, player_type: str = "HITTER") -> dict[str, Any]:
        def first(*names: str) -> str | None:
            return next((row.get(name) for name in names if row.get(name) not in (None, "")), None)

        def number(*names: str) -> float | None:
            value = first(*names)
            try:
                return float(value) if value is not None else None
            except ValueError:
                return None

        def rate(*names: str) -> float | None:
            value = number(*names)
            if value is None:
                return None
            # Savant leaderboard percentage columns are commonly emitted on a
            # 0-100 scale. Canonical rates are always fractions.
            return value / 100.0 if abs(value) > 1 else value

        return {
            "source_player_id": first("player_id", "playerid"),
            "player_name": first("last_name, first_name", "player_name"),
            "source_team": first("team", "team_name", "team_abbrev"),
            "player_type": player_type,
            "attempts": number("attempts", "pa"),
            "xwoba": number("est_woba", "xwoba"),
            "xba": number("est_ba", "xba"),
            "xslg": number("est_slg", "xslg"),
            "xera": number("xera", "est_era", "expected_era"),
            "barrel_rate": rate("brl_percent", "barrel_batted_rate"),
            "hard_hit_rate": rate("hard_hit_percent"),
            "average_exit_velocity": number("exit_velocity_avg", "avg_exit_velocity"),
            "launch_angle": number("launch_angle_avg", "avg_launch_angle"),
            "velocity": number("avg_fastball_velocity", "ff_avg_speed", "velocity"),
            "whiff_rate": rate("whiff_percent", "whiff_rate"),
            "strikeout_rate": rate("k_percent", "strikeout_rate"),
            "walk_rate": rate("bb_percent", "walk_rate"),
        }
