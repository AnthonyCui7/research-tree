from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


WORKSPACE_SCHEMA_VERSION = "research_tree_workspace.v1"
CANDIDATE_ARTIFACT_SCHEMA_VERSION = "llm_candidate_papers.v1"
PAPER_DATABASE_SCHEMA_VERSION = "s2_bulk_deduped_paper_database.v1"

# These are deliberately broad. Roles support construction and reading-path
# decisions; source metadata and the concise importance note orient the reader.
PAPER_ROLES = frozenset(
    {
        "foundational",
        "survey",
        "method",
        "benchmark",
        "evaluation",
        "critique",
        "application",
        "other",
    }
)


@dataclass(frozen=True)
class CandidatePaperMetadata:
    paper_id: str
    title: str
    abstract: str = ""
    year: int | None = None
    publication_date: str | None = None
    authors: list[str] = field(default_factory=list)
    venue: str = ""
    doi: str | None = None
    arxiv_id: str | None = None
    primary_link: str | None = None
    arxiv_link: str | None = None
    s2_link: str | None = None
    doi_link: str | None = None
    citation_count: int | None = None
    semantic_scholar_metadata: dict[str, Any] = field(default_factory=dict)
    authority_rank: int | None = None
    authority_score: float | None = None
    hub_score: float | None = None
    in_degree: int | None = None
    root_set_member: bool = False
    snowballed: bool = False
    flagged_off_topic: bool = False
    frontier_pick: bool = False
    age_years: float | None = None
    age_adjusted_citation_score: float | None = None
    citations_per_year: float | None = None
    is_survey: bool = False
    found_by: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "CandidatePaperMetadata":
        paper_id = paper_id_from_mapping(payload)
        if not paper_id:
            raise ValueError(f"candidate paper is missing a usable paper_id: {payload}")
        return cls(
            paper_id=paper_id,
            title=str(payload.get("title") or ""),
            abstract=str(payload.get("abstract") or ""),
            year=_optional_int(payload.get("year")),
            publication_date=_optional_str(payload.get("publication_date")),
            authors=[
                str(author)
                for author in payload.get("authors") or []
                if isinstance(author, str)
            ],
            venue=str(payload.get("venue") or ""),
            doi=_optional_str(payload.get("doi")),
            arxiv_id=_optional_str(payload.get("arxiv_id")),
            primary_link=_optional_str(payload.get("primary_link")),
            arxiv_link=_optional_str(payload.get("arxiv_link")),
            s2_link=_optional_str(payload.get("s2_link")),
            doi_link=_optional_str(payload.get("doi_link")),
            citation_count=_optional_int(payload.get("citation_count")),
            semantic_scholar_metadata=_dict(payload.get("semantic_scholar_metadata")),
            authority_rank=_optional_int(payload.get("authority_rank")),
            authority_score=_optional_float(payload.get("authority_score")),
            hub_score=_optional_float(payload.get("hub_score")),
            in_degree=_optional_int(payload.get("in_degree")),
            root_set_member=bool(
                # Artifacts written before the Kleinberg-aligned rename used
                # "base_set_member" for the same fact.
                payload.get("root_set_member", payload.get("base_set_member", False))
            ),
            snowballed=bool(payload.get("snowballed", False)),
            flagged_off_topic=bool(payload.get("flagged_off_topic", False)),
            frontier_pick=bool(payload.get("frontier_pick", False)),
            age_years=_optional_float(payload.get("age_years")),
            age_adjusted_citation_score=_optional_float(
                payload.get("age_adjusted_citation_score")
            ),
            citations_per_year=_optional_float(payload.get("citations_per_year")),
            is_survey=bool(payload.get("is_survey", False)),
            found_by=[
                str(value)
                for value in payload.get("found_by") or []
                if isinstance(value, str)
            ],
        )

    def document_text(self) -> str:
        if self.abstract:
            return f"{self.title}\n\n{self.abstract}"
        return self.title

    def to_json(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "abstract": self.abstract,
            "year": self.year,
            "publication_date": self.publication_date,
            "authors": self.authors,
            "venue": self.venue,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "primary_link": self.primary_link,
            "arxiv_link": self.arxiv_link,
            "s2_link": self.s2_link,
            "doi_link": self.doi_link,
            "citation_count": self.citation_count,
            "semantic_scholar_metadata": self.semantic_scholar_metadata,
            "authority_rank": self.authority_rank,
            "authority_score": self.authority_score,
            "hub_score": self.hub_score,
            "in_degree": self.in_degree,
            "root_set_member": self.root_set_member,
            "snowballed": self.snowballed,
            "flagged_off_topic": self.flagged_off_topic,
            "frontier_pick": self.frontier_pick,
            "age_years": self.age_years,
            "age_adjusted_citation_score": self.age_adjusted_citation_score,
            "citations_per_year": self.citations_per_year,
            "is_survey": self.is_survey,
            "found_by": self.found_by,
        }


@dataclass(frozen=True)
class WorkspaceDocument:
    schema_version: str
    workspace_id: str
    topic: str
    title: str
    scope: dict[str, Any]
    source_candidate_artifact: dict[str, Any]
    root: dict[str, Any]
    tree: dict[str, Any]
    paper_paths: list[dict[str, Any]]
    paper_cards: dict[str, dict[str, Any]]
    reading_order: list[dict[str, Any]]
    comparison_tables: list[dict[str, Any]]
    discarded_candidates: list[dict[str, Any]]
    provenance: dict[str, Any]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "WorkspaceDocument":
        return cls(
            schema_version=str(payload.get("schema_version") or ""),
            workspace_id=str(payload.get("workspace_id") or ""),
            topic=str(payload.get("topic") or ""),
            title=str(payload.get("title") or ""),
            scope=_dict(payload.get("scope")),
            source_candidate_artifact=_dict(payload.get("source_candidate_artifact")),
            root=_dict(payload.get("root")),
            tree=_dict(payload.get("tree")),
            paper_paths=_list_of_dicts(payload.get("paper_paths")),
            paper_cards={
                str(paper_id): _dict(card)
                for paper_id, card in _dict(payload.get("paper_cards")).items()
            },
            reading_order=_list_of_dicts(payload.get("reading_order")),
            comparison_tables=_list_of_dicts(payload.get("comparison_tables")),
            discarded_candidates=_list_of_dicts(payload.get("discarded_candidates")),
            provenance=_dict(payload.get("provenance")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "topic": self.topic,
            "title": self.title,
            "scope": self.scope,
            "source_candidate_artifact": self.source_candidate_artifact,
            "root": self.root,
            "tree": self.tree,
            "paper_paths": self.paper_paths,
            "paper_cards": self.paper_cards,
            "reading_order": self.reading_order,
            "comparison_tables": self.comparison_tables,
            "discarded_candidates": self.discarded_candidates,
            "provenance": self.provenance,
        }


def paper_id_from_mapping(payload: Mapping[str, Any]) -> str:
    for field_name in (
        "paper_id",
        "semantic_scholar_id",
        "doi",
        "arxiv_id",
        "openalex_id",
        "title",
    ):
        value = payload.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def candidate_papers_from_artifact(
    candidate_artifact: Mapping[str, Any],
) -> dict[str, CandidatePaperMetadata]:
    candidates: dict[str, CandidatePaperMetadata] = {}
    for key in ("non_survey_papers", "survey_papers"):
        for item in candidate_artifact.get(key) or []:
            if not isinstance(item, Mapping):
                continue
            paper = CandidatePaperMetadata.from_mapping(item)
            candidates[paper.paper_id] = paper
    return candidates


def survey_paper_ids_from_artifact(candidate_artifact: Mapping[str, Any]) -> set[str]:
    survey_ids: set[str] = set()
    for item in candidate_artifact.get("survey_papers") or []:
        if isinstance(item, Mapping):
            paper_id = paper_id_from_mapping(item)
            if paper_id:
                survey_ids.add(paper_id)
    return survey_ids


def paper_database_from_artifact(payload: Any) -> list[CandidatePaperMetadata]:
    if isinstance(payload, Mapping):
        raw_papers = payload.get("papers") or []
    elif isinstance(payload, list):
        raw_papers = payload
    else:
        raw_papers = []

    papers: list[CandidatePaperMetadata] = []
    seen_ids: set[str] = set()
    for item in raw_papers:
        if not isinstance(item, Mapping):
            continue
        try:
            paper = CandidatePaperMetadata.from_mapping(item)
        except ValueError:
            continue
        if paper.paper_id in seen_ids:
            continue
        seen_ids.add(paper.paper_id)
        papers.append(paper)
    return papers


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
