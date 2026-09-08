"""`research-tree-import-data`: move a JSON data directory into Postgres.

Reads workspaces, versions, navigation, events, reviews, and pipeline runs
through the JSON repository and writes them through the Postgres one, so the
two stores agree on every invariant. Per-paper text and annotations go to the
artifact store. Everything lands under one owner: the account named by
`--owner-email`, or the local user on a deployment without accounts. Running
it twice is a no-op: versions and events are keyed by content, reviews are
overwritten with the files' copy.

Run inside the deployed container:

    az containerapp exec -n api -g research-tree --command \\
        "research-tree-import-data --data-dir /import --owner-email you@example.com"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

from research_tree.artifact_store import default_artifact_store
from research_tree.db import DATABASE_URL_ENV, database_url, make_engine
from research_tree.paths import data_root
from research_tree.principal import LOCAL_USER_ID
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.postgres_repository import PostgresWorkspaceRepository
from research_tree.workspace.repository import (
    LocalJsonWorkspaceRepository,
    _read_json,
    normalized_topic_key,
    workspace_summary,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-tree-import-data", description=__doc__)
    parser.add_argument("--data-dir", default=None, help="the JSON data root (default: RESEARCH_TREE_DATA_DIR)")
    parser.add_argument("--database-url", default=None, help=f"overrides {DATABASE_URL_ENV}")
    parser.add_argument(
        "--owner-email",
        default=None,
        help="the account that will own everything imported (default: the local user)",
    )
    parser.add_argument("--workspace-id", action="append", default=[], help="import only these ids (repeatable)")
    parser.add_argument("--s2-cache", action="store_true", help="also copy the Semantic Scholar cache to the artifact store")
    parser.add_argument("--dry-run", action="store_true", help="report what would be written and stop")
    args = parser.parse_args(argv)

    url = args.database_url or database_url()
    if not url:
        print(f"{DATABASE_URL_ENV} is not set.", file=sys.stderr)
        return 2
    root = Path(args.data_dir) if args.data_dir else data_root()
    source_dir = root / "workspaces"
    if not source_dir.is_dir():
        print(f"no workspaces directory under {root}", file=sys.stderr)
        return 2

    engine = make_engine(url, pool_size=2, max_overflow=1)
    owner_id = LOCAL_USER_ID
    if args.owner_email:
        with engine.begin() as conn:
            row = conn.execute(
                text('SELECT id FROM "user" WHERE lower(email) = lower(:email)'),
                {"email": args.owner_email.strip()},
            ).first()
        if row is None:
            print(f"no account with email {args.owner_email}; sign in once first.", file=sys.stderr)
            return 2
        owner_id = str(row[0])
    artifacts = default_artifact_store()
    source = LocalJsonWorkspaceRepository(source_dir)
    target = PostgresWorkspaceRepository(engine, owner_id=owner_id, artifacts=artifacts)

    workspace_ids = args.workspace_id or [
        path.name
        for path in sorted(source_dir.iterdir())
        if path.is_dir() and not path.name.startswith(".") and (path / "current.json").is_file()
    ]
    totals = {"workspaces": 0, "versions": 0, "events": 0, "agent_events": 0, "reviews": 0, "artifacts": 0, "runs": 0}
    for workspace_id in workspace_ids:
        counts = _import_workspace(source, target, engine, workspace_id, dry_run=args.dry_run)
        for key, value in counts.items():
            totals[key] += value
        print(f"{workspace_id}: " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
    totals["runs"] = _import_runs(source_dir, engine, owner_id, workspace_ids, dry_run=args.dry_run)
    if args.s2_cache:
        totals["artifacts"] += _import_s2_cache(root, artifacts, dry_run=args.dry_run)
    print(
        ("would write " if args.dry_run else "wrote ")
        + ", ".join(f"{k}={v}" for k, v in totals.items())
        + f" for {owner_id}"
    )
    return 0


def _import_workspace(
    source: LocalJsonWorkspaceRepository,
    target: PostgresWorkspaceRepository,
    engine: Any,
    workspace_id: str,
    *,
    dry_run: bool,
) -> dict[str, int]:
    counts = {"workspaces": 0, "versions": 0, "events": 0, "agent_events": 0, "reviews": 0, "artifacts": 0}
    workspace_dir = source._workspace_dir(workspace_id)
    current = source.get_current_workspace(workspace_id)
    current_hash = workspace_version_hash(current)
    navigation = source._workspace_navigation(workspace_id)
    versions_dir = workspace_dir / "versions"
    documents: dict[str, dict[str, Any]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    if versions_dir.is_dir():
        for path in sorted(versions_dir.glob("*.json")):
            if path.name.endswith(".metadata.json"):
                item = _read_json(path)
                if isinstance(item, dict) and item.get("version_hash"):
                    metadata[str(item["version_hash"])] = item
                continue
            document = _read_json(path)
            if isinstance(document, dict):
                computed = workspace_version_hash(document)
                if computed != path.stem:
                    raise SystemExit(f"{path}: content hash {computed} does not match the filename")
                documents[computed] = document
    if current_hash not in documents:
        # An unfiled current.json (pre-versioning data) becomes a version too.
        documents[current_hash] = current
    hashes = [str(item) for item in navigation["version_hashes"]]
    for version_hash in hashes:
        if version_hash not in documents:
            raise SystemExit(f"{workspace_id}: navigation names {version_hash} but no version file exists")
    if hashes and hashes[navigation["current_index"]] != current_hash:
        raise SystemExit(f"{workspace_id}: navigation's current entry does not match current.json")
    events = source.list_workspace_events(workspace_id)
    event_ids = [str(event.get("event_id")) for event in events]
    if len(event_ids) != len(set(event_ids)):
        raise SystemExit(f"{workspace_id}: duplicate event ids in events.jsonl")
    agent_events = source.list_agent_run_events(workspace_id)
    reviews = source.list_workspace_reviews(workspace_id)
    paper_content = sorted((workspace_dir / "paper_content").glob("*.json")) if (workspace_dir / "paper_content").is_dir() else []
    annotations = sorted((workspace_dir / "annotations").glob("*.json")) if (workspace_dir / "annotations").is_dir() else []

    counts.update(
        workspaces=1,
        versions=len(documents),
        events=len(events),
        agent_events=len(agent_events),
        reviews=len(reviews),
        artifacts=len(paper_content) + len(annotations),
    )
    if dry_run:
        return counts

    owner_id = target.owner_id
    summary = workspace_summary(workspace_id, current)
    with engine.begin() as conn:
        target._lock_workspace(conn, workspace_id)
        conn.execute(
            text(
                """
                INSERT INTO workspaces (owner_id, id, topic, title, topic_key)
                VALUES (:owner, :id, :topic, :title, :topic_key)
                ON CONFLICT (owner_id, id) DO NOTHING
                """
            ),
            {
                "owner": owner_id,
                "id": workspace_id,
                "topic": summary["topic"],
                "title": summary["title"],
                "topic_key": normalized_topic_key(summary["topic"] or summary["title"]),
            },
        )
        for version_hash, document in documents.items():
            item = metadata.get(version_hash) or {
                "schema_version": "research_tree.workspace_version_metadata.v1",
                "workspace_id": workspace_id,
                "version_hash": version_hash,
                "parent_version_hash": None,
                "actor": "system",
                "actor_type": "system",
                "actor_id": "workspace_agent",
                "reason": "imported current workspace",
                "agent_run_id": None,
                "created_at": None,
            }
            conn.execute(
                text(
                    """
                    INSERT INTO workspace_versions
                        (owner_id, workspace_id, version_hash, document, metadata)
                    VALUES (:owner, :id, :hash, CAST(:document AS json), CAST(:metadata AS jsonb))
                    ON CONFLICT (owner_id, workspace_id, version_hash) DO NOTHING
                    """
                ),
                {
                    "owner": owner_id,
                    "id": workspace_id,
                    "hash": version_hash,
                    "document": json.dumps(document),
                    "metadata": json.dumps(item),
                },
            )
        target._update_head(conn, workspace_id, summary)
        if not hashes:
            hashes, current_index = [current_hash], 0
        else:
            current_index = int(navigation["current_index"])
        target._write_navigation(conn, workspace_id, hashes, current_index)
        for event in events:
            payload = event.get("payload")
            review_id = payload.get("review_id") if isinstance(payload, dict) else None
            conn.execute(
                text(
                    """
                    INSERT INTO workspace_events
                        (owner_id, workspace_id, event_id, event_type, review_id, payload)
                    VALUES (:owner, :workspace_id, :event_id, :event_type, :review_id,
                            CAST(:payload AS jsonb))
                    ON CONFLICT (event_id) DO NOTHING
                    """
                ),
                {
                    "owner": owner_id,
                    "workspace_id": workspace_id,
                    "event_id": str(event.get("event_id")),
                    "event_type": str(event.get("event_type") or ""),
                    "review_id": str(review_id) if review_id else None,
                    "payload": json.dumps(event),
                },
            )
        for event in agent_events:
            conn.execute(
                text(
                    """
                    INSERT INTO agent_run_events
                        (owner_id, workspace_id, run_event_id, agent_run_id, status, payload)
                    VALUES (:owner, :workspace_id, :run_event_id, :agent_run_id, :status,
                            CAST(:payload AS jsonb))
                    ON CONFLICT (run_event_id) DO NOTHING
                    """
                ),
                {
                    "owner": owner_id,
                    "workspace_id": workspace_id,
                    "run_event_id": str(event.get("run_event_id")),
                    "agent_run_id": str(event.get("agent_run_id") or ""),
                    "status": str(event.get("status") or ""),
                    "payload": json.dumps(event),
                },
            )
        for review in reviews:
            target._write_review(conn, workspace_id, review)
    for path in paper_content:
        payload = _read_json(path)
        if isinstance(payload, dict) and payload.get("paper_id"):
            target.save_paper_content(workspace_id, str(payload["paper_id"]), payload)
    for path in annotations:
        payload = _read_json(path)
        if isinstance(payload, dict) and payload.get("paper_id"):
            target.save_paper_annotations(workspace_id, str(payload["paper_id"]), payload)
    return counts


def _import_runs(source_dir: Path, engine: Any, owner_id: str, workspace_ids: list[str], *, dry_run: bool) -> int:
    runs_dir = source_dir / ".pipeline_runs"
    if not runs_dir.is_dir():
        return 0
    count = 0
    wanted = set(workspace_ids)
    for path in sorted(runs_dir.glob("*.json")):
        run = _read_json(path)
        if not isinstance(run, dict) or not run.get("run_id"):
            continue
        if run.get("workspace_id") not in wanted:
            continue
        run = dict(run)
        if run.get("status") in {"queued", "running"}:
            # Nothing is executing it any more; say so rather than leave it active.
            run.update({"status": "failed", "current_stage": None, "error": "imported while active"})
        run["owner_id"] = owner_id
        count += 1
        if dry_run:
            continue
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO pipeline_runs (run_id, owner_id, workspace_id, topic_key, status, record)
                    VALUES (:run_id, :owner, :workspace_id, :topic_key, :status, CAST(:record AS jsonb))
                    ON CONFLICT (run_id) DO NOTHING
                    """
                ),
                {
                    "run_id": str(run["run_id"]),
                    "owner": owner_id,
                    "workspace_id": str(run.get("workspace_id") or "") or None,
                    "topic_key": normalized_topic_key(str(run.get("topic") or "")),
                    "status": str(run.get("status") or ""),
                    "record": json.dumps(run),
                },
            )
    return count


def _import_s2_cache(root: Path, artifacts: Any, *, dry_run: bool) -> int:
    cache_dir = root / "cache" / "semantic_scholar"
    if not cache_dir.is_dir():
        return 0
    files = sorted(cache_dir.glob("*.json"))
    if not dry_run:
        for path in files:
            artifacts.put(f"s2/{path.stem}", path.read_bytes())
    return len(files)


if __name__ == "__main__":
    raise SystemExit(main())
