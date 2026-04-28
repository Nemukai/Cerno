from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from cerno.api.deps import ConnDep, SettingsDep, UserDep, cookie_serializer
from cerno.auth import build_oauth
from cerno.repositories import UserRepository

router = APIRouter(prefix="/auth", tags=["auth"])


class CurrentUser(BaseModel):
    id: str
    email: str
    name: str | None = None
    picture: str | None = None


def _set_session_cookie(
    response: Response, *, settings, user_id: str
) -> None:
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
        raise HTTPException(
            status_code=503, detail="google oauth not configured on server"
        )
    oauth = build_oauth(settings)
    redirect_uri = str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback", name="google_callback")
async def google_callback(
    request: Request, conn: ConnDep, settings: SettingsDep
) -> Response:
    oauth = build_oauth(settings)
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"oauth exchange failed: {exc}"
        ) from exc
    userinfo = token.get("userinfo") or {}
    sub = userinfo.get("sub")
    email = userinfo.get("email")
    if not sub or not email:
        raise HTTPException(
            status_code=400, detail="google did not return required identity"
        )
    user = UserRepository(conn).upsert_from_google(
        google_sub=sub,
        email=email,
        name=userinfo.get("name"),
        picture=userinfo.get("picture"),
    )
    response = RedirectResponse(url=settings.frontend_origin, status_code=302)
    _set_session_cookie(response, settings=settings, user_id=user.id)
    return response


@router.get("/me", response_model=CurrentUser)
def me(user: UserDep) -> CurrentUser:
    return CurrentUser(
        id=user.id, email=user.email, name=user.name, picture=user.picture
    )


@router.post("/logout", status_code=204)
def logout(settings: SettingsDep) -> Response:
    response = Response(status_code=204)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response


__all__ = ["router"]
