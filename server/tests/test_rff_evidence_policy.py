"""RFF Core: fixed design weights, partial identification, fail-closed publication.

Two failures are pinned here.

The original one: renormalising by the available weight let a single small
component become the whole composite, so sparse collection read as market
unrest. The second, which this file is the gate for: **missing data must never
be able to create an extreme score or an extreme band.** Extremity has to be
earned by evidence that was actually observed.

Core weights are fixed by design and are never renormalised for an individual
game. They were chosen once, over a fixed and fully collectable set of five
components, and every figure below is computed against that fixed denominator.
"""
from __future__ import annotations

import pytest

from app.services.rff_index import (
    BANDS, CATEGORY_UNCERTAIN, CATEGORY_UNKNOWN, CORE_COMPONENTS, CROSS_BOOK_DISAGREEMENT,
    Component, EXTENDED_ONLY, INFORMATION_UNCERTAINTY, LATE_MOVEMENT, MIN_COMPONENTS_PUBLISHED,
    MIN_COVERAGE_PUBLISHED, MIN_FAMILIES_PUBLISHED, MODEL_MARKET_DISAGREEMENT,
    REVERSE_LINE_MOVEMENT, RffService, STALE_EVIDENCE, STATUS_INSUFFICIENT, STATUS_PROVISIONAL,
    STATUS_PUBLISHED, WEIGHTS,
)

TOP_BAND = BANDS[-1][1]


def components(scores: dict[str, float]) -> list[Component]:
    """Every Core component, with the named ones available at the given score."""

    return [Component(key=key, available=key in scores, score=scores.get(key))
            for key in CORE_COMPONENTS]


def score(scores: dict[str, float]) -> dict:
    return RffService._score(components(scores))


# -- the shape of the index -------------------------------------------------
def test_core_is_five_fixed_design_weights_that_sum_to_one():
    assert set(CORE_COMPONENTS) == {
        REVERSE_LINE_MOVEMENT, LATE_MOVEMENT, CROSS_BOOK_DISAGREEMENT,
        MODEL_MARKET_DISAGREEMENT, INFORMATION_UNCERTAINTY,
    }
    assert WEIGHTS == {
        REVERSE_LINE_MOVEMENT: 0.28125,
        LATE_MOVEMENT: 0.21875,
        CROSS_BOOK_DISAGREEMENT: 0.21875,
        MODEL_MARKET_DISAGREEMENT: 0.15625,
        INFORMATION_UNCERTAINTY: 0.125,
    }
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_weights_are_never_renormalised_for_an_individual_game():
    """The same component at the same score contributes the same points, whatever
    else that game happens to have."""

    alone = score({REVERSE_LINE_MOVEMENT: 40.0})
    crowded = score({REVERSE_LINE_MOVEMENT: 40.0, LATE_MOVEMENT: 0.0,
                     CROSS_BOOK_DISAGREEMENT: 0.0, MODEL_MARKET_DISAGREEMENT: 0.0,
                     INFORMATION_UNCERTAINTY: 0.0})
    contribution = WEIGHTS[REVERSE_LINE_MOVEMENT] * 40.0
    assert alone["known_contribution"] == pytest.approx(contribution, abs=0.1)
    assert crowded["known_contribution"] == pytest.approx(contribution, abs=0.1)
    # Only the interval differs: the sparse game does not know the rest.
    assert alone["interval_width"] > crowded["interval_width"] == 0.0


def test_extended_is_a_concept_and_is_never_calculated():
    extended = RffService._extended_concept()
    assert extended["calculated"] is False
    assert extended["score"] is None
    assert {entry["key"] for entry in extended["missing_components"]} == {
        entry["key"] for entry in EXTENDED_ONLY}
    for entry in extended["missing_components"]:
        assert entry["weight"] is None
        assert entry["key"] not in WEIGHTS, "an Extended component never carries Core weight"


# -- partial identification -------------------------------------------------
def test_one_component_at_100_cannot_become_the_composite():
    """The original defect, in Core terms: information uncertainty is 12.5%."""

    result = score({INFORMATION_UNCERTAINTY: 100.0})
    assert result["evidence_coverage"] == pytest.approx(0.125)
    assert result["known_contribution"] == pytest.approx(12.5), "0.125 x 100, fixed denominator"
    assert result["score_range"] == [12.5, 100.0]
    assert result["score"] is None, "no number below the publication floor"
    assert result["status"] == STATUS_INSUFFICIENT
    assert result["band"]["key"] == CATEGORY_UNKNOWN
    assert result["band"]["label"] == "Unknown"
    assert result["rankable"] is False and result["emphasize_point"] is False
    # The old renormalised figure survives only as a named diagnostic.
    assert result["diagnostics"]["observedComponentAverage"] == pytest.approx(100.0)
    assert "not RFF" in result["diagnostics"]["note"]


def test_unavailable_is_never_zero_and_never_imputed():
    high = score({INFORMATION_UNCERTAINTY: 100.0})
    low = score({INFORMATION_UNCERTAINTY: 0.0})
    assert high["lower_bound"] == 12.5 and low["lower_bound"] == 0.0
    # A low observed component cannot reach 100 either: its 12.5 points are spent.
    assert high["upper_bound"] == 100.0 and low["upper_bound"] == 87.5
    assert high["unknown_weight"] == pytest.approx(0.875)
    for entry in components({}):
        assert entry.as_dict()["score"] is None and entry.as_dict()["display"] == "Unavailable"


def test_the_interval_is_exactly_the_unobserved_weight():
    result = score({REVERSE_LINE_MOVEMENT: 50.0, CROSS_BOOK_DISAGREEMENT: 50.0})
    coverage = WEIGHTS[REVERSE_LINE_MOVEMENT] + WEIGHTS[CROSS_BOOK_DISAGREEMENT]
    assert result["evidence_coverage"] == pytest.approx(coverage)
    assert result["known_contribution"] == pytest.approx(coverage * 50.0, abs=0.1)
    assert result["upper_bound"] - result["lower_bound"] == pytest.approx((1 - coverage) * 100, abs=0.1)


# -- THE GATE: missing data cannot manufacture extremity --------------------
@pytest.mark.parametrize("observed", [
    {INFORMATION_UNCERTAINTY: 100.0},
    {MODEL_MARKET_DISAGREEMENT: 100.0},
    {REVERSE_LINE_MOVEMENT: 100.0},
    {REVERSE_LINE_MOVEMENT: 100.0, LATE_MOVEMENT: 100.0},
    {REVERSE_LINE_MOVEMENT: 100.0, LATE_MOVEMENT: 100.0, CROSS_BOOK_DISAGREEMENT: 100.0},
])
def test_missing_data_can_never_produce_a_score_or_an_extreme_band(observed):
    """Every partial-evidence shape, each component pinned at its maximum.

    However extreme the observed slice is, an incomplete Core publishes no
    number and names no band -- least of all the top one.
    """

    result = score(observed)
    assert result["score"] is None
    assert result["band"]["key"] in {CATEGORY_UNKNOWN, CATEGORY_UNCERTAIN}
    assert result["band"]["key"] != TOP_BAND
    assert result["rankable"] is False and result["emphasize_point"] is False
    # The upper bound may be high -- that is the honest statement that we do not
    # know -- but it is a bound, never a score.
    assert result["upper_bound"] >= result["known_contribution"]


def test_the_top_band_requires_observed_evidence_not_absent_evidence():
    everything = score({key: 100.0 for key in CORE_COMPONENTS})
    assert everything["evidence_coverage"] == pytest.approx(1.0)
    assert everything["score"] == pytest.approx(100.0)
    assert everything["band"]["key"] == TOP_BAND, "full evidence, unanimously extreme"
    assert everything["interval_width"] == 0.0 and everything["emphasize_point"] is True

    # Four of five at the maximum also reaches the top band -- but every point of
    # it was observed, and the unknown weight can only push it further up.
    four = score({key: 100.0 for key in CORE_COMPONENTS if key != INFORMATION_UNCERTAINTY})
    assert four["status"] == STATUS_PUBLISHED
    assert four["known_contribution"] == pytest.approx(87.5)
    assert four["lower_bound"] >= BANDS[-1][0], "the whole interval is inside the top band"
    assert four["band"]["key"] == TOP_BAND

    # One extreme component among four calm ones does not come close.
    lopsided = score({REVERSE_LINE_MOVEMENT: 100.0, LATE_MOVEMENT: 0.0,
                      CROSS_BOOK_DISAGREEMENT: 0.0, MODEL_MARKET_DISAGREEMENT: 0.0})
    assert lopsided["score"] == pytest.approx(28.1, abs=0.1)
    assert lopsided["band"]["key"] != TOP_BAND


# -- publication policy -----------------------------------------------------
def test_half_covered_games_are_provisional_and_unranked():
    result = score({LATE_MOVEMENT: 40.0, CROSS_BOOK_DISAGREEMENT: 40.0,
                    MODEL_MARKET_DISAGREEMENT: 80.0})
    assert result["evidence_coverage"] == pytest.approx(0.59375)
    assert result["status"] == STATUS_PROVISIONAL
    assert result["score"] is None and result["rankable"] is False
    assert result["band"]["key"] == CATEGORY_UNKNOWN
    codes = {entry["code"] for entry in result["publication_blockers"]}
    assert "COVERAGE_BELOW_PUBLICATION_THRESHOLD" in codes


def test_a_published_score_is_the_known_contribution_not_a_renormalised_mean():
    scores = {REVERSE_LINE_MOVEMENT: 5.0, LATE_MOVEMENT: 5.0, CROSS_BOOK_DISAGREEMENT: 5.0,
              MODEL_MARKET_DISAGREEMENT: 5.0}
    result = score(scores)
    assert result["evidence_coverage"] >= MIN_COVERAGE_PUBLISHED
    assert result["status"] == STATUS_PUBLISHED and result["rankable"] is True
    assert result["score"] == pytest.approx(result["known_contribution"])
    assert result["score"] < result["diagnostics"]["observedComponentAverage"], (
        "the composite counts the unobserved weight as unknown, not as agreement")
    assert result["band"]["key"] == "CALM"
    assert len(result["families_present"]) >= MIN_FAMILIES_PUBLISHED
    assert result["available_components"] >= MIN_COMPONENTS_PUBLISHED


def test_publication_needs_four_components_however_high_the_coverage():
    """Three price-and-structure components clear 70% coverage and still cannot
    publish: too few components, and too little of the measure seen."""

    thin = score({REVERSE_LINE_MOVEMENT: 90.0, LATE_MOVEMENT: 90.0,
                  CROSS_BOOK_DISAGREEMENT: 90.0})
    assert thin["evidence_coverage"] == pytest.approx(0.71875)
    assert thin["evidence_coverage"] >= MIN_COVERAGE_PUBLISHED
    assert thin["status"] == STATUS_PROVISIONAL
    assert "TOO_FEW_COMPONENTS" in {entry["code"] for entry in thin["publication_blockers"]}
    assert thin["score"] is None


def test_publication_needs_more_than_one_evidence_family():
    """Both price-movement components, and nothing else: one family."""

    same_family = RffService._score([
        Component(key=REVERSE_LINE_MOVEMENT, available=True, score=90.0),
        Component(key=LATE_MOVEMENT, available=True, score=90.0),
        *[Component(key=key, available=False) for key in CORE_COMPONENTS
          if key not in {REVERSE_LINE_MOVEMENT, LATE_MOVEMENT}],
    ])
    assert same_family["families_present"] == ["PRICE_MOVEMENT"]
    assert same_family["status"] == STATUS_PROVISIONAL
    assert "TOO_FEW_EVIDENCE_FAMILIES" in {
        entry["code"] for entry in same_family["publication_blockers"]}


def test_a_category_is_refused_when_the_interval_straddles_bands():
    wide = score({REVERSE_LINE_MOVEMENT: 60.0, LATE_MOVEMENT: 60.0,
                  CROSS_BOOK_DISAGREEMENT: 60.0, MODEL_MARKET_DISAGREEMENT: 60.0})
    assert wide["status"] == STATUS_PUBLISHED
    assert wide["band"]["key"] == CATEGORY_UNCERTAIN
    assert "spans Curious to Spicy" in wide["category_reason"]
    assert wide["emphasize_point"] is False, "the range is the finding, not the point"


def test_insufficient_evidence_is_called_unknown_and_never_calm():
    result = score({})
    assert result["evidence_coverage"] == 0.0
    assert result["band"]["label"] == "Unknown"
    assert "not calm" in result["band"]["description"].lower()
    assert "calm" not in result["band"]["label"].lower()


def test_stale_evidence_does_not_buy_coverage():
    stale = RffService._score([
        Component(key=CROSS_BOOK_DISAGREEMENT, available=False, reason=STALE_EVIDENCE, stale=True),
        *[Component(key=key, available=False) for key in CORE_COMPONENTS
          if key != CROSS_BOOK_DISAGREEMENT],
    ])
    assert stale["evidence_coverage"] == 0.0
    assert stale["status"] == STATUS_INSUFFICIENT
    assert stale["contributions"] == [], "a stale component contributes nothing"


def test_contribution_shares_are_of_the_whole_measure():
    result = score({INFORMATION_UNCERTAINTY: 100.0, CROSS_BOOK_DISAGREEMENT: 50.0})
    shares = {entry["key"]: entry["share_of_measure"] for entry in result["contributions"]}
    assert shares[INFORMATION_UNCERTAINTY] == pytest.approx(0.125)
    assert shares[CROSS_BOOK_DISAGREEMENT] == pytest.approx(0.109375, abs=1e-4)
    assert sum(shares.values()) < 1.0, "shares can never total the whole measure on partial evidence"
