# Redesign verification

Updated: 2026-10-07 (UTC+8). The implementation/browser acceptance below uses synthetic responses. User-authorized live API checks are recorded separately below. Deployment `.env` was never displayed or modified; only the live checking/preview processes loaded it in-process after explicit authorization.

## Results

| Check | Result | Evidence |
|---|---|---|
| Python integration and retained checks | 82 passed after production-update checks | [JUnit results](verification/results.xml) |
| Production-update preparation | 8 passed with simulated Docker/Git boundaries | [Update results](verification/production-update.json) |
| Sequential small-host build | 9 selected deployment checks passed | [Deployment results](verification/sequential-build-after.xml) |
| Browser acceptance | 29 passed | [Browser results](verification/capacity-analysis-browser/browser-results.json) |
| Production frontend build | Passed, including TypeScript | `npm run build` |
| Python lint | Passed | `ruff check cloudways_monitor tests scripts` |
| Desktop visual inspection | Passed at 1280×900 | [Screenshot](verification/capacity-analysis-browser/desktop.png), [full page](verification/capacity-analysis-browser/desktop-full.png) |
| Mobile visual inspection | Passed at 390×844 | [Screenshot](verification/capacity-analysis-browser/mobile.png) |
| Telegram delivery capture | Local fixture only | [Captured messages](verification/telegram-capture.json) |

The Python run includes redesign integration scenarios and retained authentication, storage, health, alert API and deployment checks. Failure scenarios were specified before implementation. Coverage includes paginated discovery, direct bearer authentication, asynchronous task results, invalid schemas, units and timestamps, null/zero distinctions, request throttling, partial resource failures, distinct-sample alert confirmation, stale samples, recovery, failed Telegram delivery retry, restart persistence, authentication-health alerts and secret redaction. Obsolete OAuth, SSE and removed-route tests were replaced by current end-to-end contracts.

The current browser run exercised the built frontend against local FastAPI routes with a mock upstream transport. Both desktop and mobile screenshots were visually inspected. Latest completed values are visually dominant; sample tables and the two graph footnotes are absent. Chart gaps remain visible. Application disk values, actual zero, Partial request labels and Unavailable bandwidth are verified. All server groups initially expand, with applications alongside their server graph on desktop and below it on mobile.

The capacity/application-analysis addition has 15 scenarios specified before their corresponding implementation. Eleven initial failures and two subsequent quota/reuse failures were reproduced through collection and protected HTTP routes with a mock upstream. Evidence: [capacity failures](verification/capacity-analysis-before.xml) and [quota failures](verification/capacity-analysis-quota-before.xml). The browser checks gray secondary capacity labels, four inline analysis categories, analysis-period switching, actual empty slow-page results and removal of the polling component when a server collapses. These additions preserve the terminal theme and English/UTC+8 presentation.

## Production-update preparation

Eight additional cases exercise the real Bash deployment script and protected alert routes using isolated fixtures. The online backup runs against a real SQLite WAL database with an open writer; the snapshot includes committed rows, excludes an uncommitted row and passes SQLite's integrity check. Git, Compose configuration, image build, application configuration and backup failures abort before the script recreates the service. First installation skips the backup. Removed alert rules do not appear in current counts or badges; stored alert events remain available.

Docker and Git commands are simulated at the process boundary. No Docker daemon, production server, real credential file or external API is used by these tests. The local Docker daemon was unavailable, so no actual Docker image build or container recreation was verified. The full suite passed 82 checks with no skips; the existing browser/frontend verification remains the latest UI evidence because these preparation changes do not change the frontend. Repeat with `tests/test_production_update_e2e.py`; cases produce machine-readable results under `.tmp/e2e/production-update-*.json`. Summary: [update results](verification/production-update.json). Eight initial failures were recorded before the fixes in [pre-change results](verification/production-update-before.xml).

The owner's production trace showed pip and npm installation overlapping for more than 30 minutes on a 1 CPU, 1 GB shared host. Resource exhaustion was suspected but could not be confirmed from host diagnostics. A deployment-file contract first failed because frontend artifacts were copied after pip installation. The copy now precedes pip, creating a stage dependency that makes frontend completion a prerequisite for backend installation. npm/pip installation logs and plain Compose progress are enabled. The file contract and eight existing isolated deployment cases pass: [before](verification/sequential-build-before.xml), [after](verification/sequential-build-after.xml). This is a pressure reduction and deployment-script verification, not a measured memory benchmark or proof that the production stall is resolved. The latest full-suite result above precedes this build-order change.

## Capacity and application analysis live check

The running real-data preview's protected overview returned capacity values for all four servers. IMS returned `instance_type=intel-2GB`, `volume_size=50` and `storage=100`. The UI shows Total 2 GB beside Free memory and Total 100 GB beside Free Disk, using the configured Block Storage data disk rather than adding the primary and attached volumes. Both `strorge=false` and `strorge=true` returned the same IMS Free Disk series in the scope probe. The [official disk guide](https://support.cloudways.com/en/articles/5121323-how-to-monitor-the-disk-usage-of-a-server) identifies attached Block Storage as the monitored data disk. Capacities are plan/configuration values, not exact filesystem totals or derived used memory.

Four application-analysis categories completed successfully through the preview's protected routes for a representative IMS application: status codes (two returned rows, three requests, two 4xx responses, zero 5xx), URLs (three returned rows), PHP slow pages (empty table) and SQL slow queries (empty table). These are current-window observations for one application. The initial live run encountered a real Traffic quota; completed status tables and pending tasks now share the collector cache, while other analyses honor the provider's retry delay. Evidence contains counts and headers only, without actual URLs, queries or credentials: [API contracts](verification/capacity-analysis-contract-live.json), [preview results](verification/capacity-analysis-preview-live.json).

The live PHP and SQL endpoints also accepted `1d` through the protected preview API. The one-day PHP result contained one returned slow page; the one-day SQL result was empty. [Daily analysis evidence](verification/capacity-analysis-daily-live.json) records these observations separately from the hourly checks.

## Reproduce

See [README.md](../README.md#repeatable-verification) for installation, build and Python checks, and [browser-acceptance.md](browser-acceptance.md) for the isolated browser server and acceptance helper. Machine-readable results and screenshots are stored in `docs/verification/`; repeat runs write to `.tmp/e2e/`.

## Verification boundary

The implementation/browser acceptance run did not access Cloudways. No real Telegram message was sent. No Docker image build or production deployment was performed. The user's access token remains in their untouched deployment-local `.env`.

The API documentation does not provide a complete final graph/application task payload schema. The subsequent live checks confirm representative graph contracts, application disk identities and returned request counts. Application bandwidth remains unavailable in the tested responses, and Memcached Hit Rate units remain unconfirmed. Unknown data is withheld and reported through sanitized collection diagnostics; it is never replaced by fabricated values. No production deployment or real Telegram delivery has been performed.

After deployment, inspect `/api/doctor`, `/api/monitor/catalog`, and each server's `Collection details`. Confirm representative target/duration charts against Cloudways and confirm a Telegram delivery using an actual operational alert. Catalog choices are dynamic: the 15 targets and six durations asserted here are the fixture's current catalog, not hardcoded production choices.

## Subsequent live API check

### After permissions were updated

The subsequent owner-provided units are implemented in [monitor-targets.md](monitor-targets.md). Confirmed server targets no longer require units to be repeated in each API response. Raw Free memory bytes convert once to MB; unknown targets and Memcached Hit Rate remain unconfirmed. Eight parameterized graph API scenarios and one unknown-target scenario were written before the unit implementation, bringing the suite to 44 passing checks. The frontend production build passed after adding English metric descriptions. Seven-day live results for one representative server are recorded in [monitor-targets-7days.json](verification/monitor-targets-7days.json). Earlier unit-missing results below describe the state before the owner confirmation.

The updated token successfully discovered **four DigitalOcean servers and 19 applications**, with **14 targets and five durations**. The earlier 403 results below are historical and have been resolved.

Live JSON graph results use a JSON-encoded `content` field; this is now decoded and preserves null samples. Traffic tables use a `Count` header, now supported while remaining explicitly partial. Discovery now persists all identities before slow telemetry calls so all applications appear immediately. Three additional integration scenarios reproduced these failures before fixes; the updated total is 35 passing checks. [Current live results](verification/live-api-after-permissions.json) supersede the initial permissions-error results below.

The initial check after permissions changed parsed three representative server graphs (Idle CPU, Free memory, Free Disk) but initially warned about missing units and slower disk cadence. These issues were resolved by the later unit/completed-sample changes. Application `type=db` returned a database-table size list; `type=bw` returned `app_home`, `app_mysql`, and `total` without units. Those results were not treated as bandwidth or guessed GB totals. The subsequent application fix below replaces these incorrect endpoint interpretations. A real traffic operation completed with `top_statuses` and `Status`/`Count` headers; the initial representative staging application's table was empty.

The real-data local preview uses the existing dashboard credentials, a separate SQLite database and disabled Telegram delivery/alert evaluation. Run `python -m scripts.live_preview`; stop with Ctrl+C. The earlier browser screenshots remain synthetic and must not be presented as live-account screenshots.

### Before permissions were updated

The user explicitly authorized loading `.env` in the checking process without displaying or modifying it. Real read-only HTTP requests returned:

| Endpoint | Status | Result |
|---|---|---|
| `/monitor_targets` | 200 | Catalog available |
| `/monitor_durations` | 200 | Catalog available |
| `/servers` | 403 | `insufficient_scope` |
| `/apps/flexible` | 403 | `insufficient_scope` |

Both discovery endpoints returned `This token does not have access to this endpoint`. No resource count or telemetry value can be confirmed with this token's current scopes; this is not an empty-account result. No Telegram request was made. Safe machine-readable evidence: [live-api-results.json](verification/live-api-results.json).

In Cloudways Platform → profile menu → API Integration, inspect the token's scopes. Read-Only Access is documented as appropriate for monitoring dashboards. If using Limited Access, allow GET `/servers`, `/apps/flexible`, `/monitor_targets`, `/monitor_durations`, `/server/monitor/detail`, `/app/monitor/summary`, `/app/analytics/traffic`, and `/operation/{id}`. Edit the token's permissions if available, or create a suitable read-only token and replace its value locally before retrying. Source: [Cloudways Access Token documentation](https://support.cloudways.com/en/articles/5136065-how-to-create-and-manage-cloudways-api-access-tokens).

## Latency, completed samples and number colors

A real timing probe reproduced cached reads waiting behind application network calls. Three new integration scenarios were written before fixes: cached UI requests remain responsive while an application transport is blocked; trailing null plus normal 30-minute disk cadence stays fresh but old samples become stale; entirely null series produce no completed values. All 47 checks pass.

| Same probe | Before | After |
|---|---:|---:|
| Cached catalog | 28.592 s | 0.002 s |
| First 12-hour graph | 13.227 s | 0.750 s |
| Repeated 12-hour graph | 13.110 s | 0.006 s |
| Seven-day graph | 7.261 s | 0.711 s |
| Direct Cloudways graph | 0.684 s | 0.793 s |

The global lock covered network I/O, including unrelated cached requests. Network I/O now runs outside the short budget lock, with per-key graph coordination preserving deduplication. Rate-limit backoff and existing tests remain intact. Machine-readable timings: [before](verification/performance-before.json), [after](verification/performance-after.json). These are one-run measurements under live collection, not a latency guarantee.

The latest null row was confirmed in a real 12-hour graph, at five-minute source intervals. Completed summaries now skip trailing placeholders, show their actual source time, and keep the original null chart gaps. Normal freshness is max(configured minimum, two returned intervals). Real preview verified fresh CPU, memory and disk values after this change. Existing screenshots/browser results predate this semantic change.

Frontend numbers now show Warning in amber and Critical in red for configured rules, with text badges on server summaries. Stale samples are excluded from current numeric severity. Threshold colors are immediate; confirmed Telegram events still need distinct fresh samples. Charts retain matching windows during refresh and use an eight-window browser cache per server. Production build passed; this update does not claim a new browser visual acceptance run.

## Application usage and Latest hierarchy

Nine additional end-to-end cases were specified before the application changes. Eight initially failed and reproduced missing disk values, wrong identity/unit handling and redundant queries; their [failure artifact](verification/application-before.xml) is retained. The application-only suite had 56 passing checks, increasing to 59 after the endpoint-quota fix below. The additional cases cover matching exact system users, zero and null disk values, unknown units, duplicate/incorrect identities, negative values, sharing one summary per parent server, refusing an old disk value after a new null result, and retaining a pending request refresh's original completed fetch time until expiry.

Live `/server/monitor/summary?type=db` entries match `/apps/flexible`'s `sys_user` and report application disk in the MB units used by Cloudways' disk monitor. A production application's 606.7 MB entry matched its separate disk total, 621260.8 KB / 1024. This source is cached once per parent server per poll interval, replacing two slow app summary calls per application. The measured app summary calls took 9.093 s and 8.391 s; the corresponding server summary took 0.766 s. These are individual-call observations, not a promised collection duration.

Real production traffic completed with JSON-encoded operation parameters and Status/Count rows. Completed results remain labelled Partial; ranking uses returned counts. Pending/rate-limited refreshes retain their completed count and original collection time, show Refreshing/Rate limited, and expire after the stale threshold. No old value is reused after other errors or newly completed missing results.

The local real preview discovered four servers and 19 applications. After quota recovery, all 19 applications had disk values and completed request counts, with no pending tasks or disk diagnostics; collection health was ok. Nine request counts were nonzero and ten were real zero. Full counts and pending states are recorded in [application-usage-live.json](verification/application-usage-live.json); this is aggregate evidence without credentials or application identities. Its overview read took 0.031 s during live collection. The check was at 2026-10-07 00:23 UTC+8.

The documented top_urls response contained URL/Count only. Read-only probes with top_bandwidth and bandwidth parameters both returned HTTP 422. These responses do not supply application bandwidth; the UI displays Unavailable and removes unsupported bandwidth ranking. Disk data is never relabelled as bandwidth. See [metric definitions](monitor-targets.md#application-usage) and the linked Cloudways guides for source context.

The new 20-check browser run verifies the larger Latest value, small secondary statistics, removed samples/footnotes, disk values, Partial/Unavailable labels, request ranking, duration switching, desktop/mobile layout and absence of console errors. Screenshots in application-usage-browser are synthetic acceptance evidence, separate from the live-account counts. The real preview remains on 8084 with original dashboard credentials and Telegram disabled.

### Traffic-specific quota

Continued live collection exposed HTTP 429 from `/app/analytics/traffic`. Its response headers declared limit 20, remaining 0, Retry-After 88 seconds; adjacent discovery/disk endpoints returned 200 with limit 100. The time window for the smaller quota is not inferred. Evidence: [rate headers](verification/application-rate-headers.json).

Three failure-first end-to-end scenarios reproduced excessive new analysis creation, Traffic backoff wiping out unrelated graph/disk metrics, and missing traffic status after cache expiry. All initially failed; [failure artifact](verification/application-rate-before.xml). Backoff is now scoped to each endpoint, authentication pause remains global, completed traffic analyses are reused for five minutes, and pending/limited refreshes retain only unexpired completed values with original collection times. All 59 checks pass. The frontend build and Python lint pass; the final browser run passes 20 checks.
