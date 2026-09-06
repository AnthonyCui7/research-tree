from __future__ import annotations

import logging
import os

from fastapi import APIRouter

from research_tree.api.schemas import (
    ApiKeyStatus,
    ApiKeysResponse,
    BugReportRequest,
    BugReportResponse,
    SaveApiKeyRequest,
    SaveApiKeyResponse,
    SessionResponse,
)
from research_tree.principal import LOCAL_PRINCIPAL, auth_mode, current_principal


logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/account", tags=["account"])

# Everything here is scoped to whoever is running the server, because this build
# has no accounts and no database. The read side is real — it reports the key the
# process is actually using — while the write sides accept and refuse to store.
# See PROJECT.md "Next Steps" for what turning them on requires.
STORAGE_UNAVAILABLE = "This build reads its key from the server environment."
BUG_REPORT_UNAVAILABLE = "Bug reports are written to the server log for now."


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
    return {"openai": _environment_key_status("OPENAI_API_KEY")}


@router.put("/api-keys", response_model=SaveApiKeyResponse)
def save_api_key(request: SaveApiKeyRequest) -> dict[str, object]:
    # The key is deliberately not read, logged, or echoed: there is nowhere safe
    # to put it yet, and a secret that reaches a log is a secret that leaked.
    del request
    return {"stored": False, "detail": STORAGE_UNAVAILABLE}


@router.post("/bug-reports", response_model=BugReportResponse, status_code=202)
def report_bug(request: BugReportRequest) -> dict[str, object]:
    logger.info(
        "bug report received area=%s summary=%s details_chars=%d",
        request.area,
        request.summary,
        len(request.details),
    )
    return {"received": True, "stored": False, "detail": BUG_REPORT_UNAVAILABLE}


def _environment_key_status(variable: str) -> dict[str, object]:
    """Report a configured key by its last four characters and nothing more."""

    key = (os.environ.get(variable) or "").strip()
    if not key:
        return {"configured": False, "masked": None, "source": None}
    return {
        "configured": True,
        "masked": f"sk-…{key[-4:]}" if len(key) >= 4 else "sk-…",
        "source": "environment",
    }
