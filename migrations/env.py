"""Alembic runtime: the URL is the app's, never alembic.ini's.

`alembic -x url=postgresql+psycopg://… upgrade head` overrides the environment
for one run (the test suite does this against a scratch database).
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from research_tree.db import database_url

config = context.config


def _url() -> str:
    override = context.get_x_argument(as_dictionary=True).get("url")
    url = override or database_url()
    if not url:
        raise RuntimeError("Set RESEARCH_TREE_DATABASE_URL or pass -x url=… to alembic.")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
