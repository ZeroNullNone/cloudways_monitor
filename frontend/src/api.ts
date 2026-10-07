export type Auth = { authenticated: boolean; username: string | null };
export type Diagnostic = { metric: string; status: string; message: string };
export type Metric = {
  target: string;
  value: number;
  comparable_value: number | null;
  unit: string | null;
  sample_at: string;
  stale: boolean;
};
export type Resource = {
  id: number;
  provider_id: string;
  resource_type: string;
  name: string;
  metadata: Record<string, string | boolean | null>;
  latest: {
    fetched_at: string | null;
    stale: boolean;
    diagnostics: Diagnostic[];
    metrics: Metric[];
    capacity?: {
      memory_gb?: number;
      disk_gb?: number;
      disk_source?: string;
      fetched_at: string;
    };
    disk_used_gb?: number;
    traffic_requests?: number;
    bandwidth_status?: "unavailable";
    traffic_complete?: boolean;
    traffic_window?: string;
    traffic_status?: string;
    traffic_fetched_at?: string;
    disk_used_gb_sample_at?: string;
  };
  alerts: { id: number; rule_key: string; severity: string; status: string }[];
  applications: Resource[];
};
export type Overview = {
  servers: Resource[];
  unassociated_applications: Resource[];
  telegram_enabled: boolean;
  refresh_seconds: number;
  timezone: string;
  collector: {
    status: string;
    last_run_at: string | null;
    last_error: string | null;
    snapshots_stored: number;
    stale: boolean;
  };
  attention: {
    active_alert_count: number;
    stale_resource_count: number;
    status: string;
  };
};
export type Rule = {
  target: string;
  unit: string;
  warning: number;
  critical: number;
  comparison: string;
};
export type Catalog = {
  metric_definitions: Record<
    string,
    { unit: string | null; description: string }
  >;
  targets: string[];
  durations: string[];
  timezone: string;
  alert_rules: Rule[];
};
export type Point = { timestamp: string; value: number | null };
export type Series = {
  freshness_seconds: number;
  cadence_seconds?: number | null;
  name: string;
  unit: string | null;
  points: Point[];
  summary: {
    latest?: number;
    min?: number;
    max?: number;
    avg?: number;
    count?: number;
    sample_at?: string;
  };
};
export type Graph = {
  target: string;
  duration: string;
  series: Series[];
  status: string;
  error: string | null;
  fetched_at: string;
};

export type ApplicationAnalysis = {
  category: string;
  duration: string;
  status: string;
  columns: string[];
  rows: (string | number | null)[][];
  summary: Record<string, number>;
  partial: boolean;
  fetched_at: string;
  error?: string;
  retry_after_seconds?: number;
};

export async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { credentials: "same-origin", ...init });
  if (!response.ok) {
    if (response.status === 401 && !url.endsWith("/auth/login"))
      window.dispatchEvent(new Event("session-expired"));
    const payload = await response.json().catch(() => ({}));
    throw new Error(
      typeof payload.detail === "string"
        ? payload.detail
        : `Request failed (HTTP ${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}

const timeFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Singapore",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});
const shortFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Singapore",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
export function time(value: string | null | undefined) {
  return value
    ? `${timeFormatter.format(new Date(value))} UTC+8`
    : "Not collected yet";
}
const dateFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Singapore",
  day: "2-digit",
  month: "short",
});
export function shortTime(value: number, span = 0) {
  return span >= 86400000
    ? dateFormatter.format(new Date(value))
    : shortFormatter.format(new Date(value));
}
export function numeric(value: number) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(
    value,
  );
}
export function size(bytes: number) {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(
    units.length - 1,
    Math.max(0, Math.floor(Math.log10(bytes) / 3)),
  );
  return `${numeric(bytes / 1000 ** index)} ${units[index]}`;
}
export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Request failed";
}

export function metricSeverity(
  target: string,
  value: number,
  unit: string | null,
  rules: Rule[],
  stale = false,
): "warning" | "critical" | null {
  if (stale) return null;
  const rule = rules.find((r) => r.target === target);
  if (!rule || !unit) return null;
  const factors: Record<string, number> = {
    B: 1,
    bytes: 1,
    KB: 1000,
    MB: 1e6,
    GB: 1e9,
    KiB: 1024,
    MiB: 1048576,
    GiB: 1073741824,
  };
  let comparable = value;
  if (unit !== rule.unit) {
    if (!factors[unit] || !factors[rule.unit]) return null;
    comparable = (value * factors[unit]) / factors[rule.unit];
  }
  const breached = (threshold: number) =>
    rule.comparison === ">="
      ? comparable >= threshold
      : comparable <= threshold;
  return breached(rule.critical)
    ? "critical"
    : breached(rule.warning)
      ? "warning"
      : null;
}
