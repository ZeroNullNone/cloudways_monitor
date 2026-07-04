# 0004. Use React and Vite for the dashboard UI

Date: 2026-07-04

## Status
Accepted

## Context
The dashboard UI is expected to show live and historical server and application telemetry, including metric cards, charts, freshness states, filters, and drilldowns.

## Decision
Use React with Vite for the dashboard UI.

## Consequences
A separate React UI gives enough structure for a richer monitoring interface while keeping frontend development fast. Vite provides a lightweight development server and production build flow.

The project should keep the frontend focused on monitoring workflows and avoid turning v1 into a broad admin portal.
