#!/usr/bin/env bash
# Write the .env used by CI.
#
# Shared by the jobs that run `docker compose`, because compose interpolates the
# whole file even when you only ask for one service. A job that writes only the
# variables it happens to need fails on whatever another service marks as
# required — which is exactly what happened twice. Adding a required variable to
# docker-compose.yml therefore has to touch only this script.
#
# Everything here is generated per run. A secret in a committed workflow file is
# a credential in the repository and in its history.
set -euo pipefail

{
  # OpenSearch admin password.
  echo "OPENSEARCH_INITIAL_ADMIN_PASSWORD=$(openssl rand -base64 18)"
  # Signs the HITL confirmation tokens. The booking agent refuses to clear the
  # gate without it, so compose requires it.
  echo "HITL_TOKEN_SECRET=$(openssl rand -hex 24)"
  # Run the containers as the runner's own uid so bind-mounted writes succeed.
  # Hardcoding 1000 broke the booking store against the runner's 1001.
  echo "DOCKER_UID=$(id -u)"
  echo "DOCKER_GID=$(id -g)"
  echo "LOG_LEVEL=WARNING"
  echo "DEBUG_MODE=False"
  # The GROQ key is deliberately absent: the router and the parser both have
  # deterministic fallbacks, which is what the tests should be exercising anyway.
} > .env

echo "wrote .env with $(grep -c '=' .env) generated variables"
