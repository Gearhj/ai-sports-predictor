"""Entry terms come from the position, never from the latest quote.

Daily Decisions showed DEN at KC "Position recorded" beside "+114 · sgo:fanduel".
The position was taken at +115 on sgo:espnbet. These pin the label helpers that
turn persisted evidence into what the reader sees.
"""
from app.services.position_entry_terms import TIE_LABELS, _american, _provider


def test_american_label_keeps_the_sign():
    assert _american(115) == "+115"
    assert _american(-120) == "-120"
    assert _american(None) is None


def test_sgo_namespace_is_disclosed_as_a_research_provider():
    provider = _provider("SPORTSGAMEODDS", "sgo:espnbet", None)
    assert provider["classification"] == "RESEARCH"
    assert provider["label"] == "SportsGameOdds · research provider"


def test_persisted_research_classification_is_honoured():
    assert _provider("SPORTSGAMEODDS", "espnbet", "RESEARCH")["classification"] == "RESEARCH"


def test_a_governed_book_is_not_mislabelled_research():
    provider = _provider("THE_ODDS_API", "draftkings", None)
    assert provider["classification"] is None
    assert "research" not in (provider["label"] or "")


def test_only_a_persisted_tie_rule_has_a_label():
    assert TIE_LABELS["STAKE_REFUNDED_PUSH"] == "Tie settlement: stake refunded"
    assert TIE_LABELS.get(None) is None
