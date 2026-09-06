"""The async engine the account routes use.

fastapi-users' adapter is async-only, so accounts get their own small pool
on the same database URL; every other query in the app stays synchronous.
"""

from __future__ import annotations

from functools import lru_cache
from typing import AsyncIterator

from fastapi import Depends
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from research_tree.auth.models import AccessToken, OAuthAccount, User
from research_tree.db import DATABASE_URL_ENV, database_url


@lru_cache(maxsize=None)
def get_async_engine() -> AsyncEngine:
    url = database_url()
    if url is None:
        raise RuntimeError(f"{DATABASE_URL_ENV} is required when accounts are enabled.")
    return create_async_engine(
        url, pool_size=2, max_overflow=2, pool_pre_ping=True, pool_recycle=1800
    )


@lru_cache(maxsize=None)
def _session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_async_engine(), expire_on_commit=False)


async def get_async_session() -> AsyncIterator[AsyncSession]:
    async with _session_factory()() as session:
        yield session


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncIterator[SQLAlchemyUserDatabase]:
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


async def get_access_token_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncIterator[SQLAlchemyAccessTokenDatabase]:
    yield SQLAlchemyAccessTokenDatabase(session, AccessToken)


async def dispose_async_engine() -> None:
    if get_async_engine.cache_info().currsize:
        await get_async_engine().dispose()
        get_async_engine.cache_clear()
        _session_factory.cache_clear()
