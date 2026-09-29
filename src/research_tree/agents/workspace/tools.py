"""Tools the workspace agent may call.

Two kinds:

*Read tools* answer a question and hand the result straight back to the model.
They are pure lookups — nothing here mutates a workspace.

*Terminal tools* end the loop and route the graph to the node that does the
work: proposing an edit, rerunning retrieval, or critiquing. They exist so the
model chooses an action by name instead of a planner call guessing one, and so
every write still lands in the deterministic
propose -> validate -> pending review -> human approval path.

Papers the agent discovers are recorded from Semantic Scholar's own payloads
into `session_discovered_papers`. A proposal may cite those ids, and the
metadata attached to them comes from the API rather than the model, so an
invented paper cannot reach a workspace.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from research_tree.paths import semantic_scholar_cache_dir
from research_tree.retrieval.semantic_scholar import (
    SEMANTIC_SCHOLAR_INTERACTIVE_MAX_RETRIES,
    SemanticScholarClient,
    s2_api_key,
)
from research_tree.workspace.construction import DERIVED_PAPER_CARD_FIELDS
from research_tree.workspace.repository import WorkspaceRepository


# One agent turn may not spend more than this many Semantic Scholar requests.
# S2 allows ~1 req/s across all endpoints, cumulative, so an unbounded loop
# would starve pipeline builds running in the same process.
MAX_SEMANTIC_SCHOLAR_CALLS_PER_RUN = 8
MAX_SEARCH_RESULTS = 20
MAX_DISCOVERED_PAPERS_PER_THREAD = 200
# Tool results are replayed on every later turn, so a large one is paid for
# repeatedly.
MAX_TOOL_RESULT_CHARACTERS = 20_000
# Full text is read a window at a time; the window fits inside the result cap
# with room for the JSON around it, so a page of text is never truncated
# twice.
FULL_TEXT_WINDOW_CHARACTERS = 15_000
WEB_SEARCH_ENV_FLAG = "RESEARCH_TREE_AGENT_WEB_SEARCH"


@dataclass
class ToolContext:
    """Everything a tool handler is allowed to touch."""

    workspace: Mapping[str, Any]
    workspace_id: str
    repository: WorkspaceRepository | None
    repo_root: Path
    discovered_papers: dict[str, dict[str, Any]] = field(default_factory=dict)
    semantic_scholar_calls: int = 0
    _semantic_scholar: SemanticScholarClient | None = None

    def semantic_scholar(self) -> SemanticScholarClient:
        if self._semantic_scholar is None:
            # Shares the pipeline's cache directory, so a paper already fetched
            # during a build costs no request here.
            self._semantic_scholar = SemanticScholarClient(
                cache_dir=semantic_scholar_cache_dir(),
                api_key=s2_api_key(),
                max_retries=SEMANTIC_SCHOLAR_INTERACTIVE_MAX_RETRIES,
            )
        return self._semantic_scholar

    def spend_semantic_scholar_call(self) -> bool:
        if self.semantic_scholar_calls >= MAX_SEMANTIC_SCHOLAR_CALLS_PER_RUN:
            return False
        self.semantic_scholar_calls += 1
        return True

    def record_discovered_papers(self, payloads: list[Mapping[str, Any]]) -> None:
        for payload in payloads:
            paper_id = str(payload.get("paperId") or "")
            if paper_id:
                # Re-inserted so the newest sighting is also the last to go.
                self.discovered_papers.pop(paper_id, None)
                self.discovered_papers[paper_id] = dict(payload)
        # A thread keeps its discoveries from turn to turn, each a whole
        # provider payload in the checkpoint, so the oldest give way. A paper
        # that has gone can be fetched again.
        for stale_id in list(self.discovered_papers)[:-MAX_DISCOVERED_PAPERS_PER_THREAD]:
            del self.discovered_papers[stale_id]


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], Any] | None = None
    terminal: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


# --- read tools -------------------------------------------------------------


def _get_branch(context: ToolContext, arguments: dict[str, Any]) -> Any:
    branch_id = str(arguments.get("branch_id") or "")
    nodes = _tree_nodes(context.workspace)
    branch = next(
        (node for node in nodes if str(node.get("node_id")) == branch_id), None
    )
    if branch is None:
        return {
            "error": f"no branch with id {branch_id!r}",
            "available_branch_ids": [str(node.get("node_id")) for node in nodes],
        }
    # A path names its branch in `branch_node_id`; every other reader of a path
    # uses that. Matching on `branch_id` found nothing, so the assistant was
    # told every branch was empty and answered from that.
    paths = [
        path
        for path in context.workspace.get("paper_paths") or []
        if isinstance(path, Mapping) and str(path.get("branch_node_id")) == branch_id
    ]
    return {
        "branch": branch,
        "paper_paths": paths,
        "paper_ids": [
            str(step.get("paper_id"))
            for path in paths
            for step in path.get("paper_steps") or []
            if isinstance(step, Mapping) and step.get("paper_id")
        ],
    }


def _get_paper(context: ToolContext, arguments: dict[str, Any]) -> Any:
    paper_id = str(arguments.get("paper_id") or "")
    cards = context.workspace.get("paper_cards")
    card = cards.get(paper_id) if isinstance(cards, Mapping) else None
    if not isinstance(card, Mapping):
        return {"error": f"no paper card with id {paper_id!r}"}
    result: dict[str, Any] = {"paper_card": dict(card)}
    if arguments.get("include_full_text") and context.repository is not None:
        # The window is sized to fit the result cap beside a card. A card's
        # derived payloads (the provider's record, its recommendations) are
        # what pushed the two past it, and the model lost the text and the
        # offset of the next window to the truncation.
        for derived_field in DERIVED_PAPER_CARD_FIELDS:
            result["paper_card"].pop(derived_field, None)
        result.update(_paper_full_text_window(context, paper_id, 0))
    return result


def _search_workspace(context: ToolContext, arguments: dict[str, Any]) -> Any:
    query_tokens = _tokens(str(arguments.get("query") or ""))
    if not query_tokens:
        return {"error": "query is empty"}
    matches: list[dict[str, Any]] = []
    cards = context.workspace.get("paper_cards")
    for paper_id, card in (cards if isinstance(cards, Mapping) else {}).items():
        if not isinstance(card, Mapping):
            continue
        haystack = _tokens(f"{card.get('title') or ''} {card.get('tldr') or ''}")
        overlap = len(query_tokens & haystack)
        if overlap:
            matches.append(
                {
                    "kind": "paper",
                    "id": str(paper_id),
                    "title": card.get("title"),
                    "score": overlap,
                }
            )
    for node in _tree_nodes(context.workspace):
        haystack = _tokens(f"{node.get('label') or ''} {node.get('description') or ''}")
        overlap = len(query_tokens & haystack)
        if overlap:
            matches.append(
                {
                    "kind": "branch",
                    "id": str(node.get("node_id")),
                    "title": node.get("label"),
                    "score": overlap,
                }
            )
    matches.sort(key=lambda item: (-int(item["score"]), str(item["id"])))
    return {"matches": matches[:MAX_SEARCH_RESULTS]}


def _get_paper_full_text(context: ToolContext, arguments: dict[str, Any]) -> Any:
    paper_id = str(arguments.get("paper_id") or "")
    if context.repository is None:
        return {"error": "no workspace repository is available"}
    return {"paper_id": paper_id, **_paper_full_text_window(context, paper_id, arguments.get("offset"))}


def _search_semantic_scholar(context: ToolContext, arguments: dict[str, Any]) -> Any:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return {"error": "query is empty"}
    if not context.spend_semantic_scholar_call():
        return _budget_exhausted()
    limit = _bounded_limit(arguments.get("limit"))
    filters: dict[str, str] = {}
    year_from = arguments.get("year_from")
    if isinstance(year_from, int):
        filters["publicationDateOrYear"] = f"{year_from}:"
    papers = context.semantic_scholar().bulk_search(
        query, max_papers=limit, filters=filters or None
    )
    # Record the provider's own payload, never anything the model wrote.
    payloads = [paper.semantic_scholar_metadata for paper in papers]
    context.record_discovered_papers(payloads)
    return {
        "results": [
            {
                "paper_id": payload.get("paperId"),
                "title": payload.get("title"),
                "year": payload.get("year"),
                "venue": payload.get("venue"),
                "citation_count": payload.get("citationCount"),
                "abstract": _clip(payload.get("abstract"), 600),
            }
            for payload in payloads
        ],
        "note": (
            "Cite these paper_id values in propose_workspace_edit.add_paper_ids "
            "to add any of them to the workspace."
        ),
    }


def _get_semantic_scholar_paper(context: ToolContext, arguments: dict[str, Any]) -> Any:
    paper_id = str(arguments.get("paper_id") or "").strip()
    if not paper_id:
        return {"error": "paper_id is empty"}
    if not context.spend_semantic_scholar_call():
        return _budget_exhausted()
    details = context.semantic_scholar().get_paper_details([paper_id], None)
    if not details:
        return {"error": f"Semantic Scholar has no record for {paper_id!r}"}
    context.record_discovered_papers(list(details.values()))
    return {"paper": next(iter(details.values()))}


def _get_workspace_overview(context: ToolContext, _arguments: dict[str, Any]) -> Any:
    workspace = context.workspace
    root = workspace.get("root")
    provenance = workspace.get("provenance")
    provenance = dict(provenance) if isinstance(provenance, Mapping) else {}
    provenance.pop("warnings", None)
    nodes = _tree_nodes(workspace)
    cards = workspace.get("paper_cards")
    return {
        "workspace_id": workspace.get("workspace_id"),
        "title": workspace.get("title"),
        "topic": workspace.get("topic"),
        "scope": workspace.get("scope"),
        "root": dict(root) if isinstance(root, Mapping) else {},
        "comparison_tables": workspace.get("comparison_tables") or [],
        "provenance": provenance,
        "discarded_candidates": [
            {
                "paper_id": item.get("paper_id"),
                "title": item.get("title"),
                "discard_reason": item.get("discard_reason"),
            }
            for item in workspace.get("discarded_candidates") or []
            if isinstance(item, Mapping)
        ],
        "counts": {
            "branches": len(nodes),
            "visible_papers": len(cards) if isinstance(cards, Mapping) else 0,
            "paper_paths": len(workspace.get("paper_paths") or []),
        },
    }


def _list_reading_order(context: ToolContext, _arguments: dict[str, Any]) -> Any:
    workspace = context.workspace
    cards = workspace.get("paper_cards")
    cards = cards if isinstance(cards, Mapping) else {}

    def _title(paper_id: str) -> Any:
        card = cards.get(paper_id)
        return card.get("title") if isinstance(card, Mapping) else None

    return {
        "reading_order": [
            {
                "order": entry.get("order"),
                "paper_id": entry.get("paper_id"),
                "title": _title(str(entry.get("paper_id") or "")),
                "reason": entry.get("reason"),
            }
            for entry in workspace.get("reading_order") or []
            if isinstance(entry, Mapping)
        ],
        "paper_paths": [
            {
                "path_id": path.get("path_id"),
                "branch_node_id": path.get("branch_node_id"),
                "label": path.get("label"),
                "description": path.get("description"),
                "papers": [
                    {
                        "paper_id": step.get("paper_id"),
                        "title": _title(str(step.get("paper_id") or "")),
                        "why_read_here": step.get("why_read_here"),
                    }
                    for step in path.get("paper_steps") or []
                    if isinstance(step, Mapping)
                ],
            }
            for path in workspace.get("paper_paths") or []
            if isinstance(path, Mapping)
        ],
    }


def _list_workspace_history(context: ToolContext, _arguments: dict[str, Any]) -> Any:
    if context.repository is None:
        return {"error": "no workspace repository is available"}
    # Newest first: the recent changes are the ones a question about history
    # is about. Versions are stored oldest first, reviews by id.
    versions = context.repository.list_workspace_versions(context.workspace_id)[::-1]
    reviews = sorted(
        context.repository.list_workspace_reviews(context.workspace_id),
        key=lambda review: str(review.get("created_at") or ""),
        reverse=True,
    )
    return {
        "versions": [
            {
                "workspace_version_hash": version.get("version_hash"),
                "reason": version.get("reason"),
                "actor_type": version.get("actor_type"),
                "created_at": version.get("created_at"),
            }
            for version in versions[:20]
        ],
        "reviews": [
            {
                "review_id": review.get("review_id"),
                "status": review.get("status"),
                "user_message": review.get("user_message"),
            }
            for review in reviews[:20]
        ],
    }


READ_TOOLS: tuple[AgentTool, ...] = (
    AgentTool(
        name="get_branch",
        description=(
            "Read one branch of the workspace tree with its reading paths and "
            "the paper ids on them."
        ),
        parameters=_object({"branch_id": {"type": "string"}}, ["branch_id"]),
        handler=_get_branch,
    ),
    AgentTool(
        name="get_paper",
        description="Read one workspace paper card, optionally with its extracted full text.",
        parameters=_object(
            {
                "paper_id": {"type": "string"},
                "include_full_text": {"type": "boolean"},
            },
            ["paper_id"],
        ),
        handler=_get_paper,
    ),
    AgentTool(
        name="search_workspace",
        description="Find papers and branches in this workspace by keyword.",
        parameters=_object({"query": {"type": "string"}}, ["query"]),
        handler=_search_workspace,
    ),
    AgentTool(
        name="get_paper_full_text",
        description=(
            "Read the extracted open-access full text of a workspace paper, "
            "when one was downloaded. The text comes back one window at a "
            "time; pass the `next_offset` a result gives you to read on."
        ),
        parameters=_object(
            {
                "paper_id": {"type": "string"},
                "offset": {
                    "type": "integer",
                    "description": "Character offset to start from; 0 or omitted for the beginning.",
                },
            },
            ["paper_id"],
        ),
        handler=_get_paper_full_text,
    ),
    AgentTool(
        name="search_semantic_scholar",
        description=(
            "Search Semantic Scholar for papers outside this workspace. Use it "
            "to find work the workspace is missing. Requests are rate limited, "
            "so search deliberately rather than repeatedly."
        ),
        parameters=_object(
            {
                "query": {"type": "string"},
                "year_from": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            ["query"],
        ),
        handler=_search_semantic_scholar,
    ),
    AgentTool(
        name="get_semantic_scholar_paper",
        description=(
            "Fetch one paper's authoritative metadata from Semantic Scholar by "
            "its paper id, DOI, or arXiv id. Resolve a paper found through web "
            "search this way before proposing it."
        ),
        parameters=_object({"paper_id": {"type": "string"}}, ["paper_id"]),
        handler=_get_semantic_scholar_paper,
    ),
    AgentTool(
        name="get_workspace_overview",
        description=(
            "Read the workspace's editorial front matter: the root overview, "
            "scope, key terms, open questions, survey anchors, comparison "
            "tables, provenance (including the similar-papers policy), and "
            "the candidates the pipeline considered but discarded."
        ),
        parameters=_object({}, []),
        handler=_get_workspace_overview,
    ),
    AgentTool(
        name="list_reading_order",
        description=(
            "Read the workspace-wide reading order and every reading path "
            "with its ordered papers and per-step rationale."
        ),
        parameters=_object({}, []),
        handler=_list_reading_order,
    ),
    AgentTool(
        name="list_workspace_history",
        description="List recent workspace versions and reviews.",
        parameters=_object({}, []),
        handler=_list_workspace_history,
    ),
)


# --- terminal tools ---------------------------------------------------------

TERMINAL_TOOLS: tuple[AgentTool, ...] = (
    AgentTool(
        name="propose_workspace_edit",
        description=(
            "Propose a structural change to the workspace: rename or split a "
            "branch, move or remove papers, reorder a reading path, add papers "
            "you found, or refresh similar-paper recommendations. The change is "
            "validated and shown to the user for approval; it is never applied "
            "directly. Describe the edit in one instruction."
        ),
        parameters=_object(
            {
                "instruction": {
                    "type": "string",
                    "description": (
                        "Exactly the change the user asked for — no cleanups "
                        "or improvements they did not request."
                    ),
                },
                "edit_kind": {
                    "type": "string",
                    "enum": ["structural", "remove_papers", "refresh_similar_papers"],
                    "description": (
                        "structural: any tree, paper, path, or card edit, "
                        "including adding papers. remove_papers: delete exactly "
                        "the papers in target_paper_ids from the visible "
                        "workspace. refresh_similar_papers: recompute "
                        "similar-paper recommendations only, changing nothing "
                        "else."
                    ),
                },
                "message_to_user": {
                    "type": "string",
                    "description": (
                        "One or two sentences telling the user what you are "
                        "proposing and why. Shown as your chat reply above the "
                        "review card."
                    ),
                },
                "target_branch_id": {"type": "string"},
                "target_paper_ids": {"type": "array", "items": {"type": "string"}},
                "add_paper_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Required when the edit adds papers: every Semantic "
                        "Scholar id the edit should newly add, exactly as a "
                        "search tool returned it in this conversation. Ids "
                        "from anywhere else are rejected."
                    ),
                },
            },
            ["instruction", "edit_kind", "message_to_user"],
        ),
        terminal=True,
    ),
    AgentTool(
        name="propose_pipeline_rerun",
        description=(
            "Rerun part of the workspace build pipeline: 'candidates' to gather "
            "papers again from Semantic Scholar, 'construct' to rebuild the tree "
            "from existing candidates, 'hydrate' to refill paper metadata and "
            "full text, or 'related' to recompute similar papers. This is "
            "expensive and needs the user's approval."
        ),
        parameters=_object(
            {
                "stage": {
                    "type": "string",
                    "enum": ["candidates", "construct", "hydrate", "related"],
                },
                "reason": {"type": "string"},
                "message_to_user": {
                    "type": "string",
                    "description": (
                        "One or two sentences telling the user what rerunning "
                        "would do and why. Shown as your chat reply above the "
                        "review card."
                    ),
                },
                "topic": {"type": "string"},
                "max_candidates": {"type": "integer"},
            },
            ["stage", "reason", "message_to_user"],
        ),
        terminal=True,
    ),
    AgentTool(
        name="critique_workspace",
        description=(
            "Audit the workspace for weak branches, misplaced papers, missing "
            "lines of work, and reading paths that are out of prerequisite order."
        ),
        parameters=_object({"focus": {"type": "string"}}, []),
        terminal=True,
    ),
)

ALL_TOOLS: dict[str, AgentTool] = {
    tool.name: tool for tool in (*READ_TOOLS, *TERMINAL_TOOLS)
}


def tool_schemas(
    *, include_web_search: bool, include_pipeline_rerun: bool = True
) -> list[dict[str, Any]]:
    schemas = [
        tool.schema()
        for tool in ALL_TOOLS.values()
        if include_pipeline_rerun or tool.name != "propose_pipeline_rerun"
    ]
    if include_web_search:
        # Executed by OpenAI, not by us: no key, no HTTP client, no SSRF surface.
        schemas.append({"type": "web_search"})
    return schemas


def web_search_enabled() -> bool:
    return os.environ.get(WEB_SEARCH_ENV_FLAG, "1").strip().lower() not in {
        "0",
        "false",
        "off",
    }


def run_tool(name: str, context: ToolContext, arguments: dict[str, Any]) -> str:
    """Run one read tool and return its JSON result, bounded in size."""

    tool = ALL_TOOLS.get(name)
    if tool is None:
        return _as_json({"error": f"unknown tool {name!r}"})
    if tool.handler is None:
        # A terminal tool that arrived beside another call the loop took
        # first. It exists; it was not run.
        return _as_json(
            {"error": f"{name} ends the turn and was not run this time. Call it again on its own."}
        )
    try:
        result = tool.handler(context, arguments)
    except Exception as error:  # Surfaced to the model, which can adapt.
        result = {"error": f"{name} failed: {error}"}
    return _as_json(result)


def _as_json(result: Any) -> str:
    text = json.dumps(result, ensure_ascii=True, default=str)
    if len(text) <= MAX_TOOL_RESULT_CHARACTERS:
        return text
    return json.dumps(
        {
            "truncated": True,
            "note": "Result was too large; ask for something narrower.",
            "partial": text[:MAX_TOOL_RESULT_CHARACTERS],
        }
    )


def _budget_exhausted() -> dict[str, Any]:
    return {
        "error": (
            "Semantic Scholar request budget for this turn is spent. Answer "
            "with what you already have."
        )
    }


def _bounded_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 10
    return max(1, min(limit, MAX_SEARCH_RESULTS))


def _tree_nodes(workspace: Mapping[str, Any]) -> list[dict[str, Any]]:
    tree = workspace.get("tree")
    nodes = tree.get("nodes") if isinstance(tree, Mapping) else None
    return [node for node in nodes or [] if isinstance(node, Mapping)]


def _paper_full_text_window(context: ToolContext, paper_id: str, offset: Any) -> dict[str, Any]:
    """One window of a paper's stored text, and where the next one starts."""

    text = ""
    if context.repository is not None:
        try:
            content = context.repository.get_paper_content(context.workspace_id, paper_id)
        except (FileNotFoundError, ValueError):
            content = {}
        text = str(content.get("full_text") or content.get("text") or "")
    try:
        start = max(0, int(offset or 0))
    except (TypeError, ValueError):
        start = 0
    end = min(len(text), start + FULL_TEXT_WINDOW_CHARACTERS)
    # The window is measured the way the result cap measures it, escaped:
    # accented and mathematical text grows several times over in JSON, and a
    # window over the cap would come back as a stub the model cannot read on from.
    while end > start and len(json.dumps(text[start:end], ensure_ascii=True)) > FULL_TEXT_WINDOW_CHARACTERS:
        end = start + (end - start) * 9 // 10
    return {
        "full_text": text[start:end],
        "offset": start,
        "next_offset": end if end < len(text) else None,
        "total_characters": len(text),
    }


def _clip(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value if len(value) <= limit else value[:limit] + "…"


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in "".join(
            char.lower() if char.isalnum() else " " for char in value
        ).split()
        if len(token) > 2
    }
