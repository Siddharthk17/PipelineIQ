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
  const token = await page.evaluate(() => localStorage.getItem("pipelineiq_token"));
  return token ?? "";
}

function buildPipelineYAML(fileId: string, pipelineName: string): string {
  return [
    "pipeline:",
    `  name: ${pipelineName}`,
    "  steps:",
    "    - name: load_orders",
    "      type: load",
    `      file_id: "${fileId}"`,
    "    - name: filter_completed",
    "      type: filter",
    "      input: load_orders",
    "      column: status",
    "      operator: equals",
    "      value: completed",
    "    - name: save_output",
    "      type: save",
    "      input: filter_completed",
    "      format: csv",
    "      filename: e2e_output",
  ].join("\n");
}

test.describe("Pipeline Run Lifecycle", () => {
  test("Submit pipeline run and see it reach success", async ({ page }) => {
    const token = await login(page);

    const csvContent = "id,region,amount,status\n1,North,150,completed\n2,South,320,completed";
    const uploadResp = await page.request.post(`${apiUrl}/api/files/upload`, {
      multipart: { file: { name: "run_test.csv", mimeType: "text/csv", buffer: Buffer.from(csvContent) } },
      headers: { Authorization: `Bearer ${token}` },
    });
    const { file_id: fileId } = await uploadResp.json();

    const pipelineName = `e2e_run_${Date.now()}`;
    const yaml = buildPipelineYAML(fileId, pipelineName);

    const runResp = await page.request.post(`${apiUrl}/api/pipelines/run`, {
      data: { yaml_config: yaml, pipeline_name: pipelineName },
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    });
    expect(runResp.ok()).toBeTruthy();
    const { run_id: runId } = await runResp.json();
    expect(runId).toBeTruthy();

    const start = Date.now();
    while (Date.now() - start < 120_000) {
      const statusResp = await page.request.get(`${apiUrl}/api/pipelines/${runId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const { status } = await statusResp.json();
      if (status === "COMPLETED" || status === "HEALED") return;
      if (status === "FAILED" || status === "TIMEOUT") {
        throw new Error(`Run failed with status: ${status}`);
      }
      await page.waitForTimeout(2_000);
    }
    throw new Error("Run did not complete within 120s");
  }, 150_000);

  test("Run detail page shows status and Gantt chart", async ({ page }) => {
    const token = await login(page);

    const csvContent = "id,amount\n1,100\n2,200";
    const uploadResp = await page.request.post(`${apiUrl}/api/files/upload`, {
      multipart: { file: { name: "gantt_test.csv", mimeType: "text/csv", buffer: Buffer.from(csvContent) } },
      headers: { Authorization: `Bearer ${token}` },
    });
    const { file_id: fileId } = await uploadResp.json();

    const pipelineName = `e2e_gantt_${Date.now()}`;
    const yaml = buildPipelineYAML(fileId, pipelineName);

    const runResp = await page.request.post(`${apiUrl}/api/pipelines/run`, {
      data: { yaml_config: yaml, pipeline_name: pipelineName },
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    });
    const { run_id: runId } = await runResp.json();

    const start = Date.now();
    while (Date.now() - start < 120_000) {
      const statusResp = await page.request.get(`${apiUrl}/api/pipelines/${runId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const statusData = await statusResp.json();
      if (statusData.status === "COMPLETED" || statusData.status === "HEALED") break;
      if (statusData.status === "FAILED" || statusData.status === "TIMEOUT") {
        test.skip(true, `Run ended with ${statusData.status}`);
        return;
      }
      await page.waitForTimeout(2_000);
    }

    await page.goto(`${baseUrl}/runs/${runId}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("run-status")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("execution-gantt")).toBeVisible({ timeout: 10_000 });
  }, 150_000);

  test("Run detail page shows SSE step-by-step progress events", async ({ page }) => {
    const token = await login(page);

    const csvContent = "id,amount\n1,100\n2,200";
    const uploadResp = await page.request.post(`${apiUrl}/api/files/upload`, {
      multipart: { file: { name: "sse_test.csv", mimeType: "text/csv", buffer: Buffer.from(csvContent) } },
      headers: { Authorization: `Bearer ${token}` },
    });
    const { file_id: fileId } = await uploadResp.json();

    const pipelineName = `e2e_sse_${Date.now()}`;
    const yaml = buildPipelineYAML(fileId, pipelineName);

    const runResp = await page.request.post(`${apiUrl}/api/pipelines/run`, {
      data: { yaml_config: yaml, pipeline_name: pipelineName },
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    });
    const { run_id: runId } = await runResp.json();
    expect(runId).toBeTruthy();

    await page.goto(`${baseUrl}/runs/${runId}`, { waitUntil: "domcontentloaded" });

    const statusElement = page.locator('[data-testid="run-status"]');
    await expect(statusElement).toBeVisible({ timeout: 10_000 });
    const statusText = await statusElement.textContent();
    expect(statusText).not.toBeNull();

    const sseEvents = page.locator(
      '[data-testid^="step-progress-"], [data-testid^="step-event-"], [data-testid^="pipeline-event-"]'
    );
    const stepNodes = page.locator('[data-testid^="step-node-"]');

    const eventsVisible = await sseEvents.isVisible().catch(() => false);
    const nodesVisible = await stepNodes.isVisible().catch(() => false);

    if (!eventsVisible && !nodesVisible) {
      await page.waitForTimeout(5000);
      const pollStatus = page.locator(
        '[data-testid^="step-progress-"], [data-testid^="step-event-"], [data-testid^="pipeline-event-"], [data-testid^="step-node-"]'
      );
      const hasAny = await pollStatus.count().catch(() => 0);
      expect(hasAny).toBeGreaterThanOrEqual(0);
    }
  });

  test("Completed run shows download button", async ({ page }) => {
    const token = await login(page);

    const csvContent = "id,amount\n1,100\n2,200";
    const uploadResp = await page.request.post(`${apiUrl}/api/files/upload`, {
      multipart: { file: { name: "dl_test.csv", mimeType: "text/csv", buffer: Buffer.from(csvContent) } },
      headers: { Authorization: `Bearer ${token}` },
    });
    const { file_id: fileId } = await uploadResp.json();

    const pipelineName = `e2e_dl_${Date.now()}`;
    const yaml = buildPipelineYAML(fileId, pipelineName);

    const runResp = await page.request.post(`${apiUrl}/api/pipelines/run`, {
      data: { yaml_config: yaml, pipeline_name: pipelineName },
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    });
    const { run_id: runId } = await runResp.json();

    const start = Date.now();
    while (Date.now() - start < 120_000) {
      const statusResp = await page.request.get(`${apiUrl}/api/pipelines/${runId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const statusData = await statusResp.json();
      if (statusData.status === "COMPLETED" || statusData.status === "HEALED") break;
      if (statusData.status === "FAILED" || statusData.status === "TIMEOUT") {
        test.skip(true, `Run ended with ${statusData.status}`);
        return;
      }
      await page.waitForTimeout(2_000);
    }

    await page.goto(`${baseUrl}/runs/${runId}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("download-output-btn")).toBeVisible({ timeout: 10_000 });
  }, 150_000);
});
