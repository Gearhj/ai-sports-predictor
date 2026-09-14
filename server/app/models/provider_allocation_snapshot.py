"""One frozen allocation per Eastern service day.

WHY A SNAPSHOT AND NOT A CALCULATION
------------------------------------
The allowance used to be recomputed from the remaining balance on every read,
which meant spending inside a day lowered that same day's allowance underneath
itself: a lane told it had eight objects could draw only seven before the
number moved. An allowance that shrinks as you use it is not an allowance, and
it cannot be planned against.

So the day's capacity is computed ONCE, from the provider's actual balance at
the first calculation of that Eastern service day, and then held. Usage is
reconciled against it; it is never retroactively reduced. Tomorrow's snapshot
is computed from tomorrow's balance, so a heavy day still costs the next one --
which is the part that should carry forward.

The provider's hard remaining balance and the protected reserve are enforced
separately and always, so a stale snapshot can never authorise a spend the
account cannot actually fund.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class ProviderAllocationSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """The capacity one Eastern service day was issued, and what it was based on."""

    __tablename__ = "provider_allocation_snapshots"

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The Eastern calendar date this allocation governs.
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Which day-boundary rule produced it. An older row is not comparable.
    semantics: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The provider's own figures at the moment the snapshot was taken.
    monthly_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    used_this_month: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_source: Mapped[str] = mapped_column(String(48), nullable=False)
    balance_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: The reserve in force for this day, and the capacity it left.
    reserve: Mapped[int] = mapped_column(Integer, nullable=False)
    days_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_total: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Per-lane allowances as issued, plus the demand that shaped them.
    lanes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    demand: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # One allocation per provider per service day. The uniqueness is the
        # mechanism: a second calculation on the same day finds this row and
        # returns it rather than issuing a different number.
        UniqueConstraint("provider", "service_date",
                         name="uq_provider_allocation_service_day"),
    )
