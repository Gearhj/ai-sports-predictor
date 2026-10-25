"""Wager Results: what the OFFICIAL FUNDED POSITIONS actually returned.

The Model Performance page's accuracy tabs score forecasts. This read model is
a different population: every row of ``official_asp_positions`` whose
experiment is a FUNDED_ERA member of a sport account -- the same rows the
command-center experiment ledger counts -- joined to its exact governing
decision (``official_asp_positions.shadow_decision_id``) and that decision's
recorded evidence.

WHAT IS READ, NEVER RECOMPUTED
------------------------------
* Recorded policy-adjusted return (``edge_metrics.py`` field
  ``recorded_policy_adjusted_return``): expected profit per $1 at the entry
  decimal odds, as the funding decision stored it.
    - MLB V2.1: ``evidence.adjusted_expected_value`` (p_adj * d - 1)
    - CFB:      ``evidence.selection.terms.expected_return_per_dollar`` (p * d - 1)
    - NFL:      ``evidence.selection.expected_value`` (tie-aware, ties refunded)
  A decision that did not record it is placed in the ``missing`` band; it is
  never reconstructed.
* Original (raw) selected-side probability: MLB ``evidence.raw_model_probability``;
  CFB/NFL ``evidence.selection.win_probability``. MLB's policy-adjusted
  probability (``evidence.adjusted_probability``) is reported separately.
* Net profit: ``position_results.profit_loss`` (= payout - stake).

EXCLUSIONS (by construction)
----------------------------
* House Divided copies live in ``competition_wagers``; that table is never read.
* Repeated checkpoint decisions (MORNING / LINEUP / other FINAL rows for the
  same game) are never read: each position joins only its one funding decision.
* Unfunded shadows: experiments that are not a FUNDED_ERA member hold no
  counted positions here (a count of any such position is reported).

BINDING
-------
The joined decision must provably govern the position: same experiment, same
game, same market, and a decision side resolved independently of the position
(evidence side, decision selection via team aliases, the decision's own
outcome) that equals the position's outcome side. Otherwise the row keeps its
position, settlement and identities, carries ``binding_status`` (CONFLICT_* /
UNRESOLVED_*), and contributes no estimate, probability or return.

PREDICTED VS OBSERVED
---------------------
Observed win rate is wins / (wins + losses). The matching prediction is the
win probability given a decided result: the raw p where the market cannot tie
(MLB, CFB), and the derived pWin / (1 - pTie) for NFL. An NFL row without a
recorded tie probability is flagged and left out of that comparison.

Settlement follows the experiment ledger exactly: a position is settled when
it has a ``position_results`` row with a profit and a settled time. Pushes and
voids return the stake: they count in W-L-P/V and in settled stake, and are
left out of the win-rate denominator and of expected wins.

Read-only: no provider call, no model execution, no write.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models.entities import (
    Game,
    GameTeam,
    Market,
    MarketOutcome,
    OddsSnapshot,
    OfficialAspPosition,
    PositionSettlement,
    ShadowExperiment,
    ShadowExperimentDecision,
    Sportsbook,
    Team,
)
from app.models.experiment_accounts import ExperimentAccount, ExperimentAccountMembership, MembershipRole

SCHEMA = "AISP_WAGER_RESULTS_V1"
ET = ZoneInfo("America/New_York")
SPORTS = ("MLB", "NFL", "NBA", "CFB", "NHL")
#: Research-only sports: listed so the page can say "no official positions exist", never a silent gap.
#: Empty since 2026-10-16: NHL's paper era (AISP_NHL_PAPER_POLICY_V1) holds funded positions like the
#: other four accounts, so its rows are read here exactly as theirs are.
RESEARCH_ONLY_SPORTS: tuple[str, ...] = ()
SPORT_LABELS = {"MLB": "MLB", "NFL": "NFL", "NBA": "NBA", "CFB": "College Football", "NHL": "NHL"}
ENGINE_LABELS = {
    "MLB_V18_WHOLE_GAME": "V18 engine",
    "AISP_BASELINE": "AISP baseline engine",
    "NFL_M1_XGBOOST": "NFL M1 engine",
    "CFB_GEN1_JOINT_BASELINE": "CFB Gen1 joint baseline",
    "NBA_GEN1_JOINT_BASELINE": "NBA Gen1 joint baseline",
    "NHL_GEN1_FINAL_TWO_WAY": "NHL Gen1 final two-way (OT/SO included)",
}

RETURN_BANDS: tuple[tuple[str, str, float | None, float | None], ...] = (
    ("lt0", "<0%", None, 0.0),
    ("0to5", "0–<5%", 0.0, 0.05),
    ("5to10", "5–<10%", 0.05, 0.10),
    ("10to20", "10–<20%", 0.10, 0.20),
    ("ge20", "≥20%", 0.20, None),
)
PROBABILITY_BANDS: tuple[tuple[str, str, float | None, float | None], ...] = (
    ("lt40", "<40%", None, 0.40),
    ("40to50", "40–<50%", 0.40, 0.50),
    ("50to60", "50–<60%", 0.50, 0.60),
    ("60to70", "60–<70%", 0.60, 0.70),
    ("ge70", "≥70%", 0.70, None),
)
MISSING = ("missing", "Missing")
BOOTSTRAP_RESAMPLES = 4000
BOOTSTRAP_SEED = 20260926
DATE_BASIS = {
    "field": "slate_date",
    "label": "Slate date (ET) of the funding decision",
    "note": "Date filters use the Eastern slate date recorded on the governing decision "
            "(the game's ET slate), not the time the position was placed or settled.",
}
METHODS = {
    "roi": "Stake-weighted ROI = net profit / settled stake. Pushes and voids stay in the stake (their net is 0).",
    "roi_ci95": f"95% percentile bootstrap: {BOOTSTRAP_RESAMPLES:,} resamples of settled wagers with a fixed "
                f"seed; shown only when n ≥ 2. Wide intervals mean the slice cannot separate skill from noise.",
    "win_rate_ci95": "Wilson score 95% interval on wins / (wins + losses); pushes and voids are excluded.",
    "expected_wins": "Predicted vs observed use one denominator: settled wins and losses only (pushes and voids "
                     "are refunded and excluded). The prediction is the selected-side probability of a win given a "
                     "decided result: the recorded raw probability where the market cannot tie (MLB, CFB), and the "
                     "derived non-tie probability pWin / (1 − pTie) for NFL, where ties are refunded. An NFL wager "
                     "without a recorded tie probability is left out of the comparison, not guessed. The recorded "
                     "raw probability and tie-aware return are shown unchanged.",
    "binding": "Each position's funding decision must match its experiment, game, market and selected side. A "
               "decision that conflicts or cannot be resolved keeps the position and settlement but contributes "
               "no estimate or probability (listed under Missing, flagged).",
    "estimated_profit": "Estimated at entry: Σ stake × recorded policy-adjusted return. Not a promise; it is "
                        "what the recorded probability and entry price implied.",
    "exploratory": "Band results are exploratory slices of a small sample, unadjusted for multiple comparisons.",
    "return_vs_confidence": "Higher estimated return is not higher confidence: return = p × decimal odds − 1, "
                            "so a longshot can carry a high return with a low win probability.",
}


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _iso(moment: datetime | None) -> str | None:
    aware = _aware(moment)
    return aware.astimezone(UTC).isoformat() if aware else None


def _enum(value: Any) -> str:
    raw = getattr(value, "name", None) or str(value or "")
    return raw.split(".")[-1].upper()


def _american(decimal_odds: float | None) -> int | None:
    if decimal_odds is None or decimal_odds <= 1:
        return None
    if decimal_odds >= 2:
        return int(round((decimal_odds - 1) * 100))
    return int(round(-100 / (decimal_odds - 1)))


def band_of(value: float | None, bands: Iterable[tuple[str, str, float | None, float | None]]) -> str:
    if value is None:
        return MISSING[0]
    for key, _label, lo, hi in bands:
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return key
    return MISSING[0]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_roi(stakes: list[float], nets: list[float]) -> tuple[float, float] | None:
    """95% percentile bootstrap of stake-weighted ROI (deterministic seed)."""

    n = len(stakes)
    if n < 2:
        return None
    import numpy as np

    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, n, size=(BOOTSTRAP_RESAMPLES, n))
    s = np.asarray(stakes, dtype=float)[idx].sum(axis=1)
    p = np.asarray(nets, dtype=float)[idx].sum(axis=1)
    roi = np.divide(p, s, out=np.zeros_like(p), where=s > 0)
    lo, hi = np.percentile(roi, [2.5, 97.5])
    return (float(lo), float(hi))


# ------------------------------------------------------------------ rows ---

def _recorded(sport: str, evidence: Any, decision_model_p: Any, decision_ev: Any) -> dict[str, Any]:
    """The recorded return, raw/adjusted probability and adjustments for one decision."""

    ev = evidence if isinstance(evidence, dict) else {}
    sel = ev.get("selection") if isinstance(ev.get("selection"), dict) else {}
    terms = sel.get("terms") if isinstance(sel.get("terms"), dict) else {}
    out: dict[str, Any] = {"recorded_return": None, "return_source": None, "raw_probability": None,
                           "adjusted_probability": None, "tie_probability": None, "no_vig_probability": None,
                           "adjustments": None, "evidence_side": None, "policy_adjusts": False}
    if sport == "MLB":
        out["recorded_return"] = _f(ev.get("adjusted_expected_value"))
        out["return_source"] = "evidence.adjusted_expected_value"
        out["raw_probability"] = _f(ev.get("raw_model_probability", decision_model_p))
        out["adjusted_probability"] = _f(ev.get("adjusted_probability"))
        out["no_vig_probability"] = _f(ev.get("market_probability"))
        out["policy_adjusts"] = True
        if "adjusted_probability" in ev:
            out["adjustments"] = {
                "uncertainty": _f(ev.get("uncertainty_adjustment")),
                "starter": _f(ev.get("starter_penalty")),
                "lineup": _f(ev.get("lineup_penalty")),
                "uncertainty_status": ev.get("uncertainty_status"),
            }
    elif sport in ("CFB", "NBA", "NHL"):  # NHL: the final two-way channel; an NHL moneyline cannot tie
        out["recorded_return"] = _f(terms.get("expected_return_per_dollar"))
        out["return_source"] = "evidence.selection.terms.expected_return_per_dollar"
        out["raw_probability"] = _f(sel.get("win_probability", decision_model_p) if sel else decision_model_p)
        out["no_vig_probability"] = _f(terms.get("no_vig_probability"))
        out["evidence_side"] = (str(sel.get("side")).upper() if sel.get("side") else None)
    elif sport == "NFL":
        out["recorded_return"] = _f(sel.get("expected_value", decision_ev) if sel else None)
        out["return_source"] = "evidence.selection.expected_value (tie-aware)"
        out["raw_probability"] = _f(sel.get("win_probability", decision_model_p) if sel else decision_model_p)
        out["tie_probability"] = _f(sel.get("tie_probability")) if sel else None
        out["evidence_side"] = (str(sel.get("side")).upper() if sel.get("side") else None)
    return out


INVALID_DOMAIN = "INVALID_PROBABILITY_DOMAIN"
DOMAIN_TOLERANCE = 1e-9


def _comparison(sport: str, raw_p: float | None, tie_p: float | None) -> tuple[float | None, str | None]:
    """The probability compared with wins / (wins + losses): same denominator as the observation.

    A tie-capable sport (NFL) refunds ties, so the observed rate excludes pushes; the matching
    prediction is the derived non-tie probability pWin / (1 - pTie). Without a recorded tie
    probability it is not guessed. MLB, CFB, NBA and NHL (overtime/shootout) moneylines cannot tie.
    """

    if raw_p is None:
        return None, None
    if not (math.isfinite(raw_p) and 0.0 <= raw_p <= 1.0):
        return None, INVALID_DOMAIN
    if sport == "NFL":
        if tie_p is None:
            return None, "TIE_PROBABILITY_NOT_RECORDED"
        # A valid win/tie pair: both bounded, tie < 1, and non-negative remaining loss mass.
        # Nothing is clamped or substituted; an invalid pair is reported, not repaired.
        if not (math.isfinite(tie_p) and 0.0 <= tie_p < 1.0) or raw_p + tie_p > 1.0 + DOMAIN_TOLERANCE:
            return None, INVALID_DOMAIN
        denominator = 1.0 - tie_p
        if denominator <= 0.0:
            return None, INVALID_DOMAIN
        derived = raw_p / denominator
        # The division amplifies any slack in the pair check (5e-10 / 1e-12 = 500), so the derived value
        # itself must be a probability. The tolerance applies to the derived value; nothing is clamped.
        if not (math.isfinite(derived) and 0.0 <= derived <= 1.0 + DOMAIN_TOLERANCE):
            return None, INVALID_DOMAIN
        return derived, "NON_TIE_DERIVED"
    return raw_p, "RAW_NO_TIE_OUTCOME"


def _norm_market(value: Any) -> str:
    raw = (getattr(value, "name", None) or str(value or "")).split(".")[-1].upper()
    return "MONEYLINE" if raw in ("MONEYLINE", "H2H", "ML") else raw


def _binding(dec: Any, pos: Any, game_id: Any, market_id: Any, market_type: Any, position_side: str | None,
             position_side_conflict: bool, evidence_side: str | None,
             decision_outcomes: dict[Any, tuple[Any, str | None, str | None, Any]],
             side_of_text: Any, side_signals: Any) -> str:
    """Whether the joined decision provably governs this position (experiment, game, market, side)."""

    if position_side_conflict:
        return "CONFLICT_OUTCOME_SIDE_SIGNALS"
    if dec is None:
        return "NO_GOVERNING_DECISION"
    if dec.experiment_id != pos.shadow_experiment_id:
        return "CONFLICT_EXPERIMENT"
    if dec.game_id != game_id:
        return "CONFLICT_GAME"
    decision_market = decision_outcomes.get(dec.market_outcome_id) if dec.market_outcome_id is not None else None
    if dec.market_outcome_id is not None and decision_market is None:
        return "UNRESOLVED_MARKET"
    if decision_market is not None and decision_market[0] != market_id:
        return "CONFLICT_MARKET"
    if dec.market and _norm_market(dec.market) != _norm_market(market_type):
        return "CONFLICT_MARKET"
    decision_outcome_signals = side_signals(dec.game_id, *decision_market[1:]) if decision_market is not None else set()
    if len(decision_outcome_signals) > 1:
        return "CONFLICT_DECISION_OUTCOME_SIDE_SIGNALS"
    signals = {s for s in (evidence_side, side_of_text(dec.game_id, dec.selection)) if s} | decision_outcome_signals
    if position_side is None or not signals:
        return "UNRESOLVED_SIDE"
    if signals != {position_side}:
        return "CONFLICT_SIDE"
    return "OK"


def _load(session: Session) -> tuple[list[dict[str, Any]], int]:
    p, e, m, a = OfficialAspPosition, ShadowExperiment, ExperimentAccountMembership, ExperimentAccount
    d, pr, mo, mk, g = ShadowExperimentDecision, PositionSettlement, MarketOutcome, Market, Game
    os_, sb = aliased(OddsSnapshot), aliased(Sportsbook)
    stmt = (
        select(p, e, a.sport, d, pr, mo.outcome_key, mo.label, g.id, g.starts_at, os_.american_odds, sb.name,
               mo.participant_team_id, mo.market_id, mk.market_type)
        .join(e, e.id == p.shadow_experiment_id)
        .join(m, (m.experiment_id == e.id) & (m.role == MembershipRole.FUNDED_ERA))
        .join(a, a.id == m.account_id)
        .join(mo, mo.id == p.market_outcome_id)
        .join(mk, mk.id == mo.market_id)
        .join(g, g.id == mk.game_id)
        .outerjoin(d, d.id == p.shadow_decision_id)
        .outerjoin(pr, pr.position_id == p.id)
        .outerjoin(os_, os_.id == p.odds_snapshot_id)
        .outerjoin(sb, sb.id == os_.sportsbook_id)
    )
    records = session.execute(stmt).all()
    unfunded = session.execute(
        select(p.id).outerjoin(m, m.experiment_id == p.shadow_experiment_id)
        .where((m.id.is_(None)) | (m.role != MembershipRole.FUNDED_ERA))
    ).all()

    game_ids = sorted({r[7] for r in records}, key=str)
    teams: dict[Any, dict[str, str]] = {}
    #: (game, team) -> HOME/AWAY, plus the names a label may carry, for sides not coded in the outcome key.
    team_side: dict[tuple[Any, Any], str] = {}
    name_side: dict[tuple[Any, str], str] = {}
    if game_ids:
        for game_id, team_id, is_home, abbreviation, name, city in session.execute(
                select(GameTeam.game_id, GameTeam.team_id, GameTeam.is_home, Team.abbreviation, Team.name, Team.city)
                .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id.in_(game_ids))).all():
            abbr = abbreviation
            if abbr and str(abbr).startswith("CFBD") and str(abbr)[4:].isdigit():
                abbr = name
            side_key = "HOME" if is_home else "AWAY"
            teams.setdefault(game_id, {})[side_key.lower()] = abbr or name
            team_side[(game_id, team_id)] = side_key
            for alias in (abbreviation, name, f"{city} {name}" if city else None):
                if alias:
                    name_side[(game_id, str(alias).strip().upper())] = side_key

    # The decisions' own outcomes, so a decision's side and market are resolved without the position's.
    decision_outcome_ids = sorted({r[3].market_outcome_id for r in records
                                   if r[3] is not None and r[3].market_outcome_id is not None}, key=str)
    decision_outcomes: dict[Any, tuple[Any, str | None, str | None, Any]] = {}
    if decision_outcome_ids:
        for oid, omarket, key, olabel, part in session.execute(
                select(MarketOutcome.id, MarketOutcome.market_id, MarketOutcome.outcome_key, MarketOutcome.label,
                       MarketOutcome.participant_team_id).where(MarketOutcome.id.in_(decision_outcome_ids))).all():
            decision_outcomes[oid] = (omarket, key, olabel, part)

    def side_of_text(game: Any, text: Any) -> str | None:
        raw = str(text or "").strip()
        head = raw.split(":")[0].strip().upper()
        if head in ("HOME", "AWAY"):
            return head
        for candidate in (raw.upper(), raw.upper().removesuffix(" ML").strip(), head):
            found = name_side.get((game, candidate))
            if found:
                return found
        return None

    def side_signals(game: Any, key: Any, olabel: Any, part: Any) -> set[str]:
        """Every independently resolvable side of one outcome: key, participant team, label.

        Returned as a set so a caller sees disagreement; no field outranks another.
        """

        found = {
            side_of_text(game, key),
            team_side.get((game, part)) if part is not None else None,
            side_of_text(game, olabel),
        }
        return {x for x in found if x}

    rows: list[dict[str, Any]] = []
    for (pos, exp, sport, dec, res, outcome_key, label, game_id, starts_at, snap_american, book_name,
         participant, market_id, market_type) in records:
        sport = str(sport).upper()
        flags: list[str] = []
        # The position's side, from its own outcome only; contradictory signals resolve to no side.
        position_signals = side_signals(game_id, outcome_key, label, participant)
        side_conflict = len(position_signals) > 1
        side = next(iter(position_signals)) if len(position_signals) == 1 else None
        pair = teams.get(game_id, {})
        selection = label
        if (not selection or selection.upper() in ("HOME", "AWAY")) and side:
            selection = f"{pair.get(side.lower(), side)} ML"
        rec = _recorded(sport, dec.evidence if dec else None, dec.model_probability if dec else None,
                        dec.expected_value if dec else None) if dec else _recorded(sport, None, None, None)
        binding = _binding(dec, pos, game_id, market_id, market_type, side, side_conflict, rec["evidence_side"],
                           decision_outcomes, side_of_text, side_signals)
        if binding != "OK":
            # Keep the position, its settlement and identities; withhold every estimate from a decision
            # that cannot be shown to govern this exact position.
            flags.append(binding)
            for key in ("recorded_return", "raw_probability", "adjusted_probability", "tie_probability",
                        "no_vig_probability", "adjustments"):
                rec[key] = None
        comparison_p, comparison_basis = _comparison(sport, rec["raw_probability"], rec["tie_probability"])
        if comparison_basis in ("TIE_PROBABILITY_NOT_RECORDED", INVALID_DOMAIN):
            flags.append(comparison_basis)
        probability_valid = rec["raw_probability"] is not None and comparison_basis != INVALID_DOMAIN
        if rec["recorded_return"] is None and binding == "OK":
            flags.append("RETURN_NOT_RECORDED")
        settled = res is not None and res.profit_loss is not None and res.settled_at is not None
        status = "SETTLED" if settled else _enum(pos.status)
        result = _enum(res.result) if res is not None else _enum(pos.result)
        if result == "PENDING":
            result = None
        decimal = _f(pos.decimal_odds)
        american = snap_american if snap_american is not None else (
            dec.american_odds if dec is not None and dec.american_odds is not None else _american(decimal))
        slate = dec.slate_date if dec is not None and dec.slate_date else None
        if slate is None and starts_at is not None:
            slate = _aware(starts_at).astimezone(ET).date()
        stake = _f(pos.stake) or 0.0
        ret = rec["recorded_return"]
        rows.append({
            "position_id": str(pos.id),
            "decision_id": str(dec.id) if dec is not None else None,
            "decision_phase": _enum(dec.phase) if dec is not None else None,
            "decision_recommendation": dec.recommendation if dec is not None else None,
            "game_id": str(game_id),
            "matchup": f"{pair.get('away', '?')} @ {pair.get('home', '?')}",
            "starts_at": _iso(starts_at),
            "slate_date": slate.isoformat() if isinstance(slate, date) else None,
            "sport": sport,
            "engine": exp.controlling_engine,
            "experiment_id": str(exp.id),
            "experiment_name": (exp.name or "").replace("�", "-"),
            "experiment_version": exp.version,
            "recommendation_policy_version": pos.recommendation_policy_version or exp.recommendation_policy_version,
            "staking_policy_version": pos.staking_policy_version or exp.staking_policy_version,
            "selection": selection or outcome_key,
            "selected_side": side,
            "decision_at": _iso(dec.recommended_at) if dec is not None else None,
            "placed_at": _iso(pos.placed_at),
            "sportsbook": book_name or (dec.sportsbook if dec is not None else None),
            "entry_american": american,
            "entry_decimal": decimal,
            "raw_probability": rec["raw_probability"],
            "adjusted_probability": rec["adjusted_probability"],
            "tie_probability": rec["tie_probability"],
            "comparison_probability": comparison_p,
            "comparison_basis": comparison_basis,
            "binding_status": binding,
            "no_vig_probability": rec["no_vig_probability"],
            "adjustments": rec["adjustments"],
            "policy_adjusts": rec["policy_adjusts"],
            "recorded_return": ret,
            "return_source": rec["return_source"],
            "return_band": band_of(ret, RETURN_BANDS),
            # An out-of-domain recorded p stays visible on the row but is never banded or averaged.
            "probability_band": band_of(rec["raw_probability"] if probability_valid else None, PROBABILITY_BANDS),
            "probability_valid": probability_valid,
            "stake": round(stake, 2),
            "estimated_profit": (stake * ret) if ret is not None else None,
            "status": status,
            "result": result,
            "settled": settled,
            "net_profit": _f(res.profit_loss) if settled else None,
            "settled_at": _iso(res.settled_at) if settled else None,
            "flags": flags,
        })
    # A correction recorded beside the settlement (position_settlement_adjustments)
    # is what a settled wager now reads as; none exist today.
    from app.cfb.settlement_corrections import effective_settlements

    corrected = {str(key): value for key, value in effective_settlements(
        session, [r[0].id for r in records if r[4] is not None]).items()}
    for row in rows:
        fix = corrected.get(row["position_id"])
        if fix is not None and row["settled"]:
            row.update(result=fix.result, net_profit=fix.profit_loss, **fix.as_dict())
    rows.sort(key=lambda r: (r["placed_at"] or "", r["position_id"]), reverse=True)
    return rows, len(unfunded)


# --------------------------------------------------------------- summary ---

def _is(r: dict[str, Any], *results: str) -> bool:
    return (r["result"] or "") in results


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = sorted((r for r in rows if r["settled"]), key=lambda r: r["position_id"])
    open_ = [r for r in rows if not r["settled"]]
    decided = [r for r in settled if _is(r, "WIN", "LOSS")]
    wins = sum(1 for r in settled if _is(r, "WIN"))
    stake = sum(r["stake"] for r in settled)
    net = sum(r["net_profit"] for r in settled)
    est_rows = [r for r in settled if r["recorded_return"] is not None]
    est = sum(r["estimated_profit"] for r in est_rows)
    est_stake = sum(r["stake"] for r in est_rows)
    with_p = [r for r in decided if r["raw_probability"] is not None and r["probability_valid"]]
    with_adj = [r for r in decided if r["adjusted_probability"] is not None and r["probability_valid"]]
    # The prediction/observation comparison uses one denominator: decided (win/loss) wagers with a
    # comparison probability, and the observed rate over exactly those wagers.
    matched = [r for r in decided if r["comparison_probability"] is not None]
    matched_wins = sum(1 for r in matched if _is(r, "WIN"))
    ci = bootstrap_roi([r["stake"] for r in settled], [r["net_profit"] for r in settled])
    wilson_ci = wilson(wins, len(decided))
    matched_ci = wilson(matched_wins, len(matched))
    return {
        "n": len(rows),
        "settled": len(settled),
        "wins": wins,
        "losses": sum(1 for r in settled if _is(r, "LOSS")),
        "pushes": sum(1 for r in settled if _is(r, "PUSH")),
        "voids": sum(1 for r in settled if _is(r, "VOID")),
        "decided": len(decided),
        "settled_stake": round(stake, 2),
        "net_profit": round(net, 2),
        "roi": (net / stake) if stake else None,
        "roi_ci95": list(ci) if ci else None,
        "estimated_profit": round(est, 2) if est_rows else None,
        "estimated_roi": (est / est_stake) if est_stake else None,
        "estimated_n": len(est_rows),
        "avg_recorded_return": (sum(r["recorded_return"] for r in est_rows) / len(est_rows)) if est_rows else None,
        "win_rate": (wins / len(decided)) if decided else None,
        "win_rate_ci95": list(wilson_ci) if wilson_ci else None,
        "avg_raw_probability": (sum(r["raw_probability"] for r in with_p) / len(with_p)) if with_p else None,
        "probability_n": len(with_p),
        "avg_adjusted_probability": (sum(r["adjusted_probability"] for r in with_adj) / len(with_adj))
        if with_adj else None,
        "adjusted_n": len(with_adj),
        "comparison_n": len(matched),
        "avg_comparison_probability": (sum(r["comparison_probability"] for r in matched) / len(matched))
        if matched else None,
        "expected_wins": sum(r["comparison_probability"] for r in matched) if matched else None,
        "actual_wins_with_probability": matched_wins,
        "comparison_win_rate": (matched_wins / len(matched)) if matched else None,
        "comparison_win_rate_ci95": list(matched_ci) if matched_ci else None,
        "tie_derived_n": sum(1 for r in matched if r["comparison_basis"] == "NON_TIE_DERIVED"),
        "binding_excluded": sum(1 for r in rows if r["binding_status"] != "OK"),
        "open_count": len(open_),
        "open_exposure": round(sum(r["stake"] for r in open_), 2),
        "open_estimated_profit": round(sum(r["estimated_profit"] for r in open_ if r["estimated_profit"] is not None), 2),
    }


def _bands(rows: list[dict[str, Any]], key: str,
           bands: tuple[tuple[str, str, float | None, float | None], ...]) -> list[dict[str, Any]]:
    out = []
    for band_key, label, lo, hi in (*bands, (MISSING[0], MISSING[1], None, None)):
        subset = [r for r in rows if r[key] == band_key]
        out.append({"key": band_key, "label": label, "lower": lo, "upper": hi, **summarize(subset)})
    return out


# ------------------------------------------------------------- endpoint ---

def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def wager_results(session: Session, *, sport: str = "ALL", engine: str | None = None,
                  era: str | None = None, date_from: str | None = None, date_to: str | None = None,
                  now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    sport = (sport or "ALL").upper()
    if sport not in ("ALL", *SPORTS):
        raise ValueError(f"Unknown sport {sport}")
    start, end = _parse_date(date_from), _parse_date(date_to)
    everything, unfunded = _load(session)

    # Options are listed from the full population so a filter can always be widened.
    engines: dict[tuple[str, str], int] = {}
    eras: dict[str, dict[str, Any]] = {}
    for r in everything:
        engines[(r["sport"], r["engine"])] = engines.get((r["sport"], r["engine"]), 0) + 1
        item = eras.setdefault(r["experiment_id"], {
            "id": r["experiment_id"], "sport": r["sport"], "engine": r["engine"],
            "name": r["experiment_name"], "version": r["experiment_version"],
            "recommendation_policy_version": r["recommendation_policy_version"],
            "staking_policy_version": r["staking_policy_version"], "n": 0,
            "first_slate": None, "last_slate": None})
        item["n"] += 1
        if r["slate_date"]:
            item["first_slate"] = min(filter(None, (item["first_slate"], r["slate_date"])))
            item["last_slate"] = max(filter(None, (item["last_slate"], r["slate_date"])))
    for item in eras.values():
        item["label"] = f"{item['sport']} · {item['name']} (v{item['version']})"

    rows = everything
    if sport != "ALL":
        rows = [r for r in rows if r["sport"] == sport]
    if engine:
        rows = [r for r in rows if r["engine"] == engine]
    if era:
        rows = [r for r in rows if r["experiment_id"] == era]
    if start:
        rows = [r for r in rows if r["slate_date"] and r["slate_date"] >= start.isoformat()]
    if end:
        rows = [r for r in rows if r["slate_date"] and r["slate_date"] <= end.isoformat()]

    parts = ["All official funded positions — all sports" if sport == "ALL"
             else f"Official funded positions — {SPORT_LABELS[sport]}"]
    if engine:
        parts.append(f"{sport if sport != 'ALL' else ''} {ENGINE_LABELS.get(engine, engine)}".strip())
    if era and era in eras:
        parts.append(eras[era]["label"])
    if not engine and not era:
        parts.append("all engines and policy eras")
    if start or end:
        parts.append(f"slate {start.isoformat() if start else '…'} → {end.isoformat() if end else '…'}")

    stamps = [s for r in everything for s in (r["placed_at"], r["settled_at"]) if s]
    return {
        "schema_version": SCHEMA,
        "generated_at_utc": _iso(now),
        "data_as_of_utc": max(stamps) if stamps else None,
        "population": {
            "key": "OFFICIAL_FUNDED_POSITIONS",
            "label": "Official funded positions",
            "note": "One row per official funded position (official_asp_positions in a FUNDED_ERA experiment), "
                    "joined to its exact funding decision (shadow_decision_id). Not the scored-forecast "
                    "population used by the accuracy tabs.",
            "exclusions": [
                "House Divided copies (competition_wagers) are never read.",
                "Repeated checkpoint decisions are never read: each position joins only its funding decision.",
                "Unfunded shadow and control experiments hold no counted positions.",
            ],
            "positions_outside_funded_eras": unfunded,
        },
        "scope": {"sport": sport, "engine": engine, "era": era,
                  "date_from": start.isoformat() if start else None,
                  "date_to": end.isoformat() if end else None,
                  "label": " · ".join(parts)},
        "date_basis": DATE_BASIS,
        "methods": METHODS,
        "options": {
            "sports": [{"code": s, "label": SPORT_LABELS[s], "n": sum(1 for r in everything if r["sport"] == s)}
                       for s in SPORTS],
            "engines": [{"sport": s, "engine": en, "label": f"{s} · {ENGINE_LABELS.get(en, en)}", "n": n}
                        for (s, en), n in sorted(engines.items())],
            "eras": sorted(eras.values(), key=lambda x: (x["sport"], x["version"])),
        },
        "summary": summarize(rows),
        # Research-only sports hold no official positions BY DESIGN. Stated, so an
        # empty table can never read as "positions exist but failed to load". (None now.)
        "research_only_sports": {code: _research_only_notice(code) for code in RESEARCH_ONLY_SPORTS
                                 if sport in ("ALL", code)},
        "return_bands": _bands(rows, "return_band", RETURN_BANDS),
        "probability_bands": _bands(rows, "probability_band", PROBABILITY_BANDS),
        "rows": rows,
    }


def _research_only_notice(code: str) -> dict[str, Any]:
    return {"sport": code, "official_positions": 0, "wagering_enabled": False,
            "statement": f"No official {code} positions exist."}


__all__ = ["wager_results", "summarize", "band_of", "wilson", "bootstrap_roi"]
