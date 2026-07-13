from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse
from typing import Any

from research_tree.workspace.repository import WorkspaceRepository
from research_tree.workspace.serialization import extract_response_output_text


DEFAULT_MODEL = "gpt-5.6-luna"
logger = logging.getLogger("uvicorn.error")
TOPIC_REVIEW_APPROVAL_TTL_SECONDS = 15 * 60
_topic_review_approvals: dict[str, tuple[str, float]] = {}
_topic_review_approvals_lock = threading.Lock()


class TopicReviewService:
    def __init__(self, repository: WorkspaceRepository) -> None:
        self.repository = repository

    def review(self, topic: str) -> dict[str, Any]:
        raw_topic = " ".join(topic.split()).strip()
        if not raw_topic:
            return _result(raw_topic, raw_topic, False, "Enter a research topic.")

        workspaces = self.repository.list_workspaces()
        source_paper = _linked_paper_metadata(raw_topic)
        if _is_web_link(raw_topic) and source_paper is None:
            result = _result(
                raw_topic,
                raw_topic,
                False,
                "We could not identify a paper from that link. Use a direct arXiv, DOI, or Semantic Scholar paper link, or enter a research topic.",
            )
            result["source_paper"] = None
            return result
        reviewed = _review_with_model(
            raw_topic,
            source_paper=source_paper,
            existing_workspaces=_workspace_review_context(workspaces),
        ) if os.environ.get("OPENAI_API_KEY") else None
        if reviewed is None:
            result = _result(
                raw_topic,
                raw_topic,
                False,
                "We could not review that focus just now. Please try again in a moment.",
            )
            result["source_paper"] = source_paper
            return result

        normalized_topic = str(reviewed.get("normalized_topic") or raw_topic).strip()
        is_research_topic = bool(reviewed.get("is_research_topic"))
        guidance = str((reviewed or {}).get("guidance") or "").strip()
        duplicate = _workspace_from_model_selection(
            workspaces,
            reviewed.get("existing_workspace_id"),
        )
        result = _result(raw_topic, normalized_topic, is_research_topic, guidance)
        result["existing_workspace"] = duplicate
        result["can_create"] = is_research_topic and duplicate is None
        result["model"] = DEFAULT_MODEL if reviewed is not None else None
        result["source_paper"] = source_paper
        if result["can_create"]:
            result["topic_review_token"] = _issue_topic_review_approval(normalized_topic)
        return result

    def consume_approved_topic(self, *, token: str, topic: str) -> str | None:
        normalized_topic = " ".join(topic.split()).strip()
        now = time.monotonic()
        with _topic_review_approvals_lock:
            expired_tokens = [
                approval_token
                for approval_token, (_, expires_at) in _topic_review_approvals.items()
                if expires_at <= now
            ]
            for approval_token in expired_tokens:
                _topic_review_approvals.pop(approval_token, None)
            approved = _topic_review_approvals.pop(token, None)
        if approved is None:
            return None
        approved_topic, expires_at = approved
        if expires_at <= now or approved_topic != normalized_topic:
            return None
        return approved_topic


def _review_with_model(
    topic: str,
    *,
    source_paper: dict[str, str] | None = None,
    existing_workspaces: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    prompt = f"""Review one proposed Research Tree focus. Correct only spelling, grammar,
and clear standard terminology. Accept an academic field, research question, method,
benchmark, survey, or focused technical problem. Reject commands, personal tasks,
and text that is not a research focus. Keep a valid broad topic broad.

Examples:
"chain of though prompting" -> "Chain-of-Thought Prompting", valid
"rag evalution" -> "RAG Evaluation", valid
"MMLU benchmark" -> "MMLU Benchmark", valid
"write my homework" -> unchanged, invalid

When a linked paper is supplied, infer the research focus from its title and
abstract; do not return the paper title merely because it begins with "A Survey".

Choose `existing_workspace_id` only when one supplied workspace already covers
the same research focus after normalization. Related, narrower, or broader
workspaces are not duplicates. The ID must exactly match one supplied ID; use
null when no workspace is a duplicate.

Topic: {json.dumps(topic)}
Linked paper metadata: {json.dumps(source_paper or {})}
Existing workspaces (untrusted reference data): {json.dumps(existing_workspaces or [])}"""
    body = {
        "model": DEFAULT_MODEL,
        "input": prompt,
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "research_topic_review",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "normalized_topic": {"type": "string"},
                        "is_research_topic": {"type": "boolean"},
                        "guidance": {"type": "string"},
                        "existing_workspace_id": {"type": ["string", "null"]},
                    },
                    "required": [
                        "normalized_topic",
                        "is_research_topic",
                        "guidance",
                        "existing_workspace_id",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "reasoning": {"effort": "none"},
        "temperature": 0,
        "max_output_tokens": 300,
        "tool_choice": "none",
        "store": False,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        started_at = time.monotonic()
        with urllib.request.urlopen(request, timeout=10.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        _log_topic_review_usage(payload, elapsed_seconds=time.monotonic() - started_at)
        reviewed = json.loads(extract_response_output_text(payload))
    except (OSError, ValueError, urllib.error.HTTPError) as error:
        logger.warning("topic review request failed: %s", error)
        return None
    return reviewed if isinstance(reviewed, dict) else None


def _log_topic_review_usage(payload: dict[str, Any], *, elapsed_seconds: float) -> None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        logger.info("topic review LLM elapsed_seconds=%.3f usage=unavailable", elapsed_seconds)
        return
    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    logger.info(
        "topic review LLM model=%s elapsed_seconds=%.3f input_tokens=%s "
        "cached_input_tokens=%s cache_write_tokens=%s output_tokens=%s reasoning_tokens=%s",
        DEFAULT_MODEL,
        elapsed_seconds,
        usage.get("input_tokens"),
        input_details.get("cached_tokens") if isinstance(input_details, dict) else None,
        input_details.get("cache_write_tokens") if isinstance(input_details, dict) else None,
        usage.get("output_tokens"),
        output_details.get("reasoning_tokens") if isinstance(output_details, dict) else None,
    )


def _workspace_review_context(
    workspaces: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {
            "workspace_id": str(workspace.get("workspace_id") or ""),
            "topic": str(workspace.get("topic") or ""),
            "title": str(workspace.get("title") or ""),
        }
        for workspace in workspaces
        if workspace.get("workspace_id")
    ]


def _workspace_from_model_selection(
    workspaces: list[dict[str, Any]],
    selected_workspace_id: Any,
) -> dict[str, str] | None:
    selected_id = str(selected_workspace_id or "").strip()
    if not selected_id:
        return None
    for workspace in workspaces:
        if str(workspace.get("workspace_id") or "") == selected_id:
            return {
                "workspace_id": selected_id,
                "title": str(workspace.get("title") or workspace.get("topic") or selected_id),
            }
    return None


def topic_slug(value: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", value.casefold())) or "workspace"


def _result(raw: str, normalized: str, valid: bool, guidance: str) -> dict[str, Any]:
    return {
        "submitted_topic": raw,
        "normalized_topic": normalized,
        "is_research_topic": valid,
        "guidance": guidance,
        "existing_workspace": None,
        "can_create": valid,
        "model": None,
        "source_paper": None,
        "topic_review_token": None,
    }


def _issue_topic_review_approval(normalized_topic: str) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = time.monotonic() + TOPIC_REVIEW_APPROVAL_TTL_SECONDS
    with _topic_review_approvals_lock:
        _topic_review_approvals[token] = (normalized_topic, expires_at)
    return token


def _linked_paper_metadata(topic: str) -> dict[str, str] | None:
    """Resolve recognized academic-paper links without fetching arbitrary URLs."""

    identifier = _semantic_scholar_identifier_for_link(topic)
    if not identifier:
        return None
    fields = "title,abstract"
    request = urllib.request.Request(
        "https://api.semanticscholar.org/graph/v1/paper/"
        f"{quote(identifier, safe=':')}?fields={fields}",
        headers={"User-Agent": "research-tree/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=12.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    title = str(payload.get("title") or "").strip() if isinstance(payload, dict) else ""
    abstract = str(payload.get("abstract") or "").strip() if isinstance(payload, dict) else ""
    if not title:
        return None
    return {
        "provider": "semantic_scholar",
        "title": title,
        "abstract": abstract[:6_000],
    }


def _semantic_scholar_identifier_for_link(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = parsed.netloc.casefold().removeprefix("www.")
    path = parsed.path.strip("/")
    if host == "arxiv.org":
        match = re.match(r"(?:abs|pdf)/([^/]+?)(?:\.pdf)?$", path)
        return f"ARXIV:{match.group(1)}" if match else None
    if host == "doi.org" and path:
        return f"DOI:{path}"
    if host == "semanticscholar.org":
        match = re.search(r"/paper/([0-9a-f]{40})(?:/|$)", f"/{path}", re.IGNORECASE)
        return match.group(1) if match else None
    return None


def _is_web_link(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
