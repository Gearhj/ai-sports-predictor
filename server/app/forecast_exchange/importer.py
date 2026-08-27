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
from app.gen2.exchange.canonical_json import canonical_json_bytes, canonical_sha256
from app.gen2.exchange.filesystem import (
    ExchangePathError,
    assert_regular_independent_file,
    governed_root,
    resolve_under,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_URI_ADDRESS = re.compile(r"/sha256/([0-9a-f]{64})/")


class ForecastBundleImportError(RuntimeError):
    """A bundle is missing, corrupted, non-canonical, or unsafe to trust."""


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


__all__ = [
    "ForecastBundleImportError",
    "ImportedForecastBundle",
    "verify_and_load_bundle",
]
