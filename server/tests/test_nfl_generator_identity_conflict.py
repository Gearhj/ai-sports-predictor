"""The NFL generator's import loop must not be held hostage by a finished game.

On 2026-10-11 bundle 77afaba3 carried 2026_05_CHI_GB's EARLY snapshot at the
08-29 schedule (20:25Z) beside its CURRENT snapshot at the 10-05 schedule
(17:00Z). The verifier correctly refuses that bundle, but the generator's
import loop aborted on it, so every run failed and every address sorted after
it went unimported by that job.

These tests rebuild that shape synthetically -- real content addresses over
real canonical bytes, written only under ``tmp_path`` -- and pin the narrow
rule: skip and record ONLY an identity conflict whose bundle's every kickoff
has passed; fail the run for anything else.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.forecast_exchange import (
    ForecastBundleImportError,
    SportsIdentityInconsistencyError,
    verify_and_load_sports_bundle,
)
from app.jobs.orchestrator import JobOrchestrator
from app.models import ShadowForecast, ShadowForecastBundle
from tests.fixtures import nfl_final_readiness as fx

CONFLICT_EVENT = fx.EVENT_ID
#: The kickoff the EARLY snapshot was frozen with, and the one the schedule
#: later moved it to. Both are Eastern 2026-09-21, the fixture slate.
ORIGINAL_KICKOFF = fx.KICKOFF  # 2026-09-22T00:20Z
MOVED_KICKOFF = datetime(2026, 9, 21, 17, 0, tzinfo=UTC)
#: Another game in the SAME bundle that starts after both of those.
LATER_EVENT = "NFL-2026-W03-SYN-LATE-GAME"
LATER_KICKOFF = datetime(2026, 9, 22, 3, 0, tzinfo=UTC)

EARLY_AVAILABLE = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
CURRENT_AVAILABLE = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
GENERATED = datetime(2026, 9, 20, 12, 30, tzinfo=UTC)

CONFLICT_MESSAGE = (
    f"NFL event {CONFLICT_EVENT} has inconsistent exact identity across snapshots"
)

GOOD_EVENTS = (
    ("NFL-2026-W03-SYN-GOOD-ONE", ("nfl-home-011", "31", "H11", "Good Home One"),
     ("nfl-away-012", "32", "A12", "Good Away One")),
    ("NFL-2026-W03-SYN-GOOD-TWO", ("nfl-home-013", "33", "H13", "Good Home Two"),
     ("nfl-away-014", "34", "A14", "Good Away Two")),
    ("NFL-2026-W03-SYN-GOOD-THREE", ("nfl-home-015", "35", "H15", "Good Home Three"),
     ("nfl-away-016", "36", "A16", "Good Away Three")),
)


def _conflict_bundle(root):
    children = [
        fx.build_forecast_child(
            event_id=CONFLICT_EVENT, kickoff=ORIGINAL_KICKOFF,
            snapshot_type="EARLY", available_as_of=EARLY_AVAILABLE,
        ),
        fx.build_forecast_child(
            event_id=CONFLICT_EVENT, kickoff=MOVED_KICKOFF,
            snapshot_type="CURRENT", available_as_of=CURRENT_AVAILABLE,
        ),
        fx.build_forecast_child(
            event_id=LATER_EVENT, kickoff=LATER_KICKOFF,
            snapshot_type="EARLY", available_as_of=EARLY_AVAILABLE,
            home=("nfl-home-021", "41", "H21", "Late Home"),
            away=("nfl-away-022", "42", "A22", "Late Away"),
        ),
    ]
    return fx.write_forecast_wrapper(root, children, generated_at=GENERATED)


def _good_bundle(root, event_id, home, away):
    child = fx.build_forecast_child(
        event_id=event_id, kickoff=ORIGINAL_KICKOFF, snapshot_type="CURRENT",
        available_as_of=CURRENT_AVAILABLE, home=home, away=away,
    )
    return fx.write_forecast_wrapper(root, [child], generated_at=GENERATED)


def _family(tmp_path):
    bad, bad_dir = _conflict_bundle(tmp_path)
    good = [_good_bundle(tmp_path, *event)[0] for event in GOOD_EVENTS]
    fx.write_forecast_index(tmp_path, [bad, *good], updated_at=GENERATED)
    return bad, bad_dir, good


def _orchestrator(session, settings, tmp_path):
    configured = settings.model_copy(update={
        "nfl_shadow_enabled": True,
        "nfl_shadow_bundle_import_dir": str(tmp_path / fx.FORECAST_FAMILY),
    })
    return JobOrchestrator(session, configured)


def test_identity_conflict_is_a_typed_refusal_carrying_the_latest_kickoff(tmp_path):
    _, bad_dir = _conflict_bundle(tmp_path)

    with pytest.raises(SportsIdentityInconsistencyError) as raised:
        verify_and_load_sports_bundle(bad_dir)

    error = raised.value
    # Still the same refusal every existing caller catches, with the same text
    # the per-game importer already records as its rejection reason.
    assert isinstance(error, ForecastBundleImportError)
    assert str(error) == CONFLICT_MESSAGE
    assert error.event_id == CONFLICT_EVENT
    # The latest kickoff in the WHOLE bundle -- another game's -- not merely
    # the later of the conflicting event's two claims.
    assert error.latest_scheduled_start_utc == LATER_KICKOFF


async def test_generator_import_skips_a_finished_identity_conflict_and_imports_the_rest(
    session, settings, tmp_path,
):
    bad, _, good = _family(tmp_path)
    # The point of the change is the addresses AFTER the bad one; make sure the
    # synthetic family actually has some, so this cannot pass vacuously.
    listed = sorted([bad, *good])
    assert listed.index(bad) < len(listed) - 1, "fixture no longer sorts a good bundle after the bad one"
    orchestrator = _orchestrator(session, settings, tmp_path)
    after_every_kickoff = datetime(2026, 9, 23, tzinfo=UTC)

    first = await orchestrator.import_nfl_shadow_bundle(
        GENERATED.date(), now=after_every_kickoff)

    assert first["imported_bundles"] == len(good)
    assert first["identity_conflict_skipped_bundles"] == 1
    assert first["identity_conflicts_skipped"] == [{
        "bundle_sha256": bad,
        "event_id": CONFLICT_EVENT,
        "reason": CONFLICT_MESSAGE,
        "latest_scheduled_start_utc": LATER_KICKOFF.isoformat(),
        "disposition": "NOT_IMPORTED_ALL_KICKOFFS_PASSED",
    }]
    registered = set(session.scalars(select(ShadowForecastBundle.bundle_sha256)))
    assert registered == set(good)
    # Skipped means skipped: nothing from the refused bundle reached persistence.
    assert session.scalar(select(ShadowForecast.id).where(
        ShadowForecast.event_id.in_([CONFLICT_EVENT, LATER_EVENT]))) is None

    # The next run reuses what it imported and records the refusal again; it
    # never quietly forgets the bundle it would not import.
    second = await orchestrator.import_nfl_shadow_bundle(
        GENERATED.date(), now=after_every_kickoff)
    assert second["reused_bundles"] == len(good)
    assert second["imported_bundles"] == 0
    assert second["identity_conflict_skipped_bundles"] == 1


@pytest.mark.parametrize("now", [
    # Before anything starts.
    datetime(2026, 9, 20, 13, 0, tzinfo=UTC),
    # Both of the conflicting game's kickoffs have passed, but another game in
    # the same bundle has not started: refusing the bundle would also refuse
    # that game's children, so the run must still fail loudly.
    datetime(2026, 9, 22, 1, 0, tzinfo=UTC),
])
async def test_generator_import_still_fails_while_any_game_in_the_bundle_is_to_start(
    session, settings, tmp_path, now,
):
    _family(tmp_path)
    orchestrator = _orchestrator(session, settings, tmp_path)

    with pytest.raises(SportsIdentityInconsistencyError, match=CONFLICT_MESSAGE):
        await orchestrator.import_nfl_shadow_bundle(GENERATED.date(), now=now)


async def test_generator_import_still_fails_on_every_other_refusal(session, settings, tmp_path):
    good, good_dir = _good_bundle(tmp_path, *GOOD_EVENTS[0])
    fx.write_forecast_index(tmp_path, [good], updated_at=GENERATED)
    # Tamper with the bytes: the content address no longer matches.
    bundle_path = good_dir / "bundle.json"
    bundle_path.write_bytes(bundle_path.read_bytes().replace(b"Good Home One", b"Good Home Uno"))
    orchestrator = _orchestrator(session, settings, tmp_path)

    with pytest.raises(ForecastBundleImportError) as raised:
        await orchestrator.import_nfl_shadow_bundle(
            GENERATED.date(), now=datetime(2026, 12, 1, tzinfo=UTC))

    assert not isinstance(raised.value, SportsIdentityInconsistencyError)
    assert "SHA-256 does not match" in str(raised.value)
