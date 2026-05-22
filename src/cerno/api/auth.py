from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from cerno.api.deps import ConnDep, SettingsDep, UserDep, cookie_serializer
from cerno.auth import build_oauth
from cerno.config import Settings
from cerno.models import AccessStatus, SiteRole
from cerno.repositories import (
    AnalyticsRepository,
    ApprovedEmailRepository,
    OrganizationRepository,
    UserRepository,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class CurrentUser(BaseModel):
    id: str
    email: str
    name: str | None = None
    picture: str | None = None
    access_status: AccessStatus = "pending"
    site_role: SiteRole = "user"
    organizations: list[CurrentOrganization] = Field(default_factory=list)


class CurrentOrganization(BaseModel):
    id: str
    name: str
    slug: str


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
    approved_emails = ApprovedEmailRepository(conn)
    is_site_owner = email.strip().lower() in settings.site_owner_email_set()
    user = UserRepository(conn).upsert_from_google(
        google_sub=sub,
        email=email,
        name=userinfo.get("name"),
        picture=userinfo.get("picture"),
        email_approved=approved_emails.is_approved(email) or is_site_owner,
    )
    organization_id = None
    if user.access_status == "granted":
        organization_id = OrganizationRepository(conn).ensure_personal_for_user(user).id
    AnalyticsRepository(conn).record_product_event(
        event_name="user_signed_in",
        organization_id=organization_id,
        user_id=user.id,
        metadata={"site_role": "site_owner" if is_site_owner else "user"},
    )
    conn.commit()
    response = RedirectResponse(url=settings.frontend_origin, status_code=302)
    _set_session_cookie(response, settings=settings, user_id=user.id)
    return response


@router.get("/me", response_model=CurrentUser)
def me(conn: ConnDep, settings: SettingsDep, user: UserDep) -> CurrentUser:
    is_site_owner = user.email.strip().lower() in settings.site_owner_email_set()
    approved = ApprovedEmailRepository(conn).is_approved(user.email) or is_site_owner
    synced = UserRepository(conn).sync_allowlist_status(user.id, email_approved=approved)
    if synced is None:
        raise HTTPException(status_code=401, detail="user not found")
    org_repo = OrganizationRepository(conn)
    if synced.access_status == "granted":
        org_repo.ensure_personal_for_user(synced)
    organizations = org_repo.list_for_user(synced.id)
    conn.commit()
    return CurrentUser(
        id=synced.id,
        email=synced.email,
        name=synced.name,
        picture=synced.picture,
        access_status=synced.access_status,
        site_role="site_owner" if is_site_owner else "user",
        organizations=[
            CurrentOrganization(id=org.id, name=org.name, slug=org.slug)
            for org in organizations
        ],
    )


@router.post("/logout", status_code=204)
def logout(settings: SettingsDep) -> Response:
    response = Response(status_code=204)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response


__all__ = ["router"]
