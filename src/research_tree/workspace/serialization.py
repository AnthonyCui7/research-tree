from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from research_tree.artifacts import write_json_file, write_text_file


def load_json_artifact(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_candidate_artifact(path: Path) -> dict[str, Any]:
    payload = load_json_artifact(path)
    if not isinstance(payload, dict):
        raise ValueError(f"candidate artifact must be a JSON object: {path}")
    return payload


def parse_workspace_output(raw_output: str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(raw_output, Mapping) and raw_output.get("schema_version"):
        return dict(raw_output)

    output_text = (
        extract_response_output_text(raw_output)
        if isinstance(raw_output, Mapping)
        else raw_output
    )
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as error:
        stripped = _strip_markdown_json_fence(output_text)
        if stripped == output_text:
            raise ValueError(f"workspace LLM output is not valid JSON: {error}") from error
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as stripped_error:
            raise ValueError(
                f"workspace LLM output is not valid JSON: {stripped_error}"
            ) from stripped_error
    if not isinstance(payload, dict):
        raise ValueError("workspace LLM output must be a JSON object.")
    return payload


def extract_response_output_text(raw_response: Mapping[str, Any]) -> str:
    output_text = raw_response.get("output_text")
    if isinstance(output_text, str):
        return output_text

    chunks: list[str] = []
    for item in raw_response.get("output") or []:
        if not isinstance(item, Mapping):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, Mapping):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    if chunks:
        return "".join(chunks)

    choices = raw_response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
        if isinstance(message, Mapping) and isinstance(message.get("content"), str):
            return message["content"]

    raise ValueError("OpenAI response did not contain output text.")


def write_workspace_artifacts(
    *,
    output_dir: Path,
    prompt_text: str,
    raw_llm_output: Any,
    workspace: Mapping[str, Any],
    validation: Mapping[str, Any],
    run_label: str | None = None,
    prompt_already_written: bool = False,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "prompt": output_dir / "workspace_prompt.txt",
        "raw_llm_output": output_dir / "workspace_raw_llm_output.json",
        "workspace": output_dir / "workspace.json",
        "validation": output_dir / "workspace_validation.json",
    }
    if not prompt_already_written:
        write_text_file(
            paths["prompt"],
            prompt_text,
            archive_existing=True,
            run_label=run_label,
        )
    write_json_file(
        paths["raw_llm_output"],
        raw_llm_output,
        archive_existing=True,
        run_label=run_label,
    )
    write_json_file(
        paths["workspace"],
        dict(workspace),
        archive_existing=True,
        run_label=run_label,
    )
    write_json_file(
        paths["validation"],
        dict(validation),
        archive_existing=True,
        run_label=run_label,
    )
    return paths


def _strip_markdown_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        return text
    return "\n".join(lines[1:-1]).strip()
