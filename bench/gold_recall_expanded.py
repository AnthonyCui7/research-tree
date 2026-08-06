"""RW-2 (expanded): Ranker recall against survey-derived gold sets, 4 topics.

Gold construction (neutral third-party ground truth): per topic, take the 2-3
most relevant recent (>=2024) surveys present in that run's pool whose
reference lists were recorded (production citation_graph_edges.json, or the
experiments s2_references cache for the sampling snapshot). Gold = the
surveys' references that are in the pool DB, non-survey, citation_count>=50,
ranked by (cited-by-N-of-the-surveys, citation_count), top 8 per topic.

Rankers (same graph per topic): HITS authority, PageRank, raw citation count,
age-adjusted citations; cross-encoder where the snapshot has it (sampling
only). survey_reference_count is EXCLUDED (circular with gold construction).
Also reports the curation-funnel metric: gold recall of the shipped curated
candidate set vs an equal-size top-by-citation baseline.

Run:  uv run python bench/gold_recall_expanded.py
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "experiments"))

import validate_ranking_signals as vrs

CACHE = ROOT / "experiments/cache/s2_references"

TOPICS = {
    "sampling": {
        "dir": "experiments/output/workspace_candidate_preparation/run32",
        "survey_regex": r"decod(ing|er)|meta.generation|inference.time",
        "refs": "cache",
    },
    "prompting": {
        "dir": "data/candidate_runs/run12",
        "survey_regex": r"prompt|in.context|chain.of.thought",
        "refs": "edges",
    },
    "rag": {
        "dir": "experiments/output/workspace_candidate_preparation/run40",
        "survey_regex": r"retrieval.augmented|\bRAG\b",
        "refs": "edges",
    },
    "speculative-decoding": {
        "dir": "experiments/output/workspace_candidate_preparation/run45",
        "survey_regex": r"speculative|inference accelerat|efficient inference",
        "refs": "edges",
    },
}

MIN_GOLD_CITATIONS = 50
GOLD_PER_TOPIC = 8
SURVEYS_PER_TOPIC = 3
KS = (20, 50, 100)


def load_refs(config: dict, run_dir: Path, db_ids: set[str]) -> dict[str, set[str]]:
    if config["refs"] == "edges":
        raw = json.loads((run_dir / "citation_graph_edges.json").read_text())["references"]
        return {pid: set(refs) for pid, refs in raw.items()}
    refs = {}
    for pid in db_ids:
        cache_file = CACHE / f"{pid}.json"
        if cache_file.exists():
            refs[pid] = set(json.loads(cache_file.read_text()))
    return refs


def ref_list_for_survey(pid: str, refs: dict[str, set[str]]) -> set[str] | None:
    if pid in refs:
        return refs[pid]
    cache_file = CACHE / f"{pid}.json"
    if cache_file.exists():
        return set(json.loads(cache_file.read_text()))
    return None


def evaluate_topic(name: str, config: dict) -> dict:
    run_dir = ROOT / config["dir"]
    db = json.loads((run_dir / "s2_bulk_deduped_paper_database.json").read_text())
    papers = db["papers"]
    by_id = {p["paper_id"]: p for p in papers}
    refs = load_refs(config, run_dir, set(by_id))

    pattern = re.compile(config["survey_regex"], re.I)
    survey_pool = [
        p for p in papers
        if p.get("is_survey") and (p.get("year") or 0) >= 2024 and pattern.search(p["title"])
        and ref_list_for_survey(p["paper_id"], refs) is not None
    ]
    surveys = sorted(survey_pool, key=lambda p: -(p.get("citation_count") or 0))[:SURVEYS_PER_TOPIC]

    votes: dict[str, int] = {}
    for survey in surveys:
        for ref in ref_list_for_survey(survey["paper_id"], refs):
            votes[ref] = votes.get(ref, 0) + 1
    survey_ids = {s["paper_id"] for s in surveys}
    eligible = [
        (pid, count) for pid, count in votes.items()
        if pid in by_id and pid not in survey_ids
        and not by_id[pid].get("is_survey")
        and (by_id[pid].get("citation_count") or 0) >= MIN_GOLD_CITATIONS
    ]
    eligible.sort(key=lambda item: (-item[1], -(by_id[item[0]].get("citation_count") or 0)))
    gold = [pid for pid, _ in eligible[:GOLD_PER_TOPIC]]

    out_edges = {pid: {r for r in prefs if r in by_id and r != pid} for pid, prefs in refs.items()}
    graph_nodes = set(out_edges) | {t for ts in out_edges.values() for t in ts}
    auth = vrs.hits(out_edges, graph_nodes)
    pr = vrs.pagerank(out_edges, graph_nodes)

    def age_score(p):
        return p.get("age_adjusted_citation_score") or 0

    rankers = {
        "hits_authority": sorted(graph_nodes, key=lambda p: -auth.get(p, 0)),
        "pagerank": sorted(graph_nodes, key=lambda p: -pr.get(p, 0)),
        "raw_citation_count": [p["paper_id"] for p in sorted(papers, key=lambda p: -(p.get("citation_count") or 0))],
        "age_adj_citations": [p["paper_id"] for p in sorted(papers, key=lambda p: -age_score(p))],
    }
    if "cross_encoder_rank" in papers[0]:
        rankers["cross_encoder"] = [
            p["paper_id"] for p in sorted(
                (p for p in papers if p.get("cross_encoder_rank") is not None),
                key=lambda p: p["cross_encoder_rank"])
        ]

    gold_set = set(gold)
    recall = {
        rname: {f"r@{k}": round(vrs.recall_at_k(ranking, gold_set, k), 3) for k in KS}
        for rname, ranking in rankers.items()
    }

    cand = json.loads((run_dir / "llm_candidate_papers.json").read_text())
    curated = [p["paper_id"] for p in cand["non_survey_papers"] + cand["survey_papers"]]
    baseline_topk = rankers["raw_citation_count"][: len(curated)]
    funnel = {
        "curated_size": len(curated),
        "gold_in_curated": len(gold_set & set(curated)),
        "gold_in_citation_topk_baseline": len(gold_set & set(baseline_topk)),
        "pool_size": len(papers),
    }

    return {
        "topic": name,
        "snapshot": config["dir"],
        "graph": {"ref_lists": len(refs), "nodes": len(graph_nodes)},
        "surveys_used": [
            {"title": s["title"], "year": s.get("year"), "citations": s.get("citation_count")}
            for s in surveys
        ],
        "gold": [
            {"title": by_id[pid]["title"], "year": by_id[pid].get("year"),
             "citations": by_id[pid].get("citation_count"), "survey_votes": votes[pid]}
            for pid in gold
        ],
        "recall": recall,
        "funnel": funnel,
    }


def main() -> None:
    results = [evaluate_topic(name, config) for name, config in TOPICS.items()]

    pooled: dict[str, dict[str, list[float]]] = {}
    for res in results:
        for rname, row in res["recall"].items():
            for k, value in row.items():
                pooled.setdefault(rname, {}).setdefault(k, []).append(value)
    macro = {
        rname: {k: round(sum(vs) / len(vs), 3) for k, vs in row.items()}
        for rname, row in pooled.items()
    }

    report = {
        "experiment": "Survey-derived gold-set recall, 4 topics",
        "gold_total": sum(len(r["gold"]) for r in results),
        "topics": results,
        "macro_avg_recall (rankers present in all topics)": {
            r: macro[r] for r in ("hits_authority", "pagerank", "raw_citation_count", "age_adj_citations")
        },
        "notes": [
            "survey_reference_count ranker excluded (circular with gold construction)",
            "cross_encoder only available in the sampling snapshot (legacy schema)",
            "surveys contribute edges to the shared graph; gold papers receive at most "
            "3 in-degree units from the gold-defining surveys (disclosed)",
        ],
    }
    out = Path(__file__).parent / "gold_recall_expanded_results.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
