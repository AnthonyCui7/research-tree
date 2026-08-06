"""RW-3b: Reading-path ordering quality vs citation direction.

Ground truth: within each generated paper path, for every ordered pair
(A before B) where a citation link exists between the two papers (from the
recorded reference lists: all candidate-run edges files + the experiments
s2_references cache), the cited paper is the prerequisite and should come
first. Metric: % of linked pairs where the path puts the cited paper first.
Baselines: publication-year ordering evaluated on the same pairs (ties
excluded), and random ordering = 50% by symmetry.

Covers all workspaces under data/workspaces/.
Run:  python3 bench/reading_path_order_benchmark.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "experiments/cache/s2_references"


def load_reference_lists() -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for edges_file in list(ROOT.glob("data/candidate_runs/run*/citation_graph_edges.json")) + list(
        ROOT.glob("experiments/output/workspace_candidate_preparation/run*/citation_graph_edges.json")
    ):
        for pid, lst in json.loads(edges_file.read_text()).get("references", {}).items():
            refs.setdefault(pid, set()).update(lst)
    for cache_file in CACHE.glob("*.json"):
        refs.setdefault(cache_file.stem, set()).update(json.loads(cache_file.read_text()))
    return refs


def main() -> None:
    refs = load_reference_lists()
    per_workspace = []
    total_pairs = path_correct = year_correct = year_ties = 0

    for ws_file in sorted(ROOT.glob("data/workspaces/*/current.json")):
        ws = json.loads(ws_file.read_text())
        cards = ws.get("paper_cards", {})
        years = {
            pid: (card.get("year") or card.get("publication_year"))
            for pid, card in cards.items()
        }
        ws_pairs = ws_path = ws_year = 0
        for path in ws.get("paper_paths", []):
            ids = path.get("paper_ids", [])
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]  # path says: read a before b
                    a_cites_b = b in refs.get(a, ())
                    b_cites_a = a in refs.get(b, ())
                    if a_cites_b == b_cites_a:
                        continue  # no link or mutual: no ground truth
                    prerequisite_first = b_cites_a  # b cites a -> a is prerequisite
                    ws_pairs += 1
                    if prerequisite_first:
                        ws_path += 1
                    ya, yb = years.get(a), years.get(b)
                    if ya is None or yb is None or ya == yb:
                        year_ties += 1
                    else:
                        older, newer = (a, b) if ya < yb else (b, a)
                        cited = a if b_cites_a else b
                        if cited == older:
                            ws_year += 1
        total_pairs += ws_pairs
        path_correct += ws_path
        year_correct += ws_year
        per_workspace.append(
            {
                "workspace": ws_file.parent.name,
                "paths": len(ws.get("paper_paths", [])),
                "linked_pairs": ws_pairs,
                "path_correct": ws_path,
            }
        )

    dated_pairs = total_pairs - year_ties
    result = {
        "experiment": "Reading-path pairwise ordering vs citation-prerequisite direction",
        "per_workspace": per_workspace,
        "totals": {
            "linked_pairs": total_pairs,
            "path_ordering_accuracy": round(path_correct / total_pairs, 4) if total_pairs else None,
            "publication_year_baseline_accuracy": round(year_correct / dated_pairs, 4) if dated_pairs else None,
            "year_baseline_evaluable_pairs": dated_pairs,
            "random_baseline": 0.5,
        },
        "notes": [
            "citation links only cover pairs present in recorded reference lists; unlinked pairs unscored",
            "year baseline = 'older paper first' on the same linked pairs (same-year/unknown excluded)",
        ],
    }
    out = Path(__file__).parent / "reading_path_order_results.json"
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
