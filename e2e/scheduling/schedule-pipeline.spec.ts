import { test, expect } from "../fixtures/auth";

test.describe("Pipeline Scheduling", () => {
  test("Schedule creation UI is accessible", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/schedules");
    const hasContent = await Promise.any([
      authenticatedPage.locator('[data-testid="create-schedule-btn"]').isVisible({ timeout: 5000 }).catch(() => false),
      authenticatedPage.locator("text=No schedules configured").isVisible({ timeout: 5000 }).catch(() => false),
      authenticatedPage.locator("text=New Schedule").isVisible({ timeout: 5000 }).catch(() => false),
    ]);
    expect(hasContent).toBeTruthy();
  });

  test("Cron validation rejects invalid expressions via API", async ({ apiContext }) => {
    const resp = await apiContext.post("/api/schedules", {
      data: {
        pipeline_name: "test",
        yaml_config: "pipeline:\n  name: test\n  steps: []",
        cron_expression: "not-a-cron",
      },
    });
    expect([400, 401, 422]).toContain(resp.status());
  });

  test("Templates page shows 5 templates", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/templates");
    await authenticatedPage.waitForSelector('[data-testid="template-card"]', { timeout: 10_000 });
    const count = await authenticatedPage.locator('[data-testid="template-card"]').count();
    expect(count).toBe(5);
  });

  test("Template fork button is present", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/templates");
    await authenticatedPage.waitForSelector('[data-testid="template-card"]', { timeout: 10_000 });
    const firstCard = authenticatedPage.locator('[data-testid="template-card"]').first();
    await expect(
      firstCard.locator('[data-testid="fork-template-btn"]').or(
        firstCard.locator("text=Use Template").or(firstCard.locator("text=Fork"))
      )
    ).toBeVisible();
  });

  test("Completed run shows download button", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/runs");
    const successRun = authenticatedPage.locator('[data-status="success"]').first();
    const count = await successRun.count();

    if (count > 0) {
      await successRun.click();
      await expect(
        authenticatedPage.locator('[data-testid="download-output-btn"]').or(
          authenticatedPage.locator("text=Download Output").or(authenticatedPage.locator("text=Download CSV"))
        )
      ).toBeVisible();
    } else {
      test.skip(true, "No completed runs available");
    }
  });
});
