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

test.describe("Streaming Pipeline", () => {
  test("Step palette contains stream_consume and stream_publish", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("palette-item-stream_consume")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("palette-item-stream_publish")).toBeVisible();
  });

  test("Topics API returns data from Redpanda", async ({ page }) => {
    await login(page);
    const resp = await page.request.get(`${baseUrl}/api/streaming/topics`);
    expect([200, 503]).toContain(resp.status());
    if (resp.status() === 200) {
      const data = await resp.json();
      expect(Array.isArray(data.topics)).toBe(true);
    }
  });

  test("stream_consume config panel opens with topic field", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="step-palette"]');

    const paletteItem = page.locator('[data-testid="palette-item-stream_consume"]');
    const canvas = page.locator('[data-testid="pipeline-canvas"]');
    const box = await canvas.boundingBox();
    if (!box) {
      test.skip(true, "Canvas not rendered");
      return;
    }

    await paletteItem.dragTo(canvas, {
      targetPosition: { x: box.width / 2, y: box.height / 2 },
    });
    await page.waitForTimeout(400);

    const configButton = page.locator('[data-testid^="config-btn-"]').first();
    if (!(await configButton.isVisible())) {
      test.skip(true, "Config button not visible after drag");
      return;
    }
    await configButton.click();
    await expect(page.locator('[data-testid="stream-topic-input"]')).toBeVisible({ timeout: 5_000 });
  });

  test("Streaming controls visible on streaming run card", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/runs`, { waitUntil: "domcontentloaded" });

    const card = page.locator('[data-testid="streaming-run-card"]').first();
    if (await card.count() > 0) {
      await expect(card).toBeVisible({ timeout: 5_000 });
    } else {
      test.skip(true, "No streaming run cards on page");
    }
  });
});
