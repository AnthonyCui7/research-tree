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
    existing.found_by.update(incoming.found_by)
    existing.is_survey = existing.is_survey or incoming.is_survey
    return existing


def dedupe_papers(papers: Iterable[Paper]) -> list[Paper]:
    papers_by_key: dict[str, Paper] = {}
    aliases: dict[str, str] = {}

    for paper in papers:
        keys = identity_keys(paper)
        canonical_key = _find_existing_key(keys, aliases)
        if canonical_key is None:
            canonical_key = keys[0] if keys else stable_paper_key(paper)
            papers_by_key[canonical_key] = paper
        else:
            papers_by_key[canonical_key] = merge_papers(
                papers_by_key[canonical_key],
                paper,
            )

        for key in identity_keys(papers_by_key[canonical_key]):
            aliases[key] = canonical_key

    return list(papers_by_key.values())


def _find_existing_key(keys: list[str], aliases: dict[str, str]) -> str | None:
    for key in keys:
        if key in aliases:
            return aliases[key]
    return None


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
