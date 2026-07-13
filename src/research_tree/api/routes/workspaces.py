from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from research_tree.api.dependencies import (
    get_topic_review_service,
    get_workspace_pipeline_service,
    get_workspace_query_service,
)
from research_tree.api.schemas import (
    CreateWorkspaceRequest,
    DeleteWorkspaceRequest,
    PipelineRerunApiRequest,
    PipelineRunResponse,
    PipelineRunsResponse,
    RestoreWorkspaceRequest,
    TopicReviewRequest,
    TopicReviewResponse,
    WorkspaceEventsResponse,
    WorkspaceMutationResponse,
    WorkspaceResponse,
    WorkspaceReviewsResponse,
    WorkspaceVersionsResponse,
    WorkspacesResponse,
)
from research_tree.services.pipeline import WorkspacePipelineService
from research_tree.services.topics import TopicReviewService
from research_tree.services.workspaces import WorkspaceQueryService


router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("/topic-review", response_model=TopicReviewResponse)
def review_workspace_topic(
    request: TopicReviewRequest,
    service: TopicReviewService = Depends(get_topic_review_service),
) -> dict[str, object]:
    return service.review(request.topic)


@router.post("", response_model=PipelineRunResponse, status_code=202)
def create_workspace(
    request: CreateWorkspaceRequest,
    service: WorkspacePipelineService = Depends(get_workspace_pipeline_service),
) -> dict[str, object]:
    return {
        "pipeline_run": service.start_approved_new_workspace(
            topic=request.topic,
            topic_review_token=request.topic_review_token,
            model=request.model,
        )
    }


@router.get("/pipeline-runs/{run_id}", response_model=PipelineRunResponse)
def get_pipeline_run(
    run_id: str,
    service: WorkspacePipelineService = Depends(get_workspace_pipeline_service),
) -> dict[str, object]:
    return {"pipeline_run": service.get_run(run_id)}


@router.post("/pipeline-runs/{run_id}/cancel", response_model=PipelineRunResponse)
def cancel_pipeline_run(
    run_id: str,
    service: WorkspacePipelineService = Depends(get_workspace_pipeline_service),
) -> dict[str, object]:
    return {"pipeline_run": service.cancel_run(run_id)}


@router.get("/pipeline-runs/{run_id}/events")
async def stream_pipeline_run_updates(
    run_id: str,
    service: WorkspacePipelineService = Depends(get_workspace_pipeline_service),
) -> StreamingResponse:
    async def event_stream():
        previous_signature: str | None = None
        while True:
            run = service.get_run(run_id)
            signature = json.dumps(run, sort_keys=True, default=str)
            if signature != previous_signature:
                previous_signature = signature
                yield f"event: pipeline_run_updated\ndata: {signature}\n\n"
            if run.get("status") not in {"queued", "running"}:
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/events/stream")
async def stream_workspace_updates(
    service: WorkspaceQueryService = Depends(get_workspace_query_service),
) -> StreamingResponse:
    async def event_stream():
        previous_signature: str | None = None
        while True:
            workspaces = service.list_workspaces()["workspaces"]
            signature = json.dumps(
                [
                    {
                        "workspace_id": item.get("workspace_id"),
                        "workspace_version_hash": item.get("workspace_version_hash"),
                    }
                    for item in workspaces
                ],
                sort_keys=True,
            )
            if signature != previous_signature:
                previous_signature = signature
                yield f"event: workspaces_updated\ndata: {signature}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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


@router.delete("/{workspace_id}", response_model=WorkspaceMutationResponse)
def delete_workspace(
    workspace_id: str,
    request: DeleteWorkspaceRequest | None = None,
    service: WorkspaceQueryService = Depends(get_workspace_query_service),
) -> dict[str, object]:
    return service.delete_workspace(
        workspace_id,
        expected_version_hash=(request or DeleteWorkspaceRequest()).expected_version_hash,
    )


@router.get("/{workspace_id}/versions", response_model=WorkspaceVersionsResponse)
def list_workspace_versions(
    workspace_id: str,
    service: WorkspaceQueryService = Depends(get_workspace_query_service),
) -> dict[str, object]:
    return service.list_versions(workspace_id)


@router.get("/{workspace_id}/versions/{version_hash}", response_model=WorkspaceResponse)
def get_workspace_version(
    workspace_id: str,
    version_hash: str,
    service: WorkspaceQueryService = Depends(get_workspace_query_service),
) -> dict[str, object]:
    return service.get_version(workspace_id, version_hash)


@router.post(
    "/{workspace_id}/versions/{version_hash}/restore",
    response_model=WorkspaceMutationResponse,
)
def restore_workspace_version(
    workspace_id: str,
    version_hash: str,
    request: RestoreWorkspaceRequest | None = None,
    service: WorkspaceQueryService = Depends(get_workspace_query_service),
) -> dict[str, object]:
    restore = request or RestoreWorkspaceRequest()
    return service.restore_version(
        workspace_id,
        version_hash,
        expected_version_hash=restore.expected_version_hash,
        reason=restore.reason,
    )


@router.get("/{workspace_id}/pipeline-runs", response_model=PipelineRunsResponse)
def list_pipeline_runs(
    workspace_id: str,
    service: WorkspacePipelineService = Depends(get_workspace_pipeline_service),
) -> dict[str, object]:
    return service.list_runs(workspace_id)


@router.post("/{workspace_id}/pipeline-runs", response_model=PipelineRunResponse, status_code=202)
def rerun_workspace_pipeline(
    workspace_id: str,
    request: PipelineRerunApiRequest,
    service: WorkspacePipelineService = Depends(get_workspace_pipeline_service),
) -> dict[str, object]:
    return {
        "pipeline_run": service.rerun(
            workspace_id,
            start_stage=request.start_stage,
            model=request.model,
            expected_version_hash=request.expected_version_hash,
        )
    }


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
