"""One place where this project talks to OpenAI.

Every LLM call in the backend goes through `call_responses_api`, and every
embedding through `call_embeddings_api`. They own the two things each call site
would otherwise duplicate: the HTTP request and usage logging.
"""

from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from typing import Any

from research_tree.billing.usage import record_llm_usage


logger = logging.getLogger("uvicorn.error")

DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"

# Rate limits and gateway errors are routine on a shared key; only these are
# worth a second attempt, since a 4xx will fail identically however long we
# wait. The tiers cover a tokens-per-minute limit, which is what a large
# construction call actually trips: the observed advice is "try again in ~10s",
# and the last tier outlasts that even when several calls are queued behind it.
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
RETRY_BACKOFF_SECONDS = (2.0, 8.0, 20.0)


class LlmRequestError(RuntimeError):
    pass


def call_responses_api(
    body: dict[str, Any],
    *,
    api_key: str,
    timeout_seconds: float,
    label: str,
    timeout_hint: str = "",
) -> dict[str, Any]:
    """POST one Responses request and return the parsed payload.

    `label` names the call in error messages and logs. `timeout_hint` is appended
    to the timeout error when a caller can suggest a remedy.

    Note for future callers: reasoning models reject `temperature` outright
    ("Unsupported parameter"), so no request built here sets it. Randomness is
    controlled through `reasoning.effort` and the prompt instead.
    """

    started_at = time.monotonic()
    raw_response = _request_with_retries(
        OPENAI_RESPONSES_URL,
        body,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        label=label,
        timeout_hint=timeout_hint,
    )
    log_llm_usage(
        label=label,
        model=str(body.get("model") or ""),
        raw_response=raw_response,
        elapsed_seconds=time.monotonic() - started_at,
    )
    record_llm_usage(model=str(body.get("model") or ""), raw_response=raw_response, label=label)
    return raw_response


def call_embeddings_api(
    texts: list[str],
    *,
    api_key: str,
    timeout_seconds: float,
    label: str,
    model: str = DEFAULT_EMBEDDING_MODEL,
) -> list[list[float]]:
    """Embed one batch of texts and return the vectors in the order given.

    Batching across several requests belongs to the caller, which knows how big
    its corpus is; this stays a single request, like `call_responses_api`.
    """

    started_at = time.monotonic()
    raw_response = _request_with_retries(
        OPENAI_EMBEDDINGS_URL,
        {"model": model, "input": texts},
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        label=label,
        timeout_hint="",
    )
    usage = raw_response.get("usage")
    logger.info(
        "%s embeddings model=%s elapsed_seconds=%.3f count=%s input_tokens=%s",
        label,
        model,
        time.monotonic() - started_at,
        len(texts),
        usage.get("prompt_tokens") if isinstance(usage, dict) else None,
    )
    record_llm_usage(model=model, raw_response=raw_response, label=label)

    data = raw_response.get("data")
    if not isinstance(data, list) or len(data) != len(texts):
        raise LlmRequestError(f"OpenAI {label} call returned {len(texts)} texts unmatched by vectors.")
    vectors: list[list[float]] = []
    for item in sorted(data, key=lambda entry: entry.get("index", 0)):
        embedding = item.get("embedding")
        if not isinstance(embedding, list):
            raise LlmRequestError(f"OpenAI {label} call returned an entry without an embedding.")
        vectors.append([float(value) for value in embedding])
    return vectors


def _request_with_retries(
    url: str,
    body: dict[str, Any],
    *,
    api_key: str,
    timeout_seconds: float,
    label: str,
    timeout_hint: str,
) -> dict[str, Any]:
    for attempt in range(len(RETRY_BACKOFF_SECONDS) + 1):
        try:
            return _post(body, url=url, api_key=api_key, timeout_seconds=timeout_seconds)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            if error.code in RETRYABLE_HTTP_STATUS and attempt < len(RETRY_BACKOFF_SECONDS):
                # Never wait longer than this ladder's own worst case, however
                # long the server asks for.
                asked_for = _retry_after_seconds(error)
                delay = (
                    min(asked_for, max(RETRY_BACKOFF_SECONDS))
                    if asked_for
                    else RETRY_BACKOFF_SECONDS[attempt]
                )
                logger.warning(
                    "OpenAI %s call got HTTP %s; retrying in %.0fs",
                    label,
                    error.code,
                    delay,
                )
                time.sleep(delay)
                continue
            raise LlmRequestError(f"OpenAI {label} call failed: {detail}") from error
        except urllib.error.URLError as error:
            raise LlmRequestError(f"OpenAI {label} call failed: {error}") from error
        except (TimeoutError, socket.timeout) as error:
            message = f"OpenAI {label} call timed out after {timeout_seconds:g}s."
            raise LlmRequestError(f"{message}{timeout_hint}") from error
    raise LlmRequestError(f"OpenAI {label} call exhausted its retries.")


def _retry_after_seconds(error: urllib.error.HTTPError) -> float | None:
    raw_value = error.headers.get("Retry-After") if error.headers else None
    try:
        return max(float(str(raw_value)), 0.0)
    except (TypeError, ValueError):
        return None


def log_llm_usage(
    *,
    label: str,
    model: str,
    raw_response: dict[str, Any],
    elapsed_seconds: float,
) -> None:
    usage = raw_response.get("usage")
    if not isinstance(usage, dict):
        logger.info(
            "%s LLM model=%s elapsed_seconds=%.3f usage=unavailable",
            label,
            model,
            elapsed_seconds,
        )
        return
    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    logger.info(
        "%s LLM model=%s elapsed_seconds=%.3f input_tokens=%s cached_input_tokens=%s "
        "cache_write_tokens=%s output_tokens=%s reasoning_tokens=%s",
        label,
        model,
        elapsed_seconds,
        usage.get("input_tokens"),
        input_details.get("cached_tokens") if isinstance(input_details, dict) else None,
        input_details.get("cache_write_tokens") if isinstance(input_details, dict) else None,
        usage.get("output_tokens"),
        output_details.get("reasoning_tokens") if isinstance(output_details, dict) else None,
    )


def _post(
    body: dict[str, Any],
    *,
    url: str,
    api_key: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))
