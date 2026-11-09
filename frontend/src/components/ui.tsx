import type { ReactNode } from "react";
import { riskTier } from "../lib/format";

export function PageHeading({
  eyebrow,
  title,
  subtitle,
  actions,
}: Readonly<{ eyebrow: string; title: string; subtitle?: string; actions?: ReactNode }>) {
  return (
    <div className="route-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        {subtitle && <p className="subheading">{subtitle}</p>}
      </div>
      {actions && <div className="heading-actions">{actions}</div>}
    </div>
  );
}

export function MetricCard({
  label,
  value,
  detail,
  tone = "",
}: Readonly<{ label: string; value: string; detail?: string; tone?: "coral" | "mint" | "amber" | "blue" | "" }>) {
  return (
    <div className={`metric-card ${tone}`}>
      <div className="metric-top">
        <span>{label}</span>
      </div>
      <b className="metric-value">{value}</b>
      {detail && <span className="metric-detail">{detail}</span>}
    </div>
  );
}

export function Panel({
  title,
  aside,
  className = "",
  children,
}: Readonly<{ title: string; aside?: ReactNode; className?: string; children: ReactNode }>) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-header">
        <h2>{title}</h2>
        {typeof aside === "string" ? <span className="muted">{aside}</span> : aside}
      </div>
      {children}
    </section>
  );
}

export function StatusBadge({ status }: Readonly<{ status: string }>) {
  return <span className={`status status-${status.toLowerCase()}`}>{status.replaceAll("_", " ")}</span>;
}

export function RiskBadge({ score }: Readonly<{ score: number }>) {
  return (
    <span className={`risk ${riskTier(score)}`} title={`Risk score ${score}/100`}>
      {score}
    </span>
  );
}

export function DetailMetric({ label, value }: Readonly<{ label: string; value: ReactNode }>) {
  return (
    <div className="detail-metric">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

export function EmptyState({ title, body }: Readonly<{ title: string; body?: string }>) {
  return (
    <div className="empty-state">
      <h2>{title}</h2>
      {body && <p>{body}</p>}
    </div>
  );
}

export function LoadingState({ label }: Readonly<{ label: string }>) {
  return (
    <div className="loading-state" role="status">
      {label}
    </div>
  );
}

export function ErrorState({ message }: Readonly<{ message: string }>) {
  return (
    <div className="error-state" role="alert">
      {message}
    </div>
  );
}

export function Notice({ message }: Readonly<{ message: string }>) {
  return message ? (
    <output className="inline-notice" aria-live="polite">
      {message}
    </output>
  ) : null;
}

/** Previous/next paging for offset-based lists, with the visible range. */
export function Pager({
  total,
  offset,
  limit,
  onChange,
  noun,
}: Readonly<{ total: number; offset: number; limit: number; onChange: (offset: number) => void; noun: string }>) {
  if (total === 0) return null;
  const last = Math.min(offset + limit, total);
  return (
    <nav className="pager" aria-label={`${noun} pages`}>
      <span className="muted">
        {offset + 1}–{last} of {total.toLocaleString("en-IN")} {noun}
      </span>
      <span className="pager-buttons">
        <button
          type="button"
          className="secondary-button"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          Previous
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={last >= total}
          onClick={() => onChange(offset + limit)}
        >
          Next
        </button>
      </span>
    </nav>
  );
}
