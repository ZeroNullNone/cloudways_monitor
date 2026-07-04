# 0011. Store metric snapshots with core columns and raw payloads

Date: 2026-07-05

## Status
Accepted

## Context
The dashboard needs to chart and alert on common metrics such as CPU, RAM, disk or storage, bandwidth, traffic, freshness, and API health. Cloudways may also expose provider-specific details that are useful to retain but should not force a brittle schema for every field.

## Decision
Use a hybrid metric snapshot model: store normalized core metric columns for common chart and alert queries, and store the raw provider-specific metric payload alongside them as JSON.

## Consequences
Core dashboards and alerts can use efficient structured queries. Raw Cloudways details remain available for debugging, future UI additions, or schema evolution.

The application must treat raw payload fields as provider-specific and avoid making critical v1 behavior depend on undocumented fields.
