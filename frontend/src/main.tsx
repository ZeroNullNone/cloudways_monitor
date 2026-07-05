import React, {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type AuthState = {
  authenticated: boolean;
  username: string | null;
};

type ResourceType = "server" | "application";
type RangeKey = "1h" | "6h" | "24h" | "7d" | "30d";

type LatestMetrics = {
  captured_at: string | null;
  stale: boolean;
  cpu_percent: number | null;
  ram_percent: number | null;
  disk_percent: number | null;
  bandwidth_bytes: number | null;
  traffic_requests: number | null;
};

type CompactAlert = {
  id: number;
  rule_key: string;
  severity: string | null;
  status: string;
};

type ResourceSummary = ResourceIdentity & {
  latest: LatestMetrics;
  alerts: CompactAlert[];
  applications?: ResourceSummary[];
};

type ResourceIdentity = {
  id: number;
  provider_id: string;
  resource_type: ResourceType;
  name: string;
  parent_provider_id: string | null;
};

type OverviewAlert = CompactAlert & {
  resource_id: number;
  resource_name: string | null;
  resource_type: ResourceType | null;
  consecutive_breaches: number;
  opened_at: string | null;
  last_notification_at: string | null;
};

type CollectorHealth = {
  status: string;
  last_run_at: string | null;
  last_success_at: string | null;
  servers_discovered: number;
  applications_discovered: number;
  snapshots_stored: number;
  snapshots_expired: number;
  stale: boolean;
  last_error_code: string | null;
  last_error: string | null;
};

type Overview = {
  attention: {
    status: "ok" | "needs_attention";
    active_alert_count: number;
    stale_resource_count: number;
  };
  collector: CollectorHealth;
  active_alerts: OverviewAlert[];
  servers: ResourceSummary[];
};

type ResourceListPayload = {
  resources: ResourceSummary[];
};

type ResourceDetailPayload = {
  resource: ResourceSummary;
  parent_server: ResourceSummary | null;
};

type SeriesPoint = {
  captured_at: string;
  cpu_percent: number | null;
  ram_percent: number | null;
  disk_percent: number | null;
  bandwidth_bytes: number | null;
  traffic_requests: number | null;
  collection_status: string;
  error_code: string | null;
};

type SeriesPayload = {
  resource: ResourceIdentity;
  range: {
    key: RangeKey;
    start: string;
    end: string;
  };
  points: SeriesPoint[];
};

type RawLatestPayload = {
  resource: ResourceIdentity;
  snapshot: {
    captured_at: string;
    collection_status: string;
    error_code: string | null;
    php_metric: Record<string, unknown>;
    mysql_metric: Record<string, unknown>;
    raw_payload: Record<string, unknown>;
  };
};

const RANGE_OPTIONS: RangeKey[] = ["1h", "6h", "24h", "7d", "30d"];

function App() {
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [path, setPath] = useState(window.location.pathname);

  useEffect(() => {
    let active = true;
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then((response) => response.json() as Promise<AuthState>)
      .then((payload) => {
        if (active) {
          setAuth(payload);
        }
      })
      .catch(() => {
        if (active) {
          setAuth({ authenticated: false, username: null });
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const onPopState = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (auth === null) {
      return;
    }
    if (!auth.authenticated && path !== "/login") {
      window.history.replaceState(null, "", "/login");
      setPath("/login");
    }
    if (auth.authenticated && path === "/login") {
      window.history.replaceState(null, "", "/");
      setPath("/");
    }
  }, [auth, path]);

  if (auth === null) {
    return (
      <main className="shell shell-center">
        <p className="loading">Checking session</p>
      </main>
    );
  }

  if (!auth.authenticated || path === "/login") {
    return <LoginView onAuthenticated={setAuth} setPath={setPath} />;
  }

  return (
    <DashboardShell
      auth={auth}
      onAuthenticated={setAuth}
      path={path}
      setPath={setPath}
    />
  );
}

function LoginView({
  onAuthenticated,
  setPath,
}: {
  onAuthenticated: (auth: AuthState) => void;
  setPath: (path: string) => void;
}) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ username, password }),
    });
    setSubmitting(false);
    if (!response.ok) {
      setError("Invalid username or password");
      return;
    }
    const payload = (await response.json()) as AuthState;
    onAuthenticated(payload);
    window.history.replaceState(null, "", "/");
    setPath("/");
  }

  return (
    <main className="auth-shell">
      <form className="login-panel" onSubmit={submit}>
        <div>
          <p className="eyebrow">Cloudways Monitor</p>
          <h1>Sign in</h1>
        </div>
        <label>
          Username
          <input
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label>
          Password
          <input
            autoComplete="current-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error ? <p className="form-error">{error}</p> : null}
        <button type="submit" disabled={submitting}>
          {submitting ? "Signing in" : "Sign in"}
        </button>
      </form>
    </main>
  );
}

function DashboardShell({
  auth,
  onAuthenticated,
  path,
  setPath,
}: {
  auth: AuthState;
  onAuthenticated: (auth: AuthState) => void;
  path: string;
  setPath: (path: string) => void;
}) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [resources, setResources] = useState<ResourceSummary[]>([]);
  const [selectedResourceId, setSelectedResourceId] = useState<number | null>(
    parseResourceId(path),
  );
  const [range, setRange] = useState<RangeKey>("1h");
  const [detail, setDetail] = useState<ResourceDetailPayload | null>(null);
  const [series, setSeries] = useState<SeriesPayload | null>(null);
  const [rawLatest, setRawLatest] = useState<RawLatestPayload | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<string | null>(null);

  const refreshOverview = useCallback(async () => {
    setOverviewLoading(true);
    setError(null);
    try {
      const [overviewPayload, resourcesPayload] = await Promise.all([
        fetchJson<Overview>("/api/overview"),
        fetchJson<ResourceListPayload>("/api/resources"),
      ]);
      setOverview(overviewPayload);
      setResources(resourcesPayload.resources);
      setLastRefresh(new Date().toISOString());
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setOverviewLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshOverview();
  }, [refreshOverview]);

  useEffect(() => {
    const idFromPath = parseResourceId(path);
    if (idFromPath !== null) {
      setSelectedResourceId(idFromPath);
      return;
    }
    if (selectedResourceId === null && resources.length > 0) {
      setSelectedResourceId(resources[0].id);
    }
  }, [path, resources, selectedResourceId]);

  useEffect(() => {
    if (selectedResourceId === null) {
      setDetail(null);
      setSeries(null);
      setRawLatest(null);
      return;
    }

    let active = true;
    setDetailLoading(true);
    setError(null);
    Promise.all([
      fetchJson<ResourceDetailPayload>(`/api/resources/${selectedResourceId}`),
      fetchJson<SeriesPayload>(
        `/api/resources/${selectedResourceId}/series?range=${range}`,
      ),
      fetchJsonOrNull<RawLatestPayload>(
        `/api/resources/${selectedResourceId}/raw/latest`,
      ),
    ])
      .then(([detailPayload, seriesPayload, rawPayload]) => {
        if (!active) {
          return;
        }
        setDetail(detailPayload);
        setSeries(seriesPayload);
        setRawLatest(rawPayload);
      })
      .catch((caught) => {
        if (active) {
          setError(errorMessage(caught));
        }
      })
      .finally(() => {
        if (active) {
          setDetailLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [range, selectedResourceId]);

  useEffect(() => {
    const events = new EventSource("/api/events");
    const refresh = () => {
      void refreshOverview();
    };
    events.addEventListener("dashboard-refresh", refresh);
    return () => {
      events.removeEventListener("dashboard-refresh", refresh);
      events.close();
    };
  }, [refreshOverview]);

  async function logout() {
    await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    });
    onAuthenticated({ authenticated: false, username: null });
    window.history.replaceState(null, "", "/login");
    setPath("/login");
  }

  function selectResource(resourceId: number) {
    const nextPath = `/resources/${resourceId}`;
    setSelectedResourceId(resourceId);
    window.history.pushState(null, "", nextPath);
    setPath(nextPath);
  }

  const selectedResource = detail?.resource ?? findResource(resources, selectedResourceId);

  return (
    <main className="shell">
      <nav className="topbar">
        <div>
          <p className="eyebrow">Cloudways Monitor</p>
          <h1>Live telemetry</h1>
        </div>
        <div className="account">
          <span>{auth.username}</span>
          <button type="button" onClick={logout}>
            Logout
          </button>
        </div>
      </nav>

      {error ? <p className="app-error">{error}</p> : null}

      <AttentionBand
        lastRefresh={lastRefresh}
        loading={overviewLoading}
        overview={overview}
      />

      <section className="workspace" aria-label="Resource dashboard">
        <ResourceNavigator
          resources={resources}
          selectedResourceId={selectedResourceId}
          servers={overview?.servers ?? []}
          onSelect={selectResource}
        />
        <ResourceDrilldown
          detail={detail}
          loading={detailLoading}
          onRangeChange={setRange}
          range={range}
          rawLatest={rawLatest}
          selectedResource={selectedResource}
          series={series}
        />
      </section>
    </main>
  );
}

function AttentionBand({
  lastRefresh,
  loading,
  overview,
}: {
  lastRefresh: string | null;
  loading: boolean;
  overview: Overview | null;
}) {
  const status = overview?.attention.status ?? "ok";
  const activeAlerts = overview?.attention.active_alert_count ?? 0;
  const staleResources = overview?.attention.stale_resource_count ?? 0;
  const collector = overview?.collector;

  return (
    <section className={`attention-band attention-${status}`}>
      <div className="attention-copy">
        <p className="eyebrow">Attention</p>
        <h2>{status === "needs_attention" ? "Needs attention" : "Systems normal"}</h2>
        <span className="refresh-time">
          {loading ? "Refreshing" : `Updated ${formatDateTime(lastRefresh)}`}
        </span>
      </div>
      <div className="attention-facts" aria-label="Dashboard status counts">
        <StatusFact label="Active alerts" value={activeAlerts.toString()} tone={activeAlerts > 0 ? "danger" : "ok"} />
        <StatusFact label="Stale resources" value={staleResources.toString()} tone={staleResources > 0 ? "warning" : "ok"} />
        <StatusFact label="Collector" value={collector?.status ?? "unknown"} tone={collector?.stale ? "warning" : "ok"} />
      </div>
      <AlertList alerts={overview?.active_alerts ?? []} />
    </section>
  );
}

function StatusFact({
  label,
  tone,
  value,
}: {
  label: string;
  tone: "ok" | "warning" | "danger";
  value: string;
}) {
  return (
    <div className={`status-fact tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function AlertList({ alerts }: { alerts: OverviewAlert[] }) {
  if (alerts.length === 0) {
    return <p className="quiet-line">No active alerts</p>;
  }

  return (
    <div className="alert-list">
      {alerts.map((alert) => (
        <div className="alert-row" key={alert.id}>
          <span className={`severity severity-${alert.severity ?? "info"}`}>
            {alert.severity ?? "info"}
          </span>
          <strong>{alert.resource_name ?? `Resource ${alert.resource_id}`}</strong>
          <span>{labelForMetric(alert.rule_key)}</span>
          <span>{alert.consecutive_breaches} polls</span>
        </div>
      ))}
    </div>
  );
}

function ResourceNavigator({
  onSelect,
  resources,
  selectedResourceId,
  servers,
}: {
  onSelect: (resourceId: number) => void;
  resources: ResourceSummary[];
  selectedResourceId: number | null;
  servers: ResourceSummary[];
}) {
  const hasGroupedResources = servers.length > 0;
  if (!hasGroupedResources && resources.length === 0) {
    return (
      <aside className="resource-nav">
        <p className="panel-title">Resources</p>
        <p className="quiet-line">Waiting for discovery</p>
      </aside>
    );
  }

  return (
    <aside className="resource-nav">
      <p className="panel-title">Resources</p>
      <div className="resource-stack">
        {hasGroupedResources
          ? servers.map((server) => (
              <div className="resource-group" key={server.id}>
                <ResourceButton
                  onSelect={onSelect}
                  resource={server}
                  selectedResourceId={selectedResourceId}
                />
                {(server.applications ?? []).map((application) => (
                  <ResourceButton
                    application
                    key={application.id}
                    onSelect={onSelect}
                    resource={application}
                    selectedResourceId={selectedResourceId}
                  />
                ))}
              </div>
            ))
          : resources.map((resource) => (
              <ResourceButton
                key={resource.id}
                onSelect={onSelect}
                resource={resource}
                selectedResourceId={selectedResourceId}
              />
            ))}
      </div>
    </aside>
  );
}

function ResourceButton({
  application = false,
  onSelect,
  resource,
  selectedResourceId,
}: {
  application?: boolean;
  onSelect: (resourceId: number) => void;
  resource: ResourceSummary;
  selectedResourceId: number | null;
}) {
  return (
    <button
      className={`resource-button${application ? " application-resource" : ""}`}
      data-active={resource.id === selectedResourceId}
      onClick={() => onSelect(resource.id)}
      type="button"
    >
      <span>
        <strong>{resource.name}</strong>
        <small>{resource.resource_type}</small>
      </span>
      <HealthPill resource={resource} />
    </button>
  );
}

function ResourceDrilldown({
  detail,
  loading,
  onRangeChange,
  range,
  rawLatest,
  selectedResource,
  series,
}: {
  detail: ResourceDetailPayload | null;
  loading: boolean;
  onRangeChange: (range: RangeKey) => void;
  range: RangeKey;
  rawLatest: RawLatestPayload | null;
  selectedResource: ResourceSummary | null;
  series: SeriesPayload | null;
}) {
  if (!selectedResource) {
    return (
      <section className="detail-pane">
        <p className="panel-title">Resource detail</p>
        <p className="quiet-line">Waiting for resources</p>
      </section>
    );
  }

  return (
    <section className="detail-pane">
      <div className="detail-header">
        <div>
          <p className="eyebrow">{selectedResource.resource_type}</p>
          <h2>{selectedResource.name}</h2>
          {detail?.parent_server ? (
            <span className="refresh-time">Parent: {detail.parent_server.name}</span>
          ) : null}
        </div>
        <HealthPill resource={selectedResource} />
      </div>

      <MetricGrid latest={selectedResource.latest} />

      <section className="trend-section">
        <div className="section-heading">
          <div>
            <p className="panel-title">Resource trend</p>
            <span className="refresh-time">
              {series ? `${formatDateTime(series.range.start)} to ${formatDateTime(series.range.end)}` : "Loading"}
            </span>
          </div>
          <RangeSelector range={range} onRangeChange={onRangeChange} />
        </div>
        {loading ? <p className="quiet-line">Loading resource telemetry</p> : null}
        <TrendChart points={series?.points ?? []} />
      </section>

      <section className="raw-section">
        <div className="section-heading">
          <div>
            <p className="panel-title">Raw latest payload</p>
            <span className="refresh-time">
              {rawLatest ? formatDateTime(rawLatest.snapshot.captured_at) : "No raw payload"}
            </span>
          </div>
          {rawLatest ? (
            <span className={`severity severity-${rawLatest.snapshot.collection_status}`}>
              {rawLatest.snapshot.collection_status}
            </span>
          ) : null}
        </div>
        <pre>{rawLatest ? JSON.stringify(rawLatest.snapshot.raw_payload, null, 2) : "{}"}</pre>
      </section>
    </section>
  );
}

function MetricGrid({ latest }: { latest: LatestMetrics }) {
  return (
    <div className="metric-grid">
      <MetricTile label="CPU" value={formatPercent(latest.cpu_percent)} stale={latest.stale} />
      <MetricTile label="RAM" value={formatPercent(latest.ram_percent)} stale={latest.stale} />
      <MetricTile label="Disk" value={formatPercent(latest.disk_percent)} stale={latest.stale} />
      <MetricTile label="Bandwidth" value={formatBytes(latest.bandwidth_bytes)} stale={latest.stale} />
      <MetricTile label="Traffic" value={formatNumber(latest.traffic_requests)} stale={latest.stale} />
      <MetricTile label="Captured" value={formatDateTime(latest.captured_at)} stale={latest.stale} />
    </div>
  );
}

function MetricTile({
  label,
  stale,
  value,
}: {
  label: string;
  stale: boolean;
  value: string;
}) {
  return (
    <div className="metric-tile" data-stale={stale}>
      <span>{label}</span>
      <strong>{value}</strong>
      {stale ? <small>stale</small> : null}
    </div>
  );
}

function RangeSelector({
  onRangeChange,
  range,
}: {
  onRangeChange: (range: RangeKey) => void;
  range: RangeKey;
}) {
  return (
    <div className="range-selector" aria-label="Time range">
      {RANGE_OPTIONS.map((option) => (
        <button
          data-active={option === range}
          key={option}
          onClick={() => onRangeChange(option)}
          type="button"
        >
          {option}
        </button>
      ))}
    </div>
  );
}

function TrendChart({ points }: { points: SeriesPoint[] }) {
  const series = useMemo(
    () => [
      buildLine(points, "cpu_percent", "CPU"),
      buildLine(points, "ram_percent", "RAM"),
      buildLine(points, "disk_percent", "Disk"),
    ],
    [points],
  );
  const visibleSeries = series.filter((line) => line.path.length > 0);

  if (points.length === 0 || visibleSeries.length === 0) {
    return <p className="quiet-line">No points in this range</p>;
  }

  return (
    <div className="chart-wrap">
      <svg aria-label="CPU RAM and disk percent over time" viewBox="0 0 640 220">
        <line className="axis" x1="44" x2="612" y1="28" y2="28" />
        <line className="axis" x1="44" x2="612" y1="106" y2="106" />
        <line className="axis" x1="44" x2="612" y1="184" y2="184" />
        <text className="axis-label" x="8" y="32">100%</text>
        <text className="axis-label" x="14" y="110">50%</text>
        <text className="axis-label" x="22" y="188">0%</text>
        {visibleSeries.map((line) => (
          <polyline
            className={`trend-line trend-${line.key}`}
            fill="none"
            key={line.key}
            points={line.path}
          />
        ))}
      </svg>
      <div className="chart-legend">
        {visibleSeries.map((line) => (
          <span className={`legend-item trend-${line.key}`} key={line.key}>
            {line.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function HealthPill({ resource }: { resource: ResourceSummary }) {
  if (resource.alerts.length > 0) {
    return <span className="pill pill-danger">alert</span>;
  }
  if (resource.latest.stale) {
    return <span className="pill pill-warning">stale</span>;
  }
  return <span className="pill pill-ok">live</span>;
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { credentials: "same-origin" });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

async function fetchJsonOrNull<T>(url: string): Promise<T | null> {
  const response = await fetch(url, { credentials: "same-origin" });
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

function parseResourceId(path: string): number | null {
  const match = path.match(/^\/resources\/(\d+)$/);
  if (!match) {
    return null;
  }
  return Number(match[1]);
}

function findResource(
  resources: ResourceSummary[],
  selectedResourceId: number | null,
): ResourceSummary | null {
  if (selectedResourceId === null) {
    return null;
  }
  return resources.find((resource) => resource.id === selectedResourceId) ?? null;
}

function buildLine(
  points: SeriesPoint[],
  key: "cpu_percent" | "ram_percent" | "disk_percent",
  label: string,
) {
  const chartLeft = 44;
  const chartRight = 612;
  const chartTop = 28;
  const chartBottom = 184;
  const plottable = points
    .map((point, index) => ({ index, value: point[key] }))
    .filter((point): point is { index: number; value: number } =>
      typeof point.value === "number",
    );
  const divisor = Math.max(points.length - 1, 1);
  const path = plottable
    .map((point) => {
      const x = chartLeft + ((chartRight - chartLeft) * point.index) / divisor;
      const bounded = Math.min(Math.max(point.value, 0), 100);
      const y = chartBottom - ((chartBottom - chartTop) * bounded) / 100;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return { key: key.replace("_percent", ""), label, path };
}

function formatPercent(value: number | null): string {
  if (value === null) {
    return "No data";
  }
  return `${formatNumber(value)}%`;
}

function formatBytes(value: number | null): string {
  if (value === null) {
    return "No data";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let scaled = value;
  let unitIndex = 0;
  while (scaled >= 1024 && unitIndex < units.length - 1) {
    scaled /= 1024;
    unitIndex += 1;
  }
  return `${formatNumber(scaled)} ${units[unitIndex]}`;
}

function formatNumber(value: number | null): string {
  if (value === null) {
    return "No data";
  }
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: value >= 10 ? 0 : 1,
  }).format(value);
}

function formatDateTime(value: string | null): string {
  if (value === null) {
    return "Never";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function labelForMetric(ruleKey: string): string {
  return ruleKey.replaceAll("_", " ");
}

function errorMessage(caught: unknown): string {
  return caught instanceof Error ? caught.message : "Request failed";
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
