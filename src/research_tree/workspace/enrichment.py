from __future__ import annotations

import copy
from typing import Any, Mapping

from research_tree.retrieval.full_text import retrieve_open_access_paper_content
from research_tree.retrieval.semantic_scholar import (
    SemanticScholarClient,
    paper_from_semantic_scholar,
    semantic_scholar_metadata,
)
from research_tree.workspace.repository import WorkspaceRepository


def hydrate_workspace_papers(
    *,
    workspace: dict[str, Any],
    repository: WorkspaceRepository,
    semantic_scholar: SemanticScholarClient,
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
    details_by_id = semantic_scholar.get_paper_details(
        [str(paper_id) for paper_id in cards], warnings
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
                raw_card["tldr_source"] = "semantic_scholar"
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
