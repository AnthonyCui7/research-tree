"""The request-level identity gate.

`auth_dependency()` picks, once per app, the dependency every protected
router carries: with accounts it resolves the session cookie and refuses
anything else with 401; without accounts it binds the local user. Either way
the principal is bound for the rest of the request, including the streaming
responses, and unbound afterwards.
"""

from __future__ import annotations

import uuid
from typing import Any, AsyncIterator, Callable

from fastapi import Depends, Request

from research_tree.principal import LOCAL_PRINCIPAL, Principal, auth_mode, bind_principal
from research_tree.services.errors import ForbiddenError, UnauthenticatedError


def auth_dependency() -> Callable[..., Any]:
    if auth_mode() == "accounts":
        return require_account
    return local_principal


async def local_principal() -> AsyncIterator[Principal]:
    with bind_principal(LOCAL_PRINCIPAL):
        yield LOCAL_PRINCIPAL


def _optional_user_dependency() -> Callable[..., Any]:
    from research_tree.auth.backend import optional_current_user

    return optional_current_user


def _session_dependency() -> Callable[..., Any]:
    from research_tree.auth.db import get_async_session

    return get_async_session


async def require_account(
    request: Request,
    user: Any = Depends(_optional_user_dependency()),
    session: Any = Depends(_session_dependency()),
) -> AsyncIterator[Principal]:
    if user is None:
        raise UnauthenticatedError("Sign in to continue.")
    from research_tree.auth.manager import NOT_ALLOWED_MESSAGE
    from research_tree.auth.settings import admin_emails, email_is_allowed

    # Re-checked on every request, not only at sign-in, so removing an email
    # from the allowlist locks the account out immediately.
    if not email_is_allowed(user.email):
        raise ForbiddenError(NOT_ALLOWED_MESSAGE)
    principal = Principal(
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        avatar_url=user.avatar_url,
        is_admin=bool(user.is_superuser) or user.email.lower() in admin_emails(),
        is_verified=bool(user.is_verified),
        is_local=False,
    )
    # FastAPI holds a dependency open until the response is finished, and the
    # two SSE streams finish when the reader closes the tab. The session
    # fastapi-users just used is the same instance (dependencies are cached per
    # request), and nothing needs it again, so close it here: otherwise every
    # open stream parks one of the four connections in the auth pool and the
    # fifth request anywhere in the app waits for a connection that never comes.
    await session.close()
    with bind_principal(principal, request_id=uuid.uuid4().hex):
        yield principal
