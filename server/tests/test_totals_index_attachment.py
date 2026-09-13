"""Verifying a published totals index and binding it to imported baselines.

The package under test is built here rather than read from the exchange, so the
failure modes can be produced deliberately. The reference numbers, though, are
the real ones from the published CIN-MIL 823734 row: mean 7.9656, Over 8 at
0.4082 / 0.0764 / 0.5154, Over 8.5 at 0.4082 / 0 / 0.5918.

What these pin is that attachment REFUSES rather than repairs. Every layer --
delivered bytes, contract, identity, the PMF itself, population shape -- fails
closed, because an attachment that quietly tolerated a mismatch would stamp
"verified" on a distribution nobody had actually checked.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Game,
    GameStatus,
    GameTeam,
    League,
    Season,
    ShadowForecast,
    ShadowForecastBundle,
    Sport,
    Team,
    TotalsIndexAttachment,
)
from app.totals.index_attachment import (
    CONSUMER_FIELD,
    PRODUCER_POINTER,
    TotalsIndexError,
    acknowledgement,
    attach,
    attachment_map,
    verify_package,
)
from app.totals.pricing import TotalsDistribution, price_line

START = datetime(2026, 9, 13, 18, 10, tzinfo=UTC)
GAME_PK = "823734"
OBJECT_SHA = "05f86403fb40fc60600d479b94821a46e17b91471afbf6c8ac3c48e18ceeaeaa"
FORECAST_ID = f"mlb-v18-daily-{OBJECT_SHA}"

#: The real CIN-MIL PMF, as bins. Chosen so the published reference values fall
#: out exactly: P(T>8)=0.4082, P(T=8)=0.0764, P(T<8)=0.5154, mean 7.9656.
PMF = {
    5: 0.1136, 7: 0.4018,
    8: 0.0764,
    9: 0.3082, 12: 0.1000,
}


def _mean(pmf: dict[int, float]) -> float:
    return sum(k * v for k, v in pmf.items())


def build_package(
    tmp_path: Path, *, pmf: dict[int, float] | None = None, **overrides,
) -> Path:
    """A minimal but structurally real package: index, manifest, forecast file."""

    mass = pmf if pmf is not None else PMF
    root = tmp_path / "pkg"
    (root / "forecasts" / OBJECT_SHA).mkdir(parents=True, exist_ok=True)

    forecast = {
        "distributions": {
            "totalRuns": [
                {"value": value, "probability": probability}
                for value, probability in sorted(mass.items())
            ],
        },
    }
    forecast_bytes = json.dumps(forecast, indent=2).encode("utf-8")
    forecast_path = root / "forecasts" / OBJECT_SHA / "forecast.json"
    forecast_path.write_bytes(forecast_bytes)
    forecast_sha = hashlib.sha256(forecast_bytes).hexdigest()

    row = {
        "baselineForecastId": FORECAST_ID,
        "baselineObjectSha256": OBJECT_SHA,
        "canonicalGameId": None,
        "mlbamGamePk": int(GAME_PK),
        "gameDate": "2026-09-13",
        "engineId": "MLB_V18_WHOLE_GAME",
        "engineVersion": "mlb-v18-whole-game-a6e7e99122f1b60701a4728b",
        "simulatorId": "V18_WHOLE_GAME_MONTE_CARLO",
        "seed": 20260907,
        "originalSimulationCount": 5000,
        "validOriginalDrawsUsed": 5000,
        "excludedDrawCount": 0,
        "extraInningsIncluded": True,
        "scoreLimited": False,
        "expectations": {"totalRuns": _mean(mass)},
        "distribution": {
            "relativePath": f"forecasts/{OBJECT_SHA}/forecast.json",
            "sha256": forecast_sha,
            "bytes": len(forecast_bytes),
            "jsonPointer": PRODUCER_POINTER,
            "probabilityUnits": "FRACTION_0_TO_1",
            "denominator": 5000,
            "tailTruncated": False,
            "totalRunValues": "INTEGER_RUNS",
            "completeOriginalPopulation": True,
        },
    }
    row.update(overrides.pop("row", {}))

    index = {
        "schemaVersion": "MLB_V18_TOTALS_DISTRIBUTION_INDEX_V1",
        "probabilityUnits": "FRACTION_0_TO_1",
        "newForecastGenerated": False,
        "newAuthorityGranted": False,
        "distinctGameCount": 1,
        "forecastVersionCount": 1,
        "coverageByDate": {"2026-09-13": 1},
        "sourceInventoryAtUtc": "2026-09-13T17:14:29.907599+00:00",
        "exportCreatedAtUtc": "2026-09-13T17:19:18.869945+00:00",
        "forecasts": [row],
    }
    index.update(overrides.pop("index", {}))
    index_bytes = json.dumps(index, indent=2).encode("utf-8")
    index_path = root / "index.json"
    index_path.write_bytes(index_bytes)

    manifest = {
        "schemaVersion": "MLB_V18_TOTALS_PACKAGE_MANIFEST_V1",
        "createdAtUtc": "2026-09-13T17:19:27.740611+00:00",
        "indexSha256": hashlib.sha256(index_bytes).hexdigest(),
        "files": [{
            "relativePath": f"forecasts/{OBJECT_SHA}/forecast.json",
            "sha256": forecast_sha,
            "bytes": len(forecast_bytes),
        }],
    }
    manifest.update(overrides.pop("manifest", {}))
    (root / "manifest.json").write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))
    (root / "publication-receipt.json").write_bytes(
        json.dumps({"publishedAtUtc": "2026-09-13T17:21:11.715942+00:00"}).encode("utf-8"))
    return index_path


@pytest.fixture
def world(session: Session):
    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=START - timedelta(days=160),
                    ends_at=START + timedelta(days=30))
    home = Team(league_id=league.id, name="Milwaukee Brewers", abbreviation="MIL")
    away = Team(league_id=league.id, name="Cincinnati Reds", abbreviation="CIN")
    session.add_all([season, home, away])
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=START,
                status=GameStatus.SCHEDULED, source_mode="LIVE")
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    bundle = ShadowForecastBundle(
        bundle_sha256="a" * 64, bundle_id="b", logical_uri="test://b",
        source_fixture_id="f", slate_date=START.date(), league="MLB", sport="MLB",
        status="RESEARCH_SHADOW", research_shadow_only=True,
        generated_at=START - timedelta(hours=6),
        forecast_count=1, quote_count=0, candidate_count=0,
    )
    session.add(bundle)
    session.flush()
    return {"session": session, "game": game, "bundle": bundle}


def seed_forecast(world, *, pmf: dict[int, float] | None = None, **overrides):
    mass = pmf if pmf is not None else PMF
    session = world["session"]
    values = {
        "bundle_id": world["bundle"].id,
        "forecast_id": FORECAST_ID,
        "engine_id": "MLB_V18_WHOLE_GAME",
        "engine_version": "mlb-v18-whole-game-a6e7e99122f1b60701a4728b",
        "model_artifact_sha256": "4" * 64,
        "simulator_id": "V18_WHOLE_GAME_MONTE_CARLO",
        "simulator_version": "MLB_V18_DAILY_GAME_FORECAST_V1",
        "simulator_seed": 20260907,
        "event_id": "e-1",
        "source_event_id": GAME_PK,
        "event_date": START.date(),
        "season": 2026,
        "canonical_game_id": world["game"].id,
        "home_team_id": "MIL",
        "away_team_id": "CIN",
        "scheduled_start_utc": START,
        "available_as_of_utc": START - timedelta(hours=2),
        "snapshot_type": "PROSPECTIVE",
        "snapshot_version": 1,
        "status": "PROSPECTIVE_RESEARCH_SHADOW",
        "full_home_win_probability": 0.4962,
        "full_away_win_probability": 0.5038,
        "full_expected_home_score": 3.8540,
        "full_expected_away_score": 4.1116,
        "is_ready": True,
        "readiness_score": 100.0,
        "readiness_threshold": 70.0,
        "uncertainty": {"simulationCount": 5000},
        "readiness_detail": {"eligible": True},
        "distributions": {
            "totalRunDistribution": {str(k): v for k, v in mass.items()},
        },
    }
    values.update(overrides)
    row = ShadowForecast(**values)
    session.add(row)
    session.flush()
    return row


# -- the published reference values ------------------------------------------

class TestTheContractReferenceValues:
    """The numbers Codex published, reproduced from the stored PMF."""

    def test_the_mean_is_the_published_one(self):
        assert _mean(PMF) == pytest.approx(7.9656, abs=1e-9)

    def test_over_eight_matches_the_published_triplet(self):
        distribution = TotalsDistribution.from_stored(
            {str(k): v for k, v in PMF.items()},
            forecast_id=FORECAST_ID, engine_id="MLB_V18_WHOLE_GAME")
        win, push, loss = price_line(distribution, 8.0).fraction("OVER")
        assert win == pytest.approx(0.4082, abs=1e-9)
        assert push == pytest.approx(0.0764, abs=1e-9)
        assert loss == pytest.approx(0.5154, abs=1e-9)

    def test_over_eight_and_a_half_has_no_push(self):
        distribution = TotalsDistribution.from_stored(
            {str(k): v for k, v in PMF.items()},
            forecast_id=FORECAST_ID, engine_id="MLB_V18_WHOLE_GAME")
        win, push, loss = price_line(distribution, 8.5).fraction("OVER")
        assert win == pytest.approx(0.4082, abs=1e-9)
        assert push == 0.0
        assert loss == pytest.approx(0.5918, abs=1e-9)

    def test_under_reverses_win_and_loss_and_keeps_the_push(self):
        distribution = TotalsDistribution.from_stored(
            {str(k): v for k, v in PMF.items()},
            forecast_id=FORECAST_ID, engine_id="MLB_V18_WHOLE_GAME")
        win, push, loss = price_line(distribution, 8.0).fraction("UNDER")
        assert win == pytest.approx(0.5154, abs=1e-9)
        assert push == pytest.approx(0.0764, abs=1e-9)
        assert loss == pytest.approx(0.4082, abs=1e-9)

    def test_fair_odds_equal_the_contract_formula(self):
        """The contract writes 1 + pLoss/pWin; this module writes (1-pPush)/pWin.

        They are the same number, and pinning that keeps a future simplification
        of either expression from silently changing the other.
        """

        from app.totals.pricing import fair_decimal_odds
        distribution = TotalsDistribution.from_stored(
            {str(k): v for k, v in PMF.items()},
            forecast_id=FORECAST_ID, engine_id="MLB_V18_WHOLE_GAME")
        win, push, loss = price_line(distribution, 8.0).fraction("OVER")
        assert fair_decimal_odds(win, push) == pytest.approx(1 + loss / win, abs=1e-12)

    def test_expected_return_uses_raw_not_conditional_probabilities(self):
        """The contract forbids substituting pWin/(pWin+pLoss) into EV.

        On a line with real push mass the two differ materially, so using the
        conditional would overstate the return on every integer total.
        """

        from app.totals.pricing import expected_net_return
        distribution = TotalsDistribution.from_stored(
            {str(k): v for k, v in PMF.items()},
            forecast_id=FORECAST_ID, engine_id="MLB_V18_WHOLE_GAME")
        win, push, loss = price_line(distribution, 8.0).fraction("OVER")
        raw = expected_net_return(win, loss, 2.0)
        conditional = win / (win + loss)
        wrong = expected_net_return(conditional, 1 - conditional, 2.0)
        assert raw == pytest.approx(0.4082 * 1.0 - 0.5154, abs=1e-9)
        assert raw != pytest.approx(wrong, abs=1e-6)


# -- package verification ----------------------------------------------------

class TestPackageVerification:
    def test_accepts_a_well_formed_package(self, tmp_path):
        path = build_package(tmp_path)
        verified = verify_package(path)
        assert verified.schema_version == "MLB_V18_TOTALS_DISTRIBUTION_INDEX_V1"
        assert verified.manifest_files_verified == 1
        assert verified.derivative_published_at_utc is not None

    def test_checks_the_declared_index_digest(self, tmp_path):
        path = build_package(tmp_path)
        with pytest.raises(TotalsIndexError, match="physical SHA-256"):
            verify_package(path, expected_index_sha256="0" * 64)

    def test_refuses_a_package_with_no_manifest(self, tmp_path):
        path = build_package(tmp_path)
        (path.parent / "manifest.json").unlink()
        with pytest.raises(TotalsIndexError, match="manifest.json is absent"):
            verify_package(path)

    def test_refuses_a_manifest_bound_to_a_different_index(self, tmp_path):
        path = build_package(tmp_path, manifest={"indexSha256": "b" * 64})
        with pytest.raises(TotalsIndexError, match="manifest binds index"):
            verify_package(path)

    def test_refuses_a_file_whose_bytes_changed(self, tmp_path):
        path = build_package(tmp_path)
        member = path.parent / "forecasts" / OBJECT_SHA / "forecast.json"
        member.write_bytes(member.read_bytes() + b" ")
        with pytest.raises(TotalsIndexError, match="does not match its manifest SHA-256"):
            verify_package(path)

    def test_refuses_percent_units(self, tmp_path):
        """A percent arriving where a fraction is declared scales everything by 100."""

        path = build_package(tmp_path, index={"probabilityUnits": "PERCENT"})
        with pytest.raises(TotalsIndexError, match="FRACTION_0_TO_1"):
            verify_package(path)

    def test_refuses_an_index_claiming_a_new_forecast_or_authority(self, tmp_path):
        path = build_package(tmp_path, index={"newAuthorityGranted": True})
        with pytest.raises(TotalsIndexError, match="new authority"):
            verify_package(path)


# -- attachment --------------------------------------------------------------

class TestAttachment:
    def test_binds_to_the_imported_baseline_and_records_what_was_checked(
        self, world, tmp_path,
    ):
        seed_forecast(world)
        verified = verify_package(build_package(tmp_path))
        outcome = attach(world["session"], verified)

        assert outcome.accepted == [FORECAST_ID]
        assert outcome.rejected == []
        assert outcome.missing_baselines == []
        row = world["session"].scalar(select(TotalsIndexAttachment))
        assert row.pmf_matches_stored is True
        assert row.mlbam_game_pk == GAME_PK
        assert row.canonical_game_id == world["game"].id
        assert row.original_simulation_count == 5000
        assert row.recomputed_mean_total_runs == pytest.approx(7.9656, abs=1e-9)
        # The PMF itself is NOT copied; the row says where it lives.
        assert row.verification_detail["consumer_field"] == CONSUMER_FIELD
        assert row.verification_detail["pmf_source"].startswith("ALREADY_IMPORTED")
        assert "PMF_EQUALITY_BIN_FOR_BIN" in row.verification_detail["checks"]

    def test_attaching_twice_writes_one_row(self, world, tmp_path):
        seed_forecast(world)
        verified = verify_package(build_package(tmp_path))
        attach(world["session"], verified)
        again = attach(world["session"], verified)
        assert again.accepted == []
        assert again.already_attached == [FORECAST_ID]
        assert len(world["session"].scalars(select(TotalsIndexAttachment)).all()) == 1

    def test_a_missing_baseline_is_a_dependency_not_a_rejection(self, world, tmp_path):
        """Nothing is seeded, so the ordinary daily import is what is missing."""

        verified = verify_package(build_package(tmp_path))
        outcome = attach(world["session"], verified)
        assert outcome.accepted == []
        assert outcome.rejected == []
        assert len(outcome.missing_baselines) == 1
        assert "ordinary daily import" in outcome.missing_baselines[0]["reason"]

    def test_refuses_when_the_stored_pmf_differs_from_the_indexed_one(
        self, world, tmp_path,
    ):
        """The check the whole attachment exists for."""

        drifted = dict(PMF)
        drifted[9] = 0.3000
        drifted[12] = 0.1082
        seed_forecast(world, pmf=drifted)
        verified = verify_package(build_package(tmp_path))
        outcome = attach(world["session"], verified)
        assert outcome.accepted == []
        assert len(outcome.rejected) == 1
        assert "stored PMF is not the indexed one" in outcome.rejected[0]["reason"]
        assert world["session"].scalars(select(TotalsIndexAttachment)).all() == []

    def test_refuses_a_gamepk_mismatch(self, world, tmp_path):
        seed_forecast(world, source_event_id="999999")
        verified = verify_package(build_package(tmp_path))
        outcome = attach(world["session"], verified)
        assert "gamePk mismatch" in outcome.rejected[0]["reason"]

    def test_refuses_a_seed_mismatch(self, world, tmp_path):
        seed_forecast(world, simulator_seed=1)
        outcome = attach(world["session"], verify_package(build_package(tmp_path)))
        assert "seed mismatch" in outcome.rejected[0]["reason"]

    def test_refuses_a_denominator_mismatch(self, world, tmp_path):
        seed_forecast(world, uncertainty={"simulationCount": 4722})
        outcome = attach(world["session"], verify_package(build_package(tmp_path)))
        assert "denominator mismatch" in outcome.rejected[0]["reason"]

    def test_refuses_a_score_limited_population(self, world, tmp_path):
        """A conditioned population is a different question, not a baseline."""

        seed_forecast(world)
        path = build_package(tmp_path, row={"scoreLimited": True})
        outcome = attach(world["session"], verify_package(path))
        assert "score-limited" in outcome.rejected[0]["reason"]

    def test_refuses_an_excluded_draw_count(self, world, tmp_path):
        seed_forecast(world)
        path = build_package(
            tmp_path, row={"validOriginalDrawsUsed": 4900, "excludedDrawCount": 100})
        outcome = attach(world["session"], verify_package(path))
        assert "every valid original draw" in outcome.rejected[0]["reason"]

    def test_refuses_a_truncated_tail(self, world, tmp_path):
        seed_forecast(world)
        path = build_package(tmp_path)
        index = json.loads(path.read_bytes())
        index["forecasts"][0]["distribution"]["tailTruncated"] = True
        path.write_bytes(json.dumps(index, indent=2).encode("utf-8"))
        manifest_path = path.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["indexSha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))
        outcome = attach(world["session"], verify_package(path))
        assert "truncated tail" in outcome.rejected[0]["reason"]

    def test_two_versions_of_one_game_attach_separately(self, world, tmp_path):
        """Latest-at-inventory is a discovery hint, not permission to overwrite."""

        seed_forecast(world)
        other_sha = "b" * 64
        seed_forecast(
            world, forecast_id=f"mlb-v18-daily-{other_sha}", event_id="e-2",
            snapshot_version=2, pmf=PMF)
        verified = verify_package(build_package(tmp_path))
        attach(world["session"], verified)

        rows = world["session"].scalars(select(TotalsIndexAttachment)).all()
        # Only the indexed version attaches; the other stays untouched, not
        # replaced and not silently adopted.
        assert [row.forecast_identity for row in rows] == [FORECAST_ID]

    def test_attachment_map_keys_by_forecast_identity(self, world, tmp_path):
        seed_forecast(world)
        attach(world["session"], verify_package(build_package(tmp_path)))
        mapped = attachment_map(world["session"])
        assert set(mapped) == {FORECAST_ID}


class TestAcknowledgement:
    def test_names_the_index_the_forecasts_and_four_distinct_clocks(
        self, world, tmp_path,
    ):
        seed_forecast(world)
        verified = verify_package(build_package(tmp_path))
        outcome = attach(world["session"], verified)
        ack = acknowledgement(verified, outcome)

        assert ack["indexSha256"] == verified.index_sha256
        assert ack["consumerResultThisRun"]["acceptedForecastIds"] == [FORECAST_ID]
        assert ack["consumerResultThisRun"]["distinctGamesBound"] == 1
        clocks = ack["clocks"]
        assert clocks["producerSourceInventoryAtUtc"]
        assert clocks["producerDerivativePublishedAtUtc"]
        assert clocks["consumerAttachedAtUtc"]
        # The consumer clock is its own, and says so.
        assert "never backdates" in clocks["note"]

    def test_states_that_no_distribution_was_copied(self, world, tmp_path):
        seed_forecast(world)
        verified = verify_package(build_package(tmp_path))
        ack = acknowledgement(verified, attach(world["session"], verified))
        assert ack["pmfHandling"]["copied"] is False
        assert ack["pmfHandling"]["source"] == CONSUMER_FIELD
        assert ack["authority"]["newAuthorityGranted"] is False
        assert ack["authority"]["providerRequests"] == 0
