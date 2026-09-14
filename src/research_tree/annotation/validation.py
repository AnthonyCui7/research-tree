"""Decide which candidate annotations survive.

Three filters, cheapest last so the expensive one sees fewer inputs: drop
duplicates, ask the model to review each page as a whole, then enforce the
rules that can be checked against the page without asking anyone.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable

from research_tree.annotation.config import (
    ANNOTATION_TIMEOUT_SECONDS,
    PROMPT_CACHE_KEY,
    VALIDATION_MAX_OUTPUT_TOKENS,
    annotation_model,
    annotation_reasoning_effort,
)
from research_tree.annotation.generation import parse_annotation_json
from research_tree.annotation.models import PaperAnnotation
from research_tree.annotation.prompts import VALIDATION_INSTRUCTIONS, build_validation_input
from research_tree.annotation.quotes import normalize_quote, quote_key, shorten_quote, word_count, word_limit
from research_tree.llm import call_responses_api
from research_tree.services.errors import WorkspaceServiceError
from research_tree.workspace.serialization import extract_response_output_text


logger = logging.getLogger("uvicorn.error")

TYPE_PRIORITY = {"definition": 0, "note": 1, "highlight": 2}


def dedupe_annotations(annotations: list[PaperAnnotation]) -> list[PaperAnnotation]:
    """Keep the strongest annotation per quote, in reading order."""

    winners: dict[str, tuple[PaperAnnotation, int]] = {}
    for position, annotation in enumerate(annotations):
        key = quote_key(annotation.text_ref)
        if not key:
            continue
        current = winners.get(key)
        if current is None or _rank(annotation, position) > _rank(*current):
            winners[key] = (annotation, position)
    return _in_reading_order(winners.values())


def review_annotations_by_page(
    annotations: list[PaperAnnotation],
    page_sources: dict[int, str],
    *,
    api_key: str,
) -> list[PaperAnnotation]:
    """Show the model a page's annotations together and take its corrections.

    Passages are annotated in isolation, so overlap and near-duplicates only
    become visible with the page in view. A page whose review comes back
    unusable keeps what it had; a review that keeps nothing on a page is
    taken at its word, since deleting is one of the things it is asked to do.
    """

    if not annotations:
        return []

    reviewed: list[PaperAnnotation] = []
    for page_number, page_annotations in _grouped_by_page(annotations).items():
        page_source = page_sources.get(page_number, "")
        try:
            items = _request_review(page_number, page_source, page_annotations, api_key=api_key)
        except WorkspaceServiceError:
            # The allowance ran out, or the key is gone: that ends the paper,
            # not this page.
            raise
        except Exception as error:
            logger.warning("Reviewing page %s failed (%s); keeping its annotations", page_number, error)
            reviewed.extend(page_annotations)
            continue
        if items is None:
            logger.warning("Review of page %s was not JSON; keeping its annotations", page_number)
            reviewed.extend(page_annotations)
            continue
        if not items:
            logger.info("Page %s review kept none of %s", page_number, len(page_annotations))
            continue
        kept = _rebuild_reviewed(items, page_annotations, page_number)
        logger.info("Page %s review kept %s of %s", page_number, len(kept), len(page_annotations))
        # Items none of which could be read back are an unusable review, not
        # a verdict on every annotation.
        reviewed.extend(kept or page_annotations)
    return reviewed


def enforce_quote_rules(
    annotations: list[PaperAnnotation],
    page_sources: dict[int, str],
) -> list[PaperAnnotation]:
    """Keep only annotations whose quote is on its page and short enough."""

    kept: list[PaperAnnotation] = []
    dropped: Counter[str] = Counter()

    for annotation in annotations:
        page_text = page_sources.get(annotation.page_number, "")
        annotation.text_ref = normalize_quote(annotation.text_ref)
        annotation.note = annotation.note.strip()
        if not annotation.text_ref or not annotation.note:
            dropped["empty"] += 1
            continue
        if annotation.text_ref not in page_text:
            # Nothing can place a quote the page does not contain.
            dropped["quote_not_on_page"] += 1
            continue
        shortened = shorten_quote(annotation.text_ref, annotation.type, page_text)
        if shortened is None or word_count(shortened) >= word_limit(annotation.type):
            dropped["quote_too_long"] += 1
            continue
        annotation.text_ref = shortened
        kept.append(annotation)

    logger.info(
        "Quote rules kept %s of %s annotations (%s)",
        len(kept),
        len(annotations),
        ", ".join(f"{reason}={count}" for reason, count in dropped.most_common()) or "none dropped",
    )
    # Shortening can collapse two annotations onto the same quote.
    return dedupe_annotations(kept)


def _request_review(
    page_number: int,
    page_source: str,
    annotations: list[PaperAnnotation],
    *,
    api_key: str,
) -> list | None:
    body = {
        "model": annotation_model(),
        "instructions": VALIDATION_INSTRUCTIONS,
        "input": build_validation_input(
            {page_number: page_source},
            [
                {
                    "type": annotation.type,
                    "text_ref": annotation.text_ref,
                    "note": annotation.note,
                    "importance": annotation.importance,
                    "page_number": annotation.page_number,
                }
                for annotation in annotations
            ],
        ),
        "text": {"format": {"type": "text"}, "verbosity": "low"},
        "reasoning": {"effort": annotation_reasoning_effort()},
        "max_output_tokens": VALIDATION_MAX_OUTPUT_TOKENS,
        "tool_choice": "none",
        "store": False,
        "prompt_cache_key": PROMPT_CACHE_KEY,
    }
    raw_response = call_responses_api(
        body,
        api_key=api_key,
        timeout_seconds=ANNOTATION_TIMEOUT_SECONDS,
        label="annotation review",
    )
    return parse_annotation_json(extract_response_output_text(raw_response))


def _rebuild_reviewed(
    items: list,
    page_annotations: list[PaperAnnotation],
    page_number: int,
) -> list[PaperAnnotation]:
    """Apply the review's text to the annotations it revised.

    The review only ever sees text, so geometry is carried over from the
    annotation whose quote it started from.
    """

    rebuilt: list[PaperAnnotation] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        quote = normalize_quote(str(item.get("text_ref") or ""))
        note = str(item.get("note") or "").strip()
        if not quote or not note or item.get("type") not in TYPE_PRIORITY:
            continue
        if item.get("importance") not in (1, 2, 3):
            continue
        source = _closest_annotation(page_annotations, quote)
        rebuilt.append(
            PaperAnnotation(
                type=item["type"],
                text_ref=quote,
                note=note,
                importance=item["importance"],
                page_number=page_number,
                bbox=source.bbox,
                anchor=source.anchor,
            )
        )
    return rebuilt


def _closest_annotation(annotations: list[PaperAnnotation], quote: str) -> PaperAnnotation:
    key = quote_key(quote)
    for annotation in annotations:
        if quote_key(annotation.text_ref) == key:
            return annotation
    # A rewritten quote still sits on the page the review was given, and the
    # exact rectangle is resolved from the page afterwards regardless.
    return annotations[0]


def _grouped_by_page(annotations: list[PaperAnnotation]) -> dict[int, list[PaperAnnotation]]:
    grouped: dict[int, list[PaperAnnotation]] = {}
    for annotation in annotations:
        grouped.setdefault(annotation.page_number, []).append(annotation)
    return grouped


def _rank(annotation: PaperAnnotation, position: int) -> tuple[int, int, int]:
    return (annotation.importance, TYPE_PRIORITY[annotation.type], -position)


def _in_reading_order(
    winners: Iterable[tuple[PaperAnnotation, int]],
) -> list[PaperAnnotation]:
    return [
        annotation
        for annotation, _ in sorted(winners, key=lambda entry: (entry[0].page_number, entry[1]))
    ]
