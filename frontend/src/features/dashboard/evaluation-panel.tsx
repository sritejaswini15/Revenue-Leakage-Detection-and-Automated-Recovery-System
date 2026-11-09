"use client";

import { useEffect } from "react";
import { DetailMetric, ErrorState, Panel } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { formatDateTime, formatInr, formatPercent } from "../../lib/format";
import { api } from "../../services/api/recoverx";

// Wrapped so "no evaluation yet" (null) is distinguishable from "still loading".
const loadLatest = () => api.analytics.latestEvaluation().then((latest) => ({ latest }));

/** The last batch evaluation: every case replayed through the advisor and policy engine. */
export function EvaluationPanel({ runs }: Readonly<{ runs: number }>) {
  const { data, error, reload } = useApi(loadLatest);
  useEffect(() => {
    if (runs > 0) reload();
  }, [runs, reload]);

  const latest = data?.latest;
  const summary = latest?.summary;
  return (
    <Panel
      title="Batch evaluation"
      aside={
        summary ? (
          <span
            className={`status ${summary.evaluation_status === "PASSED" ? "status-recovered" : "status-escalated"}`}
          >
            {summary.evaluation_status}
          </span>
        ) : undefined
      }
      className="span-all"
    >
      {error && <ErrorState message={error} />}
      {data && !summary && (
        <p className="muted">No evaluation has run yet. Run one to replay every case through the advisor and policy.</p>
      )}
      {summary && latest && (
        <>
          <div className="detail-metrics evaluation-metrics">
            <DetailMetric label="Cases processed" value={summary.cases_processed.toLocaleString("en-IN")} />
            <DetailMetric label="Revenue at risk" value={formatInr(summary.revenue_at_risk)} />
            <DetailMetric label="Revenue targeted" value={formatInr(summary.revenue_targeted)} />
            <DetailMetric label="Revenue recovered" value={formatInr(summary.revenue_recovered)} />
            <DetailMetric label="Recovery rate" value={formatPercent(summary.recovery_rate, 1)} />
            <DetailMetric
              label="Avg. recovery probability"
              value={formatPercent(summary.average_recovery_probability, 0)}
            />
            <DetailMetric label="Successful recoveries" value={summary.successful_recoveries.toLocaleString("en-IN")} />
            <DetailMetric label="Escalations" value={summary.escalations.toLocaleString("en-IN")} />
            <DetailMetric label="False escalations" value={summary.false_escalations} />
            <DetailMetric label="Policy violations" value={summary.policy_violations} />
            <DetailMetric label="Invalid AI decisions" value={summary.invalid_ai_decisions} />
            <DetailMetric label="Action failures" value={summary.action_failures} />
            <DetailMetric label="Avg. recovery time" value={`${summary.average_recovery_time_hours} h`} />
          </div>
          <p className="muted small-print">
            Advisor {summary.advisor_model} · run by {latest.run_by} on {formatDateTime(latest.run_at)}. Recorded in the
            audit trail as evaluation.completed.
          </p>
        </>
      )}
    </Panel>
  );
}
