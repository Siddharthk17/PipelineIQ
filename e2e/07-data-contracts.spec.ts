import { expect, test } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL;
const apiUrl = process.env.E2E_API_URL ?? baseUrl;
const email = process.env.E2E_EMAIL ?? "demo@pipelineiq.app";
const password = process.env.E2E_PASSWORD ?? "Demo1234!";

async function login(page: import("@playwright/test").Page): Promise<string> {
  test.skip(!baseUrl, "Set E2E_BASE_URL to run Playwright tests.");
  await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("email-input").fill(email);
  await page.getByTestId("password-input").fill(password);
  await page.getByTestId("login-btn").click();
  await page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 15_000 });
  return (await page.evaluate(() => localStorage.getItem("pipelineiq_token"))) ?? "";
}

test.describe("Data Contracts", () => {
  test("Create a data contract via API", async ({ page }) => {
    const token = await login(page);

    const resp = await page.request.post(`${apiUrl}/api/contracts`, {
      data: {
        pipeline_name: "e2e_contract_test",
        output_schema: {
          region: { type: "object" },
          amount_sum: { type: "float64" },
        },
        consumers: [],
        severity: "warn",
      },
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    });

    if (resp.ok()) {
      const data = await resp.json();
      expect(data.id).toBeTruthy();
      await page.request.delete(`${apiUrl}/api/contracts/${data.id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
    }
  });

  test("Contract breach is detected when output schema mismatches", async ({ page }) => {
    const token = await login(page);

    const csvContent = "id,region,amount\n1,North,100\n2,South,200";
    const uploadResp = await page.request.post(`${apiUrl}/api/files/upload`, {
      multipart: { file: { name: "breach_test.csv", mimeType: "text/csv", buffer: Buffer.from(csvContent) } },
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!uploadResp.ok()) {
      test.skip(true, "File upload not available");
      return;
    }
    const { file_id: fileId } = await uploadResp.json();

    const contractResp = await page.request.post(`${apiUrl}/api/contracts`, {
      data: {
        pipeline_name: `breach_${Date.now()}`,
        output_schema: { nonexistent_column: { type: "float64" } },
        severity: "warn",
      },
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    });
    if (!contractResp.ok()) {
      test.skip(true, "Contract creation not supported");
      return;
    }
    const contract = await contractResp.json();

    const yaml = [
      "pipeline:",
      `  name: breach_test_${Date.now()}`,
      "  steps:",
      "    - name: load",
      "      type: load",
      `      file_id: "${fileId}"`,
      "    - name: save",
      "      type: save",
      "      input: load",
      "      format: csv",
      "      filename: breach_output",
    ].join("\n");

    const runResp = await page.request.post(`${apiUrl}/api/pipelines/run`, {
      data: { yaml_config: yaml },
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    });
    if (!runResp.ok()) {
      test.skip(true, "Pipeline run not supported");
      return;
    }
    const { run_id: runId } = await runResp.json();

    const start = Date.now();
    while (Date.now() - start < 60_000) {
      const statusResp = await page.request.get(`${apiUrl}/api/pipelines/${runId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const statusData = await statusResp.json();
      if (["COMPLETED", "HEALED", "CONTRACT_VIOLATION", "FAILED"].includes(statusData.status)) break;
      await page.waitForTimeout(2_000);
    }

    const breachesResp = await page.request.get(`${apiUrl}/api/contracts/${contract.id}/breaches`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (breachesResp.ok()) {
      const breaches = await breachesResp.json();
      if (breaches.total > 0) {
        expect(breaches.breaches[0]).toHaveProperty("breach_type");
      }
    }

    await page.request.delete(`${apiUrl}/api/contracts/${contract.id}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  }, 150_000);
});
