"""What caller data has to look like for this service to carry it.

Two limits, both about the machinery rather than the product.

Postgres stores text and json as UTF-8 and refuses U+0000 outright, and a JSON
response cannot encode an unpaired surrogate, so a value carrying either one
fails on the way into the database or on the way back out to the browser.

Nesting has a ceiling too. The response serializer gives up at 255 levels, and
a document that got past that depth on the way in could never be read back: the
workspace stayed in the sidebar and answered 500 to every request for it.

Numbers have to be finite. Python's parser admits `NaN` and `Infinity`, which
JSON does not have: Postgres refuses them, the file store writes them and the
response turns them into `null`, so the stored document and the one the browser
sees would disagree.

The request models apply this to every body the API accepts. The workspace
validator applies it again to documents the pipeline and the assistant build,
which never pass through a request.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

# U+0000, and the surrogate range, which reaches a running program only as the
# unpaired half of a pair that never arrived.
UNSTORABLE_CHARACTERS = re.compile("[\x00\ud800-\udfff]")

# The five workspaces this was measured against are six and seven levels deep.
# Sixty-four leaves an order of magnitude above a real document and a wide
# margin below the serializer's limit.
MAX_DEPTH = 64


def unstorable_reason(value: Any, *, path: str) -> str | None:
    """The first reason this service cannot carry `value`, or None.

    `path` names what is being checked, and the reason points at the part of it
    that is wrong. A depth refusal names `path` itself rather than the place the
    ceiling was reached, because that place is sixty-four subscripts long and
    everything under the named field is equally the problem.

    The ceiling bounds this walk's own recursion as well as the caller's data,
    so a hostile payload is refused rather than exhausting the stack on the way
    to being refused.
    """

    def walk(item: Any, where: str, depth: int) -> str | None:
        if depth > MAX_DEPTH:
            return f"{path} is nested more than {MAX_DEPTH} levels deep."
        if isinstance(item, str):
            if UNSTORABLE_CHARACTERS.search(item):
                return f"{where} contains a character that cannot be stored."
            return None
        if isinstance(item, float) and not math.isfinite(item):
            return f"{where} is not a finite number."
        if isinstance(item, Mapping):
            for key, entry in item.items():
                if isinstance(key, str) and UNSTORABLE_CHARACTERS.search(key):
                    return f"{where} has a field name containing a character that cannot be stored."
                reason = walk(entry, f"{where}.{key}", depth + 1)
                if reason is not None:
                    return reason
            return None
        if isinstance(item, (list, tuple)):
            for index, entry in enumerate(item):
                reason = walk(entry, f"{where}[{index}]", depth + 1)
                if reason is not None:
                    return reason
            return None
        return None

    return walk(value, path, 0)
