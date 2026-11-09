import { API_BASE } from "../../lib/config";
import { clearSession } from "../../lib/session";

/** An HTTP error from the API, carrying the server's `detail` message when present. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function errorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH";
  body?: unknown;
  /** Public requests (login, customer payment page) never redirect to the login page on 401. */
  authenticated?: boolean;
};

// The session is an HttpOnly cookie the browser sends to our own origin (/api is proxied to
// the backend). The custom header proves the request came from this app, not a forged form.
const CSRF_HEADER = { "X-Requested-With": "recoverx-web" };

export async function request<T>(path: string, { method = "GET", body, authenticated = true }: RequestOptions = {}) {
  const headers = new Headers(CSRF_HEADER);
  if (body !== undefined) headers.set("Content-Type", "application/json");

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
    cache: "no-store",
  });

  if (!response.ok) {
    if (response.status === 401 && authenticated) {
      clearSession();
      window.location.replace("/login");
    }
    throw new ApiError(response.status, await readDetail(response));
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

type ValidationIssue = { loc?: Array<string | number>; msg?: string };

async function readDetail(response: Response) {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.detail) && payload.detail.length > 0) {
      const issue = payload.detail[0] as ValidationIssue;
      const field = issue.loc?.filter((part) => part !== "body" && part !== "query").join(".");
      return field ? `${field}: ${issue.msg ?? "invalid value"}` : (issue.msg ?? "Invalid request");
    }
  } catch {
    // Non-JSON error body; fall through to the generic message.
  }
  return response.status === 413 ? "The file is too large to upload." : `Request failed (${response.status})`;
}
