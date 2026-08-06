"""RW-1: Semantic Scholar API latency vs cache-hit latency (disk JSON today,
Redis tomorrow), on identical real payloads.

Fetches paper details for 20 seed-sampled paper IDs from run12's citation
graph through the production SemanticScholarClient (fresh scratch cache dir,
so every call goes to the network), timing the raw HTTP request separately
from rate-limiter pacing. Then times the current on-disk JSON cache hit path
and Redis GETs for the same responses.

Prereqs: local redis-server on :6379, S2_API_KEY in .env (optional).
Run:  uv run python bench/s2_vs_redis_latency_benchmark.py
"""

import json
import random
import statistics
import tempfile
import time
from pathlib import Path

import redis

from research_tree.retrieval.semantic_scholar import SemanticScholarClient

ROOT = Path(__file__).resolve().parent.parent
SEED = 42
N_PAPERS = 12
REDIS_GETS_PER_KEY = 250


def load_env() -> dict[str, str]:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def pctiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50_ms": round(statistics.median(ordered) * 1000, 2),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)] * 1000, 2),
        "n": len(ordered),
    }


def main() -> None:
    env = load_env()
    refs = json.loads(
        (ROOT / "data/candidate_runs/run12/citation_graph_edges.json").read_text()
    )["references"]
    paper_ids = random.Random(SEED).sample(sorted(refs), N_PAPERS)

    scratch = Path(tempfile.mkdtemp(prefix="s2_bench_cache_"))
    client = SemanticScholarClient(cache_dir=scratch, api_key=env.get("S2_API_KEY"), max_retries=4)

    http_times: list[float] = []
    inner = client.client._request_with_retries

    def timed_request(method, url, body):
        start = time.perf_counter()
        result = inner(method, url, body)
        http_times.append(time.perf_counter() - start)
        return result

    client.client._request_with_retries = timed_request

    payload_bytes = []
    for paper_id in paper_ids:
        details = client.get_paper_details([paper_id], warnings=None)
        payload_bytes.append(len(json.dumps(details)))

    # Current cache-hit path: same calls again, now served from disk JSON.
    disk_times = []
    for paper_id in paper_ids:
        start = time.perf_counter()
        client.get_paper_details([paper_id], warnings=None)
        disk_times.append(time.perf_counter() - start)

    # Redis: store the exact cached response files, time GETs.
    store = redis.Redis()
    cache_files = sorted(scratch.glob("*.json"))
    for cache_file in cache_files:
        store.set(f"s2:{cache_file.stem}", cache_file.read_bytes())
    redis_times = []
    keys = [f"s2:{f.stem}" for f in cache_files]
    for _ in range(REDIS_GETS_PER_KEY):
        for key in keys:
            start = time.perf_counter()
            payload = store.get(key)
            json.loads(payload)
            redis_times.append(time.perf_counter() - start)

    result = {
        "experiment": "S2 paper-details latency: live API vs disk-JSON cache hit vs Redis GET (incl. JSON parse)",
        "sample": {
            "paper_ids": N_PAPERS,
            "seed": SEED,
            "median_payload_bytes": int(statistics.median(payload_bytes)),
            "api_key_used": bool(env.get("S2_API_KEY")),
        },
        "live_s2_http": pctiles(http_times),
        "disk_cache_hit": pctiles(disk_times),
        "redis_get": pctiles(redis_times),
        "notes": [
            "live_s2_http excludes deliberate rate-limiter pacing (timed inside the retry wrapper)",
            "S2 traffic is additionally capped at 1 req/s globally by the production RateLimiter",
            "redis_get on local loopback; a managed Redis adds network RTT",
        ],
    }
    out = Path(__file__).parent / "s2_vs_redis_latency_results.json"
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
