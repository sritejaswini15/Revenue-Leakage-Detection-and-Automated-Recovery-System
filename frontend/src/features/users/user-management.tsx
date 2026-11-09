"use client";

import { type FormEvent, useState } from "react";
import { AppShell } from "../../components/app-shell";
import { EmptyState, ErrorState, LoadingState, Notice, PageHeading, Panel, StatusBadge } from "../../components/ui";
import { useApi } from "../../hooks/use-api";
import { useSession } from "../../hooks/use-session";
import { formatDateTime, humanize } from "../../lib/format";
import { errorMessage } from "../../services/api/client";
import { api } from "../../services/api/recoverx";
import type { ManagedUser, Role } from "../../types/api";

const ROLES: Array<{ value: Role; hint: string }> = [
  { value: "ADMIN", hint: "Everything, including users and Razorpay credentials" },
  { value: "FINANCE_MANAGER", hint: "Policies, imports and approvals" },
  { value: "OPERATOR", hint: "Works cases" },
  { value: "VIEWER", hint: "Read only" },
];
const MIN_PASSWORD = 12;
const loadUsers = () => api.users.list();
const EMPTY_FORM = { name: "", email: "", role: "OPERATOR" as Role, password: "" };

export function UserManagement() {
  const session = useSession();
  const { data, error, reload } = useApi(loadUsers);
  const [form, setForm] = useState(EMPTY_FORM);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const act = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true);
    try {
      await action();
      setNotice(success);
      reload();
    } catch (problem) {
      setNotice(errorMessage(problem, "That change could not be made."));
    } finally {
      setBusy(false);
    }
  };

  const create = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void act(async () => {
      await api.users.create(form);
      setForm(EMPTY_FORM);
    }, `${form.email} can now sign in. Share the temporary password with them directly; RecoverX sends no email.`);
  };

  const resetPassword = (user: ManagedUser) => {
    const password = window.prompt(`Temporary password for ${user.email} (${MIN_PASSWORD}+ characters)`);
    if (password)
      void act(
        () => api.users.resetPassword(user.id, password),
        `${user.email} was signed out; share the new password.`,
      );
  };

  if (session && session.user.role !== "ADMIN") {
    return (
      <AppShell title="Users">
        <EmptyState title="Administrators only" body="Ask an administrator to change user access." />
      </AppShell>
    );
  }

  return (
    <AppShell title="Users">
      <PageHeading
        eyebrow="ACCESS"
        title="Users"
        subtitle="Who can sign in to this merchant, and what they can do. Every change is in the audit trail."
      />
      <Notice message={notice} />
      {error && <ErrorState message={error} />}
      <Panel title="Add a user" aside="No invitation email is sent">
        <form className="policy-form user-form" onSubmit={create}>
          <label>
            Name
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
              maxLength={160}
            />
          </label>
          <label>
            Email
            <input
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              required
            />
          </label>
          <label>
            Role
            <select
              aria-label="New user role"
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value as Role })}
            >
              {ROLES.map((role) => (
                <option key={role.value} value={role.value}>
                  {humanize(role.value)}: {role.hint}
                </option>
              ))}
            </select>
          </label>
          <label>
            Temporary password
            <input
              type="password"
              autoComplete="new-password"
              minLength={MIN_PASSWORD}
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              required
            />
            <small className="muted">At least {MIN_PASSWORD} characters. They can change it under Account.</small>
          </label>
          <button className="primary-button" type="submit" disabled={busy}>
            Add user
          </button>
        </form>
      </Panel>

      <Panel title="People" aside={data ? `${data.length} users` : undefined}>
        {!data && !error && <LoadingState label="Loading users…" />}
        {data && (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Sessions</th>
                  <th>Last sign-in</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {data.map((user) => {
                  const self = user.id === session?.user.id;
                  const locked = self || user.environment_managed;
                  return (
                    <tr key={user.id}>
                      <td>
                        <b>{user.name}</b>
                        <small className="muted block">{user.email}</small>
                        {user.environment_managed && (
                          <small className="muted block">Managed by deployment settings</small>
                        )}
                      </td>
                      <td>
                        <select
                          aria-label={`Role for ${user.email}`}
                          value={user.role}
                          disabled={busy || locked}
                          onChange={(e) =>
                            void act(
                              () => api.users.update(user.id, { role: e.target.value as Role }),
                              `${user.email} is now ${humanize(e.target.value)} and was signed out.`,
                            )
                          }
                        >
                          {ROLES.map((role) => (
                            <option key={role.value} value={role.value}>
                              {humanize(role.value)}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <StatusBadge status={user.is_active ? "ACTIVE" : "DEACTIVATED"} />
                      </td>
                      <td>{user.active_sessions}</td>
                      <td className="muted">{user.last_sign_in_at ? formatDateTime(user.last_sign_in_at) : "Never"}</td>
                      <td className="row-actions">
                        <button
                          type="button"
                          className="text-button"
                          disabled={busy || locked}
                          onClick={() =>
                            void act(
                              () => api.users.update(user.id, { is_active: !user.is_active }),
                              user.is_active
                                ? `${user.email} deactivated and signed out.`
                                : `${user.email} reactivated.`,
                            )
                          }
                        >
                          {user.is_active ? "Deactivate" : "Reactivate"}
                        </button>
                        <button
                          type="button"
                          className="text-button"
                          disabled={busy || locked}
                          onClick={() => resetPassword(user)}
                        >
                          Reset password
                        </button>
                        <button
                          type="button"
                          className="text-button"
                          disabled={busy || user.active_sessions === 0}
                          onClick={() =>
                            void act(
                              () => api.users.revokeSessions(user.id),
                              `${user.email} was signed out everywhere.`,
                            )
                          }
                        >
                          Sign out everywhere
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </AppShell>
  );
}
