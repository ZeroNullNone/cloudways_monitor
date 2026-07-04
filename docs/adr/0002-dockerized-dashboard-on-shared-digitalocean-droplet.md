# 0002. Run the dashboard as Docker services on a shared DigitalOcean droplet

Date: 2026-07-04

## Status
Accepted

## Context
The dashboard should run outside the monitored Cloudways infrastructure. The available deployment target is an existing DigitalOcean droplet that already hosts other small projects using Docker, with each project exposed through a subdomain by Caddy. Existing projects run as Python servers behind that Caddy routing layer.

## Decision
Package the monitoring dashboard as Docker services intended to run on the shared DigitalOcean droplet as a Python server behind the existing Caddy subdomain routing setup.

## Consequences
The project should include a Docker-friendly runtime and configuration model. Runtime secrets such as Cloudways API credentials should be injected through environment variables or Docker secrets rather than committed files.

The dashboard must avoid assuming it owns the host, the public ports, or the reverse proxy. It should expose an internal application port that can be routed by Caddy.
