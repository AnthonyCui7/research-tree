from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from research_tree.artifacts import write_json_file
from research_tree.retrieval.similarity import cosine_similarity, min_max_normalize
from research_tree.workspace.schemas import (
    CandidatePaperMetadata,
    paper_database_from_artifact,
)
from research_tree.workspace.serialization import load_json_artifact


@dataclass(frozen=True)
class ScoredSimilarPaperCandidate:
    paper: CandidatePaperMetadata
    similarity_score: float
    cross_encoder_score: float | None = None


DEFAULT_SIMILAR_PAPERS_K = 10
DEFAULT_SIMILAR_BI_ENCODER_TOP_N = 100
DEFAULT_SIMILAR_CITATION_AGE_EXPONENT = 1.25
DEFAULT_SIMILAR_CITATION_SCORE_FLOOR = 10.0
DEFAULT_SIMILAR_PAPER_WORKERS = 1


class SimilarPaperRetriever(Protocol):
    def rank(
        self,
        query: str,
        papers: list[CandidatePaperMetadata],
        top_n: int,
    ) -> list[ScoredSimilarPaperCandidate]:
        ...


class SimilarPaperReranker(Protocol):
    def rerank(
        self,
        query: str,
        candidates: list[ScoredSimilarPaperCandidate],
    ) -> list[ScoredSimilarPaperCandidate]:
        ...


class LocalBiEncoderSimilarPaperRetriever:
    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        batch_size: int = 32,
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError(
                "sentence-transformers is required for similar-paper retrieval. "
                "Install with: pip install -e '.[pipeline]'"
            ) from error
        self.model = SentenceTransformer(model_name)

    def rank(
        self,
        query: str,
        papers: list[CandidatePaperMetadata],
        top_n: int,
    ) -> list[ScoredSimilarPaperCandidate]:
        if not papers or top_n <= 0:
            return []
        texts = [query, *[paper.document_text() for paper in papers]]
        embeddings = self.model.encode(texts, batch_size=self.batch_size)
        query_embedding = _float_vector(embeddings[0])
        paper_embeddings = [_float_vector(embedding) for embedding in embeddings[1:]]
        raw_scores = [
            cosine_similarity(query_embedding, paper_embedding)
            for paper_embedding in paper_embeddings
        ]
        normalized_scores = min_max_normalize(raw_scores)
        scored = [
            ScoredSimilarPaperCandidate(paper=paper, similarity_score=score)
            for paper, score in zip(papers, normalized_scores, strict=True)
        ]
        return sorted(
            scored,
            key=lambda candidate: candidate.similarity_score,
            reverse=True,
        )[:top_n]


class LocalCrossEncoderSimilarPaperReranker:
    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L6-v2",
        batch_size: int = 16,
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as error:
            raise RuntimeError(
                "sentence-transformers is required for similar-paper reranking. "
                "Install with: pip install -e '.[pipeline]'"
            ) from error
        self.model = CrossEncoder(model_name)

    def rerank(
        self,
        query: str,
        candidates: list[ScoredSimilarPaperCandidate],
    ) -> list[ScoredSimilarPaperCandidate]:
        if not candidates:
            return []
        pairs = [(query, candidate.paper.document_text()) for candidate in candidates]
        raw_scores = [
            float(score)
            for score in self.model.predict(pairs, batch_size=self.batch_size)
        ]
        normalized_scores = min_max_normalize(raw_scores)
        reranked = [
            ScoredSimilarPaperCandidate(
                paper=candidate.paper,
                similarity_score=candidate.similarity_score,
                cross_encoder_score=score,
            )
            for candidate, score in zip(candidates, normalized_scores, strict=True)
        ]
        return sorted(
            reranked,
            key=lambda candidate: (
                candidate.cross_encoder_score or 0.0,
                candidate.similarity_score,
            ),
            reverse=True,
        )


def build_similar_papers(
    *,
    workspace: dict[str, Any],
    paper_database: list[CandidatePaperMetadata],
    k: int = DEFAULT_SIMILAR_PAPERS_K,
    bi_encoder_top_n: int = DEFAULT_SIMILAR_BI_ENCODER_TOP_N,
    citation_age_exponent: float = DEFAULT_SIMILAR_CITATION_AGE_EXPONENT,
    citation_score_floor: float = DEFAULT_SIMILAR_CITATION_SCORE_FLOOR,
    retriever: SimilarPaperRetriever | None = None,
    reranker: SimilarPaperReranker | None = None,
    paper_ids: set[str] | None = None,
    max_workers: int = DEFAULT_SIMILAR_PAPER_WORKERS,
    bi_encoder_model: str = "all-MiniLM-L6-v2",
    cross_encoder_model: str = "cross-encoder/ms-marco-MiniLM-L6-v2",
) -> tuple[dict[str, Any], dict[str, Any]]:
    if k <= 0:
        raise ValueError("k must be positive.")
    if bi_encoder_top_n < k:
        raise ValueError("bi_encoder_top_n must be at least k.")
    if citation_age_exponent <= 0:
        raise ValueError("citation_age_exponent must be positive.")
    if citation_score_floor < 0:
        raise ValueError("citation_score_floor cannot be negative.")
    if max_workers <= 0:
        raise ValueError("max_workers must be positive.")

    enriched_workspace = copy.deepcopy(workspace)
    paper_cards = enriched_workspace.get("paper_cards") or {}
    if not isinstance(paper_cards, dict):
        raise ValueError("workspace paper_cards must be an object.")
    workspace_paper_ids = {str(paper_id) for paper_id in paper_cards}
    as_of = datetime.now(UTC).date()

    active_retriever = retriever or LocalBiEncoderSimilarPaperRetriever(
        model_name=bi_encoder_model
    )
    active_reranker = reranker or LocalCrossEncoderSimilarPaperReranker(
        model_name=cross_encoder_model
    )

    background_by_id = {paper.paper_id: paper for paper in paper_database}
    debug: dict[str, Any] = {
        "schema_version": "research_tree_similar_papers_debug.v1",
        "k": k,
        "bi_encoder_top_n": bi_encoder_top_n,
        "citation_age_exponent": citation_age_exponent,
        "citation_score_floor": citation_score_floor,
        "max_workers": max_workers,
        "paper_count": len(paper_cards),
        "papers": {},
    }

    unfiltered_candidates = [
        paper
        for paper in paper_database
        if paper.paper_id not in workspace_paper_ids and paper.document_text().strip()
    ]
    citation_scores = {
        paper.paper_id: _age_adjusted_citation_score(
            paper, as_of=as_of, citation_age_exponent=citation_age_exponent
        )
        for paper in unfiltered_candidates
    }
    candidates = [
        paper
        for paper in unfiltered_candidates
        if citation_scores[paper.paper_id] >= citation_score_floor
    ]
    targets = [
        (str(paper_id), card)
        for paper_id, card in paper_cards.items()
        if isinstance(card, dict)
        and (paper_ids is None or str(paper_id) in paper_ids)
    ]
    worker_count = min(max_workers, len(targets) or 1)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = executor.map(
            lambda target: _build_similar_papers_for_card(
                paper_id=target[0],
                card=target[1],
                retriever=active_retriever,
                reranker=active_reranker,
                candidates=candidates,
                citation_scores=citation_scores,
                unfiltered_candidate_count=len(unfiltered_candidates),
                filtered_by_citation_floor=len(unfiltered_candidates) - len(candidates),
                current_paper_in_database=target[0] in background_by_id,
                k=k,
                bi_encoder_top_n=bi_encoder_top_n,
            ),
            targets,
        )
    for paper_id, similar_papers, paper_debug in results:
        card = paper_cards.get(paper_id)
        if isinstance(card, dict):
            card["similar_papers"] = similar_papers
        debug["papers"][paper_id] = paper_debug

    return enriched_workspace, debug


def build_similar_papers_from_files(
    *,
    workspace_json_path: Path,
    paper_database_json_path: Path,
    k: int = DEFAULT_SIMILAR_PAPERS_K,
    bi_encoder_top_n: int = DEFAULT_SIMILAR_BI_ENCODER_TOP_N,
    citation_age_exponent: float = DEFAULT_SIMILAR_CITATION_AGE_EXPONENT,
    citation_score_floor: float = DEFAULT_SIMILAR_CITATION_SCORE_FLOOR,
    bi_encoder_model: str = "all-MiniLM-L6-v2",
    cross_encoder_model: str = "cross-encoder/ms-marco-MiniLM-L6-v2",
) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = load_json_artifact(workspace_json_path)
    paper_database_payload = load_json_artifact(paper_database_json_path)
    if not isinstance(workspace, dict):
        raise ValueError(f"workspace JSON must be an object: {workspace_json_path}")
    paper_database = paper_database_from_artifact(paper_database_payload)
    return build_similar_papers(
        workspace=workspace,
        paper_database=paper_database,
        k=k,
        bi_encoder_top_n=bi_encoder_top_n,
        citation_age_exponent=citation_age_exponent,
        citation_score_floor=citation_score_floor,
        bi_encoder_model=bi_encoder_model,
        cross_encoder_model=cross_encoder_model,
    )


def write_similar_paper_artifacts(
    *,
    output_dir: Path,
    workspace_with_similar_papers: dict[str, Any],
    debug: dict[str, Any],
    run_label: str | None = None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_path = output_dir / "workspace_with_similar_papers.json"
    debug_path = output_dir / "similar_papers_debug.json"
    write_json_file(
        workspace_path,
        workspace_with_similar_papers,
        archive_existing=True,
        run_label=run_label,
    )
    write_json_file(
        debug_path,
        debug,
        archive_existing=True,
        run_label=run_label,
    )
    return {"workspace": workspace_path, "debug": debug_path}


def _paper_card_query(card: dict[str, Any]) -> str:
    title = str(card.get("title") or card.get("paper_id") or "")
    abstract = str(card.get("abstract") or "")
    if abstract:
        return f"{title}\n\n{abstract}"
    return title


def _build_similar_papers_for_card(
    *,
    paper_id: str,
    card: dict[str, Any],
    retriever: SimilarPaperRetriever,
    reranker: SimilarPaperReranker,
    candidates: list[CandidatePaperMetadata],
    citation_scores: dict[str, float],
    unfiltered_candidate_count: int,
    filtered_by_citation_floor: int,
    current_paper_in_database: bool,
    k: int,
    bi_encoder_top_n: int,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    query = _paper_card_query(card)
    top_n = min(len(candidates), bi_encoder_top_n)
    bi_encoder_candidates = retriever.rank(query, candidates, top_n)
    reranked_candidates = reranker.rerank(query, bi_encoder_candidates)
    selected = reranked_candidates[:k]
    similar_papers = [
        _similar_paper_output(
            candidate,
            rank=rank,
            age_adjusted_citation_score=citation_scores[candidate.paper.paper_id],
        )
        for rank, candidate in enumerate(selected, start=1)
    ]
    return (
        paper_id,
        similar_papers,
        {
            "query": query,
            "current_paper_in_database": current_paper_in_database,
            "candidate_pool_count": unfiltered_candidate_count,
            "filtered_by_citation_floor": filtered_by_citation_floor,
            "bi_encoder_candidate_count": len(bi_encoder_candidates),
            "selected_count": len(selected),
            "selected_paper_ids": [candidate.paper.paper_id for candidate in selected],
        },
    )


def _similar_paper_output(
    candidate: ScoredSimilarPaperCandidate,
    *,
    rank: int,
    age_adjusted_citation_score: float,
) -> dict[str, Any]:
    paper = candidate.paper
    return {
        "paper_id": paper.paper_id,
        "title": paper.title,
        "authors": paper.authors,
        "year": paper.year,
        "publication_date": paper.publication_date,
        "venue": paper.venue,
        "primary_link": paper.primary_link,
        "arxiv_link": paper.arxiv_link,
        "s2_link": paper.s2_link,
        "provider": "initial_candidate_pool",
        "rank": rank,
        "citation_count": paper.citation_count,
        "age_adjusted_citation_score": _rounded(age_adjusted_citation_score),
        "similarity_score": _rounded(candidate.similarity_score),
        "cross_encoder_score": _rounded(candidate.cross_encoder_score),
        "reason": "",
    }


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _age_adjusted_citation_score(
    paper: CandidatePaperMetadata,
    *,
    as_of: date,
    citation_age_exponent: float,
) -> float:
    age_years = _paper_age_years(paper, as_of=as_of)
    if age_years is None:
        return 0.0
    return max(paper.citation_count or 0, 0) / max(age_years, 0.5) ** citation_age_exponent


def _paper_age_years(paper: CandidatePaperMetadata, *, as_of: date) -> float | None:
    publication_date = None
    if paper.publication_date:
        try:
            publication_date = date.fromisoformat(paper.publication_date)
        except ValueError:
            publication_date = None
    if publication_date is None and paper.year is not None:
        publication_date = date(paper.year, 7, 1)
    if publication_date is None:
        return None
    return max((as_of - publication_date).days, 1) / 365.25


def _float_vector(value: Any) -> list[float]:
    return [float(item) for item in value]
