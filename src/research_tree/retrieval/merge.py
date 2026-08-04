from __future__ import annotations

import hashlib
from collections.abc import Iterable

from research_tree.retrieval.models import Paper
from research_tree.retrieval.text import (
    normalize_arxiv_id,
    normalize_doi,
    normalize_title,
)


def identity_keys(paper: Paper) -> list[str]:
    keys: list[str] = []
    doi = normalize_doi(paper.doi)
    arxiv_id = normalize_arxiv_id(paper.arxiv_id)
    normalized_title = normalize_title(paper.title)
    if doi:
        keys.append(f"doi:{doi}")
    if arxiv_id:
        keys.append(f"arxiv:{arxiv_id}")
    if paper.semantic_scholar_id:
        keys.append(f"s2:{paper.semantic_scholar_id}")
    if normalized_title:
        keys.append(f"title:{normalized_title}")
    return keys


def stable_paper_key(paper: Paper) -> str:
    keys = identity_keys(paper)
    if keys:
        return keys[0]
    digest = hashlib.sha256(paper.title.encode("utf-8")).hexdigest()[:16]
    return f"unknown:{digest}"


def merge_papers(existing: Paper, incoming: Paper) -> Paper:
    """Fold `incoming` into `existing` and return the surviving record.

    Note for callers: `citation_count` can go *up* here (the merge keeps the
    larger of the two), which invalidates anything derived from it —
    `citations_per_year` and `age_adjusted_citation_score`. This module owns
    identity, not scoring, and has no clock, so it cannot recompute them.
    Any caller that dedupes papers it has already scored must re-score the
    result (see `_score_age_adjusted_citations` in `candidate_preparation`).
    """

    existing.title = _prefer_longer(existing.title, incoming.title)
    existing.abstract = _prefer_longer(existing.abstract, incoming.abstract)
    existing.year = existing.year or incoming.year
    existing.publication_date = existing.publication_date or incoming.publication_date
    existing.venue = existing.venue or incoming.venue
    existing.authors = existing.authors or incoming.authors
    existing.doi = normalize_doi(existing.doi) or normalize_doi(incoming.doi)
    existing.arxiv_id = normalize_arxiv_id(existing.arxiv_id) or normalize_arxiv_id(
        incoming.arxiv_id
    )
    existing.semantic_scholar_id = (
        existing.semantic_scholar_id or incoming.semantic_scholar_id
    )
    existing.citation_count = _max_optional_int(
        existing.citation_count,
        incoming.citation_count,
    )
    existing.publication_types = sorted(
        set(existing.publication_types) | set(incoming.publication_types)
    )
    existing.url = existing.url or incoming.url
    existing.semantic_scholar_metadata = (
        existing.semantic_scholar_metadata or incoming.semantic_scholar_metadata
    )
    existing.found_by.update(incoming.found_by)
    existing.is_survey = existing.is_survey or incoming.is_survey
    return existing


def dedupe_papers(papers: Iterable[Paper]) -> list[Paper]:
    papers_by_key: dict[str, Paper] = {}
    aliases: dict[str, str] = {}

    for paper in papers:
        keys = identity_keys(paper)
        canonical_keys = _existing_keys(keys, aliases)
        if not canonical_keys:
            canonical_key = keys[0] if keys else stable_paper_key(paper)
            papers_by_key[canonical_key] = paper
        else:
            # One record can match several already-registered records at once:
            # a DOI-only record and an arXiv-only record are two entries until
            # a third arrives carrying both identifiers and proves they are the
            # same paper. Fold all of them together, or the losers survive as
            # duplicates whose aliases now point at a record they are not in.
            canonical_key, *superseded = canonical_keys
            merged = papers_by_key[canonical_key]
            for other_key in superseded:
                merged = merge_papers(merged, papers_by_key.pop(other_key))
            if superseded:
                folded = set(superseded)
                for key, target in list(aliases.items()):
                    if target in folded:
                        aliases[key] = canonical_key
            papers_by_key[canonical_key] = merge_papers(merged, paper)

        for key in identity_keys(papers_by_key[canonical_key]):
            aliases[key] = canonical_key

    return list(papers_by_key.values())


def _existing_keys(keys: list[str], aliases: dict[str, str]) -> list[str]:
    """Canonical keys already registered for any of `keys`, in match order."""

    found: list[str] = []
    for key in keys:
        canonical = aliases.get(key)
        if canonical is not None and canonical not in found:
            found.append(canonical)
    return found


def _prefer_longer(existing: str, incoming: str) -> str:
    if not existing:
        return incoming
    if not incoming:
        return existing
    return incoming if len(incoming) > len(existing) else existing


def _max_optional_int(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)
