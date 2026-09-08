"""`/auth/*`: login, logout, register, and the Google round trip."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from research_tree.auth.backend import cookie_backend, fastapi_users, redirect_backend
from research_tree.auth.google import GoogleOpenIdOAuth2
from research_tree.auth.manager import UserCreate, UserRead
from research_tree.auth.settings import (
    cookies_are_secure,
    google_client,
    google_redirect_uri,
    session_secret,
)
from research_tree.auth.throttle import throttle_login

logger = logging.getLogger("uvicorn.error")


def build_auth_router() -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    router.include_router(
        fastapi_users.get_auth_router(cookie_backend),
        dependencies=[Depends(throttle_login)],
    )
    router.include_router(fastapi_users.get_register_router(UserRead, UserCreate))
    client = google_client()
    if client is not None:
        client_id, client_secret = client
        router.include_router(
            fastapi_users.get_oauth_router(
                GoogleOpenIdOAuth2(client_id, client_secret),
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
