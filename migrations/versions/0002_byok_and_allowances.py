"""Bring-your-own keys, usage metering, sponsored allowances.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-06

Hand-written DDL, like 0001. A stored key is a sealed blob plus the id of the
key-encryption key that wrapped its data key; the last four characters are
kept in the clear for the account page. Usage rows are never updated, so the
account page's totals are a sum over them. Allowances hang off an email and
attach to the verified account that owns it.
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


UPGRADE = """
CREATE TABLE user_api_keys (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES "user" (id) ON DELETE CASCADE,
    provider text NOT NULL,
    ciphertext bytea NOT NULL,
    nonce bytea NOT NULL,
    wrapped_dek bytea NOT NULL,
    kek_id text NOT NULL,
    last4 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at timestamptz
);
CREATE UNIQUE INDEX ux_user_api_keys_active ON user_api_keys (user_id, provider)
    WHERE revoked_at IS NULL;

CREATE TABLE usage_events (
    id bigserial PRIMARY KEY,
    user_id uuid NOT NULL,
    source text NOT NULL,
    feature text,
    label text,
    model text NOT NULL,
    input_tokens integer NOT NULL DEFAULT 0,
    cached_input_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    reasoning_tokens integer NOT NULL DEFAULT 0,
    cost_usd numeric(12, 6) NOT NULL,
    request_id text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_usage_events_user_created ON usage_events (user_id, created_at DESC);

CREATE TABLE allowances (
    id uuid PRIMARY KEY,
    email text NOT NULL,
    user_id uuid REFERENCES "user" (id) ON DELETE SET NULL,
    limit_usd numeric(12, 6) NOT NULL,
    period text NOT NULL,
    spent_usd numeric(12, 6) NOT NULL DEFAULT 0,
    period_start timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    status text NOT NULL DEFAULT 'active',
    granted_by text,
    note text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_allowances_user_active ON allowances (user_id) WHERE status = 'active';
CREATE INDEX ix_allowances_email_active ON allowances (email) WHERE status = 'active';
"""

DOWNGRADE = """
DROP TABLE IF EXISTS allowances;
DROP TABLE IF EXISTS usage_events;
DROP TABLE IF EXISTS user_api_keys;
"""


def upgrade() -> None:
    for statement in _statements(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in _statements(DOWNGRADE):
        op.execute(statement)


def _statements(script: str) -> list[str]:
    lines = [line for line in script.splitlines() if not line.strip().startswith("--")]
    return [chunk.strip() for chunk in "\n".join(lines).split(";") if chunk.strip()]
