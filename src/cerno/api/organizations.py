from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cerno.api.deps import ConnDep, GrantedUserDep, OrgDep
from cerno.db import DbConnection
from cerno.models import OrganizationRole
from cerno.repositories import OrganizationRepository

router = APIRouter(prefix="/organizations", tags=["organizations"])


class OrganizationBody(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    role: OrganizationRole


class OrganizationMemberBody(BaseModel):
    user_id: str
    email: str
    name: str | None = None
    role: OrganizationRole
    status: str
    joined_at: datetime


class OrganizationEntitlementsBody(BaseModel):
    organization_id: str
    plan_name: str
    contract_status: str
    seat_limit: int
    monthly_token_limit: int
    storage_quota_bytes: int
    monthly_upload_bytes: int | None
    max_file_size_bytes: int | None
    max_workspaces: int | None
    soft_limit_percent: int
    hard_limit_percent: int
    feature_flags: dict[str, object]
    notes: str | None


class OrganizationSummary(BaseModel):
    organization: OrganizationBody
    entitlements: OrganizationEntitlementsBody
    members: list[OrganizationMemberBody]


@router.get("", response_model=list[OrganizationBody])
def list_organizations(conn: ConnDep, user: GrantedUserDep) -> list[OrganizationBody]:
    rows = conn.execute(
        """SELECT o.*, m.role
           FROM organizations o
           JOIN organization_members m ON m.organization_id = o.id
           WHERE m.user_id = ?
             AND m.status = 'active'
           ORDER BY o.created_at""",
        (user.id,),
    ).fetchall()
    return [
        OrganizationBody(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            status=row["status"],
            role=row["role"],
        )
        for row in rows
    ]


@router.get("/current", response_model=OrganizationSummary)
def current_organization(
    conn: ConnDep,
    user: GrantedUserDep,
    organization: OrgDep,
) -> OrganizationSummary:
    return _organization_summary(conn, organization.id, user.id)


@router.get("/{organization_id}", response_model=OrganizationSummary)
def get_organization(
    organization_id: str,
    conn: ConnDep,
    user: GrantedUserDep,
) -> OrganizationSummary:
    membership = OrganizationRepository(conn).get_member(
        organization_id=organization_id,
        user_id=user.id,
    )
    if membership is None or membership.status != "active":
        raise HTTPException(status_code=404, detail="organization not found")
    return _organization_summary(conn, organization_id, user.id)


def _organization_summary(
    conn: DbConnection,
    organization_id: str,
    current_user_id: str,
) -> OrganizationSummary:
    row = conn.execute(
        """SELECT o.*, m.role
           FROM organizations o
           JOIN organization_members m ON m.organization_id = o.id
           WHERE o.id = ?
             AND m.user_id = ?
             AND m.status = 'active'""",
        (organization_id, current_user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="organization not found")
    entitlements = OrganizationRepository(conn).ensure_entitlements(organization_id)
    members = conn.execute(
        """SELECT u.id AS user_id, u.email, u.name, m.role, m.status, m.created_at
           FROM organization_members m
           JOIN users u ON u.id = m.user_id
           WHERE m.organization_id = ?
           ORDER BY m.created_at""",
        (organization_id,),
    ).fetchall()
    return OrganizationSummary(
        organization=OrganizationBody(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            status=row["status"],
            role=row["role"],
        ),
        entitlements=OrganizationEntitlementsBody(
            organization_id=entitlements.organization_id,
            plan_name=entitlements.plan_name,
            contract_status=entitlements.contract_status,
            seat_limit=entitlements.seat_limit,
            monthly_token_limit=entitlements.monthly_token_limit,
            storage_quota_bytes=entitlements.storage_quota_bytes,
            monthly_upload_bytes=entitlements.monthly_upload_bytes,
            max_file_size_bytes=entitlements.max_file_size_bytes,
            max_workspaces=entitlements.max_workspaces,
            soft_limit_percent=entitlements.soft_limit_percent,
            hard_limit_percent=entitlements.hard_limit_percent,
            feature_flags=entitlements.feature_flags,
            notes=entitlements.notes,
        ),
        members=[
            OrganizationMemberBody(
                user_id=member["user_id"],
                email=member["email"],
                name=member["name"],
                role=member["role"],
                status=member["status"],
                joined_at=datetime.fromisoformat(member["created_at"]),
            )
            for member in members
        ],
    )
