"""Provider payloads into canonical NBA rows, idempotently and revisionably.

THE THREE RULES THIS ENFORCES
-----------------------------
1.  **Re-importing the same bytes writes nothing new.** Identity is the payload
    checksum for a response, and for a game it is a checksum over the fields
    that would make it a different fact. Running the command twice is a no-op,
    which is what makes a recovery rerun safe.

2.  **A provider correction is a successor, not an edit.** When a game comes
    back with different scores or status, the previous row keeps everything it
    had, stops being current, and the new row records ``revision + 1`` and
    points at it. Nothing is overwritten, so what we believed on Tuesday is
    still readable on Wednesday.

3.  **Identity is the provider's id, not a name and a date.** A rescheduled game
    keeps its ``GameID``, so the mapping survives the move. Joining on team
    names and a date would silently create a second game the first time a
    postponement happened.

DATA MODE TRAVELS WITH EVERY ROW
--------------------------------
Nothing here decides that data is real. The caller supplies the mode the
entitlement assessment established, and it is written onto every row. A FIXTURE
import can never become REAL by being reimported.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_sources.nba_sportsdataio import (
    Envelope, NbaBoxScore, NbaGame as ParsedGame, NbaPlayer as ParsedPlayer,
    NbaTeam as ParsedTeam, SEASON_TYPES,
)
from app.models.nba import (
    NbaGame, NbaPlayer, NbaPlayerGameStat, NbaRosterMembership,
    NbaSourcePayload, NbaTeam, NbaTeamGameStat,
)
from app.nba.entitlement import DataMode


class NbaIntakeError(RuntimeError):
    """The intake refused. The message is the reason."""


@dataclass
class IntakeOutcome:
    payloads_stored: int = 0
    payloads_reused: int = 0
    teams_created: int = 0
    players_created: int = 0
    games_created: int = 0
    games_reused: int = 0
    game_revisions: int = 0
    team_game_rows: int = 0
    player_game_rows: int = 0
    roster_rows: int = 0
    bytes_admitted: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _game_identity(game: ParsedGame, data_mode: DataMode, revision: int) -> str:
    """A checksum over the facts that make this a distinct version of a game."""

    body = {
        "providerGameId": game.provider_game_id,
        "dataMode": str(data_mode),
        "revision": revision,
        "status": game.status,
        "homeScore": game.home_score,
        "awayScore": game.away_score,
        "seasonType": game.season_type,
        "gameDay": game.game_day.isoformat() if game.game_day else None,
        "scheduledLocal": game.datetime_local.isoformat() if game.datetime_local else None,
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _materially_differs(stored: NbaGame, game: ParsedGame) -> bool:
    """Is this a correction, or the same fact arriving again?

    Only the fields that change what the game WAS count. A new retrieval time or
    a provider ``Updated`` bump with identical content is not a correction, and
    treating it as one would manufacture revisions on every poll.
    """

    return (
        stored.status != game.status
        or stored.home_score != game.home_score
        or stored.away_score != game.away_score
        or stored.is_final != game.is_final
        or stored.game_day != game.game_day
        or stored.scheduled_local != game.datetime_local
        or stored.season_type != game.season_type
    )


class NbaIntake:
    """Writes canonical NBA rows. Never calls a provider."""

    def __init__(self, session: Session, *, data_mode: DataMode,
                 retention_permitted: bool = False) -> None:
        if data_mode is DataMode.REAL and not retention_permitted:
            # Not a refusal to ingest, a refusal to KEEP. Checksums and byte
            # counts are still recorded so a later copy can be proven identical.
            pass
        self.session = session
        self.data_mode = data_mode
        self.retention_permitted = retention_permitted

    # -- payloads --------------------------------------------------------
    def store_payload(self, envelope: Envelope, *, ingestion_run_id=None) -> tuple[NbaSourcePayload, bool]:
        """Record one original response. Byte-identical re-import is a no-op."""

        if envelope.data_mode is not self.data_mode:
            raise NbaIntakeError(
                f"envelope data mode {envelope.data_mode} does not match the "
                f"intake's {self.data_mode}; a mode is never coerced"
            )
        existing = self.session.scalar(
            select(NbaSourcePayload).where(
                NbaSourcePayload.payload_checksum == envelope.payload_checksum,
                NbaSourcePayload.endpoint_key == envelope.endpoint_key,
            )
        )
        if existing is not None:
            return existing, False
        row = NbaSourcePayload(
            source_identity=envelope.source_identity,
            endpoint_key=envelope.endpoint_key,
            url_template=envelope.url_template,
            parameters=envelope.parameters,
            payload_checksum=envelope.payload_checksum,
            byte_count=envelope.byte_count,
            payload=envelope.payload if self.retention_permitted else None,
            retention_permitted=self.retention_permitted,
            retrieved_at=envelope.retrieved_at,
            provider_updated_at=envelope.provider_updated_at,
            data_mode=str(envelope.data_mode),
            ingestion_run_id=ingestion_run_id,
            notes={"adapterNotes": list(envelope.notes)},
        )
        self.session.add(row)
        self.session.flush()
        return row, True

    # -- identity --------------------------------------------------------
    def team(self, parsed: ParsedTeam, *, now: datetime, payload_id=None) -> tuple[NbaTeam, bool]:
        existing = self.session.scalar(
            select(NbaTeam).where(
                NbaTeam.provider_team_id == parsed.provider_team_id,
                NbaTeam.data_mode == str(self.data_mode),
            )
        )
        if existing is not None:
            return existing, False
        row = NbaTeam(
            provider_team_id=parsed.provider_team_id, team_key=parsed.key,
            city=parsed.city, name=parsed.name, active=parsed.active,
            data_mode=str(self.data_mode), source_payload_id=payload_id,
            first_seen_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return row, True

    def team_by_provider_id(self, provider_team_id: int | None) -> NbaTeam | None:
        if provider_team_id is None:
            return None
        return self.session.scalar(
            select(NbaTeam).where(
                NbaTeam.provider_team_id == provider_team_id,
                NbaTeam.data_mode == str(self.data_mode),
            )
        )

    def player(self, parsed: ParsedPlayer, *, now: datetime, payload_id=None) -> tuple[NbaPlayer, bool]:
        existing = self.session.scalar(
            select(NbaPlayer).where(
                NbaPlayer.provider_player_id == parsed.provider_player_id,
                NbaPlayer.data_mode == str(self.data_mode),
            )
        )
        if existing is not None:
            return existing, False
        row = NbaPlayer(
            provider_player_id=parsed.provider_player_id,
            first_name=parsed.first_name, last_name=parsed.last_name,
            position=parsed.position, data_mode=str(self.data_mode),
            source_payload_id=payload_id, first_seen_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return row, True

    def roster(self, parsed: ParsedPlayer, team: NbaTeam, player: NbaPlayer, *,
               observed_at: datetime, payload_id=None) -> bool:
        """Current roster state. Explicitly not game participation."""

        # Keyed on the PAYLOAD, not on wall-clock time. Re-reading the same
        # response is the same observation; keying on `now` would record a new
        # roster fact on every run and turn a replay into apparent churn.
        stmt = select(NbaRosterMembership).where(
            NbaRosterMembership.nba_team_id == team.id,
            NbaRosterMembership.nba_player_id == player.id,
        )
        stmt = (stmt.where(NbaRosterMembership.source_payload_id == payload_id)
                if payload_id is not None
                else stmt.where(NbaRosterMembership.observed_at == observed_at))
        if self.session.scalar(stmt) is not None:
            return False
        self.session.add(NbaRosterMembership(
            nba_team_id=team.id, nba_player_id=player.id, jersey=parsed.jersey,
            status=parsed.status, position=parsed.position,
            observed_at=observed_at, data_mode=str(self.data_mode),
            source_payload_id=payload_id,
        ))
        self.session.flush()
        return True

    # -- games -----------------------------------------------------------
    def game(self, parsed: ParsedGame, *, retrieved_at: datetime,
             payload_id=None) -> tuple[NbaGame | None, str]:
        """Insert, reuse, or append a correction. Returns the row and what happened."""

        home = self.team_by_provider_id(parsed.home_team_id)
        away = self.team_by_provider_id(parsed.away_team_id)
        if home is None or away is None:
            # Identity first. A game whose clubs are unknown is not filed under
            # invented teams; it is reported so the identity gap is visible.
            return None, "UNRESOLVED_TEAM_IDENTITY"
        if home.id == away.id:
            return None, "HOME_AND_AWAY_ARE_THE_SAME_TEAM"

        current = self.session.scalar(
            select(NbaGame).where(
                NbaGame.provider_game_id == parsed.provider_game_id,
                NbaGame.data_mode == str(self.data_mode),
                NbaGame.is_current.is_(True),
            ).order_by(NbaGame.revision.desc())
        )
        if current is not None and not _materially_differs(current, parsed):
            # Same facts, but a richer payload. The schedule endpoint carries no
            # period lines; the box score does. Filling an EMPTY field from a
            # fuller response of the same version is recording something that
            # was absent, not changing something that was recorded -- so it is
            # not a revision, and refusing it would throw the period scores away.
            if parsed.quarters and not (current.period_scores or {}).get("quarters"):
                current.period_scores = {"quarters": list(parsed.quarters)}
                self.session.flush()
                return current, "ENRICHED"
            return current, "REUSED"

        # This version may already be on file as an EARLIER revision -- which is
        # what happens when the same directory is replayed and an older payload
        # is re-read after a correction. Without this check the two versions
        # would take turns superseding each other and manufacture a new revision
        # on every run.
        prior = self.session.scalars(
            select(NbaGame).where(
                NbaGame.provider_game_id == parsed.provider_game_id,
                NbaGame.data_mode == str(self.data_mode),
            )
        ).all()
        for candidate in prior:
            if not _materially_differs(candidate, parsed):
                return candidate, "REUSED_EARLIER_REVISION"

        # A correction must be NEWER than what it corrects. The provider's own
        # Updated stamp decides; a payload that is merely different but not
        # later is a stale re-presentation, not a revision.
        if current is not None and parsed.updated_at is not None:
            stored_updated = current.provider_updated_at
            if stored_updated is not None:
                incoming = parsed.updated_at
                if incoming.tzinfo is None and stored_updated.tzinfo is not None:
                    incoming = incoming.replace(tzinfo=stored_updated.tzinfo)
                elif incoming.tzinfo is not None and stored_updated.tzinfo is None:
                    stored_updated = stored_updated.replace(tzinfo=incoming.tzinfo)
                # STRICTLY older only. An equal stamp with materially different
                # content is ambiguous, and dropping it would silently lose a
                # real reschedule that the provider did not re-stamp. Replay
                # oscillation is already prevented by the earlier-revision check
                # above, so accepting the equal case costs nothing.
                if incoming < stored_updated:
                    return current, "STALE_NOT_A_CORRECTION"

        revision = (current.revision + 1) if current is not None else 1
        row = NbaGame(
            provider_game_id=parsed.provider_game_id,
            season=parsed.season, season_type=parsed.season_type,
            season_type_label=SEASON_TYPES.get(parsed.season_type,
                                               f"UNKNOWN_{parsed.season_type}"),
            status=parsed.status,
            home_nba_team_id=home.id, away_nba_team_id=away.id,
            game_day=parsed.game_day,
            scheduled_local=parsed.datetime_local,
            scheduled_utc=parsed.datetime_utc,
            home_score=parsed.home_score, away_score=parsed.away_score,
            period_scores={"quarters": list(parsed.quarters)},
            is_final=parsed.is_final,
            provider_updated_at=parsed.updated_at,
            retrieved_at=retrieved_at,
            revision=revision,
            supersedes_game_id=current.id if current is not None else None,
            is_current=True,
            identity_checksum=_game_identity(parsed, self.data_mode, revision),
            data_mode=str(self.data_mode),
            source_payload_id=payload_id,
        )
        if current is not None:
            # The prior row keeps every value it was written with; only its
            # currency flag moves.
            current.is_current = False
        self.session.add(row)
        self.session.flush()
        return row, ("REVISED" if current is not None else "CREATED")

    def box_score(self, box: NbaBoxScore, *, retrieved_at: datetime,
                  payload_id=None, outcome: IntakeOutcome) -> None:
        """Team and player rows for one game, attributed to the right clubs."""

        game, disposition = self.game(box.game, retrieved_at=retrieved_at,
                                      payload_id=payload_id)
        if game is None:
            outcome.notes.append(
                f"game {box.game.provider_game_id}: {disposition}")
            return
        if disposition == "CREATED":
            outcome.games_created += 1
        elif disposition == "REVISED":
            outcome.game_revisions += 1
        else:
            outcome.games_reused += 1
            if disposition not in {"REUSED", "REUSED_EARLIER_REVISION",
                                   "STALE_NOT_A_CORRECTION", "ENRICHED"}:
                outcome.notes.append(
                    f"game {box.game.provider_game_id}: {disposition}")

        for team_game in box.team_games:
            team = self.team_by_provider_id(team_game.provider_team_id)
            if team is None:
                outcome.notes.append(
                    f"team game {team_game.provider_team_id}: UNRESOLVED_TEAM_IDENTITY")
                continue
            existing = self.session.scalar(select(NbaTeamGameStat).where(
                NbaTeamGameStat.nba_game_id == game.id,
                NbaTeamGameStat.nba_team_id == team.id))
            if existing is not None:
                continue
            self.session.add(NbaTeamGameStat(
                nba_game_id=game.id, nba_team_id=team.id,
                opponent_nba_team_id=getattr(
                    self.team_by_provider_id(team_game.opponent_team_id), "id", None),
                is_home=team_game.is_home, points=team_game.points,
                stats=team_game.stats, data_mode=str(self.data_mode),
                source_payload_id=payload_id, retrieved_at=retrieved_at,
            ))
            outcome.team_game_rows += 1

        for player_game in box.player_games:
            player = self.session.scalar(select(NbaPlayer).where(
                NbaPlayer.provider_player_id == player_game.provider_player_id,
                NbaPlayer.data_mode == str(self.data_mode)))
            if player is None:
                parsed = ParsedPlayer(
                    provider_player_id=player_game.provider_player_id,
                    provider_team_id=player_game.provider_team_id,
                    first_name=None, last_name=player_game.name,
                    position=player_game.position, jersey=None, status=None,
                )
                player, created = self.player(parsed, now=retrieved_at,
                                              payload_id=payload_id)
                if created:
                    outcome.players_created += 1
            existing = self.session.scalar(select(NbaPlayerGameStat).where(
                NbaPlayerGameStat.nba_game_id == game.id,
                NbaPlayerGameStat.nba_player_id == player.id))
            if existing is not None:
                continue
            self.session.add(NbaPlayerGameStat(
                nba_game_id=game.id, nba_player_id=player.id,
                nba_team_id=getattr(
                    self.team_by_provider_id(player_game.provider_team_id), "id", None),
                opponent_nba_team_id=getattr(
                    self.team_by_provider_id(player_game.opponent_team_id), "id", None),
                provider_stat_id=player_game.provider_stat_id,
                started=player_game.started, minutes=player_game.minutes,
                points=player_game.points, stats=player_game.stats,
                data_mode=str(self.data_mode), source_payload_id=payload_id,
                retrieved_at=retrieved_at,
            ))
            outcome.player_game_rows += 1
        self.session.flush()


def reconcile_scores(box: NbaBoxScore) -> dict[str, Any]:
    """Do the final score, the period lines and the player points agree?

    Reported, never repaired. A mismatch is a fact about the payload, and
    silently adjusting one side to match the other would destroy the only signal
    that something is wrong.
    """

    game = box.game
    period_home, period_away = game.period_totals()
    team_points = {tg.provider_team_id: tg.points for tg in box.team_games}
    player_sum: dict[int, int] = {}
    for pg in box.player_games:
        if pg.provider_team_id is None or pg.points is None:
            continue
        player_sum[pg.provider_team_id] = player_sum.get(pg.provider_team_id, 0) + pg.points

    checks = {
        "finalVersusPeriodTotals": {
            "home": {"final": game.home_score, "periods": period_home,
                     "agrees": period_home is not None and period_home == game.home_score},
            "away": {"final": game.away_score, "periods": period_away,
                     "agrees": period_away is not None and period_away == game.away_score},
        },
        "finalVersusTeamGamePoints": {
            "home": {"final": game.home_score,
                     "teamGame": team_points.get(game.home_team_id),
                     "agrees": team_points.get(game.home_team_id) == game.home_score},
            "away": {"final": game.away_score,
                     "teamGame": team_points.get(game.away_team_id),
                     "agrees": team_points.get(game.away_team_id) == game.away_score},
        },
        "teamPointsVersusPlayerSum": {
            str(team_id): {
                "teamGame": points, "playerSum": player_sum.get(team_id),
                "agrees": player_sum.get(team_id) == points,
            }
            for team_id, points in team_points.items()
        },
        "periodCount": len(game.quarters),
        "overtime": len(game.quarters) > 4,
        "note": "Disagreements are reported, never repaired. Nothing here edits a "
                "payload to force agreement.",
    }
    flat = [
        checks["finalVersusPeriodTotals"]["home"]["agrees"],
        checks["finalVersusPeriodTotals"]["away"]["agrees"],
        checks["finalVersusTeamGamePoints"]["home"]["agrees"],
        checks["finalVersusTeamGamePoints"]["away"]["agrees"],
        *[v["agrees"] for v in checks["teamPointsVersusPlayerSum"].values()],
    ]
    checks["allAgree"] = all(flat)
    return checks


__all__ = ["IntakeOutcome", "NbaIntake", "NbaIntakeError", "reconcile_scores"]
