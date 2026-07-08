from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any, Mapping

from research_tree.workspace.context import workspace_version_hash


def apply_workspace_patch_in_memory(
    *,
    base_workspace: Mapping[str, Any],
    proposed_workspace: Mapping[str, Any],
    validation_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if validation_summary and not validation_summary.get("valid", False):
        raise ValueError("cannot apply an invalid workspace proposal")
    if not proposed_workspace:
        raise ValueError("proposed workspace is empty")
    updated = copy.deepcopy(dict(proposed_workspace))
    base_hash = workspace_version_hash(base_workspace)
    proposed_hash = workspace_version_hash(updated)
    provenance = updated.setdefault("provenance", {})
    if isinstance(provenance, dict):
        provenance.setdefault("agent_updates", [])
        provenance["agent_updates"].append(
            {
                "source": "workspace_agent",
                "applied_in_memory": True,
                "base_workspace_id": base_workspace.get("workspace_id"),
                "base_workspace_version_hash": base_hash,
                "proposed_workspace_version_hash": proposed_hash,
                "applied_at": datetime.now(UTC).isoformat(),
            }
        )
    return updated
