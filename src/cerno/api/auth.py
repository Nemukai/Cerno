from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from cerno.api.deps import ConnDep, SettingsDep, UserDep, cookie_serializer
from cerno.auth import build_oauth
from cerno.config import Settings
from cerno.models import AccessStatus
from cerno.repositories import BetaCodeRepository, UserRepository

router = APIRouter(prefix="/auth", tags=["auth"])


class CurrentUser(BaseModel):
    id: str
    email: str
    name: str | None = None
    picture: str | None = None
    access_status: AccessStatus = "pending"


class RedeemRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


def _set_session_cookie(response: Response, *, settings: Settings, user_id: str) -> None:
    token = cookie_serializer(settings).dumps({"user_id": user_id})
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.frontend_origin.startswith("https://"),
        samesite="lax",
        path="/",
    )


@router.get("/google/login")
async def google_login(request: Request, settings: SettingsDep) -> Response:
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=503, detail="google oauth not configured on server")
    oauth = build_oauth(settings)
    redirect_uri = str(request.url_for("google_callback"))
    return cast(Response, await oauth.google.authorize_redirect(request, redirect_uri))


@router.get("/google/callback", name="google_callback")
async def google_callback(request: Request, conn: ConnDep, settings: SettingsDep) -> Response:
    oauth = build_oauth(settings)
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"oauth exchange failed: {exc}") from exc
    userinfo = token.get("userinfo") or {}
    sub = userinfo.get("sub")
    email = userinfo.get("email")
    if not sub or not email:
        raise HTTPException(status_code=400, detail="google did not return required identity")
    user = UserRepository(conn).upsert_from_google(
        google_sub=sub,
        email=email,
        name=userinfo.get("name"),
        picture=userinfo.get("picture"),
        operator_emails=settings.operator_email_set(),
    )
    response = RedirectResponse(url=settings.frontend_origin, status_code=302)
    _set_session_cookie(response, settings=settings, user_id=user.id)
    return response


@router.get("/me", response_model=CurrentUser)
def me(user: UserDep) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        email=user.email,
        name=user.name,
        picture=user.picture,
        access_status=user.access_status,
    )


@router.post("/redeem", response_model=CurrentUser)
def redeem(body: RedeemRequest, conn: ConnDep, user: UserDep) -> CurrentUser:
    if user.access_status == "revoked":
        raise HTTPException(status_code=403, detail="access has been revoked")
    user_repo = UserRepository(conn)
    if user.access_status == "granted":
        return CurrentUser(
            id=user.id,
            email=user.email,
            name=user.name,
            picture=user.picture,
            access_status="granted",
        )
    code = body.code.strip().upper()
    redeemed = BetaCodeRepository(conn).redeem(code)
    if redeemed is None:
        raise HTTPException(status_code=403, detail="invalid or expired code")
    updated = user_repo.mark_granted(user.id, redeemed.code)
    assert updated is not None
    return CurrentUser(
        id=updated.id,
        email=updated.email,
        name=updated.name,
        picture=updated.picture,
        access_status=updated.access_status,
    )


@router.post("/logout", status_code=204)
def logout(settings: SettingsDep) -> Response:
    response = Response(status_code=204)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response


__all__ = ["router"]
