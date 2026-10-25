"""Read models behind the command center.

Two views the redesigned pages chart directly:

``experiment_ledger``
    Every funded sport account, the positions recorded against its funded
    eras, and the realized bankroll path those settlements produce. The path
    is the account's own (contribution plus cumulative settled P&L), not an
    individual experiment's chain, so it reconciles to the account figure the
    readiness panel reports.

``market_coverage``
    For one Eastern slate date, every game in every league with the latest
    persisted two-sided sportsbook quote per book and market. Price time of a
    pair is its OLDER side; retrieval time is never substituted. Sides are
    only paired within one book and one provider. A game with no persisted
    quote is listed with an explicit NOT_COLLECTED state and the next
    collection checkpoint, so the page is never blank.

Neither function requests a price, evaluates a policy or writes a row. Model
output is reported beside a quote, never inside one.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from app.services.edge_metrics import edge_metrics
from sqlalchemy.orm import Session

ET = ZoneInfo("America/New_York")
LEDGER_SCHEMA = "AISP_COMMAND_CENTER_LEDGER_V1"
COVERAGE_SCHEMA = "AISP_COMMAND_CENTER_MARKET_COVERAGE_V1"
#: Every funded sport account the ledger reports. Hockey joined on 2026-10-16
#: (AISP_NHL_PAPER_POLICY_V1, era 658e9bb2, its own $1,000 season funding), so
#: its era, positions and settlements are read exactly like the other four.
#: The ledger is a read model only: listing a sport here changes no decision,
#: stake or settlement.
SPORTS = ("MLB", "NFL", "NBA", "CFB", "NHL")
#: Leagues whose games carry governed price collection and decision windows.
PRICED_LEAGUES = ("MLB", "NFL")
#: Leagues listed on the slate surfaces. College Football is listed because it
#: has a paper decision checkpoint (AISP_CFB_PAPER_POLICY_V1); its collection
#: time is The Odds API CFB lane's buy-window opening (T-75) while that lane is
#: on, and null with the reason (``price_unavailable``) otherwise. Hockey is listed
#: since its paper era opened (2026-10-16): the forecast the checkpoint would
#: hold (``_nhl_coverage``) and The Odds API NHL lane's T-75 collection time.
LISTED_LEAGUES = ("MLB", "NFL", "CFB", "NBA", "NHL")
#: NBA is listed since the Gen1 consumer (2026-09-19): its games carry verified
#: production packages (three separately labelled channels) or, for 2025-26,
#: HISTORICAL reconstruction rows. Like CFB it has no collection checkpoint, so
#: its collection time is null and its price state names why.
SPORT_LABELS = {"MLB": "MLB", "NFL": "NFL", "NBA": "NBA", "CFB": "College Football", "NHL": "NHL"}
QUOTE_FRESHNESS_MINUTES = 240
WINDOW_OPENS = timedelta(minutes=30)
WINDOW_CLOSES = timedelta(minutes=15)
CONTROLLING_ENGINES = {"MLB": "MLB_V18_WHOLE_GAME", "NFL": "NFL_M1_XGBOOST"}


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _iso(moment: datetime | None) -> str | None:
    aware = _aware(moment)
    return aware.astimezone(UTC).isoformat() if aware else None


def _american(decimal_odds: float | None) -> int | None:
    if decimal_odds is None or decimal_odds <= 1:
        return None
    if decimal_odds >= 2:
        return int(round((decimal_odds - 1) * 100))
    return int(round(-100 / (decimal_odds - 1)))


def _provider_label(source: str | None) -> str | None:
    return {"THE_ODDS_API": "The Odds API", "SPORTSGAMEODDS": "SportsGameOdds"}.get(
        (source or "").upper(), source)


def _classification(source: str | None, book_key: str | None) -> str:
    if (book_key or "").startswith("sgo:") or (source or "").upper() == "SPORTSGAMEODDS":
        return "RESEARCH"
    if (source or "").upper() == "THE_ODDS_API":
        return "GOVERNED"
    return "UNCLASSIFIED"


def _matchups(session: Session, game_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not game_ids:
        return {}
    rows = session.execute(text("""
        select gt.game_id, gt.is_home, t.id, coalesce(t.abbreviation, t.name), t.name
        from game_teams gt join teams t on t.id = gt.team_id
        where gt.game_id = any(cast(:ids as uuid[]))"""), {"ids": game_ids}).all()
    out: dict[str, dict[str, Any]] = defaultdict(dict)
    for game_id, is_home, team_id, abbreviation, name in rows:
        # College Football teams carry their CFBD id as a code (no abbreviation
        # is invented); the school name is what a reader should see.
        if abbreviation and str(abbreviation).startswith("CFBD") and str(abbreviation)[4:].isdigit():
            abbreviation = name
        out[str(game_id)]["home" if is_home else "away"] = {
            "team_id": str(team_id), "abbreviation": abbreviation, "name": name}
    for value in out.values():
        away = (value.get("away") or {}).get("abbreviation", "?")
        home = (value.get("home") or {}).get("abbreviation", "?")
        value["label"] = f"{away} @ {home}"
    return out


# ---------------------------------------------------------------- ledger ---

def _uuid_key(value: Any) -> str:
    """One spelling of a UUID whatever the driver returned (UUID, dashed or SQLite hex text)."""

    import uuid

    return str(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))


def _corrections(session: Session, position_ids: list[Any]) -> dict[str, Any]:
    """{position id (dashed): EffectiveSettlement} for the corrected positions among these."""

    import uuid

    if not position_ids:
        return {}
    from app.cfb.settlement_corrections import effective_settlements

    ids = [uuid.UUID(_uuid_key(value)) for value in position_ids]
    return {_uuid_key(key): value for key, value in effective_settlements(session, ids).items()}


def experiment_ledger(session: Session, now: datetime | None = None) -> dict[str, Any]:
    from app.services.sport_readiness import _account

    now = now or datetime.now(UTC)
    sports: list[dict[str, Any]] = []
    combined_events: list[dict[str, Any]] = []
    for sport in SPORTS:
        account = _account(session, sport)
        rows = session.execute(text("""
            select p.id, p.placed_at, p.status, p.result, p.stake, p.decimal_odds,
                   pr.profit_loss, pr.settled_at, pr.result,
                   mk.game_id, mk.market_type, mo.label, mo.outcome_key,
                   sb.name, sb.key, os.source, os.american_odds, os.observed_at,
                   g.starts_at, e.name, e.id
            from official_asp_positions p
            join shadow_experiments e on e.id = p.shadow_experiment_id
            join experiment_account_memberships m on m.experiment_id = e.id and m.role = 'FUNDED_ERA'
            join experiment_accounts a on a.id = m.account_id and a.sport = :s
            join market_outcomes mo on mo.id = p.market_outcome_id
            join markets mk on mk.id = mo.market_id
            join games g on g.id = mk.game_id
            left join position_results pr on pr.position_id = p.id
            left join odds_snapshots os on os.id = p.odds_snapshot_id
            left join sportsbooks sb on sb.id = os.sportsbook_id
            order by coalesce(pr.settled_at, p.placed_at), p.placed_at, p.id"""), {"s": sport}).all()
        matchups = _matchups(session, sorted({str(r[9]) for r in rows}))
        # Corrections recorded beside a settlement (position_settlement_adjustments):
        # a corrected position shows its corrected result and P&L, and the
        # series gains an ADJUSTMENT point when the correction was recorded.
        # None exist today, so the payload is unchanged until one does.
        corrected = _corrections(session, [r[0] for r in rows if r[6] is not None and r[7] is not None])
        positions: list[dict[str, Any]] = []
        for (pid, placed_at, status, result, stake, dec, pnl, settled_at, pr_result, game_id,
             market_type, label, outcome_key, book_name, book_key, source, american, observed_at,
             starts_at, exp_name, exp_id) in rows:
            settled = pnl is not None and settled_at is not None
            teams = matchups.get(str(game_id), {})
            side = (outcome_key or "").split(":")[0].upper()
            if (not label or label.upper() in ("HOME", "AWAY")) and side in ("HOME", "AWAY"):
                label = f"{(teams.get(side.lower()) or {}).get('abbreviation', side)} ML"
            positions.append({
                "position_id": str(pid), "game_id": str(game_id),
                "matchup": matchups.get(str(game_id), {}).get("label"),
                "starts_at": _iso(starts_at),
                "market": str(market_type).split(".")[-1],
                "selection": label or outcome_key,
                "entry": {
                    "american": american if american is not None else _american(dec),
                    "decimal": dec, "sportsbook": book_name, "sportsbook_key": book_key,
                    "provider": _provider_label(source),
                    "classification": _classification(source, book_key),
                    "quote_observed_at": _iso(observed_at),
                },
                "stake": float(stake) if stake is not None else None,
                "placed_at": _iso(placed_at),
                "status": "SETTLED" if settled else str(status).upper(),
                "result": str(pr_result or result or "").upper() or None,
                "profit_loss": float(pnl) if pnl is not None else None,
                "settled_at": _iso(settled_at),
                "experiment": {"id": str(exp_id), "name": exp_name},
            })
            fix = corrected.get(_uuid_key(pid)) if settled else None
            if fix is not None:
                positions[-1].update(result=fix.result, profit_loss=fix.profit_loss, **fix.as_dict())

        contribution = float(account.get("contribution") or 0) if account.get("funded") else 0.0
        series = [{"at": _iso(datetime.combine(date.fromisoformat(account["effective_on"]),
                                               datetime.min.time(), ET))
                   if account.get("effective_on") else None,
                   "bankroll": round(contribution, 2), "kind": "FUNDING", "position_id": None}]
        running = contribution
        events = [(position["settled_at"], "SETTLEMENT", position["position_id"],
                   position.get("original_profit_loss", position["profit_loss"]))
                  for position in positions if position["status"] == "SETTLED"]
        own_id = {_uuid_key(position["position_id"]): position["position_id"] for position in positions}
        adjustments = [(_iso(at), "ADJUSTMENT", own_id.get(pid, pid), delta)
                       for pid, fix in corrected.items() for at, delta, _result in fix.events]
        if adjustments:
            # Stable: settlements keep their ledger order; each correction lands
            # at the moment it was recorded (UTC ISO strings sort by time).
            events = sorted(events + adjustments, key=lambda event: event[0] or "")
        for at, kind, position_id, change in events:
            running += change or 0.0
            series.append({"at": at, "bankroll": round(running, 2),
                           "kind": kind, "position_id": position_id,
                           "change": change})
            combined_events.append({"sport": sport, **series[-1]})
        settled = [p for p in positions if p["status"] == "SETTLED"]
        open_positions = [p for p in positions if p["status"] != "SETTLED"]
        record = {k: sum(1 for p in settled if (p["result"] or "").startswith(k))
                  for k in ("WIN", "LOSS", "PUSH")}
        sports.append({
            "sport": sport, "label": SPORT_LABELS[sport], "account": account,
            "starting_capital": round(contribution, 2),
            "realized_bankroll": round(running, 2),
            "profit_loss": round(running - contribution, 2),
            "open_exposure": round(sum(p["stake"] or 0 for p in open_positions), 2),
            "available_capital": round(running - sum(p["stake"] or 0 for p in open_positions), 2),
            "open_count": len(open_positions), "settled_count": len(settled),
            "record": record, "positions": positions, "series": series,
            # Reconciles the path above with the readiness account figure.
            "reconciled": abs(round(running, 2) - float(account.get("realized_bankroll") or contribution)) < 0.011
            if account.get("funded") else None,
        })

    starting = round(sum(s["starting_capital"] for s in sports), 2)
    combined_events.sort(key=lambda e: e["at"] or "")
    running = starting
    combined = [{"at": min((s["series"][0]["at"] for s in sports if s["series"][0]["at"]), default=None),
                 "bankroll": starting, "kind": "FUNDING", "position_id": None}]
    for event in combined_events:
        running += event.get("change") or 0.0
        combined.append({**event, "bankroll": round(running, 2)})
    totals = {
        "starting_capital": starting,
        "realized_bankroll": round(sum(s["realized_bankroll"] for s in sports), 2),
        "profit_loss": round(sum(s["profit_loss"] for s in sports), 2),
        "open_exposure": round(sum(s["open_exposure"] for s in sports), 2),
        "available_capital": round(sum(s["available_capital"] for s in sports), 2),
        "open_count": sum(s["open_count"] for s in sports),
        "settled_count": sum(s["settled_count"] for s in sports),
        "record": {k: sum(s["record"][k] for s in sports) for k in ("WIN", "LOSS", "PUSH")},
    }
    return {"schema_version": LEDGER_SCHEMA, "generated_at": _iso(now),
            "authority": "Virtual research only. No real-money wagering and no execution outside AISP.",
            "totals": totals, "combined_series": combined, "sports": sports}


# ------------------------------------------------------------- decisions ---

DECISIONS_SCHEMA = "AISP_COMMAND_CENTER_DECISIONS_V1"
PHASE_ORDER = {"MORNING": 0, "LINEUP_CONFIRMED": 1, "FINAL_PREGAME": 2}


def _position_display(position_status: Any, profit_loss: Any, game_status: Any) -> str:
    """What to call a position on the board.

    The only case this exists for: the game is over, the settlement owner has
    not run yet, and an open position is indistinguishable from one on a game
    still being played. Saying "Final -- settlement pending" describes what is
    already true; it settles nothing and changes no ledger row.
    """

    state = str(position_status).split(".")[-1].upper()
    game = str(game_status).split(".")[-1].upper()
    if profit_loss is not None or state == "SETTLED":
        return "Settled"
    if state == "VOID":
        return "Void"
    if state == "OPEN" and game == "FINAL":
        return "Final — settlement pending"
    # The canonical value is LIVE; IN_PROGRESS is what the scoreboard feed
    # calls it, and both reach this function depending on the caller.
    if state == "OPEN" and game in {"LIVE", "IN_PROGRESS"}:
        return "Open — game in progress"
    return "Open"


def decision_board(session: Session, day: date, now: datetime | None = None) -> dict[str, Any]:
    """The governed evaluations of one slate date, for the funded eras only.

    One row per (experiment, game, selection): its latest phase. A MORNING row
    is labelled as a morning evaluation -- it can never become a position on
    its own -- and a stored NO_BET from a phase whose quote was missing is
    reported as NOT_YET_EVALUATED, not as a pass.
    """

    now = now or datetime.now(UTC)
    rows = session.execute(text("""
        select d.id, d.experiment_id, e.name, a.sport, d.game_id, d.phase, d.market, d.selection,
               d.line, d.sportsbook, d.american_odds, d.quote_observed_at, d.model_probability,
               d.no_vig_probability, d.edge, d.expected_value, d.recommendation,
               d.recommendation_reason, d.recommended_at, g.starts_at, g.status,
               p.id, p.stake, p.status, pr.result, pr.profit_loss,
               d.odds_snapshot_id, d.evidence, d.qualification_reasons
        from shadow_experiment_decisions d
        join shadow_experiments e on e.id = d.experiment_id
        join experiment_account_memberships m on m.experiment_id = e.id and m.role = 'FUNDED_ERA'
        join experiment_accounts a on a.id = m.account_id
        join games g on g.id = d.game_id
        join leagues l on l.id = g.league_id
        left join official_asp_positions p on p.shadow_decision_id = d.id
        left join position_results pr on pr.position_id = p.id
        -- A decision counts for a sport only on that sport's games. The NFL
        -- experiment's retained rows on MLB games (2026-09-14, frozen since)
        -- stay in the ledger and never surface as NFL evaluations.
        where d.slate_date = :day and l.code = a.sport"""), {"day": day}).all()
    matchups = _matchups(session, sorted({str(r[4]) for r in rows}))
    book_names = dict(session.execute(text(
        "select key, name from sportsbooks where key = any(:k)"),
        {"k": sorted({str(r[9]) for r in rows if r[9]})}).all()) if rows else {}

    # Keyed by market, not selection: the chosen side changes between phases
    # (and a MORNING row with no quote stores a placeholder), so a selection key
    # left a finished game on the board twice -- its final evaluation beside an
    # older "not yet evaluated" row. A row that carries a position always wins.
    latest: dict[tuple, Any] = {}
    for row in rows:
        key = (str(row[1]), str(row[4]), str(row[6]))
        rank = (PHASE_ORDER.get(str(row[5]), -1), _aware(row[18]) or datetime.min.replace(tzinfo=UTC))
        if key not in latest:
            latest[key] = (rank, row)
            continue
        current_rank, current = latest[key]
        has_position, current_has_position = row[21] is not None, current[21] is not None
        if has_position != current_has_position:
            if has_position:
                latest[key] = (rank, row)
        elif rank > current_rank:
            latest[key] = (rank, row)

    shown = [str(row[0]) for _, row in latest.values()]
    integrity = _market_integrity(session, shown)
    forecasts_used = _forecast_check(session, shown, now)
    # MLB_V18_FUNDING_PAUSE_V1: a qualified V18 decision blocked before funding
    # evaluation carries its immutable pause record here, read-only. Additive:
    # null for every other decision, and never a stake or money figure.
    from app.services.mlb_v18_funding_pause import funding_pauses_for_decisions

    pauses = funding_pauses_for_decisions(session, shown)
    from app.services.owner_funding_restrictions import funding_receipt_views
    owner_funding_views = funding_receipt_views(session, shown)
    # A correction recorded beside a settlement (position_settlement_adjustments)
    # is what the position now reads as, as in the ledger; none exist today.
    corrected = _corrections(session, [row[21] for _, row in latest.values()
                                       if row[21] is not None and row[25] is not None])
    items: list[dict[str, Any]] = []
    for _, row in latest.values():
        (did, exp_id, exp_name, sport, game_id, phase, market, selection, line, book, american,
         quote_at, model_p, no_vig, edge, ev, rec, reason, rec_at, starts_at, status,
         pid, stake, pstatus, result, pnl, _snapshot_id, evidence, qualification) = row
        fix = corrected.get(_uuid_key(pid)) if pid is not None and pnl is not None else None
        start = _aware(starts_at)
        started = start is not None and start <= now
        missing_quote = no_vig is None or american is None
        restriction = ((evidence or {}).get("funding_restriction")
                       or owner_funding_views.get(str(did)))
        record_only = bool(restriction and restriction.get("record_only") and pid is None)
        presented = "RECORD_ONLY" if record_only else str(rec)
        if presented == "NO_BET" and missing_quote:
            presented = "NOT_YET_EVALUATED"
        if pid is not None:
            group = "POSITION"
        elif started or str(status).split(".")[-1].upper() in ("FINAL", "IN_PROGRESS"):
            group = "CLOSED"
        elif record_only and str(phase) == "FINAL_PREGAME":
            group = "PASSED"
        elif presented == "QUALIFIED_BET" and str(phase) == "FINAL_PREGAME":
            group = "READY"
        elif presented == "NO_BET" and str(phase) == "FINAL_PREGAME":
            group = "PASSED"
        else:
            group = "WATCHING"
        # Age AT THE DECISION, which is what the policy judged -- not age now.
        age = ((_aware(rec_at) - _aware(quote_at)).total_seconds() / 60) if quote_at and rec_at else None
        teams = matchups.get(str(game_id), {})
        if str(selection or "").upper() in ("HOME", "AWAY"):
            selection = f"{(teams.get(str(selection).lower()) or {}).get('abbreviation', selection)} ML"
        book = book_names.get(book, book)
        items.append({
            "decision_id": str(did), "sport": sport, "experiment": exp_name,
            "game_id": str(game_id), "matchup": matchups.get(str(game_id), {}).get("label"),
            "starts_at": _iso(starts_at), "phase": str(phase), "market": str(market),
            "selection": selection, "line": line, "sportsbook": book, "american": american,
            "quote_observed_at": _iso(quote_at), "quote_age_minutes": round(age, 1) if age is not None else None,
            "model_probability": model_p, "market_probability": no_vig,
            "edge": edge, "expected_value": ev,
            "recommendation": presented, "stored_recommendation": str(rec),
            "reason": ("Record-only: " + str(restriction.get("reason")) + "; original value evidence retained."
                       if record_only else reason), "recommended_at": _iso(rec_at),
            "funding_restriction": restriction,
            # Independently re-derived from persisted rows, for surfaces that
            # rank or headline a decision: never inferred from the disposition.
            "market_integrity": integrity.get(str(did)),
            "forecast_check": forecasts_used.get(str(did)),
            "funding_pause": pauses.get(str(did)),
            "adjusted": _adjusted(evidence),
            # Every edge figure the decision recorded, each named for what it is,
            # with the policy's own controlling tests (services/edge_metrics).
            "edge_metrics": edge_metrics(sport, american, model_p, no_vig, edge, ev, evidence),
            "plain_reasons": _plain_reasons(qualification),
            # College Football: what the recorded checkpoint means -- a priced
            # no-bet, a declined no-price, or a block that evaluated nothing.
            **({"checkpoint_semantics": _cfb_semantics(rec, qualification, reason)} if sport == "CFB" else {}),
            "decision_window": {"opens_at": _iso(start - WINDOW_OPENS), "closes_at": _iso(start - WINDOW_CLOSES)}
            if start else None,
            "group": group,
            "position": {"position_id": str(pid), "stake": float(stake) if stake is not None else None,
                         "status": "SETTLED" if pnl is not None else str(pstatus).upper(),
                         "result": fix.result if fix is not None else (str(result).upper() if result else None),
                         "profit_loss": (fix.profit_loss if fix is not None
                                         else float(pnl) if pnl is not None else None),
                         **(fix.as_dict() if fix is not None else {}),
                         # A finished game whose position the settlement owner has
                         # not reached yet reads identically to a live one unless
                         # we say so. The settlement itself is still the owner's:
                         # this only describes what is already true.
                         "display_status": _position_display(pstatus, pnl, status),
                         "awaiting_settlement": (
                             pnl is None
                             and str(pstatus).split(".")[-1].upper() == "OPEN"
                             and str(status).split(".")[-1].upper() == "FINAL")} if pid else None,
        })
    items.sort(key=lambda i: (i["starts_at"] or "", i["matchup"] or "", i["selection"] or ""))
    upcoming_windows = sorted(i["decision_window"]["closes_at"] for i in items
                              if i["decision_window"] and i["decision_window"]["closes_at"] > _iso(now))
    positions = [i for i in items if i["position"]]
    return {
        "schema_version": DECISIONS_SCHEMA, "date": day.isoformat(), "generated_at": _iso(now),
        "counts": {g: sum(1 for i in items if i["group"] == g)
                   for g in ("READY", "WATCHING", "POSITION", "PASSED", "CLOSED")},
        "exposure": round(sum((i["position"]["stake"] or 0) for i in positions
                              if i["position"]["status"] != "SETTLED"), 2),
        "staked": round(sum((i["position"]["stake"] or 0) for i in positions), 2),
        "realized_profit_loss": round(sum((i["position"]["profit_loss"] or 0) for i in positions), 2),
        "next_decision_deadline": upcoming_windows[0] if upcoming_windows else None,
        "items": items,
    }


PERMITTED_SOURCES = ("THE_ODDS_API", "SPORTSGAMEODDS")


def _market_integrity(session: Session, decision_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Re-derive the quote pair a decision priced against, from the stored snapshot.

    The selected leg is the decision's own odds snapshot. The opposite leg is
    the latest snapshot of the other outcome of the SAME market, from the SAME
    sportsbook and the SAME provider, observed no later than the decision. Pair
    time is the older leg; its age is measured at the decision, which is what
    the policy judged. A decision with no stored snapshot has no integrity row.
    """

    if not decision_ids:
        return {}
    rows = session.execute(text("""
        select d.id, os.observed_at, os.source, os.source_mode, os.american_odds,
               sb.key, sb.name, mo.outcome_key, mk.market_type, d.market, d.recommended_at,
               leg.observed_at, leg.american_odds, leg.outcome_key, leg.others
        from shadow_experiment_decisions d
        join odds_snapshots os on os.id = d.odds_snapshot_id
        join market_outcomes mo on mo.id = os.market_outcome_id
        join markets mk on mk.id = mo.market_id
        join sportsbooks sb on sb.id = os.sportsbook_id
        left join lateral (
            select os2.observed_at, os2.american_odds, mo2.outcome_key,
                   (select count(*) from market_outcomes m3
                     where m3.market_id = mo.market_id and m3.id <> mo.id) as others
            from market_outcomes mo2
            join odds_snapshots os2 on os2.market_outcome_id = mo2.id
            where mo2.market_id = mo.market_id and mo2.id <> mo.id
              and os2.sportsbook_id = os.sportsbook_id and os2.source = os.source
              and os2.source_mode = 'LIVE'
              and os2.observed_at <= coalesce(d.recommended_at, d.created_at)
            order by os2.observed_at desc limit 1
        ) leg on true
        where d.id = any(cast(:ids as uuid[]))"""), {"ids": decision_ids}).all()
    out: dict[str, dict[str, Any]] = {}
    for (did, observed, source, mode, american, book_key, book_name, _outcome, market_type,
         decision_market, rec_at, opp_observed, opp_american, _opp_outcome, others) in rows:
        two_sided = opp_observed is not None and int(others or 0) == 1
        older = min(_aware(observed), _aware(opp_observed)) if two_sided else _aware(observed)
        age = ((_aware(rec_at) - older).total_seconds() / 60) if rec_at and older else None
        market_matches = str(market_type).split(".")[-1].upper() == str(decision_market or "").upper()
        permitted = str(source or "").upper() in PERMITTED_SOURCES and str(mode or "").upper() == "LIVE"
        out[str(did)] = {
            "two_sided_same_book": two_sided,
            "market_matches": market_matches,
            "provider": _provider_label(source),
            "provider_permitted": permitted,
            "classification": _classification(source, book_key),
            "sportsbook": book_name, "sportsbook_key": book_key,
            "selected_american": american,
            "opposite_american": opp_american if two_sided else None,
            "older_leg_observed_at": _iso(older),
            "older_leg_age_minutes_at_decision": round(age, 1) if age is not None else None,
            "fresh_at_decision": age is not None and 0 <= age <= QUOTE_FRESHNESS_MINUTES,
        }
    return out


def _forecast_check(session: Session, decision_ids: list[str], now: datetime) -> dict[str, dict[str, Any]]:
    """Which forecast a decision used, and whether it was the controlling one."""

    if not decision_ids:
        return {}
    rows = session.execute(text("""
        with used as (
            select d.id, d.game_id, d.recommended_at, a.sport,
                   coalesce(d.shadow_forecast_id,
                            case when (d.evidence->>'shadow_forecast_row_id') ~ '^[0-9a-f-]{36}$'
                                 then cast(d.evidence->>'shadow_forecast_row_id' as uuid) end) as fid
            from shadow_experiment_decisions d
            join experiment_account_memberships m on m.experiment_id = d.experiment_id and m.role = 'FUNDED_ERA'
            join experiment_accounts a on a.id = m.account_id
            where d.id = any(cast(:ids as uuid[]))
        )
        select u.id, u.sport, sf.engine_id, sf.created_at,
               (select max(s2.created_at) from shadow_forecasts s2
                 where s2.canonical_game_id = u.game_id and s2.engine_id = sf.engine_id
                   and s2.created_at <= u.recommended_at) as latest_at_decision,
               (select max(s3.created_at) from shadow_forecasts s3
                 where s3.canonical_game_id = u.game_id and s3.engine_id = sf.engine_id) as latest_now
        from used u join shadow_forecasts sf on sf.id = u.fid"""), {"ids": decision_ids}).all()
    out: dict[str, dict[str, Any]] = {}
    for did, sport, engine, created, latest_at_decision, latest_now in rows:
        out[str(did)] = {
            "engine_id": engine,
            "controlling": CONTROLLING_ENGINES.get(str(sport)) == engine,
            "latest_at_decision": latest_at_decision is not None and _aware(created) >= _aware(latest_at_decision),
            "superseded_since_decision": latest_now is not None and _aware(latest_now) > _aware(created),
            # Historical name; the value is shadow_forecasts.created_at, the start of
            # AISP's import transaction -- not the producer's publication time.
            "published_at": _iso(created),
            "published_at_basis": "AISP_IMPORT_TRANSACTION_START",
        }
    return out


def _adjusted(evidence: Any) -> dict[str, Any] | None:
    """The policy's own adjusted edge and expected value, when it recorded them."""

    if not isinstance(evidence, dict):
        return None
    edge, ev = evidence.get("adjusted_edge"), evidence.get("adjusted_expected_value")
    if edge is None and ev is None:
        return None
    return {"edge": edge, "expected_value": ev, "probability": evidence.get("adjusted_probability")}


def _cfb_semantics(recommendation: Any, qualification: Any, detail: Any = None) -> dict[str, Any]:
    from app.cfb.paper_decision import checkpoint_semantics

    reason = qualification.get("reason") if isinstance(qualification, dict) else None
    return checkpoint_semantics(str(recommendation) if recommendation is not None else None, reason,
                                str(detail) if detail is not None else None)


def _plain_reasons(qualification: Any) -> list[str]:
    """Human sentences only; machine codes (ALL_CAPS_WITH_UNDERSCORES) are dropped."""

    if isinstance(qualification, str):
        qualification = [qualification]
    if isinstance(qualification, dict):
        qualification = list(qualification.values())
    out = []
    for item in qualification or []:
        text_item = str(item).strip()
        if not text_item or " " not in text_item or text_item.upper() == text_item:
            continue
        out.append(text_item)
    return out


# -------------------------------------------------------------- coverage ---

def _side(outcome_key: str | None, participant: str | None, teams: dict[str, Any]) -> tuple[str | None, float | None]:
    if participant:
        if participant == (teams.get("home") or {}).get("team_id"):
            return "HOME", None
        if participant == (teams.get("away") or {}).get("team_id"):
            return "AWAY", None
    key = (outcome_key or "").upper()
    for prefix in ("HOME", "AWAY", "OVER", "UNDER"):
        if key.startswith(prefix):
            return prefix, None
    return None, None


def _latest_snapshot(session: Session, before: datetime) -> dict[str, Any] | None:
    row = session.execute(text("""
        select g.starts_at, os.observed_at from odds_snapshots os
        join market_outcomes mo on mo.id = os.market_outcome_id
        join markets mk on mk.id = mo.market_id
        join games g on g.id = mk.game_id
        where os.source_mode = 'LIVE' and g.starts_at < :before
        order by os.observed_at desc limit 1"""), {"before": before}).first()
    if row is None:
        return None
    return {"date": _aware(row[0]).astimezone(ET).date().isoformat(), "observed_at": _iso(row[1])}


def _best(quotes: list[dict[str, Any]], side: str) -> dict[str, Any] | None:
    if not quotes:
        return None
    top = max(quotes, key=lambda q: q["sides"][side]["decimal"] or 0)
    return {"american": top["sides"][side]["american"], "sportsbook": top["sportsbook"],
            "provider": top["provider"], "observed_at": top["observed_at"]}


def _cfb_models(session: Session, ids: list[str]) -> dict[str, dict[str, Any]]:
    """The current CFB forecast per admitted game, joint channel labelled as controlling."""

    rows = session.execute(text("""
        select distinct on (canonical_game_id) canonical_game_id, joint_home_win_probability,
               joint_away_win_probability, expected_home_points, expected_away_points,
               exchange_published_utc, forecast_id, ridge_home_win_probability, elo_home_win_probability,
               origin, publication_clock_source, pmfs
        from cfb_research_forecasts
        where canonical_game_id = any(cast(:ids as uuid[]))
        order by canonical_game_id, exchange_published_utc desc nulls last, imported_at desc"""),
        {"ids": ids}).all()
    return {str(row[0]): cfb_model_entry(*row[1:]) for row in rows}


def cfb_model_entry(home_p: Any, away_p: Any, exp_home: Any, exp_away: Any, published: Any, forecast_id: Any,
                    ridge_home: Any, elo_home: Any, origin: Any, clock: Any, pmfs: Any) -> dict[str, Any]:
    """One CFB game's model reading for the slate surfaces -- every number from the JOINT channel.

    The projected score shown beside the joint win probability is the mean of
    the joint simulation's own draws, never Ridge's expected points: a card
    that put a Ridge score next to a joint probability would be quoting two
    models as one. Ridge and Elo stay available, labelled, as separate channels.
    """

    from app.cfb.market_read import joint_expected_points

    if isinstance(pmfs, str):
        import json as _json

        pmfs = _json.loads(pmfs)
    two_way = (home_p or 0) + (away_p or 0)
    joint_points = joint_expected_points(pmfs)
    return {
        "engine_id": "CFB_GEN1_JOINT_BASELINE", "home": home_p, "away": away_p,
        "home_two_way": round(home_p / two_way, 4) if two_way else None,
        "expected_home": round(joint_points["home"], 2) if joint_points else None,
        "expected_away": round(joint_points["away"], 2) if joint_points else None,
        "created_at": _iso(published), "forecast_id": forecast_id,
        "controlling_channel": "JOINT_PAIRED_RESIDUAL_BASELINE",
        "expected_points_channel": "JOINT_SCORE_DISTRIBUTION_MEAN" if joint_points else None,
        "separate_channels": {"ridge_development_calibrated_home": ridge_home, "elo_diagnostic_home": elo_home,
                              "ridge_expected_home_points": round(exp_home, 2) if exp_home is not None else None,
                              "ridge_expected_away_points": round(exp_away, 2) if exp_away is not None else None},
        "origin": origin, "publication_clock_source": clock,
    }


_CFB_PRICE_NOTE: dict[str, Any] = {}


def _cfb_price_note(session: Session) -> dict[str, Any]:
    """Why a CFB game has no price, read from the capacity gate (writes nothing).

    Held for ten minutes: the gate builds the allocator's projection, and the
    answer moves on the scale of a day, not a page view.
    """

    # Since 2026-10-03 the College Football price comes from The Odds API CFB lane
    # (``app.cfb.odds_api_price``: one request per kickoff cluster, T-75..T-35). When
    # that lane is switched on it IS the collector; the SportsGameOdds lane's capacity
    # gate (authorization expired, no allocation) no longer says anything about
    # whether a CFB game will be priced, so it is not reported as the reason.
    try:
        from app.core.config import get_settings

        odds_api_on = bool(getattr(get_settings(), "cfb_odds_api_enabled", False))
    except Exception:  # noqa: BLE001
        odds_api_on = False
    cached = _CFB_PRICE_NOTE.get("value")
    if (cached is not None and _CFB_PRICE_NOTE.get("odds_api") == odds_api_on
            and (datetime.now(UTC) - _CFB_PRICE_NOTE["at"]) < timedelta(minutes=10)):
        return cached
    if odds_api_on:
        from app.cfb.odds_api_price import BUY_CLOSES, BUY_OPENS
        from app.services.pregame_status import ODDS_API_LANE_ACTIVE

        opens, closes = int(BUY_OPENS.total_seconds() // 60), int(BUY_CLOSES.total_seconds() // 60)
        note = {"reason": ODDS_API_LANE_ACTIVE, "lane": "THE_ODDS_API_CFB", "lane_enabled": True,
                "provider": "The Odds API", "buy_opens_minutes": opens, "buy_closes_minutes": closes,
                "detail": (f"The Odds API CFB lane prices each game once, {opens} to {closes} minutes before "
                           "kickoff (one request per kickoff cluster). A game the provider does not list, or "
                           "that AISP cannot bind by team names and kickoff, is left without a price."),
                "objects_allowed": None, "spent_on_lane": None}
        _CFB_PRICE_NOTE.update({"value": note, "at": datetime.now(UTC), "odds_api": odds_api_on})
        return note
    # The owner's lane switch is read on its own: a switched-off lane has no
    # collector registered at all, whatever the capacity gate says.
    try:
        from app.core.config import get_settings

        lane_enabled: bool | None = bool(getattr(get_settings(), "cfb_sgo_lane_enabled", False))
    except Exception:  # noqa: BLE001
        lane_enabled = None
    try:
        from app.cfb.sgo_lane import capacity
        from app.core.config import get_settings

        nested = session.begin_nested()
        try:
            gate = capacity(session, get_settings())
        finally:
            nested.rollback()
        note = {"reason": gate["state"], "detail": gate["explanation"],
                "lane": gate.get("lane"), "lane_enabled": lane_enabled,
                "objects_allowed": gate.get("objects_allowed"), "spent_on_lane": gate.get("spent_on_lane"),
                "reference_lines": ("CFBD /lines (one probe, 2026-09-18) carries no quote timestamps and "
                                    "covers none of the current forecasts; reference only, never a price.")}
        _CFB_PRICE_NOTE.update({"value": note, "at": datetime.now(UTC), "odds_api": odds_api_on})
        return note
    except Exception as error:  # noqa: BLE001 -- the note is explanatory; never break the slate
        return {"reason": "PRICE_STATE_UNAVAILABLE", "detail": type(error).__name__, "lane_enabled": lane_enabled}


def _cfb_eligibility(session: Session, models: dict[str, dict[str, Any]], *, now: datetime,
                     as_of: datetime | None = None, settings: Any = None) -> dict[str, dict[str, Any]]:
    """Per CFB game: the present discovery lease of its forecast, and its recorded checkpoint.

    The present reading is the NEWEST verified manifest's observation of the
    exact forecast -- the manifest ``production_import.present_eligibility``
    reads -- without its kickoff short-cut, so a started game still shows when
    its lease lapsed. The checkpoint is the latest recorded FINAL_PREGAME
    decision under the CFB paper policy, exactly as written (its own evidence
    carries the lease it saw). Reads only; with ``as_of``, only what AISP had
    recorded by then. A live read (no ``as_of``) of an expired or unlisted lease is
    then projected the way the checkpoint would read it now
    (``_project_cfb_leases``), with the recorded reading kept beside it.
    """

    import uuid as _uuid

    from sqlalchemy import select

    from app.cfb.production_import import NO_DISCOVERY, NOT_IN_CURRENT_DISCOVERY, _lease_state
    from app.models import CfbDiscoveryManifest, CfbDiscoveryObservation, ShadowExperiment, ShadowExperimentDecision
    from app.services.research_paper_policy import CFB as CFB_POLICY

    forecast_ids = {m["forecast_id"] for m in models.values() if m and m.get("forecast_id")}
    newest = select(CfbDiscoveryManifest).order_by(CfbDiscoveryManifest.producer_created_utc.desc().nullslast(),
                                                   CfbDiscoveryManifest.first_verified_at.desc()).limit(1)
    if as_of is not None:
        newest = newest.where(CfbDiscoveryManifest.first_verified_at <= as_of)
    manifest = session.scalar(newest)
    observations: dict[str, Any] = {}
    if manifest is not None and forecast_ids:
        for obs in session.scalars(select(CfbDiscoveryObservation).where(
                CfbDiscoveryObservation.manifest_id == manifest.id,
                CfbDiscoveryObservation.forecast_id.in_(sorted(forecast_ids)))):
            observations[obs.forecast_id] = obs

    decisions = select(ShadowExperimentDecision).join(
        ShadowExperiment, ShadowExperiment.id == ShadowExperimentDecision.experiment_id).where(
        ShadowExperiment.decision_policy == CFB_POLICY.policy_version,
        ShadowExperimentDecision.phase == "FINAL_PREGAME",
        ShadowExperimentDecision.game_id.in_([_uuid.UUID(g) for g in models]))
    if as_of is not None:
        decisions = decisions.where(ShadowExperimentDecision.recommended_at <= as_of)
    from app.cfb.paper_decision import checkpoint_semantics

    checkpoints: dict[str, dict[str, Any]] = {}
    for row in session.scalars(decisions.order_by(ShadowExperimentDecision.recommended_at)):
        evidence = row.evidence if isinstance(row.evidence, dict) else {}
        reasons = row.qualification_reasons if isinstance(row.qualification_reasons, dict) else {}
        checkpoints[str(row.game_id)] = {
            "decision_id": str(row.id), "recorded_at": _iso(row.recommended_at),
            "recommendation": row.recommendation, "reason": reasons.get("reason"),
            "semantics": checkpoint_semantics(row.recommendation, reasons.get("reason"), row.recommendation_reason),
            "detail": row.recommendation_reason, "discovery": evidence.get("discovery")}

    manifest_fields = ({"manifest_sha256": manifest.manifest_sha256,
                        "manifest_created_utc": _iso(manifest.producer_created_utc)} if manifest else {})
    out: dict[str, dict[str, Any]] = {}
    for gid, model in models.items():
        reading = None
        if model and model.get("forecast_id"):
            obs = observations.get(model["forecast_id"])
            if manifest is None:
                reading = {"state": NO_DISCOVERY}
            elif obs is None:
                reading = {"state": NOT_IN_CURRENT_DISCOVERY, **manifest_fields}
            else:
                reading = {"state": _lease_state(obs, now), **manifest_fields,
                           "lease_valid_until": _iso(obs.eligibility_valid_until_utc),
                           "producer_status": obs.producer_status}
        out[gid] = {"now": reading, "checkpoint": checkpoints.get(gid)}
    if as_of is None:
        _project_cfb_leases(session, out, models, now=now, settings=settings)
    return out


def _project_cfb_leases(session: Session, out: dict[str, dict[str, Any]], models: dict[str, dict[str, Any]], *,
                        now: datetime, settings: Any = None) -> None:
    """Live reads only: an expired or unlisted lease is read the way the checkpoint would read it now.

    Between the producer's renewal and AISP's next import, the newest RECORDED
    manifest says EXPIRED_LEASE while the producer's pointer already holds a
    verified renewal; the checkpoint imports before it decides, so it never sees
    that gap. ``discovery_projection.project`` reads the pointer (read-only,
    cached per manifest) and the reading carries its ``basis``. A historical
    ``as_of`` replay never reads the pointer. Display only: never raises.
    """

    from sqlalchemy import select

    from app.cfb import discovery_projection
    from app.models import CfbResearchForecast

    wanted = {model["forecast_id"]: gid for gid, model in models.items()
              if model and model.get("forecast_id")
              and ((out.get(gid) or {}).get("now") or {}).get("state") in discovery_projection.PROJECTABLE}
    if not wanted:
        return
    try:
        if settings is None:
            from app.core.config import get_settings

            settings = get_settings()
        if not getattr(settings, "cfb_production_consumer_enabled", False):
            return
        rows = session.scalars(select(CfbResearchForecast).where(CfbResearchForecast.forecast_id.in_(sorted(wanted))))
        for row in rows:
            gid = wanted[row.forecast_id]
            out[gid]["now"] = discovery_projection.project(session, settings, row, recorded=out[gid]["now"], now=now)
    except Exception:  # noqa: BLE001 -- the recorded reading stands if the projection cannot be read
        return


def _nba_coverage(session: Session, ids: list[str], *, now: datetime,
                  as_of: datetime | None = None) -> dict[str, Any]:
    """NBA slate fields: model per game (three labelled channels), price note, collection, eligibility.

    ORM reads only (SQLite-testable), never a write. A game with no production
    package but a 2025-26 HISTORICAL row carries that row, labelled HISTORICAL.
    Any failure (for example, before the NBA migration is applied) degrades to
    empty NBA fields rather than breaking the MLB/NFL/CFB slate.
    """

    try:
        from app.core.config import get_settings
        from app.models.nba_gen1 import EVIDENCE_HISTORICAL
        from app.nba.gen1_read import eligibility_for, model_for_game, price_note
        from app.services import pregame_status
        from app.services.research_paper_policy import NBA as NBA_POLICY

        nested = session.begin_nested()
        try:
            models = {gid: model_for_game(session, gid) for gid in ids}
            eligibility = {gid: eligibility_for(session, (m or {}).get("forecast_id"), gid, now=now, as_of=as_of)
                           for gid, m in models.items() if m and m.get("evidence_class") != EVIDENCE_HISTORICAL}
            price = price_note(session, get_settings())
        finally:
            nested.rollback()
        return {"models": models, "eligibility": eligibility, "price": price,
                "collection": pregame_status.cfb_collection(price),
                "permitted": {_provider_label(source) for source in NBA_POLICY.permitted_quote_sources}}
    except Exception as error:  # noqa: BLE001 -- NBA must never blank the other sports' slate
        return {"models": {}, "eligibility": {}, "price": {"reason": "PRICE_STATE_UNAVAILABLE",
                                                           "detail": type(error).__name__},
                "collection": {}, "permitted": set(), "error": type(error).__name__}


def _game_collection(collection: dict[str, Any] | None, start: datetime) -> dict[str, Any]:
    """The CFB lane reading with THIS game's Odds API buy window (T-75..T-35) attached."""

    from app.cfb.odds_api_price import BUY_CLOSES, BUY_OPENS

    return {**(collection or {}), "opens_at": _iso(start - BUY_OPENS), "closes_at": _iso(start - BUY_CLOSES)}


def _nhl_coverage(session: Session, games: list[tuple[str, datetime]], *, now: datetime) -> dict[str, Any]:
    """NHL slate fields: the forecast the checkpoint would hold, and The Odds API lane's window.

    The model is ``nhl.paper.pregame_forecast`` at ``now`` (an AISP in-process
    Gen1 row or a producer-published one; with a replay ``as_of`` that clock is
    ``now``, so only what AISP held then is read), reported on its FINAL two-way
    channel (overtime and shootout included). The regulation three-way channel
    travels beside it, labelled, and is never the controlling number. Reads only;
    any failure degrades to empty NHL fields rather than breaking the slate.
    """

    try:
        import uuid as _uuid

        from app.nhl import paper as nhl_paper
        from app.nhl.odds_api_price import BUY_CLOSES, BUY_OPENS
        from app.nhl.read_service import publication

        models: dict[str, dict[str, Any] | None] = {}
        for gid, starts_at in games:
            start = _aware(starts_at)
            forecast, _excluded = nhl_paper.pregame_forecast(session, _uuid.UUID(gid), moment=now, starts_at=start)
            if forecast is None:
                models[gid] = None
                continue
            home, away = forecast.final_home_probability, forecast.final_away_probability
            two_way = (home or 0) + (away or 0)
            in_process = forecast.producer_evidence_status == nhl_paper.IN_PROCESS_EVIDENCE_STATUS
            models[gid] = {
                "engine_id": "NHL_GEN1_FINAL_TWO_WAY", "home": home, "away": away,
                "home_two_way": round(home / two_way, 4) if two_way else None,
                "expected_home": (round(forecast.final_expected_home_goals, 2)
                                  if forecast.final_expected_home_goals is not None else None),
                "expected_away": (round(forecast.final_expected_away_goals, 2)
                                  if forecast.final_expected_away_goals is not None else None),
                "created_at": _iso(publication(forecast)), "created_at_basis": "PUBLICATION_CLOCK",
                "forecast_id": forecast.forecast_id, "controlling_channel": nhl_paper.CHANNEL,
                "route": nhl_paper.ROUTE_IN_PROCESS if in_process else nhl_paper.ROUTE_PRODUCER,
                "separate_channels": {"regulation_home": forecast.regulation_home_probability,
                                      "regulation_tie": forecast.regulation_tie_probability,
                                      "regulation_away": forecast.regulation_away_probability},
            }
        try:
            from app.core.config import get_settings

            enabled = bool(getattr(get_settings(), "nhl_odds_api_enabled", False))
        except Exception:  # noqa: BLE001
            enabled = False
        return {"models": models, "enabled": enabled, "buy_opens": BUY_OPENS, "buy_closes": BUY_CLOSES,
                "permitted": {"The Odds API"}}
    except Exception as error:  # noqa: BLE001 -- NHL must never blank the other sports' slate
        return {"models": {}, "enabled": False, "permitted": {"The Odds API"}, "error": type(error).__name__}


def market_coverage(session: Session, day: date, now: datetime | None = None,
                    *, as_of: datetime | None = None) -> dict[str, Any]:
    """Stored coverage for one slate. With ``as_of``, only observations made at or
    before that instant are read and freshness is measured at that instant --
    the historical replay's point-in-time view. Nothing is restamped."""
    now = as_of or now or datetime.now(UTC)
    observed_cut = "and os.observed_at <= :as_of" if as_of is not None else ""
    lo = datetime.combine(day, datetime.min.time(), ET)
    hi = lo + timedelta(days=1)
    # MLB/NFL carry governed collection. College Football and NBA are listed with
    # their paper checkpoints but NO collection checkpoint: collection_opens_at is
    # null for them and their price state says why (see LISTED_LEAGUES).
    games = session.execute(text("""
        select g.id, l.code, g.starts_at, g.status, g.source_mode
        from games g join leagues l on l.id = g.league_id
        where g.starts_at >= :lo and g.starts_at < :hi
          and l.code = any(:leagues)
        order by g.starts_at, g.id"""), {"lo": lo, "hi": hi, "leagues": list(LISTED_LEAGUES)}).all()
    ids = [str(g[0]) for g in games]
    teams = _matchups(session, ids)

    quote_rows = session.execute(text("""
        with latest as (
          select distinct on (os.market_outcome_id, os.sportsbook_id, os.source)
                 os.market_outcome_id, os.sportsbook_id, os.source, os.observed_at,
                 os.american_odds, os.decimal_odds, os.point, os.source_mode,
                 os.available_as_of, os.raw_payload->>'response_received_at' as response_received_at
          from odds_snapshots os
          join market_outcomes mo on mo.id = os.market_outcome_id
          join markets mk on mk.id = mo.market_id
          where mk.game_id = any(cast(:ids as uuid[])) {observed_cut}
          order by os.market_outcome_id, os.sportsbook_id, os.source, os.observed_at desc
        )
        select mk.game_id, mk.market_type, mk.period, mo.outcome_key, mo.participant_team_id,
               sb.name, sb.key, l.source, l.observed_at, l.american_odds, l.decimal_odds, l.point,
               l.source_mode, l.available_as_of, l.response_received_at
        from latest l
        join market_outcomes mo on mo.id = l.market_outcome_id
        join markets mk on mk.id = mo.market_id
        join sportsbooks sb on sb.id = l.sportsbook_id""".format(observed_cut=observed_cut)),
        {"ids": ids, **({"as_of": as_of} if as_of is not None else {})}).all() if ids else []

    forecasts = {str(r[0]): r[1:] for r in session.execute(text("""
        select distinct on (canonical_game_id) canonical_game_id, engine_id,
               full_home_win_probability, full_away_win_probability, created_at,
               full_expected_home_score, full_expected_away_score
        from shadow_forecasts
        where canonical_game_id = any(cast(:ids as uuid[])) and engine_id = any(:engines)
        order by canonical_game_id, created_at desc"""),
        {"ids": ids, "engines": list(CONTROLLING_ENGINES.values())}).all()} if ids else {}

    from app.services import pregame_status

    receipt = (pregame_status.latest_receipt(day)
               if as_of is None and any(g[1] == "MLB" for g in games) else None)
    permitted = {_provider_label(source) for source in PERMITTED_SOURCES}
    cfb_ids = [str(g[0]) for g in games if g[1] == "CFB"]
    cfb_models = _cfb_models(session, cfb_ids) if cfb_ids else {}
    cfb_price = _cfb_price_note(session) if cfb_ids else None
    cfb_collection = pregame_status.cfb_collection(cfb_price) if cfb_ids else None
    cfb_eligibility = (_cfb_eligibility(session, {gid: cfb_models.get(gid) for gid in cfb_ids},
                                        now=now, as_of=as_of) if cfb_ids else {})
    from app.services.research_paper_policy import CFB as CFB_POLICY

    from app.services.research_paper_policy import effective_quote_sources

    try:
        from app.core.config import get_settings

        cfb_sources, _amendments = effective_quote_sources(CFB_POLICY, get_settings(), moment=now)
    except Exception:  # noqa: BLE001 -- display only: fall back to the frozen record
        cfb_sources = CFB_POLICY.permitted_quote_sources
    cfb_permitted = {_provider_label(source) for source in cfb_sources}
    nba_ids = [str(g[0]) for g in games if g[1] == "NBA"]
    nba = _nba_coverage(session, nba_ids, now=now, as_of=as_of) if nba_ids else {}
    nhl_games = [(str(g[0]), g[2]) for g in games if g[1] == "NHL"]
    nhl = _nhl_coverage(session, nhl_games, now=now) if nhl_games else {}
    # The Odds API CFB lane prices each game in its own T-75..T-35 window.
    cfb_odds_api = (cfb_collection or {}).get("allocation_state") == pregame_status.ODDS_API_LANE_ACTIVE

    by_game: dict[str, list[Any]] = defaultdict(list)
    for row in quote_rows:
        by_game[str(row[0])].append(row)

    out_games: list[dict[str, Any]] = []
    for game_id, league, starts_at, status, _source_mode in games:
        gid = str(game_id)
        matchup = teams.get(gid, {})
        # (market, book_key, source, line) -> side -> row ; pairs never cross book or provider
        buckets: dict[tuple, dict[str, Any]] = defaultdict(dict)
        # A hockey moneyline is the full game INCLUDING overtime and shootout (its own period label).
        full_game = "full_game_including_ot_so" if league == "NHL" else "full_game"
        for (_, market_type, period, outcome_key, participant, book_name, book_key, source,
             observed_at, american, dec, point, row_mode, available_as_of, received_at) in by_game.get(gid, []):
            # Only real provider observations: a demo/fixture row is never a quote.
            # (NFL games are RESEARCH_SHADOW rows whose quotes are LIVE observations.)
            if row_mode != "LIVE" or (period and str(period).split(".")[-1].lower() != full_game):
                continue
            market = str(market_type).split(".")[-1].upper()
            side, _ = _side(outcome_key, str(participant) if participant else None, matchup)
            if side is None:
                continue
            line = None
            if market == "TOTAL":
                line = point
            elif market in ("SPREAD", "RUN_LINE"):
                line = point if side == "HOME" else (-point if point is not None else None)
            buckets[(market, book_key, book_name, source, line)][side] = (observed_at, american, dec, point,
                                                                         available_as_of, received_at)

        quotes: list[dict[str, Any]] = []
        newest_per_book_market: dict[tuple, dict[str, Any]] = {}
        for (market, book_key, book_name, source, line), sides in buckets.items():
            a, b = ("OVER", "UNDER") if market == "TOTAL" else ("HOME", "AWAY")
            if a not in sides or b not in sides:
                continue
            older = min(_aware(sides[a][0]), _aware(sides[b][0]))
            age = (now - older).total_seconds() / 60
            quote = {
                "market": market, "line": line, "sportsbook": book_name, "sportsbook_key": book_key,
                "provider": _provider_label(source), "classification": _classification(source, book_key),
                "observed_at": _iso(older), "age_minutes": round(age, 1),
                "fresh": 0 <= age <= QUOTE_FRESHNESS_MINUTES,
                "sides": {a: {"american": sides[a][1], "decimal": sides[a][2]},
                          b: {"american": sides[b][1], "decimal": sides[b][2]}},
            }
            if league == "CFB":
                # The CFB SportsGameOdds path stamps each side with its own book
                # clock and keeps AISP's fetch and receipt clocks beside it; say
                # all three. (MLB rows carry one clock on both legs, so they get none.)
                from app.cfb.market_read import quote_clocks

                quote["clocks"] = quote_clocks(
                    {s: {"observed_at": sides[s][0], "available_as_of": sides[s][4],
                         "response_received_at": sides[s][5]} for s in (a, b)}, now=now)
            key = (market, book_key, source)
            if key not in newest_per_book_market or quote["observed_at"] > newest_per_book_market[key]["observed_at"]:
                newest_per_book_market[key] = quote
        quotes = sorted(newest_per_book_market.values(), key=lambda q: (q["market"], q["sportsbook"] or ""))

        moneylines = [q for q in quotes if q["market"] == "MONEYLINE"
                      and all((q["sides"][s]["decimal"] or 0) > 1 for s in ("HOME", "AWAY"))]
        no_vig = []
        for q in moneylines:
            h, w = 1 / q["sides"]["HOME"]["decimal"], 1 / q["sides"]["AWAY"]["decimal"]
            no_vig.append(h / (h + w))
            q["no_vig_home"] = round(h / (h + w), 4)
        fresh_ml = [q for q in moneylines if q["fresh"]]
        forecast = forecasts.get(gid)
        model = None
        if forecast is not None:
            engine, home_p, away_p, created, exp_home, exp_away = forecast
            two_way = (home_p or 0) + (away_p or 0)
            model = {"engine_id": engine, "home": home_p, "away": away_p,
                     "home_two_way": round(home_p / two_way, 4) if two_way else None,
                     "expected_home": round(exp_home, 2) if exp_home is not None else None,
                     "expected_away": round(exp_away, 2) if exp_away is not None else None,
                     "created_at": _iso(created),
                     # The start of AISP's import transaction, not publication/insert/commit.
                     "created_at_basis": "AISP_IMPORT_TRANSACTION_START"}
        if league == "CFB":
            model = cfb_models.get(gid)
        if league == "NBA":
            model = (nba.get("models") or {}).get(gid)
        if league == "NHL":
            model = (nhl.get("models") or {}).get(gid)
        start = _aware(starts_at)
        closes = start - WINDOW_CLOSES
        # When collection opens: MLB/NFL from the freshness rule; College Football (The Odds API lane on)
        # and hockey at their lanes' buy-window opening (T-75); NBA has no collector.
        if league in ("MLB", "NFL"):
            collection_opens = _iso(closes - timedelta(minutes=QUOTE_FRESHNESS_MINUTES))
        elif league == "CFB" and cfb_odds_api:
            from app.cfb.odds_api_price import BUY_OPENS as CFB_BUY_OPENS

            collection_opens = _iso(start - CFB_BUY_OPENS)
        elif league == "NHL" and nhl.get("enabled") and nhl.get("buy_opens") is not None:
            collection_opens = _iso(start - nhl["buy_opens"])
        else:
            collection_opens = None
        if fresh_ml:
            state = "CURRENT_PRICE"
        elif quotes:
            state = "STORED_REFERENCE_ONLY"
        else:
            state = "NOT_COLLECTED"
        out_games.append({
            "game_id": gid, "league": league, "matchup": matchup.get("label"),
            "home": matchup.get("home"), "away": matchup.get("away"),
            "starts_at": _iso(starts_at), "status": str(status).split(".")[-1],
            "price_state": state,
            "books": len({q["sportsbook_key"] for q in quotes}),
            "fresh_books": len({q["sportsbook_key"] for q in fresh_ml}),
            "freshest_observed_at": max((q["observed_at"] for q in quotes), default=None),
            "stalest_usable_observed_at": min((q["observed_at"] for q in fresh_ml), default=None),
            "consensus_no_vig_home": round(sum(no_vig) / len(no_vig), 4) if no_vig else None,
            "consensus_is_current": bool(fresh_ml) and len(fresh_ml) == len(moneylines),
            # Best CURRENT price only; a stale quote is never "best".
            "best": {side: _best(fresh_ml, side) for side in ("HOME", "AWAY")},
            "model": model,
            "quotes": quotes,
            **({"price_unavailable": cfb_price} if league == "CFB" and not quotes else {}),
            **({"market_status": pregame_status.market_status(moneylines, permitted, now=now,
                                                              opens=start - WINDOW_OPENS, closes=closes),
                "model_status": pregame_status.model_status(league, gid, start, model, now=now,
                                                            receipt=receipt)}
               if league in CONTROLLING_ENGINES else {}),
            # College Football: whether any collector can fetch a price, and the
            # producer's eligibility lease -- two facts, each on its own evidence.
            **({"market_status": pregame_status.cfb_market_status(
                    moneylines, len(quotes), _game_collection(cfb_collection, start) if cfb_odds_api
                    else (cfb_collection or {}), cfb_permitted, now=now,
                    opens=start - WINDOW_OPENS, closes=closes),
                "model_status": pregame_status.cfb_model_status(
                    model, eligibility_now=(cfb_eligibility.get(gid) or {}).get("now"),
                    checkpoint=(cfb_eligibility.get(gid) or {}).get("checkpoint"), starts_at=start)}
               if league == "CFB" else {}),
            # NBA: the same two facts, from the NBA lane's own gate and the NBA
            # discovery lease. A HISTORICAL row is labelled and never "eligible".
            **({"price_unavailable": nba.get("price")} if league == "NBA" and not quotes else {}),
            **({"market_status": pregame_status.cfb_market_status(
                    moneylines, len(quotes), nba.get("collection") or {}, nba.get("permitted") or set(), now=now,
                    opens=start - WINDOW_OPENS, closes=closes),
                "model_status": pregame_status.cfb_model_status(
                    model, eligibility_now=((nba.get("eligibility") or {}).get(gid) or {}).get("now"),
                    checkpoint=((nba.get("eligibility") or {}).get(gid) or {}).get("checkpoint"), starts_at=start,
                    engine_id=(model or {}).get("engine_id") or "NBA_GEN1_PRODUCTION")}
               if league == "NBA" else {}),
            # Hockey: the policy's market reading (The Odds API only) and The Odds API lane's window.
            **({"market_status": {**pregame_status.market_status(
                    moneylines, nhl.get("permitted") or set(), now=now, opens=start - WINDOW_OPENS, closes=closes),
                    "quotes_stored": len(quotes),
                    "collection": {"lane": "THE_ODDS_API_NHL", "active": bool(nhl.get("enabled")),
                                   "lane_enabled": bool(nhl.get("enabled")), "provider": "The Odds API",
                                   **({"opens_at": _iso(start - nhl["buy_opens"]),
                                       "closes_at": _iso(start - nhl["buy_closes"])}
                                      if nhl.get("buy_opens") is not None else {})}}}
               if league == "NHL" else {}),
            "checkpoints": {
                "collection_opens_at": collection_opens,
                "decision_window_opens_at": _iso(start - WINDOW_OPENS),
                "decision_window_closes_at": _iso(closes),
            },
        })

    counts = defaultdict(int)
    for g in out_games:
        counts[g["price_state"]] += 1
    all_quotes = [q for g in out_games for q in g["quotes"]]
    fresh = [q for q in all_quotes if q["fresh"]]
    upcoming = [g for g in out_games if g["starts_at"] and g["starts_at"] > _iso(now)]
    next_collection = min((g["checkpoints"]["collection_opens_at"] for g in upcoming
                           if g["price_state"] != "CURRENT_PRICE"
                           and g["checkpoints"]["collection_opens_at"]
                           and g["checkpoints"]["collection_opens_at"] > _iso(now)), default=None)
    return {
        "schema_version": COVERAGE_SCHEMA, "date": day.isoformat(), "generated_at": _iso(now),
        "freshness_window_minutes": QUOTE_FRESHNESS_MINUTES,
        "summary": {
            "games": len(out_games), "by_state": dict(counts),
            "by_league": {lg: sum(1 for g in out_games if g["league"] == lg)
                          for lg in sorted({g["league"] for g in out_games})},
            "sportsbooks": sorted({q["sportsbook"] for q in all_quotes if q["sportsbook"]}),
            "providers": sorted({q["provider"] for q in all_quotes if q["provider"]}),
            "freshest_observed_at": max((q["observed_at"] for q in all_quotes), default=None),
            "stalest_usable_observed_at": min((q["observed_at"] for q in fresh), default=None),
            "next_collection_at": next_collection,
            # The most recent legitimate stored market evidence, for a page that
            # must show SOMETHING when this date is not priced yet. Its date and
            # observation time travel with it so it is never mistaken for today.
            "latest_snapshot": _latest_snapshot(session, hi),
        },
        "provider_calls_made": 0,
        "games": out_games,
    }
