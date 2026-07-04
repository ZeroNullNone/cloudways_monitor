# 0006. Use simple single-user app-level authentication

Date: 2026-07-04

## Status
Accepted

## Context
The dashboard exposes operational telemetry for Cloudways servers and applications. It is intended for personal use by one owner and will be reachable through a subdomain behind Caddy.

## Decision
Require simple app-level authentication for a single personal dashboard user.

## Consequences
The application should prevent telemetry from being visible until the user is authenticated. The first version does not need team accounts, roles, or organization management.

Credentials and session secrets must be provided through deployment configuration, not committed to the repository. If additional users or roles are needed later, the authentication model should be revisited.
