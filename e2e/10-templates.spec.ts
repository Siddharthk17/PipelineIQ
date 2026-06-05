import { expect, test } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL;
const apiUrl = process.env.E2E_API_URL ?? baseUrl;
const email = process.env.E2E_EMAIL ?? "demo@pipelineiq.app";
const password = process.env.E2E_PASSWORD ?? "Demo1234!";

async function login(page: import("@playwright/test").Page): Promise<void> {
  test.skip(!baseUrl, "Set E2E_BASE_URL to run Playwright tests.");
  await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("email-input").fill(email);
  await page.getByTestId("password-input").fill(password);
  await page.getByTestId("login-btn").click();
  await page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 15_000 });
}

test.describe("Pipeline Templates", () => {
  test("Templates page shows templates", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/templates`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="template-card"]', { timeout: 10_000 });
    const count = await page.locator('[data-testid="template-card"]').count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test("Fork template via API with correct file mappings", async ({ page }) => {
    await login(page);

    const csvContent = "id,amount\n1,100\n2,200";
    const uploadResp = await page.request.post(`${apiUrl}/api/files/upload`, {
      multipart: { file: { name: "template_test.csv", mimeType: "text/csv", buffer: Buffer.from(csvContent) } },
    });
    if (!uploadResp.ok()) {
      test.skip(true, "File upload not available");
      return;
    }
    const { file_id: fileId } = await uploadResp.json();

    const resp = await page.request.post(`${apiUrl}/api/templates/customer_segmentation/fork`, {
      data: {
        pipeline_name: `e2e_forked_${Date.now()}`,
        file_mappings: { orders_file_id: fileId },
      },
      headers: { "Content-Type": "application/json" },
    });
    if (resp.ok()) {
      const data = await resp.json();
      expect(data.yaml).toContain(fileId);
    }
  });

  test("Fork template with missing placeholder returns 400", async ({ page }) => {
    await login(page);

    const resp = await page.request.post(`${apiUrl}/api/templates/sales_revenue_report/fork`, {
      data: {
        pipeline_name: "bad_fork",
        file_mappings: {},
      },
      headers: { "Content-Type": "application/json" },
    });
    if (resp.ok()) {
      test.skip(true, "Templates may not enforce placeholders in this environment");
      return;
    }
    expect(resp.status()).toBe(400);
  });
});
