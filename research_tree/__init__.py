"""Development import shim for the src-layout package.

This lets `python -m research_tree...` work from a fresh repository checkout
without requiring `PYTHONPATH=src`. Editable installs still use `src/` directly.
"""

from __future__ import annotations

from pathlib import Path


_SRC_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "research_tree"
if _SRC_PACKAGE.is_dir():
    __path__.append(str(_SRC_PACKAGE))
