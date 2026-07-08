from __future__ import annotations

import hashlib
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class JsonRequestError(RuntimeError):
    pass


class CachedJsonClient:
    def __init__(
        self,
        cache_dir: Path,
        request_delay_seconds: float,
        refresh_cache: bool = False,
        headers: dict[str, str] | None = None,
        max_retries: int = 2,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.cache_dir = cache_dir
        self.request_delay_seconds = request_delay_seconds
        self.refresh_cache = refresh_cache
        self.headers = headers or {}
        self.max_retries = max(max_retries, 0)
        self.timeout_seconds = max(timeout_seconds, 1.0)
        self._last_request_at = 0.0
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        full_url = _url_with_params(url, params or {})
        cache_path = self._cache_path("GET", full_url)
        if cache_path.exists() and not self.refresh_cache:
            return json.loads(cache_path.read_text(encoding="utf-8"))

        payload = self._request_with_retries("GET", full_url, None)
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def post_json(self, url: str, body: dict[str, Any]) -> Any:
        cache_path = self._cache_path("POST", url, body)
        if cache_path.exists() and not self.refresh_cache:
            return json.loads(cache_path.read_text(encoding="utf-8"))

        payload = self._request_with_retries("POST", url, body)
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
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
        backoffs = [30, 60, 120, 240, 240][: self.max_retries]
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
                if error.code != 429 or attempt >= len(backoffs):
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
        if self.request_delay_seconds <= 0:
            self._last_request_at = time.monotonic()
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.request_delay_seconds:
            time.sleep(self.request_delay_seconds - elapsed)
        self._last_request_at = time.monotonic()

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
