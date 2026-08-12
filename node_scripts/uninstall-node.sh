#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${REMNANODE_DIR:-/opt/remnanode}"
PURGE=false
if [[ "${1:-}" == "--purge" ]]; then
  PURGE=true
elif [[ $# -gt 0 ]]; then
  echo "Usage: sudo $0 [--purge]" >&2
  exit 2
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root (or through sudo)." >&2
  exit 1
fi
if [[ "$PROJECT_DIR" != /* || "$PROJECT_DIR" == "/" ]]; then
  echo "Unsafe REMNANODE_DIR: $PROJECT_DIR" >&2
  exit 2
fi
if [[ ! -f "$PROJECT_DIR/docker-compose.yml" ]]; then
  echo "Compose file not found: $PROJECT_DIR/docker-compose.yml" >&2
  exit 1
fi

echo "This will stop and remove Remnawave Node containers in $PROJECT_DIR."
if $PURGE; then
  echo "The project directory will also be permanently deleted."
fi
read -r -p 'Type DELETE REMNANODE to continue: ' confirmation
if [[ "$confirmation" != "DELETE REMNANODE" ]]; then
  echo "Cancelled."
  exit 1
fi

cd "$PROJECT_DIR"
docker compose down --remove-orphans

if $PURGE; then
  cd /
  rm -rf --one-file-system "$PROJECT_DIR"
  echo "Containers and $PROJECT_DIR were removed."
else
  echo "Containers were removed; configuration remains in $PROJECT_DIR."
fi

