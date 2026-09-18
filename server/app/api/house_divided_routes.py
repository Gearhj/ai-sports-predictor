"""A House Divided -- the virtual competition's HTTP surface.

Reads are public, like the other consumer pages. Every write needs a signed-in
AISP login: the participant's own linked login, or an administrator entering on
their behalf. Recording a sport's start is administrator-only. Every rule is
enforced in `app.services.house_divided`, never in the browser.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies import AdminUser, CurrentUser, DatabaseSession
from app.services import house_divided as competition

router = APIRouter(prefix="/house-divided", tags=["house-divided"])


class DraftRequest(BaseModel):
    participant: str = Field(min_length=1, max_length=24)
    quote_id: UUID
    stake: str = Field(min_length=1, max_length=16)


def _refuse(exc: competition.CompetitionError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


@router.get("")
def house_divided(session: DatabaseSession,
                  sport: Literal["MLB", "NFL", "NBA", "CFB", "ALL"] = Query("MLB")) -> dict:
    """Standings, bankroll paths, the side-by-side game board and full history."""

    try:
        return competition.summary(session, sport)
    except competition.CompetitionError as exc:
        raise _refuse(exc) from exc


@router.get("/games")
def house_divided_open_games(session: DatabaseSession,
                             sport: Literal["MLB", "NFL", "NBA", "CFB"] = Query("MLB")) -> dict:
    """Games still accepting picks (before the pregame cutoff) in a started sport."""

    return {"sport": sport, "games": competition.open_games(session, sport)}


@router.get("/games/{game_id}/quotes")
def house_divided_quotes(game_id: UUID, session: DatabaseSession) -> dict:
    """Wagerable prices right now: newest per book and selection, inside 240 minutes."""

    try:
        return competition.current_quotes(session, game_id)
    except competition.CompetitionError as exc:
        raise _refuse(exc) from exc


@router.get("/me")
def house_divided_me(session: DatabaseSession, user: CurrentUser) -> dict:
    """Whom this login may enter picks for, and its editable drafts."""

    return {"can_enter_for": competition.entry_permissions(session, user),
            "is_admin": user.role.value.upper() == "ADMIN",
            "drafts": competition.drafts_for(session, user)}


def _write(session, action):
    try:
        wager = action()
    except competition.CompetitionError as exc:
        session.rollback()
        raise _refuse(exc) from exc
    session.commit()
    return {"wager_id": str(wager.id), "status": wager.status}


@router.post("/drafts")
def house_divided_create_draft(payload: DraftRequest, session: DatabaseSession, user: CurrentUser) -> dict:
    return _write(session, lambda: competition.save_draft(
        session, participant_key=payload.participant, odds_snapshot_id=payload.quote_id,
        stake=payload.stake, actor=user))


@router.post("/drafts/{wager_id}")
def house_divided_edit_draft(wager_id: UUID, payload: DraftRequest, session: DatabaseSession,
                             user: CurrentUser) -> dict:
    return _write(session, lambda: competition.save_draft(
        session, participant_key=payload.participant, odds_snapshot_id=payload.quote_id,
        stake=payload.stake, actor=user, wager_id=wager_id))


@router.post("/drafts/{wager_id}/submit")
def house_divided_submit(wager_id: UUID, session: DatabaseSession, user: CurrentUser) -> dict:
    """Lock the terms and commit the stake, or refuse and change nothing."""

    return _write(session, lambda: competition.submit(session, wager_id=wager_id, actor=user))


@router.post("/drafts/{wager_id}/discard")
def house_divided_discard(wager_id: UUID, session: DatabaseSession, user: CurrentUser) -> dict:
    return _write(session, lambda: competition.discard(session, wager_id=wager_id, actor=user))


@router.post("/sports/{sport}/start")
def house_divided_start(sport: str, session: DatabaseSession, user: AdminUser) -> dict:
    """Record a sport's prospective start. Refused if already started or ineligible."""

    try:
        result = competition.start_sport(session, sport, actor=user)
    except competition.CompetitionError as exc:
        session.rollback()
        raise _refuse(exc) from exc
    session.commit()
    return result
