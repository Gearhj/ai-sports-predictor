"""Shared NBA Gen1 consumer primitives: byte binding, clocks, channels, population summaries.

THE THREE CHANNELS, AS THE PRODUCER DEFINES THEM
------------------------------------------------
Model Studio's production contract (``NBA_GEN1_ELO_WIN_RIDGE_A100_ORDINARY_REST_V1``)
estimates three separate things and says so in every package:

``RIDGE_POINTS``        Ridge alpha100 ordinary-rest EXPECTED POINTS (home, away,
                        total, margin). A point expectation, never a probability.
                        It answers no market on its own.
``ELO_WINNER``          Chronological Elo (1500 / K20 / home 65 / scale 400 / 2/3
                        season regression) WINNER probabilities, fractions 0-1.
                        The producer's declared controlling winner channel.
``JOINT_DISTRIBUTION``  10,000 paired HOME/AWAY full-game draws including overtime
                        (``NBA_GEN1_JOINT_FINAL_PAIRED_OOF_LATTICE_V1``). Winner,
                        spread (margin) and total probabilities, PMFs and intervals.

A correction to the earlier staged adapter (eea993a): it read the handoff
manifest's ``winnerModel.coefficients`` -- an OLS association of home-minus-away
MARGIN on the Elo difference, used for the Elo benchmark's point expectation --
as the Elo channel, and concluded Elo was not a probability. The forecast bytes
say otherwise: ``eloWinnerProbabilities`` is the classic Elo logistic winner
probability, and the 2025-26 assessment scores it as one (log loss 0.606258,
binary Brier 0.208971 on 1,225 games). So Elo IS a winner-probability channel;
what it is not is a spread or total channel.

NEVER COMPARE RIDGE TO A MONEYLINE
----------------------------------
:data:`MARKET_CHANNELS` is a closed allow-list. RIDGE_POINTS answers nothing. A
market not listed has no authorised channel and returns None.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
from datetime import UTC, datetime
from typing import Any

import numpy as np

from app.models.nba_gen1 import CHANNEL_ELO, CHANNEL_JOINT, CHANNEL_RIDGE

CONFIGURATION = "NBA_GEN1_ELO_WIN_RIDGE_A100_ORDINARY_REST_V1"
DISTRIBUTION_CONFIGURATION = "NBA_GEN1_JOINT_FINAL_PAIRED_OOF_LATTICE_V1"
DRAW_COUNT = 10_000
FORECAST_SCHEMA = "NBA_GEN1_PRODUCTION_FORECAST_V1"
INDEX_SCHEMA_V1 = "NBA_GEN1_PRODUCTION_FORECAST_PACKAGE_INDEX_V1"
INDEX_SCHEMA_V2 = "NBA_GEN1_PRODUCTION_FORECAST_PACKAGE_INDEX_V2"
HANDOFF_INDEX_SCHEMA = "NBA_GEN1_PRODUCTION_HANDOFF_INDEX_V2"
HANDOFF_MANIFEST_SCHEMA = "NBA_GEN1_PRODUCTION_BASELINE_HANDOFF_V2"
DISCOVERY_V1_SCHEMA = "NBA_GEN1_PRODUCTION_DISCOVERY_V1"
SLATE_POINTER_SCHEMA = "NBA_GEN1_SLATE_DISCOVERY_POINTER_V1"
SLATE_DISCOVERY_SCHEMA = "NBA_GEN1_SLATE_DISCOVERY_V1"

LEAGUE_CODE = "NBA"
SOURCE_ESPN_GAME = "NBA_ESPN"               # external_id_mappings.source for games (existing)
SOURCE_FRANCHISE = "NBA_NEIL_FRANCHISE"     # external_id_mappings.source for teams (existing)
SOURCE_ESPN_TEAM = "NBA_ESPN_TEAM"          # ESPN numeric team id, from retained source rows
#: Games admitted from Codex evidence are research-shadow provenance, like CFB/NFL.
SOURCE_MODE = "RESEARCH_SHADOW"

CHANNEL_ROLES: dict[str, dict[str, Any]] = {
    CHANNEL_RIDGE: {
        "label": "Ridge expected points",
        "model": "Ridge alpha100 ordinary-rest",
        "produces": "Expected home and away points (overtime included)",
        "units": "points",
        "is_probability": False,
        "markets": [],
        "display_note": ("An expected score, not a probability. It is never compared with a "
                         "moneyline, spread or total price."),
    },
    CHANNEL_ELO: {
        "label": "Elo win probability",
        "model": "Chronological Elo (1500 / K20 / home 65 / scale 400 / 2/3 regression)",
        "produces": "Home and away winner probabilities",
        "units": "fractions 0 to 1",
        "is_probability": True,
        "markets": ["MONEYLINE"],
        "display_note": ("The producer's declared controlling winner channel. Separately "
                         "estimated from the points and the joint draws; the three are not "
                         "forced to agree."),
    },
    CHANNEL_JOINT: {
        "label": "Joint-distribution probabilities",
        "model": "Paired OOF residual lattice, 10,000 draws",
        "produces": "Winner, spread (margin) and total probabilities, PMFs and intervals",
        "units": "fractions 0 to 1 (probabilities); points (scores)",
        "is_probability": True,
        "markets": ["MONEYLINE", "SPREAD", "TOTAL"],
        "display_note": ("The only channel with a distribution, so the only one that can price "
                         "a spread or a total. Its winner probability is a separate summary, "
                         "not the Elo number."),
    },
}

#: Market -> channels allowed to answer it. Closed: absent means none.
MARKET_CHANNELS: dict[str, tuple[str, ...]] = {
    "MONEYLINE": (CHANNEL_ELO, CHANNEL_JOINT),
    "SPREAD": (CHANNEL_JOINT,),
    "TOTAL": (CHANNEL_JOINT,),
}

#: Carried with every display. The one-point gap matters most where a spread sits
#: on an integer, so it travels with the joint channel rather than in a footnote.
RETAINED_LIMITATIONS: tuple[str, ...] = (
    "Simulated one-point finishes were 7.78% against 4.01% observed, so margins at or near "
    "a single point are the least trustworthy part of this model.",
    "A close-score correction was tested and rejected; the challenger stays disabled.",
    "Overtime games remain materially harder to forecast.",
    "No player, lineup, injury, projected-minutes, referee or travel inputs.",
    "No profitability claim has been established.",
    "Ridge points, Elo winner and joint-distribution channels are separately estimated.",
)


def channels_for(market: str) -> tuple[str, ...]:
    """The channels allowed to answer ``market``; empty for anything unlisted."""

    return MARKET_CHANNELS.get(str(market).upper(), ())


class BindingRefused(RuntimeError):
    """A byte binding (path, sha256, bytes) failed. The message names which."""


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def bound(ref: dict[str, Any], label: str) -> bytes:
    """Read ``ref['path']`` and prove it against ``ref['sha256']`` and ``ref['bytes']``."""

    try:
        path = pathlib.Path(str(ref["path"]))
        expected = str(ref["sha256"]).lower()
        size = int(ref["bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BindingRefused(f"{label}: binding is not {{path, sha256, bytes}}") from exc
    if not path.is_file():
        raise BindingRefused(f"{label} is missing: {path}")
    raw = path.read_bytes()
    if len(raw) != size or sha256_bytes(raw) != expected:
        raise BindingRefused(f"{label} does not match its declared SHA-256 and byte count")
    return raw


def ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def iso(value: datetime | None) -> str | None:
    value = aware(value)
    return value.isoformat() if value is not None else None


def canonical_bytes(value: Any) -> bytes:
    """Model Studio's canonical encoding (sorted keys, compact, trailing newline)."""

    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def pmf(values: np.ndarray) -> dict[str, Any]:
    unique, counts = np.unique(values.astype(np.int64), return_counts=True)
    return {"values": [int(v) for v in unique], "counts": [int(c) for c in counts],
            "draw_count": int(counts.sum())}


def weighted_pmf(values: np.ndarray, weights: np.ndarray) -> dict[str, Any]:
    order: dict[int, int] = {}
    for value, weight in zip(values.astype(np.int64).tolist(), weights.astype(np.int64).tolist()):
        order[value] = order.get(value, 0) + int(weight)
    keys = sorted(order)
    return {"values": keys, "counts": [order[k] for k in keys], "draw_count": int(sum(order.values()))}


def interval_from_pmf(mass: dict[str, Any], level: int) -> dict[str, Any]:
    """Central equal-tail inverted-CDF interval, computed exactly as the producer does.

    ``level`` is an integer percentage (50/80/90). The expansion back to 10,000
    sorted values and the float arithmetic of ``alpha`` are deliberate: the
    producer's own bounds come from ``np.quantile(..., method="inverted_cdf")``
    with ``alpha = (1 - level / 100) / 2``, and matching them to the point means
    reproducing that arithmetic, not an idealised one.
    """

    values = np.repeat(np.asarray(mass["values"], dtype=np.int64), np.asarray(mass["counts"], dtype=np.int64))
    alpha = (1 - level / 100) / 2
    lo, hi = np.quantile(values, [alpha, 1 - alpha], method="inverted_cdf")
    return {"level": level / 100, "lower": int(lo), "upper": int(hi),
            "method": "central equal-tail inverted_cdf", "units": "points including overtime"}


def totals_query(total_values: list[int], total_counts: list[int], line: float) -> dict[str, Any]:
    """Over/under win/push/loss at an ILLUSTRATIVE line. Never a recorded market."""

    n = sum(total_counts)
    over = sum(c for v, c in zip(total_values, total_counts) if v > line)
    push = sum(c for v, c in zip(total_values, total_counts) if v == line)
    under = sum(c for v, c in zip(total_values, total_counts) if v < line)
    return {"line": float(line), "lineClassification": "ILLUSTRATIVE_NOT_RECORDED_MARKET", "drawCount": n,
            "probabilityUnits": "FRACTIONS_0_TO_1",
            "over": {"win": over / n, "push": push / n, "loss": under / n},
            "under": {"win": under / n, "push": push / n, "loss": over / n}}


def summarize_draws(home: np.ndarray, away: np.ndarray) -> dict[str, Any]:
    """Everything AISP derives from a complete paired population, in one place."""

    if len(home) != DRAW_COUNT or len(away) != DRAW_COUNT:
        raise ValueError(f"a joint population must hold exactly {DRAW_COUNT} draws")
    if (home < 0).any() or (away < 0).any() or (home == away).any():
        raise ValueError("draws must be nonnegative and untied (NBA finals cannot tie)")
    total, margin = home + away, home - away
    p_home = float((margin > 0).mean())
    return {
        "draw_count": DRAW_COUNT,
        "winner": {"home": p_home, "away": 1 - p_home, "tie": 0.0,
                   "home_mcse": float(math.sqrt(p_home * (1 - p_home) / DRAW_COUNT))},
        "expected": {"home": float(home.mean()), "away": float(away.mean()),
                     "total": float(total.mean()), "margin": float(margin.mean())},
        "pmfs": {"home": pmf(home), "away": pmf(away), "total": pmf(total), "margin": pmf(margin)},
    }


def summarize_counts(home: np.ndarray, away: np.ndarray, counts: np.ndarray) -> dict[str, Any]:
    """The same summary from lossless joint-cell counts (historical populations)."""

    n = int(counts.sum())
    if n != DRAW_COUNT:
        raise ValueError(f"joint counts hold {n} draws, not {DRAW_COUNT}")
    if (home == away).any():
        raise ValueError("a joint cell is tied; NBA finals cannot tie")
    total, margin = home + away, home - away
    p_home = float(counts[margin > 0].sum() / n)
    return {
        "draw_count": n,
        "winner": {"home": p_home, "away": 1 - p_home, "tie": 0.0,
                   "home_mcse": float(math.sqrt(p_home * (1 - p_home) / n))},
        "expected": {"home": float((home * counts).sum() / n), "away": float((away * counts).sum() / n),
                     "total": float((total * counts).sum() / n), "margin": float((margin * counts).sum() / n)},
        "pmfs": {"home": weighted_pmf(home, counts), "away": weighted_pmf(away, counts),
                 "total": weighted_pmf(total, counts), "margin": weighted_pmf(margin, counts)},
    }


def with_intervals(summary: dict[str, Any]) -> dict[str, Any]:
    summary["intervals"] = {name: [interval_from_pmf(summary["pmfs"][name], level) for level in (50, 80, 90)]
                            for name in ("home", "away", "total", "margin")}
    return summary


def pmf_probability(mass: dict[str, Any], predicate) -> float:
    total = mass["draw_count"]
    return sum(c for v, c in zip(mass["values"], mass["counts"]) if predicate(v)) / total


def spread_probabilities(joint_summary: dict[str, Any], home_line: float) -> dict[str, Any]:
    """Home covers ``home_line`` (e.g. -5.5) from the joint MARGIN pmf. Joint channel only."""

    margin = joint_summary["pmfs"]["margin"]
    covers = pmf_probability(margin, lambda m: m + home_line > 0)
    push = pmf_probability(margin, lambda m: m + home_line == 0)
    return {"home_line": home_line, "channel": CHANNEL_JOINT, "home_cover": covers, "push": push,
            "away_cover": 1 - covers - push, "classification": "ILLUSTRATIVE_NOT_RECORDED_MARKET"}


__all__ = [
    "BindingRefused", "CHANNEL_ROLES", "CONFIGURATION", "DISCOVERY_V1_SCHEMA", "DISTRIBUTION_CONFIGURATION",
    "DRAW_COUNT", "FORECAST_SCHEMA", "HANDOFF_INDEX_SCHEMA", "HANDOFF_MANIFEST_SCHEMA", "INDEX_SCHEMA_V1",
    "INDEX_SCHEMA_V2", "LEAGUE_CODE", "MARKET_CHANNELS", "RETAINED_LIMITATIONS", "SLATE_DISCOVERY_SCHEMA",
    "SLATE_POINTER_SCHEMA", "SOURCE_ESPN_GAME", "SOURCE_ESPN_TEAM", "SOURCE_FRANCHISE", "SOURCE_MODE",
    "aware", "bound", "canonical_bytes", "channels_for", "interval_from_pmf", "iso", "pmf", "pmf_probability",
    "sha256_bytes", "spread_probabilities", "summarize_counts", "summarize_draws", "totals_query", "ts",
    "weighted_pmf", "with_intervals",
]
