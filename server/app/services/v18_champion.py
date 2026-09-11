"""Import the V18 conditional-champion supplement, and arm its experiment.

WHY AUTHORITY IS A SEPARATE OBJECT
----------------------------------
A forecast is data a producer sends. If the field that makes an engine the
champion could travel inside a forecast, then anything able to write a forecast
could promote itself, and "conditional champion" would mean whatever the last
payload said. So authority arrives only here, addressed by the sha256 of the
exact bytes that granted it, and the import refuses a supplement whose digest
does not match what the caller asked for.

ARMED IS NOT ACTIVE
-------------------
The supplement names an exact future boundary. Importing it before that
boundary arms the era; it does not start it. Nothing is backdated, and a
boundary already in the past is refused outright rather than quietly moved --
the producer's own contract says a missed boundary needs a new supplement.

WHAT ACTIVATION DOES NOT DO
---------------------------
It does not execute the model (AISP reads saved forecasts and never fits or
predicts), does not clear the disclosed historical-validation review, does not
touch a single Gen1 decision, position or settlement, and does not authorize
real money. It changes which engine may control PROSPECTIVE decisions, from an
exact instant forward.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.entities import (
    ModelChampionAuthority,
    OfficialAspPosition,
    PositionSettlement,
    ShadowExperiment,
    ShadowExperimentDecision,
)

SCHEMA = "AISP_MLB_V18_CONDITIONAL_CHAMPION_HANDOFF_V1"
ENGINE_ID = "MLB_V18_WHOLE_GAME"
#: ``shadow_experiments.sport`` uses the league code; the decision rows use
#: the sport-qualified form. They are different columns with different
#: vocabularies, and conflating them silently finds no predecessor at all.
SPORT = "MLB"

ARMED = "ARMED"
ACTIVE = "ACTIVE"

#: Refusal reasons. Each one is a fact about the supplement, not an opinion.
WRONG_DIGEST = "HANDOFF_BYTES_DO_NOT_MATCH_THE_EXPECTED_SHA256"
WRONG_SCHEMA = "SUPPLEMENT_IS_NOT_AISP_MLB_V18_CONDITIONAL_CHAMPION_HANDOFF_V1"
NOT_AUTHORIZED = "SUPPLEMENT_DOES_NOT_AUTHORIZE_ACTIVATION"
CLAIMS_EXECUTION = "SUPPLEMENT_CLAIMS_AISP_MAY_EXECUTE_THE_MODEL"
CLAIMS_REAL_MONEY = "SUPPLEMENT_CLAIMS_REAL_MONEY_AUTHORITY"
BOUNDARY_PASSED = "PROPOSED_BOUNDARY_IS_NOT_IN_THE_FUTURE"


class ChampionImportError(RuntimeError):
    """The supplement was refused. The message is the reason, verbatim."""


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _parse_instant(raw: str) -> datetime:
    return _aware(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))


def read_supplement(path: str | Path, *, expected_sha256: str) -> tuple[dict[str, Any], str, int]:
    """Read and verify the exact bytes. Digest first, contents second.

    Reading before verifying would mean parsing bytes we have not yet decided to
    trust, so the digest check comes first and nothing else is attempted when it
    fails.
    """

    blob = Path(path).read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != expected_sha256:
        raise ChampionImportError(f"{WRONG_DIGEST}: read {digest}, expected {expected_sha256}")
    return json.loads(blob.decode("utf-8")), digest, len(blob)


class V18ChampionService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- import ---------------------------------------------------------
    def import_supplement(
        self,
        path: str | Path,
        *,
        expected_sha256: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist the authority. Idempotent on the handoff digest."""

        now = _aware(now or datetime.now(UTC))
        payload, digest, size = read_supplement(path, expected_sha256=expected_sha256)

        existing = self.session.scalar(select(ModelChampionAuthority).where(
            ModelChampionAuthority.handoff_sha256 == digest
        ))
        if existing is not None:
            return {"imported": False, "reused": True, "authority_id": str(existing.id),
                    "handoff_sha256": digest, "state": existing.state,
                    "model_era_id": existing.model_era_id,
                    "effective_at_utc": _aware(existing.effective_at).isoformat()}

        if payload.get("schemaVersion") != SCHEMA:
            raise ChampionImportError(f"{WRONG_SCHEMA}: {payload.get('schemaVersion')!r}")
        activation = payload.get("activation") or {}
        if activation.get("authorized") is not True:
            raise ChampionImportError(NOT_AUTHORIZED)
        # These two are refused rather than merely ignored. A supplement that
        # claims either is not a supplement we understand, and importing the
        # rest of it while silently dropping the claim would hide that.
        if payload.get("executionAllowedInAISP") is not False:
            raise ChampionImportError(CLAIMS_EXECUTION)
        if payload.get("realMoneyExecutionAllowed") is not False:
            raise ChampionImportError(CLAIMS_REAL_MONEY)

        era = payload["modelEra"]
        effective_at = _parse_instant(era["effectiveAtUtc"])
        if effective_at <= now:
            raise ChampionImportError(
                f"{BOUNDARY_PASSED}: {effective_at.isoformat()} is not after {now.isoformat()}. "
                "The producer's contract requires a new immutable future-boundary "
                "supplement rather than a backdated activation."
            )

        identity = payload["modelIdentity"]
        authority = ModelChampionAuthority(
            handoff_sha256=digest,
            schema_version=SCHEMA,
            sport=SPORT,
            engine_id=ENGINE_ID,
            model_name=identity["name"],
            model_version=identity["version"],
            model_artifact_sha256=identity["artifactSha256"],
            champion_manifest_sha256=payload["championManifest"]["sha256"],
            frozen_bindings_sha256=(payload.get("frozenBindings") or {}).get("sha256"),
            parent_bundle_id=identity.get("parentBundleId"),
            training_run_id=identity.get("trainingRunId"),
            model_era_id=era["experimentId"],
            effective_at=effective_at,
            historical_validation_under_review=True,
            historical_integrity_note=activation.get("historicalIntegrity"),
            execution_allowed_in_aisp=False,
            real_money_execution_allowed=False,
            gen1_fallback_allowed=bool(era.get("gen1FallbackAllowedInV18")),
            position_backfill_allowed=bool(era.get("positionBackfillAllowed")),
            minimum_adjusted_edge=float(era["minimumAdjustedEdge"]),
            recommendation_policy_version=era["recommendationPolicyVersion"],
            staking_policy_version=era["stakingPolicyVersion"],
            state=ARMED,
            imported_at=now,
            evidence={
                "handoff_path": str(path),
                "handoff_bytes": size,
                "handoff_sha256": digest,
                "champion_manifest_sha256": payload["championManifest"]["sha256"],
                "champion_manifest_bytes": payload["championManifest"].get("bytes"),
                "estimator_sha256": identity["artifactSha256"],
                "frozen_bindings_sha256": (payload.get("frozenBindings") or {}).get("sha256"),
                "development_integrity_sha256": (payload.get("developmentIntegrity") or {}).get("sha256"),
                "registration_receipt_sha256": (payload.get("registrationReceipt") or {}).get("sha256"),
                "idempotency_identity": payload.get("idempotencyIdentity"),
                "forecast_timestamp_semantics": payload.get("forecastTimestampSemantics"),
                "fail_closed": activation.get("failClosed"),
                "verified_at_utc": now.isoformat(),
            },
        )
        self.session.add(authority)
        self.session.flush()
        return {"imported": True, "reused": False, "authority_id": str(authority.id),
                "handoff_sha256": digest, "state": ARMED,
                "model_era_id": authority.model_era_id,
                "effective_at_utc": effective_at.isoformat()}

    # -- arming ---------------------------------------------------------
    def authority(self) -> ModelChampionAuthority | None:
        return self.session.scalar(
            select(ModelChampionAuthority)
            .where(ModelChampionAuthority.engine_id == ENGINE_ID)
            .order_by(ModelChampionAuthority.effective_at.desc())
            .limit(1)
        )

    def predecessor(self) -> ShadowExperiment | None:
        """The funded experiment the era succeeds: the active MLB one."""

        return self.session.scalar(
            select(ShadowExperiment)
            .where(ShadowExperiment.is_active.is_(True),
                   ShadowExperiment.sport == SPORT,
                   ShadowExperiment.model_era_id.is_(None))
            .order_by(ShadowExperiment.version.desc())
            .limit(1)
        )

    def arm_experiment(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Create the era's experiment, dormant until the boundary arrives.

        The row exists before the boundary so that the boundary itself needs no
        decision: at 04:00Z the scheduler simply finds an experiment whose
        ``started_at`` has arrived. Creating it lazily at the boundary would put
        a write on the critical path of the first decision it governs.
        """

        now = _aware(now or datetime.now(UTC))
        authority = self.authority()
        if authority is None:
            return {"armed": False, "reason": "NO_IMPORTED_CHAMPION_AUTHORITY"}
        if authority.experiment_id is not None:
            experiment = self.session.get(ShadowExperiment, authority.experiment_id)
            return {"armed": False, "reused": True, "experiment_id": str(authority.experiment_id),
                    "model_era_id": authority.model_era_id,
                    "state": self._state(authority, now),
                    "effective_at_utc": _aware(authority.effective_at).isoformat(),
                    "boundary_capital": (experiment.boundary_capital if experiment else None)}

        predecessor = self.predecessor()
        if predecessor is None:
            return {"armed": False, "reason": "NO_PREDECESSOR_MLB_EXPERIMENT"}

        next_version = int(self.session.scalar(
            select(func.max(ShadowExperiment.version))
        ) or 0) + 1
        effective_at = _aware(authority.effective_at)
        experiment = ShadowExperiment(
            name="ASP MLB V18 CONDITIONAL VIRTUAL - 2026 V1",
            version=next_version,
            started_at=effective_at,
            start_date=effective_at.astimezone(
                __import__("zoneinfo").ZoneInfo(self.settings.display_timezone)
            ).date(),
            # Opening capital is NOT decided here. It is captured atomically at
            # the boundary from what the predecessor actually holds; until then
            # this mirrors the predecessor's starting figure purely so the
            # column is not null, and `boundary_capital` records the truth.
            starting_bankroll=predecessor.starting_bankroll,
            sport=SPORT,
            recommendation_policy_version=authority.recommendation_policy_version,
            staking_policy_version=authority.staking_policy_version,
            decision_policy=predecessor.decision_policy,
            controlling_engine=ENGINE_ID,
            supported_markets=list(predecessor.supported_markets or []),
            kelly_fraction=predecessor.kelly_fraction,
            unit_percentage=predecessor.unit_percentage,
            max_position_percentage=predecessor.max_position_percentage,
            max_same_game_percentage=predecessor.max_same_game_percentage,
            max_daily_percentage=predecessor.max_daily_percentage,
            code_commit=predecessor.code_commit,
            code_identity=predecessor.code_identity,
            # Dormant on purpose. `capture_boundary` activates it, and only
            # once the boundary has actually arrived.
            is_active=False,
            policy_config=predecessor.policy_config,
            model_era_id=authority.model_era_id,
            predecessor_experiment_id=predecessor.id,
        )
        self.session.add(experiment)
        self.session.flush()
        authority.experiment_id = experiment.id
        self.session.flush()
        return {"armed": True, "experiment_id": str(experiment.id),
                "model_era_id": authority.model_era_id,
                "predecessor_experiment_id": str(predecessor.id),
                "state": self._state(authority, now),
                "effective_at_utc": effective_at.isoformat(),
                "starts_in_seconds": max(0, int((effective_at - now).total_seconds()))}

    # -- the boundary ---------------------------------------------------
    def capture_boundary(self, *, now: datetime | None = None) -> dict[str, Any]:
        """At (not before) the boundary, snapshot capital and open the era.

        The snapshot is taken in one transaction and written once. Realized
        profit and reserved open stakes are read from the predecessor's own
        ledger; nothing is invented and no second $1,000 appears. Gen1's open
        positions and their later settlements stay in Gen1 -- an internal
        transfer is not profit, and moving the liabilities would let the same
        stake be credited twice.
        """

        now = _aware(now or datetime.now(UTC))
        authority = self.authority()
        if authority is None or authority.experiment_id is None:
            return {"captured": False, "reason": "NOTHING_ARMED"}
        experiment = self.session.get(ShadowExperiment, authority.experiment_id)
        if experiment is None:
            return {"captured": False, "reason": "ARMED_EXPERIMENT_IS_MISSING"}
        if experiment.boundary_capital is not None:
            return {"captured": False, "reused": True, "state": self._state(authority, now),
                    "boundary_capital": experiment.boundary_capital}
        effective_at = _aware(authority.effective_at)
        if now < effective_at:
            return {"captured": False, "reason": "BOUNDARY_HAS_NOT_ARRIVED",
                    "state": ARMED, "effective_at_utc": effective_at.isoformat(),
                    "starts_in_seconds": int((effective_at - now).total_seconds())}

        predecessor = self.session.get(ShadowExperiment, experiment.predecessor_experiment_id)
        if predecessor is None:
            return {"captured": False, "reason": "PREDECESSOR_IS_MISSING"}

        realized = self._realized_profit(predecessor)
        reserved = self._reserved_open_stake(predecessor)
        starting = float(predecessor.starting_bankroll)
        # Realized bankroll includes settled results only. Reserved stake is
        # money already committed to Gen1 positions that have not settled; it
        # cannot come with the era, because Gen1 must be able to pay out.
        realized_bankroll = round(starting + realized, 2)
        carried = round(realized_bankroll - reserved, 2)
        last_gen1 = self._last_decision(predecessor, before=effective_at)

        snapshot = {
            "captured_at_utc": now.isoformat(),
            "boundary_utc": effective_at.isoformat(),
            "predecessor_experiment_id": str(predecessor.id),
            "predecessor_name": predecessor.name,
            "predecessor_starting_bankroll": starting,
            "predecessor_realized_profit": round(realized, 2),
            "predecessor_realized_bankroll": realized_bankroll,
            "predecessor_reserved_open_stake": round(reserved, 2),
            "carried_forward_to_v18": max(0.0, carried),
            "open_liabilities_stay_with": str(predecessor.id),
            "last_gen1_eligible_decision_id": last_gen1[0],
            "last_gen1_eligible_decision_at": last_gen1[1],
            "new_capital_created": 0.0,
            "note": (
                "Carried capital is the predecessor's realized bankroll minus the "
                "stake still reserved against its open positions. No new capital "
                "was created and no open liability moved; an internal transfer is "
                "not profit."
            ),
        }
        experiment.boundary_capital = snapshot
        experiment.starting_bankroll = max(0.0, carried)
        experiment.is_active = True
        authority.state = ACTIVE
        self.session.flush()
        return {"captured": True, "state": ACTIVE, "experiment_id": str(experiment.id),
                "boundary_capital": snapshot}

    # -- read -----------------------------------------------------------
    def status(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = _aware(now or datetime.now(UTC))
        authority = self.authority()
        if authority is None:
            return {"present": False, "state": "NOT_IMPORTED",
                    "engine_id": ENGINE_ID, "sport": SPORT}
        experiment = (self.session.get(ShadowExperiment, authority.experiment_id)
                      if authority.experiment_id else None)
        effective_at = _aware(authority.effective_at)
        return {
            "present": True,
            "state": self._state(authority, now),
            "sport": SPORT,
            "engine_id": authority.engine_id,
            "model": {
                "name": authority.model_name,
                "version": authority.model_version,
                "artifact_sha256": authority.model_artifact_sha256,
                "champion_manifest_sha256": authority.champion_manifest_sha256,
                "parent_bundle_id": authority.parent_bundle_id,
                "training_run_id": authority.training_run_id,
            },
            "handoff_sha256": authority.handoff_sha256,
            "model_era_id": authority.model_era_id,
            "effective_at_utc": effective_at.isoformat(),
            "starts_in_seconds": max(0, int((effective_at - now).total_seconds())),
            "historical_validation_under_review": authority.historical_validation_under_review,
            "historical_integrity_note": authority.historical_integrity_note,
            "executes_in_aisp": authority.execution_allowed_in_aisp,
            "real_money": authority.real_money_execution_allowed,
            "gen1_fallback_allowed": authority.gen1_fallback_allowed,
            "position_backfill_allowed": authority.position_backfill_allowed,
            "minimum_adjusted_edge": authority.minimum_adjusted_edge,
            "experiment_id": str(authority.experiment_id) if authority.experiment_id else None,
            "boundary_capital": experiment.boundary_capital if experiment else None,
        }

    @staticmethod
    def _state(authority: ModelChampionAuthority, now: datetime) -> str:
        return ACTIVE if now >= _aware(authority.effective_at) else ARMED

    # -- predecessor accounting, read from its own ledger ----------------
    def _realized_profit(self, experiment: ShadowExperiment) -> float:
        return float(self.session.scalar(
            select(func.coalesce(func.sum(PositionSettlement.profit_loss), 0))
            .join(OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
            .where(OfficialAspPosition.shadow_experiment_id == experiment.id)
        ) or 0.0)

    def _reserved_open_stake(self, experiment: ShadowExperiment) -> float:
        return float(self.session.scalar(
            select(func.coalesce(func.sum(OfficialAspPosition.stake), 0))
            .where(OfficialAspPosition.shadow_experiment_id == experiment.id,
                   OfficialAspPosition.settled_at.is_(None))
        ) or 0.0)

    def _last_decision(self, experiment: ShadowExperiment, *, before: datetime) -> tuple[str | None, str | None]:
        row = self.session.execute(
            select(ShadowExperimentDecision.id, ShadowExperimentDecision.recommended_at)
            .where(ShadowExperimentDecision.experiment_id == experiment.id,
                   ShadowExperimentDecision.recommended_at < before)
            .order_by(ShadowExperimentDecision.recommended_at.desc(),
                      ShadowExperimentDecision.id.desc())
            .limit(1)
        ).first()
        if row is None:
            return None, None
        return str(row[0]), _aware(row[1]).isoformat()


__all__ = [
    "ACTIVE", "ARMED", "ENGINE_ID", "SCHEMA", "SPORT",
    "ChampionImportError", "V18ChampionService", "read_supplement",
]
