"""What the existing SportsDataIO account is evidenced to permit for NBA.

WHY THIS IS A MODULE AND NOT A COMMENT
--------------------------------------
The connector must be unable to make a provider request that nobody has
established is permitted. So the decision is code, it runs before any transport
is constructed, and it fails closed. A configured key is not an entitlement and
an HTTP 200 is not a licence: both establish that a request *worked*, which is a
different question from whether it was allowed and what it cost.

WHAT THIS DOES NOT DO
---------------------
It makes no network request of any kind, including to the provider's own
utility endpoints. There is no documented free entitlement or billing
introspection endpoint, so a probe could not answer the question it would be
making -- it would only spend an unknown allowance to learn that the key works.

THREE PERMISSIONS, RECORDED SEPARATELY
--------------------------------------
API access, training/research use, and subscriber display are distinct grants.
This module never infers one from another. A key that returns data may still
carry personal-use-only terms, and Discovery Lab's real data is advertised
exactly that way.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

#: The prior assessment this continues. Model Studio branch
#: codex/nba-sportsdataio-pilot-v1 @ da05d9f6, 2026-09-10.
PRIOR_VERDICT = "NBA_REAL_DATA_PILOT_ACCESS_BLOCKED"
PRIOR_SOURCE = (
    "asp-model-studio @ da05d9f6f12ecb628101dab42f67709101d01265, "
    "docs/nba-sportsdataio-pilot-v1/PILOT_REPORT.md"
)

#: The pilot ceiling, carried forward rather than reset. Previous intake was
#: zero real games and zero provider bytes, so the whole allowance remains.
PILOT_MAX_GAMES = 3
PILOT_MAX_ORIGINAL_BYTES = 25_000_000


class DataMode(StrEnum):
    """What the bytes actually are. Never inferred from a 200 response."""

    #: Real provider data, entitlement evidenced. Not reachable today.
    REAL = "REAL"
    #: Provider data whose numeric fields are deliberately perturbed.
    SCRAMBLED = "SCRAMBLED"
    #: Locally authored test data. Never provider output.
    FIXTURE = "FIXTURE"
    #: Nothing has been established.
    UNKNOWN = "UNKNOWN"


class Permission(StrEnum):
    GRANTED = "GRANTED"
    DENIED = "DENIED"
    #: No evidence either way. The operative state for this account.
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class Finding:
    """One piece of evidence, and what it does and does not establish."""

    key: str
    observed: str
    establishes: str
    does_not_establish: str


@dataclass(frozen=True)
class Entitlement:
    """The decision, with every finding behind it."""

    verdict: str
    data_mode: DataMode
    network_collection_allowed: bool
    api_access: Permission
    training_research_use: Permission
    subscriber_display_use: Permission
    incremental_cost_position: str
    findings: tuple[Finding, ...]
    missing_prerequisite: str | None
    assessed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def blocked(self) -> bool:
        return not self.network_collection_allowed

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "dataMode": str(self.data_mode),
            "networkCollectionAllowed": self.network_collection_allowed,
            "permissions": {
                "apiAccess": str(self.api_access),
                "trainingResearchUse": str(self.training_research_use),
                "subscriberDisplayUse": str(self.subscriber_display_use),
                "note": "Recorded separately. API access never implies research or "
                        "display rights.",
            },
            "incrementalCostPosition": self.incremental_cost_position,
            "pilotCeiling": {
                "maxCompletedGames": PILOT_MAX_GAMES,
                "maxOriginalResponseBytes": PILOT_MAX_ORIGINAL_BYTES,
                "priorIntakeGames": 0,
                "priorIntakeBytes": 0,
                "note": "Carried forward from the prior pilot, not reset. Prior "
                        "intake was zero, so the full ceiling remains.",
            },
            "priorVerdict": PRIOR_VERDICT,
            "priorVerdictSource": PRIOR_SOURCE,
            "missingPrerequisite": self.missing_prerequisite,
            "findings": [
                {
                    "key": f.key, "observed": f.observed,
                    "establishes": f.establishes,
                    "doesNotEstablish": f.does_not_establish,
                }
                for f in self.findings
            ],
            "assessedAtUtc": self.assessed_at.isoformat(),
        }


def _key_state(settings: Any) -> tuple[bool, int]:
    """Whether a key is configured, and its length. Never its value."""

    value = str(getattr(settings, "sportsdataio_api_key", "") or "").strip()
    if not value:
        value = str(os.environ.get("SPORTSDATAIO_API_KEY", "") or "").strip()
    return bool(value), len(value)


def assess(settings: Any, *, sportsdataio_runs: int = 0) -> Entitlement:
    """Decide from evidence already in this environment. No network call.

    ``sportsdataio_runs`` is the count of recorded SportsDataIO ingestion runs.
    Zero means there is no usage or quota history to reason from either.
    """

    configured, key_length = _key_state(settings)
    enabled = bool(getattr(settings, "sportsdataio_enabled", False))
    base_url = str(getattr(settings, "sportsdataio_base_url", "") or "")
    nba_scoped = "/nba" in base_url.lower()

    findings = [
        Finding(
            key="credential_present",
            observed=f"SPORTSDATAIO_API_KEY configured, length {key_length}"
            if configured else "SPORTSDATAIO_API_KEY absent or empty",
            establishes="a credential exists in this environment" if configured
            else "no credential exists",
            does_not_establish="which product, league, season or endpoints it is "
                               "entitled to, what a request costs, or what use is "
                               "licensed",
        ),
        Finding(
            key="adapter_enabled_flag",
            observed=f"sportsdataio_enabled={enabled} "
                     f"(SPORTSDATAIO_ENABLED is unset in the environment)",
            establishes="the operator has not turned this provider on",
            does_not_establish="anything about entitlement; the flag is a local "
                               "switch, not a grant",
        ),
        Finding(
            key="configured_scope",
            observed=f"sportsdataio_base_url={base_url!r}",
            establishes="the only configured scope is MLB" if not nba_scoped
            else "an NBA scope is configured",
            does_not_establish="NBA entitlement" if not nba_scoped
            else "that the NBA scope is entitled",
        ),
        Finding(
            key="recorded_data_mode",
            observed="app/core/config.py records that this feed's data is "
                     "trial/scrambled and therefore non-authoritative for local "
                     "operation",
            establishes="AISP's own standing determination that the accessible "
                        "data is not real",
            does_not_establish="that a different product on the same account "
                               "would be real; that would need account evidence",
        ),
        Finding(
            key="usage_history",
            observed=f"{sportsdataio_runs} SportsDataIO ingestion runs recorded",
            establishes="there is no usage, quota or billing history to reason "
                        "from" if sportsdataio_runs == 0
                        else "some usage history exists",
            does_not_establish="remaining allowance or cost position",
        ),
        Finding(
            key="introspection_endpoint",
            observed="no documented free entitlement or billing introspection "
                     "endpoint in the official NBA documentation or OpenAPI",
            establishes="entitlement cannot be settled by a request",
            does_not_establish="that no such endpoint exists undocumented; it "
                               "means none can be relied on",
        ),
        Finding(
            key="product_terms",
            observed="Free Trial and Dev Key are documented as scrambled; "
                     "Discovery Lab is real but next-day delayed and personal "
                     "use; commercial rights require an agreement",
            establishes="real NBA data on this account would, at best, be "
                        "delayed and personal-use unless a commercial agreement "
                        "covers it",
            does_not_establish="which of these this key is; that is the missing "
                               "fact",
        ),
    ]

    # Fail closed. Every path to REAL requires account evidence that does not
    # exist in this environment, and no permitted way to obtain it here.
    return Entitlement(
        verdict=PRIOR_VERDICT,
        data_mode=DataMode.UNKNOWN,
        network_collection_allowed=False,
        api_access=Permission.UNVERIFIED,
        training_research_use=Permission.UNVERIFIED,
        subscriber_display_use=Permission.UNVERIFIED,
        incremental_cost_position="UNVERIFIED — no $0 position is evidenced for "
                                  "any NBA endpoint on this key, and no allowance "
                                  "remaining is known.",
        findings=tuple(findings),
        missing_prerequisite=(
            "Written account evidence naming, for THIS SportsDataIO key: (a) the "
            "product and its NBA league/season/endpoint entitlement, (b) that the "
            "requested NBA scores, box-score and schedule calls are real rather "
            "than scrambled, (c) a $0 charge position with the remaining request "
            "allowance and confirmation that no shared odds quota is consumed, and "
            "(d) permission for internal research retention of original payloads "
            "and for any subscriber-facing display. The prepared inquiry at "
            "docs/nba-sportsdataio-pilot-v1/SPORTSDATAIO_INQUIRY.md covers exactly "
            "these and has not been sent."
        ),
    )


__all__ = [
    "DataMode", "Entitlement", "Finding", "PILOT_MAX_GAMES",
    "PILOT_MAX_ORIGINAL_BYTES", "PRIOR_SOURCE", "PRIOR_VERDICT", "Permission",
    "assess",
]
