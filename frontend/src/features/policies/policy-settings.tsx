"use client";

import { type FormEvent, useState } from "react";
import { AppShell } from "../../components/app-shell";
import { ErrorState, LoadingState, Notice, PageHeading } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { errorMessage } from "../../services/api/client";
import { api } from "../../services/api/recoverx";
import { useSession } from "../../hooks/use-session";
import { humanize } from "../../lib/format";
import { canReview } from "../../lib/roles";
import type { Policy } from "../../types/api";

type ListField = "preferred_channels" | "supported_languages" | "allowed_actions";
type NumericField = Exclude<keyof Policy, ListField>;

const LIST_FIELDS: Array<{ key: ListField; label: string; hint: string; options: string[] }> = [
  {
    key: "preferred_channels",
    label: "Contact channels",
    hint: "A customer is messaged on their own channel when it is on here, otherwise on the first one listed.",
    options: ["whatsapp", "email"],
  },
  {
    key: "supported_languages",
    label: "Message languages",
    hint: "Customers whose language is off get the first language listed.",
    options: ["English", "Hinglish"],
  },
  {
    key: "allowed_actions",
    label: "Allowed recovery actions",
    hint: "The Tool Gateway refuses anything else, whoever asks: the advisor, an operator or an MCP agent.",
    options: ["PAYMENT_LINK", "PAYMENT_RETRY", "REMINDER", "PERSONALIZED_OUTREACH", "FOLLOW_UP", "CHECKOUT_RECOVERY"],
  },
];

const split = (value: string) =>
  value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

/** Turn one option on or off, keeping the merchant's order (it decides the fallback). */
const toggle = (value: string, option: string, on: boolean) => {
  const current = split(value).filter((item) => item.toLowerCase() !== option.toLowerCase());
  return (on ? [...current, option] : current).join(",");
};

const NUMERIC_FIELDS: Array<{ key: NumericField; label: string; hint: string; step?: string }> = [
  { key: "max_auto_action_amount", label: "Maximum auto-action amount (₹)", hint: "Larger cases need a person." },
  {
    key: "human_approval_above_amount",
    label: "Human approval above (₹)",
    hint: "The lower of the two limits applies.",
  },
  { key: "max_contact_attempts", label: "Maximum contact attempts", hint: "Contact stops after this many." },
  { key: "escalate_after_days", label: "Escalate after (days overdue)", hint: "Older cases go to review." },
  {
    key: "case_expiry_days",
    label: "Close unpaid cases after (days)",
    hint: "Open cases expire and their link is cancelled; a later payment still counts.",
  },
  {
    key: "min_ai_confidence",
    label: "Minimum advisor confidence",
    hint: "0–1; below this the case goes to a person.",
    step: "0.01",
  },
];

const loadPolicy = () => api.policy.get();

export function PolicySettings() {
  const { data: policy, error, setData } = useApi(loadPolicy);
  const editable = canReview(useSession()?.user.role);
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!policy) return;
    setSaving(true);
    try {
      setData(await api.policy.update(policy));
      setNotice("Policy saved. New decisions use these limits immediately.");
    } catch (problem) {
      setNotice(errorMessage(problem, "Policy update failed."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <AppShell title="Policies">
      <PageHeading
        eyebrow="GUARDRAILS"
        title="Policy management"
        subtitle="Deterministic limits every recovery action must pass. Admins and finance managers can edit."
      />
      {error && <ErrorState message={error} />}
      {!policy && !error && <LoadingState label="Loading policy settings…" />}
      {policy && (
        <form className="panel policy-form" onSubmit={save}>
          {!editable && (
            <p className="muted action-note">Read only: administrators and finance managers can change the policy.</p>
          )}
          <fieldset className="policy-fields" disabled={!editable}>
            {NUMERIC_FIELDS.map(({ key, label, hint, step = "1" }) => (
              <label key={key}>
                {label}
                <input
                  type="number"
                  min={key === "case_expiry_days" ? 1 : 0}
                  max={key === "min_ai_confidence" ? 1 : undefined}
                  step={step}
                  value={policy[key]}
                  onChange={(event) => setData({ ...policy, [key]: Number(event.target.value) })}
                />
                <small className="muted">{hint}</small>
              </label>
            ))}
            {LIST_FIELDS.map(({ key, label, hint, options }) => {
              const chosen = split(policy[key]).map((item) => item.toLowerCase());
              return (
                <fieldset key={key} className="policy-options">
                  <legend>{label}</legend>
                  {options.map((option) => (
                    <label key={option} className="check-option">
                      <input
                        type="checkbox"
                        checked={chosen.includes(option.toLowerCase())}
                        onChange={(event) =>
                          setData({ ...policy, [key]: toggle(policy[key], option, event.target.checked) })
                        }
                      />
                      {humanize(option)}
                    </label>
                  ))}
                  <small className="muted">
                    {hint}
                    {key !== "allowed_actions" &&
                      chosen.length > 1 &&
                      ` Order: ${split(policy[key]).map(humanize).join(" → ")}.`}
                  </small>
                </fieldset>
              );
            })}
          </fieldset>
          {editable && (
            <button className="primary-button" type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save policy"}
            </button>
          )}
          <Notice message={notice} />
        </form>
      )}
    </AppShell>
  );
}
