from __future__ import annotations

import copy
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
    k: int = 10,
    retriever: SimilarPaperRetriever | None = None,
    reranker: SimilarPaperReranker | None = None,
    bi_encoder_model: str = "all-MiniLM-L6-v2",
    cross_encoder_model: str = "cross-encoder/ms-marco-MiniLM-L6-v2",
) -> tuple[dict[str, Any], dict[str, Any]]:
    if k <= 0:
        raise ValueError("k must be positive.")

    enriched_workspace = copy.deepcopy(workspace)
    paper_cards = enriched_workspace.get("paper_cards") or {}
    if not isinstance(paper_cards, dict):
        raise ValueError("workspace paper_cards must be an object.")
    workspace_paper_ids = {str(paper_id) for paper_id in paper_cards}

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
        "paper_count": len(paper_cards),
        "papers": {},
    }

    for paper_id, card in paper_cards.items():
        if not isinstance(card, dict):
            continue
        query = _paper_card_query(card)
        candidates = [
            paper
            for paper in paper_database
            if paper.paper_id not in workspace_paper_ids and paper.document_text().strip()
        ]
        top_n = min(len(candidates), k * 10)
        bi_encoder_candidates = active_retriever.rank(query, candidates, top_n)
        reranked_candidates = active_reranker.rerank(query, bi_encoder_candidates)
        selected = reranked_candidates[:k]
        card["similar_papers"] = [
            _similar_paper_output(candidate) for candidate in selected
        ]
        debug["papers"][paper_id] = {
            "query": query,
            "current_paper_in_database": paper_id in background_by_id,
            "bi_encoder_candidate_count": len(bi_encoder_candidates),
            "selected_count": len(selected),
            "selected_paper_ids": [candidate.paper.paper_id for candidate in selected],
        }

    return enriched_workspace, debug


def build_similar_papers_from_files(
    *,
    workspace_json_path: Path,
    paper_database_json_path: Path,
    k: int = 10,
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


def _similar_paper_output(
    candidate: ScoredSimilarPaperCandidate,
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
        "similarity_score": _rounded(candidate.similarity_score),
        "cross_encoder_score": _rounded(candidate.cross_encoder_score),
        "reason": "",
    }


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _float_vector(value: Any) -> list[float]:
    return [float(item) for item in value]
