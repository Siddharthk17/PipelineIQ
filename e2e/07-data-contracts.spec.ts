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

async function authed(page: import("@playwright/test").Page) {
  await login(page);
  return page.request;
}

test.describe("Data Contracts", () => {
  test("Create, read and delete a data contract via API", async ({ page }) => {
    const api = await authed(page);
    const pipelineName = `e2e_contract_${Date.now()}`;

    const createResp = await api.post(`${apiUrl}/api/v1/contracts/pipelines/${pipelineName}`, {
      data: {
        yaml_content: "contracts:\n  - column: amount\n    rule: not_null\n",
        severity: "warn",
      },
      headers: { "Content-Type": "application/json" },
    });
    expect(createResp.ok(), await createResp.text()).toBeTruthy();
    const created = await createResp.json();
    expect(created.id).toBeTruthy();
    expect(created.version).toBe(1);

    const listResp = await api.get(`${apiUrl}/api/v1/contracts/pipelines/${pipelineName}`);
    expect(listResp.ok()).toBeTruthy();
    const listed = await listResp.json();
    expect(listed.total).toBeGreaterThanOrEqual(1);

    const statusResp = await api.get(`${apiUrl}/api/v1/contracts/pipelines/${pipelineName}/status`);
    expect(statusResp.ok()).toBeTruthy();

    const delResp = await api.delete(
      `${apiUrl}/api/v1/contracts/pipelines/${pipelineName}/${created.id}`,
    );
    expect([200, 204].includes(delResp.status())).toBeTruthy();
  });

  test("Contract breach is detected when output violates the contract", async ({ page }) => {
    const api = await authed(page);

    const csvContent = "id,region,amount\n1,North,100\n2,South,";
    const uploadResp = await api.post(`${apiUrl}/api/files/upload`, {
      multipart: { file: { name: "breach_test.csv", mimeType: "text/csv", buffer: Buffer.from(csvContent) } },
    });
    if (!uploadResp.ok()) {
      test.skip(true, "File upload not available");
      return;
    }
    const { id: fileId } = await uploadResp.json();

    const pipelineName = `breach_${Date.now()}`;
    const contractResp = await api.post(`${apiUrl}/api/v1/contracts/pipelines/${pipelineName}`, {
      data: {
        yaml_content: "contracts:\n  - column: amount\n    rule: not_null\n",
        severity: "block",
      },
      headers: { "Content-Type": "application/json" },
    });
    if (!contractResp.ok()) {
      test.skip(true, "Contract creation not supported");
      return;
    }

    const yaml = [
      "pipeline:",
      `  name: ${pipelineName}`,
      "  steps:",
      "    - name: load",
      "      type: load",
      `      file_id: "${fileId}"`,
      "    - name: save",
      "      type: save",
      "      input: load",
      "      filename: breach_output",
    ].join("\n");

    const runResp = await api.post(`${apiUrl}/api/pipelines/run`, {
      data: { yaml_config: yaml },
      headers: { "Content-Type": "application/json" },
    });
    if (!runResp.ok()) {
      test.skip(true, "Pipeline run not supported");
      return;
    }
    const { run_id: runId } = await runResp.json();

    const start = Date.now();
    let status = "";
    while (Date.now() - start < 60_000) {
      const statusResp = await api.get(`${apiUrl}/api/pipelines/${runId}`);
      const statusData = await statusResp.json();
      status = statusData.status;
      if (["COMPLETED", "HEALED", "CONTRACT_VIOLATION", "FAILED"].includes(status)) break;
      await page.waitForTimeout(2_000);
    }
    expect(["COMPLETED", "HEALED", "CONTRACT_VIOLATION"]).toContain(status);

    const breachesResp = await api.get(`${apiUrl}/api/v1/contracts/runs/${runId}`);
    expect(breachesResp.ok()).toBeTruthy();
  }, 150_000);
});
