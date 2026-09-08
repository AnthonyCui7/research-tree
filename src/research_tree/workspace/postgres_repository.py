"""The workspace repository on Postgres.

A repository is bound to one owner, and every row it touches carries that
owner: `(owner_id, id)` is the key of `workspaces` and of everything hanging
off one, so two accounts can each have a "prompting" and neither can reach the
other's however a query is written. `for_owner` binds another account to the
same engine; the API does that once per request from the signed-in principal.

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
(`research_tree:workspaces:{owner}` for the account's collection,
`research_tree:runs:{id}` for one run) after the transaction commits, so the
API's event streams can wake up instead of polling. A failed announcement is
logged and otherwise ignored.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from research_tree.artifact_store import ArtifactStore, FilesystemArtifactStore
from research_tree.principal import LOCAL_USER_ID
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.repository import (
    StaleVersionError,
    WorkspaceRepositoryBase,
    _actor_fields,
    _now,
    _paper_content_key,
    _safe_version_hash,
    normalized_topic_key,
    _id_candidates,
    _versions_from_navigation,
    order_workspace_summaries,
    workspace_summary,
)

logger = logging.getLogger("uvicorn.error")

ACTIVE_RUN_STATUSES = ("queued", "running")
WORKSPACES_CHANNEL_PREFIX = "research_tree:workspaces:"
RUN_CHANNEL_PREFIX = "research_tree:runs:"
RUNNING_RECLAIM_AFTER = "3 minutes"
QUEUED_RECLAIM_AFTER = "30 minutes"
RECLAIM_ERROR = "Pipeline worker stopped before this run completed."


def workspaces_channel(owner_id: str) -> str:
    """The Redis channel that announces changes to one account's collection."""

    return f"{WORKSPACES_CHANNEL_PREFIX}{owner_id}"


class PostgresWorkspaceRepository(WorkspaceRepositoryBase):
    def __init__(
        self,
        engine: Engine,
        *,
        owner_id: str = LOCAL_USER_ID,
        artifacts: ArtifactStore | None = None,
        redis: Any = None,
    ) -> None:
        self._engine = engine
        self.owner_id = owner_id
        self._artifacts = artifacts or FilesystemArtifactStore()
        self._redis = redis

    def for_owner(self, owner_id: str) -> "PostgresWorkspaceRepository":
        if owner_id == self.owner_id:
            return self
        return PostgresWorkspaceRepository(
            self._engine, owner_id=owner_id, artifacts=self._artifacts, redis=self._redis
        )

    @contextmanager
    def _transaction(self, workspace_id: str | None = None) -> Iterator[Connection]:
        channels: set[str] = set()
        with self._engine.begin() as conn:
            conn.info["notify"] = channels
            if workspace_id is not None:
                self._lock_workspace(conn, workspace_id)
            yield conn
        self._publish(channels)

    def _lock_workspace(self, conn: Connection, workspace_id: str) -> None:
        # Transaction-scoped and re-entrant within the transaction, so nested
        # primitives can take it again without care.
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"workspace:{self.owner_id}:{workspace_id}"},
        )

    @staticmethod
    def _notify(conn: Connection, *channels: str) -> None:
        pending = conn.info.get("notify")
        if pending is not None:
            pending.update(channels)

    def _notify_collection(self, conn: Connection, *run_ids: str) -> None:
        self._notify(
            conn,
            workspaces_channel(self.owner_id),
            *(f"{RUN_CHANNEL_PREFIX}{run_id}" for run_id in run_ids),
        )

    def _publish(self, channels: set[str]) -> None:
        if self._redis is None or not channels:
            return
        for channel in sorted(channels):
            try:
                self._redis.publish(channel, "1")
            except Exception as error:  # noqa: BLE001 - streams fall back to polling
                logger.warning("change notification failed on %s: %s", channel, error)
                return

    def _params(self, **values: Any) -> dict[str, Any]:
        return {"owner": self.owner_id, **values}

    # ---- workspaces -------------------------------------------------------

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, current_version_hash, title, topic, paper_count, branch_count,
                           paper_path_count, document_updated_at
                    FROM workspaces
                    WHERE owner_id = :owner AND deleted_at IS NULL
                      AND current_version_hash IS NOT NULL
                    """
                ),
                self._params(),
            ).mappings().all()
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
        return order_workspace_summaries(summaries)

    def claim_workspace_id(self, base_id: str) -> str:
        """Take the first free id for this topic, and hold it from now on.

        Choosing a name and then publishing under it minutes later meant two
        builds of the same topic were handed the same one, and whoever finished
        second wrote into the other's workspace. The name is taken here, in a
        single statement, so a second build is simply handed the next one.

        A name whose build ran and finished without ever publishing is free
        again, so a failed build does not burn the good name for ever. A name
        claimed seconds ago that has no run yet is not: that is a build about to
        start, not an abandoned one. A deleted workspace keeps its name, as it
        always has, so a rebuilt "prompting" becomes "prompting-2" rather than
        inheriting the deleted one's history.
        """

        with self._engine.begin() as conn:
            for candidate in _id_candidates(base_id):
                claimed = conn.execute(
                    text(
                        """
                        INSERT INTO workspaces (owner_id, id)
                        VALUES (:owner, :id)
                        ON CONFLICT (owner_id, id) DO UPDATE
                        SET updated_at = now()
                        WHERE workspaces.current_version_hash IS NULL
                          AND workspaces.deleted_at IS NULL
                          AND EXISTS (
                              SELECT 1 FROM pipeline_runs r
                              WHERE r.owner_id = workspaces.owner_id
                                AND r.workspace_id = workspaces.id
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM pipeline_runs r
                              WHERE r.owner_id = workspaces.owner_id
                                AND r.workspace_id = workspaces.id
                                AND r.status IN ('queued', 'running')
                          )
                        RETURNING id
                        """
                    ),
                    self._params(id=candidate),
                ).first()
                if claimed is not None:
                    return str(claimed[0])
        raise ValueError(f"no workspace id is available for {base_id!r}")

    def _get_current_workspace(self, ctx: Connection, workspace_id: str) -> dict[str, Any]:
        row = ctx.execute(
            text(
                """
                SELECT v.document
                FROM workspaces w
                JOIN workspace_versions v
                  ON v.owner_id = w.owner_id AND v.workspace_id = w.id
                 AND v.version_hash = w.current_version_hash
                WHERE w.owner_id = :owner AND w.id = :id AND w.deleted_at IS NULL
                """
            ),
            self._params(id=workspace_id),
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
                JOIN workspaces w ON w.owner_id = v.owner_id AND w.id = v.workspace_id
                WHERE v.owner_id = :owner AND v.workspace_id = :id AND v.version_hash = :hash
                  AND w.deleted_at IS NULL
                """
            ),
            self._params(id=workspace_id, hash=safe_version_hash),
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
    ) -> str:
        actor_fields = _actor_fields(actor_type or actor, actor_id)
        self._lock_workspace(ctx, workspace_id)
        if pipeline_run_id:
            run = ctx.execute(
                text(
                    "SELECT status FROM pipeline_runs "
                    "WHERE owner_id = :owner AND run_id = :run_id FOR UPDATE"
                ),
                self._params(run_id=pipeline_run_id),
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
                INSERT INTO workspaces (owner_id, id, topic, title, topic_key)
                VALUES (:owner, :id, :topic, :title, :topic_key)
                ON CONFLICT (owner_id, id) DO NOTHING
                """
            ),
            self._params(
                id=workspace_id,
                topic=summary["topic"],
                title=summary["title"],
                topic_key=normalized_topic_key(summary["topic"] or summary["title"]),
            ),
        )
        row = ctx.execute(
            text(
                "SELECT current_version_hash, deleted_at FROM workspaces "
                "WHERE owner_id = :owner AND id = :id FOR UPDATE"
            ),
            self._params(id=workspace_id),
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
                INSERT INTO workspace_versions
                    (owner_id, workspace_id, version_hash, document, metadata)
                VALUES (:owner, :id, :hash, CAST(:document AS json), CAST(:metadata AS jsonb))
                ON CONFLICT (owner_id, workspace_id, version_hash) DO NOTHING
                """
            ),
            self._params(
                id=workspace_id,
                hash=version_hash,
                document=json.dumps(workspace),
                metadata=json.dumps(metadata),
            ),
        )
        self._update_head(ctx, workspace_id, summary)
        self._record_new_navigation_head(ctx, workspace_id, version_hash)
        return version_hash

    def _set_current_version(
        self, ctx: Connection, workspace_id: str, version_hash: str, workspace: dict[str, Any]
    ) -> None:
        self._lock_workspace(ctx, workspace_id)
        self._update_head(ctx, workspace_id, workspace_summary(workspace_id, workspace))
        hashes, _current_index = self._navigation(ctx, workspace_id)
        if version_hash not in hashes:
            hashes.append(version_hash)
        current_index = max(index for index, item in enumerate(hashes) if item == version_hash)
        self._write_navigation(ctx, workspace_id, hashes, current_index)

    def _update_head(self, ctx: Connection, workspace_id: str, summary: Mapping[str, Any]) -> None:
        self._notify_collection(ctx)
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
                WHERE owner_id = :owner AND id = :id
                """
            ),
            self._params(
                id=workspace_id,
                hash=summary["workspace_version_hash"],
                topic=summary["topic"],
                title=summary["title"],
                topic_key=normalized_topic_key(summary["topic"] or summary["title"]),
                paper_count=summary["paper_count"],
                branch_count=summary["branch_count"],
                paper_path_count=summary["paper_path_count"],
                document_updated_at=summary["updated_at"],
            ),
        )

    def _navigation(self, ctx: Connection, workspace_id: str) -> tuple[list[str], int]:
        row = ctx.execute(
            text(
                "SELECT version_hashes, current_index FROM workspace_navigation "
                "WHERE owner_id = :owner AND workspace_id = :id FOR UPDATE"
            ),
            self._params(id=workspace_id),
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
                INSERT INTO workspace_navigation
                    (owner_id, workspace_id, version_hashes, current_index, updated_at)
                VALUES (:owner, :id, CAST(:hashes AS jsonb), :current_index, now())
                ON CONFLICT (owner_id, workspace_id) DO UPDATE
                SET version_hashes = EXCLUDED.version_hashes,
                    current_index = EXCLUDED.current_index,
                    updated_at = now()
                """
            ),
            self._params(id=workspace_id, hashes=json.dumps(hashes), current_index=current_index),
        )

    def _record_new_navigation_head(
        self, ctx: Connection, workspace_id: str, version_hash: str
    ) -> None:
        hashes, current_index = self._navigation(ctx, workspace_id)
        hashes = hashes[: current_index + 1]
        if version_hash in hashes:
            # Versions are content-addressed, so saving a document identical to
            # one already in this history is a move back to it. Appending would
            # list the same row a second time, under the author and the reason
            # of the first time it was written - an undo shown as a fresh build
            # by the pipeline, and two entries the reader cannot tell apart.
            self._write_navigation(ctx, workspace_id, hashes, hashes.index(version_hash))
            return
        hashes.append(version_hash)
        self._write_navigation(ctx, workspace_id, hashes, len(hashes) - 1)

    def list_workspace_versions(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._engine.begin() as conn:
            navigation = conn.execute(
                text(
                    """
                    SELECT n.version_hashes, n.current_index FROM workspace_navigation n
                    JOIN workspaces w ON w.owner_id = n.owner_id AND w.id = n.workspace_id
                    WHERE n.owner_id = :owner AND n.workspace_id = :id AND w.deleted_at IS NULL
                    """
                ),
                self._params(id=workspace_id),
            ).first()
            if navigation is None:
                return []
            rows = conn.execute(
                text(
                    "SELECT version_hash, metadata FROM workspace_versions "
                    "WHERE owner_id = :owner AND workspace_id = :id"
                ),
                self._params(id=workspace_id),
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
                    "UPDATE workspaces SET deleted_at = now(), updated_at = now() "
                    "WHERE owner_id = :owner AND id = :id"
                ),
                self._params(id=workspace_id),
            )
            self._notify_collection(conn)
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
                INSERT INTO workspace_events
                    (owner_id, workspace_id, event_id, event_type, review_id, payload)
                VALUES (:owner, :workspace_id, :event_id, :event_type, :review_id,
                        CAST(:payload AS jsonb))
                """
            ),
            self._params(
                workspace_id=workspace_id,
                event_id=event["event_id"],
                event_type=event["event_type"],
                review_id=str(review_id) if review_id else None,
                payload=json.dumps(event),
            ),
        )

    def _record_agent_run_event(
        self, ctx: Connection, workspace_id: str, event: dict[str, Any]
    ) -> None:
        ctx.execute(
            text(
                """
                INSERT INTO agent_run_events
                    (owner_id, workspace_id, run_event_id, agent_run_id, status, payload)
                VALUES (:owner, :workspace_id, :run_event_id, :agent_run_id, :status,
                        CAST(:payload AS jsonb))
                """
            ),
            self._params(
                workspace_id=workspace_id,
                run_event_id=event["run_event_id"],
                agent_run_id=str(event.get("agent_run_id") or ""),
                status=str(event.get("status") or ""),
                payload=json.dumps(event),
            ),
        )

    def _existing_review_event_id(
        self, ctx: Connection, workspace_id: str, review_id: str, event_type: str
    ) -> str | None:
        row = ctx.execute(
            text(
                """
                SELECT event_id FROM workspace_events
                WHERE owner_id = :owner AND workspace_id = :workspace_id
                  AND review_id = :review_id AND event_type = :event_type
                ORDER BY id LIMIT 1
                """
            ),
            self._params(workspace_id=workspace_id, review_id=review_id, event_type=event_type),
        ).first()
        return str(row[0]) if row is not None else None

    def list_workspace_events(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT payload FROM workspace_events "
                    "WHERE owner_id = :owner AND workspace_id = :id ORDER BY id"
                ),
                self._params(id=workspace_id),
            ).all()
        return [dict(row[0]) for row in rows]

    def list_agent_run_events(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT payload FROM agent_run_events "
                    "WHERE owner_id = :owner AND workspace_id = :id ORDER BY id"
                ),
                self._params(id=workspace_id),
            ).all()
        return [dict(row[0]) for row in rows]

    # ---- reviews ----------------------------------------------------------

    def _get_review(self, ctx: Connection, workspace_id: str, review_id: str) -> dict[str, Any]:
        row = ctx.execute(
            text(
                "SELECT payload FROM reviews WHERE owner_id = :owner "
                "AND workspace_id = :workspace_id AND review_id = :review_id"
            ),
            self._params(workspace_id=workspace_id, review_id=review_id),
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
                INSERT INTO reviews (owner_id, workspace_id, review_id, review_type, status, payload)
                VALUES (:owner, :workspace_id, :review_id, :review_type, :status,
                        CAST(:payload AS json))
                ON CONFLICT (owner_id, workspace_id, review_id) DO UPDATE
                SET review_type = EXCLUDED.review_type,
                    status = EXCLUDED.status,
                    payload = EXCLUDED.payload,
                    updated_at = now()
                """
            ),
            self._params(
                workspace_id=workspace_id,
                review_id=str(review.get("review_id") or ""),
                review_type=str(review.get("review_type") or "workspace_patch"),
                status=str(review.get("status") or ""),
                payload=json.dumps(review),
            ),
        )

    def list_workspace_reviews(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT payload FROM reviews WHERE owner_id = :owner AND workspace_id = :id "
                    "ORDER BY review_id"
                ),
                self._params(id=workspace_id),
            ).all()
        return [_document(row[0]) for row in rows]

    # ---- pipeline runs ----------------------------------------------------

    def save_pipeline_run(self, pipeline_run: Mapping[str, Any]) -> None:
        with self._transaction() as conn:
            self._upsert_pipeline_run(conn, pipeline_run)

    def _upsert_pipeline_run(self, conn: Connection, pipeline_run: Mapping[str, Any]) -> None:
        """Write a run record, except over a cancelled one.

        The executor holds the run in memory for the length of a stage and
        writes it back at the end. A cancel landing during that stage was
        overwritten by the write that followed it, and the next stage's check
        then read the resurrected `running` and carried on to completion. A
        cancelled run is the end of that run.
        """

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
                        (run_id, owner_id, workspace_id, topic_key, status, record,
                         celery_task_id, heartbeat_at)
                    VALUES
                        (:run_id, :owner, :workspace_id, :topic_key, :status,
                         CAST(:record AS jsonb), :celery_task_id,
                         CASE WHEN :active THEN now() ELSE NULL END)
                    ON CONFLICT (run_id) DO UPDATE
                    SET workspace_id = EXCLUDED.workspace_id,
                        topic_key = EXCLUDED.topic_key,
                        status = EXCLUDED.status,
                        record = EXCLUDED.record,
                        celery_task_id = COALESCE(EXCLUDED.celery_task_id, pipeline_runs.celery_task_id),
                        heartbeat_at = CASE WHEN EXCLUDED.status IN ('queued', 'running')
                                            THEN now() ELSE pipeline_runs.heartbeat_at END,
                        updated_at = now()
                    WHERE pipeline_runs.owner_id = EXCLUDED.owner_id
                      AND pipeline_runs.status <> 'cancelled'
                    """
                ),
                self._params(
                    run_id=run_id,
                    workspace_id=str(run["workspace_id"]) if run.get("workspace_id") else None,
                    topic_key=normalized_topic_key(str(run.get("topic") or "")),
                    status=status,
                    record=json.dumps(run),
                    celery_task_id=run.get("celery_task_id"),
                    active=status in ACTIVE_RUN_STATUSES,
                ),
            )
        except IntegrityError as error:
            raise ValueError("a pipeline run for this workspace is already active.") from error
        self._notify_collection(conn, run_id)

    def reserve_new_workspace_run(self, pipeline_run: Mapping[str, Any]) -> None:
        """Atomically reject duplicate topics across this account's workspaces and jobs."""

        topic_key = normalized_topic_key(str(pipeline_run.get("topic") or ""))
        if not topic_key:
            raise ValueError("pipeline run topic cannot be empty.")
        with self._transaction() as conn:
            conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"topic:{self.owner_id}:{topic_key}"},
            )
            self._reclaim_stale_runs(conn)
            taken = conn.execute(
                text(
                    """
                    SELECT 1 FROM workspaces
                    WHERE owner_id = :owner AND deleted_at IS NULL
                      AND current_version_hash IS NOT NULL AND topic_key = :topic_key
                    LIMIT 1
                    """
                ),
                self._params(topic_key=topic_key),
            ).first()
            if taken is not None:
                raise ValueError("a workspace for this topic already exists.")
            building = conn.execute(
                text(
                    """
                    SELECT 1 FROM pipeline_runs
                    WHERE owner_id = :owner AND status IN ('queued', 'running')
                      AND topic_key = :topic_key
                    LIMIT 1
                    """
                ),
                self._params(topic_key=topic_key),
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
                    "SELECT 1 FROM pipeline_runs WHERE owner_id = :owner AND workspace_id = :id "
                    "AND status IN ('queued', 'running') LIMIT 1"
                ),
                self._params(id=workspace_id),
            ).first()
            if active is not None:
                raise ValueError("a pipeline run for this workspace is already active.")
            self._upsert_pipeline_run(conn, pipeline_run)

    def get_pipeline_run(self, run_id: str) -> dict[str, Any]:
        with self._transaction() as conn:
            self._reclaim_stale_runs(conn, run_id=run_id)
            row = conn.execute(
                text("SELECT record FROM pipeline_runs WHERE owner_id = :owner AND run_id = :run_id"),
                self._params(run_id=run_id),
            ).first()
        if row is None:
            raise FileNotFoundError(f"pipeline run does not exist: {run_id}")
        return dict(row[0])

    def list_pipeline_runs(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._transaction() as conn:
            self._reclaim_stale_runs(conn, workspace_id=workspace_id)
            rows = conn.execute(
                text(
                    "SELECT record FROM pipeline_runs WHERE owner_id = :owner AND workspace_id = :id "
                    "ORDER BY record->>'created_at' DESC, run_id"
                ),
                self._params(id=workspace_id),
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
                WHERE owner_id = :owner AND workspace_id = :id AND status IN ('queued', 'running')
                RETURNING run_id
                """
            ),
            self._params(
                id=workspace_id,
                patch=json.dumps(
                    {
                        "status": "cancelled",
                        "current_stage": None,
                        "error": None,
                        "cancelled_at": now,
                        "updated_at": now,
                    }
                ),
            ),
        ).all()
        cancelled = [str(row[0]) for row in rows]
        if cancelled:
            self._notify_collection(conn, *cancelled)
        return cancelled

    def touch_pipeline_run(self, run_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pipeline_runs SET heartbeat_at = now() "
                    "WHERE owner_id = :owner AND run_id = :run_id AND status IN ('queued', 'running')"
                ),
                self._params(run_id=run_id),
            )

    def _reclaim_stale_runs(
        self,
        conn: Connection,
        *,
        run_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        """Fail this account's active runs nobody has touched in a while.

        A running run gets three minutes: the executor heartbeats every thirty
        seconds and every stage transition refreshes it. A queued run gets
        longer, since it may legitimately wait for a worker.
        """

        now = _now()
        scope = ""
        params = self._params(
            patch=json.dumps(
                {
                    "status": "failed",
                    "current_stage": None,
                    "error": RECLAIM_ERROR,
                    "completed_at": now,
                    "updated_at": now,
                }
            )
        )
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
                WHERE owner_id = :owner AND (
                    (status = 'running' AND heartbeat_at < now() - INTERVAL '{RUNNING_RECLAIM_AFTER}')
                    OR (status = 'queued' AND heartbeat_at < now() - INTERVAL '{QUEUED_RECLAIM_AFTER}')
                ){scope}
                RETURNING run_id
                """
            ),
            params,
        ).all()
        if rows:
            self._notify_collection(conn, *(str(row[0]) for row in rows))

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

    def _artifact_key(self, kind: str, workspace_id: str, paper_id: str) -> str:
        return f"{kind}/{self.owner_id}/{workspace_id}/{_paper_content_key(paper_id)}"

    def _put_paper_artifact(
        self, kind: str, workspace_id: str, paper_id: str, payload: Mapping[str, Any]
    ) -> str:
        record = dict(payload)
        record["paper_id"] = paper_id
        self._artifacts.put(
            self._artifact_key(kind, workspace_id, paper_id),
            json.dumps(record).encode("utf-8"),
        )
        return _paper_content_key(paper_id)

    def _get_paper_artifact(self, kind: str, workspace_id: str, paper_id: str) -> dict[str, Any]:
        key = self._artifact_key(kind, workspace_id, paper_id)
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


def _document(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        loaded = json.loads(value)
        if isinstance(loaded, dict):
            return loaded
    raise ValueError("stored document is not a JSON object")
