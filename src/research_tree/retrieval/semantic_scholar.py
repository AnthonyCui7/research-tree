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


# Semantic Scholar's introductory keyed limit is one request per second across
# endpoints. A 1.5-second spacing stays below that shared limit and leaves room
# for retries without creating burst traffic.
SEMANTIC_SCHOLAR_KEYED_REQUEST_DELAY_SECONDS = 1.5


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
    "corpusId",
    "referenceCount",
    "influentialCitationCount",
    "isOpenAccess",
    "openAccessPdf",
    "fieldsOfStudy",
    "s2FieldsOfStudy",
    "publicationVenue",
    "journal",
    "citationStyles",
]
SEMANTIC_SCHOLAR_SEARCH_FIELDS = ",".join(SEMANTIC_SCHOLAR_PAPER_FIELDS)
SEMANTIC_SCHOLAR_DETAIL_FIELDS = ",".join([
    *SEMANTIC_SCHOLAR_PAPER_FIELDS,
    "tldr",
])


class SemanticScholarClient:
    base_url = "https://api.semanticscholar.org/graph/v1"

    def __init__(
        self,
        cache_dir: Path,
        api_key: str | None = None,
        request_delay_seconds: float = SEMANTIC_SCHOLAR_KEYED_REQUEST_DELAY_SECONDS,
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

    def get_paper_details(
        self,
        paper_ids: list[str],
        warnings: list[str] | None,
    ) -> dict[str, dict[str, Any]]:
        """Fetch source metadata for the small visible workspace set.

        Paper batch accepts at most 500 IDs, while a workspace intentionally stays
        below 30 papers. Keeping this as one cached request avoids generating a
        summary when Semantic Scholar already supplies a TLDR.
        """

        ids = list(dict.fromkeys(paper_id for paper_id in paper_ids if paper_id))
        if not ids:
            return {}
        try:
            payload = self.client.post_json(
                f"{self.base_url}/paper/batch?fields={SEMANTIC_SCHOLAR_DETAIL_FIELDS}",
                {"ids": ids},
            )
        except JsonRequestError as error:
            _append_warning(
                warnings,
                f"Semantic Scholar paper metadata enrichment failed: {error}",
            )
            return {}

        return {
            str(item.get("paperId")): item
            for item in payload
            if isinstance(item, dict) and item.get("paperId")
        }

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
        venue=_normalized_venue(item.get("venue")),
        authors=authors,
        doi=normalize_doi(external_ids.get("DOI")),
        arxiv_id=normalize_arxiv_id(external_ids.get("ArXiv")),
        semantic_scholar_id=item.get("paperId"),
        citation_count=item.get("citationCount"),
        publication_types=publication_types,
        url=item.get("url"),
        semantic_scholar_metadata=_paper_metadata(item),
    )
    paper.is_survey = looks_like_survey(paper.title, paper.publication_types)
    return paper


def semantic_scholar_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Return the documented bulk-search fields without discarding provider data."""

    return _paper_metadata(item)


def _paper_metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {
        field: item.get(field)
        for field in [*SEMANTIC_SCHOLAR_PAPER_FIELDS, "tldr"]
        if field in item
    }


def _normalized_venue(value: Any) -> str:
    venue = str(value or "").strip()
    return "N/A" if venue.casefold() in {"arxiv", "arxiv.org"} else venue


def _append_warning(warnings: list[str] | None, message: str) -> None:
    if warnings is not None:
        warnings.append(message)
