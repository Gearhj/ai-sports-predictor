"""Read models for every NBA Gen1 surface: slate cards, game page, performance, readiness.

Every payload carries the three channels under their own names, with units, and
every historical row carries ``evidence_class`` and a HISTORICAL label. Nothing
here writes, requests or decides.

CHANNEL DISCIPLINE ON THE WIRE
------------------------------
``channels.RIDGE_POINTS``        ``{home, away, total, home_margin, units: "points"}``
``channels.ELO_WINNER``          ``{home, away, units: "fractions 0-1"}``
``channels.JOINT_DISTRIBUTION``  ``{home, away, expected{...}, intervals, units}``
The slate ``model.home``/``model.away`` fields that cross-sport cards read are
the JOINT channel (the channel the NBA paper policy names), with
``controlling_channel`` saying so. Ridge points are never placed in a
probability field, and a Ridge value is never compared with a price.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.nba_gen1 import (
    CHANNEL_ELO, CHANNEL_JOINT, CHANNEL_RIDGE, EVIDENCE_HISTORICAL, EVIDENCE_PROSPECTIVE,
)
from app.nba import gen1_common as common

SCHEMA_BOARD = "AISP_NBA_GEN1_BOARD_V1"
SCHEMA_GAME = "AISP_NBA_GEN1_GAME_V1"
ET = ZoneInfo("America/New_York")
ENGINE_PRODUCTION = "NBA_GEN1_PRODUCTION"
HISTORICAL_LABEL = "HISTORICAL · 2025-26 reconstruction — not a prospective forecast"
PRODUCTION_LABEL = "Production baseline package (prospective)"

CHANNEL_LABELS = {CHANNEL_RIDGE: "Ridge expected points", CHANNEL_ELO: "Elo win probability",
                  CHANNEL_JOINT: "Joint-distribution probabilities"}
ENGINE_FOR_CHANNEL = {CHANNEL_RIDGE: "NBA_GEN1_RIDGE_POINTS", CHANNEL_ELO: "NBA_GEN1_ELO_WINNER",
                      CHANNEL_JOINT: "NBA_GEN1_JOINT_DISTRIBUTION"}


def _r(value: float | None, digits: int = 4) -> float | None:
    return round(float(value), digits) if value is not None else None


def channels(row: Any) -> dict[str, Any]:
    """The three channels of one forecast row, each under its own name, with units."""

    summary = row.joint_summary or {}
    return {
        CHANNEL_RIDGE: {"label": CHANNEL_LABELS[CHANNEL_RIDGE], "units": "points (overtime included)",
                        "is_probability": False, "home": _r(row.ridge_home_points, 2),
                        "away": _r(row.ridge_away_points, 2), "total": _r(row.ridge_total_points, 2),
                        "home_margin": _r(row.ridge_home_margin, 2),
                        "note": common.CHANNEL_ROLES[CHANNEL_RIDGE]["display_note"]},
        CHANNEL_ELO: {"label": CHANNEL_LABELS[CHANNEL_ELO], "units": "fractions 0 to 1", "is_probability": True,
                      "home": row.elo_home_win_probability, "away": row.elo_away_win_probability,
                      "tie": row.elo_tie_probability, "method": row.elo_method,
                      "note": common.CHANNEL_ROLES[CHANNEL_ELO]["display_note"]},
        CHANNEL_JOINT: {"label": CHANNEL_LABELS[CHANNEL_JOINT], "units": "fractions 0 to 1; scores in points",
                        "is_probability": True, "draw_count": row.joint_draw_count,
                        "home": row.joint_home_win_probability, "away": row.joint_away_win_probability,
                        "home_mcse": row.joint_home_win_mcse,
                        "expected": {"home": _r(row.joint_expected_home, 2), "away": _r(row.joint_expected_away, 2),
                                     "total": _r(row.joint_expected_total, 2),
                                     "margin": _r(row.joint_expected_margin, 2)},
                        "intervals": summary.get("intervals"),
                        "illustrative_totals": summary.get("illustrative_totals") or [],
                        "note": common.CHANNEL_ROLES[CHANNEL_JOINT]["display_note"]},
    }


def _teams(session: Session, game_ids: list[Any]) -> dict[str, dict[str, Any]]:
    from app.models import GameTeam, Team

    if not game_ids:
        return {}
    out: dict[str, dict[str, Any]] = defaultdict(dict)
    for game_id, is_home, team_id, abbreviation, name in session.execute(
            select(GameTeam.game_id, GameTeam.is_home, Team.id, Team.abbreviation, Team.name)
            .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id.in_(game_ids))).all():
        out[str(game_id)]["home" if is_home else "away"] = {"team_id": str(team_id), "abbreviation": abbreviation,
                                                             "name": name}
    for value in out.values():
        value["label"] = f"{(value.get('away') or {}).get('abbreviation', '?')} @ " \
                         f"{(value.get('home') or {}).get('abbreviation', '?')}"
    return out


def _uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def model_for_game(session: Session, game_id: Any) -> dict[str, Any] | None:
    """The cross-sport slate ``model`` block for one NBA game (production first, else historical)."""

    from app.models import NbaHistoricalForecast
    from app.nba.gen1_production import current_forecast_for_game

    row = current_forecast_for_game(session, game_id=_uuid(game_id))
    evidence, label = EVIDENCE_PROSPECTIVE, PRODUCTION_LABEL
    if row is None:
        row = session.scalar(select(NbaHistoricalForecast).where(
            NbaHistoricalForecast.canonical_game_id == _uuid(game_id)).limit(1))
        evidence, label = EVIDENCE_HISTORICAL, HISTORICAL_LABEL
    if row is None:
        return None
    return {
        "engine_id": ENGINE_PRODUCTION if evidence == EVIDENCE_PROSPECTIVE else "NBA_GEN1_HISTORICAL",
        "evidence_class": evidence, "evidence_label": label,
        "home": row.joint_home_win_probability, "away": row.joint_away_win_probability,
        "home_two_way": _r(row.joint_home_win_probability),
        "expected_home": _r(row.ridge_home_points, 2), "expected_away": _r(row.ridge_away_points, 2),
        "created_at": common.iso(getattr(row, "exchange_published_utc", None)),
        "forecast_id": getattr(row, "forecast_id", None) or row.codex_game_id,
        "controlling_channel": CHANNEL_JOINT, "expected_points_channel": CHANNEL_RIDGE,
        "separate_channels": {"elo_winner_home": row.elo_home_win_probability,
                              "joint_home": row.joint_home_win_probability},
        "channels": channels(row),
        "publication_clock_source": getattr(row, "publication_clock_source", None),
    }


def eligibility_for(session: Session, forecast_id: str | None, game_id: Any, *, now: datetime,
                    as_of: datetime | None = None) -> dict[str, Any]:
    """The newest NBA discovery's reading of a forecast, and the recorded checkpoint decision."""

    from app.models import NbaDiscoveryManifest, NbaDiscoveryObservation, ShadowExperiment, ShadowExperimentDecision
    from app.nba.gen1_production import NO_DISCOVERY, NOT_IN_CURRENT_DISCOVERY
    from app.services.research_paper_policy import NBA as NBA_POLICY

    newest = select(NbaDiscoveryManifest).order_by(NbaDiscoveryManifest.producer_created_utc.desc().nullslast(),
                                                   NbaDiscoveryManifest.first_verified_at.desc()).limit(1)
    if as_of is not None:
        newest = newest.where(NbaDiscoveryManifest.first_verified_at <= as_of)
    manifest = session.scalar(newest)
    reading = None
    if forecast_id:
        if manifest is None:
            reading = {"state": NO_DISCOVERY}
        else:
            obs = session.scalar(select(NbaDiscoveryObservation).where(
                NbaDiscoveryObservation.manifest_id == manifest.id,
                NbaDiscoveryObservation.forecast_id == forecast_id))
            if obs is None:
                reading = {"state": NOT_IN_CURRENT_DISCOVERY, "manifest_sha256": manifest.manifest_sha256}
            else:
                state = obs.lease_at_check if not obs.verified else None
                if obs.verified:
                    if obs.lease_at_check == "NO_LEASE_IN_SCHEMA":
                        state = "NO_LEASE_IN_SCHEMA"
                    elif not obs.currently_eligible:
                        state = "HISTORICAL"
                    else:
                        until = common.aware(obs.eligibility_valid_until_utc)
                        state = "CURRENT" if until is not None and now < until else "EXPIRED_LEASE"
                else:
                    state = "REJECTED"
                reading = {"state": state, "manifest_sha256": manifest.manifest_sha256,
                           "manifest_schema": manifest.schema_version,
                           "lease_valid_until": common.iso(obs.eligibility_valid_until_utc)}
    decisions = select(ShadowExperimentDecision).join(
        ShadowExperiment, ShadowExperiment.id == ShadowExperimentDecision.experiment_id).where(
        ShadowExperiment.decision_policy == NBA_POLICY.policy_version,
        ShadowExperimentDecision.phase == "FINAL_PREGAME", ShadowExperimentDecision.game_id == _uuid(game_id))
    if as_of is not None:
        decisions = decisions.where(ShadowExperimentDecision.recommended_at <= as_of)
    checkpoint = None
    for row in session.scalars(decisions.order_by(ShadowExperimentDecision.recommended_at)):
        evidence = row.evidence if isinstance(row.evidence, dict) else {}
        reasons = row.qualification_reasons if isinstance(row.qualification_reasons, dict) else {}
        checkpoint = {"decision_id": str(row.id), "recorded_at": common.iso(row.recommended_at),
                      "recommendation": row.recommendation, "reason": reasons.get("reason"),
                      "detail": row.recommendation_reason, "discovery": evidence.get("discovery")}
    return {"now": reading, "checkpoint": checkpoint}


def price_note(session: Session, settings: Any) -> dict[str, Any]:
    """Why an NBA game has no price, from the lane's own capacity gate (writes nothing)."""

    from app.nba.sgo_price import LANE, capacity, load_binding

    enabled = bool(getattr(settings, "nba_sgo_lane_enabled", False))
    try:
        gate = capacity(session, settings)
    except Exception as error:  # noqa: BLE001 -- explanatory only
        return {"reason": "PRICE_STATE_UNAVAILABLE", "detail": type(error).__name__, "lane": LANE,
                "lane_enabled": enabled}
    binding = load_binding(settings)
    detail = {
        "LANE_DISABLED": "The NBA SportsGameOdds lane is off (NBA_SGO_LANE_ENABLED=false); no collector runs.",
        "NO_AUTHORITY_WINDOW": "No NBA acquisition window has been authorised.",
        "AUTHORIZATION_EXPIRED": "The NBA acquisition window has expired.",
        "NO_OBJECTS_ALLOCATED": "The NBA lane has no object allowance (cap 0).",
        "ASSIGNMENT_CAP_REACHED": "The NBA lane has spent its allowance.",
    }.get(gate["state"], gate["state"])
    return {"reason": gate["state"], "detail": detail, "lane": LANE, "lane_enabled": enabled,
            "objects_allowed": gate.get("objects_allowed", 0), "spent_on_lane": gate.get("spent_on_lane"),
            "binding": binding.as_dict()}


# ---------------------------------------------------------------- game page --

def resolve_game(session: Session, ref: str):
    """An AISP game UUID, an ESPN id, or a Codex game id -> the AISP game (NBA only)."""

    from app.models import Game, League
    from app.nba.gen1_identity import canonical_game_id

    text = str(ref).strip()
    if text.startswith("nba-espn:game:"):
        text = text.split(":")[-1]
    game_id = None
    if text.isdigit():
        game_id = canonical_game_id(session, text)
    else:
        try:
            game_id = uuid.UUID(text)
        except ValueError:
            return None
    if game_id is None:
        return None
    game = session.get(Game, game_id)
    if game is None:
        return None
    league = session.get(League, game.league_id)
    return game if league is not None and league.code == common.LEAGUE_CODE else None


def _evaluations(session: Session, game_id: Any) -> list[dict[str, Any]]:
    from app.models import NbaChannelEvaluation

    out = []
    for row in session.scalars(select(NbaChannelEvaluation).where(NbaChannelEvaluation.canonical_game_id == game_id)
                               .order_by(NbaChannelEvaluation.evidence_class, NbaChannelEvaluation.channel)):
        out.append({"evidence_class": row.evidence_class, "channel": row.channel,
                    "channel_label": CHANNEL_LABELS.get(row.channel, row.channel),
                    "forecast_identity": row.forecast_identity, "result_source": row.result_source,
                    "final": {"home": row.home_score, "away": row.away_score, "overtime": row.overtime},
                    "home_win_probability": row.home_win_probability, "probability_of_actual": row.probability_of_actual,
                    "log_loss": row.log_loss, "two_way_brier": row.two_way_brier, "winner_correct": row.winner_correct,
                    "predicted": {"home": row.predicted_home, "away": row.predicted_away},
                    "home_abs_error": row.home_abs_error, "away_abs_error": row.away_abs_error,
                    "margin_error": row.margin_error, "total_error": row.total_error,
                    "crps": {"home": row.crps_home, "away": row.crps_away, "margin": row.crps_margin,
                             "total": row.crps_total} if row.channel == CHANNEL_JOINT else None,
                    "interval_coverage": row.interval_coverage, "scoring_version": row.scoring_version,
                    "evaluated_at": common.iso(row.evaluated_at)})
    return out


def game_detail(session: Session, settings: Any, ref: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    from app.models import ExternalIdMapping, NbaFinalObservation, NbaGen1Forecast, NbaHistoricalForecast

    moment = now or datetime.now(UTC)
    game = resolve_game(session, ref)
    if game is None:
        return None
    espn = session.scalar(select(ExternalIdMapping.external_id).where(
        ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == common.SOURCE_ESPN_GAME,
        ExternalIdMapping.entity_id == game.id))
    teams = _teams(session, [game.id]).get(str(game.id), {})
    production = [
        {"forecast_id": row.forecast_id, "forecast_sha256": row.forecast_sha256,
         "package_index_sha256": row.package_index_sha256, "envelope_schema": row.envelope_schema,
         "revision_key": row.revision_key, "evidence_class": row.evidence_class, "label": PRODUCTION_LABEL,
         "handoff_sha256": row.handoff_sha256, "configuration_identity": row.configuration_identity,
         "distribution_configuration_identity": row.distribution_configuration_identity,
         "population_logical_sha256": row.population_logical_sha256,
         "captured_tipoff_utc": common.iso(row.captured_tipoff_utc),
         "clocks": {"source_cutoff_utc": common.iso(row.source_cutoff_utc),
                    "forecast_created_utc": common.iso(row.forecast_created_utc),
                    "producer_published_utc": common.iso(row.producer_published_utc),
                    "designated_publication_utc": common.iso(row.exchange_published_utc),
                    "publication_clock_source": row.publication_clock_source,
                    "imported_at": common.iso(row.imported_at)},
         "orientation": ((row.detail or {}).get("orientation") or {}).get("state"),
         "limitations": (row.detail or {}).get("limitations"), "channels": channels(row),
         "eligibility": eligibility_for(session, row.forecast_id, game.id, now=moment)}
        for row in session.scalars(select(NbaGen1Forecast).where(NbaGen1Forecast.canonical_game_id == game.id)
                                   .order_by(NbaGen1Forecast.exchange_published_utc.desc().nullslast()))]
    historical = [
        {"codex_game_id": row.codex_game_id, "evidence_class": row.evidence_class, "label": HISTORICAL_LABEL,
         "assessment_receipt_sha256": row.assessment_receipt_sha256, "joint_receipt_sha256": row.joint_receipt_sha256,
         "joint_counts_int64_le_sha256": row.joint_counts_int64_le_sha256,
         "point_model": {"id": row.point_model_id, "sha256": row.point_model_sha256},
         "elo_model_sha256": row.elo_model_sha256, "game_date": row.game_date.isoformat(),
         "qualification": (row.detail or {}).get("qualification"), "channels": channels(row)}
        for row in session.scalars(select(NbaHistoricalForecast).where(
            NbaHistoricalForecast.canonical_game_id == game.id))]
    finals = [{"source": row.source, "home": row.home_points, "away": row.away_points, "periods": row.periods,
               "overtime": row.overtime, "source_object_sha256": row.source_object_sha256,
               "captured_at_utc": common.iso(row.captured_at_utc),
               "completion_clock_status": row.completion_clock_status, "status": row.source_status}
              for row in session.scalars(select(NbaFinalObservation).where(NbaFinalObservation.game_id == game.id))]
    return {
        "schema_version": SCHEMA_GAME, "game_id": str(game.id), "espn_game_id": espn,
        "codex_game_id": f"nba-espn:game:{espn}" if espn else None, "teams": teams,
        "tipoff_utc": common.iso(game.starts_at), "status": str(game.status).split(".")[-1],
        "final": {"home": game.home_score, "away": game.away_score} if game.home_score is not None else None,
        "production_forecasts": production, "historical_forecasts": historical, "finals": finals,
        "evaluations": _evaluations(session, game.id),
        "price": price_note(session, settings),
        "channel_roles": common.CHANNEL_ROLES,
        "market_channels": {k: list(v) for k, v in common.MARKET_CHANNELS.items()},
        "limitations": list(common.RETAINED_LIMITATIONS),
        "authority": {"research_only": True, "wagering_authority": False,
                      "note": "No NBA price is held and no NBA position exists. Historical rows are never "
                              "prospective forecasts and never feed a decision."},
    }


# ---------------------------------------------------------------- board -----

def board(session: Session, settings: Any, *, date_iso: str | None = None,
          now: datetime | None = None) -> dict[str, Any]:
    from app.models import Game, League, NbaChannelEvaluation, NbaGen1Forecast, NbaHistoricalForecast
    from app.nba.gen1_handoff import production_status

    moment = now or datetime.now(UTC)
    verdict = production_status(settings)
    query = select(NbaGen1Forecast).order_by(NbaGen1Forecast.captured_tipoff_utc)
    rows = list(session.scalars(query))
    latest: dict[str, Any] = {}
    for row in rows:
        key = str(row.canonical_game_id or row.codex_game_id)
        prior = latest.get(key)
        if prior is None or (common.aware(row.exchange_published_utc) or moment) > \
                (common.aware(prior.exchange_published_utc) or moment):
            latest[key] = row
    games = []
    teams = _teams(session, [r.canonical_game_id for r in latest.values() if r.canonical_game_id])
    for row in latest.values():
        if date_iso and row.captured_game_date.isoformat() != date_iso:
            continue
        games.append({"game_id": str(row.canonical_game_id) if row.canonical_game_id else None,
                      "codex_game_id": row.codex_game_id, "espn_game_id": row.source_game_id,
                      "matchup": f"{row.away_team_code} at {row.home_team_code}",
                      "teams": teams.get(str(row.canonical_game_id), {}),
                      "tipoff_utc": common.iso(row.captured_tipoff_utc), "game_date": row.captured_game_date.isoformat(),
                      "forecast_id": row.forecast_id, "label": PRODUCTION_LABEL, "evidence_class": row.evidence_class,
                      "revisions": sum(1 for r in rows if r.codex_game_id == row.codex_game_id),
                      "channels": channels(row),
                      "eligibility": eligibility_for(session, row.forecast_id, row.canonical_game_id, now=moment)
                      if row.canonical_game_id else None})
    historical_count = session.scalar(select(func.count()).select_from(NbaHistoricalForecast)) or 0
    scored = {cls: session.scalar(select(func.count(func.distinct(NbaChannelEvaluation.canonical_game_id))).where(
        NbaChannelEvaluation.evidence_class == cls)) or 0 for cls in (EVIDENCE_PROSPECTIVE, EVIDENCE_HISTORICAL)}
    return {
        "schema_version": SCHEMA_BOARD, "generated_at_utc": moment.isoformat(),
        "handoff": {"status": verdict.status, "reason": verdict.reason,
                    "production_label_allowed": verdict.production_label_allowed,
                    "handoff_sha256": verdict.detail.get("handoff_sha256")},
        "production": games,
        "historical": {"rows": historical_count, "games_scored": scored[EVIDENCE_HISTORICAL],
                       "label": HISTORICAL_LABEL},
        "prospective_games_scored": scored[EVIDENCE_PROSPECTIVE],
        "channel_roles": common.CHANNEL_ROLES,
        "market_channels": {k: list(v) for k, v in common.MARKET_CHANNELS.items()},
        "limitations": list(common.RETAINED_LIMITATIONS),
        "note": ("NBA 2026-27 regular season opens 2026-10-20. Production packages are prospective; the "
                 "2025-26 rows are HISTORICAL reconstructions. No NBA price is held and no position exists."),
    }


# ---------------------------------------------------------- performance -----

def performance_models(session: Session) -> list[dict[str, Any]]:
    """Model Performance entries, one per (evidence class, channel). Never pooled across either."""

    from app.models import NbaChannelEvaluation

    rows = list(session.scalars(select(NbaChannelEvaluation).order_by(NbaChannelEvaluation.start_utc)))
    grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in rows:
        grouped[(row.evidence_class, row.channel)].append(row)
    teams = _teams(session, list({row.canonical_game_id for row in rows}))
    models = []
    for (evidence, channel), entries in sorted(grouped.items()):
        historical = evidence == EVIDENCE_HISTORICAL
        models.append({
            "engine_id": ENGINE_FOR_CHANNEL[channel], "engine_version": evidence,
            "model_label": f"NBA Gen1 · {CHANNEL_LABELS[channel]}"
                           + (" · HISTORICAL 2025-26 reconstruction" if historical else " · prospective"),
            "channel": channel, "is_probability": channel != CHANNEL_RIDGE,
            "scoring_version": entries[0].scoring_version, "snapshot_types": [],
            "evidence_classification": ("HISTORICAL_RECONSTRUCTION_NOT_PROSPECTIVE" if historical
                                        else "PROSPECTIVE_RESEARCH_SCORES"),
            "cohorts": {"ALL": _metrics(entries, channel), "VERIFIED_PUBLICATION": _metrics(
                [] if historical else entries, channel)},
            "cohort_note": ("HISTORICAL: Model Studio's fixed 2025-26 assessment, models frozen before any 2025-26 "
                            "score was read (evidence class A). No publication clock exists, so no row is in the "
                            "verified-publication cohort, and none is ever counted as a prospective forecast."
                            if historical else
                            "Production packages scored only when published before tipoff."),
            "games": [_game(row, teams) for row in reversed(entries[-200:])],
        })
    return models


def _metrics(entries: list[Any], channel: str) -> dict[str, Any]:
    def mean(values: list[float]) -> float | None:
        return fmean(values) if values else None

    probability = channel != CHANNEL_RIDGE
    two_way = [(row, row.home_win_probability, row.home_score > row.away_score) for row in entries
               if probability and row.home_win_probability is not None]
    bins = []
    for index in range(10):
        lo, hi = index / 10, (index + 1) / 10
        members = [(p, won) for _, p, won in two_way if lo <= p < hi or (index == 9 and p == 1.0)]
        if members:
            bins.append({"lower": lo, "upper": hi, "n": len(members), "mean_predicted": fmean(p for p, _ in members),
                         "observed": sum(1 for _, won in members if won) / len(members)})
    series, seen_b, seen_l, count = [], 0.0, 0.0, 0
    daily: dict[str, list[Any]] = defaultdict(list)
    for row in entries:
        daily[common.aware(row.start_utc).astimezone(ET).date().isoformat()].append(row)
    for day in sorted(daily):
        for row in daily[day]:
            if probability and row.log_loss is not None:
                seen_b += row.two_way_brier
                seen_l += row.log_loss
                count += 1
        series.append({"date": day, "games": len(daily[day]), "cumulative_n": count,
                       "cumulative_brier_two_way": seen_b / count if count else None,
                       "cumulative_log_loss_two_way": seen_l / count if count else None,
                       "cumulative_market_n": 0, "cumulative_model_brier_matched": None,
                       "cumulative_market_brier_matched": None})
    starts = [row.start_utc for row in entries]
    decided = [row for row in entries if row.winner_correct is not None]
    return {
        "n": len(entries),
        "period": {"first_start_utc": common.iso(min(starts)) if starts else None,
                   "last_start_utc": common.iso(max(starts)) if starts else None},
        "accuracy": (sum(1 for r in decided if r.winner_correct) / len(decided)) if probability and decided else None,
        "correct_winners": sum(1 for r in decided if r.winner_correct) if probability else None,
        "log_loss": mean([r.log_loss for r in entries if r.log_loss is not None]) if probability else None,
        "brier_three_class": None,
        "two_way_n": len(two_way),
        "brier_two_way": mean([r.two_way_brier for r in entries if r.two_way_brier is not None]) if probability else None,
        "log_loss_two_way": mean([r.log_loss for r in entries if r.log_loss is not None]) if probability else None,
        "auc": _auc_of(two_way) if probability else None,
        "margin_mae": mean([abs(r.margin_error) for r in entries if r.margin_error is not None]),
        "total_mae": mean([abs(r.total_error) for r in entries if r.total_error is not None]),
        "team_score_mae": mean([(r.home_abs_error + r.away_abs_error) / 2 for r in entries
                                if r.home_abs_error is not None and r.away_abs_error is not None]),
        "crps_total": mean([r.crps_total for r in entries if r.crps_total is not None]),
        "crps_margin": mean([r.crps_margin for r in entries if r.crps_margin is not None]),
        "margin_errors": [r.margin_error for r in entries if r.margin_error is not None][-400:],
        "total_errors": [r.total_error for r in entries if r.total_error is not None][-400:],
        "market": None,
        "calibration": bins,
        "series": series,
    }


def _auc_of(two_way: list[tuple[Any, float, bool]]) -> float | None:
    from app.services.model_performance_read import _auc

    return _auc([(p, won) for _, p, won in two_way])


def _game(row: Any, teams: dict[str, dict[str, Any]]) -> dict[str, Any]:
    side = teams.get(str(row.canonical_game_id), {})
    return {
        "evaluation_id": str(row.id), "canonical_game_id": str(row.canonical_game_id),
        "start_utc": common.iso(row.start_utc), "home": (side.get("home") or {}).get("abbreviation"),
        "away": (side.get("away") or {}).get("abbreviation"), "final": {"home": row.home_score, "away": row.away_score},
        "home_probability_two_way": row.home_win_probability,
        "predicted_outcome": (None if row.home_win_probability is None else
                              "HOME" if row.home_win_probability > 0.5 else "AWAY"),
        "actual_outcome": "HOME" if row.home_score > row.away_score else "AWAY",
        "winner_correct": row.winner_correct, "log_loss": row.log_loss, "brier_three_class": None,
        "predicted_score": {"home": row.predicted_home, "away": row.predicted_away},
        "margin_error": row.margin_error, "total_error": row.total_error, "market_no_vig_home": None,
        "market_book": None,
        "publication_clock": ("HISTORICAL_NO_PUBLICATION_CLOCK" if row.evidence_class == EVIDENCE_HISTORICAL
                              else "PACKAGE_CLOCK"),
        "published_before_start_hours": None, "evidence_class": row.evidence_class, "channel": row.channel,
    }


# ----------------------------------------------------------- readiness ------

def _finals_lane(session: Session, settings: Any) -> dict[str, Any]:
    """The PRODUCTION final path's state: the pointer, the newest verified manifest, membership."""

    from app.models import NbaFinalDiscoveryManifest, NbaFinalObservation
    from app.models.nba_gen1 import LANE_EXCHANGE
    from app.nba import gen1_final_exchange as lane

    pointer = lane.pointer_path(settings)
    newest = session.scalar(select(NbaFinalDiscoveryManifest)
                            .order_by(NbaFinalDiscoveryManifest.first_verified_at.desc()).limit(1))
    rows = session.scalars(select(NbaFinalObservation).where(NbaFinalObservation.lane == LANE_EXCHANGE)).all()
    retained = [r for r in rows if not r.currently_source_confirmed]
    pending = [r for r in rows if r.reconciliation_required]
    return {
        "lane": LANE_EXCHANGE, "schema": common.FINAL_DISCOVERY_SCHEMA,
        "record_schema": common.FINAL_RECORD_SCHEMA,
        "pointer": str(pointer) if pointer else None,
        "state": ("MANIFEST_VERIFIED" if newest is not None else
                  "POINTER_PRESENT_NOT_YET_VERIFIED" if pointer and pointer.is_file() else
                  "NO_FINALS_DISCOVERY_POINTER"),
        "manifest_sha256": newest.manifest_sha256 if newest else None,
        "outcomes_current": newest.outcomes_current if newest else 0,
        "records_refused": newest.records_refused if newest else 0,
        "currently_source_confirmed": sum(1 for r in rows if r.currently_source_confirmed),
        "retained_ineligible": len(retained),
        "awaiting_reconciliation": [{"game_id": str(r.game_id), "codex_game_id": r.codex_game_id,
                                     "reason": r.reconciliation_reason} for r in pending][:20],
        "completion_clock": common.COMPLETION_CLOCK_BASIS,
        "detail": ("The source-confirmed finals exchange is the production final path. Retained ESPN season "
                   "exports remain a separate historical-reconstruction lane. No completion clock exists in "
                   "either lane; capture, validation and admission are never used as one."),
    }


def readiness(session: Session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Per-component truth for NBA. Each component states what is PRESENT and what is DUE, with dates."""

    from app.models import Game, League, NbaChannelEvaluation, NbaGen1Forecast, NbaHistoricalForecast
    from app.nba.gen1_handoff import production_status
    from app.nba.gen1_paper import bound_release, experiment, policy_grant

    moment = now or datetime.now(UTC)
    verdict = production_status(settings)
    upcoming = session.scalar(select(NbaGen1Forecast).where(NbaGen1Forecast.captured_tipoff_utc > moment)
                              .order_by(NbaGen1Forecast.captured_tipoff_utc).limit(1))
    next_game = session.scalar(select(Game).join(League, League.id == Game.league_id).where(
        League.code == common.LEAGUE_CODE, Game.starts_at > moment).order_by(Game.starts_at).limit(1))
    price = price_note(session, settings)
    # The grant is release-bound, so it is judged against the release the newest
    # held package binds -- not in the abstract.
    grant = policy_grant(settings, now=moment,
                         release=bound_release(upcoming) if upcoming is not None else None)
    era = experiment(session)
    historical = session.scalar(select(func.count()).select_from(NbaHistoricalForecast)) or 0
    scored_hist = session.scalar(select(func.count(func.distinct(NbaChannelEvaluation.canonical_game_id))).where(
        NbaChannelEvaluation.evidence_class == EVIDENCE_HISTORICAL)) or 0
    eligibility = (eligibility_for(session, upcoming.forecast_id, upcoming.canonical_game_id, now=moment)
                   if upcoming is not None and upcoming.canonical_game_id else None)
    opening = datetime(2026, 10, 20, tzinfo=UTC)
    return {
        "handoff": {"state": verdict.status, "detail": verdict.reason,
                    "handoff_sha256": verdict.detail.get("handoff_sha256")},
        "forecast": ({"state": "PRODUCTION_PACKAGE_HELD", "forecast_id": upcoming.forecast_id,
                      "tipoff_utc": common.iso(upcoming.captured_tipoff_utc),
                      "matchup": f"{upcoming.away_team_code} at {upcoming.home_team_code}"}
                     if upcoming is not None else
                     {"state": "NO_PRODUCTION_PACKAGE", "detail": "No NBA production package is imported."}),
        "eligibility": ({"state": (eligibility.get("now") or {}).get("state") or "UNKNOWN",
                         "detail": "V1 discovery has no lease; decision eligibility needs a V2 slate lease "
                                   "(first producer checkpoint 2026-10-13T19:00Z, NOT YET DUE)."}
                        if eligibility else {"state": "NO_FORECAST"}),
        "price": {"state": price["reason"], "detail": price.get("detail"),
                  "blocker": "SGO_LEAGUE_IDENTITY_UNVERIFIED" if not price.get("binding", {}).get("resolved") else None},
        "decision": {"state": ("OWNER_INACTIVE" if not getattr(settings, "nba_paper_decisions_enabled", False)
                               else "ACTIVE"),
                     "grant": grant["state"], "experiment": str(era.id) if era else None,
                     "detail": "AISP_NBA_PAPER_POLICY_V1 exists with its $1,000 virtual account; positions need a "
                               "release-bound NBA_RECURRING_PAPER_AUTHORIZATION_V1 grant (optional by contract; "
                               "without one: NO_PAPER_AUTHORIZATION) and NBA_PAPER_DECISIONS_ENABLED."},
        "outcomes": {"state": "HISTORICAL_SCORED" if scored_hist else "NONE_SCORED", "historical_rows": historical,
                     "historical_games_scored": scored_hist,
                     "prospective": f"NOT YET DUE — first 2026-27 final after {opening.date().isoformat()}"},
        "finals_lane": _finals_lane(session, settings),
        "next_game": ({"game_id": str(next_game.id), "starts_at": common.iso(next_game.starts_at)}
                      if next_game is not None else None),
    }


__all__ = ["CHANNEL_LABELS", "ENGINE_FOR_CHANNEL", "HISTORICAL_LABEL", "PRODUCTION_LABEL", "board", "channels",
           "eligibility_for", "game_detail", "model_for_game", "performance_models", "price_note", "readiness",
           "resolve_game"]
