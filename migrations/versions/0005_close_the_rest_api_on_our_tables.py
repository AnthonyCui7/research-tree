"""Take our tables out of reach of Supabase's REST API.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-08

Supabase runs PostgREST in front of every table in `public` and, through
`ALTER DEFAULT PRIVILEGES`, grants `anon`, `authenticated` and `service_role`
full DML on anything the `postgres` role creates there. Our migrations create
their tables as `postgres`, so all nineteen of them - accounts, session
tokens, wrapped API keys, allowances, every workspace document - were readable
and writable by anyone holding the project's anon key, which Supabase publishes
as a client-side value.

This service never uses that API. It reaches Postgres over the wire protocol
as `research_tree_app` and does its own authentication, so the whole REST
surface is exposure with no upside.

Two layers, because they stop different things:

  Revoking the grants is what actually closes it. `service_role` carries
  BYPASSRLS, so no row policy would have held it; without SELECT it has
  nothing to bypass.

  Row-level security is the backstop for a grant that comes back - a Supabase
  default we did not anticipate, or a table someone creates by hand. RLS is
  deny-by-default, and the only policy is the one that lets the application
  role through. It is not this service's tenancy model: a workspace belongs to
  an account because the query says so, not because a row policy does.

The work is `research_tree.db.secure_public_schema`, which
`research-tree-migrate` also runs after every upgrade so a table created
later (by a migration, or by LangGraph's checkpointer setup) is covered too.
This revision records it in the schema's history for a database that only
ever sees `alembic upgrade`.

`postgres` owns the tables and carries BYPASSRLS, so migrations, `pg_dump` and
the import CLI are unaffected. On a plain Postgres - CI, docker compose, a
self-hosted deployment - none of these roles exist, nothing happens, and the
connecting role owns its tables and so is exempt from RLS.
"""

from __future__ import annotations

from alembic import op

from research_tree.db import APPLICATION_ROLE, secure_public_schema

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


DOWNGRADE = f"""
DO $$
DECLARE
    table_name text;
BEGIN
    FOR table_name IN
        SELECT c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS {APPLICATION_ROLE}_all ON public.%I', table_name);
        EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY', table_name);
    END LOOP;
END $$;
"""


def upgrade() -> None:
    secure_public_schema(op.get_bind())


def downgrade() -> None:
    # The grants are deliberately not restored: putting the tables back on a
    # public API is not something a schema downgrade should do silently.
    op.execute(DOWNGRADE)
