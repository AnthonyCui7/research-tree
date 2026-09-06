from __future__ import annotations

import copy
import logging
import os
import socket
from concurrent.futures import Future
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from typing import Any, Callable
from uuid import uuid4

from research_tree.artifacts import write_json_file
from research_tree.retrieval.candidate_preparation import (
    PipelineConfig,
    run_workspace_candidate_preparation_pipeline,
)
from research_tree.paths import data_root, pipeline_runs_dir, semantic_scholar_cache_dir
from research_tree.retrieval.semantic_scholar import SemanticScholarClient, s2_api_key
from research_tree.principal import LOCAL_PRINCIPAL, Principal, bind_principal, current_owner_id
from research_tree.services.errors import InvalidPayloadError, WorkspaceNotFoundError
from research_tree.services.tenancy import require_owned, require_run_owned
from research_tree.services.topics import TopicReviewService, topic_slug
from research_tree.llm import DEFAULT_MODEL
from research_tree.workspace.construction import construct_workspace_from_candidates
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.publishing import publish_workspace_version
from research_tree.workspace.repository import WorkspaceRepository
from research_tree.workspace.enrichment import (
    hydrate_workspace_papers,
    prefetch_paper_content,
)
from research_tree.workspace.schemas import (
    candidate_papers_from_artifact,
    paper_database_from_artifact,
)
from research_tree.workspace.serialization import load_json_artifact
from research_tree.workspace.similar_papers import (
    DEFAULT_SIMILAR_CITATION_AGE_EXPONENT,
    DEFAULT_SIMILAR_CITATION_SCORE_FLOOR,
    DEFAULT_SIMILAR_PAPERS_K,
    build_similar_papers,
    precompute_similar_paper_rankings,
)
from research_tree.workspace.tldr import OpenAIResponsesTldrGenerator, TldrGenerator


# The local FastAPI server configures this logger at INFO without requiring a
# second logging configuration for the application.
logger = logging.getLogger("uvicorn.error")


PIPELINE_STAGES = ("candidates", "construct", "hydrate", "related")


class WorkspacePipelineService:
    def __init__(
        self,
        repository: WorkspaceRepository,
        *,
        repo_root: Path,
        dispatch: Callable[[Callable[[], None], str], None] | None = None,
    ) -> None:
        self.repository = repository
        self.repo_root = repo_root.resolve()
        self.dispatch = dispatch or _dispatch_local_thread

    def start_approved_new_workspace(
        self,
        *,
        topic: str,
        topic_review_token: str,
        instructions: str | None = None,
    ) -> dict[str, Any]:
        normalized_topic = TopicReviewService(self.repository).consume_approved_topic(
            token=topic_review_token,
            topic=topic,
        )
        if normalized_topic is None:
            raise InvalidPayloadError(
                "Review the research focus again before building a workspace."
            )
        workspace_id = self._available_workspace_id(topic_slug(normalized_topic))
        return self._start(
            workspace_id=workspace_id,
            topic=normalized_topic,
            start_stage="candidates",
            source_run=None,
            source_version_hash=None,
            reserve_topic=True,
            instructions=instructions,
        )

    def rerun(
        self,
        workspace_id: str,
        *,
        start_stage: str,
        expected_version_hash: str | None = None,
    ) -> dict[str, Any]:
        if start_stage not in PIPELINE_STAGES:
            raise InvalidPayloadError(f"start_stage must be one of {PIPELINE_STAGES}.")
        require_owned(self.repository, workspace_id)
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
            start_stage=start_stage,
            source_run=source_run,
            source_version_hash=current_hash,
            # Rebuilding a workspace should rebuild the one that was asked for,
            # so the steer from the run that created it comes along.
            instructions=next(
                (
                    str(prior["instructions"])
                    for prior in prior_runs
                    if prior.get("instructions")
                ),
                None,
            ),
        )

    def get_run(self, run_id: str) -> dict[str, Any]:
        try:
            run = self.repository.get_pipeline_run(run_id)
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError(f"pipeline run does not exist: {run_id}") from error
        require_run_owned(run)
        return run

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run.get("status") in {"queued", "running"}:
            self.repository.cancel_pipeline_runs(str(run.get("workspace_id") or ""))
            return self.get_run(run_id)
        return run

    def list_runs(self, workspace_id: str) -> dict[str, Any]:
        require_owned(self.repository, workspace_id)
        return {
            "workspace_id": workspace_id,
            "pipeline_runs": self.repository.list_pipeline_runs(workspace_id),
        }

    def _start(
        self,
        *,
        workspace_id: str,
        topic: str,
        start_stage: str,
        source_run: dict[str, Any] | None,
        source_version_hash: str | None,
        reserve_topic: bool = False,
        instructions: str | None = None,
    ) -> dict[str, Any]:
        run_id = f"pipeline_{uuid4().hex}"
        start_index = PIPELINE_STAGES.index(start_stage)
        run = {
            "schema_version": "research_tree.pipeline_run.v2",
            "run_id": run_id,
            "workspace_id": workspace_id,
            "topic": topic,
            # The reader's optional steer for the construction prompt. It lives
            # on the run so a rerun of the same workspace builds it the same way.
            "instructions": _clean_instructions(instructions),
            # Construction is locked to one model: the prompt and its structured
            # output schema are tuned against it, and a workspace's quality
            # should not vary with whatever the caller asked for.
            "model": DEFAULT_MODEL,
            "status": "queued",
            "current_stage": None,
            "requested_stages": list(PIPELINE_STAGES[start_index:]),
            "source_run_id": source_run.get("run_id") if source_run else None,
            "source_workspace_version_hash": source_version_hash,
            # The account this run builds for; the workspace it publishes is
            # owned by the same account. None for the local user.
            "owner_id": current_owner_id(),
            "runner_pid": os.getpid(),
            # PID liveness only means anything on the machine that owns the
            # PID, so the reclaimer checks the host before trusting the probe.
            "runner_host": socket.gethostname(),
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
        # The thread (or worker) that runs this has no request context, so the
        # run's owner is bound here: everything downstream that records an
        # actor or resolves credentials sees the account that asked for it.
        with bind_principal(principal_for_owner_id(run.get("owner_id")), feature="pipeline"):
            self._execute_bound(run_id, run, source_run)

    def _execute_bound(
        self, run_id: str, run: dict[str, Any], source_run: dict[str, Any] | None
    ) -> None:
        logger.info(
            "workspace pipeline started run_id=%s workspace_id=%s stages=%s",
            run_id,
            run["workspace_id"],
            ",".join(run["requested_stages"]),
        )
        if run.get("status") != "queued":
            # Redelivered or already handled: the record says what happened.
            logger.info(
                "workspace pipeline not started run_id=%s status=%s", run_id, run.get("status")
            )
            return
        run["status"] = "running"
        run["updated_at"] = _now()
        self.repository.save_pipeline_run(run)
        heartbeat = _Heartbeat.start(self.repository, run_id)
        try:
            self._run_stages(run_id, run, source_run)
        finally:
            heartbeat.stop()

    def _run_stages(
        self, run_id: str, run: dict[str, Any], source_run: dict[str, Any] | None
    ) -> None:
        artifacts = self._execution_artifacts(run, source_run)
        warnings: list[str] = []
        prefetch: _ConstructPrefetch | None = None
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
                prefetch = _ConstructPrefetch.start(
                    candidate_json_path=Path(candidate_json),
                    paper_database_json_path=artifacts.get("paper_database_json"),
                    prefetch_content="hydrate" in run["requested_stages"],
                    precompute_rankings="related" in run["requested_stages"],
                )
                result = construct_workspace_from_candidates(
                    candidate_json_path=candidate_json,
                    model=run["model"],
                    output_dir=run_dir,
                    workspace_id_override=run["workspace_id"],
                    instructions=run.get("instructions"),
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
                    tldr_generator=self._tldr_generator(),
                    prefetched_content=prefetch.paper_content() if prefetch else None,
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
                    repository=self.repository,
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
                    owner_id=run.get("owner_id"),
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
                try:
                    workspace, debug = build_similar_papers(
                        workspace=workspace,
                        paper_database=paper_database,
                        k=DEFAULT_SIMILAR_PAPERS_K,
                        citation_age_exponent=DEFAULT_SIMILAR_CITATION_AGE_EXPONENT,
                        citation_score_floor=DEFAULT_SIMILAR_CITATION_SCORE_FLOOR,
                        precomputed_rankings=prefetch.similar_rankings() if prefetch else None,
                    )
                except (ImportError, RuntimeError, OSError) as error:
                    # Similar papers need local embedding models from the optional
                    # `pipeline` extra. The workspace is already usable without
                    # them, so a missing model degrades the run, not the product.
                    warnings.append(f"Similar-paper recommendations were skipped: {error}")
                    self._stage(run, "related", "completed_with_warnings")
                else:
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
                        outputs={
                            "workspace_json": str(related_path),
                            "debug_json": str(debug_path),
                        },
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
                repository=self.repository,
                workspace=workspace,
                reason=f"pipeline run {run_id} completed",
                event_type="workspace_pipeline_completed",
                event_payload={"pipeline_run_id": run_id, "stages": run["requested_stages"]},
                expected_parent_version_hash=published_parent_hash,
                pipeline_run_id=run_id,
                owner_id=run.get("owner_id"),
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
        directory = (pipeline_runs_dir() / run_id).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _safe_artifact_path(self, value: str) -> Path:
        """Confine artifact paths to the data root.

        The candidates stage reports where it wrote, and that path is later
        read back and passed to file APIs; nothing outside the directory the
        product owns is a legitimate answer.
        """

        path = Path(value).resolve()
        if not path.is_relative_to(data_root().resolve()):
            raise InvalidPayloadError("pipeline artifact path is outside the data directory.")
        return path

    def _available_workspace_id(self, base_id: str) -> str:
        candidate = base_id
        suffix = 2
        while self.repository.workspace_id_is_taken(candidate):
            candidate = f"{base_id}-{suffix}"
            suffix += 1
        return candidate

    def _run_was_cancelled(self, run_id: str) -> bool:
        try:
            return self.repository.get_pipeline_run(run_id).get("status") == "cancelled"
        except FileNotFoundError:
            return True

    def _semantic_scholar_client(self) -> SemanticScholarClient:
        # Shares the candidates stage's cache directory, so a paper already
        # fetched during retrieval costs no request during hydration.
        return SemanticScholarClient(
            cache_dir=semantic_scholar_cache_dir(),
            api_key=s2_api_key(),
        )

    def _tldr_generator(self) -> TldrGenerator | None:
        try:
            return OpenAIResponsesTldrGenerator()
        except RuntimeError as error:
            logger.warning("generated TLDR fallback unavailable: %s", error)
            return None


class _ConstructPrefetch:
    """Hydrate downloads and related-paper rankings, run during the construct call.

    Both stages' expensive work depends only on the candidate hand-off — the
    construction model can only select workspace papers from it — so it runs in
    the background while the multi-minute construction LLM call is in flight.
    Accessors block until their job finishes and return None on any failure;
    the stages then simply do the work themselves.
    """

    def __init__(
        self,
        content_future: Future | None,
        rankings_future: Future | None,
    ) -> None:
        self._content_future = content_future
        self._rankings_future = rankings_future

    @classmethod
    def start(
        cls,
        *,
        candidate_json_path: Path,
        paper_database_json_path: str | None,
        prefetch_content: bool,
        precompute_rankings: bool,
    ) -> "_ConstructPrefetch | None":
        if not (prefetch_content or precompute_rankings):
            return None
        try:
            query_papers = list(
                candidate_papers_from_artifact(
                    load_json_artifact(candidate_json_path)
                ).values()
            )
        except (OSError, ValueError) as error:
            logger.warning("construct prefetch skipped: %s", error)
            return None
        if not query_papers:
            return None
        # Daemon threads, not a ThreadPoolExecutor: executor workers are
        # non-daemon and joined at interpreter exit, so an in-flight prefetch
        # kept "restarted" backends alive as zombies — each with its own
        # in-memory rate limiter, together overrunning the S2 key's budget
        # (measured Aug 2026: three such processes, sustained 429s).
        content_future = (
            _run_in_daemon_thread(
                lambda: prefetch_paper_content(query_papers),
                "construct-prefetch-content",
            )
            if prefetch_content
            else None
        )
        rankings_future = None
        if precompute_rankings and paper_database_json_path:
            database_path = Path(paper_database_json_path)

            def compute() -> dict[str, Any]:
                paper_database = paper_database_from_artifact(
                    load_json_artifact(database_path)
                )
                return precompute_similar_paper_rankings(
                    query_papers=query_papers,
                    paper_database=paper_database,
                    citation_age_exponent=DEFAULT_SIMILAR_CITATION_AGE_EXPONENT,
                    citation_score_floor=DEFAULT_SIMILAR_CITATION_SCORE_FLOOR,
                )

            rankings_future = _run_in_daemon_thread(compute, "construct-prefetch-rankings")
        return cls(content_future, rankings_future)

    def paper_content(self) -> dict[str, Any] | None:
        return self._resolve(self._content_future, "paper content prefetch")

    def similar_rankings(self) -> dict[str, Any] | None:
        return self._resolve(self._rankings_future, "similar-paper precompute")

    @staticmethod
    def _resolve(future: Future | None, label: str) -> dict[str, Any] | None:
        if future is None:
            return None
        try:
            return future.result()
        except Exception as error:
            # The consuming stage redoes the work itself, so a prefetch failure
            # only costs the time it would have saved.
            logger.warning("construct %s failed: %s", label, error)
            return None


class _Heartbeat:
    """Touches the run record every 30 s so a crashed runner is noticed.

    The Postgres repository fails a running run whose heartbeat is older than
    three minutes; the JSON repository ignores the touch and probes the PID.
    """

    INTERVAL_SECONDS = 30.0

    def __init__(self, repository: WorkspaceRepository, run_id: str) -> None:
        self._repository = repository
        self._run_id = run_id
        self._stop = Event()
        self._thread = Thread(target=self._loop, name=f"heartbeat-{run_id}", daemon=True)

    @classmethod
    def start(cls, repository: WorkspaceRepository, run_id: str) -> "_Heartbeat":
        heartbeat = cls(repository, run_id)
        heartbeat._thread.start()
        return heartbeat

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.INTERVAL_SECONDS):
            try:
                self._repository.touch_pipeline_run(self._run_id)
            except Exception as error:  # noqa: BLE001 - a missed beat is not fatal
                logger.warning("pipeline heartbeat failed run_id=%s: %s", self._run_id, error)


def principal_for_owner_id(owner_id: Any) -> Principal:
    if not owner_id:
        return LOCAL_PRINCIPAL
    from research_tree.auth.accounts import principal_for_user_id

    principal = principal_for_user_id(str(owner_id))
    return principal if principal is not None else LOCAL_PRINCIPAL


MAX_INSTRUCTIONS_CHARS = 2_000


def _clean_instructions(instructions: str | None) -> str | None:
    text = " ".join((instructions or "").split()).strip()
    return text[:MAX_INSTRUCTIONS_CHARS] or None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dispatch_local_thread(callback: Callable[[], None], name: str) -> None:
    """Local runner boundary; deployments can inject a durable queue dispatcher."""

    Thread(target=callback, name=name, daemon=True).start()


def _run_in_daemon_thread(callback: Callable[[], Any], name: str) -> Future:
    future: Future = Future()

    def run() -> None:
        try:
            future.set_result(callback())
        except BaseException as error:  # noqa: BLE001
            future.set_exception(error)

    Thread(target=run, name=name, daemon=True).start()
    return future
