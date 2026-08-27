"""SportsDataIO MLB availability adapter (injuries / player status).

The single injury provider for this phase. Disabled unless a key is configured;
the key is read from the environment and never logged, serialized, or committed.
With no key it reports INJURY_FEED_NOT_CONFIGURED, runs nothing, and the
MANUAL_ADMIN path remains the only source. The SportsDataIO free trial scrambles
player identity/status ("Scrambled"); that is detected and labelled TRIAL and no
identity is forced onto the live slate. Every parsing/mapping/change-detection
function is pure and testable without a network call or an API key.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import IngestionRun, RunStatus
from app.prospective.contracts import AvailabilityEventInput, AvailabilityEventType


SOURCE = "SPORTSDATAIO"
INJURED_PLAYERS_PATH = "/projections/json/InjuredPlayers"

STATUS_NOT_CONFIGURED = "INJURY_FEED_NOT_CONFIGURED"
STATUS_ACTIVE = "INJURY_FEED_ACTIVE"
STATUS_TRIAL = "INJURY_FEED_TRIAL"
STATUS_ERROR = "INJURY_FEED_ERROR"

# The literal value SportsDataIO returns for scrambled trial fields.
TRIAL_SENTINEL = "Scrambled"

_INJURED_LIST = "injured list"


# ---------------------------------------------------------------------------
# Normalization (pure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedInjury:
    player_source_id: str
    mlbam_id: str | None
    team: str | None
    player_name: str | None
    position: str | None
    roster_status: str          # SportsDataIO "Status" (Active / *Injured List / ...)
    injury_status: str          # SportsDataIO "InjuryStatus" (Out / Day-To-Day / ... / Scrambled)
    body_part: str | None
    notes: str | None
    injury_start: str | None

    def change_key(self) -> tuple[str, str]:
        return (self.roster_status.strip().lower(), self.injury_status.strip().lower())


def _clean(value: object, *, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit] if limit else text


def _name(row: Mapping) -> str | None:
    name = _clean(row.get("Name"), limit=160)
    if name:
        return name
    first = _clean(row.get("FirstName"))
    last = _clean(row.get("LastName"))
    combined = " ".join(part for part in (first, last) if part).strip()
    return combined[:160] or None if combined else None


def parse_injuries(rows: Iterable[Mapping]) -> list[NormalizedInjury]:
    """Normalize raw InjuredPlayers rows. Missing fields stay None; never invented."""

    parsed: list[NormalizedInjury] = []
    for row in rows:
        player_id = _clean(row.get("PlayerID"))
        if player_id is None:
            continue
        parsed.append(NormalizedInjury(
            player_source_id=player_id,
            mlbam_id=_clean(row.get("MLBAMID")),
            team=_clean(row.get("Team")),
            player_name=_name(row),
            position=_clean(row.get("Position")),
            roster_status=_clean(row.get("Status")) or "",
            injury_status=_clean(row.get("InjuryStatus")) or "",
            body_part=_clean(row.get("InjuryBodyPart") or row.get("BodyPart"), limit=120),
            notes=_clean(row.get("InjuryNotes"), limit=500),
            injury_start=_clean(row.get("InjuryStartDate")),
        ))
    return parsed


def is_trial(rows: Iterable[Mapping]) -> bool:
    """True when the response is SportsDataIO trial/scrambled data.

    The trial replaces identity/status with the literal "Scrambled"; a majority
    of scrambled rows marks the whole response as trial (SPORTSDATAIO_TRIAL_DATA).
    """

    rows = list(rows)
    if not rows:
        return False
    scrambled = sum(
        1 for row in rows
        if str(row.get("InjuryStatus") or "") == TRIAL_SENTINEL
        or str(row.get("InjuryBodyPart") or "") == TRIAL_SENTINEL
    )
    return scrambled * 2 >= len(rows)


# ---------------------------------------------------------------------------
# Change detection + event mapping (pure)
# ---------------------------------------------------------------------------


def injury_event_type(
    previous: NormalizedInjury | None,
    current: NormalizedInjury,
) -> AvailabilityEventType | None:
    """Map a material availability change onto an existing event type.

    Returns None when nothing material changed (dedup). Reuses the governed
    AISP_AVAILABILITY_EVENT_V1 event types; no new event framework is created.
    """

    if previous is not None and previous.change_key() == current.change_key():
        return None  # no material change

    prev_il = bool(previous and _INJURED_LIST in previous.roster_status.lower())
    curr_il = _INJURED_LIST in current.roster_status.lower()
    if curr_il and not prev_il:
        return AvailabilityEventType.INJURED_LIST_PLACED
    if prev_il and current.roster_status.strip().lower() == "active":
        return AvailabilityEventType.ACTIVATED

    injury = current.injury_status.strip().lower()
    if injury == "out":
        return AvailabilityEventType.CONFIRMED_OUT
    if injury in {"day-to-day", "day to day"}:
        return AvailabilityEventType.DAY_TO_DAY
    if injury in {"questionable", "doubtful", "probable"}:
        return AvailabilityEventType.INJURY_STATUS_CHANGED
    if previous is None:
        return None  # first sighting establishes a baseline, no event
    return AvailabilityEventType.INJURY_STATUS_CHANGED


def injuries_to_events(
    fresh: Iterable[NormalizedInjury],
    previous: Mapping[str, NormalizedInjury],
    *,
    published_at: datetime | None = None,
) -> list[AvailabilityEventInput]:
    """Emit events only for players whose availability materially changed.

    A player's first observation only establishes the baseline (no event), so a
    repeated identical response yields no new events (idempotent).
    """

    events: list[AvailabilityEventInput] = []
    for current in fresh:
        prior = previous.get(current.player_source_id)
        if prior is None:
            continue  # baseline-first
        event_type = injury_event_type(prior, current)
        if event_type is None:
            continue
        events.append(AvailabilityEventInput(
            event_type=event_type,
            team_source_id=current.team,
            player_source_id=current.player_source_id,
            player_name=current.player_name,
            published_at=published_at,
            description=(
                f"{prior.injury_status or 'None'}/{prior.roster_status or 'None'} -> "
                f"{current.injury_status or 'None'}/{current.roster_status or 'None'}"
            )[:500],
            payload={
                "provider": SOURCE,
                "mlbam_id": current.mlbam_id,
                "roster_status": current.roster_status,
                "injury_status": current.injury_status,
                "body_part": current.body_part,
            },
        ))
    return events


def compact_snapshot(parsed: Iterable[NormalizedInjury]) -> dict[str, list[str]]:
    """Minimal per-player state persisted for stateful change detection."""

    return {p.player_source_id: [p.roster_status, p.injury_status] for p in parsed}


def _previous_from_snapshot(snapshot: Mapping[str, list] | None) -> dict[str, NormalizedInjury]:
    previous: dict[str, NormalizedInjury] = {}
    for player_id, value in (snapshot or {}).items():
        roster = str(value[0]) if value else ""
        injury = str(value[1]) if value and len(value) > 1 else ""
        previous[str(player_id)] = NormalizedInjury(
            player_source_id=str(player_id), mlbam_id=None, team=None, player_name=None,
            position=None, roster_status=roster, injury_status=injury,
            body_part=None, notes=None, injury_start=None,
        )
    return previous


@dataclass(frozen=True)
class InjuryPollPlan:
    trial: bool
    records: int
    snapshot: dict[str, list[str]]
    candidate_events: list[AvailabilityEventInput]


def plan_injury_poll(
    rows: Iterable[Mapping],
    previous_snapshot: Mapping[str, list] | None = None,
    *,
    published_at: datetime | None = None,
) -> InjuryPollPlan:
    """Pure plan for one injuries poll: trial flag, fresh snapshot, and the
    change-detected candidate events (empty on trial/scrambled data)."""

    rows = list(rows)
    parsed = parse_injuries(rows)
    trial = is_trial(rows)
    snapshot = compact_snapshot(parsed)
    candidates: list[AvailabilityEventInput] = []
    if not trial:
        candidates = injuries_to_events(
            parsed, _previous_from_snapshot(previous_snapshot), published_at=published_at,
        )
    return InjuryPollPlan(trial=trial, records=len(parsed), snapshot=snapshot, candidate_events=candidates)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class SportsDataIOAvailabilityAdapter:
    SOURCE = SOURCE

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_configured(self) -> bool:
        return bool(self.settings.sportsdataio_enabled and self.settings.sportsdataio_api_key.strip())

    def status(self, session: Session | None = None) -> str:
        """Feed status for Data Health: NOT_CONFIGURED / ACTIVE / TRIAL / ERROR.

        Reflects the last persisted poll health when a session is supplied; never
        makes a provider call and never exposes the key."""

        if not self.is_configured():
            return STATUS_NOT_CONFIGURED
        if session is None:
            return STATUS_ACTIVE
        run = session.scalar(
            select(IngestionRun)
            .where(IngestionRun.source == SOURCE)
            .order_by(IngestionRun.completed_at.desc().nulls_last(), IngestionRun.created_at.desc())
            .limit(1)
        )
        if run is None:
            return STATUS_ACTIVE
        meta = run.source_metadata or {}
        if run.status == RunStatus.FAILED or meta.get("error"):
            return STATUS_ERROR
        if meta.get("trial"):
            return STATUS_TRIAL
        return STATUS_ACTIVE

    async def fetch_injuries(self, *, client=None) -> tuple[int, list[dict]]:
        """One real InjuredPlayers request. Header auth; the key is never logged.

        Returns (http_status, rows). Disabled/misconfigured → (0, []). A non-200
        or transport error returns an empty list — a provider failure never
        fabricates availability.
        """

        if not self.is_configured():
            return (0, [])
        import httpx

        url = self.settings.sportsdataio_base_url.rstrip("/") + INJURED_PLAYERS_PATH
        headers = {"Ocp-Apim-Subscription-Key": self.settings.sportsdataio_api_key.strip()}
        owns_client = client is None
        http_client = client or httpx.AsyncClient(timeout=30)
        try:
            response = await http_client.get(url, headers=headers)
            if response.status_code != 200:
                return (response.status_code, [])
            data = response.json()
            return (200, data if isinstance(data, list) else [])
        except (httpx.HTTPError, ValueError):
            return (0, [])
        finally:
            if owns_client:
                await http_client.aclose()


__all__ = [
    "SOURCE",
    "INJURED_PLAYERS_PATH",
    "TRIAL_SENTINEL",
    "STATUS_NOT_CONFIGURED",
    "STATUS_ACTIVE",
    "STATUS_TRIAL",
    "STATUS_ERROR",
    "NormalizedInjury",
    "InjuryPollPlan",
    "SportsDataIOAvailabilityAdapter",
    "parse_injuries",
    "is_trial",
    "injury_event_type",
    "injuries_to_events",
    "compact_snapshot",
    "plan_injury_poll",
]
