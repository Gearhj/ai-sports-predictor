"""CFB readiness price state mirrors the checkpoint's quote gates (MAIN review fcaa092e).

R1: only over-age pairs -> QUOTE_TOO_STALE blocker (unless a collection is still ahead).
R3: the collector switch never makes a permitted retained pair unusable.
The engine-level pairing boundaries (wrong kickoff, malformed book, other source,
stale decline) are covered in test_cfb_odds_api_source.py.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.cfb import paper_decision
from app.services import sport_readiness as sr

KICK = datetime(2026, 10, 10, 19, 30, tzinfo=UTC)          # buy window 18:15..18:55
SOURCES = ("SPORTSGAMEODDS", "THE_ODDS_API")


def _pair(observed: datetime, source: str = "THE_ODDS_API") -> dict:
    return {"source": source, "sportsbook": "fanduel", "pair_observed_at": observed.isoformat(),
            "retained_kickoff": KICK.isoformat()}


@pytest.fixture
def patched(monkeypatch):
    holder: dict = {"pairs": []}
    monkeypatch.setattr(paper_decision, "_current_game", lambda session, game_id: (KICK, "SCHEDULED"))
    monkeypatch.setattr(paper_decision, "kickoff_bound_pairs",
                        lambda session, game_id, kickoff, moment=None, close_before=None: holder["pairs"])
    return holder


def _price(now: datetime, *, collector: bool, sources=SOURCES):
    return sr._cfb_odds_api_price(None, "g", now, sources=sources, collector_enabled=collector)


def test_fresh_permitted_pair_is_usable(patched):
    now = KICK - timedelta(minutes=25)
    patched["pairs"] = [_pair(now - timedelta(minutes=10))]
    out = _price(now, collector=True)
    assert out["state"] == "PRICE_HELD" and "blocker" not in out and out["pairs"] == 1


def test_r3_collector_off_still_uses_a_permitted_retained_pair(patched):
    now = KICK - timedelta(minutes=25)
    patched["pairs"] = [_pair(now - timedelta(minutes=10))]
    out = _price(now, collector=False)
    assert out["state"] == "PRICE_HELD" and "blocker" not in out
    assert out["collection"]["enabled"] is False and out["collection"]["ahead"] is False


def test_r1_stale_only_with_no_collection_ahead_blocks_quote_too_stale(patched):
    now = KICK - timedelta(minutes=25)                     # buy window already closed
    patched["pairs"] = [_pair(now - timedelta(minutes=241))]
    for collector in (True, False):
        out = _price(now, collector=collector)
        assert out["state"] == "PRICE_HELD_STALE" and out["blocker"] == "QUOTE_TOO_STALE"
        assert out["stale_reference_pairs"] == 1


def test_r1_stale_only_with_a_collection_still_ahead_is_scheduled_not_usable(patched):
    now = KICK - timedelta(minutes=90)                     # before the buy window closes
    patched["pairs"] = [_pair(now - timedelta(minutes=300))]
    out = _price(now, collector=True)
    assert out["state"] == "COLLECTION_SCHEDULED" and "blocker" not in out
    assert out["stale_reference_pairs"] == 1
    off = _price(now, collector=False)                      # nothing will be bought: blocked now
    assert off["blocker"] == "QUOTE_TOO_STALE"


def test_older_leg_age_boundary_matches_the_policy_maximum(patched):
    now = KICK - timedelta(minutes=20)
    limit = paper_decision.POLICY.max_quote_age_minutes
    patched["pairs"] = [_pair(now - timedelta(minutes=limit))]
    assert _price(now, collector=False)["state"] == "PRICE_HELD"
    patched["pairs"] = [_pair(now - timedelta(minutes=limit, seconds=1))]
    assert _price(now, collector=False)["blocker"] == "QUOTE_TOO_STALE"


def test_unpermitted_source_pairs_block_no_permitted_provider(patched):
    now = KICK - timedelta(minutes=25)
    patched["pairs"] = [_pair(now - timedelta(minutes=5))]
    out = _price(now, collector=True, sources=("SPORTSGAMEODDS",))
    assert out["blocker"] == "NO_PERMITTED_PROVIDER"


def test_no_pair_window_boundary(patched):
    patched["pairs"] = []
    before = _price(KICK - timedelta(minutes=36), collector=True)   # closes at T-35
    assert before["state"] == "COLLECTION_SCHEDULED" and "blocker" not in before
    after = _price(KICK - timedelta(minutes=35), collector=True)
    assert after["blocker"] == "NO_TWO_SIDED_QUOTE"
    off = _price(KICK - timedelta(minutes=60), collector=False)
    assert off["blocker"] == "NO_TWO_SIDED_QUOTE"
