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

test.describe("File Upload and Profiling", () => {
  test("Upload CSV via API and see it in files list", async ({ page }) => {
    await login(page);

    const csvContent = [
      "customer_id,region,amount,status,order_date",
      "1,North,150,completed,2024-01-15",
      "2,South,320,completed,2024-01-16",
    ].join("\n");

    const uploadResp = await page.request.post(`${apiUrl}/api/files/upload`, {
      multipart: { file: { name: "sample_orders.csv", mimeType: "text/csv", buffer: Buffer.from(csvContent) } },
    });
    expect(uploadResp.ok()).toBeTruthy();

    await page.goto(`${baseUrl}/files`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="file-list"]', { timeout: 10_000 });
    await expect(page.locator("text=sample_orders.csv")).toBeVisible({ timeout: 10_000 });
  });

  test("Uploaded file detail page shows profiling status", async ({ page }) => {
    await login(page);

    const csvContent = "id,name,value\n1,Alice,100\n2,Bob,200";
    const uploadResp = await page.request.post(`${apiUrl}/api/files/upload`, {
      multipart: { file: { name: "profiling_test.csv", mimeType: "text/csv", buffer: Buffer.from(csvContent) } },
    });
    const { id: fileId } = await uploadResp.json();

    await page.goto(`${baseUrl}/files/${fileId}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("profile-status-complete")).toBeVisible({ timeout: 15_000 });
  });

  test("File upload shows error for invalid file", async ({ page }) => {
    await login(page);
    await page.goto(`${baseUrl}/files/nonexistent-id`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("upload-error")).toBeVisible({ timeout: 10_000 });
  });
});
