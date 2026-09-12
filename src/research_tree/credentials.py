"""Whose OpenAI key a model call spends.

Every call site that used to read `OPENAI_API_KEY` from the environment asks
here instead. For the local profile, and for code with no bound principal
(the CLIs), the answer is still the environment and may be None. For an
account it is that account's own key when it has one, otherwise the platform
key when the operator has granted the account an allowance that is not used
up, otherwise a 402 the API turns into a sentence. The choice is stamped on
the request's binding (`credential_source`) so the metering hook knows whose
money moved.

The account's key is read from the database on every call, never from a
per-process cache. The API and the worker are separate processes, so a cache
in one could not be told when the account page in the other saved or removed
a key: a build that failed for want of a key kept failing for five minutes
after the key was added, and a removed key went on being spent for as long.
The read is one indexed row and one unwrap, milliseconds beside the model
call it precedes.
"""

from __future__ import annotations

import os

from research_tree.db import database_url
from research_tree.principal import current_principal, set_credential_source
from research_tree.services.errors import AllowanceExhaustedError, NoLlmCredentialsError

NO_CREDENTIALS_MESSAGE = "Add an OpenAI API key to your account before running this."
ALLOWANCE_EXHAUSTED_MESSAGE = (
    "Your sponsored allowance is used up. Add your own OpenAI API key to keep going."
)


def openai_api_key() -> str | None:
    principal = current_principal()
    if principal is None or principal.is_local or database_url() is None:
        return _platform_key()
    key = _load_user_key(principal.user_id)
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


def _platform_key() -> str | None:
    return (os.environ.get("OPENAI_API_KEY") or "").strip() or None


def _load_user_key(user_id: str) -> str | None:
    from research_tree.billing.user_keys import load_user_key

    return load_user_key(user_id)


def _allowance_status(user_id: str, email: str, *, verified: bool) -> str:
    from research_tree.billing.allowances import allowance_status

    return allowance_status(user_id, email, verified=verified)
