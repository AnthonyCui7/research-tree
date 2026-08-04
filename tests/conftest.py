from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from research_tree.api.app import create_app
from research_tree.api.dependencies import get_repository
from research_tree.workspace.repository import LocalJsonWorkspaceRepository


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """An empty workspace data root for one test."""

    return tmp_path / "workspaces"


@pytest.fixture
def repository(data_dir: Path) -> LocalJsonWorkspaceRepository:
    return LocalJsonWorkspaceRepository(data_dir)


@pytest.fixture
def client(repository: LocalJsonWorkspaceRepository) -> TestClient:
    """A TestClient whose routes all share `repository`."""

    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    return TestClient(app)


@pytest.fixture
def seed_workspace(
    repository: LocalJsonWorkspaceRepository,
) -> Callable[..., str]:
    """Publish a workspace version and return its hash.

    Route-level tests only care that a workspace exists, so this stays minimal;
    tests that exercise workspace *content* build their own fixture.
    """

    def _seed(workspace_id: str = "workspace-1", topic: str = "Prompting") -> str:
        return repository.save_workspace_version(
            workspace_id,
            {
                "schema_version": "research_tree_workspace.v1",
                "workspace_id": workspace_id,
                "topic": topic,
                "title": topic,
                "root": {"node_id": "root", "label": topic, "overview": "Overview."},
                "tree": {"root_node_id": "root", "nodes": []},
                "paper_paths": [],
                "paper_cards": {},
            },
            actor="system",
            parent_version_hash=None,
            reason="seeded for tests",
        )

    return _seed


def read_sse_events(response: Any, limit: int) -> list[str]:
    """Collect up to `limit` non-comment SSE frames from a streaming response."""

    frames: list[str] = []
    buffer = ""
    for chunk in response.iter_text():
        buffer += chunk
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            frame = frame.strip()
            if frame and not frame.startswith(":"):
                frames.append(frame)
                if len(frames) >= limit:
                    return frames
    return frames
