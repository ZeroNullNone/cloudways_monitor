# Cloudways Monitor

Private live dashboard for monitoring one Cloudways account. It discovers Cloudways servers and applications through the Cloudways Platform API v2, stores resource history in SQLite, shows a protected dashboard, and supports threshold-based Telegram alerts.

## Features

- Cloudways v2 OAuth/API integration.
- Server and nested application discovery.
- Single-user dashboard login.
- SQLite-backed resource and metric history.
- Collector health and protected doctor diagnostics.
- Threshold alerts with optional Telegram notifications.
- Docker deployment behind Caddy or another reverse proxy.

## Requirements

- Docker and Docker Compose for the recommended deployment path.
- A Cloudways API key.
- Python 3.12+ if running tests or helper commands locally.

## First Setup

Create your runtime environment file:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and fill in at least these values:

```env
DASHBOARD_BASE_URL=https://your-monitor-subdomain.example.com
CLOUDWAYS_EMAIL=you@example.com
CLOUDWAYS_API_KEY=your-cloudways-api-key
CLOUDWAYS_API_BASE_URL=https://api.cloudways.com/api/v2
DASHBOARD_USERNAME=admin
SQLITE_PATH=/data/cloudways-monitor.sqlite3
```

Leave these blank to monitor every discovered Cloudways server and application:

```env
MONITORED_SERVER_IDS=
MONITORED_APP_IDS=
```

Or set comma-separated Cloudways IDs to restrict monitoring:

```env
MONITORED_SERVER_IDS=687506,706980,794126,1366047
MONITORED_APP_IDS=
```

## Generate Login Secrets

Generate a Docker-safe dashboard password hash:

```powershell
python -m cloudways_monitor.auth
```

Enter your plaintext dashboard password when prompted. Copy the printed hash into `.env`:

```env
DASHBOARD_PASSWORD_HASH=pbkdf2_sha256:100000:...
```

Generate a session secret:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy it into `.env`:

```env
SESSION_SECRET=generated-value-here
```

Use the plaintext password you typed into the generator when signing in. Do not use the hash as the login password.

## Telegram Alerts

If Telegram alerts are enabled, set:

```env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

To run without Telegram while testing:

```env
TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

## Run With Docker

Build and start:

```powershell
docker compose up -d --build --force-recreate
```

Check container status and logs:

```powershell
docker compose ps
docker compose logs -f cloudways-monitor
```

Open the dashboard:

```text
http://127.0.0.1:8083
```

If deployed behind Caddy, open your configured `DASHBOARD_BASE_URL` instead.

## Check The App

Public health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8083/health
```

Login and check protected endpoints:

```powershell
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession

Invoke-RestMethod `
  -Uri http://127.0.0.1:8083/api/auth/login `
  -Method Post `
  -WebSession $session `
  -ContentType "application/json" `
  -Body '{"username":"admin","password":"YOUR_DASHBOARD_PASSWORD"}'

Invoke-RestMethod `
  -Uri http://127.0.0.1:8083/api/collector/health `
  -WebSession $session

Invoke-RestMethod `
  -Uri http://127.0.0.1:8083/api/doctor `
  -WebSession $session
```

Check discovered resource counts:

```powershell
$payload = Invoke-RestMethod `
  -Uri http://127.0.0.1:8083/api/resources `
  -WebSession $session

($payload.resources | Where-Object resource_type -eq "server").Count
($payload.resources | Where-Object resource_type -eq "application").Count

$payload.resources | Select-Object resource_type, provider_id, parent_provider_id, name
```

## Caddy Example

For a host port published on loopback:

```caddyfile
cloudways-monitor.example.com {
    reverse_proxy 127.0.0.1:8083
}
```

If Caddy and this service share a Docker network, remove the Compose host port and route by service name:

```caddyfile
cloudways-monitor.example.com {
    reverse_proxy cloudways-monitor:8083
}
```

## Local Development

Install dev dependencies in your Python environment:

```powershell
python -m pip install -e ".[dev]"
```

Run tests and static checks:

```powershell
python -m pytest -q
python -m ruff check .
python -m pyright
```

Run the backend directly:

```powershell
python -m cloudways_monitor
```

The Docker path is preferred for realistic runtime behavior because it loads `.env`, mounts `/data`, and starts the collector the same way production does.

## Troubleshooting

If login fails after generating a password hash:

- Make sure the hash in `.env` starts with `pbkdf2_sha256:`, not `pbkdf2_sha256$`.
- Recreate the container after changing `.env`: `docker compose up -d --build --force-recreate`.
- Login with the plaintext password you typed into `python -m cloudways_monitor.auth`.
- Confirm `DASHBOARD_USERNAME` matches the username you enter.

If Cloudways discovery is correct but the dashboard resources are wrong:

- Check `/api/doctor` for direct Cloudways discovery counts.
- Check `/api/collector/health` for collector status and discovered counts.
- Check `MONITORED_SERVER_IDS` and `MONITORED_APP_IDS`; blank means monitor all.
- Confirm `CLOUDWAYS_API_BASE_URL=https://api.cloudways.com/api/v2`.

If resources appear but metrics are empty or stay stale:

- The collector reads Cloudways v2 summary endpoints for bandwidth/disk and graph/task endpoints for server CPU/RAM and app traffic.
- Cloudways traffic tasks often need one poll to start and a later poll to complete, so app traffic can appear one collector cycle after bandwidth/disk.
- For accounts with many servers/apps, keep `STALE_AFTER_SECONDS` at `600` or higher. The app also applies an effective stale window of at least 10 polling intervals when Cloudways task polling is enabled.

## Security Notes

- Never commit `.env`.
- Keep Cloudways API keys and dashboard secrets private.
- Rotate any Cloudways/server/app/database credentials that were pasted into chat, logs, screenshots, or tickets.
- Keep the dashboard behind HTTPS when exposed publicly.
