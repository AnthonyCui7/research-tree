"""How a session reaches the browser: one HttpOnly cookie, one database row.

The token in the cookie is an opaque random string looked up in `accesstoken`
(fastapi-users' `DatabaseStrategy`), so signing out really revokes it and an
operator can revoke every session by clearing the table. Two backends share
that strategy: `cookie` answers JSON callers with 204, `cookie-redirect`
answers the Google callback with a redirect back into the app.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import Depends
from fastapi.responses import RedirectResponse, Response
from fastapi_users import FastAPIUsers
from fastapi_users.authentication import AuthenticationBackend, CookieTransport
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase

from research_tree.auth.db import get_access_token_db
from research_tree.auth.manager import get_user_manager
from research_tree.auth.models import User
from research_tree.auth.settings import (
    SESSION_COOKIE_NAME,
    SESSION_LIFETIME_SECONDS,
    cookies_are_secure,
)


class SessionCookieTransport(CookieTransport):
    """`Secure` follows the public origin at request time, not at import time."""

    def __init__(self) -> None:
        super().__init__(
            cookie_name=SESSION_COOKIE_NAME,
            cookie_max_age=SESSION_LIFETIME_SECONDS,
            cookie_path="/",
            cookie_httponly=True,
            cookie_samesite="lax",
        )

    @property
    def cookie_secure(self) -> bool:  # type: ignore[override]
        return cookies_are_secure()

    @cookie_secure.setter
    def cookie_secure(self, value: bool) -> None:
        # The base class assigns this in __init__; the property is the truth.
        del value


class RedirectCookieTransport(SessionCookieTransport):
    """The Google callback lands in a browser tab, so it answers with a redirect."""

    async def get_login_response(self, token: str) -> Response:
        response = RedirectResponse("/", status_code=303)
        return self._set_login_cookie(response, token)


def get_database_strategy(
    access_token_db: SQLAlchemyAccessTokenDatabase = Depends(get_access_token_db),
) -> DatabaseStrategy:
    return DatabaseStrategy(access_token_db, lifetime_seconds=SESSION_LIFETIME_SECONDS)


cookie_backend: AuthenticationBackend = AuthenticationBackend(
    name="cookie",
    transport=SessionCookieTransport(),
    get_strategy=get_database_strategy,
)

redirect_backend: AuthenticationBackend = AuthenticationBackend(
    name="cookie-redirect",
    transport=RedirectCookieTransport(),
    get_strategy=get_database_strategy,
)

fastapi_users: FastAPIUsers[User, uuid.UUID] = FastAPIUsers(
    get_user_manager, [cookie_backend, redirect_backend]
)

# Resolves the cookie to a user, or None; the API turns None into 401 itself
# so every unauthenticated answer has the same shape.
optional_current_user = fastapi_users.current_user(active=True, optional=True)

SameSite = Literal["lax", "strict", "none"]
