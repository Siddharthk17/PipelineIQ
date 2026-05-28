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
