"""The one place that knows how to reach Postgres.

`RESEARCH_TREE_DATABASE_URL` selects the Postgres repository; unset, the app
keeps its file-based repository and nothing here is ever called. The URL is the
SQLAlchemy form (`postgresql+psycopg://…`); `plain_postgres_dsn` strips the
driver tag for libraries that speak to psycopg directly.
"""

from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine

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
