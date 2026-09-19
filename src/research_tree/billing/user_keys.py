"""Bring-your-own OpenAI keys, sealed per account.

A key is encrypted with a fresh 256-bit data key under AES-GCM, with the
account id, the row id and the provider as associated data, so a ciphertext
copied onto another row does not open. The data key is wrapped by the
key-encryption key (`keywrap.py`). The whole key is never logged, never
echoed, and never returned to a browser; the last four characters are stored
in the clear so the account page can name it.
"""

from __future__ import annotations

import logging
import os
import socket
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from research_tree.billing.keywrap import KeyWrapper, key_wrapper_from_env
from research_tree.db import get_engine
from research_tree.services.errors import (
    ApiKeyInvalidError,
    ByokUnavailableError,
    ProviderUnreachableError,
)

logger = logging.getLogger("uvicorn.error")

PROVIDER = "openai"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
KEY_CHECK_TIMEOUT_SECONDS = 10.0
MIN_KEY_LENGTH = 20
MAX_KEY_LENGTH = 400
SAVING_DISABLED_MESSAGE = "Saving keys is not enabled on this server."
KEY_REJECTED_MESSAGE = "OpenAI did not accept that key."
KEY_CHECK_FAILED_MESSAGE = "We could not check that key with OpenAI. Try again."


@dataclass(frozen=True)
class SealedSecret:
    ciphertext: bytes
    nonce: bytes
    wrapped_dek: bytes
    kek_id: str


def _associated_data(user_id: str, key_id: str, provider: str) -> bytes:
    return f"{user_id}|{key_id}|{provider}".encode("utf-8")


def seal_secret(
    wrapper: KeyWrapper, *, user_id: str, key_id: str, provider: str, secret: str
) -> SealedSecret:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    data_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    ciphertext = AESGCM(data_key).encrypt(
        nonce, secret.encode("utf-8"), _associated_data(user_id, key_id, provider)
    )
    return SealedSecret(
        ciphertext=ciphertext, nonce=nonce, wrapped_dek=wrapper.wrap(data_key), kek_id=wrapper.kek_id
    )


def open_secret(
    wrapper: KeyWrapper, sealed: SealedSecret, *, user_id: str, key_id: str, provider: str
) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    data_key = wrapper.unwrap(sealed.wrapped_dek, sealed.kek_id)
    plaintext = AESGCM(data_key).decrypt(
        sealed.nonce, sealed.ciphertext, _associated_data(user_id, key_id, provider)
    )
    return plaintext.decode("utf-8")


def looks_like_openai_key(value: str) -> bool:
    return (
        value.startswith("sk-")
        and MIN_KEY_LENGTH <= len(value) <= MAX_KEY_LENGTH
        # A key travels in an Authorization header, which is latin-1 only.
        # `isprintable` admits every printable code point, so a pasted key with
        # an accent or a CJK character passed this and then raised inside the
        # HTTP client, answering 500 instead of "that is not a key".
        and value.isascii()
        and value.isprintable()
        and not any(character.isspace() for character in value)
    )


def validate_openai_key(api_key: str, *, timeout_seconds: float = KEY_CHECK_TIMEOUT_SECONDS) -> None:
    """One cheap authenticated call; the key is refused if OpenAI refuses it.

    Exceptions are raised `from None` so no traceback carries the request
    that held the key.
    """

    request = urllib.request.Request(
        OPENAI_MODELS_URL, headers={"Authorization": f"Bearer {api_key}"}, method="GET"
    )
    from research_tree.llm import open_openai_request

    try:
        with open_openai_request(request, timeout_seconds=timeout_seconds) as response:
            response.read(64)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ApiKeyInvalidError(KEY_REJECTED_MESSAGE) from None
        raise ProviderUnreachableError(KEY_CHECK_FAILED_MESSAGE) from None
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
        raise ProviderUnreachableError(KEY_CHECK_FAILED_MESSAGE) from None


def store_user_key(user_id: str, api_key: str, *, wrapper: KeyWrapper | None = None) -> str:
    """Seal and save the key, retiring the account's previous one. Returns the last four characters."""

    wrapper = wrapper or key_wrapper_from_env()
    if wrapper is None:
        raise ByokUnavailableError(SAVING_DISABLED_MESSAGE)
    key_id = str(uuid.uuid4())
    sealed = seal_secret(wrapper, user_id=user_id, key_id=key_id, provider=PROVIDER, secret=api_key)
    last4 = api_key[-4:]
    with get_engine().begin() as conn:
        # Two saves at once each retired the old row and each inserted its
        # own, and the second insert hit the one-active-key index: a 500 for
        # a double click. Serialised per account, the second save retires the
        # first's row and lands, as a second save should.
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"user_api_key:{user_id}"},
        )
        conn.execute(
            text(
                "UPDATE user_api_keys SET revoked_at = now() "
                "WHERE user_id = CAST(:user_id AS uuid) AND provider = :provider AND revoked_at IS NULL"
            ),
            {"user_id": user_id, "provider": PROVIDER},
        )
        conn.execute(
            text(
                "INSERT INTO user_api_keys "
                "(id, user_id, provider, ciphertext, nonce, wrapped_dek, kek_id, last4) VALUES "
                "(CAST(:id AS uuid), CAST(:user_id AS uuid), :provider, :ciphertext, :nonce, "
                ":wrapped_dek, :kek_id, :last4)"
            ),
            {
                "id": key_id,
                "user_id": user_id,
                "provider": PROVIDER,
                "ciphertext": sealed.ciphertext,
                "nonce": sealed.nonce,
                "wrapped_dek": sealed.wrapped_dek,
                "kek_id": sealed.kek_id,
                "last4": last4,
            },
        )
    return last4


def load_user_key(user_id: str, *, wrapper: KeyWrapper | None = None) -> str | None:
    """The account's current key in the clear, or None when it has none or it cannot be opened."""

    with get_engine().begin() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT id, ciphertext, nonce, wrapped_dek, kek_id FROM user_api_keys "
                    "WHERE user_id = CAST(:user_id AS uuid) AND provider = :provider "
                    "AND revoked_at IS NULL"
                ),
                {"user_id": user_id, "provider": PROVIDER},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        # Read on every model call, so the timestamp is refreshed at most once
        # a minute rather than written back on each of them.
        conn.execute(
            text(
                "UPDATE user_api_keys SET last_used_at = now() WHERE id = :id "
                "AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')"
            ),
            {"id": row["id"]},
        )
    wrapper = wrapper or key_wrapper_from_env()
    if wrapper is None:
        logger.warning("account %s has a stored key but no key-encryption key is configured", user_id)
        return None
    sealed = SealedSecret(
        ciphertext=bytes(row["ciphertext"]),
        nonce=bytes(row["nonce"]),
        wrapped_dek=bytes(row["wrapped_dek"]),
        kek_id=str(row["kek_id"]),
    )
    try:
        return open_secret(wrapper, sealed, user_id=user_id, key_id=str(row["id"]), provider=PROVIDER)
    except Exception as error:  # noqa: BLE001 - a missing KEK or a tampered row, never the key itself
        logger.error(
            "stored key %s for account %s could not be opened: %s",
            row["id"],
            user_id,
            type(error).__name__,
        )
        return None


def delete_user_key(user_id: str) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(
            text(
                "UPDATE user_api_keys SET revoked_at = now() "
                "WHERE user_id = CAST(:user_id AS uuid) AND provider = :provider AND revoked_at IS NULL"
            ),
            {"user_id": user_id, "provider": PROVIDER},
        )
    return result.rowcount > 0


def user_key_status(user_id: str) -> dict[str, Any] | None:
    """`{"last4", "created_at"}` for the account's current key, or None."""

    with get_engine().begin() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT last4, created_at FROM user_api_keys "
                    "WHERE user_id = CAST(:user_id AS uuid) AND provider = :provider "
                    "AND revoked_at IS NULL"
                ),
                {"user_id": user_id, "provider": PROVIDER},
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    created = row["created_at"]
    return {
        "last4": str(row["last4"]),
        "created_at": created.isoformat() if isinstance(created, datetime) else None,
    }
