from __future__ import annotations

from typing import Any, Callable

from research_tree.agents.workspace.graph import build_workspace_agent_graph
from research_tree.agents.workspace.run import run_workspace_agent
from research_tree.services.errors import WorkspaceNotFoundError
from research_tree.services.validation import validate_resource_id
from research_tree.workspace.repository import WorkspaceRepository


GraphFactory = Callable[[WorkspaceRepository], Any]


class WorkspaceAgentService:
    def __init__(
        self,
        repository: WorkspaceRepository,
        *,
        graph_factory: GraphFactory | None = None,
    ) -> None:
        self.repository = repository
        self.graph_factory = graph_factory or _default_graph_factory

    def run_agent(
        self,
        workspace_id: str,
        *,
        message: str,
        thread_id: str | None = None,
        allow_pipeline_rerun: bool = False,
        require_approval: bool = True,
    ) -> dict[str, Any]:
        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        try:
            self.repository.get_current_workspace(safe_workspace_id)
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError(
                f"workspace does not exist: {safe_workspace_id}"
            ) from error

        try:
            graph = self.graph_factory(self.repository)
            result = run_workspace_agent(
                {
                    "workspace_id": safe_workspace_id,
                    "user_message": message,
                    "thread_id": thread_id,
                    "allow_pipeline_rerun": allow_pipeline_rerun,
                    "require_approval": require_approval,
                },
                graph=graph,
            )
        except Exception as error:  # API callers get a structured agent failure.
            return {
                "workspace_id": safe_workspace_id,
                "status": "failed_exception",
                "final_response": "Workspace agent failed before completing the request.",
                "errors": [str(error)],
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


def _normalized_status(output: dict[str, Any], *, interrupted: bool) -> str:
    if _is_guardrail_failure(output):
        return "failed_guardrail"
    if interrupted or output.get("approval_required") or output.get("review_status") == "pending":
        return "pending_review"
    status = str(output.get("status") or "")
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed_exception"
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

