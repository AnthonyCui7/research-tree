"""Whose OpenAI key a model call spends.

Every call site that used to read `OPENAI_API_KEY` from the environment asks
here instead. For the local profile, and for code with no bound principal
(the CLIs), the answer is still the environment and may be None. For an
account it is that account's own key when it has one, otherwise the platform
key when the operator has granted the account an allowance that is not used
up, otherwise a 402 the API turns into a sentence. The choice is stamped on
the request's binding (`credential_source`) so the metering hook knows whose
money moved.
"""

from __future__ import annotations

import os
import time
from threading import Lock

from research_tree.db import database_url
from research_tree.principal import current_principal, set_credential_source
from research_tree.services.errors import AllowanceExhaustedError, NoLlmCredentialsError

CACHE_TTL_SECONDS = 300.0
NO_CREDENTIALS_MESSAGE = "Add an OpenAI API key to your account before running this."
ALLOWANCE_EXHAUSTED_MESSAGE = (
    "Your sponsored allowance is used up. Add your own OpenAI API key to keep going."
)

# user id -> (key or None, expires at). Other replicas catch up within the TTL;
# this one is told directly when the account page saves or removes a key.
_user_keys: dict[str, tuple[str | None, float]] = {}
_user_keys_lock = Lock()


def openai_api_key() -> str | None:
    principal = current_principal()
    if principal is None or principal.is_local or database_url() is None:
        return _platform_key()
    key = _cached_user_key(principal.user_id)
    if key:
        set_credential_source("byok")
        return key
    status = _allowance_status(principal.user_id, principal.email, verified=principal.is_verified)
    platform = _platform_key()
    if status == "ok" and platform:
        set_credential_source("sponsored")
        return platform
    if status == "exhausted":
        raise AllowanceExhaustedError(ALLOWANCE_EXHAUSTED_MESSAGE)
    raise NoLlmCredentialsError(NO_CREDENTIALS_MESSAGE)


def forget_user_key(user_id: str | None = None) -> None:
    with _user_keys_lock:
        if user_id is None:
            _user_keys.clear()
        else:
            _user_keys.pop(user_id, None)


def _platform_key() -> str | None:
    return (os.environ.get("OPENAI_API_KEY") or "").strip() or None


def _cached_user_key(user_id: str) -> str | None:
    now = time.monotonic()
    with _user_keys_lock:
        cached = _user_keys.get(user_id)
        if cached is not None and cached[1] > now:
            return cached[0]
    key = _load_user_key(user_id)
    with _user_keys_lock:
        _user_keys[user_id] = (key, now + CACHE_TTL_SECONDS)
    return key


def _load_user_key(user_id: str) -> str | None:
    from research_tree.billing.user_keys import load_user_key

    return load_user_key(user_id)


def _allowance_status(user_id: str, email: str, *, verified: bool) -> str:
    from research_tree.billing.allowances import allowance_status

    return allowance_status(user_id, email, verified=verified)
