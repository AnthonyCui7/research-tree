"""Bytes that are too big for the database: PDFs, extracted text, annotations.

Keys are paths (`pdf/{sha256}`, `paper_content/{workspace}/{sha}`,
`annotations/{workspace}/{sha}`, `pipeline/{sha256}`, `s2/{key}`). Locally
they land under the data root; in the cloud they are blobs in one private
container reached through the container's managed identity.

A `put` replaces whatever was under that key. Most keys are content-addressed,
where replacing means writing the same bytes again, but `annotations/...` and
`paper_content/...` are keyed by workspace and paper: regenerating a paper's
annotations writes new content under the same key. The Blob implementation
used to refuse to overwrite, so "regenerate from scratch" did nothing at all in
the cloud while working on a laptop.
"""

from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from research_tree.paths import data_root

BLOB_ACCOUNT_URL_ENV = "RESEARCH_TREE_BLOB_ACCOUNT_URL"
BLOB_CONTAINER_ENV = "RESEARCH_TREE_BLOB_CONTAINER"
DEFAULT_BLOB_CONTAINER = "artifacts"


class ArtifactStore(Protocol):
    def put(self, key: str, data: bytes) -> None:
        """Store `data` under `key`, replacing anything already there."""

    def get(self, key: str) -> bytes | None: ...

    def exists(self, key: str) -> bool: ...


class FilesystemArtifactStore:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else data_root() / "artifacts"

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def _path(self, key: str) -> Path:
        path = (self.root / _safe_key(key)).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"artifact key escapes the store: {key!r}")
        return path


class AzureBlobArtifactStore:
    """One private container; the identity running the process must hold
    Storage Blob Data Contributor on the account."""

    def __init__(self, account_url: str, container: str = DEFAULT_BLOB_CONTAINER) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import ContainerClient

        self._client = ContainerClient(
            account_url=account_url,
            container_name=container,
            credential=DefaultAzureCredential(),
        )

    def put(self, key: str, data: bytes) -> None:
        self._client.upload_blob(_safe_key(key), data, overwrite=True)

    def get(self, key: str) -> bytes | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._client.download_blob(_safe_key(key)).readall()
        except ResourceNotFoundError:
            return None

    def exists(self, key: str) -> bool:
        return self._client.get_blob_client(_safe_key(key)).exists()


def blob_account_url() -> str | None:
    value = (os.environ.get(BLOB_ACCOUNT_URL_ENV) or "").strip()
    return value or None


@lru_cache(maxsize=None)
def default_artifact_store() -> ArtifactStore:
    account_url = blob_account_url()
    if account_url:
        container = (os.environ.get(BLOB_CONTAINER_ENV) or "").strip() or DEFAULT_BLOB_CONTAINER
        return AzureBlobArtifactStore(account_url, container)
    return FilesystemArtifactStore()


def _safe_key(key: str) -> str:
    parts = [part for part in key.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError(f"invalid artifact key: {key!r}")
    return "/".join(parts)
