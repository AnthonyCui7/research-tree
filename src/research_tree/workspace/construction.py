from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from research_tree.artifacts import write_json_file, write_text_file
from research_tree.retrieval.semantic_scholar import (
    SemanticScholarClient,
    paper_from_semantic_scholar,
    semantic_scholar_metadata,
)
from research_tree.workspace.prompts import (
    WORKSPACE_CONSTRUCTION_PROMPT_VERSION,
    build_workspace_prompt,
)
from research_tree.workspace.prompts.paper_tldr import (
    PAPER_TLDR_PROMPT_VERSION,
    build_missing_paper_tldr_prompt,
)
from research_tree.workspace.schemas import (
    CandidatePaperMetadata,
    PAPER_ROLES,
    WORKSPACE_SCHEMA_VERSION,
    candidate_papers_from_artifact,
)
from research_tree.workspace.serialization import (
    load_candidate_artifact,
    load_json_artifact,
    parse_workspace_output,
    write_workspace_artifacts,
)
from research_tree.workspace.structured_outputs import workspace_response_format
from research_tree.workspace.validation import WorkspaceValidationResult, validate_workspace


DEFAULT_WORKSPACE_LLM_TIMEOUT_SECONDS = 600.0
DEFAULT_WORKSPACE_LLM_REASONING_EFFORT = "medium"
DEFAULT_WORKSPACE_LLM_MAX_OUTPUT_TOKENS: int | None = None
DEFAULT_WORKSPACE_LLM_TEXT_VERBOSITY = "low"
DEFAULT_WORKSPACE_LLM_RESPONSE_FORMAT = "json_schema"
WORKSPACE_LLM_REASONING_EFFORTS = {
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
}
WORKSPACE_LLM_TEXT_VERBOSITIES = {"low", "medium", "high"}
WORKSPACE_LLM_RESPONSE_FORMATS = {"json_schema", "json_object"}


@dataclass(frozen=True)
class WorkspaceLlmResponse:
    text: str
    raw_response: dict[str, Any]


@dataclass(frozen=True)
class WorkspaceConstructionResult:
    workspace: dict[str, Any]
    validation: WorkspaceValidationResult
    output_paths: dict[str, Path]


ConstructionMode = Literal[
    "initial_workspace",
    "agent_modify_workspace",
    "branch_expansion",
    "workspace_repair",
]


class OpenAIResponsesWorkspaceClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout_seconds: float = DEFAULT_WORKSPACE_LLM_TIMEOUT_SECONDS,
        reasoning_effort: str | None = DEFAULT_WORKSPACE_LLM_REASONING_EFFORT,
        max_output_tokens: int | None = DEFAULT_WORKSPACE_LLM_MAX_OUTPUT_TOKENS,
        text_verbosity: str | None = DEFAULT_WORKSPACE_LLM_TEXT_VERBOSITY,
        response_format: str = DEFAULT_WORKSPACE_LLM_RESPONSE_FORMAT,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for workspace construction.")
        self.timeout_seconds = max(timeout_seconds, 1.0)
        if (
            reasoning_effort is not None
            and reasoning_effort not in WORKSPACE_LLM_REASONING_EFFORTS
        ):
            raise ValueError(f"unsupported reasoning effort: {reasoning_effort}")
        if (
            text_verbosity is not None
            and text_verbosity not in WORKSPACE_LLM_TEXT_VERBOSITIES
        ):
            raise ValueError(f"unsupported text verbosity: {text_verbosity}")
        if max_output_tokens is not None and max_output_tokens < 16:
            raise ValueError("max_output_tokens must be at least 16.")
        if response_format not in WORKSPACE_LLM_RESPONSE_FORMATS:
            raise ValueError(f"unsupported response format: {response_format}")
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self.text_verbosity = text_verbosity
        self.response_format = response_format

    def call_workspace_llm(self, *, prompt: str, model: str) -> WorkspaceLlmResponse:
        text_options: dict[str, Any] = {
            "format": (
                workspace_response_format()
                if self.response_format == "json_schema"
                else {"type": "json_object"}
            )
        }
        if self.text_verbosity:
            text_options["verbosity"] = self.text_verbosity
        body = {
            "model": model,
            "input": prompt,
            "text": text_options,
            "tool_choice": "none",
        }
        if self.max_output_tokens is not None:
            body["max_output_tokens"] = self.max_output_tokens
        reasoning_effort = _reasoning_effort_for_model(model, self.reasoning_effort)
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                raw_response = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI workspace LLM call failed: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"OpenAI workspace LLM call failed: {error}") from error
        except (TimeoutError, socket.timeout) as error:
            raise RuntimeError(
                "OpenAI workspace LLM call timed out after "
                f"{self.timeout_seconds:g}s. Retry the command, or raise "
                "--request-timeout-seconds for this one-shot workspace generation."
            ) from error

        workspace_text = _extract_llm_text(raw_response)
        return WorkspaceLlmResponse(text=workspace_text, raw_response=raw_response)


def construct_workspace(
    *,
    candidate_artifact_path: str | Path | None = None,
    candidate_artifact: dict[str, Any] | None = None,
    base_workspace: dict[str, Any] | None = None,
    construction_mode: ConstructionMode = "initial_workspace",
    agent_instruction: str | None = None,
    target_branch_id: str | None = None,
    target_paper_ids: list[str] | None = None,
    similar_papers_context: dict[str, Any] | None = None,
    run_metadata: dict[str, Any] | None = None,
    model: str = "gpt-5.4-mini",
    prompt_version: str = WORKSPACE_CONSTRUCTION_PROMPT_VERSION,
    llm_client: OpenAIResponsesWorkspaceClient | None = None,
    raw_llm_output: str | dict[str, Any] | None = None,
    workspace_id_override: str | None = None,
    semantic_scholar_client: SemanticScholarClient | None = None,
) -> dict[str, Any]:
    """Construct or modify a workspace in memory without writing artifacts."""

    candidate_path = Path(candidate_artifact_path) if candidate_artifact_path else None
    active_candidate_artifact = (
        candidate_artifact
        if candidate_artifact is not None
        else load_candidate_artifact(candidate_path) if candidate_path is not None else None
    )

    if construction_mode == "initial_workspace":
        if active_candidate_artifact is None:
            raise ValueError("candidate_artifact is required for initial workspace construction.")
        prompt_text = build_workspace_prompt(
            active_candidate_artifact,
            prompt_version=prompt_version,
        )
    else:
        if base_workspace is None:
            raise ValueError(f"base_workspace is required for {construction_mode}.")
        prompt_text = _build_agent_workspace_prompt(
            construction_mode=construction_mode,
            user_message=str((run_metadata or {}).get("user_message") or agent_instruction or ""),
            base_workspace=base_workspace,
            proposed_workspace=(run_metadata or {}).get("proposed_workspace"),
            candidate_artifact=active_candidate_artifact,
            agent_instruction=agent_instruction or "",
            target_branch_id=target_branch_id,
            target_paper_ids=target_paper_ids or [],
            similar_papers_context=similar_papers_context,
            run_metadata=run_metadata or {},
        )

    if raw_llm_output is not None:
        raw_output = raw_llm_output
    elif llm_client is not None:
        raw_output = llm_client.call_workspace_llm(
            prompt=prompt_text,
            model=model,
        ).raw_response
    elif os.environ.get("OPENAI_API_KEY"):
        raw_output = OpenAIResponsesWorkspaceClient().call_workspace_llm(
            prompt=prompt_text,
            model=model,
        ).raw_response
    elif construction_mode == "workspace_repair" and isinstance(
        (run_metadata or {}).get("proposed_workspace"),
        dict,
    ):
        raw_output = copy.deepcopy((run_metadata or {})["proposed_workspace"])
    elif base_workspace is not None:
        workspace_copy = copy.deepcopy(base_workspace)
        provenance = workspace_copy.setdefault("provenance", {})
        if isinstance(provenance, dict):
            provenance.setdefault("warnings", [])
            provenance["warnings"].append(
                "No OPENAI_API_KEY was configured; returned base workspace as an unmodified agent proposal."
            )
        raw_output = workspace_copy
    else:
        raise RuntimeError("OPENAI_API_KEY is required for workspace construction.")

    workspace = parse_workspace_output(raw_output)
    normalize_workspace_payload(workspace)
    if active_candidate_artifact is not None:
        materialize_workspace_candidate_references(workspace, active_candidate_artifact)
        fill_paper_card_source_metadata(workspace, active_candidate_artifact)
        ensure_survey_anchor_cards(workspace, active_candidate_artifact)
        if semantic_scholar_client is not None:
            enrich_workspace_papers_from_semantic_scholar(
                workspace,
                semantic_scholar_client,
            )
    if construction_mode == "initial_workspace" and active_candidate_artifact is not None:
        _fill_workspace_metadata(
            workspace=workspace,
            candidate_artifact=active_candidate_artifact,
            candidate_json_path=(candidate_path or Path("<in-memory-candidate-artifact>")),
            model=model,
            prompt_version=prompt_version,
            workspace_id_override=workspace_id_override,
        )
        validation = validate_workspace(workspace, active_candidate_artifact)
        if not validation.is_valid:
            raise ValueError(
                "workspace validation failed: " + "; ".join(validation.errors)
            )
    else:
        workspace.setdefault("schema_version", WORKSPACE_SCHEMA_VERSION)
        if base_workspace is not None:
            workspace.setdefault("workspace_id", base_workspace.get("workspace_id"))
            workspace.setdefault("topic", base_workspace.get("topic"))
            workspace.setdefault("title", base_workspace.get("title"))
    return workspace


def construct_workspace_from_candidates(
    *,
    candidate_json_path: Path,
    model: str,
    prompt_version: str = WORKSPACE_CONSTRUCTION_PROMPT_VERSION,
    output_dir: Path | None = None,
    llm_client: OpenAIResponsesWorkspaceClient | None = None,
    raw_llm_output_json_path: Path | None = None,
    request_timeout_seconds: float = DEFAULT_WORKSPACE_LLM_TIMEOUT_SECONDS,
    reasoning_effort: str | None = DEFAULT_WORKSPACE_LLM_REASONING_EFFORT,
    max_output_tokens: int | None = DEFAULT_WORKSPACE_LLM_MAX_OUTPUT_TOKENS,
    text_verbosity: str | None = DEFAULT_WORKSPACE_LLM_TEXT_VERBOSITY,
    response_format: str = DEFAULT_WORKSPACE_LLM_RESPONSE_FORMAT,
    workspace_id_override: str | None = None,
    semantic_scholar_client: SemanticScholarClient | None = None,
) -> WorkspaceConstructionResult:
    candidate_json_path = candidate_json_path.resolve()
    output_dir = output_dir.resolve() if output_dir else candidate_json_path.parent
    candidate_artifact = load_candidate_artifact(candidate_json_path)
    prompt_text = build_workspace_prompt(
        candidate_artifact,
        prompt_version=prompt_version,
    )
    run_label = _run_label(candidate_json_path.parent)
    write_text_file(
        output_dir / "workspace_prompt.txt",
        prompt_text,
        archive_existing=True,
        run_label=run_label,
    )

    if raw_llm_output_json_path is not None:
        raw_llm_output = load_json_artifact(raw_llm_output_json_path)
    else:
        active_llm_client = llm_client or OpenAIResponsesWorkspaceClient(
            timeout_seconds=request_timeout_seconds,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            text_verbosity=text_verbosity,
            response_format=response_format,
        )
        response = active_llm_client.call_workspace_llm(prompt=prompt_text, model=model)
        raw_llm_output = response.raw_response

    try:
        workspace = parse_workspace_output(raw_llm_output)
    except ValueError:
        write_json_file(
            output_dir / "workspace_raw_llm_output.json",
            raw_llm_output,
            archive_existing=True,
            run_label=run_label,
        )
        raise
    normalize_workspace_payload(workspace)
    materialize_workspace_candidate_references(workspace, candidate_artifact)
    fill_paper_card_source_metadata(workspace, candidate_artifact)
    ensure_survey_anchor_cards(workspace, candidate_artifact)
    if semantic_scholar_client is not None:
        enrich_workspace_papers_from_semantic_scholar(
            workspace,
            semantic_scholar_client,
        )
    if raw_llm_output_json_path is None:
        generate_missing_paper_tldrs(workspace)
    _fill_workspace_metadata(
        workspace=workspace,
        candidate_artifact=candidate_artifact,
        candidate_json_path=candidate_json_path,
        model=model,
        prompt_version=prompt_version,
        workspace_id_override=workspace_id_override,
    )
    validation = validate_workspace(workspace, candidate_artifact)
    if not validation.is_valid:
        write_json_file(
            output_dir / "workspace_raw_llm_output.json",
            raw_llm_output,
            archive_existing=True,
            run_label=run_label,
        )
        write_json_file(
            output_dir / "workspace_validation.json",
            validation.to_json(),
            archive_existing=True,
            run_label=run_label,
        )
        raise ValueError(
            "workspace validation failed: " + "; ".join(validation.errors)
        )
    output_paths = write_workspace_artifacts(
        output_dir=output_dir,
        prompt_text=prompt_text,
        raw_llm_output=raw_llm_output,
        workspace=workspace,
        validation=validation.to_json(),
        run_label=run_label,
        prompt_already_written=True,
    )
    return WorkspaceConstructionResult(
        workspace=workspace,
        validation=validation,
        output_paths=output_paths,
    )


def call_workspace_llm(
    *,
    prompt: str,
    model: str,
    llm_client: OpenAIResponsesWorkspaceClient | None = None,
) -> WorkspaceLlmResponse:
    return (llm_client or OpenAIResponsesWorkspaceClient()).call_workspace_llm(
        prompt=prompt,
        model=model,
    )


def parse_workspace_llm_output(raw_llm_output: str | dict[str, Any]) -> dict[str, Any]:
    return parse_workspace_output(raw_llm_output)


def _build_agent_workspace_prompt(
    *,
    construction_mode: ConstructionMode,
    user_message: str,
    base_workspace: dict[str, Any],
    proposed_workspace: Any,
    candidate_artifact: dict[str, Any] | None,
    agent_instruction: str,
    target_branch_id: str | None,
    target_paper_ids: list[str],
    similar_papers_context: dict[str, Any] | None,
    run_metadata: dict[str, Any],
) -> str:
    from research_tree.agents.workspace.prompts import (
        build_agent_modify_workspace_prompt,
        build_workspace_repair_prompt,
    )
    from research_tree.workspace.context import build_workspace_chat_context

    workspace_context = build_workspace_chat_context(
        workspace=base_workspace,
        candidate_artifact=candidate_artifact,
        target_branch_id=target_branch_id,
        target_paper_ids=target_paper_ids,
    )
    if construction_mode == "workspace_repair":
        return build_workspace_repair_prompt(
            user_message=user_message,
            base_workspace=base_workspace,
            proposed_workspace=(
                proposed_workspace if isinstance(proposed_workspace, dict) else {}
            ),
            validation_errors=[
                str(error) for error in run_metadata.get("validation_errors") or []
            ],
            original_instruction=agent_instruction,
            operation_history=[
                item
                for item in run_metadata.get("operation_history") or []
                if isinstance(item, dict)
            ],
            candidate_artifact=candidate_artifact,
        )
    return build_agent_modify_workspace_prompt(
        user_message=user_message,
        workspace=base_workspace,
        workspace_context=workspace_context,
        candidate_artifact=candidate_artifact,
        agent_instruction=agent_instruction,
        target_branch_id=target_branch_id,
        target_paper_ids=target_paper_ids,
        similar_papers_context=similar_papers_context,
    )


def _extract_llm_text(raw_response: dict[str, Any]) -> str:
    from research_tree.workspace.serialization import extract_response_output_text

    return extract_response_output_text(raw_response)


def normalize_workspace_payload(workspace: dict[str, Any]) -> None:
    _normalize_tree(workspace)
    _normalize_paper_paths(workspace)
    _normalize_paper_cards(workspace)


def fill_paper_card_source_metadata(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> None:
    paper_cards = workspace.get("paper_cards")
    if not isinstance(paper_cards, dict):
        return
    candidates = candidate_papers_from_artifact(candidate_artifact)
    for paper_id, card in paper_cards.items():
        if not isinstance(card, dict):
            continue
        candidate = candidates.get(str(paper_id))
        if candidate is None:
            continue
        card["paper_id"] = candidate.paper_id
        card["title"] = candidate.title
        card["authors"] = candidate.authors
        card["year"] = candidate.year
        card["publication_date"] = candidate.publication_date
        card["venue"] = candidate.venue
        card["primary_link"] = candidate.primary_link or candidate.arxiv_link
        card["doi"] = candidate.doi
        card["arxiv_id"] = candidate.arxiv_id
        card["arxiv_link"] = candidate.arxiv_link
        card["doi_link"] = candidate.doi_link
        card["s2_link"] = candidate.s2_link
        card["citation_count"] = candidate.citation_count
        card["abstract"] = candidate.abstract
        card["semantic_scholar_metadata"] = candidate.semantic_scholar_metadata
        if candidate.is_survey:
            card["paper_role"] = "survey"


def materialize_workspace_candidate_references(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> None:
    """Complete a compact LLM tree with deterministic candidate-backed cards.

    The LLM owns the field shape, branch placement, paths, and paper importance.
    Candidate artifacts own paper metadata. This keeps a partial model response
    from discarding an otherwise usable retrieval run.
    """

    candidates = candidate_papers_from_artifact(candidate_artifact)
    paper_cards = workspace.setdefault("paper_cards", {})
    if not isinstance(paper_cards, dict):
        workspace["paper_cards"] = paper_cards = {}
    warnings = _workspace_warnings(workspace)

    for paper_id in list(paper_cards):
        if str(paper_id) not in candidates:
            paper_cards.pop(paper_id)
            warnings.append(
                f"Removed LLM-selected paper {paper_id!r}: it was not in the candidate artifact."
            )

    tree = workspace.setdefault("tree", {"root_node_id": "root", "nodes": []})
    if not isinstance(tree, dict):
        workspace["tree"] = tree = {"root_node_id": "root", "nodes": []}
    node_labels = _node_labels(workspace)
    locations: dict[str, tuple[str, list[str]]] = {}

    paths = workspace.get("paper_paths")
    if not isinstance(paths, list):
        workspace["paper_paths"] = paths = []
    valid_paths: list[dict[str, Any]] = []
    for path in paths:
        if not isinstance(path, dict):
            continue
        branch_node_id = str(path.get("branch_node_id") or "")
        valid_steps = [
            step
            for step in _paper_steps(path.get("paper_steps"))
            if step["paper_id"] in candidates
        ]
        if not valid_steps:
            valid_steps = [
                step
                for step in _steps_from_paper_ids(_string_list(path.get("paper_ids")))
                if step["paper_id"] in candidates
            ]
        removed_ids = set(_string_list(path.get("paper_ids"))) - {
            step["paper_id"] for step in valid_steps
        }
        if removed_ids:
            warnings.append(
                f"Removed unknown papers from path {path.get('path_id')!r}: "
                f"{sorted(removed_ids)}."
            )
        if not valid_steps:
            continue
        path["paper_steps"] = valid_steps
        path["paper_ids"] = [step["paper_id"] for step in valid_steps]
        for paper_id in path["paper_ids"]:
            locations.setdefault(
                paper_id,
                (branch_node_id, _location_path(branch_node_id, node_labels)),
            )
        valid_paths.append(path)
    workspace["paper_paths"] = valid_paths

    for node in tree.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or "")
        for field_name in ("primary_paper_ids", "secondary_paper_ids"):
            original_ids = _string_list(node.get(field_name))
            node[field_name] = [paper_id for paper_id in original_ids if paper_id in candidates]
            for paper_id in node[field_name]:
                locations.setdefault(
                    paper_id,
                    (node_id, _location_path(node_id, node_labels)),
                )

    root = workspace.setdefault("root", {})
    if not isinstance(root, dict):
        workspace["root"] = root = {}
    for field_name in ("survey_anchor_paper_ids", "representative_paper_ids"):
        original_ids = _string_list(root.get(field_name))
        root[field_name] = [paper_id for paper_id in original_ids if paper_id in candidates]
        for paper_id in root[field_name]:
            locations.setdefault(paper_id, ("root", _location_path("root", node_labels)))

    anchor_ids = set(_string_list(root.get("survey_anchor_paper_ids")))
    for node in tree.get("nodes") or []:
        if isinstance(node, dict):
            anchor_ids.add(str(node.get("survey_anchor_paper_id") or ""))
    for paper_id in list(paper_cards):
        candidate = candidates.get(str(paper_id))
        if candidate and candidate.is_survey and paper_id not in anchor_ids:
            paper_cards.pop(paper_id)
            warnings.append(
                f"Removed survey {paper_id!r}: surveys are retained only as overview anchors."
            )

    for paper_id, (node_id, path) in locations.items():
        candidate = candidates[paper_id]
        card = paper_cards.setdefault(
            paper_id,
            _candidate_paper_card(candidate, node_id=node_id, path=path),
        )
        if not isinstance(card, dict):
            paper_cards[paper_id] = _candidate_paper_card(
                candidate,
                node_id=node_id,
                path=path,
            )
            continue
        card.setdefault("primary_tree_location", {"node_id": node_id, "path": path})
        card.setdefault("importance", "")
        card.setdefault("reading_status", "unread")
        card.setdefault("paper_role", "survey" if candidate.is_survey else "other")
        card.setdefault("secondary_tags", [])
        card.setdefault("read_before", [])
        card.setdefault("read_after", [])
        card.setdefault("user_notes", "")
        card.setdefault("similar_papers", [])

    workspace.setdefault("reading_order", _reading_order_from_paths(valid_paths))
    workspace.setdefault("comparison_tables", [])
    workspace.setdefault(
        "discarded_candidates",
        _discarded_candidates(candidates, set(paper_cards)),
    )


def _candidate_paper_card(
    candidate: CandidatePaperMetadata,
    *,
    node_id: str,
    path: list[str],
) -> dict[str, Any]:
    return {
        "paper_id": candidate.paper_id,
        "title": candidate.title,
        "authors": candidate.authors,
        "year": candidate.year,
        "publication_date": candidate.publication_date,
        "venue": candidate.venue,
        "primary_link": candidate.primary_link or candidate.arxiv_link,
        "doi": candidate.doi,
        "arxiv_id": candidate.arxiv_id,
        "arxiv_link": candidate.arxiv_link,
        "doi_link": candidate.doi_link,
        "s2_link": candidate.s2_link,
        "citation_count": candidate.citation_count,
        "semantic_scholar_metadata": candidate.semantic_scholar_metadata,
        "abstract": candidate.abstract,
        "primary_tree_location": {"node_id": node_id, "path": path},
        "secondary_tags": [],
        "reading_status": "unread",
        "paper_role": "survey" if candidate.is_survey else "other",
        "importance": "",
        "read_before": [],
        "read_after": [],
        "user_notes": "",
        "similar_papers": [],
    }


def _reading_order_from_paths(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    paper_ids: list[str] = []
    for path in paths:
        for step in path.get("paper_steps") or []:
            if not isinstance(step, dict):
                continue
            paper_id = str(step.get("paper_id") or "")
            if paper_id and paper_id not in paper_ids:
                paper_ids.append(paper_id)
    return [
        {
            "order": index,
            "paper_id": paper_id,
            "reason": "Part of the selected learning path.",
        }
        for index, paper_id in enumerate(paper_ids, start=1)
    ]


def _discarded_candidates(
    candidates: dict[str, CandidatePaperMetadata],
    visible_paper_ids: set[str],
) -> list[dict[str, str]]:
    return [
        {
            "paper_id": candidate.paper_id,
            "title": candidate.title,
            "discard_reason": "Not selected for the scoped core workspace.",
            "possible_future_use": "candidate_for_branch_workspace",
        }
        for paper_id, candidate in candidates.items()
        if paper_id not in visible_paper_ids
    ]


def ensure_survey_anchor_cards(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> None:
    paper_cards = workspace.get("paper_cards")
    if not isinstance(paper_cards, dict):
        return
    candidates = candidate_papers_from_artifact(candidate_artifact)
    root = workspace.get("root")
    root = root if isinstance(root, dict) else {}
    root_label = str(root.get("label") or workspace.get("title") or "Root")
    root_anchor_ids = _string_list(root.get("survey_anchor_paper_ids"))
    for paper_id in root_anchor_ids:
        candidate = candidates.get(paper_id)
        if candidate and candidate.is_survey:
            paper_cards.setdefault(
                paper_id,
                _survey_anchor_card(
                    candidate=candidate,
                    node_id="root",
                    path=[root_label],
                ),
            )

    node_labels = _node_labels(workspace)
    tree = workspace.get("tree")
    for node in (tree.get("nodes") or []) if isinstance(tree, dict) else []:
        if not isinstance(node, dict):
            continue
        paper_id = str(node.get("survey_anchor_paper_id") or "").strip()
        candidate = candidates.get(paper_id)
        if not paper_id or candidate is None or not candidate.is_survey:
            continue
        node_id = str(node.get("node_id") or "")
        paper_cards.setdefault(
            paper_id,
            _survey_anchor_card(
                candidate=candidate,
                node_id=node_id,
                path=_location_path(node_id, node_labels),
            ),
        )


def _survey_anchor_card(
    *,
    candidate: CandidatePaperMetadata,
    node_id: str,
    path: list[str],
) -> dict[str, Any]:
    return {
        "paper_id": candidate.paper_id,
        "title": candidate.title,
        "authors": candidate.authors,
        "year": candidate.year,
        "publication_date": candidate.publication_date,
        "venue": candidate.venue,
        "primary_link": candidate.primary_link or candidate.arxiv_link,
        "doi": candidate.doi,
        "arxiv_id": candidate.arxiv_id,
        "arxiv_link": candidate.arxiv_link,
        "doi_link": candidate.doi_link,
        "s2_link": candidate.s2_link,
        "citation_count": candidate.citation_count,
        "semantic_scholar_metadata": candidate.semantic_scholar_metadata,
        "abstract": candidate.abstract,
        "primary_tree_location": {"node_id": node_id, "path": path},
        "secondary_tags": [],
        "reading_status": "unread",
        "paper_role": "survey",
        "importance": "",
        "problem": "",
        "core_idea": "",
        "method": "",
        "assumptions": "",
        "datasets_or_benchmarks": "",
        "results": "",
        "limitations": "",
        "read_before": [],
        "read_after": [],
        "user_notes": "",
        "similar_papers": [],
    }


def _normalize_tree(workspace: dict[str, Any]) -> None:
    tree = workspace.get("tree")
    if not isinstance(tree, dict):
        return
    if "nodes" in tree and "root_node_id" in tree:
        return

    branches = tree.get("branches")
    if not isinstance(branches, list):
        tree.setdefault("root_node_id", "root")
        tree.setdefault("nodes", [])
        return

    nodes: list[dict[str, Any]] = []
    child_ids_by_parent: dict[str, list[str]] = {}
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        node_id = str(branch.get("node_id") or branch.get("id") or "").strip()
        if not node_id:
            continue
        parent_id = str(branch.get("parent_id") or "root").strip()
        child_ids = [
            str(child.get("id") or child.get("node_id") or child)
            for child in branch.get("children") or []
            if child
        ]
        child_ids_by_parent.setdefault(parent_id, []).append(node_id)
        nodes.append(
            {
                "node_id": node_id,
                "parent_id": parent_id,
                "label": str(branch.get("label") or node_id.replace("_", " ").title()),
                "description": str(branch.get("description") or ""),
                "why_it_matters": str(
                    branch.get("why_it_matters")
                    or branch.get("rationale")
                    or branch.get("description")
                    or ""
                ),
                "is_leaf": not child_ids,
                "child_node_ids": child_ids,
                "primary_paper_ids": _string_list(
                    branch.get("primary_paper_ids") or branch.get("paper_ids")
                ),
                "secondary_paper_ids": _string_list(branch.get("secondary_paper_ids")),
                "survey_anchor_paper_id": branch.get("survey_anchor_paper_id"),
                "tags": _string_list(branch.get("tags")),
                "open_questions": _string_list(branch.get("open_questions")),
            }
        )

    for node in nodes:
        if not node["child_node_ids"]:
            node["child_node_ids"] = child_ids_by_parent.get(node["node_id"], [])
            node["is_leaf"] = not node["child_node_ids"]

    tree["root_node_id"] = str(tree.get("root_node_id") or "root")
    tree["nodes"] = nodes


def _normalize_paper_paths(workspace: dict[str, Any]) -> None:
    paper_paths = workspace.get("paper_paths")
    if not isinstance(paper_paths, list):
        return
    for path in paper_paths:
        if not isinstance(path, dict):
            continue
        if "branch_node_id" not in path:
            path["branch_node_id"] = path.get("branch_id") or path.get("node_id") or ""
        paper_steps = _paper_steps(path.get("paper_steps"))
        if paper_steps:
            path["paper_steps"] = paper_steps
            path["paper_ids"] = [step["paper_id"] for step in paper_steps]
        else:
            if "paper_ids" not in path:
                path["paper_ids"] = _string_list(
                    path.get("ordered_paper_ids") or path.get("papers")
                )
            else:
                path["paper_ids"] = _string_list(path.get("paper_ids"))
            path["paper_steps"] = _steps_from_paper_ids(path["paper_ids"])
        path.setdefault("path_type", "primary_timeline")
        path.setdefault("description", path.get("learning_goal") or path.get("notes") or "")
        path.setdefault("rationale", path.get("notes") or path.get("learning_goal") or "")
        for obsolete_field in (
            "timeline_intent",
            "recommended_reading_depth",
        ):
            path.pop(obsolete_field, None)


def _paper_steps(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    legacy_steps: list[tuple[int, dict[str, Any]]] = []
    for fallback_index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        paper_id = str(item.get("paper_id") or "").strip()
        if not paper_id:
            continue
        legacy_steps.append(
            (
                _int_or_default(item.get("step_index"), fallback_index),
                {
                    "paper_id": paper_id,
                    "why_read_here": str(
                        item.get("why_read_here")
                        or "Read this next in the saved path."
                    ),
                },
            )
        )
    legacy_steps.sort(key=lambda item: item[0])
    return [step for _, step in legacy_steps]


def _steps_from_paper_ids(paper_ids: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "paper_id": paper_id,
            "why_read_here": "Read this next in the saved path.",
        }
        for paper_id in paper_ids
    ]


def _normalize_paper_cards(workspace: dict[str, Any]) -> None:
    paper_cards = workspace.get("paper_cards")
    if not isinstance(paper_cards, dict):
        return

    node_labels = _node_labels(workspace)
    for paper_id, card in paper_cards.items():
        if not isinstance(card, dict):
            continue
        card.setdefault("paper_id", paper_id)
        location = card.get("primary_tree_location")
        if isinstance(location, str):
            node_id = location.rstrip("/").split("/")[-1]
            card["primary_tree_location"] = {
                "node_id": node_id,
                "path": _location_path(node_id, node_labels),
            }
        card.setdefault("similar_papers", [])
        if "importance" not in card:
            card["importance"] = (
                card.get("why_it_belongs")
                or card.get("why_it_belongs_in_this_branch")
                or card.get("one_sentence_contribution")
                or ""
            )
        if "read_before" not in card and "what_to_read_before_it" in card:
            card["read_before"] = _string_list(card.get("what_to_read_before_it"))
        if "read_after" not in card and "what_to_read_after_it" in card:
            card["read_after"] = _string_list(card.get("what_to_read_after_it"))
        for field_name in (
            "authors",
            "secondary_tags",
            "read_before",
            "read_after",
            "similar_papers",
        ):
            if field_name in card:
                continue
            card[field_name] = []
        card.setdefault("user_notes", "")
        card["paper_role"] = _paper_role(card.get("paper_role"))
        card.pop("one_sentence_contribution", None)
        card.pop("why_it_belongs", None)
        card.pop("why_it_belongs_in_this_branch", None)


def enrich_workspace_papers_from_semantic_scholar(
    workspace: dict[str, Any],
    semantic_scholar: SemanticScholarClient,
) -> None:
    """Attach Semantic Scholar's source TLDR to the curated workspace papers."""

    paper_cards = workspace.get("paper_cards")
    if not isinstance(paper_cards, dict):
        return
    warnings = _workspace_warnings(workspace)
    details_by_id = semantic_scholar.get_paper_details(
        [str(paper_id) for paper_id in paper_cards],
        warnings,
    )
    for paper_id, card in paper_cards.items():
        if not isinstance(card, dict):
            continue
        details = details_by_id.get(str(paper_id))
        if details is None:
            continue
        source_paper = paper_from_semantic_scholar(details)
        _fill_card_with_semantic_scholar_details(card, source_paper)
        card["semantic_scholar_metadata"] = semantic_scholar_metadata(details)
        tldr = details.get("tldr")
        if isinstance(tldr, dict) and isinstance(tldr.get("text"), str):
            card["tldr"] = tldr["text"].strip() or None
            card["tldr_model"] = tldr.get("model")
            card["tldr_source"] = "semantic_scholar"


def generate_missing_paper_tldrs(
    workspace: dict[str, Any],
    *,
    model: str = "gpt-5.4-mini",
    api_key: str | None = None,
) -> None:
    """Generate TLDRs only for curated papers without a Semantic Scholar TLDR."""

    paper_cards = workspace.get("paper_cards")
    if not isinstance(paper_cards, dict):
        return
    missing = [
        {
            "paper_id": str(paper_id),
            "title": str(card.get("title") or ""),
            "abstract": str(card.get("abstract") or ""),
        }
        for paper_id, card in paper_cards.items()
        if isinstance(card, dict) and not str(card.get("tldr") or "").strip()
    ]
    if not missing:
        return

    active_api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not active_api_key:
        _workspace_warnings(workspace).append(
            "Semantic Scholar had no TLDR for one or more visible papers and "
            "OPENAI_API_KEY was unavailable for the fallback."
        )
        return

    body = {
        "model": model,
        "input": build_missing_paper_tldr_prompt(missing),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "research_tree_paper_tldrs",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "tldrs": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "paper_id": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                                "required": ["paper_id", "text"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["tldrs"],
                    "additionalProperties": False,
                },
            }
        },
        "reasoning": {"effort": "minimal"},
        "max_output_tokens": 2000,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {active_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60.0) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
        generated = json.loads(_extract_llm_text(raw_response))
    except (OSError, ValueError, urllib.error.HTTPError, urllib.error.URLError) as error:
        _workspace_warnings(workspace).append(
            f"Generated TLDR fallback failed: {error}"
        )
        return

    tldrs = generated.get("tldrs") if isinstance(generated, dict) else []
    if not isinstance(tldrs, list):
        tldrs = []
    for item in tldrs:
        if not isinstance(item, dict):
            continue
        paper_id = str(item.get("paper_id") or "")
        text = str(item.get("text") or "").strip()
        card = paper_cards.get(paper_id)
        if isinstance(card, dict) and text:
            card["tldr"] = text
            card["tldr_model"] = model
            card["tldr_source"] = "generated"
    provenance = workspace.setdefault("provenance", {})
    if isinstance(provenance, dict):
        provenance.setdefault("paper_tldr_prompt_version", PAPER_TLDR_PROMPT_VERSION)


def _fill_card_with_semantic_scholar_details(
    card: dict[str, Any],
    source_paper: Any,
) -> None:
    if source_paper.title:
        card["title"] = source_paper.title
    if source_paper.abstract:
        card["abstract"] = source_paper.abstract
    if source_paper.year is not None:
        card["year"] = source_paper.year
    if source_paper.publication_date is not None:
        card["publication_date"] = source_paper.publication_date.isoformat()
    if source_paper.venue:
        card["venue"] = source_paper.venue
    if source_paper.authors:
        card["authors"] = source_paper.authors
    if source_paper.url:
        card["primary_link"] = source_paper.url
    if source_paper.doi:
        card["doi"] = source_paper.doi
    if source_paper.arxiv_id:
        card["arxiv_id"] = source_paper.arxiv_id
        card["arxiv_link"] = f"https://arxiv.org/abs/{source_paper.arxiv_id}"
    if source_paper.citation_count is not None:
        card["citation_count"] = source_paper.citation_count


def _workspace_warnings(workspace: dict[str, Any]) -> list[str]:
    provenance = workspace.setdefault("provenance", {})
    if not isinstance(provenance, dict):
        return []
    warnings = provenance.setdefault("warnings", [])
    return warnings if isinstance(warnings, list) else []


def _node_labels(workspace: dict[str, Any]) -> dict[str, str]:
    labels = {
        "root": str(
            (workspace.get("root") or {}).get("label")
            or workspace.get("title")
            or "Root"
        )
    }
    tree = workspace.get("tree")
    if not isinstance(tree, dict):
        return labels
    for node in tree.get("nodes") or []:
        if isinstance(node, dict) and node.get("node_id"):
            labels[str(node["node_id"])] = str(node.get("label") or node["node_id"])
    return labels


def _location_path(node_id: str, node_labels: dict[str, str]) -> list[str]:
    if node_id == "root":
        return [node_labels.get("root", "Root")]
    return [node_labels.get("root", "Root"), node_labels.get(node_id, node_id)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _int_or_default(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _paper_role(value: Any) -> str:
    normalized = str(value or "").strip().casefold().replace("_", " ")
    if normalized in PAPER_ROLES:
        return normalized
    if "survey" in normalized:
        return "survey"
    if "foundational" in normalized or "precursor" in normalized:
        return "foundational"
    if "benchmark" in normalized:
        return "benchmark"
    if "evaluat" in normalized:
        return "evaluation"
    if "critique" in normalized or "limitation" in normalized:
        return "critique"
    if "application" in normalized:
        return "application"
    if "method" in normalized or "tuning" in normalized or "alignment" in normalized:
        return "method"
    return "other"


def _model_supports_reasoning_effort(model: str) -> bool:
    normalized = model.casefold()
    return normalized.startswith("gpt-5") or normalized.startswith(
        ("o1", "o2", "o3", "o4")
    )


def _reasoning_effort_for_model(
    model: str,
    reasoning_effort: str | None,
) -> str | None:
    if not reasoning_effort or not _model_supports_reasoning_effort(model):
        return None
    if model.casefold().startswith("gpt-5-pro") and reasoning_effort != "high":
        return None
    return reasoning_effort


def _fill_workspace_metadata(
    *,
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
    candidate_json_path: Path,
    model: str,
    prompt_version: str,
    workspace_id_override: str | None,
) -> None:
    topic = str(workspace.get("topic") or candidate_artifact.get("topic") or "")
    workspace.setdefault("schema_version", WORKSPACE_SCHEMA_VERSION)
    workspace.setdefault("topic", topic)
    workspace.setdefault("title", _title_from_topic(topic))
    if workspace_id_override:
        workspace["workspace_id"] = workspace_id_override
    else:
        workspace.setdefault(
            "workspace_id",
            f"{_slug(topic or 'workspace')}__{datetime.now(UTC).date().isoformat()}",
        )
    candidate_artifact_hash = _candidate_artifact_hash(candidate_artifact)
    workspace["source_candidate_artifact"] = {
        "path": str(candidate_json_path),
        "schema_version": candidate_artifact.get("schema_version"),
        "content_hash": candidate_artifact_hash,
        "input_mode": "existing_candidate_artifact",
        "non_survey_count": len(candidate_artifact.get("non_survey_papers") or []),
        "survey_count": len(candidate_artifact.get("survey_papers") or []),
        "candidate_order": candidate_artifact.get(
            "candidate_pool_order", "age_adjusted_citation_score_desc"
        ),
        "run_name": candidate_artifact.get("run_name"),
        "run_dir": candidate_artifact.get("run_dir"),
    }
    scope = workspace.setdefault("scope", {})
    if isinstance(scope, dict):
        scope.setdefault(
            "visible_paper_budget",
            {"target_min": 10, "target_max": 25, "hard_max_default": 30},
        )
    provenance = workspace.setdefault("provenance", {})
    if isinstance(provenance, dict):
        provenance.setdefault("workspace_constructor", "llm")
        provenance["model"] = model
        provenance["prompt_version"] = prompt_version
        provenance["construction_input"] = {
            "candidate_artifact_path": str(candidate_json_path),
            "candidate_artifact_hash": candidate_artifact_hash,
            "input_mode": "existing_candidate_artifact",
        }
        provenance.setdefault("created_at", datetime.now(UTC).isoformat())
        provenance.setdefault("warnings", [])


def _run_label(path: Path) -> str:
    if path.name:
        return path.name
    return "workspace"


def _candidate_artifact_hash(candidate_artifact: dict[str, Any]) -> str:
    payload = json.dumps(
        candidate_artifact,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold())
    return normalized.strip("-") or "workspace"


def _title_from_topic(topic: str) -> str:
    return " ".join(part.capitalize() for part in topic.split())
