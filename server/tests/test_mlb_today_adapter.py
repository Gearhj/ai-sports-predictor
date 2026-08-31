"""Direct acceptance coverage for the provider-free MLB Today adapter."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.models import (
    ExperimentDecisionPhase,
    OfficialAspPosition,
    PositionResult,
    PositionStatus,
    ShadowExperiment,
    ShadowExperimentDecision,
)
from app.sports.adapters.mlb import MARKET_NOT_SUPPORTED_NOTE, MlbTodayAdapter
from app.sports.contracts import (
    NormalizedDailyDecisionState as DState,
    NormalizedFundingState as FState,
    SportSection,
)
from app.sports.read_service import TodayAllSportsReadService
from app.sports.registry import MLB
from app.services.shadow_experiment_service import (
    DECISION_POLICY,
    FINAL_PREGAME_CUTOFF_MINUTES,
    SUPERSEDED_POLICIES,
    V2_DECISION_POLICY,
)


FIRST_PITCH = datetime(2026, 8, 31, 23, 10, tzinfo=UTC)
TODAY = date(2026, 8, 31)


def _team(abbreviation: str) -> SimpleNamespace:
    return SimpleNamespace(abbreviation=abbreviation, name=abbreviation)


def _game(
    *,
    start_time: datetime | None = FIRST_PITCH,
    forecast_available: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid4()),
        start_time=start_time,
        forecast_available=forecast_available,
        away_team=_team("BOS"),
        home_team=_team("NYY"),
    )


def _price(
    *,
    market_type: str = "MONEYLINE",
    selection: str = "NYY",
    american_odds: int | None = -115,
    stale: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        market_type=market_type,
        selection=selection,
        line=None,
        american_odds=american_odds,
        sportsbook="Book A" if american_odds is not None else None,
        model_probability=58.2,
        no_vig_probability=51.1,
        is_stale=stale,
        quote_observed_at=FIRST_PITCH - timedelta(minutes=20),
    )


def _opportunity(
    state: str = "QUALIFIED_BET",
    *,
    best: SimpleNamespace | None = None,
    alternates: list[SimpleNamespace] | None = None,
    v2: SimpleNamespace | None = None,
    reason: str = "Current board assessment.",
) -> SimpleNamespace:
    return SimpleNamespace(
        recommendation_state=state,
        recommendation_reason=reason,
        qualification_reasons=["Awaiting governed final evidence"],
        best=_price() if best is None else best,
        alternates=alternates or [],
        v2=v2,
        # These display fields must never be accepted as ledger authority.
        experiment_status="FROZEN",
        experiment_stake=99.0,
    )


def _experiment(
    session,
    *,
    policy: str = DECISION_POLICY,
    version: int = 1,
    active: bool = True,
) -> ShadowExperiment:
    experiment = ShadowExperiment(
        name=f"MLB experiment {version}",
        version=version,
        started_at=FIRST_PITCH - timedelta(days=1),
        start_date=TODAY,
        starting_bankroll=1000.0,
        sport="MLB",
        recommendation_policy_version=policy,
        staking_policy_version=f"STAKING_{version}",
        decision_policy=policy,
        controlling_engine="AISP_BASELINE",
        supported_markets=["MONEYLINE"],
        kelly_fraction=0.25,
        unit_percentage=0.01,
        max_position_percentage=0.01,
        max_same_game_percentage=0.02,
        max_daily_percentage=0.05,
        code_commit="a" * 40,
        code_identity=f"git:{'a' * 40}",
        is_active=active,
        policy_config=None,
    )
    session.add(experiment)
    session.flush()
    return experiment


def _decision(
    session,
    experiment: ShadowExperiment,
    game_id: str,
    *,
    recommendation: str,
    recommended_at: datetime | None = None,
    with_quote: bool = True,
    evidence: dict | None = None,
    source_mode: str = "LIVE",
    market: str = "MONEYLINE",
    selection: str = "NYY",
) -> ShadowExperimentDecision:
    decision = ShadowExperimentDecision(
        experiment_id=experiment.id,
        game_id=UUID(game_id),
        slate_date=TODAY,
        phase=ExperimentDecisionPhase.FINAL_PREGAME,
        sport="BASEBALL_MLB",
        market=market,
        period="Full game",
        selection=selection if with_quote else "—",
        line=None,
        sportsbook="Book A" if with_quote else None,
        american_odds=-115 if with_quote else None,
        decimal_odds=1.87 if with_quote else None,
        market_outcome_id=uuid4() if with_quote else None,
        odds_snapshot_id=uuid4() if with_quote else None,
        quote_observed_at=(FIRST_PITCH - timedelta(minutes=20)) if with_quote else None,
        forecast_id=uuid4() if with_quote else None,
        forecast_generated_at=(FIRST_PITCH - timedelta(minutes=25)) if with_quote else None,
        forecast_cutoff=(FIRST_PITCH - timedelta(minutes=20)) if with_quote else None,
        forecast_version="FINAL · v3" if with_quote else None,
        recommended_at=recommended_at
        or FIRST_PITCH - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES),
        model_probability=0.582 if with_quote else None,
        no_vig_probability=0.511 if with_quote else None,
        edge=0.071 if with_quote else None,
        expected_value=0.088 if with_quote else None,
        readiness=91.0 if with_quote else None,
        uncertainty=0.04 if with_quote else None,
        recommendation=recommendation,
        recommendation_reason=f"Persisted final {recommendation}.",
        qualification_reasons=[f"Persisted {recommendation}"],
        evidence_fingerprint=uuid4().hex,
        source_mode=source_mode,
        evidence=evidence,
    )
    session.add(decision)
    session.flush()
    return decision


def _position(
    session,
    experiment: ShadowExperiment,
    decision: ShadowExperimentDecision,
) -> OfficialAspPosition:
    position = OfficialAspPosition(
        forecast_id=decision.forecast_id,
        market_outcome_id=decision.market_outcome_id,
        odds_snapshot_id=decision.odds_snapshot_id,
        placed_at=decision.recommended_at,
        stake=12.5,
        decimal_odds=1.87,
        result=PositionResult.PENDING,
        status=PositionStatus.OPEN,
        strategy=experiment.staking_policy_version,
        shadow_experiment_id=experiment.id,
        shadow_decision_id=decision.id,
        bankroll_before_position=1000.0,
        recommendation_policy_version=experiment.recommendation_policy_version,
        staking_policy_version=experiment.staking_policy_version,
    )
    session.add(position)
    session.flush()
    return position


def _selection(adapter: MlbTodayAdapter, game, opportunity, now):
    return adapter._selection(str(game.id), game, opportunity, now)


def _model_with_selection(session, settings, monkeypatch, selection):
    section = SportSection(
        sport_code=MLB.sport_code,
        league_code=MLB.league_code,
        display_name=MLB.display_name,
        availability_state=MLB.availability,
        can_create_official_positions=True,
        selections=[selection],
    )
    return _model_with_section(session, settings, monkeypatch, section)


def _model_with_section(session, settings, monkeypatch, section):
    from app.sports import read_service as read_service_module

    class Stub:
        def __init__(self, *_args, **_kwargs):
            pass

        def build_section(self, _event_date, *, now=None):
            return section

    monkeypatch.setattr(
        read_service_module,
        "_adapter_factory",
        lambda league: Stub if league == "MLB" else None,
    )
    return TodayAllSportsReadService(session, settings).build(
        TODAY,
        league_filter="MLB",
        now=FIRST_PITCH - timedelta(hours=8),
    )


def _build_adapter_section(adapter, monkeypatch, game, opportunity, *, now):
    from app.services.read_service import ReadService

    opportunity.game_id = str(game.id)
    opportunity.sport = "MLB"
    dashboard = SimpleNamespace(
        games=[game],
        opportunities=[opportunity],
        summary=SimpleNamespace(complete_forecasts=1, overall_data_health=94.0),
        opportunity_summary=SimpleNamespace(
            odds_last_refresh=FIRST_PITCH - timedelta(minutes=20),
            odds_scheduler_running=True,
        ),
    )
    monkeypatch.setattr(ReadService, "dashboard", lambda _self, _date: dashboard)
    return adapter.build_section(TODAY, now=now)


def test_start_time_contract_preserves_aware_source_and_serializes_exactly(session, settings):
    adapter = MlbTodayAdapter(session, settings)
    eastern = timezone(timedelta(hours=-4))
    start = datetime(2026, 8, 31, 19, 10, tzinfo=eastern)
    game = _game(start_time=start)

    item = _selection(adapter, game, None, start - timedelta(hours=8))

    assert item.starts_at == start
    assert item.final_decision_cutoff == start - timedelta(minutes=15)
    assert item.final_decision_cutoff < item.starts_at
    serialized = item.model_dump(mode="json")
    assert serialized["starts_at"] == "2026-08-31T19:10:00-04:00"
    assert serialized["final_decision_cutoff"] == "2026-08-31T18:55:00-04:00"


def test_build_section_strictly_filters_mixed_dashboard_and_derives_mlb_metrics(
    session,
    settings,
    monkeypatch,
):
    from app.services.read_service import ReadService

    mlb_game = _game()
    mlb_game.data_readiness = 84.0
    nfl_game = _game()
    nfl_game.data_readiness = 3.0
    mlb_opportunity = _opportunity("QUALIFIED_BET")
    mlb_opportunity.game_id = str(mlb_game.id)
    mlb_opportunity.sport = "MLB"
    nfl_price = _price()
    nfl_price.quote_observed_at = FIRST_PITCH - timedelta(minutes=1)
    nfl_opportunity = _opportunity("QUALIFIED_BET", best=nfl_price)
    nfl_opportunity.game_id = str(nfl_game.id)
    nfl_opportunity.sport = "NFL"
    dashboard = SimpleNamespace(
        games=[mlb_game, nfl_game],
        opportunities=[mlb_opportunity, nfl_opportunity],
        # Deliberately cross-league values that the MLB section must not copy.
        summary=SimpleNamespace(complete_forecasts=99, overall_data_health=1.0),
        opportunity_summary=SimpleNamespace(
            odds_last_refresh=FIRST_PITCH - timedelta(minutes=1),
            odds_scheduler_running=True,
        ),
    )
    monkeypatch.setattr(ReadService, "dashboard", lambda _self, _date: dashboard)

    section = MlbTodayAdapter(session, settings).build_section(
        TODAY,
        now=FIRST_PITCH - timedelta(hours=8),
    )

    assert section.scheduled_event_count == 1
    assert section.valid_forecast_count == 1
    assert section.data_health == 84.0
    assert section.latest_market_at == FIRST_PITCH - timedelta(minutes=20)
    assert [item.event_id for item in section.selections] == [str(mlb_game.id)]
    assert all(str(item.league_code) == "MLB" for item in section.selections)


def test_missing_start_time_remains_null_and_cannot_make_board_state_final(session, settings):
    adapter = MlbTodayAdapter(session, settings)
    game = _game(start_time=None)

    item = _selection(adapter, game, _opportunity("QUALIFIED_BET"), FIRST_PITCH)

    assert item.starts_at is None
    assert item.final_decision_cutoff is None
    assert item.decision_state == DState.AWAITING_FINAL_EVIDENCE
    assert item.funding_state == FState.ELIGIBLE_NOT_FUNDED
    assert item.model_dump(mode="json")["starts_at"] is None


@pytest.mark.parametrize("board_state", ["QUALIFIED_BET", "NO_BET"])
def test_board_state_eight_hours_before_first_pitch_is_awaiting(
    session,
    settings,
    monkeypatch,
    board_state,
):
    adapter = MlbTodayAdapter(session, settings)
    game = _game()

    item = _selection(
        adapter,
        game,
        _opportunity(board_state),
        FIRST_PITCH - timedelta(hours=8),
    )

    assert item.starts_at == FIRST_PITCH
    assert item.final_decision_cutoff == FIRST_PITCH - timedelta(minutes=15)
    assert item.decision_state == DState.AWAITING_FINAL_EVIDENCE
    assert item.funding_state == FState.ELIGIBLE_NOT_FUNDED
    assert item.market_label == "MONEYLINE"
    assert item.selection_label == "NYY"
    assert item.price_american == -115
    assert item.can_ever_fund_official_position is True
    assert item.official_position_id is None
    model = _model_with_selection(session, settings, monkeypatch, item)
    assert [row.event_id for row in model.awaiting_final_evidence] == [str(game.id)]
    assert model.qualified_positions == []
    assert model.provisional_opportunities == []


def test_display_only_frozen_badge_does_not_create_an_official_position(session, settings):
    adapter = MlbTodayAdapter(session, settings)
    game = _game()
    opportunity = _opportunity("QUALIFIED_BET")
    assert opportunity.experiment_status == "FROZEN"

    item = _selection(adapter, game, opportunity, FIRST_PITCH - timedelta(hours=8))

    assert item.decision_state == DState.AWAITING_FINAL_EVIDENCE
    assert item.funding_state == FState.ELIGIBLE_NOT_FUNDED
    assert item.virtual_stake is None
    assert item.official_position_id is None


def test_persisted_final_row_is_not_presented_as_final_before_cutoff(session, settings):
    game = _game()
    experiment = _experiment(session)
    _decision(session, experiment, game.id, recommendation="QUALIFIED_BET")

    item = _selection(
        MlbTodayAdapter(session, settings),
        game,
        _opportunity("QUALIFIED_BET"),
        FIRST_PITCH - timedelta(hours=8),
    )

    assert item.decision_state == DState.AWAITING_FINAL_EVIDENCE
    assert item.funding_state == FState.ELIGIBLE_NOT_FUNDED
    assert item.decision_timestamp is None
    assert item.official_position_id is None


def test_no_forecast_is_not_evaluated_even_when_board_has_placeholder(session, settings):
    item = _selection(
        MlbTodayAdapter(session, settings),
        _game(forecast_available=False),
        _opportunity("NO_BET", best=SimpleNamespace()),
        FIRST_PITCH - timedelta(hours=8),
    )
    assert item.decision_state == DState.NOT_EVALUATED
    assert item.funding_state == FState.NOT_EVALUATED
    assert item.market_label is None
    assert item.selection_label is None
    assert item.price_american is None


def test_live_forecast_with_missing_quote_awaits_final_evidence(session, settings):
    opportunity = _opportunity("NO_BET")
    opportunity.best = None
    item = _selection(
        MlbTodayAdapter(session, settings),
        _game(),
        opportunity,
        FIRST_PITCH - timedelta(hours=8),
    )
    assert item.decision_state == DState.AWAITING_FINAL_EVIDENCE
    assert item.funding_state == FState.ELIGIBLE_NOT_FUNDED
    assert item.market_label is None
    assert item.selection_label is None
    assert item.price_american is None


def test_stale_moneyline_quote_stays_non_final_and_preserves_price(session, settings):
    stale = _price(stale=True)
    item = _selection(
        MlbTodayAdapter(session, settings),
        _game(),
        _opportunity("NO_BET", best=stale, reason="Latest quote is stale"),
        FIRST_PITCH - timedelta(hours=8),
    )
    assert item.decision_state == DState.AWAITING_FINAL_EVIDENCE
    assert item.funding_state == FState.ELIGIBLE_NOT_FUNDED
    assert item.market_label == "MONEYLINE"
    assert item.price_american == -115
    assert item.status_note == "Latest quote is stale"


@pytest.mark.parametrize(
    ("market_type", "selection_label"),
    [("RUN_LINE", "NYY -1.5"), ("TOTAL", "Over 8.5")],
)
def test_unsupported_market_is_explicit_and_never_qualified(
    session,
    settings,
    market_type,
    selection_label,
):
    unsupported = _price(market_type=market_type, selection=selection_label)
    item = _selection(
        MlbTodayAdapter(session, settings),
        _game(),
        _opportunity("QUALIFIED_BET", best=unsupported),
        FIRST_PITCH - timedelta(hours=8),
    )
    assert item.decision_state == DState.AWAITING_FINAL_EVIDENCE
    assert item.funding_state == FState.MARKET_NOT_SUPPORTED
    assert item.market_label == market_type
    assert item.selection_label == selection_label
    assert item.status_note == MARKET_NOT_SUPPORTED_NOTE
    assert item.can_ever_fund_official_position is False


@pytest.mark.parametrize("market_type", ["RUN_LINE", "TOTAL"])
def test_post_cutoff_unsupported_board_market_remains_never_fundable(
    session,
    settings,
    market_type,
):
    item = _selection(
        MlbTodayAdapter(session, settings),
        _game(),
        _opportunity(
            "QUALIFIED_BET",
            best=_price(market_type=market_type, selection="Unsupported selection"),
        ),
        FIRST_PITCH - timedelta(minutes=5),
    )

    assert item.decision_state == DState.NOT_EVALUATED
    assert item.funding_state == FState.MARKET_NOT_SUPPORTED
    assert item.market_label == market_type
    assert item.status_note == MARKET_NOT_SUPPORTED_NOTE
    assert item.can_ever_fund_official_position is False


@pytest.mark.parametrize("with_linked_position", [False, True])
def test_persisted_unsupported_market_authority_fails_closed_even_with_position(
    session,
    settings,
    monkeypatch,
    with_linked_position,
):
    game = _game()
    experiment = _experiment(session)
    decision = _decision(
        session,
        experiment,
        game.id,
        recommendation="QUALIFIED_BET",
        market="RUN_LINE",
        selection="NYY -1.5",
    )
    if with_linked_position:
        _position(session, experiment, decision)

    item = _selection(
        MlbTodayAdapter(session, settings),
        game,
        _opportunity("NO_BET"),
        FIRST_PITCH - timedelta(minutes=5),
    )

    assert item.decision_state == DState.NOT_EVALUATED
    assert item.funding_state == FState.MARKET_NOT_SUPPORTED
    assert item.market_label == "RUN_LINE"
    assert item.selection_label == "NYY -1.5"
    assert item.status_note == MARKET_NOT_SUPPORTED_NOTE
    assert item.can_ever_fund_official_position is False
    assert item.official_position_id is None
    assert item.virtual_stake is None

    model = _model_with_selection(session, settings, monkeypatch, item)
    assert model.qualified_positions == []
    assert model.awaiting_final_evidence == []
    assert model.provisional_opportunities == []
    assert model.sport_sections[0].final_qualified_decision_count == 0
    assert model.sport_sections[0].official_position_count == 0


def test_v21_display_lifecycle_cannot_be_final_before_persisted_authority(session, settings):
    _experiment(session, policy=V2_DECISION_POLICY, version=2)
    v2 = SimpleNamespace(
        active=True,
        policy_version=V2_DECISION_POLICY,
        lifecycle="FINAL_QUALIFIED",
        tier="A",
        adjusted_edge=6.4,
        adjusted_expected_value=9.1,
        reasons=["Awaiting frozen decision"],
    )
    item = _selection(
        MlbTodayAdapter(session, settings),
        _game(),
        _opportunity("QUALIFIED_BET", v2=v2),
        FIRST_PITCH - timedelta(hours=8),
    )
    assert item.decision_state == DState.AWAITING_FINAL_EVIDENCE
    assert item.funding_state == FState.ELIGIBLE_NOT_FUNDED
    # A mutable board row is intentionally policy-neutral. Experiment identity
    # appears only with persisted authority (or an explicit missed-freeze row).
    assert item.experiment_id is None
    assert item.policy_version is None
    assert item.evidence_tier == "A"
    assert item.adjusted_edge == 6.4


def test_cutoff_safe_final_qualified_decision_is_final_but_unfunded(
    session,
    settings,
    monkeypatch,
):
    game = _game()
    experiment = _experiment(session)
    decision = _decision(session, experiment, game.id, recommendation="QUALIFIED_BET")

    item = _selection(
        MlbTodayAdapter(session, settings),
        game,
        _opportunity("NO_BET"),
        FIRST_PITCH - timedelta(minutes=5),
    )

    assert item.event_id == str(game.id)
    assert str(item.league_code) == "MLB"
    assert item.starts_at == FIRST_PITCH
    assert item.final_decision_cutoff == FIRST_PITCH - timedelta(minutes=15)
    assert item.decision_state == DState.FINAL_QUALIFIED
    assert item.funding_state == FState.ELIGIBLE_NOT_FUNDED
    assert item.market_label == "MONEYLINE"
    assert item.selection_label == "NYY"
    assert item.price_american == -115
    assert item.model_probability == 58.2
    assert item.no_vig_probability == 51.1
    assert item.expected_value == 8.8
    assert item.experiment_id == str(experiment.id)
    assert item.policy_version == DECISION_POLICY
    assert item.decision_timestamp == decision.recommended_at
    assert item.can_ever_fund_official_position is True
    assert item.official_position_id is None
    model = _model_with_selection(session, settings, monkeypatch, item)
    assert model.qualified_positions == []
    assert model.awaiting_final_evidence == []
    assert model.sport_sections[0].final_qualified_decision_count == 1
    assert model.sport_sections[0].official_position_count == 0


def test_actual_canonical_position_is_the_only_qualified_today_path(
    session,
    settings,
    monkeypatch,
):
    game = _game()
    experiment = _experiment(session)
    decision = _decision(session, experiment, game.id, recommendation="QUALIFIED_BET")
    position = _position(session, experiment, decision)

    item = _selection(
        MlbTodayAdapter(session, settings),
        game,
        _opportunity("NO_BET"),
        FIRST_PITCH - timedelta(minutes=5),
    )

    assert item.decision_state == DState.FINAL_QUALIFIED
    assert item.funding_state == FState.OFFICIAL_VIRTUAL_POSITION
    assert item.virtual_stake == 12.5
    assert item.experiment_id == str(experiment.id)
    assert item.can_ever_fund_official_position is True
    assert item.official_position_id == str(position.id)
    model = _model_with_selection(session, settings, monkeypatch, item)
    assert [row.event_id for row in model.qualified_positions] == [str(game.id)]
    assert model.awaiting_final_evidence == []
    assert model.provisional_opportunities == []
    assert model.overall_summary.official_position_count == 1


def test_simultaneous_v1_and_v21_positions_keep_separate_authorities_and_exposure(
    session,
    settings,
    monkeypatch,
):
    game = _game()
    v1 = _experiment(session, policy=DECISION_POLICY, version=1)
    v21 = _experiment(session, policy=V2_DECISION_POLICY, version=2)
    v1_decision = _decision(session, v1, game.id, recommendation="QUALIFIED_BET")
    v21_decision = _decision(
        session,
        v21,
        game.id,
        recommendation="QUALIFIED_BET",
        evidence={
            "policy_version": V2_DECISION_POLICY,
            "tier": "B",
            "adjusted_edge": 0.064,
            "adjusted_expected_value": 0.091,
        },
    )
    v1_position = _position(session, v1, v1_decision)
    v21_position = _position(session, v21, v21_decision)
    v2_view = SimpleNamespace(
        active=True,
        policy_version=V2_DECISION_POLICY,
        lifecycle="FINAL_QUALIFIED",
        tier="B",
        adjusted_edge=6.4,
        adjusted_expected_value=9.1,
        reasons=[],
    )

    section = _build_adapter_section(
        MlbTodayAdapter(session, settings),
        monkeypatch,
        game,
        _opportunity("QUALIFIED_BET", v2=v2_view),
        now=FIRST_PITCH - timedelta(minutes=5),
    )

    assert section.scheduled_event_count == 1
    assert len(section.selections) == 2
    assert {item.event_id for item in section.selections} == {str(game.id)}
    by_policy = {item.policy_version: item for item in section.selections}
    assert set(by_policy) == {DECISION_POLICY, V2_DECISION_POLICY}
    assert by_policy[DECISION_POLICY].experiment_id == str(v1.id)
    assert by_policy[V2_DECISION_POLICY].experiment_id == str(v21.id)
    assert by_policy[DECISION_POLICY].official_position_id == str(v1_position.id)
    assert by_policy[V2_DECISION_POLICY].official_position_id == str(v21_position.id)
    assert all(
        item.decision_state == DState.FINAL_QUALIFIED
        and item.funding_state == FState.OFFICIAL_VIRTUAL_POSITION
        for item in section.selections
    )
    assert section.final_qualified_decision_count == 2
    assert section.official_position_count == 2
    assert section.open_position_count == 2
    assert section.virtual_stake == 25.0

    model = _model_with_section(session, settings, monkeypatch, section)
    assert len(model.qualified_positions) == 2
    assert model.overall_summary.official_position_count == 2
    assert model.overall_summary.final_qualified_decision_count == 2
    assert model.overall_summary.total_virtual_stake == 25.0
    assert model.overall_summary.open_position_count == 2


def test_v1_final_and_v21_missed_freeze_keep_both_experiment_identities(
    session,
    settings,
    monkeypatch,
):
    game = _game()
    v1 = _experiment(session, policy=DECISION_POLICY, version=1)
    v21 = _experiment(session, policy=V2_DECISION_POLICY, version=2)
    v1_decision = _decision(session, v1, game.id, recommendation="QUALIFIED_BET")
    v1_position = _position(session, v1, v1_decision)
    v2_view = SimpleNamespace(
        active=True,
        policy_version=V2_DECISION_POLICY,
        lifecycle="FINAL_QUALIFIED",
        tier="B",
        adjusted_edge=6.4,
        adjusted_expected_value=9.1,
        reasons=[],
    )

    section = _build_adapter_section(
        MlbTodayAdapter(session, settings),
        monkeypatch,
        game,
        _opportunity("QUALIFIED_BET", v2=v2_view),
        now=FIRST_PITCH - timedelta(minutes=5),
    )

    assert len(section.selections) == 2
    by_policy = {item.policy_version: item for item in section.selections}
    assert set(by_policy) == {DECISION_POLICY, V2_DECISION_POLICY}
    assert by_policy[DECISION_POLICY].experiment_id == str(v1.id)
    assert by_policy[DECISION_POLICY].official_position_id == str(v1_position.id)
    assert by_policy[DECISION_POLICY].decision_state == DState.FINAL_QUALIFIED
    assert by_policy[V2_DECISION_POLICY].experiment_id == str(v21.id)
    assert by_policy[V2_DECISION_POLICY].decision_state == DState.NOT_EVALUATED
    assert by_policy[V2_DECISION_POLICY].funding_state == FState.NOT_EVALUATED
    assert by_policy[V2_DECISION_POLICY].official_position_id is None
    assert "mutable board evidence was not substituted" in (
        by_policy[V2_DECISION_POLICY].status_note or ""
    )
    assert section.final_qualified_decision_count == 1
    assert section.official_position_count == 1
    assert section.open_position_count == 1
    assert section.virtual_stake == 12.5


def test_inactive_and_superseded_higher_versions_are_excluded_from_projection(
    session,
    settings,
    monkeypatch,
):
    game = _game()
    active_v1 = _experiment(session, policy=DECISION_POLICY, version=1)
    superseded_policy = next(iter(SUPERSEDED_POLICIES))
    superseded = _experiment(session, policy=superseded_policy, version=2)
    inactive_v21 = _experiment(
        session,
        policy=V2_DECISION_POLICY,
        version=3,
        active=False,
    )
    active_decision = _decision(
        session,
        active_v1,
        game.id,
        recommendation="QUALIFIED_BET",
    )
    superseded_decision = _decision(
        session,
        superseded,
        game.id,
        recommendation="QUALIFIED_BET",
    )
    inactive_decision = _decision(
        session,
        inactive_v21,
        game.id,
        recommendation="QUALIFIED_BET",
    )
    active_position = _position(session, active_v1, active_decision)
    _position(session, superseded, superseded_decision)
    _position(session, inactive_v21, inactive_decision)

    section = _build_adapter_section(
        MlbTodayAdapter(session, settings),
        monkeypatch,
        game,
        _opportunity("NO_BET"),
        now=FIRST_PITCH - timedelta(minutes=5),
    )

    assert len(section.selections) == 1
    item = section.selections[0]
    assert item.experiment_id == str(active_v1.id)
    assert item.policy_version == DECISION_POLICY
    assert item.official_position_id == str(active_position.id)
    assert section.final_qualified_decision_count == 1
    assert section.official_position_count == 1
    assert section.open_position_count == 1
    assert section.virtual_stake == 12.5


def test_only_latest_active_experiment_version_per_policy_is_authoritative(
    session,
    settings,
    monkeypatch,
):
    game = _game()
    older = _experiment(session, policy=DECISION_POLICY, version=1)
    latest = _experiment(session, policy=DECISION_POLICY, version=2)
    older_decision = _decision(session, older, game.id, recommendation="QUALIFIED_BET")
    latest_decision = _decision(session, latest, game.id, recommendation="QUALIFIED_BET")
    _position(session, older, older_decision)
    latest_position = _position(session, latest, latest_decision)

    section = _build_adapter_section(
        MlbTodayAdapter(session, settings),
        monkeypatch,
        game,
        _opportunity("QUALIFIED_BET"),
        now=FIRST_PITCH - timedelta(minutes=5),
    )

    assert len(section.selections) == 1
    item = section.selections[0]
    assert item.experiment_id == str(latest.id)
    assert item.official_position_id == str(latest_position.id)
    assert section.open_position_count == 1
    assert section.official_position_count == 1
    assert section.virtual_stake == 12.5


def test_non_live_final_decision_cannot_authorize_today_state(session, settings):
    game = _game()
    experiment = _experiment(session)
    _decision(
        session,
        experiment,
        game.id,
        recommendation="QUALIFIED_BET",
        source_mode="DEMO",
    )

    item = _selection(
        MlbTodayAdapter(session, settings),
        game,
        _opportunity("QUALIFIED_BET"),
        FIRST_PITCH - timedelta(minutes=5),
    )

    assert item.decision_state == DState.NOT_EVALUATED
    assert item.funding_state == FState.NOT_EVALUATED
    assert item.decision_timestamp is None
    assert item.experiment_id is None
    assert item.policy_version is None
    assert "not final authority" in (item.status_note or "")


def test_persisted_final_no_bet_is_distinct_from_pass(session, settings):
    game = _game()
    experiment = _experiment(session)
    decision = _decision(
        session,
        experiment,
        game.id,
        recommendation="NO_BET",
        with_quote=False,
    )

    item = _selection(
        MlbTodayAdapter(session, settings),
        game,
        _opportunity("QUALIFIED_BET"),
        FIRST_PITCH - timedelta(minutes=5),
    )

    assert item.decision_state == DState.FINAL_NO_BET
    assert item.funding_state == FState.ELIGIBLE_NOT_FUNDED
    assert item.market_label == "MONEYLINE"
    assert item.selection_label == "—"
    assert item.price_american is None
    assert item.decision_timestamp == decision.recommended_at
    assert item.experiment_id == str(experiment.id)
    assert item.official_position_id is None


def test_persisted_nonqualifying_final_opportunity_maps_to_pass(session, settings):
    game = _game()
    experiment = _experiment(session)
    _decision(session, experiment, game.id, recommendation="WATCH")

    item = _selection(
        MlbTodayAdapter(session, settings),
        game,
        _opportunity("QUALIFIED_BET"),
        FIRST_PITCH - timedelta(minutes=5),
    )
    assert item.decision_state == DState.PASS
    assert item.funding_state == FState.ELIGIBLE_NOT_FUNDED
    assert item.experiment_id == str(experiment.id)
    assert item.policy_version == DECISION_POLICY


def test_late_decision_is_not_treated_as_pregame_authority(session, settings):
    game = _game()
    experiment = _experiment(session)
    _decision(
        session,
        experiment,
        game.id,
        recommendation="QUALIFIED_BET",
        recommended_at=FIRST_PITCH - timedelta(minutes=5),
    )

    item = _selection(
        MlbTodayAdapter(session, settings),
        game,
        _opportunity("QUALIFIED_BET"),
        FIRST_PITCH - timedelta(minutes=4),
    )
    assert item.decision_state == DState.NOT_EVALUATED
    assert item.funding_state == FState.NOT_EVALUATED
    assert item.decision_timestamp is None
    assert item.official_position_id is None
    assert "not final authority" in (item.status_note or "")


def test_game_already_started_does_not_recast_board_evidence_as_final(session, settings):
    game = _game()
    item = _selection(
        MlbTodayAdapter(session, settings),
        game,
        _opportunity("QUALIFIED_BET"),
        FIRST_PITCH + timedelta(minutes=1),
    )
    assert item.decision_state == DState.NOT_EVALUATED
    assert item.funding_state == FState.NOT_EVALUATED
    assert item.decision_timestamp is None
    assert item.starts_at == FIRST_PITCH
    assert "not final authority" in (item.status_note or "")


def test_persisted_v21_authority_uses_its_exact_experiment_and_policy(session, settings):
    game = _game()
    _experiment(session, policy=DECISION_POLICY, version=1)
    v21 = _experiment(session, policy=V2_DECISION_POLICY, version=2)
    decision = _decision(
        session,
        v21,
        game.id,
        recommendation="QUALIFIED_BET",
        evidence={
            "policy_version": V2_DECISION_POLICY,
            "tier": "B",
            "adjusted_edge": 0.064,
            "adjusted_expected_value": 0.091,
        },
    )
    v2_view = SimpleNamespace(
        active=True,
        policy_version=V2_DECISION_POLICY,
        lifecycle="FINAL_NO_BET",
        tier="D",
        adjusted_edge=None,
        adjusted_expected_value=None,
        reasons=[],
    )

    item = _selection(
        MlbTodayAdapter(session, settings),
        game,
        _opportunity("NO_BET", v2=v2_view),
        FIRST_PITCH - timedelta(minutes=5),
    )

    assert item.decision_state == DState.FINAL_QUALIFIED
    assert item.funding_state == FState.ELIGIBLE_NOT_FUNDED
    assert item.experiment_id == str(v21.id)
    assert item.experiment_name == v21.name
    assert item.policy_version == V2_DECISION_POLICY
    assert item.decision_timestamp == decision.recommended_at
    assert item.evidence_tier == "B"
    assert item.adjusted_edge == 6.4
    assert item.expected_value == 9.1


def test_experiment_unavailable_keeps_candidate_honest_without_inventing_identity(
    session,
    settings,
):
    item = _selection(
        MlbTodayAdapter(session, settings),
        _game(),
        _opportunity("QUALIFIED_BET"),
        FIRST_PITCH - timedelta(hours=8),
    )
    assert item.decision_state == DState.AWAITING_FINAL_EVIDENCE
    assert item.funding_state == FState.ELIGIBLE_NOT_FUNDED
    assert item.experiment_id is None
    assert item.experiment_name is None
    assert item.policy_version is None
    assert item.official_position_id is None
