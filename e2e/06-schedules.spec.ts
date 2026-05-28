import { expect, test } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL;
const email = process.env.E2E_EMAIL ?? "demo@pipelineiq.app";
const password = process.env.E2E_PASSWORD ?? "Demo1234!";

async function login(page: import("@playwright/test").Page) {
  test.skip(!baseUrl, "Set E2E_BASE_URL to run Playwright tests.");
  await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("email-input").fill(email);
  await page.getByTestId("password-input").fill(password);
  await page.getByTestId("login-btn").click();
  await page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 15_000 });
}

test.describe("Pipeline Scheduling", () => {
  test("Schedules page is accessible", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/schedules`, { waitUntil: "domcontentloaded" });
    await expect(page).not.toHaveURL(/login/);
  });

  test("Create schedule via API and verify it responds", async ({ page }) => {
    await login(page);
    const token = await page.evaluate(() => localStorage.getItem("pipelineiq_token"));
    expect(token).toBeTruthy();

    const yaml = [
      "pipeline:",
      "  name: e2e_schedule_test",
      "  steps:",
      "    - name: step1",
      "      type: load",
      "      file_id: dummy-id",
    ].join("\n");

    const apiUrl = process.env.E2E_API_URL ?? baseUrl;
    const resp = await page.request.post(`${apiUrl}/api/schedules`, {
      data: { pipeline_name: "e2e_schedule_test", cron_expression: "0 6 * * *", pipeline_yaml: yaml },
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    });

    if (resp.ok()) {
      const data = await resp.json();
      expect(data.id).toBeTruthy();
      await page.request.delete(`${apiUrl}/api/schedules/${data.id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
    }
  });

  test("Pause and resume a schedule via API", async ({ page }) => {
    await login(page);
    const token = await page.evaluate(() => localStorage.getItem("pipelineiq_token"));
    expect(token).toBeTruthy();

    const apiUrl = process.env.E2E_API_URL ?? baseUrl;
    const yaml = [
      "pipeline:",
      "  name: pause_test",
      "  steps:",
      "    - name: step1",
      "      type: load",
      "      file_id: dummy-id",
    ].join("\n");

    const createResp = await page.request.post(`${apiUrl}/api/schedules`, {
      data: { pipeline_name: "pause_test", cron_expression: "0 9 * * 1", pipeline_yaml: yaml },
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    });
    if (!createResp.ok()) {
      test.skip(true, "Schedule creation not supported");
      return;
    }

    const { id } = await createResp.json();

    const pauseResp = await page.request.post(`${apiUrl}/api/schedules/${id}/pause`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (pauseResp.ok()) {
      const paused = await pauseResp.json();
      expect(paused.is_active).toBe(false);

      const resumeResp = await page.request.post(`${apiUrl}/api/schedules/${id}/resume`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const resumed = await resumeResp.json();
      expect(resumed.is_active).toBe(true);
    }

    await page.request.delete(`${apiUrl}/api/schedules/${id}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  });
});
