import { APIRequestContext, Page, expect } from "@playwright/test";

/** Shared helpers for journey specs: one browser context == one user. */
export const API = process.env.E2E_API_URL || "http://127.0.0.1:8000";
export const UI = process.env.E2E_BASE_URL || "http://localhost";

export interface JourneyUser {
  email: string;
  password: string;
  token: string;
}

export async function registerUser(
  request: APIRequestContext,
  prefix: string,
): Promise<JourneyUser> {
  const tag = `${prefix}_${Date.now().toString(36)}`;
  const email = `${tag}@pipelineiq.test`;
  const password = "Testpass123!";
  // Registration is rate-limited; back off and retry on 429.
  let reg = await request.post(`${API}/auth/register`, {
    data: { email, username: tag, password },
  });
  for (let i = 0; i < 8 && reg.status() === 429; i++) {
    await new Promise((r) => setTimeout(r, 10_000));
    reg = await request.post(`${API}/auth/register`, {
      data: { email, username: tag, password },
    });
  }
  expect(reg.ok(), await reg.text()).toBeTruthy();
  let login = await request.post(`${API}/auth/login`, {
    data: { email, password },
  });
  for (let i = 0; i < 8 && login.status() === 429; i++) {
    await new Promise((r) => setTimeout(r, 10_000));
    login = await request.post(`${API}/auth/login`, {
      data: { email, password },
    });
  }
  expect(login.ok(), await login.text()).toBeTruthy();
  const { access_token: token } = await login.json();
  return { email, password, token };
}

export async function uploadCsv(
  request: APIRequestContext,
  token: string,
  filename: string,
  content: string,
): Promise<string> {
  const resp = await request.post(`${API}/api/v1/files/upload`, {
    multipart: {
      file: { name: filename, mimeType: "text/csv", buffer: Buffer.from(content) },
    },
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(resp.ok(), await resp.text()).toBeTruthy();
  const { id } = await resp.json();
  expect(id).toBeTruthy();
  return id as string;
}

export async function runPipeline(
  request: APIRequestContext,
  token: string,
  yaml: string,
  name?: string,
): Promise<string> {
  // Pipeline runs are rate-limited; back off and retry on 429.
  let resp = await request.post(`${API}/api/v1/pipelines/run`, {
    data: name ? { yaml_config: yaml, name } : { yaml_config: yaml },
    headers: { Authorization: `Bearer ${token}` },
  });
  for (let i = 0; i < 5 && resp.status() === 429; i++) {
    await new Promise((r) => setTimeout(r, 15_000));
    resp = await request.post(`${API}/api/v1/pipelines/run`, {
      data: name ? { yaml_config: yaml, name } : { yaml_config: yaml },
      headers: { Authorization: `Bearer ${token}` },
    });
  }
  expect(resp.status(), await resp.text()).toBe(202);
  const { run_id } = await resp.json();
  return run_id as string;
}

export async function waitForRun(
  request: APIRequestContext,
  token: string,
  runId: string,
  timeoutMs = 120_000,
): Promise<string> {
  const start = Date.now();
  for (;;) {
    // Per-request timeout with retry: under heavy worker load a single
    // poll can be slow; that is not failure. Only the overall deadline
    // below fails the wait.
    let resp;
    try {
      resp = await request.get(`${API}/api/v1/pipelines/${runId}`, {
        headers: { Authorization: `Bearer ${token}` },
        timeout: 15_000,
      });
    } catch (e) {
      if (Date.now() - start > timeoutMs) {
        throw new Error(`run ${runId} polling timed out: last error ${e}`);
      }
      await new Promise((r) => setTimeout(r, 5000));
      continue;
    }
    expect(resp.ok()).toBeTruthy();
    const run = await resp.json();
    if (["COMPLETED", "HEALED", "FAILED", "CONTRACT_VIOLATION", "CANCELLED", "TIMEOUT"].includes(run.status)) {
      return run.status as string;
    }
    if (Date.now() - start > timeoutMs) {
      throw new Error(`run ${runId} did not finish in ${timeoutMs}ms (last: ${run.status})`);
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
}

export function simpleYaml(name: string, fileId: string, saveAs: string): string {
  return [
    "pipeline:",
    `  name: ${name}`,
    "  steps:",
    "    - name: src",
    "      type: load",
    `      file_id: ${fileId}`,
    "    - name: out",
    "      type: save",
    "      input: src",
    `      filename: ${saveAs}`,
  ].join("\n");
}

/** Log in through the UI, tolerating auth rate limits (5/min). */
export async function uiLogin(page: Page, email: string, password: string): Promise<void> {
  await page.goto(`${UI}/login`, { waitUntil: "domcontentloaded" }).catch(() => {});
  await page.goto(`${UI}/login`, { waitUntil: "domcontentloaded" });
  for (let attempt = 0; attempt < 8; attempt++) {
    await page.getByTestId("email-input").fill(email);
    await page.getByTestId("password-input").fill(password);
    await page.getByTestId("login-btn").click();
    try {
      await page.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 15_000 });
      return;
    } catch {
      // Probably a 429 from the auth limiter; back off and retry.
      await new Promise((r) => setTimeout(r, 15_000));
      await page.goto(`${UI}/login`, { waitUntil: "domcontentloaded" }).catch(() => {});
    }
  }
  throw new Error("uiLogin: still on /login after retries (auth rate limited?)");
}
