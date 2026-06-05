import { expect, test } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL;
const apiUrl = process.env.E2E_API_URL ?? baseUrl;
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

test.describe("AI Pipeline Generation", () => {
  test("AI Generate modal opens from builder toolbar", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });

    await page.getByTestId("open-ai-generate-btn").click();
    await expect(page.getByTestId("ai-generate-modal")).toBeVisible({ timeout: 5_000 });
  });

  test("Generate button is disabled until requirements are met", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });

    await page.getByTestId("open-ai-generate-btn").click();
    await expect(page.getByTestId("ai-generate-btn")).toBeDisabled();

    await page.getByTestId("ai-description-input").fill(
      "Build a pipeline that filters delivered orders and aggregates by region",
    );

    const checkboxes = page.locator('[data-testid="ai-generate-modal"] input[type="checkbox"]');
    const checkboxCount = await checkboxes.count();
    if (checkboxCount > 0) {
      for (let i = 0; i < checkboxCount; i++) {
        await checkboxes.nth(i).uncheck().catch(() => {});
      }
      await checkboxes.first().check();
      await expect(page.getByTestId("ai-generate-btn")).toBeEnabled();
    }
  });

  test("AI validate YAML endpoint returns structured response", async ({ page }) => {
    await login(page);

    const response = await page.request.post(`${apiUrl}/api/ai/validate-yaml`, {
      headers: { "Content-Type": "application/json" },
      data: {
        yaml_text: [
          "pipeline:",
          "  name: validate_test",
          "  steps:",
          "    - name: one",
          "      type: load",
          "      file_id: dummy",
        ].join("\n"),
      },
    });
    expect(response.ok()).toBeTruthy();
    const payload = await response.json();
    expect(payload).toHaveProperty("valid");
  });

  test("repair action visible on failed runs", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/runs`, { waitUntil: "domcontentloaded" });

    const failedRows = page.locator('[data-status="failed"]');
    const failedCount = await failedRows.count();
    test.skip(failedCount === 0, "No failed runs available in this environment.");

    await expect(page.getByTestId("repair-pipeline-btn").first()).toBeVisible();
  });
});
