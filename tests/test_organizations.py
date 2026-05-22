from __future__ import annotations

import sqlite3
import unittest

from cerno.repositories import (
    LLMUsageRepository,
    OrganizationRepository,
    SessionRepository,
    UserRepository,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            google_sub TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            name TEXT,
            picture TEXT,
            access_status TEXT NOT NULL DEFAULT 'pending',
            access_granted_at TEXT,
            access_code_used TEXT,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE organizations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE organization_members (
            organization_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (organization_id, user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE organization_entitlements (
            organization_id TEXT PRIMARY KEY,
            plan_name TEXT NOT NULL DEFAULT 'manual',
            contract_status TEXT NOT NULL DEFAULT 'trial',
            seat_limit INTEGER NOT NULL DEFAULT 1,
            monthly_token_limit INTEGER NOT NULL DEFAULT 6000000,
            storage_quota_bytes INTEGER NOT NULL DEFAULT 5368709120,
            monthly_upload_bytes INTEGER,
            max_file_size_bytes INTEGER,
            max_workspaces INTEGER,
            soft_limit_percent INTEGER NOT NULL DEFAULT 100,
            hard_limit_percent INTEGER NOT NULL DEFAULT 120,
            feature_flags TEXT NOT NULL DEFAULT '{}',
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            organization_id TEXT,
            user_id TEXT,
            created_by_user_id TEXT,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            discovery_status TEXT NOT NULL DEFAULT 'empty',
            overview TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE llm_usage (
            organization_id TEXT,
            user_id TEXT NOT NULL,
            day TEXT NOT NULL,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE llm_usage_by_model (
            organization_id TEXT,
            user_id TEXT NOT NULL,
            day TEXT NOT NULL,
            model TEXT NOT NULL,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day, model)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE usage_events (
            id TEXT PRIMARY KEY,
            organization_id TEXT,
            user_id TEXT,
            session_id TEXT,
            event_type TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            model TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            occurred_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE organization_usage_daily (
            organization_id TEXT NOT NULL,
            day TEXT NOT NULL,
            metric TEXT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (organization_id, day, metric)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE organization_usage_daily_by_model (
            organization_id TEXT NOT NULL,
            day TEXT NOT NULL,
            metric TEXT NOT NULL,
            model TEXT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (organization_id, day, metric, model)
        )
        """
    )
    return conn


class OrganizationRepositoryTests(unittest.TestCase):
    def test_default_org_and_private_sessions_inside_same_org(self) -> None:
        conn = _make_conn()
        try:
            users = UserRepository(conn)
            owner = users.upsert_from_google(
                google_sub="owner-google",
                email="owner@example.com",
                name="Owner",
                picture=None,
                email_approved=True,
            )
            member = users.upsert_from_google(
                google_sub="member-google",
                email="member@example.com",
                name="Member",
                picture=None,
                email_approved=True,
            )
            orgs = OrganizationRepository(conn)
            org = orgs.ensure_personal_for_user(owner)
            orgs.add_member(organization_id=org.id, user_id=member.id, role="member")

            sessions = SessionRepository(conn)
            owner_session = sessions.create(
                "Owner session",
                user_id=owner.id,
                organization_id=org.id,
            )
            sessions.create("Member session", user_id=member.id, organization_id=org.id)

            self.assertEqual([s.id for s in sessions.list(owner.id, organization_id=org.id)], [owner_session.id])
            self.assertIsNone(
                sessions.get(
                    owner_session.id,
                    user_id=member.id,
                    organization_id=org.id,
                )
            )
        finally:
            conn.close()

    def test_llm_usage_records_org_daily_rollups(self) -> None:
        conn = _make_conn()
        try:
            repo = LLMUsageRepository(conn)

            repo.add_tokens(
                "user_1",
                "2026-05-23",
                100,
                model="gpt-5.4-mini",
                organization_id="org_1",
            )
            repo.add_tokens(
                "user_1",
                "2026-05-23",
                25,
                model="gpt-5.4-mini",
                organization_id="org_1",
            )

            daily = conn.execute(
                "SELECT amount FROM organization_usage_daily WHERE organization_id = ?",
                ("org_1",),
            ).fetchone()
            by_model = conn.execute(
                "SELECT amount FROM organization_usage_daily_by_model WHERE organization_id = ?",
                ("org_1",),
            ).fetchone()
            events = conn.execute("SELECT COUNT(*) AS count FROM usage_events").fetchone()
            self.assertEqual(daily["amount"], 125)
            self.assertEqual(by_model["amount"], 125)
            self.assertEqual(events["count"], 2)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
