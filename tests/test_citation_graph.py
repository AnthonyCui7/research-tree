from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_tree.retrieval.citation_graph import (
    blended_root_set,
    build_citation_graph,
    rank_citation_graph,
    select_snowball_candidates,
)


class CitationGraphTest(unittest.TestCase):
    def test_graph_keeps_only_in_pool_edges(self) -> None:
        graph = build_citation_graph(
            {"a": ["b", "outside", "a"], "b": ["c"]},
            allowed_ids={"a", "b", "c"},
        )

        self.assertEqual(graph.out_edges["a"], {"b"})
        self.assertEqual(graph.nodes, {"a", "b", "c"})
        self.assertEqual(graph.edge_count, 2)

    def test_authority_rewards_papers_cited_by_the_pool(self) -> None:
        graph = build_citation_graph(
            {
                "survey": ["foundational", "minor"],
                "paper-1": ["foundational"],
                "paper-2": ["foundational"],
                "paper-3": ["minor"],
            },
            allowed_ids={"survey", "paper-1", "paper-2", "paper-3", "foundational", "minor"},
        )

        ranking = rank_citation_graph(graph)

        self.assertTrue(ranking.converged)
        self.assertGreater(ranking.authority["foundational"], ranking.authority["minor"])
        self.assertEqual(ranking.in_degree["foundational"], 3)

    def test_hub_normalization_resists_a_tightly_knit_cluster(self) -> None:
        """A clique that cites itself should not outrank a widely cited paper.

        This is the tightly-knit-community effect: without dividing each hub's
        vote by its out-degree, a dense cluster of mutually citing papers
        accumulates enough score to bury the paper the rest of the field cites.
        """

        cluster = [f"clique-{index}" for index in range(6)]
        references = {member: [other for other in cluster if other != member] for member in cluster}
        references.update({f"outsider-{index}": ["classic"] for index in range(5)})
        allowed = set(cluster) | {"classic"} | {f"outsider-{index}" for index in range(5)}
        graph = build_citation_graph(references, allowed_ids=allowed)

        normalized = rank_citation_graph(graph, normalize_hub_by_out_degree=True)
        vanilla = rank_citation_graph(graph, normalize_hub_by_out_degree=False)

        self.assertGreater(normalized.authority["classic"], normalized.authority["clique-0"])
        self.assertLess(vanilla.authority["classic"], vanilla.authority["clique-0"])

    def test_convergence_does_not_tighten_as_the_graph_grows(self) -> None:
        """The same graph shape must converge in the same number of iterations.

        Both HITS vectors are L2-normalized, so their entries shrink like
        1/sqrt(n) and an absolute L1 movement threshold silently demands more
        precision from a bigger graph. Twenty disjoint copies of one motif are
        that motif: the authority vector is the single-copy vector scaled by
        1/sqrt(20), so the relative criterion must not notice the difference.
        (Under the old absolute 1e-6 test these two took 13 and 14 iterations.)
        """

        def motif(prefix: str) -> dict[str, list[str]]:
            return {
                f"{prefix}-survey": [f"{prefix}-classic", f"{prefix}-minor", f"{prefix}-recent"],
                f"{prefix}-paper-1": [f"{prefix}-classic", f"{prefix}-minor"],
                f"{prefix}-paper-2": [f"{prefix}-classic"],
                f"{prefix}-recent": [f"{prefix}-paper-1", f"{prefix}-paper-2"],
                f"{prefix}-minor": [f"{prefix}-classic"],
            }

        def graph_of(references: dict[str, list[str]]):
            allowed = set(references) | {t for targets in references.values() for t in targets}
            return build_citation_graph(references, allowed_ids=allowed)

        one = motif("copy-0")
        twenty: dict[str, list[str]] = {}
        for index in range(20):
            twenty.update(motif(f"copy-{index}"))

        small = rank_citation_graph(graph_of(one))
        large = rank_citation_graph(graph_of(twenty))

        self.assertTrue(small.converged)
        self.assertTrue(large.converged)
        self.assertGreater(small.iterations, 1)
        self.assertEqual(small.iterations, large.iterations)

    def test_ranking_an_empty_graph_is_not_an_error(self) -> None:
        ranking = rank_citation_graph(build_citation_graph({}, allowed_ids=set()))

        self.assertEqual(ranking.authority, {})
        self.assertTrue(ranking.converged)

    def test_snowball_selects_frequently_cited_papers_outside_the_pool(self) -> None:
        references = {
            "a": ["missing-classic", "rare"],
            "b": ["missing-classic"],
            "c": ["missing-classic", "known"],
        }

        selected = select_snowball_candidates(
            references,
            known_ids={"a", "b", "c", "known"},
            min_in_degree=2,
            limit=10,
        )

        self.assertEqual(selected, ["missing-classic"])

    def test_snowball_respects_its_limit(self) -> None:
        references = {
            f"source-{index}": ["first", "second", "third"] for index in range(4)
        }

        selected = select_snowball_candidates(
            references,
            known_ids=set(references),
            min_in_degree=2,
            limit=2,
        )

        self.assertEqual(len(selected), 2)

    def test_root_set_alternates_both_orderings_without_duplicates(self) -> None:
        by_citations = [f"cited-{index}" for index in range(10)]
        by_age_adjusted = [f"recent-{index}" for index in range(10)]

        selected = blended_root_set(by_citations, by_age_adjusted, size=10)

        self.assertEqual(len(selected), 10)
        self.assertEqual(len(set(selected)), 10)
        # Disjoint orderings each contribute exactly half, interleaved.
        self.assertEqual(selected[:4], ["cited-0", "recent-0", "cited-1", "recent-1"])
        self.assertEqual(sum(1 for p in selected if p.startswith("cited")), 5)
        self.assertEqual(sum(1 for p in selected if p.startswith("recent")), 5)

    def test_root_set_fills_to_size_when_the_orderings_overlap(self) -> None:
        shared = [f"paper-{index}" for index in range(10)]

        selected = blended_root_set(shared, shared, size=10)

        self.assertEqual(selected, shared)

    def test_root_set_is_the_union_of_equal_depth_prefixes(self) -> None:
        """Overlap deepens both prefixes rather than costing either its share.

        The toggle flips on every consumption attempt, including the ones that
        land on a paper the other ordering already contributed, so the two
        pointers always sit at the same depth. The result is exactly the union
        of the two equal-depth prefixes, cut off at `size`.
        """

        by_citations = ["a", "b", "c", "d", "e", "f"]
        by_age_adjusted = ["a", "b", "x", "y", "z", "w"]

        selected = blended_root_set(by_citations, by_age_adjusted, size=6)

        self.assertEqual(selected, ["a", "b", "c", "x", "d", "y"])
        # Two shared papers, so the pointers reach depth 4 — not the depth 3 a
        # fixed half-share each would have stopped at.
        depth = 4
        self.assertEqual(
            set(selected),
            set(by_citations[:depth]) | set(by_age_adjusted[:depth]),
        )
        self.assertLess(
            len(set(by_citations[: depth - 1]) | set(by_age_adjusted[: depth - 1])),
            6,
        )


if __name__ == "__main__":
    unittest.main()
