import { createHmac, randomUUID } from "node:crypto";
import { expect, request as playwrightRequest, test, type APIRequestContext, type Page } from "@playwright/test";

/*
 * End-to-end journeys against a running RecoverX stack seeded with demo data.
 * Requires a non-production backend (demo logins and /demo/* operations are enabled).
 * The browser talks to the API through the app's own /api proxy with an HttpOnly cookie;
 * direct API calls here use bearer tokens, as any API client would.
 */

const API_URL = process.env.E2E_API_URL ?? "http://localhost:8000";
const ADMIN = {
  email: process.env.E2E_EMAIL ?? "admin@recoverx.local",
  password: process.env.E2E_PASSWORD ?? "recoverx-demo",
};
const SESSION_COOKIE = "recoverx_session";

async function apiToken(request: APIRequestContext, email = ADMIN.email, password = ADMIN.password) {
  const response = await request.post(`${API_URL}/auth/login`, { data: { email, password } });
  expect(response.ok(), await response.text()).toBeTruthy();
  return ((await response.json()) as { access_token: string }).access_token;
}

async function api<T>(request: APIRequestContext, method: "GET" | "POST", path: string, data?: unknown) {
  const token = await apiToken(request);
  const response = await request.fetch(`${API_URL}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}` },
    data,
  });
  expect(response.ok(), `${method} ${path}: ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

async function signIn(page: Page, email = ADMIN.email, password = ADMIN.password) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/$/);
}

test.beforeAll(async ({ request }) => {
  await api(request, "POST", "/demo/reset", { customers: 1000, cases: 500, seed: 20260902 });
});

// --- access ----------------------------------------------------------------------------------

test("signed-out visitors are sent to the login page", async ({ page }) => {
  await page.goto("/cases");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".error-state")).toHaveText("Invalid credentials");
});

test("the session is an HttpOnly cookie that scripts cannot read", async ({ page }) => {
  await signIn(page);
  const cookie = (await page.context().cookies()).find((item) => item.name === SESSION_COOKIE);
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Lax");
  expect(await page.evaluate(() => document.cookie)).not.toContain(SESSION_COOKIE);
  expect(await page.evaluate(() => JSON.stringify(window.localStorage))).not.toContain(cookie?.value ?? "missing");
});

test("cookie-authenticated writes without the CSRF header are refused", async ({ page }) => {
  await signIn(page);
  const policy = await (await page.request.get("/api/policies")).json();
  const forged = await page.request.put("/api/policies", { data: policy });
  expect(forged.status()).toBe(403);
  const genuine = await page.request.put("/api/policies", {
    data: policy,
    headers: { "X-Requested-With": "recoverx-web" },
  });
  expect(genuine.status()).toBe(200);
});

test("signing out revokes the session on the server", async ({ page }) => {
  await signIn(page);
  const token = (await page.context().cookies()).find((item) => item.name === SESSION_COOKIE)?.value;
  expect(token).toBeTruthy();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);

  const response = await page.request.get(`${API_URL}/recovery-cases?limit=1`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.status()).toBe(401);
  expect((await page.request.get("/api/auth/me")).status()).toBe(401);
});

test("API enforces roles and webhook signatures", async ({ request }) => {
  const viewer = await apiToken(request, "viewer@recoverx.local");
  const execute = await request.post(`${API_URL}/recovery-cases/RC-ACME-12500/execute`, {
    headers: { Authorization: `Bearer ${viewer}` },
  });
  expect(execute.status()).toBe(403);
  const users = await request.get(`${API_URL}/users`, { headers: { Authorization: `Bearer ${viewer}` } });
  expect(users.status()).toBe(403);
  const anonymous = await playwrightRequest.newContext(); // no cookies from earlier logins
  expect((await anonymous.get(`${API_URL}/recovery-cases`)).status()).toBe(401);
  await anonymous.dispose();

  // A real merchant account id, signed with the wrong secret.
  const { account_id } = await api<{ account_id: string }>(request, "GET", "/integrations/razorpay");
  const body = JSON.stringify({ account_id, event: "payment.failed", payload: {} });
  const forged = await request.post(`${API_URL}/webhooks/razorpay`, {
    headers: {
      "Content-Type": "application/json",
      "X-Razorpay-Signature": createHmac("sha256", "not-the-secret").update(body).digest("hex"),
      "X-Razorpay-Event-Id": `evt_e2e_${randomUUID().slice(0, 8)}`,
    },
    data: body,
  });
  // Without a configured account id (BOOTSTRAP_RAZORPAY_ACCOUNT_ID) the event cannot be routed at all.
  expect(forged.status()).toBe(account_id ? 401 : 400);
});

// --- recovery --------------------------------------------------------------------------------

test("command center shows live metrics, trend and priority cases", async ({ page, request }) => {
  await signIn(page);
  const analytics = await api<{ cases: number }>(request, "GET", "/analytics/recovery?days=30");

  await expect(page.getByRole("heading", { name: /^Good (morning|afternoon|evening)/ })).toBeVisible();
  await expect(page.getByText("Revenue at risk").first()).toBeVisible();
  await expect(
    page.getByText(`across ${analytics.cases.toLocaleString("en-IN")} cases opened in 30 days`),
  ).toBeVisible();
  await expect(page.locator(".recharts-wrapper").first()).toBeVisible(); // recovery trend chart
  await expect(page.locator('a[href^="/cases/RC-"]').first()).toBeVisible();
});

test("case queue searches on the server", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Cases" }).first().click();
  await expect(page.getByRole("heading", { name: "Case queue" })).toBeVisible();
  await page.getByLabel("Search cases").fill("RC-RAHUL");
  await expect(page.locator('a[href^="/cases/"]')).toHaveCount(1);
  await expect(page.getByText("1 cases")).toBeVisible();
});

test("golden path: execute recovery, customer pays, case is recovered", async ({ page, context }) => {
  await signIn(page);
  await page.goto("/cases/RC-RAHUL-18K");
  await expect(page.getByRole("heading", { level: 1, name: "Rahul Sharma" })).toBeVisible();
  await expect(page.getByText("Recovery diagnosis")).toBeVisible();

  await page.getByRole("button", { name: "Execute recovery" }).click();
  await expect(page.locator(".inline-notice")).toHaveText(/executed\.$/);
  const paymentLink = page.locator('a[href^="/pay/"]');
  await expect(paymentLink).toBeVisible();

  const customerPage = await context.newPage();
  await customerPage.goto((await paymentLink.getAttribute("href")) as string);
  await expect(customerPage.getByText("Hi Rahul,")).toBeVisible();
  await customerPage.getByRole("button", { name: /^Pay ₹18,000 now$/ }).click();
  await expect(customerPage.getByRole("heading", { name: "Payment received, Rahul" })).toBeVisible();

  await page.reload();
  await expect(page.locator(".route-heading .status")).toHaveText("RECOVERED");
  await expect(page.getByText("Payment Recovered")).toBeVisible(); // audit timeline

  // The paying payment is a transaction linked to the case.
  await page.getByRole("link", { name: "Transactions" }).last().click();
  await expect(page).toHaveURL(/\/transactions\?case_id=RC-RAHUL-18K$/);
  await expect(page.locator(".transactions-table .case-row").first()).toBeVisible();
});

test("an operator can choose a different recovery action", async ({ page, request }) => {
  const customers = await api<{ items: Array<{ id: string; opted_out: boolean }> }>(
    request,
    "GET",
    "/customers?contact=contactable&limit=1",
  );
  const created = await api<{ case_id: string; status: string }>(request, "POST", "/simulation/event", {
    event_type: "PAYMENT_FAILURE",
    customer_id: customers.items[0].id,
    amount: 900,
  });
  expect(created.status).toBe("WAITING_FOR_PAYMENT");

  await signIn(page);
  await page.goto(`/cases/${created.case_id}`);
  await page.getByLabel("Recovery action").selectOption("REMINDER");
  await page.getByRole("button", { name: "Execute recovery" }).click();
  await expect(page.locator(".inline-notice")).toHaveText(/^Reminder executed\.$/i);
  await expect(page.getByText("Actions and payment attempts")).toBeVisible();
  await expect(page.locator(".queue-row").filter({ hasText: "Reminder" }).first()).toBeVisible();
  await expect(page.getByText("Action Override")).toBeVisible(); // audit timeline
});

test("a declined retry falls back to a payment link and says so", async ({ page, request }) => {
  // The simulated provider declines retries for customers at or below 70% reliability.
  const customers = await api<{ items: Array<{ id: string; payment_reliability_pct: number }> }>(
    request,
    "GET",
    "/customers?contact=contactable&limit=500",
  );
  const unreliable = customers.items.filter((item) => item.payment_reliability_pct <= 70);
  let caseId: string | null = null;
  for (const customer of unreliable) {
    const created = await api<{ case_id: string; status: string }>(request, "POST", "/simulation/event", {
      event_type: "PAYMENT_FAILURE",
      customer_id: customer.id,
      amount: 600,
    });
    if (created.status === "WAITING_FOR_PAYMENT") {
      caseId = created.case_id;
      break;
    }
  }
  test.skip(caseId === null, "No auto-approved low-reliability customer in this dataset");

  await signIn(page);
  await page.goto(`/cases/${caseId}`);
  await page.getByLabel("Recovery action").selectOption("PAYMENT_RETRY");
  await page.getByRole("button", { name: "Execute recovery" }).click();
  await expect(page.locator(".inline-notice")).toHaveText(/^Payment retry executed\.$/i);
  await expect(page.getByText("sent a payment link instead").first()).toBeVisible();
  await expect(page.locator(".route-heading .status")).toHaveText("WAITING FOR PAYMENT");
});

test("a replayed provider event is recognised and opens no second case", async ({ request }) => {
  const customers = await api<{ items: Array<{ id: string }> }>(
    request,
    "GET",
    "/customers?contact=contactable&limit=1",
  );
  const event = {
    event_type: "PAYMENT_FAILURE",
    customer_id: customers.items[0].id,
    amount: 700,
    id: `evt_e2e_${randomUUID().slice(0, 10)}`,
  };
  const first = await api<{ case_id: string; is_duplicate: boolean }>(request, "POST", "/simulation/event", event);
  const replay = await api<{ case_id: string; is_duplicate: boolean }>(request, "POST", "/simulation/event", event);
  expect(first.is_duplicate).toBe(false);
  expect(replay.is_duplicate).toBe(true);
  expect(replay.case_id).toBe(first.case_id);
});

test("a person can resolve an escalated case", async ({ page }) => {
  await signIn(page);
  await page.goto("/escalations");
  const firstRow = page.locator(".queue-row").first();
  const caseId = (await firstRow.locator("small").first().textContent())?.split(" · ")[0];
  await firstRow.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText(`Case ${caseId} rejected: Stopped.`)).toBeVisible();
});

test("a reviewer sees why a case was escalated and closes it from the case page", async ({ page }) => {
  await signIn(page);
  await page.goto("/escalations");
  await page.getByRole("tab", { name: /^High value/ }).click();
  const row = page.locator(".queue-row").first();
  const caseId = (await row.locator("small").first().textContent())?.split(" · ")[0] as string;
  await row.getByRole("link", { name: "Review" }).click();

  await expect(page).toHaveURL(new RegExp(`/cases/${caseId}$`));
  await expect(page.getByText("WHY THIS CASE IS PRIORITISED")).toBeVisible();
  await expect(page.getByRole("heading", { name: /^Now: (Human Review|Stop)$/ })).toBeVisible(); // live policy
  await page.getByLabel("Reviewer notes (saved with the decision)").fill("Customer settled offline.");
  await page.getByRole("button", { name: "Close case" }).click();
  await expect(page.locator(".inline-notice")).toHaveText("Case closed.");
  await expect(page.locator(".route-heading .status")).toHaveText("STOPPED");
  await expect(page.getByText("Customer settled offline.").first()).toBeVisible(); // escalation panel and timeline
});

test("simulation center routes low advisor confidence to human review", async ({ page }) => {
  await signIn(page);
  await page.goto("/simulator");
  await expect(page.getByRole("heading", { name: "RecoverX Simulation Center" })).toBeVisible();
  await page.getByRole("button", { name: "Low advisor confidence" }).click();
  await expect(page.getByRole("log")).toHaveText("Low-confidence decision escalated: Escalated.");
});

// --- data screens ----------------------------------------------------------------------------

test("transactions can be filtered and opened", async ({ page }) => {
  await signIn(page);
  await page.goto("/transactions");
  await expect(page.getByRole("heading", { name: "Transactions" })).toBeVisible();
  await page.getByLabel("Payment status").selectOption("FAILED");
  const rows = page.locator(".transactions-table .case-row");
  await expect(rows.first()).toBeVisible();
  await expect(page.locator(".transactions-table .status").first()).toHaveText("FAILED");
  await rows.first().click();
  await expect(page.getByRole("heading", { name: "Attempts" })).toBeVisible();
  await expect(page.getByText("Recovery case").first()).toBeVisible();
  await page.getByRole("button", { name: "Next", exact: true }).click(); // not the dev-tools button
  await expect(page.getByText(/^26–50 of/)).toBeVisible();
});

test("customers can be searched and imported from CSV", async ({ page }) => {
  await signIn(page);
  await page.goto("/customers");
  await page.getByLabel("Search customers").fill("Rahul Sharma");
  await page.locator('a[href="/customers/cus_rahul_golden_demo"]').click();
  await expect(page.getByRole("heading", { level: 1, name: "Rahul Sharma" })).toBeVisible();
  await expect(page.getByText("Payment reliability")).toBeVisible();

  await page.goto("/customers");
  await page.getByRole("button", { name: "Import or sync" }).click();
  const tag = randomUUID().slice(0, 6);
  const csv = [
    "name,email,phone",
    `E2E Asha ${tag},asha.${tag}@example.test,98765 43210`,
    `E2E Bad ${tag},not-an-email,`,
    `E2E Again ${tag},asha.${tag}@example.test,`,
  ].join("\n");
  await page
    .locator('input[type="file"]')
    .setInputFiles({ name: "customers.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await expect(page.locator(".stat-box").filter({ hasText: "New" })).toContainText("1");
  await expect(page.locator(".stat-box").filter({ hasText: "Invalid" })).toContainText("1");
  await expect(page.locator(".stat-box").filter({ hasText: "Duplicates" })).toContainText("1");
  await page.getByRole("button", { name: "Save 1 customers" }).click();
  await expect(page.getByText("Imported: 1 created, 0 updated.")).toBeVisible();
  await page.getByLabel("Search customers").fill(`asha.${tag}`);
  await expect(page.getByText(`E2E Asha ${tag}`)).toBeVisible();
});

test("an administrator adds a user who then changes their password", async ({ page }) => {
  const email = `e2e.${randomUUID().slice(0, 8)}@merchant.test`;
  await signIn(page);
  await page.goto("/users");
  await page.getByLabel("Name", { exact: true }).fill("E2E Operator");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("New user role").selectOption("OPERATOR");
  await page.getByLabel("Temporary password").fill("temporary-pass-0001");
  await page.getByRole("button", { name: "Add user" }).click();
  await expect(page.getByText(`${email} can now sign in.`)).toBeVisible();
  await expect(page.locator("td").filter({ hasText: email })).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();

  await signIn(page, email, "temporary-pass-0001");
  await expect(page.getByRole("link", { name: "Users" })).toHaveCount(0); // not an administrator
  await page.goto("/account");
  await page.getByLabel("Current password").fill("temporary-pass-0001");
  await page.getByLabel(/^New password/).fill("permanent-pass-0002");
  await page.getByLabel("Confirm new password").fill("permanent-pass-0002");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByText(/^Password changed\./)).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await signIn(page, email, "permanent-pass-0002");
});

test("policy and integration pages reflect the backend", async ({ page }) => {
  await signIn(page);
  await page.goto("/policies");
  await expect(page.getByLabel("Maximum contact attempts")).toHaveValue("3");
  await expect(page.getByLabel("Close unpaid cases after (days)")).toHaveValue("7");
  await page.getByRole("button", { name: "Save policy" }).click();
  await expect(page.getByText("Policy saved.")).toBeVisible();

  await page.goto("/integrations");
  const razorpay = page.locator(".integration-card").filter({ has: page.locator("b", { hasText: /^Razorpay$/ }) });
  await expect(razorpay).toContainText("Simulated");
  await expect(page.getByRole("heading", { name: "Razorpay credentials" })).toBeVisible();
  await page.getByLabel("Key id").fill("rzp_live_NotAllowed01");
  await page.getByLabel("Key secret").fill("not-a-real-secret");
  await page.getByLabel("Razorpay account id").fill("acc_e2e_live_check");
  await page.getByRole("button", { name: "Save keys" }).click();
  await expect(page.locator(".inline-notice")).toContainText(/Live keys are disabled|rzp_test_/);
});

// Last: it expires every stale demo case.
test("the hourly job expires stale cases and cancels their links", async ({ page, request }) => {
  // Demo cases overdue more than a week are already escalated by policy, and escalated cases never
  // expire; shorten the window so the open demo cases (opened 1-7 days ago) are past it.
  const policy = await api<Record<string, unknown>>(request, "GET", "/policies");
  const token = await apiToken(request);
  const headers = { Authorization: `Bearer ${token}` };
  expect(
    (await request.put(`${API_URL}/policies`, { headers, data: { ...policy, case_expiry_days: 1 } })).ok(),
  ).toBeTruthy();
  try {
    const run = await api<{ expired: number }>(request, "POST", "/demo/run-due-workflows");
    expect(run.expired).toBeGreaterThan(0);
  } finally {
    await request.put(`${API_URL}/policies`, { headers, data: policy });
  }

  await signIn(page);
  await page.goto("/cases");
  await page.getByLabel("Filter by status").selectOption("EXPIRED");
  const first = page.locator('a[href^="/cases/"]').first();
  await expect(first).toBeVisible();
  await first.click();
  await expect(page.locator(".route-heading .status")).toHaveText("EXPIRED");
  await expect(page.getByText("Case Expired")).toBeVisible(); // audit timeline
});
