const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

export function formatInr(value: number) {
  return inr.format(value);
}

export function formatPercent(ratio: number, digits = 1) {
  return `${(ratio * 100).toFixed(digits)}%`;
}

export function formatDateTime(iso: string) {
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

/** "PAYMENT_LINK_PAID" -> "Payment Link Paid" */
// Stored rule codes predate the advisor's naming; show them in today's words.
const REASON_LABELS: Record<string, string> = {
  LOW_AI_CONFIDENCE: "Low advisor confidence",
};

/** A comma-separated list of escalation reason codes, readable. */
export function formatReasons(codes: string) {
  return codes
    .split(",")
    .map((code) => code.trim())
    .filter(Boolean)
    .map((code) => REASON_LABELS[code] ?? humanize(code))
    .join(", ");
}

export function humanize(value: string) {
  return value
    .replaceAll(/[_.]/g, " ")
    .toLowerCase()
    .replace(/(^| )\w/g, (letter) => letter.toUpperCase());
}

export function initials(name: string) {
  return name
    .split(" ")
    .filter(Boolean)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

export function riskTier(score: number) {
  if (score >= 75) return "high";
  if (score >= 50) return "medium";
  return "low";
}
