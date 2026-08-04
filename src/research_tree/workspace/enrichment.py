from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Mapping

from research_tree.retrieval.full_text import (
    PaperContentResult,
    retrieve_open_access_paper_content,
)
from research_tree.retrieval.semantic_scholar import (
    SemanticScholarClient,
    paper_from_semantic_scholar,
    semantic_scholar_metadata,
)
from research_tree.workspace.repository import WorkspaceRepository
from research_tree.workspace.schemas import CandidatePaperMetadata
from research_tree.workspace.tldr import TldrGenerator, apply_generated_tldr


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
        metadata = paper.semantic_scholar_metadata or {}
        source_url = _open_access_pdf_url(metadata, {"arxiv_id": paper.arxiv_id})
        return paper.paper_id, retrieve_open_access_paper_content(
            paper_id=paper.paper_id,
            title=paper.title or paper.paper_id,
            source_url=source_url,
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
        details_by_id.update(semantic_scholar.get_paper_details(missing_ids, warnings))
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
                warnings.append(f"Generated TLDR failed for {paper_id}: {error}")
        content_result = (prefetched_content or {}).get(str(paper_id))
        if content_result is None:
            source_url = _open_access_pdf_url(details, raw_card)
            content_result = retrieve_open_access_paper_content(
                paper_id=str(paper_id),
                title=str(raw_card.get("title") or paper_id),
                source_url=source_url,
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


def load_paper_content_context(
    repository: WorkspaceRepository,
    *,
    workspace_id: str,
    paper_ids: list[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    contents: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for paper_id in dict.fromkeys(paper_ids):
        try:
            content = repository.get_paper_content(workspace_id, paper_id)
        except (FileNotFoundError, OSError, ValueError) as error:
            warnings.append(f"Full text could not be loaded for {paper_id}: {error}")
            continue
        contents[paper_id] = content
    return contents, warnings


def _open_access_pdf_url(details: Mapping[str, Any], card: Mapping[str, Any]) -> str | None:
    open_access_pdf = details.get("openAccessPdf")
    if isinstance(open_access_pdf, Mapping) and open_access_pdf.get("url"):
        return str(open_access_pdf["url"])
    arxiv_id = card.get("arxiv_id")
    return f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None


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
