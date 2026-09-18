"""A House Divided -- the virtual competition's rules, in one place.

WHO PLAYS
---------
James (the models), Scotty, and Red Lou (Mom). Each has a separate virtual
$1,000 account in each sport. Balances never move between people or sports,
never top up, and never reset.

WHEN A SPORT STARTS
-------------------
A sport starts at a recorded instant, and only a sport whose official model
pipeline actually issues decisions can start (MLB and NFL today). Nothing
issued before that instant is ever imported; there is no backfill. A sport that
has not started shows "Not started" with every $1,000 intact.

JAMES'S WAGERS ARE THE MODEL'S
------------------------------
Once a game's final-pregame cutoff has passed, the official experiment's
decision for that game is settled fact: the latest FINAL_PREGAME decision
recorded at or before the cutoff -- the same rule the official $1,000
Experiment uses. If it is QUALIFIED_BET with a complete quote, it is copied
once (the decision id is unique on the wager) with its market, selection, line,
book, price, quote and timestamps, and sized by that experiment's own staking
policy against James's competition bankroll. A pass stays a pass. A decision
the account cannot fund is recorded as UNFUNDED with the reason, not dropped.
The official decision, its position and its ledger are only read.

SCOTTY AND RED LOU CHOOSE THEIR OWN
-----------------------------------
A pick names a stored quote. The server re-reads that quote and copies its
terms; the browser never supplies a price or a line. A draft may be edited.
Submitting locks the terms and commits the stake, and is refused unless: the
quote is still the newest for its book and selection and inside the existing
240-minute freshness rule; the game is before the existing pregame cutoff
(start - 15 minutes); the stake is positive and within available funds. The
participant's own linked login, or an administrator on their behalf, may enter
it; both identities are kept.

SETTLEMENT
----------
Exactly as the official $1,000 Experiment settles: a FINAL game with scores
settles through the existing market rules (spread and total pushes, the
two-way moneyline tie refunds as a PUSH); a CANCELLED game voids the stake;
anything else stays open. The status guard makes each wager settle once.

ACCOUNTING
----------
bankroll  = starting balance + settled net profit
available = bankroll - open stakes
A placed wager moves `available`, never `bankroll`, so the bankroll chart only
moves when something settles.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import Session

from app.bankroll.service import SettlementResult, settle_profit
from app.bankroll.settlement import _outcome_line, determine_result
from app.models import (
    CompetitionAccount, CompetitionParticipant, CompetitionWager, ExperimentDecisionPhase, Game,
    GameStatus, GameTeam, League, Market, MarketOutcome, MarketType, OddsSnapshot, OddsStatus,
    PositionResult, ShadowExperiment, ShadowExperimentDecision, Sportsbook, Team, User, UserRole,
)
from app.models.house_divided import (
    SOURCE_HUMAN, SOURCE_MODEL, WAGER_DISCARDED, WAGER_DRAFT, WAGER_OPEN, WAGER_SETTLED,
    WAGER_UNFUNDED,
)

SPORTS: tuple[str, ...] = ("MLB", "NFL", "NBA", "CFB")
#: The existing pregame cutoff (FINAL_PREGAME_CUTOFF_MINUTES).
CUTOFF = timedelta(minutes=15)
#: The existing quote-freshness rule (MAX_QUOTE_AGE_MINUTES).
MAX_QUOTE_AGE = timedelta(minutes=240)
STARTING_BALANCE = Decimal("1000.00")
ET = ZoneInfo("America/New_York")
CENT = Decimal("0.01")

MARKET_NAMES = {MarketType.MONEYLINE: "MONEYLINE", MarketType.SPREAD: "SPREAD", MarketType.TOTAL: "TOTAL"}
SPREAD_LABEL = {"MLB": "Run line", "NFL": "Spread", "NBA": "Spread", "CFB": "Spread"}

MLB_POLICY = "AISP_PRACTICAL_PREGAME_POLICY_V2_1"
NFL_POLICY = "AISP_NFL_PAPER_POLICY_V1"

NOT_ELIGIBLE_REASON = {
    "NBA": "No official NBA model decision pipeline is active; NBA stays staged.",
    "CFB": "College football forecasts are research-only; no official decision pipeline.",
}


class CompetitionError(Exception):
    """A refused request. `code` is stable; the message is for a person."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat() if value else None


# ---------------------------------------------------------------------------
# Which pipeline is official for a sport
# ---------------------------------------------------------------------------
def official_experiment(session: Session, sport: str, *, now: datetime | None = None) -> ShadowExperiment | None:
    """The experiment whose FINAL_PREGAME decisions are official for `sport`.

    MLB: the practical-pregame policy era that has not itself been succeeded
    by an active, begun era (today the V18 conditional era). NFL: the active
    NFL paper policy. NBA and CFB have no official decision pipeline.
    """

    moment = now or datetime.now(UTC)
    if sport == "NFL":
        return session.scalar(select(ShadowExperiment).where(
            ShadowExperiment.sport == "NFL",
            ShadowExperiment.decision_policy == NFL_POLICY,
            ShadowExperiment.is_active.is_(True),
        ).order_by(ShadowExperiment.started_at.desc()).limit(1))
    if sport != "MLB":
        return None
    candidates = list(session.scalars(select(ShadowExperiment).where(
        ShadowExperiment.sport == "MLB",
        ShadowExperiment.decision_policy == MLB_POLICY,
        ShadowExperiment.is_active.is_(True),
        ShadowExperiment.started_at <= moment,
    ).order_by(ShadowExperiment.started_at.desc())))
    for experiment in candidates:
        succeeded = session.scalar(select(ShadowExperiment.id).where(
            ShadowExperiment.predecessor_experiment_id == experiment.id,
            ShadowExperiment.is_active.is_(True),
            ShadowExperiment.started_at <= moment,
        ).limit(1))
        if succeeded is None:
            return experiment
    return None


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
@dataclass
class Balance:
    starting: Decimal
    settled_profit: Decimal
    open_stake: Decimal

    @property
    def bankroll(self) -> Decimal:
        return self.starting + self.settled_profit

    @property
    def available(self) -> Decimal:
        return self.bankroll - self.open_stake


PARTICIPANTS = (
    ("JAMES", "James — The Models", "#2f7de1", True, 0),
    ("SCOTTY", "Scotty", "#e0772f", False, 1),
    ("RED_LOU", "Red Lou — Mom", "#c9304a", False, 2),
)


def ensure_competition(session: Session) -> None:
    """Create any missing participant or Not-started account. Changes nothing that exists."""

    for key, name, color, is_model, order in PARTICIPANTS:
        participant = session.scalar(select(CompetitionParticipant).where(CompetitionParticipant.key == key))
        if participant is None:
            participant = CompetitionParticipant(key=key, display_name=name, color=color,
                                                 is_model=is_model, sort_order=order)
            session.add(participant)
            session.flush()
        for sport in SPORTS:
            exists = session.scalar(select(CompetitionAccount.id).where(
                CompetitionAccount.participant_id == participant.id, CompetitionAccount.sport == sport))
            if exists is None:
                session.add(CompetitionAccount(participant_id=participant.id, sport=sport,
                                               starting_balance=STARTING_BALANCE))
    session.flush()


def participants(session: Session) -> list[CompetitionParticipant]:
    return list(session.scalars(select(CompetitionParticipant).order_by(CompetitionParticipant.sort_order)))


def participant_by_key(session: Session, key: str) -> CompetitionParticipant:
    row = session.scalar(select(CompetitionParticipant).where(CompetitionParticipant.key == key.upper()))
    if row is None:
        raise CompetitionError("UNKNOWN_PARTICIPANT", f"No participant {key!r}.", 404)
    return row


def account_for(session: Session, participant: CompetitionParticipant, sport: str, *,
                lock: bool = False) -> CompetitionAccount:
    query = select(CompetitionAccount).where(
        CompetitionAccount.participant_id == participant.id, CompetitionAccount.sport == sport)
    if lock:
        query = query.with_for_update()
    account = session.scalar(query)
    if account is None:
        raise CompetitionError("NO_ACCOUNT", f"{participant.display_name} has no {sport} account.", 404)
    return account


def balance(session: Session, account: CompetitionAccount, *, as_of: datetime | None = None) -> Balance:
    """The account's position, optionally as it stood at `as_of`.

    Only submitted wagers count: a draft commits nothing, and an UNFUNDED
    record never held money.
    """

    starting = _money(account.starting_balance)
    wagers = session.scalars(select(CompetitionWager).where(
        CompetitionWager.account_id == account.id,
        CompetitionWager.status.in_((WAGER_OPEN, WAGER_SETTLED)),
    ))
    settled = Decimal("0")
    open_stake = Decimal("0")
    for wager in wagers:
        submitted = _aware(wager.submitted_at)
        settled_at = _aware(wager.settled_at)
        if as_of is not None and submitted is not None and submitted > as_of:
            continue
        if wager.status == WAGER_SETTLED and (as_of is None or (settled_at and settled_at <= as_of)):
            settled += _money(wager.profit_loss or 0)
        else:
            open_stake += _money(wager.stake)
    return Balance(starting=starting, settled_profit=settled, open_stake=open_stake)


def start_sport(session: Session, sport: str, *, actor: User | None, now: datetime | None = None) -> dict[str, Any]:
    """Record the prospective start for one sport. Never moves an existing start."""

    sport = sport.upper()
    if sport not in SPORTS:
        raise CompetitionError("UNKNOWN_SPORT", f"{sport} is not a competition sport.", 404)
    moment = now or datetime.now(UTC)
    if official_experiment(session, sport, now=moment) is None:
        raise CompetitionError(
            "NO_ELIGIBLE_MODEL_PIPELINE",
            NOT_ELIGIBLE_REASON.get(sport, f"{sport} has no official model decision pipeline."))
    accounts = list(session.scalars(select(CompetitionAccount).where(
        CompetitionAccount.sport == sport).with_for_update()))
    if any(account.started_at is not None for account in accounts):
        raise CompetitionError("ALREADY_STARTED", f"{sport} already started; a start is never moved.")
    for account in accounts:
        account.started_at = moment
        account.started_by_user_id = actor.id if actor else None
    session.flush()
    return {"sport": sport, "started_at": moment.isoformat(), "accounts": len(accounts)}


def _started_at(session: Session, sport: str) -> datetime | None:
    return _aware(session.scalar(select(func.min(CompetitionAccount.started_at)).where(
        CompetitionAccount.sport == sport)))


# ---------------------------------------------------------------------------
# James: mirror the official decisions
# ---------------------------------------------------------------------------
def _cutoff(game: Game) -> datetime:
    return _aware(game.starts_at) - CUTOFF


def _official_final_decisions(session: Session, experiment: ShadowExperiment, game_ids: list[Any]
                              ) -> dict[Any, ShadowExperimentDecision]:
    """Per game: the latest FINAL_PREGAME decision at or before its cutoff."""

    if not game_ids:
        return {}
    rows = session.execute(
        select(ShadowExperimentDecision, Game.starts_at)
        .join(Game, Game.id == ShadowExperimentDecision.game_id)
        .where(ShadowExperimentDecision.experiment_id == experiment.id,
               ShadowExperimentDecision.game_id.in_(game_ids),
               ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME)
        .order_by(ShadowExperimentDecision.recommended_at)
    ).all()
    out: dict[Any, ShadowExperimentDecision] = {}
    for decision, starts_at in rows:
        if _aware(decision.recommended_at) <= _aware(starts_at) - CUTOFF:
            out[decision.game_id] = decision
    return out


def _complete(decision: ShadowExperimentDecision, cutoff: datetime) -> str | None:
    """Why a qualified decision cannot be copied as a wager, or None."""

    if decision.market_outcome_id is None or decision.odds_snapshot_id is None:
        return "decision names no stored quote"
    if decision.decimal_odds is None or float(decision.decimal_odds) <= 1:
        return "decision has no usable price"
    if decision.quote_observed_at is None or _aware(decision.quote_observed_at) > cutoff:
        return "decision quote is not from before the cutoff"
    return None


def _policy_stake(session: Session, experiment: ShadowExperiment, decision: ShadowExperimentDecision,
                  account: CompetitionAccount, game: Game, at: datetime) -> tuple[Decimal, dict[str, Any]]:
    """The experiment's own staking policy, applied to James's competition account."""

    state = balance(session, account, as_of=at)
    bankroll = float(state.bankroll)
    available = float(state.available)
    james_wagers = select(CompetitionWager).where(
        CompetitionWager.account_id == account.id,
        CompetitionWager.status.in_((WAGER_OPEN, WAGER_SETTLED)),
        CompetitionWager.submitted_at <= at,
    ).subquery()
    same_game = float(session.scalar(select(func.coalesce(func.sum(james_wagers.c.stake), 0)).where(
        james_wagers.c.game_id == game.id)) or 0)
    same_day = float(session.scalar(
        select(func.coalesce(func.sum(james_wagers.c.stake), 0))
        .join(ShadowExperimentDecision, ShadowExperimentDecision.id == james_wagers.c.source_decision_id)
        .where(ShadowExperimentDecision.slate_date == decision.slate_date)) or 0)

    if experiment.sport == "NFL":
        # nfl_paper_decision: frozen quarter-Kelly fraction x the policy's
        # fixed bankroll basis (the account's starting balance), capped.
        evidence = decision.evidence or {}
        selection = evidence.get("selection") or evidence.get("best") or {}
        basis = float(account.starting_balance)
        fraction = selection.get("quarter_kelly")
        limits = {
            "quarter_kelly": (float(fraction) * basis) if fraction is not None else 0.0,
            "position_cap": experiment.max_position_percentage * basis,
            "same_game_cap": max(0.0, experiment.max_same_game_percentage * basis - same_game),
            "daily_cap": max(0.0, experiment.max_daily_percentage * basis - same_day),
        }
        policy = experiment.staking_policy_version
    else:
        from app.services.shadow_experiment_service import ShadowExperimentService

        raw = ShadowExperimentService.compute_position_stake(experiment, decision, bankroll)
        basis = bankroll
        limits = {
            "policy_stake": raw or 0.0,
            "same_game_cap": max(0.0, bankroll * experiment.max_same_game_percentage - same_game),
            "daily_cap": max(0.0, bankroll * experiment.max_daily_percentage - same_day),
        }
        policy = experiment.staking_policy_version
    policy_stake = min(limits.values())
    stake = _money(max(0.0, min(policy_stake, available)))
    bound = min(limits, key=limits.get)
    if available <= 0 or available < policy_stake:
        bound = "available_funds"
    return stake, {
        "staking_policy": policy, "basis": round(basis, 2), "bankroll_at_decision": round(bankroll, 2),
        "available_at_decision": round(available, 2), "limits": {k: round(v, 2) for k, v in limits.items()},
        "policy_stake": round(policy_stake, 2), "bound_by": bound,
        "slate_date": decision.slate_date.isoformat(),
    }


def mirror_model_decisions(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Copy each eligible official decision into James's account, once."""

    moment = now or datetime.now(UTC)
    james = session.scalar(select(CompetitionParticipant).where(CompetitionParticipant.is_model.is_(True)))
    report: dict[str, Any] = {"imported": 0, "unfunded": 0, "sports": {}}
    if james is None:
        return {**report, "note": "no model participant"}
    for sport in SPORTS:
        account = session.scalar(select(CompetitionAccount).where(
            CompetitionAccount.participant_id == james.id, CompetitionAccount.sport == sport))
        if account is None or account.started_at is None:
            continue
        experiment = official_experiment(session, sport, now=moment)
        if experiment is None:
            report["sports"][sport] = "NO_OFFICIAL_PIPELINE"
            continue
        started = _aware(account.started_at)
        games = list(session.scalars(
            select(Game).join(League, League.id == Game.league_id)
            .where(League.code == sport, Game.starts_at >= started + CUTOFF,
                   Game.starts_at <= moment + CUTOFF)))
        finals = _official_final_decisions(session, experiment, [game.id for game in games])
        by_id = {game.id: game for game in games}
        due = sorted(
            (d for d in finals.values()
             if d.recommendation == "QUALIFIED_BET" and _aware(d.recommended_at) >= started),
            key=lambda d: _aware(d.recommended_at))
        imported = unfunded = 0
        for decision in due:
            if session.scalar(select(CompetitionWager.id).where(
                    CompetitionWager.source_decision_id == decision.id)):
                continue
            game = by_id[decision.game_id]
            wager = _mirror_one(session, james, account, experiment, decision, game)
            session.flush()
            if wager.status == WAGER_UNFUNDED:
                unfunded += 1
            else:
                imported += 1
        report["sports"][sport] = {"experiment_id": str(experiment.id), "imported": imported,
                                   "unfunded": unfunded, "qualified_final_decisions": len(due)}
        report["imported"] += imported
        report["unfunded"] += unfunded
    return report


def _mirror_one(session: Session, james: CompetitionParticipant, account: CompetitionAccount,
                experiment: ShadowExperiment, decision: ShadowExperimentDecision, game: Game) -> CompetitionWager:
    at = _aware(decision.recommended_at)
    outcome = session.get(MarketOutcome, decision.market_outcome_id) if decision.market_outcome_id else None
    market = session.get(Market, outcome.market_id) if outcome else None
    snapshot = session.get(OddsSnapshot, decision.odds_snapshot_id) if decision.odds_snapshot_id else None
    book = session.get(Sportsbook, snapshot.sportsbook_id) if snapshot else None
    line = decision.line
    if line is None and outcome is not None and market is not None and market.market_type != MarketType.MONEYLINE:
        line = _outcome_line(outcome)
    wager = CompetitionWager(
        account_id=account.id, participant_id=james.id, sport=account.sport,
        source=SOURCE_MODEL, status=WAGER_OPEN, game_id=game.id,
        market_type=MARKET_NAMES.get(market.market_type) if market else decision.market,
        market_outcome_id=decision.market_outcome_id,
        selection_label=(outcome.label if outcome else decision.selection),
        line=line, sportsbook_id=snapshot.sportsbook_id if snapshot else None,
        sportsbook_key=(book.key if book else decision.sportsbook),
        odds_snapshot_id=decision.odds_snapshot_id, american_odds=decision.american_odds,
        decimal_odds=_f(decision.decimal_odds), quote_observed_at=decision.quote_observed_at,
        quote_available_as_of=snapshot.available_as_of if snapshot else None,
        stake=Decimal("0"), submitted_at=at, source_decision_id=decision.id,
        source_experiment_id=experiment.id,
        model_version=(decision.forecast_version or experiment.model_era_id
                       or experiment.recommendation_policy_version),
        decision_recommended_at=at,
    )
    problem = _complete(decision, _cutoff(game))
    if problem is not None or outcome is None or market is None:
        wager.status = WAGER_UNFUNDED
        wager.unfunded_reason = f"Not copyable: {problem or 'market outcome not found'}"
        session.add(wager)
        return wager
    stake, detail = _policy_stake(session, experiment, decision, account, game, at)
    wager.staking_detail = detail
    if stake <= 0:
        wager.status = WAGER_UNFUNDED
        wager.unfunded_reason = (
            "Insufficient available funds" if detail["bound_by"] == "available_funds"
            else f"Staking policy resolved to $0 (bound by {detail['bound_by']})")
    else:
        wager.stake = stake
    session.add(wager)
    return wager


# ---------------------------------------------------------------------------
# Scotty and Red Lou: drafts and submission
# ---------------------------------------------------------------------------
def _authorize(participant: CompetitionParticipant, actor: User) -> None:
    if participant.is_model:
        raise CompetitionError("MODEL_ACCOUNT",
                               "James's wagers are the models' official decisions; nobody enters them.", 403)
    if actor.role == UserRole.ADMIN:
        return
    if participant.user_id is not None and participant.user_id == actor.id:
        return
    raise CompetitionError("NOT_AUTHORIZED",
                           f"Only {participant.display_name} or an administrator may enter this pick.", 403)


def _game_sport(session: Session, game: Game) -> str:
    return session.scalar(select(League.code).where(League.id == game.league_id)) or ""


def _quote_terms(session: Session, snapshot_id: Any, *, now: datetime) -> dict[str, Any]:
    """Every term of a pick, read from the stored quote itself."""

    snapshot = session.get(OddsSnapshot, snapshot_id)
    if snapshot is None:
        raise CompetitionError("UNKNOWN_QUOTE", "That quote does not exist.", 404)
    outcome = session.get(MarketOutcome, snapshot.market_outcome_id)
    market = session.get(Market, outcome.market_id) if outcome else None
    if outcome is None or market is None:
        raise CompetitionError("UNKNOWN_QUOTE", "That quote has no market.", 404)
    if market.market_type not in MARKET_NAMES or market.period != "full_game":
        raise CompetitionError("UNSUPPORTED_MARKET",
                               "Only full-game moneyline, spread/run-line and total wagers are supported.")
    game = session.get(Game, market.game_id)
    line = None
    if market.market_type != MarketType.MONEYLINE:
        line = _outcome_line(outcome)
        if line is None or (snapshot.point is not None and float(snapshot.point) != line):
            raise CompetitionError("UNSUPPORTED_MARKET", "That quote's line cannot be settled.")
    book = session.get(Sportsbook, snapshot.sportsbook_id)
    return {"snapshot": snapshot, "outcome": outcome, "market": market, "game": game, "line": line,
            "book": book}


def _current_problem(session: Session, terms: dict[str, Any], *, now: datetime) -> str | None:
    """Why this quote may not be wagered on right now, or None."""

    snapshot: OddsSnapshot = terms["snapshot"]
    if snapshot.status != OddsStatus.OPEN:
        return "That price is not open."
    if (snapshot.source_mode or "").upper() == "DEMO":
        return "Demonstration prices are never wagerable."
    if now - _aware(snapshot.observed_at) > MAX_QUOTE_AGE:
        return "That price is older than 240 minutes; choose a current one."
    newer = session.scalar(select(OddsSnapshot.id).where(
        OddsSnapshot.market_outcome_id == snapshot.market_outcome_id,
        OddsSnapshot.sportsbook_id == snapshot.sportsbook_id,
        OddsSnapshot.observed_at > snapshot.observed_at,
        OddsSnapshot.observed_at <= now,
    ).limit(1))
    if newer is not None:
        return "That book has posted a newer price; re-price the pick."
    return None


def _check_game_open(session: Session, game: Game, account: CompetitionAccount, *, now: datetime) -> None:
    if account.started_at is None:
        raise CompetitionError("NOT_STARTED", f"{account.sport} has not started.")
    if _aware(game.starts_at) < _aware(account.started_at) + CUTOFF:
        raise CompetitionError("BEFORE_START", "That game predates the competition start.")
    if game.status != GameStatus.SCHEDULED:
        raise CompetitionError("GAME_NOT_OPEN", "That game is no longer scheduled.")
    if now >= _cutoff(game):
        raise CompetitionError("PAST_CUTOFF",
                               "Picks lock 15 minutes before the start; this game is past its cutoff.")


def _parse_stake(stake: Any) -> Decimal:
    try:
        value = Decimal(str(stake))
    except Exception as exc:  # noqa: BLE001 -- any unparsable input is the same refusal
        raise CompetitionError("BAD_STAKE", "Stake must be a number.", 422) from exc
    if not value.is_finite() or value <= 0:
        raise CompetitionError("BAD_STAKE", "Stake must be greater than $0.", 422)
    if value != value.quantize(CENT):
        raise CompetitionError("BAD_STAKE", "Stake is whole cents.", 422)
    return value


def save_draft(session: Session, *, participant_key: str, odds_snapshot_id: Any, stake: Any, actor: User,
               wager_id: Any = None, now: datetime | None = None) -> CompetitionWager:
    """Create or edit a draft. Drafts commit no money and may change freely."""

    moment = now or datetime.now(UTC)
    participant = participant_by_key(session, participant_key)
    _authorize(participant, actor)
    terms = _quote_terms(session, odds_snapshot_id, now=moment)
    game: Game = terms["game"]
    sport = _game_sport(session, game)
    account = account_for(session, participant, sport)
    _check_game_open(session, game, account, now=moment)
    amount = _parse_stake(stake)
    if wager_id is not None:
        wager = session.get(CompetitionWager, wager_id)
        if wager is None or wager.participant_id != participant.id:
            raise CompetitionError("UNKNOWN_WAGER", "No such draft.", 404)
        if wager.status != WAGER_DRAFT:
            raise CompetitionError("LOCKED", "A submitted wager's terms are locked.")
    else:
        wager = CompetitionWager(account_id=account.id, participant_id=participant.id, sport=sport,
                                 source=SOURCE_HUMAN, status=WAGER_DRAFT)
        session.add(wager)
    _apply_terms(wager, terms, account)
    wager.stake = amount
    wager.entered_by_user_id = actor.id
    wager.updated_at = moment
    session.flush()
    return wager


def _apply_terms(wager: CompetitionWager, terms: dict[str, Any], account: CompetitionAccount) -> None:
    snapshot: OddsSnapshot = terms["snapshot"]
    wager.account_id = account.id
    wager.sport = account.sport
    wager.game_id = terms["game"].id
    wager.market_type = MARKET_NAMES[terms["market"].market_type]
    wager.market_outcome_id = terms["outcome"].id
    wager.selection_label = terms["outcome"].label
    wager.line = terms["line"]
    wager.sportsbook_id = snapshot.sportsbook_id
    wager.sportsbook_key = terms["book"].key if terms["book"] else None
    wager.odds_snapshot_id = snapshot.id
    wager.american_odds = snapshot.american_odds
    wager.decimal_odds = float(snapshot.decimal_odds)
    wager.quote_observed_at = snapshot.observed_at
    wager.quote_available_as_of = snapshot.available_as_of


def submit(session: Session, *, wager_id: Any, actor: User, now: datetime | None = None) -> CompetitionWager:
    """Lock a draft's terms and commit its stake -- or refuse, changing nothing."""

    moment = now or datetime.now(UTC)
    wager = session.scalar(select(CompetitionWager).where(CompetitionWager.id == wager_id).with_for_update())
    if wager is None:
        raise CompetitionError("UNKNOWN_WAGER", "No such draft.", 404)
    participant = session.get(CompetitionParticipant, wager.participant_id)
    _authorize(participant, actor)
    if wager.status != WAGER_DRAFT:
        raise CompetitionError("LOCKED", "Already submitted; its terms are locked.")
    # Serialise every commitment against this account.
    account = session.scalar(select(CompetitionAccount).where(
        CompetitionAccount.id == wager.account_id).with_for_update())
    terms = _quote_terms(session, wager.odds_snapshot_id, now=moment)
    _check_game_open(session, terms["game"], account, now=moment)
    problem = _current_problem(session, terms, now=moment)
    if problem is not None:
        raise CompetitionError("STALE_PRICE", problem)
    stake = _money(wager.stake)
    available = balance(session, account).available
    if stake > available:
        raise CompetitionError("INSUFFICIENT_FUNDS",
                               f"Stake ${stake} exceeds available funds ${available}.")
    _apply_terms(wager, terms, account)
    wager.status = WAGER_OPEN
    wager.submitted_at = moment
    wager.entered_by_user_id = actor.id
    wager.updated_at = moment
    session.flush()
    return wager


def discard(session: Session, *, wager_id: Any, actor: User, now: datetime | None = None) -> CompetitionWager:
    wager = session.scalar(select(CompetitionWager).where(CompetitionWager.id == wager_id).with_for_update())
    if wager is None:
        raise CompetitionError("UNKNOWN_WAGER", "No such draft.", 404)
    _authorize(session.get(CompetitionParticipant, wager.participant_id), actor)
    if wager.status != WAGER_DRAFT:
        raise CompetitionError("LOCKED", "Only a draft can be withdrawn.")
    wager.status = WAGER_DISCARDED
    wager.updated_at = now or datetime.now(UTC)
    session.flush()
    return wager


def current_quotes(session: Session, game_id: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """The wagerable prices for a game right now: newest per book and selection, fresh."""

    moment = now or datetime.now(UTC)
    game = session.get(Game, game_id)
    if game is None:
        raise CompetitionError("UNKNOWN_GAME", "No such game.", 404)
    newest = (
        select(OddsSnapshot.market_outcome_id, OddsSnapshot.sportsbook_id,
               func.max(OddsSnapshot.observed_at).label("observed_at"))
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .where(Market.game_id == game.id, OddsSnapshot.observed_at <= moment)
        .group_by(OddsSnapshot.market_outcome_id, OddsSnapshot.sportsbook_id)
    ).subquery()
    rows = session.execute(
        select(OddsSnapshot, MarketOutcome, Market, Sportsbook)
        .join(newest, and_(newest.c.market_outcome_id == OddsSnapshot.market_outcome_id,
                           newest.c.sportsbook_id == OddsSnapshot.sportsbook_id,
                           newest.c.observed_at == OddsSnapshot.observed_at))
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
    ).all()
    sport = _game_sport(session, game)
    quotes = []
    for snapshot, outcome, market, book in rows:
        terms = {"snapshot": snapshot, "outcome": outcome, "market": market, "game": game, "book": book,
                 "line": None if market.market_type == MarketType.MONEYLINE else _outcome_line(outcome)}
        if market.market_type not in MARKET_NAMES or market.period != "full_game":
            continue
        if market.market_type != MarketType.MONEYLINE and (
                terms["line"] is None or (snapshot.point is not None and float(snapshot.point) != terms["line"])):
            continue
        if _current_problem(session, terms, now=moment) is not None:
            continue
        quotes.append({
            "quote_id": str(snapshot.id), "market_type": MARKET_NAMES[market.market_type],
            "market_label": _market_label(sport, MARKET_NAMES[market.market_type]),
            "selection": outcome.label, "line": terms["line"], "sportsbook": book.key,
            "sportsbook_name": book.name, "american_odds": snapshot.american_odds,
            "decimal_odds": float(snapshot.decimal_odds), "observed_at": _iso(snapshot.observed_at),
            "age_minutes": int((moment - _aware(snapshot.observed_at)).total_seconds() // 60),
        })
    order = {"MONEYLINE": 0, "SPREAD": 1, "TOTAL": 2}
    quotes.sort(key=lambda q: (order[q["market_type"]], q["selection"], q["line"] or 0, q["sportsbook"]))
    return {"game": _game_card(session, game, moment), "quotes": quotes,
            "cutoff": _cutoff(game).isoformat(), "max_quote_age_minutes": 240}


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------
def settle_open(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Settle each open wager whose game has a final (or was cancelled). Once."""

    moment = now or datetime.now(UTC)
    rows = session.execute(
        select(CompetitionWager, Game)
        .join(Game, Game.id == CompetitionWager.game_id)
        .where(CompetitionWager.status == WAGER_OPEN,
               or_(Game.status == GameStatus.CANCELED,
                   and_(Game.status == GameStatus.FINAL, Game.home_score.is_not(None),
                        Game.away_score.is_not(None))))
        .order_by(Game.starts_at, CompetitionWager.submitted_at)
        .with_for_update(of=CompetitionWager)
    ).all()
    settled = {"WIN": 0, "LOSS": 0, "PUSH": 0, "VOID": 0}
    for wager, game in rows:
        if wager.status != WAGER_OPEN:
            continue
        cancelled = game.status == GameStatus.CANCELED
        outcome = session.get(MarketOutcome, wager.market_outcome_id) if wager.market_outcome_id else None
        market = session.get(Market, outcome.market_id) if outcome else None
        if cancelled or outcome is None or market is None:
            result = PositionResult.VOID
        else:
            result = determine_result(session, game, market, outcome)
        profit = _money(settle_profit(float(wager.stake), float(wager.decimal_odds),
                                      SettlementResult(result.value)))
        wager.result = result.value.upper()
        wager.profit_loss = profit
        wager.payout = _money(wager.stake) + profit
        wager.settled_at = moment
        wager.game_home_score = game.home_score
        wager.game_away_score = game.away_score
        wager.settlement_note = ("Game cancelled; stake refunded" if cancelled
                                 else "Settled from the canonical final under the existing market rules")
        wager.status = WAGER_SETTLED
        settled[wager.result] += 1
    session.flush()
    return {"settled": sum(settled.values()), **{k.lower(): v for k, v in settled.items()}}


def run(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """The finalize-games step: settle what has finished, then copy new decisions."""

    moment = now or datetime.now(UTC)
    settled = settle_open(session, now=moment)
    mirrored = mirror_model_decisions(session, now=moment)
    return {"records": settled["settled"] + mirrored["imported"] + mirrored["unfunded"],
            "settlement": settled, "mirror": mirrored}


# ---------------------------------------------------------------------------
# Read model
# ---------------------------------------------------------------------------
def _market_label(sport: str, market_type: str | None) -> str:
    if market_type == "SPREAD":
        return SPREAD_LABEL.get(sport, "Spread")
    return {"MONEYLINE": "Moneyline", "TOTAL": "Total"}.get(market_type or "", market_type or "")


def _teams(session: Session, game_ids: list[Any]) -> dict[Any, dict[str, Any]]:
    out: dict[Any, dict[str, Any]] = {}
    if not game_ids:
        return out
    for side, team in session.execute(
            select(GameTeam, Team).join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id.in_(game_ids))):
        entry = out.setdefault(side.game_id, {})
        entry["home" if side.is_home else "away"] = {"name": team.name, "abbreviation": team.abbreviation}
    return out


def _game_card(session: Session, game: Game, now: datetime, teams: dict[str, Any] | None = None) -> dict[str, Any]:
    teams = teams if teams is not None else _teams(session, [game.id]).get(game.id, {})
    status = game.status.value if hasattr(game.status, "value") else str(game.status)
    return {"game_id": str(game.id), "starts_at": _iso(game.starts_at), "cutoff": _cutoff(game).isoformat(),
            "status": status.upper(), "locked": now >= _cutoff(game) or game.status != GameStatus.SCHEDULED,
            "home": teams.get("home"), "away": teams.get("away"),
            "home_score": game.home_score, "away_score": game.away_score}


def _wager_view(wager: CompetitionWager, participant: CompetitionParticipant, entered_by: dict[Any, str]) -> dict[str, Any]:
    return {
        "wager_id": str(wager.id), "participant": participant.key, "sport": wager.sport,
        "source": wager.source, "status": wager.status, "game_id": str(wager.game_id),
        "market_type": wager.market_type, "market_label": _market_label(wager.sport, wager.market_type),
        "selection": wager.selection_label, "line": wager.line, "sportsbook": wager.sportsbook_key,
        "american_odds": wager.american_odds, "decimal_odds": wager.decimal_odds,
        "quote_id": str(wager.odds_snapshot_id) if wager.odds_snapshot_id else None,
        "quote_observed_at": _iso(wager.quote_observed_at), "stake": _f(wager.stake),
        "submitted_at": _iso(wager.submitted_at), "created_at": _iso(wager.created_at),
        "entered_by": entered_by.get(wager.entered_by_user_id) if wager.entered_by_user_id else None,
        "entered_on_behalf": bool(wager.entered_by_user_id and participant.user_id != wager.entered_by_user_id),
        "source_decision_id": str(wager.source_decision_id) if wager.source_decision_id else None,
        "model_version": wager.model_version, "decision_recommended_at": _iso(wager.decision_recommended_at),
        "staking_detail": wager.staking_detail, "unfunded_reason": wager.unfunded_reason,
        "result": wager.result, "profit_loss": _f(wager.profit_loss), "payout": _f(wager.payout),
        "settled_at": _iso(wager.settled_at), "settlement_note": wager.settlement_note,
        "final_score": ({"home": wager.game_home_score, "away": wager.game_away_score}
                        if wager.game_home_score is not None else None),
    }


def _opposition(a: dict[str, Any], b: dict[str, Any], teams: dict[str, Any]) -> str:
    """How two wagers on the same game relate. Different markets are different bets."""

    if a["market_type"] != b["market_type"]:
        return "DIFFERENT_BETS"
    same_side = a["selection"] == b["selection"]
    if a["market_type"] == "MONEYLINE":
        return "SAME_SIDE" if same_side else "DIRECTLY_OPPOSED"
    if a["line"] is None or b["line"] is None:
        return "DIFFERENT_BETS"
    if a["market_type"] == "TOTAL":
        if a["line"] != b["line"]:
            return "DIFFERENT_LINES"
        return "SAME_SIDE" if same_side else "DIRECTLY_OPPOSED"
    # Spread: opposite teams at mirrored lines are the two sides of one bet.
    if same_side:
        return "SAME_SIDE" if a["line"] == b["line"] else "DIFFERENT_LINES"
    return "DIRECTLY_OPPOSED" if a["line"] == -b["line"] else "DIFFERENT_LINES"


def _account_view(session: Session, participant: CompetitionParticipant, account: CompetitionAccount,
                  wagers: list[CompetitionWager]) -> dict[str, Any]:
    state = balance(session, account)
    settled = [w for w in wagers if w.status == WAGER_SETTLED]
    open_ = [w for w in wagers if w.status == WAGER_OPEN]
    risked = sum((_money(w.stake) for w in settled if w.result != "VOID"), Decimal("0"))
    record = {k: sum(1 for w in settled if w.result == k) for k in ("WIN", "LOSS", "PUSH", "VOID")}
    return {
        "participant": participant.key, "display_name": participant.display_name, "color": participant.color,
        "is_model": participant.is_model, "sport": account.sport,
        "started_at": _iso(account.started_at), "status": "STARTED" if account.started_at else "NOT_STARTED",
        "starting_balance": _f(state.starting), "bankroll": _f(state.bankroll),
        "settled_profit": _f(state.settled_profit), "open_exposure": _f(state.open_stake),
        "available": _f(state.available),
        "roi": (float(state.settled_profit / risked) if risked > 0 else None),
        "settled_stake": _f(risked), "record": record, "settled_count": len(settled), "open_count": len(open_),
        "unfunded_count": sum(1 for w in wagers if w.status == WAGER_UNFUNDED),
        "volume": _f(sum((_money(w.stake) for w in wagers if w.status in (WAGER_OPEN, WAGER_SETTLED)),
                         Decimal("0"))),
        "wager_count": len(settled) + len(open_),
    }


def summary(session: Session, sport: str, *, now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    sport = sport.upper()
    people = participants(session)
    if sport == "ALL":
        return _all_sports(session, people, moment)
    if sport not in SPORTS:
        raise CompetitionError("UNKNOWN_SPORT", f"{sport} is not a competition sport.", 404)

    experiment = official_experiment(session, sport, now=moment)
    started = _started_at(session, sport)
    users = {row.id: row.display_name for row in session.scalars(select(User))}
    wagers = list(session.scalars(select(CompetitionWager).where(
        CompetitionWager.sport == sport,
        CompetitionWager.status.in_((WAGER_OPEN, WAGER_SETTLED, WAGER_UNFUNDED)),
    ).order_by(CompetitionWager.submitted_at)))
    by_person = {p.id: p for p in people}

    cards = []
    for participant in people:
        account = account_for(session, participant, sport)
        mine = [w for w in wagers if w.participant_id == participant.id]
        cards.append(_account_view(session, participant, account, mine))
    ranked = _rank(cards, people)

    chart = _chart(people, wagers, started, moment)
    games = _games(session, sport, people, wagers, experiment, started, moment, users) if started else []
    history = [_wager_view(w, by_person[w.participant_id], users)
               for w in sorted(wagers, key=lambda w: _aware(w.submitted_at) or _aware(w.created_at), reverse=True)]

    return {
        "sport": sport, "virtual": True, "label": "Virtual competition",
        "status": "STARTED" if started else "NOT_STARTED", "started_at": _iso(started),
        "not_started_reason": None if started else (
            NOT_ELIGIBLE_REASON.get(sport) if experiment is None
            else "An official model pipeline exists; the start has not been recorded yet."),
        "official_pipeline": ({"experiment_id": str(experiment.id), "name": experiment.name,
                               "decision_policy": experiment.decision_policy,
                               "staking_policy": experiment.staking_policy_version} if experiment else None),
        "rules": {"cutoff_minutes": 15, "max_quote_age_minutes": 240,
                  "markets": ["MONEYLINE", "SPREAD", "TOTAL"], "parlays": "NOT_YET_SUPPORTED"},
        "standings": ranked, "chart": chart, "games": games, "history": history,
        "sports": _sport_list(session, moment), "generated_at": moment.isoformat(),
    }


def _rank(cards: list[dict[str, Any]], people) -> list[dict[str, Any]]:
    """Rank by bankroll. Equal bankrolls share a rank and keep the roster order."""

    order = {p.key: p.sort_order for p in people}
    ranked = sorted(cards, key=lambda c: (-c["bankroll"], order.get(c["participant"], 99)))
    for index, card in enumerate(ranked):
        tied = index > 0 and card["bankroll"] == ranked[index - 1]["bankroll"]
        card["rank"] = ranked[index - 1]["rank"] if tied else index + 1
    return ranked


def _chart(people, wagers, started, moment) -> dict[str, Any]:
    """Bankroll paths: a point at the start, then one per settlement. Placing moves nothing."""

    series = []
    for participant in people:
        bankroll = STARTING_BALANCE
        points = [{"at": _iso(started), "bankroll": float(bankroll), "wager_id": None}] if started else []
        settled = sorted((w for w in wagers if w.participant_id == participant.id and w.status == WAGER_SETTLED),
                         key=lambda w: (_aware(w.settled_at), str(w.id)))
        for wager in settled:
            bankroll += _money(wager.profit_loss or 0)
            points.append({"at": _iso(wager.settled_at), "bankroll": float(bankroll),
                           "wager_id": str(wager.id)})
        if started:
            points.append({"at": moment.isoformat(), "bankroll": float(bankroll), "wager_id": None, "now": True})
        series.append({"participant": participant.key, "color": participant.color,
                       "display_name": participant.display_name, "points": points})
    return {"starting_balance": float(STARTING_BALANCE), "series": series}


def _games(session, sport, people, wagers, experiment, started, moment, users) -> list[dict[str, Any]]:
    window_end = moment + timedelta(days=3)
    wager_games = {w.game_id for w in wagers}
    games = list(session.scalars(
        select(Game).join(League, League.id == Game.league_id)
        .where(League.code == sport,
               or_(Game.id.in_(wager_games) if wager_games else false(),
                   and_(Game.starts_at >= started + CUTOFF, Game.starts_at <= window_end)))
        .order_by(Game.starts_at.desc()).limit(80)))
    team_map = _teams(session, [g.id for g in games])
    finals = _official_final_decisions(session, experiment, [g.id for g in games]) if experiment else {}
    by_person = {p.id: p for p in people}
    out = []
    for game in games:
        card = _game_card(session, game, moment, team_map.get(game.id, {}))
        game_wagers = [w for w in wagers if w.game_id == game.id]
        cells = []
        for participant in people:
            mine = [_wager_view(w, participant, users) for w in game_wagers if w.participant_id == participant.id]
            cells.append({"participant": participant.key, "wagers": mine,
                          "state": _cell_state(participant, mine, game, finals.get(game.id), started, moment)})
        placed = [w for cell in cells for w in cell["wagers"] if w["status"] in (WAGER_OPEN, WAGER_SETTLED)]
        pairs = []
        for i, a in enumerate(placed):
            for b in placed[i + 1:]:
                if a["participant"] != b["participant"]:
                    pairs.append({"a": a["participant"], "b": b["participant"],
                                  "relation": _opposition(a, b, card)})
        card.update({"cells": cells, "comparisons": pairs,
                     "divided": any(p["relation"] == "DIRECTLY_OPPOSED" for p in pairs)})
        out.append(card)
    # Upcoming first (soonest), then finished (most recent).
    upcoming = sorted((g for g in out if not g["locked"]), key=lambda g: g["starts_at"])
    done = sorted((g for g in out if g["locked"]), key=lambda g: g["starts_at"], reverse=True)
    return upcoming + done


def _cell_state(participant, mine, game, decision, started, moment) -> dict[str, Any]:
    if any(w["status"] == WAGER_OPEN for w in mine):
        return {"code": "OPEN", "label": "Open"}
    if mine and all(w["status"] == WAGER_SETTLED for w in mine):
        return {"code": "SETTLED", "label": "Settled"}
    if mine and any(w["status"] == WAGER_UNFUNDED for w in mine):
        return {"code": "UNFUNDED", "label": "Model pick not funded"}
    if participant.is_model:
        if decision is not None and _aware(decision.recommended_at) >= started:
            if decision.recommendation == "QUALIFIED_BET":
                return {"code": "IMPORTING", "label": "Model bet — recording"}
            return {"code": "MODEL_PASSED", "label": "Model passed",
                    "detail": decision.recommendation_reason}
        if moment < _cutoff(game):
            return {"code": "AWAITING_MODEL", "label": "Awaiting model decision"}
        return {"code": "NO_MODEL_DECISION", "label": "No model decision recorded"}
    if moment < _cutoff(game) and game.status == GameStatus.SCHEDULED:
        return {"code": "NO_PICK_YET", "label": "No pick submitted"}
    return {"code": "NO_PICK", "label": "No pick submitted"}


def _sport_list(session: Session, moment: datetime) -> list[dict[str, Any]]:
    out = []
    for sport in SPORTS:
        started = _started_at(session, sport)
        eligible = official_experiment(session, sport, now=moment) is not None
        out.append({"sport": sport, "status": "STARTED" if started else "NOT_STARTED",
                    "started_at": _iso(started), "eligible": eligible,
                    "reason": None if eligible else NOT_ELIGIBLE_REASON.get(sport)})
    return out


def _all_sports(session: Session, people, moment) -> dict[str, Any]:
    """Sum of started accounts only. Combined starting capital is stated as it is."""

    users = {row.id: row.display_name for row in session.scalars(select(User))}
    started_sports = [s["sport"] for s in _sport_list(session, moment) if s["status"] == "STARTED"]
    standings = []
    for participant in people:
        total = {"starting_balance": 0.0, "bankroll": 0.0, "settled_profit": 0.0, "open_exposure": 0.0,
                 "available": 0.0, "settled_stake": 0.0, "settled_count": 0, "open_count": 0, "volume": 0.0,
                 "record": {"WIN": 0, "LOSS": 0, "PUSH": 0, "VOID": 0}, "unfunded_count": 0}
        per_sport = []
        for sport in started_sports:
            account = account_for(session, participant, sport)
            mine = list(session.scalars(select(CompetitionWager).where(
                CompetitionWager.account_id == account.id,
                CompetitionWager.status.in_((WAGER_OPEN, WAGER_SETTLED, WAGER_UNFUNDED)))))
            view = _account_view(session, participant, account, mine)
            per_sport.append(view)
            for key in ("starting_balance", "bankroll", "settled_profit", "open_exposure", "available",
                        "settled_stake", "volume"):
                total[key] += view[key] or 0.0
            for key in ("settled_count", "open_count", "unfunded_count"):
                total[key] += view[key]
            for key in total["record"]:
                total["record"][key] += view["record"][key]
        total["roi"] = total["settled_profit"] / total["settled_stake"] if total["settled_stake"] else None
        standings.append({"participant": participant.key, "display_name": participant.display_name,
                          "color": participant.color, "is_model": participant.is_model,
                          **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in total.items()},
                          "by_sport": per_sport})
    standings = _rank(standings, people)
    wagers = list(session.scalars(select(CompetitionWager).where(
        CompetitionWager.status.in_((WAGER_OPEN, WAGER_SETTLED, WAGER_UNFUNDED)))
        .order_by(CompetitionWager.submitted_at.desc()).limit(200)))
    by_person = {p.id: p for p in people}
    return {
        "sport": "ALL", "virtual": True, "label": "Virtual competition",
        "started_sports": started_sports,
        "combined_starting_capital_each": float(STARTING_BALANCE) * len(started_sports),
        "note": ("Each participant holds a separate $1,000 account per started sport; this view adds "
                 "them up, so combined starting capital is $1,000 x the number of started sports. "
                 "Not-started sports keep their $1,000 untouched and are not included."),
        "standings": standings, "history": [_wager_view(w, by_person[w.participant_id], users) for w in wagers],
        "sports": _sport_list(session, moment), "generated_at": moment.isoformat(),
    }


def drafts_for(session: Session, actor: User) -> list[dict[str, Any]]:
    """The drafts this login may still edit."""

    users = {row.id: row.display_name for row in session.scalars(select(User))}
    query = select(CompetitionWager, CompetitionParticipant).join(
        CompetitionParticipant, CompetitionParticipant.id == CompetitionWager.participant_id
    ).where(CompetitionWager.status == WAGER_DRAFT)
    if actor.role != UserRole.ADMIN:
        query = query.where(CompetitionParticipant.user_id == actor.id)
    return [_wager_view(w, p, users) for w, p in session.execute(query.order_by(CompetitionWager.created_at.desc()))]


def entry_permissions(session: Session, actor: User | None) -> list[str]:
    if actor is None:
        return []
    return [p.key for p in participants(session)
            if not p.is_model and (actor.role == UserRole.ADMIN or p.user_id == actor.id)]


def open_games(session: Session, sport: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Games still accepting picks in a started sport."""

    moment = now or datetime.now(UTC)
    started = _started_at(session, sport.upper())
    if started is None:
        return []
    games = list(session.scalars(
        select(Game).join(League, League.id == Game.league_id)
        .where(League.code == sport.upper(), Game.status == GameStatus.SCHEDULED,
               Game.starts_at > moment + CUTOFF, Game.starts_at >= started + CUTOFF,
               Game.starts_at <= moment + timedelta(days=8))
        .order_by(Game.starts_at).limit(60)))
    teams = _teams(session, [g.id for g in games])
    return [_game_card(session, g, moment, teams.get(g.id, {})) for g in games]


__all__ = [
    "CUTOFF", "MAX_QUOTE_AGE", "SPORTS", "CompetitionError", "account_for", "balance", "current_quotes",
    "discard", "drafts_for", "ensure_competition", "entry_permissions", "mirror_model_decisions", "official_experiment", "open_games",
    "participant_by_key", "run", "save_draft", "settle_open", "start_sport", "submit", "summary",
]

