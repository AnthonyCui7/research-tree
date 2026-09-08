"""`research-tree-migrate`: bring the database up to date.

Runs the Alembic migrations, then LangGraph's checkpointer setup (which
creates its own tables), then the hardening pass that keeps every table in
`public` off Supabase's REST API. The pass comes last because the checkpointer
tables are created outside the migrations and would otherwise start with
row-level security off. All of it needs DDL rights, so this runs as the
database owner (the `migrate` job in the cloud, the `migrate` service in
compose); the API itself connects as a role that can only read and write rows.
"""

from __future__ import annotations

import sys
from pathlib import Path

from research_tree.db import DATABASE_URL_ENV, database_url, make_engine, plain_postgres_dsn, secure_public_schema

REPO_ROOT = Path(__file__).resolve().parents[3]


def migrate(url: str) -> None:
    """Everything a database needs before this version of the service runs on it."""

    from alembic import command
    from alembic.config import Config
    from langgraph.checkpoint.postgres import PostgresSaver

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.cmd_opts = _AlembicArgs(x=[f"url={url}"])
    command.upgrade(config, "head")
    with PostgresSaver.from_conn_string(plain_postgres_dsn(url)) as saver:
        saver.setup()
    engine = make_engine(url, pool_size=1, max_overflow=0)
    try:
        with engine.begin() as conn:
            secure_public_schema(conn)
    finally:
        engine.dispose()


class _AlembicArgs:
    """What `alembic -x url=…` puts on the command object; `env.py` reads it."""

    def __init__(self, x: list[str]) -> None:
        self.x = x


def main(argv: list[str] | None = None) -> int:
    del argv
    url = database_url()
    if not url:
        print(f"{DATABASE_URL_ENV} is not set.", file=sys.stderr)
        return 2
    migrate(url)
    print("database at head, checkpointer tables present, public schema secured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
