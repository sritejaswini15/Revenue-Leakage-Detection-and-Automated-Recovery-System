"use client";

import { type ChangeEvent, useState } from "react";
import { ErrorState, Notice, Panel, StatusBadge } from "../../components/ui";
import { errorMessage } from "../../services/api/client";
import { api } from "../../services/api/recoverx";
import type { ImportSummary } from "../../types/api";

const MAX_FILE_BYTES = 2_000_000;
const TEMPLATE = "name,email,phone,razorpay_customer_id,preferred_channel,lifetime_value,opted_out";

type Source = { kind: "csv"; name: string; text: string } | { kind: "razorpay" };

/** Preview first, then apply: nothing is written until the operator confirms the summary. */
export function CustomerImport({ onImported }: Readonly<{ onImported: () => void }>) {
  const [source, setSource] = useState<Source | null>(null);
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const preview = async (next: Source) => {
    setSource(next);
    setSummary(null);
    setError("");
    setNotice("");
    setBusy(true);
    try {
      setSummary(
        next.kind === "csv" ? await api.customers.importCsv(next.text, false) : await api.customers.syncRazorpay(false),
      );
    } catch (problem) {
      setError(errorMessage(problem, "The preview could not be prepared."));
    } finally {
      setBusy(false);
    }
  };

  const chooseFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > MAX_FILE_BYTES) {
      setError("Files up to 2 MB can be imported at a time; split larger files.");
      return;
    }
    await preview({ kind: "csv", name: file.name, text: await file.text() });
  };

  const apply = async () => {
    if (!source) return;
    setBusy(true);
    try {
      const result =
        source.kind === "csv"
          ? await api.customers.importCsv(source.text, true)
          : await api.customers.syncRazorpay(true);
      setSummary(result);
      setNotice(`Imported: ${result.created} created, ${result.updated} updated.`);
      onImported();
    } catch (problem) {
      setError(errorMessage(problem, "The import failed; nothing was written."));
    } finally {
      setBusy(false);
    }
  };

  const writable = summary ? summary.created + summary.updated : 0;
  return (
    <Panel title="Import customers" aside="Preview before anything is saved">
      <div className="import-sources">
        <label className="secondary-button file-button">
          Choose CSV file
          <input type="file" accept=".csv,text/csv" onChange={chooseFile} disabled={busy} />
        </label>
        <button
          type="button"
          className="secondary-button"
          disabled={busy}
          onClick={() => preview({ kind: "razorpay" })}
        >
          Sync from Razorpay
        </button>
      </div>
      <p className="muted small-print">
        Columns: <code>{TEMPLATE}</code>. A name and one of email, phone or Razorpay id are required. Indian 10-digit
        mobile numbers get +91. Existing customers are matched by Razorpay id, email, then phone. An import can opt a
        customer out but never back in.
      </p>
      {error && <ErrorState message={error} />}
      <Notice message={notice} />
      {busy && !summary && <p className="muted">Checking every row…</p>}
      {summary && (
        <>
          <div className="stat-grid">
            <ImportStat label="Rows" value={summary.total_rows} />
            <ImportStat label="New" value={summary.created} />
            <ImportStat label="Updates" value={summary.updated} />
            <ImportStat label="Duplicates" value={summary.duplicates} />
            <ImportStat label="Invalid" value={summary.invalid} />
          </div>
          {summary.ignored_columns.length > 0 && (
            <p className="muted small-print">Ignored columns: {summary.ignored_columns.join(", ")}</p>
          )}
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Line</th>
                  <th>Result</th>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Phone</th>
                  <th>Problems</th>
                </tr>
              </thead>
              <tbody>
                {summary.rows.map((row) => (
                  <tr key={`${row.line}-${row.action}`}>
                    <td>{row.line}</td>
                    <td>
                      <StatusBadge status={row.action} />
                    </td>
                    <td>{row.name || "—"}</td>
                    <td>{row.email || "—"}</td>
                    <td>{row.phone || "—"}</td>
                    <td className="muted">{row.errors.join("; ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!summary.committed && (
            <div className="action-row">
              <button type="button" className="primary-button" disabled={busy || writable === 0} onClick={apply}>
                {busy ? "Saving…" : `Save ${writable} customers`}
              </button>
              <span className="muted">
                {source?.kind === "csv" ? source.name : "Razorpay customers"}: invalid and duplicate rows are skipped.
              </span>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}

function ImportStat({ label, value }: Readonly<{ label: string; value: number }>) {
  return (
    <div className="stat-box">
      <span>{label}</span>
      <b>{value.toLocaleString("en-IN")}</b>
    </div>
  );
}
