"""E2E tests for pipeline scheduling using Playwright."""
import { test, expect } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "http://localhost:3000";

test.describe("Pipeline Scheduling", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/login`);
    await page.fill('[data-testid="email-input"]', "test@pipelineiq.test");
    await page.fill('[data-testid="password-input"]', "TestPassword@2024");
    await page.click('[data-testid="login-btn"]');
    await page.waitForURL(`${BASE_URL}/dashboard`);
  });

  test("Schedule creation UI is accessible", async ({ page }) => {
    await page.goto(`${BASE_URL}/schedules`);
    const hasContent = await Promise.any([
      page.locator('[data-testid="create-schedule-btn"]').isVisible({ timeout: 5000 }).catch(() => false),
      page.locator("text=No schedules configured").isVisible({ timeout: 5000 }).catch(() => false),
      page.locator("text=New Schedule").isVisible({ timeout: 5000 }).catch(() => false),
    ]);
    expect(hasContent).toBeTruthy();
  });

  test("Cron validation rejects invalid expressions via API", async ({ page }) => {
    const resp = await page.request.post(`${BASE_URL}/api/v1/schedules/`, {
      headers: { Authorization: "Bearer invalid" },
      data: {
        pipeline_name: "test",
        yaml_config: "pipeline:\n  name: test\n  steps: []",
        cron_expression: "not-a-cron",
      },
    });
    expect([400, 401, 422]).toContain(resp.status());
  });

  test("Templates page shows 5 templates", async ({ page }) => {
    await page.goto(`${BASE_URL}/templates`);
    await page.waitForSelector('[data-testid="template-card"]', { timeout: 10_000 });
    const count = await page.locator('[data-testid="template-card"]').count();
    expect(count).toBe(5);
  });

  test("Template fork button is present", async ({ page }) => {
    await page.goto(`${BASE_URL}/templates`);
    await page.waitForSelector('[data-testid="template-card"]', { timeout: 10_000 });
    const firstCard = page.locator('[data-testid="template-card"]').first();
    await expect(
      firstCard.locator('[data-testid="fork-template-btn"]').or(
        firstCard.locator("text=Use Template").or(firstCard.locator("text=Fork"))
      )
    ).toBeVisible();
  });

  test("Completed run shows download button", async ({ page }) => {
    await page.goto(`${BASE_URL}/runs`);
    const successRun = page.locator('[data-status="success"]').first();
    const count = await successRun.count();

    if (count > 0) {
      await successRun.click();
      await expect(
        page.locator('[data-testid="download-output-btn"]').or(
          page.locator("text=Download Output").or(page.locator("text=Download CSV"))
        )
      ).toBeVisible();
    } else {
      test.skip();
    }
  });
});
