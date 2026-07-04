# Cloudways Monitor Implementation Plan

Date: 2026-07-05

## Phase 1 - Project skeleton
Deliverables:

- Python backend package with FastAPI/Uvicorn.
- React/Vite frontend package.
- Dockerfile and docker-compose example for one production container.
- `.env.example` loaded by backend settings.
- Basic app shell served through FastAPI in production.

Acceptance checks:

- App starts locally.
- Health endpoint returns OK.
- React build is served by FastAPI.
- `.env` is ignored and `.env.example` is tracked.

## Phase 2 - Cloudways API client
Deliverables:

- OAuth token acquisition and cache.
- Typed client methods for server list, app list, server monitoring, server usage, disk usage, app disk usage, traffic, PHP, MySQL, and service status as available from Cloudways.
- Cloudways error mapping for 400, 401, 403, 422, 500, network failure, and rate limiting.
- Doctor checks for API auth and discovery.

Acceptance checks:

- Doctor check can authenticate with Cloudways.
- Server and app discovery returns normalized resources.
- API failures produce structured collector health errors.

## Phase 3 - SQLite persistence
Deliverables:

- SQLite schema migrations.
- `monitored_resources`, `metric_snapshots`, `collector_runs`, `alert_states`, and `alert_events` tables.
- Repository/query layer for latest snapshots, history ranges, and aggregation buckets.
- Retention cleanup for snapshots older than 30 days.

Acceptance checks:

- Snapshots can be inserted and queried by resource/range.
- Latest snapshot query returns last known telemetry.
- Retention cleanup removes records older than 30 days.

## Phase 4 - Telemetry Collector
Deliverables:

- 60-second polling loop inside the app container.
- Auto-discovery plus optional allowlists.
- Normalization of server and application metrics.
- Last-known/stale behavior for API failures.
- Collector health state exposed through API.

Acceptance checks:

- Collector stores snapshots on schedule.
- Failed Cloudways calls do not overwrite metrics with zero.
- Data older than 3 minutes is marked stale.

## Phase 5 - Alerts and Telegram
Deliverables:

- Config-driven alert thresholds.
- Sustained breach tracking across 3 polls.
- 30-minute cooldown per ongoing alert condition.
- Telegram notification sender.
- Alert state and event APIs.

Acceptance checks:

- CPU/RAM/disk thresholds open warning and critical alerts.
- Telegram notification includes resource, severity, value, threshold, duration, and dashboard link.
- Repeated notifications are suppressed during cooldown.
- Resolution and reopen behavior works.

## Phase 6 - Auth
Deliverables:

- Single-user login form.
- Password hash verification.
- Secure session cookie.
- Auth guards for API routes and React routes.

Acceptance checks:

- Unauthenticated users cannot see telemetry or raw payloads.
- Login/logout work.
- Session secret is read from `.env`.

## Phase 7 - Dashboard UI
Deliverables:

- Attention-first overview.
- Server cards with CPU/RAM/disk/storage status.
- Application cards grouped under parent server.
- Resource drilldown pages.
- Range selector for 1h, 6h, 24h, 7d, and 30d.
- SSE live update handling.
- Collector/API health footer.
- Protected raw metrics debug panel.

Acceptance checks:

- Overview answers whether anything needs attention.
- Drilldowns show charts and parent server context for applications.
- SSE refresh updates visible status without page reload.
- Stale telemetry is visually distinct from zero usage.

## Phase 8 - Deployment docs
Deliverables:

- Production Dockerfile.
- Docker Compose example with persistent SQLite volume.
- Caddy reverse proxy snippet.
- Deployment checklist.
- Doctor command or protected doctor endpoint instructions.

Acceptance checks:

- Container exposes one internal HTTP port.
- SQLite data persists after container restart.
- Caddy can route the selected subdomain to the app.
- Doctor check passes on the droplet.

## Suggested first build order
1. Build settings, auth-safe config loading, and health endpoint.
2. Build Cloudways auth/discovery client and doctor check.
3. Add SQLite schema and snapshot repositories.
4. Add collector loop with mocked Cloudways responses in tests.
5. Add real Cloudways polling behind config.
6. Add alert evaluation and Telegram notifications.
7. Add React overview and drilldowns.
8. Package as one Docker container and deploy behind Caddy.
