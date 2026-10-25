"""Model Performance Overview: three populations, decision-bound forecasts, one calculation.

This is the single calculation behind BOTH the Model Performance "Overview" view
(``GET /research/model-performance/overview``) and the offline Model Performance
Brief. The brief calls :func:`overview`, and a check asserts the brief payload
equals the endpoint's for the same read-only snapshot.

POPULATIONS (never merged)
--------------------------
1. **Forecast evaluations** -- current ``research_forecast_evaluations`` rows, one
   per (game, engine, engine version): the scorer's rule, the latest snapshot
   published before kickoff. Used for FORECAST ACCURACY (winner, margin/total
   error, interval coverage) and the evaluated forecast's own calibration.
2. **Decision opportunities** -- the governing FINAL_PREGAME decision per game in
   each experiment that holds official positions (MLB V2.1, MLB V18, NFL paper,
   CFB paper): the decision a funded position links to, else the latest decision
   recorded before kickoff. Every decision-level number -- model versus market,
   selection, selected-side calibration -- uses the forecast THAT DECISION
   references (never "the latest forecast before kickoff"), and only when that
   forecast's availability clock is at or before the decision time:

   ============  ===============================================  ==========================================
   sport/table    decision reference                               availability clock (must be <= decision)
   ============  ===============================================  ==========================================
   MLB V2.1       shadow_experiment_decisions.forecast_id          forecasts.available_as_of
   MLB V18, NFL   shadow_forecast_id (NFL: or evidence row id)     shadow_forecasts.available_as_of_utc
   CFB            cfb_research_forecast_id                         cfb_research_forecasts.imported_at
   ============  ===============================================  ==========================================

   A decision whose forecast was not available before the decision (or has no
   clock, or no linked forecast) is counted in an explicit excluded bucket.
3. **Funded positions** -- ``official_asp_positions``; returns and open exposure
   come from ``wager_results_read`` (the Wager Results tab), never from here.

MARKET REFERENCE
----------------
Median across books of the multiplicative no-vig home probability at a time t:
per (source, book, side) the latest MONEYLINE quote with observed_at <= t and
available_as_of <= t; a same-book pair counts when its older leg is <= 240 min
before t and its legs are <= 30 min apart; a book carried by two providers counts
once (fresher pair). At t = decision time this reproduces the all-game dataset's
``ref_p_home_mult``. "Comparable time" = the decision-bound forecast became
available no more than 6 h before the decision (and never after it). For CFB the
reference is also built at the forecast's own availability time.

All probability scores compared with the market are TWO-WAY, conditional on no
tie: p = home / (home + away). The stored ``brier_score`` is the native
three-class score (0-2); it equals twice the two-way score only when the tie
probability is zero and no tie occurred (:func:`brier_checks`). Signed errors are
predicted minus actual: a NEGATIVE total error is an underestimate.
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from math import exp, log, sqrt
from statistics import fmean, median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

SCHEMA_VERSION = "AISP_MODEL_PERFORMANCE_OVERVIEW_V2"
EPSILON = 1e-15
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20260927
MAX_QUOTE_AGE = timedelta(minutes=240)
MAX_PAIR_GAP = timedelta(minutes=30)
COMPARABLE_BEFORE = timedelta(hours=6)
CALIBRATION_BINS = 10
INTERVAL_LEVELS = ("50", "80", "90")
EASTERN = ZoneInfo("America/New_York")

FORECAST_TYPES = ("FINAL", "CURRENT", "PROSPECTIVE")
#: (key, label, lower hours inclusive, upper hours exclusive)
LEAD_BUCKETS: tuple[tuple[str, str, float, float | None], ...] = (
    ("LE_1H", "≤ 1 h", 0.0, 1.0),
    ("1_6H", "1–6 h", 1.0, 6.0),
    ("6_24H", "6–24 h", 6.0, 24.0),
    ("24_72H", "1–3 days", 24.0, 72.0),
    ("GT_72H", "> 3 days", 72.0, None),
)
MODEL_LABELS = {
    "MLB_V18_WHOLE_GAME": "MLB V18 whole-game",
    "MLB_ENSEMBLE_CANDIDATE": "MLB V18 candidate",
    "NFL_M1_XGBOOST": "NFL M1",
    "CFB_GEN1_JOINT_BASELINE": "CFB Gen1 joint baseline",
    "AISP_BASELINE": "MLB V2.1 baseline (AISP_BASELINE)",
}
SPORT_OF_DECISION = {"BASEBALL_MLB": "MLB", "MLB": "MLB", "NFL": "NFL", "CFB": "CFB"}
AVAILABILITY_FIELD = {
    "forecasts": "forecasts.available_as_of",
    "shadow_forecasts": "shadow_forecasts.available_as_of_utc",
    "cfb_research_forecasts": "cfb_research_forecasts.imported_at",
}

#: The active or proposed work each finding family belongs to. Findings are evidence for these, not decisions.
EXPERIMENTS = {
    "R2": "R2 market-anchored residual (Codex, frozen)",
    "CFB": "CFB opponent-adjusted specialist study (Codex; PROMISING_EXPLORATORY_SPECIALIST, Gen1 retained)",
    "NFL_TIE": "NFL tie-construction check (no defect found)",
    "CAPTURE": "Near-kickoff capture proposal (design, not active)",
    "REPAIRS": "Decision-repairs candidate (ordering re-check and alerts, pending review)",
}

METHODOLOGY = {
    "populations": (
        "Three populations are kept apart. Forecast evaluations: current research_forecast_evaluations rows, one per "
        "game and model (the scorer's rule: latest snapshot published before kickoff) -- used for forecast accuracy. "
        "Decision opportunities: the governing FINAL_PREGAME decision per game (the funded one, else the latest before "
        "kickoff) in the experiments that hold official positions -- used for model-versus-market and selection, with "
        "the forecast THAT DECISION references. Funded positions: official_asp_positions; returns come from Wager Results."
    ),
    "decision_bound_forecast": (
        "MLB V2.1: decision.forecast_id → forecasts, available when forecasts.available_as_of ≤ decision time. MLB V18 "
        "and NFL: decision.shadow_forecast_id (NFL also evidence.shadow_forecast_row_id) → shadow_forecasts, available "
        "when available_as_of_utc ≤ decision time. CFB: decision.cfb_research_forecast_id → cfb_research_forecasts, "
        "available when imported_at ≤ decision time. There is no allowance for a forecast that became available after "
        "the decision: such decisions are counted as excluded."
    ),
    "forecast_type": (
        "FINAL = FINAL_PREGAME snapshot (about 30 min before kickoff). CURRENT = a rolling CURRENT snapshot (NFL: "
        "25–155 h before kickoff where no FINAL_PREGAME existed). PROSPECTIVE = the single pregame publication type "
        "of the MLB V18 and CFB producers (and the V2.1 EARLY/DRAFT forecasts). Lead = hours from availability "
        "(evaluations: publication) to kickoff."
    ),
    "brier": (
        "Native Brier = squared error summed over home, away and tie (0–2), as stored. Conditional two-way Brier = "
        "(p − y)² with p = home / (home + away) on non-tie games. Only the conditional two-way score is compared with "
        "the market, which is two-way. Native = 2 × two-way only when p_tie = 0 and no tie occurred."
    ),
    "market": (
        "Median across books of the multiplicative no-vig home probability at time t, from retained MONEYLINE quotes "
        "observed and available at or before t; same-book pairs with the older leg ≤ 240 min before t and legs ≤ 30 "
        "min apart; a book carried by two providers counts once. At the decision time it equals the all-game "
        "dataset's ref_p_home_mult. For CFB it is also built at the forecast's availability time."
    ),
    "comparable_time": (
        "Comparable = the decision-bound forecast became available at most 6 h before the decision and not after it. "
        "Decision-time comparisons include older forecasts and are labelled with the forecast's age."
    ),
    "deltas": (
        "Paired differences are model minus market on the same games; negative favours the model. 95% intervals are "
        f"percentile bootstraps over games ({BOOTSTRAP_RESAMPLES} resamples, seed {BOOTSTRAP_SEED}); proportions use "
        "Wilson intervals. Same-day games are correlated, so intervals are optimistic."
    ),
    "calibration": (
        "Reliability uses ONE row per game, the HOME side (pooling both sides forces a 50% mean). Slope from a "
        "logistic fit of the outcome on logit(p); below 1 = over-confident."
    ),
    "selection": (
        "Selected = the governing decision was QUALIFIED_BET. Selected-side win rate is compared with the decision-bound "
        "model's average probability for that side; a conclusion is stated only when the Wilson interval excludes it."
    ),
    "favourite_vs_wager": (
        "Forecast favourite = the evaluated forecast's side above 50% (two-way), with whether it won. Wagered side = "
        "the side of an official funded position on that game, with its W/L/push/open result. They are different "
        "things: a called winner is not a wager outcome."
    ),
    "signed_error": (
        "Signed error = predicted minus actual; margin is home minus away. Negative total error = underestimate."
    ),
    "intervals": (
        "Interval coverage uses the stored 50/80/90% predictive intervals from each evaluated forecast's saved draws."
    ),
    "proposals": (
        "Freshness blocks, market-anchored blending and interval recalibration are separate proposals that each need "
        "their own tested change. Nothing on this page decides or applies them."
    ),
    "authority": (
        "This view scores forecasts. It creates no position, changes no policy or threshold and promotes no model. "
        "Official wagers are a separate population shown in the Wager Results tab."
    ),
}


# ----------------------------------------------------------------- basics ---

def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.astimezone(UTC).isoformat() if value else None


def et_date(iso: str | None) -> str | None:
    """The US-Eastern calendar date of a UTC ISO timestamp (the slate date)."""

    if not iso:
        return None
    return datetime.fromisoformat(iso).astimezone(EASTERN).date().isoformat()


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def forecast_type(snapshot_type: str | None) -> str:
    """FINAL / CURRENT / PROSPECTIVE, from the scored snapshot's type."""

    kind = (snapshot_type or "").upper()
    if kind == "FINAL_PREGAME":
        return "FINAL"
    if kind == "CURRENT":
        return "CURRENT"
    return "PROSPECTIVE"


def lead_bucket(hours: float | None) -> str | None:
    if hours is None:
        return None
    for key, _label, lo, hi in LEAD_BUCKETS:
        if hours >= lo and (hi is None or hours < hi):
            return key
    return LEAD_BUCKETS[0][0] if hours < 0 else None


def two_way(home: float | None, away: float | None) -> float | None:
    """Home probability conditional on no tie."""

    if home is None or away is None or (home + away) <= 0:
        return None
    return float(home) / float(home + away)


def log_loss_two_way(p_home: float, home_won: bool) -> float:
    p = p_home if home_won else 1.0 - p_home
    return -log(min(max(p, EPSILON), 1.0))


def brier_two_way(p_home: float, home_won: bool) -> float:
    return (p_home - (1.0 if home_won else 0.0)) ** 2


def brier_three_class(home: float, away: float, tie: float, outcome: str) -> float:
    """The STORED convention: squared error summed over home, away and tie (0-2)."""

    target = {"HOME": (1, 0, 0), "AWAY": (0, 1, 0), "TIE": (0, 0, 1)}[outcome]
    return sum((p - y) ** 2 for p, y in zip((home, away, tie), target))


def two_way_brier_from_probabilities(home: float, away: float, outcome: str) -> float | None:
    """Two-way Brier on the renormalised home probability; None for a tie (not scored two-way)."""

    if outcome not in ("HOME", "AWAY"):
        return None
    p = two_way(home, away)
    return None if p is None else brier_two_way(p, outcome == "HOME")


def signed_error(predicted: float | None, actual: float | None) -> float | None:
    """Predicted minus actual: negative = forecast too low (underestimate)."""

    if predicted is None or actual is None:
        return None
    return float(predicted) - float(actual)


def error_direction(error: float | None) -> str | None:
    if error is None:
        return None
    if error > 0:
        return "OVERESTIMATE"
    if error < 0:
        return "UNDERESTIMATE"
    return "EXACT"


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    spread = z / denom * sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [max(0.0, centre - spread), min(1.0, centre + spread)]


@lru_cache(maxsize=512)
def _resample_indices(n: int, resamples: int, seed: int) -> tuple[tuple[int, ...], ...]:
    """The resample index sets for a sample of size n. Same RNG sequence for every metric of that size."""

    rng = random.Random(seed)
    return tuple(tuple(rng.randrange(n) for _ in range(n)) for _ in range(resamples))


def bootstrap_mean_ci(values: list[float], *, resamples: int = BOOTSTRAP_RESAMPLES,
                      seed: int = BOOTSTRAP_SEED) -> list[float] | None:
    """Percentile 95% CI of the mean, resampling games. Deterministic for a given input order."""

    n = len(values)
    if n < 2:
        return None
    pick = values.__getitem__
    means = sorted(sum(map(pick, idx)) / n for idx in _resample_indices(n, resamples, seed))
    return [means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]]


def _mean(values: list[float]) -> float | None:
    return fmean(values) if values else None


def logistic_calibration(points: list[tuple[float, bool]]) -> dict[str, float] | None:
    """Fit y ~ a + b*logit(p) by Newton-Raphson. Slope b < 1 = over-confident."""

    xs, ys = [], []
    for p, won in points:
        p = min(max(p, 1e-6), 1 - 1e-6)
        xs.append(log(p / (1 - p)))
        ys.append(1.0 if won else 0.0)
    if len(xs) < 20 or len(set(ys)) < 2:
        return None
    a, b = 0.0, 1.0
    for _ in range(50):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            z = a + b * x
            mu = 1 / (1 + exp(-z)) if z > -700 else 0.0
            w = mu * (1 - mu)
            ga += y - mu
            gb += (y - mu) * x
            haa += w
            hab += w * x
            hbb += w * x * x
        det = haa * hbb - hab * hab
        if det <= 1e-12:
            return None
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a + da, b + db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    return {"intercept": a, "slope": b}


# ------------------------------------------------------- market reference ---

def canonical_book(key: str) -> str:
    key = (key or "").replace("sgo:", "")
    return {"williamhill_us": "williamhill"}.get(key, key)


def market_reference(quotes: Iterable[dict[str, Any]], at: datetime) -> dict[str, Any] | None:
    """Median-of-books multiplicative no-vig home probability at ``at``.

    ``quotes``: dicts with side (HOME/AWAY), book, source, observed_at, available_as_of, decimal.
    """

    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for q in sorted(quotes, key=lambda q: (q["observed_at"], q["available_as_of"])):
        if q["side"] not in ("HOME", "AWAY") or not q["decimal"] or q["decimal"] <= 1.0:
            continue
        if q["observed_at"] <= at and q["available_as_of"] <= at:
            latest[(q["source"], q["book"], q["side"])] = q
    pairs: dict[str, dict[str, Any]] = {}
    for (source, book, side), home in latest.items():
        if side != "HOME" or (source, book, "AWAY") not in latest:
            continue
        away = latest[(source, book, "AWAY")]
        if at - min(home["observed_at"], away["observed_at"]) > MAX_QUOTE_AGE:
            continue
        if abs(home["observed_at"] - away["observed_at"]) > MAX_PAIR_GAP:
            continue
        name = canonical_book(book)
        newest = max(home["observed_at"], away["observed_at"])
        if name in pairs and pairs[name]["newest"] >= newest:
            continue
        ih, ia = 1 / home["decimal"], 1 / away["decimal"]
        pairs[name] = {"newest": newest, "oldest": min(home["observed_at"], away["observed_at"]),
                       "p_home": ih / (ih + ia)}
    if not pairs:
        return None
    return {
        "reference_time_utc": _iso(at),
        "p_home": median(p["p_home"] for p in pairs.values()),
        "n_books": len(pairs),
        "books": sorted(pairs),
        "oldest_leg_utc": _iso(min(p["oldest"] for p in pairs.values())),
        "newest_leg_utc": _iso(max(p["newest"] for p in pairs.values())),
    }


def earliest_reference(quotes: list[dict[str, Any]], start: datetime, end: datetime) -> dict[str, Any] | None:
    """The first time t in [start, end] (a quote clock) at which :func:`market_reference` yields a reference."""

    times = sorted({max(q["observed_at"], q["available_as_of"]) for q in quotes} | {start})
    for t in times:
        if t < start or t > end:
            continue
        ref = market_reference(quotes, t)
        if ref:
            return ref
    return None


def governing_decision(decisions: list[dict[str, Any]], kickoff: datetime) -> tuple[dict[str, Any], str] | None:
    """The all-game dataset rule: funded decision, else latest before kickoff, else latest."""

    if not decisions:
        return None
    funded = [d for d in decisions if d.get("funded")]
    if funded:
        return max(funded, key=lambda d: d["decision_time"]), "FUNDED_POSITION_DECISION"
    before = [d for d in decisions if d["decision_time"] < kickoff]
    if before:
        return max(before, key=lambda d: d["decision_time"]), "LATEST_FINAL_PREGAME_BEFORE_KICKOFF"
    return max(decisions, key=lambda d: d["decision_time"]), "LATEST_FINAL_PREGAME_NONE_BEFORE_KICKOFF"


def is_comparable(available: datetime | None, decision_time: datetime | None) -> bool:
    """Available at most 6 h before the decision and NOT after it (no future-publication allowance)."""

    if available is None or decision_time is None:
        return False
    return decision_time - COMPARABLE_BEFORE <= available <= decision_time


def availability_status(forecast: dict[str, Any] | None, decision_time: datetime) -> str:
    if not forecast or forecast.get("p_home") is None:
        return "NO_FORECAST_LINKED"
    if forecast.get("available_utc") is None:
        return "NO_AVAILABILITY_CLOCK"
    return "AVAILABLE" if forecast["available_utc"] <= decision_time else "NOT_AVAILABLE_BEFORE_DECISION"


def primary_evaluations(rows: list[Any]) -> list[Any]:
    """Enforce the scorer's rule: one current evaluation per (game, engine, version).

    If two current rows ever collide, keep the latest pre-kickoff publication (the
    scorer's CONTROLLING_REASON). Ordering of the survivors is by kickoff.
    """

    keep: dict[tuple[Any, str, str], Any] = {}
    for row in rows:
        if getattr(row, "is_current", True) is False:
            continue
        key = (row.canonical_game_id or row.event_id, row.engine_id, row.engine_version)
        prior = keep.get(key)
        published = _aware(row.forecast_published_utc)
        if prior is None or (published or datetime.min.replace(tzinfo=UTC)) > (
                _aware(prior.forecast_published_utc) or datetime.min.replace(tzinfo=UTC)):
            keep[key] = row
    return sorted(keep.values(), key=lambda r: (_aware(r.kickoff_utc), r.engine_id, r.engine_version))


def model_label(engine_id: str, version: str | None = None) -> str:
    base = MODEL_LABELS.get(engine_id, engine_id.replace("_", " ").title())
    if engine_id.endswith("_CANDIDATE") and version:
        variant = version.replace("mlb-v18-", "").replace("-v1", "").replace("-", " ")
        return f"{base} · {variant}"
    return base


def _outcome(home: int | None, away: int | None) -> str | None:
    if home is None or away is None:
        return None
    return "HOME" if home > away else "AWAY" if away > home else "TIE"


# ---------------------------------------------------------- decision record ---

def decision_record(d: dict[str, Any]) -> dict[str, Any]:
    """One governing decision with the forecast it references. Pure.

    ``d`` keys: sport, experiment_version, engine_id, decision_id, game_id, home, away, kickoff, decision_time,
    governing_rule, n_decisions, recommendation, selected_side, funded, position (side/result/status/stake or None),
    forecast (table/row_id/snapshot_type/available_utc/published_utc/p_home/p_away/p_tie or None),
    final ({home, away} or None), market (at decision time or None), market_at_availability (or None),
    evaluation_forecast_same (bool or None).
    """

    T = d["decision_time"]
    fc = d.get("forecast") or {}
    status = availability_status(fc, T)
    available = status == "AVAILABLE"
    p2 = two_way(fc.get("p_home"), fc.get("p_away")) if fc else None
    final = d.get("final")
    outcome = _outcome(final["home"], final["away"]) if final else None
    decided = outcome in ("HOME", "AWAY")
    q = (d.get("market") or {}).get("p_home")
    qa = (d.get("market_at_availability") or {}).get("p_home")
    age = ((T - fc["available_utc"]).total_seconds() / 3600) if fc.get("available_utc") else None
    lead = ((d["kickoff"] - fc["available_utc"]).total_seconds() / 3600) if fc.get("available_utc") else None
    usable = available and p2 is not None and decided
    m_ll = log_loss_two_way(p2, outcome == "HOME") if usable else None
    m_br = brier_two_way(p2, outcome == "HOME") if usable else None
    k_ll = log_loss_two_way(q, outcome == "HOME") if (q is not None and decided) else None
    k_br = brier_two_way(q, outcome == "HOME") if (q is not None and decided) else None
    ka_ll = log_loss_two_way(qa, outcome == "HOME") if (qa is not None and decided) else None
    ka_br = brier_two_way(qa, outcome == "HOME") if (qa is not None and decided) else None
    side = d.get("selected_side") if d.get("selected_side") in ("HOME", "AWAY") else None
    selected = d["recommendation"] == "QUALIFIED_BET"
    comparable = available and is_comparable(fc.get("available_utc"), T)
    return {
        "decision_id": d["decision_id"],
        "sport": d["sport"],
        "experiment_version": d["experiment_version"],
        "engine_id": d["engine_id"],
        "model": model_label(d["engine_id"]),
        "game_id": str(d["game_id"]) if d.get("game_id") else None,
        "home": d.get("home"), "away": d.get("away"),
        "kickoff_utc": _iso(d["kickoff"]),
        "slate_date": et_date(_iso(d["kickoff"])),
        "decision_time_utc": _iso(T),
        "minutes_before_kickoff": round((d["kickoff"] - T).total_seconds() / 60, 1),
        "governing_rule": d["governing_rule"],
        "n_final_pregame_decisions": d.get("n_decisions"),
        "recommendation": d["recommendation"],
        "selected": selected,
        "selected_side": side,
        "funded": bool(d.get("funded")),
        "position": d.get("position"),
        "forecast": None if not fc else {
            "table": fc.get("table"), "row_id": fc.get("row_id"), "snapshot": fc.get("snapshot"),
            "availability_field": AVAILABILITY_FIELD.get(fc.get("table") or ""),
            "available_utc": _iso(fc.get("available_utc")), "published_utc": _iso(fc.get("published_utc")),
            "p_home": fc.get("p_home"), "p_away": fc.get("p_away"), "p_tie": fc.get("p_tie"),
        },
        "forecast_type": forecast_type(fc.get("snapshot_type")),
        "availability_status": status,
        "forecast_age_hours": round(age, 3) if age is not None else None,
        "lead_hours": round(lead, 3) if lead is not None else None,
        "lead_bucket": lead_bucket(lead),
        "comparable": comparable,
        "evaluation_forecast_same": d.get("evaluation_forecast_same"),
        "p_home_two_way": p2,
        "final": final,
        "actual_outcome": outcome,
        "market": d.get("market"),
        "market_at_availability": d.get("market_at_availability"),
        "market_earliest_retained": d.get("market_earliest_retained"),
        "model_log_loss_two_way": m_ll, "model_brier_two_way": m_br,
        "market_log_loss_two_way": k_ll, "market_brier_two_way": k_br,
        "delta_log_loss": (m_ll - k_ll) if (m_ll is not None and k_ll is not None) else None,
        "delta_brier": (m_br - k_br) if (m_br is not None and k_br is not None) else None,
        "market_at_availability_log_loss_two_way": ka_ll, "market_at_availability_brier_two_way": ka_br,
        "delta_log_loss_at_availability": (m_ll - ka_ll) if (m_ll is not None and ka_ll is not None) else None,
        "delta_brier_at_availability": (m_br - ka_br) if (m_br is not None and ka_br is not None) else None,
        "selected_side_model_p": (p2 if side == "HOME" else 1 - p2) if (side and p2 is not None and available) else None,
        "selected_side_market_p": (q if side == "HOME" else 1 - q) if (side and q is not None) else None,
        "selected_side_won": (side == outcome) if (side and decided) else None,
    }


# ------------------------------------------------------------ game record ---

def game_record(row: Any, *, teams: dict[str, Any] | None = None,
                decision: dict[str, Any] | None = None) -> dict[str, Any]:
    """One evaluation (forecast accuracy), plus the governing decision record for the same game and model."""

    detail = row.detail or {}
    probs = detail.get("published_probabilities") or {}
    ph, pa, pt = _f(probs.get("home")), _f(probs.get("away")), _f(probs.get("tie"))
    p2 = two_way(ph, pa)
    outcome = row.actual_outcome
    decided = outcome in ("HOME", "AWAY")
    lead = (row.published_before_kickoff_seconds / 3600.0) if row.published_before_kickoff_seconds is not None else None
    teams = teams or {}
    coverage = row.interval_coverage or {}
    margin_error = _f(row.margin_error)
    total_error = _f(row.total_error)
    favourite = None if p2 is None or p2 == 0.5 else ("HOME" if p2 > 0.5 else "AWAY")
    position = (decision or {}).get("position")
    return {
        "evaluation_id": str(row.id),
        "game_id": str(row.canonical_game_id) if row.canonical_game_id else None,
        "sport": row.league_code,
        "engine_id": row.engine_id,
        "engine_version": row.engine_version,
        "model_key": f"{row.engine_id}|{row.engine_version}",
        "model": model_label(row.engine_id, row.engine_version),
        "kickoff_utc": _iso(row.kickoff_utc),
        "slate_date": et_date(_iso(row.kickoff_utc)),
        "home": (teams.get("home") or {}).get("abbreviation"),
        "away": (teams.get("away") or {}).get("abbreviation"),
        "forecast_type": forecast_type(row.snapshot_type),
        "snapshot": f"{row.snapshot_type or '—'} v{row.snapshot_version}" if row.snapshot_version is not None
        else (row.snapshot_type or "—"),
        "published_utc": _iso(row.forecast_published_utc),
        "lead_hours": round(lead, 3) if lead is not None else None,
        "lead_bucket": lead_bucket(lead),
        "publication_clock": (detail.get("publication_clock") or {}).get("classification"),
        "p_home": ph, "p_away": pa, "p_tie": pt, "p_home_two_way": p2,
        "final": {"home": row.home_score, "away": row.away_score},
        "actual_outcome": outcome,
        "forecast_favourite": favourite,
        "forecast_favourite_p": (p2 if favourite == "HOME" else 1 - p2) if favourite else None,
        "forecast_favourite_correct": (favourite == outcome) if (favourite and outcome) else None,
        "log_loss_stored": _f(row.log_loss),
        "brier_native_stored": _f(row.brier_score),
        "brier_native_recomputed": brier_three_class(ph, pa, pt or 0.0, outcome)
        if (ph is not None and pa is not None and outcome in ("HOME", "AWAY", "TIE")) else None,
        "log_loss_two_way": log_loss_two_way(p2, outcome == "HOME") if (p2 is not None and decided) else None,
        "brier_two_way": brier_two_way(p2, outcome == "HOME") if (p2 is not None and decided) else None,
        "predicted_home": _f(row.predicted_home_score),
        "predicted_away": _f(row.predicted_away_score),
        "predicted_margin": _f(row.predicted_margin),
        "actual_margin": row.actual_margin,
        "margin_error": margin_error,
        "margin_direction": error_direction(margin_error),
        "predicted_total": _f(row.predicted_total),
        "actual_total": row.actual_total,
        "total_error": total_error,
        "total_direction": error_direction(total_error),
        "interval_coverage": {
            kind: {level: bool(((coverage.get(kind) or {}).get(level) or {}).get("contains"))
                   for level in INTERVAL_LEVELS if (coverage.get(kind) or {}).get(level)}
            for kind in ("margin", "total") if coverage.get(kind)
        },
        "wager": None if not position else {
            "side": position.get("side"), "result": position.get("result"), "status": position.get("status"),
            "stake": position.get("stake"), "position_id": position.get("position_id")},
        "decision": None if not decision else {
            k: decision[k] for k in (
                "decision_id", "experiment_version", "decision_time_utc", "minutes_before_kickoff", "governing_rule",
                "recommendation", "selected", "selected_side", "funded", "availability_status", "forecast_age_hours",
                "comparable", "evaluation_forecast_same", "p_home_two_way", "delta_log_loss", "delta_brier",
                "n_final_pregame_decisions", "forecast")} | {
            "market_p_home": (decision.get("market") or {}).get("p_home"),
            "market_n_books": (decision.get("market") or {}).get("n_books"),
            "market_books": (decision.get("market") or {}).get("books")},
    }


# ---------------------------------------------------------------- metrics ---

def brier_checks(games: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Independent recomputation of native (three-class) and conditional two-way Brier. Pure."""

    rows = [g for g in games if g["brier_native_recomputed"] is not None]
    if not rows:
        return None
    stored = [g for g in rows if g["brier_native_stored"] is not None]
    diffs = [abs(g["brier_native_stored"] - g["brier_native_recomputed"]) for g in stored]
    decided = [g for g in rows if g["brier_two_way"] is not None]
    twice = [g for g in decided if abs(g["brier_native_recomputed"] - 2 * g["brier_two_way"]) < 1e-9]
    fails = [g for g in decided if abs(g["brier_native_recomputed"] - 2 * g["brier_two_way"]) >= 1e-9]
    ties = [g for g in rows if g["actual_outcome"] == "TIE"]
    return {
        "n": len(rows),
        "native_stored_mean": _mean([g["brier_native_stored"] for g in stored]),
        "native_recomputed_mean": _mean([g["brier_native_recomputed"] for g in rows]),
        "native_max_abs_diff": max(diffs) if diffs else None,
        "native_matches_stored": sum(1 for d in diffs if d < 1e-9),
        "conditional_two_way_mean": _mean([g["brier_two_way"] for g in decided]),
        "conditional_two_way_n": len(decided),
        "twice_two_way_holds": len(twice),
        "twice_two_way_fails": len(fails),
        "fails_with_tie_mass": sum(1 for g in fails if (g["p_tie"] or 0) > 1e-12),
        "mean_p_tie": _mean([g["p_tie"] or 0.0 for g in rows]),
        "max_native_minus_twice_two_way": max((g["brier_native_recomputed"] - 2 * g["brier_two_way"] for g in decided),
                                              default=None),
        "ties_occurred": len(ties),
        "market_comparison_uses": "conditional two-way Brier (p = home / (home + away), non-tie games)",
    }


def group_metrics(games: list[dict[str, Any]]) -> dict[str, Any]:
    """Forecast accuracy and the evaluated forecast's own probability quality for one slice. Pure."""

    decided = [g for g in games if g["actual_outcome"] in ("HOME", "AWAY") and g["p_home_two_way"] is not None]
    ll = [g["log_loss_two_way"] for g in decided]
    br = [g["brier_two_way"] for g in decided]
    points = [(g["p_home_two_way"], g["actual_outcome"] == "HOME") for g in decided]
    bins = []
    for index in range(CALIBRATION_BINS):
        lo, hi = index / CALIBRATION_BINS, (index + 1) / CALIBRATION_BINS
        members = [(p, won) for p, won in points if lo <= p < hi or (index == CALIBRATION_BINS - 1 and p == 1.0)]
        if members:
            wins = sum(1 for _, won in members if won)
            bins.append({"lower": lo, "upper": hi, "n": len(members), "mean_predicted": fmean(p for p, _ in members),
                         "observed": wins / len(members), "observed_ci95": wilson(wins, len(members))})
    gap = [p - (1.0 if won else 0.0) for p, won in points]
    margin = [g["margin_error"] for g in games if g["margin_error"] is not None]
    total = [g["total_error"] for g in games if g["total_error"] is not None]
    coverage: dict[str, dict[str, Any]] = {}
    for kind in ("margin", "total"):
        for level in INTERVAL_LEVELS:
            hits = [g["interval_coverage"][kind][level] for g in games
                    if kind in g["interval_coverage"] and level in g["interval_coverage"][kind]]
            if hits:
                coverage.setdefault(kind, {})[level] = {
                    "nominal": int(level) / 100, "n": len(hits), "contained": sum(hits),
                    "observed": sum(hits) / len(hits), "observed_ci95": wilson(sum(hits), len(hits))}
    fav = [g for g in games if g["forecast_favourite_correct"] is not None]
    kick = [g["kickoff_utc"] for g in games if g["kickoff_utc"]]
    return {
        "n": len(games),
        "decided_two_way": len(decided),
        "ties": sum(1 for g in games if g["actual_outcome"] == "TIE"),
        "period": {"first_utc": min(kick) if kick else None, "last_utc": max(kick) if kick else None},
        "dates": len({et_date(k) for k in kick}),
        "favourite_n": len(fav),
        "favourite_correct": sum(1 for g in fav if g["forecast_favourite_correct"]),
        "favourite_accuracy": (sum(1 for g in fav if g["forecast_favourite_correct"]) / len(fav)) if fav else None,
        "log_loss_two_way": _mean(ll),
        "log_loss_two_way_ci95": bootstrap_mean_ci(ll),
        "brier_two_way": _mean(br),
        "brier_native_stored": _mean([g["brier_native_stored"] for g in games if g["brier_native_stored"] is not None]),
        "mean_home_probability": _mean([p for p, _ in points]),
        "home_win_rate": (sum(1 for _, won in points if won) / len(points)) if points else None,
        "calibration_in_the_large": _mean(gap),
        "calibration_in_the_large_ci95": bootstrap_mean_ci(gap),
        "calibration_fit": logistic_calibration(points),
        "calibration_bins": bins,
        "brier_check": brier_checks(games),
        "margin": None if not margin else {
            "n": len(margin), "bias": fmean(margin), "bias_ci95": bootstrap_mean_ci(margin),
            "mae": fmean(abs(v) for v in margin)},
        "total": None if not total else {
            "n": len(total), "bias": fmean(total), "bias_ci95": bootstrap_mean_ci(total),
            "mae": fmean(abs(v) for v in total),
            "underestimates": sum(1 for v in total if v < 0), "overestimates": sum(1 for v in total if v > 0)},
        "interval_coverage": coverage,
    }


def _paired(rows: list[dict[str, Any]], model_ll: str, market_ll: str, model_br: str, market_br: str,
            delta_ll: str, delta_br: str) -> dict[str, Any] | None:
    rows = [r for r in rows if r[delta_ll] is not None]
    if not rows:
        return None
    d_ll = [r[delta_ll] for r in rows]
    d_br = [r[delta_br] for r in rows]
    return {
        "n": len(rows),
        "dates": len({r["slate_date"] for r in rows}),
        "model_log_loss_two_way": fmean(r[model_ll] for r in rows),
        "market_log_loss_two_way": fmean(r[market_ll] for r in rows),
        "delta_log_loss": fmean(d_ll), "delta_log_loss_ci95": bootstrap_mean_ci(d_ll),
        "model_brier_two_way": fmean(r[model_br] for r in rows),
        "market_brier_two_way": fmean(r[market_br] for r in rows),
        "delta_brier": fmean(d_br), "delta_brier_ci95": bootstrap_mean_ci(d_br),
        "model_better_games": sum(1 for d in d_ll if d < 0),
        "median_forecast_age_hours": median(r["forecast_age_hours"] for r in rows if r["forecast_age_hours"] is not None)
        if any(r["forecast_age_hours"] is not None for r in rows) else None,
    }


def _side_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sides = [r for r in rows if r["selected_side_won"] is not None and r["selected_side_model_p"] is not None]
    wins = sum(1 for r in sides if r["selected_side_won"])
    ci = wilson(wins, len(sides))
    model_p = _mean([r["selected_side_model_p"] for r in sides])
    with_mkt = [r for r in sides if r["selected_side_market_p"] is not None]
    market_p = _mean([r["selected_side_market_p"] for r in with_mkt])
    paired = _paired(rows, "model_log_loss_two_way", "market_log_loss_two_way", "model_brier_two_way",
                     "market_brier_two_way", "delta_log_loss", "delta_brier")
    return {
        "n": len(rows), "side_n": len(sides), "side_wins": wins,
        "side_win_rate": (wins / len(sides)) if sides else None, "side_win_rate_ci95": ci,
        "side_mean_model_p": model_p, "side_market_n": len(with_mkt), "side_mean_market_p": market_p,
        "ci_excludes_model_p": bool(ci and model_p is not None and not (ci[0] <= model_p <= ci[1])),
        "ci_excludes_market_p": bool(ci and market_p is not None and not (ci[0] <= market_p <= ci[1])),
        "paired": paired,
    }


def decision_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Decision-bound model versus market and selection for one (sport, engine) slice. Pure."""

    status: dict[str, int] = defaultdict(int)
    for r in rows:
        status[r["availability_status"]] += 1
    available = [r for r in rows if r["availability_status"] == "AVAILABLE"]
    both = [r for r in rows if r["evaluation_forecast_same"] is not None]
    return {
        "governing_decisions": len(rows),
        "final": sum(1 for r in rows if r["actual_outcome"] is not None),
        "availability": dict(status),
        "excluded_not_available_before_decision": status.get("NOT_AVAILABLE_BEFORE_DECISION", 0),
        "excluded_no_clock_or_forecast": status.get("NO_AVAILABILITY_CLOCK", 0) + status.get("NO_FORECAST_LINKED", 0),
        "with_market": sum(1 for r in available if r["market"]),
        "evaluation_joined": len(both),
        "evaluation_forecast_differs": sum(1 for r in both if r["evaluation_forecast_same"] is False),
        "market_decision_time": _paired(available, "model_log_loss_two_way", "market_log_loss_two_way",
                                        "model_brier_two_way", "market_brier_two_way", "delta_log_loss", "delta_brier"),
        "market_comparable": _paired([r for r in available if r["comparable"]], "model_log_loss_two_way",
                                     "market_log_loss_two_way", "model_brier_two_way", "market_brier_two_way",
                                     "delta_log_loss", "delta_brier"),
        "selection": {
            "selected": _side_block([r for r in available if r["selected"]]),
            "not_selected": _side_block([r for r in available if not r["selected"]]),
            "funded": _side_block([r for r in available if r["funded"]]),
        },
    }


def summarize(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluation rows per (sport, model) and per (sport, model, forecast type). Never pools sports."""

    out = []
    by_model: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for g in games:
        by_model[(g["sport"], g["model_key"])].append(g)
    for (sport, key), rows in sorted(by_model.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
        types = sorted({g["forecast_type"] for g in rows}, key=FORECAST_TYPES.index)
        out.append({"sport": sport, "model_key": key, "model": rows[0]["model"], "forecast_type": "ALL",
                    **group_metrics(rows)})
        if len(types) > 1:
            for kind in types:
                subset = [g for g in rows if g["forecast_type"] == kind]
                out.append({"sport": sport, "model_key": key, "model": rows[0]["model"], "forecast_type": kind,
                            **group_metrics(subset)})
    return out


def summarize_decisions(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in decisions:
        by[(r["sport"], r["engine_id"], r["experiment_version"])].append(r)
    return [{"sport": s, "engine_id": e, "experiment_version": v, "model": model_label(e), **decision_metrics(rows)}
            for (s, e, v), rows in sorted(by.items())]


def _cfb_pair(rows: list[dict[str, Any]], ref_key: str) -> dict[str, Any] | None:
    """Paired model-vs-market (two-way) with the market taken from ``ref_key`` on each row."""

    if not rows:
        return None
    d_ll, d_br, m_ll, k_ll, m_br, k_br = [], [], [], [], [], []
    for r in rows:
        won = r["actual_outcome"] == "HOME"
        p, q = r["p_home_two_way"], r[ref_key]["p_home"]
        m_ll.append(log_loss_two_way(p, won)); k_ll.append(log_loss_two_way(q, won))
        m_br.append(brier_two_way(p, won)); k_br.append(brier_two_way(q, won))
        d_ll.append(m_ll[-1] - k_ll[-1]); d_br.append(m_br[-1] - k_br[-1])
    return {"n": len(rows), "dates": len({r["slate_date"] for r in rows}),
            "model_log_loss_two_way": fmean(m_ll), "market_log_loss_two_way": fmean(k_ll),
            "delta_log_loss": fmean(d_ll), "delta_log_loss_ci95": bootstrap_mean_ci(d_ll),
            "model_brier_two_way": fmean(m_br), "market_brier_two_way": fmean(k_br),
            "delta_brier": fmean(d_br), "delta_brier_ci95": bootstrap_mean_ci(d_br),
            "model_better_games": sum(1 for d in d_ll if d < 0)}


def _cfb_movement(rows: list[dict[str, Any]], early_key: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Change in model-market disagreement from ``early_key`` to decision time. The model p is fixed."""

    change, signed_change, toward, listing = [], [], [], []
    for r in rows:
        p = r["p_home_two_way"]
        q0, q1 = r[early_key]["p_home"], r["market"]["p_home"]
        model_side = "HOME" if p >= q0 else "AWAY"  # the side the model rated above the market at the early time
        g0 = (p - q0) if model_side == "HOME" else (q0 - p)
        g1 = (p - q1) if model_side == "HOME" else (q1 - p)
        move = (q1 - q0) if r["actual_outcome"] == "HOME" else (q0 - q1)
        change.append(abs(p - q1) - abs(p - q0)); signed_change.append(g1 - g0); toward.append(move)
        listing.append({
            "game_id": r["game_id"], "decision_id": r["decision_id"], "away": r["away"], "home": r["home"],
            "slate_date": r["slate_date"], "model_p_home": p, "market_p_home_early": q0, "market_p_home_at_decision": q1,
            "forecast_available_utc": (r["forecast"] or {}).get("available_utc"),
            "early_reference_time_utc": r[early_key]["reference_time_utc"], "decision_time_utc": r["decision_time_utc"],
            "abs_gap_early": abs(p - q0), "abs_gap_at_decision": abs(p - q1), "abs_gap_change": change[-1],
            "model_side": model_side, "model_side_gap_early": g0, "model_side_gap_at_decision": g1,
            "market_move_toward_outcome": move, "final": r["final"], "outcome": r["actual_outcome"],
            "selected": r["selected"], "books_early": r[early_key]["n_books"], "books_at_decision": r["market"]["n_books"]})
    listing.sort(key=lambda x: -x["abs_gap_at_decision"])
    if not rows:
        return None, listing
    return {
        "n": len(rows),
        "mean_abs_gap_early": fmean(x["abs_gap_early"] for x in listing),
        "mean_abs_gap_at_decision": fmean(x["abs_gap_at_decision"] for x in listing),
        "mean_abs_gap_change": fmean(change), "mean_abs_gap_change_ci95": bootstrap_mean_ci(change),
        "share_grew": sum(1 for c in change if c > 0) / len(change),
        "mean_model_side_gap_change": fmean(signed_change),
        "mean_model_side_gap_change_ci95": bootstrap_mean_ci(signed_change),
        "mean_market_move_toward_outcome": fmean(toward),
        "mean_market_move_toward_outcome_ci95": bootstrap_mean_ci(toward),
        "share_market_moved_toward_outcome": sum(1 for t in toward if t > 0) / len(toward),
        "share_market_moved_away_from_outcome": sum(1 for t in toward if t < 0) / len(toward),
    }, listing


def cfb_publication_vs_decision(decisions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """CFB: paired model-vs-market at the forecast's availability time AND at decision time. Pure.

    ``market_at_availability`` uses the forecast's own availability clock as t. Where
    no retained pair qualifies at that time, ``market_earliest_retained`` (the first
    time after availability, and no later than the decision, at which the same rule
    yields a reference) is analysed separately and labelled as such. The model
    probability is fixed, so any change in the gap is market movement.
    """

    rows = [r for r in decisions if r["sport"] == "CFB" and r["availability_status"] == "AVAILABLE"
            and r["actual_outcome"] in ("HOME", "AWAY") and r["p_home_two_way"] is not None]
    if not rows:
        return None
    at_avail = [r for r in rows if r.get("market_at_availability")]
    at_dec = [r for r in rows if r.get("market")]
    both = [r for r in rows if r.get("market_at_availability") and r.get("market")]
    early = [r for r in rows if r.get("market_earliest_retained") and r.get("market")]
    movement_avail, list_avail = _cfb_movement(both, "market_at_availability")
    movement_early, list_early = _cfb_movement(early, "market_earliest_retained")
    gaps = [r["forecast_age_hours"] for r in at_dec if r["forecast_age_hours"] is not None]
    early_lead = [(datetime.fromisoformat(r["decision_time_utc"])
                   - datetime.fromisoformat(r["market_earliest_retained"]["reference_time_utc"])).total_seconds() / 3600
                  for r in early]
    return {
        "eligible_final_decisions": len(rows),
        "availability_clock": AVAILABILITY_FIELD["cfb_research_forecasts"],
        "n_at_availability": len(at_avail), "n_at_decision": len(at_dec), "n_both": len(both),
        "n_earliest_retained_and_decision": len(early),
        "median_forecast_age_at_decision_hours": median(gaps) if gaps else None,
        "median_earliest_reference_hours_before_decision": median(early_lead) if early_lead else None,
        "max_earliest_reference_hours_before_decision": max(early_lead) if early_lead else None,
        "at_availability": _cfb_pair(at_avail, "market_at_availability"),
        "at_decision": _cfb_pair(at_dec, "market"),
        "both_at_availability": _cfb_pair(both, "market_at_availability"),
        "both_at_decision": _cfb_pair(both, "market"),
        "disagreement_availability_to_decision": movement_avail,
        "earliest_retained": _cfb_pair(early, "market_earliest_retained"),
        "earliest_retained_decision": _cfb_pair(early, "market"),
        "disagreement_earliest_to_decision": movement_early,
        "games_both": list_avail,
        "games_earliest": list_early,
        "unsupported": None if both else (
            "No retained CFB moneyline quote exists near the forecasts' availability time: the forecasts became "
            "available a median of about 156 h before the decision, but the first retained quote is at most "
            f"{(max(early_lead) if early_lead else 0):.1f} h before it (THE_ODDS_API only; no SportsGameOdds CFB "
            "moneyline rows). The availability-time comparison cannot be built; the earliest retained reference is "
            "reported instead and is not a publication-time market."),
    }


# -------------------------------------------------------------- narrative ---

def _fmt(value: float | None, digits: int = 3, signed: bool = False) -> str:
    if value is None:
        return "—"
    text = f"{abs(value):.{digits}f}"
    if not signed or float(text) == 0:
        return text
    return f"{'+' if value > 0 else '−'}{text}"


def _ci(ci: list[float] | None, digits: int = 3) -> str:
    return "" if not ci else f" [{_fmt(ci[0], digits, True)}, {_fmt(ci[1], digits, True)}]"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _excludes_zero(ci: list[float] | None) -> bool:
    return bool(ci) and (ci[0] > 0 or ci[1] < 0)


#: Below this many settled selected sides no selection conclusion is stated, whatever the interval says.
MIN_SIDES_FOR_CONCLUSION = 20


def selection_statement(block: dict[str, Any], label: str) -> str:
    """What one sport/model's selected-side data show, and only that."""

    if not block["side_n"]:
        return f"{label}: no settled selected side with a forecast available before the decision."
    ci = block["side_win_rate_ci95"]
    text = (f"{label}: selected sides won {block['side_wins']} of {block['side_n']} ({_pct(block['side_win_rate'])}, "
            f"95% CI {_pct(ci[0])}–{_pct(ci[1])}); the decision-bound model gave them {_pct(block['side_mean_model_p'])}")
    if block["side_mean_market_p"] is not None:
        text += f", the market {_pct(block['side_mean_market_p'])}"
    notes = []
    for who, key in (("model", "side_mean_model_p"), ("market", "side_mean_market_p")):
        near = borderline(ci, block[key])
        if near:
            notes.append(f"borderline: the interval's {near[0]} bound ({near[1] * 100:.1f}%) is within "
                         f"{BORDERLINE_BAND * 100:.0f} pt of the {who}'s rate ({block[key] * 100:.1f}%); not established")
    tail = ("; " + "; ".join(notes)) if notes else ""
    if block["side_n"] < MIN_SIDES_FOR_CONCLUSION:
        return text + f". n={block['side_n']} is too small to support a conclusion{tail}."
    if borderline(ci, block["side_mean_model_p"]):
        return text + f"{tail}."
    if block["ci_excludes_model_p"]:
        return text + f". The interval excludes the model's rate: these sides won less often than the model said{tail}."
    return text + f". The interval still includes the model's rate, so the shortfall is not established{tail}."


def selection_is_lead_worthy(block: dict[str, Any]) -> bool:
    """Stable enough to lead the page: n >= 30, the interval excludes the model's rate, and not borderline."""

    return (block["side_n"] >= MIN_SIDES_FOR_LEAD and block["ci_excludes_model_p"]
            and borderline(block["side_win_rate_ci95"], block["side_mean_model_p"]) is None)


#: A rate within this distance of the nearer interval bound (inside or outside) is borderline: no verdict.
BORDERLINE_BAND = 0.01
#: A selection statement may lead the page only with at least this many settled sides and no borderline.
MIN_SIDES_FOR_LEAD = 30


def borderline(ci: list[float] | None, rate: float | None, band: float = BORDERLINE_BAND) -> tuple[str, float] | None:
    """('upper'|'lower', bound) when ``rate`` lies within ``band`` of the nearer CI bound; else None."""

    if not ci or rate is None:
        return None
    lower, upper = abs(rate - ci[0]), abs(rate - ci[1])
    if min(lower, upper) > band:
        return None
    return ("upper", ci[1]) if upper <= lower else ("lower", ci[0])


def narrative(games: dict[str, Any], groups: list[dict[str, Any]], decision_groups: list[dict[str, Any]],
              cfb: dict[str, Any] | None, scope: str) -> dict[str, Any]:
    """Three short lead findings plus per-sport selection statements, from THIS filtered snapshot only.

    ``games`` is {"evaluations": [...], "decisions": [...]} for the snapshot.
    """

    candidates: list[tuple[float, dict[str, Any]]] = []
    dgroups = [g for g in decision_groups if g["governing_decisions"]]

    def ids(rows: list[dict[str, Any]], key: str, n: int = 8) -> list[str]:
        return [r[key] for r in rows[:n] if r.get(key)]

    # 1. CFB against the market (decision time; availability-time where the records allow).
    for g in dgroups:
        block = g["market_decision_time"]
        if g["sport"] != "CFB" or not block:
            continue
        rows = sorted((r for r in decisions_of(games, g) if r["delta_log_loss"] is not None), key=lambda r: -r["delta_log_loss"])
        text = (f"CFB Gen1 trails the median-of-books market at decision time: Δ log loss "
                f"{_fmt(block['delta_log_loss'], 3, True)}{_ci(block['delta_log_loss_ci95'])}, n={block['n']} games on "
                f"{block['dates']} date(s), with forecasts about {_fmt(block['median_forecast_age_hours'], 0)} h old.")
        if cfb and cfb.get("disagreement_earliest_to_decision"):
            mv = cfb["disagreement_earliest_to_decision"]
            text += (f" The gap was already there at the earliest retained quote "
                     f"({_fmt(mv['mean_abs_gap_early'] * 100, 1)} pts vs {_fmt(mv['mean_abs_gap_at_decision'] * 100, 1)} "
                     f"pts at decision); no quote exists near the forecasts' availability time.")
        candidates.append((3.0 if _excludes_zero(block["delta_log_loss_ci95"]) else 1.0, {
            "key": "market:CFB", "family": "market-cfb", "title": "CFB model trails the market", "text": text,
            "experiment": EXPERIMENTS["CFB"], "game_ids": ids(rows, "game_id"), "decision_ids": ids(rows, "decision_id")}))
    # 2. MLB / NFL: no detectable edge over the market at a comparable time.
    parts, rows_all = [], []
    for g in dgroups:
        block = g["market_comparable"]
        if g["sport"] == "CFB" or not block:
            continue
        verdict = ("worse than" if _excludes_zero(block["delta_log_loss_ci95"]) and block["delta_log_loss"] > 0 else
                   "better than" if _excludes_zero(block["delta_log_loss_ci95"]) else "not distinguishable from")
        parts.append(f"{g['model']} {verdict} the market (Δ {_fmt(block['delta_log_loss'], 3, True)}"
                     f"{_ci(block['delta_log_loss_ci95'])}, n={block['n']})")
        rows_all += sorted((r for r in decisions_of(games, g) if r["delta_log_loss"] is not None and r["comparable"]),
                           key=lambda r: -r["delta_log_loss"])[:4]
    if parts:
        candidates.append((2.0, {
            "key": "market:other", "family": "market-other", "title": "No detectable edge over the market",
            "text": "At a comparable time, using the forecast each decision used: " + "; ".join(parts) + ".",
            "experiment": EXPERIMENTS["R2"], "game_ids": ids(rows_all, "game_id", 12),
            "decision_ids": ids(rows_all, "decision_id", 12)}))
    # 3. Decisions whose forecast arrived after the decision.
    excluded = sum(g["excluded_not_available_before_decision"] for g in dgroups)
    if excluded:
        rows = [r for r in decisions_of(games, None) if r["availability_status"] == "NOT_AVAILABLE_BEFORE_DECISION"]
        candidates.append((2.5, {
            "key": "availability", "family": "availability", "title": "Forecasts that arrived after the decision",
            "text": (f"{excluded} governing decision(s) reference a forecast whose availability clock is later than the "
                     "decision; they are excluded from every decision-level number."),
            "experiment": EXPERIMENTS["REPAIRS"], "game_ids": ids(rows, "game_id", 12),
            "decision_ids": ids(rows, "decision_id", 12)}))
    # 4. FINAL vs CURRENT mix.
    for sport in sorted({g["sport"] for g in groups if g["forecast_type"] == "CURRENT"}):
        fin = next((g for g in groups if g["sport"] == sport and g["forecast_type"] == "FINAL"), None)
        cur = next((g for g in groups if g["sport"] == sport and g["forecast_type"] == "CURRENT"), None)
        if not (fin and cur) or cur["n"] < 3:
            continue
        rows = [x for x in games["evaluations"] if x["sport"] == sport and x["forecast_type"] == "CURRENT"]
        leads = [x["lead_hours"] for x in rows if x["lead_hours"] is not None]
        dec_age = next((d["market_decision_time"]["median_forecast_age_hours"] for d in dgroups
                        if d["sport"] == sport and d["market_decision_time"]), None)
        candidates.append((1.5, {
            "key": f"timing:{sport}", "family": "timing", "title": f"{sport} mixes kickoff and days-old forecasts",
            "text": (f"{cur['n']} of {cur['n'] + fin['n']} scored {sport} games used a CURRENT snapshot "
                     f"{_fmt(min(leads), 0)}–{_fmt(max(leads), 0)} h before kickoff (log loss "
                     f"{_fmt(cur['log_loss_two_way'])} vs FINAL {_fmt(fin['log_loss_two_way'])})"
                     + (f"; the forecasts {sport} decisions used were a median {_fmt(dec_age, 0)} h old." if dec_age else ".")),
            "experiment": EXPERIMENTS["CAPTURE"], "game_ids": ids(rows, "game_id", 12),
            "evaluation_ids": ids(rows, "evaluation_id", 12)}))
    # 5. Selection, only where a conclusion is supported.
    for g in dgroups:
        sel = g["selection"]["selected"]
        if selection_is_lead_worthy(sel):
            rows = [r for r in decisions_of(games, g) if r["selected"] and r["selected_side_won"] is not None]
            candidates.append((1.8, {
                "key": f"selection:{g['sport']}:{g['engine_id']}", "family": "selection",
                "title": f"{g['model']} selected sides", "text": selection_statement(sel, g["model"]),
                "experiment": EXPERIMENTS["R2"], "game_ids": ids(rows, "game_id", 12),
                "decision_ids": ids(rows, "decision_id", 12)}))
    candidates.sort(key=lambda c: -c[0])
    findings, seen = [], set()
    for _s, item in candidates:
        if item["family"] in seen:
            continue
        seen.add(item["family"])
        findings.append(item)
        if len(findings) == 3:
            break
    selection = [selection_statement(g["selection"]["selected"], f"{g['sport']} · {g['model']}")
                 for g in dgroups if g["selection"]["selected"]["side_n"]]
    return {"scope": scope, "findings": findings, "selection_statements": selection}


def decisions_of(bundle: dict[str, Any], group: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = bundle["decisions"]
    if group is None:
        return rows
    return [r for r in rows if r["sport"] == group["sport"] and r["engine_id"] == group["engine_id"]
            and r["experiment_version"] == group["experiment_version"]]


# ----------------------------------------------------------------- loader ---

@dataclass
class Loaded:
    games: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    positions_total: int
    positions_open: int
    as_of: datetime | None


def _status(value: Any) -> str:
    return str(getattr(value, "value", value) or "").upper()


def load(session: Session) -> Loaded:
    """Read-only: evaluations, governing decisions with their own forecasts, market references, positions."""

    from app.models import (
        CfbResearchForecast, Forecast, Game, GameTeam, Market, MarketOutcome, MarketType, OddsSnapshot,
        OfficialAspPosition, ResearchForecastEvaluation, ShadowExperiment, ShadowForecast, Sportsbook, Team,
    )
    from app.models.entities import ExperimentDecisionPhase, ShadowExperimentDecision

    evaluations = primary_evaluations(list(session.scalars(
        select(ResearchForecastEvaluation).where(ResearchForecastEvaluation.is_current.is_(True)))))

    positions = list(session.execute(select(
        OfficialAspPosition.id, OfficialAspPosition.shadow_decision_id, OfficialAspPosition.shadow_experiment_id,
        OfficialAspPosition.status, OfficialAspPosition.result, OfficialAspPosition.stake,
        OfficialAspPosition.market_outcome_id)).all())
    experiment_ids = {p.shadow_experiment_id for p in positions if p.shadow_experiment_id}
    experiments = {e.id: e for e in session.scalars(select(ShadowExperiment).where(ShadowExperiment.id.in_(experiment_ids)))} \
        if experiment_ids else {}
    raw = list(session.execute(select(
        ShadowExperimentDecision.id, ShadowExperimentDecision.experiment_id, ShadowExperimentDecision.game_id,
        ShadowExperimentDecision.sport, ShadowExperimentDecision.recommendation,
        ShadowExperimentDecision.recommended_at, ShadowExperimentDecision.created_at,
        ShadowExperimentDecision.market_outcome_id, ShadowExperimentDecision.selection,
        ShadowExperimentDecision.forecast_id, ShadowExperimentDecision.shadow_forecast_id,
        ShadowExperimentDecision.cfb_research_forecast_id,
    ).where(ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME,
            ShadowExperimentDecision.experiment_id.in_(experiment_ids))).all()) if experiment_ids else []
    raw = [d for d in raw if SPORT_OF_DECISION.get(d.sport) == (experiments[d.experiment_id].sport or "").upper()]
    # NFL rows without shadow_forecast_id name their forecast in evidence.shadow_forecast_row_id.
    missing_nfl = [d.id for d in raw if d.shadow_forecast_id is None and d.sport == "NFL"]
    evidence_row = {}
    if missing_nfl:
        for did, ev in session.execute(select(ShadowExperimentDecision.id, ShadowExperimentDecision.evidence)
                                       .where(ShadowExperimentDecision.id.in_(missing_nfl))):
            evidence_row[did] = (ev or {}).get("shadow_forecast_row_id")

    game_ids = {row.canonical_game_id for row in evaluations if row.canonical_game_id} | {d.game_id for d in raw}
    games_meta = {g.id: g for g in session.scalars(select(Game).where(Game.id.in_(game_ids)))} if game_ids else {}
    teams: dict[Any, dict[str, Any]] = defaultdict(dict)
    home_team: dict[Any, Any] = {}
    if game_ids:
        for gid, tid, is_home, abbreviation, name in session.execute(
                select(GameTeam.game_id, GameTeam.team_id, GameTeam.is_home, Team.abbreviation, Team.name)
                .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id.in_(game_ids))):
            if abbreviation and abbreviation.startswith("CFBD") and abbreviation[4:].isdigit():
                abbreviation = name
            teams[gid]["home" if is_home else "away"] = {"abbreviation": abbreviation, "name": name}
            if is_home:
                home_team[gid] = tid
    decision_games = {d.game_id for d in raw}
    outcome_side: dict[Any, str | None] = {}
    if decision_games:
        for oid, gid, key, team in session.execute(
                select(MarketOutcome.id, Market.game_id, MarketOutcome.outcome_key, MarketOutcome.participant_team_id)
                .join(Market, Market.id == MarketOutcome.market_id)
                .where(Market.market_type == MarketType.MONEYLINE, Market.game_id.in_(decision_games))):
            k = (key or "").split(":")[0].upper()
            outcome_side[oid] = k if k in ("HOME", "AWAY", "TIE") else (
                ("HOME" if team == home_team.get(gid) else "AWAY") if team else None)
    pos_by_decision = {p.shadow_decision_id: {
        "position_id": str(p.id), "status": _status(p.status), "result": _status(p.result),
        "stake": float(p.stake) if p.stake is not None else None, "side": outcome_side.get(p.market_outcome_id)}
        for p in positions if p.shadow_decision_id}

    # -- the forecasts the decisions reference
    fc_rows: dict[str, dict[Any, dict[str, Any]]] = {"forecasts": {}, "shadow_forecasts": {}, "cfb_research_forecasts": {}}
    v_ids = {d.forecast_id for d in raw if d.forecast_id}
    if v_ids:
        for r in session.execute(select(Forecast.id, Forecast.home_win_probability, Forecast.away_win_probability,
                                        Forecast.available_as_of, Forecast.published_at, Forecast.version)
                                 .where(Forecast.id.in_(v_ids))):
            fc_rows["forecasts"][r.id] = {"table": "forecasts", "row_id": str(r.id), "snapshot": f"forecast v{r.version}",
                                          "snapshot_type": "PROSPECTIVE", "p_home": _f(r.home_win_probability),
                                          "p_away": _f(r.away_win_probability), "p_tie": 0.0,
                                          "available_utc": _aware(r.available_as_of), "published_utc": _aware(r.published_at)}
    s_ids = {d.shadow_forecast_id for d in raw if d.shadow_forecast_id}
    s_ids |= {_uuid(v) for v in evidence_row.values() if v}
    s_ids.discard(None)
    if s_ids:
        for r in session.execute(select(ShadowForecast.id, ShadowForecast.full_home_win_probability,
                                        ShadowForecast.full_away_win_probability, ShadowForecast.available_as_of_utc,
                                        ShadowForecast.snapshot_type, ShadowForecast.snapshot_version,
                                        ShadowForecast.forecast_cutoff_utc)
                                 .where(ShadowForecast.id.in_(s_ids))):
            ph, pa = _f(r.full_home_win_probability), _f(r.full_away_win_probability)
            fc_rows["shadow_forecasts"][r.id] = {
                "table": "shadow_forecasts", "row_id": str(r.id), "snapshot": f"{r.snapshot_type} v{r.snapshot_version}",
                "snapshot_type": r.snapshot_type, "p_home": ph, "p_away": pa,
                "p_tie": max(0.0, 1.0 - ph - pa) if (ph is not None and pa is not None) else None,
                "available_utc": _aware(r.available_as_of_utc), "published_utc": _aware(r.forecast_cutoff_utc)}
    c_ids = {d.cfb_research_forecast_id for d in raw if d.cfb_research_forecast_id}
    cfb_identity = {}
    if c_ids:
        for r in session.execute(select(CfbResearchForecast.id, CfbResearchForecast.forecast_id,
                                        CfbResearchForecast.joint_home_win_probability,
                                        CfbResearchForecast.joint_away_win_probability, CfbResearchForecast.tie_probability,
                                        CfbResearchForecast.imported_at, CfbResearchForecast.exchange_published_utc)
                                 .where(CfbResearchForecast.id.in_(c_ids))):
            cfb_identity[r.id] = r.forecast_id
            fc_rows["cfb_research_forecasts"][r.id] = {
                "table": "cfb_research_forecasts", "row_id": str(r.id), "snapshot": "CFB Gen1 joint",
                "snapshot_type": "PROSPECTIVE", "p_home": _f(r.joint_home_win_probability),
                "p_away": _f(r.joint_away_win_probability), "p_tie": _f(r.tie_probability),
                "available_utc": _aware(r.imported_at), "published_utc": _aware(r.exchange_published_utc)}

    by_key: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for d in raw:
        side = outcome_side.get(d.market_outcome_id) if d.market_outcome_id else None
        if side is None and (d.selection or "").upper() in ("HOME", "AWAY"):
            side = d.selection.upper()
        if d.forecast_id:
            fc = fc_rows["forecasts"].get(d.forecast_id)
        elif d.shadow_forecast_id or evidence_row.get(d.id):
            fc = fc_rows["shadow_forecasts"].get(d.shadow_forecast_id or _uuid(evidence_row.get(d.id)))
        elif d.cfb_research_forecast_id:
            fc = fc_rows["cfb_research_forecasts"].get(d.cfb_research_forecast_id)
        else:
            fc = None
        by_key[(d.experiment_id, d.game_id)].append({
            "decision_id": str(d.id), "decision_time": _aware(d.recommended_at or d.created_at),
            "recommendation": d.recommendation, "selected_side": side, "funded": d.id in pos_by_decision,
            "position": pos_by_decision.get(d.id), "forecast": fc, "cfb_identity": cfb_identity.get(d.cfb_research_forecast_id)})

    governing: list[dict[str, Any]] = []
    for (exp_id, gid), ds in by_key.items():
        game = games_meta.get(gid)
        if game is None:
            continue
        kickoff = _aware(game.starts_at)
        decision, rule = governing_decision(ds, kickoff)
        exp_row = experiments[exp_id]
        final = ({"home": game.home_score, "away": game.away_score}
                 if _status(game.status) == "FINAL" and game.home_score is not None and game.away_score is not None else None)
        governing.append({**decision, "governing_rule": rule, "n_decisions": len(ds), "game_id": gid,
                          "kickoff": kickoff, "final": final, "sport": (exp_row.sport or "").upper(),
                          "experiment_version": exp_row.version, "engine_id": exp_row.controlling_engine,
                          "home": (teams[gid].get("home") or {}).get("abbreviation"),
                          "away": (teams[gid].get("away") or {}).get("abbreviation")})

    # -- quotes for every governing decision's game
    need = {d["game_id"] for d in governing}
    quotes: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    if need:
        for gid, key, team, book, source, observed, available, decimal in session.execute(
                select(Market.game_id, MarketOutcome.outcome_key, MarketOutcome.participant_team_id, Sportsbook.key,
                       OddsSnapshot.source, OddsSnapshot.observed_at, OddsSnapshot.available_as_of,
                       OddsSnapshot.decimal_odds)
                .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
                .join(Market, Market.id == MarketOutcome.market_id)
                .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
                .where(Market.market_type == MarketType.MONEYLINE, Market.game_id.in_(need))):
            k = (key or "").split(":")[0].upper()
            side = k if k in ("HOME", "AWAY") else (("HOME" if team == home_team.get(gid) else "AWAY") if team else None)
            if side:
                quotes[gid].append({"side": side, "book": book, "source": source, "observed_at": _aware(observed),
                                    "available_as_of": _aware(available), "decimal": decimal})

    eval_by_key = {(row.canonical_game_id, row.engine_id): row for row in evaluations}
    decisions = []
    for d in governing:
        ev = eval_by_key.get((d["game_id"], d["engine_id"]))
        fc = d.get("forecast")
        same = None
        if ev is not None and fc:
            same = (d.get("cfb_identity") == ev.forecast_identity) if fc["table"] == "cfb_research_forecasts" else (
                fc["row_id"] == str(ev.shadow_forecast_id) if ev.shadow_forecast_id else None)
        game_quotes = quotes.get(d["game_id"], [])
        d["market"] = market_reference(game_quotes, d["decision_time"])
        d["market_at_availability"] = (market_reference(game_quotes, fc["available_utc"])
                                       if (d["sport"] == "CFB" and fc and fc.get("available_utc")) else None)
        d["market_earliest_retained"] = (earliest_reference(game_quotes, fc["available_utc"], d["decision_time"])
                                         if (d["sport"] == "CFB" and fc and fc.get("available_utc")) else None)
        d["evaluation_forecast_same"] = same
        decisions.append(decision_record(d))
    decisions.sort(key=lambda r: (r["kickoff_utc"] or "", r["sport"]))
    by_eval = {(r["game_id"], r["engine_id"]): r for r in decisions}
    games = [game_record(row, teams=teams.get(row.canonical_game_id),
                         decision=by_eval.get((str(row.canonical_game_id), row.engine_id))) for row in evaluations]
    as_of = max((_aware(row.created_at) for row in evaluations if row.created_at is not None), default=None)
    return Loaded(games=games, decisions=decisions, positions_total=len(positions),
                  positions_open=sum(1 for p in positions if _status(p.status) in ("OPEN", "PENDING")), as_of=as_of)


def _uuid(value: Any) -> Any:
    from uuid import UUID

    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        return None


def funnel(games: list[dict[str, Any]], decisions: list[dict[str, Any]], positions_total: int,
           positions_open: int) -> dict[str, Any]:
    status: dict[str, int] = defaultdict(int)
    for r in decisions:
        status[r["availability_status"]] += 1
    joined = [r for r in decisions if r["evaluation_forecast_same"] is not None]
    available = [r for r in decisions if r["availability_status"] == "AVAILABLE"]
    return {
        "forecast_evaluations": len(games),
        "evaluations_with_governing_decision": sum(1 for g in games if g["decision"]),
        "evaluations_funded": sum(1 for g in games if g["wager"]),
        "governing_decisions": len(decisions),
        "decision_availability": dict(status),
        "decisions_available_before_decision": len(available),
        "decisions_excluded_not_available": status.get("NOT_AVAILABLE_BEFORE_DECISION", 0),
        "decisions_excluded_no_clock_or_forecast": status.get("NO_AVAILABILITY_CLOCK", 0) + status.get("NO_FORECAST_LINKED", 0),
        "decisions_final": sum(1 for r in available if r["actual_outcome"]),
        "decisions_with_market": sum(1 for r in available if r["market"]),
        "decisions_comparable_with_market": sum(1 for r in available if r["market"] and r["comparable"]),
        "decisions_selected": sum(1 for r in decisions if r["selected"]),
        "decisions_funded": sum(1 for r in decisions if r["funded"]),
        "decision_vs_evaluation_forecast_joined": len(joined),
        "decision_vs_evaluation_forecast_same": sum(1 for r in joined if r["evaluation_forecast_same"]),
        "decision_vs_evaluation_forecast_differs": sum(1 for r in joined if not r["evaluation_forecast_same"]),
        "funded_positions": {"total": positions_total, "open": positions_open, "settled": positions_total - positions_open},
    }


# ------------------------------------------------------------ entry point ---

def filter_rows(rows: list[dict[str, Any]], *, sport: str | None = None, model: str | None = None,
                forecast_kind: str | None = None, lead: str | None = None) -> list[dict[str, Any]]:
    out = rows
    if sport and sport.upper() != "ALL":
        out = [g for g in out if g["sport"] == sport.upper()]
    if model and model != "ALL":
        engine = model.split("|")[0]
        out = [g for g in out if g.get("model_key") == model or (g.get("model_key") is None and g["engine_id"] == engine)
               or (("|" not in model) and g["engine_id"] == model)]
    if forecast_kind and forecast_kind.upper() != "ALL":
        out = [g for g in out if g["forecast_type"] == forecast_kind.upper()]
    if lead and lead.upper() != "ALL":
        out = [g for g in out if g["lead_bucket"] == lead.upper()]
    return out


filter_games = filter_rows


def scope_label(sport: str | None, model: str | None, forecast_kind: str | None, lead: str | None,
                games: list[dict[str, Any]]) -> str:
    parts = [f"sport {sport.upper()}" if sport and sport.upper() != "ALL" else "all sports"]
    if model and model != "ALL":
        name = next((g["model"] for g in games if g["model_key"] == model or g["engine_id"] == model), model)
        parts.append(f"model {name}")
    parts.append(f"{forecast_kind.upper()} forecasts" if forecast_kind and forecast_kind.upper() != "ALL"
                 else "all forecast types")
    label = next((lbl for key, lbl, _lo, _hi in LEAD_BUCKETS if lead and key == lead.upper()), None)
    parts.append(f"lead {label}" if label else "all lead times")
    return " · ".join(parts)


def overview(session: Session, *, sport: str | None = None, model: str | None = None,
             forecast_kind: str | None = None, lead: str | None = None, include_wagering: bool = True,
             now: datetime | None = None, include_test_data: bool = False) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    loaded = load(session)
    return build_payload(loaded, sport=sport, model=model, forecast_kind=forecast_kind, lead=lead,
                         wagering=_wagering(session, sport, now) if include_wagering else None, now=now,
                         nhl=_nhl_block(session, sport, include_test_data))


def _nhl_block(session: Session, sport: str | None, include_test_data: bool = False) -> dict[str, Any] | None:
    """NHL Gen1 research scores: regulation 3-way and final 2-way, kept apart. None when out of scope."""

    if sport and sport.upper() not in ("ALL", "NHL"):
        return None
    try:
        from app.nhl.read_service import NhlReadService

        return NhlReadService(session).overview_block(include_test_data)
    except Exception:  # noqa: BLE001 -- before the NHL migration the block is simply absent
        session.rollback()
        return None


def _wagering(session: Session, sport: str | None, now: datetime) -> dict[str, Any]:
    from app.services.wager_results_read import wager_results

    out = {}
    for code in ("MLB", "NFL", "CFB"):
        if sport and sport.upper() not in ("ALL", code):
            continue
        try:
            summary = wager_results(session, sport=code, now=now)["summary"]
        except Exception:  # noqa: BLE001 -- the returns block is supplementary; never 500 the overview
            session.rollback()
            continue
        out[code] = {k: summary.get(k) for k in (
            "n", "settled", "wins", "losses", "pushes", "settled_stake", "net_profit", "roi", "roi_ci95",
            "open_count", "open_exposure")}
    return out


def build_payload(loaded: Loaded, *, sport: str | None = None, model: str | None = None,
                  forecast_kind: str | None = None, lead: str | None = None,
                  wagering: dict[str, Any] | None = None, now: datetime | None = None,
                  nhl: dict[str, Any] | None = None) -> dict[str, Any]:
    games = filter_rows(loaded.games, sport=sport, model=model, forecast_kind=forecast_kind, lead=lead)
    decisions = filter_rows(loaded.decisions, sport=sport, model=model, forecast_kind=forecast_kind, lead=lead)
    groups = summarize(games)
    decision_groups = summarize_decisions(decisions)
    cfb = cfb_publication_vs_decision(decisions)
    scope = scope_label(sport, model, forecast_kind, lead, loaded.games)
    options_models = sorted({(g["sport"], g["model_key"], g["model"]) for g in loaded.games})
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _iso(now or datetime.now(UTC)),
        "evidence_as_of_utc": _iso(loaded.as_of),
        "scope": {"sport": (sport or "ALL").upper(), "model": model or "ALL",
                  "forecast_type": (forecast_kind or "ALL").upper(), "lead_bucket": (lead or "ALL").upper(),
                  "label": scope, "n_games": len(games), "n_decisions": len(decisions)},
        "narrative": narrative({"evaluations": games, "decisions": decisions}, groups, decision_groups, cfb, scope),
        "groups": groups,
        "decision_groups": decision_groups,
        "cfb_publication_vs_decision": cfb,
        # NHL research block: regulation 3-way and final 2-way kept apart; None when out of scope.
        "nhl": nhl,
        "funnel": funnel(games, decisions, loaded.positions_total, loaded.positions_open),
        "wagering": wagering,
        "games": games,
        "decisions": decisions,
        "options": {
            # NHL Gen1 research scores live outside research_forecast_evaluations;
            # the sport is offered once any NHL forecast exists.
            "sports": sorted({g["sport"] for g in loaded.games} | ({"NHL"} if nhl and nhl.get("forecast_games") else set())),
            "models": [{"sport": s, "key": k, "label": lbl,
                        "n": sum(1 for g in loaded.games if g["model_key"] == k)} for s, k, lbl in options_models],
            "forecast_types": [k for k in FORECAST_TYPES if any(g["forecast_type"] == k for g in loaded.games)],
            "lead_buckets": [{"key": k, "label": lbl} for k, lbl, _lo, _hi in LEAD_BUCKETS],
        },
        "experiments": EXPERIMENTS,
        "methodology": METHODOLOGY,
        "authority": {
            "creates_positions": False, "changes_policy": False, "promotes_a_model": False,
            "statement": "This evaluation view scores forecasts only. It does not create positions. Official NFL "
                         "(and MLB, CFB) wagers are a separate population shown in the Wager Results tab.",
        },
    }


__all__ = [
    "SCHEMA_VERSION", "overview", "build_payload", "load", "summarize", "summarize_decisions", "group_metrics",
    "decision_metrics", "decision_record", "narrative", "market_reference", "governing_decision",
    "primary_evaluations", "game_record", "forecast_type", "lead_bucket", "two_way", "brier_three_class",
    "two_way_brier_from_probabilities", "signed_error", "error_direction", "bootstrap_mean_ci", "is_comparable",
    "filter_rows", "filter_games", "brier_checks", "cfb_publication_vs_decision", "selection_statement",
]
