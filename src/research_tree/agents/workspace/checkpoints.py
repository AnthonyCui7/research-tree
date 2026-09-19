"""Conversation checkpoints that stay the size of one conversation.

A turn resumes from the newest checkpoint of its thread and from nothing
older, and a checkpoint carries the whole state: the workspace document, the
context built from it, a draft. The saver keeps every checkpoint it ever
wrote, so a thread grew by a few hundred kilobytes with each message and
nothing took any of it back. `prune` is the hook the base class declares for
this; the Postgres saver in this version leaves it unimplemented.
"""

from __future__ import annotations

from typing import Sequence

from langgraph.checkpoint.postgres import PostgresSaver


class PrunedPostgresSaver(PostgresSaver):
    def prune(self, thread_ids: Sequence[str], *, strategy: str = "keep_latest") -> None:
        if strategy == "delete":
            for thread_id in thread_ids:
                self.delete_thread(thread_id)
            return
        if strategy != "keep_latest":
            raise ValueError(f"unknown pruning strategy: {strategy}")
        # The graph uses no delta channels, so the newest checkpoint stands on
        # its own: it needs the blobs its `channel_versions` name and nothing
        # from its ancestors.
        with self._cursor() as cursor:
            for thread_id in thread_ids:
                cursor.execute(
                    """
                    DELETE FROM checkpoints old
                    WHERE old.thread_id = %s AND old.checkpoint_id < (
                        SELECT max(newest.checkpoint_id) FROM checkpoints newest
                        WHERE newest.thread_id = old.thread_id
                          AND newest.checkpoint_ns = old.checkpoint_ns
                    )
                    """,
                    (thread_id,),
                )
                cursor.execute(
                    """
                    DELETE FROM checkpoint_writes writes
                    WHERE writes.thread_id = %s AND NOT EXISTS (
                        SELECT 1 FROM checkpoints kept
                        WHERE kept.thread_id = writes.thread_id
                          AND kept.checkpoint_ns = writes.checkpoint_ns
                          AND kept.checkpoint_id = writes.checkpoint_id
                    )
                    """,
                    (thread_id,),
                )
                cursor.execute(
                    """
                    DELETE FROM checkpoint_blobs blobs
                    WHERE blobs.thread_id = %s AND NOT EXISTS (
                        SELECT 1
                        FROM checkpoints kept,
                             jsonb_each_text(kept.checkpoint -> 'channel_versions') AS named
                        WHERE kept.thread_id = blobs.thread_id
                          AND kept.checkpoint_ns = blobs.checkpoint_ns
                          AND named.key = blobs.channel
                          AND named.value = blobs.version
                    )
                    """,
                    (thread_id,),
                )

    def delete_threads_of(self, scope: str) -> None:
        """Delete every thread named `<scope>:<suffix>`, as a deleted workspace's are.

        The suffix holds no colon and a workspace id may, so `owner:a` does
        not reach into the threads of a workspace called `a:b`.
        """

        prefix = f"{scope}:"
        with self._cursor() as cursor:
            for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                cursor.execute(
                    f"DELETE FROM {table} WHERE starts_with(thread_id, %s) "
                    "AND position(':' in substr(thread_id, %s)) = 0",
                    (prefix, len(prefix) + 1),
                )
