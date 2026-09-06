# Research Tree

Research Tree turns an academic topic into an editable tree map of its research field: branches for the field's lines of work, curated paper cards, and reading paths ordered by intellectual prerequisites rather than publication date.

## How a workspace is built

A four-stage pipeline runs per topic:

1. **candidates** — one LLM call plans the field's search vocabulary; a single boolean bulk query against Semantic Scholar builds a pool; hub-normalized HITS ranks the citation graph; one hop of citation snowballing recovers founding papers keyword search structurally misses; the top authorities and surveys become the candidate artifact.
2. **construct** — a reasoning model curates the candidates into the tree: branches, paper cards, reading paths.
3. **hydrate** — metadata, TLDRs, and open-access full text for the selected papers.
4. **related** — per-paper similar-paper recommendations (optional `pipeline` extra).

A workspace agent (LangGraph) answers questions about the workspace and proposes reviewed, validated modifications.

## Reading a paper

Any paper card opens its PDF with inline annotations: a model reads the paper a
passage at a time, marks the claims, implications, and terms worth a reader's
attention, and each annotation is placed on the exact words it quotes. A paper
is annotated once and cached, so only the first open waits for it.

## Running locally

Backend (FastAPI, Python ≥ 3.11, [uv](https://docs.astral.sh/uv/)):

```sh
uv sync --extra dev            # add --extra pipeline for similar-paper reranking
cp .env.example .env           # fill in OPENAI_API_KEY (S2_API_KEY recommended)
./scripts/run_backend.sh       # serves on 127.0.0.1:8000
```

Frontend (React + Vite + TypeScript):

```sh
npm install
npm run dev                    # proxies /api to the backend
```

Without a database URL the app keeps workspaces as JSON files under `data/`
and runs as one implicit local user (`RESEARCH_TREE_AUTH_MODE=none`). Only run
it that way on loopback or behind a proxy that does its own sign-in.

## With Docker

`docker compose up --build` runs the production image against a scratch
Postgres and Redis, applying the schema first: an `api` container serving the
site and a `worker` container (Celery) running workspace builds, paper
annotations, a daily database keep-alive and a weekly backup. `.env.example`
documents every variable, including the ones that turn on accounts (email +
password and Google sign-in), Postgres, Redis, blob storage, and the
per-account limits.

Without Redis the API does the long work itself, in-process, which is fine on
a laptop.

## Deploying

Pushes to `main` deploy to Azure through `.github/workflows/ci.yml`: the tests
(with Postgres and Redis lanes) and a vulnerability audit must pass, then the
image is built, the database is migrated, and the `api` and `worker` container
apps are rolled forward. Self-hosters can run the same image anywhere; set
`RESEARCH_TREE_AUTH_MODE=accounts` with a Postgres URL and a `SESSION_SECRET`,
or `none` behind a reverse proxy that authenticates for you.

## Verifying changes

```sh
uv run pytest -q       # passes without API keys (deterministic LLM fallback)
npm run typecheck
```

Setting `RESEARCH_TREE_TEST_DATABASE_URL` to a scratch Postgres runs every
repository test a second time against the schema the migrations build;
`RESEARCH_TREE_TEST_REDIS_URL` turns on the tests for jobs, limits and tokens.

## Notes

- Semantic Scholar allows ~1 request/second across all endpoints; the retrieval code is built around few, dense requests and a shared file cache. Treat every external API as unreliable — stages degrade with warnings instead of failing the run.
- All LLM calls go through the OpenAI Responses API with reasoning models; requests never set `temperature` (the models reject it) — behavior is tuned via `reasoning.effort`.
