from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from cerno.api.deps import ConnDep, SiteOwnerDep
from cerno.db import DbConnection, loads_json
from cerno.models import (
    EffectiveUsageLimits,
    Organization,
    OrganizationEntitlements,
    OrganizationMember,
    UserOrganizationLimits,
)
from cerno.repositories import (
    AnalyticsRepository,
    LLMUsageRepository,
    OrganizationRepository,
    ProcessingJobRepository,
    SessionRepository,
    UsageLimitRepository,
)

router = APIRouter(prefix="/owner", tags=["owner"])


class OwnerTotals(BaseModel):
    organizations: int = 0
    users: int = 0
    sessions: int = 0
    storage_bytes: int = 0
    upload_bytes_month: int = 0
    llm_tokens_month: int = 0
    chat_turns_month: int = 0
    upload_count_month: int = 0
    active_jobs: int = 0
    failed_jobs_month: int = 0
    errors_month: int = 0
    avg_chat_response_ms: float | None = None
    avg_processing_ms: float | None = None


class OwnerOrganizationRow(BaseModel):
    organization: Organization
    entitlements: OrganizationEntitlements
    user_count: int
    session_count: int
    storage_bytes: int
    upload_bytes_month: int
    upload_count_month: int
    llm_tokens_month: int
    active_jobs: int
    failed_jobs_month: int
    chat_turns_month: int
    avg_chat_response_ms: float | None = None
    avg_processing_ms: float | None = None
    last_activity_at: str | None = None


class OwnerUserRow(BaseModel):
    organization_id: str
    membership: OrganizationMember
    user_id: str
    email: str
    name: str | None = None
    access_status: str
    last_seen_at: str
    effective_limits: EffectiveUsageLimits
    user_limits: UserOrganizationLimits | None = None
    session_count: int = 0
    storage_bytes: int = 0
    upload_bytes_month: int = 0
    llm_tokens_month: int = 0
    chat_turns_month: int = 0
    avg_chat_response_ms: float | None = None


class OwnerRecentEvent(BaseModel):
    occurred_at: str
    organization_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    event_name: str
    metric_value: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OwnerDashboard(BaseModel):
    generated_at: str
    month: str
    totals: OwnerTotals
    organizations: list[OwnerOrganizationRow]
    users: list[OwnerUserRow]
    recent_events: list[OwnerRecentEvent]


class OrganizationLimitsUpdate(BaseModel):
    plan_name: str | None = None
    contract_status: str | None = None
    seat_limit: int | None = None
    daily_token_limit: int | None = None
    monthly_token_limit: int | None = None
    storage_quota_bytes: int | None = None
    monthly_upload_bytes: int | None = None
    max_file_size_bytes: int | None = None
    max_workspaces: int | None = None
    max_concurrent_jobs: int | None = None
    soft_limit_percent: int | None = None
    hard_limit_percent: int | None = None
    feature_flags: dict[str, Any] | None = None
    notes: str | None = None


class UserLimitsUpdate(BaseModel):
    daily_token_limit: int | None = None
    monthly_token_limit: int | None = None
    storage_quota_bytes: int | None = None
    monthly_upload_bytes: int | None = None
    max_file_size_bytes: int | None = None
    max_sessions: int | None = None
    max_concurrent_jobs: int | None = None
    notes: str | None = None


@router.get("/dashboard", response_model=OwnerDashboard)
def owner_dashboard(conn: ConnDep, owner: SiteOwnerDep) -> OwnerDashboard:
    del owner
    month = datetime.now(UTC).strftime("%Y-%m")
    generated_at = datetime.now(UTC).isoformat()
    org_repo = OrganizationRepository(conn)
    limit_repo = UsageLimitRepository(conn)
    usage_repo = LLMUsageRepository(conn)
    analytics = AnalyticsRepository(conn)
    organizations = _list_organizations(conn)
    org_rows: list[OwnerOrganizationRow] = []
    user_rows: list[OwnerUserRow] = []
    for org in organizations:
        entitlements = org_repo.ensure_entitlements(org.id)
        org_rows.append(
            OwnerOrganizationRow(
                organization=org,
                entitlements=entitlements,
                user_count=_scalar_int(
                    conn,
                    "SELECT COUNT(*) AS value FROM organization_members WHERE organization_id = ? AND status = 'active'",
                    (org.id,),
                ),
                session_count=SessionRepository(conn).count_for_organization(org.id),
                storage_bytes=_scalar_int(
                    conn,
                    "SELECT COALESCE(SUM(size_bytes), 0) AS value FROM source_assets WHERE organization_id = ?",
                    (org.id,),
                ),
                upload_bytes_month=analytics.monthly_usage_amount(
                    event_type="upload_completed",
                    resource_type="bytes",
                    organization_id=org.id,
                    month_prefix=month,
                ),
                upload_count_month=analytics.monthly_usage_amount(
                    event_type="upload_completed",
                    resource_type="count",
                    organization_id=org.id,
                    month_prefix=month,
                ),
                llm_tokens_month=usage_repo.get_monthly_organization_tokens(org.id, month),
                active_jobs=ProcessingJobRepository(conn).active_count(organization_id=org.id),
                failed_jobs_month=_scalar_int(
                    conn,
                    """SELECT COUNT(*) AS value
                       FROM processing_jobs
                       WHERE organization_id = ?
                         AND status = 'failed'
                         AND updated_at LIKE ?""",
                    (org.id, f"{month}-%"),
                ),
                chat_turns_month=_chat_turn_count(conn, org.id, month_prefix=month),
                avg_chat_response_ms=_event_avg(conn, "chat_turn_completed", org.id, month),
                avg_processing_ms=_event_avg(conn, "processing_job_completed", org.id, month),
                last_activity_at=_last_activity(conn, org.id),
            )
        )
        for row in _list_members(conn, org.id):
            user_id = row["user_id"]
            user_rows.append(
                OwnerUserRow(
                    organization_id=org.id,
                    membership=_row_to_member(row),
                    user_id=user_id,
                    email=row["email"],
                    name=row["name"],
                    access_status=row["access_status"],
                    last_seen_at=row["last_seen_at"],
                    effective_limits=limit_repo.effective_for(
                        organization_id=org.id,
                        user_id=user_id,
                    ),
                    user_limits=limit_repo.get_user_limits(
                        organization_id=org.id,
                        user_id=user_id,
                    ),
                    session_count=SessionRepository(conn).count_for_user(
                        user_id,
                        organization_id=org.id,
                    ),
                    storage_bytes=_scalar_int(
                        conn,
                        """SELECT COALESCE(SUM(size_bytes), 0) AS value
                           FROM source_assets
                           WHERE organization_id = ? AND user_id = ?""",
                        (org.id, user_id),
                    ),
                    upload_bytes_month=analytics.monthly_usage_amount(
                        event_type="upload_completed",
                        resource_type="bytes",
                        organization_id=org.id,
                        user_id=user_id,
                        month_prefix=month,
                    ),
                    llm_tokens_month=usage_repo.get_monthly_user_tokens(
                        user_id,
                        month,
                        organization_id=org.id,
                    ),
                    chat_turns_month=_chat_turn_count(
                        conn,
                        org.id,
                        user_id=user_id,
                        month_prefix=month,
                    ),
                    avg_chat_response_ms=_event_avg(conn, "chat_turn_completed", org.id, month, user_id),
                )
            )
    return OwnerDashboard(
        generated_at=generated_at,
        month=month,
        totals=_owner_totals(conn, org_rows, month),
        organizations=org_rows,
        users=user_rows,
        recent_events=_recent_events(conn),
    )


@router.patch("/organizations/{organization_id}/limits", response_model=OrganizationEntitlements)
def update_organization_limits(
    organization_id: str,
    body: OrganizationLimitsUpdate,
    conn: ConnDep,
    owner: SiteOwnerDep,
) -> OrganizationEntitlements:
    del owner
    org_repo = OrganizationRepository(conn)
    if org_repo.get(organization_id) is None:
        raise HTTPException(status_code=404, detail="organization not found")
    current = org_repo.ensure_entitlements(organization_id)
    fields = body.model_fields_set
    entitlements = org_repo.update_entitlements(
        organization_id,
        plan_name=body.plan_name if "plan_name" in fields else current.plan_name,
        contract_status=body.contract_status
        if "contract_status" in fields
        else current.contract_status,
        seat_limit=body.seat_limit if "seat_limit" in fields else current.seat_limit,
        daily_token_limit=body.daily_token_limit
        if "daily_token_limit" in fields
        else current.daily_token_limit,
        monthly_token_limit=body.monthly_token_limit
        if "monthly_token_limit" in fields
        else current.monthly_token_limit,
        storage_quota_bytes=body.storage_quota_bytes
        if "storage_quota_bytes" in fields
        else current.storage_quota_bytes,
        monthly_upload_bytes=body.monthly_upload_bytes
        if "monthly_upload_bytes" in fields
        else current.monthly_upload_bytes,
        max_file_size_bytes=body.max_file_size_bytes
        if "max_file_size_bytes" in fields
        else current.max_file_size_bytes,
        max_workspaces=body.max_workspaces if "max_workspaces" in fields else current.max_workspaces,
        max_concurrent_jobs=body.max_concurrent_jobs
        if "max_concurrent_jobs" in fields
        else current.max_concurrent_jobs,
        soft_limit_percent=body.soft_limit_percent
        if "soft_limit_percent" in fields
        else current.soft_limit_percent,
        hard_limit_percent=body.hard_limit_percent
        if "hard_limit_percent" in fields
        else current.hard_limit_percent,
        feature_flags=body.feature_flags if "feature_flags" in fields else current.feature_flags,
        notes=body.notes if "notes" in fields else current.notes,
    )
    conn.commit()
    return entitlements


@router.put(
    "/organizations/{organization_id}/users/{user_id}/limits",
    response_model=UserOrganizationLimits,
)
def update_user_limits(
    organization_id: str,
    user_id: str,
    body: UserLimitsUpdate,
    conn: ConnDep,
    owner: SiteOwnerDep,
) -> UserOrganizationLimits:
    del owner
    org_repo = OrganizationRepository(conn)
    if org_repo.get(organization_id) is None:
        raise HTTPException(status_code=404, detail="organization not found")
    if org_repo.get_member(organization_id=organization_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="organization member not found")
    limits = UsageLimitRepository(conn).set_user_limits(
        organization_id=organization_id,
        user_id=user_id,
        daily_token_limit=body.daily_token_limit,
        monthly_token_limit=body.monthly_token_limit,
        storage_quota_bytes=body.storage_quota_bytes,
        monthly_upload_bytes=body.monthly_upload_bytes,
        max_file_size_bytes=body.max_file_size_bytes,
        max_sessions=body.max_sessions,
        max_concurrent_jobs=body.max_concurrent_jobs,
        notes=body.notes,
    )
    conn.commit()
    return limits


def _owner_totals(
    conn: DbConnection, organization_rows: list[OwnerOrganizationRow], month: str
) -> OwnerTotals:
    errors_since = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    return OwnerTotals(
        organizations=len(organization_rows),
        users=_scalar_int(conn, "SELECT COUNT(*) AS value FROM users", ()),
        sessions=_scalar_int(conn, "SELECT COUNT(*) AS value FROM sessions", ()),
        storage_bytes=sum(row.storage_bytes for row in organization_rows),
        upload_bytes_month=sum(row.upload_bytes_month for row in organization_rows),
        upload_count_month=sum(row.upload_count_month for row in organization_rows),
        llm_tokens_month=sum(row.llm_tokens_month for row in organization_rows),
        chat_turns_month=sum(row.chat_turns_month for row in organization_rows),
        active_jobs=sum(row.active_jobs for row in organization_rows),
        failed_jobs_month=sum(row.failed_jobs_month for row in organization_rows),
        errors_month=_scalar_int(
            conn,
            """SELECT COUNT(*) AS value
               FROM product_events
               WHERE event_name LIKE ?
                 AND occurred_at >= ?""",
            ("%failed%", errors_since),
        ),
        avg_chat_response_ms=_global_event_avg(conn, "chat_turn_completed", month),
        avg_processing_ms=_global_event_avg(conn, "processing_job_completed", month),
    )


def _list_organizations(conn: DbConnection) -> list[Organization]:
    rows = conn.execute("SELECT * FROM organizations ORDER BY created_at DESC").fetchall()
    return [
        Organization(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
        for row in rows
    ]


def _list_members(conn: DbConnection, organization_id: str) -> list[Any]:
    return conn.execute(
        """SELECT m.*, u.email, u.name, u.access_status, u.last_seen_at
           FROM organization_members m
           JOIN users u ON u.id = m.user_id
           WHERE m.organization_id = ?
           ORDER BY u.email""",
        (organization_id,),
    ).fetchall()


def _row_to_member(row: Any) -> OrganizationMember:
    return OrganizationMember(
        organization_id=row["organization_id"],
        user_id=row["user_id"],
        role=row["role"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


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


def _global_event_avg(conn: DbConnection, event_name: str, month: str) -> float | None:
    row = conn.execute(
        """SELECT AVG(metric_value) AS value
           FROM product_events
           WHERE event_name = ?
             AND occurred_at LIKE ?
             AND metric_value IS NOT NULL""",
        (event_name, f"{month}-%"),
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


def _last_activity(conn: DbConnection, organization_id: str) -> str | None:
    row = conn.execute(
        """SELECT MAX(ts) AS value
           FROM (
             SELECT MAX(created_at) AS ts FROM sessions WHERE organization_id = ?
             UNION ALL
             SELECT MAX(occurred_at) AS ts FROM product_events WHERE organization_id = ?
             UNION ALL
             SELECT MAX(created_at) AS ts FROM llm_call_events WHERE organization_id = ?
             UNION ALL
             SELECT MAX(created_at) AS ts FROM source_assets WHERE organization_id = ?
           ) activity""",
        (organization_id, organization_id, organization_id, organization_id),
    ).fetchone()
    return row["value"] if row and row["value"] else None


def _recent_events(conn: DbConnection) -> list[OwnerRecentEvent]:
    rows = conn.execute(
        """SELECT organization_id, user_id, session_id, event_name,
                  metric_value, metadata, occurred_at
           FROM product_events
           ORDER BY occurred_at DESC
           LIMIT 50"""
    ).fetchall()
    events: list[OwnerRecentEvent] = []
    for row in rows:
        metadata = loads_json(row["metadata"], {})
        events.append(
            OwnerRecentEvent(
                occurred_at=row["occurred_at"],
                organization_id=row["organization_id"],
                user_id=row["user_id"],
                session_id=row["session_id"],
                event_name=row["event_name"],
                metric_value=row["metric_value"],
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        )
    return events


__all__ = ["router"]
