from __future__ import annotations

import logging
from typing import Any, Callable

from research_tree.agents.workspace.graph import build_workspace_agent_graph
from research_tree.llm import DEFAULT_MODEL
from research_tree.agents.workspace.run import ProgressCallback, run_workspace_agent
from research_tree.rate_limits import check_rate_limit
from research_tree.services.errors import ReviewConflictError, WorkspaceNotFoundError
from research_tree.services.tenancy import require_owned
from research_tree.services.validation import validate_resource_id
from research_tree.workspace.repository import WorkspaceRepository


GraphFactory = Callable[[WorkspaceRepository], Any]
logger = logging.getLogger("uvicorn.error")


class WorkspaceAgentService:
    def __init__(
        self,
        repository: WorkspaceRepository,
        *,
        graph: Any | None = None,
        graph_factory: GraphFactory | None = None,
    ) -> None:
        self.repository = repository
        # A prebuilt graph is the production path: its checkpointer and node
        # cache only mean something when the graph outlives one request.
        self._graph = graph
        self.graph_factory = graph_factory or _default_graph_factory

    def ensure_agent_available(self, workspace_id: str) -> str:
        """Everything that can refuse a turn before any model call is made.

        The streaming route runs this first so a refusal is an ordinary error
        response rather than something buried in a stream that has started.
        """

        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        require_owned(self.repository, safe_workspace_id)
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
            raise ReviewConflictError(
                "Assistant is unavailable while the workspace pipeline is still running."
            )
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
        safe_workspace_id = self.ensure_agent_available(workspace_id)
        check_rate_limit("agent_turns")

        try:
            graph = self._graph or self.graph_factory(self.repository)
            result = run_workspace_agent(
                {
                    "workspace_id": safe_workspace_id,
                    "user_message": message,
                    "conversation_history": _bounded_conversation_history(
                        conversation_history or []
                    ),
                    "thread_id": thread_id,
                    "allow_pipeline_rerun": allow_pipeline_rerun,
                    "agent_model": model,
                },
                graph=graph,
                on_progress=on_progress,
            )
        except Exception:  # API callers get a structured agent failure.
            # The traceback goes to the log, not to the client: exception text
            # from anywhere in the graph can carry internal paths and payloads.
            logger.exception(
                "workspace assistant failed workspace_id=%s", safe_workspace_id
            )
            return {
                "workspace_id": safe_workspace_id,
                "status": "failed_exception",
                # The request's thread id, so the client can continue the
                # conversation after a failed turn.
                "thread_id": thread_id,
                "final_response": "Assistant failed before completing the request.",
                "errors": ["The assistant could not complete that request. Try again."],
                "warnings": [],
            }

        output = result.final_output or {}
        normalized_status = _normalized_status(output, interrupted=result.interrupted)
        interrupt_payload = _interrupt_payload(output, result.interrupt_payloads)
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


def _default_graph_factory(repository: WorkspaceRepository) -> Any:
    return build_workspace_agent_graph(workspace_repository=repository)


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


def _normalized_status(output: dict[str, Any], *, interrupted: bool) -> str:
    if _is_guardrail_failure(output):
        return "failed_guardrail"
    if interrupted or output.get("approval_required") or output.get("review_status") == "pending":
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


def _interrupt_payload(
    output: dict[str, Any],
    interrupt_payloads: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if interrupt_payloads:
        return interrupt_payloads[0]
    payload = output.get("approval_payload")
    return payload if isinstance(payload, dict) else None
