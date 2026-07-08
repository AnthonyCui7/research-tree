from __future__ import annotations

import argparse
from pathlib import Path

from research_tree.workspace.similar_papers import (
    build_similar_papers_from_files,
    write_similar_paper_artifacts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enrich Research Tree paper cards with similar-paper recommendations."
    )
    parser.add_argument("--workspace-json", required=True)
    parser.add_argument("--paper-database-json", required=True)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--bi-encoder-model", default="all-MiniLM-L6-v2")
    parser.add_argument(
        "--cross-encoder-model",
        default="cross-encoder/ms-marco-MiniLM-L6-v2",
    )
    parser.add_argument("--output-dir")
    args = parser.parse_args(argv)

    if args.k <= 0:
        raise ValueError("--k must be positive.")

    workspace_json_path = Path(args.workspace_json).resolve()
    output_dir = (
        Path(args.output_dir).resolve() if args.output_dir else workspace_json_path.parent
    )
    workspace_with_similar_papers, debug = build_similar_papers_from_files(
        workspace_json_path=workspace_json_path,
        paper_database_json_path=Path(args.paper_database_json),
        k=args.k,
        bi_encoder_model=args.bi_encoder_model,
        cross_encoder_model=args.cross_encoder_model,
    )
    paths = write_similar_paper_artifacts(
        output_dir=output_dir,
        workspace_with_similar_papers=workspace_with_similar_papers,
        debug=debug,
        run_label=workspace_json_path.parent.name,
    )
    print(f"Wrote workspace with similar papers: {paths['workspace']}")
    print(f"Wrote similar-papers debug: {paths['debug']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
