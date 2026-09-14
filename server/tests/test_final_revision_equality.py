"""A final's identity is its result, not the clock that fetched it.

THE DEFECT
----------
``payload_checksum`` was computed over the whole final DTO, which carries
``acquired_at``. So re-reading a finished game produced a different digest every
time and minted a fresh revision of a result that had not changed. It is not
hypothetical: re-running the 2026-09-13 slate produced revision 2 for eleven
games whose scores, innings and ``final_at`` were byte-identical to revision 1.
Only the retrieval clocks differed.

Those eleven revisions are preserved -- they are append-only evidence, and
deleting them would be a worse edit than the one that created them. What changes
is that it cannot happen again.

WHAT THE FIX MUST NOT DO
------------------------
Suppress a genuine correction. A revised score, a corrected completion time or a
changed innings count must still create a successor revision, because that is
what a correction IS. So the tests below check both directions.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.historical.persistence import _checksum, _result_content


def final_payload(**over):
    """A final DTO as `model_dump(mode="json")` produces it."""

    payload = {
        "source_game_id": "824225",
        "official_date": "2026-09-13",
        "status": "Final",
        "abstract_state": "Final",
        "home_score": 8,
        "away_score": 1,
        "innings_played": 9,
        "event_completed_at": "2026-09-13T22:51:26.280000+00:00",
        "completion_time_basis": "AUTHORITATIVE_FINAL_AT",
        "completion_time_source_field": "gameData.datetime",
        "completion_time_method": "explicit",
        "completion_time_limitation": None,
        "source_published_at": None,
        # The field that caused it.
        "acquired_at": "2026-09-13T21:21:20.382571+00:00",
        "research_provenance": "HISTORICAL_RECONSTRUCTION",
        "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
    }
    payload.update(over)
    return payload


def test_the_retrieval_clock_is_not_part_of_the_identity():
    first = final_payload()
    # The same game, read again fifteen hours later. This is the exact pair that
    # produced revision 2 for eleven games.
    later = final_payload(acquired_at="2026-09-14T12:49:29.598580+00:00")
    assert _checksum(_result_content(first)) == _checksum(_result_content(later))


def test_the_old_behaviour_is_what_minted_the_spurious_revisions():
    """Guards the regression by showing the difference the fix makes."""

    first, later = final_payload(), final_payload(
        acquired_at="2026-09-14T12:49:29.598580+00:00")
    # Over the whole payload -- what the code used to do -- they differ.
    assert _checksum(first) != _checksum(later)
    # Over result content alone they do not.
    assert _checksum(_result_content(first)) == _checksum(_result_content(later))


def test_a_corrected_score_is_still_a_different_final():
    original = _result_content(final_payload())
    corrected = _result_content(final_payload(home_score=9))
    assert _checksum(original) != _checksum(corrected)


def test_a_corrected_completion_time_is_still_a_different_final():
    original = _result_content(final_payload())
    corrected = _result_content(
        final_payload(event_completed_at="2026-09-13T23:05:00+00:00"))
    assert _checksum(original) != _checksum(corrected)


def test_a_corrected_innings_count_is_still_a_different_final():
    original = _result_content(final_payload())
    corrected = _result_content(final_payload(innings_played=10))
    assert _checksum(original) != _checksum(corrected)


def test_only_the_retrieval_clock_is_stripped():
    stripped = set(final_payload()) - set(_result_content(final_payload()))
    # Anything else removed here would be a result fact quietly excluded from
    # the identity, which is the opposite failure and a worse one.
    assert stripped == {"acquired_at"}


def test_every_result_fact_survives_into_the_identity():
    content = _result_content(final_payload())
    for field in ("source_game_id", "official_date", "status", "abstract_state",
                  "home_score", "away_score", "innings_played",
                  "event_completed_at", "completion_time_basis",
                  "source_published_at", "evidence_class"):
        assert field in content, f"{field} must remain part of the final's identity"
