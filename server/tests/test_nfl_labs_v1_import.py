"""Producer V1 byte verification, independent drive identity and append-only imports."""
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.forecast_exchange.nfl_labs_v1 import (
    CONTRACT, CURRENT, DRIVE, DRIVE_ENGINE, MANIFEST, M1, ROOT_NAME, SCORE,
    register_labs_current, verify_labs_current,
)
from app.forecast_exchange.supplements import SupplementImportError
from app.gen2.exchange.canonical_json import canonical_json_bytes, canonical_sha256
from app.models import ResearchSupplement, ShadowForecast, ShadowForecastBundle
from app.services.nfl_read_service import NflReadService
from tests.test_nfl_labs_product import EVENT, FORECAST_ID, _register_forecast

GOV = {"status": "RESEARCH_SHADOW", "promotionStatus": "NOT_PROMOTED",
       "executionAllowedInAISP": False, "wageringAuthority": False}


def _artifact(*, drive=False, pace=False):
    doc = {
        **GOV, "schemaVersion": DRIVE if drive else SCORE, "sport": "NFL",
        "eventId": EVENT, "forecastId": "drive-pace" if pace else "drive-base" if drive else FORECAST_ID,
        "homeTeam": {"teamId": "SEA", "sourceTeamId": "SEA"}, "awayTeam": {"teamId": "NE", "sourceTeamId": "NE"},
        "scheduledStartUtc": "2026-09-10T00:20:00Z", "forecastCreatedUtc": "2026-09-09T00:00:00Z",
        "sourceCutoffUtc": "2026-09-08T18:00:00Z", "publishedAtUtc": "2026-09-09T01:00:00Z",
        "model": {"engineId": DRIVE_ENGINE if drive else M1, "modelBinarySha256": "a"*64},
        "sourceForecast": {"forecastId": FORECAST_ID, "sha256": "b"*64},
        "drawCount": 2, "seed": 7,
        "jointScoreDistribution": [{"homeScore": 7, "awayScore": 0, "probability": .5},
                                   {"homeScore": 0, "awayScore": 3, "probability": .5}],
        "scoreMarginDistribution": [{"homeMinusAway": 7, "probability": .5}, {"homeMinusAway": -3, "probability": .5}],
        "totalScoreDistribution": [{"totalScore": 7, "probability": .5}, {"totalScore": 3, "probability": .5}],
        "scoreSummary": {"homeWinProbability": .5, "awayWinProbability": .5, "tieProbability": 0,
                         "expectedHomeScore": 3.5, "expectedAwayScore": 1.5, "expectedTotal": 5,
                         "expectedMarginHomeMinusAway": 2},
        "representativeFinals": [{"homeScore": 7, "awayScore": 0}, {"homeScore": 0, "awayScore": 3}],
        "scenario": {"scenarioId": "PACE_DURATION_0.95" if pace else "BASE", "paceDurationMultiplier": .95 if pace else 1.0,
                     "modelInputChange": pace, "interpretation": "Controlled duration sensitivity"},
    }
    if drive:
        doc.update(relatedM1ForecastId=FORECAST_ID, explainsM1Forecast=False,
                   populationFinals=deepcopy(doc["representativeFinals"]))
        doc["simulatedDrives"] = []
        for index, (h, a, side, outcome) in enumerate(((7, 0, "HOME", "TD"), (0, 3, "AWAY", "FG"))):
            doc["simulatedDrives"].append({"simulationIndex": index, "label": side, "homeScore": h, "awayScore": a,
                "drives": [{"driveNumber": 1, "possession": side, "offenseTeam": "SEA" if side == "HOME" else "NE",
                    "period": "REGULATION_HALF_1", "periodSecondsBefore": 1800, "periodSecondsAfter": 1700,
                    "durationSeconds": 100, "startYardline100": 65, "endYardline100": 1 if h else 20,
                    "outcome": outcome, "homePointsAdded": h, "awayPointsAdded": a,
                    "homeScoreAfter": h, "awayScoreAfter": a}]})
    return doc


def _publish(root: Path, docs):
    contracts = root / "contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    contract_file = contracts / f"{CONTRACT}.json"
    contract_file.write_bytes(canonical_json_bytes({**GOV, "schemaVersion": CONTRACT}))

    def reference(path):
        raw = path.read_bytes()
        import hashlib
        return {"path": str(path.resolve()), "byteCount": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

    entries = []
    for doc in docs:
        address = canonical_sha256(doc)
        directory = root / ROOT_NAME / "sha256" / address
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "artifact.json"
        path.write_bytes(canonical_json_bytes(doc))
        artifact_ref = reference(path)
        manifest = directory / "manifest.json"
        manifest.write_bytes(canonical_json_bytes({**GOV, "schemaVersion": MANIFEST,
            "artifactSchemaVersion": doc["schemaVersion"], "files": [artifact_ref]}))
        entries.append({"artifact": artifact_ref, "manifest": reference(manifest),
                        "artifactSchemaVersion": doc["schemaVersion"], "eventId": doc["eventId"],
                        "forecastId": doc["forecastId"], "modelId": doc["model"]["engineId"]})
    (root / ROOT_NAME / "current.json").write_bytes(canonical_json_bytes({**GOV,
        "schemaVersion": CURRENT, "coordinationContract": reference(contract_file), "artifacts": entries,
        "forecastCount": sum(d["schemaVersion"] == SCORE for d in docs),
        "driveResearchArtifactCount": sum(d["schemaVersion"] == DRIVE for d in docs)}))
    return entries


def test_v1_import_keeps_independent_engine_and_reuses_all_rows(session, settings, tmp_path):
    parent = _register_forecast(session)
    docs = [_artifact(), _artifact(drive=True), _artifact(drive=True, pace=True)]
    _publish(tmp_path, docs)
    verified = verify_labs_current(tmp_path)
    dry = register_labs_current(session, verified, dry_run=True)
    assert len(dry) == 3 and session.scalar(select(func.count()).select_from(ResearchSupplement)) == 0
    first = register_labs_current(session, verified)
    assert all(not r["idempotent_reuse"] for r in first)
    assert all(r["idempotent_reuse"] for r in register_labs_current(session, verified))
    assert session.scalar(select(func.count()).select_from(ShadowForecast)) == 1
    assert session.scalar(select(func.count()).select_from(ShadowForecastBundle)) == 1
    assert session.scalar(select(func.count()).select_from(ResearchSupplement)) == 3
    rows = list(session.scalars(select(ResearchSupplement).where(ResearchSupplement.engine_id == DRIVE_ENGINE)))
    assert len(rows) == 2 and all(r.shadow_forecast_id is None for r in rows)
    assert all(r.evidence["related_m1_forecast_id"] == parent.forecast_id for r in rows)
    read = NflReadService(session, settings)._drive_supplement(rows[0])
    assert read["paths"] == docs[1]["simulatedDrives"]
    assert read["scenario"]["scenarioId"] == "BASE"
    assert read["explorer"]["expected"]["total"] == 5
    assert read["explains_m1_forecast"] is False


def test_v1_rejects_tampered_bytes_before_parsing(tmp_path):
    entries = _publish(tmp_path, [_artifact()])
    path = Path(entries[0]["artifact"]["path"])
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(SupplementImportError, match="do not hash"):
        verify_labs_current(tmp_path)


@pytest.mark.parametrize("mutation,match", [
    (lambda d: d["simulatedDrives"][0]["drives"][0].update(homeScoreAfter=6), "score accounting"),
    (lambda d: d["simulatedDrives"][0]["drives"][0].update(endYardline100=101), "yard line"),
    (lambda d: d["simulatedDrives"][0]["drives"][0].update(periodSecondsAfter=1600), "clock"),
    (lambda d: d["scoreSummary"].update(homeWinProbability=.7), "not the joint"),
    (lambda d: d.update(explainsM1Forecast=True), "independent engine"),
    (lambda d: d["scenario"].update(paceDurationMultiplier=.5), "scenario"),
    (lambda d: d.update(wageringAuthority=True), "authority"),
])
def test_v1_rejects_self_rehashed_inconsistent_evidence(tmp_path, mutation, match):
    doc = _artifact(drive=True)
    mutation(doc)
    _publish(tmp_path, [doc])
    with pytest.raises(SupplementImportError, match=match):
        verify_labs_current(tmp_path)


def test_v1_validates_every_binding_before_inserting(session, tmp_path):
    _register_forecast(session)
    bad = _artifact(drive=True)
    bad["relatedM1ForecastId"] = "unknown-parent"
    _publish(tmp_path, [_artifact(), bad])
    with pytest.raises(SupplementImportError, match="not registered"):
        register_labs_current(session, verify_labs_current(tmp_path))
    assert session.scalar(select(func.count()).select_from(ResearchSupplement)) == 0


def test_consumer_discovers_v1_family_and_reuses_it(session, tmp_path):
    from app.jobs.model_exchange_consumer import ConsumeOutcome, _consume_nfl_labs_v1
    _register_forecast(session)
    _publish(tmp_path, [_artifact(), _artifact(drive=True), _artifact(drive=True, pace=True)])
    first = ConsumeOutcome(root=str(tmp_path), dry_run=False)
    _consume_nfl_labs_v1(session, tmp_path, first, dry_run=False)
    assert len(first.supplements_accepted) == 3 and not first.supplements_rejected
    second = ConsumeOutcome(root=str(tmp_path), dry_run=False)
    _consume_nfl_labs_v1(session, tmp_path, second, dry_run=False)
    assert len(second.supplements_skipped) == 3 and not second.supplements_accepted
    assert session.scalar(select(func.count()).select_from(ShadowForecast)) == 1
