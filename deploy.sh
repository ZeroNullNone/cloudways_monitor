#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -d .git ]; then
  git pull --ff-only
fi

service_name="${SERVICE_NAME:-cloudways-monitor}"
database_path="${DATABASE_PATH:-/data/cloudways-monitor.sqlite3}"

if docker compose ps --services --status running | grep -qx "$service_name"; then
  backup_dir="${BACKUP_DIR:-/backups/cloudways_monitor}"
  mkdir -p "$backup_dir"
  backup_file="$backup_dir/cloudways-monitor-$(date +%Y%m%d-%H%M%S).sqlite3"

  if docker compose exec -T "$service_name" sh -lc "test -f '$database_path'"; then
    docker compose cp "$service_name:$database_path" "$backup_file"
  fi
fi

docker compose up -d --build --remove-orphans
docker compose ps