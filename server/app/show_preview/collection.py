"""Day-before preview sportsbook collection: deliberately dormant in V1.

This module imports no provider client and makes no request. It exists so the
4:00 PM ET step of the show clock has one named entry point whose answer is
explicit:

* switches off (the deployed state): refused, nothing runs, nothing reserved;
* switches on: still refused in V1. Enabling day-before collection needs the
  owner's provider plan, a funded allocation lane that does not reduce
  final-pregame coverage, and a reconciled budget guard (see
  docs/SGO_BUDGET_GUARD_AUDIT.md). None of those exist yet.
"""
from __future__ import annotations

from typing import Any

from app.core.config import Settings

DISABLED = "SHOW_PREVIEW_PROVIDER_COLLECTION_DISABLED"
NOT_IMPLEMENTED = "SHOW_PREVIEW_PROVIDER_COLLECTION_NOT_AUTHORIZED_IN_V1"


def collect_preview_markets(settings: Settings) -> dict[str, Any]:
    if not (settings.show_preview_enabled and settings.show_preview_provider_collection_enabled):
        return {"attempted": False, "reason": DISABLED, "objects": 0, "reservations": 0}
    return {"attempted": False, "reason": NOT_IMPLEMENTED, "objects": 0, "reservations": 0,
            "detail": "Day-before collection requires an approved provider plan and a dedicated lane; V1 never requests."}
