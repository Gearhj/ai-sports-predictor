from __future__ import annotations

import base64
import csv
import hashlib
from datetime import UTC, date, datetime
from io import StringIO
from typing import Any

import httpx

from .base import AdapterResponse, SourceEnvelope, SourceError


STATCAST_DETAIL_ENDPOINT = "https://baseballsavant.mlb.com/statcast_search/csv"
STATCAST_DETAIL_REQUIRED_FIELDS = frozenset({
    "game_date",
    "game_pk",
    "batter",
    "pitcher",
})
STATCAST_DETAIL_SCHEMA_VERSION = "statcast-search-detail-csv-v1"


def statcast_detail_query(target_date: date) -> dict[str, str]:
    """Return the single-day detail-search query used by the raw-only trial.

    This is a community-facing Baseball Savant search/download route, not a
    documented stable API. Both date bounds are deliberately identical and
    every returned row is independently checked against ``target_date``.
    """

    requested = target_date.isoformat()
    return {
        "all": "true",
        "type": "details",
        "player_type": "pitcher",
        "game_date_gt": requested,
        "game_date_lt": requested,
    }


def parse_statcast_detail_csv(
    text: str,
    *,
    content_type: str,
    target_date: date,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Validate an exact-day Statcast detail CSV without inferring availability."""

    media_type = content_type.casefold()
    sample = text.lstrip("\ufeff\r\n\t ")[:32].casefold()
    if "html" in media_type or sample.startswith(("<!doctype html", "<html")):
        raise SourceError(
            BaseballSavantStatcastAdapter.source,
            "Statcast detail response was HTML, not CSV",
            retryable=False,
        )
    if not text.strip():
        raise SourceError(
            BaseballSavantStatcastAdapter.source,
            "Statcast detail response was empty",
            retryable=False,
        )

    try:
        parsed = list(csv.reader(StringIO(text.lstrip("\ufeff")), strict=True))
    except (csv.Error, UnicodeError) as exc:
        raise SourceError(
            BaseballSavantStatcastAdapter.source,
            "Statcast detail CSV could not be parsed",
            retryable=False,
        ) from exc
    if not parsed or not parsed[0]:
        raise SourceError(
            BaseballSavantStatcastAdapter.source,
            "Statcast detail CSV has no header",
            retryable=False,
        )

    header = [value.strip() for value in parsed[0]]
    duplicate_headers = sorted({name for name in header if header.count(name) > 1})
    missing = sorted(STATCAST_DETAIL_REQUIRED_FIELDS.difference(header))
    if any(not name for name in header) or duplicate_headers or missing:
        details = {
            "blank_header": any(not name for name in header),
            "duplicate_headers": duplicate_headers,
            "missing_required_fields": missing,
        }
        raise SourceError(
            BaseballSavantStatcastAdapter.source,
            f"Statcast detail CSV schema validation failed: {details}",
            retryable=False,
        )

    nonempty_rows = [values for values in parsed[1:] if any(value.strip() for value in values)]
    if not nonempty_rows:
        raise SourceError(
            BaseballSavantStatcastAdapter.source,
            "Statcast detail CSV contained no data rows",
            retryable=False,
        )
    inconsistent_rows = [
        index
        for index, values in enumerate(nonempty_rows, start=2)
        if len(values) != len(header)
    ]
    if inconsistent_rows:
        raise SourceError(
            BaseballSavantStatcastAdapter.source,
            "Statcast detail CSV has rows inconsistent with its header",
            retryable=False,
        )

    rows = [dict(zip(header, values, strict=True)) for values in nonempty_rows]
    for index, row in enumerate(rows, start=2):
        missing_values = sorted(
            field for field in STATCAST_DETAIL_REQUIRED_FIELDS if not row[field].strip()
        )
        if missing_values:
            raise SourceError(
                BaseballSavantStatcastAdapter.source,
                f"Statcast detail CSV row {index} lacks required values: {missing_values}",
                retryable=False,
            )
        try:
            row_date = date.fromisoformat(row["game_date"].strip())
            int(row["game_pk"].strip())
            int(row["batter"].strip())
            int(row["pitcher"].strip())
        except ValueError as exc:
            raise SourceError(
                BaseballSavantStatcastAdapter.source,
                f"Statcast detail CSV row {index} has invalid identity/date fields",
                retryable=False,
            ) from exc
        if row_date != target_date:
            raise SourceError(
                BaseballSavantStatcastAdapter.source,
                "Statcast detail CSV included a row outside the exact requested date",
                retryable=False,
            )

    schema_fingerprint = hashlib.sha256(
        "\x1f".join(header).encode("utf-8")
    ).hexdigest()
    return rows, {
        "contract_version": STATCAST_DETAIL_SCHEMA_VERSION,
        "required_fields": sorted(STATCAST_DETAIL_REQUIRED_FIELDS),
        "observed_fields": header,
        "observed_field_count": len(header),
        "schema_fingerprint": schema_fingerprint,
        "schema_drift_detected": False,
        "schema_drift_basis": "REQUIRED_FIELDS_AND_RECTANGULAR_ROWS_ONLY",
        "missing_required_fields": [],
        "duplicate_headers": [],
        "inconsistent_row_count": 0,
        "additional_fields_are_permitted": True,
    }


class BaseballSavantStatcastAdapter:
    """Isolated Baseball Savant CSV acquisition boundary.

    Baseball Savant does not promise these community-facing CSV schemas as a
    stable public API. Keeping parsing here makes drift visible and replaceable.
    """

    source = "BASEBALL_SAVANT_STATCAST"
    endpoint = "https://baseballsavant.mlb.com/leaderboard/statcast"
    detail_endpoint = STATCAST_DETAIL_ENDPOINT

    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 30.0) -> None:
        self._client = client
        self.timeout = timeout

    async def get_pitch_detail_for_date(self, target_date: date) -> AdapterResponse:
        """Fetch one exact date of pitch-level CSV for validation and raw archive only."""

        if target_date.year < 2015:
            raise SourceError(
                self.source,
                "Statcast detail coverage begins in 2015",
                retryable=False,
            )
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            # Redirect following is disabled so this method represents exactly
            # one physical provider request at the bounded trial boundary.
            response = await client.get(
                self.detail_endpoint,
                params=statcast_detail_query(target_date),
                follow_redirects=False,
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            try:
                csv_text = response.content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise SourceError(
                    self.source,
                    "Statcast detail CSV is not valid UTF-8",
                    retryable=False,
                ) from exc
            rows, schema = parse_statcast_detail_csv(
                csv_text,
                content_type=content_type,
                target_date=target_date,
            )
            now = datetime.now(UTC)
            raw_payload = {
                "format": "CSV",
                "content_type": content_type,
                "requested_date": target_date.isoformat(),
                "query": statcast_detail_query(target_date),
                "body": csv_text,
                "body_base64": base64.b64encode(response.content).decode("ascii"),
                "body_encoding": "BASE64_EXACT_RESPONSE_BYTES",
            }
            return AdapterResponse(
                envelope=SourceEnvelope(
                    source=self.source,
                    source_mode="LIVE",
                    fetched_at=now,
                    source_event_time=None,
                    data=rows,
                    metadata={
                        "target_date": target_date.isoformat(),
                        "row_count": len(rows),
                        "schema": schema,
                        "research_provenance": "HISTORICAL_RECONSTRUCTION",
                        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                        "evidence_class": "PARTIALLY_RECONSTRUCTABLE",
                        "publication_timing_proven": False,
                        "eligibility_timing_proven": False,
                        "use_scope": "RAW_ARCHIVE_ONLY",
                        "canonicalization_eligible": False,
                        "training_eligible": False,
                        "stability_notice": (
                            "Community-facing Statcast Search CSV; no stable API claim"
                        ),
                    },
                ),
                raw_payload=raw_payload,
            )
        finally:
            if owns_client:
                await client.aclose()

    async def get_batting_leaderboard(
        self,
        season: int,
        minimum_plate_appearances: int = 50,
    ) -> AdapterResponse:
        if season < 2015:
            raise SourceError(self.source, "Statcast coverage begins in 2015", retryable=False)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        try:
            response = await client.get(
                self.endpoint,
                params={
                    "type": "batter",
                    "year": season,
                    "position": "",
                    "team": "",
                    "min": minimum_plate_appearances,
                    "csv": "true",
                },
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
                        "stability_notice": (
                            "Community-facing CSV schema; adapter validation required"
                        ),
                    },
                ),
                raw_payload=rows,
            )
        except SourceError:
            raise
        except (httpx.HTTPError, UnicodeError, csv.Error) as exc:
            raise SourceError(
                self.source,
                f"Statcast request failed: {type(exc).__name__}",
            ) from exc
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
                raise SourceError(
                    self.source,
                    "Pitcher Statcast returned no parsed rows",
                    retryable=False,
                )
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
                        "stability_notice": (
                            "Community-facing CSV schema; adapter validation required"
                        ),
                    },
                ),
                raw_payload=rows,
            )
        except SourceError:
            raise
        except (httpx.HTTPError, UnicodeError, csv.Error) as exc:
            raise SourceError(
                self.source,
                f"Pitcher Statcast request failed: {type(exc).__name__}",
            ) from exc
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
