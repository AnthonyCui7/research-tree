"""Annotate a paper, from PDF bytes to placed annotations.

    extract -> chunk -> index -> annotate -> dedupe -> review -> place

Each stage is a module of its own; this is the order they run in and the only
place that knows the whole sequence.
"""

from __future__ import annotations

import logging
import time

import fitz

from research_tree.annotation.anchoring import resolve_annotation_geometry
from research_tree.annotation.chunking import chunk_blocks
from research_tree.annotation.config import openai_api_key, retrieval_mode
from research_tree.annotation.extraction import build_page_sources, extract_blocks
from research_tree.annotation.generation import annotate_chunks, build_paper_brief
from research_tree.annotation.models import PaperAnnotation
from research_tree.annotation.retrieval import build_retrieval_index
from research_tree.annotation.validation import (
    dedupe_annotations,
    enforce_quote_rules,
    review_annotations_by_page,
)


logger = logging.getLogger("uvicorn.error")


def generate_paper_annotations(
    pdf_bytes: bytes,
    *,
    title: str,
    abstract: str = "",
    api_key: str | None = None,
    mode: str | None = None,
) -> list[PaperAnnotation]:
    key = api_key or openai_api_key()
    retrieval = mode or retrieval_mode()
    started_at = time.monotonic()

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        blocks = extract_blocks(document)
        page_sources = build_page_sources(blocks)
        chunks = chunk_blocks(blocks)
        logger.info(
            "Annotating %r: %s pages, %s blocks, %s passages, %s retrieval",
            title,
            document.page_count,
            len(blocks),
            len(chunks),
            retrieval,
        )
        if not chunks:
            raise ValueError("The PDF holds no extractable prose to annotate.")

        index = build_retrieval_index(
            retrieval,
            title=title,
            abstract=abstract,
            chunks=chunks,
            api_key=key,
        )
        candidates = annotate_chunks(
            chunks,
            page_sources=page_sources,
            paper_brief=build_paper_brief(title, abstract, chunks),
            index=index,
            api_key=key,
        )
        reviewed = review_annotations_by_page(
            dedupe_annotations(candidates), page_sources, api_key=key
        )
        annotations = enforce_quote_rules(reviewed, page_sources)
        annotations = resolve_annotation_geometry(annotations, document, page_sources)
    finally:
        document.close()

    logger.info(
        "Annotated %r: %s annotations in %.1fs",
        title,
        len(annotations),
        time.monotonic() - started_at,
    )
    return annotations
