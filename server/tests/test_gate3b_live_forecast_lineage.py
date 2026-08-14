from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import Text

from app.models import Forecast
from app.services.forecast_service import _market_snapshot_reference


def test_market_snapshot_reference_preserves_the_complete_deterministic_set() -> None:
    snapshot_ids = [uuid4() for _ in range(54)]
    observations = [SimpleNamespace(id=value) for value in reversed(snapshot_ids)]

    reference = _market_snapshot_reference(observations)

    assert reference is not None
    assert len(reference) > 500
    resolved = reference.split(",")
    assert resolved == sorted(str(value) for value in snapshot_ids)
    assert len(resolved) == 54
    assert len(set(resolved)) == 54
    assert all(str(UUID(value)) == value for value in resolved)


def test_forecast_market_snapshot_reference_uses_unbounded_text() -> None:
    column_type = Forecast.__table__.c.market_snapshot_reference.type

    assert isinstance(column_type, Text)
