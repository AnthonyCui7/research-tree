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
    from research_tree.agents.workspace.llm import DeterministicWorkspaceAgentLlmClient
    from research_tree.agents.workspace.models import AgentIntent, AgentNextAction
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

    def test_classify_intent_routes_chat_modification_and_retrieval(self) -> None:
        for intent, expected in [
            (_intent("chat"), False),
            (_intent("modify_workspace", modifies=True), True),
            (_intent("retrieve_more_papers", more=True), False),
        ]:
            nodes = WorkspaceAgentNodes(
                llm_client=DeterministicWorkspaceAgentLlmClient(
                    structured_outputs=[intent]
                )
            )
            command = nodes.classify_intent(
                {
                    "user_message": "test",
                    "workspace_summary": {},
                }
            )

            self.assertEqual(command.goto, "build_workspace_context")
            self.assertEqual(
                command.update["intent"]["requires_workspace_modification"],
                expected,
            )

    def test_plan_next_action_routes_to_structured_action(self) -> None:
        action = AgentNextAction(
            action_type="construct_workspace_modification",
            reason="edit requested",
            modification_instruction="rename branch",
        )
        nodes = WorkspaceAgentNodes(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                structured_outputs=[action]
            )
        )

        command = nodes.plan_next_action(
            {
                "user_message": "rename branch",
                "intent": _intent("modify_workspace", modifies=True).model_dump(),
                "chat_context": {},
                "action_history": [],
            }
        )

        self.assertEqual(command.goto, "construct_workspace_modification")
        self.assertEqual(command.update["next_action"]["action_type"], action.action_type)

    def test_similar_paper_requests_do_not_route_to_retrieval_guardrail(self) -> None:
        nodes = WorkspaceAgentNodes(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                structured_outputs=[
                    AgentNextAction(
                        action_type="prepare_retrieval_rerun",
                        reason="model chose retrieval",
                        retrieval_request={"topic": "prompting"},
                    )
                ]
            )
        )

        command = nodes.plan_next_action(
            {
                "user_message": "Refresh similar papers for Chain of Thought.",
                "intent": _intent("modify_workspace", modifies=True).model_dump(),
                "chat_context": {},
                "action_history": [],
            }
        )

        self.assertEqual(command.goto, "construct_workspace_modification")
        self.assertEqual(
            command.update["action_history"][0]["action_type"],
            "construct_workspace_modification",
        )

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
        chunks = list(stream)

        self.assertEqual(calls[0]["construction_mode"], "agent_modify_workspace")
        self.assertTrue(any("__interrupt__" in chunk for chunk in chunks))

    def test_remove_visible_paper_uses_deterministic_patch(self) -> None:
        calls: list[dict[str, object]] = []
        graph = build_workspace_agent_graph(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                structured_outputs=[
                    _intent("modify_workspace", modifies=True),
                    AgentNextAction(
                        action_type="construct_workspace_modification",
                        reason="remove paper",
                        modification_instruction="Remove evaluation benchmark paper.",
                    ),
                ]
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
        class ForbiddenRerunLlm:
            def complete_structured(self, *, prompt, response_model, model_name=None):
                if response_model is AgentIntent:
                    return _intent("retrieve_more_papers", more=True)
                if response_model is AgentNextAction:
                    return AgentNextAction(
                        action_type="prepare_retrieval_rerun",
                        reason="forbidden rerun",
                        retrieval_request={
                            "topic": "retrieval augmented generation",
                            "max_candidates": 10,
                            "alpha": 1.25,
                            "scoring_algorithm": "replace scoring formula",
                            "reason": "test rejection",
                        },
                    )
                raise AssertionError(response_model)

            def complete_text(self, *, prompt, model_name=None):
                return "unused"

        calls: list[object] = []
        graph = build_workspace_agent_graph(
            llm_client=ForbiddenRerunLlm(),
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

    def test_human_review_interrupt_emits_json_serializable_payload(self) -> None:
        graph = build_workspace_agent_graph(
            llm_client=_modify_llm(),
            workspace_constructor=lambda **_kwargs: _workspace(branch_label="Renamed Branch"),
        )

        chunks = list(
            graph.stream(
                {
                    "workspace": _workspace(),
                    "candidate_artifact": _candidate_artifact(),
                    "user_message": "Rename the branch.",
                },
                {"configurable": {"thread_id": "review-payload"}},
            )
        )
        interrupt_chunk = next(chunk for chunk in chunks if "__interrupt__" in chunk)
        payload = interrupt_chunk["__interrupt__"][0].value

        json.dumps(payload)
        self.assertEqual(payload["type"], "workspace_patch_review")
        self.assertEqual(payload["choices"], ["approve", "edit", "reject"])

    def test_pending_review_is_persisted_before_interrupt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            base_hash = _seed_repository_current(repository)
            graph = build_workspace_agent_graph(
                llm_client=_modify_llm(),
                workspace_constructor=lambda **_kwargs: _workspace(branch_label="Renamed Branch"),
                workspace_repository=repository,
            )

            chunks = list(
                graph.stream(
                    {
                        "workspace": _workspace(),
                        "candidate_artifact": _candidate_artifact(),
                        "user_message": "Rename the branch.",
                    },
                    {"configurable": {"thread_id": "pending-review"}},
                )
            )
            interrupt_chunk = next(chunk for chunk in chunks if "__interrupt__" in chunk)
            payload = interrupt_chunk["__interrupt__"][0].value
            review = repository.get_pending_review(
                "rag__2026-07-05__test",
                payload["review_id"],
            )
            run_events = repository.list_agent_run_events("rag__2026-07-05__test")

        self.assertEqual(review["status"], "pending")
        self.assertEqual(review["base_workspace_version_hash"], base_hash)
        self.assertEqual(review["interrupt_payload"]["review_id"], payload["review_id"])
        self.assertEqual(
            review["proposed_workspace"]["tree"]["nodes"][0]["label"],
            "Renamed Branch",
        )
        self.assertEqual(run_events[0]["status"], "pending_review")

    def test_resume_approval_applies_patch_and_rejection_does_not(self) -> None:
        approve_graph = build_workspace_agent_graph(
            llm_client=_modify_llm(),
            workspace_constructor=lambda **_kwargs: _workspace(branch_label="Renamed Branch"),
        )
        approve_config = {"configurable": {"thread_id": "approve"}}
        list(
            approve_graph.stream(
                {
                    "workspace": _workspace(),
                    "candidate_artifact": _candidate_artifact(),
                    "user_message": "Rename the branch.",
                },
                approve_config,
            )
        )
        approved = approve_graph.invoke(Command(resume={"choice": "approve"}), approve_config)

        reject_graph = build_workspace_agent_graph(
            llm_client=_modify_llm(),
            workspace_constructor=lambda **_kwargs: _workspace(branch_label="Rejected Branch"),
        )
        reject_config = {"configurable": {"thread_id": "reject"}}
        list(
            reject_graph.stream(
                {
                    "workspace": _workspace(),
                    "candidate_artifact": _candidate_artifact(),
                    "user_message": "Rename the branch.",
                },
                reject_config,
            )
        )
        rejected = reject_graph.invoke(Command(resume={"choice": "reject"}), reject_config)

        self.assertEqual(approved["updated_workspace"]["tree"]["nodes"][0]["label"], "Renamed Branch")
        self.assertEqual(rejected["status"], "rejected")
        self.assertNotIn("updated_workspace", rejected)

    def test_resume_approval_persists_version_event_and_current_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            _seed_repository_current(repository)
            graph = build_workspace_agent_graph(
                llm_client=_modify_llm(),
                workspace_constructor=lambda **_kwargs: _workspace(branch_label="Renamed Branch"),
                workspace_repository=repository,
            )
            config = {"configurable": {"thread_id": "approve-persist"}}
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
            approved = graph.invoke(Command(resume={"choice": "approve"}), config)
            current = repository.get_current_workspace("rag__2026-07-05__test")
            events = repository.list_workspace_events("rag__2026-07-05__test")
            run_events = repository.list_agent_run_events("rag__2026-07-05__test")
            versions = repository.list_workspace_versions("rag__2026-07-05__test")
            reviews = repository.list_workspace_reviews("rag__2026-07-05__test")

        self.assertEqual(approved["status"], "completed")
        self.assertEqual(current["tree"]["nodes"][0]["label"], "Renamed Branch")
        self.assertIn(
            approved["persisted_version_hash"],
            [version["version_hash"] for version in versions],
        )
        self.assertEqual(events[0]["event_type"], "workspace_patch_approved_applied")
        self.assertEqual(events[0]["after_hash"], approved["persisted_version_hash"])
        self.assertEqual([event["status"] for event in run_events], ["pending_review", "approved_applied"])
        self.assertEqual(reviews[0]["status"], "approved_applied")
        self.assertEqual(
            reviews[0]["applied_workspace_version_hash"],
            approved["persisted_version_hash"],
        )

    def test_resume_rejection_persists_event_without_current_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            _seed_repository_current(repository)
            graph = build_workspace_agent_graph(
                llm_client=_modify_llm(),
                workspace_constructor=lambda **_kwargs: _workspace(branch_label="Rejected Branch"),
                workspace_repository=repository,
            )
            config = {"configurable": {"thread_id": "reject-persist"}}
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
            rejected = graph.invoke(Command(resume={"choice": "reject"}), config)
            events = repository.list_workspace_events("rag__2026-07-05__test")
            run_events = repository.list_agent_run_events("rag__2026-07-05__test")
            current = repository.get_current_workspace("rag__2026-07-05__test")
            reviews = repository.list_workspace_reviews("rag__2026-07-05__test")

        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(current["tree"]["nodes"][0]["label"], "Main Branch")
        self.assertEqual(events[0]["event_type"], "workspace_patch_rejected")
        self.assertIsNone(events[0]["after_hash"])
        self.assertEqual([event["status"] for event in run_events], ["pending_review", "rejected"])
        self.assertEqual(reviews[0]["status"], "rejected")

    def test_resume_approval_rejects_stale_pending_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            base_hash = _seed_repository_current(repository)
            graph = build_workspace_agent_graph(
                llm_client=_modify_llm(),
                workspace_constructor=lambda **_kwargs: _workspace(branch_label="Agent Proposal"),
                workspace_repository=repository,
            )
            config = {"configurable": {"thread_id": "stale-approval"}}
            chunks = list(
                graph.stream(
                    {
                        "workspace": _workspace(),
                        "candidate_artifact": _candidate_artifact(),
                        "user_message": "Rename the branch.",
                    },
                    config,
                )
            )
            interrupt_chunk = next(chunk for chunk in chunks if "__interrupt__" in chunk)
            review_id = interrupt_chunk["__interrupt__"][0].value["review_id"]
            repository.save_workspace_version(
                "rag__2026-07-05__test",
                _workspace(branch_label="User Changed Branch"),
                actor="user",
                parent_version_hash=base_hash,
                reason="manual edit before approval",
            )

            approved = graph.invoke(Command(resume={"choice": "approve"}), config)
            current = repository.get_current_workspace("rag__2026-07-05__test")
            review = repository.get_review("rag__2026-07-05__test", review_id)
            events = repository.list_workspace_events("rag__2026-07-05__test")

        self.assertEqual(approved["status"], "failed")
        self.assertNotIn("updated_workspace", approved)
        self.assertEqual(current["tree"]["nodes"][0]["label"], "User Changed Branch")
        self.assertEqual(review["status"], "failed_stale_base")
        self.assertEqual(review["base_workspace_version_hash"], base_hash)
        self.assertEqual(events[0]["event_type"], "workspace_patch_stale_approval_rejected")

    def test_edited_review_payload_gets_revalidated(self) -> None:
        graph = build_workspace_agent_graph(
            llm_client=_modify_llm(),
            workspace_constructor=lambda **_kwargs: _workspace(branch_label="First Proposal"),
        )
        config = {"configurable": {"thread_id": "edited"}}
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
        chunks = list(
            graph.stream(
                Command(
                    resume={
                        "choice": "edit",
                        "proposed_workspace": _workspace(branch_label="Edited Branch"),
                    }
                ),
                config,
            )
        )
        state = graph.get_state(config).values

        self.assertEqual(state["proposed_workspace"]["tree"]["nodes"][0]["label"], "Edited Branch")
        self.assertTrue(any("__interrupt__" in chunk for chunk in chunks))

    def test_edited_review_payload_persists_edit_event_before_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            _seed_repository_current(repository)
            graph = build_workspace_agent_graph(
                llm_client=_modify_llm(),
                workspace_constructor=lambda **_kwargs: _workspace(branch_label="First Proposal"),
                workspace_repository=repository,
            )
            config = {"configurable": {"thread_id": "edited-persist"}}
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
            first_review_id = graph.get_state(config).values["review_id"]
            list(
                graph.stream(
                    Command(
                        resume={
                            "choice": "edit",
                            "proposed_workspace": _workspace(branch_label="Edited Branch"),
                        }
                    ),
                    config,
                )
            )
            events = repository.list_workspace_events("rag__2026-07-05__test")
            run_events = repository.list_agent_run_events("rag__2026-07-05__test")
            reviews = {
                review["review_id"]: review
                for review in repository.list_workspace_reviews("rag__2026-07-05__test")
            }
            state = graph.get_state(config).values

        self.assertEqual(events[0]["event_type"], "workspace_patch_edited")
        self.assertEqual([event["status"] for event in run_events], ["pending_review", "edited", "pending_review"])
        self.assertEqual(reviews[first_review_id]["status"], "edited")
        self.assertEqual(reviews[state["review_id"]]["status"], "pending")

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


def _chat_llm(answer: str = "answer") -> DeterministicWorkspaceAgentLlmClient:
    return DeterministicWorkspaceAgentLlmClient(
        structured_outputs=[
            _intent("chat"),
            AgentNextAction(action_type="answer_chat", reason="chat"),
        ],
        text_outputs=[answer],
    )


def _modify_llm() -> DeterministicWorkspaceAgentLlmClient:
    return DeterministicWorkspaceAgentLlmClient(
        structured_outputs=[
            _intent("modify_workspace", modifies=True),
            AgentNextAction(
                action_type="construct_workspace_modification",
                reason="edit",
                modification_instruction="Rename the branch.",
            ),
        ]
    )


def _retrieval_llm() -> DeterministicWorkspaceAgentLlmClient:
    return DeterministicWorkspaceAgentLlmClient(
        structured_outputs=[
            _intent("retrieve_more_papers", more=True),
            AgentNextAction(
                action_type="prepare_retrieval_rerun",
                reason="retrieve",
                retrieval_request={
                    "topic": "retrieval augmented generation",
                    "max_candidates": 60,
                    "alpha": 2.0,
                    "reason": "Need more candidates.",
                },
            ),
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


def _intent(
    intent_type: str,
    *,
    modifies: bool = False,
    more: bool = False,
) -> AgentIntent:
    return AgentIntent(
        intent_type=intent_type,
        confidence=0.9,
        requires_workspace_modification=modifies,
        requires_more_papers=more,
        reason="test",
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
