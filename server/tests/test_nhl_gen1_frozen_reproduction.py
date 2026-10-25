"""The frozen NHL Gen1 model in AISP: byte-pinned state, the producer's crosswalk, and the reproduction proof.

The schedule bytes are subsets cut from the producer's retained real captures
(``tests/fixtures/nhl_inprocess``); the producer's own prepared forecast objects
for those slates are the expected outputs. Hermetic: no network, no producer files.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import UTC, datetime

import pytest

from app.nhl import contract as c
from app.nhl import gen1_frozen as frozen
from app.nhl import inprocess_generator as gen

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "nhl_inprocess"
SLATES = ("20261010", "20261012", "20261013")
TOLERANCE = 1e-9


def _expected(tag: str) -> dict:
    return json.loads((FIX / f"expected_{tag}.json").read_text(encoding="utf-8"))


def _schedule(tag: str) -> bytes:
    return (FIX / f"schedule_{tag}_subset.csv").read_bytes()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ===================================================================== frozen ==
def test_the_frozen_state_and_alias_subset_are_byte_pinned():
    raw = frozen.SELECTED_MODEL_PATH.read_bytes()
    assert _sha(raw) == "3f7981aac2bfee6f3d05b7e1d099a3342cc9057d09e13b09aaf4c81b7f720417"
    assert frozen.SELECTED_MODEL_SHA256 == gen.CONFIGURATION_SHA256 == _sha(raw)
    assert _sha(frozen.ALIASES_PATH.read_bytes()) == frozen.ALIASES_SUBSET_SHA256
    state = frozen.selected_state()
    assert len(state["teamIds"]) == 34 and len(state["theta"]) == 73 == 2 * 34 + 5
    assert state["fitCutoff"] == "2026-09-28T00:00:00" and state["selectedBaseline"] == "REGULATION_POISSON"
    assert frozen.handoff_sha256() == "e9802f904188452609ac8d04f5be4596312d051bcd7a322358d3c3604629b05a"


def test_a_changed_frozen_file_fails_closed(monkeypatch, tmp_path):
    bad = tmp_path / "selected_model.json"
    bad.write_bytes(frozen.SELECTED_MODEL_PATH.read_bytes() + b" ")
    monkeypatch.setattr(frozen, "SELECTED_MODEL_PATH", bad)
    frozen._state_bytes.cache_clear()
    try:
        with pytest.raises(frozen.FrozenArtifactChanged):
            frozen.selected_state()
    finally:
        monkeypatch.undo()
        frozen._state_bytes.cache_clear()


def test_the_crosswalk_is_the_producers_including_utah_mammoth_68():
    expected = json.loads((FIX / "producer_team_crosswalk.json").read_text(encoding="utf-8"))
    assert expected["aliasSourceSha256"] == frozen.ALIASES_SOURCE_SHA256
    assert frozen.team_crosswalk() == expected["teamCrosswalk"]
    uta = frozen.team_crosswalk()["UTA"]
    assert uta == {"sourceTeamId": "68", "canonicalTeamId": "nhl:team:68", "triCode": "UTA", "name": "Utah Mammoth"}
    assert all(t["sourceTeamId"] != "59" for t in frozen.team_crosswalk().values())
    # The vendored subset still holds the raw 2024 alias (Utah Hockey Club 59): the correction is applied, not baked in.
    assert b"59,2024,UTA,Utah Hockey Club" in frozen.ALIASES_PATH.read_bytes()


def test_production_code_never_imports_pandas():
    for path in (frozen.ROOT / "model.py", frozen.ROOT / "__init__.py", pathlib.Path(gen.__file__)):
        text = path.read_text(encoding="utf-8")
        assert "import pandas" not in text and "from pandas" not in text, path


# ============================================================== reproduction ==
@pytest.mark.parametrize("tag", SLATES)
def test_reproduction_matches_the_producers_prepared_forecasts(tag):
    """Same bytes, same target date, same observed clock -> the producer's features and probabilities.

    Probabilities and expected goals must match within 1e-9 (observed: <= 4.5e-16, the
    Skellam ulp difference between scipy 1.17 and 1.18); with the producer's probability
    block swapped in, every other byte of every document is identical.
    """

    expected = _expected(tag)
    raw = _schedule(tag)
    assert _sha(raw) == expected["subsetCsvSha256"]
    started = datetime.fromisoformat(expected["preparedCreatedUtc"].replace("Z", "+00:00"))
    slate = gen.prepare_slate(gen.parse_schedule(raw), expected["targetDate"], observed=expected["observedUtc"],
                              started=started, crosswalk=frozen.team_crosswalk(),
                              model_team_ids=frozen.selected_state()["teamIds"])
    assert slate.rows == expected["inputFeatures"]
    assert [(e["sourceGameId"], e["reason"]) for e in slate.excluded] == \
        [(e["sourceGameId"], e["reason"]) for e in expected["excluded"]]
    predictions = gen.predict(slate.rows)
    assert len(predictions) == len(expected["forecasts"]) > 0
    worst = 0.0
    for g, p, want in zip(slate.games, predictions, expected["forecasts"]):
        theirs = want["document"]
        mine = gen.forecast_document(
            g, p, crosswalk=frozen.team_crosswalk(), observed=expected["observedUtc"],
            created=expected["preparedCreatedUtc"], published=None,
            evidence_ref=theirs["provenance"]["producerEvidenceRef"],
            evidence_mode=theirs["provenance"]["evidenceMode"], supersedes=None, handoff_sha256=frozen.handoff_sha256())
        pairs = [(mine["probabilities"]["final"][k], theirs["probabilities"]["final"][k]) for k in ("home", "away")]
        pairs += [(mine["probabilities"]["regulation"][k], theirs["probabilities"]["regulation"][k])
                  for k in ("home", "tie", "away")]
        pairs += [(mine["goals"]["regulation"][k], theirs["goals"]["regulation"][k])
                  for k in ("expectedHome", "expectedAway")]
        pairs += [(mine["probabilities"]["overtime"]["pOvertime"], theirs["probabilities"]["overtime"]["pOvertime"])]
        diff = max(abs(a - b) for a, b in pairs)
        worst = max(worst, diff)
        assert diff <= TOLERANCE, (g["game_id"], diff)
        assert abs(sum(mine["probabilities"]["final"].values()) - 1.0) <= 1e-12
        # Structure: with the producer's numbers swapped in, the bytes are the producer's object.
        mine["probabilities"] = theirs["probabilities"]
        mine["forecastId"] = c.forecast_identity(mine)
        assert mine["forecastId"] == want["forecastId"]
        assert _sha(c.canonical_bytes(mine)) == want["objectSha256"]
    print(f"\n{tag}: {len(predictions)} games, max abs difference vs producer = {worst:.3e}")


def test_the_parser_never_reads_a_score_column():
    rows = gen.parse_schedule(_schedule("20261013"))
    assert set(rows[0]) == set(gen.SCHEDULE_COLUMNS)
    with_scores = (b"game_id,season_full,game_type,game_date,game_time,home_team_abbr,away_team_abbr,home_team_name,"
                   b"away_team_name,home_score,away_score,game_state,venue\n"
                   b"2026020200,20262027,R,2026-10-20,2026-10-20T23:00:00Z,BOS,TOR,Boston,Toronto,9,0,FUT,TD Garden\n")
    parsed = gen.parse_schedule(with_scores)
    assert "home_score" not in parsed[0] and parsed[0]["venue"] == "TD Garden"


def test_started_non_pregame_and_unknown_teams_are_skipped_with_reasons():
    head = ("game_id,season_full,game_type,game_date,game_time,home_team_abbr,away_team_abbr,home_team_name,"
            "away_team_name,game_state,venue\n")
    body = ("2026020301,20262027,R,2026-10-20,2026-10-20T16:00:00Z,BOS,TOR,Boston,Toronto,FUT,A\n"
            "2026020302,20262027,R,2026-10-20,2026-10-20T23:00:00Z,BOS,XYZ,Boston,Nowhere,FUT,A\n"
            "2026020303,20262027,R,2026-10-20,2026-10-20T23:30:00Z,NYR,PIT,New York,Pittsburgh,PPD,B\n"
            "2026010304,20262027,PR,2026-10-20,2026-10-20T23:45:00Z,NYR,PIT,New York,Pittsburgh,FUT,B\n"
            "2026020305,20262027,R,2026-10-20,2026-10-21T00:00:00Z,COL,PIT,Colorado,Pittsburgh,FUT,C\n")
    slate = gen.prepare_slate(gen.parse_schedule((head + body).encode()), "2026-10-20",
                              observed="2026-10-20T17:00:00Z", started=datetime(2026, 10, 20, 17, tzinfo=UTC),
                              crosswalk=frozen.team_crosswalk(), model_team_ids=frozen.selected_state()["teamIds"])
    reasons = {e["sourceGameId"]: e["reason"] for e in slate.excluded}
    assert reasons == {"2026020301": "ALREADY_STARTED_OR_NOT_PREGAME", "2026020302": "TEAM_NOT_IN_CROSSWALK:XYZ",
                       "2026020303": "ALREADY_STARTED_OR_NOT_PREGAME",
                       "2026010304": "NOT_A_2026_27_REGULAR_SEASON_GAME"}
    assert [r["game_id"] for r in slate.rows] == ["2026020305"]
    assert slate.rows[0]["home_rest_missing"] == 1 and slate.rows[0]["home_rest_days"] == 7
