"""One place where this project talks to the OpenAI Responses API.

Every LLM call in the backend goes through `call_responses_api`, which owns the
two things each call site would otherwise duplicate: the HTTP request and usage
logging.
"""

from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from typing import Any


logger = logging.getLogger("uvicorn.error")

DEFAULT_MODEL = "gpt-5.6-luna"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

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
    for attempt in range(len(RETRY_BACKOFF_SECONDS) + 1):
        try:
            raw_response = _post(body, api_key=api_key, timeout_seconds=timeout_seconds)
            break
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            if error.code in RETRYABLE_HTTP_STATUS and attempt < len(RETRY_BACKOFF_SECONDS):
                delay = _retry_after_seconds(error) or RETRY_BACKOFF_SECONDS[attempt]
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

    log_llm_usage(
        label=label,
        model=str(body.get("model") or ""),
        raw_response=raw_response,
        elapsed_seconds=time.monotonic() - started_at,
    )
    return raw_response


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
    api_key: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))
