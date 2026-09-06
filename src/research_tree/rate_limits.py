"""Per-account ceilings on the actions that spend money.

Every workspace build, topic review, assistant turn, and annotation job ends
in model calls billed to the platform key, so each account gets a fixed
number per window. Counters are Redis fixed windows (`INCR` + `EXPIRE`), so
the limit holds across the API and the worker. Without Redis, or for the
local single-user profile, nothing is counted.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from research_tree.principal import current_principal
from research_tree.services.errors import RateLimitedError

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class Limit:
    action: str
    env: str
    default: int
    window_seconds: int
    noun: str
    period: str


LIMITS = {
    limit.action: limit
    for limit in (
        Limit("workspaces", "RESEARCH_TREE_LIMIT_WORKSPACES_PER_DAY", 5, 86_400, "workspace builds", "day"),
        Limit("topic_reviews", "RESEARCH_TREE_LIMIT_TOPIC_REVIEWS_PER_HOUR", 30, 3_600, "topic reviews", "hour"),
        Limit("agent_turns", "RESEARCH_TREE_LIMIT_AGENT_TURNS_PER_MINUTE", 30, 60, "assistant requests", "minute"),
        Limit("annotation_jobs", "RESEARCH_TREE_LIMIT_ANNOTATION_JOBS_PER_HOUR", 20, 3_600, "paper annotations", "hour"),
    )
}


def limit_for(action: str) -> int:
    limit = LIMITS[action]
    raw = (os.environ.get(limit.env) or "").strip()
    try:
        return int(raw) if raw else limit.default
    except ValueError:
        return limit.default


def check_rate_limit(action: str, *, redis: Any = None) -> None:
    """Count one `action` for the bound account; raise 429 past its ceiling.

    A ceiling of zero disables the action for everyone; a negative one
    disables the check.
    """

    limit = LIMITS[action]
    principal = current_principal()
    if principal is None or principal.is_local:
        return
    ceiling = limit_for(action)
    if ceiling < 0:
        return
    if redis is None:
        from research_tree.redis_client import get_redis

        redis = get_redis()
    if redis is None:
        return
    window = int(time.time()) // limit.window_seconds
    key = f"ratelimit:{action}:{principal.user_id}:{window}"
    try:
        count = int(redis.incr(key))
        if count == 1:
            redis.expire(key, limit.window_seconds + 1)
    except Exception as error:  # noqa: BLE001 - a limiter outage must not take the product down
        logger.warning("rate limit check skipped for %s: %s", action, error)
        return
    if count > ceiling:
        raise RateLimitedError(
            f"You have reached the limit of {ceiling} {limit.noun} per {limit.period}. "
            "Try again later."
        )
