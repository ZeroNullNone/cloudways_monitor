# 0001. Use SQLite for v1 metric history

Date: 2026-07-04

## Status
Accepted

## Context
The dashboard needs to retain metric snapshots for server and application telemetry so it can show trends, spikes, and recent history. The first version is scoped as a single custom dashboard for Cloudways monitoring, not a multi-tenant analytics platform.

## Decision
Use SQLite as the v1 storage engine for metric history.

## Consequences
SQLite keeps deployment simple and avoids requiring a separate database service. It is suitable for local retention of 60-second snapshots for a small number of Cloudways servers and applications.

If the dashboard later needs multi-user scale, larger retention windows, high write concurrency, or advanced time-series queries, the storage layer may need to move to Postgres or a time-series database.
## Retention cleanup
Metric snapshots older than 30 days should be deleted in v1. The system should not downsample older data until a later version explicitly adds longer-range historical analysis.
