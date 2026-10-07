# Deployment Notes

The v1 production shape is one Docker container behind Caddy on the shared DigitalOcean droplet. The app exposes one loopback HTTP port for Caddy and keeps SQLite metric history on a named Docker volume mounted at `/data`.

## Build and run

Commit and push the prepared changes from the development checkout before updating the production checkout. Commit source, `frontend/package-lock.json`, `.env.example`, tests and verification records. `.env`, local databases, `.tmp/`, `.venv/`, dependency directories and built frontend assets are ignored. The Docker build also excludes the local preview files and virtual environment.

1. Enter the existing production checkout and fetch the release. Keep the same directory and Compose project name so the existing database volume is reused:

   ```bash
   cd /path/to/existing/cloudways_monitor
   git pull --ff-only
   nano .env
   ```

   Keep the deployment-local `.env`; Git does not update this ignored file. Use the configuration section below to adjust it. For a new installation only, copy `.env.example` and configure it locally.

2. Build and start the service:

   ```bash
   BACKUP_DIR="$HOME/backups/cloudways_monitor" bash deploy.sh
   ```

   `deploy.sh` repeats `git pull --ff-only`, validates Compose without printing interpolated environment values, builds the image, and validates the new application's settings in a temporary container. These preparation steps leave the existing service running. The settings check does not start collection or send Telegram messages.

   If the service is running and its database exists, the script uses Python's [SQLite online backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup), checks the backup's integrity, then copies the snapshot to `BACKUP_DIR`. Committed WAL data is included. The source path comes from the running container's `SQLITE_PATH`. A configuration, build or backup error aborts before recreating the service. `BACKUP_DIR` must be writable; its default is `/backups/cloudways_monitor` when the override above is omitted.

   Finally the script recreates the application container using the newly built image and updated environment. The named data volume is preserved. A plain `git pull` does not deploy code; `docker compose restart` does not apply new environment settings. See [Compose up](https://docs.docker.com/reference/cli/docker/compose/up/).

3. Confirm container state and the backend health check from the droplet. Health can initially show `starting` while the Docker health check runs:

   ```bash
   docker compose ps
   curl -fsS http://127.0.0.1:8083/health
   docker compose logs --tail=100 cloudways-monitor
   ```

   `/health` checks HTTP availability only. It does not prove that Cloudways collection, login or Telegram delivery works. Open the HTTPS dashboard, sign in, and inspect `Collection details`, the collector's last successful cycle and any configuration/API errors. Check server/application discovery, one chart, application disk and one application analysis. Application analyses can take time to finish their upstream tasks. IMS should show configured totals of approximately 2 GB RAM and 100 GB data disk if its plan is unchanged.

4. For command-line diagnosis, confirm protected endpoints with a temporary cookie jar. `SESSION_COOKIE_SECURE=true` requires using the public HTTPS URL:

   ```bash
   BASE_URL=https://cloudways-monitor.example.com
   curl -c /tmp/cloudways-monitor.cookies \
     -H "Content-Type: application/json" \
     -d '{"username":"admin","password":"your-dashboard-password"}' \
     "$BASE_URL/api/auth/login"

   curl -b /tmp/cloudways-monitor.cookies \
     "$BASE_URL/api/doctor"
   curl -b /tmp/cloudways-monitor.cookies \
     "$BASE_URL/api/collector/health"
   rm -f /tmp/cloudways-monitor.cookies
   ```

   Substitute the existing domain and dashboard credentials locally. `/api/doctor` may show degraded status while the API budget is exhausted; inspect the error and retry delay together with `/api/collector/health`. Initial server data appears after collection. The 60-second polling setting is a wait after a full cycle, so upstream calls can extend the first collection.

## Updating an existing production environment

Set `CLOUDWAYS_ACCESS_TOKEN` to a current Cloudways v2 **Access Token**. Renaming the old API-key setting while retaining its old API-key value is insufficient. Keep `CLOUDWAYS_API_BASE_URL=https://api.cloudways.com/api/v2`.

Preserve the existing dashboard username/password hash, `SESSION_SECRET`, domain, and Telegram bot/chat credentials. Keep `TELEGRAM_ENABLED=true`, `SESSION_COOKIE_SECURE=true` behind HTTPS and `SQLITE_PATH=/data/cloudways-monitor.sqlite3` for the named volume. Never replace a working production `.env` wholesale with the example.

The current timing defaults are `POLL_INTERVAL_SECONDS=60`, `STALE_AFTER_SECONDS=600`, `RETENTION_DAYS=30`, `ALERT_CONSECUTIVE_POLLS=3`, and `ALERT_COOLDOWN_SECONDS=1800`. Existing explicit values still apply; adjust them if the current defaults are desired. Clear `MONITORED_SERVER_IDS` and `MONITORED_APP_IDS` only when every account resource should be monitored.

The new optional alert settings have built-in defaults:

```env
IDLE_CPU_WARNING_PERCENT=20
IDLE_CPU_CRITICAL_PERCENT=5
FREE_MEMORY_WARNING_MB=256
FREE_MEMORY_CRITICAL_MB=128
FREE_DISK_WARNING_MB=5120
FREE_DISK_CRITICAL_MB=2048
```

All thresholds measure free resources. Remove these unused settings from the production file:

- `CLOUDWAYS_EMAIL`, `CLOUDWAYS_API_KEY`
- `CLOUDWAYS_TASK_POLLING_ENABLED`, `CLOUDWAYS_TASK_POLL_ATTEMPTS`, `CLOUDWAYS_TASK_POLL_INTERVAL_SECONDS`
- `CLOUDWAYS_MONITOR_GRAPH_DURATION`, `CLOUDWAYS_MONITOR_GRAPH_TIMEZONE`
- `CLOUDWAYS_APP_TRAFFIC_POLLING_ENABLED`, `CLOUDWAYS_APP_TRAFFIC_DURATION`
- `CPU_WARNING_PERCENT`, `CPU_CRITICAL_PERCENT`, `RAM_WARNING_PERCENT`, `RAM_CRITICAL_PERCENT`, `DISK_WARNING_PERCENT`, `DISK_CRITICAL_PERCENT`

The dashboard and current-alert endpoint use only the currently configured alert targets. Stored events remain available as history. Updating the container does not require resetting the database or migrating old alert rules.

## Caddy example

Route your chosen subdomain to the loopback port published by Compose:

```caddyfile
cloudways-monitor.example.com {
    reverse_proxy 127.0.0.1:8083
}
```

If this service joins an existing Docker network with Caddy, remove the host port mapping and point `reverse_proxy` at the service name and port instead:

```caddyfile
cloudways-monitor.example.com {
    reverse_proxy cloudways-monitor:8083
}
```

## Persistent data

`compose.yaml` mounts the named Docker volume `cloudways-monitor-data` to `/data`. Keep `SQLITE_PATH=/data/cloudways-monitor.sqlite3` in `.env` so metric history survives container restarts and image rebuilds.

Do not use `docker compose down -v` or delete the data volume during an update. The backup produced by `deploy.sh` is a point-in-time SQLite snapshot, not an automatic rollback. An application startup failure after recreation needs diagnosis using the logs and collector status; the script does not automatically restore the previous image.

To confirm the mounted data path from inside the running container:

```bash
docker compose exec cloudways-monitor sh -lc 'test -d /data && echo /data-mounted'
```
