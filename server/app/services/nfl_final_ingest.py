"""Bringing completed NFL games into AISP by themselves.

THE GAP THIS CLOSES
-------------------
Prospective scoring worked, but only after a person typed a box score in. A
workflow that needs a human at the end of it on a Wednesday night is not
unattended, and the forecasts for every other week-one game would have sat
unscored until someone remembered. This is the piece that notices a game
finished.

WHAT IT COSTS
-------------
Nothing. It reads the nflverse schedules release: a public GitHub asset under
CC-BY-4.0 with no key and no metered allowance. It asks a 43-byte timestamp
file whether anything changed and downloads the ~2 MB schedule only when it
has, so an hour in which no game finished costs one small request.

It deliberately does NOT use The Odds API scores endpoint, which is already
wired up and would work: that endpoint draws on the paid allowance reserved
for sportsbook prices, and spending price credits on a score that a free
better-identified feed publishes anyway is a bad trade.

WHAT IT REFUSES TO DO
---------------------
* Invent a status. The feed has no status column -- a game in progress and a
  game that never kicked off both show empty scores -- so "no score yet" is
  recorded as awaiting a final, never as a final of 0-0.
* Move a kickoff. If the feed shows a game rescheduled, that is reported and
  the game is left alone. Forecast eligibility was decided against the
  schedule as it stood; re-deciding it against a later correction would be
  hindsight.
* Overwrite a stronger record. Provenance ranks, and a weaker source never
  demotes a better-evidenced final.
* Manufacture market evidence. games.csv carries moneyline and total columns;
  they are dropped at the parse boundary and never persisted.
* Run twice. It is bound to the single supervised process and holds a kill
  switch, and every write is keyed by a checksum over the result facts alone,
  so refetching an unchanged game writes nothing.

It makes no model call, creates no decision, opens no position and settles
nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_sources.nflverse import (
    same_club,
    ATTRIBUTION, NFLVERSE, NflverseGame, NflverseSchedule, NflverseScheduleSource,
)
from app.gen2.exchange.canonical_json import canonical_sha256
from app.models import (
    Game, GameStateObservation, GameStatus, GameTeam, IngestionRun, League,
    NflFinalObservation, RunStatus, Season, ShadowForecast, Team,
)
from app.services.result_provenance import result_tier

JOB_NAME = "ingest-nfl-finals"
SOURCE_MODE = "RESEARCH_SHADOW"
LEAGUE_CODE = "NFL"

#: A game is not asked about until it has plausibly finished. An NFL game runs
#: a little over three hours; 150 minutes is early enough to catch a blowout
#: that ends fast and late enough that we are not asking during the first half.
SETTLE_GRACE_MINUTES = 150

#: Keep watching a finalised game for this long so a stat correction can land,
#: then stop asking about it forever. Without a window, "stop polling finalized
#: games" would also mean "never see a correction".
CORRECTION_WINDOW_DAYS = 8

#: Skip reasons, so every game we did not finalise says why.
AWAITING_FINAL = "AWAITING_FINAL"
NOT_IN_FEED = "NOT_IN_FEED"
RESCHEDULED = "RESCHEDULED_IN_FEED"
ALREADY_CURRENT = "ALREADY_RECORDED"
WEAKER_SOURCE = "WEAKER_THAN_EXISTING_RESULT"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class PendingGame:
    """One AISP game we would like a final for."""

    game: Game
    event_id: str
    kickoff: datetime
    home_team_id: UUID | None
    away_team_id: UUID | None
    home_abbreviation: str | None
    away_abbreviation: str | None
    #: Strongest tier already recorded for this game, or None.
    existing_tier: int | None
    existing_scores: tuple[int, int] | None


class NflFinalIngestService:
    """Detects finals, persists them with provenance, and stops when done."""

    def __init__(self, session: Session, *, source: NflverseScheduleSource | None = None) -> None:
        self.session = session
        self.source = source or NflverseScheduleSource()

    # -- what still needs a result ---------------------------------------
    def pending(self, *, now: datetime | None = None) -> list[PendingGame]:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(minutes=SETTLE_GRACE_MINUTES)
        correction_horizon = now - timedelta(days=CORRECTION_WINDOW_DAYS)

        rows = list(self.session.execute(
            select(Game, ShadowForecast.event_id)
            .join(Season, Season.id == Game.season_id)
            .join(League, League.id == Season.league_id)
            .join(ShadowForecast, ShadowForecast.canonical_game_id == Game.id)
            .where(League.code == LEAGUE_CODE, Game.starts_at <= cutoff)
            .order_by(Game.starts_at)
        ).all())

        seen: set[UUID] = set()
        pending: list[PendingGame] = []
        for game, event_id in rows:
            if game.id in seen:
                continue
            seen.add(game.id)
            kickoff = _aware(game.starts_at)
            strongest = self._strongest_observation(game.id)
            existing_tier = result_tier(strongest.source) if strongest is not None else None
            existing_scores = (
                (int(strongest.home_score), int(strongest.away_score))
                if strongest is not None
                and strongest.home_score is not None and strongest.away_score is not None
                else None
            )
            # Stop polling a game that is automatically finalised and older
            # than the window in which a correction could still arrive.
            if (existing_tier is not None and existing_tier <= 2
                    and kickoff is not None and kickoff < correction_horizon):
                continue
            sides = {row.is_home: row for row in self.session.scalars(
                select(GameTeam).where(GameTeam.game_id == game.id))}
            home_side, away_side = sides.get(True), sides.get(False)
            pending.append(PendingGame(
                game=game, event_id=event_id, kickoff=kickoff,
                home_team_id=home_side.team_id if home_side else None,
                away_team_id=away_side.team_id if away_side else None,
                home_abbreviation=self._abbreviation(home_side.team_id if home_side else None),
                away_abbreviation=self._abbreviation(away_side.team_id if away_side else None),
                existing_tier=existing_tier, existing_scores=existing_scores,
            ))
        pending.sort(key=lambda entry: entry.kickoff or datetime.max.replace(tzinfo=UTC))
        return pending

    def _abbreviation(self, team_id: UUID | None) -> str | None:
        if team_id is None:
            return None
        team = self.session.get(Team, team_id)
        return team.abbreviation if team else None

    def _strongest_observation(self, game_id: UUID) -> GameStateObservation | None:
        """The best-provenanced FINAL recorded for a game, latest breaking ties."""

        observations = list(self.session.scalars(
            select(GameStateObservation).where(
                GameStateObservation.game_id == game_id,
                GameStateObservation.status == GameStatus.FINAL.value,
            )
        ))
        if not observations:
            return None
        floor = datetime.min.replace(tzinfo=UTC)
        return min(observations, key=lambda row: (
            result_tier(row.source),
            -(_aware(row.available_as_of) or floor).timestamp(),
            # Same rank and same knowledge time is contradictory evidence, not
            # a correction. Break it on the checksum so the answer is stable.
            str(row.payload_checksum or ""),
        ))

    # -- the run ----------------------------------------------------------
    async def ingest(self, *, now: datetime | None = None, dry_run: bool = False,
                     archive: Any = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        summary: dict[str, Any] = {
            "job": JOB_NAME, "source": NFLVERSE, "attribution": ATTRIBUTION,
            "requests": 0, "bytes_downloaded_note": "timestamp probe only unless the feed moved",
            "games_pending": 0, "finals_detected": 0, "observations_written": 0,
            "observations_reused": 0, "projections_updated": 0, "skipped": [],
            "sportsbook_quote_credits_spent": 0, "provider_calls": {"THE_ODDS_API": 0,
                                                                    "SPORTSGAMEODDS": 0},
            "models_executed": 0, "positions_created": 0, "official_decisions_created": 0,
        }

        pending = self.pending(now=now)
        summary["games_pending"] = len(pending)
        if not pending:
            summary["status"] = "NO_GAME_AWAITING_A_FINAL"
            return summary

        # The cheap probe first: if the feed has not moved since our last
        # successful run, nothing it could tell us has changed.
        last_seen = self._last_feed_stamp()
        summary["requests"] += 1
        stamp = await self.source.probe()
        summary["feed_last_updated"] = stamp
        summary["feed_last_seen"] = last_seen
        if stamp is not None and last_seen is not None and stamp == last_seen:
            summary["status"] = "FEED_UNCHANGED_SINCE_LAST_RUN"
            return summary

        summary["requests"] += 1
        schedule = await self.source.fetch(now=now)
        summary["feed_last_updated"] = schedule.last_updated
        summary["feed_rows"] = schedule.row_count
        summary["feed_checksum"] = schedule.payload_checksum

        acted: list[dict[str, Any]] = []
        for entry in pending:
            outcome = self._apply(entry, schedule, now=now, dry_run=dry_run)
            if outcome.get("skipped"):
                summary["skipped"].append(outcome)
                continue
            acted.append(outcome)
            summary["finals_detected"] += 1
            if outcome.get("written"):
                summary["observations_written"] += 1
            else:
                summary["observations_reused"] += 1
            if outcome.get("projection_updated"):
                summary["projections_updated"] += 1

        summary["games"] = acted
        summary["status"] = "OK"
        if not dry_run:
            summary["ingestion_run_id"] = str(self._record_run(schedule, summary, acted,
                                                               archive=archive, now=now))
        return summary

    def _last_feed_stamp(self) -> str | None:
        run = self.session.scalars(
            select(IngestionRun)
            .where(IngestionRun.source == NFLVERSE, IngestionRun.job_name == JOB_NAME,
                   IngestionRun.status == RunStatus.SUCCEEDED)
            .order_by(IngestionRun.completed_at.desc().nullslast())
            .limit(1)
        ).first()
        if run is None:
            return None
        return (run.source_metadata or {}).get("feed_last_updated")

    # -- one game ---------------------------------------------------------
    def _apply(
        self, entry: PendingGame, schedule: NflverseSchedule, *,
        now: datetime, dry_run: bool,
    ) -> dict[str, Any]:
        base = {"event_id": entry.event_id, "game_id": str(entry.game.id),
                "matchup": f"{entry.away_abbreviation} @ {entry.home_abbreviation}"}
        feed_game = schedule.get(entry.event_id)
        if feed_game is None:
            return {**base, "skipped": NOT_IN_FEED,
                    "detail": "The schedules release carries no row for this event id. A "
                              "cancelled game looks like this; so does an id we got wrong. "
                              "Nothing is recorded either way."}
        if not feed_game.is_final:
            moved = self._reschedule_note(entry, feed_game)
            return {**base, "skipped": RESCHEDULED if moved else AWAITING_FINAL,
                    "detail": moved or "The feed publishes no score for this game yet. A game in "
                                       "progress and a postponed game look identical here, so "
                                       "neither is recorded as final.",
                    "feed_kickoff_utc": (feed_game.scheduled_utc.isoformat()
                                         if feed_game.scheduled_utc else None)}

        home_score = int(feed_game.home_score)
        away_score = int(feed_game.away_score)
        tier = result_tier(NFLVERSE)

        # Never demote. If a governed provider already recorded this game, a
        # free mirror does not get to replace it.
        if entry.existing_tier is not None and entry.existing_tier < tier:
            return {**base, "skipped": WEAKER_SOURCE,
                    "detail": f"A stronger source (tier {entry.existing_tier}) already recorded "
                              f"this final; {NFLVERSE} is tier {tier} and does not replace it."}

        evidence = feed_game.result_evidence()
        # Sanity: the feed's sides must be the sides we hold. A silent home/away
        # swap would invert every margin we score.
        #
        # Compared through the feed's club vocabulary rather than by raw string
        # equality. The two vocabularies agree on 31 of 32 clubs and differ on
        # one spelling -- nflverse writes the Rams as "LA", AISP as "LAR" -- so
        # a plain != rejected every Rams home game as an orientation mismatch
        # and left them permanently unscorable. The guard is unchanged for a
        # real disagreement: same_club() is False for two different clubs, and
        # False for an unrecognised token.
        if entry.home_abbreviation and feed_game.home_team and not same_club(
            feed_game.home_team, entry.home_abbreviation
        ):
            return {**base, "skipped": "HOME_SIDE_MISMATCH",
                    "detail": f"The feed calls {feed_game.home_team} the home side; AISP holds "
                              f"{entry.home_abbreviation}. Refusing to record a result whose "
                              "sides we cannot confirm."}

        checksum = canonical_sha256(evidence)
        existing = self.session.scalar(select(NflFinalObservation).where(
            NflFinalObservation.game_id == entry.game.id,
            NflFinalObservation.source == NFLVERSE,
            NflFinalObservation.payload_checksum == checksum,
        ))
        record = {
            **base,
            "home_score": home_score, "away_score": away_score,
            "overtime": feed_game.overtime,
            "source": NFLVERSE, "result_tier": tier,
            "payload_checksum": checksum,
            "result_identity": f"{NFLVERSE}:{checksum}",
            "written": False, "projection_updated": False,
            "upgrades_from": None if entry.existing_tier is None else entry.existing_tier,
            "agrees_with_existing": (entry.existing_scores == (home_score, away_score)
                                     if entry.existing_scores else None),
        }
        if existing is not None or dry_run:
            record["reused"] = existing is not None
            record["dry_run"] = dry_run
            if existing is not None:
                # Still reconcile the projection: an earlier run may have
                # written the observation and failed before projecting it.
                record["projection_updated"] = (
                    False if dry_run else self._project(entry.game))
            return record

        state = GameStateObservation(
            game_id=entry.game.id,
            home_team_id=entry.home_team_id, away_team_id=entry.away_team_id,
            scheduled_at=entry.game.starts_at, status=GameStatus.FINAL.value,
            home_score=home_score, away_score=away_score,
            final_at=now, observed_at=now, available_as_of=now,
            source=NFLVERSE, source_mode=SOURCE_MODE,
            source_record_id=entry.event_id, payload_checksum=checksum,
        )
        self.session.add(state)
        self.session.flush()
        self.session.add(NflFinalObservation(
            game_id=entry.game.id, source_event_id=entry.event_id,
            game_state_observation_id=state.id,
            source=NFLVERSE, source_mode=SOURCE_MODE, result_tier=tier,
            status="FINAL", home_score=home_score, away_score=away_score,
            overtime=feed_game.overtime, season=feed_game.season,
            game_type=feed_game.game_type, week=feed_game.week,
            source_last_updated=schedule.last_updated, retrieved_at=schedule.fetched_at,
            payload_checksum=checksum, feed_checksum=schedule.payload_checksum,
            raw_row={**evidence, "gameday": feed_game.gameday, "gametime": feed_game.gametime,
                     "location": feed_game.location, "espn_id": feed_game.espn_id,
                     "gsis_id": feed_game.gsis_id, "pfr_id": feed_game.pfr_id,
                     "old_game_id": feed_game.old_game_id},
        ))
        self.session.flush()
        record["written"] = True
        record["projection_updated"] = self._project(entry.game)
        return record

    def _reschedule_note(self, entry: PendingGame, feed_game: NflverseGame) -> str | None:
        feed_kickoff = feed_game.scheduled_utc
        if feed_kickoff is None or entry.kickoff is None:
            return None
        if abs((feed_kickoff - entry.kickoff).total_seconds()) < 3600:
            return None
        return (f"The feed now schedules this game for {feed_kickoff.isoformat()}; AISP holds "
                f"{entry.kickoff.isoformat()}. The game is left alone: forecast eligibility was "
                "decided against the schedule as it stood, and moving the kickoff now would "
                "re-decide it with hindsight.")

    # -- the projection ---------------------------------------------------
    def _project(self, game: Game) -> bool:
        """Point ``games`` at the best-provenanced final recorded for it.

        The observations are the evidence; the game row is a convenience view
        of them. Recomputing it from the strongest observation -- rather than
        writing whatever arrived last -- is what stops a late operator entry
        from overwriting a provider-verified score.
        """

        strongest = self._strongest_observation(game.id)
        if strongest is None or strongest.home_score is None or strongest.away_score is None:
            return False
        home, away = int(strongest.home_score), int(strongest.away_score)
        changed = (game.status != GameStatus.FINAL
                   or game.home_score != home or game.away_score != away)
        game.status = GameStatus.FINAL
        game.home_score = home
        game.away_score = away
        game.final_at = _aware(strongest.final_at) or _aware(strongest.observed_at)
        for side in self.session.scalars(select(GameTeam).where(GameTeam.game_id == game.id)):
            side.score = home if side.is_home else away
        self.session.flush()
        return changed

    # -- provenance -------------------------------------------------------
    def _record_run(
        self, schedule: NflverseSchedule, summary: dict[str, Any],
        acted: list[dict[str, Any]], *, archive: Any, now: datetime,
    ) -> UUID:
        reference = None
        if archive is not None:
            # The rows we acted on, not the whole 2 MB CSV: the full payload
            # checksum is recorded beside them, so anyone can refetch the
            # release and confirm these rows came out of that exact file.
            written = archive.write(NFLVERSE, {
                "metadata": schedule.envelope_metadata(),
                "acted_rows": acted,
            }, source_event_timestamp=schedule.fetched_at, schema_version="1")
            reference = written.raw_file_reference
        run = IngestionRun(
            source=NFLVERSE, job_name=JOB_NAME, status=RunStatus.SUCCEEDED,
            started_at=schedule.fetched_at, completed_at=now,
            records_processed=summary["finals_detected"],
            raw_reference=reference, schema_version="NFLVERSE_SCHEDULES_V1",
            source_metadata={
                **schedule.envelope_metadata(),
                "observations_written": summary["observations_written"],
                "observations_reused": summary["observations_reused"],
                "skipped": summary["skipped"],
            },
        )
        self.session.add(run)
        self.session.flush()
        return run.id


__all__ = [
    "ALREADY_CURRENT", "AWAITING_FINAL", "CORRECTION_WINDOW_DAYS", "JOB_NAME", "LEAGUE_CODE",
    "NOT_IN_FEED", "NflFinalIngestService", "PendingGame", "RESCHEDULED",
    "SETTLE_GRACE_MINUTES", "WEAKER_SOURCE",
]
