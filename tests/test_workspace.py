from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import copy
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_tree.cli.construct_workspace import main as construct_main
from research_tree.cli.enrich_workspace_similar_papers import main as similar_main
from research_tree.agents.workspace.prompts import _workspace_delta_rules
from research_tree.agents.workspace.nodes import _similar_paper_context_item
from research_tree.workspace.schemas import (
    CandidatePaperMetadata,
    candidate_papers_from_artifact,
)
from research_tree.workspace.repository import LocalJsonWorkspaceRepository
from research_tree.workspace.diff import derive_operations_and_diff_summary
from research_tree.services.reviews import validate_workspace_proposal
from research_tree.workspace.operations import (
    WorkspacePatchError,
    apply_structured_workspace_patch,
    remove_visible_paper_operation,
)
from research_tree.workspace.construction import (
    OpenAIResponsesWorkspaceClient,
    construct_workspace,
    enrich_workspace_papers_from_semantic_scholar,
    ensure_survey_anchor_cards,
    materialize_workspace_candidate_references,
    normalize_workspace_payload,
)
from research_tree.retrieval.full_text import PaperContentResult
from research_tree.workspace.enrichment import hydrate_workspace_papers
from research_tree.workspace.publishing import publish_workspace_version
from research_tree.workspace.prompts import (
    build_workspace_prompt,
    workspace_description_rules,
    workspace_importance_rules,
)
from research_tree.workspace.serialization import load_candidate_artifact
from research_tree.workspace.similar_papers import (
    ScoredSimilarPaperCandidate,
    build_similar_papers,
    precompute_similar_paper_rankings,
)
from research_tree.workspace.validation import validate_workspace


class WorkspaceBackendTest(unittest.TestCase):
    def test_structured_remove_preserves_removed_placement_state(self) -> None:
        workspace = _workspace()

        proposed = apply_structured_workspace_patch(
            base_workspace=workspace,
            operations=[remove_visible_paper_operation(paper_id="p1")],
        )

        self.assertNotIn("p1", proposed["paper_cards"])
        self.assertEqual(proposed["tree"]["nodes"][0]["primary_paper_ids"], ["p2"])
        self.assertEqual(proposed["paper_paths"][0]["paper_ids"], ["p2"])
        self.assertEqual(
            proposed["reading_order"],
            [{"order": 1, "paper_id": "p2", "reason": "Then evaluate."}],
        )
        [placement] = proposed["removed_paper_placements"]
        self.assertEqual(placement["paper_id"], "p1")
        self.assertEqual(placement["paper_card"]["title"], "Core Method")
        self.assertEqual(
            placement["branch_placements"],
            [{"branch_id": "branch-main", "field": "primary_paper_ids", "position": 0}],
        )
        self.assertEqual(placement["path_placements"][0]["path_id"], "path-main")
        self.assertEqual(placement["reading_order_entry"]["paper_id"], "p1")
        self.assertTrue(placement["removed_from_workspace_version"])

    def test_structured_patch_failure_is_atomic(self) -> None:
        workspace = _workspace()
        original = copy.deepcopy(workspace)

        with self.assertRaises(WorkspacePatchError):
            apply_structured_workspace_patch(
                base_workspace=workspace,
                operations=[
                    {
                        "op": "set",
                        "entity_type": "branch",
                        "branch_id": "branch-main",
                        "field": "label",
                        "value": "Renamed",
                    },
                    {
                        "op": "remove",
                        "entity_type": "paper_placement",
                        "paper_id": "missing",
                    },
                ],
            )

        self.assertEqual(workspace, original)

    def test_structured_set_rejects_arbitrary_workspace_replacement(self) -> None:
        with self.assertRaises(WorkspacePatchError):
            apply_structured_workspace_patch(
                base_workspace=_workspace(),
                operations=[
                    {
                        "op": "set",
                        "entity_type": "workspace",
                        "field": "paper_cards",
                        "value": {},
                    }
                ],
            )

    def test_insert_refuses_a_paper_card_that_is_not_an_object(self) -> None:
        """A card that arrived as a string reached `card.get` and answered 500."""

        for card in ("hello", 7, 1.5, True, ["a"], None):
            with self.assertRaises(WorkspacePatchError):
                apply_structured_workspace_patch(
                    base_workspace=_workspace(),
                    operations=[
                        {
                            "op": "insert",
                            "entity_type": "paper_placement",
                            "paper_id": "new-1",
                            "branch_id": "branch-main",
                            "value": {"paper_card": card},
                        }
                    ],
                )

    def test_insert_removed_paper_reuses_card_without_old_placement(self) -> None:
        removed = apply_structured_workspace_patch(
            base_workspace=_workspace(),
            operations=[remove_visible_paper_operation(paper_id="p1")],
        )

        restored = apply_structured_workspace_patch(
            base_workspace=removed,
            operations=[
                {
                    "op": "insert",
                    "entity_type": "paper_placement",
                    "paper_id": "p1",
                    "branch_id": "branch-main",
                    "index": 1,
                }
            ],
        )

        self.assertEqual(restored["paper_cards"]["p1"]["title"], "Core Method")
        self.assertEqual(
            restored["tree"]["nodes"][0]["primary_paper_ids"],
            ["p2", "p1"],
        )

    def test_move_paper_between_branches_keeps_it_readable(self) -> None:
        workspace = _workspace_with_side_branch()

        proposed = apply_structured_workspace_patch(
            base_workspace=workspace,
            operations=[
                {
                    "op": "move",
                    "entity_type": "paper_placement",
                    "paper_id": "p1",
                    "to_branch_id": "branch-side",
                }
            ],
        )

        main, side = proposed["tree"]["nodes"][0], proposed["tree"]["nodes"][1]
        self.assertEqual(main["primary_paper_ids"], ["p2"])
        self.assertEqual(side["primary_paper_ids"], ["p3", "p1"])
        path_main, path_side = proposed["paper_paths"]
        self.assertEqual(path_main["paper_ids"], ["p2"])
        self.assertEqual([step["paper_id"] for step in path_main["paper_steps"]], ["p2"])
        # The paper lands at the end of the destination's path, with the reason
        # it already had, so it is still drawn and still explained.
        self.assertEqual(path_side["paper_ids"], ["p3", "p1"])
        self.assertEqual(
            path_side["paper_steps"],
            [
                {"paper_id": "p3", "why_read_here": "The side view."},
                {"paper_id": "p1", "why_read_here": "Start with the method."},
            ],
        )
        self.assertEqual(
            proposed["paper_cards"]["p1"]["primary_tree_location"],
            {"node_id": "branch-side", "path": ["Retrieval-Augmented Generation", "Side Branch"]},
        )
        # A move is not a removal: the reading order, the root's references and
        # other cards' references to the paper all survive.
        self.assertEqual(
            [item["paper_id"] for item in proposed["reading_order"]], ["p1", "p2", "p3"]
        )
        self.assertEqual(proposed["root"]["representative_paper_ids"], ["p1"])
        self.assertEqual(proposed["paper_cards"]["p2"]["read_before"], ["p1"])
        self.assertNotIn("removed_paper_placements", proposed)

    def test_move_lands_where_the_publication_date_puts_it(self) -> None:
        workspace = _workspace_with_side_branch()
        # Side path: p3 (2021), p4 (2023). p1 (2022) belongs between them; p2,
        # undated, goes last.
        workspace["paper_cards"]["p4"] = _paper_card("p4", "Later Side Paper")
        workspace["paper_cards"]["p4"]["primary_tree_location"] = {
            "node_id": "branch-side",
            "path": ["Retrieval-Augmented Generation", "Side Branch"],
        }
        workspace["tree"]["nodes"][1]["primary_paper_ids"] = ["p3", "p4"]
        workspace["paper_paths"][1]["paper_ids"] = ["p3", "p4"]
        workspace["paper_paths"][1]["paper_steps"].append(
            {"paper_id": "p4", "why_read_here": "The later view."}
        )
        workspace["paper_cards"]["p3"]["publication_date"] = "2021-03-01"
        workspace["paper_cards"]["p4"]["publication_date"] = "2023-05-01"
        workspace["paper_cards"]["p1"]["publication_date"] = "2022-01-15"
        workspace["paper_cards"]["p2"]["year"] = None

        proposed = apply_structured_workspace_patch(
            base_workspace=workspace,
            operations=[
                {
                    "op": "move",
                    "entity_type": "paper_placement",
                    "paper_id": "p1",
                    "to_branch_id": "branch-side",
                },
                {
                    "op": "move",
                    "entity_type": "paper_placement",
                    "paper_id": "p2",
                    "to_branch_id": "branch-side",
                },
            ],
        )

        side_path = proposed["paper_paths"][-1]
        self.assertEqual(side_path["paper_ids"], ["p3", "p1", "p4", "p2"])
        self.assertEqual(
            [step["paper_id"] for step in side_path["paper_steps"]], ["p3", "p1", "p4", "p2"]
        )
        self.assertEqual(proposed["tree"]["nodes"][1]["primary_paper_ids"], ["p3", "p1", "p4", "p2"])
        # The source path is gone with its papers; nothing else moved.
        self.assertEqual([path["path_id"] for path in proposed["paper_paths"]], ["path-side"])

    def test_moving_a_lone_paper_onto_its_own_path_keeps_the_path(self) -> None:
        workspace = _workspace_with_side_branch()

        proposed = apply_structured_workspace_patch(
            base_workspace=workspace,
            operations=[
                {
                    "op": "move",
                    "entity_type": "paper_placement",
                    "paper_id": "p3",
                    "to_branch_id": "branch-side",
                }
            ],
        )

        self.assertEqual(proposed["paper_paths"][1]["paper_ids"], ["p3"])
        self.assertEqual(
            proposed["paper_paths"][1]["paper_steps"],
            [{"paper_id": "p3", "why_read_here": "The side view."}],
        )

    def test_moving_the_last_paper_off_a_path_drops_the_path(self) -> None:
        workspace = _workspace_with_side_branch()

        proposed = apply_structured_workspace_patch(
            base_workspace=workspace,
            operations=[
                {
                    "op": "move",
                    "entity_type": "paper_placement",
                    "paper_id": "p3",
                    "to_branch_id": "branch-main",
                    "index": 0,
                }
            ],
        )

        self.assertEqual([path["path_id"] for path in proposed["paper_paths"]], ["path-main"])
        self.assertEqual(proposed["paper_paths"][0]["paper_ids"], ["p3", "p1", "p2"])
        self.assertEqual(
            [step["paper_id"] for step in proposed["paper_paths"][0]["paper_steps"]],
            ["p3", "p1", "p2"],
        )
        self.assertEqual(proposed["tree"]["nodes"][1]["primary_paper_ids"], [])

    def test_move_without_a_prior_step_uses_the_cards_own_words(self) -> None:
        workspace = _workspace_with_side_branch()
        del workspace["paper_paths"][0]["paper_steps"]
        workspace["paper_cards"]["p1"]["importance"] = "It set the template."

        proposed = apply_structured_workspace_patch(
            base_workspace=workspace,
            operations=[
                {
                    "op": "move",
                    "entity_type": "paper_placement",
                    "paper_id": "p1",
                    "to_branch_id": "branch-side",
                    "path_id": "path-side",
                }
            ],
        )

        self.assertEqual(
            proposed["paper_paths"][1]["paper_steps"][-1],
            {"paper_id": "p1", "why_read_here": "It set the template."},
        )

    def test_move_refuses_a_grouping_branch_and_a_foreign_path(self) -> None:
        workspace = _workspace_with_side_branch()
        group = _branch_node("branch-group", "root")
        group.update({"label": "Group", "is_leaf": False, "child_node_ids": ["branch-side"]})
        workspace["tree"]["nodes"].append(group)  # type: ignore[union-attr]

        with self.assertRaisesRegex(WorkspacePatchError, "groups other branches"):
            apply_structured_workspace_patch(
                base_workspace=workspace,
                operations=[
                    {
                        "op": "move",
                        "entity_type": "paper_placement",
                        "paper_id": "p1",
                        "to_branch_id": "branch-group",
                    }
                ],
            )
        with self.assertRaisesRegex(WorkspacePatchError, "does not belong to branch"):
            apply_structured_workspace_patch(
                base_workspace=workspace,
                operations=[
                    {
                        "op": "move",
                        "entity_type": "paper_placement",
                        "paper_id": "p1",
                        "to_branch_id": "branch-side",
                        "path_id": "path-main",
                    }
                ],
            )

    def test_structured_reorder_requires_exact_members(self) -> None:
        with self.assertRaises(WorkspacePatchError):
            apply_structured_workspace_patch(
                base_workspace=_workspace(),
                operations=[
                    {
                        "op": "reorder",
                        "entity_type": "branch_papers",
                        "branch_id": "branch-main",
                        "ordered_ids": ["p2"],
                    }
                ],
            )

    def test_structured_patch_accepts_nested_target_schema(self) -> None:
        workspace = _workspace()

        proposed = apply_structured_workspace_patch(
            base_workspace=workspace,
            operations=[
                {
                    "op": "set",
                    "target": {
                        "type": "branch",
                        "id": "branch-main",
                        "field": "label",
                    },
                    "value": "Nested Branch",
                },
                {
                    "op": "move",
                    "target": {
                        "type": "paper_placement",
                        "paper_id": "p1",
                        "from_branch_id": "branch-main",
                        "to_branch_id": "branch-main",
                        "path_id": "path-main",
                    },
                    "index": 1,
                },
                {
                    "op": "reorder",
                    "target": {
                        "type": "paper_path",
                        "path_id": "path-main",
                    },
                    "ordered_ids": ["p2", "p1"],
                },
            ],
        )

        self.assertEqual(proposed["tree"]["nodes"][0]["label"], "Nested Branch")
        self.assertEqual(
            proposed["tree"]["nodes"][0]["primary_paper_ids"],
            ["p2", "p1"],
        )
        self.assertEqual(proposed["paper_paths"][0]["paper_ids"], ["p2", "p1"])

    def test_similar_paper_context_tolerates_candidate_without_tldr(self) -> None:
        paper = CandidatePaperMetadata(paper_id="p2", title="Related Paper")

        item = _similar_paper_context_item(
            {"paper_id": "p2", "rank": 1},
            {"p2": paper},
        )

        self.assertEqual(item["paper_id"], "p2")
        self.assertIsNone(item["tldr"])

    def test_workspace_prompt_uses_only_structural_candidate_fields(self) -> None:
        artifact = _candidate_artifact()
        artifact["non_survey_papers"][0]["abstract"] = "word " * 400  # type: ignore[index]
        artifact["non_survey_papers"][0]["tldr"] = "  A   generated summary.  "  # type: ignore[index]

        prompt = build_workspace_prompt(artifact)
        payload = json.loads(
            prompt.split("<candidate_artifact_json>\n", 1)[1].split(
                "\n</candidate_artifact_json>",
                1,
            )[0]
        )
        paper = payload["non_survey_papers"][0]

        self.assertEqual(set(paper), {
            "paper_id",
            "title",
            "tldr",
            "abstract",
            "publication_date",
            "citation_count",
            "authority_rank",
            "in_degree",
            "is_survey",
        })
        # Abstracts are budgeted in words, not characters.
        self.assertEqual(len(paper["abstract"].removesuffix("...").split()), 250)
        self.assertEqual(paper["tldr"], "A generated summary.")
        self.assertIn("llm_handoff", payload)

    def test_workspace_prompt_keeps_a_short_abstract_whole(self) -> None:
        artifact = _candidate_artifact()
        artifact["non_survey_papers"][0]["abstract"] = "Ten words is well under the budget for one abstract."  # type: ignore[index]

        prompt = build_workspace_prompt(artifact)

        self.assertIn("Ten words is well under the budget for one abstract.", prompt)
        self.assertNotIn("budget for one abstract....", prompt)

    def test_workspace_prompt_marks_only_flagged_papers(self) -> None:
        """flagged_off_topic reaches the model when true and costs nothing when false."""

        artifact = _candidate_artifact()
        artifact["non_survey_papers"][0]["flagged_off_topic"] = True  # type: ignore[index]

        prompt = build_workspace_prompt(artifact)
        payload = json.loads(
            prompt.split("<candidate_artifact_json>\n", 1)[1].split(
                "\n</candidate_artifact_json>",
                1,
            )[0]
        )

        self.assertTrue(payload["non_survey_papers"][0]["flagged_off_topic"])
        for paper in payload["non_survey_papers"][1:]:
            self.assertNotIn("flagged_off_topic", paper)
        self.assertIn("flagged_off_topic: true", prompt)

    def test_workspace_prompt_marks_only_frontier_picks(self) -> None:
        """frontier_pick reaches the model when true and costs nothing when false."""

        artifact = _candidate_artifact()
        artifact["non_survey_papers"][0]["frontier_pick"] = True  # type: ignore[index]

        prompt = build_workspace_prompt(artifact)
        payload = json.loads(
            prompt.split("<candidate_artifact_json>\n", 1)[1].split(
                "\n</candidate_artifact_json>",
                1,
            )[0]
        )

        self.assertTrue(payload["non_survey_papers"][0]["frontier_pick"])
        for paper in payload["non_survey_papers"][1:]:
            self.assertNotIn("frontier_pick", paper)
        self.assertIn("frontier_pick: true", prompt)

    def test_workspace_prompt_requires_topic_first_descriptions(self) -> None:
        prompt = build_workspace_prompt(_candidate_artifact())

        for rule in workspace_description_rules():
            self.assertIn(rule, prompt)

        for rule in workspace_importance_rules():
            self.assertIn(rule, prompt)

    def test_reader_instructions_are_quoted_into_the_prompt(self) -> None:
        prompt = build_workspace_prompt(
            _candidate_artifact(),
            instructions="  Emphasize\n benchmarks  ",
        )

        self.assertIn("<reader_instructions>\nEmphasize benchmarks\n</reader_instructions>", prompt)
        self.assertIn("never licenses papers outside the candidate artifact", prompt)

    def test_prompt_without_instructions_carries_no_instructions_section(self) -> None:
        for instructions in (None, "", "   "):
            prompt = build_workspace_prompt(_candidate_artifact(), instructions=instructions)

            self.assertNotIn("reader_instructions", prompt)
            self.assertIn("Output contract", prompt)

    def test_workspace_editorial_rules_reach_agent_revisions(self) -> None:
        mutation_rules = _workspace_delta_rules()

        for rule in [*workspace_description_rules(), *workspace_importance_rules()]:
            self.assertIn(rule, mutation_rules)

    def test_workspace_request_never_sends_temperature(self) -> None:
        """Reasoning models reject `temperature`; sending it fails the call."""

        client = OpenAIResponsesWorkspaceClient(api_key="test-key")
        response = {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]
        }
        with patch("research_tree.llm._post", return_value=response) as post_response:
            client.call_workspace_llm(prompt="test", model="gpt-5.6-luna")

        body = post_response.call_args.args[0]
        self.assertNotIn("temperature", body)
        self.assertFalse(body["store"])
        self.assertEqual(body["prompt_cache_key"], "research-tree-workspace-construction")
        self.assertEqual(body["reasoning"], {"effort": "xhigh"})
        self.assertNotIn("background", body)

    def test_llm_request_failure_is_reported_with_the_call_name(self) -> None:
        from research_tree.llm import LlmRequestError

        client = OpenAIResponsesWorkspaceClient(api_key="test-key")
        error = HTTPError(
            "https://api.openai.com/v1/responses",
            400,
            "Bad request",
            None,
            BytesIO(b'{"error":{"message":"boom"}}'),
        )
        with patch("research_tree.llm._post", side_effect=error):
            with self.assertRaises(LlmRequestError) as raised:
                client.call_workspace_llm(prompt="test", model="gpt-5.6-luna")

        self.assertIn("workspace construction", str(raised.exception))

    def test_reads_llm_candidate_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "llm_candidate_papers.json"
            path.write_text(json.dumps(_candidate_artifact()), encoding="utf-8")

            artifact = load_candidate_artifact(path)
            papers = candidate_papers_from_artifact(artifact)

        self.assertEqual(artifact["schema_version"], "llm_candidate_papers.v1")
        self.assertIn("p1", papers)
        self.assertEqual(papers["p1"].title, "Core Method")

    def test_validates_workspace_json(self) -> None:
        result = validate_workspace(_workspace(), _candidate_artifact())

        self.assertTrue(result.is_valid)
        self.assertEqual(result.errors, [])

    def test_initial_construction_normalizes_model_root_and_schema_version(self) -> None:
        raw_workspace = _workspace()
        raw_workspace["schema_version"] = "research_tree.v1"
        raw_workspace["tree"]["nodes"].insert(0, {"node_id": "root"})  # type: ignore[index]

        workspace = construct_workspace(
            candidate_artifact=_candidate_artifact(),
            raw_llm_output=raw_workspace,
        )

        self.assertEqual(workspace["schema_version"], "research_tree_workspace.v1")
        self.assertNotIn(
            "root",
            [node["node_id"] for node in workspace["tree"]["nodes"]],  # type: ignore[index]
        )

    def test_enriches_visible_papers_with_semantic_scholar_tldrs(self) -> None:
        class FakeSemanticScholar:
            def get_paper_details(
                self,
                paper_ids: list[str],
                warnings: list[str],
            ) -> dict[str, dict[str, object]]:
                self.paper_ids = paper_ids
                return {
                    "p1": {
                        "paperId": "p1",
                        "citationCount": 123,
                        "publicationDate": "2024-03-14",
                        "tldr": {"model": "tldr@v2.0.0", "text": "Source TLDR."},
                    }
                }

        workspace = _workspace()
        semantic_scholar = FakeSemanticScholar()
        enrich_workspace_papers_from_semantic_scholar(workspace, semantic_scholar)  # type: ignore[arg-type]

        self.assertEqual(workspace["paper_cards"]["p1"]["tldr"], "Source TLDR.")
        self.assertEqual(workspace["paper_cards"]["p1"]["citation_count"], 123)
        self.assertEqual(workspace["paper_cards"]["p1"]["publication_date"], "2024-03-14")

    def test_hydration_generates_missing_semantic_scholar_tldr(self) -> None:
        class FakeSemanticScholar:
            def get_paper_details(
                self,
                paper_ids: list[str],
                warnings: list[str],
            ) -> dict[str, dict[str, object]]:
                return {
                    "p1": {
                        "paperId": "p1",
                        "title": "Core Method",
                        "abstract": "A method abstract.",
                    }
                }

        class FakeRepository:
            def save_paper_content(
                self,
                workspace_id: str,
                paper_id: str,
                content: dict[str, object],
            ) -> str:
                return f"{workspace_id}:{paper_id}:content"

        class FakeTldrGenerator:
            def generate_tldr(self, paper: dict[str, object]) -> str:
                return "Core method framing establishes a reusable baseline for comparing retrieval and generation design choices."

        workspace, warnings = hydrate_workspace_papers(
            workspace=_workspace(),
            repository=FakeRepository(),  # type: ignore[arg-type]
            semantic_scholar=FakeSemanticScholar(),  # type: ignore[arg-type]
            tldr_generator=FakeTldrGenerator(),  # type: ignore[arg-type]
        )

        card = workspace["paper_cards"]["p1"]
        self.assertEqual(
            card["tldr"],
            "Core method framing establishes a reusable baseline for comparing retrieval and generation design choices.",
        )
        self.assertEqual(card["tldr_source"], "generated_s2_style")
        self.assertEqual(card["tldr_model"], "gpt-5.6-luna")
        self.assertFalse(any("Generated TLDR failed" in warning for warning in warnings))

    def test_hydration_uses_prefetched_content_without_downloading(self) -> None:
        class FakeSemanticScholar:
            def get_paper_details(
                self,
                paper_ids: list[str],
                warnings: list[str],
            ) -> dict[str, dict[str, object]]:
                return {}

        class FakeRepository:
            def save_paper_content(
                self,
                workspace_id: str,
                paper_id: str,
                content: dict[str, object],
            ) -> str:
                return f"{workspace_id}:{paper_id}:content"

        downloaded: list[str] = []

        def fake_retrieve(*, paper_id: str, title: str, source_url, timeout_seconds=30.0):
            downloaded.append(paper_id)
            return PaperContentResult(
                content={"status": "unavailable", "paper_id": paper_id}
            )

        prefetched = {
            "p1": PaperContentResult(
                content={
                    "status": "available",
                    "source_type": "open_access_pdf",
                    "source_url": "https://arxiv.org/pdf/1234.5678",
                    "page_count": 3,
                    "figure_count": 0,
                    "sha256": "abc",
                    "truncated": False,
                    "full_text": "Prefetched text.",
                }
            )
        }
        with patch(
            "research_tree.workspace.enrichment.retrieve_open_access_paper_content",
            fake_retrieve,
        ):
            workspace, _warnings = hydrate_workspace_papers(
                workspace=_workspace(),
                repository=FakeRepository(),  # type: ignore[arg-type]
                semantic_scholar=FakeSemanticScholar(),  # type: ignore[arg-type]
                prefetched_content=prefetched,
            )

        self.assertNotIn("p1", downloaded)
        self.assertIn("p2", downloaded)
        self.assertEqual(
            workspace["paper_cards"]["p1"]["paper_content"]["status"], "available"
        )

    def test_precomputed_rankings_skip_models_and_exclude_workspace_papers(self) -> None:
        paper_database = [
            CandidatePaperMetadata(paper_id="p1", title="Core Method", abstract="A"),
            CandidatePaperMetadata(paper_id="p2", title="Benchmark", abstract="B"),
            CandidatePaperMetadata(paper_id="p3", title="Follow Up", abstract="C", citation_count=40),
            CandidatePaperMetadata(paper_id="p4", title="Related Method", abstract="D", citation_count=60),
        ]
        by_id = {paper.paper_id: paper for paper in paper_database}
        ranking = [
            ScoredSimilarPaperCandidate(paper=by_id["p2"], similarity_score=0.9),
            ScoredSimilarPaperCandidate(paper=by_id["p4"], similarity_score=0.8),
            ScoredSimilarPaperCandidate(paper=by_id["p3"], similarity_score=0.7),
        ]

        # No retriever or reranker is supplied: covered cards must not load models.
        enriched, _debug = build_similar_papers(
            workspace=_workspace(),
            paper_database=paper_database,
            k=2,
            citation_score_floor=0,
            precomputed_rankings={"p1": ranking, "p2": ranking},
        )

        for paper_id in ("p1", "p2"):
            similar_ids = [
                paper["paper_id"]
                for paper in enriched["paper_cards"][paper_id]["similar_papers"]
            ]
            self.assertEqual(similar_ids, ["p4", "p3"])

    def test_precompute_rankings_excludes_the_query_paper_itself(self) -> None:
        papers = [
            CandidatePaperMetadata(paper_id="p1", title="Core Method", abstract="A", citation_count=50),
            CandidatePaperMetadata(paper_id="p3", title="Follow Up", abstract="C", citation_count=40),
            CandidatePaperMetadata(paper_id="p4", title="Related Method", abstract="D", citation_count=60),
        ]

        class FakeRetriever:
            def rank(self, query, papers, top_n):
                return [
                    ScoredSimilarPaperCandidate(paper=paper, similarity_score=1.0)
                    for paper in papers[:top_n]
                ]

        class FakeReranker:
            def rerank(self, query, candidates):
                return list(reversed(candidates))

        rankings = precompute_similar_paper_rankings(
            query_papers=papers,
            paper_database=papers,
            citation_score_floor=0,
            retriever=FakeRetriever(),
            reranker=FakeReranker(),
        )

        self.assertEqual(set(rankings), {"p1", "p3", "p4"})
        self.assertEqual(
            [candidate.paper.paper_id for candidate in rankings["p1"]],
            ["p4", "p3"],
        )

    def test_rejects_invalid_paper_ids(self) -> None:
        workspace = _workspace()
        workspace["paper_cards"]["missing"] = {
            **workspace["paper_cards"]["p1"],
            "paper_id": "missing",
        }

        result = validate_workspace(workspace, _candidate_artifact())

        self.assertFalse(result.is_valid)
        self.assertTrue(
            any("not present in the candidate artifact" in error for error in result.errors)
        )

    def test_detects_tree_cycles(self) -> None:
        workspace = _workspace()
        workspace["tree"]["nodes"] = [
            _branch_node("a", "b"),
            _branch_node("b", "a"),
        ]

        result = validate_workspace(workspace, _candidate_artifact())

        self.assertFalse(result.is_valid)
        self.assertTrue(any("cycle" in error for error in result.errors))

    def test_visible_paper_budget_warnings(self) -> None:
        workspace = _workspace()
        workspace["scope"]["visible_paper_budget"] = {
            "target_min": 1,
            "target_max": 1,
            "hard_max_default": 1,
        }

        result = validate_workspace(workspace, _candidate_artifact())

        self.assertTrue(result.is_valid)
        self.assertTrue(any("above target_max" in warning for warning in result.warnings))
        self.assertTrue(
            any("exceeds hard_max_default" in warning for warning in result.warnings)
        )

    def test_requires_primary_tree_location(self) -> None:
        workspace = _workspace()
        del workspace["paper_cards"]["p1"]["primary_tree_location"]

        result = validate_workspace(workspace, _candidate_artifact())

        self.assertFalse(result.is_valid)
        self.assertTrue(
            any("missing primary_tree_location" in error for error in result.errors)
        )

    def test_normalizes_legacy_paper_steps_to_simple_reading_sequence(self) -> None:
        workspace = _workspace()
        workspace["paper_paths"][0]["paper_steps"] = [
            {
                "paper_id": "p2",
                "step_index": 2,
                "step_label": "Evaluation milestone",
                "milestone_role": "benchmark_or_evaluation",
                "why_read_here": "Evaluate the method after learning it.",
                "read_status_default": "must_read",
            },
            {
                "paper_id": "p1",
                "step_index": 1,
                "step_label": "Method milestone",
                "milestone_role": "foundational_method",
                "why_read_here": "Start with the core method.",
                "read_status_default": "must_read",
            },
        ]
        workspace["paper_paths"][0]["timeline_intent"] = "Legacy intent"
        workspace["paper_paths"][0]["recommended_reading_depth"] = "core"

        normalize_workspace_payload(workspace)

        path = workspace["paper_paths"][0]
        self.assertEqual(path["paper_ids"], ["p1", "p2"])
        self.assertEqual(
            path["paper_steps"],
            [
                {"paper_id": "p1", "why_read_here": "Start with the core method."},
                {
                    "paper_id": "p2",
                    "why_read_here": "Evaluate the method after learning it.",
                },
            ],
        )
        self.assertNotIn("timeline_intent", path)
        self.assertNotIn("recommended_reading_depth", path)

    def test_rebuilds_reading_order_after_removing_unknown_paper_cards(self) -> None:
        workspace = _workspace()
        workspace["paper_cards"]["missing"] = _paper_card("missing", "Missing")
        workspace["reading_order"].append(
            {"order": 3, "paper_id": "missing", "reason": "Stale model output."}
        )

        normalize_workspace_payload(workspace)
        materialize_workspace_candidate_references(workspace, _candidate_artifact())

        self.assertEqual(
            [entry["paper_id"] for entry in workspace["reading_order"]],
            ["p1", "p2"],
        )
        self.assertNotIn("missing", workspace["paper_cards"])

    def test_rejects_survey_paper_in_reading_path(self) -> None:
        workspace = _workspace()
        workspace["paper_cards"]["survey"] = {
            **_paper_card("survey", "RAG Survey"),
            "paper_role": "survey",
            "primary_tree_location": {
                "node_id": "root",
                "path": ["Retrieval-Augmented Generation"],
            },
        }
        workspace["paper_paths"][0]["paper_ids"] = ["survey", "p1"]
        workspace["paper_paths"][0]["paper_steps"] = [
            {"paper_id": "survey", "why_read_here": "Legacy path."},
            {"paper_id": "p1", "why_read_here": "Then read the method."},
        ]

        result = validate_workspace(workspace, _candidate_artifact())

        self.assertFalse(result.is_valid)
        self.assertTrue(any("contains survey paper" in error for error in result.errors))

    def test_materialization_removes_surveys_from_reading_paths(self) -> None:
        workspace = _workspace()
        workspace["paper_cards"]["survey"] = {
            **_paper_card("survey", "RAG Survey"),
            "paper_role": "survey",
        }
        workspace["paper_paths"][0]["paper_ids"] = ["survey", "p1"]
        workspace["paper_paths"][0]["paper_steps"] = [
            {"paper_id": "survey", "why_read_here": "Orientation."},
            {"paper_id": "p1", "why_read_here": "Core method."},
        ]
        workspace["tree"]["nodes"][0]["primary_paper_ids"] = ["survey", "p1"]

        normalize_workspace_payload(workspace)
        materialize_workspace_candidate_references(workspace, _candidate_artifact())
        ensure_survey_anchor_cards(workspace, _candidate_artifact())

        path = workspace["paper_paths"][0]
        self.assertEqual(path["paper_ids"], ["p1"])
        self.assertEqual(path["paper_steps"], [{"paper_id": "p1", "why_read_here": "Core method."}])
        self.assertEqual(workspace["tree"]["nodes"][0]["primary_paper_ids"], ["p1"])
        self.assertTrue(validate_workspace(workspace, _candidate_artifact()).is_valid)

    def test_similar_papers_excludes_workspace_papers_and_preserves_rerank_order(self) -> None:
        workspace = _workspace()
        paper_database = [
            CandidatePaperMetadata(paper_id="p1", title="Core Method", abstract="A"),
            CandidatePaperMetadata(paper_id="p2", title="Benchmark", abstract="B"),
            CandidatePaperMetadata(paper_id="p3", title="Follow Up", abstract="C", citation_count=40),
            CandidatePaperMetadata(paper_id="p4", title="Related Method", abstract="D", citation_count=60),
        ]

        class FakeRetriever:
            def rank(self, query, papers, top_n):
                return [
                    ScoredSimilarPaperCandidate(paper=paper, similarity_score=1.0)
                    for paper in papers[:top_n]
                ]

        class FakeReranker:
            def rerank(self, query, candidates):
                return list(reversed(candidates))

        enriched, _debug = build_similar_papers(
            workspace=workspace,
            paper_database=paper_database,
            k=2,
            citation_score_floor=0,
            retriever=FakeRetriever(),
            reranker=FakeReranker(),
        )

        for paper_id in ("p1", "p2"):
            similar_papers = enriched["paper_cards"][paper_id]["similar_papers"]
            self.assertEqual(
                [paper["paper_id"] for paper in similar_papers],
                ["p4", "p3"],
            )
            self.assertTrue(
                {paper["paper_id"] for paper in similar_papers}.isdisjoint({"p1", "p2"})
            )

    def test_similar_papers_skips_barely_cited_candidates(self) -> None:
        """An uncited paper is not a useful recommendation, however well it embeds."""

        paper_database = [
            CandidatePaperMetadata(paper_id="p1", title="Core Method", abstract="A"),
            CandidatePaperMetadata(paper_id="p2", title="Benchmark", abstract="B"),
            CandidatePaperMetadata(paper_id="p3", title="Barely Cited", abstract="C", citation_count=3),
            CandidatePaperMetadata(paper_id="p4", title="Well Cited", abstract="D", citation_count=60),
        ]

        class FakeRetriever:
            def rank(self, query, papers, top_n):
                return [
                    ScoredSimilarPaperCandidate(paper=paper, similarity_score=1.0)
                    for paper in papers[:top_n]
                ]

        class FakeReranker:
            def rerank(self, query, candidates):
                return list(candidates)

        enriched, debug = build_similar_papers(
            workspace=_workspace(),
            paper_database=paper_database,
            k=2,
            citation_score_floor=0,
            retriever=FakeRetriever(),
            reranker=FakeReranker(),
        )

        similar_ids = {
            paper["paper_id"]
            for paper in enriched["paper_cards"]["p1"]["similar_papers"]
        }
        self.assertEqual(similar_ids, {"p4"})
        self.assertEqual(debug["min_citation_count"], 5)

    def test_construct_script_runs_with_tiny_fixture_without_api_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path = root / "llm_candidate_papers.json"
            raw_workspace_path = root / "raw_workspace.json"
            output_dir = root / "out"
            candidate_path.write_text(
                json.dumps(_candidate_artifact()),
                encoding="utf-8",
            )
            raw_workspace_path.write_text(json.dumps(_workspace()), encoding="utf-8")

            with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
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

            workspace_path = output_dir / "workspace.json"
            validation_path = output_dir / "workspace_validation.json"
            workspace_exists = workspace_path.exists()
            validation_exists = validation_path.exists()

        self.assertEqual(exit_code, 0)
        self.assertTrue(workspace_exists)
        self.assertTrue(validation_exists)

    def test_construct_script_can_publish_repository_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path = root / "llm_candidate_papers.json"
            raw_workspace_path = root / "raw_workspace.json"
            output_dir = root / "out"
            repository_dir = root / "repository"
            candidate_path.write_text(
                json.dumps(_candidate_artifact()),
                encoding="utf-8",
            )
            raw_workspace_path.write_text(json.dumps(_workspace()), encoding="utf-8")

            with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                exit_code = construct_main(
                    [
                        "--candidate-json",
                        str(candidate_path),
                        "--llm-output-json",
                        str(raw_workspace_path),
                        "--output-dir",
                        str(output_dir),
                        "--publish-to-repository",
                        "--repository-dir",
                        str(repository_dir),
                    ]
                )

            repository = LocalJsonWorkspaceRepository(repository_dir)
            current = repository.get_current_workspace("rag__2026-07-05__test")
            versions = repository.list_workspace_versions("rag__2026-07-05__test")
            events = repository.list_workspace_events("rag__2026-07-05__test")

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(versions), 1)
        self.assertTrue(versions[0]["is_current"])
        self.assertEqual(events[0]["event_type"], "workspace_constructed")
        self.assertIn("paper_steps", current["paper_paths"][0])
        self.assertEqual(current["paper_cards"]["survey"]["paper_role"], "survey")
        self.assertEqual(
            current["paper_cards"]["survey"]["primary_tree_location"]["node_id"],
            "root",
        )

    def test_construct_script_defaults_to_latest_candidate_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run1 = root / "run1"
            run3 = root / "run3"
            run1.mkdir()
            run3.mkdir()
            (run1 / "llm_candidate_papers.json").write_text(
                json.dumps(
                    {
                        **_candidate_artifact(),
                        "topic": "old run",
                    }
                ),
                encoding="utf-8",
            )
            (run3 / "llm_candidate_papers.json").write_text(
                json.dumps(_candidate_artifact()),
                encoding="utf-8",
            )
            raw_workspace_path = root / "raw_workspace.json"
            output_dir = root / "out"
            raw_workspace_path.write_text(json.dumps(_workspace()), encoding="utf-8")

            with patch(
                "research_tree.cli.construct_workspace.DEFAULT_CANDIDATE_PREPARATION_DIR",
                root,
            ):
                with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                    exit_code = construct_main(
                        [
                            "--llm-output-json",
                            str(raw_workspace_path),
                            "--output-dir",
                            str(output_dir),
                        ]
                    )

            workspace = json.loads(
                (output_dir / "workspace.json").read_text(encoding="utf-8")
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            workspace["source_candidate_artifact"]["path"],
            str((run3 / "llm_candidate_papers.json").resolve()),
        )

    def test_construct_script_keeps_a_requested_workspace_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path = root / "llm_candidate_papers.json"
            raw_workspace_path = root / "raw_workspace.json"
            output_dir = root / "out"
            candidate_path.write_text(json.dumps(_candidate_artifact()), encoding="utf-8")
            raw_workspace_path.write_text(json.dumps(_workspace()), encoding="utf-8")

            with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                exit_code = construct_main(
                    [
                        "--candidate-json",
                        str(candidate_path),
                        "--llm-output-json",
                        str(raw_workspace_path),
                        "--output-dir",
                        str(output_dir),
                        "--workspace-id",
                        "existing-workspace-id",
                    ]
                )

            workspace = json.loads((output_dir / "workspace.json").read_text())

        self.assertEqual(exit_code, 0)
        self.assertEqual(workspace["workspace_id"], "existing-workspace-id")
        self.assertEqual(
            workspace["source_candidate_artifact"]["input_mode"],
            "existing_candidate_artifact",
        )

    def test_similar_script_writes_outputs_without_model_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace_path = root / "workspace.json"
            database_path = root / "s2_bulk_deduped_paper_database.json"
            workspace_path.write_text(json.dumps(_workspace()), encoding="utf-8")
            database_path.write_text(
                json.dumps({"papers": _candidate_artifact()["non_survey_papers"]}),
                encoding="utf-8",
            )

            with patch(
                "research_tree.cli.enrich_workspace_similar_papers.build_similar_papers_from_files",
                return_value=(_workspace(), {"papers": {}}),
            ):
                exit_code = similar_main(
                    [
                        "--workspace-json",
                        str(workspace_path),
                        "--paper-database-json",
                        str(database_path),
                        "--k",
                        "1",
                    ]
                )

            output_path = root / "workspace_with_similar_papers.json"
            debug_path = root / "similar_papers_debug.json"
            output_exists = output_path.exists()
            debug_exists = debug_path.exists()

        self.assertEqual(exit_code, 0)
        self.assertTrue(output_exists)
        self.assertTrue(debug_exists)

    def test_similar_script_can_publish_repository_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace_path = root / "workspace.json"
            database_path = root / "s2_bulk_deduped_paper_database.json"
            repository_dir = root / "repository"
            workspace_path.write_text(json.dumps(_workspace()), encoding="utf-8")
            database_path.write_text(
                json.dumps({"papers": _candidate_artifact()["non_survey_papers"]}),
                encoding="utf-8",
            )

            with patch(
                "research_tree.cli.enrich_workspace_similar_papers.build_similar_papers_from_files",
                return_value=(_workspace(), {"papers": {}}),
            ):
                exit_code = similar_main(
                    [
                        "--workspace-json",
                        str(workspace_path),
                        "--paper-database-json",
                        str(database_path),
                        "--k",
                        "1",
                        "--publish-to-repository",
                        "--repository-dir",
                        str(repository_dir),
                    ]
                )

            repository = LocalJsonWorkspaceRepository(repository_dir)
            current = repository.get_current_workspace("rag__2026-07-05__test")
            versions = repository.list_workspace_versions("rag__2026-07-05__test")
            events = repository.list_workspace_events("rag__2026-07-05__test")

        self.assertEqual(exit_code, 0)
        self.assertEqual(current["workspace_id"], "rag__2026-07-05__test")
        self.assertEqual(len(versions), 1)
        self.assertTrue(versions[0]["is_current"])
        self.assertEqual(events[0]["event_type"], "workspace_similar_papers_enriched")

    def test_publish_replaces_same_workspace_with_a_versioned_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository_dir = Path(directory) / "repository"
            first = _workspace()
            first_result = publish_workspace_version(
                repository=LocalJsonWorkspaceRepository(repository_dir),
                workspace=first,
                reason="first construction",
                event_type="workspace_constructed",
                event_payload={},
            )
            rebuilt = {**first, "title": "Rebuilt RAG Workspace"}
            rebuilt_result = publish_workspace_version(
                repository=LocalJsonWorkspaceRepository(repository_dir),
                workspace=rebuilt,
                reason="reconstructed from existing candidates",
                event_type="workspace_constructed",
                event_payload={"candidate_artifact": "existing.json"},
                expected_parent_version_hash=first_result["version_hash"],
            )
            repository = LocalJsonWorkspaceRepository(repository_dir)
            current = repository.get_current_workspace("rag__2026-07-05__test")
            versions = repository.list_workspace_versions("rag__2026-07-05__test")

        self.assertTrue(first_result["published"])
        self.assertTrue(rebuilt_result["published"])
        self.assertEqual(rebuilt_result["parent_version_hash"], first_result["version_hash"])
        self.assertEqual(current["title"], "Rebuilt RAG Workspace")
        self.assertEqual(len(versions), 2)

    def test_first_publish_snapshots_a_legacy_current_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository_dir = Path(directory) / "repository"
            workspace_id = "rag__2026-07-05__test"
            legacy = _workspace()
            current_path = repository_dir / workspace_id / "current.json"
            current_path.parent.mkdir(parents=True)
            current_path.write_text(json.dumps(legacy), encoding="utf-8")
            rebuilt = {**legacy, "title": "Rebuilt RAG Workspace"}

            result = publish_workspace_version(
                repository=LocalJsonWorkspaceRepository(repository_dir),
                workspace=rebuilt,
                reason="reconstructed from existing candidates",
                event_type="workspace_constructed",
                event_payload={},
            )
            repository = LocalJsonWorkspaceRepository(repository_dir)
            versions = repository.list_workspace_versions(workspace_id)
            restored = repository.restore_workspace_version(
                workspace_id,
                result["parent_version_hash"],
                actor="user",
                actor_type="user",
                reason="rejected reconstructed workspace",
            )

        self.assertEqual(len(versions), 2)
        self.assertTrue(restored["restored"])


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


class MalformedDocumentDiffTest(unittest.TestCase):
    """The diff runs before anything validates, on documents a model or a browser wrote."""

    def test_it_describes_any_shape_without_raising(self) -> None:
        workspace = _workspace()
        for proposed in (
            {**workspace, "paper_cards": {"p1": None}},
            {**workspace, "paper_cards": "nope"},
            {**workspace, "paper_paths": 5},
            {**workspace, "tree": [1, 2, 3]},
            {**workspace, "tree": {"root_node_id": "root", "nodes": 9}},
            {**workspace, "reading_order": {"a": 1}},
            {**workspace, "root": "root"},
            {},
        ):
            operations, summary, _warnings = derive_operations_and_diff_summary(
                workspace=workspace,
                proposed_workspace=proposed,
            )
            self.assertIsInstance(operations, list)
            self.assertIn("operation_count", summary)



def _workspace() -> dict[str, object]:
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
            "nodes": [_branch_node("branch-main", "root")],
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
            "p1": _paper_card("p1", "Core Method"),
            "p2": _paper_card("p2", "Evaluation Benchmark"),
        },
        "reading_order": [
            {"order": 1, "paper_id": "p1", "reason": "Start here."},
            {"order": 2, "paper_id": "p2", "reason": "Then evaluate."},
        ],
        "comparison_tables": [
            {
                "table_id": "papers-by-branch",
                "title": "Papers by Branch",
                "columns": ["branch", "paper", "role", "why_it_matters"],
                "rows": [],
            }
        ],
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


def _workspace_with_side_branch() -> dict[str, Any]:
    """The fixture plus a second leaf branch, each path carrying its steps."""

    workspace: dict[str, Any] = _workspace()  # type: ignore[assignment]
    side = _branch_node("branch-side", "root")
    side.update({"label": "Side Branch", "primary_paper_ids": ["p3"]})
    workspace["tree"]["nodes"].append(side)
    workspace["paper_cards"]["p3"] = _paper_card("p3", "Side Paper")
    workspace["paper_cards"]["p3"]["primary_tree_location"] = {
        "node_id": "branch-side",
        "path": ["Retrieval-Augmented Generation", "Side Branch"],
    }
    workspace["paper_cards"]["p2"]["read_before"] = ["p1"]
    workspace["paper_paths"][0]["paper_steps"] = [
        {"paper_id": "p1", "why_read_here": "Start with the method."},
        {"paper_id": "p2", "why_read_here": "Then the benchmark."},
    ]
    workspace["paper_paths"].append(
        {
            "path_id": "path-side",
            "branch_node_id": "branch-side",
            "path_type": "primary_timeline",
            "label": "Side path",
            "description": "The other line.",
            "paper_ids": ["p3"],
            "paper_steps": [{"paper_id": "p3", "why_read_here": "The side view."}],
            "rationale": "Fixture.",
        }
    )
    workspace["reading_order"].append(
        {"order": 3, "paper_id": "p3", "reason": "Finally the side view."}
    )
    return workspace


def _branch_node(node_id: str, parent_id: str) -> dict[str, object]:
    return {
        "node_id": node_id,
        "parent_id": parent_id,
        "label": "Main Branch",
        "description": "Fixture branch.",
        "why_it_matters": "It matters.",
        "is_leaf": True,
        "child_node_ids": [],
        "primary_paper_ids": ["p1", "p2"] if node_id == "branch-main" else [],
        "secondary_paper_ids": [],
        "tags": [],
        "open_questions": [],
    }


def _paper_card(paper_id: str, title: str) -> dict[str, object]:
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
            "path": ["Retrieval-Augmented Generation", "Main Branch"],
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


class HandEditConsistencyTest(unittest.TestCase):
    def test_removing_a_branch_removes_its_path_and_the_parent_becomes_a_leaf(self) -> None:
        workspace = _workspace_with_side_branch()
        emptied = apply_structured_workspace_patch(
            base_workspace=workspace,
            operations=[remove_visible_paper_operation(paper_id="p3")],
        )

        removed = apply_structured_workspace_patch(
            base_workspace=emptied,
            operations=[{"op": "remove", "entity_type": "branch", "branch_id": "branch-side"}],
        )

        self.assertEqual([node["node_id"] for node in removed["tree"]["nodes"]], ["branch-main"])
        self.assertEqual([path["path_id"] for path in removed["paper_paths"]], ["path-main"])
        self.assertTrue(removed["tree"]["nodes"][0]["is_leaf"])
        operations, summary, _ = derive_operations_and_diff_summary(
            workspace=emptied, proposed_workspace=removed
        )
        validation = validate_workspace_proposal(
            current_workspace=emptied,
            proposed_workspace=removed,
            proposed_operations=operations,
            diff_summary=summary,
        )
        self.assertTrue(validation["valid"], validation["errors"])

    def test_a_branch_still_carrying_a_survey_anchor_card_cannot_be_removed(self) -> None:
        workspace = _workspace_with_side_branch()
        workspace["tree"]["nodes"][1]["survey_anchor_paper_id"] = "p3"
        workspace["tree"]["nodes"][1]["primary_paper_ids"] = []
        workspace["paper_paths"] = [workspace["paper_paths"][0]]

        with self.assertRaisesRegex(WorkspacePatchError, "still contains papers"):
            apply_structured_workspace_patch(
                base_workspace=workspace,
                operations=[{"op": "remove", "entity_type": "branch", "branch_id": "branch-side"}],
            )

    def test_removing_a_paper_clears_the_branch_anchor_that_named_it(self) -> None:
        workspace = _workspace_with_side_branch()
        workspace["tree"]["nodes"][1]["survey_anchor_paper_id"] = "p3"

        proposed = apply_structured_workspace_patch(
            base_workspace=workspace,
            operations=[remove_visible_paper_operation(paper_id="p3")],
        )

        self.assertIsNone(proposed["tree"]["nodes"][1]["survey_anchor_paper_id"])

    def test_moving_a_paper_to_a_branch_without_a_path_gives_it_one(self) -> None:
        workspace = _workspace_with_side_branch()
        workspace["paper_paths"] = [workspace["paper_paths"][0]]

        proposed = apply_structured_workspace_patch(
            base_workspace=workspace,
            operations=[
                {
                    "op": "move",
                    "entity_type": "paper_placement",
                    "paper_id": "p1",
                    "to_branch_id": "branch-side",
                }
            ],
        )

        side_paths = [
            path for path in proposed["paper_paths"] if path["branch_node_id"] == "branch-side"
        ]
        self.assertEqual(len(side_paths), 1)
        self.assertEqual(side_paths[0]["paper_ids"], ["p1"])
        self.assertEqual(side_paths[0]["paper_steps"][0]["paper_id"], "p1")
        self.assertEqual(side_paths[0]["paper_steps"][0]["why_read_here"], "Start with the method.")

    def test_a_new_branch_takes_only_editorial_fields_and_is_a_leaf(self) -> None:
        proposed = apply_structured_workspace_patch(
            base_workspace=_workspace(),
            operations=[
                {
                    "op": "insert",
                    "entity_type": "branch",
                    "value": {
                        "node_id": "branch-new",
                        "parent_id": "branch-main",
                        "label": "New",
                        "primary_paper_ids": ["p1"],
                        "child_node_ids": ["branch-main"],
                    },
                }
            ],
        )

        new = next(node for node in proposed["tree"]["nodes"] if node["node_id"] == "branch-new")
        parent = next(node for node in proposed["tree"]["nodes"] if node["node_id"] == "branch-main")
        self.assertEqual(new["primary_paper_ids"], [])
        self.assertEqual(new["child_node_ids"], [])
        self.assertTrue(new["is_leaf"])
        self.assertFalse(parent["is_leaf"])
        self.assertEqual(parent["child_node_ids"], ["branch-new"])

    def test_a_paper_card_written_by_the_caller_is_refused(self) -> None:
        with self.assertRaisesRegex(WorkspacePatchError, "cannot be put back"):
            apply_structured_workspace_patch(
                base_workspace=_workspace(),
                operations=[
                    {
                        "op": "insert",
                        "entity_type": "paper_placement",
                        "paper_id": "new-1",
                        "branch_id": "branch-main",
                        "value": {
                            "paper_card": {
                                "title": "Injected",
                                "paper_content": {"source_url": "https://example.org/x.pdf"},
                            }
                        },
                    }
                ],
            )

    def test_set_refuses_list_elements_of_the_wrong_shape(self) -> None:
        for field_name, value in (("key_terms", [{}]), ("open_questions", [None, "ok"])):
            with self.assertRaisesRegex(WorkspacePatchError, "list of text"):
                apply_structured_workspace_patch(
                    base_workspace=_workspace(),
                    operations=[
                        {"op": "set", "entity_type": "root", "field": field_name, "value": value}
                    ],
                )

    def test_renaming_a_branch_rewrites_its_cards_display_path(self) -> None:
        proposed = apply_structured_workspace_patch(
            base_workspace=_workspace(),
            operations=[
                {
                    "op": "set",
                    "entity_type": "branch",
                    "branch_id": "branch-main",
                    "field": "label",
                    "value": "Core Methods",
                }
            ],
        )

        self.assertEqual(
            proposed["paper_cards"]["p1"]["primary_tree_location"]["path"][-1], "Core Methods"
        )

    def test_removing_one_paper_does_not_read_as_a_global_rewrite(self) -> None:
        workspace = _workspace_with_side_branch()
        proposed = apply_structured_workspace_patch(
            base_workspace=workspace,
            operations=[remove_visible_paper_operation(paper_id="p3")],
        )

        _, summary, warnings = derive_operations_and_diff_summary(
            workspace=workspace, proposed_workspace=proposed
        )

        self.assertFalse(summary["appears_global"])
        self.assertEqual(warnings, [])

    def test_a_malformed_container_is_a_validation_error_not_a_crash(self) -> None:
        workspace = _workspace()
        proposed = {**workspace, "tree": {"root_node_id": "root", "nodes": 1}, "paper_paths": 1}

        operations, summary, _ = derive_operations_and_diff_summary(
            workspace=workspace, proposed_workspace=proposed
        )
        validation = validate_workspace_proposal(
            current_workspace=workspace,
            proposed_workspace=proposed,
            proposed_operations=operations,
            diff_summary=summary,
        )

        self.assertFalse(validation["valid"])
        self.assertTrue(any("tree.nodes must be a list" in error for error in validation["errors"]))
