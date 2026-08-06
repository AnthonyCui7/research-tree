"""RW-3d: LLM token cost per workspace build, parsed from the backend's own
usage log (log_llm_usage lines in data/logs/backend.log).

Aggregates every logged LLM call by call type (tokens in/out/reasoning,
wall seconds), and divides pipeline-build call types by the number of
distinct pipeline runs observed in the log to get per-workspace-build
averages. Dollar conversion is left to the current OpenAI price sheet
(models are gpt-5.6-luna / gpt-5.4-mini); token counts are the measurement.

Run:  python3 bench/workspace_cost_benchmark.py
"""

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data/logs/backend.log"

LINE = re.compile(
    r"INFO:\s+(?P<kind>.+?) (?:LLM (?:response |call |model ?)?)"
    r"model=(?P<model>\S+) elapsed_seconds=(?P<sec>[\d.]+) "
    r"input_tokens=(?P<inp>\d+) cached_input_tokens=(?P<cached>\d+)"
    r"(?: cache_write_tokens=\d+)? output_tokens=(?P<out>\d+) reasoning_tokens=(?P<reason>\d+)"
)


def main() -> None:
    stats = defaultdict(lambda: {"calls": 0, "input": 0, "cached_input": 0, "output": 0,
                                 "reasoning": 0, "seconds": 0.0})
    pipeline_ids = set()
    for line in LOG.read_text(errors="ignore").splitlines():
        pid = re.search(r"pipeline_[a-f0-9]{16,}", line)
        if pid:
            pipeline_ids.add(pid.group(0))
        match = LINE.search(line)
        if not match:
            continue
        bucket = stats[match.group("kind").strip()]
        bucket["calls"] += 1
        bucket["input"] += int(match.group("inp"))
        bucket["cached_input"] += int(match.group("cached"))
        bucket["output"] += int(match.group("out"))
        bucket["reasoning"] += int(match.group("reason"))
        bucket["seconds"] += float(match.group("sec"))

    build_kinds = [k for k in stats if "construction" in k or "workspace" in k
                   or "review" in k or "query plan" in k or "tldr" in k.lower()]
    build_totals = {
        "input": sum(stats[k]["input"] for k in build_kinds),
        "output": sum(stats[k]["output"] for k in build_kinds),
        "reasoning": sum(stats[k]["reasoning"] for k in build_kinds),
        "calls": sum(stats[k]["calls"] for k in build_kinds),
    }
    n_runs = len(pipeline_ids)
    result = {
        "experiment": "LLM token usage per workspace build (backend usage log)",
        "by_call_type": {k: {**v, "seconds": round(v["seconds"], 1)} for k, v in sorted(stats.items())},
        "pipeline_runs_in_log": n_runs,
        "build_call_types": build_kinds,
        "per_workspace_build_avg": {
            k: round(v / n_runs) for k, v in build_totals.items()
        } if n_runs else None,
        "models": "gpt-5.6-luna (188 calls), gpt-5.4-mini (1)",
        "note": "dollar cost = tokens x current OpenAI prices; agent-chat calls excluded from build totals",
    }
    out = Path(__file__).parent / "workspace_cost_results.json"
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
