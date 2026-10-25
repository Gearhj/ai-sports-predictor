"""MLB_V18_FUNDING_PAUSE_V1: one immutable record per paused V18 decision.

WHAT A ROW MEANS
----------------
A genuine, cutoff-safe, exact-authority V18 ``QUALIFIED_BET`` decision was
blocked BEFORE funding evaluation. Nothing more. Staking, bankroll and cap
arithmetic never ran for it, so a row makes no claim that a position would
otherwise have been funded, at what size, or with what result.

WHAT IS DELIBERATELY ABSENT
---------------------------
Stake, stake fraction, payout, profit, bankroll, exposure, cap, settlement and
outcome fields do not exist here in any form. Their absence is what keeps the
record honest: there is no hypothetical portfolio behind it.

SELF-VERIFYING
--------------
``content_sha256`` is the SHA-256 of the canonical serialisation of every
business field below except ``id``, ``created_at`` and the hash itself
(``app.services.mlb_v18_funding_pause.record_content_sha256``). A reuse
recomputes it from the stored columns and fails closed on any difference, so
the record never depends on a later join to a mutable decision surface.

The table is append-only (``app.audit.immutability``) and unique on
``decision_id`` alone.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.entities import CreatedAtMixin, UUIDPrimaryKeyMixin

TABLE = "mlb_v18_funding_pauses"


class MlbV18FundingPause(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = TABLE

    # -- the row's own identity ------------------------------------------
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # -- the decision (UNIQUE: one record per decision, ever) -------------
    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shadow_experiment_decisions.id"), nullable=False)
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False, index=True)
    decision_phase: Mapped[str] = mapped_column(String(24), nullable=False)
    decision_recommendation: Mapped[str] = mapped_column(String(24), nullable=False)
    decision_source_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_recommended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # -- the complete frozen authority tuple -----------------------------
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shadow_experiments.id"), nullable=False, index=True)
    model_era_id: Mapped[str] = mapped_column(String(80), nullable=False)
    controlling_engine: Mapped[str] = mapped_column(String(40), nullable=False)
    model_id: Mapped[str] = mapped_column(String(80), nullable=False)
    model_configuration_identity: Mapped[str] = mapped_column(String(120), nullable=False)
    model_artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recommendation_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    decision_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    staking_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_configuration_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    authority_mapping_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # -- the forecast the decision was made on ---------------------------
    shadow_forecast_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shadow_forecasts.id"), nullable=False)
    forecast_identity: Mapped[str] = mapped_column(String(120), nullable=False)
    forecast_package_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # -- the quote, market and side --------------------------------------
    market_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("markets.id"), nullable=False)
    market_outcome_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market_outcomes.id"), nullable=False)
    odds_snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("odds_snapshots.id"), nullable=False)
    sportsbook: Mapped[str] = mapped_column(String(80), nullable=False)
    market_type: Mapped[str] = mapped_column(String(40), nullable=False)
    selection: Mapped[str] = mapped_column(String(80), nullable=False)
    american_odds: Mapped[int] = mapped_column(Integer, nullable=False)
    decimal_odds: Mapped[float] = mapped_column(Float, nullable=False)

    # -- what made it qualify (all as RECORDED on the decision) ----------
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    market_probability: Mapped[float] = mapped_column(Float, nullable=False)
    #: The decision's stored ``edge``: unadjusted model probability minus the
    #: offered price's break-even probability. NOT what the policy tested.
    edge: Mapped[float] = mapped_column(Float, nullable=False)
    #: The recorded policy-adjusted edge the 0.02 minimum was applied to.
    policy_adjusted_edge: Mapped[float] = mapped_column(Float, nullable=False)
    qualified_edge_threshold: Mapped[float] = mapped_column(Float, nullable=False)

    # -- the clocks -------------------------------------------------------
    quote_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The forecast's evidence cutoff, exactly as the decision recorded it.
    forecast_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: When the forecast was published/available to AISP.
    forecast_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The checkpoint's own evaluation clock (the ``now`` the freeze ran with).
    checkpoint_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The actual wall-clock instant the pause was detected and recorded.
    paused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # -- the disposition --------------------------------------------------
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_mlb_v18_funding_pause_decision"),
        CheckConstraint("reason_code = 'FUNDING_EDGE_NOT_ESTABLISHED'",
                        name="ck_mlb_v18_funding_pause_reason"),
        CheckConstraint("validation_status = 'PENDING_PROSPECTIVE_VALIDATION'",
                        name="ck_mlb_v18_funding_pause_status"),
        CheckConstraint("decision_phase = 'FINAL_PREGAME'",
                        name="ck_mlb_v18_funding_pause_phase"),
        CheckConstraint("decision_recommendation = 'QUALIFIED_BET'",
                        name="ck_mlb_v18_funding_pause_recommendation"),
        CheckConstraint("decision_source_mode = 'LIVE'",
                        name="ck_mlb_v18_funding_pause_source_mode"),
    )


__all__ = ["MlbV18FundingPause", "TABLE"]
