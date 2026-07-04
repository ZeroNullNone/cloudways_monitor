# 0005. Use server-sent events for live dashboard updates

Date: 2026-07-04

## Status
Accepted

## Context
The dashboard needs to push refreshed telemetry from the backend to the browser. The first version refreshes Cloudways telemetry on a 60-second polling interval and does not require bidirectional real-time control messages from the browser.

## Decision
Use server-sent events for the v1 live update stream.

## Consequences
Server-sent events keep the live update path simple and work well for one-way metric updates. They are easier to operate behind a reverse proxy than a WebSocket setup for this use case.

If the dashboard later needs bidirectional interactions such as live commands, collaborative views, or high-frequency streams, WebSockets can be reconsidered.
