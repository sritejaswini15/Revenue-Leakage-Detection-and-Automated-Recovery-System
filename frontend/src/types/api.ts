// Response shapes of the RecoverX API. Keep in sync with backend/app/api/schemas.

export type CaseStatus =
  | "NEW"
  | "ACTIONABLE"
  | "WAITING_FOR_PAYMENT"
  | "PROMISE_TO_PAY"
  | "ESCALATED"
  | "RECOVERED"
  | "STOPPED"
  | "EXPIRED"
  | (string & {});

export type CaseSummary = {
  id: string;
  customer_id: string;
  customer: string;
  customer_phone: string;
  customer_email: string;
  customer_reliability: number;
  preferred_channel: string;
  preferred_language: string;
  event_type: string;
  amount: number;
  currency: string;
  risk_score: number;
  recovery_probability: number;
  expected_recovery_value: number;
  ai_confidence: number;
  policy_decision: string;
  recommended_action: string;
  status: CaseStatus;
  contact_attempts: number;
  recovered_amount: number;
  action_executed: boolean;
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
};

export type CaseDetail = CaseSummary & {
  advisor_model: string | null;
  diagnosis: string;
  root_cause: string;
  ai_reasoning: string;
  payment_link: {
    id: string;
    provider_link_id: string | null;
    amount: number;
    status: string;
    short_url: string | null;
    payment_url: string | null;
  } | null;
  communications: Array<{
    id: string;
    channel: string;
    direction: string;
    content: string;
    status: string;
    created_at: string;
  }>;
  escalation: { id: string; reason: string; resolution: string | null; resolution_notes: string | null } | null;
  promises_to_pay: Array<{ id: string; amount: number; promised_date: string; status: string; fulfilled: boolean }>;
  actions: Array<{ id: string; action_type: string; status: string; created_at: string; detail: string | null }>;
  payment_attempts: Array<{
    provider_payment_id: string;
    attempt_id: string | null;
    status: string;
    failure_reason: string | null;
    created_at: string;
  }>;
  /** Actions policy allows on this open case: run by an operator, or by the reviewer (EDIT) once escalated. */
  executable_actions: string[];
  /** The merchant's current policy applied now; the Tool Gateway re-checks it before every action. */
  policy_evaluation: PolicyEvaluation | null;
  /** Why the case is prioritised, from the risk engine's factors. */
  risk_reasons: Array<{ text: string; effect: "positive" | "negative" | "neutral" }>;
};

export type PolicyEvaluation = {
  decision: "AUTO_APPROVE" | "HUMAN_REVIEW" | "STOP" | (string & {});
  reasons: string[];
  violations: string[];
  max_amount_limit: number;
  min_confidence_limit: number;
  current_attempts: number;
  max_attempts_limit: number;
};

/** A reviewer's decision: the first three run an action through the Tool Gateway, the last two stop the case. */
export type Resolution = "APPROVE" | "EDIT" | "CONTACT" | "REJECT" | "CLOSE";

export type PromiseToPay = { id: string; amount: number; promised_date: string; status: string; fulfilled: boolean };

export type Page<T> = { items: T[]; total: number };

export type EscalationQueueName =
  | "HIGH_VALUE"
  | "LOW_CONFIDENCE"
  | "PROMISE_MISSED"
  | "MAX_ATTEMPTS"
  | "PROVIDER_FAILURE"
  | "CUSTOMER_REQUEST"
  | "SPECIAL_CASE";

export type EscalationItem = CaseSummary & {
  escalation_reason: string;
  queue: EscalationQueueName | (string & {});
  escalated_at: string | null;
};

/** `total` counts the requested queue; `queues` always counts every queue. */
export type EscalationList = OffsetPage<EscalationItem> & { queues: Record<string, number> };

export type OffsetPage<T> = Page<T> & { limit: number; offset: number };

/** Combined figures, and the merchant profile and observed payments they come from. */
export type CustomerStats = {
  reliability_pct: number;
  successful_payments: number;
  failed_payments: number;
  profile_reliability_pct: number;
  profile_successful: number;
  profile_failed: number;
  observed_successful: number;
  observed_failed: number;
  observed_captured_amount: number;
  last_payment_at: string | null;
};

export type Customer = {
  id: string;
  razorpay_customer_id: string;
  name: string;
  email: string;
  phone: string;
  lifetime_value: number;
  successful_payments: number;
  failed_payments: number;
  payment_reliability_pct: number;
  preferred_channel: string;
  preferred_language: string;
  historical_recovery_rate: number;
  /** Learned from closed cases: how many, and the action that most often recovered them. */
  previous_recovery_attempts: number;
  best_historical_recovery_action: string | null;
  opted_out: boolean;
  created_at: string;
  stats: CustomerStats;
};

export type PaymentOrigin = "webhook" | "seed" | (string & {});

export type CustomerDetail = Customer & {
  payments: Array<{
    id: string;
    provider_payment_id: string;
    amount: number;
    currency: string;
    status: string;
    method: string | null;
    failure_reason: string | null;
    origin: PaymentOrigin;
    recovery_case_id: string | null;
    created_at: string;
  }>;
  cases: Array<{
    id: string;
    status: CaseStatus;
    amount: number;
    recovered_amount: number;
    currency: string;
    created_at: string;
  }>;
};

export type ImportRow = {
  line: number;
  action: "create" | "update" | "duplicate" | "invalid";
  name: string;
  email: string;
  phone: string;
  razorpay_customer_id: string;
  errors: string[];
};

export type ImportSummary = {
  source: "csv" | "razorpay";
  committed: boolean;
  total_rows: number;
  created: number;
  updated: number;
  duplicates: number;
  invalid: number;
  ignored_columns: string[];
  rows: ImportRow[];
};

export type Transaction = {
  id: string;
  provider_payment_id: string;
  amount: number;
  currency: string;
  status: "CREATED" | "AUTHORIZED" | "CAPTURED" | "FAILED" | (string & {});
  method: string | null;
  failure_reason: string | null;
  origin: PaymentOrigin;
  customer_id: string;
  customer_name: string | null;
  recovery_case_id: string | null;
  created_at: string;
  updated_at: string;
};

export type TransactionDetail = Transaction & {
  attempts: Array<{
    provider_attempt_id: string | null;
    status: string;
    failure_reason: string | null;
    created_at: string;
  }>;
  case: { id: string; status: CaseStatus; amount: number; recovered_amount: number } | null;
};

export type ManagedUser = {
  id: string;
  name: string;
  email: string;
  role: Role;
  is_active: boolean;
  created_at: string;
  active_sessions: number;
  last_sign_in_at: string | null;
  environment_managed: boolean;
};

export type Role = "ADMIN" | "FINANCE_MANAGER" | "OPERATOR" | "VIEWER";

export type RazorpayIntegration = {
  source: "merchant" | "legacy-environment" | "simulated";
  provider: string;
  mode: "test" | "live" | null;
  key_id_masked: string | null;
  has_webhook_secret: boolean;
  account_id: string | null;
  enabled: boolean;
  validated_at: string | null;
  last_error: string | null;
  live_payments_allowed: boolean;
  webhook_path: string;
};

export type Analytics = {
  revenue_at_risk: number;
  revenue_targeted: number;
  revenue_recovered: number;
  recovery_rate: number;
  expected_recovery_value: number;
  cases: number;
  successful_recoveries: number;
  escalations: number;
  stopped_cases: number;
  auto_approved: number;
  action_failures: number;
  average_ai_confidence: number;
  average_recovery_time_hours: number;
  funnel: { risk_cases: number; eligible: number; actions: number; successful: number };
  payments: {
    captured: number;
    captured_amount: number;
    failed: number;
    failed_amount: number;
    authorized: number;
    success_rate: number;
  };
  window_days: number | null;
  window_start: string | null;
  mode: "simulation" | "live";
};

export type StrategyPerformance = {
  strategy: string;
  cases: number;
  attempts: number;
  success: number;
  recovery_rate: number;
  recovered: number;
  average_recovery_time: number;
};

export type TrendPoint = { date: string; detected: number; recovered: number; cases: number };

export type EvaluationSummary = {
  cases_processed: number;
  advisor_model: string;
  revenue_at_risk: number;
  revenue_targeted: number;
  revenue_recovered: number;
  recovery_rate: number;
  average_recovery_probability: number;
  average_ai_confidence: number;
  successful_recoveries: number;
  escalations: number;
  false_escalations: number;
  policy_violations: number;
  invalid_ai_decisions: number;
  action_failures: number;
  average_recovery_time_hours: number;
  evaluation_status: "PASSED" | "FAILED";
};

export type LatestEvaluation = { summary: EvaluationSummary; run_by: string; run_at: string };

export type AuditEvent = {
  id: string;
  event_name: string;
  case_id: string | null;
  actor: string;
  payload: Record<string, unknown>;
  correlation_id: string | null;
  created_at: string;
};

export type Policy = {
  max_auto_action_amount: number;
  human_approval_above_amount: number;
  max_contact_attempts: number;
  escalate_after_days: number;
  case_expiry_days: number;
  min_ai_confidence: number;
  /** Comma-separated, most preferred first. */
  preferred_channels: string;
  supported_languages: string;
  /** Comma-separated recovery actions the Tool Gateway may execute. */
  allowed_actions: string;
};

export type IntegrationEntry = {
  name: string;
  category: string;
  status: "CONNECTED" | "CONFIGURED" | "SIMULATED" | "NOT_CONFIGURED";
  label: string;
  detail: string;
};

export type IntegrationStatus = {
  integrations: IntegrationEntry[];
  counts: { total: number; connected: number; configured: number; simulated: number; not_configured: number };
};

export type SessionUser = {
  id: string;
  name: string;
  email: string;
  role: Role | (string & {});
  organization_id: string;
};

export type LoginResponse = { access_token: string; token_type: string; user: SessionUser };

export type PublicPaymentRequest = {
  case_id: string;
  customer_name: string;
  amount: number;
  currency: string;
  status: CaseStatus;
  recovered_amount: number;
  event_type: string;
  failure_reason: string;
  recommended_action: string;
  payment_link: { id: string; short_url: string | null; amount: number; status: string };
};

export type CustomerPaymentResult = {
  case_id: string;
  status: CaseStatus;
  recovered_amount?: number | null;
  promise_id?: string | null;
  promised_date?: string | null;
  message?: string | null;
};
