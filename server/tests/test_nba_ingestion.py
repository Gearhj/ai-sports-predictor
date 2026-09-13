"""NBA ingestion: only the boundaries this integration actually changes.

Six things, and nothing else:

  * the transport cannot be constructed while collection is unpermitted, and
    entitlement is never inferred from a credential;
  * identity reconciles -- teams, players and games resolve to stored rows
    rather than being invented from names;
  * final score, period lines and player points agree, and a disagreement is
    REPORTED rather than repaired;
  * attribution is right, including a player appearing for the club they played
    for and not their current one;
  * a repeated import writes nothing, and a provider correction appends a
    revision instead of editing;
  * fixture data is isolated: it says FIXTURE on every row and cannot be
    written as REAL.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.data_sources.nba_sportsdataio import (
    AUTH_HEADER, Envelope, NbaAdapterError, NbaSportsDataIoSource,
    parse_box_score, parse_games, parse_players, parse_teams,
)
from app.models.nba import (
    NbaGame, NbaPlayer, NbaPlayerGameStat, NbaRosterMembership,
    NbaSourcePayload, NbaTeam, NbaTeamGameStat,
)
from app.nba.entitlement import DataMode, Permission, assess
from app.nba.intake import IntakeOutcome, NbaIntake, NbaIntakeError, reconcile_scores

FIXTURES = Path(__file__).parent / "fixtures" / "nba"
NOW = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)


def envelope(name: str, endpoint_key: str, **parameters) -> Envelope:
    return Envelope(
        endpoint_key=endpoint_key, url_template=f"(fixture:{name})",
        parameters=parameters, payload=(FIXTURES / name).read_bytes(),
        retrieved_at=NOW, data_mode=DataMode.FIXTURE,
    )


@pytest.fixture
def intake(session: Session) -> NbaIntake:
    return NbaIntake(session, data_mode=DataMode.FIXTURE, retention_permitted=True)


def load_identity(intake: NbaIntake) -> None:
    teams = envelope("AllTeams.json", "teams")
    payload, _ = intake.store_payload(teams)
    for team in parse_teams(teams.decode()):
        intake.team(team, now=NOW, payload_id=payload.id)
    players = envelope("Players_AUR_BRI.json", "players_by_team", team="AUR")
    payload, _ = intake.store_payload(players)
    for parsed in parse_players(players.decode()):
        player, _ = intake.player(parsed, now=NOW, payload_id=payload.id)
        team = intake.team_by_provider_id(parsed.provider_team_id)
        if team is not None:
            intake.roster(parsed, team, player, observed_at=NOW, payload_id=payload.id)


# -- the access gate ---------------------------------------------------------

def test_a_configured_credential_does_not_establish_entitlement():
    settings = Settings()
    settings.sportsdataio_api_key = "x" * 32

    verdict = assess(settings, sportsdataio_runs=0)

    assert verdict.network_collection_allowed is False
    assert verdict.data_mode is DataMode.UNKNOWN
    assert verdict.api_access is Permission.UNVERIFIED
    # API access, research use and display rights are recorded separately.
    assert verdict.training_research_use is Permission.UNVERIFIED
    assert verdict.subscriber_display_use is Permission.UNVERIFIED
    assert verdict.missing_prerequisite


def test_the_transport_refuses_to_exist_while_collection_is_unpermitted():
    settings = Settings()
    settings.sportsdataio_api_key = "x" * 32
    verdict = assess(settings, sportsdataio_runs=0)

    with pytest.raises(NbaAdapterError, match="not permitted"):
        NbaSportsDataIoSource(settings, verdict)


def test_authentication_would_use_a_header_not_the_query_string():
    """A credential must not be able to reach a URL, a log or a traceback."""

    from app.data_sources.nba_sportsdataio import BASE_URL, ENDPOINTS

    assert AUTH_HEADER == "Ocp-Apim-Subscription-Key"
    for template in ENDPOINTS.values():
        assert "key" not in template.lower()
    assert BASE_URL.startswith("https://")


# -- identity reconciliation -------------------------------------------------

def test_teams_players_and_games_resolve_to_stored_identities(session, intake):
    load_identity(intake)
    schedule = envelope("Games_2026.json", "schedule", season="2026")
    payload, _ = intake.store_payload(schedule)
    dispositions = [
        intake.game(g, retrieved_at=NOW, payload_id=payload.id)[1]
        for g in parse_games(schedule.decode())
    ]

    assert dispositions == ["CREATED", "CREATED", "CREATED"]
    assert session.scalar(select(func.count()).select_from(NbaTeam)) == 3
    assert session.scalar(select(func.count()).select_from(NbaPlayer)) == 4
    stored = session.scalars(select(NbaGame)).all()
    assert {g.home_nba_team_id for g in stored} != {g.away_nba_team_id for g in stored}


def test_a_game_whose_clubs_are_unknown_is_reported_not_invented(session, intake):
    """No team row is fabricated to make a game fit."""

    schedule = envelope("Games_2026.json", "schedule", season="2026")
    payload, _ = intake.store_payload(schedule)
    game, disposition = intake.game(parse_games(schedule.decode())[0],
                                    retrieved_at=NOW, payload_id=payload.id)

    assert game is None
    assert disposition == "UNRESOLVED_TEAM_IDENTITY"
    assert session.scalar(select(func.count()).select_from(NbaTeam)) == 0


def test_a_rescheduled_game_keeps_its_provider_identity(session, intake):
    """Moving the date must not create a second game."""

    load_identity(intake)
    schedule = envelope("Games_2026.json", "schedule", season="2026")
    payload, _ = intake.store_payload(schedule)
    original = parse_games(schedule.decode())[2]
    intake.game(original, retrieved_at=NOW, payload_id=payload.id)

    moved = type(original)(**{**original.__dict__,
                              "game_day": original.game_day + timedelta(days=2)})
    row, disposition = intake.game(moved, retrieved_at=NOW, payload_id=payload.id)

    assert disposition == "REVISED"
    assert row.provider_game_id == original.provider_game_id
    assert session.scalar(select(func.count()).select_from(NbaGame).where(
        NbaGame.provider_game_id == original.provider_game_id, NbaGame.is_current.is_(True))) == 1


# -- arithmetic, reported rather than repaired -------------------------------

def test_final_score_period_totals_and_player_points_agree(session, intake):
    box = parse_box_score(envelope("BoxScoreFinal_500101.json", "box_score_final").decode())

    checks = reconcile_scores(box)

    assert checks["allAgree"] is True
    assert checks["finalVersusPeriodTotals"]["home"]["periods"] == 112
    assert checks["periodCount"] == 4 and checks["overtime"] is False


def test_overtime_periods_still_reconcile(session, intake):
    box = parse_box_score(envelope("BoxScoreFinal_500102.json", "box_score_final").decode())

    checks = reconcile_scores(box)

    assert checks["allAgree"] is True
    assert checks["periodCount"] == 5 and checks["overtime"] is True


def test_a_disagreement_is_reported_and_the_payload_is_not_edited(tmp_path: Path, intake):
    document = json.loads((FIXTURES / "BoxScoreFinal_500101.json").read_text(encoding="utf-8"))
    document["Game"]["HomeTeamScore"] = 999          # a final that matches nothing
    box = parse_box_score(document)

    checks = reconcile_scores(box)

    assert checks["allAgree"] is False
    assert checks["finalVersusPeriodTotals"]["home"]["agrees"] is False
    assert checks["finalVersusTeamGamePoints"]["home"]["agrees"] is False
    # The payload is untouched: the parsed game still carries what was supplied.
    assert box.game.home_score == 999


# -- attribution -------------------------------------------------------------

def test_players_are_attributed_to_the_club_they_played_for(session, intake):
    load_identity(intake)
    box_envelope = envelope("BoxScoreFinal_500101.json", "box_score_final")
    payload, _ = intake.store_payload(box_envelope)
    outcome = IntakeOutcome()
    intake.box_score(parse_box_score(box_envelope.decode()), retrieved_at=NOW,
                     payload_id=payload.id, outcome=outcome)

    aur = session.scalar(select(NbaTeam).where(NbaTeam.provider_team_id == 901))
    bri = session.scalar(select(NbaTeam).where(NbaTeam.provider_team_id == 902))
    rows = session.scalars(select(NbaPlayerGameStat)).all()
    assert len(rows) == 4
    by_team = {}
    for row in rows:
        by_team.setdefault(row.nba_team_id, []).append(row.points)
    assert sum(by_team[aur.id]) == 112
    assert sum(by_team[bri.id]) == 108
    assert all(r.opponent_nba_team_id in {aur.id, bri.id} for r in rows)
    # Team rows carry the same totals, on the right side of the game.
    team_rows = {r.nba_team_id: r for r in session.scalars(select(NbaTeamGameStat)).all()}
    assert team_rows[aur.id].is_home is True and team_rows[aur.id].points == 112
    assert team_rows[bri.id].is_home is False and team_rows[bri.id].points == 108


def test_current_roster_is_not_game_participation(session, intake):
    """A roster row says where a player is listed; it is not an appearance."""

    load_identity(intake)

    rosters = session.scalars(select(NbaRosterMembership)).all()
    appearances = session.scalars(select(NbaPlayerGameStat)).all()
    assert len(rosters) == 4
    assert appearances == []


# -- idempotency and corrections ---------------------------------------------

def _ingest_all(intake: NbaIntake, outcome: IntakeOutcome) -> None:
    load_identity(intake)
    schedule = envelope("Games_2026.json", "schedule", season="2026")
    payload, created = intake.store_payload(schedule)
    outcome.payloads_stored += int(created)
    for parsed in parse_games(schedule.decode()):
        intake.game(parsed, retrieved_at=NOW, payload_id=payload.id)
    for name in ("BoxScoreFinal_500101.json", "BoxScoreFinal_500102.json"):
        box_envelope = envelope(name, "box_score_final")
        payload, created = intake.store_payload(box_envelope)
        outcome.payloads_stored += int(created)
        intake.box_score(parse_box_score(box_envelope.decode()), retrieved_at=NOW,
                         payload_id=payload.id, outcome=outcome)


def test_a_repeated_import_writes_nothing(session, intake):
    first = IntakeOutcome()
    _ingest_all(intake, first)
    counts = {
        model.__name__: session.scalar(select(func.count()).select_from(model))
        for model in (NbaSourcePayload, NbaTeam, NbaPlayer, NbaGame,
                      NbaTeamGameStat, NbaPlayerGameStat, NbaRosterMembership)
    }

    second = IntakeOutcome()
    _ingest_all(intake, second)

    assert second.payloads_stored == 0
    assert second.games_created == 0 and second.game_revisions == 0
    assert second.team_game_rows == 0 and second.player_game_rows == 0
    for model in (NbaSourcePayload, NbaTeam, NbaPlayer, NbaGame,
                  NbaTeamGameStat, NbaPlayerGameStat, NbaRosterMembership):
        assert session.scalar(select(func.count()).select_from(model)) == counts[model.__name__]


def test_a_provider_correction_appends_a_revision_and_keeps_the_original(session, intake):
    load_identity(intake)
    base = envelope("BoxScoreFinal_500101.json", "box_score_final")
    payload, _ = intake.store_payload(base)
    outcome = IntakeOutcome()
    intake.box_score(parse_box_score(base.decode()), retrieved_at=NOW,
                     payload_id=payload.id, outcome=outcome)

    corrected = envelope("BoxScoreFinal_500101_corrected.json", "box_score_final")
    payload, _ = intake.store_payload(corrected)
    intake.box_score(parse_box_score(corrected.decode()),
                     retrieved_at=NOW + timedelta(days=1),
                     payload_id=payload.id, outcome=outcome)

    rows = session.scalars(select(NbaGame).where(
        NbaGame.provider_game_id == 500101).order_by(NbaGame.revision)).all()
    assert [r.revision for r in rows] == [1, 2]
    assert [r.is_current for r in rows] == [False, True]
    # The original keeps every value it was written with.
    assert rows[0].away_score == 108
    assert rows[1].away_score == 109
    assert rows[1].supersedes_game_id == rows[0].id


def test_a_stale_re_presentation_is_not_treated_as_a_correction(session, intake):
    """An older payload arriving after a newer one must not flip the record."""

    load_identity(intake)
    corrected = envelope("BoxScoreFinal_500101_corrected.json", "box_score_final")
    payload, _ = intake.store_payload(corrected)
    outcome = IntakeOutcome()
    intake.box_score(parse_box_score(corrected.decode()), retrieved_at=NOW,
                     payload_id=payload.id, outcome=outcome)

    base = envelope("BoxScoreFinal_500101.json", "box_score_final")
    payload, _ = intake.store_payload(base)
    intake.box_score(parse_box_score(base.decode()),
                     retrieved_at=NOW + timedelta(days=2),
                     payload_id=payload.id, outcome=outcome)

    current = session.scalar(select(NbaGame).where(
        NbaGame.provider_game_id == 500101, NbaGame.is_current.is_(True)))
    assert current.away_score == 109, "the newer correction must remain current"
    assert session.scalar(select(func.count()).select_from(NbaGame).where(
        NbaGame.provider_game_id == 500101)) == 1


# -- isolation ---------------------------------------------------------------

def test_every_fixture_row_says_it_is_a_fixture(session, intake):
    outcome = IntakeOutcome()
    _ingest_all(intake, outcome)

    for model in (NbaSourcePayload, NbaTeam, NbaPlayer, NbaGame,
                  NbaTeamGameStat, NbaPlayerGameStat, NbaRosterMembership):
        modes = set(session.scalars(select(model.data_mode)).all())
        assert modes == {"FIXTURE"}, f"{model.__name__} leaked a non-fixture mode"


def test_a_fixture_envelope_cannot_be_written_as_real(session):
    real_intake = NbaIntake(session, data_mode=DataMode.REAL, retention_permitted=True)

    with pytest.raises(NbaIntakeError, match="never coerced"):
        real_intake.store_payload(envelope("AllTeams.json", "teams"))


def test_ingesting_fixtures_touches_no_mlb_or_nfl_table(session, intake):
    from app.models import Game, ShadowForecast, OfficialAspPosition

    outcome = IntakeOutcome()
    _ingest_all(intake, outcome)

    for model in (Game, ShadowForecast, OfficialAspPosition):
        assert session.scalar(select(func.count()).select_from(model)) == 0
