"""Ownership checks at the service funnels.

A workspace belongs to the account that built it. Another account asking for
it gets the same 404 a nonexistent id gets, so ids cannot be enumerated. The
local principal (auth mode `none`) owns everything, which keeps the
single-user deployment and the test suite unchanged.
"""

from __future__ import annotations

from typing import Any, Mapping

from research_tree.principal import current_principal
from research_tree.services.errors import WorkspaceNotFoundError
from research_tree.workspace.repository import WorkspaceRepository


def require_owned(repository: WorkspaceRepository, workspace_id: str) -> None:
    principal = current_principal()
    if principal is None or principal.is_local:
        return
    if repository.get_workspace_owner_id(workspace_id) != principal.user_id:
        raise WorkspaceNotFoundError(f"workspace does not exist: {workspace_id}")


def require_run_owned(run: Mapping[str, Any]) -> None:
    principal = current_principal()
    if principal is None or principal.is_local:
        return
    if str(run.get("owner_id") or "") != principal.user_id:
        raise WorkspaceNotFoundError(f"pipeline run does not exist: {run.get('run_id')}")
