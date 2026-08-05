from __future__ import annotations

from typing import Any

from langgraph.cache.memory import InMemoryCache
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import CachePolicy

from research_tree.agents.workspace.cache import workspace_context_cache_key
from research_tree.agents.workspace.llm import WorkspaceAgentLlmClient
from research_tree.agents.workspace.nodes import WorkspaceAgentNodes
from research_tree.agents.workspace.routing import (
    fan_out_validators_with_send,
    route_after_rerun_guardrail,
)
from research_tree.agents.workspace.state import (
    WorkspaceAgentInput,
    WorkspaceAgentOutput,
    WorkspaceAgentState,
)


def build_workspace_agent_graph(
    *,
    checkpointer: Any = None,
    cache: Any = None,
    llm_client: WorkspaceAgentLlmClient | None = None,
    workspace_constructor: Any = None,
    workspace_repository: Any = None,
) -> Any:
    nodes = WorkspaceAgentNodes(
        llm_client=llm_client,
        **(
            {"workspace_constructor": workspace_constructor}
            if workspace_constructor is not None
            else {}
        ),
        **(
            {"workspace_repository": workspace_repository}
            if workspace_repository is not None
            else {}
        ),
    )
    builder = StateGraph(
        WorkspaceAgentState,
        input_schema=WorkspaceAgentInput,
        output_schema=WorkspaceAgentOutput,
    )
    builder.add_node("begin_turn", nodes.begin_turn)
    builder.add_node("load_workspace", nodes.load_workspace)
    builder.add_node(
        "build_workspace_context",
        nodes.build_workspace_context,
        cache_policy=CachePolicy(key_func=workspace_context_cache_key),
        destinations=("construct_workspace_modification", "critique_workspace"),
    )
    builder.add_node(
        "agent_loop",
        nodes.agent_loop,
        destinations=(
            "execute_tools",
            "build_workspace_context",
            "prepare_retrieval_rerun",
            "finalize_response",
        ),
    )
    builder.add_node("execute_tools", nodes.execute_tools, destinations=("agent_loop",))
    builder.add_node("critique_workspace", nodes.critique_workspace)
    builder.add_node("prepare_retrieval_rerun", nodes.prepare_retrieval_rerun)
    builder.add_node("validate_rerun_args", nodes.validate_rerun_args)
    builder.add_node("persist_rerun_review", nodes.persist_rerun_review)
    builder.add_node(
        "answer_with_guardrail_rejection",
        nodes.answer_with_guardrail_rejection,
    )
    builder.add_node(
        "construct_workspace_modification",
        nodes.construct_workspace_modification,
    )
    builder.add_node("derive_operations_and_diff", nodes.derive_operations_and_diff)
    builder.add_node("select_validators", nodes.select_validators)
    builder.add_node(
        "fan_out_validators_with_Send",
        nodes.fan_out_validators_with_Send,
    )
    builder.add_node("run_validator", nodes.run_validator)
    builder.add_node(
        "combine_validation_results",
        nodes.combine_validation_results,
        destinations=(
            "skeptic_review_proposal",
            "repair_workspace_proposal",
            "finalize_validation_failure",
            "finalize_response",
        ),
    )
    builder.add_node("skeptic_review_proposal", nodes.skeptic_review_proposal)
    builder.add_node("persist_pending_review", nodes.persist_pending_review)
    builder.add_node("repair_workspace_proposal", nodes.repair_workspace_proposal)
    builder.add_node("finalize_response", nodes.finalize_response)
    builder.add_node("finalize_validation_failure", nodes.finalize_validation_failure)

    builder.add_edge(START, "begin_turn")
    builder.add_edge("begin_turn", "load_workspace")
    # The loop opens on the workspace summary alone; the heavy context is built
    # only for the paths that read it.
    builder.add_edge("load_workspace", "agent_loop")
    builder.add_edge("critique_workspace", "finalize_response")
    builder.add_edge("prepare_retrieval_rerun", "validate_rerun_args")
    builder.add_conditional_edges("validate_rerun_args", route_after_rerun_guardrail)
    builder.add_edge("answer_with_guardrail_rejection", END)
    builder.add_edge("construct_workspace_modification", "derive_operations_and_diff")
    builder.add_edge("derive_operations_and_diff", "select_validators")
    builder.add_edge("select_validators", "fan_out_validators_with_Send")
    builder.add_conditional_edges(
        "fan_out_validators_with_Send",
        fan_out_validators_with_send,
    )
    builder.add_edge("run_validator", "combine_validation_results")
    # One skeptic reading rides on every proposal that survives validation.
    builder.add_edge("skeptic_review_proposal", "persist_pending_review")
    # A proposal ends the run. Approval happens later through the reviews API,
    # not by resuming this graph.
    builder.add_edge("persist_pending_review", "finalize_response")
    builder.add_edge("persist_rerun_review", "finalize_response")
    builder.add_edge("repair_workspace_proposal", "derive_operations_and_diff")
    builder.add_edge("finalize_response", END)
    builder.add_edge("finalize_validation_failure", END)

    return builder.compile(
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
        cache=cache if cache is not None else InMemoryCache(),
        name="WorkspaceAgentGraph",
    )
