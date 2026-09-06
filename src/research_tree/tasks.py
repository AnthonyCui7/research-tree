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
    # A pipeline that is still running after 45 minutes is stuck; the soft
    # limit lands first so the run record can say so.
    task_time_limit=2700,
    task_soft_time_limit=2400,
    broker_transport_options={
        # Longer than the hard time limit, so a slow run is never handed to a
        # second worker while the first is still on it.
        "visibility_timeout": 7200,
        "global_keyprefix": "{research-tree}",
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
    install_log_scrubbing()


@lru_cache(maxsize=None)
def _repository():
    from research_tree.workspace.repository import build_workspace_repository

    return build_workspace_repository()


@app.task(name="research_tree.run_pipeline")
def run_pipeline(run_id: str) -> None:
    from research_tree.services.pipeline import WorkspacePipelineService

    WorkspacePipelineService(_repository(), repo_root=REPO_ROOT)._execute(run_id)


@app.task(name="research_tree.generate_annotations")
def generate_annotations(job_id: str) -> None:
    from research_tree.services.annotations import PaperAnnotationService

    PaperAnnotationService(_repository()).run_annotation_job(job_id)


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
