from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, Request

from research_tree.services.agent import WorkspaceAgentService
from research_tree.services.reviews import WorkspaceReviewService
from research_tree.services.pipeline import WorkspacePipelineService
from research_tree.services.topics import TopicReviewService
from research_tree.services.workspaces import WorkspaceQueryService
from research_tree.workspace.repository import LocalJsonWorkspaceRepository
from research_tree.paths import workspaces_dir


REPO_ROOT = Path(__file__).resolve().parents[3]


def get_repository(request: Request) -> LocalJsonWorkspaceRepository:
    # The lifespan builds one repository per process. Constructing one here is
    # the fallback for apps created without it (tests build them directly).
    repository = getattr(request.app.state, "repository", None)
    if repository is not None:
        return repository
    return LocalJsonWorkspaceRepository(workspaces_dir())


def get_workspace_query_service(
    repository: LocalJsonWorkspaceRepository = Depends(get_repository),
) -> WorkspaceQueryService:
    return WorkspaceQueryService(repository)


def get_workspace_agent_service(
    request: Request,
    repository: LocalJsonWorkspaceRepository = Depends(get_repository),
) -> WorkspaceAgentService:
    # Reuse the process-wide service: it owns the compiled graph, whose
    # checkpointer and node cache are worthless if rebuilt per request.
    service = getattr(request.app.state, "agent_service", None)
    if service is not None and service.repository is repository:
        return service
    return WorkspaceAgentService(repository)


def get_workspace_review_service(
    repository: LocalJsonWorkspaceRepository = Depends(get_repository),
) -> WorkspaceReviewService:
    # Approving a pipeline_rerun review starts a run, so the review service
    # needs the pipeline.
    return WorkspaceReviewService(
        repository,
        pipeline_service=WorkspacePipelineService(repository, repo_root=REPO_ROOT),
    )


def get_topic_review_service(
    repository: LocalJsonWorkspaceRepository = Depends(get_repository),
) -> TopicReviewService:
    return TopicReviewService(repository)


def get_workspace_pipeline_service(
    repository: LocalJsonWorkspaceRepository = Depends(get_repository),
) -> WorkspacePipelineService:
    return WorkspacePipelineService(repository, repo_root=REPO_ROOT)
