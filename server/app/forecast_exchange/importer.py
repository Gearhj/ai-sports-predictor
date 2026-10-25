"""Verify and load an immutable Model Studio daily forecast bundle.

Verification order (fail closed at the first violation):

1. Read the consumer import fixture and validate its shape.
2. Parse the logical URI and extract the SHA-256 address segment.
3. Confirm the address equals the fixture ``bundleSha256`` and that the
   fixture's four contract versions equal the versions this consumer supports.
4. Resolve ``bundle.json`` under a governed, link-free root.
5. Hash the exact bundle bytes and require equality with the address.
6. Parse the bundle with duplicate-key rejection and confirm canonical bytes.
7. Validate the closed bundle contract (unknown fields rejected).
8. Recompute the self-referential ``bundleId`` and every child ``forecastId``.
9. Enforce declared record counts and the research-shadow safety invariants
   (no execution, no promotion, no parlays, no 2025+ event data).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.forecast_exchange.contracts import (
    CONTRACT_VERSIONS,
    REJECTED_SEASON_FLOOR,
    ConsumerImportFixtureV1,
    DailyForecastBundleV1,
    GameForecastExportV1,
)
from app.forecast_exchange.sports_contracts import (
    NFLShadowBundleIndexV1,
    NFLShadowBundleIndexV2,
    SPORTS_CONTRACT_VERSIONS,
    SPORTS_FIXTURE_CONTRACT,
    SportsConsumerImportFixtureV1,
    SportsForecastBundleV1,
    SportsGameForecastV1,
)
from app.gen2.exchange.canonical_json import canonical_json_bytes, canonical_sha256
from app.gen2.exchange.filesystem import (
    ExchangePathError,
    assert_regular_independent_file,
    governed_root,
    resolve_under,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_URI_ADDRESS = re.compile(r"/sha256/([0-9a-f]{64})/")
_SPORTS_VALIDATION_V1 = b"""# ASP Sports Forecast Exchange V1 validation

1. Resolve only the exact bundleLogicalUri named by the import fixture.
2. Hash canonical bundle.json bytes and require the URI, fixture, and directory address to agree.
3. Reject duplicate JSON keys, unknown fields, invalid distributions, or invalid child content IDs.
4. Require NFL, 2026, RESEARCH_SHADOW, NOT_PROMOTED, and executionAllowed=false.
5. Verify exact canonical event/team IDs, pre-kickoff cutoffs, model manifest/binary hashes, and feature-vector hash.
6. This evidence authorizes no provider call, wager, model promotion, or official-bankroll mutation.
"""
_SPORTS_VALIDATION_V1_SHA256 = hashlib.sha256(_SPORTS_VALIDATION_V1).hexdigest()


class ForecastBundleImportError(RuntimeError):
    """A bundle is missing, corrupted, non-canonical, or unsafe to trust."""


class SportsIdentityInconsistencyError(ForecastBundleImportError):
    """One event's snapshots inside a single NFL bundle disagree on exact identity.

    Still a refusal: the bundle is never imported. It is raised only after every
    other check (address, canonical bytes, contract, child bindings, record
    count) has passed, so the kickoffs it carries are authentic producer claims
    and a caller may use them to tell a bundle whose games have all started
    from one that can still control a pregame decision.

    The real case is a moved kickoff: on 2026-10-11 bundle 77afaba3 carried
    2026_05_CHI_GB's EARLY snapshot at the 08-29 schedule (20:25Z) beside its
    CURRENT snapshot at the 10-05 schedule (17:00Z).
    """

    def __init__(self, message: str, *, event_id: str, latest_scheduled_start_utc: datetime) -> None:
        super().__init__(message)
        self.event_id = event_id
        #: The LATEST kickoff any child in the whole bundle claims, across every
        #: event and snapshot -- not just the conflicting one, because refusing
        #: the bundle also refuses every other child it carries.
        self.latest_scheduled_start_utc = latest_scheduled_start_utc


@dataclass(frozen=True)
class ImportedForecastBundle:
    bundle_dir: Path
    bundle_sha256: str
    bundle_id: str
    logical_uri: str
    fixture: ConsumerImportFixtureV1
    bundle: DailyForecastBundleV1
    raw_bundle: dict[str, Any]
    forecast_child_ids: tuple[str, ...]


@dataclass(frozen=True)
class ImportedSportsForecastBundle:
    """Verified prospective sport-neutral research-shadow bundle."""

    bundle_dir: Path
    bundle_sha256: str
    bundle_id: str
    logical_uri: str
    fixture: SportsConsumerImportFixtureV1
    bundle: SportsForecastBundleV1
    raw_bundle: dict[str, Any]
    forecast_child_ids: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedNFLShadowBundleIndex:
    family_root: Path
    sha256_root: Path
    index_path: Path
    index: NFLShadowBundleIndexV1 | NFLShadowBundleIndexV2


def _load_unique_json(raw: bytes, *, label: str) -> Any:
    def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        for key, value in pairs:
            if key in seen:
                raise ForecastBundleImportError(f"Duplicate JSON key in {label}: {key}")
            seen[key] = value
        return seen

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ForecastBundleImportError(f"{label} is not valid UTF-8 JSON") from exc


def verify_and_load_bundle(bundle_dir: Path) -> ImportedForecastBundle:
    """Verify one bundle directory and return its validated, hashed content."""

    try:
        root = governed_root(Path(bundle_dir))
        fixture_path = resolve_under(
            root, "consumer-import-fixture.json", require_exists=True, require_file=True
        )
        bundle_path = resolve_under(root, "bundle.json", require_exists=True, require_file=True)
        assert_regular_independent_file(fixture_path)
        assert_regular_independent_file(bundle_path)
    except ExchangePathError as exc:
        raise ForecastBundleImportError(f"Unsafe bundle location: {exc}") from exc

    # --- fixture ---------------------------------------------------------
    fixture_raw = _load_unique_json(fixture_path.read_bytes(), label="import fixture")
    try:
        fixture = ConsumerImportFixtureV1.model_validate(fixture_raw)
    except ValidationError as exc:
        raise ForecastBundleImportError("Import fixture is invalid") from exc

    if not fixture.research_shadow_only or fixture.execution_allowed:
        raise ForecastBundleImportError("Fixture does not authorize research-shadow-only import")
    if fixture.expected_contract_versions != CONTRACT_VERSIONS:
        raise ForecastBundleImportError("Fixture declares unsupported contract versions")
    if not _SHA256.fullmatch(fixture.bundle_sha256):
        raise ForecastBundleImportError("Fixture bundleSha256 is not a lowercase SHA-256")

    address = _uri_address(fixture.bundle_logical_uri)
    if address != fixture.bundle_sha256:
        raise ForecastBundleImportError("Logical URI address does not match bundleSha256")

    # --- complete bundle hash -------------------------------------------
    raw_bytes = bundle_path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if digest != address:
        raise ForecastBundleImportError(
            "bundle.json SHA-256 does not match the immutable address"
        )

    raw_bundle = _load_unique_json(raw_bytes, label="bundle")
    if not isinstance(raw_bundle, dict):
        raise ForecastBundleImportError("Bundle must be a JSON object")
    if raw_bytes != canonical_json_bytes(raw_bundle):
        raise ForecastBundleImportError("Bundle is not encoded with canonical exchange JSON")

    try:
        bundle = DailyForecastBundleV1.model_validate(raw_bundle)
    except ValidationError as exc:
        raise ForecastBundleImportError("Bundle failed the closed daily-forecast contract") from exc

    # --- self-referential and child identity ----------------------------
    _verify_child_binding(raw_bundle, "bundleId", bundle.bundle_id, label="bundle")
    raw_forecasts = raw_bundle.get("forecasts")
    if not isinstance(raw_forecasts, list) or len(raw_forecasts) != len(bundle.forecasts):
        raise ForecastBundleImportError("Forecast list is malformed")
    child_ids: list[str] = []
    for raw_forecast, forecast in zip(raw_forecasts, bundle.forecasts, strict=True):
        _verify_child_binding(raw_forecast, "forecastId", forecast.forecast_id, label="forecast")
        child_ids.append(forecast.forecast_id)

    # --- declared counts -------------------------------------------------
    counts = fixture.expected_record_counts
    if (
        len(bundle.forecasts) != counts.forecasts
        or len(bundle.market_quotes) != counts.market_quotes
        or len(bundle.bet_candidates) != counts.bet_candidates
    ):
        raise ForecastBundleImportError("Bundle record counts differ from the fixture")

    # --- research-shadow safety invariants ------------------------------
    _reject_unsafe_bundle(bundle)
    for forecast in bundle.forecasts:
        _reject_unsafe_forecast(forecast)
    _reject_identity_inconsistency(bundle)

    return ImportedForecastBundle(
        bundle_dir=root,
        bundle_sha256=digest,
        bundle_id=bundle.bundle_id,
        logical_uri=fixture.bundle_logical_uri,
        fixture=fixture,
        bundle=bundle,
        raw_bundle=raw_bundle,
        forecast_child_ids=tuple(child_ids),
    )


def verify_and_load_any_bundle(
    bundle_dir: Path,
) -> ImportedForecastBundle | ImportedSportsForecastBundle:
    """Dispatch by the closed fixture schema without weakening legacy V1.

    The legacy function remains untouched for callers that deliberately accept
    only historical MLB bundles.  The shadow service uses this dispatcher to
    add the prospective sport-neutral family.
    """

    try:
        root = governed_root(Path(bundle_dir))
        fixture_path = resolve_under(
            root, "consumer-import-fixture.json", require_exists=True, require_file=True
        )
        assert_regular_independent_file(fixture_path)
    except ExchangePathError as exc:
        raise ForecastBundleImportError(f"Unsafe bundle location: {exc}") from exc
    raw_fixture = _load_unique_json(fixture_path.read_bytes(), label="import fixture")
    if not isinstance(raw_fixture, dict):
        raise ForecastBundleImportError("Import fixture must be a JSON object")
    if raw_fixture.get("schemaVersion") == SPORTS_FIXTURE_CONTRACT:
        return verify_and_load_sports_bundle(root)
    return verify_and_load_bundle(root)


def verify_and_load_sports_bundle(bundle_dir: Path) -> ImportedSportsForecastBundle:
    """Verify one immutable ``ASP_SPORTS_FORECAST_EXCHANGE_V1`` bundle."""

    try:
        root = governed_root(Path(bundle_dir))
        fixture_path = resolve_under(
            root, "consumer-import-fixture.json", require_exists=True, require_file=True
        )
        bundle_path = resolve_under(root, "bundle.json", require_exists=True, require_file=True)
        validation_path = resolve_under(root, "VALIDATION.md", require_exists=True, require_file=True)
        assert_regular_independent_file(fixture_path)
        assert_regular_independent_file(bundle_path)
        assert_regular_independent_file(validation_path)
    except ExchangePathError as exc:
        raise ForecastBundleImportError(f"Unsafe bundle location: {exc}") from exc

    fixture_raw = _load_unique_json(fixture_path.read_bytes(), label="sports import fixture")
    try:
        fixture = SportsConsumerImportFixtureV1.model_validate(fixture_raw)
    except ValidationError as exc:
        raise ForecastBundleImportError("Sports import fixture is invalid") from exc
    if fixture.expected_contract_versions != SPORTS_CONTRACT_VERSIONS:
        raise ForecastBundleImportError("Sports fixture declares unsupported contract versions")
    _verify_child_binding(fixture_raw, "fixtureId", fixture.fixture_id, label="sports fixture")
    actual_inventory = {entry.name for entry in root.iterdir()}
    expected_inventory = {"bundle.json", "consumer-import-fixture.json", "VALIDATION.md"}
    if actual_inventory != expected_inventory:
        raise ForecastBundleImportError("Sports bundle directory inventory is not the exact three-file contract")
    validation_bytes = validation_path.read_bytes()
    if fixture.validation_instructions_sha256 != _SPORTS_VALIDATION_V1_SHA256:
        raise ForecastBundleImportError("Fixture does not bind the consumer-pinned V1 validation instructions")
    if validation_bytes != _SPORTS_VALIDATION_V1:
        raise ForecastBundleImportError("VALIDATION.md differs from the consumer-pinned V1 instructions")
    if hashlib.sha256(validation_bytes).hexdigest() != fixture.validation_instructions_sha256:
        raise ForecastBundleImportError("VALIDATION.md SHA-256 differs from the fixture authority")
    address = _uri_address(fixture.bundle_logical_uri)
    if address != fixture.bundle_sha256:
        raise ForecastBundleImportError("Sports logical URI address does not match bundleSha256")

    raw_bytes = bundle_path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if digest != address:
        raise ForecastBundleImportError("sports bundle.json SHA-256 does not match the immutable address")
    raw_bundle = _load_unique_json(raw_bytes, label="sports bundle")
    if not isinstance(raw_bundle, dict):
        raise ForecastBundleImportError("Sports bundle must be a JSON object")
    if raw_bytes != canonical_json_bytes(raw_bundle):
        raise ForecastBundleImportError("Sports bundle is not encoded with canonical exchange JSON")
    try:
        bundle = SportsForecastBundleV1.model_validate(raw_bundle)
    except ValidationError as exc:
        raise ForecastBundleImportError("Bundle failed the closed sports-forecast contract") from exc

    _verify_child_binding(raw_bundle, "bundleId", bundle.bundle_id, label="sports bundle")
    raw_forecasts = raw_bundle.get("forecasts")
    if not isinstance(raw_forecasts, list) or len(raw_forecasts) != len(bundle.forecasts):
        raise ForecastBundleImportError("Sports forecast list is malformed")
    child_ids: list[str] = []
    for raw_forecast, forecast in zip(raw_forecasts, bundle.forecasts, strict=True):
        _verify_child_binding(raw_forecast, "forecastId", forecast.forecast_id, label="sports forecast")
        _reject_unsafe_sports_forecast(forecast)
        child_ids.append(forecast.forecast_id)
    if len(bundle.forecasts) != fixture.expected_record_counts.forecasts:
        raise ForecastBundleImportError("Sports bundle record count differs from the fixture")
    _reject_sports_identity_inconsistency(bundle)
    return ImportedSportsForecastBundle(
        bundle_dir=root,
        bundle_sha256=digest,
        bundle_id=bundle.bundle_id,
        logical_uri=fixture.bundle_logical_uri,
        fixture=fixture,
        bundle=bundle,
        raw_bundle=raw_bundle,
        forecast_child_ids=tuple(child_ids),
    )


def verify_nfl_shadow_bundle_index(location: Path) -> VerifiedNFLShadowBundleIndex:
    """Verify the bounded mutable NFL locator without granting it scientific authority.

    ``location`` may name the locator, the family directory, or its ``sha256``
    child. Every listed object must still pass :func:`verify_and_load_sports_bundle`.
    """

    supplied = Path(location)
    if supplied.name == "nfl-2026-research-shadow-index.json":
        family_candidate = supplied.parent
    elif supplied.name == "sha256":
        family_candidate = supplied.parent
    else:
        family_candidate = supplied
    try:
        family_root = governed_root(family_candidate)
        index_path = resolve_under(
            family_root,
            "nfl-2026-research-shadow-index.json",
            require_exists=True,
            require_file=True,
        )
        sha256_root = resolve_under(
            family_root, "sha256", require_exists=True, require_file=False
        )
        assert_regular_independent_file(index_path)
        if not sha256_root.is_dir():
            raise ForecastBundleImportError("NFL exchange sha256 root is not a directory")
    except ExchangePathError as exc:
        raise ForecastBundleImportError(f"Unsafe NFL bundle index location: {exc}") from exc
    raw_bytes = index_path.read_bytes()
    raw_index = _load_unique_json(raw_bytes, label="NFL shadow bundle index")
    if not isinstance(raw_index, dict):
        raise ForecastBundleImportError("NFL shadow bundle index must be a JSON object")
    if raw_bytes != canonical_json_bytes(raw_index):
        raise ForecastBundleImportError("NFL shadow bundle index is not canonical JSON")
    schema_version = raw_index.get("schemaVersion")
    index_type = {
        "ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V1": NFLShadowBundleIndexV1,
        "ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V2": NFLShadowBundleIndexV2,
    }.get(schema_version)
    if index_type is None:
        raise ForecastBundleImportError("NFL shadow bundle index schema is unsupported")
    try:
        index = index_type.model_validate(raw_index)
    except ValidationError as exc:
        raise ForecastBundleImportError("NFL shadow bundle index is invalid") from exc
    return VerifiedNFLShadowBundleIndex(
        family_root=family_root,
        sha256_root=sha256_root,
        index_path=index_path,
        index=index,
    )


def _uri_address(uri: str) -> str:
    match = _URI_ADDRESS.search(uri)
    if not match:
        raise ForecastBundleImportError("Logical URI does not contain a sha256 address")
    return match.group(1)


def _verify_child_binding(raw_obj: object, id_field: str, declared_id: str, *, label: str) -> None:
    if not isinstance(raw_obj, dict):
        raise ForecastBundleImportError(f"{label} content is not an object")
    _, separator, hex_digest = declared_id.rpartition("sha256:")
    if separator != "sha256:" or not _SHA256.fullmatch(hex_digest):
        raise ForecastBundleImportError(f"{label} id is not a sha256-addressed identifier")
    projection = {key: value for key, value in raw_obj.items() if key != id_field}
    if canonical_sha256(projection) != hex_digest:
        raise ForecastBundleImportError(f"{label} content does not bind to its declared id")


def _reject_unsafe_bundle(bundle: DailyForecastBundleV1) -> None:
    if not bundle.research_shadow_only:
        raise ForecastBundleImportError("Bundle is not research-shadow-only")
    if bundle.execution_allowed:
        raise ForecastBundleImportError("Bundle asserts execution authority")
    if bundle.model_promotion_allowed:
        raise ForecastBundleImportError("Bundle asserts model-promotion authority")
    if bundle.parlays_allowed:
        raise ForecastBundleImportError("Bundle asserts parlay authority")


def _reject_unsafe_forecast(forecast: GameForecastExportV1) -> None:
    if not forecast.research_shadow_only:
        raise ForecastBundleImportError("Forecast is not research-shadow-only")
    if forecast.execution_allowed or forecast.model_promotion_allowed:
        raise ForecastBundleImportError("Forecast asserts execution or promotion authority")
    if forecast.season >= REJECTED_SEASON_FLOOR or forecast.event_date.year >= REJECTED_SEASON_FLOOR:
        raise ForecastBundleImportError("Forecast carries 2025+ event data")
    if forecast.home_team.team_id == forecast.away_team.team_id:
        raise ForecastBundleImportError("Forecast home and away identity collide")


def _reject_identity_inconsistency(bundle: DailyForecastBundleV1) -> None:
    """Every engine forecasting one event must agree on the mapped identity."""

    identities: dict[str, tuple[str, str, Any, int, str]] = {}
    for forecast in bundle.forecasts:
        identity = (
            forecast.home_team.team_id,
            forecast.away_team.team_id,
            forecast.event_date,
            forecast.season,
            forecast.source_event_id,
        )
        existing = identities.setdefault(forecast.event_id, identity)
        if existing != identity:
            raise ForecastBundleImportError(
                f"Event {forecast.event_id} has inconsistent team/identity mapping across engines"
            )


def _reject_unsafe_sports_forecast(forecast: SportsGameForecastV1) -> None:
    if forecast.source_event_id != f"nflverse:{forecast.event_id}":
        raise ForecastBundleImportError("NFL source event identity is not exact/provider-qualified")
    if not forecast.home_team.team_id.strip() or not forecast.away_team.team_id.strip():
        raise ForecastBundleImportError("NFL canonical team identity is absent")
    if not forecast.home_team.source_team_id.strip() or not forecast.away_team.source_team_id.strip():
        raise ForecastBundleImportError("NFL source team identity is absent")


def _reject_sports_identity_inconsistency(bundle: SportsForecastBundleV1) -> None:
    identities: dict[str, tuple[str, str, str, str, Any, int, str]] = {}
    for forecast in bundle.forecasts:
        identity = (
            forecast.home_team.team_id,
            forecast.away_team.team_id,
            forecast.home_team.source_team_id,
            forecast.away_team.source_team_id,
            forecast.scheduled_start_utc,
            forecast.season,
            forecast.source_event_id,
        )
        existing = identities.setdefault(forecast.event_id, identity)
        if existing != identity:
            raise SportsIdentityInconsistencyError(
                f"NFL event {forecast.event_id} has inconsistent exact identity across snapshots",
                event_id=forecast.event_id,
                latest_scheduled_start_utc=max(row.scheduled_start_utc for row in bundle.forecasts),
            )


__all__ = [
    "ForecastBundleImportError",
    "ImportedForecastBundle",
    "ImportedSportsForecastBundle",
    "SportsIdentityInconsistencyError",
    "verify_and_load_any_bundle",
    "verify_and_load_bundle",
    "verify_and_load_sports_bundle",
]
