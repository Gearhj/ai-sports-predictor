"""Authentic sportsbook player-prop quotes (quote display and virtual selection only).

WHAT A ROW IS
-------------
One provider outcome exactly as the sportsbook offered it: provider, provider
event, market key, outcome name, player display name, line and price, with the
provider's own observation time and AISP's ingestion time. Rows are append-only
observations; a later price is a new row.

IDENTITY IS BOUND, OR THE ROW IS QUARANTINED
--------------------------------------------
``identity_status = RESOLVED`` means the provider event matched exactly one
canonical MLB game and the display name matched exactly one player on that
game's teams through governed roster / lineup / starter evidence. Anything else
-- an unresolved game, an ambiguous or unknown name, a player whose evidence is
on another team, a conflicting Over/Under orientation -- is stored with
``identity_status = QUARANTINED`` and a reason, and is never selectable. A
provider name never creates a player.

NO ASSESSMENT
-------------
There is no probability, fair price, edge, expected return, confidence or
decision column, on purpose. Model Studio's player-prop probabilities are
INSUFFICIENT_EVIDENCE; these rows authorize quote display and a user's own
virtual pick, nothing more.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.entities import CreatedAtMixin, UUIDPrimaryKeyMixin

PLAYER_PROP_RESOLVED = "RESOLVED"
PLAYER_PROP_QUARANTINED = "QUARANTINED"


class PlayerPropQuote(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "player_prop_quotes"

    # -- provider identity, exactly as received ---------------------------
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_sport_key: Mapped[str] = mapped_column(String(60), nullable=False)
    provider_market_key: Mapped[str] = mapped_column(String(60), nullable=False)
    provider_outcome_name: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_player_name: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_point: Mapped[float | None] = mapped_column(Float)
    sportsbook_key: Mapped[str] = mapped_column(String(80), nullable=False)
    sportsbook_title: Mapped[str | None] = mapped_column(String(120))
    american_price: Mapped[int | None] = mapped_column(Integer)
    decimal_price: Mapped[float | None] = mapped_column(Float)
    market_status: Mapped[str] = mapped_column(String(24), nullable=False, default="OPEN")
    provider_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="LIVE")
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_ledger_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("provider_request_ledger.id"))

    # -- canonical binding ------------------------------------------------
    game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id"), index=True)
    mlb_game_ref: Mapped[str | None] = mapped_column(String(40))
    player_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    outcome_side: Mapped[str | None] = mapped_column(String(8))
    line: Mapped[float | None] = mapped_column(Float)
    identity_status: Mapped[str] = mapped_column(String(16), nullable=False)
    identity_evidence: Mapped[str | None] = mapped_column(String(40))
    quarantine_reason: Mapped[str | None] = mapped_column(String(60))
    selectable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_event_id", "sportsbook_key", "provider_market_key",
            "provider_player_name", "provider_outcome_name", "provider_point", "provider_observed_at",
            name="uq_player_prop_quote_observation",
        ),
        CheckConstraint("identity_status IN ('RESOLVED', 'QUARANTINED')", name="ck_player_prop_identity_status"),
        CheckConstraint(
            "(identity_status = 'RESOLVED' AND game_id IS NOT NULL AND player_id IS NOT NULL "
            "AND team_id IS NOT NULL AND outcome_side IS NOT NULL AND quarantine_reason IS NULL) "
            "OR (identity_status = 'QUARANTINED' AND selectable = false AND quarantine_reason IS NOT NULL)",
            name="ck_player_prop_binding",
        ),
    )
