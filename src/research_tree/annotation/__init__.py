"""Inline annotations for a paper's PDF.

`generate_paper_annotations` reads a PDF and returns the annotations a reader
sees over it: a quote, a note explaining why the quote matters, and the
rectangles the quote occupies on the page.
"""

from research_tree.annotation.models import (
    ANNOTATION_SCHEMA_VERSION,
    ANNOTATION_TYPE_LABELS,
    BoundingBox,
    HighlightFragment,
    PaperAnnotation,
    TextAnchor,
)
from research_tree.annotation.pipeline import generate_paper_annotations


__all__ = [
    "ANNOTATION_SCHEMA_VERSION",
    "ANNOTATION_TYPE_LABELS",
    "BoundingBox",
    "HighlightFragment",
    "PaperAnnotation",
    "TextAnchor",
    "generate_paper_annotations",
]
