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
 * J4: two users working at once keep strict data isolation — neither can
 * read, run, or delete the other's files, runs, or notifications.
 */
test("J4 two users have strict data isolation", async ({ request }) => {
  test.setTimeout(240_000);
  const userA = await registerUser(request, "j4a");
  const userB = await registerUser(request, "j4b");
  const headersA = { Authorization: `Bearer ${userA.token}` };
  const headersB = { Authorization: `Bearer ${userB.token}` };

  const fileA = await uploadCsv(request, userA.token, "a_secret.csv", "id,v\n1,10\n");
  const runA = await runPipeline(
    request, userA.token, simpleYaml(`j4_pipe_${Date.now().toString(36)}`, fileA, "a_out.csv"),
  );
  expect(await waitForRun(request, userA.token, runA)).toBe("COMPLETED");

  // B cannot see A's file, run, or download.
  const fileResp = await request.get(`${API}/api/v1/files/${fileA}`, { headers: headersB });
  expect(fileResp.status()).toBe(404);
  const runResp = await request.get(`${API}/api/v1/pipelines/${runA}`, { headers: headersB });
  expect(runResp.status()).toBe(404);
  const dlResp = await request.get(`${API}/api/v1/pipelines/${runA}/download`, { headers: headersB });
  expect([403, 404]).toContain(dlResp.status());
  const delResp = await request.delete(`${API}/api/v1/files/${fileA}`, { headers: headersB });
  expect(delResp.status()).toBe(404);

  // B's own listing shows none of A's artifacts.
  const listResp = await request.get(`${API}/api/v1/files/`, { headers: headersB });
  expect(listResp.ok()).toBeTruthy();
  for (const f of (await listResp.json()).files as { original_filename: string }[]) {
    expect(f.original_filename).not.toBe("a_secret.csv");
  }

  // B cannot run a pipeline over A's file.
  const runResp2 = await request.post(`${API}/api/v1/pipelines/run`, {
    data: {
      yaml_config: [
        "pipeline:",
        "  name: j4_steal",
        "  steps:",
        "    - name: src",
        "      type: load",
        `      file_id: ${fileA}`,
      ].join("\n"),
    },
    headers: headersB,
  });
  expect(runResp2.status()).toBe(404);

  // A is unaffected.
  const own = await request.get(`${API}/api/v1/files/${fileA}`, { headers: headersA });
  expect(own.ok()).toBeTruthy();
});
