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


def test_the_account_response_schema_does_not_drop_entry_terms():
    """The service attached `entry` and the response model silently removed it."""

    from app.schemas.experiment_accounts import AccountPosition

    position = AccountPosition.model_validate({
        "id": "d85909ab-f8ae-488b-be5c-dd0b0cc98117",
        "placed_at": "2026-09-14T19:49:35.655349-04:00",
        "stake": 5.0, "decimal_odds": 2.15, "status": "settled", "result": "loss",
        "era": "NFL_M1_XGBOOST", "profit_loss": -5.0, "settled_at": None,
        "entry": {
            "position_id": "d85909ab-f8ae-488b-be5c-dd0b0cc98117",
            "side": "DEN", "american_odds": 115, "american_label": "+115",
            "decimal_odds": 2.15, "sportsbook_key": "sgo:espnbet",
            "provider": {"source": "SPORTSGAMEODDS", "name": "SportsGameOdds",
                         "classification": "RESEARCH",
                         "label": "SportsGameOdds · research provider"},
            "stake": 5.0, "placed_at": "2026-09-14T19:49:35.655349-04:00",
            "status": "settled", "result": "loss",
            "tie_settlement": "STAKE_REFUNDED_PUSH",
            "tie_settlement_label": "Tie settlement: stake refunded",
            "headline": "DEN +115",
        },
    })
    dumped = position.model_dump()
    assert dumped["entry"]["headline"] == "DEN +115"
    assert dumped["entry"]["sportsbook_key"] == "sgo:espnbet"
    assert dumped["entry"]["tie_settlement_label"] == "Tie settlement: stake refunded"
