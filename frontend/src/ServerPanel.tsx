import { Fragment, useEffect, useRef, useState } from "react";
import {
  api,
  errorMessage,
  numeric,
  size,
  time,
  metricSeverity,
  type Catalog,
  type Graph,
  type Resource,
} from "./api";
import { TimeChart } from "./TimeChart";
import { ApplicationAnalytics } from "./ApplicationAnalytics";

function Status({ value }: { value: string }) {
  return (
    <span
      className={`status status-${value.toLowerCase().replaceAll(" ", "-")}`}
    >
      {value}
    </span>
  );
}

export function ServerPanel({
  server,
  catalog,
  expanded,
  toggle,
  refresh,
  query,
}: {
  server: Resource;
  catalog: Catalog | null;
  expanded: boolean;
  toggle: () => void;
  refresh: number;
  query: string;
}) {
  const [target, setTarget] = useState("");
  const [duration, setDuration] = useState("");
  const [graph, setGraph] = useState<Graph | null>(null);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [seriesIndex, setSeriesIndex] = useState(0);
  const [visible, setVisible] = useState(false);
  const [raw, setRaw] = useState<string | null>(null);
  const [sort, setSort] = useState("disk_used_gb");
  const [expandedApps, setExpandedApps] = useState(new Set<number>());
  const ref = useRef<HTMLElement>(null);
  const graphCache = useRef(new Map<string, Graph>());
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) setVisible(true);
      },
      { rootMargin: "300px" },
    );
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!catalog) return;
    setTarget((current) =>
      catalog.targets.includes(current)
        ? current
        : catalog.targets.includes("Idle CPU")
          ? "Idle CPU"
          : catalog.targets[0],
    );
    setDuration((current) =>
      catalog.durations.includes(current)
        ? current
        : catalog.durations.includes("1 Hour")
          ? "1 Hour"
          : catalog.durations[0],
    );
  }, [catalog]);
  useEffect(() => {
    if (!expanded || !visible || !target || !duration) return;
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout>;
    let disposed = false;
    const graphKey = `${target}|${duration}`;
    setGraph(graphCache.current.get(graphKey) ?? null);
    setGraphError(null);
    setLoading(true);
    setSeriesIndex(0);
    async function load() {
      try {
        const params = new URLSearchParams({ target, duration });
        const result = await api<Graph>(
          `/api/servers/${server.id}/graph?${params}`,
          { signal: controller.signal },
        );
        if (disposed) return;
        graphCache.current.set(graphKey, result);
        if (graphCache.current.size > 8)
          graphCache.current.delete(graphCache.current.keys().next().value!);
        setGraph(result);
        setLoading(false);
        if (result.status === "pending") retry = setTimeout(load, 3500);
      } catch (error) {
        if (disposed) return;
        setGraphError(errorMessage(error));
        setLoading(false);
      }
    }
    void load();
    return () => {
      disposed = true;
      controller.abort();
      clearTimeout(retry);
    };
  }, [expanded, visible, target, duration, server.id, refresh]);
  const apps = server.applications.filter(
    (a) =>
      !query ||
      `${server.name} ${server.provider_id} ${server.metadata.region}`
        .toLowerCase()
        .includes(query) ||
      `${a.name} ${a.metadata.cname ?? ""} ${a.metadata.app_fqdn ?? ""}`
        .toLowerCase()
        .includes(query),
  );
  const monitoredApps = apps.filter((a) =>
    ["disk_used_gb", "traffic_requests"].some(
      (k) => a.latest[k as keyof typeof a.latest] !== undefined,
    ),
  );
  const sorted = [...apps].sort((a, b) => {
    if (sort === "name") return a.name.localeCompare(b.name);
    const value = (r: Resource) => r.latest[sort as keyof typeof r.latest];
    const av = value(a),
      bv = value(b);
    if (typeof av !== "number")
      return typeof bv !== "number" ? a.name.localeCompare(b.name) : 1;
    return typeof bv !== "number" ? -1 : bv - av;
  });
  const hasAlert = server.alerts.length > 0;
  const diagnostics = [
    ...server.latest.diagnostics,
    ...apps.flatMap((a) =>
      a.latest.diagnostics.map((d) => ({
        ...d,
        metric: `${a.name} / ${d.metric}`,
      })),
    ),
  ];
  const numericSeverities = server.latest.metrics.map((m) =>
    metricSeverity(
      m.target,
      m.value,
      m.unit,
      catalog?.alert_rules ?? [],
      m.stale,
    ),
  );
  const state = numericSeverities.includes("critical")
    ? "Critical"
    : numericSeverities.includes("warning")
      ? "Warning"
      : hasAlert
        ? server.alerts.some((a) => a.severity === "critical")
          ? "Critical"
          : "Warning"
        : server.latest.stale || server.latest.metrics.some((m) => m.stale)
          ? "Stale"
          : diagnostics.length
            ? "Needs attention"
            : !server.latest.metrics.length
              ? "No latest samples"
              : server.latest.metrics.length <
                  (catalog?.alert_rules.length ?? 3)
                ? "Partial data"
                : "Healthy";
  const activeSeries =
    graph?.series[Math.min(seriesIndex, graph.series.length - 1)];
  const hashMatched = window.location.hash === `#server-${server.provider_id}`;
  return (
    <section
      ref={ref}
      className={`server-panel ${hashMatched ? "linked-panel" : ""}`}
      id={`server-${server.provider_id}`}
      aria-label={server.name}
    >
      <header className="server-heading">
        <button
          className="server-toggle"
          onClick={toggle}
          aria-expanded={expanded}
          aria-controls={`server-body-${server.id}`}
        >
          <svg
            viewBox="0 0 16 16"
            className={expanded ? "chevron expanded" : "chevron"}
            aria-hidden="true"
          >
            <path d="m5 3 5 5-5 5" />
          </svg>
          <span>
            <strong>{server.name}</strong>
            <span className="server-meta">
              DigitalOcean · {server.metadata.region ?? "Region not provided"} ·{" "}
              {server.applications.length} applications · ID{" "}
              {server.provider_id}
            </span>
          </span>
        </button>
        <Status value={state} />
      </header>
      {server.latest.metrics.length > 0 && (
        <dl className="server-metrics">
          {server.latest.metrics.map((metric) => {
            const severity = metricSeverity(
              metric.target,
              metric.value,
              metric.unit,
              catalog?.alert_rules ?? [],
              metric.stale,
            );
            const cpu =
              metric.target === "Idle CPU" &&
              metric.unit === "%" &&
              metric.value >= 0 &&
              metric.value <= 100;
            return (
              <div
                key={metric.target}
                title={`Source sample: ${time(metric.sample_at)}`}
              >
                <dt>{cpu ? "CPU used (100 − idle)" : metric.target}</dt>
                <dd className={severity ? `metric-${severity}` : undefined}>
                  {numeric(cpu ? 100 - metric.value : metric.value)}
                  <small>{metric.unit ?? "Unit not provided"}</small>
                  {metric.target === "Free memory" &&
                    server.latest.capacity?.memory_gb !== undefined && (
                      <small
                        className="metric-capacity"
                      title="Configured RAM from the Cloudways server plan."
                      >
                        Total {numeric(server.latest.capacity.memory_gb)} GB
                      </small>
                    )}
                  {metric.target === "Free Disk" &&
                    server.latest.capacity?.disk_gb !== undefined && (
                      <small
                        className="metric-capacity"
                        title={
                          server.latest.capacity.disk_source === "storage"
                            ? "Configured data disk capacity (Block Storage)."
                            : "Configured server disk capacity."
                        }
                      >
                        Total {numeric(server.latest.capacity.disk_gb)} GB
                      </small>
                    )}
                  {severity && (
                    <Status
                      value={severity === "critical" ? "Critical" : "Warning"}
                    />
                  )}
                  {metric.stale && <Status value="Stale" />}
                </dd>
                <span className="sample-time">
                  Completed {time(metric.sample_at)}
                </span>
              </div>
            );
          })}
        </dl>
      )}
      <div id={`server-body-${server.id}`} hidden={!expanded}>
        <div className="server-workspace">
          <div className="monitor-pane">
            <div className="monitor-controls">
              <label>
                Metric
                <select
                  aria-label={`Metric for ${server.name}`}
                  disabled={!catalog}
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                >
                  {catalog ? (
                    catalog.targets.map((t) => <option key={t}>{t}</option>)
                  ) : (
                    <option>Catalog unavailable</option>
                  )}
                </select>
              </label>
              <label>
                Duration
                <select
                  aria-label={`Duration for ${server.name}`}
                  disabled={!catalog}
                  value={duration}
                  onChange={(e) => setDuration(e.target.value)}
                >
                  {catalog ? (
                    catalog.durations.map((d) => <option key={d}>{d}</option>)
                  ) : (
                    <option>Unavailable</option>
                  )}
                </select>
              </label>
            </div>
            {catalog?.metric_definitions[target] && (
              <p className="pane-footnote">
                {catalog.metric_definitions[target].description}
              </p>
            )}
            {loading &&
              (graph ? (
                <p className="pane-footnote">Refreshing graph…</p>
              ) : (
                <div className="chart-empty loading">
                  Loading monitoring data
                </div>
              ))}
            {graphError && (
              <p className="error-message" role="alert">
                {graphError}
              </p>
            )}
            {graph?.status === "pending" && (
              <div className="chart-empty">
                Cloudways is preparing this graph. Checking again shortly.
              </div>
            )}
            {graph &&
              ["ok", "warning"].includes(graph.status) &&
              graph.series.length === 0 && (
                <div className="chart-empty">No samples in this range</div>
              )}
            {graph?.error && (
              <p
                className={
                  graph.status === "error" ? "error-message" : "warning-message"
                }
                role="status"
              >
                {graph.error}
              </p>
            )}
            {graph && graph.series.length > 1 && (
              <label className="series-select">
                Series
                <select
                  value={seriesIndex}
                  onChange={(e) => setSeriesIndex(Number(e.target.value))}
                >
                  {graph.series.map((s, i) => (
                    <option key={i} value={i}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {activeSeries && (
              <>
                <dl className="graph-summary" aria-label={`${target} summary`}>
                  {(["latest", "min", "max", "avg"] as const).map((key) => {
                    const value = activeSeries.summary[key];
                    if (
                      value === undefined ||
                      (key === "avg" &&
                        [
                          "Monthly Bandwidth",
                          "Auto-healing Restarts",
                          "Varnish Nuked",
                        ].includes(target))
                    )
                      return null;
                    return (
                      <div
                        key={key}
                        className={
                          key === "latest"
                            ? "summary-latest"
                            : "summary-secondary"
                        }
                      >
                        <dt>
                          {
                            {
                              latest: "Latest completed",
                              min: "Min",
                              max: "Max",
                              avg: "Sample avg",
                            }[key]
                          }
                        </dt>
                        <dd
                          className={
                            key === "latest"
                              ? (() => {
                                  const stale =
                                    !activeSeries.summary.sample_at ||
                                    (Date.now() -
                                      Date.parse(
                                        activeSeries.summary.sample_at,
                                      )) /
                                      1000 >
                                      activeSeries.freshness_seconds;
                                  const severity = metricSeverity(
                                    target,
                                    value,
                                    activeSeries.unit,
                                    catalog?.alert_rules ?? [],
                                    stale,
                                  );
                                  return severity
                                    ? `metric-${severity}`
                                    : undefined;
                                })()
                              : undefined
                          }
                        >
                          {numeric(value)}
                          <small>{activeSeries.unit ?? ""}</small>
                        </dd>
                        {key === "latest" && activeSeries.summary.sample_at && (
                          <time
                            className="sample-time"
                            dateTime={activeSeries.summary.sample_at}
                          >
                            {time(activeSeries.summary.sample_at)}
                          </time>
                        )}
                      </div>
                    );
                  })}
                </dl>
                <TimeChart
                  series={activeSeries}
                  target={target}
                  duration={duration}
                />
              </>
            )}
          </div>
          <div className="applications-pane">
            <div className="applications-heading">
              <h3>
                Applications <span>{apps.length}</span>
              </h3>
              <label>
                Sort
                <select
                  aria-label={`Sort applications for ${server.name}`}
                  value={sort}
                  onChange={(e) => setSort(e.target.value)}
                >
                  <option value="disk_used_gb">Largest disk</option>
                  <option value="traffic_requests">
                    Most returned requests
                  </option>
                  <option value="name">Name</option>
                </select>
              </label>
            </div>
            <div className="application-table">
              <table>
                <thead>
                  <tr>
                    <th>Application</th>
                    <th>Disk</th>
                    <th title="Application bandwidth is not returned by the monitored Cloudways endpoints.">
                      Bandwidth
                    </th>
                    <th>Requests · 1h</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((app) => (
                    <Fragment key={app.id}>
                      <tr key={app.id} id={`app-${app.provider_id}`}>
                        <td>
                          <details
                            onToggle={(e) => {
                              const open = e.currentTarget.open;
                              setExpandedApps((current) => {
                                const next = new Set(current);
                                if (open) next.add(app.id);
                                else next.delete(app.id);
                                return next;
                              });
                            }}
                          >
                            <summary>
                              <strong>{app.name}</strong>
                              <span className="domain">
                                {String(
                                  app.metadata.cname ||
                                    app.metadata.app_fqdn ||
                                    "",
                                )}
                              </span>
                            </summary>
                            <div className="app-details">
                              <span>ID {app.provider_id}</span>
                              <span>
                                Fetched: {time(app.latest.fetched_at)}
                              </span>
                              {app.latest.disk_used_gb_sample_at && (
                                <span>
                                  Disk sample:{" "}
                                  {time(app.latest.disk_used_gb_sample_at)}
                                </span>
                              )}
                              {app.latest.traffic_requests !== undefined && (
                                <span>
                                  {app.latest.traffic_complete
                                    ? "Complete request count"
                                    : "Returned requests (partial)"}{" "}
                                  · {app.latest.traffic_window}
                                </span>
                              )}
                              {app.latest.traffic_fetched_at && (
                                <span>
                                  Requests collected:{" "}
                                  {time(app.latest.traffic_fetched_at)}
                                </span>
                              )}
                              {app.latest.diagnostics.map((d, i) => (
                                <span className="error-message" key={i}>
                                  {d.metric}: {d.message}
                                </span>
                              ))}
                            </div>
                          </details>
                          {app.latest.stale && <Status value="Stale" />}
                          {app.latest.diagnostics.length > 0 && (
                            <span className="row-warning">Needs attention</span>
                          )}
                          {!["disk_used_gb", "traffic_requests"].some(
                            (k) =>
                              app.latest[k as keyof typeof app.latest] !==
                              undefined,
                          ) && (
                            <small className="domain">
                              No current usage values
                            </small>
                          )}
                        </td>
                        <td>
                          {app.latest.disk_used_gb !== undefined && (
                            <span
                              title={
                                app.latest.disk_used_gb_sample_at
                                  ? `Sample: ${time(app.latest.disk_used_gb_sample_at)}`
                                  : "Source sample time not provided"
                              }
                            >
                              {app.latest.disk_used_gb === 0
                                ? "0 GB"
                                : size(app.latest.disk_used_gb * 1e9)}
                            </span>
                          )}
                        </td>
                        <td>
                          <small
                            className="unavailable-value"
                            title="No application bandwidth value is provided by these Cloudways API responses."
                          >
                            Unavailable
                          </small>
                        </td>
                        <td>
                          {app.latest.traffic_requests !== undefined && (
                            <>
                              {numeric(app.latest.traffic_requests)}
                              {!app.latest.traffic_complete && (
                                <small className="partial-label">Partial</small>
                              )}
                              {app.latest.traffic_status === "pending" && (
                                <small
                                  className="partial-label"
                                  title={`Showing the completed request result collected ${time(app.latest.traffic_fetched_at)}`}
                                >
                                  Refreshing
                                </small>
                              )}
                              {app.latest.traffic_status === "rate_limited" && (
                                <small
                                  className="partial-label"
                                  title={`Showing the completed request result collected ${time(app.latest.traffic_fetched_at)}`}
                                >
                                  Rate limited
                                </small>
                              )}
                            </>
                          )}
                        </td>
                      </tr>
                      {expanded && expandedApps.has(app.id) && (
                        <tr className="analytics-row">
                          <td colSpan={4}>
                            <ApplicationAnalytics app={app} refresh={refresh} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            {!apps.length && (
              <p className="empty-state">No matching applications.</p>
            )}
            <p className="pane-footnote">
              {monitoredApps.length} / {apps.length} applications with current
              values · Blank cells have no current value.
            </p>
            <p className="pane-footnote">
              Application bandwidth is not returned by the monitored Cloudways
              API endpoints. Request analyses refresh no more than once every 5
              minutes. Counts marked Partial cover only the returned status
              rows.
            </p>
          </div>
        </div>
        <div className="server-diagnostics">
          {server.alerts.map((a) => (
            <p className="warning-message" key={a.id}>
              {a.severity === "critical" ? "Critical" : "Warning"}: {a.rule_key}
            </p>
          ))}
          <details>
            <summary>
              Collection details
              {diagnostics.length > 0 && (
                <span className="diagnostic-count">
                  {diagnostics.length} need attention
                </span>
              )}
            </summary>
            <p className="pane-footnote">
              Fetched: {time(server.latest.fetched_at)}. Fetch time does not
              replace source sample time.
            </p>
            {diagnostics.map((d, i) => (
              <p
                key={i}
                className={
                  d.status === "error" ? "error-message" : "warning-message"
                }
              >
                {d.metric}: {d.message}
              </p>
            ))}
            {apps
              .filter((a) => !monitoredApps.includes(a))
              .map((a) => (
                <p key={a.id} className="pane-footnote">
                  {a.name} · ID {a.provider_id} · No current monitoring values
                </p>
              ))}
            <button
              className="quiet-button"
              onClick={async () => {
                if (raw) {
                  setRaw(null);
                  return;
                }
                try {
                  setRaw(
                    JSON.stringify(
                      await api(`/api/resources/${server.id}/diagnostics`),
                      null,
                      2,
                    ),
                  );
                } catch (error) {
                  setRaw(errorMessage(error));
                }
              }}
            >
              {raw ? "Hide response" : "Inspect response"}
            </button>
            {raw && <pre className="raw-response">{raw}</pre>}
          </details>
        </div>
      </div>
    </section>
  );
}
