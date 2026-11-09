"use client";

import { AppShell } from "../../components/app-shell";
import { ErrorState, LoadingState, MetricCard, PageHeading } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { useSession } from "../../hooks/use-session";
import { api } from "../../services/api/recoverx";
import { RazorpayCredentials } from "./razorpay-credentials";

const STATUS_CLASS = {
  CONNECTED: "connected",
  CONFIGURED: "configured",
  SIMULATED: "simulated",
  NOT_CONFIGURED: "not-configured",
} as const;

const loadIntegrations = () => api.integrations.status();

export function IntegrationStatus() {
  const session = useSession();
  const { data, error, reload } = useApi(loadIntegrations);
  return (
    <AppShell title="Integrations">
      <PageHeading
        eyebrow="PROVIDER CONNECTIONS"
        title="Integrations"
        subtitle="What is live and what is simulated, as reported by the API."
      />
      {error && <ErrorState message={error} />}
      {!data && !error && <LoadingState label="Loading integration status…" />}
      {data && (
        <>
          <div className="metric-grid metric-grid-5">
            <MetricCard label="Total integrations" value={String(data.counts.total)} />
            <MetricCard label="Connected" value={String(data.counts.connected)} tone="mint" />
            <MetricCard label="Configured" value={String(data.counts.configured)} tone="mint" />
            <MetricCard label="Simulated" value={String(data.counts.simulated)} tone="amber" />
            <MetricCard label="Not configured" value={String(data.counts.not_configured)} tone="coral" />
          </div>
          <section className="integration-grid">
            {data.integrations.map((item) => (
              <div className="panel integration-card" key={item.name}>
                <span className="integration-dot" data-status={item.status} />
                <div>
                  <b>{item.name}</b>
                  <small>
                    {item.category} · {item.detail}
                  </small>
                </div>
                <strong className={STATUS_CLASS[item.status]}>{item.label}</strong>
              </div>
            ))}
          </section>
        </>
      )}
      {session?.user.role === "ADMIN" && <RazorpayCredentials onChanged={reload} />}
    </AppShell>
  );
}
