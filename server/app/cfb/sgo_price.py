"""College Football SportsGameOdds prices: plan, request, parse, bind, persist.

STATUS
------
Implemented and tested offline against clearly labelled fixtures. NOT live
validated: AISP has never observed a SportsGameOdds College Football event, so
three production identities are deliberately unresolved and parameterised --
never guessed:

* ``CFB_SGO_LEAGUE_ID``      -- the provider's league identifier (documented
  publicly as ``NCAAF``; unobserved in retained evidence, so empty here).
* ``CFB_SGO_TEAM_MAP_PATH``  -- a REVIEWED map from SportsGameOdds ``teamID``
  to CFBD school id. Empty until real team ids are observed and reviewed.
* ``teamID`` request filter  -- used only when both teams are in the reviewed
  map; its behaviour for College Football is part of the live validation.

Until both identities are pinned, planning reports ``SGO_BINDING_UNRESOLVED``
and nothing is requested. See ``LIVE_VALIDATION`` for the one observation that
resolves them.

WHAT EACH PIECE DOES
--------------------
* ``plan``      which games may be priced now: the paper checkpoint's own
                prerequisites (current discovery lease, the producer's
                eligibility record), a buy window of T-75..T-35 so the pair is
                fresh at T-30..T-15, no fresh SGO pair already held, and a
                request that can target ONE event (by reviewed team id, or a
                kickoff no other CFBD game shares within five minutes). A game
                that cannot be targeted is never bought "and filtered after".
* ``collect``   spends at most what ``sgo_lane.capacity`` calls genuinely
                uncommitted -- after the reserve, every remaining MLB/NFL
                projected need and in-flight reservations, under BOTH guards --
                and never more than the assignment's ten objects in total. Each
                request is reserved in the provider ledger before it is sent.
* parsing       the existing ``parse_main_markets``: exact oddIDs, both sides
                from ONE bookmaker, the book's own ``lastUpdatedAt`` per side,
                full-game period (``game``, overtime included), and an event
                whose ``leagueID`` differs from the pinned one is refused.
* binding       exact identities only: pinned league, both team ids through the
                reviewed map to the admitted game's CFBD home/away ids, start
                within five minutes of the admitted kickoff. Names never bind.
* persistence   the NFL shape the checkpoint already reads: ``sgo:<book>``
                sportsbooks, ``HOME:None``/``AWAY:None`` moneyline outcomes with
                participant teams, one ``OddsSnapshot`` per side stamped with
                that side's own book timestamp. The older leg governs freshness
                at the checkpoint (``nfl_paper_decision.paired_quotes``).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ExternalIdMapping, Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType, OddsSnapshot,
    Season, Sportsbook,
)

LANE = "sgo_cfb_events"
PROVIDER = "SPORTSGAMEODDS"
TEAM_MAP_SCHEMA = "AISP_CFB_SGO_TEAM_MAP_V1"
BUY_OPENS = timedelta(minutes=75)
BUY_CLOSES = timedelta(minutes=35)
TARGET_TOLERANCE = timedelta(minutes=5)
MARKET_TYPES = {"h2h": MarketType.MONEYLINE, "spreads": MarketType.SPREAD, "totals": MarketType.TOTAL}

LIVE_VALIDATION = (
    "One SportsGameOdds /events request (1 object, limit=1) for a College Football game whose kickoff no "
    "other CFBD game shares within five minutes, with leagueID set to the documented candidate. It must "
    "show: (1) the league identifier the provider actually returns; (2) the teamID values for both schools, "
    "which a reviewer maps to CFBD ids against the uniquely timed CFBD game; (3) status.startsAt within five "
    "minutes of the CFBD kickoff; (4) full-game moneyline oddIDs with per-book lastUpdatedAt; (5) that the "
    "request billed exactly one object. Only then are CFB_SGO_LEAGUE_ID and the first team-map entries "
    "pinned. Blocked while uncommitted SportsGameOdds capacity is zero."
)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


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
                "team_map_sha256": self.map_sha256, "resolved": self.resolved,
                "problems": self.problems or None}


def load_binding(settings: Any) -> Binding:
    """The pinned league id and the reviewed team map. Empty values are unresolved, never defaulted."""

    league = str(getattr(settings, "cfb_sgo_league_id", "") or "").strip() or None
    binding = Binding(league_id=league)
    if not league:
        binding.problems.append("CFB_SGO_LEAGUE_ID_UNRESOLVED")
    path = str(getattr(settings, "cfb_sgo_team_map_path", "") or "").strip()
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
    for entry in document.get("entries", []):
        if entry.get("reviewed") is True and entry.get("sgoTeamID") and str(entry.get("cfbdTeamId", "")).isdigit():
            binding.team_map[str(entry["sgoTeamID"])] = str(entry["cfbdTeamId"])
    return binding


def _cfbd_sides(session: Session, game_id: Any) -> dict[bool, tuple[Any, str | None]]:
    """is_home -> (AISP team id, CFBD team id)."""

    out: dict[bool, tuple[Any, str | None]] = {}
    for side in session.scalars(select(GameTeam).where(GameTeam.game_id == game_id)):
        cfbd = session.scalar(select(ExternalIdMapping.external_id).where(
            ExternalIdMapping.entity_type == "team", ExternalIdMapping.source == "CFBD",
            ExternalIdMapping.entity_id == side.team_id))
        out[side.is_home] = (side.team_id, cfbd)
    return out


def schedule_kickoffs(settings: Any) -> list[datetime]:
    """Every CFBD kickoff in the independent raw captures, all classifications."""

    kickoffs: dict[str, datetime] = {}
    for raw_path in str(getattr(settings, "cfb_independent_schedule_path", "") or "").split(";"):
        path = pathlib.Path(raw_path.strip())
        if not raw_path.strip() or not path.exists():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            start = row.get("startDate")
            if start and not row.get("startTimeTBD"):
                kickoffs[str(row["id"])] = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    return list(kickoffs.values())


def targeting(kickoff: datetime, sides: dict[bool, tuple[Any, str | None]], binding: Binding,
              kickoffs: list[datetime]) -> dict[str, Any]:
    """How one request can address exactly one event, or why it cannot."""

    reverse = {cfbd: sgo for sgo, cfbd in binding.team_map.items()}
    home_sgo = reverse.get(str((sides.get(True) or (None, None))[1]))
    away_sgo = reverse.get(str((sides.get(False) or (None, None))[1]))
    if home_sgo and away_sgo:
        return {"strategy": "TEAM_ID", "team_id": home_sgo, "expected": {"home": home_sgo, "away": away_sgo}}
    nearby = sum(1 for other in kickoffs if abs(other - kickoff) <= TARGET_TOLERANCE)
    if nearby == 1:
        return {"strategy": "UNIQUE_KICKOFF", "games_within_tolerance": 1}
    return {"strategy": None, "reason": "UNTARGETABLE_SHARED_KICKOFF", "games_within_tolerance": nearby}


def request_params(kickoff: datetime, target: dict[str, Any], league_id: str) -> dict[str, str]:
    """One request, one event object at most."""

    params = {
        "leagueID": league_id,
        "limit": "1",
        "startsAfter": (kickoff - TARGET_TOLERANCE).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "startsBefore": (kickoff + TARGET_TOLERANCE).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finalized": "false",
        "oddsPresent": "true",
    }
    if target.get("strategy") == "TEAM_ID":
        params["teamID"] = str(target["team_id"])
    return params


def _fresh_pair_held(session: Session, game_id: Any, since: datetime) -> bool:
    return session.scalar(
        select(OddsSnapshot.id)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .where(Market.game_id == game_id, Market.market_type == MarketType.MONEYLINE,
               OddsSnapshot.source == PROVIDER, OddsSnapshot.available_as_of >= since).limit(1)) is not None


def plan(session: Session, settings: Any, *, now: datetime | None = None,
         capacity_fn: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """Which CFB games may be priced right now, each with its reason. Writes nothing."""

    from app.cfb import paper_decision  # the checkpoint's own eligibility reference
    from app.cfb.production_import import current_forecast_for_game, present_eligibility
    from app.cfb.sgo_lane import capacity as lane_capacity
    from app.services.research_paper_policy import CFB

    moment = now or datetime.now(UTC)
    binding = load_binding(settings)
    kickoffs = schedule_kickoffs(settings)
    rows = session.execute(
        select(Game.id, Game.starts_at, ExternalIdMapping.external_id)
        .join(Season, Season.id == Game.season_id).join(League, League.id == Season.league_id)
        .join(ExternalIdMapping, (ExternalIdMapping.entity_id == Game.id)
              & (ExternalIdMapping.entity_type == "game") & (ExternalIdMapping.source == "CFBD"))
        .where(League.code == "CFB", Game.status == GameStatus.SCHEDULED,
               Game.starts_at >= moment + BUY_CLOSES, Game.starts_at <= moment + BUY_OPENS)
        .order_by(Game.starts_at, ExternalIdMapping.external_id)).all()
    games: list[dict[str, Any]] = []
    for game_id, starts_at, source_game_id in rows:
        kickoff = _aware(starts_at)
        entry: dict[str, Any] = {"game_id": str(game_id), "source_game_id": str(source_game_id),
                                 "kickoff": kickoff.isoformat()}
        forecast = current_forecast_for_game(session, str(source_game_id))
        if forecast is None or not present_eligibility(session, forecast, now=moment)["eligible"]:
            games.append({**entry, "state": "NOT_DECISION_ELIGIBLE"})
            continue
        if not paper_decision.eligibility_status(session, CFB, forecast_id=forecast.forecast_id,
                                  event_id=f"cfbd:{source_game_id}")["eligible"]:
            games.append({**entry, "state": "AWAITING_PRODUCER_ELIGIBILITY"})
            continue
        if _fresh_pair_held(session, game_id, kickoff - BUY_OPENS):
            games.append({**entry, "state": "PRICE_ALREADY_HELD"})
            continue
        target = targeting(kickoff, _cfbd_sides(session, game_id), binding, kickoffs)
        if not target.get("strategy"):
            games.append({**entry, "state": target["reason"], "target": target})
            continue
        if not binding.resolved:
            games.append({**entry, "state": "SGO_BINDING_UNRESOLVED", "target": target,
                          "binding": binding.as_dict()})
            continue
        games.append({**entry, "state": "READY", "target": target,
                      "params": request_params(kickoff, target, str(binding.league_id))})
    gate = (capacity_fn or lane_capacity)(session, settings, now=moment)
    ready = [g for g in games if g["state"] == "READY"]
    return {"checked_at": moment.isoformat(), "binding": binding.as_dict(), "capacity": gate,
            "games": games, "will_request": ready[: int(gate.get("objects_allowed") or 0)],
            "live_validation": LIVE_VALIDATION}


def bind_quote_event(session: Session, game_id: Any, quote: Any, binding: Binding) -> tuple[bool, str]:
    """Exact identities only. Returns (ok, reason)."""

    if quote.league_id != binding.league_id:
        return False, "LEAGUE_ID_MISMATCH"
    game = session.get(Game, game_id)
    if game is None or abs(quote.commence_time - _aware(game.starts_at)) > TARGET_TOLERANCE:
        return False, "START_TIME_DISAGREES_WITH_ADMITTED_KICKOFF"
    sides = _cfbd_sides(session, game_id)
    home_cfbd, away_cfbd = binding.team_map.get(quote.home_team_id), binding.team_map.get(quote.away_team_id)
    if home_cfbd is None or away_cfbd is None:
        return False, "TEAM_NOT_IN_REVIEWED_MAP"
    if (home_cfbd, away_cfbd) == ((sides.get(False) or (None, None))[1], (sides.get(True) or (None, None))[1]):
        return False, "ORIENTATION_REVERSED"
    if (home_cfbd, away_cfbd) != ((sides.get(True) or (None, None))[1], (sides.get(False) or (None, None))[1]):
        return False, "TEAMS_DISAGREE_WITH_ADMITTED_GAME"
    return True, "BOUND"


def persist(session: Session, game_id: Any, quotes: list[Any], *, available_as_of: datetime) -> dict[str, int]:
    """Write validated two-sided quotes in the shape the checkpoint reads. Idempotent."""

    from app.gen2.exchange.canonical_json import canonical_sha256
    from app.data_sources.provider_registry import identity as provider_identity

    provider = provider_identity(PROVIDER)
    sides = {is_home: team for is_home, (team, _cfbd) in _cfbd_sides(session, game_id).items()}
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
                   "book_last_updated_at": side.provider_updated_at.isoformat(), "period": quote.period_id,
                   "league_id": quote.league_id, "settlement": "full game including overtime (SGO periodID 'game')"}
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


def fetch_events(settings: Any, params: dict[str, str]) -> dict[str, Any]:
    """Exactly one HTTP request. No retry, no pagination. The key travels in a header only."""

    import httpx

    url = f"{settings.sportsgameodds_base_url.rstrip('/')}/events"
    headers = {"X-Api-Key": settings.sportsgameodds_api_key, "Accept": "application/json"}
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        response = client.get(url, params=params, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(f"SportsGameOdds returned HTTP {response.status_code}; not retried")
    return response.json()


def collect(session: Session, settings: Any, *, now: datetime | None = None,
            fetch: Callable[[Any, dict[str, str]], dict[str, Any]] | None = None,
            capacity_fn: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """Spend at most the genuinely uncommitted capacity on READY games. Zero otherwise."""

    from app.data_sources.sgo_parser import parse_main_markets
    from app.services import provider_allocation as pa
    from app.services.sgo_collection import complete_request, object_budget, record_request

    moment = now or datetime.now(UTC)
    if not getattr(settings, "cfb_sgo_lane_enabled", False):
        return {"status": "LANE_DISABLED", "objects_spent": 0}
    planned = plan(session, settings, now=moment, capacity_fn=capacity_fn)
    summary: dict[str, Any] = {"status": "PLANNED", "objects_spent": 0, "requests": [],
                               "capacity": planned["capacity"], "binding": planned["binding"],
                               "games": [{k: g[k] for k in ("source_game_id", "state")} for g in planned["games"]]}
    if not planned["will_request"]:
        summary["status"] = (planned["capacity"].get("state") if int(planned["capacity"].get("objects_allowed") or 0) < 1
                             else "NO_READY_GAME")
        return summary
    binding = load_binding(settings)
    fetcher = fetch or fetch_events
    for game in planned["will_request"]:
        guard_b = object_budget(session, now=moment)
        guard_a = pa.build_plan(session, now=moment).as_dict()
        guard_b_ok, guard_b_reason = guard_b.may_spend(1)
        if not guard_b_ok or int(guard_a.get("spendable") or 0) < 1:
            summary["requests"].append({"source_game_id": game["source_game_id"], "state": "GUARD_REFUSED",
                                         "guard_b": guard_b_reason, "guard_a_spendable": guard_a.get("spendable")})
            break
        reservation = record_request(session, requested_at=moment, params=game["params"], budget=guard_b,
                                     endpoint_key=LANE, objects_requested=1)
        session.commit()
        try:
            payload = fetcher(settings, game["params"])
        except Exception as error:  # noqa: BLE001 -- recorded, never retried
            complete_request(session, reservation, objects=0, succeeded=False, budget=guard_b,
                             error=str(error)[:200], endpoint_key=LANE)
            session.commit()
            summary["requests"].append({"source_game_id": game["source_game_id"], "state": "REQUEST_FAILED"})
            continue
        objects = len(payload.get("data") or [])
        complete_request(session, reservation, objects=objects, succeeded=True, budget=guard_b, endpoint_key=LANE,
                         extra_metadata={"league_id": binding.league_id, "cfbd_game_id": game["source_game_id"]})
        summary["objects_spent"] += objects
        parsed = parse_main_markets(payload, received_at=moment, now=moment, league_id=str(binding.league_id))
        outcome: dict[str, Any] = {"source_game_id": game["source_game_id"], "objects": objects,
                                   "quotes": len(parsed.quotes), "parse_rejections": parsed.rejected[:6]}
        bound = []
        for quote in parsed.quotes:
            ok, reason = bind_quote_event(session, uuid_of(game["game_id"]), quote, binding)
            if not ok:
                outcome.setdefault("binding_refusals", []).append(
                    {"event_id": quote.event_id, "reason": reason, "home_team_id": quote.home_team_id,
                     "away_team_id": quote.away_team_id, "home_name": quote.home_team_name,
                     "away_name": quote.away_team_name})
                continue
            bound.append(quote)
        outcome["persisted"] = persist(session, uuid_of(game["game_id"]), bound, available_as_of=moment)
        session.commit()
        summary["requests"].append(outcome)
    summary["status"] = "COLLECTED"
    return summary


def uuid_of(value: Any) -> Any:
    import uuid

    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


__all__ = ["LANE", "LIVE_VALIDATION", "Binding", "bind_quote_event", "collect", "fetch_events", "load_binding",
           "persist", "plan", "request_params", "schedule_kickoffs", "targeting"]
