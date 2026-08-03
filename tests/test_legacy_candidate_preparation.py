"""Contract test for the retired age-adjusted candidates stage.

Kept so the comparison harness measures working code. Delete alongside
`research_tree.retrieval.legacy`.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_tree.retrieval.models import Paper
from research_tree.retrieval.legacy.candidate_preparation import (
    PipelineConfig,
    _candidate_age_years,
    _citation_age_scoring_formula,
    _next_run_output_dir,
    _rank_by_age_adjusted_citations,
    citation_search_query_variants,
    ranking_query,
    run_workspace_candidate_preparation_pipeline,
    workspace_name,
)
from research_tree.retrieval.semantic_scholar import (
    SEMANTIC_SCHOLAR_SEARCH_FIELDS,
    SemanticScholarClient,
    paper_from_semantic_scholar,
)


class LegacyCandidatePreparationTest(unittest.TestCase):
    def test_citation_search_query_variants_accept_any_topic(self) -> None:
        queries = citation_search_query_variants("retrieval augmented generation")

        self.assertEqual(
            queries,
            [
                "Large Language Model retrieval augmented generation",
                "Large Language Model retrieval augmented generation paper",
                "Large Language Model retrieval augmented generation method",
                "Large Language Model retrieval augmented generation origin",
                "Large Language Model retrieval augmented generation technique",
                "Large Language Model retrieval augmented generation survey",
            ],
        )

    def test_ranking_query_and_workspace_use_input_topic(self) -> None:
        self.assertEqual(ranking_query(" retrieval   augmented generation "), "retrieval augmented generation")
        self.assertEqual(workspace_name(" retrieval   augmented generation "), "retrieval augmented generation")
        with self.assertRaises(ValueError):
            ranking_query("  ")

    def test_pipeline_config_defaults_to_alpha_125(self) -> None:
        config = PipelineConfig(repo_root=Path("."), topic="prompting")

        self.assertEqual(config.citation_age_exponent, 1.25)

    def test_semantic_scholar_bulk_citation_search_uses_pagination_token(self) -> None:
        class FakeJsonClient:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def get_json(
                self,
                url: str,
                params: dict[str, object],
            ) -> dict[str, object]:
                self.calls.append(params)
                if "token" not in params:
                    return {
                        "token": "next-token",
                        "data": [
                            {
                                "paperId": "paper-1",
                                "title": "Paper One",
                                "year": 2024,
                                "citationCount": 10,
                            },
                            {
                                "paperId": "paper-2",
                                "title": "Paper Two",
                                "year": 2024,
                                "citationCount": 9,
                            },
                        ],
                    }
                return {
                    "data": [
                        {
                            "paperId": "paper-3",
                            "title": "Paper Three",
                            "year": 2024,
                            "citationCount": 8,
                        }
                    ]
                }

        semantic_scholar = SemanticScholarClient.__new__(SemanticScholarClient)
        semantic_scholar.client = FakeJsonClient()

        papers = semantic_scholar.search_by_citation_count(
            query="retrieval augmented generation",
            target_count=3,
            warnings=[],
        )

        self.assertEqual(
            [paper.title for paper in papers],
            ["Paper One", "Paper Two", "Paper Three"],
        )
        self.assertEqual(semantic_scholar.client.calls[0]["sort"], "citationCount:desc")
        self.assertEqual(semantic_scholar.client.calls[1]["token"], "next-token")
        self.assertIn("publicationVenue", SEMANTIC_SCHOLAR_SEARCH_FIELDS)
        self.assertIn("citationStyles", SEMANTIC_SCHOLAR_SEARCH_FIELDS)
        self.assertNotIn("tldr", SEMANTIC_SCHOLAR_SEARCH_FIELDS)

    def test_semantic_scholar_preserves_bulk_metadata(self) -> None:
        paper = paper_from_semantic_scholar(
            {
                "paperId": "paper-1",
                "title": "Paper One",
                "corpusId": 42,
                "referenceCount": 12,
                "publicationVenue": {"name": "TestConf"},
                "openAccessPdf": {"url": "https://example.com/paper.pdf"},
            }
        )

        self.assertEqual(paper.semantic_scholar_metadata["corpusId"], 42)
        self.assertEqual(paper.semantic_scholar_metadata["referenceCount"], 12)
        self.assertEqual(
            paper.semantic_scholar_metadata["publicationVenue"],
            {"name": "TestConf"},
        )

    def test_candidate_age_uses_full_date_fractional_years(self) -> None:
        paper = Paper(title="Dated", publication_date=date(2026, 1, 3))

        self.assertAlmostEqual(
            _candidate_age_years(paper, as_of=date(2026, 7, 3)),
            181 / 365.25,
        )

    def test_candidate_age_falls_back_to_july_first_for_year_only(self) -> None:
        paper = Paper(title="Year Only", year=2025)

        self.assertAlmostEqual(
            _candidate_age_years(paper, as_of=date(2026, 7, 3)),
            367 / 365.25,
        )

    def test_age_adjusted_ranking_uses_half_year_floor(self) -> None:
        paper = Paper(
            title="Recent Paper",
            publication_date=date(2026, 6, 3),
            citation_count=10,
        )

        _rank_by_age_adjusted_citations(
            [paper],
            as_of=date(2026, 7, 3),
            exponent=1.25,
        )

        self.assertAlmostEqual(paper.final_score, 10 / (0.5**1.25))
        self.assertAlmostEqual(paper.citations_per_year, 20.0)
        self.assertEqual(
            _citation_age_scoring_formula(1.25),
            "citation_count / max(age_years, 0.5)^1.25",
        )

    def test_next_run_output_dir_uses_sequential_run_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_base_dir = Path(directory)
            (output_base_dir / "run1").mkdir()
            (output_base_dir / "run2").mkdir()
            (output_base_dir / "notes").mkdir()

            next_run_dir = _next_run_output_dir(output_base_dir)

        self.assertEqual(next_run_dir.name, "run3")

    def test_candidate_preparation_writes_llm_ready_metadata(self) -> None:
        today = date.today()

        class FakeSemanticScholar:
            def search_by_citation_count(
                self,
                query: str,
                target_count: int | None,
                warnings: list[str] | None = None,
            ) -> list[Paper]:
                return [
                    Paper(
                        title="Older Citation Leader",
                        abstract="Older abstract",
                        authors=["Old Author"],
                        publication_date=today - timedelta(days=3650),
                        citation_count=100,
                        semantic_scholar_id="old",
                    ),
                    Paper(
                        title="Recent Age Adjusted Leader",
                        abstract="Leader abstract",
                        authors=["First Author", "Second Author"],
                        publication_date=today - timedelta(days=30),
                        citation_count=25,
                        doi="10.1000/test",
                        arxiv_id="2402.07927",
                        semantic_scholar_id="leader",
                        url="https://www.semanticscholar.org/paper/leader",
                    ),
                    Paper(
                        title="Recent Age Adjusted Runner Up",
                        abstract="Runner up abstract",
                        authors=["Runner Author"],
                        publication_date=today - timedelta(days=60),
                        citation_count=20,
                        semantic_scholar_id="runner",
                    ),
                    Paper(
                        title="Recent Survey",
                        abstract="Survey abstract",
                        authors=["Survey Author"],
                        publication_date=today - timedelta(days=30),
                        citation_count=15,
                        semantic_scholar_id="survey",
                        is_survey=True,
                    ),
                ]

        class FakeCrossEncoder:
            def __init__(self, model_name: str) -> None:
                self.model_name = model_name

            def rerank(self, query: str, papers: list[Paper]) -> None:
                for paper in papers:
                    paper.cross_encoder_relevance = 0.1
                papers[-1].cross_encoder_relevance = 0.9

        with tempfile.TemporaryDirectory() as directory:
            config = PipelineConfig(
                repo_root=Path(directory),
                topic="retrieval augmented generation",
                k=2,
                survey_baseline_count=1,
                s2_bulk_citation_multiplier=4,
                verbose=False,
            )
            with (
                patch(
                    "research_tree.retrieval.legacy.candidate_preparation.citation_search_query_variants",
                    return_value=["query a"],
                ),
                patch(
                    "research_tree.retrieval.legacy.candidate_preparation._semantic_scholar_client",
                    return_value=FakeSemanticScholar(),
                ),
                patch(
                    "research_tree.retrieval.legacy.candidate_preparation.LocalCrossEncoderReranker",
                    FakeCrossEncoder,
                ),
            ):
                output = run_workspace_candidate_preparation_pipeline(config)
                output_dir = Path(str(output["run_dir"]))
                final_json = json.loads(
                    (output_dir / "llm_candidate_papers.json").read_text(
                        encoding="utf-8"
                    )
                )
                paper_database_json = json.loads(
                    (output_dir / "s2_bulk_deduped_paper_database.json").read_text(
                        encoding="utf-8"
                    )
                )

        self.assertFalse(output["llm_curation_complete"])
        self.assertEqual(output["workspace"], "retrieval augmented generation")
        self.assertEqual(output["cross_encoder_relevance_usage"], "metadata_only")
        self.assertIn("llm_handoff", output)
        self.assertEqual(
            [paper["title"] for paper in output["non_survey_papers"]],
            ["Recent Age Adjusted Leader", "Recent Age Adjusted Runner Up"],
        )
        self.assertEqual(output["survey_papers"][0]["title"], "Recent Survey")
        leader = output["non_survey_papers"][0]
        self.assertEqual(leader["abstract"], "Leader abstract")
        self.assertEqual(leader["paper_id"], "leader")
        self.assertEqual(leader["doi"], "10.1000/test")
        self.assertEqual(leader["arxiv_id"], "2402.07927")
        self.assertEqual(leader["authors"], ["First Author", "Second Author"])
        self.assertEqual(leader["raw_citation_rank"], 2)
        self.assertEqual(leader["age_adjusted_rank"], 1)
        self.assertEqual(leader["cross_encoder_rank"], 2)
        self.assertEqual(leader["arxiv_link"], "https://arxiv.org/abs/2402.07927")
        self.assertEqual(leader["s2_link"], "https://www.semanticscholar.org/paper/leader")
        self.assertEqual(output["survey_papers"][0]["cross_encoder_relevance"], 0.9)
        self.assertEqual(final_json["schema_version"], "llm_candidate_papers.v1")
        self.assertEqual(
            paper_database_json["schema_version"],
            "s2_bulk_deduped_paper_database.v1",
        )
        self.assertEqual(
            paper_database_json["paper_metadata_shape"],
            "semantic_scholar_bulk_complete",
        )
        self.assertEqual(paper_database_json["paper_count"], 4)


if __name__ == "__main__":
    unittest.main()
