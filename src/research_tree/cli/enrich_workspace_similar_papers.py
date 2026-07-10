from __future__ import annotations

import argparse
import os
from pathlib import Path

from research_tree.workspace.publishing import publish_workspace_version
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
    parser.add_argument(
        "--publish-to-repository",
        action="store_true",
        help=(
            "Also save the enriched workspace as the current version in the local "
            "workspace repository."
        ),
    )
    parser.add_argument(
        "--repository-dir",
        help=(
            "Workspace repository directory for --publish-to-repository. Defaults "
            "to RESEARCH_TREE_DATA_DIR or data/workspaces."
        ),
    )
    parser.add_argument(
        "--publish-reason",
        default="enriched workspace with similar papers",
    )
    parser.add_argument(
        "--expected-current-version",
        help=(
            "Only publish if this workspace version is still current. This "
            "prevents overwriting a newer workspace with stale enrichment."
        ),
    )
    args = parser.parse_args(argv)

    if args.k <= 0:
        raise ValueError("--k must be positive.")
    if args.expected_current_version and not args.publish_to_repository:
        raise ValueError("--expected-current-version requires --publish-to-repository.")

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
    if args.publish_to_repository:
        publish_result = publish_workspace_version(
            repository_dir=_repository_dir(args.repository_dir),
            workspace=workspace_with_similar_papers,
            reason=args.publish_reason,
            event_type="workspace_similar_papers_enriched",
            event_payload={
                "source_workspace": str(workspace_json_path),
                "paper_database": str(Path(args.paper_database_json).resolve()),
                "workspace_artifact": str(paths["workspace"]),
                "debug_artifact": str(paths["debug"]),
                "k": args.k,
                "bi_encoder_model": args.bi_encoder_model,
                "cross_encoder_model": args.cross_encoder_model,
            },
            expected_parent_version_hash=args.expected_current_version,
        )
        if publish_result["published"]:
            print(f"Published workspace version: {publish_result['version_hash']}")
            print(f"Replaced version: {publish_result['parent_version_hash']}")
            print(f"Published workspace event: {publish_result['event_id']}")
        else:
            print(f"Workspace already current: {publish_result['version_hash']}")
    return 0


def _repository_dir(value: str | None) -> Path:
    return Path(value or os.environ.get("RESEARCH_TREE_DATA_DIR", "data/workspaces"))


if __name__ == "__main__":
    raise SystemExit(main())
