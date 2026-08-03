from __future__ import annotations

from research_tree.retrieval.models import Paper
from research_tree.retrieval.similarity import min_max_normalize


class LocalCrossEncoderReranker:
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
                "sentence-transformers is required for the local cross-encoder. "
                "Install with: pip install -e '.[pipeline]'"
            ) from error
        self.model = CrossEncoder(model_name)

    def rerank(self, query: str, papers: list[Paper]) -> None:
        if not papers:
            return
        pairs = [(query, paper.document_text()) for paper in papers]
        raw_scores = self.model.predict(pairs, batch_size=self.batch_size)
        normalized_scores = min_max_normalize([float(score) for score in raw_scores])
        for paper, score in zip(papers, normalized_scores, strict=True):
            paper.cross_encoder_relevance = score
