# Backend image: server + engine + common + the agents/ content tree.
#
# The shell stays on the host — it is a terminal UI, and the whole point of
# Helve is that the human is at the other end of it.  Run `helve` locally with
# HELVE_SERVER_URL pointed at this container.
#
# Note: the macOS Seatbelt sandbox has no Linux counterpart, so sandboxed tool
# execution falls back to the host environment inside this image.  Run it
# against a workspace you would let an agent write to.
FROM python:3.12-slim AS base

# git: engine/memory/_snapshot.py keeps the data root under version control.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /usr/local/bin/

RUN useradd --create-home --shell /bin/bash helve
WORKDIR /app

# common/pyproject.toml declares data-files that point into ../agents/skills,
# so the content tree has to be present before the first resolve — a stub
# layer would fail the build rather than speed it up.
COPY common/ ./common/
COPY engine/ ./engine/
COPY agents/ ./agents/
COPY server/pyproject.toml server/uv.lock ./server/

# Resolve dependencies before the server source lands, so editing a router
# does not re-resolve the whole tree.
RUN --mount=type=cache,target=/root/.cache/uv \
    cd server && uv sync --frozen --extra dev --no-install-project

COPY server/ ./server/

RUN --mount=type=cache,target=/root/.cache/uv \
    cd server && uv sync --frozen --extra dev \
    && chown -R helve:helve /app

USER helve
ENV HELVE_PROJECT_ROOT=/app \
    PATH="/app/server/.venv/bin:$PATH"

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/openapi.json', timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/server"]
