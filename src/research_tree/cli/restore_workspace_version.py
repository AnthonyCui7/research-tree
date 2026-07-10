from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from research_tree.workspace.repository import LocalJsonWorkspaceRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List or restore immutable Research Tree workspace versions."
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument(
        "--repository-dir",
        default=os.environ.get("RESEARCH_TREE_DATA_DIR", "data/workspaces"),
    )
    parser.add_argument("--version-hash")
    parser.add_argument("--list", action="store_true", help="List saved versions and exit.")
    parser.add_argument(
        "--reason",
        default="restored a previous workspace version",
    )
    args = parser.parse_args(argv)

    if args.list == bool(args.version_hash):
        raise ValueError("pass exactly one of --list or --version-hash.")

    repository = LocalJsonWorkspaceRepository(Path(args.repository_dir))
    if args.list:
        print(json.dumps(repository.list_workspace_versions(args.workspace_id), indent=2))
        return 0

    result = repository.restore_workspace_version(
        args.workspace_id,
        args.version_hash,
        actor="user",
        actor_type="user",
        reason=args.reason,
    )
    if result["restored"]:
        print(f"Restored workspace version: {result['version_hash']}")
        print(f"Replaced version: {result['before_hash']}")
    else:
        print(f"Workspace already uses version: {result['version_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
