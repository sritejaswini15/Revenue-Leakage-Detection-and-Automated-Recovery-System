"use client";

import Link from "next/link";
import { useCallback } from "react";
import { AppShell } from "../../components/app-shell";
import { DetailMetric, EmptyState, ErrorState, LoadingState, Panel, StatusBadge } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { formatDateTime, formatInr, formatPercent, humanize } from "../../lib/format";
import { api } from "../../services/api/recoverx";

export function CustomerProfile({ customerId }: Readonly<{ customerId: string }>) {
  const load = useCallback(() => api.customers.get(customerId), [customerId]);
  const { data: customer, error } = useApi(load);

  return (
    <AppShell title="Customer">
      <Link className="back-link" href="/customers">
        ← Back to customers
      </Link>
      {error && <ErrorState message={error} />}
      {!customer && !error && <LoadingState label="Loading customer…" />}
      {customer && (
        <>
          <div className="route-heading">
            <div>
              <p className="eyebrow">CUSTOMER {customer.razorpay_customer_id}</p>
              <h1>{customer.name}</h1>
              <p className="subheading">{[customer.email, customer.phone].filter(Boolean).join(" · ")}</p>
            </div>
            {customer.opted_out ? (
              <span className="status status-stopped">Opted out</span>
            ) : (
              <span className="status status-recovered">Contactable</span>
            )}
          </div>

          <div className="detail-grid">
            <Panel title="Payment reliability" aside={`${customer.stats.reliability_pct}% combined`}>
              <div className="context-grid">
                <DetailMetric label="Successful payments" value={customer.stats.successful_payments} />
                <DetailMetric label="Failed payments" value={customer.stats.failed_payments} />
                <DetailMetric label="Lifetime value" value={formatInr(customer.lifetime_value)} />
                <DetailMetric label="Historical recovery" value={formatPercent(customer.historical_recovery_rate, 0)} />
                <DetailMetric label="Past recovery cases" value={customer.previous_recovery_attempts} />
                <DetailMetric
                  label="What usually works"
                  value={
                    customer.best_historical_recovery_action ? humanize(customer.best_historical_recovery_action) : "—"
                  }
                />
              </div>
              <p className="muted small-print">
                Merchant profile: {customer.stats.profile_reliability_pct}% over{" "}
                {customer.stats.profile_successful + customer.stats.profile_failed} payments. Seen by RecoverX:{" "}
                {customer.stats.observed_successful} paid ({formatInr(customer.stats.observed_captured_amount)}),{" "}
                {customer.stats.observed_failed} failed. Each payment counts once, by its latest status; the profile
                weighs at least four payments so one new payment cannot swing the figure.
              </p>
            </Panel>

            <Panel title="Contact preferences">
              <div className="context-grid">
                <DetailMetric label="Channel" value={humanize(customer.preferred_channel)} />
                <DetailMetric label="Language" value={customer.preferred_language} />
                <DetailMetric
                  label="Last payment"
                  value={customer.stats.last_payment_at ? formatDateTime(customer.stats.last_payment_at) : "—"}
                />
                <DetailMetric label="Customer since" value={formatDateTime(customer.created_at)} />
              </div>
            </Panel>

            <Panel title="Recovery cases" aside={`${customer.cases.length} most recent`} className="span-all">
              {customer.cases.length === 0 ? (
                <EmptyState title="No recovery cases" />
              ) : (
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Case</th>
                        <th>Opened</th>
                        <th>Amount</th>
                        <th>Recovered</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {customer.cases.map((item) => (
                        <tr key={item.id}>
                          <td>
                            <Link href={`/cases/${encodeURIComponent(item.id)}`}>{item.id}</Link>
                          </td>
                          <td>{formatDateTime(item.created_at)}</td>
                          <td className="mono">{formatInr(item.amount)}</td>
                          <td className="mono">{formatInr(item.recovered_amount)}</td>
                          <td>
                            <StatusBadge status={item.status} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>

            <Panel title="Payments" aside={`${customer.payments.length} most recent`} className="span-all">
              {customer.payments.length === 0 ? (
                <EmptyState title="No payments recorded" />
              ) : (
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Payment</th>
                        <th>Recorded</th>
                        <th>Amount</th>
                        <th>Status</th>
                        <th>Source</th>
                        <th>Case</th>
                      </tr>
                    </thead>
                    <tbody>
                      {customer.payments.map((payment) => (
                        <tr key={payment.id}>
                          <td className="mono">
                            <Link href={`/transactions?search=${encodeURIComponent(payment.provider_payment_id)}`}>
                              {payment.provider_payment_id}
                            </Link>
                          </td>
                          <td>{formatDateTime(payment.created_at)}</td>
                          <td className="mono">{formatInr(payment.amount)}</td>
                          <td>
                            <StatusBadge status={payment.status} />
                          </td>
                          <td className="muted">{payment.origin === "seed" ? "Demo history" : "Razorpay event"}</td>
                          <td>
                            {payment.recovery_case_id ? (
                              <Link href={`/cases/${encodeURIComponent(payment.recovery_case_id)}`}>
                                {payment.recovery_case_id}
                              </Link>
                            ) : (
                              "—"
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
          </div>
        </>
      )}
    </AppShell>
  );
}
