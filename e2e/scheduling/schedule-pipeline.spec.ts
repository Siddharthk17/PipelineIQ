import { test, expect } from "@playwright/test";

test.use({ storageState: "e2e/.auth/demo.json" });

const baseUrl = process.env.E2E_BASE_URL;

test.describe("Pipeline Scheduling", () => {
  test("Schedule creation UI is accessible", async ({ page }) => {
    test.skip(!baseUrl, "Set E2E_BASE_URL to run Playwright tests.");
    await page.goto(`${baseUrl}/schedules`, { waitUntil: "domcontentloaded" });
    // Any one of these proves the page rendered (Promise.any would resolve
    // on the first `false`, so assert directly instead).
    await expect(
      page.locator("text=No schedules configured").or(
        page.locator('[data-testid="create-schedule-btn"]'),
      ).or(page.locator("text=New Schedule")),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("Cron validation rejects invalid expressions via API", async ({ page }) => {
    test.skip(!baseUrl, "Set E2E_BASE_URL to run Playwright tests.");
    const resp = await page.request.post(`${baseUrl}/api/v1/schedules`, {
      data: {
        pipeline_name: "test",
        yaml_config: "pipeline:\n  name: test\n  steps: []",
        cron_expression: "not-a-cron",
      },
    });
    expect([400, 401, 422]).toContain(resp.status());
  });

  test("Templates page shows 5 templates", async ({ page }) => {
    test.skip(!baseUrl, "Set E2E_BASE_URL to run Playwright tests.");
    await page.goto(`${baseUrl}/templates`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="template-card"]', { timeout: 10_000 });
    const count = await page.locator('[data-testid="template-card"]').count();
    expect(count).toBe(5);
  });

  test("Template card opens the builder with the template", async ({ page }) => {
    test.skip(!baseUrl, "Set E2E_BASE_URL to run Playwright tests.");
    await page.goto(`${baseUrl}/templates`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="template-card"]', { timeout: 10_000 });
    // Forking happens by opening a template card in the builder.
    await page.locator('[data-testid="template-card"]').first().click();
    await page.waitForURL((u) => u.pathname === "/pipelines/new" && u.searchParams.has("template"), { timeout: 10_000 });
  });

  test("Completed run shows download button", async ({ page }) => {
    test.skip(!baseUrl, "Set E2E_BASE_URL to run Playwright tests.");
    await page.goto(`${baseUrl}/runs`, { waitUntil: "domcontentloaded" });
    const successRun = page.locator('[data-status="completed"]').first();
    const count = await successRun.count();

    if (count > 0) {
      await successRun.click();
      await expect(
        page.locator('[data-testid="download-output-btn"]').or(
          page.locator("text=Download Output").or(page.locator("text=Download CSV"))
        )
      ).toBeVisible();
    } else {
      test.skip(true, "No completed runs available");
    }
  });
});
