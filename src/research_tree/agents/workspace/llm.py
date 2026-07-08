from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from research_tree.agents.workspace.models import (
    AgentIntent,
    AgentNextAction,
    WorkspaceChatResponse,
    WorkspaceCritique,
)
from research_tree.workspace.serialization import extract_response_output_text


StructuredModelT = TypeVar("StructuredModelT", bound=BaseModel)


class WorkspaceAgentLlmClient(Protocol):
    def complete_structured(
        self,
        *,
        prompt: str,
        response_model: type[StructuredModelT],
        model_name: str | None = None,
    ) -> StructuredModelT:
        ...

    def complete_text(
        self,
        *,
        prompt: str,
        model_name: str | None = None,
    ) -> str:
        ...


class DeterministicWorkspaceAgentLlmClient:
    """Small offline fallback for tests and local graph development."""

    def __init__(
        self,
        *,
        structured_outputs: list[BaseModel | dict[str, Any]] | None = None,
        text_outputs: list[str] | None = None,
    ) -> None:
        self.structured_outputs = deque(structured_outputs or [])
        self.text_outputs = deque(text_outputs or [])

    def complete_structured(
        self,
        *,
        prompt: str,
        response_model: type[StructuredModelT],
        model_name: str | None = None,
    ) -> StructuredModelT:
        if self.structured_outputs:
            payload = self.structured_outputs.popleft()
            if isinstance(payload, response_model):
                return payload
            return response_model.model_validate(payload)
        return _heuristic_structured_output(prompt, response_model)

    def complete_text(
        self,
        *,
        prompt: str,
        model_name: str | None = None,
    ) -> str:
        if self.text_outputs:
            return self.text_outputs.popleft()
        return "I can answer using the current Research Tree workspace context, but no live LLM client is configured."


class OpenAIResponsesAgentClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        default_model: str = "gpt-5.4-mini",
        timeout_seconds: float = 120.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAI agent calls.")
        self.default_model = default_model
        self.timeout_seconds = max(timeout_seconds, 1.0)

    def complete_structured(
        self,
        *,
        prompt: str,
        response_model: type[StructuredModelT],
        model_name: str | None = None,
    ) -> StructuredModelT:
        raw_response = self._call_responses_api(
            prompt=prompt,
            model=model_name or self.default_model,
            text_format={
                "type": "json_schema",
                "name": response_model.__name__,
                "strict": False,
                "schema": response_model.model_json_schema(),
            },
        )
        output_text = extract_response_output_text(raw_response)
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"OpenAI structured output was not valid JSON: {error}"
            ) from error
        return response_model.model_validate(payload)

    def complete_text(
        self,
        *,
        prompt: str,
        model_name: str | None = None,
    ) -> str:
        raw_response = self._call_responses_api(
            prompt=prompt,
            model=model_name or self.default_model,
            text_format={"type": "text"},
        )
        return extract_response_output_text(raw_response)

    def _call_responses_api(
        self,
        *,
        prompt: str,
        model: str,
        text_format: dict[str, Any],
    ) -> dict[str, Any]:
        body = {
            "model": model,
            "input": prompt,
            "text": {"format": text_format},
            "tool_choice": "none",
        }
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
                request,
                timeout=self.timeout_seconds,
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI agent LLM call failed: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"OpenAI agent LLM call failed: {error}") from error
        except (TimeoutError, socket.timeout) as error:
            raise RuntimeError(
                f"OpenAI agent LLM call timed out after {self.timeout_seconds:g}s."
            ) from error


def default_workspace_agent_llm_client() -> WorkspaceAgentLlmClient:
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIResponsesAgentClient()
    return DeterministicWorkspaceAgentLlmClient()


def _heuristic_structured_output(
    prompt: str,
    response_model: type[StructuredModelT],
) -> StructuredModelT:
    payload = _prompt_payload(prompt)
    user_message = str(payload.get("user_message") or "")
    normalized = user_message.casefold()
    if response_model is AgentIntent:
        if any(word in normalized for word in ("retrieve", "find more", "more papers")):
            payload = {
                "intent_type": "retrieve_more_papers",
                "confidence": 0.7,
                "requires_workspace_modification": False,
                "requires_more_papers": True,
                "reason": "The request asks for additional papers.",
            }
        elif any(word in normalized for word in ("critique", "weak", "misplaced", "validate")):
            payload = {
                "intent_type": "critique_workspace",
                "confidence": 0.7,
                "requires_workspace_modification": False,
                "requires_more_papers": False,
                "reason": "The request asks for critique or validation.",
            }
        elif any(word in normalized for word in ("rename", "move", "split", "merge", "promote", "demote", "rewrite", "update", "expand")):
            payload = {
                "intent_type": "modify_workspace",
                "confidence": 0.6,
                "requires_workspace_modification": True,
                "requires_more_papers": "more paper" in normalized,
                "reason": "The request appears to ask for a workspace edit.",
            }
        else:
            payload = {
                "intent_type": "chat",
                "confidence": 0.6,
                "requires_workspace_modification": False,
                "requires_more_papers": False,
                "reason": "The request can be answered from workspace context.",
            }
        return response_model.model_validate(payload)

    if response_model is AgentNextAction:
        intent = payload.get("intent") if isinstance(payload.get("intent"), dict) else {}
        if intent.get("requires_more_papers"):
            action_type = "prepare_retrieval_rerun"
        elif intent.get("intent_type") == "critique_workspace":
            action_type = "critique_workspace"
        elif intent.get("requires_workspace_modification"):
            action_type = "construct_workspace_modification"
        else:
            action_type = "answer_chat"
        return response_model.model_validate(
            {
                "action_type": action_type,
                "reason": "Deterministic fallback selected the safest matching action.",
                "modification_instruction": None,
            }
        )

    if response_model is WorkspaceCritique:
        return response_model.model_validate(
            {
                "summary": "No live critique model is configured.",
                "findings": [],
                "should_modify_workspace": False,
            }
        )

    if response_model is WorkspaceChatResponse:
        return response_model.model_validate(
            {
                "answer": "No live chat model is configured for this workspace agent.",
                "referenced_paper_ids": [],
                "referenced_branch_ids": [],
            }
        )

    return response_model.model_validate({})


def _prompt_payload(prompt: str) -> dict[str, Any]:
    marker = "# Payload\n"
    if marker not in prompt:
        return {}
    raw_payload = prompt.split(marker, 1)[1].strip()
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
