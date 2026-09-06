"""Account-related configuration, read from the environment when needed."""

from __future__ import annotations

import os

SESSION_SECRET_ENV = "SESSION_SECRET"
PUBLIC_ORIGIN_ENV = "RESEARCH_TREE_PUBLIC_ORIGIN"
ALLOWED_EMAILS_ENV = "RESEARCH_TREE_ALLOWED_EMAILS"
ADMIN_EMAILS_ENV = "RESEARCH_TREE_ADMIN_EMAILS"
GOOGLE_CLIENT_ID_ENV = "GOOGLE_OAUTH_CLIENT_ID"
GOOGLE_CLIENT_SECRET_ENV = "GOOGLE_OAUTH_CLIENT_SECRET"
GOOGLE_REDIRECT_URI_ENV = "GOOGLE_OAUTH_REDIRECT_URI"

DEFAULT_PUBLIC_ORIGIN = "http://localhost:5173"
SESSION_COOKIE_NAME = "rt_session"
SESSION_LIFETIME_SECONDS = 30 * 24 * 60 * 60


def session_secret() -> str:
    value = (os.environ.get(SESSION_SECRET_ENV) or "").strip()
    if len(value) < 32:
        raise RuntimeError(
            f"{SESSION_SECRET_ENV} must be set to a random string of at least 32 characters."
        )
    return value


def public_origin() -> str:
    """The origin the site is served from, e.g. https://tryresearchtree.com."""

    value = (os.environ.get(PUBLIC_ORIGIN_ENV) or "").strip().rstrip("/")
    return value or DEFAULT_PUBLIC_ORIGIN


def cookies_are_secure() -> bool:
    return public_origin().startswith("https://")


def _email_set(variable: str) -> set[str]:
    return {
        item.strip().casefold()
        for item in (os.environ.get(variable) or "").split(",")
        if item.strip()
    }


def allowed_emails() -> set[str]:
    return _email_set(ALLOWED_EMAILS_ENV)


def admin_emails() -> set[str]:
    return _email_set(ADMIN_EMAILS_ENV)


def email_is_allowed(email: str) -> bool:
    """An empty allowlist admits everyone; otherwise the email must be on it."""

    allowed = allowed_emails()
    return not allowed or email.strip().casefold() in allowed


def google_client() -> tuple[str, str] | None:
    client_id = (os.environ.get(GOOGLE_CLIENT_ID_ENV) or "").strip()
    client_secret = (os.environ.get(GOOGLE_CLIENT_SECRET_ENV) or "").strip()
    if client_id and client_secret:
        return client_id, client_secret
    return None


def google_redirect_uri() -> str:
    value = (os.environ.get(GOOGLE_REDIRECT_URI_ENV) or "").strip()
    if value:
        return value
    origin = public_origin()
    # The dev server proxies /api to the backend; the production image serves
    # both from one origin.
    prefix = "/api" if origin == DEFAULT_PUBLIC_ORIGIN else ""
    return f"{origin}{prefix}/auth/google/callback"
