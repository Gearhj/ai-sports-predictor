"""Joining the published explanation catalogue without letting it drift.

The catalogue is references, not forecasts, so the whole risk sits in the join:
attaching September 12's observed events to a later forecast revision would
credit one forecast with another game's result. These pin that the join is by
exact identity, that unverifiable bytes mean no evidence rather than partial
evidence, and that the three kinds of statement stay labelled and separate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.explain.catalogue import (
    EXCLUDED_FROM_INGESTION,
    CatalogueError,
    build_evidence,
    evidence_for,
    load_catalogue,
    verify_package,
)
from app.explain.outcome import explain_outcome

GAME_PK = "823496"
FORECAST = "mlb-v18-daily-d8bc8570b0e62c98150a4629dc075941"
OTHER_FORECAST = "mlb-v18-daily-0000000000000000000000000000000000"


def write(root: Path, name: str, payload: dict) -> dict:
    raw = json.dumps(payload, indent=2).encode("utf-8")
    (root / name).write_bytes(raw)
    return {"relativePath": name, "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw)}


def build_package(tmp_path: Path, *, forecast_id: str = FORECAST,
                  population_forecast: str | None = None) -> Path:
    """A structurally real package: index, joins, and the artifacts they name."""

    root = tmp_path / "pkg"
    root.mkdir(parents=True, exist_ok=True)
    external = tmp_path / "external"
    external.mkdir(exist_ok=True)

    table = {"rows": [{
        "actual": {"awayRuns": 12, "homeRuns": 2, "totalRuns": 14},
        "paDiagnostic": {"observedBySide": {
            "AWAY": {"completedPA": 42,
                     "officialEventCounts": {"homeRuns": 6, "baseOnBalls": 2,
                                             "strikeOuts": 7}},
            "HOME": {"completedPA": 34,
                     "officialEventCounts": {"homeRuns": 1, "baseOnBalls": 1,
                                             "strikeOuts": 6}},
        }},
        "exposureDiagnostic": {"actual": {
            "awayTeam": "NYM", "homeTeam": "NYY",
            "sides": {
                "AWAY": {"starterBF": 26, "reliefBF": 8, "maximumInningRuns": 5},
                "HOME": {"starterBF": 22, "reliefBF": 20, "maximumInningRuns": 1},
            },
        }},
    }]}
    comparison = {"rows": [{
        "originalForecastId": population_forecast or forecast_id,
        "teams": {"AWAY": {"team": "NYM", "classes": {"HOME_RUN": {"sampledCount": {
            "actual": 6, "mean": 1.175, "inclusiveUpperTail": 0.0024, "draws": 5000}}}}},
    }]}

    table_raw = json.dumps(table, indent=2).encode("utf-8")
    (external / "table.json").write_bytes(table_raw)
    comparison_raw = json.dumps(comparison, indent=2).encode("utf-8")
    (external / "comparison.json").write_bytes(comparison_raw)

    joins = {
        "schemaVersion": "MLB_EXPLANATION_EVIDENCE_JOINS_V1",
        "caveats": ["Retrospective reproduction."],
        "artifacts": {
            "diagnosticTable": {
                "path": str(external / "table.json"),
                "sha256": hashlib.sha256(table_raw).hexdigest(),
                "bytes": len(table_raw)},
            "populationComparison": {
                "path": str(external / "comparison.json"),
                "sha256": hashlib.sha256(comparison_raw).hexdigest(),
                "bytes": len(comparison_raw)},
        },
        "fieldLocations": {"diagnosticTableSuffixes": {
            "actualScores": "/actual",
            "observedEventCountsAndRates": "/paDiagnostic/observedBySide",
            "observedStarterReliefAndInnings": "/exposureDiagnostic/actual",
        }},
        "games": [{
            "sourceGameId": GAME_PK,
            "canonicalGameId": "c5a532dc-94a2-4e4f-84d8-0bdb001ad1f3",
            "forecastId": forecast_id,
            "matchup": "NYM at NYY",
            "diagnosticTableRowPointer": "/rows/0",
            "governedFinal": {"revision": 2},
            "populationComparison": {
                "artifact": "populationComparison", "pointer": "/rows/0",
                "classification": "RETROSPECTIVE_REPRODUCTION_AFTER_OBSERVED_RESULTS",
                "simulationCount": 5000,
            },
            "availability": "SCORE_TOTAL_PA_OBSERVED_AND_RETROSPECTIVE_POPULATION",
        }],
    }
    files = [write(root, "explanation-joins.json", joins)]
    (root / "PROTECTED_INSPECTION_CLOSEOUT.md").write_text("redacted", encoding="utf-8")
    protected = (root / "PROTECTED_INSPECTION_CLOSEOUT.md").read_bytes()
    files.append({"relativePath": "PROTECTED_INSPECTION_CLOSEOUT.md",
                  "sha256": hashlib.sha256(protected).hexdigest(),
                  "bytes": len(protected)})

    index = {
        "schemaVersion": "MLB_RESEARCH_CLOSEOUT_EXPLANATION_HANDOFF_V1",
        "artifactKind": "ADDITIVE_EVIDENCE_REFERENCES_NOT_FORECASTS",
        "files": files,
    }
    index_path = root / "index.json"
    index_path.write_bytes(json.dumps(index, indent=2).encode("utf-8"))
    return index_path


class TestVerification:
    def test_accepts_a_well_formed_package(self, tmp_path):
        package = verify_package(build_package(tmp_path), expected_sha256=None)
        assert package["joins"]["schemaVersion"] == "MLB_EXPLANATION_EVIDENCE_JOINS_V1"
        assert "diagnosticTable" in package["artifacts"]

    def test_checks_the_declared_index_digest(self, tmp_path):
        with pytest.raises(CatalogueError, match="hashes to"):
            verify_package(build_package(tmp_path), expected_sha256="0" * 64)

    def test_a_changed_member_byte_is_refused(self, tmp_path):
        path = build_package(tmp_path)
        joins = path.parent / "explanation-joins.json"
        joins.write_bytes(joins.read_bytes() + b" ")
        with pytest.raises(CatalogueError, match="does not match its declared bytes"):
            verify_package(path, expected_sha256=None)

    def test_a_changed_referenced_artifact_is_refused(self, tmp_path):
        """The pointers are resolved only after the bytes check, never before."""

        path = build_package(tmp_path)
        joins = json.loads((path.parent / "explanation-joins.json").read_bytes())
        table = Path(joins["artifacts"]["diagnosticTable"]["path"])
        table.write_bytes(table.read_bytes() + b" ")
        with pytest.raises(CatalogueError, match="referenced artifact"):
            verify_package(path, expected_sha256=None)

    def test_the_protected_closeout_is_named_as_excluded(self, tmp_path):
        package = verify_package(build_package(tmp_path), expected_sha256=None)
        assert "PROTECTED_INSPECTION_CLOSEOUT.md" in package["excluded_from_ingestion"]
        assert "PROTECTED_INSPECTION_CLOSEOUT.md" in EXCLUDED_FROM_INGESTION


class TestTheJoinRule:
    def test_evidence_resolves_for_the_exact_game_and_forecast(self, tmp_path):
        package = verify_package(build_package(tmp_path), expected_sha256=None)
        evidence = build_evidence(package)
        found = evidence[(GAME_PK, FORECAST)]
        assert found.observed_events["AWAY"]["officialEventCounts"]["homeRuns"] == 6
        assert found.observed_exposure["sides"]["AWAY"]["starterBF"] == 26
        assert found.has_population_counters

    def test_a_different_forecast_reaches_nothing(self, tmp_path):
        """No nearest match and no latest-forecast fallback."""

        package = verify_package(build_package(tmp_path), expected_sha256=None)
        evidence = build_evidence(package)
        assert (GAME_PK, OTHER_FORECAST) not in evidence

    def test_a_population_row_naming_another_forecast_is_refused(self, tmp_path):
        """The rule enforced, not trusted."""

        path = build_package(tmp_path, population_forecast=OTHER_FORECAST)
        package = verify_package(path, expected_sha256=None)
        with pytest.raises(CatalogueError, match="names forecast"):
            build_evidence(package)

    def test_an_absent_catalogue_is_not_an_error(self, tmp_path):
        load_catalogue.cache_clear()
        assert load_catalogue(str(tmp_path / "nowhere" / "index.json")) is None
        assert evidence_for(source_game_id=GAME_PK, forecast_id=FORECAST,
                            index_path=str(tmp_path / "nowhere" / "index.json")) is None

    def test_a_missing_identity_reaches_nothing(self, tmp_path):
        assert evidence_for(source_game_id=None, forecast_id=FORECAST) is None
        assert evidence_for(source_game_id=GAME_PK, forecast_id=None) is None


class TestStatementKindsStayApart:
    def decision(self):
        from types import SimpleNamespace
        return SimpleNamespace(market="MONEYLINE", selection="NYY ML", line=None,
                               evidence={})

    def position(self):
        from types import SimpleNamespace
        return SimpleNamespace(id="p", stake=1.0,
                               result=SimpleNamespace(value="loss"))

    EVIDENCE = {
        "source": "CATALOGUE",
        "away_events": {"team": "NYM", "completed_pa": 42,
                        "counts": {"homeRuns": 6, "baseOnBalls": 2}},
        "exposure": {"away": {"team": "NYM", "starter_bf": 26, "relief_bf": 8}},
        "largest_inning": {"runs": 8, "team": "ATL"},
        "population": {
            "classification": "RETROSPECTIVE_REPRODUCTION_AFTER_OBSERVED_RESULTS",
            "simulation_count": 5000,
            "highlights": [{"side": "AWAY", "label": "NYM home runs", "actual": 6,
                            "mean": 1.175, "tail_pct": 0.24, "draws": 5000}],
        },
    }

    def test_observed_events_are_labelled_as_observations(self):
        payload = explain_outcome(
            decision=self.decision(), position=self.position(), settlement=None,
            final={"home_score": 2, "away_score": 12},
            forecast_distributions=None, matchup={"home": "NYY", "away": "NYM"},
            event_evidence=self.EVIDENCE)
        events = next(b for b in payload["bullets"] if b["key"] == "events")
        assert events["statement_kind"] == "OBSERVED_CONTRIBUTOR"
        assert "NYM recorded 6 home runs" in events["text"]
        assert "starter faced 26 batters" in events["text"]
        assert "largest inning contained 8 runs" in events["text"]

    def test_the_population_location_is_a_possible_explanation_not_a_cause(self):
        payload = explain_outcome(
            decision=self.decision(), position=self.position(), settlement=None,
            final={"home_score": 2, "away_score": 12},
            forecast_distributions=None, matchup={"home": "NYY", "away": "NYM"},
            event_evidence=self.EVIDENCE)
        population = next(b for b in payload["bullets"] if b["key"] == "population")
        assert population["statement_kind"] == "POSSIBLE_EXPLANATION"
        assert "1.18" in population["text"]
        assert "0.24%" in population["text"]
        # And the limits travel with it.
        assert "does not establish calibration, causation, or a model defect" \
            in population["limits"]
        assert "retrospective" in population["limits"].lower()

    def test_no_model_defect_is_ever_claimed(self):
        payload = explain_outcome(
            decision=self.decision(), position=self.position(), settlement=None,
            final={"home_score": 2, "away_score": 12},
            forecast_distributions=None, matchup={"home": "NYY", "away": "NYM"},
            event_evidence=self.EVIDENCE)
        joined = " ".join(b["text"] for b in payload["bullets"]).lower()
        for claim in ("caused", "defect", "miscalibrat", "broken", "because the model"):
            assert claim not in joined
        assert "Not claimed anywhere here" in \
            payload["statement_kinds"]["ESTABLISHED_MODEL_DEFECT"]

    def test_a_comparison_with_nothing_unusual_still_reports_itself(self):
        """Otherwise "available but unremarkable" reads as "unavailable"."""

        evidence = dict(self.EVIDENCE)
        evidence["population"] = {**self.EVIDENCE["population"], "highlights": []}
        payload = explain_outcome(
            decision=self.decision(), position=self.position(), settlement=None,
            final={"home_score": 2, "away_score": 12},
            forecast_distributions=None, matchup={"home": "NYY", "away": "NYM"},
            event_evidence=evidence)
        assert payload["population_comparison_available"] is True
        population = next(b for b in payload["bullets"] if b["key"] == "population")
        assert "no observed event class fell in an unusual tail" in population["text"]

    def test_a_game_without_population_counters_gets_events_only(self):
        evidence = {k: v for k, v in self.EVIDENCE.items() if k != "population"}
        payload = explain_outcome(
            decision=self.decision(), position=self.position(), settlement=None,
            final={"home_score": 2, "away_score": 12},
            forecast_distributions=None, matchup={"home": "NYY", "away": "NYM"},
            event_evidence=evidence)
        assert payload["event_evidence_available"] is True
        assert payload["population_comparison_available"] is False
        assert not any(b["key"] == "population" for b in payload["bullets"])

    def test_no_catalogue_evidence_keeps_the_unavailable_sentence(self):
        payload = explain_outcome(
            decision=self.decision(), position=self.position(), settlement=None,
            final={"home_score": 2, "away_score": 12},
            forecast_distributions=None, matchup={"home": "NYY", "away": "NYM"},
            event_evidence=None)
        events = next(b for b in payload["bullets"] if b["key"] == "events")
        assert events["statement_kind"] == "UNAVAILABLE"
        assert payload["event_evidence_available"] is False
