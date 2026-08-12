#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${REMNANODE_DIR:-/opt/remnanode}"
if [[ ! -f "$PROJECT_DIR/docker-compose.yml" ]]; then
  echo "Compose file not found: $PROJECT_DIR/docker-compose.yml" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed." >&2
  exit 1
fi

cd "$PROJECT_DIR"
docker compose ps

running="$(docker compose ps --status running --quiet | wc -l)"
total="$(docker compose ps --all --quiet | wc -l)"
if [[ "$total" -eq 0 || "$running" -ne "$total" ]]; then
  echo "Node is not healthy: $running of $total containers are running." >&2
  docker compose logs --tail=50 >&2 || true
  exit 1
fi
echo "Node is running: $running of $total containers."

