from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_tree.retrieval.text import (
    looks_like_survey,
    normalize_arxiv_id,
    normalize_doi,
    normalize_title,
    titles_are_near_duplicates,
)


class TextHelpersTest(unittest.TestCase):
    def test_normalize_title(self) -> None:
        self.assertEqual(
            normalize_title("Chain-of-Thought Prompting: Elicits Reasoning!"),
            "chain of thought prompting elicits reasoning",
        )

    def test_normalize_identifiers(self) -> None:
        self.assertEqual(normalize_doi("https://doi.org/10.123/ABC"), "10.123/abc")
        self.assertEqual(normalize_arxiv_id("arXiv:2201.11903"), "2201.11903")

    def test_survey_detection(self) -> None:
        self.assertTrue(looks_like_survey("A Survey of Prompting Techniques"))
        self.assertTrue(looks_like_survey("Prompting Methods", ["Review"]))
        self.assertFalse(looks_like_survey("Chain-of-Thought Prompting Elicits Reasoning"))

    def test_near_duplicate_titles(self) -> None:
        self.assertTrue(
            titles_are_near_duplicates(
                "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models",
                "Chain of Thought Prompting Elicits Reasoning in Large Language Models",
            )
        )


if __name__ == "__main__":
    unittest.main()
