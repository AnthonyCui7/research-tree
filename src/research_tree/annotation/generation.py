"""Ask the model what is worth annotating, one passage at a time.

Passages are independent, so they are annotated concurrently, and a passage
that fails is dropped rather than failing the paper. Everything here produces
candidates; deciding which survive is `validation.py`.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from research_tree.annotation.config import (
    ANNOTATION_MAX_OUTPUT_TOKENS,
    ANNOTATION_TIMEOUT_SECONDS,
    PROMPT_CACHE_KEY,
    annotation_concurrency,
    annotation_model,
    annotation_reasoning_effort,
)
from research_tree.annotation.models import BoundingBox, PaperAnnotation, TextAnchor
from research_tree.annotation.prompts import (
    ANNOTATION_INSTRUCTIONS,
    REPAIR_INSTRUCTIONS,
    build_annotation_input,
    build_repair_input,
)
from research_tree.annotation.quotes import find_occurrences, normalize_quote, quote_key
from research_tree.annotation.retrieval import RetrievalIndex
from research_tree.llm import call_responses_api
from research_tree.workspace.serialization import extract_response_output_text


logger = logging.getLogger("uvicorn.error")

JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")
JSON_ARRAY = re.compile(r"\[[\s\S]*\]")

NEIGHBOUR_CONTEXT_CHARS = 180
BRIEF_SAMPLE_WORDS = 22
ANNOTATION_TYPES = {"highlight", "note", "definition"}


def annotate_chunks(
    chunks: list[dict],
    *,
    page_sources: dict[int, str],
    paper_brief: str,
    index: RetrievalIndex,
    api_key: str,
) -> list[PaperAnnotation]:
    annotations: list[PaperAnnotation] = []
    guard = threading.Lock()

    def annotate(position: int) -> list[PaperAnnotation]:
        chunk = chunks[position]
        text = _request_annotations(
            chunk["text"],
            paper_brief=paper_brief,
            related_passages=index.related_passages(position),
            local_context=neighbour_context(chunks, position),
            page_number=chunk["page_number"],
            section_hint=chunk.get("section_hint"),
            api_key=api_key,
        )
        items = parse_annotation_json(text)
        if items is None:
            logger.warning(
                "Annotation output for page %s was not JSON; repairing", chunk["page_number"]
            )
            items = _repair_annotations(text, chunk["text"], api_key=api_key)
        return build_annotations(items or [], chunk, page_sources)

    with ThreadPoolExecutor(max_workers=annotation_concurrency()) as pool:
        futures = {pool.submit(annotate, position): position for position in range(len(chunks))}
        for future in as_completed(futures):
            position = futures[future]
            try:
                produced = future.result()
            except Exception as error:
                # One unusable passage out of a hundred is a better outcome
                # than no annotations at all.
                logger.warning(
                    "Annotating passage %s on page %s failed (%s): %s",
                    position,
                    chunks[position]["page_number"],
                    type(error).__name__,
                    error,
                )
                continue
            with guard:
                annotations.extend(produced)

    logger.info("Collected %s candidate annotations from %s passages", len(annotations), len(chunks))
    return annotations


def build_annotations(
    items: list[dict],
    chunk: dict,
    page_sources: dict[int, str],
) -> list[PaperAnnotation]:
    """Turn one passage's model output into annotations, dropping malformed ones."""

    built: list[PaperAnnotation] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        quote = normalize_quote(str(item.get("text_ref") or ""))
        note = str(item.get("note") or "").strip()
        annotation_type = item.get("type")
        importance = item.get("importance")
        if not quote or not note or annotation_type not in ANNOTATION_TYPES:
            continue
        if importance not in (1, 2, 3):
            continue
        key = quote_key(quote)
        if key in seen:
            continue
        seen.add(key)
        built.append(
            PaperAnnotation(
                type=annotation_type,
                text_ref=quote,
                note=note,
                importance=importance,
                page_number=chunk["page_number"],
                # The passage's own box until the quote is located exactly.
                bbox=BoundingBox(**chunk["bbox"]),
                anchor=anchor_within_chunk(
                    page_sources.get(chunk["page_number"], ""),
                    quote,
                    chunk["page_text_start"],
                    chunk["page_text_end"],
                ),
            )
        )
    return built


def anchor_within_chunk(
    page_text: str,
    quote: str,
    chunk_start: int,
    chunk_end: int,
) -> TextAnchor | None:
    """Pick the occurrence of `quote` that the annotated passage covers."""

    occurrences = find_occurrences(page_text, quote)
    if not occurrences:
        return None
    chunk_centre = (chunk_start + chunk_end) / 2
    position, span = min(
        enumerate(occurrences),
        key=lambda entry: (
            0 if _overlaps(entry[1], (chunk_start, chunk_end)) else 1,
            abs((entry[1][0] + entry[1][1]) / 2 - chunk_centre),
            entry[0],
        ),
    )
    return TextAnchor(page_text_start=span[0], page_text_end=span[1], occurrence_index=position)


def parse_annotation_json(text: str) -> list | None:
    """Read the JSON array out of a reply, tolerating fences and stray prose."""

    if not text:
        return None
    candidate = text.strip()
    fenced = JSON_FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    array = JSON_ARRAY.search(candidate)
    if array:
        candidate = array.group(0)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def build_paper_brief(title: str, abstract: str, chunks: list[dict]) -> str:
    """A few lines of orientation carried into every passage's prompt.

    Sampled rather than generated: a summary call per paper would cost more
    than it adds, and what the prompt needs is only enough to place a passage.
    """

    lines = [f"- Paper: {_shorten_words(title, 30)}"]
    if abstract.strip():
        lines.append(f"- Abstract: {_shorten_words(abstract, 45)}")
    labels = ("Early", "Middle", "Late")
    for label, position in zip(labels, _sample_positions(len(chunks), len(labels))):
        chunk = chunks[position]
        section = chunk.get("section_hint")
        prefix = f"{label} ({section})" if section else label
        lines.append(f"- {prefix}: {_shorten_words(chunk['text'], BRIEF_SAMPLE_WORDS)}")
    return "\n".join(lines)


def neighbour_context(chunks: list[dict], position: int) -> str:
    """The tail and head of the passages either side, so a quote's sentence is
    recognisable even when the split landed mid-argument."""

    parts: list[str] = []
    if position > 0:
        previous = normalize_quote(chunks[position - 1]["text"])
        parts.append(f"Previous passage ends:\n...{previous[-NEIGHBOUR_CONTEXT_CHARS:].lstrip()}")
    if position + 1 < len(chunks):
        following = normalize_quote(chunks[position + 1]["text"])
        parts.append(f"Next passage begins:\n{following[:NEIGHBOUR_CONTEXT_CHARS].rstrip()}...")
    return "\n\n".join(parts)


def _request_annotations(
    passage: str,
    *,
    paper_brief: str,
    related_passages: list[str],
    local_context: str,
    page_number: int,
    section_hint: str | None,
    api_key: str,
) -> str:
    body = {
        "model": annotation_model(),
        "instructions": ANNOTATION_INSTRUCTIONS,
        "input": build_annotation_input(
            passage,
            paper_brief=paper_brief,
            related_passages=related_passages,
            local_context=local_context,
            page_number=page_number,
            section_hint=section_hint,
        ),
        "text": {"format": {"type": "text"}, "verbosity": "low"},
        "reasoning": {"effort": annotation_reasoning_effort()},
        "max_output_tokens": ANNOTATION_MAX_OUTPUT_TOKENS,
        "tool_choice": "none",
        "store": False,
        "prompt_cache_key": PROMPT_CACHE_KEY,
    }
    raw_response = call_responses_api(
        body,
        api_key=api_key,
        timeout_seconds=ANNOTATION_TIMEOUT_SECONDS,
        label="annotation",
    )
    return extract_response_output_text(raw_response)


def _repair_annotations(broken_output: str, passage: str, *, api_key: str) -> list | None:
    body = {
        "model": annotation_model(),
        "instructions": REPAIR_INSTRUCTIONS,
        "input": build_repair_input(passage, broken_output),
        "text": {"format": {"type": "text"}, "verbosity": "low"},
        # Reformatting output that already exists needs no deliberation.
        "reasoning": {"effort": "low"},
        "max_output_tokens": ANNOTATION_MAX_OUTPUT_TOKENS,
        "tool_choice": "none",
        "store": False,
        "prompt_cache_key": PROMPT_CACHE_KEY,
    }
    raw_response = call_responses_api(
        body,
        api_key=api_key,
        timeout_seconds=ANNOTATION_TIMEOUT_SECONDS,
        label="annotation repair",
    )
    return parse_annotation_json(extract_response_output_text(raw_response))


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _sample_positions(total: int, count: int) -> list[int]:
    """Evenly spaced positions across the paper, without repeats."""

    if total <= 0 or count <= 0:
        return []
    if total <= count:
        return list(range(total))
    return sorted({round(step * (total - 1) / (count - 1)) for step in range(count)})


def _shorten_words(text: str, limit: int) -> str:
    words = normalize_quote(text).split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit]).rstrip(".,;:") + "..."
