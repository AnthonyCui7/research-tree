from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from research_tree.api.dependencies import get_workspace_agent_service
from research_tree.api.schemas import AgentRunRequest, AgentRunResponse
from research_tree.services.agent import WorkspaceAgentService
from research_tree.services.errors import (
    RateLimitedError,
    ServiceUnavailableError,
    WorkspaceServiceError,
    public_service_error_message,
)


router = APIRouter(prefix="/workspaces", tags=["agent"])
logger = logging.getLogger("uvicorn.error")

# The ingress closes a connection that has been silent for four minutes; a
# comment frame this often keeps a long assistant turn alive without
# meaning anything to the client.
KEEPALIVE_SECONDS = 15.0

# Turns run on threads of their own. A turn holds its thread for minutes,
# nearly all of it waiting on the model, and on the event loop's shared pool a
# handful of them left every short database read in the process queued behind
# them. The pool is sized for waiting rather than computing, a turn past it is
# refused rather than queued where nobody can see it, and no account holds
# more than its share.
AGENT_TURN_THREADS = 32
AGENT_TURNS_PER_ACCOUNT = 4
AT_CAPACITY_MESSAGE = "The assistant is at capacity right now. Try again in a minute."
ACCOUNT_AT_CAPACITY_MESSAGE = (
    "The assistant is already answering several of your messages. Wait for one to finish."
)


class _TurnThreads:
    def __init__(self, total: int, per_account: int) -> None:
        self._pool = ThreadPoolExecutor(max_workers=total, thread_name_prefix="agent-turn")
        self._total = total
        self._per_account = per_account
        self._running: Counter[str] = Counter()
        self._lock = Lock()

    async def run(self, owner_id: str, turn: Callable[[], Any]) -> Any:
        with self._lock:
            if self._running[owner_id] >= self._per_account:
                raise RateLimitedError(ACCOUNT_AT_CAPACITY_MESSAGE)
            if sum(self._running.values()) >= self._total:
                raise ServiceUnavailableError(AT_CAPACITY_MESSAGE)
            self._running[owner_id] += 1

        def run_and_give_back() -> Any:
            # The place is given back when the turn ends, not when the reader
            # stops listening: a turn whose client went away still runs.
            try:
                return turn()
            finally:
                with self._lock:
                    self._running[owner_id] -= 1
                    if self._running[owner_id] <= 0:
                        del self._running[owner_id]

        # `run_in_executor` does not carry the context over the way
        # `asyncio.to_thread` does, and the turn reads the bound account from it.
        context = contextvars.copy_context()
        return await asyncio.get_running_loop().run_in_executor(
            self._pool, context.run, run_and_give_back
        )


_turn_threads = _TurnThreads(AGENT_TURN_THREADS, AGENT_TURNS_PER_ACCOUNT)


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
    owner_id = service.repository.owner_id
    if "text/event-stream" not in (http_request.headers.get("accept") or ""):
        return await _turn_threads.run(owner_id, run)

    await asyncio.to_thread(service.ensure_agent_available, workspace_id)

    async def event_stream():
        loop = asyncio.get_running_loop()
        progress: asyncio.Queue[dict] = asyncio.Queue()

        def on_progress(event: dict) -> None:
            # Called on the turn's thread; the loop owns the queue.
            loop.call_soon_threadsafe(progress.put_nowait, event)

        task = asyncio.ensure_future(
            _turn_threads.run(owner_id, functools.partial(run, on_progress=on_progress))
        )
        next_event: asyncio.Future | None = None
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
            except Exception:  # noqa: BLE001 - the stream has started; it must still end in an event
                # Anything raised before the turn's own handling (a database
                # that did not answer, say). Left to propagate, the stream
                # just stopped and the reader was told the connection closed.
                logger.exception("assistant turn failed before it began workspace_id=%s", workspace_id)
                payload = {
                    "status": 500,
                    "detail": "We could not complete that request. Please try again.",
                    "error_code": "workspace_service_error",
                }
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                return
            yield f"event: result\ndata: {json.dumps(result, default=str)}\n\n"
        finally:
            if next_event is not None:
                next_event.cancel()
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
