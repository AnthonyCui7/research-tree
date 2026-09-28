from __future__ import annotations

import copy
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Mapping

from research_tree.retrieval.cache import JsonRequestError
from research_tree.retrieval.full_text import (
    PaperContentResult,
    retrieve_open_access_paper_content,
    upgraded_to_https,
)
from research_tree.retrieval.semantic_scholar import (
    SemanticScholarClient,
    paper_from_semantic_scholar,
    semantic_scholar_metadata,
)
from research_tree.workspace.repository import WorkspaceRepository
from research_tree.workspace.schemas import CandidatePaperMetadata
from research_tree.workspace.tldr import TldrGenerator, apply_generated_tldr

logger = logging.getLogger("uvicorn.error")


def prefetch_paper_content(
    papers: Iterable[CandidatePaperMetadata],
    *,
    max_workers: int = 4,
) -> dict[str, PaperContentResult]:
    """Download candidate full text ahead of hydration.

    The workspace's papers are a subset of the candidate hand-off, so every
    download can happen while the construction call is in flight. Only
    successful retrievals are kept: hydrate retries anything missing itself, so
    a transient download failure here costs nothing. These are publisher and
    arXiv downloads, never Semantic Scholar API requests, so the shared 1 req/s
    limiter does not apply.
    """

    def fetch(paper: CandidatePaperMetadata) -> tuple[str, PaperContentResult]:
        return paper.paper_id, _retrieve_from_first_source_that_answers(
            paper_id=paper.paper_id,
            title=paper.title or paper.paper_id,
            sources=_pdf_sources(paper.semantic_scholar_metadata or {}, paper.arxiv_id),
        )

    papers = list(papers)
    if not papers:
        return {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(papers))) as executor:
        results = executor.map(fetch, papers)
    return {
        paper_id: result
        for paper_id, result in results
        if str(result.content.get("status") or "").startswith("available")
    }


def hydrate_workspace_papers(
    *,
    workspace: dict[str, Any],
    repository: WorkspaceRepository,
    semantic_scholar: SemanticScholarClient,
    tldr_generator: TldrGenerator | None = None,
    prefetched_content: Mapping[str, PaperContentResult] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Fetch rich metadata and lawful full text for the selected visible papers."""

    hydrated = copy.deepcopy(workspace)
    cards = hydrated.get("paper_cards")
    if not isinstance(cards, dict):
        raise ValueError("workspace paper_cards must be an object.")
    workspace_id = str(hydrated.get("workspace_id") or "")
    if not workspace_id:
        raise ValueError("workspace_id is required before paper hydration.")

    warnings: list[str] = []
    # The candidates stage already fetched full metadata for every selected paper.
    # Only papers it could not describe are worth spending a request on here.
    details_by_id = _details_from_cards(cards)
    missing_ids = [str(paper_id) for paper_id in cards if str(paper_id) not in details_by_id]
    if missing_ids:
        try:
            details_by_id.update(semantic_scholar.get_paper_details(missing_ids, warnings))
        except JsonRequestError as error:
            # The workspace is built and its model calls are paid for. What is
            # missing here is a provider summary and a PDF link for a few
            # cards, which is a warning on the run, not a reason to lose it.
            logger.warning("hydration could not fetch paper metadata: %s", error)
            warnings.append(
                f"Semantic Scholar metadata was unavailable for {len(missing_ids)} papers."
            )
    for paper_id, raw_card in cards.items():
        if not isinstance(raw_card, dict):
            continue
        details = details_by_id.get(str(paper_id)) or {}
        if details:
            source = paper_from_semantic_scholar(details)
            _fill_card_metadata(raw_card, source)
            raw_card["semantic_scholar_metadata"] = semantic_scholar_metadata(details)
            tldr = details.get("tldr")
            if isinstance(tldr, Mapping) and str(tldr.get("text") or "").strip():
                raw_card["tldr"] = str(tldr["text"]).strip()
                raw_card["tldr_model"] = tldr.get("model")
                raw_card["tldr_source"] = "semantic_scholar"
        if not str(raw_card.get("tldr") or "").strip() and tldr_generator is not None:
            try:
                apply_generated_tldr(raw_card, generator=tldr_generator)
            except RuntimeError as error:
                logger.warning("generated TLDR failed paper_id=%s: %s", paper_id, error)
                warnings.append(f"A summary could not be generated for {paper_id}.")
        content_result = (prefetched_content or {}).get(str(paper_id))
        if content_result is None:
            content_result = _retrieve_from_first_source_that_answers(
                paper_id=str(paper_id),
                title=str(raw_card.get("title") or paper_id),
                sources=_pdf_sources(details, raw_card.get("arxiv_id")),
            )
        content_key = repository.save_paper_content(
            workspace_id, str(paper_id), content_result.content
        )
        raw_card["paper_content"] = {
            "content_key": content_key,
            "status": content_result.content.get("status"),
            "source_type": content_result.content.get("source_type"),
            "source_url": content_result.content.get("source_url"),
            "page_count": content_result.content.get("page_count"),
            "figure_count": content_result.content.get("figure_count"),
            "sha256": content_result.content.get("sha256"),
            "truncated": bool(content_result.content.get("truncated")),
        }
        if content_result.warning:
            warnings.append(content_result.warning)
    return hydrated, warnings


def _details_from_cards(cards: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Recover Semantic Scholar payloads the construction stage carried over."""

    details: dict[str, dict[str, Any]] = {}
    for paper_id, card in cards.items():
        if not isinstance(card, dict):
            continue
        metadata = card.get("semantic_scholar_metadata")
        # Absent `tldr` means the metadata came from bulk search, which omits it;
        # such a paper still needs a detail request.
        if isinstance(metadata, dict) and "tldr" in metadata:
            details[str(paper_id)] = metadata
    return details




def _pdf_sources(details: Mapping[str, Any], arxiv_id: Any) -> list[str]:
    """Where a paper's PDF may be fetched from, best first.

    Semantic Scholar's open-access link, then arXiv's own copy when the paper
    has one. The first is often a publisher's landing page, or a host that
    refuses anything that is not a browser, and the arXiv PDF is the same
    paper.
    """

    sources: list[str] = []
    open_access_pdf = details.get("openAccessPdf")
    if isinstance(open_access_pdf, Mapping) and open_access_pdf.get("url"):
        sources.append(upgraded_to_https(str(open_access_pdf["url"]).strip()))
    if arxiv_id:
        sources.append(f"https://arxiv.org/pdf/{arxiv_id}")
    return list(dict.fromkeys(sources))


def _retrieve_from_first_source_that_answers(
    *, paper_id: str, title: str, sources: list[str]
) -> PaperContentResult:
    result = retrieve_open_access_paper_content(
        paper_id=paper_id, title=title, source_url=sources[0] if sources else None
    )
    for source in sources[1:]:
        if str(result.content.get("status") or "").startswith("available"):
            break
        result = retrieve_open_access_paper_content(
            paper_id=paper_id, title=title, source_url=source
        )
    return result


def _fill_card_metadata(card: dict[str, Any], paper: Any) -> None:
    for key, value in {
        "title": paper.title,
        "abstract": paper.abstract,
        "year": paper.year,
        "publication_date": paper.publication_date.isoformat() if paper.publication_date else None,
        "venue": paper.venue,
        "authors": paper.authors,
        "doi": paper.doi,
        "arxiv_id": paper.arxiv_id,
        "citation_count": paper.citation_count,
        "primary_link": paper.url,
    }.items():
        if value not in (None, "", []):
            card[key] = value
