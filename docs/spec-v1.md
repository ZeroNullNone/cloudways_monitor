# Cloudways Monitor v1 Spec

Date: 2026-07-05

## Goal
Build a private live dashboard for one Cloudways account that monitors server and application usage from the Cloudways API. The dashboard should show current health, recent history, and threshold-based alerts for CPU, RAM, storage, traffic, API freshness, and related application telemetry.

## Source of telemetry
Cloudways API is the v1 source of truth. No SSH agent or server-side collector is installed on Cloudways servers.

Official Cloudways API areas relevant to v1:

- Authentication: OAuth access token, sent as a bearer token.
- Discovery: server list and app list.
- Server telemetry: disk usage, monitoring graph, server monitor, server usage, services status.
- Application telemetry: app disk usage, app disk usage graph, traffic, traffic detail, PHP, MySQL, running cron.
- Failure handling: Cloudways documents standard JSON responses and HTTP errors including 400, 401, 403, 422, and 500.

Reference: https://developers.cloudways.com/docs/

## Scope
In scope:

- One Cloudways account.
- Multiple Cloudways servers and applications.
- Auto-discovery with optional allowlists.
- Read-only monitoring only.
- 60-second polling.
- 3-minute stale threshold.
- 30 days of raw metric snapshots.
- Overview-first dashboard with server and application drilldowns.
- Threshold alerts with Telegram notifications.
- App-level single-user authentication.
- Doctor check for deployment and integration readiness.

Out of scope for v1:

- SSH-based metrics collection.
- Cloudways write actions such as restart, cache clear, scale, or settings changes.
- Multi-account or team access.
- Editing alert rules in the UI.
- Bandwidth budget or plan-limit tracking.
- Anomaly detection.
- Downsampling data older than 30 days.

## Architecture
Production runs as one Docker container behind the existing Caddy reverse proxy on the DigitalOcean droplet.

The container runs:

- FastAPI application served by Uvicorn.
- Built React/Vite frontend served by the FastAPI app.
- Telemetry Collector inside the same container as a distinct component.
- SQLite database mounted on a persistent Docker volume.

Caddy owns public HTTPS and subdomain routing. The app exposes one internal HTTP port.

## Data model
Core entities:

- `monitored_resources`: one row per discovered Cloudways server or application included in monitoring.
- `metric_snapshots`: one row per resource per successful poll, with core columns plus raw JSON payload.
- `collector_runs`: one row per polling cycle or per resource collection attempt for health/debugging.
- `alert_rules`: config-derived alert rule definitions loaded at startup.
- `alert_states`: current state for each rule/resource pair.
- `alert_events`: state transitions and notification attempts.
- `user_sessions`: authenticated dashboard sessions if cookie sessions are persisted server-side.

Recommended `metric_snapshots` core columns:

- `id`
- `resource_id`
- `resource_type`
- `captured_at`
- `cpu_percent`
- `ram_used_mb`
- `ram_total_mb`
- `ram_percent`
- `disk_used_gb`
- `disk_total_gb`
- `disk_percent`
- `bandwidth_bytes`
- `traffic_requests`
- `php_metric_json`
- `mysql_metric_json`
- `raw_payload_json`
- `collection_status`
- `error_code`

Core chart and alert queries should use structured columns. Provider-specific values stay in `raw_payload_json` or specific JSON columns until they deserve first-class columns.

## Polling behavior
Every 60 seconds, the Telemetry Collector should:

1. Ensure a valid Cloudways OAuth token is available.
2. Discover servers and applications.
3. Apply optional server/app allowlists.
4. Fetch server telemetry.
5. Fetch application telemetry.
6. Normalize values into core metrics.
7. Store metric snapshots.
8. Evaluate alert rules.
9. Send Telegram notifications when sustained breach/cooldown rules allow.
10. Expire snapshots older than 30 days.

On Cloudways API failures, the dashboard keeps serving last known telemetry, marks affected telemetry stale, exposes collector health, and avoids treating missing values as zero.

## Alerting
Default thresholds:

- CPU warning/critical: 80 percent / 95 percent.
- RAM warning/critical: 80 percent / 95 percent.
- Disk/storage warning/critical: 80 percent / 90 percent.
- Stale telemetry: older than 3 minutes.
- Cloudways API sustained failure: 3 failed polls.
- Bandwidth/traffic: chart only by default.

Noise control:

- Alert only after 3 consecutive breached polls.
- Send one Telegram notification when opened.
- Suppress repeats for 30 minutes while the same condition remains open.
- Notify again if severity changes or the alert resolves and later reopens.

Telegram messages should include resource name, severity, breached metric, current value, threshold, breach duration, and a drilldown URL based on `DASHBOARD_BASE_URL`.

## Dashboard UI
The first screen is an attention-first overview:

1. Active alerts and stale resources.
2. Server cards with CPU, RAM, and disk/storage status.
3. Application cards grouped under parent server.
4. Traffic and bandwidth trend summaries.
5. Collector/API health footer.

Drilldowns:

- Server drilldown: CPU, RAM, disk/storage, traffic/bandwidth, service status, recent alerts, raw debug panel.
- Application drilldown: app disk, traffic, PHP/MySQL metrics where available, recent alerts, raw debug panel, and parent server context.

History ranges:

- 1h
- 6h
- 24h
- 7d
- 30d

Charts store raw 60-second snapshots but aggregate by selected range for readability and performance.

## API surface
Suggested FastAPI routes:

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET /api/overview`
- `GET /api/resources`
- `GET /api/resources/{resource_id}`
- `GET /api/resources/{resource_id}/series?range=1h|6h|24h|7d|30d`
- `GET /api/resources/{resource_id}/raw/latest`
- `GET /api/alerts`
- `GET /api/collector/health`
- `GET /api/events` for SSE live updates
- `GET /api/doctor` for protected diagnostics

## Security rules
- `.env` contains real secrets and must not be committed.
- `.env.example` documents config keys only.
- Store a password hash, not a plaintext dashboard password.
- Keep Cloudways integration read-only in v1.
- Do not expose telemetry before authentication.
- Keep raw provider payloads behind authenticated debug UI.
