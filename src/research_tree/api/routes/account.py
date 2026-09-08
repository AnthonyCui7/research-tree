from __future__ import annotations

import logging
import os

from fastapi import APIRouter

from research_tree.api.schemas import (
    ApiKeysResponse,
    BugReportRequest,
    BugReportResponse,
    RemoveApiKeyResponse,
    SaveApiKeyRequest,
    SaveApiKeyResponse,
    SessionResponse,
    UsageResponse,
)
from research_tree.principal import LOCAL_PRINCIPAL, Principal, auth_mode, current_principal
from research_tree.services.errors import ByokUnavailableError, InvalidPayloadError


logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/account", tags=["account"])

# The local profile (no accounts) reads the key the server process runs with
# and cannot save one: there is no account to hold it. Behind sign-in each
# account keeps its own sealed key (`billing/user_keys.py`) and may hold a
# sponsored allowance on the platform key (`billing/allowances.py`).
STORAGE_UNAVAILABLE = "This build reads its key from the server environment."
BUG_REPORT_UNAVAILABLE = "Bug reports are written to the server log for now."
KEY_SHAPE_MESSAGE = "That does not look like an OpenAI API key."
USAGE_WINDOW_DAYS = 30


@router.get("/me", response_model=SessionResponse)
def get_session() -> dict[str, object]:
    """Who is signed in, and how sign-in works on this deployment."""

    principal = current_principal() or LOCAL_PRINCIPAL
    google_enabled = False
    if auth_mode() == "accounts":
        from research_tree.auth.routes import google_sign_in_enabled

        google_enabled = google_sign_in_enabled()
    return {
        "auth_mode": auth_mode(),
        "google_sign_in": google_enabled,
        "user": {
            "id": principal.user_id,
            "email": principal.email,
            "name": principal.name,
            "avatar_url": principal.avatar_url,
            "is_admin": principal.is_admin,
            "is_verified": principal.is_verified,
        },
    }


@router.get("/api-keys", response_model=ApiKeysResponse)
def get_api_keys() -> dict[str, object]:
    principal = _account()
    if principal is None:
        return {
            "openai": _environment_key_status("OPENAI_API_KEY"),
            "allowance": None,
            "saving_enabled": False,
            "platform_key": _platform_key_configured(),
        }
    from research_tree.billing.allowances import allowance_summary
    from research_tree.billing.keywrap import key_wrapper_from_env
    from research_tree.billing.user_keys import user_key_status

    status = user_key_status(principal.user_id)
    return {
        "openai": _account_key_status(status["last4"], status["created_at"]) if status else _no_key(),
        "allowance": allowance_summary(
            principal.user_id, principal.email, verified=principal.is_verified
        ),
        "saving_enabled": key_wrapper_from_env() is not None,
        "platform_key": _platform_key_configured(),
    }


@router.put("/api-keys", response_model=SaveApiKeyResponse)
def save_api_key(request: SaveApiKeyRequest) -> dict[str, object]:
    principal = _account()
    if principal is None:
        # Not read, logged, or echoed: there is nowhere to put it.
        return {"stored": False, "detail": STORAGE_UNAVAILABLE}
    from research_tree.billing.keywrap import key_wrapper_from_env
    from research_tree.billing.user_keys import (
        SAVING_DISABLED_MESSAGE,
        looks_like_openai_key,
        store_user_key,
        validate_openai_key,
    )
    from research_tree.credentials import forget_user_key

    api_key = request.api_key.strip()
    # Shape is checked here rather than by pydantic so a refusal never echoes
    # the value back in a validation error.
    if not looks_like_openai_key(api_key):
        raise InvalidPayloadError(KEY_SHAPE_MESSAGE)
    if key_wrapper_from_env() is None:
        raise ByokUnavailableError(SAVING_DISABLED_MESSAGE)
    validate_openai_key(api_key)
    last4 = store_user_key(principal.user_id, api_key)
    forget_user_key(principal.user_id)
    logger.info("account %s saved an OpenAI key ending in %s", principal.user_id, last4)
    return {"stored": True, "detail": "", "openai": _account_key_status(last4, None)}


@router.delete("/api-keys", response_model=RemoveApiKeyResponse)
def remove_api_key() -> dict[str, object]:
    principal = _account()
    if principal is None:
        return {"removed": False, "detail": STORAGE_UNAVAILABLE}
    from research_tree.billing.user_keys import delete_user_key
    from research_tree.credentials import forget_user_key

    removed = delete_user_key(principal.user_id)
    forget_user_key(principal.user_id)
    return {"removed": removed, "detail": ""}


@router.get("/usage", response_model=UsageResponse)
def get_usage() -> dict[str, object]:
    principal = _account()
    if principal is None:
        return {
            "days": USAGE_WINDOW_DAYS,
            "calls": 0,
            "total_usd": 0.0,
            "byok_usd": 0.0,
            "sponsored_usd": 0.0,
            "recent": [],
        }
    from research_tree.billing.usage import usage_summary

    return usage_summary(principal.user_id, days=USAGE_WINDOW_DAYS)


@router.post("/bug-reports", response_model=BugReportResponse, status_code=202)
def report_bug(request: BugReportRequest) -> dict[str, object]:
    # `%r` rather than `%s`: the summary is whatever the reader typed, and a
    # newline in it wrote a second line that read like the server's own.
    # The details are logged too — the response says the report went to the log,
    # and a report without its details is not a report.
    logger.info(
        "bug report received area=%s summary=%r details=%r",
        request.area,
        request.summary,
        request.details,
    )
    return {"received": True, "stored": False, "detail": BUG_REPORT_UNAVAILABLE}


def _account() -> Principal | None:
    """The signed-in account, or None for the local profile."""

    principal = current_principal()
    if principal is None or principal.is_local:
        return None
    return principal


def _platform_key_configured() -> bool:
    return bool((os.environ.get("OPENAI_API_KEY") or "").strip())


def _no_key() -> dict[str, object]:
    return {"configured": False, "masked": None, "source": None, "created_at": None}


def _account_key_status(last4: str, created_at: str | None) -> dict[str, object]:
    return {
        "configured": True,
        "masked": f"sk-…{last4}",
        "source": "account",
        "created_at": created_at,
    }


def _environment_key_status(variable: str) -> dict[str, object]:
    """Report a configured key by its last four characters and nothing more."""

    key = (os.environ.get(variable) or "").strip()
    if not key:
        return _no_key()
    return {
        "configured": True,
        "masked": f"sk-…{key[-4:]}" if len(key) >= 4 else "sk-…",
        "source": "environment",
        "created_at": None,
    }
