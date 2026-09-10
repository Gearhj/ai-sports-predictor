"""AISP's side of populationSpatial: reconcile it, or refuse it.

The block summarises 1,000 games of which six ship play paths, so nothing
downstream can check it against the plays. The one check available is the one
the producer states about itself -- playCount is defined exactly as
populationAccounting.plays, and the cells sum to it -- and AISP re-derives that
rather than trusting the field. A summary that silently disagrees with the
aggregate printed beside it is worse than no summary, because both look
authoritative.

The mixed-version case is deliberate, not an accident to be tidied away: an
event past kickoff cannot be regenerated, so its V1 artifact is carried forward
and the index legitimately holds both versions.
"""
from __future__ import annotations

import pytest

from app.forecast_exchange.nfl_play_level_v1 import (
    SPATIAL, SUPPORTED_ARTIFACTS, _verify_population_spatial,
)
from app.forecast_exchange.supplements import SupplementImportError

V2 = "NFL_LABS_PLAY_LEVEL_SIMULATION_V2"
V1 = "NFL_LABS_PLAY_LEVEL_SIMULATION_V1"


def spatial(cells, *, play_count=None, **over):
    total = sum(cell["count"] for cell in cells)
    body = {
        "schemaVersion": SPATIAL,
        "basis": "ALL_SIMULATED_PLAYS_IN_POPULATION",
        "playCount": total if play_count is None else play_count,
        "cells": cells,
    }
    body.update(over)
    return body


def doc(spatial_block, *, plays=None, schema=V2):
    cells = (spatial_block or {}).get("cells") or []
    total = sum(cell["count"] for cell in cells)
    body = {"schemaVersion": schema, "populationAccounting": {"plays": total if plays is None else plays}}
    if spatial_block is not None:
        body["populationSpatial"] = spatial_block
    return body


CELLS = [
    {"playType": "RUSH", "start": 50, "yards": 0, "count": 40},
    {"playType": "PASS_COMPLETE", "start": 50, "yards": 10, "count": 30},
    {"playType": "PUNT", "start": "OVERFLOW", "yards": "NA", "count": 5},
]


def test_a_reconciling_block_is_accepted():
    _verify_population_spatial(doc(spatial(CELLS)), V2)


def test_both_artifact_versions_are_supported():
    assert SUPPORTED_ARTIFACTS == (V2, V1)


def test_a_v1_artifact_is_accepted_without_the_block():
    """A carried-forward predecessor is valid, not defective.

    Rejecting it would drop a game AISP already serves, for lacking a field its
    own contract never required.
    """
    _verify_population_spatial({"schemaVersion": V1, "populationAccounting": {"plays": 75}}, V1)


def test_a_v1_artifact_carrying_the_block_is_refused():
    # Its own contract has no such field, so its presence means the version
    # binding is wrong somewhere upstream.
    with pytest.raises(SupplementImportError, match="must not carry populationSpatial"):
        _verify_population_spatial(doc(spatial(CELLS), schema=V1), V1)


def test_a_v2_artifact_without_the_block_is_refused():
    with pytest.raises(SupplementImportError, match="missing populationSpatial"):
        _verify_population_spatial(doc(None), V2)


def test_a_play_count_that_disagrees_with_the_accounting_is_refused():
    """The exact failure this whole check exists for.

    75 plays binned, 152,149 claimed by the aggregate beside it. Rendering both
    would show a density built from a seventy-five-play sample under a heading
    that says a hundred and fifty thousand.
    """
    with pytest.raises(SupplementImportError, match="does not equal populationAccounting.plays"):
        _verify_population_spatial(doc(spatial(CELLS), plays=152149), V2)


def test_cells_that_do_not_sum_to_the_stated_count_are_refused():
    with pytest.raises(SupplementImportError, match="cells sum to"):
        _verify_population_spatial(doc(spatial(CELLS, play_count=999), plays=999), V2)


def test_a_duplicated_cell_is_refused():
    # The same key twice means a play could be counted twice while the totals
    # still add up.
    duplicated = CELLS + [dict(CELLS[0])]
    with pytest.raises(SupplementImportError, match="duplicate or unlabelled cell"):
        _verify_population_spatial(doc(spatial(duplicated)), V2)


def test_an_unknown_play_type_is_refused():
    strange = CELLS + [{"playType": "TELEPORT", "start": 50, "yards": 0, "count": 1}]
    with pytest.raises(SupplementImportError, match="unknown play type"):
        _verify_population_spatial(doc(spatial(strange)), V2)


def test_a_block_that_does_not_claim_the_whole_population_is_refused():
    partial = spatial(CELLS, basis="SAMPLE_OF_SIMULATED_PLAYS")
    with pytest.raises(SupplementImportError, match="does not claim the whole population"):
        _verify_population_spatial(doc(partial), V2)


def test_the_explicit_out_of_range_bins_survive_verification():
    """UNDERFLOW / OVERFLOW / NA are legitimate bins, not malformed cells.

    They exist so that nothing is clipped into an edge bin; a verifier that
    rejected them would push the producer back towards silent clipping.
    """
    edges = [
        {"playType": "RUSH", "start": "UNDERFLOW", "yards": "UNDERFLOW", "count": 2},
        {"playType": "RUSH", "start": "OVERFLOW", "yards": "OVERFLOW", "count": 3},
        {"playType": "KICKOFF", "start": 35, "yards": "NA", "count": 9},
    ]
    _verify_population_spatial(doc(spatial(edges)), V2)
