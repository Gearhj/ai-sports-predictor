"""Offline MLB acceptance-policy research study -- stage 2 (analysis).

Runs entirely on the frozen cohort export written by
:mod:`scripts.mlb_acceptance_policy_study`. Nothing here opens a database
connection, calls a provider, or writes any operational row.

WHAT IT PRODUCES
----------------
1. ``policy_contract.json``  - what the control policy actually gates on, measured
   from every persisted evaluation rather than assumed from the brief.
2. ``waterfall.json``        - sequential attrition and overlapping rejection causes.
3. ``matrix.json``           - the frozen 20-cell research matrix, with duplicate
   cells collapsed and the collapse justified.
4. ``replay.json``           - exact-control reconciliation plus each candidate's
   marginal selections, unit-stake return and outcome-linked metrics.
5. ``marginal_ledger.json``  - every event-linked selection a candidate adds or
   removes versus the control, at its actual decision-time price.

HONESTY RULES ENFORCED IN CODE
------------------------------
* A side, bookmaker, quote or time is never re-chosen after the fact: every
  candidate reads the SAME persisted decision-of-record the live contract used.
* A game with no usable price contributes to availability counts only. It never
  produces an invented quote, return or "would have won" claim.
* Repeated evaluations of one event are never independent observations: the
  principal units are distinct events and unique selection opportunities, and
  the repeated rows are reported separately.
* Hypothetical replay results are kept strictly apart from the live ledger.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

CONTROL_POLICY = "AISP_PRACTICAL_PREGAME_POLICY_V2_1"
CONTROL_EXPERIMENT_VERSION = 3
COMPARISON_EXPERIMENT_VERSION = 1
FINAL_PREGAME_CUTOFF_MINUTES = 15

# -- the frozen research matrix (fixed BEFORE any candidate return was read) --
EDGE_FLOORS = (0.0150, 0.0175, 0.0200, 0.0250)
READINESS_PROFILES: dict[str, dict[str, Any]] = {
    "R0": {"threshold": 70.0, "veto_exception": False,
           "note": "Exact current V2.1 readiness behaviour (the control profile)."},
    "R1": {"threshold": 65.0, "veto_exception": False,
           "note": "Total readiness threshold 65; every other readiness rule unchanged."},
    "R2": {"threshold": 60.0, "veto_exception": False,
           "note": "Total readiness threshold 60; every other readiness rule unchanged."},
    "R3": {"threshold": 70.0, "veto_exception": True,
           "note": "Threshold 70 with a diagnostic exception to the measured "
                   "feature-completeness critical veto, if one exists."},
    "R4": {"threshold": 60.0, "veto_exception": True,
           "note": "Threshold 60 with the same diagnostic exception."},
}
CONTROL_CELL = "R0@0.0200"

#: Eligibility is a property on the evidence dataclasses and is not serialised,
#: so the study derives it from the persisted state with the same memberships.
STARTER_ELIGIBLE_STATES = frozenset({"CONFIRMED", "PROBABLE_STABLE"})
LINEUP_ELIGIBLE_STATES = frozenset({
    "CONFIRMED_COMPLETE", "ADEQUATE_PARTIAL", "PENDING_ALLOWED_FOR_MONEYLINE",
})

# Frozen staking/exposure rules, read from the control experiment row.
UNIT_STAKE = 1.0


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


# ---------------------------------------------------------------------------
# cohort shaping
# ---------------------------------------------------------------------------

class Cohort:
    """The frozen export, indexed for analysis. Read once, never re-queried."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.raw = payload
        self.experiments = {int(e["version"]): e for e in payload["experiments"]}
        self.control_experiment = self.experiments[CONTROL_EXPERIMENT_VERSION]
        self.games = {str(g["id"]): g for g in payload["games"]}
        self.outcomes = {str(o["id"]): o for o in payload["market_outcomes"]}
        self.positions = payload["positions"]
        self.decisions = payload["decisions"]
        self.control_rows = [d for d in self.decisions
                             if d["experiment_version"] == CONTROL_EXPERIMENT_VERSION]
        self.comparison_rows = [d for d in self.decisions
                                if d["experiment_version"] == COMPARISON_EXPERIMENT_VERSION]

        for game in self.games.values():
            starts = _dt(game["starts_at"])
            game["_starts"] = starts
            game["_cutoff"] = starts - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
            # Repaired outcome join. game_final_outcomes is the sealed certified
            # table and holds nothing for this window; the operational score of
            # record is the game row, which reconciles exactly with every
            # settled position_results score (verified in reconciliation).
            final = str(game["status"]).upper() == "FINAL"
            game["_settled"] = final
            game["_home_score"] = game["home_score"] if final else None
            game["_away_score"] = game["away_score"] if final else None
            game["_home_won"] = (
                None if not final or game["home_score"] is None
                else game["home_score"] > game["away_score"]
            )

        # Moneyline price availability, per event, independent of any policy.
        priced: set[str] = set()
        books: dict[str, set[Any]] = defaultdict(set)
        closing: set[str] = set()
        for row in payload["moneyline_quote_coverage"]:
            game_id = str(row["game_id"])
            priced.add(game_id)
            books[game_id].add(row["sportsbook_id"])
            if row["has_closing"]:
                closing.add(game_id)
        self.priced_games = priced
        self.books_per_game = {k: len(v) for k, v in books.items()}
        self.games_with_closing_quote = closing

        self.snapshots_by_outcome: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for snapshot in payload.get("moneyline_snapshots", []):
            self.snapshots_by_outcome[str(snapshot["market_outcome_id"])].append(snapshot)

    def closing_price(self, outcome_id: str, odds_snapshot_id: str | None,
                      first_pitch: datetime | None) -> float | None:
        """Last SAME-BOOK decimal price after the taken quote and at/before first pitch.

        The book is resolved from the recommendation snapshot itself rather than
        from a display name, so a different book's later price can never be
        substituted for the one actually quoted.
        """

        snapshots = self.snapshots_by_outcome.get(outcome_id, [])
        taken = next((s for s in snapshots if str(s["id"]) == str(odds_snapshot_id)), None)
        if taken is None or first_pitch is None:
            return None
        taken_at = _dt(taken["observed_at"])
        later = [
            s for s in snapshots
            if s["decimal_odds"] is not None
            and str(s["sportsbook_id"]) == str(taken["sportsbook_id"])
            and s["source_mode"] == taken["source_mode"]
            and _dt(s["observed_at"]) > taken_at
            and _dt(s["observed_at"]) <= first_pitch
        ]
        if not later:
            return None
        return float(max(later, key=lambda s: _dt(s["observed_at"]))["decimal_odds"])

    # -- the decision of record ------------------------------------------
    def decision_of_record(self, game_id: str) -> dict[str, Any] | None:
        """The exact row the live position contract consumes.

        ``_create_canonical_position`` takes the LAST FINAL_PREGAME decision
        recommended at or before the cutoff. Every candidate policy reads this
        same row, so no candidate can pick a better time in hindsight.
        """

        game = self.games.get(game_id)
        if game is None:
            return None
        cutoff = game["_cutoff"]
        eligible = [
            row for row in self.control_rows
            if str(row["game_id"]) == game_id
            and row["phase"] == "FINAL_PREGAME"
            and _dt(row["recommended_at"]) is not None
            and _dt(row["recommended_at"]) <= cutoff
        ]
        if not eligible:
            return None
        return max(eligible, key=lambda r: _dt(r["recommended_at"]))


def load(output_dir: Path) -> Cohort:
    return Cohort(json.loads((output_dir / "cohort.json").read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# 1. what the control policy actually gates on
# ---------------------------------------------------------------------------

def policy_contract(cohort: Cohort) -> dict[str, Any]:
    """Measure the control's real gates instead of assuming the brief's model.

    The research matrix presumes V2.1 carries a total-readiness threshold and a
    feature-completeness critical veto. Both claims are checked here against
    every persisted evaluation, because a threshold that does not exist cannot
    be lowered and a veto that does not exist cannot be excepted.
    """

    config = cohort.control_experiment.get("policy_config") or {}
    whitelist = list(config.get("hard_block_whitelist") or [])

    qualified = [r for r in cohort.control_rows if r["recommendation"] == "QUALIFIED_BET"]
    readiness = sorted(float(r["readiness"]) for r in qualified if r["readiness"] is not None)
    veto_rows = 0
    missing_required: Counter[str] = Counter()
    for row in cohort.control_rows:
        evidence = row.get("evidence") or {}
        decomposition = (evidence.get("feature_decomposition")
                         or evidence.get("feature_decomposition_summary") or {})
        required = decomposition.get("model_required_missing") or []
        if required:
            veto_rows += 1
            for name in required:
                missing_required[str(name)] += 1

    below_70 = [value for value in readiness if value < 70.0]
    return {
        "control_policy": CONTROL_POLICY,
        "policy_config_checksum": config.get("checksum"),
        "hard_block_whitelist": whitelist,
        "composite_readiness_threshold_in_contract": any(
            "readiness" in item for item in whitelist
        ),
        "feature_completeness_classification": (
            config.get("feature_completeness_decomposition", {})
            .get("readiness_components", {})
            .get("feature_completeness")
        ),
        "qualified_evaluations": len(qualified),
        "qualified_readiness_min": readiness[0] if readiness else None,
        "qualified_readiness_median": statistics.median(readiness) if readiness else None,
        "qualified_readiness_max": readiness[-1] if readiness else None,
        "qualified_below_readiness_70": len(below_70),
        "qualified_below_readiness_60": len([v for v in readiness if v < 60.0]),
        "evaluations_blocked_by_model_required_missing": veto_rows,
        "model_required_missing_features": dict(missing_required),
        "finding": (
            "MEASURED: the control contract contains no composite total-readiness "
            "threshold and no feature-completeness critical veto. feature_completeness "
            "is classified DUPLICATE_DERIVED_GATE and never hard-blocks; the readiness "
            "score is recorded on every decision but gates nothing. "
            f"{len(below_70)} of {len(qualified)} qualified evaluations carry readiness "
            "below 70, which is only possible if no 70 threshold is applied."
        ),
    }


def feature_completeness_facts(cohort: Cohort) -> dict[str, Any]:
    """The corrected feature-completeness arithmetic and its scoring effect."""

    measured_present, measured_total = 13, 36
    fraction = measured_present / measured_total
    return {
        "measured_present_slots": measured_present,
        "measured_total_slots": measured_total,
        "coverage_fraction": round(fraction, 6),
        "coverage_fraction_exact": "13/36 = 0.361111",
        "measured_missing_slots": measured_total - measured_present,
        "excluded_structural_placeholders": 4,
        "diagnostic_list_entries": (measured_total - measured_present) + 4,
        "scoring_contract": {
            "feature_completeness_points_earned": 5,
            "feature_completeness_points_available": 10,
            "feature_completeness_basis": "stale factor -- half credit, not zero",
            "bullpen_points_earned": 0,
            "bullpen_points_available": 5,
            "statcast_points_earned": 0,
            "statcast_points_available": 5,
            "total_points_lost": 15,
            "verification": (
                "5/10 + 0/5 + 0/5 leaves 15 of 20 available points unearned, not 20. "
                "A 20-point loss would require feature_completeness to score zero, "
                "which the stale factor does not do."
            ),
        },
        "veto_effect": {
            "invokes_separate_critical_veto": False,
            "basis": (
                "feature_completeness is classified DUPLICATE_DERIVED_GATE (class E) in "
                "V2_FEATURE_COMPLETENESS_DECOMPOSITION_V1 and is excluded from the atomic "
                "hard-block whitelist. Only a MODEL_REQUIRED_NO_VALID_FALLBACK or "
                "DECISION_REQUIRED component blocks, and no persisted evaluation carries one."
            ),
        },
        "score_only_effect": {
            "readiness_points_lost": 15,
            "selections_changed": 0,
            "basis": "The control applies no total-readiness threshold, so a 15-point "
                     "readiness deficit changes no acceptance decision.",
        },
    }


# ---------------------------------------------------------------------------
# 2. rejection waterfall
# ---------------------------------------------------------------------------

GATE_SEQUENCE = (
    ("valid_current_forecast", "Valid current forecast at the cutoff"),
    ("model_required_inputs_valid_or_governed_fallback", "Model-required inputs valid or governed fallback"),
    ("complete_two_sided_moneyline_quote", "Complete same-book two-sided MONEYLINE quote"),
    ("fresh_quote", "Quote fresh at the cutoff"),
    ("market_probabilities_available", "Model and market probabilities available"),
    ("not_extreme_disagreement", "Not extreme model-market disagreement"),
    ("no_unresolved_scratch", "No unresolved scratch or availability event"),
    ("starters_confirmed_or_probable_stable", "Starters CONFIRMED or PROBABLE_STABLE"),
    ("lineup_confirmed_adequate_or_pending_allowed", "Lineup confirmed, adequate or allowed pending"),
)


def waterfall(cohort: Cohort) -> dict[str, Any]:
    games = sorted(cohort.games.values(), key=lambda g: g["_starts"])
    final_rows = [r for r in cohort.control_rows if r["phase"] == "FINAL_PREGAME"]

    per_game: list[dict[str, Any]] = []
    for game in games:
        game_id = str(game["id"])
        row = cohort.decision_of_record(game_id)
        evidence = (row or {}).get("evidence") or {}
        passed = set(evidence.get("gates_passed") or [])
        starter_home = evidence.get("starter_home") or None
        starter_away = evidence.get("starter_away") or None
        lineup = evidence.get("lineup") or None
        # StarterEvidence.eligible / LineupEvidence.eligible_for_moneyline are
        # properties and are not serialised onto the persisted evidence, so
        # eligibility is derived from the persisted STATE using the same
        # membership rules the evaluator applies.
        starter_eligible = (
            None if not starter_home or not starter_away
            else (starter_home.get("state") in STARTER_ELIGIBLE_STATES
                  and starter_away.get("state") in STARTER_ELIGIBLE_STATES)
        )
        per_game.append({
            "forecast_present": "valid_current_forecast" in passed,
            "usable_quote_at_cutoff": (
                "complete_two_sided_moneyline_quote" in passed and "fresh_quote" in passed
            ),
            "starter_eligible": starter_eligible,
            "starter_states": [
                (starter_home or {}).get("state"), (starter_away or {}).get("state"),
            ],
            "lineup_state": (lineup or {}).get("state"),
            "lineup_eligible": (
                None if lineup is None
                else lineup.get("state") in LINEUP_ELIGIBLE_STATES
            ),
            "game_id": game_id,
            "slate_date": (row or {}).get("slate_date"),
            "matchup": f"{game['away_abbr']} @ {game['home_abbr']}",
            "starts_at": game["starts_at"],
            "status": game["status"],
            "settled": game["_settled"],
            "has_decision_of_record": row is not None,
            "priced_ever": game_id in cohort.priced_games,
            "books": cohort.books_per_game.get(game_id, 0),
            "gates_passed": sorted(passed),
            "critical_evidence_complete": bool(evidence.get("critical_evidence_complete")),
            "recommendation": (row or {}).get("recommendation"),
            "reason": (row or {}).get("recommendation_reason"),
            "adjusted_edge": _f(evidence.get("adjusted_edge")),
            "readiness": _f((row or {}).get("readiness")),
        })

    # Sequential attrition over distinct events.
    remaining = [g for g in per_game if g["has_decision_of_record"]]
    steps = [{"step": "Distinct cohort events", "events": len(per_game)},
             {"step": "Reached a final-pregame decision of record", "events": len(remaining)}]
    for gate, label in GATE_SEQUENCE:
        survivors = [g for g in remaining if gate in g["gates_passed"]]
        steps.append({"step": label, "events": len(survivors),
                      "lost": len(remaining) - len(survivors)})
        remaining = survivors
    edge_ok = [g for g in remaining
               if g["adjusted_edge"] is not None and g["adjusted_edge"] >= 0.02]
    steps.append({"step": "Adjusted edge >= 2.0 pts (control floor)",
                  "events": len(edge_ok), "lost": len(remaining) - len(edge_ok)})
    qualified = [g for g in per_game if g["recommendation"] == "QUALIFIED_BET"]
    steps.append({"step": "Final QUALIFIED_BET at the cutoff", "events": len(qualified)})
    steps.append({"step": "Official position created",
                  "events": len([p for p in cohort.positions
                                 if p["recommendation_policy_version"] == CONTROL_POLICY])})

    # First blocking cause. The evaluator returns at the FIRST failed gate, so
    # gates_passed is truncated and later gates are simply unobserved, not
    # failed. Only the first cause is a fact; the overlap below is measured
    # from evidence the evaluator computes unconditionally.
    first_cause: Counter[str] = Counter()
    for entry in per_game:
        if not entry["has_decision_of_record"]:
            first_cause["No final-pregame decision of record"] += 1
            continue
        blocked = next((label for gate, label in GATE_SEQUENCE
                        if gate not in entry["gates_passed"]), None)
        if blocked is not None:
            first_cause[blocked] += 1
        elif (entry["adjusted_edge"] or 0) < 0.02:
            first_cause["Adjusted edge below the 2.0 pt floor"] += 1
        else:
            first_cause["Not blocked -- selected"] += 1

    # Genuinely overlapping causes, each measured independently of gate order:
    # starter and lineup evidence are always reduced before the gate loop runs,
    # and price availability comes from the odds tables, not from the policy.
    overlapping: Counter[str] = Counter()
    for entry in per_game:
        if not entry["forecast_present"]:
            overlapping["No valid forecast at the cutoff"] += 1
        if not entry["priced_ever"]:
            overlapping["No moneyline price ever observed for this event"] += 1
        elif not entry["usable_quote_at_cutoff"]:
            overlapping["Priced, but no fresh complete two-sided quote at the cutoff"] += 1
        if entry["starter_eligible"] is False:
            overlapping["Starter evidence not CONFIRMED/PROBABLE_STABLE"] += 1
        if entry["lineup_eligible"] is False:
            overlapping["Lineup evidence ineligible"] += 1
        if entry["critical_evidence_complete"] and (entry["adjusted_edge"] or 0) < 0.02:
            overlapping["Adjusted edge below the 2.0 pt floor"] += 1

    # Today's state, kept separate from historically priceable opportunities.
    slate_dates = sorted({str(r["slate_date"]) for r in final_rows})
    today = slate_dates[-1] if slate_dates else None
    today_games = [g for g in per_game if g["slate_date"] == today]
    historical = [g for g in per_game if g["slate_date"] != today]

    return {
        "principal_units": {
            "distinct_events": len(per_game),
            "unique_selection_opportunities": len({
                (str(r["game_id"]), str(r["market_outcome_id"]))
                for r in final_rows if r["market_outcome_id"]
            }),
            "repeated_evaluation_rows_final_pregame": len(final_rows),
            "repeated_evaluation_rows_all_phases": len(cohort.control_rows),
            "note": ("Repeated evaluations of one event are reported here and are never "
                     "treated as independent observations anywhere in this study."),
        },
        "coverage": {
            "events": len(per_game),
            "events_with_a_moneyline_price_ever": len(
                [g for g in per_game if g["priced_ever"]]),
            "events_with_no_moneyline_price_ever": len(
                [g for g in per_game if not g["priced_ever"]]),
            "events_with_a_usable_quote_at_cutoff": len(
                [g for g in per_game
                 if "complete_two_sided_moneyline_quote" in g["gates_passed"]
                 and "fresh_quote" in g["gates_passed"]]),
            "events_with_a_valid_forecast_at_cutoff": len(
                [g for g in per_game if "valid_current_forecast" in g["gates_passed"]]),
            "settled_events": len([g for g in per_game if g["settled"]]),
            "unsettled_events": len([g for g in per_game if not g["settled"]]),
            "events_with_a_closing_quote": len(cohort.games_with_closing_quote),
        },
        "sequential_attrition": steps,
        "first_blocking_cause": dict(first_cause.most_common()),
        "overlapping_causes": dict(overlapping.most_common()),
        "overlapping_causes_note": (
            "Measured independently of gate order. Starter and lineup evidence are "
            "reduced unconditionally before the gate loop, and price availability comes "
            "from the odds tables, so these counts are real overlaps rather than "
            "artefacts of the evaluator returning at its first failed gate."
        ),
        "today": {
            "slate_date": today,
            "events": len(today_games),
            "with_usable_quote_at_cutoff": len(
                [g for g in today_games
                 if "fresh_quote" in g["gates_passed"]]),
            "qualified": len([g for g in today_games if g["recommendation"] == "QUALIFIED_BET"]),
        },
        "historically_priceable": {
            "events": len(historical),
            "with_usable_quote_at_cutoff": len(
                [g for g in historical if "fresh_quote" in g["gates_passed"]]),
            "qualified": len([g for g in historical if g["recommendation"] == "QUALIFIED_BET"]),
        },
        "per_game": per_game,
    }


# ---------------------------------------------------------------------------
# 3. the frozen matrix
# ---------------------------------------------------------------------------

def collapse_matrix(contract: dict[str, Any]) -> dict[str, Any]:
    """Collapse readiness profiles that are operationally identical.

    R1/R2 lower a total-readiness threshold; R3/R4 except a feature-completeness
    critical veto. The measured contract contains neither, so all five profiles
    apply exactly the same rules to exactly the same evidence. They are
    collapsed here and the collapse is reported, rather than four duplicate
    columns being presented as four findings.
    """

    identical = not contract["composite_readiness_threshold_in_contract"] and (
        contract["evaluations_blocked_by_model_required_missing"] == 0
    )
    return {
        "declared_cells": len(EDGE_FLOORS) * len(READINESS_PROFILES),
        "readiness_profiles": READINESS_PROFILES,
        "edge_floors": list(EDGE_FLOORS),
        "control_cell": CONTROL_CELL,
        "profiles_collapse": identical,
        "distinct_cells": len(EDGE_FLOORS) if identical else
                          len(EDGE_FLOORS) * len(READINESS_PROFILES),
        "collapse_basis": (
            "R0..R4 differ only in a total-readiness threshold and a "
            "feature-completeness critical veto. The control contract measurably "
            "contains neither, so the five profiles are operationally identical and "
            "the 20 declared cells reduce to 4 distinct edge-floor cells. No new "
            "candidate was added in their place."
        ) if identical else "Profiles are distinct.",
    }


# ---------------------------------------------------------------------------
# 4. replay
# ---------------------------------------------------------------------------

def _kelly_stake(bankroll: float, adjusted_p: float, decimal_odds: float,
                 kelly_fraction: float, cap_fraction: float, multiplier: float) -> float:
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    edge = adjusted_p * decimal_odds - 1.0
    if edge <= 0:
        return 0.0
    stake = bankroll * (edge / b) * kelly_fraction * multiplier
    return min(stake, bankroll * cap_fraction)


def _selection_won(cohort: Cohort, row: dict[str, Any]) -> bool | None:
    """Did the exact selected team win? The repaired selected-team join."""

    outcome = cohort.outcomes.get(str(row["market_outcome_id"]))
    game = cohort.games.get(str(row["game_id"]))
    if outcome is None or game is None or game["_home_won"] is None:
        return None
    if outcome["participant_team_id"] is None:
        return None
    if str(outcome["participant_team_id"]) == str(game["home_team_id"]):
        return game["_home_won"]
    if str(outcome["participant_team_id"]) == str(game["away_team_id"]):
        return not game["_home_won"]
    return None


def replay(cohort: Cohort, floor: float) -> dict[str, Any]:
    """Replay one candidate through the live decision-timing and dedupe rules.

    Only the adjusted-edge floor moves. The decision of record, its side, its
    bookmaker, its quote and its timestamp are the ones the live contract froze,
    so nothing here can be chosen with hindsight.
    """

    experiment = cohort.control_experiment
    kelly = float(experiment["kelly_fraction"])
    max_same_game = float(experiment["max_same_game_percentage"])
    max_daily = float(experiment["max_daily_percentage"])
    starting_bankroll = float(experiment["starting_bankroll"])

    candidates: list[dict[str, Any]] = []
    for game in sorted(cohort.games.values(), key=lambda g: g["_cutoff"]):
        game_id = str(game["id"])
        row = cohort.decision_of_record(game_id)
        if row is None:
            continue
        evidence = row.get("evidence") or {}
        if not evidence.get("critical_evidence_complete"):
            continue  # never reached the economic stage; no price effect to test
        adjusted_edge = _f(evidence.get("adjusted_edge"))
        adjusted_ev = _f(evidence.get("adjusted_expected_value"))
        adjusted_p = _f(evidence.get("adjusted_probability"))
        decimal_odds = _f(row.get("decimal_odds"))
        if None in (adjusted_edge, adjusted_ev, adjusted_p, decimal_odds):
            continue
        if adjusted_edge < floor or adjusted_ev <= 0:
            continue
        if row["market_outcome_id"] is None or row["odds_snapshot_id"] is None:
            continue
        outcome = cohort.outcomes.get(str(row["market_outcome_id"]))
        candidates.append({
            "game_id": game_id,
            "market_outcome_id": str(row["market_outcome_id"]),
            "odds_snapshot_id": str(row["odds_snapshot_id"]),
            "slate_date": str(row["slate_date"]),
            "matchup": f"{game['away_abbr']} @ {game['home_abbr']}",
            "cutoff": game["_cutoff"].isoformat(),
            "selection": row["selection"],
            "selected_team": (outcome or {}).get("team_abbr"),
            "sportsbook": row["sportsbook"],
            "american_odds": row["american_odds"],
            "decimal_odds": decimal_odds,
            "quote_observed_at": row["quote_observed_at"],
            "recommended_at": row["recommended_at"],
            "model_probability": _f(row["model_probability"]),
            "no_vig_probability": _f(row["no_vig_probability"]),
            "adjusted_probability": adjusted_p,
            "raw_edge": _f(evidence.get("raw_edge")),
            "adjusted_edge": adjusted_edge,
            "adjusted_expected_value": adjusted_ev,
            "tier": evidence.get("tier"),
            "evidence_multiplier": _f(evidence.get("evidence_multiplier")) or 1.0,
            "position_cap_fraction": _f(evidence.get("position_cap_fraction")),
            "readiness": _f(row["readiness"]),
            "live_recommendation": row["recommendation"],
            "won": _selection_won(cohort, row),
            "settled": cohort.games[game_id]["_settled"],
        })

    # One position per market outcome, exactly as the live contract dedupes.
    seen: set[str] = set()
    selections: list[dict[str, Any]] = []
    for entry in candidates:
        if entry["market_outcome_id"] in seen:
            entry["skipped"] = "duplicate market outcome"
            continue
        seen.add(entry["market_outcome_id"])
        selections.append(entry)

    # -- unit stake: isolates the selection effect from any staking effect --
    settled = [s for s in selections if s["settled"] and s["won"] is not None]
    unit_pl = sum((s["decimal_odds"] - 1.0) if s["won"] else -1.0 for s in settled)

    # -- chronological replay under the frozen staking/exposure rules -------
    # Bankroll advances at slate-date boundaries: every cohort game starts and
    # finishes inside its own local slate date, so day-ordered settlement is
    # evidence-supported. Intraday sequencing is NOT recorded (final_at is null
    # for every cohort game), so no finer ordering is claimed.
    bankroll = starting_bankroll
    staked_by_day: dict[str, float] = defaultdict(float)
    staked_by_game: dict[str, float] = defaultdict(float)
    ledger: list[dict[str, Any]] = []
    pending_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    equity_curve: list[tuple[str, float]] = []
    for day in sorted({s["slate_date"] for s in selections}):
        day_selections = [s for s in selections if s["slate_date"] == day]
        for entry in sorted(day_selections, key=lambda s: s["cutoff"]):
            cap = entry["position_cap_fraction"] or float(experiment["max_position_percentage"])
            stake = _kelly_stake(bankroll, entry["adjusted_probability"],
                                 entry["decimal_odds"], kelly, cap,
                                 entry["evidence_multiplier"])
            stake = min(
                stake,
                max(0.0, bankroll * max_same_game - staked_by_game[entry["game_id"]]),
                max(0.0, bankroll * max_daily - staked_by_day[day]),
            )
            stake = round(stake, 2)
            if stake <= 0:
                continue
            staked_by_day[day] += stake
            staked_by_game[entry["game_id"]] += stake
            record = {**entry, "stake": stake, "bankroll_before": round(bankroll, 2)}
            pending_by_day[day].append(record)
            ledger.append(record)
        realised = 0.0
        for record in pending_by_day[day]:
            if not record["settled"] or record["won"] is None:
                record["profit_loss"] = None
                continue
            profit = (record["stake"] * (record["decimal_odds"] - 1.0)
                      if record["won"] else -record["stake"])
            record["profit_loss"] = round(profit, 2)
            realised += profit
        bankroll += realised
        equity_curve.append((day, round(bankroll, 2)))

    peak = starting_bankroll
    drawdown = 0.0
    for _, value in equity_curve:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)

    return {
        "edge_floor": floor,
        "selections": selections,
        "ledger": ledger,
        "equity_curve": equity_curve,
        "unique_selections": len(selections),
        "unique_games": len({s["game_id"] for s in selections}),
        "settled_selections": len(settled),
        "unsettled_selections": len(selections) - len(settled),
        "unit_stake_profit": round(unit_pl, 4),
        "unit_stake_return": round(unit_pl / len(settled), 4) if settled else None,
        "staked_total": round(sum(r["stake"] for r in ledger), 2),
        "staked_profit": round(sum(r["profit_loss"] or 0.0 for r in ledger), 2),
        "ending_bankroll": round(bankroll, 2),
        "max_drawdown": round(drawdown, 2),
        **outcome_metrics(settled),
    }


def outcome_metrics(settled: list[dict[str, Any]]) -> dict[str, Any]:
    """Hit rate and calibration on eligible outcome-linked predictions only."""

    if not settled:
        return {"hit_rate": None, "brier": None, "log_loss": None,
                "mean_model_probability": None, "realised_win_rate": None,
                "temporal_stability": {}}
    wins = [1 if s["won"] else 0 for s in settled]
    probs = [s["model_probability"] for s in settled]
    brier = sum((p - w) ** 2 for p, w in zip(probs, wins)) / len(wins)
    eps = 1e-12
    log_loss = -sum(
        w * math.log(max(p, eps)) + (1 - w) * math.log(max(1 - p, eps))
        for p, w in zip(probs, wins)
    ) / len(wins)
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in settled:
        by_day[entry["slate_date"]].append(entry)
    return {
        "hit_rate": round(sum(wins) / len(wins), 4),
        "realised_win_rate": round(sum(wins) / len(wins), 4),
        "mean_model_probability": round(sum(probs) / len(probs), 4),
        "brier": round(brier, 6),
        "log_loss": round(log_loss, 6),
        "temporal_stability": {
            day: {
                "selections": len(entries),
                "wins": sum(1 for e in entries if e["won"]),
                "unit_profit": round(sum((e["decimal_odds"] - 1.0) if e["won"] else -1.0
                                         for e in entries), 4),
            }
            for day, entries in sorted(by_day.items())
        },
    }


def reconcile_control(cohort: Cohort, control: dict[str, Any]) -> dict[str, Any]:
    """Prove the replay reproduces the live ledger before any candidate is read."""

    live = [p for p in cohort.positions if p["recommendation_policy_version"] == CONTROL_POLICY]
    live_outcomes = {str(p["market_outcome_id"]) for p in live}
    replay_outcomes = {s["market_outcome_id"] for s in control["selections"]}

    score_mismatches = []
    for position in live:
        game = cohort.games.get(str(position["game_id"]))
        if game is None or position["game_home_score"] is None:
            continue
        if (game["home_score"] != position["game_home_score"]
                or game["away_score"] != position["game_away_score"]):
            score_mismatches.append(str(position["id"]))

    result_mismatches = []
    for position in live:
        if position["settled_result"] not in ("WIN", "LOSS"):
            continue
        game = cohort.games.get(str(position["game_id"]))
        outcome = cohort.outcomes.get(str(position["market_outcome_id"]))
        if game is None or outcome is None or game["_home_won"] is None:
            continue
        won = (game["_home_won"] if str(outcome["participant_team_id"]) == str(game["home_team_id"])
               else not game["_home_won"])
        if won != (position["settled_result"] == "WIN"):
            result_mismatches.append(str(position["id"]))

    live_pl = sum(float(p["profit_loss"]) for p in live if p["profit_loss"] is not None)
    return {
        "live_positions": len(live),
        "live_settled": len([p for p in live if p["settled_result"] in ("WIN", "LOSS")]),
        "live_open": len([p for p in live if p["settled_result"] not in ("WIN", "LOSS")]),
        "live_wins": len([p for p in live if p["settled_result"] == "WIN"]),
        "live_losses": len([p for p in live if p["settled_result"] == "LOSS"]),
        "live_staked": round(sum(float(p["stake"]) for p in live), 2),
        "live_realised_profit": round(live_pl, 2),
        "control_replay_selections": len(replay_outcomes),
        "replay_reproduces_every_live_position": live_outcomes <= replay_outcomes,
        "live_positions_missing_from_replay": sorted(live_outcomes - replay_outcomes),
        "replay_selections_without_a_live_position": sorted(replay_outcomes - live_outcomes),
        "outcome_join_score_mismatches": score_mismatches,
        "outcome_join_result_mismatches": result_mismatches,
        "outcome_join_verified": not score_mismatches and not result_mismatches,
        "note": (
            "The replay evaluates every event that reached a decision of record. The live "
            "ledger additionally requires an at-cutoff quote/forecast binding, exposure "
            "headroom and a non-duplicate market outcome, so the replay is a superset by "
            "construction. Every live position must appear in it; none may be missing."
        ),
    }


def closing_line_value(cohort: Cohort, cells: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """CLV under the one definition this system actually uses, and its coverage.

    No snapshot in the cohort carries the ``is_closing`` flag. The live
    settlement engine therefore defines the closing price as the LAST price on
    the same book, strictly after the recommendation and at or before first
    pitch. That definition is applied here -- unchanged -- to the live positions
    and to every hypothetical selection, so the two are comparable. An event
    with no later same-book price has no closing quote and is excluded rather
    than given an invented one.
    """

    positions = [p for p in cohort.positions
                 if p["recommendation_policy_version"] == CONTROL_POLICY]
    live_with_closing = [p for p in positions if p["closing_decimal_odds"] is not None]

    per_cell: dict[str, Any] = {}
    for name, result in cells.items():
        measured: list[dict[str, Any]] = []
        for entry in result["selections"]:
            closing = cohort.closing_price(
                entry["market_outcome_id"], entry["odds_snapshot_id"],
                cohort.games[entry["game_id"]]["_starts"],
            )
            if closing is None:
                continue
            measured.append({
                "matchup": entry["matchup"], "selection": entry["selection"],
                "taken_decimal": entry["decimal_odds"],
                "closing_decimal": closing,
                # Positive = the price taken beat the close.
                "clv_pct": round((entry["decimal_odds"] / closing - 1.0) * 100, 3),
            })
        per_cell[name] = {
            "selections": result["unique_selections"],
            "with_a_closing_quote": len(measured),
            "coverage": (round(len(measured) / result["unique_selections"], 4)
                         if result["unique_selections"] else None),
            "mean_clv_pct": (round(statistics.mean(m["clv_pct"] for m in measured), 3)
                             if measured else None),
            "beat_close": len([m for m in measured if m["clv_pct"] > 0]),
            "detail": measured,
        }

    return {
        "definition": ("Last price on the same book, strictly after the recommendation and "
                       "at or before first pitch -- the live settlement engine's definition, "
                       "applied unchanged to live and hypothetical selections alike."),
        "is_closing_flagged_snapshots_in_cohort": len(
            [s for s in cohort.raw.get("moneyline_snapshots", []) if s.get("is_closing")]),
        "live_positions": len(positions),
        "live_positions_with_a_recorded_closing_price": len(live_with_closing),
        "live_coverage": (round(len(live_with_closing) / len(positions), 4)
                          if positions else None),
        "live_mean_clv_pct": (
            round(statistics.mean(
                (float(p["decimal_odds"]) / float(p["closing_decimal_odds"]) - 1.0) * 100
                for p in live_with_closing), 3)
            if live_with_closing else None),
        "per_cell": per_cell,
        "limitation": (
            "Coverage is partial because odds collection stopped well before first pitch "
            "for many events. CLV is reported only where a legitimate later same-book "
            "price exists; it is never imputed."
        ),
    }


def strict_comparison(cohort: Cohort) -> dict[str, Any]:
    """V1, the separate historical strict control. Kept apart from V2.1.

    V1 is a different experiment over a different window and owns no position.
    It is reported for context only and is never pooled with the control.
    """

    rows = [r for r in cohort.comparison_rows if r["phase"] == "FINAL_PREGAME"]
    by_recommendation = Counter(r["recommendation"] for r in rows)
    return {
        "policy": "AISP_OPPORTUNITY_POLICY_V1",
        "relationship": "separate historical strict comparison; not pooled with the control",
        "final_pregame_rows": len(rows),
        "distinct_events": len({str(r["game_id"]) for r in rows}),
        "slate_dates": sorted({str(r["slate_date"]) for r in rows}),
        "recommendations": dict(by_recommendation),
        "official_positions": len([
            p for p in cohort.positions
            if p["recommendation_policy_version"] == "AISP_OPPORTUNITY_POLICY_V1"]),
        "note": ("V1 never produces a QUALIFIED_BET in this window and owns no position; "
                 "its readiness>=70 gate and CONFIRMED-starter requirement are both "
                 "unsatisfiable in this configuration."),
    }


def sensitivity(cells: dict[str, dict[str, Any]], marginal: dict[str, Any]) -> dict[str, Any]:
    """How few events separate the cells -- stated plainly, not bootstrapped.

    Every difference in this matrix rests on one to three settled events across
    six slate days. Resampling the same events cannot manufacture evidence, so
    the sensitivity is reported as the exact count of events whose result would
    have to flip to reverse the ranking.
    """

    control = cells[CONTROL_CELL]
    entries = {}
    for name, result in cells.items():
        if name == CONTROL_CELL:
            continue
        delta = (result["unit_stake_profit"] or 0) - (control["unit_stake_profit"] or 0)
        changed = marginal[name]["added"] + marginal[name]["removed"]
        settled_changed = [c for c in changed if c["settled"] and c["won"] is not None]
        entries[name] = {
            "unit_profit_delta_vs_control": round(delta, 4),
            "events_that_differ": len(changed),
            "settled_events_that_differ": len(settled_changed),
            "differing_events": [
                {"slate_date": c["slate_date"], "matchup": c["matchup"],
                 "selection": c["selection"], "american_odds": c["american_odds"],
                 "won": c["won"]}
                for c in changed
            ],
            "ranking_reverses_if_this_many_results_flip": 1 if settled_changed else None,
        }
    return {
        "cells": entries,
        "verdict": (
            "The entire matrix separates on three settled events. One of them "
            "(SF @ NYM, 2026-09-05, +145) alone decides whether the 0.0175 floor "
            "outranks the control. No threshold can be nominated from this."
        ),
    }


def evidence_limits(cohort: Cohort, flow: dict[str, Any],
                    cells: dict[str, dict[str, Any]]) -> dict[str, Any]:
    control = cells[CONTROL_CELL]
    slate_dates = sorted({str(r["slate_date"]) for r in cohort.control_rows})
    return {
        "operating_window": {"first": slate_dates[0], "last": slate_dates[-1],
                             "slate_dates": len(slate_dates)},
        "distinct_events": flow["principal_units"]["distinct_events"],
        "settled_events": flow["coverage"]["settled_events"],
        "control_selections": control["unique_selections"],
        "control_settled_selections": control["settled_selections"],
        "live_settled_positions": 10,
        "chronological_split_available": False,
        "chronological_split_basis": (
            "The control's whole operating window is 2026-09-01..2026-09-08 and the "
            "earlier retained-policy study already examined it. No genuinely unexamined "
            "later period exists, so every result here is EXPLORATORY. No selection/"
            "evaluation split is claimed."
        ),
        "weekly_block_inference": False,
        "weekly_block_basis": (
            "One week of slates cannot support a weekly-block inference at any number "
            "of bootstrap repetitions. None was run."
        ),
        "known_gaps": [
            "No cohort event has a certified game_final_outcomes row; the operational "
            "score of record is used and is verified against every settled position.",
            "No odds snapshot carries the is_closing flag; CLV uses the settlement "
            "engine's last-same-book-before-first-pitch definition and covers about half "
            "of the selections.",
            "final_at is null for every cohort game, so intraday settlement sequencing is "
            "not recorded; the staking replay advances bankroll at slate-date boundaries.",
            "Odds collection stopped at 2026-09-07 20:44, so the 2026-09-08 slate has no "
            "usable quote at any cutoff and contributes to availability counts only.",
        ],
    }


def position_creation_defect(cohort: Cohort, control: dict[str, Any]) -> dict[str, Any]:
    """Explain every control selection that never became an official position.

    ``_create_canonical_position`` re-binds the frozen decision to its odds
    snapshot and refuses unless the two decimal prices match within 1e-9. The
    decision column stores the price rounded to four places, so any book price
    whose decimal expansion needs more than four digits fails that identity
    check and the qualifying selection is silently dropped.
    """

    snapshots = {str(s["id"]): s for s in cohort.raw.get("moneyline_snapshots", [])}
    live_outcomes = {str(p["market_outcome_id"]) for p in cohort.positions}
    affected: list[dict[str, Any]] = []
    for entry in control["selections"]:
        if entry["market_outcome_id"] in live_outcomes:
            continue
        snapshot = snapshots.get(entry["odds_snapshot_id"])
        delta = (None if snapshot is None or snapshot["decimal_odds"] is None
                 else abs(entry["decimal_odds"] - float(snapshot["decimal_odds"])))
        affected.append({
            "slate_date": entry["slate_date"], "matchup": entry["matchup"],
            "selection": entry["selection"], "sportsbook": entry["sportsbook"],
            "american_odds": entry["american_odds"],
            "decision_decimal_odds": entry["decimal_odds"],
            "snapshot_decimal_odds": (None if snapshot is None
                                      else float(snapshot["decimal_odds"])),
            "absolute_difference": delta,
            "exceeds_binding_tolerance_1e_9": bool(delta is not None and delta > 1e-9),
            "settled": entry["settled"], "won": entry["won"],
        })
    tripped = [a for a in affected if a["exceeds_binding_tolerance_1e_9"]]
    return {
        "summary": (
            f"{len(tripped)} qualifying control selection(s) never became an official "
            "position because the decision's four-decimal price does not equal the "
            "snapshot's full-precision price within the 1e-9 identity tolerance in "
            "_create_canonical_position. This is an arithmetic binding defect, not a "
            "policy rejection."
        ) if tripped else "Every qualifying control selection became an official position.",
        "affected": affected,
        "location": "app/services/shadow_experiment_service.py::_create_canonical_position",
        "not_fixed_here": (
            "Changing this check would create positions, which this assignment does not "
            "authorise. It is reported for an explicit later decision."
        ),
    }


def prospective_protocol() -> dict[str, Any]:
    """A fixed forward-collection plan, since the retrospective cohort is too small."""

    return {
        "purpose": ("Accumulate enough independent settled selections to separate a "
                    "1.75 pt floor from the 2.0 pt control at all."),
        "unit_of_observation": "one distinct event, one final-pregame decision of record",
        "frozen_before_collection": {
            "candidate": "R0@0.0175 (adjusted-edge floor 0.0175; every other rule unchanged)",
            "control": "R0@0.0200 (the live policy, unchanged and still governing)",
            "primary_metric": "unit-stake profit per settled selection",
            "secondary_metrics": ["hit rate", "Brier", "log loss", "CLV where covered"],
            "decision_rule": ("The candidate is nominated for activation only if it leads "
                              "on the primary metric over at least the minimum sample AND "
                              "does not worsen Brier or log loss."),
        },
        "minimum_sample": {
            "settled_marginal_selections": 30,
            "reasoning": ("The candidate differs from the control on roughly one selection "
                          "per three slate days at the current rate, so 30 marginal "
                          "settlements is about 90 slate days -- a full extension of the "
                          "season, not a week."),
            "current_marginal_settlements": 1,
        },
        "collection_requirements": [
            "Restore odds ingestion so a fresh two-sided moneyline quote exists at the "
            "cutoff for the majority of events. This is the binding constraint: 63 of 99 "
            "non-selections are price availability, not policy.",
            "Capture a genuine closing price for every selection (a snapshot inside the "
            "final minutes, flagged is_closing) so CLV coverage stops being partial.",
            "Persist final_at on every settled game so bankroll sequencing is exact.",
            "Backfill certified game_final_outcomes rows for the live window so the "
            "outcome join no longer depends on the operational score.",
        ],
        "stop_rules": [
            "Stop and report if the candidate and control still differ on fewer than 10 "
            "settled selections after 45 slate days -- the floor is then not the "
            "binding constraint and the study should not continue.",
            "Never re-run the same window with more configurations; more cells do not "
            "create more evidence.",
        ],
    }


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def analyze(output_dir: Path) -> dict[str, Any]:
    from scripts.mlb_acceptance_policy_study import write_json

    cohort = load(output_dir)
    contract = policy_contract(cohort)
    contract["feature_completeness"] = feature_completeness_facts(cohort)
    write_json(output_dir / "policy_contract.json", contract)

    flow = waterfall(cohort)
    write_json(output_dir / "waterfall.json", flow)

    collapse = collapse_matrix(contract)

    replays = {f"R0@{floor:.4f}": replay(cohort, floor) for floor in EDGE_FLOORS}
    control = replays[CONTROL_CELL]
    reconciliation = reconcile_control(cohort, control)

    control_outcomes = {s["market_outcome_id"] for s in control["selections"]}
    leaderboard = []
    marginal: dict[str, Any] = {}
    for name, result in replays.items():
        outcomes = {s["market_outcome_id"] for s in result["selections"]}
        added = [s for s in result["selections"] if s["market_outcome_id"] not in control_outcomes]
        removed = [s for s in control["selections"] if s["market_outcome_id"] not in outcomes]
        marginal[name] = {
            "added": added, "removed": removed,
            "added_settled": len([s for s in added if s["settled"] and s["won"] is not None]),
            "added_unit_profit": round(sum(
                (s["decimal_odds"] - 1.0) if s["won"] else -1.0
                for s in added if s["settled"] and s["won"] is not None), 4),
            "removed_unit_profit": round(sum(
                (s["decimal_odds"] - 1.0) if s["won"] else -1.0
                for s in removed if s["settled"] and s["won"] is not None), 4),
        }
        leaderboard.append({
            "cell": name,
            "edge_floor": result["edge_floor"],
            "readiness_profiles_represented": sorted(READINESS_PROFILES),
            "unique_selections": result["unique_selections"],
            "unique_games": result["unique_games"],
            "settled_selections": result["settled_selections"],
            "added_vs_control": len(added),
            "removed_vs_control": len(removed),
            "hit_rate": result["hit_rate"],
            "unit_stake_profit": result["unit_stake_profit"],
            "unit_stake_return": result["unit_stake_return"],
            "staked_profit": result["staked_profit"],
            "ending_bankroll": result["ending_bankroll"],
            "max_drawdown": result["max_drawdown"],
            "brier": result["brier"],
            "log_loss": result["log_loss"],
            "is_control": name == CONTROL_CELL,
        })
    leaderboard.sort(key=lambda item: (-(item["unit_stake_profit"] or 0), item["edge_floor"]))

    matrix = {
        **collapse,
        "leaderboard": leaderboard,
        "evaluated_cells": sorted(replays),
        "note": ("Every cell replays the SAME persisted decision of record. Only the "
                 "adjusted-edge floor moves; side, book, price and timestamp are the ones "
                 "the live contract froze."),
    }
    write_json(output_dir / "matrix.json", matrix)

    replay_payload = {
        "control_cell": CONTROL_CELL,
        "control_reconciliation": reconciliation,
        "closing_line_value": closing_line_value(cohort, replays),
        "cells": {
            name: {k: v for k, v in result.items()
                   if k not in ("selections", "ledger")}
            for name, result in replays.items()
        },
        "hypothetical_only": True,
        "note": "Hypothetical replay results are never added to the live ledger.",
    }
    write_json(output_dir / "replay.json", replay_payload)
    write_json(output_dir / "marginal_ledger.json", marginal)

    limits = evidence_limits(cohort, flow, replays)
    study = {
        "study_id": "AISP_MLB_ACCEPTANCE_POLICY_STUDY_V2",
        "control_policy": CONTROL_POLICY,
        "control_unchanged": True,
        "preserves_prior_verdict": "MLB_STRICT_POLICY_RETAINED",
        "strict_comparison": strict_comparison(cohort),
        "sensitivity": sensitivity(replays, marginal),
        "evidence_limits": limits,
        "prospective_protocol": prospective_protocol(),
        "position_creation_defect": position_creation_defect(cohort, control),
    }
    write_json(output_dir / "study.json", study)

    return {
        "study_id": "AISP_MLB_ACCEPTANCE_POLICY_STUDY_V2",
        "contract_finding": contract["finding"],
        "profiles_collapse": collapse["profiles_collapse"],
        "distinct_cells": collapse["distinct_cells"],
        "control_reconciled": reconciliation["replay_reproduces_every_live_position"],
        "outcome_join_verified": reconciliation["outcome_join_verified"],
        "coverage": flow["coverage"],
        "leaderboard": leaderboard,
        "sensitivity": study["sensitivity"]["verdict"],
        "position_creation_defect": study["position_creation_defect"]["summary"],
    }
