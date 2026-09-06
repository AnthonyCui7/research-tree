"""`research-tree-migrate`: bring the database up to date.

Runs the Alembic migrations and then LangGraph's checkpointer setup, which
creates its own tables. Both need DDL rights, so this runs as the database
owner (the `migrate` job in the cloud, the `migrate` service in compose); the
API itself connects as a role that can only read and write rows.
"""

from __future__ import annotations

import sys
from pathlib import Path

from research_tree.db import DATABASE_URL_ENV, database_url, plain_postgres_dsn

REPO_ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    del argv
    url = database_url()
    if not url:
        print(f"{DATABASE_URL_ENV} is not set.", file=sys.stderr)
        return 2
    from alembic import command
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(config, "head")
    print("alembic: at head")

    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(plain_postgres_dsn(url)) as saver:
        saver.setup()
    print("langgraph checkpointer: tables present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
