from __future__ import annotations

import argparse
import os
from pathlib import Path

from research_tree.llm import DEFAULT_MODEL
from research_tree.retrieval.env import load_dotenv_file
from research_tree.retrieval.semantic_scholar import (
    SEMANTIC_SCHOLAR_KEYED_REQUEST_DELAY_SECONDS,
    SEMANTIC_SCHOLAR_MAX_RETRIES,
    SemanticScholarClient,
)
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
from research_tree.workspace.publishing import publish_workspace_version
from research_tree.paths import data_root, semantic_scholar_cache_dir
from research_tree.paths import workspaces_dir


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CANDIDATE_PREPARATION_DIR = (
    data_root() / "candidate_runs"
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
            "candidate_runs/runN artifact under the data directory."
        ),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
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
        "--s2-request-delay-seconds",
        type=float,
        default=SEMANTIC_SCHOLAR_KEYED_REQUEST_DELAY_SECONDS,
        help="Minimum delay between Semantic Scholar requests; 1.0 respects its keyed rate guidance.",
    )
    parser.add_argument("--s2-request-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--s2-max-retries", type=int, default=SEMANTIC_SCHOLAR_MAX_RETRIES)
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
    parser.add_argument(
        "--publish-to-repository",
        action="store_true",
        help=(
            "Also save the constructed workspace as the current version in the "
            "local workspace repository."
        ),
    )
    parser.add_argument(
        "--repository-dir",
        help=(
            "Workspace repository directory for --publish-to-repository. Defaults "
            "to RESEARCH_TREE_DATA_DIR or data/workspaces."
        ),
    )
    parser.add_argument(
        "--publish-reason",
        default="constructed workspace from candidate artifact",
    )
    parser.add_argument(
        "--workspace-id",
        help=(
            "Override the generated workspace ID. Use the existing ID when "
            "rebuilding a workspace from the same candidate artifact."
        ),
    )
    parser.add_argument(
        "--expected-current-version",
        help=(
            "Only publish if this workspace version is still current. This "
            "prevents replacing a workspace that changed during construction."
        ),
    )
    args = parser.parse_args(argv)

    if args.request_timeout_seconds <= 0:
        raise ValueError("--request-timeout-seconds must be positive.")
    if args.max_output_tokens < 0:
        raise ValueError("--max-output-tokens cannot be negative.")
    if args.s2_request_delay_seconds < 0:
        raise ValueError("--s2-request-delay-seconds cannot be negative.")
    if args.s2_request_timeout_seconds <= 0:
        raise ValueError("--s2-request-timeout-seconds must be positive.")
    if args.s2_max_retries < 0:
        raise ValueError("--s2-max-retries cannot be negative.")
    if args.expected_current_version and not args.publish_to_repository:
        raise ValueError("--expected-current-version requires --publish-to-repository.")

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
    semantic_scholar_client = None
    if not args.llm_output_json:
        semantic_scholar_client = SemanticScholarClient(
            cache_dir=semantic_scholar_cache_dir(),
            api_key=(
                os.environ.get("S2_API_KEY")
                or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
            ),
            request_delay_seconds=args.s2_request_delay_seconds,
            max_retries=args.s2_max_retries,
            timeout_seconds=args.s2_request_timeout_seconds,
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
        workspace_id_override=args.workspace_id,
        semantic_scholar_client=semantic_scholar_client,
    )
    print(f"Candidate artifact: {candidate_json_path}")
    print(f"Wrote workspace: {result.output_paths['workspace']}")
    print(f"Wrote validation: {result.output_paths['validation']}")
    if args.publish_to_repository:
        publish_result = publish_workspace_version(
            repository_dir=_repository_dir(args.repository_dir),
            workspace=result.workspace,
            reason=args.publish_reason,
            event_type="workspace_constructed",
            event_payload={
                "candidate_artifact": str(candidate_json_path.resolve()),
                "workspace_artifact": str(result.output_paths["workspace"]),
                "validation_artifact": str(result.output_paths["validation"]),
                "model": args.model,
                "prompt_version": args.prompt_version,
            },
            expected_parent_version_hash=args.expected_current_version,
        )
        if publish_result["published"]:
            print(f"Published workspace version: {publish_result['version_hash']}")
            print(f"Replaced version: {publish_result['parent_version_hash']}")
            print(f"Published workspace event: {publish_result['event_id']}")
        else:
            print(f"Workspace already current: {publish_result['version_hash']}")
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


def _repository_dir(value: str | None) -> Path:
    return Path(value) if value else workspaces_dir()


if __name__ == "__main__":
    raise SystemExit(main())
