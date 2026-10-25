"""NFL early-line export (NFL_EARLY_LINE_OBSERVATION_V1): the safety-critical behaviour.

Focused on what can go wrong silently: horizon windows, the retry-after-an-
uncharged-failure rule, forecast availability, the two price clocks, write-once
idempotency, and Model Studio's own frozen validator accepting what we emit.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStatus, GameTeam, JobRun, League, ProviderRequestLedger, RunStatus, Season,
    ShadowForecast, ShadowForecastBundle, Sport, Team,
)
from app.research_exports import nfl_early_line as el

CONTRACT_DIR = Path.home() / "asp-model-studio" / "outputs" / "nfl-early-line-information-edge-v1"
KICK = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)          # post-freeze, all horizons after it
EVENT = "sgoEVENT0001"
_DEFAULT = object()
_ABSENT = object()   # the provider sent no lastUpdatedAt field at all
NFLVERSE = "2026_03_AWY_HOM"


def _contract_available() -> bool:
    try:
        el.verify_contract_files(CONTRACT_DIR)
        return True
    except el.ContractUnbound:
        return False


needs_contract = pytest.mark.skipif(not _contract_available(), reason="frozen contract dir absent")


class World:
    def __init__(self, session: Session, root: Path) -> None:
        self.session, self.root = session, root
        self.exchange = root / "exchange"
        self.archive = root / "archive"
        self.evidence = root / "producer-evidence"
        sport = Sport(code="FOOTBALL", name="NFL"); session.add(sport); session.flush()
        league = League(sport_id=sport.id, code="NFL", name="NFL"); session.add(league); session.flush()
        season = Season(league_id=league.id, name="2026", starts_at=KICK - timedelta(days=30),
                        ends_at=KICK + timedelta(days=120))
        session.add(season); session.flush()
        self.game = Game(league_id=league.id, season_id=season.id, starts_at=KICK,
                         status=GameStatus.SCHEDULED)
        session.add(self.game); session.flush()
        self.teams = {}
        for is_home, abbr in ((True, "HOM"), (False, "AWY")):
            team = Team(league_id=league.id, name=abbr, abbreviation=abbr)
            session.add(team); session.flush()
            session.add(GameTeam(game_id=self.game.id, team_id=team.id, is_home=is_home))
            self.teams[is_home] = team
        session.flush()
        self.ref = el.GameRef(
            game_id=str(self.game.id), starts_at=KICK, status="SCHEDULED",
            home_team_id=str(self.teams[True].id), away_team_id=str(self.teams[False].id),
            home_abbr="HOM", away_abbr="AWY", sgo_event_ids=[EVENT], nflverse_ids=[NFLVERSE],
            # Pregame, AISP retains neither: they come from the producer's
            # schedule identity manifest, or stay null with a reason.
            season=2026, season_type=None, week=None)

    # -- a forecast: package bytes, import receipt, and exposure evidence ----
    def forecast(self, *, snapshot="CURRENT", version=1, exposed: datetime, created: datetime,
                 home="0.5475", away="0.4267", tie="0.0258", next_run=_DEFAULT,
                 job="import-nfl-final-pair", receipt=True, readiness_visible=None,
                 home_abbr="HOM", away_abbr="AWY", starts_at=None,
                 generated: datetime | None = None) -> ShadowForecast:
        """One published package, imported by a run whose receipt names it.

        `receipt=False` drops the import receipt, so the run no longer proves it
        committed this package. `readiness_visible` supplies a FINAL's atomic
        exposure receipt; without one a FINAL stays unbound.
        """

        fid = f"asp-sports-game-forecast:sha256:{uuid.uuid4().hex}{uuid.uuid4().hex}"
        generated = generated or (exposed - timedelta(seconds=30))
        package = {
            "bundleId": f"asp-sports-forecast-exchange:sha256:{uuid.uuid4().hex}",
            "generatedAtUtc": generated.isoformat().replace("+00:00", "Z"),
            "league": "NFL", "sport": "NFL", "season": 2026,
            "forecasts": [{
                "forecastId": fid, "eventId": NFLVERSE, "sourceEventId": f"nflverse:{NFLVERSE}",
                "season": 2026, "seasonType": "REG", "week": 3,
                "snapshotType": snapshot, "snapshotVersion": version,
                "scheduledStartUtc": (starts_at or KICK).isoformat().replace("+00:00", "Z"),
                "homeTeam": {"abbreviation": home_abbr, "displayName": "Home", "teamId": home_abbr},
                "awayTeam": {"abbreviation": away_abbr, "displayName": "Away", "teamId": away_abbr},
                "engine": {"engineId": "NFL_M1_XGBOOST", "engineVersion": "NFL_M1_2026_V1",
                           "candidateId": "cand-1", "modelBinarySha256": uuid.uuid4().hex},
                "availableAsOfUtc": exposed.isoformat().replace("+00:00", "Z"),
                "forecastCutoffUtc": (exposed - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                "sourceCutoffUtc": (exposed - timedelta(minutes=9)).isoformat().replace("+00:00", "Z"),
            }],
        }
        # The probabilities must keep the producer's exact decimal TEXT: written
        # as placeholders, then substituted as bare JSON numbers so the bytes
        # read 0.0258, not 0.02579999999999999.
        child = package["forecasts"][0]
        child["homeWinProbability"] = "@@HOME@@"
        child["awayWinProbability"] = "@@AWAY@@"
        child["tieProbability"] = "@@TIE@@"
        text = json.dumps(package)
        for token, value in (("@@HOME@@", home), ("@@AWAY@@", away), ("@@TIE@@", tie)):
            text = text.replace(f'"{token}"', value)
        raw = text.encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        path = self.exchange / "aisp-daily-forecast-bundles" / "sha256" / sha / "bundle.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        bundle = ShadowForecastBundle(
            bundle_sha256=sha, bundle_id=f"b-{sha[:8]}",
            logical_uri=f"asp-model-exchange://aisp-daily-forecast-bundles/sha256/{sha}/bundle.json",
            source_fixture_id="f", slate_date=KICK.date(), league="NFL", sport="FOOTBALL",
            status="IMPORTED", research_shadow_only=True, generated_at=generated, forecast_count=1,
            quote_count=0, candidate_count=0, imported_at=created, contract_version="v1", evidence={})
        self.session.add(bundle); self.session.flush()
        row = ShadowForecast(
            bundle_id=bundle.id, forecast_id=fid, engine_id="NFL_M1_XGBOOST",
            engine_version="1.0.0", model_artifact_sha256=uuid.uuid4().hex, simulator_id="s",
            simulator_version="1", simulator_seed=1, event_id=NFLVERSE,
            source_event_id=f"nflverse:{NFLVERSE}", event_date=KICK.date(), season=2026,
            home_team_id="H", away_team_id="A", is_ready=True, readiness_score=99.0,
            readiness_threshold=50.0, status="READY", full_home_win_probability=float(home),
            full_away_win_probability=float(away), full_expected_home_score=21.0,
            full_expected_away_score=20.0, distributions={}, uncertainty={}, readiness_detail={},
            forecast_schema_version="1", snapshot_type=snapshot, snapshot_version=version,
            identity_evidence={}, lineage_detail={}, canonical_game_id=self.game.id,
            available_as_of_utc=exposed, forecast_cutoff_utc=exposed - timedelta(minutes=5),
            created_at=created)
        self.session.add(row)
        # The run that committed it, carrying the receipt that names this exact
        # package, plus -- unless told otherwise -- the successor run whose start
        # is the post-commit upper bound.
        result = {"accepted": [{
            "bundle_sha256": sha, "bundle_id": package["bundleId"], "sport": "NFL",
            "forecasts": 1,
            "source_evidence_utc": (generated - timedelta(seconds=20)).isoformat().replace("+00:00", "Z"),
            "published_utc": exposed.isoformat().replace("+00:00", "Z"),
            "imported_at_utc": created.isoformat().replace("+00:00", "Z"),
        }]} if receipt else {"accepted": []}
        self.session.add(JobRun(job_name=job, status=RunStatus.SUCCEEDED,
                                started_at=created - timedelta(seconds=5),
                                completed_at=created + timedelta(seconds=5),
                                parameters={}, result=result))
        successor = created + timedelta(seconds=60) if next_run is _DEFAULT else next_run
        if successor is not None:
            self.session.add(JobRun(job_name=job, status=RunStatus.SUCCEEDED, started_at=successor,
                                    completed_at=successor + timedelta(seconds=2), parameters={},
                                    result={"accepted": []}))
        if readiness_visible is not None:
            self.readiness(fid, sha, readiness_visible)
        self.session.flush()
        return row

    def readiness(self, forecast_id: str, package_sha: str, visible: datetime) -> None:
        """The atomic exposure receipt a FINAL needs, at its content address."""

        receipt = {
            "schemaVersion": "NFL_FINAL_READY_RECEIPT_V1", "forecastId": forecast_id,
            "bundleRef": {"sha256": package_sha},
            "currentLocatorSnapshotRef": {"sha256": uuid.uuid4().hex},
            "forecastLocatorExposure": {"visibleAtUtc": visible.isoformat().replace("+00:00", "Z")},
        }
        raw = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        root = self.exchange / "nfl-final-readiness"
        (root / "objects" / "sha256" / sha).mkdir(parents=True, exist_ok=True)
        (root / "objects" / "sha256" / sha / "receipt.json").write_bytes(raw)
        # The real index holds EVERY receipt, so merge rather than replace --
        # replacing made each new forecast silently strip its predecessors of
        # their exposure evidence.
        root.mkdir(parents=True, exist_ok=True)
        current = root / "current.json"
        index = (json.loads(current.read_bytes()) if current.exists()
                 else {"schemaVersion": "NFL_FINAL_READINESS_INDEX_V1", "receipts": {}})
        index["receipts"][forecast_id] = {
            "receiptRef": {"sha256": sha},
            "exposureReferencePath": f"availability/{sha}.json"}
        index["updatedAtUtc"] = visible.isoformat().replace("+00:00", "Z")
        current.write_bytes(
            json.dumps(index, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    # -- one provider response, archived exactly as production archives it ---
    def response(self, *, requested: datetime, books: dict[str, tuple[int, int, datetime]] | None = None,
                 charged: int = 1, status: str = "SUCCEEDED", archived: bool = True,
                 capture_after: timedelta = timedelta(seconds=1),
                 home_short: str = "HOM", away_short: str = "AWY",
                 event_id: str = EVENT, raw_clocks: dict[str, Any] | None = None,
                 one_sided: dict[str, tuple[str, int, datetime]] | None = None,
                 ) -> ProviderRequestLedger:
        books = books if books is not None else {
            "fanduel": (-300, 245, requested - timedelta(minutes=4)),
            "betmgm": (-295, 240, requested - timedelta(minutes=4)),
            "caesars": (-320, 250, requested - timedelta(hours=6)),   # unchanged for hours
        }
        captured = requested + capture_after
        fingerprint = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        meta = {"request_shape": {"filters": {
            "startsAfter": (KICK - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            "startsBefore": (KICK + timedelta(hours=2)).isoformat().replace("+00:00", "Z")}}}
        if archived:
            # `raw_clocks` overrides a book's lastUpdatedAt with whatever the
            # provider actually sent -- absent, null, or unparseable text -- so
            # the record's raw/parsed/disposition triple can be exercised.
            def clock(book: str, moment: datetime) -> dict[str, Any]:
                if raw_clocks is not None and book in raw_clocks:
                    value = raw_clocks[book]
                    return {} if value is _ABSENT else {"lastUpdatedAt": value}
                return {"lastUpdatedAt": moment.isoformat().replace("+00:00", "Z")}

            def by(i: int) -> dict[str, Any]:
                side = ("home", "away")[i]
                quotes = {b: {"odds": f"{v[i]:+d}", "available": True, **clock(b, v[2])}
                          for b, v in books.items()}
                # A book quoting only one side of the game: a real shape, and
                # one the record must retain rather than quietly drop.
                for book, (only, price, moment) in (one_sided or {}).items():
                    if only == side:
                        quotes[book] = {"odds": f"{price:+d}", "available": True,
                                        **clock(book, moment)}
                return quotes
            body = json.dumps({"data": [{
                "eventID": event_id,
                # The provider's own identity for the event, which the consumer
                # checks against the canonical game rather than assuming.
                "teams": {"home": {"teamID": home_short, "names": {"short": home_short}},
                          "away": {"teamID": away_short, "names": {"short": away_short}}},
                "info": {"seasonWeek": 3},
                "odds": {
                    "points-home-game-ml-home": {"byBookmaker": by(0)},
                    "points-away-game-ml-away": {"byBookmaker": by(1)}}}]}).encode("utf-8")
            stem = uuid.uuid4().hex
            self.archive.mkdir(parents=True, exist_ok=True)
            body_path = self.archive / f"{stem}.body.gz"
            body_path.write_bytes(gzip.compress(body, mtime=0))
            envelope = json.dumps({"metadata": {"request_fingerprint": fingerprint}}).encode("utf-8")
            env_path = self.archive / f"{stem}.json"
            env_path.write_bytes(envelope)
            meta["raw_archive"] = {
                "status": "ARCHIVED", "path": str(env_path), "sha256": hashlib.sha256(envelope).hexdigest(),
                "responseBodyPath": str(body_path), "responseBodySha256": hashlib.sha256(body).hexdigest(),
                "archivedAtUtc": captured.isoformat().replace("+00:00", "Z"),
                "requestFingerprint": fingerprint}
        # Production writes the reservation first; the terminal row points back
        # at it. status="ORPHAN" leaves a reservation with no terminal row: an
        # attempt whose charge is unknown because the process died mid-request.
        reservation = ProviderRequestLedger(
            provider="SPORTSGAMEODDS", endpoint_key="sgo_nfl_events", requested_at=requested,
            status="RESERVED", request_fingerprint=fingerprint, quota_cost=1, response_metadata=meta)
        self.session.add(reservation); self.session.flush()
        if status == "ORPHAN":
            return reservation
        row = ProviderRequestLedger(
            provider="SPORTSGAMEODDS", endpoint_key="sgo_nfl_events", requested_at=requested,
            completed_at=captured + timedelta(milliseconds=400), status=status,
            request_fingerprint=fingerprint, records_received=charged, response_metadata=meta,
            reservation_id=reservation.id)
        self.session.add(row); self.session.flush()
        return row

    def build(self, horizon: str, *, evidence_dir=None) -> dict:
        self.session.commit()
        return el.build_record(self.session, self.ref, horizon, exchange_root=self.exchange,
                               evidence_dir=evidence_dir or self.evidence)


@pytest.fixture
def world(session: Session, tmp_path: Path) -> World:
    return World(session, tmp_path)


def _validate(record: dict, tmp_path: Path) -> dict:
    final = dict(record, createdAtUtc="2026-09-26T18:00:00Z")
    path = tmp_path / f"{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(final), encoding="utf-8")
    out = subprocess.run([sys.executable, str(CONTRACT_DIR / "validate_observation.py"), str(path)],
                         capture_output=True, text=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


# ==========================================================================
# Frozen identity and exact arithmetic
# ==========================================================================

@needs_contract
def test_the_frozen_contract_files_hash_to_the_reviewed_values():
    assert el.verify_contract_files(CONTRACT_DIR) == el.CONTRACT_FILES


def test_a_changed_contract_file_is_refused(tmp_path: Path):
    for name in el.CONTRACT_FILES:
        (tmp_path / name).write_text("tampered", encoding="utf-8")
    with pytest.raises(el.ContractUnbound):
        el.verify_contract_files(tmp_path)


def test_no_vig_and_consensus_are_exact():
    home, away = el.american_to_decimal(-300), el.american_to_decimal(245)
    assert home == Decimal(1) + Decimal(100) / Decimal(300) and away == Decimal("3.45")
    q = el.no_vig_home(home, away)
    assert q == (1 / home) / ((1 / home) + (1 / away))
    assert el.median([Decimal("0.7"), Decimal("0.5"), Decimal("0.6")]) == Decimal("0.6")
    assert el.median([Decimal("0.5"), Decimal("0.7")]) == Decimal("0.6")


# ==========================================================================
# The positive path, through Model Studio's own validator
# ==========================================================================

def _bound_late(world) -> dict:
    """A LATE record whose FINAL forecast has every piece of required evidence."""

    capture = KICK - timedelta(minutes=26)
    world.forecast(snapshot="FINAL_PREGAME", exposed=KICK - timedelta(minutes=40),
                   created=KICK - timedelta(minutes=39),
                   readiness_visible=KICK - timedelta(minutes=38))
    world.response(requested=capture)
    return world.build(el.LATE_T26)


@needs_contract
def test_an_early_record_passes_the_frozen_validator(world, tmp_path):
    """The market side binds; the CURRENT forecast cannot, and says why."""

    world.forecast(exposed=KICK - timedelta(hours=40), created=KICK - timedelta(hours=39, minutes=58))
    world.response(requested=KICK - timedelta(hours=22))
    record = world.build(el.EARLY_T24)

    assert record["eligibility"]["state"] == el.STATE_PREDICTIVE
    assert record["forecast"] is None
    assert record["eligibility"]["forecastDisposition"] == "MISSING_FORECAST"
    assert "CURRENT_INDEX_EXPOSURE_RECEIPT_ABSENT" in record["eligibility"]["reasonCodes"]
    verdict = _validate(record, tmp_path)
    assert verdict["valid"], verdict["errors"]


@needs_contract
def test_a_bound_final_forecast_record_passes_the_frozen_validator(world, tmp_path):
    """The full binding path, end to end, on the horizon that can reach it."""

    record = _bound_late(world)

    assert record["forecast"] is not None
    f = record["forecast"]
    assert f["snapshotType"] == "FINAL_PREGAME"
    assert f["exposedAtUtc"] and f["consumerObservedOrImportCompletedAtUtc"]
    assert f["generatedAtUtc"] != f["forecastCutoffUtc"]      # a real clock, not an input cutoff
    assert f["importRun"]["jobRunId"]
    verdict = _validate(record, tmp_path)
    assert verdict["valid"], verdict["errors"]


@needs_contract
@pytest.mark.parametrize("mutation, message", [
    (lambda r: r["forecast"].update(availabilityAtUtc="2026-09-27T16:59:00Z"),
     "late forecast unavailable by T-15"),
    (lambda r: r["forecast"].update(snapshotType="CURRENT"), "late forecast not FINAL_PREGAME"),
    (lambda r: r["forecast"].update(tieProbabilityRaw="0.9"),
     "raw probabilities do not sum exactly to 1"),
    (lambda r: r["market"]["candidates"][0]["home"].update(lastUpdatedAtUtc=None),
     "leg update clock missing"),
    (lambda r: r["eligibility"].update(state="RETURN_SIMULATION_ELIGIBLE", returnEligible=True),
     "return conventions incomplete"),
    (lambda r: r["market"].update(selectedSportsbookKey="zzz"), "deterministic selected book mismatch"),
    (lambda r: r["horizon"].update(providerRequestAtUtc="2026-09-27T16:45:00Z"),
     "late request after T-21 hard stop"),
])
def test_the_validator_still_rejects_each_negative_mutation(world, tmp_path, mutation, message):
    record = json.loads(json.dumps(_bound_late(world)))
    mutation(record)

    verdict = _validate(record, tmp_path)
    assert not verdict["valid"]
    assert any(message in e for e in verdict["errors"]), verdict["errors"]


# ==========================================================================
# EARLY_T24: the frozen window, never a later response
# ==========================================================================

@pytest.mark.parametrize("offset, inside", [
    (timedelta(hours=30), True), (timedelta(hours=30, seconds=2), False),
    (timedelta(hours=18, seconds=2), True), (timedelta(hours=17, minutes=59), False)])
def test_the_early_window_is_t30h_to_t18h_inclusive_by_capture(world, offset, inside):
    # capture is request + 1 s
    world.response(requested=KICK - offset - timedelta(seconds=1))
    record = world.build(el.EARLY_T24)
    if inside:
        assert "MISSING_EARLY_PRICE" not in record["eligibility"]["reasonCodes"]
    else:
        assert "MISSING_EARLY_PRICE" in record["eligibility"]["reasonCodes"]
        assert record["market"]["providerResponseSha256"] is None


def test_a_later_response_is_never_substituted_for_a_missing_early_one(world):
    world.response(requested=KICK - timedelta(hours=2))      # the late production purchase
    record = world.build(el.EARLY_T24)
    assert record["eligibility"]["state"] == el.STATE_UNPRICED
    assert "MISSING_EARLY_PRICE" in record["eligibility"]["reasonCodes"]


def test_the_early_response_closest_to_t24_wins(world):
    world.response(requested=KICK - timedelta(hours=29))
    closest = world.response(requested=KICK - timedelta(hours=23, minutes=30))
    record = world.build(el.EARLY_T24)
    assert record["market"]["originalBytesRef"]["ledgerRowId"] == str(closest.id)


def test_an_unarchived_response_is_never_authentic(world):
    world.response(requested=KICK - timedelta(hours=22), archived=False)
    assert "MISSING_EARLY_PRICE" in world.build(el.EARLY_T24)["eligibility"]["reasonCodes"]


# ==========================================================================
# The two price clocks
# ==========================================================================

def test_an_unchanged_price_in_a_fresh_response_is_eligible(world):
    """Caesars' legs were last changed six hours before capture. Still a fresh observation."""

    world.response(requested=KICK - timedelta(hours=22))
    record = world.build(el.EARLY_T24)
    caesars = next(c for c in record["market"]["candidates"] if c["sportsbookKey"] == "caesars")
    assert caesars["predictiveEligible"] is True
    assert caesars["home"]["sourceUpdateAgeSeconds"] >= 6 * 3600
    assert record["horizon"]["acquisitionAgeSeconds"] == 0          # observed at the capture itself


def test_request_capture_and_fetch_clocks_stay_separate(world):
    row = world.response(requested=KICK - timedelta(hours=22), capture_after=timedelta(seconds=3))
    h = world.build(el.EARLY_T24)["horizon"]
    assert h["providerRequestAtUtc"] != h["providerResponseCapturedAtUtc"] != h["fetchedAtUtc"]
    assert h["providerRequestAtUtc"].startswith((KICK - timedelta(hours=22)).strftime("%Y-%m-%dT%H:%M"))
    assert "Not a receipt" in h["clockBasis"]["providerRequestAtUtc"]


def test_selection_is_lexical_and_consensus_is_the_median(world):
    world.response(requested=KICK - timedelta(hours=22))
    m = world.build(el.EARLY_T24)["market"]
    assert m["selectedSportsbookKey"] == "betmgm"
    values = sorted(Decimal(c["homeNoVig"]) for c in m["candidates"])
    assert Decimal(m["consensusHomeNoVig"]) == values[1]


def test_no_record_is_return_eligible_while_quote_jurisdiction_is_unstated(world):
    world.response(requested=KICK - timedelta(hours=22), books={
        "fanduel": (-300, 245, KICK - timedelta(hours=22, minutes=4))})
    record = world.build(el.EARLY_T24)
    assert record["eligibility"]["state"] == el.STATE_PREDICTIVE
    assert record["eligibility"]["returnEligible"] is False
    assert "RETURN_QUOTE_JURISDICTION_NOT_STATED" in record["eligibility"]["reasonCodes"]


# ==========================================================================
# Forecast evidence: every piece required, nothing assumed
# ==========================================================================

def test_a_current_snapshot_has_no_atomic_exposure_receipt_and_stays_unbound(world):
    """The producer's own declaration is not exposure proof, so CURRENT is unbound."""

    capture = KICK - timedelta(hours=22)
    world.forecast(exposed=capture - timedelta(hours=2), created=capture - timedelta(hours=1))
    world.response(requested=capture)
    record = world.build(el.EARLY_T24)

    assert record["forecast"] is None
    assert "CURRENT_INDEX_EXPOSURE_RECEIPT_ABSENT" in record["eligibility"]["reasonCodes"]


def test_a_final_without_its_readiness_receipt_stays_unbound(world):
    world.forecast(snapshot="FINAL_PREGAME", exposed=KICK - timedelta(minutes=40),
                   created=KICK - timedelta(minutes=39), readiness_visible=None)
    world.response(requested=KICK - timedelta(minutes=26))
    record = world.build(el.LATE_T26)

    assert record["forecast"] is None
    assert "FORECAST_READINESS_EXPOSURE_UNPROVED" in record["eligibility"]["reasonCodes"]


def test_an_import_run_without_a_receipt_naming_the_package_proves_nothing(world):
    """A row timestamp inside an unrelated run is not import linkage."""

    world.forecast(snapshot="FINAL_PREGAME", exposed=KICK - timedelta(minutes=40),
                   created=KICK - timedelta(minutes=39),
                   readiness_visible=KICK - timedelta(minutes=38), receipt=False)
    world.response(requested=KICK - timedelta(minutes=26))
    record = world.build(el.LATE_T26)

    assert record["forecast"] is None
    assert "IMPORT_RUN_LINKAGE_UNPROVED" in record["eligibility"]["reasonCodes"]


def test_the_consumer_bound_is_the_successor_run_start(world):
    """Committed before the checkpoint, but the successor run starts after it."""

    world.forecast(snapshot="FINAL_PREGAME", exposed=KICK - timedelta(minutes=40),
                   created=KICK - timedelta(minutes=39),
                   readiness_visible=KICK - timedelta(minutes=38),
                   next_run=KICK - timedelta(minutes=10))
    world.response(requested=KICK - timedelta(minutes=26))
    assert world.build(el.LATE_T26)["forecast"] is None


def test_a_forecast_exposed_after_the_early_capture_is_never_bound(world):
    capture = KICK - timedelta(hours=22)
    world.forecast(snapshot="FINAL_PREGAME", exposed=capture + timedelta(minutes=5),
                   created=capture + timedelta(minutes=6),
                   readiness_visible=capture + timedelta(minutes=7))
    world.response(requested=capture)
    assert world.build(el.EARLY_T24)["forecast"] is None


def test_the_newest_version_wins_not_the_latest_availability(world):
    """Version 2 is the newest even though version 3's evidence lands later."""

    world.forecast(snapshot="FINAL_PREGAME", version=1, exposed=KICK - timedelta(minutes=50),
                   created=KICK - timedelta(minutes=49),
                   readiness_visible=KICK - timedelta(minutes=48))
    world.forecast(snapshot="FINAL_PREGAME", version=2, exposed=KICK - timedelta(minutes=45),
                   created=KICK - timedelta(minutes=44),
                   readiness_visible=KICK - timedelta(minutes=43))
    world.response(requested=KICK - timedelta(minutes=26))
    f = world.build(el.LATE_T26)["forecast"]
    assert f["snapshotVersion"] == 2


def test_probabilities_come_exactly_from_the_producers_bytes(world):
    f = _bound_late(world)["forecast"]
    assert (f["homeProbabilityRaw"], f["awayProbabilityRaw"], f["tieProbabilityRaw"]) == (
        "0.5475", "0.4267", "0.0258")
    assert sum(Decimal(f[k]) for k in
               ("homeProbabilityRaw", "awayProbabilityRaw", "tieProbabilityRaw")) == 1


def test_a_child_whose_scheduled_start_disagrees_is_refused(world):
    world.forecast(snapshot="FINAL_PREGAME", exposed=KICK - timedelta(minutes=40),
                   created=KICK - timedelta(minutes=39),
                   readiness_visible=KICK - timedelta(minutes=38),
                   starts_at=KICK + timedelta(hours=3))
    world.response(requested=KICK - timedelta(minutes=26))
    record = world.build(el.LATE_T26)

    assert record["forecast"] is None
    assert "CHILD_SCHEDULED_START_DISAGREES" in record["eligibility"]["reasonCodes"]


# ==========================================================================
# Identity failures are OPERATIVE (Model Studio's correction 4)
# ==========================================================================

def test_a_divergent_child_team_code_prevents_binding(world):
    """The LA/LAR case: unresolvable without evidence, so it never binds.

    This is the correction. A divergence used to be annotated and waved
    through, which let a forecast bind to a game we had not shown it belonged
    to. It now fails closed until Model Studio's alias registry can resolve it.
    """

    world.forecast(snapshot="FINAL_PREGAME", exposed=KICK - timedelta(minutes=40),
                   created=KICK - timedelta(minutes=39),
                   readiness_visible=KICK - timedelta(minutes=38), home_abbr="LA")
    world.response(requested=KICK - timedelta(minutes=26))
    record = world.build(el.LATE_T26)

    assert record["forecast"] is None
    assert record["eligibility"]["forecastDisposition"] == "MISSING_FORECAST"
    assert "CHILD_HOME_TEAM_ALIAS_UNSUPPORTED" in record["eligibility"]["reasonCodes"]
    # The game is preserved, not dropped.
    assert record["game"]["gameId"] == str(world.game.id)


def test_a_response_for_another_event_prices_nothing(world):
    world.response(requested=KICK - timedelta(hours=22), event_id="sgoSOMEONEELSE")
    record = world.build(el.EARLY_T24)

    # No response carries THIS game's event, so there is simply no early price.
    assert record["market"]["providerEventId"] is None
    assert record["eligibility"]["state"] == el.STATE_UNPRICED
    assert "MISSING_EARLY_PRICE" in record["eligibility"]["reasonCodes"]


def test_a_divergent_response_team_code_prevents_predictive_eligibility(world):
    """The provider says LA, the canonical row says HOM. Nothing prices it."""

    world.response(requested=KICK - timedelta(hours=22), home_short="LA")
    record = world.build(el.EARLY_T24)

    assert record["eligibility"]["state"] == el.STATE_EXCLUDED
    assert "RESPONSE_HOME_TEAM_ALIAS_UNSUPPORTED" in record["eligibility"]["reasonCodes"]
    assert record["market"]["selectedSportsbookKey"] is None
    # Preserved, with the reason on every candidate rather than removed.
    assert record["market"]["candidates"]
    assert all(not c["predictiveEligible"] for c in record["market"]["candidates"])
    assert all("RESPONSE_HOME_TEAM_ALIAS_UNSUPPORTED" in c["reasonCodes"]
               for c in record["market"]["candidates"])


def test_a_missing_provider_team_block_is_not_a_silent_pass(world):
    world.response(requested=KICK - timedelta(hours=22), home_short="", away_short="")
    record = world.build(el.EARLY_T24)

    assert record["eligibility"]["state"] == el.STATE_EXCLUDED
    assert "RESPONSE_HOME_TEAM_UNVERIFIABLE" in record["eligibility"]["reasonCodes"]


def test_a_missing_schedule_manifest_is_named_and_fails_closed(world):
    """Without the producer's manifest there is no week, and nothing hides it."""

    world.response(requested=KICK - timedelta(hours=22))
    record = world.build(el.EARLY_T24)

    evidence = record["producerEvidence"]
    assert evidence["scheduleRowDisposition"] == "PREGAME_SCHEDULE_MANIFEST_NOT_SUPPLIED"
    assert evidence["scheduleRowResolved"] is False
    assert record["game"]["week"] is None and record["game"]["seasonType"] is None
    assert "SCHEDULE_WEEK_NOT_RETAINED_PREGAME" in record["eligibility"]["reasonCodes"]


# ==========================================================================
# Both sportsbook keys, and all three parts of the leg clock
# ==========================================================================

def test_the_provider_key_survives_beside_the_normalized_one(world):
    """The provider's own casing is never repaired in place."""

    world.response(requested=KICK - timedelta(hours=22), books={
        "FanDuel": (-300, 245, KICK - timedelta(hours=22, minutes=4)),
        "BetMGM": (-295, 240, KICK - timedelta(hours=22, minutes=4))})
    market = world.build(el.EARLY_T24)["market"]
    by_provider = {c["providerSportsbookKey"]: c for c in market["candidates"]}

    assert set(by_provider) == {"FanDuel", "BetMGM"}
    assert by_provider["FanDuel"]["normalizedSportsbookKey"] == "fanduel"
    # Selection stays on the normalized key, and the provider's key rides along.
    assert market["selectedSportsbookKey"] == "betmgm"
    assert market["selectedProviderSportsbookKey"] == "BetMGM"
    # The no-vig lookup follows the normalized key, not the provider's casing.
    assert by_provider["FanDuel"]["homeNoVig"] is not None


@pytest.mark.parametrize("raw, disposition, code", [
    (_ABSENT, "ABSENT", "BOOK_HOME_CLOCK_ABSENT"),
    (None, "ABSENT", "BOOK_HOME_CLOCK_ABSENT"),
    ("not-a-timestamp", "UNPARSEABLE", "BOOK_HOME_CLOCK_UNPARSEABLE"),
])
def test_the_raw_leg_clock_and_its_disposition_are_both_kept(world, raw, disposition, code):
    """Absent and unreadable are different facts, and neither is invented away."""

    world.response(requested=KICK - timedelta(hours=22), raw_clocks={"caesars": raw})
    market = world.build(el.EARLY_T24)["market"]
    caesars = next(c for c in market["candidates"] if c["providerSportsbookKey"] == "caesars")

    assert caesars["home"]["lastUpdatedAtDisposition"] == disposition
    assert caesars["home"]["lastUpdatedAtUtc"] is None
    assert caesars["home"]["lastUpdatedAtRaw"] == (None if raw is _ABSENT else raw)
    assert code in caesars["reasonCodes"]
    assert not caesars["predictiveEligible"]
    # Its healthy peers still carry the observation.
    assert market["selectedSportsbookKey"] == "betmgm"


def test_a_good_leg_clock_is_parsed_and_says_so(world):
    world.response(requested=KICK - timedelta(hours=22))
    market = world.build(el.EARLY_T24)["market"]

    for candidate in market["candidates"]:
        for side in ("home", "away"):
            assert candidate[side]["lastUpdatedAtDisposition"] == "PARSED_UTC"
            assert candidate[side]["lastUpdatedAtRaw"].endswith("Z")


# ==========================================================================
# The pregame proxy must actually be pregame
# ==========================================================================

def test_a_response_captured_after_kickoff_is_not_an_opening_price(world):
    """Requested before kickoff, answered after it. Those bytes are not pregame."""

    world.response(requested=KICK - timedelta(seconds=30), capture_after=timedelta(minutes=2))
    record = world.build(el.MARKET_OPEN_PROXY)

    assert record["market"]["providerResponseSha256"] is None
    assert record["eligibility"]["state"] == el.STATE_UNPRICED
    assert "OPEN_PROXY_RESPONSE_NOT_PREGAME" in record["eligibility"]["reasonCodes"]


def test_a_pregame_capture_is_still_the_open_proxy(world):
    world.response(requested=KICK - timedelta(hours=40))
    record = world.build(el.MARKET_OPEN_PROXY)

    capture = datetime.fromisoformat(
        record["horizon"]["providerResponseCapturedAtUtc"].replace("Z", "+00:00"))
    assert capture < KICK
    assert record["eligibility"]["state"] == el.STATE_PREDICTIVE


# ==========================================================================
# The population is the whole authentic response
# ==========================================================================

def test_one_sided_and_malformed_books_are_retained_and_counted(world):
    world.response(requested=KICK - timedelta(hours=22), raw_clocks={"caesars": _ABSENT},
                   one_sided={"pinnacle": ("home", -310, KICK - timedelta(hours=23))})
    market = world.build(el.EARLY_T24)["market"]

    assert market["population"] == {
        "booksInResponse": 4, "completePairs": 3, "oneSidedBooks": 1,
        "predictiveEligible": 2, "basis": market["population"]["basis"]}
    # The malformed pair is present, not omitted.
    assert {c["providerSportsbookKey"] for c in market["candidates"]} == {
        "fanduel", "betmgm", "caesars"}
    # So is the one-sided book, with the side it did quote and the reason.
    [lone] = market["incompleteBooks"]
    assert lone["providerSportsbookKey"] == "pinnacle"
    assert lone["home"]["americanOdds"] == -310 and lone["away"] is None
    assert "BOOK_AWAY_LEG_MISSING" in lone["reasonCodes"]
    # And it never becomes the selected book despite sorting first.
    assert market["selectedSportsbookKey"] == "betmgm"


# ==========================================================================
# LATE_T26: T-26 primary, T-23 retry only after a verified zero charge, T-21 stop
# ==========================================================================

def test_the_existing_t66_purchase_is_not_a_late_price(world):
    world.response(requested=KICK - timedelta(minutes=66))
    assert "MISSING_LATE_PRICE" in world.build(el.LATE_T26)["eligibility"]["reasonCodes"]


def test_a_t26_primary_is_the_late_price(world):
    row = world.response(requested=KICK - timedelta(minutes=26))
    record = world.build(el.LATE_T26)
    assert record["market"]["originalBytesRef"]["ledgerRowId"] == str(row.id)
    assert record["horizon"]["comparisonCheckpointAtUtc"] == "2026-09-27T16:45:00Z"


def test_a_request_at_or_after_t21_is_never_used(world):
    world.response(requested=KICK - timedelta(minutes=21))
    assert "MISSING_LATE_PRICE" in world.build(el.LATE_T26)["eligibility"]["reasonCodes"]


def test_a_retry_counts_only_after_a_verified_uncharged_failure(world):
    world.response(requested=KICK - timedelta(minutes=26), status="FAILED", charged=0, archived=False)
    retry = world.response(requested=KICK - timedelta(minutes=23))
    assert world.build(el.LATE_T26)["market"]["originalBytesRef"]["ledgerRowId"] == str(retry.id)


@pytest.mark.parametrize("status, charged", [("FAILED", 1), ("FAILED", None), ("ORPHAN", None)])
def test_a_charged_or_unknown_primary_forbids_the_retry(world, status, charged):
    """FAILED+1 was charged; FAILED+None and a reservation with no terminal row are unknown."""

    world.response(requested=KICK - timedelta(minutes=26), status=status, charged=charged, archived=False)
    world.response(requested=KICK - timedelta(minutes=23))
    assert "MISSING_LATE_PRICE" in world.build(el.LATE_T26)["eligibility"]["reasonCodes"]


def test_a_t23_request_is_never_promoted_to_primary(world):
    """With no primary at all, a T-23 request is not a primary in disguise."""

    world.response(requested=KICK - timedelta(minutes=23))
    assert "MISSING_LATE_PRICE" in world.build(el.LATE_T26)["eligibility"]["reasonCodes"]


# ==========================================================================
# When a record may be written, and write-once behaviour
# ==========================================================================

def test_no_horizon_is_written_before_everything_it_depends_on_exists(world):
    assert el.writable_at(world.ref, el.EARLY_T24) == KICK - timedelta(hours=18) + el.SETTLING
    assert el.writable_at(world.ref, el.LATE_T26) == KICK - timedelta(minutes=15) + el.SETTLING
    assert el.writable_at(world.ref, el.MARKET_OPEN_PROXY) == KICK - timedelta(minutes=15) + el.SETTLING


def test_identical_content_is_adopted_and_a_closed_horizon_is_not_rebound(world, tmp_path):
    """Adopt identical bytes; refuse different bytes; write only an explicit correction."""

    world.response(requested=KICK - timedelta(hours=22))
    record = world.build(el.EARLY_T24)
    out = tmp_path / "out"
    ledger = el.Ledger(out)

    first = el.write_record(out, record, ledger=ledger, now=KICK - timedelta(hours=17))
    again = el.write_record(out, record, ledger=ledger, now=KICK - timedelta(hours=16))
    assert first["created"] and not again["created"] and again["adopted"] == first["sha256"]

    # Ordinary late evidence: refused, logged, and the original left untouched.
    changed = json.loads(json.dumps(record))
    changed["market"]["consensusHomeNoVig"] = "0.5"
    refused = el.write_record(out, changed, ledger=ledger, now=KICK - timedelta(hours=15))
    assert not refused["created"]
    assert refused["refused"] == "HORIZON_CLOSED_LATER_EVIDENCE_NOT_WRITTEN"
    assert len(sorted(out.rglob("*.json"))) == 1

    # An explicit correction: a new immutable revision naming the prior hash.
    corrected = el.write_record(out, changed, ledger=ledger, now=KICK - timedelta(hours=14),
                                correction_reason="RESPONSE_REPLACED_AFTER_PROVIDER_CORRECTION")
    assert corrected["created"]
    body = json.loads((out / corrected["file"]).read_text(encoding="utf-8"))
    assert body["lineage"]["supersedes"] == first["sha256"]
    assert body["lineage"]["priorRecordSha256"] == first["sha256"]
    assert body["lineage"]["correctionReason"] == "RESPONSE_REPLACED_AFTER_PROVIDER_CORRECTION"
    assert first["sha256"] in body["lineage"]["retainedChain"]
    kinds = [json.loads(line)["kind"] for line in
             (out / el.LEDGER_NAME).read_text(encoding="utf-8").splitlines() if line.strip()]
    assert kinds == ["WRITTEN", "HORIZON_CLOSED_LATER_EVIDENCE_NOT_WRITTEN", "CORRECTION_REVISION"]


def test_it_is_inert_when_disabled(session, settings):
    settings.nfl_early_line_capture_enabled = False
    assert el.run_early_line_capture(session, settings)["enabled"] is False


def test_a_pre_freeze_game_is_outside_the_universe(world):
    early = el.GameRef(**{**world.ref.__dict__, "starts_at": el.FROZEN_AT + timedelta(hours=29)})
    assert el.in_universe(world.ref) and not el.in_universe(early)


def test_a_second_request_after_an_unknown_charge_primary_is_never_used(world):
    """The case orphan-awareness exists for.

    The T-26 primary reserved and then died (no terminal row: charge unknown).
    A second request at T-25 -- still inside the primary window -- succeeded.
    Blind to the orphan, that success looks like the first primary and would be
    accepted: a second purchase after an unknown charge. It must not be used.
    """

    world.response(requested=KICK - timedelta(minutes=26), status="ORPHAN", archived=False)
    world.response(requested=KICK - timedelta(minutes=25))
    assert "MISSING_LATE_PRICE" in world.build(el.LATE_T26)["eligibility"]["reasonCodes"]


# ==========================================================================
# Model Studio's real producer evidence, read from its own retained objects
# ==========================================================================

EVIDENCE_DIR = CONTRACT_DIR / "producer-evidence"
FIXTURE_WORKSPACE = EVIDENCE_DIR / "contract-fixture" / "workspace"
#: The Rams game: nflverse says LA, canonical says LAR, and the manifest's
#: supportedProviderCodes is the only evidence that those are one team.
RAMS_KICKOFF = datetime(2026, 9, 28, 0, 20, tzinfo=UTC)
RAMS_FORECAST = ("asp-sports-game-forecast:sha256:"
                 "44473c8f297f0e80996c2925bd2e47202827629e4995329095d6431f798fb613")
RAMS_PACKAGE = "816f4505bad40f61f59417186e5b316f3d64692bf141334a2ffc2ad8b578f3af"

needs_evidence = pytest.mark.skipif(
    not (FIXTURE_WORKSPACE / "aisp-daily-forecast-bundles" / "sha256" / RAMS_PACKAGE
         / "bundle.json").is_file(),
    reason="producer evidence absent")


def _rams_ref(world) -> None:
    """Retarget the world's game at the Rams-at-Denver game in the real evidence."""

    world.game.starts_at = RAMS_KICKOFF
    world.session.flush()
    world.ref = el.GameRef(
        game_id=str(world.game.id), starts_at=RAMS_KICKOFF, status="SCHEDULED",
        home_team_id=str(world.teams[True].id), away_team_id=str(world.teams[False].id),
        home_abbr="DEN", away_abbr="LAR", sgo_event_ids=[EVENT], nflverse_ids=[],
        season=2026, season_type=None, week=None)


@needs_evidence
def test_the_schedule_manifest_supplies_the_week_aisp_cannot_retain(world):
    """Season type and week come from the producer's manifest, not from a model."""

    _rams_ref(world)
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LAR")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    assert (record["game"]["season"], record["game"]["seasonType"], record["game"]["week"]) == (
        2026, "REG", 3)
    assert record["producerEvidence"]["scheduleRowResolved"] is True
    assert record["producerEvidence"]["nflverseGameId"] == "2026_03_LA_DEN"
    assert "SCHEDULE_WEEK_NOT_RETAINED_PREGAME" not in record["eligibility"]["reasonCodes"]


@needs_evidence
def test_the_la_lar_crosswalk_succeeds_only_on_witnessed_evidence(world):
    """A provider saying LA for the Rams is accepted; the manifest witnesses it."""

    _rams_ref(world)
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    assert record["producerEvidence"]["supportedProviderCodes"]["away"] == ["LA", "LAR"]
    assert not [c for c in record["eligibility"]["reasonCodes"] if "ALIAS_UNSUPPORTED" in c]
    assert record["market"]["selectedSportsbookKey"] == "betmgm"


@needs_evidence
def test_an_unsupported_alias_fails_closed_even_for_the_same_city(world):
    """LAC is not the Rams, and nothing in the evidence says otherwise."""

    _rams_ref(world)
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LAC")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    assert record["eligibility"]["state"] == el.STATE_EXCLUDED
    assert "RESPONSE_AWAY_TEAM_ALIAS_UNSUPPORTED" in record["eligibility"]["reasonCodes"]
    assert record["market"]["selectedSportsbookKey"] is None


@needs_evidence
def test_the_contract_fixture_receipt_never_enrolls_anything(world, tmp_path):
    """Model Studio's own fixture declares itself non-operational. We refuse it.

    This is the check that catches a consumer which would have enrolled from a
    synthetic publication: the producer half is otherwise complete and verified,
    so only the status refuses it.
    """

    _rams_ref(world)
    exposure, evidence = el._current_exposure(
        FIXTURE_WORKSPACE, RAMS_FORECAST, package_sha256=RAMS_PACKAGE,
        package_bytes=_rams_package_bytes(), game=world.ref,
        row=el.schedule_row(el.load_schedule_identity(EVIDENCE_DIR)[0], world.ref),
        snapshot_version=13, cutoff=RAMS_KICKOFF - timedelta(hours=22),
        child_event_id="2026_03_LA_DEN")

    assert exposure is None
    assert evidence["detail"] == "CURRENT_EXPOSURE_RECEIPT_STATUS_NOT_OPERATIONAL"
    assert evidence["status"] == "SYNTHETIC_FIXTURE_NOT_OPERATIONAL"


def _rams_package_bytes() -> bytes:
    return (FIXTURE_WORKSPACE / "aisp-daily-forecast-bundles" / "sha256" / RAMS_PACKAGE
            / "bundle.json").read_bytes()


def _rams_child(forecast_id: str = RAMS_FORECAST) -> dict:
    package = json.loads(_rams_package_bytes())
    return next(c for c in package["forecasts"] if c["forecastId"] == forecast_id)


@needs_evidence
def test_the_receipts_membership_is_the_childs_own_content_address():
    """Membership is proved from bytes, not from the producer's assertion."""

    receipt = next(
        json.loads(p.read_bytes())
        for p in (FIXTURE_WORKSPACE / "aisp-daily-forecast-bundles" / "nfl-current-exposure"
                  / "receipts" / "sha256").glob("*/receipt.json")
        if json.loads(p.read_bytes()).get("status") == "SYNTHETIC_FIXTURE_NOT_OPERATIONAL")
    member = next(m for m in receipt["members"] if m["forecastId"] == RAMS_FORECAST)

    assert member["childSha256"] == el.canonical_child_sha256(_rams_package_bytes(),
                                                              RAMS_FORECAST)
    assert member["forecastPackageSha256"] == RAMS_PACKAGE


#: The one status a usable exposure receipt carries, now published by Model
#: Studio. The earlier local placeholder is gone: the consumer allowlists this
#: exact token, so a placeholder would fail closed like any unknown status.
OPERATIONAL_STATUS = "FUTURE_ONLY_PRODUCER_PUBLICATION_EVIDENCE"


def _write_receipt(root: Path, receipt: dict) -> None:
    """Replace the retained receipts with this one, at its own content address."""

    receipts = root / "aisp-daily-forecast-bundles" / "nfl-current-exposure" / "receipts" / "sha256"
    for directory in list(receipts.glob("*")):
        shutil.rmtree(directory)
    raw = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    (receipts / sha).mkdir(parents=True, exist_ok=True)
    (receipts / sha / "receipt.json").write_bytes(raw)


def _operational_evidence(destination: Path) -> dict:
    """Model Studio's real objects, with ONLY the fixture status replaced.

    Every byte that carries evidence -- the package, the child, the immutable
    index snapshot, the membership -- is Model Studio's own. The single edit is
    the status field, which their fixture deliberately sets to a value that must
    never bind, swapped for the published operational token so the rest of the
    chain can be exercised.
    """

    shutil.copytree(FIXTURE_WORKSPACE / "aisp-daily-forecast-bundles",
                    destination / "aisp-daily-forecast-bundles", dirs_exist_ok=True)
    receipts = destination / "aisp-daily-forecast-bundles" / "nfl-current-exposure" / "receipts" / "sha256"
    receipt = next(json.loads((d / "receipt.json").read_bytes()) for d in sorted(receipts.glob("*"))
                   if json.loads((d / "receipt.json").read_bytes()).get("status")
                   == "SYNTHETIC_FIXTURE_NOT_OPERATIONAL")
    receipt["status"] = OPERATIONAL_STATUS
    _write_receipt(destination, receipt)
    return receipt


def _adopt_real_forecast(world, receipt: dict, *, imported: datetime,
                         successor: datetime | None, snapshot: str = "CURRENT",
                         forecast_id: str = RAMS_FORECAST) -> None:
    """An AISP import of Model Studio's real package: the CONSUMER half."""

    child = _rams_child(forecast_id)
    bundle = ShadowForecastBundle(
        bundle_sha256=RAMS_PACKAGE, bundle_id="asp-sports-forecast-exchange:fixture",
        logical_uri=("asp-model-exchange://aisp-daily-forecast-bundles/sha256/"
                     + RAMS_PACKAGE + "/bundle.json"),
        source_fixture_id="f", slate_date=date(2026, 9, 27), league="NFL", sport="FOOTBALL",
        status="IMPORTED", research_shadow_only=True,
        generated_at=datetime(2026, 9, 22, 9, 32, 1, 940463, tzinfo=UTC), forecast_count=28,
        quote_count=0, candidate_count=0, imported_at=imported, contract_version="v1", evidence={})
    world.session.add(bundle)
    world.session.flush()
    world.session.add(ShadowForecast(
        bundle_id=bundle.id, forecast_id=forecast_id, engine_id="NFL_M1_XGBOOST",
        engine_version="NFL_M1_2026_RESEARCH_SHADOW_V1", model_artifact_sha256=uuid.uuid4().hex,
        simulator_id="s", simulator_version="1", simulator_seed=1, event_id="2026_03_LA_DEN",
        source_event_id="nflverse:2026_03_LA_DEN", event_date=date(2026, 9, 27), season=2026,
        home_team_id="DEN", away_team_id="LAR", is_ready=True, readiness_score=99.0,
        readiness_threshold=50.0, status="READY",
        full_home_win_probability=float(child["homeWinProbability"]),
        full_away_win_probability=float(child["awayWinProbability"]),
        full_expected_home_score=21.0, full_expected_away_score=20.0, distributions={},
        uncertainty={}, readiness_detail={}, forecast_schema_version="1", snapshot_type=snapshot,
        snapshot_version=child["snapshotVersion"], identity_evidence={}, lineage_detail={},
        canonical_game_id=world.game.id,
        available_as_of_utc=el.pilot.aware(child["availableAsOfUtc"]),
        forecast_cutoff_utc=el.pilot.aware(child["forecastCutoffUtc"]), created_at=imported))
    world.session.add(JobRun(
        job_name="consume-model-exchange-research-artifacts", status=RunStatus.SUCCEEDED,
        started_at=imported - timedelta(seconds=5), completed_at=imported + timedelta(seconds=5),
        parameters={}, result={"accepted": [{
            "bundle_sha256": RAMS_PACKAGE, "sport": "NFL", "forecasts": 28,
            "published_utc": (receipt.get("producerExposureUpperBoundUtc")
                              or "2026-09-22T21:32:14.480171Z"),
            "imported_at_utc": imported.isoformat().replace("+00:00", "Z")}]}))
    if successor is not None:
        world.session.add(JobRun(
            job_name="consume-model-exchange-research-artifacts", status=RunStatus.SUCCEEDED,
            started_at=successor, completed_at=successor + timedelta(seconds=2),
            parameters={}, result={"accepted": []}))
    world.session.flush()


@needs_evidence
def test_a_real_early_current_forecast_binds_on_producer_and_consumer_evidence(world):
    """The study's whole point, end to end, on Model Studio's own objects.

    Producer half: an atomic index-exposure receipt whose index snapshot, package
    membership and child content address all verify. Consumer half: AISP's own
    import run, with the successor run that bounds when we could first have seen
    it. Both must pass, and the binding takes the LATER of the two.
    """

    _rams_ref(world)
    receipt = _operational_evidence(world.exchange)
    imported = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    _adopt_real_forecast(world, receipt, imported=imported,
                         successor=imported + timedelta(minutes=1))
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    forecast = record["forecast"]
    assert forecast is not None, record["eligibility"]["reasonCodes"]
    assert record["eligibility"]["forecastDisposition"] == "BOUND"
    assert forecast["snapshotType"] == "CURRENT" and forecast["snapshotVersion"] == 13
    assert forecast["packageSha256"] == RAMS_PACKAGE
    assert (forecast["homeProbabilityRaw"], forecast["awayProbabilityRaw"],
            forecast["tieProbabilityRaw"]) == ("0.406", "0.5463", "0.0477")
    # Availability is the LATER of the two halves. Here the producer exposure
    # is later than our import, so the producer clock governs.
    assert forecast["exposedAtUtc"] == receipt["producerExposureUpperBoundUtc"]
    assert forecast["consumerObservedOrImportCompletedAtUtc"] == "2026-09-22T10:01:00Z"
    assert forecast["availabilityAtUtc"] == receipt["producerExposureUpperBoundUtc"]
    assert forecast["exposureEvidence"]["detail"] == "VERIFIED"
    assert forecast["exposureEvidence"]["indexCurrentBundleCount"] == 1
    assert forecast["exposureEvidence"]["producerRevision"] == (
        "078e5dafd1f61fd42b7a36e4d6a4e8073be0c50b")
    # And a real early disagreement can finally be computed.
    assert record["hypotheticalDecision"]["side"] in {"HOME", "AWAY"}
    assert record["hypotheticalDecision"]["position"] == "SHADOW_ONLY"


@needs_evidence
def test_without_the_consumer_half_the_producer_receipt_alone_never_binds(world):
    """A receipt proves publication. It does not prove we ever saw it."""

    _rams_ref(world)
    receipt = _operational_evidence(world.exchange)
    _adopt_real_forecast(world, receipt, imported=datetime(2026, 9, 22, 10, 0, tzinfo=UTC),
                         successor=None)
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    assert record["forecast"] is None
    assert "CONSUMER_OBSERVATION_BOUND_NOT_YET_ESTABLISHED" in record["eligibility"]["reasonCodes"]


@needs_evidence
@pytest.mark.parametrize("break_it, expected", [
    (lambda r: r.update(currentIndexSha256="0" * 64), "CURRENT_INDEX_SNAPSHOT_UNVERIFIED"),
    (lambda r: _member(r).update(childSha256="0" * 64), "CHILD_CONTENT_ADDRESS_DISAGREES"),
    (lambda r: _member(r).update(snapshotType="FINAL_PREGAME"),
     "MEMBER_IS_NOT_A_CURRENT_SNAPSHOT"),
    (lambda r: _member(r).update(scheduledStartUtc="2026-09-28T03:20:00.000000Z"),
     "MEMBER_KICKOFF_DISAGREES"),
    (lambda r: _member(r).update(awayTeamId="LAC"), "MEMBER_AWAY_TEAM_ALIAS_UNSUPPORTED"),
    (lambda r: r.update(producerExposureUpperBoundUtc=None), "CURRENT_EXPOSURE_CLOCK_MISSING"),
    (lambda r: r.update(status="SYNTHETIC_FIXTURE_NOT_OPERATIONAL"),
     "CURRENT_EXPOSURE_RECEIPT_STATUS_NOT_OPERATIONAL"),
])
def test_each_piece_of_producer_evidence_is_load_bearing(world, break_it, expected):
    """Break one thing in the receipt and the forecast stops binding."""

    _rams_ref(world)
    receipt = _operational_evidence(world.exchange)
    break_it(receipt)
    _write_receipt(world.exchange, receipt)
    imported = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    _adopt_real_forecast(world, receipt, imported=imported,
                         successor=imported + timedelta(minutes=1))
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    assert record["forecast"] is None
    assert expected in record["eligibility"]["reasonCodes"], record["eligibility"]["reasonCodes"]


def _member(receipt: dict) -> dict:
    return next(m for m in receipt["members"] if m["forecastId"] == RAMS_FORECAST)


@needs_evidence
def test_availability_is_the_later_half_when_the_consumer_saw_it_afterwards(world):
    """Publication first, observation later: our own clock governs."""

    _rams_ref(world)
    receipt = _operational_evidence(world.exchange)
    imported = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)          # well after publication
    _adopt_real_forecast(world, receipt, imported=imported,
                         successor=imported + timedelta(minutes=1))
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    forecast = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)["forecast"]

    assert forecast is not None
    assert forecast["exposedAtUtc"] == receipt["producerExposureUpperBoundUtc"]
    assert forecast["availabilityAtUtc"] == "2026-09-23T06:01:00Z"


# ==========================================================================
# The producer handoff of 2026-09-22: version, horizon membership, independence
# ==========================================================================

#: The receipt Model Studio's fixture manifest names as authoritative.
AUTHORITATIVE_RECEIPT = "64c9e2db40acf0f5632e43dad03dc25530ff0ffd53cc24c25875bc6a3925d2a1"
FIXTURE_MANIFEST_SHA = "41d4a293b552babf5bf18f06d1636366af679f59b081f760d9fe538b9fa64841"


@needs_evidence
def test_the_handoffs_stated_identities_are_what_is_on_disk():
    """Re-hash what the handoff claims, rather than taking its word."""

    claims = {
        EVIDENCE_DIR / "CONTRACT.md":
            "30b65bb895db98160a364d095cf3f7fcf6b01cd8ed7df4f40152aabbf85bdd2a",
        (EVIDENCE_DIR / "schedule" / "sha256"
         / "f80236ce1e9de10088adbd5b0c244169e13ebd5554754370aa0b5234811447cb"
         / "SCHEDULE_IDENTITY.json"):
            "f80236ce1e9de10088adbd5b0c244169e13ebd5554754370aa0b5234811447cb",
        (EVIDENCE_DIR / "contract-fixture" / "objects" / "sha256" / FIXTURE_MANIFEST_SHA
         / "FIXTURE_MANIFEST.json"): FIXTURE_MANIFEST_SHA,
        (FIXTURE_WORKSPACE / "aisp-daily-forecast-bundles" / "nfl-current-exposure"
         / "receipts" / "sha256" / AUTHORITATIVE_RECEIPT / "receipt.json"):
            AUTHORITATIVE_RECEIPT,
        (FIXTURE_WORKSPACE / "aisp-daily-forecast-bundles" / "sha256" / RAMS_PACKAGE
         / "bundle.json"): RAMS_PACKAGE,
    }
    for path, expected in claims.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, path.name
    # And the child address the manifest states.
    assert el.canonical_child_sha256(_rams_package_bytes(), RAMS_FORECAST) == (
        "a047cacce1e7514fcfced3ecc6b82ac82ec884832d482a78b93d7b1677704995")


@needs_evidence
def test_the_early_snapshot_type_is_not_used_at_the_early_horizon(world):
    """The package carries an EARLY-typed child too. It is excluded BY TYPE.

    Version numbers are not comparable across snapshot types, so an EARLY v1
    must never win or lose a version comparison against a CURRENT v13. The
    exposure receipts cover only CURRENT members in any case.
    """

    package = json.loads(_rams_package_bytes())
    kinds = {c["snapshotType"] for c in package["forecasts"]
             if c["eventId"] == "2026_03_LA_DEN"}
    assert kinds == {"CURRENT", "EARLY"}

    _rams_ref(world)
    receipt = _operational_evidence(world.exchange)
    imported = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
    _adopt_real_forecast(world, receipt, imported=imported,
                         successor=imported + timedelta(minutes=1),
                         snapshot="EARLY", forecast_id=(
                             "asp-sports-game-forecast:sha256:"
                             "774ef3dc2181dd87c5291a42d1af84029fc3bec14939fe2ea894ea9fb044df1c"))
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    assert record["forecast"] is None
    assert "FORECAST_SNAPSHOT_TYPE_NOT_USED_AT_THIS_HORIZON" in record["eligibility"]["reasonCodes"]
    # The rest of the observation still records: the market side is intact.
    assert record["market"]["selectedSportsbookKey"] == "betmgm"
    assert record["game"]["week"] == 3


@needs_evidence
def test_a_receipt_for_a_different_version_does_not_attest_this_one(world):
    _rams_ref(world)
    receipt = _operational_evidence(world.exchange)
    _member(receipt)["snapshotVersion"] = 12
    _write_receipt(world.exchange, receipt)
    imported = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
    _adopt_real_forecast(world, receipt, imported=imported,
                         successor=imported + timedelta(minutes=1))
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    assert record["forecast"] is None
    assert "MEMBER_SNAPSHOT_VERSION_DISAGREES" in record["eligibility"]["reasonCodes"]


@needs_evidence
def test_a_forecast_replaced_before_the_horizon_is_no_longer_current(world):
    """Model Studio's REPLACED_MEMBERSHIP case, from the consumer's side.

    A first exposure is not enough. The index in force at the horizon is the one
    from the latest publication at or before it, and if this child has dropped
    out of that one, the producer has replaced it and it is no longer the
    model's view at the horizon.
    """

    _rams_ref(world)
    receipt = _operational_evidence(world.exchange)
    # A LATER publication, otherwise identical, that no longer carries this child.
    replacement = json.loads(json.dumps(receipt))
    replacement["members"] = [m for m in replacement["members"]
                              if m["forecastId"] != RAMS_FORECAST]
    replacement["previousIndexSha256"] = receipt["currentIndexSha256"]
    replacement["producerExposureUpperBoundUtc"] = "2026-09-24T00:00:00Z"
    receipts = (world.exchange / "aisp-daily-forecast-bundles" / "nfl-current-exposure"
                / "receipts" / "sha256")
    raw = json.dumps(replacement, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    (receipts / sha).mkdir(parents=True, exist_ok=True)
    (receipts / sha / "receipt.json").write_bytes(raw)

    imported = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
    _adopt_real_forecast(world, receipt, imported=imported,
                         successor=imported + timedelta(minutes=1))
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    assert record["forecast"] is None
    assert "NOT_CURRENT_IN_INDEX_AT_HORIZON" in record["eligibility"]["reasonCodes"]


@needs_evidence
def test_the_producers_synthetic_event_id_is_never_adopted(world):
    """The fixture manifest states a synthetic SGO event id. We never read it.

    Model Studio leaves the AISP UUID and the SportsGameOdds event id for us to
    bind from our own retained evidence, and says a null is unresolved. The one
    id their fixture does state is synthetic, and adopting it would fabricate an
    identity that no provider response ever carried.
    """

    manifest = json.loads(
        (EVIDENCE_DIR / "contract-fixture" / "objects" / "sha256" / FIXTURE_MANIFEST_SHA
         / "FIXTURE_MANIFEST.json").read_bytes())
    assert manifest["fixtureGameIdentity"]["sportsGameOddsEventId"] == (
        "SYNTHETIC_EVENT_LA_DEN_ONLY")
    assert manifest["fixtureGameIdentity"]["aispCanonicalGameUuid"] is None

    _rams_ref(world)
    receipt = _operational_evidence(world.exchange)
    imported = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
    _adopt_real_forecast(world, receipt, imported=imported,
                         successor=imported + timedelta(minutes=1))
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    blob = json.dumps(record)
    assert "SYNTHETIC_EVENT_LA_DEN_ONLY" not in blob
    # The identities we DO carry come from AISP's own schedule and the original
    # provider response, not from any producer document.
    assert record["game"]["gameId"] == str(world.game.id)
    assert record["market"]["providerEventId"] == EVENT
    assert record["market"]["responseIdentity"]["providerAwayShort"] == "LA"


@needs_evidence
def test_no_receipt_yet_leaves_the_forecast_unbound_and_records_everything_else(world):
    """No natural publication has run this code yet, which must not lose a record.

    Model Studio's handoff says operating exposure is unverified until a real
    publication happens. Until then every EARLY forecast stays unbound -- and
    the observation is still written, carrying the market side, the schedule
    identity and an explicit reason.
    """

    _rams_ref(world)
    imported = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
    _adopt_real_forecast(world, {"producerExposureUpperBoundUtc": "2026-09-22T21:34:37.788511Z"},
                         imported=imported, successor=imported + timedelta(minutes=1))
    shutil.copytree(FIXTURE_WORKSPACE / "aisp-daily-forecast-bundles" / "sha256",
                    world.exchange / "aisp-daily-forecast-bundles" / "sha256",
                    dirs_exist_ok=True)          # the package, but NO exposure receipts
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    assert record["forecast"] is None
    assert record["eligibility"]["forecastDisposition"] == "MISSING_FORECAST"
    assert "CURRENT_INDEX_EXPOSURE_RECEIPT_ABSENT" in record["eligibility"]["reasonCodes"]
    # Everything that does not depend on the receipt is still recorded.
    assert record["eligibility"]["state"] == el.STATE_PREDICTIVE
    assert record["market"]["selectedSportsbookKey"] == "betmgm"
    assert (record["game"]["season"], record["game"]["seasonType"],
            record["game"]["week"]) == (2026, "REG", 3)
    assert record["producerEvidence"]["nflverseGameId"] == "2026_03_LA_DEN"


# ==========================================================================
# Model Studio's final three corrections
# ==========================================================================

@needs_evidence
@pytest.mark.parametrize("break_it, expected", [
    (lambda r: r.pop("status"), "CURRENT_EXPOSURE_RECEIPT_STATUS_NOT_OPERATIONAL"),
    (lambda r: r.update(status="SOMETHING_NOBODY_ANTICIPATED"),
     "CURRENT_EXPOSURE_RECEIPT_STATUS_NOT_OPERATIONAL"),
    (lambda r: r.update(status="LOCAL_TEST_PLACEHOLDER_STATUS_PENDING_MODEL_STUDIO"),
     "CURRENT_EXPOSURE_RECEIPT_STATUS_NOT_OPERATIONAL"),
    (lambda r: r.update(contract="SOME_OTHER_CONTRACT_V1"),
     "CURRENT_INDEX_EXPOSURE_RECEIPT_ABSENT"),
    (lambda r: r.pop("futureOnlyAfterUtc"),
     "CURRENT_EXPOSURE_FUTURE_ONLY_THRESHOLD_MISSING"),
    (lambda r: r.update(futureOnlyAfterUtc="not-a-clock"),
     "CURRENT_EXPOSURE_FUTURE_ONLY_THRESHOLD_MISSING"),
    (lambda r: r.update(futureOnlyAfterUtc="2026-09-23T00:00:00Z"),
     "CURRENT_EXPOSURE_RECEIPT_RETROACTIVE"),
])
def test_the_status_allowlist_fails_closed_on_anything_unexpected(world, break_it, expected):
    """An allowlist, not a refusal list: only the published token is usable."""

    _rams_ref(world)
    receipt = _operational_evidence(world.exchange)
    break_it(receipt)
    _write_receipt(world.exchange, receipt)
    imported = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
    _adopt_real_forecast(world, receipt, imported=imported,
                         successor=imported + timedelta(minutes=1))
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    assert record["forecast"] is None
    assert expected in record["eligibility"]["reasonCodes"], record["eligibility"]["reasonCodes"]


@needs_evidence
def test_an_unusable_receipt_cannot_decide_what_was_in_force(world):
    """The allowlist governs the horizon scan too, not just the chosen receipt.

    A later publication that drops this child would mark it replaced -- but only
    if that publication is itself usable evidence. A synthetic one must not be
    able to unbind a forecast any more than it could bind one.
    """

    _rams_ref(world)
    receipt = _operational_evidence(world.exchange)
    # A later publication WITHOUT the child, but not usable evidence.
    unusable = json.loads(json.dumps(receipt))
    unusable["members"] = [m for m in unusable["members"] if m["forecastId"] != RAMS_FORECAST]
    unusable["producerExposureUpperBoundUtc"] = "2026-09-24T00:00:00Z"
    unusable["status"] = "SYNTHETIC_FIXTURE_NOT_OPERATIONAL"
    receipts = (world.exchange / "aisp-daily-forecast-bundles" / "nfl-current-exposure"
                / "receipts" / "sha256")
    raw = json.dumps(unusable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    (receipts / hashlib.sha256(raw).hexdigest()).mkdir(parents=True, exist_ok=True)
    (receipts / hashlib.sha256(raw).hexdigest() / "receipt.json").write_bytes(raw)

    imported = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
    _adopt_real_forecast(world, receipt, imported=imported,
                         successor=imported + timedelta(minutes=1))
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    # Still bound: the unusable receipt is not evidence of anything.
    assert record["forecast"] is not None, record["eligibility"]["reasonCodes"]
    assert record["forecast"]["exposureEvidence"]["stillCurrentAtHorizon"] is True


@needs_evidence
@pytest.mark.parametrize("break_it, expected", [
    (lambda r: _member(r).update(canonicalGameId="2026_03_KC_MIA"),
     "FORECAST_IDENTITY_THREE_WAY_DISAGREES"),
])
def test_the_three_way_identity_must_agree_exactly(world, break_it, expected):
    """Member canonicalGameId, child eventId and schedule nflverseGameId.

    Two of the three agreeing proves nothing: the producer can be perfectly
    consistent with itself about a game that is not this one.
    """

    _rams_ref(world)
    receipt = _operational_evidence(world.exchange)
    break_it(receipt)
    _write_receipt(world.exchange, receipt)
    imported = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
    _adopt_real_forecast(world, receipt, imported=imported,
                         successor=imported + timedelta(minutes=1))
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    assert record["forecast"] is None
    assert expected in record["eligibility"]["reasonCodes"], record["eligibility"]["reasonCodes"]


@needs_evidence
def test_no_unique_schedule_row_leaves_the_forecast_unbound(world):
    """Without a unique point-in-time row there is no third identity to agree."""

    _rams_ref(world)
    world.ref = el.GameRef(
        game_id=world.ref.game_id, starts_at=RAMS_KICKOFF, status="SCHEDULED",
        home_team_id=world.ref.home_team_id, away_team_id=world.ref.away_team_id,
        home_abbr="DEN", away_abbr="SEA",          # no manifest row joins this pair
        sgo_event_ids=[EVENT], nflverse_ids=[], season=2026)
    receipt = _operational_evidence(world.exchange)
    imported = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
    _adopt_real_forecast(world, receipt, imported=imported,
                         successor=imported + timedelta(minutes=1))
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="SEA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    assert record["forecast"] is None
    assert record["producerEvidence"]["scheduleRowResolved"] is False
    assert "SCHEDULE_IDENTITY_ROW_UNRESOLVED" in record["eligibility"]["reasonCodes"]


@needs_evidence
def test_a_governing_index_that_no_longer_holds_the_package_unbinds_it(world):
    """At the horizon the governing index must still carry this exact package.

    A later usable publication that keeps the forecast id but points at an index
    the package has left is not evidence that the package was still current.
    """

    _rams_ref(world)
    receipt = _operational_evidence(world.exchange)
    later = json.loads(json.dumps(receipt))
    later["producerExposureUpperBoundUtc"] = "2026-09-24T00:00:00Z"
    later["currentIndexSha256"] = "0" * 64          # an index nobody retained
    receipts = (world.exchange / "aisp-daily-forecast-bundles" / "nfl-current-exposure"
                / "receipts" / "sha256")
    raw = json.dumps(later, sort_keys=True, separators=(",", ":")).encode("utf-8")
    (receipts / hashlib.sha256(raw).hexdigest()).mkdir(parents=True, exist_ok=True)
    (receipts / hashlib.sha256(raw).hexdigest() / "receipt.json").write_bytes(raw)

    imported = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
    _adopt_real_forecast(world, receipt, imported=imported,
                         successor=imported + timedelta(minutes=1))
    world.response(requested=RAMS_KICKOFF - timedelta(hours=22),
                   home_short="DEN", away_short="LA")
    record = world.build(el.EARLY_T24, evidence_dir=EVIDENCE_DIR)

    assert record["forecast"] is None
    assert "NOT_CURRENT_IN_INDEX_AT_HORIZON" in record["eligibility"]["reasonCodes"]


def test_two_retained_provider_event_ids_keep_the_response_but_price_nothing(world):
    """Duplicates: the response and its whole population stay, ineligible."""

    world.ref = el.GameRef(
        game_id=world.ref.game_id, starts_at=KICK, status="SCHEDULED",
        home_team_id=world.ref.home_team_id, away_team_id=world.ref.away_team_id,
        home_abbr="HOM", away_abbr="AWY", sgo_event_ids=[EVENT, "sgoEVENT0002"],
        nflverse_ids=[NFLVERSE], season=2026)
    world.response(requested=KICK - timedelta(hours=22))
    record = world.build(el.EARLY_T24)

    market = record["market"]
    assert market["providerEventIdDisposition"] == "PROVIDER_EVENT_ID_NOT_UNIQUE_FOR_GAME"
    assert market["retainedProviderEventIds"] == [EVENT, "sgoEVENT0002"]
    # The response is RETAINED, with every book still on the record.
    assert market["providerResponseSha256"] is not None
    assert len(market["candidates"]) == 3
    assert market["population"]["booksInResponse"] == 3
    # And nothing is priced, with the reason on every candidate.
    assert all(not c["predictiveEligible"] for c in market["candidates"])
    assert all("PROVIDER_EVENT_ID_NOT_UNIQUE_FOR_GAME" in c["reasonCodes"]
               for c in market["candidates"])
    assert market["selectedSportsbookKey"] is None
    assert record["eligibility"]["state"] == el.STATE_EXCLUDED


def test_no_retained_provider_event_id_prices_nothing_and_says_so(world):
    world.ref = el.GameRef(
        game_id=world.ref.game_id, starts_at=KICK, status="SCHEDULED",
        home_team_id=world.ref.home_team_id, away_team_id=world.ref.away_team_id,
        home_abbr="HOM", away_abbr="AWY", sgo_event_ids=[], nflverse_ids=[NFLVERSE],
        season=2026)
    world.response(requested=KICK - timedelta(hours=22))
    record = world.build(el.EARLY_T24)

    assert record["market"]["providerEventIdDisposition"] == "PROVIDER_EVENT_ID_ABSENT_FOR_GAME"
    assert record["market"]["providerResponseSha256"] is None
    assert record["eligibility"]["state"] == el.STATE_EXCLUDED
    assert "PROVIDER_EVENT_ID_ABSENT_FOR_GAME" in record["eligibility"]["reasonCodes"]


def test_a_mismatched_event_keeps_the_response_and_never_substitutes_another(world):
    """The retained id is unique but the response carries a different event."""

    world.response(requested=KICK - timedelta(hours=22), event_id="sgoSOMEONEELSE")
    record = world.build(el.EARLY_T24)

    # No response is adopted for this game, and no other game's event is used.
    assert record["market"]["providerEventId"] is None
    assert record["market"]["providerEventIdDisposition"] == "EXACTLY_ONE_RETAINED"
    assert record["market"]["candidates"] == []
    assert record["eligibility"]["state"] == el.STATE_UNPRICED


def test_the_newest_version_is_chosen_within_one_snapshot_type(world):
    """v2 beats v1; neither is compared against a different snapshot type."""

    for version, minutes in ((1, 50), (3, 45), (2, 47)):
        world.forecast(snapshot="FINAL_PREGAME", version=version,
                       exposed=KICK - timedelta(minutes=minutes),
                       created=KICK - timedelta(minutes=minutes - 1),
                       readiness_visible=KICK - timedelta(minutes=minutes - 2))
    world.response(requested=KICK - timedelta(minutes=26))
    forecast = world.build(el.LATE_T26)["forecast"]

    assert forecast["snapshotVersion"] == 3
    assert forecast["snapshotType"] == "FINAL_PREGAME"


def test_the_eligible_set_is_single_typed_by_construction(world):
    """The version comparison never crosses snapshot types.

    An EARLY v1 and a CURRENT v13 are not two versions of one thing. Each
    horizon filters to a single type BEFORE any version comparison, so ordering
    is always within-type. Both halves are pinned: LATE sees only FINAL_PREGAME
    even when a CURRENT of a higher version exists, and EARLY sees only CURRENT.
    """

    world.forecast(snapshot="CURRENT", version=99,
                   exposed=KICK - timedelta(minutes=50),
                   created=KICK - timedelta(minutes=49),
                   readiness_visible=KICK - timedelta(minutes=48))
    world.forecast(snapshot="FINAL_PREGAME", version=2,
                   exposed=KICK - timedelta(minutes=45),
                   created=KICK - timedelta(minutes=44),
                   readiness_visible=KICK - timedelta(minutes=43))
    world.response(requested=KICK - timedelta(minutes=26))

    forecast = world.build(el.LATE_T26)["forecast"]
    # The CURRENT v99 is not even a candidate, so it cannot win on version.
    assert forecast["snapshotType"] == "FINAL_PREGAME"
    assert forecast["snapshotVersion"] == 2
