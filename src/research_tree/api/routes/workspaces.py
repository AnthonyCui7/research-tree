from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from research_tree.api.dependencies import (
    get_paper_annotation_service,
    get_topic_review_service,
    get_workspace_pipeline_service,
    get_workspace_query_service,
)
from research_tree.api.schemas import (
    CreateWorkspaceRequest,
    DeleteWorkspaceRequest,
    PaperAnnotationsResponse,
    PaperContentResponse,
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
from research_tree.services.annotations import PaperAnnotationService
from research_tree.services.pipeline import WorkspacePipelineService
from research_tree.services.topics import TopicReviewService
from research_tree.services.workspaces import WorkspaceQueryService


router = APIRouter(prefix="/workspaces", tags=["workspaces"])

ACTIVE_PIPELINE_RUN_STATUSES = {"queued", "running"}
RUN_POLL_SECONDS = 0.5
COLLECTION_POLL_SECONDS = 1.0
# Proxies drop idle connections; a comment line keeps them open without
# looking like an event to the client.
HEARTBEAT_SECONDS = 15.0


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
            instructions=request.instructions,
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
    request: Request,
    service: WorkspacePipelineService = Depends(get_workspace_pipeline_service),
) -> StreamingResponse:
    # Read once up front so an unknown run answers 404 through the normal error
    # handler. Raising inside the generator would break a response that has
    # already started streaming.
    first_run = await asyncio.to_thread(service.get_run, run_id)

    async def event_stream():
        previous_signature: str | None = None
        run = first_run
        since_heartbeat = 0.0
        while True:
            if await request.is_disconnected():
                return
            signature = json.dumps(run, sort_keys=True, default=str)
            if signature != previous_signature:
                previous_signature = signature
                since_heartbeat = 0.0
                yield f"event: pipeline_run_updated\ndata: {signature}\n\n"
            if run.get("status") not in ACTIVE_PIPELINE_RUN_STATUSES:
                # Say the stream is over on purpose. EventSource treats a closed
                # connection as a dropped one and reconnects forever otherwise.
                yield "event: stream_complete\ndata: {}\n\n"
                return
            await asyncio.sleep(RUN_POLL_SECONDS)
            since_heartbeat += RUN_POLL_SECONDS
            if since_heartbeat >= HEARTBEAT_SECONDS:
                since_heartbeat = 0.0
                yield ": heartbeat\n\n"
            # Repository reads hit the filesystem; keep them off the event loop.
            run = await asyncio.to_thread(service.get_run, run_id)

    return _sse_response(event_stream())


@router.get("/events/stream")
async def stream_workspace_updates(
    request: Request,
    service: WorkspaceQueryService = Depends(get_workspace_query_service),
) -> StreamingResponse:
    async def event_stream():
        previous_signature: str | None = None
        since_heartbeat = 0.0
        while True:
            if await request.is_disconnected():
                return
            workspaces = await asyncio.to_thread(_workspace_collection_signature, service)
            if workspaces != previous_signature:
                previous_signature = workspaces
                since_heartbeat = 0.0
                yield f"event: workspaces_updated\ndata: {workspaces}\n\n"
            await asyncio.sleep(COLLECTION_POLL_SECONDS)
            since_heartbeat += COLLECTION_POLL_SECONDS
            if since_heartbeat >= HEARTBEAT_SECONDS:
                since_heartbeat = 0.0
                yield ": heartbeat\n\n"

    return _sse_response(event_stream())


def _workspace_collection_signature(service: WorkspaceQueryService) -> str:
    workspaces = service.list_workspaces()["workspaces"]
    return json.dumps(
        [
            {
                "workspace_id": item.get("workspace_id"),
                "workspace_version_hash": item.get("workspace_version_hash"),
            }
            for item in workspaces
        ],
        sort_keys=True,
    )


def _sse_response(event_stream) -> StreamingResponse:
    return StreamingResponse(
        event_stream,
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
    expected_version_hash: str | None = None,
    request: DeleteWorkspaceRequest | None = None,
    service: WorkspaceQueryService = Depends(get_workspace_query_service),
) -> dict[str, object]:
    # The hash travels as a query parameter because intermediaries are entitled
    # to drop a DELETE body. The body form stays accepted for older callers.
    return service.delete_workspace(
        workspace_id,
        expected_version_hash=(
            expected_version_hash
            or (request or DeleteWorkspaceRequest()).expected_version_hash
        ),
    )


# The paper id travels as a query parameter because it can be a DOI or a title —
# values carrying slashes and spaces that no single path segment can hold.
@router.get("/{workspace_id}/paper-content", response_model=PaperContentResponse)
def get_paper_content(
    workspace_id: str,
    paper_id: str,
    service: WorkspaceQueryService = Depends(get_workspace_query_service),
) -> dict[str, object]:
    return service.get_paper_content(workspace_id, paper_id)


@router.get("/{workspace_id}/paper-pdf")
def get_paper_pdf(
    workspace_id: str,
    paper_id: str,
    service: PaperAnnotationService = Depends(get_paper_annotation_service),
) -> Response:
    filename, pdf_bytes = service.get_paper_pdf(workspace_id, paper_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# Annotating a paper for the first time is minutes of model calls, so this is
# written as a plain request the reader waits on rather than a run to poll.
# Handled off the event loop by FastAPI, since the work is blocking.
@router.get("/{workspace_id}/paper-annotations", response_model=PaperAnnotationsResponse)
def get_paper_annotations(
    workspace_id: str,
    paper_id: str,
    service: PaperAnnotationService = Depends(get_paper_annotation_service),
) -> dict[str, object]:
    return service.get_paper_annotations(workspace_id, paper_id)


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
