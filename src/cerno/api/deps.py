from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from cerno.config import Settings, get_settings
from cerno.db import DbConnection, connect
from cerno.llm import LLMClient
from cerno.models import Organization, User
from cerno.repositories import OrganizationRepository, UserRepository


def get_conn(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Iterator[DbConnection]:
    conn = connect(settings)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_llm_client(settings: Annotated[Settings, Depends(get_settings)]) -> LLMClient:
    return LLMClient(settings=settings)


def cookie_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="cerno.session")


def get_current_user(
    request: Request,
    conn: Annotated[DbConnection, Depends(get_conn)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="not signed in")
    try:
        payload = cookie_serializer(settings).loads(token, max_age=settings.session_max_age_seconds)
    except SignatureExpired as exc:
        raise HTTPException(status_code=401, detail="session expired") from exc
    except BadSignature as exc:
        raise HTTPException(status_code=401, detail="invalid session") from exc
    user_id = payload.get("user_id") if isinstance(payload, dict) else None
    if not user_id:
        raise HTTPException(status_code=401, detail="invalid session")
    user = UserRepository(conn).get(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return user


def get_granted_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if user.access_status == "revoked":
        raise HTTPException(status_code=403, detail="access has been revoked")
    if user.access_status != "granted":
        raise HTTPException(status_code=403, detail="email is not approved for access")
    return user


def get_granted_organization(
    request: Request,
    user: Annotated[User, Depends(get_granted_user)],
    conn: Annotated[DbConnection, Depends(get_conn)],
) -> Organization:
    org_repo = OrganizationRepository(conn)
    requested_org_id = request.headers.get("x-cerno-organization-id")
    if requested_org_id:
        membership = org_repo.get_member(
            organization_id=requested_org_id,
            user_id=user.id,
        )
        org = org_repo.get(requested_org_id)
        if membership is None or membership.status != "active" or org is None:
            raise HTTPException(status_code=403, detail="organization access denied")
        if org.status != "active":
            raise HTTPException(status_code=403, detail="organization is not active")
        return org
    return org_repo.ensure_personal_for_user(user)


SettingsDep = Annotated[Settings, Depends(get_settings)]
ConnDep = Annotated[DbConnection, Depends(get_conn)]
LLMDep = Annotated[LLMClient, Depends(get_llm_client)]
UserDep = Annotated[User, Depends(get_current_user)]
GrantedUserDep = Annotated[User, Depends(get_granted_user)]
OrgDep = Annotated[Organization, Depends(get_granted_organization)]
