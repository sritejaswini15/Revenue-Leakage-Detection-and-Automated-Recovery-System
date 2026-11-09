"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../components/app-shell";
import {
  DetailMetric,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeading,
  Pager,
  Panel,
  StatusBadge,
} from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { formatDateTime, formatInr, humanize } from "../../lib/format";
import { api } from "../../services/api/recoverx";

const PAGE_SIZE = 25;
const SEARCH_DELAY_MS = 300;
const STATUSES = ["CAPTURED", "FAILED", "AUTHORIZED", "CREATED"];

export function TransactionList() {
  const params = useSearchParams();
  const [search, setSearch] = useState(params.get("search") ?? "");
  const [query, setQuery] = useState(params.get("search") ?? "");
  const [caseId, setCaseId] = useState(params.get("case_id") ?? "");
  const [status, setStatus] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(search.trim());
      setOffset(0);
    }, SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [search]);

  const load = useCallback(
    () =>
      api.transactions.list({
        search: query,
        status,
        date_from: dateFrom,
        date_to: dateTo,
        case_id: caseId,
        limit: PAGE_SIZE,
        offset,
      }),
    [query, status, dateFrom, dateTo, caseId, offset],
  );
  const { data, error } = useApi(load);
  const filtered = (setter: (value: string) => void) => (value: string) => {
    setter(value);
    setOffset(0);
  };

  return (
    <AppShell title="Transactions">
      <PageHeading
        eyebrow="PAYMENTS"
        title="Transactions"
        subtitle="Every payment RecoverX knows about, newest first: Razorpay events, captured retries and demo history."
      />
      {error && <ErrorState message={error} />}
      <section className="panel route-panel">
        <div className="filter-bar">
          <input
            aria-label="Search transactions"
            placeholder="Payment id, customer, email or case id"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <select
            aria-label="Payment status"
            value={status}
            onChange={(event) => filtered(setStatus)(event.target.value)}
          >
            <option value="">All statuses</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {humanize(value)}
              </option>
            ))}
          </select>
          <label className="inline-field">
            <span>From</span>
            <input
              type="date"
              value={dateFrom}
              max={dateTo || undefined}
              onChange={(e) => filtered(setDateFrom)(e.target.value)}
            />
          </label>
          <label className="inline-field">
            <span>To</span>
            <input
              type="date"
              value={dateTo}
              min={dateFrom || undefined}
              onChange={(e) => filtered(setDateTo)(e.target.value)}
            />
          </label>
          {caseId && (
            <button type="button" className="text-button" onClick={() => filtered(setCaseId)("")}>
              Case {caseId} ✕
            </button>
          )}
        </div>
        <p className="muted small-print">Dates are whole UTC days, both ends included.</p>
        {!data && !error && <LoadingState label="Loading transactions…" />}
        {data?.items.length === 0 && <EmptyState title="No transactions match" body="Try other filters." />}
        {data && data.items.length > 0 && (
          <div className="case-table transactions-table">
            <div className="table-row table-head">
              <span>Payment</span>
              <span>Customer</span>
              <span>Amount</span>
              <span>Status</span>
              <span>Case</span>
            </div>
            {data.items.map((item) => (
              <button
                type="button"
                className={`table-row case-row ${selected === item.id ? "selected" : ""}`}
                key={item.id}
                onClick={() => setSelected(selected === item.id ? null : item.id)}
                aria-expanded={selected === item.id}
              >
                <span className="customer-cell">
                  <span>
                    <b className="mono">{item.provider_payment_id}</b>
                    <small>
                      {formatDateTime(item.created_at)}
                      {item.origin === "seed" ? " · demo history" : ""}
                    </small>
                  </span>
                </span>
                <span>{item.customer_name ?? "—"}</span>
                <span className="amount-cell">{formatInr(item.amount)}</span>
                <span>
                  <StatusBadge status={item.status} />
                </span>
                <span>{item.recovery_case_id ?? "—"}</span>
              </button>
            ))}
          </div>
        )}
        {data && <Pager total={data.total} offset={offset} limit={PAGE_SIZE} onChange={setOffset} noun="payments" />}
      </section>
      {selected && <TransactionPanel paymentId={selected} />}
    </AppShell>
  );
}

function TransactionPanel({ paymentId }: Readonly<{ paymentId: string }>) {
  const load = useCallback(() => api.transactions.get(paymentId), [paymentId]);
  const { data, error } = useApi(load);
  if (error) return <ErrorState message={error} />;
  if (!data) return <LoadingState label="Loading payment…" />;
  return (
    <Panel title={`Payment ${data.provider_payment_id}`} aside={<StatusBadge status={data.status} />}>
      <div className="context-grid">
        <DetailMetric label="Amount" value={formatInr(data.amount)} />
        <DetailMetric label="Method" value={data.method ? formatMethod(data.method) : "—"} />
        <DetailMetric label="Recorded" value={formatDateTime(data.created_at)} />
        <DetailMetric label="Last update" value={formatDateTime(data.updated_at)} />
        <DetailMetric
          label="Customer"
          value={
            <Link href={`/customers/${encodeURIComponent(data.customer_id)}`}>
              {data.customer_name ?? data.customer_id}
            </Link>
          }
        />
        <DetailMetric
          label="Recovery case"
          value={
            data.case ? (
              <Link href={`/cases/${encodeURIComponent(data.case.id)}`}>
                {data.case.id} · {humanize(data.case.status)}
              </Link>
            ) : (
              "Not linked to a case"
            )
          }
        />
        {data.failure_reason && <DetailMetric label="Failure reason" value={humanize(data.failure_reason)} />}
      </div>
      <h3>Attempts</h3>
      {data.attempts.length === 0 ? (
        <p className="muted">No attempts recorded.</p>
      ) : (
        data.attempts.map((attempt) => (
          <div className="queue-row" key={`${attempt.provider_attempt_id}-${attempt.created_at}`}>
            <span>
              <b>{formatDateTime(attempt.created_at)}</b>
              <small className="muted">
                {attempt.provider_attempt_id ?? "—"}
                {attempt.failure_reason ? ` · ${humanize(attempt.failure_reason)}` : ""}
              </small>
            </span>
            <StatusBadge status={attempt.status} />
          </div>
        ))
      )}
    </Panel>
  );
}

/** Razorpay method codes: short ones are acronyms (UPI, EMI), the rest read as words. */
function formatMethod(method: string) {
  return method.length <= 3 ? method.toUpperCase() : humanize(method);
}
