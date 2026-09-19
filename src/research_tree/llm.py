"""One place where this project talks to OpenAI.

Every LLM call in the backend goes through `call_responses_api`, and every
embedding through `call_embeddings_api`. They own the two things each call site
would otherwise duplicate: the HTTP request and usage logging.
"""

from __future__ import annotations

import http.client
import json
import logging
import socket
import time
import urllib.error
import urllib.request
from typing import Any

from research_tree.billing.usage import record_llm_usage
from research_tree.credentials import ALLOWANCE_EXHAUSTED_MESSAGE
from research_tree.log_scrub import scrub
from research_tree.principal import current_binding
from research_tree.services.errors import (
    AllowanceExhaustedError,
    ProviderRefusedError,
    ServiceUnavailableError,
    WorkspaceServiceError,
)


logger = logging.getLogger("uvicorn.error")

DEFAULT_MODEL = "gpt-5.6-luna"
# The models the assistant may be asked to use, and the only ones it will:
# every one is priced in `billing/pricing.py`, so a sponsored turn is never
# charged at a guess, and a request naming anything else is refused.
AGENT_MODELS = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")
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


# Seconds of silence before the first keepalive probe, and between probes. A
# construction call can think for minutes with nothing on the wire, and a
# cloud load balancer forgets a connection that has been silent for four; the
# answer then arrives at a connection that no longer exists and the call runs
# out its whole timeout. A probe a minute keeps the connection known.
KEEPALIVE_IDLE_SECONDS = 60
KEEPALIVE_INTERVAL_SECONDS = 30


class LlmRequestError(RuntimeError):
    pass


class _KeptAliveConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        super().connect()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Linux calls the idle time TCP_KEEPIDLE; macOS calls it TCP_KEEPALIVE.
        idle = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
        if idle is not None:
            self.sock.setsockopt(socket.IPPROTO_TCP, idle, KEEPALIVE_IDLE_SECONDS)
        if hasattr(socket, "TCP_KEEPINTVL"):
            self.sock.setsockopt(
                socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, KEEPALIVE_INTERVAL_SECONDS
            )


class _KeptAliveHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(_KeptAliveConnection, req, context=self._context)


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A request that carries a key goes where it was sent or nowhere.

    urllib follows a redirect with the original headers, `Authorization`
    included, whichever host it points at.
    """

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_KeptAliveHandler(), _RefuseRedirects())


def open_openai_request(request: urllib.request.Request, *, timeout_seconds: float) -> Any:
    """Every request that carries an OpenAI key leaves through here."""

    return _OPENER.open(request, timeout=timeout_seconds)


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

    _refuse_when_allowance_is_spent()
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

    _refuse_when_allowance_is_spent()
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


def _refuse_when_allowance_is_spent() -> None:
    """Stop sponsored work the moment its allowance runs out.

    The key was chosen when the work began; every call since has been metered
    and charged, and the charge that empties the allowance marks the binding.
    The refusal is the same 402 the account would have received at the start.
    """

    binding = current_binding()
    if (
        binding is not None
        and binding.credential_source == "sponsored"
        and binding.spend.exhausted
    ):
        raise AllowanceExhaustedError(ALLOWANCE_EXHAUSTED_MESSAGE)


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
        retries_left = attempt < len(RETRY_BACKOFF_SECONDS)
        try:
            return _post(body, url=url, api_key=api_key, timeout_seconds=timeout_seconds)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            refusal = _refusal_of_the_key(error.code, detail, model=str(body.get("model") or ""))
            if refusal is not None:
                raise refusal from error
            if error.code in RETRYABLE_HTTP_STATUS and retries_left:
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
        except TimeoutError as error:
            # The model may still be writing the answer this gave up on, and a
            # second request would be paid for as well; the caller chose how
            # long an answer is worth waiting for.
            message = f"OpenAI {label} call timed out after {timeout_seconds:g}s."
            raise LlmRequestError(f"{message}{timeout_hint}") from error
        except (OSError, http.client.HTTPException, ValueError) as error:
            # The connection could not be made, or was lost before a whole
            # answer arrived. Nothing came back to keep, so asking again is
            # the only way to have one.
            if retries_left:
                logger.warning(
                    "OpenAI %s call lost its connection (%s); retrying in %.0fs",
                    label,
                    type(error).__name__,
                    RETRY_BACKOFF_SECONDS[attempt],
                )
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])
                continue
            # The client library quotes a header it cannot send, and the
            # header is the key.
            raise LlmRequestError(f"OpenAI {label} call failed: {scrub(str(error))}") from None
    raise LlmRequestError(f"OpenAI {label} call exhausted its retries.")


def _refusal_of_the_key(status: int, detail: str, *, model: str) -> WorkspaceServiceError | None:
    """The sentence for a refusal only the key's owner can do something about.

    These fail the same way however often they are sent, so they are neither
    retried nor reported as the generic failure that tells someone to try
    again. On an account's own key the sentence says what to change. On the
    platform key the account can change nothing, so it hears that the service
    is unavailable and the operator's log hears why.
    """

    try:
        error = json.loads(detail).get("error") or {}
    except (ValueError, AttributeError):
        error = {}
    code = str(error.get("code") or "") if isinstance(error, dict) else ""
    message = str(error.get("message") or "") if isinstance(error, dict) else ""
    if status == 401:
        sentence = "OpenAI refused your API key. Replace it under API keys, then try again."
    elif code == "insufficient_quota":
        sentence = "Your OpenAI account is out of credit. Add credit with OpenAI, then try again."
    elif status == 429 and "Request too large" in message:
        sentence = (
            "This request is larger than the rate limit on your OpenAI key allows. "
            "OpenAI raises that limit as the account moves up its usage tiers."
        )
    elif code == "model_not_found":
        sentence = f"Your OpenAI key cannot use the model {model}. Allow it for the key's project, or use another key."
    else:
        return None
    binding = current_binding()
    if binding is not None and binding.credential_source == "sponsored":
        logger.error("OpenAI refused the platform key (HTTP %s %s): %s", status, code, message)
        return ServiceUnavailableError(
            "The model provider is not available right now. Try again later."
        )
    return ProviderRefusedError(sentence)


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
    with open_openai_request(request, timeout_seconds=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))
