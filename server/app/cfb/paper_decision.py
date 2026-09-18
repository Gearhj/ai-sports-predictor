"""AISP_CFB_PAPER_POLICY_V1 -- the CFB paper checkpoint, unchanged policy, gated activation.

THE POLICY IS THE OWNER'S, NOT THIS MODULE'S
--------------------------------------------
Every number comes from ``research_paper_policy.CFB`` -- the terms James set on
2026-09-15 (moneyline only, quarter Kelly, 0.5% / $5 / 1% / 2% caps, a two-point
minimum edge against the same book's no-vig price, zero tie probability, no MLB
penalties, SportsGameOdds research quotes only, T-30 to T-15) -- and the stake
is ``research_paper_policy.moneyline_terms``. Nothing here tunes, rounds or
re-derives a threshold.

THE GATES, IN ORDER, AND WHY THAT ORDER
---------------------------------------
1. The game is in its window (T-30..T-15) and still SCHEDULED.
2. A forecast exists, and the NEWEST verified discovery manifest lists that
   exact forecast as currently eligible with an unexpired lease
   (``production_import.present_eligibility``). An expired lease is a block,
   not a stale-but-usable forecast.
3. The producer's per-forecast ``ASP_CFB_PAPER_RESEARCH_ELIGIBILITY_V1`` record
   binds this policy to that forecast.
4. A two-sided moneyline from one permitted provider, book and market, no
   older than 240 minutes on its STALER leg.
5. The policy's own edge and expected-return rule.
6. The activation control (``CFB_PAPER_DECISIONS_ENABLED``). Everything above is
   evaluated and recorded either way; only a position waits on this switch.

Eligibility precedes arithmetic so a missing permission is never "rescued" by a
large number. Every evaluation is written with its reason: a checkpoint that
wrote nothing cannot be told apart from one that never ran.

CONTROLLING PROBABILITY
-----------------------
The joint paired-residual draw probability of the eligible baseline forecast.
Ridge (Development-calibrated) and Elo are recorded beside it for the reader and
never used here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CfbResearchForecast, ExternalIdMapping, Game, GameStatus, League, OfficialAspPosition, PositionResult,
    PositionStatus, Season, ShadowExperiment, ShadowExperimentDecision,
)
from app.services.research_paper_policy import CFB as POLICY
from app.services.research_paper_policy import eligibility_status, moneyline_terms

PHASE = "FINAL_PREGAME"
WINDOW_OPENS = POLICY.window_opens
WINDOW_CLOSES = POLICY.window_closes
CONTROLLING_CHANNEL = "JOINT_PAIRED_RESIDUAL_BASELINE"

#: Evaluations that end on a missing prerequisite. A later tick inside the same
#: window may record a new decision if the prerequisite has since arrived.
PREREQUISITE_REASONS = frozenset({
    "NO_FORECAST", "DISCOVERY_NOT_CURRENT", "AWAITING_PRODUCER_ELIGIBILITY", "ELIGIBILITY_RECORD_INVALID",
    "NO_TWO_SIDED_QUOTE", "NO_PERMITTED_PROVIDER", "QUOTE_TOO_STALE",
})


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def slate_date(game: dict[str, Any]) -> date:
    """The game's Eastern calendar date -- the day Daily Decisions files it under.

    A Saturday 8 PM ET kickoff is Sunday in UTC; filing its decision by UTC
    would move it to the wrong day's board and the wrong day's exposure cap.
    """

    from zoneinfo import ZoneInfo

    return game["starts_at"].astimezone(ZoneInfo("America/New_York")).date()


def american_to_decimal(american: int) -> float:
    return 1 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def experiment(session: Session) -> ShadowExperiment | None:
    return session.scalar(select(ShadowExperiment).where(
        ShadowExperiment.sport == POLICY.sport, ShadowExperiment.decision_policy == POLICY.policy_version,
        ShadowExperiment.is_active.is_(True)))


def games_at_checkpoint(session: Session, *, now: datetime) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Game.id, Game.starts_at, ExternalIdMapping.external_id)
        .join(Season, Season.id == Game.season_id).join(League, League.id == Season.league_id)
        .join(ExternalIdMapping, (ExternalIdMapping.entity_id == Game.id)
              & (ExternalIdMapping.entity_type == "game") & (ExternalIdMapping.source == "CFBD"))
        .where(League.code == "CFB", Game.status == GameStatus.SCHEDULED,
               Game.starts_at >= now + WINDOW_CLOSES, Game.starts_at <= now + WINDOW_OPENS)
        .order_by(Game.starts_at)).all()
    return [{"game_id": gid, "starts_at": _aware(starts), "source_game_id": str(ext)} for gid, starts, ext in rows]


def _bankroll(session: Session, settings: Any, era: ShadowExperiment) -> float:
    from app.services.shadow_experiment_service import ShadowExperimentService

    return ShadowExperimentService(session, settings).current_bankroll(era)


def _exposure(session: Session, era: ShadowExperiment, game_id: Any, day: date) -> tuple[float, float]:
    rows = session.execute(
        select(OfficialAspPosition.stake, ShadowExperimentDecision.game_id, ShadowExperimentDecision.slate_date)
        .join(ShadowExperimentDecision, ShadowExperimentDecision.id == OfficialAspPosition.shadow_decision_id)
        .where(OfficialAspPosition.shadow_experiment_id == era.id,
               OfficialAspPosition.status == PositionStatus.OPEN)).all()
    return (sum(float(s) for s, gid, _ in rows if gid == game_id),
            sum(float(s) for s, _, slate in rows if slate == day))


def _grant(settings: Any) -> dict[str, Any]:
    """The pinned handoff's virtual-policy grant: its SHA and expiry (empty when not pinned)."""

    from app.cfb.production_discovery import production_status

    try:
        detail = production_status(settings).detail or {}
    except Exception:  # noqa: BLE001 -- an unreadable pin is treated as no grant
        return {"sha256": None, "valid_until": None, "state": "UNREADABLE"}
    until = detail.get("grant_valid_until_utc")
    return {"sha256": detail.get("grant_sha256"),
            "valid_until": datetime.fromisoformat(str(until).replace("Z", "+00:00")) if until else None}


def _supplement_published(session: Session, record_id: Any) -> datetime | None:
    from app.models import ResearchSupplement

    if not record_id:
        return None
    row = session.get(ResearchSupplement, uuid.UUID(str(record_id)))
    return _aware(row.published_utc) if row is not None else None


def decide(session: Session, settings: Any, era: ShadowExperiment, game: dict[str, Any], moment: datetime) -> dict[str, Any]:
    from app.cfb.production_import import current_forecast_for_game, present_eligibility
    from app.services.nfl_paper_decision import paired_quotes

    base: dict[str, Any] = {"game_id": str(game["game_id"]), "source_game_id": game["source_game_id"],
                            "starts_at": game["starts_at"].isoformat(), "evaluated_at": moment.isoformat(),
                            "policy_version": POLICY.policy_version, "controlling_channel": CONTROLLING_CHANNEL}
    forecast = current_forecast_for_game(session, game["source_game_id"])
    if forecast is None:
        return {**base, "outcome": "BLOCKED", "reason": "NO_FORECAST",
                "detail": "AISP holds no verified CFB forecast for this game."}
    base.update({"forecast_id": forecast.forecast_id, "cfb_research_forecast_id": forecast.id,
                 "matchup": f"{forecast.away_team_name} at {forecast.home_team_name}",
                 "channels": {"joint": {"home": forecast.joint_home_win_probability,
                                        "away": forecast.joint_away_win_probability},
                              "ridge_development_calibrated": {"home": forecast.ridge_home_win_probability,
                                                               "away": forecast.ridge_away_win_probability},
                              "elo_diagnostic": {"home": forecast.elo_home_win_probability,
                                                 "away": forecast.elo_away_win_probability}}})

    lease = present_eligibility(session, forecast, now=moment)
    if not lease["eligible"]:
        return {**base, "outcome": "BLOCKED", "reason": "DISCOVERY_NOT_CURRENT",
                "detail": f"{lease['state']}: {lease['detail']}", "discovery": lease}
    base["discovery"] = lease

    permission = eligibility_status(session, POLICY, forecast_id=forecast.forecast_id,
                                    event_id=f"cfbd:{forecast.source_game_id}")
    if not permission["eligible"]:
        reason = ("AWAITING_PRODUCER_ELIGIBILITY" if permission["state"] == "AWAITING_PRODUCER_ELIGIBILITY"
                  else "ELIGIBILITY_RECORD_INVALID")
        return {**base, "outcome": "BLOCKED", "reason": reason, "detail": permission.get("detail")
                or f"violations {permission.get('violations')} missing {permission.get('missing')}",
                "eligibility": permission}
    base["eligibility"] = {k: permission.get(k) for k in ("state", "record_id", "contract", "experiment_key")}

    # The owner's 2026-09-18 grant extends the unchanged policy only to
    # supplements published before the game's T-30, and only until it expires.
    grant = _grant(settings)
    base["grant"] = grant
    if grant.get("valid_until") and moment >= grant["valid_until"]:
        return {**base, "outcome": "BLOCKED", "reason": "POLICY_GRANT_EXPIRED",
                "detail": f"The virtual-policy grant expired at {grant['valid_until'].isoformat()}."}
    published = _supplement_published(session, permission.get("record_id"))
    if published is None or published >= game["starts_at"] - WINDOW_OPENS:
        return {**base, "outcome": "BLOCKED", "reason": "ELIGIBILITY_RECORD_INVALID",
                "detail": ("The producer's eligibility record was not published before T-30 "
                           f"({published.isoformat() if published else 'no publication time'}).")}

    pairs = paired_quotes(session, game["game_id"])
    if not pairs:
        return {**base, "outcome": "DECLINED", "reason": "NO_TWO_SIDED_QUOTE",
                "detail": ("AISP holds no moneyline pair for this game from one provider, book and market. "
                           "No governed CFB price was collected; a reference line is never a price.")}
    permitted = [p for p in pairs if p["source"] in POLICY.permitted_quote_sources]
    if not permitted:
        return {**base, "outcome": "DECLINED", "reason": "NO_PERMITTED_PROVIDER",
                "detail": f"Stored pairs come only from {sorted({p['source'] for p in pairs})}."}
    fresh, stale = [], []
    for pair in permitted:
        age = (moment - datetime.fromisoformat(pair["pair_observed_at"])).total_seconds() / 60
        (fresh if age <= POLICY.max_quote_age_minutes else stale).append({**pair, "pair_age_minutes": round(age, 1)})
    if not fresh:
        return {**base, "outcome": "DECLINED", "reason": "QUOTE_TOO_STALE",
                "detail": f"No permitted pair is within {POLICY.max_quote_age_minutes} minutes on its older leg.",
                "pairs_considered": stale}

    bankroll = _bankroll(session, settings, era)
    same_game, same_day = _exposure(session, era, game["game_id"], slate_date(game))
    candidates = []
    for pair in fresh:
        for side, win, american, other in (
                ("AWAY", forecast.joint_away_win_probability, pair["away_american"], pair["home_american"]),
                ("HOME", forecast.joint_home_win_probability, pair["home_american"], pair["away_american"])):
            terms = moneyline_terms(POLICY, win_probability=float(win), decimal_odds=american_to_decimal(int(american)),
                                    opposite_decimal_odds=american_to_decimal(int(other)), bankroll=bankroll,
                                    same_game_exposure=same_game, daily_exposure=same_day)
            candidates.append({"side": side, "american_odds": int(american), "win_probability": float(win),
                               "sportsbook": pair["sportsbook"], "source": pair["source"],
                               "market_outcome_id": pair[f"{side.lower()}_outcome_id"],
                               "odds_snapshot_id": pair[f"{side.lower()}_snapshot_id"],
                               "observed_at": pair[f"{side.lower()}_observed_at"],
                               "pair_observed_at": pair["pair_observed_at"], "older_leg": pair["older_leg"],
                               "pair_age_minutes": pair["pair_age_minutes"], "terms": terms})
    qualifying = [c for c in candidates if c["terms"]["qualifies"]]
    best = max(qualifying or candidates, key=lambda c: c["terms"]["expected_return_per_dollar"])
    if not qualifying:
        return {**base, "outcome": "DECLINED", "reason": "NO_QUALIFYING_EDGE",
                "detail": f"Best side {best['side']} at {best['sportsbook']}: {best['terms']['reason']}.",
                "best": best, "bankroll": bankroll}
    if not getattr(settings, "cfb_paper_decisions_enabled", False):
        return {**base, "outcome": "DECLINED", "reason": "ACTIVATION_CONTROL_OFF",
                "detail": "Every gate passed, but CFB_PAPER_DECISIONS_ENABLED is off; no position is recorded.",
                "selection": best, "bankroll": bankroll}
    return {**base, "outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED", "selection": best, "bankroll": bankroll}


def record(session: Session, era: ShadowExperiment, game: dict[str, Any], decision: dict[str, Any],
           moment: datetime) -> dict[str, Any]:
    prior = session.scalar(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.experiment_id == era.id, ShadowExperimentDecision.game_id == game["game_id"],
        ShadowExperimentDecision.phase == PHASE).order_by(ShadowExperimentDecision.recommended_at.desc()).limit(1))
    if prior is not None:
        position = session.scalar(select(OfficialAspPosition).where(
            OfficialAspPosition.shadow_decision_id == prior.id))
        prior_reason = (prior.qualification_reasons or {}).get("reason")
        if position is not None or prior_reason == decision["reason"] or prior_reason not in PREREQUISITE_REASONS:
            return _jsonable({**decision, "persisted": "REUSED_EXISTING_DECISION", "decision_id": prior.id,
                              "position_id": position.id if position else None})
    selection = decision.get("selection") or decision.get("best") or {}
    qualified = decision["outcome"] == "QUALIFIED"
    row = ShadowExperimentDecision(
        experiment_id=era.id, game_id=game["game_id"], slate_date=slate_date(game), phase=PHASE, sport="CFB",
        market="MONEYLINE", period="full_game_including_overtime",
        selection=str(selection.get("side") or "NONE"), sportsbook=selection.get("sportsbook"),
        american_odds=selection.get("american_odds"),
        decimal_odds=american_to_decimal(int(selection["american_odds"])) if selection.get("american_odds") else None,
        market_outcome_id=selection.get("market_outcome_id"), odds_snapshot_id=selection.get("odds_snapshot_id"),
        quote_observed_at=datetime.fromisoformat(selection["observed_at"]) if selection.get("observed_at") else None,
        cfb_research_forecast_id=decision.get("cfb_research_forecast_id"), recommended_at=moment,
        model_probability=selection.get("win_probability"),
        expected_value=(selection.get("terms") or {}).get("expected_return_per_dollar"),
        recommendation="QUALIFIED_BET" if qualified else decision["outcome"],
        recommendation_reason=str(decision.get("detail") or decision["reason"]),
        qualification_reasons={"reason": decision["reason"], "outcome": decision["outcome"]},
        source_mode="LIVE", evidence=_jsonable({k: v for k, v in decision.items() if k != "eligibility"}))
    session.add(row)
    session.flush()
    position_id = None
    if qualified:
        stake = float(selection["terms"]["stake"])
        if stake > 0:
            position = OfficialAspPosition(
                shadow_forecast_id=None, market_outcome_id=selection["market_outcome_id"],
                odds_snapshot_id=selection["odds_snapshot_id"], placed_at=moment, stake=stake,
                decimal_odds=american_to_decimal(int(selection["american_odds"])),
                result=PositionResult.PENDING, status=PositionStatus.OPEN, strategy=era.staking_policy_version,
                shadow_experiment_id=era.id, shadow_decision_id=row.id,
                bankroll_before_position=round(float(decision.get("bankroll") or era.starting_bankroll), 2),
                recommendation_policy_version=era.recommendation_policy_version,
                staking_policy_version=era.staking_policy_version)
            session.add(position)
            session.flush()
            position_id = position.id
    return _jsonable({**decision, "persisted": "RECORDED", "decision_id": row.id, "position_id": position_id})


def evaluate(session: Session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """The checkpoint. One recorded outcome per game in its window, always with a reason."""

    moment = now or datetime.now(UTC)
    era = experiment(session)
    if era is None:
        return {"records": 0, "reason": "CFB_PAPER_POLICY_IS_NOT_ACTIVE", "decisions": []}
    due = games_at_checkpoint(session, now=moment)
    if not due:
        return {"records": 0, "reason": "NO_CFB_GAME_AT_ITS_CHECKPOINT", "decisions": [],
                "experiment_id": str(era.id), "activation": bool(getattr(settings, "cfb_paper_decisions_enabled", False))}
    discovery = None
    if getattr(settings, "cfb_production_consumer_enabled", False):
        from app.cfb.production_import import consume_discovery

        try:
            discovery = consume_discovery(session, settings, now=moment)
            session.commit()
            discovery = {k: discovery.get(k) for k in ("status", "manifest_sha256", "currently_eligible", "imported")}
        except Exception as error:  # noqa: BLE001 -- a stale manifest is a block below, never a crash
            session.rollback()
            discovery = {"status": f"FAILED:{type(error).__name__}"}
    decisions = []
    for game in due:
        outcome = record(session, era, game, decide(session, settings, era, game, moment), moment)
        session.commit()
        decisions.append(outcome)
    return _jsonable({"records": sum(1 for d in decisions if d.get("position_id")), "reason": "CHECKPOINT_EVALUATED",
                      "experiment_id": era.id, "policy_version": POLICY.policy_version,
                      "activation": bool(getattr(settings, "cfb_paper_decisions_enabled", False)),
                      "discovery_refresh": discovery, "decisions": decisions})


__all__ = ["PHASE", "WINDOW_CLOSES", "WINDOW_OPENS", "decide", "evaluate", "games_at_checkpoint", "record"]
