"""Embeddings for the retrieval index.

Rows are normalized once at embed time so a similarity query is a single
matrix-vector product rather than a per-row norm.
"""

from __future__ import annotations

import logging

import numpy as np

from research_tree.llm import DEFAULT_EMBEDDING_MODEL, call_embeddings_api


logger = logging.getLogger("uvicorn.error")

EMBED_BATCH_SIZE = 100
EMBED_TIMEOUT_SECONDS = 120.0
# Below this a vector carries no direction worth dividing by; extraction
# occasionally yields near-empty text that embeds to almost nothing.
NORM_FLOOR = 1e-6


def embed_texts(
    texts: list[str],
    *,
    api_key: str,
    label: str,
    model: str = DEFAULT_EMBEDDING_MODEL,
) -> np.ndarray:
    """Embed every text and return one normalized row per input."""

    if not texts:
        return np.zeros((0, 1), dtype=np.float32)

    # A blank input is rejected by the API, and a blank chunk is not worth
    # failing a whole paper over.
    safe_texts = [text if text.strip() else " " for text in texts]
    rows: list[list[float]] = []
    for start in range(0, len(safe_texts), EMBED_BATCH_SIZE):
        rows.extend(
            call_embeddings_api(
                safe_texts[start : start + EMBED_BATCH_SIZE],
                api_key=api_key,
                timeout_seconds=EMBED_TIMEOUT_SECONDS,
                label=label,
                model=model,
            )
        )

    matrix = np.asarray(rows, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    degenerate = norms < NORM_FLOOR
    if degenerate.any():
        logger.warning(
            "Embedding produced %s near-zero vectors; leaving them unscaled",
            int(degenerate.sum()),
        )
        norms[degenerate] = 1.0
    return (matrix / norms).astype(np.float32)


def cosine_top_k(
    query: np.ndarray,
    matrix: np.ndarray,
    k: int,
    *,
    skip_index: int,
) -> list[tuple[int, float]]:
    """Rank normalized rows against a normalized query, best first."""

    if matrix.size == 0 or k <= 0:
        return []
    similarities = matrix @ query
    ranked: list[tuple[int, float]] = []
    for index in np.argsort(-similarities):
        position = int(index)
        if position == skip_index:
            continue
        ranked.append((position, float(similarities[position])))
        if len(ranked) >= k:
            break
    return ranked
