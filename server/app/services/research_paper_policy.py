"""AISP_NBA_PAPER_POLICY_V1 and AISP_CFB_PAPER_POLICY_V1 -- virtual research only.

WHAT THESE ARE
--------------
Two paper policies authorised by the project owner on 2026-09-15, each with its
own $1,000 virtual season account. Moneyline only, quarter Kelly, the same caps
as the NFL paper policy, and nothing that could reach a real sportsbook.

WHAT THEY ARE NOT
-----------------
Active on their own say-so. A policy may be registered, funded and wired end to
end and still decline every game, because each position needs the producer's
own PAPER_RESEARCH_ELIGIBILITY record binding this policy to the exact forecast.
Until Codex publishes that record, every evaluation returns
AWAITING_PRODUCER_ELIGIBILITY and names what is missing. Nothing here writes
eligibility, and nothing here can: the record is content-addressed producer
evidence, and minting it would be fabricated provenance.

WHAT IS DELIBERATELY ABSENT
---------------------------
MLB's starting-pitcher and lineup penalties, NFL's tie handling (both sports
here have zero final-tie probability), and any injury, lineup, quarterback or
player adjustment. None has evidence behind it in either sport, and inventing an
adjustment is worse than having none.

WHICH PROBABILITY
-----------------
The joint-distribution win probability of the producer's eligible BASELINE
forecast, and only that. Never a close-score challenger, never Elo, never a
probability reverse-engineered from expected points. Ridge and Elo remain on the
research card as separately labelled outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.nfl_paper_eligibility import (
    KIND,
    MUST_REMAIN_FALSE,
    MUST_REMAIN_TRUE,
    REQUIRED_BINDINGS,
    _at,
    experiment_key,
    stored_document,
)

SEASON_ID = "2026"
STARTING_BANKROLL = 1000.0

#: The provider reserve every paper lane must leave untouched.
PROVIDER_RESERVE_FLOOR = 300


@dataclass(frozen=True)
class SportPaperPolicy:
    sport: str
    label: str
    policy_version: str
    staking_version: str
    era_name: str
    eligibility_contract: str
    #: The controlling configuration the eligibility record must bind.
    controlling_configuration: str
    #: The program's own season identifier. NBA crosses New Year, so the
    #: season-calendar rule names it "2026-27", not "2026".
    season_id: str = "2026"
    #: shadow_experiments.controlling_engine is varchar(40); the full
    #: configuration identity lives in policy_config and the eligibility check.
    controlling_engine: str = ""
    kelly_fraction: float = 0.25
    max_position_percentage: float = 0.005
    max_stake: float = 5.00
    max_same_game_percentage: float = 0.01
    max_daily_percentage: float = 0.02
    min_adjusted_edge: float = 0.02
    markets: tuple[str, ...] = ("MONEYLINE",)
    window_opens: timedelta = timedelta(minutes=30)
    window_closes: timedelta = timedelta(minutes=15)
    max_quote_age_minutes: int = 240
    permitted_quote_sources: tuple[str, ...] = ("SPORTSGAMEODDS",)
    authority: dict[str, Any] = field(default_factory=lambda: {
        "virtual_research_only": True,
        "real_money_execution": False,
        "execution_outside_aisp": False,
        "sportsgameodds_governed_provider": False,
        "sportsgameodds_note": (
            "SportsGameOdds research-provider quotes are permitted only inside "
            "this virtual policy. That does not make SportsGameOdds a governed "
            "provider for anything else."),
        "other_sports_or_policies": False,
    })


NBA = SportPaperPolicy(
    sport="NBA", label="NBA",
    policy_version="AISP_NBA_PAPER_POLICY_V1",
    staking_version="AISP_QUARTER_KELLY_NBA_V1",
    era_name="ASP NBA RESEARCH PAPER BANKROLL — 2026 V1",
    eligibility_contract="ASP_NBA_PAPER_RESEARCH_ELIGIBILITY_V1",
    controlling_configuration="NBA_GEN1_JOINT_FINAL_PAIRED_OOF_LATTICE_V1",
    season_id="2026-27",
    controlling_engine="NBA_GEN1_JOINT_BASELINE",
)

CFB = SportPaperPolicy(
    sport="CFB", label="College Football",
    policy_version="AISP_CFB_PAPER_POLICY_V1",
    staking_version="AISP_QUARTER_KELLY_CFB_V1",
    era_name="ASP CFB RESEARCH PAPER BANKROLL — 2026 V1",
    eligibility_contract="ASP_CFB_PAPER_RESEARCH_ELIGIBILITY_V1",
    controlling_configuration="CFB_GEN1_RIDGE_A100_PAIRED_RESIDUAL_V1",
    controlling_engine="CFB_GEN1_JOINT_BASELINE",
)

POLICIES: dict[str, SportPaperPolicy] = {"NBA": NBA, "CFB": CFB}

AWAITING = "AWAITING_PRODUCER_ELIGIBILITY"


def eligibility_status(
    session: Session, policy: SportPaperPolicy, *, forecast_id: str, event_id: str | None,
) -> dict[str, Any]:
    """Does a producer-issued eligibility record bind this policy to this forecast?

    Reuses the NFL gate's kind, required bindings and authority checks rather
    than a second implementation, keyed to this sport's contract. Reports what is
    missing in full when the answer is no.
    """

    from app.models import ResearchSupplement

    key = experiment_key(policy.policy_version, policy.sport, policy.season_id)
    candidates = list(session.scalars(
        select(ResearchSupplement).where(
            ResearchSupplement.kind == KIND,
            ResearchSupplement.artifact_version == policy.eligibility_contract,
        )
    ))
    bound = []
    for record in candidates:
        doc = stored_document(record)
        if _at(doc, "binding.forecastId") != forecast_id:
            continue
        if _at(doc, "summaries.authorization.experimentId") != key:
            continue
        bound.append((record, doc))

    base = {
        "policy_version": policy.policy_version,
        "contract": policy.eligibility_contract,
        "kind": KIND,
        "experiment_key": key,
        "forecast_id": forecast_id,
        "event_id": event_id,
    }
    if not bound:
        return {**base, "eligible": False, "state": AWAITING,
                "detail": (
                    f"No {policy.eligibility_contract} record binds {key} to forecast "
                    f"{forecast_id}. Codex publishes this evidence; AISP does not "
                    "create it. The policy is funded and wired, and declines every "
                    "game until the record is imported."),
                "required_bindings": list(REQUIRED_BINDINGS),
                "candidates_seen": len(candidates)}

    record, doc = bound[0]
    missing = [path for path in REQUIRED_BINDINGS if _at(doc, path) in (None, "")]
    violations = [path for path in MUST_REMAIN_FALSE if _at(doc, path) is not False]
    violations += [path for path in MUST_REMAIN_TRUE if _at(doc, path) is not True]
    configuration = _at(doc, "binding.configurationIdentity") or _at(
        doc, "summaries.frozenConfiguration.configurationIdentity")
    if configuration and configuration != policy.controlling_configuration:
        violations.append(
            f"binding names configuration {configuration}, not the controlling "
            f"{policy.controlling_configuration}")
    eligible = not missing and not violations
    return {**base, "eligible": eligible,
            "state": "ELIGIBLE" if eligible else "ELIGIBILITY_RECORD_INVALID",
            "record_id": str(record.id), "missing": missing, "violations": violations,
            "frozen_probability": {
                "home": _at(doc, "summaries.frozenConfiguration.homeWinProbability"),
                "away": _at(doc, "summaries.frozenConfiguration.awayWinProbability"),
                "tie": _at(doc, "summaries.frozenConfiguration.tieProbability"),
            }}


def moneyline_terms(
    policy: SportPaperPolicy, *, win_probability: float, decimal_odds: float,
    opposite_decimal_odds: float, bankroll: float,
    same_game_exposure: float = 0.0, daily_exposure: float = 0.0,
) -> dict[str, Any]:
    """Edge, expected return and a capped quarter-Kelly stake. Zero-tie sports.

    Edge is the model probability against the NO-VIG probability of the same
    side, from the two legs of one book's pair -- not against the raw implied
    probability, which would count the vig as disagreement.
    """

    implied = 1.0 / decimal_odds
    implied_other = 1.0 / opposite_decimal_odds
    no_vig = implied / (implied + implied_other)
    edge = win_probability - no_vig
    profit = decimal_odds - 1.0
    expected_return = win_probability * profit - (1.0 - win_probability)
    full_kelly = expected_return / profit if profit > 0 else 0.0
    quarter = max(0.0, full_kelly * policy.kelly_fraction)

    limits = {
        "quarter_kelly": quarter * bankroll,
        "position_cap": policy.max_position_percentage * bankroll,
        "max_stake": policy.max_stake,
        "same_game_cap": max(0.0, policy.max_same_game_percentage * bankroll - same_game_exposure),
        "daily_cap": max(0.0, policy.max_daily_percentage * bankroll - daily_exposure),
    }
    qualifies = edge >= policy.min_adjusted_edge and expected_return > 0
    stake = round(min(limits.values()), 2) if qualifies else 0.0
    return {
        "no_vig_probability": no_vig,
        "edge": edge,
        "expected_return_per_dollar": expected_return,
        "full_kelly_fraction": full_kelly,
        "quarter_kelly_fraction": quarter,
        "limits": limits,
        "bound_by": min(limits, key=limits.get) if qualifies else None,
        "qualifies": qualifies and stake > 0,
        "stake": stake,
        "reason": (
            "QUALIFIES" if qualifies and stake > 0 else
            f"edge {edge:+.4f} below the {policy.min_adjusted_edge:.2f} minimum"
            if edge < policy.min_adjusted_edge else
            "expected return is not positive" if expected_return <= 0 else
            "every cap resolved to zero"),
    }


def policy_config(policy: SportPaperPolicy, *, code_commit: str, code_identity: str) -> dict[str, Any]:
    return {
        "policy_version": policy.policy_version,
        "staking_version": policy.staking_version,
        "sport": policy.sport,
        "markets": list(policy.markets),
        "kelly_fraction": policy.kelly_fraction,
        "max_position_percentage": policy.max_position_percentage,
        "max_stake": policy.max_stake,
        "max_same_game_percentage": policy.max_same_game_percentage,
        "max_daily_percentage": policy.max_daily_percentage,
        "min_adjusted_edge": policy.min_adjusted_edge,
        "tie_probability": 0.0,
        "tie_note": "Final-game tie probability is zero in this sport; no tie rule is applied.",
        "inherited_mlb_adjustments": None,
        "no_pitcher_penalty": True,
        "no_lineup_penalty": True,
        "no_injury_or_player_adjustment": True,
        "controlling_engine": policy.controlling_engine,
        "controlling_configuration": policy.controlling_configuration,
        "controlling_probability": (
            f"Joint-distribution win probability of the eligible "
            f"{policy.controlling_configuration} baseline forecast"),
        "totals": "Illustrative only; not governed and never wagered.",
        "timing": {"final_pregame_window": "30 to 15 minutes before start",
                   "quote_freshness_limit_minutes": policy.max_quote_age_minutes},
        "quotes": {"permitted_sources": list(policy.permitted_quote_sources),
                   "pairing": "same book, same provider, same market, older leg governs",
                   "provider_reserve_floor": PROVIDER_RESERVE_FLOOR},
        "eligibility": {"required": True, "kind": KIND,
                        "contract": policy.eligibility_contract},
        "authority": policy.authority,
        "approval": {"authority": "project owner", "granted": "2026-09-15",
                     "scope": f"{policy.label} 2026 $1,000 virtual research account and paper policy"},
        "code_commit": code_commit,
        "code_identity": code_identity,
    }


__all__ = ["AWAITING", "CFB", "NBA", "POLICIES", "PROVIDER_RESERVE_FLOOR", "SEASON_ID",
           "STARTING_BANKROLL", "SportPaperPolicy", "eligibility_status",
           "moneyline_terms", "policy_config"]
