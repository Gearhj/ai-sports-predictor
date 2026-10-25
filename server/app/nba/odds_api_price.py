"""OWNER RELIABILITY REVISION: same-event matching allows +/-2h, not later betting.
Exact teams/orientation, unique occurrence and provider mappings still apply.
BOTH quote legs retain canonical/provider clocks; decision uses earliest clock.
Scheduled-game quote collection no longer waits for a forecast discovery lease.
Failed/interrupted reservations use the existing bounded retry helper, within
unchanged quotas. Older descriptive comments below document prior behavior.

NBA prices from The Odds API (``basketball_nba``): plan, bind, persist. Built and OFF.

WHAT THIS IS
------------
A second, PROSPECTIVE quote source for the unchanged AISP_NBA_PAPER_POLICY_V1
checkpoint (``app.nba.gen1_paper``), cloned from the reviewed CFB lane
(``app.cfb.odds_api_price``). SportsGameOdds -- the frozen policy's only
permitted source -- has no NBA authority in October, so without this the NBA
checkpoint has no price source on opening night (2026-10-20).

It reuses the existing Odds API adapter (``app.data_sources.odds.TheOddsAPIAdapter``),
the existing provider ledger / quota / credit-lane guards in the orchestrator
(plus its own ``NBA_RESEARCH`` credit lane, zero unless funded), and writes
quotes in the exact shape the checkpoint already reads
(``nfl_paper_decision.paired_quotes``): ``HOME:None`` / ``AWAY:None``
moneyline outcomes, one ``OddsSnapshot`` per side, both sides from ONE
provider, ONE book and ONE market.

SHIPPED OFF, UNFUNDED AND NOT EFFECTIVE
---------------------------------------
* ``NBA_ODDS_API_ENABLED`` is false: the collector requests nothing, and the
  scheduler registers it only when that AND ``NBA_PRODUCTION_CONSUMER_ENABLED``
  are both on;
* ``ODDS_NBA_LANE_MONTHLY_CREDITS`` / ``_DAILY_CREDITS`` are 0: even switched
  on, the ``NBA_RESEARCH`` lane refuses every request until funded;
* ``NBA_ODDS_API_SOURCE_EFFECTIVE_FROM`` is unset: the appended source
  amendment (``research_paper_policy.SOURCE_AMENDMENTS``) is not effective, so
  the checkpoint still permits SportsGameOdds alone and refuses any stored
  Odds API pair as NO_PERMITTED_PROVIDER.
Activation is an owner decision, not this module's.

It does NOT go through ``CanonicalNormalizer.ingest_odds``: that binder keys
outcomes on provider team names and its participant matcher is not the
identity rule NBA needs. ``odds_league.PROVIDER_LEAGUES`` therefore still
refuses ``basketball_nba`` for ``ingest_odds``.

GAME IDENTITY (the only place a provider event meets an NBA game)
-----------------------------------------------------------------
AISP's NBA teams are bound to Codex franchise ids (``nba-neil:franchise:<CODE>``,
``external_id_mappings`` source ``NBA_NEIL_FRANCHISE``; ``Team.abbreviation``
carries the same code). The Odds API names a team in full ("Boston Celtics").
:data:`ODDS_API_TEAM_FRANCHISES` is the explicit, closed 30-team map between
the two -- no fuzzy match, no prefix rule, no normalisation beyond trimming
whitespace. An event binds to a game only when ALL of these hold:

1. both provider names are EXACTLY a key of the map, and each mapped franchise
   resolves to exactly one AISP team through its franchise mapping (an
   unmapped name, a franchise AISP has not admitted, or a franchise bound to
   two teams is refused, never guessed);
2. the resolved home and away teams are exactly the game's home and away
   teams, in that orientation (reversed is refused, never flipped);
3. the provider tip-off EQUALS the admitted tip-off (``TIPOFF_TOLERANCE`` is
   zero, as for CFB). Any disagreement is unresolved -- AISP cannot tell which
   clock is right, and funding against the later one could buy after the real
   start -- so the event is refused as ``TIPOFF_DISAGREEMENT``;
4. exactly one game satisfies 1-3, no other event in the same response binds
   the same game, and no earlier THE_ODDS_API mapping ties either the event or
   the game to something else;
5. the event has not started at fetch time and the game is still SCHEDULED.

Anything else is refused FOR THAT EVENT ALONE with a named receipt; no other
game is ever substituted and the rest of the response still binds.

QUOTE VALIDATION AND CLOCKS
---------------------------
Exactly as CFB: ``h2h`` only; two outcomes named exactly as the event's home
and away teams; valid American prices; a quote clock (the book's market
``last_update``, else the bookmaker ``last_update`` flagged
``quote_clock_source = BOOK_LAST_UPDATE``) not after AISP's receipt clock plus
a small skew. The fetch clock (``requested_at``) and the receipt clock
(``available_as_of``) are kept separately and never relabelled as quote clocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ExternalIdMapping, Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType, OddsSnapshot,
    ProviderRequestLedger, Sportsbook,
)
from app.nba import gen1_common as common
from app.services.event_time_reconciliation import MATCH_WINDOW, assess_clock, make_binding

PROVIDER = "THE_ODDS_API"
SPORT_KEY = "basketball_nba"
#: Ledger endpoint key. Contains ``basketball_nba`` so the credit-lane
#: attribution files it under the NBA lane, never under any other.
ENDPOINT_KEY = "basketball_nba_odds"
#: Moneyline only: the NBA policy prices nothing else, and every extra market
#: is another credit per region.
MARKETS: tuple[str, ...] = ("h2h",)
LEAGUE_CODE = common.LEAGUE_CODE
#: Provider vs admitted tip-off. Both are the scheduled tip-off, so they must
#: agree exactly; any gap is an unresolved disagreement and is refused.
TIPOFF_TOLERANCE = MATCH_WINDOW  # matching only; earliest clock still closes betting
#: How far from the provider tip-off a same-teams game is still named as a
#: tip-off disagreement (rather than "no game"). Naming only; never binds.
TIPOFF_NAMING_WINDOW = timedelta(days=2)
#: Two admitted same-teams games this close to the provider tip-off make the
#: event ambiguous even if one of them matches exactly: refused, never picked.
AMBIGUITY_WINDOW = timedelta(hours=12)
#: How far a book's quote clock may run ahead of AISP's receipt clock.
CLOCK_SKEW = timedelta(minutes=5)
#: The buy window: a pair bought here is fresh at the T-30..T-15 checkpoint.
BUY_OPENS = timedelta(minutes=75)
BUY_CLOSES = timedelta(minutes=35)

FRANCHISE_PREFIX = "nba-neil:franchise:"

#: The Odds API ``basketball_nba`` team name -> Codex franchise code. Closed and
#: one-to-one: 30 names, 30 codes. The codes are the CANONICAL franchise codes
#: Model Studio writes into ``nba-neil:franchise:<CODE>`` (its
#: ``sports_nba_gen1.current_history_source.TEAM_IDENTITIES``, second column),
#: which are NOT always ESPN's display abbreviations: Brooklyn is BRK (ESPN
#: BKN), Charlotte CHO (CHA), Golden State GSW (GS), New Orleans NOP (NO), New
#: York NYK (NY), Phoenix PHO (PHX), San Antonio SAS (SA), Utah UTA (UTAH),
#: Washington WAS (WSH). A provider name not listed here -- including any
#: spelling variant such as "LA Clippers" -- is refused as TEAM_NAME_UNMAPPED,
#: never matched approximately; adding one is a reviewed code change.
ODDS_API_TEAM_FRANCHISES: dict[str, str] = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BRK",
    "Charlotte Hornets": "CHO",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHO",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}
if len(ODDS_API_TEAM_FRANCHISES) != 30 or len(set(ODDS_API_TEAM_FRANCHISES.values())) != 30:
    raise RuntimeError("ODDS_API_TEAM_FRANCHISES must map 30 names to 30 distinct franchise codes")

CLOCK_SEMANTICS = {
    "observed_at": ("the book's own market last_update (The Odds API bookmakers[].markets[].last_update); "
                    "when the response carries no market clock, the bookmaker last_update "
                    "(quote_clock_source = BOOK_LAST_UPDATE)"),
    "quote_clock_source": "MARKET_LAST_UPDATE or BOOK_LAST_UPDATE: which provider field observed_at came from",
    "book_last_update": "the bookmaker-level last_update, recorded for audit; never the quote clock",
    "requested_at": "when AISP reserved and sent the request (provider ledger requested_at)",
    "available_as_of": "when AISP received the response (response_received_at); never a quote clock",
}

# -- receipt reasons (one per refused event or book) ---------------------------
BOUND = "BOUND"
TEAM_NAME_UNMAPPED = "TEAM_NAME_UNMAPPED"
TEAM_NOT_ADMITTED = "TEAM_NOT_ADMITTED"
TEAM_IDENTITY_AMBIGUOUS = "TEAM_IDENTITY_AMBIGUOUS"
NO_NBA_GAME_FOR_TEAMS = "NO_NBA_GAME_FOR_TEAMS"
ORIENTATION_REVERSED = "ORIENTATION_REVERSED"
TIPOFF_DISAGREEMENT = "TIPOFF_DISAGREEMENT"
MORE_THAN_ONE_GAME_MATCHES = "MORE_THAN_ONE_GAME_MATCHES"
MORE_THAN_ONE_EVENT_BINDS_THIS_GAME = "MORE_THAN_ONE_EVENT_BINDS_THIS_GAME"
EVENT_MAPPED_TO_ANOTHER_GAME = "EVENT_MAPPED_TO_ANOTHER_GAME"
GAME_MAPPED_TO_ANOTHER_EVENT = "GAME_MAPPED_TO_ANOTHER_EVENT"
EVENT_ALREADY_STARTED = "EVENT_ALREADY_STARTED"
GAME_NOT_SCHEDULED = "GAME_NOT_SCHEDULED"
MARKET_NOT_TWO_SIDED = "MARKET_NOT_TWO_SIDED"
OUTCOME_NAMES_DISAGREE_WITH_EVENT = "OUTCOME_NAMES_DISAGREE_WITH_EVENT"
INVALID_AMERICAN_PRICE = "INVALID_AMERICAN_PRICE"
QUOTE_CLOCK_AFTER_RECEIPT = "QUOTE_CLOCK_AFTER_RECEIPT"

# -- quote clock provenance (raw_payload.quote_clock_source) -------------------
MARKET_CLOCK = "MARKET_LAST_UPDATE"
BOOK_CLOCK = "BOOK_LAST_UPDATE"


def _utc(value: datetime | None) -> datetime | None:
    """The same instant in UTC (the DB session hands back ET-aware values)."""

    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class Resolution:
    team_ids: frozenset[Any]
    franchise: str | None
    reason: str

    @property
    def ok(self) -> bool:
        return self.reason == BOUND and len(self.team_ids) == 1


def nba_franchises(session: Session) -> dict[str, set[Any]]:
    """Codex franchise code -> AISP team ids, from the exact franchise mappings AISP holds."""

    out: dict[str, set[Any]] = {}
    for external_id, team_id in session.execute(select(ExternalIdMapping.external_id, ExternalIdMapping.entity_id)
                                                .where(ExternalIdMapping.entity_type == "team",
                                                       ExternalIdMapping.source == common.SOURCE_FRANCHISE)).all():
        text = str(external_id or "")
        if text.startswith(FRANCHISE_PREFIX):
            out.setdefault(text[len(FRANCHISE_PREFIX):], set()).add(team_id)
    return out


def resolve_team(provider_name: str | None, franchises: dict[str, set[Any]]) -> Resolution:
    """Map an Odds API NBA team name to exactly one AISP team, through the closed name map only."""

    code = ODDS_API_TEAM_FRANCHISES.get(str(provider_name or "").strip())
    if code is None:
        return Resolution(frozenset(), None, TEAM_NAME_UNMAPPED)
    ids = frozenset(franchises.get(code) or ())
    if not ids:
        return Resolution(ids, code, TEAM_NOT_ADMITTED)
    if len(ids) != 1:
        return Resolution(ids, code, TEAM_IDENTITY_AMBIGUOUS)
    return Resolution(ids, code, BOUND)


def _nba_games(session: Session, start: datetime, end: datetime) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Game.id, Game.starts_at, Game.status).join(League, League.id == Game.league_id)
        .where(League.code == LEAGUE_CODE, Game.starts_at >= start, Game.starts_at <= end)).all()
    games = []
    for game_id, starts_at, status in rows:
        sides = {side.is_home: side.team_id for side in session.scalars(
            select(GameTeam).where(GameTeam.game_id == game_id))}
        if True in sides and False in sides:
            games.append({"game_id": game_id, "starts_at": _utc(starts_at), "status": status,
                          "home": sides[True], "away": sides[False]})
    return games


def _receipt(event: Any, reason: str, **detail: Any) -> dict[str, Any]:
    row = {"reason": reason, "source": PROVIDER, "sport_key": SPORT_KEY,
           "source_event_id": str(getattr(event, "source_event_id", "") or ""),
           "home_team": getattr(event, "home_team", None), "away_team": getattr(event, "away_team", None),
           "commence_time": _utc(getattr(event, "commence_time", None)).isoformat()
           if getattr(event, "commence_time", None) else None}
    row.update({k: (str(v) if v is not None and not isinstance(v, (int, float, bool, list)) else v)
                for k, v in detail.items()})
    return row


def bind_events(session: Session, events: Iterable[Any], *, received_at: datetime,
                ) -> tuple[list[tuple[Any, Any]], list[dict[str, Any]]]:
    """Bind each NBA event to at most one AISP NBA game. Returns (bound [(event, game_id)], receipts).

    Every event yields exactly one receipt: BOUND or the reason it was refused.
    """

    events = list(events)
    received_at = _utc(received_at)
    receipts: list[dict[str, Any]] = []
    if not events:
        return [], receipts
    franchises = nba_franchises(session)
    tips = [_utc(e.commence_time) for e in events]
    # A wide window so a near-miss can be named (tip-off moved, reversed) rather
    # than reported as "no game".
    games = _nba_games(session, min(tips) - TIPOFF_NAMING_WINDOW, max(tips) + TIPOFF_NAMING_WINDOW)

    tentative: dict[Any, list[tuple[Any, dict[str, Any]]]] = {}
    for event in events:
        tip = _utc(event.commence_time)
        if tip <= received_at:
            receipts.append(_receipt(event, EVENT_ALREADY_STARTED))
            continue
        home, away = resolve_team(event.home_team, franchises), resolve_team(event.away_team, franchises)
        refused = next((r for r in (home, away) if not r.ok), None)
        if refused is not None:
            receipts.append(_receipt(event, refused.reason,
                                     home_resolution=home.reason, away_resolution=away.reason,
                                     home_franchise=home.franchise, away_franchise=away.franchise))
            continue
        home_id, away_id = next(iter(home.team_ids)), next(iter(away.team_ids))
        same = [g for g in games if g["home"] == home_id and g["away"] == away_id]
        reversed_ = [g for g in games if g["home"] == away_id and g["away"] == home_id
                     and abs(g["starts_at"] - tip) <= AMBIGUITY_WINDOW]
        in_tolerance = [g for g in same if abs(g["starts_at"] - tip) <= TIPOFF_TOLERANCE]
        # Two admitted games with these teams near this tip-off is itself an
        # unresolved identity: an exact tip-off match does not pick between them.
        from app.services.event_time_reconciliation import SLATE_TIMEZONE
        nearby = [g for g in same if g["starts_at"].astimezone(SLATE_TIMEZONE).date() == tip.astimezone(SLATE_TIMEZONE).date()]
        if len(nearby) > 1:
            receipts.append(_receipt(event, MORE_THAN_ONE_GAME_MATCHES,
                                     candidates=[str(g["game_id"]) for g in nearby]))
            continue
        if not in_tolerance:
            if reversed_:
                receipts.append(_receipt(event, ORIENTATION_REVERSED, game_id=reversed_[0]["game_id"]))
            elif same:
                nearest = min(same, key=lambda g: abs(g["starts_at"] - tip))
                receipts.append(_receipt(event, TIPOFF_DISAGREEMENT, game_id=nearest["game_id"],
                                         admitted_tipoff=nearest["starts_at"].isoformat(),
                                         provider_tipoff=tip.isoformat(),
                                         gap_minutes=round(abs(nearest["starts_at"] - tip).total_seconds() / 60, 1)))
            else:
                receipts.append(_receipt(event, NO_NBA_GAME_FOR_TEAMS, home_franchise=home.franchise,
                                         away_franchise=away.franchise))
            continue
        if len(in_tolerance) > 1:
            receipts.append(_receipt(event, MORE_THAN_ONE_GAME_MATCHES,
                                     candidates=[str(g["game_id"]) for g in in_tolerance]))
            continue
        game = in_tolerance[0]
        clock = assess_clock(game["starts_at"], tip, received_at)
        if not clock.accepted:
            receipts.append(_receipt(event, clock.reason, game_id=game["game_id"]))
            continue
        if game["status"] != GameStatus.SCHEDULED:
            receipts.append(_receipt(event, GAME_NOT_SCHEDULED, game_id=game["game_id"],
                                     status=getattr(game["status"], "value", game["status"])))
            continue
        tentative.setdefault(game["game_id"], []).append((event, game))

    bound: list[tuple[Any, Any]] = []
    for game_id, claims in tentative.items():
        if len(claims) > 1:
            for event, _game in claims:
                receipts.append(_receipt(event, MORE_THAN_ONE_EVENT_BINDS_THIS_GAME, game_id=game_id,
                                         events=[str(e.source_event_id) for e, _g in claims]))
            continue
        event, _game = claims[0]
        event_id = str(event.source_event_id)
        mapped_game = session.scalar(select(ExternalIdMapping.entity_id).where(
            ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == PROVIDER,
            ExternalIdMapping.external_id == event_id))
        if mapped_game is not None and str(mapped_game) != str(game_id):
            receipts.append(_receipt(event, EVENT_MAPPED_TO_ANOTHER_GAME, game_id=game_id,
                                     mapped_game_id=mapped_game))
            continue
        other_event = session.scalar(select(ExternalIdMapping.external_id).where(
            ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == PROVIDER,
            ExternalIdMapping.entity_id == game_id, ExternalIdMapping.external_id != event_id))
        if other_event is not None:
            receipts.append(_receipt(event, GAME_MAPPED_TO_ANOTHER_EVENT, game_id=game_id,
                                     mapped_event_id=other_event))
            continue
        receipts.append(_receipt(event, BOUND, game_id=game_id, clock_reconciliation=assess_clock(_game["starts_at"], event.commence_time, received_at).receipt()))
        bound.append((event, game_id))
    return bound, receipts


def _valid_american(price: Any) -> bool:
    return isinstance(price, int) and not isinstance(price, bool) and abs(price) >= 100


def persist(session: Session, bound: list[tuple[Any, Any]], *, requested_at: datetime, received_at: datetime,
            response: dict[str, Any] | None = None, admission_asof: datetime | None = None) -> dict[str, Any]:
    """Write each bound event's validated h2h pairs in the checkpoint's shape. Idempotent.

    ``response`` carries the response identity (archive path, payload sha256,
    ledger reservation id) so it travels with every row.
    """

    from app.data_sources.provider_registry import identity as provider_identity
    from app.gen2.exchange.canonical_json import canonical_sha256

    provider = provider_identity(PROVIDER)
    requested_at, received_at = _utc(requested_at), _utc(received_at)
    available_at = max(received_at, _utc(admission_asof)) if admission_asof is not None else received_at
    created = existing = 0
    receipts: list[dict[str, Any]] = []
    # In-batch memory: production sessions do not autoflush, so a query alone
    # cannot see a row added earlier in this same call.
    seen: set[tuple] = set()
    for event, game_id in bound:
        sides = {side.is_home: side.team_id for side in session.scalars(
            select(GameTeam).where(GameTeam.game_id == game_id))}
        canonical_start = _utc(session.scalar(select(Game.starts_at).where(Game.id == game_id)))
        try:
            clock_binding = make_binding(game_id=game_id, sport=LEAGUE_CODE, home_id=sides.get(True), away_id=sides.get(False), provider_event_id=str(event.source_event_id), canonical_start=canonical_start, provider_start=_utc(event.commence_time), received_at=received_at)
        except (TypeError, ValueError) as error:
            receipts.append(_receipt(event, "EVENT_CLOCK_BINDING_REFUSED", detail=str(error)))
            continue
        wrote_any = False
        for book in event.bookmakers:
            for market_dto in book.markets:
                if market_dto.key != "h2h":
                    continue
                names = [o.name for o in market_dto.outcomes]
                if len(names) != 2 or len(set(names)) != 2:
                    receipts.append(_receipt(event, MARKET_NOT_TWO_SIDED, book=book.key, outcomes=len(names)))
                    continue
                by_name = {o.name: o for o in market_dto.outcomes}
                if set(by_name) != {event.home_team, event.away_team}:
                    receipts.append(_receipt(event, OUTCOME_NAMES_DISAGREE_WITH_EVENT, book=book.key,
                                             outcome_names=sorted(by_name)))
                    continue
                if not all(_valid_american(o.price) for o in market_dto.outcomes):
                    receipts.append(_receipt(event, INVALID_AMERICAN_PRICE, book=book.key,
                                             prices=[o.price for o in market_dto.outcomes]))
                    continue
                quote_clock = _utc(market_dto.last_update)
                clock_source = BOOK_CLOCK if getattr(market_dto, "clock_source", None) == "BOOK" else MARKET_CLOCK
                if quote_clock > received_at + CLOCK_SKEW:
                    receipts.append(_receipt(event, QUOTE_CLOCK_AFTER_RECEIPT, book=book.key,
                                             last_update=quote_clock.isoformat(), quote_clock_source=clock_source))
                    continue
                book_key = provider.bookmaker_key(book.key)
                sportsbook = session.scalar(select(Sportsbook).where(Sportsbook.key == book_key))
                if sportsbook is None:
                    sportsbook = Sportsbook(key=book_key, name=book.title, region="us", active=True)
                    session.add(sportsbook)
                    session.flush()
                market = session.scalar(select(Market).where(
                    Market.game_id == game_id, Market.market_type == MarketType.MONEYLINE,
                    Market.period == "full_game"))
                if market is None:
                    market = Market(game_id=game_id, market_type=MarketType.MONEYLINE, period="full_game")
                    session.add(market)
                    session.flush()
                for key_side, is_home, provider_name in (("HOME", True, event.home_team),
                                                         ("AWAY", False, event.away_team)):
                    quote = by_name[provider_name]
                    outcome_key = f"{key_side}:None"
                    outcome = session.scalar(select(MarketOutcome).where(
                        MarketOutcome.market_id == market.id, MarketOutcome.outcome_key == outcome_key))
                    if outcome is None:
                        outcome = MarketOutcome(market_id=market.id, outcome_key=outcome_key, label=key_side,
                                                participant_team_id=sides.get(is_home))
                        session.add(outcome)
                        session.flush()
                    commence = _utc(event.commence_time)
                    # The retained tip-off is part of the quote's identity: after a
                    # schedule change the same price at the same book clock, now
                    # bound at the new tip-off, is a NEW row.
                    identity = (outcome.id, sportsbook.id, quote_clock, int(quote.price), commence)
                    prior_tips = [] if identity in seen else [
                        _utc(datetime.fromisoformat(str((raw_prior or {}).get("event_commence_time"))
                                                    .replace("Z", "+00:00")))
                        if (raw_prior or {}).get("event_commence_time") else None
                        for raw_prior in session.scalars(select(OddsSnapshot.raw_payload).where(
                            OddsSnapshot.market_outcome_id == outcome.id,
                            OddsSnapshot.sportsbook_id == sportsbook.id,
                            OddsSnapshot.observed_at == quote_clock,
                            OddsSnapshot.american_odds == int(quote.price),
                            OddsSnapshot.source == PROVIDER))]
                    if identity in seen or commence in prior_tips:
                        existing += 1
                        continue
                    seen.add(identity)
                    decimal = 1 + (quote.price / 100.0 if quote.price > 0 else 100.0 / abs(quote.price))
                    raw = {"provider_event_id": str(event.source_event_id), "sport_key": SPORT_KEY,
                           "market": "h2h", "book": book.key, "book_title": book.title,
                           "side": key_side, "provider_team_name": provider_name, "american": int(quote.price),
                           "franchise": ODDS_API_TEAM_FRANCHISES.get(str(provider_name).strip()),
                           "quote_clock_source": clock_source,
                           "market_last_update": quote_clock.isoformat() if clock_source == MARKET_CLOCK else None,
                           "book_last_update": _utc(book.last_update).isoformat(),
                           "event_commence_time": _utc(event.commence_time).isoformat(),
                           "requested_at": requested_at.isoformat(),
                           "response_received_at": received_at.isoformat(),
                           "settlement": "full game including overtime (The Odds API h2h)",
                           "settlement_basis": "ASSERTED_BY_AISP_NOT_STATED_BY_PROVIDER",
                           "clock_semantics": CLOCK_SEMANTICS}
                    if response:
                        raw.update({k: v for k, v in response.items() if v is not None})
                    raw["event_clock_binding"] = clock_binding
                    if admission_asof is not None:
                        raw["retained_quote_ingested_at"] = available_at.isoformat()
                    session.add(OddsSnapshot(
                        market_outcome_id=outcome.id, sportsbook_id=sportsbook.id, observed_at=quote_clock,
                        available_as_of=available_at, american_odds=int(quote.price), decimal_odds=decimal,
                        point=None, implied_probability=1.0 / decimal, source=PROVIDER,
                        source_event_id=str(event.source_event_id), source_mode="LIVE", raw_payload=raw,
                        payload_checksum=canonical_sha256(raw)))
                    created += 1
                wrote_any = True
        if wrote_any and session.scalar(select(ExternalIdMapping.id).where(
                ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == PROVIDER,
                ExternalIdMapping.external_id == str(event.source_event_id))) is None \
                and ("map", str(event.source_event_id)) not in seen:
            seen.add(("map", str(event.source_event_id)))
            session.add(ExternalIdMapping(entity_type="game", entity_id=game_id, source=PROVIDER,
                                          external_id=str(event.source_event_id)))
    session.flush()
    return {"created": created, "existing": existing, "quote_receipts": receipts}


def plan(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Is a request due now? One request prices every NBA event, so the answer is per tick.

    Due when at least one SCHEDULED NBA game is inside its buy window
    (T-75..T-35) with a verified production forecast that the newest discovery
    lists as decision-eligible at this clock (the same selection the paper
    checkpoint and the NBA SportsGameOdds lane use), and no NBA Odds API request
    has been reserved since that game's window opened -- so each tip-off cluster
    costs at most one request, including a game the provider turns out not to
    offer. A game whose forecast could never reach a decision (no forecast, a
    V1 package without a lease, an expired lease) never makes a request due.
    """

    from app.nba.gen1_production import current_forecast_for_game, present_eligibility

    moment = _utc(now or datetime.now(UTC))
    rows = session.execute(
        select(Game.id, Game.starts_at, ExternalIdMapping.external_id)
        .join(League, League.id == Game.league_id)
        .join(ExternalIdMapping, (ExternalIdMapping.entity_id == Game.id)
              & (ExternalIdMapping.entity_type == "game") & (ExternalIdMapping.source == common.SOURCE_ESPN_GAME))
        .where(League.code == LEAGUE_CODE, Game.status == GameStatus.SCHEDULED,
               Game.starts_at >= moment + BUY_CLOSES, Game.starts_at <= moment + BUY_OPENS + timedelta(hours=2))
        .order_by(Game.starts_at, ExternalIdMapping.external_id)).all()
    games, due = [], []
    for game_id, starts_at, espn_id in rows:
        tip = _utc(starts_at)
        entry = {"game_id": str(game_id), "espn_game_id": str(espn_id), "starts_at": tip.isoformat()}
        forecast = current_forecast_for_game(session, game_id=game_id)
        lease = present_eligibility(session, forecast, now=moment) if forecast is not None else {"state": "NO_FORECAST", "eligible": False}
        from app.nba.gen1_paper import _operational_start
        operational = _operational_start(session,{"game_id":game_id,"starts_at":tip,"espn_game_id":str(espn_id)},moment)
        entry["operational_start"] = operational.isoformat()
        if not operational-BUY_OPENS <= moment <= operational-BUY_CLOSES:
            games.append({**entry,"state":"OUTSIDE_ORIGINAL_PRICE_WINDOW","collection_independent_of_funding_eligibility":True})
            continue
        opened = operational - BUY_OPENS
        from app.services.odds_request_window import window_requests
        coverage = quote_coverage(session, game_id, moment=moment)
        window = window_requests(session, provider=PROVIDER, endpoint_key=ENDPOINT_KEY, opened=opened, now=moment, event_complete=coverage["complete"])
        state = "PRICE_HELD" if coverage["complete"] else ("ALREADY_REQUESTED_IN_WINDOW" if window["blocking"] is not None else "DUE")
        games.append({**entry, "state": state, "window_requests": window,
                      "forecast_available": forecast is not None, "discovery": lease.get("state"), "coverage": coverage,
                      "collection_independent_of_funding_eligibility": True})
        if state == "DUE":
            due.append(entry)
    return {"due": bool(due), "due_games": due, "games": games, "evaluated_at": moment.isoformat(),
            "markets": list(MARKETS), "sport_key": SPORT_KEY}


__all__ = [
    "BOUND", "BUY_CLOSES", "BUY_OPENS", "ENDPOINT_KEY", "MARKETS", "ODDS_API_TEAM_FRANCHISES", "PROVIDER",
    "SPORT_KEY", "TIPOFF_DISAGREEMENT", "TIPOFF_TOLERANCE", "bind_events", "nba_franchises", "persist", "plan",
    "resolve_team",
]


def quote_coverage(session: Session, game_id: Any, *, moment: datetime) -> dict[str, Any]:
    """Per-event same-book completeness; a successful bulk request is not a pair."""
    from app.nba.gen1_paper import _tipoff_bound
    from app.services.nfl_paper_decision import paired_quotes
    from app.services.research_paper_policy import NBA
    game = session.get(Game, game_id)
    if game is None:
        return {"complete": False, "reason": "NO_CANONICAL_GAME", "pairs": 0}
    pairs = [pair for pair in paired_quotes(session, game_id) if pair["source"] == PROVIDER]
    pairs, refused = _tipoff_bound(session, pairs, _utc(game.starts_at), game_id=game_id, moment=moment)
    fresh = [pair for pair in pairs if 0 <= (moment - datetime.fromisoformat(pair["pair_observed_at"])).total_seconds() <= NBA.max_quote_age_minutes * 60]
    return {"complete": bool(fresh), "pairs": len(fresh), "reason": "PRICE_HELD" if fresh else "NO_USABLE_PAIR", "refusals": refused}
