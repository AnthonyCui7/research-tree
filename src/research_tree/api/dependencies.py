from __future__ import annotations

from pathlib import Path

from fastapi import Depends, Request

from research_tree.services.agent import WorkspaceAgentService
from research_tree.services.annotations import PaperAnnotationService
from research_tree.services.edits import WorkspaceEditService
from research_tree.services.reviews import WorkspaceReviewService
from research_tree.services.pipeline import WorkspacePipelineService
from research_tree.services.topics import TopicReviewService
from research_tree.services.workspaces import WorkspaceQueryService
from research_tree.workspace.repository import WorkspaceRepository, build_workspace_repository


REPO_ROOT = Path(__file__).resolve().parents[3]


def get_repository(request: Request) -> WorkspaceRepository:
    # The lifespan builds one repository per process. Constructing one here is
    # the fallback for apps created without it (tests build them directly).
    repository = getattr(request.app.state, "repository", None)
    if repository is not None:
        return repository
    return build_workspace_repository()


def get_workspace_query_service(
    repository: WorkspaceRepository = Depends(get_repository),
) -> WorkspaceQueryService:
    return WorkspaceQueryService(repository)


def get_workspace_edit_service(
    repository: WorkspaceRepository = Depends(get_repository),
) -> WorkspaceEditService:
    return WorkspaceEditService(repository)


def get_paper_annotation_service(
    repository: WorkspaceRepository = Depends(get_repository),
) -> PaperAnnotationService:
    return PaperAnnotationService(repository)


def get_workspace_agent_service(
    request: Request,
    repository: WorkspaceRepository = Depends(get_repository),
) -> WorkspaceAgentService:
    # Reuse the process-wide service: it owns the compiled graph, whose
    # checkpointer and node cache are worthless if rebuilt per request.
    service = getattr(request.app.state, "agent_service", None)
    if service is not None and service.repository is repository:
        return service
    return WorkspaceAgentService(repository)


def get_workspace_review_service(
    repository: WorkspaceRepository = Depends(get_repository),
) -> WorkspaceReviewService:
    # Approving a pipeline_rerun review starts a run, so the review service
    # needs the pipeline.
    return WorkspaceReviewService(
        repository,
        pipeline_service=WorkspacePipelineService(repository, repo_root=REPO_ROOT),
    )


def get_topic_review_service(
    repository: WorkspaceRepository = Depends(get_repository),
) -> TopicReviewService:
    return TopicReviewService(repository)


def get_workspace_pipeline_service(
    repository: WorkspaceRepository = Depends(get_repository),
) -> WorkspacePipelineService:
    return WorkspacePipelineService(repository, repo_root=REPO_ROOT)
