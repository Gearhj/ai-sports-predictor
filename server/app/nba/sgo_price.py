"""NBA SportsGameOdds prices: plan, request, retain, parse, bind, persist. Built and OFF.

STATUS
------
Implemented and tested offline against clearly labelled SYNTHETIC fixtures only.
NOT live validated and makes no request: ``NBA_SGO_LANE_ENABLED`` is false, the
lane's object cap is 0, it has no authorisation window, and two production
identities are unresolved and parameterised -- never guessed:

* ``NBA_SGO_LEAGUE_ID``      the provider's league identifier. AISP has never
  observed an SGO NBA event; ``show_preview`` already lists NBA as
  ``SGO_LEAGUE_IDENTITY_UNVERIFIED``.
* ``NBA_SGO_TEAM_MAP_PATH``  a REVIEWED map from SGO ``teamID`` to Codex
  franchise id (``nba-neil:franchise:<CODE>``). Empty until real ids are seen.

THE SAME PATTERNS AS MLB/NFL (and three the CFB adapter lacks)
--------------------------------------------------------------
* Exact event binding: pinned league, both teams through the reviewed map to
  the admitted game's franchise ids, start within five minutes of the admitted
  tipoff, orientation checked both ways. Names never bind.
* Targeting: one request addresses ONE event -- by reviewed team id, or by a
  tipoff no other held/retained NBA game shares within five minutes. NBA slates
  stack tipoffs on the half hour, so untargetable games are common and are
  never "bought and filtered after".
* Raw retention (``sgo_raw_archive.archive_response``): the exact response bytes
  are archived before parsing; the archive receipt rides on the ledger row.
* Lane halt (``collection_halt``): an active halt refuses every request, and a
  response carrying an event that was not selected latches the halt.
* Per-leg clocks: each side's own book ``lastUpdatedAt`` is its ``observed_at``;
  the checkpoint's ``paired_quotes`` lets the OLDER leg govern freshness.
* Both budget guards, a ledger reservation before the request, no retry.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ExternalIdMapping, Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType, OddsSnapshot,
    Sportsbook,
)
from app.nba import gen1_common as common

LANE = "sgo_nba_events"
PROVIDER = "SPORTSGAMEODDS"
TEAM_MAP_SCHEMA = "AISP_NBA_SGO_TEAM_MAP_V1"
BUY_OPENS = timedelta(minutes=75)
BUY_CLOSES = timedelta(minutes=35)
TARGET_TOLERANCE = timedelta(minutes=5)
MARKET_TYPES = {"h2h": MarketType.MONEYLINE, "spreads": MarketType.SPREAD, "totals": MarketType.TOTAL}

LIVE_VALIDATION = (
    "One SportsGameOdds /events request (1 object, limit=1) for an NBA game whose tipoff no other NBA game "
    "shares within five minutes, with leagueID set to the documented candidate. It must show: (1) the league "
    "identifier the provider actually returns; (2) the teamID values of both clubs, which a reviewer maps to "
    "Codex franchise ids against that uniquely timed game; (3) status.startsAt within five minutes of the "
    "admitted tipoff; (4) full-game moneyline, spread and total oddIDs with per-book lastUpdatedAt; (5) that "
    "the request billed exactly one object. Only then are NBA_SGO_LEAGUE_ID and the first reviewed team-map "
    "entries pinned. It needs an owner-authorised October allowance (docs/NBA_LAUNCH_PLAN_20260919.md)."
)


@dataclass
class Binding:
    league_id: str | None
    team_map: dict[str, str] = field(default_factory=dict)
    map_sha256: str | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return bool(self.league_id) and not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {"league_id": self.league_id or None, "mapped_teams": len(self.team_map),
                "team_map_sha256": self.map_sha256, "resolved": self.resolved, "problems": self.problems or None}


def load_binding(settings: Any) -> Binding:
    """The pinned league id and the reviewed team map. Empty values are unresolved, never defaulted."""

    league = str(getattr(settings, "nba_sgo_league_id", "") or "").strip() or None
    binding = Binding(league_id=league)
    if not league:
        binding.problems.append("NBA_SGO_LEAGUE_ID_UNRESOLVED")
    path = str(getattr(settings, "nba_sgo_team_map_path", "") or "").strip()
    if not path:
        return binding
    raw = pathlib.Path(path).read_bytes()
    document = json.loads(raw)
    binding.map_sha256 = hashlib.sha256(raw).hexdigest()
    if document.get("schema") != TEAM_MAP_SCHEMA:
        binding.problems.append("TEAM_MAP_SCHEMA_UNKNOWN")
        return binding
    if league and document.get("leagueID") != league:
        binding.problems.append("TEAM_MAP_LEAGUE_MISMATCH")
        return binding
    seen: dict[str, str] = {}
    for entry in document.get("entries", []):
        sgo, franchise = str(entry.get("sgoTeamID") or ""), str(entry.get("franchiseId") or "")
        if entry.get("reviewed") is not True or not sgo or not franchise.startswith("nba-neil:franchise:"):
            continue
        if sgo in binding.team_map or franchise in seen:
            binding.problems.append(f"TEAM_MAP_NOT_ONE_TO_ONE:{sgo}")
            continue
        binding.team_map[sgo] = franchise
        seen[franchise] = sgo
    return binding


def _franchise_sides(session: Session, game_id: Any) -> dict[bool, tuple[Any, str | None]]:
    """is_home -> (AISP team id, Codex franchise id)."""

    out: dict[bool, tuple[Any, str | None]] = {}
    for side in session.scalars(select(GameTeam).where(GameTeam.game_id == game_id)):
        franchise = session.scalar(select(ExternalIdMapping.external_id).where(
            ExternalIdMapping.entity_type == "team", ExternalIdMapping.source == common.SOURCE_FRANCHISE,
            ExternalIdMapping.entity_id == side.team_id))
        out[side.is_home] = (side.team_id, franchise)
    return out


def known_tipoffs(session: Session, settings: Any) -> list[datetime]:
    """Every NBA tipoff AISP knows: held games plus the retained schedule objects."""

    from app.nba import gen1_source

    # Keyed by ESPN game id so a held game and its retained schedule row count once.
    tipoffs: dict[str, datetime] = {}
    for gid, start, espn in session.execute(
            select(Game.id, Game.starts_at, ExternalIdMapping.external_id)
            .join(League, League.id == Game.league_id)
            .outerjoin(ExternalIdMapping, (ExternalIdMapping.entity_id == Game.id)
                       & (ExternalIdMapping.entity_type == "game")
                       & (ExternalIdMapping.source == common.SOURCE_ESPN_GAME))
            .where(League.code == common.LEAGUE_CODE)).all():
        tipoffs[f"espn:{espn}" if espn else f"aisp:{gid}"] = common.aware(start)
    objects, _ = gen1_source.load_objects(settings)
    for obj in objects:
        for espn, row in obj.rows.items():
            start = common.ts(row.get("start_date"))
            if start is not None:
                tipoffs.setdefault(f"espn:{espn}", start)
    return list(tipoffs.values())


def targeting(tipoff: datetime, sides: dict[bool, tuple[Any, str | None]], binding: Binding,
              tipoffs: list[datetime]) -> dict[str, Any]:
    reverse = {franchise: sgo for sgo, franchise in binding.team_map.items()}
    home_sgo = reverse.get(str((sides.get(True) or (None, None))[1]))
    away_sgo = reverse.get(str((sides.get(False) or (None, None))[1]))
    if home_sgo and away_sgo:
        return {"strategy": "TEAM_ID", "team_id": home_sgo, "expected": {"home": home_sgo, "away": away_sgo}}
    nearby = sum(1 for other in tipoffs if abs(other - tipoff) <= TARGET_TOLERANCE)
    if nearby == 1:
        return {"strategy": "UNIQUE_TIPOFF", "games_within_tolerance": 1}
    return {"strategy": None, "reason": "UNTARGETABLE_SHARED_TIPOFF", "games_within_tolerance": nearby}


def request_params(tipoff: datetime, target: dict[str, Any], league_id: str) -> dict[str, str]:
    # CONVERTED, then labelled "Z". `tipoff` reaches here from
    # `common.aware(Game.starts_at)`, which attaches a timezone without
    # converting -- and the database session's TimeZone is America/New_York, so
    # that value carries an Eastern wall clock. Stamping it with a literal "Z"
    # moves this +/- five-minute band four hours off the tipoff it is meant to
    # name. `common.aware` is shared by the whole NBA gen1 stack (where it is
    # only ever used for instant arithmetic, which is unaffected), so the
    # conversion is applied here, at the one place the value becomes a label.
    from app.services.sgo_collection import utc_stamp

    params = {"leagueID": league_id, "limit": "1",
              "startsAfter": utc_stamp(tipoff - TARGET_TOLERANCE),
              "startsBefore": utc_stamp(tipoff + TARGET_TOLERANCE),
              "finalized": "false", "oddsPresent": "true"}
    if target.get("strategy") == "TEAM_ID":
        params["teamID"] = str(target["team_id"])
    return params


def _lane_spent(session: Session) -> int:
    from app.models import ProviderRequestLedger

    return int(session.scalar(select(func.coalesce(func.sum(ProviderRequestLedger.records_received), 0))
                              .where(ProviderRequestLedger.endpoint_key == LANE)) or 0)


def capacity(session: Session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """The lane's allowance. With shipped settings: zero, and it says why. Writes nothing.

    Order: switch, authority window, lane cap, then both budget guards (read-only).
    The guards are read only when everything before them passes, so a disabled
    lane never even computes a spend.
    """

    moment = now or datetime.now(UTC)
    cap = int(getattr(settings, "nba_sgo_lane_object_cap", 0) or 0)
    until = common.ts(getattr(settings, "nba_sgo_lane_authorized_until", "") or None)
    spent = _lane_spent(session)
    base = {"lane": LANE, "checked_at": moment.isoformat(), "assignment_cap": cap, "spent_on_lane": spent,
            "authorized_until": common.iso(until), "objects_allowed": 0}
    if not getattr(settings, "nba_sgo_lane_enabled", False):
        return {**base, "state": "LANE_DISABLED"}
    if until is None or moment >= until:
        return {**base, "state": "NO_AUTHORITY_WINDOW" if until is None else "AUTHORIZATION_EXPIRED"}
    if spent >= cap:
        return {**base, "state": "ASSIGNMENT_CAP_REACHED" if cap else "NO_OBJECTS_ALLOCATED"}
    from app.services import provider_allocation as pa
    from app.services.sgo_collection import object_budget

    guard_b = object_budget(session, now=moment)
    plan = pa.build_plan(session, now=moment).as_dict()
    spendable = min(int(plan.get("spendable") or 0),
                    0 if guard_b.blocked_reason else int(guard_b.spendable or 0))
    allowed = max(0, min(cap - spent, spendable))
    return {**base, "state": "CAPACITY_AVAILABLE" if allowed else "NO_GUARD_CAPACITY",
            "guard_a_spendable": plan.get("spendable"), "guard_b_spendable": guard_b.spendable,
            "objects_allowed": allowed}


def _fresh_pair_held(session: Session, game_id: Any, since: datetime) -> bool:
    return session.scalar(
        select(OddsSnapshot.id).join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .where(Market.game_id == game_id, Market.market_type == MarketType.MONEYLINE,
               OddsSnapshot.source == PROVIDER, OddsSnapshot.available_as_of >= since).limit(1)) is not None


def plan(session: Session, settings: Any, *, now: datetime | None = None,
         capacity_fn: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """Which NBA games may be priced now, each with its reason. Writes nothing."""

    from app.models import ExternalIdMapping as Mapping
    from app.nba.gen1_production import current_forecast_for_game, present_eligibility

    moment = now or datetime.now(UTC)
    binding = load_binding(settings)
    tipoffs = known_tipoffs(session, settings)
    rows = session.execute(
        select(Game.id, Game.starts_at, Mapping.external_id)
        .join(League, League.id == Game.league_id)
        .join(Mapping, (Mapping.entity_id == Game.id) & (Mapping.entity_type == "game")
              & (Mapping.source == common.SOURCE_ESPN_GAME))
        .where(League.code == common.LEAGUE_CODE, Game.status == GameStatus.SCHEDULED,
               Game.starts_at >= moment + BUY_CLOSES, Game.starts_at <= moment + BUY_OPENS)
        .order_by(Game.starts_at, Mapping.external_id)).all()
    games: list[dict[str, Any]] = []
    for game_id, starts_at, espn in rows:
        tipoff = common.aware(starts_at)
        entry = {"game_id": str(game_id), "espn_game_id": str(espn), "tipoff": tipoff.isoformat()}
        forecast = current_forecast_for_game(session, game_id=game_id)
        if forecast is None or not present_eligibility(session, forecast, now=moment)["eligible"]:
            games.append({**entry, "state": "NOT_DECISION_ELIGIBLE"})
            continue
        if _fresh_pair_held(session, game_id, tipoff - BUY_OPENS):
            games.append({**entry, "state": "PRICE_ALREADY_HELD"})
            continue
        target = targeting(tipoff, _franchise_sides(session, game_id), binding, tipoffs)
        if not target.get("strategy"):
            games.append({**entry, "state": target["reason"], "target": target})
            continue
        if not binding.resolved:
            games.append({**entry, "state": "SGO_BINDING_UNRESOLVED", "target": target, "binding": binding.as_dict()})
            continue
        games.append({**entry, "state": "READY", "target": target,
                      "params": request_params(tipoff, target, str(binding.league_id))})
    gate = (capacity_fn or capacity)(session, settings, now=moment)
    ready = [g for g in games if g["state"] == "READY"]
    return {"checked_at": moment.isoformat(), "binding": binding.as_dict(), "capacity": gate, "games": games,
            "will_request": ready[: int(gate.get("objects_allowed") or 0)], "live_validation": LIVE_VALIDATION}


def bind_quote_event(session: Session, game_id: Any, quote: Any, binding: Binding) -> tuple[bool, str]:
    """Exact identities only. Returns (ok, reason)."""

    if quote.league_id != binding.league_id:
        return False, "LEAGUE_ID_MISMATCH"
    game = session.get(Game, game_id)
    if game is None or abs(quote.commence_time - common.aware(game.starts_at)) > TARGET_TOLERANCE:
        return False, "START_TIME_DISAGREES_WITH_ADMITTED_TIPOFF"
    sides = _franchise_sides(session, game_id)
    home, away = binding.team_map.get(quote.home_team_id), binding.team_map.get(quote.away_team_id)
    if home is None or away is None:
        return False, "TEAM_NOT_IN_REVIEWED_MAP"
    admitted_home, admitted_away = (sides.get(True) or (None, None))[1], (sides.get(False) or (None, None))[1]
    if (home, away) == (admitted_away, admitted_home):
        return False, "ORIENTATION_REVERSED"
    if (home, away) != (admitted_home, admitted_away):
        return False, "TEAMS_DISAGREE_WITH_ADMITTED_GAME"
    return True, "BOUND"


def event_binds(session: Session, game_id: Any, event: dict[str, Any], binding: Binding) -> bool:
    """Does one RAW returned event identify exactly the targeted game? (league, teams, start)."""

    teams = event.get("teams") or {}
    start = common.ts((event.get("status") or {}).get("startsAt"))
    game = session.get(Game, game_id)
    if event.get("leagueID") != binding.league_id or game is None or start is None:
        return False
    if abs(start - common.aware(game.starts_at)) > TARGET_TOLERANCE:
        return False
    sides = _franchise_sides(session, game_id)
    home = binding.team_map.get(str((teams.get("home") or {}).get("teamID") or ""))
    away = binding.team_map.get(str((teams.get("away") or {}).get("teamID") or ""))
    return (home, away) == ((sides.get(True) or (None, None))[1], (sides.get(False) or (None, None))[1])


def persist(session: Session, game_id: Any, quotes: list[Any], *, available_as_of: datetime,
            raw_archive: dict[str, Any] | None = None) -> dict[str, int]:
    """Write validated two-sided quotes in the shape the checkpoint reads. Idempotent."""

    from app.data_sources.provider_registry import identity as provider_identity
    from app.gen2.exchange.canonical_json import canonical_sha256

    provider = provider_identity(PROVIDER)
    sides = {is_home: team for is_home, (team, _f) in _franchise_sides(session, game_id).items()}
    created = existing = 0
    for quote in quotes:
        market_type = MARKET_TYPES.get(quote.market_key)
        if market_type is None:
            continue
        book_key = provider.bookmaker_key(quote.bookmaker_key)
        book = session.scalar(select(Sportsbook).where(Sportsbook.key == book_key))
        if book is None:
            book = Sportsbook(key=book_key, name=f"{quote.bookmaker_title} ({provider.display_name}, research)",
                              region="us", active=True)
            session.add(book)
            session.flush()
        market = session.scalar(select(Market).where(Market.game_id == game_id, Market.market_type == market_type,
                                                     Market.period == "full_game"))
        if market is None:
            market = Market(game_id=game_id, market_type=market_type, period="full_game")
            session.add(market)
            session.flush()
        for side in quote.sides:
            if market_type == MarketType.TOTAL:
                key_side, participant = side.side_id.upper(), None
            else:
                key_side = "HOME" if side.side_id == "home" else "AWAY"
                participant = sides.get(key_side == "HOME")
            outcome_key = f"{key_side}:{side.point}"
            outcome = session.scalar(select(MarketOutcome).where(MarketOutcome.market_id == market.id,
                                                                 MarketOutcome.outcome_key == outcome_key))
            if outcome is None:
                outcome = MarketOutcome(market_id=market.id, outcome_key=outcome_key, label=key_side,
                                        participant_team_id=participant)
                session.add(outcome)
                session.flush()
            if session.scalar(select(OddsSnapshot.id).where(
                    OddsSnapshot.market_outcome_id == outcome.id, OddsSnapshot.sportsbook_id == book.id,
                    OddsSnapshot.observed_at == side.provider_updated_at, OddsSnapshot.american_odds == side.american,
                    OddsSnapshot.source == PROVIDER).limit(1)) is not None:
                existing += 1
                continue
            raw = {"provider_event_id": quote.event_id, "odd_id": side.odd_id, "side_id": side.side_id,
                   "book": quote.bookmaker_key, "american": side.american, "point": side.point,
                   # Each leg's OWN book clock. The pair's age is its older leg.
                   "book_last_updated_at": side.provider_updated_at.isoformat(), "period": quote.period_id,
                   "league_id": quote.league_id, "settlement": "full game including overtime (SGO periodID 'game')",
                   "raw_archive_sha256": (raw_archive or {}).get("responseBodySha256") or (raw_archive or {}).get("sha256")}
            session.add(OddsSnapshot(
                market_outcome_id=outcome.id, sportsbook_id=book.id, observed_at=side.provider_updated_at,
                available_as_of=available_as_of, american_odds=side.american, decimal_odds=side.decimal,
                point=side.point, implied_probability=1.0 / side.decimal, source=PROVIDER,
                source_event_id=quote.event_id, source_mode="LIVE", raw_payload=raw,
                payload_checksum=canonical_sha256(raw)))
            created += 1
    if quotes and session.scalar(select(ExternalIdMapping.id).where(
            ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == PROVIDER,
            ExternalIdMapping.external_id == quotes[0].event_id)) is None:
        session.add(ExternalIdMapping(entity_type="game", entity_id=game_id, source=PROVIDER,
                                      external_id=quotes[0].event_id))
    session.flush()
    return {"created": created, "existing": existing}


def fetch_events(settings: Any, params: dict[str, str]) -> bytes:
    """Exactly one HTTP request, returning the EXACT body bytes. No retry; key in a header only."""

    import httpx

    url = f"{settings.sportsgameodds_base_url.rstrip('/')}/events"
    headers = {"X-Api-Key": settings.sportsgameodds_api_key, "Accept": "application/json"}
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        response = client.get(url, params=params, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(f"SportsGameOdds returned HTTP {response.status_code}; not retried")
    return response.content


def collect(session: Session, settings: Any, *, now: datetime | None = None,
            fetch: Callable[[Any, dict[str, str]], bytes] | None = None,
            capacity_fn: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """Spend at most the lane's allowance on READY games. With shipped settings: nothing."""

    from app.data_sources.sgo_parser import parse_main_markets
    from app.services import provider_allocation as pa
    from app.services.collection_halt import active_halt, halt_lane, mismatch
    from app.services.sgo_collection import complete_request, object_budget, record_request
    from app.services.sgo_raw_archive import archive_response

    moment = now or datetime.now(UTC)
    if not getattr(settings, "nba_sgo_lane_enabled", False):
        return {"status": "LANE_DISABLED", "objects_spent": 0, "provider_requests": 0}
    halt = active_halt(session, lane=LANE)
    if halt is not None:
        return {"status": "LANE_HALTED", "objects_spent": 0, "provider_requests": 0, "halt_reason": halt.reason}
    planned = plan(session, settings, now=moment, capacity_fn=capacity_fn)
    summary: dict[str, Any] = {"status": "PLANNED", "objects_spent": 0, "provider_requests": 0, "requests": [],
                               "capacity": planned["capacity"], "binding": planned["binding"],
                               "games": [{k: g[k] for k in ("espn_game_id", "state")} for g in planned["games"]]}
    if not planned["will_request"]:
        summary["status"] = (planned["capacity"].get("state")
                             if int(planned["capacity"].get("objects_allowed") or 0) < 1 else "NO_READY_GAME")
        return summary
    binding = load_binding(settings)
    fetcher = fetch or fetch_events
    for game in planned["will_request"]:
        guard_b = object_budget(session, now=moment)
        guard_a = pa.build_plan(session, now=moment).as_dict()
        guard_b_ok, guard_b_reason = guard_b.may_spend(1)
        if not guard_b_ok or int(guard_a.get("spendable") or 0) < 1:
            summary["requests"].append({"espn_game_id": game["espn_game_id"], "state": "GUARD_REFUSED",
                                        "guard_b": guard_b_reason, "guard_a_spendable": guard_a.get("spendable")})
            break
        reservation = record_request(session, requested_at=moment, params=game["params"], budget=guard_b,
                                     endpoint_key=LANE, objects_requested=1)
        session.commit()
        summary["provider_requests"] += 1
        try:
            body = fetcher(settings, game["params"])
            payload = json.loads(body)
        except Exception as error:  # noqa: BLE001 -- recorded, never retried
            complete_request(session, reservation, objects=0, succeeded=False, budget=guard_b,
                             error=str(error)[:200], endpoint_key=LANE)
            session.commit()
            summary["requests"].append({"espn_game_id": game["espn_game_id"], "state": "REQUEST_FAILED"})
            continue
        archive = archive_response(settings, body, requested_at=moment,
                                   request_fingerprint=hashlib.sha256(json.dumps(game["params"], sort_keys=True)
                                                                      .encode()).hexdigest(),
                                   lane=LANE, league=str(binding.league_id), filters=game["params"])
        events = payload.get("data") or []
        objects = len(events)
        complete_request(session, reservation, objects=objects, succeeded=True, budget=guard_b, endpoint_key=LANE,
                         extra_metadata={"league_id": binding.league_id, "espn_game_id": game["espn_game_id"],
                                         "raw_archive": archive})
        summary["objects_spent"] += objects
        parsed = parse_main_markets(payload, received_at=moment, now=moment, league_id=str(binding.league_id))
        outcome: dict[str, Any] = {"espn_game_id": game["espn_game_id"], "objects": objects,
                                   "quotes": len(parsed.quotes), "parse_rejections": parsed.rejected[:6],
                                   "raw_archive": archive.get("status")}
        bound = []
        for quote in parsed.quotes:
            ok, reason = bind_quote_event(session, _uuid(game["game_id"]), quote, binding)
            if not ok:
                outcome.setdefault("binding_refusals", []).append(
                    {"event_id": quote.event_id, "reason": reason, "home_team_id": quote.home_team_id,
                     "away_team_id": quote.away_team_id})
                continue
            bound.append(quote)
        # The halt test reads the RAW events, not the parsed quotes: an event with an
        # unparseable market is still an event the request did or did not ask for.
        selected, returned = [], []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("eventID"))
            returned.append(event_id)
            if event_binds(session, _uuid(game["game_id"]), event, binding):
                selected.append(event_id)
        fault = mismatch(selected, returned) if returned else None
        if fault is not None:
            halt_lane(session, lane=LANE, reason="PROVIDER_RETURNED_UNSELECTED_GAME",
                      evidence={**fault, "params": game["params"], "raw_archive": archive}, now=moment)
            outcome["halted"] = fault
        outcome["persisted"] = persist(session, _uuid(game["game_id"]), bound, available_as_of=moment,
                                       raw_archive=archive)
        session.commit()
        summary["requests"].append(outcome)
        if fault is not None:
            break
    summary["status"] = "COLLECTED"
    return summary


def _uuid(value: Any) -> Any:
    import uuid

    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


__all__ = ["LANE", "LIVE_VALIDATION", "TEAM_MAP_SCHEMA", "Binding", "bind_quote_event", "capacity", "collect", "event_binds",
           "fetch_events", "known_tipoffs", "load_binding", "persist", "plan", "request_params", "targeting"]
