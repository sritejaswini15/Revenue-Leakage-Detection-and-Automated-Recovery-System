"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../components/app-shell";
import { EmptyState, ErrorState, LoadingState, PageHeading, Pager } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { useSession } from "../../hooks/use-session";
import { formatInr, humanize, initials } from "../../lib/format";
import { type CustomerQuery, api } from "../../services/api/recoverx";
import { CustomerImport } from "./customer-import";

const PAGE_SIZE = 25;
const SEARCH_DELAY_MS = 300;
const MANAGERS = new Set(["ADMIN", "FINANCE_MANAGER"]);

type Contact = NonNullable<CustomerQuery["contact"]>;
type Sort = NonNullable<CustomerQuery["sort"]>;

export function CustomerDirectory() {
  const session = useSession();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [contact, setContact] = useState<Contact>("all");
  const [sort, setSort] = useState<Sort>("value");
  const [offset, setOffset] = useState(0);
  const [showImport, setShowImport] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(search.trim());
      setOffset(0);
    }, SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [search]);

  const load = useCallback(
    () => api.customers.list({ search: query, contact, sort, limit: PAGE_SIZE, offset }),
    [query, contact, sort, offset],
  );
  const { data, error, reload } = useApi(load);
  const canImport = MANAGERS.has(session?.user.role ?? "");

  return (
    <AppShell title="Customers">
      <PageHeading
        eyebrow="CUSTOMERS"
        title="Customers"
        subtitle="Payment behavior from the merchant's profile and every payment RecoverX has seen."
        actions={
          canImport && (
            <button type="button" className="secondary-button" onClick={() => setShowImport((value) => !value)}>
              {showImport ? "Close import" : "Import or sync"}
            </button>
          )
        }
      />
      {showImport && canImport && <CustomerImport onImported={reload} />}
      {error && <ErrorState message={error} />}
      <section className="panel route-panel">
        <div className="filter-bar">
          <input
            aria-label="Search customers"
            placeholder="Name, email, phone or Razorpay id"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <select
            aria-label="Contact status"
            value={contact}
            onChange={(event) => {
              setContact(event.target.value as Contact);
              setOffset(0);
            }}
          >
            <option value="all">Everyone</option>
            <option value="contactable">Contactable</option>
            <option value="opted_out">Opted out</option>
          </select>
          <select
            aria-label="Sort customers"
            value={sort}
            onChange={(event) => {
              setSort(event.target.value as Sort);
              setOffset(0);
            }}
          >
            <option value="value">Highest lifetime value</option>
            <option value="name">Name A–Z</option>
            <option value="recent">Newest first</option>
          </select>
        </div>
        {!data && !error && <LoadingState label="Loading customers…" />}
        {data?.items.length === 0 && <EmptyState title="No customers match" body="Try another search or filter." />}
        {data && data.items.length > 0 && (
          <div className="case-table customers-table">
            <div className="table-row table-head">
              <span>Customer</span>
              <span>Lifetime value</span>
              <span>Payments</span>
              <span>Reliability</span>
              <span>Preferred contact</span>
            </div>
            {data.items.map((customer) => (
              <Link
                className="table-row case-row"
                href={`/customers/${encodeURIComponent(customer.id)}`}
                key={customer.id}
              >
                <span className="customer-cell">
                  <span className="customer-avatar">{initials(customer.name)}</span>
                  <span>
                    <b>{customer.name}</b>
                    <small>{customer.email || customer.phone}</small>
                  </span>
                </span>
                <span>{formatInr(customer.lifetime_value)}</span>
                <span>
                  {customer.successful_payments} paid · {customer.failed_payments} failed
                  {customer.stats.observed_successful + customer.stats.observed_failed > 0 && (
                    <small className="muted">
                      {customer.stats.observed_successful + customer.stats.observed_failed} seen by RecoverX
                    </small>
                  )}
                </span>
                <span>{customer.payment_reliability_pct}%</span>
                <span>
                  {customer.opted_out ? (
                    <span className="status status-stopped">Opted out</span>
                  ) : (
                    `${humanize(customer.preferred_channel)} · ${customer.preferred_language}`
                  )}
                </span>
              </Link>
            ))}
          </div>
        )}
        {data && <Pager total={data.total} offset={offset} limit={PAGE_SIZE} onChange={setOffset} noun="customers" />}
      </section>
    </AppShell>
  );
}
