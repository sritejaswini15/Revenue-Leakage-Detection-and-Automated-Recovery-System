"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { ErrorState } from "../../components/ui";
import { useSession } from "../../hooks/use-session";
import { IS_DEVELOPMENT } from "../../lib/config";
import { saveSession } from "../../lib/session";
import { errorMessage } from "../../services/api/client";
import { api } from "../../services/api/recoverx";

export function LoginForm() {
  const router = useRouter();
  const session = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (session) router.replace("/");
  }, [session, router]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      // The server sets an HttpOnly session cookie; only the public profile is kept here.
      const result = await api.auth.login(email, password);
      saveSession(result.user);
      router.replace("/");
    } catch (failure) {
      setError(errorMessage(failure, "Unable to reach the RecoverX API. Check that the backend is running."));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="login-page">
      <section className="login-card">
        <div className="brand login-brand">
          <span className="brand-mark">R</span>
          <span>
            recover<span className="brand-x">X</span>
          </span>
        </div>
        <p className="eyebrow">REVENUE RECOVERY CONTROL PLANE</p>
        <h1>Sign in to RecoverX</h1>
        <p className="login-subtitle">Operate recovery workflows with policy, context, and measurable outcomes.</p>
        <form onSubmit={submit}>
          <label>
            Email
            <input
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error && <ErrorState message={error} />}
          <button className="primary-button" type="submit" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
        {IS_DEVELOPMENT && <small className="login-note">Local demo login: admin@recoverx.local / recoverx-demo</small>}
      </section>
    </main>
  );
}
