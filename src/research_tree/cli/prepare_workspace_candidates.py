from __future__ import annotations

import argparse
from pathlib import Path

from research_tree.retrieval.env import load_dotenv_file
from research_tree.retrieval.semantic_scholar import (
    SEMANTIC_SCHOLAR_KEYED_REQUEST_DELAY_SECONDS,
    SEMANTIC_SCHOLAR_MAX_RETRIES,
)
from research_tree.retrieval.candidate_preparation import (
    DEFAULT_TOPIC,
    PipelineConfig,
    run_workspace_candidate_preparation_pipeline,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare Semantic Scholar candidate artifacts for Research Tree "
            "workspace construction. This stage does not call an LLM."
        )
    )
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--non-survey-count", type=int, default=50)
    parser.add_argument("--survey-baseline-count", type=int, default=5)
    parser.add_argument("--alpha", type=float, default=1.25)
    parser.add_argument("--pool-target", type=int, default=5000)
    parser.add_argument("--root-set-size", type=int, default=250)
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=SEMANTIC_SCHOLAR_KEYED_REQUEST_DELAY_SECONDS,
    )
    parser.add_argument("--request-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--max-academic-retries", type=int, default=SEMANTIC_SCHOLAR_MAX_RETRIES)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv_file(REPO_ROOT / ".env")
    _validate_args(args)

    config = PipelineConfig(
        repo_root=REPO_ROOT,
        topic=args.topic.strip(),
        k=args.non_survey_count,
        survey_baseline_count=args.survey_baseline_count,
        request_delay_seconds=args.request_delay_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
        max_academic_retries=args.max_academic_retries,
        refresh_cache=args.refresh_cache,
        citation_age_exponent=args.alpha,
        pool_target=args.pool_target,
        root_set_size=args.root_set_size,
        verbose=not args.quiet,
    )
    output = run_workspace_candidate_preparation_pipeline(config)
    output_dir = Path(str(output["run_dir"]))
    latest_dir = Path(str(output.get("latest_dir") or output_dir.parent))
    print(
        f"Wrote {len(output['non_survey_papers'])} non-survey papers and "
        f"{len(output.get('survey_papers', []))} surveys to "
        f"{output_dir / 'llm_candidate_papers.json'}"
    )
    print(f"Latest candidate artifact: {latest_dir / 'llm_candidate_papers.json'}")
    print(
        "Latest S2 background database: "
        f"{latest_dir / 's2_bulk_deduped_paper_database.json'}"
    )
    warning_count = int(output.get("warning_count", 0))
    if warning_count:
        print(
            f"Completed with {warning_count} warning(s); see "
            f"{output_dir / 'pipeline_warnings.json'}"
        )
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
    if args.pool_target <= 0:
        raise ValueError("--pool-target must be positive.")
    if args.root_set_size <= 0:
        raise ValueError("--root-set-size must be positive.")
    if args.request_delay_seconds < 0:
        raise ValueError("--request-delay-seconds cannot be negative.")
    if args.request_timeout_seconds <= 0:
        raise ValueError("--request-timeout-seconds must be positive.")
    if args.max_academic_retries < 0:
        raise ValueError("--max-academic-retries cannot be negative.")


if __name__ == "__main__":
    raise SystemExit(main())
