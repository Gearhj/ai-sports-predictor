# PM current event-time adapter; original clocks and policy remain.
"""NHL prices from The Odds API (``icehockey_nhl``, ``h2h``): plan, bind, persist. Built and OFF.

WHAT THIS IS
------------
The native price path for AISP_NHL_PAPER_POLICY_V1 (``app.nhl.paper``), whose
ONLY permitted quote source is The Odds API. A provider event is bound to
exactly one AISP NHL game, and each book's ``h2h`` market -- the full-game
TWO-WAY moneyline, overtime and shootout included -- is stored as one
``OddsSnapshot`` per side, both sides from ONE provider, ONE book and ONE market
row stored at ``paper.MARKET_PERIOD``, in the shape the checkpoint pairs
(``HOME:None`` / ``AWAY:None``). It reuses the CFB binder's name normaliser and
refusal vocabulary; it does not go through ``ingest_odds``
(``odds_league.PROVIDER_LEAGUES`` still refuses ``icehockey_nhl`` there).

THE COLLECTOR (``collect-nhl-odds-api``, cloned from the CFB/NBA lanes)
-----------------------------------------------------------------------
``plan`` says whether one request is due now; the orchestrator then reserves it
in the provider ledger through every existing Odds API guard plus the
``NHL_RESEARCH`` credit lane (zero unless funded), fetches with the existing
``TheOddsAPIAdapter``, archives the raw response and hands the events to
``bind_events`` / ``persist`` below. Shipped OFF: ``NHL_ODDS_API_ENABLED`` is
false (the scheduler does not even register the job) and the lane is unfunded.
A regulation three-way market (``h2h_3_way``, or any market with a draw) is
never requested or stored, so it can never stand in for the two-way price.

GAME IDENTITY
-------------
NHL teams are stored with their full names ("Toronto Maple Leafs"), which is how
the provider names them. An event binds only when ALL of these hold (the same
rules, tolerance and windows as the reviewed CFB and NBA binders):

1. BOTH names equal, after accent/punctuation normalisation, exactly one NHL
   team each ("Montréal" = "Montreal", "St. Louis" = "St Louis"); anything else
   is TEAM_NAME_UNMATCHED / TEAM_NAME_AMBIGUOUS, never guessed;
2. those are exactly the game's home and away teams in that orientation
   (reversed is refused as ORIENTATION_REVERSED, never flipped);
3. the provider start EQUALS the admitted start (``START_TOLERANCE`` is zero,
   as CFB's ``KICKOFF_TOLERANCE`` and NBA's ``TIPOFF_TOLERANCE``). Any gap is
   an UNRESOLVED disagreement -- AISP cannot tell which clock is right, and
   funding against the later one could buy after the real start -- so the
   event is refused as KICKOFF_DISAGREEMENT (with ``gap_minutes``) and nothing
   is stored;
4. no second admitted game with the same teams lies within
   ``AMBIGUITY_WINDOW`` of the provider start (an exact match does not pick
   between two), exactly one game matches, no other event in the response binds
   the same game, and no earlier mapping ties the event or the game elsewhere;
5. the event has not started at receipt and the game is still SCHEDULED.

Anything else is refused for that event alone, with a named receipt.

CLOCKS (never mixed): ``observed_at`` is the book's own market ``last_update``
(or the bookmaker ``last_update``, labelled as such); ``requested_at`` and
``available_as_of`` (= response receipt) travel in ``raw_payload``, together
with the provider start the quote was retained at (``event_commence_time``).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session
from app.services.event_time_reconciliation import (
    MATCH_WINDOW, SLATE_TIMEZONE, CONTRACT, assess_clock, reconcile_clock_identity,
    resolve_candidate, make_binding, utc as event_utc,
)

from app.cfb.odds_api_price import (
    BOOK_CLOCK, BOUND, CLOCK_SEMANTICS, CLOCK_SKEW, EVENT_ALREADY_STARTED, EVENT_MAPPED_TO_ANOTHER_GAME,
    GAME_MAPPED_TO_ANOTHER_EVENT, GAME_NOT_SCHEDULED, INVALID_AMERICAN_PRICE, KICKOFF_DISAGREEMENT, MARKET_CLOCK,
    MARKET_NOT_TWO_SIDED, MORE_THAN_ONE_EVENT_BINDS_THIS_GAME, MORE_THAN_ONE_GAME_MATCHES,
    ORIENTATION_REVERSED, OUTCOME_NAMES_DISAGREE_WITH_EVENT, QUOTE_CLOCK_AFTER_RECEIPT, TEAM_NAME_AMBIGUOUS,
    TEAM_NAME_UNMATCHED, _utc, normalise,
)
from app.models import (
    ExternalIdMapping, Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType, OddsSnapshot,
    ProviderRequestLedger, Sportsbook, Team,
)
from app.models.nhl_research import NHL_GAME_SOURCE, NHL_LEAGUE_CODE
from app.nhl import paper as _paper_pins
from app.nhl.paper import MARKET_PERIOD, PROVIDER_MARKET, PROVIDER_SPORT_KEY, PROVIDER_START_GRACE
from app.services.odds_request_window import ALREADY_BOUGHT, MAX_ATTEMPTS_PER_WINDOW, window_requests

PROVIDER = "THE_ODDS_API"
SPORT_KEY = PROVIDER_SPORT_KEY
#: Ledger endpoint key. Contains ``icehockey_nhl`` so the credit-lane
#: attribution files it under the NHL lane, never under any other.
ENDPOINT_KEY = "icehockey_nhl_odds"
#: Moneyline only: the NHL policy prices nothing else, and every extra market
#: is another credit per region. ``h2h`` is the two-way market incl. OT/SO.
MARKETS: tuple[str, ...] = (PROVIDER_MARKET,)
LEAGUE_CODE = NHL_LEAGUE_CODE
NATIVE_SOURCE = NHL_GAME_SOURCE
#: Provider vs admitted start. The Odds API lists NHL games at puck drop, about
#: 10 min after the scheduled start, so a provider start in [admitted, admitted +
#: 15 min] binds (paper.PROVIDER_START_GRACE); earlier or later is refused.
START_TOLERANCE = MATCH_WINDOW
#: A cluster whose request SUCCEEDED but bound no quote for a due game may retry once, this long after
#: that request (fcc5e46). FAILED and interrupted requests follow ``app.services.odds_request_window``.
RETRY_UNBOUND_AFTER = timedelta(minutes=9)
#: Reservations one buy window may hold, whatever the reason for the retry: the request and one retry.
MAX_REQUESTS_PER_WINDOW = MAX_ATTEMPTS_PER_WINDOW
#: How far from the provider start a same-teams game is still named as a start
#: disagreement (rather than "no game"). Naming only; never binds.
NAMING_WINDOW = timedelta(days=2)
#: Two admitted same-teams games this close to the provider start make the
#: event ambiguous even if one of them matches exactly: refused, never picked.
AMBIGUITY_WINDOW = timedelta(hours=12)
#: The buy window: a pair bought here is fresh at the T-30..T-15 checkpoint.
BUY_OPENS = timedelta(minutes=75)
BUY_CLOSES = timedelta(minutes=35)

#: AISP in-process NHL Gen1 forecasts (interface contract 2026-10-16): the
#: evidence status they carry and the frozen configuration (sha256 of the
#: producer's selected_model.json) they must be computed from. One definition,
#: the paper lifecycle's, so the price lane can never pin a different value.
IN_PROCESS_EVIDENCE_STATUS = _paper_pins.IN_PROCESS_EVIDENCE_STATUS
IN_PROCESS_CONFIGURATION_SHA256 = _paper_pins.IN_PROCESS_CONFIGURATION_SHA256

NO_NHL_GAME_FOR_TEAMS = "NO_NHL_GAME_FOR_TEAMS"
NOT_THE_TWO_WAY_MARKET = "NOT_THE_TWO_WAY_MARKET"

# -- plan states (one per in-window game) ----------------------------------------
DUE = "DUE"
NO_FORECAST = "NO_FORECAST"
NOT_DECISION_ELIGIBLE = "NOT_DECISION_ELIGIBLE"
ALREADY_REQUESTED_IN_WINDOW = "ALREADY_REQUESTED_IN_WINDOW"


def _receipt(event: Any, reason: str, **detail: Any) -> dict[str, Any]:
    row = {"reason": reason, "source": PROVIDER, "sport_key": SPORT_KEY,
           "source_event_id": str(getattr(event, "source_event_id", "") or ""),
           "home_team": getattr(event, "home_team", None), "away_team": getattr(event, "away_team", None),
           "commence_time": _utc(event.commence_time).isoformat() if getattr(event, "commence_time", None) else None}
    row.update({k: (str(v) if v is not None and not isinstance(v, (int, float, bool, list)) else v)
                for k, v in detail.items()})
    return row


def nhl_teams(session: Session) -> dict[str, set[Any]]:
    out: dict[str, set[Any]] = {}
    for team_id, name in session.execute(select(Team.id, Team.name).join(League, League.id == Team.league_id)
                                         .where(League.code == NHL_LEAGUE_CODE)).all():
        out.setdefault(normalise(name), set()).add(team_id)
    return out


def _games(session: Session, start: datetime, end: datetime) -> list[dict[str, Any]]:
    games = []
    for game_id, starts_at, status in session.execute(
            select(Game.id, Game.starts_at, Game.status).join(League, League.id == Game.league_id)
            .where(League.code == NHL_LEAGUE_CODE, Game.starts_at >= start, Game.starts_at <= end)).all():
        sides = {s.is_home: s.team_id for s in session.scalars(select(GameTeam).where(GameTeam.game_id == game_id))}
        if True in sides and False in sides:
            games.append({"game_id": game_id, "starts_at": _utc(starts_at), "status": status,
                          "home": sides[True], "away": sides[False]})
    return games


def bind_events(session: Session, events: Iterable[Any], *, received_at: datetime,
                ) -> tuple[list[tuple[Any, Any]], list[dict[str, Any]]]:
    """Bind each NHL event to at most one AISP NHL game. Every event yields exactly one receipt."""

    events, received_at = list(events), _utc(received_at)
    if not events:
        return [], []
    teams = nhl_teams(session)
    kicks = [_utc(e.commence_time) for e in events]
    # A wide window so a near-miss can be named (start moved, reversed) rather
    # than reported as "no game".
    games = _games(session, min(kicks) - NAMING_WINDOW, max(kicks) + NAMING_WINDOW)
    receipts, tentative = [], {}
    for event in events:
        kick = _utc(event.commence_time)
        if kick <= received_at:
            receipts.append(_receipt(event, EVENT_ALREADY_STARTED))
            continue
        ids = [teams.get(normalise(event.home_team), set()), teams.get(normalise(event.away_team), set())]
        if any(len(i) == 0 for i in ids) or any(len(i) > 1 for i in ids):
            receipts.append(_receipt(event, TEAM_NAME_UNMATCHED if any(len(i) == 0 for i in ids)
                                     else TEAM_NAME_AMBIGUOUS))
            continue
        home_id, away_id = next(iter(ids[0])), next(iter(ids[1]))
        # Complete Eastern same-day identity set, never nearest occurrence.
        same_day = [g for g in games if g["home"] == home_id and g["away"] == away_id
                    and g["starts_at"].astimezone(SLATE_TIMEZONE).date() == kick.astimezone(SLATE_TIMEZONE).date()]
        if len(same_day) > 1:
            receipts.append(_receipt(event, MORE_THAN_ONE_GAME_MATCHES,
                                     candidates=[str(g["game_id"]) for g in same_day]))
            continue
        reversed_ = [g for g in games if g["home"] == away_id and g["away"] == home_id
                     and abs(g["starts_at"] - kick) <= MATCH_WINDOW]
        declared_sport = getattr(event, "sport_key", None)
        if declared_sport is not None and declared_sport != SPORT_KEY:
            receipts.append(_receipt(event, "SPORT_KEY_MISMATCH"))
            continue
        candidate = resolve_candidate(
            sport=LEAGUE_CODE, home_id=str(home_id), away_id=str(away_id),
            provider_start=kick, observed_at=received_at,
            games=[{**g, "sport": LEAGUE_CODE, "home": str(g["home"]), "away": str(g["away"])} for g in same_day])
        if not candidate["accepted"]:
            reason = ORIENTATION_REVERSED if reversed_ and not same_day else candidate["reason"]
            receipts.append(_receipt(event, reason, clock_reconciliation=candidate))
            continue
        game = next(g for g in same_day if str(g["game_id"]) == candidate["game_id"])
        native_ids = list(session.scalars(select(ExternalIdMapping.external_id).where(
            ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == NATIVE_SOURCE,
            ExternalIdMapping.entity_id == game["game_id"])))
        if len(native_ids) != 1 or len(list(session.scalars(select(ExternalIdMapping.entity_id).where(
                ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == NATIVE_SOURCE,
                ExternalIdMapping.external_id == native_ids[0])))) != 1:
            receipts.append(_receipt(event, "NATIVE_GAME_IDENTITY_MISSING_OR_AMBIGUOUS", game_id=game["game_id"]))
            continue
        tentative.setdefault(game["game_id"], []).append(event)
    bound = []
    for game_id, claims in tentative.items():
        if len(claims) > 1:
            receipts += [_receipt(e, MORE_THAN_ONE_EVENT_BINDS_THIS_GAME, game_id=game_id,
                                  events=[str(c.source_event_id) for c in claims]) for e in claims]
            continue
        event, event_id = claims[0], str(claims[0].source_event_id)
        mapped = session.scalar(select(ExternalIdMapping.entity_id).where(
            ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == PROVIDER,
            ExternalIdMapping.external_id == event_id))
        if mapped is not None and str(mapped) != str(game_id):
            receipts.append(_receipt(event, EVENT_MAPPED_TO_ANOTHER_GAME, game_id=game_id, mapped_game_id=mapped))
            continue
        other = session.scalar(select(ExternalIdMapping.external_id).where(
            ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == PROVIDER,
            ExternalIdMapping.entity_id == game_id, ExternalIdMapping.external_id != event_id))
        if other is not None:
            receipts.append(_receipt(event, GAME_MAPPED_TO_ANOTHER_EVENT, game_id=game_id, mapped_event_id=other))
            continue
        # The BOUND receipt's clock reading must use an AWARE canonical start: a raw column read is
        # naive on SQLite and the receipt then said MISSING_OR_INVALID_EVENT_CLOCK on a bound event.
        receipts.append(_receipt(event, BOUND, game_id=game_id, clock_reconciliation=assess_clock(
            _utc(session.scalar(select(Game.starts_at).where(Game.id == game_id))), event.commence_time, received_at).receipt()))
        bound.append((event, game_id))
    return bound, receipts


def _valid_american(price: Any) -> bool:
    return isinstance(price, int) and not isinstance(price, bool) and abs(price) >= 100


def _retained_start(raw: dict[str, Any] | None) -> datetime | None:
    text = (raw or {}).get("event_commence_time")
    if not text:
        return None
    try:
        return _utc(datetime.fromisoformat(str(text).replace("Z", "+00:00")))
    except ValueError:
        return None


def persist(session: Session, bound: list[tuple[Any, Any]], *, requested_at: datetime, received_at: datetime,
            response: dict[str, Any] | None = None, admission_asof: datetime | None = None) -> dict[str, Any]:
    """Store each bound event's validated two-way ``h2h`` pairs. Idempotent.

    A row's identity is (outcome, book, quote clock, price, provider) AND the
    provider start it was retained at (the CFB precedent): after a schedule
    change, the same price at the same book clock bound at the NEW start is a
    new row, because the checkpoint only pairs legs retained at the game's
    current start.
    """

    from app.data_sources.provider_registry import identity as provider_identity
    from app.gen2.exchange.canonical_json import canonical_sha256

    provider = provider_identity(PROVIDER)
    requested_at, received_at = _utc(requested_at), _utc(received_at)
    available_at = max(received_at, _utc(admission_asof)) if admission_asof is not None else received_at
    created = existing = 0
    receipts: list[dict[str, Any]] = []
    for event, game_id in bound:
        sides = {s.is_home: s.team_id for s in session.scalars(select(GameTeam).where(GameTeam.game_id == game_id))}
        canonical_start = _utc(session.scalar(select(Game.starts_at).where(Game.id == game_id)))
        try:
            binding = make_binding(game_id=game_id, sport=LEAGUE_CODE,
                home_id=sides.get(True), away_id=sides.get(False),
                provider_event_id=str(event.source_event_id), canonical_start=canonical_start,
                provider_start=_utc(event.commence_time), received_at=received_at)
            available_check = assess_clock(canonical_start, event.commence_time, available_at)
            if not available_check.accepted:
                raise ValueError(available_check.reason)
        except (TypeError, ValueError) as error:
            receipts.append(_receipt(event, "EVENT_CLOCK_BINDING_REFUSED", detail=str(error)))
            continue

        commence = _utc(event.commence_time)
        wrote = False
        for book in event.bookmakers:
            for dto in book.markets:
                if dto.key != PROVIDER_MARKET:
                    receipts.append(_receipt(event, NOT_THE_TWO_WAY_MARKET, book=book.key, market=dto.key))
                    continue
                names = [o.name for o in dto.outcomes]
                if len(names) != 2 or len(set(names)) != 2:
                    receipts.append(_receipt(event, MARKET_NOT_TWO_SIDED, book=book.key, outcomes=len(names)))
                    continue
                by_name = {o.name: o for o in dto.outcomes}
                if set(by_name) != {event.home_team, event.away_team}:
                    receipts.append(_receipt(event, OUTCOME_NAMES_DISAGREE_WITH_EVENT, book=book.key,
                                             outcome_names=sorted(by_name)))
                    continue
                if not all(_valid_american(o.price) for o in dto.outcomes):
                    receipts.append(_receipt(event, INVALID_AMERICAN_PRICE, book=book.key))
                    continue
                clock = _utc(dto.last_update)
                clock_source = BOOK_CLOCK if getattr(dto, "clock_source", None) == "BOOK" else MARKET_CLOCK
                if clock > received_at + CLOCK_SKEW:
                    receipts.append(_receipt(event, QUOTE_CLOCK_AFTER_RECEIPT, book=book.key, last_update=clock.isoformat()))
                    continue
                book_key = provider.bookmaker_key(book.key)
                sportsbook = session.scalar(select(Sportsbook).where(Sportsbook.key == book_key))
                if sportsbook is None:
                    sportsbook = Sportsbook(key=book_key, name=book.title, region="us", active=True)
                    session.add(sportsbook)
                    session.flush()
                market = session.scalar(select(Market).where(Market.game_id == game_id,
                                                             Market.market_type == MarketType.MONEYLINE,
                                                             Market.period == MARKET_PERIOD))
                if market is None:
                    market = Market(game_id=game_id, market_type=MarketType.MONEYLINE, period=MARKET_PERIOD)
                    session.add(market)
                    session.flush()
                for side, is_home, name in (("HOME", True, event.home_team), ("AWAY", False, event.away_team)):
                    quote = by_name[name]
                    outcome = session.scalar(select(MarketOutcome).where(MarketOutcome.market_id == market.id,
                                                                         MarketOutcome.outcome_key == f"{side}:None"))
                    if outcome is None:
                        outcome = MarketOutcome(market_id=market.id, outcome_key=f"{side}:None", label=side,
                                                participant_team_id=sides.get(is_home))
                        session.add(outcome)
                        session.flush()
                    prior_starts = [_retained_start(raw) for raw in session.scalars(
                        select(OddsSnapshot.raw_payload).where(
                            OddsSnapshot.market_outcome_id == outcome.id, OddsSnapshot.sportsbook_id == sportsbook.id,
                            OddsSnapshot.observed_at == clock, OddsSnapshot.american_odds == int(quote.price),
                            OddsSnapshot.source == PROVIDER))]
                    if commence == canonical_start and commence in prior_starts:
                        existing += 1
                        continue
                    proved_prior = any(
                        isinstance(r, dict) and all((r.get('event_clock_binding') or {}).get(k) == binding.get(k)
                            for k in ('contract','game_id','sport','home_id','away_id','provider_event_id',
                                      'canonical_start','provider_start'))
                        for r in session.scalars(select(OddsSnapshot.raw_payload).where(
                            OddsSnapshot.market_outcome_id == outcome.id, OddsSnapshot.sportsbook_id == sportsbook.id,
                            OddsSnapshot.observed_at == clock, OddsSnapshot.american_odds == int(quote.price),
                            OddsSnapshot.source == PROVIDER)))
                    if proved_prior:
                        existing += 1
                        continue
                    decimal = 1 + (quote.price / 100.0 if quote.price > 0 else 100.0 / abs(quote.price))
                    raw = {"provider_event_id": str(event.source_event_id), "sport_key": SPORT_KEY,
                           "market": PROVIDER_MARKET, "book": book.key, "book_title": book.title, "side": side,
                           "provider_team_name": name, "american": int(quote.price),
                           "quote_clock_source": clock_source,
                           "market_last_update": clock.isoformat() if clock_source == MARKET_CLOCK else None,
                           "book_last_update": _utc(book.last_update).isoformat(),
                           "event_commence_time": commence.isoformat(),
                           "requested_at": requested_at.isoformat(), "response_received_at": received_at.isoformat(),
                           "settlement": "full game two-way, including overtime and shootout (The Odds API h2h)",
                           "settlement_basis": "ASSERTED_BY_AISP_NOT_STATED_BY_PROVIDER",
                           "clock_semantics": CLOCK_SEMANTICS, **(response or {})}
                    raw["event_clock_binding"] = binding
                    raw["admitted_at"] = available_at.isoformat()
                    session.add(OddsSnapshot(
                        market_outcome_id=outcome.id, sportsbook_id=sportsbook.id, observed_at=clock,
                        available_as_of=available_at, american_odds=int(quote.price), decimal_odds=decimal, point=None,
                        implied_probability=1.0 / decimal, source=PROVIDER, source_event_id=str(event.source_event_id),
                        source_mode="LIVE", raw_payload=raw, payload_checksum=canonical_sha256(raw)))
                    session.flush()
                    created += 1
                wrote = True
        if wrote and session.scalar(select(ExternalIdMapping.id).where(
                ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == PROVIDER,
                ExternalIdMapping.external_id == str(event.source_event_id))) is None:
            session.add(ExternalIdMapping(entity_type="game", entity_id=game_id, source=PROVIDER,
                                          external_id=str(event.source_event_id)))
            session.flush()
    return {"created": created, "existing": existing, "quote_receipts": receipts}


# --------------------------------------------------------------------- the plan --
def decision_capability(session: Session, forecast: Any, *, now: datetime, starts_at: datetime) -> dict[str, Any]:
    """Could this held forecast reach a decision? Only then is a credit worth spending on its game.

    * An AISP in-process forecast (``producer_evidence_status`` =
      ``AISP_IN_PROCESS_GENERATED``) qualifies only when the paper lifecycle's own
      ``in_process_eligibility`` accepts it as of now (pinned configuration,
      model id/version, evidence mode, record class, clocks, current start). At a
      buy-window moment (<= T-35) that is the same verdict the checkpoint will
      reach, so the lane and the decision can never disagree on what is pinned.
    * A producer-published forecast keeps the producer route: its
      ``ASP_NHL_PAPER_RESEARCH_ELIGIBILITY_V1`` record must already be held.
    """

    from app.nhl.paper import eligibility_record, in_process_eligibility

    if forecast.producer_evidence_status == IN_PROCESS_EVIDENCE_STATUS:
        record = in_process_eligibility(forecast, moment=now, starts_at=starts_at)
        ok = bool(record["eligible"])
        return {"capable": ok, "route": "AISP_IN_PROCESS", "detail": None if ok else record["detail"]}
    record = eligibility_record(session, forecast, moment=now, starts_at=starts_at)
    return {"capable": bool(record.get("eligible")), "route": "PRODUCER_ELIGIBILITY", "detail": record.get("state")}



def effective_start(session: Session, game_id: Any, canonical_start: datetime, *, moment: datetime) -> datetime:
    """Earliest held, internally bound event clock; immutable original clocks are never replaced.
    No unproved raw timestamp, future availability, changed schedule or other sport can move it.
    """
    canonical, now = _utc(canonical_start), _utc(moment)
    sides = {s.is_home: s.team_id for s in session.scalars(select(GameTeam).where(GameTeam.game_id == game_id))}
    earliest = canonical
    rows = session.execute(select(OddsSnapshot.raw_payload, OddsSnapshot.available_as_of,
        OddsSnapshot.observed_at, OddsSnapshot.source_event_id)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .where(Market.game_id == game_id, OddsSnapshot.source == PROVIDER)).all()
    for raw, available, observed, event_id in rows:
        if not isinstance(raw, dict) or not isinstance(raw.get("event_clock_binding"), dict): continue
        b = raw["event_clock_binding"]
        expected = {"contract": CONTRACT, "game_id": str(game_id), "sport": LEAGUE_CODE,
            "home_id": str(sides.get(True)), "away_id": str(sides.get(False)),
            "canonical_start": canonical.isoformat(), "provider_event_id": str(event_id)}
        if any(b.get(k) != v for k,v in expected.items()): continue
        try:
            provider = event_utc(raw["event_commence_time"])
            check = reconcile_clock_identity(canonical, provider)
            if (check.accepted and b.get("provider_start") == provider.isoformat()
                    and event_utc(b["bound_at"]) == event_utc(raw["response_received_at"])
                    and _utc(observed) <= event_utc(b["bound_at"]) <= _utc(available) <= now):
                earliest = min(earliest, provider)
        except (KeyError, TypeError, ValueError): continue
    return earliest

def plan(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Is a request due now? One request prices every NHL event, so the answer is per tick.

    Due when at least one SCHEDULED NHL game is inside its buy window
    (T-75..T-35) with an admissible NHL FORECAST that AISP holds now
    (``paper.pregame_forecast``: real record class, the policy's model,
    published <= imported <= now, made for the game's current start) and that
    could reach a decision (``decision_capability``), and no NHL Odds API
    request since that game's buy window opened still counts
    (``app.services.odds_request_window``): one that SUCCEEDED, or one still in
    flight. So each start-time cluster costs one request, and at most one retry
    (two reservations per window), each through every existing guard:

    * a FAILED (or interrupted) request is retried on a later tick inside the
      same window (2026-10-17, ``odds_request_window``);
    * a request that SUCCEEDED but bound no quote for this game since the window
      opened (e.g. the provider listed it outside the puck-drop grace) is retried
      once, ``RETRY_UNBOUND_AFTER`` after the latest reservation (fcc5e46). A
      game the provider does not offer therefore costs its cluster two requests
      at most, never more.
    """

    from app.nhl.paper import pregame_forecast

    moment = _utc(now or datetime.now(UTC))
    rows = session.execute(
        select(Game.id, Game.starts_at, ExternalIdMapping.external_id)
        .join(League, League.id == Game.league_id)
        .join(ExternalIdMapping, (ExternalIdMapping.entity_id == Game.id)
              & (ExternalIdMapping.entity_type == "game") & (ExternalIdMapping.source == NHL_GAME_SOURCE))
        .where(League.code == LEAGUE_CODE, Game.status == GameStatus.SCHEDULED,
               Game.starts_at >= moment + BUY_CLOSES, Game.starts_at <= moment + BUY_OPENS + MATCH_WINDOW)
        .order_by(Game.starts_at, ExternalIdMapping.external_id)).all()
    games, due = [], []
    for game_id, starts_at, canonical in rows:
        start = _utc(starts_at)
        operational = effective_start(session, game_id, start, moment=moment)
        if not moment + BUY_CLOSES <= operational <= moment + BUY_OPENS:
            continue
        source_id = str(canonical).rsplit(":", 1)[-1]
        entry = {"game_id": str(game_id), "source_game_id": source_id, "starts_at": start.isoformat(), "operational_start": operational.isoformat()}
        forecast, _excluded = pregame_forecast(session, game_id, moment=moment, starts_at=start)
        if forecast is None:
            games.append({**entry, "state": NO_FORECAST})
            continue
        capability = decision_capability(session, forecast, now=moment, starts_at=start)
        if not capability["capable"]:
            games.append({**entry, "state": NOT_DECISION_ELIGIBLE, "forecast_id": forecast.forecast_id,
                          "route": capability["route"], "detail": capability["detail"]})
            continue
        opened = operational - BUY_OPENS
        window = window_requests(session, provider=PROVIDER, endpoint_key=ENDPOINT_KEY, opened=opened, now=moment)
        state = ALREADY_REQUESTED_IN_WINDOW if window["blocking"] is not None else DUE
        if (window["blocking"] == ALREADY_BOUGHT and window["attempts"] < MAX_REQUESTS_PER_WINDOW
                and _unbound_retry_owed(session, game_id, opened=opened, moment=moment)):
            # The window's one request SUCCEEDED but priced nothing for this game (e.g. it was
            # refused at binding): one retry, 9+ min later, bounded by MAX_REQUESTS_PER_WINDOW
            # and every lane guard (fcc5e46).
            state = DUE
        game = {**entry, "state": state, "forecast_id": forecast.forecast_id, "route": capability["route"]}
        if window["attempts"]:
            game["window_requests"] = window
        games.append(game)
        if state == DUE:
            due.append(entry)
    return {"due": bool(due), "due_games": due, "games": games, "evaluated_at": moment.isoformat(),
            "markets": list(MARKETS), "sport_key": SPORT_KEY}


def _unbound_retry_owed(session: Session, game_id: Any, *, opened: datetime, moment: datetime) -> bool:
    """fcc5e46: no quote from this provider is bound to the game since its window opened, and the
    window's latest reservation is at least ``RETRY_UNBOUND_AFTER`` old."""

    bound = session.scalar(select(OddsSnapshot.id).join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
                           .join(Market, Market.id == MarketOutcome.market_id)
                           .where(Market.game_id == game_id, OddsSnapshot.source == PROVIDER,
                                  OddsSnapshot.available_as_of >= opened).limit(1))
    if bound is not None:
        return False
    latest = session.scalar(select(ProviderRequestLedger.requested_at).where(
        ProviderRequestLedger.provider == PROVIDER, ProviderRequestLedger.endpoint_key == ENDPOINT_KEY,
        ProviderRequestLedger.status == "RESERVED", ProviderRequestLedger.requested_at >= opened)
        .order_by(ProviderRequestLedger.requested_at.desc()).limit(1))
    return latest is not None and moment - _utc(latest) >= RETRY_UNBOUND_AFTER


__all__ = [
    "AMBIGUITY_WINDOW", "BOUND", "BUY_CLOSES", "BUY_OPENS", "ENDPOINT_KEY", "IN_PROCESS_CONFIGURATION_SHA256",
    "IN_PROCESS_EVIDENCE_STATUS", "KICKOFF_DISAGREEMENT", "MARKETS", "NOT_THE_TWO_WAY_MARKET", "PROVIDER",
    "SPORT_KEY", "START_TOLERANCE", "bind_events", "decision_capability", "nhl_teams", "persist", "plan",
]
