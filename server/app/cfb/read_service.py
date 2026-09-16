"""Presentation data for the College Football research surfaces.

WHERE THE PERCENTAGES COME FROM
-------------------------------
Nowhere. Probabilities leave this service as the fractions they were stored as,
and the view multiplies by 100 exactly once. A service that returned percentages
and a view that formatted them would eventually do it twice, and 76.92% would
become something nobody could trace.

THE SCENARIO CALCULATOR
-----------------------
Reads the complete total PMF, never the expected total. P(total > line),
P(total == line) and P(total < line) are counts over ten thousand retained
draws; a normal curve around 52.58 would give different, wrong answers and look
just as plausible. An integer line has real push mass (2.38% at 53.0); a
half-point line has exactly zero, which is retained as a number and described in
words rather than hidden.

WHAT THIS NEVER RETURNS
-----------------------
A price, an offered line, an expected betting return, or a recommendation. There
is no recorded College Football market, and a scenario probability beside an
invented price would read as one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CFB_SPORT_CODE, CfbResearchForecast

SCHEMA_VERSION = "AISP_CFB_RESEARCH_VIEW_V1"

#: Said once, here, so every surface says the same thing.
AUTHORITY = {
    "sport": CFB_SPORT_CODE,
    "status": "Research forecast",
    # A virtual paper account and a paper policy exist for College Football
    # (see /sports/readiness); what is missing is a bindable price. Saying "no
    # bankroll" here contradicted the $1,000 Experiment page.
    "bankroll": "Virtual paper account only · no real money",
    "policy": "Paper policy · no assessment without a price",
    "market": "No sportsbook price held · market comparison blocked",
    "research_only": True,
    "note": ("College Football forecasts are research imports. A virtual paper account and "
             "paper policy exist, but AISP holds no College Football sportsbook price and has "
             "no admitted College Football schedule, so no assessment or paper position can "
             "occur. There is no College Football forecast scheduler, and importing this "
             "forecast created no account or position."),
}

ATTRIBUTION = "Data provided by CollegeFootballData.com"

#: The producer's raw provider responses are excluded from the package and must
#: never reach a browser payload. Nothing in this module reads them.
RAW_SOURCE_POLICY = "CFBD raw responses remain private and are not redistributed."

METHOD_LABELS = {
    "joint": "Joint score simulation",
    "ridge": "Ridge margin model",
    "elo": "Elo benchmark",
}

#: Which probability the distribution section is keyed to, named explicitly
#: rather than silently chosen.
PRIMARY_METHOD = "joint"
PRIMARY_METHOD_NOTE = (
    "All three methods favor Pittsburgh. The joint simulation is the primary "
    "probability used for the score-distribution display; Ridge and Elo are "
    "independent comparisons."
)

EXPLANATION = (
    "Different methods estimate the same game differently. The score "
    "distributions and intervals below come from the joint simulation."
)


def _fmt(row: CfbResearchForecast) -> dict[str, Any]:
    home, away = row.home_team_name, row.away_team_name
    favourite = home if row.joint_home_win_probability >= 0.5 else away

    by_metric: dict[str, dict[str, Any]] = {}
    for band in row.intervals or []:
        by_metric.setdefault(str(band["metric"]), {})[int(band["level"])] = {
            "lower": band["lower"], "upper": band["upper"]}

    return {
        "schema_version": SCHEMA_VERSION,
        "forecast_row_id": str(row.id),
        "forecast_id": row.forecast_id,
        "forecast_sha256": row.forecast_sha256,
        "package_index_sha256": row.package_index_sha256,
        "publication_receipt_sha256": row.publication_receipt_sha256,
        "baseline_configuration": row.baseline_configuration,
        "engine_label": "College Football Gen 1 — Early research forecast",

        "game": {
            "source_provider": row.source_provider,
            "source_game_id": row.source_game_id,
            "home_team": home,
            "away_team": away,
            "home_source_team_id": row.home_source_team_id,
            "away_source_team_id": row.away_source_team_id,
            "matchup": f"{away} at {home}",
            "kickoff_utc": row.captured_kickoff_utc.astimezone(UTC).isoformat(),
            "neutral_site": row.neutral_site,
            "conference_competition": row.conference_competition,
        },

        # The visible research pick. A forecast, not a wager.
        "pick": {
            "team": favourite,
            "label": f"Research pick: {favourite}",
            "basis": METHOD_LABELS[PRIMARY_METHOD],
            "is_a_wager": False,
        },

        "expected": {
            "away_points": row.expected_away_points,
            "home_points": row.expected_home_points,
            "total": row.expected_total,
            "home_margin": row.expected_home_margin,
        },

        # Three methods, three entries, never averaged and never merged.
        "win_probabilities": [
            {"method": "joint", "label": METHOD_LABELS["joint"],
             "home": row.joint_home_win_probability,
             "away": row.joint_away_win_probability, "primary": True},
            {"method": "ridge", "label": METHOD_LABELS["ridge"],
             "home": row.ridge_home_win_probability,
             "away": row.ridge_away_win_probability, "primary": False},
            {"method": "elo", "label": METHOD_LABELS["elo"],
             "home": row.elo_home_win_probability,
             "away": row.elo_away_win_probability, "primary": False},
        ],
        "primary_method": PRIMARY_METHOD,
        "primary_method_note": PRIMARY_METHOD_NOTE,
        "explanation": EXPLANATION,
        "tie_probability": row.tie_probability,

        "draw_count": row.draw_count,
        "simulation_precision_note": (
            f"{row.draw_count:,} simulated games. That is the precision of the "
            "simulation itself, which is not a statement about how accurate the "
            "model is — this is its first prospective College Football forecast."
        ),
        "simulation_mcse": row.simulation_mcse,

        "intervals": by_metric,
        "margin_sentence": _margin_sentence(by_metric, home, away),
        "pmfs": row.pmfs,
        "illustrative_totals": [
            {**scenario, "label": "Illustrative line · no sportsbook quote recorded"}
            for scenario in (row.illustrative_totals or [])
        ],

        "clocks": {
            "source_cutoff_utc": row.source_cutoff_utc.astimezone(UTC).isoformat(),
            "forecast_created_utc": row.forecast_created_utc.astimezone(UTC).isoformat(),
            "exchange_published_utc": (
                row.exchange_published_utc.astimezone(UTC).isoformat()
                if row.exchange_published_utc else None),
            "imported_at_utc": row.imported_at.astimezone(UTC).isoformat(),
        },

        "limitations": list(row.limitations or []),
        "disclosure": _disclosure(row),
        "authority": AUTHORITY,
        "attribution": ATTRIBUTION,
        "raw_source_policy": RAW_SOURCE_POLICY,
        "technical": {
            "model_bindings": row.model_bindings,
            "source_bindings": row.source_bindings,
            "transformation_accounting": row.transformation_accounting,
            "challenger": row.challenger,
            "feature_values": row.feature_values,
        },
    }


def _margin_sentence(by_metric: dict[str, Any], home: str, away: str) -> str | None:
    band = (by_metric.get("home_margin") or {}).get(80)
    if not band:
        return None
    lower, upper = band["lower"], band["upper"]
    # home_margin is home minus away, so a negative lower bound is the AWAY team
    # winning by that much. Rendering it as "-10" invites the reader to work out
    # the sign convention themselves, and half of them will get it backwards.
    low = (f"{away} by {abs(int(lower))}" if lower < 0
           else f"{home} by {int(lower)}")
    high = (f"{home} by {int(upper)}" if upper > 0
            else f"{away} by {abs(int(upper))}")
    return f"The middle 80% of simulated outcomes range from {low} to {high}."


def _disclosure(row: CfbResearchForecast) -> list[str]:
    """Evidence-supported bullets only. No player, coaching or weather reasons."""

    features = row.feature_values or {}
    challenger = row.challenger or {}
    bullets = [
        f"Both teams had {int(features.get('home_current_season_games', 0))} "
        "eligible completed 2026 games in the forecast's current-season history."
        if features.get("home_current_season_games") == features.get("away_current_season_games")
        else "Each team's eligible completed 2026 game count is recorded in the technical details.",
        "The model uses prior results and supported team statistics, including "
        "prior-season and opponent-strength information.",
        "Historical assessment is reconstructed evidence, with historical "
        "information-availability limitations.",
        "Quarterback, injury, lineup, weather, recruiting and transfer inputs are absent.",
        "The Ridge, Elo and joint-simulation probabilities are separately "
        "estimated and are not one shared model.",
    ]
    if challenger.get("publishedAsSecondArm") is False:
        bullets.append(
            "The opener/extended-gap challenger was not applied because this game "
            f"met ordinary-support criteria (support: {challenger.get('support')}). "
            "That model's opener weakness remains under research.")
    bullets.append(
        "The upstream data provides no record-level revision timestamp and no "
        "explicit postponed or cancelled status.")
    bullets.append(RAW_SOURCE_POLICY)
    bullets.append(
        "The model has no established prospective accuracy or profitability record.")
    return bullets


def research_board(session: Session, *, date_iso: str | None = None) -> dict[str, Any]:
    """Every imported CFB forecast, optionally for one kickoff date."""

    rows = list(session.scalars(
        select(CfbResearchForecast).order_by(CfbResearchForecast.captured_kickoff_utc)))
    forecasts = [_fmt(row) for row in rows]
    if date_iso:
        forecasts = [f for f in forecasts
                     if f["game"]["kickoff_utc"][:10] == date_iso
                     or _local_date(f["game"]["kickoff_utc"]) == date_iso]
    return {
        "schema_version": SCHEMA_VERSION,
        "sport": CFB_SPORT_CODE,
        "label": "College Football · Research",
        "date": date_iso,
        "forecasts": forecasts,
        "count": len(forecasts),
        # One forecast is not a slate. Said explicitly so an empty board reads as
        # "nothing published for this date" rather than "the page is broken".
        "empty_state": (
            "No College Football research forecast has been published for this date. "
            "AISP has no admitted College Football schedule source, so games without "
            "a published forecast are not listed."),
        "coverage_note": (
            "One imported forecast does not mean the College Football slate has "
            "model coverage."),
        "authority": AUTHORITY,
        "attribution": ATTRIBUTION,
    }


def game_detail(session: Session, *, source_game_id: str) -> dict[str, Any] | None:
    row = session.scalar(select(CfbResearchForecast).where(
        CfbResearchForecast.source_game_id == str(source_game_id)))
    return _fmt(row) if row else None


def total_scenario(session: Session, *, source_game_id: str, line: float) -> dict[str, Any] | None:
    """Over/push/under from the complete PMF. Never from the expected total."""

    row = session.scalar(select(CfbResearchForecast).where(
        CfbResearchForecast.source_game_id == str(source_game_id)))
    if row is None:
        return None
    pmf = (row.pmfs or {}).get("total")
    if not pmf:
        return None
    values, counts = pmf["values"], pmf["counts"]
    draws = int(pmf["draw_count"])
    over = sum(c for v, c in zip(values, counts) if v > line)
    push = sum(c for v, c in zip(values, counts) if v == line)
    under = sum(c for v, c in zip(values, counts) if v < line)
    return {
        "line": line,
        "over": over / draws,
        "push": push / draws,
        "under": under / draws,
        "draw_count": draws,
        "counts": {"over": over, "push": push, "under": under},
        "integer_line": float(line).is_integer(),
        "push_possible": float(line).is_integer(),
        "label": "Illustrative line · no sportsbook quote recorded",
        "market_recorded": False,
        "note": ("Computed from the complete retained total distribution over "
                 f"{draws:,} simulated games, not from the expected total. "
                 "No sportsbook offered this line to AISP."),
    }


def _local_date(kickoff_iso: str) -> str:
    from zoneinfo import ZoneInfo
    return (datetime.fromisoformat(kickoff_iso)
            .astimezone(ZoneInfo("America/New_York")).date().isoformat())


__all__ = ["ATTRIBUTION", "AUTHORITY", "EXPLANATION", "METHOD_LABELS",
           "PRIMARY_METHOD", "PRIMARY_METHOD_NOTE", "RAW_SOURCE_POLICY",
           "SCHEMA_VERSION", "game_detail", "research_board", "total_scenario"]
