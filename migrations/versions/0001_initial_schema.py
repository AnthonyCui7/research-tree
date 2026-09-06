"""Initial schema: accounts, workspaces, reviews, pipeline runs.

Revision ID: 0001
Revises:
Create Date: 2026-09-06

Hand-written DDL is the single source of truth for the schema; the ORM models
in `research_tree.auth.models` mirror the three account tables and nothing
else is mapped. Workspace documents and review payloads are `json`, not
`jsonb`: both are hashed after a round trip, and jsonb normalises key order
and number spelling, which would change the hash.
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


UPGRADE = """
-- fastapi-users tables (columns match fastapi_users_db_sqlalchemy 7.x mixins).
CREATE TABLE "user" (
    id uuid PRIMARY KEY,
    email varchar(320) NOT NULL,
    hashed_password varchar(1024) NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    is_superuser boolean NOT NULL DEFAULT false,
    is_verified boolean NOT NULL DEFAULT false,
    name text,
    avatar_url text,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz
);
CREATE UNIQUE INDEX ix_user_email ON "user" (email);

CREATE TABLE oauth_account (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES "user" (id) ON DELETE CASCADE,
    oauth_name varchar(100) NOT NULL,
    access_token varchar(1024) NOT NULL,
    expires_at integer,
    refresh_token varchar(1024),
    account_id varchar(320) NOT NULL,
    account_email varchar(320) NOT NULL
);
CREATE INDEX ix_oauth_account_oauth_name ON oauth_account (oauth_name);
CREATE INDEX ix_oauth_account_account_id ON oauth_account (account_id);
CREATE INDEX ix_oauth_account_user_id ON oauth_account (user_id);

CREATE TABLE accesstoken (
    token varchar(43) PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES "user" (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_accesstoken_created_at ON accesstoken (created_at);
CREATE INDEX ix_accesstoken_user_id ON accesstoken (user_id);

-- Workspaces. One row per workspace id, kept after deletion so the id stays
-- taken; summary columns are what the sidebar lists.
CREATE TABLE workspaces (
    id text PRIMARY KEY,
    owner_id uuid REFERENCES "user" (id) ON DELETE SET NULL,
    topic text NOT NULL DEFAULT '',
    title text NOT NULL DEFAULT '',
    topic_key text NOT NULL DEFAULT '',
    current_version_hash char(64),
    paper_count integer NOT NULL DEFAULT 0,
    branch_count integer NOT NULL DEFAULT 0,
    paper_path_count integer NOT NULL DEFAULT 0,
    document_updated_at text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);
CREATE INDEX ix_workspaces_owner_topic ON workspaces (owner_id, topic_key)
    WHERE deleted_at IS NULL;

CREATE TABLE workspace_versions (
    workspace_id text NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version_hash char(64) NOT NULL,
    document json NOT NULL,
    metadata jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, version_hash)
);

CREATE TABLE workspace_navigation (
    workspace_id text PRIMARY KEY REFERENCES workspaces (id) ON DELETE CASCADE,
    version_hashes jsonb NOT NULL DEFAULT '[]'::jsonb,
    current_index integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Events and reviews carry no foreign key: a review can be recorded for a
-- workspace whose first version has not been published yet.
CREATE TABLE workspace_events (
    id bigserial PRIMARY KEY,
    workspace_id text NOT NULL,
    event_id text NOT NULL UNIQUE,
    event_type text NOT NULL,
    review_id text,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_workspace_events_workspace ON workspace_events (workspace_id, id);
CREATE INDEX ix_workspace_events_review ON workspace_events (workspace_id, review_id, event_type)
    WHERE review_id IS NOT NULL;

CREATE TABLE agent_run_events (
    id bigserial PRIMARY KEY,
    workspace_id text NOT NULL,
    run_event_id text NOT NULL UNIQUE,
    agent_run_id text NOT NULL,
    status text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_agent_run_events_workspace ON agent_run_events (workspace_id, id);

CREATE TABLE reviews (
    workspace_id text NOT NULL,
    review_id text NOT NULL,
    review_type text NOT NULL,
    status text NOT NULL,
    payload json NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, review_id)
);

CREATE TABLE pipeline_runs (
    run_id text PRIMARY KEY,
    workspace_id text,
    topic_key text NOT NULL DEFAULT '',
    owner_id uuid,
    status text NOT NULL,
    record jsonb NOT NULL,
    celery_task_id text,
    heartbeat_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- One active run per workspace, enforced by the database rather than by
-- whoever read the run list last.
CREATE UNIQUE INDEX ux_pipeline_runs_active_workspace ON pipeline_runs (workspace_id)
    WHERE status IN ('queued', 'running');
CREATE INDEX ix_pipeline_runs_active_topic ON pipeline_runs (topic_key)
    WHERE status IN ('queued', 'running');
CREATE INDEX ix_pipeline_runs_workspace ON pipeline_runs (workspace_id, created_at DESC);

-- Touched daily so Supabase's free tier never sees an idle database.
CREATE TABLE service_heartbeat (
    id text PRIMARY KEY,
    touched_at timestamptz NOT NULL DEFAULT now()
);
"""

DOWNGRADE = """
DROP TABLE IF EXISTS service_heartbeat;
DROP TABLE IF EXISTS pipeline_runs;
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS agent_run_events;
DROP TABLE IF EXISTS workspace_events;
DROP TABLE IF EXISTS workspace_navigation;
DROP TABLE IF EXISTS workspace_versions;
DROP TABLE IF EXISTS workspaces;
DROP TABLE IF EXISTS accesstoken;
DROP TABLE IF EXISTS oauth_account;
DROP TABLE IF EXISTS "user";
"""


def upgrade() -> None:
    for statement in _statements(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in _statements(DOWNGRADE):
        op.execute(statement)


def _statements(script: str) -> list[str]:
    # Comments go first so a semicolon inside one cannot split a statement.
    lines = [line for line in script.splitlines() if not line.strip().startswith("--")]
    return [chunk.strip() for chunk in "\n".join(lines).split(";") if chunk.strip()]
