# DigitalOcean monitoring definitions

Updated 2026-10-06 after the owner supplied Cloudways definitions and confirmed raw units. Targets are still discovered from `/monitor_targets`; this file does not add unavailable metrics to the catalog.

| Target | Source unit when omitted by API | Display unit | Meaning |
|---|---|---|---|
| Idle CPU | % | % | Free CPU, not used CPU |
| Free Disk | GB | GB | Free space on the data disk |
| Reads per second | ops/s | ops/s | Disk read operations per second |
| Writes per second | ops/s | ops/s | Disk write operations per second |
| Free memory | B | MB | Unused memory; decimal conversion, 1 MB = 1,000,000 bytes |
| Incoming network traffic | Mbps | Mbps | Megabits per second |
| Outgoing network traffic | Mbps | Mbps | Megabits per second |
| Monthly Bandwidth | GB | GB | Monthly cumulative usage, resetting each month |
| Memcached Fill Ratio | % | % | Used cache capacity |
| Memcached Hit Rate | Unconfirmed | Unconfirmed | Recommend percentage if Cloudways confirms a 0–100 hit ratio |
| Varnish Hit Rate | % | % | Cache hit percentage |
| Varnish Nuked | items | items | Evicted cache items |
| Auto-healing Restarts | restarts | restarts | Service restart count |
| MySQL Connections | connections | connections | Established connections |

The user-confirmed bytes for Free memory describe the actual raw API quantity, while the Cloudways help text describes its MB presentation. Conversion happens once in normalization, so summaries, chart axes and alert comparisons use the same value. Explicit units returned by Cloudways take precedence over these target definitions. Unknown targets retain unconfirmed-unit warnings. Per-series `source_unit` and `unit_source` identify the conversion provenance.

Memcached's official definition of hit rate is `get_hits / (get_hits + get_misses)`; a percentage representation multiplies by 100. However, this does not prove the scaling of Cloudways' raw endpoint, and the owner's text describes hits. The representative server's seven-day values are all zero, so no scale can be established empirically. Keep this unit unconfirmed until the owner confirms Cloudways' displayed unit. Sources: [Memcached documentation](https://github.com/memcached/memcached/wiki/ServerMaint), [Cloudways cache monitoring](https://support.cloudways.com/en/articles/5121349-how-to-monitor-the-caching-services-of-a-server), [Cloudways memory monitoring](https://support.cloudways.com/en/articles/5121308-how-to-monitor-the-ram-usage-of-a-server).

## Seven-day live check

All 14 catalog targets were queried for one representative DigitalOcean server over `7 Days`. Most series contained 168 valid hourly samples; network derivatives contained 167. The following series were entirely zero: Memcached Fill Ratio, Memcached Hit Rate, Varnish Hit Rate, Varnish Nuked, and MySQL Connections. Auto-healing Restarts contained one nonzero sample. This is a one-server observation, not an account-wide service-state conclusion.

Zero remains valid. Null remains missing and never becomes zero. Following the subsequent clarification that the final row is an unfinished null placeholder, summaries select the last completed sample within the returned window and display its actual source time. Chart gaps stay null. Entirely null series have no completed summary. Freshness allows two source intervals, with the configured stale threshold as a minimum. Historical event/counter samples are not summed into fabricated seven-day totals. Monthly counters can reset within a seven-day window crossing month boundaries.

Existing pressure-alert defaults remain operational choices and are separate from the guidance in metric descriptions. This unit update enables valid comparisons for confirmed server metrics; local live preview still disables alert evaluation and Telegram delivery.

## Application usage

Application units were investigated independently of the server graph definitions. `/server/monitor/summary?type=db` returns per-application disk datapoints keyed by system user. Match `name` to `/apps/flexible`'s `sys_user`, within the parent server; never sum other applications or top database tables.

Cloudways describes its application disk monitor in MB in the [official disk usage guide](https://support.cloudways.com/en/articles/5124939-how-to-view-application-disk-usage). The real matching entry was 606.7 MB; the application's separate disk total was 621260.8 KB, which matches exactly after division by 1024. The dashboard uses the monitor's MB value and the project's decimal MB-to-GB convention. Explicit response units override this definition. The source timestamp is retained and shown in application details; refreshing does not change that timestamp. The observed source disk summary updates daily.

`/app/monitor/summary?type=db` is a top-table list. `type=bw` returns `app_home`, `app_mysql` and `total`, which are disk consumption rather than transferred bandwidth. Those paths are no longer used for application totals. The documented traffic `top_statuses` table returns Status/Count rows; sums are labelled Partial and can be ranked as returned counts. `top_urls` returned URL/Count, without bandwidth. Tested `top_bandwidth` and `bandwidth` resource parameters returned HTTP 422. Application bandwidth remains Unavailable; no zero or guessed value is displayed.

## Server capacities and inline application analysis

Configured RAM comes from the DigitalOcean `instance_type` in `/servers/{serverId}`, including prefixed values such as `intel-2GB`. Configured data-disk capacity uses a positive `storage` value when Block Storage is attached, otherwise `volume_size`. The owner-requested capacity label is small gray text beside the free metric; no used-memory value or percentage is derived. The IMS live detail returned 2 GB RAM, a 50 GB primary volume and 100 GB attached storage, so the displayed data-disk total is 100 GB. The [official disk guide](https://support.cloudways.com/en/articles/5121323-how-to-monitor-the-disk-usage-of-a-server) confirms the Block Storage monitoring scope and explains why filesystem capacity can differ from plan capacity.

Application details expose four on-demand analyses with independent 15m/30m/1h/1d periods. `top_statuses` supplies HTTP status/count rows and returned 4xx/5xx counts; `top_urls` supplies URL/count rows. `/app/analytics/php?resource=slow_pages` returned URL/Occurrences/Max Time/Avg. Duration headers. `/app/analytics/mysql?resource=slow_queries` returned Query/Execution Time/Occurrences headers. Time cells and column labels are preserved as returned; no unverified conversion is applied. Summaries count returned rows, not whole-application CPU/RAM usage or a complete error rate. Empty tables are genuine zero returned rows; missing/null/invalid tables have no fabricated summary.
