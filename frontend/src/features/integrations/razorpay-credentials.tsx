"use client";

import { type FormEvent, useState } from "react";
import { DetailMetric, ErrorState, LoadingState, Notice, Panel, StatusBadge } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { formatDateTime } from "../../lib/format";
import { errorMessage } from "../../services/api/client";
import { api } from "../../services/api/recoverx";
import type { RazorpayIntegration } from "../../types/api";

const loadRazorpay = () => api.integrations.razorpay();
const SOURCE_LABEL: Record<RazorpayIntegration["source"], string> = {
  merchant: "This merchant's keys",
  "legacy-environment": "Deployment environment keys",
  simulated: "Simulated provider",
};

/** Admin-only: store, rotate, check and switch off this merchant's Razorpay keys. Secrets are write-only. */
export function RazorpayCredentials({ onChanged }: Readonly<{ onChanged: () => void }>) {
  const { data, error, setData } = useApi(loadRazorpay);
  const [form, setForm] = useState({ key_id: "", key_secret: "", account_id: "", webhook_secret: "", verify: true });
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const run = async (action: () => Promise<RazorpayIntegration>, success: (view: RazorpayIntegration) => string) => {
    setBusy(true);
    setNotice("");
    try {
      const view = await action();
      setData(view);
      setNotice(success(view));
      onChanged();
    } catch (problem) {
      setNotice(errorMessage(problem, "The change could not be saved."));
    } finally {
      setBusy(false);
    }
  };

  const save = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void run(
      () =>
        api.integrations.saveRazorpay({
          ...form,
          webhook_secret: form.webhook_secret || undefined,
        }),
      (view) => {
        setForm({ key_id: "", key_secret: "", account_id: view.account_id ?? "", webhook_secret: "", verify: true });
        if (view.last_error) return `Saved, but Razorpay rejected the keys: ${view.last_error}`;
        return view.validated_at ? "Saved and verified with Razorpay." : "Saved without verification.";
      },
    );
  };

  if (error) return <ErrorState message={error} />;
  if (!data) return <LoadingState label="Loading Razorpay settings…" />;

  const status = data.source === "simulated" ? "SIMULATED" : data.enabled ? "ENABLED" : "DISABLED";
  return (
    <Panel title="Razorpay credentials" aside={<StatusBadge status={status} />}>
      <div className="context-grid">
        <DetailMetric label="In use" value={SOURCE_LABEL[data.source]} />
        <DetailMetric label="Mode" value={data.mode ? data.mode.toUpperCase() : "—"} />
        <DetailMetric label="Key id" value={data.key_id_masked ?? "—"} />
        <DetailMetric label="Razorpay account" value={data.account_id ?? "—"} />
        <DetailMetric label="Webhook secret" value={data.has_webhook_secret ? "Set" : "Missing"} />
        <DetailMetric label="Last verified" value={data.validated_at ? formatDateTime(data.validated_at) : "Never"} />
      </div>
      {data.last_error && <ErrorState message={`Last check failed: ${data.last_error}`} />}
      <p className="muted small-print">
        Webhook URL for the Razorpay dashboard: <code>https://&lt;your API host&gt;{data.webhook_path}</code>. Secrets
        are encrypted at rest and never shown again.{" "}
        {data.live_payments_allowed ? "Live keys are allowed on this deployment." : "Only rzp_test_ keys are accepted."}
      </p>
      {data.source === "merchant" && (
        <div className="action-row">
          <button
            type="button"
            className="secondary-button"
            disabled={busy}
            onClick={() =>
              void run(api.integrations.validateRazorpay, (view) =>
                view.last_error ? `Razorpay rejected the keys: ${view.last_error}` : "Keys verified with Razorpay.",
              )
            }
          >
            Verify keys
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={busy}
            onClick={() =>
              void run(
                () => api.integrations.setRazorpayEnabled(!data.enabled),
                (view) =>
                  view.enabled
                    ? "Razorpay re-enabled."
                    : "Disabled: actions use the simulator and Razorpay events are acknowledged without acting.",
              )
            }
          >
            {data.enabled ? "Disable" : "Enable"}
          </button>
        </div>
      )}
      <form className="policy-form" onSubmit={save}>
        <label>
          Key id
          <input
            value={form.key_id}
            onChange={(e) => setForm({ ...form, key_id: e.target.value.trim() })}
            placeholder="rzp_test_…"
            autoComplete="off"
            required
          />
        </label>
        <label>
          Key secret
          <input
            type="password"
            value={form.key_secret}
            onChange={(e) => setForm({ ...form, key_secret: e.target.value })}
            autoComplete="new-password"
            required
          />
        </label>
        <label>
          Razorpay account id
          <input
            value={form.account_id}
            onChange={(e) => setForm({ ...form, account_id: e.target.value.trim() })}
            placeholder={data.account_id ?? "acc_…"}
            autoComplete="off"
            required
          />
          <small className="muted">Webhooks are routed to this merchant by the account id Razorpay sends.</small>
        </label>
        <label>
          Webhook secret
          <input
            type="password"
            value={form.webhook_secret}
            onChange={(e) => setForm({ ...form, webhook_secret: e.target.value })}
            autoComplete="new-password"
            placeholder={data.has_webhook_secret ? "Leave blank to keep the current secret" : ""}
          />
        </label>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={form.verify}
            onChange={(e) => setForm({ ...form, verify: e.target.checked })}
          />
          Verify with Razorpay before saving
        </label>
        <button className="primary-button" type="submit" disabled={busy}>
          {data.source === "merchant" ? "Rotate keys" : "Save keys"}
        </button>
        <Notice message={notice} />
      </form>
    </Panel>
  );
}
