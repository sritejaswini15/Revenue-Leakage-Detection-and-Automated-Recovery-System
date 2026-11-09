import type {
  Analytics,
  AuditEvent,
  CaseDetail,
  CaseSummary,
  Customer,
  CustomerDetail,
  CustomerPaymentResult,
  EscalationList,
  EvaluationSummary,
  ImportSummary,
  LatestEvaluation,
  IntegrationStatus,
  LoginResponse,
  ManagedUser,
  OffsetPage,
  Page,
  Policy,
  PromiseToPay,
  PublicPaymentRequest,
  RazorpayIntegration,
  Resolution,
  Role,
  SessionUser,
  StrategyPerformance,
  Transaction,
  TransactionDetail,
  TrendPoint,
} from "../../types/api";
import { request } from "./client";

export type CaseQuery = { status?: string; query?: string; limit?: number };
export type CustomerQuery = {
  search?: string;
  contact?: "all" | "contactable" | "opted_out";
  sort?: "value" | "name" | "recent";
  limit?: number;
  offset?: number;
};
export type TransactionQuery = {
  search?: string;
  status?: string;
  date_from?: string;
  date_to?: string;
  case_id?: string;
  limit?: number;
  offset?: number;
};
export type RazorpayCredentials = {
  key_id: string;
  key_secret: string;
  account_id: string;
  webhook_secret?: string;
  verify: boolean;
};

function queryString(params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const post = <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body });
const patch = <T>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body });
const id = encodeURIComponent;

export const api = {
  auth: {
    login: (email: string, password: string) =>
      request<LoginResponse>("/auth/login", { method: "POST", body: { email, password }, authenticated: false }),
    logout: () => request<void>("/auth/logout", { method: "POST", authenticated: false }),
    me: () => request<SessionUser>("/auth/me"),
    changePassword: (currentPassword: string, newPassword: string) =>
      post<{ sessions_revoked: number }>("/auth/password", {
        current_password: currentPassword,
        new_password: newPassword,
      }),
  },

  cases: {
    list: ({ status, query, limit = 100 }: CaseQuery = {}) =>
      request<Page<CaseSummary>>(`/recovery-cases${queryString({ status, query, limit })}`),
    get: (caseId: string) => request<CaseDetail>(`/recovery-cases/${id(caseId)}`),
    execute: (caseId: string, actionType?: string) =>
      post<{ case_id: string; status: string; action_id: string }>(
        `/recovery-cases/${id(caseId)}/execute`,
        actionType ? { action_type: actionType } : undefined,
      ),
    stop: (caseId: string) => post<{ case_id: string; status: string }>(`/recovery-cases/${id(caseId)}/stop`),
    escalate: (caseId: string, notes?: string) =>
      post<{ case_id: string; status: string }>(`/recovery-cases/${id(caseId)}/escalate`, { notes }),
    promise: (caseId: string, amount: number, promisedDate: string, notes?: string) =>
      post<PromiseToPay>(`/recovery-cases/${id(caseId)}/promise-to-pay`, {
        amount,
        promised_date: promisedDate,
        notes,
      }),
    escalated: ({ queue, limit = 25, offset = 0 }: { queue?: string; limit?: number; offset?: number } = {}) =>
      request<EscalationList>(`/escalations${queryString({ queue, limit, offset })}`),
    resolve: (caseId: string, resolution: Resolution, options: { actionType?: string; notes?: string } = {}) =>
      post<{ case_id: string; resolution: string; status: string }>(`/escalations/${id(caseId)}/resolve`, {
        resolution,
        action_type: options.actionType,
        notes: options.notes,
      }),
  },

  customers: {
    list: (query: CustomerQuery = {}) => request<OffsetPage<Customer>>(`/customers${queryString(query)}`),
    get: (customerId: string) => request<CustomerDetail>(`/customers/${id(customerId)}`),
    importCsv: (csv: string, commit: boolean) => post<ImportSummary>("/customers/import", { csv, commit }),
    syncRazorpay: (commit: boolean) => post<ImportSummary>("/customers/sync", { commit }),
  },

  transactions: {
    list: (query: TransactionQuery = {}) => request<OffsetPage<Transaction>>(`/transactions${queryString(query)}`),
    get: (paymentId: string) => request<TransactionDetail>(`/transactions/${id(paymentId)}`),
  },

  analytics: {
    overview: (days?: number) => request<Analytics>(`/analytics/recovery${queryString({ days })}`),
    strategies: (days?: number) =>
      request<StrategyPerformance[]>(`/analytics/strategy-performance${queryString({ days })}`),
    trend: (days: number) => request<TrendPoint[]>(`/analytics/trend${queryString({ days })}`),
    evaluate: () => post<EvaluationSummary>("/demo/run-batch"),
    latestEvaluation: () => request<LatestEvaluation | null>("/demo/evaluation"),
  },

  audit: (limit = 50, caseId?: string, offset = 0) =>
    request<Page<AuditEvent>>(`/audit-events${queryString({ limit, case_id: caseId, offset })}`),

  policy: {
    get: () => request<Policy>("/policies"),
    update: (policy: Policy) => request<Policy>("/policies", { method: "PUT", body: policy }),
  },

  integrations: {
    status: () => request<IntegrationStatus>("/integrations/status"),
    razorpay: () => request<RazorpayIntegration>("/integrations/razorpay"),
    saveRazorpay: (credentials: RazorpayCredentials) =>
      request<RazorpayIntegration>("/integrations/razorpay", { method: "PUT", body: credentials }),
    validateRazorpay: () => post<RazorpayIntegration>("/integrations/razorpay/validate"),
    setRazorpayEnabled: (enabled: boolean) => patch<RazorpayIntegration>("/integrations/razorpay", { enabled }),
  },

  users: {
    list: () => request<ManagedUser[]>("/users"),
    create: (user: { name: string; email: string; role: Role; password: string }) => post<ManagedUser>("/users", user),
    update: (userId: string, changes: { name?: string; role?: Role; is_active?: boolean }) =>
      patch<ManagedUser>(`/users/${id(userId)}`, changes),
    resetPassword: (userId: string, password: string) =>
      post<ManagedUser>(`/users/${id(userId)}/password`, { password }),
    revokeSessions: (userId: string) => post<ManagedUser>(`/users/${id(userId)}/revoke-sessions`),
  },

  simulation: {
    event: (eventType: string, customerId: string, amount: number) =>
      post<{ event_id: string | null; case_id: string | null; status: string | null }>("/simulation/event", {
        event_type: eventType,
        customer_id: customerId,
        amount,
      }),
    payment: (caseId: string) =>
      post<{ case_id: string; status: string; recovered_amount: number; message: string }>("/simulation/payment", {
        case_id: caseId,
      }),
    timeout: (caseId: string) =>
      post<{
        case_id: string;
        status: string;
        workflow_preserved: boolean;
        action_id: string;
        action_type: string;
        failed_attempts: number;
        messages_sent: number;
      }>("/simulation/timeout", { case_id: caseId }),
    optOut: (customerId: string) =>
      post<{ customer_id: string; opted_out: boolean; cases_stopped: number }>("/simulation/opt-out", {
        customer_id: customerId,
      }),
    lowConfidence: (caseId: string) =>
      post<{ case_id: string; status: string }>("/simulation/low-confidence", { case_id: caseId }),
    maxAttempts: (caseId: string) =>
      post<{ case_id: string; status: string }>("/simulation/max-attempts", { case_id: caseId }),
    invalidAi: (caseId: string) =>
      post<{ case_id: string; status: string }>("/simulation/invalid-ai", { case_id: caseId }),
    promise: (caseId: string, amount: number, promisedDate: string) =>
      post<{ id: string; case_id: string }>("/simulation/promise-to-pay", {
        case_id: caseId,
        amount,
        promised_date: promisedDate,
      }),
  },

  // Public customer payment page: no session, addressed by the link token.
  payments: {
    get: (token: string) => request<PublicPaymentRequest>(`/pay/${id(token)}`, { authenticated: false }),
    pay: (token: string) =>
      request<CustomerPaymentResult>(`/pay/${id(token)}`, {
        method: "POST",
        body: { action: "pay" },
        authenticated: false,
      }),
    promise: (token: string, promiseDate: string) =>
      request<CustomerPaymentResult>(`/pay/${id(token)}`, {
        method: "POST",
        body: { action: "promise", promise_date: promiseDate },
        authenticated: false,
      }),
  },
};
