import { expect, test } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL;
const email = process.env.E2E_EMAIL ?? "demo@pipelineiq.app";
const password = process.env.E2E_PASSWORD ?? "Demo1234!";

async function login(page: import("@playwright/test").Page) {
  test.skip(!baseUrl, "Set E2E_BASE_URL to run AI Playwright tests.");

  await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("email-input").fill(email);
  await page.getByTestId("password-input").fill(password);
  await page.getByTestId("login-btn").click();

  await page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 20_000 });
}

test.describe("AI pipeline generation", () => {
  test("opens and closes AI generate modal from /pipelines/new", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });

    await page.getByTestId("open-ai-generate-btn").click();
    await expect(page.getByTestId("ai-generate-modal")).toBeVisible();

    await page.getByTestId("ai-modal-close").click();
    await expect(page.getByTestId("ai-generate-modal")).toBeHidden();
  });

  test("keeps Generate button disabled until requirements are met", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });

    await page.getByTestId("open-ai-generate-btn").click();
    const generateButton = page.getByTestId("ai-generate-btn");
    await expect(generateButton).toBeDisabled();

    await page.getByTestId("ai-description-input").fill(
      "Build a pipeline that filters delivered orders and aggregates by region"
    );

    const checkboxes = page.locator('[data-testid="ai-generate-modal"] input[type="checkbox"]');
    const checkboxCount = await checkboxes.count();
    if (checkboxCount > 0) {
      for (let i = 0; i < checkboxCount; i++) {
        const checkbox = checkboxes.nth(i);
        if (await checkbox.isChecked()) {
          await checkbox.uncheck();
        }
      }
      await expect(generateButton).toBeDisabled();
      await checkboxes.first().check();
      await expect(generateButton).toBeEnabled();
    } else {
      await expect(generateButton).toBeDisabled();
    }
  });

  test("shows repair action on failed runs", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/runs`, { waitUntil: "domcontentloaded" });

    const failedRows = page.locator('[data-status="failed"]');
    const failedCount = await failedRows.count();
    test.skip(failedCount === 0, "No failed runs available in this environment.");

    await expect(page.getByTestId("repair-pipeline-btn").first()).toBeVisible();
  });

  test("validates YAML via AI validate endpoint", async ({ page }) => {
    await login(page);
    const token = await page.evaluate(() => localStorage.getItem("pipelineiq_token"));
    expect(token).toBeTruthy();

    const response = await page.request.post(`${baseUrl}/api/ai/validate-yaml`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
      data: {
        yaml_text: "pipeline:\n  name: ai_test\n  steps:\n    - name: one\n      type: load\n      file_id: x",
      },
    });

    expect(response.ok()).toBeTruthy();
    const payload = await response.json();
    expect(payload).toHaveProperty("valid");
  });
});
