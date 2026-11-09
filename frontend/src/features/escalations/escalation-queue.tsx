"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { AppShell } from "../../components/app-shell";
import { EmptyState, ErrorState, LoadingState, Notice, PageHeading, Pager, StatusBadge } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { useSession } from "../../hooks/use-session";
import { formatInr, formatReasons, humanize } from "../../lib/format";
import { canReview } from "../../lib/roles";
import { errorMessage } from "../../services/api/client";
import { api } from "../../services/api/recoverx";
import type { Resolution } from "../../types/api";

const PAGE_SIZE = 25;

// Review queues in the order the backend prioritises them (backend/app/domain/policy.py).
const QUEUES: Array<{ key: string; label: string }> = [
  { key: "HIGH_VALUE", label: "High value" },
  { key: "LOW_CONFIDENCE", label: "Low confidence" },
  { key: "PROMISE_MISSED", label: "Promise missed" },
  { key: "MAX_ATTEMPTS", label: "Maximum attempts" },
  { key: "PROVIDER_FAILURE", label: "Provider failure" },
  { key: "CUSTOMER_REQUEST", label: "Requested review" },
  { key: "SPECIAL_CASE", label: "Special case" },
];
const queueLabel = (key: string) => QUEUES.find((queue) => queue.key === key)?.label ?? humanize(key);

export function EscalationQueue() {
  const reviewer = canReview(useSession()?.user.role);
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [queue, setQueue] = useState<string>("ALL");
  const [offset, setOffset] = useState(0);
  const load = useCallback(
    () => api.cases.escalated({ queue: queue === "ALL" ? undefined : queue, limit: PAGE_SIZE, offset }),
    [queue, offset],
  );
  const { data, error, reload } = useApi(load);
  const choose = (next: string) => {
    setQueue(next);
    setOffset(0);
  };

  const resolve = async (caseId: string, resolution: Extract<Resolution, "APPROVE" | "REJECT">) => {
    setPending(caseId);
    try {
      const result = await api.cases.resolve(caseId, resolution);
      setNotice(`Case ${caseId} ${resolution === "APPROVE" ? "approved" : "rejected"}: ${humanize(result.status)}.`);
    } catch (problem) {
      setNotice(errorMessage(problem, "Unable to resolve this escalation."));
    } finally {
      setPending(null);
      reload();
    }
  };

  const items = data?.items ?? [];
  const waiting = data ? Object.values(data.queues).reduce((sum, count) => sum + count, 0) : 0;

  return (
    <AppShell title="Escalations">
      <PageHeading
        eyebrow="HUMAN REVIEW QUEUE"
        title="Escalations"
        subtitle="Cases the policy engine routed to a person, most recoverable value first. Open a case to edit the action, contact the customer or close it."
      />
      <Notice message={notice} />
      {error && <ErrorState message={error} />}
      {data && (
        <div className="queue-filters" role="tablist" aria-label="Review queues">
          <button
            type="button"
            role="tab"
            aria-selected={queue === "ALL"}
            className={queue === "ALL" ? "chip chip-active" : "chip"}
            onClick={() => choose("ALL")}
          >
            All <span>{waiting}</span>
          </button>
          {QUEUES.filter(({ key }) => data.queues[key]).map(({ key, label }) => (
            <button
              type="button"
              role="tab"
              key={key}
              aria-selected={queue === key}
              className={queue === key ? "chip chip-active" : "chip"}
              onClick={() => choose(key)}
            >
              {label} <span>{data.queues[key]}</span>
            </button>
          ))}
        </div>
      )}
      <section className="panel route-panel">
        {!data && !error && <LoadingState label="Loading escalations…" />}
        {data && items.length === 0 && (
          <EmptyState title="No escalations need attention" body="You're all caught up." />
        )}
        {items.map((item) => (
          <div className="queue-row" key={item.id}>
            <Link href={`/cases/${item.id}`}>
              <span>
                <b>{item.customer}</b>
                <small>
                  {item.id} · {queueLabel(item.queue)}: {formatReasons(item.escalation_reason)}
                </small>
                <small>
                  Recommends {humanize(item.recommended_action)} · {Math.round(item.ai_confidence * 100)}% advisor
                  confidence · expected {formatInr(item.expected_recovery_value)}
                </small>
              </span>
            </Link>
            <strong>{formatInr(item.amount)}</strong>
            <StatusBadge status={item.status} />
            <div className="queue-actions">
              <Link className="secondary-button" href={`/cases/${item.id}`}>
                Review
              </Link>
              {reviewer && (
                <>
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={pending === item.id}
                    onClick={() => resolve(item.id, "APPROVE")}
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={pending === item.id}
                    onClick={() => resolve(item.id, "REJECT")}
                  >
                    Reject
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
      </section>
      {data && <Pager total={data.total} offset={offset} limit={PAGE_SIZE} onChange={setOffset} noun="escalations" />}
    </AppShell>
  );
}
