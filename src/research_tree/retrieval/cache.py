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
import logging
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

logger = logging.getLogger("uvicorn.error")


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


# Reserves the next request slot atomically on the Redis server's own clock,
# so every process sharing the key queues into one lane without a busy loop
# and without trusting container clocks. Returns how long the caller waits.
# How long the shared lane stays out of play after Redis refuses one call. At
# roughly one Semantic Scholar request a second, this costs one failed call per
# thirty while Redis is down, and a blip splits the lane for half a minute
# rather than until the container restarts.
REDIS_RETRY_SECONDS = 30.0

_RESERVE_SLOT_LUA = """
local key = KEYS[1]
local delay = tonumber(ARGV[1])
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
local last = tonumber(redis.call('GET', key) or '0')
local slot = math.max(now, last + delay)
redis.call('SET', key, slot, 'PX', delay * 20)
return slot - now
"""


class RateLimiter:
    """Spaces request starts across everything that shares this limiter.

    Semantic Scholar counts one request per second cumulatively across all of
    its endpoints, so the budget belongs to the API key rather than to any one
    client. With Redis configured, the last-request timestamp lives there and
    the spacing holds across containers (the API, the worker, a CLI). With a
    `lock_file` it lives in that file under an exclusive flock, which spans
    *processes* on one machine. (Measured Aug 2026: three backend processes
    with independent in-memory limiters tripled the request rate under one
    key and kept it in Semantic Scholar's penalty box.) Without either it is
    in-memory only.
    """

    def __init__(
        self,
        lock_file: Path | None = None,
        *,
        redis_client: Any = None,
        redis_key: str = "s2:rate_limiter",
    ) -> None:
        self._last_request_at = 0.0
        self._lock = Lock()
        self._lock_file = lock_file
        self._redis_client = redis_client
        self._redis_resolved = redis_client is not None
        self._redis_key = redis_key
        self._redis_unavailable_until = 0.0
        self._reserve_slot: Any = None

    def acquire(self, delay_seconds: float) -> None:
        with self._lock:
            if delay_seconds <= 0:
                self._last_request_at = time.monotonic()
                return
            wait = self._reserve_across_containers(delay_seconds)
            if wait is not None:
                if wait > 0:
                    time.sleep(wait)
                return
            if self._lock_file is not None:
                self._acquire_across_processes(delay_seconds)
                return
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < delay_seconds:
                time.sleep(delay_seconds - elapsed)
            self._last_request_at = time.monotonic()

    def _redis(self) -> Any:
        if not self._redis_resolved:
            from research_tree.redis_client import get_redis

            self._redis_client = get_redis()
            self._redis_resolved = True
        return self._redis_client

    def _reserve_across_containers(self, delay_seconds: float) -> float | None:
        """Seconds to wait for the reserved slot, or None when Redis is not in play."""

        client = self._redis()
        if client is None or time.monotonic() < self._redis_unavailable_until:
            return None
        try:
            if self._reserve_slot is None:
                self._reserve_slot = client.register_script(_RESERVE_SLOT_LUA)
            wait_ms = self._reserve_slot(
                keys=[self._redis_key], args=[int(delay_seconds * 1000)]
            )
        except Exception as error:  # noqa: BLE001 - fall back rather than stall every request
            # Back off, do not give up. This used to latch for the life of the
            # process, so one blip left the API and the worker each keeping
            # their own lock file and their own idea of the shared lane, which
            # is the arrangement that overruns Semantic Scholar's budget.
            self._reserve_slot = None
            self._redis_unavailable_until = time.monotonic() + REDIS_RETRY_SECONDS
            logger.warning(
                "Redis rate limiter unavailable (%s); using the local limiter for %d seconds.",
                error,
                REDIS_RETRY_SECONDS,
            )
            return None
        return max(int(wait_ms), 0) / 1000.0

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


class JsonResponseCache(Protocol):
    """Where a provider's JSON responses are kept between runs."""

    def get(self, key: str) -> Any | None: ...

    def put(self, key: str, payload: Any) -> None: ...


class FileJsonResponseCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> Any | None:
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, key: str, payload: Any) -> None:
        _write_atomic(self.cache_dir / f"{key}.json", json.dumps(payload, indent=2))


class ArtifactJsonResponseCache:
    """The same cache on the artifact store (Blob in the cloud), so every
    container shares one copy and a redeploy does not empty it."""

    def __init__(self, store: Any, prefix: str = "s2") -> None:
        self.store = store
        self.prefix = prefix

    def get(self, key: str) -> Any | None:
        raw = self.store.get(f"{self.prefix}/{key}")
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def put(self, key: str, payload: Any) -> None:
        self.store.put(f"{self.prefix}/{key}", json.dumps(payload, indent=2).encode("utf-8"))


def default_response_cache(cache_dir: Path) -> JsonResponseCache:
    from research_tree.artifact_store import blob_account_url, default_artifact_store

    if blob_account_url():
        return ArtifactJsonResponseCache(default_artifact_store())
    return FileJsonResponseCache(cache_dir)


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
        response_cache: JsonResponseCache | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.request_delay_seconds = request_delay_seconds
        self.refresh_cache = refresh_cache
        self.headers = headers or {}
        self.max_retries = max(max_retries, 0)
        self.timeout_seconds = max(timeout_seconds, 1.0)
        self.rate_limiter = rate_limiter or RateLimiter()
        self.cache = response_cache or default_response_cache(cache_dir)

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        full_url = _url_with_params(url, params or {})
        cache_key = self._cache_key("GET", full_url)
        if not self.refresh_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        payload = self._request_with_retries("GET", full_url, None)
        self.cache.put(cache_key, payload)
        return payload

    def post_json(self, url: str, body: dict[str, Any]) -> Any:
        cache_key = self._cache_key("POST", url, body)
        if not self.refresh_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        payload = self._request_with_retries("POST", url, body)
        self.cache.put(cache_key, payload)
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
                # The server's own number wins, but never past the longest wait
                # this ladder would take by itself. An absurd `Retry-After`
                # otherwise parks the stage, and the thread running it, for
                # hours on a promise nobody checked.
                asked_for = _parse_retry_after(error.headers.get("Retry-After"))
                wait_seconds = (
                    min(asked_for, max(backoffs)) if asked_for else backoffs[attempt]
                )
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

    def _cache_key(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
    ) -> str:
        cache_key = json.dumps(
            {"method": method, "url": url, "body": body},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(cache_key.encode("utf-8")).hexdigest()

    def _cache_path(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
    ) -> Path:
        # The file the on-disk cache uses for this request; kept for callers
        # that inspect the cache directly.
        return self.cache_dir / f"{self._cache_key(method, url, body)}.json"


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
