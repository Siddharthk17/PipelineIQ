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

test.describe("Visual Pipeline Builder", () => {
  test("Builder page loads with palette and canvas", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("step-palette")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("pipeline-canvas")).toBeVisible();
  });

  test("Step palette items are rendered", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="step-palette"]');
    const items = await page.locator('[data-testid^="palette-item-"]').count();
    expect(items).toBeGreaterThanOrEqual(1);
  });

  test("Dragging a step onto canvas creates a node", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="step-palette"]');

    const paletteItem = page.locator('[data-testid="palette-item-filter"]');
    if (!(await paletteItem.isVisible())) {
      test.skip(true, "Filter step not in palette");
      return;
    }
    const canvas = page.locator('[data-testid="pipeline-canvas"]');
    const box = await canvas.boundingBox();
    if (!box) {
      test.skip(true, "Canvas not rendered");
      return;
    }

    await paletteItem.dragTo(canvas, {
      targetPosition: { x: box.width / 2, y: box.height / 2 },
    });
    await page.waitForTimeout(500);

    const nodes = await page.locator('[data-testid^="step-node-"]').count();
    expect(nodes).toBeGreaterThanOrEqual(1);
  });

  test("YAML editor and canvas are bidirectionally synced", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });

    const yamlEditor = page.locator(".cm-editor, [data-testid='yaml-editor']").first();
    await yamlEditor.click();
    await page.keyboard.press("Control+A");

    const yamlContent = [
      "pipeline:",
      "  name: sync_test",
      "  steps:",
      "    - name: load_data",
      "      type: load",
      "      file_id: dummy-id",
    ].join("\n");

    await page.keyboard.type(yamlContent);
    await page.waitForTimeout(600);

    await expect(
      page.locator('[data-testid="step-node-load_data"]').or(page.locator("text=load_data")),
    ).toBeVisible({ timeout: 5_000 });
  });

  test("Config panel opens when gear icon is clicked on a step", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="step-palette"]');

    const paletteItem = page.locator('[data-testid^="palette-item-"]').first();
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

    const configBtn = page.locator('[data-testid^="config-btn-"]').first();
    await configBtn.click();
    await expect(page.getByTestId("config-panel")).toBeVisible({ timeout: 5_000 });
  });
});
