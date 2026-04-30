"""Operator CLI for managing beta access.

Run with `uv run cerno-admin <command>` once installed.

Commands:
  create-code    Generate (or set) a beta access code
  list-codes     List all beta codes with usage counts
  delete-code    Delete a code so no one else can redeem it
  list-users     List signed-in users with their access status
  grant          Grant a user access without a code (by email)
  revoke         Revoke a user's access (by email)
  unrevoke       Move a revoked user back to pending
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from datetime import UTC, datetime, timedelta

from cerno.config import get_settings
from cerno.db import connect
from cerno.repositories import BetaCodeRepository, UserRepository

# Avoid look-alike characters: no I, O, 0, 1.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([dwmh])\s*$", re.IGNORECASE)


def _generate_code(prefix: str = "CERNO", segments: int = 2, segment_len: int = 4) -> str:
    parts = [prefix] + [
        "".join(secrets.choice(_ALPHABET) for _ in range(segment_len))
        for _ in range(segments)
    ]
    return "-".join(parts)


def _parse_duration(spec: str) -> datetime:
    match = _DURATION_RE.match(spec)
    if not match:
        raise SystemExit(
            f"invalid duration {spec!r}; use forms like 30d, 12h, 4w"
        )
    n = int(match.group(1))
    unit = match.group(2).lower()
    delta = {
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "w": timedelta(weeks=n),
        "m": timedelta(days=30 * n),
    }[unit]
    return datetime.now(UTC) + delta


def _open_conn():
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
        print(
            f"{u.email:<40}  {u.access_status:<10}  {code:<22}  {u.created_at.isoformat()}"
        )


def cmd_grant(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
        repo = UserRepository(conn)
        user = repo.get_by_email(args.email)
        if user is None:
            sys.exit(f"no user with email: {args.email}")
        repo.mark_granted(user.id, "ADMIN")
        conn.commit()
    finally:
        conn.close()
    print(f"granted access to {args.email}")


def cmd_revoke(args: argparse.Namespace) -> None:
    conn = _open_conn()
    try:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cerno-admin")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-code", help="generate (or set) a beta code")
    p.add_argument("--note", help="who is this for / why")
    p.add_argument("--max-uses", type=int, default=1, help="default 1")
    p.add_argument(
        "--expires-in", help="duration like 30d, 12h, 4w (default: never)"
    )
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

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
