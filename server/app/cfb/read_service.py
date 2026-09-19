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
An invented price or line, an expected betting return, or a recommendation. A
game's ``pipeline.market`` lists only STORED SportsGameOdds quotes (with their
quote, fetch and receipt clocks) and compares them with the joint simulation
alone (``app.cfb.market_read``); a game with no stored quote says so. An
illustrative scenario line is labelled as one and never stands in for a quote.
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
    "status": "Prospective forecast · research model",
    "bankroll": "Virtual paper account only · no real money",
    "policy": ("AISP_CFB_PAPER_POLICY_V1 (owner-approved 2026-09-15): moneyline only, quarter Kelly, "
               "0.5% / $5 / 1% / 2% caps, 2-point minimum edge vs the same book's no-vig price, "
               "decided 30 to 15 minutes before kickoff"),
    "market": ("College Football prices come only from the SportsGameOdds CFB lane; each game states whether a "
               "quote is stored, and compares a stored quote with the joint simulation only"),
    "research_only": True,
    "controlling_channel": ("Joint score simulation (paired-residual draws) is the channel the paper policy "
                            "decides on. Ridge (Development-calibrated) and Elo are shown beside it and never "
                            "averaged with it or substituted for it."),
    "note": ("Forecasts are verified from the producer's content-addressed discovery and imported with their "
             "original publication clock. A paper position needs, at the checkpoint: a currently eligible "
             "discovery lease, the producer's per-forecast eligibility record, and a fresh two-sided "
             "SportsGameOdds moneyline. Whether any collector is fetching a price is stated per game from the "
             "CFB lane's capacity gate; a checkpoint with no usable price records an explicit no-position "
             "reason (declined, not a no-bet)."),
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

#: What each channel is, so none is read as another's number.
METHOD_MEANINGS = {
    "joint": "Winner share of the paired-residual joint score draws; the distributions below come from these draws.",
    "ridge": "Calibrated winner probability from the Ridge expected-margin model.",
    "elo": "Winner probability from the Elo rating benchmark, estimated separately.",
}

#: Which probability the distribution section is keyed to, named explicitly
#: rather than silently chosen.
PRIMARY_METHOD = "joint"
PRIMARY_METHOD_TAIL = (
    "The joint simulation is the primary probability used for the "
    "score-distribution display; Ridge and Elo are independent comparisons."
)


def _primary_method_note(row: CfbResearchForecast) -> str:
    """Which team each method favors, computed from this row -- never hard-coded."""

    home, away = row.home_team_name, row.away_team_name
    picks = {
        method: home if h >= 0.5 else away
        for method, h in (("joint", row.joint_home_win_probability),
                          ("ridge", row.ridge_home_win_probability),
                          ("elo", row.elo_home_win_probability))
    }
    if len(set(picks.values())) == 1:
        lead = f"All three methods favor {picks['joint']}."
    else:
        lead = "The methods disagree: " + "; ".join(
            f"{METHOD_LABELS[m]} favors {team}" for m, team in picks.items()) + "."
    return f"{lead} {PRIMARY_METHOD_TAIL}"


def _binding_and_eligibility(session: Session, row: CfbResearchForecast) -> dict[str, Any]:
    from app.cfb.game_binding import canonical_game_id
    from app.services.research_paper_policy import CFB, eligibility_status

    game_id = canonical_game_id(session, row.source_game_id)
    elig = eligibility_status(session, CFB, forecast_id=row.forecast_id, event_id=f"cfbd:{row.source_game_id}")
    return {
        "canonical_game": {
            "bound": game_id is not None,
            "game_id": str(game_id) if game_id else None,
            "source": "CFBD",
            "source_game_id": row.source_game_id,
            "label": (f"Bound to AISP's canonical schedule by CFBD game {row.source_game_id}"
                      if game_id else "Not admitted to AISP's canonical schedule"),
        },
        "paper_eligibility": {
            "eligible": bool(elig["eligible"]),
            "state": elig["state"],
            "label": ("The producer's paper-eligibility record binds this exact forecast"
                      if elig["eligible"] else
                      "Research only until the producer's paper-eligibility record binds this forecast; the checkpoint "
                      "cannot position it"),
        },
        "pipeline": _pipeline(session, row, game_id),
    }


def _pipeline(session: Session, row: CfbResearchForecast, game_id: Any) -> dict[str, Any]:
    """Discovery lease, decision, price, final and score -- each as AISP holds it now."""

    from datetime import timedelta

    from app.cfb.production_import import present_eligibility

    out: dict[str, Any] = {
        "origin": row.origin,
        "publication_clock": {
            "source": row.publication_clock_source,
            "label": ("Discovery-bound receipt (committedAtUtc) — the producer's designated publication clock"
                      if row.publication_clock_source == "DISCOVERY_RECEIPT_COMMITTED_AT"
                      else "Package-local publication receipt (earlier research import)"),
            "published_utc": row.exchange_published_utc.astimezone(UTC).isoformat()
            if row.exchange_published_utc else None,
        },
        "discovery": present_eligibility(session, row) if row.origin == "PRODUCTION_DISCOVERY" else {
            "state": "NOT_IN_PRODUCTION_DISCOVERY", "eligible": False,
            "detail": "An earlier research package, retained as history; never decision-eligible."},
    }
    if game_id is None:
        return out
    from app.models import (
        CfbFinalObservation, Game, OfficialAspPosition, ResearchForecastEvaluation, ShadowExperimentDecision,
    )

    decision = session.execute(
        select(ShadowExperimentDecision, OfficialAspPosition)
        .outerjoin(OfficialAspPosition, OfficialAspPosition.shadow_decision_id == ShadowExperimentDecision.id)
        .where(ShadowExperimentDecision.game_id == game_id, ShadowExperimentDecision.sport == "CFB")
        .order_by(ShadowExperimentDecision.recommended_at.desc()).limit(1)).first()
    if decision is None:
        out["decision"] = None
    else:
        from app.cfb.paper_decision import checkpoint_semantics

        row_d, pos = decision
        recorded = row_d.recommended_at
        reason = (row_d.qualification_reasons or {}).get("reason")
        out["decision"] = {
            "recommendation": row_d.recommendation, "reason": reason,
            # NO_BET (priced, edge below policy) vs NO_PRICE (declined, no usable
            # pair) vs BLOCKED (not evaluated) -- read from the row, never rewritten.
            "semantics": checkpoint_semantics(row_d.recommendation, reason),
            "detail": row_d.recommendation_reason,
            "recorded_at": (recorded if recorded.tzinfo else recorded.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
            if recorded else None,
            "position": None if pos is None else {
                "position_id": str(pos.id), "stake": float(pos.stake), "status": str(pos.status),
                "result": str(pos.result)}}
    game = session.get(Game, game_id)
    kickoff = None
    if game is not None and game.starts_at is not None:
        kickoff = (game.starts_at if game.starts_at.tzinfo else game.starts_at.replace(tzinfo=UTC)).astimezone(UTC)
        out["decision_window"] = {"opens_utc": (kickoff - timedelta(minutes=30)).isoformat(),
                                  "closes_utc": (kickoff - timedelta(minutes=15)).isoformat()}
    status = str(game.status).split(".")[-1].upper() if game is not None else None
    out["game_status"] = status
    if status == "FINAL":
        final = session.scalar(select(CfbFinalObservation).where(CfbFinalObservation.game_id == game_id)
                               .order_by(CfbFinalObservation.created_at.desc()).limit(1))
        evaluation = session.scalar(select(ResearchForecastEvaluation).where(
            ResearchForecastEvaluation.canonical_game_id == game_id, ResearchForecastEvaluation.is_current.is_(True),
            ResearchForecastEvaluation.engine_id == "CFB_GEN1_JOINT_BASELINE").limit(1))
        captured = final.captured_at_utc if final else None
        out["final"] = {
            "home_score": game.home_score, "away_score": game.away_score,
            "source": "CFBD (retained by the producer)",
            "observed_utc": (captured if captured.tzinfo else captured.replace(tzinfo=UTC)).isoformat()
            if captured else None,
            "completion_clock": final.completion_clock_status if final else None,
            "overtime": bool((final.evidence_row or {}).get("overtime")) if final else None,
        }
        out["evaluation"] = None if evaluation is None else {
            "channel": "Joint score simulation", "log_loss": evaluation.log_loss,
            "two_way_brier": (evaluation.detail or {}).get("two_way_brier"),
            "winner_correct": evaluation.winner_correct, "probability_of_actual": evaluation.probability_of_actual,
            "margin_error": evaluation.margin_error, "total_error": evaluation.total_error,
            "crps_margin": evaluation.crps_margin, "forecast_id": evaluation.forecast_identity,
            "diagnostic_channels": (evaluation.detail or {}).get("diagnostic_channels")}
    try:
        from app.services.command_center import _cfb_price_note

        out["price"] = _cfb_price_note(session)
    except Exception:  # noqa: BLE001
        out["price"] = None
    try:
        from app.cfb.market_read import game_market

        # Stored quotes with their three clocks, compared with the joint channel
        # only; with none stored, the coverage note above says why.
        out["market"] = game_market(session, game_id, row, price_note=out.get("price"))
    except Exception:  # noqa: BLE001 -- the market reading is explanatory; never break the page
        out["market"] = None
    return out

EXPLANATION = (
    "Different methods estimate the same game differently. The score "
    "distributions and intervals below come from the joint simulation."
)


def _fmt(row: CfbResearchForecast, session: Session | None = None) -> dict[str, Any]:
    home, away = row.home_team_name, row.away_team_name
    favourite = home if row.joint_home_win_probability >= 0.5 else away

    by_metric: dict[str, dict[str, Any]] = {}
    for band in row.intervals or []:
        by_metric.setdefault(str(band["metric"]), {})[int(band["level"])] = {
            "lower": band["lower"], "upper": band["upper"]}
    bound = _binding_and_eligibility(session, row) if session is not None else {}
    stored = (((bound.get("pipeline") or {}).get("market") or {}).get("quotes")) or []
    illustrative_label = ("Illustrative line · not the stored sportsbook total (see Market)"
                          if any(q.get("market") == "TOTAL" for q in stored)
                          else "Illustrative line · no sportsbook quote recorded")

    return {
        "schema_version": SCHEMA_VERSION,
        "forecast_row_id": str(row.id),
        "forecast_id": row.forecast_id,
        "forecast_sha256": row.forecast_sha256,
        "package_index_sha256": row.package_index_sha256,
        "publication_receipt_sha256": row.publication_receipt_sha256,
        "baseline_configuration": row.baseline_configuration,
        "engine_label": ("College Football Gen 1 — prospective slate forecast"
                         if row.origin == "PRODUCTION_DISCOVERY"
                         else "College Football Gen 1 — Early research forecast"),
        # A CFB package is a prospective snapshot taken at its source cutoff and
        # published at its receipt clock. It is never relabelled as a
        # final-pregame refresh or a day-before preview.
        "horizon": "PROSPECTIVE_RESEARCH",
        "horizon_label": "Prospective forecast · published before kickoff · not a final pregame forecast",
        **bound,

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
            {"method": "joint", "label": METHOD_LABELS["joint"], "meaning": METHOD_MEANINGS["joint"],
             "home": row.joint_home_win_probability,
             "away": row.joint_away_win_probability, "primary": True},
            {"method": "ridge", "label": METHOD_LABELS["ridge"], "meaning": METHOD_MEANINGS["ridge"],
             "home": row.ridge_home_win_probability,
             "away": row.ridge_away_win_probability, "primary": False},
            {"method": "elo", "label": METHOD_LABELS["elo"], "meaning": METHOD_MEANINGS["elo"],
             "home": row.elo_home_win_probability,
             "away": row.elo_away_win_probability, "primary": False},
        ],
        "primary_method": PRIMARY_METHOD,
        "primary_method_note": _primary_method_note(row),
        "explanation": EXPLANATION,
        "tie_probability": row.tie_probability,

        "draw_count": row.draw_count,
        "simulation_precision_note": (
            f"{row.draw_count:,} simulated games. That is the precision of the "
            "simulation itself, which is not a statement about how accurate the "
            "model is — it has no established prospective College Football record."
        ),
        "simulation_mcse": row.simulation_mcse,

        "intervals": by_metric,
        "margin_sentence": _margin_sentence(by_metric, home, away),
        "pmfs": row.pmfs,
        "illustrative_totals": [
            {**scenario, "label": illustrative_label}
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
    # One card per game: its current revision by the designated publication
    # clock. Earlier revisions stay in the table as history.
    def utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    current: dict[str, CfbResearchForecast] = {}
    for row in rows:
        held = current.get(row.source_game_id)
        clock = utc(row.exchange_published_utc or row.imported_at)
        if held is None or clock > utc(held.exchange_published_utc or held.imported_at):
            current[row.source_game_id] = row
    rows = sorted(current.values(), key=lambda r: (utc(r.captured_kickoff_utc), r.source_game_id))
    forecasts = [_fmt(row, session) for row in rows]
    if date_iso:
        forecasts = [f for f in forecasts
                     if f["game"]["kickoff_utc"][:10] == date_iso
                     or _local_date(f["game"]["kickoff_utc"]) == date_iso]
    return {
        "schema_version": SCHEMA_VERSION,
        "sport": CFB_SPORT_CODE,
        "label": "College Football",
        "date": date_iso,
        "forecasts": forecasts,
        "count": len(forecasts),
        # One forecast is not a slate. Said explicitly so an empty board reads as
        # "nothing published for this date" rather than "the page is broken".
        "empty_state": (
            "No College Football forecast has been published for this date. "
            "AISP admits a College Football game only from a verified forecast, "
            "so games without one are not listed."),
        "coverage_note": (
            "Coverage is the producer's scope: FBS-vs-FBS regular-season games it published "
            "a verified forecast for. Other games are not listed."),
        "authority": AUTHORITY,
        "attribution": ATTRIBUTION,
    }


def resolve_source_game_id(session: Session, game_id: str) -> str:
    """A CFBD game id, also when an admitted AISP game id is given.

    Cross-sport surfaces link games by their canonical AISP id; this page is
    addressed by CFBD id. The admitted CFBD mapping translates one to the other;
    anything unmapped is passed through unchanged.
    """

    import uuid

    from app.cfb.game_binding import SOURCE_CFBD
    from app.models import ExternalIdMapping

    try:
        canonical = uuid.UUID(str(game_id))
    except ValueError:
        return str(game_id)
    mapped = session.scalar(select(ExternalIdMapping.external_id).where(
        ExternalIdMapping.entity_type == "game",
        ExternalIdMapping.source == SOURCE_CFBD,
        ExternalIdMapping.entity_id == canonical,
    ))
    return str(mapped) if mapped else str(game_id)


def game_detail(session: Session, *, source_game_id: str) -> dict[str, Any] | None:
    from app.cfb.production_import import current_forecast_for_game

    source_game_id = resolve_source_game_id(session, source_game_id)
    row = current_forecast_for_game(session, str(source_game_id))
    return _fmt(row, session) if row else None


def total_scenario(session: Session, *, source_game_id: str, line: float) -> dict[str, Any] | None:
    """Over/push/under from the complete PMF. Never from the expected total."""

    from app.cfb.production_import import current_forecast_for_game

    source_game_id = resolve_source_game_id(session, source_game_id)
    row = current_forecast_for_game(session, str(source_game_id))
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
    # Whether a sportsbook actually offered AISP this exact total: a stored
    # SportsGameOdds pair at this line, not the illustrative line typed here.
    recorded = False
    if row.canonical_game_id is not None:
        from app.cfb.market_read import stored_quotes

        recorded = any(q["market"] == "TOTAL" and q.get("line") is not None and float(q["line"]) == float(line)
                       for q in stored_quotes(session, [row.canonical_game_id], now=datetime.now(UTC))
                       .get(str(row.canonical_game_id), []))
    return {
        "line": line,
        "over": over / draws,
        "push": push / draws,
        "under": under / draws,
        "draw_count": draws,
        "counts": {"over": over, "push": push, "under": under},
        "integer_line": float(line).is_integer(),
        "push_possible": float(line).is_integer(),
        "label": ("Stored sportsbook line · see Market for its price and clocks" if recorded
                  else "Illustrative line · no sportsbook quote recorded"),
        "market_recorded": recorded,
        "note": ("Computed from the complete retained total distribution over "
                 f"{draws:,} simulated games, not from the expected total. "
                 + ("A stored sportsbook quote exists at this line." if recorded
                    else "No sportsbook offered this line to AISP.")),
    }


def _local_date(kickoff_iso: str) -> str:
    from zoneinfo import ZoneInfo
    return (datetime.fromisoformat(kickoff_iso)
            .astimezone(ZoneInfo("America/New_York")).date().isoformat())


__all__ = ["ATTRIBUTION", "AUTHORITY", "EXPLANATION", "METHOD_LABELS",
           "PRIMARY_METHOD", "PRIMARY_METHOD_TAIL", "RAW_SOURCE_POLICY",
           "SCHEMA_VERSION", "game_detail", "research_board", "total_scenario"]
