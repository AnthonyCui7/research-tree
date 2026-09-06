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

## Self-hosting for more than one person

`docker compose up --build` starts the whole stack from the repository: an
`api` container serving the site, a `worker` (Celery) running workspace builds
and paper annotations, Postgres, Redis, and a one-shot `migrate` service that
applies the schema first. Set `RESEARCH_TREE_AUTH_MODE=accounts` and a
`SESSION_SECRET` to require sign-in (email + password, plus Google when a
client id and secret are configured). Each account then sees only its own
workspaces, and the per-account limits cap what one person can spend in a day.
Large per-paper payloads can live in Azure Blob Storage instead of the data
directory. `.env.example` documents every variable.

Without Redis the API does the long work itself, in-process, which is fine on
a laptop.

## Bring your own key

Behind sign-in each account can save its own OpenAI key from the account menu
(API keys). The key is checked against OpenAI, sealed with a data key that
`RESEARCH_TREE_KEY_ENCRYPTION_KEY` (or an Azure Key Vault RSA key) wraps, and
spent only by that account's builds, annotations and assistant turns; every
call is metered at list price and shown on the same screen. An account without
a key can spend the server's `OPENAI_API_KEY` only through an allowance the
operator grants with `research-tree-grant --email … --usd …`.

## Verifying changes

```sh
uv run pytest -q       # passes without API keys (deterministic LLM fallback)
npm run typecheck
```

Setting `RESEARCH_TREE_TEST_DATABASE_URL` to a scratch Postgres runs every
repository test a second time against the schema the migrations build;
`RESEARCH_TREE_TEST_REDIS_URL` turns on the tests for jobs, limits and tokens.
CI runs the same suite in all three lanes and a dependency audit on every push.
Its `deploy` job belongs to the maintainers' hosting and only runs with their
secrets, so a fork can delete it.

## Notes

- Semantic Scholar allows ~1 request/second across all endpoints; the retrieval code is built around few, dense requests and a shared file cache. Treat every external API as unreliable — stages degrade with warnings instead of failing the run.
- All LLM calls go through the OpenAI Responses API with reasoning models; requests never set `temperature` (the models reject it) — behavior is tuned via `reasoning.effort`.
