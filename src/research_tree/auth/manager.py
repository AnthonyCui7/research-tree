"""The account lifecycle: password rules, the allowlist, Google profile sync."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, AsyncIterator

import httpx
from fastapi import Depends, Request, Response
from fastapi_users import BaseUserManager, UUIDIDMixin, schemas
from fastapi_users.exceptions import InvalidPasswordException
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

from research_tree.auth.db import get_user_db
from research_tree.auth.models import User
from research_tree.auth.settings import email_is_allowed, session_secret
from research_tree.services.errors import ForbiddenError

logger = logging.getLogger("uvicorn.error")

MIN_PASSWORD_LENGTH = 10
# The handful of passwords that appear on every breach list; a length rule
# alone still admits "password1234".
COMMON_PASSWORDS = frozenset(
    {
        "password12",
        "password123",
        "password1234",
        "1234567890",
        "12345678910",
        "qwertyuiop",
        "qwerty1234",
        "iloveyou123",
        "letmein123",
        "welcome123",
        "abcdefghij",
        "researchtree",
    }
)
NOT_ALLOWED_MESSAGE = (
    "Research Tree is in a private preview. Ask the person who invited you to add "
    "your email, then sign in again."
)
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class UserRead(schemas.BaseUser[uuid.UUID]):
    name: str | None = None
    avatar_url: str | None = None


class UserCreate(schemas.BaseUserCreate):
    pass


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    def __init__(self, user_db: SQLAlchemyUserDatabase) -> None:
        super().__init__(user_db)
        secret = session_secret()
        self.reset_password_token_secret = secret
        self.verification_token_secret = secret

    async def validate_password(self, password: str, user: schemas.UC | User) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise InvalidPasswordException(
                f"Use at least {MIN_PASSWORD_LENGTH} characters."
            )
        email = str(getattr(user, "email", "") or "")
        if email and password.casefold() == email.casefold():
            raise InvalidPasswordException("Your password cannot be your email address.")
        if password.casefold() in COMMON_PASSWORDS:
            raise InvalidPasswordException("That password is too common. Choose another.")

    async def create(
        self,
        user_create: schemas.UC,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        ensure_email_allowed(user_create.email)
        return await super().create(user_create, safe=safe, request=request)

    async def oauth_callback(
        self,
        oauth_name: str,
        access_token: str,
        account_id: str,
        account_email: str,
        expires_at: int | None = None,
        refresh_token: str | None = None,
        request: Request | None = None,
        *,
        associate_by_email: bool = False,
        is_verified_by_default: bool = False,
    ) -> User:
        ensure_email_allowed(account_email)
        user = await super().oauth_callback(
            oauth_name,
            access_token,
            account_id,
            account_email,
            expires_at,
            refresh_token,
            request,
            associate_by_email=associate_by_email,
            is_verified_by_default=is_verified_by_default,
        )
        profile = await _google_profile(access_token)
        if profile:
            user = await self.user_db.update(user, profile)
        return user

    async def on_after_login(
        self,
        user: User,
        request: Request | None = None,
        response: Response | None = None,
    ) -> None:
        await self.user_db.update(user, {"last_seen_at": datetime.now(UTC)})


def ensure_email_allowed(email: str) -> None:
    if not email_is_allowed(email):
        raise ForbiddenError(NOT_ALLOWED_MESSAGE)


async def _google_profile(access_token: str) -> dict[str, Any]:
    """Name and picture from Google, best effort: sign-in works without them."""

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        logger.info("google profile lookup skipped: %s", type(error).__name__)
        return {}
    profile: dict[str, Any] = {}
    name = payload.get("name") if isinstance(payload, dict) else None
    picture = payload.get("picture") if isinstance(payload, dict) else None
    if isinstance(name, str) and name.strip():
        profile["name"] = name.strip()[:200]
    if isinstance(picture, str) and picture.startswith("https://"):
        profile["avatar_url"] = picture[:1000]
    return profile


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncIterator[UserManager]:
    yield UserManager(user_db)
