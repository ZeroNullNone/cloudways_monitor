import { useEffect, useMemo, useRef, useState } from "react";
import { numeric, shortTime, time, type Series } from "./api";

export function TimeChart({
  series,
  target,
  duration,
}: {
  series: Series;
  target: string;
  duration: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    const observer = new ResizeObserver((entries) =>
      setWidth(Math.max(260, entries[0].contentRect.width)),
    );
    if (container.current) observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  const points = series.points;
  const chart = useMemo(() => {
    const valid = points.filter((p) => p.value !== null);
    if (!valid.length) return null;
    const times = points.map((p) => Date.parse(p.timestamp));
    let start = times[0],
      end = times[times.length - 1];
    if (start === end) {
      start -= 30000;
      end += 30000;
    }
    const values = valid.map((p) => p.value as number);
    let low = Math.min(...values),
      high = Math.max(...values);
    if (low === high) {
      low -= Math.max(Math.abs(low) * 0.05, 1);
      high += Math.max(Math.abs(high) * 0.05, 1);
    }
    const pad = (high - low) * 0.1;
    low -= pad;
    high += pad;
    const x = (timestamp: number) =>
      64 + ((timestamp - start) / (end - start)) * (width - 90);
    const y = (value: number) => 24 + ((high - value) / (high - low)) * 174;
    const intervals = times
      .slice(1)
      .map((t, i) => t - times[i])
      .filter((d) => d > 0)
      .sort((a, b) => a - b);
    const step = intervals[Math.floor(intervals.length / 2)];
    const paths: string[] = [];
    let path = "";
    for (let i = 0; i < points.length; i++) {
      if (step && i > 0 && times[i] - times[i - 1] > step * 2) {
        if (path) paths.push(path);
        path = "";
      }
      const point = points[i];
      if (point.value === null) {
        if (path) paths.push(path);
        path = "";
        continue;
      }
      path += `${path ? " L" : "M"}${x(times[i])},${y(point.value)}`;
    }
    if (path) paths.push(path);
    return { start, end, low, high, x, y, paths, times };
  }, [points, width]);
  if (!chart)
    return (
      <div ref={container} className="chart-empty">
        No samples in this range
      </div>
    );
  const hovered = hover === null ? null : points[hover];
  return (
    <div ref={container} className="chart">
      <div className="chart-unit">
        {series.unit ?? "Unit not provided"} <span>UTC+8</span>
      </div>
      <svg
        viewBox={`0 0 ${width} 240`}
        role="img"
        aria-label={`${target} time series, ${duration}; all times UTC+8`}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const px = ((event.clientX - rect.left) / rect.width) * width;
          const timestamp =
            chart.start +
            ((px - 64) / (width - 90)) * (chart.end - chart.start);
          let nearest = 0;
          points.forEach((_, i) => {
            if (
              Math.abs(chart.times[i] - timestamp) <
              Math.abs(chart.times[nearest] - timestamp)
            )
              nearest = i;
          });
          setHover(nearest);
        }}
        onMouseLeave={() => setHover(null)}
      >
        <title>
          {target} in {series.unit ?? "unverified units"}. Missing samples
          remain gaps.
        </title>
        {[0, 1, 2, 3].map((i) => {
          const value = chart.high - ((chart.high - chart.low) * i) / 3;
          const y = chart.y(value);
          return (
            <g key={i}>
              <line
                className="grid-line"
                x1="64"
                x2={width - 26}
                y1={y}
                y2={y}
              />
              <text x="54" y={y + 4} textAnchor="end">
                {numeric(value)}
              </text>
            </g>
          );
        })}
        {chart.paths.map((path, i) => (
          <path key={i} className="chart-line" d={path} />
        ))}
        {points.length <= 200 &&
          points.map((p, i) =>
            p.value === null ? null : (
              <circle
                key={p.timestamp}
                className="chart-dot"
                cx={chart.x(chart.times[i])}
                cy={chart.y(p.value)}
                r="2"
              />
            ),
          )}
        {[0, 1, 2, 3, 4].map((i) => {
          const timestamp = chart.start + ((chart.end - chart.start) * i) / 4;
          if (width < 400 && i % 2) return null;
          return (
            <text key={i} x={chart.x(timestamp)} y="224" textAnchor="middle">
              {shortTime(timestamp, chart.end - chart.start)}
            </text>
          );
        })}
        {hovered && (
          <line
            className="cursor-line"
            x1={chart.x(Date.parse(hovered.timestamp))}
            x2={chart.x(Date.parse(hovered.timestamp))}
            y1="20"
            y2="199"
          />
        )}
      </svg>
      <div className="chart-tooltip" aria-live="polite">
        {hovered ? (
          <>
            <span>{time(hovered.timestamp)}</span>
            <strong>
              {hovered.value === null
                ? "No sample"
                : `${numeric(hovered.value)} ${series.unit ?? ""}`}
            </strong>
          </>
        ) : null}
      </div>
    </div>
  );
}
