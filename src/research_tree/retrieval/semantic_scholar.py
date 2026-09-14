from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from research_tree.retrieval.cache import CachedJsonClient, JsonRequestError, RateLimiter
from research_tree.retrieval.dates import parse_iso_date
from research_tree.retrieval.models import Paper
from research_tree.retrieval.text import (
    looks_like_survey,
    normalize_arxiv_id,
    normalize_doi,
)


# Semantic Scholar allows one request per second, counted cumulatively across
# every endpoint; an API key buys reliability, not throughput. The margin above
# one second absorbs clock jitter and keeps us clear of server-side load
# shedding, which 429s compliant clients when S2 is stressed.
SEMANTIC_SCHOLAR_KEYED_REQUEST_DELAY_SECONDS = 1.5

# Four retries engage the client's full backoff ladder (5/10/45/90 s). The
# probed recovery window after a bulk 429 streak was 30+ seconds, so the 45 s
# tier is the first one that can actually outlast a shedding period; with only
# two retries (5/10 s) every real shedding window killed the run. A request
# that still fails after ~2.5 minutes of patience is a genuine outage.
SEMANTIC_SCHOLAR_MAX_RETRIES = 4

# Every Semantic Scholar client shares one request budget — across threads,
# clients, and processes (backend, CLIs, anything else using this key from
# this machine). The file holds the last-request timestamp under an flock.
SEMANTIC_SCHOLAR_RATE_LIMITER = RateLimiter(
    lock_file=Path.home() / ".research_tree" / "s2_rate_limiter.lock"
)

# Documented endpoint ceilings.
SEMANTIC_SCHOLAR_BATCH_ID_LIMIT = 500
SEMANTIC_SCHOLAR_BULK_PAGE_SIZE = 1000

# The batch endpoint caps a response at 10 MB. Reference lists run to hundreds of
# ids per paper, so reference fetches use a smaller chunk than the id limit.
SEMANTIC_SCHOLAR_REFERENCE_CHUNK_SIZE = 250
# Retries for papers S2 failed to hydrate go out in smaller batches: hydration
# failures scale with batch size (measured Aug 2026 — one 250-paper chunk lost
# most of its reference lists while a single-paper batch succeeded live).
SEMANTIC_SCHOLAR_REFERENCE_RETRY_CHUNK_SIZE = 50


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
        max_retries: int = SEMANTIC_SCHOLAR_MAX_RETRIES,
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
            rate_limiter=SEMANTIC_SCHOLAR_RATE_LIMITER,
        )

    def bulk_search(
        self,
        query: str,
        *,
        max_papers: int,
        sort: str = "citationCount:desc",
        filters: dict[str, str] | None = None,
        source_tag: str | None = None,
        warnings: list[str] | None = None,
    ) -> list[Paper]:
        """Page bulk search until `max_papers` papers are collected.

        Bulk search returns up to 1,000 papers per request and supports boolean
        query syntax, so one composed query covering a field's whole vocabulary
        costs far fewer requests than one query per phrase.
        """

        found_by = source_tag or f"s2_bulk_search:{sort}"
        papers: list[Paper] = []
        token: str | None = None
        while len(papers) < max_papers:
            params: dict[str, Any] = {
                "query": query,
                "fields": SEMANTIC_SCHOLAR_SEARCH_FIELDS,
                "sort": sort,
                **(filters or {}),
            }
            if token:
                params["token"] = token
            try:
                payload = self.client.get_json(
                    f"{self.base_url}/paper/search/bulk",
                    params,
                )
            except JsonRequestError as error:
                # The client already retried with backoff, so this is a real
                # outage or sustained throttling. A truncated pool silently
                # reshapes everything downstream; fail the run instead.
                raise JsonRequestError(
                    f"Semantic Scholar bulk search failed after retries for "
                    f"query '{query}': {error}"
                ) from error

            items = (payload.get("data") or [])[: max_papers - len(papers)]
            for item in items:
                paper = paper_from_semantic_scholar(item)
                paper.found_by.add(found_by)
                papers.append(paper)
            token = payload.get("token")
            if not token or not items:
                break
        return papers

    def get_references_batch(
        self,
        paper_ids: list[str],
        warnings: list[str] | None,
        chunk_size: int = SEMANTIC_SCHOLAR_REFERENCE_CHUNK_SIZE,
    ) -> dict[str, list[str]]:
        """Return `{paper_id: [referenced paper ids]}` for the given papers.

        The batch endpoint returns complete reference lists, which collapses what
        would otherwise be one request per paper into a couple of requests total.
        """

        ids = _unique_ids(paper_ids)
        references: dict[str, list[str]] = {}
        for chunk in _chunked(ids, max(chunk_size, 1)):
            references.update(self._references_for_chunk(chunk, warnings))
        # Under load the batch endpoint returns 200 with the `references`
        # field absent (or empty despite a nonzero referenceCount) for a
        # subset of papers — measured Aug 2026, up to 179 of 250 in one
        # chunk, which silently halved a run's citation graph. Hydration
        # failures scale with batch size (a single-paper batch succeeded live
        # while 250-paper batches failed), so the retry over the missing ids
        # uses smaller chunks; being a different request body, it also can
        # never be satisfied by a cached partial response.
        missing = [paper_id for paper_id in ids if paper_id not in references]
        if missing:
            retry_chunk = max(min(chunk_size, SEMANTIC_SCHOLAR_REFERENCE_RETRY_CHUNK_SIZE), 1)
            for chunk in _chunked(missing, retry_chunk):
                references.update(self._references_for_chunk(chunk, warnings))
            still_missing = [
                paper_id for paper_id in missing if paper_id not in references
            ]
            if still_missing:
                _append_warning(
                    warnings,
                    "Semantic Scholar returned no reference lists for "
                    f"{len(still_missing)} of {len(ids)} papers even after a "
                    "retry; the citation graph is missing their bibliographies.",
                )
        return references

    def _references_for_chunk(
        self,
        ids: list[str],
        warnings: list[str] | None,
    ) -> dict[str, list[str]]:
        try:
            payload = self.client.post_json(
                f"{self.base_url}/paper/batch?fields=references.paperId,referenceCount",
                {"ids": ids},
            )
        except JsonRequestError as error:
            # A chunk can exceed the 10 MB response cap when its papers have very
            # long bibliographies. Splitting once recovers those without turning
            # a size problem into a per-paper request storm. It is only for a
            # refusal of the request itself: a failure that outlasted the
            # client's retries is the service being down, and halving the ids
            # ran the whole retry ladder again at every level (measured: twenty
            # minutes of sleeps for one chunk) to reach the same answer.
            if len(ids) > 1 and not error.transient:
                midpoint = len(ids) // 2
                return {
                    **self._references_for_chunk(ids[:midpoint], warnings),
                    **self._references_for_chunk(ids[midpoint:], warnings),
                }
            # A single-paper request cannot be oversized, so this is a real
            # failure that survived the client's retries; kill the run.
            named = ids[0] if len(ids) == 1 else f"{len(ids)} papers"
            raise JsonRequestError(
                f"Semantic Scholar reference fetch failed after retries for {named}: {error}",
                transient=error.transient,
            ) from error

        references: dict[str, list[str]] = {}
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, dict) or not item.get("paperId"):
                continue
            # The `references` field being absent — or empty while S2's own
            # referenceCount says the bibliography exists — means S2 did not
            # hydrate it, not that the paper cites nothing. Recording those as
            # empty poisons the citation graph, so leave the papers out and
            # let the caller retry them.
            raw_references = item.get("references")
            if raw_references is None:
                continue
            if raw_references == [] and (item.get("referenceCount") or 0) > 0:
                continue
            references[str(item["paperId"])] = [
                str(reference["paperId"])
                for reference in item.get("references") or []
                if isinstance(reference, dict) and reference.get("paperId")
            ]
        return references

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
        """Fetch full source metadata, including Semantic Scholar's own TLDRs."""

        ids = _unique_ids(paper_ids)
        details: dict[str, dict[str, Any]] = {}
        for chunk in _chunked(ids, SEMANTIC_SCHOLAR_BATCH_ID_LIMIT):
            details.update(self._details_for_chunk(chunk))
        # The batch endpoint answers 200 with `null` for some ids under load,
        # and that answer is cached under the request that got it. Asking for
        # the missing ids alone is a different request, so it can be answered
        # afresh; ids still missing after that are unknown to the provider.
        missing = [paper_id for paper_id in ids if paper_id not in details]
        if missing:
            for chunk in _chunked(missing, SEMANTIC_SCHOLAR_REFERENCE_RETRY_CHUNK_SIZE):
                details.update(self._details_for_chunk(chunk))
            still_missing = [paper_id for paper_id in missing if paper_id not in details]
            if still_missing:
                _append_warning(
                    warnings,
                    f"Semantic Scholar returned no metadata for {len(still_missing)} of "
                    f"{len(ids)} papers even after a retry.",
                )
        return details

    def _details_for_chunk(self, chunk: list[str]) -> dict[str, dict[str, Any]]:
        try:
            payload = self.client.post_json(
                f"{self.base_url}/paper/batch?fields={SEMANTIC_SCHOLAR_DETAIL_FIELDS}",
                {"ids": chunk},
            )
        except JsonRequestError as error:
            raise JsonRequestError(
                f"Semantic Scholar paper metadata fetch failed after retries: {error}"
            ) from error
        return {
            str(item["paperId"]): item
            for item in payload
            if isinstance(item, dict) and item.get("paperId")
        }


def s2_api_key() -> str | None:
    return os.environ.get("S2_API_KEY") or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")


def _unique_ids(paper_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(paper_id for paper_id in paper_ids if paper_id))


def _chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


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
        tldr=_tldr_text(item),
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
        semantic_scholar_metadata=semantic_scholar_metadata(item),
    )
    paper.is_survey = looks_like_survey(paper.title, paper.publication_types)
    return paper


def _tldr_text(item: dict[str, Any]) -> str:
    """Semantic Scholar's own one-sentence summary, when the response has one.

    Only the paper-detail endpoint returns `tldr`; bulk search never does, so
    papers straight out of a search carry an empty string here.
    """

    text = (item.get("tldr") or {}).get("text") if isinstance(item.get("tldr"), dict) else None
    return " ".join(str(text).split()) if text else ""


def semantic_scholar_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Return the documented paper fields without discarding provider data."""

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
