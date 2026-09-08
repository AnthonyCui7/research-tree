from __future__ import annotations

from typing import Any
from uuid import uuid4

from research_tree.agents.workspace.models import WorkspaceValidationSummary
from research_tree.principal import acting_user_id
from research_tree.services.errors import (
    InvalidPayloadError,
    ReviewConflictError,
    ReviewNotFoundError,
    StaleWorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceServiceError,
)
from research_tree.services.validation import (
    as_mapping,
    validate_resource_id,
)
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.diff import derive_operations_and_diff_summary
from research_tree.workspace.operations import operation_target_ids
from research_tree.workspace.repository import WorkspaceRepository
from research_tree.workspace.validators import (
    run_workspace_validator,
    select_workspace_validators,
)


class WorkspaceReviewService:
    def __init__(
        self,
        repository: WorkspaceRepository,
        *,
        pipeline_service: Any | None = None,
    ) -> None:
        self.repository = repository
        # Approving a pipeline_rerun review starts a run. Injected so review
        # logic stays testable without a pipeline.
        self.pipeline_service = pipeline_service

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
        review = self._load_review(safe_workspace_id, safe_review_id)
        if str(review.get("review_type") or "workspace_patch") == "pipeline_rerun":
            return self._approve_pipeline_rerun(
                safe_workspace_id,
                safe_review_id,
                review=review,
                reason=reason,
                approval_decision=approval_decision,
            )
        try:
            result = self.repository.approve_review_once(
                safe_workspace_id,
                safe_review_id,
                actor_type="user",
                actor_id=acting_user_id(),
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
                actor_id=acting_user_id(),
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
        # A rerun proposal has no document to edit. Editing one used to retire
        # it and hand back a workspace patch instead, so the rebuild the reader
        # asked for quietly became a text change.
        if str(review.get("review_type") or "workspace_patch") != "workspace_patch":
            raise InvalidPayloadError(
                "A rebuild proposal cannot be edited. Approve or reject it instead."
            )
        base_hash = str(review.get("base_workspace_version_hash") or "")
        if current_hash != base_hash:
            raise StaleWorkspaceError(
                "Cannot edit workspace review because current.json changed after the proposal was created."
            )

        operations, diff_summary, diff_warnings = derive_operations_and_diff_summary(
            workspace=current,
            proposed_workspace=proposed,
        )
        # Validate before the review is consumed. `edit_review_once` retires the
        # pending review and the successor is only written further down, so
        # validating afterwards threw the proposal away whenever the edit was
        # rejected: the reader lost the assistant's work with nothing to approve.
        validation_summary = validate_workspace_proposal(
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
                "persisted_event_ids": [],
            }

        edit_result = self.repository.edit_review_once(
            safe_workspace_id,
            safe_review_id,
            edited_workspace=proposed,
            actor_type="user",
            actor_id=acting_user_id(),
            target_ids=operation_target_ids(operations),
            approval_decision=approval_decision or {},
        )
        if not edit_result.get("ok"):
            raise ReviewConflictError(
                str(edit_result.get("error_message") or "workspace review could not be edited.")
            )

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

    def _approve_pipeline_rerun(
        self,
        workspace_id: str,
        review_id: str,
        *,
        review: dict[str, Any],
        reason: str | None,
        approval_decision: dict[str, Any] | None,
    ) -> dict[str, Any]:
        status = str(review.get("status") or "")
        if status == "approved_applied":
            return self._rerun_action_result(workspace_id, review_id, idempotent=True)
        if status != "pending":
            raise ReviewConflictError(f"cannot approve review with status {status!r}")
        if self.pipeline_service is None:
            raise WorkspaceServiceError("no pipeline service is configured.")

        rerun = review.get("pipeline_rerun")
        stage = str((rerun or {}).get("stage") or "candidates")
        run = self.pipeline_service.rerun(workspace_id, start_stage=stage)
        event_id = self.repository.append_workspace_event(
            workspace_id,
            actor="user",
            actor_type="user",
            actor_id=acting_user_id(),
            event_type="pipeline_rerun_approved",
            target_ids={},
            before_hash=review.get("base_workspace_version_hash"),
            after_hash=None,
            payload={
                "review_id": review_id,
                "stage": stage,
                "run_id": run.get("run_id"),
                "reason": reason or "user approved a pipeline rerun",
                "approval_decision": approval_decision or {},
            },
        )
        self.repository.mark_review_approved(
            workspace_id,
            review_id,
            applied_workspace_version_hash="",
            event_id=event_id,
            actor_type="user",
            actor_id=acting_user_id(),
        )
        return self._rerun_action_result(
            workspace_id,
            review_id,
            idempotent=False,
            event_ids=[event_id],
            pipeline_run=run,
        )

    def _rerun_action_result(
        self,
        workspace_id: str,
        review_id: str,
        *,
        idempotent: bool,
        event_ids: list[str] | None = None,
        pipeline_run: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self._load_current(workspace_id)
        return {
            "workspace_id": workspace_id,
            "review_id": review_id,
            "status": "approved_applied",
            "idempotent": idempotent,
            "workspace_version_hash": workspace_version_hash(current),
            "persisted_version_hash": None,
            "persisted_event_ids": event_ids or [],
            "pipeline_run": pipeline_run,
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


def validate_workspace_proposal(
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
) -> str:
    return repository.append_agent_run_event(
        workspace_id,
        agent_run_id=agent_run_id,
        status="pending_review",
        payload={"review_id": new_review_id},
        actor_type="agent",
        actor_id="workspace_agent",
    )
