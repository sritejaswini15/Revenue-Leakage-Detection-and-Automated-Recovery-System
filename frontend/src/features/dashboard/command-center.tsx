"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { AppShell } from "../../components/app-shell";
import { ErrorState, LoadingState, MetricCard, Notice, Panel, RiskBadge, StatusBadge } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { useSession } from "../../hooks/use-session";
import { formatDateTime, formatInr, formatPercent, humanize, initials } from "../../lib/format";
import { canReview } from "../../lib/roles";
import { errorMessage } from "../../services/api/client";
import { api } from "../../services/api/recoverx";
import { PeriodSelect } from "../analytics/analytics-overview";
import { RecoveryFunnel, StrategyBars, TrendChart } from "../analytics/charts";
import { EvaluationPanel } from "./evaluation-panel";

function greeting(name: string | undefined) {
  const hour = new Date().getHours();
  const part = hour < 12 ? "morning" : hour < 17 ? "afternoon" : "evening";
  return name ? `Good ${part}, ${name.split(" ")[0]}` : `Good ${part}`;
}

export function CommandCenter() {
  const session = useSession();
  const [days, setDays] = useState(30);
  const [notice, setNotice] = useState("");
  const [running, setRunning] = useState(false);
  const [evaluationRuns, setEvaluationRuns] = useState(0);
  // Cards, funnel, trend and strategies all cover the same period.
  const loadOverview = useCallback(
    () =>
      Promise.all([
        api.analytics.overview(days),
        api.cases.list({ status: "OPEN", limit: 5 }), // the work: closed cases never crowd it out
        api.analytics.strategies(days),
        api.audit(8),
        api.analytics.trend(days),
      ]).then(([analytics, cases, strategies, activity, trend]) => ({
        analytics,
        cases: cases.items,
        strategies,
        activity,
        trend,
      })),
    [days],
  );
  const overview = useApi(loadOverview);

  const runEvaluation = async () => {
    setRunning(true);
    setNotice("Replaying every case through the advisor and policy engine…");
    try {
      const result = await api.analytics.evaluate();
      setNotice(
        `Evaluation ${result.evaluation_status}: ${result.cases_processed} cases, ` +
          `${result.policy_violations} policy violations, ${result.invalid_ai_decisions} invalid advisor decisions.`,
      );
      overview.reload();
      setEvaluationRuns((runs) => runs + 1);
    } catch (error) {
      setNotice(errorMessage(error, "Evaluation failed. Check the API and retry."));
    } finally {
      setRunning(false);
    }
  };

  const data = overview.data;
  return (
    <AppShell title="Command center">
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            LIVE RECOVERX CONTROL PLANE · {data ? data.analytics.mode.toUpperCase() : "CONNECTING"} MODE
          </p>
          <h1>{greeting(session?.user?.name)}</h1>
          <p className="subheading">Here&apos;s what&apos;s happening with your revenue recovery.</p>
        </div>
        <div className="heading-actions">
          <PeriodSelect days={days} onChange={setDays} />
          {canReview(session?.user.role) && (
            <button type="button" className="primary-button" onClick={runEvaluation} disabled={running || !data}>
              {running ? "Evaluating…" : "Run recovery evaluation"}
            </button>
          )}
        </div>
      </div>
      <Notice message={notice} />
      {overview.error && <ErrorState message={overview.error} />}
      {!data && !overview.error && <LoadingState label="Loading live recovery signals…" />}
      {data && (
        <>
          <section className="metric-grid">
            <MetricCard
              label="Revenue at risk"
              value={formatInr(data.analytics.revenue_at_risk)}
              detail={`across ${data.analytics.cases.toLocaleString("en-IN")} cases opened in ${days} days`}
              tone="coral"
            />
            <MetricCard
              label="Revenue recovered"
              value={formatInr(data.analytics.revenue_recovered)}
              detail={`${data.analytics.successful_recoveries.toLocaleString("en-IN")} successful recoveries`}
              tone="mint"
            />
            <MetricCard
              label="Recovery rate"
              value={formatPercent(data.analytics.recovery_rate)}
              detail="of revenue at risk"
              tone="amber"
            />
            <MetricCard
              label="Needs attention"
              value={data.analytics.escalations.toLocaleString("en-IN")}
              detail={`of this period's cases are in human review`}
              tone="blue"
            />
          </section>

          <section className="main-grid">
            <Panel title="Recovery funnel" aside={<Link href="/analytics">View analytics</Link>}>
              <RecoveryFunnel analytics={data.analytics} />
            </Panel>
            <Panel title="Recovery trend" aside="By case opening date">
              <TrendChart points={data.trend} />
            </Panel>
          </section>

          <section className="lower-grid">
            <Panel title="Priority recovery cases" aside={<Link href="/cases">View all cases →</Link>}>
              <div className="case-table">
                <div className="table-row table-head">
                  <span>Customer</span>
                  <span>Amount at risk</span>
                  <span>Expected recovery</span>
                  <span>Risk</span>
                  <span>Status</span>
                </div>
                {data.cases.map((item) => (
                  <Link className="table-row case-row" href={`/cases/${item.id}`} key={item.id}>
                    <span className="customer-cell">
                      <span className="customer-avatar">{initials(item.customer)}</span>
                      <span>
                        <b>{item.customer}</b>
                        <small>
                          {humanize(item.event_type)} · {item.id}
                        </small>
                      </span>
                    </span>
                    <span className="amount-cell">{formatInr(item.amount)}</span>
                    <span>
                      {formatInr(item.expected_recovery_value)}
                      <small className="muted">{formatPercent(item.recovery_probability, 0)} probability</small>
                    </span>
                    <span>
                      <RiskBadge score={item.risk_score} />
                    </span>
                    <span>
                      <StatusBadge status={item.status} />
                    </span>
                  </Link>
                ))}
              </div>
            </Panel>
            <Panel title="Strategy performance" aside="Recovered cases / worked cases">
              <StrategyBars strategies={data.strategies} />
            </Panel>
          </section>

          <Panel
            title="Recent activity"
            aside={<Link href="/audit">Full audit trail →</Link>}
            className="activity-feed"
          >
            <div className="activity-list">
              {data.activity.items.length ? (
                data.activity.items.map((event) => (
                  <div className="activity-item" key={event.id}>
                    <span className="activity-icon blue">•</span>
                    <span className="activity-copy">
                      <b>{humanize(event.event_name)}</b>
                      <small>{event.case_id ?? "System event"}</small>
                    </span>
                    <span className="activity-time">{formatDateTime(event.created_at)}</span>
                  </div>
                ))
              ) : (
                <p className="muted">No recovery activity yet.</p>
              )}
            </div>
          </Panel>
          <EvaluationPanel runs={evaluationRuns} />
        </>
      )}
    </AppShell>
  );
}
