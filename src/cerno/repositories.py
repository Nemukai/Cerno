from __future__ import annotations

import logging
import math
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from cerno.db import DbConnection, DbRow, dumps_json, loads_json
from cerno.models import (
    AccessStatus,
    Anomaly,
    ApprovedEmail,
    AssetArtifact,
    AuditEvent,
    BetaCode,
    ChatArtifact,
    ChatMessage,
    ChatTurn,
    Dashboard,
    DashboardCell,
    DashboardPage,
    DataDoc,
    DiscoveryStatus,
    Document,
    DocumentChunk,
    DocumentChunkSearchRow,
    DocumentPage,
    DocumentPageSource,
    DocumentStatus,
    EffectiveUsageLimits,
    File,
    FileSchema,
    Link,
    LinkReview,
    MessageRole,
    NumberSystem,
    Organization,
    OrganizationEntitlements,
    OrganizationInvite,
    OrganizationMember,
    OrganizationRole,
    ProcessingEvent,
    ProcessingEventKind,
    ProcessingJob,
    ProcessingJobKind,
    ReviewStatus,
    RunStatus,
    SchemaColumn,
    Session,
    SessionStatus,
    SourceAsset,
    TurnState,
    UploadIntent,
    User,
    UserOrganizationLimits,
    WorkspaceAsset,
    WorkspaceTable,
)

logger = logging.getLogger(__name__)


def new_id() -> str:
    return str(uuid.uuid4())


def vector_literal(values: Sequence[float]) -> str:
    vector: list[str] = []
    for value in values:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("embedding vector contained a non-finite value")
        vector.append(format(parsed, ".9g"))
    return f"[{','.join(vector)}]"


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _organization_slug_from_email(email: str, user_id: str) -> str:
    local = email.split("@", 1)[0].strip().lower() or "user"
    safe = "".join(ch if ch.isalnum() else "-" for ch in local).strip("-")
    suffix = user_id.replace("-", "")[:8]
    return f"{safe or 'user'}-{suffix}"


class OrganizationRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        name: str,
        slug: str,
        organization_id: str | None = None,
        status: str = "active",
    ) -> Organization:
        oid = organization_id or new_id()
        now = _now()
        self.conn.execute(
            """INSERT INTO organizations (id, name, slug, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (oid, name, slug, status, now.isoformat(), now.isoformat()),
        )
        row = self.conn.execute("SELECT * FROM organizations WHERE id = ?", (oid,)).fetchone()
        if row is None:
            raise LookupError(f"organization not found after create: {oid}")
        return _row_to_organization(row)

    def get(self, organization_id: str) -> Organization | None:
        row = self.conn.execute(
            "SELECT * FROM organizations WHERE id = ?",
            (organization_id,),
        ).fetchone()
        return _row_to_organization(row) if row else None

    def list_for_user(self, user_id: str) -> list[Organization]:
        rows = self.conn.execute(
            """SELECT o.*
               FROM organizations o
               JOIN organization_members m ON m.organization_id = o.id
               WHERE m.user_id = ?
                 AND m.status = 'active'
                 AND o.status = 'active'
               ORDER BY o.created_at""",
            (user_id,),
        ).fetchall()
        return [_row_to_organization(row) for row in rows]

    def primary_for_user(self, user_id: str) -> Organization | None:
        orgs = self.list_for_user(user_id)
        return orgs[0] if orgs else None

    def add_member(
        self,
        *,
        organization_id: str,
        user_id: str,
        role: OrganizationRole = "member",
        status: str = "active",
    ) -> OrganizationMember:
        now = _now()
        self.conn.execute(
            """INSERT INTO organization_members
               (organization_id, user_id, role, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(organization_id, user_id) DO UPDATE
               SET role = excluded.role,
                   status = excluded.status,
                   updated_at = excluded.updated_at""",
            (organization_id, user_id, role, status, now.isoformat(), now.isoformat()),
        )
        row = self.conn.execute(
            """SELECT * FROM organization_members
               WHERE organization_id = ? AND user_id = ?""",
            (organization_id, user_id),
        ).fetchone()
        if row is None:
            raise LookupError(f"organization member not found after upsert: {organization_id}:{user_id}")
        return _row_to_organization_member(row)

    def get_member(
        self,
        *,
        organization_id: str,
        user_id: str,
    ) -> OrganizationMember | None:
        row = self.conn.execute(
            """SELECT * FROM organization_members
               WHERE organization_id = ? AND user_id = ?""",
            (organization_id, user_id),
        ).fetchone()
        return _row_to_organization_member(row) if row else None

    def revoke_member(
        self,
        *,
        organization_id: str,
        user_id: str,
    ) -> OrganizationMember:
        now = _now()
        self.conn.execute(
            """UPDATE organization_members
               SET status = 'revoked', updated_at = ?
               WHERE organization_id = ? AND user_id = ?""",
            (now.isoformat(), organization_id, user_id),
        )
        member = self.get_member(organization_id=organization_id, user_id=user_id)
        if member is None:
            raise LookupError(f"organization member not found: {organization_id}:{user_id}")
        return member

    def active_member_count(self, organization_id: str) -> int:
        row = self.conn.execute(
            """SELECT COUNT(*) AS value
               FROM organization_members
               WHERE organization_id = ? AND status = 'active'""",
            (organization_id,),
        ).fetchone()
        return int(row["value"] or 0) if row else 0

    def active_admin_count(self, organization_id: str) -> int:
        row = self.conn.execute(
            """SELECT COUNT(*) AS value
               FROM organization_members
               WHERE organization_id = ?
                 AND status = 'active'
                 AND role = 'admin'""",
            (organization_id,),
        ).fetchone()
        return int(row["value"] or 0) if row else 0

    def ensure_personal_for_user(self, user: User) -> Organization:
        existing = self.primary_for_user(user.id)
        if existing is not None:
            return existing
        name = f"{user.name or user.email}'s Organization"
        slug = _organization_slug_from_email(user.email, user.id)
        org = self.create(name=name, slug=slug)
        self.add_member(organization_id=org.id, user_id=user.id, role="admin")
        self.ensure_entitlements(org.id)
        return org

    def ensure_entitlements(self, organization_id: str) -> OrganizationEntitlements:
        existing = self.get_entitlements(organization_id)
        if existing is not None:
            return existing
        now = _now()
        self.conn.execute(
            """INSERT INTO organization_entitlements
               (organization_id, plan_name, contract_status, seat_limit,
                monthly_token_limit, storage_quota_bytes, soft_limit_percent,
                hard_limit_percent, feature_flags, created_at, updated_at)
               VALUES (?, 'manual', 'trial', 1, 6000000, 5368709120, 100, 120, '{}', ?, ?)""",
            (organization_id, now.isoformat(), now.isoformat()),
        )
        entitlements = self.get_entitlements(organization_id)
        if entitlements is None:
            raise LookupError(f"organization entitlements not found after create: {organization_id}")
        return entitlements

    def get_entitlements(self, organization_id: str) -> OrganizationEntitlements | None:
        row = self.conn.execute(
            "SELECT * FROM organization_entitlements WHERE organization_id = ?",
            (organization_id,),
        ).fetchone()
        return _row_to_organization_entitlements(row) if row else None

    def update_entitlements(
        self,
        organization_id: str,
        *,
        plan_name: str | None = None,
        contract_status: str | None = None,
        seat_limit: int | None = None,
        daily_token_limit: int | None = None,
        monthly_token_limit: int | None = None,
        storage_quota_bytes: int | None = None,
        monthly_upload_bytes: int | None = None,
        max_file_size_bytes: int | None = None,
        max_workspaces: int | None = None,
        max_concurrent_jobs: int | None = None,
        soft_limit_percent: int | None = None,
        hard_limit_percent: int | None = None,
        feature_flags: dict[str, Any] | None = None,
        notes: str | None = None,
    ) -> OrganizationEntitlements:
        self.ensure_entitlements(organization_id)
        current = self.get_entitlements(organization_id)
        if current is None:
            raise LookupError(f"organization entitlements not found: {organization_id}")
        now = _now().isoformat()
        next_flags = feature_flags if feature_flags is not None else current.feature_flags
        self.conn.execute(
            """UPDATE organization_entitlements
               SET plan_name = ?,
                   contract_status = ?,
                   seat_limit = ?,
                   daily_token_limit = ?,
                   monthly_token_limit = ?,
                   storage_quota_bytes = ?,
                   monthly_upload_bytes = ?,
                   max_file_size_bytes = ?,
                   max_workspaces = ?,
                   max_concurrent_jobs = ?,
                   soft_limit_percent = ?,
                   hard_limit_percent = ?,
                   feature_flags = ?,
                   notes = ?,
                   updated_at = ?
               WHERE organization_id = ?""",
            (
                plan_name if plan_name is not None else current.plan_name,
                contract_status if contract_status is not None else current.contract_status,
                seat_limit if seat_limit is not None else current.seat_limit,
                daily_token_limit if daily_token_limit is not None else current.daily_token_limit,
                monthly_token_limit if monthly_token_limit is not None else current.monthly_token_limit,
                storage_quota_bytes if storage_quota_bytes is not None else current.storage_quota_bytes,
                monthly_upload_bytes if monthly_upload_bytes is not None else current.monthly_upload_bytes,
                max_file_size_bytes if max_file_size_bytes is not None else current.max_file_size_bytes,
                max_workspaces if max_workspaces is not None else current.max_workspaces,
                max_concurrent_jobs if max_concurrent_jobs is not None else current.max_concurrent_jobs,
                soft_limit_percent if soft_limit_percent is not None else current.soft_limit_percent,
                hard_limit_percent if hard_limit_percent is not None else current.hard_limit_percent,
                dumps_json(next_flags),
                notes if notes is not None else current.notes,
                now,
                organization_id,
            ),
        )
        refreshed = self.get_entitlements(organization_id)
        if refreshed is None:
            raise LookupError(f"organization entitlements not found after update: {organization_id}")
        return refreshed

    def create_invite(
        self,
        *,
        organization_id: str,
        email: str,
        role: OrganizationRole = "member",
        invited_by_user_id: str | None = None,
        token: str | None = None,
        expires_at: datetime | None = None,
    ) -> OrganizationInvite:
        invite_id = new_id()
        now = _now()
        normalized_email = ApprovedEmailRepository.normalize(email)
        self.conn.execute(
            """INSERT INTO organization_invites
               (id, organization_id, email, role, invited_by_user_id, status,
                token, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (
                invite_id,
                organization_id,
                normalized_email,
                role,
                invited_by_user_id,
                token,
                expires_at.isoformat() if expires_at else None,
                now.isoformat(),
            ),
        )
        row = self.conn.execute(
            "SELECT * FROM organization_invites WHERE id = ?",
            (invite_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"organization invite not found after create: {invite_id}")
        return _row_to_organization_invite(row)


class UsageLimitRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def get_user_limits(
        self, *, organization_id: str, user_id: str
    ) -> UserOrganizationLimits | None:
        row = self.conn.execute(
            """SELECT * FROM user_organization_limits
               WHERE organization_id = ? AND user_id = ?""",
            (organization_id, user_id),
        ).fetchone()
        return _row_to_user_organization_limits(row) if row else None

    def set_user_limits(
        self,
        *,
        organization_id: str,
        user_id: str,
        daily_token_limit: int | None = None,
        monthly_token_limit: int | None = None,
        storage_quota_bytes: int | None = None,
        monthly_upload_bytes: int | None = None,
        max_file_size_bytes: int | None = None,
        max_sessions: int | None = None,
        max_concurrent_jobs: int | None = None,
        notes: str | None = None,
    ) -> UserOrganizationLimits:
        now = _now()
        self.conn.execute(
            """INSERT INTO user_organization_limits
               (organization_id, user_id, daily_token_limit, monthly_token_limit,
                storage_quota_bytes, monthly_upload_bytes, max_file_size_bytes,
                max_sessions, max_concurrent_jobs, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(organization_id, user_id) DO UPDATE
               SET daily_token_limit = excluded.daily_token_limit,
                   monthly_token_limit = excluded.monthly_token_limit,
                   storage_quota_bytes = excluded.storage_quota_bytes,
                   monthly_upload_bytes = excluded.monthly_upload_bytes,
                   max_file_size_bytes = excluded.max_file_size_bytes,
                   max_sessions = excluded.max_sessions,
                   max_concurrent_jobs = excluded.max_concurrent_jobs,
                   notes = excluded.notes,
                   updated_at = excluded.updated_at""",
            (
                organization_id,
                user_id,
                daily_token_limit,
                monthly_token_limit,
                storage_quota_bytes,
                monthly_upload_bytes,
                max_file_size_bytes,
                max_sessions,
                max_concurrent_jobs,
                notes,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        limits = self.get_user_limits(organization_id=organization_id, user_id=user_id)
        if limits is None:
            raise LookupError(f"user organization limits not found after upsert: {organization_id}:{user_id}")
        return limits

    def effective_for(self, *, organization_id: str, user_id: str) -> EffectiveUsageLimits:
        entitlements = OrganizationRepository(self.conn).ensure_entitlements(organization_id)
        user_limits = self.get_user_limits(organization_id=organization_id, user_id=user_id)
        return EffectiveUsageLimits(
            organization_id=organization_id,
            user_id=user_id,
            daily_token_limit=(
                user_limits.daily_token_limit
                if user_limits and user_limits.daily_token_limit is not None
                else entitlements.daily_token_limit
            ),
            user_monthly_token_limit=user_limits.monthly_token_limit if user_limits else None,
            organization_monthly_token_limit=entitlements.monthly_token_limit,
            user_storage_quota_bytes=user_limits.storage_quota_bytes if user_limits else None,
            organization_storage_quota_bytes=entitlements.storage_quota_bytes,
            user_monthly_upload_bytes=user_limits.monthly_upload_bytes if user_limits else None,
            organization_monthly_upload_bytes=entitlements.monthly_upload_bytes,
            user_max_file_size_bytes=user_limits.max_file_size_bytes if user_limits else None,
            organization_max_file_size_bytes=entitlements.max_file_size_bytes,
            user_max_sessions=user_limits.max_sessions if user_limits else None,
            organization_max_sessions=entitlements.max_workspaces,
            user_max_concurrent_jobs=user_limits.max_concurrent_jobs if user_limits else None,
            organization_max_concurrent_jobs=entitlements.max_concurrent_jobs,
            soft_limit_percent=entitlements.soft_limit_percent,
            hard_limit_percent=entitlements.hard_limit_percent,
        )

    @staticmethod
    def hard_cap(limit: int | None, limits: EffectiveUsageLimits) -> int | None:
        if limit is None:
            return None
        return max(0, limit * limits.hard_limit_percent // 100)


class AnalyticsRepository:
    def __init__(self, conn: DbConnection, *, auto_commit: bool = False) -> None:
        self.conn = conn
        self.auto_commit = auto_commit

    def record_product_event(
        self,
        *,
        event_name: str,
        organization_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metric_value: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO product_events
               (id, organization_id, user_id, session_id, event_name,
                metric_value, metadata, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id(),
                organization_id,
                user_id,
                session_id,
                event_name,
                metric_value,
                dumps_json(metadata or {}),
                _now().isoformat(),
            ),
        )
        if self.auto_commit:
            self.conn.commit()

    def last_product_event_at(self, *, event_name: str, user_id: str) -> datetime | None:
        row = self.conn.execute(
            """SELECT MAX(occurred_at) AS last_at FROM product_events
               WHERE event_name = ? AND user_id = ?""",
            (event_name, user_id),
        ).fetchone()
        return _parse_dt(row["last_at"]) if row else None

    def record_usage_event(
        self,
        *,
        event_type: str,
        resource_type: str,
        amount: int,
        organization_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        occurred_at = _now()
        self.conn.execute(
            """INSERT INTO usage_events
               (id, organization_id, user_id, session_id, event_type,
                resource_type, amount, model, metadata, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id(),
                organization_id,
                user_id,
                session_id,
                event_type,
                resource_type,
                amount,
                model,
                dumps_json(metadata or {}),
                occurred_at.isoformat(),
            ),
        )
        if organization_id is not None:
            metric = f"{resource_type}:{event_type}"
            self.conn.execute(
                """INSERT INTO organization_usage_daily
                   (organization_id, day, metric, amount)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(organization_id, day, metric) DO UPDATE
                   SET amount = organization_usage_daily.amount + excluded.amount""",
                (organization_id, occurred_at.date().isoformat(), metric, amount),
            )
        if self.auto_commit:
            self.conn.commit()

    def monthly_usage_amount(
        self,
        *,
        event_type: str,
        resource_type: str,
        organization_id: str | None = None,
        user_id: str | None = None,
        month_prefix: str | None = None,
    ) -> int:
        prefix = month_prefix or _now().strftime("%Y-%m")
        clauses = [
            "event_type = ?",
            "resource_type = ?",
            "occurred_at LIKE ?",
        ]
        params: list[Any] = [event_type, resource_type, f"{prefix}-%"]
        if organization_id is not None:
            clauses.append("organization_id = ?")
            params.append(organization_id)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        row = self.conn.execute(
            f"""SELECT COALESCE(SUM(amount), 0) AS total
                FROM usage_events
                WHERE {' AND '.join(clauses)}""",
            params,
        ).fetchone()
        return int(row["total"] or 0) if row else 0

    def record_llm_call(
        self,
        *,
        provider: str,
        status: str,
        organization_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        job_id: str | None = None,
        model: str | None = None,
        response_id: str | None = None,
        request_id: str | None = None,
        error_message: str | None = None,
        duration_ms: int | None = None,
        total_tokens: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO llm_call_events
               (id, organization_id, user_id, session_id, turn_id, job_id,
                provider, model, response_id, request_id, status, error_message,
                duration_ms, total_tokens, input_tokens, output_tokens, cached_tokens,
                reasoning_tokens, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id(),
                organization_id,
                user_id,
                session_id,
                turn_id,
                job_id,
                provider,
                model,
                response_id,
                request_id,
                status,
                error_message[:1000] if error_message else None,
                duration_ms,
                total_tokens,
                input_tokens,
                output_tokens,
                cached_tokens,
                reasoning_tokens,
                dumps_json(metadata or {}),
                _now().isoformat(),
            ),
        )
        if self.auto_commit:
            self.conn.commit()


class SessionRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create(
        self,
        name: str,
        user_id: str,
        organization_id: str,
        session_id: str | None = None,
    ) -> Session:
        sid = session_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO sessions
               (id, organization_id, user_id, created_by_user_id, name, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sid, organization_id, user_id, user_id, name, "new", created_at.isoformat()),
        )
        return Session(
            id=sid,
            organization_id=organization_id,
            user_id=user_id,
            created_by_user_id=user_id,
            name=name,
            status="new",
            created_at=created_at,
        )

    def get(
        self,
        session_id: str,
        user_id: str | None = None,
        organization_id: str | None = None,
    ) -> Session | None:
        if user_id is None:
            if organization_id is None:
                row = self.conn.execute(
                    "SELECT * FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT * FROM sessions WHERE id = ? AND organization_id = ?",
                    (session_id, organization_id),
                ).fetchone()
        else:
            if organization_id is None:
                row = self.conn.execute(
                    """SELECT * FROM sessions
                       WHERE id = ?
                         AND COALESCE(created_by_user_id, user_id) = ?""",
                    (session_id, user_id),
                ).fetchone()
            else:
                row = self.conn.execute(
                    """SELECT * FROM sessions
                       WHERE id = ?
                         AND organization_id = ?
                         AND COALESCE(created_by_user_id, user_id) = ?""",
                    (session_id, organization_id, user_id),
                ).fetchone()
        return _row_to_session(row) if row else None

    def list(self, user_id: str, organization_id: str | None = None) -> list[Session]:
        if organization_id is None:
            rows = self.conn.execute(
                """SELECT * FROM sessions
                   WHERE COALESCE(created_by_user_id, user_id) = ?
                   ORDER BY created_at DESC""",
                (user_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT * FROM sessions
                   WHERE organization_id = ?
                     AND COALESCE(created_by_user_id, user_id) = ?
                   ORDER BY created_at DESC""",
                (organization_id, user_id),
            ).fetchall()
        return [_row_to_session(r) for r in rows]

    def count_for_organization(self, organization_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS count FROM sessions WHERE organization_id = ?",
            (organization_id,),
        ).fetchone()
        return int(row["count"] or 0) if row else 0

    def count_for_user(self, user_id: str, organization_id: str | None = None) -> int:
        if organization_id is None:
            row = self.conn.execute(
                """SELECT COUNT(*) AS count
                   FROM sessions
                   WHERE COALESCE(created_by_user_id, user_id) = ?""",
                (user_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                """SELECT COUNT(*) AS count
                   FROM sessions
                   WHERE organization_id = ?
                     AND COALESCE(created_by_user_id, user_id) = ?""",
                (organization_id, user_id),
            ).fetchone()
        return int(row["count"] or 0) if row else 0

    def set_status(self, session_id: str, status: SessionStatus) -> None:
        self.conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))

    def set_discovery_status(self, session_id: str, status: DiscoveryStatus) -> None:
        self.conn.execute(
            "UPDATE sessions SET discovery_status = ? WHERE id = ?",
            (status, session_id),
        )

    def set_overview(self, session_id: str, overview: str | None) -> None:
        self.conn.execute(
            "UPDATE sessions SET overview = ? WHERE id = ?",
            (overview, session_id),
        )

    def delete(
        self,
        session_id: str,
        user_id: str | None = None,
        organization_id: str | None = None,
    ) -> bool:
        if user_id is None:
            if organization_id is None:
                cur = self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            else:
                cur = self.conn.execute(
                    "DELETE FROM sessions WHERE id = ? AND organization_id = ?",
                    (session_id, organization_id),
                )
        else:
            if organization_id is None:
                cur = self.conn.execute(
                    """DELETE FROM sessions
                       WHERE id = ?
                         AND COALESCE(created_by_user_id, user_id) = ?""",
                    (session_id, user_id),
                )
            else:
                cur = self.conn.execute(
                    """DELETE FROM sessions
                       WHERE id = ?
                         AND organization_id = ?
                         AND COALESCE(created_by_user_id, user_id) = ?""",
                    (session_id, organization_id, user_id),
                )
        return cur.rowcount > 0


class FileRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        session_id: str,
        filename: str,
        parquet_path: str,
        row_count: int,
        raw_parquet_path: str | None = None,
        original_size_bytes: int | None = None,
        content_hash: str | None = None,
        file_id: str | None = None,
    ) -> File:
        fid = file_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO files
               (id, session_id, filename, parquet_path, raw_parquet_path,
                original_size_bytes, row_count, schema_version, content_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                fid,
                session_id,
                filename,
                parquet_path,
                raw_parquet_path,
                original_size_bytes,
                row_count,
                content_hash,
                created_at.isoformat(),
            ),
        )
        return File(
            id=fid,
            session_id=session_id,
            filename=filename,
            parquet_path=parquet_path,
            raw_parquet_path=raw_parquet_path,
            original_size_bytes=original_size_bytes,
            row_count=row_count,
            schema_version=1,
            content_hash=content_hash,
            created_at=created_at,
        )

    def find_by_hash(self, session_id: str, content_hash: str) -> File | None:
        row = self.conn.execute(
            "SELECT * FROM files WHERE session_id = ? AND content_hash = ? LIMIT 1",
            (session_id, content_hash),
        ).fetchone()
        return _row_to_file(row) if row else None

    def delete(self, file_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        return cur.rowcount > 0

    def get(self, file_id: str) -> File | None:
        row = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return _row_to_file(row) if row else None

    def list_for_session(self, session_id: str) -> list[File]:
        rows = self.conn.execute(
            "SELECT * FROM files WHERE session_id = ? ORDER BY created_at", (session_id,)
        ).fetchall()
        return [_row_to_file(r) for r in rows]

    def bump_schema_version(self, file_id: str) -> int:
        cursor = self.conn.execute(
            "UPDATE files SET schema_version = schema_version + 1 WHERE id = ? RETURNING schema_version",
            (file_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(f"file not found: {file_id}")
        return int(row[0])

    def update_processed(
        self,
        *,
        file_id: str,
        parquet_path: str,
        row_count: int,
        header_row: int | None,
        friendly_name: str | None,
        description: str | None,
    ) -> None:
        self.conn.execute(
            """UPDATE files
               SET parquet_path = ?, row_count = ?, header_row = ?,
                   friendly_name = ?, description = ?
               WHERE id = ?""",
            (
                parquet_path,
                row_count,
                header_row,
                friendly_name,
                description,
                file_id,
            ),
        )

    def set_metadata(
        self,
        *,
        file_id: str,
        header_row: int | None = None,
        friendly_name: str | None = None,
        description: str | None = None,
    ) -> None:
        self.conn.execute(
            """UPDATE files
               SET header_row = COALESCE(?, header_row),
                   friendly_name = COALESCE(?, friendly_name),
                   description = COALESCE(?, description)
               WHERE id = ?""",
            (header_row, friendly_name, description, file_id),
        )


class DocumentRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        session_id: str,
        user_id: str,
        organization_id: str,
        filename: str,
        content_hash: str,
        page_count: int,
        status: DocumentStatus = "processing",
        document_id: str | None = None,
    ) -> Document:
        did = document_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO documents
               (id, session_id, user_id, organization_id, filename, content_hash,
                page_count, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                did,
                session_id,
                user_id,
                organization_id,
                filename,
                content_hash,
                page_count,
                status,
                created_at.isoformat(),
            ),
        )
        return Document(
            id=did,
            session_id=session_id,
            user_id=user_id,
            organization_id=organization_id,
            filename=filename,
            content_hash=content_hash,
            page_count=page_count,
            status=status,
            created_at=created_at,
        )

    def find_by_hash(self, session_id: str, content_hash: str) -> Document | None:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE session_id = ? AND content_hash = ? LIMIT 1",
            (session_id, content_hash),
        ).fetchone()
        return _row_to_document(row) if row else None

    def get(self, document_id: str) -> Document | None:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        return _row_to_document(row) if row else None

    def list_for_session(self, session_id: str) -> list[Document]:
        rows = self.conn.execute(
            "SELECT * FROM documents WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        return [_row_to_document(row) for row in rows]

    def set_status(self, document_id: str, status: DocumentStatus) -> None:
        self.conn.execute(
            "UPDATE documents SET status = ? WHERE id = ?",
            (status, document_id),
        )

    def add_page(
        self,
        *,
        document_id: str,
        page_number: int,
        source: DocumentPageSource,
        markdown: str,
        char_count: int,
        quality_score: float,
        low_confidence: bool,
        quality_reasons: list[str],
        image_object_key: str | None = None,
        page_id: str | None = None,
    ) -> DocumentPage:
        pid = page_id or new_id()
        self.conn.execute(
            """INSERT INTO document_pages
               (id, document_id, page_number, source, markdown, char_count,
                quality_score, low_confidence, quality_reasons, image_object_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pid,
                document_id,
                page_number,
                source,
                markdown,
                char_count,
                quality_score,
                low_confidence,
                dumps_json(quality_reasons),
                image_object_key,
            ),
        )
        return DocumentPage(
            id=pid,
            document_id=document_id,
            page_number=page_number,
            source=source,
            markdown=markdown,
            char_count=char_count,
            quality_score=quality_score,
            low_confidence=low_confidence,
            quality_reasons=quality_reasons,
            image_object_key=image_object_key,
        )

    def list_pages(self, document_id: str) -> list[DocumentPage]:
        rows = self.conn.execute(
            "SELECT * FROM document_pages WHERE document_id = ? ORDER BY page_number",
            (document_id,),
        ).fetchall()
        return [_row_to_document_page(row) for row in rows]


class DocumentChunkRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def delete_for_document(self, document_id: str) -> None:
        self.conn.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))

    def add(
        self,
        *,
        document_id: str,
        session_id: str,
        organization_id: str,
        chunk_index: int,
        text: str,
        heading_path: Sequence[str],
        section_no: str | None,
        clause_no: str | None,
        page_number: int,
        start_char: int,
        end_char: int,
        low_confidence: bool,
        embedding: Sequence[float],
        chunk_id: str | None = None,
    ) -> DocumentChunk:
        cid = chunk_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO document_chunks
               (id, document_id, session_id, organization_id, chunk_index, text,
                heading_path, section_no, clause_no, page_number, start_char, end_char,
                low_confidence, embedding, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::vector, ?)""",
            (
                cid,
                document_id,
                session_id,
                organization_id,
                chunk_index,
                text,
                dumps_json(list(heading_path)),
                section_no,
                clause_no,
                page_number,
                start_char,
                end_char,
                low_confidence,
                vector_literal(embedding),
                created_at.isoformat(),
            ),
        )
        return DocumentChunk(
            id=cid,
            document_id=document_id,
            session_id=session_id,
            organization_id=organization_id,
            chunk_index=chunk_index,
            text=text,
            heading_path=[str(item) for item in heading_path],
            section_no=section_no,
            clause_no=clause_no,
            page_number=page_number,
            start_char=start_char,
            end_char=end_char,
            low_confidence=low_confidence,
            created_at=created_at,
        )

    def list_for_document(self, document_id: str) -> list[DocumentChunk]:
        rows = self.conn.execute(
            "SELECT * FROM document_chunks WHERE document_id = ? ORDER BY chunk_index",
            (document_id,),
        ).fetchall()
        return [_row_to_document_chunk(row) for row in rows]

    def search_bm25(
        self,
        *,
        query: str,
        session_id: str,
        organization_id: str,
        user_id: str,
        limit: int,
    ) -> list[DocumentChunkSearchRow]:
        rows = self.conn.execute(
            """WITH q AS (SELECT websearch_to_tsquery('english', ?) AS query)
               SELECT c.*, d.filename AS document_filename, ts_rank_cd(c.tsv, q.query) AS rank
               FROM document_chunks c
               JOIN documents d ON d.id = c.document_id
               CROSS JOIN q
               WHERE c.organization_id = ?
                 AND c.session_id = ?
                 AND d.user_id = ?
                 AND c.tsv @@ q.query
               ORDER BY rank DESC, c.chunk_index ASC
               LIMIT ?""",
            (query, organization_id, session_id, user_id, limit),
        ).fetchall()
        return [_row_to_document_chunk_search_row(row) for row in rows]

    def search_vector(
        self,
        *,
        embedding: Sequence[float],
        session_id: str,
        organization_id: str,
        user_id: str,
        limit: int,
    ) -> list[DocumentChunkSearchRow]:
        vector = vector_literal(embedding)
        rows = self.conn.execute(
            """SELECT c.*, d.filename AS document_filename,
                      1.0 - (c.embedding <=> ?::vector) AS rank
               FROM document_chunks c
               JOIN documents d ON d.id = c.document_id
               WHERE c.organization_id = ?
                 AND c.session_id = ?
                 AND d.user_id = ?
                 AND c.embedding IS NOT NULL
               ORDER BY c.embedding <=> ?::vector, c.chunk_index ASC
               LIMIT ?""",
            (vector, organization_id, session_id, user_id, vector, limit),
        ).fetchall()
        return [_row_to_document_chunk_search_row(row) for row in rows]


class SourceAssetRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def get_by_hash(
        self,
        user_id: str,
        sha256: str,
        organization_id: str | None = None,
    ) -> SourceAsset | None:
        if organization_id is not None:
            row = self.conn.execute(
                """SELECT * FROM source_assets
                   WHERE user_id = ? AND organization_id = ? AND sha256 = ?""",
                (user_id, organization_id, sha256),
            ).fetchone()
            return _row_to_source_asset(row) if row else None
        row = self.conn.execute(
            "SELECT * FROM source_assets WHERE user_id = ? AND sha256 = ?",
            (user_id, sha256),
        ).fetchone()
        return _row_to_source_asset(row) if row else None

    def total_size_for_organization(self, organization_id: str) -> int:
        row = self.conn.execute(
            """SELECT COALESCE(SUM(size_bytes), 0) AS total
               FROM source_assets
               WHERE organization_id = ?""",
            (organization_id,),
        ).fetchone()
        return int(row["total"] or 0) if row else 0

    def total_size_for_user(self, user_id: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM source_assets WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return int(row["total"] or 0) if row else 0

    def create(
        self,
        *,
        user_id: str,
        organization_id: str | None = None,
        sha256: str,
        original_filename: str,
        mime_type: str | None,
        size_bytes: int,
        storage_backend: str,
        object_key: str,
        asset_id: str | None = None,
    ) -> SourceAsset:
        aid = asset_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO source_assets
               (id, organization_id, user_id, sha256, original_filename, mime_type, size_bytes,
                storage_backend, object_key, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                aid,
                organization_id,
                user_id,
                sha256,
                original_filename,
                mime_type,
                size_bytes,
                storage_backend,
                object_key,
                created_at.isoformat(),
            ),
        )
        return SourceAsset(
            id=aid,
            organization_id=organization_id,
            user_id=user_id,
            sha256=sha256,
            original_filename=original_filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            storage_backend=storage_backend,
            object_key=object_key,
            created_at=created_at,
        )


class WorkspaceAssetRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def get(self, session_id: str, source_asset_id: str) -> WorkspaceAsset | None:
        row = self.conn.execute(
            "SELECT * FROM workspace_assets WHERE session_id = ? AND source_asset_id = ?",
            (session_id, source_asset_id),
        ).fetchone()
        return _row_to_workspace_asset(row) if row else None

    def create(
        self,
        *,
        session_id: str,
        source_asset_id: str,
        display_name: str,
        workspace_asset_id: str | None = None,
    ) -> WorkspaceAsset:
        wid = workspace_asset_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO workspace_assets
               (id, session_id, source_asset_id, display_name, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (wid, session_id, source_asset_id, display_name, created_at.isoformat()),
        )
        return WorkspaceAsset(
            id=wid,
            session_id=session_id,
            source_asset_id=source_asset_id,
            display_name=display_name,
            created_at=created_at,
        )


class AssetArtifactRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        user_id: str,
        organization_id: str | None = None,
        artifact_type: str,
        storage_backend: str,
        object_key: str,
        size_bytes: int,
        session_id: str | None = None,
        source_asset_id: str | None = None,
        file_id: str | None = None,
        content_hash: str | None = None,
        mime_type: str | None = None,
        artifact_id: str | None = None,
    ) -> AssetArtifact:
        aid = artifact_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO asset_artifacts
               (id, organization_id, user_id, session_id, source_asset_id, file_id, artifact_type,
                storage_backend, object_key, content_hash, size_bytes, mime_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                aid,
                organization_id,
                user_id,
                session_id,
                source_asset_id,
                file_id,
                artifact_type,
                storage_backend,
                object_key,
                content_hash,
                size_bytes,
                mime_type,
                created_at.isoformat(),
            ),
        )
        return AssetArtifact(
            id=aid,
            organization_id=organization_id,
            user_id=user_id,
            session_id=session_id,
            source_asset_id=source_asset_id,
            file_id=file_id,
            artifact_type=artifact_type,
            storage_backend=storage_backend,
            object_key=object_key,
            content_hash=content_hash,
            size_bytes=size_bytes,
            mime_type=mime_type,
            created_at=created_at,
        )

    def get(self, artifact_id: str) -> AssetArtifact | None:
        row = self.conn.execute(
            "SELECT * FROM asset_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        return _row_to_asset_artifact(row) if row else None

    def latest_for_file(self, file_id: str, artifact_type: str) -> AssetArtifact | None:
        row = self.conn.execute(
            """SELECT * FROM asset_artifacts
               WHERE file_id = ? AND artifact_type = ?
               ORDER BY created_at DESC LIMIT 1""",
            (file_id, artifact_type),
        ).fetchone()
        return _row_to_asset_artifact(row) if row else None


class WorkspaceTableRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        session_id: str,
        display_name: str,
        workspace_asset_id: str | None,
        legacy_file_id: str | None,
        sheet_name: str | None,
        table_index: int,
        row_count: int,
        table_id: str | None = None,
    ) -> WorkspaceTable:
        tid = table_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO tables
               (id, session_id, workspace_asset_id, legacy_file_id, sheet_name,
                table_index, display_name, row_count, current_schema_version, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                tid,
                session_id,
                workspace_asset_id,
                legacy_file_id,
                sheet_name,
                table_index,
                display_name,
                row_count,
                created_at.isoformat(),
            ),
        )
        return WorkspaceTable(
            id=tid,
            session_id=session_id,
            workspace_asset_id=workspace_asset_id,
            legacy_file_id=legacy_file_id,
            sheet_name=sheet_name,
            table_index=table_index,
            display_name=display_name,
            row_count=row_count,
            created_at=created_at,
        )

    def list_for_session(self, session_id: str) -> list[WorkspaceTable]:
        rows = self.conn.execute(
            "SELECT * FROM tables WHERE session_id = ? ORDER BY table_index, created_at",
            (session_id,),
        ).fetchall()
        return [_row_to_workspace_table(row) for row in rows]

    def set_processed_artifact(
        self, *, legacy_file_id: str, artifact_id: str, schema_version: int, row_count: int
    ) -> None:
        self.conn.execute(
            """UPDATE tables
               SET processed_artifact_id = ?, current_schema_version = ?, row_count = ?
               WHERE legacy_file_id = ?""",
            (artifact_id, schema_version, row_count, legacy_file_id),
        )


class UploadIntentRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        user_id: str,
        organization_id: str | None = None,
        session_id: str,
        original_filename: str,
        mime_type: str | None,
        expected_size_bytes: int,
        storage_backend: str,
        object_key: str,
        intent_id: str | None = None,
    ) -> UploadIntent:
        iid = intent_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO upload_intents
               (id, organization_id, user_id, session_id, original_filename, mime_type,
                expected_size_bytes, storage_backend, object_key, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                iid,
                organization_id,
                user_id,
                session_id,
                original_filename,
                mime_type,
                expected_size_bytes,
                storage_backend,
                object_key,
                "pending",
                created_at.isoformat(),
            ),
        )
        return UploadIntent(
            id=iid,
            organization_id=organization_id,
            user_id=user_id,
            session_id=session_id,
            original_filename=original_filename,
            mime_type=mime_type,
            expected_size_bytes=expected_size_bytes,
            storage_backend=storage_backend,
            object_key=object_key,
            status="pending",
            created_at=created_at,
        )

    def get(self, intent_id: str, user_id: str | None = None) -> UploadIntent | None:
        if user_id is None:
            row = self.conn.execute(
                "SELECT * FROM upload_intents WHERE id = ?", (intent_id,)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM upload_intents WHERE id = ? AND user_id = ?",
                (intent_id, user_id),
            ).fetchone()
        return _row_to_upload_intent(row) if row else None

    def mark_uploaded(self, intent_id: str, *, observed_size_bytes: int) -> None:
        self.conn.execute(
            """UPDATE upload_intents
               SET status = 'uploaded', observed_size_bytes = ?, completed_at = ?
               WHERE id = ?""",
            (observed_size_bytes, _now().isoformat(), intent_id),
        )

    def mark_processing(self, intent_id: str) -> None:
        self.conn.execute(
            "UPDATE upload_intents SET status = 'processing' WHERE id = ?",
            (intent_id,),
        )

    def mark_processed(self, intent_id: str, *, source_asset_id: str | None) -> None:
        self.conn.execute(
            """UPDATE upload_intents
               SET status = 'processed', source_asset_id = ?, error_message = NULL
               WHERE id = ?""",
            (source_asset_id, intent_id),
        )

    def mark_failed(self, intent_id: str, message: str) -> None:
        self.conn.execute(
            "UPDATE upload_intents SET status = 'failed', error_message = ? WHERE id = ?",
            (message[:1000], intent_id),
        )


class ProcessingJobRepository:
    ACTIVE_STATUSES = ("queued", "running")

    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        user_id: str,
        organization_id: str | None = None,
        session_id: str,
        kind: ProcessingJobKind,
        idempotency_key: str,
        checkpoint_json: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> ProcessingJob:
        existing = self.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing
        jid = job_id or new_id()
        now = _now()
        payload = checkpoint_json or {}
        self.conn.execute(
            """INSERT INTO processing_jobs
               (id, organization_id, user_id, session_id, kind, status, attempts, checkpoint_json,
                idempotency_key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)""",
            (
                jid,
                organization_id,
                user_id,
                session_id,
                kind,
                dumps_json(payload),
                idempotency_key,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        return ProcessingJob(
            id=jid,
            organization_id=organization_id,
            user_id=user_id,
            session_id=session_id,
            kind=kind,
            status="queued",
            checkpoint_json=payload,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )

    def get(self, job_id: str) -> ProcessingJob | None:
        row = self.conn.execute("SELECT * FROM processing_jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_processing_job(row) if row else None

    def get_by_idempotency_key(self, idempotency_key: str) -> ProcessingJob | None:
        row = self.conn.execute(
            "SELECT * FROM processing_jobs WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return _row_to_processing_job(row) if row else None

    def active_for_session(
        self, *, session_id: str, kind: ProcessingJobKind
    ) -> ProcessingJob | None:
        row = self.conn.execute(
            """SELECT * FROM processing_jobs
               WHERE session_id = ? AND kind = ? AND status IN ('queued', 'running')
               ORDER BY created_at DESC LIMIT 1""",
            (session_id, kind),
        ).fetchone()
        return _row_to_processing_job(row) if row else None

    def active_count(
        self,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
    ) -> int:
        clauses = ["status IN ('queued', 'running')"]
        params: list[str] = []
        if organization_id is not None:
            clauses.append("organization_id = ?")
            params.append(organization_id)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS count FROM processing_jobs WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()
        return int(row["count"] or 0) if row else 0

    def claim_next(
        self, *, worker_id: str, lock_seconds: int = 600
    ) -> ProcessingJob | None:
        now = _now()
        lock_until = now + timedelta(seconds=lock_seconds)
        if self._is_postgres():
            row = self.conn.execute(
                """SELECT * FROM processing_jobs
                   WHERE status = 'queued'
                      OR (status = 'running' AND locked_until IS NOT NULL AND locked_until < ?)
                   ORDER BY created_at
                   FOR UPDATE SKIP LOCKED
                   LIMIT 1""",
                (now.isoformat(),),
            ).fetchone()
        else:
            row = self.conn.execute(
                """SELECT * FROM processing_jobs
                   WHERE status = 'queued'
                      OR (status = 'running' AND locked_until IS NOT NULL AND locked_until < ?)
                   ORDER BY created_at
                   LIMIT 1""",
                (now.isoformat(),),
            ).fetchone()
        if row is None:
            return None
        job = _row_to_processing_job(row)
        self.conn.execute(
            """UPDATE processing_jobs
               SET status = 'running',
                   attempts = attempts + 1,
                   locked_by = ?,
                   locked_until = ?,
                   heartbeat_at = ?,
                   started_at = COALESCE(started_at, ?),
                   updated_at = ?
               WHERE id = ?""",
            (
                worker_id,
                lock_until.isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                job.id,
            ),
        )
        self.conn.commit()
        claimed = self.get(job.id)
        return claimed

    def heartbeat(
        self,
        *,
        job_id: str,
        worker_id: str,
        checkpoint_json: dict[str, Any] | None = None,
        lock_seconds: int = 600,
    ) -> None:
        now = _now()
        lock_until = now + timedelta(seconds=lock_seconds)
        if checkpoint_json is None:
            self.conn.execute(
                """UPDATE processing_jobs
                   SET heartbeat_at = ?, locked_until = ?, updated_at = ?
                   WHERE id = ? AND locked_by = ?""",
                (
                    now.isoformat(),
                    lock_until.isoformat(),
                    now.isoformat(),
                    job_id,
                    worker_id,
                ),
            )
        else:
            self.conn.execute(
                """UPDATE processing_jobs
                   SET heartbeat_at = ?, locked_until = ?, checkpoint_json = ?, updated_at = ?
                   WHERE id = ? AND locked_by = ?""",
                (
                    now.isoformat(),
                    lock_until.isoformat(),
                    dumps_json(checkpoint_json),
                    now.isoformat(),
                    job_id,
                    worker_id,
                ),
            )

    def mark_succeeded(self, job_id: str) -> None:
        now = _now()
        self.conn.execute(
            """UPDATE processing_jobs
               SET status = 'succeeded',
                   locked_by = NULL,
                   locked_until = NULL,
                   finished_at = ?,
                   updated_at = ?
               WHERE id = ?""",
            (now.isoformat(), now.isoformat(), job_id),
        )

    def mark_failed(self, job_id: str, message: str) -> None:
        now = _now()
        self.conn.execute(
            """UPDATE processing_jobs
               SET status = 'failed',
                   locked_by = NULL,
                   locked_until = NULL,
                   error_message = ?,
                   finished_at = ?,
                   updated_at = ?
               WHERE id = ?""",
            (message[:1000], now.isoformat(), now.isoformat(), job_id),
        )

    def _is_postgres(self) -> bool:
        return self.conn.__class__.__name__ == "PostgresCompatConnection"


class SchemaRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def replace(self, schema: FileSchema) -> None:
        self.conn.execute(
            "DELETE FROM schema_columns WHERE file_id = ? AND schema_version = ?",
            (schema.file_id, schema.schema_version),
        )
        for col in schema.columns:
            self.conn.execute(
                """INSERT INTO schema_columns
                   (file_id, schema_version, name, dtype, inferred_kind,
                    confidence, position, column_id, description, confidence_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    col.file_id,
                    col.schema_version,
                    col.name,
                    col.dtype,
                    col.inferred_kind,
                    col.confidence,
                    col.position,
                    col.column_id,
                    col.description,
                    col.confidence_reason,
                ),
            )

    def get(self, file_id: str, schema_version: int) -> FileSchema | None:
        rows = self.conn.execute(
            """SELECT * FROM schema_columns
               WHERE file_id = ? AND schema_version = ?
               ORDER BY position""",
            (file_id, schema_version),
        ).fetchall()
        if not rows:
            return None
        return FileSchema(
            file_id=file_id,
            schema_version=schema_version,
            columns=[_row_to_schema_column(r) for r in rows],
        )


class LinkRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        session_id: str,
        file_a: str,
        col_a: str,
        file_b: str,
        col_b: str,
        overlap: float,
        direction: str,
        score: float,
        summary: str | None = None,
        source: str = "discovered",
        link_id: str | None = None,
    ) -> Link:
        lid = link_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO links
               (id, session_id, file_a, col_a, file_b, col_b, overlap, direction, score, summary, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lid,
                session_id,
                file_a,
                col_a,
                file_b,
                col_b,
                overlap,
                direction,
                score,
                summary,
                source,
                created_at.isoformat(),
            ),
        )
        return Link(
            id=lid,
            session_id=session_id,
            file_a=file_a,
            col_a=col_a,
            file_b=file_b,
            col_b=col_b,
            overlap=overlap,
            direction=direction,  # type: ignore[arg-type]
            score=score,
            summary=summary,
            source=source,  # type: ignore[arg-type]
            created_at=created_at,
        )

    def get(self, link_id: str) -> Link | None:
        row = self.conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone()
        return _row_to_link(row) if row else None

    def latest_review(self, link_id: str) -> LinkReview | None:
        row = self.conn.execute(
            """SELECT * FROM link_reviews WHERE link_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (link_id,),
        ).fetchone()
        if row is None:
            return None
        return LinkReview(
            id=row["id"],
            link_id=row["link_id"],
            action=row["action"],
            notes=row["notes"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_for_session(self, session_id: str) -> list[Link]:
        rows = self.conn.execute(
            "SELECT * FROM links WHERE session_id = ? ORDER BY score DESC",
            (session_id,),
        ).fetchall()
        return [_row_to_link(r) for r in rows]

    def delete_for_session(self, session_id: str) -> None:
        self.conn.execute("DELETE FROM links WHERE session_id = ?", (session_id,))

    def add_review(self, *, link_id: str, action: str, notes: str | None = None) -> LinkReview:
        created_at = _now()
        cursor = self.conn.execute(
            """INSERT INTO link_reviews (link_id, action, notes, created_at)
               VALUES (?, ?, ?, ?)""",
            (link_id, action, notes, created_at.isoformat()),
        )
        return LinkReview(
            id=cursor.lastrowid,
            link_id=link_id,
            action=action,  # type: ignore[arg-type]
            notes=notes,
            created_at=created_at,
        )


class AnomalyRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create(self, anomaly: Anomaly) -> Anomaly:
        self.conn.execute(
            """INSERT INTO anomalies
               (id, session_id, file_id, row_id, detector, reason_plain, reason_technical,
                score_normalized, score_raw, source_code, review_status, reviewed_at, reviewed_by,
                notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                anomaly.id,
                anomaly.session_id,
                anomaly.file_id,
                anomaly.row_id,
                anomaly.detector,
                anomaly.reason_plain,
                anomaly.reason_technical,
                anomaly.score_normalized,
                anomaly.score_raw,
                anomaly.source_code,
                anomaly.review_status,
                anomaly.reviewed_at.isoformat() if anomaly.reviewed_at else None,
                anomaly.reviewed_by,
                anomaly.notes,
                anomaly.created_at.isoformat(),
            ),
        )
        return anomaly

    def top_for_session(self, session_id: str, limit: int = 20) -> list[Anomaly]:
        rows = self.conn.execute(
            """SELECT * FROM anomalies
               WHERE session_id = ? AND review_status IS NULL
               ORDER BY score_normalized DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [_row_to_anomaly(r) for r in rows]

    def set_review(
        self,
        *,
        anomaly_id: str,
        status: ReviewStatus | None,
        reviewed_by: str | None,
        notes: str | None,
    ) -> None:
        self.conn.execute(
            """UPDATE anomalies
               SET review_status = ?, reviewed_at = ?, reviewed_by = ?, notes = ?
               WHERE id = ?""",
            (
                status,
                _now().isoformat() if status else None,
                reviewed_by,
                notes,
                anomaly_id,
            ),
        )


class DashboardRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create(self, session_id: str, dashboard_id: str | None = None) -> Dashboard:
        did = dashboard_id or new_id()
        created_at = _now()
        self.conn.execute(
            "INSERT INTO dashboards (id, session_id, created_at) VALUES (?, ?, ?)",
            (did, session_id, created_at.isoformat()),
        )
        return Dashboard(id=did, session_id=session_id, created_at=created_at)

    def get_for_session(self, session_id: str) -> Dashboard | None:
        row = self.conn.execute(
            "SELECT * FROM dashboards WHERE session_id = ?", (session_id,)
        ).fetchone()
        return (
            Dashboard(
                id=row["id"],
                session_id=row["session_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            if row
            else None
        )

    def add_page(
        self,
        *,
        dashboard_id: str,
        title: str,
        kind: str,
        position: int,
        source_chat_turn_id: str | None = None,
        pinned: bool = False,
        page_id: str | None = None,
    ) -> DashboardPage:
        pid = page_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO dashboard_pages
               (id, dashboard_id, title, kind, source_chat_turn_id, pinned, position, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pid,
                dashboard_id,
                title,
                kind,
                source_chat_turn_id,
                int(pinned),
                position,
                created_at.isoformat(),
            ),
        )
        return DashboardPage(
            id=pid,
            dashboard_id=dashboard_id,
            title=title,
            kind=kind,  # type: ignore[arg-type]
            source_chat_turn_id=source_chat_turn_id,
            pinned=pinned,
            position=position,
            created_at=created_at,
        )

    def list_pages(self, dashboard_id: str) -> list[DashboardPage]:
        rows = self.conn.execute(
            "SELECT * FROM dashboard_pages WHERE dashboard_id = ? ORDER BY position",
            (dashboard_id,),
        ).fetchall()
        return [_row_to_page(r) for r in rows]

    def delete_page(self, page_id: str) -> None:
        self.conn.execute("DELETE FROM dashboard_pages WHERE id = ?", (page_id,))


class DashboardCellRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def add_cell(self, cell: DashboardCell) -> DashboardCell:
        self.conn.execute(
            """INSERT INTO notebook_cells
               (id, page_id, order_index, kind, code, output, bound_file_ids,
                bound_schema_versions, threshold_snapshot, last_run_at, last_run_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cell.id,
                cell.page_id,
                cell.order_index,
                cell.kind,
                cell.code,
                dumps_json(cell.output) if cell.output is not None else None,
                dumps_json(cell.bound_file_ids),
                dumps_json(cell.bound_schema_versions),
                dumps_json(cell.threshold_snapshot),
                cell.last_run_at.isoformat() if cell.last_run_at else None,
                cell.last_run_status,
                cell.created_at.isoformat(),
            ),
        )
        return cell

    def list_for_page(self, page_id: str) -> list[DashboardCell]:
        rows = self.conn.execute(
            "SELECT * FROM notebook_cells WHERE page_id = ? ORDER BY order_index",
            (page_id,),
        ).fetchall()
        return [_row_to_cell(r) for r in rows]

    def set_output(
        self,
        *,
        cell_id: str,
        output: dict[str, Any] | None,
        status: RunStatus,
    ) -> None:
        self.conn.execute(
            """UPDATE notebook_cells
               SET output = ?, last_run_at = ?, last_run_status = ?
               WHERE id = ?""",
            (
                dumps_json(output) if output is not None else None,
                _now().isoformat(),
                status,
                cell_id,
            ),
        )


class ChatRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create_turn(
        self,
        *,
        session_id: str,
        user_message: str,
        turn_id: str | None = None,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ChatTurn:
        tid = turn_id or new_id()
        created_at = _now()
        saved_metadata = metadata or {}
        self.conn.execute(
            """INSERT INTO chat_turns
               (id, session_id, user_message, title, metadata, state, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (
                tid,
                session_id,
                user_message,
                title,
                dumps_json(saved_metadata),
                created_at.isoformat(),
            ),
        )
        return ChatTurn(
            id=tid,
            session_id=session_id,
            user_message=user_message,
            title=title,
            metadata=saved_metadata,
            state="pending",
            created_at=created_at,
        )

    def set_turn_state(self, turn_id: str, state: TurnState) -> None:
        self.conn.execute("UPDATE chat_turns SET state = ? WHERE id = ?", (state, turn_id))

    def complete_turn(
        self,
        *,
        turn_id: str,
        assistant_message: str,
        spawned_page_id: str | None = None,
    ) -> None:
        self.conn.execute(
            """UPDATE chat_turns
               SET assistant_message = ?, spawned_page_id = ?, state = 'complete'
               WHERE id = ?""",
            (assistant_message, spawned_page_id, turn_id),
        )

    def append_message(
        self,
        *,
        turn_id: str,
        role: MessageRole,
        content: str,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        tool_result: dict[str, Any] | None = None,
    ) -> ChatMessage:
        mid = new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO chat_messages
               (id, turn_id, role, content, tool_call_id, tool_name, tool_args, tool_result, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mid,
                turn_id,
                role,
                content,
                tool_call_id,
                tool_name,
                dumps_json(tool_args) if tool_args is not None else None,
                dumps_json(tool_result) if tool_result is not None else None,
                created_at.isoformat(),
            ),
        )
        return ChatMessage(
            id=mid,
            turn_id=turn_id,
            role=role,
            content=content,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            created_at=created_at,
        )

    def get_turn(self, turn_id: str) -> ChatTurn | None:
        row = self.conn.execute("SELECT * FROM chat_turns WHERE id = ?", (turn_id,)).fetchone()
        return _row_to_turn(row) if row else None

    def update_turn(
        self,
        *,
        turn_id: str,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ChatTurn | None:
        current = self.get_turn(turn_id)
        if current is None:
            return None
        next_title = title if title is not None else current.title
        next_metadata = metadata if metadata is not None else current.metadata
        self.conn.execute(
            "UPDATE chat_turns SET title = ?, metadata = ? WHERE id = ?",
            (next_title, dumps_json(next_metadata), turn_id),
        )
        return self.get_turn(turn_id)

    def list_turns(self, session_id: str) -> list[ChatTurn]:
        rows = self.conn.execute(
            "SELECT * FROM chat_turns WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        return [_row_to_turn(r) for r in rows]

    def list_messages_for_session(self, session_id: str) -> dict[str, list[ChatMessage]]:
        rows = self.conn.execute(
            """SELECT chat_messages.*
               FROM chat_messages
               JOIN chat_turns ON chat_turns.id = chat_messages.turn_id
               WHERE chat_turns.session_id = ?
               ORDER BY chat_turns.created_at, chat_messages.created_at""",
            (session_id,),
        ).fetchall()
        messages_by_turn: dict[str, list[ChatMessage]] = {}
        for row in rows:
            message = _row_to_message(row)
            messages_by_turn.setdefault(message.turn_id, []).append(message)
        return messages_by_turn

    def list_messages(self, turn_id: str) -> list[ChatMessage]:
        rows = self.conn.execute(
            "SELECT * FROM chat_messages WHERE turn_id = ? ORDER BY created_at",
            (turn_id,),
        ).fetchall()
        return [_row_to_message(r) for r in rows]

    def delete_turn(self, turn_id: str) -> None:
        self.conn.execute("DELETE FROM chat_turns WHERE id = ?", (turn_id,))


class ChatArtifactRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        session_id: str,
        artifact_type: str,
        title: str,
        turn_id: str | None = None,
        message_id: str | None = None,
        inline_payload: dict[str, Any] | None = None,
        storage_backend: str | None = None,
        object_key: str | None = None,
        size_bytes: int = 0,
        mime_type: str | None = None,
        order_index: int = 0,
        artifact_id: str | None = None,
    ) -> ChatArtifact:
        aid = artifact_id or new_id()
        created_at = _now()
        self.conn.execute(
            """INSERT INTO chat_artifacts
               (id, session_id, turn_id, message_id, artifact_type, title,
                inline_payload, storage_backend, object_key, size_bytes,
                mime_type, order_index, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                aid,
                session_id,
                turn_id,
                message_id,
                artifact_type,
                title,
                dumps_json(inline_payload) if inline_payload is not None else None,
                storage_backend,
                object_key,
                size_bytes,
                mime_type,
                order_index,
                created_at.isoformat(),
            ),
        )
        return ChatArtifact(
            id=aid,
            session_id=session_id,
            turn_id=turn_id,
            message_id=message_id,
            artifact_type=artifact_type,
            title=title,
            inline_payload=inline_payload,
            storage_backend=storage_backend,
            object_key=object_key,
            size_bytes=size_bytes,
            mime_type=mime_type,
            order_index=order_index,
            created_at=created_at,
        )

    def list_for_turn(self, turn_id: str) -> list[ChatArtifact]:
        rows = self.conn.execute(
            "SELECT * FROM chat_artifacts WHERE turn_id = ? ORDER BY order_index",
            (turn_id,),
        ).fetchall()
        return [_row_to_chat_artifact(r) for r in rows]

    def list_for_session(self, session_id: str) -> dict[str, list[ChatArtifact]]:
        rows = self.conn.execute(
            """SELECT * FROM chat_artifacts
               WHERE session_id = ? AND turn_id IS NOT NULL
               ORDER BY turn_id, order_index""",
            (session_id,),
        ).fetchall()
        artifacts_by_turn: dict[str, list[ChatArtifact]] = {}
        for row in rows:
            artifact = _row_to_chat_artifact(row)
            if artifact.turn_id is None:
                continue
            artifacts_by_turn.setdefault(artifact.turn_id, []).append(artifact)
        return artifacts_by_turn


class AuditRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def log(
        self,
        *,
        kind: str,
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        created_at = _now()
        cursor = self.conn.execute(
            """INSERT INTO audit_events (session_id, kind, details, created_at)
               VALUES (?, ?, ?, ?)""",
            (session_id, kind, dumps_json(details or {}), created_at.isoformat()),
        )
        return AuditEvent(
            id=cursor.lastrowid,
            session_id=session_id,
            kind=kind,
            details=details or {},
            created_at=created_at,
        )


class DataDocRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def get(self, session_id: str) -> DataDoc | None:
        row = self.conn.execute(
            "SELECT * FROM data_docs WHERE session_id = ?", (session_id,)
        ).fetchone()
        return _row_to_data_doc(row) if row else None

    def replace(self, doc: DataDoc) -> DataDoc:
        now = _now()
        existing = self.get(doc.session_id)
        created_at = existing.created_at if existing is not None else now
        saved = doc.model_copy(update={"created_at": created_at, "updated_at": now})
        self.conn.execute(
            """INSERT INTO data_docs (session_id, content, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                   content = excluded.content,
                   updated_at = excluded.updated_at""",
            (
                saved.session_id,
                dumps_json(saved.model_dump(mode="json")),
                saved.created_at.isoformat(),
                saved.updated_at.isoformat(),
            ),
        )
        return saved


class ProcessingEventRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def append(
        self,
        *,
        session_id: str,
        kind: ProcessingEventKind,
        message: str,
        job_id: str | None = None,
        step_key: str | None = None,
        level: str | None = None,
        progress: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProcessingEvent:
        created_at = _now()
        cursor = self.conn.execute(
            """INSERT INTO processing_events
               (session_id, kind, message, created_at, job_id, step_key, level, progress, details)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                kind,
                message,
                created_at.isoformat(),
                job_id,
                step_key,
                level,
                progress,
                dumps_json(details or {}),
            ),
        )
        try:
            self.conn.commit()
        except Exception as exc:
            logger.warning("processing_events commit failed: %s", exc)
        return ProcessingEvent(
            id=cursor.lastrowid,
            session_id=session_id,
            kind=kind,
            message=message,
            created_at=created_at,
            job_id=job_id,
            step_key=step_key,
            level=level,
            progress=progress,
            details=details or {},
        )

    def list_for_session(self, session_id: str) -> list[ProcessingEvent]:
        rows = self.conn.execute(
            "SELECT * FROM processing_events WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [_row_to_processing_event(r) for r in rows]

    def clear(self, session_id: str) -> None:
        self.conn.execute("DELETE FROM processing_events WHERE session_id = ?", (session_id,))


def _row_to_session(row: DbRow) -> Session:
    keys = row.keys()
    return Session(
        id=row["id"],
        organization_id=row["organization_id"] if "organization_id" in keys else None,
        user_id=row["user_id"] if "user_id" in keys else None,
        created_by_user_id=row["created_by_user_id"] if "created_by_user_id" in keys else None,
        name=row["name"],
        status=row["status"],
        discovery_status=row["discovery_status"] if "discovery_status" in keys else "empty",
        overview=row["overview"] if "overview" in keys else None,
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_file(row: DbRow) -> File:
    keys = row.keys()
    return File(
        id=row["id"],
        session_id=row["session_id"],
        filename=row["filename"],
        parquet_path=row["parquet_path"],
        raw_parquet_path=row["raw_parquet_path"] if "raw_parquet_path" in keys else None,
        original_size_bytes=(
            row["original_size_bytes"] if "original_size_bytes" in keys else None
        ),
        row_count=row["row_count"],
        schema_version=row["schema_version"],
        header_row=row["header_row"] if "header_row" in keys else None,
        friendly_name=row["friendly_name"] if "friendly_name" in keys else None,
        description=row["description"] if "description" in keys else None,
        content_hash=row["content_hash"] if "content_hash" in keys else None,
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_document(row: DbRow) -> Document:
    return Document(
        id=row["id"],
        session_id=row["session_id"],
        user_id=row["user_id"],
        organization_id=row["organization_id"],
        filename=row["filename"],
        content_hash=row["content_hash"],
        page_count=row["page_count"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_document_page(row: DbRow) -> DocumentPage:
    reasons = loads_json(row["quality_reasons"], [])
    if not isinstance(reasons, list):
        reasons = []
    return DocumentPage(
        id=row["id"],
        document_id=row["document_id"],
        page_number=row["page_number"],
        source=row["source"],
        markdown=row["markdown"],
        char_count=row["char_count"],
        quality_score=float(row["quality_score"]),
        low_confidence=bool(row["low_confidence"]),
        quality_reasons=[str(reason) for reason in reasons],
        image_object_key=row["image_object_key"],
    )


def _row_to_document_chunk(row: DbRow) -> DocumentChunk:
    heading_path = loads_json(row["heading_path"], [])
    if not isinstance(heading_path, list):
        heading_path = []
    return DocumentChunk(
        id=row["id"],
        document_id=row["document_id"],
        session_id=row["session_id"],
        organization_id=row["organization_id"],
        chunk_index=row["chunk_index"],
        text=row["text"],
        heading_path=[str(item) for item in heading_path],
        section_no=row["section_no"],
        clause_no=row["clause_no"],
        page_number=row["page_number"],
        start_char=row["start_char"],
        end_char=row["end_char"],
        low_confidence=bool(row["low_confidence"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_document_chunk_search_row(row: DbRow) -> DocumentChunkSearchRow:
    return DocumentChunkSearchRow(
        chunk=_row_to_document_chunk(row),
        document_filename=row["document_filename"],
        rank=float(row["rank"] or 0.0),
    )


def _row_to_source_asset(row: DbRow) -> SourceAsset:
    keys = row.keys()
    return SourceAsset(
        id=row["id"],
        organization_id=row["organization_id"] if "organization_id" in keys else None,
        user_id=row["user_id"],
        sha256=row["sha256"],
        original_filename=row["original_filename"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        storage_backend=row["storage_backend"],
        object_key=row["object_key"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_workspace_asset(row: DbRow) -> WorkspaceAsset:
    return WorkspaceAsset(
        id=row["id"],
        session_id=row["session_id"],
        source_asset_id=row["source_asset_id"],
        display_name=row["display_name"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_workspace_table(row: DbRow) -> WorkspaceTable:
    return WorkspaceTable(
        id=row["id"],
        session_id=row["session_id"],
        workspace_asset_id=row["workspace_asset_id"],
        legacy_file_id=row["legacy_file_id"],
        sheet_name=row["sheet_name"],
        table_index=row["table_index"],
        display_name=row["display_name"],
        row_count=row["row_count"],
        current_schema_version=row["current_schema_version"],
        processed_artifact_id=row["processed_artifact_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_asset_artifact(row: DbRow) -> AssetArtifact:
    keys = row.keys()
    return AssetArtifact(
        id=row["id"],
        organization_id=row["organization_id"] if "organization_id" in keys else None,
        user_id=row["user_id"],
        session_id=row["session_id"],
        source_asset_id=row["source_asset_id"],
        file_id=row["file_id"],
        artifact_type=row["artifact_type"],
        storage_backend=row["storage_backend"],
        object_key=row["object_key"],
        content_hash=row["content_hash"],
        size_bytes=row["size_bytes"],
        mime_type=row["mime_type"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_upload_intent(row: DbRow) -> UploadIntent:
    keys = row.keys()
    return UploadIntent(
        id=row["id"],
        organization_id=row["organization_id"] if "organization_id" in keys else None,
        user_id=row["user_id"],
        session_id=row["session_id"],
        original_filename=row["original_filename"],
        mime_type=row["mime_type"],
        expected_size_bytes=row["expected_size_bytes"],
        observed_size_bytes=row["observed_size_bytes"],
        storage_backend=row["storage_backend"],
        object_key=row["object_key"],
        status=row["status"],
        source_asset_id=row["source_asset_id"],
        error_message=row["error_message"],
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=_parse_dt(row["completed_at"]),
    )


def _row_to_processing_job(row: DbRow) -> ProcessingJob:
    keys = row.keys()
    checkpoint = loads_json(row["checkpoint_json"], {})
    if not isinstance(checkpoint, dict):
        checkpoint = {}
    return ProcessingJob(
        id=row["id"],
        organization_id=row["organization_id"] if "organization_id" in keys else None,
        user_id=row["user_id"],
        session_id=row["session_id"],
        kind=row["kind"],
        status=row["status"],
        attempts=row["attempts"],
        locked_by=row["locked_by"],
        locked_until=_parse_dt(row["locked_until"]),
        heartbeat_at=_parse_dt(row["heartbeat_at"]),
        checkpoint_json=checkpoint,
        error_message=row["error_message"],
        idempotency_key=row["idempotency_key"],
        started_at=_parse_dt(row["started_at"]),
        finished_at=_parse_dt(row["finished_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_processing_event(row: DbRow) -> ProcessingEvent:
    keys = row.keys()
    details = loads_json(row["details"], {}) if "details" in keys else {}
    if not isinstance(details, dict):
        details = {}
    return ProcessingEvent(
        id=row["id"],
        session_id=row["session_id"],
        kind=row["kind"],
        message=row["message"],
        created_at=datetime.fromisoformat(row["created_at"]),
        job_id=row["job_id"] if "job_id" in keys else None,
        step_key=row["step_key"] if "step_key" in keys else None,
        level=row["level"] if "level" in keys else None,
        progress=row["progress"] if "progress" in keys else None,
        details=details,
    )


def _row_to_data_doc(row: DbRow) -> DataDoc:
    content = loads_json(row["content"])
    if not isinstance(content, dict):
        content = {}
    content["session_id"] = row["session_id"]
    content["created_at"] = row["created_at"]
    content["updated_at"] = row["updated_at"]
    return DataDoc.model_validate(content)


def _row_to_user(row: DbRow) -> User:
    keys = row.keys()
    return User(
        id=row["id"],
        google_sub=row["google_sub"],
        email=row["email"],
        name=row["name"],
        picture=row["picture"],
        access_status=row["access_status"] if "access_status" in keys else "pending",
        access_granted_at=_parse_dt(row["access_granted_at"])
        if "access_granted_at" in keys
        else None,
        access_code_used=row["access_code_used"] if "access_code_used" in keys else None,
        number_system=row["number_system"] if "number_system" in keys else "international",
        created_at=datetime.fromisoformat(row["created_at"]),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
    )


def _row_to_organization(row: DbRow) -> Organization:
    return Organization(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_organization_member(row: DbRow) -> OrganizationMember:
    return OrganizationMember(
        organization_id=row["organization_id"],
        user_id=row["user_id"],
        role=row["role"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_organization_invite(row: DbRow) -> OrganizationInvite:
    return OrganizationInvite(
        id=row["id"],
        organization_id=row["organization_id"],
        email=row["email"],
        role=row["role"],
        invited_by_user_id=row["invited_by_user_id"],
        status=row["status"],
        token=row["token"],
        expires_at=_parse_dt(row["expires_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        accepted_at=_parse_dt(row["accepted_at"]),
    )


def _row_to_organization_entitlements(row: DbRow) -> OrganizationEntitlements:
    keys = row.keys()
    flags = loads_json(row["feature_flags"], default={})
    if not isinstance(flags, dict):
        flags = {}
    return OrganizationEntitlements(
        organization_id=row["organization_id"],
        plan_name=row["plan_name"],
        contract_status=row["contract_status"],
        seat_limit=row["seat_limit"],
        daily_token_limit=row["daily_token_limit"] if "daily_token_limit" in keys else None,
        monthly_token_limit=row["monthly_token_limit"],
        storage_quota_bytes=row["storage_quota_bytes"],
        monthly_upload_bytes=row["monthly_upload_bytes"],
        max_file_size_bytes=row["max_file_size_bytes"],
        max_workspaces=row["max_workspaces"],
        max_concurrent_jobs=(
            row["max_concurrent_jobs"] if "max_concurrent_jobs" in keys else None
        ),
        soft_limit_percent=row["soft_limit_percent"],
        hard_limit_percent=row["hard_limit_percent"],
        feature_flags=flags,
        notes=row["notes"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_user_organization_limits(row: DbRow) -> UserOrganizationLimits:
    return UserOrganizationLimits(
        organization_id=row["organization_id"],
        user_id=row["user_id"],
        daily_token_limit=row["daily_token_limit"],
        monthly_token_limit=row["monthly_token_limit"],
        storage_quota_bytes=row["storage_quota_bytes"],
        monthly_upload_bytes=row["monthly_upload_bytes"],
        max_file_size_bytes=row["max_file_size_bytes"],
        max_sessions=row["max_sessions"],
        max_concurrent_jobs=row["max_concurrent_jobs"],
        notes=row["notes"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_beta_code(row: DbRow) -> BetaCode:
    return BetaCode(
        code=row["code"],
        note=row["note"],
        max_uses=row["max_uses"],
        uses_count=row["uses_count"],
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=_parse_dt(row["expires_at"]),
    )


def _row_to_approved_email(row: DbRow) -> ApprovedEmail:
    return ApprovedEmail(
        email=row["email"],
        note=row["note"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class ApprovedEmailRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    @staticmethod
    def normalize(email: str) -> str:
        return email.strip().lower()

    def is_approved(self, email: str) -> bool:
        normalized = self.normalize(email)
        if not normalized:
            return False
        row = self.conn.execute(
            "SELECT email FROM approved_emails WHERE email = ?",
            (normalized,),
        ).fetchone()
        return row is not None

    def add(self, email: str, note: str | None = None) -> ApprovedEmail:
        normalized = self.normalize(email)
        if not normalized:
            raise ValueError("email cannot be empty")
        now = _now().isoformat()
        self.conn.execute(
            """INSERT INTO approved_emails (email, note, created_at)
               VALUES (?, ?, ?)
               ON CONFLICT (email) DO UPDATE SET note = excluded.note""",
            (normalized, note, now),
        )
        row = self.conn.execute(
            "SELECT * FROM approved_emails WHERE email = ?",
            (normalized,),
        ).fetchone()
        if row is None:
            raise LookupError(f"approved email not found after upsert: {normalized}")
        return _row_to_approved_email(row)

    def remove(self, email: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM approved_emails WHERE email = ?",
            (self.normalize(email),),
        )
        return cursor.rowcount > 0

    def list_all(self) -> list[ApprovedEmail]:
        rows = self.conn.execute("SELECT * FROM approved_emails ORDER BY created_at DESC").fetchall()
        return [_row_to_approved_email(row) for row in rows]


class UserRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def upsert_from_google(
        self,
        *,
        google_sub: str,
        email: str,
        name: str | None,
        picture: str | None,
        email_approved: bool = False,
    ) -> User:
        now = _now().isoformat()
        existing = self.conn.execute(
            "SELECT * FROM users WHERE google_sub = ?", (google_sub,)
        ).fetchone()
        if existing is None:
            uid = new_id()
            initial_status = "granted" if email_approved else "pending"
            granted_at = now if email_approved else None
            code_used = "EMAIL_ALLOWLIST" if email_approved else None
            self.conn.execute(
                """INSERT INTO users
                   (id, google_sub, email, name, picture,
                    access_status, access_granted_at, access_code_used,
                    created_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uid,
                    google_sub,
                    email,
                    name,
                    picture,
                    initial_status,
                    granted_at,
                    code_used,
                    now,
                    now,
                ),
            )
            row = self.conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if row is None:
                raise LookupError(f"user not found after create: {uid}")
            return _row_to_user(row)
        # Existing user: refresh profile + last_seen, then reconcile access
        # against the email allowlist. Revoked users stay revoked.
        if existing["access_status"] != "revoked" and email_approved:
            self.conn.execute(
                """UPDATE users
                   SET email = ?, name = ?, picture = ?, last_seen_at = ?,
                       access_status = 'granted',
                       access_granted_at = COALESCE(access_granted_at, ?),
                       access_code_used = 'EMAIL_ALLOWLIST'
                   WHERE id = ?""",
                (email, name, picture, now, now, existing["id"]),
            )
        elif existing["access_status"] == "granted":
            self.conn.execute(
                """UPDATE users
                   SET email = ?, name = ?, picture = ?, last_seen_at = ?,
                       access_status = 'pending',
                       access_code_used = NULL
                   WHERE id = ?""",
                (email, name, picture, now, existing["id"]),
            )
        else:
            self.conn.execute(
                """UPDATE users
                   SET email = ?, name = ?, picture = ?, last_seen_at = ?
                   WHERE id = ?""",
                (email, name, picture, now, existing["id"]),
            )
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (existing["id"],)).fetchone()
        if row is None:
            raise LookupError(f"user not found after profile refresh: {existing['id']}")
        return _row_to_user(row)

    def get(self, user_id: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row) if row else None

    def get_by_google_sub(self, google_sub: str) -> User | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE google_sub = ?", (google_sub,)
        ).fetchone()
        return _row_to_user(row) if row else None

    def get_by_email(self, email: str) -> User | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE lower(email) = lower(?)", (email,)
        ).fetchone()
        return _row_to_user(row) if row else None

    def list_all(self) -> list[User]:
        rows = self.conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        return [_row_to_user(r) for r in rows]

    def mark_granted(self, user_id: str, code: str) -> User | None:
        now = _now().isoformat()
        self.conn.execute(
            """UPDATE users
               SET access_status = 'granted',
                   access_granted_at = ?,
                   access_code_used = ?
               WHERE id = ?""",
            (now, code, user_id),
        )
        return self.get(user_id)

    def sync_allowlist_status(self, user_id: str, *, email_approved: bool) -> User | None:
        user = self.get(user_id)
        if user is None:
            return None
        if user.access_status == "revoked":
            return user
        if email_approved:
            return self.mark_granted(user_id, "EMAIL_ALLOWLIST")
        if user.access_status == "granted":
            self.conn.execute(
                """UPDATE users
                   SET access_status = 'pending',
                       access_code_used = NULL
                   WHERE id = ?""",
                (user_id,),
            )
            return self.get(user_id)
        return user

    def set_access_status(self, user_id: str, status: AccessStatus) -> User | None:
        self.conn.execute(
            "UPDATE users SET access_status = ? WHERE id = ?",
            (status, user_id),
        )
        return self.get(user_id)

    def update_number_system(self, user_id: str, number_system: NumberSystem) -> User | None:
        self.conn.execute(
            "UPDATE users SET number_system = ? WHERE id = ?",
            (number_system, user_id),
        )
        return self.get(user_id)


class BetaCodeRepository:
    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        code: str,
        note: str | None,
        max_uses: int,
        expires_at: datetime | None = None,
    ) -> BetaCode:
        normalized = code.strip().upper()
        if not normalized:
            raise ValueError("code cannot be empty")
        if max_uses < 1:
            raise ValueError("max_uses must be >= 1")
        now = _now()
        self.conn.execute(
            """INSERT INTO beta_codes
               (code, note, max_uses, uses_count, created_at, expires_at)
               VALUES (?, ?, ?, 0, ?, ?)""",
            (
                normalized,
                note,
                max_uses,
                now.isoformat(),
                expires_at.isoformat() if expires_at else None,
            ),
        )
        return BetaCode(
            code=normalized,
            note=note,
            max_uses=max_uses,
            uses_count=0,
            created_at=now,
            expires_at=expires_at,
        )

    def get(self, code: str) -> BetaCode | None:
        row = self.conn.execute(
            "SELECT * FROM beta_codes WHERE code = ?",
            (code.strip().upper(),),
        ).fetchone()
        return _row_to_beta_code(row) if row else None

    def list_all(self) -> list[BetaCode]:
        rows = self.conn.execute("SELECT * FROM beta_codes ORDER BY created_at DESC").fetchall()
        return [_row_to_beta_code(r) for r in rows]

    def redeem(self, code: str) -> BetaCode | None:
        """Atomically increment uses_count if the code is valid.

        Returns the updated BetaCode on success, None if the code does not
        exist, has expired, or has been exhausted.
        """
        normalized = code.strip().upper()
        now_iso = _now().isoformat()
        cursor = self.conn.execute(
            """UPDATE beta_codes
               SET uses_count = uses_count + 1
               WHERE code = ?
                 AND uses_count < max_uses
                 AND (expires_at IS NULL OR expires_at > ?)""",
            (normalized, now_iso),
        )
        if cursor.rowcount == 0:
            return None
        return self.get(normalized)

    def delete(self, code: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM beta_codes WHERE code = ?",
            (code.strip().upper(),),
        )
        return cursor.rowcount > 0


class LLMUsageRepository:
    def __init__(self, conn: DbConnection, *, auto_commit: bool = False) -> None:
        self.conn = conn
        self.auto_commit = auto_commit

    def get(self, user_id: str, day: str, organization_id: str | None = None) -> int:
        if organization_id is not None:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(amount), 0) AS tokens_used
                   FROM usage_events
                   WHERE user_id = ?
                     AND organization_id = ?
                     AND event_type = 'llm_tokens'
                     AND resource_type = 'tokens'
                     AND occurred_at LIKE ?""",
                (user_id, organization_id, f"{day}%"),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT tokens_used FROM llm_usage WHERE user_id = ? AND day = ?",
                (user_id, day),
            ).fetchone()
        return int(row["tokens_used"]) if row else 0

    def get_monthly_user_tokens(
        self,
        user_id: str,
        month_prefix: str,
        organization_id: str | None = None,
    ) -> int:
        if organization_id is not None:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(amount), 0) AS total
                   FROM usage_events
                   WHERE user_id = ?
                     AND organization_id = ?
                     AND event_type = 'llm_tokens'
                     AND resource_type = 'tokens'
                     AND occurred_at LIKE ?""",
                (user_id, organization_id, f"{month_prefix}-%"),
            ).fetchone()
        else:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(tokens_used), 0) AS total
                   FROM llm_usage
                   WHERE user_id = ? AND day LIKE ?""",
                (user_id, f"{month_prefix}-%"),
            ).fetchone()
        return int(row["total"] or 0) if row else 0

    def get_monthly_organization_tokens(self, organization_id: str, month_prefix: str) -> int:
        row = self.conn.execute(
            """SELECT COALESCE(SUM(amount), 0) AS total
               FROM organization_usage_daily
               WHERE organization_id = ?
                 AND metric = 'llm_tokens'
                 AND day LIKE ?""",
            (organization_id, f"{month_prefix}-%"),
        ).fetchone()
        return int(row["total"] or 0) if row else 0

    def add_tokens(
        self,
        user_id: str,
        day: str,
        tokens: int,
        model: str | None = None,
        organization_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
        duration_ms: int | None = None,
    ) -> int:
        response_ms = max(0, int(duration_ms or 0))
        occurred_at = _now()
        if occurred_at.date().isoformat() != day:
            occurred_at = datetime.fromisoformat(f"{day}T00:00:00+00:00")
        if organization_id is not None:
            self.conn.execute(
                """INSERT INTO llm_usage
                   (organization_id, user_id, day, tokens_used, input_tokens,
                    output_tokens, cached_tokens, reasoning_tokens, call_count,
                    total_response_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(user_id, day) DO UPDATE
                   SET tokens_used = llm_usage.tokens_used + excluded.tokens_used,
                       input_tokens = llm_usage.input_tokens + excluded.input_tokens,
                       output_tokens = llm_usage.output_tokens + excluded.output_tokens,
                       cached_tokens = llm_usage.cached_tokens + excluded.cached_tokens,
                       reasoning_tokens = llm_usage.reasoning_tokens + excluded.reasoning_tokens,
                       call_count = llm_usage.call_count + 1,
                       total_response_ms = llm_usage.total_response_ms + excluded.total_response_ms,
                       organization_id = COALESCE(llm_usage.organization_id, excluded.organization_id)""",
                (
                    organization_id,
                    user_id,
                    day,
                    tokens,
                    input_tokens,
                    output_tokens,
                    cached_tokens,
                    reasoning_tokens,
                    response_ms,
                ),
            )
        else:
            self.conn.execute(
                """INSERT INTO llm_usage
                   (user_id, day, tokens_used, input_tokens, output_tokens,
                    cached_tokens, reasoning_tokens, call_count, total_response_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(user_id, day) DO UPDATE
                   SET tokens_used = llm_usage.tokens_used + excluded.tokens_used,
                       input_tokens = llm_usage.input_tokens + excluded.input_tokens,
                       output_tokens = llm_usage.output_tokens + excluded.output_tokens,
                       cached_tokens = llm_usage.cached_tokens + excluded.cached_tokens,
                       reasoning_tokens = llm_usage.reasoning_tokens + excluded.reasoning_tokens,
                       call_count = llm_usage.call_count + 1,
                       total_response_ms = llm_usage.total_response_ms + excluded.total_response_ms""",
                (
                    user_id,
                    day,
                    tokens,
                    input_tokens,
                    output_tokens,
                    cached_tokens,
                    reasoning_tokens,
                    response_ms,
                ),
            )
        normalized_model = model.strip() if model else ""
        if normalized_model:
            if organization_id is not None:
                self.conn.execute(
                    """INSERT INTO llm_usage_by_model
                       (organization_id, user_id, day, model, tokens_used,
                        input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                        call_count, total_response_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                       ON CONFLICT(user_id, day, model) DO UPDATE
                       SET tokens_used = llm_usage_by_model.tokens_used + excluded.tokens_used,
                           input_tokens = llm_usage_by_model.input_tokens + excluded.input_tokens,
                           output_tokens = llm_usage_by_model.output_tokens + excluded.output_tokens,
                           cached_tokens = llm_usage_by_model.cached_tokens + excluded.cached_tokens,
                           reasoning_tokens = llm_usage_by_model.reasoning_tokens + excluded.reasoning_tokens,
                           call_count = llm_usage_by_model.call_count + 1,
                           total_response_ms = llm_usage_by_model.total_response_ms + excluded.total_response_ms,
                           organization_id = COALESCE(
                               llm_usage_by_model.organization_id,
                               excluded.organization_id
                           )""",
                    (
                        organization_id,
                        user_id,
                        day,
                        normalized_model,
                        tokens,
                        input_tokens,
                        output_tokens,
                        cached_tokens,
                        reasoning_tokens,
                        response_ms,
                    ),
                )
            else:
                self.conn.execute(
                    """INSERT INTO llm_usage_by_model
                       (user_id, day, model, tokens_used, input_tokens, output_tokens,
                        cached_tokens, reasoning_tokens, call_count, total_response_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                       ON CONFLICT(user_id, day, model) DO UPDATE
                       SET tokens_used = llm_usage_by_model.tokens_used + excluded.tokens_used,
                           input_tokens = llm_usage_by_model.input_tokens + excluded.input_tokens,
                           output_tokens = llm_usage_by_model.output_tokens + excluded.output_tokens,
                           cached_tokens = llm_usage_by_model.cached_tokens + excluded.cached_tokens,
                           reasoning_tokens = llm_usage_by_model.reasoning_tokens + excluded.reasoning_tokens,
                           call_count = llm_usage_by_model.call_count + 1,
                           total_response_ms = llm_usage_by_model.total_response_ms + excluded.total_response_ms""",
                    (
                        user_id,
                        day,
                        normalized_model,
                        tokens,
                        input_tokens,
                        output_tokens,
                        cached_tokens,
                        reasoning_tokens,
                        response_ms,
                    ),
                )
        if organization_id is not None:
            self.conn.execute(
                """INSERT INTO usage_events
                   (id, organization_id, user_id, event_type, resource_type,
                    amount, model, metadata, occurred_at)
                   VALUES (?, ?, ?, 'llm_tokens', 'tokens', ?, ?, ?, ?)""",
                (
                    new_id(),
                    organization_id,
                    user_id,
                    tokens,
                    normalized_model or None,
                    dumps_json(
                        {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "cached_tokens": cached_tokens,
                            "reasoning_tokens": reasoning_tokens,
                            "duration_ms": response_ms,
                        }
                    ),
                    occurred_at.isoformat(),
                ),
            )
            self.conn.execute(
                """INSERT INTO organization_usage_daily
                   (organization_id, day, metric, amount)
                   VALUES (?, ?, 'llm_tokens', ?)
                   ON CONFLICT(organization_id, day, metric) DO UPDATE
                   SET amount = organization_usage_daily.amount + excluded.amount""",
                (organization_id, day, tokens),
            )
            if normalized_model:
                self.conn.execute(
                    """INSERT INTO organization_usage_daily_by_model
                       (organization_id, day, metric, model, amount)
                       VALUES (?, ?, 'llm_tokens', ?, ?)
                       ON CONFLICT(organization_id, day, metric, model) DO UPDATE
                       SET amount = organization_usage_daily_by_model.amount + excluded.amount""",
                    (organization_id, day, normalized_model, tokens),
                )
        if self.auto_commit:
            self.conn.commit()
        return self.get(user_id, day, organization_id)


def _row_to_schema_column(row: DbRow) -> SchemaColumn:
    keys = row.keys()
    return SchemaColumn(
        file_id=row["file_id"],
        schema_version=row["schema_version"],
        name=row["name"],
        dtype=row["dtype"],
        inferred_kind=row["inferred_kind"],
        confidence=row["confidence"],
        position=row["position"],
        column_id=row["column_id"] if "column_id" in keys else None,
        description=row["description"] if "description" in keys else None,
        confidence_reason=row["confidence_reason"] if "confidence_reason" in keys else None,
    )


def _row_to_link(row: DbRow) -> Link:
    return Link(
        id=row["id"],
        session_id=row["session_id"],
        file_a=row["file_a"],
        col_a=row["col_a"],
        file_b=row["file_b"],
        col_b=row["col_b"],
        overlap=row["overlap"],
        direction=row["direction"],
        score=row["score"],
        summary=row["summary"],
        source=row["source"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_anomaly(row: DbRow) -> Anomaly:
    return Anomaly(
        id=row["id"],
        session_id=row["session_id"],
        file_id=row["file_id"],
        row_id=row["row_id"],
        detector=row["detector"],
        reason_plain=row["reason_plain"],
        reason_technical=row["reason_technical"],
        score_normalized=row["score_normalized"],
        score_raw=row["score_raw"],
        source_code=row["source_code"],
        review_status=row["review_status"],
        reviewed_at=_parse_dt(row["reviewed_at"]),
        reviewed_by=row["reviewed_by"],
        notes=row["notes"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_page(row: DbRow) -> DashboardPage:
    return DashboardPage(
        id=row["id"],
        dashboard_id=row["dashboard_id"],
        title=row["title"],
        kind=row["kind"],
        source_chat_turn_id=row["source_chat_turn_id"],
        pinned=bool(row["pinned"]),
        position=row["position"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_cell(row: DbRow) -> DashboardCell:
    return DashboardCell(
        id=row["id"],
        page_id=row["page_id"],
        order_index=row["order_index"],
        kind=row["kind"],
        code=row["code"],
        output=loads_json(row["output"]),
        bound_file_ids=loads_json(row["bound_file_ids"], default=[]),
        bound_schema_versions=loads_json(row["bound_schema_versions"], default={}),
        threshold_snapshot=loads_json(row["threshold_snapshot"], default={}),
        last_run_at=_parse_dt(row["last_run_at"]),
        last_run_status=row["last_run_status"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_turn(row: DbRow) -> ChatTurn:
    title = row["title"] if "title" in row.keys() else None
    metadata = loads_json(row["metadata"], default={}) if "metadata" in row.keys() else {}
    return ChatTurn(
        id=row["id"],
        session_id=row["session_id"],
        user_message=row["user_message"],
        assistant_message=row["assistant_message"],
        spawned_page_id=row["spawned_page_id"],
        title=title,
        metadata=metadata,
        state=row["state"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_message(row: DbRow) -> ChatMessage:
    return ChatMessage(
        id=row["id"],
        turn_id=row["turn_id"],
        role=row["role"],
        content=row["content"],
        tool_call_id=row["tool_call_id"] if "tool_call_id" in row.keys() else None,
        tool_name=row["tool_name"],
        tool_args=loads_json(row["tool_args"]),
        tool_result=loads_json(row["tool_result"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_chat_artifact(row: DbRow) -> ChatArtifact:
    return ChatArtifact(
        id=row["id"],
        session_id=row["session_id"],
        turn_id=row["turn_id"],
        message_id=row["message_id"],
        artifact_type=row["artifact_type"],
        title=row["title"],
        inline_payload=loads_json(row["inline_payload"]) if row["inline_payload"] else None,
        storage_backend=row["storage_backend"],
        object_key=row["object_key"],
        size_bytes=row["size_bytes"],
        mime_type=row["mime_type"],
        order_index=row["order_index"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
