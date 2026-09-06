"""Model and pacing settings for annotation, in one place.

Annotating a paper is many small calls rather than one big one, so the knobs
that decide cost and wall time — which model, how hard it thinks, how many
calls run at once — are read here and nowhere else.
"""

from __future__ import annotations

import os

from research_tree.credentials import openai_api_key as resolve_openai_api_key
from research_tree.llm import DEFAULT_MODEL


def annotation_model() -> str:
    return os.environ.get("RESEARCH_TREE_ANNOTATION_MODEL", DEFAULT_MODEL)


def annotation_reasoning_effort() -> str:
    """How hard the model thinks per passage.

    Annotation is judgement work — deciding what in a passage is worth a
    reader's attention and why — so this runs high by default and is lowered
    only to trade quality for cost.
    """

    return os.environ.get("RESEARCH_TREE_ANNOTATION_EFFORT", "high")


def retrieval_mode() -> str:
    """`fast` groups the paper into sections; `dense` situates every chunk.

    Dense costs one extra model call per chunk while building the index and
    returns more annotations; fast only embeds. See `retrieval.py`.
    """

    mode = os.environ.get("RESEARCH_TREE_ANNOTATION_RETRIEVAL", "fast").strip().casefold()
    return mode if mode in {"fast", "dense"} else "fast"


def annotation_concurrency() -> int:
    """Passages annotated at once.

    Held low deliberately: each call is a high-effort reasoning request, and
    the account's tokens-per-minute limit is the real ceiling.
    """

    raw = os.environ.get("RESEARCH_TREE_ANNOTATION_CONCURRENCY", "3")
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def openai_api_key() -> str:
    api_key = resolve_openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required to annotate a paper.")
    return api_key


# A high-effort call spends most of its output budget on reasoning, so these
# are sized for the reasoning plus the JSON, not the JSON alone.
ANNOTATION_TIMEOUT_SECONDS = 180.0
ANNOTATION_MAX_OUTPUT_TOKENS = 4_000
VALIDATION_MAX_OUTPUT_TOKENS = 6_000
# Every annotation call shares a prompt prefix — the rules and the examples —
# so naming that prefix lets the provider bill it as a cache hit.
PROMPT_CACHE_KEY = "research-tree-paper-annotations"
