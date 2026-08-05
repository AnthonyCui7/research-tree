"""Split blocks into passages small enough to annotate one at a time.

Splitting has to preserve where each passage sits in its page's text stream:
that offset is how an annotation's quote is later matched to the right
occurrence on the page. So the splitter returns offsets rather than searching
for its own output afterwards.
"""

from __future__ import annotations

from research_tree.annotation.extraction import should_skip_chunk


CHUNK_SIZE = 1400
# Enough overlap that a sentence spanning a split is whole in one of the two
# passages, so a quote is never cut in half by chunking alone.
CHUNK_OVERLAP = 120

# Preferred split points, widest first: paragraph, then sentence, then word.
SEPARATORS = ("\n\n", "\n", ". ", " ")


def chunk_blocks(blocks: list[dict]) -> list[dict]:
    chunks: list[dict] = []
    for block in blocks:
        for start, text in split_with_offsets(block["text"]):
            if should_skip_chunk(text):
                continue
            chunks.append(
                {
                    "page_number": block["page_number"],
                    "text": text,
                    "section_hint": block.get("section_hint"),
                    "page_text_start": block["page_text_start"] + start,
                    "page_text_end": block["page_text_start"] + start + len(text),
                    "bbox": block["bbox"],
                }
            )
    return chunks


def split_with_offsets(
    text: str,
    *,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[tuple[int, str]]:
    """Split `text` into `(offset, passage)` pairs that slice back exactly."""

    if len(text) <= chunk_size:
        return [(0, text)] if text else []

    pieces: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            end = _break_before(text, start, end)
        pieces.append((start, text[start:end]))
        if end >= len(text):
            break
        # Overlap is measured back from the end of the piece just taken, but a
        # split must always advance or the loop would never finish.
        start = max(end - overlap, start + 1)
    return pieces


def _break_before(text: str, start: int, end: int) -> int:
    """Find the widest natural boundary in the back half of the window."""

    floor = start + (end - start) // 2
    for separator in SEPARATORS:
        boundary = text.rfind(separator, floor, end)
        if boundary != -1:
            return boundary + len(separator)
    return end
