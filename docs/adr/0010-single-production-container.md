# 0010. Deploy v1 as a single production container

Date: 2026-07-04

## Status
Accepted

## Context
The dashboard will run on a shared DigitalOcean droplet that already hosts small Dockerized Python server projects behind Caddy. The v1 system includes a FastAPI backend, a React/Vite frontend, SQLite metric history, app-level authentication, and Telegram notifications.

## Decision
Deploy v1 as one production container. The container should run the FastAPI/Uvicorn application, serve the built React frontend, and use a mounted volume for SQLite data.

## Consequences
A single production container keeps deployment simple on the shared droplet and minimizes reverse proxy configuration. The app should expose one internal HTTP port for Caddy.

Local development may still use separate backend and frontend development servers if that improves iteration speed.
## Collector placement
The Telemetry Collector should run inside the same v1 production container. It should still be implemented as a distinct component so it can be moved to a separate worker process or container later if needed.
