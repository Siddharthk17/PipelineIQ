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

test.describe("Wasm Module Management", () => {
  test("Wasm modules page is accessible", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/wasm-modules`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("wasm-manager-page")).toBeVisible({ timeout: 10_000 });
  });

  test("Wasm upload zone is visible", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/wasm-modules`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("wasm-upload-zone")).toBeVisible({ timeout: 10_000 });
  });

  test("Upload invalid file shows error", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/wasm-modules`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="wasm-upload-zone"]');

    const [fileChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      page.click('[data-testid="wasm-upload-zone"]'),
    ]);
    await fileChooser.setFiles({
      name: "not_a_module.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("this is not wasm"),
    });

    await expect(page.getByTestId("upload-error")).toBeVisible({ timeout: 5_000 });
  });
});
