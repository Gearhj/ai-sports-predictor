"""Fixed-weight evidence accounting and the fail-closed publication policy.

The failure being pinned: with available-component renormalisation, one 8%-weight
component sitting at 100 became "RFF 100 · Maximum unrest", so sparse collection
read as market unrest. Missing evidence must widen the interval, never lift the
number, and never earn a category.
"""
from __future__ import annotations

import pytest

from app.services.rff_index import (
    CATEGORY_UNCERTAIN, CROSS_BOOK_DISAGREEMENT, Component, INFORMATION_UNCERTAINTY,
    LATE_MOVEMENT, MIN_COMPONENTS_PUBLISHED, MIN_COVERAGE_PUBLISHED, MIN_FAMILIES_PUBLISHED,
    MODEL_MARKET_DISAGREEMENT, PUBLIC_CONCENTRATION, REVERSE_LINE_MOVEMENT, RffService,
    SOCIAL_NARRATIVE, SPORTSBOOK_RELIEF, STALE_EVIDENCE, STATUS_INSUFFICIENT,
    STATUS_PROVISIONAL, STATUS_PUBLISHED, WEIGHTS,
)

ALL_KEYS = (PUBLIC_CONCENTRATION, SPORTSBOOK_RELIEF, REVERSE_LINE_MOVEMENT, LATE_MOVEMENT,
            CROSS_BOOK_DISAGREEMENT, MODEL_MARKET_DISAGREEMENT, INFORMATION_UNCERTAINTY,
            SOCIAL_NARRATIVE)


def components(scores: dict[str, float]) -> list[Component]:
    """Every component, with the named ones available at the given score."""

    return [Component(key=key, available=key in scores, score=scores.get(key)) for key in ALL_KEYS]


def score(scores: dict[str, float]) -> dict:
    return RffService._score(components(scores))


def test_one_component_at_100_cannot_become_the_composite():
    """The live failure: information uncertainty alone, weight 0.08, scored 100."""

    result = score({INFORMATION_UNCERTAINTY: 100.0})
    assert result["evidence_coverage"] == pytest.approx(0.08)
    assert result["known_contribution"] == pytest.approx(8.0), "0.08 x 100, against the fixed total"
    assert result["score_range"] == [8.0, 100.0]
    assert result["score"] is None, "no number below the publication floor"
    assert result["status"] == STATUS_INSUFFICIENT
    assert result["band"]["key"] == CATEGORY_UNCERTAIN
    assert result["rankable"] is False
    # The old renormalised figure survives only as a named diagnostic.
    assert result["diagnostics"]["observedComponentAverage"] == pytest.approx(100.0)
    assert "not RFF" in result["diagnostics"]["note"]


def test_unavailable_is_never_zero_and_never_imputed():
    high = score({INFORMATION_UNCERTAINTY: 100.0})
    low = score({INFORMATION_UNCERTAINTY: 0.0})
    # If absence were zero, both would have the same interval; it is not.
    assert high["lower_bound"] == 8.0 and low["lower_bound"] == 0.0
    # The upper bound is the known contribution plus the unobserved weight, so a
    # low observed component cannot reach 100 either.
    assert high["upper_bound"] == 100.0 and low["upper_bound"] == 92.0
    assert high["unknown_weight"] == pytest.approx(0.92)
    for entry in components({}):
        assert entry.as_dict()["score"] is None and entry.as_dict()["display"] == "Unavailable"


def test_the_interval_is_exactly_the_unobserved_weight():
    result = score({REVERSE_LINE_MOVEMENT: 50.0, CROSS_BOOK_DISAGREEMENT: 50.0})
    coverage = WEIGHTS[REVERSE_LINE_MOVEMENT] + WEIGHTS[CROSS_BOOK_DISAGREEMENT]
    assert result["evidence_coverage"] == pytest.approx(coverage)
    assert result["known_contribution"] == pytest.approx(coverage * 50.0)
    assert result["upper_bound"] - result["lower_bound"] == pytest.approx((1 - coverage) * 100)


def test_half_covered_games_are_provisional_and_unranked():
    result = score({REVERSE_LINE_MOVEMENT: 8.0, CROSS_BOOK_DISAGREEMENT: 40.0,
                    MODEL_MARKET_DISAGREEMENT: 82.0, INFORMATION_UNCERTAINTY: 50.0})
    assert result["evidence_coverage"] == pytest.approx(0.50)
    assert result["status"] == STATUS_PROVISIONAL
    assert result["score"] is None and result["rankable"] is False
    assert result["band"]["key"] == CATEGORY_UNCERTAIN
    assert [entry["code"] for entry in result["publication_blockers"]] == ["COVERAGE_BELOW_PUBLICATION_THRESHOLD"]


def test_a_published_score_is_the_known_contribution_not_a_renormalised_mean():
    scores = {REVERSE_LINE_MOVEMENT: 5.0, LATE_MOVEMENT: 5.0, CROSS_BOOK_DISAGREEMENT: 5.0,
              MODEL_MARKET_DISAGREEMENT: 5.0, INFORMATION_UNCERTAINTY: 5.0,
              PUBLIC_CONCENTRATION: 5.0}
    result = score(scores)
    assert result["evidence_coverage"] >= MIN_COVERAGE_PUBLISHED
    assert result["status"] == STATUS_PUBLISHED and result["rankable"] is True
    assert result["score"] == pytest.approx(result["known_contribution"])
    assert result["score"] < result["diagnostics"]["observedComponentAverage"], (
        "the composite counts the unobserved weight as unknown, not as agreement")
    assert result["band"]["key"] == "CALM"
    assert len(result["families_present"]) >= MIN_FAMILIES_PUBLISHED


def test_publication_needs_components_and_more_than_one_family():
    # Coverage is high, but everything measured is one family plus a dependency.
    thin = RffService._score([
        Component(key=REVERSE_LINE_MOVEMENT, available=True, score=90.0),
        Component(key=LATE_MOVEMENT, available=True, score=90.0),
        Component(key=PUBLIC_CONCENTRATION, available=True, score=90.0),
        Component(key=SPORTSBOOK_RELIEF, available=True, score=90.0),
        *[Component(key=key, available=False) for key in
          (CROSS_BOOK_DISAGREEMENT, MODEL_MARKET_DISAGREEMENT, INFORMATION_UNCERTAINTY, SOCIAL_NARRATIVE)],
    ])
    assert thin["evidence_coverage"] == pytest.approx(0.64)
    assert thin["status"] == STATUS_PROVISIONAL
    codes = {entry["code"] for entry in thin["publication_blockers"]}
    assert "COVERAGE_BELOW_PUBLICATION_THRESHOLD" in codes

    few = score({REVERSE_LINE_MOVEMENT: 50.0, LATE_MOVEMENT: 50.0, CROSS_BOOK_DISAGREEMENT: 50.0,
                 MODEL_MARKET_DISAGREEMENT: 50.0, INFORMATION_UNCERTAINTY: 50.0,
                 PUBLIC_CONCENTRATION: 50.0})
    assert few["available_components"] >= MIN_COMPONENTS_PUBLISHED
    assert few["status"] == STATUS_PUBLISHED


def test_a_category_is_refused_when_the_interval_straddles_bands():
    # 84% coverage: the interval is narrow enough, but it crosses a band boundary.
    wide = score({REVERSE_LINE_MOVEMENT: 60.0, LATE_MOVEMENT: 60.0, CROSS_BOOK_DISAGREEMENT: 60.0,
                  MODEL_MARKET_DISAGREEMENT: 60.0, INFORMATION_UNCERTAINTY: 60.0,
                  PUBLIC_CONCENTRATION: 60.0})
    assert wide["status"] == STATUS_PUBLISHED
    assert wide["band"]["key"] == CATEGORY_UNCERTAIN
    assert "spans Curious to Spicy" in wide["category_reason"]


def test_maximum_unrest_cannot_come_from_one_component_or_thin_coverage():
    single = score({INFORMATION_UNCERTAINTY: 100.0})
    assert single["band"]["key"] != "MAXIMUM_FUCKERY"
    # Even at full coverage, the top band needs the whole interval inside it.
    everything = score({key: 100.0 for key in ALL_KEYS})
    assert everything["evidence_coverage"] == pytest.approx(1.0)
    assert everything["score"] == pytest.approx(100.0)
    assert everything["band"]["key"] == "MAXIMUM_FUCKERY", "full evidence, unanimously extreme"
    assert everything["interval_width"] == 0.0


def test_stale_evidence_does_not_buy_coverage():
    stale = RffService._score([
        Component(key=CROSS_BOOK_DISAGREEMENT, available=False, reason=STALE_EVIDENCE, stale=True),
        *[Component(key=key, available=False) for key in ALL_KEYS if key != CROSS_BOOK_DISAGREEMENT],
    ])
    assert stale["evidence_coverage"] == 0.0
    assert stale["status"] == STATUS_INSUFFICIENT
    entry = next(c for c in stale["contributions"] if c["key"] == CROSS_BOOK_DISAGREEMENT) if stale["contributions"] else None
    assert entry is None, "a stale component contributes nothing"


def test_contribution_shares_are_of_the_whole_measure():
    result = score({INFORMATION_UNCERTAINTY: 100.0, CROSS_BOOK_DISAGREEMENT: 50.0})
    shares = {entry["key"]: entry["share_of_measure"] for entry in result["contributions"]}
    assert shares[INFORMATION_UNCERTAINTY] == pytest.approx(0.08)
    assert shares[CROSS_BOOK_DISAGREEMENT] == pytest.approx(0.07)
    assert sum(shares.values()) < 1.0, "shares can never total the whole measure on partial evidence"
