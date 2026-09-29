"""Pure ASGI middlewares: browser hardening, same-origin requests, a body cap.

Written against the ASGI interface rather than Starlette's BaseHTTPMiddleware
so streaming responses (the two SSE endpoints) pass through untouched.
"""

from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from starlette.exceptions import HTTPException as StarletteHTTPException

from research_tree.auth.settings import PUBLIC_ORIGIN_ENV

ASGIApp = Callable[[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[None]]

MAX_BODY_BYTES = 1_000_000
TOO_LARGE_MESSAGE = "That request is too large."
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# `default-src 'self'` covers scripts, styles and connections (including the
# EventSource streams and the Google sign-in redirect, which is a navigation,
# not a fetch). Inter comes from Google Fonts; pdf.js runs its worker from a
# blob URL; Google avatars are the only third-party images.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: blob: https://lh3.googleusercontent.com",
        "connect-src 'self'",
        "worker-src 'self' blob:",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode("ascii")),
)
HSTS_HEADER = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


class CanonicalHostMiddleware:
    """Send `www.` (and any other alias) to the one origin the site lives on.

    The session cookie and the Google callback belong to a single host, so a
    reader who typed the alias is moved there before anything else happens.
    Only navigations are redirected; API calls to the wrong host simply fail
    the same-origin check.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["method"] in {"GET", "HEAD"}:
            origin = (os.environ.get(PUBLIC_ORIGIN_ENV) or "").strip().rstrip("/")
            canonical_host = origin.split("://", 1)[1] if "://" in origin else ""
            host = (_header(scope, b"x-forwarded-host") or _header(scope, b"host") or "").split(",")[0].strip()
            if (
                canonical_host
                and host
                and host != canonical_host
                and host.endswith("." + canonical_host)
            ):
                # The raw path, still percent-encoded, is what belongs in a
                # header. `scope["path"]` is decoded, so a link with an accent
                # in it could not be encoded as latin-1 (500) and one carrying
                # %0d%0a became a header value the HTTP layer refused to send,
                # dropping the connection with no response at all.
                raw_path = scope.get("raw_path") or (scope.get("path") or "/").encode("utf-8")
                query = scope.get("query_string") or b""
                target = origin.encode("latin-1") + quote(raw_path, safe="/%:@!$&'()*+,;=~-._").encode(
                    "latin-1"
                )
                if query:
                    target += b"?" + query
                await send(
                    {
                        "type": "http.response.start",
                        "status": 308,
                        "headers": [(b"location", target), (b"content-length", b"0")],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {name.lower() for name, _ in headers}
                for name, value in SECURITY_HEADERS:
                    if name not in present:
                        headers.append((name, value))
                if _public_origin_is_https() and HSTS_HEADER[0] not in present:
                    headers.append(HSTS_HEADER)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class SameOriginMiddleware:
    """Refuse requests a browser made from another site.

    Cookies travel with cross-site requests; this is the check that makes a
    session cookie safe to rely on. Every write is checked, and so is every
    read outside the few paths another site may legitimately send a reader
    to, so that nothing behind sign-in depends on a route being read-only.
    `Sec-Fetch-Site` says where a request came from when a browser sends it;
    otherwise an `Origin` header has to match the site.
    Requests with neither header (curl, the test client, server-to-server)
    carry no ambient credentials and pass.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and (
            scope["method"] in STATE_CHANGING_METHODS
            or not _open_to_other_sites(scope.get("path") or "/")
        ):
            headers = {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in scope["headers"]}
            if not _same_origin(headers, scope):
                await _json_response(
                    send,
                    403,
                    {
                        "detail": "This request did not come from Research Tree itself.",
                        "error_code": "csrf_origin_rejected",
                    },
                )
                return
        await self.app(scope, receive, send)


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _header(scope, b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            await _json_response(send, 413, {"detail": TOO_LARGE_MESSAGE})
            return

        received = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > self.max_bytes:
                    # Raised inside the app, whose handlers answer it. FastAPI
                    # lets an HTTPException out of its body parsing and turns
                    # anything else raised there into a 400.
                    raise StarletteHTTPException(413, TOO_LARGE_MESSAGE)
            return message

        await self.app(scope, limited_receive, send)


def _open_to_other_sites(path: str) -> bool:
    """Where a link on another site, or Google's sign-in, may land a reader."""

    return (
        path in {"/", "/favicon.png", "/health"}
        or path.startswith("/assets/")
        or path.endswith("/auth/google/callback")
    )


def _same_origin(headers: dict[str, str], scope: dict[str, Any]) -> bool:
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site in {"same-origin", "none"}:
        return True
    # From elsewhere, or from a client that does not say. A separately hosted
    # frontend is elsewhere and names itself in `Origin`; a navigation or an
    # embed from another site names nothing.
    origin = headers.get("origin")
    if origin:
        return origin.rstrip("/") in _acceptable_origins(headers, scope)
    return fetch_site is None


def _acceptable_origins(headers: dict[str, str], scope: dict[str, Any]) -> set[str]:
    origins: set[str] = set()
    configured = (os.environ.get(PUBLIC_ORIGIN_ENV) or "").strip().rstrip("/")
    if configured:
        origins.add(configured)
    for item in (os.environ.get("RESEARCH_TREE_ALLOWED_ORIGINS") or "").split(","):
        if item.strip():
            origins.add(item.strip().rstrip("/"))
    host = headers.get("x-forwarded-host") or headers.get("host")
    if host:
        scheme = headers.get("x-forwarded-proto") or scope.get("scheme") or "http"
        origins.add(f"{scheme}://{host}")
        # The dev server proxies /api to this process from its own port.
        if not configured:
            origins.add("http://localhost:5173")
            origins.add("http://127.0.0.1:5173")
    return origins


def _public_origin_is_https() -> bool:
    return (os.environ.get(PUBLIC_ORIGIN_ENV) or "").strip().startswith("https://")


def _header(scope: dict[str, Any], name: bytes) -> str | None:
    for key, value in scope["headers"]:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


async def _json_response(send: Any, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
