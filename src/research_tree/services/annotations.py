"""Serving a paper's PDF and the annotations drawn over it."""

from __future__ import annotations

import hashlib
import logging
import re
import urllib.error
from datetime import UTC, datetime
from typing import Any, Callable

from research_tree.annotation import (
    ANNOTATION_SCHEMA_VERSION,
    PaperAnnotation,
    generate_paper_annotations,
)
from research_tree.annotation.config import annotation_model, retrieval_mode
from research_tree.retrieval.full_text import download_open_access_pdf
from research_tree.services.errors import (
    InvalidPayloadError,
    InvalidResourceIdError,
    PaperUnavailableError,
    WorkspaceNotFoundError,
    WorkspaceServiceError,
)
from research_tree.services.tenancy import require_owned
from research_tree.services.validation import validate_resource_id
from research_tree.workspace.repository import WorkspaceRepository


logger = logging.getLogger("uvicorn.error")

MAX_FILENAME_STEM = 80
UNSAFE_FILENAME_CHARACTERS = re.compile(r"[^A-Za-z0-9]+")


class PaperAnnotationService:
    """Annotations are derived from a paper's PDF and cached beside its text.

    Generating them costs a model call per passage, so the result is stored per
    paper and reused until the PDF behind it changes. The callables are
    injectable so tests can exercise the caching and error paths without
    reaching the network.
    """

    def __init__(
        self,
        repository: WorkspaceRepository,
        *,
        download_pdf: Callable[[str], bytes] | None = None,
        generate_annotations: Callable[..., list[PaperAnnotation]] | None = None,
    ) -> None:
        self.repository = repository
        self.download_pdf = download_pdf or download_open_access_pdf
        self.generate_annotations = generate_annotations or generate_paper_annotations

    def get_paper_pdf(self, workspace_id: str, paper_id: str) -> tuple[str, bytes]:
        """The paper's PDF, fetched through this server.

        The reader cannot fetch a publisher's PDF itself — cross-origin reads
        are refused — so the bytes come back through the same origin as the
        annotations, and only from a URL the workspace already resolved.
        """

        card = self._paper_card(workspace_id, paper_id)
        return _pdf_filename(card), self._download(card)

    def get_paper_annotations(
        self,
        workspace_id: str,
        paper_id: str,
        *,
        mode: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """The paper's annotations, generated when the cache cannot serve them.

        `mode` selects the retrieval depth (`fast` or `dense`); a cached result
        built with a different mode is regenerated rather than passed off as
        the requested one. `refresh` regenerates unconditionally.
        """

        if mode is not None and mode not in {"fast", "dense"}:
            raise InvalidPayloadError("mode must be 'fast' or 'dense'.")
        requested_mode = mode or retrieval_mode()
        safe_workspace_id, safe_paper_id, card = self._locate_paper(workspace_id, paper_id)
        pdf_bytes = self._download(card)
        pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

        cached = self._cached_annotations(safe_workspace_id, safe_paper_id)
        if (
            not refresh
            and cached is not None
            and cached.get("pdf_sha256") == pdf_sha256
            and (mode is None or cached.get("retrieval_mode") == requested_mode)
        ):
            return _response(safe_workspace_id, safe_paper_id, cached)

        title = str(card.get("title") or safe_paper_id)
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
            raise PaperUnavailableError(f"could not annotate paper: {safe_paper_id}") from error

        payload = {
            "schema_version": ANNOTATION_SCHEMA_VERSION,
            "paper_id": safe_paper_id,
            "title": title,
            "pdf_sha256": pdf_sha256,
            "model": annotation_model(),
            "retrieval_mode": requested_mode,
            "generated_at": datetime.now(UTC).isoformat(),
            "annotations": [annotation.model_dump() for annotation in annotations],
        }
        self.repository.save_paper_annotations(safe_workspace_id, safe_paper_id, payload)
        return _response(safe_workspace_id, safe_paper_id, payload)

    def _locate_paper(
        self, workspace_id: str, paper_id: str
    ) -> tuple[str, str, dict[str, Any]]:
        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        # A paper id can be a DOI or a title, so it is bounded rather than
        # patterned; the stored filename is a hash of it.
        safe_paper_id = paper_id.strip()
        if not safe_paper_id or len(safe_paper_id) > 512:
            raise InvalidResourceIdError("paper_id must be between 1 and 512 characters.")
        require_owned(self.repository, safe_workspace_id)
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


def _pdf_filename(card: dict[str, Any]) -> str:
    stem = UNSAFE_FILENAME_CHARACTERS.sub("-", str(card.get("title") or "paper")).strip("-")
    return f"{(stem[:MAX_FILENAME_STEM].rstrip('-') or 'paper')}.pdf"
