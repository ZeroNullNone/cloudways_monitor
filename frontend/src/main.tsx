import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { createRoot } from "react-dom/client";
import {
  api,
  errorMessage,
  time,
  metricSeverity,
  type Auth,
  type Catalog,
  type Overview,
  type Resource,
} from "./api";
import { ServerPanel } from "./ServerPanel";
import "./styles.css";

function App() {
  const [auth, setAuth] = useState<Auth | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void api<Auth>("/api/auth/me")
      .then(setAuth)
      .catch((e) => {
        setAuth({ authenticated: false, username: null });
        setError(errorMessage(e));
      });
    const expire = () => setAuth({ authenticated: false, username: null });
    window.addEventListener("session-expired", expire);
    return () => window.removeEventListener("session-expired", expire);
  }, []);
  useEffect(() => {
    if (auth)
      window.history.replaceState(
        null,
        "",
        auth.authenticated ? `/${window.location.hash}` : "/login",
      );
  }, [auth]);
  if (!auth)
    return (
      <main className="login-shell">
        <p className="loading">Checking session</p>
      </main>
    );
  if (!auth.authenticated)
    return <Login initialError={error} onLogin={setAuth} />;
  return (
    <Dashboard
      auth={auth}
      onLogout={() => setAuth({ authenticated: false, username: null })}
    />
  );
}

function Login({
  onLogin,
  initialError,
}: {
  onLogin: (auth: Auth) => void;
  initialError: string | null;
}) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(initialError);
  const [submitting, setSubmitting] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      onLogin(
        await api<Auth>("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password }),
        }),
      );
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }
  return (
    <main className="login-shell">
      <form className="login-panel" onSubmit={submit}>
        <div className="brand-mark" aria-hidden="true">
          <i />
          <i />
          <i />
        </div>
        <p className="eyebrow">Cloudways Monitor</p>
        <h1>Sign in</h1>
        <p className="muted">
          Your servers and applications, in one workspace.
        </p>
        <label>
          Username
          <input
            required
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label>
          Password
          <input
            required
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && (
          <p className="error-message" role="alert">
            {error}
          </p>
        )}
        <button className="primary-button" disabled={submitting}>
          {submitting ? "Signing in" : "Sign in"}
        </button>
        <p className="pane-footnote">Private monitoring · All times UTC+8</p>
      </form>
    </main>
  );
}

function Dashboard({ auth, onLogout }: { auth: Auth; onLogout: () => void }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [refresh, setRefresh] = useState(0);
  const [lastRefresh, setLastRefresh] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(new Set<number>());
  const [query, setQuery] = useState("");
  const [attentionOnly, setAttentionOnly] = useState(false);
  const [sort, setSort] = useState("attention");
  const [history, setHistory] = useState<
    { id: number; message: string; created_at: string }[] | null
  >(null);
  const refreshData = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    const results = await Promise.allSettled([
      api<Overview>("/api/overview"),
      api<Catalog>("/api/monitor/catalog"),
    ]);
    if (results[0].status === "fulfilled") {
      setOverview(results[0].value);
      setLastRefresh(new Date().toISOString());
    }
    if (results[1].status === "fulfilled") setCatalog(results[1].value);
    setErrors(
      results.flatMap((result) =>
        result.status === "rejected" ? [errorMessage(result.reason)] : [],
      ),
    );
    setRefresh((value) => value + 1);
    busyRef.current = false;
    setBusy(false);
  }, []);
  useEffect(() => {
    void refreshData();
  }, [refreshData]);
  useEffect(() => {
    const timer = setInterval(
      () => void refreshData(),
      (overview?.refresh_seconds ?? 60) * 1000,
    );
    return () => clearInterval(timer);
  }, [refreshData, overview?.refresh_seconds]);
  useEffect(() => {
    if (overview && window.location.hash)
      document
        .getElementById(window.location.hash.slice(1))
        ?.scrollIntoView({ block: "start" });
  }, [Boolean(overview)]);
  const hasAttention = (s: Resource) =>
    s.alerts.length > 0 ||
    s.latest.stale ||
    s.latest.metrics.some((m) => m.stale) ||
    s.latest.metrics.some((m) =>
      metricSeverity(
        m.target,
        m.value,
        m.unit,
        catalog?.alert_rules ?? [],
        m.stale,
      ),
    ) ||
    s.latest.metrics.length < (catalog?.alert_rules.length ?? 3) ||
    s.latest.diagnostics.length > 0 ||
    s.applications.some(
      (a) => a.latest.stale || a.latest.diagnostics.length > 0,
    );
  const search = query.trim().toLowerCase();
  const matches = (s: Resource) =>
    (!attentionOnly || hasAttention(s)) &&
    (!search ||
      `${s.name} ${s.provider_id} ${s.metadata.region}`
        .toLowerCase()
        .includes(search) ||
      s.applications.some((a) =>
        `${a.name} ${a.metadata.cname} ${a.metadata.app_fqdn}`
          .toLowerCase()
          .includes(search),
      ));
  const servers = [...(overview?.servers ?? [])].sort((a, b) => {
    if (sort === "attention")
      return (
        Number(hasAttention(b)) - Number(hasAttention(a)) ||
        a.name.localeCompare(b.name)
      );
    if (sort === "name") return a.name.localeCompare(b.name);
    if (sort === "applications")
      return b.applications.length - a.applications.length;
    const value = (s: Resource) => {
      const metric = s.latest.metrics.find((m) => m.target === sort);
      return metric?.stale ? null : metric?.comparable_value;
    };
    const av = value(a),
      bv = value(b);
    if (av == null) return bv == null ? a.name.localeCompare(b.name) : 1;
    return bv == null ? -1 : av - bv;
  });
  const totalApps = servers.reduce((n, s) => n + s.applications.length, 0);
  return (
    <main className="shell">
      <a className="skip-link" href="#servers">
        Skip to servers
      </a>
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <i />
            <i />
            <i />
          </div>
          <div>
            <h1>Cloudways Monitor</h1>
            <p>
              Resources <span>DigitalOcean</span>
            </p>
          </div>
        </div>
        <div className="top-actions">
          <span className="operator">{auth.username}</span>
          <button
            className="quiet-button"
            onClick={async () => {
              try {
                await api("/api/auth/logout", { method: "POST" });
                onLogout();
              } catch (error) {
                setErrors([errorMessage(error)]);
              }
            }}
          >
            Sign out
          </button>
        </div>
      </header>
      <section className="workspace-summary" aria-label="Workspace summary">
        <dl>
          <div>
            <dt>Servers</dt>
            <dd>{servers.length}</dd>
          </div>
          <div>
            <dt>Applications</dt>
            <dd>{totalApps}</dd>
          </div>
          <div>
            <dt>Need attention</dt>
            <dd className={servers.some(hasAttention) ? "warning-text" : ""}>
              {servers.filter(hasAttention).length}
            </dd>
          </div>
        </dl>
        <div className="workspace-meta">
          <span
            className={`status ${overview?.telegram_enabled ? "status-healthy" : ""}`}
          >
            Telegram {overview?.telegram_enabled ? "enabled" : "disabled"}
          </span>
          <span>All times UTC+8</span>
          <span>Updated {time(lastRefresh)}</span>
        </div>
      </section>
      <div className="toolbar">
        <label className="search-label">
          <span className="visually-hidden">
            Search servers and applications
          </span>
          <input
            type="search"
            placeholder="Search servers or applications"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label>
          Sort servers
          <select
            aria-label="Sort servers"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
          >
            <option value="attention">Attention first</option>
            <option value="name">Name</option>
            <option value="applications">Application count</option>
            <option value="Idle CPU">Lowest idle CPU</option>
            <option value="Free memory">Lowest free memory</option>
            <option value="Free Disk">Lowest free disk</option>
          </select>
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={attentionOnly}
            onChange={(e) => setAttentionOnly(e.target.checked)}
          />
          Needs attention
        </label>
        <div className="toolbar-actions">
          <button
            onClick={() => setCollapsed(new Set(servers.map((s) => s.id)))}
          >
            Collapse all
          </button>
          <button onClick={() => setCollapsed(new Set())}>Expand all</button>
          <button
            className="primary-button"
            disabled={busy}
            onClick={() => void refreshData()}
          >
            {busy ? "Refreshing" : "Refresh"}
          </button>
        </div>
      </div>
      {errors.map((error, i) => (
        <p key={i} className="error-banner" role="alert">
          {error}
        </p>
      ))}
      {overview?.collector.status === "degraded" && (
        <p className="warning-banner">
          Collection needs attention:{" "}
          {overview.collector.last_error ?? "Data is stale"}. Inspect collection
          details below.
        </p>
      )}
      {!overview && !errors.length && (
        <p className="empty-state loading">Discovering resources</p>
      )}
      <div id="servers" className="server-list">
        {servers.map((server) => (
          <div key={server.id} hidden={!matches(server)}>
            <ServerPanel
              server={server}
              catalog={catalog}
              query={search}
              refresh={refresh}
              expanded={
                matches(server) &&
                (!collapsed.has(server.id) || Boolean(search))
              }
              toggle={() =>
                setCollapsed((current) => {
                  const next = new Set(current);
                  if (next.has(server.id)) next.delete(server.id);
                  else next.add(server.id);
                  return next;
                })
              }
            />
          </div>
        ))}
      </div>
      {overview && !servers.some(matches) && (
        <div className="empty-state">
          {servers.length
            ? "No resources match your filters."
            : "No DigitalOcean resources discovered. Check collection status and token permissions."}
        </div>
      )}
      {overview && overview.unassociated_applications.length > 0 && (
        <p className="warning-banner">
          {overview.unassociated_applications.length} applications have no
          matching server. Check discovery.
        </p>
      )}
      <footer className="dashboard-footer">
        <p>
          {servers.filter(matches).length} / {servers.length} servers shown ·
          Automatic refresh every {overview?.refresh_seconds ?? 60}s · UTC+8
        </p>
        <details>
          <summary>Alert defaults</summary>
          <p className="pane-footnote">
            Thresholds apply to verified units and fresh, distinct source
            samples. Unknown data never recovers an alert.
          </p>
          <div className="application-table">
            <table>
              <thead>
                <tr>
                  <th>DigitalOcean target</th>
                  <th>Warning</th>
                  <th>Critical</th>
                </tr>
              </thead>
              <tbody>
                {catalog?.alert_rules.map((rule) => (
                  <tr key={rule.target}>
                    <td>{rule.target}</td>
                    <td>
                      {rule.comparison} {rule.warning} {rule.unit}
                    </td>
                    <td>
                      {rule.comparison} {rule.critical} {rule.unit}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
        <details
          onToggle={(event) => {
            if (event.currentTarget.open)
              api<{ events: typeof history }>("/api/alerts/events")
                .then((result) => setHistory(result.events))
                .catch((error) => setErrors([errorMessage(error)]));
          }}
        >
          <summary>Alert history</summary>
          {history?.length === 0 && (
            <p className="pane-footnote">No alert events.</p>
          )}
          {history?.map((event) => (
            <article className="alert-event" key={event.id}>
              <time>{time(event.created_at)}</time>
              <p>{event.message}</p>
            </article>
          ))}
        </details>
      </footer>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
