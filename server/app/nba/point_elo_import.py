"""Importing the NBA Gen-1 point/Elo research forecast, exactly as delivered.

WHAT THIS IMPORTS, AND WHAT IT REFUSES TO INVENT
------------------------------------------------
Two labelled outputs from one package: a ridge point expectation and an Elo win
probability. They are separate models and the package says so, so nothing here
combines them, reconciles them when they disagree, or presents either as the
other's confidence. ``drawCount`` is zero and every distribution is null; that
stays true through the import, because a total of 225.72 points is not a
distribution and deriving an Over/Under from it would be manufacturing one.

IDENTITY
--------
Keyed on the original ``forecastId`` and the forecast file's own SHA-256. The
same bytes import once and re-import as a no-op; different bytes under the same
id are rejected rather than merged, because two different forecasts sharing an
identity is the one failure that cannot be untangled afterwards.

The game is reconciled through the source identities the package carries --
ESPN game id and the two franchise ids -- never through team names and a date.
Boston at Detroit on a given night is a join that works until the night two
teams play twice.

FOUR CLOCKS, KEPT APART
-----------------------
``sourceCutoffUtc``   what the model was allowed to know.
``createdAtUtc``      when the forecast was computed.
``exportedAtUtc``     when it was written locally.
delivery receipt      when it actually arrived here.

The forecast's own ``publishedAtUtc`` is null and stays null: it describes the
producer's local state, and overwriting it with the exchange delivery time would
backdate a publication that had not happened.

AUTHORITY
---------
The package declares ``researchOnly`` with wagering, production, scheduler and
account-creation authority all false. Importing it creates no account, no
wager, no staking policy and no recurring publisher, and this module has no code
path that could.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORECAST_SCHEMA = "NBA_GEN1_REDUCED_POINT_ELO_FORECAST_V1"
ENVELOPE_SCHEMA = "NBA_GEN1_POINT_ELO_EXCHANGE_INDEX_V1"
ENGINE_ID = "NBA_GEN1_POINT_ELO"


class PointEloImportError(ValueError):
    """The package cannot be verified or reconciled, and the message says why."""


@dataclass(frozen=True)
class VerifiedPackage:
    """Bytes that matched their manifest, and the values inside them."""

    root: Path
    index: dict[str, Any]
    forecast: dict[str, Any]
    index_sha256: str
    index_bytes: int
    forecast_sha256: str
    forecast_bytes: int
    files_verified: list[str]
    delivered_at: datetime | None


def _sha256(path: Path) -> tuple[str, int]:
    raw = path.read_bytes()
    return hashlib.sha256(raw).hexdigest(), len(raw)


def verify_package(
    index_path: Path | str, *, expected_index_sha256: str | None = None,
) -> VerifiedPackage:
    """Physical bytes first: the index, then every file it declares.

    No normalisation of any kind. A file whose bytes or length differ from the
    manifest fails the whole package rather than being read anyway.
    """

    path = Path(index_path)
    if not path.is_file():
        raise PointEloImportError(f"no index at {path}")
    index_sha, index_bytes = _sha256(path)
    if expected_index_sha256 and index_sha != expected_index_sha256:
        raise PointEloImportError(
            f"index hashes to {index_sha}, not {expected_index_sha256}")

    index = json.loads(path.read_bytes())
    if index.get("schemaVersion") != ENVELOPE_SCHEMA:
        raise PointEloImportError(
            f"envelope {index.get('schemaVersion')!r} is not {ENVELOPE_SCHEMA}")

    root = path.parent
    verified: list[str] = []
    for entry in index.get("files") or []:
        member = root / entry["relativePath"]
        if not member.is_file():
            raise PointEloImportError(f"index names a missing file: {entry['relativePath']}")
        digest, size = _sha256(member)
        if digest != entry["sha256"] or size != entry["bytes"]:
            raise PointEloImportError(
                f"{entry['relativePath']} does not match its declared bytes")
        verified.append(entry["relativePath"])

    forecast_sha, forecast_bytes = _sha256(root / "forecast.json")
    if index.get("forecastSha256") and forecast_sha != index["forecastSha256"]:
        raise PointEloImportError("forecast.json does not match the declared digest")

    forecast = json.loads((root / "forecast.json").read_bytes())
    if forecast.get("schema") != FORECAST_SCHEMA:
        raise PointEloImportError(
            f"forecast schema {forecast.get('schema')!r} is not {FORECAST_SCHEMA}")

    authority = forecast.get("authority") or {}
    for claim in ("wageringAuthority", "productionAuthority",
                  "schedulerAuthority", "accountCreationAuthority"):
        if authority.get(claim):
            raise PointEloImportError(
                f"forecast claims {claim}; this importer accepts research only")

    # The delivery clock lives in the receipt beside the index, never in the
    # forecast: the forecast's own publishedAtUtc describes the producer's local
    # state and is not rewritten.
    delivered_at = None
    receipt_path = root / "publication-receipt.json"
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_bytes())
        raw = receipt.get("publishedAtUtc") or receipt.get("published_at_utc")
        if isinstance(raw, str):
            delivered_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))

    return VerifiedPackage(
        root=root, index=index, forecast=forecast,
        index_sha256=index_sha, index_bytes=index_bytes,
        forecast_sha256=forecast_sha, forecast_bytes=forecast_bytes,
        files_verified=verified, delivered_at=delivered_at,
    )


def display_values(forecast: dict[str, Any]) -> dict[str, Any]:
    """The card's numbers, with each output kept under its own name.

    Probabilities stay fractions here. The one multiplication by 100 happens at
    the point of display, once, so a percentage cannot be computed twice on its
    way to a screen.
    """

    points = forecast.get("pointExpectations") or {}
    winner = forecast.get("winnerProbabilities") or {}
    distributions = forecast.get("distributions") or {}
    game = forecast.get("game") or {}

    return {
        "label": "Early research forecast — September 14, 2026",
        "matchup": f"{game.get('away_team_code')} at {game.get('home_team_code')}",
        "ridge_expected_points": {
            "away": points.get("away"),
            "home": points.get("home"),
            "total": points.get("total"),
            "home_margin": points.get("homeMargin"),
            "units": points.get("units"),
            "model": "Ridge alpha100",
        },
        "elo_win_probability": {
            "away": winner.get("away"),
            "home": winner.get("home"),
            "tie": winner.get("tie"),
            "units": winner.get("units"),
            "method": winner.get("method"),
            "model": "Elo — separate model output",
        },
        # Explicit, so a reader is never left to infer that a missing chart
        # means a chart that failed to load.
        "distributions": {
            "draw_count": distributions.get("drawCount"),
            "status": distributions.get("status"),
            "home": distributions.get("home"),
            "away": distributions.get("away"),
            "total": distributions.get("total"),
            "margin": distributions.get("margin"),
            "intervals": distributions.get("intervals"),
            "note": (
                "No simulated draws were retained, so run distributions, "
                "prediction intervals, Monte Carlo error, Over/Under "
                "probabilities and market fair prices are unavailable. They are "
                "not derived from the expected total."
            ),
        },
        "captured_schedule": {
            "tipoff_utc": game.get("scheduled_tipoff_utc"),
            "game_date": game.get("game_date"),
            "source_revision": game.get("source_revision"),
            "neutral_site": game.get("neutral_site"),
        },
        "rest_support": forecast.get("restSupportAudit"),
        "limitations": forecast.get("limitations"),
        "bindings": forecast.get("bindings"),
        "authority": forecast.get("authority"),
    }


def source_identity(forecast: dict[str, Any]) -> dict[str, Any]:
    """The identities the package carries, for reconciliation and for the record."""

    game = forecast.get("game") or {}
    return {
        "canonical_game": game.get("game_id"),
        "espn_game_id": str(game.get("source_game_id") or ""),
        "home_franchise": game.get("home_team_id"),
        "away_franchise": game.get("away_team_id"),
        "home_code": game.get("home_team_code"),
        "away_code": game.get("away_team_code"),
        "source_revision": game.get("source_revision"),
        "season_end": game.get("season_end"),
        "season_type": game.get("season_type"),
        "status": game.get("status"),
    }


def clocks(package: VerifiedPackage) -> dict[str, Any]:
    """Four separate times, none standing in for another."""

    forecast = package.forecast
    return {
        "source_cutoff_utc": forecast.get("sourceCutoffUtc"),
        "forecast_created_utc": forecast.get("createdAtUtc"),
        "local_export_utc": forecast.get("exportedAtUtc"),
        # Null in the artifact and left null: it describes the producer's local
        # state, not this delivery.
        "producer_published_utc": forecast.get("publishedAtUtc"),
        "exchange_delivered_utc": (
            package.delivered_at.isoformat() if package.delivered_at else None),
        "note": (
            "The forecast's own publishedAtUtc is null and is not rewritten. "
            "The exchange delivery time comes from publication-receipt.json."
        ),
    }


__all__ = [
    "ENGINE_ID",
    "ENVELOPE_SCHEMA",
    "FORECAST_SCHEMA",
    "PointEloImportError",
    "VerifiedPackage",
    "clocks",
    "display_values",
    "source_identity",
    "verify_package",
]
