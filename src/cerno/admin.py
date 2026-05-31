"""Operator CLI for managing access.

Run with `uv run cerno-admin <command>` once installed.

Commands:
  approve-email  Add an email to the approved access list
  remove-email   Remove an email from the approved access list
  list-emails    List approved access emails
  list-usage     List daily LLM token usage, including model breakdown
  list-users     List signed-in users with their access status
  grant          Grant a user access without a code (by email)
  revoke         Revoke a user's access (by email)
  unrevoke       Move a revoked user back to pending
  storage-gc     Delete unreferenced R2 objects for one or all users
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from datetime import UTC, datetime, timedelta

from cerno.config import get_settings
from cerno.db import DbConnection, DbRow, connect
from cerno.models import Organization
from cerno.repositories import (
    ApprovedEmailRepository,
    BetaCodeRepository,
    OrganizationRepository,
    UserRepository,
    new_id,
)
from cerno.services.storage_gc import cleanup_unused_storage_for_user

# Avoid look-alike characters: no I, O, 0, 1.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([dwmh])\s*$", re.IGNORECASE)


def _generate_code(prefix: str = "CERNO", segments: int = 2, segment_len: int = 4) -> str:
    parts = [prefix] + [
        "".join(secrets.choice(_ALPHABET) for _ in range(segment_len)) for _ in range(segments)
    ]
    return "-".join(parts)


def _parse_duration(spec: str) -> datetime:
    match = _DURATION_RE.match(spec)
    if not match:
        raise SystemExit(f"invalid duration {spec!r}; use forms like 30d, 12h, 4w")
    n = int(match.group(1))
    unit = match.group(2).lower()
    delta = {
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "w": timedelta(weeks=n),
        "m": timedelta(days=30 * n),
    }[unit]
    return datetime.now(UTC) + delta


def _open_conn() -> DbConnection:
    settings = get_settings()
    return connect(settings)


def cmd_create_code(args: argparse.Namespace) -> None:
    code = (args.code or _generate_code()).strip().upper()
    expires_at = _parse_duration(args.expires_in) if args.expires_in else None
    conn = _open_conn()
    try:
        bc = BetaCodeRepository(conn).create(
            code=code,
            note=args.note,
            max_uses=args.max_uses,
            expires_at=expires_at,
        )
        conn.commit()
    finally:
        conn.close()
    expires_str = bc.expires_at.isoformat() if bc.expires_at else "never"
    print(f"created  {bc.code}  uses=0/{bc.max_uses}  expires={expires_str}")
    if bc.note:
        print(f"  note: {bc.note}")


def cmd_list_codes(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
        codes = BetaCodeRepository(conn).list_all()
    finally:
        conn.close()
    if not codes:
        print("(no codes)")
        return
    print(f"{'code':<22}  {'uses':<8}  {'expires':<25}  note")
    print("-" * 80)
    for c in codes:
        uses = f"{c.uses_count}/{c.max_uses}"
        expires = c.expires_at.isoformat() if c.expires_at else "never"
        note = c.note or ""
        print(f"{c.code:<22}  {uses:<8}  {expires:<25}  {note}")


def cmd_delete_code(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
        deleted = BetaCodeRepository(conn).delete(args.code)
        conn.commit()
    finally:
        conn.close()
    if deleted:
        print(f"deleted {args.code.upper()}")
    else:
        sys.exit(f"no such code: {args.code}")


def cmd_list_users(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
        users = UserRepository(conn).list_all()
    finally:
        conn.close()
    if not users:
        print("(no users)")
        return
    print(f"{'email':<40}  {'status':<10}  {'code':<22}  joined")
    print("-" * 100)
    for u in users:
        code = u.access_code_used or ""
        print(f"{u.email:<40}  {u.access_status:<10}  {code:<22}  {u.created_at.isoformat()}")


def cmd_approve_email(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
        approved = ApprovedEmailRepository(conn).add(args.email, args.note)
        user = UserRepository(conn).get_by_email(approved.email)
        if user is not None and user.access_status != "revoked":
            user = UserRepository(conn).sync_allowlist_status(user.id, email_approved=True)
            if user is not None:
                OrganizationRepository(conn).ensure_personal_for_user(user)
        conn.commit()
    finally:
        conn.close()
    print(f"approved {approved.email}")


def cmd_remove_email(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
        repo = ApprovedEmailRepository(conn)
        email = repo.normalize(args.email)
        removed = repo.remove(email)
        user = UserRepository(conn).get_by_email(email)
        if user is not None:
            UserRepository(conn).sync_allowlist_status(user.id, email_approved=False)
        conn.commit()
    finally:
        conn.close()
    if removed:
        print(f"removed {email}")
    else:
        sys.exit(f"no approved email: {email}")


def cmd_list_emails(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
        emails = ApprovedEmailRepository(conn).list_all()
    finally:
        conn.close()
    if not emails:
        print("(no approved emails)")
        return
    print(f"{'email':<40}  {'created':<25}  note")
    print("-" * 90)
    for item in emails:
        print(f"{item.email:<40}  {item.created_at.isoformat():<25}  {item.note or ''}")


def cmd_grant(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
        ApprovedEmailRepository(conn).add(args.email, "admin grant")
        repo = UserRepository(conn)
        user = repo.get_by_email(args.email)
        if user is not None:
            user = repo.sync_allowlist_status(user.id, email_approved=True)
            if user is not None:
                OrganizationRepository(conn).ensure_personal_for_user(user)
        conn.commit()
    finally:
        conn.close()
    print(f"granted access to {args.email}")


def cmd_revoke(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
        ApprovedEmailRepository(conn).remove(args.email)
        repo = UserRepository(conn)
        user = repo.get_by_email(args.email)
        if user is None:
            sys.exit(f"no user with email: {args.email}")
        repo.set_access_status(user.id, "revoked")
        conn.commit()
    finally:
        conn.close()
    print(f"revoked access for {args.email}")


def cmd_unrevoke(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
        repo = UserRepository(conn)
        user = repo.get_by_email(args.email)
        if user is None:
            sys.exit(f"no user with email: {args.email}")
        repo.set_access_status(user.id, "pending")
        conn.commit()
    finally:
        conn.close()
    print(f"moved {args.email} back to pending")


def cmd_storage_gc(args: argparse.Namespace) -> None:
    settings = get_settings()
    conn = connect(settings)
    try:
        if args.email:
            user = UserRepository(conn).get_by_email(args.email)
            if user is None:
                sys.exit(f"no user with email: {args.email}")
            users = [user]
        else:
            users = UserRepository(conn).list_all()
        if not users:
            print("(no users)")
            return
        for user in users:
            result = cleanup_unused_storage_for_user(conn, settings, user.id)
            print(
                f"{user.email}: scanned={result.scanned_keys} "
                f"deleted={len(result.deleted_keys)} failed={len(result.failed_keys)} "
                f"pruned_source_assets={len(result.pruned_source_asset_ids)}"
            )
        conn.commit()
    finally:
        conn.close()


def cmd_list_usage(args: argparse.Namespace) -> None:
    clauses: list[str] = []
    params: list[str] = []
    if args.email:
        clauses.append("lower(u.email) = lower(?)")
        params.append(args.email)
    if args.day:
        clauses.append("l.day = ?")
        params.append(args.day)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = _open_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT
                u.email,
                l.day,
                l.tokens_used AS total_tokens,
                m.model,
                m.tokens_used AS model_tokens
            FROM llm_usage l
            JOIN users u ON u.id = l.user_id
            LEFT JOIN llm_usage_by_model m
              ON m.user_id = l.user_id AND m.day = l.day
            {where}
            ORDER BY l.day DESC, u.email, m.tokens_used DESC NULLS LAST, m.model
            """,
            tuple(params),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        print("(no usage)")
        return
    print(f"{'email':<40}  {'day':<10}  {'total':>10}  {'model':<24}  model_tokens")
    print("-" * 104)
    for row in rows:
        model = row["model"] or "(unattributed)"
        model_tokens = row["model_tokens"] if row["model_tokens"] is not None else row["total_tokens"]
        print(
            f"{row['email']:<40}  {row['day']:<10}  "
            f"{row['total_tokens']:>10}  {model:<24}  {model_tokens}"
        )


def cmd_list_orgs(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                o.id,
                o.name,
                o.slug,
                o.status,
                COUNT(m.user_id) FILTER (WHERE m.status = 'active') AS active_members
            FROM organizations o
            LEFT JOIN organization_members m ON m.organization_id = o.id
            GROUP BY o.id, o.name, o.slug, o.status
            ORDER BY o.created_at DESC
            """
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        print("(no organizations)")
        return
    print(f"{'id':<38}  {'slug':<28}  {'members':>7}  {'status':<10}  name")
    print("-" * 110)
    for row in rows:
        print(
            f"{row['id']:<38}  {row['slug']:<28}  "
            f"{row['active_members']:>7}  {row['status']:<10}  {row['name']}"
        )


def cmd_create_org(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
        org = OrganizationRepository(conn).create(name=args.name, slug=args.slug)
        OrganizationRepository(conn).ensure_entitlements(org.id)
        conn.commit()
    finally:
        conn.close()
    print(f"created organization {org.name} ({org.id})")


def cmd_add_org_member(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
        user = UserRepository(conn).get_by_email(args.email)
        if user is None:
            sys.exit(f"no user with email: {args.email}")
        org_repo = OrganizationRepository(conn)
        org = org_repo.get(args.organization_id)
        if org is None:
            sys.exit(f"no organization with id: {args.organization_id}")
        org_repo.add_member(
            organization_id=args.organization_id,
            user_id=user.id,
            role=args.role,
        )
        conn.commit()
    finally:
        conn.close()
    print(f"added {args.email} to {args.organization_id} as {args.role}")


def _org_slug(name: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
    return re.sub(r"-+", "-", slug) or f"org-{new_id()[:8]}"


def _get_org_by_slug(conn: DbConnection, slug: str) -> DbRow | None:
    row = conn.execute("SELECT * FROM organizations WHERE slug = ?", (slug,)).fetchone()
    return row


def _move_org_rollups(conn: DbConnection, *, source_org_id: str, target_org_id: str) -> None:
    rows = conn.execute(
        """SELECT day, metric, amount
           FROM organization_usage_daily
           WHERE organization_id = ?""",
        (source_org_id,),
    ).fetchall()
    for row in rows:
        conn.execute(
            """INSERT INTO organization_usage_daily (organization_id, day, metric, amount)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(organization_id, day, metric) DO UPDATE
               SET amount = organization_usage_daily.amount + excluded.amount""",
            (target_org_id, row["day"], row["metric"], row["amount"]),
        )
    rows = conn.execute(
        """SELECT day, metric, model, amount
           FROM organization_usage_daily_by_model
           WHERE organization_id = ?""",
        (source_org_id,),
    ).fetchall()
    for row in rows:
        conn.execute(
            """INSERT INTO organization_usage_daily_by_model
               (organization_id, day, metric, model, amount)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(organization_id, day, metric, model) DO UPDATE
               SET amount = organization_usage_daily_by_model.amount + excluded.amount""",
            (target_org_id, row["day"], row["metric"], row["model"], row["amount"]),
        )


def _reassign_user_data_to_org(conn: DbConnection, *, user_id: str, target_org_id: str) -> None:
    statements = [
        ("UPDATE sessions SET organization_id = ? WHERE COALESCE(created_by_user_id, user_id) = ?", (target_org_id, user_id)),
        ("UPDATE source_assets SET organization_id = ? WHERE user_id = ?", (target_org_id, user_id)),
        ("UPDATE upload_intents SET organization_id = ? WHERE user_id = ?", (target_org_id, user_id)),
        ("UPDATE asset_artifacts SET organization_id = ? WHERE user_id = ?", (target_org_id, user_id)),
        ("UPDATE processing_jobs SET organization_id = ? WHERE user_id = ?", (target_org_id, user_id)),
        ("UPDATE llm_usage SET organization_id = ? WHERE user_id = ?", (target_org_id, user_id)),
        ("UPDATE llm_usage_by_model SET organization_id = ? WHERE user_id = ?", (target_org_id, user_id)),
        ("UPDATE usage_events SET organization_id = ? WHERE user_id = ?", (target_org_id, user_id)),
        ("UPDATE product_events SET organization_id = ? WHERE user_id = ?", (target_org_id, user_id)),
        ("UPDATE llm_call_events SET organization_id = ? WHERE user_id = ?", (target_org_id, user_id)),
    ]
    for sql, params in statements:
        conn.execute(sql, params)


def cmd_move_users_to_org(args: argparse.Namespace) -> None:
    emails = [ApprovedEmailRepository.normalize(email) for email in args.email]
    if not emails:
        sys.exit("provide at least one email")
    slug = args.slug or _org_slug(args.org_name)
    conn = _open_conn()
    try:
        org_repo = OrganizationRepository(conn)
        row = _get_org_by_slug(conn, slug)
        target: Organization
        if row is None:
            target = org_repo.create(name=args.org_name, slug=slug)
            org_repo.ensure_entitlements(target.id)
            print(f"created organization {target.name} ({target.id})")
        else:
            existing = org_repo.get(row["id"])
            if existing is None:
                sys.exit(f"organization disappeared: {row['id']}")
            target = existing
            print(f"using organization {target.name} ({target.id})")

        users = []
        for email in emails:
            user = UserRepository(conn).get_by_email(email)
            if user is None:
                sys.exit(f"no user with email: {email}")
            if user.access_status != "granted" and not args.include_pending:
                sys.exit(f"{email} is {user.access_status}; pass --include-pending to move it")
            users.append(user)

        entitlements = org_repo.ensure_entitlements(target.id)
        org_repo.update_entitlements(
            target.id,
            contract_status=args.contract_status,
            seat_limit=max(entitlements.seat_limit, len(users)),
        )

        moved: list[str] = []
        deleted_orgs: list[str] = []
        for user in users:
            old_memberships = conn.execute(
                """SELECT organization_id
                   FROM organization_members
                   WHERE user_id = ? AND organization_id <> ?""",
                (user.id, target.id),
            ).fetchall()

            org_repo.add_member(
                organization_id=target.id,
                user_id=user.id,
                role=args.role,
                status="active",
            )
            _reassign_user_data_to_org(conn, user_id=user.id, target_org_id=target.id)

            for membership in old_memberships:
                old_org_id = membership["organization_id"]
                count_row = conn.execute(
                    """SELECT COUNT(*) AS count
                       FROM organization_members
                       WHERE organization_id = ?
                         AND user_id <> ?
                         AND status = 'active'""",
                    (old_org_id, user.id),
                ).fetchone()
                member_count = int(count_row["count"] or 0) if count_row else 0
                if member_count:
                    conn.execute(
                        """UPDATE organization_members
                           SET status = 'revoked', updated_at = ?
                           WHERE organization_id = ? AND user_id = ?""",
                        (datetime.now(UTC).isoformat(), old_org_id, user.id),
                    )
                    continue
                _move_org_rollups(conn, source_org_id=old_org_id, target_org_id=target.id)
                conn.execute(
                    "DELETE FROM organization_members WHERE organization_id = ? AND user_id = ?",
                    (old_org_id, user.id),
                )
                if args.cleanup_empty_orgs:
                    conn.execute("DELETE FROM organizations WHERE id = ?", (old_org_id,))
                    deleted_orgs.append(old_org_id)

            moved.append(user.email)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"moved {len(moved)} user(s) to {args.org_name}: {', '.join(moved)}")
    if deleted_orgs:
        print(f"deleted empty old organization(s): {', '.join(deleted_orgs)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cerno-admin")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("approve-email", help="add an email to the approved access list")
    p.add_argument("email")
    p.add_argument("--note", help="who is this / why")
    p.set_defaults(func=cmd_approve_email)

    p = sub.add_parser("remove-email", help="remove an email from the approved access list")
    p.add_argument("email")
    p.set_defaults(func=cmd_remove_email)

    p = sub.add_parser("list-emails", help="list approved access emails")
    p.set_defaults(func=cmd_list_emails)

    p = sub.add_parser("create-code", help="generate (or set) a beta code")
    p.add_argument("--note", help="who is this for / why")
    p.add_argument("--max-uses", type=int, default=1, help="default 1")
    p.add_argument("--expires-in", help="duration like 30d, 12h, 4w (default: never)")
    p.add_argument("--code", help="set an explicit code instead of generating one")
    p.set_defaults(func=cmd_create_code)

    p = sub.add_parser("list-codes", help="list all beta codes")
    p.set_defaults(func=cmd_list_codes)

    p = sub.add_parser("delete-code", help="delete a beta code")
    p.add_argument("code")
    p.set_defaults(func=cmd_delete_code)

    p = sub.add_parser("list-users", help="list users + access status")
    p.set_defaults(func=cmd_list_users)

    p = sub.add_parser("list-usage", help="list daily LLM token usage")
    p.add_argument("--email", help="limit to one user email")
    p.add_argument("--day", help="limit to a UTC day like 2026-05-18")
    p.set_defaults(func=cmd_list_usage)

    p = sub.add_parser("list-orgs", help="list organizations")
    p.set_defaults(func=cmd_list_orgs)

    p = sub.add_parser("create-org", help="create a manually managed organization")
    p.add_argument("name")
    p.add_argument("--slug", required=True)
    p.set_defaults(func=cmd_create_org)

    p = sub.add_parser("add-org-member", help="add an existing user to an organization")
    p.add_argument("organization_id")
    p.add_argument("email")
    p.add_argument("--role", choices=["admin", "member", "viewer"], default="member")
    p.set_defaults(func=cmd_add_org_member)

    p = sub.add_parser("move-users-to-org", help="move existing users and their data to one organization")
    p.add_argument("org_name")
    p.add_argument("email", nargs="+")
    p.add_argument("--slug", help="organization slug; defaults to slugified name")
    p.add_argument("--role", choices=["admin", "member", "viewer"], default="member")
    p.add_argument(
        "--contract-status",
        choices=["trial", "active", "paused", "suspended", "archived"],
        default="active",
    )
    p.add_argument("--include-pending", action="store_true", help="allow moving pending users")
    p.add_argument(
        "--cleanup-empty-orgs",
        action="store_true",
        help="delete old organizations that have no other active members after the move",
    )
    p.set_defaults(func=cmd_move_users_to_org)

    p = sub.add_parser("grant", help="grant a user access without a code")
    p.add_argument("email")
    p.set_defaults(func=cmd_grant)

    p = sub.add_parser("revoke", help="revoke a user's access")
    p.add_argument("email")
    p.set_defaults(func=cmd_revoke)

    p = sub.add_parser("unrevoke", help="move a revoked user back to pending")
    p.add_argument("email")
    p.set_defaults(func=cmd_unrevoke)

    p = sub.add_parser("storage-gc", help="delete unreferenced R2 objects")
    p.add_argument("--email", help="limit cleanup to one user email")
    p.set_defaults(func=cmd_storage_gc)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
