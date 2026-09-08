"""Serving a paper's PDF and the annotations drawn over it.

Annotating a paper for the first time is minutes of model calls. Locally the
request simply waits. In the cloud the ingress drops idle connections long
before that, so a miss becomes a *job*: the request answers 202 with a job id,
the worker generates and stores the annotations, and the reader polls the job
until it can fetch the cached result. Job state lives in Redis for a day;
the durable result is the annotations artifact itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.error
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import uuid4

from research_tree.annotation import (
    ANNOTATION_SCHEMA_VERSION,
    PaperAnnotation,
    generate_paper_annotations,
)
from research_tree.annotation.config import annotation_model, retrieval_mode
from research_tree.artifact_store import ArtifactStore, default_artifact_store
from research_tree.principal import bind_principal
from research_tree.retrieval.full_text import download_open_access_pdf
from research_tree.services.errors import (
    InvalidPayloadError,
    InvalidResourceIdError,
    PaperUnavailableError,
    WorkspaceNotFoundError,
    WorkspaceServiceError,
    public_service_error_message,
)
from research_tree.services.validation import validate_resource_id
from research_tree.workspace.repository import WorkspaceRepository, _paper_content_key


logger = logging.getLogger("uvicorn.error")

MAX_FILENAME_STEM = 80
UNSAFE_FILENAME_CHARACTERS = re.compile(r"[^A-Za-z0-9]+")
JOB_KEY_PREFIX = "annotations:job:"
ACTIVE_KEY_PREFIX = "annotations:active:"
JOB_TTL_SECONDS = 24 * 60 * 60
ACTIVE_TTL_SECONDS = 60 * 60
JOB_STATUSES = ("queued", "running", "completed", "failed")


class PaperAnnotationService:
    """Annotations are derived from a paper's PDF and cached beside its text.

    Generating them costs a model call per passage, so the result is stored per
    paper and reused until the PDF behind it changes. The callables are
    injectable so tests can exercise the caching and error paths without
    reaching the network; `enqueue` hands a job id to the worker and defaults
    to the Celery task when Redis is configured.
    """

    def __init__(
        self,
        repository: WorkspaceRepository,
        *,
        download_pdf: Callable[[str], bytes] | None = None,
        generate_annotations: Callable[..., list[PaperAnnotation]] | None = None,
        artifacts: ArtifactStore | None = None,
        redis: Any = None,
        enqueue: Callable[[str], None] | None = None,
    ) -> None:
        self.repository = repository
        self.download_pdf = download_pdf or download_open_access_pdf
        self.generate_annotations = generate_annotations or generate_paper_annotations
        self._artifacts = artifacts
        self._redis = redis
        self._redis_resolved = redis is not None
        self._enqueue = enqueue

    # ---- reads ----------------------------------------------------------

    def get_paper_pdf(self, workspace_id: str, paper_id: str) -> tuple[str, bytes]:
        """The paper's PDF, fetched through this server.

        The reader cannot fetch a publisher's PDF itself — cross-origin reads
        are refused — so the bytes come back through the same origin as the
        annotations, and only from a URL the workspace already resolved.
        """

        card = self._paper_card(workspace_id, paper_id)
        return _pdf_filename(card), self._pdf_bytes(card)

    def get_paper_annotations(
        self,
        workspace_id: str,
        paper_id: str,
        *,
        mode: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """The paper's annotations, or the job that is producing them.

        `mode` selects the retrieval depth (`fast` or `dense`); a cached result
        built with a different mode is regenerated rather than passed off as
        the requested one. `refresh` regenerates unconditionally. A miss is
        generated inline without Redis and returns a job (a dict carrying
        `job_id`) with it.

        Annotations belong to one PDF, named by its hash. A paper the build
        hydrated already carries that hash on its card, so a cached result for
        it is served without fetching anything; otherwise the PDF is fetched
        (from the store when it has been seen, else downloaded) and compared.
        """

        if mode is not None and mode not in {"fast", "dense"}:
            raise InvalidPayloadError("mode must be 'fast' or 'dense'.")
        requested_mode = mode or retrieval_mode()
        safe_workspace_id, safe_paper_id, card = self._locate_paper(workspace_id, paper_id)

        cached = None if refresh else self._cached_annotations(safe_workspace_id, safe_paper_id)
        if cached is not None and mode is not None and cached.get("retrieval_mode") != requested_mode:
            cached = None
        known_sha256 = _known_pdf_sha256(card)
        if cached is not None and known_sha256 and cached.get("pdf_sha256") == known_sha256:
            return _response(safe_workspace_id, safe_paper_id, cached)

        pdf_bytes = self._pdf_bytes(card)
        pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        if cached is not None and cached.get("pdf_sha256") == pdf_sha256:
            return _response(safe_workspace_id, safe_paper_id, cached)

        redis = self._redis_client()
        if redis is None:
            return self._generate_and_store(
                safe_workspace_id, safe_paper_id, card, pdf_bytes, pdf_sha256, requested_mode
            )
        return self._start_job(
            redis,
            workspace_id=safe_workspace_id,
            paper_id=safe_paper_id,
            mode=requested_mode,
            pdf_sha256=pdf_sha256,
        )

    def annotation_job(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        """The state of one job, for the account that started it."""

        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        safe_job_id = validate_resource_id(job_id, field_name="job_id")
        redis = self._redis_client()
        job = _load_job(redis, safe_job_id) if redis is not None else None
        if (
            job is None
            or job.get("owner_id") != self.repository.owner_id
            or job.get("workspace_id") != safe_workspace_id
        ):
            raise WorkspaceNotFoundError(f"annotation job does not exist: {safe_job_id}")
        return _job_view(job)

    # ---- the worker side --------------------------------------------------

    def run_annotation_job(self, job: dict[str, Any]) -> None:
        """Generate and store the annotations a job asked for.

        The service is bound to the job's account (`load_annotation_job` says
        whose it is). Idempotent under redelivery: a job that is no longer
        queued is left alone. The account is bound as the principal for the
        duration so credential resolution and metering see the person who
        asked.
        """

        from research_tree.services.pipeline import principal_for_owner_id

        redis = self._redis_client()
        if redis is None:
            raise RuntimeError("annotation jobs need Redis.")
        job_id = str(job["job_id"])
        if job.get("owner_id") != self.repository.owner_id:
            raise ValueError(f"annotation job {job_id} belongs to another account.")
        if job.get("status") != "queued":
            logger.info("annotation job %s not started: status=%s", job_id, job.get("status"))
            return
        _save_job(redis, {**job, "status": "running"})
        # Resolving the job's account is inside the try: an account that has
        # gone away is a reason to fail the job, not to run it as somebody else.
        try:
            with bind_principal(
                principal_for_owner_id(self.repository.owner_id), feature="annotations"
            ):
                _, safe_paper_id, card = self._locate_paper(job["workspace_id"], job["paper_id"])
                pdf_bytes = self.artifacts.get(f"pdf/{job['pdf_sha256']}")
                if pdf_bytes is None:
                    pdf_bytes = self._download(card)
                pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
                self._generate_and_store(
                    job["workspace_id"], safe_paper_id, card, pdf_bytes, pdf_sha256, job["mode"]
                )
        except WorkspaceServiceError as error:
            _save_job(
                redis,
                {
                    **job,
                    "status": "failed",
                    "error_code": error.error_code,
                    "error_status": error.status_code,
                    "detail": public_service_error_message(error),
                },
            )
        except Exception:  # noqa: BLE001 - the reader gets a reason, the log the trace
            logger.exception("annotation job %s failed", job_id)
            _save_job(
                redis,
                {
                    **job,
                    "status": "failed",
                    "error_code": "paper_unavailable",
                    "error_status": 502,
                    "detail": "We could not annotate that paper. Please try again.",
                },
            )
        else:
            _save_job(redis, {**job, "status": "completed"})
        finally:
            try:
                redis.delete(self._active_key(job["workspace_id"], job["paper_id"], job["mode"]))
            except Exception:  # noqa: BLE001
                pass

    # ---- internals --------------------------------------------------------

    @property
    def artifacts(self) -> ArtifactStore:
        if self._artifacts is None:
            self._artifacts = default_artifact_store()
        return self._artifacts

    def _redis_client(self) -> Any:
        if not self._redis_resolved:
            from research_tree.redis_client import get_redis

            self._redis = get_redis()
            self._redis_resolved = True
        return self._redis

    def _enqueue_job(self, job_id: str) -> None:
        if self._enqueue is not None:
            self._enqueue(job_id)
            return
        from research_tree.tasks import generate_annotations

        generate_annotations.delay(job_id)

    def _start_job(
        self,
        redis: Any,
        *,
        workspace_id: str,
        paper_id: str,
        mode: str,
        pdf_sha256: str,
    ) -> dict[str, Any]:
        active_key = self._active_key(workspace_id, paper_id, mode)
        existing_id = redis.get(active_key)
        if existing_id:
            existing = _load_job(redis, existing_id.decode("utf-8"))
            if existing is not None and existing.get("status") in {"queued", "running"}:
                return _job_view(existing)
        now = datetime.now(UTC).isoformat()
        job = {
            "job_id": f"annotation_{uuid4().hex}",
            "owner_id": self.repository.owner_id,
            "workspace_id": workspace_id,
            "paper_id": paper_id,
            "mode": mode,
            "pdf_sha256": pdf_sha256,
            "status": "queued",
            "error_code": None,
            "error_status": None,
            "detail": None,
            "created_at": now,
        }
        if not redis.set(active_key, job["job_id"], nx=True, ex=ACTIVE_TTL_SECONDS):
            existing_id = redis.get(active_key)
            existing = _load_job(redis, existing_id.decode("utf-8")) if existing_id else None
            if existing is not None:
                return _job_view(existing)
        _save_job(redis, job)
        try:
            self._enqueue_job(job["job_id"])
        except Exception as error:  # noqa: BLE001 - the queue is down; say so, leave nothing pending
            logger.exception("annotation job %s could not be queued", job["job_id"])
            redis.delete(active_key)
            redis.delete(f"{JOB_KEY_PREFIX}{job['job_id']}")
            raise WorkspaceServiceError("Annotation could not be queued. Try again.") from error
        return _job_view(job)

    def _generate_and_store(
        self,
        workspace_id: str,
        paper_id: str,
        card: dict[str, Any],
        pdf_bytes: bytes,
        pdf_sha256: str,
        requested_mode: str,
    ) -> dict[str, Any]:
        title = str(card.get("title") or paper_id)
        try:
            annotations = self.generate_annotations(
                pdf_bytes,
                title=title,
                abstract=str(card.get("abstract") or ""),
                mode=requested_mode,
            )
        except WorkspaceServiceError:
            raise
        except Exception as error:
            logger.warning("Annotating %r failed (%s): %s", title, type(error).__name__, error)
            raise PaperUnavailableError(f"could not annotate paper: {paper_id}") from error

        payload = {
            "schema_version": ANNOTATION_SCHEMA_VERSION,
            "paper_id": paper_id,
            "title": title,
            "pdf_sha256": pdf_sha256,
            "model": annotation_model(),
            "retrieval_mode": requested_mode,
            "generated_at": datetime.now(UTC).isoformat(),
            "annotations": [annotation.model_dump() for annotation in annotations],
        }
        self.repository.save_paper_annotations(workspace_id, paper_id, payload)
        return _response(workspace_id, paper_id, payload)

    def _locate_paper(
        self, workspace_id: str, paper_id: str
    ) -> tuple[str, str, dict[str, Any]]:
        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        # A paper id can be a DOI or a title, so it is bounded rather than
        # patterned; the stored filename is a hash of it.
        safe_paper_id = paper_id.strip()
        if not safe_paper_id or len(safe_paper_id) > 512:
            raise InvalidResourceIdError("paper_id must be between 1 and 512 characters.")
        try:
            workspace = self.repository.get_current_workspace(safe_workspace_id)
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError(
                f"workspace does not exist: {safe_workspace_id}"
            ) from error
        except ValueError as error:
            raise WorkspaceServiceError(str(error)) from error

        cards = workspace.get("paper_cards")
        card = cards.get(safe_paper_id) if isinstance(cards, dict) else None
        if not isinstance(card, dict):
            raise WorkspaceNotFoundError(f"no such paper in workspace: {safe_paper_id}")
        return safe_workspace_id, safe_paper_id, card

    def _paper_card(self, workspace_id: str, paper_id: str) -> dict[str, Any]:
        return self._locate_paper(workspace_id, paper_id)[2]

    def _cached_annotations(self, workspace_id: str, paper_id: str) -> dict[str, Any] | None:
        try:
            return self.repository.get_paper_annotations(workspace_id, paper_id)
        except (FileNotFoundError, ValueError):
            return None

    def _active_key(self, workspace_id: str, paper_id: str, mode: str) -> str:
        return (
            f"{ACTIVE_KEY_PREFIX}{self.repository.owner_id}:{workspace_id}:"
            f"{_paper_content_key(paper_id)}:{mode}"
        )

    def _pdf_bytes(self, card: dict[str, Any]) -> bytes:
        """The paper's PDF: from the store when it has been fetched before, else fetched and kept.

        The build records the hash of the PDF it extracted text from, so a paper
        it hydrated names its own file in the store. Everything fetched here
        is kept under its hash, which is what the worker reads a job's PDF from
        and what the next open is served from.
        """

        known_sha256 = _known_pdf_sha256(card)
        if known_sha256:
            stored = self.artifacts.get(f"pdf/{known_sha256}")
            if stored is not None:
                return stored
        pdf_bytes = self._download(card)
        try:
            self.artifacts.put(f"pdf/{hashlib.sha256(pdf_bytes).hexdigest()}", pdf_bytes)
        except Exception as error:  # noqa: BLE001 - the next open downloads it again
            logger.warning("could not store a PDF: %s", error)
        return pdf_bytes

    def _download(self, card: dict[str, Any]) -> bytes:
        url = paper_pdf_url(card)
        if not url:
            raise InvalidPayloadError("This paper has no open-access PDF to annotate.")
        try:
            return self.download_pdf(url)
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
            logger.warning("Fetching %s failed: %s", url, error)
            raise PaperUnavailableError(f"could not fetch the paper PDF: {error}") from error


def paper_pdf_url(card: dict[str, Any]) -> str | None:
    """The PDF enrichment resolved, falling back to arXiv's own PDF path."""

    content = card.get("paper_content")
    resolved = content.get("source_url") if isinstance(content, dict) else None
    if isinstance(resolved, str) and resolved.strip():
        return _https(resolved.strip())

    arxiv_link = card.get("arxiv_link")
    if isinstance(arxiv_link, str) and "arxiv.org/abs/" in arxiv_link:
        return _https(arxiv_link.replace("/abs/", "/pdf/"))
    return None


def _known_pdf_sha256(card: dict[str, Any]) -> str | None:
    """The hash of the PDF the build extracted this paper's text from, if it did."""

    content = card.get("paper_content")
    value = content.get("sha256") if isinstance(content, dict) else None
    return value if isinstance(value, str) and value else None


def _https(url: str) -> str:
    # Stored links predate the requirement that a paper URL be HTTPS; the same
    # hosts serve both, so upgrade rather than reject.
    return f"https://{url[len('http://'):]}" if url.startswith("http://") else url


def _response(workspace_id: str, paper_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    annotations = payload.get("annotations")
    return {
        "workspace_id": workspace_id,
        "paper_id": paper_id,
        "generated_at": payload.get("generated_at"),
        "model": payload.get("model"),
        "retrieval_mode": payload.get("retrieval_mode"),
        "annotations": annotations if isinstance(annotations, list) else [],
    }


def _job_view(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "status": job.get("status"),
        "workspace_id": job.get("workspace_id"),
        "paper_id": job.get("paper_id"),
        "retrieval_mode": job.get("mode"),
        "error_code": job.get("error_code"),
        "error_status": job.get("error_status"),
        "detail": job.get("detail"),
        "created_at": job.get("created_at"),
    }


def load_annotation_job(job_id: str, *, redis: Any = None) -> dict[str, Any] | None:
    """The job as queued, for the worker to bind its owner before running it."""

    if redis is None:
        from research_tree.redis_client import get_redis

        redis = get_redis()
    if redis is None:
        raise RuntimeError("annotation jobs need Redis.")
    return _load_job(redis, job_id)


def _load_job(redis: Any, job_id: str) -> dict[str, Any] | None:
    raw = redis.get(f"{JOB_KEY_PREFIX}{job_id}")
    if not raw:
        return None
    try:
        job = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return job if isinstance(job, dict) and job.get("job_id") == job_id else None


def _save_job(redis: Any, job: dict[str, Any]) -> None:
    redis.set(f"{JOB_KEY_PREFIX}{job['job_id']}", json.dumps(job), ex=JOB_TTL_SECONDS)


def _pdf_filename(card: dict[str, Any]) -> str:
    stem = UNSAFE_FILENAME_CHARACTERS.sub("-", str(card.get("title") or "paper")).strip("-")
    return f"{(stem[:MAX_FILENAME_STEM].rstrip('-') or 'paper')}.pdf"
