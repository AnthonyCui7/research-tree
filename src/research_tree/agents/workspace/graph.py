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
    retrieval_runner: Any = None,
) -> Any:
    nodes = WorkspaceAgentNodes(
        llm_client=llm_client,
        **(
            {"workspace_constructor": workspace_constructor}
            if workspace_constructor is not None
            else {}
        ),
        **({"retrieval_runner": retrieval_runner} if retrieval_runner is not None else {}),
    )
    builder = StateGraph(
        WorkspaceAgentState,
        input_schema=WorkspaceAgentInput,
        output_schema=WorkspaceAgentOutput,
    )
    builder.add_node("load_workspace", nodes.load_workspace)
    builder.add_node(
        "classify_intent",
        nodes.classify_intent,
        destinations=("build_workspace_context",),
    )
    builder.add_node(
        "build_workspace_context",
        nodes.build_workspace_context,
        cache_policy=CachePolicy(key_func=workspace_context_cache_key),
    )
    builder.add_node(
        "plan_next_action",
        nodes.plan_next_action,
        destinations=(
            "answer_chat",
            "critique_workspace",
            "construct_workspace_modification",
            "prepare_retrieval_rerun",
            "repair_workspace_proposal",
            "finalize_response",
        ),
    )
    builder.add_node("answer_chat", nodes.answer_chat)
    builder.add_node("critique_workspace", nodes.critique_workspace)
    builder.add_node("prepare_retrieval_rerun", nodes.prepare_retrieval_rerun)
    builder.add_node("validate_rerun_args", nodes.validate_rerun_args)
    builder.add_node(
        "maybe_review_expensive_rerun",
        nodes.maybe_review_expensive_rerun,
        destinations=("rerun_candidate_pipeline", "finalize_rejection"),
    )
    builder.add_node("rerun_candidate_pipeline", nodes.rerun_candidate_pipeline)
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
            "human_review_proposal",
            "repair_workspace_proposal",
            "finalize_validation_failure",
        ),
    )
    builder.add_node("repair_workspace_proposal", nodes.repair_workspace_proposal)
    builder.add_node(
        "human_review_proposal",
        nodes.human_review_proposal,
        destinations=(
            "apply_patch_in_memory",
            "validate_user_edited_patch",
            "finalize_rejection",
        ),
    )
    builder.add_node("validate_user_edited_patch", nodes.validate_user_edited_patch)
    builder.add_node("apply_patch_in_memory", nodes.apply_patch_in_memory)
    builder.add_node("finalize_response", nodes.finalize_response)
    builder.add_node("finalize_rejection", nodes.finalize_rejection)
    builder.add_node("finalize_validation_failure", nodes.finalize_validation_failure)

    builder.add_edge(START, "load_workspace")
    builder.add_edge("load_workspace", "classify_intent")
    builder.add_edge("build_workspace_context", "plan_next_action")
    builder.add_edge("answer_chat", "finalize_response")
    builder.add_edge("critique_workspace", "finalize_response")
    builder.add_edge("prepare_retrieval_rerun", "validate_rerun_args")
    builder.add_conditional_edges("validate_rerun_args", route_after_rerun_guardrail)
    builder.add_edge("rerun_candidate_pipeline", "build_workspace_context")
    builder.add_edge("answer_with_guardrail_rejection", END)
    builder.add_edge("construct_workspace_modification", "derive_operations_and_diff")
    builder.add_edge("derive_operations_and_diff", "select_validators")
    builder.add_edge("select_validators", "fan_out_validators_with_Send")
    builder.add_conditional_edges(
        "fan_out_validators_with_Send",
        fan_out_validators_with_send,
    )
    builder.add_edge("run_validator", "combine_validation_results")
    builder.add_edge("repair_workspace_proposal", "derive_operations_and_diff")
    builder.add_edge("validate_user_edited_patch", "derive_operations_and_diff")
    builder.add_edge("apply_patch_in_memory", "finalize_response")
    builder.add_edge("finalize_response", END)
    builder.add_edge("finalize_rejection", END)
    builder.add_edge("finalize_validation_failure", END)

    return builder.compile(
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
        cache=cache if cache is not None else InMemoryCache(),
        name="WorkspaceAgentGraph",
    )

