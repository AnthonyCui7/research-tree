from __future__ import annotations

from typing import Any
from uuid import uuid4

from research_tree.agents.workspace.models import WorkspaceValidationSummary
from research_tree.services.errors import (
    ReviewConflictError,
    ReviewNotFoundError,
    StaleWorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceServiceError,
)
from research_tree.services.validation import (
    as_mapping,
    operation_target_ids,
    validate_resource_id,
)
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.diff import derive_operations_and_diff_summary
from research_tree.workspace.repository import WorkspaceRepository
from research_tree.workspace.validators import (
    run_workspace_validator,
    select_workspace_validators,
)


class WorkspaceReviewService:
    def __init__(self, repository: WorkspaceRepository) -> None:
        self.repository = repository

    def get_review(self, workspace_id: str, review_id: str) -> dict[str, Any]:
        safe_workspace_id, safe_review_id = self._safe_ids(workspace_id, review_id)
        self._load_current(safe_workspace_id)
        try:
            return self.repository.get_review(safe_workspace_id, safe_review_id)
        except FileNotFoundError as error:
            raise ReviewNotFoundError(
                f"review does not exist: {safe_review_id}"
            ) from error

    def approve_review(
        self,
        workspace_id: str,
        review_id: str,
        *,
        reason: str | None = None,
        approval_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_workspace_id, safe_review_id = self._safe_ids(workspace_id, review_id)
        self._load_current(safe_workspace_id)
        try:
            result = self.repository.approve_review_once(
                safe_workspace_id,
                safe_review_id,
                actor_type="user",
                actor_id="local_user",
                reason=reason,
                approval_decision=approval_decision or {},
            )
        except FileNotFoundError as error:
            raise ReviewNotFoundError(
                f"review does not exist: {safe_review_id}"
            ) from error
        except ValueError as error:
            raise ReviewConflictError(str(error)) from error
        if not result.get("ok"):
            raise ReviewConflictError(
                str(result.get("error_message") or "workspace review could not be approved.")
            )
        updated_workspace = result.get("updated_workspace")
        current = (
            updated_workspace
            if isinstance(updated_workspace, dict)
            else self.repository.get_current_workspace(safe_workspace_id)
        )
        return {
            "workspace_id": safe_workspace_id,
            "review_id": safe_review_id,
            "status": result.get("status"),
            "idempotent": bool(result.get("idempotent")),
            "workspace_version_hash": workspace_version_hash(current),
            "persisted_version_hash": result.get("persisted_version_hash"),
            "persisted_event_ids": result.get("persisted_event_ids") or [],
        }

    def reject_review(
        self,
        workspace_id: str,
        review_id: str,
        *,
        reason: str | None = None,
        approval_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_workspace_id, safe_review_id = self._safe_ids(workspace_id, review_id)
        current = self._load_current(safe_workspace_id)
        try:
            result = self.repository.reject_review_once(
                safe_workspace_id,
                safe_review_id,
                actor_type="user",
                actor_id="local_user",
                reason=reason,
                approval_decision=approval_decision or {},
            )
        except FileNotFoundError as error:
            raise ReviewNotFoundError(
                f"review does not exist: {safe_review_id}"
            ) from error
        except ValueError as error:
            raise ReviewConflictError(str(error)) from error
        if not result.get("ok"):
            raise ReviewConflictError(
                str(result.get("error_message") or "workspace review could not be rejected.")
            )
        return {
            "workspace_id": safe_workspace_id,
            "review_id": safe_review_id,
            "status": result.get("status"),
            "idempotent": bool(result.get("idempotent")),
            "workspace_version_hash": workspace_version_hash(current),
            "persisted_event_ids": result.get("persisted_event_ids") or [],
        }

    def edit_review(
        self,
        workspace_id: str,
        review_id: str,
        *,
        proposed_workspace: dict[str, Any],
        approval_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_workspace_id, safe_review_id = self._safe_ids(workspace_id, review_id)
        proposed = as_mapping(proposed_workspace, field_name="proposed_workspace")
        current = self._load_current(safe_workspace_id)
        current_hash = workspace_version_hash(current)
        review = self._load_review(safe_workspace_id, safe_review_id)
        status = str(review.get("status") or "")
        if status != "pending":
            raise ReviewConflictError(f"cannot edit review with status {status!r}")
        base_hash = str(review.get("base_workspace_version_hash") or "")
        if current_hash != base_hash:
            raise StaleWorkspaceError(
                "Cannot edit workspace review because current.json changed after the proposal was created."
            )

        operations, diff_summary, diff_warnings = derive_operations_and_diff_summary(
            workspace=current,
            proposed_workspace=proposed,
        )
        edit_result = self.repository.edit_review_once(
            safe_workspace_id,
            safe_review_id,
            edited_workspace=proposed,
            actor_type="user",
            actor_id="local_user",
            target_ids=operation_target_ids(operations),
            approval_decision=approval_decision or {},
        )
        if not edit_result.get("ok"):
            raise ReviewConflictError(
                str(edit_result.get("error_message") or "workspace review could not be edited.")
            )

        validation_summary = _validate_proposal(
            current_workspace=current,
            proposed_workspace=proposed,
            proposed_operations=operations,
            diff_summary=diff_summary,
        )
        warnings = [*diff_warnings, *validation_summary.get("warnings", [])]
        if not validation_summary.get("valid"):
            return {
                "workspace_id": safe_workspace_id,
                "review_id": safe_review_id,
                "status": "failed_validation",
                "idempotent": False,
                "workspace_version_hash": current_hash,
                "diff_summary": diff_summary,
                "validation_summary": validation_summary,
                "warnings": warnings,
                "errors": validation_summary.get("errors") or [],
                "persisted_event_ids": edit_result.get("persisted_event_ids") or [],
            }

        new_review_id = f"review_{uuid4().hex}"
        interrupt_payload = _review_interrupt_payload(
            review_id=new_review_id,
            proposed_operations=operations,
            diff_summary=diff_summary,
            validation_summary=validation_summary,
            warnings=warnings,
        )
        self.repository.save_pending_review(
            safe_workspace_id,
            review_id=new_review_id,
            agent_run_id=str(review.get("agent_run_id") or ""),
            base_workspace_version_hash=current_hash,
            user_message=str(review.get("user_message") or ""),
            proposed_workspace=proposed,
            proposed_operations=operations,
            diff_summary=diff_summary,
            validation_summary=validation_summary,
            interrupt_payload=interrupt_payload,
            actor_type="agent",
            actor_id="workspace_agent",
        )
        run_event_id = _append_pending_review_run_event(
            self.repository,
            safe_workspace_id,
            agent_run_id=str(review.get("agent_run_id") or ""),
            new_review_id=new_review_id,
        )
        persisted_event_ids = [
            *[str(item) for item in edit_result.get("persisted_event_ids") or []],
            *([run_event_id] if run_event_id else []),
        ]
        return {
            "workspace_id": safe_workspace_id,
            "review_id": safe_review_id,
            "new_review_id": new_review_id,
            "status": "pending_review",
            "idempotent": False,
            "workspace_version_hash": current_hash,
            "interrupt_payload": interrupt_payload,
            "diff_summary": diff_summary,
            "validation_summary": validation_summary,
            "warnings": warnings,
            "errors": [],
            "persisted_event_ids": persisted_event_ids,
        }

    def _safe_ids(self, workspace_id: str, review_id: str) -> tuple[str, str]:
        return (
            validate_resource_id(workspace_id, field_name="workspace_id"),
            validate_resource_id(review_id, field_name="review_id"),
        )

    def _load_current(self, workspace_id: str) -> dict[str, Any]:
        try:
            return self.repository.get_current_workspace(workspace_id)
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError(
                f"workspace does not exist: {workspace_id}"
            ) from error
        except ValueError as error:
            raise WorkspaceServiceError(str(error)) from error

    def _load_review(self, workspace_id: str, review_id: str) -> dict[str, Any]:
        try:
            return self.repository.get_review(workspace_id, review_id)
        except FileNotFoundError as error:
            raise ReviewNotFoundError(f"review does not exist: {review_id}") from error
        except ValueError as error:
            raise WorkspaceServiceError(str(error)) from error


def _validate_proposal(
    *,
    current_workspace: dict[str, Any],
    proposed_workspace: dict[str, Any],
    proposed_operations: list[dict[str, Any]],
    diff_summary: dict[str, Any],
) -> dict[str, Any]:
    validation_round = 1
    validators = select_workspace_validators(
        proposed_operations=proposed_operations,
        diff_summary=diff_summary,
    )
    payload = {
        "workspace": current_workspace,
        "proposed_workspace": proposed_workspace,
        "proposed_operations": proposed_operations,
        "candidate_pool": [],
        "candidate_artifact": None,
        "diff_summary": diff_summary,
        "validation_round": validation_round,
    }
    results: list[dict[str, Any]] = []
    for validator_name in validators:
        result = run_workspace_validator(validator_name=validator_name, payload=payload)
        result["validation_round"] = validation_round
        results.append(result)
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
    return WorkspaceValidationSummary(
        valid=not errors,
        error_count=len(errors),
        warning_count=len(warnings),
        errors=errors,
        warnings=warnings,
        validators_run=[str(result.get("validator_name")) for result in results],
        repair_attempts=0,
        validation_round=validation_round,
    ).model_dump()


def _review_interrupt_payload(
    *,
    review_id: str,
    proposed_operations: list[dict[str, Any]],
    diff_summary: dict[str, Any],
    validation_summary: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "type": "workspace_patch_review",
        "question": "Approve, edit, or reject this workspace change?",
        "review_id": review_id,
        "diff_summary": diff_summary,
        "proposed_operations": proposed_operations,
        "validation_summary": validation_summary,
        "warnings": warnings,
        "choices": ["approve", "edit", "reject"],
    }


def _append_pending_review_run_event(
    repository: WorkspaceRepository,
    workspace_id: str,
    *,
    agent_run_id: str,
    new_review_id: str,
) -> str | None:
    append_agent_run_event = getattr(repository, "append_agent_run_event", None)
    if append_agent_run_event is None:
        return None
    return append_agent_run_event(
        workspace_id,
        agent_run_id=agent_run_id,
        status="pending_review",
        payload={"review_id": new_review_id},
        actor_type="agent",
        actor_id="workspace_agent",
    )
