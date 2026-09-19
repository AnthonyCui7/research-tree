"""Wrapping the data keys that seal stored API keys.

A stored key is sealed with its own random data key (AES-256-GCM, see
`user_keys.py`); that data key is in turn wrapped by a key-encryption key that
never sits in the database. In the cloud the KEK is an RSA key in Azure Key
Vault and the wrap and unwrap happen inside the vault (`KeyVaultKeyWrapper`).
Locally, and in the cloud until the vault's crypto roles are granted, it is a
32-byte AES key read from the environment (`LocalAesKeyWrapper`). Every
wrapped data key records which KEK wrapped it, so both can be configured at
once and a rotation never strands old rows: new keys wrap with the primary,
old ones still unwrap with whichever KEK made them.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import time
from functools import lru_cache
from threading import Lock
from typing import Any, Protocol

KEY_ENCRYPTION_KEY_ENV = "RESEARCH_TREE_KEY_ENCRYPTION_KEY"
KEY_VAULT_URL_ENV = "RESEARCH_TREE_KEY_VAULT_URL"
KEY_VAULT_KEK_NAME_ENV = "RESEARCH_TREE_KEY_VAULT_KEK_NAME"
DEFAULT_KEK_NAME = "byok-kek"
LOCAL_KEK_PREFIX = "local:"
# How long the vault's answer to one unwrap is kept in this process, and how
# many answers. Every model call opens the account's key, a build or an
# annotation job makes hundreds, and each unwrap is a request to a vault that
# meters them and can fail. What a wrapped data key unwraps to never changes,
# and the key's row is still read from the database on every call, so a key
# that was removed stops being used at once: nothing looks its data key up
# again. A data key opens one row and is no more to hold than the API key it
# seals, which is already in memory for the length of the call.
UNWRAPPED_KEY_TTL_SECONDS = 5 * 60
UNWRAPPED_KEY_LIMIT = 512


class KeyUnavailableError(RuntimeError):
    """No configured key-encryption key can unwrap this data key."""


class KeyWrapper(Protocol):
    @property
    def kek_id(self) -> str: ...

    def wrap(self, data_key: bytes) -> bytes: ...

    def unwrap(self, wrapped: bytes, kek_id: str) -> bytes: ...

    def can_unwrap(self, kek_id: str) -> bool: ...


class LocalAesKeyWrapper:
    """AES key wrap (RFC 3394) with a KEK held in the process environment."""

    def __init__(self, kek: bytes) -> None:
        if len(kek) != 32:
            raise ValueError("the key-encryption key must be exactly 32 bytes")
        self._kek = kek
        self._kek_id = LOCAL_KEK_PREFIX + hashlib.sha256(kek).hexdigest()[:16]

    @classmethod
    def from_env(cls) -> LocalAesKeyWrapper | None:
        raw = (os.environ.get(KEY_ENCRYPTION_KEY_ENV) or "").strip()
        if not raw:
            return None
        try:
            kek = base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error) as error:
            raise RuntimeError(
                f"{KEY_ENCRYPTION_KEY_ENV} must be the base64 of 32 random bytes."
            ) from error
        if len(kek) != 32:
            raise RuntimeError(f"{KEY_ENCRYPTION_KEY_ENV} must decode to exactly 32 bytes.")
        return cls(kek)

    @property
    def kek_id(self) -> str:
        return self._kek_id

    def wrap(self, data_key: bytes) -> bytes:
        from cryptography.hazmat.primitives.keywrap import aes_key_wrap

        return aes_key_wrap(self._kek, data_key)

    def unwrap(self, wrapped: bytes, kek_id: str) -> bytes:
        if not self.can_unwrap(kek_id):
            raise KeyUnavailableError(f"no local key-encryption key matches {kek_id}")
        from cryptography.hazmat.primitives.keywrap import aes_key_unwrap

        return aes_key_unwrap(self._kek, wrapped)

    def can_unwrap(self, kek_id: str) -> bool:
        return kek_id == self._kek_id


class KeyVaultKeyWrapper:
    """RSA-OAEP-256 wrap and unwrap performed inside Azure Key Vault.

    The KEK never leaves the vault; the app's managed identity needs the
    Key Vault Crypto User role. The wrapping key's *versioned* id is recorded
    as the kek_id, so after a rotation the previous version still unwraps
    what it wrapped.
    """

    def __init__(self, vault_url: str, key_name: str, *, credential: Any = None) -> None:
        self.vault_url = vault_url.rstrip("/")
        self.key_name = key_name
        self._credential = credential
        self._kek_id: str | None = None
        self._clients: dict[str, Any] = {}
        self._unwrapped: dict[tuple[str, bytes], tuple[float, bytes]] = {}
        self._unwrapped_lock = Lock()

    def _get_credential(self) -> Any:
        if self._credential is None:
            from azure.identity import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
        return self._credential

    @property
    def kek_id(self) -> str:
        if self._kek_id is None:
            from azure.keyvault.keys import KeyClient

            key = KeyClient(self.vault_url, self._get_credential()).get_key(self.key_name)
            self._kek_id = str(key.id)
        return self._kek_id

    def _client(self, kek_id: str) -> Any:
        client = self._clients.get(kek_id)
        if client is None:
            from azure.keyvault.keys.crypto import CryptographyClient

            client = CryptographyClient(kek_id, self._get_credential())
            self._clients[kek_id] = client
        return client

    def wrap(self, data_key: bytes) -> bytes:
        from azure.keyvault.keys.crypto import KeyWrapAlgorithm

        result = self._client(self.kek_id).wrap_key(KeyWrapAlgorithm.rsa_oaep_256, data_key)
        return bytes(result.encrypted_key)

    def unwrap(self, wrapped: bytes, kek_id: str) -> bytes:
        if not self.can_unwrap(kek_id):
            raise KeyUnavailableError(f"{kek_id} is not a version of {self.key_name} in this vault")
        now = time.monotonic()
        with self._unwrapped_lock:
            expires_at, data_key = self._unwrapped.get((kek_id, wrapped), (0.0, b""))
        if expires_at > now:
            return data_key
        from azure.keyvault.keys.crypto import KeyWrapAlgorithm

        result = self._client(kek_id).unwrap_key(KeyWrapAlgorithm.rsa_oaep_256, wrapped)
        data_key = bytes(result.key)
        with self._unwrapped_lock:
            if len(self._unwrapped) >= UNWRAPPED_KEY_LIMIT:
                self._unwrapped = {
                    key: kept for key, kept in self._unwrapped.items() if kept[0] > now
                }
            if len(self._unwrapped) >= UNWRAPPED_KEY_LIMIT:
                # Insertion order is age order: the first entry is the oldest.
                del self._unwrapped[next(iter(self._unwrapped))]
            self._unwrapped[(kek_id, wrapped)] = (now + UNWRAPPED_KEY_TTL_SECONDS, data_key)
        return data_key

    def can_unwrap(self, kek_id: str) -> bool:
        return kek_id.startswith(f"{self.vault_url}/keys/{self.key_name}/")


class CompositeKeyWrapper:
    """Wraps with the first wrapper; unwraps with whichever recognises the kek_id."""

    def __init__(self, primary: KeyWrapper, *others: KeyWrapper) -> None:
        self._wrappers: tuple[KeyWrapper, ...] = (primary, *others)

    @property
    def kek_id(self) -> str:
        return self._wrappers[0].kek_id

    def wrap(self, data_key: bytes) -> bytes:
        return self._wrappers[0].wrap(data_key)

    def unwrap(self, wrapped: bytes, kek_id: str) -> bytes:
        for wrapper in self._wrappers:
            if wrapper.can_unwrap(kek_id):
                return wrapper.unwrap(wrapped, kek_id)
        raise KeyUnavailableError(f"no configured key-encryption key can unwrap {kek_id}")

    def can_unwrap(self, kek_id: str) -> bool:
        return any(wrapper.can_unwrap(kek_id) for wrapper in self._wrappers)


@lru_cache(maxsize=None)
def key_wrapper_from_env() -> KeyWrapper | None:
    """Key Vault when its URL is set, the local KEK when that is set, both when
    both are (the vault wraps, either unwraps); None means keys cannot be
    saved on this server."""

    wrappers: list[KeyWrapper] = []
    vault_url = (os.environ.get(KEY_VAULT_URL_ENV) or "").strip()
    if vault_url:
        key_name = (os.environ.get(KEY_VAULT_KEK_NAME_ENV) or "").strip() or DEFAULT_KEK_NAME
        wrappers.append(KeyVaultKeyWrapper(vault_url, key_name))
    local = LocalAesKeyWrapper.from_env()
    if local is not None:
        wrappers.append(local)
    if not wrappers:
        return None
    if len(wrappers) == 1:
        return wrappers[0]
    return CompositeKeyWrapper(*wrappers)


def forget_key_wrapper() -> None:
    key_wrapper_from_env.cache_clear()
