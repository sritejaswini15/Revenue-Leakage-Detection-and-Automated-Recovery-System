"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../components/app-shell";
import { DetailMetric, ErrorState, LoadingState, Notice, Panel, StatusBadge } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { formatDateTime, formatInr, formatPercent, formatReasons, humanize } from "../../lib/format";
import { errorMessage } from "../../services/api/client";
import { api } from "../../services/api/recoverx";
import { CaseActions, type RunCommand } from "./case-actions";

const REFRESH_INTERVAL_MS = 8000;

/** Who produced the diagnosis, from the model recorded with the decision. */
function describeAdvisor(model: string | null, rootCause: string) {
  if (rootCause === "ADVISOR_OUTPUT_INVALID") {
    return {
      badge: "Safe failure",
      note: `The advisor (${model ?? "unknown"}) produced no valid recommendation, so nothing ran and the case went to a person.`,
    };
  }
  if (model === "synthetic-seed") {
    return { badge: "Demo data", note: "This diagnosis was generated with the demo dataset, not by the advisor." };
  }
  if (model?.startsWith("claude")) {
    return {
      badge: "Claude",
      note: `Recommended by ${model} inside RecoverX's LangGraph advisor, validated against a strict schema. It recommends only; the policy engine decided.`,
    };
  }
  return {
    badge: "Rule-based advisor",
    note: "Produced by RecoverX's deterministic decision rules (LangGraph); no machine-learning model is involved.",
  };
}

export function CaseInvestigation({ caseId }: Readonly<{ caseId: string }>) {
  const load = useCallback(
    () =>
      Promise.all([api.cases.get(caseId), api.audit(100, caseId)]).then(async ([item, audit]) => ({
        item,
        events: audit.items,
        customer: await api.customers.get(item.customer_id),
      })),
    [caseId],
  );
  const { data, error, reload } = useApi(load);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [seenStatus, setSeenStatus] = useState<string | null>(null);
  // Set when this page's own action is about to change the status, so its feedback is not overwritten.
  const [ownChangePending, setOwnChangePending] = useState(false);

  // Poll so payments completed on the customer page show up without a refresh.
  useEffect(() => {
    const timer = window.setInterval(reload, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [reload]);

  const status = data?.item.status ?? null;
  if (status !== seenStatus) {
    // Announce only changes that happened elsewhere, e.g. the customer paying while this page is open.
    if (seenStatus && status && !ownChangePending) setNotice(`Case updated: ${humanize(status)}`);
    if (ownChangePending) setOwnChangePending(false);
    setSeenStatus(status);
  }

  const run: RunCommand = async (action, success, failure) => {
    setBusy(true);
    try {
      await action();
      setNotice(success);
      setOwnChangePending(true);
      reload();
      return true;
    } catch (problem) {
      setNotice(errorMessage(problem, failure));
      reload(); // a refusal is audited, so the timeline shows it
      return false;
    } finally {
      setBusy(false);
    }
  };

  if (!data) {
    return (
      <AppShell title="Case investigation">
        <Link className="back-link" href="/cases">
          ← Back to cases
        </Link>
        {error ? <ErrorState message={error} /> : <LoadingState label="Loading case investigation…" />}
      </AppShell>
    );
  }

  const { item, events, customer } = data;
  const link = item.payment_link;
  const customerUrl = link?.payment_url ?? link?.short_url;
  const stats = customer.stats;
  const policy = item.policy_evaluation;

  return (
    <AppShell title="Case investigation">
      <Link className="back-link" href="/cases">
        ← Back to cases
      </Link>
      <div className="route-heading">
        <div>
          <p className="eyebrow">RECOVERY CASE {item.id}</p>
          <h1>{item.customer}</h1>
          <p className="subheading">
            {humanize(item.event_type)} · {formatInr(item.amount)}
          </p>
        </div>
        <StatusBadge status={item.status} />
      </div>
      <Notice message={notice} />

      <CaseActions item={item} busy={busy} run={run} />

      <div className="detail-grid">
        <Panel title="Risk assessment" aside={<span className="status status-recovered">Deterministic</span>}>
          <div className="detail-metrics">
            <DetailMetric label="Risk score" value={`${item.risk_score}/100`} />
            <DetailMetric label="Recovery probability" value={formatPercent(item.recovery_probability, 0)} />
            <DetailMetric label="Expected recovery" value={formatInr(item.expected_recovery_value)} />
            <DetailMetric label="Advisor confidence" value={formatPercent(item.ai_confidence, 0)} />
            <DetailMetric label="Contact attempts" value={item.contact_attempts} />
            {item.failure_reason && <DetailMetric label="Failure reason" value={humanize(item.failure_reason)} />}
          </div>
          {item.risk_reasons.length > 0 && (
            <>
              <p className="eyebrow reasons-heading">WHY THIS CASE IS PRIORITISED</p>
              <ul className="reason-list">
                {item.risk_reasons.map((reason) => (
                  <li key={reason.text} className={`reason-${reason.effect}`}>
                    {reason.text}
                  </li>
                ))}
              </ul>
              <p className="muted small-print">
                Expected recovery = {formatInr(item.amount)} × {formatPercent(item.recovery_probability, 0)}. Cases are
                worked in order of expected recovery, not amount.
              </p>
            </>
          )}
        </Panel>

        <Panel
          title="Customer context"
          aside={<span className="status status-recovered">Explainable</span>}
          className="customer-context"
        >
          <h3>{customer.name}</h3>
          <p className="muted">
            {customer.email} · {customer.phone}
            {customer.opted_out && " · Opted out"}
          </p>
          <div className="context-grid">
            <DetailMetric label="Lifetime value" value={formatInr(customer.lifetime_value)} />
            <DetailMetric label="Reliability" value={`${stats.reliability_pct}%`} />
            <DetailMetric label="Successful payments" value={stats.successful_payments} />
            <DetailMetric label="Failed payments" value={stats.failed_payments} />
            <DetailMetric label="Historical recovery" value={formatPercent(customer.historical_recovery_rate, 0)} />
            <DetailMetric label="Preferred channel" value={humanize(customer.preferred_channel)} />
            <DetailMetric label="Preferred language" value={customer.preferred_language} />
          </div>
          <p className="muted small-print">
            Profile {stats.profile_reliability_pct}% over {stats.profile_successful + stats.profile_failed} payments,
            plus {stats.observed_successful} paid and {stats.observed_failed} failed payments seen by RecoverX.{" "}
            <Link href={`/customers/${encodeURIComponent(customer.id)}`}>Customer profile</Link>
          </p>
        </Panel>

        <Panel
          title="Recovery diagnosis"
          aside={
            <span className="status status-actionable">
              {describeAdvisor(item.advisor_model, item.root_cause).badge}
            </span>
          }
        >
          <div className="ai-block">
            <p className="eyebrow">DIAGNOSIS</p>
            <h3>{item.diagnosis}</h3>
            <p>
              <strong>Root cause:</strong> {humanize(item.root_cause)}
            </p>
            <p className="muted">{item.ai_reasoning}</p>
          </div>
          <p>
            <strong>Recommended strategy:</strong> {humanize(item.recommended_action)}
          </p>
          <p className="muted">{describeAdvisor(item.advisor_model, item.root_cause).note}</p>
        </Panel>

        <Panel title="Policy evaluation" aside={<span className="status status-recovered">Backend guardrail</span>}>
          {policy ? (
            <>
              <ul className="reason-list">
                {policy.reasons.map((reason) => (
                  <li key={reason} className={policy.violations.length ? "reason-neutral" : "reason-positive"}>
                    {reason}
                  </li>
                ))}
              </ul>
              {policy.violations.length > 0 && (
                <p className="muted small-print">Rules triggered: {policy.violations.map(humanize).join(", ")}</p>
              )}
              <h3 className="policy-decision">Now: {humanize(policy.decision)}</h3>
              <p className="muted small-print">
                Decided when the case opened: {humanize(item.policy_decision)}. The merchant&apos;s current policy is
                applied above, and the Tool Gateway re-checks it before every action.
              </p>
            </>
          ) : (
            <h3 className="policy-decision">{humanize(item.policy_decision)}</h3>
          )}
        </Panel>

        {link && (
          <Panel title="Payment link" aside={<StatusBadge status={link.status} />}>
            <div className="context-grid">
              <DetailMetric label="Amount" value={formatInr(link.amount)} />
              <DetailMetric label="Provider link" value={link.provider_link_id ?? "—"} />
              {customerUrl && (
                <DetailMetric
                  label="Customer payment page"
                  value={
                    <a href={customerUrl} target="_blank" rel="noopener noreferrer">
                      {customerUrl}
                    </a>
                  }
                />
              )}
            </div>
          </Panel>
        )}

        <Panel
          title="Actions and payment attempts"
          aside={<Link href={`/transactions?case_id=${encodeURIComponent(item.id)}`}>Transactions</Link>}
        >
          {item.actions.length === 0 && item.payment_attempts.length === 0 && (
            <p className="muted">No action has run on this case yet.</p>
          )}
          {item.actions.map((entry) => (
            <div key={entry.id} className="queue-row">
              <span>
                <b>{humanize(entry.action_type)}</b>
                <small className="muted">
                  {formatDateTime(entry.created_at)}
                  {entry.detail ? ` · ${entry.detail}` : ""}
                </small>
              </span>
              <StatusBadge status={entry.status} />
            </div>
          ))}
          {item.payment_attempts.map((attempt) => (
            <div key={`${attempt.provider_payment_id}-${attempt.attempt_id}`} className="queue-row">
              <span>
                <b>Payment attempt</b>
                <small className="muted">
                  {attempt.provider_payment_id} · {formatDateTime(attempt.created_at)}
                  {attempt.failure_reason ? ` · ${humanize(attempt.failure_reason)}` : ""}
                </small>
              </span>
              <StatusBadge status={attempt.status} />
            </div>
          ))}
        </Panel>

        {item.escalation && (
          <Panel title="Escalation" aside={<span className="status status-escalated">Human review</span>}>
            <p>
              <strong>Reason:</strong> {formatReasons(item.escalation.reason)}
            </p>
            {item.escalation.resolution && (
              <p>
                <strong>Resolution:</strong> {item.escalation.resolution}
              </p>
            )}
            {item.escalation.resolution_notes && <p className="muted">{item.escalation.resolution_notes}</p>}
          </Panel>
        )}

        {item.communications.length > 0 && (
          <Panel title="Communications" aside={`${item.communications.length} sent`}>
            <div className="activity-list">
              {item.communications.map((message) => (
                <div key={message.id} className="activity-item">
                  <span className="activity-icon blue" aria-hidden>
                    {message.channel.toUpperCase() === "WHATSAPP" ? "📱" : "📧"}
                  </span>
                  <span className="activity-copy">
                    <b>
                      {humanize(message.channel)} · {humanize(message.status)}
                    </b>
                    <small className="message-preview">{message.content}</small>
                  </span>
                  <span className="activity-time">{formatDateTime(message.created_at)}</span>
                </div>
              ))}
            </div>
          </Panel>
        )}

        {item.promises_to_pay.length > 0 && (
          <Panel title="Promise to pay" aside={`${item.promises_to_pay.length} recorded`}>
            {item.promises_to_pay.map((promise) => (
              <div key={promise.id} className="queue-row">
                <span>
                  <b>{formatInr(promise.amount)}</b> by {new Date(promise.promised_date).toLocaleDateString("en-IN")}
                </span>
                <StatusBadge status={promise.status} />
              </div>
            ))}
          </Panel>
        )}

        <Panel title="Complete timeline" aside={`${events.length} events`} className="span-all">
          <div className="timeline">
            {events.length ? (
              [...events].reverse().map((event) => (
                <div className="timeline-item" key={event.id}>
                  <span className="timeline-dot" />
                  <span>
                    <b>{humanize(event.event_name)}</b>
                    <small>
                      {event.actor} · {formatDateTime(event.created_at)}
                    </small>
                    {Object.keys(event.payload).length > 0 && (
                      <small className="timeline-payload">
                        {Object.entries(event.payload)
                          .slice(0, 3)
                          .map(([key, value]) => `${key}: ${JSON.stringify(value)}`)
                          .join(" · ")}
                      </small>
                    )}
                  </span>
                </div>
              ))
            ) : (
              <p className="muted">No audit events recorded for this case.</p>
            )}
          </div>
        </Panel>
      </div>
    </AppShell>
  );
}
