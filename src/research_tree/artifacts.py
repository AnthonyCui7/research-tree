from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def archive_existing_file(
    path: Path,
    *,
    run_label: str | None = None,
    now: datetime | None = None,
) -> Path | None:
    if not path.is_file():
        return None

    archived_at = now or datetime.now(UTC)
    timestamp = archived_at.strftime("%Y%m%dT%H%M%SZ")
    label = _safe_label(run_label or "previous")
    archive_dir = path.parent / "previous_versions"
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / f"{path.stem}__{timestamp}__{label}{path.suffix}"
    counter = 2
    while destination.exists():
        destination = (
            archive_dir / f"{path.stem}__{timestamp}__{label}__{counter}{path.suffix}"
        )
        counter += 1

    path.rename(destination)
    return destination


def write_json_file(
    path: Path,
    payload: Any,
    *,
    archive_existing: bool = False,
    run_label: str | None = None,
) -> Path | None:
    archived_path = None
    path.parent.mkdir(parents=True, exist_ok=True)
    if archive_existing:
        archived_path = archive_existing_file(path, run_label=run_label)
    _atomic_write(path, json.dumps(payload, indent=2))
    return archived_path


def write_text_file(
    path: Path,
    payload: str,
    *,
    archive_existing: bool = False,
    run_label: str | None = None,
) -> Path | None:
    archived_path = None
    path.parent.mkdir(parents=True, exist_ok=True)
    if archive_existing:
        archived_path = archive_existing_file(path, run_label=run_label)
    _atomic_write(path, payload)
    return archived_path


def _safe_label(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "previous"


def _atomic_write(path: Path, payload: str) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(payload, encoding="utf-8")
    temporary_path.replace(path)
