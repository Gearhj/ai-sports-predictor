"""Record an NFL final that the provider path has not supplied.

WHY THIS EXISTS
---------------
AISP's provider-verified NFL result path is ``finalize-nfl-shadow-games``,
which reads The Odds API scores endpoint. It is disabled
(``NFL_SHADOW_ODDS_ENABLED=false``) and enabling it spends provider credit, so
a completed game can sit unscored indefinitely while the forecast that
predicted it goes unmeasured.

This is the operator's own route to the same place. It is NOT a shortcut past
verification -- it applies the identical identity checks the provider path
applies -- but the score comes from a human rather than a feed, and every
record it writes says so:

    source = OPERATOR_ENTERED     (never THE_ODDS_API)

That string travels with the result into the evaluation's ``result_source`` and
onto the page, so nobody can mistake an operator-entered final for a verified
one. When the provider path is later enabled and returns the same game, its
observation has a different source and checksum, which makes it a NEW result
identity and therefore a NEW evaluation beside this one -- the first score
stays exactly as it was when it was made.

WHAT IT REFUSES
---------------
- a game that is not a research-shadow NFL game;
- a game that has not kicked off;
- a score for sides that do not match the game's own home/away rows;
- a different score for a game already recorded FINAL.

    python -m scripts.record_nfl_final --event 2026_01_NE_SEA --home 13 --away 10 \
        --observed-at 2026-09-10T03:30:00Z --note "official box score" --apply
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json

from sqlalchemy import select

from app.db.session import SessionLocal
from app.gen2.exchange.canonical_json import canonical_sha256
from app.models import (
    Game, GameStateObservation, GameStatus, GameTeam, ShadowForecast, Team,
)

SOURCE = "OPERATOR_ENTERED"
SOURCE_MODE = "RESEARCH_SHADOW"


class OperatorResultError(RuntimeError):
    """The final cannot be recorded as supplied."""


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def record_final(
    session, *, event_id: str, home_score: int, away_score: int,
    observed_at: datetime, note: str, apply: bool, now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    observed_at = _aware(observed_at)

    forecast = session.scalars(
        select(ShadowForecast).where(ShadowForecast.event_id == event_id)
        .order_by(ShadowForecast.available_as_of_utc.desc()).limit(1)
    ).first()
    if forecast is None or forecast.canonical_game_id is None:
        raise OperatorResultError(f"No registered NFL forecast binds event {event_id!r} to a game.")
    game = session.get(Game, forecast.canonical_game_id)
    if game is None or game.source_mode != SOURCE_MODE:
        raise OperatorResultError("The mapped game is not a research-shadow game.")

    kickoff = _aware(game.starts_at)
    if kickoff >= now:
        raise OperatorResultError(
            f"Kickoff {kickoff.isoformat()} has not passed; a final cannot exist yet.")
    if observed_at < kickoff:
        raise OperatorResultError(
            "The observation time precedes kickoff, so it cannot be an observation of the final.")

    sides = {row.is_home: row for row in session.scalars(
        select(GameTeam).where(GameTeam.game_id == game.id))}
    if set(sides) != {False, True}:
        raise OperatorResultError("The game lacks exact home/away rows.")
    home_team = session.get(Team, sides[True].team_id)
    away_team = session.get(Team, sides[False].team_id)

    if game.status == GameStatus.FINAL:
        if game.home_score != home_score or game.away_score != away_score:
            raise OperatorResultError(
                f"This game is already FINAL at {game.home_score}-{game.away_score}; "
                f"{home_score}-{away_score} conflicts with it. A correction must supersede "
                "the existing observation deliberately, not overwrite it here.")

    evidence = {
        "event_id": event_id,
        "canonical_game_id": str(game.id),
        "scheduled_start_utc": kickoff.isoformat(),
        "home_team_id": home_team.abbreviation if home_team else None,
        "away_team_id": away_team.abbreviation if away_team else None,
        "home_score": home_score,
        "away_score": away_score,
        "completed": True,
        "entered_by": SOURCE,
        "operator_note": note,
        "observed_at": observed_at.isoformat(),
    }
    checksum = canonical_sha256(evidence)

    existing = session.scalar(select(GameStateObservation).where(
        GameStateObservation.game_id == game.id,
        GameStateObservation.source == SOURCE,
        GameStateObservation.source_mode == SOURCE_MODE,
        GameStateObservation.payload_checksum == checksum,
    ))
    receipt = {
        "event_id": event_id, "game_id": str(game.id),
        "matchup": f"{away_team.abbreviation if away_team else '?'} @ "
                   f"{home_team.abbreviation if home_team else '?'}",
        "kickoff_utc": kickoff.isoformat(),
        "home_score": home_score, "away_score": away_score,
        "source": SOURCE, "source_mode": SOURCE_MODE,
        "payload_checksum": checksum,
        "result_identity": f"{SOURCE}:{checksum}",
        "observed_at_utc": observed_at.isoformat(),
        "note": note,
        "idempotent_reuse": existing is not None,
        "applied": False,
        "provider_calls": 0, "models_executed": 0,
        "positions_created": 0, "official_decisions_created": 0,
        "provenance_warning":
            "OPERATOR_ENTERED is not provider-verified. Every evaluation scored against this "
            "result carries the same source string, and a later provider-verified final is a "
            "separate result identity that produces its own evaluation beside this one.",
    }
    if existing is not None or not apply:
        return receipt

    session.add(GameStateObservation(
        game_id=game.id,
        home_team_id=sides[True].team_id, away_team_id=sides[False].team_id,
        scheduled_at=game.starts_at, status=GameStatus.FINAL.value,
        home_score=home_score, away_score=away_score,
        final_at=observed_at, observed_at=observed_at, available_as_of=observed_at,
        source=SOURCE, source_mode=SOURCE_MODE,
        source_record_id=event_id, payload_checksum=checksum,
    ))
    sides[True].score = home_score
    sides[False].score = away_score
    game.status = GameStatus.FINAL
    game.home_score = home_score
    game.away_score = away_score
    game.final_at = observed_at
    session.flush()
    receipt["applied"] = True
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True, help="canonical event id, e.g. 2026_01_NE_SEA")
    parser.add_argument("--home", type=int, required=True, help="home team final score")
    parser.add_argument("--away", type=int, required=True, help="away team final score")
    parser.add_argument("--observed-at", required=True,
                        help="ISO-8601 instant at which the operator observed the final")
    parser.add_argument("--note", default="operator-entered final", help="where the score came from")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    observed_at = datetime.fromisoformat(args.observed_at.replace("Z", "+00:00"))
    with SessionLocal() as session:
        receipt = record_final(
            session, event_id=args.event, home_score=args.home, away_score=args.away,
            observed_at=observed_at, note=args.note, apply=args.apply,
        )
        if args.apply and receipt["applied"]:
            session.commit()
        else:
            session.rollback()
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
