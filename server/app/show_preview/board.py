"""Build the day-before Show Board from persisted evidence. Read-only.

Everything is cut at ``evidence_as_of``: a quote counts only if the sportsbook
observed it AND AISP had retrieved it by then; a preview forecast counts only
if it was created and its evidence cut off by then. Nothing after the cut can
leak into a board, which is what lets a past slate be reconstructed honestly
and what makes a frozen snapshot reproducible.

This module calls no provider, generates no forecast, evaluates no wagering
policy and writes nothing.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    Game, GameTeam, League, Market, MarketOutcome, OddsSnapshot, ProviderRequestLedger, Sportsbook, Team,
)
from app.models.show_preview import ShowPreviewForecast
from app.show_preview.clock import SHOW_SPORTS, clock_for, planned_freeze_utc
from app.show_preview.contract import (
    KICKOFF_TOLERANCE, SHOW_PREVIEW, lines_compatible, mandatory_disclosures, no_vig, preview_difference, settlement_rule,
)

BOARD_SCHEMA = "AISP_SHOW_BOARD_V1"
STATUS_LABEL = "SHOW PREVIEW — NOT FINAL"
DIFFERENCE_LABEL = "Preview model-market difference"
AUTHORITY = {
    "horizon": SHOW_PREVIEW,
    "research_only": True,
    "grants_wagering_authority": False,
    "creates_positions": False,
    "final_pregame_evidence": False,
    "statement": ("Show Preview research for the day-before recording. Not a final wagering decision, not a "
                  "governed position and not final-pregame evidence. Final decisions are made on game day by the "
                  "unchanged final-pregame workflow and may differ."),
}
# "Not posted" would claim something about the sportsbook that AISP cannot
# know: an absent row means AISP holds no capture (day-before collection is
# disabled), not that no book offered a line.
READINESS_LABEL = {
    "READY": "Forecast and market ready",
    "FORECAST_ONLY": "No current preview price held",
    "MARKET_ONLY": "Preview forecast pending",
    "NEITHER": "Preview forecast pending · No current preview price held",
}
PROVIDER_LABEL = {"SPORTSGAMEODDS": "SportsGameOdds", "THE_ODDS_API": "The Odds API"}
MIN = 60


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.astimezone(UTC).isoformat() if aware else None


def _classification(source: str, book_key: str) -> str:
    if book_key.startswith("sgo:") or source.upper() == "SPORTSGAMEODDS":
        return "RESEARCH"
    if source.upper() == "THE_ODDS_API":
        return "GOVERNED"
    return "UNCLASSIFIED"


def _market_type(raw: Any) -> str | None:
    value = str(getattr(raw, "value", raw)).split(".")[-1].upper()
    if value in ("MONEYLINE", "H2H"):
        return "MONEYLINE"
    if value in ("SPREAD", "RUN_LINE", "SPREADS"):
        return "SPREAD"
    if value in ("TOTAL", "TOTALS"):
        return "TOTAL"
    return None


def _side(outcome_key: str, participant: Any, teams: dict[str, Any]) -> str | None:
    if participant is not None:
        if teams.get("home") and participant == teams["home"]["team_id"]:
            return "HOME"
        if teams.get("away") and participant == teams["away"]["team_id"]:
            return "AWAY"
    key = (outcome_key or "").upper()
    for prefix in ("HOME", "AWAY", "OVER", "UNDER"):
        if key.startswith(prefix):
            return prefix
    return None


def _et_clock(value: datetime | None, tz: ZoneInfo) -> str:
    if value is None:
        return "—"
    return value.astimezone(tz).strftime("%-I:%M %p") if hasattr(value, "astimezone") else "—"


def _clock_text(value: datetime | None, tz: ZoneInfo) -> str:
    if value is None:
        return "—"
    local = value.astimezone(tz)
    return f"{local.strftime('%a')} {local.month}/{local.day} {int(local.strftime('%I'))}:{local.strftime('%M %p')} ET"


# ------------------------------------------------------------------ games ----

def _display_code(team: dict[str, Any] | None) -> str:
    """A team's short label. College Football teams admitted from a research
    package carry their CFBD id as the stored abbreviation (no abbreviation is
    invented), so their name is shown instead."""
    if not team:
        return "?"
    code = str(team.get("abbreviation") or "")
    return team.get("name") or code if code.startswith("CFBD") else code or "?"


def _slate_games(session: Session, slate_date: date, tz: ZoneInfo) -> list[dict[str, Any]]:
    lo = datetime.combine(slate_date, time.min, tz).astimezone(UTC)
    hi = lo + timedelta(days=1)
    rows = session.execute(
        select(Game, League.code).join(League, League.id == Game.league_id)
        .where(Game.starts_at >= lo, Game.starts_at < hi, League.code.in_(SHOW_SPORTS))
        .order_by(Game.starts_at, Game.id)
    ).all()
    ids = [game.id for game, _ in rows]
    teams: dict[Any, dict[str, Any]] = defaultdict(dict)
    if ids:
        for game_id, is_home, team_id, abbreviation, name in session.execute(
            select(GameTeam.game_id, GameTeam.is_home, Team.id, Team.abbreviation, Team.name)
            .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id.in_(ids))
        ).all():
            teams[game_id]["home" if is_home else "away"] = {"team_id": team_id, "abbreviation": abbreviation, "name": name}
    out = []
    for game, league in rows:
        side = teams.get(game.id, {})
        out.append({
            "game": game, "league": league, "teams": side,
            "away": _display_code(side.get("away")), "home": _display_code(side.get("home")),
        })
    return out


# ---------------------------------------------------------------- markets ----

def _quote_rows(session: Session, game_ids: list[Any], as_of: datetime) -> list[Any]:
    if not game_ids:
        return []
    return session.execute(
        select(
            Market.game_id, Market.id, Market.market_type, Market.period, MarketOutcome.id, MarketOutcome.outcome_key,
            MarketOutcome.participant_team_id, Sportsbook.key, Sportsbook.name, OddsSnapshot.id, OddsSnapshot.source,
            OddsSnapshot.observed_at, OddsSnapshot.available_as_of, OddsSnapshot.american_odds,
            OddsSnapshot.decimal_odds, OddsSnapshot.point,
        )
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id.in_(game_ids), OddsSnapshot.source_mode == "LIVE",
               OddsSnapshot.observed_at <= as_of, OddsSnapshot.available_as_of <= as_of)
        .order_by(OddsSnapshot.observed_at)
    ).all()


def _ledger_rows(session: Session, sources: set[str], lo: datetime | None, hi: datetime | None) -> list[Any]:
    if not sources or lo is None or hi is None:
        return []
    return session.execute(
        select(ProviderRequestLedger.id, ProviderRequestLedger.provider, ProviderRequestLedger.endpoint_key,
               ProviderRequestLedger.requested_at, ProviderRequestLedger.completed_at,
               ProviderRequestLedger.records_received)
        .where(ProviderRequestLedger.provider.in_(sorted(sources)), ProviderRequestLedger.status == "SUCCEEDED",
               ProviderRequestLedger.requested_at >= lo - timedelta(minutes=10),
               ProviderRequestLedger.requested_at <= hi + timedelta(minutes=10))
    ).all()


def _source_request(ledger: list[Any], source: str, retrieved: datetime) -> dict[str, Any] | None:
    best = None
    for row in ledger:
        if str(row[1]).upper() != source.upper():
            continue
        start = _aware(row[3])
        end = _aware(row[4]) or start
        if start is None or not (start - timedelta(minutes=2) <= retrieved <= end + timedelta(minutes=2)):
            continue
        gap = abs((retrieved - start).total_seconds())
        if best is None or gap < best[0]:
            best = (gap, row)
    if best is None:
        return None
    row = best[1]
    return {"ledger_id": str(row[0]), "endpoint_key": row[2], "requested_utc": _iso(row[3]),
            "objects_in_request": row[5], "match": "RETRIEVAL_TIME_WINDOW",
            "note": "Matched by retrieval time; retained snapshots do not store their request id."}


def _pairs_for_game(sport: str, teams: dict[str, Any], rows: list[Any], as_of: datetime, max_age: timedelta,
                    ledger: list[Any]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    latest: dict[tuple, Any] = {}
    first: dict[tuple, Any] = {}
    for row in rows:
        (_, market_id, market_type, period, outcome_id, outcome_key, participant, book_key, book_name, snap_id,
         source, observed, retrieved, american, decimal, point) = row
        if period and str(period).split(".")[-1].lower() not in ("full_game", "full game"):
            continue
        kind = _market_type(market_type)
        side = _side(outcome_key, participant, teams)
        if kind is None or side is None:
            continue
        key = (kind, market_id, book_key, str(source), side)
        latest[key] = row  # rows are ascending by observation time
        first.setdefault(key, row)

    grouped: dict[tuple, dict[str, Any]] = defaultdict(dict)
    for (kind, market_id, book_key, source, side), row in latest.items():
        grouped[(kind, market_id, book_key, source)][side] = row
    movement_first: dict[tuple, dict[str, Any]] = defaultdict(dict)
    for (kind, market_id, book_key, source, side), row in first.items():
        movement_first[(kind, market_id, book_key, source)][side] = row

    markets: dict[str, list[dict[str, Any]]] = {"MONEYLINE": [], "SPREAD": [], "TOTAL": []}
    movements: list[dict[str, Any]] = []
    for (kind, market_id, book_key, source), sides in grouped.items():
        expected = ("OVER", "UNDER") if kind == "TOTAL" else ("HOME", "AWAY")
        legs = []
        for side in expected:
            if side not in sides:
                continue
            row = sides[side]
            observed, retrieved = _aware(row[11]), _aware(row[12])
            legs.append({"side": side, "american": row[13], "decimal": round(float(row[14]), 6) if row[14] else None,
                         "point": row[15], "observed_utc": _iso(observed), "retrieved_utc": _iso(retrieved),
                         "snapshot_id": str(row[9]), "outcome_key": row[5]})
        by_side = {leg["side"]: leg for leg in legs}
        complete = (len(legs) == 2 and all((leg["decimal"] or 0) > 1 for leg in legs)
                    and lines_compatible(kind, by_side))
        observed_times = [datetime.fromisoformat(leg["observed_utc"]) for leg in legs]
        older = min(observed_times) if observed_times else None
        age = (as_of - older).total_seconds() / MIN if older else None
        line = None
        if kind == "TOTAL":
            line = by_side.get("OVER", {}).get("point")
        elif kind == "SPREAD":
            line = by_side.get("HOME", {}).get("point")
        retrieved_latest = max((datetime.fromisoformat(leg["retrieved_utc"]) for leg in legs), default=None)
        pair = {
            "market_type": kind, "sportsbook": str(sides[next(iter(sides))][8]), "sportsbook_key": book_key,
            "provider": PROVIDER_LABEL.get(source.upper(), source), "provider_code": source,
            "classification": _classification(source, book_key),
            "line": line, "line_key": "ML" if kind == "MONEYLINE" else f"{line}",
            "complete": complete,
            "missing_side": [side for side in expected if side not in by_side],
            "stale": bool(age is not None and age > max_age.total_seconds() / MIN),
            "older_leg_observed_utc": _iso(older), "older_leg_age_minutes": round(age, 1) if age is not None else None,
            "retrieved_utc": _iso(retrieved_latest),
            "settlement": settlement_rule(sport, kind, line),
            "legs": legs,
            "source_request": _source_request(ledger, source, retrieved_latest) if retrieved_latest else None,
            "no_vig_home": None,
        }
        if complete and kind == "MONEYLINE":
            pair["no_vig_home"] = round(no_vig(by_side["HOME"]["decimal"], by_side["AWAY"]["decimal"]), 6)
            earliest = movement_first.get((kind, market_id, book_key, source), {})
            if "HOME" in earliest and "AWAY" in earliest and earliest["HOME"][9] != by_side["HOME"]["snapshot_id"]:
                start = no_vig(float(earliest["HOME"][14]), float(earliest["AWAY"][14]))
                movements.append({"sportsbook": pair["sportsbook"], "from_no_vig_home": round(start, 6),
                                  "to_no_vig_home": pair["no_vig_home"],
                                  "from_utc": _iso(min(_aware(earliest["HOME"][11]), _aware(earliest["AWAY"][11]))),
                                  "to_utc": pair["older_leg_observed_utc"]})
        markets[kind].append(pair)
    for kind in markets:
        markets[kind].sort(key=lambda p: (not p["complete"], p["sportsbook"], p["provider"], str(p["line"])))
    return markets, movements


def _moneyline_summary(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [p for p in pairs if p["complete"] and not p["stale"]]
    if not pairs:
        return None
    best: dict[str, Any] = {}
    for side in ("HOME", "AWAY"):
        top = None
        for pair in usable:
            leg = next(leg for leg in pair["legs"] if leg["side"] == side)
            if top is None or leg["decimal"] > top[1]["decimal"]:
                top = (pair, leg)
        if top:
            best[side] = {"american": top[1]["american"], "decimal": top[1]["decimal"], "sportsbook": top[0]["sportsbook"],
                          "provider": top[0]["provider"], "classification": top[0]["classification"],
                          "observed_utc": top[0]["older_leg_observed_utc"]}
    values = [p["no_vig_home"] for p in usable]
    return {
        "books": len({p["sportsbook_key"] for p in pairs}),
        "complete_books": len({p["sportsbook_key"] for p in usable}),
        "stale_books": len({p["sportsbook_key"] for p in pairs if p["complete"] and p["stale"]}),
        "partial_books": len({p["sportsbook_key"] for p in pairs if not p["complete"]}),
        "consensus_no_vig_home": round(sum(values) / len(values), 6) if values else None,
        "range_no_vig_home": [min(values), max(values)] if values else None,
        "best": best,
        "captured_utc": max((p["older_leg_observed_utc"] for p in usable), default=None),
    }


def _line_summary(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [p for p in pairs if p["complete"] and not p["stale"] and p["line"] is not None]
    if not usable:
        return None
    lines = [float(p["line"]) for p in usable]
    return {"median_line": median(lines), "range": [min(lines), max(lines)], "books": len({p["sportsbook_key"] for p in usable}),
            "captured_utc": max(p["older_leg_observed_utc"] for p in usable)}


# --------------------------------------------------------------- forecasts ---

def _forecasts(session: Session, slate_date: date, game_ids: list[Any], as_of: datetime) -> list[ShowPreviewForecast]:
    clauses = [ShowPreviewForecast.slate_date == slate_date]
    if game_ids:
        clauses.append(ShowPreviewForecast.canonical_game_id.in_(game_ids))
    return list(session.scalars(
        select(ShowPreviewForecast)
        .where(or_(*clauses), and_(ShowPreviewForecast.horizon == SHOW_PREVIEW,
                                   ShowPreviewForecast.created_utc <= as_of,
                                   ShowPreviewForecast.source_cutoff_utc <= as_of,
                                   ShowPreviewForecast.grants_wagering_authority.is_(False)))
        .order_by(ShowPreviewForecast.created_utc)
    ))


def _forecast_view(row: ShowPreviewForecast) -> dict[str, Any]:
    payload = row.payload or {}
    lean = "HOME" if row.home_win_probability >= row.away_win_probability else "AWAY"
    two_way = row.home_win_probability + row.away_win_probability
    lean_p = (row.home_win_probability if lean == "HOME" else row.away_win_probability) / two_way if two_way else None
    return {
        "row_id": str(row.id), "forecast_id": row.forecast_id, "content_sha256": row.content_sha256,
        "schema_version": row.schema_version, "horizon": row.horizon,
        "model_id": row.model_id, "model_version": row.model_version, "artifact_sha256": row.artifact_sha256,
        "created_utc": _iso(row.created_utc), "source_cutoff_utc": _iso(row.source_cutoff_utc),
        "expected_score": {"away": round(row.expected_away_score, 3), "home": round(row.expected_home_score, 3)},
        "projected_margin_home": round(row.expected_home_score - row.expected_away_score, 3),
        "projected_total": round(row.expected_home_score + row.expected_away_score, 3),
        "probabilities": {"away_win": row.away_win_probability, "home_win": row.home_win_probability, "tie": row.tie_probability},
        "lean": {"side": lean, "team": row.home_abbreviation if lean == "HOME" else row.away_abbreviation,
                 "probability_two_way": round(lean_p, 6) if lean_p is not None else None},
        "starters": row.starter_status, "lineups": row.lineup_status,
        "distribution": payload.get("distribution"),
        "missing_evidence": payload.get("missing_evidence") or [],
    }


# ------------------------------------------------------------------ board ----

def build_board(session: Session, slate_date: date, settings: Settings, *, evidence_as_of: datetime) -> dict[str, Any]:
    tz = ZoneInfo(settings.show_preview_timezone)
    as_of = evidence_as_of.astimezone(UTC)
    max_age = timedelta(hours=settings.show_preview_quote_max_age_hours)
    slate = _slate_games(session, slate_date, tz)
    game_ids = [entry["game"].id for entry in slate]
    rows = _quote_rows(session, game_ids, as_of)
    rows_by_game: dict[Any, list[Any]] = defaultdict(list)
    for row in rows:
        rows_by_game[row[0]].append(row)
    retrieved = [_aware(row[12]) for row in rows if row[12] is not None]
    ledger = _ledger_rows(session, {str(row[10]).upper() for row in rows}, min(retrieved, default=None), max(retrieved, default=None))

    forecasts = _forecasts(session, slate_date, game_ids, as_of)
    by_game: dict[str, ShowPreviewForecast] = {}
    unscheduled: dict[str, ShowPreviewForecast] = {}
    for row in forecasts:  # ascending by creation: the latest wins
        if row.canonical_game_id is not None and row.canonical_game_id in game_ids:
            by_game[str(row.canonical_game_id)] = row
            continue
        match = next((e for e in slate if e["league"] == row.sport and e["away"] == row.away_abbreviation
                      and e["home"] == row.home_abbreviation
                      and abs(_aware(e["game"].starts_at) - _aware(row.kickoff_utc)) <= KICKOFF_TOLERANCE), None)
        if match is not None:
            by_game[str(match["game"].id)] = row
        elif row.slate_date == slate_date:
            unscheduled[f"{row.sport}:{row.source_game_ref}"] = row

    games: list[dict[str, Any]] = []
    disclosures: list[dict[str, Any]] = []
    all_movements: list[tuple[str, dict[str, Any]]] = []

    def add_game(key: str, sport: str, entry: dict[str, Any] | None, forecast: ShowPreviewForecast | None) -> None:
        game = entry["game"] if entry else None
        away = entry["away"] if entry else forecast.away_abbreviation
        home = entry["home"] if entry else forecast.home_abbreviation
        teams = entry["teams"] if entry else {}
        start = _aware(game.starts_at) if game else _aware(forecast.kickoff_utc)
        markets, movements = (_pairs_for_game(sport, teams, rows_by_game.get(game.id, []), as_of, max_age, ledger)
                              if game else ({"MONEYLINE": [], "SPREAD": [], "TOTAL": []}, []))
        for movement in movements:
            all_movements.append((key, movement))
        ml = _moneyline_summary(markets["MONEYLINE"])
        view = _forecast_view(forecast) if forecast else None
        missing: list[dict[str, str]] = []
        if view is None:
            missing.append({"code": "PREVIEW_FORECAST_AWAITING_PRODUCER", "message": "Preview forecast awaiting producer"})
        else:
            for item in view["missing_evidence"]:
                missing.append({"code": str(item.get("code", "PRODUCER_DISCLOSURE")), "message": str(item.get("message", ""))})
            if view["starters"] == "PROBABLE":
                missing.append({"code": "PROBABLE_STARTERS", "message": "Starters are probable, not confirmed"})
            if view["lineups"] in ("PROBABLE", "UNKNOWN"):
                missing.append({"code": "LINEUPS_NOT_CONFIRMED", "message": "Lineups are not confirmed"})
        if game is None:
            missing.append({"code": "SCHEDULE_NOT_REGISTERED", "message": "Game is not in AISP's registered schedule; no market can be bound"})
        if not markets["MONEYLINE"]:
            missing.append({"code": "PRICE_NOT_CAPTURED", "message": (
                "No preview moneyline held: AISP captured none for this game before the evidence cut. "
                "This is AISP's capture state, not a statement that no sportsbook posted a line")})
        elif ml and ml["complete_books"] == 0:
            if ml["stale_books"]:
                missing.append({"code": "STALE_PREVIEW_PRICE", "message": "Only stale preview moneyline prices were captured"})
            if ml["partial_books"]:
                missing.append({"code": "PARTIAL_MARKET", "message": "Only one side of the moneyline was captured"})
        if not any(p["complete"] for p in markets["SPREAD"]):
            missing.append({"code": "NO_COMPLETE_SPREAD", "message": "No complete spread or run line"})
        if not any(p["complete"] for p in markets["TOTAL"]):
            missing.append({"code": "NO_COMPLETE_TOTAL", "message": "No complete total"})

        comparison = None
        if view is not None and ml and ml["complete_books"]:
            candidates = []
            for side in ("HOME", "AWAY"):
                best = None
                for pair in markets["MONEYLINE"]:
                    if not pair["complete"] or pair["stale"]:
                        continue
                    legs = {leg["side"]: leg for leg in pair["legs"]}
                    if best is None or legs[side]["decimal"] > best[1][side]["decimal"]:
                        best = (pair, legs)
                if best is None:
                    continue
                pair, legs = best
                other = "AWAY" if side == "HOME" else "HOME"
                numbers = preview_difference(side=side, probabilities=view["probabilities"],
                                             decimal_side=legs[side]["decimal"], decimal_other=legs[other]["decimal"],
                                             settlement=pair["settlement"])
                candidates.append({
                    "label": DIFFERENCE_LABEL, "selection": side, "team": home if side == "HOME" else away,
                    **numbers,
                    "american": legs[side]["american"], "decimal": legs[side]["decimal"],
                    "opposite_american": legs[other]["american"],
                    "sportsbook": pair["sportsbook"], "provider": pair["provider"], "classification": pair["classification"],
                    "settlement": pair["settlement"], "price_observed_utc": pair["older_leg_observed_utc"],
                    "price_retrieved_utc": pair["retrieved_utc"], "forecast_created_utc": view["created_utc"],
                })
            if candidates:
                candidates.sort(key=lambda c: (-c["difference"], -c["expected_return"]))
                comparison = {**candidates[0], "other_side": candidates[1] if len(candidates) > 1 else None}

        # Consumer-side obligations, attached wherever the forecast is shown
        # rather than left to whatever the producer chose to declare. Today this
        # is empty for every row -- no genuine MLB preview package exists -- and
        # it is written now so the first one that arrives cannot be displayed
        # without them.
        model_disclosures = mandatory_disclosures(sport, view)

        has_forecast = view is not None
        has_market = bool(ml and ml["complete_books"])
        readiness = "READY" if has_forecast and has_market else "FORECAST_ONLY" if has_forecast else "MARKET_ONLY" if has_market else "NEITHER"
        status = str(getattr(game.status, "value", game.status)).split(".")[-1].upper() if game else "UNREGISTERED"
        record = {
            "game_key": key, "canonical_game_id": str(game.id) if game else None, "sport": sport, "league": sport,
            "matchup": f"{away} @ {home}",
            "away": {"abbreviation": away, "name": (teams.get("away") or {}).get("name") or away},
            "home": {"abbreviation": home, "name": (teams.get("home") or {}).get("name") or home},
            "kickoff_utc": _iso(start), "status": status,
            "final_decision_window": {"opens_utc": _iso(start - timedelta(minutes=30)), "closes_utc": _iso(start - timedelta(minutes=15))} if start else None,
            "readiness": readiness,
            "readiness_label": (
                "Forecast held · not in AISP schedule, so no price can be bound"
                if game is None and readiness == "FORECAST_ONLY"
                else READINESS_LABEL[readiness]
            ),
            "forecast_status": "AVAILABLE" if has_forecast else "AWAITING_PRODUCER",
            "forecast": view,
            "disclosures": model_disclosures,
            "markets": markets,
            "market_summary": {"moneyline": ml, "spread": _line_summary(markets["SPREAD"]), "total": _line_summary(markets["TOTAL"])},
            "comparison": comparison,
            "rff": {"supported": False, "note": "RFF is not computed at the preview horizon: its inputs have no evidence-time cut."},
            "missing": missing,
        }
        games.append(record)
        for item in missing:
            disclosures.append({"game_key": key, **item})
        # The model's own limitations belong on the board's disclosure list too,
        # not only on the row: the print view and the rundown read from here.
        for item in model_disclosures:
            disclosures.append({"game_key": key, **item})

    for entry in slate:
        add_game(str(entry["game"].id), entry["league"], entry, by_game.get(str(entry["game"].id)))
    for key, row in sorted(unscheduled.items()):
        add_game(key, row.sport, None, row)
    games.sort(key=lambda g: (SHOW_SPORTS.index(g["sport"]) if g["sport"] in SHOW_SPORTS else 9, g["kickoff_utc"] or "", g["matchup"]))

    ranked = sorted((g for g in games if g["comparison"] and g["comparison"]["difference"] > 0),
                    key=lambda g: (-g["comparison"]["difference"], -g["comparison"]["expected_return"], g["kickoff_utc"] or "", g["game_key"]))
    ranking = [{"rank": index + 1, "game_key": g["game_key"], "difference": g["comparison"]["difference"],
                "expected_return": g["comparison"]["expected_return"]} for index, g in enumerate(ranked)]

    readiness = {
        "games": len(games),
        "preview_forecasts": sum(1 for g in games if g["forecast"]),
        "complete_markets": sum(1 for g in games if (g["market_summary"]["moneyline"] or {}).get("complete_books")),
        "both": sum(1 for g in games if g["readiness"] == "READY"),
        "missing_evidence_games": sum(1 for g in games if g["readiness"] != "READY"),
        "valid_comparisons": len(ranking),
        "by_sport": {sport: sum(1 for g in games if g["sport"] == sport) for sport in SHOW_SPORTS},
    }
    if not slate:
        disclosures.append({"game_key": None, "code": "NO_REGISTERED_GAMES", "message": "No game is registered for this slate date"})
    disclosures.append({"game_key": None, "code": "PREVIEW_COLLECTION_DISABLED",
                        "message": "Day-before sportsbook collection is not enabled; markets shown are those already persisted"})

    return {
        "schema_version": BOARD_SCHEMA,
        "horizon": SHOW_PREVIEW,
        "status_label": STATUS_LABEL,
        "slate_date": slate_date.isoformat(),
        "timezone": settings.show_preview_timezone,
        "planned_freeze_utc": _iso(planned_freeze_utc(slate_date, settings)),
        "evidence_as_of_utc": _iso(as_of),
        "authority": AUTHORITY,
        "readiness": readiness,
        "games": games,
        "ranking": ranking,
        "talking_points": talking_points(games, ranking, all_movements, tz),
        "disclosures": disclosures,
        "clock": clock_for(slate_date, settings),
    }


def talking_points(games: list[dict[str, Any]], ranking: list[dict[str, Any]], movements: list[tuple[str, dict[str, Any]]],
                   tz: ZoneInfo) -> list[dict[str, Any]]:
    """Deterministic facts from stored evidence only. No narrative, no players, no weather."""

    by_key = {g["game_key"]: g for g in games}
    points: list[dict[str, Any]] = []
    pct = lambda v: f"{v * 100:.1f}%"  # noqa: E731
    if ranking:
        g = by_key[ranking[0]["game_key"]]
        c = g["comparison"]
        points.append({"code": "STRONGEST_DISAGREEMENT", "game_key": g["game_key"],
                       "text": (f"Largest preview model-market difference: {c['team']} moneyline in {g['matchup']}, "
                                f"{c['difference'] * 100:+.1f} pts ({pct(c['model_probability'])} model vs "
                                f"{pct(c['no_vig_probability'])} no-vig at {c['sportsbook']}, price captured "
                                f"{_clock_text(datetime.fromisoformat(c['price_observed_utc']), tz)}).")})
    with_forecast = [g for g in games if g["forecast"]]
    if with_forecast:
        closest = min(with_forecast, key=lambda g: (abs(g["forecast"]["projected_margin_home"]), g["kickoff_utc"] or ""))
        f = closest["forecast"]
        points.append({"code": "CLOSEST_PROJECTED", "game_key": closest["game_key"],
                       "text": (f"Closest projected game: {closest['matchup']}, {closest['away']['abbreviation']} "
                                f"{f['expected_score']['away']:.1f} – {f['expected_score']['home']:.1f} {closest['home']['abbreviation']}.")})
        high = max(with_forecast, key=lambda g: (g["forecast"]["projected_total"], g["game_key"]))
        points.append({"code": "HIGHEST_PROJECTED_TOTAL", "game_key": high["game_key"],
                       "text": f"Highest projected total: {high['matchup']}, {high['forecast']['projected_total']:.1f}."})
        wide = max(with_forecast, key=lambda g: (abs(g["forecast"]["projected_margin_home"]), g["game_key"]))
        margin = wide["forecast"]["projected_margin_home"]
        leader = wide["home"]["abbreviation"] if margin >= 0 else wide["away"]["abbreviation"]
        points.append({"code": "LARGEST_PROJECTED_MARGIN", "game_key": wide["game_key"],
                       "text": f"Largest projected margin: {leader} by {abs(margin):.1f} in {wide['matchup']}."})
    ranged = [g for g in games if (g["market_summary"]["moneyline"] or {}).get("range_no_vig_home")
              and g["market_summary"]["moneyline"]["complete_books"] >= 2]
    if ranged:
        widest = max(ranged, key=lambda g: (g["market_summary"]["moneyline"]["range_no_vig_home"][1] - g["market_summary"]["moneyline"]["range_no_vig_home"][0], g["game_key"]))
        lo, hi = widest["market_summary"]["moneyline"]["range_no_vig_home"]
        points.append({"code": "LARGEST_CROSS_BOOK_RANGE", "game_key": widest["game_key"],
                       "text": (f"Widest cross-book range: {widest['matchup']}, {widest['home']['abbreviation']} no-vig "
                                f"{pct(lo)} to {pct(hi)} across {widest['market_summary']['moneyline']['complete_books']} books.")})
    meaningful = [(key, m) for key, m in movements if abs(m["to_no_vig_home"] - m["from_no_vig_home"]) >= 0.02]
    if meaningful:
        key, m = max(meaningful, key=lambda item: (abs(item[1]["to_no_vig_home"] - item[1]["from_no_vig_home"]), item[0]))
        g = by_key[key]
        points.append({"code": "LINE_MOVEMENT", "game_key": key,
                       "text": (f"Line movement: {g['matchup']} at {m['sportsbook']}, {g['home']['abbreviation']} no-vig "
                                f"{pct(m['from_no_vig_home'])} → {pct(m['to_no_vig_home'])} "
                                f"({_clock_text(datetime.fromisoformat(m['from_utc']), tz)} to "
                                f"{_clock_text(datetime.fromisoformat(m['to_utc']), tz)}).")})
    unposted = [g for g in games if not (g["market_summary"]["moneyline"] or {}).get("complete_books")]
    if games and unposted:
        points.append({"code": "MISSING_MARKETS", "game_key": None,
                       "text": f"{len(unposted)} of {len(games)} games have no complete preview moneyline yet."})
    return points
