"""Verified-population adapter: one canonical game, one exact forecast revision.

AISP chooses the game and the forecast revision; this module proves the
retained paired final-score population belongs to that revision and hands it
to the producer's pure contract (``market_probabilities``). It never runs a
model, never calls a provider and never writes a row.

Fail-closed rules
-----------------
* The population is read from the immutable exchange artifact the forecast
  was imported from and re-hashed against the digests AISP recorded at import
  (plus the producer's logical digest where one exists).
* Its cell counts must equal the declared draw count, and the winner/tie mass
  it implies must equal the probabilities AISP stored for the same forecast.
* Model and configuration identities must be the admitted ones below.
* A caller's population binding must equal the current binding field for
  field; an older revision, another game or another hash is refused.
* A point/Elo forecast has no population. It answers the winner channel only
  and is never substituted for a paired population.

Any disagreement raises :class:`ProbabilityUnavailable` with a stable code.
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
import threading
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.probability.market_probabilities import (
    JointScorePopulation,
    MarketProbabilityError,
)

SCHEMA = "AISP_PROBABILITY_BINDING_V1"

#: AISP's governed two-way moneyline rule: a tied final is refunded. This is
#: the rule ``forecast_exchange.pricing`` already applies to NFL decisions; for
#: the other sports the admitted populations carry no tie mass.
MONEYLINE_TIE_SETTLEMENT = "PUSH"

RESEARCH_ONLY = "RESEARCH_ONLY"
PAPER_RESEARCH = "PAPER_RESEARCH_EVIDENCE"
CONTROLLING = "CONTROLLING_VIRTUAL_RESEARCH"

#: Admitted identities, from the producer capability matrix and AISP's own
#: import records. A new model, configuration or parent requires review.
ADMITTED = {
    "MLB": {
        "engine_id": "MLB_V18_WHOLE_GAME",
        "model_id": "MLB_V18_HGB_PA",
        "configuration_id": "UNRESTRICTED_V18_FOLD5_PA_TRANSITION_FOLD4",
        "model_artifact_sha256": "44ec4f457741f1aaeff42b81af1be5fd2712e494709ff1a0a8f0742b88d71c8b",
        "parent_bundle_sha256": "a6e7e99122f1b60701a4728b97b32fcc11e03405bfd456ed6cd54f69b69adb27",
        "draw_count": 5000,
        "authority": CONTROLLING,
        "authority_label": "Controlling MLB forecast (V18) for the virtual research experiment",
        "tie_policy": "ZERO_TIE_FINAL",
    },
    "NFL": {
        "engine_id": "NFL_M1_XGBOOST",
        "model_id": "NFL_M1_XGBOOST",
        "configuration_id": "NFL_M1_2026_RESEARCH_SHADOW_V1",
        "model_artifact_sha256": "7bec06646fd5035380107501d07e139425ac483a47e02e0bbbd56301124fb8fd",
        "draw_count": 10000,
        "authority": PAPER_RESEARCH,
        "authority_label": "NFL M1 research/paper probability evidence",
        "tie_policy": "DISCRETE_TIE_MASS_NOT_SEPARATELY_VALIDATED",
    },
    "NBA": {
        "model_id": "NBA_GEN1_JOINT_BASELINE",
        "configuration_id": "NBA_GEN1_JOINT_FINAL_PAIRED_OOF_LATTICE_V1",
        "arm": "baseline",
        "draw_count": 10000,
        "authority": RESEARCH_ONLY,
        "authority_label": "NBA joint baseline — research only",
        "tie_policy": "ZERO_TIE_FINAL",
    },
    "CFB": {
        "model_id": "CFB_GEN1_RIDGE_A100",
        "configuration_id": "CFB_GEN1_RIDGE_A100_PAIRED_RESIDUAL_V1",
        "draw_count": 10000,
        "authority": RESEARCH_ONLY,
        "authority_label": "College Football paired-residual baseline — research only",
        "tie_policy": "ZERO_TIE_FINAL",
    },
}

SUPPORTED_ALL = {"moneyline": True, "spread": True, "total": True, "same_game": True}
WINNER_ONLY = {"moneyline": True, "spread": False, "total": False, "same_game": False}
_TOLERANCE = 1e-9


class ProbabilityUnavailable(Exception):
    """A stable, consumer-facing reason no verified population is available."""

    def __init__(self, code: str, message: str, *, status: int = 422):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass
class VerifiedChannel:
    """A bound, verified paired population plus the forecast facts shown beside it."""

    sport: str
    population: JointScorePopulation
    authority: str
    authority_label: str
    tie_policy: str
    forecast_created_at: datetime | None
    source_cutoff: datetime | None
    horizon: str | None
    stored: dict[str, float | None]
    expected: dict[str, float | None]
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def binding(self) -> dict[str, Any]:
        return self.population.binding

    def describe(self) -> dict[str, Any]:
        return {
            "channel": "PAIRED_POPULATION",
            "status": "SUPPORTED",
            "binding": self.binding,
            "capabilities": dict(SUPPORTED_ALL),
            "authority": self.authority,
            "authority_label": self.authority_label,
            "tie_policy": self.tie_policy,
            "moneyline_tie_settlement": MONEYLINE_TIE_SETTLEMENT,
            "forecast_created_at": _iso(self.forecast_created_at),
            "source_cutoff": _iso(self.source_cutoff),
            "horizon": self.horizon,
            "winner": self.stored,
            "expected_score": self.expected,
            **self.extra,
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def exchange_root(settings: Settings) -> Path:
    return Path(settings.probability_exchange_root)


# ------------------------------------------------------------------ cache --

_CACHE: OrderedDict[tuple, tuple[tuple[dict[str, int], ...], int]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_SIZE = 96


def _cached_cells(key: tuple, build) -> tuple[tuple[dict[str, int], ...], int]:
    """Cells keyed by forecast id and every verified digest; bytes re-read on miss."""

    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    value = build()
    with _CACHE_LOCK:
        _CACHE[key] = value
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    return value


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _cells_from_pairs(away: list[int], home: list[int]) -> tuple[dict[str, int], ...]:
    counts: dict[tuple[int, int], int] = {}
    for a, h in zip(away, home, strict=True):
        counts[(int(a), int(h))] = counts.get((int(a), int(h)), 0) + 1
    return tuple({"awayScore": a, "homeScore": h, "count": n} for (a, h), n in sorted(counts.items()))


def _read_verified(path: Path, *, sha256: str, size: int | None = None) -> bytes:
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise ProbabilityUnavailable(
            "POPULATION_SOURCE_UNREADABLE", f"retained population file is unreadable ({type(exc).__name__})",
            status=503) from exc
    if hashlib.sha256(raw).hexdigest() != str(sha256).lower() or (size is not None and len(raw) != int(size)):
        raise ProbabilityUnavailable("PACKAGE_HASH_MISMATCH", f"{path.name} does not match its recorded digest")
    return raw


def _inside(root: Path, path: Path) -> Path:
    resolved, base = path.resolve(), root.resolve()
    if resolved != base and base not in resolved.parents:
        raise ProbabilityUnavailable("PACKAGE_PATH_INVALID", "population source is outside the model exchange")
    return resolved


def _population(sport: str, *, game_id: str, forecast_id: str, package_sha256: str,
                cells, draw_count: int, expected_population_sha256: str | None = None) -> JointScorePopulation:
    admitted = ADMITTED[sport]
    if int(draw_count) != admitted["draw_count"]:
        raise ProbabilityUnavailable("DRAW_COUNT_MISMATCH",
                                     f"{sport} population declares {draw_count} draws, admitted {admitted['draw_count']}")
    try:
        return JointScorePopulation.from_cells(
            sport=sport, game_id=game_id, forecast_id=forecast_id,
            model_id=admitted["model_id"], configuration_id=admitted["configuration_id"],
            package_sha256=package_sha256, cells=list(cells), draw_count=int(draw_count),
            expected_population_sha256=expected_population_sha256,
        )
    except MarketProbabilityError as exc:
        raise ProbabilityUnavailable(exc.code, exc.message) from exc


def _reconcile(population: JointScorePopulation, home: float | None, away: float | None,
               tie: float | None) -> None:
    n = population.draw_count
    derived = {
        "home": sum(c.count for c in population.cells if c.home_score > c.away_score) / n,
        "away": sum(c.count for c in population.cells if c.away_score > c.home_score) / n,
        "tie": sum(c.count for c in population.cells if c.away_score == c.home_score) / n,
    }
    for name, stored in (("home", home), ("away", away), ("tie", tie)):
        if stored is None:
            continue
        if abs(derived[name] - float(stored)) > _TOLERANCE:
            raise ProbabilityUnavailable(
                "POPULATION_RECONCILIATION_FAILED",
                f"population {name} mass {derived[name]:.6f} differs from the stored forecast {float(stored):.6f}")


# ------------------------------------------------------------- MLB / NFL --

def _latest_shadow(session: Session, game_id: uuid.UUID, engine_id: str):
    from app.models import ShadowForecast

    return session.scalar(
        select(ShadowForecast)
        .where(ShadowForecast.canonical_game_id == game_id, ShadowForecast.engine_id == engine_id)
        .order_by(ShadowForecast.created_at.desc(), ShadowForecast.id.desc())
        .limit(1)
    )


def _bundle_sha(session: Session, row) -> str:
    from app.models import ShadowForecastBundle

    return session.scalar(select(ShadowForecastBundle.bundle_sha256)
                          .where(ShadowForecastBundle.id == row.bundle_id)) or ""


def _mlb_channel(session: Session, settings: Settings, game_id: uuid.UUID) -> VerifiedChannel:
    from app.shadow_import.mlb_v18_daily import _canonical_bytes

    admitted = ADMITTED["MLB"]
    row = _latest_shadow(session, game_id, admitted["engine_id"])
    if row is None:
        raise ProbabilityUnavailable("FORECAST_UNAVAILABLE", "no V18 forecast is bound to this game", status=404)
    lineage = row.lineage_detail or {}
    object_sha = str(lineage.get("objectSha256") or "").lower()
    if (row.forecast_id != f"mlb-v18-daily-{object_sha}" or len(object_sha) != 64
            or str(lineage.get("parentBundleSha256") or "").lower() != admitted["parent_bundle_sha256"]
            or row.model_artifact_sha256 != admitted["model_artifact_sha256"]):
        raise ProbabilityUnavailable("IDENTITY_MISMATCH", "V18 forecast is not the admitted unrestricted configuration")
    if int((row.uncertainty or {}).get("simulationCount") or 0) != admitted["draw_count"]:
        raise ProbabilityUnavailable("DRAW_COUNT_MISMATCH", "V18 forecast does not declare 5,000 simulations")
    source = lineage.get("sourceArtifact")
    if not source:
        raise ProbabilityUnavailable("POPULATION_UNAVAILABLE", "V18 forecast records no source object")
    root = _inside(exchange_root(settings) / "mlb-v18-current", Path(str(source)))
    if root.name.lower() != object_sha:
        raise ProbabilityUnavailable("PACKAGE_HASH_MISMATCH", "source object directory is not the forecast's object")

    def build():
        try:
            manifest = json.loads((root / "manifest.json").read_bytes())
        except (FileNotFoundError, PermissionError, OSError, ValueError) as exc:
            raise ProbabilityUnavailable("POPULATION_SOURCE_UNREADABLE",
                                         f"V18 object manifest is unreadable ({type(exc).__name__})", status=503) from exc
        body = {k: v for k, v in manifest.items() if k not in {"objectId", "objectSha256"}}
        if (str(manifest.get("objectSha256") or "").lower() != object_sha
                or hashlib.sha256(_canonical_bytes(body)).hexdigest() != object_sha):
            raise ProbabilityUnavailable("PACKAGE_HASH_MISMATCH", "V18 object manifest checksum mismatch")
        entry = next((a for a in manifest.get("artifacts") or []
                      if a.get("relativePath") == "execution-inputs.json"), None)
        if entry is None:
            raise ProbabilityUnavailable("POPULATION_UNAVAILABLE", "V18 object retains no paired score population")
        raw = _read_verified(root / "execution-inputs.json", sha256=entry.get("sha256", ""), size=entry.get("bytes"))
        population = (json.loads(raw) or {}).get("population") or {}
        away, home = population.get("awayRuns"), population.get("homeRuns")
        count = int(population.get("simulationCount") or 0)
        if not isinstance(away, list) or not isinstance(home, list) or not (len(away) == len(home) == count):
            raise ProbabilityUnavailable("DRAW_COUNT_MISMATCH", "V18 paired arrays do not match simulationCount")
        return _cells_from_pairs(away, home), count

    cells, count = _cached_cells(("MLB", row.forecast_id, object_sha), build)
    population = _population("MLB", game_id=str(game_id), forecast_id=row.forecast_id,
                             package_sha256=object_sha, cells=cells, draw_count=count)
    _reconcile(population, row.full_home_win_probability, row.full_away_win_probability, 0.0)
    return VerifiedChannel(
        "MLB", population, admitted["authority"], admitted["authority_label"], admitted["tie_policy"],
        row.created_at, row.source_cutoff_utc, row.snapshot_type,
        {"home": row.full_home_win_probability, "away": row.full_away_win_probability, "tie": 0.0},
        {"home": row.full_expected_home_score, "away": row.full_expected_away_score},
    )


def verified_nfl_population(session: Session, row, *, game_id: uuid.UUID | None = None):
    """The verified paired population of ONE stored NFL M1 forecast revision.

    The same checks the live channel applies, for any revision rather than only
    the latest import: admitted configuration and artifact, completed draws equal
    to declared draws, whole-draw joint cells, and home/away/tie reconciliation
    against the stored probabilities. Raises ProbabilityUnavailable on any
    failure; never substitutes another population.
    """

    admitted = ADMITTED["NFL"]
    if (row.engine_version != admitted["configuration_id"]
            or row.model_artifact_sha256 != admitted["model_artifact_sha256"]):
        raise ProbabilityUnavailable("IDENTITY_MISMATCH", "NFL forecast is not the admitted M1 configuration")
    lineage = row.lineage_detail or {}
    draws = int(lineage.get("completed_simulations") or 0)
    if draws != int(lineage.get("simulation_count") or -1):
        raise ProbabilityUnavailable("DRAW_COUNT_MISMATCH", "completed and declared NFL simulations differ")
    package = _bundle_sha(session, row)
    distributions = row.distributions or {}

    def build():
        joint = distributions.get("joint_score_distribution")
        if not isinstance(joint, list) or not joint:
            raise ProbabilityUnavailable("POPULATION_UNAVAILABLE", "NFL forecast retains no joint score distribution")
        cells = []
        for cell in joint:
            exact = float(cell["probability"]) * draws
            count = round(exact)
            if abs(exact - count) > 1e-6:
                raise ProbabilityUnavailable("INVALID_SCORE_POPULATION",
                                             "NFL joint probabilities are not whole draw counts")
            if count:
                cells.append({"awayScore": int(cell["away_score"]), "homeScore": int(cell["home_score"]),
                              "count": count})
        return tuple(sorted(cells, key=lambda c: (c["awayScore"], c["homeScore"]))), draws

    cells, count = _cached_cells(("NFL", row.forecast_id, package), build)
    population = _population("NFL", game_id=str(game_id or row.canonical_game_id), forecast_id=row.forecast_id,
                             package_sha256=package, cells=cells, draw_count=count)
    _reconcile(population, row.full_home_win_probability, row.full_away_win_probability,
               distributions.get("tie_probability"))
    return population


def _nfl_channel(session: Session, settings: Settings, game_id: uuid.UUID) -> VerifiedChannel:
    """The governed controlling NFL forecast, selected by PUBLICATION time.

    The same rule the scorer applies, from the same implementation: the latest
    snapshot of the admitted M1 identity published strictly before kickoff.
    Import time is never the authority clock, so a snapshot that arrived late
    cannot displace what an observer would have been looking at, and a
    FINAL_PREGAME revision controls only when it was genuinely published later
    and still before kickoff.
    """

    from app.services.forecast_evaluation import controlling_forecast

    admitted = ADMITTED["NFL"]
    # Selected by model id, as before, so a forecast of the wrong configuration is
    # still refused as an IDENTITY_MISMATCH by verification rather than silently
    # reported as "no forecast".
    selection = controlling_forecast(session, canonical_game_id=game_id, engine_id=admitted["engine_id"])
    if selection is None:
        raise ProbabilityUnavailable(
            "FORECAST_UNAVAILABLE",
            "no NFL M1 forecast published before kickoff is bound to this game", status=404)
    row = selection.row
    population = verified_nfl_population(session, row, game_id=game_id)
    tie = (row.distributions or {}).get("tie_probability")
    return VerifiedChannel(
        "NFL", population, admitted["authority"], admitted["authority_label"], admitted["tie_policy"],
        row.created_at, row.source_cutoff_utc, f"{row.snapshot_type} v{row.snapshot_version}",
        {"home": row.full_home_win_probability, "away": row.full_away_win_probability, "tie": tie},
        {"home": row.full_expected_home_score, "away": row.full_expected_away_score},
        extra={"controlling_revision": {
            "reason": selection.reason,
            "forecast_id": row.forecast_id,
            "snapshot_type": row.snapshot_type,
            "snapshot_version": row.snapshot_version,
            "published_utc": _iso(selection.published_utc),
            "imported_utc": _iso(row.created_at),
            "kickoff_utc": _iso(selection.kickoff),
            "hours_before_kickoff": round(selection.lead_seconds / 3600, 2),
            "snapshots_considered": selection.considered,
            "excluded": selection.excluded,
        }},
    )


# -------------------------------------------------------------- NBA / CFB --

def _parquet(raw: bytes):
    import polars as pl

    return pl.read_parquet(io.BytesIO(raw))


def _by_prefix(columns: list[str], prefix: str) -> str:
    matches = [c for c in columns if c.startswith(prefix)]
    if len(matches) != 1:
        raise ProbabilityUnavailable("ORIENTATION_AMBIGUOUS", f"population needs exactly one {prefix} score column")
    return matches[0]


def _nba_channel(session: Session, settings: Settings, game_id: uuid.UUID) -> VerifiedChannel:
    from app.models import NbaJointComparisonArm

    admitted = ADMITTED["NBA"]
    arm = session.scalar(select(NbaJointComparisonArm).where(
        NbaJointComparisonArm.canonical_game_id == game_id, NbaJointComparisonArm.arm == admitted["arm"]))
    if arm is None:
        raise ProbabilityUnavailable("PAIRED_POPULATION_UNAVAILABLE",
                                     "no NBA joint baseline is bound to this game", status=404)
    if arm.configuration_identity != admitted["configuration_id"]:
        raise ProbabilityUnavailable("IDENTITY_MISMATCH", "NBA arm is not the admitted joint baseline")
    spec = (arm.population or {})
    artifact = spec.get("artifact") or {}
    logical = str(spec.get("logicalScorePairsInt64LeSha256") or "").lower()
    if int(spec.get("drawCount") or 0) != int(arm.draw_count) or not logical:
        raise ProbabilityUnavailable("DRAW_COUNT_MISMATCH", "NBA population record is incomplete")
    root = _inside(exchange_root(settings) / "nba-gen1-research-v1",
                   exchange_root(settings) / "nba-gen1-research-v1" / "joint-comparison-v1" / "packages"
                   / "sha256" / arm.package_sha256)

    def build():
        raw = _read_verified(_inside(root, root / str(artifact.get("relativePath") or "")),
                             sha256=artifact.get("sha256", ""), size=artifact.get("bytes"))
        frame = _parquet(raw)
        columns = list(frame.columns)
        frame = frame.sort("drawIndex")
        home = [int(v) for v in frame[_by_prefix(columns, "home")].to_list()]
        away = [int(v) for v in frame[_by_prefix(columns, "away")].to_list()]
        interleaved = [v for pair in zip(home, away, strict=True) for v in pair]
        if hashlib.sha256(struct.pack(f"<{len(interleaved)}q", *interleaved)).hexdigest() != logical:
            raise ProbabilityUnavailable("POPULATION_HASH_MISMATCH", "NBA logical score-pair digest differs")
        return _cells_from_pairs(away, home), len(home)

    cells, count = _cached_cells(("NBA", arm.forecast_id, arm.package_sha256, logical), build)
    if count != int(arm.draw_count):
        raise ProbabilityUnavailable("DRAW_COUNT_MISMATCH", "NBA population rows differ from its draw count")
    population = _population("NBA", game_id=str(game_id), forecast_id=arm.forecast_id,
                             package_sha256=arm.package_sha256, cells=cells, draw_count=count)
    _reconcile(population, arm.home_win_probability, arm.away_win_probability, arm.tie_probability)
    return VerifiedChannel(
        "NBA", population, admitted["authority"], admitted["authority_label"], admitted["tie_policy"],
        arm.created_at_utc, arm.source_cutoff_utc, "EARLY_RESEARCH_JOINT_BASELINE",
        {"home": arm.home_win_probability, "away": arm.away_win_probability, "tie": arm.tie_probability},
        {"home": arm.expected_home, "away": arm.expected_away},
        {"population_logical_sha256": logical},
    )


def _nba_point_channel(session: Session, game_id: uuid.UUID) -> dict[str, Any] | None:
    from app.models import NbaResearchForecast

    row = session.scalar(select(NbaResearchForecast)
                         .where(NbaResearchForecast.canonical_game_id == game_id)
                         .order_by(NbaResearchForecast.imported_at.desc()).limit(1))
    if row is None:
        return None
    return {
        "channel": "POINT_ELO",
        "status": "WINNER_ONLY",
        "forecast_id": row.forecast_id,
        "draw_count": int(row.draw_count or 0),
        "capabilities": dict(WINNER_ONLY),
        "authority": RESEARCH_ONLY,
        "authority_label": "NBA point/Elo package — research winner channel only",
        "winner": {"home": row.elo_win_probability_home, "away": row.elo_win_probability_away,
                   "tie": row.elo_tie_probability},
        "unsupported": {"code": "PAIRED_POPULATION_UNAVAILABLE",
                        "message": "point/Elo forecasts cannot answer spread, total or parlay queries"},
    }


def _cfb_channel(session: Session, settings: Settings, game_id: uuid.UUID) -> VerifiedChannel:
    from app.models import CfbResearchForecast, ExternalIdMapping

    admitted = ADMITTED["CFB"]
    source_ids = list(session.scalars(select(ExternalIdMapping.external_id).where(
        ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == "CFBD",
        ExternalIdMapping.entity_id == game_id)))
    if len(source_ids) != 1:
        raise ProbabilityUnavailable("FORECAST_UNAVAILABLE", "game has no single CFBD binding", status=404)
    # The current revision by its designated publication clock -- the same
    # choice every CFB surface and the paper checkpoint make.
    row = session.scalar(select(CfbResearchForecast)
                         .where(CfbResearchForecast.source_game_id == str(source_ids[0]))
                         .order_by(CfbResearchForecast.exchange_published_utc.desc().nullslast(),
                                   CfbResearchForecast.imported_at.desc()).limit(1))
    if row is None:
        raise ProbabilityUnavailable("FORECAST_UNAVAILABLE", "no CFB research forecast for this game", status=404)
    if row.baseline_configuration != admitted["configuration_id"] or not row.joint_draws_sha256:
        raise ProbabilityUnavailable("IDENTITY_MISMATCH", "CFB forecast is not the admitted paired baseline")
    package = row.package_index_sha256
    if row.origin == "PRODUCTION_DISCOVERY":
        # Discovery packages live in the producer's production tree, content-addressed.
        root = _inside(exchange_root(settings) / "cfb-gen1-production-v1",
                       exchange_root(settings) / "cfb-gen1-production-v1" / "forecasts" / "sha256" / package)
    else:
        root = _inside(exchange_root(settings) / "cfb-gen1-research-v1",
                       exchange_root(settings) / "cfb-gen1-research-v1" / "current-v1" / "packages" / "sha256" / package)

    def build():
        index = root / "index.json"
        try:
            index_raw = index.read_bytes()
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise ProbabilityUnavailable("POPULATION_SOURCE_UNREADABLE", "CFB package index is unreadable",
                                         status=503) from exc
        if hashlib.sha256(index_raw).hexdigest() != package:
            raise ProbabilityUnavailable("PACKAGE_HASH_MISMATCH", "CFB package index does not address its package")
        frame = _parquet(_read_verified(root / "baseline-joint-draws.parquet", sha256=row.joint_draws_sha256))
        frame = frame.sort("draw")
        away = [int(v) for v in frame["away_score"].to_list()]
        home = [int(v) for v in frame["home_score"].to_list()]
        return _cells_from_pairs(away, home), len(home)

    cells, count = _cached_cells(("CFB", row.forecast_id, package, row.joint_draws_sha256), build)
    population = _population("CFB", game_id=str(game_id), forecast_id=row.forecast_id,
                             package_sha256=package, cells=cells, draw_count=count)
    if count != int(row.draw_count):
        raise ProbabilityUnavailable("DRAW_COUNT_MISMATCH", "CFB population rows differ from its draw count")
    _reconcile(population, row.joint_home_win_probability, row.joint_away_win_probability, row.tie_probability)
    return VerifiedChannel(
        "CFB", population, admitted["authority"], admitted["authority_label"], admitted["tie_policy"],
        row.forecast_created_utc, row.source_cutoff_utc, "PROSPECTIVE_RESEARCH",
        {"home": row.joint_home_win_probability, "away": row.joint_away_win_probability, "tie": row.tie_probability},
        {"home": row.expected_home_points, "away": row.expected_away_points},
    )


_LOADERS = {"MLB": _mlb_channel, "NFL": _nfl_channel, "NBA": _nba_channel, "CFB": _cfb_channel}


# ------------------------------------------------------------ public API --

def game_league(session: Session, game_id: uuid.UUID) -> str | None:
    from app.models import Game, League

    return session.scalar(select(League.code).join(Game, Game.league_id == League.id).where(Game.id == game_id))


def parse_game_id(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ProbabilityUnavailable("IDENTITY_INVALID", "gameId must be an AISP canonical game id") from exc


def bind(session: Session, settings: Settings, game_id: uuid.UUID) -> VerifiedChannel:
    """The verified paired population for a game's selected forecast revision."""

    league = game_league(session, game_id)
    if league is None:
        raise ProbabilityUnavailable("GAME_NOT_FOUND", "no canonical game has this id", status=404)
    loader = _LOADERS.get(league)
    if loader is None:
        raise ProbabilityUnavailable("SPORT_UNSUPPORTED", f"{league} has no probability contract support")
    return loader(session, settings, game_id)


def describe(session: Session, settings: Settings, game_id: uuid.UUID) -> dict[str, Any]:
    """Every channel for a game, each supported or explicitly not."""

    from app.probability.identity import CROSS_GAME_CALIBRATION_NOT_VALIDATED, CROSS_GAME_MESSAGE

    league = game_league(session, game_id)
    channels: list[dict[str, Any]] = []
    try:
        channels.append(bind(session, settings, game_id).describe())
    except ProbabilityUnavailable as exc:
        if exc.code == "GAME_NOT_FOUND":
            raise
        channels.append({"channel": "PAIRED_POPULATION", "status": "UNSUPPORTED", "error": exc.as_dict(),
                         "capabilities": {k: False for k in SUPPORTED_ALL}})
    if league == "NBA":
        point = _nba_point_channel(session, game_id)
        if point is not None:
            channels.append(point)
    return {
        "schema_version": SCHEMA,
        "game_id": str(game_id),
        "sport": league,
        "channels": channels,
        "cross_game": {"status": "UNSUPPORTED", "code": CROSS_GAME_CALIBRATION_NOT_VALIDATED,
                       "message": CROSS_GAME_MESSAGE},
        "provider_calls_made": 0,
    }


def require_binding(channel: VerifiedChannel, claimed: Mapping[str, Any]) -> None:
    """The caller's binding must equal the current one exactly."""

    current = channel.binding
    for key, value in current.items():
        if claimed.get(key) != value:
            raise ProbabilityUnavailable(
                "BINDING_MISMATCH",
                f"populationBinding.{key} does not match the current verified forecast revision", status=409)
