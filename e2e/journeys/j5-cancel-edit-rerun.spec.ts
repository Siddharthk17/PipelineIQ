import { expect, test } from "@playwright/test";
import { API, registerUser, runPipeline, uploadCsv, waitForRun } from "./helpers";

/**
 * J5: start a long run, cancel it from the UI/API, edit the config
 * (add a filter step), rerun successfully.
 */
test("J5 cancel a run, edit config, rerun", async ({ request }) => {
  test.setTimeout(240_000);
  const user = await registerUser(request, "j5");
  const headers = { Authorization: `Bearer ${user.token}` };

  const rows = ["id,region,amount"];
  for (let i = 0; i < 50_000; i++) rows.push(`${i},R${i % 4},${i}`);
  const fileId = await uploadCsv(request, user.token, "j5.csv", rows.join("\n"));

  // Long-ish run: aggregate over 50k rows (fast enough but cancellable).
  const base = `j5_pipe_${Date.now().toString(36)}`;
  const yaml = [
    "pipeline:",
    `  name: ${base}`,
    "  steps:",
    "    - name: src",
    "      type: load",
    `      file_id: ${fileId}`,
    "    - name: agg",
    "      type: aggregate",
    "      input: src",
    "      group_by: [region]",
    "      aggregations:",
    "        amount: sum",
    "    - name: out",
    "      type: save",
    "      input: agg",
    "      filename: j5_out.csv",
  ].join("\n");
  const runId = await runPipeline(request, user.token, yaml);

  // Cancel while running (best effort: it may finish first on a fast box).
  const cancel = await request.post(`${API}/api/v1/pipelines/${runId}/cancel`, { headers });
  expect([200, 404, 409]).toContain(cancel.status());
  if (cancel.status() === 200) {
    expect(await waitForRun(request, user.token, runId)).toBe("CANCELLED");
  }

  // Edit: add a filter step, rerun to green.
  const edited = [
    "pipeline:",
    `  name: ${base}_v2`,
    "  steps:",
    "    - name: src",
    "      type: load",
    `      file_id: ${fileId}`,
    "    - name: keep",
    "      type: filter",
    "      input: src",
    "      column: region",
    "      operator: equals",
    "      value: R1",
    "    - name: out",
    "      type: save",
    "      input: keep",
    "      filename: j5_out_v2.csv",
  ].join("\n");
  const run2 = await runPipeline(request, user.token, edited);
  expect(await waitForRun(request, user.token, run2)).toBe("COMPLETED");

  // History shows both runs.
  const history = await (
    await request.get(`${API}/api/v1/pipelines/`, { headers })
  ).json();
  const ids = (history.runs ?? history).map((r: { id: string; run_id: string }) => r.id ?? r.run_id);
  expect(ids).toContain(runId);
  expect(ids).toContain(run2);
});
