"""Monday Morning QB (MMQB): the weekly NFL report, from the same reconciled numbers as the site.

One read-only snapshot answers four questions about one NFL week:

1. **What did we actually bet?** One row per OFFICIAL FUNDED POSITION in the week, exactly as the
   Wager Results tab counts it (``wager_results_read._load``): the funded selection (never the
   model's predicted winner), the decision-time probabilities recorded on that position's own
   funding decision, the entry book and price, the stake, the recorded tie-aware return, and the
   settlement from ``position_results``. Unsettled positions and games with no position are listed
   separately; a pending game is never shown as a result.
2. **How did it go against what we expected at entry?** Settled W-L-P, stake, net and ROI come from
   ``wager_results_read.summarize``. The expectation uses the recorded decision-time numbers:
   expected wins = sum pWin, expected pushes = sum pTie, expected profit = sum stake x recorded EV,
   where NFL EV = pWin(d-1) - pLoss (a tie refunds the stake). Its spread is the model's own: the
   standard deviation of profit if every wager resolved with the recorded probabilities.
3. **Was the model better than the market on the same games at the same time?** Two-way scores
   (p = home / (home + away), non-tie games only) from ``model_performance_overview``: the forecast
   each decision referenced, versus (a) the decision's own no-vig pair and (b) the Overview's
   median-of-books reference at the decision time. The stored three-class Brier is never used here.
4. **What happened in each game, and can any problem be established?** Pregame rationale from
   retained evidence only; the postgame result against the forecast's stored score distribution;
   record checks (forecast availability and type, quote clocks, price-to-snapshot, EV arithmetic,
   settlement-to-score); and what remains a hypothesis. No play-by-play is retained, so no in-game
   cause is asserted.

REUSED, NEVER RE-IMPLEMENTED
----------------------------
* Positions, settlement, binding, recorded returns and band summaries: ``wager_results_read``.
* Decision-bound forecasts, market references, two-way scores and paired deltas:
  ``model_performance_overview`` (``load``, ``decision_metrics``, ``group_metrics``,
  ``log_loss_two_way``, ``brier_two_way``, ``bootstrap_mean_ci``).
Settlement is only *checked* here (stored result and profit against the stored final score); it is
never recomputed into the numbers.

PRICES
------
A later quote is labelled **last observed quote**. A genuine close needs capture evidence (a
same-book later capture with both legs in one receipt before kickoff); none is claimed here. The
ingest also drops re-served identical quotes, so the absence of a later row does not prove the
price was never re-confirmed.

Read-only: one caller-owned transaction (the CLI and the endpoint use REPEATABLE READ READ ONLY),
no provider call, no write, no job trigger.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import model_performance_overview as mpo
from app.services import wager_results_read as wr

SCHEMA_VERSION = "AISP_MMQB_REPORT_V1"
ET = ZoneInfo("America/New_York")
SUPPORTED_LEAGUES = ("NFL",)
DISAGREEMENT_PTS = 10.0
MC_DRAWS = 200_000
MC_SEED = 20260928
TAIL = 0.05
STALE_BOOK_QUOTE_MINUTES = 10.0
FRESH_FORECAST_HOURS = 6.0

GENUINE_CLOSE_RULE = (
    "A genuine close requires capture evidence: a later capture from the same book with both legs in one "
    "receipt before kickoff. None is claimed in this report. Any later price is labelled 'last observed quote'. "
    "The ingest drops re-served identical quotes, so a missing later row does not prove the price was never "
    "re-confirmed."
)
NO_PLAY_BY_PLAY = (
    "The AISP database retains no play-by-play, drive, injury or in-game availability record for these games; only "
    "the final score, the overtime flag and the settlement. Where a retained play-by-play file outside the database "
    "was found, the deep dives quote it exactly, with its source; nothing else about in-game events is asserted."
)
ONE_WEEK_CAVEAT = (
    "One Sunday can flag cases worth checking. It cannot justify fitting calibration curves, moving thresholds or "
    "changing stakes: a week is a dozen or so correlated outcomes."
)
READ_NOTES = (
    "A win does not validate an edge and a loss does not invalidate one: an edge is a statement about the price, "
    "and one game resolves only one draw from the forecast.",
    "Selected team = the funded selection. It is often not the model's predicted winner: an underdog is bought "
    "when its price beats the model's probability.",
    "Expected profit treats a tie as a push (stake refunded): EV = pWin(d-1) - pLoss.",
    "Model-versus-market scores are two-way (conditional on no tie). The stored three-class Brier (0-2) is not "
    "comparable to a two-way market Brier and is not used.",
)

#: Reviews recorded OUTSIDE this database (quoted verbatim with their source). A record is never changed by them.
EXTERNAL_RULINGS: dict[str, dict[str, str]] = {
    "7770a220-1100-4c32-a065-c42b4f20c59f": {
        "source": "Codex review, exchange entry 2026-09-28T02:36:17Z, commit 883bf629",
        "classification": "RECEIPT_ORDER_UNPROVEN",
        "text": ("Codex review (2026-09-28 02:36Z): LA@DEN decision 7770a220 is classified RECEIPT_ORDER_UNPROVEN under "
                 "the strict research rule. Quote completion follows the recorded tick-start decision time by 1.687551 s. "
                 "The actual quote-read time is not retained, and the discrepancy alone does not establish look-ahead. "
                 "There is no backdating, tolerance waiver or record change; the official position and its settlement "
                 "stand. The forward fix (retaining the read instant plus an alert-only completed_at ≤ read_at check) is "
                 "a separate candidate not yet submitted."),
    },
}

EXPERIMENT_LINKS = {
    "R2": mpo.EXPERIMENTS["R2"],
    "CAPTURE": mpo.EXPERIMENTS["CAPTURE"],
    "REPAIRS": mpo.EXPERIMENTS["REPAIRS"],
    "NFL_TIE": mpo.EXPERIMENTS["NFL_TIE"],
    "CFB": mpo.EXPERIMENTS["CFB"],
}
RESEARCH_LINKS = [
    {"label": "Model Performance brief v2", "path": r"C:\Users\james\aisp-analysis\model-performance-brief-20260927\BRIEF.md"},
    {"label": "Betting deep dive: corrected addendum", "path": r"C:\Users\james\aisp-analysis\betting-deep-dive-20260927\06_CORRECTED_ADDENDUM.md"},
    {"label": "All-game dataset", "path": r"C:\Users\james\aisp-analysis\all-game-dataset-20260927"},
    {"label": "Decision repairs package (collection/decision ordering race)", "path": r"C:\Users\james\aisp-analysis\decision-repairs-20260927\REPAIRS_PACKAGE.md"},
    {"label": "Near-kickoff capture design", "path": r"C:\Users\james\aisp-analysis\decision-repairs-20260927\CAPTURE_DESIGN.md"},
]


# ------------------------------------------------------------------ basics ---

def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _utc(value: datetime | str | None) -> datetime | None:
    """Any aware datetime or ISO string, as UTC. The DB hands back ET-aware values: never format those with 'Z'."""

    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return _aware(value).astimezone(UTC)


def _iso(value: datetime | str | None) -> str | None:
    moment = _utc(value)
    return moment.isoformat() if moment else None


def zulu(value: datetime | str | None) -> str:
    """'2026-09-27 17:00Z', converted to UTC first."""

    moment = _utc(value)
    return moment.strftime("%Y-%m-%d %H:%MZ") if moment else "—"


def et_label(value: datetime | str | None) -> str:
    moment = _utc(value)
    return moment.astimezone(ET).strftime("%a %b %d %I:%M %p ET").replace(" 0", " ") if moment else "—"


def _hms(value: datetime | str | None) -> str:
    """'23:53:29.170006' in UTC (converted first), for clock-order evidence."""

    moment = _utc(value)
    return moment.strftime("%H:%M:%S.%f") if moment else "—"


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def american_to_decimal(american: Any) -> float | None:
    a = _f(american)
    if a is None or a == 0:
        return None
    return 1 + a / 100 if a > 0 else 1 + 100 / abs(a)


def no_vig_pair(selected_decimal: float | None, opposite_decimal: float | None) -> float | None:
    """Multiplicative no-vig probability of the selected side from one recorded two-way pair."""

    if not selected_decimal or not opposite_decimal or selected_decimal <= 1 or opposite_decimal <= 1:
        return None
    a, b = 1 / selected_decimal, 1 / opposite_decimal
    return a / (a + b)


def nfl_ev(p_win: float, p_tie: float, decimal: float) -> float:
    """NFL moneyline EV per $1 with a tie refunded (push): pWin(d-1) - pLoss."""

    p_loss = 1.0 - p_win - p_tie
    return p_win * (decimal - 1.0) - p_loss


def mid_percentile(distribution: Iterable[dict[str, Any]], key: str, value: float | None) -> float | None:
    """Mid-rank percentile of ``value`` in a stored discrete distribution: P(X < v) + P(X = v) / 2."""

    if value is None:
        return None
    total = below = equal = 0.0
    for point in distribution or []:
        x, p = _f(point.get(key)), _f(point.get("probability"))
        if x is None or p is None:
            continue
        total += p
        if x < value:
            below += p
        elif x == value:
            equal += p
    if total <= 0:
        return None
    return (below + equal / 2) / total


def exact_score_probability(joint: Iterable[dict[str, Any]], home: int | None, away: int | None) -> float | None:
    if home is None or away is None:
        return None
    for point in joint or []:
        if point.get("home_score") == home and point.get("away_score") == away:
            return _f(point.get("probability"))
    return 0.0


# --------------------------------------------------------------- expectation ---

def _poisson_binomial_tail(ps: list[float], k: int) -> float:
    """P(at least k successes) for independent Bernoulli(p_i), exactly."""

    dist = [1.0]
    for p in ps:
        nxt = [0.0] * (len(dist) + 1)
        for i, mass in enumerate(dist):
            nxt[i] += mass * (1 - p)
            nxt[i + 1] += mass * p
        dist = nxt
    return sum(dist[k:]) if k < len(dist) else 0.0


def expectation(rows: list[dict[str, Any]], *, draws: int = MC_DRAWS, seed: int = MC_SEED) -> dict[str, Any]:
    """Entry expectation for wager rows that carry pWin, pTie, a recorded return and an entry price.

    expected wins = sum pWin; pushes = sum pTie; losses = sum pLoss; expected profit = sum stake x recorded EV
    (also recomputed from pWin(d-1) - pLoss as a check). SD = the model's own spread of profit, treating wagers
    as independent. Where the rows are settled, the realised values are compared on the SAME rows.
    """

    usable = [r for r in rows if r.get("raw_probability") is not None and r.get("tie_probability") is not None
              and r.get("recorded_return") is not None and r.get("entry_decimal")]
    out: dict[str, Any] = {"n": len(usable), "n_rows": len(rows), "excluded_missing_inputs": len(rows) - len(usable)}
    if not usable:
        return out | {"expected_wins": None, "expected_profit": None}
    exp_w = exp_t = exp_l = exp_profit = exp_recomputed = var = 0.0
    max_diff = 0.0
    for r in usable:
        pw, pt = r["raw_probability"], r["tie_probability"]
        pl = max(0.0, 1.0 - pw - pt)
        stake, d = r["stake"], r["entry_decimal"]
        win_amount = stake * (d - 1)
        mean = pw * win_amount - pl * stake
        exp_w += pw
        exp_t += pt
        exp_l += pl
        exp_profit += stake * r["recorded_return"]
        exp_recomputed += mean
        var += pw * win_amount ** 2 + pl * stake ** 2 - mean ** 2
        max_diff = max(max_diff, abs(nfl_ev(pw, pt, d) - r["recorded_return"]))
    sd = math.sqrt(max(var, 0.0))
    out |= {
        "stake": round(sum(r["stake"] for r in usable), 2),
        "expected_wins": exp_w, "expected_pushes": exp_t, "expected_losses": exp_l,
        "expected_profit": exp_profit, "expected_profit_recomputed": exp_recomputed,
        "max_abs_recorded_vs_recomputed_ev": max_diff,
        "sd_profit": sd, "sd_wins": math.sqrt(sum(r["raw_probability"] * (1 - r["raw_probability"]) for r in usable)),
    }
    settled = [r for r in usable if r.get("settled")]
    if len(settled) == len(usable):
        actual_net = sum(r["net_profit"] for r in usable)
        wins = sum(1 for r in usable if r.get("result") == "WIN")
        out |= {
            "actual_wins": wins,
            "actual_pushes": sum(1 for r in usable if r.get("result") in ("PUSH", "VOID")),
            "actual_losses": sum(1 for r in usable if r.get("result") == "LOSS"),
            "actual_profit": round(actual_net, 2),
            "profit_minus_expected": actual_net - exp_profit,
            "z_profit": (actual_net - exp_profit) / sd if sd > 0 else None,
            "p_wins_at_least_actual": _poisson_binomial_tail([r["raw_probability"] for r in usable], wins),
            "p_profit_at_least_actual": _mc_tail(usable, actual_net, draws=draws, seed=seed),
        }
    return out


def _mc_tail(rows: list[dict[str, Any]], actual: float, *, draws: int, seed: int) -> float | None:
    """P(profit >= actual) under the recorded probabilities (seeded Monte Carlo, independent wagers)."""

    if not rows or draws <= 0:
        return None
    import numpy as np

    rng = np.random.default_rng(seed)
    u = rng.random((draws, len(rows)))
    pw = np.array([r["raw_probability"] for r in rows])
    pt = np.array([r["tie_probability"] for r in rows])
    stake = np.array([r["stake"] for r in rows])
    win_amount = stake * (np.array([r["entry_decimal"] for r in rows]) - 1)
    profit = np.where(u < pw, win_amount, np.where(u < pw + pt, 0.0, -stake)).sum(axis=1)
    return float((profit >= actual - 1e-9).mean())


# -------------------------------------------------------------------- inputs ---

@dataclass
class Inputs:
    league: str
    window: dict[str, Any]
    snapshot: dict[str, Any]
    games: list[dict[str, Any]]
    wager_rows: list[dict[str, Any]]
    loaded: mpo.Loaded | None
    decision_detail: dict[str, dict[str, Any]] = field(default_factory=dict)
    decision_history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    forecasts: dict[str, dict[str, Any]] = field(default_factory=dict)
    later_quotes: dict[str, dict[str, Any]] = field(default_factory=dict)
    settlements: dict[str, dict[str, Any]] = field(default_factory=dict)
    now: datetime | None = None
    generator_commit: str | None = None
    game_evidence: dict[str, Any] | None = None
    game_evidence_status: dict[str, Any] | None = None


def _rows(session: Session, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in session.execute(text(sql), params).mappings().all()]


def begin_read_only(session: Session) -> bool:
    """Open the caller's transaction as REPEATABLE READ READ ONLY (PostgreSQL). Must precede every other statement.

    Returns False where the database has no such mode (SQLite in tests) or a transaction had already begun.
    """

    bind = session.get_bind()
    if getattr(getattr(bind, "dialect", None), "name", "") != "postgresql":
        return False
    try:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout = '60s'"))
        return True
    except Exception:  # noqa: BLE001 -- a transaction already under way keeps its mode; reads stay reads
        session.rollback()
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout = '60s'"))
        return True


def snapshot_identity(session: Session) -> dict[str, Any]:
    """The database clock at the snapshot and the PostgreSQL snapshot identity (xmin:xmax:xip)."""

    out: dict[str, Any] = {"isolation": None, "read_only": None, "db_clock_utc": None, "pg_snapshot": None}
    try:
        row = _rows(session, "select now() as clock, current_setting('transaction_isolation') as iso, "
                             "current_setting('transaction_read_only') as ro")[0]
        out |= {"db_clock_utc": _iso(row["clock"]), "isolation": row["iso"], "read_only": row["ro"]}
        out["pg_snapshot"] = session.execute(text("select pg_current_snapshot()::text")).scalar()
    except Exception:  # noqa: BLE001 -- SQLite (tests) has neither; the report states it
        session.rollback()
        out["db_clock_utc"] = _iso(datetime.now(UTC))
        out["note"] = "Database without snapshot identity (not PostgreSQL); the clock is the process clock."
    return out


def resolve_window(session: Session, *, league: str, season: int | None, week: int | None,
                   date_from: str | None, date_to: str | None, now: datetime) -> dict[str, Any]:
    """The week's games: by (season, week) from the producer's own week labels, or by ET slate dates."""

    if date_from or date_to:
        start = date.fromisoformat(date_from or date_to)
        end = date.fromisoformat(date_to or date_from)
        lo = datetime(start.year, start.month, start.day, tzinfo=ET).astimezone(UTC)
        hi = (datetime(end.year, end.month, end.day, tzinfo=ET) + timedelta(days=1)).astimezone(UTC)
        return {"mode": "DATES", "season": season, "week": week, "start_utc": _iso(lo), "end_utc": _iso(hi),
                "label": f"{league} slate dates {start.isoformat()} to {end.isoformat()} (ET)",
                "short_label": f"{league} {start.isoformat()}–{end.isoformat()}"}
    if season is None or week is None:
        latest = latest_week(session, league=league, now=now)
        if latest is None:
            raise LookupError(f"No {league} week with an official position was found.")
        season, week = latest["season"], latest["week"]
    bounds = _rows(session, """
        select min(g.starts_at) as lo, max(g.starts_at) as hi
          from shadow_forecasts sf join games g on g.id = sf.canonical_game_id
         where sf.season = :season and sf.week = :week and sf.engine_id like 'NFL%'""", season=season, week=week)
    if not bounds or bounds[0]["lo"] is None:
        raise LookupError(f"No {league} games are labelled season {season} week {week}.")
    lo, hi = _utc(bounds[0]["lo"]), _utc(bounds[0]["hi"])
    return {"mode": "WEEK", "season": season, "week": week, "start_utc": _iso(lo),
            "end_utc": _iso(hi + timedelta(seconds=1)),
            "label": f"{league} {season} Week {week}", "short_label": f"{league} Week {week}",
            "basis": "Games whose producer forecasts carry this season/week label, plus every league game kicking "
                     "off between the first and last of them."}


def latest_week(session: Session, *, league: str, now: datetime) -> dict[str, Any] | None:
    rows = weeks_with_positions(session, league=league)
    started = [r for r in rows if r["first_kickoff_utc"] and _utc(r["first_kickoff_utc"]) <= now]
    return (started or rows or [None])[-1]


def weeks_with_positions(session: Session, *, league: str) -> list[dict[str, Any]]:
    """Every (season, week) holding at least one official position in this league. Light: grouped ids only."""

    rows = _rows(session, """
        select sf.season, sf.week, min(g.starts_at) as first_ko, max(g.starts_at) as last_ko,
               count(distinct p.id) as positions
          from official_asp_positions p
          join market_outcomes mo on mo.id = p.market_outcome_id
          join markets m on m.id = mo.market_id
          join games g on g.id = m.game_id
          join leagues l on l.id = g.league_id
          join shadow_forecasts sf on sf.canonical_game_id = g.id
         where l.code = :league and sf.week is not null
         group by sf.season, sf.week order by sf.season, sf.week""", league=league)
    return [{"season": r["season"], "week": r["week"], "positions": r["positions"],
             "first_kickoff_utc": _iso(r["first_ko"]), "last_kickoff_utc": _iso(r["last_ko"]),
             "label": f"{league} {r['season']} Week {r['week']}"} for r in rows]


def load_inputs(session: Session, *, league: str = "NFL", season: int | None = None, week: int | None = None,
                date_from: str | None = None, date_to: str | None = None, now: datetime | None = None,
                generator_commit: str | None = None, game_evidence: dict[str, Any] | None = None) -> Inputs:
    """Every read for one report, inside the caller's transaction. Id-scoped, no loops of queries."""

    league = league.upper()
    if league not in SUPPORTED_LEAGUES:
        raise ValueError(f"MMQB v1 supports {', '.join(SUPPORTED_LEAGUES)} only (got {league}).")
    now = now or datetime.now(UTC)
    snap = snapshot_identity(session)
    window = resolve_window(session, league=league, season=season, week=week, date_from=date_from,
                            date_to=date_to, now=now)
    games = _rows(session, """
        select g.id, g.starts_at, g.status, g.home_score, g.away_score, g.final_at
          from games g join leagues l on l.id = g.league_id
         where l.code = :league and g.starts_at >= :lo and g.starts_at < :hi order by g.starts_at, g.id""",
                  league=league, lo=_utc(window["start_utc"]), hi=_utc(window["end_utc"]))
    game_ids = [g["id"] for g in games]
    teams: dict[Any, dict[str, str]] = defaultdict(dict)
    finals: dict[Any, dict[str, Any]] = {}
    counts: dict[str, dict[Any, int]] = defaultdict(dict)
    if game_ids:
        for r in _rows(session, """select gt.game_id, gt.is_home, t.abbreviation, t.name from game_teams gt
                                   join teams t on t.id = gt.team_id where gt.game_id = any(:ids)""", ids=game_ids):
            teams[r["game_id"]]["home" if r["is_home"] else "away"] = r["abbreviation"] or r["name"]
        for r in _rows(session, """select game_id, source, source_event_id, status, home_score, away_score, overtime,
                                          week, source_last_updated, retrieved_at
                                     from nfl_final_observations where game_id = any(:ids)
                                    order by retrieved_at""", ids=game_ids):
            finals[r["game_id"]] = r
        for table in ("game_state_observations", "game_scoreboard_observations", "availability_events"):
            for r in _rows(session, f"select game_id, count(*) as n from {table} where game_id = any(:ids) "
                                    f"group by game_id", ids=game_ids):
                counts[table][r["game_id"]] = r["n"]
    game_rows = []
    for g in games:
        fin = finals.get(g["id"])
        game_rows.append({
            "game_id": str(g["id"]), "kickoff_utc": _iso(g["starts_at"]), "status": mpo._status(g["status"]),
            "home_score": g["home_score"], "away_score": g["away_score"], "final_at_utc": _iso(g["final_at"]),
            "home": teams[g["id"]].get("home"), "away": teams[g["id"]].get("away"),
            "final_observation": None if not fin else {
                "source": fin["source"], "source_event_id": fin["source_event_id"], "status": fin["status"],
                "home_score": fin["home_score"], "away_score": fin["away_score"], "overtime": fin["overtime"],
                "source_last_updated": fin["source_last_updated"], "retrieved_at_utc": _iso(fin["retrieved_at"])},
            "retained_evidence": {t: counts[t].get(g["id"], 0) for t in
                                  ("game_state_observations", "game_scoreboard_observations", "availability_events")},
        })

    rows, _unfunded = wr._load(session)
    wager_rows = [r for r in rows if r["sport"] == league]
    loaded = mpo.load(session)

    week_positions = [r for r in wager_rows if r["game_id"] in {str(g) for g in game_ids}]
    decision_ids = [r["decision_id"] for r in week_positions if r["decision_id"]]
    detail: dict[str, dict[str, Any]] = {}
    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    forecasts: dict[str, dict[str, Any]] = {}
    later: dict[str, dict[str, Any]] = {}
    settlements: dict[str, dict[str, Any]] = {}
    if game_ids:
        for r in _rows(session, """
                select d.id, d.game_id, d.experiment_id, d.recommendation, d.recommendation_reason, d.qualification_outcome,
                       d.qualification_reason, d.execution_outcome, d.execution_reason, d.recommended_at, d.created_at
                  from shadow_experiment_decisions d
                 where d.game_id = any(:ids) and d.phase = 'FINAL_PREGAME' and d.sport = :league
                 order by d.recommended_at, d.id""", ids=game_ids, league=league):
            history[str(r["game_id"])].append({
                "decision_id": str(r["id"]), "experiment_id": str(r["experiment_id"]),
                "recommendation": r["recommendation"], "qualification_outcome": r["qualification_outcome"],
                "qualification_reason": r["qualification_reason"], "execution_outcome": r["execution_outcome"],
                "execution_reason": r["execution_reason"], "recommended_at_utc": _iso(r["recommended_at"]),
                "created_at_utc": _iso(r["created_at"])})
    if decision_ids:
        for r in _rows(session, """
                select d.id, d.recommended_at, d.created_at, d.quote_observed_at, d.odds_snapshot_id, d.market_outcome_id,
                       d.american_odds, d.decimal_odds, d.sportsbook, d.selection, d.model_probability, d.no_vig_probability,
                       d.edge, d.expected_value, d.qualification_outcome, d.qualification_reason, d.execution_outcome,
                       d.execution_reason, d.shadow_forecast_id, d.forecast_snapshot_phase, d.forecast_published_at,
                       d.evidence, d.staking_detail,
                       os.observed_at as snap_observed, os.available_as_of as snap_available, os.created_at as snap_created,
                       os.decimal_odds as snap_decimal, os.american_odds as snap_american, os.sportsbook_id as snap_book,
                       os.source as snap_source, sb.key as snap_book_key
                  from shadow_experiment_decisions d
                  left join odds_snapshots os on os.id = d.odds_snapshot_id
                  left join sportsbooks sb on sb.id = os.sportsbook_id
                 where d.id = any(:ids)""", ids=[_uuid(x) for x in decision_ids]):
            detail[str(r["id"])] = {
                "recommended_at_utc": _iso(r["recommended_at"]), "created_at_utc": _iso(r["created_at"]),
                "quote_observed_at_utc": _iso(r["quote_observed_at"]),
                "odds_snapshot_id": str(r["odds_snapshot_id"]) if r["odds_snapshot_id"] else None,
                "market_outcome_id": str(r["market_outcome_id"]) if r["market_outcome_id"] else None,
                "american_odds": r["american_odds"], "decimal_odds": _f(r["decimal_odds"]),
                "sportsbook": r["sportsbook"], "selection": r["selection"],
                "model_probability": _f(r["model_probability"]), "no_vig_probability": _f(r["no_vig_probability"]),
                "edge": _f(r["edge"]), "expected_value": _f(r["expected_value"]),
                "qualification_outcome": r["qualification_outcome"], "qualification_reason": r["qualification_reason"],
                "execution_outcome": r["execution_outcome"], "execution_reason": r["execution_reason"],
                "shadow_forecast_id": str(r["shadow_forecast_id"]) if r["shadow_forecast_id"] else None,
                "forecast_snapshot_phase": r["forecast_snapshot_phase"],
                "forecast_published_at_utc": _iso(r["forecast_published_at"]),
                "evidence": r["evidence"] or {}, "staking_detail": r["staking_detail"] or {},
                "entry_snapshot": None if r["snap_decimal"] is None else {
                    "observed_at_utc": _iso(r["snap_observed"]), "available_as_of_utc": _iso(r["snap_available"]),
                    "created_at_utc": _iso(r["snap_created"]), "decimal": _f(r["snap_decimal"]),
                    "american": r["snap_american"], "book_key": r["snap_book_key"], "source": r["snap_source"],
                    "sportsbook_id": str(r["snap_book"])},
            }
        # A quote whose receipt clock is after its decision's clock: the provider request that delivered it
        # (its requested_at IS the receipt's available_as_of) says when the quote could first have been read.
        late = {_utc(d["entry_snapshot"]["available_as_of_utc"]) for d in detail.values() if d["entry_snapshot"]
                and _utc(d["entry_snapshot"]["available_as_of_utc"]) > _utc(d["recommended_at_utc"])}
        if late:
            ledger = {_utc(r["requested_at"]): r for r in _rows(session, """
                    select requested_at, completed_at, status from provider_request_ledger
                     where requested_at = any(:ts)""", ts=sorted(late))}
            for d in detail.values():
                entry_snap = d["entry_snapshot"]
                hit = ledger.get(_utc(entry_snap["available_as_of_utc"])) if entry_snap else None
                if hit:
                    entry_snap["request_started_at_utc"] = _iso(hit["requested_at"])
                    entry_snap["request_completed_at_utc"] = _iso(hit["completed_at"])
        fc_ids = set()
        for d in detail.values():
            for candidate in (d["shadow_forecast_id"], (d["evidence"] or {}).get("shadow_forecast_row_id")):
                if candidate:
                    fc_ids.add(_uuid(candidate))
        fc_ids.discard(None)
        if fc_ids:
            for r in _rows(session, """
                    select id, event_id, season, week, snapshot_type, snapshot_version, engine_id, engine_version,
                           available_as_of_utc, forecast_cutoff_utc, full_home_win_probability, full_away_win_probability,
                           full_expected_home_score, full_expected_away_score, distributions
                      from shadow_forecasts where id = any(:ids)""", ids=list(fc_ids)):
                dist = r["distributions"] or {}
                forecasts[str(r["id"])] = {
                    "row_id": str(r["id"]), "event_id": r["event_id"], "season": r["season"], "week": r["week"],
                    "snapshot_type": r["snapshot_type"], "snapshot_version": r["snapshot_version"],
                    "engine": f"{r['engine_id']} {r['engine_version']}",
                    "available_utc": _iso(r["available_as_of_utc"]), "cutoff_utc": _iso(r["forecast_cutoff_utc"]),
                    "p_home": _f(r["full_home_win_probability"]), "p_away": _f(r["full_away_win_probability"]),
                    "p_tie": _f(dist.get("tie_probability")),
                    "expected_home": _f(r["full_expected_home_score"]), "expected_away": _f(r["full_expected_away_score"]),
                    "margin_distribution": dist.get("score_margin_distribution") or [],
                    "total_distribution": dist.get("total_score_distribution") or [],
                    "joint_distribution": dist.get("joint_score_distribution") or [],
                    "prediction_interval": dist.get("prediction_interval") or {},
                    "most_likely_scores": dist.get("most_likely_scores") or [],
                }
        position_ids = [r["position_id"] for r in week_positions]
        for r in _rows(session, """
                select p.id as position_id, pr.result, pr.settled_at, pr.profit_loss, pr.game_home_score,
                       pr.game_away_score, pr.settlement_note, pr.closing_odds_snapshot_id, pr.closing_decimal_odds,
                       pr.closing_line_value, p.decimal_odds as position_decimal, p.odds_snapshot_id as position_snapshot,
                       p.placed_at
                  from official_asp_positions p left join position_results pr on pr.position_id = p.id
                 where p.id = any(:ids)""", ids=[_uuid(x) for x in position_ids]):
            settlements[str(r["position_id"])] = {
                "result": mpo._status(r["result"]) or None, "settled_at_utc": _iso(r["settled_at"]),
                "profit_loss": _f(r["profit_loss"]), "game_home_score": r["game_home_score"],
                "game_away_score": r["game_away_score"], "settlement_note": r["settlement_note"],
                "closing_odds_snapshot_id": str(r["closing_odds_snapshot_id"]) if r["closing_odds_snapshot_id"] else None,
                "closing_decimal_odds": _f(r["closing_decimal_odds"]), "closing_line_value": _f(r["closing_line_value"]),
                "position_decimal": _f(r["position_decimal"]),
                "position_snapshot_id": str(r["position_snapshot"]) if r["position_snapshot"] else None,
                "placed_at_utc": _iso(r["placed_at"])}
        # Later same-outcome quotes (either leg) up to kickoff, for the "last observed quote" label only.
        outcome_ids = {d["market_outcome_id"] for d in detail.values() if d["market_outcome_id"]}
        if outcome_ids:
            market_rows = _rows(session, """select mo.id, mo.market_id from market_outcomes mo
                                            where mo.market_id in (select market_id from market_outcomes where id = any(:ids))""",
                                ids=[_uuid(x) for x in outcome_ids])
            by_market: dict[str, list[str]] = defaultdict(list)
            market_of: dict[str, str] = {}
            for m in market_rows:
                by_market[str(m["market_id"])].append(str(m["id"]))
                market_of[str(m["id"])] = str(m["market_id"])
            all_outcomes = [_uuid(x) for x in market_of]
            quotes = _rows(session, """
                select os.id, os.market_outcome_id, os.sportsbook_id, os.observed_at, os.available_as_of, os.decimal_odds,
                       os.american_odds, os.source, os.is_closing
                  from odds_snapshots os where os.market_outcome_id = any(:ids)""", ids=all_outcomes)
            game_ko = {str(g["id"]): _utc(g["starts_at"]) for g in games}
            for r in week_positions:
                d = detail.get(r["decision_id"] or "")
                if not d or not d["entry_snapshot"] or not d["market_outcome_id"]:
                    continue
                later[r["position_id"]] = last_observed_quote(
                    quotes, selected_outcome=d["market_outcome_id"],
                    market_outcomes=by_market.get(market_of.get(d["market_outcome_id"], ""), []),
                    book_id=d["entry_snapshot"]["sportsbook_id"], entry=d["entry_snapshot"],
                    entry_snapshot_id=d["odds_snapshot_id"], kickoff=game_ko.get(r["game_id"]))
    return Inputs(league=league, window=window, snapshot=snap, games=game_rows, wager_rows=wager_rows,
                  loaded=loaded, decision_detail=detail, decision_history=dict(history), forecasts=forecasts,
                  later_quotes=later, settlements=settlements, now=now, generator_commit=generator_commit,
                  game_evidence=game_evidence)


def _uuid(value: Any) -> Any:
    return mpo._uuid(value)


def last_observed_quote(quotes: list[dict[str, Any]], *, selected_outcome: str, market_outcomes: list[str],
                        book_id: str, entry: dict[str, Any], entry_snapshot_id: str | None,
                        kickoff: datetime | None) -> dict[str, Any]:
    """The latest same-book quote for the selected outcome observed after the entry quote and available before
    kickoff. Always labelled LAST_OBSERVED_QUOTE, never a close; whether its opposite leg shares its receipt
    (the same ``available_as_of`` receipt stamp, odds_snapshots carries no run id) is recorded as a fact, and still
    does not make it a close here."""

    entry_obs = _utc(entry["observed_at_utc"])
    same_book = [q for q in quotes if str(q["sportsbook_id"]) == book_id and str(q["id"]) != entry_snapshot_id]
    mine = [q for q in same_book if str(q["market_outcome_id"]) == selected_outcome
            and _utc(q["observed_at"]) > entry_obs and (kickoff is None or _utc(q["available_as_of"]) < kickoff)]
    base = {"label": "LAST_OBSERVED_QUOTE", "is_closing_line": False, "rule": GENUINE_CLOSE_RULE,
            "later_same_book_quotes": len(mine)}
    if not mine:
        return base | {"quote": None, "statement": "No later quote from the entry book was retained before kickoff. "
                                                  "The entry quote is the last observed quote."}
    last = max(mine, key=lambda q: (_utc(q["observed_at"]), _utc(q["available_as_of"])))
    opposite = [q for q in same_book if str(q["market_outcome_id"]) in market_outcomes
                and str(q["market_outcome_id"]) != selected_outcome
                and q["available_as_of"] == last["available_as_of"]]
    return base | {
        "quote": {"observed_at_utc": _iso(last["observed_at"]), "available_as_of_utc": _iso(last["available_as_of"]),
                  "decimal": _f(last["decimal_odds"]), "american": last["american_odds"], "source": last["source"]},
        "opposite_leg_same_receipt": bool(opposite),
        "statement": "Later same-book price retained; labelled last observed quote, not a closing line.",
    }


# ------------------------------------------------------------------ assembly ---

def _team(row: dict[str, Any], side: str | None) -> str | None:
    if side not in ("HOME", "AWAY"):
        return None
    away, _, home = (row.get("matchup") or "? @ ?").partition(" @ ")
    return home if side == "HOME" else away


def slate_row(r: dict[str, Any], *, detail: dict[str, Any] | None, forecast: dict[str, Any] | None,
              decision: dict[str, Any] | None, game: dict[str, Any] | None, later: dict[str, Any] | None,
              settlement: dict[str, Any] | None) -> dict[str, Any]:
    """One official position with its decision-time numbers. Pure."""

    detail = detail or {}
    ev = detail.get("evidence") or {}
    sel = ev.get("selection") if isinstance(ev.get("selection"), dict) else {}
    side = r.get("selected_side")
    pw, pt = r.get("raw_probability"), r.get("tie_probability")
    pl = (1.0 - pw - pt) if (pw is not None and pt is not None) else None
    two_way_sel = r.get("comparison_probability")
    d = r.get("entry_decimal")
    opp_dec = american_to_decimal(sel.get("opposite_american"))
    nv = no_vig_pair(d, opp_dec)
    mkt = (decision or {}).get("market") or {}
    mkt_home = mkt.get("p_home")
    mkt_sel = (mkt_home if side == "HOME" else 1 - mkt_home) if (mkt_home is not None and side) else None
    fc = forecast or {}
    p_home2 = mpo.two_way(fc.get("p_home"), fc.get("p_away"))
    model_fav = None if p_home2 is None or p_home2 == 0.5 else ("HOME" if p_home2 > 0.5 else "AWAY")
    kickoff = _utc(r.get("starts_at"))
    decided_at = _utc(detail.get("recommended_at_utc") or r.get("decision_at"))
    available = _utc(fc.get("available_utc"))
    entry = detail.get("entry_snapshot") or {}
    q_obs, q_av = _utc(entry.get("observed_at_utc")), _utc(entry.get("available_as_of_utc"))
    status = r.get("status")
    g = game or {}
    final = ({"home": g.get("home_score"), "away": g.get("away_score")}
             if g.get("status") == "FINAL" and g.get("home_score") is not None else None)
    disagreement = ((two_way_sel - nv) * 100) if (two_way_sel is not None and nv is not None) else None
    return {
        "position_id": r["position_id"], "decision_id": r.get("decision_id"), "game_id": r["game_id"],
        "matchup": r.get("matchup"), "home": _team(r, "HOME"), "away": _team(r, "AWAY"),
        "kickoff_utc": _iso(kickoff), "kickoff_et": et_label(kickoff), "kickoff_z": zulu(kickoff),
        "selected_side": side, "selected_team": _team(r, side), "selection_label": r.get("selection"),
        "model_favourite_side": model_fav, "model_favourite_team": _team(r, model_fav),
        "model_favourite_p_two_way": (p_home2 if model_fav == "HOME" else 1 - p_home2) if model_fav else None,
        "selected_is_model_favourite": (model_fav == side) if (model_fav and side) else None,
        "p_win": pw, "p_tie": pt, "p_loss": pl, "p_win_two_way": two_way_sel,
        "probability_source": "decision evidence.selection (win/tie/loss) — recorded at the decision",
        "forecast_row_id": fc.get("row_id"), "forecast_snapshot_type": fc.get("snapshot_type"),
        "forecast_type": mpo.forecast_type(fc.get("snapshot_type")) if fc else None,
        "forecast_snapshot": f"{fc.get('snapshot_type')} v{fc.get('snapshot_version')}" if fc else None,
        "forecast_available_utc": _iso(available),
        "forecast_lead_hours": ((kickoff - available).total_seconds() / 3600) if (kickoff and available) else None,
        "forecast_age_at_decision_hours": ((decided_at - available).total_seconds() / 3600) if (decided_at and available) else None,
        "decision_at_utc": _iso(decided_at),
        "decision_minutes_before_kickoff": ((kickoff - decided_at).total_seconds() / 60) if (kickoff and decided_at) else None,
        "sportsbook": r.get("sportsbook"), "book_key": entry.get("book_key") or sel.get("sportsbook"),
        "entry_american": r.get("entry_american"), "entry_decimal": d,
        "opposite_american": sel.get("opposite_american"),
        "quote_observed_at_utc": _iso(q_obs), "quote_available_utc": _iso(q_av),
        "quote_book_age_minutes": ((decided_at - q_obs).total_seconds() / 60) if (decided_at and q_obs) else None,
        "quote_available_age_minutes": ((decided_at - q_av).total_seconds() / 60) if (decided_at and q_av) else None,
        "no_vig_selected": nv, "no_vig_source": "multiplicative no-vig of the decision's own recorded pair",
        "market_median_selected": mkt_sel, "market_median_books": mkt.get("n_books"),
        "model_minus_market_pts": disagreement,
        "large_disagreement": (abs(disagreement) >= DISAGREEMENT_PTS) if disagreement is not None else None,
        "price_group": None if nv is None else ("FAVOURITE" if nv >= 0.5 else "UNDERDOG"),
        "stake": r.get("stake"), "recorded_ev": r.get("recorded_return"),
        "expected_profit": r.get("estimated_profit"),
        "status": status, "settled": bool(r.get("settled")), "result": r.get("result"),
        "net_profit": r.get("net_profit"), "settled_at_utc": r.get("settled_at"),
        "game_status": g.get("status"), "final": final,
        "overtime": ((g.get("final_observation") or {}).get("overtime")),
        "last_observed_quote": later or {"label": "LAST_OBSERVED_QUOTE", "is_closing_line": False, "quote": None,
                                         "rule": GENUINE_CLOSE_RULE,
                                         "statement": "No later quote evaluated."},
        "closing_line": "NOT_CAPTURED" if not (settlement or {}).get("closing_odds_snapshot_id") else "RECORDED_BUT_UNVERIFIED",
        "binding_status": r.get("binding_status"), "flags": list(r.get("flags") or []),
        "probability_band": r.get("probability_band"), "return_band": r.get("return_band"),
        "experiment_id": r.get("experiment_id"),
        "experiment_name": r.get("experiment_name"),
    }


def group_block(label: str, key: str, rows: list[dict[str, Any]], wmap: dict[str, dict[str, Any]]) -> dict[str, Any]:
    wrows = [wmap[r["position_id"]] for r in rows]
    settled = [w for w in wrows if w["settled"]]
    return {"key": key, "label": label, "n": len(rows), **{k: v for k, v in wr.summarize(wrows).items() if k in (
        "settled", "wins", "losses", "pushes", "settled_stake", "net_profit", "roi", "open_count", "open_exposure")},
            "expectation_settled": expectation(settled, draws=0)}


def market_comparison(rows: list[dict[str, Any]], decisions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Model vs market on identically qualified games: the funded decisions, same game, same decision time.

    Two-way, conditional on no tie. Model = the forecast the decision referenced (two-way home). Market (a) =
    the decision's own recorded pair, no-vig; (b) = the Overview's median-of-books reference at the decision.
    """

    per_game = []
    for r in rows:
        dec = decisions.get(r.get("decision_id") or "")
        if not dec or dec.get("actual_outcome") not in ("HOME", "AWAY") or dec.get("availability_status") != "AVAILABLE":
            continue
        p_model = dec.get("p_home_two_way")
        nv_sel = r.get("no_vig_selected")
        p_pair = (nv_sel if r["selected_side"] == "HOME" else 1 - nv_sel) if nv_sel is not None else None
        p_med = (dec.get("market") or {}).get("p_home")
        home_won = dec["actual_outcome"] == "HOME"
        item = {"position_id": r["position_id"], "matchup": r["matchup"], "home_won": home_won,
                "model_p_home": p_model, "pair_p_home": p_pair, "median_p_home": p_med,
                "median_books": (dec.get("market") or {}).get("n_books")}
        for name, p in (("model", p_model), ("pair", p_pair), ("median", p_med)):
            item[f"{name}_log_loss"] = mpo.log_loss_two_way(p, home_won) if p is not None else None
            item[f"{name}_brier"] = mpo.brier_two_way(p, home_won) if p is not None else None
        per_game.append(item)

    def paired(market: str) -> dict[str, Any] | None:
        use = [g for g in per_game if g["model_log_loss"] is not None and g[f"{market}_log_loss"] is not None]
        if not use:
            return None
        d_ll = [g["model_log_loss"] - g[f"{market}_log_loss"] for g in use]
        d_br = [g["model_brier"] - g[f"{market}_brier"] for g in use]
        return {"n": len(use),
                "model_log_loss": sum(g["model_log_loss"] for g in use) / len(use),
                "market_log_loss": sum(g[f"{market}_log_loss"] for g in use) / len(use),
                "delta_log_loss": sum(d_ll) / len(d_ll), "delta_log_loss_ci95": mpo.bootstrap_mean_ci(d_ll),
                "model_brier": sum(g["model_brier"] for g in use) / len(use),
                "market_brier": sum(g[f"{market}_brier"] for g in use) / len(use),
                "delta_brier": sum(d_br) / len(d_br), "delta_brier_ci95": mpo.bootstrap_mean_ci(d_br),
                "model_better_games": sum(1 for x in d_ll if x < 0)}

    return {"games": per_game, "vs_decision_pair": paired("pair"), "vs_median_of_books": paired("median"),
            "excluded": len(rows) - len(per_game),
            "note": "Same games, same decision time. Two-way log loss and Brier conditional on no tie; negative "
                    "delta favours the model. The median-of-books reference follows the Overview's strict clock "
                    "(quotes observed AND available at or before the decision)."}


def game_story(row: dict[str, Any], *, forecast: dict[str, Any] | None, decision: dict[str, Any] | None,
               detail: dict[str, Any] | None, history: list[dict[str, Any]], game: dict[str, Any] | None,
               settlement: dict[str, Any] | None) -> dict[str, Any]:
    """Pregame rationale, postgame result, record checks and open hypotheses for one position. Pure."""

    fc = forecast or {}
    detail = detail or {}
    ev = detail.get("evidence") or {}
    sel = ev.get("selection") if isinstance(ev.get("selection"), dict) else {}
    g = game or {}
    exp_home, exp_away = fc.get("expected_home"), fc.get("expected_away")
    exp_margin = (exp_home - exp_away) if (exp_home is not None and exp_away is not None) else None
    exp_total = (exp_home + exp_away) if (exp_home is not None and exp_away is not None) else None
    pregame = {
        "label": "PREGAME (retained evidence only)",
        "forecast": {"row_id": fc.get("row_id"), "event_id": fc.get("event_id"), "snapshot": row["forecast_snapshot"],
                     "engine": fc.get("engine"), "available_utc": fc.get("available_utc"),
                     "p_home": fc.get("p_home"), "p_away": fc.get("p_away"), "p_tie": fc.get("p_tie"),
                     "expected_home": exp_home, "expected_away": exp_away, "expected_margin_home": exp_margin,
                     "expected_total": exp_total, "prediction_interval": fc.get("prediction_interval"),
                     "most_likely_scores": (fc.get("most_likely_scores") or [])[:3]},
        "selected": {"team": row["selected_team"], "side": row["selected_side"], "p_win": row["p_win"],
                     "p_tie": row["p_tie"], "p_loss": row["p_loss"], "p_win_two_way": row["p_win_two_way"]},
        "model_favourite": {"team": row["model_favourite_team"], "p_two_way": row["model_favourite_p_two_way"]},
        "market": {"book": row["sportsbook"], "american": row["entry_american"], "decimal": row["entry_decimal"],
                   "opposite_american": row["opposite_american"], "no_vig_selected": row["no_vig_selected"],
                   "median_selected": row["market_median_selected"], "median_books": row["market_median_books"],
                   "quote_book_age_minutes": row["quote_book_age_minutes"]},
        "ev": row["recorded_ev"], "stake": row["stake"],
        "staking": {k: (detail.get("staking_detail") or {}).get(k) for k in ("bound_by", "quarter_kelly", "position_cap",
                                                                               "recommended_kelly_stake")},
        "gates": {"outcome": ev.get("outcome"), "reason": ev.get("reason"),
                  "readiness_verified": (ev.get("readiness") or {}).get("verified"),
                  "exposure_status": (ev.get("readiness") or {}).get("exposure_status"),
                  "eligibility": (ev.get("eligibility") or {}).get("reason"),
                  "pair_age_minutes": sel.get("pair_age_minutes"), "settlement_rule": sel.get("settlement")},
    }
    final = row["final"]
    post: dict[str, Any] = {"label": "POSTGAME", "status": row["game_status"], "final": final}
    if final:
        margin = final["home"] - final["away"]
        total = final["home"] + final["away"]
        pi = fc.get("prediction_interval") or {}
        margin_pct = mid_percentile(fc.get("margin_distribution"), "home_minus_away", margin)
        total_pct = mid_percentile(fc.get("total_distribution"), "total_score", total)
        post |= {
            "margin_home": margin, "expected_margin_home": exp_margin,
            "margin_error": mpo.signed_error(exp_margin, margin),
            "total": total, "expected_total": exp_total, "total_error": mpo.signed_error(exp_total, total),
            "total_direction": mpo.error_direction(mpo.signed_error(exp_total, total)),
            "margin_percentile": margin_pct, "total_percentile": total_pct,
            "exact_score_probability": exact_score_probability(fc.get("joint_distribution"), final["home"], final["away"]),
            "margin_in_90": (pi.get("margin_lower") is not None and pi["margin_lower"] <= margin <= pi["margin_upper"])
            if pi else None,
            "total_in_90": (pi.get("total_lower") is not None and pi["total_lower"] <= total <= pi["total_upper"])
            if pi else None,
            "overtime": row["overtime"], "winner": "HOME" if margin > 0 else "AWAY" if margin < 0 else "TIE",
            "result": row["result"], "net_profit": row["net_profit"],
        }
    post["retained_game_evidence"] = (g.get("retained_evidence") or {}) | {
        "final_observation": bool(g.get("final_observation"))}
    post["play_by_play"] = NO_PLAY_BY_PLAY
    checks = record_checks(row, forecast=fc, decision=decision, detail=detail, history=history, game=g,
                           settlement=settlement)
    established = [c for c in checks if c["status"] == "FLAG"]
    return {"position_id": row["position_id"], "matchup": row["matchup"], "selected_team": row["selected_team"],
            "kickoff_utc": row["kickoff_utc"], "pregame": pregame, "postgame": post, "checks": checks,
            "established": [c["detail"] for c in established],
            "established_summary": ("No input, forecast, price/execution or settlement problem is established by the "
                                    "retained records." if not established else
                                    f"{len(established)} record issue(s) established; see checks."),
            "hypotheses": hypotheses(row, post, fc)}


def record_checks(row: dict[str, Any], *, forecast: dict[str, Any], decision: dict[str, Any] | None,
                  detail: dict[str, Any], history: list[dict[str, Any]], game: dict[str, Any],
                  settlement: dict[str, Any] | None) -> list[dict[str, Any]]:
    """What the records can establish. PASS / FLAG / INFO / PENDING / NOT_CHECKABLE, each with its evidence."""

    out: list[dict[str, Any]] = []

    def add(key: str, area: str, status: str, detail_text: str, statement: str | None = None) -> None:
        out.append({"key": key, "area": area, "status": status, "detail": detail_text,
                    **({"statement": statement} if statement else {})})

    ev = detail.get("evidence") or {}
    sel = ev.get("selection") if isinstance(ev.get("selection"), dict) else {}
    decided = _utc(detail.get("recommended_at_utc"))
    # -- forecast
    status = (decision or {}).get("availability_status")
    if status == "AVAILABLE":
        add("forecast_available_before_decision", "forecast", "PASS",
            f"Decision-bound forecast available {row['forecast_age_at_decision_hours']:.2f} h before the decision "
            f"(shadow_forecasts.available_as_of_utc).")
    elif status:
        add("forecast_available_before_decision", "forecast", "FLAG", f"Decision-bound forecast status {status}.")
    else:
        add("forecast_available_before_decision", "forecast", "NOT_CHECKABLE", "No governing decision record joined.")
    kind = row.get("forecast_type")
    age = row.get("forecast_age_at_decision_hours")
    if kind == "FINAL" and age is not None and age <= FRESH_FORECAST_HOURS:
        add("forecast_type_and_age", "forecast", "PASS", f"FINAL_PREGAME snapshot, {age:.2f} h old at the decision.")
    elif kind:
        add("forecast_type_and_age", "forecast", "INFO",
            f"{row['forecast_snapshot']} forecast, {age:.1f} h old at the decision." if age is not None else
            f"{row['forecast_snapshot']} forecast; age unknown.")
    bound = detail.get("shadow_forecast_id") or ev.get("shadow_forecast_row_id")
    if bound and forecast.get("row_id") and str(bound) == forecast["row_id"]:
        add("decision_forecast_identity", "forecast", "PASS", f"Decision names forecast row {str(bound)[:8]}; "
                                                               f"the same row supplies every forecast number here.")
    else:
        add("decision_forecast_identity", "forecast", "NOT_CHECKABLE", "Decision's forecast row not resolved.")
    frozen = (ev.get("eligibility") or {}).get("frozen_probabilities") or {}
    if frozen and forecast.get("p_home") is not None and row["p_win"] is not None:
        side_key = "home_win" if row["selected_side"] == "HOME" else "away_win"
        fc_side = forecast["p_home"] if row["selected_side"] == "HOME" else forecast["p_away"]
        ok = abs(_f(frozen.get(side_key)) - row["p_win"]) < 1e-9 and abs(fc_side - row["p_win"]) < 1e-9
        add("probabilities_match_forecast", "forecast", "PASS" if ok else "FLAG",
            f"Selected-side pWin {row['p_win']:.4f}: decision evidence, frozen eligibility probabilities and the "
            f"forecast row {'agree' if ok else 'DISAGREE'}.")
    # -- price / execution
    entry = detail.get("entry_snapshot") or {}
    q_av = _utc(entry.get("available_as_of_utc"))
    if q_av and decided:
        gap_ms = (q_av - decided).total_seconds() * 1000
        if gap_ms <= 0:
            add("quote_available_before_decision", "price", "PASS",
                f"Entry quote available {-gap_ms / 60000:.1f} min before the decision (odds_snapshots.available_as_of).")
        else:
            done = _utc(entry.get("request_completed_at_utc"))
            clocks = (f"Clocks (UTC): decision recommended_at {_hms(decided)}; decision row created_at "
                      f"{_hms(detail.get('created_at_utc'))} (transaction start); entry quote available_as_of "
                      f"{_hms(q_av)} (= the provider request start); quote row created_at "
                      f"{_hms(entry.get('created_at_utc'))}; request completed_at {_hms(done)}. ")
            measured = (f"Quote completion follows the recorded tick-start decision time by "
                        f"{(done - decided).total_seconds():.6f} s" if done else
                        f"The quote's receipt clock follows the recorded decision time by {gap_ms:.3f} ms")
            measured += (f" (the delivering request started {gap_ms:.3f} ms after it)." if done else ".")
            ruling = EXTERNAL_RULINGS.get(row.get("decision_id") or "")
            review = (f"{ruling['text']} [{ruling['source']}]" if ruling else
                      "Pending review of the actual receipt and read evidence; no conclusion is drawn beyond the "
                      "recorded clocks.")
            add("quote_available_before_decision", "price", "FLAG",
                clocks + measured + " " + review + f" The price was {row['quote_book_age_minutes']:.1f} min old on the "
                f"book's clock; under the Overview's strict clock no median-of-books reference exists at this decision.",
                statement=measured + " " + review)
    else:
        add("quote_available_before_decision", "price", "NOT_CHECKABLE", "Entry snapshot not joined.")
    book_age = row.get("quote_book_age_minutes")
    if book_age is not None:
        add("quote_age_at_decision", "price", "INFO" if book_age > STALE_BOOK_QUOTE_MINUTES else "PASS",
            f"Entry price {book_age:.1f} min old on the book's clock at the decision "
            f"(pair age recorded {sel.get('pair_age_minutes')} min).")
    snap_dec, snap_am = entry.get("decimal"), entry.get("american")
    pos_dec = (settlement or {}).get("position_decimal") or row.get("entry_decimal")
    if snap_dec is not None:
        ok = (abs(snap_dec - (pos_dec or 0)) < 1e-9 and (detail.get("american_odds") in (None, snap_am))
              and (sel.get("american_odds") in (None, snap_am))
              and (settlement or {}).get("position_snapshot_id") in (None, detail.get("odds_snapshot_id")))
        add("odds_match_snapshot", "price", "PASS" if ok else "FLAG",
            f"Position {(pos_dec or 0):.4f}, decision {detail.get('american_odds')}, evidence {sel.get('american_odds')} and "
            f"snapshot {snap_am} ({snap_dec:.4f}) {'match' if ok else 'DO NOT match'}; position and decision name the same "
            f"snapshot.")
    if row["p_win"] is not None and row["p_tie"] is not None and row["entry_decimal"] and row["recorded_ev"] is not None:
        recomputed = nfl_ev(row["p_win"], row["p_tie"], row["entry_decimal"])
        ok = abs(recomputed - row["recorded_ev"]) < 1e-9
        add("ev_arithmetic", "price", "PASS" if ok else "FLAG",
            f"Recorded EV {row['recorded_ev']:+.4f} {'equals' if ok else 'DIFFERS FROM'} pWin(d-1) - pLoss = "
            f"{recomputed:+.4f} (tie refunded).")
    earlier = [h for h in history if h["decision_id"] != row["decision_id"]
               and (h["recommended_at_utc"] or "") < (detail.get("recommended_at_utc") or "")]
    if earlier:
        first = earlier[0]
        delay = (decided - _utc(first["recommended_at_utc"])).total_seconds() / 60 if decided else None
        add("first_in_window_decision", "execution", "INFO",
            f"An earlier FINAL_PREGAME decision at {zulu(first['recommended_at_utc'])} recorded "
            f"{first['recommendation']} ({first['qualification_reason']}); the funded decision followed "
            f"{delay:.0f} min later. This is the collection/decision ordering race documented in the decision-repairs "
            f"package: the position was delayed one tick and used the quote that collection had just delivered.")
    # -- settlement
    s = settlement or {}
    if row["settled"]:
        fin = game.get("final_observation") or {}
        scores = {(s.get("game_home_score"), s.get("game_away_score")), (game.get("home_score"), game.get("away_score"))}
        if fin:
            scores.add((fin.get("home_score"), fin.get("away_score")))
        margin = (game.get("home_score") or 0) - (game.get("away_score") or 0)
        expected_result = ("PUSH" if margin == 0 else
                           "WIN" if (margin > 0) == (row["selected_side"] == "HOME") else "LOSS")
        d = row["entry_decimal"] or 0
        expected_profit = {"WIN": round(row["stake"] * (d - 1), 2), "LOSS": -row["stake"], "PUSH": 0.0}[expected_result]
        ok = (len(scores) == 1 and row["result"] == expected_result
              and abs((row["net_profit"] or 0) - expected_profit) <= 0.01)
        add("settlement_matches_score", "settlement", "PASS" if ok else "FLAG",
            f"Settlement {row['result']} {row['net_profit']:+.2f} on "
            f"{game.get('away_score')}-{game.get('home_score')} (games, position_results"
            f"{', nflverse final' if fin else ''} {'agree' if len(scores) == 1 else 'DISAGREE'}); "
            f"{row['selected_side'].lower()} side implies {expected_result} {expected_profit:+.2f}.")
    else:
        add("settlement_matches_score", "settlement", "PENDING",
            f"Not settled at the data cutoff (game status {game.get('status')}).")
    lq = row.get("last_observed_quote") or {}
    add("closing_line", "price", "INFO",
        ("No genuine closing line: " + (lq.get("statement") or "")) if not lq.get("quote") else
        f"Last observed quote {lq['quote']['american']} at {zulu(lq['quote']['observed_at_utc'])} (book clock); "
        f"not a closing line under the capture rule.")
    return out


def hypotheses(row: dict[str, Any], post: dict[str, Any], fc: dict[str, Any]) -> list[str]:
    """What stays a hypothesis. Worded as questions for a test, never as a finding."""

    out: list[str] = []
    gap = row.get("model_minus_market_pts")
    if gap is not None and abs(gap) >= DISAGREEMENT_PTS:
        out.append(f"The model rated {row['selected_team']} {gap:+.1f} pts above the market's no-vig price. Whether the "
                   f"model saw something the market missed, or the market priced information the model lacks, cannot be "
                   f"told from one game. Test across the season: are large-disagreement picks better or worse calibrated "
                   f"than the market on the same games ({EXPERIMENT_LINKS['R2']}).")
    mp, tp = post.get("margin_percentile"), post.get("total_percentile")
    if mp is not None and (mp <= TAIL or mp >= 1 - TAIL):
        out.append(f"The final margin sat at the {mp * 100:.0f}th percentile of the forecast's margin distribution. "
                   f"Testable assumption: the margin distribution's width, checked on the season's interval coverage, "
                   f"not on this game.")
    if tp is not None and (tp <= TAIL or tp >= 1 - TAIL):
        out.append(f"The total ({post.get('total')}) sat at the {tp * 100:.0f}th percentile of the forecast's total "
                   f"distribution (expected {post.get('expected_total'):.1f}). Check the season total bias before "
                   f"reading anything into it.")
    if row.get("forecast_age_at_decision_hours") is not None and row["forecast_age_at_decision_hours"] > FRESH_FORECAST_HOURS:
        out.append("The forecast was more than 6 h old at the decision; freshness is a candidate explanation for any gap "
                   f"({EXPERIMENT_LINKS['CAPTURE']}).")
    if row.get("quote_book_age_minutes") is not None and row["quote_book_age_minutes"] > STALE_BOOK_QUOTE_MINUTES:
        out.append(f"The entry price was {row['quote_book_age_minutes']:.0f} min old on the book's clock. Whether the "
                   f"book moved before kickoff is unknown: no genuine close was captured "
                   f"({EXPERIMENT_LINKS['CAPTURE']}).")
    if not out:
        out.append("Nothing in the retained records singles this game out; the result is one draw from the forecast.")
    out.append("In-game causes are not assessed: no play-by-play is retained.")
    return out


def _ci_text(ci: list[float] | None) -> str:
    return f" [{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else ""


def _cum_wagers(rows: list[dict[str, Any]]) -> dict[str, Any]:
    s = wr.summarize(rows)
    return {k: s.get(k) for k in ("n", "settled", "wins", "losses", "pushes", "settled_stake", "net_profit", "roi",
                                  "roi_ci95", "open_count", "open_exposure", "expected_wins",
                                  "actual_wins_with_probability", "comparison_n")}


def findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The three most useful findings, each with the next test it suggests. Data-driven, ordered candidates."""

    out: list[dict[str, Any]] = []
    fin = payload["financial"]
    groups = {g["key"]: g for g in fin["groups"]["disagreement"]}
    big = groups.get("LARGE")
    mc = payload["model_vs_market"]["week"]
    pair = mc.get("vs_decision_pair") or {}
    cum = payload["model_vs_market"]["season_overview"] or {}
    cum_dt = (cum.get("market_decision_time") or {})
    if big and big["n"]:
        rec = f"{big['wins']}-{big['losses']}" + (f"-{big['pushes']}" if big["pushes"] else "")
        exp = big["expectation_settled"]
        text_ = (f"The model's biggest disagreements with the market (|model − no-vig| ≥ {DISAGREEMENT_PTS:.0f} pts) "
                 f"were {big['n']} of the week's positions and went {rec} (net {big['net_profit']:+.2f} dollars on "
                 f"{big['settled_stake']:.2f} settled)")
        if exp.get("expected_wins") is not None:
            text_ += f", against {exp['expected_wins']:.1f} expected wins by the model's own numbers"
        text_ += "."
        if pair:
            text_ += (f" On the same funded games at the same decision time the model's two-way log loss was "
                      f"{pair['model_log_loss']:.3f} against the decision pair's {pair['market_log_loss']:.3f} "
                      f"(Δ {pair['delta_log_loss']:+.3f}" + _ci_text(pair.get("delta_log_loss_ci95")) + f", n={pair['n']}). "
                      + verdict(pair))
        if cum_dt:
            text_ += (f" Season to date (Overview, median of books): Δ {cum_dt['delta_log_loss']:+.3f}"
                      + _ci_text(cum_dt.get("delta_log_loss_ci95")) + f", n={cum_dt['n']}. " + verdict(cum_dt))
        out.append({"key": "DISAGREEMENT", "title": "The largest model–market disagreements",
                    "finding": text_,
                    "next_test": "Score every NFL decision's large-disagreement subset against the market on the same "
                                 "games, season to date, with a pre-set minimum n before any reading; this is the R2 "
                                 "market-anchored residual question. Do not shrink or cap disagreements on one week.",
                    "spoken": "We'll score every big model-versus-market disagreement all season, on the same games as "
                              "the market, and we won't read anything into it until the sample is big enough.",
                    "experiment": EXPERIMENT_LINKS["R2"]})
    timing = [c for s in payload["games"] for c in s["checks"]
              if c["key"] in ("first_in_window_decision", "quote_available_before_decision") and c["status"] in ("INFO", "FLAG")]
    delayed_ids = {s["position_id"] for s in payload["games"]
                   if any(c["key"] == "first_in_window_decision" for c in s["checks"])}
    delayed = len(delayed_ids)
    clusters = {s["kickoff_utc"] for s in payload["slate"]}
    delayed_clusters = {s["kickoff_utc"] for s in payload["slate"] if s["position_id"] in delayed_ids}
    clock = [(c, s["matchup"]) for s in payload["games"] for c in s["checks"]
             if c["key"] == "quote_available_before_decision" and c["status"] == "FLAG"]
    if delayed or clock:
        text_ = []
        if delayed:
            text_.append(f"{delayed} of {len(payload['games'])} positions ({len(delayed_clusters)} of {len(clusters)} "
                         f"kickoff clusters) were taken one 5-minute tick after an earlier in-window decision recorded "
                         f"QUOTE_TOO_STALE while the odds collection was still committing")
        finding = "; ".join(text_) + (". Those decisions used the quotes that collection delivered; the race "
                                     "changed when the positions were taken." if text_ else "")
        for c, owner in clock:
            finding += (f" {'In a further' if text_ else 'In one'} position ({owner}) the entry quote's available_as_of is after the decision's "
                        f"recommended_at: {c.get('statement') or c['detail']}")
        out.append({"key": "ORDERING", "title": "Odds collection and the decision checkpoint still race each other",
                    "finding": finding.strip(),
                    "next_test": "Review the decision-repairs candidate (post-collection re-check, default off) and add an "
                                 "alert-only invariant on the recorded clocks (entry quote receipt ≤ decision read instant), and "
                                 "record the actual quote-read instant on the decision instead of the tick start. Replay "
                                 "Sunday's four clusters against it before enabling anything.",
                    "spoken": "We're fixing the timing: each bet should record the moment it actually read its price, "
                              "and the checkpoint should wait for fresh prices instead of skipping a turn.",
                    "experiment": EXPERIMENT_LINKS["REPAIRS"]})
    tot = payload["model_vs_market"]["week_forecast_accuracy"] or {}
    total = tot.get("total") or {}
    margin = tot.get("margin") or {}
    cov = (tot.get("interval_coverage") or {})
    if total:
        m50 = (cov.get("margin") or {}).get("50") or {}
        t50 = (cov.get("total") or {}).get("50") or {}
        text_ = (f"Across the week's {total['n']} scored NFL forecasts the total was off by {total['bias']:+.1f} points "
                 f"on average (predicted minus actual; {total['underestimates']} under, {total['overestimates']} over; "
                 f"MAE {total['mae']:.1f})")
        if margin:
            text_ += f"; margin MAE {margin['mae']:.1f}"
        if m50 and t50:
            text_ += (f". The 50% intervals held {m50['contained']}/{m50['n']} margins and {t50['contained']}/{t50['n']} "
                      f"totals")
        text_ += "."
        out.append({"key": "SCORES", "title": "Score distributions: check spread, not just the winner",
                    "finding": text_,
                    "next_test": "Add this week to the season interval-coverage and total-bias audit (Overview → forecast "
                                 "accuracy) and read it only when the interval excludes zero; interval recalibration stays "
                                 "a separate proposal.",
                    "spoken": "This week goes into the season-long check on whether our predicted score ranges are too "
                              "narrow or too wide.",
                    "experiment": "Interval recalibration (proposal, not active)"})
    if len(out) < 3:
        out.append({"key": "CLOSE", "title": "No closing line was captured",
                    "finding": "None of the week's positions has a genuine close, so closing-line value cannot be "
                               "measured; later quotes are last observed quotes only.",
                    "next_test": "The near-kickoff capture design (one T-6 moneyline observation per cluster) would make "
                                 "CLV measurable without touching the T-26 workflow.",
                    "spoken": "We want one more price captured right before kickoff, so we can finally tell whether we "
                              "beat the closing line.",
                    "experiment": EXPERIMENT_LINKS["CAPTURE"]})
    return out[:3]


UNCERTAINTY_LABEL = ("Conditional on the model's own probabilities and on independence between wagers; this does not "
                     "validate calibration.")
FORECAST_INPUTS_STATEMENT = (
    "The decision-bound M1 FINAL forecast retains no QB, injury or availability field: it is the LONG_HISTORY_CORE "
    "feature tier (197 features, readiness 100, no missing features). The QB rows below come from a separate "
    "research-shadow capture (admission not granted, funding-ineligible) and were NOT inputs to this forecast."
)


def _strict_max(pool: list[dict[str, Any]], chosen: dict[str, Any], score) -> bool:
    """True only when ``chosen`` scores strictly above every other member of ``pool`` (no ties)."""

    return all(score(chosen) > score(other) for other in pool if other["position_id"] != chosen["position_id"])


DAY_LABELS = {0: "Monday night", 1: "Tuesday", 2: "Wednesday", 3: "Thursday night", 4: "Friday", 5: "Saturday",
              6: "Sunday"}


def slate_groups(slate: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Positions grouped by the ET calendar day of kickoff (Sunday slate, Monday night, ...), in kickoff order.

    The week scorecard counts every group; each group lists its own positions, settled and open, so a Monday-night
    position never enters the Sunday slate and an open one is never shown as a result.
    """

    groups: dict[str, dict[str, Any]] = {}
    for s in sorted(slate, key=lambda x: (x["kickoff_utc"] or "", x["matchup"] or "")):
        ko = _utc(s["kickoff_utc"])
        day = ko.astimezone(ET).date() if ko else None
        key = day.isoformat() if day else "unknown"
        g = groups.setdefault(key, {
            "key": key, "et_date": key,
            "label": f"{DAY_LABELS[day.weekday()]} {day.strftime('%a %b %d').replace(' 0', ' ')}" if day else "Unknown",
            "day": DAY_LABELS[day.weekday()] if day else "Unknown", "position_ids": [], "settled": 0, "open": 0})
        g["position_ids"].append(s["position_id"])
        g["settled" if s["settled"] else "open"] += 1
    for g in groups.values():
        g["n"] = len(g["position_ids"])
    return list(groups.values())


def representative_games(slate: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Three settled positions that teach something, chosen by rule (never by hindsight on the story): the
    longest-priced win, the loss with the largest model-market disagreement, and the loss with the highest recorded
    pWin for the SELECTED side. A position already chosen is skipped for the next rule.

    Every label is verified against the whole slate: a superlative ("the highest-...") is used only when the pick is
    strictly first in the population the label names; otherwise the label is the plain "a ..." form. The label never
    claims more than that population (a losing-selection label never says "surest pick").
    """

    settled = [s for s in slate if s["settled"] and s["final"]]
    wins = [s for s in settled if s["result"] == "WIN"]
    losses = [s for s in settled if s["result"] == "LOSS"]
    gap_losses = [s for s in losses if s["model_minus_market_pts"] is not None]
    rules = (
        ("LONGSHOT_WIN", wins, lambda s: s["entry_decimal"] or 0,
         "the longest-priced winning selection", "a long-priced winning selection", "winning selections"),
        ("DISAGREEMENT_LOSS", gap_losses, lambda s: abs(s["model_minus_market_pts"]),
         "the losing selection with the largest model–market disagreement",
         "a losing selection with a large model–market disagreement", "losing selections"),
        ("CONFIDENT_LOSS", losses, lambda s: s["p_win"] or 0,
         "the highest-confidence losing selection", "a high-confidence losing selection", "losing selections"),
    )
    picks: list[dict[str, Any]] = []
    taken: set[str] = set()
    for key, population, score, superlative, plain, scope in rules:
        pool = [s for s in population if s["position_id"] not in taken]
        if not pool:
            continue
        best = max(pool, key=score)
        taken.add(best["position_id"])
        verified = _strict_max(population, best, score)
        picks.append({"key": key, "reason": superlative if verified else plain, "position_id": best["position_id"],
                      "superlative_verified": verified, "superlative_scope": scope,
                      "rank_basis": {"LONGSHOT_WIN": "entry decimal odds", "DISAGREEMENT_LOSS": "|model − market| pts",
                                     "CONFIDENT_LOSS": "decision-bound pWin of the selected side"}[key]})
    return picks


def deep_dive(pick: dict[str, Any], s: dict[str, Any], story: dict[str, Any], forecast: dict[str, Any] | None,
              evidence: dict[str, Any] | None, evidence_status: dict[str, Any] | None = None) -> dict[str, Any]:
    """One representative game: BEFORE KICKOFF (retained), OBSERVED (retained, final), HYPOTHESIS (to investigate)."""

    fc = forecast or {}
    event = fc.get("event_id")
    pbp_src = (evidence or {}).get("play_by_play") or {}
    pbp = ((evidence or {}).get("games") or {}).get(event or "")
    qb = ((evidence or {}).get("qb_inputs") or {}).get(event or "")
    post = story["postgame"]
    final = s["final"] or {}
    pbp_ok = bool(pbp and pbp.get("retained") and pbp.get("ends_with_end_game")
                  and pbp.get("final_as_recorded") == {"away": final.get("away"), "home": final.get("home")})
    observed: dict[str, Any] = {
        "label": "OBSERVED",
        "final": final, "margin_home": post.get("margin_home"), "expected_margin_home": post.get("expected_margin_home"),
        "total": post.get("total"), "expected_total": post.get("expected_total"),
        "margin_percentile": post.get("margin_percentile"), "total_percentile": post.get("total_percentile"),
        "play_by_play_status": ("FINAL_PLAY_BY_PLAY_RETAINED" if pbp_ok else
                                "PLAY_BY_PLAY_NOT_FINAL_OR_MISMATCHED" if pbp else
                                "GAME_EVIDENCE_UNAVAILABLE" if evidence is None else "NO_PLAY_BY_PLAY_RETAINED"),
        "evidence_unavailable_reason": None if evidence is not None else
        f"Game evidence unavailable: {(evidence_status or {}).get('reason') or 'no evidence file supplied'}",
    }
    if pbp_ok:
        passers = pbp.get("passers") or []
        observed |= {
            "source": {"path": pbp_src.get("payload_path"), "sha256": pbp_src.get("payload_sha256"),
                       "sha256_matches_vintage": pbp_src.get("sha256_matches_vintage"),
                       "source_url_as_recorded": pbp_src.get("source_url_as_recorded"),
                       "retrieved_at_utc": pbp_src.get("source_retrieved_at_utc"),
                       "archive_observed_at_utc": pbp_src.get("archive_observed_at_utc"),
                       "rows_for_game": pbp.get("rows"), "rows_total": pbp_src.get("row_count_total"),
                       "final_check": "last row is END GAME and its score equals the settled final score"},
            "scoring_sequence": pbp.get("scoring_sequence"), "turnovers": pbp.get("turnovers"),
            "turnover_counts": pbp.get("turnover_counts"), "passers": passers,
        }
    qb_rows = []
    for side in (qb or {}).get("sides") or []:
        threw = [p for p in (pbp or {}).get("passers") or [] if p["team"] == side.get("team")]
        proxy = side.get("depth_rank_one_gsis_id")
        proxy_attempts = next((p["pass_attempts"] for p in threw if p["gsis_id"] == proxy), 0) if pbp_ok else None
        qb_rows.append(side | {"observed_proxy_pass_attempts": proxy_attempts,
                               "observed_passers": [{"gsis_id": p["gsis_id"], "name_as_recorded": p["name_as_recorded"],
                                                     "pass_attempts": p["pass_attempts"]} for p in threw] if pbp_ok else None})
    before = {
        "label": "BEFORE KICKOFF (retained evidence only)",
        "p_home": fc.get("p_home"), "p_away": fc.get("p_away"), "p_tie": fc.get("p_tie"),
        "expected_home": fc.get("expected_home"), "expected_away": fc.get("expected_away"),
        "expected_margin_home": post.get("expected_margin_home"), "expected_total": post.get("expected_total"),
        "selected": s["selected_team"], "p_win": s["p_win"], "p_win_two_way": s["p_win_two_way"],
        "entry_american": s["entry_american"], "book": s["sportsbook"], "no_vig_selected": s["no_vig_selected"],
        "model_minus_market_pts": s["model_minus_market_pts"], "recorded_ev": s["recorded_ev"],
        "forecast_inputs": FORECAST_INPUTS_STATEMENT,
        "qb_capture": None if not qb else {k: qb.get(k) for k in ("path", "sha256", "schema", "forecast_cutoff_utc",
                                                                   "admission_granted", "evidence_class",
                                                                   "funding_eligible")},
        "qb_sides": qb_rows,
    }
    hyp: list[dict[str, str]] = []
    for row in qb_rows:
        if row.get("observed_proxy_pass_attempts") == 0 and row.get("observed_passers"):
            top = max(row["observed_passers"], key=lambda p: p["pass_attempts"])
            hyp.append({"topic": "QB input",
                        "text": f"An unadmitted research-candidate discrepancy and an explicit-input gap: the research QB "
                                f"capture's depth-rank candidate for {row['team']} ({row['depth_rank_one_gsis_id']}; not "
                                f"admitted, not an M1 input) did not attempt a pass, while {top['name_as_recorded']} "
                                f"({top['gsis_id']}) threw {top['pass_attempts']} of {row['team']}'s passes; the production "
                                f"M1 forecast has no explicit QB input. This is NOT evidence that production selected the "
                                f"wrong starter: production selects no starter. Testable: whether an admitted "
                                f"confirmed-starter input changes forecast quality.",
                        "link": "QB-change prospective shadow bundles (Model Studio nfl-qb-t60-admission-preparation); "
                                "M1 QB-usage residual challenger was rejected (nfl-m1-qb-context-residual-v1)"})
        elif row.get("change_signal") == "DEPTH_PROXY_DIFFERS_FROM_PRIOR_DOMINANT_QB":
            hyp.append({"topic": "QB input",
                        "text": f"An explicit-input gap: for {row['team']} the research capture's depth-rank candidate "
                                f"({row['depth_rank_one_gsis_id']}, {row.get('depth_proxy_prior_pass_attempts')} prior pass "
                                f"attempts; not admitted, not an M1 input) differed from the prior week's dominant QB "
                                f"({row.get('prior_dominant_gsis_id')}); the production M1 forecast has no explicit QB input "
                                f"that could reflect a change. Testable in the QB-change prospective study.",
                        "link": "QB-change prospective shadow bundles (research, not admitted)"})
    if s["model_minus_market_pts"] is not None and abs(s["model_minus_market_pts"]) >= DISAGREEMENT_PTS:
        hyp.append({"topic": "Model–market gap",
                    "text": f"The model sat {s['model_minus_market_pts']:+.1f} pts from the entry book's no-vig price. "
                            f"Testable assumption: that large model–market gaps carry information the market lacks "
                            f"(the R2 question); one game cannot answer it.",
                    "link": EXPERIMENT_LINKS["R2"]})
    mp = post.get("margin_percentile")
    if mp is not None:
        hyp.append({"topic": "Margin distribution width",
                    "text": f"The final home margin fell at the {mp * 100:.0f}th percentile of the forecast's margin "
                            f"distribution (total at the {post['total_percentile'] * 100:.0f}th). Testable assumption: that "
                            f"the stored margin and total distributions have the right width, judged on the season's "
                            f"interval coverage, not on one game.",
                    "link": "Interval recalibration (proposal, not active); Overview forecast accuracy"})
    if s["p_tie"] is not None:
        hyp.append({"topic": "Tie mass",
                    "text": f"Testable assumption: the forecast's {s['p_tie'] * 100:.1f}% tie mass (refunded under the "
                            f"push rule) is constructed correctly; the tie-construction check found no defect.",
                    "link": EXPERIMENT_LINKS["NFL_TIE"]})
    return {"key": pick["key"], "reason": pick["reason"], "superlative_verified": pick.get("superlative_verified"),
            "superlative_scope": pick.get("superlative_scope"), "rank_basis": pick.get("rank_basis"),
            "position_id": s["position_id"], "matchup": s["matchup"],
            "event_id": event, "selected_team": s["selected_team"], "result": s["result"], "net_profit": s["net_profit"],
            "before_kickoff": before, "observed": observed, "hypotheses": hyp}


def explanations(slate: list[dict[str, Any]], week_mc: dict[str, Any], games: list[dict[str, Any]],
                 no_position: list[dict[str, Any]], week_evals: list[dict[str, Any]],
                 stories: list[dict[str, Any]]) -> dict[str, str]:
    """Why the populations differ in size: named games and the rule, never an unexplained n."""

    pair_ids = {g["position_id"] for g in week_mc["games"] if g["pair_log_loss"] is not None}
    med_ids = {g["position_id"] for g in week_mc["games"] if g["median_log_loss"] is not None}
    missing = [g for g in week_mc["games"] if g["position_id"] in pair_ids - med_ids]
    reasons = []
    for g in missing:
        story = next((x for x in stories if x["position_id"] == g["position_id"]), None)
        late = story and any(c["key"] == "quote_available_before_decision" and c["status"] == "FLAG"
                             for c in story["checks"])
        reasons.append(f"{g['matchup']}: " + (
            "no quote was both observed and available at or before the recorded decision time (its own entry quote's "
            "receipt is stamped after the tick-start decision time, see the Sunday-night clock review), so the Overview's "
            "strict median-of-books reference is empty" if late else
            "no same-book pair within 240 min was available at the decision"))
    pair_n, med_n = len(pair_ids), len(med_ids)
    eval_ids = {g["game_id"] for g in week_evals}
    positioned = {s["game_id"] for s in slate}
    extra = [n for n in no_position if n["game_id"] in eval_ids]
    return {
        "pair_vs_median": (f"The entry-book comparison uses the decision's own recorded two-way pair and covers {pair_n} "
                           f"decided games; the median-of-books comparison covers {med_n}. "
                           + ("; ".join(reasons) + "." if reasons else "")),
        "evaluations_vs_wagers": (f"Forecast diagnostics score {len(eval_ids)} games; the wager scorecard counts "
                                  f"{len(positioned)} positions. Rule: forecast accuracy scores every week game with a "
                                  f"scored forecast (one evaluation per game, latest snapshot before kickoff), whether or "
                                  f"not it was bet; the scorecard counts official funded positions only. "
                                  + (" ".join(f"Extra game: {n['matchup']} ({n['note']})" for n in extra))),
    }


def verdict(block: dict[str, Any] | None) -> str:
    """Plain wording for a paired model-minus-market delta and its interval."""

    if not block:
        return "Not computable."
    lo, hi = (block.get("delta_log_loss_ci95") or (None, None))
    worse = block["delta_log_loss"] > 0
    if lo is not None and lo <= 0 <= hi:
        return (f"The model scored {'worse' if worse else 'better'} in this sample, but the uncertainty does not "
                f"establish a difference.")
    return f"The model scored {'worse' if worse else 'better'}, and the 95% interval excludes zero."


def generator_version() -> str | None:
    """The git commit of the running generator (same answer for the CLI and the endpoint)."""

    import os
    import subprocess
    from pathlib import Path

    if os.environ.get("AISP_GENERATOR_COMMIT"):
        return os.environ["AISP_GENERATOR_COMMIT"]
    try:
        out = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "status", "--porcelain",
                                "--untracked-files=no", "--", "."], capture_output=True, text=True,
                               timeout=10).stdout.strip()
        return f"{out}{'+dirty' if dirty else ''}" if out else None
    except Exception:  # noqa: BLE001
        return None


EVIDENCE_SCHEMA = "AISP_MMQB_GAME_EVIDENCE_V1"
EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "data" / "mmqb"


def evidence_content_sha256(doc: dict[str, Any]) -> str:
    """Canonical-JSON sha256 of an evidence document without its own ``content_sha256`` (same rule as the extractor)."""

    body = {k: v for k, v in doc.items() if k != "content_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def _evidence_status(status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, **extra}


def resolve_game_evidence(*, league: str, season: int | None, week: int | None, override: str | None = None,
                          evidence_dir: Path | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The retained game evidence for one week, verified, and its status: VERIFIED, MISSING or INVALID.

    Default: the vendored artifact registered in ``app/data/mmqb/manifest.json`` for (league, season, week); its file
    sha256 must equal the manifest's and its content sha256 must recompute. ``override`` (or ``MMQB_GAME_EVIDENCE``)
    points at another extractor output, which must pass the same schema and content checks. Never raises.
    """

    import os

    override = override or os.environ.get("MMQB_GAME_EVIDENCE")
    folder = evidence_dir or EVIDENCE_DIR
    expected_file_sha = None
    if override:
        path, source = Path(override), "OVERRIDE"
    else:
        manifest_path = folder / "manifest.json"
        if season is None or week is None:
            return None, _evidence_status("MISSING", "no season/week (date-range mode) to look up vendored evidence")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, _evidence_status("MISSING", f"evidence manifest not readable ({manifest_path.name})")
        entry = (manifest.get("artifacts") or {}).get(f"{league}|{season}|{week}")
        if not entry:
            return None, _evidence_status("MISSING", f"no vendored game evidence for {league} {season} week {week}")
        path, source, expected_file_sha = folder / entry["file"], "VENDORED", entry.get("file_sha256")
    if not path.is_file():
        return None, _evidence_status("MISSING", f"evidence file not found ({path.name})", source=source)
    raw = path.read_bytes()
    file_sha = hashlib.sha256(raw).hexdigest()
    info = {"source": source, "file": path.name, "file_sha256": file_sha}
    if expected_file_sha and file_sha != expected_file_sha:
        return None, _evidence_status("INVALID", "file sha256 does not match the manifest", **info)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None, _evidence_status("INVALID", "evidence file is not valid JSON", **info)
    if doc.get("schema") != EVIDENCE_SCHEMA:
        return None, _evidence_status("INVALID", f"schema is not {EVIDENCE_SCHEMA}", **info)
    if not doc.get("content_sha256") or evidence_content_sha256(doc) != doc["content_sha256"]:
        return None, _evidence_status("INVALID", "content sha256 does not recompute (edited or truncated)", **info)
    if (doc.get("league"), doc.get("season"), doc.get("week")) != (league, season, week):
        return None, _evidence_status("INVALID", f"evidence is for {doc.get('league')} {doc.get('season')} week "
                                                 f"{doc.get('week')}", **info)
    pbp = doc.get("play_by_play") or {}
    return doc, _evidence_status("VERIFIED", "file and content sha256 verified", **info,
                                 content_sha256=doc["content_sha256"],
                                 source_vintage_sha256=pbp.get("payload_sha256"),
                                 source_retrieved_at_utc=pbp.get("source_retrieved_at_utc"),
                                 source_row_count=pbp.get("row_count_total"))


def assemble(inputs: Inputs) -> dict[str, Any]:
    """The whole report payload from loaded inputs. Pure: no database access."""

    league = inputs.league
    game_ids = {g["game_id"] for g in inputs.games}
    games_by_id = {g["game_id"]: g for g in inputs.games}
    loaded = inputs.loaded
    decisions_all = [d for d in (loaded.decisions if loaded else []) if d["sport"] == league]
    evaluations_all = [g for g in (loaded.games if loaded else []) if g["sport"] == league]
    decisions_by_id = {d["decision_id"]: d for d in decisions_all}
    week_rows = sorted((r for r in inputs.wager_rows if r["game_id"] in game_ids),
                       key=lambda r: (r["starts_at"] or "", r["matchup"] or ""))
    wmap = {r["position_id"]: r for r in week_rows}
    slate = []
    for r in week_rows:
        detail = inputs.decision_detail.get(r.get("decision_id") or "") or {}
        fc_id = detail.get("shadow_forecast_id") or (detail.get("evidence") or {}).get("shadow_forecast_row_id")
        slate.append(slate_row(r, detail=detail, forecast=inputs.forecasts.get(str(fc_id)) if fc_id else None,
                               decision=decisions_by_id.get(r.get("decision_id") or ""),
                               game=games_by_id.get(r["game_id"]), later=inputs.later_quotes.get(r["position_id"]),
                               settlement=inputs.settlements.get(r["position_id"])))
    settled = [s for s in slate if s["settled"]]
    open_ = [s for s in slate if not s["settled"]]
    positioned_games = {s["game_id"] for s in slate}
    no_position = []
    for g in inputs.games:
        if g["game_id"] in positioned_games:
            continue
        hist = inputs.decision_history.get(g["game_id"], [])
        gov = next((d for d in decisions_all if d["game_id"] == g["game_id"]), None)
        state = ("FINAL" if g["status"] == "FINAL" else "SCHEDULED")
        no_position.append({
            "game_id": g["game_id"], "matchup": f"{g['away']} @ {g['home']}", "kickoff_utc": g["kickoff_utc"],
            "kickoff_et": et_label(g["kickoff_utc"]), "status": g["status"],
            "final": {"home": g["home_score"], "away": g["away_score"]} if state == "FINAL" else None,
            "decisions": len(hist),
            "governing_recommendation": gov["recommendation"] if gov else None,
            "governing_reason": hist[-1]["qualification_reason"] if hist else None,
            "note": (f"No position: governing decision {gov['recommendation']} "
                     f"({hist[-1]['qualification_reason'] if hist else '—'})." if gov else
                     ("Scheduled; no FINAL_PREGAME decision recorded yet (the decision window opens 30 min before "
                      "kickoff)." if state != "FINAL" else "Final; no decision recorded.")),
        })

    summary = wr.summarize(week_rows)
    settled_w = [wmap[s["position_id"]] for s in settled]
    all_w = [wmap[s["position_id"]] for s in slate]
    exp_settled = expectation(settled_w)
    exp_all = expectation(all_w, draws=0)
    exp_open = expectation([wmap[s["position_id"]] for s in open_], draws=0)
    bands_p = wr._bands(week_rows, "probability_band", wr.PROBABILITY_BANDS)
    bands_r = wr._bands(week_rows, "return_band", wr.RETURN_BANDS)

    def groups_of(fn, labels):
        out = []
        for key, label in labels:
            members = [s for s in slate if fn(s) == key]
            out.append(group_block(label, key, members, wmap))
        return out

    fav = groups_of(lambda s: s["price_group"] or "UNKNOWN",
                    [("FAVOURITE", "Favourite (market no-vig ≥ 50%)"), ("UNDERDOG", "Underdog (market no-vig < 50%)"),
                     ("UNKNOWN", "No recorded pair")])
    dis = groups_of(lambda s: "UNKNOWN" if s["large_disagreement"] is None else ("LARGE" if s["large_disagreement"] else "SMALL"),
                    [("LARGE", f"|model − market| ≥ {DISAGREEMENT_PTS:.0f} pts"),
                     ("SMALL", f"|model − market| < {DISAGREEMENT_PTS:.0f} pts"), ("UNKNOWN", "Not computable")])
    model_fav = groups_of(lambda s: "UNKNOWN" if s["selected_is_model_favourite"] is None else (
        "MODEL_FAVOURITE" if s["selected_is_model_favourite"] else "MODEL_UNDERDOG"),
        [("MODEL_FAVOURITE", "Selected = model's predicted winner"),
         ("MODEL_UNDERDOG", "Selected = model's predicted loser (price value)"), ("UNKNOWN", "Unknown")])
    week_mc = market_comparison(slate, decisions_by_id)

    end = _utc(inputs.window["end_utc"])
    start = _utc(inputs.window["start_utc"])
    week_decisions = [d for d in decisions_all if d["game_id"] in game_ids]
    season_decisions = [d for d in decisions_all if d["kickoff_utc"] and _utc(d["kickoff_utc"]) < end]
    prior_decisions = [d for d in season_decisions if _utc(d["kickoff_utc"]) < start]
    week_evals = [g for g in evaluations_all if g["game_id"] in game_ids]
    season_evals = [g for g in evaluations_all if g["kickoff_utc"] and _utc(g["kickoff_utc"]) < end]
    prior_rows = [r for r in inputs.wager_rows if r["starts_at"] and _utc(r["starts_at"]) < start]
    season_rows = [r for r in inputs.wager_rows if r["starts_at"] and _utc(r["starts_at"]) < end]

    def dm(rows):
        if not rows:
            return None
        m = mpo.decision_metrics(rows)
        return {k: m[k] for k in ("governing_decisions", "final", "with_market", "market_decision_time",
                                  "market_comparable", "excluded_not_available_before_decision",
                                  "excluded_no_clock_or_forecast")} | {"funded_selection": m["selection"]["funded"]}

    def gm(rows):
        if not rows:
            return None
        m = mpo.group_metrics(rows)
        return {k: m[k] for k in ("n", "decided_two_way", "ties", "favourite_n", "favourite_correct",
                                  "favourite_accuracy", "log_loss_two_way", "log_loss_two_way_ci95", "brier_two_way",
                                  "margin", "total", "interval_coverage", "brier_check")}

    stories = []
    for s in slate:
        detail = inputs.decision_detail.get(s.get("decision_id") or "") or {}
        stories.append(game_story(s, forecast=inputs.forecasts.get(s["forecast_row_id"] or ""),
                                  decision=decisions_by_id.get(s.get("decision_id") or ""), detail=detail,
                                  history=inputs.decision_history.get(s["game_id"], []),
                                  game=games_by_id.get(s["game_id"]), settlement=inputs.settlements.get(s["position_id"])))

    slate_by_id = {s["position_id"]: s for s in slate}
    story_by_id = {g["position_id"]: g for g in stories}
    dives = [deep_dive(pick, slate_by_id[pick["position_id"]], story_by_id[pick["position_id"]],
                       inputs.forecasts.get(slate_by_id[pick["position_id"]]["forecast_row_id"] or ""),
                       inputs.game_evidence, inputs.game_evidence_status) for pick in representative_games(slate)]
    explain = explanations(slate, week_mc, inputs.games, no_position, week_evals, stories)
    settled_at = [s["settled_at_utc"] for s in slate if s["settled_at_utc"]]
    finals_seen = [g["final_observation"]["retrieved_at_utc"] for g in inputs.games if g.get("final_observation")]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "title": f"Monday Morning QB: {inputs.window['short_label']}",
        "league": league,
        "window": inputs.window,
        "data_cutoff": {
            "db_clock_utc": inputs.snapshot.get("db_clock_utc"),
            "statement": (f"Data cutoff {zulu(inputs.snapshot.get('db_clock_utc'))}: every row committed before this "
                          f"read-only snapshot, and nothing after it."),
            "latest_settlement_utc": max(settled_at) if settled_at else None,
            "latest_final_observation_utc": max(finals_seen) if finals_seen else None,
            "overview_evidence_as_of_utc": _iso(loaded.as_of) if loaded else None,
        },
        "snapshot": inputs.snapshot | {"generator_commit": inputs.generator_commit,
                                       "generated_at_utc": _iso(inputs.now or datetime.now(UTC))},
        "population": {
            "label": "Official funded positions (Wager Results population)",
            "note": "One row per official funded position in a FUNDED_ERA experiment, joined to its own funding decision "
                    "(shadow_decision_id). Same rows and settlement as the Wager Results tab.",
            "experiments": sorted({(s["experiment_id"], s["experiment_name"]) for s in slate}),
        },
        "notes": list(READ_NOTES),
        "slate": slate,
        "settled": [s["position_id"] for s in settled],
        "slate_groups": slate_groups(slate),
        "open": [s["position_id"] for s in open_],
        "games_without_position": no_position,
        "financial": {
            "summary": {k: summary[k] for k in ("n", "settled", "wins", "losses", "pushes", "voids", "settled_stake",
                                                "net_profit", "roi", "roi_ci95", "open_count", "open_exposure",
                                                "open_estimated_profit", "expected_wins", "actual_wins_with_probability",
                                                "comparison_n", "comparison_win_rate")},
            "expectation_settled": exp_settled,
            "expectation_all_positions": exp_all,
            "expectation_open": exp_open,
            "bands": {"probability": bands_p, "return": bands_r},
            "groups": {"price": fav, "disagreement": dis, "model_pick": model_fav},
        },
        "model_vs_market": {
            "week": week_mc | {"verdict_pair": verdict(week_mc["vs_decision_pair"]),
                               "verdict_median": verdict(week_mc["vs_median_of_books"])},
            "explanations": explain,
            "week_overview": dm(week_decisions),
            "season_overview": dm(season_decisions),
            "prior_weeks_overview": dm(prior_decisions),
            "week_forecast_accuracy": gm(week_evals),
            "season_forecast_accuracy": gm(season_evals),
        },
        "cumulative_wagers": {"week": _cum_wagers(week_rows), "prior_weeks": _cum_wagers(prior_rows),
                              "season_to_date": _cum_wagers(season_rows)},
        "games": stories,
        "deep_dives": dives,
        "game_evidence_status": inputs.game_evidence_status or (
            _evidence_status("VERIFIED", "supplied by the caller") if inputs.game_evidence else
            _evidence_status("MISSING", "no evidence file supplied")),
        "game_evidence_sources": None if not inputs.game_evidence else {
            "play_by_play": inputs.game_evidence.get("play_by_play"), "searched": inputs.game_evidence.get("searched"),
            "extracted_at_utc": inputs.game_evidence.get("extracted_at_utc")},
        "uncertainty_label": UNCERTAINTY_LABEL,
        "closing_line_rule": GENUINE_CLOSE_RULE,
        "play_by_play": NO_PLAY_BY_PLAY,
        "caveat": ONE_WEEK_CAVEAT,
        "experiments": EXPERIMENT_LINKS,
        "research_links": RESEARCH_LINKS,
        "authority": {"creates_positions": False, "changes_policy": False, "writes": False,
                      "statement": "Read-only report. It changes no record, policy, threshold, stake or model."},
    }
    payload["findings"] = findings(payload)
    payload["snapshot"]["content_sha256"] = content_hash(payload)
    return payload


def content_hash(payload: dict[str, Any]) -> str:
    """sha256 of the report DATA: excludes the snapshot block and the database clock, so an unchanged database gives the same hash on every run."""

    body = {k: v for k, v in payload.items() if k not in ("snapshot", "data_cutoff")}
    body["data_cutoff"] = {k: v for k, v in payload["data_cutoff"].items() if k not in ("db_clock_utc", "statement")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def mmqb_report(session: Session, *, league: str = "NFL", season: int | None = None, week: int | None = None,
                date_from: str | None = None, date_to: str | None = None, now: datetime | None = None,
                generator_commit: str | None = None, game_evidence_path: str | None = None) -> dict[str, Any]:
    inputs = load_inputs(session, league=league, season=season, week=week, date_from=date_from, date_to=date_to,
                         now=now, generator_commit=generator_commit or generator_version())
    inputs.game_evidence, inputs.game_evidence_status = resolve_game_evidence(
        league=inputs.league, season=inputs.window.get("season"), week=inputs.window.get("week"),
        override=game_evidence_path)
    return assemble(inputs)


__all__ = ["SCHEMA_VERSION", "mmqb_report", "load_inputs", "assemble", "expectation", "slate_row", "game_story",
           "record_checks", "market_comparison", "findings", "last_observed_quote", "no_vig_pair", "nfl_ev",
           "mid_percentile", "weeks_with_positions", "latest_week", "zulu", "Inputs"]
