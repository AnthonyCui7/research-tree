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


if __name__ == "__main__":
    unittest.main()
