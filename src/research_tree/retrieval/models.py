from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class Paper:
    title: str
    abstract: str = ""
    # Semantic Scholar's own one-sentence summary. Bulk search does not return
    # it, so it stays empty until `attach_semantic_scholar_tldrs` fills it in.
    tldr: str = ""
    year: int | None = None
    publication_date: date | None = None
    venue: str = ""
    authors: list[str] = field(default_factory=list)
    doi: str | None = None
    arxiv_id: str | None = None
    semantic_scholar_id: str | None = None
    citation_count: int | None = None
    publication_types: list[str] = field(default_factory=list)
    url: str | None = None
    semantic_scholar_metadata: dict[str, Any] = field(default_factory=dict)
    found_by: set[str] = field(default_factory=set)
    is_survey: bool = False
    flagged_off_topic: bool = False
    frontier_pick: bool = False
    cross_encoder_relevance: float = 0.0
    citations_per_year: float = 0.0
    age_adjusted_citation_score: float = 0.0
    final_score: float = 0.0

    def document_text(self) -> str:
        if self.abstract:
            return f"{self.title}\n\n{self.abstract}"
        return self.title

    def display_id(self) -> str:
        return (
            self.semantic_scholar_id
            or self.doi
            or self.arxiv_id
            or self.title
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "paper_id": self.display_id(),
            "title": self.title,
            "abstract": self.abstract,
            "tldr": self.tldr,
            "year": self.year,
            "publication_date": (
                self.publication_date.isoformat() if self.publication_date else None
            ),
            "venue": self.venue,
            "authors": self.authors,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "primary_link": self.url,
            "citation_count": self.citation_count,
            "semantic_scholar_metadata": self.semantic_scholar_metadata,
            "citations_per_year": self.citations_per_year,
            "is_survey": self.is_survey,
            "flagged_off_topic": self.flagged_off_topic,
            "frontier_pick": self.frontier_pick,
            "cross_encoder_relevance": self.cross_encoder_relevance,
            "age_adjusted_citation_score": self.age_adjusted_citation_score,
            "found_by": sorted(self.found_by),
        }
