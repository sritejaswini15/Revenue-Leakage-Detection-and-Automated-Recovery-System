"use client";

import { type FormEvent, useState } from "react";
import { AppShell } from "../../components/app-shell";
import { DetailMetric, ErrorState, Notice, PageHeading, Panel } from "../../components/ui";
import { useSession } from "../../hooks/use-session";
import { humanize } from "../../lib/format";
import { errorMessage } from "../../services/api/client";
import { api } from "../../services/api/recoverx";

const MIN_PASSWORD = 12;

export function AccountSettings() {
  const session = useSession();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setNotice("");
    if (next !== confirm) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const result = await api.auth.changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      setNotice(
        `Password changed. ${result.sessions_revoked} other ${result.sessions_revoked === 1 ? "session was" : "sessions were"} signed out.`,
      );
    } catch (problem) {
      setError(errorMessage(problem, "Your password could not be changed."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell title="Account">
      <PageHeading eyebrow="ACCOUNT" title="Your account" />
      {session && (
        <Panel title="Profile">
          <div className="context-grid">
            <DetailMetric label="Name" value={session.user.name} />
            <DetailMetric label="Email" value={session.user.email} />
            <DetailMetric label="Role" value={humanize(session.user.role)} />
          </div>
        </Panel>
      )}
      <Panel title="Change password" aside="Signs out your other sessions">
        <form className="policy-form" onSubmit={submit}>
          <label>
            Current password
            <input
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              required
            />
          </label>
          <label>
            New password
            <input
              type="password"
              autoComplete="new-password"
              minLength={MIN_PASSWORD}
              value={next}
              onChange={(e) => setNext(e.target.value)}
              required
            />
            <small className="muted">At least {MIN_PASSWORD} characters.</small>
          </label>
          <label>
            Confirm new password
            <input
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
            />
          </label>
          {error && <ErrorState message={error} />}
          <button className="primary-button" type="submit" disabled={busy}>
            {busy ? "Saving…" : "Change password"}
          </button>
          <Notice message={notice} />
        </form>
      </Panel>
    </AppShell>
  );
}
