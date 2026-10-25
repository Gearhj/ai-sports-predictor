"""AISP's own NHL Gen1 forecasts: the frozen, accepted model run in-process on the public schedule.

WHY THIS EXISTS (coordinator decision CLAUDE_COORDINATOR_NHL_SIMPLIFICATION_2026-10-16)
--------------------------------------------------------------------------------------
NHL forecasts used to arrive only as producer publications (a separate install,
exchange folder, activation grants and an eligibility publisher). This module
replaces that chain for the SAME frozen model: AISP fetches the same public
schedule asset the producer used, builds the same features, runs the same fitted
state (``app.nhl.gen1_frozen``, byte-identical ``selected_model.json``) and stores
the result as an ``NHL_GEN1_FORECAST_V1`` document in ``nhl_research_forecasts``.
Producer-published forecasts keep their own route; nothing here touches it.

WHAT ONE RUN DOES (at most five HTTP requests, eight MiB in all, https only)
---------------------------------------------------------------------------
1. Rights, first, fail CLOSED (status ``RIGHTS_CHANGED``, no forecast):
   the repository LICENSE must be byte-identical to the one the producer's
   2026-10-13 rights review approved (sha256 ``9c3898cc...``), and the README must
   still declare CC BY 4.0. The README's own SHA-256 is recorded, not pinned (the
   file is a long, frequently edited dataset index). The sportsdataverse.org About
   page is NOT read: its live warehouse row counter changes daily (51.3M -> 66.9M
   -> 405M between Oct 10 and Oct 13), so a byte comparison would refuse every
   day while proving nothing about the licence; the producer's own reviews
   classified those diffs as "VISIBLE_DIFF_ONLY_WAREHOUSE_ROW_COUNT".
2. The GitHub release API names the asset, its size, its ``sha256:`` digest and
   its update clock. The asset (one redirect hop to GitHub's asset host) must
   match that digest and size exactly, or the run is ``DIGEST_MISMATCH``.
3. Every response body is retained byte-exact through the existing
   :class:`~app.ingestion.archive.RawArchive` (``<raw_data_root>/nhl_sportsdataverse/...``).
   If the schedule bytes cannot be retained the run is ``EVIDENCE_NOT_RETAINED``.
4. Features exactly as ``produce_current.prepare`` builds them, for TODAY and
   TOMORROW (America/New_York game dates): only the columns it reads (no score
   column is ever parsed), rest days from scheduled games strictly before the
   game's date whose start is before the schedule receipt clock, capped at 7,
   missing -> 7 with a flag. Games already started or not FUT/PRE, non-regular or
   non-2026-27 rows, and teams missing from the crosswalk or the frozen state are
   skipped with a reason. A TOMORROW game whose team still has an earlier-date
   game that had not started at the receipt clock (a back-to-back: tonight's
   game is not yet "prior") is skipped as ``REST_INCOMPLETE_...``: its rest would
   differ from the game-day value. The game-day run forecasts it. Every written
   row's evidence says ``restComplete: true``, and the paper lifecycle's
   in-process eligibility refuses any row that does not.
5. Persist, idempotently, per game: the latest in-process forecast for the game
   with the same scheduled start AND identical input features -> nothing written
   (``UNCHANGED``); otherwise a new forecast whose ``supersedesForecastId`` names
   the one it replaces. A start the schedule moved (still SCHEDULED, both clocks
   in the future) moves the AISP game's start with it; the old forecast keeps
   its original start, so it can never be read as made for the new one.

CLOCKS (never backdated, never retimed)
---------------------------------------
``sourceCutoffUtc`` = ``sourceRetrievedAtUtc`` = when the schedule bytes were
fully received. ``createdAtUtc`` = ``publishedAtUtc`` = the real generation clock,
taken after the model ran. ``imported_at`` = ``available_as_of`` = the real
clock at which the row is written (>= generation; a backwards clock refuses).
No outcome is ever an input: the score columns are never read.

Research only. No decision, quote, price, position or provider credit; no
paid request. Every switch defaults off (``nhl_forecast_generation_enabled``).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Game, GameStatus
from app.models.nhl_research import NhlResearchForecast
from app.nhl import contract as c
from app.nhl import gen1_frozen as frozen
from app.nhl.gen1_frozen.model import predict_goal

# ----------------------------------------------------------------- identity --
PRODUCER = "AISP_IN_PROCESS_NHL_GEN1_V1"
EVIDENCE_STATUS = "AISP_IN_PROCESS_GENERATED"
EVIDENCE_MODE = "AISP_IN_PROCESS_FROZEN_GEN1"
EVIDENCE_SCHEMA = "AISP_NHL_IN_PROCESS_GEN1_EVIDENCE_V1"
RESULT_SCHEMA = "AISP_NHL_IN_PROCESS_GENERATION_RESULT_V1"
PUBLICATION_CLOCK_SOURCE = "AISP_IN_PROCESS_GENERATION_CLOCK"
#: Where the per-game evidence document lives: the row's own ``producer_evidence_ref`` column.
#: That column is ``{path, sha256, bytes}`` (the document's ``provenance.producerEvidenceRef``,
#: the shape every NHL reader expects) PLUS the evidence's own keys; ``sha256``/``bytes`` are
#: of the canonical bytes of the column WITHOUT those three keys (see ``evidence_binding``).
EVIDENCE_LOCATION = "AISP_DB:nhl_research_forecasts.producer_evidence_ref"
EVIDENCE_BINDING_KEYS = ("path", "sha256", "bytes")
EVIDENCE_MODES = c.EVIDENCE_MODES + (EVIDENCE_MODE,)
MODEL_ID = frozen.MODEL_ID
MODEL_VERSION = frozen.MODEL_VERSION
CONFIGURATION_SHA256 = frozen.SELECTED_MODEL_SHA256
ET = ZoneInfo("America/New_York")

# ------------------------------------------------------------------- source --
ASSET_NAME = "nhl_schedule_2027.csv"
ASSET_URL = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/download/nhl_schedules/"
             + ASSET_NAME)
RELEASE_API_URL = "https://api.github.com/repos/sportsdataverse/sportsdataverse-data/releases/tags/nhl_schedules"
LICENSE_URL = "https://raw.githubusercontent.com/sportsdataverse/sportsdataverse-data/main/LICENSE"
README_URL = "https://raw.githubusercontent.com/sportsdataverse/sportsdataverse-data/main/README.md"
#: LICENSE bytes the producer's 2026-10-13 rights review approved
#: (nhl-current-slate-producer-20261013-v1/SOURCE_METADATA_CAPTURE_HOST.json, MIT, 1072 bytes).
APPROVED_LICENSE_SHA256 = "9c3898cce6957255c63911737a6441daf7ed70c445381b8a7f32b75a28123ea6"
#: ``release-assets.githubusercontent.com`` is where github.com actually redirects
#: release downloads today (``finalHost`` of the producer's Oct 12 and Oct 13 captures).
ALLOWED_HOSTS = frozenset({"github.com", "objects.githubusercontent.com", "api.github.com",
                           "raw.githubusercontent.com", "release-assets.githubusercontent.com"})
MAX_BYTES = 8 * 1024 * 1024
MAX_REQUESTS = 5          # 2 rights reads + release API + asset + its one redirect hop
MAX_REDIRECTS = 2
TIMEOUT_SECONDS = 25.0
USER_AGENT = "AISP-private-NHL-research/1.0"
ARCHIVE_SOURCE = "NHL_SPORTSDATAVERSE"
SCHEDULE_SOURCE = "SPORTSDATAVERSE_NHL_SCHEDULE_PUBLISHER_OFFERED_DAILY_SNAPSHOT"
ATTRIBUTION = ("SportsDataverse, sportsdataverse-data nhl_schedules, "
               "https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/nhl_schedules; "
               "publisher-declared CC BY 4.0 https://creativecommons.org/licenses/by/4.0/")

# ----------------------------------------------------------------- document --
#: The producer's exact lists (produce_current.py), unchanged: every one is still true here.
MISSING_INPUTS = ["HOME_GOALIE_UNKNOWN", "AWAY_GOALIE_UNKNOWN", "LINEUPS_UNAVAILABLE", "INJURIES_UNAVAILABLE",
                  "MARKET_PRICES_NOT_USED"]
LIMITATIONS = ["UNFUNDED", "PRIVATE_RESEARCH_ONLY", "TEAM_LEVEL_GOALIE_FALLBACK",
               "RECONSTRUCTED_HISTORICAL_SOURCE_AVAILABILITY_NOT_PROVED", "NO_CURRENT_2026_27_OUTCOME_UPDATE",
               "REST_IS_SCHEDULE_METADATA_NOT_ACTUAL_PARTICIPATION_PROOF",
               "INDEPENDENT_POISSON_REGULATION_AND_POOLED_TIE_SPLIT_NOT_SHOOTOUT_MODEL",
               "DAILY_SCHEDULE_SNAPSHOT_NOT_LIVE_POSTPONEMENT_PROOF",
               "HOME_VENUE_SCHEDULE_ASSUMES_NON_NEUTRAL_NO_EXPLICIT_NEUTRAL_FLAG"]
AUTHORITY = {k: False for k in ("wageringAuthority", "decisionAuthority", "positionAuthority",
                                "modelPromotionAuthority")}

# ----------------------------------------------------------------- schedule --
#: The only columns read -- the producer's ``usecols``. ``home_score``/``away_score`` are never parsed.
SCHEDULE_COLUMNS = ("game_id", "season_full", "game_type", "game_date", "game_time", "home_team_abbr",
                    "away_team_abbr", "home_team_name", "away_team_name", "game_state", "venue")
#: pandas ``read_csv`` default NA strings: the producer read these as missing, so does this port.
PANDAS_NA = frozenset({"", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan", "1.#IND",
                       "1.#QNAN", "<NA>", "N/A", "NA", "NULL", "NaN", "None", "n/a", "nan", "null"})
SEASON_FULL = "20262027"
SEASON_LABEL = "2026-27"
SEASON_START_YEAR = 2026
REGULAR = "R"
GAME_ID = re.compile(r"^202602\d{4}$")
PREGAME_STATES = ("FUT", "PRE")
REST_CAP_DAYS = 7
#: Exclusion reason (and the rule the evidence's ``restComplete`` asserts): a team in the game has an
#: earlier-date regular-season game whose start was not before the schedule receipt clock.
REST_INCOMPLETE = "REST_INCOMPLETE_EARLIER_GAME_NOT_STARTED_AT_RECEIPT"
REST_COMPLETE_RULE = ("Neither team has an earlier-date 2026-27 regular-season game whose game_time is at or after "
                      "the schedule receipt clock, so the rest features equal a game-day computation on the same "
                      "schedule bytes (AISP_NHL_IN_PROCESS_REST_COMPLETE_V1)")


class ScheduleInvalid(ValueError):
    pass


class SourceRefused(RuntimeError):
    def __init__(self, status: str, reason: str) -> None:
        super().__init__(f"{status}:{reason}")
        self.status, self.reason = status, reason


def _now() -> datetime:
    return datetime.now(UTC)


def _client() -> httpx.AsyncClient:
    """The HTTP client a scheduled run uses: redirects are followed by hand, hop by hop, never by httpx."""

    return httpx.AsyncClient(follow_redirects=False)


def utc_text(moment: datetime) -> str:
    """The producer's clock text: ``datetime.isoformat()`` with ``+00:00`` written ``Z``."""

    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_clock(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ===================================================================== parse ==
def parse_schedule(raw: bytes) -> list[dict[str, str | None]]:
    """The schedule's rows, reading ONLY ``SCHEDULE_COLUMNS`` as strings; NA -> None.

    Raises ``ScheduleInvalid`` for a missing column or a duplicated game id (the
    producer asserted ``game_id.is_unique``).
    """

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ScheduleInvalid("NOT_UTF8") from exc
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if not header:
        raise ScheduleInvalid("EMPTY")
    index = {}
    for column in SCHEDULE_COLUMNS:
        if column not in header:
            raise ScheduleInvalid(f"MISSING_COLUMN:{column}")
        index[column] = header.index(column)
    rows: list[dict[str, str | None]] = []
    for record in reader:
        if not record or all(not cell for cell in record):
            continue
        row: dict[str, str | None] = {}
        for column, i in index.items():
            value = record[i] if i < len(record) else ""
            row[column] = None if value in PANDAS_NA else value
        rows.append(row)
    ids = [r["game_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ScheduleInvalid("DUPLICATE_GAME_ID")
    return rows


# ================================================================== features ==
@dataclass
class Slate:
    target_date: str
    rows: list[dict[str, Any]] = field(default_factory=list)       # model inputs, producer shape
    games: list[dict[str, Any]] = field(default_factory=list)      # the schedule row of each input
    excluded: list[dict[str, Any]] = field(default_factory=list)


def _team_of(abbr: str | None, crosswalk: dict[str, dict[str, str]]) -> dict[str, str] | None:
    return crosswalk.get(abbr) if abbr else None


def pending_earlier_games(regular: list[dict[str, str | None]], abbr: str | None, game_date: str,
                          observed: str) -> list[str]:
    """Regular-season games of ``abbr`` dated before ``game_date`` that had NOT started at ``observed``.

    Non-empty means the producer's rest rule (prior = earlier date AND started before the receipt
    clock) would skip a game that WILL precede this one: the rest feature is incomplete.
    """

    return sorted(str(p["game_id"]) for p in regular
                  if (p["home_team_abbr"] == abbr or p["away_team_abbr"] == abbr)
                  and p["game_date"] is not None and p["game_date"] < game_date
                  and p["game_time"] is not None and p["game_time"] >= observed)


def prepare_slate(schedule: list[dict[str, str | None]], target_date: str, *, observed: str, started: datetime,
                  crosswalk: dict[str, dict[str, str]], model_team_ids: list[str]) -> Slate:
    """``produce_current.prepare``'s feature build for one ET game date, pandas-free.

    ``observed`` is the schedule receipt clock text; rest days count only games
    whose ``game_time`` sorts before it (the producer's string comparison, kept).
    ``started`` is the run clock: a game starting at or before it is skipped.

    REST COMPLETENESS (``REST_INCOMPLETE``): the producer ran only on the game
    day, when every earlier-date game had started, so its filter never dropped a
    game that precedes the target. A DAY-AHEAD target can: a team playing tonight
    and tomorrow would get rest counted from its game before tonight's. Such a
    game is skipped with ``REST_INCOMPLETE:<team>:<pending game id>``, so every
    row this returns has rest features identical to the game-day computation.
    """

    slate = Slate(target_date)
    regular = [r for r in schedule if r["game_type"] == REGULAR and r["season_full"] == SEASON_FULL]
    names: dict[tuple[str, str], set[str | None]] = {}
    for r in regular:
        for side in ("home", "away"):
            names.setdefault((side, r[f"{side}_team_abbr"] or ""), set()).add(r[f"{side}_team_name"])
    inconsistent = {abbr for (_side, abbr), seen in names.items() if len(seen) > 1}
    today = sorted((r for r in schedule if r["game_date"] == target_date),
                   key=lambda r: (r["game_time"] is None, r["game_time"] or "", r["game_id"] or ""))
    model_ids = set(model_team_ids)

    def skip(g: dict[str, Any], reason: str) -> None:
        slate.excluded.append({"sourceGameId": g["game_id"], "scheduledStartUtc": g["game_time"], "reason": reason})

    for g in today:
        start = _parse_clock(g["game_time"])
        if start is None:
            skip(g, "START_MISSING_OR_INVALID")
            continue
        if start <= started or g["game_state"] not in PREGAME_STATES:
            skip(g, "ALREADY_STARTED_OR_NOT_PREGAME")
            continue
        if g["game_type"] != REGULAR or g["season_full"] != SEASON_FULL or not GAME_ID.match(g["game_id"] or ""):
            skip(g, "NOT_A_2026_27_REGULAR_SEASON_GAME")
            continue
        if g["home_team_abbr"] == g["away_team_abbr"]:
            skip(g, "INVALID_ORIENTATION")
            continue
        try:
            game_day = date.fromisoformat(str(g["game_date"]))
        except ValueError:
            skip(g, "GAME_DATE_INVALID")
            continue
        row: dict[str, Any] = {"game_id": g["game_id"], "season": SEASON_START_YEAR, "game_date": g["game_date"],
                               "date": f"{g['game_date']}T00:00:00"}
        refusal = None
        for side in ("home", "away"):
            abbr = g[f"{side}_team_abbr"]
            team = _team_of(abbr, crosswalk)
            if team is None:
                refusal = f"TEAM_NOT_IN_CROSSWALK:{abbr}"
                break
            if abbr in inconsistent:
                refusal = f"TEAM_NAME_INCONSISTENT_IN_SCHEDULE:{abbr}"
                break
            if team["sourceTeamId"] not in model_ids:
                refusal = f"TEAM_NOT_IN_FROZEN_MODEL_STATE:{abbr}"
                break
            row[side + "_id"] = team["sourceTeamId"]
            pending = pending_earlier_games(regular, abbr, str(g["game_date"]), observed)
            if pending:
                # A DAY-AHEAD forecast made before this team's earlier-date game started would count rest
                # from the game BEFORE it: features the model never defines (training counts every game).
                # The producer only ever prepared the game day, where this cannot happen. Refuse; the
                # game-day run, after that game has started, forecasts with the true rest.
                refusal = f"{REST_INCOMPLETE}:{abbr}:{pending[0]}"
                break
            prior = [p["game_date"] for p in regular
                     if (p["home_team_abbr"] == abbr or p["away_team_abbr"] == abbr)
                     and p["game_date"] is not None and p["game_date"] < g["game_date"]
                     and p["game_time"] is not None and p["game_time"] < observed]
            if len(prior) != len(set(prior)):
                refusal = f"TEAM_SAME_DAY_DUPLICATE:{abbr}"
                break
            last = max(prior) if prior else None
            try:
                days = None if last is None else (game_day - date.fromisoformat(last)).days
            except ValueError:
                refusal = f"PRIOR_GAME_DATE_INVALID:{abbr}"
                break
            row[side + "_rest_days"] = REST_CAP_DAYS if days is None else min(days, REST_CAP_DAYS)
            row[side + "_rest_missing"] = int(last is None)
            row[side + "_last_scheduled_game_date"] = last
        if refusal:
            skip(g, refusal)
            continue
        slate.rows.append(row)
        slate.games.append(dict(g))
    return slate


def predict(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return predict_goal(frozen.goal_model(), rows) if rows else []


# ================================================================== document ==
def forecast_document(g: dict[str, Any], prediction: dict[str, Any], *, crosswalk: dict[str, dict[str, str]],
                      observed: str, created: str, published: str | None, evidence_ref: dict[str, Any],
                      evidence_mode: str, supersedes: str | None, handoff_sha256: str) -> dict[str, Any]:
    """The ``NHL_GEN1_FORECAST_V1`` document, field for field as ``produce_current.prepare`` writes it.

    Only the publication clock, ``supersedesForecastId``, the evidence mode and
    the evidence reference are parameters, so the same builder reproduces the
    producer's prepared objects exactly (the reproduction test proves it).
    """

    if g["game_id"] != prediction["game_id"]:
        raise AssertionError("PREDICTION_ORDER_MISMATCH")
    p, q = prediction, prediction["tied_reg_home_allocation"]
    doc = {
        "schema": c.FORECAST_SCHEMA, "canonicalization": c.CANONICALIZATION, "league": "NHL", "sport": "HOCKEY",
        "forecastId": "", "supersedesForecastId": supersedes,
        "model": {"modelId": MODEL_ID, "modelVersion": MODEL_VERSION, "configurationSha256": CONFIGURATION_SHA256,
                  "trainingCutoffUtc": None, "handoffSha256": handoff_sha256},
        "game": {"canonicalGameId": "nhl:game:" + g["game_id"], "sourceGameId": g["game_id"], "season": SEASON_LABEL,
                 "seasonStartYear": SEASON_START_YEAR, "gameType": "REGULAR", "scheduledStartUtc": g["game_time"],
                 "home": dict(crosswalk[g["home_team_abbr"]]), "away": dict(crosswalk[g["away_team_abbr"]]),
                 "neutralSite": False,
                 "scheduleSource": {"source": SCHEDULE_SOURCE, "observedAtUtc": observed, "retrievedAtUtc": observed}},
        "researchStatus": "RESEARCH_ONLY", "authority": dict(AUTHORITY),
        "sourceCutoffUtc": observed, "sourceRetrievedAtUtc": observed, "createdAtUtc": created,
        "publishedAtUtc": published,
        "probabilities": {
            "regulation": {"home": p["reg_home_win"], "tie": p["reg_tie"], "away": p["reg_away_win"]},
            "final": {"home": p["goal_home_win_final"], "away": 1 - p["goal_home_win_final"]},
            "overtime": {"pOvertime": p["reg_tie"], "pShootout": None,
                         "conditionalOnTieAfterRegulation": {"home": q, "away": 1 - q}, "conditionalOnShootout": None}},
        "goals": {"regulation": {"horizon": c.REGULATION_HORIZON, "expectedHome": p["goal_home_mean_regulation"],
                                 "expectedAway": p["goal_away_mean_regulation"], "distribution": None},
                  "final": None},
        "goalies": {s: {"status": "UNKNOWN", "goalieId": None, "goalieName": None, "statusSource": None,
                        "statusObservedAtUtc": None} for s in ("home", "away")},
        "missingInputs": list(MISSING_INPUTS), "limitations": list(LIMITATIONS),
        "provenance": {"evidenceMode": evidence_mode, "producerEvidenceRef": dict(evidence_ref),
                       "goalieFeatureMode": "TEAM_LEVEL"},
    }
    doc["forecastId"] = c.forecast_identity(doc)
    return doc


# ==================================================================== source ==
@dataclass
class SourceCapture:
    status: str
    reason: str | None = None
    requests: list[dict[str, Any]] = field(default_factory=list)
    rights: dict[str, Any] = field(default_factory=dict)
    schedule: dict[str, Any] | None = None
    schedule_bytes: bytes | None = None
    observed_utc: str | None = None

    def summary(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "requestCount": len(self.requests),
                "requests": self.requests, "rights": self.rights, "schedule": self.schedule,
                "observedUtc": self.observed_utc}


class _Fetcher:
    """GET with an allow-listed https host on every hop, a request budget and a shared byte cap."""

    def __init__(self, client: httpx.AsyncClient, *, now: Callable[[], datetime], archive: Callable[..., dict]) -> None:
        self.client, self.now, self.archive = client, now, archive
        self.requests: list[dict[str, Any]] = []
        self.http_requests = 0
        self.used_bytes = 0

    async def get(self, label: str, url: str, *, accept: str = "*/*") -> tuple[dict[str, Any], bytes]:
        current, hops = url, []
        requested = self.now()
        for _ in range(MAX_REDIRECTS + 1):
            parsed = urlparse(current)
            if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
                raise SourceRefused("SOURCE_REFUSED", f"HOST_NOT_ALLOWED:{label}:{parsed.scheme}://{parsed.hostname}")
            if self.http_requests >= MAX_REQUESTS:
                raise SourceRefused("SOURCE_REFUSED", "REQUEST_BUDGET_EXHAUSTED")
            self.http_requests += 1
            try:
                async with self.client.stream("GET", current, headers={"User-Agent": USER_AGENT, "Accept": accept},
                                              timeout=TIMEOUT_SECONDS) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise SourceRefused("SOURCE_UNAVAILABLE", f"REDIRECT_WITHOUT_LOCATION:{label}")
                        hops.append({"url": current.split("?", 1)[0], "status": response.status_code})
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise SourceRefused("SOURCE_UNAVAILABLE", f"HTTP_{response.status_code}:{label}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if self.used_bytes + len(body) > MAX_BYTES:
                            raise SourceRefused("SOURCE_REFUSED", f"BYTE_CAP_EXCEEDED:{label}")
                    completed = self.now()
                    headers = {k.lower(): v for k, v in response.headers.items() if k.lower() in {
                        "date", "last-modified", "content-type", "content-length", "etag", "x-ratelimit-limit",
                        "x-ratelimit-remaining", "x-ratelimit-reset"}}
            except httpx.HTTPError as exc:
                raise SourceRefused("SOURCE_UNAVAILABLE", f"{type(exc).__name__}:{label}") from exc
            raw = bytes(body)
            self.used_bytes += len(raw)
            final = urlparse(current)
            receipt = {"label": label, "url": url, "finalHost": final.hostname, "redirects": hops, "status": 200,
                       "requestStartedAtUtc": utc_text(requested), "responseCompletedAtUtc": utc_text(completed),
                       "headers": headers, "sha256": _sha(raw), "bytes": len(raw), "authentication": "NONE",
                       "addedCostUsd": 0}
            receipt["archive"] = self.archive(label, url, raw, receipt)
            self.requests.append(receipt)
            return receipt, raw
        raise SourceRefused("SOURCE_REFUSED", f"TOO_MANY_REDIRECTS:{label}")


def readme_declares_cc_by_4(raw: bytes) -> bool:
    text = re.sub(r"\s+", " ", raw.decode("utf-8", errors="replace"))
    return "CC BY 4.0" in text and "creativecommons.org/licenses/by/4.0" in text


def _archiver(settings: Any) -> Callable[..., dict]:
    from app.ingestion.archive import RawArchive

    root = Path(settings.raw_data_root)

    def archive(label: str, url: str, raw: bytes, receipt: dict[str, Any]) -> dict[str, Any]:
        try:
            record = RawArchive(root).write_response(
                ARCHIVE_SOURCE, raw, request_fingerprint=_sha(url.encode("utf-8")),
                extra_metadata={"label": label, "url": url, "final_host": receipt["finalHost"],
                                "request_started_at": receipt["requestStartedAtUtc"],
                                "response_completed_at": receipt["responseCompletedAtUtc"],
                                "response_headers": receipt["headers"], "producer": PRODUCER,
                                "attribution": ATTRIBUTION})
        except Exception as exc:  # noqa: BLE001 - recorded, and the caller fails closed on it
            return {"status": "FAILED", "reason": f"{type(exc).__name__}:{str(exc)[:200]}"}
        return {"status": "ARCHIVED", "path": record.path, "sha256": record.sha256, "bytes": record.bytes,
                "bodyPath": record.body_path, "bodySha256": record.body_sha256, "bodyBytes": record.body_bytes}

    return archive


async def capture_source(settings: Any, *, client: httpx.AsyncClient | None = None,
                         now: Callable[[], datetime] | None = None) -> SourceCapture:
    """Rights reads, then the release API, then the asset. Fails closed with a named status."""

    now = now or _now
    owned = client is None
    client = client or _client()
    fetcher = _Fetcher(client, now=now, archive=_archiver(settings))
    capture = SourceCapture(status="CAPTURING")
    try:
        lic, lic_raw = await fetcher.get("sdv-license", LICENSE_URL)
        capture.rights = {"licenseUrl": LICENSE_URL, "licenseSha256": lic["sha256"], "licenseBytes": lic["bytes"],
                          "approvedLicenseSha256": APPROVED_LICENSE_SHA256,
                          "licenseUnchanged": lic["sha256"] == APPROVED_LICENSE_SHA256}
        if not capture.rights["licenseUnchanged"]:
            raise SourceRefused("RIGHTS_CHANGED", "LICENSE_BYTES_DIFFER_FROM_APPROVED")
        readme, readme_raw = await fetcher.get("sdv-readme", README_URL)
        declares = readme_declares_cc_by_4(readme_raw)
        capture.rights.update({"readmeUrl": README_URL, "readmeSha256": readme["sha256"],
                               "readmeBytes": readme["bytes"], "readmeDeclaresCcBy40": declares,
                               "aboutPage": "NOT_READ: its live warehouse row counter changes daily, so bytes "
                                            "cannot be pinned; licence evidence is LICENSE + README"})
        if not declares:
            raise SourceRefused("RIGHTS_CHANGED", "README_NO_LONGER_DECLARES_CC_BY_4_0")
        capture.rights["status"] = "RIGHTS_UNCHANGED"
        if any(r["archive"].get("status") != "ARCHIVED" for r in fetcher.requests):
            raise SourceRefused("EVIDENCE_NOT_RETAINED", "RIGHTS_RESPONSE_NOT_ARCHIVED")

        release_receipt, release_raw = await fetcher.get("sdv-release", RELEASE_API_URL,
                                                         accept="application/vnd.github+json")
        try:
            release = json.loads(release_raw)
            asset = next(a for a in release["assets"] if a.get("name") == ASSET_NAME)
        except (ValueError, KeyError, TypeError, StopIteration) as exc:
            raise SourceRefused("SCHEDULE_INVALID", f"RELEASE_ASSET_NOT_LISTED:{type(exc).__name__}") from exc
        if asset.get("browser_download_url") != ASSET_URL:
            raise SourceRefused("SOURCE_REFUSED", "ASSET_URL_CHANGED")
        digest = str(asset.get("digest") or "")
        if not re.match(r"^sha256:[0-9a-f]{64}$", digest):
            raise SourceRefused("DIGEST_MISMATCH", "RELEASE_API_PUBLISHES_NO_SHA256_DIGEST")
        size = asset.get("size")
        if not isinstance(size, int) or size <= 0 or size > MAX_BYTES - fetcher.used_bytes:
            raise SourceRefused("SOURCE_REFUSED", f"ASSET_SIZE_OUT_OF_BOUNDS:{size}")
        updated = _parse_clock(str(asset.get("updated_at") or ""))
        max_age = float(getattr(settings, "nhl_forecast_generation_max_asset_age_hours", 48.0))
        age_hours = None if updated is None else (now() - updated).total_seconds() / 3600.0
        capture.schedule = {"url": ASSET_URL, "releaseApiUrl": RELEASE_API_URL, "assetId": asset.get("id"),
                            "assetUpdatedAtUtc": asset.get("updated_at"), "assetSize": size,
                            "releaseDigest": digest, "assetAgeHoursAtCapture": age_hours,
                            "maxAssetAgeHours": max_age, "releaseResponseSha256": release_receipt["sha256"]}
        if age_hours is None or age_hours < -0.25 or age_hours > max_age:
            raise SourceRefused("ASSET_STALE", f"ASSET_UPDATED_AT:{asset.get('updated_at')}")

        asset_receipt, body = await fetcher.get(ASSET_NAME, ASSET_URL)
        expected = digest.split(":", 1)[1]
        capture.schedule.update({"sha256": asset_receipt["sha256"], "bytes": asset_receipt["bytes"],
                                 "finalHost": asset_receipt["finalHost"],
                                 "requestStartedAtUtc": asset_receipt["requestStartedAtUtc"],
                                 "responseCompletedAtUtc": asset_receipt["responseCompletedAtUtc"],
                                 "rawArchive": asset_receipt["archive"],
                                 "publisherDigestVerified": asset_receipt["sha256"] == expected and len(body) == size})
        if not capture.schedule["publisherDigestVerified"]:
            raise SourceRefused("DIGEST_MISMATCH", "ASSET_BYTES_DO_NOT_MATCH_RELEASE_DIGEST")
        if asset_receipt["archive"].get("status") != "ARCHIVED":
            raise SourceRefused("EVIDENCE_NOT_RETAINED", "SCHEDULE_BYTES_NOT_ARCHIVED")
        capture.status = "CAPTURED"
        capture.schedule_bytes = body
        capture.observed_utc = asset_receipt["responseCompletedAtUtc"]
    except SourceRefused as refused:
        capture.status, capture.reason = refused.status, refused.reason
    finally:
        capture.requests = fetcher.requests
        if owned:
            await client.aclose()
    return capture


# ================================================================ provenance ==
def _module_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def code_identity() -> dict[str, str]:
    here = Path(__file__).resolve()
    return {**frozen.artifact_hashes(),
            "generatorModuleSha256": _module_sha(here),
            "modelPortSha256": _module_sha(frozen.ROOT / "model.py"),
            "frozenPackageSha256": _module_sha(frozen.ROOT / "__init__.py")}


def git_revision() -> str | None:
    """The deployed checkout's HEAD, if git can say; the content hashes above are the binding identity."""

    try:
        out = subprocess.run(["git", "-C", str(Path(__file__).resolve().parents[3]), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    value = (out.stdout or "").strip()
    return value if out.returncode == 0 and re.match(r"^[0-9a-f]{40}$", value) else None


# =================================================================== persist ==
def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def evidence_binding(evidence: dict[str, Any]) -> dict[str, Any]:
    """``{path, sha256, bytes}`` of an evidence document (binding keys, if present, excluded)."""

    body = {k: v for k, v in evidence.items() if k not in EVIDENCE_BINDING_KEYS}
    raw = c.canonical_bytes(body)
    return {"path": EVIDENCE_LOCATION, "sha256": _sha(raw), "bytes": len(raw)}


def latest_in_process(session: Session, canonical_game_ref: str) -> NhlResearchForecast | None:
    rows = session.scalars(select(NhlResearchForecast).where(
        NhlResearchForecast.canonical_game_ref == canonical_game_ref, NhlResearchForecast.model_id == MODEL_ID,
        NhlResearchForecast.record_class == "FORECAST",
        NhlResearchForecast.producer_evidence_status == EVIDENCE_STATUS)).all()
    return max(rows, key=lambda r: (_aware(r.published_utc), _aware(r.imported_at))) if rows else None


def _same_inputs(row: NhlResearchForecast, start: datetime, features: dict[str, Any]) -> bool:
    held = (row.producer_evidence_ref or {}).get("inputFeatures")
    return (_aware(row.scheduled_start_utc) == start and held == features
            and row.configuration_sha256 == CONFIGURATION_SHA256)


def _sync_game(session: Session, game: dict[str, Any], *, now: datetime) -> tuple[Any, list[str]]:
    """Bind the AISP game for a document's ``game`` block, or admit it exactly as
    ``import_manifest(admit_unbound_games=True)`` does (``identity.ensure_game``, RESEARCH_SHADOW).

    A bound game whose start the schedule moved follows the schedule only while it is
    still SCHEDULED and both clocks are in the future; otherwise the difference is noted.
    """

    from app.nhl.gen1_import import bind_game
    from app.nhl.identity import ensure_game

    ref = game["canonicalGameId"]
    game_id, orientation = bind_game(session, ref, game["home"]["canonicalTeamId"], game["away"]["canonicalTeamId"])
    if orientation:
        raise ValueError(";".join(orientation))
    if game_id is None:
        return ensure_game(session, game), ["GAME_ADMITTED_FROM_SCHEDULE_IDENTITY"]
    start = c.parse_utc(game["scheduledStartUtc"])
    row = session.get(Game, game_id)
    held = _aware(row.starts_at) if row is not None else None
    if row is not None and held is not None and start is not None and held != start:
        if row.status == GameStatus.SCHEDULED and held > now and start > now:
            row.starts_at = start
            return game_id, [f"START_MOVED:{held.isoformat()}->{start.isoformat()}"]
        return game_id, [f"SCHEDULED_START_DIFFERS_FROM_AISP_GAME:{held.isoformat()}"]
    return game_id, []


def generate_from_capture(session: Session, settings: Any, capture: SourceCapture, *,
                          now: Callable[[], datetime] | None = None, run_id: str | None = None,
                          revision: str | None = None) -> dict[str, Any]:
    """Build and persist today's and tomorrow's forecasts from a verified capture. Does not commit."""

    now = now or _now
    from app.nhl.gen1_import import ImportSummary, _receipt, forecast_row, revision_disposition

    run_id = run_id or uuid.uuid4().hex
    started = now()
    result: dict[str, Any] = {
        "schema": RESULT_SCHEMA, "producer": PRODUCER, "run_id": run_id, "research_only": True,
        "paper_authority": False, "provider_called": bool(capture.requests), "paid_requests": 0,
        "source": capture.summary(), "status": capture.status, "reason": capture.reason,
        "records": 0, "writes": False, "counts": {}, "games": [], "excluded": [], "start_moved": [],
    }
    if capture.status != "CAPTURED" or capture.schedule_bytes is None or capture.observed_utc is None:
        return result
    try:
        schedule = parse_schedule(capture.schedule_bytes)
        crosswalk = frozen.team_crosswalk()
        state = frozen.selected_state()
        handoff = frozen.handoff_sha256()
    except ScheduleInvalid as exc:
        result.update(status="SCHEDULE_INVALID", reason=str(exc))
        return result
    except frozen.FrozenArtifactChanged as exc:
        result.update(status="FROZEN_ARTIFACT_CHANGED", reason=str(exc))
        return result

    observed = capture.observed_utc
    local = started.astimezone(ET).date()
    targets = [local.isoformat(), (local + timedelta(days=1)).isoformat()]
    result["target_dates"] = targets
    slates = [prepare_slate(schedule, t, observed=observed, started=started, crosswalk=crosswalk,
                            model_team_ids=state["teamIds"]) for t in targets]
    predictions = [predict(s.rows) for s in slates]
    regular = [r for r in schedule if r["game_type"] == REGULAR and r["season_full"] == SEASON_FULL]
    created = utc_text(now())                 # the real generation clock, after the model ran
    identity = code_identity()
    summary = ImportSummary(run_id=run_id)
    counts = {"considered": 0, "IMPORTED": 0, "REVISED": 0, "UNCHANGED": 0, "REJECTED": 0,
              "SKIPPED": sum(len(s.excluded) for s in slates)}
    label = f"{PRODUCER}:{run_id}"
    for slate, preds in zip(slates, predictions):
        result["excluded"].extend({**e, "targetDate": slate.target_date} for e in slate.excluded)
        for features, g, p in zip(slate.rows, slate.games, preds):
            counts["considered"] += 1
            ref = "nhl:game:" + g["game_id"]
            start = _parse_clock(g["game_time"])
            entry: dict[str, Any] = {"canonicalGameId": ref, "targetDate": slate.target_date,
                                     "scheduledStartUtc": g["game_time"]}
            # Measured again, not assumed: prepare_slate already skipped every such game (REST_INCOMPLETE).
            pending = [gid for side in ("home", "away")
                       for gid in pending_earlier_games(regular, g[f"{side}_team_abbr"], str(g["game_date"]),
                                                        observed)]
            prior = latest_in_process(session, ref)
            if prior is not None and _same_inputs(prior, start, features):
                counts["UNCHANGED"] += 1
                result["games"].append({**entry, "disposition": "UNCHANGED", "forecastId": prior.forecast_id})
                continue
            evidence = {
                "schema": EVIDENCE_SCHEMA, "producer": PRODUCER, "runId": run_id, "generatedAtUtc": created,
                "coordinatorDecision": "CLAUDE_COORDINATOR_NHL_SIMPLIFICATION_2026-10-16",
                "code": identity, "generatorGitRevision": revision,
                "schedule": capture.schedule, "rights": capture.rights, "attribution": ATTRIBUTION,
                "targetDate": slate.target_date, "scheduleRow": g, "inputFeatures": features,
                # The paper lifecycle's in-process eligibility requires this to be exactly True.
                "restComplete": not pending, "restCompleteRule": REST_COMPLETE_RULE,
                "restPendingEarlierGames": pending,
                "restIsScheduledOnly": True, "outcomeColumnsLoaded": [], "fittingRuns": 0,
                "authority": dict(AUTHORITY),
            }
            evidence_ref = evidence_binding(evidence)
            document = forecast_document(g, p, crosswalk=crosswalk, observed=observed, created=created,
                                         published=created, evidence_ref=evidence_ref, evidence_mode=EVIDENCE_MODE,
                                         supersedes=prior.forecast_id if prior is not None else None,
                                         handoff_sha256=handoff)
            raw = c.canonical_bytes(document)
            problems = c.validate_forecast(document, evidence_modes=EVIDENCE_MODES)
            if pending:
                problems.append(f"{REST_INCOMPLETE}:{','.join(pending)}")
            write_clock = now()
            if write_clock < c.parse_utc(created):          # type: ignore[operator]
                problems.append("CLOCK_ORDER:write_clock_before_generation_clock")
            game_id, notes = None, []
            if not problems:
                try:
                    game_id, notes = _sync_game(session, document["game"], now=write_clock)
                except ValueError as exc:
                    problems.append(str(exc))
            if problems:
                counts["REJECTED"] += 1
                _receipt(session, summary, kind="FORECAST", disposition="REJECTED", identity=document["forecastId"],
                         object_sha256=_sha(raw), manifest_sha256=None, game_ref=ref, bound=False,
                         problems=problems, source_label=label, now=write_clock)
                result["games"].append({**entry, "disposition": "REJECTED", "problems": problems})
                continue
            if session.scalar(select(NhlResearchForecast.id).where(
                    NhlResearchForecast.forecast_id == document["forecastId"])) is not None:
                counts["UNCHANGED"] += 1          # identical identity already held: never a second row
                result["games"].append({**entry, "disposition": "UNCHANGED", "forecastId": document["forecastId"]})
                continue
            disposition = revision_disposition(session, ref, MODEL_ID)
            session.add(forecast_row(
                document, object_sha256=_sha(raw), object_bytes=len(raw), manifest_sha256=None, game_id=game_id,
                record_class="FORECAST", producer_evidence_status=EVIDENCE_STATUS,
                producer_evidence_ref={**evidence_ref, **evidence},
                published_utc=c.parse_utc(created), publication_clock_source=PUBLICATION_CLOCK_SOURCE,
                available_as_of=write_clock, imported_at=write_clock))
            session.flush()
            _receipt(session, summary, kind="FORECAST", disposition=disposition, identity=document["forecastId"],
                     object_sha256=_sha(raw), manifest_sha256=None, game_ref=ref, bound=True,
                     problems=notes, source_label=label, now=write_clock)
            counts[disposition] += 1
            if any(n.startswith("START_MOVED") for n in notes):
                result["start_moved"].append({"canonicalGameId": ref, "notes": notes})
            result["games"].append({**entry, "disposition": disposition, "forecastId": document["forecastId"],
                                    "supersedesForecastId": document["supersedesForecastId"], "notes": notes,
                                    "finalHome": document["probabilities"]["final"]["home"],
                                    "importedAt": write_clock.isoformat()})
    written = counts["IMPORTED"] + counts["REVISED"]
    result.update(status="COMPLETED", reason=None, counts=counts, records=written,
                  writes=written > 0 or counts["REJECTED"] > 0 or bool(result["start_moved"]),
                  generated_at_utc=created, observed_utc=observed)
    return result


async def run_generation(session: Session, settings: Any, *, client: httpx.AsyncClient | None = None,
                         now: Callable[[], datetime] | None = None, revision: str | None = None) -> dict[str, Any]:
    """One full run: capture (network), then generate (database). The caller commits."""

    now = now or _now
    capture = await capture_source(settings, client=client, now=now)
    return generate_from_capture(session, settings, capture, now=now,
                                 revision=revision if revision is not None else git_revision())


__all__ = ["ALLOWED_HOSTS", "APPROVED_LICENSE_SHA256", "ASSET_URL", "CONFIGURATION_SHA256", "EVIDENCE_MODE",
           "evidence_binding",
           "EVIDENCE_STATUS", "LICENSE_URL", "MODEL_ID", "PRODUCER", "README_URL", "RELEASE_API_URL",
           "REST_COMPLETE_RULE", "REST_INCOMPLETE",
           "ScheduleInvalid", "Slate", "SourceCapture", "capture_source", "forecast_document",
           "generate_from_capture", "parse_schedule", "pending_earlier_games", "predict", "prepare_slate",
           "run_generation", "utc_text"]
