"use client";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatInr, formatPercent, humanize } from "../../lib/format";
import type { Analytics, StrategyPerformance, TrendPoint } from "../../types/api";

const COLORS = { detected: "#ef725d", recovered: "#1d9b87" };

function shortDate(iso: string) {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
}

function compactInr(value: number) {
  return value >= 100000 ? `₹${(value / 100000).toFixed(1)}L` : `₹${Math.round(value / 1000)}k`;
}

export function TrendChart({ points, height = 220 }: Readonly<{ points: TrendPoint[]; height?: number }>) {
  if (!points.some((point) => point.cases > 0)) {
    return <p className="muted chart-empty">No recovery cases were opened in this period.</p>;
  }
  return (
    <div className="chart-container">
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#edf0f3" vertical={false} />
          <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fontSize: 10 }} minTickGap={24} />
          <YAxis tickFormatter={compactInr} tick={{ fontSize: 10 }} width={48} />
          <Tooltip
            labelFormatter={(label) => shortDate(String(label))}
            formatter={(value, name) => [formatInr(Number(value)), name === "detected" ? "At risk" : "Recovered"]}
          />
          <Area type="monotone" dataKey="detected" stroke={COLORS.detected} fill={COLORS.detected} fillOpacity={0.12} />
          <Area
            type="monotone"
            dataKey="recovered"
            stroke={COLORS.recovered}
            fill={COLORS.recovered}
            fillOpacity={0.25}
          />
        </AreaChart>
      </ResponsiveContainer>
      <div className="chart-legend">
        <span>
          <i style={{ background: COLORS.detected }} /> Revenue at risk
        </span>
        <span>
          <i style={{ background: COLORS.recovered }} /> Recovered
        </span>
      </div>
    </div>
  );
}

export function RecoveryFunnel({ analytics }: Readonly<{ analytics: Analytics }>) {
  const { funnel } = analytics;
  const steps = [
    { label: "Revenue risk cases", value: funnel.risk_cases, color: "navy" },
    { label: "Eligible for recovery", value: funnel.eligible, color: "blue" },
    { label: "Recovery actions taken", value: funnel.actions, color: "teal" },
    { label: "Successful recoveries", value: funnel.successful, color: "green" },
  ];
  const top = Math.max(funnel.risk_cases, 1);
  return (
    <>
      <div className="funnel">
        {steps.map((step) => (
          <div className="funnel-row" key={step.label}>
            <div className="funnel-label">
              <span>{step.label}</span>
              <b>{step.value.toLocaleString("en-IN")}</b>
            </div>
            <div className="funnel-track">
              <div className={`funnel-bar ${step.color}`} style={{ width: `${(step.value / top) * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
      <div className="funnel-footer">
        <span>
          <b>{formatInr(analytics.revenue_targeted)}</b> targeted
        </span>
        <span>
          <b>{formatInr(analytics.revenue_recovered)}</b> recovered
        </span>
        <span className="success-text">{formatPercent(analytics.recovery_rate)} recovery rate</span>
      </div>
    </>
  );
}

export function StrategyBars({ strategies }: Readonly<{ strategies: StrategyPerformance[] }>) {
  const worked = strategies.filter((item) => item.attempts > 0);
  if (!worked.length) return <p className="muted">No recovery actions have been taken yet.</p>;
  return (
    <div className="strategy-list">
      {worked.map((item) => (
        <div className="strategy" key={item.strategy}>
          <div className="strategy-title">
            <span className="strategy-icon teal">↗</span>
            <span>
              <b>{humanize(item.strategy)}</b>
              <small>{item.attempts.toLocaleString("en-IN")} worked cases</small>
            </span>
            <strong>{formatInr(item.recovered)}</strong>
          </div>
          <div className="strategy-bar">
            <div className="strategy-progress teal" style={{ width: formatPercent(item.recovery_rate, 0) }} />
          </div>
          <div className="strategy-meta">
            <span>
              <b>{item.success}</b> recovered
            </span>
            <span>{formatPercent(item.recovery_rate, 0)} conversion</span>
          </div>
        </div>
      ))}
    </div>
  );
}
