# 0008. Use root environment files for runtime configuration

Date: 2026-07-04

## Status
Accepted

## Context
The dashboard needs Cloudways API credentials, app authentication credentials, session secrets, Telegram notification settings, and runtime configuration. The deployment style is Dockerized Python services on a shared DigitalOcean droplet.

## Decision
Use a root `.env` file for real runtime configuration and a root `.env.example` file to document the required keys.

## Consequences
The `.env` file must be treated as deployment-local secret material and must not be committed. The `.env.example` file should contain variable names and safe placeholder values only.

The backend should read Cloudways API email/key credentials from configuration, obtain OAuth tokens at runtime, and cache tokens internally rather than requiring a static access token in `.env`.
