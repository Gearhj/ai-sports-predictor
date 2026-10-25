"""The frozen, accepted NHL Gen1 model, vendored byte for byte, and its team crosswalk.

FILES (both ``-text`` in .gitattributes, so no checkout can change a byte)
-------------------------------------------------------------------------
``selected_model.json``     Model Studio ``nhl-gen1-overnight-20261001-v1/publication/selected_model.json``,
                            byte-identical; its SHA-256 IS the forecast ``configurationSha256``.
``team_aliases_latest.csv`` The 33 rows of ``data/games_2015_2024_v3_team_aliases.csv`` (sha256 8e2a187a...)
                            that survive the producer's ``sort_values('season').drop_duplicates('abbr',
                            keep='last')``, copied verbatim (header + one latest row per tri-code).

THE CROSSWALK
-------------
Exactly ``produce_current.prepare``: tri-code -> the latest alias row, then the
reviewed Utah correction (``upcoming/UTAH_IDENTITY_CORRECTION.json`` sha256
ac4db26c..., ``upcoming/GAMES_V2.json`` sha256 6796cec0...): ``UTA`` is the
Utah Mammoth, NHL team 68, never Utah Hockey Club 59. Nothing is inferred from a
display name. Every load re-verifies both files' SHA-256 and fails closed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.nhl.gen1_frozen.model import SOURCE_MODEL_CODE_SHA256, GoalModel, model_from_state

ROOT = Path(__file__).resolve().parent
SELECTED_MODEL_PATH = ROOT / "selected_model.json"
ALIASES_PATH = ROOT / "team_aliases_latest.csv"

#: SHA-256 of the frozen fitted state; the forecast's ``configurationSha256``.
SELECTED_MODEL_SHA256 = "3f7981aac2bfee6f3d05b7e1d099a3342cc9057d09e13b09aaf4c81b7f720417"
#: SHA-256 of the vendored 33-row alias subset.
ALIASES_SUBSET_SHA256 = "a3db85b18e7587d7e3da0af9c603eb31c0bfc779b48ebfd2f4f26fb7715a5e41"
#: SHA-256 of the full producer alias file the subset was cut from (provenance only).
ALIASES_SOURCE_SHA256 = "8e2a187a9ee5df31c3b813c9f4f3d4cb07424d821509be0aace3c2bc023e361f"
UTAH_CORRECTION_SHA256 = "ac4db26cc8210b667bc8b974dbfc1d5fc2a256f86a1eb99ef6051fdbcf7af783"
UTAH_GAMES_V2_SHA256 = "6796cec019ad610f9a71bf5d15761e5f6efa4d6803a53c582a899984afc0c997"
#: The reviewed correction, as the producer applies it after the alias table.
UTAH_CORRECTION = {"UTA": {"sourceTeamId": "68", "name": "Utah Mammoth"}}
#: The producer's preparer whose feature build ``app.nhl.inprocess_generator`` reproduces (provenance only).
SOURCE_PREPARER_SHA256 = "076a2dab2f39eecd0bc82efe0224d39a049e6f9f75e76de50dfebbb9f4ea97d0"

MODEL_ID = "NHL_GEN1_REGULATION_POISSON"
MODEL_VERSION = "1.0.0"


class FrozenArtifactChanged(RuntimeError):
    """A vendored frozen file no longer has its pinned SHA-256."""


def _pinned(path: Path, digest: str) -> bytes:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise FrozenArtifactChanged(f"PIN_CHANGED:{path.name}")
    return raw


@lru_cache(maxsize=1)
def _state_bytes() -> bytes:
    return _pinned(SELECTED_MODEL_PATH, SELECTED_MODEL_SHA256)


def selected_state() -> dict[str, Any]:
    """The frozen fitted state (a fresh copy each call)."""

    return json.loads(_state_bytes())


def goal_model() -> GoalModel:
    return model_from_state(selected_state())


def handoff_sha256() -> str:
    """``model.handoffSha256`` in the producer's documents: the selected-model's own selection receipt."""

    return str(selected_state()["selectedRef"]["sha256"])


@lru_cache(maxsize=1)
def _crosswalk() -> dict[str, dict[str, str]]:
    raw = _pinned(ALIASES_PATH, ALIASES_SUBSET_SHA256).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(raw, newline="")))
    rows.sort(key=lambda r: r["season"])           # (season, abbr) is unique: the order is deterministic
    latest: dict[str, dict[str, str]] = {}
    for r in rows:                                  # keep='last' per tri-code
        latest[r["abbr"]] = r
    teams = {abbr: {"sourceTeamId": r["team_id"], "canonicalTeamId": "nhl:team:" + r["team_id"],
                    "triCode": abbr, "name": r["name"]} for abbr, r in latest.items()}
    for abbr, fix in UTAH_CORRECTION.items():
        teams[abbr] = {"sourceTeamId": fix["sourceTeamId"], "canonicalTeamId": "nhl:team:" + fix["sourceTeamId"],
                       "triCode": abbr, "name": fix["name"]}
    return teams


def team_crosswalk() -> dict[str, dict[str, str]]:
    """Tri-code -> the contract team block, exactly as the producer's preparer builds it."""

    return {abbr: dict(team) for abbr, team in _crosswalk().items()}


def artifact_hashes() -> dict[str, str]:
    """Every pinned identity the generator records as provenance."""

    return {"selectedModelSha256": SELECTED_MODEL_SHA256, "teamAliasSubsetSha256": ALIASES_SUBSET_SHA256,
            "teamAliasSourceSha256": ALIASES_SOURCE_SHA256, "utahCorrectionSha256": UTAH_CORRECTION_SHA256,
            "utahGamesV2Sha256": UTAH_GAMES_V2_SHA256, "producerModelCodeSha256": SOURCE_MODEL_CODE_SHA256,
            "producerPreparerSha256": SOURCE_PREPARER_SHA256}


__all__ = ["ALIASES_SOURCE_SHA256", "ALIASES_SUBSET_SHA256", "FrozenArtifactChanged", "MODEL_ID", "MODEL_VERSION",
           "SELECTED_MODEL_SHA256", "SOURCE_PREPARER_SHA256", "UTAH_CORRECTION", "artifact_hashes", "goal_model",
           "handoff_sha256", "selected_state", "team_crosswalk"]
