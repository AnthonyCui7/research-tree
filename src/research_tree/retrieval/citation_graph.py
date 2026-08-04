"""Citation-graph ranking for workspace candidates.

Keyword search alone ranks papers by how often other people cite them, which
rewards recent celebrity work in adjacent fields. Ranking over the citation graph
instead asks a better question: which papers do the papers in *this* pool treat as
foundational?

The ranker is HITS (Kleinberg). Every paper has two scores:

  authority — how much this paper is cited by good hubs (a foundational paper)
  hub       — how well this paper cites good authorities (a survey or review)

Each hub's vote is divided by its out-degree, so a 500-reference survey casts
1/500-strength votes. Without that normalization a single tightly-knit cluster of
papers that cite each other heavily captures the whole top of the ranking.

Every function here is pure: no network, no disk, no clock. Callers supply the
edges; this module only does arithmetic.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


DEFAULT_HITS_MAX_ITERATIONS = 60
# Compared against *relative* authority movement (see `rank_citation_graph`).
# Measured Aug 2026 by replaying nine saved graphs (407-910 nodes, prompting /
# RAG / sampling): 1e-7 reproduces the iteration counts the old absolute 1e-6
# criterion produced on every one of them (the live 910-node prompting run
# converges at 19 either way), so the ladder is unchanged where it was already
# calibrated and no longer tightens as the graph grows.
DEFAULT_HITS_TOLERANCE = 1e-7


@dataclass(frozen=True)
class CitationGraphRanking:
    authority: dict[str, float]
    hub: dict[str, float]
    in_degree: dict[str, int]
    iterations: int = 0
    converged: bool = False


@dataclass
class CitationGraph:
    """References between papers, restricted to a chosen set of nodes."""

    out_edges: dict[str, set[str]] = field(default_factory=dict)
    nodes: set[str] = field(default_factory=set)

    @property
    def edge_count(self) -> int:
        return sum(len(targets) for targets in self.out_edges.values())


def build_citation_graph(
    references: dict[str, list[str]],
    *,
    allowed_ids: set[str],
) -> CitationGraph:
    """Keep only edges whose source and target are both in `allowed_ids`."""

    out_edges: dict[str, set[str]] = {}
    nodes: set[str] = set()
    for source, targets in references.items():
        if source not in allowed_ids:
            continue
        kept = {target for target in targets if target in allowed_ids and target != source}
        out_edges[source] = kept
        nodes.add(source)
        nodes.update(kept)
    return CitationGraph(out_edges=out_edges, nodes=nodes)


def rank_citation_graph(
    graph: CitationGraph,
    *,
    max_iterations: int = DEFAULT_HITS_MAX_ITERATIONS,
    tolerance: float = DEFAULT_HITS_TOLERANCE,
    normalize_hub_by_out_degree: bool = True,
) -> CitationGraphRanking:
    """Run HITS power iteration until the authority vector stops moving.

    Movement is measured *relative* to the authority vector: the L1 distance
    between successive iterates divided by the vector's own L1 norm. Both
    iterates are L2-normalized, so their entries are O(1/sqrt(n)) and an
    absolute L1 threshold silently demands more precision as the graph grows —
    a 5,000-node pool would have had to settle far further than a 500-node one
    for the same `tolerance`. Dividing by the L1 norm makes the criterion
    dimensionless: it asks for the same fractional precision on every graph.
    """

    in_degree: dict[str, int] = defaultdict(int)
    for targets in graph.out_edges.values():
        for target in targets:
            in_degree[target] += 1

    nodes = graph.nodes
    if not nodes:
        return CitationGraphRanking({}, {}, {}, iterations=0, converged=True)

    authority = dict.fromkeys(nodes, 1.0)
    hub = dict.fromkeys(nodes, 1.0)
    iterations = 0
    converged = False

    for iterations in range(1, max_iterations + 1):
        next_authority = dict.fromkeys(nodes, 0.0)
        for source, targets in graph.out_edges.items():
            if not targets:
                continue
            vote = hub[source] / len(targets) if normalize_hub_by_out_degree else hub[source]
            for target in targets:
                next_authority[target] += vote

        next_hub = dict.fromkeys(nodes, 0.0)
        for source, targets in graph.out_edges.items():
            next_hub[source] = sum(next_authority[target] for target in targets)

        _normalize_in_place(next_authority)
        _normalize_in_place(next_hub)

        movement = sum(abs(next_authority[node] - authority[node]) for node in nodes)
        # An edgeless graph normalizes to the all-zero vector, which is a fixed
        # point: it has converged, there is just nothing to rank.
        scale = sum(abs(value) for value in next_authority.values())
        authority, hub = next_authority, next_hub
        if scale <= 0.0 or movement / scale < tolerance:
            converged = True
            break

    return CitationGraphRanking(
        authority=authority,
        hub=hub,
        in_degree=dict(in_degree),
        iterations=iterations,
        converged=converged,
    )


def select_snowball_candidates(
    references: dict[str, list[str]],
    *,
    known_ids: set[str],
    min_in_degree: int,
    limit: int,
) -> list[str]:
    """Return papers cited often by the pool but missing from it.

    Keyword search cannot find the papers that founded a field, because they
    predate its vocabulary. They are, however, cited by nearly everything the
    search did find, which is exactly what this counts.
    """

    in_degree: dict[str, int] = defaultdict(int)
    for source, targets in references.items():
        for target in set(targets):
            if target != source and target not in known_ids:
                in_degree[target] += 1

    frequent = [
        paper_id for paper_id, degree in in_degree.items() if degree >= min_in_degree
    ]
    frequent.sort(key=lambda paper_id: (-in_degree[paper_id], paper_id))
    return frequent[:limit]


def blended_root_set(
    citation_ranked_ids: list[str],
    age_adjusted_ranked_ids: list[str],
    *,
    size: int,
) -> list[str]:
    """Pick the papers whose reference lists are worth fetching.

    Raw citations alone over-weight old work and the age-adjusted score alone
    over-weights whatever cluster is hot right now, so the two orderings are
    merged round-robin, skipping papers already taken, until `size` unique
    papers are selected.

    The toggle flips on every consumption attempt, including the ones that hit
    a paper the other ordering already contributed, so the two pointers always
    advance to the same depth. The result is therefore the *union of
    equal-depth prefixes* of the two orderings, cut off as soon as it holds
    `size` unique papers: overlap does not cost either ordering its own picks,
    it just pushes the shared depth further down both lists (disjoint
    orderings stop at depth `size`/2, identical ones at depth `size`). That is
    the point — the measured overlap is 55–80%, so a fixed-share split with
    backfill quietly collapses to a nearly pure citation ranking.
    """

    selected: list[str] = []
    seen: set[str] = set()
    citation_index = age_index = 0
    take_citation = True
    while len(selected) < size and (
        citation_index < len(citation_ranked_ids) or age_index < len(age_adjusted_ranked_ids)
    ):
        use_citation = (
            take_citation and citation_index < len(citation_ranked_ids)
        ) or age_index >= len(age_adjusted_ranked_ids)
        if use_citation:
            paper_id = citation_ranked_ids[citation_index]
            citation_index += 1
        else:
            paper_id = age_adjusted_ranked_ids[age_index]
            age_index += 1
        take_citation = not take_citation
        if paper_id in seen:
            continue
        seen.add(paper_id)
        selected.append(paper_id)
    return selected


def _normalize_in_place(scores: dict[str, float]) -> None:
    total = sum(value * value for value in scores.values()) ** 0.5
    if total <= 0:
        return
    for key in scores:
        scores[key] /= total
