"""NHL paper lifecycle, end to end, on an isolated in-memory database. TEST FIXTURE ONLY.

Nothing here is production evidence: every forecast, quote, eligibility record,
account and result is invented in a throwaway SQLite database to exercise the
code path forecast -> native quote pair -> qualification -> shared $100 funding
-> position -> source-backed settlement (OT/SO, correction, void) and a
duplicate-safe replay, plus the negative cases that must stay refused.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.nhl import contract as c
from app.nhl import paper
from app.nhl.gen1_import import ImportSummary, import_forecast_bytes, import_result_bytes
from app.nhl.odds_api_price import bind_events, persist
from app.services.research_paper_policy import NHL
from tests import nhl_fixtures as fx

START = datetime(2026, 10, 20, 23, 0, tzinfo=UTC)          # 7:00 PM ET
DECIDE = START - timedelta(minutes=20)                      # inside T-30..T-15
MODEL = {"modelId": "NHL_GEN1_REGULATION_POISSON", "modelVersion": "1.0.0",
         "configurationSha256": "3f7981aac2bfee6f3d05b7e1d099a3342cc9057d09e13b09aaf4c81b7f720417",
         "trainingCutoffUtc": None, "handoffSha256": None}
MISSING = ["HOME_GOALIE_UNKNOWN", "AWAY_GOALIE_UNKNOWN", "LINEUPS_UNAVAILABLE", "INJURIES_UNAVAILABLE",
           "MARKET_PRICES_NOT_USED"]


def _evaluate(session, settings, *, now):
    """A checkpoint tick at ``now`` whose real decision clock (read after the inputs are held) also reads ``now``."""

    return paper.evaluate(session, settings, now=now, clock=lambda: now)


def _settings(settings, **overrides):
    return settings.model_copy(update={"shared_daily_allowance_effective_from": "2026-10-10T20:30:00Z",
                                       "nhl_paper_decisions_enabled": True, **overrides})


def _forecast(session, source_id="2026020240", home="TOR", away="MTL", start=START, home_mu=3.4, away_mu=2.5,
              published=None, imported=None):
    game = fx.game_block(source_id, home, away, start)
    doc = fx.forecast(game, created=start - timedelta(hours=10), home_mu=home_mu, away_mu=away_mu, ot_home=0.55,
                      goalies={"home": fx.goalie("UNKNOWN"), "away": fx.goalie("UNKNOWN")}, missing=list(MISSING),
                      published=published or start - timedelta(hours=9))
    doc["model"] = dict(MODEL)
    doc = c.with_identity(doc)
    raw = c.canonical_bytes(doc)
    disposition = import_forecast_bytes(session, raw, summary=ImportSummary(run_id="fixture"), admit_unbound_games=True,
                                        now=imported or start - timedelta(hours=9))
    assert disposition in ("IMPORTED", "REVISED")
    from app.models.nhl_research import NhlResearchForecast
    return session.query(NhlResearchForecast).filter_by(forecast_id=doc["forecastId"]).one(), game


def _eligibility(session, forecast, published=START - timedelta(hours=2), imported=None, **frozen_override):
    from app.models import ResearchSupplement
    from app.services.nfl_paper_eligibility import KIND, experiment_key

    frozen = {"forecastId": forecast.forecast_id, "homeWinProbability": forecast.final_home_probability,
              "awayWinProbability": forecast.final_away_probability, "tieProbability": 0.0,
              "executionAllowed": False, "modelPromotionAllowed": False,
              "configurationIdentity": NHL.controlling_configuration, **frozen_override}
    sha = hashlib.sha256(forecast.forecast_id.encode() + repr(frozen).encode() + repr(published).encode()).hexdigest()
    session.add(ResearchSupplement(
        supplement_sha256=sha, supplement_id=f"fixture-eligibility-{sha[:8]}", contract_version="AISP_RESEARCH_SUPPLEMENT_V1",
        kind=KIND, sport="NHL", event_id=forecast.canonical_game_ref, forecast_id=forecast.forecast_id,
        artifact_version=NHL.eligibility_contract, simulation_kind="NONE", published_utc=published,
        imported_at=imported or published + timedelta(minutes=1),
        evidence={"binding": {"eventId": forecast.canonical_game_ref, "engineId": "NHL_GEN1_FINAL_TWO_WAY",
                              "forecastId": forecast.forecast_id, "modelArtifactSha256": MODEL["configurationSha256"],
                              "configurationIdentity": NHL.controlling_configuration},
                  "governance": {"executionAllowedInAisp": False, "modelPromotionAllowed": False,
                                 "wageringAuthority": False, "researchShadowOnly": True}},
        payload={"summaries": {"authorization": {
            "experimentId": experiment_key(NHL.policy_version, NHL.sport, NHL.season_id),
            "policyVersion": NHL.policy_version, "mode": "VIRTUAL_RESEARCH_ONLY",
            "consumerVirtualExperimentAuthorized": True, "realMoneyAuthorized": False},
            "frozenConfiguration": frozen,
            "probabilitySemantics": {"settlementMustBeExplicit": True}}}))
    session.flush()


def _quotes(session, *, home=-150, away=+135, clock=DECIDE - timedelta(minutes=5), start=START, markets=None,
            home_name="Toronto Maple Leafs", away_name="Montreal Canadiens", event_id="evt-tor-mtl", received=None):
    outcomes = [SimpleNamespace(name=home_name, price=home), SimpleNamespace(name=away_name, price=away)]
    market = SimpleNamespace(key="h2h", last_update=clock, outcomes=outcomes, clock_source="MARKET")
    book = SimpleNamespace(key="draftkings", title="DraftKings", last_update=clock,
                           markets=markets if markets is not None else [market])
    event = SimpleNamespace(source_event_id=event_id, home_team=home_name, away_team=away_name,
                            commence_time=start, bookmakers=[book])
    received = received or clock + timedelta(seconds=40)
    bound, receipts = bind_events(session, [event], received_at=received)
    return persist(session, bound, requested_at=received - timedelta(seconds=2), received_at=received), receipts


def _activate(session, settings):
    from app.services.research_paper_activation import NHL_STANDING_AUTHORITY, activate

    return activate(session, settings, NHL, now=datetime(2026, 10, 12, 12, 0, tzinfo=UTC),
                    provenance=NHL_STANDING_AUTHORITY)


def _result(session, game, *, reg, decided, ot=None, so=None, supersedes=None):
    doc = fx.result(game, reg=reg, decided=decided, ot=ot, so=so, observed=START + timedelta(hours=3))
    if supersedes:
        doc["supersedesResultSha256"] = supersedes
        doc = c.with_result_identity({**doc, "resultSha256": None})
    assert import_result_bytes(session, c.canonical_bytes(doc), summary=ImportSummary(run_id="fixture-result"),
                               now=START + timedelta(hours=4)) == "IMPORTED"
    session.flush()
    return doc["resultSha256"]


def _ready(session, settings):
    s = _settings(settings)
    forecast, game = _forecast(session)
    _eligibility(session, forecast)
    stored, receipts = _quotes(session)
    assert stored["created"] == 2 and receipts[0]["reason"] == "BOUND"
    _activate(session, s)
    session.commit()
    return s, forecast, game


# ------------------------------------------------------------- the lifecycle --
def test_forecast_quote_funding_position_settlement_correction_and_replay(session, settings):
    from app.models import (OfficialAspPosition, PositionSettlement, PositionSettlementAdjustment,
                            ShadowExperimentDecision)
    from app.services.shadow_experiment_service import ShadowExperimentService

    s, forecast, game = _ready(session, settings)
    out = _evaluate(session, s, now=DECIDE)
    decision = out["decisions"][0]
    assert decision["outcome"] == "QUALIFIED", decision.get("detail")
    sel = decision["selection"]
    assert sel["channel"] == "FINAL_TWO_WAY_INCLUDING_OT_SO" and sel["terms"]["expected_return_per_dollar"] > 0
    assert sel["win_probability"] == pytest.approx(forecast.final_home_probability if sel["side"] == "HOME"
                                                   else forecast.final_away_probability)
    shared = decision["shared_daily_allowance"]
    assert shared["slate_date"] == "2026-10-20" and shared["entered_usd"] == 0.0 and shared["room_usd"] == 100.0
    row = session.get(ShadowExperimentDecision, uuid.UUID(decision["decision_id"]))
    assert row.sport == "NHL" and row.period == paper.DECISION_PERIOD
    assert row.staking_detail["shared_daily_allowance"]["room_usd"] == 100.0 and row.staking_detail["bound_by"]
    # A producer-published forecast keeps the producer eligibility route, named in the evidence.
    assert row.evidence["forecast_route"]["route"] == "PRODUCER_ELIGIBILITY"
    assert row.evidence["forecast_route"]["contract"] == NHL.eligibility_contract
    assert row.eligibility_binding["state"] == "ELIGIBLE" and row.eligibility_supplement_id
    position = session.get(OfficialAspPosition, uuid.UUID(decision["position_id"]))
    assert 0 < float(position.stake) <= NHL.max_stake

    # Replay at a later tick: the same decision and position, nothing new.
    again = _evaluate(session, s, now=DECIDE + timedelta(minutes=5))
    assert again["decisions"][0]["persisted"] == "REUSED_EXISTING_DECISION"
    assert session.query(OfficialAspPosition).count() == 1 and session.query(ShadowExperimentDecision).count() == 1

    # Played, no result yet: nothing settles; the position is reported as awaiting its result.
    pending = paper.settle(session, s, now=START + timedelta(hours=3))
    assert pending["settled"] == 0 and pending["awaiting"][0]["state"] == "PLAYED_AWAITING_SOURCE_RESULT"

    # Source-backed result decided in OVERTIME: the final (OT included) winner settles the moneyline.
    side = sel["side"]
    first = _result(session, game, reg=(2, 2), decided="OVERTIME", ot=(1, 0) if side == "HOME" else (0, 1))
    assert ShadowExperimentService(session, s).settle()["experiments"][NHL.policy_version]["owner"] == \
        "app.nhl.paper.settle"                                     # the generic owner leaves NHL alone
    done = paper.settle(session, s, now=START + timedelta(hours=5))
    assert done["settled"] == 1 and done["settlements"][0]["result"] == "WIN"
    assert "decided_in=OVERTIME" in done["settlements"][0]["basis"] and first in done["settlements"][0]["basis"]
    assert paper.settle(session, s, now=START + timedelta(hours=6))["settled"] == 0   # duplicate-safe
    assert session.query(PositionSettlement).count() == 1

    # A superseding source result flips the winner: one correction beside the original, never twice.
    _result(session, game, reg=(2, 2), decided="SHOOTOUT", so=(0, 1) if side == "HOME" else (1, 0), supersedes=first)
    corrected = paper.settle(session, s, now=START + timedelta(days=1))
    assert corrected["corrected"] == 1 and corrected["corrections"][0]["corrected"] == "LOSS"
    assert paper.settle(session, s, now=START + timedelta(days=2))["corrected"] == 0
    assert session.query(PositionSettlementAdjustment).count() == 1
    assert session.query(PositionSettlement).one().result.name == "WIN"    # the original is retained


def test_a_cancelled_game_voids_its_position(session, settings):
    from app.models import Game, GameStatus

    s, forecast, _game = _ready(session, settings)
    position_id = _evaluate(session, s, now=DECIDE)["decisions"][0]["position_id"]
    # What app.nhl.schedule writes for a source CANCELLED schedule entry.
    session.get(Game, forecast.game_id).status = GameStatus.CANCELED
    session.flush()
    out = paper.settle(session, s, now=START + timedelta(hours=4))
    assert out["settled"] == 1 and out["settlements"][0]["result"] == "VOID"
    assert out["settlements"][0]["position_id"] == position_id and out["settlements"][0]["profit_loss"] == 0


# ---------------------------------------------------------------- refusals --
def test_without_eligibility_or_a_price_or_an_era_nothing_is_written(session, settings):
    from app.models import OfficialAspPosition

    s = _settings(settings)
    forecast, _ = _forecast(session)
    assert _evaluate(session, s, now=DECIDE)["reason"] == "NHL_PAPER_POLICY_IS_NOT_ACTIVE"
    _activate(session, s)
    session.commit()
    first = _evaluate(session, s, now=DECIDE)["decisions"][0]
    assert first["reason"] == "AWAITING_PRODUCER_ELIGIBILITY"
    _eligibility(session, forecast)
    session.commit()
    second = _evaluate(session, s, now=DECIDE + timedelta(minutes=1))["decisions"][0]
    assert second["reason"] == "NO_TWO_SIDED_QUOTE"
    assert session.query(OfficialAspPosition).count() == 0


def test_a_three_way_or_draw_market_never_prices_the_two_way_moneyline(session, settings):
    from app.models import Market, MarketOutcome, MarketType, OddsSnapshot, Sportsbook

    s = _settings(settings)
    forecast, _ = _forecast(session)
    _eligibility(session, forecast)
    three_way = SimpleNamespace(key="h2h_3_way", last_update=DECIDE - timedelta(minutes=5), outcomes=[
        SimpleNamespace(name="Toronto Maple Leafs", price=+150), SimpleNamespace(name="Draw", price=+330),
        SimpleNamespace(name="Montreal Canadiens", price=+180)])
    stored, _ = _quotes(session, markets=[three_way])
    assert stored["created"] == 0 and stored["quote_receipts"][0]["reason"] == "NOT_THE_TWO_WAY_MARKET"
    # Even a regulation market stored by some other path (HOME/AWAY/DRAW at another period) is refused.
    market = Market(game_id=forecast.game_id, market_type=MarketType.MONEYLINE, period="regulation")
    session.add(market)
    session.flush()
    book = session.query(Sportsbook).first() or Sportsbook(key="fixture-book", name="Fixture", region="us", active=True)
    session.add(book)
    session.flush()
    for key, price in (("HOME:None", 150), ("AWAY:None", 180), ("DRAW:None", 330)):
        outcome = MarketOutcome(market_id=market.id, outcome_key=key, label=key)
        session.add(outcome)
        session.flush()
        session.add(OddsSnapshot(market_outcome_id=outcome.id, sportsbook_id=book.id, observed_at=DECIDE - timedelta(minutes=4),
                                 available_as_of=DECIDE - timedelta(minutes=4), american_odds=price,
                                 decimal_odds=1 + price / 100, implied_probability=100 / (100 + price),
                                 source="THE_ODDS_API", source_mode="LIVE",
                                 raw_payload={"market": "h2h_3_way", "sport_key": "icehockey_nhl"}))
    _activate(session, s)
    session.commit()
    decision = _evaluate(session, s, now=DECIDE)["decisions"][0]
    assert decision["reason"] == "NO_TWO_SIDED_QUOTE"
    assert {q["why"] for q in decision["refused_quotes"]} == {"MARKET_PERIOD:regulation"}


def test_a_stale_pair_is_refused(session, settings):
    s = _settings(settings)
    forecast, _ = _forecast(session)
    _eligibility(session, forecast)
    _quotes(session, clock=DECIDE - timedelta(minutes=NHL.max_quote_age_minutes + 1))
    _activate(session, s)
    session.commit()
    assert _evaluate(session, s, now=DECIDE)["decisions"][0]["reason"] == "QUOTE_TOO_STALE"


def test_an_exhausted_shared_allowance_funds_nothing(session, settings, monkeypatch):
    from app.models import OfficialAspPosition
    from app.services import shared_daily_allowance

    s, _forecast_row, _game = _ready(session, settings)
    real = shared_daily_allowance.entered_detail
    monkeypatch.setattr(shared_daily_allowance, "entered_detail",
                        lambda session_, day: {**real(session_, day), "entered_usd": 100.0, "entries": 7})
    decision = _evaluate(session, s, now=DECIDE)["decisions"][0]
    assert decision["reason"] == "SHARED_ALLOWANCE_EXHAUSTED" and decision["shared_daily_allowance"]["room_usd"] == 0
    assert session.query(OfficialAspPosition).count() == 0


def test_no_shared_allowance_means_no_nhl_funding_and_the_activation_control_is_honoured(session, settings):
    from app.models import OfficialAspPosition

    s, _forecast_row, _game = _ready(session, settings)
    off = s.model_copy(update={"shared_daily_allowance_effective_from": None})
    assert _evaluate(session, off, now=DECIDE)["decisions"][0]["reason"] == "SHARED_ALLOWANCE_NOT_EFFECTIVE"
    inactive = s.model_copy(update={"nhl_paper_decisions_enabled": False})
    assert _evaluate(session, inactive, now=DECIDE + timedelta(minutes=1))["decisions"][0]["reason"] == \
        "ACTIVATION_CONTROL_OFF"
    assert session.query(OfficialAspPosition).count() == 0


def test_eligibility_whose_frozen_probability_is_not_the_final_channel_is_refused(session, settings):
    s = _settings(settings)
    forecast, _ = _forecast(session)
    _eligibility(session, forecast, homeWinProbability=forecast.regulation_home_probability)   # regulation, not final
    _quotes(session)
    _activate(session, s)
    session.commit()
    decision = _evaluate(session, s, now=DECIDE)["decisions"][0]
    assert decision["reason"] == "ELIGIBILITY_RECORD_INVALID" and "final channel" in decision["detail"]


def test_the_nhl_policy_is_defined_but_never_auto_activated():
    from app.services.research_paper_policy import NBA, POLICIES

    assert "NHL" not in POLICIES
    assert (NHL.kelly_fraction, NHL.max_stake, NHL.max_position_percentage, NHL.max_same_game_percentage,
            NHL.min_adjusted_edge, NHL.window_opens, NHL.window_closes, NHL.max_quote_age_minutes) == (
        NBA.kelly_fraction, NBA.max_stake, NBA.max_position_percentage, NBA.max_same_game_percentage,
        NBA.min_adjusted_edge, NBA.window_opens, NBA.window_closes, NBA.max_quote_age_minutes)


# ------------------------------------------------------------------- the job --
def test_the_checkpoint_job_is_off_by_default_and_on_the_decision_lane(settings):
    from app.core.config import Settings
    from app.jobs.lanes import DECISION_CHECKPOINT_LANE, lane_for
    from app.jobs.orchestrator import AVAILABLE_JOBS
    from app.jobs.scheduler import build_scheduler

    assert Settings.model_fields["nhl_paper_checkpoint_enabled"].default is False
    assert Settings.model_fields["nhl_paper_decisions_enabled"].default is False
    assert "evaluate-nhl-paper-checkpoint" in AVAILABLE_JOBS
    assert build_scheduler(settings.model_copy(update={"nhl_paper_checkpoint_enabled": False})).get_job(
        "asp-dc-nhl-paper-checkpoint") is None
    job = build_scheduler(settings.model_copy(update={"nhl_paper_checkpoint_enabled": True})).get_job(
        "asp-dc-nhl-paper-checkpoint")
    assert job is not None and lane_for(job.id) == DECISION_CHECKPOINT_LANE


@pytest.mark.asyncio
async def test_a_manual_run_with_the_checkpoint_off_does_nothing(session, settings):
    from app.jobs.orchestrator import JobOrchestrator
    from app.models import ShadowExperimentDecision

    s, _forecast_row, _game = _ready(session, settings)
    run = await JobOrchestrator(session, s.model_copy(update={"nhl_paper_checkpoint_enabled": False})).run(
        "evaluate-nhl-paper-checkpoint")
    assert run.result["status"] == "DISABLED" and session.query(ShadowExperimentDecision).count() == 0


# --------------------------------------------- results and eligibility from the exchange --
def test_a_published_result_manifest_is_imported_then_settles_the_position(session, settings, tmp_path):
    from app.models.nhl_research import NhlGameResult

    s, _forecast_row, game = _ready(session, settings)
    position_id = _evaluate(session, s, now=DECIDE)["decisions"][0]["position_id"]
    exchange = tmp_path / "nhl-gen1-research-v1"
    fx.write_pointer(exchange, None, None)
    nothing = paper.run_checkpoint(session, s, now=START + timedelta(hours=4), exchange_root=exchange)
    assert nothing["results"]["status"] == "NO_RESULT_MANIFEST_PUBLISHED" and nothing["settlement"]["settled"] == 0
    doc = fx.result(game, reg=(3, 1), decided="REGULATION", observed=START + timedelta(hours=3))
    entry = fx.write_object(exchange, doc, "result.json")
    fx.write_pointer(exchange, None, fx.write_manifest(exchange, c.RESULT_MANIFEST_SCHEMA, [entry],
                                                       START + timedelta(hours=4)))
    out = paper.run_checkpoint(session, s, now=START + timedelta(hours=5), exchange_root=exchange)
    assert out["results"]["results"]["IMPORTED"] == 1 and session.query(NhlGameResult).count() == 1
    assert out["settlement"]["settled"] == 1 and out["settlement"]["settlements"][0]["position_id"] == position_id
    again = paper.run_checkpoint(session, s, now=START + timedelta(hours=6), exchange_root=exchange)
    assert again["results"]["results"]["DUPLICATE"] == 1 and again["settlement"]["settled"] == 0


def _eligibility_document(forecast):
    from app.services.nfl_paper_eligibility import experiment_key

    frozen = {"forecastId": forecast.forecast_id, "homeWinProbability": forecast.final_home_probability,
              "awayWinProbability": forecast.final_away_probability, "tieProbability": 0.0,
              "executionAllowed": False, "modelPromotionAllowed": False, "engineId": NHL.controlling_engine,
              "configurationIdentity": NHL.controlling_configuration,
              "modelArtifactSha256": MODEL["configurationSha256"]}
    cutoff = forecast.document["sourceCutoffUtc"]
    summaries = {
        "authorization": {"experimentId": experiment_key(NHL.policy_version, NHL.sport, NHL.season_id),
                          "policyVersion": NHL.policy_version, "mode": "VIRTUAL_RESEARCH_ONLY",
                          "consumerVirtualExperimentAuthorized": True, "realMoneyAuthorized": False,
                          "realMoneyExecutionAllowed": False, "wageringOutsideAispAllowed": False,
                          "marketScope": list(NHL.markets), "kellyFraction": NHL.kelly_fraction,
                          "positionCapFraction": NHL.max_position_percentage, "initialPositionCapUsd": NHL.max_stake,
                          "sameGameCapFraction": NHL.max_same_game_percentage,
                          "dailyCapFraction": NHL.max_daily_percentage,
                          "minimumAdjustedEdgeFraction": NHL.min_adjusted_edge},
        "frozenConfiguration": frozen,
        "frozenForecast": {"forecastSha256": forecast.object_sha256},
        "probabilitySemantics": {"settlementMustBeExplicit": True},
        "probabilityChannels": {"controllingFinalTwoWay": {"homeWin": frozen["homeWinProbability"],
                                                           "awayWin": frozen["awayWinProbability"], "tie": 0.0}},
        "originalClocks": {"sourceCutoffUtc": cutoff, "forecastCreatedAtUtc": forecast.document["createdAtUtc"],
                           "originalExchangePublishedAtUtc": forecast.document["publishedAtUtc"],
                           "eligibilityPublishedAtUtc": fx.z(START - timedelta(hours=2))},
    }
    binding = {k: frozen[k] for k in ("forecastId", "engineId", "configurationIdentity", "modelArtifactSha256")}
    binding["eventId"] = forecast.canonical_game_ref
    return {"sport": "NHL", "artifactVersion": NHL.eligibility_contract, "binding": binding,
            "sourceCutoffUtc": cutoff, "summaries": summaries}


def test_the_importer_admits_nhl_eligibility_only_on_the_final_two_way_channel(session, settings):
    from app.forecast_exchange import supplements as sup

    forecast, _ = _forecast(session)
    doc = _eligibility_document(forecast)
    sup._verify_paper_eligibility(doc, doc["summaries"])
    sup._bind_nhl_forecast(session, doc, doc["binding"])
    assert "nhl-gen1-research-v1/paper-eligibility" in sup.SUPPLEMENT_ROOTS

    regulation = _eligibility_document(forecast)
    regulation["summaries"]["frozenConfiguration"]["homeWinProbability"] = forecast.regulation_home_probability
    regulation["summaries"]["frozenConfiguration"]["awayWinProbability"] = 1 - forecast.regulation_home_probability
    with pytest.raises(sup.SupplementImportError, match="final home probability"):
        sup._bind_nhl_forecast(session, regulation, regulation["binding"])
    looser = _eligibility_document(forecast)
    looser["summaries"]["authorization"]["initialPositionCapUsd"] = 50.0
    with pytest.raises(sup.SupplementImportError, match="initialPositionCapUsd"):
        sup._verify_paper_eligibility(looser, looser["summaries"])


# ------------------------------------------- MAIN 4fd review: NHL_PAPER_1, _2 and _3 in ONE regression --
def test_main_4fd_as_of_inputs_event_and_start_binding_and_successive_corrections(session, settings):
    """NHL_PAPER_1: newer inputs AISP did not HOLD at the decision never displace older held ones.
    NHL_PAPER_2: mixed provider events and a moved start fail closed. NHL_PAPER_3: successive corrections
    take their previous scores from the preceding applied adjustment; a replay adds nothing."""
    from app.models import (Game, OddsSnapshot, OfficialAspPosition, PositionSettlement,
                            PositionSettlementAdjustment)

    s = _settings(settings)
    late = DECIDE + timedelta(minutes=1)
    # Game A: older forecast / eligibility / pair held in time; newer ones published in time but received late.
    a_old, game_a = _forecast(session)
    a_new, _ = _forecast(session, home_mu=3.0, away_mu=2.9, published=DECIDE - timedelta(minutes=30), imported=late)
    _eligibility(session, a_old)                                                        # held from T-2h+1m
    _eligibility(session, a_old, published=START - timedelta(hours=1), imported=late)   # newer, received late
    _eligibility(session, a_new, published=DECIDE - timedelta(minutes=40))              # binds the late forecast
    _quotes(session)                                                                    # held: received T-24m20s
    _quotes(session, home=-110, away=+100, clock=DECIDE - timedelta(minutes=2), received=late)  # newer, late
    # Game B: the same provider quotes it under two events, and once with no event id at all.
    b, _ = _forecast(session, source_id="2026020241", home="COL", away="DAL")
    _eligibility(session, b)
    _quotes(session, home_name="Colorado Avalanche", away_name="Dallas Stars", event_id="evt-b-1")
    away_leg = next(o for o in session.query(OddsSnapshot).filter(OddsSnapshot.source_event_id == "evt-b-1")
                    if (o.raw_payload or {}).get("side") == "AWAY")
    for event, bump in (("evt-b-2", 5), (None, 10)):
        raw = {**away_leg.raw_payload, "provider_event_id": event}
        session.add(OddsSnapshot(market_outcome_id=away_leg.market_outcome_id, sportsbook_id=away_leg.sportsbook_id,
                                 observed_at=away_leg.observed_at, available_as_of=away_leg.available_as_of,
                                 american_odds=away_leg.american_odds + bump, decimal_odds=away_leg.decimal_odds,
                                 implied_probability=away_leg.implied_probability, source=away_leg.source,
                                 source_event_id=event, source_mode="LIVE", raw_payload=raw))
    # Game C: forecast and quotes for its original start, then the game is moved (still inside the window).
    c, _ = _forecast(session, source_id="2026020242", home="BOS", away="NYR")
    _eligibility(session, c)
    _quotes(session, home_name="Boston Bruins", away_name="New York Rangers", event_id="evt-c")
    session.get(Game, c.game_id).starts_at = START - timedelta(minutes=5)
    _activate(session, s)
    session.commit()

    by_game = {d["game_id"]: d for d in _evaluate(session, s, now=DECIDE)["decisions"]}
    a = by_game[str(a_old.game_id)]
    assert a["outcome"] == "QUALIFIED" and a["forecast_id"] == a_old.forecast_id
    late_forecast = next(e for e in a["forecasts_not_admitted"] if e["forecast_id"] == a_new.forecast_id)
    assert late_forecast["why"] == "NOT_AVAILABLE_AT_DECISION"
    assert [e["why"] for e in a["eligibility"]["excluded"]] == ["NOT_AVAILABLE_AT_DECISION"]
    held = {str(i) for (i,) in session.query(OddsSnapshot.id).filter(OddsSnapshot.source_event_id == "evt-tor-mtl")
            if session.get(OddsSnapshot, i).available_as_of.replace(tzinfo=UTC) <= DECIDE}
    legs = a["selection"]["legs"]
    assert {legs["home"]["snapshot_id"], legs["away"]["snapshot_id"]} <= held and len(held) == 2
    assert all(datetime.fromisoformat(legs[side]["available_as_of"]) <= DECIDE for side in ("home", "away"))
    assert a["selection"]["provider_event_id"] == "evt-tor-mtl"
    assert datetime.fromisoformat(a["selection"]["event_commence_time"]) == START
    assert {q["why"] for q in a["refused_quotes"]} == {"NOT_AVAILABLE_AT_DECISION"}
    b_out = by_game[str(b.game_id)]
    assert b_out["reason"] == "NO_TWO_SIDED_QUOTE"
    whys = {q["why"] for q in b_out["refused_quotes"]}
    assert "EVENT_IDENTITY_MISSING_OR_CONFLICTING" in whys and any(w.startswith("MIXED_PROVIDER_EVENTS") for w in whys)
    assert by_game[str(c.game_id)]["reason"] == "SCHEDULE_DRIFT"
    assert session.query(OfficialAspPosition).count() == 1

    # Settlement, then two successive winner-changing source corrections, then a duplicate replay.
    win_home = a["selection"]["side"] == "HOME"
    r1 = _result(session, game_a, reg=(3, 1) if win_home else (1, 3), decided="REGULATION")
    assert paper.settle(session, s, now=START + timedelta(hours=5))["settlements"][0]["result"] == "WIN"
    r2 = _result(session, game_a, reg=(2, 2), decided="OVERTIME", ot=(0, 1) if win_home else (1, 0), supersedes=r1)
    first = paper.settle(session, s, now=START + timedelta(days=1))
    r3 = _result(session, game_a, reg=(2, 2), decided="SHOOTOUT", so=(1, 0) if win_home else (0, 1), supersedes=r2)
    second = paper.settle(session, s, now=START + timedelta(days=2))
    replay = paper.settle(session, s, now=START + timedelta(days=3))
    assert (first["corrected"], second["corrected"], replay["corrected"]) == (1, 1, 0)
    original = session.query(PositionSettlement).one()
    adj1, adj2 = session.query(PositionSettlementAdjustment).order_by(PositionSettlementAdjustment.adjusted_at).all()
    assert original.result.name == "WIN" and f"result_sha256={r1}" in original.settlement_note       # retained
    assert (adj1.previous_result, adj1.corrected_result, adj2.previous_result, adj2.corrected_result) == (
        "WIN", "LOSS", "LOSS", "WIN")
    assert (adj1.previous_home_score, adj1.previous_away_score) == (original.game_home_score, original.game_away_score)
    assert (adj2.previous_home_score, adj2.previous_away_score) == (adj1.corrected_home_score, adj1.corrected_away_score)
    assert float(adj2.previous_profit_loss) == float(adj1.corrected_profit_loss)
    assert f"result_sha256={r2}" in adj1.reason and f"basis_result_sha256={r1}" in adj1.reason
    assert f"result_sha256={r3}" in adj2.reason and f"basis_result_sha256={r2}" in adj2.reason
    assert round(float(adj1.profit_loss_delta) + float(adj2.profit_loss_delta), 2) == 0.0


# ------------------------------------------- AISP in-process forecasts (coordinator decision 2026-10-16) --
#: TEST FIXTURE ONLY: the provenance shape the in-process generator writes; every hash here is invented.
IN_PROCESS_PROVENANCE = {
    "producer": "AISP_IN_PROCESS_NHL_GEN1_V1", "fixture": "TEST FIXTURE ONLY -- invented hashes",
    "code_sha256": {"nhl_gen1.py": "d" * 64, "selected_model.json": paper.IN_PROCESS_CONFIGURATION_SHA256},
    "schedule": {"url": "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/nhl_schedules/"
                        "nhl_schedule_2027.csv", "sha256": "e" * 64, "release_digest": "sha256:" + "e" * 64},
    "license_check": "FIXTURE", "generator_git_revision": "fixture",
    # The generator's measured rest completeness (AISP_NHL_IN_PROCESS_REST_COMPLETE_V1); eligibility needs True.
    "restComplete": True}


def _in_process_forecast(session, *, source_id="2026020250", home="TOR", away="MTL", start=START,
                         published=START - timedelta(hours=3), imported=None,
                         configuration=paper.IN_PROCESS_CONFIGURATION_SHA256, home_mu=3.4, away_mu=2.5,
                         supersedes=None):
    """One row exactly as the AISP in-process generator writes it (the interface contract), on a game it
    creates through ``identity.ensure_game`` like ``import_manifest(admit_unbound_games=True)`` does."""

    from app.models.nhl_research import NhlResearchForecast
    from app.nhl.identity import ensure_game

    game = fx.game_block(source_id, home, away, start)
    received = published - timedelta(minutes=2)                     # the schedule bytes' receipt clock
    doc = fx.forecast(game, created=published, home_mu=home_mu, away_mu=away_mu, ot_home=0.55,
                      goalies={"home": fx.goalie("UNKNOWN"), "away": fx.goalie("UNKNOWN")}, missing=list(MISSING),
                      published=published, supersedes=supersedes)
    doc["model"] = {**MODEL, "configurationSha256": configuration}
    doc["sourceCutoffUtc"] = doc["sourceRetrievedAtUtc"] = fx.z(received)
    doc["provenance"] = {"evidenceMode": paper.IN_PROCESS_EVIDENCE_MODE, "producerEvidenceRef": IN_PROCESS_PROVENANCE,
                         "goalieFeatureMode": "TEAM_LEVEL"}
    doc = c.with_identity(doc)
    raw = c.canonical_bytes(doc)
    imported = imported or published + timedelta(seconds=5)
    probs, goals = doc["probabilities"], doc["goals"]
    row = NhlResearchForecast(
        forecast_id=doc["forecastId"], object_sha256=c.sha256_hex(raw), object_bytes=len(raw), manifest_sha256=None,
        forecast_schema=doc["schema"], supersedes_forecast_id=supersedes, model_id=MODEL["modelId"],
        model_version=MODEL["modelVersion"], configuration_sha256=configuration,
        canonical_game_ref=game["canonicalGameId"], source_game_id=game["sourceGameId"],
        game_id=ensure_game(session, game), season=game["season"], game_type=game["gameType"],
        scheduled_start_utc=start, home_canonical_team_ref=game["home"]["canonicalTeamId"],
        away_canonical_team_ref=game["away"]["canonicalTeamId"],
        regulation_home_probability=probs["regulation"]["home"], regulation_tie_probability=probs["regulation"]["tie"],
        regulation_away_probability=probs["regulation"]["away"], final_home_probability=probs["final"]["home"],
        final_away_probability=probs["final"]["away"], overtime_probability=probs["overtime"]["pOvertime"],
        shootout_probability=probs["overtime"]["pShootout"],
        regulation_expected_home_goals=goals["regulation"]["expectedHome"],
        regulation_expected_away_goals=goals["regulation"]["expectedAway"],
        final_expected_home_goals=goals["final"]["expectedHome"],
        final_expected_away_goals=goals["final"]["expectedAway"],
        home_goalie_status="UNKNOWN", away_goalie_status="UNKNOWN", goalies=doc["goalies"],
        missing_inputs=doc["missingInputs"], evidence_mode=paper.IN_PROCESS_EVIDENCE_MODE,
        goalie_feature_mode="TEAM_LEVEL", producer_evidence_ref=IN_PROCESS_PROVENANCE,
        producer_evidence_status=paper.IN_PROCESS_EVIDENCE_STATUS, record_class="FORECAST", document=doc,
        source_cutoff_utc=received, source_retrieved_utc=received, forecast_created_utc=published,
        published_utc=published, publication_clock_source="FORECAST_PUBLISHED_AT",
        available_as_of=imported, imported_at=imported, research_only=True)
    session.add(row)
    session.flush()
    return row, game


def test_an_in_process_forecast_qualifies_funds_and_settles_on_its_own_eligibility_route(session, settings):
    from app.models import OfficialAspPosition, ResearchSupplement, ShadowExperimentDecision

    s = _settings(settings)
    forecast, game = _in_process_forecast(session)
    stored, receipts = _quotes(session)
    assert stored["created"] == 2 and receipts[0]["reason"] == "BOUND"     # the generator's game binds the price
    _activate(session, s)
    session.commit()
    # The scheduled checkpoint selects the game the generator created.
    assert [g["game_id"] for g in paper.games_at_checkpoint(session, now=DECIDE)] == [forecast.game_id]

    decision = _evaluate(session, s, now=DECIDE)["decisions"][0]
    assert decision["outcome"] == "QUALIFIED", decision.get("detail")
    route = decision["forecast_route"]
    assert (route["route"], route["state"], route["contract"]) == (
        "AISP_IN_PROCESS", "AISP_IN_PROCESS_ELIGIBLE", "AISP_NHL_IN_PROCESS_ELIGIBILITY_V1")
    assert route["authority_reference"] == paper.IN_PROCESS_AUTHORITY
    assert route["coordinator_decision"] == "CLAUDE_COORDINATOR_NHL_SIMPLIFICATION_2026-10-16"
    assert route["configuration_sha256"] == paper.IN_PROCESS_CONFIGURATION_SHA256 and route["violations"] == []
    assert session.query(ResearchSupplement).count() == 0                  # no producer record consulted or minted
    sel = decision["selection"]
    assert sel["win_probability"] == pytest.approx(forecast.final_home_probability if sel["side"] == "HOME"
                                                   else forecast.final_away_probability)
    assert sel["terms"]["edge"] >= NHL.min_adjusted_edge and 0 < sel["terms"]["stake"] <= NHL.max_stake

    row = session.get(ShadowExperimentDecision, uuid.UUID(decision["decision_id"]))
    assert row.recommendation == "QUALIFIED_BET" and row.controlling_forecast_identity == forecast.forecast_id
    assert row.eligibility_supplement_id is None
    assert row.evidence["forecast_route"]["route"] == "AISP_IN_PROCESS"
    assert row.eligibility_binding["authority_reference"] == paper.IN_PROCESS_AUTHORITY
    assert row.evidence["as_of"]["contract"] == paper.AS_OF_CONTRACT
    position = session.get(OfficialAspPosition, uuid.UUID(decision["position_id"]))
    assert 0 < float(position.stake) <= NHL.max_stake and position.shadow_decision_id == row.id

    side = sel["side"]
    _result(session, game, reg=(3, 1) if side == "HOME" else (1, 3), decided="REGULATION")
    done = paper.settle(session, s, now=START + timedelta(hours=5))
    assert done["settled"] == 1 and done["settlements"][0]["result"] == "WIN"


def test_a_later_in_process_rerun_after_t30_never_displaces_the_forecast_held_at_t30(session, settings):
    s = _settings(settings)
    held, _ = _in_process_forecast(session)                                          # published T-3h
    late, _ = _in_process_forecast(session, home_mu=2.6, away_mu=3.3, published=START - timedelta(minutes=29),
                                   supersedes=held.forecast_id)                      # after T-30, before the tick
    _quotes(session)
    _activate(session, s)
    session.commit()
    decision = _evaluate(session, s, now=DECIDE)["decisions"][0]
    assert decision["outcome"] == "QUALIFIED" and decision["forecast_id"] == held.forecast_id
    assert [(e["forecast_id"], e["why"]) for e in decision["forecasts_not_admitted"]] == [
        (late.forecast_id, "IN_PROCESS_PUBLISHED_AFTER_T30")]


@pytest.mark.parametrize("case", ["published_after_t30", "imported_after_the_decision", "configuration_differs",
                                  "start_moved", "rest_incomplete", "rest_flag_missing"])
def test_an_in_process_forecast_is_refused_unless_every_contract_clock_and_pin_holds(session, settings, case):
    from app.models import Game, OfficialAspPosition, ShadowExperimentDecision

    s = _settings(settings)
    forecast, _ = _in_process_forecast(session, **{
        "published_after_t30": {"published": START - timedelta(minutes=29)},
        "imported_after_the_decision": {"imported": DECIDE + timedelta(seconds=1)},
        "configuration_differs": {"configuration": "0" * 64},
        "start_moved": {}, "rest_incomplete": {}, "rest_flag_missing": {}}[case])
    # A day-ahead row whose rest the generator measured incomplete, or a row that never says (review MAJOR
    # 2026-10-16): only restComplete exactly True is admissible.
    if case == "rest_incomplete":
        forecast.producer_evidence_ref = {**IN_PROCESS_PROVENANCE, "restComplete": False}
    elif case == "rest_flag_missing":
        forecast.producer_evidence_ref = {k: v for k, v in IN_PROCESS_PROVENANCE.items() if k != "restComplete"}
    _quotes(session)
    current_start = START
    if case == "start_moved":                       # moved after it was forecast and priced; still in the window
        current_start = START + timedelta(minutes=5)
        session.get(Game, forecast.game_id).starts_at = current_start
    _activate(session, s)
    session.commit()

    decision = _evaluate(session, s, now=DECIDE)["decisions"][0]
    reason, why, gate = {
        "published_after_t30": ("NO_FORECAST", "IN_PROCESS_PUBLISHED_AFTER_T30",
                                "PUBLISHED_AFTER_T30_OF_ORIGINAL_START"),
        "imported_after_the_decision": ("NO_FORECAST", "NOT_AVAILABLE_AT_DECISION", "NOT_HELD_AT_DECISION_MOMENT"),
        "configuration_differs": ("ELIGIBILITY_RECORD_INVALID", None, "CONFIGURATION_SHA256:"),
        "start_moved": ("SCHEDULE_DRIFT", "ORIGINAL_START_IS_NOT_THE_CURRENT_START",
                        "ORIGINAL_START_IS_NOT_THE_CURRENT_START"),
        "rest_incomplete": ("ELIGIBILITY_RECORD_INVALID", None, "REST_FEATURES_NOT_COMPLETE_AT_GENERATION"),
        "rest_flag_missing": ("ELIGIBILITY_RECORD_INVALID", None, "REST_FEATURES_NOT_COMPLETE_AT_GENERATION")}[case]
    assert (decision["outcome"], decision["reason"]) == ("BLOCKED", reason)
    if why:
        assert [e["why"] for e in decision["forecasts_not_admitted"]] == [why]
    else:
        assert decision["forecast_route"]["route"] == "AISP_IN_PROCESS"
        assert decision["forecast_route"]["state"] == "ELIGIBILITY_RECORD_INVALID"
        assert any(v.startswith(gate) for v in decision["forecast_route"]["violations"])
    # The contract gate itself refuses the row, whichever upstream filter caught it first.
    verdict = paper.in_process_eligibility(forecast, moment=DECIDE, starts_at=current_start)
    assert not verdict["eligible"] and any(v.startswith(gate) for v in verdict["violations"]), verdict["violations"]
    assert session.query(OfficialAspPosition).count() == 0
    assert session.query(ShadowExperimentDecision).one().qualification_reasons["reason"] == reason


# ------------------------------------------ MAIN 92b50e8d: NHL_PAPER_1 held visibility, completed --
@pytest.mark.parametrize("clock_reads", [DECIDE + timedelta(minutes=3), START - timedelta(minutes=14)],
                         ids=["inside-the-window", "after-T-15"])
def test_main_92b50e8d_a_quote_received_before_the_tick_but_committed_after_inputs_are_held_is_never_admitted(
        session, settings, clock_reads):
    """Response BEFORE the tick, commit AFTER it: the receipt clock is old, so with the tick as the decision
    moment a later read would admit it. Now the inputs are held first and the decision clock is read
    afterwards; that later clock is the decision moment, used only if still inside T-30..T-15."""
    from sqlalchemy import event

    from app.models import OddsSnapshot, OfficialAspPosition, ShadowExperimentDecision

    s = _settings(settings)
    _in_process_forecast(session)
    _quotes(session)                                      # committed before the tick: book DECIDE-5m, receipt -4m20s
    _activate(session, s)
    session.commit()
    held_ids = {i for (i,) in session.query(OddsSnapshot.id)}

    statements: list[str] = []
    engine = session.get_bind()

    def log(_conn, _cursor, statement, *_args):
        statements.append(statement)

    def clock():
        statements.append("<<DECISION CLOCK READ>>")
        # The provider answered BEFORE the tick (receipt DECIDE-1m) but this transaction commits only now.
        # A far better price for the home side, so admitting it could not go unnoticed.
        statements.append("<<LATE WRITER>>")
        _quotes(session, home=+250, away=-300, clock=DECIDE - timedelta(seconds=90),
                received=DECIDE - timedelta(minutes=1))
        session.commit()
        statements.append("<<LATE WRITER COMMITTED>>")
        return clock_reads

    event.listen(engine, "before_cursor_execute", log)
    try:
        out = paper.evaluate(session, s, now=DECIDE, clock=clock)
    finally:
        event.remove(engine, "before_cursor_execute", log)
    decision = out["decisions"][0]
    late_ids = {i for (i,) in session.query(OddsSnapshot.id)} - held_ids
    assert len(late_ids) == 2                             # the late rows exist and are visible now ...
    mark, done = statements.index("<<DECISION CLOCK READ>>"), statements.index("<<LATE WRITER COMMITTED>>")
    before = " ".join(statements[:mark]).lower()
    assert all(t in before for t in ("nhl_research_forecasts", "research_supplements", "odds_snapshots"))
    after = [q.lower() for q in statements[done + 1:]]
    assert not [q for q in after if q.lstrip().startswith("select") and ("odds_snapshots" in q
                or "nhl_research_forecasts" in q or "research_supplements" in q)]   # ... but never read again
    assert decision["as_of"]["tick_moment"] == DECIDE.isoformat()
    assert decision["as_of"]["decision_moment"] == decision["evaluated_at"] == clock_reads.isoformat()
    row = session.query(ShadowExperimentDecision).one()
    assert row.recommended_at.replace(tzinfo=UTC) == clock_reads                   # never the earlier tick

    if clock_reads > START - NHL.window_closes:
        assert (decision["outcome"], decision["reason"]) == ("BLOCKED", "AFTER_DECISION_CUTOFF")
        assert session.query(OfficialAspPosition).count() == 0
        return
    assert decision["outcome"] == "QUALIFIED"
    legs = decision["selection"]["legs"]
    assert {legs["home"]["snapshot_id"], legs["away"]["snapshot_id"]} == {str(i) for i in held_ids}
    assert not {str(i) for i in late_ids} & {str(q.get("snapshot_id")) for q in decision.get("refused_quotes", [])}
    assert decision["selection"]["american_odds"] in (-150, 135)                  # the held prices, not +250/-300
    # Upstream clocks are kept as delivered beside the decision clock, never relabelled.
    receipt = DECIDE - timedelta(minutes=5) + timedelta(seconds=40)        # _quotes: book clock + 40s
    assert datetime.fromisoformat(legs["home"]["available_as_of"]) == receipt
    position = session.query(OfficialAspPosition).one()
    assert position.placed_at.replace(tzinfo=UTC) == clock_reads


@pytest.mark.asyncio
async def test_the_scheduled_checkpoint_decides_an_in_process_game_on_the_real_clock(session, settings, monkeypatch):
    """The production path: the job passes no clock, so the tick and the decision clock are the wall clock."""
    from app.jobs.orchestrator import JobOrchestrator
    from app.nhl import results_consumer

    s = _settings(settings, nhl_paper_checkpoint_enabled=True)
    forecast, _ = _in_process_forecast(session)
    _quotes(session)
    _activate(session, s)
    session.commit()
    reads = iter([DECIDE, DECIDE + timedelta(seconds=2)])                     # tick, then the decision clock
    monkeypatch.setattr(paper, "_wall_clock", lambda: next(reads))
    monkeypatch.setattr(results_consumer, "consume_results", lambda *_a, **_k: {"status": "FIXTURE_NO_RESULTS"})
    run = await JobOrchestrator(session, s).run("evaluate-nhl-paper-checkpoint")
    decision = run.result["decisions"]["decisions"][0]
    assert decision["outcome"] == "QUALIFIED" and decision["forecast_id"] == forecast.forecast_id
    assert decision["as_of"]["tick_moment"] == DECIDE.isoformat()
    assert decision["evaluated_at"] == (DECIDE + timedelta(seconds=2)).isoformat()
    assert decision["forecast_route"]["route"] == "AISP_IN_PROCESS" and decision["position_id"]


# --------------------------------------------------- opening the NHL era --
def test_opening_the_nhl_era_records_its_real_authority_and_nba_cfb_keep_their_words(session, settings):
    from app.models import ShadowExperiment
    from app.models.experiment_accounts import ExperimentAccountFunding, ExperimentAccountMembership
    from app.services.research_paper_activation import (
        NHL_STANDING_AUTHORITY, ActivationProvenance, activate, activate_all,
    )
    from app.services.research_paper_policy import POLICIES

    moment = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)
    opened = activate_all(session, settings, now=moment)
    assert "NHL" not in POLICIES and sorted(o["sport"] for o in opened) == ["CFB", "NBA"]
    assert session.query(ShadowExperiment).filter(ShadowExperiment.sport == "NHL").count() == 0
    assert {f.executed_by for f in session.query(ExperimentAccountFunding)} == {
        "nba-activation/project-owner-approval-2026-09-15", "cfb-activation/project-owner-approval-2026-09-15"}
    assert all(o["decision_authority"] == "REQUIRES_PRODUCER_ELIGIBILITY" and "provenance" not in o for o in opened)
    nba = session.query(ShadowExperiment).filter(ShadowExperiment.sport == "NBA").one()
    assert nba.policy_config["approval"]["granted"] == "2026-09-15" and "activation" not in nba.policy_config

    nhl = activate(session, settings, NHL, now=moment, provenance=NHL_STANDING_AUTHORITY)
    funding = session.get(ExperimentAccountFunding, uuid.UUID(nhl["funding_id"]))
    assert funding.executed_by == "nhl-activation/james-standing-authority-2026-10-15/claude-coordinator"
    assert paper.IN_PROCESS_AUTHORITY in funding.note and paper.IN_PROCESS_COORDINATOR_DECISION in funding.note
    assert "2026-09-15" not in funding.note
    era = session.get(ShadowExperiment, uuid.UUID(nhl["era_id"]))
    assert era.policy_config["approval"]["authority"] == paper.IN_PROCESS_AUTHORITY
    assert era.policy_config["approval"]["coordinator_decision"] == paper.IN_PROCESS_COORDINATOR_DECISION
    assert era.policy_config["authority"]["status"] == "OPENED_UNDER_RECORDED_APPROVAL"
    assert era.policy_config["activation"]["executed_by"] == funding.executed_by
    membership = session.query(ExperimentAccountMembership).filter_by(experiment_id=era.id).one()
    assert paper.IN_PROCESS_AUTHORITY in membership.role_basis
    assert nhl["decision_authority"] == "REQUIRES_PRODUCER_OR_AISP_IN_PROCESS_ELIGIBILITY"
    again = activate(session, settings, NHL, now=moment, provenance=NHL_STANDING_AUTHORITY)
    assert (again["funded_now"], again["era_created"], again["linked_now"]) == (False, False, False)
    with pytest.raises(ValueError):
        ActivationProvenance(executed_by="x" * 81, note="n", role_basis="r")


def test_the_nhl_stake_funding_window_and_freshness_terms_are_unchanged():
    from app.services.shared_daily_allowance import SHARED_DAILY_LIMIT_USD

    assert (NHL.kelly_fraction, NHL.max_stake, NHL.min_adjusted_edge, NHL.window_opens, NHL.window_closes,
            NHL.max_quote_age_minutes, NHL.permitted_quote_sources, SHARED_DAILY_LIMIT_USD) == (
        0.25, 5.00, 0.02, timedelta(minutes=30), timedelta(minutes=15), 240, ("THE_ODDS_API",), 100.00)
    assert (paper.WINDOW_OPENS, paper.WINDOW_CLOSES) == (NHL.window_opens, NHL.window_closes)


def test_decision_strings_fit_the_production_column_lengths():
    """SQLite ignores VARCHAR lengths; Postgres does not (2026-10-17 00:30Z: period was 41 chars > 40)."""
    from app.models import ShadowExperimentDecision
    limits = {c.name: c.type.length for c in ShadowExperimentDecision.__table__.columns
              if getattr(c.type, "length", None)}
    for column, value in (("period", paper.DECISION_PERIOD), ("market", "MONEYLINE"), ("sport", "NHL"),
                          ("phase", paper.PHASE), ("recommendation", "QUALIFIED_BET")):
        assert len(value) <= limits[column], (column, value, limits[column])
