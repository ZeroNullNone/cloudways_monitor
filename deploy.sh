#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -d .git ]; then
  git pull --ff-only
fi

service_name="${SERVICE_NAME:-cloudways-monitor}"

# Prepare the new image and validate settings while the existing service runs.
docker compose config --quiet
docker compose --progress plain build "$service_name"
docker compose run --rm --no-deps --entrypoint python "$service_name" -c \
  'from cloudways_monitor.settings import Settings; Settings.from_env(); print("Configuration OK")'

if docker compose ps --services --status running | grep -qx "$service_name"; then
  backup_dir="${BACKUP_DIR:-/backups/cloudways_monitor}"
  mkdir -p "$backup_dir"
  backup_file="$backup_dir/cloudways-monitor-$(date +%Y%m%d-%H%M%S).sqlite3"
  container_backup="/tmp/cloudways-monitor-deploy-backup.sqlite3"

  # Use the running container's path, including its committed SQLite WAL data.
  if docker compose exec -T "$service_name" python - "$container_backup" <<'PY'
import os
from pathlib import Path
import sqlite3
import sys

database = Path(os.environ["SQLITE_PATH"])
if not database.is_file():
    print("No existing SQLite database to back up")
    sys.exit(3)
snapshot = Path(sys.argv[1])
snapshot.unlink(missing_ok=True)
with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as source:
    with sqlite3.connect(snapshot) as destination:
        source.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("SQLite backup integrity check failed")
print("SQLite backup integrity OK")
PY
  then
    docker compose cp "$service_name:$container_backup" "$backup_file"
    echo "Database backup: $backup_file"
  else
    backup_status=$?
    if [ "$backup_status" -ne 3 ]; then
      exit "$backup_status"
    fi
  fi
fi

docker compose up -d --force-recreate --remove-orphans "$service_name"
docker compose ps
