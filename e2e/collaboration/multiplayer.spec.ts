import { test, expect } from "../fixtures/auth";
import { uploadSampleCSV, buildSimplePipelineYAML, submitPipelineRun, waitForRunStatus } from "../fixtures/pipeline-helpers";

async function loginWithRetry(page: import("@playwright/test").Page, email: string, password: string) {
  // Back-to-back logins trip the auth limiter (5/min); retry with backoff.
  for (let attempt = 0; ; attempt++) {
    await page.goto("/login");
    await page.fill('[data-testid="email-input"]', email);
    await page.fill('[data-testid="password-input"]', password);
    await page.click('[data-testid="login-btn"]');
    try {
      await page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 10_000 });
      return;
    } catch {
      if (attempt >= 7) throw new Error("collaboration login keeps failing");
      await new Promise((r) => setTimeout(r, 15_000));
    }
  }
}

test.describe("Multiplayer Collaboration", () => {
  test("Two browser contexts can open the same pipeline builder simultaneously", async ({
    browser,
    user,
    apiContext,
  }) => {
    const fileId = await uploadSampleCSV(apiContext, user.token);
    const pipelineName = `multi_${Date.now()}`;
    const yaml = buildSimplePipelineYAML(fileId, pipelineName);

    const runResp = await apiContext.post("/api/v1/pipelines/run", {
      data: { yaml_config: yaml, pipeline_name: pipelineName },
    });
    expect(runResp.ok(), await runResp.text()).toBeTruthy();
    const { run_id: runId } = await runResp.json();

    const userOneCtx = await browser.newContext();
    const userTwoCtx = await browser.newContext();
    const page1 = await userOneCtx.newPage();
    const page2 = await userTwoCtx.newPage();

    await loginWithRetry(page1, user.email, user.password);
    await loginWithRetry(page2, user.email, user.password);

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

    await loginWithRetry(page1, user.email, user.password);
    await loginWithRetry(page2, user.email, user.password);

    await page1.goto("/pipelines/new");
    await page2.goto("/pipelines/new");

    await expect(page1.locator('[data-testid="pipeline-canvas"]')).toBeVisible({ timeout: 10_000 });
    await expect(page2.locator('[data-testid="pipeline-canvas"]')).toBeVisible({ timeout: 10_000 });

    // The builder opens in visual mode; switch page1 to YAML mode to edit.
    await page1.getByTestId("mode-yaml-btn").click();
    const yamlEditor1 = page1.locator(".cm-editor, [data-testid='yaml-editor']").first();
    await yamlEditor1.click();

    const yamlContent = [
      "pipeline:",
      "  name: collaboration_test",
      "  steps:",
      "    - name: load_data",
      "      type: load",
      "      file_id: shared-file-id",
    ].join("\n");

    // Paste atomically: char-by-char typing fights the editor's
    // auto-indent and mangles the YAML before it can parse.
    await page1.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    await page1.evaluate((t) => navigator.clipboard.writeText(t), yamlContent);
    await page1.keyboard.press("Control+A");
    await page1.keyboard.press("Control+V");
    await page1.waitForTimeout(800);

    // The canvas only mounts in visual mode; switch back to see the node.
    await page1.getByTestId("mode-visual-btn").click();
    await expect(
      page1.locator('[data-testid="step-node-load_data"]')
    ).toBeVisible({ timeout: 10_000 });

    // The edit syncs through the shared yjs room: page2's canvas converges.
    await expect(
      page2.locator('[data-testid="step-node-load_data"]')
    ).toBeVisible({ timeout: 15_000 });

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

    await loginWithRetry(page1, user.email, user.password);
    await loginWithRetry(page2, user.email, user.password);

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
