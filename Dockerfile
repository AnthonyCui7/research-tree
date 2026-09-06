# syntax=docker/dockerfile:1.7
#
# One image for everything that runs in the cloud: the API (which also serves
# the built web app), the migrate job, the backup job, and later the worker.
# Three stages: build the SPA, build the Python environment (with the two
# embedding models baked in so the container never reaches Hugging Face), then
# a slim runtime with nothing but those artifacts and pg_dump.

# ---- 1. the web app --------------------------------------------------------
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS web
WORKDIR /src
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/
RUN npm ci --no-audit --no-fund
COPY apps/web apps/web
# Same-origin: the bundle calls relative paths on the host that served it.
ENV VITE_RESEARCH_TREE_API_BASE_URL=""
RUN npm run build

# ---- 2. the Python environment --------------------------------------------
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.29@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so a source change does not reinstall torch.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra pipeline
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev --extra pipeline
# The similar-papers stage loads these two models; downloading them at request
# time would make the first build of the day minutes slower and depend on a
# third party being up.
ENV HF_HOME=/opt/hf-home
RUN /app/.venv/bin/python -c "from sentence_transformers import CrossEncoder, SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L6-v2')" && rm -rf /opt/hf-home/hub/.locks /root/.cache

# ---- 3. the runtime ---------------------------------------------------------
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
# pg_dump has to be at least as new as the server (Postgres 17); Debian's
# packaged client is 15, so it comes from the PostgreSQL project's repository.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-client-17; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*; \
    useradd --uid 1000 --create-home --shell /usr/sbin/nologin app; \
    install -d -o app -g app /data
COPY --from=builder --chown=app:app /app /app
COPY --from=builder --chown=app:app /opt/hf-home /opt/hf-home
COPY --from=web --chown=app:app /src/apps/web/dist /app/web
USER app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf-home \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=1 \
    RESEARCH_TREE_DATA_DIR=/data \
    RESEARCH_TREE_WEB_DIR=/app/web \
    RESEARCH_TREE_AUTH_MODE=accounts
EXPOSE 8000
# One worker: the process holds in-memory locks and the agent's node cache.
# Proxy headers come from Container Apps' ingress, the only thing in front.
CMD ["python", "-m", "uvicorn", "research_tree.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
