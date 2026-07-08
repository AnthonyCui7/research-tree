from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends

from research_tree.services.agent import WorkspaceAgentService
from research_tree.services.reviews import WorkspaceReviewService
from research_tree.services.workspaces import WorkspaceQueryService
from research_tree.workspace.repository import LocalJsonWorkspaceRepository


def get_repository() -> LocalJsonWorkspaceRepository:
    base_dir = Path(os.environ.get("RESEARCH_TREE_DATA_DIR", "data/workspaces"))
    return LocalJsonWorkspaceRepository(base_dir)


def get_workspace_query_service(
    repository: LocalJsonWorkspaceRepository = Depends(get_repository),
) -> WorkspaceQueryService:
    return WorkspaceQueryService(repository)


def get_workspace_agent_service(
    repository: LocalJsonWorkspaceRepository = Depends(get_repository),
) -> WorkspaceAgentService:
    return WorkspaceAgentService(repository)


def get_workspace_review_service(
    repository: LocalJsonWorkspaceRepository = Depends(get_repository),
) -> WorkspaceReviewService:
    return WorkspaceReviewService(repository)
