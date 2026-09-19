from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from research_tree.agents.workspace.graph import build_workspace_agent_graph
from research_tree.agents.workspace.state import WorkspaceAgentInput


ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class WorkspaceAgentRunResult:
    thread_id: str
    final_output: dict[str, Any] | None
    state_updates: list[dict[str, Any]] = field(default_factory=list)


def run_workspace_agent(
    input: WorkspaceAgentInput,
    *,
    thread_id: str | None = None,
    graph: Any | None = None,
    workspace_repository: Any | None = None,
    on_progress: ProgressCallback | None = None,
) -> WorkspaceAgentRunResult:
    """Run one turn to its end state.

    `on_progress` receives each progress event the nodes report (see
    `nodes._report_progress`) on the calling thread, as it happens.
    """

    # Callers own graph lifetime (the app builds one per process); building one
    # here is only for ad-hoc use such as scripts.
    active_graph = graph or build_workspace_agent_graph(
        workspace_repository=workspace_repository
    )
    active_thread_id = _thread_id_for_input(input, explicit_thread_id=thread_id)
    run_input = dict(input)
    run_input["thread_id"] = active_thread_id
    # The longest honest turn is twelve rounds of tools and two repairs, under
    # forty steps. The library's own ceiling has been 25 and is now 10,007;
    # the turn's does not depend on which.
    config = {"configurable": {"thread_id": active_thread_id}, "recursion_limit": 100}
    return _run_graph(
        active_graph,
        run_input,
        config,
        thread_id=active_thread_id,
        on_progress=on_progress,
    )


def _run_graph(
    graph: Any,
    graph_input: Any,
    config: dict[str, Any],
    *,
    thread_id: str,
    on_progress: ProgressCallback | None = None,
) -> WorkspaceAgentRunResult:
    updates: list[dict[str, Any]] = []
    final_output: dict[str, Any] | None = None
    # With a listener the stream carries the nodes' progress events alongside
    # the state snapshots, as (mode, chunk) pairs.
    stream_mode: Any = ["values", "custom"] if on_progress is not None else "values"
    # The graph never pauses, so only the state at the end of a turn is worth
    # keeping: a checkpoint after every superstep wrote the whole state, full
    # text and transcript included, forty times per edit.
    for chunk in graph.stream(
        graph_input, config=config, stream_mode=stream_mode, durability="exit"
    ):
        if isinstance(chunk, tuple) and len(chunk) == 2:
            mode, snapshot = chunk
            if mode == "custom":
                if on_progress is not None and isinstance(snapshot, dict):
                    on_progress(snapshot)
                continue
        else:
            snapshot = chunk
        if not isinstance(snapshot, dict):
            continue
        updates.append(snapshot)
        final_output = snapshot
    return WorkspaceAgentRunResult(
        thread_id=thread_id,
        final_output=final_output,
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
