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


def build_workspace_chat_context(
    *,
    workspace: Mapping[str, Any],
    candidate_artifact: Mapping[str, Any] | None = None,
    include_similar_papers: bool = True,
    max_similar_per_paper: int = 10,
    target_branch_id: str | None = None,
    target_paper_ids: list[str] | None = None,
) -> dict[str, Any]:
    target_paper_set = set(target_paper_ids or [])
    visible_cards = _visible_paper_cards(
        workspace=workspace,
        include_similar_papers=include_similar_papers,
        max_similar_per_paper=max_similar_per_paper,
        target_paper_ids=target_paper_set,
    )
    similar_papers_context = {
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
        "root_overview": _root_overview(workspace),
        "branch_summaries": branch_summaries,
        "paper_paths": _paper_paths(workspace, target_branch_id=target_branch_id),
        "reading_order": _reading_order(workspace, target_paper_set),
        "visible_paper_cards": visible_cards,
        "visible_paper_count": len((workspace.get("paper_cards") or {})),
        "similar_papers_context": similar_papers_context,
        "off_path_papers": _off_path_papers(workspace),
        "candidate_pool_summary": _candidate_pool_summary(candidate_artifact),
        "provenance_warnings": _provenance_warnings(workspace, candidate_artifact),
    }


def build_workspace_summary(workspace: Mapping[str, Any]) -> dict[str, Any]:
    paper_cards = workspace.get("paper_cards") or {}
    tree = workspace.get("tree") or {}
    nodes = tree.get("nodes") or []
    return {
        "workspace_id": workspace.get("workspace_id"),
        "topic": workspace.get("topic"),
        "title": workspace.get("title"),
        "branch_count": len(nodes) if isinstance(nodes, list) else 0,
        "visible_paper_count": len(paper_cards) if isinstance(paper_cards, Mapping) else 0,
    }


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
        payload = {
            "paper_id": card.get("paper_id") or paper_id,
            "title": card.get("title"),
            "authors": card.get("authors") or [],
            "year": card.get("year"),
            "venue": card.get("venue"),
            "primary_tree_location": card.get("primary_tree_location"),
            "secondary_tags": card.get("secondary_tags") or [],
            "paper_role": card.get("paper_role"),
            "one_sentence_contribution": card.get("one_sentence_contribution"),
            "core_idea": card.get("core_idea"),
            "why_it_belongs": card.get("why_it_belongs"),
            "read_before": card.get("read_before") or [],
            "read_after": card.get("read_after") or [],
        }
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


def _off_path_papers(workspace: Mapping[str, Any]) -> list[dict[str, Any]]:
    off_path: list[dict[str, Any]] = []
    for item in workspace.get("discarded_candidates") or []:
        if isinstance(item, Mapping):
            off_path.append(dict(item))
    return off_path


def _candidate_pool_summary(
    candidate_artifact: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(candidate_artifact, Mapping):
        return {"candidate_count": 0, "candidates": []}
    candidates = candidate_pool_from_artifact(candidate_artifact)
    return {
        "schema_version": candidate_artifact.get("schema_version"),
        "topic": candidate_artifact.get("topic"),
        "candidate_pool_order": candidate_artifact.get("candidate_pool_order"),
        "citation_age_exponent": candidate_artifact.get("citation_age_exponent"),
        "candidate_count": len(candidates),
        "candidates": candidates[:50],
    }


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
        "age_adjusted_rank": paper.get("age_adjusted_rank"),
        "cross_encoder_rank": paper.get("cross_encoder_rank"),
        "is_survey": paper.get("is_survey"),
    }

