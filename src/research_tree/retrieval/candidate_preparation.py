from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from research_tree.artifacts import write_json_file
from research_tree.retrieval.merge import dedupe_papers, stable_paper_key
from research_tree.retrieval.models import Paper
from research_tree.retrieval.reranking import LocalCrossEncoderReranker
from research_tree.retrieval.semantic_scholar import SemanticScholarClient


DEFAULT_TOPIC = "prompting"


@dataclass(frozen=True)
class PipelineConfig:
    repo_root: Path
    topic: str = DEFAULT_TOPIC
    k: int = 50
    s2_bulk_citation_multiplier: int = 50
    survey_baseline_count: int = 5
    request_delay_seconds: float = 1.0
    request_timeout_seconds: float = 20.0
    max_academic_retries: int = 2
    refresh_cache: bool = False
    cross_encoder_model: str = "cross-encoder/ms-marco-MiniLM-L6-v2"
    citation_age_exponent: float = 1.25
    verbose: bool = True


def run_workspace_candidate_preparation_pipeline(
    config: PipelineConfig,
) -> dict[str, object]:
    output_base_dir = (
        config.repo_root
        / "experiments"
        / "output"
        / "workspace_candidate_preparation"
    )
    output_dir = _next_run_output_dir(output_base_dir)
    cache_dir = config.repo_root / "experiments" / "cache"
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    cross_encoder_query = ranking_query(config.topic)
    queries = citation_search_query_variants(config.topic)
    warnings: list[str] = []
    semantic_scholar = _semantic_scholar_client(config, cache_dir)
    bulk_citation_target_per_query = config.k * config.s2_bulk_citation_multiplier
    as_of = datetime.now(UTC).date()
    selection_mode = "s2_bulk_citation_age_adjusted_workspace_candidate_preparation"
    _write_run_metadata(
        output_dir,
        config,
        cross_encoder_query=cross_encoder_query,
        queries=queries,
        selection_mode=selection_mode,
        extra_metadata={
            "candidate_cutoff_formula": _citation_age_scoring_formula(
                config.citation_age_exponent
            ),
            "non_survey_target": config.k,
            "survey_target": config.survey_baseline_count,
            "s2_bulk_citation_target_per_query": bulk_citation_target_per_query,
            "citation_age_exponent": config.citation_age_exponent,
            "cross_encoder_relevance_usage": "metadata_only",
            "llm_curation_complete": False,
        },
    )
    _log(config, f"Run directory: {output_dir}")
    _log(config, f"Cross-encoder metadata query: {cross_encoder_query}")
    _log(
        config,
        "Retrieving S2 bulk citation-sorted candidates "
        f"({bulk_citation_target_per_query} per query) for workspace candidate prep...",
    )

    _write_stage_status(output_dir, "s2_bulk_citation_search")
    s2_bulk_candidates = _retrieve_semantic_scholar_bulk_citation_candidates(
        semantic_scholar=semantic_scholar,
        queries=queries,
        per_query_count=bulk_citation_target_per_query,
        warnings=warnings,
        config=config,
    )
    raw_citation_rank_by_key = _raw_citation_rank_by_key(s2_bulk_candidates)
    raw_candidates = [
        row["paper"] for row in s2_bulk_candidates if isinstance(row["paper"], Paper)
    ]
    _write_json(
        output_dir / "s2_bulk_citation_candidates.json",
        [
            _s2_bulk_citation_candidate_output(
                query=str(row["query"]),
                rank=int(row["rank"]),
                paper=row["paper"],
            )
            for row in s2_bulk_candidates
            if isinstance(row["paper"], Paper)
        ],
    )
    if not raw_candidates:
        warnings.append(
            "No Semantic Scholar bulk citation candidates were retrieved; "
            "wrote empty workspace candidate artifacts."
        )
        final_output = _workspace_candidate_final_output(
            non_survey_papers=[],
            survey_papers=[],
            warnings=warnings,
            output_dir=output_dir,
            config=config,
            cross_encoder_query=cross_encoder_query,
            selection_mode=selection_mode,
            as_of=as_of,
            bulk_citation_target_per_query=bulk_citation_target_per_query,
            raw_citation_rank_by_key={},
            age_adjusted_rank_by_key={},
            cross_encoder_rank_by_key={},
        )
        paper_database_output = _s2_bulk_deduped_paper_database_output(
            papers=[],
            output_dir=output_dir,
            config=config,
            as_of=as_of,
            raw_citation_rank_by_key={},
            age_adjusted_rank_by_key={},
            cross_encoder_rank_by_key={},
            warnings=warnings,
        )
        _write_json(output_dir / "s2_bulk_citation_deduped_candidates.json", [])
        _write_json(output_dir / "age_adjusted_ranked_candidates.json", [])
        _write_json(output_dir / "s2_bulk_deduped_paper_database.json", paper_database_output)
        _write_json(output_dir / "llm_candidate_papers.json", final_output)
        _write_json(output_dir / "pipeline_warnings.json", warnings)
        _write_latest_candidate_prep_artifacts(output_base_dir, output_dir)
        return final_output

    _write_stage_status(output_dir, "s2_bulk_citation_dedupe")
    deduped_candidates = dedupe_papers(raw_candidates)
    _write_json(
        output_dir / "s2_bulk_citation_deduped_candidates.json",
        [paper.to_json() for paper in deduped_candidates],
    )

    _write_stage_status(output_dir, "age_adjusted_candidate_ranking")
    age_adjusted_ranked_candidates = _rank_by_age_adjusted_citations(
        deduped_candidates,
        as_of=as_of,
        exponent=config.citation_age_exponent,
    )
    age_adjusted_rank_by_key = _rank_by_key(age_adjusted_ranked_candidates)
    _write_json(
        output_dir / "age_adjusted_ranked_candidates.json",
        [
            _age_adjusted_candidate_output(
                paper,
                rank=index,
                as_of=as_of,
                raw_citation_rank=raw_citation_rank_by_key.get(stable_paper_key(paper)),
                age_adjusted_rank=index,
            )
            for index, paper in enumerate(age_adjusted_ranked_candidates, start=1)
        ],
    )

    selected_non_survey = [
        paper for paper in age_adjusted_ranked_candidates if not paper.is_survey
    ][: config.k]
    selected_surveys = [
        paper for paper in age_adjusted_ranked_candidates if paper.is_survey
    ][: config.survey_baseline_count]
    if len(selected_non_survey) < config.k:
        warnings.append(
            "Fewer non-survey papers than requested after age-adjusted ranking: "
            f"{len(selected_non_survey)} selected for target {config.k}."
        )
    if len(selected_surveys) < config.survey_baseline_count:
        warnings.append(
            "Fewer survey papers than requested after age-adjusted ranking: "
            f"{len(selected_surveys)} selected for target {config.survey_baseline_count}."
        )

    _write_stage_status(output_dir, "cross_encoder_metadata_scoring")
    cross_encoder = LocalCrossEncoderReranker(config.cross_encoder_model)
    llm_candidate_papers = [*selected_non_survey, *selected_surveys]
    cross_encoder.rerank(cross_encoder_query, llm_candidate_papers)
    cross_encoder_rank_by_key = _rank_by_key(_rank_by_cross_encoder(llm_candidate_papers))

    final_output = _workspace_candidate_final_output(
        non_survey_papers=selected_non_survey,
        survey_papers=selected_surveys,
        warnings=warnings,
        output_dir=output_dir,
        config=config,
        cross_encoder_query=cross_encoder_query,
        selection_mode=selection_mode,
        as_of=as_of,
        bulk_citation_target_per_query=bulk_citation_target_per_query,
        raw_citation_rank_by_key=raw_citation_rank_by_key,
        age_adjusted_rank_by_key=age_adjusted_rank_by_key,
        cross_encoder_rank_by_key=cross_encoder_rank_by_key,
    )
    paper_database_output = _s2_bulk_deduped_paper_database_output(
        papers=age_adjusted_ranked_candidates,
        output_dir=output_dir,
        config=config,
        as_of=as_of,
        raw_citation_rank_by_key=raw_citation_rank_by_key,
        age_adjusted_rank_by_key=age_adjusted_rank_by_key,
        cross_encoder_rank_by_key=cross_encoder_rank_by_key,
        warnings=warnings,
    )
    _write_json(output_dir / "s2_bulk_deduped_paper_database.json", paper_database_output)
    _write_json(output_dir / "pipeline_warnings.json", warnings)
    _write_json(output_dir / "llm_candidate_papers.json", final_output)
    _write_stage_status(output_dir, "complete", {"selection_mode": selection_mode})
    _write_latest_candidate_prep_artifacts(output_base_dir, output_dir)
    _log(
        config,
        "Prepared LLM candidate set with "
        f"{len(selected_non_survey)} non-survey papers and "
        f"{len(selected_surveys)} surveys; no LLM call was made.",
    )
    return final_output


def citation_search_query_variants(topic: str) -> list[str]:
    query_topic = biencoder_query(topic)
    return [
        query_topic,
        f"{query_topic} paper",
        f"{query_topic} method",
        f"{query_topic} origin",
        f"{query_topic} technique",
        f"{query_topic} survey",
    ]


def ranking_query(topic: str) -> str:
    clean_topic = " ".join(topic.split())
    if not clean_topic:
        raise ValueError("topic cannot be empty.")
    return clean_topic


def biencoder_query(topic: str) -> str:
    return f"Large Language Model {ranking_query(topic)}"


def workspace_name(topic: str) -> str:
    return ranking_query(topic)


def _retrieve_semantic_scholar_bulk_citation_candidates(
    semantic_scholar: SemanticScholarClient,
    queries: list[str],
    per_query_count: int,
    warnings: list[str],
    config: PipelineConfig | None = None,
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for query in queries:
        if config:
            _log(config, f"S2 bulk citation search query: {query}")
        papers = semantic_scholar.search_by_citation_count(
            query=query,
            target_count=per_query_count,
            warnings=warnings,
        )
        for rank, paper in enumerate(papers, start=1):
            candidates.append({"query": query, "rank": rank, "paper": paper})
    return candidates


def _rank_by_age_adjusted_citations(
    papers: list[Paper],
    as_of: date,
    exponent: float,
) -> list[Paper]:
    for paper in papers:
        _set_age_adjusted_citation_scores(
            paper,
            as_of=as_of,
            exponent=exponent,
        )

    return sorted(
        papers,
        key=lambda paper: (
            -paper.final_score,
            -(paper.citation_count or 0),
            -(paper.year or 0),
            paper.title.casefold(),
        ),
    )


def _set_age_adjusted_citation_scores(
    paper: Paper,
    as_of: date,
    exponent: float,
) -> None:
    age_years = _candidate_age_years(paper, as_of)
    citation_count = max(paper.citation_count or 0, 0)
    if age_years is None:
        paper.citations_per_year = 0.0
        paper.age_adjusted_citation_score = 0.0
        paper.final_score = 0.0
        return

    age_denominator = max(age_years, 0.5)
    paper.citations_per_year = citation_count / age_denominator
    paper.age_adjusted_citation_score = citation_count / (age_denominator**exponent)
    paper.final_score = paper.age_adjusted_citation_score


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


def _citation_age_scoring_formula(exponent: float) -> str:
    return f"citation_count / max(age_years, 0.5)^{exponent:g}"


def _semantic_scholar_client(
    config: PipelineConfig,
    cache_dir: Path,
) -> SemanticScholarClient:
    return SemanticScholarClient(
        cache_dir=cache_dir / "semantic_scholar",
        api_key=os.environ.get("S2_API_KEY") or os.environ.get("SEMANTIC_SCHOLAR_API_KEY"),
        request_delay_seconds=config.request_delay_seconds,
        refresh_cache=config.refresh_cache,
        max_retries=config.max_academic_retries,
        timeout_seconds=config.request_timeout_seconds,
    )


def _workspace_candidate_final_output(
    non_survey_papers: list[Paper],
    survey_papers: list[Paper],
    warnings: list[str],
    output_dir: Path,
    config: PipelineConfig,
    cross_encoder_query: str,
    selection_mode: str,
    as_of: date,
    bulk_citation_target_per_query: int,
    raw_citation_rank_by_key: dict[str, int],
    age_adjusted_rank_by_key: dict[str, int],
    cross_encoder_rank_by_key: dict[str, int],
) -> dict[str, object]:
    return {
        "schema_version": "llm_candidate_papers.v1",
        "topic": config.topic,
        "workspace": workspace_name(config.topic),
        "candidate_set_purpose": (
            "High-recall Research Tree handoff set for later LLM curation into "
            "core papers, branches, paper paths, structured paper cards, and discards."
        ),
        "llm_handoff": {
            "llm_curation_complete": False,
            "cross_encoder_relevance_usage": "metadata_only",
            "recommended_next_step": (
                "Use these candidates to construct a small, scoped workspace with "
                "a root overview, natural branches, paper paths, paper cards, "
                "reading order, comparison tables, and discarded candidates."
            ),
            "candidate_order_note": (
                "non_survey_papers and survey_papers are ordered by "
                "age_adjusted_rank; cross_encoder_rank is metadata, not the "
                "selection order for this file."
            ),
        },
        "non_survey_papers": [
            _candidate_paper_output(
                paper,
                list_rank=index,
                as_of=as_of,
                raw_citation_rank=raw_citation_rank_by_key.get(stable_paper_key(paper)),
                age_adjusted_rank=age_adjusted_rank_by_key.get(stable_paper_key(paper)),
                cross_encoder_rank=cross_encoder_rank_by_key.get(stable_paper_key(paper)),
            )
            for index, paper in enumerate(non_survey_papers, start=1)
        ],
        "survey_papers": [
            _candidate_paper_output(
                paper,
                list_rank=index,
                as_of=as_of,
                raw_citation_rank=raw_citation_rank_by_key.get(stable_paper_key(paper)),
                age_adjusted_rank=age_adjusted_rank_by_key.get(stable_paper_key(paper)),
                cross_encoder_rank=cross_encoder_rank_by_key.get(stable_paper_key(paper)),
            )
            for index, paper in enumerate(survey_papers, start=1)
        ],
        "non_survey_count": len(non_survey_papers),
        "survey_count": len(survey_papers),
        "non_survey_target": config.k,
        "survey_target": config.survey_baseline_count,
        "selection_mode": selection_mode,
        "candidate_pool_order": "age_adjusted_citation_score_desc",
        "cross_encoder_relevance_usage": "metadata_only",
        "candidate_cutoff_formula": _citation_age_scoring_formula(
            config.citation_age_exponent
        ),
        "citation_age_exponent": config.citation_age_exponent,
        "s2_bulk_citation_target_per_query": bulk_citation_target_per_query,
        "cross_encoder_query": cross_encoder_query,
        "cross_encoder_scores_included": True,
        "llm_curation_complete": False,
        "as_of": as_of.isoformat(),
        "retrieval_complete": not warnings,
        "warning_count": len(warnings),
        "warnings": warnings,
        "run_name": output_dir.name,
        "run_dir": str(output_dir),
        "latest_dir": str(output_dir.parent),
    }


def _s2_bulk_deduped_paper_database_output(
    papers: list[Paper],
    output_dir: Path,
    config: PipelineConfig,
    as_of: date,
    raw_citation_rank_by_key: dict[str, int],
    age_adjusted_rank_by_key: dict[str, int],
    cross_encoder_rank_by_key: dict[str, int],
    warnings: list[str],
) -> dict[str, object]:
    return {
        "schema_version": "s2_bulk_deduped_paper_database.v1",
        "topic": config.topic,
        "workspace": workspace_name(config.topic),
        "paper_count": len(papers),
        "candidate_order": "age_adjusted_citation_score_desc",
        "paper_metadata_shape": "semantic_scholar_bulk_complete",
        "as_of": as_of.isoformat(),
        "run_name": output_dir.name,
        "run_dir": str(output_dir),
        "warning_count": len(warnings),
        "warnings": warnings,
        "papers": [
            _candidate_paper_output(
                paper,
                list_rank=index,
                as_of=as_of,
                raw_citation_rank=raw_citation_rank_by_key.get(stable_paper_key(paper)),
                age_adjusted_rank=age_adjusted_rank_by_key.get(stable_paper_key(paper)),
                cross_encoder_rank=cross_encoder_rank_by_key.get(stable_paper_key(paper)),
            )
            for index, paper in enumerate(papers, start=1)
        ],
    }


def _candidate_paper_output(
    paper: Paper,
    list_rank: int,
    as_of: date,
    raw_citation_rank: int | None,
    age_adjusted_rank: int | None,
    cross_encoder_rank: int | None,
) -> dict[str, object]:
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
        "raw_citation_rank": raw_citation_rank,
        "age_adjusted_rank": age_adjusted_rank,
        "cross_encoder_rank": cross_encoder_rank,
        "age_years": _rounded_optional_float(_candidate_age_years(paper, as_of)),
        "age_adjusted_citation_score": _rounded_optional_float(
            paper.age_adjusted_citation_score
        ),
        "citations_per_year": _rounded_optional_float(paper.citations_per_year),
        "cross_encoder_relevance": _rounded_optional_float(
            paper.cross_encoder_relevance if cross_encoder_rank is not None else None
        ),
        "is_survey": paper.is_survey,
        "found_by": sorted(paper.found_by),
    }


def _s2_bulk_citation_candidate_output(
    query: str,
    rank: int,
    paper: object,
) -> dict[str, object]:
    if not isinstance(paper, Paper):
        return {"query": query, "s2_bulk_citation_rank": rank}
    output = paper.to_json()
    output["query"] = query
    output["s2_bulk_citation_rank"] = rank
    return output


def _age_adjusted_candidate_output(
    paper: Paper,
    rank: int,
    as_of: date,
    raw_citation_rank: int | None = None,
    age_adjusted_rank: int | None = None,
    cross_encoder_rank: int | None = None,
) -> dict[str, object]:
    output = _candidate_paper_output(
        paper,
        list_rank=rank,
        as_of=as_of,
        raw_citation_rank=raw_citation_rank,
        age_adjusted_rank=age_adjusted_rank if age_adjusted_rank is not None else rank,
        cross_encoder_rank=cross_encoder_rank,
    )
    if cross_encoder_rank is None:
        output.pop("cross_encoder_rank", None)
    return output


def _rank_by_cross_encoder(papers: list[Paper]) -> list[Paper]:
    return sorted(
        papers,
        key=lambda paper: (
            paper.cross_encoder_relevance,
            paper.final_score,
            paper.citation_count or 0,
        ),
        reverse=True,
    )


def _rank_by_key(papers: list[Paper]) -> dict[str, int]:
    return {
        stable_paper_key(paper): rank for rank, paper in enumerate(papers, start=1)
    }


def _raw_citation_rank_by_key(candidates: list[dict[str, object]]) -> dict[str, int]:
    ranks: dict[str, int] = {}
    for row in candidates:
        paper = row.get("paper")
        rank = row.get("rank")
        if not isinstance(paper, Paper) or not isinstance(rank, int):
            continue
        key = stable_paper_key(paper)
        ranks[key] = min(rank, ranks.get(key, rank))
    return ranks


def _arxiv_link(arxiv_id: str | None) -> str | None:
    if not arxiv_id:
        return None
    return f"https://arxiv.org/abs/{arxiv_id}"


def _semantic_scholar_link(paper: Paper) -> str | None:
    if paper.semantic_scholar_id:
        return f"https://www.semanticscholar.org/paper/{paper.semantic_scholar_id}"
    if paper.url and "semanticscholar.org" in paper.url:
        return paper.url
    return None


def _doi_link(doi: str | None) -> str | None:
    if not doi:
        return None
    return f"https://doi.org/{doi}"


def _rounded_optional_float(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _next_run_output_dir(output_base_dir: Path) -> Path:
    output_base_dir.mkdir(parents=True, exist_ok=True)
    run_numbers: list[int] = []
    for child in output_base_dir.iterdir():
        if not child.is_dir() or not child.name.startswith("run"):
            continue
        suffix = child.name.removeprefix("run")
        if suffix.isdigit():
            run_numbers.append(int(suffix))
    return output_base_dir / f"run{max(run_numbers, default=0) + 1}"


def _write_run_metadata(
    output_dir: Path,
    config: PipelineConfig,
    cross_encoder_query: str,
    queries: list[str],
    selection_mode: str,
    extra_metadata: dict[str, object] | None = None,
) -> None:
    metadata = {
        "run_name": output_dir.name,
        "run_dir": str(output_dir),
        "started_at": datetime.now(UTC).isoformat(),
        "workspace": workspace_name(config.topic),
        "topic": config.topic,
        "cross_encoder_query": cross_encoder_query,
        "selection_mode": selection_mode,
        "query_variants": queries,
        "config": {
            "k": config.k,
            "s2_bulk_citation_multiplier": config.s2_bulk_citation_multiplier,
            "survey_baseline_count": config.survey_baseline_count,
            "request_delay_seconds": config.request_delay_seconds,
            "request_timeout_seconds": config.request_timeout_seconds,
            "max_academic_retries": config.max_academic_retries,
            "refresh_cache": config.refresh_cache,
            "citation_age_exponent": config.citation_age_exponent,
            "cross_encoder_model": config.cross_encoder_model,
        },
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    _write_json(output_dir / "run_metadata.json", metadata)


def _write_stage_status(
    output_dir: Path,
    stage: str,
    details: dict[str, object] | None = None,
) -> None:
    _write_json(
        output_dir / "stage_status.json",
        {
            "stage": stage,
            "updated_at": datetime.now(UTC).isoformat(),
            "details": details or {},
        },
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


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
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        write_json_file(
            output_base_dir / file_name,
            payload,
            archive_existing=True,
            run_label=output_dir.name,
        )


def _log(config: PipelineConfig | None, message: str) -> None:
    if config is not None and not config.verbose:
        return
    print(f"[research-tree] {message}", flush=True)
