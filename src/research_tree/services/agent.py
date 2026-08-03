from __future__ import annotations

import logging
from typing import Any, Callable
from uuid import uuid4

from research_tree.agents.workspace.graph import build_workspace_agent_graph
from research_tree.agents.workspace.nodes import _visible_removal_target_ids
from research_tree.llm import DEFAULT_MODEL
from research_tree.agents.workspace.run import run_workspace_agent
from research_tree.services.errors import ReviewConflictError, WorkspaceNotFoundError
from research_tree.services.validation import validate_resource_id
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.diff import derive_operations_and_diff_summary
from research_tree.workspace.operations import (
    WorkspacePatchError,
    apply_structured_workspace_patch,
    remove_visible_paper_operation,
)
from research_tree.workspace.repository import WorkspaceRepository
from research_tree.workspace.validators import run_workspace_validator, select_workspace_validators


GraphFactory = Callable[[WorkspaceRepository], Any]
logger = logging.getLogger("uvicorn.error")


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
        conversation_history: list[dict[str, str]] | None = None,
        thread_id: str | None = None,
        allow_pipeline_rerun: bool = False,
        require_approval: bool = True,
        model: str = DEFAULT_MODEL,
    ) -> dict[str, Any]:
        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        try:
            workspace = self.repository.get_current_workspace(safe_workspace_id)
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

        simple_result = _run_simple_visible_paper_removal(
            self.repository,
            safe_workspace_id,
            workspace=workspace,
            message=message,
            thread_id=thread_id,
            require_approval=require_approval,
        )
        if simple_result is not None:
            return simple_result

        try:
            graph = self.graph_factory(self.repository)
            result = run_workspace_agent(
                {
                    "workspace_id": safe_workspace_id,
                    "user_message": message,
                    "conversation_history": _bounded_conversation_history(
                        conversation_history or []
                    ),
                    "thread_id": thread_id,
                    "allow_pipeline_rerun": allow_pipeline_rerun,
                    "require_approval": require_approval,
                    "agent_model": model,
                },
                graph=graph,
            )
        except Exception as error:  # API callers get a structured agent failure.
            logger.exception(
                "workspace assistant failed workspace_id=%s", safe_workspace_id
            )
            return {
                "workspace_id": safe_workspace_id,
                "status": "failed_exception",
                "final_response": "Assistant failed before completing the request.",
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


def _run_simple_visible_paper_removal(
    repository: WorkspaceRepository,
    workspace_id: str,
    *,
    workspace: dict[str, Any],
    message: str,
    thread_id: str | None,
    require_approval: bool,
) -> dict[str, Any] | None:
    target_paper_ids = _visible_removal_target_ids(
        workspace,
        user_message=message,
        next_action={},
    )
    if not target_paper_ids:
        return None
    structured_patch_operations = [
        remove_visible_paper_operation(paper_id=paper_id)
        for paper_id in target_paper_ids
    ]
    try:
        proposed_workspace = apply_structured_workspace_patch(
            base_workspace=workspace,
            operations=structured_patch_operations,
        )
    except WorkspacePatchError as error:
        logger.warning(
            "simple visible paper removal patch rejected workspace_id=%s error=%s",
            workspace_id,
            error,
        )
        return None

    operations, diff_summary, warnings = derive_operations_and_diff_summary(
        workspace=workspace,
        proposed_workspace=proposed_workspace,
    )
    validation_summary = _validate_simple_workspace_patch(
        workspace=workspace,
        proposed_workspace=proposed_workspace,
        operations=operations,
        diff_summary=diff_summary,
    )
    warnings = [*warnings, *validation_summary["warnings"]]
    agent_run_id = f"agent_run_{uuid4().hex}"
    active_thread_id = thread_id or f"workspace-agent:{workspace_id}:{uuid4().hex[:12]}"
    base_hash = workspace_version_hash(workspace)
    target_ids = _operation_target_ids(operations)

    if validation_summary["errors"]:
        run_event_id = _append_agent_run_event(
            repository,
            workspace_id,
            agent_run_id=agent_run_id,
            status="failed_validation",
            payload={
                "diff_summary": diff_summary,
                "validation_summary": validation_summary,
                "target_ids": target_ids,
            },
            actor_type="system",
            error_message="Simple paper removal failed validation.",
            errors=validation_summary["errors"],
            thread_id=active_thread_id,
            workspace_version_hash_value=base_hash,
        )
        return {
            "workspace_id": workspace_id,
            "status": "failed_validation",
            "thread_id": active_thread_id,
            "agent_run_id": agent_run_id,
            "final_response": "Paper removal failed validation.",
            "diff_summary": diff_summary,
            "validation_summary": validation_summary,
            "warnings": warnings,
            "errors": validation_summary["errors"],
            "persisted_event_ids": [run_event_id] if run_event_id else [],
        }

    if not require_approval:
        version_hash = repository.save_workspace_version(
            workspace_id,
            proposed_workspace,
            actor="agent",
            actor_type="agent",
            actor_id="workspace_agent",
            parent_version_hash=base_hash,
            reason=message,
            agent_run_id=agent_run_id,
        )
        event_id = repository.append_workspace_event(
            workspace_id,
            actor="agent",
            actor_type="agent",
            actor_id="workspace_agent",
            event_type="workspace_patch_approved_applied",
            target_ids=target_ids,
            before_hash=base_hash,
            after_hash=version_hash,
            payload={
                "agent_run_id": agent_run_id,
                "thread_id": active_thread_id,
                "proposed_operations": operations,
                "structured_patch_operations": structured_patch_operations,
                "diff_summary": diff_summary,
                "validation_summary": validation_summary,
            },
        )
        run_event_id = _append_agent_run_event(
            repository,
            workspace_id,
            agent_run_id=agent_run_id,
            status="approved_applied",
            payload={"version_hash": version_hash, "event_id": event_id},
            actor_type="agent",
            thread_id=active_thread_id,
            workspace_version_hash_value=base_hash,
        )
        return {
            "workspace_id": workspace_id,
            "status": "completed",
            "thread_id": active_thread_id,
            "agent_run_id": agent_run_id,
            "final_response": "Paper removed from the workspace.",
            "diff_summary": diff_summary,
            "validation_summary": validation_summary,
            "warnings": warnings,
            "errors": [],
            "persisted_event_ids": [
                event_id,
                *([run_event_id] if run_event_id else []),
            ],
        }

    review_id = f"review_{uuid4().hex}"
    interrupt_payload = {
        "type": "workspace_patch_review",
        "question": "Approve, edit, or reject this workspace change?",
        "diff_summary": diff_summary,
        "proposed_operations": operations,
        "structured_patch_operations": structured_patch_operations,
        "validation_summary": validation_summary,
        "warnings": warnings,
        "choices": ["approve", "edit", "reject"],
        "review_id": review_id,
    }
    repository.save_pending_review(
        workspace_id,
        review_id=review_id,
        agent_run_id=agent_run_id,
        base_workspace_version_hash=base_hash,
        user_message=message,
        proposed_workspace=proposed_workspace,
        proposed_operations=operations,
        diff_summary=diff_summary,
        validation_summary=validation_summary,
        interrupt_payload=interrupt_payload,
        structured_patch_operations=structured_patch_operations,
    )
    run_event_id = _append_agent_run_event(
        repository,
        workspace_id,
        agent_run_id=agent_run_id,
        status="pending_review",
        payload={"review_id": review_id},
        actor_type="agent",
        thread_id=active_thread_id,
        workspace_version_hash_value=base_hash,
    )
    return {
        "workspace_id": workspace_id,
        "status": "pending_review",
        "thread_id": active_thread_id,
        "agent_run_id": agent_run_id,
        "review_id": review_id,
        "interrupt_payload": interrupt_payload,
        "final_response": None,
        "diff_summary": diff_summary,
        "validation_summary": validation_summary,
        "warnings": warnings,
        "errors": [],
        "persisted_event_ids": [run_event_id] if run_event_id else [],
    }


def _validate_simple_workspace_patch(
    *,
    workspace: dict[str, Any],
    proposed_workspace: dict[str, Any],
    operations: list[dict[str, Any]],
    diff_summary: dict[str, Any],
) -> dict[str, Any]:
    validator_payload = {
        "workspace": workspace,
        "proposed_workspace": proposed_workspace,
        "proposed_operations": operations,
        "diff_summary": diff_summary,
    }
    results = [
        run_workspace_validator(validator_name=validator, payload=validator_payload)
        for validator in select_workspace_validators(
            proposed_operations=operations,
            diff_summary=diff_summary,
        )
    ]
    errors = [
        str(error)
        for result in results
        for error in result.get("errors", [])
    ]
    warnings = [
        str(warning)
        for result in results
        for warning in result.get("warnings", [])
    ]
    return {
        "valid": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "validators_run": [str(result.get("validator_name")) for result in results],
        "repair_attempts": 0,
        "validation_round": 1,
    }


def _append_agent_run_event(
    repository: WorkspaceRepository,
    workspace_id: str,
    *,
    agent_run_id: str,
    status: str,
    payload: dict[str, Any],
    actor_type: str,
    thread_id: str,
    workspace_version_hash_value: str,
    error_message: str | None = None,
    errors: list[str] | None = None,
) -> str | None:
    append_run_event = getattr(repository, "append_agent_run_event", None)
    if append_run_event is None:
        return None
    return append_run_event(
        workspace_id,
        agent_run_id=agent_run_id,
        status=status,
        actor_type=actor_type,
        error_message=error_message,
        errors=errors,
        payload={
            **payload,
            "thread_id": thread_id,
            "workspace_version_hash": workspace_version_hash_value,
        },
    )


def _operation_target_ids(operations: list[dict[str, Any]]) -> dict[str, Any]:
    branch_ids: set[str] = set()
    paper_ids: set[str] = set()
    path_ids: set[str] = set()
    operation_types: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        operation_types.add(str(operation.get("operation_type") or ""))
        target_ids = operation.get("target_ids")
        if not isinstance(target_ids, dict):
            continue
        for key, value in target_ids.items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                text = str(item)
                if "paper" in key:
                    paper_ids.add(text)
                elif "path" in key:
                    path_ids.add(text)
                elif "branch" in key:
                    branch_ids.add(text)
    return {
        "operation_types": sorted(item for item in operation_types if item),
        "branch_ids": sorted(branch_ids),
        "paper_ids": sorted(paper_ids),
        "path_ids": sorted(path_ids),
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
