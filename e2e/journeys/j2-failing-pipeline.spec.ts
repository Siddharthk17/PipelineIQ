import { expect, test } from "@playwright/test";
import {
  API,
  UI,
  registerUser,
  uiLogin,
  runPipeline,
  simpleYaml,
  uploadCsv,
  waitForRun,
} from "./helpers";

/**
 * J2: a pipeline that fails halfway surfaces a visible, understandable
 * error; retry after fixing the config works; partial output is handled.
 */
test("J2 failing pipeline shows error, retry succeeds", async ({ page, request }) => {
  test.setTimeout(240_000);
  const user = await registerUser(request, "j2");
  const headers = { Authorization: `Bearer ${user.token}` };

  const fileId = await uploadCsv(
    request, user.token, "j2.csv", "id,region,amount\n1,North,100\n2,South,200\n",
  );

  // Break the pipeline at submit time: filter on a column that does not
  // exist. The API refuses it with a 400 and a precise message.
  const badYaml = [
    "pipeline:",
    `  name: j2_broken_${Date.now().toString(36)}`,
    "  steps:",
    "    - name: src",
    "      type: load",
    `      file_id: ${fileId}`,
    "    - name: bad",
    "      type: filter",
    "      input: src",
    "      column: no_such_column",
    "      operator: equals",
    "      value: 1",
  ].join("\n");
  const rejected = await request.post(`${API}/api/v1/pipelines/run`, {
    data: { yaml_config: badYaml },
    headers,
  });
  expect(rejected.status()).toBe(400);
  expect(await rejected.text()).toContain("no_such_column");

  // Break the pipeline at runtime: passes validation (syntax is fine),
  // fails in DuckDB on a nonexistent column.
  const runtimeBadYaml = [
    "pipeline:",
    `  name: j2_runtime_${Date.now().toString(36)}`,
    "  steps:",
    "    - name: src",
    "      type: load",
    `      file_id: ${fileId}`,
    "    - name: boom",
    "      type: sql",
    "      input: src",
    "      query: \"SELECT j2_intentional_boom FROM {{input}}\"",
    "    - name: out",
    "      type: save",
    "      input: boom",
    "      filename: j2_never.csv",
  ].join("\n");
  const badRun = await runPipeline(request, user.token, runtimeBadYaml);
  const badStatus = await waitForRun(request, user.token, badRun);
  expect(badStatus).toBe("FAILED");

  // The failed run is visible in the UI with an error.
  await uiLogin(page, user.email, user.password);
  await page.goto(`${UI}/runs/${badRun}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByText(/failed/i).first()).toBeVisible({ timeout: 30_000 });
  // Error message names the missing column.
  await expect(page.getByText(/j2_intentional_boom/i).first()).toBeVisible({ timeout: 30_000 });

  // Fix the config and rerun: green path works.
  const goodRun = await runPipeline(
    request, user.token, simpleYaml(`j2_fixed_${Date.now().toString(36)}`, fileId, "j2_out.csv"),
  );
  expect(await waitForRun(request, user.token, goodRun)).toBe("COMPLETED");

  // Failed run has an error message via the API.
  const detail = await (
    await request.get(`${API}/api/v1/pipelines/${badRun}`, { headers })
  ).json();
  expect(detail.error_message).toBeTruthy();
});
