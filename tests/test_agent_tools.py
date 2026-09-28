"""Tools the workspace agent calls, and the rerun-approval path they feed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from research_tree.agents.workspace.tools import (
    ALL_TOOLS,
    MAX_TOOL_RESULT_CHARACTERS,
    ToolContext,
    run_tool,
    tool_schemas,
    web_search_enabled,
)
from research_tree.services.errors import ReviewConflictError
from research_tree.services.reviews import WorkspaceReviewService
from research_tree.workspace.repository import LocalJsonWorkspaceRepository


def _workspace() -> dict[str, Any]:
    return {
        "schema_version": "research_tree_workspace.v1",
        "workspace_id": "workspace-1",
        "topic": "prompting",
        "title": "Prompting",
        "root": {"node_id": "root", "label": "Prompting", "overview": "Overview."},
        "tree": {
            "root_node_id": "root",
            "nodes": [
                {
                    "node_id": "branch-main",
                    "parent_id": "root",
                    "label": "Chain of Thought",
                    "description": "Step by step reasoning.",
                    "is_leaf": True,
                }
            ],
        },
        "paper_paths": [
            {
                "path_id": "path-1",
                "branch_node_id": "branch-main",
                "paper_ids": ["p1"],
                "paper_steps": [{"paper_id": "p1", "order": 1}],
            }
        ],
        "paper_cards": {
            "p1": {"paper_id": "p1", "title": "Chain of Thought Prompting", "tldr": "Reasoning steps."}
        },
    }


def _context(**overrides: Any) -> ToolContext:
    defaults: dict[str, Any] = {
        "workspace": _workspace(),
        "workspace_id": "workspace-1",
        "repository": None,
        "repo_root": Path("."),
    }
    return ToolContext(**{**defaults, **overrides})


class TestReadTools:
    def test_full_text_that_grows_when_escaped_still_reads_on(self) -> None:
        class Contents:
            def get_paper_content(self, workspace_id: str, paper_id: str) -> dict[str, Any]:
                # Every character escapes to six: "\u00e9".
                return {"full_text": "é" * 20_000}

        raw = run_tool("get_paper_full_text", _context(repository=Contents()), {"paper_id": "p1"})
        result = json.loads(raw)

        assert len(raw) <= MAX_TOOL_RESULT_CHARACTERS
        assert "truncated" not in result
        assert result["next_offset"] == len(result["full_text"])

    def test_get_branch_returns_its_paths_and_papers(self) -> None:
        result = json.loads(run_tool("get_branch", _context(), {"branch_id": "branch-main"}))

        assert result["branch"]["label"] == "Chain of Thought"
        assert result["paper_ids"] == ["p1"]

    def test_get_branch_lists_valid_ids_when_asked_for_a_missing_one(self) -> None:
        result = json.loads(run_tool("get_branch", _context(), {"branch_id": "nope"}))

        # Telling the model what does exist lets it recover without another turn.
        assert result["available_branch_ids"] == ["branch-main"]

    def test_get_paper_reads_a_card(self) -> None:
        result = json.loads(run_tool("get_paper", _context(), {"paper_id": "p1"}))

        assert result["paper_card"]["title"] == "Chain of Thought Prompting"

    def test_search_workspace_finds_papers_and_branches(self) -> None:
        both = json.loads(
            run_tool("search_workspace", _context(), {"query": "chain of thought"})
        )
        paper_only = json.loads(
            run_tool("search_workspace", _context(), {"query": "prompting"})
        )

        assert {match["id"] for match in both["matches"]} == {"p1", "branch-main"}
        assert [match["kind"] for match in paper_only["matches"]] == ["paper"]

    def test_search_workspace_ranks_stronger_matches_first(self) -> None:
        result = json.loads(
            run_tool("search_workspace", _context(), {"query": "chain thought prompting"})
        )

        # The paper title carries all three terms; the branch label has two.
        assert result["matches"][0]["id"] == "p1"

    def test_an_unknown_tool_is_reported_not_raised(self) -> None:
        result = json.loads(run_tool("not_a_tool", _context(), {}))

        assert "unknown tool" in result["error"]

    def test_a_failing_handler_becomes_a_tool_error(self) -> None:
        with patch.dict(ALL_TOOLS, {}, clear=False):
            broken = ALL_TOOLS["get_branch"]
            with patch.object(
                type(broken), "handler", property(lambda _self: _boom)
            ):
                result = json.loads(run_tool("get_branch", _context(), {"branch_id": "x"}))

        assert "get_branch failed" in result["error"]

    def test_oversized_results_are_truncated(self) -> None:
        workspace = _workspace()
        workspace["paper_cards"]["p1"]["tldr"] = "x" * (MAX_TOOL_RESULT_CHARACTERS + 10)

        result = json.loads(
            run_tool("get_paper", _context(workspace=workspace), {"paper_id": "p1"})
        )

        # Results are replayed on every later turn, so an unbounded one is paid
        # for repeatedly.
        assert result["truncated"] is True


class TestToolSchemas:
    def test_every_tool_is_offered_with_a_json_schema(self) -> None:
        schemas = tool_schemas(include_web_search=False)

        assert {schema["name"] for schema in schemas} == set(ALL_TOOLS)
        for schema in schemas:
            assert schema["type"] == "function"
            assert schema["parameters"]["additionalProperties"] is False

    def test_web_search_is_added_only_when_asked_for(self) -> None:
        without = tool_schemas(include_web_search=False)
        with_search = tool_schemas(include_web_search=True)

        assert {"type": "web_search"} not in without
        assert {"type": "web_search"} in with_search

    def test_web_search_can_be_switched_off_by_environment(self) -> None:
        with patch.dict("os.environ", {"RESEARCH_TREE_AGENT_WEB_SEARCH": "0"}):
            assert web_search_enabled() is False
        with patch.dict("os.environ", {"RESEARCH_TREE_AGENT_WEB_SEARCH": "1"}):
            assert web_search_enabled() is True

    def test_only_the_action_tools_are_terminal(self) -> None:
        terminal = {name for name, tool in ALL_TOOLS.items() if tool.terminal}

        assert terminal == {
            "propose_workspace_edit",
            "propose_pipeline_rerun",
            "critique_workspace",
        }
        # Everything terminal routes into the review path rather than acting.
        for name in terminal:
            assert ALL_TOOLS[name].handler is None


class TestPipelineRerunReview:
    def test_approving_a_rerun_review_starts_that_stage(self, tmp_path: Path) -> None:
        repository = LocalJsonWorkspaceRepository(tmp_path)
        base_hash = _seed(repository)
        _save_rerun_review(repository, base_hash, stage="construct")
        started: list[dict[str, Any]] = []
        service = WorkspaceReviewService(
            repository, pipeline_service=_FakePipeline(started)
        )

        result = service.approve_review("workspace-1", "review-rerun")

        assert started == [{"workspace_id": "workspace-1", "start_stage": "construct"}]
        assert result["status"] == "approved_applied"
        assert result["pipeline_run"]["run_id"] == "pipeline_1"
        # A rerun publishes no workspace version of its own.
        assert result["persisted_version_hash"] is None
        assert repository.get_review("workspace-1", "review-rerun")["status"] == "approved_applied"

    def test_rejecting_a_rerun_review_starts_nothing(self, tmp_path: Path) -> None:
        repository = LocalJsonWorkspaceRepository(tmp_path)
        base_hash = _seed(repository)
        _save_rerun_review(repository, base_hash, stage="candidates")
        started: list[dict[str, Any]] = []
        service = WorkspaceReviewService(
            repository, pipeline_service=_FakePipeline(started)
        )

        result = service.reject_review("workspace-1", "review-rerun")

        assert started == []
        assert result["status"] == "rejected"

    def test_approving_twice_starts_one_run(self, tmp_path: Path) -> None:
        repository = LocalJsonWorkspaceRepository(tmp_path)
        base_hash = _seed(repository)
        _save_rerun_review(repository, base_hash, stage="candidates")
        started: list[dict[str, Any]] = []
        service = WorkspaceReviewService(
            repository, pipeline_service=_FakePipeline(started)
        )

        service.approve_review("workspace-1", "review-rerun")
        second = service.approve_review("workspace-1", "review-rerun")

        assert len(started) == 1
        assert second["idempotent"] is True

    def test_a_rebuild_proposed_for_an_older_version_is_refused_and_retired(
        self, tmp_path: Path
    ) -> None:
        from research_tree.services.errors import StaleWorkspaceError

        repository = LocalJsonWorkspaceRepository(tmp_path)
        base_hash = _seed(repository)
        _save_rerun_review(repository, base_hash, stage="construct")
        edited = {**_workspace(), "title": "Renamed by hand since"}
        repository.save_workspace_version(
            "workspace-1", edited, actor="user", parent_version_hash=base_hash, reason="hand edit"
        )
        started: list[dict[str, Any]] = []
        service = WorkspaceReviewService(
            repository, pipeline_service=_FakePipeline(started, repository)
        )

        with pytest.raises(StaleWorkspaceError):
            service.approve_review("workspace-1", "review-rerun")

        assert started == []
        assert repository.get_review("workspace-1", "review-rerun")["status"] == "rejected"

    def test_approving_a_rejected_rerun_conflicts(self, tmp_path: Path) -> None:
        repository = LocalJsonWorkspaceRepository(tmp_path)
        base_hash = _seed(repository)
        _save_rerun_review(repository, base_hash, stage="candidates")
        service = WorkspaceReviewService(repository, pipeline_service=_FakePipeline([]))
        service.reject_review("workspace-1", "review-rerun")

        with pytest.raises(ReviewConflictError):
            service.approve_review("workspace-1", "review-rerun")


class _FakePipeline:
    """Holds `rerun` to the contract the real service keeps: a stale hash is a conflict."""

    def __init__(self, started: list[dict[str, Any]], repository: Any = None) -> None:
        self.started = started
        self.repository = repository

    def rerun(
        self,
        workspace_id: str,
        *,
        start_stage: str,
        expected_version_hash: str | None = None,
    ) -> dict[str, Any]:
        if self.repository is not None and expected_version_hash:
            from research_tree.services.errors import StaleWorkspaceError
            from research_tree.workspace.context import workspace_version_hash

            current = self.repository.get_current_workspace(workspace_id)
            if workspace_version_hash(current) != expected_version_hash:
                raise StaleWorkspaceError("Workspace changed.")
        self.started.append({"workspace_id": workspace_id, "start_stage": start_stage})
        return {"run_id": "pipeline_1", "workspace_id": workspace_id, "status": "queued"}


def _boom(_context: ToolContext, _arguments: dict[str, Any]) -> Any:
    raise RuntimeError("handler exploded")


def _seed(repository: LocalJsonWorkspaceRepository) -> str:
    return repository.save_workspace_version(
        "workspace-1",
        _workspace(),
        actor="system",
        parent_version_hash=None,
        reason="seeded for tests",
    )


def _save_rerun_review(
    repository: LocalJsonWorkspaceRepository,
    base_hash: str,
    *,
    stage: str,
) -> None:
    repository.save_pending_review(
        "workspace-1",
        review_id="review-rerun",
        agent_run_id="agent_run_1",
        base_workspace_version_hash=base_hash,
        user_message="Find more papers.",
        proposed_workspace={},
        proposed_operations=[],
        diff_summary={},
        validation_summary={},
        interrupt_payload={"type": "pipeline_rerun_approval", "review_id": "review-rerun"},
        review_type="pipeline_rerun",
        pipeline_rerun={"stage": stage, "reason": "pool is thin", "normalized_args": {}},
    )
