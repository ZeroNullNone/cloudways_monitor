# Context

## Glossary

### Cloudways API
The authoritative external source for server and application telemetry in the first version of this monitoring dashboard.

### Telemetry
Time-varying measurements about a Cloudways-hosted server or application, such as CPU, RAM, storage, bandwidth, traffic, and service health.
### Server Telemetry
Telemetry describing infrastructure-level usage and health for a Cloudways server.

### Application Telemetry
Telemetry describing usage and health for an application hosted on a Cloudways server.

### Live Telemetry
Telemetry that is refreshed on a short polling interval and displayed with enough freshness context for monitoring decisions.

### Stale Telemetry
Telemetry whose age exceeds the dashboard's freshness threshold and should be visually marked as no longer live.
### Metric Snapshot
A captured set of telemetry values for one server or application at a specific point in time.

### Retention Window
The period during which metric snapshots remain available for dashboard trends, comparisons, and incident review.
### Monitored Account
The single Cloudways account whose servers and applications are included in the dashboard.
### Dashboard Host
The external compute environment where the monitoring dashboard runs independently from the Cloudways servers it monitors.
### Reverse Proxy
The existing edge service that routes a public subdomain to the dashboard's internal application server.

### Caddy
The reverse proxy used on the Dashboard Host to expose Dockerized Python server projects through subdomains.
### Dashboard API
The Python HTTP service that exposes dashboard data and live telemetry updates to the browser.
### Dashboard UI
The browser-based interface used to inspect live and historical Cloudways telemetry.
### Live Update Stream
The one-way browser subscription that delivers refreshed telemetry from the Dashboard API to the Dashboard UI.
### Dashboard User
The single personal user allowed to access the dashboard.

### App-Level Authentication
Authentication enforced by the Dashboard API and Dashboard UI before telemetry is visible.
### Alert
A threshold-based signal that telemetry has crossed a configured condition requiring attention.

### Telegram Notification
An alert delivery message sent to the dashboard owner through Telegram.
### Sustained Breach
An alert condition that remains true across enough consecutive polling intervals to be treated as actionable.

### Alert Cooldown
A suppression period after an alert notification during which repeated notifications for the same ongoing condition are not sent.
### Environment File
The root `.env` file that provides deployment-specific secrets and configuration to the dashboard runtime.

### Environment Example
The root `.env.example` file that documents required configuration keys without containing real secrets.
### Alert Rule
A configured threshold condition that determines when telemetry should produce an alert.
### History Range
A preset time window used by the dashboard to display historical telemetry.
### Overview
The dashboard's first screen, used to assess whether any monitored server or application currently needs attention.

### Drilldown
A focused dashboard view for inspecting one server or application in more detail.
### Read-Only Monitoring
A dashboard capability boundary where telemetry is collected and displayed, but Cloudways resources are not changed by the dashboard.
### Resource Discovery
The process of finding available Cloudways servers and applications from the Cloudways API.

### Monitoring Allowlist
Optional configuration that limits which discovered servers or applications are included in monitoring.
### Telemetry Collector
The component that periodically calls the Cloudways API, normalizes telemetry, stores metric snapshots, and evaluates alert rules.
### Last Known Telemetry
The most recent successfully collected telemetry for a server or application, retained for display when fresh telemetry cannot be collected.

### Collector Health
The dashboard's view of whether telemetry collection is currently succeeding, delayed, rate-limited, or failing.
### Chart Aggregation
The process of grouping raw metric snapshots into time buckets for readable and efficient historical charts.
### Snapshot Expiration
The removal of metric snapshots older than the configured retention window.
### Doctor Check
A diagnostic capability that verifies required dashboard configuration and external integrations are working.
### Monitored Resource
A Cloudways server or application included in dashboard monitoring.

### Resource Type
The category of a monitored resource, such as server or application.
### Core Metric
A commonly queried telemetry value stored in a structured column for charts and alerts.

### Raw Metric Payload
Provider-specific telemetry details stored alongside core metrics for inspection or future use.
### Attention-First Overview
An overview layout that prioritizes active alerts and stale telemetry before routine capacity and traffic information.
### Parent Server Context
The server-level telemetry shown alongside an application drilldown to explain the infrastructure conditions around that application.
### Normalized Metric Label
A user-facing metric name that describes telemetry consistently without exposing provider-specific field names.

### Raw Metrics Debug Panel
A protected diagnostic view that exposes raw provider-specific metric payloads for troubleshooting.
### Dashboard Base URL
The public URL used to link Telegram notifications back to the dashboard.

### Actionable Notification
A notification that includes enough context and a dashboard link for the owner to investigate the affected resource directly.
