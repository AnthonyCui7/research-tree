from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_tree.retrieval.candidate_preparation import (
    MAX_FLAGGED_PASSTHROUGH,
    PipelineConfig,
    _candidate_age_years,
    _next_run_output_dir,
    ranking_query,
    run_workspace_candidate_preparation_pipeline,
    select_root_set,
    select_candidates,
    workspace_name,
)
from research_tree.retrieval.citation_graph import CitationGraphRanking
from research_tree.retrieval.models import Paper
from research_tree.retrieval.query_plan import (
    SearchQueryPlan,
    fallback_query_plan,
    query_plan_from_overrides,
)
from research_tree.retrieval.semantic_scholar import (
    SEMANTIC_SCHOLAR_SEARCH_FIELDS,
    SemanticScholarClient,
    paper_from_semantic_scholar,
)


TODAY = date.today()


def _paper(
    title: str,
    *,
    paper_id: str,
    citations: int,
    days_old: int,
    is_survey: bool = False,
    **extra: object,
) -> Paper:
    paper = Paper(
        title=title,
        abstract=f"{title} abstract",
        authors=[f"{title} Author"],
        publication_date=TODAY - timedelta(days=days_old),
        citation_count=citations,
        semantic_scholar_id=paper_id,
        is_survey=is_survey,
        **extra,
    )
    paper.semantic_scholar_metadata = {"paperId": paper_id, "title": title}
    return paper


class FakeSemanticScholar:
    """Stands in for the S2 client above the network layer."""

    def __init__(
        self,
        papers: list[Paper],
        references: dict[str, list[str]],
        snowball_title: str = "Retrieval Augmented Generation Origins {paper_id}",
    ) -> None:
        self.papers = papers
        self.references = references
        self.snowball_title = snowball_title
        self.bulk_queries: list[dict[str, object]] = []
        self.detail_requests: list[list[str]] = []
        self.reference_requests: list[list[str]] = []

    def bulk_search(self, query: str, **kwargs: object) -> list[Paper]:
        self.bulk_queries.append({"query": query, **kwargs})
        if kwargs.get("source_tag") == "s2_bulk_search:recent":
            return []
        return list(self.papers)

    def get_references_batch(
        self,
        paper_ids: list[str],
        warnings: list[str] | None,
        chunk_size: int = 250,
    ) -> dict[str, list[str]]:
        self.reference_requests.append(list(paper_ids))
        return {
            paper_id: self.references.get(paper_id, [])
            for paper_id in paper_ids
            if paper_id in self.references
        }

    def get_paper_details(
        self,
        paper_ids: list[str],
        warnings: list[str] | None,
    ) -> dict[str, dict[str, object]]:
        self.detail_requests.append(list(paper_ids))
        return {
            paper_id: {
                "paperId": paper_id,
                "title": self.snowball_title.format(paper_id=paper_id),
                "abstract": "Founding paper abstract",
                "year": 2017,
                "citationCount": 9000,
                "tldr": {"model": "s2", "text": "A founding contribution."},
            }
            for paper_id in paper_ids
        }


class CandidatePreparationTest(unittest.TestCase):
    def test_ranking_query_and_workspace_use_input_topic(self) -> None:
        self.assertEqual(ranking_query("  retrieval   augmented generation "), "retrieval augmented generation")
        self.assertEqual(workspace_name(" retrieval  generation "), "retrieval generation")
        with self.assertRaises(ValueError):
            ranking_query("  ")

    def test_query_plan_composes_a_boolean_query_without_hyphens(self) -> None:
        plan = SearchQueryPlan(phrases=["nucleus sampling", "speculative decoding"])

        self.assertEqual(
            plan.boolean_query(),
            '"nucleus sampling" | "speculative decoding"',
        )
        self.assertEqual(
            query_plan_from_overrides(["retrieval-augmented generation"]).phrases,
            ["retrieval augmented generation"],
        )
        self.assertEqual(fallback_query_plan("prompting").source, "fallback")

    def test_query_plan_only_filters_on_a_known_field_of_study(self) -> None:
        self.assertEqual(SearchQueryPlan(["x"], "Computer Science").filters(), {"fieldsOfStudy": "Computer Science"})
        self.assertEqual(SearchQueryPlan(["x"], None).filters(), {})

    def test_bulk_search_pages_with_the_continuation_token(self) -> None:
        class FakeJsonClient:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def get_json(self, url: str, params: dict[str, object]) -> dict[str, object]:
                self.calls.append(params)
                if "token" not in params:
                    return {
                        "token": "next-token",
                        "data": [
                            {"paperId": "paper-1", "title": "Paper One", "citationCount": 10},
                            {"paperId": "paper-2", "title": "Paper Two", "citationCount": 9},
                        ],
                    }
                return {"data": [{"paperId": "paper-3", "title": "Paper Three", "citationCount": 8}]}

        client = SemanticScholarClient.__new__(SemanticScholarClient)
        client.client = FakeJsonClient()

        papers = client.bulk_search(
            '"a" | "b"',
            max_papers=3,
            filters={"fieldsOfStudy": "Computer Science"},
            warnings=[],
        )

        self.assertEqual([paper.title for paper in papers], ["Paper One", "Paper Two", "Paper Three"])
        self.assertEqual(client.client.calls[0]["sort"], "citationCount:desc")
        self.assertEqual(client.client.calls[0]["fieldsOfStudy"], "Computer Science")
        self.assertEqual(client.client.calls[1]["token"], "next-token")
        self.assertNotIn("tldr", SEMANTIC_SCHOLAR_SEARCH_FIELDS)
        self.assertIn("publicationVenue", SEMANTIC_SCHOLAR_SEARCH_FIELDS)

    def test_reference_batch_splits_a_chunk_that_fails(self) -> None:
        from research_tree.retrieval.cache import JsonRequestError

        class FakeJsonClient:
            def __init__(self) -> None:
                self.batch_sizes: list[int] = []

            def post_json(self, url: str, body: dict[str, object]) -> object:
                ids = list(body["ids"])
                self.batch_sizes.append(len(ids))
                if len(ids) > 2:
                    raise JsonRequestError("HTTP 413: response too large")
                return [{"paperId": paper_id, "references": [{"paperId": "shared"}]} for paper_id in ids]

        client = SemanticScholarClient.__new__(SemanticScholarClient)
        client.client = FakeJsonClient()

        references = client.get_references_batch(["a", "b", "c", "d"], [], chunk_size=4)

        self.assertEqual(references, {key: ["shared"] for key in ["a", "b", "c", "d"]})
        self.assertEqual(client.client.batch_sizes, [4, 2, 2])

    def test_semantic_scholar_preserves_bulk_metadata(self) -> None:
        paper = paper_from_semantic_scholar(
            {
                "paperId": "paper-1",
                "title": "Paper One",
                "corpusId": 42,
                "referenceCount": 12,
                "publicationVenue": {"name": "TestConf"},
            }
        )

        self.assertEqual(paper.semantic_scholar_metadata["corpusId"], 42)
        self.assertEqual(paper.semantic_scholar_metadata["publicationVenue"], {"name": "TestConf"})

    def test_candidate_age_uses_full_date_then_falls_back_to_july(self) -> None:
        self.assertAlmostEqual(
            _candidate_age_years(Paper(title="Dated", publication_date=date(2026, 1, 3)), date(2026, 7, 3)),
            181 / 365.25,
        )
        self.assertAlmostEqual(
            _candidate_age_years(Paper(title="Year Only", year=2025), date(2026, 7, 3)),
            367 / 365.25,
        )

    def test_root_set_skips_papers_without_an_s2_id(self) -> None:
        pool = [
            _paper("With Id", paper_id="known", citations=10, days_old=100),
            Paper(title="No Id", citation_count=999),
        ]

        self.assertEqual(select_root_set(pool, size=10), ["known"])

    def test_next_run_output_dir_uses_sequential_run_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "run1").mkdir()
            (base / "run2").mkdir()
            (base / "notes").mkdir()

            self.assertEqual(_next_run_output_dir(base).name, "run3")

    def test_pipeline_ranks_by_citation_graph_authority(self) -> None:
        pool = [
            _paper("Recent Celebrity", paper_id="celebrity", citations=400, days_old=200),
            _paper("Foundational Method", paper_id="foundational", citations=300, days_old=2500),
            _paper("Citing Work One", paper_id="citing-1", citations=90, days_old=400),
            _paper("Citing Work Two", paper_id="citing-2", citations=80, days_old=420),
            _paper("Field Survey", paper_id="survey", citations=60, days_old=300, is_survey=True),
        ]
        references = {
            "citing-1": ["foundational", "missing-classic"],
            "citing-2": ["foundational", "missing-classic"],
            "survey": ["foundational", "citing-1", "citing-2", "missing-classic"],
            "celebrity": ["foundational"],
            "foundational": [],
        }
        fake = FakeSemanticScholar(pool, references)

        with tempfile.TemporaryDirectory() as directory:
            config = PipelineConfig(
                repo_root=Path(directory),
                topic="retrieval augmented generation",
                k=3,
                survey_baseline_count=1,
                snowball_min_in_degree=3,
                verbose=False,
            )
            with (
                patch(
                    "research_tree.retrieval.candidate_preparation.plan_search_queries",
                    return_value=SearchQueryPlan(["retrieval augmented generation"], "Computer Science"),
                ),
                patch(
                    "research_tree.retrieval.candidate_preparation._semantic_scholar_client",
                    return_value=fake,
                ),
            ):
                output = run_workspace_candidate_preparation_pipeline(config)
                run_dir = Path(str(output["run_dir"]))
                final_json = json.loads((run_dir / "llm_candidate_papers.json").read_text())
                database_json = json.loads(
                    (run_dir / "s2_bulk_deduped_paper_database.json").read_text()
                )

        titles = [paper["title"] for paper in output["non_survey_papers"]]
        self.assertEqual(titles[0], "Foundational Method")
        self.assertIn("Retrieval Augmented Generation Origins missing-classic", titles)
        self.assertNotIn("Recent Celebrity", titles[:2])
        self.assertEqual(output["survey_papers"][0]["title"], "Field Survey")

        leader = output["non_survey_papers"][0]
        self.assertEqual(leader["paper_id"], "foundational")
        self.assertEqual(leader["authority_rank"], 1)
        self.assertEqual(leader["in_degree"], 4)
        self.assertTrue(leader["s2_link"].endswith("foundational"))
        self.assertGreater(leader["authority_score"], 0)

        snowballed = next(p for p in output["non_survey_papers"] if p["snowballed"])
        self.assertIn("snowball:root_set_references", snowballed["found_by"])
        self.assertFalse(snowballed["flagged_off_topic"])
        self.assertEqual(
            snowballed["semantic_scholar_metadata"]["tldr"]["text"],
            "A founding contribution.",
        )
        self.assertEqual(output["snowballed_count"], 1)

        self.assertEqual(final_json["schema_version"], "llm_candidate_papers.v2")
        self.assertEqual(database_json["schema_version"], "s2_bulk_deduped_paper_database.v2")
        self.assertEqual(database_json["paper_metadata_shape"], "semantic_scholar_bulk_complete")
        self.assertEqual(database_json["paper_count"], 6)
        self.assertFalse(output["llm_curation_complete"])
        self.assertEqual(output["workspace"], "retrieval augmented generation")
        self.assertEqual(output["candidate_pool_order"], "hits_authority_desc")
        self.assertTrue(output["hits_converged"])
        self.assertEqual(fake.bulk_queries[0]["filters"], {"fieldsOfStudy": "Computer Science"})

    def test_snowball_flags_papers_that_are_not_about_the_topic(self) -> None:
        """Infrastructure everything cites must not crowd out the field's own work.

        Snowballed papers arrive by citation count alone, so an optimizer or a
        dataset can out-rank the papers a reader actually came for. Such papers
        stay in the hand-off marked flagged_off_topic — the construction model
        makes the final call — but they never consume one of the k slots.
        """

        pool = [
            _paper("Retrieval Method One", paper_id="citing-1", citations=90, days_old=400),
            _paper("Retrieval Method Two", paper_id="citing-2", citations=80, days_old=420),
            _paper("Retrieval Method Three", paper_id="citing-3", citations=70, days_old=440),
        ]
        references = {
            "citing-1": ["infrastructure"],
            "citing-2": ["infrastructure"],
            "citing-3": ["infrastructure"],
        }
        fake = FakeSemanticScholar(
            pool,
            references,
            snowball_title="Adam: A Method for Stochastic Optimization",
        )

        with tempfile.TemporaryDirectory() as directory:
            config = PipelineConfig(
                repo_root=Path(directory),
                topic="retrieval augmented generation",
                k=3,
                survey_baseline_count=0,
                verbose=False,
            )
            with (
                patch(
                    "research_tree.retrieval.candidate_preparation.plan_search_queries",
                    return_value=SearchQueryPlan(["retrieval augmented generation"]),
                ),
                patch(
                    "research_tree.retrieval.candidate_preparation._semantic_scholar_client",
                    return_value=fake,
                ),
            ):
                output = run_workspace_candidate_preparation_pipeline(config)

        papers = output["non_survey_papers"]
        by_title = {paper["title"]: paper for paper in papers}
        adam = by_title["Adam: A Method for Stochastic Optimization"]
        self.assertTrue(adam["flagged_off_topic"])
        self.assertEqual(output["snowballed_count"], 1)
        self.assertEqual(output["flagged_off_topic_count"], 1)
        # Adam out-ranks the topical papers by authority yet does not consume a
        # slot: all three unflagged papers are still present alongside the flag.
        unflagged = [p for p in papers if not p["flagged_off_topic"]]
        self.assertEqual(len(unflagged), 3)
        self.assertEqual(len(papers), 4)
        self.assertFalse(any(warning for warning in output["warnings"]))

    def test_flagged_passthrough_is_capped(self) -> None:
        """An ambiguous topic can flag most of the snowball; the hand-off must not balloon.

        A mistakenly flagged founding paper is by definition a top authority, so
        carrying only the first MAX_FLAGGED_PASSTHROUGH flagged papers keeps the
        rescue value without letting infrastructure dwarf the real candidates.
        """

        ranked = [
            _paper(
                f"Infrastructure {index}",
                paper_id=f"infra-{index}",
                citations=1000 - index,
                days_old=2000,
                flagged_off_topic=True,
            )
            for index in range(30)
        ] + [
            _paper(f"Topical {index}", paper_id=f"topic-{index}", citations=100 - index, days_old=400)
            for index in range(10)
        ]

        non_survey, surveys = select_candidates(
            ranked, ranking=CitationGraphRanking({}, {}, {}), k=5, survey_count=0
        )

        self.assertEqual(surveys, [])
        flagged = [paper for paper in non_survey if paper.flagged_off_topic]
        unflagged = [paper for paper in non_survey if not paper.flagged_off_topic]
        self.assertEqual(len(flagged), MAX_FLAGGED_PASSTHROUGH)
        self.assertEqual(len(unflagged), 5)
        # Flagged papers keep their ranked position ahead of the topical papers.
        self.assertTrue(non_survey[0].flagged_off_topic)

    def test_pipeline_falls_back_to_age_adjusted_order_without_references(self) -> None:
        pool = [
            _paper("Recent Leader", paper_id="leader", citations=25, days_old=30),
            _paper("Older Citation Leader", paper_id="old", citations=100, days_old=3650),
        ]
        fake = FakeSemanticScholar(pool, references={})

        with tempfile.TemporaryDirectory() as directory:
            config = PipelineConfig(
                repo_root=Path(directory),
                topic="prompting",
                k=2,
                survey_baseline_count=1,
                verbose=False,
            )
            with (
                patch(
                    "research_tree.retrieval.candidate_preparation.plan_search_queries",
                    return_value=fallback_query_plan("prompting"),
                ),
                patch(
                    "research_tree.retrieval.candidate_preparation._semantic_scholar_client",
                    return_value=fake,
                ),
            ):
                output = run_workspace_candidate_preparation_pipeline(config)

        self.assertEqual(
            [paper["title"] for paper in output["non_survey_papers"]],
            ["Recent Leader", "Older Citation Leader"],
        )
        self.assertTrue(
            any("age-adjusted citation order" in warning for warning in output["warnings"])
        )
        self.assertFalse(output["retrieval_complete"])

    def test_pipeline_writes_empty_artifacts_without_candidates(self) -> None:
        fake = FakeSemanticScholar([], references={})

        with tempfile.TemporaryDirectory() as directory:
            config = PipelineConfig(repo_root=Path(directory), topic="prompting", verbose=False)
            with (
                patch(
                    "research_tree.retrieval.candidate_preparation.plan_search_queries",
                    return_value=fallback_query_plan("prompting"),
                ),
                patch(
                    "research_tree.retrieval.candidate_preparation._semantic_scholar_client",
                    return_value=fake,
                ),
            ):
                output = run_workspace_candidate_preparation_pipeline(config)
                run_dir = Path(str(output["run_dir"]))
                artifact_written = (run_dir / "llm_candidate_papers.json").is_file()

        self.assertEqual(output["non_survey_papers"], [])
        self.assertTrue(artifact_written)
        self.assertTrue(any("No Semantic Scholar candidates" in w for w in output["warnings"]))


if __name__ == "__main__":
    unittest.main()
