from __future__ import annotations

import fcntl
import hashlib
import os
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Lock
from typing import Any


class JsonRequestError(RuntimeError):
    pass


# Statuses worth retrying on the backoff ladder. 429 is Semantic Scholar
# shedding load, and its 5xx are the same condition reported differently — a
# transient server-side failure that the next attempt may not see. Neither is
# a statement about our request, so failing the whole run on the first one
# throws away retries that could have succeeded. Every other 4xx *is* a
# statement about our request and will fail identically on retry, so those
# still raise immediately.
RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})


class RateLimiter:
    """Spaces request starts across everything that shares this limiter.

    Semantic Scholar counts one request per second cumulatively across all of
    its endpoints, so the budget belongs to the API key rather than to any one
    client. With a `lock_file`, the last-request timestamp lives in that file
    under an exclusive flock, so the spacing holds across *processes* too —
    a backend, a CLI, and a not-yet-exited old backend all share one lane.
    (Measured Aug 2026: three backend processes with independent in-memory
    limiters tripled the request rate under one key and kept it in Semantic
    Scholar's penalty box.) Without a `lock_file` it is in-memory only.
    """

    def __init__(self, lock_file: Path | None = None) -> None:
        self._last_request_at = 0.0
        self._lock = Lock()
        self._lock_file = lock_file

    def acquire(self, delay_seconds: float) -> None:
        with self._lock:
            if delay_seconds <= 0:
                self._last_request_at = time.monotonic()
                return
            if self._lock_file is not None:
                self._acquire_across_processes(delay_seconds)
                return
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < delay_seconds:
                time.sleep(delay_seconds - elapsed)
            self._last_request_at = time.monotonic()

    def _acquire_across_processes(self, delay_seconds: float) -> None:
        # Sleeping while holding the flock makes waiting processes queue
        # behind this one, which is exactly the serialization we want.
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_file, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            raw = handle.read().strip()
            try:
                last_request_at = float(raw) if raw else 0.0
            except ValueError:
                last_request_at = 0.0
            remaining = delay_seconds - (time.time() - last_request_at)
            if remaining > 0:
                time.sleep(remaining)
            handle.seek(0)
            handle.truncate()
            handle.write(f"{time.time():.3f}")
            handle.flush()


class CachedJsonClient:
    def __init__(
        self,
        cache_dir: Path,
        request_delay_seconds: float,
        refresh_cache: bool = False,
        headers: dict[str, str] | None = None,
        max_retries: int = 2,
        timeout_seconds: float = 20.0,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.request_delay_seconds = request_delay_seconds
        self.refresh_cache = refresh_cache
        self.headers = headers or {}
        self.max_retries = max(max_retries, 0)
        self.timeout_seconds = max(timeout_seconds, 1.0)
        self.rate_limiter = rate_limiter or RateLimiter()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        full_url = _url_with_params(url, params or {})
        cache_path = self._cache_path("GET", full_url)
        if cache_path.exists() and not self.refresh_cache:
            return json.loads(cache_path.read_text(encoding="utf-8"))

        payload = self._request_with_retries("GET", full_url, None)
        _write_atomic(cache_path, json.dumps(payload, indent=2))
        return payload

    def post_json(self, url: str, body: dict[str, Any]) -> Any:
        cache_path = self._cache_path("POST", url, body)
        if cache_path.exists() and not self.refresh_cache:
            return json.loads(cache_path.read_text(encoding="utf-8"))

        payload = self._request_with_retries("POST", url, body)
        _write_atomic(cache_path, json.dumps(payload, indent=2))
        return payload

    def _request_with_retries(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
    ) -> Any:
        raw = self._request_text_with_retries(method, url, body)
        return json.loads(raw)

    def _request_text_with_retries(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
    ) -> str:
        # A shared limiter already paces normal traffic, so a 429 here is
        # Semantic Scholar shedding load, not overuse on our side. Measured
        # Aug 2026: recovery after a bulk 429 streak takes 30+ seconds, which
        # is why the ladder climbs to 45/90 s. S2 never sends `Retry-After`,
        # but it still wins here if that ever changes.
        backoffs = [5, 10, 45, 90, 90][: self.max_retries]
        last_error: Exception | None = None
        for attempt in range(len(backoffs) + 1):
            self._wait_for_delay()
            try:
                request_body = None
                headers = dict(self.headers)
                if body is not None:
                    request_body = json.dumps(body).encode("utf-8")
                    headers["Content-Type"] = "application/json"
                request = urllib.request.Request(
                    url,
                    data=request_body,
                    headers=headers,
                    method=method,
                )
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout_seconds,
                ) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code not in RETRYABLE_HTTP_STATUS or attempt >= len(backoffs):
                    raise JsonRequestError(_format_http_error(error)) from error
                retry_after = error.headers.get("Retry-After")
                wait_seconds = _parse_retry_after(retry_after) or backoffs[attempt]
                time.sleep(wait_seconds)
            except urllib.error.URLError as error:
                last_error = error
                if attempt >= len(backoffs):
                    raise JsonRequestError(str(error)) from error
                time.sleep(backoffs[attempt])
            except (TimeoutError, socket.timeout) as error:
                last_error = error
                if attempt >= len(backoffs):
                    raise JsonRequestError(
                        f"Request timed out after {self.timeout_seconds:g}s"
                    ) from error
                time.sleep(backoffs[attempt])
        raise JsonRequestError(str(last_error))

    def _wait_for_delay(self) -> None:
        self.rate_limiter.acquire(self.request_delay_seconds)

    def _cache_path(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
    ) -> Path:
        cache_key = json.dumps(
            {"method": method, "url": url, "body": body},
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"


def _write_atomic(path: Path, text: str) -> None:
    # A process can die mid-write (daemon threads exit with the interpreter);
    # a torn cache file would fail json.loads on every later read.
    temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _url_with_params(url: str, params: dict[str, Any]) -> str:
    if not params:
        return url
    query = urllib.parse.urlencode(params, doseq=True)
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{query}"


def _parse_retry_after(retry_after: str | None) -> int | None:
    if not retry_after:
        return None
    try:
        return max(int(retry_after), 0)
    except ValueError:
        return None


def _format_http_error(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8")
    except Exception:
        body = ""
    return f"HTTP {error.code}: {body[:500]}"
