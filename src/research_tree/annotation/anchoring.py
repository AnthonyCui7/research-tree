"""Locate each annotation's quote on the page it belongs to.

Until now an annotation carries the box of the passage it came from, which is
a paragraph. This resolves it to the rectangles the quoted words actually
occupy, which is what the reader draws.
"""

from __future__ import annotations

import logging

import fitz

from research_tree.annotation.models import BoundingBox, HighlightFragment, PaperAnnotation, TextAnchor
from research_tree.annotation.quotes import find_occurrences, normalize_quote


logger = logging.getLogger("uvicorn.error")


def resolve_annotation_geometry(
    annotations: list[PaperAnnotation],
    document: fitz.Document,
    page_sources: dict[int, str],
) -> list[PaperAnnotation]:
    """Give every annotation the anchor and rectangles of its quote."""

    located = 0
    for annotation in annotations:
        page_text = page_sources.get(annotation.page_number, "")
        anchor = resolve_anchor(annotation, page_text)
        if anchor is not None:
            annotation.anchor = anchor
        box = resolve_bounding_box(annotation, document, page_text)
        if box is not None:
            annotation.bbox = box
            located += 1
    logger.info("Located %s of %s annotations on the page", located, len(annotations))
    return annotations


def resolve_anchor(annotation: PaperAnnotation, page_text: str) -> TextAnchor | None:
    """Choose which occurrence of the quote this annotation means.

    The passage the annotation came from already narrowed it down, so prefer
    the occurrence nearest the anchor recorded then, and fall back to the first.
    """

    occurrences = find_occurrences(page_text, annotation.text_ref)
    if not occurrences:
        return None
    if annotation.anchor is None:
        return TextAnchor(
            page_text_start=occurrences[0][0],
            page_text_end=occurrences[0][1],
            occurrence_index=0,
        )

    hinted_centre = (annotation.anchor.page_text_start + annotation.anchor.page_text_end) / 2
    position, span = min(
        enumerate(occurrences),
        key=lambda entry: (abs((entry[1][0] + entry[1][1]) / 2 - hinted_centre), entry[0]),
    )
    return TextAnchor(page_text_start=span[0], page_text_end=span[1], occurrence_index=position)


def resolve_bounding_box(
    annotation: PaperAnnotation,
    document: fitz.Document,
    page_text: str,
) -> BoundingBox | None:
    """Search the page for the quote and normalize the rectangles it occupies."""

    if not 1 <= annotation.page_number <= document.page_count:
        return None
    quote = normalize_quote(annotation.text_ref)
    if not quote:
        return None

    page = document.load_page(annotation.page_number - 1)
    matches = search_page(page, quote)
    if not matches:
        return None

    rectangles = _rectangles_for_occurrence(annotation, matches, page_text, quote)
    fragments = [normalize_rectangle(rectangle, page.rect) for rectangle in rectangles]
    return BoundingBox(**_covering_box(fragments).model_dump(), fragments=fragments)


def search_page(page: fitz.Page, quote: str) -> list[fitz.Rect]:
    """Every rectangle on the page matching the quote.

    A quote spanning a line break is returned as one rectangle per line, so a
    single match can be several rectangles.
    """

    queries = [quote]
    trimmed = quote.strip("()[]{}\"'.,;: ")
    if trimmed and trimmed != quote:
        queries.append(trimmed)
    for query in queries:
        try:
            matches = page.search_for(query)
        except Exception:
            logger.warning("Searching page %s for a quote failed", page.number + 1, exc_info=True)
            return []
        if matches:
            return list(matches)
    return []


def _rectangles_for_occurrence(
    annotation: PaperAnnotation,
    matches: list[fitz.Rect],
    page_text: str,
    quote: str,
) -> list[fitz.Rect]:
    """Narrow the page's matches down to the one occurrence meant.

    When the quote appears once on the page, every rectangle belongs to it —
    which is how a quote that wraps across lines keeps all of its lines. When
    it repeats, the rectangles cannot be attributed to occurrences reliably, so
    take the single one the anchor points at.
    """

    if len(find_occurrences(page_text, quote)) <= 1:
        return matches
    position = annotation.anchor.occurrence_index if annotation.anchor else 0
    return [matches[position]] if position < len(matches) else [matches[0]]


def normalize_rectangle(rectangle: fitz.Rect, page_rect: fitz.Rect) -> HighlightFragment:
    return HighlightFragment(
        x=(rectangle.x0 - page_rect.x0) / page_rect.width,
        y=(rectangle.y0 - page_rect.y0) / page_rect.height,
        width=(rectangle.x1 - rectangle.x0) / page_rect.width,
        height=(rectangle.y1 - rectangle.y0) / page_rect.height,
    )


def _covering_box(fragments: list[HighlightFragment]) -> HighlightFragment:
    left = min(fragment.x for fragment in fragments)
    top = min(fragment.y for fragment in fragments)
    right = max(fragment.x + fragment.width for fragment in fragments)
    bottom = max(fragment.y + fragment.height for fragment in fragments)
    return HighlightFragment(x=left, y=top, width=right - left, height=bottom - top)
