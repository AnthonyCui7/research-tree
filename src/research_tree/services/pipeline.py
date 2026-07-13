from __future__ import annotations

import copy
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from typing import Any, Callable
from uuid import uuid4

from research_tree.artifacts import write_json_file
from research_tree.retrieval.candidate_preparation import (
    PipelineConfig,
    run_workspace_candidate_preparation_pipeline,
)
from research_tree.retrieval.semantic_scholar import SemanticScholarClient
from research_tree.services.errors import InvalidPayloadError, WorkspaceNotFoundError
from research_tree.services.topics import DEFAULT_MODEL, TopicReviewService, topic_slug
from research_tree.workspace.construction import construct_workspace_from_candidates
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.publishing import publish_workspace_version
from research_tree.workspace.repository import LocalJsonWorkspaceRepository
from research_tree.workspace.enrichment import (
    hydrate_workspace_papers,
)
from research_tree.workspace.schemas import paper_database_from_artifact
from research_tree.workspace.serialization import load_json_artifact
from research_tree.workspace.similar_papers import (
    DEFAULT_SIMILAR_CITATION_AGE_EXPONENT,
    DEFAULT_SIMILAR_CITATION_SCORE_FLOOR,
    DEFAULT_SIMILAR_PAPERS_K,
    build_similar_papers,
)


# The local FastAPI server configures this logger at INFO without requiring a
# second logging configuration for the application.
logger = logging.getLogger("uvicorn.error")


PIPELINE_STAGES = ("candidates", "construct", "hydrate", "related")


class WorkspacePipelineService:
    def __init__(
        self,
        repository: LocalJsonWorkspaceRepository,
        *,
        repo_root: Path,
        dispatch: Callable[[Callable[[], None], str], None] | None = None,
    ) -> None:
        self.repository = repository
        self.repo_root = repo_root.resolve()
        self.dispatch = dispatch or _dispatch_local_thread

    def start_new_workspace(self, *, topic: str, model: str = DEFAULT_MODEL) -> dict[str, Any]:
        review = TopicReviewService(self.repository).review(topic)
        if not review["is_research_topic"]:
            raise InvalidPayloadError(review["guidance"] or "Enter an academic research topic.")
        if review["existing_workspace"]:
            raise InvalidPayloadError(
                "A workspace for this topic already exists. Open it and use the Assistant to revise or expand it."
            )
        return self._start_approved_new_workspace(
            topic=str(review["normalized_topic"]),
            model=model,
        )

    def start_approved_new_workspace(
        self,
        *,
        topic: str,
        topic_review_token: str,
        model: str = DEFAULT_MODEL,
    ) -> dict[str, Any]:
        normalized_topic = TopicReviewService(self.repository).consume_approved_topic(
            token=topic_review_token,
            topic=topic,
        )
        if normalized_topic is None:
            raise InvalidPayloadError(
                "Review the research focus again before building a workspace."
            )
        return self._start_approved_new_workspace(topic=normalized_topic, model=model)

    def _start_approved_new_workspace(self, *, topic: str, model: str) -> dict[str, Any]:
        normalized_topic = topic
        workspace_id = self._available_workspace_id(topic_slug(normalized_topic))
        return self._start(
            workspace_id=workspace_id,
            topic=normalized_topic,
            model=model,
            start_stage="candidates",
            source_run=None,
            source_version_hash=None,
            reserve_topic=True,
        )

    def rerun(
        self,
        workspace_id: str,
        *,
        start_stage: str,
        model: str = DEFAULT_MODEL,
        expected_version_hash: str | None = None,
    ) -> dict[str, Any]:
        if start_stage not in PIPELINE_STAGES:
            raise InvalidPayloadError(f"start_stage must be one of {PIPELINE_STAGES}.")
        try:
            workspace = self.repository.get_current_workspace(workspace_id)
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError(f"workspace does not exist: {workspace_id}") from error
        current_hash = workspace_version_hash(workspace)
        if expected_version_hash and current_hash != expected_version_hash:
            raise InvalidPayloadError("Workspace changed. Refresh before starting a pipeline rerun.")
        prior_runs = self.repository.list_pipeline_runs(workspace_id)
        source_run = next(
            (run for run in prior_runs if run.get("status") in {"completed", "completed_with_warnings"}),
            None,
        )
        if start_stage != "candidates" and source_run is None:
            raise InvalidPayloadError(
                "No completed pipeline run is available to provide reusable stage artifacts."
            )
        return self._start(
            workspace_id=workspace_id,
            topic=str(workspace.get("topic") or workspace.get("title") or ""),
            model=model,
            start_stage=start_stage,
            source_run=source_run,
            source_version_hash=current_hash,
        )

    def get_run(self, run_id: str) -> dict[str, Any]:
        try:
            return self.repository.get_pipeline_run(run_id)
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError(f"pipeline run does not exist: {run_id}") from error

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run.get("status") in {"queued", "running"}:
            self.repository.cancel_pipeline_runs(str(run.get("workspace_id") or ""))
            return self.get_run(run_id)
        return run

    def list_runs(self, workspace_id: str) -> dict[str, Any]:
        return {
            "workspace_id": workspace_id,
            "pipeline_runs": self.repository.list_pipeline_runs(workspace_id),
        }

    def _start(
        self,
        *,
        workspace_id: str,
        topic: str,
        model: str,
        start_stage: str,
        source_run: dict[str, Any] | None,
        source_version_hash: str | None,
        reserve_topic: bool = False,
    ) -> dict[str, Any]:
        run_id = f"pipeline_{uuid4().hex}"
        start_index = PIPELINE_STAGES.index(start_stage)
        run = {
            "schema_version": "research_tree.pipeline_run.v2",
            "run_id": run_id,
            "workspace_id": workspace_id,
            "topic": topic,
            "model": model or DEFAULT_MODEL,
            "status": "queued",
            "current_stage": None,
            "requested_stages": list(PIPELINE_STAGES[start_index:]),
            "source_run_id": source_run.get("run_id") if source_run else None,
            "source_workspace_version_hash": source_version_hash,
            "runner_pid": os.getpid(),
            "created_at": _now(),
            "updated_at": _now(),
            "stages": {},
            "warnings": [],
            "error": None,
        }
        try:
            if reserve_topic:
                self.repository.reserve_new_workspace_run(run)
            else:
                self.repository.reserve_pipeline_rerun(run)
        except ValueError as error:
            raise InvalidPayloadError(str(error)) from error
        self.dispatch(
            lambda: self._execute(run_id, copy.deepcopy(source_run)),
            f"research-tree-{run_id}",
        )
        return run

    def _execute(self, run_id: str, source_run: dict[str, Any] | None) -> None:
        run = self.repository.get_pipeline_run(run_id)
        logger.info(
            "workspace pipeline started run_id=%s workspace_id=%s stages=%s",
            run_id,
            run["workspace_id"],
            ",".join(run["requested_stages"]),
        )
        if self._run_was_cancelled(run_id):
            logger.info("workspace pipeline cancelled before start run_id=%s", run_id)
            return
        run["status"] = "running"
        run["updated_at"] = _now()
        self.repository.save_pipeline_run(run)
        artifacts = self._execution_artifacts(run, source_run)
        warnings: list[str] = []
        try:
            if "candidates" in run["requested_stages"]:
                self._stage(run, "candidates", "running", inputs={"topic": run["topic"]})
                candidate_output = run_workspace_candidate_preparation_pipeline(
                    PipelineConfig(repo_root=self.repo_root, topic=run["topic"], verbose=False)
                )
                run_dir = self._safe_artifact_path(str(candidate_output["run_dir"]))
                artifacts.update(
                    {
                        "run_dir": str(run_dir),
                        "candidate_json": str(run_dir / "llm_candidate_papers.json"),
                        "paper_database_json": str(run_dir / "s2_bulk_deduped_paper_database.json"),
                    }
                )
                self._stage(run, "candidates", "completed", outputs=artifacts)

            run_dir = self._required_artifact(artifacts, "run_dir")
            workspace = None
            published_parent_hash = run.get("source_workspace_version_hash")
            if "construct" in run["requested_stages"]:
                candidate_json = self._required_artifact(artifacts, "candidate_json")
                self._stage(
                    run,
                    "construct",
                    "running",
                    inputs={"candidate_json": str(candidate_json), "model": run["model"]},
                )
                result = construct_workspace_from_candidates(
                    candidate_json_path=candidate_json,
                    model=run["model"],
                    output_dir=run_dir,
                    workspace_id_override=run["workspace_id"],
                )
                workspace = result.workspace
                artifacts["workspace_json"] = str(result.output_paths["workspace"])
                self._stage(run, "construct", "completed", outputs={"workspace_json": artifacts["workspace_json"]})
            else:
                workspace = self.repository.get_current_workspace(run["workspace_id"])
                source_workspace_path = run_dir / f"workspace-source-{run_id}.json"
                write_json_file(source_workspace_path, workspace)
                artifacts["workspace_json"] = str(source_workspace_path)

            semantic_scholar = self._semantic_scholar_client()
            if "hydrate" in run["requested_stages"]:
                self._stage(
                    run,
                    "hydrate",
                    "running",
                    inputs={"workspace_json": artifacts["workspace_json"]},
                )
                workspace, hydration_warnings = hydrate_workspace_papers(
                    workspace=workspace,
                    repository=self.repository,
                    semantic_scholar=semantic_scholar,
                )
                warnings.extend(hydration_warnings)
                hydrated_path = run_dir / "workspace_with_paper_content.json"
                provenance = workspace.setdefault("provenance", {})
                if isinstance(provenance, dict):
                    now = _now()
                    provenance["pipeline_run_id"] = run_id
                    provenance["pipeline_run"] = {
                        "run_id": run_id,
                        "candidate_json": artifacts.get("candidate_json"),
                        "paper_database_json": artifacts.get("paper_database_json"),
                        "workspace_json": artifacts.get("workspace_json"),
                        "similar_workspace_json": None,
                        "core_ready_at": now,
                    }
                    provenance["updated_at"] = now
                write_json_file(hydrated_path, workspace)
                artifacts["hydrated_workspace_json"] = str(hydrated_path)
                self._stage(
                    run,
                    "hydrate",
                    "completed_with_warnings" if hydration_warnings else "completed",
                    outputs={"workspace_json": str(hydrated_path)},
                )
                publish = publish_workspace_version(
                    repository_dir=self.repository.base_dir,
                    workspace=workspace,
                    reason=f"pipeline run {run_id} core workspace ready",
                    event_type="workspace_pipeline_core_ready",
                    event_payload={
                        "pipeline_run_id": run_id,
                        "completed_stages": [
                            stage for stage in run["requested_stages"] if stage != "related"
                        ],
                        "remaining_stages": ["related"]
                        if "related" in run["requested_stages"]
                        else [],
                    },
                    expected_parent_version_hash=published_parent_hash,
                    pipeline_run_id=run_id,
                )
                published_parent_hash = publish["version_hash"]
                artifacts["core_workspace_version_hash"] = publish["version_hash"]
                run["artifacts"] = artifacts
                self.repository.save_pipeline_run(run)

            if "related" in run["requested_stages"]:
                self._stage(run, "related", "running")
                paper_database_path = self._required_artifact(artifacts, "paper_database_json")
                paper_database = paper_database_from_artifact(
                    load_json_artifact(paper_database_path)
                )
                workspace, debug = build_similar_papers(
                    workspace=workspace,
                    paper_database=paper_database,
                    k=DEFAULT_SIMILAR_PAPERS_K,
                    citation_age_exponent=DEFAULT_SIMILAR_CITATION_AGE_EXPONENT,
                    citation_score_floor=DEFAULT_SIMILAR_CITATION_SCORE_FLOOR,
                )
                related_path = run_dir / "workspace_with_related_papers.json"
                debug_path = run_dir / "related_papers_debug.json"
                write_json_file(related_path, workspace)
                write_json_file(debug_path, debug)
                artifacts["enriched_workspace_json"] = str(related_path)
                artifacts["related_papers_debug_json"] = str(debug_path)
                self._stage(
                    run,
                    "related",
                    "completed",
                    outputs={"workspace_json": str(related_path), "debug_json": str(debug_path)},
                )

            if workspace is None:
                raise RuntimeError("pipeline did not produce a workspace")
            provenance = workspace.setdefault("provenance", {})
            if isinstance(provenance, dict):
                provenance["pipeline_run_id"] = run_id
                provenance["pipeline_run"] = {
                    "run_id": run_id,
                    "candidate_json": artifacts.get("candidate_json"),
                    "paper_database_json": artifacts.get("paper_database_json"),
                    "workspace_json": artifacts.get("workspace_json"),
                    "similar_workspace_json": artifacts.get("enriched_workspace_json"),
                    "completed_at": _now(),
                }
                provenance["updated_at"] = _now()
            publish = publish_workspace_version(
                repository_dir=self.repository.base_dir,
                workspace=workspace,
                reason=f"pipeline run {run_id} completed",
                event_type="workspace_pipeline_completed",
                event_payload={"pipeline_run_id": run_id, "stages": run["requested_stages"]},
                expected_parent_version_hash=published_parent_hash,
                pipeline_run_id=run_id,
            )
            artifacts["workspace_version_hash"] = publish["version_hash"]
            run.update(
                {
                    "status": "completed_with_warnings" if warnings else "completed",
                    "current_stage": None,
                    "artifacts": artifacts,
                    "warnings": warnings,
                    "completed_at": _now(),
                    "updated_at": _now(),
                }
            )
            self.repository.save_pipeline_run(run)
            for warning in warnings:
                logger.warning(
                    "workspace pipeline warning run_id=%s workspace_id=%s: %s",
                    run_id,
                    run["workspace_id"],
                    warning,
                )
        except Exception as error:
            if self._run_was_cancelled(run_id):
                logger.info("workspace pipeline cancelled run_id=%s", run_id)
                return
            logger.exception(
                "workspace pipeline failed run_id=%s workspace_id=%s",
                run_id,
                run["workspace_id"],
            )
            failed_stage = run.get("current_stage")
            if isinstance(failed_stage, str) and failed_stage in PIPELINE_STAGES:
                self._stage(run, failed_stage, "failed", error=str(error))
            run.update(
                {
                    "status": "failed",
                    "error": str(error),
                    "current_stage": None,
                    "artifacts": artifacts,
                    "updated_at": _now(),
                    "completed_at": _now(),
                }
            )
            self.repository.save_pipeline_run(run)

    def _stage(
        self,
        run: dict[str, Any],
        name: str,
        status: str,
        *,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if self._run_was_cancelled(str(run["run_id"])):
            raise RuntimeError("pipeline run was cancelled")
        previous = dict(run["stages"].get(name) or {})
        run["stages"][name] = {
            **previous,
            "status": status,
            "inputs": inputs if inputs is not None else previous.get("inputs", {}),
            "outputs": outputs if outputs is not None else previous.get("outputs", {}),
            "error": error,
            "updated_at": _now(),
        }
        run["current_stage"] = name if status == "running" else None
        run["updated_at"] = _now()
        self.repository.save_pipeline_run(run)
        logger.info(
            "workspace pipeline stage run_id=%s workspace_id=%s stage=%s status=%s",
            run["run_id"],
            run["workspace_id"],
            name,
            status,
        )

    def _required_artifact(self, artifacts: dict[str, Any], name: str) -> Path:
        value = artifacts.get(name)
        if not value:
            raise InvalidPayloadError(f"pipeline source is missing {name}.")
        path = self._safe_artifact_path(str(value))
        if not path.exists():
            raise InvalidPayloadError(f"pipeline source artifact does not exist: {name}.")
        return path

    def _execution_artifacts(
        self,
        run: dict[str, Any],
        source_run: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Return only the completed inputs a rerun is allowed to reuse.

        New stages write to a run-owned directory. This preserves prior run
        artifacts for inspection and prevents a failed rerun from replacing a
        previously completed run's workspace files.
        """

        if run["requested_stages"][0] == "candidates":
            return {}
        artifacts = {"run_dir": str(self._pipeline_artifact_dir(run["run_id"]))}
        source_artifacts = (source_run or {}).get("artifacts") or {}
        for name in ("candidate_json", "paper_database_json"):
            value = source_artifacts.get(name)
            if value:
                artifacts[name] = value
        return artifacts

    def _pipeline_artifact_dir(self, run_id: str) -> Path:
        directory = (
            self.repo_root
            / "experiments"
            / "output"
            / "workspace_pipeline_runs"
            / run_id
        ).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _safe_artifact_path(self, value: str) -> Path:
        path = Path(value).resolve()
        if not path.is_relative_to(self.repo_root):
            raise InvalidPayloadError("pipeline artifact path is outside the repository.")
        return path

    def _available_workspace_id(self, base_id: str) -> str:
        candidate = base_id
        suffix = 2
        # Failed hydration can leave cached paper content without ever
        # publishing a workspace. Only a current workspace reserves its ID.
        while (self.repository.base_dir / candidate / "current.json").exists():
            candidate = f"{base_id}-{suffix}"
            suffix += 1
        return candidate

    def _run_was_cancelled(self, run_id: str) -> bool:
        try:
            return self.repository.get_pipeline_run(run_id).get("status") == "cancelled"
        except FileNotFoundError:
            return True

    def _semantic_scholar_client(self) -> SemanticScholarClient:
        import os

        return SemanticScholarClient(
            cache_dir=self.repository.base_dir.parent / "cache" / "semantic_scholar",
            api_key=os.environ.get("S2_API_KEY") or os.environ.get("SEMANTIC_SCHOLAR_API_KEY"),
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dispatch_local_thread(callback: Callable[[], None], name: str) -> None:
    """Local runner boundary; deployments can inject a durable queue dispatcher."""

    Thread(target=callback, name=name, daemon=True).start()
