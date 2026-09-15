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
    OfficialAspPosition, PositionResult, PositionStatus, ShadowExperiment,
    ShadowExperimentDecision, ShadowForecast, Sportsbook, Team,
)
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


def _forecast(session: Session, game_id: Any) -> ShadowForecast | None:
    """The frozen final-pregame forecast, falling back to the current one."""

    for snapshot in ("FINAL_PREGAME", None):
        query = select(ShadowForecast).where(
            ShadowForecast.canonical_game_id == game_id,
            ShadowForecast.engine_id == "NFL_M1_XGBOOST",
        )
        if snapshot:
            query = query.where(ShadowForecast.snapshot_type == snapshot)
        row = session.scalar(
            query.order_by(ShadowForecast.available_as_of_utc.desc()).limit(1))
        if row is not None:
            return row
    return None


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
        recommendation="QUALIFIED_BET" if qualified else decision["outcome"],
        recommendation_reason=str(decision.get("detail") or decision["reason"]),
        qualification_reasons={"reason": decision["reason"],
                               "outcome": decision["outcome"]},
        source_mode="LIVE",
        # JSON-safe: the decision carries UUIDs for the forecast row, the
        # market outcome and the odds snapshot, and a UUID is not serialisable.
        # They are stringified rather than dropped, because the evidence blob
        # is where a reader looks to find exactly what was priced.
        evidence=_jsonable({k: v for k, v in decision.items()
                            if k not in ("eligibility",)}),
    )
    session.add(row)
    session.flush()

    position_id = None
    capped_by = None
    if qualified:
        bankroll = float(experiment.starting_bankroll)
        same_game, same_day = _exposure(session, experiment, game_id, slate_date)
        raw = float(best["quarter_kelly"]) * bankroll
        limits = {
            "quarter_kelly": raw,
            "position_cap": experiment.max_position_percentage * bankroll,
            "same_game_cap": max(
                0.0, experiment.max_same_game_percentage * bankroll - same_game),
            "daily_cap": max(
                0.0, experiment.max_daily_percentage * bankroll - same_day),
        }
        stake = round(min(limits.values()), 2)
        capped_by = min(limits, key=limits.get)
        if stake > 0:
            position = OfficialAspPosition(
                shadow_forecast_id=decision.get("shadow_forecast_row_id"),
                market_outcome_id=best["market_outcome_id"],
                odds_snapshot_id=best["odds_snapshot_id"],
                placed_at=moment, stake=stake,
                decimal_odds=policy.american_to_decimal(int(best["american_odds"])),
                result=PositionResult.PENDING, status=PositionStatus.OPEN,
                strategy=experiment.staking_policy_version,
                shadow_experiment_id=experiment.id, shadow_decision_id=row.id,
                bankroll_before_position=round(bankroll, 2),
                recommendation_policy_version=experiment.recommendation_policy_version,
                staking_policy_version=experiment.staking_policy_version,
            )
            session.add(position)
            session.flush()
            position_id = str(position.id)
        else:
            decision = {**decision, "outcome": "DECLINED",
                        "reason": "STAKING_OR_EXPOSURE_CAP",
                        "detail": f"Every cap resolved to zero ({limits})."}
        decision = {**decision, "staking": {**limits, "stake": stake,
                                            "bound_by": capped_by}}

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
        outcome = _decide(session, era, game, moment)
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


def _decide(
    session: Session, era: ShadowExperiment, game: dict[str, Any], moment: datetime,
) -> dict[str, Any]:
    base = {"game_id": game["game_id"], "matchup": f"{game['away']} at {game['home']}",
            "starts_at": game["starts_at"], "evaluated_at": moment.isoformat()}

    forecast = _forecast(session, game["game_id"])
    if forecast is None:
        return {**base, "outcome": "BLOCKED", "reason": "NO_FORECAST",
                "detail": "No NFL_M1_XGBOOST forecast exists for this game."}
    base = {**base, "forecast_id": forecast.forecast_id,
            "shadow_forecast_row_id": forecast.id,
            "forecast_snapshot": f"{forecast.snapshot_type} v{forecast.snapshot_version}"}

    # Eligibility first, deliberately. See the module docstring.
    binding = eligibility.check(
        session, event_id=forecast.event_id, forecast_id=forecast.forecast_id,
        experiment_id=str(era.id), now=moment)
    if not binding["eligible"]:
        return {**base, "outcome": "BLOCKED", "reason": binding["reason"],
                "detail": binding.get("detail"),
                "forecast_id": forecast.forecast_id,
                "eligibility": binding}

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


__all__ = ["MAX_QUOTE_AGE_MINUTES", "PHASE", "WINDOW_CLOSES", "WINDOW_OPENS",
           "evaluate", "existing_decision", "games_at_checkpoint",
           "paired_quotes", "record_outcome"]
