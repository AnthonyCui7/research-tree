from __future__ import annotations

import json
from typing import Any


PAPER_TLDR_PROMPT_VERSION = "paper_tldr.v1"


def build_missing_paper_tldr_prompt(papers: list[dict[str, Any]]) -> str:
    return f"""You write concise research-paper TLDRs for Research Tree.

For each paper below, write one factual sentence that explains its central
contribution. Use only its title and abstract. Do not invent results, datasets,
or claims. Return valid JSON only in this exact shape:

{{"tldrs":[{{"paper_id":"...","text":"..."}}]}}

Papers:
{json.dumps(papers, ensure_ascii=False)}
"""
