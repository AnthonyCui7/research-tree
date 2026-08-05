"""The annotation contract shared by the pipeline, the cache, and the reader.

Geometry is stored page-normalized — every coordinate is a fraction of the page
width or height — so the reader can place a highlight at whatever scale it
happens to be rendering the page.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ANNOTATION_SCHEMA_VERSION = "research_tree.paper_annotations.v1"

AnnotationType = Literal["highlight", "note", "definition"]

# What each kind of annotation is for, in one line, so the reader's legend and
# the prompt rules cannot drift apart.
ANNOTATION_TYPE_LABELS: dict[str, str] = {
    "highlight": "Key claim or result",
    "note": "Implication or caveat",
    "definition": "Term explained",
}


class HighlightFragment(BaseModel):
    """One rectangle of a highlight, as a fraction of the page."""

    x: float
    y: float
    width: float
    height: float


class BoundingBox(HighlightFragment):
    """The whole highlight, plus the per-line rectangles that make it up.

    A quoted phrase that wraps across lines occupies several rectangles. The
    outer box covers all of them, which is enough to scroll to; `fragments` is
    what the reader actually paints.
    """

    fragments: list[HighlightFragment] = Field(default_factory=list)


class TextAnchor(BaseModel):
    """Where the quote sits in the page's text, for disambiguating repeats."""

    page_text_start: int
    page_text_end: int
    occurrence_index: int


class PaperAnnotation(BaseModel):
    type: AnnotationType
    """The exact quote from the page that the annotation is attached to."""
    text_ref: str
    note: str
    importance: Literal[1, 2, 3]
    page_number: int
    bbox: BoundingBox
    anchor: TextAnchor | None = None
