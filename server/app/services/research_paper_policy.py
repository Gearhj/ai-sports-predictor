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
from datetime import UTC, datetime, timedelta
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

#: AISP_NHL_PAPER_POLICY_V1 -- DEFINED, NOT AUTHORISED. The same numeric terms as
#: NBA/CFB (no threshold is new), applied to the ONLY NHL market that matches the
#: forecast's frozen final channel: the full-game two-way moneyline INCLUDING
#: overtime and shootout. Deliberately NOT in ``POLICIES``: ``activate_all`` must
#: never open an NHL account. Opening it (``research_paper_activation.activate``
#: with this policy) is the owner's decision, as NBA/CFB were on 2026-09-15.
#: Daily exposure is the owner's shared $100 cross-sport allowance only
#: (``app.nhl.paper`` refuses to decide without it), never an NHL-only pool.
NHL = SportPaperPolicy(
    sport="NHL", label="NHL",
    policy_version="AISP_NHL_PAPER_POLICY_V1",
    staking_version="AISP_QUARTER_KELLY_NHL_V1",
    era_name="ASP NHL RESEARCH PAPER BANKROLL — 2026-27 V1",
    eligibility_contract="ASP_NHL_PAPER_RESEARCH_ELIGIBILITY_V1",
    controlling_configuration="NHL_GEN1_REGULATION_POISSON_1.0.0_FINAL_TWO_WAY_3f7981aa",
    season_id="2026-27",
    controlling_engine="NHL_GEN1_FINAL_TWO_WAY",
    permitted_quote_sources=("THE_ODDS_API",),
    authority={
        "virtual_research_only": True,
        "real_money_execution": False,
        "execution_outside_aisp": False,
        "other_sports_or_policies": False,
        "status": "DEFINED_NOT_AUTHORISED",
        "note": ("No NHL account is opened by code. The Odds API (icehockey_nhl, h2h) is the only permitted "
                 "quote source; its NHL budget lane is zero until the owner funds it."),
    },
)

AWAITING = "AWAITING_PRODUCER_ELIGIBILITY"


# -- prospective quote-source amendments (APPEND-ONLY) ---------------------------
# The policies above are the FROZEN records: ``policy_config`` still reproduces
# the SportsGameOdds-only CFB record the era was opened with, byte for byte. A
# later source is never written back into them. It is appended here as a
# separate, prospective amendment that applies only to decisions made at or
# after its effective clock -- and that clock is not in code: it is the setting
# named by ``effective_setting``, unset (so the amendment is NOT effective)
# until the owner sets it at activation. Nothing here reaches back over a
# decision already made.


@dataclass(frozen=True)
class QuoteSourceAmendment:
    amendment_id: str
    sport: str
    #: The frozen policy and configuration this amendment extends, exactly.
    policy_version: str
    controlling_configuration: str
    added_sources: tuple[str, ...]
    authorized_by: str
    authorized_on: str
    #: Settings attribute holding the effective clock (UTC). Unset = not effective.
    effective_setting: str
    #: The code change that introduced the amendment (branch; parent commit).
    introduced_by: str
    note: str


SOURCE_AMENDMENTS: tuple[QuoteSourceAmendment, ...] = (
    QuoteSourceAmendment(
        amendment_id="AISP_CFB_PAPER_POLICY_V1__SOURCE_AMENDMENT_001__THE_ODDS_API",
        sport="CFB",
        policy_version="AISP_CFB_PAPER_POLICY_V1",
        controlling_configuration="CFB_GEN1_RIDGE_A100_PAIRED_RESIDUAL_V1",
        added_sources=("THE_ODDS_API",),
        authorized_by="project owner",
        authorized_on="2026-09-26",
        effective_setting="cfb_odds_api_source_effective_from",
        introduced_by="claude/cfb-odds-api-source (parent 51a0d2bd508ef4e2a10fe92dcaff0cbf5f8f0361)",
        note=("The Odds API (americanfootball_ncaaf) h2h is a prospective CFB price source beside "
              "SportsGameOdds. Same pairing rule (one provider, one book, one market), same 240-minute "
              "older-leg freshness, same T-30..T-15 window; nothing else in the policy changes."),
    ),
    QuoteSourceAmendment(
                amendment_id="AISP_NBA_PAPER_POLICY_V1__SOURCE_AMENDMENT_001__THE_ODDS_API",
                sport="NBA",
                policy_version="AISP_NBA_PAPER_POLICY_V1",
                controlling_configuration="NBA_GEN1_JOINT_FINAL_PAIRED_OOF_LATTICE_V1",
                added_sources=("THE_ODDS_API",),
                authorized_by="James, direct no-added-cost NBA operational repair directive",
                authorized_on="2026-10-20",
                effective_setting="nba_odds_api_source_effective_from",
                introduced_by="claude/nba-odds-api-lane (parent 29815a8)",
                note=("Owner-authorized no-added-cost NBA operational repair Oct20; effective prospectively only. The Odds API (basketball_nba) h2h as a "
                      "prospective NBA price source beside SportsGameOdds, whose NBA authority is absent in October. "
                      "It has no owner authorization yet; it takes effect only when the owner sets "
                      "NBA_ODDS_API_SOURCE_EFFECTIVE_FROM, and then only for decisions at or after that clock. Until "
                      "then the frozen SportsGameOdds-only record alone governs. Same pairing rule (one provider, "
                      "one book, one market), same 240-minute older-leg freshness, same T-30..T-15 window; nothing "
                      "else in the policy changes."),
            ),
)


def _effective_clock(settings: Any, name: str) -> datetime | None:
    value = getattr(settings, name, None) if settings is not None else None
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None  # an unreadable clock is not an effective one
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def effective_quote_sources(policy: SportPaperPolicy, settings: Any, *, moment: datetime,
                            ) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    """The frozen sources plus every amendment effective at ``moment``; and which amendments applied."""

    moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    sources = list(policy.permitted_quote_sources)
    applied: list[dict[str, Any]] = []
    for amendment in SOURCE_AMENDMENTS:
        if (amendment.sport, amendment.policy_version, amendment.controlling_configuration) != (
                policy.sport, policy.policy_version, policy.controlling_configuration):
            continue
        effective = _effective_clock(settings, amendment.effective_setting)
        if effective is None or moment < effective:
            continue
        sources.extend(s for s in amendment.added_sources if s not in sources)
        applied.append({"amendment_id": amendment.amendment_id, "added_sources": list(amendment.added_sources),
                        "effective_from": effective.isoformat(), "authorized_on": amendment.authorized_on})
    return tuple(sources), applied


# -- prospective staking amendments (APPEND-ONLY) --------------------------------
# Same discipline as the source amendments: the frozen record above keeps its
# 2% daily limit and ``policy_config`` still reproduces it byte for byte. An
# amendment replaces ONLY the daily limit, only for decisions whose clock is at
# or after the effective setting, and that setting is unset (so the amendment is
# NOT effective) until the owner sets it at activation. Per-position caps, the
# same-game cap, Kelly fraction and the edge minimum are never touched here.

#: Daily-exposure bases, both keyed to the game-slate date the frozen rule uses
#: (the kickoff's Eastern calendar date, ``paper_decision.slate_date``). The frozen
#: rule counts OPEN positions only; the owner's flat limit counts every stake
#: entered on that slate date, whatever its status (a settled stake frees nothing).
DAILY_BASIS_OPEN = "OPEN_POSITIONS_ON_SLATE_DATE"
DAILY_BASIS_ENTERED = "ALL_POSITIONS_ENTERED_ON_SLATE_DATE_ANY_STATUS"


@dataclass(frozen=True)
class StakingAmendment:
    amendment_id: str
    sport: str
    policy_version: str
    staking_version: str
    #: The frozen value this amendment replaces (fraction of the bankroll).
    replaces_max_daily_percentage: float
    #: The new daily limit: an ABSOLUTE dollar amount per game-slate date. It
    #: does not scale with the bankroll.
    daily_limit_usd: float
    #: Which stakes count toward it (DAILY_BASIS_*).
    daily_exposure_basis: str
    authorized_by: str
    authorized_on: str
    #: Settings attribute holding the effective clock (UTC). Unset = not effective.
    effective_setting: str
    introduced_by: str
    note: str


STAKING_AMENDMENTS: tuple[StakingAmendment, ...] = (
    StakingAmendment(
        amendment_id="AISP_CFB_PAPER_POLICY_V1__STAKING_AMENDMENT_001__DAILY_LIMIT_USD_50",
        sport="CFB",
        policy_version="AISP_CFB_PAPER_POLICY_V1",
        staking_version="AISP_QUARTER_KELLY_CFB_V1",
        replaces_max_daily_percentage=0.02,
        daily_limit_usd=50.00,
        daily_exposure_basis=DAILY_BASIS_ENTERED,
        authorized_by="project owner",
        authorized_on="2026-09-26",
        effective_setting="cfb_daily_limit_amendment_effective_from",
        introduced_by="claude/cfb-daily-limit-50 (parent 15fc3ef55a5ca45af721c842923a52f5a503ce51)",
        note=("Owner: a flat $50 daily limit for the official CFB paper account, counting all stakes "
              "already entered on the game-slate date (open, settled or void, including positions placed "
              "before the effective clock). Replaces the frozen 2%-of-bankroll daily limit only; the "
              "$5 / 0.5% per-position cap, 1% same-game cap, quarter Kelly, two-point edge minimum, "
              "window and cutoffs are unchanged. Prospective; nothing is backfilled or re-opened."),
    ),
)


def effective_daily_limit(policy: SportPaperPolicy, settings: Any, *, moment: datetime,
                          ) -> tuple[float | None, str, list[dict[str, Any]]]:
    """The daily limit governing a decision at ``moment``.

    Returns ``(daily_limit_usd, exposure_basis, applied)``. ``daily_limit_usd`` is
    None when no amendment is effective: the frozen percentage rule, counted on
    open positions, then governs exactly as before.
    """

    moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    for amendment in STAKING_AMENDMENTS:
        if (amendment.sport, amendment.policy_version, amendment.staking_version) != (
                policy.sport, policy.policy_version, policy.staking_version):
            continue
        if policy.max_daily_percentage != amendment.replaces_max_daily_percentage:
            continue  # not the record this amendment was written against
        effective = _effective_clock(settings, amendment.effective_setting)
        if effective is None or moment < effective:
            continue
        return amendment.daily_limit_usd, amendment.daily_exposure_basis, [{
            "amendment_id": amendment.amendment_id,
            "daily_limit": {"from": f"{amendment.replaces_max_daily_percentage:.0%} of bankroll",
                            "to_usd": amendment.daily_limit_usd},
            "daily_exposure_basis": amendment.daily_exposure_basis,
            "daily_exposure_rule": "all positions entered on the slate date, any status",
            "effective_from": effective.isoformat(), "authorized_on": amendment.authorized_on}]
    return None, DAILY_BASIS_OPEN, []


class EligibilityIndex:
    """Every eligibility record of one policy, read and parsed ONCE, keyed by its bound forecast id.

    For a read model that asks ``eligibility_status`` about many forecasts in one build
    (the College Football board): without it each call re-reads and re-parses every
    record of the contract (hundreds of documents, megabytes of JSON) per forecast. The
    index holds exactly the (record, document) pairs the per-call scan would select, in
    the same order, so the answer is identical. Never used by a decision path.
    """

    def __init__(self, session: Session, policy: SportPaperPolicy) -> None:
        from app.models import ResearchSupplement

        self.policy = policy
        self.key = experiment_key(policy.policy_version, policy.sport, policy.season_id)
        candidates = list(session.scalars(
            select(ResearchSupplement).where(
                ResearchSupplement.kind == KIND,
                ResearchSupplement.artifact_version == policy.eligibility_contract,
            )
        ))
        self.candidates_seen = len(candidates)
        self.by_forecast: dict[Any, list[tuple[Any, dict[str, Any]]]] = {}
        for record in candidates:
            doc = stored_document(record)
            if _at(doc, "summaries.authorization.experimentId") != self.key:
                continue
            self.by_forecast.setdefault(_at(doc, "binding.forecastId"), []).append((record, doc))


def eligibility_status(
    session: Session, policy: SportPaperPolicy, *, forecast_id: str, event_id: str | None,
    index: EligibilityIndex | None = None,
) -> dict[str, Any]:
    """Does a producer-issued eligibility record bind this policy to this forecast?

    Reuses the NFL gate's kind, required bindings and authority checks rather
    than a second implementation, keyed to this sport's contract. Reports what is
    missing in full when the answer is no. ``index`` (read models only) supplies the
    same records already parsed; without it they are read now.
    """

    from app.models import ResearchSupplement

    key = experiment_key(policy.policy_version, policy.sport, policy.season_id)
    if index is not None and index.policy == policy:
        candidates_seen = index.candidates_seen
        bound = list(index.by_forecast.get(forecast_id, []))
    else:
        candidates = list(session.scalars(
            select(ResearchSupplement).where(
                ResearchSupplement.kind == KIND,
                ResearchSupplement.artifact_version == policy.eligibility_contract,
            )
        ))
        candidates_seen = len(candidates)
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
                "candidates_seen": candidates_seen}

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
    daily_limit_usd: float | None = None,
) -> dict[str, Any]:
    """Edge, expected return and a capped quarter-Kelly stake. Zero-tie sports.

    ``daily_limit_usd`` (an effective staking amendment) replaces the policy's
    percentage daily limit with an absolute dollar limit; None keeps the frozen rule.

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
        "daily_cap": max(0.0, (policy.max_daily_percentage * bankroll if daily_limit_usd is None
                               else daily_limit_usd) - daily_exposure),
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


__all__ = ["AWAITING", "CFB", "EligibilityIndex", "NBA", "NHL", "POLICIES", "PROVIDER_RESERVE_FLOOR", "SEASON_ID",
           "DAILY_BASIS_ENTERED", "DAILY_BASIS_OPEN", "SOURCE_AMENDMENTS", "STAKING_AMENDMENTS",
           "STARTING_BANKROLL", "QuoteSourceAmendment", "SportPaperPolicy", "StakingAmendment",
           "effective_daily_limit", "effective_quote_sources",
           "eligibility_status",
           "moneyline_terms", "policy_config"]
