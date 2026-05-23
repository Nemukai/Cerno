from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from cerno.api.deps import ConnDep, GrantedUserDep, OrgDep, SettingsDep
from cerno.db import DbConnection, loads_json
from cerno.models import EffectiveUsageLimits, OrganizationRole, UserOrganizationLimits
from cerno.repositories import (
    AnalyticsRepository,
    LLMUsageRepository,
    OrganizationRepository,
    ProcessingJobRepository,
    SessionRepository,
    UsageLimitRepository,
    UserRepository,
)

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
    daily_token_limit: int | None
    monthly_token_limit: int
    storage_quota_bytes: int
    monthly_upload_bytes: int | None
    max_file_size_bytes: int | None
    max_workspaces: int | None
    max_concurrent_jobs: int | None
    soft_limit_percent: int
    hard_limit_percent: int
    feature_flags: dict[str, object]
    notes: str | None


class OrganizationSummary(BaseModel):
    organization: OrganizationBody
    entitlements: OrganizationEntitlementsBody
    members: list[OrganizationMemberBody]


class OrganizationAdminTotals(BaseModel):
    users: int = 0
    sessions: int = 0
    storage_bytes: int = 0
    upload_bytes_month: int = 0
    upload_count_month: int = 0
    llm_tokens_month: int = 0
    chat_turns_month: int = 0
    active_jobs: int = 0
    failed_jobs_month: int = 0
    avg_chat_response_ms: float | None = None
    avg_processing_ms: float | None = None


class OrganizationAdminUserRow(BaseModel):
    user_id: str
    email: str
    name: str | None = None
    access_status: str
    membership: OrganizationMemberBody
    effective_limits: EffectiveUsageLimits
    user_limits: UserOrganizationLimits | None = None
    session_count: int = 0
    storage_bytes: int = 0
    upload_bytes_month: int = 0
    llm_tokens_month: int = 0
    chat_turns_month: int = 0
    avg_chat_response_ms: float | None = None
    last_seen_at: str


class OrganizationAdminEvent(BaseModel):
    occurred_at: str
    user_id: str | None = None
    session_id: str | None = None
    event_name: str
    metric_value: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrganizationAdminDashboard(BaseModel):
    generated_at: str
    month: str
    organization: OrganizationBody
    entitlements: OrganizationEntitlementsBody
    current_role: OrganizationRole
    totals: OrganizationAdminTotals
    users: list[OrganizationAdminUserRow]
    recent_events: list[OrganizationAdminEvent]


class OrganizationMemberCreate(BaseModel):
    email: str
    role: OrganizationRole = "member"

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("valid email required")
        return normalized


class OrganizationMemberUpdate(BaseModel):
    role: OrganizationRole


class OrganizationMemberMutationResult(BaseModel):
    status: str
    member: OrganizationMemberBody | None = None
    invite_id: str | None = None
    message: str | None = None


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


@router.get("/{organization_id}/admin/dashboard", response_model=OrganizationAdminDashboard)
def organization_admin_dashboard(
    organization_id: str,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
) -> OrganizationAdminDashboard:
    role = _require_org_admin(conn, settings, organization_id, user.id, user.email)
    return _organization_admin_dashboard(conn, organization_id, role)


@router.post(
    "/{organization_id}/admin/members",
    response_model=OrganizationMemberMutationResult,
)
def add_organization_member(
    organization_id: str,
    body: OrganizationMemberCreate,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
) -> OrganizationMemberMutationResult:
    _require_org_admin(conn, settings, organization_id, user.id, user.email)
    role = body.role
    normalized_email = body.email.strip().lower()
    org_repo = OrganizationRepository(conn)
    target_user = UserRepository(conn).get_by_email(normalized_email)
    if target_user is None:
        invite = org_repo.create_invite(
            organization_id=organization_id,
            email=normalized_email,
            role=role,
            invited_by_user_id=user.id,
        )
        AnalyticsRepository(conn).record_product_event(
            event_name="organization_member_invited",
            organization_id=organization_id,
            user_id=user.id,
            metadata={"email": normalized_email, "role": role},
        )
        conn.commit()
        return OrganizationMemberMutationResult(
            status="invited",
            invite_id=invite.id,
            message="Invite recorded. The user will still need site access approval before they can use Cerno.",
        )

    current = org_repo.get_member(organization_id=organization_id, user_id=target_user.id)
    active_member_count = org_repo.active_member_count(organization_id)
    if active_member_count == 0:
        role = "admin"
    if current is None or current.status != "active":
        entitlements = org_repo.ensure_entitlements(organization_id)
        if active_member_count >= entitlements.seat_limit:
            raise HTTPException(status_code=409, detail="organization seat limit reached")
    member = org_repo.add_member(
        organization_id=organization_id,
        user_id=target_user.id,
        role=role,
        status="active",
    )
    AnalyticsRepository(conn).record_product_event(
        event_name="organization_member_added",
        organization_id=organization_id,
        user_id=user.id,
        metadata={"target_user_id": target_user.id, "email": normalized_email, "role": role},
    )
    conn.commit()
    return OrganizationMemberMutationResult(
        status="added",
        member=_member_body_for_user(conn, member.organization_id, member.user_id),
    )


@router.patch(
    "/{organization_id}/admin/members/{user_id}",
    response_model=OrganizationMemberMutationResult,
)
def update_organization_member(
    organization_id: str,
    user_id: str,
    body: OrganizationMemberUpdate,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
) -> OrganizationMemberMutationResult:
    _require_org_admin(conn, settings, organization_id, user.id, user.email)
    role = body.role
    org_repo = OrganizationRepository(conn)
    member = org_repo.get_member(organization_id=organization_id, user_id=user_id)
    if member is None or member.status != "active":
        raise HTTPException(status_code=404, detail="organization member not found")
    if org_repo.active_member_count(organization_id) == 1 and role != "admin":
        raise HTTPException(status_code=409, detail="single-member organizations must keep an admin")
    if member.role == "admin" and role != "admin":
        if org_repo.active_admin_count(organization_id) <= 1:
            raise HTTPException(status_code=409, detail="organization must keep an admin")
    updated = org_repo.add_member(
        organization_id=organization_id,
        user_id=user_id,
        role=role,
        status="active",
    )
    AnalyticsRepository(conn).record_product_event(
        event_name="organization_member_role_updated",
        organization_id=organization_id,
        user_id=user.id,
        metadata={"target_user_id": user_id, "role": role},
    )
    conn.commit()
    return OrganizationMemberMutationResult(
        status="updated",
        member=_member_body_for_user(conn, updated.organization_id, updated.user_id),
    )


@router.delete(
    "/{organization_id}/admin/members/{user_id}",
    response_model=OrganizationMemberMutationResult,
)
def remove_organization_member(
    organization_id: str,
    user_id: str,
    conn: ConnDep,
    settings: SettingsDep,
    user: GrantedUserDep,
) -> OrganizationMemberMutationResult:
    _require_org_admin(conn, settings, organization_id, user.id, user.email)
    if user_id == user.id:
        raise HTTPException(status_code=409, detail="you cannot remove yourself from this organization")
    org_repo = OrganizationRepository(conn)
    member = org_repo.get_member(organization_id=organization_id, user_id=user_id)
    if member is None or member.status != "active":
        raise HTTPException(status_code=404, detail="organization member not found")
    if org_repo.active_member_count(organization_id) <= 1:
        raise HTTPException(status_code=409, detail="organization must keep at least one admin")
    if member.role == "admin" and org_repo.active_admin_count(organization_id) <= 1:
        raise HTTPException(status_code=409, detail="organization must keep an admin")
    revoked = org_repo.revoke_member(organization_id=organization_id, user_id=user_id)
    AnalyticsRepository(conn).record_product_event(
        event_name="organization_member_removed",
        organization_id=organization_id,
        user_id=user.id,
        metadata={"target_user_id": user_id, "previous_role": member.role},
    )
    conn.commit()
    return OrganizationMemberMutationResult(
        status="removed",
        member=_member_body_for_user(conn, revoked.organization_id, revoked.user_id),
    )


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
            daily_token_limit=entitlements.daily_token_limit,
            monthly_token_limit=entitlements.monthly_token_limit,
            storage_quota_bytes=entitlements.storage_quota_bytes,
            monthly_upload_bytes=entitlements.monthly_upload_bytes,
            max_file_size_bytes=entitlements.max_file_size_bytes,
            max_workspaces=entitlements.max_workspaces,
            max_concurrent_jobs=entitlements.max_concurrent_jobs,
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


def _require_org_admin(
    conn: DbConnection,
    settings: Any,
    organization_id: str,
    user_id: str,
    email: str,
) -> OrganizationRole:
    org_repo = OrganizationRepository(conn)
    org = org_repo.get(organization_id)
    if org is None or org.status != "active":
        raise HTTPException(status_code=404, detail="organization not found")
    if email.strip().lower() in settings.site_owner_email_set():
        return "admin"
    membership = org_repo.get_member(organization_id=organization_id, user_id=user_id)
    if membership is None or membership.status != "active":
        raise HTTPException(status_code=404, detail="organization not found")
    if membership.role != "admin":
        raise HTTPException(status_code=403, detail="organization admin access required")
    return membership.role


def _organization_admin_dashboard(
    conn: DbConnection,
    organization_id: str,
    current_role: OrganizationRole,
) -> OrganizationAdminDashboard:
    month = datetime.now(UTC).strftime("%Y-%m")
    org_repo = OrganizationRepository(conn)
    org = org_repo.get(organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    entitlements = org_repo.ensure_entitlements(organization_id)
    analytics = AnalyticsRepository(conn)
    usage_repo = LLMUsageRepository(conn)
    limit_repo = UsageLimitRepository(conn)
    member_rows = _list_members(conn, organization_id)
    users: list[OrganizationAdminUserRow] = []
    for row in member_rows:
        if row["status"] != "active":
            continue
        user_id = row["user_id"]
        users.append(
            OrganizationAdminUserRow(
                user_id=user_id,
                email=row["email"],
                name=row["name"],
                access_status=row["access_status"],
                membership=_member_body_from_row(row),
                effective_limits=limit_repo.effective_for(
                    organization_id=organization_id,
                    user_id=user_id,
                ),
                user_limits=limit_repo.get_user_limits(
                    organization_id=organization_id,
                    user_id=user_id,
                ),
                session_count=SessionRepository(conn).count_for_user(
                    user_id,
                    organization_id=organization_id,
                ),
                storage_bytes=_scalar_int(
                    conn,
                    """SELECT COALESCE(SUM(size_bytes), 0) AS value
                       FROM source_assets
                       WHERE organization_id = ? AND user_id = ?""",
                    (organization_id, user_id),
                ),
                upload_bytes_month=analytics.monthly_usage_amount(
                    event_type="upload_completed",
                    resource_type="bytes",
                    organization_id=organization_id,
                    user_id=user_id,
                    month_prefix=month,
                ),
                llm_tokens_month=usage_repo.get_monthly_user_tokens(
                    user_id,
                    month,
                    organization_id=organization_id,
                ),
                chat_turns_month=_chat_turn_count(
                    conn,
                    organization_id,
                    user_id=user_id,
                    month_prefix=month,
                ),
                avg_chat_response_ms=_event_avg(
                    conn,
                    "chat_turn_completed",
                    organization_id,
                    month,
                    user_id,
                ),
                last_seen_at=row["last_seen_at"],
            )
        )
    return OrganizationAdminDashboard(
        generated_at=datetime.now(UTC).isoformat(),
        month=month,
        organization=OrganizationBody(
            id=org.id,
            name=org.name,
            slug=org.slug,
            status=org.status,
            role=current_role,
        ),
        entitlements=OrganizationEntitlementsBody(
            organization_id=entitlements.organization_id,
            plan_name=entitlements.plan_name,
            contract_status=entitlements.contract_status,
            seat_limit=entitlements.seat_limit,
            daily_token_limit=entitlements.daily_token_limit,
            monthly_token_limit=entitlements.monthly_token_limit,
            storage_quota_bytes=entitlements.storage_quota_bytes,
            monthly_upload_bytes=entitlements.monthly_upload_bytes,
            max_file_size_bytes=entitlements.max_file_size_bytes,
            max_workspaces=entitlements.max_workspaces,
            max_concurrent_jobs=entitlements.max_concurrent_jobs,
            soft_limit_percent=entitlements.soft_limit_percent,
            hard_limit_percent=entitlements.hard_limit_percent,
            feature_flags=entitlements.feature_flags,
            notes=entitlements.notes,
        ),
        current_role=current_role,
        totals=OrganizationAdminTotals(
            users=len(users),
            sessions=SessionRepository(conn).count_for_organization(organization_id),
            storage_bytes=sum(row.storage_bytes for row in users),
            upload_bytes_month=analytics.monthly_usage_amount(
                event_type="upload_completed",
                resource_type="bytes",
                organization_id=organization_id,
                month_prefix=month,
            ),
            upload_count_month=analytics.monthly_usage_amount(
                event_type="upload_completed",
                resource_type="count",
                organization_id=organization_id,
                month_prefix=month,
            ),
            llm_tokens_month=usage_repo.get_monthly_organization_tokens(organization_id, month),
            chat_turns_month=_chat_turn_count(conn, organization_id, month_prefix=month),
            active_jobs=ProcessingJobRepository(conn).active_count(organization_id=organization_id),
            failed_jobs_month=_scalar_int(
                conn,
                """SELECT COUNT(*) AS value
                   FROM processing_jobs
                   WHERE organization_id = ?
                     AND status = 'failed'
                     AND updated_at LIKE ?""",
                (organization_id, f"{month}-%"),
            ),
            avg_chat_response_ms=_event_avg(conn, "chat_turn_completed", organization_id, month),
            avg_processing_ms=_event_avg(conn, "processing_job_completed", organization_id, month),
        ),
        users=users,
        recent_events=_recent_events(conn, organization_id),
    )


def _member_body_for_user(
    conn: DbConnection,
    organization_id: str,
    user_id: str,
) -> OrganizationMemberBody:
    row = conn.execute(
        """SELECT u.id AS user_id, u.email, u.name, m.role, m.status, m.created_at
           FROM organization_members m
           JOIN users u ON u.id = m.user_id
           WHERE m.organization_id = ? AND m.user_id = ?""",
        (organization_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="organization member not found")
    return _member_body_from_row(row)


def _member_body_from_row(row: Any) -> OrganizationMemberBody:
    return OrganizationMemberBody(
        user_id=row["user_id"],
        email=row["email"],
        name=row["name"],
        role=row["role"],
        status=row["status"],
        joined_at=datetime.fromisoformat(row["created_at"]),
    )


def _list_members(conn: DbConnection, organization_id: str) -> list[Any]:
    return conn.execute(
        """SELECT u.id AS user_id, u.email, u.name, u.access_status, u.last_seen_at,
                  m.role, m.status, m.created_at
           FROM organization_members m
           JOIN users u ON u.id = m.user_id
           WHERE m.organization_id = ?
           ORDER BY u.email""",
        (organization_id,),
    ).fetchall()


def _scalar_int(conn: DbConnection, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row["value"] or 0) if row else 0


def _event_avg(
    conn: DbConnection,
    event_name: str,
    organization_id: str,
    month: str,
    user_id: str | None = None,
) -> float | None:
    clauses = ["event_name = ?", "organization_id = ?", "occurred_at LIKE ?", "metric_value IS NOT NULL"]
    params: list[Any] = [event_name, organization_id, f"{month}-%"]
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    row = conn.execute(
        f"""SELECT AVG(metric_value) AS value
            FROM product_events
            WHERE {' AND '.join(clauses)}""",
        params,
    ).fetchone()
    return float(row["value"]) if row and row["value"] is not None else None


def _chat_turn_count(
    conn: DbConnection,
    organization_id: str,
    *,
    user_id: str | None = None,
    month_prefix: str,
) -> int:
    clauses = ["s.organization_id = ?", "ct.created_at LIKE ?"]
    params: list[Any] = [organization_id, f"{month_prefix}-%"]
    if user_id is not None:
        clauses.append("COALESCE(s.created_by_user_id, s.user_id) = ?")
        params.append(user_id)
    row = conn.execute(
        f"""SELECT COUNT(*) AS value
            FROM chat_turns ct
            JOIN sessions s ON s.id = ct.session_id
            WHERE {' AND '.join(clauses)}""",
        params,
    ).fetchone()
    return int(row["value"] or 0) if row else 0


def _recent_events(conn: DbConnection, organization_id: str) -> list[OrganizationAdminEvent]:
    rows = conn.execute(
        """SELECT user_id, session_id, event_name, metric_value, metadata, occurred_at
           FROM product_events
           WHERE organization_id = ?
           ORDER BY occurred_at DESC
           LIMIT 50""",
        (organization_id,),
    ).fetchall()
    events: list[OrganizationAdminEvent] = []
    for row in rows:
        metadata = loads_json(row["metadata"], {})
        events.append(
            OrganizationAdminEvent(
                occurred_at=row["occurred_at"],
                user_id=row["user_id"],
                session_id=row["session_id"],
                event_name=row["event_name"],
                metric_value=row["metric_value"],
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        )
    return events
