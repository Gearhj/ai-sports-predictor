# PM current event-time adapter; original clocks and policy remain.
"""College Football prices from The Odds API (``americanfootball_ncaaf``): plan, bind, persist.

WHAT THIS IS
------------
A second permitted quote source for the unchanged AISP_CFB_PAPER_POLICY_V1
checkpoint (``app.cfb.paper_decision``). It reuses the existing Odds API
adapter (``app.data_sources.odds.TheOddsAPIAdapter``), the existing provider
ledger / quota / credit-lane guards in the orchestrator, and writes quotes in
the exact shape the checkpoint already reads
(``nfl_paper_decision.paired_quotes``): ``HOME:None`` / ``AWAY:None``
moneyline outcomes, one ``OddsSnapshot`` per side, both sides from ONE
provider, ONE book and ONE market.

It does NOT go through ``CanonicalNormalizer.ingest_odds``: that binder keys
outcomes on provider team names, which the checkpoint cannot pair, and its
fuzzy participant matcher is not the identity rule CFB needs. NFL binds through
its own exact service for the same reason. ``odds_league.PROVIDER_LEAGUES``
therefore still refuses ``americanfootball_ncaaf`` for ``ingest_odds``.

GAME IDENTITY (the only place a provider event meets a CFB game)
----------------------------------------------------------------
CFB teams are stored with CFBD school names and ``CFBD<id>`` abbreviations, so
the abbreviation never matches anything a provider says. The Odds API names a
team "<school> <mascot>" ("Pittsburgh Panthers"). An event binds to a game only
when ALL of these hold:

1. both provider names resolve, by normalised whole-token school-name prefix,
   to a CFB team -- the LONGEST school name wins across every CFB team AISP
   holds ("Texas Tech Red Raiders" is Texas Tech, not Texas), and a remainder
   that begins with a token such as ``state`` / ``tech`` / ``a`` (A&M) /
   ``southern`` is refused as a prefix collision rather than guessed;
2. the resolved home and away teams are exactly the game's home and away
   teams, in that orientation (reversed is refused, never flipped);
3. the provider kickoff EQUALS the admitted kickoff (``KICKOFF_TOLERANCE`` is
   zero). Any disagreement is unresolved -- AISP cannot tell which clock is
   right, and funding against the later one could buy after the real start --
   so the event is refused as ``KICKOFF_DISAGREEMENT`` and nothing is stored;
4. exactly one game satisfies 1-3, no other event in the same response binds
   the same game, and no earlier THE_ODDS_API mapping ties either the event or
   the game to something else;
5. the event has not started at fetch time (in-play prices are never pregame).

Anything else is refused FOR THAT EVENT ALONE with a named receipt; no other
game is ever substituted and the rest of the response still binds.

QUOTE VALIDATION (per book, per market)
---------------------------------------
Only ``h2h``. Exactly two outcomes whose names are exactly the event's home and
away names, valid American prices, and a market ``last_update`` not after the
fetch clock (plus a small skew). A failing book is refused with a receipt; the
other books of the same event still persist.

THREE CLOCKS, NEVER MIXED
-------------------------
* quote clock    ``observed_at``: the book's own market ``last_update`` (both
                 sides of an Odds API h2h market share it); the book-level
                 ``last_update`` is kept beside it in ``raw_payload``. The
                 documented ``/odds`` shape may carry ONLY the bookmaker clock;
                 then the adapter marks the market ``clock_source="BOOK"``, the
                 quote clock is the bookmaker ``last_update``, and
                 ``raw_payload.quote_clock_source`` says so while
                 ``market_last_update`` stays null -- a bookmaker time is never
                 relabelled as an observed market clock. A market with neither
                 clock never reaches this module (the adapter skips it);
* fetch clock    ``raw_payload.requested_at``: the ledger reservation time;
* receipt clock  ``available_as_of`` (= ``raw_payload.response_received_at``):
                 when AISP held the response and could first have used it.
The checkpoint ages a pair by its OLDER leg's quote clock, unchanged.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session
from app.services.event_time_reconciliation import (
    MATCH_WINDOW, SLATE_TIMEZONE, CONTRACT, assess_clock, reconcile_clock_identity,
    resolve_candidate, make_binding, utc as event_utc,
)

from app.models import (
    ExternalIdMapping, Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType, OddsSnapshot,
    Season, Sportsbook, Team,
)

PROVIDER = "THE_ODDS_API"
SPORT_KEY = "americanfootball_ncaaf"
#: Ledger endpoint key. Contains ``ncaaf`` so the credit-lane attribution files
#: it under the CFB lane, never under NFL's.
ENDPOINT_KEY = "americanfootball_ncaaf_odds"
#: Moneyline only: the CFB policy prices nothing else, and every extra market
#: is another credit per region.
MARKETS: tuple[str, ...] = ("h2h",)
LEAGUE_CODE = "CFB"
NATIVE_SOURCE = 'CFBD'
#: Provider vs admitted kickoff. Both are the scheduled kickoff, so they must
#: agree exactly. Any gap is an UNRESOLVED disagreement (a moved game, a TBA
#: placeholder or a different game) and is refused, never averaged or guessed:
#: a 60-minute tolerance let a provider kickoff of 18:45Z bind a game admitted
#: at 19:30Z and fund a position at 19:10Z, after the provider's kickoff.
KICKOFF_TOLERANCE = MATCH_WINDOW
#: How far from the provider kickoff a same-teams game is still named as a
#: kickoff disagreement (rather than "no game"). Naming only; never binds.
KICKOFF_NAMING_WINDOW = timedelta(days=2)
#: Two admitted same-teams games this close to the provider kickoff make the
#: event ambiguous even if one of them matches exactly: refused, never picked.
AMBIGUITY_WINDOW = timedelta(hours=12)
#: How far a book's quote clock may run ahead of AISP's receipt clock.
CLOCK_SKEW = timedelta(minutes=5)
#: The buy window: a pair bought here is fresh at the T-30..T-15 checkpoint.
BUY_OPENS = timedelta(minutes=75)
BUY_CLOSES = timedelta(minutes=35)

#: A remainder (after the school prefix) that starts with one of these is a
#: DIFFERENT school whose name extends this one ("Georgia State", "Texas A&M",
#: "Louisiana Monroe"), not a mascot. Refused unless that longer school is
#: itself a known CFB team (then the longest match already took it).
COLLISION_TOKENS = frozenset({
    "state", "st", "tech", "a", "am", "and", "southern", "northern", "eastern", "western", "central",
    "international", "atlantic", "christian", "methodist", "oh", "fl", "monroe", "lafayette", "baptist",
    "poly", "university", "college",
})

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
TEAM_NAME_UNMATCHED = "TEAM_NAME_UNMATCHED"
TEAM_NAME_AMBIGUOUS = "TEAM_NAME_AMBIGUOUS"
TEAM_NAME_PREFIX_COLLISION = "TEAM_NAME_PREFIX_COLLISION"
NO_CFB_GAME_FOR_TEAMS = "NO_CFB_GAME_FOR_TEAMS"
ORIENTATION_REVERSED = "ORIENTATION_REVERSED"
KICKOFF_DISAGREEMENT = "KICKOFF_DISAGREEMENT"
#: Former name of the same refusal, kept so older receipts still read.
KICKOFF_OUTSIDE_TOLERANCE = KICKOFF_DISAGREEMENT
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


def normalise(name: str | None) -> str:
    """Lower-case, accent-free, apostrophes dropped, '&' spelled out, punctuation to spaces."""

    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = text.replace("&", " and ").replace("'", "").replace("’", "").replace("`", "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


@dataclass(frozen=True)
class Resolution:
    team_ids: frozenset[Any]
    school: str | None
    reason: str

    @property
    def ok(self) -> bool:
        return self.reason == BOUND and len(self.team_ids) == 1


#: The Odds API's school spelling -> AISP's (CFBD's) school name, both normalised. Used ONLY when the
#: provider name does not resolve on its own, and only when the aliased name then resolves to exactly
#: one team, so an alias can never re-route a name that already binds. Each entry is a name observed
#: unmatched (or prefix-collided) in production NCAAF receipts through 2026-10-16:
#:   "Southern Mississippi Golden Eagles" x38, "UMass Minutemen" x38, "Appalachian State Mountaineers" x23,
#:   "Sam Houston State Bearkats" x8 (refused as a prefix collision with "Sam Houston" + "State"),
#:   "Citadel Bulldogs" x7 (AISP holds no "The Citadel" team today; the alias binds once it does).
TEAM_ALIASES: dict[str, str] = {
    "appalachian state": "app state",
    "umass": "massachusetts",
    "southern mississippi": "southern miss",
    "sam houston state": "sam houston",
    "citadel": "the citadel",
}


def _aliased(target: str) -> str | None:
    """``target`` with its leading provider school spelling replaced by AISP's, or None."""

    for provider_school in sorted(TEAM_ALIASES, key=len, reverse=True):
        if target == provider_school or target.startswith(provider_school + " "):
            return TEAM_ALIASES[provider_school] + target[len(provider_school):]
    return None


def resolve_team(provider_name: str, schools: dict[str, set[Any]]) -> Resolution:
    """Map "<school> <mascot>" to exactly one CFB team by longest whole-token school prefix.

    A name that does not bind on its own is retried once through ``TEAM_ALIASES``; the alias is used
    only if it binds, otherwise the original refusal stands.
    """

    direct = _resolve(normalise(provider_name), schools)
    if direct.ok:
        return direct
    aliased = _aliased(normalise(provider_name))
    if aliased is not None:
        via_alias = _resolve(aliased, schools)
        if via_alias.ok:
            return via_alias
    return direct


def _resolve(target: str, schools: dict[str, set[Any]]) -> Resolution:
    if not target:
        return Resolution(frozenset(), None, TEAM_NAME_UNMATCHED)
    matches = [school for school in schools if school and (target == school or target.startswith(school + " "))]
    if not matches:
        return Resolution(frozenset(), None, TEAM_NAME_UNMATCHED)
    best = max(matches, key=len)
    ids = frozenset(schools[best])
    if len(ids) != 1:
        return Resolution(ids, best, TEAM_NAME_AMBIGUOUS)
    remainder = target[len(best):].split()
    if remainder and remainder[0] in COLLISION_TOKENS:
        return Resolution(ids, best, TEAM_NAME_PREFIX_COLLISION)
    return Resolution(ids, best, BOUND)


def cfb_schools(session: Session) -> dict[str, set[Any]]:
    """Normalised CFBD school name -> CFB team ids (every CFB team AISP holds)."""

    out: dict[str, set[Any]] = {}
    rows = session.execute(select(Team.id, Team.name).join(League, League.id == Team.league_id)
                           .where(League.code == LEAGUE_CODE)).all()
    for team_id, name in rows:
        out.setdefault(normalise(name), set()).add(team_id)
    return out


def _cfb_games(session: Session, start: datetime, end: datetime) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Game.id, Game.starts_at, Game.status)
        .join(Season, Season.id == Game.season_id).join(League, League.id == Season.league_id)
        .where(League.code == LEAGUE_CODE, Game.league_id == League.id, Game.starts_at >= start, Game.starts_at <= end)).all()
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
    """Bind each NCAAF event to at most one CFB game. Returns (bound [(event, game_id)], receipts).

    Every event yields exactly one receipt: BOUND or the reason it was refused.
    """

    events = list(events)
    received_at = _utc(received_at)
    receipts: list[dict[str, Any]] = []
    if not events:
        return [], receipts
    schools = cfb_schools(session)
    kicks = [_utc(e.commence_time) for e in events]
    # A wide window so a near-miss can be named (kickoff moved, reversed) rather
    # than reported as "no game".
    games = _cfb_games(session, min(kicks) - KICKOFF_NAMING_WINDOW, max(kicks) + KICKOFF_NAMING_WINDOW)

    tentative: dict[Any, list[tuple[Any, dict[str, Any]]]] = {}
    for event in events:
        kick = _utc(event.commence_time)
        if kick <= received_at:
            receipts.append(_receipt(event, EVENT_ALREADY_STARTED))
            continue
        home, away = resolve_team(event.home_team, schools), resolve_team(event.away_team, schools)
        refused = next((r for r in (home, away) if not r.ok), None)
        if refused is not None:
            receipts.append(_receipt(event, refused.reason,
                                     home_resolution=home.reason, away_resolution=away.reason,
                                     home_school=home.school, away_school=away.school))
            continue
        home_id, away_id = next(iter(home.team_ids)), next(iter(away.team_ids))
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
        # The BOUND receipt's clock reading must use an AWARE canonical start: a raw column read is
        # naive on SQLite and the receipt then said MISSING_OR_INVALID_EVENT_CLOCK on a bound event.
        receipts.append(_receipt(event, BOUND, game_id=game_id, clock_reconciliation=assess_clock(
            _utc(session.scalar(select(Game.starts_at).where(Game.id == game_id))), event.commence_time, received_at).receipt()))
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
                    # The retained kickoff is part of the quote's identity: after a
                    # schedule change the same price at the same book clock, now
                    # bound at the new kickoff, is a NEW row (the checkpoint only
                    # counts rows retained at the game's current kickoff).
                    identity = (outcome.id, sportsbook.id, quote_clock, int(quote.price), commence)
                    prior_kicks = [] if identity in seen else [
                        _utc(datetime.fromisoformat(str((raw_prior or {}).get("event_commence_time"))
                                                    .replace("Z", "+00:00")))
                        if (raw_prior or {}).get("event_commence_time") else None
                        for raw_prior in session.scalars(select(OddsSnapshot.raw_payload).where(
                            OddsSnapshot.market_outcome_id == outcome.id,
                            OddsSnapshot.sportsbook_id == sportsbook.id,
                            OddsSnapshot.observed_at == quote_clock,
                            OddsSnapshot.american_odds == int(quote.price),
                            OddsSnapshot.source == PROVIDER))]
                    if identity in seen or (commence == canonical_start and commence in prior_kicks):
                        existing += 1
                        continue
                    seen.add(identity)
                    proved_prior = any(
                        isinstance(r, dict) and all((r.get('event_clock_binding') or {}).get(k) == binding.get(k)
                            for k in ('contract','game_id','sport','home_id','away_id','provider_event_id',
                                      'canonical_start','provider_start'))
                        for r in session.scalars(select(OddsSnapshot.raw_payload).where(
                            OddsSnapshot.market_outcome_id == outcome.id, OddsSnapshot.sportsbook_id == sportsbook.id,
                            OddsSnapshot.observed_at == quote_clock, OddsSnapshot.american_odds == int(quote.price),
                            OddsSnapshot.source == PROVIDER)))
                    if proved_prior:
                        existing += 1
                        continue
                    decimal = 1 + (quote.price / 100.0 if quote.price > 0 else 100.0 / abs(quote.price))
                    raw = {"provider_event_id": str(event.source_event_id), "sport_key": SPORT_KEY,
                           "market": "h2h", "book": book.key, "book_title": book.title,
                           "side": key_side, "provider_team_name": provider_name, "american": int(quote.price),
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
                    raw["event_clock_binding"] = binding
                    raw["admitted_at"] = available_at.isoformat()
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
    """Is a request due now? One request prices every NCAAF event, so the answer is per tick.

    Due when at least one SCHEDULED CFB game with a current forecast is inside
    its buy window (T-75..T-35) and no NCAAF Odds API request since that game's
    window opened still counts (``app.services.odds_request_window``): one that
    SUCCEEDED, or one still in flight. So each kickoff cluster costs one
    request, including a game the provider turns out not to offer -- and since
    2026-10-17 a FAILED (or interrupted) request is retried on a later tick
    inside the same window, at most once (two reservations per window), through
    every existing guard.
    """

    from app.services.odds_request_window import window_requests

    from app.cfb.production_import import current_forecast_for_game

    moment = _utc(now or datetime.now(UTC))
    rows = session.execute(
        select(Game.id, Game.starts_at, ExternalIdMapping.external_id)
        .join(Season, Season.id == Game.season_id).join(League, League.id == Season.league_id)
        .join(ExternalIdMapping, (ExternalIdMapping.entity_id == Game.id)
              & (ExternalIdMapping.entity_type == "game") & (ExternalIdMapping.source == "CFBD"))
        .where(League.code == LEAGUE_CODE, Game.league_id == League.id, Game.status == GameStatus.SCHEDULED,
               Game.starts_at >= moment + BUY_CLOSES, Game.starts_at <= moment + BUY_OPENS + MATCH_WINDOW)
        .order_by(Game.starts_at)).all()
    games, due = [], []
    for game_id, starts_at, cfbd_id in rows:
        kick = _utc(starts_at)
        operational = effective_start(session, game_id, kick, moment=moment)
        if not moment + BUY_CLOSES <= operational <= moment + BUY_OPENS:
            continue
        entry = {"game_id": str(game_id), "source_game_id": str(cfbd_id), "starts_at": kick.isoformat(), "operational_start": operational.isoformat()}
        if current_forecast_for_game(session, str(cfbd_id)) is None:
            games.append({**entry, "state": "NO_FORECAST"})
            continue
        opened = operational - BUY_OPENS
        window = window_requests(session, provider=PROVIDER, endpoint_key=ENDPOINT_KEY, opened=opened, now=moment)
        state = "ALREADY_REQUESTED_IN_WINDOW" if window["blocking"] is not None else "DUE"
        games.append({**entry, "state": state, **({"window_requests": window} if window["attempts"] else {})})
        if state == "DUE":
            due.append(entry)
    return {"due": bool(due), "due_games": due, "games": games, "evaluated_at": moment.isoformat(),
            "markets": list(MARKETS), "sport_key": SPORT_KEY}


__all__ = [
    "BOUND", "BUY_CLOSES", "BUY_OPENS", "ENDPOINT_KEY", "KICKOFF_DISAGREEMENT", "KICKOFF_TOLERANCE", "MARKETS", "PROVIDER", "SPORT_KEY",
    "TEAM_ALIASES", "bind_events", "cfb_schools", "normalise", "persist", "plan", "resolve_team",
]
