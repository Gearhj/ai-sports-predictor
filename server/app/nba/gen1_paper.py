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
5. A release-bound ``NBA_RECURRING_PAPER_AUTHORIZATION_V1`` grant. It is OPTIONAL
   by contract: without one the producer reports ``NO_PAPER_AUTHORIZATION``,
   writes no companions and continues ordinary evidence delivery, and AISP says
   the same words and records no position. It is pinned by
   ``NBA_PAPER_POLICY_GRANT_PATH`` / ``_SHA256``, must repeat the unchanged
   2026-09-15 terms and the prior policy supplement's own authorization, and
   must be the grant THIS package's slate release binds -- an owner decision,
   never assumed, and never widened from the 2026-09-15 single-game approval.
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
    "ELIGIBILITY_RECORD_INVALID", "NO_PAPER_AUTHORIZATION", "NO_TWO_SIDED_QUOTE", "NO_PERMITTED_PROVIDER",
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

    release = bound_release(forecast)
    grant = policy_grant(settings, now=moment, release=release)
    base["grant"] = {k: v for k, v in grant.items() if k != "document"}
    base["grant"]["release_sha256"] = forecast.slate_release_sha256
    if not grant["valid"]:
        return {**base, "outcome": "BLOCKED", "reason": "NO_PAPER_AUTHORIZATION",
                "detail": ("No release-bound NBA_RECURRING_PAPER_AUTHORIZATION_V1 grant authorises this "
                           f"forecast ({grant['state']}). AISP_NBA_PAPER_POLICY_V1 was authorised on "
                           "2026-09-15 for research-eligibility records only; evidence still imports and "
                           "this decision is recorded, but no paper authority exists. See "
                           "docs/NBA_LAUNCH_PLAN_20260919.md.")}

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


#: The producer's optional, release-bound recurring paper grant. THE ONLY
#: document that can carry paper authority for NBA production packages. Its
#: absence is reported as NO_PAPER_AUTHORIZATION and changes nothing else:
#: evidence still imports, decisions are still recorded, no position forms.
GRANT_SCHEMA = common.PAPER_GRANT_SCHEMA

#: Exactly the scope string the producer validates. A different scope is a
#: different grant, not a wider reading of this one.
GRANT_SCOPE = "ALL_SUPPORTED_2026_27_REGULAR_SEASON_FORECASTS"

#: Flags that must be literally these values, or the grant is not this grant.
GRANT_FLAGS: dict[str, Any] = {
    "approvedBy": "James",
    "consumerVirtualExperimentAuthorized": True,
    "realMoneyAuthorized": False,
    "modelPromotionAuthorized": False,
    "noAispWriteAuthorityForProducer": True,
    "scope": GRANT_SCOPE,
}


def _policy_terms() -> dict[str, Any]:
    """AISP's OWN unchanged 2026-09-15 terms, in the producer's field names.

    The grant must repeat these numbers exactly. A grant that changes one is a
    different policy and is refused rather than adopted.
    """

    return {
        "policyVersion": POLICY.policy_version, "mode": "VIRTUAL_RESEARCH_ONLY",
        "realMoneyAuthorized": False, "realMoneyExecutionAllowed": False,
        "wageringOutsideAispAllowed": False, "modelPromotionClaim": False,
        "consumerVirtualExperimentAuthorized": True,
        "kellyFraction": POLICY.kelly_fraction, "positionCapFraction": POLICY.max_position_percentage,
        "initialPositionCapUsd": POLICY.max_stake, "sameGameCapFraction": POLICY.max_same_game_percentage,
        "dailyCapFraction": POLICY.max_daily_percentage,
        "minimumAdjustedEdgeFraction": POLICY.min_adjusted_edge,
        "marketScope": list(POLICY.markets),
    }


def policy_grant(settings: Any, *, now: datetime, release: dict[str, Any] | None = None) -> dict[str, Any]:
    """The producer's ``NBA_RECURRING_PAPER_AUTHORIZATION_V1``, proved from bytes.

    Optional by design. Without it the owner reports ``NO_PAPER_AUTHORIZATION``,
    writes no companions and continues ordinary evidence delivery -- so AISP
    blocks with the same words and imports everything else exactly as before.

    What is proved when one IS pinned (``NBA_PAPER_POLICY_GRANT_PATH`` /
    ``_SHA256``):

    * the schema, James's approval and every authority flag, literally;
    * the selected model: ``configurationIdentity`` is the joint lattice this
      policy controls and ``pointConfigurationIdentity`` is the Ridge/Elo
      configuration -- the grant names both, and neither may drift;
    * the season and scope strings, unchanged;
    * ``policyAuthorization`` equal to AISP's own unchanged terms AND to the
      prior policy supplement's ``summaries.authorization``, read from the
      grant's physically bound ``priorPolicySupplement``;
    * the approval interval: ``authorizedAtUtc`` in the past, a non-empty
      interval, and ``now`` inside it;
    * the RELEASE BINDING, when a release is supplied: that release's
      ``paperEligibilityAuthorization`` must be this exact grant by SHA-256 and
      byte count. A grant that no release binds authorises nothing here.

    A season-scoped grant still does not widen anything by itself: every
    position also needs the producer's per-forecast
    ``ASP_NBA_PAPER_RESEARCH_ELIGIBILITY_V1`` companion (gate 4) and a current
    discovery lease (gate 2). The 2026-09-15 single-game BOS-DET authorisation is
    never stretched to another game or to a season.
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
    try:
        grant = json.loads(raw)
    except ValueError:
        return {"valid": False, "state": "GRANT_UNREADABLE", "sha256": pin}
    base = {"sha256": pin, "bytes": len(raw), "schema": grant.get("schema"),
            "scope": grant.get("scope"), "season_id": grant.get("seasonId")}

    if grant.get("schema") != GRANT_SCHEMA:
        return {"valid": False, "state": "GRANT_SCHEMA_UNKNOWN", **base}
    if any(grant.get(key) != value for key, value in GRANT_FLAGS.items()):
        return {"valid": False, "state": "GRANT_AUTHORITY_FLAGS_DIFFER", **base}
    if (grant.get("configurationIdentity") != POLICY.controlling_configuration
            or grant.get("pointConfigurationIdentity") != common.CONFIGURATION):
        return {"valid": False, "state": "GRANT_NAMES_A_DIFFERENT_MODEL", **base}
    if grant.get("policyVersion") != POLICY.policy_version or grant.get("seasonId") != POLICY.season_id:
        return {"valid": False, "state": "GRANT_NAMES_A_DIFFERENT_POLICY_OR_SEASON", **base}

    terms = grant.get("policyAuthorization") or {}
    expected = _policy_terms()
    differs = sorted(k for k, v in expected.items() if terms.get(k) != v)
    if differs:
        return {"valid": False, "state": "GRANT_CHANGES_THE_AUTHORISED_TERMS", "fields": differs, **base}

    prior_ref = grant.get("priorPolicySupplement")
    try:
        prior = json.loads(common.bound(prior_ref, "prior policy supplement"))
    except (common.BindingRefused, ValueError, TypeError) as exc:
        return {"valid": False, "state": f"PRIOR_POLICY_SUPPLEMENT_UNVERIFIED:{exc}"[:160], **base}
    if prior.get("artifactVersion") != POLICY.eligibility_contract or prior.get("sport") != POLICY.sport:
        return {"valid": False, "state": "PRIOR_POLICY_SUPPLEMENT_IS_NOT_THIS_CONTRACT", **base}
    if terms != ((prior.get("summaries") or {}).get("authorization")):
        return {"valid": False, "state": "GRANT_TERMS_DIFFER_FROM_THE_PRIOR_SUPPLEMENT", **base}

    authorized = common.ts(grant.get("authorizedAtUtc"))
    start, until = common.ts(grant.get("validFromUtc")), common.ts(grant.get("validUntilUtc"))
    window = {"authorized_at": common.iso(authorized), "valid_from": common.iso(start),
              "valid_until": common.iso(until)}
    if authorized is None or start is None or until is None or until <= start:
        return {"valid": False, "state": "GRANT_INTERVAL_MISSING_OR_EMPTY", **base, **window}
    if authorized > now:
        return {"valid": False, "state": "GRANT_AUTHORIZED_IN_THE_FUTURE", **base, **window}
    if not start <= now < until:
        return {"valid": False, "state": "GRANT_NOT_CURRENT", **base, **window}

    if release is None:
        return {"valid": False, "state": "GRANT_NOT_BOUND_TO_A_VERIFIED_RELEASE", **base, **window}
    bound_ref = release.get("paperEligibilityAuthorization")
    if not isinstance(bound_ref, dict) or str(bound_ref.get("sha256", "")).lower() != pin \
            or int(bound_ref.get("bytes") or -1) != len(raw):
        return {"valid": False, "state": "RELEASE_DOES_NOT_BIND_THIS_GRANT", **base, **window,
                "release_binds": str((bound_ref or {}).get("sha256", "")) if isinstance(bound_ref, dict) else None}
    if str(grant.get("refreshPlanSha256", "")).lower() != \
            str((release.get("refreshPlan") or {}).get("sha256", "")).lower():
        return {"valid": False, "state": "GRANT_NAMES_A_DIFFERENT_REFRESH_PLAN", **base, **window}
    return {"valid": True, "state": "GRANT_VALID", **base, **window,
            "prior_policy_supplement_sha256": str((prior_ref or {}).get("sha256", "")).lower(),
            "note": ("Season-scoped, and still not sufficient on its own: each position also needs the "
                     "producer's per-forecast eligibility companion and a current discovery lease.")}


def bound_release(forecast: Any) -> dict[str, Any] | None:
    """The slate release THIS forecast binds, re-proved from its recorded binding.

    V1 packages bind no slate release, so they can never reach the release-bound
    paper lane -- which is correct: the 2026-09-15 single-game authorisation is
    not widened to them.
    """

    import json

    ref = ((forecast.detail or {}).get("bindings") or {}).get("slateRelease")
    if not isinstance(ref, dict) or not ref.get("sha256"):
        return None
    if forecast.slate_release_sha256 and \
            str(ref["sha256"]).lower() != str(forecast.slate_release_sha256).lower():
        return None
    try:
        return json.loads(common.bound(ref, "slate release"))
    except (common.BindingRefused, ValueError, TypeError):
        return None


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
