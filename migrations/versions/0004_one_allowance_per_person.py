"""One allowance per person, that grants add to.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-07

Every grant used to insert a row, and only the oldest was ever read. Topping
somebody up therefore did nothing: the new row sat behind the spent one while
the account went on being told it had no credit left.

An allowance is one number now. Granting adds to it, a negative amount takes
away from it, and spending draws it down. Existing rows for one address are
folded into a single row that carries the totals, and a partial unique index
keeps it that way.
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


FOLD = """
UPDATE allowances a
SET limit_usd = totals.limit_usd,
    spent_usd = totals.spent_usd
FROM (
    SELECT email,
           min(created_at) AS first_granted,
           sum(limit_usd) AS limit_usd,
           sum(spent_usd) AS spent_usd
    FROM allowances
    WHERE status = 'active'
    GROUP BY email
) AS totals
WHERE a.email = totals.email
  AND a.status = 'active'
  AND a.created_at = totals.first_granted;

UPDATE allowances a
SET status = 'folded'
WHERE a.status = 'active'
  AND a.created_at > (
      SELECT min(b.created_at) FROM allowances b
      WHERE b.email = a.email AND b.status = 'active'
  );
"""


def upgrade() -> None:
    for statement in FOLD.split(";"):
        if statement.strip():
            op.execute(statement.strip())
    op.execute(
        "CREATE UNIQUE INDEX ux_allowances_active_email ON allowances (email) "
        "WHERE status = 'active'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_allowances_active_email")
    op.execute("UPDATE allowances SET status = 'active' WHERE status = 'folded'")
