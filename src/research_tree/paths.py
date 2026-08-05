"""Where the product writes at runtime.

Everything the app produces — workspaces, pipeline artifacts, provider caches,
logs — lives under one root so a deployment can point at a volume by setting a
single environment variable. `experiments/` is for exploratory scripts and is
not read or written by the product.
"""

from __future__ import annotations

import os
from pathlib import Path


DATA_ROOT_ENV = "RESEARCH_TREE_DATA_DIR"
DEFAULT_DATA_ROOT = Path("data")


def data_root() -> Path:
    return Path(os.environ.get(DATA_ROOT_ENV, str(DEFAULT_DATA_ROOT))).expanduser()


def workspaces_dir() -> Path:
    return data_root() / "workspaces"


def pipeline_runs_dir() -> Path:
    return data_root() / "pipeline_runs"


def cache_dir() -> Path:
    return data_root() / "cache"


def semantic_scholar_cache_dir() -> Path:
    return cache_dir() / "semantic_scholar"
