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
    _ranked_papers,
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
    judge_flagged_papers,
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

    def test_rate_limiter_spaces_requests_across_instances_via_lock_file(self) -> None:
        # Each process gets its own module-level limiter, so the spacing must
        # live in the shared lock file — two instances model two processes.
        import time as time_module

        from research_tree.retrieval.cache import RateLimiter

        with tempfile.TemporaryDirectory() as directory:
            lock_file = Path(directory) / "limiter.lock"
            first = RateLimiter(lock_file=lock_file)
            second = RateLimiter(lock_file=lock_file)

            first.acquire(0.3)
            started = time_module.monotonic()
            second.acquire(0.3)
            elapsed = time_module.monotonic() - started

        self.assertGreaterEqual(elapsed, 0.25)

    def test_bulk_search_failure_kills_the_run(self) -> None:
        # The JSON client only raises after its own backoff retries, so a
        # failure here is sustained throttling or an outage. A truncated pool
        # silently reshapes every later step; the run must die loudly instead.
        from research_tree.retrieval.cache import JsonRequestError

        class FakeJsonClient:
            def get_json(self, url: str, params: dict[str, object]) -> object:
                raise JsonRequestError("HTTP 429: Too Many Requests")

        client = SemanticScholarClient.__new__(SemanticScholarClient)
        client.client = FakeJsonClient()

        with self.assertRaises(JsonRequestError) as raised:
            client.bulk_search('"a" | "b"', max_papers=10, warnings=[])

        self.assertIn("after retries", str(raised.exception))
        self.assertIn("429", str(raised.exception))

    def test_reference_fetch_failure_on_a_single_paper_kills_the_run(self) -> None:
        # Halving handles oversized chunks, but a single-paper request cannot
        # be oversized — if it still fails after the client's retries, the
        # service is down and the run must not continue on a partial graph.
        from research_tree.retrieval.cache import JsonRequestError

        class FakeJsonClient:
            def post_json(self, url: str, body: dict[str, object]) -> object:
                raise JsonRequestError("HTTP 429: Too Many Requests")

        client = SemanticScholarClient.__new__(SemanticScholarClient)
        client.client = FakeJsonClient()

        with self.assertRaises(JsonRequestError) as raised:
            client.get_references_batch(["a", "b"], [], chunk_size=2)

        self.assertIn("after retries", str(raised.exception))

    def test_reference_batch_retries_papers_with_the_field_left_out(self) -> None:
        """A 200 with `references` absent is a partial failure, not an answer.

        Under load Semantic Scholar returns the batch with the references
        field simply missing for a subset of papers (measured Aug 2026: up to
        179 of 250 in one chunk). Treating that as an empty bibliography
        silently halves the citation graph — and the poisoned response gets
        cached. The retry uses only the missing ids, so a cached partial
        response can never satisfy it.
        """

        class FakeJsonClient:
            def __init__(self) -> None:
                self.request_ids: list[list[str]] = []

            def post_json(self, url: str, body: dict[str, object]) -> object:
                ids = list(body["ids"])
                self.request_ids.append(ids)
                first_call = len(self.request_ids) == 1
                return [
                    # First call: "b" comes back without the field, "e" comes
                    # back empty despite a nonzero referenceCount, "gone"
                    # never recovers. "c" legitimately has zero references.
                    {"paperId": "a", "references": [{"paperId": "shared"}]}
                    if paper_id == "a"
                    else {"paperId": "c", "references": [], "referenceCount": 0}
                    if paper_id == "c"
                    else {"paperId": "e", "references": [], "referenceCount": 3}
                    if paper_id == "e" and first_call
                    else {"paperId": paper_id}
                    if first_call or paper_id == "gone"
                    else {"paperId": paper_id, "references": [{"paperId": "late"}]}
                    for paper_id in ids
                ]

        client = SemanticScholarClient.__new__(SemanticScholarClient)
        client.client = FakeJsonClient()
        warnings: list[str] = []

        references = client.get_references_batch(
            ["a", "b", "c", "e", "gone"], warnings, chunk_size=5
        )

        self.assertEqual(
            references, {"a": ["shared"], "b": ["late"], "c": [], "e": ["late"]}
        )
        self.assertEqual(
            client.client.request_ids, [["a", "b", "c", "e", "gone"], ["b", "e", "gone"]]
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("1 of 5", warnings[0])

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
        self.assertEqual(output["candidate_pool_order"], "hits_authority_age_normalized_desc")
        self.assertTrue(output["hits_converged"])
        self.assertEqual(fake.bulk_queries[0]["filters"], {"fieldsOfStudy": "Computer Science"})

    def test_age_normalization_lets_recent_cohorts_compete(self) -> None:
        """A recent paper with real votes must not lose to age alone.

        Raw authority compounds with age, so a 20-year-old paper with slightly
        more authority always outranks the field's current canon. Cohort
        normalization divides by (age+1)^0.75; exponent 0 recovers the pure
        authority order.
        """

        old = _paper("Old Classic", paper_id="old", citations=5000, days_old=365 * 20)
        recent = _paper("Recent Canon", paper_id="recent", citations=800, days_old=365 * 2)
        ranking = CitationGraphRanking(
            authority={"old": 0.5, "recent": 0.3},
            hub={},
            in_degree={"old": 10, "recent": 6},
        )

        normalized = _ranked_papers(
            [old, recent], ranking, as_of=TODAY, age_exponent=0.75
        )
        self.assertEqual([p.semantic_scholar_id for p in normalized], ["recent", "old"])

        pure = _ranked_papers([old, recent], ranking, as_of=TODAY, age_exponent=0.0)
        self.assertEqual([p.semantic_scholar_id for p in pure], ["old", "recent"])

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
                # The model judge agrees with the token flag for this run.
                patch(
                    "research_tree.retrieval.candidate_preparation.judge_flagged_papers",
                    return_value=set(),
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

    def test_flag_judge_rescues_a_founding_paper(self) -> None:
        """A paper the model judge says belongs loses its flag and fills a real slot.

        The token match cannot tell DPR from Adam; the judge can. Measured on
        prompting / RAG / sampling, the judge rescued every wrongly flagged core
        paper with zero false keeps.
        """

        pool = [
            _paper("Retrieval Method One", paper_id="citing-1", citations=90, days_old=400),
            _paper("Retrieval Method Two", paper_id="citing-2", citations=80, days_old=420),
            _paper("Retrieval Method Three", paper_id="citing-3", citations=70, days_old=440),
        ]
        references = {
            "citing-1": ["founding"],
            "citing-2": ["founding"],
            "citing-3": ["founding"],
        }
        fake = FakeSemanticScholar(
            pool,
            references,
            snowball_title="Latent Retrieval for Weakly Supervised Open Domain QA",
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
                patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
                patch(
                    "research_tree.retrieval.candidate_preparation.plan_search_queries",
                    return_value=SearchQueryPlan(["retrieval augmented generation"]),
                ),
                patch(
                    "research_tree.retrieval.candidate_preparation._semantic_scholar_client",
                    return_value=fake,
                ),
                patch(
                    "research_tree.retrieval.candidate_preparation.judge_flagged_papers",
                    return_value={"founding"},
                ) as judge,
            ):
                output = run_workspace_candidate_preparation_pipeline(config)

        judge.assert_called_once()
        papers = output["non_survey_papers"]
        titles = [p["title"] for p in papers]
        # The rescued paper is unflagged, ranks first, and consumes a real slot.
        self.assertEqual(titles[0], "Latent Retrieval for Weakly Supervised Open Domain QA")
        self.assertFalse(papers[0]["flagged_off_topic"])
        self.assertEqual(len(papers), 3)
        self.assertEqual(output["flagged_off_topic_count"], 0)
        self.assertFalse(any(warning for warning in output["warnings"]))

    def test_flag_judge_without_a_key_returns_none(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(
                judge_flagged_papers(
                    "retrieval augmented generation",
                    ["retrieval augmented generation"],
                    [_paper("Adam", paper_id="adam", citations=1, days_old=100)],
                )
            )

    def test_flag_judge_with_no_flagged_papers_is_a_no_op(self) -> None:
        self.assertEqual(
            judge_flagged_papers("topic", ["topic"], []),
            set(),
        )

    def test_graph_blind_cutoff_is_measured_not_assumed(self) -> None:
        """The frontier band is the years the graph cannot rank, per run.

        Measured Aug 2026: prompting went blind at 2024, RAG at 2025,
        sampling at 2023 — no fixed window fits every field. A year is blind
        when its pool papers average under half an in-pool vote (or the year
        has too few papers to tell); the walk back from the present stops at
        the first sighted year and is capped by max_years.
        """

        from research_tree.retrieval.candidate_preparation import graph_blind_cutoff

        as_of = date(2026, 8, 1)
        pool = []
        in_degree = {}
        # 2023 is sighted: 12 papers averaging 1 vote. 2024-2026 are blind.
        for year, votes in ((2023, 1), (2024, 0), (2025, 0), (2026, 0)):
            for index in range(12):
                paper_id = f"{year}-{index}"
                pool.append(
                    _paper(f"Paper {paper_id}", paper_id=paper_id, citations=10,
                           days_old=(2026 - year) * 365 + 30)
                )
                in_degree[paper_id] = votes
        ranking = CitationGraphRanking(authority={}, hub={}, in_degree=in_degree)

        cutoff = graph_blind_cutoff(pool, ranking, as_of=as_of, max_years=6)
        self.assertEqual(cutoff, 2024)

        # A sparse year (too few papers) is blind; the cap bounds the walk.
        sparse = [p for p in pool if not p.semantic_scholar_id.startswith("2023")]
        cutoff = graph_blind_cutoff(sparse, ranking, as_of=as_of, max_years=2)
        self.assertEqual(cutoff, 2025)

    def test_frontier_picks_add_judged_recent_papers(self) -> None:
        """Recent papers the judge keeps join the hand-off despite zero authority.

        Citation authority cannot rank recent work — hubs that predate a paper
        can never cite it — so reserved frontier slots are filled by citation
        velocity and screened by the judge. A rejected celebrity paper stays out.
        """

        pool = [
            _paper("Old Canon One", paper_id="old-1", citations=900, days_old=2400),
            _paper("Old Canon Two", paper_id="old-2", citations=800, days_old=2200),
            _paper("Old Canon Three", paper_id="old-3", citations=700, days_old=2000),
            _paper("Tree of Prompts", paper_id="tot", citations=400, days_old=300),
            _paper("Segment Everything", paper_id="sam", citations=900, days_old=250),
        ]
        references = {
            "old-1": ["old-2", "old-3"],
            "old-2": ["old-3"],
            "old-3": ["old-2"],
        }
        fake = FakeSemanticScholar(pool, references)

        with tempfile.TemporaryDirectory() as directory:
            config = PipelineConfig(
                repo_root=Path(directory),
                topic="prompting",
                k=2,
                survey_baseline_count=0,
                verbose=False,
            )
            with (
                patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
                patch(
                    "research_tree.retrieval.candidate_preparation.plan_search_queries",
                    return_value=SearchQueryPlan(["prompting"]),
                ),
                patch(
                    "research_tree.retrieval.candidate_preparation._semantic_scholar_client",
                    return_value=fake,
                ),
                patch(
                    "research_tree.retrieval.candidate_preparation.judge_flagged_papers",
                    return_value={"tot"},
                ) as judge,
            ):
                output = run_workspace_candidate_preparation_pipeline(config)

        judge.assert_called_once()
        judged_ids = {
            paper.semantic_scholar_id for paper in judge.call_args.args[2]
        }
        self.assertIn("tot", judged_ids)
        self.assertIn("sam", judged_ids)
        papers = output["non_survey_papers"]
        self.assertEqual(output["frontier_pick_count"], 1)
        self.assertEqual(papers[-1]["paper_id"], "tot")
        self.assertTrue(papers[-1]["frontier_pick"])
        self.assertFalse(papers[-1]["flagged_off_topic"])
        # The judged-out celebrity paper takes no slot.
        self.assertNotIn("sam", [paper["paper_id"] for paper in papers])
        # Authority slots are untouched: the top-k block is still the old canon
        # (equal authority, so age-cohort normalization favors the younger one).
        self.assertEqual([paper["paper_id"] for paper in papers[:2]], ["old-3", "old-2"])

    def test_frontier_picks_require_the_judge(self) -> None:
        """Without a judge the velocity slice stays out: it is mostly celebrity papers."""

        pool = [
            _paper("Old Canon One", paper_id="old-1", citations=900, days_old=2400),
            _paper("Old Canon Two", paper_id="old-2", citations=800, days_old=2200),
            _paper("Recent Celebrity", paper_id="recent", citations=900, days_old=250),
        ]
        references = {"old-1": ["old-2"], "old-2": ["old-1"]}
        fake = FakeSemanticScholar(pool, references)

        with tempfile.TemporaryDirectory() as directory:
            config = PipelineConfig(
                repo_root=Path(directory),
                topic="prompting",
                k=2,
                survey_baseline_count=0,
                verbose=False,
            )
            with (
                patch.dict("os.environ", {}, clear=True),
                patch(
                    "research_tree.retrieval.candidate_preparation.plan_search_queries",
                    return_value=SearchQueryPlan(["prompting"]),
                ),
                patch(
                    "research_tree.retrieval.candidate_preparation._semantic_scholar_client",
                    return_value=fake,
                ),
            ):
                output = run_workspace_candidate_preparation_pipeline(config)

        self.assertEqual(output["frontier_pick_count"], 0)
        self.assertFalse(
            any(paper["frontier_pick"] for paper in output["non_survey_papers"])
        )

    def test_frontier_pick_is_not_duplicated_when_already_selected(self) -> None:
        """A recent paper that earned a top-k slot by authority is not re-added."""

        from research_tree.retrieval.candidate_preparation import select_frontier_picks

        recent = _paper("Recent Authority", paper_id="recent", citations=400, days_old=300)
        other = _paper("Recent Other", paper_id="other", citations=300, days_old=200)
        picks = select_frontier_picks(
            [recent, other],
            belongs={"recent", "other"},
            already_selected={"recent"},
            limit=10,
        )
        self.assertEqual([paper.semantic_scholar_id for paper in picks], ["other"])
        self.assertTrue(other.frontier_pick)
        self.assertFalse(recent.frontier_pick)

    def test_flagged_papers_ride_along_without_consuming_slots(self) -> None:
        """Flagged papers keep their ranked position; unflagged papers fill the k slots.

        The passthrough cap exists because, measured uncapped on prompting / RAG /
        sampling, 58-75 flagged papers rode along while every wrongly flagged core
        paper sat within the first ~12 flagged positions.
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

    def test_pipeline_fails_when_no_reference_lists_come_back(self) -> None:
        # Request failures raise inside the client, so an empty reference map
        # means S2 hydrated nothing. There is no graph to rank — the run must
        # fail with the reason rather than ship a differently-ranked artifact.
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
                with self.assertRaises(RuntimeError) as raised:
                    run_workspace_candidate_preparation_pipeline(config)

        self.assertIn("no reference lists", str(raised.exception))

    def test_pipeline_fails_when_search_matches_nothing(self) -> None:
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
                with self.assertRaises(RuntimeError) as raised:
                    run_workspace_candidate_preparation_pipeline(config)

        self.assertIn("no papers", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
