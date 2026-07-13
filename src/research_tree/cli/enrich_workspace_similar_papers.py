from __future__ import annotations

import argparse
import os
from pathlib import Path

from research_tree.retrieval.env import load_dotenv_file
from research_tree.workspace.publishing import publish_workspace_version
from research_tree.workspace.serialization import load_json_artifact
from research_tree.workspace.similar_papers import (
    build_similar_papers_from_files,
    write_similar_paper_artifacts,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Attach similar papers from an existing candidate paper database."
    )
    parser.add_argument("--workspace-json", required=True)
    parser.add_argument(
        "--paper-database-json",
        required=True,
        help="s2_bulk_deduped_paper_database.json from the workspace candidate-preparation run.",
    )
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--output-dir")
    parser.add_argument("--publish-to-repository", action="store_true")
    parser.add_argument("--repository-dir")
    parser.add_argument("--expected-current-version")
    args = parser.parse_args(argv)
    if not 1 <= args.k <= 20:
        raise ValueError("--k must be between 1 and 20.")
    if args.expected_current_version and not args.publish_to_repository:
        raise ValueError("--expected-current-version requires --publish-to-repository.")

    load_dotenv_file(REPO_ROOT / ".env")
    workspace_path = Path(args.workspace_json).resolve()
    workspace = load_json_artifact(workspace_path)
    if not isinstance(workspace, dict):
        raise ValueError("workspace JSON must be an object.")
    repository_dir = Path(
        args.repository_dir or os.environ.get("RESEARCH_TREE_DATA_DIR", "data/workspaces")
    )
    output_dir = Path(args.output_dir).resolve() if args.output_dir else workspace_path.parent
    enriched, debug = build_similar_papers_from_files(
        workspace_json_path=workspace_path,
        paper_database_json_path=Path(args.paper_database_json),
        k=args.k,
    )
    output_paths = write_similar_paper_artifacts(
        output_dir=output_dir,
        workspace_with_similar_papers=enriched,
        debug=debug,
        run_label=workspace_path.parent.name,
    )
    output_path = output_paths["workspace"]
    debug_path = output_paths["debug"]
    print(f"Wrote workspace with related papers: {output_path}")
    if args.publish_to_repository:
        result = publish_workspace_version(
            repository_dir=repository_dir,
            workspace=enriched,
            reason="enriched workspace with candidate-pool similar papers",
            event_type="workspace_similar_papers_enriched",
            event_payload={"source_workspace": str(workspace_path), "k": args.k},
            expected_parent_version_hash=args.expected_current_version,
        )
        print(f"Published workspace version: {result['version_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
