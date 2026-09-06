"""Gather and rank the candidate papers a workspace is built from.

The stage answers one question: of everything published on a topic, which
~55-115 papers should the construction model be allowed to choose from? The
worst case is 115 because `MAX_FLAGGED_PASSTHROUGH` applies per block, surveys
included: 50 authorities + up to 20 flagged riders, 5 surveys + up to 20
flagged riders, and up to 20 frontier picks.

    plan queries   one model call names the field's search vocabulary
    build pool     one boolean bulk query, plus a recency page
    root set       the slice of the pool worth fetching references for
    graph          one batch request returns every root-set bibliography
    snowball       pull in papers the pool cites but keyword search missed
    judge          one model call adjudicates flagged snowballs + the frontier slice
    rank           hub-normalized HITS, then age-cohort normalization
    select         top authorities, surveys by hub score, judged frontier picks

Citation authority lags the field by two to four years — a recent paper cannot
be cited by hubs that predate it, so the newest work never earns authority no
matter how central it is (measured Aug 2026: mean in-pool votes received by
publication year fell from 1.8 for 2021 papers to 0.0 for 2024+). Two
mechanisms close that gap, one per band. Papers with votes but young cohorts
are ranked by authority / (age+1)^0.75 — age-cohort normalization (see
`_ranked_papers`). Papers too new to have votes at all get the frontier picks:
ranked by age-adjusted citation velocity, screened by the judge because
velocity alone surfaces celebrity papers from adjacent fields. Reweighting
formulas cannot replace the frontier picks — any edge weight times zero edges
is still zero (verified offline: on sampling, every time-aware HITS variant
left the top 50 unchanged).

Terminology follows Kleinberg's HITS: the *root set* is the ranked slice whose
bibliographies seed the graph; the *base set* is the expanded node set the
final ranking runs over — here, the pool plus the snowballed papers.

Semantic Scholar allows one request per second across all of its endpoints, so
the cost of this stage is the number of requests, not the amount of data. Every
step above is a batch or a paged bulk query; a cold run is under a dozen requests.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from research_tree.credentials import openai_api_key
from research_tree.artifacts import write_json_file
from research_tree.paths import cache_dir as default_cache_dir, data_root
from research_tree.retrieval.citation_graph import (
    DEFAULT_HITS_MAX_ITERATIONS,
    DEFAULT_HITS_TOLERANCE,
    CitationGraphRanking,
    blended_root_set,
    build_citation_graph,
    rank_citation_graph,
    select_snowball_candidates,
)
from research_tree.retrieval.merge import dedupe_papers, stable_paper_key
from research_tree.retrieval.models import Paper
from research_tree.retrieval.query_plan import (
    SearchQueryPlan,
    judge_flagged_papers,
    matches_topic,
    plan_search_queries,
    query_plan_from_overrides,
)
from research_tree.retrieval.semantic_scholar import (
    SEMANTIC_SCHOLAR_BULK_PAGE_SIZE,
    SEMANTIC_SCHOLAR_KEYED_REQUEST_DELAY_SECONDS,
    SEMANTIC_SCHOLAR_MAX_RETRIES,
    SemanticScholarClient,
    paper_from_semantic_scholar,
    s2_api_key,
)


DEFAULT_TOPIC = "prompting"
SELECTION_MODE = "s2_bulk_hits_authority_workspace_candidate_preparation"
CANDIDATE_POOL_ORDER = "hits_authority_age_normalized_desc"
LLM_CANDIDATE_SCHEMA_VERSION = "llm_candidate_papers.v2"
PAPER_DATABASE_SCHEMA_VERSION = "s2_bulk_deduped_paper_database.v2"


@dataclass(frozen=True)
class PipelineConfig:
    repo_root: Path
    topic: str = DEFAULT_TOPIC
    k: int = 50
    survey_baseline_count: int = 5
    pool_target: int = 5_000
    recency_years: int = 3
    # Blend slots only; `ROOT_SET_SURVEY_RESERVE` surveys are added on top, so
    # the root set actually fetched is up to 270 bibliographies.
    root_set_size: int = 250
    snowball_min_in_degree: int = 3
    snowball_cap: int = 100
    frontier_years: int = 6  # cap on the measured graph-blind band, not a window
    frontier_slots: int = 20
    hits_max_iterations: int = DEFAULT_HITS_MAX_ITERATIONS
    hits_tolerance: float = DEFAULT_HITS_TOLERANCE
    citation_age_exponent: float = 1.25
    authority_age_exponent: float = 0.75
    request_delay_seconds: float = SEMANTIC_SCHOLAR_KEYED_REQUEST_DELAY_SECONDS
    request_timeout_seconds: float = 20.0
    max_academic_retries: int = SEMANTIC_SCHOLAR_MAX_RETRIES
    refresh_cache: bool = False
    verbose: bool = True
    query_overrides: tuple[str, ...] = ()
    # Both default to the data root. Exploratory scripts point them elsewhere
    # so a benchmark run cannot land in the product's artifact directory.
    output_base_dir: Path | None = None
    cache_dir: Path | None = None


def run_workspace_candidate_preparation_pipeline(
    config: PipelineConfig,
) -> dict[str, object]:
    output_base_dir = config.output_base_dir or (data_root() / "candidate_runs")
    output_dir = _next_run_output_dir(output_base_dir)
    cache_dir = config.cache_dir or default_cache_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    as_of = datetime.now(UTC).date()
    semantic_scholar = _semantic_scholar_client(config, cache_dir)

    _write_stage_status(output_dir, "search_query_planning")
    query_plan = (
        query_plan_from_overrides(list(config.query_overrides))
        if config.query_overrides
        else plan_search_queries(config.topic)
    )
    if query_plan.source == "fallback":
        warnings.append(
            "Search query planning fell back to the topic phrase; "
            "the candidate pool may miss alternative vocabulary."
        )
    _write_run_metadata(output_dir, config, query_plan=query_plan)
    _log(config, f"Run directory: {output_dir}")
    _log(config, f"Search vocabulary ({query_plan.source}): {query_plan.phrases}")

    _write_stage_status(output_dir, "s2_bulk_pool_search")
    pool = build_pool(
        semantic_scholar=semantic_scholar,
        query_plan=query_plan,
        pool_target=config.pool_target,
        recency_years=config.recency_years,
        as_of=as_of,
        warnings=warnings,
    )
    _log(config, f"Pool: {len(pool)} deduped papers")
    write_json_file(output_dir / "s2_bulk_pool.json", [paper.to_json() for paper in pool])

    if not pool:
        # The searches succeeded but matched nothing — a candidate set cannot
        # exist, and empty artifacts would let construct build an empty
        # workspace over a good one. Fail with the reason instead.
        raise RuntimeError(
            "Semantic Scholar returned no papers for "
            f"'{query_plan.boolean_query()}'; cannot build a candidate set."
        )

    _score_age_adjusted_citations(pool, as_of=as_of, exponent=config.citation_age_exponent)
    papers_by_id = {paper.semantic_scholar_id: paper for paper in pool if paper.semantic_scholar_id}

    _write_stage_status(output_dir, "citation_graph_construction")
    root_set_ids = select_root_set(
        pool,
        size=config.root_set_size,
        topic_phrases=query_plan.phrases,
    )
    references = semantic_scholar.get_references_batch(root_set_ids, warnings)
    _log(config, f"Fetched reference lists for {len(references)} of {len(root_set_ids)} root-set papers")

    if not references:
        # Request failures already raise inside the client, so an empty result
        # means S2 answered 200 for every root-set paper and hydrated none of
        # the bibliographies. There is no graph to rank; a silently different
        # ranking would be worse than an explainable failure.
        raise RuntimeError(
            "Semantic Scholar returned no reference lists for any of the "
            f"{len(root_set_ids)} root-set papers; cannot rank the citation "
            "graph. Rerun when Semantic Scholar recovers."
        )
    if len(references) * 2 < len(root_set_ids):
        # Partial hydration loss warns and continues (the client already
        # retried the missing ids — a live run shipped fine at 48 of 250
        # missing). Past half, the hub basis is a minority of the root set and
        # the ranking is no longer the thing the artifact says it is, so this
        # crosses from degraded data into a failed run.
        raise RuntimeError(
            "Semantic Scholar hydrated only "
            f"{len(references)} of {len(root_set_ids)} root-set bibliographies; "
            "more than half the citation graph's hub basis is missing. Rerun "
            "when Semantic Scholar recovers."
        )

    _write_stage_status(output_dir, "citation_snowball")
    snowballed = snowball_pool(
        semantic_scholar=semantic_scholar,
        references=references,
        known_ids=set(papers_by_id),
        min_in_degree=config.snowball_min_in_degree,
        limit=config.snowball_cap,
        warnings=warnings,
        topic_phrases=query_plan.phrases,
    )
    _log(config, f"Snowballed {len(snowballed)} papers the pool cites but search missed")
    snowballed_ids = {paper.semantic_scholar_id for paper in snowballed if paper.semantic_scholar_id}
    if snowballed:
        _score_age_adjusted_citations(
            snowballed, as_of=as_of, exponent=config.citation_age_exponent
        )
        pool = dedupe_papers([*pool, *snowballed])
        # Dedupe keeps the larger citation count of each merged pair, so the
        # scores computed above are stale for anything that merged. They drive
        # root-set ordering and frontier ranking, so re-derive them from the
        # counts that actually survived.
        _score_age_adjusted_citations(
            pool, as_of=as_of, exponent=config.citation_age_exponent
        )
        papers_by_id = {
            paper.semantic_scholar_id: paper for paper in pool if paper.semantic_scholar_id
        }
    _write_stage_status(output_dir, "citation_graph_ranking")
    ranking = rank_authorities(
        references,
        known_ids=set(papers_by_id),
        max_iterations=config.hits_max_iterations,
        tolerance=config.hits_tolerance,
        warnings=warnings,
    )
    _log(
        config,
        f"HITS over {len(papers_by_id)} nodes converged={ranking.converged} "
        f"iterations={ranking.iterations}",
    )
    write_json_file(
        output_dir / "citation_graph_edges.json",
        {"root_set_size": len(root_set_ids), "references": references},
    )

    _write_stage_status(output_dir, "candidate_selection")
    ranked_papers = _ranked_papers(
        pool, ranking, as_of=as_of, age_exponent=config.authority_age_exponent
    )
    frontier_cutoff_year = graph_blind_cutoff(
        pool, ranking, as_of=as_of, max_years=config.frontier_years
    )
    frontier = (
        frontier_candidates(pool, as_of=as_of, cutoff_year=frontier_cutoff_year)
        if config.frontier_slots > 0
        else []
    )
    # The authority-ranked core anchors the judge: on an ambiguous topic name,
    # "does this belong with these papers" is stable where "is this about the
    # topic" flips between runs (measured on "Prompting" vs. promptable
    # segmentation).
    context_titles = [
        paper.title
        for paper in ranked_papers
        if not paper.is_survey and not paper.flagged_off_topic
    ][:10]
    # The judge reads abstracts and TLDRs, and neither reaches it from bulk
    # search. Topping up the papers it will actually see costs one request.
    _fill_missing_details(
        semantic_scholar=semantic_scholar,
        papers=[*(paper for paper in pool if paper.flagged_off_topic), *frontier],
        warnings=warnings,
    )
    frontier_belongs = adjudicate_flags(
        topic=config.topic,
        phrases=query_plan.phrases,
        pool=pool,
        warnings=warnings,
        frontier=frontier,
        context_titles=context_titles,
    )
    non_survey, surveys = select_candidates(
        ranked_papers,
        ranking=ranking,
        k=config.k,
        survey_count=config.survey_baseline_count,
    )
    unflagged_non_survey = sum(1 for paper in non_survey if not paper.flagged_off_topic)
    unflagged_surveys = sum(1 for paper in surveys if not paper.flagged_off_topic)
    frontier_picks = select_frontier_picks(
        frontier,
        belongs=frontier_belongs,
        already_selected={
            str(paper.semantic_scholar_id)
            for paper in non_survey
            if paper.semantic_scholar_id
        },
        limit=config.frontier_slots,
    )
    if frontier_picks:
        _log(config, f"Frontier picks (judged recent work): {len(frontier_picks)}")
    non_survey = [*non_survey, *frontier_picks]
    _fill_missing_details(
        semantic_scholar=semantic_scholar,
        papers=[*non_survey, *surveys],
        warnings=warnings,
    )
    if unflagged_non_survey < config.k:
        warnings.append(
            "Fewer non-survey papers than requested after citation-graph ranking: "
            f"{unflagged_non_survey} selected for target {config.k}."
        )
    if unflagged_surveys < config.survey_baseline_count:
        warnings.append(
            "Fewer survey papers than requested after citation-graph ranking: "
            f"{unflagged_surveys} selected for target {config.survey_baseline_count}."
        )

    final_output = _write_artifacts(
        output_base_dir=output_base_dir,
        output_dir=output_dir,
        config=config,
        query_plan=query_plan,
        as_of=as_of,
        warnings=warnings,
        non_survey_papers=non_survey,
        survey_papers=surveys,
        database_papers=ranked_papers,
        ranking=ranking,
        root_set_ids=set(root_set_ids),
        snowballed_ids=snowballed_ids,
        frontier_cutoff_year=frontier_cutoff_year,
    )
    _log(
        config,
        f"Prepared LLM candidate set with {len(non_survey)} non-survey papers "
        f"and {len(surveys)} surveys; no LLM call was made for selection.",
    )
    return final_output


# --- Pipeline steps -------------------------------------------------------
# Each step is independent of the orchestrator above so an agent can later run
# one of them on its own ("search more papers on X", "re-rank the pool").


def build_pool(
    *,
    semantic_scholar: SemanticScholarClient,
    query_plan: SearchQueryPlan,
    pool_target: int,
    recency_years: int,
    as_of: date,
    warnings: list[str],
) -> list[Paper]:
    """Collect the candidate pool: most-cited papers plus recent work."""

    query = query_plan.boolean_query()
    filters = query_plan.filters()
    papers = semantic_scholar.bulk_search(
        query,
        max_papers=pool_target,
        sort="citationCount:desc",
        filters=filters,
        warnings=warnings,
    )
    citation_search_empty = not papers
    # Citation-sorted paging never reaches work published in the last few years,
    # which is exactly where an unsettled field is most active.
    recent_start = _years_before(as_of, max(recency_years, 0))
    papers.extend(
        semantic_scholar.bulk_search(
            query,
            max_papers=SEMANTIC_SCHOLAR_BULK_PAGE_SIZE,
            sort="citationCount:desc",
            filters={**filters, "publicationDateOrYear": f"{recent_start.isoformat()}:"},
            source_tag="s2_bulk_search:recent",
            warnings=warnings,
        )
    )
    if citation_search_empty and papers:
        # A recency-only pool has no citation backbone: HITS then ranks whatever
        # recent work happens to cite, which is mostly out-of-field celebrity
        # papers. The run completes, but its ranking should not be trusted.
        warnings.append(
            "Candidate pool is recency-only: the citation-sorted bulk search "
            "returned no papers (see earlier warning). Citation-graph ranking "
            "quality is severely degraded for this run."
        )
    return dedupe_papers(papers)


# Root-set slots for surveys the blend did not reach. Additive: the blend keeps
# its full `size`, and these ride on top of it, so the root set is `size +
# ROOT_SET_SURVEY_RESERVE` ids at most.
ROOT_SET_SURVEY_RESERVE = 20


def select_root_set(
    pool: list[Paper],
    *,
    size: int,
    survey_reserve: int = ROOT_SET_SURVEY_RESERVE,
    topic_phrases: list[str] | None = None,
) -> list[str]:
    """Choose the papers whose bibliographies define the citation graph.

    The blend of the raw-citation and age-adjusted orderings gets `size` slots.
    Up to `survey_reserve` more go to the highest-cited on-topic surveys the
    blend missed — added to the root set, not taken out of it.

    Only root-set papers have their bibliographies fetched, so only they can
    have a nonzero hub score — and `select_candidates` ranks the survey block
    by hub score. Without the reservation almost every survey scores exactly
    0.0 and the block silently falls back to raw citation count: on the live
    prompting run 770 of 787 surveys had hub 0.0, because only 30 were in the
    root set at all.

    The reserve is gated on `topic_phrases` because an ungated one picks the
    wrong surveys. Replayed offline on the saved Prompting run (5,090 papers,
    787 surveys), reserving slots by citation count alone admitted ColorBrewer,
    a discrete-data econometrics book review, remote sensing for precision
    agriculture, and data stream management: "highest-cited survey" over a pool
    built from a broad boolean OR is not "this field's survey", and an off-topic
    survey is a near-inert hub anyway because `build_citation_graph` drops the
    edges that leave the pool. Requiring the same token match that gates
    snowballed papers swaps those for surveys of in-context learning, LLM
    explainability, and pretrained foundation models.

    Reserving additively rather than out of the blend's tail was the Aug 2026
    correction. Carving the slots out cost real edges — on that run the tail it
    displaced (blend positions ~234-250) carried 197 of the root set's 2,641
    in-pool edges (7.5%), including P-tuning. The surveys are worth having and
    the tail is worth keeping, and the only thing the two were actually
    competing for was a request budget that chunks anyway: the reference batch
    is already split into chunks, so 20 more ids buys both.
    """

    with_ids = [paper for paper in pool if paper.semantic_scholar_id]
    # Explicit secondary keys: without them ties fall back to pool insertion
    # order, i.e. whatever order S2 search happened to return.
    by_citations = sorted(
        with_ids,
        key=lambda paper: (
            -(paper.citation_count or 0),
            -paper.age_adjusted_citation_score,
            paper.title.casefold(),
        ),
    )
    by_age_adjusted = sorted(
        with_ids,
        key=lambda paper: (
            -paper.age_adjusted_citation_score,
            -(paper.citation_count or 0),
            paper.title.casefold(),
        ),
    )
    blended = blended_root_set(
        [str(paper.semantic_scholar_id) for paper in by_citations],
        [str(paper.semantic_scholar_id) for paper in by_age_adjusted],
        size=size,
    )

    selected = list(blended)
    seen = set(selected)
    reserve = max(survey_reserve, 0)
    admitted = 0
    for paper in by_citations:
        if admitted >= reserve:
            break
        paper_id = str(paper.semantic_scholar_id)
        if not paper.is_survey or paper_id in seen:
            continue
        if topic_phrases and not matches_topic(
            f"{paper.title} {paper.abstract}", topic_phrases
        ):
            continue
        seen.add(paper_id)
        selected.append(paper_id)
        admitted += 1
    # A field with fewer on-topic surveys than the reserve simply gets a smaller
    # root set; there is nothing to backfill it with that the blend did not
    # already rank below `size`.
    return selected


def rank_authorities(
    references: dict[str, list[str]],
    *,
    known_ids: set[str],
    max_iterations: int = DEFAULT_HITS_MAX_ITERATIONS,
    tolerance: float = DEFAULT_HITS_TOLERANCE,
    warnings: list[str] | None = None,
) -> CitationGraphRanking:
    graph = build_citation_graph(references, allowed_ids=known_ids)
    ranking = rank_citation_graph(
        graph,
        max_iterations=max_iterations,
        tolerance=tolerance,
        normalize_hub_by_out_degree=True,
    )
    if not ranking.converged and warnings is not None:
        # The artifact still ships — a stopped-early power iteration is close
        # to the fixed point, not garbage — but the ordering it produced is not
        # the converged one the ranking claims, so say so.
        warnings.append(
            "Citation-graph ranking did not converge: HITS stopped at the "
            f"{max_iterations}-iteration cap without reaching tolerance "
            f"{tolerance:g}. The authority ordering is approximate."
        )
    return ranking


def snowball_pool(
    *,
    semantic_scholar: SemanticScholarClient,
    references: dict[str, list[str]],
    known_ids: set[str],
    min_in_degree: int,
    limit: int,
    warnings: list[str],
    topic_phrases: list[str] | None = None,
) -> list[Paper]:
    """Fetch the papers the pool keeps citing but keyword search never returned."""

    missing_ids = select_snowball_candidates(
        references,
        known_ids=known_ids,
        min_in_degree=min_in_degree,
        limit=limit,
    )
    if not missing_ids:
        return []
    details = semantic_scholar.get_paper_details(missing_ids, warnings)
    papers = []
    for paper_id, item in details.items():
        paper = paper_from_semantic_scholar(item)
        paper.found_by.add("snowball:root_set_references")
        if not paper.semantic_scholar_id:
            paper.semantic_scholar_id = paper_id
        # Snowballed papers arrive on citation count alone, which also describes
        # a field's universal infrastructure (Adam, ImageNet, Attention Is All
        # You Need). The flag is advisory: the construction model sees flagged
        # papers and makes the final call, and they never consume a top-k slot.
        paper.flagged_off_topic = bool(topic_phrases) and not matches_topic(
            f"{paper.title} {paper.abstract}", topic_phrases
        )
        papers.append(paper)
    return papers


# Sized at ~6x frontier_slots: velocity ranks adjacent-field celebrity papers
# first, and the judge keeps roughly one slice paper in seven (measured Aug
# 2026 on "Prompting": 8 of 60 kept), so a slice near the slot count starves
# the picks.
FRONTIER_JUDGE_SLICE = 120
# A year is graph-blind when its pool papers average under half an in-pool
# citation each: the ranking cannot order what nothing votes for. Years with
# under 10 pool papers carry no evidence either way and count as blind.
FRONTIER_BLIND_VOTE_THRESHOLD = 0.5
FRONTIER_BLIND_MIN_YEAR_POOL = 10


def _paper_year(paper: Paper) -> int:
    if paper.year:
        return paper.year
    return paper.publication_date.year if paper.publication_date else 0


def graph_blind_cutoff(
    pool: list[Paper],
    ranking: CitationGraphRanking,
    *,
    as_of: date,
    max_years: int,
) -> int:
    """First year of the band the citation graph cannot rank, measured.

    Citations only point backward, so the newest years earn no in-pool votes
    no matter how central their papers are — but how many years that band
    covers depends on the field's citation latency, not on a constant.
    Measured Aug 2026: prompting went blind at 2024, RAG at 2025, sampling at
    2023. Walking back from the present while the mean in-pool vote count
    stays under the threshold finds the band per run; `max_years` caps the
    walk so a degraded graph cannot declare the whole pool blind.

    The threshold is calibrated to this pipeline's two sizes, not absolute.
    Votes can only come from the root-set bibliographies that were actually
    fetched (`root_set_size` 250, plus up to `ROOT_SET_SURVEY_RESERVE` surveys),
    while `counts[year]` counts every pool paper in that year and so scales with
    `pool_target` (5,000). The measured ratio is therefore
    votes-per-250-hubs over papers-per-5,000-pool: raising `pool_target`
    dilutes it and declares more years blind, raising `root_set_size`
    concentrates it and declares fewer. Re-measure the threshold if either
    changes.
    """

    votes: dict[int, int] = defaultdict(int)
    counts: dict[int, int] = defaultdict(int)
    for paper in pool:
        year = _paper_year(paper)
        if year:
            counts[year] += 1
            votes[year] += ranking.in_degree.get(str(paper.semantic_scholar_id), 0)
    cutoff = as_of.year + 1
    for year in range(as_of.year, as_of.year - max(max_years, 1), -1):
        sighted = (
            counts[year] >= FRONTIER_BLIND_MIN_YEAR_POOL
            and votes[year] / counts[year] >= FRONTIER_BLIND_VOTE_THRESHOLD
        )
        if sighted:
            break
        cutoff = year
    return cutoff


def frontier_candidates(
    pool: list[Paper],
    *,
    as_of: date,
    cutoff_year: int,
    limit: int = FRONTIER_JUDGE_SLICE,
) -> list[Paper]:
    """The graph-blind slice of the pool, ranked by age-adjusted velocity.

    These are the papers citation authority structurally cannot rank: hubs
    that predate them can never cite them. Velocity alone is contaminated —
    on "Prompting" the unscreened slice was ~60% adjacent-field celebrity
    papers (Segment Anything, ControlNet, LLaVA) — so every frontier
    candidate goes through the judge before it can take a slot. Structural
    replacements for the judge were measured and rejected: hub score (does
    the paper cite this pool's canon) excludes celebrity papers but ranks
    18-citation papers over Search-o1 and admits general LLM infrastructure,
    and a velocity-hub rank product readmits SAM 2 — topical belonging is a
    semantic judgment the graph cannot make.

    One asymmetry is deliberate: this runs before `adjudicate_flags`, so the
    `flagged_off_topic` filter below still reads the raw token match, and a
    snowballed paper the judge would later rescue can never become a frontier
    pick. It can still lose its flag and take an authority slot; it just
    cannot take a frontier one. Ordering it the other way means judging the
    frontier slice before knowing which flags survive — two judge calls
    instead of one — to widen a band that snowballed papers (old work the
    field keeps citing) barely reach in the first place.
    """

    recent = [
        paper
        for paper in pool
        if not paper.is_survey
        and not paper.flagged_off_topic
        and _paper_year(paper) >= cutoff_year
    ]
    return _age_adjusted_order(recent)[:limit]


def adjudicate_flags(
    *,
    topic: str,
    phrases: list[str],
    pool: list[Paper],
    warnings: list[str],
    frontier: list[Paper] | None = None,
    context_titles: list[str] | None = None,
) -> set[str]:
    """Let a model editor judge topical belonging in one batched call.

    Two kinds of paper need the call. Token-flagged snowballs: the token match
    cannot tell a founding paper that predates the topic's vocabulary (DPR,
    GPT-2) from an optimizer everyone cites (Adam); papers the judge says
    belong lose their flag and count toward the selection targets. Frontier
    candidates: recent papers entering by citation velocity, where the judge
    separates the field's own frontier from adjacent-field celebrity papers.

    Returns the frontier paper ids the judge kept. Without a key, or if the
    call fails, token flags stand and no frontier paper is kept — an
    unscreened velocity slice is worse than none. Both cases warn: losing the
    whole frontier mechanism is a materially different artifact, and without a
    warning the run still reports `retrieval_complete: true`.
    """

    flagged = [paper for paper in pool if paper.flagged_off_topic]
    frontier = frontier or []
    if not flagged and not frontier:
        return set()
    if not openai_api_key():
        warnings.append(
            "Flag adjudication was skipped (no OPENAI_API_KEY); token-match "
            "flags stand and no frontier picks were added for this run."
        )
        return set()
    belongs = judge_flagged_papers(
        topic, phrases, [*flagged, *frontier], context_titles=context_titles
    )
    if belongs is None:
        warnings.append(
            "Flag adjudication was unavailable; token-match flags stand and no "
            "frontier picks were added for this run."
        )
        return set()
    for paper in flagged:
        if str(paper.semantic_scholar_id) in belongs:
            paper.flagged_off_topic = False
    return {
        str(paper.semantic_scholar_id)
        for paper in frontier
        if str(paper.semantic_scholar_id) in belongs
    }


def select_frontier_picks(
    frontier: list[Paper],
    *,
    belongs: set[str],
    already_selected: set[str],
    limit: int,
) -> list[Paper]:
    """Judged frontier papers that earn one of the reserved recent slots."""

    picks: list[Paper] = []
    for paper in frontier:
        if len(picks) >= limit:
            break
        paper_id = str(paper.semantic_scholar_id)
        if paper_id in belongs and paper_id not in already_selected:
            paper.frontier_pick = True
            paper.found_by.add("frontier:age_adjusted_recent")
            picks.append(paper)
    return picks


def select_candidates(
    ranked_papers: list[Paper],
    *,
    ranking: CitationGraphRanking,
    k: int,
    survey_count: int,
) -> tuple[list[Paper], list[Paper]]:
    """Take the top authorities, and the surveys that best orient a reader.

    Surveys are chosen by hub score rather than authority: a good survey is one
    that cites the field's important papers, which is what a hub score measures.

    Papers flagged off-topic keep their ranked position but never consume one of
    the requested slots: the list holds `k` unflagged papers plus the flagged
    papers that rank among them, and the construction model decides their fate.
    The passthrough is capped: measured uncapped on prompting / RAG / sampling,
    58–75 flagged papers rode along (a 108–125 paper hand-off), while every
    wrongly flagged core paper (DPR, FiD, BM25, GPT-2, nucleus sampling, ...)
    sat within the first ~12 flagged positions. Twenty covers them with margin;
    the deeper tail was infrastructure on every topic tested.
    """

    non_survey = _take_keeping_flagged(
        [paper for paper in ranked_papers if not paper.is_survey], count=k
    )
    surveys = _take_keeping_flagged(
        sorted(
            (paper for paper in ranked_papers if paper.is_survey),
            key=lambda paper: (
                -ranking.hub.get(str(paper.semantic_scholar_id), 0.0),
                -(paper.citation_count or 0),
            ),
        ),
        count=survey_count,
    )
    return non_survey, surveys


MAX_FLAGGED_PASSTHROUGH = 20


def _take_keeping_flagged(papers: list[Paper], *, count: int) -> list[Paper]:
    selected: list[Paper] = []
    unflagged = 0
    flagged = 0
    for paper in papers:
        if unflagged >= count:
            break
        if paper.flagged_off_topic:
            if flagged >= MAX_FLAGGED_PASSTHROUGH:
                continue
            flagged += 1
        else:
            unflagged += 1
        selected.append(paper)
    return selected


# --- Internals ------------------------------------------------------------


def _ranked_papers(
    pool: list[Paper],
    ranking: CitationGraphRanking,
    *,
    as_of: date,
    age_exponent: float,
) -> list[Paper]:
    """Order the pool by age-normalized authority.

    Raw HITS authority compounds with age twice over — an old paper has had
    longer to collect citations, and its citers are themselves old high-hub
    papers — so the raw ordering ends years before the present. Dividing by
    (age + 1)^exponent is age-cohort normalization. It is *not* the eigenvector
    analog of the citations/max(age, 0.5)^1.25 velocity score used elsewhere in
    this module: that one floors its denominator at half a year, so a
    brand-new paper divides by 0.5^1.25 ≈ 0.42, a 2.4x boost against the 1.0
    this one gives at age 0, and the two curves stay far apart for the whole
    first year. Different shapes, different exponents, tuned separately for
    different jobs — do not treat a change to one as justified by the other.

    Measured Aug 2026 offline on saved
    prompting / RAG / sampling runs: 0.75 lifts the field's own 2021-24 work
    (Self-Consistency 13→8, ReAct 33→28, Self-RAG 14→7, GraphRAG 31→15) with
    no adjacent-field celebrity paper entering and every founding paper keeping
    its slot; 1.25 was too strong (BM25 and DrQA fell out of RAG's top 50).
    Papers the graph gave no votes stay at zero — only frontier picks can
    surface the newest, still-uncited band.
    """

    def normalized_authority(paper: Paper) -> float:
        authority = ranking.authority.get(str(paper.semantic_scholar_id), 0.0)
        age_years = _candidate_age_years(paper, as_of)
        if age_years is None:
            return 0.0  # same convention as the age-adjusted citation score
        return authority / ((age_years + 1.0) ** age_exponent)

    return sorted(
        pool,
        key=lambda paper: (
            -normalized_authority(paper),
            -ranking.in_degree.get(str(paper.semantic_scholar_id), 0),
            -paper.age_adjusted_citation_score,
            paper.title.casefold(),
        ),
    )


def _age_adjusted_order(pool: list[Paper]) -> list[Paper]:
    return sorted(
        pool,
        key=lambda paper: (
            -paper.age_adjusted_citation_score,
            -(paper.citation_count or 0),
            paper.title.casefold(),
        ),
    )


def _fill_missing_details(
    *,
    semantic_scholar: SemanticScholarClient,
    papers: list[Paper],
    warnings: list[str],
) -> None:
    """Top up papers that bulk search could not describe fully.

    Bulk search omits Semantic Scholar's own TLDRs — only the paper-detail
    endpoint returns them. They are worth a request of their own because both
    prompts that reason about these papers carry the TLDR next to the abstract:
    one is the authors' own one-sentence claim, the other is the evidence for
    it. One batch covers 500 ids, so a whole candidate set costs one request.
    """

    missing = [
        str(paper.semantic_scholar_id)
        for paper in papers
        if paper.semantic_scholar_id and "tldr" not in paper.semantic_scholar_metadata
    ]
    if not missing:
        return
    details = semantic_scholar.get_paper_details(missing, warnings)
    for paper in papers:
        item = details.get(str(paper.semantic_scholar_id))
        if not item:
            continue
        enriched = paper_from_semantic_scholar(item)
        paper.semantic_scholar_metadata = enriched.semantic_scholar_metadata
        paper.abstract = paper.abstract or enriched.abstract
        paper.tldr = paper.tldr or enriched.tldr


def _score_age_adjusted_citations(
    papers: list[Paper],
    *,
    as_of: date,
    exponent: float,
) -> None:
    for paper in papers:
        age_years = _candidate_age_years(paper, as_of)
        citation_count = max(paper.citation_count or 0, 0)
        if age_years is None:
            paper.citations_per_year = 0.0
            paper.age_adjusted_citation_score = 0.0
            continue
        age_denominator = max(age_years, 0.5)
        paper.citations_per_year = citation_count / age_denominator
        paper.age_adjusted_citation_score = citation_count / (age_denominator**exponent)


def _candidate_age_years(paper: Paper, as_of: date) -> float | None:
    publication_date = _candidate_publication_date(paper)
    if publication_date is None:
        return None
    return max((as_of - publication_date).days, 1) / 365.25


def _years_before(as_of: date, years: int) -> date:
    """`years` calendar years before `as_of`.

    `date.replace(year=...)` raises on Feb 29 of a leap year whenever the
    target year is not one, which would have failed the run one day in four.
    """

    try:
        return as_of.replace(year=as_of.year - years)
    except ValueError:
        return as_of.replace(year=as_of.year - years, day=28)


def _candidate_publication_date(paper: Paper) -> date | None:
    if paper.publication_date is not None:
        return paper.publication_date
    if paper.year is None:
        return None
    try:
        return date(paper.year, 7, 1)
    except ValueError:
        return None


def _semantic_scholar_client(
    config: PipelineConfig,
    cache_dir: Path,
) -> SemanticScholarClient:
    return SemanticScholarClient(
        cache_dir=cache_dir / "semantic_scholar",
        api_key=s2_api_key(),
        request_delay_seconds=config.request_delay_seconds,
        refresh_cache=config.refresh_cache,
        max_retries=config.max_academic_retries,
        timeout_seconds=config.request_timeout_seconds,
    )


def _write_artifacts(
    *,
    output_base_dir: Path,
    output_dir: Path,
    config: PipelineConfig,
    query_plan: SearchQueryPlan,
    as_of: date,
    warnings: list[str],
    non_survey_papers: list[Paper],
    survey_papers: list[Paper],
    database_papers: list[Paper],
    ranking: CitationGraphRanking,
    root_set_ids: set[str],
    snowballed_ids: set[str],
    frontier_cutoff_year: int,
) -> dict[str, object]:
    authority_rank_by_key = {
        stable_paper_key(paper): rank
        for rank, paper in enumerate(database_papers, start=1)
    }

    def paper_output(paper: Paper, list_rank: int) -> dict[str, object]:
        return _candidate_paper_output(
            paper,
            list_rank=list_rank,
            as_of=as_of,
            ranking=ranking,
            authority_rank=authority_rank_by_key.get(stable_paper_key(paper)),
            root_set_ids=root_set_ids,
            snowballed_ids=snowballed_ids,
        )

    final_output = {
        "schema_version": LLM_CANDIDATE_SCHEMA_VERSION,
        "topic": config.topic,
        "workspace": workspace_name(config.topic),
        "candidate_set_purpose": (
            "High-recall Research Tree handoff set for later LLM curation into "
            "core papers, branches, paper paths, structured paper cards, and discards."
        ),
        "llm_handoff": {
            "llm_curation_complete": False,
            "recommended_next_step": (
                "Use these candidates to construct a small, scoped workspace with "
                "a root overview, natural branches, paper paths, paper cards, "
                "reading order, comparison tables, and discarded candidates."
            ),
            "candidate_order_note": (
                "non_survey_papers are ordered by citation-graph authority, "
                "age-normalized so recent cohorts compete fairly; "
                "survey_papers are ordered by hub score. Papers with "
                "flagged_off_topic=true do not count toward the targets. "
                "Papers with frontier_pick=true are recent work selected by "
                "citation velocity and judged on-topic, appended after the "
                "authority-ranked block."
            ),
        },
        "non_survey_papers": [
            paper_output(paper, index)
            for index, paper in enumerate(non_survey_papers, start=1)
        ],
        "survey_papers": [
            paper_output(paper, index)
            for index, paper in enumerate(survey_papers, start=1)
        ],
        "non_survey_count": len(non_survey_papers),
        "survey_count": len(survey_papers),
        "non_survey_target": config.k,
        "survey_target": config.survey_baseline_count,
        "selection_mode": SELECTION_MODE,
        "candidate_pool_order": CANDIDATE_POOL_ORDER,
        "search_phrases": query_plan.phrases,
        "search_query": query_plan.boolean_query(),
        "search_plan_source": query_plan.source,
        "field_of_study": query_plan.field_of_study,
        "hits_converged": ranking.converged,
        "hits_iterations": ranking.iterations,
        "root_set_size": len(root_set_ids),
        "snowballed_count": len(snowballed_ids),
        "flagged_off_topic_count": sum(
            1
            for paper in [*non_survey_papers, *survey_papers]
            if paper.flagged_off_topic
        ),
        "frontier_pick_count": sum(
            1 for paper in non_survey_papers if paper.frontier_pick
        ),
        "frontier_cutoff_year": frontier_cutoff_year,
        "llm_curation_complete": False,
        "as_of": as_of.isoformat(),
        "retrieval_complete": not warnings,
        "warning_count": len(warnings),
        "warnings": warnings,
        "run_name": output_dir.name,
        "run_dir": str(output_dir),
        "latest_dir": str(output_dir.parent),
    }
    paper_database_output = {
        "schema_version": PAPER_DATABASE_SCHEMA_VERSION,
        "topic": config.topic,
        "workspace": workspace_name(config.topic),
        "paper_count": len(database_papers),
        "candidate_order": CANDIDATE_POOL_ORDER,
        "paper_metadata_shape": "semantic_scholar_bulk_complete",
        "as_of": as_of.isoformat(),
        "run_name": output_dir.name,
        "run_dir": str(output_dir),
        "warning_count": len(warnings),
        "warnings": warnings,
        "papers": [
            paper_output(paper, index)
            for index, paper in enumerate(database_papers, start=1)
        ],
    }

    write_json_file(output_dir / "s2_bulk_deduped_paper_database.json", paper_database_output)
    write_json_file(output_dir / "pipeline_warnings.json", warnings)
    write_json_file(output_dir / "llm_candidate_papers.json", final_output)
    _write_stage_status(output_dir, "complete", {"selection_mode": SELECTION_MODE})
    _write_latest_candidate_prep_artifacts(output_base_dir, output_dir)
    return final_output


def _candidate_paper_output(
    paper: Paper,
    *,
    list_rank: int,
    as_of: date,
    ranking: CitationGraphRanking,
    authority_rank: int | None,
    root_set_ids: set[str],
    snowballed_ids: set[str],
) -> dict[str, object]:
    paper_id = str(paper.semantic_scholar_id or "")
    return {
        "rank": list_rank,
        "paper_id": paper.display_id(),
        "title": paper.title,
        "abstract": paper.abstract,
        "year": paper.year,
        "publication_date": (
            paper.publication_date.isoformat() if paper.publication_date else None
        ),
        "authors": paper.authors,
        "venue": paper.venue,
        "doi": paper.doi,
        "arxiv_id": paper.arxiv_id,
        "primary_link": paper.url,
        "arxiv_link": _arxiv_link(paper.arxiv_id),
        "s2_link": _semantic_scholar_link(paper),
        "doi_link": _doi_link(paper.doi),
        "citation_count": paper.citation_count,
        "semantic_scholar_metadata": paper.semantic_scholar_metadata,
        "authority_rank": authority_rank,
        "authority_score": _rounded(ranking.authority.get(paper_id, 0.0), 6),
        "hub_score": _rounded(ranking.hub.get(paper_id, 0.0), 6),
        "in_degree": ranking.in_degree.get(paper_id, 0),
        "root_set_member": paper_id in root_set_ids,
        "snowballed": paper_id in snowballed_ids,
        "flagged_off_topic": paper.flagged_off_topic,
        "frontier_pick": paper.frontier_pick,
        "age_years": _rounded(_candidate_age_years(paper, as_of)),
        "age_adjusted_citation_score": _rounded(paper.age_adjusted_citation_score),
        "citations_per_year": _rounded(paper.citations_per_year),
        "is_survey": paper.is_survey,
        "found_by": sorted(paper.found_by),
    }


def ranking_query(topic: str) -> str:
    clean_topic = " ".join(topic.split())
    if not clean_topic:
        raise ValueError("topic cannot be empty.")
    return clean_topic


def workspace_name(topic: str) -> str:
    return ranking_query(topic)


def _arxiv_link(arxiv_id: str | None) -> str | None:
    return f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None


def _semantic_scholar_link(paper: Paper) -> str | None:
    if paper.semantic_scholar_id:
        return f"https://www.semanticscholar.org/paper/{paper.semantic_scholar_id}"
    if paper.url and "semanticscholar.org" in paper.url:
        return paper.url
    return None


def _doi_link(doi: str | None) -> str | None:
    return f"https://doi.org/{doi}" if doi else None


def _rounded(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _next_run_output_dir(output_base_dir: Path) -> Path:
    output_base_dir.mkdir(parents=True, exist_ok=True)
    run_numbers = [
        int(child.name.removeprefix("run"))
        for child in output_base_dir.iterdir()
        if child.is_dir() and child.name.removeprefix("run").isdigit()
    ]
    return output_base_dir / f"run{max(run_numbers, default=0) + 1}"


def _config_dump(config: PipelineConfig) -> dict[str, object]:
    # Every config field lands in run_metadata.json automatically; a
    # hand-listed dict silently dropped fields as they were added.
    dump = asdict(config)
    for recorded_elsewhere in ("repo_root", "topic", "verbose"):
        dump.pop(recorded_elsewhere, None)
    dump["query_overrides"] = list(config.query_overrides)
    # Paths are where this run wrote, not settings that shaped it; keep them
    # readable rather than letting json choke on Path.
    for path_field in ("output_base_dir", "cache_dir"):
        value = dump.get(path_field)
        dump[path_field] = str(value) if value is not None else None
    return dump


def _write_run_metadata(
    output_dir: Path,
    config: PipelineConfig,
    *,
    query_plan: SearchQueryPlan,
) -> None:
    write_json_file(
        output_dir / "run_metadata.json",
        {
            "run_name": output_dir.name,
            "run_dir": str(output_dir),
            "started_at": datetime.now(UTC).isoformat(),
            "workspace": workspace_name(config.topic),
            "topic": config.topic,
            "selection_mode": SELECTION_MODE,
            "search_phrases": query_plan.phrases,
            "search_query": query_plan.boolean_query(),
            "search_plan_source": query_plan.source,
            "field_of_study": query_plan.field_of_study,
            "config": _config_dump(config),
        },
    )


def _write_stage_status(
    output_dir: Path,
    stage: str,
    details: dict[str, object] | None = None,
) -> None:
    write_json_file(
        output_dir / "stage_status.json",
        {
            "stage": stage,
            "updated_at": datetime.now(UTC).isoformat(),
            "details": details or {},
        },
    )


def _write_latest_candidate_prep_artifacts(
    output_base_dir: Path,
    output_dir: Path,
) -> None:
    for file_name in [
        "run_metadata.json",
        "llm_candidate_papers.json",
        "s2_bulk_deduped_paper_database.json",
        "pipeline_warnings.json",
    ]:
        source_path = output_dir / file_name
        if not source_path.is_file():
            continue
        write_json_file(
            output_base_dir / file_name,
            json.loads(source_path.read_text(encoding="utf-8")),
            archive_existing=True,
            run_label=output_dir.name,
        )


def _log(config: PipelineConfig | None, message: str) -> None:
    if config is not None and not config.verbose:
        return
    print(f"[research-tree] {message}", flush=True)
