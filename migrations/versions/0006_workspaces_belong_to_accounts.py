"""A workspace is one account's, and so is its name.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-08

A workspace used to be identified by its id alone, with the owner as a column
beside it. Names were therefore taken across every account at once: two people
building "prompting" got "prompting" and "prompting-2", each learning that the
other existed, and every read had to check the owner column after finding the
row. The owner is half of the identity now. `(owner_id, id)` is the key of
`workspaces` and of everything that hangs off one, so a name is only ever
taken within one account, and a query that names its owner cannot reach
another account's rows however it is written.

The owner is the acting principal's id as text: an account's uuid, or
`local_user` on a deployment without accounts. It is deliberately not a foreign
key to "user": an account row going away must not quietly reassign or orphan
what that account built.

Every row here has an owner to inherit (a workspace's children take its
owner; a run without one takes its workspace's), so the NOT NULL constraints
hold on the data as it stands. On a database where they would not, the
migration fails and says which table: that data needs a decision, not a
default.
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

LOCAL_OWNER = "local_user"

UPGRADE = f"""
ALTER TABLE workspace_versions DROP CONSTRAINT workspace_versions_workspace_id_fkey;
ALTER TABLE workspace_navigation DROP CONSTRAINT workspace_navigation_workspace_id_fkey;

ALTER TABLE workspaces DROP CONSTRAINT workspaces_owner_id_fkey;
ALTER TABLE workspaces DROP CONSTRAINT workspaces_pkey;
ALTER TABLE workspaces ALTER COLUMN owner_id TYPE text USING owner_id::text;
UPDATE workspaces SET owner_id = '{LOCAL_OWNER}' WHERE owner_id IS NULL;
ALTER TABLE workspaces ALTER COLUMN owner_id SET NOT NULL;
ALTER TABLE workspaces ADD PRIMARY KEY (owner_id, id);

ALTER TABLE workspace_versions ADD COLUMN owner_id text;
UPDATE workspace_versions v SET owner_id = w.owner_id FROM workspaces w WHERE w.id = v.workspace_id;
ALTER TABLE workspace_versions ALTER COLUMN owner_id SET NOT NULL;
ALTER TABLE workspace_versions DROP CONSTRAINT workspace_versions_pkey;
ALTER TABLE workspace_versions ADD PRIMARY KEY (owner_id, workspace_id, version_hash);
ALTER TABLE workspace_versions ADD FOREIGN KEY (owner_id, workspace_id)
    REFERENCES workspaces (owner_id, id) ON DELETE CASCADE;

ALTER TABLE workspace_navigation ADD COLUMN owner_id text;
UPDATE workspace_navigation n SET owner_id = w.owner_id FROM workspaces w WHERE w.id = n.workspace_id;
ALTER TABLE workspace_navigation ALTER COLUMN owner_id SET NOT NULL;
ALTER TABLE workspace_navigation DROP CONSTRAINT workspace_navigation_pkey;
ALTER TABLE workspace_navigation ADD PRIMARY KEY (owner_id, workspace_id);
ALTER TABLE workspace_navigation ADD FOREIGN KEY (owner_id, workspace_id)
    REFERENCES workspaces (owner_id, id) ON DELETE CASCADE;

ALTER TABLE workspace_events ADD COLUMN owner_id text;
UPDATE workspace_events e SET owner_id = w.owner_id FROM workspaces w WHERE w.id = e.workspace_id;
ALTER TABLE workspace_events ALTER COLUMN owner_id SET NOT NULL;
DROP INDEX ix_workspace_events_workspace;
CREATE INDEX ix_workspace_events_workspace ON workspace_events (owner_id, workspace_id, id);
DROP INDEX ix_workspace_events_review;
CREATE INDEX ix_workspace_events_review
    ON workspace_events (owner_id, workspace_id, review_id, event_type)
    WHERE review_id IS NOT NULL;

ALTER TABLE agent_run_events ADD COLUMN owner_id text;
UPDATE agent_run_events e SET owner_id = w.owner_id FROM workspaces w WHERE w.id = e.workspace_id;
ALTER TABLE agent_run_events ALTER COLUMN owner_id SET NOT NULL;
DROP INDEX ix_agent_run_events_workspace;
CREATE INDEX ix_agent_run_events_workspace ON agent_run_events (owner_id, workspace_id, id);

ALTER TABLE reviews ADD COLUMN owner_id text;
UPDATE reviews r SET owner_id = w.owner_id FROM workspaces w WHERE w.id = r.workspace_id;
ALTER TABLE reviews ALTER COLUMN owner_id SET NOT NULL;
ALTER TABLE reviews DROP CONSTRAINT reviews_pkey;
ALTER TABLE reviews ADD PRIMARY KEY (owner_id, workspace_id, review_id);

ALTER TABLE pipeline_runs ALTER COLUMN owner_id TYPE text USING owner_id::text;
UPDATE pipeline_runs r SET owner_id = w.owner_id FROM workspaces w
    WHERE r.owner_id IS NULL AND w.id = r.workspace_id;
UPDATE pipeline_runs SET owner_id = '{LOCAL_OWNER}' WHERE owner_id IS NULL;
ALTER TABLE pipeline_runs ALTER COLUMN owner_id SET NOT NULL;
UPDATE pipeline_runs SET record = record || jsonb_build_object('owner_id', owner_id)
    WHERE record->>'owner_id' IS DISTINCT FROM owner_id;
DROP INDEX ux_pipeline_runs_active_workspace;
CREATE UNIQUE INDEX ux_pipeline_runs_active_workspace ON pipeline_runs (owner_id, workspace_id)
    WHERE status IN ('queued', 'running');
DROP INDEX ix_pipeline_runs_active_topic;
CREATE INDEX ix_pipeline_runs_active_topic ON pipeline_runs (owner_id, topic_key)
    WHERE status IN ('queued', 'running');
DROP INDEX ix_pipeline_runs_workspace;
CREATE INDEX ix_pipeline_runs_workspace ON pipeline_runs (owner_id, workspace_id, created_at DESC);
"""

# Only possible while no two accounts hold the same workspace id; the primary
# key on `workspaces (id)` refuses otherwise, which is the right answer.
DOWNGRADE = """
ALTER TABLE workspace_versions DROP CONSTRAINT workspace_versions_owner_id_workspace_id_fkey;
ALTER TABLE workspace_navigation DROP CONSTRAINT workspace_navigation_owner_id_workspace_id_fkey;

ALTER TABLE workspaces DROP CONSTRAINT workspaces_pkey;
ALTER TABLE workspaces ADD PRIMARY KEY (id);
ALTER TABLE workspaces ALTER COLUMN owner_id DROP NOT NULL;
UPDATE workspaces SET owner_id = NULL WHERE owner_id = 'local_user';
ALTER TABLE workspaces ALTER COLUMN owner_id TYPE uuid USING owner_id::uuid;
ALTER TABLE workspaces ADD FOREIGN KEY (owner_id) REFERENCES "user" (id) ON DELETE SET NULL;

ALTER TABLE workspace_versions DROP CONSTRAINT workspace_versions_pkey;
ALTER TABLE workspace_versions ADD PRIMARY KEY (workspace_id, version_hash);
ALTER TABLE workspace_versions DROP COLUMN owner_id;
ALTER TABLE workspace_versions ADD FOREIGN KEY (workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE;

ALTER TABLE workspace_navigation DROP CONSTRAINT workspace_navigation_pkey;
ALTER TABLE workspace_navigation ADD PRIMARY KEY (workspace_id);
ALTER TABLE workspace_navigation DROP COLUMN owner_id;
ALTER TABLE workspace_navigation ADD FOREIGN KEY (workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE;

DROP INDEX ix_workspace_events_workspace;
DROP INDEX ix_workspace_events_review;
ALTER TABLE workspace_events DROP COLUMN owner_id;
CREATE INDEX ix_workspace_events_workspace ON workspace_events (workspace_id, id);
CREATE INDEX ix_workspace_events_review ON workspace_events (workspace_id, review_id, event_type)
    WHERE review_id IS NOT NULL;

DROP INDEX ix_agent_run_events_workspace;
ALTER TABLE agent_run_events DROP COLUMN owner_id;
CREATE INDEX ix_agent_run_events_workspace ON agent_run_events (workspace_id, id);

ALTER TABLE reviews DROP CONSTRAINT reviews_pkey;
ALTER TABLE reviews DROP COLUMN owner_id;
ALTER TABLE reviews ADD PRIMARY KEY (workspace_id, review_id);

DROP INDEX ux_pipeline_runs_active_workspace;
DROP INDEX ix_pipeline_runs_active_topic;
DROP INDEX ix_pipeline_runs_workspace;
ALTER TABLE pipeline_runs ALTER COLUMN owner_id DROP NOT NULL;
UPDATE pipeline_runs SET owner_id = NULL WHERE owner_id = 'local_user';
ALTER TABLE pipeline_runs ALTER COLUMN owner_id TYPE uuid USING owner_id::uuid;
CREATE UNIQUE INDEX ux_pipeline_runs_active_workspace ON pipeline_runs (workspace_id)
    WHERE status IN ('queued', 'running');
CREATE INDEX ix_pipeline_runs_active_topic ON pipeline_runs (topic_key)
    WHERE status IN ('queued', 'running');
CREATE INDEX ix_pipeline_runs_workspace ON pipeline_runs (workspace_id, created_at DESC);
"""


def upgrade() -> None:
    for statement in _statements(UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in _statements(DOWNGRADE):
        op.execute(statement)


def _statements(script: str) -> list[str]:
    return [chunk.strip() for chunk in script.split(";") if chunk.strip()]
