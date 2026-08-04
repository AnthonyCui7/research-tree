from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from research_tree.llm import DEFAULT_MODEL
from research_tree.retrieval.env import load_dotenv_file
from research_tree.retrieval.semantic_scholar import (
    SEMANTIC_SCHOLAR_KEYED_REQUEST_DELAY_SECONDS,
    SEMANTIC_SCHOLAR_MAX_RETRIES,
    SemanticScholarClient,
)
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
from research_tree.workspace.publishing import publish_workspace_version
from research_tree.workspace.enrichment import (
    hydrate_workspace_papers,
)
from research_tree.workspace.repository import LocalJsonWorkspaceRepository
from research_tree.artifacts import write_json_file
from research_tree.workspace.schemas import paper_database_from_artifact
from research_tree.workspace.serialization import load_json_artifact
from research_tree.paths import workspaces_dir
from research_tree.workspace.similar_papers import (
    DEFAULT_SIMILAR_CITATION_AGE_EXPONENT,
    DEFAULT_SIMILAR_CITATION_SCORE_FLOOR,
    DEFAULT_SIMILAR_PAPERS_K,
    build_similar_papers,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
API_DEFAULT_REASONING_EFFORT = "api-default"
API_DEFAULT_TEXT_VERBOSITY = "api-default"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Research Tree candidate prep, workspace construction, and similar-paper enrichment."
    )
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument(
        "--workspace-id",
        help="Stable workspace ID to update. Defaults to a slug of the candidate topic.",
    )
    parser.add_argument(
        "--repository-dir",
        help="Workspace repository served by the API. Defaults to RESEARCH_TREE_DATA_DIR or data/workspaces.",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Write stage artifacts without updating the workspace served by the API.",
    )
    parser.add_argument(
        "--candidate-json",
        help=(
            "Reuse an existing llm_candidate_papers.json and skip candidate preparation. "
            "This is the safe recovery path after workspace construction fails."
        ),
    )
    parser.add_argument(
        "--paper-database-json",
        help=(
            "Paper database to use with --candidate-json. Defaults to the sibling "
            "s2_bulk_deduped_paper_database.json."
        ),
    )
    parser.add_argument("--non-survey-count", type=int, default=50)
    parser.add_argument("--survey-baseline-count", type=int, default=5)
    parser.add_argument("--alpha", type=float, default=1.25)
    parser.add_argument("--pool-target", type=int, default=5000)
    parser.add_argument("--root-set-size", type=int, default=250)
    parser.add_argument("--similar-papers-k", type=int, default=DEFAULT_SIMILAR_PAPERS_K)
    parser.add_argument(
        "--similar-papers-alpha",
        type=float,
        default=DEFAULT_SIMILAR_CITATION_AGE_EXPONENT,
        help="Age exponent for related-paper citation scoring.",
    )
    parser.add_argument(
        "--similar-papers-citation-floor",
        type=float,
        default=DEFAULT_SIMILAR_CITATION_SCORE_FLOOR,
        help="Minimum age-adjusted citation score for a related paper.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--prompt-version",
        default=WORKSPACE_CONSTRUCTION_PROMPT_VERSION,
    )
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=SEMANTIC_SCHOLAR_KEYED_REQUEST_DELAY_SECONDS,
    )
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
    parser.add_argument("--max-academic-retries", type=int, default=SEMANTIC_SCHOLAR_MAX_RETRIES)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--quiet", action="store_true")
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
    repository_dir = Path(
        args.repository_dir
        or workspaces_dir()
    )
    if args.candidate_json:
        candidate_json_path = Path(args.candidate_json).resolve()
        if not candidate_json_path.is_file():
            raise FileNotFoundError(f"Candidate artifact does not exist: {candidate_json_path}")
        run_dir = candidate_json_path.parent
        paper_database_json_path = (
            Path(args.paper_database_json).resolve()
            if args.paper_database_json
            else run_dir / "s2_bulk_deduped_paper_database.json"
        )
        if not paper_database_json_path.is_file():
            raise FileNotFoundError(
                "Paper database does not exist; pass --paper-database-json or use "
                "the matching candidate preparation run."
            )
    else:
        retrieval_config = PipelineConfig(
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
        candidate_output = run_workspace_candidate_preparation_pipeline(retrieval_config)
        run_dir = Path(str(candidate_output["run_dir"]))
        candidate_json_path = run_dir / "llm_candidate_papers.json"
        paper_database_json_path = run_dir / "s2_bulk_deduped_paper_database.json"

    _record_pipeline_stage(
        run_dir,
        "prepare_workspace_candidates",
        "reused" if args.candidate_json else "completed",
        artifacts={
            "candidate_json": str(candidate_json_path),
            "paper_database_json": str(paper_database_json_path),
        },
    )

    content_repository = LocalJsonWorkspaceRepository(
        run_dir / "local_workspace_data" if args.no_publish else repository_dir
    )
    semantic_scholar = SemanticScholarClient(
        cache_dir=repository_dir.parent / "cache" / "semantic_scholar",
        api_key=(
            os.environ.get("S2_API_KEY")
            or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
        ),
        request_delay_seconds=args.request_delay_seconds,
        refresh_cache=args.refresh_cache,
        max_retries=args.max_academic_retries,
        timeout_seconds=args.request_timeout_seconds,
    )
    try:
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
            workspace_id_override=args.workspace_id or _workspace_id_from_candidate(
                candidate_json_path
            ),
            semantic_scholar_client=semantic_scholar,
        )
    except Exception as error:
        _record_pipeline_stage(
            run_dir,
            "construct_workspace",
            "failed",
            error=str(error),
        )
        raise
    _record_pipeline_stage(
        run_dir,
        "construct_workspace",
        "completed",
        artifacts={name: str(path) for name, path in workspace_result.output_paths.items()},
    )

    try:
        hydrated_workspace, hydration_warnings = hydrate_workspace_papers(
            workspace=workspace_result.workspace,
            repository=content_repository,
            semantic_scholar=semantic_scholar,
        )
        hydrated_path = run_dir / "workspace_with_paper_content.json"
        write_json_file(hydrated_path, hydrated_workspace)
        paper_database = paper_database_from_artifact(
            load_json_artifact(paper_database_json_path)
        )
        workspace_with_similar_papers, debug = (
            build_similar_papers(
                workspace=hydrated_workspace,
                paper_database=paper_database,
                k=args.similar_papers_k,
                citation_age_exponent=args.similar_papers_alpha,
                citation_score_floor=args.similar_papers_citation_floor,
            )
        )
        similar_path = run_dir / "workspace_with_related_papers.json"
        debug_path = run_dir / "related_papers_debug.json"
        write_json_file(similar_path, workspace_with_similar_papers)
        write_json_file(debug_path, debug)
        similar_paths = {"workspace": similar_path, "debug": debug_path}
        stage_warnings = hydration_warnings
    except Exception as error:
        _record_pipeline_stage(
            run_dir,
            "hydrate_and_recommend_papers",
            "failed",
            error=str(error),
        )
        raise
    _record_pipeline_stage(
        run_dir,
        "hydrate_and_recommend_papers",
        "completed_with_warnings" if stage_warnings else "completed",
        artifacts={name: str(path) for name, path in similar_paths.items()},
        error="; ".join(stage_warnings) if stage_warnings else None,
    )

    publish_result: dict[str, object] | None = None
    if not args.no_publish:
        _attach_pipeline_provenance(
            workspace_with_similar_papers,
            run_dir=run_dir,
            candidate_json_path=candidate_json_path,
            paper_database_json_path=paper_database_json_path,
            workspace_path=workspace_result.output_paths["workspace"],
            similar_workspace_path=similar_paths["workspace"],
        )
        try:
            publish_result = publish_workspace_version(
                repository_dir=repository_dir,
                workspace=workspace_with_similar_papers,
                reason="pipeline workspace generation completed",
                event_type="workspace_pipeline_completed",
                event_payload={
                    "run_dir": str(run_dir),
                    "candidate_json": str(candidate_json_path),
                    "paper_database_json": str(paper_database_json_path),
                    "workspace_json": str(workspace_result.output_paths["workspace"]),
                    "similar_workspace_json": str(similar_paths["workspace"]),
                },
            )
        except Exception as error:
            _record_pipeline_stage(run_dir, "publish_workspace", "failed", error=str(error))
            raise
        _record_pipeline_stage(
            run_dir,
            "publish_workspace",
            "completed",
            artifacts={"repository_dir": str(publish_result["repository_dir"])},
        )

    print(f"Run directory: {run_dir}")
    print(f"Candidate artifact: {candidate_json_path}")
    print(f"Workspace: {workspace_result.output_paths['workspace']}")
    print(f"Workspace with similar papers: {similar_paths['workspace']}")
    if publish_result is not None:
        print(f"Published workspace version: {publish_result['version_hash']}")
    return 0


def _record_pipeline_stage(
    run_dir: Path,
    stage_name: str,
    status: str,
    *,
    artifacts: dict[str, str] | None = None,
    error: str | None = None,
) -> None:
    manifest_path = run_dir / "pipeline_run.json"
    if manifest_path.is_file():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        payload = {
            "schema_version": "research_tree.pipeline_run.v1",
            "run_dir": str(run_dir),
            "started_at": datetime.now(UTC).isoformat(),
            "stages": {},
        }
    stages = payload.setdefault("stages", {})
    stages[stage_name] = {
        "status": status,
        "updated_at": datetime.now(UTC).isoformat(),
        "artifacts": artifacts or {},
        "error": error,
    }
    write_json_file(manifest_path, payload)


def _workspace_id_from_candidate(candidate_json_path: Path) -> str:
    candidate = json.loads(candidate_json_path.read_text(encoding="utf-8"))
    topic = str(candidate.get("topic") or "workspace") if isinstance(candidate, dict) else "workspace"
    return re.sub(r"[^a-z0-9]+", "-", topic.casefold()).strip("-") or "workspace"


def _attach_pipeline_provenance(
    workspace: dict[str, object],
    *,
    run_dir: Path,
    candidate_json_path: Path,
    paper_database_json_path: Path,
    workspace_path: Path,
    similar_workspace_path: Path,
) -> None:
    provenance = workspace.setdefault("provenance", {})
    if not isinstance(provenance, dict):
        return
    provenance["pipeline_run"] = {
        "run_dir": str(run_dir),
        "candidate_json": str(candidate_json_path),
        "paper_database_json": str(paper_database_json_path),
        "workspace_json": str(workspace_path),
        "similar_workspace_json": str(similar_workspace_path),
        "completed_at": datetime.now(UTC).isoformat(),
    }
    provenance["updated_at"] = provenance["pipeline_run"]["completed_at"]


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
    if args.similar_papers_k <= 0:
        raise ValueError("--similar-papers-k must be positive.")
    if args.similar_papers_alpha <= 0:
        raise ValueError("--similar-papers-alpha must be positive.")
    if args.similar_papers_citation_floor < 0:
        raise ValueError("--similar-papers-citation-floor cannot be negative.")
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
