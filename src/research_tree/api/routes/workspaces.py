from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from research_tree.api.dependencies import (
    get_paper_annotation_service,
    get_topic_review_service,
    get_workspace_edit_service,
    get_workspace_pipeline_service,
    get_workspace_query_service,
)
from research_tree.api.schemas import (
    AnnotationJobResponse,
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
    WorkspaceEditRequest,
    WorkspaceEditResponse,
    WorkspaceEventsResponse,
    WorkspaceMutationResponse,
    WorkspaceResponse,
    WorkspaceReviewsResponse,
    WorkspaceVersionsResponse,
    WorkspacesResponse,
)
from research_tree.auth.settings import SESSION_COOKIE_NAME
from research_tree.principal import current_principal
from research_tree.redis_client import get_async_redis
from research_tree.services.annotations import PaperAnnotationService
from research_tree.services.edits import WorkspaceEditService
from research_tree.services.pipeline import WorkspacePipelineService
from research_tree.services.topics import TopicReviewService
from research_tree.services.workspaces import WorkspaceQueryService
from research_tree.workspace.postgres_repository import RUN_CHANNEL_PREFIX, workspaces_channel


logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/workspaces", tags=["workspaces"])

ACTIVE_PIPELINE_RUN_STATUSES = {"queued", "running"}
RUN_POLL_SECONDS = 0.5
COLLECTION_POLL_SECONDS = 1.0
# With Redis the repository announces every change, so the streams re-read on
# a message and only fall back to a slow poll in case one is missed.
NOTIFIED_POLL_SECONDS = 10.0
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


@router.get("/pipeline-runs/active", response_model=PipelineRunsResponse)
def list_active_pipeline_runs(
    service: WorkspacePipelineService = Depends(get_workspace_pipeline_service),
) -> dict[str, object]:
    return service.list_active_runs()


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
        async with _ChangeSignal(f"{RUN_CHANNEL_PREFIX}{run_id}", RUN_POLL_SECONDS) as changes:
            while True:
                if await request.is_disconnected() or not await _still_signed_in(request):
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
                since_heartbeat += await changes.wait()
                if since_heartbeat >= HEARTBEAT_SECONDS:
                    since_heartbeat = 0.0
                    yield ": heartbeat\n\n"
                # Repository reads leave the event loop; the database has its own pool.
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
        channel = workspaces_channel(service.repository.owner_id)
        async with _ChangeSignal(channel, COLLECTION_POLL_SECONDS) as changes:
            while True:
                if await request.is_disconnected() or not await _still_signed_in(request):
                    return
                workspaces = await asyncio.to_thread(_workspace_collection_signature, service)
                if workspaces != previous_signature:
                    previous_signature = workspaces
                    since_heartbeat = 0.0
                    yield f"event: workspaces_updated\ndata: {workspaces}\n\n"
                since_heartbeat += await changes.wait()
                if since_heartbeat >= HEARTBEAT_SECONDS:
                    since_heartbeat = 0.0
                    yield ": heartbeat\n\n"

    return _sse_response(event_stream())


async def _still_signed_in(request: Request) -> bool:
    """Whether the account behind an open stream may still read.

    Authorization happens once, when the stream connects, and a stream stays
    open for as long as the tab does. Without this an account that was
    deactivated, dropped from the allowlist, or signed out everywhere went on
    receiving every change to its workspaces; new requests were refused the
    whole time.
    """

    principal = current_principal()
    if principal is None or principal.is_local:
        return True
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return await asyncio.to_thread(_account_may_read, principal.user_id, token)


def _account_may_read(user_id: str, token: str | None) -> bool:
    from research_tree.auth.accounts import principal_for_user_id, session_is_live
    from research_tree.auth.settings import email_is_allowed

    live = principal_for_user_id(user_id)
    return live is not None and email_is_allowed(live.email) and session_is_live(user_id, token)


class _ChangeSignal:
    """Waits for the next change on a channel, or for the poll interval.

    Subscribed through Redis when it is configured (the repository publishes
    after every commit); otherwise, or if Redis fails mid-stream, a plain
    sleep at the fast poll interval. `wait` returns the seconds it waited so
    the caller can keep its heartbeat cadence.
    """

    def __init__(self, channel: str, poll_seconds: float) -> None:
        self._channel = channel
        self._poll_seconds = poll_seconds
        self._pubsub = None

    async def __aenter__(self) -> "_ChangeSignal":
        client = get_async_redis()
        if client is not None:
            try:
                pubsub = client.pubsub()
                await pubsub.subscribe(self._channel)
                self._pubsub = pubsub
            except Exception as error:  # noqa: BLE001 - polling is the fallback
                logger.warning("event stream falls back to polling: %s", error)
                self._pubsub = None
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(self._channel)
                await self._pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def wait(self) -> float:
        if self._pubsub is None:
            await asyncio.sleep(self._poll_seconds)
            return self._poll_seconds
        started = asyncio.get_running_loop().time()
        try:
            await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=NOTIFIED_POLL_SECONDS
            )
        except Exception as error:  # noqa: BLE001 - keep serving, just poll from here on
            logger.warning("event stream lost its Redis subscription: %s", error)
            # Dropping the reference without closing leaked the connection for
            # as long as the stream stayed open, which is until the tab closes.
            failed, self._pubsub = self._pubsub, None
            try:
                await failed.aclose()
            except Exception:  # noqa: BLE001 - it is already broken
                pass
            await asyncio.sleep(self._poll_seconds)
        return asyncio.get_running_loop().time() - started


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


@router.post("/{workspace_id}/edits", response_model=WorkspaceEditResponse)
def edit_workspace(
    workspace_id: str,
    request: WorkspaceEditRequest,
    service: WorkspaceEditService = Depends(get_workspace_edit_service),
) -> dict[str, object]:
    """Apply a hand edit and publish it as a version the reader authored.

    Answers 409 when the workspace moved past `expected_version_hash`, and 400
    when an operation cannot apply or the result fails the validators.
    """

    return service.apply(
        workspace_id,
        operations=request.operations,
        expected_version_hash=request.expected_version_hash,
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


# Annotating a paper for the first time is minutes of model calls. Without a
# queue this is a plain request the reader waits on (handled off the event
# loop by FastAPI, since the work is blocking); with one, a miss answers 202
# with the job the worker is running, and the reader polls it below.
@router.get("/{workspace_id}/paper-annotations", response_model=PaperAnnotationsResponse)
def get_paper_annotations(
    workspace_id: str,
    paper_id: str,
    mode: str | None = None,
    refresh: bool = False,
    service: PaperAnnotationService = Depends(get_paper_annotation_service),
):
    result = service.get_paper_annotations(workspace_id, paper_id, mode=mode, refresh=refresh)
    if "job_id" in result:
        return JSONResponse(status_code=202, content=result)
    return result


@router.get(
    "/{workspace_id}/paper-annotations/jobs/{job_id}", response_model=AnnotationJobResponse
)
def get_paper_annotation_job(
    workspace_id: str,
    job_id: str,
    service: PaperAnnotationService = Depends(get_paper_annotation_service),
) -> dict[str, object]:
    return service.annotation_job(workspace_id, job_id)


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
