from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from research_tree.agents.workspace.graph import build_workspace_agent_graph
from research_tree.agents.workspace.state import WorkspaceAgentInput


@dataclass(frozen=True)
class WorkspaceAgentRunResult:
    thread_id: str
    final_output: dict[str, Any] | None
    interrupted: bool
    interrupt_payloads: list[dict[str, Any]] = field(default_factory=list)
    streamed_messages: list[str] = field(default_factory=list)
    state_updates: list[dict[str, Any]] = field(default_factory=list)


_DEFAULT_GRAPH: Any | None = None
_DEFAULT_REPOSITORY_GRAPHS: dict[int, Any] = {}


def run_workspace_agent(
    input: WorkspaceAgentInput,
    *,
    thread_id: str | None = None,
    graph: Any | None = None,
    workspace_repository: Any | None = None,
) -> WorkspaceAgentRunResult:
    active_graph = graph or _default_graph(workspace_repository=workspace_repository)
    active_thread_id = _thread_id_for_input(input, explicit_thread_id=thread_id)
    run_input = dict(input)
    run_input["thread_id"] = active_thread_id
    config = {"configurable": {"thread_id": active_thread_id}}
    return _run_graph(active_graph, run_input, config, thread_id=active_thread_id)


def resume_workspace_agent(
    thread_id: str,
    resume_value: dict[str, Any],
    *,
    graph: Any | None = None,
    workspace_repository: Any | None = None,
) -> WorkspaceAgentRunResult:
    active_graph = graph or _default_graph(workspace_repository=workspace_repository)
    config = {"configurable": {"thread_id": thread_id}}
    return _run_graph(
        active_graph,
        Command(resume=resume_value),
        config,
        thread_id=thread_id,
    )


def _run_graph(
    graph: Any,
    graph_input: Any,
    config: dict[str, Any],
    *,
    thread_id: str,
) -> WorkspaceAgentRunResult:
    # LangGraph 0.6 exposes stream(); newer releases add the v3 event stream.
    # Supporting both keeps the declared dependency floor honest.
    if hasattr(graph, "stream_events"):
        return _drain_workspace_agent_stream(
            graph.stream_events(graph_input, config=config, version="v3"),
            thread_id=thread_id,
        )

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


def _default_graph(*, workspace_repository: Any | None = None) -> Any:
    global _DEFAULT_GRAPH
    if workspace_repository is not None:
        repository_key = id(workspace_repository)
        if repository_key not in _DEFAULT_REPOSITORY_GRAPHS:
            _DEFAULT_REPOSITORY_GRAPHS[repository_key] = build_workspace_agent_graph(
                workspace_repository=workspace_repository
            )
        return _DEFAULT_REPOSITORY_GRAPHS[repository_key]
    if _DEFAULT_GRAPH is None:
        _DEFAULT_GRAPH = build_workspace_agent_graph()
    return _DEFAULT_GRAPH


def _drain_workspace_agent_stream(stream: Any, *, thread_id: str) -> WorkspaceAgentRunResult:
    streamed_messages: list[str] = []
    state_updates: list[dict[str, Any]] = []
    final_output: dict[str, Any] | None = None

    try:
        for snapshot in stream.values:
            if isinstance(snapshot, dict):
                state_updates.append(snapshot)
    except AttributeError:
        pass

    try:
        for message in stream.messages:
            text = getattr(message, "text", "")
            streamed_messages.append(str(text))
    except AttributeError:
        pass

    try:
        output = stream.output
        if isinstance(output, dict):
            final_output = output
    except AttributeError:
        for event in stream:
            if isinstance(event, dict) and event.get("method") == "values":
                data = ((event.get("params") or {}).get("data") or {})
                if isinstance(data, dict):
                    final_output = data

    interrupts = []
    for item in getattr(stream, "interrupts", ()) or ():
        value = getattr(item, "value", item)
        if isinstance(value, dict):
            interrupts.append(value)
        else:
            interrupts.append({"value": value})
    return WorkspaceAgentRunResult(
        thread_id=thread_id,
        final_output=final_output,
        interrupted=bool(getattr(stream, "interrupted", False)),
        interrupt_payloads=interrupts,
        streamed_messages=streamed_messages,
        state_updates=state_updates,
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
