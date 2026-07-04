# 0003. Use FastAPI and Uvicorn for the dashboard API

Date: 2026-07-04

## Status
Accepted

## Context
The dashboard needs a Python server that can expose JSON endpoints, serve live telemetry updates to the browser, poll Cloudways API endpoints, and run cleanly in Docker behind Caddy.

## Decision
Use FastAPI for the dashboard API and Uvicorn as the ASGI server.

## Consequences
FastAPI provides a straightforward route model, typed request and response handling, and native compatibility with async Cloudways API polling. Uvicorn provides the ASGI runtime expected by FastAPI.

The project should avoid adding a second backend framework unless a concrete need emerges.
