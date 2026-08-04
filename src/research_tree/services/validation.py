from __future__ import annotations

import re
from typing import Any, Mapping

from research_tree.services.errors import InvalidResourceIdError


_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,180}$")
_VERSION_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


def validate_resource_id(value: str, *, field_name: str) -> str:
    resource_id = value.strip()
    if resource_id in {"", ".", ".."} or not _SAFE_ID_PATTERN.fullmatch(resource_id):
        raise InvalidResourceIdError(
            f"{field_name} must contain only letters, numbers, '.', '_', ':', or '-'."
        )
    return resource_id


def validate_version_hash(value: str, *, field_name: str = "version_hash") -> str:
    """Reject a malformed hash here so it answers 400 rather than 500.

    The repository raises the same ValueError for a malformed hash and for a
    corrupt version file; only the first is the caller's fault.
    """

    version_hash = value.strip().casefold()
    if not _VERSION_HASH_PATTERN.fullmatch(version_hash):
        raise InvalidResourceIdError(
            f"{field_name} must be a 64-character SHA-256 hash."
        )
    return version_hash


def as_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        from research_tree.services.errors import InvalidPayloadError

        raise InvalidPayloadError(f"{field_name} must be a JSON object.")
    return dict(value)
