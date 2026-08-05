from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def workspace_version_hash(workspace: Mapping[str, Any]) -> str:
    payload = json.dumps(
        workspace,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def atomic_branch_count(tree_nodes: Any) -> int:
    """Count the atomic branches — the leaves that actually carry reading paths.

    A parent branch is a grouping of its children, so counting it alongside them
    reports the same work twice. Nodes written before `is_leaf` was required fall
    back to having no children of their own.
    """

    nodes = [node for node in tree_nodes if isinstance(node, Mapping)] if isinstance(tree_nodes, list) else []
    parent_ids = {str(node.get("parent_id")) for node in nodes if node.get("parent_id")}
    count = 0
    for node in nodes:
        is_leaf = node.get("is_leaf")
        if is_leaf is None:
            is_leaf = str(node.get("node_id")) not in parent_ids
        count += 1 if is_leaf else 0
    return count


def build_workspace_chat_context(
    *,
    workspace: Mapping[str, Any],
    candidate_artifact: Mapping[str, Any] | None = None,
    include_similar_papers: bool = False,
    max_similar_per_paper: int = 10,
    target_branch_id: str | None = None,
    target_paper_ids: list[str] | None = None,
    similar_papers_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target_paper_set = set(target_paper_ids or [])
    visible_cards = _visible_paper_cards(
        workspace=workspace,
        include_similar_papers=include_similar_papers,
        max_similar_per_paper=max_similar_per_paper,
        target_paper_ids=target_paper_set,
    )
    visible_similar_papers_context = {
        paper_id: card.get("similar_papers") or []
        for paper_id, card in visible_cards.items()
        if card.get("similar_papers")
    }
    branch_summaries = _branch_summaries(
        workspace,
        target_branch_id=target_branch_id,
    )
    return {
        "workspace_id": workspace.get("workspace_id"),
        "topic": workspace.get("topic"),
        "title": workspace.get("title"),
        "workspace": _workspace_prompt_payload(workspace),
        "root_overview": _root_overview(workspace),
        "branch_summaries": branch_summaries,
        "paper_paths": _paper_paths(workspace, target_branch_id=target_branch_id),
        "reading_order": _reading_order(workspace, target_paper_set),
        "visible_paper_cards": visible_cards,
        "visible_paper_count": len((workspace.get("paper_cards") or {})),
        "similar_papers_context": (
            dict(similar_papers_context)
            if isinstance(similar_papers_context, Mapping)
            else visible_similar_papers_context
        ),
        "off_path_papers": [],
        "source_candidate_artifact": _source_candidate_artifact_reference(candidate_artifact),
        "provenance_warnings": _provenance_warnings(workspace, candidate_artifact),
    }


def build_workspace_summary(workspace: Mapping[str, Any]) -> dict[str, Any]:
    """The agent loop's opening picture of the workspace.

    Shape plus a one-line gist per paper: branches with their tree structure,
    and each paper's title, leading authors, year, citation count, and TLDR.
    That answers shape, authorship, and recency questions without a tool
    round; abstracts, importance text, and full text stay behind the read
    tools because the loop replays this summary every round.
    """

    paper_cards = workspace.get("paper_cards") or {}
    tree = workspace.get("tree") or {}
    nodes = tree.get("nodes") or []
    return {
        "workspace_id": workspace.get("workspace_id"),
        "topic": workspace.get("topic"),
        "title": workspace.get("title"),
        "branch_count": atomic_branch_count(nodes),
        "visible_paper_count": len(paper_cards) if isinstance(paper_cards, Mapping) else 0,
        "branches": [
            {
                "node_id": node.get("node_id"),
                "label": node.get("label"),
                "parent_id": node.get("parent_id"),
                "is_leaf": node.get("is_leaf"),
            }
            for node in nodes
            if isinstance(node, Mapping)
        ],
        "papers": [
            _paper_summary_line(str(paper_id), card)
            for paper_id, card in paper_cards.items()
            if isinstance(card, Mapping)
        ] if isinstance(paper_cards, Mapping) else [],
    }


def _paper_summary_line(paper_id: str, card: Mapping[str, Any]) -> dict[str, Any]:
    """One summary row per paper; sparse cards simply omit the missing keys."""

    line: dict[str, Any] = {"paper_id": paper_id, "title": card.get("title")}
    authors = [
        str(author).strip()
        for author in (card.get("authors") or [])
        if str(author).strip()
    ]
    if authors:
        # Complete, not truncated: "which papers here are by X" must see a
        # fourth author too, and a wrong-but-confident answer costs more than
        # the tokens (measured live: truncating to three hid two Jason Wei
        # papers and the summary-grounded answer was flatly incomplete).
        line["authors"] = authors
    if card.get("year") is not None:
        line["year"] = card.get("year")
    if card.get("citation_count") is not None:
        line["citation_count"] = card.get("citation_count")
    tldr = card.get("tldr")
    if isinstance(tldr, str) and tldr.strip():
        line["tldr"] = tldr.strip()[:270]
    return line


def candidate_pool_from_artifact(
    candidate_artifact: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(candidate_artifact, Mapping):
        return []
    pool: list[dict[str, Any]] = []
    for key in ("non_survey_papers", "survey_papers"):
        for item in candidate_artifact.get(key) or []:
            if isinstance(item, Mapping):
                pool.append(_paper_summary(item))
    return pool


def _root_overview(workspace: Mapping[str, Any]) -> dict[str, Any]:
    root = workspace.get("root") if isinstance(workspace.get("root"), Mapping) else {}
    return {
        "node_id": root.get("node_id"),
        "label": root.get("label"),
        "overview": root.get("overview"),
        "root_survey_type": root.get("root_survey_type"),
        "survey_anchor_paper_ids": root.get("survey_anchor_paper_ids") or [],
        "representative_paper_ids": root.get("representative_paper_ids") or [],
        "suggested_reading_direction": root.get("suggested_reading_direction"),
    }


def _branch_summaries(
    workspace: Mapping[str, Any],
    *,
    target_branch_id: str | None,
) -> list[dict[str, Any]]:
    tree = workspace.get("tree") if isinstance(workspace.get("tree"), Mapping) else {}
    branches: list[dict[str, Any]] = []
    for node in tree.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        node_id = str(node.get("node_id") or "")
        if target_branch_id and node_id != target_branch_id and node.get("parent_id") != target_branch_id:
            continue
        branches.append(
            {
                "node_id": node_id,
                "parent_id": node.get("parent_id"),
                "label": node.get("label"),
                "description": node.get("description"),
                "why_it_matters": node.get("why_it_matters"),
                "is_leaf": node.get("is_leaf"),
                "child_node_ids": node.get("child_node_ids") or [],
                "primary_paper_ids": node.get("primary_paper_ids") or [],
                "secondary_paper_ids": node.get("secondary_paper_ids") or [],
                "survey_anchor_paper_id": node.get("survey_anchor_paper_id"),
                "tags": node.get("tags") or [],
                "open_questions": node.get("open_questions") or [],
            }
        )
    return branches


def _paper_paths(
    workspace: Mapping[str, Any],
    *,
    target_branch_id: str | None,
) -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []
    for path in workspace.get("paper_paths") or []:
        if not isinstance(path, Mapping):
            continue
        if target_branch_id and path.get("branch_node_id") != target_branch_id:
            continue
        paths.append(
            {
                "path_id": path.get("path_id"),
                "branch_node_id": path.get("branch_node_id"),
                "path_type": path.get("path_type"),
                "label": path.get("label"),
                "description": path.get("description"),
                "paper_ids": path.get("paper_ids") or [],
                "paper_steps": path.get("paper_steps") or [],
                "rationale": path.get("rationale"),
            }
        )
    return paths


def _visible_paper_cards(
    *,
    workspace: Mapping[str, Any],
    include_similar_papers: bool,
    max_similar_per_paper: int,
    target_paper_ids: set[str],
) -> dict[str, dict[str, Any]]:
    cards = workspace.get("paper_cards") if isinstance(workspace.get("paper_cards"), Mapping) else {}
    visible: dict[str, dict[str, Any]] = {}
    for paper_id, card in cards.items():
        if not isinstance(card, Mapping):
            continue
        if target_paper_ids and str(paper_id) not in target_paper_ids:
            continue
        payload = dict(card)
        payload["paper_id"] = payload.get("paper_id") or paper_id
        if not include_similar_papers:
            payload.pop("similar_papers", None)
        if include_similar_papers:
            payload["similar_papers"] = list(card.get("similar_papers") or [])[
                :max_similar_per_paper
            ]
        visible[str(paper_id)] = payload
    return visible


def _reading_order(
    workspace: Mapping[str, Any],
    target_paper_ids: set[str],
) -> list[dict[str, Any]]:
    order: list[dict[str, Any]] = []
    for entry in workspace.get("reading_order") or []:
        if not isinstance(entry, Mapping):
            continue
        paper_id = str(entry.get("paper_id") or "")
        if target_paper_ids and paper_id not in target_paper_ids:
            continue
        order.append(
            {
                "order": entry.get("order"),
                "paper_id": paper_id,
                "reason": entry.get("reason"),
            }
        )
    return order


def _source_candidate_artifact_reference(
    candidate_artifact: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(candidate_artifact, Mapping):
        return {}
    return {
        "schema_version": candidate_artifact.get("schema_version"),
        "topic": candidate_artifact.get("topic"),
        "workspace": candidate_artifact.get("workspace"),
        "candidate_set_purpose": candidate_artifact.get("candidate_set_purpose"),
        "candidate_pool_order": candidate_artifact.get("candidate_pool_order"),
        "citation_age_exponent": candidate_artifact.get("citation_age_exponent"),
        "non_survey_count": len(candidate_artifact.get("non_survey_papers") or []),
        "survey_count": len(candidate_artifact.get("survey_papers") or []),
    }


def _workspace_prompt_payload(workspace: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(workspace)
    payload["source_candidate_artifact"] = _source_candidate_artifact_reference(
        workspace.get("source_candidate_artifact")
        if isinstance(workspace.get("source_candidate_artifact"), Mapping)
        else None
    )
    payload["discarded_candidates"] = []
    paper_cards = payload.get("paper_cards")
    if isinstance(paper_cards, Mapping):
        payload["paper_cards"] = {
            str(paper_id): _paper_card_without_external_candidates(card)
            for paper_id, card in paper_cards.items()
            if isinstance(card, Mapping)
        }
    return payload


def _paper_card_without_external_candidates(card: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(card)
    payload.pop("similar_papers", None)
    return payload


def _provenance_warnings(
    workspace: Mapping[str, Any],
    candidate_artifact: Mapping[str, Any] | None,
) -> list[str]:
    warnings: list[str] = []
    provenance = workspace.get("provenance")
    if isinstance(provenance, Mapping):
        warnings.extend(str(item) for item in provenance.get("warnings") or [])
    if isinstance(candidate_artifact, Mapping):
        warnings.extend(str(item) for item in candidate_artifact.get("warnings") or [])
    return warnings


def _paper_summary(paper: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": paper.get("paper_id"),
        "title": paper.get("title"),
        "year": paper.get("year"),
        "venue": paper.get("venue"),
        "citation_count": paper.get("citation_count"),
        "authority_rank": paper.get("authority_rank"),
        "in_degree": paper.get("in_degree"),
        "is_survey": paper.get("is_survey"),
    }
