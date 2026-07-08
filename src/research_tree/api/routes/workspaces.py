from __future__ import annotations

from fastapi import APIRouter, Depends

from research_tree.api.dependencies import get_workspace_query_service
from research_tree.api.schemas import (
    WorkspaceEventsResponse,
    WorkspaceResponse,
    WorkspaceReviewsResponse,
    WorkspaceVersionsResponse,
    WorkspacesResponse,
)
from research_tree.services.workspaces import WorkspaceQueryService


router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=WorkspacesResponse)
def list_workspaces(
    service: WorkspaceQueryService = Depends(get_workspace_query_service),
) -> dict[str, object]:
    return service.list_workspaces()


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(
    workspace_id: str,
    service: WorkspaceQueryService = Depends(get_workspace_query_service),
) -> dict[str, object]:
    return service.get_current_workspace(workspace_id)


@router.get("/{workspace_id}/versions", response_model=WorkspaceVersionsResponse)
def list_workspace_versions(
    workspace_id: str,
    service: WorkspaceQueryService = Depends(get_workspace_query_service),
) -> dict[str, object]:
    return service.list_versions(workspace_id)


@router.get("/{workspace_id}/events", response_model=WorkspaceEventsResponse)
def list_workspace_events(
    workspace_id: str,
    service: WorkspaceQueryService = Depends(get_workspace_query_service),
) -> dict[str, object]:
    return service.list_events(workspace_id)


@router.get("/{workspace_id}/reviews", response_model=WorkspaceReviewsResponse)
def list_workspace_reviews(
    workspace_id: str,
    service: WorkspaceQueryService = Depends(get_workspace_query_service),
) -> dict[str, object]:
    return service.list_reviews(workspace_id)
