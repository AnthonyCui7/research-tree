from __future__ import annotations

import argparse
from pathlib import Path

from research_tree.retrieval.env import load_dotenv_file
from research_tree.workspace.construction import (
    DEFAULT_WORKSPACE_LLM_MAX_OUTPUT_TOKENS,
    DEFAULT_WORKSPACE_LLM_REASONING_EFFORT,
    DEFAULT_WORKSPACE_LLM_RESPONSE_FORMAT,
    DEFAULT_WORKSPACE_LLM_TEXT_VERBOSITY,
    DEFAULT_WORKSPACE_LLM_TIMEOUT_SECONDS,
    WORKSPACE_LLM_RESPONSE_FORMATS,
    WORKSPACE_LLM_REASONING_EFFORTS,
    WORKSPACE_LLM_TEXT_VERBOSITIES,
    construct_workspace_from_candidates,
)
from research_tree.workspace.prompts import WORKSPACE_CONSTRUCTION_PROMPT_VERSION


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CANDIDATE_PREPARATION_DIR = (
    REPO_ROOT / "experiments" / "output" / "workspace_candidate_preparation"
)
API_DEFAULT_REASONING_EFFORT = "api-default"
API_DEFAULT_TEXT_VERBOSITY = "api-default"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Construct a Research Tree workspace from llm_candidate_papers.json."
    )
    parser.add_argument(
        "--candidate-json",
        help=(
            "Path to llm_candidate_papers.json. Defaults to the newest "
            "experiments/output/workspace_candidate_preparation/runN artifact."
        ),
    )
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument(
        "--prompt-version",
        default=WORKSPACE_CONSTRUCTION_PROMPT_VERSION,
    )
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--llm-output-json",
        help=(
            "Use an existing raw LLM response or workspace JSON instead of calling "
            "the OpenAI API. Intended for tests and prompt debugging."
        ),
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=DEFAULT_WORKSPACE_LLM_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=[
            API_DEFAULT_REASONING_EFFORT,
            *sorted(WORKSPACE_LLM_REASONING_EFFORTS),
        ],
        default=DEFAULT_WORKSPACE_LLM_REASONING_EFFORT,
        help=(
            "Reasoning effort for GPT-5/o-series workspace generation. Use "
            f"{API_DEFAULT_REASONING_EFFORT!r} to omit the parameter."
        ),
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=DEFAULT_WORKSPACE_LLM_MAX_OUTPUT_TOKENS or 0,
        help=(
            "Maximum output tokens for the LLM response. Default 0 omits this "
            "parameter, leaving the response bounded only by model limits."
        ),
    )
    parser.add_argument(
        "--text-verbosity",
        choices=[
            API_DEFAULT_TEXT_VERBOSITY,
            *sorted(WORKSPACE_LLM_TEXT_VERBOSITIES),
        ],
        default=DEFAULT_WORKSPACE_LLM_TEXT_VERBOSITY,
    )
    parser.add_argument(
        "--response-format",
        choices=sorted(WORKSPACE_LLM_RESPONSE_FORMATS),
        default=DEFAULT_WORKSPACE_LLM_RESPONSE_FORMAT,
    )
    args = parser.parse_args(argv)

    if args.request_timeout_seconds <= 0:
        raise ValueError("--request-timeout-seconds must be positive.")
    if args.max_output_tokens < 0:
        raise ValueError("--max-output-tokens cannot be negative.")

    load_dotenv_file(REPO_ROOT / ".env")
    candidate_json_path = (
        Path(args.candidate_json)
        if args.candidate_json
        else latest_candidate_json_path(DEFAULT_CANDIDATE_PREPARATION_DIR)
    )
    reasoning_effort = (
        None
        if args.reasoning_effort == API_DEFAULT_REASONING_EFFORT
        else args.reasoning_effort
    )
    text_verbosity = (
        None
        if args.text_verbosity == API_DEFAULT_TEXT_VERBOSITY
        else args.text_verbosity
    )
    result = construct_workspace_from_candidates(
        candidate_json_path=candidate_json_path,
        model=args.model,
        prompt_version=args.prompt_version,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        raw_llm_output_json_path=(
            Path(args.llm_output_json) if args.llm_output_json else None
        ),
        request_timeout_seconds=args.request_timeout_seconds,
        reasoning_effort=reasoning_effort,
        max_output_tokens=(
            args.max_output_tokens if args.max_output_tokens > 0 else None
        ),
        text_verbosity=text_verbosity,
        response_format=args.response_format,
    )
    print(f"Candidate artifact: {candidate_json_path}")
    print(f"Wrote workspace: {result.output_paths['workspace']}")
    print(f"Wrote validation: {result.output_paths['validation']}")
    if result.validation.warnings:
        print(f"Validation warnings: {len(result.validation.warnings)}")
    return 0


def latest_candidate_json_path(output_base_dir: Path) -> Path:
    run_paths: list[tuple[int, Path]] = []
    for child in output_base_dir.iterdir() if output_base_dir.exists() else []:
        if not child.is_dir() or not child.name.startswith("run"):
            continue
        suffix = child.name.removeprefix("run")
        if not suffix.isdigit():
            continue
        candidate_path = child / "llm_candidate_papers.json"
        if candidate_path.is_file():
            run_paths.append((int(suffix), candidate_path))
    if run_paths:
        return max(run_paths, key=lambda item: item[0])[1]

    latest_copy = output_base_dir / "llm_candidate_papers.json"
    if latest_copy.is_file():
        return latest_copy

    raise FileNotFoundError(
        "No default candidate artifact found. Run candidate preparation first, "
        "or pass --candidate-json explicitly."
    )


if __name__ == "__main__":
    raise SystemExit(main())
