from __future__ import annotations

import argparse
import os
from pathlib import Path

from research_tree.retrieval.env import load_dotenv_file
from research_tree.retrieval.semantic_scholar import SemanticScholarClient
from research_tree.retrieval.candidate_preparation import (
    DEFAULT_TOPIC,
    PipelineConfig,
    run_workspace_candidate_preparation_pipeline,
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
from research_tree.workspace.similar_papers import (
    build_similar_papers_from_files,
    write_similar_paper_artifacts,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
API_DEFAULT_REASONING_EFFORT = "api-default"
API_DEFAULT_TEXT_VERBOSITY = "api-default"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Research Tree candidate prep, workspace construction, and similar-paper enrichment."
    )
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--non-survey-count", type=int, default=50)
    parser.add_argument("--survey-baseline-count", type=int, default=5)
    parser.add_argument("--alpha", type=float, default=1.25)
    parser.add_argument("--s2-bulk-citation-multiplier", type=int, default=50)
    parser.add_argument("--similar-papers-k", type=int, default=10)
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument(
        "--prompt-version",
        default=WORKSPACE_CONSTRUCTION_PROMPT_VERSION,
    )
    parser.add_argument("--request-delay-seconds", type=float, default=1.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=20.0)
    parser.add_argument(
        "--llm-request-timeout-seconds",
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
    parser.add_argument("--max-academic-retries", type=int, default=2)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--cross-encoder-model",
        default="cross-encoder/ms-marco-MiniLM-L6-v2",
    )
    parser.add_argument("--bi-encoder-model", default="all-MiniLM-L6-v2")
    parser.add_argument(
        "--llm-output-json",
        help=(
            "Use an existing raw LLM response or workspace JSON instead of "
            "calling the OpenAI API."
        ),
    )
    args = parser.parse_args(argv)
    _validate_args(args)

    load_dotenv_file(REPO_ROOT / ".env")
    retrieval_config = PipelineConfig(
        repo_root=REPO_ROOT,
        topic=args.topic.strip(),
        k=args.non_survey_count,
        survey_baseline_count=args.survey_baseline_count,
        request_delay_seconds=args.request_delay_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
        max_academic_retries=args.max_academic_retries,
        refresh_cache=args.refresh_cache,
        cross_encoder_model=args.cross_encoder_model,
        citation_age_exponent=args.alpha,
        s2_bulk_citation_multiplier=args.s2_bulk_citation_multiplier,
        verbose=not args.quiet,
    )
    candidate_output = run_workspace_candidate_preparation_pipeline(retrieval_config)
    run_dir = Path(str(candidate_output["run_dir"]))
    candidate_json_path = run_dir / "llm_candidate_papers.json"
    paper_database_json_path = run_dir / "s2_bulk_deduped_paper_database.json"

    workspace_result = construct_workspace_from_candidates(
        candidate_json_path=candidate_json_path,
        model=args.model,
        prompt_version=args.prompt_version,
        output_dir=run_dir,
        raw_llm_output_json_path=(
            Path(args.llm_output_json) if args.llm_output_json else None
        ),
        request_timeout_seconds=args.llm_request_timeout_seconds,
        reasoning_effort=(
            None
            if args.reasoning_effort == API_DEFAULT_REASONING_EFFORT
            else args.reasoning_effort
        ),
        max_output_tokens=(
            args.max_output_tokens if args.max_output_tokens > 0 else None
        ),
        text_verbosity=(
            None
            if args.text_verbosity == API_DEFAULT_TEXT_VERBOSITY
            else args.text_verbosity
        ),
        response_format=args.response_format,
        semantic_scholar_client=SemanticScholarClient(
            cache_dir=REPO_ROOT / "experiments" / "cache" / "semantic_scholar",
            api_key=(
                os.environ.get("S2_API_KEY")
                or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
            ),
            request_delay_seconds=args.request_delay_seconds,
            refresh_cache=args.refresh_cache,
            max_retries=args.max_academic_retries,
            timeout_seconds=args.request_timeout_seconds,
        ),
    )
    workspace_with_similar_papers, debug = build_similar_papers_from_files(
        workspace_json_path=workspace_result.output_paths["workspace"],
        paper_database_json_path=paper_database_json_path,
        k=args.similar_papers_k,
        bi_encoder_model=args.bi_encoder_model,
        cross_encoder_model=args.cross_encoder_model,
    )
    similar_paths = write_similar_paper_artifacts(
        output_dir=run_dir,
        workspace_with_similar_papers=workspace_with_similar_papers,
        debug=debug,
        run_label=run_dir.name,
    )

    print(f"Run directory: {run_dir}")
    print(f"Candidate artifact: {candidate_json_path}")
    print(f"Workspace: {workspace_result.output_paths['workspace']}")
    print(f"Workspace with similar papers: {similar_paths['workspace']}")
    return 0


def _validate_args(args: argparse.Namespace) -> None:
    if not args.topic.strip():
        raise ValueError("--topic cannot be empty.")
    if args.non_survey_count <= 0:
        raise ValueError("--non-survey-count must be positive.")
    if args.survey_baseline_count < 0:
        raise ValueError("--survey-baseline-count cannot be negative.")
    if args.alpha <= 0:
        raise ValueError("--alpha must be positive.")
    if args.s2_bulk_citation_multiplier <= 0:
        raise ValueError("--s2-bulk-citation-multiplier must be positive.")
    if args.similar_papers_k <= 0:
        raise ValueError("--similar-papers-k must be positive.")
    if args.request_delay_seconds < 0:
        raise ValueError("--request-delay-seconds cannot be negative.")
    if args.request_timeout_seconds <= 0:
        raise ValueError("--request-timeout-seconds must be positive.")
    if args.llm_request_timeout_seconds <= 0:
        raise ValueError("--llm-request-timeout-seconds must be positive.")
    if args.max_output_tokens < 0:
        raise ValueError("--max-output-tokens cannot be negative.")
    if args.max_academic_retries < 0:
        raise ValueError("--max-academic-retries cannot be negative.")


if __name__ == "__main__":
    raise SystemExit(main())
