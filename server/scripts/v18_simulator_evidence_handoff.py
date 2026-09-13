"""Extend the existing V18 evidence export with what the next simulator needs.

Reuses ``AISP_V18_ERROR_ANALYSIS_EVIDENCE_V2`` and its directory. This adds
three things and creates no second framework:

1.  **Pitcher context inventory and export.** What AISP actually holds for
    pitcher usage and transitions, in two strictly separated scopes:

    *   *Current-data pregame context* (2026 season): starter and lineup
        observations with their own ``observed_at`` / ``available_as_of``
        clocks, joined only where they precede a forecast's cutoff.
    *   *Development history* (2025 season): per-appearance pitcher rows --
        batters faced, outs, pitches, innings -- from
        ``player_game_history_observations``.

    The second is exported as LABELS for authorized historical evaluation, and
    is marked so. A history row whose ``evidence_end_at`` falls after a
    forecast's cutoff cannot be that forecast's pregame input, and this export
    never presents one as such.

2.  **A same-cutoff market comparison.** For every eligible V18 forecast, the
    complete two-sided moneyline from ONE sportsbook, as it stood at that
    forecast's own declared decision cutoff. Margin removed by the repository's
    documented proportional method (``forecast_exchange.pricing.no_vig_two_sided``).
    Book selection is frozen before any performance number is computed.

3.  **A checksummed handoff for the exchange**, with an explicit omissions
    table, so Codex can choose an implementation without waiting on an
    acquisition project.

Read-only. No provider call, no model execution, no policy or threshold change.

Usage:
    python server/scripts/v18_simulator_evidence_handoff.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.forecast_exchange.pricing import (  # noqa: E402
    implied_probability, no_vig_two_sided,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXCHANGE_ROOT = Path(r"C:\Users\james\asp-model-exchange\mlb-v18-simulator-evidence-v1")
STUDY_ID = "AISP_MLB_V18_SIMULATOR_EVIDENCE_HANDOFF_V1"
BASE_SCHEMA = "AISP_V18_ERROR_ANALYSIS_EVIDENCE_V2"

ENGINE_ID = "MLB_V18_WHOLE_GAME"
DATE_FLOOR = "2026-08-28"
SEALED_OOT = ("2026-08-14", "2026-08-27")
QUARANTINED = ("823343", "824235", "822693")
CUTOFF_MINUTES = 15
#: The repository's pregame quote-freshness window: a quote must still be
#: inside it AT the cutoff, not merely when it was observed.
FRESHNESS_MINUTES = 240

#: Frozen BEFORE any performance number is computed, so the choice cannot be
#: made to flatter a result: the book whose two-sided quote is CLOSEST IN TIME
#: to the decision cutoff, ties broken by the lower overround, then by name.
BOOK_SELECTION_RULE = (
    "single book, complete two sided, latest quote at or before the decision "
    "cutoff and still inside the freshness window; ties broken by lower "
    "overround then by sportsbook name"
)

# The forecast population. Quarantine and the sealed window are SQL predicates,
# never post-filters, so a protected game's outcome is not read and discarded.
FORECASTS_SQL = f"""
select sf.id, sf.forecast_id, sf.canonical_game_id, sf.source_event_id,
       sf.scheduled_start_utc, sf.source_cutoff_utc, sf.available_as_of_utc,
       sf.created_at as imported_at, sf.full_home_win_probability,
       sf.full_away_win_probability, sf.full_expected_home_score,
       sf.full_expected_away_score, sf.distributions, sf.uncertainty,
       sf.simulator_seed, sf.home_team_id, sf.away_team_id, sf.is_ready
from shadow_forecasts sf
join shadow_forecast_bundles b on b.id = sf.bundle_id
where b.sport = 'MLB'
  and sf.engine_id = :engine_id
  and sf.event_date >= date '{DATE_FLOOR}'
  and not (sf.event_date between date '{SEALED_OOT[0]}' and date '{SEALED_OOT[1]}')
  and sf.source_event_id not in {QUARANTINED}
order by sf.scheduled_start_utc
"""

SCORES_SQL = """
select e.shadow_forecast_id, e.home_score, e.away_score, e.actual_outcome,
       e.log_loss, e.brier_score, e.result_identity, e.result_source,
       e.result_tier, e.detail
from research_forecast_evaluations e
where e.league_code = 'MLB' and e.is_current is true
"""

#: Complete two-sided moneylines for one game, with each side's own clocks.
QUOTES_SQL = """
select sb.name as book, mo.outcome_key, mo.label, mo.participant_team_id,
       os.american_odds, os.decimal_odds, os.observed_at, os.available_as_of
from odds_snapshots os
join market_outcomes mo on mo.id = os.market_outcome_id
join markets m on m.id = mo.market_id
join sportsbooks sb on sb.id = os.sportsbook_id
where m.game_id = :game_id
  and m.market_type = 'MONEYLINE'
  and os.american_odds is not null
order by os.observed_at
"""

STARTERS_SQL = """
select so.game_id, so.team_id, so.observed_at, so.available_as_of,
       so.probable_pitcher_id, so.confirmed_pitcher_id, so.confirmation_state,
       so.throws, so.days_rest, so.pitches_previous_day, so.pitches_last_start,
       so.workload, so.source
from starter_observations so
where so.game_id = any(:game_ids)
order by so.observed_at
"""

HISTORY_SQL = """
select h.player_id, h.team_id, h.opponent_team_id, h.game_id, h.role, h.started,
       h.event_at, h.evidence_end_at, h.eligible_at, h.retrieved_at,
       h.availability_basis, h.evidence_classification, h.quality_tier,
       h.revision, h.payload_checksum, h.values
from player_game_history_observations h
where h.role = 'PITCHER'
order by h.event_at, h.player_id
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _rows(conn, sql: str, **params: Any) -> list[dict[str, Any]]:
    result = conn.execute(text(sql), params)
    cols = list(result.keys())
    return [dict(zip(cols, row)) for row in result.fetchall()]


def _pitching(values: dict[str, Any] | None) -> dict[str, Any] | None:
    block = (values or {}).get("pitching")
    if not isinstance(block, dict):
        return None
    return {
        "battersFaced": block.get("battersFaced"),
        "outs": block.get("outs"),
        "numberOfPitches": block.get("numberOfPitches"),
        "inningsPitched": block.get("inningsPitched"),
        "strikeOuts": block.get("strikeOuts"),
        "baseOnBalls": block.get("baseOnBalls"),
        "hits": block.get("hits"),
        "homeRuns": block.get("homeRuns"),
        "earnedRuns": block.get("earnedRuns"),
        "gamesStarted": block.get("gamesStarted"),
    }


# -------------------------------------------------------- market matching --

def _side_of(row: dict[str, Any], home_team: str, away_team: str) -> str | None:
    """HOME or AWAY for one priced outcome.

    Two conventions live in this table: the provider-neutral HOME/AWAY keys, and
    team-named keys from The Odds API. Both are resolved, the second through the
    outcome's participant team, so a team-named market is not silently dropped.
    """

    for token in (row.get("label"), str(row.get("outcome_key") or "").split(":")[0]):
        upper = str(token or "").strip().upper()
        if upper in {"HOME", "H"}:
            return "HOME"
        if upper in {"AWAY", "A"}:
            return "AWAY"
    participant = str(row.get("participant_team_id") or "")
    if participant and participant == home_team:
        return "HOME"
    if participant and participant == away_team:
        return "AWAY"
    return None


def match_quote(
    quotes: list[dict[str, Any]], cutoff: datetime,
    *, home_team: str = "", away_team: str = "",
) -> tuple[dict[str, Any] | None, str | None]:
    """One book's complete two-sided quote as it stood at the cutoff.

    Both sides must have been KNOWN by the cutoff (``available_as_of <= cutoff``)
    and still inside the freshness window at that instant. Sides are never
    combined across books, and a one-sided book is not a price.
    """

    floor = cutoff - timedelta(minutes=FRESHNESS_MINUTES)
    by_book: dict[str, dict[str, dict[str, Any]]] = {}
    for row in quotes:
        known = _aware(row["available_as_of"]) or _aware(row["observed_at"])
        if known is None or known > cutoff or known < floor:
            continue
        side = _side_of(row, home_team, away_team)
        if side is None:
            continue
        book = str(row["book"])
        slot = by_book.setdefault(book, {})
        previous = slot.get(side)
        if previous is None or (_aware(previous["known"]) or floor) <= known:
            slot[side] = {**row, "known": known}

    complete = {
        book: sides for book, sides in by_book.items()
        if {"HOME", "AWAY"} <= set(sides) and all(
            isinstance(sides[key]["american_odds"], int) for key in sides
        )
    }
    if not complete:
        if by_book:
            return None, "NO_COMPLETE_TWO_SIDED_BOOK_AT_CUTOFF"
        return None, "NO_QUOTE_KNOWN_BY_CUTOFF_INSIDE_FRESHNESS_WINDOW"

    scored = []
    for book, sides in complete.items():
        keys = sorted(sides)
        implied = {k: implied_probability(int(sides[k]["american_odds"])) for k in keys}
        overround = sum(implied.values())
        latest = max(_aware(sides[k]["known"]) for k in keys)
        scored.append(((cutoff - latest).total_seconds(), overround, book,
                       book, sides, implied, overround, latest))
    scored.sort(key=lambda row: (row[0], row[1], row[2]))
    _, _, _, book, sides, implied, overround, latest = scored[0]

    home_key, away_key = "HOME", "AWAY"
    return {
        "book": book,
        "home_american": int(sides[home_key]["american_odds"]),
        "away_american": int(sides[away_key]["american_odds"]),
        "home_decimal": sides[home_key]["decimal_odds"],
        "away_decimal": sides[away_key]["decimal_odds"],
        "home_implied": implied[home_key],
        "away_implied": implied[away_key],
        "overround": overround,
        "home_no_vig": no_vig_two_sided(implied[home_key], implied[away_key]),
        "quote_known_utc": _iso(latest),
        "seconds_before_cutoff": int((cutoff - latest).total_seconds()),
        "margin_removal": "proportional two-sided (forecast_exchange.pricing.no_vig_two_sided)",
    }, None


def _latest_per_team(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The last observation per club, by its own availability clock."""

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["team_id"])
        known = _aware(row["available_as_of"]) or _aware(row["observed_at"])
        current = out.get(key)
        if current is None:
            out[key] = row
            continue
        current_known = _aware(current["available_as_of"]) or _aware(current["observed_at"])
        if current_known is None or (known is not None and known >= current_known):
            out[key] = row
    return out


def _log_loss(probability: float, home_won: bool) -> float:
    eps = 1e-15
    p = min(max(probability if home_won else 1.0 - probability, eps), 1 - eps)
    import math
    return -math.log(p)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / "outputs" / "v18-error-analysis-evidence"))
    parser.add_argument("--exchange", default=str(EXCHANGE_ROOT))
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    engine = create_engine(os.environ["DATABASE_URL"])
    as_of = datetime.now(UTC)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    omissions: list[dict[str, Any]] = []

    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        forecasts = _rows(conn, FORECASTS_SQL, engine_id=ENGINE_ID)
        scores = {str(r["shadow_forecast_id"]): r for r in _rows(conn, SCORES_SQL)}
        game_ids = [str(r["canonical_game_id"]) for r in forecasts if r["canonical_game_id"]]
        starters = _rows(conn, STARTERS_SQL, game_ids=game_ids) if game_ids else []
        history = _rows(conn, HISTORY_SQL)

        coverage = _rows(conn, """
            select 'starter_observations' as relation, count(*) as rows,
                   min(observed_at) as first_at, max(observed_at) as last_at
            from starter_observations
            union all select 'lineup_observations', count(*), min(observed_at), max(observed_at)
            from lineup_observations
            union all select 'roster_availability_observations', count(*),
                   min(observed_at), max(observed_at) from roster_availability_observations
            union all select 'game_feature_snapshots', count(*), min(generated_at), max(generated_at)
            from game_feature_snapshots
            union all select 'player_game_history_observations', count(*),
                   min(event_at), max(event_at) from player_game_history_observations
            union all select 'pitching_features', count(*), null, null from pitching_features
            union all select 'player_game_stats', count(*), null, null from player_game_stats
            union all select 'player_talent_estimates', count(*), null, null from player_talent_estimates
            union all select 'pitch_events', count(*), null, null from pitch_events
            union all select 'bullpen_availability_observations', count(*), null, null
            from bullpen_availability_observations
            union all select 'statcast_player_observations', count(*), null, null
            from statcast_player_observations
        """)

        # --- the market comparison ---------------------------------------
        market_rows: list[dict[str, Any]] = []
        for row in forecasts:
            cutoff = _aware(row["scheduled_start_utc"]) - timedelta(minutes=CUTOFF_MINUTES)
            quotes = _rows(conn, QUOTES_SQL, game_id=str(row["canonical_game_id"]))
            quote, reason = match_quote(
                quotes, cutoff,
                home_team=str(row["home_team_id"] or ""),
                away_team=str(row["away_team_id"] or ""),
            )
            score = scores.get(str(row["id"]))
            clock = ((score or {}).get("detail") or {}).get("publication_clock") or {}
            published = _aware(row["available_as_of_utc"])
            imported = _aware(row["imported_at"])
            entry = {
                "mlbGamePk": row["source_event_id"],
                "forecastId": row["forecast_id"],
                "decisionCutoffUtc": _iso(cutoff),
                "modelHomeWinProbability": row["full_home_win_probability"],
                "publicationClock": clock.get("classification"),
                "evidenceQuality": ("VERIFIED_PROSPECTIVE" if clock.get("verified")
                                    else "QUALIFIED_DIAGNOSTIC" if clock
                                    else "NOT_SCORED"),
                # A forecast imported after its own cutoff could not have
                # reached a decision. It is kept, and labelled, not dropped.
                "operationallyEligible": bool(imported and imported <= cutoff),
                "publishedByCutoff": bool(published and published <= cutoff),
                "quote": quote,
                "quoteMissingReason": reason,
            }
            if score and quote:
                home_won = str(score["actual_outcome"]).upper() == "HOME"
                model_ll = _log_loss(float(row["full_home_win_probability"]), home_won)
                market_ll = _log_loss(float(quote["home_no_vig"]), home_won)
                actual = 1.0 if home_won else 0.0
                entry["paired"] = {
                    "actualOutcome": score["actual_outcome"],
                    "modelLogLoss": round(model_ll, 6),
                    "marketLogLoss": round(market_ll, 6),
                    "logLossDifference": round(model_ll - market_ll, 6),
                    "modelBrier": round((float(row["full_home_win_probability"]) - actual) ** 2, 6),
                    "marketBrier": round((float(quote["home_no_vig"]) - actual) ** 2, 6),
                    "brierDifference": round(
                        (float(row["full_home_win_probability"]) - actual) ** 2
                        - (float(quote["home_no_vig"]) - actual) ** 2, 6),
                    "convention": "model minus market; negative favours the model",
                }
            elif not quote:
                omissions.append({
                    "stage": "market", "mlbGamePk": row["source_event_id"],
                    "reason": reason,
                })
            market_rows.append(entry)

    # --- pregame starter context, gated on the forecast's own cutoff ------
    by_game: dict[str, list[dict[str, Any]]] = {}
    for row in starters:
        by_game.setdefault(str(row["game_id"]), []).append(row)

    pregame_rows: list[dict[str, Any]] = []
    for row in forecasts:
        cutoff = _aware(row["scheduled_start_utc"]) - timedelta(minutes=CUTOFF_MINUTES)
        observations = by_game.get(str(row["canonical_game_id"]), [])
        usable = [
            o for o in observations
            if (_aware(o["available_as_of"]) or _aware(o["observed_at"])) is not None
            and (_aware(o["available_as_of"]) or _aware(o["observed_at"])) <= cutoff
        ]
        pregame_rows.append({
            "mlbGamePk": row["source_event_id"],
            "forecastId": row["forecast_id"],
            "decisionCutoffUtc": _iso(cutoff),
            "starterObservationsKnownByCutoff": len(usable),
            "starterObservationsTotal": len(observations),
            "distinctClubsWithStarter": len({str(o["team_id"]) for o in usable}),
            "latestStarterKnownUtc": _iso(max(
                ((_aware(o["available_as_of"]) or _aware(o["observed_at"])) for o in usable),
                default=None)),
            "confirmationStates": dict(Counter(
                str(o["confirmation_state"]) for o in usable)),
            "sources": sorted({str(o["source"]) for o in usable}),
            # The workload fields the next simulator actually needs, taken from
            # the LATEST observation per club that was knowable by the cutoff.
            "startersKnownByCutoff": [
                {
                    "teamId": team,
                    "probablePitcherId": str(latest["probable_pitcher_id"] or "") or None,
                    "confirmedPitcherId": str(latest["confirmed_pitcher_id"] or "") or None,
                    "confirmationState": latest["confirmation_state"],
                    "throws": latest["throws"],
                    "daysRest": latest["days_rest"],
                    "pitchesPreviousDay": latest["pitches_previous_day"],
                    "pitchesLastStart": latest["pitches_last_start"],
                    "workload": latest["workload"],
                    "knownUtc": _iso(_aware(latest["available_as_of"])
                                     or _aware(latest["observed_at"])),
                }
                for team, latest in _latest_per_team(usable).items()
            ],
            "note": "Only observations whose own availability clock precedes this "
                    "forecast's decision cutoff are counted or exported.",
        })

    # --- Development pitcher appearance history, as LABELS ----------------
    history_path = out_dir / "pitcher_appearance_history.jsonl"
    starts = relief = 0
    players: set[str] = set()
    first_at = last_at = None
    with history_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in history:
            pitching = _pitching(row["values"])
            if pitching is None:
                continue
            event_at = _aware(row["event_at"])
            first_at = event_at if first_at is None else min(first_at, event_at)
            last_at = event_at if last_at is None else max(last_at, event_at)
            players.add(str(row["player_id"]))
            if row["started"]:
                starts += 1
            else:
                relief += 1
            handle.write(json.dumps({
                "schemaVersion": "AISP_MLB_PITCHER_APPEARANCE_HISTORY_V1",
                "use": "LABEL_ONLY_AUTHORIZED_HISTORICAL_EVALUATION",
                "notAPregameCovariate": True,
                "playerId": str(row["player_id"]),
                "teamId": str(row["team_id"]),
                "opponentTeamId": str(row["opponent_team_id"]),
                "gameId": str(row["game_id"]),
                "started": row["started"],
                "eventAtUtc": _iso(event_at),
                "evidenceEndAtUtc": _iso(row["evidence_end_at"]),
                "eligibleAtUtc": _iso(row["eligible_at"]),
                "retrievedAtUtc": _iso(row["retrieved_at"]),
                "availabilityBasis": row["availability_basis"],
                "evidenceClassification": row["evidence_classification"],
                "qualityTier": row["quality_tier"],
                "revision": row["revision"],
                "payloadChecksum": row["payload_checksum"],
                "pitching": pitching,
            }, sort_keys=True, default=str) + "\n")

    market_path = out_dir / "market_comparison.json"
    matched = [r for r in market_rows if r["quote"]]
    paired = [r for r in market_rows if r.get("paired")]
    operational = [r for r in paired if r["operationallyEligible"]]
    scientific = [r for r in paired if not r["operationallyEligible"]]

    def _summary(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
        if not rows:
            return {"population": label, "games": 0,
                    "note": "no games in this population"}
        ll = [r["paired"]["logLossDifference"] for r in rows]
        br = [r["paired"]["brierDifference"] for r in rows]
        return {
            "population": label,
            "games": len(rows),
            "meanLogLossDifference": round(sum(ll) / len(ll), 6),
            "modelBetterOnLogLoss": sum(1 for v in ll if v < 0),
            "meanBrierDifference": round(sum(br) / len(br), 6),
            "modelBetterOnBrier": sum(1 for v in br if v < 0),
            "convention": "model minus market; negative favours the model",
            "caveat": "Paired game-level differences on identical games. This is a "
                      "descriptive comparison on a small sample, not a significance "
                      "test and not a returns measure. It carries no funded-era "
                      "authority and was not used to tune any threshold or selection "
                      "rule.",
        }

    market_payload = {
        "schemaVersion": "AISP_MLB_V18_SAME_CUTOFF_MARKET_COMPARISON_V1",
        "asOfUtc": as_of.isoformat(),
        "labels": {
            "whatEligibilityMeansHere": (
                "Timely forecast, import and quote eligibility. This is NOT funded "
                "model-era authority: during this window the funded controlling "
                "engine was Gen 1 (AISP_BASELINE), so no row here was, or could have "
                "been, a funded V18 decision."
            ),
            "populationsOverlap": (
                "operationallyEligible and byEvidenceQuality describe the SAME paired "
                "games along two different axes -- import timing and publication-clock "
                "quality. They overlap and MUST NOT be added. See "
                "coverage.populationOverlap for the intersection."
            ),
            "quotesAreCutoffEligibleNotAtCutoff": (
                f"A quote qualifies if it was knowable by the cutoff and still inside "
                f"the {FRESHNESS_MINUTES}-minute freshness window at that instant. "
                "These are cutoff-ELIGIBLE available quotes, not quotes observed at "
                "the exact cutoff. Every matched game carries "
                "quote.seconds_before_cutoff; read it before treating a price as "
                "contemporaneous."
            ),
        },
        "rules": {
            "decisionCutoff": f"scheduled start minus {CUTOFF_MINUTES} minutes",
            "quoteFreshnessMinutes": FRESHNESS_MINUTES,
            "bookSelection": BOOK_SELECTION_RULE,
            "marginRemoval": "proportional two-sided, "
                             "forecast_exchange.pricing.no_vig_two_sided",
            "frozenBeforePerformanceExamined": True,
            "closingPricesExcluded": "Closing prices are later information and are a "
                                     "separate comparison; none is used here.",
            "sidesNeverCombinedAcrossBooks": True,
        },
        "coverage": {
            "forecastsInPopulation": len(market_rows),
            "withCompleteTwoSidedQuoteAtCutoff": len(matched),
            "withScoredOutcomeAndQuote": len(paired),
            "missingQuoteReasons": dict(Counter(
                r["quoteMissingReason"] for r in market_rows if r["quoteMissingReason"])),
            "quoteAgeSecondsBeforeCutoff": (
                {
                    "min": min(ages), "median": sorted(ages)[len(ages) // 2],
                    "max": max(ages),
                    "note": "Age of the matched quote at the decision cutoff.",
                } if (ages := [r["quote"]["seconds_before_cutoff"]
                               for r in market_rows if r["quote"]]) else None
            ),
            "populationOverlap": {
                "pairedGames": len(paired),
                "operationallyEligible": len(operational),
                "verifiedProspective": len([
                    r for r in paired if r["evidenceQuality"] == "VERIFIED_PROSPECTIVE"]),
                "inBoth": len([
                    r for r in paired
                    if r["operationallyEligible"]
                    and r["evidenceQuality"] == "VERIFIED_PROSPECTIVE"]),
                "warning": "These are overlapping views of one set of paired games. "
                           "Do not sum them.",
            },
        },
        "operationallyEligible": _summary(operational, "OPERATIONALLY_ELIGIBLE"),
        "scientificOnly": _summary(scientific, "SCIENTIFIC_ONLY_LATE_IMPORT"),
        "byEvidenceQuality": {
            quality: _summary([r for r in paired if r["evidenceQuality"] == quality], quality)
            for quality in ("VERIFIED_PROSPECTIVE", "QUALIFIED_DIAGNOSTIC")
        },
        "games": market_rows,
    }
    market_path.write_text(
        json.dumps(market_payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")

    pregame_path = out_dir / "pregame_pitcher_context.json"
    pregame_path.write_text(json.dumps({
        "schemaVersion": "AISP_MLB_V18_PREGAME_PITCHER_CONTEXT_V1",
        "asOfUtc": as_of.isoformat(),
        "gate": "observation availability clock <= that forecast's decision cutoff",
        "games": pregame_rows,
    }, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    inventory = {
        "schemaVersion": "AISP_MLB_V18_EVIDENCE_INVENTORY_V1",
        "asOfUtc": as_of.isoformat(),
        "relations": [
            {"relation": r["relation"], "rows": r["rows"],
             "firstAt": _iso(r["first_at"]), "lastAt": _iso(r["last_at"]),
             "populated": bool(r["rows"])}
            for r in coverage
        ],
        "emptyRelations": [r["relation"] for r in coverage if not r["rows"]],
    }

    files = {
        "pitcher_appearance_history.jsonl": history_path,
        "market_comparison.json": market_path,
        "pregame_pitcher_context.json": pregame_path,
    }
    handoff = {
        "schemaVersion": STUDY_ID,
        "extendsExport": BASE_SCHEMA,
        "extendsManifest": str(out_dir / "manifest.json"),
        "asOfUtc": as_of.isoformat(),
        "producer": "AISP",
        "consumer": "Model Studio (Codex)",
        "protectedPopulations": {
            "quarantinedGamePks": list(QUARANTINED),
            "sealedFinalOutOfTimeWindow": list(SEALED_OOT),
            "appliedAs": "SQL predicates on the population, before any outcome read",
            "colDetDurationPilot": "outcomes and authority remain with the pilot's "
                                   "existing Studio owner; nothing here scores or "
                                   "re-derives them",
            "replayScorerExclusion": "HISTORICAL_REPLAY_RESEARCH_SHADOW rows stay "
                                     "refused by the research scorer",
        },
        "scopes": {
            "currentDataPregame": {
                "season": 2026,
                "use": "PREGAME_COVARIATES",
                "file": "pregame_pitcher_context.json",
                "gate": "each observation's own availability clock must precede the "
                        "forecast's decision cutoff",
                "fields": ["probablePitcherId", "confirmedPitcherId",
                           "confirmationState", "throws", "daysRest",
                           "pitchesPreviousDay", "pitchesLastStart", "workload"],
            },
            "developmentHistory": {
                "season": 2025,
                "use": "LABEL_ONLY_AUTHORIZED_HISTORICAL_EVALUATION",
                "file": "pitcher_appearance_history.jsonl",
                "warning": "Reliever usage and PA outcomes here are LABELS. A history "
                           "row whose evidenceEndAt falls after a forecast's cutoff is "
                           "not that forecast's pregame input and must never be used "
                           "as one.",
                "appearances": starts + relief,
                "starts": starts,
                "reliefAppearances": relief,
                "distinctPitchers": len(players),
                "firstEventAtUtc": _iso(first_at),
                "lastEventAtUtc": _iso(last_at),
                "fields": ["battersFaced", "outs", "numberOfPitches", "inningsPitched",
                           "strikeOuts", "baseOnBalls", "hits", "homeRuns",
                           "earnedRuns", "gamesStarted"],
            },
            "marketComparison": {
                "file": "market_comparison.json",
                "use": "DESCRIPTIVE_SAME_CUTOFF_COMPARISON",
            },
        },
        "inventory": inventory,
        "omissions": {
            "note": "Stated explicitly so an implementation can be chosen without "
                    "waiting for an acquisition project.",
            "emptyRelations": inventory["emptyRelations"],
            "consequences": [
                "pitching_features, player_game_stats, player_talent_estimates and "
                "pitch_events hold no rows: no per-pitch or aggregated talent inputs "
                "exist in AISP at all.",
                "bullpen_availability_observations and statcast_player_observations "
                "hold no rows: reliever workload and batted-ball quality are "
                "unavailable as pregame covariates.",
                "player_game_history_observations covers the 2025 season only; there "
                "is no 2026 per-appearance history, so within-season pitcher usage "
                "for the current slate cannot be derived from AISP.",
            ],
            "marketOmissions": omissions,
        },
        "files": {
            name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
            for name, path in files.items()
        },
        "guarantees": {"databaseWrites": 0, "providerCalls": 0, "modelExecutions": 0,
                       "policyChanges": 0},
    }

    handoff_body = json.dumps(handoff, indent=2, sort_keys=True, default=str).encode()
    handoff_sha = hashlib.sha256(handoff_body).hexdigest()
    local_handoff = out_dir / "simulator_evidence_handoff.json"
    local_handoff.write_bytes(handoff_body)

    exchange = Path(args.exchange)
    target = exchange / "objects" / "sha256" / handoff_sha
    target.mkdir(parents=True, exist_ok=True)
    (target / "simulator-evidence-handoff.json").write_bytes(handoff_body)
    for name, path in files.items():
        digest = handoff["files"][name]["sha256"]
        folder = exchange / "objects" / "sha256" / digest
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(path.read_bytes())
    pointer = {
        "schemaVersion": "AISP_MLB_V18_SIMULATOR_EVIDENCE_EXCHANGE_CURRENT_V1",
        "producer": "AISP",
        "handoff": {
            "relativePath": f"objects/sha256/{handoff_sha}/simulator-evidence-handoff.json",
            "sha256": handoff_sha, "bytes": len(handoff_body),
        },
        "files": {
            name: {
                "relativePath": f"objects/sha256/{handoff['files'][name]['sha256']}/{name}",
                **handoff["files"][name],
            }
            for name in files
        },
        "researchOnly": True, "wageringAuthority": False,
        "executionAllowedInAISP": False,
        "asOfUtc": as_of.isoformat(),
    }
    exchange.mkdir(parents=True, exist_ok=True)
    (exchange / "current.json").write_text(
        json.dumps(pointer, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps({
        "handoffSha256": handoff_sha,
        "exchangePointer": str(exchange / "current.json"),
        "localHandoff": str(local_handoff),
        "pitcherAppearances": starts + relief,
        "distinctPitchers": len(players),
        "marketCoverage": market_payload["coverage"],
        "operationallyEligible": market_payload["operationallyEligible"],
        "scientificOnly": market_payload["scientificOnly"],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
