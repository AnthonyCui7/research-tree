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
    hits = search_page(page, quote)
    if not hits:
        return None

    rectangles = _rectangles_for_occurrence(annotation, hits)
    fragments = [normalize_rectangle(rectangle, page.rect) for rectangle in rectangles]
    return BoundingBox(**_covering_box(fragments).model_dump(), fragments=fragments)


def search_page(page: fitz.Page, quote: str) -> list[list[fitz.Rect]]:
    """Every occurrence of the quote on the page, each as its rectangles.

    The viewer returns one rectangle per line and no boundary between
    occurrences, so a quote that wraps once and repeats once comes back as
    three rectangles. Each rectangle holds the words it covers, and the words
    of one occurrence read the whole quote, which is what tells the hits apart.
    """

    queries = [quote]
    trimmed = quote.strip("()[]{}\"'.,;: ")
    if trimmed and trimmed != quote:
        queries.append(trimmed)
    for query in queries:
        try:
            rectangles = list(page.search_for(query))
        except Exception:
            logger.warning("Searching page %s for a quote failed", page.number + 1, exc_info=True)
            return []
        if rectangles:
            return _group_by_occurrence(page, rectangles, query)
    return []


def _group_by_occurrence(
    page: fitz.Page, rectangles: list[fitz.Rect], quote: str
) -> list[list[fitz.Rect]]:
    target = _squash(quote)
    hits: list[list[fitz.Rect]] = []
    current: list[fitz.Rect] = []
    covered = ""
    for rectangle in rectangles:
        current.append(rectangle)
        covered += _squash(page.get_text("text", clip=rectangle))
        if target in covered or len(covered) >= len(target):
            hits.append(current)
            current, covered = [], ""
    if current:
        hits.append(current)
    return hits


def _squash(text: str) -> str:
    return "".join(text.split()).casefold()


def _rectangles_for_occurrence(
    annotation: PaperAnnotation,
    hits: list[list[fitz.Rect]],
) -> list[fitz.Rect]:
    """The occurrence the anchor names, or the first when it names none."""

    position = annotation.anchor.occurrence_index if annotation.anchor else 0
    return hits[position] if position < len(hits) else hits[0]


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
