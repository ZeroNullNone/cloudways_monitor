# 0007. Use threshold alerts with Telegram notifications

Date: 2026-07-04

## Status
Accepted

## Context
The dashboard needs to call attention to operational conditions such as high CPU, high RAM usage, low storage, high bandwidth or traffic usage, stale telemetry, and Cloudways API failures. The dashboard owner wants Telegram notifications in addition to visible dashboard alert states.

## Decision
Use threshold-based alerts in v1 and deliver alert notifications through Telegram.

## Consequences
Alert rules should be explicit and configurable. The first version should avoid anomaly detection or predictive alerting until enough historical telemetry exists to justify those models.

Telegram bot credentials and chat identifiers must be supplied through deployment configuration and must not be committed to the repository.
## Alert noise control
An alert should notify only after the threshold is breached for 3 consecutive polling intervals. After a Telegram notification is sent, repeat notifications for the same ongoing condition should be suppressed for 30 minutes unless severity changes or the alert resolves and later reopens.
## Configuration ownership
Alert rules are configured outside the UI in deployment configuration for v1. The dashboard UI should display alert state and threshold context, but it should not edit alert rules until a later version explicitly adds configuration management.
## Cloudways API failure behavior
When the Cloudways API is unavailable, rate-limited, or otherwise fails, the dashboard should continue serving the last known telemetry, mark affected telemetry as stale, expose collector health, and notify through Telegram only after sustained failure. Missing telemetry must not be treated as zero usage.
## Default alert thresholds
The v1 default thresholds are CPU warning/critical at 80%/95%, RAM warning/critical at 80%/95%, disk or storage warning/critical at 80%/90%, stale telemetry after the effective freshness window, and sustained Cloudways API failure after 3 failed polls. Bandwidth and traffic are charted by default without a default alert threshold.
## Actionable Telegram notifications
Telegram alert messages should include the resource name, severity, breached metric, current value, threshold, breach duration, and a link to the relevant dashboard drilldown using the configured dashboard base URL.
