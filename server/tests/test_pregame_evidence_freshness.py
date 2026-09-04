"""MLB pregame starter/lineup evidence — cutoff, as-of, identity, state,
scheduler and decision-integrity proofs.

All fixtures are rollback-isolated SQLite rows; no provider is called and no
historical decision, position or settlement is touched. Numbering follows the
audit mandate's 45-point matrix (grouped, several points per test).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditEvent,
    ExperimentDecisionPhase,
    Game,
    GameStatus,
    GameTeam,
    League,
    LineupObservation,
    OfficialAspPosition,
    ShadowExperimentDecision,
    Sport,
    StarterObservation,
    Team,
)
from app.services.evidence_states import (
    FRESHNESS_FRESH,
    FRESHNESS_INVALID,
    FRESHNESS_MISSING,
    FRESHNESS_STALE,
    LINEUP_CONFIRMED_COMPLETE,
    LINEUP_PENDING_ALLOWED,
    LINEUP_SOURCE_CONFIRMED,
    LINEUP_SOURCE_NOT_YET_PUBLISHED,
    LINEUP_SOURCE_PARTIAL,
    STARTER_CONFIRMED,
    STARTER_MISSING,
    STARTER_PROBABLE_STABLE,
    STARTER_PROBABLE_UNSTABLE,
    LineupContext,
    evaluation_reference,
    lineup_evidence,
    starter_evidence,
)
from app.services.pregame_evidence import pregame_evidence_summary
from app.services.pregame_evidence_monitor import ALERT_ACTION, PregameEvidenceMonitor
from app.services.practical_policy import POLICY_VERSION, PracticalPolicyEvaluator
from app.services.shadow_experiment_service import (
    FINAL_FREEZE_WINDOW_MINUTES,
    FINAL_PREGAME_CUTOFF_MINUTES,
    V2_DECISION_POLICY,
    in_final_window,
)

STARTS = datetime(2026, 9, 4, 23, 10, tzinfo=UTC)          # 19:10 ET
CUTOFF = STARTS - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
P1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
P2 = uuid.UUID("22222222-2222-2222-2222-222222222222")


# -- fixtures ----------------------------------------------------------------

def _league(session):
    sport = session.scalar(select(Sport).where(Sport.code == "BASEBALL")) or Sport(code="BASEBALL", name="Baseball", active=True)
    session.add(sport); session.flush()
    league = session.scalar(select(League).where(League.code == "MLB")) or League(sport_id=sport.id, code="MLB", name="MLB", active=True)
    session.add(league); session.flush()
    return league


def _team(session, league, name, abbr):
    row = session.scalar(select(Team).where(Team.league_id == league.id, Team.abbreviation == abbr))
    if row is None:
        row = Team(league_id=league.id, name=name, abbreviation=abbr, city=name.split()[0], active=True)
        session.add(row); session.flush()
    return row


def _game(session, *, home=("New York Yankees", "NYY"), away=("Boston Red Sox", "BOS"), starts=STARTS):
    league = _league(session)
    h, a = _team(session, league, *home), _team(session, league, *away)
    game = Game(league_id=league.id, starts_at=starts, status=GameStatus.SCHEDULED, source_mode="LIVE")
    session.add(game); session.flush()
    session.add_all([GameTeam(game_id=game.id, team_id=h.id, is_home=True), GameTeam(game_id=game.id, team_id=a.id, is_home=False)])
    session.commit()
    return game, h, a


def _obs(session, game, team, *, at, pitcher=P1, state="PROBABLE", source="MLB_STATS_API", created=None):
    row = StarterObservation(
        game_id=game.id, team_id=team.id, probable_pitcher_id=pitcher,
        confirmed_pitcher_id=pitcher if state == "CONFIRMED" else None, confirmation_state=state,
        observed_at=at, available_as_of=at, source=source, source_mode="LIVE",
        source_record_id=f"{game.id}:{team.id}:{at.isoformat()}", payload_checksum=f"cs-{team.id}-{at.timestamp()}-{pitcher}",
    )
    if created is not None:  # persisted-at time is NOT the source time (rows are append-only)
        row.created_at = created
    session.add(row); session.commit()
    return row


def _stable(session, game, team, *, latest, pitcher=P1):
    _obs(session, game, team, at=latest - timedelta(minutes=30), pitcher=pitcher)
    _obs(session, game, team, at=latest, pitcher=pitcher)


def _lineup(session, game, team, *, at, status="CONFIRMED", complete=True):
    session.add(LineupObservation(
        game_id=game.id, team_id=team.id, lineup_status=status, is_complete=complete,
        observed_at=at, available_as_of=at, source="MLB_STATS_API", source_mode="LIVE",
        source_record_id=f"{game.id}:{team.id}", payload_checksum=f"lc-{team.id}-{at.timestamp()}-{status}-{complete}",
    ))
    session.commit()


def _forecast(generated_at):
    return SimpleNamespace(generated_at=generated_at, uncertainty=0.01, readiness_components={})


def _selection():
    return SimpleNamespace(market_type="MONEYLINE", selection="NYY ML", is_stale=False, model_probability_fraction=0.60,
                           no_vig_probability_fraction=0.52, decimal_odds=2.0, american_odds=100, sportsbook="BookX")


# -- TIME (5–10) -------------------------------------------------------------

def test_evaluation_reference_is_min_of_instant_and_cutoff_and_tz_aware():
    early = CUTOFF - timedelta(hours=5)
    assert evaluation_reference(CUTOFF, early) == early                     # (5) pre-cutoff instant
    assert evaluation_reference(CUTOFF, CUTOFF + timedelta(hours=1)) == CUTOFF  # post-cutoff clamps to cutoff
    assert evaluation_reference(CUTOFF, None) == CUTOFF
    naive = CUTOFF.replace(tzinfo=None)
    assert evaluation_reference(naive, None).tzinfo is not None             # naive treated as UTC, never local
    # 19:10 ET first pitch == 23:10 UTC: the cutoff converts to 18:55 ET.
    from zoneinfo import ZoneInfo
    assert CUTOFF.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M") == "18:55"


def test_freshness_uses_governed_source_time_at_the_evaluation_instant_not_the_future_cutoff(session):
    game, home, _ = _game(session)
    latest = CUTOFF - timedelta(hours=6)              # 6h before cutoff …
    _stable(session, game, home, latest=latest)
    now = latest + timedelta(minutes=42)              # … but only 42m before the evaluation instant
    ev = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=now - timedelta(minutes=5), as_of=now)
    assert ev.freshness == FRESHNESS_FRESH and ev.age_minutes_at_reference == pytest.approx(42.0)   # (6)
    assert ev.state == STARTER_PROBABLE_STABLE
    # Aged against the future cutoff the same evidence would be STALE (the old defect).
    at_cutoff = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=now, as_of=None)
    assert at_cutoff.freshness == FRESHNESS_STALE and at_cutoff.state == STARTER_PROBABLE_UNSTABLE


def test_persisted_at_time_cannot_make_old_evidence_fresh(session):
    game, home, _ = _game(session)
    old = CUTOFF - timedelta(hours=9)
    _obs(session, game, home, at=old - timedelta(minutes=30))
    _obs(session, game, home, at=old, created=CUTOFF - timedelta(minutes=1))   # written to the DB just now
    ev = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=CUTOFF, as_of=CUTOFF)
    assert ev.freshness == FRESHNESS_STALE and ev.age_minutes_at_reference == pytest.approx(540.0)   # (7)


def test_post_reference_and_future_dated_evidence_fail_closed(session):
    game, home, _ = _game(session)
    now = CUTOFF - timedelta(hours=2)
    _stable(session, game, home, latest=now - timedelta(minutes=10))
    _obs(session, game, home, at=now + timedelta(minutes=5), pitcher=P2)     # arrives after the evaluation instant
    ev = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=now, as_of=now)
    assert ev.pitcher_id == str(P1) and ev.observation_count == 2                # (8) never selected
    # A future-dated observation relative to the reference is invalid, not fresh.
    g2, h2, _ = _game(session, home=("Los Angeles Dodgers", "LAD"), away=("Washington Nationals", "WSH"))
    _obs(session, g2, h2, at=CUTOFF + timedelta(minutes=30))
    ev2 = starter_evidence(session, g2, h2.id, cutoff=CUTOFF + timedelta(hours=1), forecast_generated_at=CUTOFF, as_of=CUTOFF)
    assert ev2.state == STARTER_MISSING and ev2.freshness in (FRESHNESS_MISSING, FRESHNESS_INVALID)   # (10)


# -- LATEST-AS-OF (11–15) ----------------------------------------------------

def test_newest_eligible_observation_is_selected_and_duplicates_ignored(session):
    game, home, _ = _game(session)
    for m in (300, 200, 100, 40):
        _obs(session, game, home, at=CUTOFF - timedelta(minutes=m))
    _obs(session, game, home, at=CUTOFF - timedelta(minutes=40), pitcher=P1, source="MLB_STATS_API_DUP")  # older duplicate time, other source
    ev = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=CUTOFF, as_of=CUTOFF)
    assert ev.age_minutes_at_reference == pytest.approx(40.0) and ev.observation_count == 5             # (11, 13)
    # Independent SQL reduction agrees with the application.
    sql_latest = session.scalar(select(func.max(StarterObservation.available_as_of)).where(
        StarterObservation.game_id == game.id, StarterObservation.team_id == home.id,
        StarterObservation.available_as_of <= CUTOFF))
    assert (sql_latest if sql_latest.tzinfo else sql_latest.replace(tzinfo=UTC)) == CUTOFF - timedelta(minutes=40)


def test_doubleheader_and_team_level_records_never_cross_games(session):
    g1, home, away = _game(session, starts=STARTS - timedelta(hours=5))
    g2, _, _ = _game(session, starts=STARTS)                        # same teams, game two
    _stable(session, g1, home, latest=g1.starts_at - timedelta(minutes=30), pitcher=P1)
    ev_g2 = starter_evidence(session, g2, home.id, cutoff=CUTOFF, forecast_generated_at=CUTOFF, as_of=CUTOFF)
    assert ev_g2.state == STARTER_MISSING                            # (14) game-one evidence cannot enter game two
    ev_g1 = starter_evidence(session, g1, home.id, cutoff=g1.starts_at - timedelta(minutes=15),
                             forecast_generated_at=g1.starts_at, as_of=g1.starts_at - timedelta(minutes=15))
    assert ev_g1.pitcher_id == str(P1)
    # (15) another game's team rows are invisible to this game.
    assert starter_evidence(session, g2, away.id, cutoff=CUTOFF, forecast_generated_at=CUTOFF, as_of=CUTOFF).state == STARTER_MISSING
    assert g1.id != g2.id                                            # (2) distinct canonical events


# -- STARTER STATE (16–21) ---------------------------------------------------

def test_confirmed_probable_stable_unstable_stale_missing_are_distinct(session):
    game, home, away = _game(session)
    now = CUTOFF - timedelta(minutes=30)
    _obs(session, game, home, at=now - timedelta(minutes=5), state="CONFIRMED")
    confirmed = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=now, as_of=now)
    assert confirmed.state == STARTER_CONFIRMED and confirmed.source_status == "CONFIRMED"    # (16)
    _obs(session, game, away, at=now - timedelta(minutes=5))       # single probable observation
    unstable = starter_evidence(session, game, away.id, cutoff=CUTOFF, forecast_generated_at=now, as_of=now)
    assert unstable.state == STARTER_PROBABLE_UNSTABLE and unstable.freshness == FRESHNESS_FRESH   # (17) fresh but unstable
    g2, h2, a2 = _game(session, home=("Chicago Cubs", "CHC"), away=("Miami Marlins", "MIA"))
    _stable(session, g2, h2, latest=now - timedelta(hours=5))
    stale = starter_evidence(session, g2, h2.id, cutoff=CUTOFF, forecast_generated_at=now, as_of=now)
    missing = starter_evidence(session, g2, a2.id, cutoff=CUTOFF, forecast_generated_at=now, as_of=now)
    assert stale.state == STARTER_PROBABLE_UNSTABLE and stale.freshness == FRESHNESS_STALE       # (18) STALE …
    assert missing.state == STARTER_MISSING and missing.freshness == FRESHNESS_MISSING           # … ≠ MISSING
    summary = pregame_evidence_summary(SimpleNamespace(starter_home=stale, starter_away=missing, lineup=None), cutoff=CUTOFF, reference=now)
    assert summary["starter_block_category"] == "MISSING"        # missing outranks stale in the exact taxonomy
    only_stale = pregame_evidence_summary(SimpleNamespace(starter_home=stale, starter_away=confirmed, lineup=None), cutoff=CUTOFF, reference=now)
    assert only_stale["starter_block_category"] == "STALE" and "not current" in only_stale["starter_block_reason"]
    assert "observed 5h 00m before reference" in only_stale["starter_home"]["label"]


def test_starter_change_invalidates_superseded_stability_and_no_update_stays_blocked(session):
    game, home, _ = _game(session)
    now = CUTOFF - timedelta(minutes=20)
    _stable(session, game, home, latest=now - timedelta(minutes=60), pitcher=P1)
    _obs(session, game, home, at=now - timedelta(minutes=5), pitcher=P2)      # identity changed
    ev = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=now - timedelta(minutes=90), as_of=now)
    assert ev.pitcher_id == str(P2) and ev.state == STARTER_PROBABLE_UNSTABLE and ev.stable_streak_count == 1   # (20)
    assert any("predates" in r for r in ev.reasons)
    # (21) a source that genuinely stopped publishing stays blocked at the reference.
    g2, h2, _ = _game(session, home=("Seattle Mariners", "SEA"), away=("Athletics", "ATH"))
    _stable(session, g2, h2, latest=CUTOFF - timedelta(hours=8))
    late = starter_evidence(session, g2, h2.id, cutoff=CUTOFF, forecast_generated_at=CUTOFF, as_of=CUTOFF)
    assert late.freshness == FRESHNESS_STALE and not late.eligible


# -- LINEUPS (22–26) ---------------------------------------------------------

def test_lineup_source_states_not_yet_published_partial_confirmed(session):
    game, home, away = _game(session)
    now = CUTOFF - timedelta(minutes=30)
    ctx = LineupContext(market_is_moneyline=True, dependency_allows_pending=True, pregame=True)
    pending = lineup_evidence(session, game, home.id, away.id, cutoff=CUTOFF, forecast_generated_at=now, context=ctx, as_of=now)
    assert pending.state == LINEUP_PENDING_ALLOWED
    assert pending.home_source_state == LINEUP_SOURCE_NOT_YET_PUBLISHED == pending.away_source_state      # (22)
    _lineup(session, game, home, at=now - timedelta(minutes=10), status="EXPECTED", complete=False)
    _lineup(session, game, away, at=now - timedelta(minutes=8))
    partial = lineup_evidence(session, game, home.id, away.id, cutoff=CUTOFF, forecast_generated_at=now, context=ctx, as_of=now)
    assert partial.home_source_state == LINEUP_SOURCE_PARTIAL and partial.away_source_state == LINEUP_SOURCE_CONFIRMED   # (23)
    assert partial.state != LINEUP_CONFIRMED_COMPLETE
    _lineup(session, game, home, at=now - timedelta(minutes=2))
    complete = lineup_evidence(session, game, home.id, away.id, cutoff=CUTOFF, forecast_generated_at=now, context=ctx, as_of=now)
    assert complete.state == LINEUP_CONFIRMED_COMPLETE and complete.eligible_for_moneyline           # (24)
    label = pregame_evidence_summary(SimpleNamespace(starter_home=None, starter_away=None, lineup=pending), cutoff=CUTOFF, reference=now)
    assert label["lineup"]["label"] == "Lineup: NOT YET PUBLISHED"


def test_lineup_published_after_reference_is_not_prospective_evidence(session):
    game, home, away = _game(session)
    now = CUTOFF - timedelta(hours=3)
    _lineup(session, game, home, at=CUTOFF - timedelta(minutes=30))   # published later than the evaluation instant
    _lineup(session, game, away, at=CUTOFF - timedelta(minutes=30))
    ctx = LineupContext(market_is_moneyline=True, dependency_allows_pending=True, pregame=True)
    ev = lineup_evidence(session, game, home.id, away.id, cutoff=CUTOFF, forecast_generated_at=now, context=ctx, as_of=now)
    assert ev.home_source_state == LINEUP_SOURCE_NOT_YET_PUBLISHED and ev.state == LINEUP_PENDING_ALLOWED   # (26)
    at_cutoff = lineup_evidence(session, game, home.id, away.id, cutoff=CUTOFF, forecast_generated_at=CUTOFF - timedelta(minutes=20), context=ctx, as_of=CUTOFF)
    assert at_cutoff.state == LINEUP_CONFIRMED_COMPLETE


# -- RESCORING AND DECISIONS (32–40) ----------------------------------------

def test_v2_evaluator_uses_the_evaluation_instant_and_freezes_final_only_in_window(session, settings):
    game, home, away = _game(session)
    now = CUTOFF - timedelta(hours=4)
    _stable(session, game, home, latest=now - timedelta(minutes=15), pitcher=P1)
    _stable(session, game, away, latest=now - timedelta(minutes=15), pitcher=P2)
    ev = PracticalPolicyEvaluator(session, settings).evaluate(
        game, _forecast(now - timedelta(minutes=5)), SimpleNamespace(last_availability_event=None), _selection(),
        now=now, cutoff=CUTOFF,
    )
    assert ev.starter_home.state == STARTER_PROBABLE_STABLE and ev.starter_home.freshness == FRESHNESS_FRESH
    assert ev.qualified and ev.lifecycle == "PROVISIONALLY_ELIGIBLE"                       # (32) pre-cutoff, provisional only
    assert ev.evidence_json()["starter_home"]["age_minutes_at_reference"] == pytest.approx(15.0)
    # (35/36) the final window is the last FINAL_FREEZE_WINDOW_MINUTES before the cutoff, versioned.
    assert in_final_window(CUTOFF, CUTOFF - timedelta(minutes=FINAL_FREEZE_WINDOW_MINUTES))
    assert in_final_window(CUTOFF, CUTOFF)
    assert not in_final_window(CUTOFF, CUTOFF - timedelta(minutes=FINAL_FREEZE_WINDOW_MINUTES + 1))
    assert not in_final_window(CUTOFF, CUTOFF + timedelta(seconds=1))
    assert POLICY_VERSION == V2_DECISION_POLICY == "AISP_PRACTICAL_PREGAME_POLICY_V2_1"   # (13/39) policy identity unchanged


def test_evaluation_creates_no_decision_or_position(session, settings):
    game, home, away = _game(session)
    now = CUTOFF - timedelta(minutes=10)
    _stable(session, game, home, latest=now, pitcher=P1); _stable(session, game, away, latest=now, pitcher=P2)
    before = (session.scalar(select(func.count()).select_from(ShadowExperimentDecision)),
              session.scalar(select(func.count()).select_from(OfficialAspPosition)))
    PracticalPolicyEvaluator(session, settings).evaluate(game, _forecast(now), SimpleNamespace(last_availability_event=None), _selection(), now=now, cutoff=CUTOFF)
    pregame_evidence_summary(PracticalPolicyEvaluator(session, settings).evaluate(game, _forecast(now), None, _selection(), now=now, cutoff=CUTOFF), cutoff=CUTOFF, reference=now)
    after = (session.scalar(select(func.count()).select_from(ShadowExperimentDecision)),
             session.scalar(select(func.count()).select_from(OfficialAspPosition)))
    assert before == after                                                                     # (34, 42)


# -- SCHEDULER / MONITOR (27–31) --------------------------------------------

def test_monitor_alerts_are_idempotent_and_distinguish_conditions(session, settings):
    game, home, away = _game(session)
    now = STARTS - timedelta(hours=1)                      # inside the 4h fast window, before cutoff
    s = settings.model_copy(update={"display_timezone": "America/New_York"})
    first = PregameEvidenceMonitor(session, s).run(STARTS.date(), now=now)
    session.commit()
    kinds = set(first["kinds"])
    assert "NO_STARTER_OBSERVATIONS" in kinds and "POLL_JOB_SILENT" in kinds        # zero observations; no poll run recorded
    again = PregameEvidenceMonitor(session, s).run(STARTS.date(), now=now + timedelta(minutes=5))
    session.commit()
    assert again["alerts_recorded"] == 0 and again["alerts_seen"] >= 2                # (31) idempotent, no duplicates
    assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == ALERT_ACTION)) == first["alerts_recorded"]
    # An observation older than expected inside the fast window is its own condition.
    _obs(session, game, home, at=now - timedelta(minutes=60))
    third = PregameEvidenceMonitor(session, s).run(STARTS.date(), now=now + timedelta(minutes=6))
    assert "STARTER_EVIDENCE_OLDER_THAN_EXPECTED" in third["kinds"]
    # Past the cutoff with no FINAL decision for the active experiment → FINAL_DECISION_MISSING.
    from app.services.shadow_experiment_service import ShadowExperimentService
    ShadowExperimentService(session, s).ensure_started(start_date=STARTS.date() - timedelta(days=3), now=STARTS - timedelta(days=3), code_commit="abc")
    session.commit()
    late = PregameEvidenceMonitor(session, s).run(STARTS.date(), now=CUTOFF + timedelta(minutes=1))
    assert "FINAL_DECISION_MISSING" in late["kinds"]


def test_poll_heartbeat_persists_verified_state_only_when_stale(session, settings, monkeypatch):
    """(27–30) The availability poll persists a starter observation for a due
    game only when its latest observation is older than the heartbeat interval,
    via the governed normalizer path, with the poll fetch time as the source
    time; an unchanged fresh observation is not duplicated."""
    from app.jobs.orchestrator import STARTER_HEARTBEAT_MINUTES, JobOrchestrator
    from app.models import ExternalIdMapping

    game, home, away = _game(session)
    session.add(ExternalIdMapping(entity_type="game", source="MLB_STATS_API", entity_id=game.id, external_id="900001"))
    for team, ext in ((home, "147"), (away, "111")):
        session.add(ExternalIdMapping(entity_type="team", source="MLB_STATS_API", entity_id=team.id, external_id=ext))
    session.commit()
    orchestrator = JobOrchestrator(session, settings)
    now = STARTS - timedelta(hours=2)
    item = {
        "source_game_id": "900001", "scheduled_at": STARTS.isoformat(), "status": "Scheduled",
        "home_team": {"source_id": "147", "name": "New York Yankees", "abbreviation": "NYY"},
        "away_team": {"source_id": "111", "name": "Boston Red Sox", "abbreviation": "BOS"},
        "home_pitcher": {"source_id": "6001", "full_name": "Home Ace", "throws": "R", "confirmed": False},
        "away_pitcher": {"source_id": "6002", "full_name": "Away Ace", "throws": "L", "confirmed": False},
        "event_date": STARTS.date().isoformat(), "venue": {"source_id": "3313", "name": "Yankee Stadium"}, "source_mode": "LIVE",
    }
    count_before = session.scalar(select(func.count()).select_from(StarterObservation).where(StarterObservation.game_id == game.id))
    persisted = orchestrator._persist_starter_heartbeats({game.id: game}, {"900001": item}, {game.id: "900001"}, now, now)
    count_after = session.scalar(select(func.count()).select_from(StarterObservation).where(StarterObservation.game_id == game.id))
    assert persisted == 1 and count_after >= count_before + 1
    latest = session.scalar(select(func.max(StarterObservation.available_as_of)).where(StarterObservation.game_id == game.id))
    assert (latest if latest.tzinfo else latest.replace(tzinfo=UTC)) == now          # source time = poll fetch time
    # Fresh (younger than the heartbeat interval) → no duplicate write.
    again = orchestrator._persist_starter_heartbeats({game.id: game}, {"900001": item}, {game.id: "900001"},
                                                     now + timedelta(minutes=STARTER_HEARTBEAT_MINUTES - 1),
                                                     now + timedelta(minutes=STARTER_HEARTBEAT_MINUTES - 1))
    assert again == 0
