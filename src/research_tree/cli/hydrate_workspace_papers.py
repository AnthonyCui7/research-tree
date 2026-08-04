from __future__ import annotations

import argparse
import os
from pathlib import Path

from research_tree.artifacts import write_json_file
from research_tree.retrieval.env import load_dotenv_file
from research_tree.retrieval.semantic_scholar import SemanticScholarClient
from research_tree.workspace.enrichment import hydrate_workspace_papers
from research_tree.workspace.repository import LocalJsonWorkspaceRepository
from research_tree.workspace.serialization import load_json_artifact
from research_tree.paths import workspaces_dir


REPO_ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch rich metadata and lawful full text for visible workspace papers."
    )
    parser.add_argument("--workspace-json", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--repository-dir")
    args = parser.parse_args(argv)

    load_dotenv_file(REPO_ROOT / ".env")
    workspace_path = Path(args.workspace_json).resolve()
    workspace = load_json_artifact(workspace_path)
    if not isinstance(workspace, dict):
        raise ValueError("workspace JSON must be an object.")
    repository_dir = Path(
        args.repository_dir or workspaces_dir()
    )
    repository = LocalJsonWorkspaceRepository(repository_dir)
    semantic_scholar = SemanticScholarClient(
        cache_dir=repository_dir.parent / "cache" / "semantic_scholar",
        api_key=os.environ.get("S2_API_KEY") or os.environ.get("SEMANTIC_SCHOLAR_API_KEY"),
    )
    hydrated, warnings = hydrate_workspace_papers(
        workspace=workspace,
        repository=repository,
        semantic_scholar=semantic_scholar,
    )
    output_dir = Path(args.output_dir).resolve() if args.output_dir else workspace_path.parent
    output_path = output_dir / "workspace_with_paper_content.json"
    write_json_file(output_path, hydrated)
    print(f"Wrote hydrated workspace: {output_path}")
    for warning in warnings:
        print(f"Warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
