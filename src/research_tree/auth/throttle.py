"""Fixed-window limits on the two unauthenticated routes, and on key checks.

In process, because one replica serves the site: a limit that resets on restart
is still a limit an attacker cannot lean on for long. Sign-in is counted at the
route, where every attempt is one guess. Registration is counted from the user
manager instead, which is the first point a request has a real email and
password behind it — counting it at the route charged people for typing their
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

import logging
import time
from threading import Lock

from fastapi import Request

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

    def __init__(self) -> None:
        self._hits: dict[str, tuple[int, float, int]] = {}
        self._lock = Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
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
    ok_email = (
        _limiter.hit(f"login:ip:{ip}:email:{email}", *LOGIN_ATTEMPTS_PER_ADDRESS_AND_EMAIL)
        if email
        else True
    )
    ok_ip = _limiter.hit(f"login:ip:{ip}", *LOGIN_ATTEMPTS_PER_ADDRESS)
    if not (ok_email and ok_ip):
        raise RateLimitedError("Too many sign-in attempts. Wait a few minutes and try again.")


def count_registration(request: Request | None) -> None:
    """Count one real attempt to create an account."""

    if request is None:
        return
    if not _limiter.hit(f"register:ip:{_client_ip(request)}", *REGISTRATIONS_PER_IP):
        raise RateLimitedError("Too many accounts created from here. Try again later.")


def count_key_check(user_id: str) -> None:
    """Count one key sent to the provider to be checked."""

    if not _limiter.hit(f"key-check:user:{user_id}", *KEY_CHECKS_PER_ACCOUNT):
        raise RateLimitedError("Too many keys checked recently. Wait an hour and try again.")
