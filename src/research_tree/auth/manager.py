"""The account lifecycle: password rules, the allowlist, Google profile sync."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, AsyncIterator

import httpx
from fastapi import Depends, Request, Response
from fastapi_users import BaseUserManager, UUIDIDMixin, exceptions, schemas
from fastapi_users.exceptions import InvalidPasswordException
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

from research_tree.auth.db import get_user_db
from research_tree.auth.google import USERINFO_ENDPOINT
from research_tree.auth.models import User
from research_tree.auth.accounts import claim_unproven_account
from research_tree.auth.settings import email_is_allowed, session_secret
from research_tree.auth.throttle import count_registration
from research_tree.services.errors import ForbiddenError

logger = logging.getLogger("uvicorn.error")

# Password hashes in flight at once. Each holds 64 MB while it runs, and the
# sign-in route is open to anyone, so the number is what a small container can
# spare rather than what its thread pool would allow.
_hashing = asyncio.Semaphore(2)

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
        # Counted here rather than at the route, which runs before the body is
        # validated: a mistyped address used to spend the hour's registrations.
        await count_registration(request)
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
        try:
            await self.get_by_oauth_account(oauth_name, account_id)
        except exceptions.UserNotExists:
            # Only an identity signing in for the first time is joined to an
            # account by its address. One that is already linked signs in to
            # the account it is linked to, whatever address it carries now.
            await self._claim_unproven_account(account_email)
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

    async def _claim_unproven_account(self, account_email: str) -> None:
        """Hand the account to whoever can prove the address, and lock out anyone else.

        `associate_by_email` joins a Google identity to any existing account
        with the same address without asking whether anybody ever proved that
        address belongs to it. Nothing here sends email yet, so no password
        account is verified, and anyone could register a stranger's address,
        wait for them to press Continue with Google, and be handed their session
        while keeping the password they had set.

        Google has just proved the mailbox; the password account never did. So
        the account goes to Google: the password is replaced with one nobody
        holds, every existing session for it is revoked, and the address is
        marked verified. This is the standard remedy for a pre-hijacked account,
        and it costs a genuine password user their password rather than their
        account, at the moment they have just demonstrated they own the mailbox.

        TEMPORARY, and self-removing. Once registration verifies an address by
        email, a real owner's account arrives here already verified and takes
        the branch above, so this one stops firing on its own. Deleting it then
        is safe.
        """

        try:
            existing = await self.get_by_email(account_email)
        except exceptions.UserNotExists:
            return
        if existing.is_verified:
            return
        logger.warning(
            "google proved an address held by an unverified account; "
            "revoking its password and sessions user_id=%s",
            existing.id,
        )
        # Off the event loop: the hash is tens of milliseconds of CPU and the
        # write is a blocking call on the synchronous engine.
        unguessable = await asyncio.to_thread(
            self.password_helper.hash, self.password_helper.generate()
        )
        await asyncio.to_thread(claim_unproven_account, str(existing.id), unguessable)

    async def authenticate(self, credentials: Any) -> User | None:
        """The library's sign-in, with the hashing taken off the event loop.

        One argon2 verification is tens of milliseconds of CPU and 64 MB, and
        the library runs it inline, where it holds up every open stream and
        every other request for that long. Hashing releases the interpreter
        lock, so a thread really does free the loop; two at a time bounds the
        memory. An unknown address is still hashed, as the library does, so
        it answers no faster than a wrong password.
        """

        try:
            user = await self.get_by_email(credentials.username)
        except exceptions.UserNotExists:
            async with _hashing:
                await asyncio.to_thread(self.password_helper.hash, credentials.password)
            return None
        async with _hashing:
            verified, updated_hash = await asyncio.to_thread(
                self.password_helper.verify_and_update,
                credentials.password,
                user.hashed_password,
            )
        if not verified:
            return None
        if updated_hash is not None:
            await self.user_db.update(user, {"hashed_password": updated_hash})
        # The allowlist is checked on every request once signed in, but the
        # sign-in route itself is fastapi-users' own and knew nothing of it:
        # a removed address still got a 204 and a cookie, and only the next
        # request said no. Refusing here says so at the door instead.
        ensure_email_allowed(user.email)
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
                USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
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
