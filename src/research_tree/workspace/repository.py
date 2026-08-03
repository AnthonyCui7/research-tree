from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Protocol

from research_tree.workspace.context import workspace_version_hash


ALLOWED_ACTOR_TYPES = {"user", "agent", "system"}
DEFAULT_ACTOR_IDS = {
    "user": "local_user",
    "agent": "workspace_agent",
    "system": "workspace_agent",
}

_REPOSITORY_LOCKS: dict[str, RLock] = {}
_REPOSITORY_LOCKS_GUARD = RLock()


class WorkspaceRepository(Protocol):
    def list_workspaces(self) -> list[dict[str, Any]]:
        ...

    def get_current_workspace(self, workspace_id: str) -> dict[str, Any]:
        ...

    def get_workspace_version(
        self,
        workspace_id: str,
        version_hash: str,
    ) -> dict[str, Any]:
        ...

    def save_workspace_version(
        self,
        workspace_id: str,
        workspace: dict[str, Any],
        *,
        actor: str,
        actor_type: str | None = None,
        actor_id: str | None = None,
        parent_version_hash: str | None,
        reason: str,
        agent_run_id: str | None = None,
        pipeline_run_id: str | None = None,
    ) -> str:
        ...

    def append_workspace_event(
        self,
        workspace_id: str,
        *,
        actor: str,
        actor_type: str | None = None,
        actor_id: str | None = None,
        event_type: str,
        target_ids: dict[str, Any],
        before_hash: str | None,
        after_hash: str | None,
        payload: dict[str, Any],
    ) -> str:
        ...

    def restore_workspace_version(
        self,
        workspace_id: str,
        version_hash: str,
        *,
        actor: str,
        actor_type: str | None = None,
        actor_id: str | None = None,
        reason: str,
    ) -> dict[str, Any]:
        ...

    def save_pending_review(
        self,
        workspace_id: str,
        *,
        agent_run_id: str,
        base_workspace_version_hash: str,
        user_message: str,
        proposed_workspace: dict[str, Any],
        proposed_operations: list[dict[str, Any]],
        diff_summary: dict[str, Any],
        validation_summary: dict[str, Any],
        interrupt_payload: dict[str, Any],
        structured_patch_operations: list[dict[str, Any]] | None = None,
        review_id: str | None = None,
        actor_type: str = "agent",
        actor_id: str | None = None,
    ) -> str:
        ...

    def get_pending_review(self, workspace_id: str, review_id: str) -> dict[str, Any]:
        ...

    def mark_review_approved(
        self,
        workspace_id: str,
        review_id: str,
        *,
        applied_workspace_version_hash: str,
        event_id: str | None = None,
        actor_type: str = "user",
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        ...

    def mark_review_rejected(
        self,
        workspace_id: str,
        review_id: str,
        *,
        event_id: str | None = None,
        reason: str | None = None,
        actor_type: str = "user",
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        ...

    def mark_review_edited(
        self,
        workspace_id: str,
        review_id: str,
        *,
        edited_workspace: dict[str, Any] | None = None,
        event_id: str | None = None,
        actor_type: str = "user",
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        ...

    def approve_review_once(
        self,
        workspace_id: str,
        review_id: str,
        *,
        actor_type: str = "user",
        actor_id: str | None = None,
        reason: str | None = None,
        target_ids: dict[str, Any] | None = None,
        approval_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def reject_review_once(
        self,
        workspace_id: str,
        review_id: str,
        *,
        actor_type: str = "user",
        actor_id: str | None = None,
        reason: str | None = None,
        target_ids: dict[str, Any] | None = None,
        approval_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def edit_review_once(
        self,
        workspace_id: str,
        review_id: str,
        *,
        edited_workspace: dict[str, Any],
        actor_type: str = "user",
        actor_id: str | None = None,
        target_ids: dict[str, Any] | None = None,
        approval_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def list_workspace_versions(self, workspace_id: str) -> list[dict[str, Any]]:
        ...

    def list_workspace_events(self, workspace_id: str) -> list[dict[str, Any]]:
        ...

    def list_workspace_reviews(self, workspace_id: str) -> list[dict[str, Any]]:
        ...

    def get_review(self, workspace_id: str, review_id: str) -> dict[str, Any]:
        ...

    def delete_workspace(
        self,
        workspace_id: str,
        *,
        expected_version_hash: str | None = None,
    ) -> dict[str, Any]:
        ...

    def save_pipeline_run(self, pipeline_run: Mapping[str, Any]) -> None:
        ...

    def reserve_new_workspace_run(self, pipeline_run: Mapping[str, Any]) -> None:
        ...

    def reserve_pipeline_rerun(self, pipeline_run: Mapping[str, Any]) -> None:
        ...

    def get_pipeline_run(self, run_id: str) -> dict[str, Any]:
        ...

    def list_pipeline_runs(self, workspace_id: str) -> list[dict[str, Any]]:
        ...

    def cancel_pipeline_runs(self, workspace_id: str) -> list[str]:
        ...

    def save_paper_content(
        self, workspace_id: str, paper_id: str, content: Mapping[str, Any]
    ) -> str:
        ...

    def get_paper_content(self, workspace_id: str, paper_id: str) -> dict[str, Any]:
        ...


class LocalJsonWorkspaceRepository:
    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir)
        lock_key = str(self.base_dir.resolve())
        with _REPOSITORY_LOCKS_GUARD:
            self._lock = _REPOSITORY_LOCKS.setdefault(lock_key, RLock())

    def list_workspaces(self) -> list[dict[str, Any]]:
        if not self.base_dir.is_dir():
            return []

        summaries: list[dict[str, Any]] = []
        for workspace_dir in sorted(self.base_dir.iterdir()):
            if not workspace_dir.is_dir():
                continue
            current_path = workspace_dir / "current.json"
            if not current_path.is_file():
                continue
            try:
                workspace = _read_json(current_path)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(workspace, dict):
                continue
            workspace_id = str(workspace.get("workspace_id") or workspace_dir.name)
            tree = workspace.get("tree")
            tree_nodes = tree.get("nodes") if isinstance(tree, Mapping) else []
            summaries.append(
                {
                    "workspace_id": workspace_id,
                    "workspace_version_hash": workspace_version_hash(workspace),
                    "title": str(workspace.get("title") or workspace_id),
                    "topic": str(workspace.get("topic") or ""),
                    "paper_count": len(_mapping(workspace.get("paper_cards"))),
                    "branch_count": len(_list(tree_nodes)),
                    "paper_path_count": len(_list(workspace.get("paper_paths"))),
                    "updated_at": _workspace_updated_at(workspace),
                }
            )
        ordered = sorted(
            summaries,
            key=lambda summary: str(summary.get("updated_at") or ""),
            reverse=True,
        )
        # Older pipeline runs could publish the same normalized topic under
        # different IDs. Keep the newest visible without deleting either copy.
        visible: list[dict[str, Any]] = []
        seen_topics: set[str] = set()
        for summary in ordered:
            topic_key = _topic_key(str(summary.get("topic") or summary.get("title") or ""))
            if topic_key and topic_key in seen_topics:
                continue
            if topic_key:
                seen_topics.add(topic_key)
            visible.append(summary)
        return visible

    def get_current_workspace(self, workspace_id: str) -> dict[str, Any]:
        path = self._workspace_dir(workspace_id) / "current.json"
        if not path.is_file():
            raise FileNotFoundError(f"current workspace does not exist: {path}")
        payload = _read_json(path)
        if not isinstance(payload, dict):
            raise ValueError(f"current workspace must be a JSON object: {path}")
        return payload

    def get_workspace_version(
        self,
        workspace_id: str,
        version_hash: str,
    ) -> dict[str, Any]:
        safe_version_hash = _safe_version_hash(version_hash)
        path = self._workspace_dir(workspace_id) / "versions" / f"{safe_version_hash}.json"
        if not path.is_file():
            raise FileNotFoundError(f"workspace version does not exist: {path}")
        payload = _read_json(path)
        if not isinstance(payload, dict):
            raise ValueError(f"workspace version must be a JSON object: {path}")
        return payload

    def save_workspace_version(
        self,
        workspace_id: str,
        workspace: dict[str, Any],
        *,
        actor: str,
        actor_type: str | None = None,
        actor_id: str | None = None,
        parent_version_hash: str | None,
        reason: str,
        agent_run_id: str | None = None,
        pipeline_run_id: str | None = None,
    ) -> str:
        if actor not in {"user", "agent", "system"}:
            raise ValueError(f"unsupported workspace actor: {actor!r}")
        actor_fields = _actor_fields(actor_type or actor, actor_id)
        with self._lock:
            if pipeline_run_id:
                active_run = self.get_pipeline_run(pipeline_run_id)
                if active_run.get("status") != "running":
                    raise RuntimeError("pipeline run no longer owns this workspace publication.")
            workspace_dir = self._workspace_dir(workspace_id)
            versions_dir = workspace_dir / "versions"
            versions_dir.mkdir(parents=True, exist_ok=True)

            version_hash = workspace_version_hash(workspace)
            version_path = versions_dir / f"{version_hash}.json"
            metadata_path = versions_dir / f"{version_hash}.metadata.json"
            metadata = {
                "schema_version": "research_tree.workspace_version_metadata.v1",
                "workspace_id": workspace_id,
                "version_hash": version_hash,
                "parent_version_hash": parent_version_hash,
                "actor": actor,
                **actor_fields,
                "reason": reason,
                "agent_run_id": agent_run_id,
                "created_at": _now(),
            }
            # Content-addressed snapshots are immutable. Browser-style navigation
            # is stored separately so restoring a snapshot never rewrites lineage.
            if not version_path.is_file():
                _write_json_atomic(version_path, workspace)
            if not metadata_path.is_file():
                _write_json_atomic(metadata_path, metadata)
            _write_json_atomic(workspace_dir / "current.json", workspace)
            self._record_new_navigation_head(workspace_id, version_hash)
            return version_hash

    def append_workspace_event(
        self,
        workspace_id: str,
        *,
        actor: str,
        actor_type: str | None = None,
        actor_id: str | None = None,
        event_type: str,
        target_ids: dict[str, Any],
        before_hash: str | None,
        after_hash: str | None,
        payload: dict[str, Any],
    ) -> str:
        if actor not in {"user", "agent", "system"}:
            raise ValueError(f"unsupported workspace actor: {actor!r}")
        if not event_type.strip():
            raise ValueError("event_type cannot be empty.")
        actor_fields = _actor_fields(actor_type or actor, actor_id)
        event_id = f"evt_{uuid.uuid4().hex}"
        event = {
            "schema_version": "research_tree.workspace_event.v1",
            "event_id": event_id,
            "workspace_id": workspace_id,
            "actor": actor,
            **actor_fields,
            "event_type": event_type,
            "target_ids": target_ids,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "payload": payload,
            "created_at": _now(),
        }
        self._append_jsonl(self._workspace_dir(workspace_id) / "events.jsonl", event)
        return event_id

    def restore_workspace_version(
        self,
        workspace_id: str,
        version_hash: str,
        *,
        actor: str,
        actor_type: str | None = None,
        actor_id: str | None = None,
        reason: str,
    ) -> dict[str, Any]:
        if actor not in {"user", "agent", "system"}:
            raise ValueError(f"unsupported workspace actor: {actor!r}")
        target_workspace = self.get_workspace_version(workspace_id, version_hash)
        target_hash = workspace_version_hash(target_workspace)
        try:
            current_workspace = self.get_current_workspace(workspace_id)
            current_hash = workspace_version_hash(current_workspace)
        except FileNotFoundError:
            current_hash = None

        if current_hash == target_hash:
            return {
                "workspace_id": workspace_id,
                "before_hash": current_hash,
                "version_hash": target_hash,
                "event_id": None,
                "restored": False,
            }

        with self._lock:
            navigation = self._workspace_navigation(workspace_id)
            if target_hash not in navigation["version_hashes"]:
                navigation["version_hashes"].append(target_hash)
            navigation["current_index"] = max(
                index
                for index, item in enumerate(navigation["version_hashes"])
                if item == target_hash
            )
            navigation["updated_at"] = _now()
            _write_json_atomic(self._navigation_path(workspace_id), navigation)
            _write_json_atomic(
                self._workspace_dir(workspace_id) / "current.json",
                target_workspace,
            )
            event_id = self.append_workspace_event(
                workspace_id,
                actor=actor,
                actor_type=actor_type,
                actor_id=actor_id,
                event_type="workspace_restored",
                target_ids={"workspace_id": workspace_id, "version_hash": target_hash},
                before_hash=current_hash,
                after_hash=target_hash,
                payload={"reason": reason, "restored_version_hash": target_hash},
            )
        return {
            "workspace_id": workspace_id,
            "before_hash": current_hash,
            "version_hash": target_hash,
            "event_id": event_id,
            "restored": True,
        }

    def append_agent_run_event(
        self,
        workspace_id: str,
        *,
        agent_run_id: str,
        status: str,
        payload: dict[str, Any],
        actor_type: str = "system",
        actor_id: str | None = None,
        error_message: str | None = None,
        errors: list[str] | None = None,
    ) -> str:
        actor_fields = _actor_fields(actor_type, actor_id)
        run_event_id = f"run_evt_{uuid.uuid4().hex}"
        event = {
            "schema_version": "research_tree.agent_run.v1",
            "run_event_id": run_event_id,
            "workspace_id": workspace_id,
            "agent_run_id": agent_run_id,
            "status": status,
            **actor_fields,
            "error_message": error_message,
            "errors": errors or [],
            "payload": payload,
            "created_at": _now(),
        }
        self._append_jsonl(self._workspace_dir(workspace_id) / "agent_runs.jsonl", event)
        return run_event_id

    def save_pending_review(
        self,
        workspace_id: str,
        *,
        agent_run_id: str,
        base_workspace_version_hash: str,
        user_message: str,
        proposed_workspace: dict[str, Any],
        proposed_operations: list[dict[str, Any]],
        diff_summary: dict[str, Any],
        validation_summary: dict[str, Any],
        interrupt_payload: dict[str, Any],
        structured_patch_operations: list[dict[str, Any]] | None = None,
        review_id: str | None = None,
        actor_type: str = "agent",
        actor_id: str | None = None,
    ) -> str:
        if not base_workspace_version_hash:
            raise ValueError(
                "base_workspace_version_hash is required for a pending review."
            )
        actor_fields = _actor_fields(actor_type, actor_id)
        active_review_id = review_id or f"review_{uuid.uuid4().hex}"
        review_path = self._review_path(workspace_id, active_review_id)
        now = _now()
        created_at = now
        status_history: list[dict[str, Any]] = [
            {
                "status": "pending",
                **actor_fields,
                "created_at": now,
                "reason": "workspace proposal created",
            }
        ]
        if review_path.is_file():
            existing = _read_json(review_path)
            if not isinstance(existing, dict):
                raise ValueError(f"review must be a JSON object: {review_path}")
            if existing.get("status") != "pending":
                raise ValueError(
                    f"cannot overwrite non-pending review: {active_review_id}"
                )
            created_at = str(existing.get("created_at") or now)
            existing_history = existing.get("status_history")
            if isinstance(existing_history, list):
                status_history = [
                    item for item in existing_history if isinstance(item, dict)
                ]

        payload = {
            "schema_version": "research_tree.workspace_review.v1",
            "review_id": active_review_id,
            "agent_run_id": agent_run_id,
            "workspace_id": workspace_id,
            "status": "pending",
            **actor_fields,
            "base_workspace_version_hash": base_workspace_version_hash,
            "proposed_workspace_version_hash": workspace_version_hash(proposed_workspace),
            "created_at": created_at,
            "updated_at": now,
            "user_message": user_message,
            "proposed_workspace": proposed_workspace,
            "proposed_operations": proposed_operations,
            "structured_patch_operations": structured_patch_operations or [],
            "diff_summary": diff_summary,
            "validation_summary": validation_summary,
            "interrupt_payload": interrupt_payload,
            "status_history": status_history,
        }
        _write_json_atomic(review_path, payload)
        return active_review_id

    def get_pending_review(self, workspace_id: str, review_id: str) -> dict[str, Any]:
        review = self.get_review(workspace_id, review_id)
        if review.get("status") != "pending":
            raise ValueError(f"review is not pending: {review_id}")
        return review

    def get_review(self, workspace_id: str, review_id: str) -> dict[str, Any]:
        review_path = self._review_path(workspace_id, review_id)
        if not review_path.is_file():
            raise FileNotFoundError(f"workspace review does not exist: {review_path}")
        review = _read_json(review_path)
        if not isinstance(review, dict):
            raise ValueError(f"workspace review must be a JSON object: {review_path}")
        return review

    def mark_review_approved(
        self,
        workspace_id: str,
        review_id: str,
        *,
        applied_workspace_version_hash: str,
        event_id: str | None = None,
        actor_type: str = "user",
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        return self._mark_review_status(
            workspace_id,
            review_id,
            status="approved_applied",
            extra={
                "applied_workspace_version_hash": applied_workspace_version_hash,
                "approval_event_id": event_id,
                "approved_at": _now(),
            },
            actor_type=actor_type,
            actor_id=actor_id,
        )

    def approve_review_once(
        self,
        workspace_id: str,
        review_id: str,
        *,
        actor_type: str = "user",
        actor_id: str | None = None,
        reason: str | None = None,
        target_ids: dict[str, Any] | None = None,
        approval_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor_fields = _actor_fields(actor_type, actor_id)
        review = self.get_review(workspace_id, review_id)
        status = str(review.get("status") or "")
        if status == "approved_applied":
            return _review_action_result(
                review,
                ok=True,
                idempotent=True,
                updated_workspace=self.get_current_workspace(workspace_id),
            )
        if status != "pending":
            return _review_action_result(
                review,
                ok=False,
                idempotent=False,
                error_message=f"cannot approve review with status {status!r}",
            )

        proposed_workspace = review.get("proposed_workspace")
        if not isinstance(proposed_workspace, dict):
            return self._fail_pending_review(
                workspace_id,
                review,
                status="failed_exception",
                error_message="pending review does not contain a proposed workspace.",
                target_ids=target_ids,
            )

        base_hash = str(review.get("base_workspace_version_hash") or "")
        proposed_hash = str(
            review.get("proposed_workspace_version_hash")
            or workspace_version_hash(proposed_workspace)
        )
        current_workspace = self.get_current_workspace(workspace_id)
        current_hash = workspace_version_hash(current_workspace)
        if current_hash == proposed_hash:
            event_id = self._existing_review_event_id(
                workspace_id,
                review_id,
                "workspace_patch_approved_applied",
            ) or self.append_workspace_event(
                workspace_id,
                actor="user",
                actor_type=actor_type,
                actor_id=actor_fields["actor_id"],
                event_type="workspace_patch_approved_applied",
                target_ids=target_ids or {},
                before_hash=base_hash,
                after_hash=proposed_hash,
                payload=self._review_event_payload(
                    review,
                    approval_decision=approval_decision,
                ),
            )
            updated_review = self.mark_review_approved(
                workspace_id,
                review_id,
                applied_workspace_version_hash=proposed_hash,
                event_id=event_id,
                actor_type=actor_type,
                actor_id=actor_fields["actor_id"],
            )
            return _review_action_result(
                updated_review,
                ok=True,
                idempotent=True,
                event_ids=[event_id],
                updated_workspace=current_workspace,
            )

        if current_hash != base_hash:
            event_id = self._existing_review_event_id(
                workspace_id,
                review_id,
                "workspace_patch_stale_approval_rejected",
            ) or self.append_workspace_event(
                workspace_id,
                actor="system",
                actor_type="system",
                event_type="workspace_patch_stale_approval_rejected",
                target_ids=target_ids or {},
                before_hash=current_hash,
                after_hash=None,
                payload={
                    **self._review_event_payload(
                        review,
                        approval_decision=approval_decision,
                    ),
                    "error_message": (
                        "Cannot approve workspace review because current.json "
                        "changed after the proposal was created."
                    ),
                    "base_workspace_version_hash": base_hash,
                    "current_workspace_version_hash": current_hash,
                },
            )
            updated_review = self._mark_review_status(
                workspace_id,
                review_id,
                status="failed_stale_base",
                extra={
                    "failure_event_id": event_id,
                    "error_message": (
                        "Cannot approve workspace review because current.json "
                        "changed after the proposal was created."
                    ),
                    "base_workspace_version_hash": base_hash,
                    "current_workspace_version_hash": current_hash,
                    "failed_at": _now(),
                },
                actor_type="system",
                actor_id="workspace_agent",
                reason="stale base workspace",
            )
            run_event_id = self.append_agent_run_event(
                workspace_id,
                agent_run_id=str(review.get("agent_run_id") or ""),
                status="failed_stale_base",
                payload={
                    "review_id": review_id,
                    "event_id": event_id,
                    "base_workspace_version_hash": base_hash,
                    "current_workspace_version_hash": current_hash,
                },
                actor_type="system",
                error_message=updated_review.get("error_message"),
                errors=[str(updated_review.get("error_message") or "")],
            )
            return _review_action_result(
                updated_review,
                ok=False,
                idempotent=False,
                event_ids=[event_id, run_event_id],
                error_message=str(updated_review.get("error_message") or ""),
            )

        version_hash = self.save_workspace_version(
            workspace_id,
            proposed_workspace,
            actor="agent",
            actor_type="agent",
            actor_id="workspace_agent",
            parent_version_hash=base_hash,
            reason=reason
            or str(review.get("user_message") or "approved workspace agent patch"),
            agent_run_id=str(review.get("agent_run_id") or ""),
        )
        event_id = self.append_workspace_event(
            workspace_id,
            actor="user",
            actor_type=actor_type,
            actor_id=actor_fields["actor_id"],
            event_type="workspace_patch_approved_applied",
            target_ids=target_ids or {},
            before_hash=base_hash,
            after_hash=version_hash,
            payload=self._review_event_payload(
                review,
                approval_decision=approval_decision,
            ),
        )
        updated_review = self.mark_review_approved(
            workspace_id,
            review_id,
            applied_workspace_version_hash=version_hash,
            event_id=event_id,
            actor_type=actor_type,
            actor_id=actor_fields["actor_id"],
        )
        run_event_id = self.append_agent_run_event(
            workspace_id,
            agent_run_id=str(review.get("agent_run_id") or ""),
            status="approved_applied",
            payload={
                "review_id": review_id,
                "version_hash": version_hash,
                "event_id": event_id,
            },
            actor_type=actor_type,
            actor_id=actor_fields["actor_id"],
        )
        return _review_action_result(
            updated_review,
            ok=True,
            idempotent=False,
            event_ids=[event_id, run_event_id],
            updated_workspace=proposed_workspace,
        )

    def reject_review_once(
        self,
        workspace_id: str,
        review_id: str,
        *,
        actor_type: str = "user",
        actor_id: str | None = None,
        reason: str | None = None,
        target_ids: dict[str, Any] | None = None,
        approval_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor_fields = _actor_fields(actor_type, actor_id)
        review = self.get_review(workspace_id, review_id)
        status = str(review.get("status") or "")
        if status == "rejected":
            return _review_action_result(review, ok=True, idempotent=True)
        if status != "pending":
            return _review_action_result(
                review,
                ok=False,
                idempotent=False,
                error_message=f"cannot reject review with status {status!r}",
            )

        event_id = self._existing_review_event_id(
            workspace_id,
            review_id,
            "workspace_patch_rejected",
        ) or self.append_workspace_event(
            workspace_id,
            actor="user",
            actor_type=actor_type,
            actor_id=actor_fields["actor_id"],
            event_type="workspace_patch_rejected",
            target_ids=target_ids or {},
            before_hash=review.get("base_workspace_version_hash"),
            after_hash=None,
            payload={
                **self._review_event_payload(
                    review,
                    approval_decision=approval_decision,
                ),
                "reason": reason or "user rejected workspace patch",
            },
        )
        updated_review = self.mark_review_rejected(
            workspace_id,
            review_id,
            event_id=event_id,
            reason=reason or "user rejected workspace patch",
            actor_type=actor_type,
            actor_id=actor_fields["actor_id"],
        )
        run_event_id = self.append_agent_run_event(
            workspace_id,
            agent_run_id=str(review.get("agent_run_id") or ""),
            status="rejected",
            payload={"review_id": review_id, "event_id": event_id},
            actor_type=actor_type,
            actor_id=actor_fields["actor_id"],
        )
        return _review_action_result(
            updated_review,
            ok=True,
            idempotent=False,
            event_ids=[event_id, run_event_id],
        )

    def edit_review_once(
        self,
        workspace_id: str,
        review_id: str,
        *,
        edited_workspace: dict[str, Any],
        actor_type: str = "user",
        actor_id: str | None = None,
        target_ids: dict[str, Any] | None = None,
        approval_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor_fields = _actor_fields(actor_type, actor_id)
        edited_hash = workspace_version_hash(edited_workspace)
        review = self.get_review(workspace_id, review_id)
        status = str(review.get("status") or "")
        if status == "edited":
            if review.get("edited_workspace_version_hash") == edited_hash:
                return _review_action_result(review, ok=True, idempotent=True)
            return _review_action_result(
                review,
                ok=False,
                idempotent=False,
                error_message="cannot replace an already edited review.",
            )
        if status != "pending":
            return _review_action_result(
                review,
                ok=False,
                idempotent=False,
                error_message=f"cannot edit review with status {status!r}",
            )

        event_id = self._existing_review_event_id(
            workspace_id,
            review_id,
            "workspace_patch_edited",
        ) or self.append_workspace_event(
            workspace_id,
            actor="user",
            actor_type=actor_type,
            actor_id=actor_fields["actor_id"],
            event_type="workspace_patch_edited",
            target_ids=target_ids or {},
            before_hash=review.get("base_workspace_version_hash"),
            after_hash=edited_hash,
            payload={
                **self._review_event_payload(
                    review,
                    approval_decision=approval_decision,
                ),
                "edited_workspace_version_hash": edited_hash,
            },
        )
        updated_review = self.mark_review_edited(
            workspace_id,
            review_id,
            edited_workspace=edited_workspace,
            event_id=event_id,
            actor_type=actor_type,
            actor_id=actor_fields["actor_id"],
        )
        run_event_id = self.append_agent_run_event(
            workspace_id,
            agent_run_id=str(review.get("agent_run_id") or ""),
            status="edited",
            payload={"review_id": review_id, "event_id": event_id},
            actor_type=actor_type,
            actor_id=actor_fields["actor_id"],
        )
        return _review_action_result(
            updated_review,
            ok=True,
            idempotent=False,
            event_ids=[event_id, run_event_id],
        )

    def mark_review_rejected(
        self,
        workspace_id: str,
        review_id: str,
        *,
        event_id: str | None = None,
        reason: str | None = None,
        actor_type: str = "user",
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        return self._mark_review_status(
            workspace_id,
            review_id,
            status="rejected",
            extra={
                "rejection_event_id": event_id,
                "rejection_reason": reason,
                "rejected_at": _now(),
            },
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
        )

    def mark_review_edited(
        self,
        workspace_id: str,
        review_id: str,
        *,
        edited_workspace: dict[str, Any] | None = None,
        event_id: str | None = None,
        actor_type: str = "user",
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        extra: dict[str, Any] = {
            "edit_event_id": event_id,
            "edited_at": _now(),
        }
        if edited_workspace is not None:
            extra["edited_workspace_version_hash"] = workspace_version_hash(
                edited_workspace
            )
            extra["edited_workspace"] = edited_workspace
        return self._mark_review_status(
            workspace_id,
            review_id,
            status="edited",
            extra=extra,
            actor_type=actor_type,
            actor_id=actor_id,
        )

    def list_workspace_events(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._read_jsonl(self._workspace_dir(workspace_id) / "events.jsonl")

    def list_agent_run_events(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._read_jsonl(self._workspace_dir(workspace_id) / "agent_runs.jsonl")

    def list_workspace_versions(self, workspace_id: str) -> list[dict[str, Any]]:
        versions_dir = self._workspace_dir(workspace_id) / "versions"
        if not versions_dir.is_dir():
            return []
        current_hash = None
        current_path = self._workspace_dir(workspace_id) / "current.json"
        if current_path.is_file():
            current = _read_json(current_path)
            if isinstance(current, dict):
                current_hash = workspace_version_hash(current)
        metadata_by_hash: dict[str, dict[str, Any]] = {}
        for metadata_path in sorted(versions_dir.glob("*.metadata.json")):
            metadata = _read_json(metadata_path)
            if isinstance(metadata, dict):
                metadata_by_hash[str(metadata.get("version_hash") or "")] = metadata
        navigation = self._workspace_navigation(workspace_id)
        versions: list[dict[str, Any]] = []
        for index, version_hash in enumerate(navigation["version_hashes"]):
            metadata = dict(metadata_by_hash.get(version_hash) or {
                "schema_version": "research_tree.workspace_version_metadata.v1",
                "workspace_id": workspace_id,
                "version_hash": version_hash,
                "parent_version_hash": None,
                "actor": "system",
                "actor_type": "system",
                "actor_id": "workspace_agent",
                "reason": "saved workspace",
                "created_at": None,
            })
            metadata["navigation_index"] = index
            metadata["is_current"] = index == navigation["current_index"]
            versions.append(metadata)
        return versions

    def delete_workspace(
        self,
        workspace_id: str,
        *,
        expected_version_hash: str | None = None,
    ) -> dict[str, Any]:
        """Soft-delete a workspace by atomically moving it into repository trash."""

        with self._lock:
            workspace_dir = self._workspace_dir(workspace_id)
            current = self.get_current_workspace(workspace_id)
            current_hash = workspace_version_hash(current)
            if expected_version_hash and current_hash != expected_version_hash:
                raise ValueError(
                    "workspace changed before deletion; refresh and try again."
                )
            self._cancel_pipeline_runs_locked(workspace_id)
            trash_dir = self.base_dir / ".trash"
            trash_dir.mkdir(parents=True, exist_ok=True)
            deleted_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            destination = trash_dir / f"{workspace_dir.name}--{deleted_at}"
            workspace_dir.replace(destination)
            return {
                "workspace_id": workspace_id,
                "workspace_version_hash": current_hash,
                "deleted": True,
                "deleted_at": _now(),
            }

    def save_pipeline_run(self, pipeline_run: Mapping[str, Any]) -> None:
        run_id = _safe_workspace_id(str(pipeline_run.get("run_id") or ""))
        with self._lock:
            _write_json_atomic(
                self.base_dir / ".pipeline_runs" / f"{run_id}.json",
                pipeline_run,
            )

    def reserve_new_workspace_run(self, pipeline_run: Mapping[str, Any]) -> None:
        """Atomically reject duplicate topics across active workspaces and jobs."""

        topic_key = _topic_key(str(pipeline_run.get("topic") or ""))
        if not topic_key:
            raise ValueError("pipeline run topic cannot be empty.")
        with self._lock:
            if any(
                _topic_key(str(item.get("topic") or item.get("title") or "")) == topic_key
                for item in self.list_workspaces()
            ):
                raise ValueError("a workspace for this topic already exists.")
            runs_dir = self.base_dir / ".pipeline_runs"
            if runs_dir.is_dir():
                for path in runs_dir.glob("*.json"):
                    item = _read_json(path)
                    if not isinstance(item, dict):
                        continue
                    if _reclaim_dead_pipeline_run(path, item):
                        continue
                    if (
                        item.get("status") in {"queued", "running"}
                        and _topic_key(str(item.get("topic") or "")) == topic_key
                    ):
                        raise ValueError("a workspace for this topic is already being built.")
            self.save_pipeline_run(pipeline_run)

    def reserve_pipeline_rerun(self, pipeline_run: Mapping[str, Any]) -> None:
        """Reserve one active rerun per workspace without touching current data."""

        workspace_id = _safe_workspace_id(str(pipeline_run.get("workspace_id") or ""))
        with self._lock:
            runs_dir = self.base_dir / ".pipeline_runs"
            if runs_dir.is_dir():
                for path in runs_dir.glob("*.json"):
                    item = _read_json(path)
                    if not isinstance(item, dict):
                        continue
                    if _reclaim_dead_pipeline_run(path, item):
                        continue
                    if (
                        item.get("workspace_id") == workspace_id
                        and item.get("status") in {"queued", "running"}
                    ):
                        raise ValueError("a pipeline run for this workspace is already active.")
            self.save_pipeline_run(pipeline_run)

    def get_pipeline_run(self, run_id: str) -> dict[str, Any]:
        path = self.base_dir / ".pipeline_runs" / f"{_safe_workspace_id(run_id)}.json"
        if not path.is_file():
            raise FileNotFoundError(f"pipeline run does not exist: {path}")
        payload = _read_json(path)
        if not isinstance(payload, dict):
            raise ValueError(f"pipeline run must be a JSON object: {path}")
        return payload

    def list_pipeline_runs(self, workspace_id: str) -> list[dict[str, Any]]:
        runs_dir = self.base_dir / ".pipeline_runs"
        if not runs_dir.is_dir():
            return []
        runs = []
        for path in runs_dir.glob("*.json"):
            payload = _read_json(path)
            if isinstance(payload, dict) and payload.get("workspace_id") == workspace_id:
                runs.append(payload)
        return sorted(runs, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def cancel_pipeline_runs(self, workspace_id: str) -> list[str]:
        with self._lock:
            return self._cancel_pipeline_runs_locked(workspace_id)

    def _cancel_pipeline_runs_locked(self, workspace_id: str) -> list[str]:
        runs_dir = self.base_dir / ".pipeline_runs"
        if not runs_dir.is_dir():
            return []
        cancelled: list[str] = []
        for path in runs_dir.glob("*.json"):
            run = _read_json(path)
            if not isinstance(run, dict):
                continue
            if run.get("workspace_id") != workspace_id or run.get("status") not in {"queued", "running"}:
                continue
            run.update(
                {
                    "status": "cancelled",
                    "current_stage": None,
                    "error": None,
                    "cancelled_at": _now(),
                    "updated_at": _now(),
                }
            )
            _write_json_atomic(path, run)
            cancelled.append(str(run.get("run_id") or ""))
        return cancelled

    def list_workspace_reviews(self, workspace_id: str) -> list[dict[str, Any]]:
        reviews_dir = self._workspace_dir(workspace_id) / "reviews"
        if not reviews_dir.is_dir():
            return []
        reviews: list[dict[str, Any]] = []
        for review_path in sorted(reviews_dir.glob("*.json")):
            review = _read_json(review_path)
            if isinstance(review, dict):
                reviews.append(review)
        return reviews

    def save_paper_content(
        self, workspace_id: str, paper_id: str, content: Mapping[str, Any]
    ) -> str:
        content_key = _paper_content_key(paper_id)
        path = self._workspace_dir(workspace_id) / "paper_content" / f"{content_key}.json"
        payload = dict(content)
        payload["paper_id"] = paper_id
        with self._lock:
            _write_json_atomic(path, payload)
        return content_key

    def get_paper_content(self, workspace_id: str, paper_id: str) -> dict[str, Any]:
        path = (
            self._workspace_dir(workspace_id)
            / "paper_content"
            / f"{_paper_content_key(paper_id)}.json"
        )
        if not path.is_file():
            raise FileNotFoundError(path)
        payload = _read_json(path)
        if not isinstance(payload, dict) or payload.get("paper_id") != paper_id:
            raise ValueError(f"invalid paper content artifact: {path}")
        return payload

    def _workspace_dir(self, workspace_id: str) -> Path:
        safe_workspace_id = _safe_workspace_id(workspace_id)
        return self.base_dir / safe_workspace_id

    def _navigation_path(self, workspace_id: str) -> Path:
        return self._workspace_dir(workspace_id) / "navigation.json"

    def _record_new_navigation_head(self, workspace_id: str, version_hash: str) -> None:
        navigation = self._workspace_navigation(workspace_id)
        hashes = navigation["version_hashes"][: navigation["current_index"] + 1]
        if not hashes or hashes[-1] != version_hash:
            hashes.append(version_hash)
        navigation["version_hashes"] = hashes
        navigation["current_index"] = len(hashes) - 1
        navigation["updated_at"] = _now()
        _write_json_atomic(self._navigation_path(workspace_id), navigation)

    def _workspace_navigation(self, workspace_id: str) -> dict[str, Any]:
        path = self._navigation_path(workspace_id)
        if path.is_file():
            payload = _read_json(path)
            if (
                isinstance(payload, dict)
                and isinstance(payload.get("version_hashes"), list)
                and isinstance(payload.get("current_index"), int)
            ):
                return payload
        versions_dir = self._workspace_dir(workspace_id) / "versions"
        metadata = []
        if versions_dir.is_dir():
            for metadata_path in versions_dir.glob("*.metadata.json"):
                item = _read_json(metadata_path)
                if isinstance(item, dict) and item.get("version_hash"):
                    metadata.append(item)
        ordered = _ordered_version_hashes(metadata)
        current_hash = None
        current_path = self._workspace_dir(workspace_id) / "current.json"
        if current_path.is_file():
            current = _read_json(current_path)
            if isinstance(current, dict):
                current_hash = workspace_version_hash(current)
        if current_hash and current_hash not in ordered:
            ordered.append(current_hash)
        current_index = ordered.index(current_hash) if current_hash in ordered else max(0, len(ordered) - 1)
        return {
            "schema_version": "research_tree.workspace_navigation.v1",
            "workspace_id": workspace_id,
            "version_hashes": ordered,
            "current_index": current_index,
            "updated_at": _now(),
        }

    def _review_path(self, workspace_id: str, review_id: str) -> Path:
        return (
            self._workspace_dir(workspace_id)
            / "reviews"
            / f"{_safe_workspace_id(review_id)}.json"
        )

    def _mark_review_status(
        self,
        workspace_id: str,
        review_id: str,
        *,
        status: str,
        extra: dict[str, Any],
        actor_type: str,
        actor_id: str | None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        review_path = self._review_path(workspace_id, review_id)
        review = self.get_pending_review(workspace_id, review_id)
        actor_fields = _actor_fields(actor_type, actor_id)
        now = _now()
        history = review.get("status_history")
        if not isinstance(history, list):
            history = []
        history = [item for item in history if isinstance(item, dict)]
        history.append(
            {
                "status": status,
                **actor_fields,
                "created_at": now,
                "reason": reason,
            }
        )
        review.update(extra)
        review["status"] = status
        review.update(actor_fields)
        review["updated_at"] = now
        review["status_history"] = history
        _write_json_atomic(review_path, review)
        return review

    def _fail_pending_review(
        self,
        workspace_id: str,
        review: dict[str, Any],
        *,
        status: str,
        error_message: str,
        target_ids: dict[str, Any] | None,
    ) -> dict[str, Any]:
        review_id = str(review.get("review_id") or "")
        event_id = self.append_workspace_event(
            workspace_id,
            actor="system",
            actor_type="system",
            event_type=f"workspace_patch_{status}",
            target_ids=target_ids or {},
            before_hash=review.get("base_workspace_version_hash"),
            after_hash=None,
            payload={
                **self._review_event_payload(review),
                "error_message": error_message,
            },
        )
        updated_review = self._mark_review_status(
            workspace_id,
            review_id,
            status=status,
            extra={
                "failure_event_id": event_id,
                "error_message": error_message,
                "failed_at": _now(),
            },
            actor_type="system",
            actor_id="workspace_agent",
            reason=error_message,
        )
        run_event_id = self.append_agent_run_event(
            workspace_id,
            agent_run_id=str(review.get("agent_run_id") or ""),
            status=status,
            payload={"review_id": review_id, "event_id": event_id},
            actor_type="system",
            error_message=error_message,
            errors=[error_message],
        )
        return _review_action_result(
            updated_review,
            ok=False,
            idempotent=False,
            event_ids=[event_id, run_event_id],
            error_message=error_message,
        )

    def _existing_review_event_id(
        self,
        workspace_id: str,
        review_id: str,
        event_type: str,
    ) -> str | None:
        for event in self.list_workspace_events(workspace_id):
            payload = event.get("payload")
            if (
                event.get("event_type") == event_type
                and isinstance(payload, dict)
                and payload.get("review_id") == review_id
            ):
                return str(event.get("event_id") or "")
        return None

    def _review_event_payload(
        self,
        review: dict[str, Any],
        *,
        approval_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "review_id": review.get("review_id"),
            "agent_run_id": review.get("agent_run_id"),
            "user_message": review.get("user_message"),
            "proposed_operations": review.get("proposed_operations") or [],
            "diff_summary": review.get("diff_summary") or {},
            "validation_summary": review.get("validation_summary") or {},
            "approval_decision": approval_decision or {},
        }

    def _append_jsonl(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(dict(payload), sort_keys=True) + "\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                events.append(payload)
        return events


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _workspace_updated_at(workspace: Mapping[str, Any]) -> str | None:
    provenance = workspace.get("provenance")
    if isinstance(provenance, Mapping):
        for field_name in ("updated_at",):
            value = provenance.get(field_name)
            if isinstance(value, str):
                return value
        agent_updates = provenance.get("agent_updates")
        if isinstance(agent_updates, list) and agent_updates:
            latest_update = agent_updates[-1]
            if isinstance(latest_update, Mapping):
                applied_at = latest_update.get("applied_at")
                if isinstance(applied_at, str):
                    return applied_at
        created_at = provenance.get("created_at")
        if isinstance(created_at, str):
            return created_at
    return None


def _paper_content_key(paper_id: str) -> str:
    return hashlib.sha256(paper_id.encode("utf-8")).hexdigest()


def _ordered_version_hashes(metadata: list[dict[str, Any]]) -> list[str]:
    """Recover a stable browser history for repositories created before v1 navigation."""

    by_hash = {
        str(item.get("version_hash")): item
        for item in metadata
        if item.get("version_hash")
    }
    children: dict[str | None, list[str]] = {}
    for version_hash, item in by_hash.items():
        parent = item.get("parent_version_hash")
        parent_hash = str(parent) if parent else None
        children.setdefault(parent_hash, []).append(version_hash)
    for values in children.values():
        values.sort(key=lambda version_hash: str(by_hash[version_hash].get("created_at") or ""))

    ordered: list[str] = []
    roots = children.get(None, [])
    if not roots:
        roots = sorted(by_hash, key=lambda value: str(by_hash[value].get("created_at") or ""))[:1]
    current = roots[0] if roots else None
    while current and current not in ordered:
        ordered.append(current)
        descendants = children.get(current, [])
        current = descendants[-1] if descendants else None
    for version_hash in sorted(
        by_hash,
        key=lambda value: str(by_hash[value].get("created_at") or ""),
    ):
        if version_hash not in ordered:
            ordered.append(version_hash)
    return ordered


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as file:
        temp_path = Path(file.name)
        json.dump(dict(payload), file, indent=2, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    try:
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _reclaim_dead_pipeline_run(path: Path, run: dict[str, Any]) -> bool:
    if run.get("status") not in {"queued", "running"}:
        return False
    runner_pid = run.get("runner_pid")
    if not isinstance(runner_pid, int) or runner_pid <= 0:
        return False
    try:
        os.kill(runner_pid, 0)
    except ProcessLookupError:
        run.update(
            {
                "status": "failed",
                "current_stage": None,
                "error": "Local backend stopped before this pipeline run completed.",
                "completed_at": _now(),
                "updated_at": _now(),
            }
        )
        _write_json_atomic(path, run)
        return True
    except PermissionError:
        return False
    return False


def _actor_fields(actor_type: str, actor_id: str | None) -> dict[str, str]:
    if actor_type not in ALLOWED_ACTOR_TYPES:
        raise ValueError(f"unsupported actor_type: {actor_type!r}")
    return {
        "actor_type": actor_type,
        "actor_id": actor_id or DEFAULT_ACTOR_IDS[actor_type],
    }


def _review_action_result(
    review: dict[str, Any],
    *,
    ok: bool,
    idempotent: bool,
    event_ids: list[str | None] | None = None,
    updated_workspace: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    clean_event_ids = [str(event_id) for event_id in event_ids or [] if event_id]
    result = {
        "ok": ok,
        "idempotent": idempotent,
        "status": review.get("status"),
        "review_id": review.get("review_id"),
        "workspace_id": review.get("workspace_id"),
        "agent_run_id": review.get("agent_run_id"),
        "event_ids": clean_event_ids,
        "persisted_event_ids": clean_event_ids,
        "persisted_version_hash": review.get("applied_workspace_version_hash"),
        "error_message": error_message or review.get("error_message"),
    }
    if updated_workspace is not None:
        result["updated_workspace"] = updated_workspace
    return result


def _safe_workspace_id(workspace_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", workspace_id.strip())
    cleaned = cleaned.strip(".-")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("workspace_id must contain at least one safe path character.")
    return cleaned[:180]


def _safe_version_hash(version_hash: str) -> str:
    normalized = version_hash.strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError("version_hash must be a 64-character SHA-256 hash.")
    return normalized


def _topic_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _now() -> str:
    return datetime.now(UTC).isoformat()
