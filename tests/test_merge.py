from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_tree.retrieval.merge import dedupe_papers
from research_tree.retrieval.models import Paper


class MergeTest(unittest.TestCase):
    def test_dedupe_merges_by_doi_and_title(self) -> None:
        first = Paper(
            title="Chain-of-Thought Prompting Elicits Reasoning",
            doi="https://doi.org/10.123/ABC",
            found_by={"query_search:Large Language Models prompting"},
        )
        second = Paper(
            title="Chain of Thought Prompting Elicits Reasoning",
            abstract="Longer abstract",
            doi="10.123/abc",
            citation_count=25,
            found_by={"seed_reference:seed"},
        )

        merged = dedupe_papers([first, second])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].doi, "10.123/abc")
        self.assertEqual(merged[0].abstract, "Longer abstract")
        self.assertEqual(merged[0].citation_count, 25)
        self.assertEqual(
            merged[0].found_by,
            {"query_search:Large Language Models prompting", "seed_reference:seed"},
        )

    def test_dedupe_merges_by_normalized_title(self) -> None:
        merged = dedupe_papers(
            [
                Paper(title="Self-Consistency Improves Chain of Thought Reasoning"),
                Paper(title="Self Consistency Improves Chain-of-Thought Reasoning"),
            ]
        )

        self.assertEqual(len(merged), 1)

    def test_dedupe_folds_records_a_later_paper_proves_are_the_same(self) -> None:
        """Two records joined by a third must not survive as two.

        A DOI-only record and an arXiv-only record are separate entries until
        a record carrying *both* identifiers arrives. It merges into the first
        match, and its identity keys then re-point the arXiv alias at that
        record — so without folding, the arXiv record stays in the output as a
        duplicate nothing can ever reach again.
        """

        doi_only = Paper(title="A", doi="10.1/x", citation_count=5)
        arxiv_only = Paper(title="B", arxiv_id="2501.00001", citation_count=7)
        both = Paper(title="C", doi="10.1/x", arxiv_id="2501.00001", citation_count=9)

        merged = dedupe_papers([doi_only, arxiv_only, both])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].doi, "10.1/x")
        self.assertEqual(merged[0].arxiv_id, "2501.00001")
        self.assertEqual(merged[0].citation_count, 9)

    def test_dedupe_keeps_a_later_alias_of_a_folded_record_resolvable(self) -> None:
        # Folding pops the superseded record, so every alias that pointed at it
        # has to be re-pointed too. Here the short title "DPR" loses the merge,
        # so re-registering the survivor's identity keys does not cover it: an
        # unrepointed `title:dpr` alias sends the fourth paper to a record that
        # is no longer in the map.
        merged = dedupe_papers(
            [
                Paper(title="Dense Passage Retrieval for Open Domain QA", doi="10.1/x"),
                Paper(title="DPR", arxiv_id="1906.00300"),
                Paper(title="Joined", doi="10.1/x", arxiv_id="1906.00300"),
                Paper(title="DPR", citation_count=42),
            ]
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].citation_count, 42)
        self.assertEqual(merged[0].title, "Dense Passage Retrieval for Open Domain QA")

    def test_merge_can_raise_the_citation_count_derived_scores_were_built_on(
        self,
    ) -> None:
        """Pins the reason callers must re-score after deduping scored papers.

        `merge.py` owns identity, not scoring, and has no clock, so it cannot
        recompute `age_adjusted_citation_score` itself. The surviving record
        can carry a citation count neither input's scores were computed for,
        which is why `candidate_preparation` re-scores the deduped pool (see
        `test_pipeline_rescores_papers_whose_dedupe_raised_their_citations`).
        """

        low = Paper(title="Same Paper", doi="10.1/x", citation_count=10)
        low.age_adjusted_citation_score = 10.0
        high = Paper(title="Same Paper", doi="10.1/x", citation_count=900)

        merged = dedupe_papers([low, high])[0]

        self.assertEqual(merged.citation_count, 900)
        self.assertEqual(merged.age_adjusted_citation_score, 10.0)


if __name__ == "__main__":
    unittest.main()
