"""Opening the NFL experiment, and the things it must never do twice.

The season-account rule is that a season receives exactly ONE contribution.
Activation runs from a scheduler and from a hand, can be retried, and can race
itself, so "runs twice" is the normal case rather than the exceptional one --
and running twice must produce one account, one $1,000 and one era.

The other half is separation: NFL capital and MLB capital are different
accounts, and opening one must leave the other untouched down to the row.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.models import (
    AccountStatus, ExperimentAccount, ExperimentAccountFunding, FundingKind,
    MembershipRole, ShadowExperiment,
)
from app.services import nfl_paper_policy as policy
from app.services.experiment_accounts import ExperimentAccountService
from app.services.nfl_activation import SEASON_ID, SPORT, activate, policy_config

NOW = datetime(2026, 9, 14, 16, 50, 39, tzinfo=UTC)


def test_activation_opens_one_account_with_one_thousand_once(session, settings):
    result = activate(session, settings, now=NOW)

    assert result["sport"] == "NFL"
    assert result["season_id"] == "2026"
    assert result["funding_amount"] == 1000.00
    assert result["funding_kind"] == str(FundingKind.SEASON_OPENING)
    assert result["account_status"] == str(AccountStatus.ACTIVE)
    assert result["created"] == {"funding": True, "experiment": True, "membership": True}


def test_running_it_three_times_still_contributes_one_thousand(session, settings):
    first = activate(session, settings, now=NOW)
    second = activate(session, settings, now=NOW)
    third = activate(session, settings, now=NOW)

    assert second["already_active"] is True
    assert third["already_active"] is True
    # The same rows, not merely the same totals.
    assert first["account_id"] == second["account_id"] == third["account_id"]
    assert first["funding_id"] == second["funding_id"] == third["funding_id"]
    assert first["experiment_id"] == second["experiment_id"] == third["experiment_id"]

    account = session.scalar(select(ExperimentAccount).where(
        ExperimentAccount.sport == SPORT, ExperimentAccount.season_id == SEASON_ID))
    rows = session.scalars(select(ExperimentAccountFunding).where(
        ExperimentAccountFunding.account_id == account.id)).all()
    assert len(rows) == 1
    assert float(rows[0].amount) == 1000.00
    assert session.scalar(select(func.count(ShadowExperiment.id))
                          .where(ShadowExperiment.sport == SPORT)) == 1


def test_opening_nfl_does_not_touch_an_existing_mlb_account(session, settings):
    service = ExperimentAccountService(session, settings)
    mlb = service.ensure_account("MLB", "2026")
    service.open_season(mlb, now=NOW, executed_by="test")
    session.flush()
    before = [(r.id, float(r.amount)) for r in session.scalars(
        select(ExperimentAccountFunding).where(
            ExperimentAccountFunding.account_id == mlb.id)).all()]

    activate(session, settings, now=NOW)

    after = [(r.id, float(r.amount)) for r in session.scalars(
        select(ExperimentAccountFunding).where(
            ExperimentAccountFunding.account_id == mlb.id)).all()]
    assert after == before
    assert sum(amount for _, amount in after) == 1000.00


def test_the_era_is_nfl_only_and_carries_no_baseball_adjustment(session, settings):
    activate(session, settings, now=NOW)
    era = session.scalar(select(ShadowExperiment).where(
        ShadowExperiment.sport == SPORT))

    assert era.sport == "NFL"
    assert era.decision_policy == policy.POLICY_VERSION
    assert "PRACTICAL_PREGAME" not in era.decision_policy
    assert era.supported_markets == ["MONEYLINE"]
    assert era.kelly_fraction == 0.25
    assert era.max_position_percentage == policy.POSITION_CAP_UNCALIBRATED

    config = era.policy_config
    assert config["no_pitcher_penalty"] is True
    assert config["no_lineup_penalty"] is True
    assert config["inherited_mlb_adjustments"] is None
    # The tie is the thing NFL adds, and it is stated rather than implied.
    assert config["tie_treatment"] == "STAKE_REFUNDED_PUSH"
    assert "PUSH" in config["tie_settlement"]
    assert "(pWin + pLoss)" in config["quarter_kelly"]


def test_producer_authority_is_carried_through_untouched(session, settings):
    config = policy_config()
    flags = config["producer_flags"]
    # A consumer paper permission grants no execution and no promotion. If any
    # of these ever became true here, AISP would be asserting an authority the
    # producer did not give.
    assert flags["executionAllowed"] is False
    assert flags["modelPromotionAllowed"] is False
    assert flags["parlaysAllowed"] is False
    assert flags["promotionStatus"] == "NOT_PROMOTED"
    assert flags["executionStatus"] == "NOT_EXECUTABLE"
    assert flags["researchShadowOnly"] is True
    assert config["approval"]["real_money"].startswith("NEVER")
    # Bound to the exact authorised configuration, not to "the NFL model".
    assert config["model_binding"]["model_binary_sha256"].startswith("7bec0664")


def test_the_membership_records_the_era_as_funded(session, settings):
    activate(session, settings, now=NOW)
    account = session.scalar(select(ExperimentAccount).where(
        ExperimentAccount.sport == SPORT))
    service = ExperimentAccountService(session, settings)
    members = service.member_experiments(account)
    assert len(members) == 1
    _, role = members[0]
    assert role == MembershipRole.FUNDED_ERA
