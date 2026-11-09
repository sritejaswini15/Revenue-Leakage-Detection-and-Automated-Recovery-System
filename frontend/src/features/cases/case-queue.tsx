"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../components/app-shell";
import { EmptyState, ErrorState, LoadingState, PageHeading, RiskBadge, StatusBadge } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { formatInr, formatPercent, humanize, initials } from "../../lib/format";
import { api } from "../../services/api/recoverx";

const STATUSES = [
  "NEW",
  "ACTIONABLE",
  "WAITING_FOR_PAYMENT",
  "PROMISE_TO_PAY",
  "ESCALATED",
  "RECOVERED",
  "STOPPED",
  "EXPIRED",
];
const PAGE_SIZE = 200;
const SEARCH_DELAY_MS = 300;

export function CaseQueue() {
  const [status, setStatus] = useState("ALL");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [search]);

  const load = useCallback(
    () => api.cases.list({ status: status === "ALL" ? undefined : status, query, limit: PAGE_SIZE }),
    [status, query],
  );
  const { data, error } = useApi(load);

  return (
    <AppShell title="Cases">
      <PageHeading
        eyebrow="RECOVERY OPERATIONS"
        title="Case queue"
        subtitle="Prioritized by expected recovery value."
        actions={
          <Link className="secondary-button" href="/escalations">
            Review escalations
          </Link>
        }
      />
      {error && <ErrorState message={error} />}
      <section className="panel route-panel">
        <div className="filter-bar">
          <input
            aria-label="Search cases"
            placeholder="Search by customer or case id"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <select aria-label="Filter by status" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="ALL">All statuses</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {humanize(value)}
              </option>
            ))}
          </select>
          {data && (
            <span className="result-count">
              {data.total > data.items.length
                ? `Top ${data.items.length} of ${data.total.toLocaleString("en-IN")}`
                : `${data.total} cases`}
            </span>
          )}
        </div>
        {!data && !error && <LoadingState label="Loading recovery cases…" />}
        {data && (
          <div className="case-table wide-table">
            <div className="table-row table-head">
              <span>Customer</span>
              <span>Revenue type</span>
              <span>Amount</span>
              <span>Risk</span>
              <span>Expected recovery</span>
              <span>Status</span>
            </div>
            {data.items.map((item) => (
              <Link className="table-row case-row" href={`/cases/${item.id}`} key={item.id}>
                <span className="customer-cell">
                  <span className="customer-avatar">{initials(item.customer)}</span>
                  <span>
                    <b>{item.customer}</b>
                    <small>{item.id}</small>
                  </span>
                </span>
                <span>{humanize(item.event_type)}</span>
                <span className="amount-cell">{formatInr(item.amount)}</span>
                <span>
                  <RiskBadge score={item.risk_score} />
                </span>
                <span>
                  {formatInr(item.expected_recovery_value)}
                  <small className="muted">{formatPercent(item.recovery_probability, 0)} probability</small>
                </span>
                <span>
                  <StatusBadge status={item.status} />
                </span>
              </Link>
            ))}
          </div>
        )}
        {data?.items.length === 0 && <EmptyState title="No recovery cases found" body="Try changing your filters." />}
      </section>
    </AppShell>
  );
}
