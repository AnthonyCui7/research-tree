"""RW-2: How many Semantic Scholar reference lookups would a shared
(e.g. Redis) cache have avoided across the 12 recorded candidate runs?

Replays the per-run reference-fetch workload recorded in
data/candidate_runs/run*/citation_graph_edges.json in chronological order
(run_metadata.json started_at) and counts, per run, how many paper reference
lookups were repeats of lookups made by ANY earlier run. Splits the result by
whether an earlier run shared the topic (dev-iteration reuse) or not
(cross-topic reuse). No network, no mocks — this is the actual recorded
workload.

Run:  python3 bench/s2_cache_overlap_benchmark.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_ROOTS = (
    ROOT / "data" / "candidate_runs",
    ROOT / "experiments" / "output" / "workspace_candidate_preparation",
)


def normalize_topic(topic: str) -> str:
    return " ".join(topic.lower().replace("-", " ").split())


def run_dirs():
    for root in RUN_ROOTS:
        for run_dir in root.glob("run*"):
            yield run_dir


def main() -> None:
    runs = []
    for run_dir in run_dirs():
        edges_file = run_dir / "citation_graph_edges.json"
        meta_file = run_dir / "run_metadata.json"
        if not edges_file.exists() or not meta_file.exists():
            continue
        meta = json.loads(meta_file.read_text())
        refs = json.loads(edges_file.read_text()).get("references", {})
        runs.append(
            {
                "run": f"{run_dir.parent.name}/{run_dir.name}",
                "topic": normalize_topic(meta.get("topic", "")),
                "started_at": meta.get("started_at", ""),
                "papers": set(refs),
            }
        )
    runs.sort(key=lambda r: r["started_at"])

    seen: set[str] = set()
    seen_by_topic: dict[str, set[str]] = {}
    per_run = []
    total_lookups = total_repeats = total_cross_topic_repeats = 0
    for run in runs:
        papers = run["papers"]
        prior_same_topic = seen_by_topic.get(run["topic"], set())
        repeats = papers & seen
        cross_topic_repeats = repeats - prior_same_topic
        per_run.append(
            {
                "run": run["run"],
                "topic": run["topic"],
                "reference_lookups": len(papers),
                "repeats_of_any_earlier_run": len(repeats),
                "repeats_from_other_topics_only": len(cross_topic_repeats),
            }
        )
        total_lookups += len(papers)
        total_repeats += len(repeats)
        total_cross_topic_repeats += len(cross_topic_repeats)
        seen |= papers
        seen_by_topic.setdefault(run["topic"], set()).update(papers)

    followup = [r for r in per_run[1:]]
    followup_lookups = sum(r["reference_lookups"] for r in followup)
    result = {
        "experiment": "Shared-cache avoidable S2 reference lookups across recorded candidate runs",
        "runs_in_chronological_order": per_run,
        "totals": {
            "runs": len(runs),
            "distinct_topics": len(seen_by_topic),
            "reference_lookups_all_runs": total_lookups,
            "unique_papers": len(seen),
            "repeat_lookups": total_repeats,
            "repeat_rate_overall": round(total_repeats / total_lookups, 4) if total_lookups else None,
            "repeat_rate_runs_after_first": round(total_repeats / followup_lookups, 4) if followup_lookups else None,
            "cross_topic_repeat_lookups": total_cross_topic_repeats,
        },
        "interpretation_caveats": [
            "per-paper cache keying assumed (current client caches per batch request hash)",
            "same-topic repeats are dev-iteration reuse; cross-topic repeats are the organic overlap",
        ],
    }
    out = Path(__file__).parent / "s2_cache_overlap_results.json"
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
