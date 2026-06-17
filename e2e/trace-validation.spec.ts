/**
 * E2E: OTel trace validation
 *
 * Validates that distributed trace context (trace_id, span_id) propagates
 * correctly from the Celery worker through the database into API responses.
 *
 * All tests skip gracefully when E2E_BASE_URL is unset so CI passes without
 * a live environment.
 */
import { expect, test } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL;
const email = process.env.E2E_EMAIL ?? "demo@pipelineiq.app";
const password = process.env.E2E_PASSWORD ?? "Demo1234!";

// Helper
async function login(page: import("@playwright/test").Page): Promise<void> {
  test.skip(!baseUrl, "Set E2E_BASE_URL to run trace validation tests.");

  await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("email-input").fill(email);
  await page.getByTestId("password-input").fill(password);
  await page.getByTestId("login-btn").click();
  await page.waitForURL((url) => !url.pathname.endsWith("/login"), {
    timeout: 20_000,
  });

}

// Suite 
test.describe("OTel trace field validation", () => {
  /**
   * The /timing endpoint is the primary surface where step-level trace IDs
   * are exposed. Every TimelineStep must carry the trace_id / span_id fields
   * (values may be null when OTel is disabled, but the keys must exist).
   */
  test("timing endpoint returns required trace fields on every step", async ({
    page,
  }) => {
    await login(page);

    // Fetch the 50 most recent runs to find a completed one
    const runsResp = await page.request.get(
      `${baseUrl}/api/v1/pipelines/runs?limit=50`,
    );
    expect(runsResp.ok()).toBeTruthy();
    const runs: Array<{ id: string; status: string }> = await runsResp.json();

    const completedRun = runs.find((r) => r.status === "COMPLETED");
    test.skip(!completedRun, "No COMPLETED run available; skipping trace validation.");

    const timingResp = await page.request.get(
      `${baseUrl}/api/v1/pipelines/${completedRun!.id}/timing`,
    );
    expect(timingResp.ok()).toBeTruthy();

    const timing = await timingResp.json();

    // Top-level schema
    expect(timing).toHaveProperty("run_id");
    expect(timing).toHaveProperty("steps");
    expect(timing).toHaveProperty("total_duration_ms");
    expect(typeof timing.run_id).toBe("string");
    expect(Array.isArray(timing.steps)).toBe(true);

    // Per-step trace fields
    for (const step of timing.steps as Record<string, unknown>[]) {
      expect(step, `step ${step.step_name} must have trace_id key`).toHaveProperty(
        "trace_id",
      );
      expect(step, `step ${step.step_name} must have span_id key`).toHaveProperty(
        "span_id",
      );
      // trace_id, when present, must be a 32-char hex string
      if (step.trace_id !== null) {
        expect(typeof step.trace_id).toBe("string");
        expect((step.trace_id as string).length).toBe(32);
        expect((step.trace_id as string)).toMatch(/^[0-9a-f]{32}$/);
      }
      // span_id, when present, must be a 16-char hex string
      if (step.span_id !== null) {
        expect(typeof step.span_id).toBe("string");
        expect((step.span_id as string).length).toBe(16);
        expect((step.span_id as string)).toMatch(/^[0-9a-f]{16}$/);
      }
    }
  });

  /**
   * The runs list endpoint must include trace_id on the PipelineRun itself,
   * and the nested step_results must each carry trace_id / span_id keys.
   */
  test("runs list embeds trace_id on step_results", async ({ page }) => {
    await login(page);

    const runsResp = await page.request.get(
      `${baseUrl}/api/v1/pipelines/runs?limit=10`,
    );
    expect(runsResp.ok()).toBeTruthy();
    const runs: Array<{ step_results: unknown[] }> = await runsResp.json();

    const runWithSteps = runs.find(
      (r) => Array.isArray(r.step_results) && r.step_results.length > 0,
    );
    test.skip(
      !runWithSteps,
      "No runs with step_results available; skipping step trace validation.",
    );

    for (const step of runWithSteps!.step_results as Record<string, unknown>[]) {
      // The field must be present (value may be null)
      expect(
        Object.prototype.hasOwnProperty.call(step, "trace_id"),
        `step ${step.step_name} is missing trace_id key`,
      ).toBe(true);
    }
  });

  /**
   * When OTel is enabled and a run completes, at least one step should carry
   * a non-null trace_id. If every trace_id is null the OTel pipeline is broken.
   *
   * This test is advisory (warn-only) — it only fails if the environment
   * explicitly declares OTEL_ENABLED=true via E2E_ASSERT_TRACES.
   */
  test("completed run has at least one non-null trace_id when OTel is enabled", async ({
    page,
  }) => {
    const assertTraces = process.env.E2E_ASSERT_TRACES === "true";
    test.skip(
      !assertTraces,
      "Set E2E_ASSERT_TRACES=true to enforce non-null trace IDs.",
    );

    await login(page);

    const runsResp = await page.request.get(
      `${baseUrl}/api/v1/pipelines/runs?limit=50`,
    );
    const runs: Array<{ id: string; status: string }> = await runsResp.json();
    const completedRun = runs.find((r) => r.status === "COMPLETED");
    test.skip(!completedRun, "No COMPLETED run to check trace IDs against.");

    const timingResp = await page.request.get(
      `${baseUrl}/api/v1/pipelines/${completedRun!.id}/timing`,
    );
    const timing = await timingResp.json();
    const steps: Array<{ trace_id: string | null }> = timing.steps ?? [];

    const hasTrace = steps.some((s) => s.trace_id !== null);
    expect(
      hasTrace,
      "OTel is asserted enabled but all step trace_ids are null. " +
        "Check that OTEL_ENABLED=true and the Jaeger exporter is reachable.",
    ).toBe(true);
  });

  /**
   * The Execution Timeline widget must render the "Trace" column header in the
   * workspace so users can see trace IDs in the UI.
   */
  test("Execution Timeline widget shows Trace column header in workspace", async ({
    page,
  }) => {
    await login(page);

    await page.goto(`${baseUrl}/workspace`, { waitUntil: "domcontentloaded" });

    // The column header "Trace" is rendered inside the timeline widget table
    const traceHeader = page.locator("text=Trace").first();
    await expect(traceHeader).toBeVisible({ timeout: 15_000 });
  });
});
