# syntax=docker/dockerfile:1
#
# LankaJourney AI — shared image for the Orchestrator, Planner and Booking agents.
# The CMD in docker-compose.yml selects which agent to run.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is used by the compose healthchecks
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so layer caching survives source edits.
COPY requirements.docker.txt .
RUN pip install --no-cache-dir -r requirements.docker.txt

# Dense retrieval, opt-in. sentence-transformers pulls in ~2GB of PyTorch, which
# the Orchestrator, Booking and Conditions agents have no use for — they never
# embed anything. Only the Planner does, when RETRIEVER_BACKEND=opensearch.
#
# Off by default so `docker compose build` stays quick and the other agents stay
# small. The planner service sets --build-arg dense=true. Without it the planner
# still serves BM25 and degrades to sparse-only, which is logged rather than
# silent.
ARG dense=false
COPY requirements.dense.txt .
RUN if [ "$dense" = "true" ]; then \
        pip install --no-cache-dir -r requirements.dense.txt; \
    else \
        echo "dense retrieval disabled — build with --build-arg dense=true"; \
    fi

COPY pyproject.toml ./
COPY src/ ./src/
# The planner's retriever scores against these fixtures at request time.
COPY data/processed/ ./data/processed/
# Coastline data for the route map. Copied, not bind-mounted: it is read-only
# reference data that never changes at runtime, and the planner has no reason to
# see the git-ignored runtime directories the other agents mount.
COPY data/geo/ ./data/geo/

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app

# The booking volume is mounted at /app/state. Docker seeds a fresh named volume
# from this directory's ownership, so creating it here is what lets the
# non-root runtime user actually write booking.db inside it.
RUN mkdir -p /app/state && chown appuser:appuser /app/state

USER appuser

EXPOSE 8000 8001 8002

CMD ["uvicorn", "src.orchestrator.server:app", "--host", "0.0.0.0", "--port", "8000"]