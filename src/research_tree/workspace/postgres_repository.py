"""The workspace repository on Postgres.

Every public method is one transaction. Mutations of a workspace first take
that workspace's transaction-scoped advisory lock, which gives the same
serialisation the JSON repository gets from its process lock — across
processes, replicas, and the worker. Documents and review payloads are stored
as `json` (not `jsonb`) because both are hashed after a round trip; everything
else is `jsonb`. Large per-paper payloads (extracted text, annotations) are
not rows at all: they go to the artifact store under content-addressed keys.

Liveness for pipeline runs is a heartbeat column rather than a PID probe: a
run whose owner stopped touching it for three minutes is failed on read, which
is how a crashed worker surfaces to the reader.

With a Redis client, every committed change is announced on a channel
(`research_tree:workspaces` for the collection, `research_tree:runs:{id}` for
one run) after the transaction commits, so the API's event streams can wake
up instead of polling. A failed announcement is logged and otherwise ignored.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from research_tree.artifact_store import ArtifactStore, FilesystemArtifactStore
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.repository import (
    StaleVersionError,
    WorkspaceRepositoryBase,
    _actor_fields,
    _now,
    _paper_content_key,
    _safe_version_hash,
    _topic_key,
    _versions_from_navigation,
    dedupe_workspace_summaries,
    workspace_summary,
)

logger = logging.getLogger("uvicorn.error")

ACTIVE_RUN_STATUSES = ("queued", "running")
WORKSPACES_CHANNEL = "research_tree:workspaces"
RUN_CHANNEL_PREFIX = "research_tree:runs:"
RUNNING_RECLAIM_AFTER = "3 minutes"
QUEUED_RECLAIM_AFTER = "30 minutes"
RECLAIM_ERROR = "Pipeline worker stopped before this run completed."


class PostgresWorkspaceRepository(WorkspaceRepositoryBase):
    def __init__(
        self,
        engine: Engine,
        *,
        artifacts: ArtifactStore | None = None,
        redis: Any = None,
    ) -> None:
        self._engine = engine
        self._artifacts = artifacts or FilesystemArtifactStore()
        self._redis = redis

    @contextmanager
    def _transaction(self, workspace_id: str | None = None) -> Iterator[Connection]:
        channels: set[str] = set()
        with self._engine.begin() as conn:
            conn.info["notify"] = channels
            if workspace_id is not None:
                _lock_workspace(conn, workspace_id)
            yield conn
        self._publish(channels)

    @staticmethod
    def _notify(conn: Connection, *channels: str) -> None:
        pending = conn.info.get("notify")
        if pending is not None:
            pending.update(channels)

    def _publish(self, channels: set[str]) -> None:
        if self._redis is None or not channels:
            return
        for channel in sorted(channels):
            try:
                self._redis.publish(channel, "1")
            except Exception as error:  # noqa: BLE001 - streams fall back to polling
                logger.warning("change notification failed on %s: %s", channel, error)
                return

    # ---- workspaces -------------------------------------------------------

    def list_workspaces(self, *, owner_id: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT id, current_version_hash, title, topic, paper_count, branch_count,
                   paper_path_count, document_updated_at
            FROM workspaces
            WHERE deleted_at IS NULL AND current_version_hash IS NOT NULL
        """
        params: dict[str, Any] = {}
        if owner_id is not None:
            sql += " AND owner_id = CAST(:owner_id AS uuid)"
            params["owner_id"] = owner_id
        with self._engine.begin() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        summaries = [
            {
                "workspace_id": row["id"],
                "workspace_version_hash": row["current_version_hash"],
                "title": row["title"],
                "topic": row["topic"],
                "paper_count": row["paper_count"],
                "branch_count": row["branch_count"],
                "paper_path_count": row["paper_path_count"],
                "updated_at": row["document_updated_at"],
            }
            for row in rows
        ]
        return dedupe_workspace_summaries(summaries)

    def workspace_id_is_taken(self, workspace_id: str) -> bool:
        # Deleted workspaces keep their id: a rebuilt "prompting" becomes
        # "prompting-2" rather than adopting the deleted one's history.
        with self._engine.begin() as conn:
            row = conn.execute(
                text("SELECT 1 FROM workspaces WHERE id = :id"), {"id": workspace_id}
            ).first()
        return row is not None

    def get_workspace_owner_id(self, workspace_id: str) -> str | None:
        with self._engine.begin() as conn:
            row = conn.execute(
                text("SELECT owner_id FROM workspaces WHERE id = :id AND deleted_at IS NULL"),
                {"id": workspace_id},
            ).first()
        if row is None or row[0] is None:
            return None
        return str(row[0])

    def _get_current_workspace(self, ctx: Connection, workspace_id: str) -> dict[str, Any]:
        row = ctx.execute(
            text(
                """
                SELECT v.document
                FROM workspaces w
                JOIN workspace_versions v
                  ON v.workspace_id = w.id AND v.version_hash = w.current_version_hash
                WHERE w.id = :id AND w.deleted_at IS NULL
                """
            ),
            {"id": workspace_id},
        ).first()
        if row is None:
            raise FileNotFoundError(f"current workspace does not exist: {workspace_id}")
        return _document(row[0])

    def _get_workspace_version(
        self, ctx: Connection, workspace_id: str, version_hash: str
    ) -> dict[str, Any]:
        safe_version_hash = _safe_version_hash(version_hash)
        row = ctx.execute(
            text(
                """
                SELECT v.document
                FROM workspace_versions v
                JOIN workspaces w ON w.id = v.workspace_id
                WHERE v.workspace_id = :id AND v.version_hash = :hash AND w.deleted_at IS NULL
                """
            ),
            {"id": workspace_id, "hash": safe_version_hash},
        ).first()
        if row is None:
            raise FileNotFoundError(
                f"workspace version does not exist: {workspace_id}/{safe_version_hash}"
            )
        return _document(row[0])

    def _save_workspace_version(
        self,
        ctx: Connection,
        workspace_id: str,
        workspace: dict[str, Any],
        *,
        actor: str,
        actor_type: str | None = None,
        actor_id: str | None = None,
        parent_version_hash: str | None,
        reason: str,
        agent_run_id: str | None = None,
        pipeline_run_id: str | None = None,
        expected_version_hash: str | None = None,
        owner_id: str | None = None,
    ) -> str:
        actor_fields = _actor_fields(actor_type or actor, actor_id)
        _lock_workspace(ctx, workspace_id)
        if pipeline_run_id:
            run = ctx.execute(
                text("SELECT status FROM pipeline_runs WHERE run_id = :run_id FOR UPDATE"),
                {"run_id": pipeline_run_id},
            ).first()
            if run is None:
                raise FileNotFoundError(f"pipeline run does not exist: {pipeline_run_id}")
            if run[0] != "running":
                raise RuntimeError("pipeline run no longer owns this workspace publication.")

        summary = workspace_summary(workspace_id, workspace)
        # Insert-if-absent first, then lock: two first publishes of the same
        # id both reach the row lock, and the second sees the first's head.
        ctx.execute(
            text(
                """
                INSERT INTO workspaces (id, owner_id, topic, title, topic_key)
                VALUES (:id, CAST(:owner_id AS uuid), :topic, :title, :topic_key)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": workspace_id,
                "owner_id": _uuid_or_none(owner_id),
                "topic": summary["topic"],
                "title": summary["title"],
                "topic_key": _topic_key(summary["topic"] or summary["title"]),
            },
        )
        row = ctx.execute(
            text(
                "SELECT current_version_hash, deleted_at FROM workspaces WHERE id = :id FOR UPDATE"
            ),
            {"id": workspace_id},
        ).first()
        assert row is not None
        current_hash, deleted_at = row[0], row[1]
        if deleted_at is not None:
            raise FileNotFoundError(f"workspace was deleted: {workspace_id}")
        if expected_version_hash is not None and current_hash != expected_version_hash:
            raise StaleVersionError(
                "workspace current version changed before publish: "
                f"expected {expected_version_hash}, found {current_hash}."
            )

        version_hash = summary["workspace_version_hash"]
        metadata = {
            "schema_version": "research_tree.workspace_version_metadata.v1",
            "workspace_id": workspace_id,
            "version_hash": version_hash,
            "parent_version_hash": parent_version_hash,
            "actor": actor,
            **actor_fields,
            "reason": reason,
            "agent_run_id": agent_run_id,
            "created_at": _now(),
        }
        # Content-addressed snapshots are immutable; a re-save keeps the
        # original metadata, as the file store keeps the original file.
        ctx.execute(
            text(
                """
                INSERT INTO workspace_versions (workspace_id, version_hash, document, metadata)
                VALUES (:id, :hash, CAST(:document AS json), CAST(:metadata AS jsonb))
                ON CONFLICT (workspace_id, version_hash) DO NOTHING
                """
            ),
            {
                "id": workspace_id,
                "hash": version_hash,
                "document": json.dumps(workspace),
                "metadata": json.dumps(metadata),
            },
        )
        self._update_head(ctx, workspace_id, summary)
        self._record_new_navigation_head(ctx, workspace_id, version_hash)
        return version_hash

    def _set_current_version(
        self, ctx: Connection, workspace_id: str, version_hash: str, workspace: dict[str, Any]
    ) -> None:
        _lock_workspace(ctx, workspace_id)
        self._update_head(ctx, workspace_id, workspace_summary(workspace_id, workspace))
        hashes, _current_index = self._navigation(ctx, workspace_id)
        if version_hash not in hashes:
            hashes.append(version_hash)
        current_index = max(index for index, item in enumerate(hashes) if item == version_hash)
        self._write_navigation(ctx, workspace_id, hashes, current_index)

    def _update_head(self, ctx: Connection, workspace_id: str, summary: Mapping[str, Any]) -> None:
        self._notify(ctx, WORKSPACES_CHANNEL)
        ctx.execute(
            text(
                """
                UPDATE workspaces
                SET current_version_hash = :hash,
                    topic = :topic,
                    title = :title,
                    topic_key = :topic_key,
                    paper_count = :paper_count,
                    branch_count = :branch_count,
                    paper_path_count = :paper_path_count,
                    document_updated_at = :document_updated_at,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": workspace_id,
                "hash": summary["workspace_version_hash"],
                "topic": summary["topic"],
                "title": summary["title"],
                "topic_key": _topic_key(summary["topic"] or summary["title"]),
                "paper_count": summary["paper_count"],
                "branch_count": summary["branch_count"],
                "paper_path_count": summary["paper_path_count"],
                "document_updated_at": summary["updated_at"],
            },
        )

    def _navigation(self, ctx: Connection, workspace_id: str) -> tuple[list[str], int]:
        row = ctx.execute(
            text(
                "SELECT version_hashes, current_index FROM workspace_navigation "
                "WHERE workspace_id = :id FOR UPDATE"
            ),
            {"id": workspace_id},
        ).first()
        if row is None:
            return [], 0
        hashes = [str(item) for item in (row[0] or [])]
        return hashes, int(row[1])

    def _write_navigation(
        self, ctx: Connection, workspace_id: str, hashes: list[str], current_index: int
    ) -> None:
        ctx.execute(
            text(
                """
                INSERT INTO workspace_navigation (workspace_id, version_hashes, current_index, updated_at)
                VALUES (:id, CAST(:hashes AS jsonb), :current_index, now())
                ON CONFLICT (workspace_id) DO UPDATE
                SET version_hashes = EXCLUDED.version_hashes,
                    current_index = EXCLUDED.current_index,
                    updated_at = now()
                """
            ),
            {"id": workspace_id, "hashes": json.dumps(hashes), "current_index": current_index},
        )

    def _record_new_navigation_head(
        self, ctx: Connection, workspace_id: str, version_hash: str
    ) -> None:
        hashes, current_index = self._navigation(ctx, workspace_id)
        hashes = hashes[: current_index + 1]
        if not hashes or hashes[-1] != version_hash:
            hashes.append(version_hash)
        self._write_navigation(ctx, workspace_id, hashes, len(hashes) - 1)

    def list_workspace_versions(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._engine.begin() as conn:
            navigation = conn.execute(
                text(
                    "SELECT n.version_hashes, n.current_index FROM workspace_navigation n "
                    "JOIN workspaces w ON w.id = n.workspace_id "
                    "WHERE n.workspace_id = :id AND w.deleted_at IS NULL"
                ),
                {"id": workspace_id},
            ).first()
            if navigation is None:
                return []
            rows = conn.execute(
                text(
                    "SELECT version_hash, metadata FROM workspace_versions WHERE workspace_id = :id"
                ),
                {"id": workspace_id},
            ).all()
        metadata_by_hash = {str(row[0]): dict(row[1]) for row in rows}
        return _versions_from_navigation(
            workspace_id,
            {
                "version_hashes": [str(item) for item in (navigation[0] or [])],
                "current_index": int(navigation[1]),
            },
            metadata_by_hash,
        )

    def delete_workspace(
        self,
        workspace_id: str,
        *,
        expected_version_hash: str | None = None,
    ) -> dict[str, Any]:
        """Soft-delete: the row stays (its id stays taken) but nothing reads it again."""

        with self._transaction(workspace_id) as conn:
            current = self._get_current_workspace(conn, workspace_id)
            current_hash = workspace_version_hash(current)
            if expected_version_hash and current_hash != expected_version_hash:
                raise ValueError(
                    "workspace changed before deletion; refresh and try again."
                )
            self._cancel_pipeline_runs(conn, workspace_id)
            conn.execute(
                text(
                    "UPDATE workspaces SET deleted_at = now(), updated_at = now() WHERE id = :id"
                ),
                {"id": workspace_id},
            )
            self._notify(conn, WORKSPACES_CHANNEL)
        return {
            "workspace_id": workspace_id,
            "workspace_version_hash": current_hash,
            "deleted": True,
            "deleted_at": _now(),
        }

    # ---- events -----------------------------------------------------------

    def _record_workspace_event(
        self, ctx: Connection, workspace_id: str, event: dict[str, Any]
    ) -> None:
        payload = event.get("payload")
        review_id = payload.get("review_id") if isinstance(payload, dict) else None
        ctx.execute(
            text(
                """
                INSERT INTO workspace_events (workspace_id, event_id, event_type, review_id, payload)
                VALUES (:workspace_id, :event_id, :event_type, :review_id, CAST(:payload AS jsonb))
                """
            ),
            {
                "workspace_id": workspace_id,
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "review_id": str(review_id) if review_id else None,
                "payload": json.dumps(event),
            },
        )

    def _record_agent_run_event(
        self, ctx: Connection, workspace_id: str, event: dict[str, Any]
    ) -> None:
        ctx.execute(
            text(
                """
                INSERT INTO agent_run_events (workspace_id, run_event_id, agent_run_id, status, payload)
                VALUES (:workspace_id, :run_event_id, :agent_run_id, :status, CAST(:payload AS jsonb))
                """
            ),
            {
                "workspace_id": workspace_id,
                "run_event_id": event["run_event_id"],
                "agent_run_id": str(event.get("agent_run_id") or ""),
                "status": str(event.get("status") or ""),
                "payload": json.dumps(event),
            },
        )

    def _existing_review_event_id(
        self, ctx: Connection, workspace_id: str, review_id: str, event_type: str
    ) -> str | None:
        row = ctx.execute(
            text(
                """
                SELECT event_id FROM workspace_events
                WHERE workspace_id = :workspace_id AND review_id = :review_id
                  AND event_type = :event_type
                ORDER BY id LIMIT 1
                """
            ),
            {"workspace_id": workspace_id, "review_id": review_id, "event_type": event_type},
        ).first()
        return str(row[0]) if row is not None else None

    def list_workspace_events(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT payload FROM workspace_events WHERE workspace_id = :id ORDER BY id"
                ),
                {"id": workspace_id},
            ).all()
        return [dict(row[0]) for row in rows]

    def list_agent_run_events(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT payload FROM agent_run_events WHERE workspace_id = :id ORDER BY id"
                ),
                {"id": workspace_id},
            ).all()
        return [dict(row[0]) for row in rows]

    # ---- reviews ----------------------------------------------------------

    def _get_review(self, ctx: Connection, workspace_id: str, review_id: str) -> dict[str, Any]:
        row = ctx.execute(
            text(
                "SELECT payload FROM reviews WHERE workspace_id = :workspace_id "
                "AND review_id = :review_id"
            ),
            {"workspace_id": workspace_id, "review_id": review_id},
        ).first()
        if row is None:
            raise FileNotFoundError(
                f"workspace review does not exist: {workspace_id}/{review_id}"
            )
        return _document(row[0])

    def _write_review(self, ctx: Connection, workspace_id: str, review: dict[str, Any]) -> None:
        ctx.execute(
            text(
                """
                INSERT INTO reviews (workspace_id, review_id, review_type, status, payload)
                VALUES (:workspace_id, :review_id, :review_type, :status, CAST(:payload AS json))
                ON CONFLICT (workspace_id, review_id) DO UPDATE
                SET review_type = EXCLUDED.review_type,
                    status = EXCLUDED.status,
                    payload = EXCLUDED.payload,
                    updated_at = now()
                """
            ),
            {
                "workspace_id": workspace_id,
                "review_id": str(review.get("review_id") or ""),
                "review_type": str(review.get("review_type") or "workspace_patch"),
                "status": str(review.get("status") or ""),
                "payload": json.dumps(review),
            },
        )

    def list_workspace_reviews(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                text("SELECT payload FROM reviews WHERE workspace_id = :id ORDER BY review_id"),
                {"id": workspace_id},
            ).all()
        return [_document(row[0]) for row in rows]

    # ---- pipeline runs ----------------------------------------------------

    def save_pipeline_run(self, pipeline_run: Mapping[str, Any]) -> None:
        with self._transaction() as conn:
            self._upsert_pipeline_run(conn, pipeline_run)

    def _upsert_pipeline_run(self, conn: Connection, pipeline_run: Mapping[str, Any]) -> None:
        run = dict(pipeline_run)
        run_id = str(run.get("run_id") or "")
        if not run_id:
            raise ValueError("pipeline run requires a run_id.")
        status = str(run.get("status") or "")
        try:
            conn.execute(
                text(
                    """
                    INSERT INTO pipeline_runs
                        (run_id, workspace_id, topic_key, owner_id, status, record,
                         celery_task_id, heartbeat_at)
                    VALUES
                        (:run_id, :workspace_id, :topic_key, CAST(:owner_id AS uuid), :status,
                         CAST(:record AS jsonb), :celery_task_id,
                         CASE WHEN :active THEN now() ELSE NULL END)
                    ON CONFLICT (run_id) DO UPDATE
                    SET workspace_id = EXCLUDED.workspace_id,
                        topic_key = EXCLUDED.topic_key,
                        owner_id = EXCLUDED.owner_id,
                        status = EXCLUDED.status,
                        record = EXCLUDED.record,
                        celery_task_id = COALESCE(EXCLUDED.celery_task_id, pipeline_runs.celery_task_id),
                        heartbeat_at = CASE WHEN EXCLUDED.status IN ('queued', 'running')
                                            THEN now() ELSE pipeline_runs.heartbeat_at END,
                        updated_at = now()
                    """
                ),
                {
                    "run_id": run_id,
                    "workspace_id": str(run["workspace_id"]) if run.get("workspace_id") else None,
                    "topic_key": _topic_key(str(run.get("topic") or "")),
                    "owner_id": _uuid_or_none(run.get("owner_id")),
                    "status": status,
                    "record": json.dumps(run),
                    "celery_task_id": run.get("celery_task_id"),
                    "active": status in ACTIVE_RUN_STATUSES,
                },
            )
        except IntegrityError as error:
            raise ValueError("a pipeline run for this workspace is already active.") from error
        self._notify(conn, f"{RUN_CHANNEL_PREFIX}{run_id}", WORKSPACES_CHANNEL)

    def reserve_new_workspace_run(self, pipeline_run: Mapping[str, Any]) -> None:
        """Atomically reject duplicate topics across active workspaces and jobs."""

        topic_key = _topic_key(str(pipeline_run.get("topic") or ""))
        if not topic_key:
            raise ValueError("pipeline run topic cannot be empty.")
        owner_id = _uuid_or_none(pipeline_run.get("owner_id"))
        with self._transaction() as conn:
            conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"topic:{owner_id or ''}:{topic_key}"},
            )
            self._reclaim_stale_runs(conn)
            taken = conn.execute(
                text(
                    """
                    SELECT 1 FROM workspaces
                    WHERE deleted_at IS NULL AND current_version_hash IS NOT NULL
                      AND topic_key = :topic_key
                      AND owner_id IS NOT DISTINCT FROM CAST(:owner_id AS uuid)
                    LIMIT 1
                    """
                ),
                {"topic_key": topic_key, "owner_id": owner_id},
            ).first()
            if taken is not None:
                raise ValueError("a workspace for this topic already exists.")
            building = conn.execute(
                text(
                    """
                    SELECT 1 FROM pipeline_runs
                    WHERE status IN ('queued', 'running') AND topic_key = :topic_key
                      AND owner_id IS NOT DISTINCT FROM CAST(:owner_id AS uuid)
                    LIMIT 1
                    """
                ),
                {"topic_key": topic_key, "owner_id": owner_id},
            ).first()
            if building is not None:
                raise ValueError("a workspace for this topic is already being built.")
            self._upsert_pipeline_run(conn, pipeline_run)

    def reserve_pipeline_rerun(self, pipeline_run: Mapping[str, Any]) -> None:
        """Reserve one active rerun per workspace without touching current data."""

        workspace_id = str(pipeline_run.get("workspace_id") or "")
        with self._transaction(workspace_id) as conn:
            self._reclaim_stale_runs(conn, workspace_id=workspace_id)
            active = conn.execute(
                text(
                    "SELECT 1 FROM pipeline_runs WHERE workspace_id = :id "
                    "AND status IN ('queued', 'running') LIMIT 1"
                ),
                {"id": workspace_id},
            ).first()
            if active is not None:
                raise ValueError("a pipeline run for this workspace is already active.")
            self._upsert_pipeline_run(conn, pipeline_run)

    def get_pipeline_run(self, run_id: str) -> dict[str, Any]:
        with self._transaction() as conn:
            self._reclaim_stale_runs(conn, run_id=run_id)
            row = conn.execute(
                text("SELECT record FROM pipeline_runs WHERE run_id = :run_id"),
                {"run_id": run_id},
            ).first()
        if row is None:
            raise FileNotFoundError(f"pipeline run does not exist: {run_id}")
        return dict(row[0])

    def list_pipeline_runs(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._transaction() as conn:
            self._reclaim_stale_runs(conn, workspace_id=workspace_id)
            rows = conn.execute(
                text(
                    "SELECT record FROM pipeline_runs WHERE workspace_id = :id "
                    "ORDER BY record->>'created_at' DESC, run_id"
                ),
                {"id": workspace_id},
            ).all()
        return [dict(row[0]) for row in rows]

    def cancel_pipeline_runs(self, workspace_id: str) -> list[str]:
        with self._transaction(workspace_id) as conn:
            return self._cancel_pipeline_runs(conn, workspace_id)

    def _cancel_pipeline_runs(self, conn: Connection, workspace_id: str) -> list[str]:
        now = _now()
        rows = conn.execute(
            text(
                """
                UPDATE pipeline_runs
                SET status = 'cancelled',
                    record = record || CAST(:patch AS jsonb),
                    updated_at = now()
                WHERE workspace_id = :id AND status IN ('queued', 'running')
                RETURNING run_id
                """
            ),
            {
                "id": workspace_id,
                "patch": json.dumps(
                    {
                        "status": "cancelled",
                        "current_stage": None,
                        "error": None,
                        "cancelled_at": now,
                        "updated_at": now,
                    }
                ),
            },
        ).all()
        cancelled = [str(row[0]) for row in rows]
        if cancelled:
            self._notify(conn, WORKSPACES_CHANNEL, *(f"{RUN_CHANNEL_PREFIX}{r}" for r in cancelled))
        return cancelled

    def touch_pipeline_run(self, run_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pipeline_runs SET heartbeat_at = now() "
                    "WHERE run_id = :run_id AND status IN ('queued', 'running')"
                ),
                {"run_id": run_id},
            )

    def _reclaim_stale_runs(
        self,
        conn: Connection,
        *,
        run_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        """Fail active runs nobody has touched in a while.

        A running run gets three minutes: the executor heartbeats every thirty
        seconds and every stage transition refreshes it. A queued run gets
        longer, since it may legitimately wait for a worker.
        """

        now = _now()
        scope = ""
        params: dict[str, Any] = {
            "patch": json.dumps(
                {
                    "status": "failed",
                    "current_stage": None,
                    "error": RECLAIM_ERROR,
                    "completed_at": now,
                    "updated_at": now,
                }
            )
        }
        if run_id is not None:
            scope = " AND run_id = :run_id"
            params["run_id"] = run_id
        elif workspace_id is not None:
            scope = " AND workspace_id = :workspace_id"
            params["workspace_id"] = workspace_id
        rows = conn.execute(
            text(
                f"""
                UPDATE pipeline_runs
                SET status = 'failed', record = record || CAST(:patch AS jsonb), updated_at = now()
                WHERE (
                    (status = 'running' AND heartbeat_at < now() - INTERVAL '{RUNNING_RECLAIM_AFTER}')
                    OR (status = 'queued' AND heartbeat_at < now() - INTERVAL '{QUEUED_RECLAIM_AFTER}')
                ){scope}
                RETURNING run_id
                """
            ),
            params,
        ).all()
        if rows:
            self._notify(conn, WORKSPACES_CHANNEL, *(f"{RUN_CHANNEL_PREFIX}{row[0]}" for row in rows))

    # ---- per-paper artifacts ---------------------------------------------

    def save_paper_content(
        self, workspace_id: str, paper_id: str, content: Mapping[str, Any]
    ) -> str:
        return self._put_paper_artifact("paper_content", workspace_id, paper_id, content)

    def get_paper_content(self, workspace_id: str, paper_id: str) -> dict[str, Any]:
        return self._get_paper_artifact("paper_content", workspace_id, paper_id)

    def save_paper_annotations(
        self, workspace_id: str, paper_id: str, annotations: Mapping[str, Any]
    ) -> str:
        return self._put_paper_artifact("annotations", workspace_id, paper_id, annotations)

    def get_paper_annotations(self, workspace_id: str, paper_id: str) -> dict[str, Any]:
        return self._get_paper_artifact("annotations", workspace_id, paper_id)

    def _put_paper_artifact(
        self, kind: str, workspace_id: str, paper_id: str, payload: Mapping[str, Any]
    ) -> str:
        content_key = _paper_content_key(paper_id)
        record = dict(payload)
        record["paper_id"] = paper_id
        self._artifacts.put(
            f"{kind}/{workspace_id}/{content_key}",
            json.dumps(record).encode("utf-8"),
        )
        return content_key

    def _get_paper_artifact(self, kind: str, workspace_id: str, paper_id: str) -> dict[str, Any]:
        key = f"{kind}/{workspace_id}/{_paper_content_key(paper_id)}"
        raw = self._artifacts.get(key)
        if raw is None:
            raise FileNotFoundError(key)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid paper artifact: {key}") from error
        if not isinstance(payload, dict) or payload.get("paper_id") != paper_id:
            raise ValueError(f"invalid paper artifact: {key}")
        return payload


def _lock_workspace(conn: Connection, workspace_id: str) -> None:
    # Transaction-scoped and re-entrant within the transaction, so nested
    # primitives can take it again without care.
    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"workspace:{workspace_id}"},
    )


def _document(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        loaded = json.loads(value)
        if isinstance(loaded, dict):
            return loaded
    raise ValueError("stored document is not a JSON object")


def _uuid_or_none(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None
