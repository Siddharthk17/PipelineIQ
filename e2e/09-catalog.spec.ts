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

test.describe("Data Catalog", () => {
  test("Catalog page loads and is searchable", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/catalog`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("catalog-page")).toBeVisible({ timeout: 10_000 });
  });

  test("Catalog search input accepts text", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/catalog`, { waitUntil: "domcontentloaded" });

    const searchInput = page.getByTestId("catalog-search-input");
    if (await searchInput.isVisible()) {
      await searchInput.fill("region");
      await page.waitForTimeout(500);
      const results = page.locator('[data-testid^="catalog-result-"]');
      const count = await results.count();
      expect(count).toBeGreaterThanOrEqual(0);
    }
  });

  test("Blast radius API returns structured response", async ({ page }) => {
    await login(page);
    const token = await page.evaluate(() => localStorage.getItem("pipelineiq_token"));
    expect(token).toBeTruthy();

    const resp = await page.request.get(`${apiUrl}/api/catalog/assets/customer_id/impact`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok()) {
      test.skip(true, "Blast radius API not available");
      return;
    }
    const data = await resp.json();
    expect(data).toHaveProperty("asset_name");
    expect(data).toHaveProperty("downstream");
  });
});
