from __future__ import annotations

import re
from difflib import SequenceMatcher


SURVEY_TITLE_PATTERNS = (
    "survey",
    "review",
    "overview",
    "taxonomy",
    "tutorial",
    "systematic review",
    "comprehensive review",
)


def truncate_words(value: str, max_words: int) -> str:
    """Cut text to a word budget, the unit prompts are actually reasoned about.

    Characters were the old unit and they cut mid-sentence at a length nobody
    could picture. Words survive the round trip: 250 words is roughly a full
    abstract, and the ellipsis says the rest was dropped rather than absent.
    """

    words = value.split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]) + "..."


def normalize_title(title: str) -> str:
    normalized = title.casefold()
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    value = doi.strip().casefold()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:", "", value).strip()
    return value or None


def normalize_arxiv_id(arxiv_id: str | None) -> str | None:
    if not arxiv_id:
        return None
    value = arxiv_id.strip()
    value = re.sub(r"^arxiv:", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\.pdf$", "", value, flags=re.IGNORECASE)
    return value or None


def looks_like_survey(title: str, publication_types: list[str] | None = None) -> bool:
    normalized_title = normalize_title(title)
    if any(pattern in normalized_title for pattern in SURVEY_TITLE_PATTERNS):
        return True
    for publication_type in publication_types or []:
        normalized_type = publication_type.casefold()
        if "review" in normalized_type or "survey" in normalized_type:
            return True
    return False


def titles_are_near_duplicates(left: str, right: str) -> bool:
    left_normalized = normalize_title(left)
    right_normalized = normalize_title(right)
    if not left_normalized or not right_normalized:
        return False
    if left_normalized == right_normalized:
        return True
    return SequenceMatcher(None, left_normalized, right_normalized).ratio() >= 0.92
