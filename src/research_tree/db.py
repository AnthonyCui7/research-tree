"""The one place that knows how to reach Postgres.

`RESEARCH_TREE_DATABASE_URL` selects the Postgres repository; unset, the app
keeps its file-based repository and nothing here is ever called. The URL is the
SQLAlchemy form (`postgresql+psycopg://…`); `plain_postgres_dsn` strips the
driver tag for libraries that speak to psycopg directly.
"""

from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine, text

DATABASE_URL_ENV = "RESEARCH_TREE_DATABASE_URL"


def database_url() -> str | None:
    value = (os.environ.get(DATABASE_URL_ENV) or "").strip()
    return value or None


def plain_postgres_dsn(url: str) -> str:
    """`postgresql+psycopg://…` → `postgresql://…` for psycopg and PostgresSaver."""

    scheme, separator, rest = url.partition("://")
    driver_free = scheme.split("+", 1)[0]
    if driver_free == "postgres":
        driver_free = "postgresql"
    return f"{driver_free}{separator}{rest}"


def make_engine(url: str, *, pool_size: int = 4, max_overflow: int = 4) -> Engine:
    # Supabase's session pooler holds one server connection per client
    # connection, so the pool stays small. pre_ping evicts connections the
    # pooler closed while the app was idle; recycle keeps them younger than
    # the pooler's own idle timeout.
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )


@lru_cache(maxsize=None)
def get_engine() -> Engine:
    url = database_url()
    if url is None:
        raise RuntimeError(f"{DATABASE_URL_ENV} is not set.")
    return make_engine(url)


# The roles Supabase's REST layer connects as, and the role this service
# connects as. On a plain Postgres none of them exist and the statements below
# skip themselves.
REST_ROLES = ("anon", "authenticated", "service_role")
APPLICATION_ROLE = "research_tree_app"

_REVOKE_REST_GRANTS = f"""
DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY{list(REST_ROLES)!r} LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', role_name);
            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', role_name);
            EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM %I', role_name);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %I',
                role_name);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I',
                role_name);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM %I',
                role_name);
        END IF;
    END LOOP;
END $$;
"""

_ENABLE_ROW_SECURITY = f"""
DO $$
DECLARE
    tbl record;
    app_role_exists boolean := EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APPLICATION_ROLE}');
BEGIN
    FOR tbl IN
        SELECT c.oid, c.relname, c.relrowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
    LOOP
        IF NOT tbl.relrowsecurity THEN
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl.relname);
        END IF;
        IF app_role_exists AND NOT EXISTS (
            SELECT 1 FROM pg_policy p
            WHERE p.polrelid = tbl.oid AND p.polname = '{APPLICATION_ROLE}_all'
        ) THEN
            EXECUTE format(
                'CREATE POLICY {APPLICATION_ROLE}_all ON public.%I FOR ALL TO {APPLICATION_ROLE} '
                'USING (true) WITH CHECK (true)',
                tbl.relname);
        END IF;
    END LOOP;
END $$;
"""


def secure_public_schema(conn: Connection) -> None:
    """Keep every table in `public` off Supabase's REST API.

    Supabase runs PostgREST in front of `public` and grants `anon`,
    `authenticated` and `service_role` full DML on whatever `postgres` creates
    there. This service never uses that API, so the grants are revoked, present
    and default, and row-level security is turned on with one policy, for the
    application role. Revoking is what closes it (`service_role` bypasses RLS);
    RLS is the backstop for a grant that comes back.

    Idempotent, and run after every schema change (`research-tree-migrate`),
    because a table created later - by a migration, or by LangGraph's
    checkpointer setup - starts with RLS off. On a plain Postgres none of the
    roles exist and nothing here has any effect.
    """

    # One statement per execute: psycopg's extended protocol refuses a batch.
    conn.execute(text(_REVOKE_REST_GRANTS))
    conn.execute(text(_ENABLE_ROW_SECURITY))
