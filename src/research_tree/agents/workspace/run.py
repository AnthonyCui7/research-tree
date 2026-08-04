from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from research_tree.agents.workspace.graph import build_workspace_agent_graph
from research_tree.agents.workspace.state import WorkspaceAgentInput


@dataclass(frozen=True)
class WorkspaceAgentRunResult:
    thread_id: str
    final_output: dict[str, Any] | None
    interrupted: bool
    interrupt_payloads: list[dict[str, Any]] = field(default_factory=list)
    state_updates: list[dict[str, Any]] = field(default_factory=list)


def run_workspace_agent(
    input: WorkspaceAgentInput,
    *,
    thread_id: str | None = None,
    graph: Any | None = None,
    workspace_repository: Any | None = None,
) -> WorkspaceAgentRunResult:
    # Callers own graph lifetime (the app builds one per process); building one
    # here is only for ad-hoc use such as scripts.
    active_graph = graph or build_workspace_agent_graph(
        workspace_repository=workspace_repository
    )
    active_thread_id = _thread_id_for_input(input, explicit_thread_id=thread_id)
    run_input = dict(input)
    run_input["thread_id"] = active_thread_id
    config = {"configurable": {"thread_id": active_thread_id}}
    return _run_graph(active_graph, run_input, config, thread_id=active_thread_id)


def _run_graph(
    graph: Any,
    graph_input: Any,
    config: dict[str, Any],
    *,
    thread_id: str,
) -> WorkspaceAgentRunResult:
    updates: list[dict[str, Any]] = []
    final_output: dict[str, Any] | None = None
    interrupts: list[dict[str, Any]] = []
    for snapshot in graph.stream(graph_input, config=config, stream_mode="values"):
        if not isinstance(snapshot, dict):
            continue
        updates.append(snapshot)
        final_output = snapshot
        for item in snapshot.get("__interrupt__") or []:
            value = getattr(item, "value", item)
            interrupts.append(value if isinstance(value, dict) else {"value": value})
    return WorkspaceAgentRunResult(
        thread_id=thread_id,
        final_output=final_output,
        interrupted=bool(interrupts),
        interrupt_payloads=interrupts,
        state_updates=updates,
    )


def _thread_id_for_input(
    input: WorkspaceAgentInput,
    *,
    explicit_thread_id: str | None,
) -> str:
    raw_thread_id = explicit_thread_id or input.get("thread_id")
    if raw_thread_id:
        return str(raw_thread_id)[:254]
    workspace_id = str(input.get("workspace_id") or "workspace")
    compact_workspace_id = "".join(
        char if char.isalnum() or char in ("-", "_", ":") else "-"
        for char in workspace_id
    )[:80]
    return f"workspace-agent:{compact_workspace_id}:{uuid4().hex[:12]}"
