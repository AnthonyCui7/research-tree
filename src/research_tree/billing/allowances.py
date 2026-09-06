"""Operator-granted spend on the platform key.

An allowance is granted to an email and attaches to the account with that
email once the account is verified (Google sign-ins are; password accounts
are verified by the operator through `research-tree-users --verify` after
checking out of band), so nobody can claim a friend's allowance by
registering their address first. `one_off` allowances spend down once;
`monthly` ones reset a month after their period start. Money only moves in
`charge_allowance`, called by the metering hook after each sponsored call.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from research_tree.db import get_engine

PERIODS = ("one_off", "monthly")
_ROW_COLUMNS = "id, email, user_id, limit_usd, spent_usd, period, period_start, expires_at"


def link_by_email(conn: Connection, user_id: str, email: str) -> None:
    conn.execute(
        text(
            "UPDATE allowances SET user_id = CAST(:user_id AS uuid) "
            "WHERE user_id IS NULL AND email = lower(:email) AND status = 'active'"
        ),
        {"user_id": user_id, "email": email.strip()},
    )


def _active_row(conn: Connection, user_id: str, *, lock: bool = False) -> Any:
    query = (
        f"SELECT {_ROW_COLUMNS} FROM allowances "
        "WHERE user_id = CAST(:user_id AS uuid) AND status = 'active' "
        "AND (expires_at IS NULL OR expires_at > now()) "
        "ORDER BY created_at LIMIT 1"
    )
    if lock:
        query += " FOR UPDATE"
    return conn.execute(text(query), {"user_id": user_id}).mappings().first()


def _rolled_over(conn: Connection, row: Any) -> Any:
    if row["period"] != "monthly":
        return row
    result = conn.execute(
        text(
            "UPDATE allowances SET spent_usd = 0, period_start = now() "
            "WHERE id = :id AND period = 'monthly' AND period_start + interval '1 month' <= now() "
            f"RETURNING {_ROW_COLUMNS}"
        ),
        {"id": row["id"]},
    ).mappings().first()
    return result if result is not None else row


def _summary(row: Any) -> dict[str, Any]:
    limit = Decimal(row["limit_usd"])
    spent = Decimal(row["spent_usd"])
    return {
        "id": str(row["id"]),
        "email": str(row["email"]),
        "limit_usd": float(limit),
        "spent_usd": float(spent),
        "remaining_usd": float(max(limit - spent, Decimal(0))),
        "period": str(row["period"]),
        "period_start": _iso(row["period_start"]),
        "expires_at": _iso(row["expires_at"]),
        "exhausted": spent >= limit,
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def allowance_summary(user_id: str, email: str, *, verified: bool) -> dict[str, Any] | None:
    """The account's active allowance for the account page, or None."""

    with get_engine().begin() as conn:
        if verified and email:
            link_by_email(conn, user_id, email)
        row = _active_row(conn, user_id)
        if row is None:
            return None
        return _summary(_rolled_over(conn, row))


def allowance_status(user_id: str, email: str, *, verified: bool) -> str:
    """`ok`, `exhausted`, or `none`."""

    summary = allowance_summary(user_id, email, verified=verified)
    if summary is None:
        return "none"
    return "exhausted" if summary["exhausted"] else "ok"


def charge_allowance(conn: Connection, user_id: str, cost_usd: Decimal) -> bool:
    """Add one call's cost to the active allowance inside the caller's transaction."""

    row = _active_row(conn, user_id, lock=True)
    if row is None:
        return False
    conn.execute(
        text("UPDATE allowances SET spent_usd = spent_usd + :cost WHERE id = :id"),
        {"cost": cost_usd, "id": row["id"]},
    )
    return True


def grant_allowance(
    *,
    email: str,
    limit_usd: Decimal,
    period: str = "one_off",
    expires_at: datetime | None = None,
    granted_by: str | None = None,
    note: str | None = None,
) -> str:
    if period not in PERIODS:
        raise ValueError(f"period must be one of {', '.join(PERIODS)}")
    if limit_usd <= 0:
        raise ValueError("the allowance must be a positive amount")
    allowance_id = str(uuid.uuid4())
    with get_engine().begin() as conn:
        # Attach immediately when a verified account already has this email.
        user = conn.execute(
            text('SELECT id FROM "user" WHERE lower(email) = lower(:email) AND is_verified'),
            {"email": email.strip()},
        ).first()
        conn.execute(
            text(
                "INSERT INTO allowances "
                "(id, email, user_id, limit_usd, period, expires_at, granted_by, note) VALUES "
                "(CAST(:id AS uuid), lower(:email), :user_id, :limit_usd, :period, :expires_at, "
                ":granted_by, :note)"
            ),
            {
                "id": allowance_id,
                "email": email.strip(),
                "user_id": user[0] if user is not None else None,
                "limit_usd": limit_usd,
                "period": period,
                "expires_at": expires_at,
                "granted_by": granted_by,
                "note": note,
            },
        )
    return allowance_id


def list_allowances() -> list[dict[str, Any]]:
    with get_engine().begin() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT a.id, a.email, a.user_id, a.limit_usd, a.spent_usd, a.period, "
                    "a.period_start, a.expires_at, a.status, a.granted_by, a.note, a.created_at, "
                    "u.is_verified FROM allowances a "
                    'LEFT JOIN "user" u ON u.id = a.user_id ORDER BY a.created_at'
                )
            )
            .mappings()
            .all()
        )
    return [
        {
            **_summary(row),
            "status": str(row["status"]),
            "granted_by": row["granted_by"],
            "note": row["note"],
            "created_at": _iso(row["created_at"]),
            "linked": row["user_id"] is not None,
            "verified": bool(row["is_verified"]) if row["is_verified"] is not None else None,
        }
        for row in rows
    ]


def revoke_allowance(allowance_id: str) -> bool:
    try:
        parsed = uuid.UUID(allowance_id)
    except ValueError:
        return False
    with get_engine().begin() as conn:
        result = conn.execute(
            text(
                "UPDATE allowances SET status = 'revoked' "
                "WHERE id = CAST(:id AS uuid) AND status = 'active'"
            ),
            {"id": str(parsed)},
        )
    return result.rowcount > 0
