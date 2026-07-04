# Deployment Notes

The v1 production shape is one Docker container behind Caddy on the shared DigitalOcean droplet.

## Build and run

1. Copy `.env.example` to `.env` on the droplet and fill in real values.
2. Build and start the service:

   ```powershell
   docker compose up -d --build
   ```

3. Confirm the backend health check:

   ```powershell
   curl http://127.0.0.1:8000/health
   ```

4. Confirm the protected deployment diagnostics once auth is implemented:

   ```powershell
   curl http://127.0.0.1:8000/api/doctor
   ```

## Caddy example

Route your chosen subdomain to the container's internal HTTP port:

```caddyfile
cloudways-monitor.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

If this service joins an existing Docker network with Caddy, point `reverse_proxy`
at the service name and port instead.

## Persistent data

`compose.yaml` mounts `./data` to `/data`. Keep `SQLITE_PATH` under `/data` so
metric history survives container restarts.
