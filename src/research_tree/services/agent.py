from __future__ import annotations

import logging
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from research_tree.agents.workspace.graph import build_workspace_agent_graph
from research_tree.llm import AGENT_MODELS, DEFAULT_MODEL
from research_tree.agents.workspace.run import ProgressCallback, run_workspace_agent
from research_tree.services.errors import (
    InvalidPayloadError,
    WorkspaceBusyError,
    WorkspaceNotFoundError,
    WorkspaceServiceError,
)
from research_tree.services.validation import validate_resource_id
from research_tree.workspace.repository import WorkspaceRepository


GraphFactory = Callable[[WorkspaceRepository], Any]
logger = logging.getLogger("uvicorn.error")

# Threads with a turn in flight, in this process. A second turn on the same
# thread would run from the same checkpoint and the last one to finish would
# overwrite the other's state, so it is refused instead. One replica serves
# the site, which is what makes a process-wide set enough.
_active_threads: set[str] = set()
_active_threads_lock = Lock()
BUSY_THREAD_MESSAGE = "The assistant is still answering the previous message. Wait for it to finish."
BUILD_IN_PROGRESS_MESSAGE = "The assistant is unavailable while the workspace is being built."


class WorkspaceAgentService:
    """One account's assistant.

    The graph is compiled per request around that account's repository, which
    is what keeps its nodes from reading another account's workspaces. What
    has to outlive a request is shared through `checkpointer` (conversation
    state) and `cache` (the workspace context a node builds); both are keyed by
    content, not by request.
    """

    def __init__(
        self,
        repository: WorkspaceRepository,
        *,
        checkpointer: Any = None,
        cache: Any = None,
        graph: Any | None = None,
        graph_factory: GraphFactory | None = None,
    ) -> None:
        self.repository = repository
        self._checkpointer = checkpointer
        self._cache = cache
        self._graph = graph
        self.graph_factory = graph_factory

    def _graph_for_turn(self) -> Any:
        if self._graph is not None:
            return self._graph
        if self.graph_factory is not None:
            return self.graph_factory(self.repository)
        return build_workspace_agent_graph(
            workspace_repository=self.repository,
            checkpointer=self._checkpointer,
            cache=self._cache,
        )

    def thread_id(self, workspace_id: str, requested: str | None) -> str:
        """The conversation thread a turn continues, or a new one.

        Threads are stored by id alone, so the id carries the account and the
        workspace it belongs to, and a request naming a thread outside its own
        workspace is refused rather than resumed. Nothing reads the id back;
        it only has to be unique and its own.
        """

        scope = f"{self.repository.owner_id}:{workspace_id}"
        if requested is None:
            return f"{scope}:{uuid4().hex[:12]}"
        # The scope is compared whole, not as a prefix: workspace ids may
        # contain ':', so `a:b:x` is a thread of workspace `a:b`, not of `a`.
        if requested.rsplit(":", 1)[0] != scope:
            raise InvalidPayloadError("thread_id does not belong to this workspace.")
        return requested

    def ensure_agent_available(self, workspace_id: str) -> str:
        """Everything that can refuse a turn before any model call is made.

        The streaming route runs this first so a refusal is an ordinary error
        response rather than something buried in a stream that has started.
        """

        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        try:
            self.repository.get_current_workspace(safe_workspace_id)
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError(
                f"workspace does not exist: {safe_workspace_id}"
            ) from error
        active_pipeline_run = next(
            (
                run
                for run in self.repository.list_pipeline_runs(safe_workspace_id)
                if run.get("status") in {"queued", "running"}
            ),
            None,
        )
        if active_pipeline_run is not None:
            raise WorkspaceBusyError(BUILD_IN_PROGRESS_MESSAGE)
        return safe_workspace_id

    def run_agent(
        self,
        workspace_id: str,
        *,
        message: str,
        conversation_history: list[dict[str, str]] | None = None,
        thread_id: str | None = None,
        allow_pipeline_rerun: bool = False,
        model: str = DEFAULT_MODEL,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if model not in AGENT_MODELS:
            raise InvalidPayloadError(f"model must be one of {', '.join(AGENT_MODELS)}.")
        safe_workspace_id = self.ensure_agent_available(workspace_id)
        active_thread_id = self.thread_id(safe_workspace_id, thread_id)

        with _active_threads_lock:
            if active_thread_id in _active_threads:
                raise WorkspaceBusyError(BUSY_THREAD_MESSAGE)
            _active_threads.add(active_thread_id)
        try:
            result = run_workspace_agent(
                {
                    "workspace_id": safe_workspace_id,
                    "user_message": message,
                    "conversation_history": _bounded_conversation_history(
                        conversation_history or []
                    ),
                    "thread_id": active_thread_id,
                    "allow_pipeline_rerun": allow_pipeline_rerun,
                    "agent_model": model,
                },
                graph=self._graph_for_turn(),
                on_progress=on_progress,
            )
        except WorkspaceServiceError:
            # A refusal this codebase wrote — no key, allowance spent, rate
            # limited — already says what to do about it. Swallowing it into
            # "try again" told people to retry something that cannot succeed.
            raise
        except Exception:  # API callers get a structured agent failure.
            # The traceback goes to the log, not to the client: exception text
            # from anywhere in the graph can carry internal paths and payloads.
            logger.exception(
                "workspace assistant failed workspace_id=%s", safe_workspace_id
            )
            return {
                "workspace_id": safe_workspace_id,
                "status": "failed_exception",
                # The turn's thread id, so the client can continue the
                # conversation after a failed turn.
                "thread_id": active_thread_id,
                "final_response": "Assistant failed before completing the request.",
                "errors": ["The assistant could not complete that request. Try again."],
                "warnings": [],
            }
        finally:
            with _active_threads_lock:
                _active_threads.discard(active_thread_id)

        output = result.final_output or {}
        normalized_status = _normalized_status(output)
        approval_payload = output.get("approval_payload")
        interrupt_payload = approval_payload if isinstance(approval_payload, dict) else None
        return {
            "workspace_id": safe_workspace_id,
            "status": normalized_status,
            "thread_id": result.thread_id,
            "agent_run_id": output.get("agent_run_id"),
            "review_id": output.get("review_id")
            or (interrupt_payload or {}).get("review_id"),
            "interrupt_payload": interrupt_payload,
            "final_response": output.get("final_response"),
            "diff_summary": output.get("diff_summary"),
            "validation_summary": output.get("validation_summary"),
            "warnings": output.get("warnings") or [],
            "errors": output.get("errors") or [],
            "persisted_event_ids": output.get("persisted_event_ids") or [],
        }


def _bounded_conversation_history(
    history: list[dict[str, str]],
    *,
    max_turns: int = 12,
    max_characters_per_turn: int = 4_000,
) -> list[dict[str, str]]:
    bounded: list[dict[str, str]] = []
    for item in history[-max_turns:]:
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        text = str(item.get("text") or "")[:max_characters_per_turn].strip()
        if text:
            bounded.append({"role": role, "text": text})
    return bounded


def _normalized_status(output: dict[str, Any]) -> str:
    if _is_guardrail_failure(output):
        return "failed_guardrail"
    if output.get("approval_required") or output.get("review_status") == "pending":
        return "pending_review"
    status = str(output.get("status") or "")
    validation_summary = output.get("validation_summary")
    if status == "failed" and isinstance(validation_summary, dict):
        if validation_summary.get("valid") is False:
            return "failed_validation"
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    return status or "failed_exception"


def _is_guardrail_failure(output: dict[str, Any]) -> bool:
    guardrail = output.get("retrieval_guardrail_result")
    if isinstance(guardrail, dict) and guardrail.get("allowed") is False:
        return True
    for trace_item in output.get("node_trace") or []:
        if isinstance(trace_item, dict) and trace_item.get("node") == "answer_with_guardrail_rejection":
            return True
    return False

