"""What must stay true about the V18 conditional-champion activation.

These cover the gates that would be expensive to get wrong and cheap to break:
who may grant authority, when an era may begin, what a decision records about
the forecast behind it, and what happens to a game with no V18 forecast. They
deliberately do not re-test the pricing policy -- the whole design point is
that the policy is unchanged, so a test that restated it here would only
duplicate the existing suite.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.services.v18_champion import (
    BOUNDARY_PASSED,
    CLAIMS_EXECUTION,
    CLAIMS_REAL_MONEY,
    NOT_AUTHORIZED,
    WRONG_DIGEST,
    WRONG_SCHEMA,
    ChampionImportError,
    V18ChampionService,
    read_supplement,
)
from app.services.v18_forecast_view import (
    EVIDENCE_AFTER_CUTOFF,
    NOT_READY,
    NO_V18_FORECAST,
    PUBLISHED_AFTER_CUTOFF,
    V18ForecastView,
    resolve_for_decision,
)

BOUNDARY = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
BEFORE = datetime(2026, 9, 11, 18, 0, tzinfo=UTC)


def _supplement(**overrides) -> dict:
    payload = {
        "schemaVersion": "AISP_MLB_V18_CONDITIONAL_CHAMPION_HANDOFF_V1",
        "executionAllowedInAISP": False,
        "realMoneyExecutionAllowed": False,
        "activation": {"authorized": True, "historicalIntegrity": "under review"},
        "championManifest": {"sha256": "c" * 64, "bytes": 99453},
        "frozenBindings": {"sha256": "f" * 64},
        "modelIdentity": {
            "name": "MLB_V18_HGB_PA", "version": "V18",
            "artifactSha256": "a" * 64,
            "parentBundleId": "mlb-v18-whole-game-a6e7e99122f1b60701a4728b",
            "trainingRunId": "stage3-model-development-run-8ff1e0a505da4482a0a67386754e28ff",
        },
        "modelEra": {
            "experimentId": "ASP_MLB_V18_CONDITIONAL_VIRTUAL_20260912_V1",
            "effectiveAtUtc": "2026-09-12T04:00:00Z",
            "minimumAdjustedEdge": 0.02,
            "recommendationPolicyVersion": "AISP_PRACTICAL_PREGAME_POLICY_V2_1",
            "stakingPolicyVersion": "AISP_QUARTER_KELLY_EVIDENCE_V2",
            "gen1FallbackAllowedInV18": False,
            "positionBackfillAllowed": False,
        },
    }
    payload.update(overrides)
    return payload


def _write(tmp_path: Path, payload: dict) -> tuple[Path, str]:
    blob = json.dumps(payload).encode("utf-8")
    path = tmp_path / "champion-handoff.json"
    path.write_bytes(blob)
    return path, hashlib.sha256(blob).hexdigest()


# -- who may grant authority -------------------------------------------------

def test_bytes_that_do_not_match_the_expected_digest_are_never_parsed(tmp_path):
    path, digest = _write(tmp_path, _supplement())
    wrong = "0" * 64
    with pytest.raises(ChampionImportError) as error:
        read_supplement(path, expected_sha256=wrong)
    assert WRONG_DIGEST in str(error.value)
    assert digest in str(error.value)


def test_a_supplement_of_another_schema_is_refused(session, settings, tmp_path):
    path, digest = _write(tmp_path, _supplement(schemaVersion="SOMETHING_ELSE_V9"))
    with pytest.raises(ChampionImportError) as error:
        V18ChampionService(session, settings).import_supplement(
            path, expected_sha256=digest, now=BEFORE)
    assert WRONG_SCHEMA in str(error.value)


def test_an_unauthorized_supplement_is_refused(session, settings, tmp_path):
    payload = _supplement()
    payload["activation"]["authorized"] = False
    path, digest = _write(tmp_path, payload)
    with pytest.raises(ChampionImportError) as error:
        V18ChampionService(session, settings).import_supplement(
            path, expected_sha256=digest, now=BEFORE)
    assert NOT_AUTHORIZED in str(error.value)


@pytest.mark.parametrize(("field", "expected"), [
    ("executionAllowedInAISP", CLAIMS_EXECUTION),
    ("realMoneyExecutionAllowed", CLAIMS_REAL_MONEY),
])
def test_a_supplement_claiming_execution_or_money_is_refused(
    session, settings, tmp_path, field, expected,
):
    """Refused outright, not imported-with-the-claim-dropped.

    Silently ignoring the claim would import a document we demonstrably do not
    understand, and leave a row implying we agreed with all of it.
    """

    path, digest = _write(tmp_path, _supplement(**{field: True}))
    with pytest.raises(ChampionImportError) as error:
        V18ChampionService(session, settings).import_supplement(
            path, expected_sha256=digest, now=BEFORE)
    assert expected in str(error.value)


# -- when an era may begin ---------------------------------------------------

def test_a_boundary_already_in_the_past_is_refused_not_backdated(session, settings, tmp_path):
    path, digest = _write(tmp_path, _supplement())
    with pytest.raises(ChampionImportError) as error:
        V18ChampionService(session, settings).import_supplement(
            path, expected_sha256=digest, now=BOUNDARY + timedelta(seconds=1))
    assert BOUNDARY_PASSED in str(error.value)


def test_import_is_idempotent_on_the_handoff_digest(session, settings, tmp_path):
    path, digest = _write(tmp_path, _supplement())
    service = V18ChampionService(session, settings)
    first = service.import_supplement(path, expected_sha256=digest, now=BEFORE)
    session.flush()
    second = service.import_supplement(path, expected_sha256=digest, now=BEFORE)
    assert first["imported"] is True
    assert second["imported"] is False and second["reused"] is True
    assert first["authority_id"] == second["authority_id"]


def test_armed_before_the_boundary_and_capital_is_not_captured(session, settings, tmp_path):
    path, digest = _write(tmp_path, _supplement())
    service = V18ChampionService(session, settings)
    service.import_supplement(path, expected_sha256=digest, now=BEFORE)
    session.flush()
    status = service.status(now=BEFORE)
    assert status["state"] == "ARMED"
    assert status["starts_in_seconds"] > 0
    captured = service.capture_boundary(now=BEFORE)
    assert captured["captured"] is False
    # The exact reason matters: "not yet" must never read as "failed".
    assert captured["reason"] in {"BOUNDARY_HAS_NOT_ARRIVED", "NOTHING_ARMED"}


def test_activation_never_clears_the_historical_review(session, settings, tmp_path):
    path, digest = _write(tmp_path, _supplement())
    service = V18ChampionService(session, settings)
    service.import_supplement(path, expected_sha256=digest, now=BEFORE)
    session.flush()
    status = service.status(now=BOUNDARY + timedelta(hours=1))
    assert status["state"] == "ACTIVE"
    assert status["historical_validation_under_review"] is True
    assert status["executes_in_aisp"] is False
    assert status["real_money"] is False
    assert status["gen1_fallback_allowed"] is False
    assert status["position_backfill_allowed"] is False
    assert status["minimum_adjusted_edge"] == 0.02


# -- what a V18 forecast may and may not do ----------------------------------

class _Row:
    """The fields the view reads off a persisted ``ShadowForecast``."""

    def __init__(self, **kwargs):
        defaults = dict(
            id="11111111-1111-1111-1111-111111111111",
            engine_id="MLB_V18_WHOLE_GAME",
            engine_version="mlb-v18-whole-game-a6e7e99122f1b60701a4728b",
            forecast_id="mlb-v18-daily-abc",
            event_id="22222222-2222-2222-2222-222222222222",
            source_event_id="824631",
            full_home_win_probability=0.6318,
            full_away_win_probability=0.3682,
            full_expected_home_score=4.6,
            full_expected_away_score=3.9,
            readiness_score=1.0,
            readiness_detail={},
            uncertainty=None,
            distributions={},
            is_ready=True,
            source_cutoff_utc=datetime(2026, 9, 11, 16, 34, tzinfo=UTC),
            available_as_of_utc=datetime(2026, 9, 11, 16, 43, tzinfo=UTC),
            scheduled_start_utc=datetime(2026, 9, 11, 18, 20, tzinfo=UTC),
            model_artifact_sha256="a" * 64,
            model_manifest_sha256="m" * 64,
            feature_vector_sha256="v" * 64,
            simulator_id="sim", simulator_version="1", simulator_seed=7,
        )
        defaults.update(kwargs)
        for key, value in defaults.items():
            setattr(self, key, value)


def test_the_view_keeps_publication_and_evidence_cutoff_apart():
    """The two clocks are the reason the cutoff gate can work at all."""

    view = V18ForecastView(_Row())
    assert view.generated_at == datetime(2026, 9, 11, 16, 43, tzinfo=UTC)
    assert view.available_as_of == datetime(2026, 9, 11, 16, 34, tzinfo=UTC)
    assert view.generated_at != view.available_as_of


def test_the_view_never_claims_a_gen1_forecast_row():
    view = V18ForecastView(_Row())
    assert view.id is None
    assert str(view.shadow_forecast_id) == "11111111-1111-1111-1111-111111111111"


def test_the_view_reports_lineage_and_that_nothing_was_executed():
    lineage = V18ForecastView(_Row()).lineage()
    assert lineage["model_artifact_sha256"] == "a" * 64
    assert lineage["engine_id"] == "MLB_V18_WHOLE_GAME"
    assert lineage["source_event_id"] == "824631"
    assert lineage["executed_in_aisp"] is False


def test_a_structured_uncertainty_block_uses_the_imported_scalar_not_an_average():
    # The importer stores the producer's homeWinProbabilityStandardError as
    # monteCarloStandardError (shadow_import.mlb_v18_daily); that is the scalar.
    view = V18ForecastView(_Row(uncertainty={
        "simulationCount": 5000,
        "monteCarloStandardError": 0.006998,
        "monteCarlo95HalfWidth": 0.0137,
        "somethingElseEntirely": 99.0,
    }))
    assert view.uncertainty == pytest.approx(0.006998)
    assert view.uncertainty_basis == "MONTE_CARLO_SAMPLING_STANDARD_ERROR"


def test_the_never_written_field_name_is_not_read_and_missing_stays_missing():
    view = V18ForecastView(_Row(uncertainty={"fullGameWinProbabilityStandardError": 0.031}))
    assert view.uncertainty is None
    assert view.uncertainty_basis is None


# -- no forecast means no decision, never a fallback -------------------------

class _FakeSession:
    def __init__(self, row=None):
        self._row = row

    def scalar(self, _statement):
        return self._row

    def get(self, _entity, _ident):
        # No canonical game row: the resolver's league gate has nothing to refuse.
        return None


@pytest.mark.parametrize(("row", "expected"), [
    (None, NO_V18_FORECAST),
    (_Row(is_ready=False), NOT_READY),
    (_Row(source_cutoff_utc=datetime(2026, 9, 11, 18, 30, tzinfo=UTC)), EVIDENCE_AFTER_CUTOFF),
    (_Row(available_as_of_utc=datetime(2026, 9, 11, 18, 30, tzinfo=UTC)), PUBLISHED_AFTER_CUTOFF),
])
def test_every_unusable_forecast_produces_an_exact_reason(row, expected):
    """Each failure is named, because the era forbids a silent Gen1 fallback.

    A bare ``None`` here would be indistinguishable from "no producer ran",
    and the operator could not tell a missing forecast from a late one.
    """

    view, reason = resolve_for_decision(
        _FakeSession(row), "22222222-2222-2222-2222-222222222222",
        cutoff=datetime(2026, 9, 11, 18, 5, tzinfo=UTC),
    )
    assert view is None
    assert reason == expected


def test_a_forecast_inside_both_clocks_is_usable():
    view, reason = resolve_for_decision(
        _FakeSession(_Row()), "22222222-2222-2222-2222-222222222222",
        cutoff=datetime(2026, 9, 11, 18, 5, tzinfo=UTC),
    )
    assert reason is None
    assert view is not None and view.home_win_probability == pytest.approx(0.6318)


def test_a_forecast_published_exactly_at_the_cutoff_is_still_usable():
    """The boundary is inclusive, and a test pins which side it falls on."""

    cutoff = datetime(2026, 9, 11, 16, 43, tzinfo=UTC)
    view, reason = resolve_for_decision(
        _FakeSession(_Row()), "22222222-2222-2222-2222-222222222222", cutoff=cutoff,
    )
    assert reason is None and view is not None
