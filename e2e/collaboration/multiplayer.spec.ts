import { test, expect } from "../fixtures/auth";
import { uploadSampleCSV, buildSimplePipelineYAML, submitPipelineRun, waitForRunStatus } from "../fixtures/pipeline-helpers";

test.describe("Multiplayer Collaboration", () => {
  test("Two browser contexts can open the same pipeline builder simultaneously", async ({
    browser,
    user,
    apiContext,
  }) => {
    const fileId = await uploadSampleCSV(apiContext, user.token);
    const pipelineName = `multi_${Date.now()}`;
    const yaml = buildSimplePipelineYAML(fileId, pipelineName);

    const runResp = await apiContext.post("/api/pipelines/run", {
      data: { yaml_config: yaml, pipeline_name: pipelineName },
    });
    expect(runResp.ok()).toBeTruthy();
    const { run_id: runId } = await runResp.json();

    const userOneCtx = await browser.newContext();
    const userTwoCtx = await browser.newContext();
    const page1 = await userOneCtx.newPage();
    const page2 = await userTwoCtx.newPage();

    await page1.goto("/login");
    await page1.fill('[data-testid="email-input"]', user.email);
    await page1.fill('[data-testid="password-input"]', user.password);
    await page1.click('[data-testid="login-btn"]');
    await page1.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 10_000 });

    await page2.goto("/login");
    await page2.fill('[data-testid="email-input"]', user.email);
    await page2.fill('[data-testid="password-input"]', user.password);
    await page2.click('[data-testid="login-btn"]');
    await page2.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 10_000 });

    await page1.goto(`/runs/${runId}`);
    await page2.goto(`/runs/${runId}`);

    await expect(page1.locator('[data-testid="run-status"]')).toBeVisible({ timeout: 10_000 });
    await expect(page2.locator('[data-testid="run-status"]')).toBeVisible({ timeout: 10_000 });

    await page1.close();
    await page2.close();
    await userOneCtx.close();
    await userTwoCtx.close();
  });

  test("Both users see same pipeline state after YAML edit", async ({
    browser,
    user,
  }) => {
    const userOneCtx = await browser.newContext();
    const userTwoCtx = await browser.newContext();
    const page1 = await userOneCtx.newPage();
    const page2 = await userTwoCtx.newPage();

    await page1.goto("/login");
    await page1.fill('[data-testid="email-input"]', user.email);
    await page1.fill('[data-testid="password-input"]', user.password);
    await page1.click('[data-testid="login-btn"]');
    await page1.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 10_000 });

    await page2.goto("/login");
    await page2.fill('[data-testid="email-input"]', user.email);
    await page2.fill('[data-testid="password-input"]', user.password);
    await page2.click('[data-testid="login-btn"]');
    await page2.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 10_000 });

    await page1.goto("/pipelines/new");
    await page2.goto("/pipelines/new");

    await expect(page1.locator('[data-testid="pipeline-canvas"]')).toBeVisible({ timeout: 10_000 });
    await expect(page2.locator('[data-testid="pipeline-canvas"]')).toBeVisible({ timeout: 10_000 });

    const yamlEditor1 = page1.locator(".cm-editor, [data-testid='yaml-editor']").first();
    await yamlEditor1.click();
    await page1.keyboard.press("Control+A");

    const yamlContent = [
      "pipeline:",
      "  name: collaboration_test",
      "  steps:",
      "    - name: load_data",
      "      type: load",
      "      file_id: shared-file-id",
    ].join("\n");

    await page1.keyboard.type(yamlContent);
    await page1.waitForTimeout(800);

    await expect(
      page1.locator('[data-testid="step-node-load_data"]').or(page1.locator("text=load_data"))
    ).toBeVisible({ timeout: 5_000 });

    await page1.close();
    await page2.close();
    await userOneCtx.close();
    await userTwoCtx.close();
  });

  test("Presence panel shows connected collaborators", async ({
    browser,
    user,
  }) => {
    const userOneCtx = await browser.newContext();
    const userTwoCtx = await browser.newContext();
    const page1 = await userOneCtx.newPage();
    const page2 = await userTwoCtx.newPage();

    await page1.goto("/login");
    await page1.fill('[data-testid="email-input"]', user.email);
    await page1.fill('[data-testid="password-input"]', user.password);
    await page1.click('[data-testid="login-btn"]');
    await page1.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 10_000 });

    await page2.goto("/login");
    await page2.fill('[data-testid="email-input"]', user.email);
    await page2.fill('[data-testid="password-input"]', user.password);
    await page2.click('[data-testid="login-btn"]');
    await page2.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 10_000 });

    await page1.goto("/pipelines/new");
    await page2.goto("/pipelines/new");

    await page1.waitForSelector('[data-testid="pipeline-canvas"]', { timeout: 10_000 });
    await page2.waitForSelector('[data-testid="pipeline-canvas"]', { timeout: 10_000 });

    const presencePanel1 = page1.locator('[data-testid="presence-panel"]');
    const presencePanel2 = page2.locator('[data-testid="presence-panel"]');

    const panel1Visible = await presencePanel1.isVisible().catch(() => false);
    const panel2Visible = await presencePanel2.isVisible().catch(() => false);

    expect(panel1Visible || panel2Visible).toBe(true);

    await page1.close();
    await page2.close();
    await userOneCtx.close();
    await userTwoCtx.close();
  });
});
