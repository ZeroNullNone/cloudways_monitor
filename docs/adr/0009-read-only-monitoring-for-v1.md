# 0009. Keep v1 read-only

Date: 2026-07-04

## Status
Accepted

## Context
The dashboard will hold Cloudways API credentials and expose operational information through a personal authenticated UI. Cloudways APIs can support operational actions beyond monitoring, but the first version is intended to monitor usage and health.

## Decision
Keep v1 as read-only monitoring. The dashboard may collect, store, display, and alert on telemetry, but it must not restart services, clear caches, scale servers, change applications, or mutate Cloudways resources.

## Consequences
The v1 integration should use only the Cloudways API operations needed for discovery and telemetry collection. The UI should not present operational action buttons.

If control actions are added later, they should require a separate design decision covering confirmations, audit logging, authorization, and failure recovery.
