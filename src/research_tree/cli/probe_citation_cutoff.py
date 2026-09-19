from __future__ import annotations

import argparse
import os
from pathlib import Path

from research_tree.retrieval.env import load_dotenv_file
from research_tree.paths import semantic_scholar_cache_dir
from research_tree.retrieval.semantic_scholar import (
    SEMANTIC_SCHOLAR_REQUEST_DELAY_SECONDS,
    SemanticScholarClient,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_QUERY = "Large Language Model prompting"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report the Semantic Scholar citation cutoff for one bulk query."
    )
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--k", type=int, default=50)
    parser.add_argument("--bulk-multiplier", type=int, default=25)
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=SEMANTIC_SCHOLAR_REQUEST_DELAY_SECONDS,
    )
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args(argv)
    if args.k <= 0 or args.bulk_multiplier <= 0:
        raise ValueError("--k and --bulk-multiplier must be positive.")

    load_dotenv_file(REPO_ROOT / ".env")
    target_count = args.k * args.bulk_multiplier
    warnings: list[str] = []
    client = SemanticScholarClient(
        cache_dir=semantic_scholar_cache_dir(),
        api_key=os.environ.get("S2_API_KEY") or os.environ.get("SEMANTIC_SCHOLAR_API_KEY"),
        request_delay_seconds=args.request_delay_seconds,
        refresh_cache=args.refresh_cache,
    )
    papers = client.search_by_citation_count(
        query=args.query,
        target_count=target_count,
        warnings=warnings,
    )
    cutoff = papers[-1].citation_count if papers else None
    print(f"Query: {args.query}")
    print(f"Requested: {target_count} papers ({args.k} × {args.bulk_multiplier})")
    print(f"Returned: {len(papers)} papers")
    print(f"Citation cutoff: {cutoff if cutoff is not None else 'unavailable'}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
