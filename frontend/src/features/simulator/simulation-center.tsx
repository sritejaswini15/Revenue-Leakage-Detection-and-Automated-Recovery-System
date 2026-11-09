"use client";

import Link from "next/link";
import { useState } from "react";
import { AppShell } from "../../components/app-shell";
import { EmptyState, ErrorState, LoadingState, PageHeading, StatusBadge } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { useSession } from "../../hooks/use-session";
import { canOperate } from "../../lib/roles";
import { formatInr, humanize } from "../../lib/format";
import { errorMessage } from "../../services/api/client";
import { api } from "../../services/api/recoverx";
import type { CaseSummary } from "../../types/api";

const EVENT_TYPES = [
  "PAYMENT_FAILURE",
  "SUBSCRIPTION_HALTED",
  "INVOICE_OVERDUE",
  "CHECKOUT_ABANDONED",
  "PAYMENT_LINK_ABANDONED",
];
const VISIBLE_CASES = 15;
const CLOSED = new Set(["RECOVERED", "STOPPED", "EXPIRED"]);

const loadCases = () => api.cases.list({ limit: 100 });

function inSevenDays() {
  const date = new Date();
  date.setDate(date.getDate() + 7);
  return date.toISOString().slice(0, 10);
}

export function SimulationCenter() {
  const { data, error } = useApi(loadCases);
  const operator = canOperate(useSession()?.user.role);
  const [selected, setSelected] = useState<CaseSummary | null>(null);
  const [eventType, setEventType] = useState(EVENT_TYPES[0]);
  const [promiseDate, setPromiseDate] = useState(inSevenDays);
  const [running, setRunning] = useState<string | null>(null);
  const [log, setLog] = useState("");

  // Default to an open case: the backend refuses scenarios on recovered, stopped or expired ones.
  const current = selected ?? data?.items.find((item) => !CLOSED.has(item.status)) ?? data?.items[0] ?? null;

  const run = (label: string, scenario: (item: CaseSummary) => Promise<string>) => async () => {
    if (!current) return;
    setRunning(label);
    setLog(`Running: ${label}…`);
    try {
      setLog(await scenario(current));
    } catch (problem) {
      setLog(`${label} failed: ${errorMessage(problem, "the API rejected the request.")}`);
    } finally {
      setRunning(null);
      api.cases.get(current.id).then(setSelected, () => undefined);
    }
  };

  const scenarios = {
    trigger: run("Trigger event", async (item) => {
      const result = await api.simulation.event(eventType, item.customer_id, item.amount);
      return result.case_id
        ? `Event processed → case ${result.case_id} (${humanize(result.status ?? "")}).`
        : "Event processed; it resolved an existing case or needed no action.";
    }),
    pay: run("Customer pays", async (item) => {
      const result = await api.simulation.payment(item.id);
      return `Payment captured: ${formatInr(result.recovered_amount)} recovered. Case ${humanize(result.status)}.`;
    }),
    timeout: run("Provider timeout", async (item) => {
      const result = await api.simulation.timeout(item.id);
      return (
        `Razorpay timed out ${result.failed_attempts}× (recorded as a failed action, nothing sent); ` +
        `the retry completed ${humanize(result.action_type)}: ${humanize(result.status)}, ` +
        `${result.messages_sent} message sent.`
      );
    }),
    invalidAi: run("Invalid advisor output", async (item) => {
      const result = await api.simulation.invalidAi(item.id);
      return `Invalid advisor output routed to human review: ${humanize(result.status)}.`;
    }),
    optOut: run("Customer opt-out", async (item) => {
      const result = await api.simulation.optOut(item.customer_id);
      return `Customer opted out; ${result.cases_stopped} active case(s) stopped.`;
    }),
    lowConfidence: run("Low advisor confidence", async (item) => {
      const result = await api.simulation.lowConfidence(item.id);
      return `Low-confidence decision escalated: ${humanize(result.status)}.`;
    }),
    maxAttempts: run("Maximum attempts", async (item) => {
      const result = await api.simulation.maxAttempts(item.id);
      return `Contact limit enforced: ${humanize(result.status)}.`;
    }),
    promise: run("Promise to pay", async (item) => {
      const result = await api.simulation.promise(item.id, item.amount, `${promiseDate}T00:00:00Z`);
      return `Promise to pay recorded (${result.id}) for ${promiseDate}.`;
    }),
  };

  const button = (label: string, action: () => void, disabled = false) => (
    <button
      type="button"
      className="secondary-button"
      onClick={action}
      disabled={!operator || running !== null || disabled}
    >
      {running === label ? "Processing…" : label}
    </button>
  );

  return (
    <AppShell title="Simulator">
      <PageHeading
        eyebrow="SIMULATION CENTER"
        title="RecoverX Simulation Center"
        subtitle="Drive real backend state transitions through the simulated Razorpay provider."
        actions={
          <Link className="secondary-button" href={current ? `/cases/${current.id}` : "/cases"}>
            Open case
          </Link>
        }
      />
      {!operator && (
        <p className="muted action-note">Read only: running scenarios changes cases and needs an operator role.</p>
      )}
      {error && <ErrorState message={error} />}
      {!data && !error && <LoadingState label="Loading cases…" />}
      {data && !current && <EmptyState title="No cases available" body="Seed demo data first." />}
      {data && current && (
        <div className="detail-grid">
          <section className="panel simulator-list">
            <div className="panel-header">
              <h2>Select a recovery case</h2>
              <span className="muted">Top {VISIBLE_CASES} by expected value</span>
            </div>
            {data.items.slice(0, VISIBLE_CASES).map((item) => (
              <button
                type="button"
                className={`sim-case ${current.id === item.id ? "selected" : ""}`}
                key={item.id}
                onClick={() => setSelected(item)}
              >
                <span>
                  <b>{item.customer}</b>
                  <small>
                    {item.id} · {humanize(item.status)}
                  </small>
                </span>
                <strong>{formatInr(item.amount)}</strong>
              </button>
            ))}
          </section>

          <section className="panel simulator-panel">
            <p className="eyebrow">FAILURE LAB</p>
            <h2>{current.customer}</h2>
            <p className="subheading">
              {formatInr(current.amount)} · {current.id}
            </p>
            <div className="sim-status">
              <span>Status</span>
              <StatusBadge status={current.status} />
            </div>
            {current.recovered_amount > 0 && (
              <div className="sim-status">
                <span>Recovered</span>
                <b>{formatInr(current.recovered_amount)}</b>
              </div>
            )}

            <section className="sim-section">
              <h3>Revenue event</h3>
              <label>
                Event type
                <select value={eventType} onChange={(event) => setEventType(event.target.value)}>
                  {EVENT_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {humanize(type)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="primary-button"
                onClick={scenarios.trigger}
                disabled={!operator || running !== null}
              >
                {running === "Trigger event" ? "Processing…" : "Trigger event"}
              </button>
            </section>

            <section className="sim-section">
              <h3>Customer payment</h3>
              <p className="muted">Signed payment webhook → case resolved → analytics updated.</p>
              {button("Customer pays", scenarios.pay, current.status === "RECOVERED")}
            </section>

            <section className="sim-section">
              <h3>System failures</h3>
              <p className="muted">Failures are recorded safely; nothing is executed twice.</p>
              {button("Provider timeout", scenarios.timeout)}
              {button("Invalid advisor output", scenarios.invalidAi)}
            </section>

            <section className="sim-section">
              <h3>Customer behavior and guardrails</h3>
              <p className="muted">Opt-outs and contact limits stop automation; low confidence escalates.</p>
              {button("Customer opt-out", scenarios.optOut)}
              {button("Low advisor confidence", scenarios.lowConfidence)}
              {button("Maximum attempts", scenarios.maxAttempts)}
              <label>
                Promise date
                <input type="date" value={promiseDate} onChange={(event) => setPromiseDate(event.target.value)} />
              </label>
              {button("Promise to pay", scenarios.promise)}
            </section>

            {log && (
              <div className="inline-notice" role="log" aria-live="polite">
                {log}
              </div>
            )}
          </section>
        </div>
      )}
    </AppShell>
  );
}
