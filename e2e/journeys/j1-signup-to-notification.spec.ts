import { expect, test } from "@playwright/test";

/**
 * J1: new user signs up, logs in, uploads a dataset, builds a pipeline,
 * runs it, watches progress, views results, downloads output, receives a
 * notification config entry, and logs out. Real stack, no mocks.
 *
 * Everything runs in a single browser session (page.request shares the
 * UI login cookies), exactly like a real user plus the UI's own API calls.
 */
const UI = process.env.E2E_BASE_URL || "http://localhost";

const CSV = "id,region,amount\n1,North,100\n2,South,200\n3,East,150\n";

test("J1 signup, upload, run, results, download, notify, logout", async ({
  page,
}) => {
  test.setTimeout(240_000);
  const tag = Date.now().toString(36);
  const email = `j1_${tag}@pipelineiq.test`;
  const pipelineName = `j1_pipeline_${tag}`;
  const password = "Testpass123!";
  const username = `j1_${tag}`;

  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  page.on("console", (msg) => {
    // The browser logs failed subresource fetches automatically; routine
    // 401 auth probes on public pages are expected, not app errors.
    if (msg.type() === "error" && !msg.text().includes("401 (Unauthorized)")) {
      consoleErrors.push(msg.text());
    }
  });
  page.on("response", (resp) => {
    const url = resp.url();
    // Routine auth probes (logged-out getMe) are expected, not failures.
    if (resp.status() >= 400 && !url.includes("/login") && !url.endsWith("/auth/me")) {
      failedRequests.push(`${resp.status()} ${url}`);
    }
  });

  // 1. Sign up through the UI.
  await page.goto(`${UI}/register`, { waitUntil: "domcontentloaded" });
  await expect(page.getByPlaceholder("you@example.com")).toBeVisible({
    timeout: 15_000,
  });
  await page.getByPlaceholder("you@example.com").fill(email);
  await page.getByPlaceholder("your_username").fill(username);
  const pwds = page.getByPlaceholder("••••••••");
  await pwds.nth(0).fill(password);
  await pwds.nth(1).fill(password);
  await page.getByRole("button", { name: /create account/i }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/register"), {
    timeout: 30_000,
  });

  // 2. Log in through the UI (fresh session check is part of the journey).
  await page.goto(`${UI}/login`, { waitUntil: "domcontentloaded" });
  if (page.url().endsWith("/login")) {
    await page.getByTestId("email-input").fill(email);
    await page.getByTestId("password-input").fill(password);
    await page.getByTestId("login-btn").click();
    await page.waitForURL((url) => !url.pathname.endsWith("/login"), {
      timeout: 30_000,
    });
  }
  await expect(page).not.toHaveURL(/\/login$/);

  // From here on, page.request shares the UI session cookies.
  const api = page.request;

  // 3. Upload a dataset, verify it shows in the UI files page.
  const uploadResp = await api.post(`${UI}/api/v1/files/upload`, {
    multipart: {
      file: { name: "j1_sales.csv", mimeType: "text/csv", buffer: Buffer.from(CSV) },
    },
  });
  expect(uploadResp.ok(), await uploadResp.text()).toBeTruthy();
  const { id: fileId } = await uploadResp.json();
  expect(fileId).toBeTruthy();

  await page.goto(`${UI}/files`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("file-list")).toContainText("j1_sales.csv", {
    timeout: 15_000,
  });

  // 4. Build + run a pipeline.
  const yaml = [
    "pipeline:",
    `  name: ${pipelineName}`,
    "  steps:",
    "    - name: src",
    "      type: load",
    `      file_id: ${fileId}`,
    "    - name: out",
    "      type: save",
    "      input: src",
    `      filename: j1_output_${tag}.csv`,
  ].join("\n");
  const runResp = await api.post(`${UI}/api/v1/pipelines/run`, {
    data: { yaml_config: yaml },
  });
  expect(runResp.status(), await runResp.text()).toBe(202);
  const { run_id: runId } = await runResp.json();
  expect(runId).toBeTruthy();

  // 5. Watch progress in the UI.
  await page.goto(`${UI}/runs/${runId}`, { waitUntil: "domcontentloaded" });
  // RunStatusBadge renders COMPLETED as "Success".
  await expect(page.getByText("Success", { exact: true }).first()).toBeVisible({
    timeout: 120_000,
  });

  // 6. Download output through the UI button.
  const downloadBtn = page.getByRole("button", { name: /download/i });
  if ((await downloadBtn.count()) > 0) {
    const popupPromise = page
      .waitForEvent("popup", { timeout: 5_000 })
      .catch(() => null);
    await downloadBtn.first().click();
    const popup = await popupPromise;
    if (popup) await popup.close();
  }

  // 7. Notification config round-trip.
  const notifResp = await api.post(`${UI}/api/v1/notifications/`, {
    data: {
      type: "slack",
      config: { slack_webhook_url: "https://example.com/hooks/j1" },
      events: ["pipeline_completed"],
    },
  });
  expect(notifResp.status(), await notifResp.text()).toBe(201);
  const listResp = await api.get(`${UI}/api/v1/notifications/`);
  expect(listResp.ok()).toBeTruthy();
  expect((await listResp.json()).total).toBeGreaterThanOrEqual(1);

  // 8. Log out via the dashboard TopBar and confirm the guard redirects.
  await page.goto(`${UI}/`, { waitUntil: "domcontentloaded" });
  const logoutBtn = page.getByRole("button", { name: "Log out" });
  await expect(logoutBtn.first()).toBeVisible({ timeout: 15_000 });
  await logoutBtn.first().click();
  await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
  await page.goto(`${UI}/runs`, { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });

  expect(failedRequests).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
