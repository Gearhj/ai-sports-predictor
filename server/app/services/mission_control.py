"""Mission Control: every game of one Eastern date as the pipeline it travels through. Read-only.

    FORECAST -> APPROVAL -> PRICES -> DECISION -> BET -> LIVE -> RESULT

WHAT IT IS
----------
One read model (``AISP_MISSION_CONTROL_V1``) that answers, for each game of an
Eastern slate date, where it stands in the paper pipeline right now: which stage
is done, which is waiting (and when it is due), which is blocked (and why, in
plain English), and what happens next. Every stage is evaluated against ONE
clock (``generated_at``), so two stages of one game can never disagree about
what time it is.

WHAT IT NEVER DOES
------------------
Request a price, evaluate a policy, take the shared-allowance lock or write a
row. It reads what the owners already persisted, through their own read
helpers (``command_center.market_coverage`` / ``decision_board``, the
scoreboard, the NHL lifecycle's reporting reads, the CFB/NFL gates), and says
what they would do next. ``shared_daily_allowance.room()`` is deliberately never
called: it takes ``pg_advisory_xact_lock`` and would serialize this page against
every sport's position writer.

HONEST STATES
-------------
A stage is DONE, WAITING (with ``due_at``), BLOCKED (with a plain reason),
NOT_APPLICABLE, or UNKNOWN. UNKNOWN means a read failed and is never shown as
DONE. Missing data is reported as missing. One sport's failing read marks that
sport READ_FAILED (its games' stages UNKNOWN) and never blanks the others.

THE PAYLOAD DOES NOT VARY BY CALLER
-----------------------------------
The SWR read cache keys by path + query only, so nothing here depends on who
asks, and no error message, credential or quota figure is ever included -- only
error TYPE names.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

SCHEMA_VERSION = "AISP_MISSION_CONTROL_V1"
AUTHORITY = "Virtual research only. No real-money wagering and no execution outside AISP."
MAX_DAYS_BACK = 1
MAX_DAYS_AHEAD = 3
REFRESH = {"client_poll_seconds": 30, "server_fresh_seconds": 30, "server_max_stale_seconds": 90}
ET = ZoneInfo("America/New_York")

SPORTS = ("MLB", "NFL", "CFB", "NHL", "NBA")
SPORT_LABELS = {"MLB": "MLB", "NFL": "NFL", "CFB": "College Football", "NHL": "NHL", "NBA": "NBA"}
BANKROLL_SPORTS = ("MLB", "NFL", "CFB", "NBA", "NHL")
KICK_WORD = {"MLB": "First pitch", "NFL": "Kickoff", "CFB": "Kickoff", "NHL": "Puck drop", "NBA": "Tip-off"}
#: Games that are never real; they never appear on Mission Control.
EXCLUDED_SOURCE_MODES = ("DEMO", "FIXTURE", "SYNTHETIC", "PREVIEW_FIXTURE")
#: Where a sport's own source game id lives (``external_id_mappings.source``).
SOURCE_ID_MAPPINGS = {"CFBD": "CFB", "NHL_STATS_GAME": "NHL"}

#: The shared paper decision window (every official sport): T-30..T-15.
WINDOW_OPENS = timedelta(minutes=30)
WINDOW_CLOSES = timedelta(minutes=15)
#: The MLB V18 producer's consumer-ready TARGET (never a guarantee).
MLB_PUBLICATION_TARGET = timedelta(minutes=45)
#: NFL FINAL_PREGAME generation runs T-60..T-15.
NFL_FINAL_GENERATION_OPENS = timedelta(minutes=60)
#: CFB and NHL The Odds API buy windows (their collectors' own constants, read at call time too).
BUY_OPENS = timedelta(minutes=75)
BUY_CLOSES = timedelta(minutes=35)
QUOTE_FRESHNESS_MINUTES = 240
FRESH_HOURS, AGING_HOURS = 24, 72
LABEL_MAX = 32
#: The CFB producer issues a discovery manifest (and re-issues every lease, each good for about 15 minutes)
#: about every 15 minutes; AISP's consumer imports on its own 15-minute cadence. Two cadences without a
#: newer manifest, or a lease lapsed for more than one cadence, means renewals are not arriving.
CFB_MANIFEST_STALE = timedelta(minutes=30)
CFB_LEASE_RENEWAL_GRACE = timedelta(minutes=15)
#: The health strip treats a sport as active when it plays on the date or within this horizon.
RELEVANT_AHEAD = timedelta(hours=48)
#: A live MLB/NFL game with no score observation this long after its start reads BLOCKED, not WAITING.
NO_SCORE_GRACE = timedelta(minutes=30)

DONE, WAITING, BLOCKED, NA, UNKNOWN = "DONE", "WAITING", "BLOCKED", "NOT_APPLICABLE", "UNKNOWN"
STAGES = ("forecast", "approval", "prices", "decision", "bet", "live", "result")
PRE_BET = ("forecast", "approval", "prices", "decision")
STAGE_WORD = {"forecast": "FORECAST", "approval": "APPROVAL", "prices": "PRICES", "decision": "DECISION",
              "bet": "BET", "live": "LIVE", "result": "RESULT"}
WAIT_LABEL = {"forecast": "Forecast in", "approval": "Approval in", "prices": "Prices in", "decision": "Decision in",
              "bet": "Bet window in", "live": "Starts in", "result": "Result in"}
#: A WAITING stage whose clock is a DEADLINE something unscheduled must beat, not a time it happens by itself.
DEADLINE_LABEL = {"EXPIRED_LEASE": "Lease must renew in", "AWAITING_PRODUCER_ELIGIBILITY": "Record needed in"}

#: Stage-specific fields, always present (null when unknown) so every block has one shape.
STAGE_FIELDS: dict[str, dict[str, Any]] = {
    "forecast": {"made_at": None, "made_at_basis": None, "source": None, "engine_id": None, "forecast_id": None,
                 "snapshot": None, "freshness": None, "age_minutes": None, "home_win_prob": None,
                 "away_win_prob": None, "favorite": None},
    "approval": {"route": None, "eligible": None, "valid_until": None},
    "prices": {"best": {"home": None, "away": None}, "books": 0, "fresh_books": 0, "quote_age_minutes": None,
               "fresh_until": None, "consensus_home": None, "collector": "UNKNOWN", "permitted_sources": []},
    "decision": {"class": None, "recommendation": None, "reason_code": None, "phase": None, "recorded_at": None,
                 "decision_id": None, "edge": None, "model_probability": None, "market_probability": None},
    "bet": {"position_id": None, "side": None, "american": None, "sportsbook": None, "stake_usd": None,
            "to_win_usd": None, "placed_at": None, "status": None, "display_status": None},
    "live": {"available": False, "is_live": False, "is_final": False, "away_score": None, "home_score": None,
             "period_label": None, "freshness": None, "last_checked_at": None},
    "result": {"outcome": None, "profit_usd": None, "final": None, "awaiting_settlement": False,
               "settled_at": None},
}

#: Recorded decision reasons that mean a prerequisite blocked the evaluation (nothing was priced).
BLOCKING_REASONS = frozenset({
    "AFTER_DECISION_CUTOFF", "BEFORE_DECISION_WINDOW", "SCHEDULE_DRIFT", "SHARED_ALLOWANCE_NOT_EFFECTIVE",
    "NO_FORECAST", "NO_FINAL_PREGAME_FORECAST", "FORECAST_FINAL_CHANNEL_INVALID", "AWAITING_PRODUCER_ELIGIBILITY",
    "ELIGIBILITY_RECORD_INVALID", "DISCOVERY_NOT_CURRENT", "POLICY_GRANT_EXPIRED", "NO_PAPER_RESEARCH_ELIGIBILITY_RECORD",
    "AWAITING_SUCCESSOR_ELIGIBILITY", "NHL_PAPER_POLICY_IS_NOT_ACTIVE",
})
#: Recorded reasons that are a priced decline (a real pass): CFB/NHL's NO_QUALIFYING_EDGE, NFL's
#: NO_QUALIFYING_VALUE (nfl_paper_decision: no side of a fresh permitted pair cleared the minimum), and the
#: allowance/one-position refusals.
PASS_REASONS = frozenset({"NO_QUALIFYING_EDGE", "NO_QUALIFYING_VALUE", "SHARED_ALLOWANCE_EXHAUSTED",
                          "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS", "GAME_ALREADY_HOLDS_A_POSITION"})
#: The budget declines inside PASS_REASONS (AISP_SHARED_100_PRIORITY_MLB_NHL_V1): evaluated at a usable price
#: and the value qualified, but the shared $100 had no room -- fully entered, or its rest held for MLB/NHL games
#: still to decide. Declined for budget, never a no-bet. Only the held room can free up (each sport's checkpoint
#: lists it among its PREREQUISITE_REASONS); a fully entered allowance is final.
NO_ROOM_REASONS = frozenset({"SHARED_ALLOWANCE_EXHAUSTED", "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS"})

REASON_TEXT = {
    "NO_QUALIFYING_EDGE": "no side cleared the policy's edge test",
    "NO_QUALIFYING_VALUE": "no side cleared the policy's value test",
    "SHARED_ALLOWANCE_EXHAUSTED": "the shared $100 for the day was already entered",
    "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS": "the rest of the shared $100 was held for MLB and NHL games still to decide",
    "GAME_ALREADY_HOLDS_A_POSITION": "this game already holds a position",
    "AFTER_DECISION_CUTOFF": "the decision clock was after T-15",
    "BEFORE_DECISION_WINDOW": "the decision clock was before T-30",
    "SCHEDULE_DRIFT": "the game moved after its forecast was made",
    "SHARED_ALLOWANCE_NOT_EFFECTIVE": "the shared $100 daily allowance is not in force",
    "NO_FORECAST": "no forecast was held",
    "NO_FINAL_PREGAME_FORECAST": "no FINAL_PREGAME forecast was available before T-15",
    "FORECAST_FINAL_CHANNEL_INVALID": "the forecast's final channel is not a two-way probability",
    "AWAITING_PRODUCER_ELIGIBILITY": "the producer's eligibility record was not held",
    "ELIGIBILITY_RECORD_INVALID": "the eligibility record did not qualify",
    "DISCOVERY_NOT_CURRENT": "the forecast's discovery lease was not current",
    "POLICY_GRANT_EXPIRED": "the virtual-policy grant had expired",
    "NO_TWO_SIDED_QUOTE": "no two-sided price was held",
    "NO_PERMITTED_PROVIDER": "no price came from a permitted provider",
    "QUOTE_TOO_STALE": "every price was older than 240 minutes on its older leg",
    "QUOTE_KICKOFF_MISMATCH": "the stored prices were taken at a different kickoff",
    "ACTIVATION_CONTROL_OFF": "every gate passed but the activation control is off",
    "ALL_GATES_PASSED": "every gate passed",
    "NO_PAPER_RESEARCH_ELIGIBILITY_RECORD": "the producer's eligibility record is missing",
    "AWAITING_SUCCESSOR_ELIGIBILITY": "a newer forecast awaits its successor eligibility record",
}


# ------------------------------------------------------------------ helpers --

def _aware(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: Any) -> str | None:
    moment = _aware(value)
    return moment.isoformat() if moment else None


def _clock(value: Any) -> str:
    moment = _aware(value)
    if moment is None:
        return "?"
    local = moment.astimezone(ET)
    return f"{local.hour % 12 or 12}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"


def _when(value: Any, now: datetime) -> str:
    """'7:45 PM' on now's Eastern date, 'Sat 7:45 PM' within the coming week, else 'Oct 11 7:45 PM'."""

    moment = _aware(value)
    if moment is None:
        return "?"
    local, today = moment.astimezone(ET), now.astimezone(ET).date()
    if local.date() == today:
        return _clock(moment)
    if today < local.date() <= today + timedelta(days=6):
        return f"{local:%a} {_clock(moment)}"
    year = f" {local.year}" if local.year != today.year else ""
    return f"{local:%b} {local.day}{year} {_clock(moment)}"


def _label(text: str | None) -> str | None:
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= LABEL_MAX else text[: LABEL_MAX - 1] + "…"


def _minutes(delta: timedelta) -> float:
    return round(delta.total_seconds() / 60.0, 1)


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _money(value: float | None) -> str:
    if value is None:
        return "$?"
    sign = "+" if value > 0 else "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _american_text(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "?"
    return f"+{number}" if number > 0 else str(number)


def _american_to_decimal(american: Any) -> float | None:
    try:
        number = int(american)
    except (TypeError, ValueError):
        return None
    if number == 0:
        return None
    return 1 + (number / 100.0 if number > 0 else 100.0 / abs(number))


def _decimal_to_american(decimal_odds: Any) -> int | None:
    value = _num(decimal_odds)
    if value is None or value <= 1:
        return None
    return int(round((value - 1) * 100)) if value >= 2 else int(round(-100 / (value - 1)))


def _plain(code: str | None) -> str:
    if not code:
        return ""
    return REASON_TEXT.get(code, code.replace("_", " ").lower())


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def _stage(kind: str, state: str, code: str | None = None, label: str | None = None, detail: str | None = None,
           *, due_at: Any = None, at: Any = None, **fields: Any) -> dict[str, Any]:
    block = {"state": state, "code": code, "label": _label(label), "detail": detail,
             "due_at": _iso(due_at) if state == WAITING else None,
             "at": _iso(at) if state == DONE else None}
    defaults = STAGE_FIELDS[kind]
    out = {**block, **{k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                       for k, v in defaults.items()}}
    out.update(fields)
    return out


def _unknown(kind: str, error_type: str | None) -> dict[str, Any]:
    return _stage(kind, UNKNOWN, "READ_FAILED", "Read failed",
                  f"This stage could not be read ({error_type or 'unknown error'}); it is not shown as done.")


def _favorite(home_p: float | None, away_p: float | None, teams: dict[str, Any]) -> dict[str, Any] | None:
    if home_p is None or away_p is None:
        return None
    side = "HOME" if home_p >= away_p else "AWAY"
    return {"side": side, "team": teams[side.lower()]["abbr"], "prob": round(max(home_p, away_p), 4)}


def _freshness(made_at: datetime | None, now: datetime, start: datetime) -> tuple[str | None, float | None]:
    if made_at is None:
        return None, None
    age = min(now, start) - made_at
    hours = age.total_seconds() / 3600.0
    state = "FRESH" if hours <= FRESH_HOURS else "AGING" if hours <= AGING_HOURS else "STALE"
    return state, _minutes(age)


def _forecast_done(game: "_Game", now: datetime, *, made_at: Any, basis: str, source: str, engine_id: str | None,
                   forecast_id: str | None, home_p: float | None, away_p: float | None,
                   snapshot: str | None = None, code: str = "PUBLISHED", state: str = DONE,
                   label: str | None = None, detail: str | None = None, due_at: Any = None) -> dict[str, Any]:
    made = _aware(made_at)
    freshness, age = _freshness(made, now, game.start)
    favorite = _favorite(home_p, away_p, game.teams)
    if label is None:
        label = f"{favorite['team']} {favorite['prob']:.0%}" if favorite else "Forecast held"
    if detail is None:
        detail = (f"{source} forecast made {_when(made, now)} ET"
                  + (f" ({freshness.lower()}: {age / 60:.1f} h old"
                     + (" at the start" if now >= game.start else "") + ")"
                     if freshness and age is not None else "") + ".")
    return _stage("forecast", state, code, label, detail, at=made, due_at=due_at, made_at=_iso(made),
                  made_at_basis=basis, source=source, engine_id=engine_id, forecast_id=forecast_id,
                  snapshot=snapshot, freshness=freshness, age_minutes=age,
                  home_win_prob=round(home_p, 4) if home_p is not None else None,
                  away_win_prob=round(away_p, 4) if away_p is not None else None, favorite=favorite)


class _Game:
    """One game of the day, with every clock in UTC."""

    def __init__(self, game_id: Any, sport: str, starts_at: Any, status: Any, source_mode: str | None) -> None:
        self.uuid = game_id if isinstance(game_id, uuid.UUID) else uuid.UUID(str(game_id))
        self.id = str(self.uuid)
        self.sport = sport
        self.start: datetime = _aware(starts_at)  # type: ignore[assignment]
        self.status = str(getattr(status, "value", status) or "scheduled").split(".")[-1].lower()
        self.source_mode = source_mode
        self.source_game_id: str | None = None
        self.teams: dict[str, dict[str, Any]] = {"home": {"abbr": "?", "name": None},
                                                 "away": {"abbr": "?", "name": None}}

    @property
    def opens(self) -> datetime:
        return self.start - WINDOW_OPENS

    @property
    def closes(self) -> datetime:
        return self.start - WINDOW_CLOSES

    @property
    def matchup(self) -> str:
        return f"{self.teams['away']['abbr']} @ {self.teams['home']['abbr']}"


def _isolated(session: Session, fn: Callable[[], Any]) -> Any:
    """Run one read; if it fails, roll the (read-only) transaction back so the next read can run.

    On PostgreSQL a failed statement aborts the whole transaction. A SAVEPOINT would be tidier, but some
    of the shared read helpers this page reuses end the session's transaction themselves (the CFB price
    note's capacity gate), which is illegal inside a savepoint context -- so, as ``sport_readiness``
    does, a failure is followed by a plain rollback. This read model never writes, so nothing is lost.
    """

    try:
        return fn()
    except Exception:
        session.rollback()
        raise


class _Ctx:
    """Everything read once per request, plus the one evaluation clock."""

    def __init__(self, session: Session, settings: Any, day: date, now: datetime) -> None:
        self.session, self.settings, self.day, self.now = session, settings, day, now
        self.errors: dict[str, str] = {}
        self.coverage: dict[str, dict[str, Any]] | None = None
        self.decisions: dict[str, dict[str, Any]] | None = None
        self.reasons: dict[str, str | None] = {}
        self.positions: dict[str, dict[str, Any]] = {}
        self.scores: dict[str, dict[str, Any]] | None = None
        self.nhl_results: dict[str, dict[str, Any]] = {}
        self.active_policies: set[str] | None = None
        self.jobs: dict[str, dict[str, Any]] = {}
        self.extra: dict[str, Any] = {}

    def attempt(self, key: str, fn: Callable[[], Any], default: Any = None) -> Any:
        try:
            return _isolated(self.session, fn)
        except Exception as error:  # noqa: BLE001 -- a failed shared read degrades, never blanks
            self.errors[key] = type(error).__name__
            return default

    def next_run(self, scheduler_id: str) -> datetime | None:
        from app.jobs import scheduler as jobs_scheduler

        try:
            return _aware(jobs_scheduler.scheduler_next_run_time(scheduler_id))
        except Exception:  # noqa: BLE001
            return None

    def scheduler_running(self) -> bool:
        from app.jobs import scheduler as jobs_scheduler

        try:
            return bool(jobs_scheduler.scheduler_is_running())
        except Exception:  # noqa: BLE001
            return False


# ----------------------------------------------------------- shared reads --

def _games(session: Session, settings: Any, day: date) -> tuple[Any, list[_Game]]:
    from app.models import ExternalIdMapping, Game, GameTeam, League, Team
    from app.services.slate_status import slate_window

    window = slate_window(day, getattr(settings, "display_timezone", "America/New_York") or "America/New_York")
    rows = session.execute(
        select(Game.id, League.code, Game.starts_at, Game.status, Game.source_mode)
        .join(League, League.id == Game.league_id)
        .where(Game.starts_at >= window.start_utc, Game.starts_at < window.end_utc, League.code.in_(SPORTS),
               or_(Game.source_mode.is_(None), Game.source_mode.notin_(EXCLUDED_SOURCE_MODES)))
        .order_by(Game.starts_at, Game.id)).all()
    games = [_Game(gid, code, starts, status, mode) for gid, code, starts, status, mode in rows]
    if not games:
        return window, games
    by_id = {g.uuid: g for g in games}
    for game_id, is_home, abbreviation, name in session.execute(
            select(GameTeam.game_id, GameTeam.is_home, Team.abbreviation, Team.name)
            .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id.in_(list(by_id)))).all():
        abbr = abbreviation or name or "?"
        # College Football teams carry their CFBD id as a code; the school name is what a reader should see
        # (command_center._matchups' rule).
        if abbr and str(abbr).startswith("CFBD") and str(abbr)[4:].isdigit():
            abbr = name or abbr
        by_id[game_id].teams["home" if is_home else "away"] = {"abbr": abbr, "name": name}
    for entity_id, source, external in session.execute(
            select(ExternalIdMapping.entity_id, ExternalIdMapping.source, ExternalIdMapping.external_id)
            .where(ExternalIdMapping.entity_type == "game", ExternalIdMapping.source.in_(list(SOURCE_ID_MAPPINGS)),
                   ExternalIdMapping.entity_id.in_(list(by_id)))).all():
        game = by_id.get(entity_id)
        if game is not None and SOURCE_ID_MAPPINGS[source] == game.sport:
            game.source_game_id = str(external).rsplit(":", 1)[-1]
    return window, games


def _coverage(ctx: _Ctx) -> dict[str, dict[str, Any]]:
    from app.services import command_center

    payload = command_center.market_coverage(ctx.session, ctx.day, now=ctx.now)
    return {str(g["game_id"]): g for g in payload.get("games") or []}


PHASE_RANK = {"MORNING": 0, "LINEUP_CONFIRMED": 1, "FINAL_PREGAME": 2}


def _position_slates(ctx: _Ctx, ids: set[str], read: set[date]) -> list[date]:
    """Slate dates, other than those already read, of decisions that hold a position on one of these games.

    A position is recorded under the slate date of its decision. When the game is later postponed or
    rescheduled onto another day, the board of the decision's day no longer lists it (the game is not on
    that day any more) and the board of the game's new day has no decision for it, so without this the
    open bet would be invisible on the game's row and its P/L counted on no day.
    """

    from app.models import OfficialAspPosition, ShadowExperimentDecision

    if not ids:
        return []
    slates = ctx.session.scalars(
        select(ShadowExperimentDecision.slate_date).distinct()
        .join(OfficialAspPosition, OfficialAspPosition.shadow_decision_id == ShadowExperimentDecision.id)
        .where(ShadowExperimentDecision.game_id.in_([uuid.UUID(i) for i in ids]))).all()
    out = set()
    for slate in slates:
        if isinstance(slate, str):                       # SQLite hands a DATE back as text
            slate = date.fromisoformat(slate[:10])
        if isinstance(slate, datetime):
            slate = slate.date()
        if isinstance(slate, date) and slate not in read:
            out.add(slate)
    return sorted(out)


def _decisions(ctx: _Ctx, ids: set[str]) -> dict[str, dict[str, Any]]:
    """The day's decision per game, from the board of the day AND the next day (NFL slate_date is a UTC date).

    Plus every decision that holds a POSITION on one of the day's games whatever its slate date (a game
    postponed or rescheduled after its bet was placed), read from that slate's own board.
    """

    from app.services import command_center

    items: list[dict[str, Any]] = []
    read = {ctx.day + timedelta(days=offset) for offset in (0, 1)}
    for slate in sorted(read):
        board = command_center.decision_board(ctx.session, slate, ctx.now)
        items.extend(i for i in board.get("items") or [] if str(i.get("game_id")) in ids)
    for slate in _position_slates(ctx, ids, read):
        board = command_center.decision_board(ctx.session, slate, ctx.now)
        items.extend({**i, "carried_from_slate": slate.isoformat()} for i in board.get("items") or []
                     if str(i.get("game_id")) in ids and i.get("position"))
    best: dict[str, dict[str, Any]] = {}

    def rank(item: dict[str, Any]) -> tuple:
        return (item.get("position") is not None, PHASE_RANK.get(str(item.get("phase")), -1),
                item.get("recommended_at") or "")

    for item in items:
        gid = str(item["game_id"])
        if gid not in best or rank(item) > rank(best[gid]):
            best[gid] = item
    return best


def _reason_codes(ctx: _Ctx, decision_ids: list[str]) -> dict[str, str | None]:
    """The recorded reason CODE per decision (the board exposes only plain sentences)."""

    from app.models import ShadowExperimentDecision

    if not decision_ids:
        return {}
    out: dict[str, str | None] = {}
    for did, reasons in ctx.session.execute(
            select(ShadowExperimentDecision.id, ShadowExperimentDecision.qualification_reasons)
            .where(ShadowExperimentDecision.id.in_([uuid.UUID(d) for d in decision_ids]))).all():
        code = None
        if isinstance(reasons, dict):
            code = reasons.get("reason")
        elif isinstance(reasons, list):
            code = next((str(r) for r in reasons
                         if isinstance(r, str) and r and " " not in r and r.upper() == r), None)
        out[str(did)] = str(code) if code else None
    return out


def _positions(ctx: _Ctx, decision_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Entry and settlement clocks for the positions the day's decisions produced, keyed by decision id."""

    from app.models import OfficialAspPosition, PositionSettlement

    if not decision_ids:
        return {}
    rows = ctx.session.execute(
        select(OfficialAspPosition.shadow_decision_id, OfficialAspPosition.id, OfficialAspPosition.placed_at,
               OfficialAspPosition.decimal_odds, OfficialAspPosition.stake, OfficialAspPosition.status,
               PositionSettlement.settled_at, PositionSettlement.profit_loss, PositionSettlement.result)
        .outerjoin(PositionSettlement, PositionSettlement.position_id == OfficialAspPosition.id)
        .where(OfficialAspPosition.shadow_decision_id.in_([uuid.UUID(d) for d in decision_ids]))).all()
    out = {str(did): {"position_id": str(pid), "placed_at": placed, "decimal_odds": _num(dec), "stake": _num(stake),
                      "status": str(getattr(status, "value", status)).upper(), "settled_at": settled,
                      "profit_loss": _num(pnl), "result": str(getattr(res, "value", res) or "").upper() or None}
           for did, pid, placed, dec, stake, status, settled, pnl, res in rows}
    # A correction recorded beside the settlement (position_settlement_adjustments)
    # is what the position now reads as; none exist today.
    from app.cfb.settlement_corrections import effective_settlements

    corrected = effective_settlements(ctx.session, [pid for _d, pid, *_rest in rows if _rest[-2] is not None])
    for did, pid, *_rest in rows:
        fix = corrected.get(pid)
        if fix is not None:
            out[str(did)].update(result=fix.result, profit_loss=_num(fix.profit_loss), **fix.as_dict())
    return out


def _scores(ctx: _Ctx, game_ids: list[uuid.UUID]) -> dict[str, dict[str, Any]]:
    from app.scoreboard.read_service import scoreboard_for

    return scoreboard_for(ctx.session, game_ids, now=ctx.now)


def _nhl_results(ctx: _Ctx, game_ids: list[uuid.UUID]) -> dict[str, dict[str, Any]]:
    from app.models.nhl_research import NhlGameResult
    from app.nhl.read_service import NhlReadService, current_result

    if not game_ids:
        return {}
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in ctx.session.scalars(select(NhlGameResult).where(NhlGameResult.game_id.in_(game_ids))):
        grouped[str(row.game_id)].append(row)
    out = {}
    for gid, rows in grouped.items():
        summary = NhlReadService.result_summary(current_result(rows))
        if summary is not None:
            out[gid] = summary
    return out


# ------------------------------------------------------------------ health --

#: key, job_name, scheduler id, sport, role, stale rule, expected-registration rule.
#: Stale rule: minutes since the last success (about 4x the cadence), a ("cron", hours, minute) pair for cron
#: jobs (stale only when a scheduled fire has passed without a success), or None (event-gated: never stale).
JOBS: tuple[tuple[str, str, str, str, str, Any, Callable[[Any], bool]], ...] = (
    ("import", "consume-model-exchange-research-artifacts", "asp-im-model-exchange-consumer", "ALL", "IMPORT", 60,
     lambda s: bool(getattr(s, "model_exchange_auto_import_enabled", False)
                    or getattr(s, "mlb_v18_exchange_auto_import_enabled", False))),
    ("mlb-prices", "collect-odds-checkpoint", "asp-odds-checkpoint", "MLB", "PRICES", 20,
     lambda s: bool(getattr(s, "odds_checkpoint_collection_enabled", False)) and _core(s) and _owns_odds(s)),
    ("mlb-decisions", "freeze-shadow-experiment", "asp-dc-shadow-experiment-freeze", "MLB", "DECISION", 20,
     lambda s: bool(getattr(s, "live_shadow_enabled", False)) and _core(s)),
    ("mlb-live", "refresh-scoreboard", "asp-scoreboard-refresh", "MLB", "LIVE", 10, lambda s: _core(s)),
    ("settlement", "finalize-games", "asp-finalize-games", "ALL", "RESULTS", 120, lambda s: _core(s)),
    ("nfl-forecasts", "generate-nfl-shadow-forecasts", "asp-nfl-shadow-producer-current", "NFL", "FORECAST",
     ("cron", (5,), 30),
     lambda s: bool(getattr(s, "nfl_shadow_enabled", False) and getattr(s, "nfl_shadow_producer_enabled", False))
     and _core(s)),
    ("nfl-final-forecasts", "generate-nfl-final-pregame-forecasts", "asp-nfl-shadow-producer-final", "NFL",
     "FORECAST", None,
     lambda s: bool(getattr(s, "nfl_shadow_enabled", False) and getattr(s, "nfl_shadow_producer_enabled", False))
     and _core(s)),
    ("nfl-approval", "import-nfl-final-pair", "asp-nf-nfl-final-pair", "NFL", "IMPORT", 10,
     lambda s: bool(getattr(s, "model_exchange_auto_import_enabled", False)
                    or getattr(s, "mlb_v18_exchange_auto_import_enabled", False))),
    ("nfl-prices", "collect-nfl-pregame-odds", "asp-nc-nfl-pregame-odds", "NFL", "PRICES", 60, lambda s: _core(s)),
    ("nfl-decisions", "evaluate-nfl-paper-checkpoint", "asp-dc-nfl-paper-checkpoint", "NFL", "DECISION", 20,
     lambda s: _core(s)),
    ("nfl-live", "collect-nfl-live-scores-espn", "asp-ls-nfl-live-scores-espn", "NFL", "LIVE", 10,
     lambda s: bool(getattr(s, "nfl_espn_live_scores_enabled", True)) and _core(s)),
    ("nfl-results", "ingest-nfl-finals", "asp-nfl-final-ingest", "NFL", "RESULTS", 240,
     lambda s: bool(getattr(s, "nfl_shadow_enabled", False)) and _core(s) and _owns_nfl_finals(s)),
    ("cfb-prices", "collect-cfb-odds-api", "asp-cfb-odds-api", "CFB", "PRICES", 40,
     lambda s: bool(getattr(s, "cfb_production_consumer_enabled", False) and getattr(s, "cfb_odds_api_enabled", False))
     and _core(s)),
    ("cfb-decisions", "evaluate-cfb-paper-checkpoint", "asp-dc-cfb-paper-checkpoint", "CFB", "DECISION", 20,
     lambda s: bool(getattr(s, "cfb_production_consumer_enabled", False)) and _core(s)),
    ("cfb-results", "ingest-cfb-finals", "asp-cfb-final-ingest", "CFB", "RESULTS", 120,
     lambda s: bool(getattr(s, "cfb_production_consumer_enabled", False)) and _core(s)),
    ("nhl-forecasts", "generate-nhl-forecasts", "asp-im-nhl-forecast-generation", "NHL", "FORECAST",
     ("cron", (9, 15), 20), lambda s: bool(getattr(s, "nhl_forecast_generation_enabled", False))),
    ("nhl-prices", "collect-nhl-odds-api", "asp-nhl-odds-api", "NHL", "PRICES", 40,
     lambda s: bool(getattr(s, "nhl_odds_api_enabled", False)) and _core(s)),
    ("nhl-decisions", "evaluate-nhl-paper-checkpoint", "asp-dc-nhl-paper-checkpoint", "NHL", "DECISION", 20,
     lambda s: bool(getattr(s, "nhl_paper_checkpoint_enabled", False)) and _core(s)),
    ("nhl-results", "ingest-nhl-finals-espn", "asp-nhl-finals-espn", "NHL", "RESULTS", 120,
     lambda s: bool(getattr(s, "nhl_espn_results_enabled", False)) and _core(s)),
    ("nba-decisions", "evaluate-nba-paper-checkpoint", "asp-nba-paper-checkpoint", "NBA", "DECISION", 20,
     lambda s: bool(getattr(s, "nba_production_consumer_enabled", False)) and _core(s)),
)
#: How long after a cron fire its success may still be pending (lane queueing after a restart can take ~40 min).
CRON_GRACE = timedelta(minutes=60)
JOB_LOOKBACK = timedelta(days=3)


def _core(settings: Any) -> bool:
    return bool(getattr(settings, "scheduler_core_jobs_enabled", True))


def _owns_odds(settings: Any) -> bool:
    try:
        return bool(settings.owns_odds_collection)
    except Exception:  # noqa: BLE001
        return False


def _owns_nfl_finals(settings: Any) -> bool:
    try:
        return bool(settings.owns_nfl_final_ingest)
    except Exception:  # noqa: BLE001
        return False


def _last_cron_fire(rule: tuple[str, tuple[int, ...], int], now: datetime) -> datetime:
    _kind, hours, minute = rule
    local = now.astimezone(ET)
    fires = []
    for back in (0, 1):
        day = local.date() - timedelta(days=back)
        for hour in hours:
            fire = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET).astimezone(UTC)
            if fire <= now:
                fires.append(fire)
    return max(fires)


def _job_runs(session: Session, names: list[str], now: datetime) -> dict[str, tuple[Any, Any, Any]]:
    """Per job name, in ONE pass over the bounded window: (latest status, latest start, latest success)."""

    from sqlalchemy import case

    from app.models import JobRun, RunStatus

    finished = func.coalesce(JobRun.completed_at, JobRun.started_at)
    ranked = (select(JobRun.job_name.label("job_name"), JobRun.status.label("status"),
                     JobRun.started_at.label("started_at"),
                     func.max(case((JobRun.status == RunStatus.SUCCEEDED, finished), else_=None))
                     .over(partition_by=JobRun.job_name).label("last_success"),
                     func.row_number().over(partition_by=JobRun.job_name,
                                            order_by=(JobRun.started_at.desc().nullslast(),
                                                      JobRun.created_at.desc()))
                     .label("rank"))
              # Only job_name is indexed (migration 0001); the created_at bound keeps the scan to three days.
              .where(JobRun.job_name.in_(names), JobRun.created_at >= now - JOB_LOOKBACK).subquery())
    rows = session.execute(select(ranked.c.job_name, ranked.c.status, ranked.c.started_at, ranked.c.last_success)
                           .where(ranked.c.rank == 1)).all()
    return {name: (status, started, success) for name, status, started, success in rows}


def _health_jobs(ctx: _Ctx) -> list[dict[str, Any]]:
    from app.jobs import AVAILABLE_JOBS

    names = sorted({job[1] for job in JOBS})
    runs = _job_runs(ctx.session, names, ctx.now)
    running = ctx.scheduler_running()
    out = []
    for key, name, scheduler_id, sport, role, stale_rule, expected in JOBS:
        next_run = ctx.next_run(scheduler_id) if running else None
        registered = next_run is not None
        last = runs.get(name)
        last_status = str(getattr(last[0], "value", last[0])).lower() if last else None
        last_started = _aware(last[1]) if last else None
        last_success = _aware(last[2]) if last else None
        stale_after: float | None = None
        stale = False
        if isinstance(stale_rule, (int, float)):
            stale_after = float(stale_rule)
            reference = last_success or None
            stale = reference is None or (ctx.now - reference) > timedelta(minutes=stale_after)
        elif isinstance(stale_rule, tuple):
            fire = _last_cron_fire(stale_rule, ctx.now)
            stale_after = None
            stale = (ctx.now - fire) > CRON_GRACE and (last_success is None or last_success < fire - timedelta(minutes=5))
        if not running or not registered:
            state = "NOT_REGISTERED"
        elif last is None:
            state = "NEVER_RUN"
        elif last_status == "failed":
            state = "FAILED"
        elif last_status in ("running", "queued"):
            state = "STALE" if stale_after is not None and last_started is not None and (
                ctx.now - last_started) > timedelta(minutes=max(stale_after, 60)) else "RUNNING"
        elif stale and stale_rule is not None:
            state = "STALE"
        else:
            state = "OK"
        try:
            expected_now = bool(expected(ctx.settings))
        except Exception:  # noqa: BLE001
            expected_now = False
        out.append({"key": key, "job_name": name, "scheduler_id": scheduler_id,
                    "label": AVAILABLE_JOBS.get(name, name), "sport": sport, "role": role,
                    "registered": registered, "expected": expected_now, "state": state,
                    "last_started_at": _iso(last_started), "last_status": last_status,
                    "last_success_at": _iso(last_success), "next_run_at": _iso(next_run),
                    "stale_after_minutes": stale_after})
    return out


def _nhl_generation(ctx: _Ctx) -> dict[str, Any] | None:
    """The NHL generator's last successful run: its own status and the games it could not forecast."""

    from app.models import JobRun, RunStatus

    row = ctx.session.execute(
        select(JobRun.started_at, JobRun.completed_at, JobRun.result["status"], JobRun.result["excluded"])
        .where(JobRun.job_name == "generate-nhl-forecasts", JobRun.status == RunStatus.SUCCEEDED,
               JobRun.created_at >= ctx.now - JOB_LOOKBACK)
        .order_by(JobRun.started_at.desc().nullslast()).limit(1)).first()
    if row is None:
        return None
    started, completed, status, excluded = row
    if isinstance(status, str) and status.startswith('"'):
        import json

        status = json.loads(status)
    if isinstance(excluded, str):
        import json

        try:
            excluded = json.loads(excluded)
        except ValueError:
            excluded = []
    return {"started_at": _aware(started), "completed_at": _aware(completed or started),
            "status": status, "excluded": excluded if isinstance(excluded, list) else []}


def _health_producers(ctx: _Ctx, games_by_sport: dict[str, list[_Game]], relevant: set[str]) -> list[dict[str, Any]]:
    """Each producer's last publication. A sport with a game on the date or within 48 hours is RELEVANT:
    there a missing producer is MISSING, elsewhere NOT_APPLICABLE."""

    now = ctx.now
    out: list[dict[str, Any]] = []

    def entry(sport: str, name: str, state: str, published: datetime | None, detail: str,
              valid_until: datetime | None = None) -> dict[str, Any]:
        return {"sport": sport, "name": name, "state": state, "last_published_at": _iso(published),
                "age_minutes": _minutes(now - published) if published else None, "valid_until": _iso(valid_until),
                "detail": detail}

    # MLB: the V18 producer's newest slate receipt for the day.
    def mlb() -> dict[str, Any]:
        from app.services import pregame_status

        games = games_by_sport.get("MLB") or []
        receipt = pregame_status.latest_receipt(ctx.day)
        generated = _aware((receipt or {}).get("generatedAt"))
        active = any(g.start - timedelta(minutes=60) <= now < g.closes for g in games)
        if receipt is None:
            if not games:
                return entry("MLB", "Model Studio V18", "NOT_APPLICABLE", None, "No V18 slate receipt for this date.")
            return entry("MLB", "Model Studio V18", "MISSING", None,
                         "No V18 slate receipt for this date yet; the producer checks each game from T-60.")
        age = now - generated if generated else None
        stale = active and age is not None and age > timedelta(minutes=30)
        return entry("MLB", "Model Studio V18", "STALE" if stale else "OK", generated,
                     ("The newest V18 slate receipt is more than 30 minutes old while a game is in its "
                      "T-60..T-15 producer window." if stale else
                      f"Newest V18 slate receipt {_when(generated, now)} ET."))

    # NFL: AISP's own M1 generator (daily CURRENT) and the FINAL pair import.
    def nfl() -> dict[str, Any]:
        job = ctx.jobs.get("nfl-forecasts") or {}
        pair = ctx.jobs.get("nfl-approval") or {}
        published = _aware(job.get("last_success_at"))
        if job.get("state") == "FAILED" and "NFL" in relevant:
            return entry("NFL", "NFL M1 generator", "FAILED", published,
                         f"The NFL M1 generator's last run failed ({_when(job.get('last_started_at'), now)} ET); "
                         + (f"last success {_when(published, now)} ET." if published
                            else "no successful run in the last 3 days."))
        if published is None:
            return entry("NFL", "NFL M1 generator", "MISSING" if "NFL" in relevant else "NOT_APPLICABLE", None,
                         "No successful NFL M1 generation run in the last 3 days.")
        state = "STALE" if job.get("state") == "STALE" else "OK"
        return entry("NFL", "NFL M1 generator", state, published,
                     f"Last CURRENT generation {_when(published, now)} ET; the FINAL pair import last succeeded "
                     f"{_when(pair.get('last_success_at'), now) if pair.get('last_success_at') else 'never (3 days)'}"
                     f"{' ET' if pair.get('last_success_at') else ''}.")

    # CFB: the producer's newest verified discovery manifest and the owner's virtual-policy grant.
    def cfb() -> dict[str, Any]:
        from app.cfb.paper_decision import _grant
        from app.cfb.production_import import newest_manifest

        manifest = newest_manifest(ctx.session)
        grant = ctx.extra.get("cfb_grant")
        if grant is None:
            grant = _grant(ctx.settings)
            ctx.extra["cfb_grant"] = grant
        until = _aware(grant.get("valid_until"))
        published = _aware(manifest.producer_created_utc) if manifest is not None else None
        if until is not None and now >= until:
            return entry("CFB", "CFB Gen1 discovery", "EXPIRED", published,
                         f"The virtual-policy grant expired {_when(until, now)} ET; no CFB position can be funded.",
                         until)
        if manifest is None:
            return entry("CFB", "CFB Gen1 discovery", "MISSING" if "CFB" in relevant else "NOT_APPLICABLE", None,
                         "AISP has verified no CFB discovery manifest.", until)
        from app.models import CfbDiscoveryObservation

        # Each manifest's leases run about 15 minutes; the next manifest renews them. When even the latest
        # lease of the newest recorded manifest lapsed more than one cadence ago, no renewal has arrived and
        # every CFB approval reads EXPIRED_LEASE -- the producer is not OK, however recent its last manifest.
        lease_end = _aware(ctx.session.scalar(select(func.max(CfbDiscoveryObservation.eligibility_valid_until_utc))
                                              .where(CfbDiscoveryObservation.manifest_id == manifest.id)))
        lapsed = lease_end is not None and now - lease_end > CFB_LEASE_RENEWAL_GRACE
        old = published is not None and now - published > CFB_MANIFEST_STALE
        stale = "CFB" in relevant and (lapsed or old)
        detail = f"Newest verified discovery manifest {_when(published, now)} ET"
        if stale:
            detail += (f"; every lease it carries lapsed {_when(lease_end, now)} ET and no renewal has been imported, "
                       "so CFB approvals read lease expired" if lapsed else
                       f" (over {int(CFB_MANIFEST_STALE.total_seconds() // 60)} minutes old; leases lapse about 15 "
                       "minutes after each manifest)")
            importer = ctx.jobs.get("import") or {}
            if importer.get("state") == "RUNNING" and importer.get("last_started_at"):
                detail += (f". The research-exchange import lane is busy (consume-model-exchange running since "
                           f"{_when(importer['last_started_at'], now)} ET); renewals queue behind it")
        detail += (f"; grant valid until {_when(until, now)} ET." if until else "; no grant expiry pinned.")
        return entry("CFB", "CFB Gen1 discovery", "STALE" if stale else "OK", published, detail, until)

    # NHL: AISP's own in-process Gen1 generator.
    def nhl() -> dict[str, Any]:
        generation = ctx.extra.get("nhl_generation")
        job = ctx.jobs.get("nhl-forecasts") or {}
        if generation is None:
            return entry("NHL", "AISP in-process NHL Gen1", "MISSING" if "NHL" in relevant or job.get("expected")
                         else "NOT_APPLICABLE", None, "No successful NHL forecast generation in the last 3 days.")
        published = generation["completed_at"]
        status = generation.get("status")
        if status not in (None, "COMPLETED"):
            return entry("NHL", "AISP in-process NHL Gen1", "FAILED", published,
                         f"The last generation run ended {status}; no forecast was written by it.")
        state = "STALE" if job.get("state") == "STALE" else "OK"
        return entry("NHL", "AISP in-process NHL Gen1", state, published,
                     f"Last generation {_when(published, now)} ET (runs 9:20 AM and 3:20 PM ET).")

    for sport, fn in (("MLB", mlb), ("NFL", nfl), ("CFB", cfb), ("NHL", nhl)):
        try:
            out.append(_isolated(ctx.session, fn))
        except Exception as error:  # noqa: BLE001
            out.append(entry(sport, sport, "MISSING", None, f"The producer state could not be read "
                                                            f"({type(error).__name__})."))
    return out


def _health(ctx: _Ctx, games_by_sport: dict[str, list[_Game]], relevant: set[str]) -> dict[str, Any]:
    from app.jobs import scheduler as jobs_scheduler

    running = ctx.scheduler_running()
    enabled = bool(getattr(ctx.settings, "scheduler_enabled", False))
    scheduler = {"state": "RUNNING" if running else "DISABLED" if not enabled else "NOT_RUNNING",
                 "started_at": _iso(jobs_scheduler.scheduler_started_at()) if running else None}
    jobs = ctx.attempt("health.jobs", lambda: _health_jobs(ctx), default=None)
    if jobs is not None:
        ctx.jobs = {j["key"]: j for j in jobs}
    ctx.extra["nhl_generation"] = ctx.attempt("health.nhl_generation", lambda: _nhl_generation(ctx))
    producers = _health_producers(ctx, games_by_sport, relevant)
    ctx.extra["producer_alerts"] = {p["sport"] for p in producers
                                    if p["state"] in ("STALE", "MISSING", "EXPIRED", "FAILED")}
    issues: list[str] = []
    if not running:
        issues.append("The job scheduler is not running in this server process; no job will fire."
                      if enabled else "The job scheduler is switched off (SCHEDULER_ENABLED=false).")
    if jobs is None:
        issues.append("Job run history could not be read.")
    for job in jobs or []:
        matters = job["sport"] == "ALL" or job["sport"] in relevant
        if running and job["expected"] and not job["registered"]:
            issues.append(f"{job['label']} is switched on but not registered with the scheduler.")
        elif job["state"] == "FAILED" and matters:
            issues.append(f"{job['label']}: last run failed ({_when(job['last_started_at'], ctx.now)} ET).")
        elif job["state"] == "STALE" and matters and job["registered"]:
            issues.append(f"{job['label']}: no successful run since "
                          f"{_when(job['last_success_at'], ctx.now) + ' ET' if job['last_success_at'] else 'at least 3 days'}.")
    for producer in producers:
        if producer["state"] in ("STALE", "MISSING", "EXPIRED", "FAILED") and producer["sport"] in relevant:
            issues.append(f"{producer['sport']} producer {producer['state'].lower()}: {producer['detail']}")
    reviews = ctx.attempt("health.occurrence_review", lambda: _occurrence_reviews(ctx), default=0)
    if reviews:
        issues.append(f"{reviews} MLB paper position(s) settled without occurrence verification in the last "
                      f"{JOB_LOOKBACK.days} days (the game was postponed or finished on another date): review "
                      "each, and void any the owner rules had no action through the governed postponement "
                      "correction.")
    overall = ("DOWN" if enabled and not running else "DEGRADED" if issues else "OK")
    return {"overall": overall, "issues": issues, "scheduler": scheduler, "database": {"state": "OK"},
            "jobs": jobs or [], "producers": producers}


def _occurrence_reviews(ctx: _Ctx) -> int:
    """Recent operating alerts from the MLB postponement rule (one per position, never repeated)."""

    from app.bankroll.occurrence import REVIEW_ALERT_ACTION
    from app.models import AuditEvent

    return int(ctx.session.scalar(select(func.count(AuditEvent.id)).where(
        AuditEvent.action == REVIEW_ALERT_ACTION,
        AuditEvent.occurred_at >= ctx.now - JOB_LOOKBACK,
    )) or 0)


def _sport_job_alert(ctx: _Ctx, sport: str) -> bool:
    """A job this sport depends on is FAILED or STALE, or its producer is not delivering."""

    if sport in (ctx.extra.get("producer_alerts") or set()):
        return True
    return any(j["state"] in ("FAILED", "STALE") and j["registered"] and j["sport"] in (sport, "ALL")
               for j in ctx.jobs.values())


# -------------------------------------------------------- allowance/bankroll --

def _allowance(ctx: _Ctx) -> dict[str, Any]:
    from app.services import shared_daily_allowance as shared

    clock = shared.effective_from(ctx.settings)
    in_force = shared.is_effective(ctx.settings, ctx.now)
    detail = shared.entered_detail(ctx.session, ctx.day)          # never room(): that takes the writers' lock
    entered = float(detail["entered_usd"])
    room = round(max(0.0, shared.SHARED_DAILY_LIMIT_USD - entered), 2) if in_force else None
    return {"state": ("NOT_IN_FORCE" if not in_force else "ROOM" if (room or 0) > 0 else "FULL"),
            "in_force": in_force, "effective_from": _iso(clock), "limit_usd": shared.SHARED_DAILY_LIMIT_USD,
            "entered_usd": round(entered, 2), "room_usd": room, "entries": int(detail["entries"]),
            "ambiguous_entries": int(detail["ambiguous_entries"]), "slate_date": ctx.day.isoformat(),
            "basis": shared.BASIS}


def _bankrolls(ctx: _Ctx) -> list[dict[str, Any]]:
    from app.services.sport_readiness import _account

    out = []
    for sport in BANKROLL_SPORTS:
        try:
            account = _isolated(ctx.session, lambda s=sport: _account(ctx.session, s))
        except Exception as error:  # noqa: BLE001
            out.append({"sport": sport, "label": SPORT_LABELS[sport], "funded": None, "starting_usd": None,
                        "bankroll_usd": None, "available_usd": None, "open_exposure_usd": None, "profit_usd": None,
                        "effective_on": None, "read_state": "READ_FAILED", "error_type": type(error).__name__})
            continue
        if not account.get("funded"):
            out.append({"sport": sport, "label": SPORT_LABELS[sport], "funded": False, "starting_usd": None,
                        "bankroll_usd": None, "available_usd": None, "open_exposure_usd": None, "profit_usd": None,
                        "effective_on": None, "read_state": "OK", "error_type": None})
            continue
        starting = float(account.get("contribution") or 0.0)
        bankroll = float(account.get("realized_bankroll") or starting)
        available = float(account.get("available_capital") or 0.0)
        out.append({"sport": sport, "label": SPORT_LABELS[sport], "funded": True, "starting_usd": round(starting, 2),
                    "bankroll_usd": round(bankroll, 2), "available_usd": round(available, 2),
                    "open_exposure_usd": round(bankroll - available, 2), "profit_usd": round(bankroll - starting, 2),
                    "effective_on": account.get("effective_on"), "read_state": "OK", "error_type": None})
    return out


# ------------------------------------------------------------- common stages --

def _phase(ctx: _Ctx, game: _Game, score: dict[str, Any] | None) -> str:
    if game.status in ("postponed", "canceled", "cancelled"):
        return "VOID"
    nhl = ctx.nhl_results.get(game.id)
    final = (game.status == "final" or bool((score or {}).get("governed_final")) or bool((score or {}).get("is_final"))
             or bool(nhl and (nhl.get("final") or {}).get("official")))
    if final:
        return "FINAL"
    if game.status == "live" or (score or {}).get("is_live") or ctx.now >= game.start:
        return "STARTED"
    if game.opens <= ctx.now <= game.closes:
        return "DECISION_WINDOW"
    return "PREGAME"


def _final_score(ctx: _Ctx, game: _Game, score: dict[str, Any] | None) -> dict[str, Any] | None:
    nhl = ctx.nhl_results.get(game.id)
    if nhl and (nhl.get("final") or {}).get("official"):
        final = nhl["final"]
        return {"away": final.get("away_score"), "home": final.get("home_score"), "source": final.get("source"),
                "label": nhl.get("status_label") or "Final"}
    governed = (score or {}).get("governed_final")
    if governed and governed.get("away_score") is not None:
        return {"away": governed["away_score"], "home": governed["home_score"],
                "source": governed.get("evidence_source") or "governed record", "label": "Final"}
    if score and score.get("available") and score.get("is_final"):
        return {"away": score.get("away_score"), "home": score.get("home_score"),
                "source": (score.get("source") or {}).get("name") or "scoreboard", "label": "Final (scoreboard)"}
    return None


NO_LIVE_FEED = {"CFB": "There is no live College Football score feed; finals arrive from retained evidence.",
                "NHL": "There is no NHL in-game feed; ESPN finals are read every 30 minutes after the game.",
                "NBA": "There is no NBA in-game feed."}
FRESHNESS_MAP = {"CURRENT": "FRESH", "DELAYED": "DELAYED", "UNAVAILABLE": "UNAVAILABLE"}


def _live(ctx: _Ctx, game: _Game, score: dict[str, Any] | None, phase: str) -> dict[str, Any]:
    now = ctx.now
    if ctx.scores is None:
        return _unknown("live", ctx.errors.get("scores"))
    if phase == "VOID":
        return _stage("live", NA, "VOID", game.status.capitalize(), f"The game is {game.status}.")
    final = _final_score(ctx, game, score)
    if score and score.get("available"):
        period = (score.get("period") or {}).get("label")
        fields = {"available": True, "is_live": bool(score.get("is_live")), "is_final": bool(score.get("is_final")),
                  "away_score": score.get("away_score"), "home_score": score.get("home_score"),
                  "period_label": period or score.get("status_detail"),
                  "freshness": FRESHNESS_MAP.get(str((score.get("freshness") or {}).get("state"))),
                  "last_checked_at": _iso((score.get("source") or {}).get("last_checked_at"))}
        line = f"{game.teams['away']['abbr']} {score.get('away_score')}–{score.get('home_score')} {game.teams['home']['abbr']}"
        if score.get("is_final") or final:
            if final and not score.get("is_final"):
                fields.update(is_final=True, away_score=final["away"], home_score=final["home"])
                line = f"{game.teams['away']['abbr']} {final['away']}–{final['home']} {game.teams['home']['abbr']}"
            return _stage("live", DONE, "FINAL", f"Final {fields['away_score']}–{fields['home_score']}",
                          f"{line}, final.", at=(score.get("source") or {}).get("last_checked_at"), **fields)
        if score.get("is_live"):
            detail = (score.get("freshness") or {}).get("detail") or ""
            return _stage("live", WAITING, "IN_PROGRESS",
                          f"{score.get('away_score')}–{score.get('home_score')}" + (f" · {period}" if period else ""),
                          _sentence(f"{line}" + (f" ({score.get('status_detail')})" if score.get("status_detail") else "")
                                    + (f". {detail}" if detail else "")), **fields)
        if now < game.start:
            return _stage("live", WAITING, "NOT_STARTED", f"Starts {_when(game.start, now)}",
                          f"{KICK_WORD[game.sport]} {_when(game.start, now)} ET.", due_at=game.start, **fields)
        status = str(score.get("status") or "").upper()
        if status in ("POSTPONED", "CANCELLED", "CANCELED"):
            return _stage("live", NA, status, status.capitalize(), f"The score feed reports the game {status.lower()}.",
                          **fields)
        return _stage("live", WAITING, "AWAITING_FIRST_SCORE", "Awaiting live score",
                      f"Past the scheduled start; the feed still reads {status.lower() or 'not started'}.", **fields)
    if final:
        return _stage("live", DONE, "FINAL", f"Final {final['away']}–{final['home']}",
                      f"{game.teams['away']['abbr']} {final['away']}–{final['home']} {game.teams['home']['abbr']}, "
                      f"{final['label'].lower()} ({final['source']}).", available=False, is_final=True,
                      away_score=final["away"], home_score=final["home"], period_label=final["label"])
    if game.sport in NO_LIVE_FEED:
        return _stage("live", NA, "NO_LIVE_FEED", "No live feed", NO_LIVE_FEED[game.sport])
    if now < game.start:
        return _stage("live", WAITING, "NOT_STARTED", f"Starts {_when(game.start, now)}",
                      f"{KICK_WORD[game.sport]} {_when(game.start, now)} ET.", due_at=game.start)
    if now - game.start <= NO_SCORE_GRACE:
        return _stage("live", WAITING, "NO_SCORE_YET", "Awaiting live score",
                      "Past the start; no live score has been collected for this game yet.")
    return _stage("live", BLOCKED, "NO_SCORE_COLLECTED", "No live score",
                  "No live score has been collected for this game; the scoreboard shows nothing to trust.")


def _forecast_missing(item: dict[str, Any]) -> bool:
    text = " ".join(str(r) for r in item.get("plain_reasons") or []).lower()
    return "forecast" in text and ("unavailable" in text or "no valid" in text or "missing" in text)


def _retry_reasons(sport: str) -> frozenset[str]:
    """The reason codes a sport's own checkpoint re-evaluates inside the window (its PREREQUISITE_REASONS).

    Read from each owner so the two can never disagree. MLB and NBA record no such retry here: empty.
    """

    try:
        if sport == "NFL":
            from app.services.nfl_paper_decision import PREREQUISITE_REASONS
        elif sport == "CFB":
            from app.cfb.paper_decision import PREREQUISITE_REASONS
        elif sport == "NHL":
            from app.nhl.paper import PREREQUISITE_REASONS
        else:
            return frozenset()
    except Exception:  # noqa: BLE001 -- a missing owner means no retry is promised
        return frozenset()
    return frozenset(PREREQUISITE_REASONS)


def _checkpoint_minutes(ctx: Any, sport: str) -> int:
    """How often the sport's decision checkpoint ticks: every 5 minutes, except CFB's
    (CFB_PAPER_CHECKPOINT_INTERVAL_MINUTES, 2 by default since 2026-10-17)."""

    if sport == "CFB":
        try:
            return int(getattr(getattr(ctx, "settings", None), "cfb_paper_checkpoint_interval_minutes", 2))
        except (TypeError, ValueError):
            return 2
    return 5


def _decision_class(item: dict[str, Any], code: str | None, game: _Game, now: datetime) -> str:
    from app.cfb.paper_decision import checkpoint_semantics

    if item.get("position"):
        return "BET"
    if item.get("funding_pause"):
        return "BLOCKED"
    stored = str(item.get("stored_recommendation") or item.get("recommendation") or "").upper()
    presented = str(item.get("recommendation") or "").upper()
    closed = now > game.closes
    if code in BLOCKING_REASONS:
        return "BLOCKED"
    if code in PASS_REASONS:
        return "PASS"
    if stored == "NO_BET" and _forecast_missing(item):
        return "BLOCKED"                       # MLB records "no forecast" as a NO_BET sentence, not a code
    kind = checkpoint_semantics(stored, code)["class"]
    if kind == "BLOCKED":
        return "BLOCKED"
    if kind == "QUALIFIED_NOT_ACTIVATED":
        return "NOT_ACTIVATED"
    if kind == "NO_PRICE":
        return "NO_PRICE"
    if kind == "NO_BET" and str(item.get("phase")) == "FINAL_PREGAME":
        return "PASS"
    if str(item.get("phase")) != "FINAL_PREGAME":
        return "NOT_RECORDED" if closed else "PENDING"
    if presented == "NOT_YET_EVALUATED":
        return "NO_PRICE" if closed else "PENDING"
    if stored == "NO_BET":
        return "PASS"
    if stored in ("QUALIFIED_BET", "QUALIFIED"):
        return "BLOCKED"                       # qualified, yet no position exists
    return "NOT_RECORDED" if closed else "PENDING"


def _owner_active(ctx: _Ctx, sport: str) -> bool:
    s = ctx.settings
    if sport in ("MLB", "NFL"):
        return sport in (ctx.active_policies or set())
    if sport == "CFB":
        return bool(getattr(s, "cfb_production_consumer_enabled", False) and getattr(s, "cfb_paper_decisions_enabled", False))
    if sport == "NHL":
        return bool(ctx.extra.get("nhl_era")) and bool(getattr(s, "nhl_paper_checkpoint_enabled", False)
                                                      and getattr(s, "nhl_paper_decisions_enabled", False))
    if sport == "NBA":
        return bool(getattr(s, "nba_production_consumer_enabled", False) and getattr(s, "nba_paper_decisions_enabled", False))
    return False


def _decision(ctx: _Ctx, game: _Game, item: dict[str, Any] | None, phase: str) -> dict[str, Any]:
    now = ctx.now
    if ctx.decisions is None:
        return _unknown("decision", ctx.errors.get("decisions"))
    if item is None:
        if phase == "VOID":
            return _stage("decision", NA, "VOID", "No decision", f"The game is {game.status}; nothing is decided.")
        if not _owner_active(ctx, game.sport):
            return _stage("decision", BLOCKED, "OWNER_INACTIVE", "Checkpoint not active",
                          f"The {SPORT_LABELS[game.sport]} paper checkpoint is not active, so no decision will be "
                          "recorded.", **{"class": "NOT_ACTIVATED"})
        if now < game.opens:
            return _stage("decision", WAITING, "WINDOW_NOT_OPEN", f"Decides {_when(game.opens, now)}",
                          f"The decision window opens {_when(game.opens, now)} ET and closes {_clock(game.closes)} ET.",
                          due_at=game.opens, **{"class": "PENDING"})
        if now <= game.closes:
            return _stage("decision", WAITING, "WINDOW_OPEN", "Deciding now",
                          f"The decision window is open until {_clock(game.closes)} ET; the checkpoint runs every "
                          f"{_checkpoint_minutes(ctx, game.sport)} minutes.", due_at=game.closes,
                          **{"class": "PENDING"})
        return _stage("decision", BLOCKED, "NOT_RECORDED", "Not recorded",
                      f"No decision was recorded in the {_clock(game.opens)}–{_clock(game.closes)} ET window.",
                      **{"class": "NOT_RECORDED"})
    code = ctx.reasons.get(str(item.get("decision_id")))
    klass = _decision_class(item, code, game, now)
    recorded = _aware(item.get("recommended_at"))
    common = {"class": klass, "recommendation": item.get("recommendation"), "reason_code": code,
              "phase": item.get("phase"), "recorded_at": _iso(recorded), "decision_id": item.get("decision_id"),
              "edge": _num(item.get("edge")), "model_probability": _num(item.get("model_probability")),
              "market_probability": _num(item.get("market_probability"))}
    restriction = item.get("funding_restriction") or {}
    if restriction.get("record_only") and not item.get("position"):
        return _stage("decision", DONE, restriction["reason"], "Record only",
                      "Original value decision and prices retained; owner funding restriction created no position.",
                      **{**common, "class": "RECORD_ONLY", "reason_code": restriction["reason"],
                         "funding_restriction": restriction})
    plain = "; ".join(item.get("plain_reasons") or [])
    reason = str(item.get("reason") or "").strip()
    why = plain or (reason if reason and " " in reason else _plain(code) or _plain(reason))
    if ((klass in ("NO_PRICE", "BLOCKED") or code in NO_ROOM_REASONS) and now <= game.closes
            and not item.get("position") and not item.get("funding_pause") and code in _retry_reasons(game.sport)):
        # A missing prerequisite recorded inside (or before) the window is not final: the sport's checkpoint
        # re-evaluates every few minutes until T-15 and records a new decision once the input arrives. So is
        # shared-$100 room held for MLB/NHL games: it frees as they decide.
        due = game.opens if now < game.opens else game.closes
        return _stage("decision", WAITING, code, "Re-evaluating" if now >= game.opens else f"Decides {_when(game.opens, now)}",
                      _sentence(f"At {_when(recorded, now)} ET the checkpoint recorded: {why or _plain(code)}; it "
                                f"re-evaluates every {_checkpoint_minutes(ctx, game.sport)} minutes until "
                                f"{_clock(game.closes)} ET (T-15)"),
                      due_at=due, **{**common, "class": "PENDING"})
    if klass == "BET":
        pos = item.get("position") or {}
        carried = item.get("carried_from_slate")
        return _stage("decision", DONE, code or "QUALIFIED_BET", "Bet",
                      _sentence(f"Qualified at {_when(recorded, now)} ET: {item.get('selection')} "
                                f"{_american_text(item.get('american'))} at {item.get('sportsbook') or 'the book'}"
                                + (f", stake ${pos.get('stake'):.2f}" if pos.get("stake") is not None else "")
                                + (f" (decided on the {date.fromisoformat(carried):%b} {date.fromisoformat(carried).day} "
                                   "slate, before the game moved to this day)" if carried else "")),
                      at=recorded, **common)
    if klass == "PASS" and code in NO_ROOM_REASONS:
        return _stage("decision", DONE, code, "No room",
                      _sentence(f"Evaluated at {_when(recorded, now)} ET: {(why or _plain(code)).rstrip('. ')}; "
                                "declined for budget, not a no-bet"),
                      at=recorded, **common)
    if klass == "PASS":
        return _stage("decision", DONE, code or "NO_BET", "Passed",
                      _sentence(f"Passed at {_when(recorded, now)} ET: {why or 'no side qualified'}"),
                      at=recorded, **common)
    if klass == "NO_PRICE":
        return _stage("decision", DONE, code or "NO_PRICE", "No price",
                      _sentence(f"Evaluated at {_when(recorded, now)} ET without a usable price: "
                                f"{why or 'no two-sided quote was held'}"), at=recorded, **common)
    if klass == "NOT_ACTIVATED":
        return _stage("decision", BLOCKED, code or "ACTIVATION_CONTROL_OFF", "Activation off",
                      _sentence(f"Every gate passed at {_when(recorded, now)} ET but the activation control is off; "
                                "no position was recorded"), **common)
    if klass == "BLOCKED":
        if item.get("funding_pause"):
            return _stage("decision", BLOCKED, "MLB_V18_FUNDING_PAUSE_V1", "Funding paused",
                          "Qualified, but the MLB V18 funding pause (MLB_V18_FUNDING_PAUSE_V1) blocked the stake.",
                          **common)
        stored = str(item.get("stored_recommendation") or "").upper()
        if stored in ("QUALIFIED_BET", "QUALIFIED") and code not in BLOCKING_REASONS:
            return _stage("decision", BLOCKED, code or "QUALIFIED_WITHOUT_POSITION", "Qualified, no position",
                          _sentence(f"Qualified at {_when(recorded, now)} ET but no position exists"
                                    + (f": {why}" if why else "")), **common)
        code = code or ("NO_FORECAST" if _forecast_missing(item) else None)
        common["reason_code"] = code
        return _stage("decision", BLOCKED, code or "BLOCKED", f"Blocked: {_plain(code) or 'prerequisite'}",
                      _sentence(f"Not evaluated at {_when(recorded, now)} ET: {why or _plain(code) or 'a prerequisite was missing'}"),
                      **common)
    if klass == "PENDING":
        due = game.opens if now < game.opens else game.closes
        return _stage("decision", WAITING, code or str(item.get("phase") or "PENDING"),
                      f"Decides {_when(game.opens, now)}" if now < game.opens else "Deciding now",
                      _sentence(f"Latest evaluation is {str(item.get('phase') or '').replace('_', ' ').lower()} "
                                f"({_when(recorded, now)} ET); the final-pregame decision is due "
                                f"{_clock(game.opens)}–{_clock(game.closes)} ET"), due_at=due, **common)
    return _stage("decision", BLOCKED, "NOT_RECORDED", "Not recorded",
                  _sentence(f"Only a {str(item.get('phase') or 'non-final').replace('_', ' ').lower()} evaluation was "
                            "recorded; no final-pregame decision exists"), **common)


def _bet(ctx: _Ctx, game: _Game, item: dict[str, Any] | None, decision: dict[str, Any], phase: str
         ) -> dict[str, Any]:
    now = ctx.now
    if decision["state"] == UNKNOWN:
        return _unknown("bet", ctx.errors.get("decisions"))
    pos_board = (item or {}).get("position")
    if pos_board:
        detail = ctx.positions.get(str(item.get("decision_id"))) or {}
        american = item.get("american")
        if american is None:
            american = _decimal_to_american(detail.get("decimal_odds"))
        decimal_odds = detail.get("decimal_odds") or _american_to_decimal(american)
        stake = _num(pos_board.get("stake"))
        to_win = round(stake * (decimal_odds - 1), 2) if stake is not None and decimal_odds else None
        status = str(pos_board.get("status") or "").upper() or None
        if status not in ("OPEN", "SETTLED", "VOID"):
            status = "SETTLED" if pos_board.get("profit_loss") is not None else status
        placed = _aware(detail.get("placed_at"))
        return _stage("bet", DONE, "POSITION_RECORDED",
                      f"{item.get('selection')} {_american_text(american)} · ${stake:.2f}" if stake is not None
                      else f"{item.get('selection')} {_american_text(american)}",
                      _sentence(f"{item.get('selection')} {_american_text(american)} at {item.get('sportsbook') or 'the book'}"
                                + (f", ${stake:.2f} to win ${to_win:.2f}" if stake is not None and to_win is not None else "")
                                + (f", placed {_when(placed, now)} ET" if placed else "")),
                      at=placed, position_id=pos_board.get("position_id"), side=item.get("selection"),
                      american=american, sportsbook=item.get("sportsbook"), stake_usd=stake, to_win_usd=to_win,
                      placed_at=_iso(placed), status=status, display_status=pos_board.get("display_status"))
    restriction = (item or {}).get("funding_restriction") or {}
    if restriction.get("record_only"):
        return _stage("bet", NA, restriction["reason"], "Record only",
                      "No stake: owner-approved funding restriction; this is not an economic value pass.",
                      funding_restriction=restriction)
    klass = decision.get("class")
    if phase == "VOID":
        return _stage("bet", NA, "VOID", "No bet", f"The game is {game.status}.")
    if (item or {}).get("funding_pause"):
        return _stage("bet", BLOCKED, "MLB_V18_FUNDING_PAUSE_V1", "Funding paused",
                      "Funding paused (MLB_V18_FUNDING_PAUSE_V1): the qualified V18 decision was recorded without a stake.")
    if klass == "PASS" and decision.get("reason_code") in NO_ROOM_REASONS:
        return _stage("bet", NA, "NO_BET", "No bet", "No bet: the shared $100 had no room for it.")
    if klass in ("PASS", "NO_PRICE"):
        return _stage("bet", NA, "NO_BET", "No bet", "No bet: the decision was a pass." if klass == "PASS"
                      else "No bet: the decision had no usable price.")
    if klass in ("BLOCKED", "NOT_ACTIVATED", "NOT_RECORDED"):
        return _stage("bet", NA, "NO_BET", "No bet", "No bet: the decision did not qualify a position.")
    if now > game.closes:
        return _stage("bet", NA, "NO_BET", "No bet", "No position was recorded before the decision cutoff.")
    return _stage("bet", WAITING, "AWAITING_DECISION", f"Bets by {_clock(game.closes)}",
                  f"A position can only be recorded by the checkpoint, {_clock(game.opens)}–{_clock(game.closes)} ET.",
                  due_at=game.closes)


#: This long after the start a game with no LIVE score must be over, so an open bet awaits its source result
#: (no longer "in play") and a row without a bet has nothing left to do. Sports with no in-game feed (NHL,
#: CFB, NBA) always reach it; MLB and NFL reach it only while their live feed shows nothing live (a dark
#: feed, as for the MLB postseason) -- a feed that still reads live keeps the game in play.
RESULT_EXPECTED_AFTER = {"NHL": timedelta(hours=2, minutes=30), "CFB": timedelta(hours=4, minutes=30),
                         "NBA": timedelta(hours=3), "MLB": timedelta(hours=4, minutes=30),
                         "NFL": timedelta(hours=4, minutes=30)}
RESULT_SOURCE = {"NHL": "ESPN final", "CFB": "retained College Football final", "NBA": "NBA final",
                 "MLB": "MLB final", "NFL": "NFL final"}


def _must_be_over(game: _Game, now: datetime, score: dict[str, Any] | None) -> bool:
    expected = RESULT_EXPECTED_AFTER.get(game.sport)
    return expected is not None and now >= game.start + expected and not (score or {}).get("is_live")


def _settle_due(ctx: _Ctx, game: _Game) -> datetime | None:
    if game.sport == "NHL":
        from app.nhl.espn_results import SETTLE_AFTER_START

        earliest = game.start + SETTLE_AFTER_START
        upcoming = ctx.next_run("asp-nhl-finals-espn")
        return max(earliest, upcoming) if upcoming and ctx.now >= earliest else earliest
    return ctx.next_run("asp-finalize-games")


def _result(ctx: _Ctx, game: _Game, item: dict[str, Any] | None, bet: dict[str, Any], decision: dict[str, Any],
            score: dict[str, Any] | None, phase: str) -> dict[str, Any]:
    now = ctx.now
    if bet["state"] == UNKNOWN:
        return _unknown("result", ctx.errors.get("decisions"))
    final = _final_score(ctx, game, score)
    pos_board = (item or {}).get("position")
    if pos_board:
        detail = ctx.positions.get(str(item.get("decision_id"))) or {}
        pnl = _num(pos_board.get("profit_loss"))
        outcome = str(pos_board.get("result") or detail.get("result") or "").upper() or None
        if outcome and outcome.startswith("WIN"):
            outcome = "WIN"
        elif outcome and outcome.startswith("LOSS"):
            outcome = "LOSS"
        if pnl is not None or str(pos_board.get("status")).upper() in ("SETTLED", "VOID"):
            settled = _aware(detail.get("settled_at"))
            letter = {"WIN": "W", "LOSS": "L", "PUSH": "Push", "VOID": "Void"}.get(outcome or "", outcome or "Settled")
            return _stage("result", DONE, outcome or "SETTLED", f"{letter} {_money(pnl)}".strip(),
                          _sentence(f"Settled {outcome.lower() if outcome else ''} {_money(pnl)}"
                                    + (f" ({final['label']} {final['away']}–{final['home']})" if final else "")),
                          at=settled, outcome=outcome if outcome in ("WIN", "LOSS", "PUSH", "VOID") else None,
                          profit_usd=pnl, final=final, awaiting_settlement=False, settled_at=_iso(settled))
        if phase == "VOID":
            due = _settle_due(ctx, game)
            return _stage("result", WAITING, "AWAITING_VOID_SETTLEMENT", "Voiding",
                          _sentence(f"The game is {game.status}; the settlement owner voids the stake"
                                    + (f" (next run {_when(due, now)} ET)" if due else "")),
                          due_at=due, awaiting_settlement=True)
        if phase == "FINAL" or pos_board.get("awaiting_settlement"):
            due = _settle_due(ctx, game)
            return _stage("result", WAITING, "AWAITING_SETTLEMENT", "Settling",
                          _sentence("Final; the settlement owner has not settled this position yet"
                                    + (f" (next run {_when(due, now)} ET)" if due else "")),
                          due_at=due, final=final, awaiting_settlement=True)
        if phase == "STARTED":
            if _must_be_over(game, now, score):
                # No live score long past the start: the game is over, so say what is awaited, not "in play".
                due = _settle_due(ctx, game)
                return _stage("result", WAITING, "AWAITING_SOURCE_RESULT", "Awaiting result",
                              (f"Played; AISP is waiting for the {RESULT_SOURCE[game.sport]} before settling."
                               if game.sport in NO_LIVE_FEED else
                               f"Long past the start with no live score; AISP is waiting for the "
                               f"{RESULT_SOURCE[game.sport]} before settling."),
                              due_at=due, awaiting_settlement=True)
            return _stage("result", WAITING, "IN_PLAY", "In play", "The bet is riding; the game is in progress.")
        return _stage("result", WAITING, "NOT_STARTED", "After the game",
                      f"Settles after the game ({KICK_WORD[game.sport].lower()} {_when(game.start, now)} ET).")
    if phase == "VOID":
        return _stage("result", NA, "VOID", game.status.capitalize(), f"The game is {game.status}; nothing to settle.")
    if decision.get("class") in ("PASS", "NO_PRICE", "BLOCKED", "NOT_ACTIVATED", "NOT_RECORDED") or now > game.closes:
        text = f"No bet · Final {final['away']}–{final['home']}" if final else "No bet"
        return _stage("result", NA, "NO_POSITION", text,
                      "No position, so nothing to settle." + (f" {final['label']} {final['away']}–{final['home']}."
                                                              if final else ""), final=final)
    return _stage("result", WAITING, "NOT_PLAYED", "After the game",
                  "A result exists only for a bet, after the game.", final=final)


def _prices_from_decision(game: _Game, item: dict[str, Any] | None, decision: dict[str, Any],
                          prices: dict[str, Any], now: datetime) -> dict[str, Any]:
    """After the window, the PRICES chip reports what the DECISION priced against -- not a quote's age today."""

    if item is None or now <= game.closes:
        return prices
    klass = decision.get("class")
    integrity = item.get("market_integrity") or {}
    if klass in ("BET", "PASS", "NOT_ACTIVATED") and item.get("american") is not None:
        age = integrity.get("older_leg_age_minutes_at_decision")
        side = str(item.get("selection") or "")
        return {**prices, "state": DONE, "code": "PRICED_AT_DECISION",
                "label": _label(f"{item.get('sportsbook') or 'Book'} {_american_text(item.get('american'))}"),
                "detail": _sentence(f"Priced at the decision: {side} {_american_text(item.get('american'))} at "
                                    f"{item.get('sportsbook') or 'the book'}"
                                    + (f", {age:.0f} min old on its older leg" if isinstance(age, (int, float)) else "")),
                "due_at": None, "at": _iso(item.get("quote_observed_at") or item.get("recommended_at")),
                "quote_age_minutes": age if isinstance(age, (int, float)) else prices.get("quote_age_minutes")}
    if klass == "NO_PRICE":
        code = decision.get("reason_code") or "NO_TWO_SIDED_QUOTE"
        return {**prices, "state": BLOCKED, "code": code, "label": _label("No usable price"),
                "detail": _sentence(f"At the decision {_plain(code)}"), "due_at": None, "at": None}
    return prices


def _next_step(ctx: _Ctx, game: _Game, stages: dict[str, dict[str, Any]], phase: str,
               score: dict[str, Any] | None) -> dict[str, Any]:
    now = ctx.now
    bet, result, decision, live = stages["bet"], stages["result"], stages["decision"], stages["live"]
    has_bet = bet.get("position_id") is not None
    word = KICK_WORD[game.sport]

    def step(stage: str, kind: str, at: Any, label: str | None, text: str) -> dict[str, Any]:
        return {"stage": stage, "kind": kind, "at": _iso(at), "label": label, "text": text}

    if has_bet and result["state"] == DONE:
        return step("DONE", "DONE", None, None, result.get("label") or "Settled")
    if has_bet and result.get("awaiting_settlement"):
        return step("RESULT", "WAIT", result.get("due_at"), "Settlement",
                    f"Settlement {('due ' + _when(result['due_at'], now) + ' ET') if result.get('due_at') else 'pending'}")
    if phase == "VOID":
        return step("DONE", "DONE", None, None, f"Game {game.status}")
    if phase == "STARTED" or (score or {}).get("is_live"):
        if has_bet:
            return step("LIVE", "WATCH", None, "Live", f"Sweating {bet.get('side')} · {live.get('label') or 'in play'}")
        if _must_be_over(game, now, score):
            # No live score and no bet: the game is over as far as AISP can tell, and nothing is left to do.
            return step("DONE", "DONE", None, None, f"No bet · {decision.get('label') or 'no decision'}")
        return step("LIVE", "WATCH", None, "Live", f"In play · no bet ({decision.get('label') or 'no decision'})")
    if phase == "FINAL":
        return step("DONE", "DONE", None, None,
                    "Passed" if decision.get("class") == "PASS" else
                    f"No bet · {decision.get('label') or 'no decision'}")
    if has_bet:
        return step("BET", "WAIT", game.start, word, f"Bet placed: {bet.get('label')}; {word.lower()} "
                                                     f"{_when(game.start, now)} ET")
    pre = [(name, stages[name]) for name in PRE_BET]
    for index, (name, stage) in enumerate(pre):
        if stage["state"] == BLOCKED:
            return step(STAGE_WORD[name], "BLOCKED", stage.get("due_at"), stage.get("label"),
                        stage.get("detail") or stage.get("label") or "Blocked")
        if stage["state"] in (WAITING, UNKNOWN):
            due = stage.get("due_at") or next((later.get("due_at") for _n, later in pre[index + 1:]
                                               if later.get("due_at")), None)
            if stage["state"] == UNKNOWN:
                return step(STAGE_WORD[name], "BLOCKED", None, "Read failed", stage.get("detail") or "Read failed")
            return step(STAGE_WORD[name], "WAIT", due, DEADLINE_LABEL.get(str(stage.get("code"))) or WAIT_LABEL[name],
                        stage.get("detail") or stage.get("label") or "Waiting")
    if decision.get("class") == "PASS":
        return step("DONE", "DONE", None, None, "Passed")
    if decision.get("class") == "NO_PRICE":
        return step("DONE", "DONE", None, None, "No bet · no price")
    return step("LIVE", "WAIT", game.start, word, f"{word} {_when(game.start, now)} ET")


def _attention(ctx: _Ctx, game: _Game, stages: dict[str, dict[str, Any]]) -> str:
    if stages["decision"].get("class") == "NOT_RECORDED":
        return "BLOCKED"
    # A blocker before the decision matters while it can still stop one; once the decision is recorded
    # (bet, priced pass, or a final no-price), the game has been decided and nothing upstream can change it.
    if (ctx.now <= game.closes and stages["decision"]["state"] != DONE
            and any(stages[name]["state"] == BLOCKED for name in PRE_BET)):
        return "BLOCKED"
    if stages["forecast"].get("code") == "OVERDUE" or stages["prices"].get("code") == "EXPIRES_BEFORE_WINDOW":
        return "WATCH"
    # A failing job or producer of the sport puts a game on watch while it can still affect it: up to its
    # decision cutoff, or while it holds a bet that is not settled yet. A finished game is not re-flagged.
    open_bet = bool(stages["bet"].get("position_id")) and stages["result"]["state"] != DONE
    if (ctx.now <= game.closes or open_bet) and _sport_job_alert(ctx, game.sport):
        return "WATCH"
    return "NONE"


# ----------------------------------------------------------- sport adapters --
# Each returns {game_id: {"forecast", "approval", "prices", "clocks"}} for its games.

class CoverageUnavailable(Exception):
    """The shared market-coverage read failed, so coverage-based stages cannot be computed."""


def _coverage_row(ctx: _Ctx, game: _Game) -> dict[str, Any] | None:
    if ctx.coverage is None:
        raise CoverageUnavailable(ctx.errors.get("coverage"))
    return ctx.coverage.get(game.id)


def _best_from_coverage(cov: dict[str, Any]) -> dict[str, Any]:
    best = cov.get("best") or {}
    return {"home": best.get("HOME"), "away": best.get("AWAY")}


def _at_cutoff(game: _Game, cov: dict[str, Any], permitted: list[str]) -> dict[str, Any]:
    """The policy's freshness rule evaluated AT the decision cutoff, from the stored coverage pairs."""

    from app.services import pregame_status
    from app.services.command_center import _provider_label

    moneylines = [q for q in cov.get("quotes") or [] if q.get("market") == "MONEYLINE"
                  and all((((q.get("sides") or {}).get(s) or {}).get("decimal") or 0) > 1 for s in ("HOME", "AWAY"))
                  and (_aware(q.get("observed_at")) or game.closes) <= game.closes]
    labels = {_provider_label(source) for source in permitted}
    return pregame_status.market_status(moneylines, labels, now=game.closes, opens=game.opens, closes=game.closes)


def _coverage_prices(ctx: _Ctx, game: _Game, cov: dict[str, Any], *, buy_opens: datetime | None,
                     buy_closes: datetime | None, collector: str, permitted: list[str],
                     cause: Callable[[], dict[str, Any] | None] | None = None) -> dict[str, Any]:
    """MLB/NFL/NBA prices from stored coverage (``pregame_status.market_status``): the policy's own freshness rule.

    ``cause`` (MLB): the quote-coverage service's reading of this game -- its state, exact cause, the
    cause's explanation and the next action with its clock.
    """

    now = ctx.now
    status = cov.get("market_status") or {}
    state = status.get("state")
    freshest = _aware(cov.get("freshest_observed_at"))
    fields = {"best": _best_from_coverage(cov), "books": int(cov.get("books") or 0),
              "fresh_books": int(cov.get("fresh_books") or 0),
              "quote_age_minutes": _minutes(now - freshest) if freshest else None,
              "fresh_until": status.get("fresh_until"), "consensus_home": cov.get("consensus_no_vig_home"),
              "collector": collector, "permitted_sources": permitted}
    best = fields["best"]
    top = best.get("home") or best.get("away") or {}
    book_label = f"{top.get('sportsbook') or 'Book'} {_american_text(top.get('american'))}" if top else "Priced"
    if now > game.closes and cov.get("market_status"):
        # Past the cutoff, a quote's age today says nothing; what matters is whether a pair was fresh AT it.
        then = _at_cutoff(game, cov, permitted)
        if then.get("state") == "WINDOW_CLOSED":
            return _stage("prices", DONE, "PRICED_AT_CUTOFF", f"{then.get('books_fresh_now')} book(s) at cutoff",
                          _sentence(f"{then.get('books_fresh_now')} same-book pair(s) were fresh at the "
                                    f"{_clock(game.closes)} ET cutoff"), at=then.get("newest_leg_observed_at"),
                          **fields)
        reading = cause() if cause else None
        code = (reading or {}).get("cause") or ("QUOTE_TOO_STALE" if fields["books"] else "NO_TWO_SIDED_QUOTE")
        why = (reading or {}).get("why")
        return _stage("prices", BLOCKED, code, "No usable price",
                      _sentence(why or f"No same-book pair was fresh at the {_clock(game.closes)} ET cutoff"
                                + (f" ({fields['books']} stored book(s))" if fields["books"] else
                                   " (no stored two-sided quote)")), **fields)
    if state in ("ELIGIBLE_THROUGH_WINDOW", "EXPIRES_DURING_WINDOW", "WINDOW_CLOSED"):
        detail = (f"{fields['fresh_books']} fresh two-sided book(s); fresh until {_when(status.get('fresh_until'), now)} ET"
                  + (" (expires inside the decision window)" if state == "EXPIRES_DURING_WINDOW" else ""))
        return _stage("prices", DONE, state, book_label, _sentence(detail),
                      at=status.get("newest_leg_observed_at"), **fields)
    if state == "EXPIRES_BEFORE_WINDOW" and buy_opens is not None and now < buy_opens:
        # Normal operation: the held price lapses before the window and the scheduled buy replaces it.
        return _stage("prices", WAITING, "BUY_SCHEDULED", f"Buys {_when(buy_opens, now)}",
                      _sentence(f"A price is held ({book_label}) but expires {_when(status.get('fresh_until'), now)} ET, "
                                f"before the {_clock(game.opens)} ET window; the next buy is {_when(buy_opens, now)} ET"),
                      due_at=buy_opens, **fields)
    if state == "EXPIRES_BEFORE_WINDOW":
        return _stage("prices", WAITING, "EXPIRES_BEFORE_WINDOW", "Expires before window",
                      _sentence(f"The fresh price expires {_when(status.get('fresh_until'), now)} ET, before the "
                                f"{_clock(game.opens)} ET window; a new buy is needed"
                                + (f" ({_when(buy_opens, now)} ET)" if buy_opens and buy_opens > now else "")),
                      due_at=buy_opens if buy_opens and buy_opens > now else game.opens, **fields)
    if state is None and not cov.get("market_status"):
        return _stage("prices", UNKNOWN, "NO_MARKET_STATUS", "Unknown",
                      "The stored market coverage carried no price state for this game.", **fields)
    stored = (f"{fields['books']} stored book(s), none fresh" if fields["books"] else "no stored two-sided quote")
    if now > game.closes:
        return _stage("prices", BLOCKED, state or "NO_TWO_SIDED_QUOTE", "No usable price",
                      _sentence(f"No usable two-sided price by the {_clock(game.closes)} ET cutoff ({stored})"),
                      **fields)
    if collector == "OFF":
        return _stage("prices", BLOCKED, "COLLECTOR_OFF", "Collector off",
                      _sentence(f"The price collector is switched off and {stored}"), **fields)
    if buy_opens is not None and now < buy_opens:
        return _stage("prices", WAITING, "BUY_SCHEDULED", f"Buys {_when(buy_opens, now)}",
                      _sentence(f"The price buy is scheduled for {_when(buy_opens, now)} ET ({stored} now)"),
                      due_at=buy_opens, **fields)
    if cause is not None:
        reading = cause() or {}
        at = reading.get("at")
        if reading.get("cause") and at is not None and at > now:
            return _stage("prices", WAITING, reading["cause"], "Recovering price",
                          _sentence(f"{reading.get('action') or 'Next attempt'} at {_when(at, now)} ET; so far: "
                                    f"{reading.get('why') or _plain(reading['cause'])}"), due_at=at, **fields)
        if reading.get("cause") and reading.get("why"):
            return _stage("prices", BLOCKED, reading["cause"], "Price missed", _sentence(reading["why"]), **fields)
        if at is not None and at > now:
            return _stage("prices", WAITING, "PRICE_EXPECTED", "Price pending",
                          _sentence(f"{reading.get('action') or 'A price is expected'} by {_when(at, now)} ET "
                                    f"({stored} now)"), due_at=at, **fields)
    if buy_closes is not None and now <= buy_closes:
        return _stage("prices", WAITING, "BUYING_NOW", "Buying now",
                      _sentence(f"Inside the buy window until {_clock(buy_closes)} ET ({stored} now)"),
                      due_at=buy_closes, **fields)
    if buy_closes is not None:
        return _stage("prices", BLOCKED, "NO_TWO_SIDED_QUOTE" if not fields["books"] else "QUOTE_TOO_STALE",
                      "No usable price", _sentence(f"The buy window closed at {_clock(buy_closes)} ET and {stored}"),
                      **fields)
    return _stage("prices", WAITING, state or "NO_TWO_SIDED_QUOTE", "Price pending",
                  _sentence(f"{stored.capitalize()}; the decision needs a fresh pair by {_clock(game.closes)} ET"),
                  due_at=game.closes, **fields)


# MLB ------------------------------------------------------------------------

def _mlb(ctx: _Ctx, games: list[_Game]) -> dict[str, dict[str, Any]]:
    from app.services import odds_collection_plan as plan
    from app.services.command_center import PERMITTED_SOURCES

    now, settings = ctx.now, ctx.settings
    starts = [g.start for g in games if (g.source_mode or "LIVE") == "LIVE" and g.status in ("scheduled", "live", "final")]
    checkpoints = plan.plan_checkpoints(starts) if starts else []
    collector = ("ON" if bool(getattr(settings, "odds_checkpoint_collection_enabled", False)) else "OFF")
    paused = bool(getattr(settings, "mlb_v18_funding_pause_enabled", False))
    coverage_cache: dict[str, Any] = {}

    def quote_cause(game: _Game) -> dict[str, Any] | None:
        if "rows" not in coverage_cache:
            from app.services.quote_coverage import QuoteCoverageService

            coverage_cache["rows"] = {str(e.game_id): e for e in QuoteCoverageService(ctx.session, settings).coverage(
                league_code="MLB", slate_date=ctx.day, now=now)}
        from app.services.quote_coverage import CAUSE_EXPLANATIONS

        event = coverage_cache["rows"].get(game.id)
        if event is None:
            return None
        return {"state": event.state, "cause": event.cause,
                "why": CAUSE_EXPLANATIONS.get(event.cause) if event.cause else None,
                "action": event.next_action, "at": _aware(event.next_action_at)}

    out: dict[str, dict[str, Any]] = {}
    for game in games:
        cov = _coverage_row(ctx, game) or {}
        cp = next((c for c in checkpoints if plan.planned_covers(c, plan.decision_cutoff(game.start))), None)
        target = game.start - MLB_PUBLICATION_TARGET
        # FORECAST
        model, status = cov.get("model"), cov.get("model_status") or {}
        state = status.get("state")
        producer = status.get("producer") or {}
        said = "; ".join(t for t in (producer.get("blocker_text") or []) if t) or producer.get("reason_text")
        if model:
            forecast = _forecast_done(game, now, made_at=model.get("created_at"), basis="AISP_IMPORT_TRANSACTION_START",
                                      source="Model Studio V18", engine_id=model.get("engine_id"),
                                      forecast_id=model.get("forecast_id"), home_p=_num(model.get("home")),
                                      away_p=_num(model.get("away")))
        elif state in ("SCHEDULED", "WAITING_ON_INPUTS"):
            forecast = _stage("forecast", WAITING, state, f"Due {_when(target, now)}",
                              _sentence(f"V18 publishes about T-45 ({_when(target, now)} ET), a target rather than a "
                                        "guarantee" + (f"; latest producer status: {said}" if said else "")),
                              due_at=target, source="Model Studio V18", engine_id="MLB_V18_WHOLE_GAME")
        elif state == "PUBLISHED_NOT_IMPORTED":
            due = ctx.next_run("asp-im-model-exchange-consumer")
            forecast = _stage("forecast", WAITING, state, "Importing",
                              "Published by the producer; AISP imports it on the next exchange run"
                              + (f" ({_when(due, now)} ET)." if due else "."), due_at=due,
                              source="Model Studio V18", engine_id="MLB_V18_WHOLE_GAME")
        elif state == "OVERDUE":
            forecast = _stage("forecast", WAITING, "OVERDUE", "Overdue",
                              _sentence(f"Past the T-45 target ({_clock(target)} ET) with no V18 forecast"
                                        + (f"; latest producer status: {said}" if said else "")),
                              due_at=game.closes, source="Model Studio V18", engine_id="MLB_V18_WHOLE_GAME")
        elif state == "MISSED":
            forecast = _stage("forecast", BLOCKED, "MISSED", "Forecast missed",
                              _sentence("No V18 forecast arrived before T-15"
                                        + (f"; latest producer status: {said}" if said else "")),
                              source="Model Studio V18", engine_id="MLB_V18_WHOLE_GAME")
        else:
            forecast = _stage("forecast", UNKNOWN, "NO_MODEL_STATUS", "Unknown",
                              "The stored coverage carried no V18 model state for this game.")
        # APPROVAL
        if "MLB" in (ctx.active_policies or set()):
            approval = _stage("approval", DONE, "ACTIVE_POLICY", "Policy active",
                              "The MLB practical pregame policy is active"
                              + ("; the V18 funding pause is ON, so a qualified V18 decision is recorded without a "
                                 "stake." if paused else "."), route="MLB_ACTIVE_POLICY", eligible=True,
                              funding_pause_enabled=paused)
        else:
            approval = _stage("approval", BLOCKED, "POLICY_INACTIVE", "No active policy",
                              "No MLB paper policy is active, so nothing evaluates this game.",
                              route="MLB_ACTIVE_POLICY", eligible=False, funding_pause_enabled=paused)
        prices = _coverage_prices(ctx, game, cov, buy_opens=cp, buy_closes=None, collector=collector,
                                  permitted=list(PERMITTED_SOURCES), cause=lambda g=game: quote_cause(g))
        out[game.id] = {"forecast": forecast, "approval": approval, "prices": prices,
                        "clocks": {"forecast_due_at": _iso(target), "buy_opens_at": _iso(cp), "buy_closes_at": None}}
    return out


# NFL ------------------------------------------------------------------------

def _nfl(ctx: _Ctx, games: list[_Game]) -> dict[str, dict[str, Any]]:
    from app.models import ShadowForecast
    from app.services import nfl_odds_api_fallback, nfl_paper_eligibility as eligibility, sgo_nfl_pregame
    from app.services.command_center import PERMITTED_SOURCES

    now, settings = ctx.now, ctx.settings
    rows = ctx.session.execute(
        select(ShadowForecast.canonical_game_id, ShadowForecast.id, ShadowForecast.forecast_id, ShadowForecast.event_id,
               ShadowForecast.snapshot_type, ShadowForecast.snapshot_version, ShadowForecast.available_as_of_utc,
               ShadowForecast.created_at, ShadowForecast.engine_id, ShadowForecast.full_home_win_probability,
               ShadowForecast.full_away_win_probability)
        .where(ShadowForecast.canonical_game_id.in_([g.uuid for g in games]),
               ShadowForecast.engine_id == "NFL_M1_XGBOOST")).all()
    by_game: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        by_game[str(row[0])].append(row)

    def newest(candidates: list[Any]) -> Any:
        # nfl_paper_decision._forecast orders available_as_of_utc DESC (PostgreSQL: NULLS FIRST).
        return max(candidates, key=lambda r: (r[6] is None, _aware(r[6]) or datetime.min.replace(tzinfo=UTC)))

    retimed = sgo_nfl_pregame.retime_enabled(settings)
    running = ctx.scheduler_running()
    registered = ctx.next_run("asp-nc-nfl-pregame-odds") is not None
    collector = "ON" if registered else ("OFF" if running else "UNKNOWN")
    fallback = nfl_odds_api_fallback.enabled(settings)
    current_next = ctx.next_run("asp-nfl-shadow-producer-current")
    out: dict[str, dict[str, Any]] = {}
    from app.services.nfl_paper_decision import operational_start,paired_quotes,clock_bound_pairs
    from copy import copy
    for original_game in games:
        game=copy(original_game)
        game.start=operational_start(ctx.session,game.uuid,moment=now)
        cov = _coverage_row(ctx, game) or {}
        candidates = by_game.get(game.id) or []
        finals = [r for r in candidates if r[4] == "FINAL_PREGAME"]
        chosen = newest(finals) if finals else (newest(candidates) if candidates else None)
        generation_opens = game.start - NFL_FINAL_GENERATION_OPENS
        if retimed:
            opens, _late = sgo_nfl_pregame.retimed_checkpoint_window(game.start)
            _retry_open, closes = sgo_nfl_pregame.retimed_retry_window(game.start)
        else:
            opens, closes = sgo_nfl_pregame.checkpoint_window(game.start)
        # FORECAST
        if chosen is None:
            due = current_next if current_next and current_next < game.closes else generation_opens
            forecast = (_stage("forecast", BLOCKED, "NO_FORECAST", "No forecast",
                               "No NFL M1 forecast exists for this game and the T-15 cutoff has passed.",
                               source="NFL M1", engine_id="NFL_M1_XGBOOST") if now > game.closes else
                        _stage("forecast", WAITING, "NO_FORECAST", f"Due {_when(due, now)}",
                               f"No NFL M1 forecast yet; the next generation is due {_when(due, now)} ET.",
                               due_at=due, source="NFL M1", engine_id="NFL_M1_XGBOOST"))
        else:
            _gid, _rid, fid, _event, snap, version, available, created, engine, home_p, away_p = chosen
            made, basis = (available, "PRODUCER_PUBLISHED") if available is not None else (
                created, "AISP_IMPORT_TRANSACTION_START")
            kwargs = dict(made_at=made, basis=basis, source="NFL M1", engine_id=engine, forecast_id=fid,
                          home_p=_num(home_p), away_p=_num(away_p), snapshot=snap)
            if snap == "FINAL_PREGAME" and available is not None and _aware(available) <= game.closes:
                forecast = _forecast_done(game, now, code="FINAL_PREGAME", **kwargs)
            elif snap == "FINAL_PREGAME":
                forecast = _forecast_done(game, now, state=BLOCKED, code="FINAL_AFTER_CUTOFF",
                                          label="Final arrived late",
                                          detail="The FINAL_PREGAME forecast became available after T-15; it cannot "
                                                 "control a position.", **kwargs)
            elif now > game.closes:
                forecast = _forecast_done(game, now, state=BLOCKED, code="NO_FINAL_PREGAME_FORECAST",
                                          label="No final forecast",
                                          detail=f"Only a {snap} v{version} forecast existed by T-15; a CURRENT "
                                                 "snapshot never controls a position.", **kwargs)
            else:
                due = generation_opens if now < generation_opens else game.opens
                forecast = _forecast_done(game, now, state=WAITING, code="CURRENT_PROVISIONAL",
                                          label="Provisional (CURRENT)", due_at=due,
                                          detail=f"{snap} v{version} is provisional; the FINAL_PREGAME forecast that "
                                                 f"controls the decision is generated from {_clock(generation_opens)} ET "
                                                 "(T-60).", **kwargs)
        # APPROVAL
        if chosen is None:
            approval = (_stage("approval", BLOCKED, "NO_FORECAST", "No forecast",
                               "Nothing to approve: no forecast exists.", route="NFL_PRODUCER_ELIGIBILITY",
                               eligible=False) if now >= game.opens else
                        _stage("approval", WAITING, "NO_FORECAST", "Needs a forecast",
                               "Eligibility binds an exact forecast; none exists yet.", due_at=game.opens,
                               route="NFL_PRODUCER_ELIGIBILITY", eligible=False))
        else:
            fid, event_id = chosen[2], chosen[3]
            verdict = eligibility.check(ctx.session, event_id=event_id, forecast_id=fid,
                                        experiment_id="mission-control", now=now)
            verdict = eligibility.successor_gap(ctx.session, event_id=event_id, forecast_id=fid, binding=verdict)
            reason = verdict.get("reason")
            if verdict.get("eligible"):
                approval = _stage("approval", DONE, "ELIGIBLE", "Eligible",
                                  f"The producer's eligibility record binds forecast {fid}.",
                                  at=None, route="NFL_PRODUCER_ELIGIBILITY", eligible=True)
            elif reason in eligibility.MISSING_PERMISSION_REASONS and now < game.opens:
                approval = _stage("approval", WAITING, reason, "Awaiting eligibility",
                                  _sentence(f"{_plain(reason)}; it must arrive before the {_clock(game.opens)} ET window"),
                                  due_at=game.opens, route="NFL_PRODUCER_ELIGIBILITY", eligible=False)
            else:
                approval = _stage("approval", BLOCKED, reason or "NOT_ELIGIBLE",
                                  "No eligibility" if reason in eligibility.MISSING_PERMISSION_REASONS else "Not eligible",
                                  _sentence(_plain(reason) or "the eligibility record did not qualify"),
                                  route="NFL_PRODUCER_ELIGIBILITY", eligible=False)
        asof=min(now,game.closes)
        native_pairs=paired_quotes(ctx.session,game.uuid,moment=asof)
        native_pairs,clock_refusals=clock_bound_pairs(ctx.session,game.uuid,native_pairs,moment=asof)
        prices = _pair_prices(ctx,game,native_pairs,sources=list(PERMITTED_SOURCES),collector_on=registered or fallback,
                              buy_opens=opens,buy_closes=closes,base={"clock_refusals":clock_refusals})
        prices["fallback_odds_api"] = fallback
        out[game.id] = {"forecast": forecast, "approval": approval, "prices": prices,
                        "clocks": {"operational_start":_iso(game.start), "forecast_due_at": _iso(generation_opens), "buy_opens_at": _iso(opens),
                                   "buy_closes_at": _iso(closes)}}
    return out


# Pair prices (CFB, NHL) ------------------------------------------------------

def _book_names(ctx: _Ctx) -> dict[str, str]:
    """Sportsbook key -> display name (the coverage rows already carry names; native pairs carry keys)."""

    if "book_names" not in ctx.extra:
        from app.models import Sportsbook

        ctx.extra["book_names"] = {k: n for k, n in ctx.session.execute(select(Sportsbook.key, Sportsbook.name)).all()}
    return ctx.extra["book_names"]


def _pair_observed(pair: dict[str, Any]) -> datetime:
    return _aware(pair["pair_observed_at"])  # type: ignore[return-value]


def _pair_prices(ctx: _Ctx, game: _Game, pairs: list[dict[str, Any]], *, sources: list[str], collector_on: bool,
                 buy_opens: datetime, buy_closes: datetime, base: dict[str, Any] | None = None,
                 requested: list[datetime] | None = None, capable: bool = True) -> dict[str, Any]:
    """Same-book two-sided pairs (older leg governs, 240 minutes) for CFB and NHL, judged for the WINDOW.

    Past the cutoff the pairs are judged at the cutoff (what the checkpoint could hold). Before the window,
    a pair that is fresh NOW but expires before the window opens is not "priced": the collector buys again
    in its own T-75..T-35 window, so the stage waits for that buy -- or, with no buy ahead, is flagged.
    """

    now = ctx.now
    moment = min(now, game.closes)
    max_age = timedelta(minutes=QUOTE_FRESHNESS_MINUTES)
    permitted = [p for p in pairs if p["source"] in sources]
    held = [p for p in permitted if _pair_observed(p) <= moment]
    fresh = [p for p in held if moment - _pair_observed(p) <= max_age]
    names = _book_names(ctx)
    best: dict[str, Any] = {"home": None, "away": None}
    for side in ("home", "away"):
        top = max(fresh, key=lambda p: _american_to_decimal(p[f"{side}_american"]) or 0, default=None)
        if top is not None:
            best[side] = {"american": top[f"{side}_american"],
                          "sportsbook": names.get(top["sportsbook"], top["sportsbook"]),
                          "provider": "The Odds API" if top["source"] == "THE_ODDS_API" else top["source"],
                          "observed_at": _iso(top.get(f"{side}_observed_at"))}
    newest = max((_pair_observed(p) for p in held), default=None)
    no_vig = []
    for p in fresh:
        h, a = _american_to_decimal(p["home_american"]), _american_to_decimal(p["away_american"])
        if h and a:
            no_vig.append((1 / h) / ((1 / h) + (1 / a)))
    fields = {**(base or {}), "best": best, "books": len({p["sportsbook"] for p in pairs}),
              "fresh_books": len({p["sportsbook"] for p in fresh}),
              "quote_age_minutes": _minutes(now - newest) if newest else None,
              "fresh_until": _iso(newest + max_age) if newest else None,
              "consensus_home": round(sum(no_vig) / len(no_vig), 4) if no_vig else None,
              "collector": "ON" if collector_on else "OFF", "permitted_sources": list(sources)}
    asked = sorted(r for r in (requested or []) if r is not None and r >= buy_opens)
    asked_text = (f"requested {_when(asked[-1], now)} ET" if asked else "no request yet") if requested is not None else ""
    rebuy = collector_on and now < buy_closes
    if fresh:
        until = max(_pair_observed(p) for p in fresh) + max_age
        top = best["home"] or best["away"] or {}
        label = f"{top.get('sportsbook')} {_american_text(top.get('american'))}"
        if now >= game.opens or until >= game.closes:
            return _stage("prices", DONE, "PRICE_HELD", label,
                          _sentence(f"{len(fresh)} fresh same-book pair(s); newest pair {_when(newest, now)} ET"
                                    + ("" if now >= game.opens else f", fresh until {_when(until, now)} ET")),
                          at=newest, **fields)
        if rebuy:
            due = buy_opens if now < buy_opens else buy_closes
            return _stage("prices", WAITING, "BUY_SCHEDULED" if now < buy_opens else "BUYING_NOW",
                          f"Buys {_when(buy_opens, now)}" if now < buy_opens else "Buying now",
                          _sentence(f"A price is held ({label}) but it expires {_when(until, now)} ET, before the "
                                    f"{_clock(game.opens)} ET window; the collector buys again "
                                    f"{_clock(buy_opens)}–{_clock(buy_closes)} ET"
                                    + ("" if capable else " (it buys only decision-capable games)")),
                          due_at=due, **fields)
        return _stage("prices", WAITING, "EXPIRES_BEFORE_WINDOW", "Expires before window",
                      _sentence(f"The held price ({label}) expires {_when(until, now)} ET, before the "
                                f"{_clock(game.opens)} ET window, and no further buy is scheduled"),
                      due_at=until, **fields)
    if now > buy_closes:
        code = "QUOTE_TOO_STALE" if held else "NO_PERMITTED_PROVIDER" if pairs else "NO_TWO_SIDED_QUOTE"
        why = {"QUOTE_TOO_STALE": "every held pair was older than 240 minutes on its older leg",
               "NO_PERMITTED_PROVIDER": "the stored pairs come only from providers the policy does not permit",
               "NO_TWO_SIDED_QUOTE": "no same-book two-sided pair was stored"}[code]
        tail = (f" ({asked_text})" if asked_text and collector_on else
                "; the collector is switched off" if not collector_on else "")
        return _stage("prices", BLOCKED, code, "No usable price",
                      _sentence(f"The {_clock(buy_opens)}–{_clock(buy_closes)} ET buy window closed: {why}{tail}"),
                      **fields)
    if not collector_on:
        return _stage("prices", BLOCKED, "COLLECTOR_OFF", "Collector off",
                      "The price collector for this sport is switched off, so no price will be bought.", **fields)
    if now < buy_opens:
        return _stage("prices", WAITING, "BUY_SCHEDULED", f"Buys {_when(buy_opens, now)}",
                      _sentence(f"The Odds API buy window opens {_when(buy_opens, now)} ET (T-75)"
                                + ("" if capable else "; the collector buys only decision-capable games")),
                      due_at=buy_opens, **fields)
    return _stage("prices", WAITING, "BUYING_NOW", "Buying now",
                  _sentence(f"Inside the buy window until {_clock(buy_closes)} ET"
                            + (f"; {asked_text}" + (", no pair bound yet" if asked else "") if asked_text else "")),
                  due_at=buy_closes, **fields)


# CFB ------------------------------------------------------------------------

LEASE_LABELS = {"EXPIRED_LEASE": "Lease expired", "HISTORICAL": "Not in current discovery",
                "REJECTED": "Discovery rejected", "NOT_IN_CURRENT_DISCOVERY": "Not in current discovery",
                "NO_VERIFIED_DISCOVERY": "No discovery", "NO_DISCOVERY": "No discovery"}


def _cfb_bound_pairs(ctx: _Ctx, games: list[_Game]) -> dict[str, list[dict[str, Any]]]:
    """``cfb.paper_decision.kickoff_bound_pairs`` for every game in ONE statement (same rule, same order).

    Odds API moneyline pairs whose BOTH legs retained exactly the game's current kickoff; one provider, one
    book, one market row; newest snapshot per side; the older leg governs. Only the two raw fields the rule
    reads are selected (the full raw payloads are what made the per-game reads slow).
    """

    from app.cfb.paper_decision import KICKOFF_BOUND_SOURCES, utc_instant
    from app.models import Market, MarketOutcome, OddsSnapshot, Sportsbook

    kickoffs = {g.uuid: g.start for g in games}
    rows = ctx.session.execute(
        select(Market.game_id, Market.id, Sportsbook.key, OddsSnapshot.source, MarketOutcome.outcome_key,
               OddsSnapshot.observed_at, OddsSnapshot.american_odds,
               OddsSnapshot.raw_payload["event_commence_time"].as_string())
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id.in_(list(kickoffs)), Market.market_type == "MONEYLINE",
               OddsSnapshot.source.in_(sorted(KICKOFF_BOUND_SOURCES)))
        .order_by(OddsSnapshot.observed_at.desc(), OddsSnapshot.available_as_of.desc())).all()
    latest: dict[Any, dict[tuple, dict[str, Any]]] = defaultdict(dict)
    for game_id, market_id, book, source, outcome_key, observed_at, american, commence in rows:
        if utc_instant(commence) != kickoffs.get(game_id):
            continue
        side = str(outcome_key).split(":")[0].upper()
        if side not in ("HOME", "AWAY") or american is None:
            continue
        key = (market_id, book, source, side)
        if key not in latest[game_id]:
            latest[game_id][key] = {"observed_at": utc_instant(observed_at), "american_odds": int(american)}
    out: dict[str, list[dict[str, Any]]] = {}
    for game_id, legs in latest.items():
        pairs, seen = [], set()
        for market_id, book, source, _side in legs:
            if (market_id, book, source) in seen:
                continue
            seen.add((market_id, book, source))
            away, home = legs.get((market_id, book, source, "AWAY")), legs.get((market_id, book, source, "HOME"))
            if away is None or home is None:
                continue
            older = min(away["observed_at"], home["observed_at"])
            pairs.append({"market_id": str(market_id), "sportsbook": book, "source": source,
                          "away_american": away["american_odds"], "home_american": home["american_odds"],
                          "away_observed_at": away["observed_at"].isoformat(),
                          "home_observed_at": home["observed_at"].isoformat(),
                          "pair_observed_at": older.isoformat()})
        pairs.sort(key=lambda pair: pair["pair_observed_at"], reverse=True)
        out[str(game_id)] = pairs
    return out


def _eligibility_records(ctx: _Ctx, policy: Any) -> tuple[Any, dict[str, datetime | None]]:
    """The policy's eligibility records as the gate's own ``EligibilityIndex`` (read and parsed ONCE per build).

    ``research_paper_policy.eligibility_status(index=...)`` then decides each game with the gate's own
    binding, flag and configuration checks -- nothing is re-implemented here. The second value maps each
    record id to its publication time (the T-30 rule reads it).
    """

    from app.services.research_paper_policy import EligibilityIndex

    index = EligibilityIndex(ctx.session, policy)
    published: dict[str, datetime | None] = {}
    for pairs in index.by_forecast.values():
        for record, _doc in pairs:
            published[str(record.id)] = _aware(getattr(record, "published_utc", None))
    return index, published


def _cfb(ctx: _Ctx, games: list[_Game]) -> dict[str, dict[str, Any]]:
    from app.cfb.odds_api_price import BUY_CLOSES as CFB_BUY_CLOSES, BUY_OPENS as CFB_BUY_OPENS
    from app.cfb.paper_decision import _grant
    from app.services.research_paper_policy import CFB, effective_quote_sources, eligibility_status

    now, settings = ctx.now, ctx.settings
    owner = bool(getattr(settings, "cfb_production_consumer_enabled", False))
    decisions_on = owner and bool(getattr(settings, "cfb_paper_decisions_enabled", False))
    grant = ctx.extra.get("cfb_grant")
    if grant is None:
        grant = _grant(settings)
        ctx.extra["cfb_grant"] = grant
    grant_until = _aware(grant.get("valid_until"))
    sources = list(effective_quote_sources(CFB, settings, moment=now)[0])
    collector_on = bool(getattr(settings, "cfb_odds_api_enabled", False))
    records: tuple[Any, dict[str, datetime | None]] | None = None
    pairs = _cfb_bound_pairs(ctx, games) if "THE_ODDS_API" in sources else {}
    out: dict[str, dict[str, Any]] = {}
    for game in games:
        cov = _coverage_row(ctx, game) or {}
        model, status = cov.get("model"), cov.get("model_status") or {}
        buy_opens, buy_closes = game.start - CFB_BUY_OPENS, game.start - CFB_BUY_CLOSES
        # FORECAST
        if model:
            home2 = _num(model.get("home_two_way"))
            home_p = home2 if home2 is not None else _num(model.get("home"))
            away_p = (1 - home2) if home2 is not None else _num(model.get("away"))
            forecast = _forecast_done(game, now, made_at=model.get("created_at"), basis="PRODUCER_PUBLISHED",
                                      source="CFB Gen1 joint", engine_id=model.get("engine_id"),
                                      forecast_id=model.get("forecast_id"), home_p=home_p, away_p=away_p)
        elif now > game.closes:
            forecast = _stage("forecast", BLOCKED, "NO_FORECAST", "No forecast",
                              "No CFB forecast was imported for this game by T-15.", source="CFB Gen1 joint")
        else:
            forecast = _stage("forecast", WAITING, "NO_FORECAST", "Awaiting producer",
                              "No CFB forecast imported yet; the producer publishes CFB forecasts through the "
                              "research exchange (no fixed clock).", source="CFB Gen1 joint")
        # APPROVAL: the gates of cfb.paper_decision.decide, in order (owner, lease, record, grant, T-30).
        reading = status.get("eligibility") or status.get("eligibility_now") or {}
        lease_state = reading.get("state")
        lease_until = _aware(reading.get("lease_valid_until"))
        route = {"route": "CFB_LEASE_AND_RECORD"}
        if not decisions_on:
            approval = _stage("approval", BLOCKED, "OWNER_INACTIVE", "Decisions off",
                              "CFB paper decisions are switched off (CFB_PRODUCTION_CONSUMER_ENABLED / "
                              "CFB_PAPER_DECISIONS_ENABLED).", eligible=False, **route)
        elif not model:
            approval = (_stage("approval", BLOCKED, "NO_FORECAST", "No forecast", "Nothing to approve: no forecast.",
                               eligible=False, **route) if now > game.closes else
                        _stage("approval", WAITING, "NO_FORECAST", "Needs a forecast",
                               "Approval binds an exact forecast; none is imported yet.", eligible=False, **route))
        elif lease_state == "EXPIRED_LEASE" and now < game.opens:
            # The producer re-issues every lease with each discovery manifest (about every 15 minutes); the
            # checkpoint needs it current AT the decision, so before the window a lapse is a renewal owed.
            approval = _stage("approval", WAITING, "EXPIRED_LEASE", "Lease renewal due",
                              _sentence(f"The discovery lease lapsed {_when(lease_until, now)} ET; the producer "
                                        "renews leases with each discovery manifest and it must be current at the "
                                        f"{_clock(game.opens)} ET decision"),
                              due_at=game.opens, eligible=False, valid_until=_iso(lease_until), **route)
        elif lease_state != "CURRENT":
            approval = _stage("approval", BLOCKED, lease_state or "NO_DISCOVERY",
                              LEASE_LABELS.get(lease_state or "", "Lease not current"),
                              _sentence("The forecast's discovery lease is "
                                        f"{str(lease_state or 'unknown').replace('_', ' ').lower()}"
                                        + (f" (valid until {_when(lease_until, now)} ET)" if lease_until else "")),
                              eligible=False, valid_until=_iso(lease_until), **route)
        else:
            if records is None:
                records = _eligibility_records(ctx, CFB)
            index, published_at = records
            fid = str(model.get("forecast_id"))
            verdict = eligibility_status(ctx.session, CFB, forecast_id=fid, event_id=f"cfbd:{game.source_game_id}",
                                         index=index)
            published = published_at.get(str(verdict.get("record_id"))) if verdict.get("eligible") else None
            if not verdict.get("eligible"):
                awaiting = verdict.get("state") == "AWAITING_PRODUCER_ELIGIBILITY"
                if awaiting and now < game.opens:
                    approval = _stage("approval", WAITING, "AWAITING_PRODUCER_ELIGIBILITY", "Awaiting record",
                                      f"The producer's eligibility record must be published before T-30 "
                                      f"({_clock(game.opens)} ET).", due_at=game.opens, eligible=False,
                                      valid_until=_iso(lease_until), **route)
                else:
                    approval = _stage("approval", BLOCKED, "AWAITING_PRODUCER_ELIGIBILITY" if awaiting
                                      else "ELIGIBILITY_RECORD_INVALID",
                                      "No eligibility record" if awaiting else "Record invalid",
                                      "No producer eligibility record binds this forecast." if awaiting else
                                      "The producer's eligibility record does not qualify (missing bindings or "
                                      "authority flags).", eligible=False, valid_until=_iso(lease_until), **route)
            elif grant_until is not None and now >= grant_until:
                approval = _stage("approval", BLOCKED, "POLICY_GRANT_EXPIRED", "Grant expired",
                                  f"The owner's virtual-policy grant expired {_when(grant_until, now)} ET.",
                                  eligible=False, valid_until=_iso(grant_until), **route)
            elif published is None or published >= game.opens:
                approval = _stage("approval", BLOCKED, "ELIGIBILITY_RECORD_INVALID", "Record after T-30",
                                  "The producer's eligibility record was not published before T-30.",
                                  eligible=False, valid_until=_iso(lease_until), **route)
            else:
                until = min([t for t in (lease_until, grant_until) if t is not None], default=None)
                approval = _stage("approval", DONE, "ELIGIBLE", "Eligible",
                                  _sentence("Lease current and producer record held (published "
                                            f"{_when(published, now)} ET)"
                                            + (f"; valid until {_when(until, now)} ET" if until else "")),
                                  at=published, eligible=True, valid_until=_iso(until), **route)
        # PRICES
        if "THE_ODDS_API" in sources:
            prices = _pair_prices(ctx, game, pairs.get(game.id, []), sources=sources, collector_on=collector_on,
                                  buy_opens=buy_opens, buy_closes=buy_closes, capable=bool(model))
        else:
            note = cov.get("price_unavailable") or {}
            mstate = (cov.get("market_status") or {}).get("state")
            if mstate in ("ELIGIBLE_THROUGH_WINDOW", "EXPIRES_DURING_WINDOW", "WINDOW_CLOSED"):
                prices = _coverage_prices(ctx, game, cov, buy_opens=None, buy_closes=None,
                                          collector="UNKNOWN", permitted=sources)
            else:
                prices = _stage("prices", BLOCKED, note.get("reason") or mstate or "COLLECTION_BLOCKED",
                                "No price source", _sentence(note.get("detail") or
                                                             "No permitted CFB price collector is active"),
                                best=_best_from_coverage(cov), books=int(cov.get("books") or 0),
                                collector="UNKNOWN", permitted_sources=sources)
        out[game.id] = {"forecast": forecast, "approval": approval, "prices": prices,
                        "clocks": {"forecast_due_at": None, "buy_opens_at": _iso(buy_opens),
                                   "buy_closes_at": _iso(buy_closes)}}
    return out


# NHL ------------------------------------------------------------------------

NHL_EXCLUSION_TEXT = {
    "IN_PROCESS_PUBLISHED_AFTER_T30": "The only forecast was generated after T-30; it cannot control this game.",
    "ORIGINAL_START_IS_NOT_THE_CURRENT_START": "The game moved after its forecast was made (schedule drift); nothing "
                                               "is retimed.",
    "PUBLISHED_AT_OR_AFTER_ORIGINAL_START": "The forecast was published at or after the start.",
    "NOT_AVAILABLE_AT_DECISION": "The forecast was not yet held by AISP.",
    "CLOCK_MISSING": "The forecast is missing a publication or import clock.",
}
NHL_VIOLATION_TEXT = {
    "REST_FEATURES_NOT_COMPLETE_AT_GENERATION": "Rest features were incomplete when the forecast was made "
                                                "(back-to-back); it cannot control this game.",
    "PUBLISHED_AFTER_T30_OF_ORIGINAL_START": "The forecast was generated after T-30.",
    "ORIGINAL_START_IS_NOT_THE_CURRENT_START": "The game moved after its forecast was made.",
    "NOT_HELD_AT_DECISION_MOMENT": "The forecast was not held at this moment.",
    "CLOCK_MISSING": "The forecast is missing a clock.",
}


def _nhl_held_quotes(ctx: _Ctx, games: list[_Game]) -> dict[str, dict[str, Any]]:
    """``nhl.paper._held_quotes`` for every game in ONE pass, in the exact shape ``native_pairs(held=...)`` reads.

    Rows carry only the attributes ``_leg_refusal`` / ``native_pairs`` read; the raw payload is reduced to the
    five keys the leg rule checks. The NHL lifecycle's own pairing and refusal rules then decide.
    """

    from types import SimpleNamespace

    from app.models import GameTeam, Market, MarketOutcome, OddsSnapshot, Sportsbook

    ids = [g.uuid for g in games]
    sides: dict[Any, dict[bool, Any]] = defaultdict(dict)
    for game_id, is_home, team_id in ctx.session.execute(
            select(GameTeam.game_id, GameTeam.is_home, GameTeam.team_id).where(GameTeam.game_id.in_(ids))).all():
        sides[game_id][is_home] = team_id
    raw = OddsSnapshot.raw_payload
    rows = ctx.session.execute(
        select(Market.game_id, Market.id, Market.period, MarketOutcome.id, MarketOutcome.outcome_key,
               MarketOutcome.participant_team_id, OddsSnapshot.id, OddsSnapshot.source, OddsSnapshot.source_event_id,
               OddsSnapshot.observed_at, OddsSnapshot.available_as_of, OddsSnapshot.american_odds,
               raw["event_commence_time"].as_string(), raw["market"].as_string(), raw["sport_key"].as_string(),
               raw["side"].as_string(), raw["provider_event_id"].as_string(), Sportsbook.key)
        .join(MarketOutcome, MarketOutcome.market_id == Market.id)
        .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id.in_(ids), Market.market_type == "MONEYLINE")).all()
    market_ids = {r[1] for r in rows}
    keys_by_market: dict[Any, set[str]] = defaultdict(set)
    if market_ids:
        for market_id, key in ctx.session.execute(select(MarketOutcome.market_id, MarketOutcome.outcome_key)
                                                  .where(MarketOutcome.market_id.in_(market_ids))).all():
            keys_by_market[market_id].add(key)
    out: dict[str, dict[str, Any]] = {str(g.uuid): {"sides": dict(sides.get(g.uuid, {})), "rows": [],
                                                   "keys_by_market": {}} for g in games}
    for (game_id, market_id, period, outcome_id, outcome_key, participant, snap_id, source, event, observed,
         available, american, commence, provider_market, sport_key, side, provider_event, book) in rows:
        payload = {k: v for k, v in (("event_commence_time", commence), ("market", provider_market),
                                     ("sport_key", sport_key), ("side", side),
                                     ("provider_event_id", provider_event)) if v is not None}
        entry = out[str(game_id)]
        entry["rows"].append((SimpleNamespace(id=market_id, period=period),
                              SimpleNamespace(id=outcome_id, outcome_key=outcome_key, participant_team_id=participant),
                              SimpleNamespace(id=snap_id, source=source, source_event_id=event, observed_at=observed,
                                              available_as_of=available, american_odds=american, raw_payload=payload),
                              book))
        entry["keys_by_market"][market_id] = keys_by_market.get(market_id, set())
    return out


def _nhl(ctx: _Ctx, games: list[_Game]) -> dict[str, dict[str, Any]]:
    from app.models import ProviderRequestLedger
    from app.nhl import paper
    from app.nhl.odds_api_price import BUY_CLOSES as NHL_BUY_CLOSES, BUY_OPENS as NHL_BUY_OPENS
    from app.nhl.odds_api_price import ENDPOINT_KEY, PROVIDER, decision_capability
    from app.nhl.read_service import publication
    from app.services import shared_daily_allowance as shared
    from app.services.research_paper_policy import NHL, effective_quote_sources

    now, settings = ctx.now, ctx.settings
    era = ctx.extra.get("nhl_era")
    flags = (bool(getattr(settings, "nhl_paper_checkpoint_enabled", False)),
             bool(getattr(settings, "nhl_paper_decisions_enabled", False)))
    allowance_on = shared.is_effective(settings, now)
    sources = list(effective_quote_sources(NHL, settings, moment=now)[0])
    collector_on = bool(getattr(settings, "nhl_odds_api_enabled", False))
    generator_next = ctx.next_run("asp-im-nhl-forecast-generation")
    earliest_window = min((g.start - NHL_BUY_OPENS for g in games), default=now)
    reservations = [_aware(r) for r in ctx.session.scalars(select(ProviderRequestLedger.requested_at).where(
        ProviderRequestLedger.provider == PROVIDER, ProviderRequestLedger.endpoint_key == ENDPOINT_KEY,
        ProviderRequestLedger.status == "RESERVED", ProviderRequestLedger.requested_at >= earliest_window))]
    held_quotes = _nhl_held_quotes(ctx, games)
    out: dict[str, dict[str, Any]] = {}
    for game in games:
        buy_opens, buy_closes = game.start - NHL_BUY_OPENS, game.start - NHL_BUY_CLOSES
        forecast_row, excluded = paper.pregame_forecast(ctx.session, game.uuid, moment=now, starts_at=game.start)
        in_process = forecast_row is not None and forecast_row.producer_evidence_status == paper.IN_PROCESS_EVIDENCE_STATUS
        source = "AISP in-process NHL Gen1" if in_process or forecast_row is None else "NHL Gen1 (producer)"
        # FORECAST
        due_generation = generator_next if generator_next and generator_next <= game.opens else None
        if forecast_row is not None:
            forecast = _forecast_done(game, now, made_at=publication(forecast_row),
                                      basis="AISP_IN_PROCESS_GENERATED" if in_process else "PRODUCER_PUBLISHED",
                                      source=source, engine_id=forecast_row.model_id,
                                      forecast_id=forecast_row.forecast_id,
                                      home_p=_num(forecast_row.final_home_probability),
                                      away_p=_num(forecast_row.final_away_probability))
        elif excluded and not (due_generation and now < game.opens):
            why = excluded[-1]["why"]
            forecast = _stage("forecast", BLOCKED, why, "Forecast not usable",
                              NHL_EXCLUSION_TEXT.get(why, _sentence(_plain(why))), source=source)
        elif now < game.opens and (due_generation or not ctx.scheduler_running()):
            forecast = _stage("forecast", WAITING, "NO_FORECAST",
                              f"Next run {_when(due_generation, now)}" if due_generation else "Awaiting generator",
                              ("No NHL forecast yet; the in-process generator runs "
                               + (f"{_when(due_generation, now)} ET." if due_generation
                                  else "at 9:20 AM and 3:20 PM ET.")), due_at=due_generation, source=source)
        else:
            forecast = _stage("forecast", BLOCKED, "NO_FORECAST", "No forecast",
                              "No NHL forecast is held and no generator run is due before T-30.", source=source)
        # APPROVAL: the lifecycle's own route verdict, then the owner gates the checkpoint applies.
        route_name = "NHL_IN_PROCESS" if in_process or forecast_row is None else "NHL_PRODUCER"
        capable = False
        if forecast_row is None:
            approval = (_stage("approval", WAITING, "NO_FORECAST", "Needs a forecast",
                               "Approval binds an exact forecast; none is held yet.", due_at=due_generation,
                               route=route_name, eligible=False)
                        if forecast["state"] == WAITING else
                        _stage("approval", BLOCKED, "NO_FORECAST", "No forecast",
                               "Nothing to approve: no usable forecast.", route=route_name, eligible=False))
        else:
            capability = decision_capability(ctx.session, forecast_row, now=now, starts_at=game.start)
            capable = bool(capability["capable"])
            if not capable:
                if in_process:
                    violations = paper.in_process_eligibility(forecast_row, moment=now,
                                                              starts_at=game.start)["violations"]
                    first = str(violations[0]).split(":")[0] if violations else "ELIGIBILITY_RECORD_INVALID"
                    approval = _stage("approval", BLOCKED, first, "Not eligible",
                                      NHL_VIOLATION_TEXT.get(first, _sentence(_plain(first))), route=route_name,
                                      eligible=False)
                else:
                    state = str(capability.get("detail") or "AWAITING_PRODUCER_ELIGIBILITY")
                    approval = (_stage("approval", WAITING, state, "Awaiting record",
                                       "The producer's eligibility record must be held before T-30 "
                                       f"({_clock(game.opens)} ET).", due_at=game.opens, route=route_name,
                                       eligible=False)
                                if state == "AWAITING_PRODUCER_ELIGIBILITY" and now < game.opens else
                                _stage("approval", BLOCKED, state, "Not eligible", _sentence(_plain(state)),
                                       route=route_name, eligible=False))
            elif era is None:
                approval = _stage("approval", BLOCKED, "NHL_PAPER_POLICY_IS_NOT_ACTIVE", "No NHL era",
                                  "No active NHL paper era exists.", route=route_name, eligible=False)
            elif not all(flags):
                approval = _stage("approval", BLOCKED, "ACTIVATION_CONTROL_OFF", "Activation off",
                                  "NHL_PAPER_CHECKPOINT_ENABLED / NHL_PAPER_DECISIONS_ENABLED is off.",
                                  route=route_name, eligible=False)
            elif not allowance_on:
                approval = _stage("approval", BLOCKED, "SHARED_ALLOWANCE_NOT_EFFECTIVE", "Allowance not in force",
                                  "NHL is funded only by the shared $100 daily allowance, which is not in force.",
                                  route=route_name, eligible=False)
            else:
                approval = _stage("approval", DONE, "AISP_IN_PROCESS_ELIGIBLE" if in_process else "ELIGIBLE",
                                  "Eligible", "AISP in-process eligibility holds (frozen Gen1, rest complete, made "
                                  "before T-30)." if in_process else "The producer's eligibility record is held.",
                                  at=publication(forecast_row), route=route_name, eligible=True)
        # PRICES: the lifecycle's own native pairing over the rows held now (judged at the cutoff once past it).
        original = paper._aware(forecast_row.scheduled_start_utc) if forecast_row is not None else game.start
        pairs, _refused = paper.native_pairs(ctx.session, game.uuid, moment=min(now, game.closes),
                                             starts_at=game.start, original_start=original,
                                             held={"quotes": held_quotes[game.id]})
        prices = _pair_prices(ctx, game, pairs, sources=sources, collector_on=collector_on, buy_opens=buy_opens,
                              buy_closes=buy_closes, requested=reservations, capable=capable)
        out[game.id] = {"forecast": forecast, "approval": approval, "prices": prices,
                        "clocks": {"forecast_due_at": _iso(due_generation), "buy_opens_at": _iso(buy_opens),
                                   "buy_closes_at": _iso(buy_closes)}}
    return out


# NBA ------------------------------------------------------------------------

def _nba(ctx: _Ctx, games: list[_Game]) -> dict[str, dict[str, Any]]:
    from app.models import NbaGen1Forecast
    from app.services.research_paper_policy import NBA, eligibility_status, effective_quote_sources
    from app.services.sport_readiness import _nba_odds_api_price
    now, settings = ctx.now, ctx.settings
    sources, _source_amendments = effective_quote_sources(NBA, settings, moment=now)
    owner = bool(getattr(settings, "nba_production_consumer_enabled", False)) and bool(
        getattr(settings, "nba_paper_decisions_enabled", False))
    out: dict[str, dict[str, Any]] = {}
    for game in games:
        cov = _coverage_row(ctx, game) or {}
        model, status = cov.get("model"), cov.get("model_status") or {}
        historical = bool(model) and model.get("evidence_class") == "HISTORICAL"
        if model and not historical:
            forecast = _forecast_done(game, now, made_at=model.get("created_at"), basis="PRODUCER_PUBLISHED",
                                      source="NBA Gen1", engine_id=model.get("engine_id"),
                                      forecast_id=model.get("forecast_id"), home_p=_num(model.get("home")),
                                      away_p=_num(model.get("away")))
        elif historical:
            forecast = _stage("forecast", NA, "HISTORICAL", "Historical only",
                              "Only a 2025-26 historical reconstruction exists; it is never decision-eligible.",
                              source="NBA Gen1")
        else:
            forecast = (_stage("forecast", BLOCKED, "NO_FORECAST", "No forecast", "No NBA production package by T-15.",
                               source="NBA Gen1") if now > game.closes else
                        _stage("forecast", WAITING, "NO_FORECAST", "Awaiting producer",
                               "No NBA production package imported yet.", source="NBA Gen1"))
        record = ctx.session.scalar(select(NbaGen1Forecast).where(
            NbaGen1Forecast.forecast_id == str((model or {}).get("forecast_id")))) if model else None
        paper = eligibility_status(ctx.session, NBA, forecast_id=record.forecast_id,
                                   event_id=record.codex_game_id) if record is not None else {"eligible": False, "state": "NO_FORECAST", "detail": "No current production forecast"}
        reading = status.get("eligibility") or status.get("eligibility_now") or {}
        if not owner:
            approval = _stage("approval", BLOCKED, "OWNER_INACTIVE", "Decisions off",
                              "NBA paper decisions are switched off.", route="NBA_LEASE_AND_RECORD", eligible=False)
        elif reading.get("state") == "CURRENT" and not paper.get("eligible"):
            approval = _stage("approval", BLOCKED, paper.get("state") or "AWAITING_PRODUCER_ELIGIBILITY",
                              "Paper record missing", paper.get("detail") or "The current lease also needs its matching producer paper-eligibility record.",
                              route="NBA_LEASE_AND_RECORD", eligible=False, discovery_lease_state="CURRENT",
                              valid_until=_iso(reading.get("lease_valid_until")))
        elif reading.get("state") == "CURRENT":
            approval = _stage("approval", DONE, "ELIGIBILITY_CURRENT", "Lease current",
                              "The current discovery lease and matching paper-eligibility record are present.", route="NBA_LEASE_AND_RECORD", eligible=True,
                              valid_until=_iso(reading.get("lease_valid_until")))
        else:
            approval = _stage("approval", BLOCKED, reading.get("state") or "NO_DISCOVERY", "Lease not current",
                              "The forecast has no current discovery lease.", route="NBA_LEASE_AND_RECORD",
                              eligible=False)
        mstate = (cov.get("market_status") or {}).get("state")
        if mstate in ("ELIGIBLE_THROUGH_WINDOW", "EXPIRES_DURING_WINDOW", "WINDOW_CLOSED"):
            prices = _coverage_prices(ctx, game, cov, buy_opens=None, buy_closes=None, collector="UNKNOWN",
                                      permitted=["SPORTSGAMEODDS"])
        else:
            note = cov.get("price_unavailable") or {}
            binding = (note.get("binding") or {})
            code = ("SGO_LEAGUE_IDENTITY_UNVERIFIED" if note and not binding.get("resolved") else
                    note.get("reason") or mstate or "NO_TWO_SIDED_QUOTE")
            prices = _stage("prices", BLOCKED, code, "No price source",
                            _sentence(note.get("detail") or "No NBA price is collected"),
                            best=_best_from_coverage(cov), books=int(cov.get("books") or 0),
                            collector="ON" if note.get("lane_enabled") else "OFF", permitted_sources=["SPORTSGAMEODDS"])
        nba_price = _nba_odds_api_price(ctx.session, game.id, now, settings)
        lane, held = nba_price["lane"], nba_price.get("price")
        buy_opens = buy_closes = None
        if lane.get("state") == "ACTIVE" and held is not None:
            collection = held.get("collection") or {}
            buy_opens, buy_closes = collection.get("opens_at"), collection.get("closes_at")
            common_fields = {"collector": "ON", "permitted_sources": list(sources), "odds_api_lane": lane,
                             "books": int(held.get("pairs") or 0), "fresh_books": int(held.get("pairs") or 0)}
            if held.get("state") == "PRICE_HELD":
                prices = _stage("prices", DONE, "PRICE_HELD", "Fresh paired prices", held.get("label"), **common_fields)
            elif held.get("blocker"):
                prices = _stage("prices", BLOCKED, held["blocker"], "No usable price", held.get("label"), **common_fields)
            else:
                prices = _stage("prices", WAITING, "BUY_SCHEDULED", "Scheduled price window", held.get("label"),
                                due_at=buy_opens if _aware(buy_opens) and _aware(buy_opens) > now else buy_closes,
                                **common_fields)
        out[game.id] = {"forecast": forecast, "approval": approval, "prices": prices,
                        "clocks": {"forecast_due_at": None, "buy_opens_at": buy_opens, "buy_closes_at": buy_closes}}
    return out


ADAPTERS: dict[str, Callable[[_Ctx, list[_Game]], dict[str, dict[str, Any]]]] = {
    "MLB": _mlb, "NFL": _nfl, "CFB": _cfb, "NHL": _nhl, "NBA": _nba}


# --------------------------------------------------------------- sweating --

def _sweating(ctx: _Ctx, day_scores: dict[str, dict[str, Any]] | None) -> list[dict[str, Any]]:
    from app.models import (
        ExperimentAccount, ExperimentAccountMembership, Game, Market, MarketOutcome, OddsSnapshot,
        OfficialAspPosition, PositionSettlement, PositionStatus, ShadowExperiment, Sportsbook,
    )
    from app.models.experiment_accounts import MembershipRole

    rows = ctx.session.execute(
        select(OfficialAspPosition.id, OfficialAspPosition.stake, OfficialAspPosition.decimal_odds,
               OfficialAspPosition.placed_at, Market.game_id, MarketOutcome.label, MarketOutcome.outcome_key,
               OddsSnapshot.american_odds, Sportsbook.name, ExperimentAccount.sport, Game.starts_at, Game.status)
        .join(ShadowExperiment, ShadowExperiment.id == OfficialAspPosition.shadow_experiment_id)
        .join(ExperimentAccountMembership, (ExperimentAccountMembership.experiment_id == ShadowExperiment.id)
              & (ExperimentAccountMembership.role == MembershipRole.FUNDED_ERA))
        .join(ExperimentAccount, ExperimentAccount.id == ExperimentAccountMembership.account_id)
        .join(MarketOutcome, MarketOutcome.id == OfficialAspPosition.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Game, Game.id == Market.game_id)
        .outerjoin(PositionSettlement, PositionSettlement.position_id == OfficialAspPosition.id)
        .outerjoin(OddsSnapshot, OddsSnapshot.id == OfficialAspPosition.odds_snapshot_id)
        .outerjoin(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(OfficialAspPosition.status == PositionStatus.OPEN, PositionSettlement.id.is_(None))
        .order_by(Game.starts_at, OfficialAspPosition.placed_at)).all()
    if not rows:
        return []
    ids = list(dict.fromkeys(r[4] for r in rows))
    games = {g.uuid: g for g in (_Game(gid, sport, starts, status, None)
                                 for (_p, _s, _d, _pl, gid, _l, _k, _a, _b, sport, starts, status) in rows)}
    from app.models import GameTeam, Team

    for game_id, is_home, abbreviation, name in ctx.session.execute(
            select(GameTeam.game_id, GameTeam.is_home, Team.abbreviation, Team.name)
            .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id.in_(ids))).all():
        abbr = abbreviation or name or "?"
        if abbr and str(abbr).startswith("CFBD") and str(abbr)[4:].isdigit():
            abbr = name or abbr
        games[game_id].teams["home" if is_home else "away"] = {"abbr": abbr, "name": name}
    from app.models import ExternalIdMapping

    # The sport's own game id, so a bet whose game is not on this board still links to its game page.
    for entity_id, source, external in ctx.session.execute(
            select(ExternalIdMapping.entity_id, ExternalIdMapping.source, ExternalIdMapping.external_id)
            .where(ExternalIdMapping.entity_type == "game", ExternalIdMapping.source.in_(list(SOURCE_ID_MAPPINGS)),
                   ExternalIdMapping.entity_id.in_(ids))).all():
        game = games.get(entity_id)
        if game is not None and SOURCE_ID_MAPPINGS[source] == game.sport:
            game.source_game_id = str(external).rsplit(":", 1)[-1]
    scores = dict(day_scores or {})
    missing = [gid for gid in ids if str(gid) not in scores]
    if missing:
        scores.update(_scores(ctx, missing))
    nhl_missing = [gid for gid in ids if games[gid].sport == "NHL" and str(gid) not in ctx.nhl_results]
    if nhl_missing:
        ctx.nhl_results.update(_nhl_results(ctx, nhl_missing))
    out = []
    for pid, stake, dec, placed, gid, label, outcome_key, american, book, sport, starts, status in rows:
        game = games[gid]
        score = scores.get(str(gid))
        phase = _phase(ctx, game, score)
        side = str(outcome_key or "").split(":")[0].upper()
        selection = label
        if (not label or str(label).upper() in ("HOME", "AWAY")) and side in ("HOME", "AWAY"):
            selection = f"{game.teams[side.lower()]['abbr']} ML"
        stake_f = _num(stake)
        dec_f = _num(dec)
        live = _live(ctx, game, score, phase)
        if phase == "VOID":
            state = "VOID_AWAITING_SETTLEMENT"
        elif phase == "FINAL":
            state = "FINAL_AWAITING_SETTLEMENT"
        elif (score or {}).get("is_live"):
            state = "LIVE"
        elif ctx.now < game.start:
            state = "UPCOMING"
        else:
            state = "STARTED_NO_LIVE_FEED"
        out.append({"position_id": str(pid), "game_id": game.id, "source_game_id": game.source_game_id,
                    "sport": sport, "matchup": game.matchup,
                    "selection": selection or outcome_key,
                    "american": american if american is not None else _decimal_to_american(dec_f),
                    "sportsbook": book, "stake_usd": stake_f,
                    "to_win_usd": round(stake_f * (dec_f - 1), 2) if stake_f is not None and dec_f else None,
                    "starts_at": _iso(game.start), "slate_date": game.start.astimezone(ET).date().isoformat(),
                    "placed_at": _iso(placed), "state": state, "live": live})
    order = {"LIVE": 0, "STARTED_NO_LIVE_FEED": 1, "FINAL_AWAITING_SETTLEMENT": 2, "VOID_AWAITING_SETTLEMENT": 3,
             "UPCOMING": 4}
    out.sort(key=lambda s: (order.get(s["state"], 9), s["starts_at"] or ""))
    return out


# --------------------------------------------------------------- the build --

def _next_start(ctx: _Ctx) -> dict[str, datetime]:
    from app.models import Game, League

    rows = ctx.session.execute(
        select(League.code, func.min(Game.starts_at)).join(League, League.id == Game.league_id)
        .where(Game.starts_at > ctx.now, League.code.in_(SPORTS),
               or_(Game.source_mode.is_(None), Game.source_mode.notin_(EXCLUDED_SOURCE_MODES)))
        .group_by(League.code)).all()
    return {code: _aware(start) for code, start in rows if start is not None}


#: What each shared read is, for the health strip's "some reads failed" line (keys of ``read_errors``).
READ_NAMES = {
    "policies": "active policies", "nhl_era": "the NHL era", "next_start": "upcoming starts",
    "health.jobs": "job run history", "health.nhl_generation": "NHL generation", "coverage": "prices and forecasts",
    "decisions": "decisions", "reasons": "decision reasons", "positions": "positions", "scores": "live scores",
    "nhl_results": "NHL results", "sweating": "open bets", "allowance": "the shared daily limit",
    "bankrolls": "bankrolls",
}

SCHEDULE_NOTES = {
    "MLB": "V18 publishes about 45 minutes before first pitch (a target, not a guarantee); decisions T-30..T-15.",
    "NFL": "FINAL_PREGAME forecasts are generated T-60..T-15; only a FINAL forecast can control a position.",
    "CFB": "Prices are bought T-75..T-35 from The Odds API; there is no live College Football score feed.",
    "NHL": "Forecasts run at 9:20 AM and 3:20 PM ET; prices are bought T-75..T-35 (puck drop); finals from ESPN.",
    "NBA": "NBA paper decisions need a production package, a current lease and an NBA price source.",
}


def mission_control(session: Session, settings: Any, day: date, *, now: datetime | None = None) -> dict[str, Any]:
    """The Mission Control read model for one Eastern date (``AISP_MISSION_CONTROL_V1``). Reads only."""

    now = _aware(now) or datetime.now(UTC)
    ctx = _Ctx(session, settings, day, now)
    window, games = _games(session, settings, day)
    by_sport: dict[str, list[_Game]] = {s: [g for g in games if g.sport == s] for s in SPORTS}
    ids = {g.id for g in games}

    from app.services.slate_status import _leagues_with_active_policy

    ctx.active_policies = ctx.attempt("policies", lambda: _leagues_with_active_policy(session), default=set())
    from app.nhl import paper as nhl_paper

    era = ctx.attempt("nhl_era", lambda: nhl_paper.experiment(session))
    ctx.extra["nhl_era"] = str(era.id) if era is not None else None
    next_start = ctx.attempt("next_start", lambda: _next_start(ctx), default={}) or {}
    # A sport matters to the health strip when it plays on this date or within the next 48 hours.
    relevant = {s for s in SPORTS if by_sport[s] or (next_start.get(s) and next_start[s] - now <= RELEVANT_AHEAD)}
    health = _health(ctx, by_sport, relevant)
    if games and any(by_sport[s] for s in ("MLB", "NFL", "CFB", "NBA")):
        ctx.coverage = ctx.attempt("coverage", lambda: _coverage(ctx))
    else:
        ctx.coverage = {}
    ctx.decisions = ctx.attempt("decisions", lambda: _decisions(ctx, ids), default=None) if games else {}
    decision_ids = [str(i["decision_id"]) for i in (ctx.decisions or {}).values() if i.get("decision_id")]
    ctx.reasons = ctx.attempt("reasons", lambda: _reason_codes(ctx, decision_ids), default={}) or {}
    ctx.positions = ctx.attempt("positions", lambda: _positions(ctx, decision_ids), default={}) or {}
    ctx.scores = ctx.attempt("scores", lambda: _scores(ctx, [g.uuid for g in games]), default=None) if games else {}
    ctx.nhl_results = ctx.attempt("nhl_results", lambda: _nhl_results(ctx, [g.uuid for g in by_sport["NHL"]]),
                                  default={}) or {}

    sports_out: list[dict[str, Any]] = []
    games_out: list[dict[str, Any]] = []
    generation = ctx.extra.get("nhl_generation") or {}
    for sport in SPORTS:
        sport_games = by_sport[sport]
        read_state, error_type = "OK", None
        specific: dict[str, dict[str, Any]] = {}
        if sport_games:
            try:
                specific = _isolated(session, lambda s=sport, g=sport_games: ADAPTERS[s](ctx, g))
            except Exception as error:  # noqa: BLE001 -- one sport must never blank the page
                read_state, error_type = "READ_FAILED", type(error).__name__
                if not isinstance(error, CoverageUnavailable):     # that failure is already recorded as "coverage"
                    ctx.errors[f"sport:{sport}"] = error_type
        rows = []
        for game in sport_games:
            rows.append(_assemble(ctx, game, specific.get(game.id), error_type))
        games_out.extend(rows)
        pending = []
        if sport == "NHL":
            for item in generation.get("excluded") or []:
                # Pending = not yet forecast and not yet started (a started game is no longer waiting on anything).
                start = _aware(item.get("scheduledStartUtc"))
                if str(item.get("targetDate")) == day.isoformat() and (start is None or start > now):
                    pending.append({"source_game_id": str(item.get("sourceGameId")),
                                    "starts_at": _iso(item.get("scheduledStartUtc")),
                                    "reason": item.get("reason")})
        upcoming = [r["next_step"]["at"] for r in rows if r["next_step"].get("at") and r["next_step"]["at"] > _iso(now)]
        sports_out.append({
            "sport": sport, "label": SPORT_LABELS[sport], "games": len(sport_games), "read_state": read_state,
            "error_type": error_type, "owner_active": _owner_active(ctx, sport),
            "schedule_note": (("Back-to-back games are not forecast until the 9:20 AM ET run on game day. "
                               if any(str(p["reason"]).startswith("REST_INCOMPLETE") for p in pending) else "")
                              + SCHEDULE_NOTES[sport]) if sport == "NHL" else SCHEDULE_NOTES[sport],
            "next_event_at": min(upcoming) if upcoming else _iso(next_start.get(sport)),
            "pending_games": pending,
        })
    games_out.sort(key=lambda g: (g["starts_at"] or "", SPORTS.index(g["sport"]), g["matchup"]))

    # A failed read is null (and named in read_errors), never an empty list: "no open bets" must be a fact.
    sweating = ctx.attempt("sweating", lambda: _sweating(ctx, ctx.scores), default=None)
    allowance = ctx.attempt("allowance", lambda: _allowance(ctx))
    if allowance is None:
        allowance = {"state": "UNKNOWN", "in_force": None, "effective_from": None, "limit_usd": None,
                     "entered_usd": None, "room_usd": None, "entries": None, "ambiguous_entries": None,
                     "slate_date": day.isoformat(), "basis": None, "read_state": "READ_FAILED",
                     "error_type": ctx.errors.get("allowance")}
    bankrolls = ctx.attempt("bankrolls", lambda: _bankrolls(ctx), default=[]) or []
    positions = [g["stages"]["bet"] for g in games_out if g["stages"]["bet"].get("position_id")]
    results = [g["stages"]["result"] for g in games_out if g["stages"]["bet"].get("position_id")]
    counts = {
        "games": len(games_out),
        "live": sum(1 for g in games_out if g["stages"]["live"].get("is_live")),
        "bets_open": sum(1 for p in positions if p.get("status") == "OPEN"),
        "bets_settled": sum(1 for p in positions if p.get("status") in ("SETTLED", "VOID")),
        "blocked": sum(1 for g in games_out if g["attention"] == "BLOCKED"),
        "waiting": sum(1 for g in games_out if g["next_step"]["kind"] == "WAIT"),
        "staked_usd": round(sum(p.get("stake_usd") or 0.0 for p in positions), 2),
        "profit_usd": round(sum(r.get("profit_usd") or 0.0 for r in results), 2),
    }
    if ctx.errors:
        failed = [READ_NAMES.get(key) or (f"{SPORT_LABELS.get(key[6:], key[6:])} stages" if key.startswith("sport:")
                                          else key) for key in sorted(ctx.errors)]
        health = {**health, "issues": health["issues"] + [
            f"Some reads failed and are shown as unknown, never as done: {', '.join(failed)}."]}
        if health["overall"] == "OK":
            health["overall"] = "DEGRADED"
    from app.core.time import local_today

    from app.services.owner_funding_restrictions import runtime_state

    return {
        "owner_funding_restrictions": runtime_state(settings),
        "schema_version": SCHEMA_VERSION, "date": day.isoformat(),
        "timezone": getattr(settings, "display_timezone", "America/New_York") or "America/New_York",
        "is_today": day == local_today(getattr(settings, "display_timezone", "America/New_York") or "America/New_York",
                                       now),
        "window": window.as_dict(), "generated_at": _iso(now), "authority": AUTHORITY, "provider_calls_made": 0,
        "refresh": dict(REFRESH), "health": health, "allowance": allowance, "bankrolls": bankrolls,
        "sports": sports_out, "games": games_out, "sweating": sweating, "counts": counts,
        "read_errors": {k: v for k, v in sorted(ctx.errors.items())},
    }


def _assemble(ctx: _Ctx, game: _Game, specific: dict[str, Any] | None, sport_error: str | None) -> dict[str, Any]:
    """One MissionGame row: the sport's own stages plus the common decision/bet/live/result stages."""

    now = ctx.now
    score = (ctx.scores or {}).get(game.id)
    phase = _phase(ctx, game, score)
    item = (ctx.decisions or {}).get(game.id)
    if specific is None:
        error = sport_error or ("coverage" in ctx.errors and ctx.errors["coverage"]) or None
        forecast, approval, prices = (_unknown("forecast", error), _unknown("approval", error),
                                      _unknown("prices", error))
        clocks = {"forecast_due_at": None, "buy_opens_at": None, "buy_closes_at": None}
    else:
        forecast, approval, prices, clocks = (specific["forecast"], specific["approval"], specific["prices"],
                                              specific["clocks"])
    decision_game=game
    if game.sport=="NFL" and clocks.get("operational_start"):
        from copy import copy
        decision_game=copy(game)
        decision_game.start=_aware(clocks["operational_start"])
    try:
        decision = _decision(ctx, decision_game, item, phase)
        bet = _bet(ctx, game, item, decision, phase)
        result = _result(ctx, game, item, bet, decision, score, phase)
    except Exception as error:  # noqa: BLE001
        decision, bet, result = (_unknown("decision", type(error).__name__), _unknown("bet", type(error).__name__),
                                 _unknown("result", type(error).__name__))
    try:
        live = _live(ctx, game, score, phase)
    except Exception as error:  # noqa: BLE001
        live = _unknown("live", type(error).__name__)
    prices = _prices_from_decision(game, item, decision, prices, now)
    if phase == "VOID":
        for name, block in (("forecast", forecast), ("approval", approval), ("prices", prices)):
            if block["state"] in (WAITING, BLOCKED):
                block.update(state=NA, code="VOID", label=game.status.capitalize(),
                             detail=f"The game is {game.status}.", due_at=None)
    stages = {"forecast": forecast, "approval": approval, "prices": prices, "decision": decision, "bet": bet,
              "live": live, "result": result}
    return {
        "game_id": game.id, "sport": game.sport, "source_game_id": game.source_game_id, "matchup": game.matchup,
        "away": dict(game.teams["away"]), "home": dict(game.teams["home"]), "starts_at": _iso(game.start),
        "status": game.status, "phase": phase,
        "clocks": {**clocks, "decision_opens_at": _iso(decision_game.opens), "decision_closes_at": _iso(decision_game.closes)},
        "stages": stages, "next_step": _next_step(ctx, decision_game, stages, phase, score),
        "attention": _attention(ctx, game, stages),
    }


__all__ = ["KICK_WORD", "MAX_DAYS_AHEAD", "MAX_DAYS_BACK", "SCHEMA_VERSION", "mission_control"]
