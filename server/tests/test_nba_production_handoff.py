"""The NBA Gen1 production handoff adapter, staged and fail-closed.

Three things are worth more than the rest here.

First, the pin covers a DIRECTORY. The sha256 is of `index.json`, and the index
carries the hash and byte count of every member, so a one-byte change anywhere
in the package must be refused before anything is parsed.

Second, the producer estimates THREE separate things -- Ridge point
expectations, an Elo margin association and a 10,000-draw paired population --
and its own disclosures say so. Only the population carries a distribution, so
only the population may answer a market. Deriving a win probability from the
Elo margin would be a silent substitution of one channel for another.

Third, nothing predating `prospectiveEffectiveAtUtc` is governed. BOS-DET is
research and stays research.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.forecast_exchange.nba_production_handoff import (
    ACTIVE, CHANNEL_DISTRIBUTION, CHANNEL_POINT, CHANNEL_WINNER, EXPECTED_DRAW_COUNT,
    MARKET_CHANNEL, NOT_RECEIVED, REJECTED, RETAINED_LIMITATIONS, NbaHandoffRejected,
    authorised_channel, governs, production_status, verify_package,
)

PIN = "7113b71ff5819c89103ad19d5f9afb22c51dbc92980be12b56ce8826cb34ccaf"
PACKAGE = Path(r"C:\Users\james\asp-model-exchange\nba-gen1-production-v1"
               r"\handoffs\sha256") / PIN
INDEX = PACKAGE / "index.json"

real_package = pytest.mark.skipif(
    not INDEX.is_file(), reason="the NBA handoff package is not present on this machine")


class _Settings:
    def __init__(self, path: str = "", sha: str = "") -> None:
        self.nba_production_handoff_path = path
        self.nba_production_handoff_sha256 = sha


# -- fail-closed ------------------------------------------------------------
def test_no_pin_means_not_received_and_no_label():
    verdict = production_status(_Settings())
    assert verdict.status == NOT_RECEIVED
    assert verdict.accepted is False
    assert verdict.production_label_allowed is False


def test_a_path_without_a_pin_is_still_not_received():
    assert production_status(_Settings(path=str(INDEX))).status == NOT_RECEIVED


def test_a_wrong_pin_is_rejected_not_accepted(tmp_path):
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"schemaVersion": "x", "members": []}), encoding="utf-8")
    verdict = production_status(_Settings(path=str(index), sha="0" * 64))
    assert verdict.status == REJECTED and verdict.accepted is False
    assert "INDEX_SHA256_MISMATCH" in verdict.reasons[0]


def test_a_missing_index_is_refused(tmp_path):
    with pytest.raises(NbaHandoffRejected) as error:
        verify_package(tmp_path / "nothing.json", PIN)
    assert error.value.code == "INDEX_MISSING"


# -- the package itself -----------------------------------------------------
@real_package
def test_the_real_package_verifies_byte_for_byte():
    package = verify_package(INDEX, PIN)
    assert set(package["verified"]) == {"ACTIVATION.md", "CLAUDE_HANDOFF.md", "manifest.json"}
    assert package["manifest"]["status"] == "READY_AWAITING_ACTIVATION"


@real_package
def test_a_tampered_member_is_refused(tmp_path):
    """One byte different in any member, and the package is not a package."""

    copy = tmp_path / "pkg"
    copy.mkdir()
    for name in ("index.json", "ACTIVATION.md", "CLAUDE_HANDOFF.md", "manifest.json"):
        (copy / name).write_bytes((PACKAGE / name).read_bytes())
    # The index still hashes to the pin; a member no longer matches the index.
    (copy / "ACTIVATION.md").write_bytes((PACKAGE / "ACTIVATION.md").read_bytes() + b" ")
    with pytest.raises(NbaHandoffRejected) as error:
        verify_package(copy / "index.json", PIN)
    assert error.value.code in {"MEMBER_SHA256_MISMATCH", "MEMBER_SIZE_MISMATCH"}


@real_package
def test_the_staged_handoff_is_admissible_but_not_active():
    verdict = production_status(_Settings(path=str(INDEX), sha=PIN))
    assert verdict.accepted is True
    assert verdict.status == "READY_AWAITING_ACTIVATION"
    # Ready is not active: no production label until the producer is activated.
    assert verdict.production_label_allowed is False
    assert verdict.identity["authority"]["producer_activated"] is False
    assert verdict.identity["draw_count"] == EXPECTED_DRAW_COUNT
    assert verdict.identity["close_score_challenger_enabled"] is False


@real_package
def test_the_joint_population_is_the_admitted_one():
    """A production baseline may not swap the population underneath NBA."""

    from app.probability.adapter import ADMITTED

    verdict = production_status(_Settings(path=str(INDEX), sha=PIN))
    assert (verdict.identity["distribution_configuration"]
            == ADMITTED["NBA"]["configuration_id"])
    assert verdict.identity["draw_count"] == ADMITTED["NBA"]["draw_count"]


# -- channels are never blended --------------------------------------------
def test_only_the_distribution_may_answer_a_market():
    assert MARKET_CHANNEL == {
        "MONEYLINE": CHANNEL_DISTRIBUTION,
        "SPREAD": CHANNEL_DISTRIBUTION,
        "TOTAL": CHANNEL_DISTRIBUTION,
    }
    for market in ("MONEYLINE", "SPREAD", "TOTAL"):
        assert authorised_channel(market) == CHANNEL_DISTRIBUTION
        assert authorised_channel(market) not in {CHANNEL_POINT, CHANNEL_WINNER}


def test_a_market_with_no_authorised_channel_gets_none():
    """A closed allow-list: an unlisted market has no channel, not a default."""

    for market in ("PLAYER_PROPS", "FIRST_HALF", "TEAM_TOTAL", ""):
        assert authorised_channel(market) is None


def test_the_point_and_winner_channels_are_declared_non_probabilistic():
    from app.forecast_exchange.nba_production_handoff import CHANNEL_ROLES

    assert CHANNEL_ROLES[CHANNEL_POINT]["is_probability"] is False
    assert CHANNEL_ROLES[CHANNEL_WINNER]["is_probability"] is False
    assert CHANNEL_ROLES[CHANNEL_DISTRIBUTION]["is_probability"] is True
    assert CHANNEL_ROLES[CHANNEL_POINT]["markets"] == []
    assert CHANNEL_ROLES[CHANNEL_WINNER]["markets"] == []


def test_the_elo_channel_is_described_as_a_margin_association_not_a_probability():
    from app.forecast_exchange.nba_production_handoff import CHANNEL_ROLES

    note = CHANNEL_ROLES[CHANNEL_WINNER]["display_note"].lower()
    assert "margin" in note and "not a calibrated win probability" in note


# -- the effective clock ----------------------------------------------------
@real_package
def test_a_package_created_before_the_effective_instant_is_not_governed():
    """BOS-DET predates activation and must never be relabelled."""

    verdict = production_status(_Settings(path=str(INDEX), sha=PIN))
    identity = verdict.identity
    effective = identity["prospective_effective_at_utc"]
    assert governs(identity, effective) is True
    assert governs(identity, "2026-09-15T00:00:00+00:00") is False
    assert governs(identity, "2026-09-18T01:16:08.176806+00:00") is False
    assert governs(identity, "2026-09-19T12:00:00+00:00") is True


def test_governance_fails_closed_on_junk_input():
    identity = {"prospective_effective_at_utc": "2026-09-18T01:16:08+00:00"}
    assert governs(identity, None) is False
    assert governs(identity, "not a timestamp") is False
    assert governs({}, "2026-09-19T00:00:00+00:00") is False


# -- the limitations travel -------------------------------------------------
def test_the_retained_limitations_are_carried_and_state_the_one_point_gap():
    joined = " ".join(RETAINED_LIMITATIONS)
    assert "7.78%" in joined and "4.01%" in joined
    assert "challenger stays disabled" in joined
    assert "No profitability claim" in joined
    assert "separately estimated" in joined


def test_the_status_payload_carries_channels_and_limitations():
    payload = production_status(_Settings()).as_dict()
    assert payload["market_channels"]["MONEYLINE"] == CHANNEL_DISTRIBUTION
    assert payload["retained_limitations"]
    assert payload["production_label_allowed"] is False
    assert ACTIVE not in {payload["status"]}
