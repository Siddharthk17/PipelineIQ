import { expect, test } from "@playwright/test";
import {
  registerUser,
  runPipeline,
  simpleYaml,
  uploadCsv,
  waitForRun,
} from "./helpers";
import { API } from "./helpers";

/**
 * J3: large file upload through the presigned-URL path (direct PUT to
 * object storage, browser-style), followed by a pipeline run on it.
 */
test("J3 large upload via presigned URL then run", async ({ request }) => {
  test.setTimeout(300_000);
  const user = await registerUser(request, "j3");
  const headers = { Authorization: `Bearer ${user.token}` };

  // ~15 MB CSV forces the presigned (direct) upload path (>10 MB threshold).
  const rows = ["id,region,amount"];
  for (let i = 0; i < 1_000_000; i++) {
    rows.push(`${i},R${i % 8},${(i * 1.5).toFixed(2)}`);
  }
  const payload = rows.join("\n");

  const negotiate = await request.post(`${API}/api/v1/files/request-upload-url`, {
    data: { filename: "j3_big.csv", file_size: Buffer.byteLength(payload) },
    headers,
  });
  expect(negotiate.ok(), await negotiate.text()).toBeTruthy();
  const { method, upload_url: uploadUrl, file_id: fileId, confirm_endpoint } =
    await negotiate.json();
  expect(method).toBe("direct");
  expect(typeof uploadUrl).toBe("string");

  // Direct PUT to object storage exactly like a browser would.
  const putResp = await fetch(uploadUrl, {
    method: "PUT",
    headers: { "Content-Type": "text/csv" },
    body: payload,
  });
  expect(putResp.ok, `${putResp.status} ${(await putResp.text()).slice(0, 300)}`).toBeTruthy();

  const confirmPath = new URL(confirm_endpoint, API).pathname;
  const confirm = await request.post(`${API}${confirmPath}`, { headers });
  expect(confirm.status(), await confirm.text()).toBe(201);
  const confirmed = await confirm.json();
  expect(confirmed.row_count).toBeGreaterThan(800_000);

  // Run a pipeline over the large file.
  const runId = await runPipeline(
    request,
    user.token,
    simpleYaml(`j3_bigrun_${Date.now().toString(36)}`, confirmed.id, "j3_big_out.csv"),
  );
  expect(await waitForRun(request, user.token, runId, 240_000)).toBe("COMPLETED");
});
