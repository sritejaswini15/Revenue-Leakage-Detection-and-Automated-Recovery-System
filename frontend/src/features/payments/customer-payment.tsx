"use client";

import { type ReactNode, useCallback, useState } from "react";
import { useApi } from "../../hooks/use-api";
import { formatInr } from "../../lib/format";
import { errorMessage } from "../../services/api/client";
import { api } from "../../services/api/recoverx";

const REASONS: Record<string, string> = {
  PAYMENT_FAILURE: "Your payment could not be processed",
  SUBSCRIPTION_HALTED: "Your subscription payment failed",
  INVOICE_OVERDUE: "Your invoice is overdue",
  CHECKOUT_ABANDONED: "Your checkout was not completed",
  PAYMENT_LINK_ABANDONED: "Your payment was not completed",
};

function inSevenDays() {
  const date = new Date();
  date.setDate(date.getDate() + 7);
  return date.toISOString().slice(0, 10);
}

function Card({ children, className = "" }: Readonly<{ children: ReactNode; className?: string }>) {
  return (
    <div className="pay-shell">
      <div className={`pay-card ${className}`}>{children}</div>
    </div>
  );
}

/** Public page reached from the link in a recovery message. No login. */
export function CustomerPayment({ token }: Readonly<{ token: string }>) {
  const load = useCallback(() => api.payments.get(token), [token]);
  const { data, error } = useApi(load);
  const [outcome, setOutcome] = useState<{ title: string; message: string; amount: number } | null>(null);
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState<"pay" | "promise" | null>(null);
  const [promising, setPromising] = useState(false);
  const [promiseDate, setPromiseDate] = useState(inSevenDays);

  const submit = async (action: "pay" | "promise") => {
    if (!data) return;
    setBusy(action);
    setFailure("");
    try {
      if (action === "pay") {
        const result = await api.payments.pay(token);
        setOutcome({
          title: "Payment received",
          message: "Thank you. Your account is up to date.",
          amount: result.recovered_amount ?? data.amount,
        });
      } else {
        const result = await api.payments.promise(token, promiseDate);
        const date = new Date(`${result.promised_date ?? promiseDate}T00:00:00`);
        setOutcome({
          title: "Promise recorded",
          message: `We'll expect your payment by ${date.toLocaleDateString("en-IN", { dateStyle: "long" })}.`,
          amount: data.amount,
        });
      }
    } catch (problem) {
      setFailure(errorMessage(problem, "Something went wrong. Please check your connection and try again."));
    } finally {
      setBusy(null);
    }
  };

  if (error) {
    const closed = error.includes("invalid or expired");
    return (
      <Card className="error-card">
        <span className="pay-logo">
          R<span>X</span>
        </span>
        <h1>Payment not available</h1>
        <p>{closed ? "This payment link has expired, was already used, or is not valid." : error}</p>
        <p className="pay-help">Please reply to the message that brought you here for a new link.</p>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card>
        <span className="pay-logo">
          R<span>X</span>
        </span>
        <p className="pay-loading">Loading payment details…</p>
      </Card>
    );
  }

  const firstName = data.customer_name.split(" ")[0];
  return (
    <Card>
      <div className="pay-header">
        <span className="pay-logo">
          R<span>X</span>
        </span>
        <span className="pay-powered">Powered by RecoverX</span>
      </div>

      {outcome ? (
        <div className="pay-success">
          <div className="pay-success-icon">✓</div>
          <h1>
            {outcome.title}, {firstName}
          </h1>
          <p>{outcome.message}</p>
          <div className="pay-amount-done">{formatInr(outcome.amount)}</div>
        </div>
      ) : (
        <>
          <div className="pay-greeting">
            <p className="pay-hi">Hi {firstName},</p>
            <p className="pay-reason">{REASONS[data.event_type] ?? "A payment is pending on your account"}</p>
          </div>
          <div className="pay-amount-block">
            <span className="pay-label">Amount due</span>
            <strong className="pay-amount">{formatInr(data.amount)}</strong>
          </div>
          {failure && (
            <div className="pay-notice" role="alert">
              {failure}
            </div>
          )}
          {promising ? (
            <div className="pay-promise-form">
              <p>When can you make the payment?</p>
              <label>
                Payment date
                <input
                  type="date"
                  value={promiseDate}
                  min={new Date().toISOString().slice(0, 10)}
                  onChange={(event) => setPromiseDate(event.target.value)}
                />
              </label>
              <div className="pay-actions">
                <button type="button" className="pay-btn-primary" onClick={() => submit("promise")} disabled={!!busy}>
                  {busy === "promise" ? "Recording…" : "Confirm promise to pay"}
                </button>
                <button
                  type="button"
                  className="pay-btn-secondary"
                  onClick={() => setPromising(false)}
                  disabled={!!busy}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="pay-actions">
              <button type="button" className="pay-btn-primary" onClick={() => submit("pay")} disabled={!!busy}>
                {busy === "pay" ? "Processing payment…" : `Pay ${formatInr(data.amount)} now`}
              </button>
              <button type="button" className="pay-btn-secondary" onClick={() => setPromising(true)} disabled={!!busy}>
                Promise to pay later
              </button>
            </div>
          )}
          <p className="pay-security">🔒 Secure payment · Simulated Razorpay checkout</p>
        </>
      )}
    </Card>
  );
}
