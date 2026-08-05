"""Turn a PDF into positioned text blocks.

Every later stage works in the coordinate system this module establishes: each
page has one text stream, blocks own a span of it, and every rectangle is a
fraction of the page. Character offsets into that stream are what let an
annotation say *which* "attention" on the page it means.
"""

from __future__ import annotations

import re

import fitz


# An affiliation footer or a preprint watermark is on the page but is not part
# of the paper's argument, so it is dropped before anything reads it.
BOILERPLATE_MARKERS = (
    "provided proper attribution is provided",
    "arxiv:",
    "preprint under review",
    "conference on neural information processing systems",
)
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

SECTION_HEADINGS = frozenset(
    {
        "abstract",
        "introduction",
        "background",
        "related work",
        "method",
        "methods",
        "approach",
        "model",
        "experiments",
        "results",
        "discussion",
        "limitations",
        "conclusion",
        "conclusions",
        "appendix",
    }
)
NUMBERED_HEADING_PATTERN = re.compile(r"^(?:\d+(?:\.\d+)*)\s+([A-Za-z][A-Za-z0-9 ,:/-]{1,60})$")


def extract_blocks(document: fitz.Document) -> list[dict]:
    """Read every page into blocks carrying their text, section, and geometry."""

    blocks: list[dict] = []
    section_hint: str | None = None
    for page_index in range(document.page_count):
        page = document.load_page(page_index)
        page_rect = page.rect
        page_text_offset = 0
        for block in page.get_text("blocks"):
            x0, y0, x1, y1, text, *_ = block
            cleaned = " ".join(sanitize_text(text or "").split())
            if not cleaned or should_skip_block(cleaned):
                continue

            heading = infer_section_hint(cleaned)
            if heading:
                section_hint = heading

            # Blocks are joined with a newline in the page stream, so every
            # block after the first starts one character later.
            if page_text_offset:
                page_text_offset += 1
            page_text_start = page_text_offset
            page_text_end = page_text_start + len(cleaned)
            blocks.append(
                {
                    "page_number": page_index + 1,
                    "text": cleaned,
                    "section_hint": section_hint,
                    "page_text_start": page_text_start,
                    "page_text_end": page_text_end,
                    "bbox": {
                        "x": x0 / page_rect.width,
                        "y": y0 / page_rect.height,
                        "width": (x1 - x0) / page_rect.width,
                        "height": (y1 - y0) / page_rect.height,
                    },
                }
            )
            page_text_offset = page_text_end
    return blocks


def build_page_sources(blocks: list[dict]) -> dict[int, str]:
    """Join each page's blocks into the one text stream offsets refer to."""

    by_page: dict[int, list[str]] = {}
    for block in blocks:
        by_page.setdefault(block["page_number"], []).append(block["text"])
    return {page_number: "\n".join(texts) for page_number, texts in by_page.items()}


def sanitize_text(text: str) -> str:
    """Drop control and non-characters that PDF extraction leaves behind."""

    kept: list[str] = []
    for character in text:
        code_point = ord(character)
        if character in "\n\r\t":
            kept.append(character)
            continue
        if code_point < 32 or 0x7F <= code_point <= 0x9F or 0xD800 <= code_point <= 0xDFFF:
            continue
        if 0xFDD0 <= code_point <= 0xFDEF or (code_point & 0xFFFE) == 0xFFFE:
            continue
        kept.append(character)
    return "".join(kept)


def should_skip_block(text: str) -> bool:
    lowered = text.casefold()
    if any(marker in lowered for marker in BOILERPLATE_MARKERS):
        return True
    return bool(EMAIL_PATTERN.search(text))


def should_skip_chunk(text: str) -> bool:
    """Reject passages with too little prose to annotate.

    Equations, figure axes, and reference lists survive block extraction but
    have nothing an annotation could quote, and every one sent to the model
    costs a call to be told so.
    """

    normalized = text.strip()
    if len(normalized) < 80:
        return True
    if sum(character.isalpha() for character in normalized) < 40:
        return True
    return len([word for word in normalized.split() if len(word) > 3]) < 12


def infer_section_hint(text: str) -> str | None:
    """Recognize a section heading, so chunks can say where they came from."""

    normalized = " ".join(text.split())
    lowered = normalized.casefold()
    if lowered in SECTION_HEADINGS:
        return normalized.title()

    numbered = NUMBERED_HEADING_PATTERN.match(normalized)
    if numbered:
        return " ".join(numbered.group(1).split()).title()

    if len(normalized) > 60:
        return None
    words = normalized.split()
    if not 1 <= len(words) <= 8:
        return None
    alpha_words = [word for word in words if any(character.isalpha() for character in word)]
    if not alpha_words:
        return None

    # Chart axes read like headings — short, capitalised, unpunctuated — but are
    # mostly numbers and symbols. A heading is mostly letters.
    dense = normalized.replace(" ", "")
    if sum(character.isalpha() for character in dense) / len(dense) < 0.75:
        return None

    # Title Case without terminal punctuation is what a heading looks like once
    # numbering and the common names have been ruled out.
    title_like = sum(word[:1].isupper() for word in alpha_words) / len(alpha_words)
    if title_like >= 0.8 and normalized[-1:] not in {".", "?", "!"}:
        return normalized
    return None
