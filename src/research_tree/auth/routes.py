"""`/auth/*`: login, logout, register, and the Google round trip."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from threading import Lock

from fastapi import APIRouter, Depends, Request
from httpx_oauth.clients.google import GoogleOAuth2

from research_tree.auth.backend import cookie_backend, fastapi_users, redirect_backend
from research_tree.auth.manager import UserCreate, UserRead
from research_tree.auth.settings import (
    cookies_are_secure,
    google_client,
    google_redirect_uri,
    session_secret,
)
from research_tree.services.errors import RateLimitedError

logger = logging.getLogger("uvicorn.error")

# Fixed windows, in process. One replica serves the site until Redis lands,
# and a limit that resets on restart is still a limit an attacker cannot
# lean on for long.
LOGIN_ATTEMPTS_PER_EMAIL = (10, 15 * 60)
LOGIN_ATTEMPTS_PER_IP = (30, 15 * 60)
REGISTRATIONS_PER_IP = (5, 60 * 60)


class _FixedWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
        self._lock = Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            count, window_start = self._hits[key]
            if now - window_start >= window_seconds:
                count, window_start = 0, now
            count += 1
            self._hits[key] = (count, window_start)
            if len(self._hits) > 10_000:
                self._hits.clear()
        return count <= limit

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_limiter = _FixedWindowLimiter()


async def throttle_login(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    form = await request.form()
    email = str(form.get("username") or "").strip().casefold()
    ok_email = _limiter.hit(f"login:email:{email}", *LOGIN_ATTEMPTS_PER_EMAIL) if email else True
    ok_ip = _limiter.hit(f"login:ip:{ip}", *LOGIN_ATTEMPTS_PER_IP)
    if not (ok_email and ok_ip):
        raise RateLimitedError("Too many sign-in attempts. Wait a few minutes and try again.")


async def throttle_register(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    if not _limiter.hit(f"register:ip:{ip}", *REGISTRATIONS_PER_IP):
        raise RateLimitedError("Too many accounts created from here. Try again later.")


def build_auth_router() -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    router.include_router(
        fastapi_users.get_auth_router(cookie_backend),
        dependencies=[Depends(throttle_login)],
    )
    router.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        dependencies=[Depends(throttle_register)],
    )
    client = google_client()
    if client is not None:
        client_id, client_secret = client
        router.include_router(
            fastapi_users.get_oauth_router(
                GoogleOAuth2(client_id, client_secret),
                redirect_backend,
                session_secret(),
                redirect_url=google_redirect_uri(),
                associate_by_email=True,
                is_verified_by_default=True,
                csrf_token_cookie_secure=cookies_are_secure(),
            ),
            prefix="/google",
        )
    else:
        logger.warning("Google sign-in disabled: GOOGLE_OAUTH_CLIENT_ID/SECRET are not set.")
    return router


def google_sign_in_enabled() -> bool:
    return google_client() is not None
