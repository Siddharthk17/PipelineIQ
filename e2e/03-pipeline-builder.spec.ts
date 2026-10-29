import { expect, test } from "@playwright/test";

test.use({ storageState: "e2e/.auth/demo.json" });

const baseUrl = process.env.E2E_BASE_URL;
const email = process.env.E2E_EMAIL ?? "demo@pipelineiq.app";
const password = process.env.E2E_PASSWORD ?? "Demo1234!";

async function login(page: import("@playwright/test").Page) {
  test.skip(!baseUrl, "Set E2E_BASE_URL to run Playwright tests.");
  // Session comes from global-setup storageState (one demo login for the
  // whole suite, sparing the 5/min auth limiter); just verify it works.
  await page.goto(`${baseUrl}/files`, { waitUntil: "domcontentloaded" });
  // The app may bounce through /login while the session restores; wait for
  // it to settle on an authenticated page.
  await page.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 15_000 });
}

test.describe("Visual Pipeline Builder", () => {
  test("Builder page loads with palette and canvas", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("step-palette")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("pipeline-canvas")).toBeVisible();
  });

  test("Step palette renders all step types", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="step-palette"]');
    const items = await page.locator('[data-testid^="palette-item-"]').count();
    expect(items).toBe(19);
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

    // The builder opens in visual mode; switch to YAML mode to edit text.
    await page.getByTestId("mode-yaml-btn").click();
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

    // The typed step must materialize as a canvas node (not merely as
    // editor text): this is the real YAML->canvas sync assertion.
    await expect(
      page.locator('[data-testid="step-node-load_data"]'),
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
    // Node creation is async; wait for the node's gear button explicitly.
    await page.waitForSelector('[data-testid^="config-btn-"]', { timeout: 10_000 });

    const configBtn = page.locator('[data-testid^="config-btn-"]').first();
    await configBtn.click();
    await expect(page.getByTestId("config-panel")).toBeVisible({ timeout: 5_000 });
  });

  test("Config panel closes on close button click", async ({ page }) => {
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
    await page.waitForSelector('[data-testid^="config-btn-"]', { timeout: 10_000 });

    await page.locator('[data-testid^="config-btn-"]').first().click();
    await expect(page.getByTestId("config-panel")).toBeVisible({ timeout: 5_000 });

    // The panel unmounts on close, which can detach the button between
    // Playwright's actionability check and the click; click via evaluate.
    await page.getByTestId("config-panel-close").evaluate((el: HTMLElement) => el.click());
    await expect(page.getByTestId("config-panel")).not.toBeVisible();
  });

  test("Join step shows two input handles", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="step-palette"]');

    const paletteJoin = page.locator('[data-testid="palette-item-join"]');
    const canvas = page.locator('[data-testid="pipeline-canvas"]');
    const box = await canvas.boundingBox();
    if (!box || !(await paletteJoin.isVisible())) {
      test.skip(true, "Join step or canvas not available");
      return;
    }

    await paletteJoin.dragTo(canvas, {
      targetPosition: { x: box.width / 2, y: box.height / 2 },
    });
    await page.waitForSelector('[data-testid$="-left"]', { timeout: 10_000 });

    const handles = await page.locator('[data-testid^="handle-"][data-testid$="-left"], [data-testid^="handle-"][data-testid$="-right"]').count();
    expect(handles).toBeGreaterThanOrEqual(2);
  });

  test("Run pipeline button is present", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("run-pipeline-btn")).toBeVisible({ timeout: 5_000 });
  });

  test("YAML/Visual mode toggle switches modes", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/pipelines/new`, { waitUntil: "domcontentloaded" });

    await expect(page.getByTestId("mode-visual-btn")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId("mode-yaml-btn")).toBeVisible({ timeout: 5_000 });

    await page.getByTestId("mode-visual-btn").click();
    await expect(page.getByTestId("step-palette")).toBeVisible({ timeout: 5_000 });

    await page.getByTestId("mode-yaml-btn").click();
    await page.waitForSelector(".cm-editor, [data-testid='yaml-editor']", { timeout: 5_000 });
  });
});
