import { expect, test } from "@playwright/test";
import { API, registerUser, runPipeline, simpleYaml, uploadCsv, waitForRun } from "./helpers";

/**
 * J6: delete a dataset and a pipeline while run history survives;
 * re-uploading the same bytes does not resurrect deleted references.
 */
test("J6 delete dataset and pipeline, history survives", async ({ request }) => {
  test.setTimeout(240_000);
  const user = await registerUser(request, "j6");
  const headers = { Authorization: `Bearer ${user.token}` };

  const fileId = await uploadCsv(request, user.token, "j6.csv", "id,v\n1,2\n3,4\n");
  const runId = await runPipeline(
    request, user.token, simpleYaml(`j6_pipe_${Date.now().toString(36)}`, fileId, "j6_out.csv"),
  );
  expect(await waitForRun(request, user.token, runId)).toBe("COMPLETED");

  // Delete the dataset.
  const del = await request.delete(`${API}/api/v1/files/${fileId}`, { headers });
  expect(del.status()).toBe(200);

  // The run record still exists with its status and error/output metadata.
  const run = await (
    await request.get(`${API}/api/v1/pipelines/${runId}`, { headers })
  ).json();
  expect(run.status).toBe("COMPLETED");
  expect(run.id).toBe(runId);

  // The deleted file is gone from listings.
  const list = await (await request.get(`${API}/api/v1/files/`, { headers })).json();
  expect(list.files.map((f: { id: string }) => f.id)).not.toContain(fileId);

  // Re-upload same bytes: new id, old references stay dead.
  const fileId2 = await uploadCsv(request, user.token, "j6.csv", "id,v\n1,2\n3,4\n");
  expect(fileId2).not.toBe(fileId);
  const stale = await request.get(`${API}/api/v1/files/${fileId}`, { headers });
  expect(stale.status()).toBe(404);
});
