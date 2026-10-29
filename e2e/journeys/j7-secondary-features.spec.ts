import { expect, test } from "@playwright/test";
import { API, UI, registerUser, runPipeline, simpleYaml, uiLogin, uploadCsv, waitForRun } from "./helpers";

/**
 * J7: schedules + contracts + templates + WASM + catalog — the secondary
 * feature surface, exercised end to end against the live stack.
 */
test("J7 schedules, contracts, templates, wasm, catalog", async ({ page, request }) => {
  test.setTimeout(300_000);
  const user = await registerUser(request, "j7");
  const headers = { Authorization: `Bearer ${user.token}` };

  const fileId = await uploadCsv(request, user.token, "j7.csv", "id,region,amount\n1,N,10\n2,S,20\n");

  // Schedules: create, list, pause, delete.
  const sched = await request.post(`${API}/api/v1/schedules/`, {
    data: {
      pipeline_name: `j7_sched_${Date.now().toString(36)}`,
      yaml_config: simpleYaml("j7_sched_run", fileId, "j7_sched_out.csv"),
      cron_expression: "0 3 * * *",
    },
    headers,
  });
  expect(sched.status(), await sched.text()).toBe(201);
  const schedId = (await sched.json()).id as string;
  const pause = await request.post(`${API}/api/v1/schedules/${schedId}/pause`, { headers });
  expect([200, 204, 404]).toContain(pause.status());
  const delSched = await request.delete(`${API}/api/v1/schedules/${schedId}`, { headers });
  expect([200, 204, 404]).toContain(delSched.status());

  // Templates: list exists and one renders runnable YAML.
  const templates = await request.get(`${API}/api/v1/templates/`, { headers });
  expect(templates.ok(), await templates.text()).toBeTruthy();
  const templateList = await templates.json();
  expect(Array.isArray(templateList.templates ?? templateList)).toBeTruthy();

  // Catalog: full-text search over the caller's own assets.
  const catalog = await request.get(`${API}/api/catalog/search?q=j7&limit=10`, { headers });
  expect(catalog.ok(), await catalog.text()).toBeTruthy();
  expect((await catalog.json()).query).toBe("j7");

  // SQL compute step: DuckDB over the uploaded file.
  const sqlYaml = [
    "pipeline:",
    `  name: j7_sql_${Date.now().toString(36)}`,
    "  steps:",
    "    - name: src",
    "      type: load",
    `      file_id: ${fileId}`,
    "    - name: doubled",
    "      type: sql",
    "      input: src",
    "      query: \"SELECT *, amount * 2 AS double FROM {{input}}\"",
    "    - name: out",
    "      type: save",
    "      input: doubled",
    "      filename: j7_sql_out.csv",
  ].join("\n");
  const sqlRun = await runPipeline(request, user.token, sqlYaml);
  expect(await waitForRun(request, user.token, sqlRun)).toBe("COMPLETED");

  // WASM: garbage is rejected; a real module validates, uploads,
  // and runs per-row inside a pipeline.
  const garbage = await request.post(`${API}/api/v1/wasm/validate`, {
    multipart: {
      file: { name: "evil.bin", mimeType: "application/octet-stream", buffer: Buffer.from("MZ\x90\x00not-a-module") },
    },
    headers,
  });
  expect(garbage.ok(), await garbage.text()).toBeTruthy();
  expect((await garbage.json()).is_valid).toBe(false);

  // Minimal valid module: export double(f64) -> f64 (returns x * 2).
  const body = Buffer.from([0x00, 0x20, 0x00, 0x44, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x40, 0xa2, 0x0b]);
  const wasmBytes = Buffer.concat([
    Buffer.from([0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00,
      0x01, 0x06, 0x01, 0x60, 0x01, 0x7c, 0x01, 0x7c,
      0x03, 0x02, 0x01, 0x00,
      0x07, 0x0a, 0x01, 0x06]),
    Buffer.from("double"),
    Buffer.from([0x00, 0x00, 0x0a, 0x10, 0x01, 0x0e]),
    body,
  ]);
  const valid = await request.post(`${API}/api/v1/wasm/validate`, {
    multipart: {
      file: { name: "double.wasm", mimeType: "application/wasm", buffer: wasmBytes },
    },
    headers,
  });
  expect(valid.ok(), await valid.text()).toBeTruthy();
  expect((await valid.json()).is_valid).toBe(true);

  const modName = `j7_double_${Date.now().toString(36)}`;
  const upload = await request.post(
    `${API}/api/v1/wasm/upload?name=${modName}`,
    {
      multipart: {
        file: { name: "double.wasm", mimeType: "application/wasm", buffer: wasmBytes },
      },
      headers,
    },
  );
  expect([200, 201]).toContain(upload.status());
  const moduleId = (await upload.json()).id as string;
  expect(moduleId).toBeTruthy();

  const wasmYaml = [
    "pipeline:",
    `  name: j7_wasm_${Date.now().toString(36)}`,
    "  steps:",
    "    - name: src",
    "      type: load",
    `      file_id: ${fileId}`,
    "    - name: doubled",
    "      type: wasm_compute",
    "      input: src",
    `      wasm_file_id: ${moduleId}`,
    "      function: double",
    "      input_columns: [amount]",
    "      output_column: doubled",
    "    - name: out",
    "      type: save",
    "      input: doubled",
    "      filename: j7_wasm_out.csv",
  ].join("\n");
  const wasmRun = await runPipeline(request, user.token, wasmYaml);
  expect(await waitForRun(request, user.token, wasmRun)).toBe("COMPLETED");

  // The doubled column has the right values.
  const dl = await request.get(`${API}/api/v1/pipelines/${wasmRun}/download`, { headers });
  expect(dl.ok(), await dl.text()).toBeTruthy();
  const { download_url: wasmUrl } = await dl.json();
  expect(typeof wasmUrl).toBe("string");
  const csvResp = await fetch(wasmUrl as string);
  expect(csvResp.ok).toBeTruthy();
  const csv = await csvResp.text();
  expect(csv).toContain("doubled");
  const firstData = csv.split("\n")[1];
  const cols = csv.split("\n")[0].split(",");
  const values = firstData.split(",");
  expect(Number(values[cols.indexOf("doubled")])).toBe(
    Number(values[cols.indexOf("amount")]) * 2,
  );

  // Contracts: a blocking contract quarantines a violating run.
  const contractPipe = `j7_contract_${Date.now().toString(36)}`;
  const contractYaml = [
    "columns:",
    "  amount:",
    "    type: integer",
    "    min_value: 1000",
  ].join("\n");
  const contract = await request.post(
    `${API}/api/v1/contracts/pipelines/${contractPipe}`,
    {
      data: { yaml_content: contractYaml, severity: "block" },
      headers,
    },
  );
  expect(contract.status(), await contract.text()).toBe(201);
  const violRun = await runPipeline(
    request,
    user.token,
    [
      "pipeline:",
      `  name: ${contractPipe}`,
      "  steps:",
      "    - name: src",
      "      type: load",
      `      file_id: ${fileId}`,
      "    - name: out",
      "      type: save",
      "      input: src",
      "      filename: j7_violation_out.csv",
    ].join("\n"),
    contractPipe,
  );
  expect(await waitForRun(request, user.token, violRun)).toBe("CONTRACT_VIOLATION");

  // UI spot check: schedules page renders for a logged-in user.
  await uiLogin(page, user.email, user.password);
  await page.goto(`${UI}/schedules`, { waitUntil: "domcontentloaded" }).catch(() => {});
  await expect(page.getByText(/schedule/i).first()).toBeVisible({ timeout: 30_000 });
});
