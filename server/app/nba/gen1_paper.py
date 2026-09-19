"""AISP_NBA_PAPER_POLICY_V1 -- the NBA paper checkpoint. Built, wired, and OFF.

THE POLICY IS THE OWNER'S, NOT THIS MODULE'S
--------------------------------------------
Every number comes from ``research_paper_policy.NBA`` -- the terms the owner set
on 2026-09-15 for the NBA $1,000 virtual research account (moneyline only,
quarter Kelly, 0.5% / $5 / 1% / 2% caps, a two-point minimum edge against the
same book's no-vig price, zero tie probability, SportsGameOdds research quotes
only, T-30 to T-15). The stake is ``research_paper_policy.moneyline_terms``.
Nothing here adds, tunes or re-derives a threshold.

THE GATES, IN ORDER
-------------------
1. The game is in its window (T-30..T-15) and still SCHEDULED.
2. A PRODUCTION forecast exists (never a HISTORICAL row, never research), and
   the NEWEST verified discovery lists that exact forecast as currently eligible
   with an unexpired lease. V1 discovery has no lease, so PHI at NYK can never
   pass this gate -- deliberately.
3. The retained-source orientation check did not merely bind from this row
   (``CONFIRMED`` only).
4. The producer's ``ASP_NBA_PAPER_RESEARCH_ELIGIBILITY_V1`` record binds this
   policy to that exact forecast, published before T-30.
5. The owner's extension of the policy to PRODUCTION packages. The 2026-09-15
   authorisation covered research-eligibility records; CFB needed a signed grant
   (2026-09-18) before its policy reached production forecasts. The NBA grant is
   pinned by ``NBA_PAPER_POLICY_GRANT_PATH`` / ``_SHA256`` (empty: blocks with
   ``NO_OWNER_POLICY_EXTENSION``) -- an owner decision, never assumed.
6. A two-sided moneyline from one permitted provider, book and market, no older
   than 240 minutes on its STALER leg.
7. The policy's own edge and expected-return rule.
8. The activation control (``NBA_PAPER_DECISIONS_ENABLED``, default off).

CONTROLLING PROBABILITY (an owner decision is recorded here, not made)
---------------------------------------------------------------------
The policy as authorised names the JOINT-distribution win probability of the
lattice baseline. The producer's production contract later declared Elo the
"controlling winner channel". This checkpoint keeps the policy exactly as
written (joint) and records Elo beside it; reconciling the two is listed as an
owner decision in docs/NBA_LAUNCH_PLAN_20260919.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ExternalIdMapping, Game, GameStatus, League, OfficialAspPosition, PositionResult, PositionStatus,
    ShadowExperiment, ShadowExperimentDecision,
)
from app.nba import gen1_common as common
from app.services.research_paper_policy import NBA as POLICY
from app.services.research_paper_policy import eligibility_status, moneyline_terms

PHASE = "FINAL_PREGAME"
WINDOW_OPENS = POLICY.window_opens
WINDOW_CLOSES = POLICY.window_closes
CONTROLLING_CHANNEL = "JOINT_DISTRIBUTION"

PREREQUISITE_REASONS = frozenset({
    "NO_FORECAST", "DISCOVERY_NOT_CURRENT", "ORIENTATION_NOT_CONFIRMED", "AWAITING_PRODUCER_ELIGIBILITY",
    "ELIGIBILITY_RECORD_INVALID", "NO_OWNER_POLICY_EXTENSION", "NO_TWO_SIDED_QUOTE", "NO_PERMITTED_PROVIDER",
    "QUOTE_TOO_STALE",
})


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


def slate_date(starts_at: datetime) -> date:
    from zoneinfo import ZoneInfo

    return starts_at.astimezone(ZoneInfo("America/New_York")).date()


def american_to_decimal(american: int) -> float:
    return 1 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def experiment(session: Session) -> ShadowExperiment | None:
    return session.scalar(select(ShadowExperiment).where(
        ShadowExperiment.sport == POLICY.sport, ShadowExperiment.decision_policy == POLICY.policy_version,
        ShadowExperiment.is_active.is_(True)))


def games_at_checkpoint(session: Session, *, now: datetime) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Game.id, Game.starts_at, ExternalIdMapping.external_id)
        .join(League, League.id == Game.league_id)
        .join(ExternalIdMapping, (ExternalIdMapping.entity_id == Game.id)
              & (ExternalIdMapping.entity_type == "game") & (ExternalIdMapping.source == common.SOURCE_ESPN_GAME))
        .where(League.code == common.LEAGUE_CODE, Game.status == GameStatus.SCHEDULED,
               Game.starts_at >= now + WINDOW_CLOSES, Game.starts_at <= now + WINDOW_OPENS)
        .order_by(Game.starts_at)).all()
    return [{"game_id": gid, "starts_at": common.aware(starts), "espn_game_id": str(ext)} for gid, starts, ext in rows]


def _exposure(session: Session, era: ShadowExperiment, game_id: Any, day: date) -> tuple[float, float]:
    rows = session.execute(
        select(OfficialAspPosition.stake, ShadowExperimentDecision.game_id, ShadowExperimentDecision.slate_date)
        .join(ShadowExperimentDecision, ShadowExperimentDecision.id == OfficialAspPosition.shadow_decision_id)
        .where(OfficialAspPosition.shadow_experiment_id == era.id,
               OfficialAspPosition.status == PositionStatus.OPEN)).all()
    return (sum(float(s) for s, gid, _ in rows if gid == game_id),
            sum(float(s) for s, _, slate in rows if slate == day))


def _supplement_published(session: Session, record_id: Any) -> datetime | None:
    from app.models import ResearchSupplement

    if not record_id:
        return None
    row = session.get(ResearchSupplement, uuid.UUID(str(record_id)))
    return common.aware(row.published_utc) if row is not None else None


def decide(session: Session, settings: Any, era: ShadowExperiment, game: dict[str, Any],
           moment: datetime) -> dict[str, Any]:
    from app.nba.gen1_production import current_forecast_for_game, present_eligibility
    from app.services.nfl_paper_decision import paired_quotes
    from app.services.shadow_experiment_service import ShadowExperimentService

    base: dict[str, Any] = {"game_id": str(game["game_id"]), "espn_game_id": game["espn_game_id"],
                            "starts_at": game["starts_at"].isoformat(), "evaluated_at": moment.isoformat(),
                            "policy_version": POLICY.policy_version, "controlling_channel": CONTROLLING_CHANNEL}
    forecast = current_forecast_for_game(session, game_id=game["game_id"])
    if forecast is None:
        return {**base, "outcome": "BLOCKED", "reason": "NO_FORECAST",
                "detail": "AISP holds no verified NBA production forecast for this game."}
    base.update({"forecast_id": forecast.forecast_id, "nba_gen1_forecast_id": forecast.id,
                 "matchup": f"{forecast.away_team_code} at {forecast.home_team_code}",
                 "channels": {
                     "JOINT_DISTRIBUTION": {"home": forecast.joint_home_win_probability,
                                            "away": forecast.joint_away_win_probability, "role": "controlling (policy)"},
                     "ELO_WINNER": {"home": forecast.elo_home_win_probability,
                                    "away": forecast.elo_away_win_probability,
                                    "role": "recorded beside; producer's declared winner channel"},
                     "RIDGE_POINTS": {"home": forecast.ridge_home_points, "away": forecast.ridge_away_points,
                                      "role": "points only; never compared with a price"}}})
    lease = present_eligibility(session, forecast, now=moment)
    if not lease["eligible"]:
        return {**base, "outcome": "BLOCKED", "reason": "DISCOVERY_NOT_CURRENT",
                "detail": f"{lease['state']}: {lease['detail']}", "discovery": lease}
    base["discovery"] = lease
    orientation = ((forecast.detail or {}).get("orientation") or {}).get("state")
    if orientation != "CONFIRMED":
        return {**base, "outcome": "BLOCKED", "reason": "ORIENTATION_NOT_CONFIRMED",
                "detail": f"Home/away orientation is {orientation or 'unchecked'}, not CONFIRMED by a retained source."}
    permission = eligibility_status(session, POLICY, forecast_id=forecast.forecast_id,
                                    event_id=forecast.codex_game_id)
    if not permission["eligible"]:
        reason = ("AWAITING_PRODUCER_ELIGIBILITY" if permission["state"] == "AWAITING_PRODUCER_ELIGIBILITY"
                  else "ELIGIBILITY_RECORD_INVALID")
        return {**base, "outcome": "BLOCKED", "reason": reason, "detail": permission.get("detail")
                or f"violations {permission.get('violations')} missing {permission.get('missing')}",
                "eligibility": permission}
    published = _supplement_published(session, permission.get("record_id"))
    if published is None or published >= game["starts_at"] - WINDOW_OPENS:
        return {**base, "outcome": "BLOCKED", "reason": "ELIGIBILITY_RECORD_INVALID",
                "detail": "The producer's eligibility record was not published before T-30."}
    base["eligibility"] = {k: permission.get(k) for k in ("state", "record_id", "contract", "experiment_key")}

    grant = policy_grant(settings, now=moment)
    base["grant"] = {k: v for k, v in grant.items() if k != "document"}
    if not grant["valid"]:
        return {**base, "outcome": "BLOCKED", "reason": "NO_OWNER_POLICY_EXTENSION",
                "detail": ("AISP_NBA_PAPER_POLICY_V1 was authorised on 2026-09-15 for research-eligibility records. "
                           f"Extending it to production packages needs the owner's grant ({grant['state']}); "
                           "see docs/NBA_LAUNCH_PLAN_20260919.md.")}

    pairs = paired_quotes(session, game["game_id"])
    if not pairs:
        return {**base, "outcome": "DECLINED", "reason": "NO_TWO_SIDED_QUOTE",
                "detail": "AISP holds no NBA moneyline pair for this game from one provider, book and market."}
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

    bankroll = ShadowExperimentService(session, settings).current_bankroll(era)
    same_game, same_day = _exposure(session, era, game["game_id"], slate_date(game["starts_at"]))
    candidates = []
    for pair in fresh:
        for side, win, american, other in (
                ("AWAY", forecast.joint_away_win_probability, pair["away_american"], pair["home_american"]),
                ("HOME", forecast.joint_home_win_probability, pair["home_american"], pair["away_american"])):
            terms = moneyline_terms(POLICY, win_probability=float(win), decimal_odds=american_to_decimal(int(american)),
                                    opposite_decimal_odds=american_to_decimal(int(other)), bankroll=bankroll,
                                    same_game_exposure=same_game, daily_exposure=same_day)
            candidates.append({"side": side, "american_odds": int(american), "win_probability": float(win),
                               "channel": CONTROLLING_CHANNEL, "sportsbook": pair["sportsbook"],
                               "source": pair["source"], "market_outcome_id": pair[f"{side.lower()}_outcome_id"],
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
    if not getattr(settings, "nba_paper_decisions_enabled", False):
        return {**base, "outcome": "DECLINED", "reason": "ACTIVATION_CONTROL_OFF",
                "detail": "Every gate passed, but NBA_PAPER_DECISIONS_ENABLED is off; no position is recorded.",
                "selection": best, "bankroll": bankroll}
    return {**base, "outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED", "selection": best, "bankroll": bankroll}


GRANT_SCHEMA = "NBA_VIRTUAL_POLICY_EXTENSION_AUTHORIZATION_V1"


def policy_grant(settings: Any, *, now: datetime) -> dict[str, Any]:
    """The owner's extension of AISP_NBA_PAPER_POLICY_V1 to production packages. Empty = none.

    Mirrors the CFB grant (``CFB_VIRTUAL_POLICY_EXTENSION_AUTHORIZATION_V1``): James's
    approval, this exact policy version, virtual research only, no real money, the
    controlling configuration, and an expiry. Pinned by path and SHA-256.
    """

    import json
    import pathlib

    path = str(getattr(settings, "nba_paper_policy_grant_path", "") or "").strip()
    pin = str(getattr(settings, "nba_paper_policy_grant_sha256", "") or "").strip().lower()
    if not path or not pin:
        return {"valid": False, "state": "NO_GRANT_PINNED"}
    try:
        raw = pathlib.Path(path).read_bytes()
    except OSError:
        return {"valid": False, "state": "GRANT_MISSING"}
    if common.sha256_bytes(raw) != pin:
        return {"valid": False, "state": "GRANT_SHA256_MISMATCH"}
    grant = json.loads(raw)
    terms = grant.get("policyAuthorization") or {}
    if (grant.get("schema") != GRANT_SCHEMA or grant.get("approvedBy") != "James"
            or grant.get("policyVersion") != POLICY.policy_version or grant.get("realMoneyAuthorized") is not False
            or terms.get("mode") != "VIRTUAL_RESEARCH_ONLY"
            or grant.get("modelConfiguration") != POLICY.controlling_configuration):
        return {"valid": False, "state": "GRANT_DOES_NOT_AUTHORIZE_THE_UNCHANGED_VIRTUAL_POLICY", "sha256": pin}
    until = common.ts(grant.get("validUntilUtc"))
    if until is None or now >= until:
        return {"valid": False, "state": "GRANT_EXPIRED_OR_UNBOUNDED", "sha256": pin,
                "valid_until": common.iso(until)}
    return {"valid": True, "state": "GRANT_VALID", "sha256": pin, "valid_until": until.isoformat(),
            "scope": grant.get("scope")}


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
    american = selection.get("american_odds")
    row = ShadowExperimentDecision(
        experiment_id=era.id, game_id=game["game_id"], slate_date=slate_date(game["starts_at"]), phase=PHASE,
        sport="NBA", market="MONEYLINE", period="full_game_including_overtime",
        selection=str(selection.get("side") or "NONE"), sportsbook=selection.get("sportsbook"),
        american_odds=american, decimal_odds=american_to_decimal(int(american)) if american else None,
        market_outcome_id=selection.get("market_outcome_id"), odds_snapshot_id=selection.get("odds_snapshot_id"),
        quote_observed_at=datetime.fromisoformat(selection["observed_at"]) if selection.get("observed_at") else None,
        nba_gen1_forecast_id=decision.get("nba_gen1_forecast_id"), recommended_at=moment,
        model_probability=selection.get("win_probability"),
        expected_value=(selection.get("terms") or {}).get("expected_return_per_dollar"),
        recommendation="QUALIFIED_BET" if qualified else decision["outcome"],
        recommendation_reason=str(decision.get("detail") or decision["reason"]),
        qualification_reasons={"reason": decision["reason"], "outcome": decision["outcome"]},
        source_mode="LIVE", evidence=_jsonable({k: v for k, v in decision.items() if k != "eligibility"}))
    session.add(row)
    session.flush()
    position_id = None
    if qualified and float(selection["terms"]["stake"]) > 0:
        position = OfficialAspPosition(
            shadow_forecast_id=None, market_outcome_id=selection["market_outcome_id"],
            odds_snapshot_id=selection["odds_snapshot_id"], placed_at=moment, stake=float(selection["terms"]["stake"]),
            decimal_odds=american_to_decimal(int(american)), result=PositionResult.PENDING,
            status=PositionStatus.OPEN, strategy=era.staking_policy_version, shadow_experiment_id=era.id,
            shadow_decision_id=row.id,
            bankroll_before_position=round(float(decision.get("bankroll") or era.starting_bankroll), 2),
            recommendation_policy_version=era.recommendation_policy_version,
            staking_policy_version=era.staking_policy_version)
        session.add(position)
        session.flush()
        position_id = position.id
    return _jsonable({**decision, "persisted": "RECORDED", "decision_id": row.id, "position_id": position_id})


def evaluate(session: Session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """The checkpoint. One recorded outcome per game in its window, always with a reason.

    With the shipped defaults no position can form: no grant is pinned (gate 5)
    and ``NBA_PAPER_DECISIONS_ENABLED`` is off (gate 8).
    """

    moment = now or datetime.now(UTC)
    era = experiment(session)
    if era is None:
        return {"records": 0, "reason": "NBA_PAPER_POLICY_IS_NOT_ACTIVE", "decisions": []}
    # Discovery is refreshed FIRST (local files only), so a package the producer
    # published on this tick is admitted before the due games are listed.
    refresh = None
    if getattr(settings, "nba_production_consumer_enabled", False):
        from app.nba.gen1_production import consume

        try:
            result = consume(session, settings, now=moment)
            session.commit()
            refresh = {k: result.get(k) for k in ("status", "imported", "rejected")}
        except Exception as error:  # noqa: BLE001 -- a stale discovery is a block below, never a crash
            session.rollback()
            refresh = {"status": f"FAILED:{type(error).__name__}"}
    due = games_at_checkpoint(session, now=moment)
    if not due:
        return {"records": 0, "reason": "NO_NBA_GAME_AT_ITS_CHECKPOINT", "decisions": [], "experiment_id": str(era.id),
                "activation": bool(getattr(settings, "nba_paper_decisions_enabled", False)),
                "discovery_refresh": refresh}
    decisions = []
    for game in due:
        outcome = record(session, era, game, decide(session, settings, era, game, moment), moment)
        session.commit()
        decisions.append(outcome)
    return _jsonable({"records": sum(1 for d in decisions if d.get("position_id")),
                      "reason": "CHECKPOINT_EVALUATED", "experiment_id": era.id,
                      "policy_version": POLICY.policy_version,
                      "activation": bool(getattr(settings, "nba_paper_decisions_enabled", False)),
                      "discovery_refresh": refresh, "decisions": decisions})


__all__ = ["CONTROLLING_CHANNEL", "PHASE", "WINDOW_CLOSES", "WINDOW_OPENS", "decide", "evaluate",
           "games_at_checkpoint", "record"]
