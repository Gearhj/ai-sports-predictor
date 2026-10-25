"""One budget, shared by every consumer, enforced where requests are reserved.

WHY THIS REPLACES THE PER-LANE FLOORS
-------------------------------------
Each consumer used to defend its own share with a floor: the MLB fallback had
the provider reserve, NFL in-game held a floor above it for MLB pregame. Floors
that overlap do not add up to a budget -- they are several consumers each
reasoning about the same pool from a different direction, and the arithmetic
only works if you read them together, which nothing did. The result was an MLB
cadence spending an order of magnitude more per day than the month could fund.

So: one plan, explicit allocations that sum to the daily total, and a single
enforcement point at the reservation boundary. A lane that has spent its day is
refused by name; a lane that has not cannot be starved by one that has.

THE NUMBERS COME FROM THE PROVIDER
----------------------------------
Not from a local count of what AISP believes it spent. The account's own usage
endpoint reports the monthly entity cap and the entities consumed, and the plan
is built from that. A local count cannot know the entitlement, cannot know
whether the window rolls or resets, and cannot see a spend made from anywhere
else -- and planning against a guess at those is how a budget runs out early.

The local ledger is still read, for the one thing the provider's counter cannot
give: what is in flight right now. A reservation written before its request is
visible to a concurrent job, so two jobs cannot both see room for the last
object.

REPLENISHMENT IS A DATE, NOT A ROLLING WINDOW
---------------------------------------------
The provider reports a `per-month` entity cap, and its `per-day` counter tracks
the UTC calendar day exactly. The month is read the same way, so the balance
refills at the start of the next UTC month. The remaining daily rate is
therefore the remaining objects divided by the days left in THIS month -- a
number that rises as a light day passes and falls as a heavy one does, which is
the behaviour a budget with a deadline should have.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ProviderRequestLedger

PROVIDER = "SPORTSGAMEODDS"

#: The ledger key the usage reading is recorded under. It spends no entities --
#: the endpoint returns account data -- so it is a zero-object ledger row that
#: carries the authoritative remaining balance.
USAGE_ENDPOINT_KEY = "sgo_account_usage"

#: Never spent, whatever the plan says.
#:
#: A SCHEDULE, not a constant. The owner reduced it to 300 for the remainder of
#: September 2026 to fund price coverage, and it returns to 400 at the October
#: monthly reset automatically -- so the temporary relaxation expires on its own
#: rather than depending on someone remembering to put it back. A reserve that
#: has to be manually restored is a reserve that eventually is not.
DEFAULT_OBJECT_RESERVE = 400
RESERVE_SCHEDULE: tuple[dict[str, Any], ...] = (
    {
        "from_service_date": "2026-09-14",
        "until_service_date": "2026-09-30",
        #: The provider's UTC billing month the reduction belongs to. The Sept 30
        #: service day runs to 04:00 UTC Oct 1, four hours into October's bill;
        #: without this bound the September 300 would leak into October.
        "billing_month": "2026-09",
        "reserve": 300,
        "authority": "project owner, 2026-09-14",
        "reason": (
            "Bounded owner-directed reduction from 400 to fund MLB price "
            "coverage for the remainder of September 2026. Restores to 400 on "
            "the October monthly reset without further action."
        ),
    },
)


def reserve_at(moment: datetime) -> int:
    """The reserve in force at an INSTANT -- the one rule BOTH budget guards apply.

    A schedule entry holds only while both its Eastern service days and its
    provider billing month hold. So the owner's September 300 (recorded
    2026-09-14, commit 21e1cab) applies from 2026-09-14 00:00 ET until the
    provider's October bill opens at 2026-10-01T00:00Z (Sept 30, 8 PM ET), and
    the October default of 400 applies from that instant -- not from the
    Eastern midnight four hours later.
    """

    from app.services.service_day import service_date

    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    day = service_date(aware)
    month = aware.astimezone(UTC).strftime("%Y-%m")
    for entry in RESERVE_SCHEDULE:
        start = date.fromisoformat(entry["from_service_date"])
        end = date.fromisoformat(entry["until_service_date"])
        if start <= day <= end and entry.get("billing_month", month) == month:
            return int(entry["reserve"])
    return DEFAULT_OBJECT_RESERVE


def reserve_for(day: date) -> int:
    """The protected reserve in force on one Eastern service day.

    A day-level view for receipts and projections. Enforcement uses
    ``reserve_at`` so the billing-month boundary is honoured to the instant.
    """

    for entry in RESERVE_SCHEDULE:
        start = date.fromisoformat(entry["from_service_date"])
        end = date.fromisoformat(entry["until_service_date"])
        if start <= day <= end:
            return int(entry["reserve"])
    return DEFAULT_OBJECT_RESERVE


def reserve_note(day: date) -> dict[str, Any]:
    """The reserve and why it is what it is, for a receipt."""

    for entry in RESERVE_SCHEDULE:
        start = date.fromisoformat(entry["from_service_date"])
        end = date.fromisoformat(entry["until_service_date"])
        if start <= day <= end:
            return {"reserve": int(entry["reserve"]), "scheduled": True,
                    "restores_to": DEFAULT_OBJECT_RESERVE,
                    "restores_on": "2026-10-01 (monthly reset)",
                    "authority": entry["authority"], "reason": entry["reason"]}
    return {"reserve": DEFAULT_OBJECT_RESERVE, "scheduled": False,
            "authority": "standing", "reason": "The standing protected reserve."}


#: Kept as a name for callers that still import it. Reads the schedule for
#: today rather than pretending the reserve never changes.
def _today_reserve() -> int:
    return reserve_at(datetime.now(UTC))


OBJECT_RESERVE = DEFAULT_OBJECT_RESERVE

#: The published amateur-tier monthly entity cap. Used only when the provider's
#: own reading is unavailable; the reading itself is preferred every time.
FALLBACK_MONTHLY_CAP = 2500


@dataclass(frozen=True)
class Lane:
    """One consumer's share of the daily budget, and why it has that share."""

    key: str
    label: str
    share: float
    rationale: str
    #: A lane may be switched off entirely without disturbing the others' shares.
    funded: bool = True


#: The allocation. Shares are of the DAILY total and sum to 1.0.
#:
#: MLB pregame takes the majority because it is the only lane feeding a funded
#: decision policy: a missing price there is a decision AISP cannot make. NFL
#: is a research shadow with no wagering authority, so its two lanes are funded
#: deliberately and modestly rather than by default. Final-period enrichment is
#: last because it buys presentation detail for games that are already over --
#: real, but never at the cost of a price.
LANES: tuple[Lane, ...] = (
    Lane(
        key="sgo_mlb_events", label="MLB pregame quotes", share=0.62,
        rationale=(
            "The only lane feeding a funded decision policy. A price missed here "
            "is a decision that cannot be made."
        ),
    ),
    Lane(
        key="sgo_nfl_events", label="NFL pregame quotes", share=0.08,
        rationale=(
            "Funded on the project owner's explicit 2026-09-14 approval, which "
            "opened the NFL virtual research experiment and moved one object a "
            "day out of NFL live scores. A funded paper policy needs a quote "
            "that is fresh AT ITS CHECKPOINT, and no other lane may buy one for "
            "it: pregame retrieval is charged here, never to the live lane."
        ),
    ),
    Lane(
        key="sgo_nfl_live", label="NFL in-game score", share=0.22,
        rationale=(
            "Explicitly funded, and deliberately small -- one object a day "
            "smaller since 2026-09-14, when the owner moved it to NFL pregame. "
            "Four concurrent games is a ceiling on how many may be followed at "
            "once, not a promise that the day can pay for four complete games."
        ),
    ),
    Lane(
        key="sgo_nfl_periods", label="NFL final period breakdown", share=0.08,
        rationale=(
            "One object per completed game, once. It buys presentation detail "
            "for a game that is already over, so it yields to every other lane."
        ),
    ),
    Lane(
        key="sgo_show_preview_events", label="Show Preview day-before quotes",
        share=0.0, funded=False,
        rationale=(
            "Declared so day-before show collection has a lane of its own, and "
            "unfunded so it has no claim on the daily total. A preview quote "
            "and a checkpoint quote are different purchases: the checkpoint "
            "quote has to be fresh AT its checkpoint, and letting the show draw "
            "from the same allowance would spend a decision's price on a "
            "recording. The zero share is why funding this lane cannot quietly "
            "shrink the other four -- it takes an explicit share, which is an "
            "owner decision. Until then `request_objects` refuses it by name."
        ),
    ),
)

LANES_BY_KEY = {lane.key: lane for lane in LANES}

#: Allocation changes, recorded rather than silently applied. A budget that
#: changes shape with no record is one nobody can audit later, and "why does
#: NFL have an object today" needs an answer that outlives this conversation.
ALLOCATION_CHANGES: tuple[dict[str, Any], ...] = (
    {
        "effective_utc": "2026-09-14T16:50:39+00:00",
        "authority": "project owner, explicit approval 2026-09-14",
        "change": "FUND_NFL_PREGAME_FROM_NFL_LIVE",
        "detail": (
            "NFL pregame quotes moved from unfunded to funded, which at the "
            "current 11-object daily total yields MLB pregame 7, NFL pregame 1, "
            "NFL live 2, NFL final periods 1 -- the approved split. The declared "
            "shares are unchanged; only the funded flag moved, so the split is a "
            "consequence of the existing apportionment rather than a hand-set "
            "table that could drift from it."
        ),
        "scope": "virtual research only; no real-money wagering or execution",
        "preserved": [
            "the 400-object reserve",
            "all incurred and in-flight usage",
            "the shared reservation controls",
        ],
    },
    {
        "effective_utc": "2026-09-18T21:00:00+00:00",
        "authority": ("project owner: the September reserve of 300 recorded 2026-09-14 "
                      "(RESERVE_SCHEDULE, commit 21e1cab), applied to both guards on the "
                      "owner's 2026-09-18 instruction"),
        "change": "BUDGET_GUARD_PHASE_2_SHARED_RESERVE_SCHEDULE",
        "detail": (
            "Guard B (sgo_collection.object_budget) held max(400, schedule) in phase 1, so "
            "the owner's September 300 reached only guard A and 96 planned MLB/NFL objects "
            "were unfundable from 2026-09-24. Both guards now apply reserve_at(): 300 for "
            "Eastern service days 2026-09-14..09-30 within the 2026-09 billing month, 400 "
            "from 2026-10-01T00:00Z onward. No other spending rule changes; the CFB lane "
            "keeps its own protected headroom so the remaining margin stays with MLB/NFL."
        ),
        "scope": "virtual research only; no real-money wagering or execution",
        "preserved": [
            "the October default reserve of 400",
            "the monthly entitlement, shares and demand shaping",
            "all incurred and in-flight usage",
        ],
    },
)


@dataclass(frozen=True)
class ExceptionGame:
    """One game an owner exception names, and how it was identified."""

    game_id: str
    matchup: str
    starts_at_et: str


@dataclass(frozen=True)
class OwnerDayException:
    """A dated, game-scoped grant of extra objects. NOT a policy change.

    Everything about it is closed: one Eastern service day, one lane, a fixed
    list of AISP game ids, one object per named game and a total that cannot be
    exceeded however the games are combined. It grants nothing on any other day,
    to any other lane, or for any other game, and it carries the authorisation
    it came from so a receipt can cite it rather than describe it.
    """

    service_date: str
    lane: str
    authority: str
    citation: str
    scope: str
    per_game_objects: int
    total_objects: int
    games: tuple[ExceptionGame, ...]
    #: When set, the exception may also be drawn while the month is PAUSED
    #: (`daily_total == 0`), and never so far that the provider's own remaining
    #: balance -- Guard A's reading, less every object in flight -- would fall
    #: below this number. None (the default) keeps the original behaviour: the
    #: exception adds to an allowance and is unreachable during a pause.
    provider_remaining_floor: int | None = None
    #: A strict cap on requested objects, including failed or canceled attempts.
    #: None preserves other dated exceptions' existing charged-object behavior.
    requested_objects_cap: int | None = None

    @property
    def game_ids(self) -> frozenset[str]:
        return frozenset(game.game_id for game in self.games)

    def as_dict(self) -> dict[str, Any]:
        out = {
            "service_date": self.service_date,
            "lane": self.lane,
            "authority": self.authority,
            "citation": self.citation,
            "scope": self.scope,
            "per_game_objects": self.per_game_objects,
            "total_objects": self.total_objects,
            "games": [
                {"game_id": game.game_id, "matchup": game.matchup,
                 "starts_at_et": game.starts_at_et}
                for game in self.games
            ],
        }
        if self.provider_remaining_floor is not None:
            out["provider_remaining_floor"] = self.provider_remaining_floor
        if self.requested_objects_cap is not None:
            out["requested_objects_cap"] = self.requested_objects_cap
        return out


#: OWNER EXCEPTIONS, BY DATE. An exception, never a rule.
#:
#: The shaped daily allowance is computed from demand and the month's balance,
#: and this file deliberately makes that hard to reach around -- a lane that
#: wants more objects is supposed to be told no. So when the owner authorises
#: more for a specific afternoon, the authorisation is written down HERE, as
#: data, with the day and the games it covers, rather than by moving a share, a
#: rate or a reserve. Nothing about the shaping, the split, the staking, the
#: freshness rules or any other lane changes; the named lane may simply buy up
#: to `total_objects` more objects on `service_date`, one for each of the named
#: games, and only while those games have no purchased quote.
#:
#: It expires by DATE. On 2026-09-21 this tuple grants nothing at all and the
#: allowance is exactly what the day's snapshot issued. There is no flag to
#: clear and nothing to remember to put back.
#:
#: The game ids were resolved from the canonical schedule by team and first
#: pitch on the Eastern service day (one MLB game matched each pairing), not
#: guessed: a wrong id here would spend the owner's object on a game he did not
#: authorise, so identity is the one thing this cannot be casual about.
OWNER_DAY_EXCEPTIONS: tuple[OwnerDayException, ...] = (
    OwnerDayException(
        service_date="2026-09-20",
        lane="sgo_mlb_events",
        authority="project owner, explicit single-day authorisation 2026-09-20",
        citation=(
            "Owner, 2026-09-20: \"MLB's shaped daily allowance rises from 17 to "
            "21 objects for 2026-09-20 only (Eastern service day). The 4 extra "
            "objects may be spent ONLY on these four games, at most one object "
            "each, no substitutions. Not for refreshes, not for retries charged "
            "as another object, not for any other game, sport or date.\""
        ),
        scope=(
            "MLB pregame quotes, Eastern service day 2026-09-20, the four named "
            "games only, at most one object each. No other lane, day, sport or "
            "game draws on it. Today's remaining NFL objects are untouched: the "
            "NFL lane's own allowance is not read, altered or borrowed from "
            "here. The monthly reserve is not read, altered or borrowed from "
            "either -- it is subtracted before any daily total exists, and this "
            "adds objects to one lane's DAILY allowance only, still under both "
            "budget guards and the live provider balance."
        ),
        per_game_objects=1,
        total_objects=4,
        games=(
            ExceptionGame(
                game_id="09872a69-b5c8-4f79-92af-d5548d8ec4f6",
                matchup="San Francisco Giants at Los Angeles Dodgers",
                starts_at_et="2026-09-20T16:10:00-04:00",
            ),
            ExceptionGame(
                game_id="95d1752b-8afb-4cb2-872a-16110d24bf0f",
                matchup="Miami Marlins at San Diego Padres",
                starts_at_et="2026-09-20T16:10:00-04:00",
            ),
            ExceptionGame(
                game_id="acc132fd-b644-4823-981f-bd4c2ac5a8b6",
                matchup="New York Yankees at Arizona Diamondbacks",
                starts_at_et="2026-09-20T16:10:00-04:00",
            ),
            ExceptionGame(
                game_id="693a05f2-2a80-4aa4-8d44-7f2bd37e6ed4",
                matchup="Milwaukee Brewers at Baltimore Orioles",
                starts_at_et="2026-09-20T19:20:00-04:00",
            ),
        ),
    ),
    # NFL Week 5, Sunday 2026-10-04 (Eastern service day). The month is paused
    # (cap 2500, used 2141, remaining 359, reserve 400 -> spendable 0), so this
    # one carries a provider-remaining FLOOR and is the only thing that may draw
    # during the pause: 359 - 14 = 345, so the floor admits all fourteen objects
    # and not one more. Game ids are canonical AISP `games.id`, read-only from
    # the live schedule: these fourteen are every NFL game on the service day.
    OwnerDayException(
        service_date="2026-10-04",
        lane="sgo_nfl_events",
        authority="project owner, explicit single-day reserve exception 2026-10-04",
        citation=(
            "Owner: SportsGameOdds NFL official pregame lane only, Eastern "
            "service day 2026-10-04 only, the fourteen named games, at most one "
            "object each and fourteen in total, never taking the provider's "
            "remaining balance below 345."
        ),
        scope=(
            "NFL official pregame (T-26 checkpoint) quotes on lane sgo_nfl_events, "
            "Eastern service day 2026-10-04, the fourteen named games only, at "
            "most one object each. Lifts the balance pause for those requests "
            "only; no other lane, day, sport, game, preview, live or research "
            "request draws on it. The 400 reserve, the reserve schedule, the "
            "October renewal cap and eligibility are not changed."
        ),
        per_game_objects=1,
        total_objects=14,
        provider_remaining_floor=345,
        games=(
            ExceptionGame("a98c9cdc-99a4-4c55-9e08-80bc2baba451",
                          "Indianapolis Colts at Washington Commanders",
                          "2026-10-04T09:30:00-04:00"),
            ExceptionGame("edf09727-981a-49f9-b720-3f1ad1f3ab98",
                          "Arizona Cardinals at New York Giants",
                          "2026-10-04T13:00:00-04:00"),
            ExceptionGame("e8f2ce2b-3a95-4ddf-a1c7-8fd1ec50cf2e",
                          "Dallas Cowboys at Houston Texans",
                          "2026-10-04T13:00:00-04:00"),
            ExceptionGame("149a8b3a-d547-4c69-a28f-11c462c9b750",
                          "Green Bay Packers at Tampa Bay Buccaneers",
                          "2026-10-04T13:00:00-04:00"),
            ExceptionGame("9be6c229-6169-4085-ae67-47d4b0e77423",
                          "Jacksonville Jaguars at Cincinnati Bengals",
                          "2026-10-04T13:00:00-04:00"),
            ExceptionGame("27c3582a-ed7d-4fa6-bd9e-5f9713eee682",
                          "Los Angeles Rams at Philadelphia Eagles",
                          "2026-10-04T13:00:00-04:00"),
            ExceptionGame("c5ad9fe8-c18b-4888-a6aa-d38ccf41f629",
                          "New England Patriots at Buffalo Bills",
                          "2026-10-04T13:00:00-04:00"),
            ExceptionGame("aa33f876-b548-4d86-acd1-7682692ec857",
                          "New York Jets at Chicago Bears",
                          "2026-10-04T13:00:00-04:00"),
            ExceptionGame("c2c07e12-1fe1-4b9f-aaea-3417b8509ea6",
                          "Tennessee Titans at Baltimore Ravens",
                          "2026-10-04T13:00:00-04:00"),
            ExceptionGame("d392b4c9-7c17-4192-9a4c-9d6a61aaf62a",
                          "Miami Dolphins at Minnesota Vikings",
                          "2026-10-04T16:05:00-04:00"),
            ExceptionGame("470b5b88-2acf-47bf-8cb9-1f515e56ef64",
                          "Denver Broncos at San Francisco 49ers",
                          "2026-10-04T16:25:00-04:00"),
            ExceptionGame("4d345aca-2ca4-4491-86ea-3dd79d8e9e72",
                          "Kansas City Chiefs at Las Vegas Raiders",
                          "2026-10-04T16:25:00-04:00"),
            ExceptionGame("ab7b1506-1dd7-410c-b997-79bd1f1689fe",
                          "Los Angeles Chargers at Seattle Seahawks",
                          "2026-10-04T16:25:00-04:00"),
            ExceptionGame("a7655055-8d55-42d6-8ad3-14878c083146",
                          "Detroit Lions at Carolina Panthers",
                          "2026-10-04T20:20:00-04:00"),
        ),
    ),
    # Monday recovery: James's explicit delegated NFL operations scope.
    # A separate one-game grant; the Sunday entry and standing reserve stay intact.
    OwnerDayException(
        service_date="2026-10-05",
        lane="sgo_nfl_events",
        authority="James, explicit delegated Monday NFL recovery, 2026-10-05",
        citation=(
            "Scoped decision SHA256 765b2d5ac92c2ad1e7cc1416fed733c24a3c7c5f5a874dd820e1fbec19001780. "
            "Owner authorized autonomous Monday recovery while unavailable: "
            "ATL at NO only, official pregame lane sgo_nfl_events, Eastern "
            "service day 2026-10-05, at most one requested event object in total "
            "and per event, with fully reconciled provider remaining at least 358."
        ),
        scope=(
            "NFL official T-26 CHECKPOINT for 2026_04_ATL_NO only. "
            "One reservation consumes the request allowance, even if canceled "
            "or failed with zero charge. Identity, local object-budget and October "
            "renewal-cap guards remain; no retry, preview, diagnostic, "
            "research, live, other-day or Thursday authority. The standing "
            "400 reserve and 2500 account cap are unchanged."
        ),
        per_game_objects=1,
        total_objects=1,
        provider_remaining_floor=358,
        requested_objects_cap=1,
        games=(
            ExceptionGame("5fdb1ee2-ea96-4c38-83e5-67a3b466f4ae",
                          "Atlanta Falcons at New Orleans Saints",
                          "2026-10-05T20:15:00-04:00"),
        ),
    ),

)


def owner_exception_for(day: date, lane: str) -> OwnerDayException | None:
    """The exception in force for one lane on one Eastern service day, if any.

    Matched on the EXACT date string and the EXACT lane key. A day that does not
    match returns None, which is the whole of the expiry mechanism.
    """

    iso = day.isoformat()
    for entry in OWNER_DAY_EXCEPTIONS:
        if entry.service_date == iso and entry.lane == lane:
            return entry
    return None


def _exception_games_priced(session: Session, game_ids: frozenset[str]) -> set[str]:
    """Named games that already hold a purchased provider quote.

    A unit is spent when the object landed -- when a SportsGameOdds quote row is
    bound to that game -- not when a request was planned. That is the same
    evidence `verified_sgo_event_ids` reads, and it is why a refresh of an
    already-priced named game cannot draw on the exception: the authorisation
    covers buying these four games a price, once each, and a game that has one
    has had its object.
    """

    if not game_ids:
        return set()
    import uuid as _uuid_mod

    from app.data_sources.provider_registry import SPORTSGAMEODDS
    from app.models import Market, MarketOutcome, OddsSnapshot

    keys: list[Any] = []
    for value in sorted(game_ids):
        try:
            keys.append(_uuid_mod.UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            keys.append(str(value))
    rows = session.execute(
        select(Market.game_id)
        .join(MarketOutcome, MarketOutcome.market_id == Market.id)
        .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
        .where(Market.game_id.in_(keys), OddsSnapshot.source == SPORTSGAMEODDS)
        .distinct()
    ).all()
    return {str(row[0]) for row in rows}


def _exception_games_in_flight(session: Session, *, lane: str, now: datetime) -> set[str]:
    """Named games an open reservation has already committed an exception unit to.

    The reservation is written before its request goes out and records which
    named games it draws the exception for, so two jobs cannot both claim the
    same game's single unit in the gap before the quote exists.
    """

    from app.services.service_day import service_day_start

    midnight = service_day_start(now)
    terminal = select(ProviderRequestLedger.reservation_id).where(
        ProviderRequestLedger.reservation_id.is_not(None))
    rows = session.scalars(
        select(ProviderRequestLedger).where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.endpoint_key == lane,
            ProviderRequestLedger.status == "RESERVED",
            ProviderRequestLedger.requested_at >= midnight,
            ProviderRequestLedger.id.not_in(terminal),
        )
    ).all()
    out: set[str] = set()
    for row in rows:
        receipt = ((row.response_metadata or {}).get("owner_exception") or {})
        for game_id in (receipt.get("game_ids") or []):
            out.add(str(game_id))
    return out


def owner_exception_state(
    session: Session, *, lane: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Which of an exception's units are still unspent, and why the others are not.

    Returns an inert, all-zero state on any day but the authorised one, for any
    other lane, and whenever anything at all cannot be established. Nothing here
    grants an object; it reports what `request_objects` would be willing to
    grant to a selection made up only of the named games.
    """

    from app.services.service_day import service_date

    moment = now or datetime.now(UTC)
    day = service_date(moment)
    entry = owner_exception_for(day, lane)
    if entry is None:
        return {"exception": None, "units_available": 0, "available_game_ids": [],
                "already_priced": [], "in_flight": []}

    try:
        priced = _exception_games_priced(session, entry.game_ids)
        in_flight = _exception_games_in_flight(session, lane=lane, now=moment)
    except Exception:  # noqa: BLE001 - FAIL CLOSED: an unreadable ledger grants nothing
        return {"exception": entry.as_dict(), "units_available": 0,
                "available_game_ids": [], "already_priced": [], "in_flight": [],
                "refused": "EXCEPTION_STATE_UNREADABLE"}

    spent_or_held = priced | in_flight
    available = sorted(entry.game_ids - spent_or_held)
    consumed = len(entry.game_ids & spent_or_held)
    # Two independent caps, both applied: one object per named game, and a total
    # that holds however the games are combined.
    units = max(0, min(len(available) * entry.per_game_objects,
                       entry.total_objects - consumed * entry.per_game_objects))
    return {
        "exception": entry.as_dict(),
        "units_available": units,
        "available_game_ids": available,
        "already_priced": sorted(entry.game_ids & priced),
        "in_flight": sorted(entry.game_ids & in_flight),
    }


def owner_exception_units(
    session: Session, *, lane: str, selection: list[str] | None,
    now: datetime | None = None,
) -> int:
    """Extra objects this exact selection may draw from the owner's exception.

    FAIL CLOSED, in this order:
      * no selection, or an empty one -- nothing to check, nothing granted;
      * a selection containing ANY game the exception does not name -- zero, for
        the whole selection, not just for the stranger. The authorisation is for
        four specific games and a request that also reaches for a fifth is not
        the request it authorised;
      * a named game that already holds a purchased quote, or that an open
        reservation has already drawn its unit for -- its unit is gone;
      * any other day, any other lane -- inert, zero.
    """

    state = owner_exception_state(session, lane=lane, now=now)
    entry = state["exception"]
    if entry is None or state["units_available"] <= 0:
        return 0
    wanted_games = {str(value) for value in (selection or []) if str(value).strip()}
    if not wanted_games:
        return 0
    named = {str(game["game_id"]) for game in entry["games"]}
    if not wanted_games <= named:
        return 0
    drawable = wanted_games & set(state["available_game_ids"])
    return min(len(drawable) * int(entry["per_game_objects"]),
               int(state["units_available"]))


@dataclass(frozen=True)
class Allocation:
    """One lane's position today."""

    lane: Lane
    daily_allowance: int
    spent_today: int
    in_flight: int
    #: Objects an owner exception adds to this lane TODAY, still unspent. They
    #: are deliberately NOT part of `room`: general room is what any request may
    #: draw, and these may only be drawn by a request whose selection is made up
    #: of the exception's own named games.
    owner_exception_units: int = 0
    owner_exception: dict[str, Any] | None = None

    @property
    def room(self) -> int:
        return max(0, self.daily_allowance - self.spent_today - self.in_flight)

    @property
    def authorised_allowance(self) -> int:
        """The day's allowance including any owner exception still unspent.

        This is the number the owner authorised -- 21 for MLB pregame on
        2026-09-20, 17 on every other day -- and it is reported separately from
        `daily_allowance` on purpose. `daily_allowance` remains what any request
        may draw; the difference is reachable only by the named games.
        """

        return self.daily_allowance + max(0, self.owner_exception_units)

    def as_dict(self) -> dict[str, Any]:
        out = {
            "lane": self.lane.key,
            "label": self.lane.label,
            "share": self.lane.share,
            "funded": self.lane.funded,
            "daily_allowance": self.daily_allowance,
            "authorised_allowance": self.authorised_allowance,
            "spent_today": self.spent_today,
            "in_flight": self.in_flight,
            "room": self.room,
            "rationale": self.lane.rationale,
        }
        if self.owner_exception is not None:
            out["owner_exception"] = self.owner_exception
            out["owner_exception_units"] = self.owner_exception_units
        return out


@dataclass(frozen=True)
class Plan:
    """The whole budget, as it stands right now."""

    monthly_cap: int
    used_this_month: int
    reserve: int
    replenishes_on: date
    days_remaining: int
    daily_total: int
    allocations: dict[str, Allocation]
    source: str
    read_at: datetime | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return max(0, self.monthly_cap - self.used_this_month)

    @property
    def spendable(self) -> int:
        return max(0, self.remaining - self.reserve)

    def as_dict(self) -> dict[str, Any]:
        return {
            "monthly_cap": self.monthly_cap,
            "used_this_month": self.used_this_month,
            "remaining": self.remaining,
            "reserve_never_spent": self.reserve,
            "spendable": self.spendable,
            "replenishes_on": self.replenishes_on.isoformat(),
            "days_remaining": self.days_remaining,
            "daily_total": self.daily_total,
            "balance_source": self.source,
            "balance_read_at": self.read_at.isoformat() if self.read_at else None,
            "lanes": {key: value.as_dict() for key, value in self.allocations.items()},
            "warnings": list(self.warnings),
        }


def replenishment_date(now: datetime | None = None) -> date:
    """The first day of the next UTC month.

    The provider's per-day counter tracks the UTC calendar day exactly -- a
    reading of four requests matched the four made since UTC midnight -- so the
    per-month cap is read the same way.
    """

    moment = now or datetime.now(UTC)
    days_in_month = monthrange(moment.year, moment.month)[1]
    return (moment.date().replace(day=1) + timedelta(days=days_in_month)).replace(day=1)


def apportion(daily_total: int, *, include_unfunded: bool = False) -> dict[str, int]:
    """Whole objects per funded lane, summing exactly to the daily total.

    Largest remainder rather than truncation. At eleven objects a day a lane
    holding eight per cent truncates to nothing, which would quietly defund it
    for the rest of the month; this gives it its turn and keeps the parts adding
    up to the whole. Ties break on the declared lane order, so the same total
    always produces the same split -- a budget that reshuffles between two reads
    is not one anybody can reason about.

    `include_unfunded` answers a reporting question only: what an unfunded lane
    WOULD receive if it were switched on. It exists so a zero allowance can be
    shown as the policy choice it is rather than read as rounding, and it never
    grants anything -- `request_objects` refuses an unfunded lane by name
    whatever this returns.
    """

    funded = [lane for lane in LANES if lane.funded or include_unfunded]
    if not funded or daily_total <= 0:
        return {lane.key: 0 for lane in LANES}

    weight = sum(lane.share for lane in funded)
    exact = {lane.key: daily_total * lane.share / weight for lane in funded}
    out = {lane.key: int(exact[lane.key]) for lane in funded}

    remainder = daily_total - sum(out.values())
    ranked = sorted(
        funded,
        key=lambda lane: (-(exact[lane.key] - int(exact[lane.key])),
                          LANES.index(lane)),
    )
    for lane in ranked[:max(0, remainder)]:
        out[lane.key] += 1
    return {lane.key: out.get(lane.key, 0) for lane in LANES}


def latest_usage_reading(session: Session) -> ProviderRequestLedger | None:
    """The most recent authoritative balance AISP recorded from the provider."""

    return session.scalar(
        select(ProviderRequestLedger)
        .where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.endpoint_key == USAGE_ENDPOINT_KEY,
            ProviderRequestLedger.status == "SUCCEEDED",
        )
        .order_by(ProviderRequestLedger.requested_at.desc())
        .limit(1)
    )


def _local_month_spend(session: Session, *, now: datetime) -> int:
    """What AISP's own ledger says it has spent this UTC month."""

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = session.scalar(
        select(func.coalesce(func.sum(ProviderRequestLedger.records_received), 0))
        .where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.status == "SUCCEEDED",
            ProviderRequestLedger.endpoint_key != USAGE_ENDPOINT_KEY,
            ProviderRequestLedger.requested_at >= month_start,
        )
    )
    return int(total or 0)


#: The leagues each lane is actually allowed to buy. A row whose own retained
#: request evidence names a league outside its lane's set was booked to the
#: wrong lane.
#:
#: Most lanes buy exactly one league. The Show Preview lane is the exception:
#: one day-before run covers whatever show sports are on the slate, so MLB and
#: NFL are both legitimate there. It is still a closed set -- NBA and College
#: Football are absent because SportsGameOdds' identifiers for them are in no
#: retained evidence, so a row claiming one would be evidence of a guess.
LANE_LEAGUES: dict[str, frozenset[str]] = {
    "sgo_mlb_events": frozenset({"MLB"}),
    "sgo_nfl_events": frozenset({"NFL"}),
    "sgo_nfl_live": frozenset({"NFL"}),
    "sgo_nfl_periods": frozenset({"NFL"}),
    "sgo_show_preview_events": frozenset({"MLB", "NFL"}),
}

#: The single-league view, kept for callers and receipts that read one name. A
#: multi-league lane reports None rather than picking one of its leagues.
LANE_LEAGUE: dict[str, str | None] = {
    key: (next(iter(leagues)) if len(leagues) == 1 else None)
    for key, leagues in LANE_LEAGUES.items()
}


def _recorded_league(row: ProviderRequestLedger) -> str | None:
    """The leagueID the request actually carried, from its retained evidence."""

    meta = row.response_metadata or {}
    filters = ((meta.get("request_shape") or {}).get("filters") or {})
    league = filters.get("leagueID")
    return str(league).upper() if league else None


def misattributed_rows(
    session: Session, *, now: datetime,
) -> list[ProviderRequestLedger]:
    """Today's rows whose retained evidence contradicts the lane they charge.

    This is not hypothetical. Before the NFL collectors were taught to name
    their own lane (2026-09-14, commits 60167ee and 8800f26), they called
    `record_request` without an `endpoint_key` and fell through to its default
    -- which is the MLB lane. Eighteen NFL objects were therefore booked as MLB
    pregame spend on the evening of 2026-09-13, and because the budget day is
    the UTC day, they exhausted the MLB allowance for the whole of 09-14 before
    that slate had any chance to be priced.

    The collector defect is fixed. The ledger rows are left exactly as written
    -- they are the record of what was requested, and rewriting them would
    destroy the evidence this correction depends on. Instead the ATTRIBUTION is
    corrected here, from the rows' own retained filters.
    """

    from app.services.service_day import service_day_start

    midnight = service_day_start(now)
    rows = session.scalars(
        select(ProviderRequestLedger).where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.status == "SUCCEEDED",
            ProviderRequestLedger.endpoint_key != USAGE_ENDPOINT_KEY,
            ProviderRequestLedger.requested_at >= midnight,
        )
    ).all()
    out = []
    for row in rows:
        permitted = LANE_LEAGUES.get(row.endpoint_key or "")
        actual = _recorded_league(row)
        if permitted and actual and actual not in permitted:
            out.append(row)
    return out


def _lane_usage(session: Session, *, now: datetime) -> dict[str, tuple[int, int]]:
    """Per lane: objects settled today, and objects reserved but not yet settled.

    In-flight matters for concurrency. A reservation is written before its
    request goes out and carries the count it asked for, so a job checking the
    budget can see what another job is already committed to spending and the two
    cannot both claim the last object.
    """

    # The EASTERN service day, not the UTC day. An NFL request at 22:00 ET
    # belongs to that evening's budget, not to the next morning's slate --
    # which is precisely the confusion that cost 2026-09-14 its MLB prices.
    from app.services.service_day import service_day_start

    midnight = service_day_start(now)

    settled = dict(session.execute(
        select(
            ProviderRequestLedger.endpoint_key,
            func.coalesce(func.sum(ProviderRequestLedger.records_received), 0),
        )
        .where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.status == "SUCCEEDED",
            ProviderRequestLedger.requested_at >= midnight,
        )
        .group_by(ProviderRequestLedger.endpoint_key)
    ).all())

    # A reservation is in flight until a terminal row points back at it.
    terminal = select(ProviderRequestLedger.reservation_id).where(
        ProviderRequestLedger.reservation_id.is_not(None))
    pending = dict(session.execute(
        select(
            ProviderRequestLedger.endpoint_key,
            func.coalesce(func.sum(ProviderRequestLedger.quota_cost), 0),
        )
        .where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.status == "RESERVED",
            ProviderRequestLedger.requested_at >= midnight,
            ProviderRequestLedger.id.not_in(terminal),
        )
        .group_by(ProviderRequestLedger.endpoint_key)
    ).all())

    # Subtract spend that its own evidence says belongs to another league. The
    # objects are still counted against the MONTHLY total -- they were really
    # bought -- but a lane must not be charged for a request it never made.
    misbooked: dict[str, int] = {}
    for row in misattributed_rows(session, now=now):
        key = row.endpoint_key or ""
        misbooked[key] = misbooked.get(key, 0) + int(row.records_received or 0)

    return {
        lane.key: (
            max(0, int(settled.get(lane.key, 0)) - misbooked.get(lane.key, 0)),
            int(pending.get(lane.key, 0)),
        )
        for lane in LANES
    }


def daily_snapshot(session: Session, *, now: datetime | None = None):
    """This Eastern service day's allocation, computed once and then held.

    THE BEHAVIOUR THIS REPLACES
    ---------------------------
    The allowance used to be recomputed from the remaining balance on every
    read, so spending inside a day lowered that same day's allowance underneath
    itself -- a lane told it had eight objects could draw only seven. An
    allowance that shrinks as it is used cannot be planned against.

    So the day's capacity is fixed at its first calculation, from the
    provider's actual balance at that moment, and reconciled against
    afterwards rather than reduced. Tomorrow reads tomorrow's balance, so a
    heavy day still costs the next one -- which is the part that should carry.

    The hard remaining balance and the reserve are enforced separately on every
    grant, so a stale snapshot can never authorise a spend the account cannot
    fund.
    """

    from app.models import ProviderAllocationSnapshot
    from app.services.allocation_demand import measure
    from app.services.service_day import ALLOCATION_DAY_SEMANTICS, service_date

    moment = now or datetime.now(UTC)
    day = service_date(moment)

    existing = session.scalar(select(ProviderAllocationSnapshot).where(
        ProviderAllocationSnapshot.provider == PROVIDER,
        ProviderAllocationSnapshot.service_date == day))
    if existing is not None:
        return existing

    cap, used, source, read_at = _provider_balance(session, moment)
    reserve = reserve_at(moment)
    replenishes = replenishment_date(moment)
    days_remaining = max(1, (replenishes - moment.date()).days)
    spendable = max(0, cap - used - reserve)

    demand = measure(session, now=moment)
    # The month's spendable total is unchanged; only its SHAPE across days
    # follows demand. See _shaped_daily_total.
    shaping = _shaped_daily_total(session, day=day, spendable=spendable,
                                  days_remaining=days_remaining, demand=demand)
    daily_total = shaping["daily_total"]
    demand = {**demand, "shaping": shaping}
    lanes = _demand_aware_split(daily_total, demand,
                                mlb_need=shaping["today"]["mlb"])

    snapshot = ProviderAllocationSnapshot(
        provider=PROVIDER, service_date=day,
        semantics=ALLOCATION_DAY_SEMANTICS,
        monthly_cap=cap, used_this_month=used, remaining=max(0, cap - used),
        balance_source=source, balance_read_at=read_at,
        reserve=reserve, days_remaining=days_remaining,
        daily_total=daily_total, lanes=lanes, demand=demand, taken_at=moment)
    session.add(snapshot)
    try:
        session.flush()
    except Exception:  # noqa: BLE001 - another worker won the race; use theirs
        session.rollback()
        return session.scalar(select(ProviderAllocationSnapshot).where(
            ProviderAllocationSnapshot.provider == PROVIDER,
            ProviderAllocationSnapshot.service_date == day))
    return snapshot


def _demand_aware_split(daily_total: int, demand: dict[str, Any], *,
                        mlb_need: int = 0) -> dict[str, int]:
    """NFL reservations and MLB's need, both funded when the day can fund both.

    Reservations come off the top because they are specific: one object for one
    named game with a decision to make. That order was safe only while the day
    was large enough for MLB too. On 2026-09-18 it was not: fourteen NFL objects
    came off a seventeen-object day and left MLB three for fifteen games, which
    nobody decided. So when a day cannot fund both in full, every lane is cut by
    the SAME fraction instead of MLB silently absorbing the whole shortfall.
    """

    reservations = dict(demand.get("reservations") or {})
    out = {lane.key: 0 for lane in LANES}
    budget = max(0, int(daily_total))
    nfl_keys = ("sgo_nfl_events", "sgo_nfl_live", "sgo_nfl_periods")
    wants = {key: max(0, int(reservations.get(key, 0))) for key in nfl_keys}
    need = sum(wants.values()) + max(0, int(mlb_need))
    scale = 1.0 if need <= budget or need == 0 else budget / need
    for key in nfl_keys:
        take = min(budget, int(wants[key] * scale))
        out[key] = take
        budget -= take
    # Any OTHER funded lane takes its own declared share before MLB absorbs the
    # remainder. Today that loop does nothing: the only lane it can reach is
    # Show Preview, which is unfunded with a zero share. It exists so that
    # funding that lane later grants it its declared share explicitly, rather
    # than silently shrinking MLB's remainder by an amount nobody wrote down.
    named = {"sgo_mlb_events", "sgo_nfl_events", "sgo_nfl_live", "sgo_nfl_periods"}
    for lane in LANES:
        if lane.key in named or not lane.funded:
            continue
        take = min(budget, max(0, int(daily_total * lane.share)))
        out[lane.key] = take
        budget -= take
    out["sgo_mlb_events"] = budget
    return out


#: MLB objects per game on the days the lane was fully funded (2026-09-15 and
#: 2026-09-16: 17 objects for 15 games each). It is the lane's own historical
#: funded rate, used to size a day's MLB need -- not a new coverage target.
MLB_OBJECTS_PER_GAME = 17 / 15


def _count_games(session: Session, code: str, start: datetime, end: datetime,
                 statuses: tuple[Any, ...]) -> int:
    """Games of one league on a service day, selected the way its owner selects.

    source_mode is provenance, never sport. MLB's collector prices LIVE games
    only, so MLB is counted that way; NFL games are persisted RESEARCH_SHADOW
    and the NFL demand code has never filtered on it. Filtering NFL on LIVE
    counted every future NFL checkpoint as zero.
    """

    from app.models import Game, League

    query = (select(func.count(Game.id)).join(League, League.id == Game.league_id)
             .where(League.code == code, Game.status.in_(statuses),
                    Game.starts_at >= start, Game.starts_at < end))
    if code == "MLB":
        query = query.where(Game.source_mode == "LIVE")
    return int(session.scalar(query) or 0)


def _day_need(session: Session, day: date) -> dict[str, int]:
    """Projected objects for one FUTURE service day, from the persisted schedule.

    MLB: games on that day at its funded rate. NFL: one checkpoint object per
    game on that day, plus one Show Prep preview object per game whose preview
    slot falls on that day -- the collector's own two rules, nothing added.
    """

    import math

    from app.models import GameStatus
    from app.services import nfl_paper_policy as policy
    from app.services.service_day import service_day_bounds

    start, end = service_day_bounds(day)
    mlb = math.ceil(_count_games(session, "MLB", start, end, (GameStatus.SCHEDULED,))
                    * MLB_OBJECTS_PER_GAME)
    nfl = 0
    if policy.ACTIVATED:
        from app.services.allocation_demand import _preview_games_on_service_day

        nfl = (_count_games(session, "NFL", start, end, (GameStatus.SCHEDULED,))
               + _preview_games_on_service_day(session, start, end))
    return {"mlb": mlb, "nfl": nfl}


def _shaped_daily_total(session: Session, *, day: date, spendable: int,
                        days_remaining: int, demand: dict[str, Any]) -> dict[str, Any]:
    """Today's share of the month, shaped by demand instead of split evenly.

    WHY
    ---
    An even `spendable // days_remaining` gives every day the same seventeen
    objects. NFL demand does not arrive evenly: a Sunday needs fourteen NFL
    checkpoints on top of fifteen MLB games, while a quiet Monday needs five.
    In late September the month fitted (211 of 219) and the weekend still
    starved MLB, because quiet days kept objects that weekends needed.

    RULE
    ----
    Every remaining day of the billing month is given its projected need. When
    the month can fund all of it, today gets its own need plus an even share of
    whatever is left over. When it cannot, today gets the same fraction of its
    need as every other day -- nobody's shortfall is dumped on one lane.

    Nothing here moves the monthly total, the reserve or a quota. The live
    balance and the reserve are still enforced on every grant, so a projection
    that turns out wrong can refuse a spend but never overdraw the account. With
    no schedule to project from, this is exactly the old even split.
    """

    import math

    from app.models import GameStatus
    from app.services.service_day import service_day_bounds

    start, end = service_day_bounds(day)
    today_games = _count_games(session, "MLB", start, end,
                               (GameStatus.SCHEDULED, GameStatus.LIVE))
    today_mlb = math.ceil(today_games * MLB_OBJECTS_PER_GAME)
    today_nfl = sum(max(0, int(v)) for v in (demand.get("reservations") or {}).values())
    today_need = today_mlb + today_nfl

    future: list[dict[str, Any]] = []
    for offset in range(1, max(1, days_remaining)):
        other = day + timedelta(days=offset)
        need = _day_need(session, other)
        future.append({"date": other.isoformat(), **need, "total": need["mlb"] + need["nfl"]})
    total_need = today_need + sum(entry["total"] for entry in future)

    if total_need <= spendable:
        slack = spendable - total_need
        daily_total = today_need + slack // max(1, days_remaining)
        basis = "NEED_PLUS_EVEN_SHARE_OF_SLACK"
    else:
        daily_total = (spendable * today_need) // total_need if total_need else 0
        basis = "PRO_RATA_SHORTFALL"
    daily_total = max(0, min(int(spendable), int(daily_total)))

    return {
        "basis": basis,
        "daily_total": daily_total,
        "even_split_would_have_been": spendable // max(1, days_remaining),
        "spendable": spendable,
        "month_projected_need": total_need,
        "month_fits": total_need <= spendable,
        "today": {"mlb": today_mlb, "mlb_games": today_games, "nfl": today_nfl,
                  "total": today_need},
        "future_days": future,
        "mlb_objects_per_game": round(MLB_OBJECTS_PER_GAME, 4),
    }


def _same_billing_month(read_at: datetime | None, moment: datetime) -> bool:
    """Does a usage reading describe the billing month ``moment`` falls in?

    The provider bills a UTC calendar month and refills at 00:00 UTC on the 1st.
    A reading taken at 23:50 UTC on September 30 reports SEPTEMBER's usage; used
    as October's opening balance it says the account is nearly spent when it has
    just refilled, and every lane goes dark for the day. So a reading from an
    earlier month is not a balance -- it is history.
    """

    if read_at is None:
        return False
    aware = _aware_dt(read_at).astimezone(UTC)
    current = moment.astimezone(UTC) if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return (aware.year, aware.month) == (current.year, current.month)


def _provider_balance(session: Session, moment: datetime):
    """The provider's own cap and usage, plus anything spent since the reading.

    Falls back to AISP's own current-month count -- which is the conservative
    direction, since it can only over-report what has been spent -- when there
    is no reading, when the reading carries no cap, or when the newest reading
    belongs to an earlier billing month.
    """

    reading = latest_usage_reading(session)
    meta = dict((reading.response_metadata or {}) if reading else {})
    usage = dict(meta.get("usage") or {})
    cap = int(usage.get("monthly_cap") or 0)
    used = int(usage.get("used_this_month") or 0)
    source = "PROVIDER_ACCOUNT_USAGE"
    read_at = reading.requested_at if reading else None
    if cap and not _same_billing_month(read_at, moment):
        # Provenance cannot be established for THIS month, so the reading is
        # refused rather than trusted. Fail closed on the source, not on the
        # account: the local count for the new month is still authoritative
        # about what AISP itself has spent.
        return (cap, _local_month_spend(session, now=moment),
                "LOCAL_LEDGER_COUNT_PRIOR_MONTH_READING_IGNORED", None)
    if not cap:
        return (FALLBACK_MONTHLY_CAP, _local_month_spend(session, now=moment),
                "LOCAL_LEDGER_COUNT", None)
    since = session.scalar(
        select(func.coalesce(func.sum(ProviderRequestLedger.records_received), 0))
        .where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.status == "SUCCEEDED",
            ProviderRequestLedger.endpoint_key != USAGE_ENDPOINT_KEY,
            ProviderRequestLedger.requested_at > reading.requested_at,
        ))
    return cap, used + int(since or 0), source, read_at


def build_plan(session: Session, *, now: datetime | None = None) -> Plan:
    """This service day's ISSUED allocation, plus what has been spent against it.

    The allowances come from the day's snapshot and do not move as objects are
    consumed. Only `spent_today` and `room` move, which is what a reader
    actually needs: how much of a fixed allowance is left, not a fixed-looking
    number that is quietly falling.
    """

    moment = now or datetime.now(UTC)
    warnings: list[str] = []

    snapshot = daily_snapshot(session, now=moment)
    cap = int(snapshot.monthly_cap)
    # The reserve in force NOW, not the one frozen into the day's snapshot: the
    # Sept 30 snapshot (300) outlives the September bill by four hours, and the
    # October default must hold from the provider's reset.
    reserve = reserve_at(moment)
    issued = dict(snapshot.lanes or {})

    if snapshot.balance_source == "LOCAL_LEDGER_COUNT_PRIOR_MONTH_READING_IGNORED":
        warnings.append(
            "The newest provider usage reading belongs to an earlier billing "
            "month, so it was ignored and this day's balance is AISP's own "
            "count for the current month against the provider's cap. It cannot "
            "see a spend made elsewhere. A fresh usage reading -- which costs "
            "no objects -- restores the provider's own figure.")
    elif snapshot.balance_source == "LOCAL_LEDGER_COUNT":
        warnings.append(
            "No provider usage reading was recorded when this day's allocation "
            "was taken, so its balance is AISP's own count against the "
            "published cap. It cannot see a spend made elsewhere.")

    # The LIVE balance, enforced on every grant regardless of the snapshot. A
    # fixed allowance must never outlive the money behind it.
    live_cap, live_used, live_source, live_read_at = _provider_balance(session, moment)
    if live_read_at is not None:
        aware = _aware_dt(live_read_at)
        if moment - aware > timedelta(hours=6):
            warnings.append(
                f"The provider balance was last read {aware.isoformat()}; a "
                "spend made outside AISP since then is not reflected.")

    lane_usage = _lane_usage(session, now=moment)
    allocations: dict[str, Allocation] = {}
    for lane in LANES:
        spent, flight = lane_usage.get(lane.key, (0, 0))
        # The day's ISSUED allowance is untouched. An owner exception is carried
        # alongside it, never folded into it, so every other lane's allowance --
        # NFL's fifteen and whatever of it remains -- is arithmetically unable
        # to move, and so is the reserve that was subtracted before any of this.
        state = owner_exception_state(session, lane=lane.key, now=moment)
        allocations[lane.key] = Allocation(
            lane=lane, daily_allowance=int(issued.get(lane.key, 0)),
            spent_today=spent, in_flight=flight,
            owner_exception_units=int(state["units_available"]),
            owner_exception=(None if state["exception"] is None else {
                **state["exception"],
                "units_available": int(state["units_available"]),
                "available_game_ids": state["available_game_ids"],
                "already_priced": state["already_priced"],
                "in_flight": state["in_flight"],
            }))

    if int(snapshot.daily_total) == 0:
        warnings.append(
            f"{max(0, live_cap - live_used - reserve)} objects are spendable "
            f"across {snapshot.days_remaining} days, so no lane has a whole "
            "object a day. Collection is paused until the balance replenishes "
            f"on {snapshot.replenishes_on if hasattr(snapshot, 'replenishes_on') else replenishment_date(moment).isoformat()}.")

    return Plan(
        monthly_cap=live_cap, used_this_month=live_used, reserve=reserve,
        replenishes_on=replenishment_date(moment),
        days_remaining=int(snapshot.days_remaining),
        daily_total=int(snapshot.daily_total), allocations=allocations,
        source=live_source, read_at=live_read_at, warnings=warnings)


def _aware_dt(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Grant:
    """How many objects a lane may spend right now, and why not more."""

    lane: str
    wanted: int
    granted: int
    reason: str
    plan: Plan

    @property
    def allowed(self) -> bool:
        return self.granted > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane, "wanted": self.wanted, "granted": self.granted,
            "reason": self.reason,
            "allocation": self.plan.allocations[self.lane].as_dict()
            if self.lane in self.plan.allocations else None,
            "budget": {
                "spendable": self.plan.spendable,
                "daily_total": self.plan.daily_total,
                "replenishes_on": self.plan.replenishes_on.isoformat(),
                "days_remaining": self.plan.days_remaining,
                "balance_source": self.plan.source,
            },
        }


def _committed_objects(row: ProviderRequestLedger) -> int:
    """The most a reservation can have been charged, read from the row itself.

    The larger of the count it committed to (`quota_cost`) and the `limit` its
    retained request carried -- the provider bills the event objects it
    returns and returns at most `limit` of them. A row that states neither is
    an UNKNOWN commitment and is charged the provider's per-fetch maximum, the
    same worst case `sgo_collection.object_budget` charges an unknown count.
    """

    from app.services.sgo_collection import MAX_EVENTS_PER_FETCH

    committed = max(0, int(row.quota_cost or 0))
    filters = (((row.response_metadata or {}).get("request_shape") or {})
               .get("filters") or {})
    try:
        committed = max(committed, int(filters.get("limit") or 0))
    except (TypeError, ValueError):
        pass
    return committed if committed > 0 else int(MAX_EVENTS_PER_FETCH)


def provider_outstanding_commitments(
    session: Session, *, reading: ProviderRequestLedger,
) -> dict[str, Any]:
    """Every SportsGameOdds object that may be charged and is not in ``reading``.

    PROVIDER-WIDE AND UNTRUNCATED: every lane, every service day, every month.
    Nothing here is cut to today, to a lane or to a window, because a commitment
    made at 03:59Z -- one minute before the Eastern service day starts -- costs
    the provider's balance exactly what one made at 04:01Z does.

    The provider's own reading (`used_this_month`) is the base. On top of it:

    * UNRESOLVED -- every reservation with no terminal row, whenever it was made
      and on whichever lane, charged everything it may have cost
      (`_committed_objects`). Whether the provider has already billed it is
      unknown, so it is counted even if the reading might include it.
    * COMPLETED AFTER THE READING -- every terminal row, SUCCEEDED or FAILED,
      whose `completed_at` is after the reading was REQUESTED, charged its
      `records_received`. A FAILED row that carries a charge is a charge. A
      terminal row with no count is an UNKNOWN charge and is charged its
      reservation's full commitment. This includes a reservation made BEFORE
      the reading that completed after it: the reading may not have seen it.
    * Everything that completed at or before the moment the reading was
      requested is in the provider's own figure and is NOT added again.

    The split errs one way only: a charge that completed while the reading was
    in flight is counted twice, never zero times. Reservations are read, never
    cleared or settled here -- room is never made by tidying the ledger.
    """

    read_at = reading.requested_at
    excluded = (USAGE_ENDPOINT_KEY,)
    terminal_ids = select(ProviderRequestLedger.reservation_id).where(
        ProviderRequestLedger.reservation_id.is_not(None))
    unresolved_rows = session.scalars(
        select(ProviderRequestLedger).where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.endpoint_key.not_in(excluded),
            ProviderRequestLedger.status == "RESERVED",
            ProviderRequestLedger.id.not_in(terminal_ids),
        )
    ).all()
    unresolved = sum(_committed_objects(row) for row in unresolved_rows)

    completed_rows = session.scalars(
        select(ProviderRequestLedger).where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.endpoint_key.not_in(excluded),
            ProviderRequestLedger.status.in_(("SUCCEEDED", "FAILED")),
            ProviderRequestLedger.completed_at > read_at,
        )
    ).all()
    completed = 0
    charged_failed = 0
    unknown = 0
    straddling = 0
    for row in completed_rows:
        if row.records_received is None:
            unknown += 1
            parent = (session.get(ProviderRequestLedger, row.reservation_id)
                      if row.reservation_id is not None else None)
            charge = (_committed_objects(parent) if parent is not None
                      else _committed_objects(row))
        else:
            charge = max(0, int(row.records_received))
            if str(row.status) == "FAILED" and charge > 0:
                charged_failed += 1
        if _aware_dt(row.requested_at) <= _aware_dt(read_at):
            straddling += 1
        completed += charge

    return {
        "reading_requested_at": _aware_dt(read_at).isoformat(),
        "unresolved_reservations": len(unresolved_rows),
        "unresolved_objects": int(unresolved),
        "completed_after_reading": len(completed_rows),
        "completed_after_reading_objects": int(completed),
        "charged_failed_after_reading": charged_failed,
        "unknown_charge_after_reading": unknown,
        "reserved_before_reading_completed_after": straddling,
        "objects": int(unresolved + completed),
    }


def _owner_exception_draw(
    session: Session, plan: Plan, *, lane: str, selection: list[str] | None,
    now: datetime,
) -> int:
    """`owner_exception_units`, held above the exception's provider floor if it has one.

    An exception without a floor is returned unchanged (the 2026-09-20 MLB
    behaviour). With a floor, the draw is cut so that the provider's remaining
    balance stays at or above it, measured the more conservative of two ways:

    * RECONCILED -- the provider's own reading (cap less `used_this_month`) less
      every outstanding commitment it may not contain,
      `provider_outstanding_commitments`: provider-wide, every lane and every
      day, unresolved reservations and unknown charges counted as charged;
    * GUARD A -- `plan.monthly_cap - plan.used_this_month`, less everything any
      lane holds in flight today.

    FAIL CLOSED: without a provider account reading for the current billing
    month, or if the ledger cannot be read, nothing is drawn.
    """

    from app.services.service_day import service_date

    units = owner_exception_units(session, lane=lane, selection=selection, now=now)
    if units <= 0:
        return 0
    entry = owner_exception_for(service_date(now), lane)
    if entry is None:
        return 0
    if entry.provider_remaining_floor is None:
        return units
    if plan.source != "PROVIDER_ACCOUNT_USAGE":
        return 0
    try:
        reading = latest_usage_reading(session)
        if reading is None or not _same_billing_month(reading.requested_at, now):
            return 0
        usage = dict(((reading.response_metadata or {}).get("usage")) or {})
        cap = int(usage.get("monthly_cap") or 0)
        used = int(usage.get("used_this_month") or 0)
        if cap <= 0:
            return 0
        outstanding = provider_outstanding_commitments(session, reading=reading)
    except Exception:  # noqa: BLE001 - FAIL CLOSED: an unreadable ledger draws nothing
        return 0
    reconciled = cap - used - int(outstanding["objects"])
    in_flight = sum(max(0, int(a.in_flight)) for a in plan.allocations.values())
    guard_a = int(plan.monthly_cap) - int(plan.used_this_month) - in_flight
    headroom = min(reconciled, guard_a) - int(entry.provider_remaining_floor)
    return max(0, min(units, headroom))



def _owner_requested_object_room(
    session: Session, *, lane: str, now: datetime,
) -> int | None:
    """Remaining strict request authority, where a dated entry specifies it.

    Count each reservation root once, independently of its terminal status or
    charge. The Monday grant authorizes one requested object, so a zero-charge
    failure or cancellation must not release another request. Legacy terminal
    rows without a reservation also count conservatively. This guard runs even
    if a later provider reading would reopen the ordinary allocation.
    """
    from app.services.service_day import service_date, service_day_bounds

    day = service_date(now)
    entry = owner_exception_for(day, lane)
    if entry is None or entry.requested_objects_cap is None:
        return None
    start, end = service_day_bounds(day)
    try:
        rows = session.scalars(select(ProviderRequestLedger).where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.endpoint_key == lane,
            ProviderRequestLedger.requested_at >= start,
            ProviderRequestLedger.requested_at < end,
            ProviderRequestLedger.reservation_id.is_(None),
        )).all()
        committed = sum(_committed_objects(row) for row in rows)
    except Exception:  # unreadable request evidence grants nothing
        return 0
    return max(0, int(entry.requested_objects_cap) - committed)


def request_objects(
    session: Session, *, lane: str, wanted: int, now: datetime | None = None,
    selection: list[str] | None = None,
) -> Grant:
    """The single enforcement point. Every consumer asks here before reserving.

    Grants at most what the lane's remaining daily allowance permits, after
    subtracting both what it has already spent today and what it has in flight.
    Never grants into the reserve, because the reserve is subtracted before the
    daily total is computed at all.

    ``selection`` names the AISP games the caller intends this request to buy.
    It is optional and it can only ever grant LESS than the caller asked for,
    never more, with one exception: a dated owner exception (see
    `OWNER_DAY_EXCEPTIONS`) adds objects for a selection made up entirely of the
    games that exception names. A caller that passes nothing gets exactly the
    behaviour it always had.
    """

    moment = now or datetime.now(UTC)
    plan = build_plan(session, now=moment)
    if lane not in plan.allocations:
        return Grant(lane=lane, wanted=wanted, granted=0, plan=plan,
                     reason=f"UNKNOWN_LANE:{lane}")

    request_room = _owner_requested_object_room(session, lane=lane, now=moment)
    if request_room is not None and request_room <= 0:
        return Grant(lane=lane, wanted=wanted, granted=0, plan=plan,
                     reason="OWNER_EXCEPTION_REQUEST_LIMIT_REACHED")
    request_limit = int(wanted) if request_room is None else min(int(wanted), request_room)

    allocation = plan.allocations[lane]
    if not allocation.lane.funded:
        return Grant(lane=lane, wanted=wanted, granted=0, plan=plan,
                     reason="LANE_NOT_FUNDED")
    if plan.daily_total == 0:
        # A PAUSED MONTH. Only a dated exception that carries a provider floor
        # can be drawn here, only for its own named games, and only down to
        # that floor. On every other day and lane `owner_exception_for` is None
        # and this returns exactly the refusal it always did.
        from app.services.service_day import service_date

        entry = owner_exception_for(service_date(moment), lane)
        if entry is not None and entry.provider_remaining_floor is not None:
            units = _owner_exception_draw(
                session, plan, lane=lane, selection=selection, now=moment)
            granted = max(0, min(request_limit, units))
            if granted > 0:
                return Grant(lane=lane, wanted=wanted, granted=granted, plan=plan,
                             reason="GRANTED_OWNER_EXCEPTION"
                             if granted == wanted else "GRANTED_OWNER_EXCEPTION_PARTIAL")
        return Grant(lane=lane, wanted=wanted, granted=0, plan=plan,
                     reason="BUDGET_PAUSED_UNTIL_REPLENISHMENT")

    granted = max(0, min(request_limit, allocation.room))
    # THE OWNER'S EXCEPTION, AND ONLY FOR THE GAMES IT NAMES.
    #
    # Reached only once the lane's ordinary allowance cannot cover the request,
    # so the exception is never spent while the lane still has room of its own.
    # `owner_exception_units` returns 0 on every other day, for every other
    # lane, for a selection carrying any unnamed game, and for a named game that
    # already holds a purchased quote.
    exception_units = 0
    if granted < int(wanted):
        exception_units = _owner_exception_draw(
            session, plan, lane=lane, selection=selection, now=moment)
        if exception_units > 0:
            granted = max(0, min(request_limit, allocation.room + exception_units))
    if granted == 0:
        return Grant(lane=lane, wanted=wanted, granted=0, plan=plan,
                     reason="LANE_DAILY_ALLOWANCE_SPENT")
    if exception_units > 0 and granted > allocation.room:
        return Grant(lane=lane, wanted=wanted, granted=granted, plan=plan,
                     reason="GRANTED_OWNER_EXCEPTION"
                     if granted == wanted else "GRANTED_OWNER_EXCEPTION_PARTIAL")
    reason = "GRANTED" if granted == wanted else "GRANTED_PARTIAL"
    return Grant(lane=lane, wanted=wanted, granted=granted, plan=plan, reason=reason)


def quote_affordability(
    session: Session, *, lane: str = "sgo_nfl_events", objects: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Whether one more capture on a lane fits the authorised allowance.

    Asks the enforcement point the same question a collector would, and reports
    the answer. It reserves nothing, writes nothing and moves no accounting: a
    refusal here is the guard working, not an obstacle to route around.
    """

    grant = request_objects(session, lane=lane, wanted=objects, now=now)
    allocation = grant.plan.allocations.get(lane)
    reasons = {
        "GRANTED": "The lane has room today and the capture fits.",
        "GRANTED_PARTIAL": (
            "The lane has some room but less than requested, so only part of "
            "the capture fits."
        ),
        "GRANTED_OWNER_EXCEPTION": (
            "The lane's own allowance for today is spent, and the capture fits "
            "only because every game in it is named by a dated owner exception "
            "that has an unspent object for it."
        ),
        "GRANTED_OWNER_EXCEPTION_PARTIAL": (
            "Part of the capture fits, and only because the games in it are "
            "named by a dated owner exception with some units still unspent."
        ),
        "LANE_NOT_FUNDED": (
            "The lane is switched off by policy, so it has no allowance to "
            "spend. This is a funding decision, not a depleted balance, and "
            "turning it on changes every other lane's share."
        ),
        "LANE_DAILY_ALLOWANCE_SPENT": (
            "The lane's allowance for today is already spent or reserved. It "
            "replenishes at the next UTC day, not at the next request."
        ),
        "BUDGET_PAUSED_UNTIL_REPLENISHMENT": (
            "No lane has a whole object a day at the current balance, so "
            "collection is paused until the monthly balance replenishes."
        ),
    }
    return {
        "lane": lane,
        "objects_requested": objects,
        "fits": grant.allowed,
        "objects_granted": grant.granted,
        "reason": grant.reason,
        "explanation": reasons.get(grant.reason, grant.reason),
        "allocation": allocation.as_dict() if allocation else None,
        "replenishes_on": grant.plan.replenishes_on.isoformat(),
        "reserved_nothing": True,
    }


def coverage_forecast(
    session: Session, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Achievable coverage through replenishment, from the real schedule.

    Counts the games actually scheduled between now and the replenishment date
    and compares them against what the allocation can pay for. The answer is
    usually "not all of them", and saying which fraction -- and by what rule the
    rest are dropped -- is the difference between a budget and a surprise.
    """

    from app.models import Game, League

    moment = now or datetime.now(UTC)
    plan = build_plan(session, now=moment)
    horizon = datetime.combine(plan.replenishes_on, datetime.min.time(), tzinfo=UTC)

    scheduled = dict(session.execute(
        select(League.code, func.count(Game.id))
        .join(Game, Game.league_id == League.id)
        .where(Game.starts_at >= moment, Game.starts_at < horizon)
        .group_by(League.code)
    ).all())

    mlb_games = int(scheduled.get("MLB", 0))
    nfl_games = int(scheduled.get("NFL", 0))
    mlb_daily = plan.allocations["sgo_mlb_events"].daily_allowance
    live_daily = plan.allocations["sgo_nfl_live"].daily_allowance

    mlb_fundable = mlb_daily * plan.days_remaining

    # AISP registers canonical events only a short horizon ahead, so the count
    # above is what is REGISTERED, not what the league will play. Comparing a
    # seventeen-day budget against a two-day schedule would report three times
    # coverage where the real answer is under a half. The per-day comparison is
    # the one that means something, so it is the one reported as coverage.
    horizon_days = max(1, len({
        row[0] for row in session.execute(
            select(func.date(Game.starts_at))
            .join(League, League.id == Game.league_id)
            .where(League.code == "MLB", Game.starts_at >= moment,
                   Game.starts_at < horizon)
        ).all()
    }))
    mlb_per_day = mlb_games / horizon_days if horizon_days else 0.0

    return {
        "through": plan.replenishes_on.isoformat(),
        "days_remaining": plan.days_remaining,
        "daily_total": plan.daily_total,
        "mlb": {
            "games_registered_ahead": mlb_games,
            "registered_days_ahead": horizon_days,
            "typical_games_per_day": round(mlb_per_day, 1),
            "objects_per_day": mlb_daily,
            "objects_fundable_through_replenishment": mlb_fundable,
            # The unit is an EVENT OBJECT, not a request and not a game. One
            # slate request today returned fourteen event objects and was billed
            # fourteen, so counting requests would have read as one.
            "distinct_games_per_day_if_never_recaptured": mlb_daily,
            "distinct_games_per_day_if_each_game_captured_twice": mlb_daily // 2,
            "daily_coverage_fraction": (
                round(mlb_daily / mlb_per_day, 3) if mlb_per_day else None),
            "checkpoints_per_game": 1,
            "note": (
                f"{mlb_daily} objects a day covers {mlb_daily} DISTINCT games "
                "only while no game is captured more than once. The provider "
                "bills per event object, so a second capture of a game already "
                "captured -- a refresh after a staleness expiry, a re-read of a "
                "slate that includes games already priced -- costs another "
                "object and takes a distinct game off the day one for one. The "
                "coverage fraction below assumes the no-repeat case, which is "
                "the most favourable one and not the observed one."
            ),
            "coverage_fraction_assumes": "NO_REPEAT_CAPTURES",
        },
        "nfl_in_game": {
            "games_scheduled": nfl_games,
            "objects_per_day": live_daily,
            # An in-game check is only worth anything WHILE the game is being
            # played, so the honest unit is objects available on the one day the
            # game runs. The previous figure multiplied the daily allowance by
            # the days left in the month and divided by thirteen, which reported
            # "3.9 complete games fundable" from an allowance that cannot fund
            # thirteen checks in any single game. Objects do not roll forward
            # into a game that has already ended.
            "checks_in_one_game_today": live_daily,
            "objects_for_one_complete_game_at_15_minutes": 13,
            "complete_games_fundable_today": live_daily // 13,
            "objects_unspent_through_replenishment": live_daily * plan.days_remaining,
            "note": (
                f"{live_daily} objects a day buys {live_daily} in-game checks "
                "during one game on the day it is played -- not thirteen, and "
                "not a complete game. Fifteen-minute coverage of one full game "
                "needs 13 objects on a single day. Unspent objects from earlier "
                "days do not accumulate into a later game: the month's total is "
                "what limits how many days can be funded, while this per-day "
                "number is what limits any single game. Four concurrent games "
                "is a ceiling on how many may be TRACKED at once, never a claim "
                "the day pays for four. A tracked game is followed until the "
                "day's allocation is spent and then reports its last confirmed "
                "score with its real age."
            ),
        },
        "nfl_pregame": {
            # Named explicitly because a reader who sees 0 next to a 0.08 share
            # will assume rounding, and would then expect the zero to fix itself
            # as the budget grows. It will not: the lane is switched off.
            "objects_per_day": plan.allocations["sgo_nfl_events"].daily_allowance,
            "funded": LANES_BY_KEY["sgo_nfl_events"].funded,
            "objects_per_day_if_funded": apportion(
                plan.daily_total, include_unfunded=True).get("sgo_nfl_events", 0),
            "zero_is": "AN_EXPLICIT_POLICY_CHOICE",
            "note": (
                "NFL pregame receives nothing because the lane is declared "
                "unfunded, not because its share rounded down. Its declared "
                "share is "
                f"{LANES_BY_KEY['sgo_nfl_events'].share:.2f} of the daily total, "
                "and at today's total a funded lane at that share would receive "
                f"{apportion(plan.daily_total, include_unfunded=True).get('sgo_nfl_events', 0)} "
                "objects a day. Turning it on is James's decision and changes "
                "every other lane's allowance, because the shares are of one "
                "fixed total. Nothing here bypasses the reservation guard: an "
                "unfunded lane is refused by name at the enforcement point."
            ),
        },
        "free_and_unaffected": {
            "mlb_scoreboard": (
                "Every two minutes from the free MLB Stats API. No provider "
                "allowance is involved, so it is unchanged by any of this."
            ),
            "nfl_finals": "Hourly from the free nflverse release.",
            "governed_finals": "Free MLB Stats API, one feed per missing game.",
        },
        "plan": plan.as_dict(),
    }


__all__ = [
    "ALLOCATION_CHANGES",
    "FALLBACK_MONTHLY_CAP",
    "LANES",
    "LANES_BY_KEY",
    "LANE_LEAGUE",
    "LANE_LEAGUES",
    "OBJECT_RESERVE",
    "OWNER_DAY_EXCEPTIONS",
    "PROVIDER",
    "USAGE_ENDPOINT_KEY",
    "Allocation",
    "ExceptionGame",
    "Grant",
    "Lane",
    "OwnerDayException",
    "Plan",
    "apportion",
    "build_plan",
    "coverage_forecast",
    "latest_usage_reading",
    "misattributed_rows",
    "owner_exception_for",
    "owner_exception_state",
    "owner_exception_units",
    "replenishment_date",
    "request_objects",
]
