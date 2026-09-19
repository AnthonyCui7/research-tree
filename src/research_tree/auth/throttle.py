"""Fixed-window limits on sign-in, registration, key checks and costly actions.

The counts live in Redis when it is configured, so they hold across replicas
and outlast a deploy, and in this process otherwise. Sign-in is counted at the
route, where every attempt is one guess. Registration is counted from the user
manager instead, which is the first point a request has a real email and
password behind it: counting it at the route charged people for typing their
address wrong.

Sign-in is counted per address, and per address and email together, never per
email alone. Counted per email, ten wrong passwords from anywhere locked the
account they named out for fifteen minutes, and a stranger who knew an address
could keep that up indefinitely: an account lockout is a denial of service
anyone can aim. Scoped to the address, one address cannot guess at one account
for long, and what stops a guess from many addresses is the password itself:
ten characters at least, off the common list, behind argon2.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from threading import Lock
from typing import Any

from fastapi import Request

from research_tree.principal import current_principal
from research_tree.services.errors import RateLimitedError

logger = logging.getLogger("uvicorn.error")

LOGIN_ATTEMPTS_PER_ADDRESS_AND_EMAIL = (10, 15 * 60)
LOGIN_ATTEMPTS_PER_ADDRESS = (30, 15 * 60)
# An account with no key and no allowance cannot spend anything, so the only
# cost of a new one is a row. Five an hour refused people sharing an office
# address or a mobile carrier's; twenty still stops a script.
REGISTRATIONS_PER_IP = (20, 60 * 60)
# Saving a key sends it to OpenAI to be checked before it is stored, which
# makes the route a way to test keys from this server's address. A person
# saves a key a few times in a lifetime; a script trying a list is stopped.
KEY_CHECKS_PER_ACCOUNT = (20, 60 * 60)
# What one account may ask of the parts every account shares: the Semantic
# Scholar lane, the worker's queue, the threads assistant turns run on. Each
# is set at a few times what a person working flat out gets through in an
# hour, so it is only ever a script that meets one.
ACTIONS_PER_ACCOUNT = {
    "topic_review": (60, 60 * 60, "Too many topics reviewed this hour. Try again later."),
    "build": (12, 60 * 60, "Too many builds started this hour. Try again later."),
    "assistant_turn": (120, 60 * 60, "Too many messages to the assistant this hour. Try again later."),
    "annotation_job": (40, 60 * 60, "Too many papers sent for annotation this hour. Try again later."),
}
# How long the shared counts stay out of play after Redis refuses one call.
REDIS_RETRY_SECONDS = 30.0

# Counts one hit and starts the window on the first, atomically.
_COUNT_HIT_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""

# One entry per email or address seen inside its window. Entries expire, so the
# steady-state size is the request rate times the window: reaching this needs
# more than a hundred sign-in attempts a second, sustained, all with different
# keys.
MAX_TRACKED_KEYS = 100_000


class _FixedWindowLimiter:
    """Counts attempts per key, and holds on to the counts under pressure.

    A bounded table has to decide what to do when it fills. Clearing it, which
    this did, resets the attacker's own counter along with everyone else's, so
    a flood of made-up emails bought a fresh budget of guesses. Declining to
    track new keys is worse still: it leaves every address not already in the
    table unlimited. So the overflow drops what has expired, and if the table
    is genuinely full of live keys it refuses instead, because that many
    distinct attempts inside one window is the attack rather than the traffic.
    """

    def __init__(self, redis_client: Any = None) -> None:
        self._hits: dict[str, tuple[int, float, int]] = {}
        self._lock = Lock()
        self._redis_client = redis_client
        self._redis_resolved = redis_client is not None
        self._redis_unavailable_until = 0.0
        self._count_hit: Any = None

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        shared = self._shared_count(key, window_seconds)
        if shared is not None:
            return shared <= limit
        now = time.monotonic()
        with self._lock:
            count, window_start, _ = self._hits.get(key, (0, now, window_seconds))
            if now - window_start >= window_seconds:
                count, window_start = 0, now
            count += 1
            if key not in self._hits and len(self._hits) >= MAX_TRACKED_KEYS:
                self._hits = {
                    tracked: hit
                    for tracked, hit in self._hits.items()
                    if now - hit[1] < hit[2]
                }
                if len(self._hits) >= MAX_TRACKED_KEYS:
                    logger.warning(
                        "sign-in throttle is tracking %d live keys and is refusing new ones",
                        len(self._hits),
                    )
                    return False
            self._hits[key] = (count, window_start, window_seconds)
        return count <= limit

    def _shared_count(self, key: str, window_seconds: int) -> int | None:
        """The count across every process, or None when Redis is not in play.

        A Redis that fails falls back to this process's own table rather than
        refusing sign-ins or waving everything through. The stored key is a
        digest, so the store holds no addresses and no emails.
        """

        if not self._redis_resolved:
            from research_tree.redis_client import get_redis

            self._redis_client = get_redis()
            self._redis_resolved = True
        if self._redis_client is None or time.monotonic() < self._redis_unavailable_until:
            return None
        try:
            if self._count_hit is None:
                self._count_hit = self._redis_client.register_script(_COUNT_HIT_LUA)
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
            return int(self._count_hit(keys=[f"throttle:{digest}"], args=[window_seconds]))
        except Exception as error:  # noqa: BLE001 - count locally rather than not at all
            self._count_hit = None
            self._redis_unavailable_until = time.monotonic() + REDIS_RETRY_SECONDS
            logger.warning("shared throttle unavailable (%s); counting in this process.", error)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_limiter = _FixedWindowLimiter()


def reset() -> None:
    _limiter.reset()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def throttle_login(request: Request) -> None:
    """Count one sign-in attempt, per address and per address-and-email.

    Attached to the router that also carries `/auth/logout`; signing out is not
    an attempt and must never be refused because the sign-in budget is spent.
    """

    if not request.url.path.endswith("/login"):
        return
    form = await request.form()
    email = str(form.get("username") or "").strip().casefold()
    ip = _client_ip(request)
    # Off the event loop: with Redis behind it a count is a network call.
    ok_email = (
        await asyncio.to_thread(
            _limiter.hit, f"login:ip:{ip}:email:{email}", *LOGIN_ATTEMPTS_PER_ADDRESS_AND_EMAIL
        )
        if email
        else True
    )
    ok_ip = await asyncio.to_thread(_limiter.hit, f"login:ip:{ip}", *LOGIN_ATTEMPTS_PER_ADDRESS)
    if not (ok_email and ok_ip):
        raise RateLimitedError("Too many sign-in attempts. Wait a few minutes and try again.")


async def count_registration(request: Request | None) -> None:
    """Count one real attempt to create an account."""

    if request is None:
        return
    allowed = await asyncio.to_thread(
        _limiter.hit, f"register:ip:{_client_ip(request)}", *REGISTRATIONS_PER_IP
    )
    if not allowed:
        raise RateLimitedError("Too many accounts created from here. Try again later.")


def count_key_check(user_id: str) -> None:
    """Count one key sent to the provider to be checked."""

    if not _limiter.hit(f"key-check:user:{user_id}", *KEY_CHECKS_PER_ACCOUNT):
        raise RateLimitedError("Too many keys checked recently. Wait an hour and try again.")


def count_account_action(action: str) -> None:
    """Count one costly action against the account making it.

    The local profile is one person on their own machine and is not counted.
    Called from request handlers that already run off the event loop.
    """

    principal = current_principal()
    if principal is None or principal.is_local:
        return
    limit, window_seconds, refusal = ACTIONS_PER_ACCOUNT[action]
    if not _limiter.hit(f"{action}:user:{principal.user_id}", limit, window_seconds):
        raise RateLimitedError(refusal)
