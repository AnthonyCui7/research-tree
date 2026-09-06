from __future__ import annotations

import asyncio
import functools
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from research_tree.api.dependencies import get_workspace_agent_service
from research_tree.api.schemas import AgentRunRequest, AgentRunResponse
from research_tree.services.agent import WorkspaceAgentService
from research_tree.services.errors import WorkspaceServiceError, public_service_error_message


router = APIRouter(prefix="/workspaces", tags=["agent"])

# The ingress closes a connection that has been silent for four minutes; a
# comment frame this often keeps a long assistant turn alive without
# meaning anything to the client.
KEEPALIVE_SECONDS = 15.0


@router.post("/{workspace_id}/agent", response_model=AgentRunResponse)
async def run_workspace_agent(
    workspace_id: str,
    request: AgentRunRequest,
    http_request: Request,
    service: WorkspaceAgentService = Depends(get_workspace_agent_service),
):
    """One assistant turn.

    With `Accept: text/event-stream` the turn is narrated as it runs: one
    `progress` event per model turn, tool call, and stage of an edit (see
    `agents/workspace/nodes.py`, "progress"), keepalive comments in the
    silences, and the answer as one `result` event, so a turn can outlast the
    proxy's idle timeout. Otherwise it is a plain JSON response. Refusals
    (unknown workspace, a build in progress) are ordinary error responses
    either way.
    """

    run = functools.partial(
        service.run_agent,
        workspace_id,
        message=request.message,
        conversation_history=request.conversation_history,
        thread_id=request.thread_id,
        allow_pipeline_rerun=request.allow_pipeline_rerun,
        model=request.model,
    )
    if "text/event-stream" not in (http_request.headers.get("accept") or ""):
        return await asyncio.to_thread(run)

    await asyncio.to_thread(service.ensure_agent_available, workspace_id)

    async def event_stream():
        loop = asyncio.get_running_loop()
        progress: asyncio.Queue[dict] = asyncio.Queue()

        def on_progress(event: dict) -> None:
            # Called on the turn's thread; the loop owns the queue.
            loop.call_soon_threadsafe(progress.put_nowait, event)

        task = asyncio.ensure_future(asyncio.to_thread(run, on_progress=on_progress))
        try:
            while True:
                next_event = asyncio.ensure_future(progress.get())
                done, _ = await asyncio.wait(
                    {task, next_event},
                    timeout=KEEPALIVE_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if next_event in done:
                    yield f"event: progress\ndata: {json.dumps(next_event.result())}\n\n"
                    continue
                next_event.cancel()
                if task in done:
                    # Every event the turn reported was queued before its
                    # thread returned, so the queue is already drained here.
                    break
                yield ": keepalive\n\n"
            try:
                result = task.result()
            except WorkspaceServiceError as error:
                payload = {
                    "status": error.status_code,
                    "detail": public_service_error_message(error),
                    "error_code": error.error_code,
                }
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                return
            yield f"event: result\ndata: {json.dumps(result, default=str)}\n\n"
        finally:
            if not task.done():
                # The client went away; the turn finishes on its thread and
                # anything it persisted is restored on the next load.
                task.add_done_callback(lambda finished: finished.exception())

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
