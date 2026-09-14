"""Synchronous reads and admin edits of the account table.

The request path goes through fastapi-users; this is for everything else:
background work binding a run's owner, and the operator's CLI.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from research_tree.auth.settings import SESSION_LIFETIME_SECONDS, admin_emails
from research_tree.db import get_engine
from research_tree.principal import Principal

_USER_COLUMNS = (
    "id, email, name, avatar_url, is_active, is_superuser, is_verified, created_at, last_seen_at"
)


def principal_from_row(row: Any) -> Principal:
    email = str(row["email"])
    return Principal(
        user_id=str(row["id"]),
        email=email,
        name=row["name"],
        avatar_url=row["avatar_url"],
        is_admin=bool(row["is_superuser"]) or email.casefold() in admin_emails(),
        is_verified=bool(row["is_verified"]),
        is_local=False,
    )


def principal_for_user_id(user_id: str) -> Principal | None:
    try:
        parsed = uuid.UUID(user_id)
    except ValueError:
        return None
    with get_engine().begin() as conn:
        row = (
            conn.execute(
                text(f'SELECT {_USER_COLUMNS} FROM "user" WHERE id = :id AND is_active'),
                {"id": str(parsed)},
            )
            .mappings()
            .first()
        )
    return principal_from_row(row) if row is not None else None


def session_is_live(user_id: str, token: str | None) -> bool:
    """Whether the session cookie's token still signs this account in.

    An open event stream was authorised once, when it connected, and holds
    the token it connected with. Signing out everywhere, deactivating the
    account, and the takeover in `manager._claim_unproven_account` all delete
    the row; the stream asks here on every pass, so it ends with the session.
    """

    if not token:
        return False
    try:
        parsed = uuid.UUID(user_id)
    except ValueError:
        return False
    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM accesstoken WHERE token = :token "
                "AND user_id = CAST(:user_id AS uuid) "
                "AND created_at >= now() - make_interval(secs => :lifetime)"
            ),
            {"token": token, "user_id": str(parsed), "lifetime": SESSION_LIFETIME_SECONDS},
        ).first()
    return row is not None


def user_id_for_email(email: str) -> str | None:
    with get_engine().begin() as conn:
        row = conn.execute(
            text('SELECT id FROM "user" WHERE lower(email) = lower(:email)'),
            {"email": email.strip()},
        ).first()
    return str(row[0]) if row is not None else None


def list_users() -> list[dict[str, Any]]:
    with get_engine().begin() as conn:
        rows = (
            conn.execute(text(f'SELECT {_USER_COLUMNS} FROM "user" ORDER BY created_at'))
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def set_user_flags(user_id: str, **flags: bool) -> bool:
    allowed = {"is_active", "is_superuser", "is_verified"}
    unknown = set(flags) - allowed
    if unknown:
        raise ValueError(f"unknown user flags: {sorted(unknown)}")
    if not flags:
        return False
    assignments = ", ".join(f"{name} = :{name}" for name in flags)
    with get_engine().begin() as conn:
        result = conn.execute(
            text(f'UPDATE "user" SET {assignments} WHERE id = CAST(:id AS uuid)'),
            {"id": user_id, **flags},
        )
        if not flags.get("is_active", True):
            conn.execute(
                text("DELETE FROM accesstoken WHERE user_id = CAST(:id AS uuid)"), {"id": user_id}
            )
    return result.rowcount > 0


def revoke_sessions(user_id: str) -> int:
    """Sign this account out everywhere. Returns how many sessions ended."""

    with get_engine().begin() as conn:
        result = conn.execute(
            text("DELETE FROM accesstoken WHERE user_id = CAST(:user_id AS uuid)"),
            {"user_id": user_id},
        )
    return int(result.rowcount or 0)
