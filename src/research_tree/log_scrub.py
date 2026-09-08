"""Keep API keys out of the logs, whatever path they arrive by."""

from __future__ import annotations

import logging
import re

# OpenAI keys ("sk-…", "sk-proj-…") and anything that looks like one.
_SECRET_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")
_REDACTED = "sk-…[redacted]"


class SecretScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _SECRET_PATTERN.sub(_REDACTED, record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {key: _scrub(value) for key, value in record.args.items()}
            else:
                record.args = tuple(_scrub(value) for value in record.args)
        return True


def install_log_scrubbing() -> None:
    for name in (None, "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        if not any(isinstance(existing, SecretScrubFilter) for existing in logger.filters):
            logger.addFilter(SecretScrubFilter())
        for handler in logger.handlers:
            if not any(isinstance(existing, SecretScrubFilter) for existing in handler.filters):
                handler.addFilter(SecretScrubFilter())


def scrub(text: str) -> str:
    return _SECRET_PATTERN.sub(_REDACTED, text)


def _scrub(value: object) -> object:
    if isinstance(value, str):
        return _SECRET_PATTERN.sub(_REDACTED, value)
    # A dict or a list formatted into a message prints through `repr`, which
    # carried the key straight through when only strings were scrubbed. The
    # value is replaced by its scrubbed text only when it actually holds one,
    # so a `%d` argument is still a number.
    printed = str(value)
    if _SECRET_PATTERN.search(printed):
        return _SECRET_PATTERN.sub(_REDACTED, printed)
    return value
