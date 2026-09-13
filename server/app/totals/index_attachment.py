"""Verify a published V18 totals index and bind it to already-imported baselines.

WHAT IS ACTUALLY BEING VERIFIED
-------------------------------
The index ships no new distribution. It indexes the PMF the ordinary daily
importer already wrote, and it copies the producer's forecast bytes alongside so
a consumer can check that claim rather than take it. So the verification that
matters is not "did the file arrive intact" -- though that is checked first --
but **does the PMF AISP already stores equal the one the index points at, bin
for bin**. If it does not, something has diverged between producer and consumer
and nothing should be attached.

Five layers, in order, each refusing rather than repairing:

1.  **Delivered bytes.** The index's own SHA-256 and byte count, then every file
    in the sibling manifest, hashed as delivered. No newline normalization: a
    consumer that has to normalize to pass a checksum is not checking the bytes
    it was given.
2.  **Contract.** Schema version and ``FRACTION_0_TO_1`` probability units. A
    percent arriving where a fraction is declared would be silently scaled by a
    hundred everywhere downstream.
3.  **Identity.** Engine, engine version, simulator, seed, denominator, the
    exact ``baselineForecastId``, its object SHA, the canonical game id AND the
    MLBAM gamePk. Never teams-and-date, which is how two versions of one game
    get collapsed into each other.
4.  **The PMF itself.** The indexed forecast file is hashed, its
    ``/distributions/totalRuns`` is read, and every bin is compared against
    ``ShadowForecast.distributions.totalRunDistribution``. The mass must sum to
    one and the recomputed full-precision mean must match the declared one.
5.  **Population shape.** ``valid + excluded == original``, the tail must not be
    truncated, and a score-limited population is refused outright -- this index
    is the ordinary 5,000-draw baseline and a conditioned population answers a
    different question.

WHAT ATTACHMENT DOES NOT DO
---------------------------
It does not gate the board. A forecast published after the index's inventory
freeze is the same engine writing the same field through the same importer, and
withholding it because an earlier snapshot predates it would hide a current
forecast behind an expired inventory -- three of September 13's four
snapshot-missing games published within the hour. Attachment records which
forecasts this index verified; the board prices from the stored PMF either way
and says which is which.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Game, ShadowForecast, TotalsIndexAttachment
from app.totals.pricing import MASS_TOLERANCE

INDEX_SCHEMA = "MLB_V18_TOTALS_DISTRIBUTION_INDEX_V1"
MANIFEST_SCHEMA = "MLB_V18_TOTALS_PACKAGE_MANIFEST_V1"
FRACTION_UNITS = "FRACTION_0_TO_1"
ORDINARY_ENGINE = "MLB_V18_WHOLE_GAME"

#: The producer pointer this index indexes, and the consumer field the ordinary
#: importer maps it to. Both are named so a future rename fails loudly.
PRODUCER_POINTER = "/distributions/totalRuns"
CONSUMER_FIELD = "distributions.totalRunDistribution"

#: The mean must agree to this many places. The PMF is 1/5000-granular, so
#: anything looser would accept a genuinely different population.
MEAN_TOLERANCE = 1e-6


class TotalsIndexError(ValueError):
    """The package cannot be verified, and the message names the exact failure."""


@dataclass(frozen=True)
class VerifiedIndex:
    """A package whose bytes, manifest and contract all check out."""

    root: Path
    index_sha256: str
    index_bytes: int
    schema_version: str
    index: dict[str, Any]
    manifest_files_verified: int
    derivative_published_at_utc: datetime | None

    @property
    def rows(self) -> list[dict[str, Any]]:
        return list(self.index.get("forecasts") or [])


@dataclass
class AttachmentOutcome:
    """What one attachment run accepted, already held, and refused."""

    index_sha256: str
    attached_at_utc: datetime
    accepted: list[str] = field(default_factory=list)
    already_attached: list[str] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    distinct_games: set[str] = field(default_factory=set)
    missing_baselines: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1-2. delivered bytes, manifest, contract
# ---------------------------------------------------------------------------


def verify_package(
    index_path: str | Path, *, expected_index_sha256: str | None = None,
) -> VerifiedIndex:
    """Hash what was delivered, then check the manifest and the contract."""

    path = Path(index_path)
    if not path.is_file():
        raise TotalsIndexError(f"no index file at {path}")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_index_sha256 and digest != expected_index_sha256:
        raise TotalsIndexError(
            f"index physical SHA-256 is {digest}, not the expected "
            f"{expected_index_sha256}"
        )

    root = path.parent
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise TotalsIndexError(
            "the sibling manifest.json is absent, so the copied files cannot be "
            "bound by physical hash"
        )
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA:
        raise TotalsIndexError(
            f"manifest schema {manifest.get('schemaVersion')!r} is not {MANIFEST_SCHEMA}")
    if manifest.get("indexSha256") != digest:
        raise TotalsIndexError(
            f"manifest binds index {manifest.get('indexSha256')!r}, but the "
            f"delivered index hashes to {digest}"
        )

    verified = 0
    for entry in manifest.get("files") or []:
        member = root / entry["relativePath"]
        if not member.is_file():
            raise TotalsIndexError(f"manifest names a missing file: {entry['relativePath']}")
        # Read as bytes. A consumer that needs newline normalization to pass a
        # physical checksum is not verifying the bytes it was handed.
        member_raw = member.read_bytes()
        if hashlib.sha256(member_raw).hexdigest() != entry["sha256"]:
            raise TotalsIndexError(
                f"{entry['relativePath']} does not match its manifest SHA-256")
        if len(member_raw) != entry["bytes"]:
            raise TotalsIndexError(
                f"{entry['relativePath']} is {len(member_raw)} bytes, manifest "
                f"declares {entry['bytes']}"
            )
        verified += 1

    index = json.loads(raw)
    if index.get("schemaVersion") != INDEX_SCHEMA:
        raise TotalsIndexError(
            f"index schema {index.get('schemaVersion')!r} is not {INDEX_SCHEMA}")
    if index.get("probabilityUnits") != FRACTION_UNITS:
        raise TotalsIndexError(
            f"index declares probability units {index.get('probabilityUnits')!r}; "
            f"this consumer reads {FRACTION_UNITS} and will not guess a scale"
        )
    if index.get("newForecastGenerated") or index.get("newAuthorityGranted"):
        raise TotalsIndexError(
            "this index declares a newly generated forecast or new authority; a "
            "derivative totals index may do neither"
        )

    return VerifiedIndex(
        root=root,
        index_sha256=digest,
        index_bytes=len(raw),
        schema_version=index["schemaVersion"],
        index=index,
        manifest_files_verified=verified,
        derivative_published_at_utc=_publication_clock(root),
    )


def _publication_clock(root: Path) -> datetime | None:
    """The producer's own derivative publication time, when it is retained.

    Read from the receipt rather than invented, and kept separate from AISP's
    attachment clock: a derivative published at 17:21 and attached at 19:30 is
    two facts, and collapsing them would misdate one of them.
    """

    receipt = root / "publication-receipt.json"
    if not receipt.is_file():
        return None
    try:
        payload = json.loads(receipt.read_bytes())
    except ValueError:
        return None
    return _aware(payload.get("publishedAtUtc"))


# ---------------------------------------------------------------------------
# 3-5. identity, PMF, population
# ---------------------------------------------------------------------------


def attach(
    session: Session,
    verified: VerifiedIndex,
    *,
    now: datetime | None = None,
) -> AttachmentOutcome:
    """Bind every index row to its already-imported ordinary baseline."""

    now = now or datetime.now(UTC)
    outcome = AttachmentOutcome(index_sha256=verified.index_sha256, attached_at_utc=now)

    for row in verified.rows:
        identity = str(row.get("baselineForecastId") or "")
        try:
            stored = _bind(session, row)
        except TotalsIndexError as exc:
            reason = str(exc)
            entry = {
                "forecast_identity": identity,
                "mlbam_game_pk": str(row.get("mlbamGamePk")),
                "game_date": row.get("gameDate"),
                "reason": reason,
            }
            if "no ordinary" in reason:
                # A missing baseline is a dependency, not a defect in the index.
                # It is reported as its own category so "we could not verify
                # this" is never confused with "the producer got this wrong".
                outcome.missing_baselines.append(entry)
            else:
                outcome.rejected.append(entry)
            continue

        existing = session.scalar(
            select(TotalsIndexAttachment).where(
                TotalsIndexAttachment.index_sha256 == verified.index_sha256,
                TotalsIndexAttachment.forecast_identity == identity,
            )
        )
        if existing is not None:
            outcome.already_attached.append(identity)
            outcome.distinct_games.add(str(row.get("mlbamGamePk")))
            continue

        try:
            detail = _verify_row(verified, row, stored)
        except TotalsIndexError as exc:
            outcome.rejected.append({
                "forecast_identity": identity,
                "mlbam_game_pk": str(row.get("mlbamGamePk")),
                "game_date": row.get("gameDate"),
                "reason": str(exc),
            })
            continue

        session.add(TotalsIndexAttachment(
            index_sha256=verified.index_sha256,
            index_schema_version=verified.schema_version,
            derivative_published_at_utc=verified.derivative_published_at_utc,
            attached_at_utc=now,
            forecast_identity=identity,
            baseline_object_sha256=str(row.get("baselineObjectSha256") or ""),
            engine_id=str(row.get("engineId")),
            engine_version=str(row.get("engineVersion")),
            simulator_id=row.get("simulatorId"),
            simulator_seed=row.get("seed"),
            canonical_game_id=stored.canonical_game_id,
            mlbam_game_pk=str(row.get("mlbamGamePk")),
            game_date=str(row.get("gameDate")),
            original_simulation_count=int(row.get("originalSimulationCount")),
            valid_original_draws_used=int(row.get("validOriginalDrawsUsed")),
            excluded_draw_count=int(row.get("excludedDrawCount") or 0),
            extra_innings_included=bool(row.get("extraInningsIncluded")),
            tail_truncated=bool((row.get("distribution") or {}).get("tailTruncated")),
            score_limited=bool(row.get("scoreLimited")),
            pmf_matches_stored=True,
            pmf_bin_count=detail["pmf_bin_count"],
            recomputed_mean_total_runs=detail["recomputed_mean_total_runs"],
            declared_mean_total_runs=detail["declared_mean_total_runs"],
            verification_detail=detail,
            note=(
                "The PMF is not copied here. This row records that the stored "
                f"{CONSUMER_FIELD} equalled the index's {PRODUCER_POINTER} "
                "bin for bin at the attachment clock."
            ),
        ))
        outcome.accepted.append(identity)
        outcome.distinct_games.add(str(row.get("mlbamGamePk")))

    session.flush()
    return outcome


def _bind(session: Session, row: dict[str, Any]) -> ShadowForecast:
    """Find the already-imported ordinary baseline this row names.

    Bound by forecast identity AND engine, then cross-checked on canonical game
    id and gamePk. Teams and date are never used: two versions of one game share
    both, and binding on them is how a selection gets attached to the wrong
    forecast.
    """

    identity = str(row.get("baselineForecastId") or "")
    if not identity:
        raise TotalsIndexError("index row names no baselineForecastId")
    engine = str(row.get("engineId") or "")
    if engine != ORDINARY_ENGINE:
        raise TotalsIndexError(
            f"index row declares engine {engine!r}; only the ordinary "
            f"{ORDINARY_ENGINE} baseline is eligible"
        )
    if row.get("scoreLimited"):
        raise TotalsIndexError(
            "index row is a score-limited population; a conditioned population "
            "is not the ordinary baseline and cannot price an unrestricted total"
        )

    stored = session.scalar(select(ShadowForecast).where(
        ShadowForecast.forecast_id == identity,
        ShadowForecast.engine_id == ORDINARY_ENGINE,
    ))
    if stored is None:
        raise TotalsIndexError(
            f"no ordinary {ORDINARY_ENGINE} baseline is imported for "
            f"{identity}; its dependency is the ordinary daily import and this "
            "index does not manufacture it"
        )

    declared_pk = str(row.get("mlbamGamePk") or "")
    if declared_pk and str(stored.source_event_id or "") != declared_pk:
        raise TotalsIndexError(
            f"gamePk mismatch: index says {declared_pk}, stored forecast says "
            f"{stored.source_event_id}"
        )
    declared_canonical = str(row.get("canonicalGameId") or "")
    if declared_canonical and stored.canonical_game_id is not None:
        if str(stored.canonical_game_id) != declared_canonical:
            raise TotalsIndexError(
                f"canonical game mismatch: index says {declared_canonical}, "
                f"stored forecast says {stored.canonical_game_id}"
            )
    return stored


def _verify_row(
    verified: VerifiedIndex, row: dict[str, Any], stored: ShadowForecast,
) -> dict[str, Any]:
    """Identity, population shape, and the PMF bin for bin."""

    identity = str(row.get("baselineForecastId"))

    # -- identity ----------------------------------------------------------
    expected_object = str(row.get("baselineObjectSha256") or "")
    if expected_object and not identity.endswith(expected_object):
        raise TotalsIndexError(
            f"baselineForecastId {identity} does not carry its declared object "
            f"SHA {expected_object}"
        )
    if str(stored.engine_version or "") != str(row.get("engineVersion") or ""):
        raise TotalsIndexError(
            f"engine version mismatch: index {row.get('engineVersion')!r}, stored "
            f"{stored.engine_version!r}"
        )
    if row.get("simulatorId") and str(stored.simulator_id or "") != str(row["simulatorId"]):
        raise TotalsIndexError(
            f"simulator mismatch: index {row['simulatorId']!r}, stored "
            f"{stored.simulator_id!r}"
        )
    if row.get("seed") is not None and stored.simulator_seed != row["seed"]:
        raise TotalsIndexError(
            f"seed mismatch: index {row['seed']!r}, stored {stored.simulator_seed!r}")

    # -- population shape --------------------------------------------------
    original = int(row.get("originalSimulationCount") or 0)
    used = int(row.get("validOriginalDrawsUsed") or 0)
    excluded = int(row.get("excludedDrawCount") or 0)
    if original <= 0:
        raise TotalsIndexError("originalSimulationCount must be positive")
    if used + excluded != original:
        raise TotalsIndexError(
            f"population does not account for itself: used {used} + excluded "
            f"{excluded} != original {original}"
        )
    if excluded:
        raise TotalsIndexError(
            f"{excluded} draws were excluded; the ordinary baseline uses every "
            "valid original draw"
        )
    distribution = row.get("distribution") or {}
    if distribution.get("tailTruncated"):
        raise TotalsIndexError(
            "the index declares a truncated tail; a truncated PMF cannot price a "
            "line in the tail it dropped"
        )
    if distribution.get("probabilityUnits") != FRACTION_UNITS:
        raise TotalsIndexError(
            f"distribution declares units {distribution.get('probabilityUnits')!r}, "
            f"not {FRACTION_UNITS}"
        )
    if distribution.get("jsonPointer") != PRODUCER_POINTER:
        raise TotalsIndexError(
            f"distribution points at {distribution.get('jsonPointer')!r}, not the "
            f"contracted {PRODUCER_POINTER}"
        )
    stored_count = (stored.uncertainty or {}).get("simulationCount")
    if stored_count is not None and int(stored_count) != original:
        raise TotalsIndexError(
            f"denominator mismatch: index {original}, stored forecast {stored_count}")

    # -- the PMF, bin for bin ---------------------------------------------
    member = verified.root / str(distribution.get("relativePath") or "")
    if not member.is_file():
        raise TotalsIndexError(f"the indexed forecast file is absent: {member.name}")
    member_raw = member.read_bytes()
    if hashlib.sha256(member_raw).hexdigest() != distribution.get("sha256"):
        raise TotalsIndexError("the indexed forecast file does not match its own SHA-256")

    indexed_rows = (json.loads(member_raw).get("distributions") or {}).get("totalRuns")
    if not isinstance(indexed_rows, list) or not indexed_rows:
        raise TotalsIndexError(f"the indexed forecast has no {PRODUCER_POINTER} rows")
    indexed = {int(entry["value"]): float(entry["probability"]) for entry in indexed_rows}

    stored_pmf_raw = (stored.distributions or {}).get("totalRunDistribution")
    if not isinstance(stored_pmf_raw, dict) or not stored_pmf_raw:
        raise TotalsIndexError(
            f"the stored baseline has no {CONSUMER_FIELD}; the ordinary import "
            "did not write the field this index describes"
        )
    stored_pmf = {int(k): float(v) for k, v in stored_pmf_raw.items()}

    if stored_pmf != indexed:
        only_indexed = sorted(set(indexed) - set(stored_pmf))
        only_stored = sorted(set(stored_pmf) - set(indexed))
        differing = sorted(
            k for k in set(indexed) & set(stored_pmf) if indexed[k] != stored_pmf[k])
        raise TotalsIndexError(
            "the stored PMF is not the indexed one: "
            f"{len(only_indexed)} bins only in the index, {len(only_stored)} only "
            f"stored, {len(differing)} differing"
        )

    mass = sum(stored_pmf.values())
    if abs(mass - 1.0) > MASS_TOLERANCE:
        raise TotalsIndexError(f"PMF mass is {mass:.8f}, not 1")
    recomputed = sum(value * probability for value, probability in stored_pmf.items())
    declared = (row.get("expectations") or {}).get("totalRuns")
    if declared is not None and abs(recomputed - float(declared)) > MEAN_TOLERANCE:
        raise TotalsIndexError(
            f"recomputed mean {recomputed!r} does not match the declared "
            f"{declared!r}; the stored population is not the indexed one"
        )

    return {
        "producer_pointer": PRODUCER_POINTER,
        "consumer_field": CONSUMER_FIELD,
        "pmf_source": "ALREADY_IMPORTED_ORDINARY_BASELINE_NOT_COPIED",
        "pmf_bin_count": len(stored_pmf),
        "pmf_mass": round(mass, 10),
        "pmf_support": [min(stored_pmf), max(stored_pmf)],
        "recomputed_mean_total_runs": recomputed,
        "declared_mean_total_runs": float(declared) if declared is not None else None,
        "checks": [
            "INDEX_PHYSICAL_SHA256",
            "MANIFEST_FILE_SHA256_AND_BYTES",
            "INDEXED_FORECAST_FILE_SHA256",
            "BASELINE_FORECAST_ID_AND_OBJECT_SHA",
            "CANONICAL_GAME_ID_AND_MLBAM_GAMEPK",
            "ENGINE_VERSION_SIMULATOR_SEED",
            "DENOMINATOR_AND_POPULATION_ACCOUNTING",
            "PMF_EQUALITY_BIN_FOR_BIN",
            "PMF_MASS_SUMS_TO_ONE",
            "FULL_PRECISION_MEAN",
        ],
    }


# ---------------------------------------------------------------------------
# reading attachment status back
# ---------------------------------------------------------------------------


def attachment_for(
    session: Session, forecast_identity: str,
) -> TotalsIndexAttachment | None:
    """The newest verified attachment for one forecast identity, if any."""

    return session.scalar(
        select(TotalsIndexAttachment)
        .where(
            TotalsIndexAttachment.forecast_identity == forecast_identity,
            TotalsIndexAttachment.pmf_matches_stored.is_(True),
        )
        .order_by(TotalsIndexAttachment.attached_at_utc.desc())
        .limit(1)
    )


def attachment_map(session: Session) -> dict[str, TotalsIndexAttachment]:
    """Every verified attachment, keyed by forecast identity.

    One query for a whole board. Doing it per row turned a fifteen-game slate
    into fifteen extra round trips for a field that decorates every one of them.
    """

    rows = session.scalars(
        select(TotalsIndexAttachment)
        .where(TotalsIndexAttachment.pmf_matches_stored.is_(True))
        .order_by(TotalsIndexAttachment.attached_at_utc)
    ).all()
    return {row.forecast_identity: row for row in rows}


def acknowledgement(
    verified: VerifiedIndex,
    outcome: AttachmentOutcome,
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    """The consumer acknowledgement the contract asks to be returned.

    ``accepted`` and ``alreadyAttached`` describe THIS run. A re-run of an
    already-complete attachment would otherwise report "accepted: 0", which is
    true of the run and misleading about the state. When a session is given, the
    cumulative total for this index is reported alongside, and that is the
    number that answers "what does AISP hold".
    """

    index = verified.index
    held: dict[str, Any] | None = None
    if session is not None:
        rows = session.scalars(
            select(TotalsIndexAttachment).where(
                TotalsIndexAttachment.index_sha256 == verified.index_sha256,
                TotalsIndexAttachment.pmf_matches_stored.is_(True),
            )
        ).all()
        held = {
            "forecastVersionsAttached": len(rows),
            "distinctGamesAttached": len({row.mlbam_game_pk for row in rows}),
            "forecastIds": sorted(row.forecast_identity for row in rows),
            "firstAttachedAtUtc": (
                min(row.attached_at_utc for row in rows).isoformat() if rows else None),
        }
    return {
        "schemaVersion": "AISP_MLB_V18_TOTALS_INDEX_CONSUMER_ACK_V1",
        "indexSha256": verified.index_sha256,
        "indexBytes": verified.index_bytes,
        "indexSchemaVersion": verified.schema_version,
        "manifestFilesVerified": verified.manifest_files_verified,
        "probabilityUnits": index.get("probabilityUnits"),
        # Four clocks, none standing in for another.
        "clocks": {
            "producerSourceInventoryAtUtc": index.get("sourceInventoryAtUtc"),
            "producerExportCreatedAtUtc": index.get("exportCreatedAtUtc"),
            "producerDerivativePublishedAtUtc": (
                verified.derivative_published_at_utc.isoformat()
                if verified.derivative_published_at_utc else None),
            "consumerAttachedAtUtc": outcome.attached_at_utc.isoformat(),
            "note": (
                "consumerAttachedAtUtc is AISP's own clock and never backdates "
                "the producer's. Original forecast clocks are unchanged; this "
                "derivative establishes no pregame availability."
            ),
        },
        "producerDeclaredCoverage": {
            "distinctGameCount": index.get("distinctGameCount"),
            "forecastVersionCount": index.get("forecastVersionCount"),
            "coverageByDate": index.get("coverageByDate"),
        },
        "consumerHoldsForThisIndex": held,
        "consumerResultThisRun": {
            "acceptedForecastIds": sorted(outcome.accepted),
            "alreadyAttachedForecastIds": sorted(outcome.already_attached),
            "acceptedCount": len(outcome.accepted),
            "alreadyAttachedCount": len(outcome.already_attached),
            "distinctGamesBound": len(outcome.distinct_games),
            "missingBaselines": outcome.missing_baselines,
            "rejected": outcome.rejected,
        },
        "pmfHandling": {
            "copied": False,
            "source": CONSUMER_FIELD,
            "producerPointer": PRODUCER_POINTER,
            "detail": (
                "No distribution was copied. Each accepted row records that the "
                "already-imported ordinary baseline's stored PMF equalled the "
                "index's bin for bin, with mass 1 and a matching full-precision "
                "mean."
            ),
        },
        "authority": {
            "newForecastGenerated": False,
            "newAuthorityGranted": False,
            "promotes": False,
            "wageringAuthority": False,
            "providerRequests": 0,
        },
    }


def _aware(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = [
    "CONSUMER_FIELD",
    "FRACTION_UNITS",
    "INDEX_SCHEMA",
    "PRODUCER_POINTER",
    "AttachmentOutcome",
    "TotalsIndexError",
    "VerifiedIndex",
    "acknowledgement",
    "attach",
    "attachment_for",
    "attachment_map",
    "verify_package",
]
