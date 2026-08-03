"""Gather and rank the candidate papers a workspace is built from.

The stage answers one question: of everything published on a topic, which ~55
papers should the construction model be allowed to choose from?

    plan queries   one model call names the field's search vocabulary
    build pool     one boolean bulk query, plus a recency page
    root set       the slice of the pool worth fetching references for
    graph          one batch request returns every root-set bibliography
    rank           hub-normalized HITS over the in-pool citation graph
    snowball       pull in papers the pool cites but keyword search missed
    re-rank        HITS again over the base set (pool + snowballed papers)
    select         top authorities, plus surveys by hub score

Terminology follows Kleinberg's HITS: the *root set* is the ranked slice whose
bibliographies seed the graph; the *base set* is the expanded node set the
final ranking runs over — here, the pool plus the snowballed papers.

Semantic Scholar allows one request per second across all of its endpoints, so
the cost of this stage is the number of requests, not the amount of data. Every
step above is a batch or a paged bulk query; a cold run is under a dozen requests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from research_tree.artifacts import write_json_file
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
    matches_topic,
    plan_search_queries,
    query_plan_from_overrides,
)
from research_tree.retrieval.semantic_scholar import (
    SEMANTIC_SCHOLAR_BULK_PAGE_SIZE,
    SEMANTIC_SCHOLAR_KEYED_REQUEST_DELAY_SECONDS,
    SemanticScholarClient,
    paper_from_semantic_scholar,
    s2_api_key,
)


DEFAULT_TOPIC = "prompting"
SELECTION_MODE = "s2_bulk_hits_authority_workspace_candidate_preparation"
CANDIDATE_POOL_ORDER = "hits_authority_desc"
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
    root_set_size: int = 250
    snowball_min_in_degree: int = 3
    snowball_cap: int = 100
    hits_max_iterations: int = DEFAULT_HITS_MAX_ITERATIONS
    hits_tolerance: float = DEFAULT_HITS_TOLERANCE
    citation_age_exponent: float = 1.25
    request_delay_seconds: float = SEMANTIC_SCHOLAR_KEYED_REQUEST_DELAY_SECONDS
    request_timeout_seconds: float = 20.0
    max_academic_retries: int = 2
    refresh_cache: bool = False
    verbose: bool = True
    query_overrides: tuple[str, ...] = ()


def run_workspace_candidate_preparation_pipeline(
    config: PipelineConfig,
) -> dict[str, object]:
    output_base_dir = (
        config.repo_root / "experiments" / "output" / "workspace_candidate_preparation"
    )
    output_dir = _next_run_output_dir(output_base_dir)
    cache_dir = config.repo_root / "experiments" / "cache"
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
        warnings.append(
            "No Semantic Scholar candidates were retrieved; "
            "wrote empty workspace candidate artifacts."
        )
        return _write_artifacts(
            output_base_dir=output_base_dir,
            output_dir=output_dir,
            config=config,
            query_plan=query_plan,
            as_of=as_of,
            warnings=warnings,
            non_survey_papers=[],
            survey_papers=[],
            database_papers=[],
            ranking=CitationGraphRanking({}, {}, {}),
            root_set_ids=set(),
            snowballed_ids=set(),
        )

    _score_age_adjusted_citations(pool, as_of=as_of, exponent=config.citation_age_exponent)
    papers_by_id = {paper.semantic_scholar_id: paper for paper in pool if paper.semantic_scholar_id}

    _write_stage_status(output_dir, "citation_graph_construction")
    root_set_ids = select_root_set(pool, size=config.root_set_size)
    references = semantic_scholar.get_references_batch(root_set_ids, warnings)
    _log(config, f"Fetched reference lists for {len(references)} of {len(root_set_ids)} root-set papers")

    if not references:
        warnings.append(
            "Reference fetching returned nothing, so candidates fall back to the "
            "age-adjusted citation order. Ranking quality is reduced for this run."
        )
        ranked = _age_adjusted_order(pool)
        return _write_artifacts(
            output_base_dir=output_base_dir,
            output_dir=output_dir,
            config=config,
            query_plan=query_plan,
            as_of=as_of,
            warnings=warnings,
            non_survey_papers=[p for p in ranked if not p.is_survey][: config.k],
            survey_papers=[p for p in ranked if p.is_survey][: config.survey_baseline_count],
            database_papers=ranked,
            ranking=CitationGraphRanking({}, {}, {}),
            root_set_ids=set(root_set_ids),
            snowballed_ids=set(),
        )

    _write_stage_status(output_dir, "citation_graph_ranking")
    ranking = rank_authorities(
        references,
        known_ids=set(papers_by_id),
        max_iterations=config.hits_max_iterations,
        tolerance=config.hits_tolerance,
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
        papers_by_id = {
            paper.semantic_scholar_id: paper for paper in pool if paper.semantic_scholar_id
        }
        ranking = rank_authorities(
            references,
            known_ids=set(papers_by_id),
            max_iterations=config.hits_max_iterations,
            tolerance=config.hits_tolerance,
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
    ranked_papers = _ranked_papers(pool, ranking)
    non_survey, surveys = select_candidates(
        ranked_papers,
        ranking=ranking,
        k=config.k,
        survey_count=config.survey_baseline_count,
    )
    _fill_missing_details(
        semantic_scholar=semantic_scholar,
        papers=[*non_survey, *surveys],
        warnings=warnings,
    )
    unflagged_non_survey = sum(1 for paper in non_survey if not paper.flagged_off_topic)
    unflagged_surveys = sum(1 for paper in surveys if not paper.flagged_off_topic)
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
    recent_start = as_of.replace(year=as_of.year - max(recency_years, 0))
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


def select_root_set(pool: list[Paper], *, size: int) -> list[str]:
    """Choose the papers whose bibliographies define the citation graph."""

    with_ids = [paper for paper in pool if paper.semantic_scholar_id]
    by_citations = sorted(with_ids, key=lambda p: -(p.citation_count or 0))
    by_age_adjusted = sorted(with_ids, key=lambda p: -p.age_adjusted_citation_score)
    return blended_root_set(
        [str(paper.semantic_scholar_id) for paper in by_citations],
        [str(paper.semantic_scholar_id) for paper in by_age_adjusted],
        size=size,
    )


def rank_authorities(
    references: dict[str, list[str]],
    *,
    known_ids: set[str],
    max_iterations: int = DEFAULT_HITS_MAX_ITERATIONS,
    tolerance: float = DEFAULT_HITS_TOLERANCE,
) -> CitationGraphRanking:
    graph = build_citation_graph(references, allowed_ids=known_ids)
    return rank_citation_graph(
        graph,
        max_iterations=max_iterations,
        tolerance=tolerance,
        normalize_hub_by_out_degree=True,
    )


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
    A mistakenly flagged founding paper is by definition a top authority, so the
    passthrough is capped — on an ambiguous topic nearly every snowballed paper
    fails the phrase match, and an uncapped list would dwarf the real candidates.
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


MAX_FLAGGED_PASSTHROUGH = 15


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


def _ranked_papers(pool: list[Paper], ranking: CitationGraphRanking) -> list[Paper]:
    return sorted(
        pool,
        key=lambda paper: (
            -ranking.authority.get(str(paper.semantic_scholar_id), 0.0),
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
    """Top up selected papers that bulk search could not describe fully.

    Bulk search omits Semantic Scholar's own TLDRs. Fetching them here, for the
    selected papers only, means the hydrate stage rarely has to call out again.
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
                "non_survey_papers are ordered by citation-graph authority; "
                "survey_papers are ordered by hub score. Papers with "
                "flagged_off_topic=true do not count toward the targets."
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
            "config": {
                "k": config.k,
                "survey_baseline_count": config.survey_baseline_count,
                "pool_target": config.pool_target,
                "recency_years": config.recency_years,
                "root_set_size": config.root_set_size,
                "snowball_min_in_degree": config.snowball_min_in_degree,
                "snowball_cap": config.snowball_cap,
                "hits_max_iterations": config.hits_max_iterations,
                "hits_tolerance": config.hits_tolerance,
                "citation_age_exponent": config.citation_age_exponent,
                "request_delay_seconds": config.request_delay_seconds,
                "request_timeout_seconds": config.request_timeout_seconds,
                "max_academic_retries": config.max_academic_retries,
                "refresh_cache": config.refresh_cache,
            },
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
