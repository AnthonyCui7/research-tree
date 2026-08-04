# Research Tree

Research Tree turns an academic topic into an editable tree map of its research field: branches for the field's lines of work, curated paper cards, and reading paths ordered by intellectual prerequisites rather than publication date.

## How a workspace is built

A four-stage pipeline runs per topic:

1. **candidates** — one LLM call plans the field's search vocabulary; a single boolean bulk query against Semantic Scholar builds a pool; hub-normalized HITS ranks the citation graph; one hop of citation snowballing recovers founding papers keyword search structurally misses; the top authorities and surveys become the candidate artifact.
2. **construct** — a reasoning model curates the candidates into the tree: branches, paper cards, reading paths.
3. **hydrate** — metadata, TLDRs, and open-access full text for the selected papers.
4. **related** — per-paper similar-paper recommendations (optional `pipeline` extra).

A workspace agent (LangGraph) answers questions about the workspace and proposes reviewed, validated modifications.

## Running

Backend (FastAPI, Python ≥ 3.11, [uv](https://docs.astral.sh/uv/)):

```sh
uv sync --extra dev            # add --extra pipeline for similar-paper reranking
cp .env.example .env           # fill in OPENAI_API_KEY (S2_API_KEY recommended)
./scripts/run_backend.sh       # serves on 127.0.0.1:8000
```

Frontend (React + Vite + TypeScript):

```sh
cd apps/web
npm install
npm run dev
```

Research Tree currently runs as a **local single-user app**: it persists to JSON
on disk, has no authentication, and binds to localhost.

## Verifying changes

```sh
uv run pytest -q       # passes without API keys (deterministic LLM fallback)
npm run typecheck
```

## Notes

- Semantic Scholar allows ~1 request/second across all endpoints; the retrieval code is built around few, dense requests and a shared file cache. Treat every external API as unreliable — stages degrade with warnings instead of failing the run.
- All LLM calls go through the OpenAI Responses API with reasoning models; requests never set `temperature` (the models reject it) — behavior is tuned via `reasoning.effort`.
