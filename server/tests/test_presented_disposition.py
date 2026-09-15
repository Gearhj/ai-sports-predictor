"""An unevaluated game is never presented as NO_BET."""
from app.services.slate_status import presented_disposition


def test_the_real_2026_09_15_row_is_not_yet_evaluated():
    assert presented_disposition(
        "NO_BET", "V2 NOT_YET_EVALUATED: the game has not reached its evaluation window.",
    ) == "NOT_YET_EVALUATED"


def test_an_evaluated_no_bet_stays_no_bet():
    assert presented_disposition(
        "NO_BET", "V2 TIER_B_PROBABLE_STABLE: MONEYLINE selection LAD ML does not clear the adjusted thresholds",
    ) == "NO_BET"


def test_other_dispositions_pass_through():
    assert presented_disposition("QUALIFIED_BET", "qualifies") == "QUALIFIED_BET"
    assert presented_disposition(None, None) is None


def test_a_missing_quote_is_not_an_evaluated_no_bet():
    assert presented_disposition(
        "NO_BET", "No complete two-sided governed MONEYLINE quote is available.",
    ) == "NOT_YET_EVALUATED"
