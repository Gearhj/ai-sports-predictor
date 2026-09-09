"""Multi-sport foundation (Phase 1) — SPORT_REGISTRY_V1 and the all-sports Today hub.

Proves the registry contract, the fail-closed adapter boundary, the
qualified/awaiting/provisional separation, sport-isolated bankrolls, the
research-only NFL invariant, and the zero-provider public-read boundary.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.time import local_today
from app.models import Game, GameStatus, GameTeam, League, Sport, Team
from app.sports import registry as registry_module
from app.sports.contracts import (
    AWAITING_STATES,
    PROVISIONAL_STATES,
    QUALIFIED_STATES,
    NormalizedDailyDecisionState as DState,
    NormalizedFundingState as FState,
    NormalizedSelection,
    SPORT_REGISTRY_VERSION,
    SportAvailabilityState,
    SportCode,
    TODAY_READ_MODEL_VERSION,
)
from app.sports.read_service import (
    TodayAllSportsReadService,
    is_supported_filter,
    supported_league_filters,
)
from app.sports.registry import (
    SPORT_REGISTRY,
    active_leagues,
    get_league,
    navigable_leagues,
    planned_leagues,
    registry_manifest,
    resolve_stored_identity,
)

TODAY = date(2026, 8, 31)


# -- 1. SPORT_REGISTRY_V1 ----------------------------------------------------

def test_registry_contains_mlb_and_nfl_and_keeps_planned_sports_inactive():
    leagues = {str(entry.league_code) for entry in SPORT_REGISTRY}
    assert {"MLB", "NFL", "NBA", "WNBA", "UFC"} <= leagues

    mlb = get_league("MLB")
    assert mlb.sport_code == SportCode.BASEBALL
    assert mlb.availability == SportAvailabilityState.LIVE
    assert mlb.official_positions_supported is True
    assert mlb.navigable is True
    assert mlb.navigation_href == "/mlb"

    nfl = get_league("NFL")
    assert nfl.sport_code == SportCode.FOOTBALL
    assert nfl.availability == SportAvailabilityState.RESEARCH_SHADOW
    # NFL may never fund an official position in this phase.
    assert nfl.official_positions_supported is False
    assert nfl.navigable is True
    assert nfl.navigation_href == "/nfl"

    planned = {str(e.league_code) for e in planned_leagues()}
    assert planned == {"NBA", "WNBA", "UFC"}
    for entry in planned_leagues():
        assert entry.availability == SportAvailabilityState.PLANNED
        assert entry.navigable is False
        assert entry.navigation_href is None
        assert entry.official_positions_supported is False

    assert {str(e.league_code) for e in active_leagues()} == {"MLB", "NFL"}
    assert SPORT_REGISTRY_VERSION == "SPORT_REGISTRY_V1"
    assert len(registry_manifest()) == len(SPORT_REGISTRY)


# -- 12. PLANNED sports never reach live navigation --------------------------

def test_planned_sports_are_not_navigable_or_filterable():
    navigable = {str(e.league_code) for e in navigable_leagues()}
    assert navigable == {"MLB", "NFL"}
    assert supported_league_filters() == ["ALL", "MLB", "NFL"]
    for planned in ("NBA", "WNBA", "UFC"):
        assert is_supported_filter(planned) is False
    assert is_supported_filter("ALL") is True
    assert is_supported_filter("MLB") is True
    assert is_supported_filter("NFL") is True
    assert is_supported_filter("NHL") is False  # never registered


def test_stored_identity_resolution_maps_history_without_renaming_it():
    # `sports.code` stores 'NFL' (a league-shaped value) for football history.
    assert resolve_stored_identity("NFL", None).sport_code == SportCode.FOOTBALL
    # MLB history stores sport 'BASEBALL', league 'MLB'.
    assert resolve_stored_identity("BASEBALL", "MLB").league_code.value == "MLB"
    assert resolve_stored_identity(None, "MLB").sport_code == SportCode.BASEBALL
    # Ambiguous or unknown identities fail closed rather than defaulting.
    assert resolve_stored_identity("BASKETBALL", None) is None
    assert resolve_stored_identity("CRICKET", "IPL") is None


# -- 2 / 14. public reads make zero provider calls ---------------------------

def _forbid_provider_construction(monkeypatch) -> list[str]:
    """Trap every provider/client construction boundary used by public reads."""

    import httpx
    try:
        import requests
    except ImportError:  # requests is not an application dependency
        requests = None

    from app.data_sources.mlb import MLBStatsAPIAdapter
    from app.data_sources.odds import TheOddsAPIAdapter
    from app.data_sources.statcast import BaseballSavantStatcastAdapter
    from app.data_sources.weather import OpenMeteoAdapter
    from app.prediction_markets.kalshi import KalshiPredictionMarketSource
    from app.prediction_markets.polymarket import PolymarketPredictionMarketSource
    from app.prospective.sportsdataio import SportsDataIOAvailabilityAdapter
    from app.services.nfl_shadow_odds_service import NFLShadowOddsService

    attempts: list[str] = []

    def forbidden(boundary: str):
        def fail(*_args, **_kwargs):
            attempts.append(boundary)
            raise AssertionError(f"Public read crossed provider boundary: {boundary}")

        return fail

    constructors = (
        httpx.Client,
        httpx.AsyncClient,
        TheOddsAPIAdapter,
        PolymarketPredictionMarketSource,
        KalshiPredictionMarketSource,
        MLBStatsAPIAdapter,
        BaseballSavantStatcastAdapter,
        SportsDataIOAvailabilityAdapter,
        NFLShadowOddsService,
        OpenMeteoAdapter,
    )
    for provider_class in constructors:
        monkeypatch.setattr(
            provider_class, "__init__", forbidden(provider_class.__name__),
        )
    if requests is not None:
        monkeypatch.setattr(
            requests.Session, "__init__", forbidden("requests.Session"),
        )
    return attempts


def test_today_read_makes_no_provider_call(session, settings, monkeypatch):
    import httpx

    def _no_network(*args, **kwargs):
        raise AssertionError("the all-sports Today read attempted a provider call")

    monkeypatch.setattr(httpx.AsyncClient, "get", _no_network)
    monkeypatch.setattr(httpx.Client, "get", _no_network, raising=False)
    monkeypatch.setattr(httpx.AsyncClient, "post", _no_network, raising=False)
    monkeypatch.setattr(httpx.Client, "post", _no_network, raising=False)

    model = TodayAllSportsReadService(session, settings).build(TODAY)
    assert model.schema_version == TODAY_READ_MODEL_VERSION
    assert model.data_freshness.provider_calls_made == 0


def test_public_today_route_performs_no_http_call(client, monkeypatch):
    import httpx

    def _no_network(*args, **kwargs):
        raise AssertionError("the /today route attempted a provider call")

    monkeypatch.setattr(httpx.AsyncClient, "get", _no_network)

    response = client.get("/api/v1/today")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == TODAY_READ_MODEL_VERSION
    assert body["registry_version"] == SPORT_REGISTRY_VERSION
    assert body["data_freshness"]["provider_calls_made"] == 0
    assert body["overall_summary"]["official_position_count"] == len(
        body["qualified_positions"]
    )
    assert "final_qualified_decision_count" in body["overall_summary"]
    # Both V1 compatibility aliases remain serialized with documented legacy
    # semantics while clients migrate to the unambiguous fields.
    assert "final_qualified_position_count" in body["overall_summary"]
    for section in body["sport_sections"]:
        assert "official_position_count" in section
        assert "final_qualified_decision_count" in section
        assert "final_qualified_count" in section


def test_populated_today_read_cannot_construct_or_invoke_any_provider(
    session, settings, monkeypatch,
):
    """Executable proof for the provider-free public-read boundary.

    The persisted fixture forces the MLB adapter through real dashboard/game
    mapping, so this cannot pass merely because an empty slate short-circuits.
    """

    import httpx
    try:
        import requests
    except ImportError:  # requests is not an application dependency
        requests = None

    from app.data_sources.mlb import MLBStatsAPIAdapter
    from app.data_sources.odds import TheOddsAPIAdapter
    from app.data_sources.statcast import BaseballSavantStatcastAdapter
    from app.data_sources.weather import OpenMeteoAdapter
    from app.models import ProviderRequestLedger
    from app.prediction_markets.kalshi import KalshiPredictionMarketSource
    from app.prediction_markets.polymarket import PolymarketPredictionMarketSource
    from app.prospective.sportsdataio import SportsDataIOAvailabilityAdapter
    from app.services.nfl_shadow_odds_service import NFLShadowOddsService

    _seed_mlb_game(session, starts_at=datetime(2026, 8, 31, 23, 5, tzinfo=UTC))

    attempts: list[str] = []

    def forbidden(boundary: str):
        def fail(*_args, **_kwargs):
            attempts.append(boundary)
            raise AssertionError(f"Today read crossed provider boundary: {boundary}")

        return fail

    for client_class in (httpx.Client, httpx.AsyncClient):
        monkeypatch.setattr(client_class, "__init__", forbidden(client_class.__name__))
        for method in ("get", "post", "request", "send"):
            monkeypatch.setattr(
                client_class, method, forbidden(f"{client_class.__name__}.{method}"),
            )
    if requests is not None:
        monkeypatch.setattr(requests.Session, "__init__", forbidden("requests.Session"))
        monkeypatch.setattr(requests.Session, "request", forbidden("requests.Session.request"))
        for method in ("get", "post", "request"):
            monkeypatch.setattr(requests, method, forbidden(f"requests.{method}"))

    for provider_class in (
        TheOddsAPIAdapter,
        PolymarketPredictionMarketSource,
        KalshiPredictionMarketSource,
        MLBStatsAPIAdapter,
        BaseballSavantStatcastAdapter,
        SportsDataIOAvailabilityAdapter,
        NFLShadowOddsService,
        OpenMeteoAdapter,
    ):
        monkeypatch.setattr(
            provider_class, "__init__", forbidden(provider_class.__name__),
        )

    ledger_before = len(list(session.scalars(select(ProviderRequestLedger.id))))
    model = TodayAllSportsReadService(session, settings).build(TODAY)
    ledger_after = len(list(session.scalars(select(ProviderRequestLedger.id))))

    mlb = next(section for section in model.sport_sections if str(section.league_code) == "MLB")
    assert mlb.scheduled_event_count == 1
    assert len(mlb.selections) == 1
    assert attempts == []
    assert ledger_after == ledger_before == 0
    assert model.data_freshness.provider_calls_made == 0


def test_today_route_rejects_planned_sports_and_accepts_active_ones(client):
    for planned in ("NBA", "WNBA", "UFC"):
        rejected = client.get(f"/api/v1/today?sport={planned}")
        assert rejected.status_code == 400
        assert planned in rejected.json()["detail"]
    for active in ("ALL", "MLB", "NFL"):
        assert client.get(f"/api/v1/today?sport={active}").status_code == 200


def test_sports_registry_route_exposes_planned_sports_as_non_navigable(client, monkeypatch):
    # Registry authority is a static public read; even provider/client
    # construction is forbidden, not merely expected to leave a zero ledger.
    attempts = _forbid_provider_construction(monkeypatch)
    body = client.get("/api/v1/sports/registry").json()
    assert body["version"] == SPORT_REGISTRY_VERSION
    assert body["supported_filters"] == ["ALL", "MLB", "NFL"]
    by_league = {row["league_code"]: row for row in body["sports"]}
    assert by_league["NBA"]["availability"] == "PLANNED"
    assert by_league["NBA"]["navigable"] is False
    assert by_league["NBA"]["navigation_href"] is None
    assert by_league["UFC"]["participant_type"] == "INDIVIDUAL"
    assert by_league["MLB"]["navigation_href"] == "/mlb"
    assert attempts == []
    assert by_league["NFL"]["navigation_href"] == "/nfl"


# -- 8. a missing adapter fails closed ---------------------------------------

def test_missing_sport_adapter_fails_closed_as_unavailable(session, settings, monkeypatch):
    from app.sports import read_service as read_service_module

    # Registered league, no adapter: must NOT be reported as a healthy zero.
    monkeypatch.setattr(read_service_module, "_adapter_factory", lambda _league: None)
    model = TodayAllSportsReadService(session, settings).build(TODAY)
    assert {str(s.league_code) for s in model.sport_sections} == {"MLB", "NFL"}
    for section in model.sport_sections:
        assert section.availability_state == SportAvailabilityState.UNAVAILABLE
        assert section.degraded is True
        assert "no read adapter" in (section.degraded_reason or "").lower()
        assert section.can_create_official_positions is False
    assert model.overall_summary.stale_or_degraded_sport_count == 2


def test_failing_adapter_fails_closed_instead_of_reporting_zero(session, settings, monkeypatch):
    from app.sports import read_service as read_service_module

    class Boom:
        def __init__(self, *args, **kwargs):
            pass

        def build_section(self, event_date, *, now=None):
            raise RuntimeError("adapter exploded")

    monkeypatch.setattr(read_service_module, "_adapter_factory", lambda _league: Boom)
    model = TodayAllSportsReadService(session, settings).build(TODAY)
    for section in model.sport_sections:
        assert section.availability_state == SportAvailabilityState.UNAVAILABLE
        assert "failed" in (section.degraded_reason or "").lower()


def test_one_adapter_import_failure_isolated_to_that_sport(
    client, session, monkeypatch,
):
    from app.models import ProviderRequestLedger
    from app.sports import read_service as read_service_module

    provider_attempts = _forbid_provider_construction(monkeypatch)
    real_import_module = read_service_module.import_module

    def fail_mlb_import(name: str):
        if name == "app.sports.adapters.mlb":
            raise ImportError("simulated MLB adapter import failure")
        return real_import_module(name)

    monkeypatch.setattr(read_service_module, "import_module", fail_mlb_import)
    logged: list[str] = []

    def capture_exception(message: str, *args: object, **_kwargs: object) -> None:
        logged.append(message % args)

    monkeypatch.setattr(read_service_module.logger, "exception", capture_exception)
    before = len(list(session.scalars(select(ProviderRequestLedger.id))))

    response = client.get("/api/v1/today?date=2026-08-31")

    assert response.status_code == 200
    sections = {row["league_code"]: row for row in response.json()["sport_sections"]}
    assert sections["MLB"]["availability_state"] == "UNAVAILABLE"
    assert sections["MLB"]["degraded"] is True
    assert (
        sections["MLB"]["degraded_reason"]
        == "MLB read adapter failed: ImportError: simulated MLB adapter import failure"
    )
    assert sections["NFL"]["availability_state"] == "RESEARCH_SHADOW"
    assert logged == [
        "MLB read adapter failed: ImportError: simulated MLB adapter import failure"
    ]
    assert provider_attempts == []
    assert len(list(session.scalars(select(ProviderRequestLedger.id)))) == before == 0


# -- 3 / 5 / 6 / 7 / 13. section routing from normalized states --------------

def _selection(**overrides) -> NormalizedSelection:
    base = dict(
        sport_code=SportCode.BASEBALL,
        league_code="MLB",
        event_id="evt-1",
        event_label="AWAY @ HOME",
        decision_state=DState.FINAL_QUALIFIED,
        funding_state=FState.OFFICIAL_VIRTUAL_POSITION,
        can_ever_fund_official_position=True,
        official_position_id="official-position-1",
    )
    base.update(overrides)
    if (
        "official_position_id" not in overrides
        and base["funding_state"] != FState.OFFICIAL_VIRTUAL_POSITION
    ):
        base["official_position_id"] = None
    return NormalizedSelection(**base)


def _model_with(session, settings, selections, monkeypatch):
    """Build the read model from a stubbed single-sport adapter."""
    from app.sports import read_service as read_service_module
    from app.sports.contracts import SportSection
    from app.sports.registry import MLB

    class Stub:
        def __init__(self, *args, **kwargs):
            pass

        def build_section(self, event_date, *, now=None):
            return SportSection(
                sport_code=MLB.sport_code,
                league_code=MLB.league_code,
                display_name=MLB.display_name,
                availability_state=MLB.availability,
                scheduled_event_count=len(selections),
                can_create_official_positions=True,
                selections=list(selections),
            )

    monkeypatch.setattr(
        read_service_module,
        "_adapter_factory",
        lambda league: Stub if league == "MLB" else None,
    )
    return TodayAllSportsReadService(session, settings).build(TODAY, league_filter="MLB")


def test_final_qualified_position_appears_only_in_qualified_today(session, settings, monkeypatch):
    qualified = _selection(event_id="q1", virtual_stake=12.5)
    model = _model_with(session, settings, [qualified], monkeypatch)
    assert [i.event_id for i in model.qualified_positions] == ["q1"]
    assert model.awaiting_final_evidence == []
    assert model.provisional_opportunities == []
    assert model.overall_summary.official_position_count == 1
    assert model.overall_summary.final_qualified_decision_count == 1
    # Stable V1 compatibility alias: historically this means funded positions.
    assert model.overall_summary.final_qualified_position_count == 1
    assert model.sport_sections[0].official_position_count == 1
    assert model.sport_sections[0].final_qualified_decision_count == 1
    # Stable V1 compatibility alias: historically this means final decisions.
    assert model.sport_sections[0].final_qualified_count == 1
    assert model.overall_summary.total_virtual_stake == 12.5
    assert model.overall_summary.sports_with_official_positions == ["MLB"]


def test_final_qualified_without_a_frozen_position_is_not_in_qualified_today(
    session, settings, monkeypatch,
):
    eligible_only = _selection(
        event_id="q-unfunded",
        funding_state=FState.ELIGIBLE_NOT_FUNDED,
        virtual_stake=None,
    )
    model = _model_with(session, settings, [eligible_only], monkeypatch)
    assert model.qualified_positions == []
    assert model.overall_summary.official_position_count == 0
    assert model.overall_summary.final_qualified_decision_count == 1
    assert model.overall_summary.final_qualified_position_count == 0
    assert model.sport_sections[0].official_position_count == 0
    assert model.sport_sections[0].final_qualified_decision_count == 1
    assert model.sport_sections[0].final_qualified_count == 1
    assert model.overall_summary.total_virtual_stake == 0.0


def test_official_funding_label_without_position_identity_fails_closed(
    session, settings, monkeypatch,
):
    unproven = _selection(event_id="missing-position-id", official_position_id=None)
    model = _model_with(session, settings, [unproven], monkeypatch)

    assert model.qualified_positions == []
    assert model.overall_summary.official_position_count == 0
    assert model.overall_summary.final_qualified_decision_count == 1
    assert model.sport_sections[0].official_position_count == 0


def test_final_decision_and_official_position_counts_reconcile_without_conflation(
    session, settings, monkeypatch,
):
    official = _selection(
        event_id="official", experiment_id="v1", policy_version="policy-v1",
        official_position_id="position-1",
    )
    unfunded = _selection(
        event_id="unfunded", funding_state=FState.ELIGIBLE_NOT_FUNDED,
        experiment_id="v21", policy_version="policy-v2-1",
    )

    model = _model_with(session, settings, [official, unfunded], monkeypatch)
    section = model.sport_sections[0]

    assert len(model.qualified_positions) == 1
    assert model.qualified_positions[0].official_position_id == "position-1"
    assert model.overall_summary.official_position_count == len(model.qualified_positions) == 1
    assert model.overall_summary.final_qualified_decision_count == 2
    assert section.official_position_count == 1
    assert section.final_qualified_decision_count == 2
    # Deprecated aliases preserve their distinct, documented V1 semantics.
    assert model.overall_summary.final_qualified_position_count == 1
    assert section.final_qualified_count == 2


def test_awaiting_candidates_are_never_shown_as_final_no_bet(session, settings, monkeypatch):
    awaiting = _selection(
        event_id="a1", decision_state=DState.AWAITING_FINAL_EVIDENCE,
        funding_state=FState.ELIGIBLE_NOT_FUNDED,
        outstanding_gates=["Awaiting the final-pregame decision"],
    )
    model = _model_with(session, settings, [awaiting], monkeypatch)
    assert [i.event_id for i in model.awaiting_final_evidence] == ["a1"]
    assert model.qualified_positions == []
    assert model.provisional_opportunities == []
    # An awaiting candidate is not a final decision of any kind.
    assert awaiting.decision_state not in (DState.FINAL_NO_BET, DState.PASS)
    assert awaiting.decision_state in AWAITING_STATES


def test_unsupported_market_is_marked_and_never_qualified(session, settings, monkeypatch):
    unsupported = _selection(
        event_id="u1", decision_state=DState.PROVISIONAL_EDGE,
        funding_state=FState.MARKET_NOT_SUPPORTED,
        market_label="Run line",
        status_note="Market not supported by this experiment (moneyline only)",
    )
    model = _model_with(session, settings, [unsupported], monkeypatch)
    assert model.qualified_positions == []
    assert [i.event_id for i in model.provisional_opportunities] == ["u1"]
    assert model.provisional_opportunities[0].funding_state == FState.MARKET_NOT_SUPPORTED


def test_research_only_selection_can_never_enter_qualified_today(session, settings, monkeypatch):
    # Even if an adapter mislabelled a research item FINAL_QUALIFIED, the read
    # service refuses it because the sport cannot fund a position.
    rogue = _selection(
        event_id="r1", decision_state=DState.FINAL_QUALIFIED,
        funding_state=FState.RESEARCH_ONLY,
        can_ever_fund_official_position=False,
    )
    model = _model_with(session, settings, [rogue], monkeypatch)
    assert model.qualified_positions == []


def test_registry_authority_keeps_mislabeled_nfl_out_of_official_positions(
    session, settings, monkeypatch,
):
    from app.sports import read_service as read_service_module
    from app.sports.contracts import SportSection
    from app.sports.registry import NFL

    rogue = NormalizedSelection(
        sport_code=SportCode.FOOTBALL,
        league_code="NFL",
        event_id="nfl-rogue",
        event_label="SF @ LAR",
        decision_state=DState.FINAL_QUALIFIED,
        funding_state=FState.OFFICIAL_VIRTUAL_POSITION,
        can_ever_fund_official_position=True,
        official_position_id="not-authoritative",
    )

    class Stub:
        def __init__(self, *_args, **_kwargs):
            pass

        def build_section(self, event_date, *, now=None):
            return SportSection(
                sport_code=NFL.sport_code,
                league_code=NFL.league_code,
                display_name=NFL.display_name,
                availability_state=NFL.availability,
                can_create_official_positions=True,
                selections=[rogue],
            )

    monkeypatch.setattr(
        read_service_module,
        "_adapter_factory",
        lambda league: Stub if league == "NFL" else None,
    )
    model = TodayAllSportsReadService(session, settings).build(
        TODAY, league_filter="NFL",
    )

    assert model.qualified_positions == []
    assert model.overall_summary.official_position_count == 0
    assert model.overall_summary.final_qualified_decision_count == 1
    assert model.sport_sections[0].official_position_count == 0


def test_one_canonical_selection_key_cannot_occupy_contradictory_sections(
    session, settings, monkeypatch,
):
    identity = {
        "event_id": "shared-event",
        "market_label": "Moneyline",
        "selection_label": "HOME",
        "experiment_id": "experiment-v1",
        "policy_version": "policy-v1",
    }
    selections = [
        _selection(**identity),
        _selection(
            **identity, decision_state=DState.AWAITING_FINAL_EVIDENCE,
            funding_state=FState.ELIGIBLE_NOT_FUNDED,
        ),
        _selection(
            **identity, decision_state=DState.WATCH,
            funding_state=FState.ELIGIBLE_NOT_FUNDED,
        ),
    ]
    model = _model_with(session, settings, selections, monkeypatch)
    assert model.qualified_positions == []
    assert model.awaiting_final_evidence == []
    assert model.provisional_opportunities == []
    section = model.sport_sections[0]
    assert section.availability_state == SportAvailabilityState.UNAVAILABLE
    assert section.selections == []
    assert "duplicate canonical selection identity" in (section.degraded_reason or "")


# -- 11. no cross-sport raw-edge ranking -------------------------------------

def test_no_cross_sport_ranking_is_produced(session, settings, monkeypatch):
    from app.sports import read_service as read_service_module
    from app.sports.contracts import SportSection
    from app.sports.registry import MLB, NFL

    mlb_item = _selection(event_id="m1", decision_state=DState.PROVISIONAL_EDGE,
                          funding_state=FState.ELIGIBLE_NOT_FUNDED, adjusted_edge=3.0)
    nfl_item = NormalizedSelection(
        sport_code=SportCode.FOOTBALL, league_code="NFL", event_id="n1",
        event_label="AWAY @ HOME", decision_state=DState.PROVISIONAL_EDGE,
        funding_state=FState.RESEARCH_ONLY, adjusted_edge=99.0,
        can_ever_fund_official_position=False,
    )

    def factories():
        class MlbStub:
            def __init__(self, *a, **k): pass
            def build_section(self, event_date, *, now=None):
                return SportSection(
                    sport_code=MLB.sport_code, league_code=MLB.league_code,
                    display_name="MLB", availability_state=MLB.availability,
                    can_create_official_positions=True, selections=[mlb_item],
                )

        class NflStub:
            def __init__(self, *a, **k): pass
            def build_section(self, event_date, *, now=None):
                return SportSection(
                    sport_code=NFL.sport_code, league_code=NFL.league_code,
                    display_name="NFL", availability_state=NFL.availability,
                    can_create_official_positions=False, selections=[nfl_item],
                )

        return {"MLB": MlbStub, "NFL": NflStub}

    adapter_factories = factories()
    monkeypatch.setattr(
        read_service_module,
        "_adapter_factory",
        lambda league: adapter_factories.get(league),
    )
    model = TodayAllSportsReadService(session, settings).build(TODAY)

    assert model.cross_sport_ranking == "NOT_APPLICABLE_PHASE_1"
    # Each sport is ranked only within itself: both are rank 1 despite the NFL
    # item's far larger raw edge. No global ordering by edge is emitted.
    ranks = {str(i.league_code): i.sport_rank for i in model.provisional_opportunities}
    assert ranks == {"MLB": 1, "NFL": 1}


# -- 4. NFL research shadow can never become an official position ------------

def test_nfl_adapter_marks_every_selection_research_only(session, settings):
    from app.sports.adapters.nfl import NflTodayAdapter

    section = NflTodayAdapter(session, settings).build_section(TODAY)
    assert section.can_create_official_positions is False
    assert section.official_positions_blocked_reason
    assert section.final_qualified_count == 0
    assert section.final_qualified_decision_count == 0
    assert section.official_position_count == 0
    assert section.open_position_count == 0
    assert section.virtual_stake == 0.0
    for item in section.selections:
        assert item.funding_state == FState.RESEARCH_ONLY
        assert item.can_ever_fund_official_position is False
        assert item.decision_state != DState.FINAL_QUALIFIED


@pytest.mark.parametrize("recommendation", ["QUALIFIED_BET", "QUALIFIED_SHADOW"])
def test_nfl_research_qualification_is_provisional_never_final(session, settings, recommendation):
    """A research 'qualified' recommendation is a research result — it must map
    to a provisional research opportunity, never to a governed qualification."""
    from app.sports.adapters.nfl import NflTodayAdapter

    adapter = NflTodayAdapter(session, settings)
    past_kickoff = datetime(2026, 8, 31, 17, 0, tzinfo=UTC)
    game = {
        "event_id": "nfl-1",
        "scheduled_start_utc": past_kickoff.isoformat(),
        "home_team_id": None,
        "away_team_id": None,
        "engines": [{
            "snapshot_type": "FINAL_PREGAME",
            "forecast_cutoff_utc": past_kickoff.isoformat(),
            "decisions": [{
                "market": "FULL_GAME_MONEYLINE", "selection": "HOME",
                "recommendation": recommendation, "virtual_stake": 25.0,
                "has_quote": True, "american_odds": -120,
            }],
        }],
    }
    selection, _ = adapter._selection(game, past_kickoff + timedelta(hours=1))
    assert selection.decision_state == DState.PROVISIONAL_EDGE
    assert selection.decision_state not in QUALIFIED_STATES
    assert selection.funding_state == FState.RESEARCH_ONLY
    assert selection.can_ever_fund_official_position is False
    # A research stake is never presented as funded exposure.
    assert selection.virtual_stake is None
    assert "no official bankroll" in (selection.status_note or "")


def test_nfl_pre_cutoff_event_is_awaiting_not_pass(session, settings):
    from app.sports.adapters.nfl import NflTodayAdapter

    kickoff = datetime(2026, 8, 31, 23, 0, tzinfo=UTC)
    game = {
        "event_id": "nfl-2", "scheduled_start_utc": kickoff.isoformat(),
        "home_team_id": None, "away_team_id": None,
        "engines": [{
            "snapshot_type": "CURRENT", "forecast_cutoff_utc": kickoff.isoformat(),
            "decisions": [{"market": "FULL_GAME_MONEYLINE", "selection": "HOME",
                           "recommendation": "NO_BET", "has_quote": True}],
        }],
    }
    selection, _ = NflTodayAdapter(session, settings)._selection(game, kickoff - timedelta(hours=3))
    assert selection.decision_state == DState.AWAITING_FINAL_EVIDENCE
    assert selection.decision_state != DState.PASS


def test_nfl_canonical_team_codes_do_not_trigger_uuid_database_lookups(session, settings, monkeypatch):
    from app.sports.adapters.nfl import NflTodayAdapter

    adapter = NflTodayAdapter(session, settings)
    monkeypatch.setattr(
        session,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected UUID lookup")),
    )
    assert adapter._event_label({"away_team_id": "SF", "home_team_id": "LAR"}) == "SF @ LAR"


# -- 3. MLB live mapping ------------------------------------------------------

def _seed_mlb_game(session, *, starts_at: datetime) -> Game:
    sport = session.scalar(select(Sport).where(Sport.code == "BASEBALL"))
    if sport is None:
        sport = Sport(code="BASEBALL", name="Baseball", active=True)
        session.add(sport)
        session.flush()
    league = session.scalar(select(League).where(League.code == "MLB"))
    if league is None:
        league = League(sport_id=sport.id, code="MLB", name="MLB", active=True)
        session.add(league)
        session.flush()

    def team(name, abbr):
        row = Team(league_id=league.id, name=name, abbreviation=abbr, city=name.split()[0], active=True)
        session.add(row)
        session.flush()
        return row

    home, away = team("New York Yankees", "NYY"), team("Boston Red Sox", "BOS")
    game = Game(league_id=league.id, starts_at=starts_at, status=GameStatus.SCHEDULED, source_mode="LIVE")
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    session.commit()
    return game


def test_mlb_game_sport_label_resolves_from_league_not_a_hardcode(session, settings):
    from app.services.read_service import ReadService

    game = _seed_mlb_game(session, starts_at=datetime(2026, 8, 31, 23, 5, tzinfo=UTC))
    assert ReadService(session, settings)._game_sport(game) == "MLB"


def test_mlb_section_reports_live_availability_and_can_fund(session, settings):
    from app.sports.adapters.mlb import MlbTodayAdapter

    _seed_mlb_game(session, starts_at=datetime(2026, 8, 31, 23, 5, tzinfo=UTC))
    section = MlbTodayAdapter(session, settings).build_section(TODAY)
    assert section.availability_state == SportAvailabilityState.LIVE
    assert section.can_create_official_positions is True
    assert section.official_positions_blocked_reason is None
    assert section.scheduled_event_count >= 1
    for item in section.selections:
        assert item.can_ever_fund_official_position is True
        assert str(item.league_code) == "MLB"


# -- 9. Eastern operational date ---------------------------------------------

def test_today_uses_the_eastern_operational_date_not_utc(session, settings):
    """2026-08-30 22:45 America/New_York is 2026-08-31 UTC; the hub must
    resolve the Eastern day."""
    anchor = datetime(2026, 8, 31, 2, 45, tzinfo=UTC)
    assert local_today(settings.display_timezone, anchor) == date(2026, 8, 30)
    model = TodayAllSportsReadService(session, settings).build(
        local_today(settings.display_timezone, anchor), now=anchor,
    )
    assert model.operational_date == date(2026, 8, 30)
    assert model.display_timezone == "America/New_York"


def test_today_survives_dst_boundaries(session, settings):
    # Spring forward (2026-03-08) and fall back (2026-11-01), late evening ET.
    spring = datetime(2026, 3, 9, 3, 30, tzinfo=UTC)      # 11:30 PM EDT Mar 8
    fall = datetime(2026, 11, 2, 4, 30, tzinfo=UTC)       # 11:30 PM EST Nov 1
    assert local_today(settings.display_timezone, spring) == date(2026, 3, 8)
    assert local_today(settings.display_timezone, fall) == date(2026, 11, 1)
    for anchor, expected in ((spring, date(2026, 3, 8)), (fall, date(2026, 11, 1))):
        model = TodayAllSportsReadService(session, settings).build(
            local_today(settings.display_timezone, anchor), now=anchor,
        )
        assert model.operational_date == expected


def test_today_route_honours_an_explicit_date(client):
    body = client.get("/api/v1/today?date=2026-08-30").json()
    assert body["operational_date"] == "2026-08-30"


# -- 10. existing experiment identities are untouched ------------------------

def test_reading_today_preserves_every_experiment_bankroll_and_ledger(session, settings):
    from app.models import (
        Forecast,
        ModelEvaluation,
        ModelVersion,
        OfficialAspPosition,
        PositionSettlement,
        ProviderRequestLedger,
        ShadowBoardDecision,
        ShadowDecisionSettlement,
        ShadowExperiment,
        ShadowExperimentDecision,
        ShadowForecast,
        UserVirtualBankroll,
        VirtualPosition,
    )
    from app.services.practical_policy import SUPERSEDED_POLICY_VERSION
    from app.services.shadow_experiment_service import (
        DECISION_POLICY,
        SUPERSEDED_POLICIES,
        V2_DECISION_POLICY,
        ShadowExperimentService,
    )

    service = ShadowExperimentService(session, settings)
    today = local_today(settings.display_timezone)
    v1 = service.ensure_started(start_date=today, code_commit="deadbeef")
    # Preserve the never-activated V2 registration exactly as production does:
    # it remains immutable evidence while the operational registry excludes it.
    superseded_v2 = ShadowExperiment(
        name="ASP PRACTICAL PREGAME BANKROLL — 2026 V2",
        version=2,
        started_at=datetime.now(UTC),
        start_date=today + timedelta(days=1),
        starting_bankroll=1000.0,
        sport="MLB",
        recommendation_policy_version=SUPERSEDED_POLICY_VERSION,
        staking_policy_version="AISP_QUARTER_KELLY_EVIDENCE_V2",
        decision_policy=SUPERSEDED_POLICY_VERSION,
        controlling_engine="AISP_BASELINE",
        supported_markets=["MONEYLINE"],
        kelly_fraction=0.25,
        unit_percentage=0.005,
        max_position_percentage=0.01,
        max_same_game_percentage=0.02,
        max_daily_percentage=0.05,
        code_commit="cafebabe",
        code_identity="git:cafebabe",
        is_active=True,
    )
    session.add(superseded_v2)
    session.flush()
    v21 = service.ensure_v2_started(
        effective_date=today + timedelta(days=1), code_commit="deadbeef",
    )
    session.commit()

    count_models = {
        "experiment_decisions": ShadowExperimentDecision,
        "research_decisions": ShadowBoardDecision,
        "official_positions": OfficialAspPosition,
        "virtual_positions": VirtualPosition,
        "official_settlements": PositionSettlement,
        "research_settlements": ShadowDecisionSettlement,
        "forecasts": Forecast,
        "research_forecasts": ShadowForecast,
        "provider_ledger": ProviderRequestLedger,
        "model_versions": ModelVersion,
        "model_evaluations": ModelEvaluation,
    }

    def snapshot() -> dict:
        experiments = list(session.scalars(
            select(ShadowExperiment).order_by(ShadowExperiment.version)
        ))
        return {
            "experiment_identity": [
                (
                    str(row.id), row.version, row.name, row.start_date.isoformat(),
                    str(row.starting_bankroll), row.sport,
                    row.recommendation_policy_version, row.staking_policy_version,
                    row.decision_policy, row.controlling_engine,
                    tuple(row.supported_markets), row.code_commit, row.code_identity,
                    row.is_active, row.policy_config,
                )
                for row in experiments
            ],
            "shadow_bankroll_balances": [
                (str(row.id), str(row.starting_bankroll), service.current_bankroll(row))
                for row in experiments
            ],
            "user_bankroll_balances": [
                (
                    str(row.id), str(row.starting_balance),
                    str(row.current_balance), str(row.peak_balance),
                )
                for row in session.scalars(
                    select(UserVirtualBankroll).order_by(UserVirtualBankroll.id)
                )
            ],
            "counts": {
                name: len(list(session.scalars(select(model.id))))
                for name, model in count_models.items()
            },
            "supersession_registry": dict(SUPERSEDED_POLICIES),
        }

    before = snapshot()
    TodayAllSportsReadService(session, settings).build(TODAY)
    TodayAllSportsReadService(session, settings).build(TODAY, league_filter="MLB")
    TodayAllSportsReadService(session, settings).build(TODAY, league_filter="NFL")
    after = snapshot()

    assert before == after
    identity_by_policy = {
        row[8]: row for row in after["experiment_identity"]
    }
    assert identity_by_policy[DECISION_POLICY][0] == str(v1.id)
    assert identity_by_policy[SUPERSEDED_POLICY_VERSION][0] == str(superseded_v2.id)
    assert identity_by_policy[V2_DECISION_POLICY][0] == str(v21.id)
    assert after["supersession_registry"][SUPERSEDED_POLICY_VERSION] == (
        "SUPERSEDED_BEFORE_ACTIVATION"
    )
    assert {balance[2] for balance in after["shadow_bankroll_balances"]} == {1000.0}
    assert after["user_bankroll_balances"] == []
    assert set(after["counts"].values()) == {0}


def test_sport_bankrolls_are_isolated_never_pooled(session, settings, monkeypatch):
    """The hub may summarize across sports, but every dollar keeps its sport
    and experiment identity, and research activity is never counted as funded."""
    from app.sports import read_service as read_service_module
    from app.sports.contracts import SportSection
    from app.sports.registry import MLB, NFL

    funded = _selection(event_id="m1", virtual_stake=10.0,
                        experiment_id="exp-mlb", experiment_name="MLB V1")
    research = NormalizedSelection(
        sport_code=SportCode.FOOTBALL, league_code="NFL", event_id="n1",
        event_label="AWAY @ HOME", decision_state=DState.PROVISIONAL_EDGE,
        funding_state=FState.RESEARCH_ONLY, virtual_stake=None,
        can_ever_fund_official_position=False,
    )

    def factories():
        class MlbStub:
            def __init__(self, *a, **k): pass
            def build_section(self, event_date, *, now=None):
                return SportSection(
                    sport_code=MLB.sport_code, league_code=MLB.league_code,
                    display_name="MLB", availability_state=MLB.availability,
                    can_create_official_positions=True, open_position_count=1,
                    virtual_stake=10.0, selections=[funded],
                )

        class NflStub:
            def __init__(self, *a, **k): pass
            def build_section(self, event_date, *, now=None):
                return SportSection(
                    sport_code=NFL.sport_code, league_code=NFL.league_code,
                    display_name="NFL", availability_state=NFL.availability,
                    can_create_official_positions=False, open_position_count=0,
                    virtual_stake=0.0, selections=[research],
                )

        return {"MLB": MlbStub, "NFL": NflStub}

    adapter_factories = factories()
    monkeypatch.setattr(
        read_service_module,
        "_adapter_factory",
        lambda league: adapter_factories.get(league),
    )
    model = TodayAllSportsReadService(session, settings).build(TODAY)

    # Only MLB contributes funded exposure; NFL research is excluded entirely.
    assert model.overall_summary.total_virtual_stake == 10.0
    assert model.overall_summary.sports_with_official_positions == ["MLB"]
    assert model.overall_summary.open_position_count == 1
    # Every qualified dollar retains its sport + experiment identity.
    for item in model.qualified_positions:
        assert item.experiment_id and item.experiment_name
        assert str(item.league_code) == "MLB"
    nfl_section = next(s for s in model.sport_sections if str(s.league_code) == "NFL")
    assert nfl_section.virtual_stake == 0.0
    assert nfl_section.can_create_official_positions is False


def test_availability_lists_every_registered_sport_with_navigability(session, settings):
    model = TodayAllSportsReadService(session, settings).build(TODAY)
    entries = {str(a.league_code): a for a in model.availability}
    assert set(entries) == {"MLB", "NFL", "NBA", "WNBA", "UFC"}
    assert entries["MLB"].navigable is True
    assert entries["NFL"].navigable is True
    for planned in ("NBA", "WNBA", "UFC"):
        assert entries[planned].navigable is False
        assert entries[planned].availability == SportAvailabilityState.PLANNED
