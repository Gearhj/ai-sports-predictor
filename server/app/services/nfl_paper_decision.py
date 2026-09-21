"""The NFL paper policy's decision owner, run at the final-pregame checkpoint.

WHAT IT PRODUCES
----------------
One outcome per NFL game in its checkpoint window, and always an outcome: a
recorded virtual position when every requirement passes, or the exact reason it
did not. "Nothing happened" is not an acceptable answer for a checkpoint that
came and went, because a reader cannot tell it apart from a job that failed to
run.

THE ORDER OF THE GATES IS THE POINT
-----------------------------------
Eligibility is checked FIRST, before any price arithmetic. A reader who sees an
edge computed and then a refusal will reasonably ask why the edge was not taken;
a reader who sees "not eligible" understands that the arithmetic was never the
question. It also means a missing producer binding cannot be quietly rescued by
a big number.

Nothing here calls a provider. It reads the frozen forecast and the stored
quote, both of which some other owner already paid for.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    Game, GameStatus, GameTeam, League, Market, MarketOutcome, OddsSnapshot,
    OfficialAspPosition, PositionResult, PositionStatus, ResearchSupplement,
    ShadowExperiment, ShadowExperimentDecision, ShadowForecast,
    ShadowForecastBundle, Sportsbook, Team,
)
from app.services import nfl_final_readiness as readiness_gate
from app.services import nfl_paper_policy as policy
from app.services import nfl_paper_eligibility as eligibility

#: The producer's own FINAL_PREGAME eligibility: 30 to 15 minutes before
#: kickoff. Unchanged here; NFL adds no exception to the existing timing.
WINDOW_OPENS = timedelta(minutes=30)
WINDOW_CLOSES = timedelta(minutes=15)

#: The existing freshness rule, not a new one.
MAX_QUOTE_AGE_MINUTES = 240


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _era(session: Session) -> ShadowExperiment | None:
    return session.scalar(
        select(ShadowExperiment).where(
            ShadowExperiment.sport == "NFL",
            ShadowExperiment.decision_policy == policy.POLICY_VERSION,
            ShadowExperiment.is_active.is_(True),
        )
    )


def games_at_checkpoint(
    session: Session, *, now: datetime | None = None,
) -> list[dict[str, Any]]:
    """NFL games inside the final-pregame window right now."""

    moment = now or datetime.now(UTC)
    rows = session.execute(
        select(Game.id, Game.starts_at)
        .join(League, League.id == Game.league_id)
        .where(League.code == "NFL",
               Game.status == GameStatus.SCHEDULED,
               Game.starts_at >= moment + WINDOW_CLOSES,
               Game.starts_at <= moment + WINDOW_OPENS)
        .order_by(Game.starts_at)
    ).all()
    out = []
    for game_id, starts_at in rows:
        sides = dict(session.execute(
            select(GameTeam.is_home, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id == game_id)
        ).all())
        out.append({
            "game_id": str(game_id),
            "starts_at": _aware(starts_at).isoformat(),
            "away": sides.get(False), "home": sides.get(True),
        })
    return out


#: What controlled a decision, named rather than inferred. FINAL_PREGAME is the
#: only phase that may control an official research position.
PHASE_FINAL, PHASE_CURRENT = "FINAL_PREGAME", "CURRENT"


def _as_uuid(value: Any) -> Any:
    """Game ids travel through the checkpoint as strings; columns are uuid.

    Postgres accepts the string form, so this was invisible in production, but
    it is a real coercion and stating it here keeps the query backend-neutral.
    """

    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return value
    return value


def _forecast(session: Session, game_id: Any,
              *, phase: str | None = None) -> ShadowForecast | None:
    """The controlling forecast for one game, at one snapshot phase.

    ``phase`` None keeps the old "best available" behaviour, which remains
    correct for preview and research surfaces. The official checkpoint asks for
    a phase EXPLICITLY, because which snapshot controlled a position is exactly
    the thing that must not be decided by a silent fallback.
    """

    order = (phase,) if phase else (PHASE_FINAL, None)
    for snapshot in order:
        query = select(ShadowForecast).where(
            ShadowForecast.canonical_game_id == _as_uuid(game_id),
            ShadowForecast.engine_id == "NFL_M1_XGBOOST",
        )
        if snapshot:
            query = query.where(ShadowForecast.snapshot_type == snapshot)
        row = session.scalar(
            query.order_by(ShadowForecast.available_as_of_utc.desc()).limit(1))
        if row is not None:
            return row
    return None


def _final_forecast_available_by(session: Session, game_id: Any,
                                 cutoff: datetime) -> ShadowForecast | None:
    """The FINAL_PREGAME forecast that was genuinely AVAILABLE before ``cutoff``.

    Availability is read from ``available_as_of_utc``, the producer's own
    visibility clock -- never from ``created_at``. In Postgres ``created_at``
    defaults to the TRANSACTION START, so a row can carry a timestamp minutes
    before the transaction that wrote it committed and became visible. That is
    not a hypothetical: on 2026-09-20 the eight early FINAL_PREGAME rows all
    carry created_at 12:41:07 while ``generate-nfl-final-pregame-forecasts``
    did not finish until 12:43:32. A checkpoint at 12:41:32 could not have seen
    them, and a rule keyed on created_at would wrongly conclude it had.
    """

    return session.scalar(
        select(ShadowForecast)
        .where(ShadowForecast.canonical_game_id == _as_uuid(game_id),
               ShadowForecast.engine_id == "NFL_M1_XGBOOST",
               ShadowForecast.snapshot_type == PHASE_FINAL,
               ShadowForecast.available_as_of_utc.is_not(None),
               ShadowForecast.available_as_of_utc <= cutoff)
        .order_by(ShadowForecast.available_as_of_utc.desc()).limit(1))


def paired_quotes(session: Session, game_id: Any) -> list[dict[str, Any]]:
    """Two-sided moneylines, paired within one provider, book and market.

    THE PAIR IS THE UNIT, NOT THE SIDE
    ----------------------------------
    A single side is not a price. Qualification compares a model probability
    against a market, and a market needs both sides -- from the SAME provider,
    the SAME book and the SAME market row, so the terms match by construction
    rather than by assumption. Pairing across books, or across providers, would
    assemble a "market" that no one was ever offering.

    AND THE OLDER LEG GOVERNS
    -------------------------
    Each side carries the BOOK's own `observed_at`, which is the sportsbook's
    `last_update`, never AISP's retrieval time. Those two legs can be minutes or
    hours apart, so the pair is exactly as fresh as its staler half. Ageing a
    pair by its newer leg would let a genuinely stale market pass because one
    side happened to tick recently.
    """

    rows = session.execute(
        select(Market.id, Sportsbook.key, OddsSnapshot.source,
               MarketOutcome.outcome_key, OddsSnapshot.observed_at,
               OddsSnapshot.american_odds, MarketOutcome.id, OddsSnapshot.id)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id == game_id, Market.market_type == "MONEYLINE")
        .order_by(OddsSnapshot.observed_at.desc())
    ).all()

    # Newest snapshot per (market, book, provider, side). The query is ordered
    # newest first, so the first one seen for a key is the one that counts.
    latest: dict[tuple, dict[str, Any]] = {}
    for (market_id, book, source, outcome_key, observed_at, american,
         outcome_id, snapshot_id) in rows:
        side = str(outcome_key).split(":")[0].upper()
        if side not in ("HOME", "AWAY") or american is None:
            continue
        key = (market_id, book, source, side)
        if key in latest:
            continue
        latest[key] = {"observed_at": _aware(observed_at),
                       "american_odds": int(american),
                       # Carried so a recorded position points at the exact
                       # outcome and snapshot it was priced from, rather than
                       # at a re-derived guess.
                       "market_outcome_id": outcome_id,
                       "odds_snapshot_id": snapshot_id}

    pairs: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for (market_id, book, source, _side) in latest:
        identity = (market_id, book, source)
        if identity in seen:
            continue
        seen.add(identity)
        away = latest.get((market_id, book, source, "AWAY"))
        home = latest.get((market_id, book, source, "HOME"))
        if away is None or home is None:
            continue  # one side is not a market
        older = min(away["observed_at"], home["observed_at"])
        newer = max(away["observed_at"], home["observed_at"])
        pairs.append({
            "market_id": str(market_id),
            "sportsbook": book,
            "source": source,
            "away_american": away["american_odds"],
            "home_american": home["american_odds"],
            "away_outcome_id": away["market_outcome_id"],
            "home_outcome_id": home["market_outcome_id"],
            "away_snapshot_id": away["odds_snapshot_id"],
            "home_snapshot_id": home["odds_snapshot_id"],
            "away_observed_at": away["observed_at"].isoformat(),
            "home_observed_at": home["observed_at"].isoformat(),
            # The one that governs freshness, named so a reader can check it.
            "pair_observed_at": older.isoformat(),
            "older_leg": "AWAY" if away["observed_at"] == older else "HOME",
            "legs_apart_minutes": round(
                (newer - older).total_seconds() / 60, 1),
        })
    pairs.sort(key=lambda pair: pair["pair_observed_at"], reverse=True)
    return pairs


def _jsonable(value: Any) -> Any:
    """UUIDs and datetimes to strings, recursively. Nothing is dropped."""

    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


#: The phase this owner writes under. One decision per game per phase, so the
#: five-minute job firing three times inside a fifteen-minute window records
#: one decision and one position -- not three.
PHASE = "FINAL_PREGAME"


def existing_decision(
    session: Session, *, experiment_id: Any, game_id: Any,
) -> ShadowExperimentDecision | None:
    return session.scalar(
        select(ShadowExperimentDecision).where(
            ShadowExperimentDecision.experiment_id == experiment_id,
            ShadowExperimentDecision.game_id == game_id,
            ShadowExperimentDecision.phase == PHASE,
        ).order_by(ShadowExperimentDecision.recommended_at.desc()).limit(1)
    )


def _exposure(session: Session, experiment: ShadowExperiment,
              game_id: Any, day: date) -> tuple[float, float]:
    """Stake already committed on this game, and across this day."""

    rows = session.execute(
        select(OfficialAspPosition.stake, ShadowExperimentDecision.game_id,
               ShadowExperimentDecision.slate_date)
        .join(ShadowExperimentDecision,
              ShadowExperimentDecision.id == OfficialAspPosition.shadow_decision_id)
        .where(OfficialAspPosition.shadow_experiment_id == experiment.id,
               OfficialAspPosition.status == PositionStatus.OPEN)
    ).all()
    same_game = sum(float(stake) for stake, gid, _ in rows if gid == game_id)
    same_day = sum(float(stake) for stake, _, slate in rows if slate == day)
    return same_game, same_day


def _ts(value: Any) -> datetime | None:
    """A datetime column's value from whatever the decision dict carries."""

    if value is None or isinstance(value, datetime):
        return value
    try:
        return _aware(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def _resolve_staking(session: Session, experiment: ShadowExperiment, game_id: Any,
                     slate_date: Any, best: dict[str, Any]) -> dict[str, Any]:
    """Size a qualifying pick, and record what else was considered.

    Returns the stake, the bankroll it was sized against, the binding limits,
    and the receipt that becomes ``staking_detail`` on the durable row. It
    writes nothing, so it can safely run BEFORE the decision row exists -- which
    is the whole point: the row can then be written once, already knowing
    whether a position followed.
    """

    bankroll = float(experiment.starting_bankroll)
    same_game, same_day = _exposure(session, experiment, game_id, slate_date)
    raw = float(best["quarter_kelly"]) * bankroll
    daily_room = max(0.0, experiment.max_daily_percentage * bankroll - same_day)

    # THE LIMITS THAT ACTUALLY BIND A RESEARCH POSITION.
    #
    # The aggregate daily ceiling is deliberately NOT among them: it truncated
    # the research sample in evaluation order rather than on merit (see
    # `AGGREGATE_DAILY_CAP_BINDS` in nfl_paper_policy). The per-position cap and
    # the same-game ceiling still bind, so a single pick's size and a single
    # game's total exposure are unchanged.
    limits = {
        "quarter_kelly": raw,
        "position_cap": experiment.max_position_percentage * bankroll,
        "same_game_cap": max(
            0.0, experiment.max_same_game_percentage * bankroll - same_game),
    }
    if policy.AGGREGATE_DAILY_CAP_BINDS:
        limits["daily_cap"] = daily_room
    stake = round(min(limits.values()), 2)

    # ANALYTICAL FIELDS -- recorded, never binding.
    #
    # `recommended_kelly_stake` is the unconstrained quarter-Kelly size, so the
    # sizing the model actually recommended survives even though the position is
    # taken at the flat research size.
    #
    # `constrained_bankroll_portfolio` is what this pick WOULD have received
    # under the aggregate daily ceiling, so the capped-portfolio view stays
    # reconstructable and comparable with the record built before the ceiling
    # was lifted. `would_be_declined` marks the picks the old rule would have
    # dropped entirely -- the ones whose absence used to bias the sample.
    constrained = round(min(list(limits.values()) + [daily_room]), 2)
    receipt = {
        **limits,
        "stake": stake,
        "bound_by": min(limits, key=limits.get),
        "aggregate_daily_cap_binds": policy.AGGREGATE_DAILY_CAP_BINDS,
        "recommended_kelly_stake": round(raw, 2),
        "recommended_kelly_fraction": float(best["quarter_kelly"]),
        "constrained_bankroll_portfolio": {
            "daily_cap_fraction": float(experiment.max_daily_percentage),
            "daily_room_before_this_pick": round(daily_room, 2),
            "stake_under_daily_cap": constrained,
            "would_be_declined": constrained <= 0,
            "basis": "ANALYTICAL_ONLY_NOT_ENFORCED",
        },
    }
    return {"stake": stake, "bankroll": round(bankroll, 2),
            "limits": limits, "receipt": receipt}


#: Reasons that name a MISSING PREREQUISITE rather than a judgement.
#:
#: A checkpoint that could not evaluate -- no forecast yet, no stored pair, no
#: permitted provider, only a stale pair, or the producer's permission not yet
#: imported -- has recorded a true statement about that instant, not a verdict
#: on the game. The evidence can legitimately arrive later in the same window:
#: a lineup-dependent forecast or a first price can land at T-20, well after the
#: T-30 tick that found nothing. Pinning the first tick's answer would throw the
#: window away.
#:
#: What is NOT here matters as much. ``NO_QUALIFYING_VALUE`` is a real
#: evaluation against a usable price, ``ALL_GATES_PASSED`` is a taken decision,
#: ``STAKING_OR_EXPOSURE_CAP`` is the caps doing their job and
#: ``ELIGIBILITY_RECORD_GRANTS_EXECUTION`` is a defective permission, not an
#: absent one. None of those may ever be re-opened by a later tick.
#:
#: This mirrors ``cfb/paper_decision.py:61`` and ``nba/gen1_paper.py:67``, which
#: have always worked this way; NFL was the outlier.
#: ``NO_FINAL_PREGAME_FORECAST`` belongs here for the same reason as the rest: a
#: FINAL forecast that has not committed yet is a MISSING input, not a verdict.
#: On 2026-09-20 the FINAL generation transaction committed at 12:43:32 while
#: the checkpoint ran at 12:41:32, so a later tick inside the same window would
#: have found it. Pinning the first answer would turn a two-minute wait into a
#: lost evaluation.
#: ``NO_POSITIVE_READINESS_EVIDENCE`` is here for exactly the same reason. The
#: producer publishes the forecast, the grant, the locator and the readiness
#: proof as four SEPARATE filesystem operations -- the contract is explicit that
#: this is "not an all-files atomic transaction" -- so a checkpoint can read a
#: complete forecast microseconds before its readiness sidecar appears. That is
#: an input that has not landed yet, not a verdict about the game, and a later
#: tick inside the same window must be able to find it.
PREREQUISITE_REASONS = frozenset(eligibility.MISSING_PERMISSION_REASONS) | {
    "NO_FORECAST", "NO_FINAL_PREGAME_FORECAST", "NO_TWO_SIDED_QUOTE",
    "NO_PERMITTED_PROVIDER", "QUOTE_TOO_STALE", readiness_gate.REASON,
}


def _supersedes_transient_state(
    session: Session, prior: ShadowExperimentDecision, decision: dict[str, Any],
) -> bool:
    """May this evaluation replace the stored one, or must it reuse it?

    Three conditions, all required. The stored decision named a missing
    prerequisite; nothing was ever staked on it; and this evaluation reached a
    different answer. A repeat of the same missing prerequisite reuses the
    stored row, so three ticks that all find nothing leave one record, not
    three -- and a priced or positioned decision is never re-opened at all.
    """

    prior_reason = (prior.qualification_reasons or {}).get("reason")
    if prior_reason not in PREREQUISITE_REASONS:
        return False
    if session.scalar(select(OfficialAspPosition.id).where(
            OfficialAspPosition.shadow_decision_id == prior.id)) is not None:
        return False
    return decision.get("reason") != prior_reason


def record_outcome(
    session: Session, *, experiment: ShadowExperiment, game_id: Any,
    decision: dict[str, Any], moment: datetime,
) -> dict[str, Any]:
    """Persist the decision, and a position only when one qualifies.

    Every outcome is written, not only the qualifying ones. A checkpoint that
    persists nothing when it declines leaves a reader unable to tell a declined
    evaluation from a job that never ran, and those need different responses.
    """

    prior = existing_decision(session, experiment_id=experiment.id, game_id=game_id)
    if prior is not None and _supersedes_transient_state(session, prior, decision):
        # The earlier tick recorded a MISSING PREREQUISITE -- no forecast, no
        # stored pair, no permitted provider, a stale pair, or the producer's
        # exact permission not yet imported. Nothing was priced and nothing was
        # staked. The evidence has since arrived inside the same window, so this
        # evaluation is recorded as its own decision rather than being discarded
        # in favour of a state whose premise no longer holds. Every other gate is
        # unchanged, and a priced or positioned decision is never re-evaluated.
        prior = None
    if prior is not None:
        position = session.scalar(select(OfficialAspPosition).where(
            OfficialAspPosition.shadow_decision_id == prior.id))
        # _jsonable at the RECEIPT boundary, not on the stored columns.
        # Stringifying `decision_id` alone was not enough: the receipt carries
        # `shadow_forecast_row_id` -- and anything else the evaluator adds
        # later -- nested inside, and one raw UUID anywhere in the tree fails
        # the whole json.dumps. Converting at the boundary covers the fields
        # that exist today and the ones that do not yet.
        # The reused branch re-evaluates before it discovers the prior row, so
        # the surrounding fields describe THIS evaluation while the ids describe
        # the stored one. Left as-is that reads as though the position had been
        # taken on a forecast row it never saw -- the re-evaluation can pick up
        # a newer forecast generated since. So what was actually persisted is
        # reported explicitly, next to the live recomputation rather than
        # blended into it.
        return _jsonable({**decision, "decision_id": str(prior.id),
                          "position_id": str(position.id) if position else None,
                          "persisted": "REUSED_EXISTING_DECISION",
                          "persisted_record": {
                              "decision_id": prior.id,
                              "recommendation": prior.recommendation,
                              "recommended_at": prior.recommended_at,
                              "shadow_forecast_id": prior.shadow_forecast_id,
                              "odds_snapshot_id": prior.odds_snapshot_id,
                              "american_odds": prior.american_odds,
                              "sportsbook": prior.sportsbook,
                              "position_id": position.id if position else None,
                              "stake": (float(position.stake)
                                        if position is not None else None),
                          }})

    best = decision.get("selection") or decision.get("best") or {}
    qualified = decision["outcome"] == "QUALIFIED"
    slate_date = moment.astimezone(UTC).date()

    # STAKING IS RESOLVED BEFORE THE ROW IS WRITTEN.
    #
    # It used to run after, against an already-flushed row that was never
    # updated, which is how three exposure-capped games came to be persisted as
    # QUALIFIED_BET while the job receipt said DECLINED. Computing first costs
    # nothing -- staking reads positions, not this row -- and it lets the row be
    # written once, with the answer that is actually true.
    staking = _resolve_staking(session, experiment, game_id, slate_date, best) \
        if qualified else None
    executed = bool(staking and staking["stake"] > 0)

    # The PRE-staking answer, kept verbatim before anything below can amend it.
    qualification_outcome = decision["outcome"]
    qualification_reason = decision["reason"]

    if qualified and not executed:
        # Value was there; room was not. The record says so in both fields.
        decision = {**decision, "outcome": "DECLINED",
                    "reason": "STAKING_OR_EXPOSURE_CAP",
                    "detail": f"Every cap resolved to zero ({staking['limits']})."}
    if staking is not None:
        decision = {**decision, "staking": staking["receipt"]}

    row = ShadowExperimentDecision(
        experiment_id=experiment.id, game_id=game_id, slate_date=slate_date,
        phase=PHASE, sport="NFL", market="MONEYLINE", period="full_game",
        selection=str(best.get("side") or "NONE"),
        sportsbook=best.get("sportsbook"),
        american_odds=best.get("american_odds"),
        decimal_odds=(policy.american_to_decimal(int(best["american_odds"]))
                      if best.get("american_odds") is not None else None),
        market_outcome_id=best.get("market_outcome_id"),
        odds_snapshot_id=best.get("odds_snapshot_id"),
        quote_observed_at=(datetime.fromisoformat(best["observed_at"])
                           if best.get("observed_at") else None),
        shadow_forecast_id=decision.get("shadow_forecast_row_id"),
        recommended_at=moment,
        model_probability=best.get("win_probability"),
        expected_value=best.get("expected_value"),
        # The POST-staking disposition. A value-qualified game that received no
        # position reads DECLINED here, not QUALIFIED_BET, because this is the
        # field every downstream reader already treats as "what AISP did".
        recommendation="QUALIFIED_BET" if executed else decision["outcome"],
        recommendation_reason=str(decision.get("detail") or decision["reason"]),
        qualification_reasons={"reason": decision["reason"],
                               "outcome": decision["outcome"]},
        # D2: the two questions, unambiguously apart and durable.
        qualification_outcome=qualification_outcome,
        qualification_reason=qualification_reason,
        execution_outcome="EXECUTED" if executed else "NOT_EXECUTED",
        execution_reason=(
            "POSITION_TAKEN" if executed
            else ("STAKING_OR_EXPOSURE_CAP" if qualified else qualification_reason)),
        staking_detail=_jsonable(staking["receipt"]) if staking else None,
        # D3/D6: which snapshot controlled, its durable identity, and the
        # clocks that prove it was available. The uuid `forecast_id` column is
        # deliberately left alone -- it is a foreign key to `forecasts`, and an
        # NFL forecast lives in `shadow_forecasts` (carried by
        # `shadow_forecast_id` above).
        controlling_forecast_identity=(
            str(decision["forecast_id"]) if decision.get("forecast_id") else None),
        forecast_snapshot_phase=decision.get("forecast_snapshot_phase"),
        forecast_published_at=_ts(decision.get("forecast_published_at")),
        forecast_imported_at=_ts(decision.get("forecast_available_as_of")),
        # D6: the authority behind the decision, no longer stripped.
        eligibility_binding=_jsonable(decision.get("eligibility")),
        eligibility_supplement_id=decision.get("eligibility_supplement_id"),
        source_mode="LIVE",
        # JSON-safe: the decision carries UUIDs for the forecast row, the
        # market outcome and the odds snapshot, and a UUID is not serialisable.
        # The eligibility binding is no longer stripped here -- it has its own
        # column now, and dropping it from the evidence too is what made the
        # authority unrecoverable in the first place.
        evidence=_jsonable(dict(decision)),
    )
    session.add(row)
    session.flush()

    position_id = None
    if executed:
        position = OfficialAspPosition(
            shadow_forecast_id=decision.get("shadow_forecast_row_id"),
            market_outcome_id=best["market_outcome_id"],
            odds_snapshot_id=best["odds_snapshot_id"],
            placed_at=moment, stake=staking["stake"],
            decimal_odds=policy.american_to_decimal(int(best["american_odds"])),
            result=PositionResult.PENDING, status=PositionStatus.OPEN,
            strategy=experiment.staking_policy_version,
            shadow_experiment_id=experiment.id, shadow_decision_id=row.id,
            bankroll_before_position=staking["bankroll"],
            recommendation_policy_version=experiment.recommendation_policy_version,
            staking_policy_version=experiment.staking_policy_version,
        )
        session.add(position)
        session.flush()
        position_id = str(position.id)

    return _jsonable({**decision, "decision_id": str(row.id),
                      "position_id": position_id, "persisted": "RECORDED"})


def evaluate(
    session: Session, settings: Settings, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Run the checkpoint. Returns one decision per game, always with a reason."""

    moment = now or datetime.now(UTC)
    era = _era(session)
    if era is None:
        return {"records": 0, "reason": "NFL_PAPER_POLICY_IS_NOT_ACTIVE",
                "decisions": []}

    due = games_at_checkpoint(session, now=moment)
    if not due:
        return {"records": 0, "reason": "NO_NFL_GAME_AT_ITS_CHECKPOINT",
                "decisions": [], "experiment_id": str(era.id)}

    decisions: list[dict[str, Any]] = []
    for game in due:
        outcome = _decide(session, era, game, moment, settings)
        # Persisted whatever the outcome, then committed: a checkpoint that
        # evaluated and wrote nothing is indistinguishable from one that never
        # ran, and only one of those needs someone to look at it.
        outcome = record_outcome(session, experiment=era,
                                 game_id=game["game_id"], decision=outcome,
                                 moment=moment)
        session.commit()
        decisions.append(outcome)
    # The job receipt itself. Converted here as well as in `record_outcome`,
    # because this is the object that reaches `job_runs.result`, and a future
    # key added to this dict must not be able to break the receipt again.
    #
    # `records` counted an outcome string that is never produced -- the
    # evaluator emits QUALIFIED, DECLINED or BLOCKED -- so a run that created a
    # real position reported "records": 0. It counts positions now, which is
    # what a reader of this receipt is actually asking.
    return _jsonable({
        "records": sum(1 for d in decisions if d.get("position_id")),
        "reason": "CHECKPOINT_EVALUATED",
        "experiment_id": str(era.id),
        "policy_version": policy.POLICY_VERSION,
        "decisions": decisions,
    })


def _readiness_root(settings: Settings | None) -> str | None:
    """Where the readiness family is read from, or None when unconfigured."""

    if settings is None:
        return None
    explicit = str(getattr(settings, "nfl_final_readiness_root", "") or "").strip()
    if explicit:
        return explicit
    configured = str(getattr(settings, "probability_exchange_root", "") or "").strip()
    return configured or None


def _check_readiness(
    session: Session, settings: Settings | None, forecast: ShadowForecast,
    *, kickoff: datetime, moment: datetime,
) -> readiness_gate.ReadinessVerification:
    """Run the readiness joins for one FINAL forecast and return the verdict.

    The CONSUMER'S OWN CLOCKS come from here, not from the producer's document.
    ``moment`` is when this checkpoint physically ran and read the sidecar; the
    package and grant import times are the rows' own ``imported_at``, which is
    when this consumer actually took delivery. None of the three is read from
    ``availableAsOfUtc``, which the contract says is numerical completion and
    not exchange visibility.
    """

    # ARMING WITHOUT A PIN IS REFUSED.
    #
    # An unpinned revision check can only require a NON-EMPTY string, which a
    # receipt from the OLD producer satisfies. Enforcing readiness in that state
    # would look like a safety gate while actually admitting any authentic-
    # looking receipt, which is worse than leaving it off: it converts an
    # explicit gap into a false assurance. So the two must be armed together,
    # and the refusal names the missing pin rather than failing quietly.
    if getattr(settings, "nfl_final_readiness_required", False):
        missing = [name for name, value in (
            ("NFL_FINAL_REQUIRED_PRODUCER_REVISION",
             getattr(settings, "nfl_final_required_producer_revision", "")),
            ("NFL_FINAL_READINESS_SCHEMA_SHA256",
             getattr(settings, "nfl_final_readiness_schema_sha256", "")),
        ) if not str(value or "").strip()]
        if missing:
            return readiness_gate.ReadinessVerification(
                verified=False, forecast_id=forecast.forecast_id,
                reason=readiness_gate.REASON,
                sub_reasons=("READINESS_ENFORCEMENT_ARMED_WITHOUT_PIN",),
                detail=("Readiness enforcement is armed but "
                        + ", ".join(missing) + " is unset; an unpinned check "
                        "would admit a receipt from the superseded producer."),
                evidence={"checkpoint_selected_forecast_id": forecast.forecast_id,
                          "missing_pins": missing})

    root = _readiness_root(settings)
    if root is None:
        return readiness_gate.ReadinessVerification(
            verified=False, forecast_id=forecast.forecast_id,
            reason=readiness_gate.REASON,
            sub_reasons=("READINESS_ROOT_NOT_CONFIGURED",),
            detail="No model-exchange root is configured for the readiness family.",
            evidence={"checkpoint_selected_forecast_id": forecast.forecast_id})

    package_imported_at = session.scalar(
        select(ShadowForecastBundle.imported_at)
        .where(ShadowForecastBundle.id == forecast.bundle_id))
    grant_imported_at = session.scalar(
        select(ResearchSupplement.imported_at)
        .where(ResearchSupplement.forecast_id == forecast.forecast_id,
               ResearchSupplement.kind == "PAPER_RESEARCH_ELIGIBILITY")
        .order_by(ResearchSupplement.imported_at.asc()).limit(1))

    return readiness_gate.verify_final_readiness(
        exchange_root=root,
        forecast_id=forecast.forecast_id,
        event_id=forecast.event_id,
        kickoff_utc=kickoff,
        observed_at=moment,
        checkpoint_forecast_id=forecast.forecast_id,
        snapshot_type=forecast.snapshot_type,
        snapshot_version=forecast.snapshot_version,
        engine_id=forecast.engine_id,
        package_imported_at=_aware(package_imported_at) if package_imported_at else None,
        grant_imported_at=_aware(grant_imported_at) if grant_imported_at else None,
        # Pinned from configuration, never inferred. While these are empty the
        # join can only require a NON-EMPTY producer revision, which an
        # old-revision receipt would satisfy -- tolerable only while
        # enforcement is off. They must be set before it is armed.
        required_producer_revision=(
            str(getattr(settings, "nfl_final_required_producer_revision", "") or "").strip()
            or None),
        required_schema_sha256=(
            str(getattr(settings, "nfl_final_readiness_schema_sha256", "") or "").strip()
            or None),
        schema_path=(
            str(getattr(settings, "nfl_final_readiness_schema_path", "") or "").strip()
            or None),
    )


def _decide(
    session: Session, era: ShadowExperiment, game: dict[str, Any], moment: datetime,
    settings: Settings | None = None,
) -> dict[str, Any]:
    base = {"game_id": game["game_id"], "matchup": f"{game['away']} at {game['home']}",
            "starts_at": game["starts_at"], "evaluated_at": moment.isoformat()}

    # THE CONTROLLING FORECAST IS THE FINAL ONE, OR THERE IS NO POSITION.
    #
    # The official research position may only be controlled by a FINAL_PREGAME
    # forecast that was genuinely available before this game's decision cutoff.
    # The previous code asked for FINAL and silently fell back to CURRENT, and
    # on 2026-09-20 that fallback placed all four NFL positions on forecasts
    # published five and six days earlier -- not because a newer forecast was
    # overlooked, but because the FINAL generation transaction had not committed
    # yet and the fallback quietly took a stale one instead of stopping.
    #
    # Failing closed is the whole point: a position controlled by a multi-day-old
    # CURRENT snapshot is worse than no position, because it looks governed.
    cutoff = _aware(datetime.fromisoformat(game["starts_at"])) - WINDOW_CLOSES
    forecast = _final_forecast_available_by(session, game["game_id"], cutoff)
    if forecast is None:
        current = _forecast(session, game["game_id"], phase=None)
        if current is None:
            return {**base, "outcome": "BLOCKED", "reason": "NO_FORECAST",
                    "detail": "No NFL_M1_XGBOOST forecast exists for this game."}
        # CURRENT stays VISIBLE -- it is named, with its own phase and clocks,
        # so preview and research surfaces lose nothing. It simply may not
        # control an official position.
        return {**base, "outcome": "BLOCKED", "reason": "NO_FINAL_PREGAME_FORECAST",
                "detail": (
                    "No FINAL_PREGAME forecast was available before this game's "
                    f"cutoff ({cutoff.isoformat()}). The most recent CURRENT "
                    f"snapshot is {current.snapshot_type} v{current.snapshot_version}, "
                    f"available {current.available_as_of_utc}. A CURRENT snapshot "
                    "does not control an official research position."),
                "forecast_id": current.forecast_id,
                "shadow_forecast_row_id": current.id,
                "forecast_snapshot": f"{current.snapshot_type} v{current.snapshot_version}",
                "forecast_snapshot_phase": current.snapshot_type,
                "forecast_available_as_of": current.available_as_of_utc,
                "controlling": False}
    base = {**base, "forecast_id": forecast.forecast_id,
            "shadow_forecast_row_id": forecast.id,
            "forecast_snapshot": f"{forecast.snapshot_type} v{forecast.snapshot_version}",
            "forecast_snapshot_phase": forecast.snapshot_type,
            "forecast_available_as_of": forecast.available_as_of_utc,
            "forecast_published_at": forecast.available_as_of_utc,
            "forecast_cutoff_utc": forecast.forecast_cutoff_utc,
            "controlling": True}

    # THE SECOND FINAL GATE: READINESS EVIDENCE.
    #
    # "Physical forecast bytes without all required positive evidence are not
    # consumer readiness." Having a FINAL row is not the same as the producer
    # having published, granted, indexed and PROVEN it, and those are four
    # separate filesystem operations that can land in any order or not at all.
    #
    # The verification ALWAYS RUNS and its verdict is ALWAYS RECORDED, so the
    # decision carries evidence about the readiness family from the day this
    # ships. Whether it BLOCKS is the activation switch: the contract requires
    # the family only "for FINALs from the activated operational-V2 producer
    # revision", and that revision is not activated, so turning the requirement
    # on by default would fail-close FINALs from the unchanged CURRENT producer.
    #
    # Note what is NOT reachable here: a CURRENT forecast. The branch above has
    # already returned, so readiness evidence can never rescue a CURRENT
    # snapshot into controlling a position -- it is only ever consulted about a
    # FINAL that was already selected on its own merits.
    readiness = _check_readiness(session, settings, forecast,
                                 kickoff=_aware(datetime.fromisoformat(game["starts_at"])),
                                 moment=moment)
    base = {**base, "readiness": readiness.as_record(),
            "readiness_required": bool(
                getattr(settings, "nfl_final_readiness_required", False))}
    if not readiness.verified and getattr(settings, "nfl_final_readiness_required", False):
        return {**base, "outcome": "BLOCKED", "reason": readiness.reason,
                "detail": (
                    "The FINAL_PREGAME forecast does not carry complete positive "
                    f"readiness evidence ({', '.join(readiness.sub_reasons)}): "
                    f"{readiness.detail}"),
                "forecast_id": forecast.forecast_id}

    # Eligibility first, deliberately. See the module docstring.
    binding = eligibility.check(
        session, event_id=forecast.event_id, forecast_id=forecast.forecast_id,
        experiment_id=str(era.id), now=moment)
    binding = eligibility.successor_gap(
        session, event_id=forecast.event_id, forecast_id=forecast.forecast_id, binding=binding)
    if not binding["eligible"]:
        return {**base, "outcome": "BLOCKED", "reason": binding["reason"],
                "detail": binding.get("detail"),
                "forecast_id": forecast.forecast_id,
                "eligibility": binding}

    # D6: the authority that permitted this decision travels WITH it from here
    # on, including on the success path. It used to be computed, used, and then
    # dropped -- so a decision could be read back without any way to recover
    # what had authorised it.
    base = {**base, "eligibility": binding,
            "eligibility_supplement_id": binding.get("supplement_id")}

    pairs = paired_quotes(session, game["game_id"])
    if not pairs:
        return {**base, "outcome": "DECLINED", "reason": "NO_TWO_SIDED_QUOTE",
                "detail": ("No moneyline pair is stored for this game from a "
                           "single provider, book and market. AISP holds no "
                           "price; this is not a sportsbook withholding one."),
                "forecast_id": forecast.forecast_id}

    # Provider permission, checked before anything is priced.
    permitted = []
    refused_providers: list[str] = []
    for pair in pairs:
        rule = policy.PERMITTED_QUOTE_PROVIDERS.get(pair["source"])
        if rule and rule.get("permitted"):
            permitted.append({**pair,
                              "provider_classification": rule["registry_classification"]})
        else:
            refused_providers.append(pair["source"])
    if not permitted:
        return {**base, "outcome": "DECLINED", "reason": "NO_PERMITTED_PROVIDER",
                "detail": ("This policy permits no quote provider that has a "
                           f"stored pair here ({', '.join(sorted(set(refused_providers)))})."),
                "forecast_id": forecast.forecast_id}

    # Freshness, on the OLDER leg of each pair and on the book's own timestamp.
    fresh = []
    stale = []
    for pair in permitted:
        age = (moment - datetime.fromisoformat(
            pair["pair_observed_at"])).total_seconds() / 60
        entry = {**pair, "pair_age_minutes": round(age, 1)}
        (fresh if age <= MAX_QUOTE_AGE_MINUTES else stale).append(entry)
    if not fresh:
        freshest = min(stale, key=lambda item: item["pair_age_minutes"])
        return {**base, "outcome": "DECLINED", "reason": "QUOTE_TOO_STALE",
                "detail": (
                    f"The freshest permitted pair is {freshest['pair_age_minutes'] / 60:.1f} "
                    f"hours old on its {freshest['older_leg']} leg, past the "
                    f"{MAX_QUOTE_AGE_MINUTES / 60:.0f}-hour limit. Age is the "
                    "book's own timestamp on the STALER side, never AISP's "
                    "retrieval time."),
                "pairs_considered": stale,
                "forecast_id": forecast.forecast_id}

    p_home = forecast.full_home_win_probability
    p_away = forecast.full_away_win_probability
    p_tie = max(0.0, 1.0 - (p_home or 0) - (p_away or 0))

    best: dict[str, Any] | None = None
    for pair in fresh:
        for side, win, american in (
            ("AWAY", p_away, pair["away_american"]),
            ("HOME", p_home, pair["home_american"]),
        ):
            market = policy.TiedMarket(
                win_probability=win or 0.0, tie_probability=p_tie,
                decimal_odds=policy.american_to_decimal(int(american)))
            expected = policy.moneyline_ev(market)
            if best is None or expected > best["expected_value"]:
                best = {
                    "side": side, "american_odds": american,
                    "market_outcome_id": pair[f"{side.lower()}_outcome_id"],
                    "odds_snapshot_id": pair[f"{side.lower()}_snapshot_id"],
                    "observed_at": (pair["away_observed_at"] if side == "AWAY"
                                    else pair["home_observed_at"]),
                    "sportsbook": pair["sportsbook"], "source": pair["source"],
                    "provider_classification": pair["provider_classification"],
                    "market_id": pair["market_id"],
                    "pair_observed_at": pair["pair_observed_at"],
                    "away_observed_at": pair["away_observed_at"],
                    "home_observed_at": pair["home_observed_at"],
                    "older_leg": pair["older_leg"],
                    "pair_age_minutes": pair["pair_age_minutes"],
                    "opposite_american": (pair["home_american"] if side == "AWAY"
                                          else pair["away_american"]),
                    "win_probability": win, "tie_probability": p_tie,
                    "loss_probability": market.loss_probability,
                    "expected_value": expected,
                    "quarter_kelly": policy.kelly_fraction(market) * policy.KELLY_FRACTION,
                    "stake": policy.proposed_stake(
                        market, bankroll=float(era.starting_bankroll)),
                    "settlement": policy.TIE_TREATMENT,
                }

    if best is None or best["expected_value"] < policy.MIN_ADJUSTED_EDGE:
        return {**base, "outcome": "DECLINED", "reason": "NO_QUALIFYING_VALUE",
                "detail": ("No side of any fresh permitted pair cleared the "
                           f"{policy.MIN_ADJUSTED_EDGE * 100:.1f}-point minimum."),
                "best": best, "pairs_considered": fresh,
                "forecast_id": forecast.forecast_id}

    # Everything passed. Recording the position itself is the next owner's job
    # and is deliberately not done inline here.
    return {**base, "outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED",
            "selection": best, "eligibility": binding}


__all__ = ["MAX_QUOTE_AGE_MINUTES", "PHASE", "PREREQUISITE_REASONS",
           "WINDOW_CLOSES", "WINDOW_OPENS",
           "evaluate", "existing_decision", "games_at_checkpoint",
           "paired_quotes", "record_outcome"]
