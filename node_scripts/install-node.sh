#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="/opt/remnanode"
COMPOSE_FILE=""

usage() {
  echo "Usage: sudo $0 --compose /path/to/docker-compose.yml [--project-dir /opt/remnanode]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose) COMPOSE_FILE="${2:-}"; shift 2 ;;
    --project-dir) PROJECT_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root (or through sudo)." >&2
  exit 1
fi
if [[ -z "$COMPOSE_FILE" || ! -f "$COMPOSE_FILE" ]]; then
  echo "A readable --compose file is required." >&2
  exit 2
fi
if [[ "$PROJECT_DIR" != /* || "$PROJECT_DIR" == "/" ]]; then
  echo "--project-dir must be an absolute, non-root path." >&2
  exit 2
fi
if ! grep -Eq '^[[:space:]]*services:[[:space:]]*$' "$COMPOSE_FILE"; then
  echo "The supplied file does not look like a Docker Compose file." >&2
  exit 2
fi

if [[ ! -r /etc/os-release ]]; then
  echo "Unsupported Linux distribution: /etc/os-release is missing." >&2
  exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in
  ubuntu|debian) ;;
  *) echo "Only Ubuntu and Debian are supported; detected: ${ID:-unknown}." >&2; exit 1 ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed; installing it using Docker's official installer..."
  apt-get update
  apt-get install -y ca-certificates curl
  installer="$(mktemp)"
  trap 'rm -f "${installer:-}"' EXIT
  curl --fail --silent --show-error --location https://get.docker.com --output "$installer"
  sh "$installer"
  rm -f "$installer"
  trap - EXIT
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is not available." >&2
  exit 1
fi

install -d -m 700 "$PROJECT_DIR"
target_compose="$PROJECT_DIR/docker-compose.yml"
if [[ -f "$target_compose" ]] && ! cmp -s "$COMPOSE_FILE" "$target_compose"; then
  backup="$PROJECT_DIR/docker-compose.yml.backup.$(date -u +%Y%m%dT%H%M%SZ)"
  cp --preserve=mode "$target_compose" "$backup"
  echo "Previous compose file backed up to $backup"
fi
install -m 600 "$COMPOSE_FILE" "$target_compose"

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for helper in node-status.sh uninstall-node.sh; do
  if [[ -f "$source_dir/$helper" ]]; then
    install -m 700 "$source_dir/$helper" "$PROJECT_DIR/$helper"
  fi
done

cd "$PROJECT_DIR"
docker compose config --quiet
docker compose pull
docker compose up -d --remove-orphans

echo "Waiting for containers to start..."
for _ in {1..12}; do
  if [[ -n "$(docker compose ps --status running --quiet)" ]]; then
    docker compose ps
    echo "Remnawave Node deployment finished."
    exit 0
  fi
  sleep 5
done

docker compose ps
docker compose logs --tail=100 >&2 || true
echo "No running container was detected after 60 seconds." >&2
exit 1

