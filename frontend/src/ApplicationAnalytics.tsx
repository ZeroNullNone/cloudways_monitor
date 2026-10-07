import { useEffect, useRef, useState } from "react";
import {
  api,
  errorMessage,
  numeric,
  time,
  type ApplicationAnalysis,
  type Resource,
} from "./api";

const durations: Record<string, string> = {
  "15m": "Last 15 minutes",
  "30m": "Last 30 minutes",
  "1h": "Last 1 hour",
  "1d": "Last 1 day",
};
const names: Record<string, string> = {
  returned_requests: "Returned requests",
  client_errors: "Client errors (4xx)",
  server_errors: "Server errors (5xx)",
  returned_urls: "Returned URLs",
  returned_slow_pages: "Returned slow pages",
  returned_slow_queries: "Returned slow queries",
};
const empty: Record<string, string> = {
  statuses: "No status rows returned for this period.",
  urls: "No URLs returned for this period.",
  php: "No slow pages returned for this period.",
  mysql: "No slow queries returned for this period.",
};

export function ApplicationAnalytics({
  app,
  refresh,
}: {
  app: Resource;
  refresh: number;
}) {
  const [category, setCategory] = useState("statuses");
  const [duration, setDuration] = useState("1h");
  const [result, setResult] = useState<ApplicationAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const cache = useRef(new Map<string, ApplicationAnalysis>());
  useEffect(() => {
    const key = `${category}|${duration}`;
    const controller = new AbortController();
    let disposed = false;
    let retry: ReturnType<typeof setTimeout>;
    setResult(cache.current.get(key) ?? null);
    setError(null);
    setLoading(true);
    async function load() {
      try {
        const params = new URLSearchParams({ category, duration });
        const next = await api<ApplicationAnalysis>(
          `/api/applications/${app.id}/analytics?${params}`,
          { signal: controller.signal },
        );
        if (disposed) return;
        setResult(next);
        setLoading(false);
        cache.current.set(key, next);
        if (next.status === "pending") retry = setTimeout(load, 3500);
        else if (next.retry_after_seconds)
          retry = setTimeout(load, (next.retry_after_seconds + 1) * 1000);
      } catch (e) {
        if (disposed) return;
        setLoading(false);
        setError(errorMessage(e));
      }
    }
    void load();
    return () => {
      disposed = true;
      controller.abort();
      clearTimeout(retry);
    };
  }, [app.id, category, duration, refresh]);
  return (
    <section
      className="application-analytics"
      aria-label={`Analytics for ${app.name}`}
    >
      <div className="analytics-controls">
        <label>
          Analysis
          <select
            aria-label={`Analysis for ${app.name}`}
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="statuses">HTTP status codes</option>
            <option value="urls">Top URLs</option>
            <option value="php">Slow PHP pages</option>
            <option value="mysql">Slow SQL queries</option>
          </select>
        </label>
        <label>
          Period
          <select
            aria-label={`Analysis duration for ${app.name}`}
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
          >
            {Object.entries(durations).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {loading && (
        <p className="pane-footnote" role="status">
          {result?.status === "ok"
            ? "Refreshing analysis…"
            : "Loading application analysis…"}
        </p>
      )}
      {error && (
        <p className="error-message" role="alert">
          {error}
        </p>
      )}
      {result?.status === "pending" && (
        <p className="pane-footnote" role="status">
          Cloudways is preparing this analysis. Checking again shortly.
        </p>
      )}
      {result?.error && (
        <p className="error-message" role="alert">
          {result.error}
        </p>
      )}
      {result?.retry_after_seconds && (
        <p className="pane-footnote" role="status">
          Retrying automatically after the Cloudways rate limit clears.
        </p>
      )}
      {result?.status === "ok" && (
        <>
          <div className="analytics-result-meta">
            <span>{durations[result.duration]}</span>
            <span>Partial results</span>
          </div>
          <dl className="analytics-summary">
            {Object.entries(result.summary).map(([key, value]) => (
              <div key={key}>
                <dt>{names[key] ?? key}</dt>
                <dd
                  className={
                    value > 0 && key === "server_errors"
                      ? "metric-critical"
                      : value > 0 && key === "client_errors"
                        ? "metric-warning"
                        : undefined
                  }
                >
                  {numeric(value)}
                </dd>
              </div>
            ))}
          </dl>
          {result.rows.length > 0 ? (
            <div className="analytics-table">
              <table>
                <thead>
                  <tr>
                    {result.columns.map((column, i) => (
                      <th key={i}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((row, i) => (
                    <tr key={i}>
                      {row.map((cell, j) => (
                        <td key={j}>
                          {typeof cell === "number"
                            ? numeric(cell)
                            : (cell ?? "")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="pane-footnote">{empty[category]}</p>
          )}
          <p className="pane-footnote analytics-note">
            Returned rows only; this is not a full application total. Collected{" "}
            {time(result.fetched_at)}.
          </p>
        </>
      )}
    </section>
  );
}
