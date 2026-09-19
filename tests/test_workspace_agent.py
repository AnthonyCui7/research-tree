from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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
from research_tree.workspace.construction import (
    apply_workspace_edit_delta,
    construct_workspace,
    restore_derived_paper_card_fields,
)
from research_tree.workspace.context import (
    build_workspace_chat_context,
    build_workspace_summary,
    workspace_version_hash,
)
from research_tree.workspace.diff import derive_operations_and_diff_summary

if LANGGRAPH_AVAILABLE:
    from research_tree.agents.workspace.llm import (
        AgentTurn,
        DeterministicWorkspaceAgentLlmClient,
        ToolCall,
    )
    from research_tree.agents.workspace.graph import build_workspace_agent_graph
    from research_tree.agents.workspace.run import run_workspace_agent
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

    def test_state_reducers_append_and_reset_on_none(self) -> None:
        def first(_state: WorkspaceAgentState) -> dict[str, object]:
            return {
                "warnings": ["w1"],
                "errors": ["e1"],
                "persisted_event_ids": ["ev1"],
                "validation_results": [{"v": 1}],
            }

        def second(_state: WorkspaceAgentState) -> dict[str, object]:
            return {
                "warnings": ["w2"],
                # None clears the channel — the begin_turn reset semantics.
                "errors": None,
                "persisted_event_ids": ["ev2"],
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
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["persisted_event_ids"], ["ev1", "ev2"])
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

    def test_overview_and_reading_order_tools_expose_the_rest(self) -> None:
        from research_tree.agents.workspace.tools import ToolContext, run_tool

        context = ToolContext(
            workspace=_workspace(),
            workspace_id="workspace-1",
            repository=None,
            repo_root=Path(tempfile.mkdtemp()),
        )

        overview = json.loads(run_tool("get_workspace_overview", context, {}))
        self.assertEqual(overview["root"]["overview"], "Overview.")
        self.assertEqual(
            overview["discarded_candidates"][0]["title"], "Narrow Application"
        )
        self.assertNotIn("warnings", overview["provenance"])
        self.assertEqual(overview["counts"]["visible_papers"], 2)

        order = json.loads(run_tool("list_reading_order", context, {}))
        self.assertEqual(
            [entry["title"] for entry in order["reading_order"]],
            ["Core Method", "Evaluation Benchmark"],
        )
        [path] = order["paper_paths"]
        self.assertEqual(path["path_id"], "path-main")

    def test_workspace_summary_carries_complete_author_lists(self) -> None:
        workspace = _workspace()
        workspace["paper_cards"]["p1"]["authors"] = [
            "A. Author",
            "B. Builder",
            "C. Curator",
            "D. Distant",
        ]

        summary = build_workspace_summary(workspace)

        by_id = {line["paper_id"]: line for line in summary["papers"]}
        self.assertEqual(
            by_id["p1"]["authors"],
            ["A. Author", "B. Builder", "C. Curator", "D. Distant"],
        )
        self.assertEqual(by_id["p2"]["authors"], ["A. Author"])

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

    def test_a_streamed_run_reports_each_model_turn_and_tool(self) -> None:
        """The listener hears what the loop is about to do, with the subject
        resolved to something a reader recognises (a title, not an id)."""

        graph = build_workspace_agent_graph(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                tool_turns=[
                    _tool_turn("search_workspace", {"query": "method"}, call_id="call_1"),
                    _tool_turn("get_paper", {"paper_id": "p1"}, call_id="call_2"),
                    _text_turn("Answer."),
                ]
            )
        )
        events: list[dict[str, object]] = []

        result = run_workspace_agent(
            {"workspace": _workspace(), "user_message": "What is the core method?"},
            graph=graph,
            on_progress=events.append,
        )

        self.assertEqual(result.final_output["final_response"], "Answer.")
        self.assertEqual(
            events,
            [
                {"kind": "thinking"},
                {"kind": "tool", "name": "search_workspace", "subject": "method"},
                {"kind": "thinking"},
                {"kind": "tool", "name": "get_paper", "subject": "Core Method"},
                {"kind": "thinking"},
            ],
        )

    def test_a_streamed_edit_reports_its_stages_in_order(self) -> None:
        graph = build_workspace_agent_graph(
            llm_client=_modify_llm(),
            workspace_constructor=lambda **_kwargs: _workspace(branch_label="Renamed Branch"),
        )
        events: list[dict[str, object]] = []

        run_workspace_agent(
            {
                "workspace": _workspace(),
                "candidate_artifact": _candidate_artifact(),
                "user_message": "Rename the main branch.",
            },
            graph=graph,
            on_progress=events.append,
        )

        self.assertEqual(
            [event["stage"] for event in events if event["kind"] == "stage"],
            ["reading_workspace", "constructing", "validating", "skeptic", "saving_review"],
        )

    def test_an_unstreamed_run_reports_nothing_and_still_works(self) -> None:
        graph = build_workspace_agent_graph(llm_client=_chat_llm("Quiet answer."))

        result = run_workspace_agent(
            {"workspace": _workspace(), "user_message": "Explain."}, graph=graph
        )

        self.assertEqual(result.final_output["final_response"], "Quiet answer.")

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

    def test_remove_papers_edit_kind_uses_deterministic_patch(self) -> None:
        calls: list[dict[str, object]] = []
        graph = build_workspace_agent_graph(
            llm_client=_modify_llm(
                "Remove evaluation benchmark paper.",
                edit_kind="remove_papers",
                target_paper_ids=["p2"],
            ),
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

    def test_remove_papers_with_unknown_id_is_handed_back_without_a_proposal(self) -> None:
        calls: list[dict[str, object]] = []
        graph = build_workspace_agent_graph(
            llm_client=_modify_llm(
                "Remove the missing paper.",
                edit_kind="remove_papers",
                target_paper_ids=["p999"],
            ),
            workspace_constructor=lambda **kwargs: calls.append(kwargs) or _workspace(),
        )
        config = {"configurable": {"thread_id": "remove-unknown-id"}}

        list(
            graph.stream(
                {
                    "workspace": _workspace(),
                    "candidate_artifact": _candidate_artifact(),
                    "user_message": "Remove the missing paper.",
                },
                config,
            )
        )
        state = graph.get_state(config).values

        # Nothing is proposed, and the model hears which id was wrong so it
        # can answer or correct itself within the turn.
        self.assertEqual(calls, [])
        self.assertFalse(state["approval_required"])
        told = [
            item["output"]
            for item in state["transcript_items"]
            if item.get("type") == "function_call_output"
        ]
        self.assertEqual(len(told), 1)
        self.assertIn("p999", told[0])

    def test_removal_wording_with_refresh_edit_kind_never_deletes_a_paper(self) -> None:
        """'Remove the stale similar papers for X' must refresh, not delete."""

        graph = build_workspace_agent_graph(
            llm_client=_modify_llm(
                "Remove the stale similar papers for the benchmark paper.",
                edit_kind="refresh_similar_papers",
                target_paper_ids=["p2"],
            ),
            workspace_constructor=lambda **kwargs: self.fail(
                "similar-paper refresh must not call the construction model"
            ),
        )
        config = {"configurable": {"thread_id": "refresh-not-remove"}}

        list(
            graph.stream(
                {
                    "workspace": _workspace(),
                    "candidate_artifact": _candidate_artifact(),
                    "user_message": "Remove the stale similar papers for the benchmark paper.",
                },
                config,
            )
        )
        state = graph.get_state(config).values

        # The refresh path fails without the paper database artifact, but the
        # paper survives — nothing interpreted the wording as a deletion.
        self.assertIn("p2", state["proposed_workspace"]["paper_cards"])
        self.assertFalse(state["approval_required"])

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
        graph = build_workspace_agent_graph(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                tool_turns=[
                    _tool_turn(
                        "propose_pipeline_rerun",
                        {
                            "stage": "candidates",
                            "reason": "test rejection",
                            "message_to_user": "I want to rerun retrieval.",
                            "topic": "retrieval augmented generation",
                            # Not a parameter the agent may set.
                            "scoring_algorithm": "replace scoring formula",
                        },
                    )
                ]
            ),
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

    def test_allowed_rerun_always_becomes_a_pending_review(self) -> None:
        """No rerun starts inline: an allowed request persists a review."""

        graph = build_workspace_agent_graph(
            llm_client=_retrieval_llm(),
        )
        config = {"configurable": {"thread_id": "rerun-review"}}

        result = graph.invoke(
            {
                "workspace": _workspace(),
                "candidate_artifact": _candidate_artifact(),
                "user_message": "Find more candidate papers.",
                "allow_pipeline_rerun": True,
            },
            config,
        )
        state = graph.get_state(config).values

        self.assertTrue(result["approval_required"])
        self.assertEqual(state["approval_payload"]["type"], "pipeline_rerun_approval")
        self.assertEqual(
            result["final_response"],
            "I want the pipeline to gather fresh candidates.",
        )

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

    def test_add_paper_flows_from_s2_lookup_to_pending_review(self) -> None:
        """The mockup scenario: fetch a paper, then propose adding it.

        The recorded Semantic Scholar payload — never model-written metadata —
        must reach the constructor's candidate artifact so materialization
        keeps the new card.
        """

        calls: list[dict[str, object]] = []

        def constructor(**kwargs):
            calls.append(kwargs)
            return _workspace_with_added_paper()

        graph = build_workspace_agent_graph(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                tool_turns=[
                    _tool_turn(
                        "get_semantic_scholar_paper",
                        {"paper_id": "s2new"},
                        call_id="call_1",
                    ),
                    _tool_turn(
                        "propose_workspace_edit",
                        {
                            "instruction": "Add New Paper to the main branch.",
                            "edit_kind": "structural",
                            "message_to_user": "Adding New Paper would close the recency gap.",
                            "add_paper_ids": ["s2new"],
                        },
                        call_id="call_2",
                    ),
                ]
            ),
            workspace_constructor=constructor,
        )
        config = {"configurable": {"thread_id": "add-paper"}}

        with patch(
            "research_tree.agents.workspace.tools.SemanticScholarClient",
            _FakeSemanticScholarClient,
        ):
            result = graph.invoke(
                {
                    "workspace": _workspace(),
                    "user_message": "Add the New Paper you found.",
                },
                config,
            )
        state = graph.get_state(config).values

        offered = {
            paper["paper_id"]: paper
            for paper in calls[0]["candidate_artifact"]["non_survey_papers"]
        }
        self.assertIn("s2new", offered)
        # Metadata comes from the recorded provider payload.
        self.assertEqual(offered["s2new"]["title"], "New Paper")
        self.assertEqual(offered["s2new"]["citation_count"], 3)
        self.assertTrue(result["approval_required"])
        self.assertIn(
            "promote_candidate_paper",
            state["diff_summary"]["operation_types"],
        )
        self.assertEqual(
            result["final_response"],
            "Adding New Paper would close the recency gap.",
        )

    def test_add_paper_id_not_fetched_this_conversation_is_skipped_with_warning(self) -> None:
        nodes = WorkspaceAgentNodes(llm_client=_chat_llm())

        artifact, warnings = nodes._modification_candidate_artifact(
            {
                "workspace": _workspace(),
                "candidate_artifact": _candidate_artifact(),
                "next_action": {"add_paper_ids": ["never-fetched"]},
                "session_discovered_papers": {},
            }
        )

        offered_ids = {
            paper["paper_id"]
            for key in ("non_survey_papers", "survey_papers")
            for paper in artifact[key]
        }
        self.assertNotIn("never-fetched", offered_ids)
        self.assertEqual(len(warnings), 1)
        self.assertIn("never-fetched", warnings[0])

    def test_skeptic_notes_ride_on_the_review_payload(self) -> None:
        client = DeterministicWorkspaceAgentLlmClient(
            tool_turns=[
                _tool_turn(
                    "propose_workspace_edit",
                    {
                        "instruction": "Rename the branch.",
                        "edit_kind": "structural",
                        "message_to_user": "Renaming for clarity.",
                    },
                )
            ],
            structured_outputs=[
                {"objections": ["The new label may hide the benchmark line."]}
            ],
        )
        graph = build_workspace_agent_graph(
            llm_client=client,
            workspace_constructor=lambda **_kwargs: _workspace(
                branch_label="Renamed Branch"
            ),
        )
        config = {"configurable": {"thread_id": "skeptic-notes"}}

        result = graph.invoke(
            {
                "workspace": _workspace(),
                "candidate_artifact": _candidate_artifact(),
                "user_message": "Rename the branch.",
            },
            config,
        )
        state = graph.get_state(config).values

        self.assertTrue(result["approval_required"])
        self.assertEqual(
            state["approval_payload"]["skeptic_notes"],
            ["The new label may hide the benchmark line."],
        )

    def test_skeptic_failure_never_blocks_the_proposal(self) -> None:
        class _BrokenSkepticClient(DeterministicWorkspaceAgentLlmClient):
            def complete_structured(self, **_kwargs):
                raise RuntimeError("skeptic call failed")

        graph = build_workspace_agent_graph(
            llm_client=_BrokenSkepticClient(
                tool_turns=[
                    _tool_turn(
                        "propose_workspace_edit",
                        {
                            "instruction": "Rename the branch.",
                            "edit_kind": "structural",
                            "message_to_user": "Renaming for clarity.",
                        },
                    )
                ]
            ),
            workspace_constructor=lambda **_kwargs: _workspace(
                branch_label="Renamed Branch"
            ),
        )
        config = {"configurable": {"thread_id": "skeptic-broken"}}

        result = graph.invoke(
            {
                "workspace": _workspace(),
                "candidate_artifact": _candidate_artifact(),
                "user_message": "Rename the branch.",
            },
            config,
        )
        state = graph.get_state(config).values

        self.assertTrue(result["approval_required"])
        self.assertEqual(state["approval_payload"]["skeptic_notes"], [])

    def test_reused_thread_refetches_workspace_and_resets_turn_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            first_hash = _seed_repository_current(repository)
            graph = build_workspace_agent_graph(
                llm_client=DeterministicWorkspaceAgentLlmClient(
                    tool_turns=[_text_turn("First answer."), _text_turn("Second answer.")]
                ),
                workspace_repository=repository,
            )
            config = {"configurable": {"thread_id": "reused-thread"}}

            graph.invoke(
                {
                    "workspace_id": "rag__2026-07-05__test",
                    "user_message": "First question.",
                },
                config,
            )
            first_state = dict(graph.get_state(config).values)
            renamed = _workspace(branch_label="Renamed Between Turns")
            repository.save_workspace_version(
                "rag__2026-07-05__test",
                renamed,
                actor="user",
                parent_version_hash=first_hash,
                reason="rename between turns",
            )

            graph.invoke(
                {
                    "workspace_id": "rag__2026-07-05__test",
                    "user_message": "Second question.",
                },
                config,
            )
            second_state = dict(graph.get_state(config).values)

        # The second turn sees the approved rename, not the checkpointed copy.
        self.assertEqual(
            second_state["workspace_version_hash"],
            workspace_version_hash(renamed),
        )
        self.assertNotEqual(
            first_state["agent_run_id"], second_state["agent_run_id"]
        )
        # The transcript starts fresh: its opening prompt is the new message,
        # with no residue of the previous turn.
        opening = second_state["transcript_items"][0]["content"]
        self.assertIn("Second question.", opening)
        self.assertNotIn("First question.", opening)

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
            "next_action": {"target_paper_ids": ["p1"]},
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

    def test_context_cache_key_separates_accounts_workspaces_and_actions(self) -> None:
        """The cache is process-wide: one entry answers one account's one
        question about one version, never a different account's or action's."""

        base = {
            "workspace_id": "rag",
            "workspace_version_hash": "a" * 64,
            "next_action": {"action_type": "critique_workspace"},
        }
        edit = {**base, "next_action": {"action_type": "construct_workspace_modification"}}
        other_workspace = {**base, "workspace_id": "prompting"}

        self.assertEqual(
            workspace_context_cache_key(base, owner_id="u1"),
            workspace_context_cache_key(dict(base), owner_id="u1"),
        )
        self.assertNotEqual(
            workspace_context_cache_key(base, owner_id="u1"),
            workspace_context_cache_key(base, owner_id="u2"),
        )
        self.assertNotEqual(
            workspace_context_cache_key(base, owner_id="u1"),
            workspace_context_cache_key(edit, owner_id="u1"),
        )
        self.assertNotEqual(
            workspace_context_cache_key(base, owner_id="u1"),
            workspace_context_cache_key(other_workspace, owner_id="u1"),
        )

    def test_workspace_summary_carries_shape_and_paper_gists(self) -> None:
        workspace = _workspace()
        workspace["paper_cards"]["p1"]["tldr"] = "  A method that does the thing. "
        workspace["paper_cards"]["p1"]["citation_count"] = 120

        summary = build_workspace_summary(workspace)

        branch = summary["branches"][0]
        self.assertEqual(branch["parent_id"], "root")
        self.assertTrue(branch["is_leaf"])
        papers = {paper["paper_id"]: paper for paper in summary["papers"]}
        self.assertEqual(papers["p1"]["tldr"], "A method that does the thing.")
        self.assertEqual(papers["p1"]["year"], 2024)
        self.assertEqual(papers["p1"]["citation_count"], 120)
        # A card without a TLDR or citation count simply omits those keys.
        self.assertNotIn("tldr", papers["p2"])
        self.assertNotIn("citation_count", papers["p2"])

    def test_workspace_summary_survives_sparse_cards(self) -> None:
        workspace = _workspace()
        workspace["paper_cards"]["bare"] = {"title": "Bare Card"}

        summary = build_workspace_summary(workspace)

        papers = {paper["paper_id"]: paper for paper in summary["papers"]}
        self.assertEqual(papers["bare"], {"paper_id": "bare", "title": "Bare Card"})


class DiffTaxonomyTest(unittest.TestCase):
    def test_branch_description_change_yields_update_branch_details(self) -> None:
        before = _workspace()
        after = _workspace()
        after["tree"]["nodes"][0]["description"] = "A sharper definition."

        operations, diff_summary, _warnings = derive_operations_and_diff_summary(
            workspace=before,
            proposed_workspace=after,
        )

        [operation] = operations
        self.assertEqual(operation["operation_type"], "update_branch_details")
        self.assertEqual(operation["target_ids"], {"branch_id": "branch-main"})
        self.assertEqual(operation["before"], {"description": "Fixture branch."})
        self.assertEqual(operation["after"], {"description": "A sharper definition."})
        self.assertEqual(diff_summary["operation_types"], ["update_branch_details"])

    def test_root_label_and_why_it_matters_are_reviewable(self) -> None:
        before = _workspace()
        after = _workspace()
        after["root"]["label"] = "RAG, Renamed"
        after["root"]["why_it_matters"] = "Grounding changes what models can claim."

        operations, _diff_summary, _warnings = derive_operations_and_diff_summary(
            workspace=before,
            proposed_workspace=after,
        )

        [operation] = operations
        self.assertEqual(operation["operation_type"], "update_root_overview")
        self.assertEqual(
            sorted(operation["after"]), ["label", "why_it_matters"]
        )

    def test_removed_paper_path_yields_remove_paper_path(self) -> None:
        before = _workspace()
        after = _workspace()
        after["paper_paths"] = []

        operations, _diff_summary, _warnings = derive_operations_and_diff_summary(
            workspace=before,
            proposed_workspace=after,
        )

        [operation] = operations
        self.assertEqual(operation["operation_type"], "remove_paper_path")
        self.assertEqual(
            operation["target_ids"],
            {"path_id": "path-main", "branch_id": "branch-main"},
        )
        self.assertIsNone(operation["after"])

    def test_agent_edit_post_pass_preserves_derived_card_data(self) -> None:
        """A rename proposal must not wipe similar papers or provider metadata.

        The editing artifact strips derived card payloads for prompt economy;
        the post-pass must restore them *after* materialization, or the
        metadata-stripped artifact re-empties them — which shipped proposals
        that silently deleted every card's similar_papers on approval.
        """

        seed = _workspace()
        seed["paper_cards"]["p1"]["similar_papers"] = [
            {"paper_id": "p3", "title": "Narrow Application"}
        ]
        seed["paper_cards"]["p1"]["semantic_scholar_metadata"] = {"paperId": "p1"}
        seed["paper_cards"]["p1"]["paper_content"] = {"status": "extracted"}

        # One post-pass first, so the base is materialization-stable the way a
        # pipeline-built workspace is; the edit's diff then isolates the edit.
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            base = construct_workspace(
                candidate_artifact=_candidate_artifact(),
                base_workspace=seed,
                construction_mode="workspace_repair",
                agent_instruction="Stabilize the fixture.",
                run_metadata={"proposed_workspace": copy.deepcopy(seed)},
                model="test",
            )

        # What the editing model actually returns: the renamed workspace with
        # compact schema-shaped cards — only paper_id, location, tags, and
        # importance, the latter two often emitted empty rather than copied.
        model_output = copy.deepcopy(base)
        model_output["tree"]["nodes"][0]["label"] = "Renamed Branch"
        model_output["paper_cards"] = {
            paper_id: {
                "paper_id": paper_id,
                # The display path drifts between runs; only node_id is meaning.
                "primary_tree_location": {
                    "node_id": card["primary_tree_location"]["node_id"],
                    "path": " / ".join(card["primary_tree_location"]["path"]),
                },
                "secondary_tags": [],
                "importance": "",
            }
            for paper_id, card in base["paper_cards"].items()
        }
        model_output.pop("discarded_candidates", None)

        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            proposed = construct_workspace(
                candidate_artifact=_candidate_artifact(),
                base_workspace=base,
                construction_mode="workspace_repair",
                agent_instruction="Rename the main branch.",
                run_metadata={"proposed_workspace": model_output},
                model="test",
            )

        card = proposed["paper_cards"]["p1"]
        base_card = base["paper_cards"]["p1"]
        self.assertEqual(card["similar_papers"], base_card["similar_papers"])
        self.assertEqual(card["semantic_scholar_metadata"], {"paperId": "p1"})
        self.assertEqual(card["paper_content"], {"status": "extracted"})
        # Fields the model does not own come back verbatim; its own editorial
        # fields come back when emitted empty.
        self.assertEqual(card["user_notes"], base_card["user_notes"])
        self.assertEqual(card["reading_status"], base_card["reading_status"])
        self.assertEqual(card["secondary_tags"], base_card["secondary_tags"])
        self.assertEqual(card["importance"], base_card["importance"])
        # The location survives restore with the same node, and its display
        # path carries the branch's new label rather than the stale one.
        self.assertEqual(
            card["primary_tree_location"]["node_id"],
            base_card["primary_tree_location"]["node_id"],
        )
        self.assertEqual(
            card["primary_tree_location"]["path"],
            [
                "Renamed Branch" if item == "Main Branch" else item
                for item in base_card["primary_tree_location"]["path"]
            ],
        )
        self.assertEqual(proposed["discarded_candidates"], base["discarded_candidates"])
        operations, _diff, _warnings = derive_operations_and_diff_summary(
            workspace=base,
            proposed_workspace=proposed,
        )
        self.assertEqual(
            [operation["operation_type"] for operation in operations],
            ["rename_branch"],
        )

    def test_restore_drops_discarded_entry_for_promoted_paper(self) -> None:
        base = _workspace()
        proposed = copy.deepcopy(base)
        proposed["paper_cards"]["p3"] = _paper_card("p3", "Narrow Application", "Main Branch")

        restore_derived_paper_card_fields(proposed, base)

        self.assertEqual(proposed["discarded_candidates"], [])

    def test_workspace_rename_yields_rename_workspace(self) -> None:
        before = _workspace()
        after = _workspace()
        after["title"] = "RAG: A Field Guide"

        operations, _diff_summary, _warnings = derive_operations_and_diff_summary(
            workspace=before,
            proposed_workspace=after,
        )

        [operation] = operations
        self.assertEqual(operation["operation_type"], "rename_workspace")
        self.assertEqual(operation["before"], {"title": "Retrieval-Augmented Generation"})
        self.assertEqual(operation["after"], {"title": "RAG: A Field Guide"})


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


def _modify_llm(
    instruction: str = "Rename the branch.",
    **arguments: object,
) -> DeterministicWorkspaceAgentLlmClient:
    return DeterministicWorkspaceAgentLlmClient(
        tool_turns=[
            _tool_turn(
                "propose_workspace_edit",
                {
                    "instruction": instruction,
                    "edit_kind": "structural",
                    "message_to_user": "I drafted this change for your review.",
                    **arguments,
                },
            )
        ]
    )


def _retrieval_llm() -> DeterministicWorkspaceAgentLlmClient:
    return DeterministicWorkspaceAgentLlmClient(
        tool_turns=[
            _tool_turn(
                "propose_pipeline_rerun",
                {
                    "stage": "candidates",
                    "reason": "Need more candidates.",
                    "message_to_user": "I want the pipeline to gather fresh candidates.",
                    "topic": "retrieval augmented generation",
                    "max_candidates": 60,
                },
            )
        ]
    )


class _FakeSemanticScholarClient:
    """Answers get_paper_details with a canned provider payload, no network."""

    def __init__(self, **_kwargs: object) -> None:
        pass

    def get_paper_details(self, paper_ids: list[str], _fields: object) -> dict[str, dict[str, object]]:
        return {
            paper_id: {
                "paperId": paper_id,
                "title": "New Paper",
                "abstract": "A recent method the workspace is missing.",
                "year": 2026,
                "citationCount": 3,
            }
            for paper_id in paper_ids
        }


def _workspace_with_added_paper() -> dict[str, object]:
    workspace = _workspace()
    workspace["paper_cards"]["s2new"] = _paper_card("s2new", "New Paper", "Main Branch")
    workspace["tree"]["nodes"][0]["primary_paper_ids"].append("s2new")
    workspace["paper_paths"][0]["paper_ids"].append("s2new")
    workspace["reading_order"].append(
        {"order": 3, "paper_id": "s2new", "reason": "Recent addition."}
    )
    return workspace


def _seed_repository_current(repository) -> str:
    return repository.save_workspace_version(
        "rag__2026-07-05__test",
        _workspace(),
        actor="system",
        parent_version_hash=None,
        reason="seed fixture",
    )


class WorkspaceEditDeltaTest(unittest.TestCase):
    """The agent-edit delta contract: merge only what the model names."""

    def _two_branch_workspace(self) -> dict[str, object]:
        workspace = _workspace()
        workspace["tree"]["nodes"].append(
            {
                **_branch_node("branch-b", "root", "Second Branch"),
                "primary_paper_ids": [],
            }
        )
        return workspace

    def test_empty_delta_changes_nothing(self) -> None:
        base = _workspace()

        merged = apply_workspace_edit_delta(base, {})

        self.assertEqual(merged, base)

    def test_rename_branch_delta_touches_only_the_label(self) -> None:
        base = _workspace()

        merged = apply_workspace_edit_delta(
            base,
            {"upsert_tree_nodes": [{"node_id": "branch-main", "label": "Renamed"}]},
        )

        [node] = merged["tree"]["nodes"]
        self.assertEqual(node["label"], "Renamed")
        self.assertEqual(node["primary_paper_ids"], ["p1", "p2"])
        self.assertEqual(node["description"], "Fixture branch.")
        self.assertEqual(merged["paper_cards"], base["paper_cards"])
        self.assertEqual(merged["paper_paths"], base["paper_paths"])

    def test_membership_move_syncs_other_branches_and_card(self) -> None:
        base = self._two_branch_workspace()

        merged = apply_workspace_edit_delta(
            base,
            {
                "upsert_tree_nodes": [
                    {"node_id": "branch-b", "primary_paper_ids": ["p2"]}
                ]
            },
        )

        nodes = {node["node_id"]: node for node in merged["tree"]["nodes"]}
        self.assertEqual(nodes["branch-main"]["primary_paper_ids"], ["p1"])
        self.assertEqual(nodes["branch-b"]["primary_paper_ids"], ["p2"])
        self.assertEqual(
            merged["paper_cards"]["p2"]["primary_tree_location"]["node_id"],
            "branch-b",
        )

    def test_card_location_move_syncs_membership(self) -> None:
        base = self._two_branch_workspace()

        merged = apply_workspace_edit_delta(
            base,
            {
                "upsert_paper_cards": {
                    "p2": {"primary_tree_location": {"node_id": "branch-b"}}
                }
            },
        )

        nodes = {node["node_id"]: node for node in merged["tree"]["nodes"]}
        self.assertEqual(nodes["branch-main"]["primary_paper_ids"], ["p1"])
        self.assertEqual(nodes["branch-b"]["primary_paper_ids"], ["p2"])

    def test_remove_paper_delta_prunes_every_reference(self) -> None:
        base = _workspace()

        merged = apply_workspace_edit_delta(base, {"remove_paper_ids": ["p2"]})

        self.assertNotIn("p2", merged["paper_cards"])
        [node] = merged["tree"]["nodes"]
        self.assertEqual(node["primary_paper_ids"], ["p1"])
        [path] = merged["paper_paths"]
        self.assertEqual(path["paper_ids"], ["p1"])
        self.assertEqual(
            [entry["paper_id"] for entry in merged["reading_order"]],
            ["p1"],
        )

    def test_remove_branch_moves_its_papers_to_the_parent(self) -> None:
        base = self._two_branch_workspace()
        nodes = {node["node_id"]: node for node in base["tree"]["nodes"]}
        nodes["branch-main"]["parent_id"] = "branch-b"
        nodes["branch-b"]["child_node_ids"] = ["branch-main"]
        nodes["branch-b"]["is_leaf"] = False

        merged = apply_workspace_edit_delta(
            base,
            {"remove_tree_node_ids": ["branch-main"]},
        )

        [node] = merged["tree"]["nodes"]
        self.assertEqual(node["node_id"], "branch-b")
        self.assertEqual(node["primary_paper_ids"], ["p1", "p2"])
        self.assertTrue(node["is_leaf"])
        self.assertEqual(merged["paper_paths"], [])
        self.assertEqual(
            merged["paper_cards"]["p1"]["primary_tree_location"]["node_id"],
            "branch-b",
        )

    def test_delta_through_construct_workspace_yields_single_rename_op(self) -> None:
        seed = _workspace()
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            base = construct_workspace(
                candidate_artifact=_candidate_artifact(),
                base_workspace=seed,
                construction_mode="workspace_repair",
                agent_instruction="Stabilize the fixture.",
                run_metadata={"proposed_workspace": copy.deepcopy(seed)},
                model="test",
            )

        proposed = construct_workspace(
            candidate_artifact=_candidate_artifact(),
            base_workspace=base,
            construction_mode="agent_modify_workspace",
            agent_instruction="Rename the main branch.",
            raw_llm_output={
                "upsert_tree_nodes": [
                    {"node_id": "branch-main", "label": "Renamed Branch"}
                ]
            },
            model="test",
        )

        operations, _diff, _warnings = derive_operations_and_diff_summary(
            workspace=base,
            proposed_workspace=proposed,
        )
        self.assertEqual(
            [operation["operation_type"] for operation in operations],
            ["rename_branch"],
        )
        location = proposed["paper_cards"]["p1"]["primary_tree_location"]
        self.assertIn("Renamed Branch", location["path"])

    def test_delta_adds_paper_from_artifact_with_real_metadata(self) -> None:
        base = _workspace()

        proposed = construct_workspace(
            candidate_artifact=_candidate_artifact(),
            base_workspace=base,
            construction_mode="agent_modify_workspace",
            agent_instruction="Add the narrow application paper.",
            raw_llm_output={
                "upsert_tree_nodes": [
                    {"node_id": "branch-main", "primary_paper_ids": ["p1", "p2", "p3"]}
                ],
                "upsert_paper_paths": [
                    {"path_id": "path-main", "paper_ids": ["p1", "p2", "p3"]}
                ],
            },
            model="test",
        )

        card = proposed["paper_cards"]["p3"]
        self.assertEqual(card["title"], "Narrow Application")
        self.assertEqual(card["year"], 2024)
        self.assertEqual(
            [entry["paper_id"] for entry in proposed["reading_order"]],
            ["p1", "p2", "p3"],
        )

    def test_full_document_payload_still_accepted(self) -> None:
        base = _workspace()
        full = copy.deepcopy(base)
        full["title"] = "Renamed Workspace"

        proposed = construct_workspace(
            candidate_artifact=_candidate_artifact(),
            base_workspace=base,
            construction_mode="agent_modify_workspace",
            agent_instruction="Rename the workspace.",
            raw_llm_output=full,
            model="test",
        )

        self.assertEqual(proposed["title"], "Renamed Workspace")


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


@unittest.skipUnless(LANGGRAPH_AVAILABLE, "langgraph is not installed")
class WorkspaceContextCacheRoutingTest(unittest.TestCase):
    def test_a_critique_does_not_turn_the_next_edit_of_the_same_version_into_a_critique(self) -> None:
        """Both turns build the same cached context; only the edge after it
        decides who reads it. Routing inside the cached node was replayed:
        "critique, then act on it" answered the edit with the critique again."""

        from langgraph.cache.memory import InMemoryCache

        cache = InMemoryCache()
        workspace = _workspace()
        critique_graph = build_workspace_agent_graph(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                tool_turns=[_tool_turn("critique_workspace", {})]
            ),
            cache=cache,
        )
        constructed: list[str] = []

        def constructor(**_kwargs: object) -> dict[str, object]:
            constructed.append("called")
            return _workspace(branch_label="Renamed Branch")

        edit_graph = build_workspace_agent_graph(
            llm_client=_modify_llm(),
            workspace_constructor=constructor,
            cache=cache,
        )

        critique = run_workspace_agent(
            {
                "workspace": workspace,
                "candidate_artifact": _candidate_artifact(),
                "user_message": "Critique this workspace.",
            },
            graph=critique_graph,
        )
        edit = run_workspace_agent(
            {
                "workspace": workspace,
                "candidate_artifact": _candidate_artifact(),
                "user_message": "Rename the main branch.",
            },
            graph=edit_graph,
        )

        critique_nodes = [item["node"] for item in critique.final_output["node_trace"]]
        edit_nodes = [item["node"] for item in edit.final_output["node_trace"]]
        self.assertIn("critique_workspace", critique_nodes)
        self.assertIn("construct_workspace_modification", edit_nodes)
        self.assertNotIn("critique_workspace", edit_nodes)
        self.assertEqual(constructed, ["called"])
        self.assertTrue(edit.final_output["approval_required"])
