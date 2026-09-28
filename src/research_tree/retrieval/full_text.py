from __future__ import annotations

import hashlib
import io
import ipaddress
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


MAX_PDF_BYTES = 40 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_EXTRACTED_CHARACTERS = 2_000_000

# pypdf reports each font it cannot fully decode as a warning that carries the
# whole font dictionary, hundreds per build. The text it could read comes back
# either way, so only its errors reach the logs.
logging.getLogger("pypdf").setLevel(logging.ERROR)


@dataclass(frozen=True)
class PaperContentResult:
    content: dict[str, Any]
    warning: str | None = None


def retrieve_open_access_paper_content(
    *,
    paper_id: str,
    title: str,
    source_url: str | None,
    timeout_seconds: float = 30.0,
) -> PaperContentResult:
    """Download and extract an openly available PDF with strict resource limits."""

    if not source_url:
        return _unavailable(paper_id, title, "No open-access PDF URL was supplied.")
    try:
        pdf_bytes = download_open_access_pdf(source_url, timeout_seconds=timeout_seconds)
        extracted = _extract_pdf(pdf_bytes)
    except Exception as error:  # noqa: BLE001 - see below
        # Everything a publisher can send is caught, not only the errors this
        # code raises: pypdf's own exception types, a truncated chunked body
        # (`http.client.IncompleteRead`), a zlib error inside a content
        # stream. One of those failed the whole hydrate stage, and a rerun met
        # the same PDF and failed the same way, so the workspace could never
        # be built. A paper whose text cannot be read is a paper without text.
        return _unavailable(paper_id, title, f"{type(error).__name__}: {error}", source_url=source_url)

    content = {
        "schema_version": "research_tree.paper_content.v1",
        "paper_id": paper_id,
        "title": title,
        "status": extracted["status"],
        "source_type": "open_access_pdf",
        "source_url": source_url,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "byte_count": len(pdf_bytes),
        "page_count": extracted["page_count"],
        "figure_count": extracted["figure_count"],
        "full_text": extracted["full_text"],
        "truncated": extracted["truncated"],
    }
    warning = None
    if content["truncated"]:
        warning = f"Full text for {title} exceeded safe extraction limits and is marked truncated."
    return PaperContentResult(content=content, warning=warning)


def upgraded_to_https(url: str) -> str:
    """The same link over HTTPS.

    Semantic Scholar still hands out plain `http://arxiv.org/pdf/...` links,
    and links stored before HTTPS was required are the same. Those hosts serve
    both, and only HTTPS is ever fetched, so the link is upgraded rather than
    the paper refused.
    """

    return f"https://{url[len('http://'):]}" if url.startswith("http://") else url


def download_open_access_pdf(url: str, *, timeout_seconds: float = 30.0) -> bytes:
    """Fetch a paper's PDF, refusing anything that is not a public HTTPS PDF."""

    _validate_public_https_url(url)
    return _download_pdf(url, timeout_seconds=timeout_seconds)


def _download_pdf(url: str, *, timeout_seconds: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "research-tree/0.1", "Accept": "application/pdf"},
    )
    opener = urllib.request.build_opener(_PublicHttpsRedirectHandler())
    with opener.open(request, timeout=max(timeout_seconds, 1.0)) as response:
        final_url = response.geturl()
        _validate_public_https_url(final_url)
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_PDF_BYTES:
            raise ValueError("Open-access PDF exceeds the 40 MB safety limit.")
        payload = response.read(MAX_PDF_BYTES + 1)
        content_type = str(response.headers.get("Content-Type") or "").casefold()
    if len(payload) > MAX_PDF_BYTES:
        raise ValueError("Open-access PDF exceeds the 40 MB safety limit.")
    if not payload.startswith(b"%PDF") and "pdf" not in content_type:
        raise ValueError("Open-access URL did not return a PDF.")
    return payload


class _PublicHttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validate_public_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _extract_pdf(pdf_bytes: bytes) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError("pypdf is required to extract open-access paper text.") from error

    reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
    if reader.is_encrypted:
        try:
            if reader.decrypt("") == 0:
                raise ValueError("Open-access PDF is encrypted.")
        except Exception as error:
            raise ValueError("Open-access PDF is encrypted.") from error

    page_count = len(reader.pages)
    page_limit = min(page_count, MAX_PDF_PAGES)
    text_parts: list[str] = []
    character_count = 0
    figure_count = 0
    truncated = page_count > page_limit
    for page in reader.pages[:page_limit]:
        try:
            page_text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001 - one unreadable page, not an unreadable paper
            page_text = ""
        if page_text:
            remaining = MAX_EXTRACTED_CHARACTERS - character_count
            if remaining <= 0:
                truncated = True
                break
            text_parts.append(page_text[:remaining])
            character_count += min(len(page_text), remaining)
            if len(page_text) > remaining:
                truncated = True
                break
        try:
            figure_count += len(page.images)
        except Exception:
            pass
    full_text = "\n\n".join(text_parts).strip()
    if not full_text:
        raise ValueError("PDF did not contain extractable text.")
    return {
        "status": "available_truncated" if truncated else "available",
        "page_count": page_count,
        "figure_count": figure_count,
        "full_text": full_text,
        "truncated": truncated,
    }


def _validate_public_https_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Paper PDF URL must be a public HTTPS URL.")
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Paper PDF URL must not target a local host.")
    try:
        addresses = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ValueError("Paper PDF host could not be resolved.") from error
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Paper PDF URL resolved to a non-public network address.")


def _unavailable(
    paper_id: str,
    title: str,
    reason: str,
    *,
    source_url: str | None = None,
) -> PaperContentResult:
    return PaperContentResult(
        content={
            "schema_version": "research_tree.paper_content.v1",
            "paper_id": paper_id,
            "title": title,
            "status": "unavailable",
            "source_type": "open_access_pdf" if source_url else None,
            "source_url": source_url,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "full_text": "",
            "truncated": False,
            "error": reason,
        },
        # The reason stays on the record; the run's warnings are read by the
        # person who asked for the build, who can do nothing with it.
        warning=f"Full text unavailable for {title}.",
    )
