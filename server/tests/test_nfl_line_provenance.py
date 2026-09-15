"""Every NFL line names its provider, standing, age and role.

On 2026-09-15 the Week 2 cards carried DraftKings moneyline, spread and total
values from one The Odds API fetch on 2026-08-29 -- real quotes, sixteen days
old -- labelled only by book.
"""
from app.services.nfl_read_service import _provider_classification, _provider_label


def test_the_odds_api_is_governed():
    assert _provider_classification("THE_ODDS_API", "draftkings") == "GOVERNED"
    assert _provider_label("THE_ODDS_API") == "The Odds API"


def test_a_sportsgameodds_relay_is_research_whatever_the_book():
    assert _provider_classification("SPORTSGAMEODDS", "sgo:draftkings") == "RESEARCH"
    assert _provider_classification(None, "sgo:espnbet") == "RESEARCH"


def test_an_unknown_provider_is_not_promoted():
    assert _provider_classification("SOMETHING_ELSE", "book") == "UNCLASSIFIED"
