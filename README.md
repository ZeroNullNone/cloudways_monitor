# Cloudways Monitor

Private monitoring dashboard for one Cloudways Flexible account on DigitalOcean. The English Graphite Terminal interface groups each server with its applications, expands all servers initially, and displays all times in UTC+8.

## Features

- Direct Cloudways v2 Access Token authentication; no OAuth exchange.
- Complete paginated `/servers` and `/apps/flexible` discovery each collection cycle.
- Runtime DigitalOcean `/monitor_targets` and `/monitor_durations` catalog.
- Per-server target/duration selection and source-time charts with gaps. Latest completed values lead; min/max/sample averages are secondary. No sample table or coverage footnotes.
- Application disk and last-hour returned-request comparison. Application bandwidth is explicitly unavailable in the verified monitoring responses.
- Small gray total RAM and data-disk capacities beside Free memory and Free Disk. These are configured capacities, without a used-memory calculation.
- Expand an application to view HTTP status/error counts, top URLs, slow PHP pages and slow SQL queries on the same page. Analysis periods: 15 minutes, 30 minutes, 1 hour and 1 day.
- Null samples remain graph gaps; summaries use the latest completed sample and its source time. Real zero is preserved.
- Telegram threshold, collection-health and recovery notifications, with persistent delivery retry.
- Protected single-user login, SQLite persistence, Docker and Caddy deployment.

## Configuration

Keep your existing deployment-local `.env`. Required Cloudways credential:

```env
CLOUDWAYS_ACCESS_TOKEN=your-platform-access-token
CLOUDWAYS_API_BASE_URL=https://api.cloudways.com/api/v2
```

Other required keys: `DASHBOARD_BASE_URL`, `SQLITE_PATH`, `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD_HASH`, and `SESSION_SECRET`. Telegram needs `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, unless `TELEGRAM_ENABLED=false`. See `.env.example` for safe placeholders. New settings read process environment; Docker Compose supplies it from the deployment-local file. Direct Python execution does not load a file.

The email/API-key exchange, task-poll tuning, configurable graph timezone and percentage-based RAM/disk settings were removed. Existing obsolete environment keys are unused. The timezone is fixed UTC+8; target/duration choices come from the API. Leave `MONITORED_SERVER_IDS` and `MONITORED_APP_IDS` blank to discover all resources.

Generate the dashboard password hash with `python -m cloudways_monitor.auth`; sign in using the original password. Generate `SESSION_SECRET` with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

## DigitalOcean alert defaults

| Target | Warning | Critical |
|---|---:|---:|
| Idle CPU | <= 20% | <= 5% |
| Free memory | <= 256 MB | <= 128 MB |
| Free Disk | <= 5120 MB | <= 2048 MB |

These are initial operational choices, not Cloudways recommendations. Memory and disk use decimal MB and explicit unit conversion; they are not used-percent estimates. Configure the corresponding `IDLE_CPU_*_PERCENT`, `FREE_MEMORY_*_MB`, and `FREE_DISK_*_MB` keys from `.env.example` if needed. The defaults work without adding these optional keys to an existing environment.

All three require `ALERT_CONSECUTIVE_POLLS` (default 3) **distinct fresh source samples**. Repeated old samples never confirm an alert. Trailing unfinished null rows are skipped. An entirely null series, unknown units and stale samples cannot resolve active alerts. Freshness allows two source intervals, with the configured stale threshold as a minimum. Numeric Warning/Critical colors show fresh threshold crossings immediately; Telegram still needs distinct-sample confirmation. Repeats have a 30-minute cooldown; escalation and recovery notify separately. Successful delivery updates `last_notification_at`; failed messages stay in the SQLite outbox for the next collection cycle, including across restarts.

An authentication failure notifies immediately and pauses Cloudways requests for five minutes. Other collection failures need three unsuccessful cycles. Restore the token locally and restart the service to clear the authentication pause. HTTP 429 honors `Retry-After` for the affected endpoint, so a Traffic limit does not pause disk/discovery/graph reads. Collection health and resource alerts remain separate. Other DigitalOcean targets stay selectable; no generic workload thresholds are fabricated for I/O, bandwidth, cache ratios or event counts.

## Run

```powershell
docker compose up -d --build --force-recreate
docker compose logs -f cloudways-monitor
```

Open the configured `DASHBOARD_BASE_URL`. The container publishes loopback `127.0.0.1:8083`; Caddy uses `reverse_proxy 127.0.0.1:8083`. Keep `SQLITE_PATH=/data/cloudways-monitor.sqlite3` for the persistent named volume. Run one Uvicorn worker. Deployment detail: [deployment.md](docs/deployment.md).

Authenticated diagnostics: `/api/collector/health`, `/api/doctor`, `/api/monitor/catalog`. Resource groups come from `/api/overview`; graphs use `/api/servers/{local_resource_id}/graph?target=...&duration=...`. `Collection details` provides sanitized source responses. The previous standalone resource pages, local range-series routes and SSE endpoint were removed.

## Data contracts and collection

Network calls do not hold the shared budget/cache lock. Matching graph requests coordinate per server/target/duration; unrelated cached reads remain responsive during application collection. The browser retains matching charts during refresh and keeps eight windows per server for range switching.

The live preview uses 60-second browser refresh, minimum 600-second freshness and 30-day snapshot retention. The collector waits its interval after completing a full cycle; upstream sample cadence and collection duration can make actual measurement updates slower. Refresh rereads the overview and graphs, and cannot force a new Cloudways sample. Current live ranges: 1 Hour, 12 Hours, 1 Day, 7 Days, 1 Month.

The catalog is cached for 10 minutes; selected graphs share a bounded cache and a global limit of 80 requests/minute across collection, discovery, task polling and UI queries. New graphs poll pending operations every 3.5 seconds in the browser, with a five-minute task timeout. Collapsed panels stop extra graph queries; offscreen panels remain expanded and defer their first graph load. Collection rotates resources each cycle to prevent permanent starvation under the budget. A failed resource does not stop its siblings.

Snapshot history keeps the last source point and diagnostics for each fetch. Complete graph windows are stored separately by server/target/duration, avoiding repeated full-window copies every minute. The browser receives source timestamps independently from fetch times. Storage remains UTC; display converts once to UTC+8.

Supported graph contracts are named Graphite `datapoints` in `[value, Unix time]` order, named `data` in `[time, value]` order, explicit timestamp/value or x/y objects, and dated category series. Unknown shapes show an English schema error with sanitized diagnostics.

Application disk comes from `/server/monitor/summary?type=db`, matching `name` to the application's `sys_user` from discovery. The Cloudways application-disk monitor's MB values convert to GB; an explicit response unit takes precedence. A server summary is shared by its applications and cached for one poll interval. Unknown identities/units, duplicate entries and null values are withheld. This avoids two slow `/app/monitor/summary` requests per application: `db` returns top database tables and `bw` actually returns file/database disk sizes.

`top_statuses` row sums are marked Partial unless the response explicitly identifies a complete total. Sorting uses the returned counts, with missing counts last. Completed analyses have a five-minute minimum refresh interval (bounded by the configured stale threshold); the page still refreshes every 60 seconds. A pending or rate-limited refresh retains a completed count with its original collection time and a Refreshing/Rate limited badge, for at most the stale threshold. Other errors and newly completed empty/invalid results do not reuse older counts. Application bandwidth is not fabricated from disk sizes: the verified endpoints return no such quantity, and tested bandwidth resource parameters return HTTP 422.

Live response headers showed `X-RateLimit-Limit: 20` for `/app/analytics/traffic`, versus 100 for normal endpoints. The smaller endpoint quota's time window is not inferred; its actual `Retry-After` governs backoff. Only authentication failure pauses all endpoints. The shared 80-request/minute client guard remains an additional conservative limit.

Server capacities use `/servers/{serverId}` and are cached for 10 minutes. DO plan values such as `intel-2GB` supply configured RAM; a positive `storage` supplies the data-disk capacity when Block Storage is attached, otherwise `volume_size` supplies it. The two disk capacities are not added. Unknown capacities remain absent. The live IMS crosscheck returned 2 GB RAM, 50 GB primary disk and 100 GB Block Storage; the UI displays 2 GB and 100 GB beside the free metrics.

Application detail analyses use `/api/applications/{local_resource_id}/analytics?category=statuses|urls|php|mysql&duration=15m|30m|1h|1d`. They load only when the application and its server are expanded. Completed results are cached for five minutes per application/category/period; pending operations reuse their task ID. Last-hour status analysis shares the collector's completed status table and pending task, so expanding it does not start another identical request. Cloudways rate limits expose their retry delay to the browser for automatic retry. Returned tables/counts are labelled Partial; empty tables show zero returned rows, while null/invalid responses show an error without fabricated counts. Slow-query text is available only behind the existing dashboard authentication.

Cloudways does not publish a complete final graph/traffic task schema. The current implementation has separate real-account checks and repeatable fixture acceptance. These checks do not constitute a production deployment or an account-wide validation of every target and duration.

## Repeatable verification

For a local **real-data preview**, install development dependencies and run `python -m scripts.live_preview`. It loads `.env` inside the process without printing or editing it, uses the existing dashboard login, listens at `http://127.0.0.1:8084`, stores data separately under `.tmp/live-preview/`, and disables Telegram delivery/alert evaluation. Stop with Ctrl+C. This is separate from the synthetic acceptance server below; do not run both on the same port.

The real-account check found four servers, 19 applications, 14 targets and five durations. JSON graph `content`, JSON-encoded operation parameters and traffic `Count` columns are supported. [Confirmed target definitions](docs/monitor-targets.md) supply missing server-graph units; Free memory source bytes convert once to display MB. Memcached Hit Rate remains unconfirmed. Application disk identity was crosschecked against the matching application total, and a production application's status rows returned valid request counts. See [verification](docs/redesign-verification.md) for current evidence and API limitations.

Use a project virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/pytest-run --junitxml=.tmp/e2e/results.xml
ruff check cloudways_monitor tests scripts
cd frontend
npm ci
npm run build
```

Browser acceptance uses an isolated fixture server, with no `.env` loading and no real Cloudways or Telegram requests:

```powershell
.venv\Scripts\python.exe -m scripts.e2e_preview
```

Open `http://127.0.0.1:8084`, sign in as `admin` / `correct-password`, and stop with Ctrl+C. Follow [browser-acceptance.md](docs/browser-acceptance.md) or run `scripts/browser_acceptance.mjs` through the supported Codex Browser runtime. Measurements and names in this preview are synthetic. Verification record: [redesign-verification.md](docs/redesign-verification.md).
