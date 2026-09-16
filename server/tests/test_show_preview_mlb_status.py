"""MLB's Show Preview status stays explicit.

Model Studio implemented `MLB_TEAM_LOGISTIC_C1_POISSON_A1_SHOW_PREVIEW_V1` and
published a package about it. That package is a handoff -- a statement that the
model exists and is waiting on certified current inputs -- and it is not a
forecast. The distance between those two things is exactly the distance between
"MLB preview forecast awaiting producer" and a number on air that nobody
produced, so the tests here pin both ends:

1. **The handoff cannot become a forecast.** It is not in the discovery index,
   it has no `forecast.json`, and fed to the validator by hand it is refused.
2. **When a genuine MLB package does arrive, it cannot be shown bare.** Six
   disclosures attach to it wherever it is displayed, and two of them drop out
   if the package actually supplies what they say is missing.

Nothing here reads the production database or contacts anything.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.show_preview.contract import (
    ContractViolation, MLB_PREVIEW_DISCLOSURES, mandatory_disclosures, validate_forecast,
)
from app.show_preview.exchange import ExchangeViolation, verify_index

EXCHANGE = Path.home() / "asp-model-exchange" / "show-preview-forecasts-v1"
INDEX_SHA = "146fd0c48a0dab68fbd7acb279a417c48d989a289034925d58dd86aa69bdee59"
HANDOFF_SHA = "3f445ec0648729d905324c74132bae1e2809a110667cdee654a5b1a86d1fda33"
NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

exchange_present = pytest.mark.skipif(
    not (EXCHANGE / "indexes" / "sha256" / f"{INDEX_SHA}.json").exists(),
    reason="the Model Exchange is not mounted on this machine")


# -- the handoff is not a forecast -------------------------------------------

@exchange_present
def test_the_discovery_index_names_no_handoff(  ) -> None:
    """The import path never reaches the handoff, because nothing points at it."""

    index, verified = verify_index(EXCHANGE, INDEX_SHA)
    assert len(verified) == 17
    assert {package.sport for package in verified} == {"NFL", "CFB"}
    assert not [package for package in verified if package.sport == "MLB"]
    for package in verified:
        assert "handoff" not in package.package_path.lower()
    assert HANDOFF_SHA not in json.dumps(index)


@exchange_present
def test_the_handoff_package_declares_zero_forecasts_and_has_no_forecast_file() -> None:
    directory = EXCHANGE / "handoffs" / "sha256" / HANDOFF_SHA
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "ASP_SHOW_PREVIEW_HANDOFF_PACKAGE_V1"
    assert manifest["forecastPackagesPublished"] == 0
    assert manifest["consumerActivation"] is False
    assert not (directory / "forecast.json").exists()
    assert "forecast.json" not in {entry["path"] for entry in manifest["files"]}


@exchange_present
def test_the_handoff_manifest_is_refused_by_the_forecast_validator() -> None:
    """Fed to the validator by hand, it is still not a forecast."""

    document = json.loads(
        (EXCHANGE / "handoffs" / "sha256" / HANDOFF_SHA / "manifest.json").read_text(encoding="utf-8"))
    with pytest.raises(ContractViolation):
        validate_forecast(document, now=NOW)


@exchange_present
def test_pointing_the_index_verifier_at_the_handoff_fails_closed() -> None:
    """A handoff address is not a discovery index address."""

    with pytest.raises(ExchangeViolation):
        verify_index(EXCHANGE, HANDOFF_SHA)


def test_a_handoff_shaped_document_is_refused_without_the_exchange() -> None:
    """The same refusal, pinned where no mounted exchange is needed."""

    with pytest.raises(ContractViolation):
        validate_forecast({"schema": "ASP_SHOW_PREVIEW_HANDOFF_PACKAGE_V1",
                           "type": "MLB_PREVIEW_RESEARCH_CAPABILITY_AND_CURRENT_INPUT_DEPENDENCY",
                           "forecastPackagesPublished": 0}, now=NOW)


# -- what a genuine MLB package must be shown with ---------------------------

def _view(**overrides) -> dict:
    base = {"starters": "UNKNOWN", "lineups": "UNKNOWN", "distribution": None,
            "model_id": "MLB_TEAM_LOGISTIC_C1_POISSON_A1_SHOW_PREVIEW_V1"}
    return {**base, **overrides}


def test_an_mlb_preview_forecast_carries_all_six_disclosures() -> None:
    codes = [item["code"] for item in mandatory_disclosures("MLB", _view())]
    assert codes == [
        "TEAM_HISTORY_MODEL_ONLY",
        "STARTERS_AND_LINEUPS_UNKNOWN",
        "NO_JOINT_SCORE_DISTRIBUTION",
        "NO_TOTAL_OR_RUN_LINE_PROBABILITY",
        "RESEARCH_ONLY",
        "NOT_FINAL_V18",
    ]
    assert len(MLB_PREVIEW_DISCLOSURES) == 6


def test_each_disclosure_says_the_thing_it_exists_to_prevent() -> None:
    by_code = {item["code"]: item["message"] for item in mandatory_disclosures("MLB", _view())}
    assert "No pitchers, lineups, injuries" in by_code["TEAM_HISTORY_MODEL_ONLY"]
    assert "unknown" in by_code["STARTERS_AND_LINEUPS_UNKNOWN"].lower()
    assert "not one joint distribution" in by_code["NO_JOINT_SCORE_DISTRIBUTION"]
    assert "Over/Under" in by_code["NO_TOTAL_OR_RUN_LINE_PROBABILITY"]
    assert "no decision, stake, position or wagering authority" in by_code["RESEARCH_ONLY"]
    assert "Final V18" in by_code["NOT_FINAL_V18"]


def test_a_disclosure_is_dropped_when_the_package_supplies_what_it_denies() -> None:
    """A disclosure that contradicts the evidence is a second inaccuracy."""

    stated = mandatory_disclosures("MLB", _view(starters="CONFIRMED", lineups="CONFIRMED"))
    assert "STARTERS_AND_LINEUPS_UNKNOWN" not in [item["code"] for item in stated]

    joint = mandatory_disclosures("MLB", _view(distribution={"draws": 20000}))
    assert "NO_JOINT_SCORE_DISTRIBUTION" not in [item["code"] for item in joint]
    # The unconditional four never drop.
    for codes in ([i["code"] for i in stated], [i["code"] for i in joint]):
        assert {"TEAM_HISTORY_MODEL_ONLY", "NO_TOTAL_OR_RUN_LINE_PROBABILITY",
                "RESEARCH_ONLY", "NOT_FINAL_V18"} <= set(codes)


def test_no_forecast_means_no_disclosures_to_attach() -> None:
    """An absent forecast is reported as absent, not described."""

    assert mandatory_disclosures("MLB", None) == []


def test_the_other_sports_are_not_given_mlb_limitations() -> None:
    for sport in ("NFL", "CFB", "NBA"):
        assert mandatory_disclosures(sport, _view()) == []


def test_the_disclosures_are_a_copy_a_caller_cannot_edit() -> None:
    first = mandatory_disclosures("MLB", _view())
    first[0]["message"] = "tampered"
    assert mandatory_disclosures("MLB", _view())[0]["message"] != "tampered"
