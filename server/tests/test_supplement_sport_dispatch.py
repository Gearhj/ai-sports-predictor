"""The generic supplement importer, extended by sport without weakening NFL.

Runs against the real content-addressed packages in the exchange; tamper cases
copy them to a temp directory first, so nothing published is modified.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.forecast_exchange.supplements import (
    PAPER_ELIGIBILITY_VERSIONS, SupplementImportError, chain_heads, verify_supplement,
)

EXCHANGE = Path(r"C:\Users\james\asp-model-exchange")
NBA = EXCHANGE / "nba-gen1-research-v1/paper-eligibility/sha256/ef3fd91d9444e4e0ec8959e2cf888bb117efd7b3486632c9539122f6eaf46f12"
CFB = EXCHANGE / "cfb-gen1-research-v1/paper-eligibility/sha256/7d653fec4ce94caf853d58db17348751ad5a5286539581be581bb81e826193e7"
NFL = EXCHANGE / "nfl-labs-supplements/sha256/c533de6a1d714615552a2055d52d45cebc40799ccfe14767156165c73207bb69"

pytestmark = pytest.mark.skipif(not NBA.is_dir(), reason="exchange packages not present")


def test_each_sport_has_exactly_one_contract():
    assert PAPER_ELIGIBILITY_VERSIONS == {
        "NFL": "ASP_NFL_PAPER_RESEARCH_ELIGIBILITY_V1",
        "NBA": "ASP_NBA_PAPER_RESEARCH_ELIGIBILITY_V1",
        "CFB": "ASP_CFB_PAPER_RESEARCH_ELIGIBILITY_V1",
    }


@pytest.mark.parametrize("package", [NBA, CFB])
def test_the_published_successors_verify(package):
    assert verify_supplement(package).kind == "PAPER_RESEARCH_ELIGIBILITY"


def test_the_nfl_record_still_verifies_on_its_unchanged_path():
    if not NFL.is_dir():
        pytest.skip("NFL package not present under that address")
    assert verify_supplement(NFL).supplement["sport"] == "NFL"


def _copy(package: Path, tmp_path: Path) -> Path:
    target = tmp_path / package.name
    shutil.copytree(package, target)
    return target


def test_a_single_changed_byte_is_refused(tmp_path):
    copy = _copy(NBA, tmp_path)
    raw = (copy / "supplement.json").read_bytes()
    (copy / "supplement.json").write_bytes(raw.replace(b"0.5619", b"0.5620", 1))
    with pytest.raises(SupplementImportError, match="do not hash|canonical"):
        verify_supplement(copy)


def test_a_sport_carrying_another_sports_version_is_refused(tmp_path):
    """Checked after the hash gate, so rebuild a self-consistent package with the wrong version."""
    from app.forecast_exchange.supplements import CONTRACT_VERSION, MANIFEST_SCHEMA  # noqa: F401
    from app.forecast_exchange import supplements as module

    doc = json.loads((NBA / "supplement.json").read_bytes())
    doc["artifactVersion"] = "ASP_CFB_PAPER_RESEARCH_ELIGIBILITY_V1"
    with pytest.raises(SupplementImportError, match="ASP_NBA_PAPER_RESEARCH_ELIGIBILITY_V1"):
        module._verify_paper_eligibility(doc, doc["summaries"])


def test_a_larger_cap_than_the_policy_is_refused():
    from app.forecast_exchange import supplements as module

    doc = json.loads((CFB / "supplement.json").read_bytes())
    doc["summaries"]["authorization"]["initialPositionCapUsd"] = 50
    with pytest.raises(SupplementImportError, match="initialPositionCapUsd"):
        module._verify_paper_eligibility(doc, doc["summaries"])


def test_a_second_market_is_refused():
    from app.forecast_exchange import supplements as module

    doc = json.loads((NBA / "supplement.json").read_bytes())
    doc["summaries"]["authorization"]["marketScope"] = ["MONEYLINE", "TOTAL"]
    with pytest.raises(SupplementImportError, match="market scope"):
        module._verify_paper_eligibility(doc, doc["summaries"])


def test_a_permission_dated_before_its_forecast_is_refused():
    from app.forecast_exchange import supplements as module

    doc = json.loads((CFB / "supplement.json").read_bytes())
    doc["summaries"]["originalClocks"]["eligibilityPublishedAtUtc"] = "2026-09-14T00:00:00Z"
    with pytest.raises(SupplementImportError, match="Chronology"):
        module._verify_paper_eligibility(doc, doc["summaries"])


def test_real_money_is_refused():
    from app.forecast_exchange import supplements as module

    doc = json.loads((NBA / "supplement.json").read_bytes())
    doc["summaries"]["authorization"]["realMoneyExecutionAllowed"] = True
    with pytest.raises(SupplementImportError, match="realMoneyExecutionAllowed"):
        module._verify_paper_eligibility(doc, doc["summaries"])


def test_a_predecessor_beside_its_successor_is_superseded():
    successor = verify_supplement(NBA)

    class Fake:
        address = successor.supplement["predecessorSupplementAddress"]
        supplement = {"predecessorSupplementAddress": None}

    heads, superseded = chain_heads([successor, Fake()])
    assert superseded == [Fake.address]
    assert [h.address for h in heads] == [successor.address]


def test_the_consumer_allowlist_admits_nba_and_cfb_heads_and_skips_predecessors():
    """NBA/CFB eligibility has no index; the five-minute consumer must not ignore it."""
    from app.jobs.model_exchange_consumer import ConsumeOutcome, _allowlisted_eligibility

    outcome = ConsumeOutcome(root=str(EXCHANGE), dry_run=True)
    admitted = _allowlisted_eligibility(EXCHANGE, set(), outcome)
    assert NBA.name in admitted and CFB.name in admitted
    # Only the exact registered eligibility version, and only chain heads.
    assert all(item.supplement["artifactVersion"] == PAPER_ELIGIBILITY_VERSIONS[item.supplement["sport"]]
               for item in admitted.values())
    predecessors = {admitted[NBA.name].supplement["predecessorSupplementAddress"],
                    admitted[CFB.name].supplement["predecessorSupplementAddress"]}
    assert predecessors <= set(outcome.supplements_skipped)
    assert not predecessors & set(admitted)


def test_the_consumer_allowlist_admits_nothing_already_registered():
    from app.jobs.model_exchange_consumer import ConsumeOutcome, _allowlisted_eligibility

    outcome = ConsumeOutcome(root=str(EXCHANGE), dry_run=True)
    assert _allowlisted_eligibility(EXCHANGE, {NBA.name, CFB.name}, outcome) == {}
