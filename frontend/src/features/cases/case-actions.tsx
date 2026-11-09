"use client";

import { type FormEvent, useState } from "react";
import { useSession } from "../../hooks/use-session";
import { formatInr, humanize } from "../../lib/format";
import { canOperate, canReview } from "../../lib/roles";
import { api } from "../../services/api/recoverx";
import type { CaseDetail, Resolution } from "../../types/api";

const TERMINAL = new Set(["RECOVERED", "STOPPED", "EXPIRED"]);
const DEFAULT_PROMISE_DAYS = 7;

/** Runs a backend command and reports the result on the case page; resolves to whether it succeeded. */
export type RunCommand = (command: () => Promise<unknown>, success: string, failure: string) => Promise<boolean>;

const isoDate = (date: Date) => date.toISOString().slice(0, 10);

/**
 * Every control here calls a backend endpoint; the Tool Gateway and policy engine still decide.
 * Operators work open cases; once a case is escalated only a reviewer (finance manager or admin) acts.
 */
export function CaseActions({ item, busy, run }: Readonly<{ item: CaseDetail; busy: boolean; run: RunCommand }>) {
  const role = useSession()?.user.role;
  const choices = item.executable_actions;
  const recommendedRunnable = choices.includes(item.recommended_action);
  const [chosenAction, setChosenAction] = useState("");
  const action = choices.includes(chosenAction)
    ? chosenAction
    : recommendedRunnable
      ? item.recommended_action
      : (choices[0] ?? "");
  const [notes, setNotes] = useState("");

  if (TERMINAL.has(item.status)) {
    return (
      <p className="muted action-note">This case is {humanize(item.status).toLowerCase()}; its outcome is final.</p>
    );
  }
  if (!canOperate(role)) {
    return <p className="muted action-note">Your role can view this case but not act on it.</p>;
  }

  const actionPicker = choices.length > 0 && (
    <label className="inline-field">
      <span>Recovery action</span>
      <select value={action} onChange={(event) => setChosenAction(event.target.value)} disabled={busy}>
        {choices.map((choice) => (
          <option key={choice} value={choice}>
            {humanize(choice)}
            {choice === item.recommended_action ? " (recommended)" : ""}
          </option>
        ))}
      </select>
    </label>
  );

  if (item.status === "ESCALATED") {
    if (!canReview(role)) {
      return (
        <p className="muted action-note">
          Waiting for a reviewer. A finance manager or admin decides what happens next.
        </p>
      );
    }
    const resolve = (resolution: Resolution, success: string, actionType?: string) =>
      run(
        () => api.cases.resolve(item.id, resolution, { actionType, notes: notes.trim() || undefined }),
        success,
        "The decision could not be applied.",
      );
    return (
      <div className="review-controls">
        <div className="action-row">
          <button
            type="button"
            className="primary-button"
            disabled={busy || !recommendedRunnable}
            title={
              recommendedRunnable
                ? undefined
                : `${humanize(item.recommended_action)} cannot be executed; choose an action`
            }
            onClick={() => resolve("APPROVE", `Approved: ${humanize(item.recommended_action)} sent.`)}
          >
            Approve {humanize(item.recommended_action).toLowerCase()}
          </button>
          {actionPicker}
          <button
            type="button"
            className="secondary-button"
            disabled={busy || !action || action === item.recommended_action}
            onClick={() => resolve("EDIT", `${humanize(action)} sent instead of the recommendation.`, action)}
          >
            Run chosen action
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={busy}
            onClick={() => resolve("CONTACT", "Personalised outreach sent.")}
          >
            Contact customer
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={busy}
            onClick={() => resolve("REJECT", "Recommendation rejected; case stopped.")}
          >
            Reject
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={busy}
            onClick={() => resolve("CLOSE", "Case closed.")}
          >
            Close case
          </button>
        </div>
        <label className="review-notes">
          <span>Reviewer notes (saved with the decision)</span>
          <textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={2} maxLength={2000} />
        </label>
      </div>
    );
  }

  return (
    <>
      <div className="action-row">
        {actionPicker}
        <button
          type="button"
          className="primary-button"
          disabled={busy || !action}
          onClick={() =>
            run(
              () => api.cases.execute(item.id, action === item.recommended_action ? undefined : action),
              `${humanize(action)} executed.`,
              "Recovery could not be executed.",
            )
          }
        >
          Execute recovery
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={busy}
          onClick={() =>
            run(() => api.cases.escalate(item.id), "Sent to the review queue.", "Unable to escalate this case.")
          }
        >
          Escalate to reviewer
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={busy}
          onClick={() =>
            run(
              () => api.cases.stop(item.id),
              "Case stopped; its payment link is cancelled.",
              "Unable to stop this case.",
            )
          }
        >
          Stop case
        </button>
      </div>
      <PromiseForm item={item} busy={busy} run={run} />
    </>
  );
}

function PromiseForm({ item, busy, run }: Readonly<{ item: CaseDetail; busy: boolean; run: RunCommand }>) {
  const today = isoDate(new Date());
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState(item.amount);
  const [date, setDate] = useState(() => isoDate(new Date(Date.now() + DEFAULT_PROMISE_DAYS * 86_400_000)));

  if (!open) {
    return (
      <button type="button" className="link-button" disabled={busy} onClick={() => setOpen(true)}>
        Record a promise to pay
      </button>
    );
  }
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void run(
      () => api.cases.promise(item.id, amount, `${date}T00:00:00Z`),
      `Promise of ${formatInr(amount)} by ${date} recorded; it is checked on that date.`,
      "The promise could not be recorded.",
    ).then((succeeded) => succeeded && setOpen(false));
  };
  return (
    <form className="promise-form" onSubmit={submit}>
      <label className="inline-field">
        <span>Amount (₹)</span>
        <input
          type="number"
          min={1}
          max={item.amount}
          value={amount}
          onChange={(event) => setAmount(Number(event.target.value))}
          required
        />
      </label>
      <label className="inline-field">
        <span>Promised date</span>
        <input type="date" min={today} value={date} onChange={(event) => setDate(event.target.value)} required />
      </label>
      <button type="submit" className="primary-button" disabled={busy}>
        Record promise
      </button>
      <button type="button" className="secondary-button" disabled={busy} onClick={() => setOpen(false)}>
        Cancel
      </button>
    </form>
  );
}
