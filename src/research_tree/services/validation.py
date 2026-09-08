from __future__ import annotations

import re
from typing import Any, Mapping

from research_tree.services.errors import InvalidResourceIdError


# Every read of a workspace validates its id, so anything that *makes* an id
# has to respect this length or it creates a workspace nobody can open.
MAX_RESOURCE_ID_LENGTH = 180
_SAFE_ID_PATTERN = re.compile(rf"^[A-Za-z0-9_.:-]{{1,{MAX_RESOURCE_ID_LENGTH}}}$")
_VERSION_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


def validate_resource_id(value: str, *, field_name: str) -> str:
    resource_id = value.strip()
    if len(resource_id) > MAX_RESOURCE_ID_LENGTH:
        raise InvalidResourceIdError(
            f"{field_name} must be at most {MAX_RESOURCE_ID_LENGTH} characters."
        )
    if resource_id in {"", ".", ".."} or not _SAFE_ID_PATTERN.fullmatch(resource_id):
        raise InvalidResourceIdError(
            f"{field_name} must contain only letters, numbers, '.', '_', ':', or '-'."
        )
    # The file-backed store strips leading and trailing '.' and '-' before using
    # an id as a directory name. An id carrying them therefore named a different
    # workspace there than in Postgres — `-prompting` served `prompting`'s
    # document under a name it does not have, and an id made only of them
    # stripped to nothing and answered 500. These are exactly the ids that store
    # would alter, so refusing them is what makes the two agree.
    if resource_id[0] in ".-" or resource_id[-1] in ".-":
        raise InvalidResourceIdError(
            f"{field_name} must start and end with a letter, number, '_', or ':'."
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
