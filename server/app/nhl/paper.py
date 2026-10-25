"""AISP_NHL_PAPER_POLICY_V1 -- the NHL paper lifecycle, end to end. Built, wired, and OFF.

THE ONE MARKET
--------------
The full-game TWO-WAY moneyline, INCLUDING overtime and shootout, priced with
the forecast's frozen FINAL channel (``final_home_probability`` /
``final_away_probability``: the pooled-tie allocation of the regulation Poisson
model). Never the regulation three-way market, never a regulation probability
standing in for a final one: a pair is admitted only from a market stored at
``MARKET_PERIOD`` whose outcomes are exactly HOME and AWAY, from the provider's
``h2h`` market for ``icehockey_nhl`` (``app.nhl.odds_api_price`` writes nothing
else there). An NHL final never ties, so there is no push.

THE GATES, IN ORDER (each recorded with its reason; nothing is skipped silently)
-------------------------------------------------------------------------------
AS OF THE DECISION (MAIN 4fd NHL_PAPER_1, completed for MAIN 92b50e8d): every
candidate input row -- forecasts, eligibility records, stored quotes and their
market's outcome keys -- is read ONCE (``held_inputs``), and only THEN is the
decision clock read. Under PostgreSQL READ COMMITTED (the engine default; never
REPEATABLE READ, whose snapshot would predate the shared-allowance lock) a
statement returns only rows committed before it began, so every row held was
committed and visible before that clock. That clock -- not the checkpoint's tick
-- is the decision moment: inputs are filtered publication/observation <=
import/receipt <= moment BEFORE the latest is chosen, the T-15 cutoff is
rechecked against it, and it is what the decision and the position record. A row
committed after the inputs are held is never seen by this decision, whatever its
HTTP receipt clock says. Receipt (``available_as_of``) and publication clocks are
kept as delivered, beside the tick and the decision clock (``as_of`` in the
evidence); neither a receipt clock nor ``created_at`` is ever called a commit.

1. The game is SCHEDULED and in its window (T-30..T-15, the shared policy window),
   judged at the decision clock; a decision clock after T-15 is refused.
2. A real (``record_class`` FORECAST) NHL forecast of the policy's model,
   published and imported before the decision moment, published before its
   original start, and made for the game's CURRENT start (a moved game is
   SCHEDULE_DRIFT, never retimed); its final channel is a two-way mass summing
   to one. An AISP in-process forecast is admissible only if it was also
   published at or before T-30 of its original start, so a later in-process
   rerun never displaces the forecast AISP held at T-30.
3. Eligibility, by the controlling forecast's ROUTE (persisted as
   ``forecast_route`` in the decision evidence and in ``eligibility_binding``):
   a. PRODUCER_ELIGIBILITY (every producer-published forecast; unchanged): the
      producer's ``ASP_NHL_PAPER_RESEARCH_ELIGIBILITY_V1`` record binds this
      policy to that exact forecast, was published before T-30 and imported
      before the decision, and its frozen home/away probabilities ARE the
      forecast's final channel (tie zero). Until the producer publishes one, the
      game reads AWAITING_PRODUCER_ELIGIBILITY.
   b. AISP_IN_PROCESS (``producer_evidence_status`` AISP_IN_PROCESS_GENERATED:
      AISP ran the accepted, frozen Gen1 model itself):
      ``AISP_NHL_IN_PROCESS_ELIGIBILITY_V1`` -- the row is the frozen model
      (1.0.0, evidence mode AISP_IN_PROCESS_FROZEN_GEN1) under the pinned
      configuration sha256 3f7981aa...0417, with complete rest features
      (evidence ``restComplete`` exactly true), published <= T-30 of its original
      start, published <= imported <= the decision moment, and made for the
      game's current start. State AISP_IN_PROCESS_ELIGIBLE, under
      JAMES_STANDING_AUTHORITY_2026-10-15 and coordinator decision
      CLAUDE_COORDINATOR_NHL_SIMPLIFICATION_2026-10-16. No producer record is
      consulted, and none is ever minted.
4. A native two-sided pair: one provider, one book, one PROVIDER EVENT, one market
   row; both legs oriented to the game's teams, at the original start that the
   forecast and the current game share, each leg's book clock <= receipt clock <=
   the decision; the older leg governs freshness (240 minutes). A provider quoting
   the game under two events refuses all its pairs. Only a permitted provider.
5. The owner's shared $100 cross-sport allowance is in force. NHL has no daily
   pool of its own: without the shared allowance the checkpoint refuses.
6. The policy's own edge and positive expected return AT THE ACTUAL ENTRY PRICE,
   sized by quarter Kelly against the NHL era's bankroll and capped by the
   per-position, same-game and shared daily room (``moneyline_terms``).
7. The activation control (``NHL_PAPER_DECISIONS_ENABLED``, default off).

FUNDING
-------
``shared_daily_allowance.room`` takes the cross-sport lock and reads the day's
entered total; ``record`` writes the decision -- with the evaluated allowance
(``entered_usd``, ``room_usd``, ``slate_date``, lock, binding cap) in its
evidence -- and the position in the SAME transaction, committed by ``evaluate``.
One decision per game per era; a game that already holds a position never gets
another, whatever the side (the producer's duplicate key: account, protocol,
game, market).

SETTLEMENT (``settle``)
-----------------------
Only from a source-backed result: the current (not superseded) official
``nhl_game_results`` row for the game, imported from the producer's result
manifest. The winner is the official FINAL winner, so an overtime or shootout
decision settles exactly as the market does. A game the producer's schedule
package marks CANCELLED or POSTPONED is VOID. A later superseding result that
changes the winner is a correction BESIDE the original settlement (the existing
``position_settlement_adjustments`` table), never an edit, and never twice; its
basis is the preceding APPLIED adjustment (result, profit and scores) when one
exists, otherwise the original settlement, and it names both result hashes.
No result, no settlement: a played game without a result stays OPEN and is
reported as awaiting its result.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session
from app.services.event_time_reconciliation import (
    MATCH_WINDOW, legacy_clock_tolerance, pair_clock_verdict, reconcile_clock_identity,
)

from app.models import (
    Game, GameStatus, League, Market, MarketOutcome, OddsSnapshot, OfficialAspPosition, PositionResult,
    PositionSettlement, PositionSettlementAdjustment, PositionStatus, ShadowExperiment, ShadowExperimentDecision,
)
from app.models.nhl_research import NHL_LEAGUE_CODE, NhlGameResult, NhlResearchForecast
from app.services.research_paper_policy import NHL as POLICY
from app.services.research_paper_policy import effective_quote_sources, moneyline_terms

PHASE = "FINAL_PREGAME"
CHANNEL = "FINAL_TWO_WAY_INCLUDING_OT_SO"
MARKET_PERIOD = "full_game_including_ot_so"
#: Stored in shadow_experiment_decisions.period, a VARCHAR(40). The previous 41-character
#: label made every NHL decision insert fail in production (2026-10-17 00:30Z).
DECISION_PERIOD = "full_game_including_ot_so"
PROVIDER_MARKET = "h2h"
PROVIDER_SPORT_KEY = "icehockey_nhl"
#: The Odds API lists NHL games at PUCK DROP, which follows the scheduled (TV)
#: start by about 10 minutes (observed 2026-10-16: every one of 16 events was
#: scheduled + 10 min). A provider start in [scheduled, scheduled + 15 min] is
#: the same game; an earlier or later provider start is still refused.
PROVIDER_START_GRACE = MATCH_WINDOW     # the shared event-clock window; was a 15-min one-sided grace


def provider_start_matches(provider_start: datetime | None, scheduled_start: datetime | None) -> bool:
    """Stage-independent identity only; pair and decision gates independently enforce earlier deadlines."""
    return reconcile_clock_identity(scheduled_start,provider_start).accepted
MODEL_ID = "NHL_GEN1_REGULATION_POISSON"
WINDOW_OPENS = POLICY.window_opens
WINDOW_CLOSES = POLICY.window_closes
VOID_STATUSES = (GameStatus.CANCELED, GameStatus.POSTPONED)
ET = ZoneInfo("America/New_York")

#: Outcomes a later tick may revisit (the inputs can still arrive); anything else is final for the game.
PREREQUISITE_REASONS = frozenset({
    "NO_FORECAST", "FORECAST_FINAL_CHANNEL_INVALID", "AWAITING_PRODUCER_ELIGIBILITY", "ELIGIBILITY_RECORD_INVALID",
    "NO_TWO_SIDED_QUOTE", "NO_PERMITTED_PROVIDER", "QUOTE_TOO_STALE", "SHARED_ALLOWANCE_NOT_EFFECTIVE",
    "SCHEDULE_DRIFT", "BEFORE_DECISION_WINDOW",
    # Room held for OTHER priority (MLB/NHL) games ordered before this one
    # (AISP_SHARED_100_PRIORITY_MLB_NHL_V1): it frees as they decide or start.
    # A fully entered allowance (SHARED_ALLOWANCE_EXHAUSTED) never frees and is final.
    "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS",
})

# ------------------------------------------------- forecast routes (gate 3) --
ROUTE_PRODUCER = "PRODUCER_ELIGIBILITY"
ROUTE_IN_PROCESS = "AISP_IN_PROCESS"
#: The AISP in-process NHL forecast contract (coordinator decision 2026-10-16): AISP runs the accepted,
#: frozen Gen1 model itself. These are the exact row values its generator writes.
IN_PROCESS_EVIDENCE_STATUS = "AISP_IN_PROCESS_GENERATED"
IN_PROCESS_EVIDENCE_MODE = "AISP_IN_PROCESS_FROZEN_GEN1"
IN_PROCESS_MODEL_VERSION = "1.0.0"
#: sha256 of the frozen selected_model.json (producer nhl-gen1-overnight-20261001-v1/publication).
IN_PROCESS_CONFIGURATION_SHA256 = "3f7981aac2bfee6f3d05b7e1d099a3342cc9057d09e13b09aaf4c81b7f720417"
IN_PROCESS_ELIGIBLE = "AISP_IN_PROCESS_ELIGIBLE"
IN_PROCESS_ELIGIBILITY_CONTRACT = "AISP_NHL_IN_PROCESS_ELIGIBILITY_V1"
IN_PROCESS_AUTHORITY = ("JAMES_STANDING_AUTHORITY_2026-10-15 (PRODUCER_STATUS 22:15Z; NFL/CFB/NBA/NHL 2026-27, "
                        "existing models and policies, included allowances, no new USD)")
IN_PROCESS_COORDINATOR_DECISION = "CLAUDE_COORDINATOR_NHL_SIMPLIFICATION_2026-10-16"

# ------------------------------------------------ the held-inputs clock rule --
AS_OF_CONTRACT = "AISP_NHL_HELD_INPUTS_THEN_DECISION_CLOCK_V1"
AS_OF_BASIS = (
    "Every candidate input row (forecasts, eligibility records, stored quotes, market outcome keys) was read by "
    "statements that completed BEFORE decision_moment was read from the real clock; under READ COMMITTED a "
    "statement returns only rows committed before it began, so every admitted row was committed and visible "
    "before decision_moment. Rows committed later were never read by this decision. tick_moment is the "
    "checkpoint's selection clock only. Quote available_as_of is the HTTP response receipt and forecast/"
    "eligibility imported_at the importer's clock: upstream clocks, kept as delivered, not commit clocks.")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _wall_clock() -> datetime:
    """The real decision clock. Read only AFTER a game's inputs are held (``decide``)."""

    return datetime.now(UTC)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def slate_date(starts_at: datetime) -> date:
    return _aware(starts_at).astimezone(ET).date()


def american_to_decimal(american: int) -> float:
    return 1 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def experiment(session: Session) -> ShadowExperiment | None:
    return session.scalar(select(ShadowExperiment).where(
        ShadowExperiment.sport == POLICY.sport, ShadowExperiment.decision_policy == POLICY.policy_version,
        ShadowExperiment.is_active.is_(True)))


def games_at_checkpoint(session: Session, *, now: datetime) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Game.id, Game.starts_at).join(League, League.id == Game.league_id)
        .where(League.code == NHL_LEAGUE_CODE, Game.status == GameStatus.SCHEDULED,
               Game.starts_at >= now + WINDOW_CLOSES, Game.starts_at <= now + WINDOW_OPENS + MATCH_WINDOW)
        .order_by(Game.starts_at)).all()
    from app.nhl.odds_api_price import effective_start
    return [{'game_id':gid,'starts_at':_aware(starts),
             'operational_start':effective_start(session,gid,_aware(starts),moment=now)}
            for gid,starts in rows
            if now+WINDOW_CLOSES <= effective_start(session,gid,_aware(starts),moment=now) <= now+WINDOW_OPENS]


# ------------------------------------------------------------------ inputs --
#: MAIN 4fd NHL_PAPER_1 / NHL_PAPER_2. Every consumed input is filtered by what AISP actually HELD at the
#: decision moment BEFORE the latest is chosen: a newer record that arrived later can never displace an
#: older one that was genuinely available, and is never used as if it had been. All of this is NHL-local;
#: the shared NFL/NBA/CFB pairing and eligibility helpers are unchanged.


def _clock(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    if isinstance(value, str) and value:
        try:
            return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _held_forecasts(session: Session, game_id: Any) -> list[NhlResearchForecast]:
    return list(session.scalars(select(NhlResearchForecast).where(
        NhlResearchForecast.game_id == game_id, NhlResearchForecast.record_class == "FORECAST",
        NhlResearchForecast.model_id == MODEL_ID)))


def _held_eligibility(session: Session) -> list[tuple[Any, dict[str, Any]]]:
    from app.models import ResearchSupplement
    from app.services.nfl_paper_eligibility import KIND, stored_document

    return [(row, stored_document(row)) for row in session.scalars(select(ResearchSupplement).where(
        ResearchSupplement.kind == KIND, ResearchSupplement.artifact_version == POLICY.eligibility_contract))]


def _held_quotes(session: Session, game_id: Any) -> dict[str, Any]:
    from app.models import GameTeam, Sportsbook

    sides = {s.is_home: s.team_id for s in session.scalars(select(GameTeam).where(GameTeam.game_id == game_id))}
    rows = session.execute(
        select(Market, MarketOutcome, OddsSnapshot, Sportsbook.key)
        .join(MarketOutcome, MarketOutcome.market_id == Market.id)
        .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id == game_id, Market.market_type == "MONEYLINE")).all()
    keys_by_market: dict[Any, set[str]] = {}
    market_ids = {market.id for market, _o, _s, _b in rows}
    if market_ids:
        for market_id, key in session.execute(select(MarketOutcome.market_id, MarketOutcome.outcome_key)
                                              .where(MarketOutcome.market_id.in_(market_ids))).all():
            keys_by_market.setdefault(market_id, set()).add(key)
    return {"sides": sides, "rows": list(rows), "keys_by_market": keys_by_market}


def held_inputs(session: Session, game_id: Any) -> dict[str, Any]:
    """Every candidate input row for the game, read ONCE, BEFORE the decision clock (MAIN 92b50e8d).

    Forecasts of the policy's model, the NHL eligibility records, the game's sides, every stored moneyline
    quote row and its market's outcome keys. ``decide`` reads the decision clock only after this returns
    and then filters exactly these rows as of that clock; nothing is queried again, so a row committed
    after this point -- whatever its receipt clock -- is never admitted by the decision.
    """

    return {"forecasts": _held_forecasts(session, game_id), "eligibility": _held_eligibility(session),
            "quotes": _held_quotes(session, game_id)}


def _route(forecast: NhlResearchForecast) -> str:
    return ROUTE_IN_PROCESS if forecast.producer_evidence_status == IN_PROCESS_EVIDENCE_STATUS else ROUTE_PRODUCER


def pregame_forecast(session: Session, game_id: Any, *, moment: datetime, starts_at: datetime,
                     held: dict[str, Any] | None = None,
                     ) -> tuple[NhlResearchForecast | None, list[dict[str, Any]]]:
    """The latest real forecast that AISP held at ``moment``, published before its original start.

    Admissible = publication <= import and availability <= moment, publication < the forecast's original
    start, and that original start IS the game's current start (a moved game is drift, never retimed). An
    AISP in-process forecast must also have been published at or before T-30 of its original start (its
    eligibility contract), so a later in-process rerun never displaces the one AISP held at T-30.
    ``held`` = the rows ``decide`` read before its clock; without it the rows are read now (reporting only).
    Returns (chosen or None, the excluded candidates with why).
    """

    from app.nhl.read_service import publication

    rows = held["forecasts"] if held is not None else _held_forecasts(session, game_id)
    admissible, excluded = [], []
    for r in rows:
        published, imported, available = publication(r), _aware(r.imported_at), _aware(r.available_as_of)
        original = _aware(r.scheduled_start_utc)
        in_process = _route(r) == ROUTE_IN_PROCESS
        why = ("CLOCK_MISSING" if None in (published, imported, available, original) else
               "NOT_AVAILABLE_AT_DECISION" if not (published <= imported and published <= available
                                                   and max(imported, available) <= moment) else
               "PUBLISHED_AT_OR_AFTER_ORIGINAL_START" if published >= original else
               "IN_PROCESS_PUBLISHED_AFTER_T30" if in_process and published > original - WINDOW_OPENS else
               "ORIGINAL_START_IS_NOT_THE_CURRENT_START" if original != starts_at else None)
        if why:
            excluded.append({"forecast_id": r.forecast_id, "route": _route(r), "why": why,
                             "published_utc": published, "imported_at": imported, "available_as_of": available,
                             "original_start_utc": original})
        else:
            admissible.append(r)
    chosen = max(admissible, key=lambda r: (publication(r), _aware(r.imported_at))) if admissible else None
    return chosen, excluded


def in_process_eligibility(forecast: NhlResearchForecast, *, moment: datetime,
                           starts_at: datetime) -> dict[str, Any]:
    """``AISP_NHL_IN_PROCESS_ELIGIBILITY_V1`` for a forecast AISP generated itself. Fails closed, names why.

    Eligible when the row is AISP_IN_PROCESS_GENERATED by the frozen model (the policy's model id, version
    1.0.0, evidence mode AISP_IN_PROCESS_FROZEN_GEN1, record class FORECAST) under the pinned configuration
    sha256, its evidence records ``restComplete`` exactly True (no earlier-date game of either team was
    still unstarted when the schedule was received), published <= T-30 of its original start, published <=
    imported (and available) <= ``moment``, and its original start is the game's current start.
    """

    published, imported = _aware(forecast.published_utc), _aware(forecast.imported_at)
    available, original = _aware(forecast.available_as_of), _aware(forecast.scheduled_start_utc)
    base = {"route": ROUTE_IN_PROCESS, "policy_version": POLICY.policy_version,
            "contract": IN_PROCESS_ELIGIBILITY_CONTRACT, "authority_reference": IN_PROCESS_AUTHORITY,
            "coordinator_decision": IN_PROCESS_COORDINATOR_DECISION, "forecast_id": forecast.forecast_id,
            "record_id": None, "experiment_key": None,
            "producer_evidence_status": forecast.producer_evidence_status, "evidence_mode": forecast.evidence_mode,
            "model_id": forecast.model_id, "model_version": forecast.model_version,
            "configuration_sha256": forecast.configuration_sha256,
            "pinned_configuration_sha256": IN_PROCESS_CONFIGURATION_SHA256,
            "published_utc": published, "imported_at": imported, "available_as_of": available,
            "original_start_utc": original, "current_start_utc": starts_at, "decision_moment": moment,
            "t30_of_original_start": original - WINDOW_OPENS if original else None}
    violations = []
    for label, actual, wanted in (("PRODUCER_EVIDENCE_STATUS", forecast.producer_evidence_status,
                                   IN_PROCESS_EVIDENCE_STATUS),
                                  ("RECORD_CLASS", forecast.record_class, "FORECAST"),
                                  ("MODEL_ID", forecast.model_id, MODEL_ID),
                                  ("MODEL_VERSION", forecast.model_version, IN_PROCESS_MODEL_VERSION),
                                  ("EVIDENCE_MODE", forecast.evidence_mode, IN_PROCESS_EVIDENCE_MODE),
                                  ("CONFIGURATION_SHA256", forecast.configuration_sha256,
                                   IN_PROCESS_CONFIGURATION_SHA256)):
        if actual != wanted:
            violations.append(f"{label}:{actual} is not {wanted}")
    # The generator's own measurement that the rest features equal the game-day computation (a day-ahead
    # forecast made before a team's earlier-date game started would carry the game before it). Exactly True.
    if (forecast.producer_evidence_ref or {}).get("restComplete") is not True:
        violations.append("REST_FEATURES_NOT_COMPLETE_AT_GENERATION")
    if None in (published, imported, available, original):
        violations.append("CLOCK_MISSING")
    else:
        if published > original - WINDOW_OPENS:
            violations.append("PUBLISHED_AFTER_T30_OF_ORIGINAL_START")
        if not published <= imported <= moment or not published <= available <= moment:
            violations.append("NOT_HELD_AT_DECISION_MOMENT")
        if original != starts_at:
            violations.append("ORIGINAL_START_IS_NOT_THE_CURRENT_START")
    eligible = not violations
    return {**base, "eligible": eligible, "state": IN_PROCESS_ELIGIBLE if eligible else "ELIGIBILITY_RECORD_INVALID",
            "missing": [], "violations": violations, "excluded": [],
            "detail": None if eligible else f"{IN_PROCESS_ELIGIBILITY_CONTRACT} refuses: {violations}",
            # The controlling probabilities are the forecast's own frozen final channel; no record restates them.
            "frozen_probability": {"home": forecast.final_home_probability, "away": forecast.final_away_probability,
                                   "tie": 0.0}}


def eligibility_record(session: Session, forecast: NhlResearchForecast, *, moment: datetime,
                       starts_at: datetime, held: dict[str, Any] | None = None) -> dict[str, Any]:
    """The eligibility that AISP HELD at ``moment`` for this forecast, by its route. NHL-local.

    AISP in-process forecasts take ``in_process_eligibility``. Every other forecast takes the producer
    route, unchanged: candidates bind this policy's experiment key to this exact forecast. Admissible =
    published <= imported <= moment and published before T-30. The latest admissible is then checked as the
    shared gate checks (required bindings, flags that must stay false/true, controlling configuration); an
    invalid latest record fails closed rather than falling back to an older one.
    """

    if _route(forecast) == ROUTE_IN_PROCESS:
        return in_process_eligibility(forecast, moment=moment, starts_at=starts_at)

    from app.services.nfl_paper_eligibility import (
        MUST_REMAIN_FALSE, MUST_REMAIN_TRUE, REQUIRED_BINDINGS, _at, experiment_key,
    )

    key = experiment_key(POLICY.policy_version, POLICY.sport, POLICY.season_id)
    base = {"route": ROUTE_PRODUCER, "policy_version": POLICY.policy_version, "contract": POLICY.eligibility_contract,
            "experiment_key": key, "forecast_id": forecast.forecast_id}
    candidates = []
    for row, doc in (held["eligibility"] if held is not None else _held_eligibility(session)):
        if (_at(doc, "binding.forecastId") == forecast.forecast_id
                and _at(doc, "summaries.authorization.experimentId") == key):
            candidates.append((row, doc))
    if not candidates:
        return {**base, "eligible": False, "state": "AWAITING_PRODUCER_ELIGIBILITY",
                "detail": f"No {POLICY.eligibility_contract} record binds {key} to forecast {forecast.forecast_id}."}
    admissible, excluded = [], []
    for row, doc in candidates:
        published, imported = _aware(row.published_utc), _aware(row.imported_at)
        why = ("CLOCK_MISSING" if published is None or imported is None else
               "NOT_AVAILABLE_AT_DECISION" if not published <= imported <= moment else
               "PUBLISHED_AT_OR_AFTER_T30" if published >= starts_at - WINDOW_OPENS else None)
        if why:
            excluded.append({"record_id": str(row.id), "why": why, "published_utc": published, "imported_at": imported})
        else:
            admissible.append((row, doc))
    if not admissible:
        late = all(e["why"] == "NOT_AVAILABLE_AT_DECISION" for e in excluded)
        return {**base, "eligible": False, "excluded": excluded,
                "state": "AWAITING_PRODUCER_ELIGIBILITY" if late else "ELIGIBILITY_RECORD_INVALID",
                "detail": ("No eligibility record for this forecast was held by AISP at the decision moment."
                           if late else "No eligibility record was published before T-30 and held at the decision.")}
    row, doc = max(admissible, key=lambda pair: (_aware(pair[0].published_utc), _aware(pair[0].imported_at)))
    missing = [path for path in REQUIRED_BINDINGS if _at(doc, path) in (None, "")]
    violations = [path for path in MUST_REMAIN_FALSE if _at(doc, path) is not False]
    violations += [path for path in MUST_REMAIN_TRUE if _at(doc, path) is not True]
    configuration = _at(doc, "binding.configurationIdentity") or _at(
        doc, "summaries.frozenConfiguration.configurationIdentity")
    if configuration != POLICY.controlling_configuration:
        violations.append(f"configuration {configuration} is not {POLICY.controlling_configuration}")
    eligible = not missing and not violations
    return {**base, "eligible": eligible, "state": "ELIGIBLE" if eligible else "ELIGIBILITY_RECORD_INVALID",
            "record_id": str(row.id), "published_utc": _aware(row.published_utc),
            "imported_at": _aware(row.imported_at), "missing": missing, "violations": violations,
            "excluded": excluded,
            "frozen_probability": {"home": _at(doc, "summaries.frozenConfiguration.homeWinProbability"),
                                   "away": _at(doc, "summaries.frozenConfiguration.awayWinProbability"),
                                   "tie": _at(doc, "summaries.frozenConfiguration.tieProbability")}}


def _leg_refusal(market: Market, outcome_keys: set[str], outcome: MarketOutcome, snap: OddsSnapshot,
                 sides: dict[bool, Any], *, moment: datetime, starts_at: datetime,
                 original_start: datetime) -> str | None:
    raw = snap.raw_payload or {}
    side = str(outcome.outcome_key).split(":")[0].upper()
    kick = _clock(raw.get("event_commence_time"))
    observed, available = _aware(snap.observed_at), _aware(snap.available_as_of)
    if market.period != MARKET_PERIOD:
        return f"MARKET_PERIOD:{market.period}"
    if outcome_keys != {"HOME:None", "AWAY:None"}:
        return f"NOT_TWO_WAY:{sorted(outcome_keys)}"
    if raw.get("market") != PROVIDER_MARKET or raw.get("sport_key") != PROVIDER_SPORT_KEY:
        return "NOT_THE_PROVIDER_FULL_GAME_H2H"
    if (side not in ("HOME", "AWAY") or raw.get("side") != side or outcome.participant_team_id is None
            or outcome.participant_team_id != sides.get(side == "HOME")):
        return "ORIENTATION"
    if not snap.source_event_id or str(raw.get("provider_event_id")) != str(snap.source_event_id):
        return "EVENT_IDENTITY_MISSING_OR_CONFLICTING"
    # A leg with no event clock at all is a LEGACY leg: the pair gate decides it
    # (identity exact, retained kickoff within the NHL legacy tolerance; hotfix 2026-10-24).
    if kick is not None and not (provider_start_matches(kick, original_start) and provider_start_matches(kick, starts_at)):
        return "ORIGINAL_START_DIFFERS"
    if observed is None or available is None:
        return "CLOCK_MISSING"
    if not observed <= available <= moment:
        return "NOT_AVAILABLE_AT_DECISION"
    if observed >= starts_at:
        return "QUOTED_AT_OR_AFTER_START"
    return None


def native_pairs(session: Session, game_id: Any, *, moment: datetime, starts_at: datetime,
                 original_start: datetime, held: dict[str, Any] | None = None,
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Two-way full-game pairs AISP HELD at ``moment``, both legs of ONE provider, book, event and market.

    A leg is admissible only if (``_leg_refusal``) its market is the two-way full-game (OT/SO) market with
    exactly HOME/AWAY outcomes oriented to the game's own teams; its raw record is the provider's
    icehockey_nhl ``h2h`` for that side; its provider event id is present and agrees with the snapshot's;
    its raw original start equals BOTH the forecast's original start and the game's current start (drift
    fails closed, never retimed); and book clock <= receipt clock <= ``moment``, before the start. Legs are
    grouped by (market, book, provider, event); the latest admissible leg per side is taken AFTER that
    filter. A provider quoting this game under more than one event refuses every pair it offers.
    ``held`` = the rows ``decide`` read before its clock; without it the rows are read now (reporting only).
    Returns (pairs, refused legs/groups with why).
    """

    quotes = held["quotes"] if held is not None else _held_quotes(session, game_id)
    sides, rows, keys_by_market = quotes["sides"], quotes["rows"], quotes["keys_by_market"]
    refused: list[dict[str, Any]] = []
    groups: dict[tuple, dict[str, list[dict[str, Any]]]] = {}
    for market, outcome, snap, book in rows:
        side = str(outcome.outcome_key).split(":")[0].upper()
        leg = {"snapshot_id": snap.id, "market_outcome_id": outcome.id, "market_id": market.id, "sportsbook": book,
               "source": snap.source, "provider_event_id": snap.source_event_id,
               "event_commence_time": _clock((snap.raw_payload or {}).get("event_commence_time")), "side": side,
               "american": snap.american_odds, "observed_at": _aware(snap.observed_at),
               "available_as_of": _aware(snap.available_as_of), "raw": snap.raw_payload or {}}
        why = _leg_refusal(market, keys_by_market.get(market.id, set()), outcome, snap, sides, moment=moment,
                           starts_at=starts_at, original_start=original_start)
        if why:
            refused.append({**leg, "why": why})
            continue
        groups.setdefault((market.id, book, snap.source, str(snap.source_event_id)), {}).setdefault(side, []).append(leg)
    events_by_provider: dict[str, set[str]] = {}
    for (_mid, _book, source, event) in groups:
        events_by_provider.setdefault(source, set()).add(event)
    pairs = []
    for (market_id, book, source, event), legs in groups.items():
        if len(events_by_provider[source]) > 1:
            refused.append({"sportsbook": book, "source": source, "provider_event_id": event,
                            "why": f"MIXED_PROVIDER_EVENTS:{sorted(events_by_provider[source])}"})
            continue
        if set(legs) != {"HOME", "AWAY"}:
            refused.append({"sportsbook": book, "source": source, "provider_event_id": event,
                            "why": f"ONE_SIDED:{sorted(legs)}"})
            continue
        # The newest leg PAIR that passes the clock verdict (newest first per side); a stale leg
        # retained at an old start must not win a book-clock tie and refuse the whole book.
        ordered = {side: sorted(legs[side], key=lambda leg: (leg["observed_at"], leg["available_as_of"]), reverse=True)
                   for side in ("HOME", "AWAY")}
        chosen = None
        for home in ordered["HOME"]:
            for away in ordered["AWAY"]:
                verdict=pair_clock_verdict(home['raw'],away['raw'],game_id=game_id,sport='NHL',
                    home_id=sides.get(True),away_id=sides.get(False),canonical_start=starts_at,
                    moment=moment,close_before=None,legacy_tolerance=legacy_clock_tolerance("NHL"))
                if verdict['accepted']:
                    chosen = (home, away, verdict); break
            if chosen: break
        if chosen is None:
            refused.append({'sportsbook':book,'source':source,'provider_event_id':event,'why':verdict['reason']})
            continue
        home, away, verdict = chosen
        older = min(home["observed_at"], away["observed_at"])
        retained = home["event_commence_time"].isoformat() if home["event_commence_time"] else verdict.get("provider_start")
        pairs.append({"market_id": str(market_id), "sportsbook": book, "source": source, "provider_event_id": event,
                      "event_commence_time": retained, "event_clock":verdict,
                      "home_american": home["american"], "away_american": away["american"],
                      "home_outcome_id": home["market_outcome_id"], "away_outcome_id": away["market_outcome_id"],
                      "home_snapshot_id": home["snapshot_id"], "away_snapshot_id": away["snapshot_id"],
                      "home_observed_at": home["observed_at"].isoformat(),
                      "away_observed_at": away["observed_at"].isoformat(),
                      "home_available_as_of": home["available_as_of"].isoformat(),
                      "away_available_as_of": away["available_as_of"].isoformat(),
                      "pair_observed_at": older.isoformat(),
                      "older_leg": "HOME" if home["observed_at"] <= away["observed_at"] else "AWAY"})
    return pairs, refused


def _exposure(session: Session, era: ShadowExperiment, game_id: Any) -> tuple[float, int]:
    rows = session.execute(
        select(OfficialAspPosition.stake).join(
            ShadowExperimentDecision, ShadowExperimentDecision.id == OfficialAspPosition.shadow_decision_id)
        .where(OfficialAspPosition.shadow_experiment_id == era.id,
               ShadowExperimentDecision.game_id == game_id)).all()
    return sum(float(s) for (s,) in rows), len(rows)


# ---------------------------------------------------------------- decision --
def _isolation_level(session: Session) -> str:
    try:
        return str(session.connection().get_isolation_level())
    except Exception:  # noqa: BLE001 - evidence only; never a reason to decide differently
        return "UNKNOWN"


def _route_evidence(permission: dict[str, Any]) -> dict[str, Any]:
    """What the decision evidence and ``eligibility_binding`` keep about the controlling forecast's route."""

    keys = ("route", "state", "eligible", "contract", "authority_reference", "coordinator_decision", "record_id",
            "experiment_key", "forecast_id", "producer_evidence_status", "evidence_mode", "model_version",
            "configuration_sha256", "pinned_configuration_sha256", "published_utc", "imported_at",
            "available_as_of", "original_start_utc", "t30_of_original_start", "decision_moment", "violations",
            "missing")
    return {k: permission[k] for k in keys if k in permission}


def decide(session: Session, settings: Any, era: ShadowExperiment, game: dict[str, Any],
           moment: datetime | None = None, *, clock: Any = None) -> dict[str, Any]:
    """One game's decision. ``moment`` is the checkpoint TICK (when the game was selected); the DECISION
    clock is read from ``clock`` (default: the real wall clock) only AFTER every input row is held.

    MAIN 92b50e8d (NHL_PAPER_1): a quote received before the tick but committed after it can never be
    admitted against the tick, because the tick is not the decision moment. The inputs are held first;
    the decision moment is the real clock read afterwards; the T-15 cutoff is rechecked against it; and
    only rows held at that moment, with every upstream clock <= it, are admitted.
    """

    from app.services.shadow_experiment_service import ShadowExperimentService
    from app.services.shared_daily_allowance import room as shared_room

    canonical_start = _aware(game["starts_at"])
    start = canonical_start
    held = held_inputs(session, game["game_id"])
    decision_moment = _aware((clock or _wall_clock)())     # read only now: every input above is already held
    tick = _aware(moment) if moment is not None else decision_moment
    moment = decision_moment
    from app.nhl.odds_api_price import effective_start
    start=effective_start(session,game['game_id'],canonical_start,moment=moment)
    base: dict[str, Any] = {"game_id": str(game["game_id"]), "starts_at": start.isoformat(),
                            "evaluated_at": moment.isoformat(), "policy_version": POLICY.policy_version,
                            "market": "MONEYLINE", "period": DECISION_PERIOD, "controlling_channel": CHANNEL,
                            "as_of": {"contract": AS_OF_CONTRACT, "tick_moment": tick,
                                      "inputs_held_before": moment, "decision_moment": moment,
                                      "isolation_level": _isolation_level(session),
                                      "held_rows": {"forecasts": len(held["forecasts"]),
                                                    "eligibility_records": len(held["eligibility"]),
                                                    "quote_legs": len(held["quotes"]["rows"])},
                                      "basis": AS_OF_BASIS}}
    if moment > start - WINDOW_CLOSES:
        return {**base, "outcome": "BLOCKED", "reason": "AFTER_DECISION_CUTOFF",
                "detail": ("The decision clock, read after the inputs were held, is after T-15 of the game's "
                           "start; nothing is decided late and no earlier clock stands in for it.")}
    if moment < start - WINDOW_OPENS:
        return {**base, "outcome": "BLOCKED", "reason": "BEFORE_DECISION_WINDOW",
                "detail": "The decision clock is before T-30 of the game's start; the window is not open."}
    forecast, not_admitted = pregame_forecast(session, game["game_id"], moment=moment, starts_at=canonical_start, held=held)
    if forecast is None:
        drift = any(e["why"] == "ORIGINAL_START_IS_NOT_THE_CURRENT_START" for e in not_admitted)
        return {**base, "outcome": "BLOCKED", "reason": "SCHEDULE_DRIFT" if drift else "NO_FORECAST",
                "forecasts_not_admitted": not_admitted,
                "detail": ("Every forecast was made for an original start that is not the game's current start; "
                           "nothing is retimed." if drift else
                           "AISP held no real NHL forecast of this model at this decision moment, published "
                           "before its original start (an AISP in-process forecast: at or before T-30).")}
    if _aware(forecast.published_utc) > start-WINDOW_OPENS:
        return {**base,'outcome':'BLOCKED','reason':'FORECAST_PUBLISHED_AFTER_EARLIEST_T30'}
    home, away = forecast.final_home_probability, forecast.final_away_probability
    original_start = _aware(forecast.scheduled_start_utc)
    base.update({"forecast_id": forecast.forecast_id, "nhl_research_forecast_id": forecast.id,
                 "canonical_game_ref": forecast.canonical_game_ref,
                 "forecast_published_utc": _aware(forecast.published_utc),
                 "forecast_imported_at": _aware(forecast.imported_at),
                 "forecast_available_as_of": _aware(forecast.available_as_of),
                 "forecast_original_start_utc": original_start,
                 "forecasts_not_admitted": not_admitted,
                 "channels": {CHANNEL: {"home": home, "away": away, "role": "controlling (final, includes OT/SO)"},
                              "REGULATION_THREE_WAY": {"home": forecast.regulation_home_probability,
                                                       "tie": forecast.regulation_tie_probability,
                                                       "away": forecast.regulation_away_probability,
                                                       "role": "recorded beside; never priced against this market"}}})
    if home is None or away is None or not 0 < home < 1 or abs(home + away - 1.0) > 1e-6:
        return {**base, "outcome": "BLOCKED", "reason": "FORECAST_FINAL_CHANNEL_INVALID",
                "detail": f"Final channel home={home} away={away} is not a two-way probability."}

    permission = eligibility_record(session, forecast, moment=moment, starts_at=canonical_start, held=held)
    base["forecast_route"] = _route_evidence(permission)
    if not permission["eligible"]:
        return {**base, "outcome": "BLOCKED", "reason": permission["state"], "eligibility": permission,
                "detail": permission.get("detail") or f"violations {permission.get('violations')}"}
    frozen = permission.get("frozen_probability") or {}
    if permission["route"] == ROUTE_PRODUCER and (
            frozen.get("tie") not in (0, 0.0) or frozen.get("home") is None or frozen.get("away") is None
            or abs(float(frozen["home"]) - home) > 1e-9 or abs(float(frozen["away"]) - away) > 1e-9):
        base["forecast_route"] = {**base["forecast_route"], "eligible": False, "state": "ELIGIBILITY_RECORD_INVALID"}
        return {**base, "outcome": "BLOCKED", "reason": "ELIGIBILITY_RECORD_INVALID", "eligibility": permission,
                "detail": f"The record's frozen probabilities {frozen} are not this forecast's final channel."}
    base["eligibility"] = {k: permission.get(k) for k in (
        "route", "state", "record_id", "contract", "experiment_key", "published_utc", "imported_at", "excluded")}

    pairs, wrong = native_pairs(session, game["game_id"], moment=moment, starts_at=canonical_start,
                                original_start=original_start, held=held)
    if wrong:
        base["refused_quotes"] = [{k: w.get(k) for k in ("sportsbook", "source", "provider_event_id", "side",
                                                         "snapshot_id", "observed_at", "available_as_of", "why")}
                                  for w in wrong]
    if not pairs:
        return {**base, "outcome": "DECLINED", "reason": "NO_TWO_SIDED_QUOTE",
                "detail": ("AISP held no full-game two-way NHL moneyline pair (OT/SO included) at this decision "
                           "from one provider, book, event and market at the original start"
                           + (" -- only refused quotes." if wrong else "."))}
    sources, amendments = effective_quote_sources(POLICY, settings, moment=moment)
    permitted = [p for p in pairs if p["source"] in sources]
    if not permitted:
        return {**base, "outcome": "DECLINED", "reason": "NO_PERMITTED_PROVIDER",
                "detail": f"Stored pairs come only from {sorted({p['source'] for p in pairs})}."}
    fresh, stale = [], []
    for pair in permitted:
        legs = [datetime.fromisoformat(pair[f"{s}_observed_at"]) for s in ("home", "away")]
        age = (moment - min(legs)).total_seconds() / 60
        entry = {**pair, "pair_age_minutes": round(age, 1)}
        (fresh if 0 <= age <= POLICY.max_quote_age_minutes else stale).append(entry)   # availability already proved
    if not fresh:
        return {**base, "outcome": "DECLINED", "reason": "QUOTE_TOO_STALE", "pairs_considered": stale,
                "detail": (f"No permitted pair is within {POLICY.max_quote_age_minutes} minutes on its older leg "
                           "with both legs before this decision and the start.")}

    # NHL is a PRIORITY sport (owner 2026-10-16): its room excludes its own hold and
    # counts only the holds of other undecided MLB/NHL games ordered before it.
    shared = shared_room(session, settings, day=slate_date(start), moment=moment, sport="NHL",
                         game_id=game["game_id"])
    if shared is None:
        return {**base, "outcome": "BLOCKED", "reason": "SHARED_ALLOWANCE_NOT_EFFECTIVE",
                "detail": "NHL is funded only by the owner's shared $100 daily allowance, which is not in force."}
    base["shared_daily_allowance"] = shared
    bankroll = ShadowExperimentService(session, settings).current_bankroll(era)
    same_game, held = _exposure(session, era, game["game_id"])
    candidates = []
    for pair in fresh:
        for side, win, american, other in (("HOME", home, pair["home_american"], pair["away_american"]),
                                           ("AWAY", away, pair["away_american"], pair["home_american"])):
            terms = moneyline_terms(POLICY, win_probability=float(win), decimal_odds=american_to_decimal(int(american)),
                                    opposite_decimal_odds=american_to_decimal(int(other)), bankroll=bankroll,
                                    same_game_exposure=same_game, daily_exposure=shared["exposure_counted_usd"],
                                    daily_limit_usd=shared["limit_usd"])
            candidates.append({"side": side, "american_odds": int(american), "opposite_american": int(other), "win_probability": float(win),
                               "channel": CHANNEL, "sportsbook": pair["sportsbook"], "source": pair["source"],
                               "market_id": pair["market_id"], "provider_event_id": pair["provider_event_id"],
                               "event_commence_time": pair["event_commence_time"],
                               "market_outcome_id": pair[f"{side.lower()}_outcome_id"],
                               "odds_snapshot_id": pair[f"{side.lower()}_snapshot_id"],
                               "observed_at": pair[f"{side.lower()}_observed_at"],
                               "available_as_of": pair[f"{side.lower()}_available_as_of"],
                               # Both native legs exactly as chosen: ids and original clocks, never retimed.
                               "legs": {s: {"snapshot_id": pair[f"{s}_snapshot_id"],
                                            "outcome_id": pair[f"{s}_outcome_id"],
                                            "american": pair[f"{s}_american"],
                                            "observed_at": pair[f"{s}_observed_at"],
                                            "available_as_of": pair[f"{s}_available_as_of"]}
                                        for s in ("home", "away")},
                               "pair_observed_at": pair["pair_observed_at"], "older_leg": pair["older_leg"],
                               "pair_age_minutes": pair["pair_age_minutes"], "terms": terms})
    base["quote_source_amendments"] = amendments
    qualifying = [c for c in candidates if c["terms"]["qualifies"]]
    best = max(qualifying or candidates, key=lambda c: c["terms"]["expected_return_per_dollar"])
    if held:
        return {**base, "outcome": "DECLINED", "reason": "GAME_ALREADY_HOLDS_A_POSITION",
                "detail": "One NHL position per game per era (any side); this game already holds one.",
                "best": best, "bankroll": bankroll}
    if not qualifying:
        from app.services.shared_daily_allowance import budget_only, zero_room_detail, zero_room_reason

        # Value that qualified but was sized to $0 by the shared allowance alone is a
        # budget decline (fully entered, or held for other MLB/NHL games), never a no-edge.
        budget = [c for c in candidates if budget_only(c["terms"], POLICY.min_adjusted_edge)]
        if budget:
            best = max(budget, key=lambda c: c["terms"]["expected_return_per_dollar"])
            return {**base, "outcome": "DECLINED", "reason": zero_room_reason(shared),
                    "detail": zero_room_detail(shared, "NHL"), "best": best, "bankroll": bankroll}
        return {**base, "outcome": "DECLINED", "reason": "NO_QUALIFYING_EDGE",
                "detail": f"Best side {best['side']} at {best['sportsbook']}: {best['terms']['reason']}.",
                "best": best, "bankroll": bankroll}
    if not getattr(settings, "nhl_paper_decisions_enabled", False):
        return {**base, "outcome": "DECLINED", "reason": "ACTIVATION_CONTROL_OFF",
                "detail": "Every gate passed, but NHL_PAPER_DECISIONS_ENABLED is off; no position is recorded.",
                "selection": best, "bankroll": bankroll}
    return {**base, "outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED", "selection": best, "bankroll": bankroll}


def record(session: Session, era: ShadowExperiment, game: dict[str, Any], decision: dict[str, Any],
           moment: datetime, *, settings: Any = None, clock: Any = None) -> dict[str, Any]:
    """Persist the decision (and, when qualified, the position) in the caller's transaction. Idempotent."""

    # The injected ``clock`` when given (``evaluate`` always passes one), else the caller's
    # ``moment``; never the wall clock behind an injected clock (review HIGH-3).
    moment=_aware(moment) if moment is not None else _aware((clock or _wall_clock)())
    if decision.get('outcome') == 'QUALIFIED':
        refusal=position_guard(session,game,decision.get('selection') or {},moment)
        if refusal:
            decision={**decision,'outcome':'DECLINED','reason':refusal['reason'],'position_guard':refusal}
    prior = session.scalar(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.experiment_id == era.id, ShadowExperimentDecision.game_id == game["game_id"],
        ShadowExperimentDecision.phase == PHASE).order_by(ShadowExperimentDecision.recommended_at.desc()).limit(1))
    if prior is not None:
        position = session.scalar(select(OfficialAspPosition).where(OfficialAspPosition.shadow_decision_id == prior.id))
        prior_reason = (prior.qualification_reasons or {}).get("reason")
        if position is not None or prior_reason == decision["reason"] or prior_reason not in PREREQUISITE_REASONS:
            return _jsonable({**decision, "persisted": "REUSED_EXISTING_DECISION", "decision_id": prior.id,
                              "position_id": position.id if position else None})
    selection = decision.get("selection") or decision.get("best") or {}
    from app.services.owner_funding_restrictions import apply_to_decision
    decision = apply_to_decision(decision, "NHL", moment, settings)
    qualified = decision["outcome"] == "QUALIFIED"
    american = selection.get("american_odds")
    terms = selection.get("terms") or {}
    row = ShadowExperimentDecision(
        experiment_id=era.id, game_id=game["game_id"], slate_date=slate_date(game["starts_at"]), phase=PHASE,
        sport=POLICY.sport, market="MONEYLINE", period=DECISION_PERIOD,
        selection=str(selection.get("side") or "NONE"), sportsbook=selection.get("sportsbook"),
        american_odds=american, decimal_odds=american_to_decimal(int(american)) if american else None,
        market_outcome_id=selection.get("market_outcome_id"), odds_snapshot_id=selection.get("odds_snapshot_id"),
        quote_observed_at=datetime.fromisoformat(selection["observed_at"]) if selection.get("observed_at") else None,
        forecast_generated_at=decision.get("forecast_published_utc"), recommended_at=moment,
        forecast_published_at=decision.get("forecast_published_utc"),
        forecast_imported_at=decision.get("forecast_imported_at"),
        controlling_forecast_identity=decision.get("forecast_id"),
        eligibility_supplement_id=(decision.get("eligibility") or {}).get("record_id"),
        model_probability=selection.get("win_probability"), expected_value=terms.get("expected_return_per_dollar"),
        no_vig_probability=terms.get("no_vig_probability"), edge=terms.get("edge"),
        recommendation="QUALIFIED_BET" if qualified else decision["outcome"],
        recommendation_reason=str(decision.get("detail") or decision["reason"]),
        qualification_reasons={"reason": decision["reason"], "outcome": decision["outcome"]},
        staking_detail=_jsonable({"terms": terms, "shared_daily_allowance": decision.get("shared_daily_allowance"),
                                  "bound_by": terms.get("bound_by")}) if qualified else None,
        eligibility_binding=_jsonable(decision.get("forecast_route")),
        source_mode="LIVE", evidence=_jsonable({k: v for k, v in decision.items() if k != "eligibility"}))
    session.add(row)
    session.flush()
    position_id = None
    if qualified and float(terms["stake"]) > 0:
        position = OfficialAspPosition(
            shadow_forecast_id=None, market_outcome_id=selection["market_outcome_id"],
            odds_snapshot_id=selection["odds_snapshot_id"], placed_at=moment, stake=float(terms["stake"]),
            decimal_odds=american_to_decimal(int(american)), result=PositionResult.PENDING,
            status=PositionStatus.OPEN, strategy=era.staking_policy_version, shadow_experiment_id=era.id,
            shadow_decision_id=row.id, bankroll_before_position=round(float(decision.get("bankroll") or 0.0), 2),
            recommendation_policy_version=era.recommendation_policy_version,
            staking_policy_version=era.staking_policy_version)
        session.add(position)
        session.flush()
        position_id = position.id
    return _jsonable({**decision, "persisted": "RECORDED", "decision_id": row.id, "position_id": position_id})


def evaluate(session: Session, settings: Any, *, now: datetime | None = None, clock: Any = None) -> dict[str, Any]:
    """The checkpoint: one recorded outcome per game in its window, each committed under the shared lock.

    ``now`` is the TICK that selects the games in their window (default: the clock). Each game's decision
    moment is ``clock`` (default: the real wall clock), read by ``decide`` after that game's inputs are
    held; the decision and any position are recorded at that moment, never at the tick.
    """

    # An injected ``now`` IS the clock unless a clock is injected too (review HIGH-3).
    clock = clock or ((lambda: now) if now is not None else _wall_clock)
    tick = _aware(now) if now is not None else _aware(clock())
    era = experiment(session)
    if era is None:
        return {"records": 0, "reason": "NHL_PAPER_POLICY_IS_NOT_ACTIVE", "decisions": [],
                "detail": "No NHL paper era exists: opening the NHL account is the owner's decision."}
    decisions = []
    for game in games_at_checkpoint(session, now=tick):
        try:
            decision = decide(session, settings, era, game, tick, clock=clock)
            with session.begin_nested():
                outcome=record(session,era,game,decision,_clock(decision['evaluated_at']),settings=settings,clock=clock)
                if outcome.get('position_id') and outcome.get('persisted') == 'RECORDED':
                    # Re-checked at the decision clock (MAIN 92b50e8d: the tick, then ONE decision
                    # clock), never at a further wall-clock read behind the held inputs.
                    refusal=position_guard(session,game,decision.get('selection') or {},_clock(decision['evaluated_at']))
                    if refusal: raise _LatePosition(refusal)
            decisions.append(outcome)
            session.commit()                       # releases the shared-allowance lock with the write
        except _LatePosition as error:
            session.rollback()
            declined={**decision,'outcome':'DECLINED','reason':error.detail['reason'],'position_guard':error.detail}
            decisions.append(record(session,era,game,declined,_aware(clock()),settings=settings,clock=clock))
            session.commit()
        except Exception as error:
            session.rollback()
            decisions.append({'game_id':str(game['game_id']),'outcome':'BLOCKED',
                'reason':'GAME_EVALUATION_FAILED','error_type':type(error).__name__,'position_id':None})
    return _jsonable({"records": sum(1 for d in decisions if d.get("position_id")),
                      "reason": "CHECKPOINT_EVALUATED" if decisions else "NO_NHL_GAME_AT_ITS_CHECKPOINT",
                      "experiment_id": era.id, "policy_version": POLICY.policy_version,
                      "activation": bool(getattr(settings, "nhl_paper_decisions_enabled", False)),
                      "decisions": decisions})


# -------------------------------------------------------------- settlement --
def source_result(session: Session, game_id: Any) -> NhlGameResult | None:
    """The current (not superseded) source-backed result for the game, or None."""

    from app.nhl.read_service import current_result

    return current_result(list(session.scalars(select(NhlGameResult).where(NhlGameResult.game_id == game_id))))


def _note(result: NhlGameResult) -> str:
    return (f"NHL_SOURCE_RESULT result_sha256={result.result_sha256} decided_in={result.decided_in} "
            f"final={result.final_away_score}-{result.final_home_score} (away-home) winner={result.final_winner}; "
            "moneyline includes overtime and shootout")


def _result_sha(text: str | None) -> str | None:
    """The source result hash a settlement note or adjustment reason was written from (the first one named)."""

    import re

    found = re.search(r"result_sha256=([0-9a-f]{64})", text or "")
    return found.group(1) if found else None


def _side(outcome: MarketOutcome) -> str:
    return str(outcome.outcome_key).split(":")[0].upper()


def settle(session: Session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Settle open NHL positions from source-backed results; void cancelled/postponed; then corrections."""

    from app.bankroll.service import SettlementResult, settle_profit
    from app.services.shadow_experiment_service import ShadowExperimentService

    moment = _aware(now or datetime.now(UTC))
    era = experiment(session)
    if era is None:
        return {"settled": 0, "corrected": 0, "reason": "NHL_PAPER_POLICY_IS_NOT_ACTIVE"}
    running = ShadowExperimentService(session, settings).current_bankroll(era)
    settled, waiting = [], []
    for position in session.scalars(select(OfficialAspPosition).where(
            OfficialAspPosition.shadow_experiment_id == era.id, OfficialAspPosition.status == PositionStatus.OPEN)
            .order_by(OfficialAspPosition.placed_at)):
        if session.scalar(select(PositionSettlement.id).where(PositionSettlement.position_id == position.id)):
            continue                                                  # never settled twice
        outcome = session.get(MarketOutcome, position.market_outcome_id)
        market = session.get(Market, outcome.market_id)
        game = session.get(Game, market.game_id)
        result = None if game.status in VOID_STATUSES else source_result(session, game.id)
        if game.status in VOID_STATUSES:
            verdict, note = PositionResult.VOID, f"NHL_SOURCE_SCHEDULE status={game.status.value}: virtual stake voided"
        elif result is None or not result.final_official:
            waiting.append({"position_id": str(position.id), "game_id": str(game.id),
                            "state": "PLAYED_AWAITING_SOURCE_RESULT" if _aware(game.starts_at) <= moment
                            else "SCHEDULED", "unofficial_result": bool(result and not result.final_official)})
            continue
        else:
            verdict = PositionResult.WIN if _side(outcome) == result.final_winner else PositionResult.LOSS
            note = _note(result)
        profit = round(settle_profit(float(position.stake), decimal_odds=float(position.decimal_odds),
                                     result=SettlementResult(verdict.value)), 2)
        before, running = running, round(running + profit, 2)
        position.result = verdict
        position.status = PositionStatus.VOID if verdict == PositionResult.VOID else PositionStatus.SETTLED
        position.settled_at = moment
        position.payout = round(float(position.stake) + profit, 2)
        session.add(PositionSettlement(
            position_id=position.id, result=verdict, profit_loss=profit, bankroll_before=round(before, 2),
            bankroll_after=running, game_home_score=result.final_home_score if result else None,
            game_away_score=result.final_away_score if result else None, settlement_note=note, settled_at=moment))
        settled.append({"position_id": str(position.id), "result": verdict.name, "profit_loss": profit,
                        "basis": note})
    session.flush()
    corrected = correct(session, era, now=moment)
    return _jsonable({"settled": len(settled), "corrected": len(corrected), "settlements": settled,
                      "corrections": corrected, "awaiting": waiting})


def correct(session: Session, era: ShadowExperiment, *, now: datetime) -> list[dict[str, Any]]:
    """A superseding source result that changes the winner: an adjustment BESIDE the original. Once per result."""

    from app.bankroll.service import SettlementResult, settle_profit

    out = []
    rows = session.execute(
        select(OfficialAspPosition, PositionSettlement)
        .join(PositionSettlement, PositionSettlement.position_id == OfficialAspPosition.id)
        .where(OfficialAspPosition.shadow_experiment_id == era.id,
               PositionSettlement.result.in_([PositionResult.WIN, PositionResult.LOSS]))).all()
    for position, settlement in rows:
        outcome = session.get(MarketOutcome, position.market_outcome_id)
        game = session.get(Game, session.get(Market, outcome.market_id).game_id)
        result = source_result(session, game.id)
        if result is None or not result.final_official or result.result_sha256 in (settlement.settlement_note or ""):
            continue
        adjustments = list(session.scalars(select(PositionSettlementAdjustment).where(
            PositionSettlementAdjustment.position_id == position.id)
            .order_by(PositionSettlementAdjustment.adjusted_at)))
        if any(result.result_sha256 in a.reason for a in adjustments):
            continue                                                  # this result was already applied
        # MAIN 4fd NHL_PAPER_3: the basis of a correction is the preceding APPLIED adjustment when there
        # is one -- its result, profit AND scores together -- otherwise the original settlement.
        basis = adjustments[-1] if adjustments else None
        previous = basis.corrected_result if basis else settlement.result.name
        previous_profit = float(basis.corrected_profit_loss if basis else settlement.profit_loss)
        previous_home = basis.corrected_home_score if basis else settlement.game_home_score
        previous_away = basis.corrected_away_score if basis else settlement.game_away_score
        basis_sha = _result_sha(basis.reason if basis else settlement.settlement_note)
        corrected = PositionResult.WIN if _side(outcome) == result.final_winner else PositionResult.LOSS
        if corrected.name == previous:
            continue                                                  # a score-only correction: same result
        corrected_profit = round(settle_profit(float(position.stake), decimal_odds=float(position.decimal_odds),
                                               result=SettlementResult(corrected.value)), 2)
        session.add(PositionSettlementAdjustment(
            position_id=position.id, original_settlement_id=settlement.id, game_state_observation_id=None,
            previous_result=str(previous), corrected_result=corrected.name,
            previous_profit_loss=round(previous_profit, 2), corrected_profit_loss=corrected_profit,
            profit_loss_delta=round(corrected_profit - previous_profit, 2),
            previous_home_score=previous_home, previous_away_score=previous_away,
            corrected_home_score=result.final_home_score, corrected_away_score=result.final_away_score,
            reason=(f"{_note(result)}; corrects basis_result_sha256={basis_sha} "
                    f"({'the preceding applied adjustment' if basis else 'the original settlement'}). "
                    "The original settlement is retained."), adjusted_at=now))
        session.flush()
        out.append({"position_id": str(position.id), "previous": str(previous), "corrected": corrected.name,
                    "result_sha256": result.result_sha256})
    return out


def run_checkpoint(session: Session, settings: Any, *, now: datetime | None = None,
                   exchange_root: Any = None, clock: Any = None) -> dict[str, Any]:
    """The scheduled owner: decisions for due games, then source results, then settlement. Commits its own work."""

    from app.nhl.results_consumer import consume_results

    decisions = evaluate(session, settings, now=now, clock=clock)
    try:
        results = consume_results(session, exchange_root, now=now)
        settlement = settle(session, settings, now=now)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"records": decisions.get("records", 0), "decisions": decisions, "results": results,
            "settlement": settlement}


__all__ = ["AS_OF_CONTRACT", "CHANNEL", "DECISION_PERIOD", "IN_PROCESS_AUTHORITY", "IN_PROCESS_CONFIGURATION_SHA256",
           "IN_PROCESS_COORDINATOR_DECISION", "IN_PROCESS_ELIGIBILITY_CONTRACT", "IN_PROCESS_ELIGIBLE",
           "IN_PROCESS_EVIDENCE_MODE", "IN_PROCESS_EVIDENCE_STATUS", "IN_PROCESS_MODEL_VERSION", "MARKET_PERIOD",
           "PHASE", "ROUTE_IN_PROCESS", "ROUTE_PRODUCER", "correct", "decide", "eligibility_record", "evaluate",
           "games_at_checkpoint", "held_inputs", "in_process_eligibility", "native_pairs", "pregame_forecast",
           "record", "run_checkpoint", "settle", "source_result"]


def position_guard(session: Session, game: dict[str, Any], selection: dict[str, Any], moment: datetime):
    from app.nhl.odds_api_price import effective_start
    row=session.execute(select(Game.starts_at,Game.status).where(Game.id==game['game_id'])).first()
    if row is None or row.status != GameStatus.SCHEDULED: return {'reason':'GAME_NO_LONGER_SCHEDULED'}
    canonical=_aware(row.starts_at)
    start=effective_start(session,game['game_id'],canonical,moment=moment)
    if not start-WINDOW_OPENS <= moment <= start-WINDOW_CLOSES:
        return {'reason':'AFTER_DECISION_CUTOFF','operational_start':start.isoformat()}
    pairs,_=native_pairs(session,game['game_id'],moment=moment,starts_at=canonical,original_start=canonical)
    expected=selection.get('legs') or {}
    matches=[p for p in pairs if all(str(p[s+'_snapshot_id'])==str((expected.get(s) or {}).get('snapshot_id'))
                                   for s in ('home','away'))]
    if not matches: return {'reason':'QUOTE_EVENT_CLOCK_MISMATCH'}
    pair=matches[0]
    age=(moment-datetime.fromisoformat(pair['pair_observed_at'])).total_seconds()/60
    if not 0 <= age <= POLICY.max_quote_age_minutes: return {'reason':'QUOTE_TOO_STALE'}
    return None

class _LatePosition(Exception):
    def __init__(self,detail): self.detail=detail
