"""Self-serve account routes (scale plan Phase 1, item 2): verify, forgot/reset, change password, delete.

All under /auth next to register/login/me/logout (routes.py). Every route is
rate-limited in the "auth" category; the two that take an emailed token answer
one generic 400 for unknown, expired and used tokens alike.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app.api.dependencies import AppSettings, CurrentUser, DatabaseSession
from app.core.rate_limit import rate_limit
from app.schemas.api import AuthResponse
from app.services import account_service
from app.services.auth_service import AuthenticationError

router = APIRouter(prefix="/auth", tags=["authentication"])
_PASSWORD = Field(min_length=12, max_length=128)


class TokenBody(BaseModel):
    token: str = Field(min_length=16, max_length=256)


class ForgotBody(BaseModel):
    email: EmailStr


class ResetBody(TokenBody):
    password: str = _PASSWORD


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = _PASSWORD


class DeleteBody(BaseModel):
    password: str = Field(min_length=8, max_length=128)


@router.post("/verify/request", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(rate_limit("auth:verify", category="auth"))])
def request_verification(user: CurrentUser, session: DatabaseSession, settings: AppSettings) -> dict[str, Any]:
    if user.email_verified_at is not None:
        return {"status": "already_verified"}
    outcome = account_service.send_verification(session, settings, user)
    return {"status": "sent" if outcome.get("sent") else "queued", "provider": outcome.get("provider")}


@router.post("/verify/confirm", dependencies=[Depends(rate_limit("auth:verify", category="auth"))])
def confirm_verification(body: TokenBody, session: DatabaseSession) -> dict[str, Any]:
    try:
        user = account_service.confirm_verification(session, body.token)
    except account_service.TokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    return {"status": "verified", "email": user.email}


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(rate_limit("auth:forgot", category="auth"))])
def forgot_password(body: ForgotBody, session: DatabaseSession, settings: AppSettings) -> dict[str, Any]:
    account_service.request_password_reset(session, settings, body.email)
    # The same answer whether or not the email has an account.
    return {"status": "if_an_account_exists_a_reset_link_was_sent"}


@router.post("/password/reset", response_model=AuthResponse, dependencies=[Depends(rate_limit("auth:reset", category="auth"))])
def reset_password(body: ResetBody, session: DatabaseSession, settings: AppSettings) -> AuthResponse:
    try:
        return account_service.reset_password(session, settings, body.token, body.password)
    except account_service.TokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.post("/password/change", response_model=AuthResponse, dependencies=[Depends(rate_limit("auth:change", category="auth"))])
def change_password(body: ChangePasswordBody, user: CurrentUser, session: DatabaseSession, settings: AppSettings) -> AuthResponse:
    try:
        return account_service.change_password(session, settings, user, body.current_password, body.new_password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.post("/account/delete", dependencies=[Depends(rate_limit("auth:delete", category="auth"))])
def delete_account(body: DeleteBody, user: CurrentUser, session: DatabaseSession) -> dict[str, Any]:
    try:
        account_service.delete_account(session, user, body.password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    return {"status": "deleted"}
