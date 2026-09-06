"""The one place that knows how to reach Redis.

`RESEARCH_TREE_REDIS_URL` turns on everything that needs coordination across
processes: the Celery queue, the shared Semantic Scholar request lane, topic
review tokens, per-account rate limits, and change notifications for the
event streams. Unset, every consumer keeps its in-process or on-disk
behaviour, which is what local development and the test suite use.

Azure Managed Redis runs with the Enterprise cluster policy: keys are sharded
by slot, so a multi-key command or transaction only works when every key
carries the same `{hash tag}`. The Celery configuration prefixes its keys for
that reason; everything else here is single-key.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis
    import redis.asyncio

REDIS_URL_ENV = "RESEARCH_TREE_REDIS_URL"
SOCKET_TIMEOUT_SECONDS = 5.0


def redis_url() -> str | None:
    value = (os.environ.get(REDIS_URL_ENV) or "").strip()
    return value or None


@lru_cache(maxsize=None)
def get_redis() -> "redis.Redis | None":
    url = redis_url()
    if url is None:
        return None
    import redis

    return redis.Redis.from_url(
        url,
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=SOCKET_TIMEOUT_SECONDS,
        health_check_interval=30,
    )


@lru_cache(maxsize=None)
def get_async_redis() -> "redis.asyncio.Redis | None":
    url = redis_url()
    if url is None:
        return None
    import redis.asyncio

    return redis.asyncio.Redis.from_url(
        url,
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=SOCKET_TIMEOUT_SECONDS,
        health_check_interval=30,
    )


def forget_redis_clients() -> None:
    """Drop the cached clients (tests, and a process that changes its URL)."""

    get_redis.cache_clear()
    get_async_redis.cache_clear()
