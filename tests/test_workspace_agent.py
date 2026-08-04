from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False

from research_tree.agents.workspace.cache import workspace_context_cache_key
from research_tree.retrieval.pipeline_args import validate_pipeline_rerun_request
from research_tree.workspace.context import build_workspace_chat_context, workspace_version_hash

if LANGGRAPH_AVAILABLE:
    from research_tree.agents.workspace.llm import (
        AgentTurn,
        DeterministicWorkspaceAgentLlmClient,
        ToolCall,
    )
    from research_tree.agents.workspace.graph import build_workspace_agent_graph
    from research_tree.agents.workspace.nodes import WorkspaceAgentNodes
    from research_tree.agents.workspace.state import WorkspaceAgentState
    from research_tree.workspace.repository import LocalJsonWorkspaceRepository


@unittest.skipUnless(LANGGRAPH_AVAILABLE, "langgraph is not installed")
class WorkspaceAgentGraphTest(unittest.TestCase):
    def test_graph_builds_and_compiles(self) -> None:
        graph = build_workspace_agent_graph(llm_client=_chat_llm())

        self.assertIsNotNone(graph)

    def test_offline_chat_returns_a_workspace_reading_route(self) -> None:
        client = DeterministicWorkspaceAgentLlmClient()
        prompt = "# Payload\n" + json.dumps(
            {
                "workspace_context": {
                    "reading_order": [
                        {"order": 2, "paper_id": "paper-2", "reason": "Builds on the first paper."},
                        {"order": 1, "paper_id": "paper-1", "reason": "Introduces the core idea."},
                    ],
                    "visible_paper_cards": {
                        "paper-1": {"title": "Foundational Paper"},
                        "paper-2": {"title": "Follow-up Paper"},
                    },
                }
            }
        )

        response = client.complete_text(prompt=prompt)

        self.assertIn("local reading route", response)
        self.assertLess(response.index("Foundational Paper"), response.index("Follow-up Paper"))

    def test_state_reducers_append(self) -> None:
        def first(_state: WorkspaceAgentState) -> dict[str, object]:
            return {
                "warnings": ["w1"],
                "errors": ["e1"],
                "action_history": [{"a": 1}],
                "validation_results": [{"v": 1}],
            }

        def second(_state: WorkspaceAgentState) -> dict[str, object]:
            return {
                "warnings": ["w2"],
                "errors": ["e2"],
                "action_history": [{"a": 2}],
                "validation_results": [{"v": 2}],
            }

        builder = StateGraph(WorkspaceAgentState)
        builder.add_node("first", first)
        builder.add_node("second", second)
        builder.add_edge(START, "first")
        builder.add_edge("first", "second")
        builder.add_edge("second", END)
        graph = builder.compile(checkpointer=InMemorySaver())

        result = graph.invoke({}, {"configurable": {"thread_id": "reducers"}})

        self.assertEqual(result["warnings"], ["w1", "w2"])
        self.assertEqual(result["errors"], ["e1", "e2"])
        self.assertEqual(result["action_history"], [{"a": 1}, {"a": 2}])
        self.assertEqual(result["validation_results"], [{"v": 1}, {"v": 2}])

    def test_terminal_tool_calls_route_to_their_action_node(self) -> None:
        # Editing and critique both need the heavy workspace context, so they
        # route through the node that builds it; a rerun goes straight on.
        for tool_name, arguments, expected_node in [
            ("propose_workspace_edit", {"instruction": "rename branch"}, "build_workspace_context"),
            ("propose_pipeline_rerun", {"stage": "candidates", "reason": "thin"}, "prepare_retrieval_rerun"),
            ("critique_workspace", {}, "build_workspace_context"),
        ]:
            nodes = WorkspaceAgentNodes(
                llm_client=DeterministicWorkspaceAgentLlmClient(
                    tool_turns=[_tool_turn(tool_name, arguments)]
                )
            )

            command = nodes.agent_loop(
                {"user_message": "test", "chat_context": {}, "workspace": _workspace()}
            )

            self.assertEqual(command.goto, expected_node)

    def test_read_tool_calls_route_to_execution_and_back(self) -> None:
        nodes = WorkspaceAgentNodes(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                tool_turns=[_tool_turn("search_workspace", {"query": "method"})]
            )
        )

        command = nodes.agent_loop(
            {"user_message": "what is here?", "chat_context": {}, "workspace": _workspace()}
        )

        self.assertEqual(command.goto, "execute_tools")
        # The call is kept in the transcript so the result can be paired to it.
        self.assertEqual(command.update["transcript_items"][-1]["name"], "search_workspace")

        executed = nodes.execute_tools(
            {
                "workspace": _workspace(),
                "workspace_id": "workspace-1",
                "transcript_items": command.update["transcript_items"],
            }
        )

        self.assertEqual(executed.goto, "agent_loop")
        output = executed.update["transcript_items"][-1]
        self.assertEqual(output["type"], "function_call_output")
        self.assertEqual(output["call_id"], "call_1")
        self.assertIn("Core Method", output["output"])

    def test_answering_without_tools_finalizes(self) -> None:
        nodes = WorkspaceAgentNodes(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                tool_turns=[_text_turn("The workspace covers two branches.")]
            )
        )

        command = nodes.agent_loop(
            {"user_message": "summarize", "chat_context": {}, "workspace": _workspace()}
        )

        self.assertEqual(command.goto, "finalize_response")
        self.assertEqual(command.update["final_response"], "The workspace covers two branches.")

    def test_the_last_round_answers_instead_of_calling_another_tool(self) -> None:
        """Running out of budget must still produce an answer.

        The model is offered no tools on the final round, so whatever it has
        gathered gets written up rather than thrown away as an error.
        """

        client = DeterministicWorkspaceAgentLlmClient(
            tool_turns=[_tool_turn("search_workspace", {"query": "anything"})]
        )
        nodes = WorkspaceAgentNodes(llm_client=client)

        command = nodes.agent_loop(
            {
                "user_message": "keep searching",
                "workspace_summary": {},
                "workspace": _workspace(),
                "tool_rounds": 7,
                "max_tool_rounds": 8,
            }
        )

        self.assertEqual(command.goto, "finalize_response")
        self.assertEqual(command.update["status"], "completed")
        self.assertTrue(command.update["final_response"])
        self.assertIn("tool-call limit", command.update["warnings"][0])

    def test_semantic_scholar_budget_is_spent_not_exceeded(self) -> None:
        from research_tree.agents.workspace.tools import (
            MAX_SEMANTIC_SCHOLAR_CALLS_PER_RUN,
            ToolContext,
            run_tool,
        )

        context = ToolContext(
            workspace=_workspace(),
            workspace_id="workspace-1",
            repository=None,
            repo_root=Path(tempfile.mkdtemp()),
            semantic_scholar_calls=MAX_SEMANTIC_SCHOLAR_CALLS_PER_RUN,
        )

        result = run_tool("search_semantic_scholar", context, {"query": "prompting"})

        # No client is ever constructed, so an exhausted budget cannot hit S2.
        self.assertIn("budget", result)
        self.assertEqual(context.semantic_scholar_calls, MAX_SEMANTIC_SCHOLAR_CALLS_PER_RUN)

    def test_chat_path_ends_without_modifying_workspace(self) -> None:
        graph = build_workspace_agent_graph(llm_client=_chat_llm("Workspace answer."))

        result = graph.invoke(
            {
                "workspace": _workspace(),
                "candidate_artifact": _candidate_artifact(),
                "user_message": "Explain the core method.",
            },
            {"configurable": {"thread_id": "chat-path"}},
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["final_response"], "Workspace answer.")
        self.assertNotIn("updated_workspace", result)

    def test_modification_path_calls_construct_workspace_mode(self) -> None:
        calls: list[dict[str, object]] = []

        def constructor(**kwargs):
            calls.append(kwargs)
            return _workspace(branch_label="Renamed Branch")

        graph = build_workspace_agent_graph(
            llm_client=_modify_llm(),
            workspace_constructor=constructor,
        )

        stream = graph.stream(
            {
                "workspace": _workspace(),
                "candidate_artifact": _candidate_artifact(),
                "user_message": "Rename the main branch.",
            },
            {"configurable": {"thread_id": "modify-calls"}},
        )
        list(stream)

        self.assertEqual(calls[0]["construction_mode"], "agent_modify_workspace")

    def test_remove_visible_paper_uses_deterministic_patch(self) -> None:
        calls: list[dict[str, object]] = []
        graph = build_workspace_agent_graph(
            llm_client=_modify_llm("Remove evaluation benchmark paper."),
            workspace_constructor=lambda **kwargs: calls.append(kwargs) or _workspace(),
        )

        list(
            graph.stream(
                {
                    "workspace": _workspace(),
                    "candidate_artifact": _candidate_artifact(),
                    "user_message": "Remove evaluation benchmark paper.",
                },
                {"configurable": {"thread_id": "deterministic-remove-paper"}},
            )
        )
        state = graph.get_state(
            {"configurable": {"thread_id": "deterministic-remove-paper"}}
        ).values

        self.assertEqual(calls, [])
        self.assertNotIn("p2", state["proposed_workspace"]["paper_cards"])
        self.assertIn("demote_visible_paper", state["diff_summary"]["operation_types"])
        self.assertTrue(state["approval_required"])

    def test_retrieval_path_creates_controlled_request(self) -> None:
        nodes = WorkspaceAgentNodes(llm_client=_retrieval_llm())

        result = nodes.prepare_retrieval_rerun(
            {
                "workspace": _workspace(),
                "next_action": {
                    "reason": "Need newer candidates.",
                    "retrieval_request": {
                        "topic": "retrieval augmented generation",
                        "max_candidates": 60,
                        "alpha": 2.0,
                        "reason": "Need newer candidates.",
                    },
                },
            }
        )

        self.assertEqual(result["retrieval_request"]["topic"], "retrieval augmented generation")
        self.assertEqual(result["retrieval_request"]["alpha"], 2.0)

    def test_retrieval_path_defaults_missing_reason_in_partial_request(self) -> None:
        nodes = WorkspaceAgentNodes(llm_client=_retrieval_llm())

        result = nodes.prepare_retrieval_rerun(
            {
                "workspace": _workspace(),
                "next_action": {
                    "reason": "Need more candidates.",
                    "retrieval_request": {
                        "topic": "retrieval augmented generation",
                        "max_candidates": 20,
                    },
                },
            }
        )

        self.assertEqual(result["retrieval_request"]["topic"], "retrieval augmented generation")
        self.assertEqual(result["retrieval_request"]["reason"], "Need more candidates.")

    def test_rerun_guardrails_allow_safe_alpha_depth_and_reject_algorithm_change(self) -> None:
        allowed = validate_pipeline_rerun_request(
            {
                "topic": "retrieval augmented generation",
                "max_candidates": 60,
                "max_initial_results": 20,
                "alpha": 2.0,
                "reason": "Prefer newer papers.",
            },
            repo_root=Path("."),
            allow_pipeline_rerun=True,
        )
        rejected = validate_pipeline_rerun_request(
            {
                "topic": "retrieval augmented generation",
                "scoring_algorithm": "new formula",
                "reason": "Change scoring.",
            },
            repo_root=Path("."),
            allow_pipeline_rerun=True,
        )

        self.assertTrue(allowed["allowed"])
        self.assertEqual(allowed["normalized_args"]["citation_age_exponent"], 2.0)
        self.assertFalse(rejected["allowed"])
        self.assertIn("scoring", rejected["rejection_reason"])

    def test_graph_rerun_guardrail_preserves_and_rejects_forbidden_fields(self) -> None:
        calls: list[object] = []
        graph = build_workspace_agent_graph(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                tool_turns=[
                    _tool_turn(
                        "propose_pipeline_rerun",
                        {
                            "stage": "candidates",
                            "reason": "test rejection",
                            "topic": "retrieval augmented generation",
                            # Not a parameter the agent may set.
                            "scoring_algorithm": "replace scoring formula",
                        },
                    )
                ]
            ),
            retrieval_runner=lambda config: calls.append(config) or _candidate_artifact(),
        )

        result = graph.invoke(
            {
                "workspace": _workspace(),
                "candidate_artifact": _candidate_artifact(),
                "user_message": "Use a different scoring algorithm.",
                "allow_pipeline_rerun": True,
            },
            {"configurable": {"thread_id": "graph-guardrail-forbidden"}},
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("scoring", "; ".join(result["errors"]))
        self.assertEqual(calls, [])

    def test_send_validation_fanout_runs_and_combines_results(self) -> None:
        graph = build_workspace_agent_graph(
            llm_client=_modify_llm(),
            workspace_constructor=lambda **_kwargs: _workspace(branch_label="Renamed Branch"),
        )
        config = {"configurable": {"thread_id": "fanout"}}

        list(
            graph.stream(
                {
                    "workspace": _workspace(),
                    "candidate_artifact": _candidate_artifact(),
                    "user_message": "Rename the branch.",
                },
                config,
            )
        )
        state = graph.get_state(config).values

        self.assertGreaterEqual(len(state["validation_results"]), 3)
        self.assertTrue(state["validation_summary"]["valid"])

    def test_invalid_proposal_routes_to_repair_until_valid(self) -> None:
        calls: list[str] = []

        def constructor(**kwargs):
            calls.append(kwargs["construction_mode"])
            if kwargs["construction_mode"] == "agent_modify_workspace":
                invalid = _workspace()
                invalid["paper_cards"] = {}
                return invalid
            return _workspace(branch_label="Repaired Branch")

        graph = build_workspace_agent_graph(
            llm_client=_modify_llm(),
            workspace_constructor=constructor,
        )

        list(
            graph.stream(
                {
                    "workspace": _workspace(),
                    "candidate_artifact": _candidate_artifact(),
                    "user_message": "Rename the branch.",
                },
                {"configurable": {"thread_id": "repair"}},
            )
        )

        self.assertEqual(calls, ["agent_modify_workspace", "workspace_repair"])

    def test_existing_construct_workspace_cli_behavior_still_works(self) -> None:
        from research_tree.cli.construct_workspace import main as construct_main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path = root / "llm_candidate_papers.json"
            raw_workspace_path = root / "raw_workspace.json"
            output_dir = root / "out"
            candidate_path.write_text(json.dumps(_candidate_artifact()), encoding="utf-8")
            raw_workspace_path.write_text(json.dumps(_workspace()), encoding="utf-8")

            exit_code = construct_main(
                [
                    "--candidate-json",
                    str(candidate_path),
                    "--llm-output-json",
                    str(raw_workspace_path),
                    "--output-dir",
                    str(output_dir),
                ]
            )
            workspace_exists = (output_dir / "workspace.json").exists()

        self.assertEqual(exit_code, 0)
        self.assertTrue(workspace_exists)


class WorkspaceAgentPureHelperTest(unittest.TestCase):
    def test_similar_papers_are_context_not_visible_count(self) -> None:
        workspace = _workspace()
        workspace["paper_cards"]["p1"]["similar_papers"] = [
            {"paper_id": "p3", "title": "Narrow Application"}
        ]

        context = build_workspace_chat_context(
            workspace=workspace,
            candidate_artifact=_candidate_artifact(),
        )

        self.assertEqual(context["visible_paper_count"], 2)
        self.assertEqual(context["similar_papers_context"], {})
        self.assertEqual(context["off_path_papers"], [])
        self.assertNotIn("candidates", context["source_candidate_artifact"])
        self.assertNotIn("p3", context["workspace"]["paper_cards"])
        self.assertEqual(context["workspace"]["discarded_candidates"], [])

        context = build_workspace_chat_context(
            workspace=workspace,
            candidate_artifact=_candidate_artifact(),
            include_similar_papers=True,
        )

        self.assertEqual(
            context["similar_papers_context"]["p1"][0]["paper_id"],
            "p3",
        )

    def test_context_cache_key_uses_workspace_version_hash(self) -> None:
        workspace = _workspace()
        state = {
            "workspace_version_hash": workspace_version_hash(workspace),
            "intent": {"target_paper_ids": ["p1"]},
        }
        changed_state = {
            **state,
            "workspace_version_hash": workspace_version_hash(
                _workspace(branch_label="Other")
            ),
        }

        self.assertNotEqual(
            workspace_context_cache_key(state),
            workspace_context_cache_key(changed_state),
        )


def _tool_turn(name: str, arguments: dict[str, object], call_id: str = "call_1") -> AgentTurn:
    """A model turn that calls one tool, shaped like a real Responses turn."""

    return AgentTurn(
        output_items=[
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments),
            }
        ],
        tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
        output_text=None,
    )


def _text_turn(text: str) -> AgentTurn:
    """A model turn that answers instead of calling a tool."""

    return AgentTurn(
        output_items=[
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        tool_calls=[],
        output_text=text,
    )


def _chat_llm(answer: str = "answer") -> DeterministicWorkspaceAgentLlmClient:
    return DeterministicWorkspaceAgentLlmClient(tool_turns=[_text_turn(answer)])


def _modify_llm(instruction: str = "Rename the branch.") -> DeterministicWorkspaceAgentLlmClient:
    return DeterministicWorkspaceAgentLlmClient(
        tool_turns=[_tool_turn("propose_workspace_edit", {"instruction": instruction})]
    )


def _retrieval_llm() -> DeterministicWorkspaceAgentLlmClient:
    return DeterministicWorkspaceAgentLlmClient(
        tool_turns=[
            _tool_turn(
                "propose_pipeline_rerun",
                {
                    "stage": "candidates",
                    "reason": "Need more candidates.",
                    "topic": "retrieval augmented generation",
                    "max_candidates": 60,
                },
            )
        ]
    )


def _seed_repository_current(repository) -> str:
    return repository.save_workspace_version(
        "rag__2026-07-05__test",
        _workspace(),
        actor="system",
        parent_version_hash=None,
        reason="seed fixture",
    )


def _candidate_artifact() -> dict[str, object]:
    return {
        "schema_version": "llm_candidate_papers.v1",
        "topic": "retrieval augmented generation",
        "workspace": "retrieval augmented generation",
        "candidate_pool_order": "age_adjusted_citation_score_desc",
        "non_survey_papers": [
            _candidate("p1", "Core Method"),
            _candidate("p2", "Evaluation Benchmark"),
            _candidate("p3", "Narrow Application"),
        ],
        "survey_papers": [
            {
                **_candidate("survey", "RAG Survey"),
                "is_survey": True,
            }
        ],
    }


def _candidate(paper_id: str, title: str) -> dict[str, object]:
    return {
        "paper_id": paper_id,
        "title": title,
        "abstract": f"{title} abstract.",
        "year": 2024,
        "authors": ["A. Author"],
        "venue": "Test Venue",
        "primary_link": f"https://example.com/{paper_id}",
        "citation_count": 10,
        "age_adjusted_rank": 1,
        "cross_encoder_relevance": 0.5,
        "is_survey": False,
        "found_by": ["test"],
    }


def _workspace(branch_label: str = "Main Branch") -> dict[str, object]:
    return {
        "schema_version": "research_tree_workspace.v1",
        "workspace_id": "rag__2026-07-05__test",
        "topic": "retrieval augmented generation",
        "title": "Retrieval-Augmented Generation",
        "scope": {
            "scope_label": "broad_topic",
            "scope_rationale": "Tiny fixture.",
            "visible_paper_budget": {
                "target_min": 1,
                "target_max": 3,
                "hard_max_default": 4,
            },
        },
        "source_candidate_artifact": {},
        "root": {
            "node_id": "root",
            "label": "Retrieval-Augmented Generation",
            "overview": "Overview.",
            "root_survey_type": "human_survey",
            "survey_anchor_paper_ids": ["survey"],
            "representative_paper_ids": ["p1"],
            "key_terms": [],
            "open_questions": [],
            "suggested_reading_direction": "Read core method then benchmark.",
        },
        "tree": {
            "root_node_id": "root",
            "nodes": [_branch_node("branch-main", "root", branch_label)],
        },
        "paper_paths": [
            {
                "path_id": "path-main",
                "branch_node_id": "branch-main",
                "path_type": "primary_timeline",
                "label": "Main path",
                "description": "Tiny path.",
                "paper_ids": ["p1", "p2"],
                "rationale": "Fixture.",
            }
        ],
        "paper_cards": {
            "p1": _paper_card("p1", "Core Method", branch_label),
            "p2": _paper_card("p2", "Evaluation Benchmark", branch_label),
        },
        "reading_order": [
            {"order": 1, "paper_id": "p1", "reason": "Start here."},
            {"order": 2, "paper_id": "p2", "reason": "Then evaluate."},
        ],
        "comparison_tables": [],
        "discarded_candidates": [
            {
                "paper_id": "p3",
                "title": "Narrow Application",
                "discard_reason": "application-specific",
                "possible_future_use": "off_path",
            }
        ],
        "provenance": {
            "workspace_constructor": "llm",
            "model": "test",
            "prompt_version": "workspace_construction.v1",
            "created_at": "2026-07-05T00:00:00+00:00",
            "warnings": [],
        },
    }


def _branch_node(node_id: str, parent_id: str, label: str) -> dict[str, object]:
    return {
        "node_id": node_id,
        "parent_id": parent_id,
        "label": label,
        "description": "Fixture branch.",
        "why_it_matters": "It matters.",
        "is_leaf": True,
        "child_node_ids": [],
        "primary_paper_ids": ["p1", "p2"] if node_id == "branch-main" else [],
        "secondary_paper_ids": [],
        "tags": [],
        "open_questions": [],
    }


def _paper_card(paper_id: str, title: str, branch_label: str) -> dict[str, object]:
    return {
        "paper_id": paper_id,
        "title": title,
        "authors": ["A. Author"],
        "year": 2024,
        "venue": "Test Venue",
        "primary_link": f"https://example.com/{paper_id}",
        "doi": None,
        "arxiv_id": None,
        "abstract": f"{title} abstract.",
        "primary_tree_location": {
            "node_id": "branch-main",
            "path": ["Retrieval-Augmented Generation", branch_label],
        },
        "secondary_tags": [],
        "reading_status": "unread",
        "paper_role": "method",
        "one_sentence_contribution": "Fixture contribution.",
        "problem": "",
        "core_idea": "",
        "method": "",
        "assumptions": "",
        "datasets_or_benchmarks": "",
        "results": "",
        "limitations": "",
        "why_it_belongs": "Fixture.",
        "read_before": [],
        "read_after": [],
        "user_notes": "",
        "similar_papers": [],
    }


if __name__ == "__main__":
    unittest.main()
