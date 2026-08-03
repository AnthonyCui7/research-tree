"""Run both candidate pipelines on the same topics and report the difference.

The citation-graph stage replaced an age-adjusted citation ranking. This script
exists so that replacement stays measurable: it runs each pipeline over the same
topics, sharing one Semantic Scholar cache, and writes a side-by-side report of
what each one selected, how many requests it spent, and how long it took.

Usage:
    uv run python scripts/compare_candidate_pipelines.py
    uv run python scripts/compare_candidate_pipelines.py --topics "sampling" "prompting"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from research_tree.retrieval import cache as cache_module
from research_tree.retrieval.candidate_preparation import (
    PipelineConfig,
    run_workspace_candidate_preparation_pipeline,
)
from research_tree.retrieval.env import load_dotenv_file
from research_tree.retrieval.legacy.candidate_preparation import (
    PipelineConfig as LegacyPipelineConfig,
    run_workspace_candidate_preparation_pipeline as run_legacy_pipeline,
)

DEFAULT_TOPICS = ["sampling", "prompting", "retrieval augmented generation"]
OUTPUT_DIR = REPO_ROOT / "experiments" / "output" / "pipeline_comparison"


class RequestCounter:
    """Counts HTTP requests that actually left the process (cache misses)."""

    def __init__(self) -> None:
        self.count = 0
        self._original = cache_module.CachedJsonClient._request_text_with_retries

    def __enter__(self) -> "RequestCounter":
        counter = self

        def counted(client: Any, method: str, url: str, body: Any) -> str:
            counter.count += 1
            return counter._original(client, method, url, body)

        cache_module.CachedJsonClient._request_text_with_retries = counted  # type: ignore[method-assign]
        return self

    def __exit__(self, *exc: object) -> None:
        cache_module.CachedJsonClient._request_text_with_retries = self._original  # type: ignore[method-assign]


def run_pipeline(topic: str, *, legacy: bool) -> dict[str, Any]:
    config: Any = (
        LegacyPipelineConfig(repo_root=REPO_ROOT, topic=topic, verbose=False)
        if legacy
        else PipelineConfig(repo_root=REPO_ROOT, topic=topic, verbose=False)
    )
    started_at = time.monotonic()
    with RequestCounter() as counter:
        try:
            output = (run_legacy_pipeline if legacy else run_workspace_candidate_preparation_pipeline)(
                config
            )
        except Exception as error:  # a failed side of the comparison is a result
            return {"error": f"{type(error).__name__}: {error}", "requests": counter.count}
    return {
        "output": output,
        "requests": counter.count,
        "seconds": round(time.monotonic() - started_at, 1),
    }


def paper_rows(output: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *(output.get("non_survey_papers") or []),
        *(output.get("survey_papers") or []),
    ]


def format_paper(paper: dict[str, Any]) -> str:
    year = paper.get("year") or "?"
    citations = paper.get("citation_count") or 0
    degree = paper.get("in_degree")
    marks = "".join(
        [
            " [survey]" if paper.get("is_survey") else "",
            " [snowball]" if paper.get("snowballed") else "",
        ]
    )
    degree_text = f", deg={degree}" if degree is not None else ""
    return f"({year}, c={citations}{degree_text}) {paper.get('title')}{marks}"


def compare_topic(topic: str) -> list[str]:
    lines = [f"## {topic}", ""]
    results = {
        "citation graph (current)": run_pipeline(topic, legacy=False),
        "age-adjusted citations (legacy)": run_pipeline(topic, legacy=True),
    }

    selections: dict[str, list[dict[str, Any]]] = {}
    for name, result in results.items():
        if "error" in result:
            lines.append(f"### {name}: FAILED — {result['error']}")
            lines.append("")
            selections[name] = []
            continue
        output = result["output"]
        papers = paper_rows(output)
        selections[name] = papers
        lines.append(
            f"### {name} — {len(papers)} papers, "
            f"{result['requests']} S2 requests, {result['seconds']}s"
        )
        warnings = output.get("warnings") or []
        if warnings:
            lines.append(f"warnings: {len(warnings)}")
            lines.extend(f"  - {warning}" for warning in warnings[:5])
        lines.extend(f"- {format_paper(paper)}" for paper in papers)
        lines.append("")

    names = list(selections)
    first, second = selections[names[0]], selections[names[1]]
    first_ids = {str(paper.get("paper_id")) for paper in first}
    second_ids = {str(paper.get("paper_id")) for paper in second}
    shared = first_ids & second_ids
    denominator = min(len(first_ids), len(second_ids)) or 1
    lines.append(f"### Overlap: {len(shared)}/{denominator} ({len(shared) / denominator:.0%})")
    lines.append("")
    lines.append(f"Only in {names[0]}:")
    lines.extend(
        f"- {format_paper(paper)}"
        for paper in first
        if str(paper.get("paper_id")) not in second_ids
    )
    lines.append("")
    lines.append(f"Only in {names[1]}:")
    lines.extend(
        f"- {format_paper(paper)}"
        for paper in second
        if str(paper.get("paper_id")) not in first_ids
    )
    lines.append("")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topics", nargs="+", default=DEFAULT_TOPICS)
    parser.add_argument("--output", default=str(OUTPUT_DIR / "comparison.md"))
    args = parser.parse_args(argv)

    load_dotenv_file(REPO_ROOT / ".env")
    lines = ["# Candidate pipeline comparison", ""]
    for topic in args.topics:
        print(f"[compare] {topic}", flush=True)
        lines.extend(compare_topic(topic))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[compare] wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
