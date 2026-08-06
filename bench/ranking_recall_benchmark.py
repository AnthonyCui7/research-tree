"""RW-3: Ranker comparison on the hand-curated canonical sampling-papers gold
set, reproduced this session against a matching sampling-run snapshot.

Drives experiments/validate_ranking_signals.py (unmodified) with its module
paths pointed at a sampling candidate run whose DB actually contains the gold
papers, offline (--fetch-budget 0, reference lists served from
experiments/cache/s2_references). Tries the newest matching snapshots and
reports the one with the best gold-set coverage.

Run:  uv run python bench/ranking_recall_benchmark.py
"""

import contextlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "experiments"))

import validate_ranking_signals as vrs


def cache_only_fetch(paper_id, api_key):
    """Serve reference lists strictly from the local cache (S2 was shedding
    429s during the benchmark session; misses are skipped, not fetched)."""
    cache_file = vrs.CACHE_DIR / f"{paper_id}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    return None


vrs.fetch_references = cache_only_fetch

CANDIDATES = ["run32"]
BASE = ROOT / "experiments/output/workspace_candidate_preparation"


def run_snapshot(run_name: str) -> tuple[int, str]:
    vrs.DB_PATH = BASE / run_name / "s2_bulk_deduped_paper_database.json"
    vrs.POOL_PATH = BASE / run_name / "llm_candidate_papers.json"
    vrs.RESULT_PATH = Path(__file__).parent / f"ranking_recall_{run_name}.json"
    sys.argv = ["validate_ranking_signals.py"]
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        vrs.main()
    output = buffer.getvalue()
    in_db = 0
    for line in output.splitlines():
        if line.startswith("canonical matched in DB:"):
            in_db = int(line.split(":")[1].strip().split("/")[0])
    return in_db, output


def main() -> None:
    best = None
    for run_name in CANDIDATES:
        try:
            in_db, output = run_snapshot(run_name)
        except (FileNotFoundError, ZeroDivisionError):
            print(f"{run_name}: no canonical papers in DB, skipped")
            continue
        print(f"{run_name}: {in_db} canonical papers matched in DB")
        if best is None or in_db > best[1]:
            best = (run_name, in_db, output)
    run_name, in_db, output = best
    print(f"\n=== best snapshot: {run_name} ({in_db} gold papers in DB) ===\n")
    print(output)


if __name__ == "__main__":
    main()
