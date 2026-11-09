"use client";

import Link from "next/link";
import { AppShell } from "../../components/app-shell";
import { EmptyState, ErrorState, LoadingState, PageHeading, Panel } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { formatDateTime, humanize } from "../../lib/format";
import { api } from "../../services/api/recoverx";

const loadAudit = () => api.audit(200);

export function AuditTrail() {
  const { data, error } = useApi(loadAudit);
  return (
    <AppShell title="Audit trail">
      <PageHeading
        eyebrow="CONSEQUENTIAL ACTIVITY"
        title="Audit trail"
        subtitle="Append-only history of decisions, actions and payments."
      />
      {error && <ErrorState message={error} />}
      <Panel title="Recent events" aside={data ? `${data.total} events` : undefined}>
        {!data && !error && <LoadingState label="Loading audit history…" />}
        {data?.items.length === 0 && <EmptyState title="No audit events recorded" />}
        <div className="activity-list">
          {data?.items.map((event) => (
            <div className="activity-item" key={event.id}>
              <span className="activity-icon blue">•</span>
              <span className="activity-copy">
                <b>{humanize(event.event_name)}</b>
                <small>
                  {event.case_id ? <Link href={`/cases/${event.case_id}`}>{event.case_id}</Link> : "System event"} ·{" "}
                  {event.actor}
                </small>
              </span>
              <span className="activity-time">{formatDateTime(event.created_at)}</span>
            </div>
          ))}
        </div>
      </Panel>
    </AppShell>
  );
}
