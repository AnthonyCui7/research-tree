"""The Celery application: what the worker container runs.

Redis is the broker only; a run's status lives in its Postgres row, so no
result backend is needed. The worker runs the same image as the API with

    celery -A research_tree.tasks worker -B --concurrency=2

where `-B` embeds the beat scheduler (one worker replica, so one beat). Beat
keeps the free-plan database from pausing and takes the weekly backup.

Azure Managed Redis shards keys by slot and refuses multi-key commands
across slots; the `{research-tree}` hash tag on every kombu key keeps the
queue, its unacked set, and its index together.
"""

from __future__ import annotations

import logging
import ssl
import sys
from functools import lru_cache
from pathlib import Path

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging

from research_tree.redis_client import redis_url

logger = logging.getLogger("uvicorn.error")
REPO_ROOT = Path(__file__).resolve().parents[2]

app = Celery("research_tree", broker=redis_url() or "memory://")
app.conf.update(
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # A build loads the embedding models into its process (about 1.3 GB
    # resident, measured Sep 2026). Recycling the child after every task hands
    # that memory back, so the second child stays small for annotation jobs.
    worker_max_tasks_per_child=1,
    # A build that is still running after an hour is stuck; the soft limit
    # lands first so the run record can say so.
    task_time_limit=3900,
    task_soft_time_limit=3600,
    broker_transport_options={
        # Longer than the hard time limit, so a slow run is never handed to a
        # second worker while the first is still on it.
        "visibility_timeout": 7200,
        "global_keyprefix": "{research-tree}",
        # Managed Redis closes connections that sit idle; the worker's
        # subscription sat idle between builds and was found closed, with a
        # traceback, once or twice a week. Keepalives and a periodic ping
        # keep it open instead.
        "socket_keepalive": True,
        "health_check_interval": 30,
    },
    broker_use_ssl=(
        {"ssl_cert_reqs": ssl.CERT_REQUIRED} if (redis_url() or "").startswith("rediss://") else None
    ),
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,
    timezone="UTC",
    beat_schedule={
        "keep-database-awake": {
            "task": "research_tree.keep_database_awake",
            "schedule": crontab(hour=3, minute=0),
        },
        "backup-database": {
            "task": "research_tree.backup_database",
            "schedule": crontab(day_of_week="sunday", hour=4, minute=0),
        },
    },
)


@setup_logging.connect
def _configure_logging(**kwargs) -> None:
    # Celery is told not to touch the root logger, so this is the worker's
    # whole logging setup: everything to stdout (Log Analytics collects it),
    # with the same secret scrubbing the API applies.
    from research_tree.log_scrub import install_log_scrubbing

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
        force=True,
    )
    # The Azure SDK narrates every Blob request at INFO; a build makes hundreds.
    logging.getLogger("azure").setLevel(logging.WARNING)
    install_log_scrubbing()


@lru_cache(maxsize=None)
def _repository():
    from research_tree.workspace.repository import build_workspace_repository

    return build_workspace_repository()


# One build at a time per worker: two builds' models do not fit in the
# container together. A build that finds the slot taken waits on the queue,
# touching its run so the queued-run reclaimer knows it is alive.
BUILD_SLOT_KEY = "pipeline:build_slot"
BUILD_SLOT_TTL_SECONDS = 4000
BUILD_SLOT_RETRY_SECONDS = 30


@app.task(name="research_tree.run_pipeline", bind=True, max_retries=None)
def run_pipeline(self, owner_id: str, run_id: str) -> None:
    # The run is the account's, so the repository it executes through is
    # bound to that account before anything is read.
    from research_tree.redis_client import get_redis
    from research_tree.services.pipeline import WorkspacePipelineService

    repository = _repository().for_owner(owner_id)
    redis = get_redis()
    if redis is not None and not redis.set(BUILD_SLOT_KEY, run_id, nx=True, ex=BUILD_SLOT_TTL_SECONDS):
        holder = redis.get(BUILD_SLOT_KEY)
        if holder != run_id.encode("utf-8"):
            logger.info("build %s waits for the slot held by %s", run_id, holder)
            repository.touch_pipeline_run(run_id)
            raise self.retry(countdown=BUILD_SLOT_RETRY_SECONDS)
    try:
        WorkspacePipelineService(repository, repo_root=REPO_ROOT)._execute(run_id)
    finally:
        if redis is not None:
            _release_build_slot(redis, run_id)


def _release_build_slot(redis, run_id: str) -> None:
    try:
        if redis.get(BUILD_SLOT_KEY) == run_id.encode("utf-8"):
            redis.delete(BUILD_SLOT_KEY)
    except Exception as error:  # noqa: BLE001 - the slot expires on its own
        logger.warning("could not release the build slot: %s", error)


@app.task(name="research_tree.generate_annotations")
def generate_annotations(job_id: str) -> None:
    from research_tree.services.annotations import PaperAnnotationService, load_annotation_job

    job = load_annotation_job(job_id)
    if job is None:
        logger.info("annotation job %s expired before it ran", job_id)
        return
    PaperAnnotationService(_repository().for_owner(job["owner_id"])).run_annotation_job(job)


@app.task(name="research_tree.keep_database_awake")
def keep_database_awake() -> None:
    from sqlalchemy import text

    from research_tree.db import get_engine

    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO service_heartbeat (id, touched_at) VALUES ('worker', now()) "
                "ON CONFLICT (id) DO UPDATE SET touched_at = now()"
            )
        )
    logger.info("database keep-alive recorded")


@app.task(name="research_tree.backup_database")
def backup_database() -> None:
    from research_tree.cli.backup_database import main

    status = main([])
    if status != 0:
        raise RuntimeError(f"backup exited with status {status}")
