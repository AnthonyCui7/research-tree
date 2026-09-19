"""A removed key keeps its row and loses what could be opened.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-18

Removing or replacing a saved key marked its row revoked and left the sealed
key beside it. The application now overwrites the sealed columns when it
retires a row; this does the same for the rows retired before it did. The row
itself stays: which key an account had, and when, is its history.
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE user_api_keys SET ciphertext = ''::bytea, nonce = ''::bytea, "
        "wrapped_dek = ''::bytea WHERE revoked_at IS NOT NULL"
    )


def downgrade() -> None:
    # What was overwritten cannot be put back, and nothing reads it.
    pass
