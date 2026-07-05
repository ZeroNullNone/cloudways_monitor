# Deployment Notes

The v1 production shape is one Docker container behind Caddy on the shared DigitalOcean droplet. The app exposes one loopback HTTP port for Caddy and keeps SQLite metric history on a named Docker volume mounted at `/data`.

## Build and run

1. Copy `.env.example` to `.env` on the droplet and fill in real values. Keep `SQLITE_PATH=/data/cloudways-monitor.sqlite3` so the database lives on the persistent volume.
2. Build and start the service:

   ```bash
   docker compose up -d --build
   ```

3. Confirm the backend health check from the droplet:

   ```bash
   curl http://127.0.0.1:8000/health
   ```

4. Confirm the protected deployment diagnostics with a temporary cookie jar:

   ```bash
   curl -c /tmp/cloudways-monitor.cookies \
     -H "Content-Type: application/json" \
     -d '{"username":"admin","password":"your-dashboard-password"}' \
     http://127.0.0.1:8000/api/auth/login

   curl -b /tmp/cloudways-monitor.cookies \
     http://127.0.0.1:8000/api/doctor
   ```

5. Check container logs if the doctor response is degraded:

   ```bash
   docker compose logs --tail=100 cloudways-monitor
   ```

## Caddy example

Route your chosen subdomain to the loopback port published by Compose:

```caddyfile
cloudways-monitor.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

If this service joins an existing Docker network with Caddy, remove the host port mapping and point `reverse_proxy` at the service name and port instead:

```caddyfile
cloudways-monitor.example.com {
    reverse_proxy cloudways-monitor:8000
}
```

## Persistent data

`compose.yaml` mounts the named Docker volume `cloudways-monitor-data` to `/data`. Keep `SQLITE_PATH=/data/cloudways-monitor.sqlite3` in `.env` so metric history survives container restarts and image rebuilds.

To confirm the mounted data path from inside the running container:

```bash
docker compose exec cloudways-monitor sh -lc 'test -d /data && echo /data-mounted'
```
