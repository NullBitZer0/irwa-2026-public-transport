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
# sentence-transformers is intentionally excluded: it is only needed by the
# offline OpenSearch ingest script (src/planner/opensearch_ingest.py), never by
# a running agent, and it would pull in ~2GB of PyTorch for no benefit.
COPY requirements.docker.txt .
RUN pip install --no-cache-dir -r requirements.docker.txt

COPY pyproject.toml ./
COPY src/ ./src/
# The planner's retriever scores against these fixtures at request time.
COPY data/processed/ ./data/processed/

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app

# The booking volume is mounted at /app/state. Docker seeds a fresh named volume
# from this directory's ownership, so creating it here is what lets the
# non-root runtime user actually write booking.db inside it.
RUN mkdir -p /app/state && chown appuser:appuser /app/state

USER appuser

EXPOSE 8000 8001 8002

CMD ["uvicorn", "src.orchestrator.server:app", "--host", "0.0.0.0", "--port", "8000"]