"use client";

import { useCallback, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AppShell } from "../../components/app-shell";
import { ErrorState, LoadingState, MetricCard, PageHeading, Panel } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { formatInr, formatPercent, humanize } from "../../lib/format";
import { api } from "../../services/api/recoverx";
import type { StrategyPerformance } from "../../types/api";
import { RecoveryFunnel, TrendChart } from "./charts";

export const PERIODS = [7, 30, 90] as const;

/** One window for every figure on the page: cases opened (or payments recorded) since 00:00 UTC. */
export function PeriodSelect({ days, onChange }: Readonly<{ days: number; onChange: (days: number) => void }>) {
  return (
    <select value={days} onChange={(event) => onChange(Number(event.target.value))} aria-label="Reporting period">
      {PERIODS.map((value) => (
        <option key={value} value={value}>
          Last {value} days
        </option>
      ))}
    </select>
  );
}

function StrategyChart({ strategies }: Readonly<{ strategies: StrategyPerformance[] }>) {
  const rows = strategies
    .filter((item) => item.attempts > 0)
    .map((item) => ({ name: humanize(item.strategy), worked: item.attempts, recovered: item.success }));
  if (!rows.length) return <p className="muted">No recovery actions have been taken yet.</p>;
  return (
    <div className="chart-container">
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={rows} margin={{ top: 4, right: 8, left: -12, bottom: 36 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#edf0f3" vertical={false} />
          <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-25} textAnchor="end" interval={0} />
          <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
          <Tooltip formatter={(value, name) => [Number(value), name === "worked" ? "Worked cases" : "Recovered"]} />
          <Bar dataKey="worked" fill="#dfe4ea" radius={[3, 3, 0, 0]} />
          <Bar dataKey="recovered" fill="#1d9b87" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function AnalyticsOverview() {
  const [days, setDays] = useState(30);
  const loadSummary = useCallback(
    () =>
      Promise.all([api.analytics.overview(days), api.analytics.strategies(days), api.analytics.trend(days)]).then(
        ([overview, strategies, trend]) => ({ overview, strategies, trend }),
      ),
    [days],
  );
  const summary = useApi(loadSummary);

  const data = summary.data;
  return (
    <AppShell title="Analytics">
      <PageHeading
        eyebrow="MEASURE OUTCOMES"
        title="Recovery analytics"
        subtitle="Computed from the database on load. Every card, chart and table covers cases opened in the selected period."
        actions={<PeriodSelect days={days} onChange={setDays} />}
      />
      {summary.error && <ErrorState message={summary.error} />}
      {!data && !summary.error && <LoadingState label="Loading analytics…" />}
      {data && (
        <>
          <section className="metric-grid">
            <MetricCard
              label="Revenue at risk"
              value={formatInr(data.overview.revenue_at_risk)}
              detail={`${data.overview.cases.toLocaleString("en-IN")} cases detected`}
              tone="coral"
            />
            <MetricCard
              label="Revenue targeted"
              value={formatInr(data.overview.revenue_targeted)}
              detail="Expected value of eligible cases"
              tone="blue"
            />
            <MetricCard
              label="Revenue recovered"
              value={formatInr(data.overview.revenue_recovered)}
              detail={`${formatPercent(data.overview.recovery_rate)} recovery rate`}
              tone="mint"
            />
            <MetricCard
              label="Successful recoveries"
              value={data.overview.successful_recoveries.toLocaleString("en-IN")}
              detail={`${formatPercent(data.overview.escalations / Math.max(data.overview.cases, 1))} escalation rate`}
              tone="amber"
            />
          </section>

          <section className="metric-grid">
            <MetricCard
              label="Payments captured"
              value={formatInr(data.overview.payments.captured_amount)}
              detail={`${data.overview.payments.captured.toLocaleString("en-IN")} payments`}
              tone="mint"
            />
            <MetricCard
              label="Payments failed"
              value={formatInr(data.overview.payments.failed_amount)}
              detail={`${data.overview.payments.failed.toLocaleString("en-IN")} payments`}
              tone="coral"
            />
            <MetricCard
              label="Payment success rate"
              value={formatPercent(data.overview.payments.success_rate)}
              detail="Captured ÷ (captured + failed)"
              tone="blue"
            />
            <MetricCard
              label="Awaiting capture"
              value={data.overview.payments.authorized.toLocaleString("en-IN")}
              detail="Authorized, not yet captured"
            />
          </section>

          <section className="main-grid">
            <Panel title="Revenue trend" aside={`By day opened, last ${days} days`}>
              <TrendChart points={data.trend} height={240} />
            </Panel>
            <Panel title="Recovery funnel" aside="Cases at each stage">
              <RecoveryFunnel analytics={data.overview} />
            </Panel>
          </section>

          <section className="main-grid">
            <Panel title="Strategy performance" aside="Worked vs recovered cases">
              <StrategyChart strategies={data.strategies} />
            </Panel>
            <Panel title="Operational health" aside="Live from the database">
              <div className="stat-grid">
                <Stat label="Auto-approved by policy" value={data.overview.auto_approved.toLocaleString("en-IN")} />
                <Stat label="In human review" value={data.overview.escalations.toLocaleString("en-IN")} tone="amber" />
                <Stat label="Stopped" value={data.overview.stopped_cases.toLocaleString("en-IN")} tone="coral" />
                <Stat
                  label="Failed actions"
                  value={data.overview.action_failures.toLocaleString("en-IN")}
                  tone="coral"
                />
                <Stat
                  label="Average advisor confidence"
                  value={formatPercent(data.overview.average_ai_confidence, 0)}
                />
                <Stat
                  label="Average time to recover"
                  value={`${data.overview.average_recovery_time_hours.toFixed(1)}h`}
                  tone="blue"
                />
              </div>
            </Panel>
          </section>

          <Panel title="Strategy details" aside="Recovered amount by strategy">
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th>Cases</th>
                    <th>Worked</th>
                    <th>Recovered</th>
                    <th>Conversion</th>
                    <th>Revenue recovered</th>
                    <th>Avg time</th>
                  </tr>
                </thead>
                <tbody>
                  {data.strategies.map((row) => (
                    <tr key={row.strategy}>
                      <td>
                        <b>{humanize(row.strategy)}</b>
                      </td>
                      <td>{row.cases}</td>
                      <td>{row.attempts}</td>
                      <td className="success-text">{row.success}</td>
                      <td>{row.attempts ? formatPercent(row.recovery_rate, 0) : "—"}</td>
                      <td className="mono">{formatInr(row.recovered)}</td>
                      <td className="muted">{row.success ? `${row.average_recovery_time.toFixed(1)}h` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </>
      )}
    </AppShell>
  );
}

function Stat({ label, value, tone = "" }: Readonly<{ label: string; value: string; tone?: string }>) {
  return (
    <div className="stat-box">
      <span>{label}</span>
      <b className={tone}>{value}</b>
    </div>
  );
}
