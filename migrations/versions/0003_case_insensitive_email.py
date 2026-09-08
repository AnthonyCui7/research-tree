"""One account per email address, whatever case it was typed in.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-07

fastapi-users looks an account up by `lower(email)`, but the unique index was
on `email` as written, so `Alice@x.com` and `alice@x.com` were two rows to the
database and one account to the application. Two registrations racing each
other could both pass the library's own "does this email exist" check and both
insert, after which a sign-in matched whichever row Postgres happened to return
and the reader found their workspaces gone.

The index now covers `lower(email)`, which is the identity the code already
uses. If a database already holds two accounts whose addresses differ only in
case, creating it fails and says which rows: merge or remove one of them and
run the migration again. Refusing is the point - uniqueness cannot be declared
over data that already breaks it.
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ix_user_email')
    op.execute('CREATE UNIQUE INDEX ix_user_email ON "user" (lower(email))')


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ix_user_email')
    op.execute('CREATE UNIQUE INDEX ix_user_email ON "user" (email)')
