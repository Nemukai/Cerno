"""Operator CLI for managing access.

Run with `uv run cerno-admin <command>` once installed.

Commands:
  approve-email  Add an email to the approved access list
  remove-email   Remove an email from the approved access list
  list-emails    List approved access emails
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
from cerno.db import DbConnection, connect
from cerno.repositories import ApprovedEmailRepository, BetaCodeRepository, UserRepository
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
            UserRepository(conn).sync_allowlist_status(user.id, email_approved=True)
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
            repo.sync_allowlist_status(user.id, email_approved=True)
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
