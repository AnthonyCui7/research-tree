"""Matching an annotation's quote to the text on the page.

An annotation is only as good as its quote: it has to appear on the page
verbatim, be short enough to point at one thing, and — when the phrase repeats
— identify which occurrence is meant.
"""

from __future__ import annotations

import re


WHITESPACE_RUN = re.compile(r"\s+")
TRIM_CHARACTERS = "()[]{}\"'.,;: "

DEFINITION_WORD_LIMIT = 8
QUOTE_WORD_LIMIT = 15
# A definition can be one word, because a term often is. A claim cannot: two
# words lifted out of a sentence point at nothing a reader could act on, and
# they match far too much of the page.
MIN_DEFINITION_WORDS = 1
MIN_QUOTE_WORDS = 3


def normalize_quote(value: str) -> str:
    """Collapse the whitespace, since page text is joined on single spaces."""

    return WHITESPACE_RUN.sub(" ", value).strip()


def quote_key(value: str) -> str:
    """A comparison key, so two quotes differing only in case or punctuation
    are recognised as the same annotation."""

    return normalize_quote(value).casefold().strip(TRIM_CHARACTERS + "!?\n\r\t`")


def word_count(value: str) -> int:
    return len(value.split())


def word_limit(annotation_type: str) -> int:
    return DEFINITION_WORD_LIMIT if annotation_type == "definition" else QUOTE_WORD_LIMIT


def find_occurrences(page_text: str, quote: str) -> list[tuple[int, int]]:
    """Every span of `page_text` matching `quote`, in reading order."""

    if not page_text or not quote:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        index = page_text.find(quote, start)
        if index == -1:
            return spans
        spans.append((index, index + len(quote)))
        start = index + 1


def shorten_quote(quote: str, annotation_type: str, page_text: str) -> str | None:
    """Trim an over-long quote to a shorter one still present on the page.

    Models quote a whole clause when a phrase would do. Rather than drop those
    annotations, look for the longest sub-phrase inside the limit that still
    appears verbatim; return None when nothing does.
    """

    normalized = normalize_quote(quote)
    limit = word_limit(annotation_type)
    if word_count(normalized) < limit:
        return normalized

    floor = MIN_DEFINITION_WORDS if annotation_type == "definition" else MIN_QUOTE_WORDS
    for candidate in _shortening_candidates(normalized, annotation_type, limit):
        if not candidate or candidate not in page_text:
            continue
        if floor <= word_count(candidate) < limit:
            return candidate
    return None


def _shortening_candidates(quote: str, annotation_type: str, limit: int) -> list[str]:
    words = quote.split()
    longest = min(len(words), limit - 1)
    if annotation_type == "definition":
        # A definition names a term, and the term leads the quote.
        floor = MIN_DEFINITION_WORDS
        candidates = [
            " ".join(words[:size]).strip(TRIM_CHARACTERS)
            for size in range(longest, floor - 1, -1)
        ]
    else:
        floor = MIN_QUOTE_WORDS
        candidates = [
            " ".join(words[start : start + size]).strip(TRIM_CHARACTERS)
            for size in range(longest, floor - 1, -1)
            for start in range(len(words) - size + 1)
        ]
    # Longest first: the most of the original quote that still fits the limit.
    return sorted(dict.fromkeys(candidates), key=lambda value: -word_count(value))
