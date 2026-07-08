from __future__ import annotations

from pathlib import Path
from typing import Any

from research_tree.retrieval.cache import CachedJsonClient, JsonRequestError
from research_tree.retrieval.dates import parse_iso_date
from research_tree.retrieval.models import Paper
from research_tree.retrieval.text import (
    looks_like_survey,
    normalize_arxiv_id,
    normalize_doi,
)


SEMANTIC_SCHOLAR_PAPER_FIELDS = [
    "paperId",
    "title",
    "abstract",
    "year",
    "publicationDate",
    "venue",
    "authors",
    "externalIds",
    "citationCount",
    "publicationTypes",
    "url",
]
SEMANTIC_SCHOLAR_SEARCH_FIELDS = ",".join(SEMANTIC_SCHOLAR_PAPER_FIELDS)


class SemanticScholarClient:
    base_url = "https://api.semanticscholar.org/graph/v1"

    def __init__(
        self,
        cache_dir: Path,
        api_key: str | None = None,
        request_delay_seconds: float = 1.0,
        refresh_cache: bool = False,
        max_retries: int = 2,
        timeout_seconds: float = 20.0,
    ) -> None:
        headers = {"User-Agent": "research-tree/0.1"}
        if api_key:
            headers["x-api-key"] = api_key
        self.client = CachedJsonClient(
            cache_dir=cache_dir,
            request_delay_seconds=request_delay_seconds,
            refresh_cache=refresh_cache,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            headers=headers,
        )

    def search_by_citation_count(
        self,
        query: str,
        target_count: int | None,
        warnings: list[str] | None,
    ) -> list[Paper]:
        papers: list[Paper] = []
        token: str | None = None
        while target_count is None or len(papers) < target_count:
            params = {
                "query": query,
                "fields": SEMANTIC_SCHOLAR_SEARCH_FIELDS,
                "sort": "citationCount:desc",
            }
            if token:
                params["token"] = token
            try:
                payload = self.client.get_json(
                    f"{self.base_url}/paper/search/bulk",
                    params,
                )
            except JsonRequestError as error:
                _append_warning(
                    warnings,
                    f"Semantic Scholar citation-count search failed for query '{query}': {error}",
                )
                break

            items = payload.get("data") or []
            if target_count is not None:
                remaining = target_count - len(papers)
                items = items[:remaining]
            for item in items:
                paper = paper_from_semantic_scholar(item)
                paper.found_by.add(f"query_search_citation_count:{query}")
                papers.append(paper)
            token = payload.get("token")
            if not token or target_count is None or not items:
                break
        return papers


def paper_from_semantic_scholar(item: dict[str, Any]) -> Paper:
    external_ids = item.get("externalIds") or {}
    authors = [
        author.get("name", "").strip()
        for author in item.get("authors") or []
        if author.get("name")
    ]
    publication_types = [
        value for value in item.get("publicationTypes") or [] if isinstance(value, str)
    ]
    paper = Paper(
        title=(item.get("title") or "").strip(),
        abstract=(item.get("abstract") or "").strip(),
        year=item.get("year"),
        publication_date=parse_iso_date(item.get("publicationDate")),
        venue=(item.get("venue") or "").strip(),
        authors=authors,
        doi=normalize_doi(external_ids.get("DOI")),
        arxiv_id=normalize_arxiv_id(external_ids.get("ArXiv")),
        semantic_scholar_id=item.get("paperId"),
        citation_count=item.get("citationCount"),
        publication_types=publication_types,
        url=item.get("url"),
    )
    paper.is_survey = looks_like_survey(paper.title, paper.publication_types)
    return paper


def _append_warning(warnings: list[str] | None, message: str) -> None:
    if warnings is not None:
        warnings.append(message)
