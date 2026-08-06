"""RW-1 (redo, no cache server): direct cost of avoidable S2 calls.

Measures live, through the production SemanticScholarClient:
  1. Batch reference fetch for run12's actual 202-paper root set (the real
     production request), 3 trials -> seconds per call and amortized
     seconds per reference lookup.
  2. Single-paper details calls (12) -> p50/p95 per call, clean-vs-retry
     breakdown recorded per call this time.
Then combines with the recorded 53-run workload replay
(bench/s2_cache_overlap_results.json) to report avoidable lookups and
avoidable wall-clock seconds (cache hit treated as ~0 ms), overall and for
the cross-topic-only floor. Finally parses the recorded ranking-benchmark
report for the fetch-vs-compute split of the base-set build stage.

Run:  uv run python bench/s2_avoidable_io_benchmark.py
"""

import json
import random
import re
import statistics
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = 42


def load_env() -> dict[str, str]:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def main() -> None:
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from research_tree.retrieval.semantic_scholar import SemanticScholarClient

    env = load_env()
    refs = json.loads(
        (ROOT / "data/candidate_runs/run12/citation_graph_edges.json").read_text()
    )["references"]
    root_ids = sorted(refs)

    call_times: list[dict] = []

    def make_client():
        scratch = Path(tempfile.mkdtemp(prefix="s2_bench2_"))
        client = SemanticScholarClient(
            cache_dir=scratch, api_key=env.get("S2_API_KEY"), max_retries=4
        )
        inner = client.client._request_with_retries

        def timed(method, url, body):
            start = time.perf_counter()
            result = inner(method, url, body)
            call_times.append(
                {"seconds": round(time.perf_counter() - start, 3), "url": url.split("?")[0]}
            )
            return result

        client.client._request_with_retries = timed
        return client

    # 1. batch reference fetch, 3 trials (fresh cache dir each -> live call)
    batch_trials = []
    for _ in range(3):
        client = make_client()
        call_times.clear()
        start = time.perf_counter()
        warnings: list[str] = []
        result = client.get_references_batch(root_ids, warnings)
        elapsed = time.perf_counter() - start
        got = sum(1 for v in result.values() if v) if isinstance(result, dict) else len(result)
        batch_trials.append(
            {
                "wall_seconds": round(elapsed, 1),
                "http_calls": len(call_times),
                "reference_lists_returned": got,
                "warnings": len(warnings),
            }
        )

    batch_median = statistics.median(t["wall_seconds"] for t in batch_trials)
    per_lookup_s = batch_median / len(root_ids)

    # 2. single-paper details calls
    paper_ids = random.Random(SEED).sample(root_ids, 12)
    client = make_client()
    call_times.clear()
    for pid in paper_ids:
        client.get_paper_details([pid], warnings=None)
    singles = [c["seconds"] for c in call_times]
    singles_sorted = sorted(singles)

    # 3. avoidable-cost math from the recorded workload replay
    overlap = json.loads((Path(__file__).parent / "s2_cache_overlap_results.json").read_text())
    totals = overlap["totals"]
    repeats = totals["repeat_lookups"]
    lookups = totals["reference_lookups_all_runs"]
    cross_topic = totals["cross_topic_repeat_lookups"]

    avoidable = {
        "repeat_lookups": repeats,
        "total_lookups": lookups,
        "repeat_rate": totals["repeat_rate_overall"],
        "avoidable_seconds_at_batch_rate": round(repeats * per_lookup_s, 1),
        "cross_topic_only": {
            "repeat_lookups": cross_topic,
            "avoidable_seconds_at_batch_rate": round(cross_topic * per_lookup_s, 1),
        },
        "assumption": "cache hit ~0 ms; per-lookup cost amortized from measured batch call",
    }

    # 4. fetch vs compute split from the recorded ranking benchmark report
    report = (ROOT / "experiments/output/ranking_benchmark/base_set_comparison.md").read_text()
    pairs = re.findall(r"fetch=([\d.]+)s hits=([\d.]+)ms", report)
    live_pairs = [(float(f), float(h) / 1000) for f, h in pairs if float(f) > 0]
    io_share = [f / (f + h) for f, h in live_pairs]

    result = {
        "experiment": "Avoidable S2 I/O cost, measured directly (no cache server)",
        "batch_reference_fetch": {
            "paper_ids_per_call": len(root_ids),
            "trials": batch_trials,
            "median_wall_seconds": batch_median,
            "amortized_ms_per_lookup": round(per_lookup_s * 1000, 1),
        },
        "single_details_call": {
            "n": len(singles),
            "p50_s": round(statistics.median(singles), 2),
            "p95_s": round(singles_sorted[max(0, int(len(singles) * 0.95) - 1)], 2),
            "all_seconds": singles,
        },
        "avoidable_via_response_cache": avoidable,
        "base_set_stage_io_share": {
            "recorded_strategies_n": len(live_pairs),
            "fetch_seconds_range": [min(f for f, _ in live_pairs), max(f for f, _ in live_pairs)],
            "hits_compute_seconds_range": [min(h for _, h in live_pairs), max(h for _, h in live_pairs)],
            "io_share_min": round(min(io_share), 6),
            "io_share_max": round(max(io_share), 6),
            "source": "experiments/output/ranking_benchmark/base_set_comparison.md (recorded prior runs; per-paper fetch path)",
        },
    }
    out = Path(__file__).parent / "s2_avoidable_io_results.json"
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
